package daemon

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// QA built-in isolated browser: the HTTP planes (SDD §3).
//
// Two credential planes share one set of handlers but never one credential:
//   - task plane   (/browser/session...) uses the task token the agent already
//     carries, resolved through the same in-memory registry as /repo/checkout,
//     so "task finished" revokes it with no new token system;
//   - host plane   (/browser/host/...) uses a per-profile random secret that
//     only Desktop can read off disk. It never travels over HTTP.
//
// Every guard below is load-bearing:
//   - Origin must be empty and the method must match: a page's POST always
//     carries an Origin, and this server answers no CORS preflight, so this is
//     what keeps a random web page from driving a localhost daemon.
//   - The two body groups have different limits (64 KiB JSON / 6 MiB PNG) and
//     must not be swapped: one number per group, and oversize is always 413.
//   - Chunked evidence is 411, not 400: the client chose the wrong transport,
//     and the fix (declare a length) is different from "the artifact is too
//     big".

const (
	browserJSONBodyLimitBytes = 64 << 10 // 64 KiB
	browserEvidenceMaxBytes   = 6 << 20  // 6 MiB transport cap
	browserHostSecretFile     = "browser-host.secret"
	browserHostLongPollBudget = 25 * time.Second
)

// browserResponse is the single success/error envelope. Desktop needs to tell
// "daemon too old" from "business failure", so errors are JSON with a stable
// code rather than bare text.
type browserResponse struct {
	Code    string              `json:"code"`
	Message string              `json:"message,omitempty"`
	Session *browserSessionView `json:"session,omitempty"`
}

func writeBrowserJSON(w http.ResponseWriter, status int, body browserResponse) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

func writeBrowserError(w http.ResponseWriter, status int, code, message string) {
	writeBrowserJSON(w, status, browserResponse{Code: code, Message: message})
}

// browserTransportError maps a transport-layer failure onto its status and
// code. These five never become MCP tool error codes: the agent cannot fix
// any of them from inside a tool call.
func browserTransportError(w http.ResponseWriter, status int, message string) {
	code := "BAD_REQUEST"
	switch status {
	case http.StatusForbidden:
		code = "FORBIDDEN"
	case http.StatusLengthRequired:
		code = "LENGTH_REQUIRED"
	case http.StatusRequestEntityTooLarge:
		code = "PAYLOAD_TOO_LARGE"
	case http.StatusUnsupportedMediaType:
		code = "UNSUPPORTED_MEDIA_TYPE"
	case http.StatusRequestTimeout:
		code = "TIMEOUT"
	}
	writeBrowserError(w, status, code, message)
}

// browserJSONGuard enforces the JSON group's preconditions and returns the
// decoded body. The returned bool reports whether the caller may continue.
func browserJSONGuard(w http.ResponseWriter, r *http.Request) ([]byte, bool) {
	if r.Method != http.MethodPost {
		writeBrowserError(w, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "POST only")
		return nil, false
	}
	if r.Header.Get("Origin") != "" {
		browserTransportError(w, http.StatusForbidden, "browser requests come from Desktop, as JSON, without an Origin")
		return nil, false
	}
	mediaType, _, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if mediaType != "application/json" {
		browserTransportError(w, http.StatusUnsupportedMediaType, "JSON endpoints require application/json")
		return nil, false
	}
	if r.ContentLength > browserJSONBodyLimitBytes {
		browserTransportError(w, http.StatusRequestEntityTooLarge, "request body exceeds 64 KiB")
		return nil, false
	}
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, browserJSONBodyLimitBytes))
	if err != nil {
		var maxErr *http.MaxBytesError
		if errors.As(err, &maxErr) {
			browserTransportError(w, http.StatusRequestEntityTooLarge, "request body exceeds 64 KiB")
			return nil, false
		}
		browserTransportError(w, http.StatusBadRequest, "could not read request body: "+err.Error())
		return nil, false
	}
	return body, true
}

// browserHostSecretPath is where the daemon writes the host credential. It
// lives beside the profile's other state files, never in the repository.
func browserHostSecretPath(profile string) (string, error) {
	dir, err := browserProfileDir(profile)
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, browserHostSecretFile), nil
}

// ensureBrowserHostSecret returns the host secret, creating it on first use
// with 0600 permissions. The value never enters a log line or a prompt.
func ensureBrowserHostSecret(profile string) (string, string, error) {
	path, err := browserHostSecretPath(profile)
	if err != nil {
		return "", "", err
	}
	if data, readErr := os.ReadFile(path); readErr == nil {
		secret := strings.TrimSpace(string(data))
		if len(secret) >= 32 {
			return path, secret, nil
		}
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return "", "", fmt.Errorf("create browser host secret directory: %w", err)
	}
	secret, err := randomBrokerToken()
	if err != nil {
		return "", "", err
	}
	// Write through a temp file so two daemons starting together cannot leave
	// a half-written secret behind.
	//
	// 0600 is a real boundary on POSIX. Windows has no POSIX mode bits — the
	// file inherits the profile directory's ACL, which is already the user's
	// private tree — so the mode argument is advisory there and the explicit
	// Chmod below makes that visible instead of pretending it took effect.
	temp := path + ".tmp"
	if err := os.WriteFile(temp, []byte(secret+"\n"), 0o600); err != nil {
		return "", "", fmt.Errorf("write browser host secret: %w", err)
	}
	if err := os.Chmod(temp, 0o600); err != nil {
		return "", "", fmt.Errorf("restrict browser host secret: %w", err)
	}
	if err := os.Rename(temp, path); err != nil {
		_ = os.Remove(temp)
		return "", "", fmt.Errorf("publish browser host secret: %w", err)
	}
	return path, secret, nil
}

// checkBrowserHostSecret compares the presented bearer value in constant time.
func checkBrowserHostSecret(header, secret string) bool {
	const bearer = "Bearer "
	trimmed := strings.TrimSpace(header)
	if !strings.HasPrefix(trimmed, bearer) {
		return false
	}
	presented := strings.TrimSpace(strings.TrimPrefix(trimmed, bearer))
	if presented == "" || secret == "" {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(presented), []byte(secret)) == 1
}

// browserTaskSession resolves the caller's session from the task token and
// asserts the session belongs to that task. The request body is never an
// identity source: a session id from another task is a 403, not a lookup.
func (d *Daemon) browserTaskSession(w http.ResponseWriter, r *http.Request) (*browserSession, activeRepoCheckoutTask, bool) {
	task, result := d.activeRepoCheckoutTask(r)
	if result != repoCheckoutAuthOK {
		browserTransportError(w, http.StatusUnauthorized, "task credential is not bound to a running task in this daemon")
		return nil, task, false
	}
	id := strings.TrimSpace(r.URL.Query().Get("session_id"))
	if id == "" {
		return nil, task, true
	}
	session := d.browserSessions.bySessionID(id)
	if session == nil {
		return nil, task, true
	}
	if session.TaskID != task.TaskID {
		browserTransportError(w, http.StatusForbidden, "browser session belongs to another task")
		return nil, task, false
	}
	return session, task, true
}

// browserSessionHandler serves POST/GET/DELETE /browser/session.
func (d *Daemon) browserSessionHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodPost:
			d.browserSessionCreate(w, r)
		case http.MethodGet:
			d.browserSessionGet(w, r)
		case http.MethodDelete:
			d.browserSessionDelete(w, r)
		default:
			writeBrowserError(w, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "POST, GET or DELETE")
		}
	}
}

func (d *Daemon) browserSessionCreate(w http.ResponseWriter, r *http.Request) {
	if _, ok := browserJSONGuard(w, r); !ok {
		return
	}
	task, result := d.activeRepoCheckoutTask(r)
	if result != repoCheckoutAuthOK {
		browserTransportError(w, http.StatusUnauthorized, "task credential is not bound to a running task in this daemon")
		return
	}
	// Capability is derived, never caller-supplied: a request that could claim
	// "full" for a provider that cannot carry HTTP MCP would produce a sidebar
	// entry whose tools do not exist. The body is therefore accepted and
	// ignored rather than parsed into a decision.
	capability := d.browserCapabilityForTask(task.TaskID)
	session, err := d.browserSessions.requestBrowserSession(task, capability)
	if err != nil {
		if errors.Is(err, errBrowserInvalidTaskID) {
			browserTransportError(w, http.StatusBadRequest, err.Error())
			return
		}
		writeBrowserError(w, http.StatusInternalServerError, "INTERNAL", err.Error())
		return
	}
	if capability == browserCapabilityNone {
		// The task's provider cannot carry an HTTP MCP server, so the agent
		// will never reach the tools. Say so on the view instead of failing
		// silently (SDD §8 item 3).
		writeBrowserJSON(w, http.StatusOK, browserResponse{
			Code:    browserCodeCapabilityUnsupported,
			Message: "this task's agent provider cannot host the browser tools",
			Session: new(session.view()),
		})
		return
	}
	if session.State == browserStateRequested && !d.browserHostOnline() {
		// D3: no Desktop means a hard error, never a queue. The user has to
		// open Desktop; an agent cannot wait for it — so the session becomes
		// terminal here instead of sitting in `requested` forever.
		message := "no Multica Desktop is hosting browser views on this machine"
		failed := d.browserSessions.fail(session.ID, browserCodeHostOffline, message)
		if failed == nil {
			failed = session
		}
		writeBrowserJSON(w, http.StatusServiceUnavailable, browserResponse{
			Code:    browserCodeHostOffline,
			Message: message,
			Session: new(failed.view()),
		})
		return
	}
	writeBrowserJSON(w, http.StatusOK, browserResponse{Code: "OK", Session: new(session.view())})
}

func (d *Daemon) browserSessionGet(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("Origin") != "" {
		browserTransportError(w, http.StatusForbidden, "browser requests come from Desktop or the task CLI, without an Origin")
		return
	}
	session, _, ok := d.browserTaskSession(w, r)
	if !ok {
		return
	}
	if session == nil {
		writeBrowserError(w, http.StatusNotFound, browserCodeSessionUnavailable, "this task has no browser session")
		return
	}
	if session.State == browserStateClosed {
		// Reading a closed session is a 410: the caller asked about a session
		// that no longer exists, and "closed" is the answer. DELETE keeps 200
		// for the same state, because there the caller is asking for an end
		// state it already has (invariant I6).
		writeBrowserJSON(w, http.StatusGone, browserResponse{
			Code:    browserCodeSessionClosed,
			Message: "browser session is closed",
			Session: new(session.view()),
		})
		return
	}
	writeBrowserJSON(w, http.StatusOK, browserResponse{Code: "OK", Session: new(session.view())})
}

func (d *Daemon) browserSessionDelete(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("Origin") != "" {
		browserTransportError(w, http.StatusForbidden, "browser requests come from Desktop or the task CLI, without an Origin")
		return
	}
	session, task, ok := d.browserTaskSession(w, r)
	if !ok {
		return
	}
	if session == nil {
		// Deleting a session that never existed is not an error: the task is
		// asking for an end state it already has.
		writeBrowserJSON(w, http.StatusOK, browserResponse{Code: "OK"})
		return
	}
	closed, found := d.browserSessions.closeBrowserSession(session.ID)
	if !found {
		writeBrowserJSON(w, http.StatusOK, browserResponse{Code: "OK"})
		return
	}
	d.logger.Info("browser session closing", "session_id", closed.ID, "task_id", task.TaskID)
	writeBrowserJSON(w, http.StatusOK, browserResponse{Code: "OK", Session: new(closed.view())})
}

// browserHostSessionHandler serves the lock endpoints, which accept only the
// host secret. If an agent token could release a person's lock, the lock would
// enforce nothing.
func (d *Daemon) browserHostSessionHandler(action string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		body, ok := browserJSONGuard(w, r)
		if !ok {
			return
		}
		if !d.browserHostAuthorized(w, r) {
			return
		}
		var request struct {
			SessionID string `json:"session_id"`
		}
		if err := json.Unmarshal(body, &request); err != nil {
			browserTransportError(w, http.StatusBadRequest, "invalid request body: "+err.Error())
			return
		}
		sessionID := strings.TrimSpace(request.SessionID)
		if sessionID == "" {
			browserTransportError(w, http.StatusBadRequest, "session_id is required")
			return
		}
		var (
			session *browserSession
			err     error
		)
		if action == "takeover" {
			session, err = d.browserSessions.takeover(sessionID)
		} else {
			session, err = d.browserSessions.release(sessionID)
		}
		switch {
		case err == nil:
			writeBrowserJSON(w, http.StatusOK, browserResponse{Code: "OK", Session: new(session.view())})
		case errors.Is(err, errBrowserUnknownSession):
			writeBrowserError(w, http.StatusNotFound, browserCodeSessionUnavailable, err.Error())
		case errors.Is(err, errBrowserSessionClosed):
			writeBrowserError(w, http.StatusGone, browserCodeSessionClosed, err.Error())
		default:
			writeBrowserError(w, http.StatusConflict, browserCodeSessionUnavailable, err.Error())
		}
	}
}

func (d *Daemon) browserHostAuthorized(w http.ResponseWriter, r *http.Request) bool {
	secret, err := d.browserHostSecret()
	if err != nil {
		writeBrowserError(w, http.StatusServiceUnavailable, "INTERNAL", "browser host credential unavailable")
		return false
	}
	if !checkBrowserHostSecret(r.Header.Get("Authorization"), secret) {
		browserTransportError(w, http.StatusUnauthorized, "browser host credential is invalid")
		return false
	}
	return true
}

// browserHostRegisterHandler attaches a Desktop view to a requested session.
func (d *Daemon) browserHostRegisterHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		body, ok := browserJSONGuard(w, r)
		if !ok {
			return
		}
		if !d.browserHostAuthorized(w, r) {
			return
		}
		var request struct {
			SessionID string `json:"session_id"`
			TaskID    string `json:"task_id"`
			WindowID  string `json:"window_id"`
			CDPReady  bool   `json:"cdp_ready"`
		}
		if err := json.Unmarshal(body, &request); err != nil {
			browserTransportError(w, http.StatusBadRequest, "invalid request body: "+err.Error())
			return
		}
		session := d.browserSessions.bySessionID(strings.TrimSpace(request.SessionID))
		if session == nil {
			writeBrowserError(w, http.StatusNotFound, browserCodeSessionUnavailable, "unknown browser session")
			return
		}
		if session.TaskID != strings.TrimSpace(request.TaskID) {
			browserTransportError(w, http.StatusForbidden, "session does not belong to the reported task")
			return
		}
		// No directory is accepted here: the view's partition is non-persistent,
		// so the daemon owns no path and deletes nothing. A caller-supplied
		// `profile_dir` used to be the input to `os.RemoveAll`; with the
		// argument gone, so is the attack surface.
		updated, err := d.browserSessions.registerBrowserHost(session.ID, request.WindowID, request.CDPReady)
		if err != nil {
			status := http.StatusConflict
			if errors.Is(err, errBrowserUnknownSession) {
				status = http.StatusNotFound
			}
			writeBrowserError(w, status, browserCodeSessionUnavailable, err.Error())
			return
		}
		d.logger.Info("browser host registered", "session_id", updated.ID, "task_id", updated.TaskID)
		writeBrowserJSON(w, http.StatusOK, browserResponse{Code: "OK", Session: new(updated.view())})
	}
}

// browserHostHeartbeatHandler records liveness, applies reported pointer/tool
// results, and answers with a directive plus any queued commands.
func (d *Daemon) browserHostHeartbeatHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		body, ok := browserJSONGuard(w, r)
		if !ok {
			return
		}
		if !d.browserHostAuthorized(w, r) {
			return
		}
		var request struct {
			SessionID  string                 `json:"session_id"`
			Seq        int64                  `json:"seq"`
			ViewAlive  bool                   `json:"view_alive"`
			URLHost    string                 `json:"url_host"`
			URLPath    string                 `json:"url_path"`
			Results    []browserCommandResult `json:"results,omitempty"`
			LongPollMs int                    `json:"long_poll_ms,omitempty"`
		}
		if err := json.Unmarshal(body, &request); err != nil {
			browserTransportError(w, http.StatusBadRequest, "invalid request body: "+err.Error())
			return
		}
		sessionID := strings.TrimSpace(request.SessionID)
		if sessionID == "" {
			browserTransportError(w, http.StatusBadRequest, "session_id is required")
			return
		}
		// Heartbeat is the one call Desktop cannot skip, so it refreshes the
		// host-seen clock the task plane uses to answer BROWSER_HOST_OFFLINE.
		// Leaving it out made "is a host online" depend on call ordering.
		d.markBrowserHostSeen()
		var reportedURL *browserSessionURL
		if request.URLHost != "" {
			reportedURL = &browserSessionURL{Host: sanitizeBrowserURLPart(request.URLHost), Path: sanitizeBrowserURLPart(request.URLPath)}
		}
		if _, accepted := d.browserSessions.heartbeat(sessionID, request.Seq, request.ViewAlive, reportedURL); !accepted {
			session := d.browserSessions.bySessionID(sessionID)
			if session == nil {
				writeBrowserError(w, http.StatusNotFound, browserCodeSessionUnavailable, "unknown browser session")
				return
			}
			// A stale sequence is dropped without changing state; the host
			// still gets the directive so a closing session is not stuck.
			writeBrowserJSON(w, http.StatusOK, browserResponse{
				Code:    "STALE_SEQ",
				Session: new(session.view()),
			})
			return
		}
		budget := browserHostLongPollBudget
		if request.LongPollMs > 0 && request.LongPollMs <= int(browserHostLongPollBudget/time.Millisecond) {
			budget = time.Duration(request.LongPollMs) * time.Millisecond
		}
		commands, session, err := d.browserSessions.takeCommands(r.Context(), sessionID, request.Results, budget)
		if err != nil && !errors.Is(err, errBrowserUnknownSession) {
			if r.Context().Err() != nil {
				return
			}
		}
		if session == nil {
			writeBrowserError(w, http.StatusNotFound, browserCodeSessionUnavailable, "unknown browser session")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"code":      "OK",
			"session":   session.view(),
			"directive": browserHostDirective(session),
			"commands":  commands,
		})
	}
}

// browserHostClosedHandler is the phase-two ack: the view is gone, so the
// partition directory can be removed.
func (d *Daemon) browserHostClosedHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		body, ok := browserJSONGuard(w, r)
		if !ok {
			return
		}
		if !d.browserHostAuthorized(w, r) {
			return
		}
		var request struct {
			SessionID string `json:"session_id"`
		}
		if err := json.Unmarshal(body, &request); err != nil {
			browserTransportError(w, http.StatusBadRequest, "invalid request body: "+err.Error())
			return
		}
		sessionID := strings.TrimSpace(request.SessionID)
		if sessionID == "" {
			browserTransportError(w, http.StatusBadRequest, "session_id is required")
			return
		}
		session, found := d.browserSessions.ackClosed(sessionID)
		if !found {
			writeBrowserError(w, http.StatusNotFound, browserCodeSessionUnavailable, "unknown browser session")
			return
		}
		d.logger.Info("browser host acknowledged view destruction", "session_id", sessionID)
		writeBrowserJSON(w, http.StatusOK, browserResponse{Code: "OK", Session: new(session.view())})
	}
}

// browserHostEvidenceHandler accepts one PNG. It is the only binary endpoint,
// and its length semantics are what make "the bytes match" checkable
// (SDD §3.1.1).
func (d *Daemon) browserHostEvidenceHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			writeBrowserError(w, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "POST only")
			return
		}
		if r.Header.Get("Origin") != "" {
			browserTransportError(w, http.StatusForbidden, "evidence comes from Desktop, without an Origin")
			return
		}
		// Content type is checked before the credential so a client that sent
		// the wrong body shape is told to fix that, not its secret.
		mediaType, _, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
		if mediaType != "image/png" {
			browserTransportError(w, http.StatusUnsupportedMediaType, "evidence must be image/png")
			return
		}
		if !d.browserHostAuthorized(w, r) {
			return
		}
		// A declared length is required: it is the only way the artifact
		// checksum has a byte count to compare against.
		if r.ContentLength < 0 {
			browserTransportError(w, http.StatusLengthRequired, "evidence requires a Content-Length; chunked uploads are not accepted")
			return
		}
		if r.ContentLength > browserEvidenceMaxBytes {
			browserTransportError(w, http.StatusRequestEntityTooLarge, "evidence exceeds the 6 MiB transport limit")
			return
		}
		query := r.URL.Query()
		sessionID := strings.TrimSpace(query.Get("session_id"))
		if sessionID == "" {
			browserTransportError(w, http.StatusBadRequest, "session_id is required")
			return
		}
		session := d.browserSessions.bySessionID(sessionID)
		if session == nil {
			writeBrowserError(w, http.StatusNotFound, browserCodeSessionUnavailable, "unknown browser session")
			return
		}
		declaredBytes, err := strconv.ParseInt(strings.TrimSpace(query.Get("bytes")), 10, 64)
		if err != nil || declaredBytes < 0 {
			browserTransportError(w, http.StatusBadRequest, "bytes must be a non-negative integer")
			return
		}
		declaredSum := strings.ToLower(strings.TrimSpace(query.Get("sha256")))
		if declaredSum == "" {
			browserTransportError(w, http.StatusBadRequest, "sha256 is required")
			return
		}
		data, err := io.ReadAll(http.MaxBytesReader(w, r.Body, browserEvidenceMaxBytes))
		if err != nil {
			var maxErr *http.MaxBytesError
			if errors.As(err, &maxErr) {
				browserTransportError(w, http.StatusRequestEntityTooLarge, "evidence exceeds the 6 MiB transport limit")
				return
			}
			browserTransportError(w, http.StatusBadRequest, "could not read evidence body: "+err.Error())
			return
		}
		// The actual read length is the authority; the header was already
		// trusted once for the transport decision and must not be compared
		// again here (it would contradict the chunked guard above).
		if int64(len(data)) != declaredBytes {
			browserTransportError(w, http.StatusBadRequest, "evidence byte count does not match the declared bytes")
			return
		}
		sum := sha256.Sum256(data)
		if hex.EncodeToString(sum[:]) != declaredSum {
			browserTransportError(w, http.StatusBadRequest, "evidence sha256 does not match the declared digest")
			return
		}
		attachmentID, err := d.storeBrowserEvidence(r, session, data, query.Get("url_host"), query.Get("url_path"))
		if err != nil {
			d.logger.Warn("browser evidence could not be attached", "session_id", session.ID, "error", err)
			writeBrowserError(w, http.StatusBadGateway, browserCodeEvidenceFailed, "evidence could not be attached to the issue")
			return
		}
		d.browserSessions.recordEvidence(session.ID, attachmentID)
		if host, path := sanitizeBrowserURLPart(query.Get("url_host")), sanitizeBrowserURLPart(query.Get("url_path")); host != "" {
			d.browserSessions.heartbeat(session.ID, 0, true, &browserSessionURL{Host: host, Path: path})
		}
		writeBrowserJSON(w, http.StatusOK, browserResponse{
			Code:    "OK",
			Session: new(d.browserSessions.bySessionID(session.ID).view()),
		})
	}
}

// storeBrowserEvidence uploads the PNG through the existing attachment path and
// returns its id. The image itself is not sanitised — redaction is text-only —
// so the control is "whether to capture", decided by the caller (D5).
func (d *Daemon) storeBrowserEvidence(r *http.Request, session *browserSession, data []byte, urlHost, urlPath string) (string, error) {
	if d.browserEvidenceUploader == nil {
		return "", errors.New("no attachment uploader configured")
	}
	filename := fmt.Sprintf("qa-browser-%s-%d.png", session.ID, time.Now().UTC().Unix())
	ctx, cancel := context.WithTimeout(r.Context(), 60*time.Second)
	defer cancel()
	return d.browserEvidenceUploader(ctx, session.WorkspaceID, session.IssueID, filename, data)
}

// sanitizeBrowserURLPart enforces the display-only whitelist: the value is
// never parsed into a route decision, only shown, so it is bounded and
// character-checked rather than decoded.
//
// A query or fragment is cut here rather than trusted from the host: the host
// reports `pathname` alone, but a compromised or buggy host must not be able to
// turn the session view into a token carrier (SDD §3.3).
func sanitizeBrowserURLPart(value string) string {
	value = strings.TrimSpace(value)
	if cut := strings.IndexAny(value, "?#"); cut >= 0 {
		value = value[:cut]
	}
	if value == "" {
		return ""
	}
	if len(value) > 2048 {
		value = value[:2048]
	}
	var b strings.Builder
	for _, r := range value {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9':
			b.WriteRune(r)
		case strings.ContainsRune("-._~:/?#[]@!$&'()*+,;=%", r):
			b.WriteRune(r)
		default:
			b.WriteRune('?')
		}
	}
	return b.String()
}
