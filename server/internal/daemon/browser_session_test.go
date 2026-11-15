package daemon

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
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

// quoteJSON renders a value as a JSON string literal for hand-built request
// bodies, so a traversal payload cannot break the body's own syntax.
func quoteJSON(value string) string {
	encoded, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	return string(encoded)
}

func registerBrowserTestTask(d *Daemon, token, taskID string) {
	d.registerActiveRepoCheckoutTask(token, browserTestTask(taskID))
	// A task this daemon is not running now answers capability=none and the
	// host plane refuses to open a session for it, so every test that expects a
	// usable session records the decision the claim path would have made.
	d.recordBrowserCapability(taskID, browserCapabilityFull)
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
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", true); err != nil {
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
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", true); err != nil {
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
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", true); err != nil {
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

// P1-4: the daemon owns no directory, so closing a session deletes nothing.
//
// The view's partition is non-persistent, so Chromium never writes a profile
// directory for it. This test asserts the absence of a filesystem operation:
// a directory that would previously have been the delete target must survive.
func TestBrowserCleanupDeletesNoDirectory(t *testing.T) {
	d := newBrowserTestDaemon(t)
	survivor := filepath.Join(t.TempDir(), "qa-task-dont-delete")
	if err := os.MkdirAll(survivor, 0o700); err != nil {
		t.Fatalf("create survivor dir: %v", err)
	}
	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-a"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", true); err != nil {
		t.Fatalf("register host: %v", err)
	}

	if _, ok := d.browserSessions.closeBrowserSession(session.ID); !ok {
		t.Fatal("close rejected a hosted session")
	}
	if _, ok := d.browserSessions.ackClosed(session.ID); !ok {
		t.Fatal("ackClosed rejected a closing session")
	}
	if _, statErr := os.Stat(survivor); statErr != nil {
		t.Fatalf("cleanup touched the filesystem: %v", statErr)
	}
	if got := d.browserSessions.bySessionID(session.ID); got != nil {
		t.Fatalf("session %s survived a completed cleanup", session.ID)
	}
}

// P0-1: a task id is an identifier, never a path fragment.
//
// `..\..\..\Windows\Temp\victim` passed the old length check and made
// filepath.Base(dir) == filepath.Base(partition) true, so the delete target
// escaped the managed root while the guard still passed.
func TestBrowserTaskIDRejectsPathTraversal(t *testing.T) {
	traversal := `..\..\..\..\..\..\Windows\Temp\victim-dir`
	if err := validateBrowserTaskID(traversal); err == nil {
		t.Fatal("a path-traversal task id was accepted")
	}
	for _, bad := range []string{
		"task/../../etc",
		"task one",
		"task\x00",
		"",
		strings.Repeat("a", 129),
		"C:\\Windows",
	} {
		if err := validateBrowserTaskID(bad); err == nil {
			t.Fatalf("task id %q was accepted", bad)
		}
	}
	for _, good := range []string{"task-a", "01a1db30e3947273a23530a8c30aa63c", "A_b-9"} {
		if err := validateBrowserTaskID(good); err != nil {
			t.Fatalf("task id %q was rejected: %v", good, err)
		}
	}
}

// P0-1: the partition carries no path semantics even for a valid id.
func TestBrowserPartitionNameIsOpaque(t *testing.T) {
	name := browserPartitionName("task-a")
	if strings.Contains(name, "task-a") {
		t.Fatalf("partition %q echoes the task id; it must be opaque", name)
	}
	if !strings.HasPrefix(name, "qa-task-") {
		t.Fatalf("partition %q lost its prefix", name)
	}
	if name != browserPartitionName("task-a") {
		t.Fatal("partition name is not deterministic; a repeat register would target a different partition")
	}
	if name == browserPartitionName("task-b") {
		t.Fatal("two tasks share one partition; A2 isolation is broken")
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
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", true); err != nil {
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
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", true); err != nil {
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
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", true); err != nil {
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
// ignore any path a caller volunteers.
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

	// A `profile_dir` in the body is not part of the contract any more. It is
	// ignored rather than validated: the daemon deletes nothing, so there is no
	// path whose ownership it would have to police.
	ignored := httptest.NewRecorder()
	d.browserHostRegisterHandler()(ignored, browserHostRequest(http.MethodPost, "/browser/host/register",
		`{"session_id":"`+session.ID+`","task_id":"task-a","profile_dir":"/tmp/not-this-partition"}`))
	if ignored.Code != http.StatusOK {
		t.Fatalf("register with a stray profile_dir = %d %s, want 200", ignored.Code, ignored.Body)
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
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", true); err != nil {
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
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", true); err != nil {
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
// P0-1 closure: a malicious task id is refused on every path that could reach
// the registry or trigger cleanup, and nothing is deleted on any of them.
//
// The review's requirement was explicit: the register entry point alone is not
// closure. So this drives open → register → DELETE → TTL sweep → ack and the
// cleanup call directly, asserting 400/404, an empty registry, and a surviving
// bystander directory at each step.
func TestBrowserMaliciousTaskIDNeverReachesTheRegistry(t *testing.T) {
	traversal := `..\..\..\..\..\..\Windows\Temp\victim-dir`
	bystander := filepath.Join(t.TempDir(), "victim-dir")
	if err := os.MkdirAll(bystander, 0o700); err != nil {
		t.Fatalf("create bystander dir: %v", err)
	}
	sentinel := filepath.Join(bystander, "keep.txt")
	if err := os.WriteFile(sentinel, []byte("must survive"), 0o600); err != nil {
		t.Fatalf("write sentinel: %v", err)
	}

	assertNothingHappened := func(t *testing.T, d *Daemon) {
		t.Helper()
		if sessions := d.browserSessions.list(); len(sessions) != 0 {
			t.Fatalf("registry holds %d sessions after a rejected id: %+v", len(sessions), sessions)
		}
		if _, statErr := os.Stat(sentinel); statErr != nil {
			t.Fatalf("a directory outside the managed root was deleted: %v", statErr)
		}
	}

	t.Run("host open is refused and records nothing", func(t *testing.T) {
		d := newBrowserTestDaemon(t)
		d.recordBrowserCapability(traversal, browserCapabilityFull) // even known, the id is invalid
		rec := httptest.NewRecorder()
		d.browserHostOpenSessionHandler()(rec, browserHostRequest(http.MethodPost, "/browser/host/session",
			`{"task_id":`+quoteJSON(traversal)+`}`))
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("open with a traversal task id = %d %s, want 400", rec.Code, rec.Body)
		}
		assertNothingHappened(t, d)
	})

	t.Run("task plane cannot open one either", func(t *testing.T) {
		d := newBrowserTestDaemon(t)
		d.registerActiveRepoCheckoutTask("mat_evil", browserTestTask(traversal))
		d.recordBrowserCapability(traversal, browserCapabilityFull)
		d.browserHostSeenAt.Store(time.Now().UnixNano())
		rec := httptest.NewRecorder()
		d.browserSessionHandler()(rec, browserTaskRequest(http.MethodPost, "/browser/session", "mat_evil", ""))
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("task-plane create with a traversal task id = %d %s, want 400", rec.Code, rec.Body)
		}
		assertNothingHappened(t, d)
	})

	t.Run("delete, sweep, ack and retry touch no filesystem", func(t *testing.T) {
		d := newBrowserTestDaemon(t)
		now := time.Now()
		d.browserSessions.now = func() time.Time { return now }

		// A legitimate session proves the cleanup paths run: its close/ack/sweep
		// must leave the bystander alone.
		registerBrowserTestTask(d, "mat_ok", "task-ok")
		session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-ok"), browserCapabilityFull)
		if err != nil {
			t.Fatalf("request session: %v", err)
		}
		if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", true); err != nil {
			t.Fatalf("register host: %v", err)
		}

		for _, mutate := range []func(){
			func() { d.browserSessions.closeBrowserSession(session.ID) },
			func() { d.browserSessions.ackClosed(session.ID) },
			func() { now = now.Add(2 * time.Hour); d.browserSessions.sweep() },
			func() { d.browserSessions.cleanupLocked(session) },
			func() {
				// The retry queue no longer exists; the call is idempotent.
				if s := d.browserSessions.bySessionID(session.ID); s != nil {
					d.browserSessions.cleanupLocked(s)
				}
			},
		} {
			mutate()
			if _, statErr := os.Stat(sentinel); statErr != nil {
				t.Fatalf("cleanup path deleted a path outside the managed root: %v", statErr)
			}
		}
		assertNothingHappened(t, d)
	})
}

// P1-3: a task this daemon is not running cannot obtain a usable session.
func TestBrowserUnknownTaskCannotOpenASession(t *testing.T) {
	d := newBrowserTestDaemon(t)
	d.markBrowserHostSeen()

	rec := httptest.NewRecorder()
	d.browserHostOpenSessionHandler()(rec, browserHostRequest(http.MethodPost, "/browser/host/session",
		`{"task_id":"never-claimed-here","issue_id":"i1","workspace_id":"ws"}`))
	if rec.Code != http.StatusNotFound {
		t.Fatalf("open for an unknown task = %d %s, want 404", rec.Code, rec.Body)
	}
	if sessions := d.browserSessions.list(); len(sessions) != 0 {
		t.Fatalf("a refused open left %d sessions behind", len(sessions))
	}

	// And the capability itself is `none`, not `full`, for an unrecorded task.
	if got := d.browserCapabilityForTask("never-claimed-here"); got != browserCapabilityNone {
		t.Fatalf("capability for an unknown task = %q, want none", got)
	}
}

// P1-3: a session built for one task cannot be driven or taken over from
// another task's credential.
func TestBrowserCrossTaskAccessIsRefused(t *testing.T) {
	d := newBrowserTestDaemon(t)
	registerBrowserTestTask(d, "mat_a", "task-a")
	registerBrowserTestTask(d, "mat_b", "task-b")
	d.browserHostSeenAt.Store(time.Now().UnixNano())

	create := httptest.NewRecorder()
	d.browserSessionHandler()(create, browserTaskRequest(http.MethodPost, "/browser/session", "mat_a", ""))
	sessionID := decodeBrowserResponse(t, create).Session.SessionID

	// task-b's own token cannot read, close or take over task-a's session.
	for _, method := range []string{http.MethodGet, http.MethodDelete} {
		rec := httptest.NewRecorder()
		d.browserSessionHandler()(rec, browserTaskRequest(method, "/browser/session?session_id="+sessionID, "mat_b", ""))
		if rec.Code != http.StatusForbidden {
			t.Fatalf("%s of task-a's session as task-b = %d %s, want 403", method, rec.Code, rec.Body)
		}
	}
	// Even the host plane will not retarget the lock onto a foreign session
	// with a mismatched task: takeover has no task field to lie about, so the
	// binding it relies on is the session's own TaskID.
	if got := d.browserSessions.bySessionID(sessionID).TaskID; got != "task-a" {
		t.Fatalf("session task = %q, want task-a: the binding must not be caller-controlled", got)
	}
}

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

// P1-5: console text from the page under test must not reach either sink raw.
//
// There are two sinks — the agent's context (which the agent may paste into a
// comment, and `CreateComment` does not redact) and the run transcript (which
// records the provider's view of the same tool result). Both are fed by the
// MCP response, so the daemon boundary is the one choke point that covers both;
// this test asserts that for each sink independently.
func TestBrowserConsoleIsRedactedForBothSinks(t *testing.T) {
	d := newBrowserTestDaemon(t)
	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-console"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", true); err != nil {
		t.Fatalf("register host: %v", err)
	}
	leaked := []string{"abc123def456cookie", "mat_abcdefghijklmnopqrstuvwxyz012345"}
	consoleText := "log: Cookie: session=abc123def456cookie; theme=dark\n" +
		"log: token=mat_abcdefghijklmnopqrstuvwxyz012345"

	// Start the per-task MCP server and let the host answer the console command.
	config, set, err := startTaskBrowserMCP(context.Background(), d, "task-console", d.logger)
	if err != nil || set == nil {
		t.Fatalf("start browser MCP: config=%s err=%v", config, err)
	}
	defer set.Close()
	var envelope struct {
		MCPServers map[string]struct {
			URL string `json:"url"`
		} `json:"mcpServers"`
	}
	if err := json.Unmarshal(config, &envelope); err != nil {
		t.Fatalf("decode mcp config: %v", err)
	}
	mcpURL := envelope.MCPServers["multica-browser"].URL

	var responseText string
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		body := `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"browser_console"}}`
		response, postErr := http.Post(mcpURL, "application/json", strings.NewReader(body))
		if postErr != nil {
			t.Errorf("console tool call: %v", postErr)
			return
		}
		defer response.Body.Close()
		var decoded struct {
			Result struct {
				Content []struct {
					Text string `json:"text"`
				} `json:"content"`
			} `json:"result"`
		}
		if decodeErr := json.NewDecoder(response.Body).Decode(&decoded); decodeErr != nil {
			t.Errorf("decode console result: %v", decodeErr)
			return
		}
		if len(decoded.Result.Content) > 0 {
			responseText = decoded.Result.Content[0].Text
		}
	}()

	commands, _, err := d.browserSessions.takeCommands(context.Background(), session.ID, nil, 5*time.Second)
	if err != nil || len(commands) != 1 {
		t.Fatalf("take commands = %+v err=%v, want one console command", commands, err)
	}
	results := []browserCommandResult{{ID: commands[0].ID, OK: true, Text: consoleText}}
	if _, _, err := d.browserSessions.takeCommands(context.Background(), session.ID, results, time.Second); err != nil {
		t.Fatalf("deliver console result: %v", err)
	}
	wg.Wait()

	if responseText == "" {
		t.Fatal("the console tool returned nothing")
	}
	for _, secret := range leaked {
		if strings.Contains(responseText, secret) {
			t.Fatalf("MCP response carried %q verbatim: %s", secret, responseText)
		}
	}

	// Chain 1 — console → issue comment. CreateComment only strips NUL bytes,
	// so the text the agent now holds is what would be persisted; assert it is
	// already safe before that sink ever sees it.
	commentBody := "QA log excerpt:\n" + responseText
	for _, secret := range leaked {
		if strings.Contains(commentBody, secret) {
			t.Fatalf("comment body would carry %q: %s", secret, commentBody)
		}
	}

	// Chain 2 — console → transcript. toolOutputPreview sanitizes and truncates
	// but deliberately does not redact (it serves every provider's tool output),
	// which is why the boundary above is the control point: the preview is built
	// from text that is already redacted.
	preview, _ := toolOutputPreview(responseText)
	for _, secret := range leaked {
		if strings.Contains(preview, secret) {
			t.Fatalf("transcript preview would carry %q: %s", secret, preview)
		}
	}
}

// P0-1 (new): an idle session must survive being idle.
//
// A heartbeat's real period is "arrive → block in the long poll → return", so
// the poll length is a floor on the gap between two timestamps. With a 10s poll
// and a 10s timeout, a quiet session orphaned itself and could never recover —
// the view was alive, the person could see it, and every tool failed forever.
func TestBrowserIdleSessionSurvivesTheLongPoll(t *testing.T) {
	d := newBrowserTestDaemon(t)
	now := time.Now()
	d.browserSessions.now = func() time.Time { return now }

	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-idle"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", true); err != nil {
		t.Fatalf("register host: %v", err)
	}

	// Five simulated heartbeat cycles: arrive, sit in the poll, return (touch),
	// then the desktop's next tick 6s later. That is one worst-case iteration at
	// the shipped numbers (2s tick + 4s poll).
	var seq int64
	for cycle := 0; cycle < 5; cycle++ {
		seq++
		if _, accepted := d.browserSessions.heartbeat(session.ID, seq, true, nil); !accepted {
			t.Fatalf("cycle %d: heartbeat rejected while the session was hosted", cycle)
		}
		now = now.Add(browserHostDefaultLongPoll)
		d.browserSessions.touchHost(session.ID) // the poll returned
		d.browserSessions.sweep()
		if got := d.browserSessions.bySessionID(session.ID).State; got != browserStateHosted {
			t.Fatalf("cycle %d: state = %q after a normal poll cycle, want hosted", cycle, got)
		}
		now = now.Add(2 * time.Second) // next tick
		d.browserSessions.sweep()
		if got := d.browserSessions.bySessionID(session.ID).State; got != browserStateHosted {
			t.Fatalf("cycle %d: state = %q while merely idle, want hosted", cycle, got)
		}
	}

	// The budgets themselves must leave headroom: the poll can never approach
	// the timeout, or scheduling jitter alone reintroduces the defect.
	if browserHostDefaultLongPoll*3 > defaultBrowserHeartbeatTimeout {
		t.Fatalf("long poll %s is not comfortably inside the %s heartbeat timeout",
			browserHostDefaultLongPoll, defaultBrowserHeartbeatTimeout)
	}
}

// P0-1 (new), negative half: a host that genuinely stops is still detected.
func TestBrowserDeadHostStillOrphans(t *testing.T) {
	d := newBrowserTestDaemon(t)
	now := time.Now()
	d.browserSessions.now = func() time.Time { return now }
	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-dead"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", true); err != nil {
		t.Fatalf("register host: %v", err)
	}
	if _, ok := d.browserSessions.heartbeat(session.ID, 1, true, nil); !ok {
		t.Fatal("first heartbeat rejected")
	}
	now = now.Add(defaultBrowserHeartbeatTimeout + time.Second)
	d.browserSessions.sweep()
	if got := d.browserSessions.bySessionID(session.ID).State; got != browserStateOrphaned {
		t.Fatalf("state after a real timeout = %q, want orphaned", got)
	}
}

// P1-3 (new): `closing` is not a live state. It has an exit, and nothing may
// treat it as reusable.
func TestBrowserClosingSessionHasAnExit(t *testing.T) {
	d := newBrowserTestDaemon(t)
	now := time.Now()
	d.browserSessions.now = func() time.Time { return now }

	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-close"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", true); err != nil {
		t.Fatalf("register host: %v", err)
	}

	// The window-close path: destroy the view, never ack.
	if _, ok := d.browserSessions.closeBrowserSession(session.ID); !ok {
		t.Fatal("close rejected a hosted session")
	}

	// Not listed for discovery...
	for _, listed := range d.browserSessions.list() {
		if listed.ID == session.ID {
			t.Fatal("a closing session was advertised for discovery; a register would revive it")
		}
	}
	// ...not registered onto...
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "other", true); err == nil {
		t.Fatal("a closing session accepted a register; that is how a closed session came back to hosted")
	}
	// ...and not handed to the next run of the same task.
	reused, err := d.browserSessions.requestBrowserSession(browserTestTask("task-close"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("re-request: %v", err)
	}
	if reused.ID == session.ID {
		t.Fatal("the next run inherited a closing session")
	}
	if reused.State != browserStateRequested {
		t.Fatalf("replacement state = %q, want requested", reused.State)
	}

	// The no-ack timeout finalizes the abandoned one instead of `continue`ing
	// forever: a crashed host must not pin a registry entry.
	now = now.Add(defaultBrowserClosingAckTimeout + time.Second)
	d.browserSessions.sweep()
	if got := d.browserSessions.bySessionID(session.ID); got != nil {
		t.Fatalf("a session nobody acked survived the closing timeout (state=%q)", got.State)
	}
	if sessions := d.browserSessions.list(); len(sessions) != 1 {
		t.Fatalf("registry holds %d sessions after cleanup, want the one replacement", len(sessions))
	}
}

// P1-3 (new): an ack inside the window still finalizes promptly.
func TestBrowserAckStillClosesImmediately(t *testing.T) {
	d := newBrowserTestDaemon(t)
	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-ack"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", true); err != nil {
		t.Fatalf("register host: %v", err)
	}
	d.browserSessions.closeBrowserSession(session.ID)
	if _, ok := d.browserSessions.ackClosed(session.ID); !ok {
		t.Fatal("ack rejected a closing session")
	}
	if got := d.browserSessions.bySessionID(session.ID); got != nil {
		t.Fatal("an acked session stayed in the registry")
	}
}

// P1-4 (new): evidence lands on the issue the daemon recorded, not the one a
// caller claims. The upload runs with the daemon's own credential, so the
// destination must not be an input.
func TestBrowserEvidenceTargetIsDerivedNotDeclared(t *testing.T) {
	d := newBrowserTestDaemon(t)
	// What the claim path records: the truth about this run.
	d.recordBrowserTask("task-target", browserCapabilityFull, "issue-real", "ws-real")
	d.markBrowserHostSeen()

	// A caller tries to redirect the write.
	rec := httptest.NewRecorder()
	d.browserHostOpenSessionHandler()(rec, browserHostRequest(http.MethodPost, "/browser/host/session",
		`{"task_id":"task-target","issue_id":"issue-SOMEONE-ELSE","workspace_id":"ws-SOMEONE-ELSE"}`))
	if rec.Code != http.StatusOK {
		t.Fatalf("open = %d %s", rec.Code, rec.Body)
	}
	session := decodeBrowserResponse(t, rec).Session
	if session.IssueID != "issue-real" {
		t.Fatalf("session bound issue %q, want the daemon's issue-real: a declared target was trusted",
			session.IssueID)
	}
	if bound := d.browserSessions.bySessionID(session.SessionID); bound.WorkspaceID != "ws-real" {
		t.Fatalf("session bound workspace %q, want the daemon's ws-real", bound.WorkspaceID)
	}

	if _, err := d.browserSessions.registerBrowserHost(session.SessionID, "win", true); err != nil {
		t.Fatalf("register host: %v", err)
	}

	var gotWorkspace, gotIssue string
	d.browserEvidenceUploader = func(_ context.Context, workspaceID, issueID, _ string, _ []byte) (string, error) {
		gotWorkspace, gotIssue = workspaceID, issueID
		return "att_bound", nil
	}
	png := bytes.Repeat([]byte{0x89}, 32)
	sum := sha256.Sum256(png)
	target := "/browser/host/evidence?session_id=" + session.SessionID +
		"&bytes=" + strconv.Itoa(len(png)) + "&sha256=" + hex.EncodeToString(sum[:])
	req := browserHostRequest(http.MethodPost, target, string(png))
	req.Header.Set("Content-Type", "image/png")
	er := httptest.NewRecorder()
	d.browserHostEvidenceHandler()(er, req)
	if er.Code != http.StatusOK {
		t.Fatalf("evidence = %d %s", er.Code, er.Body)
	}
	if gotIssue != "issue-real" || gotWorkspace != "ws-real" {
		t.Fatalf("evidence uploaded to %s/%s, want the daemon's issue-real/ws-real", gotWorkspace, gotIssue)
	}
}

// P1-4 (new): a task with no recorded target cannot anchor a write at all.
func TestBrowserEvidenceRequiresARecordedTarget(t *testing.T) {
	d := newBrowserTestDaemon(t)
	d.recordBrowserCapability("task-no-target", browserCapabilityFull)
	d.markBrowserHostSeen()

	rec := httptest.NewRecorder()
	d.browserHostOpenSessionHandler()(rec, browserHostRequest(http.MethodPost, "/browser/host/session",
		`{"task_id":"task-no-target","issue_id":"issue-claimed","workspace_id":"ws-claimed"}`))
	if rec.Code != http.StatusNotFound {
		t.Fatalf("open for a task with no recorded issue/workspace = %d %s, want 404", rec.Code, rec.Body)
	}
	if sessions := d.browserSessions.list(); len(sessions) != 0 {
		t.Fatalf("a refused open left %d sessions", len(sessions))
	}
}

// R3: the real attachment path, against the real server.
//
// Every other evidence assertion stops at an injected uploader, so "the bytes
// reached the daemon" was proven and "the bytes became an attachment" was not.
// This test closes that gap by calling the production `uploadIssueEvidence`
// with the daemon's own credential. It is opt-in because it writes a row to the
// live workspace:
//
//	MULTICA_RUN_REAL_ATTACHMENT_SMOKE=1 MULTICA_SMOKE_ISSUE=<id> \
//	MULTICA_SMOKE_WORKSPACE=<id> go test ./internal/daemon/ -run TestBrowserRealServerAttachment -v
func TestBrowserRealServerAttachment(t *testing.T) {
	if os.Getenv("MULTICA_RUN_REAL_ATTACHMENT_SMOKE") != "1" {
		t.Skip("set MULTICA_RUN_REAL_ATTACHMENT_SMOKE=1 to write a real attachment")
	}
	issueID := os.Getenv("MULTICA_SMOKE_ISSUE")
	workspaceID := os.Getenv("MULTICA_SMOKE_WORKSPACE")
	if issueID == "" || workspaceID == "" {
		t.Fatal("MULTICA_SMOKE_ISSUE and MULTICA_SMOKE_WORKSPACE are required")
	}
	serverURL := os.Getenv("MULTICA_SMOKE_SERVER")
	if serverURL == "" {
		serverURL = "https://api.multica.ai"
	}
	token := strings.TrimSpace(os.Getenv("MULTICA_SMOKE_TOKEN"))
	if token == "" {
		t.Fatal("MULTICA_SMOKE_TOKEN is required")
	}

	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	d := &Daemon{logger: logger}
	d.cfg.ServerBaseURL = serverURL
	d.client = NewClient(serverURL)
	d.client.SetToken(token)

	// A tiny but real PNG: the uploader's job is transport, and a 1x1 keeps the
	// round trip fast while still exercising content-type sniffing end to end.
	png := mustDecodePNG1x1(t)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	id, err := d.uploadIssueEvidence(ctx, workspaceID, issueID, "qa-browser-smoke.png", png)
	if err != nil {
		t.Fatalf("real attachment upload failed: %v", err)
	}
	if id == "" {
		t.Fatal("server returned an empty attachment id")
	}
	t.Logf("REAL ATTACHMENT id=%s bytes=%d server=%s", id, len(png), serverURL)
}

// mustDecodePNG1x1 returns a minimal valid PNG, so the server's content sniffing
// sees a real image rather than a renamed blob.
func mustDecodePNG1x1(t *testing.T) []byte {
	t.Helper()
	const encoded = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
	data, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		t.Fatalf("decode fixture png: %v", err)
	}
	return data
}

// P1-10: a heartbeat that reports a dead view ends the session.
//
// The defect was that `orphaned` had no time-driven exit of its own while every
// heartbeat refreshed the idle expiry, so a browser the user had closed lived
// forever and each "open" click revived it for exactly one heartbeat. A dead
// view is phase-two evidence, so the session closes instead of parking.
func TestBrowserDeadViewHeartbeatEndsTheSession(t *testing.T) {
	d := newBrowserTestDaemon(t)
	now := time.Now()
	d.browserSessions.now = func() time.Time { return now }

	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-deadview"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", true); err != nil {
		t.Fatalf("register host: %v", err)
	}

	// The user closed the QA window: the host keeps heartbeating but reports
	// view_alive=false on every tick.
	var seq int64
	for tick := 0; tick < 10; tick++ {
		seq++
		now = now.Add(2 * time.Second)
		d.browserSessions.heartbeat(session.ID, seq, false, nil)
		d.browserSessions.sweep()
	}

	if got := d.browserSessions.bySessionID(session.ID); got != nil {
		t.Fatalf("a session whose view has been dead for %d heartbeats is still in the registry (state=%q)",
			seq, got.State)
	}
	if sessions := d.browserSessions.list(); len(sessions) != 0 {
		t.Fatalf("registry still advertises %d sessions", len(sessions))
	}
}

// P1-10: a dead view must not extend its own lifetime, even if it is somehow
// still `orphaned` rather than closing.
func TestBrowserDeadViewDoesNotRefreshIdle(t *testing.T) {
	d := newBrowserTestDaemon(t)
	now := time.Now()
	d.browserSessions.now = func() time.Time { return now }

	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-noidle"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", true); err != nil {
		t.Fatalf("register host: %v", err)
	}
	before := d.browserSessions.bySessionID(session.ID).IdleExpiresAt

	now = now.Add(3 * time.Second)
	d.browserSessions.heartbeat(session.ID, 1, false, nil)

	// Whether the entry is gone (closing path) or merely no longer refreshed,
	// the idle expiry must not have moved forward.
	if got := d.browserSessions.bySessionID(session.ID); got != nil && got.IdleExpiresAt.After(before) {
		t.Fatalf("a dead-view heartbeat pushed idle expiry from %s to %s", before, got.IdleExpiresAt)
	}
}

// P1-10: a live view still refreshes normally, so the guard above is not just
// "never refresh".
func TestBrowserLiveViewHeartbeatStillRefreshesIdle(t *testing.T) {
	d := newBrowserTestDaemon(t)
	now := time.Now()
	d.browserSessions.now = func() time.Time { return now }
	session, err := d.browserSessions.requestBrowserSession(browserTestTask("task-liveidle"), browserCapabilityFull)
	if err != nil {
		t.Fatalf("request session: %v", err)
	}
	if _, err := d.browserSessions.registerBrowserHost(session.ID, "win", true); err != nil {
		t.Fatalf("register host: %v", err)
	}
	before := d.browserSessions.bySessionID(session.ID).IdleExpiresAt
	now = now.Add(3 * time.Second)
	if _, ok := d.browserSessions.heartbeat(session.ID, 1, true, nil); !ok {
		t.Fatal("live heartbeat rejected")
	}
	if got := d.browserSessions.bySessionID(session.ID).IdleExpiresAt; !got.After(before) {
		t.Fatalf("a live heartbeat did not refresh idle: %s -> %s", before, got)
	}
}
