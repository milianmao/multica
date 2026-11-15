package daemon

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

// QA built-in isolated browser: daemon contract tests.
//
// Each test names the SDD invariant or §9 failure case it pins, because the
// interesting part of this feature is what must NOT happen (a lock an agent can
// release, a 64 KiB cap on a 4 MiB screenshot, a resurrected orphan).

func newBrowserTestDaemon(t *testing.T) *Daemon {
	t.Helper()
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	d := &Daemon{logger: logger}
	d.cfg.Profile = "desktop-test"
	// Keep the host credential out of the user's real profile directory.
	t.Setenv("HOME", t.TempDir())
	t.Setenv("USERPROFILE", t.TempDir())
	d.browserSessions = newBrowserSessionRegistry(logger)
	d.browserSessions.cleanupInterval = time.Millisecond
	d.browserHostSecretValue = "host-secret-value-that-is-long-enough"
	d.browserEvidenceUploader = func(context.Context, string, string, string, []byte) (string, error) {
		return "att_test", nil
	}
	return d
}

func browserTestTask(taskID string) activeRepoCheckoutTask {
	return activeRepoCheckoutTask{
		WorkspaceID: "ws", TaskID: taskID, IssueID: "issue-" + taskID, AgentID: "agent-1",
	}
}

func registerBrowserTestTask(d *Daemon, token, taskID string) {
	d.registerActiveRepoCheckoutTask(token, browserTestTask(taskID))
}

func browserJSONRequest(method, target, body string) *http.Request {
	req := httptest.NewRequest(method, target, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	return req
}

func browserTaskRequest(method, target, token, body string) *http.Request {
	req := browserJSONRequest(method, target, body)
	req.Header.Set("Authorization", "Bearer "+token)
	return req
}

func browserHostRequest(method, target, body string) *http.Request {
	req := browserJSONRequest(method, target, body)
	req.Header.Set("Authorization", "Bearer host-secret-value-that-is-long-enough")
	return req
}

func decodeBrowserResponse(t *testing.T, rec *httptest.ResponseRecorder) browserResponse {
	t.Helper()
	var out browserResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("decode response %q: %v", rec.Body.String(), err)
	}
	return out
}

// SDD §1 I6 / §9 case 5: repeated create and repeated DELETE are idempotent.
func TestBrowserSessionCreateAndDeleteAreIdempotent(t *testing.T) {
	d := newBrowserTestDaemon(t)
	registerBrowserTestTask(d, "mat_a", "task-a")
	d.browserHostSeenAt.Store(time.Now().UnixNano())

	create := d.browserSessionHandler()
	first := httptest.NewRecorder()
	create(first, browserTaskRequest(http.MethodPost, "/browser/session", "mat_a", ""))
	if first.Code != http.StatusOK {
		t.Fatalf("first create = %d %s", first.Code, first.Body)
	}
	firstView := decodeBrowserResponse(t, first).Session
	if firstView == nil || firstView.State != string(browserStateRequested) {
		t.Fatalf("first create session = %+v, want requested", firstView)
	}
	second := httptest.NewRecorder()
	create(second, browserTaskRequest(http.MethodPost, "/browser/session", "mat_a", ""))
	if second.Code != http.StatusOK {
		t.Fatalf("second create = %d %s", second.Code, second.Body)
	}
	if got := decodeBrowserResponse(t, second).Session; got == nil || got.SessionID != firstView.SessionID {
		t.Fatalf("second create = %+v, want the same session %s", got, firstView.SessionID)
	}

	del := d.browserSessionHandler()
	target := "/browser/session?session_id=" + firstView.SessionID
	for range 2 {
		rec := httptest.NewRecorder()
		del(rec, browserTaskRequest(http.MethodDelete, target, "mat_a", ""))
		if rec.Code != http.StatusOK {
			t.Fatalf("delete = %d %s, want 200 for a closed session too", rec.Code, rec.Body)
		}
	}
}

// SDD §9 case 1 / D3: no Desktop is a hard error with no queue.
func TestBrowserSessionWithoutHostAnswersHostOffline(t *testing.T) {
	d := newBrowserTestDaemon(t)
	registerBrowserTestTask(d, "mat_a", "task-a")

	rec := httptest.NewRecorder()
	d.browserSessionHandler()(rec, browserTaskRequest(http.MethodPost, "/browser/session", "mat_a", ""))
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("create with no host = %d %s, want 503", rec.Code, rec.Body)
	}
	if got := decodeBrowserResponse(t, rec).Code; got != browserCodeHostOffline {
		t.Fatalf("code = %q, want %q", got, browserCodeHostOffline)
	}
	if sessions := d.browserSessions.list(); len(sessions) != 0 {
		t.Fatalf("a refused request left %d sessions queued; D3 requires no queue", len(sessions))
	}
}

// SDD §3.1: the task plane rejects no credential, a foreign token, and a
// session that belongs to another task.
func TestBrowserTaskPlaneAuthorization(t *testing.T) {
	d := newBrowserTestDaemon(t)
	registerBrowserTestTask(d, "mat_a", "task-a")
	registerBrowserTestTask(d, "mat_b", "task-b")
	d.browserHostSeenAt.Store(time.Now().UnixNano())

	handler := d.browserSessionHandler()
	for _, token := range []string{"", "mat_unknown"} {
		rec := httptest.NewRecorder()
		handler(rec, browserTaskRequest(http.MethodPost, "/browser/session", token, ""))
		if rec.Code != http.StatusUnauthorized {
			t.Fatalf("token %q = %d %s, want 401", token, rec.Code, rec.Body)
		}
	}

	rec := httptest.NewRecorder()
	handler(rec, browserTaskRequest(http.MethodPost, "/browser/session", "mat_a", ""))
	sessionID := decodeBrowserResponse(t, rec).Session.SessionID

	crossTask := httptest.NewRecorder()
	handler(crossTask, browserTaskRequest(http.MethodGet, "/browser/session?session_id="+sessionID, "mat_b", ""))
	if crossTask.Code != http.StatusForbidden {
		t.Fatalf("another task's session = %d %s, want 403", crossTask.Code, crossTask.Body)
	}
}

// SDD §3.1: a browser page's POST always carries an Origin, and this server
// answers no CORS preflight — that is the whole localhost guard.
func TestBrowserPlanesRejectBrowserOrigins(t *testing.T) {
	d := newBrowserTestDaemon(t)
	registerBrowserTestTask(d, "mat_a", "task-a")

	req := browserTaskRequest(http.MethodPost, "/browser/session", "mat_a", "")
	req.Header.Set("Origin", "https://evil.example")
	rec := httptest.NewRecorder()
	d.browserSessionHandler()(rec, req)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("task plane with Origin = %d %s, want 403", rec.Code, rec.Body)
	}

	hostReq := browserHostRequest(http.MethodPost, "/browser/host/register", `{}`)
	hostReq.Header.Set("Origin", "https://evil.example")
	hostRec := httptest.NewRecorder()
	d.browserHostRegisterHandler()(hostRec, hostReq)
	if hostRec.Code != http.StatusForbidden {
		t.Fatalf("host plane with Origin = %d %s, want 403", hostRec.Code, hostRec.Body)
	}
}

// SDD §3.1 / §9 case 12: the JSON and binary limits must not be swapped.
func TestBrowserBodyGroupsKeepSeparateLimitsAndTypes(t *testing.T) {
	d := newBrowserTestDaemon(t)
	d.browserSessions.requestBrowserSession(browserTestTask("task-a"), browserCapabilityFull)

	// A 6 MiB JSON body is over the JSON group's 64 KiB: 413, never a JSON
	// parse error.
	big := `{"pad":"` + strings.Repeat("x", 6<<20) + `"}`
	rec := httptest.NewRecorder()
	d.browserSessionHandler()(rec, browserHostRequest(http.MethodPost, "/browser/session", big))
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("6 MiB JSON body = %d %s, want 413", rec.Code, rec.Body)
	}

	// image/png to a JSON endpoint: 415, not a decode attempt.
	png := browserJSONRequest(http.MethodPost, "/browser/session", "")
	png.Header.Set("Content-Type", "image/png")
	pngRec := httptest.NewRecorder()
	d.browserSessionHandler()(pngRec, png)
	if pngRec.Code != http.StatusUnsupportedMediaType {
		t.Fatalf("image/png to the JSON group = %d %s, want 415", pngRec.Code, pngRec.Body)
	}

	// application/json to evidence: 415 before any body is read.
	evidence := browserJSONRequest(http.MethodPost, "/browser/host/evidence?session_id=x&bytes=1&sha256=00", `{}`)
	evidenceRec := httptest.NewRecorder()
	d.browserHostEvidenceHandler()(evidenceRec, evidence)
	if evidenceRec.Code != http.StatusUnsupportedMediaType {
		t.Fatalf("application/json to evidence = %d %s, want 415", evidenceRec.Code, evidenceRec.Body)
	}
}

// SDD §3.1.1 / §9 cases 13 and 14: chunked evidence is 411, and the transport
// cap is 6 MiB while the 4 MiB artifact cap belongs to Desktop.
func TestBrowserEvidenceLengthSemantics(t *testing.T) {
	d := newBrowserTestDaemon(t)
	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-a"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", "", true); err != nil {
		t.Fatalf("register host: %v", err)
	}
	target := "/browser/host/evidence?session_id=" + session.ID + "&bytes=4&sha256=00"

	chunked := browserHostRequest(http.MethodPost, target, "abc")
	chunked.ContentLength = -1
	chunked.Header.Set("Content-Type", "image/png")
	chunkedRec := httptest.NewRecorder()
	d.browserHostEvidenceHandler()(chunkedRec, chunked)
	if chunkedRec.Code != http.StatusLengthRequired {
		t.Fatalf("chunked evidence = %d %s, want 411", chunkedRec.Code, chunkedRec.Body)
	}

	// 6 MiB + 1 KiB is over the transport cap: 413 before reading.
	oversize := browserHostRequest(http.MethodPost, target, "")
	oversize.Header.Set("Content-Type", "image/png")
	oversize.ContentLength = browserEvidenceMaxBytes + 1024
	oversizeRec := httptest.NewRecorder()
	d.browserHostEvidenceHandler()(oversizeRec, oversize)
	if oversizeRec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("6 MiB + 1 KiB evidence = %d %s, want 413", oversizeRec.Code, oversizeRec.Body)
	}
}

// SDD §9 case 15: a mismatch between the declared and actual bytes or digest
// must not leave a half image on the issue.
func TestBrowserEvidenceRejectsUnverifiedBytes(t *testing.T) {
	d := newBrowserTestDaemon(t)
	var uploaded int
	d.browserEvidenceUploader = func(context.Context, string, string, string, []byte) (string, error) {
		uploaded++
		return "att_test", nil
	}
	sessions := d.browserSessions
	session, err := sessions.requestBrowserSession(browserTestTask("task-a"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}

	cases := []struct {
		name     string
		body     string
		bytes    string
		shaValue string
		want     int
	}{
		{"byte count mismatch", "abcdef", "5", "", http.StatusBadRequest},
		{"digest mismatch", "abcdef", "6", strings.Repeat("0", 64), http.StatusBadRequest},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			target := "/browser/host/evidence?session_id=" + session.ID + "&bytes=" + tc.bytes + "&sha256=" + tc.shaValue
			req := browserHostRequest(http.MethodPost, target, tc.body)
			req.Header.Set("Content-Type", "image/png")
			rec := httptest.NewRecorder()
			d.browserHostEvidenceHandler()(rec, req)
			if rec.Code != tc.want {
				t.Fatalf("%s = %d %s, want %d", tc.name, rec.Code, rec.Body, tc.want)
			}
		})
	}
	if uploaded != 0 {
		t.Fatalf("evidence uploader ran %d times for rejected artifacts; a bad image must not reach the issue", uploaded)
	}
}

// SDD §9 case 10 and 14: a 5 MiB PNG inside the transport cap lands with a
// matching digest, and the session view exposes only the attachment id.
func TestBrowserEvidenceInsideTransportLimitIsStored(t *testing.T) {
	d := newBrowserTestDaemon(t)
	stored := make([]byte, 5<<20)
	for i := range stored {
		stored[i] = byte(i % 251)
	}
	sum := sha256.Sum256(stored)
	var uploadedBytes int
	d.browserEvidenceUploader = func(_ context.Context, workspaceID, issueID, _ string, data []byte) (string, error) {
		if workspaceID != "ws" || issueID != "issue-task-a" {
			t.Errorf("evidence uploaded for %s/%s, want the session's workspace and issue", workspaceID, issueID)
		}
		uploadedBytes = len(data)
		return "att_big", nil
	}
	sessions := d.browserSessions
	session, err := sessions.requestBrowserSession(browserTestTask("task-a"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}

	target := "/browser/host/evidence?session_id=" + session.ID +
		"&bytes=" + strconv.Itoa(len(stored)) + "&sha256=" + hex.EncodeToString(sum[:])
	req := httptest.NewRequest(http.MethodPost, target, bytes.NewReader(stored))
	req.Header.Set("Content-Type", "image/png")
	req.Header.Set("Authorization", "Bearer host-secret-value-that-is-long-enough")
	rec := httptest.NewRecorder()
	d.browserHostEvidenceHandler()(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("5 MiB evidence = %d %s, want 200: the 4 MiB cap is Desktop's, not the daemon's", rec.Code, rec.Body)
	}
	if uploadedBytes != len(stored) {
		t.Fatalf("stored %d bytes, want %d", uploadedBytes, len(stored))
	}
	view := decodeBrowserResponse(t, rec).Session
	if view == nil || view.LastEvidence == nil || view.LastEvidence.AttachmentID != "att_big" {
		t.Fatalf("session view = %+v, want last_evidence attachment att_big", view)
	}
}

// SDD §9 case 2 and §3.4: a heartbeat timeout orphans the session and a
// recovering heartbeat must not resurrect it.
func TestBrowserHeartbeatOrphansAndDoesNotResurrect(t *testing.T) {
	d := newBrowserTestDaemon(t)
	now := time.Now()
	d.browserSessions.now = func() time.Time { return now }
	d.browserSessions.heartbeatTimeout = 10 * time.Second
	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-a"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", "", true); err != nil {
		t.Fatalf("register host: %v", err)
	}
	if _, ok := d.browserSessions.heartbeat(session.ID, 1, true, nil); !ok {
		t.Fatal("first heartbeat was rejected")
	}

	now = now.Add(11 * time.Second)
	d.browserSessions.sweep()
	if got := d.browserSessions.bySessionID(session.ID).State; got != browserStateOrphaned {
		t.Fatalf("state after heartbeat timeout = %q, want orphaned", got)
	}

	if _, ok := d.browserSessions.heartbeat(session.ID, 2, true, nil); ok {
		t.Fatal("a recovering heartbeat revived an orphaned session; only a new session may recover")
	}
	if got := d.browserSessions.bySessionID(session.ID).State; got != browserStateOrphaned {
		t.Fatalf("state after recovery heartbeat = %q, want orphaned", got)
	}

	// A stale sequence is dropped without touching state.
	if _, ok := d.browserSessions.heartbeat(session.ID, 1, true, nil); ok {
		t.Fatal("a replayed heartbeat sequence was accepted")
	}
}

// SDD §3.4 / §9 case 3: the 30-minute human hard limit ends the session even
// while the idle timer is paused by the takeover.
func TestBrowserHumanHardDeadlineClosesDespiteLock(t *testing.T) {
	d := newBrowserTestDaemon(t)
	now := time.Now()
	d.browserSessions.now = func() time.Time { return now }
	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-a"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", "", true); err != nil {
		t.Fatalf("register host: %v", err)
	}
	if _, err := d.browserSessions.takeover(session.ID); err != nil {
		t.Fatalf("takeover: %v", err)
	}

	// Idle time alone must not close a taken-over session.
	now = now.Add(20 * time.Minute)
	d.browserSessions.heartbeat(session.ID, 1, true, nil)
	d.browserSessions.sweep()
	if got := d.browserSessions.bySessionID(session.ID).State; got != browserStateHosted {
		t.Fatalf("state after 20 min under a human lock = %q, want hosted (idle TTL paused)", got)
	}

	now = now.Add(11 * time.Minute)
	d.browserSessions.sweep()
	if got := d.browserSessions.bySessionID(session.ID).State; got != browserStateClosing {
		t.Fatalf("state past the 30 min hard limit = %q, want closing despite the human lock", got)
	}
}

// SDD §1 I6 / §4.2: cleanup is two-phase, retries a failed directory delete,
// and only deletes the directory it recorded.
func TestBrowserCleanupIsTwoPhaseAndRetries(t *testing.T) {
	d := newBrowserTestDaemon(t)
	root := t.TempDir()
	partitionDir := filepath.Join(root, "qa-task-task-a")
	if err := os.MkdirAll(partitionDir, 0o700); err != nil {
		t.Fatalf("create partition dir: %v", err)
	}
	now := time.Now()
	d.browserSessions.now = func() time.Time { return now }
	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-a"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", partitionDir, true); err != nil {
		t.Fatalf("register host: %v", err)
	}

	closed, ok := d.browserSessions.closeBrowserSession(session.ID)
	if !ok || closed.State != browserStateClosing {
		t.Fatalf("close = %+v ok=%v, want closing", closed, ok)
	}
	if _, err := os.Stat(partitionDir); err != nil {
		t.Fatalf("the directory was removed before the host acked view destruction: %v", err)
	}

	// An ack deletes it and finalizes.
	if _, ok := d.browserSessions.ackClosed(session.ID); !ok {
		t.Fatal("ackClosed rejected a closing session")
	}
	if _, err := os.Stat(partitionDir); !os.IsNotExist(err) {
		t.Fatalf("partition dir survived the two-phase cleanup: %v", err)
	}
}

func TestBrowserProfileDirMustMatchTheSessionPartition(t *testing.T) {
	if err := validateBrowserProfileDir(filepath.Join(t.TempDir(), "qa-task-task-b"), "qa-task-task-a"); err == nil {
		t.Fatal("a directory from another session's partition was accepted for deletion")
	}
	if err := validateBrowserProfileDir("relative/path", "qa-task-task-a"); err == nil {
		t.Fatal("a relative browser host profile dir was accepted")
	}
	if err := validateBrowserProfileDir(filepath.Join(t.TempDir(), "qa-task-task-a"), "qa-task-task-a"); err != nil {
		t.Fatalf("the session's own partition dir was rejected: %v", err)
	}
}

// SDD §7: takeover and release accept only the host secret, so an agent token
// can never release a person's lock.
func TestBrowserLockEndpointsRequireHostSecret(t *testing.T) {
	d := newBrowserTestDaemon(t)
	registerBrowserTestTask(d, "mat_a", "task-a")
	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-a"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", "", true); err != nil {
		t.Fatalf("register host: %v", err)
	}
	body := `{"session_id":"` + session.ID + `"}`

	takeover := d.browserHostSessionHandler("takeover")
	agentRec := httptest.NewRecorder()
	takeover(agentRec, browserTaskRequest(http.MethodPost, "/browser/session/takeover", "mat_a", body))
	if agentRec.Code != http.StatusUnauthorized {
		t.Fatalf("takeover with a task token = %d %s, want 401", agentRec.Code, agentRec.Body)
	}

	hostRec := httptest.NewRecorder()
	takeover(hostRec, browserHostRequest(http.MethodPost, "/browser/session/takeover", body))
	if hostRec.Code != http.StatusOK {
		t.Fatalf("takeover with the host secret = %d %s, want 200", hostRec.Code, hostRec.Body)
	}
	if got := d.browserSessions.bySessionID(session.ID).Lock; got != browserLockHuman {
		t.Fatalf("lock = %q, want human", got)
	}

	// The agent still cannot unlock.
	release := d.browserHostSessionHandler("release")
	agentRelease := httptest.NewRecorder()
	release(agentRelease, browserTaskRequest(http.MethodPost, "/browser/session/release", "mat_a", body))
	if agentRelease.Code != http.StatusUnauthorized {
		t.Fatalf("release with a task token = %d %s, want 401", agentRelease.Code, agentRelease.Body)
	}
	if got := d.browserSessions.bySessionID(session.ID).Lock; got != browserLockHuman {
		t.Fatalf("lock = %q after a rejected release, want human", got)
	}
}

// SDD §9 case 9: the whole tool call, including the wait for a takeover to be
// handed back, fits in the 30 s budget instead of silently running 60 s.
func TestBrowserToolCallSharesOneBudgetWithTheLockWait(t *testing.T) {
	d := newBrowserTestDaemon(t)
	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-a"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", "", true); err != nil {
		t.Fatalf("register host: %v", err)
	}
	if _, err := d.browserSessions.takeover(session.ID); err != nil {
		t.Fatalf("takeover: %v", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
	defer cancel()
	started := time.Now()
	_, err = d.browserSessions.waitForAgentLock(ctx, session.ID, browserMCPCallTimeout)
	elapsed := time.Since(started)
	if err == nil {
		t.Fatal("a write action ran while a person held the lock")
	}
	if elapsed > time.Second {
		t.Fatalf("lock wait took %s; it must stop at the call's own deadline, not add a second budget", elapsed)
	}

	// Read-class actions are not gated at all: the daemon still needs state even
	// while a person is driving. No host is polling in this test, so the call
	// reaches the queue and stops there — the point is that it did not stop at
	// the lock.
	readCtx, readCancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer readCancel()
	_, err = d.invokeBrowserTool(readCtx, "task-a", browserToolSpec{Name: browserToolSnapshot}, nil)
	if err == nil {
		t.Fatal("a snapshot succeeded with no host polling for commands")
	}
	if strings.Contains(err.Error(), browserCodeTakenOver) {
		t.Fatalf("snapshot under a human lock was refused as taken over (%v); read actions must not wait on the lock", err)
	}
	if !strings.Contains(err.Error(), browserCodeSessionUnavailable) {
		t.Fatalf("snapshot with no host = %v, want %s", err, browserCodeSessionUnavailable)
	}
}

// SDD §8 item 3: a provider that cannot carry HTTP MCP reports capability
// none, and its tools answer BROWSER_CAPABILITY_UNSUPPORTED rather than
// pretending to work.
func TestBrowserCapabilityNoneDisablesTheTools(t *testing.T) {
	d := newBrowserTestDaemon(t)
	d.recordBrowserCapability("task-a", browserCapabilityNone)
	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-a"), d.browserCapabilityForTask("task-a"))
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	if session.Capability != browserCapabilityNone {
		t.Fatalf("capability = %q, want none", session.Capability)
	}
	session.State = browserStateHosted // pretend a view exists

	_, err = d.invokeBrowserTool(context.Background(), "task-a", browserToolSpec{Name: browserToolGoto, Write: true}, json.RawMessage(`{"url":"https://example.com"}`))
	if err == nil || !strings.Contains(err.Error(), browserCodeCapabilityUnsupported) {
		t.Fatalf("tool call on a capability=none session = %v, want %s", err, browserCodeCapabilityUnsupported)
	}
}

// SDD §8: the injection overlay must add multica-browser without displacing a
// user's own MCP servers, and must survive a nil agent config.
func TestBrowserMCPInjectionPreservesUserServers(t *testing.T) {
	base := json.RawMessage(`{"mcpServers":{"user-server":{"type":"stdio","command":"user"}}}`)
	overlay := json.RawMessage(`{"mcpServers":{"multica-browser":{"type":"http","url":"http://127.0.0.1:1/token"}}}`)
	merged, err := mergeTaskRemoteMCPConfig(base, overlay)
	if err != nil {
		t.Fatalf("merge: %v", err)
	}
	var document struct {
		MCPServers map[string]json.RawMessage `json:"mcpServers"`
	}
	if err := json.Unmarshal(merged, &document); err != nil {
		t.Fatalf("decode merged config: %v", err)
	}
	if _, ok := document.MCPServers["user-server"]; !ok {
		t.Fatal("the injection displaced the user's own MCP server")
	}
	if _, ok := document.MCPServers["multica-browser"]; !ok {
		t.Fatal("multica-browser was not injected")
	}

	// The nil agent config case is why the overlay must not go through
	// mergeRuntimeAndAgentMcpConfig, which early-returns on nil and would drop
	// the injection silently.
	nilMerged, err := mergeTaskRemoteMCPConfig(nil, overlay)
	if err != nil {
		t.Fatalf("merge onto nil: %v", err)
	}
	if !bytes.Contains(nilMerged, []byte("multica-browser")) {
		t.Fatalf("injection was lost when the base config was nil: %s", nilMerged)
	}
}

// SDD §8.1: browser_wait and the takeover wait share one 30 s budget.
func TestBrowserToolSurfaceAndBudget(t *testing.T) {
	names := make(map[string]bool)
	for _, spec := range browserToolSpecs() {
		names[spec.Name] = true
	}
	for _, want := range []string{
		browserToolGoto, browserToolSnapshot, browserToolClick, browserToolFill,
		browserToolWheel, browserToolWait, browserToolScreenshot,
	} {
		if !names[want] {
			t.Fatalf("tool %q is missing from the seven write actions", want)
		}
	}
	// The three read-only tools must not be lock-gated, or a person holding
	// the lock would blind the daemon's own state reporting.
	for _, spec := range browserToolSpecs() {
		if spec.Name == browserToolSnapshot || spec.Name == browserToolConsole || spec.Name == browserToolURL {
			if spec.Write {
				t.Fatalf("%s is read-only and must not be lock-gated", spec.Name)
			}
		}
	}
	if browserMCPCallTimeout != 30*time.Second {
		t.Fatalf("tool call budget = %s, want 30s shared with the lock wait", browserMCPCallTimeout)
	}
}

// SDD §9 case 6: a dead view answers BROWSER_CAPABILITY_UNSUPPORTED rather than
// hanging, and the session stays inspectable.
func TestBrowserOrphanedSessionFailsToolCallsImmediately(t *testing.T) {
	d := newBrowserTestDaemon(t)
	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-a"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", "", true); err != nil {
		t.Fatalf("register host: %v", err)
	}
	// view_alive=false is what render-process-gone reports.
	d.browserSessions.heartbeat(session.ID, 1, false, nil)
	started := time.Now()
	_, err = d.invokeBrowserTool(context.Background(), "task-a", browserToolSpec{Name: browserToolClick, Write: true}, json.RawMessage(`{"ref":"e1"}`))
	if err == nil {
		t.Fatal("a write action succeeded against a dead view")
	}
	if elapsed := time.Since(started); elapsed > time.Second {
		t.Fatalf("a dead view took %s to report; A5 requires a fixed, short timeout", elapsed)
	}
	if !strings.Contains(err.Error(), browserCodeSessionUnavailable) {
		t.Fatalf("error = %v, want %s", err, browserCodeSessionUnavailable)
	}
}

// The host plane must reject the wrong secret even when the session exists.
func TestBrowserHostPlaneRejectsWrongSecret(t *testing.T) {
	d := newBrowserTestDaemon(t)
	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-a"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	req := browserJSONRequest(http.MethodPost, "/browser/host/register", `{"session_id":"`+session.ID+`","task_id":"task-a"}`)
	req.Header.Set("Authorization", "Bearer wrong-secret-wrong-secret-wrong")
	rec := httptest.NewRecorder()
	d.browserHostRegisterHandler()(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("register with the wrong secret = %d %s, want 401", rec.Code, rec.Body)
	}
	if got := d.browserSessions.bySessionID(session.ID).State; got != browserStateRequested {
		t.Fatalf("state = %q after a rejected register, want requested", got)
	}
}

// The host plane must not register a session onto a different task, and must
// refuse a directory belonging to another partition.
func TestBrowserHostRegisterValidatesTaskAndDirectory(t *testing.T) {
	d := newBrowserTestDaemon(t)
	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-a"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	otherTask := httptest.NewRecorder()
	d.browserHostRegisterHandler()(otherTask, browserHostRequest(http.MethodPost, "/browser/host/register",
		`{"session_id":"`+session.ID+`","task_id":"task-b"}`))
	if otherTask.Code != http.StatusForbidden {
		t.Fatalf("register for another task = %d %s, want 403", otherTask.Code, otherTask.Body)
	}

	outsideDir := httptest.NewRecorder()
	d.browserHostRegisterHandler()(outsideDir, browserHostRequest(http.MethodPost, "/browser/host/register",
		`{"session_id":"`+session.ID+`","task_id":"task-a","profile_dir":"/tmp/not-this-partition"}`))
	if outsideDir.Code != http.StatusBadRequest {
		t.Fatalf("register with a foreign profile dir = %d %s, want 400", outsideDir.Code, outsideDir.Body)
	}
}

// Commands are delivered to the host and their results unblock the waiting
// tool call; a closed session fails the waiter instead of leaking it.
func TestBrowserCommandRoundTrip(t *testing.T) {
	d := newBrowserTestDaemon(t)
	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-a"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", "", true); err != nil {
		t.Fatalf("register host: %v", err)
	}

	var wg sync.WaitGroup
	wg.Add(1)
	var toolText string
	var toolErr error
	go func() {
		defer wg.Done()
		toolText, toolErr = d.invokeBrowserTool(context.Background(), "task-a", browserToolSpec{Name: browserToolURL}, nil)
	}()

	commands, _, err := d.browserSessions.takeCommands(context.Background(), session.ID, nil, 5*time.Second)
	if err != nil {
		t.Fatalf("take commands: %v", err)
	}
	if len(commands) != 1 || commands[0].Action != browserToolURL {
		t.Fatalf("commands = %+v, want one %s", commands, browserToolURL)
	}
	results := []browserCommandResult{{ID: commands[0].ID, OK: true, Text: `{"host":"127.0.0.1:3000","path":"/dash"}`}}
	if _, _, err := d.browserSessions.takeCommands(context.Background(), session.ID, results, time.Second); err != nil {
		t.Fatalf("deliver result: %v", err)
	}
	wg.Wait()
	if toolErr != nil {
		t.Fatalf("tool call failed: %v", toolErr)
	}
	if !strings.Contains(toolText, "/dash") {
		t.Fatalf("tool text = %q, want the reported URL", toolText)
	}
}

// The session view must never expose a query string or a local path.
func TestBrowserSessionViewHidesQueryAndPaths(t *testing.T) {
	d := newBrowserTestDaemon(t)
	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-a"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", "/tmp/qa-task-task-a", true); err != nil {
		t.Fatalf("register host: %v", err)
	}
	d.browserSessions.heartbeat(session.ID, 1, true, &browserSessionURL{Host: "127.0.0.1:3000", Path: "/dashboard?token=secret"})

	encoded, err := json.Marshal(d.browserSessions.bySessionID(session.ID).view())
	if err != nil {
		t.Fatalf("marshal view: %v", err)
	}
	if bytes.Contains(encoded, []byte("secret")) {
		t.Fatalf("session view leaked a query value: %s", encoded)
	}
	if bytes.Contains(encoded, []byte("/tmp/")) {
		t.Fatalf("session view leaked a local path: %s", encoded)
	}
}

// The host secret lives beside the profile's other files with 0600, and is
// reused rather than regenerated on every daemon start.
func TestBrowserHostSecretIsStableAndPrivate(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	path, secret, err := ensureBrowserHostSecret("desktop-test")
	if err != nil {
		t.Fatalf("ensure secret: %v", err)
	}
	if len(secret) < 32 {
		t.Fatalf("secret length %d, want at least 32 bytes", len(secret))
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatalf("stat secret: %v", err)
	}
	// Windows has no POSIX mode bits: os.Stat reports 0666 for any writable
	// file there, and the boundary is the profile directory's ACL. Asserting
	// 0600 on Windows would be a test that always fails, not a security check.
	if runtime.GOOS != "windows" {
		if perm := info.Mode().Perm(); perm != 0o600 {
			t.Fatalf("secret mode = %o, want 600", perm)
		}
	}
	if strings.Contains(path, "browser-host.secret") == false {
		t.Fatalf("secret path %q is not the profile's host secret file", path)
	}
	if _, again, err := ensureBrowserHostSecret("desktop-test"); err != nil || again != secret {
		t.Fatalf("secret changed between calls (err=%v); Desktop reads it from disk and would fail to authenticate", err)
	}
}

func TestBrowserURLSanitizerKeepsDisplayValueHarmless(t *testing.T) {
	if got := sanitizeBrowserURLPart("127.0.0.1:3000"); got != "127.0.0.1:3000" {
		t.Fatalf("host sanitized to %q", got)
	}
	if got := sanitizeBrowserURLPart("/a\nb\tc"); strings.ContainsAny(got, "\n\t") {
		t.Fatalf("control characters survived sanitizing: %q", got)
	}
	if got := sanitizeBrowserURLPart(strings.Repeat("a", 4000)); len(got) > 2048 {
		t.Fatalf("sanitized path length %d, want <= 2048", len(got))
	}
}

func TestBrowserURLForLogDropsQuery(t *testing.T) {
	if got := browserURLForLog("http://127.0.0.1:3000/dash?token=secret#frag"); strings.Contains(got, "secret") || strings.Contains(got, "frag") {
		t.Fatalf("log URL kept a query or fragment: %q", got)
	}
}
