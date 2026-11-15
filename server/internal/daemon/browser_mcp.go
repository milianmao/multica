package daemon

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
)

// QA built-in isolated browser: the `multica-browser` MCP tool surface.
//
// This is the only door an agent has into a browser session, which is what
// gives the takeover lock its force (SDD §7): the lock is checked here, in the
// daemon, on every write-class action. A third-party MCP would leave the lock
// advisory.
//
// Actions do not touch Chromium directly. They queue a command for the Desktop
// host, which owns the WebContentsView — a process-level debug port is
// forbidden (I1) and webContents.debugger is main-process-only.

const (
	browserMCPProtocolVersion = "2024-11-05"
	browserMCPMaxRequestBytes = 1 << 18
	browserMCPCallTimeout     = 30 * time.Second

	browserToolGoto       = "browser_goto"
	browserToolSnapshot   = "browser_snapshot"
	browserToolClick      = "browser_click"
	browserToolFill       = "browser_fill"
	browserToolWheel      = "browser_wheel"
	browserToolWait       = "browser_wait"
	browserToolScreenshot = "browser_screenshot"
	browserToolConsole    = "browser_console"
	browserToolURL        = "browser_url"
)

// MCP tool error codes. These are tool errors (isError + text), never JSON-RPC
// protocol errors: a protocol error makes the CLI crash and retry, while an
// agent reading "taken over" can decide to wait or stop (SDD §7).
const (
	browserCodeHostOffline           = "BROWSER_HOST_OFFLINE"
	browserCodeSessionUnavailable    = "BROWSER_SESSION_UNAVAILABLE"
	browserCodeTakenOver             = "BROWSER_TAKEN_OVER"
	browserCodeSessionClosed         = "BROWSER_SESSION_CLOSED"
	browserCodeCapabilityUnsupported = "BROWSER_CAPABILITY_UNSUPPORTED"
	browserCodeEvidenceFailed        = "BROWSER_EVIDENCE_FAILED"
	browserCodeStaleRef              = "BROWSER_STALE_REF"
)

type browserToolSpec struct {
	Name        string
	Description string
	InputSchema json.RawMessage
	// Write marks actions the takeover lock gates. Read-only actions
	// (snapshot, console, url) run while a person holds the lock because the
	// daemon still needs them to report state.
	Write bool
}

func browserToolSpecs() []browserToolSpec {
	return []browserToolSpec{
		{
			Name:        browserToolGoto,
			Description: "Navigate the QA browser view to an http/https URL. Returns the final host and path (query values are never echoed).",
			InputSchema: json.RawMessage(`{"type":"object","properties":{"url":{"type":"string","description":"Absolute http or https URL"}},"required":["url"]}`),
			Write:       true,
		},
		{
			Name:        browserToolSnapshot,
			Description: "Return a flat list of interactive page elements with stable refs. Refs are valid until the next navigation; a stale ref returns BROWSER_STALE_REF.",
			InputSchema: json.RawMessage(`{"type":"object","properties":{}}`),
		},
		{
			Name:        browserToolClick,
			Description: "Click an element by ref from the latest snapshot, or by visible text.",
			InputSchema: json.RawMessage(`{"type":"object","properties":{"ref":{"type":"string"},"text":{"type":"string"}}}`),
			Write:       true,
		},
		{
			Name:        browserToolFill,
			Description: "Type a value into an input identified by ref.",
			InputSchema: json.RawMessage(`{"type":"object","properties":{"ref":{"type":"string"},"value":{"type":"string"}},"required":["ref","value"]}`),
			Write:       true,
		},
		{
			Name:        browserToolWheel,
			Description: "Scroll the page by a pixel delta.",
			InputSchema: json.RawMessage(`{"type":"object","properties":{"deltaY":{"type":"number"},"x":{"type":"number"},"y":{"type":"number"}},"required":["deltaY"]}`),
			Write:       true,
		},
		{
			Name:        browserToolWait,
			Description: "Wait for a page condition. The whole call is bounded by 30 s, shared with any takeover wait, so it never runs to 60 s silently.",
			InputSchema: json.RawMessage(`{"type":"object","properties":{"condition":{"type":"string"},"timeout_ms":{"type":"number"}},"required":["condition"]}`),
			Write:       true,
		},
		{
			Name:        browserToolScreenshot,
			Description: "Capture the current viewport through the Desktop host and attach it to the issue. Fails with BROWSER_EVIDENCE_FAILED when capture fails twice.",
			InputSchema: json.RawMessage(`{"type":"object","properties":{}}`),
			Write:       true,
		},
		{
			Name:        browserToolConsole,
			Description: "Return recent console text from the page, already redacted.",
			InputSchema: json.RawMessage(`{"type":"object","properties":{"limit":{"type":"number"}}}`),
		},
		{
			Name:        browserToolURL,
			Description: "Return the current page's host and path (no query values).",
			InputSchema: json.RawMessage(`{"type":"object","properties":{}}`),
		},
	}
}

// browserMCPServer presents the tools for exactly one task.
type browserMCPServer struct {
	taskID string
	path   string
	write  []browserToolSpec
	byName map[string]browserToolSpec
	daemon *Daemon
	logger *slog.Logger
}

type browserMCPSet struct {
	server   *http.Server
	listener net.Listener
	once     sync.Once
}

func (set *browserMCPSet) Close() {
	if set == nil {
		return
	}
	set.once.Do(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		if set.server != nil {
			_ = set.server.Shutdown(ctx)
		}
		if set.listener != nil {
			_ = set.listener.Close()
		}
	})
}

// startTaskBrowserMCP starts the per-task browser tool server and returns the
// MCP config fragment to merge into the agent's config.
//
// The endpoint is inert until a session exists: a task without a session gets
// BROWSER_SESSION_UNAVAILABLE from the tools rather than an agent that cannot
// find them (SDD §8).
func startTaskBrowserMCP(lifetimeCtx context.Context, d *Daemon, taskID string, logger *slog.Logger) (json.RawMessage, *browserMCPSet, error) {
	if d == nil || d.browserSessions == nil {
		return nil, nil, nil
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, nil, fmt.Errorf("listen for browser MCP server: %w", err)
	}
	pathToken, err := randomBrokerToken()
	if err != nil {
		_ = listener.Close()
		return nil, nil, err
	}
	specs := browserToolSpecs()
	server := &browserMCPServer{
		taskID: taskID,
		path:   "/" + pathToken,
		byName: make(map[string]browserToolSpec, len(specs)),
		daemon: d,
		logger: logger,
	}
	for _, spec := range specs {
		server.byName[spec.Name] = spec
	}
	httpServer := &http.Server{Handler: server, ReadHeaderTimeout: 5 * time.Second}
	set := &browserMCPSet{server: httpServer, listener: listener}

	go func() {
		if serveErr := httpServer.Serve(listener); serveErr != nil && !errors.Is(serveErr, http.ErrServerClosed) && logger != nil {
			logger.Warn("browser MCP server stopped unexpectedly", "task_id", taskID, "error", serveErr)
		}
	}()
	go func() {
		<-lifetimeCtx.Done()
		set.Close()
	}()

	raw, err := json.Marshal(map[string]any{"mcpServers": map[string]any{
		"multica-browser": map[string]any{
			"type": "http",
			"url":  "http://" + listener.Addr().String() + server.path,
		},
	}})
	if err != nil {
		set.Close()
		return nil, nil, err
	}
	return raw, set, nil
}

type browserMCPRequest struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params,omitempty"`
}

func (s *browserMCPServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != s.path || r.Method != http.MethodPost {
		w.WriteHeader(http.StatusNotFound)
		return
	}
	body, err := readLimitedBody(r, browserMCPMaxRequestBytes)
	if err != nil {
		writePluginHookMCPError(w, nil, -32700, "could not read the request")
		return
	}
	var request browserMCPRequest
	if err := json.Unmarshal(body, &request); err != nil {
		writePluginHookMCPError(w, nil, -32700, "request is not valid JSON-RPC")
		return
	}
	switch request.Method {
	case "initialize":
		writePluginHookMCPResult(w, request.ID, map[string]any{
			"protocolVersion": browserMCPProtocolVersion,
			"capabilities":    map[string]any{"tools": map[string]any{}},
			"serverInfo":      map[string]any{"name": "multica-browser", "version": "1"},
		})
	case "notifications/initialized":
		w.WriteHeader(http.StatusAccepted)
	case "tools/list":
		specs := browserToolSpecs()
		sort.Slice(specs, func(i, j int) bool { return specs[i].Name < specs[j].Name })
		descriptors := make([]map[string]any, 0, len(specs))
		for _, spec := range specs {
			descriptors = append(descriptors, map[string]any{
				"name":        spec.Name,
				"description": spec.Description,
				"inputSchema": spec.InputSchema,
			})
		}
		writePluginHookMCPResult(w, request.ID, map[string]any{"tools": descriptors})
	case "tools/call":
		s.handleCall(w, r, request)
	default:
		writePluginHookMCPError(w, request.ID, -32601, "unsupported method "+request.Method)
	}
}

func (s *browserMCPServer) handleCall(w http.ResponseWriter, r *http.Request, request browserMCPRequest) {
	var params struct {
		Name      string          `json:"name"`
		Arguments json.RawMessage `json:"arguments,omitempty"`
	}
	if err := json.Unmarshal(request.Params, &params); err != nil {
		writePluginHookMCPError(w, request.ID, -32602, "invalid tool call parameters")
		return
	}
	spec, ok := s.byName[params.Name]
	if !ok {
		writePluginHookMCPError(w, request.ID, -32602, "unknown tool "+params.Name)
		return
	}
	// One context covers the takeover wait and the action itself, and it caps
	// at 30 s: "person holds the lock for 30 s, then the action also waits"
	// must not become a 60 s call the CLI kills with no explanation.
	ctx, cancel := context.WithTimeout(r.Context(), browserMCPCallTimeout)
	defer cancel()
	text, err := s.daemon.invokeBrowserTool(ctx, s.taskID, spec, params.Arguments)
	if err != nil {
		s.writeToolError(w, request.ID, err)
		return
	}
	writePluginHookMCPResult(w, request.ID, map[string]any{
		"content": []map[string]any{{"type": "text", "text": text}},
	})
}

func (s *browserMCPServer) writeToolError(w http.ResponseWriter, id json.RawMessage, err error) {
	code, message := browserToolErrorCode(err)
	if s.logger != nil {
		s.logger.Info("browser tool call failed", "task_id", s.taskID, "code", code, "error", message)
	}
	writePluginHookMCPResult(w, id, map[string]any{
		"isError": true,
		"content": []map[string]any{{"type": "text", "text": code + ": " + message}},
	})
}

// browserToolFailure carries a stable code out of the tool dispatcher.
type browserToolFailure struct {
	Code    string
	Message string
}

func (e *browserToolFailure) Error() string { return e.Code + ": " + e.Message }

func browserFail(code, message string) error {
	return &browserToolFailure{Code: code, Message: message}
}

func browserToolErrorCode(err error) (string, string) {
	var failure *browserToolFailure
	if errors.As(err, &failure) {
		return failure.Code, failure.Message
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return browserCodeTakenOver, "the call exceeded its 30 s budget while waiting for control of the browser"
	}
	return browserCodeSessionUnavailable, err.Error()
}

// invokeBrowserTool runs one action against the task's session.
func (d *Daemon) invokeBrowserTool(ctx context.Context, taskID string, spec browserToolSpec, rawArgs json.RawMessage) (string, error) {
	session := d.browserSessions.byTaskID(taskID)
	if session == nil {
		return "", browserFail(browserCodeSessionUnavailable, "this task has no browser session; open one from the issue first")
	}
	switch session.State {
	case browserStateClosed, browserStateClosing:
		return "", browserFail(browserCodeSessionClosed, "the browser session has ended")
	case browserStateRequested, browserStateOrphaned, browserStateFailed:
		return "", browserFail(browserCodeSessionUnavailable, "the browser view is not attached; the session cannot run tools in state "+string(session.State))
	}
	if session.Capability != browserCapabilityFull {
		return "", browserFail(browserCodeCapabilityUnsupported, "this task's agent provider cannot host the browser tools")
	}
	if spec.Write {
		var err error
		session, err = d.browserSessions.waitForAgentLock(ctx, session.ID, browserMCPCallTimeout)
		if err != nil {
			switch {
			case errors.Is(err, errBrowserLockHuman):
				return "", browserFail(browserCodeTakenOver, "a person is operating the browser; wait for them to hand it back")
			case errors.Is(err, errBrowserSessionClosed):
				return "", browserFail(browserCodeSessionClosed, "the browser session has ended")
			case errors.Is(err, context.DeadlineExceeded):
				return "", browserFail(browserCodeTakenOver, "a person is operating the browser; the 30 s budget ran out")
			default:
				return "", browserFail(browserCodeSessionUnavailable, err.Error())
			}
		}
	}
	args, err := decodeBrowserToolArgs(rawArgs)
	if err != nil {
		return "", err
	}
	result, err := d.browserSessions.enqueueCommand(ctx, session.ID, spec.Name, args)
	if err != nil {
		switch {
		case errors.Is(err, errBrowserSessionUnavailable):
			return "", browserFail(browserCodeSessionUnavailable, "the browser view is not attached")
		case errors.Is(err, errBrowserSessionClosed):
			return "", browserFail(browserCodeSessionClosed, "the browser session has ended")
		case errors.Is(err, context.DeadlineExceeded):
			return "", browserFail(browserCodeSessionUnavailable, "the browser host did not answer within the call budget")
		default:
			return "", browserFail(browserCodeSessionUnavailable, err.Error())
		}
	}
	if !result.OK {
		code := strings.TrimSpace(result.Error)
		switch {
		case code == browserCodeStaleRef:
			return "", browserFail(browserCodeStaleRef, "that ref came from a snapshot taken before the last navigation; take a new snapshot")
		case code == browserCodeEvidenceFailed:
			return "", browserFail(browserCodeEvidenceFailed, "the screenshot could not be captured")
		case code == browserCodeCapabilityUnsupported:
			return "", browserFail(browserCodeCapabilityUnsupported, "the browser view is gone; the session cannot recover")
		case code != "":
			return "", browserFail(browserCodeSessionUnavailable, code)
		}
		return "", browserFail(browserCodeSessionUnavailable, "the browser host refused the action")
	}
	if strings.TrimSpace(result.Text) != "" {
		return result.Text, nil
	}
	if len(result.Result) > 0 {
		encoded, marshalErr := json.Marshal(result.Result)
		if marshalErr != nil {
			return "", browserFail(browserCodeSessionUnavailable, "could not render the tool result")
		}
		return string(encoded), nil
	}
	return "done", nil
}

func decodeBrowserToolArgs(raw json.RawMessage) (map[string]any, error) {
	trimmed := strings.TrimSpace(string(raw))
	if trimmed == "" || trimmed == "null" {
		return nil, nil
	}
	var args map[string]any
	if err := json.Unmarshal(raw, &args); err != nil {
		return nil, browserFail("BAD_ARGUMENTS", "tool arguments must be a JSON object")
	}
	return args, nil
}
