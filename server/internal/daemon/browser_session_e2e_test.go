package daemon

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// End-to-end smoke for the two HTTP planes over a real TCP listener.
//
// The unit tests call handlers directly, which cannot catch a routing mistake
// (a path registered in one place but mounted in another) or a header/method
// assumption that only shows up through net/http's own request path. This test
// binds a listener, drives the real mux, and walks one session from create
// through host registration, a tool command, evidence and two-phase cleanup.
func TestBrowserPlanesEndToEndOverTCP(t *testing.T) {
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("bind: %v", err)
	}
	base := "http://" + listener.Addr().String()

	partitionRoot := t.TempDir()
	partitionDir := filepath.Join(partitionRoot, "qa-task-task-smoke")
	if err := os.MkdirAll(partitionDir, 0o700); err != nil {
		t.Fatalf("create partition dir: %v", err)
	}

	d := &Daemon{logger: logger}
	d.cfg.Profile = "desktop-smoke"
	d.browserSessions = newBrowserSessionRegistry(logger)
	d.browserHostSecretValue = "smoke-host-secret-value-long-enough"
	var storedEvidence int
	d.browserEvidenceUploader = func(_ context.Context, _, _ string, _ string, data []byte) (string, error) {
		storedEvidence = len(data)
		return "att_smoke", nil
	}
	d.registerActiveRepoCheckoutTask("mat_smoke", activeRepoCheckoutTask{
		WorkspaceID: "ws", TaskID: "task-smoke", IssueID: "issue-smoke", AgentID: "agent-smoke",
	})
	d.browserHostSeenAt.Store(time.Now().UnixNano())

	mux := http.NewServeMux()
	d.registerBrowserRoutes(mux)
	server := &httptest.Server{Config: &http.Server{Handler: mux}, Listener: listener}
	server.Start()
	defer server.Close()

	client := &http.Client{Timeout: 10 * time.Second}
	hostAuth := map[string]string{"Authorization": "Bearer smoke-host-secret-value-long-enough"}
	taskAuth := map[string]string{"Authorization": "Bearer mat_smoke"}

	do := func(method, path string, headers map[string]string, body []byte) (int, []byte) {
		t.Helper()
		req, reqErr := http.NewRequest(method, base+path, bytes.NewReader(body))
		if reqErr != nil {
			t.Fatalf("build request: %v", reqErr)
		}
		for key, value := range headers {
			req.Header.Set(key, value)
		}
		response, reqErr := client.Do(req)
		if reqErr != nil {
			t.Fatalf("%s %s: %v", method, path, reqErr)
		}
		defer response.Body.Close()
		payload, _ := io.ReadAll(response.Body)
		return response.StatusCode, payload
	}

	// 1. Task plane creates the session.
	jsonHeaders := map[string]string{"Content-Type": "application/json"}
	taskHeaders := mergeHeaders(taskAuth, jsonHeaders)
	status, body := do(http.MethodPost, "/browser/session", taskHeaders, nil)
	if status != http.StatusOK {
		t.Fatalf("create session = %d %s", status, body)
	}
	var created browserResponse
	if err := json.Unmarshal(body, &created); err != nil {
		t.Fatalf("decode create: %v", err)
	}
	sessionID := created.Session.SessionID
	if sessionID == "" || created.Session.State != string(browserStateRequested) {
		t.Fatalf("created session = %+v, want a requested session", created.Session)
	}

	// 2. A browser page cannot reach either plane.
	origin := mergeHeaders(taskHeaders, map[string]string{"Origin": "https://evil.example"})
	if status, _ := do(http.MethodGet, "/browser/session?session_id="+sessionID, origin, nil); status != http.StatusForbidden {
		t.Fatalf("task plane with Origin = %d, want 403", status)
	}

	// 3. Host plane registers the view.
	register := fmt.Sprintf(`{"session_id":%q,"task_id":"task-smoke","window_id":"w1","profile_dir":%q,"cdp_ready":true}`,
		sessionID, partitionDir)
	status, body = do(http.MethodPost, "/browser/host/register", mergeHeaders(hostAuth, jsonHeaders), []byte(register))
	if status != http.StatusOK {
		t.Fatalf("register host = %d %s", status, body)
	}

	// A wrong secret is refused.
	if status, _ := do(http.MethodPost, "/browser/host/register",
		mergeHeaders(jsonHeaders, map[string]string{"Authorization": "Bearer wrong"}),
		[]byte(register)); status != http.StatusUnauthorized {
		t.Fatalf("register with a bad secret = %d, want 401", status)
	}

	// 4. One tool call travels the whole chain: MCP server -> registry command
	//    -> (host long poll) -> result -> MCP response.
	config, mcpSet, err := startTaskBrowserMCP(context.Background(), d, "task-smoke", logger)
	if err != nil || mcpSet == nil {
		t.Fatalf("start browser MCP: config=%s err=%v", config, err)
	}
	defer mcpSet.Close()
	var mcpEnvelope struct {
		MCPServers map[string]struct {
			URL string `json:"url"`
		} `json:"mcpServers"`
	}
	if err := json.Unmarshal(config, &mcpEnvelope); err != nil {
		t.Fatalf("decode mcp config: %v", err)
	}
	mcpURL := mcpEnvelope.MCPServers["multica-browser"].URL
	if mcpURL == "" {
		t.Fatalf("mcp config has no multica-browser endpoint: %s", config)
	}
	var toolResponse struct {
		Result struct {
			Content []struct {
				Text string `json:"text"`
			} `json:"content"`
			IsError bool `json:"isError"`
		} `json:"result"`
	}
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		payload := `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"browser_url"}}`
		req, _ := http.NewRequest(http.MethodPost, mcpURL, strings.NewReader(payload))
		req.Header.Set("Content-Type", "application/json")
		response, reqErr := client.Do(req)
		if reqErr != nil {
			t.Errorf("mcp tool call: %v", reqErr)
			return
		}
		defer response.Body.Close()
		_ = json.NewDecoder(response.Body).Decode(&toolResponse)
	}()

	// The host long-polls and receives the command.
	heartbeatBody := fmt.Sprintf(`{"session_id":%q,"seq":1,"view_alive":true,"long_poll_ms":5000}`, sessionID)
	status, body = do(http.MethodPost, "/browser/host/heartbeat", mergeHeaders(hostAuth, jsonHeaders), []byte(heartbeatBody))
	if status != http.StatusOK {
		t.Fatalf("heartbeat = %d %s", status, body)
	}
	var heartbeat struct {
		Commands []browserCommand `json:"commands"`
	}
	if err := json.Unmarshal(body, &heartbeat); err != nil {
		t.Fatalf("decode heartbeat: %v", err)
	}
	if len(heartbeat.Commands) != 1 || heartbeat.Commands[0].Action != browserToolURL {
		t.Fatalf("heartbeat commands = %+v, want one %s", heartbeat.Commands, browserToolURL)
	}

	// The host answers with the result.
	resultBody, _ := json.Marshal(map[string]any{
		"session_id": sessionID, "seq": 2, "view_alive": true,
		"url_host": "127.0.0.1:3000", "url_path": "/dashboard",
		"results": []browserCommandResult{{
			ID: heartbeat.Commands[0].ID, OK: true,
			Text: `{"host":"127.0.0.1:3000","path":"/dashboard"}`,
		}},
		"long_poll_ms": 200,
	})
	if status, body := do(http.MethodPost, "/browser/host/heartbeat", mergeHeaders(hostAuth, jsonHeaders), resultBody); status != http.StatusOK {
		t.Fatalf("heartbeat result = %d %s", status, body)
	}
	wg.Wait()
	if toolResponse.Result.IsError {
		t.Fatalf("tool call reported an error: %+v", toolResponse.Result.Content)
	}
	if len(toolResponse.Result.Content) == 0 || !strings.Contains(toolResponse.Result.Content[0].Text, "/dashboard") {
		t.Fatalf("tool text = %+v, want the reported path", toolResponse.Result.Content)
	}

	// 5. Evidence lands as an attachment with a verified digest.
	png := bytes.Repeat([]byte{0x89, 0x50, 0x4e, 0x47}, 64)
	sum := sha256.Sum256(png)
	evidencePath := fmt.Sprintf("/browser/host/evidence?session_id=%s&bytes=%d&sha256=%s",
		sessionID, len(png), hex.EncodeToString(sum[:]))
	status, body = do(http.MethodPost, evidencePath,
		mergeHeaders(hostAuth, map[string]string{"Content-Type": "image/png"}), png)
	if status != http.StatusOK {
		t.Fatalf("evidence upload = %d %s", status, body)
	}
	if storedEvidence != len(png) {
		t.Fatalf("stored evidence = %d bytes, want %d", storedEvidence, len(png))
	}

	// Chunked evidence is refused before any read.
	chunked, _ := http.NewRequest(http.MethodPost, base+evidencePath, io.NopCloser(bytes.NewReader(png)))
	chunked.Header.Set("Content-Type", "image/png")
	chunked.Header.Set("Authorization", "Bearer smoke-host-secret-value-long-enough")
	chunked.ContentLength = -1
	chunkedResponse, err := client.Do(chunked)
	if err != nil {
		t.Fatalf("chunked evidence request: %v", err)
	}
	chunkedResponse.Body.Close()
	if chunkedResponse.StatusCode != http.StatusLengthRequired {
		t.Fatalf("chunked evidence = %d, want 411", chunkedResponse.StatusCode)
	}

	// 6. Two-phase cleanup: the daemon asks for the view to go, the host acks,
	//    and only then does the directory disappear.
	if status, body := do(http.MethodDelete, "/browser/session?session_id="+sessionID, taskAuth, nil); status != http.StatusOK {
		t.Fatalf("delete session = %d %s", status, body)
	}
	status, body = do(http.MethodPost, "/browser/host/heartbeat", mergeHeaders(hostAuth, jsonHeaders),
		[]byte(fmt.Sprintf(`{"session_id":%q,"seq":3,"view_alive":true,"long_poll_ms":1}`, sessionID)))
	if status != http.StatusOK {
		t.Fatalf("closing heartbeat = %d %s", status, body)
	}
	var closing struct {
		Directive map[string]any `json:"directive"`
	}
	if err := json.Unmarshal(body, &closing); err != nil {
		t.Fatalf("decode closing heartbeat: %v", err)
	}
	if closing.Directive == nil || closing.Directive["action"] != "close_view" {
		t.Fatalf("directive = %+v, want close_view", closing.Directive)
	}
	if _, statErr := os.Stat(partitionDir); statErr != nil {
		t.Fatalf("partition dir removed before the host acked: %v", statErr)
	}

	status, body = do(http.MethodPost, "/browser/host/session/closed", mergeHeaders(hostAuth, jsonHeaders),
		[]byte(fmt.Sprintf(`{"session_id":%q}`, sessionID)))
	if status != http.StatusOK {
		t.Fatalf("closed ack = %d %s", status, body)
	}
	if _, statErr := os.Stat(partitionDir); !os.IsNotExist(statErr) {
		t.Fatalf("partition dir survived the two-phase cleanup: %v", statErr)
	}

	// 7. A second DELETE is still a success (I6).
	if status, body := do(http.MethodDelete, "/browser/session?session_id="+sessionID, taskAuth, nil); status != http.StatusOK {
		t.Fatalf("repeat delete = %d %s, want 200", status, body)
	}
}

func mergeHeaders(base, extra map[string]string) map[string]string {
	merged := make(map[string]string, len(base)+len(extra))
	for key, value := range base {
		merged[key] = value
	}
	for key, value := range extra {
		merged[key] = value
	}
	return merged
}
