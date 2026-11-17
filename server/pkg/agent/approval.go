package agent

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"sync"
	"time"
)

// ApprovalRequest preserves the native JSON-RPC ID as canonical JSON text so
// string IDs and large numeric IDs survive storage without losing precision.
type ApprovalRequest struct {
	ID     string          `json:"native_request_id"`
	Kind   string          `json:"kind"`
	Method string          `json:"method"`
	Params json.RawMessage `json:"params"`
}

// ApprovalDecision grants or refuses only the action in one native request.
type ApprovalDecision string

const (
	ApprovalApprove ApprovalDecision = "approve"
	ApprovalDeny    ApprovalDecision = "deny"
)

// ApprovalAcknowledgement records native delivery or expiry independently of
// the turn outcome. A failed receipt must never cause the prompt to run again.
type ApprovalAcknowledgement string

const (
	ApprovalProcessed ApprovalAcknowledgement = "processed"
	ApprovalExpired   ApprovalAcknowledgement = "expired"
)

// ApprovalHandler owns registration and human decision polling for one execution.
// Implementations must release their wait when the request context is cancelled.
type ApprovalHandler interface {
	// RequestApproval registers the request before waiting for its decision.
	RequestApproval(context.Context, ApprovalRequest) (ApprovalDecision, error)
	// AcknowledgeApproval settles delivery or expiry using a separate bounded context.
	AcknowledgeApproval(context.Context, ApprovalRequest, ApprovalAcknowledgement) error
}

// codexApprovalState owns native waits for one process execution. Registration
// and pending callbacks are serialized; human polling never runs on stdout.
type codexApprovalState struct {
	parent    context.Context
	ctx       context.Context
	cancel    context.CancelFunc
	workspace string
	handler   ApprovalHandler
	onPending func(int)
	changed   chan struct{}
	mu        sync.Mutex
	waiting   map[string]context.CancelFunc
	workers   sync.WaitGroup
}

// newCodexApprovalState keeps inherited/full-access runs on their old path.
func newCodexApprovalState(ctx context.Context, opts ExecOptions) *codexApprovalState {
	if opts.ApprovalPolicy != "on-request" {
		return nil
	}
	waitCtx, cancel := context.WithCancel(ctx)
	return &codexApprovalState{parent: ctx, ctx: waitCtx, cancel: cancel, workspace: opts.Cwd,
		handler: opts.ApprovalHandler, onPending: opts.OnApprovalPending,
		changed: make(chan struct{}, 1), waiting: make(map[string]context.CancelFunc)}
}

// begin increments pending before the parent registers the native request.
func (s *codexApprovalState) begin(id string) (context.Context, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.ctx.Err(); err != nil {
		return nil, err
	}
	if _, exists := s.waiting[id]; exists {
		return nil, fmt.Errorf("duplicate native approval ID %s", id)
	}
	ctx, cancel := context.WithCancel(s.ctx)
	s.waiting[id] = cancel
	s.workers.Add(1)
	s.pendingChanged(1)
	return ctx, nil
}

// finish removes each wait once, whether it replied or was cancelled.
func (s *codexApprovalState) finish(id string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if cancel, exists := s.waiting[id]; exists {
		delete(s.waiting, id)
		cancel()
		s.pendingChanged(-1)
	}
}

// pendingChanged is called with mu held so daemon deltas cannot reorder.
func (s *codexApprovalState) pendingChanged(delta int) {
	if s.onPending != nil {
		s.onPending(delta)
	}
	select {
	case s.changed <- struct{}{}:
	default:
	}
}

// count snapshots all outstanding native waits for the inactivity watchdogs.
func (s *codexApprovalState) count() int {
	if s == nil {
		return 0
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.waiting)
}

// resolve cancels a request cleared by the native server without an answer.
func (s *codexApprovalState) resolve(id string) {
	s.mu.Lock()
	cancel := s.waiting[id]
	s.mu.Unlock()
	if cancel != nil {
		cancel()
	}
}

// stopAndWait fences cancellation against begin before joining native workers.
// Receipts use bounded contexts independent of native or execution cancellation.
func (s *codexApprovalState) stopAndWait() {
	if s == nil {
		return
	}
	s.mu.Lock()
	s.cancel()
	s.mu.Unlock()
	s.workers.Wait()
}

// canonicalApprovalID normalizes strings while retaining JSON numbers exactly.
func canonicalApprovalID(raw json.RawMessage) (string, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var id any
	if err := decoder.Decode(&id); err != nil {
		return "", err
	}
	switch id.(type) {
	case string, json.Number:
		encoded, err := json.Marshal(id)
		return string(encoded), err
	default:
		return "", errors.New("native approval ID must be a JSON string or number")
	}
}

// codexApprovalAcknowledgementTimeout bounds receipt cleanup after cancellation.
const codexApprovalAcknowledgementTimeout = 2 * time.Second

// awaitApproval settles one receipt after releasing pending. Only a valid human
// decision successfully written to native becomes processed; every other exit
// expires. Receipt failure never changes completed work into a retryable error.
func (c *codexClient) awaitApproval(ctx context.Context, request ApprovalRequest, nativeParams json.RawMessage) {
	s := c.approvals
	defer s.workers.Done()
	acknowledgement := ApprovalExpired
	defer func() {
		s.finish(request.ID)
		ackCtx, cancel := context.WithTimeout(context.WithoutCancel(s.parent), codexApprovalAcknowledgementTimeout)
		defer cancel()
		if err := s.handler.AcknowledgeApproval(ackCtx, request, acknowledgement); err != nil {
			c.cfg.Logger.Warn("codex approval acknowledgement failed", "native_request_id", request.ID, "status", acknowledgement, "error", err)
		}
	}()
	decision, err := s.handler.RequestApproval(ctx, request)
	if ctx.Err() != nil {
		return
	}
	validDecision := err == nil && (decision == ApprovalApprove || decision == ApprovalDeny)
	if err != nil {
		c.setTurnError(fmt.Sprintf("codex approval handler failed: %v", err))
		decision = ApprovalDeny
	} else if !validDecision {
		c.setTurnError(fmt.Sprintf("codex approval handler returned unsupported decision %q", decision))
		decision = ApprovalDeny
	}
	var result map[string]any
	if request.Kind == "permissions" {
		result = map[string]any{"permissions": map[string]any{}, "scope": "turn"}
		if decision == ApprovalApprove {
			result = codexPermissionsApprovalResponse(nativeParams, c.cfg.Logger)
		}
	} else {
		nativeDecision := "decline"
		if decision == ApprovalApprove {
			nativeDecision = "accept"
		}
		result = map[string]any{"decision": nativeDecision}
	}
	if ctx.Err() != nil {
		return
	}
	if err := c.respond(json.RawMessage(request.ID), result); err != nil {
		c.setTurnError(fmt.Sprintf("codex native approval response failed: %v", err))
		c.markProcessExited(fmt.Errorf("write codex native approval response: %w", err))
		return
	}
	if validDecision {
		acknowledgement = ApprovalProcessed
	}
}

// codexApprovalItemKey isolates display snapshots by their native conversation,
// turn and item IDs. Only the stdout reader reads or mutates the item cache.
type codexApprovalItemKey struct {
	threadID, turnID, itemID string
}

// cacheApprovalItem retains raw native details until item completion. It does
// not reconstruct patches from tool summaries or mix another turn's items in.
func (c *codexClient) cacheApprovalItem(method string, raw json.RawMessage) {
	var notification struct {
		ThreadID string          `json:"threadId"`
		TurnID   string          `json:"turnId"`
		Item     json.RawMessage `json:"item"`
	}
	if json.Unmarshal(raw, &notification) != nil {
		return
	}
	var item struct {
		ID   string `json:"id"`
		Type string `json:"type"`
	}
	if json.Unmarshal(notification.Item, &item) != nil || item.ID == "" {
		return
	}
	key := codexApprovalItemKey{notification.ThreadID, notification.TurnID, item.ID}
	if method == "item/completed" {
		delete(c.approvalItems, key)
		return
	}
	if item.Type != "commandExecution" && item.Type != "fileChange" {
		return
	}
	if c.approvalItems == nil {
		c.approvalItems = make(map[codexApprovalItemKey]json.RawMessage)
	}
	c.approvalItems[key] = notification.Item
}

// approvalDisplayParams enriches only parent/UI data. Native permissions stay
// separate so redaction or display scope cannot change the actual grant.
func (c *codexClient) approvalDisplayParams(kind string, raw json.RawMessage) (json.RawMessage, error) {
	var params map[string]json.RawMessage
	if err := json.Unmarshal(raw, &params); err != nil {
		return nil, err
	}
	if params == nil {
		return nil, errors.New("approval params must be a JSON object")
	}
	var ids struct {
		ThreadID string `json:"threadId"`
		TurnID   string `json:"turnId"`
		ItemID   string `json:"itemId"`
	}
	if err := json.Unmarshal(raw, &ids); err != nil {
		return nil, err
	}
	var item map[string]json.RawMessage
	_ = json.Unmarshal(c.approvalItems[codexApprovalItemKey{ids.ThreadID, ids.TurnID, ids.ItemID}], &item)
	missing := func(value json.RawMessage) bool {
		return len(value) == 0 || bytes.Equal(bytes.TrimSpace(value), []byte("null"))
	}
	fields := []string{"cwd"}
	switch kind {
	case "command":
		fields = append(fields, "command", "commandActions")
	case "file_change":
		fields = append(fields, "changes")
	case "permissions":
		params["scope"] = json.RawMessage(`"turn"`)
	}
	for _, field := range fields {
		if missing(params[field]) && !missing(item[field]) {
			params[field] = item[field]
		}
	}
	if missing(params["cwd"]) && c.approvals.workspace != "" {
		params["cwd"], _ = json.Marshal(c.approvals.workspace)
	}
	return json.Marshal(params)
}

// validateCodexApprovalOptions rejects unsupported intent before executable
// lookup, so on-request can never silently become unattended execution.
func validateCodexApprovalOptions(opts ExecOptions) error {
	switch opts.ApprovalPolicy {
	case "", "full-access":
		return nil
	case "on-request":
		if opts.ApprovalHandler == nil {
			return errors.New("codex on-request requires an approval handler")
		}
		if !filepath.IsAbs(opts.Cwd) {
			return errors.New("codex on-request requires an absolute workspace cwd")
		}
		return nil
	default:
		return fmt.Errorf("unsupported codex approval policy %q", opts.ApprovalPolicy)
	}
}

var codexManagedApprovalConfigKeyRe = regexp.MustCompile(
	`^\s*(?:profiles\s*\.\s*` + codexProfileNameKeyPattern + `\s*\.\s*)?(?:approval_policy|sandbox_mode|sandbox_workspace_write|permissions)\s*(?:\.|=|$)`)

// Argument normalization may unwrap outer quotes around a quoted TOML key,
// leaving one boundary quote; reviewer ownership must survive that normalization.
var codexManagedApprovalsReviewerConfigKeyRe = regexp.MustCompile(
	`^\s*(?:profiles\s*\.\s*` + codexProfileNameKeyPattern + `\s*\.\s*)?(?:"|')?approvals_reviewer(?:"|')?\s*(?:\.|=|$)`)

var codexApprovalBlockedArgs = map[string]blockedArgMode{
	"--ask-for-approval": blockedWithValue, "-a": blockedWithValue,
	"--sandbox": blockedWithValue, "-s": blockedWithValue,
	"--full-auto": blockedStandalone, "--yolo": blockedStandalone,
	"--dangerously-bypass-approvals-and-sandbox": blockedStandalone,
}

// stripCodexApprovalOverrides gives the execution policy one owner across
// profile prefixes, daemon extra args and agent custom args. Human reviewer
// ownership applies only to on-request; unattended reviewer settings survive.
func stripCodexApprovalOverrides(args []string, policy string, logger *slog.Logger) []string {
	args = filterCustomArgs(args, codexApprovalBlockedArgs, logger)
	args = filterCodexConfigOverrides(args, codexManagedApprovalConfigKeyRe, "approval/sandbox", logger)
	if policy == "on-request" {
		args = filterCodexConfigOverrides(args, codexManagedApprovalsReviewerConfigKeyRe, "approvals_reviewer", logger)
	}
	return args
}

// codexNativeApprovalSettings maps the UI policy to Codex's native vocabulary.
func codexNativeApprovalSettings(policy string) (approval, sandbox string) {
	if policy == "on-request" {
		return "on-request", "workspace-write"
	}
	return "never", "danger-full-access"
}

// applyCodexApprovalLaunchArgs constrains app-server defaults without changing
// global config. Thread and turn RPCs independently repeat these settings.
func applyCodexApprovalLaunchArgs(args []string, policy string, logger *slog.Logger) []string {
	if policy == "" {
		return args
	}
	args = stripCodexApprovalOverrides(args, policy, logger)
	approval, sandbox := codexNativeApprovalSettings(policy)
	args = append(args, "-c", "approval_policy="+strconv.Quote(approval), "-c", "sandbox_mode="+strconv.Quote(sandbox))
	if policy == "on-request" {
		args = append(args, "-c", `approvals_reviewer="user"`, "-c", "sandbox_workspace_write.network_access=true")
	}
	return args
}

// applyCodexThreadApprovalParams applies the same explicit policy on start
// and resume; persisted thread settings are not trusted to inherit it.
func applyCodexThreadApprovalParams(params map[string]any, policy string) {
	if policy == "" {
		return
	}
	approval, sandbox := codexNativeApprovalSettings(policy)
	params["approvalPolicy"], params["sandbox"] = approval, sandbox
	if policy == "on-request" {
		params["approvalsReviewer"] = "user"
		config, _ := params["config"].(map[string]any)
		if config == nil {
			config = make(map[string]any)
		}
		config["sandbox_workspace_write.network_access"] = true
		params["config"] = config
	}
}

// applyCodexTurnApprovalParams constrains the actual command execution turn,
// including Multica networking, rather than only the thread defaults.
func applyCodexTurnApprovalParams(params map[string]any, opts ExecOptions) {
	if opts.ApprovalPolicy == "" {
		return
	}
	approval, _ := codexNativeApprovalSettings(opts.ApprovalPolicy)
	params["approvalPolicy"] = approval
	if opts.ApprovalPolicy == "on-request" {
		params["approvalsReviewer"] = "user"
		params["sandboxPolicy"] = map[string]any{"type": "workspaceWrite", "writableRoots": []string{opts.Cwd}, "networkAccess": true}
	} else {
		params["sandboxPolicy"] = map[string]any{"type": "dangerFullAccess"}
	}
}

// verifyCodexOnRequestThread fails closed when a CLI cannot attest to the
// effective network-enabled workspace policy and human reviewer. Both v2
// thread/start and thread/resume responses require approvalsReviewer.
func verifyCodexOnRequestThread(result json.RawMessage, policy string) error {
	if policy != "on-request" {
		return nil
	}
	var settings struct {
		ApprovalPolicy    string `json:"approvalPolicy"`
		ApprovalsReviewer string `json:"approvalsReviewer"`
		Sandbox           struct {
			Type          string `json:"type"`
			NetworkAccess bool   `json:"networkAccess"`
		} `json:"sandbox"`
	}
	if err := json.Unmarshal(result, &settings); err != nil {
		return fmt.Errorf("codex on-request cannot verify native execution settings: %w", err)
	}
	if settings.ApprovalPolicy != "on-request" || settings.Sandbox.Type != "workspaceWrite" || !settings.Sandbox.NetworkAccess {
		return errors.New("codex on-request requires confirmed approvalPolicy=on-request and network-enabled workspaceWrite; this app-server did not apply the strict settings (unknown or unsupported CLI capability)")
	}
	if settings.ApprovalsReviewer != "user" {
		return fmt.Errorf("codex on-request requires confirmed approvalsReviewer=user; app-server returned %q (unknown or unsupported human reviewer capability)", settings.ApprovalsReviewer)
	}
	return nil
}

// verifyCodexWindowsSandbox never provisions or falls back during a task.
// Native readiness takes optional unit params (null or omitted) and must be
// affirmative before creating or resuming a turn.
func (c *codexClient) verifyCodexWindowsSandbox(ctx context.Context, policy string) error {
	if policy != "on-request" || runtime.GOOS != "windows" {
		return nil
	}
	result, err := c.request(ctx, "windowsSandbox/readiness", nil)
	if err != nil {
		return fmt.Errorf("codex on-request cannot verify native Windows sandbox readiness: %w", err)
	}
	var readiness struct {
		Status string `json:"status"`
	}
	if err := json.Unmarshal(result, &readiness); err != nil {
		return fmt.Errorf("codex on-request cannot read native Windows sandbox readiness: %w", err)
	}
	if readiness.Status != "ready" {
		return fmt.Errorf("codex on-request native Windows sandbox is not ready (%q); configure it outside this task; full-access fallback is forbidden", readiness.Status)
	}
	return nil
}
