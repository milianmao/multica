package daemon

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/multica-ai/multica/server/internal/cli"
)

// QA built-in isolated browser: daemon-side wiring.
//
// Everything the feature needs hangs off a few fields on Daemon, all created
// in New() and torn down with the process. No session is persisted: a restart
// closes every session by definition (SDD §2).

// browserEvidenceUploader stores one PNG and returns its attachment id.
// Injected so tests never touch the network.
type browserEvidenceUploader func(ctx context.Context, workspaceID, issueID, filename string, data []byte) (string, error)

// initBrowserSessions prepares the registry and the host credential.
// A failure here degrades the feature to "no sessions" rather than failing
// startup: an unusable browser must never stop task dispatch.
func (d *Daemon) initBrowserSessions() {
	d.browserSessions = newBrowserSessionRegistry(d.logger)
	d.browserEvidenceUploader = d.uploadIssueEvidence
	path, secret, err := ensureBrowserHostSecret(d.cfg.Profile)
	if err != nil {
		d.logger.Warn("browser host credential unavailable; the QA browser stays disabled", "error", err)
		return
	}
	d.browserHostSecretPath = path
	d.browserHostSecretValue = secret
}

// browserHostSecret returns the credential Desktop must present. Read once at
// startup; the value never enters a log line.
func (d *Daemon) browserHostSecret() (string, error) {
	if d.browserHostSecretValue != "" {
		return d.browserHostSecretValue, nil
	}
	path, secret, err := ensureBrowserHostSecret(d.cfg.Profile)
	if err != nil {
		return "", err
	}
	d.browserHostSecretPath = path
	d.browserHostSecretValue = secret
	return secret, nil
}

// browserProfileDir is the profile's state directory, used only to place the
// host secret beside the daemon's other files.
func browserProfileDir(profile string) (string, error) {
	return cli.ProfileDir(profile)
}

// uploadIssueEvidence attaches a screenshot to the session's issue through the
// existing attachment endpoint using the daemon's own credential. Same path as
// `multica attachment upload`, so retention, signing and workspace scoping are
// unchanged.
func (d *Daemon) uploadIssueEvidence(ctx context.Context, workspaceID, issueID, filename string, data []byte) (string, error) {
	if d.client == nil || d.client.Token() == "" {
		return "", errors.New("daemon has no credential to attach evidence with")
	}
	api := cli.NewAPIClient(d.cfg.ServerBaseURL, workspaceID, d.client.Token())
	api.Platform = "daemon"
	api.Version = d.cfg.CLIVersion
	attachment, err := api.UploadIssueAttachment(ctx, data, filename, issueID)
	if err != nil {
		return "", err
	}
	return attachment.ID, nil
}

// markBrowserHostSeen records host-plane traffic. The task plane uses it to
// answer BROWSER_HOST_OFFLINE honestly: with no recent Desktop contact there is
// nothing to host a view, and D3 requires a hard error, not a queue.
func (d *Daemon) markBrowserHostSeen() {
	d.browserHostSeenAt.Store(time.Now().UnixNano())
}

func (d *Daemon) browserHostOnline() bool {
	seen := d.browserHostSeenAt.Load()
	if seen == 0 {
		return false
	}
	return time.Since(time.Unix(0, seen)) <= 30*time.Second
}

// readLimitedBody reads at most limit bytes, surfacing overflow as an error so
// callers answer 413 rather than a truncated parse failure.
func readLimitedBody(r *http.Request, limit int64) ([]byte, error) {
	data, err := io.ReadAll(io.LimitReader(r.Body, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(data)) > limit {
		return nil, errors.New("request body exceeds the endpoint limit")
	}
	return data, nil
}

// registerBrowserRoutes wires the two planes into the health mux (SDD §3.2).
func (d *Daemon) registerBrowserRoutes(mux *http.ServeMux) {
	mux.HandleFunc("/browser/session", d.browserSessionHandler())
	mux.HandleFunc("/browser/session/takeover", d.browserHostSessionHandler("takeover"))
	mux.HandleFunc("/browser/session/release", d.browserHostSessionHandler("release"))
	mux.HandleFunc("/browser/host/sessions", d.browserHostListSessionsHandler())
	mux.HandleFunc("/browser/host/session", d.browserHostOpenSessionHandler())
	mux.HandleFunc("/browser/host/register", d.browserHostRegisterHandler())
	mux.HandleFunc("/browser/host/heartbeat", d.browserHostHeartbeatHandler())
	mux.HandleFunc("/browser/host/session/closed", d.browserHostClosedHandler())
	mux.HandleFunc("/browser/host/evidence", d.browserHostEvidenceHandler())
}

// browserSessionList is the host discovery poll's payload. The SDD endpoint
// table has no discovery route, but Desktop cannot register or render a view
// without learning the session id, so this host-plane list is the minimal
// addition (reported on ORIO-7).
type browserSessionList struct {
	Code     string               `json:"code"`
	Sessions []browserSessionView `json:"sessions"`
}

// browserHostListSessionsHandler answers Desktop's discovery poll: every
// session this daemon holds, whether or not a view is attached yet.
func (d *Daemon) browserHostListSessionsHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			writeBrowserError(w, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "GET only")
			return
		}
		if r.Header.Get("Origin") != "" {
			browserTransportError(w, http.StatusForbidden, "browser host requests come from Desktop, without an Origin")
			return
		}
		if !d.browserHostAuthorized(w, r) {
			return
		}
		d.markBrowserHostSeen()
		sessions := d.browserSessions.list()
		views := make([]browserSessionView, 0, len(sessions))
		for _, session := range sessions {
			views = append(views, session.view())
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(browserSessionList{Code: "OK", Sessions: views})
	}
}

// browserHostOpenSessionHandler lets Desktop open a session for the task it is
// showing. Desktop holds no task token, and the task plane is reachable only
// from inside a task, so this host-plane route is the second reported addition
// to the SDD's endpoint table.
func (d *Daemon) browserHostOpenSessionHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		body, ok := browserJSONGuard(w, r)
		if !ok {
			return
		}
		if !d.browserHostAuthorized(w, r) {
			return
		}
		d.markBrowserHostSeen()
		var request struct {
			TaskID      string `json:"task_id"`
			IssueID     string `json:"issue_id"`
			WorkspaceID string `json:"workspace_id"`
			AgentID     string `json:"agent_id"`
		}
		if err := json.Unmarshal(body, &request); err != nil {
			browserTransportError(w, http.StatusBadRequest, "invalid request body: "+err.Error())
			return
		}
		taskID := strings.TrimSpace(request.TaskID)
		if taskID == "" {
			browserTransportError(w, http.StatusBadRequest, "task_id is required")
			return
		}
		session, err := d.browserSessions.requestBrowserSession(activeRepoCheckoutTask{
			TaskID:      taskID,
			IssueID:     strings.TrimSpace(request.IssueID),
			WorkspaceID: strings.TrimSpace(request.WorkspaceID),
			AgentID:     strings.TrimSpace(request.AgentID),
		}, d.browserCapabilityForTask(taskID))
		if err != nil {
			writeBrowserError(w, http.StatusInternalServerError, "INTERNAL", err.Error())
			return
		}
		writeBrowserJSON(w, http.StatusOK, browserResponse{Code: "OK", Session: new(session.view())})
	}
}

// browserCapabilityForTask reports whether the task's provider can carry the
// HTTP MCP server. A task this daemon is not running has no recorded decision,
// so it defaults to full: Desktop may still show the page for a manual QA pass,
// and the agent-side tools stay absent for a provider that cannot host them.
func (d *Daemon) browserCapabilityForTask(taskID string) string {
	d.browserCapabilitiesMu.Lock()
	defer d.browserCapabilitiesMu.Unlock()
	if value := d.browserCapabilities[taskID]; value != "" {
		return value
	}
	return browserCapabilityFull
}

// recordBrowserCapability remembers the provider decision for one task.
func (d *Daemon) recordBrowserCapability(taskID, capability string) {
	if taskID == "" {
		return
	}
	d.browserCapabilitiesMu.Lock()
	if d.browserCapabilities == nil {
		d.browserCapabilities = make(map[string]string)
	}
	d.browserCapabilities[taskID] = capability
	d.browserCapabilitiesMu.Unlock()
}

// forgetBrowserCapability drops the entry when the task ends.
func (d *Daemon) forgetBrowserCapability(taskID string) {
	d.browserCapabilitiesMu.Lock()
	delete(d.browserCapabilities, taskID)
	d.browserCapabilitiesMu.Unlock()
}

// browserSessionCapability is the capability a task's session must advertise:
// "none" when the provider cannot carry HTTP MCP, which is what makes the
// sidebar entry disabled instead of silently broken (SDD §8 item 3).
func browserSessionCapability(provider string) string {
	if providerSupportsRemoteMCPBroker(provider) {
		return browserCapabilityFull
	}
	return browserCapabilityNone
}
