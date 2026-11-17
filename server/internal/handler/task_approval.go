package handler

import (
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"
	"github.com/multica-ai/multica/server/internal/middleware"
	"github.com/multica-ai/multica/server/internal/service"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

const maxTaskApprovalBodyBytes = 2 << 20

// taskApprovalExecutionRequest carries both the persisted claim generation and one native process UUID.
type taskApprovalExecutionRequest struct {
	RuntimeID    string `json:"runtime_id"`
	DispatchedAt string `json:"dispatched_at"`
	ExecutionID  string `json:"execution_id"`
}

// parse validates untrusted UUIDs and preserves the existing PostgreSQL claim timestamp precision.
func (req taskApprovalExecutionRequest) parse(w http.ResponseWriter) (service.TaskApprovalExecution, bool) {
	runtimeID, ok := parseUUIDOrBadRequest(w, req.RuntimeID, "runtime_id")
	if !ok {
		return service.TaskApprovalExecution{}, false
	}
	executionID, ok := parseUUIDOrBadRequest(w, req.ExecutionID, "execution_id")
	if !ok {
		return service.TaskApprovalExecution{}, false
	}
	dispatchedAt, err := time.Parse(time.RFC3339Nano, req.DispatchedAt)
	if err != nil || dispatchedAt.Nanosecond()%1000 != 0 {
		writeError(w, http.StatusBadRequest, "dispatched_at must be a PostgreSQL claim timestamp")
		return service.TaskApprovalExecution{}, false
	}
	return service.TaskApprovalExecution{
		RuntimeID: runtimeID, ExecutionID: executionID, DispatchedAt: pgtype.Timestamptz{Time: dispatchedAt, Valid: true},
	}, true
}

// TaskApprovalResponse exposes native request data only through the task's existing privacy gate.
// CanDecide is an affordance, not authority: decisions recheck identity, generation and state.
type TaskApprovalResponse struct {
	ID              string          `json:"id"`
	TaskID          string          `json:"task_id"`
	RuntimeID       string          `json:"runtime_id"`
	DispatchedAt    string          `json:"dispatched_at"`
	ExecutionID     string          `json:"execution_id"`
	NativeRequestID string          `json:"native_request_id"`
	Kind            string          `json:"kind"`
	Method          string          `json:"method"`
	Params          json.RawMessage `json:"params"`
	Status          string          `json:"status"`
	Decision        *string         `json:"decision"`
	DecidedBy       *string         `json:"decided_by"`
	CanDecide       bool            `json:"can_decide"`
	CreatedAt       string          `json:"created_at"`
	DecidedAt       *string         `json:"decided_at"`
	ProcessedAt     *string         `json:"processed_at"`
	ExpiredAt       *string         `json:"expired_at"`
}

// taskApprovalToResponse keeps JSON params on the wire instead of encoding sqlc bytes as base64.
func taskApprovalToResponse(row db.TaskApproval, canDecide bool) TaskApprovalResponse {
	params, err := service.RedactTaskApprovalParams(row.Params)
	if err != nil {
		params = json.RawMessage(`{}`)
	}
	return TaskApprovalResponse{
		ID: uuidToString(row.ID), TaskID: uuidToString(row.TaskID), RuntimeID: uuidToString(row.RuntimeID), DispatchedAt: row.DispatchedAt.Time.UTC().Format(time.RFC3339Nano), ExecutionID: uuidToString(row.ExecutionID),
		NativeRequestID: row.NativeRequestID, Kind: row.Kind, Method: row.Method,
		Params: params, Status: row.Status, Decision: textToPtr(row.Decision),
		DecidedBy: uuidToPtr(row.DecidedBy), CanDecide: canDecide, CreatedAt: timestampToString(row.CreatedAt),
		DecidedAt: timestampToPtr(row.DecidedAt), ProcessedAt: timestampToPtr(row.ProcessedAt), ExpiredAt: timestampToPtr(row.ExpiredAt),
	}
}

// writeTaskApprovalError preserves authorization misses as 404 and actionable-state conflicts as 409.
func writeTaskApprovalError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, service.ErrTaskApprovalInvalid):
		writeError(w, http.StatusBadRequest, err.Error())
	case errors.Is(err, service.ErrTaskApprovalConflict), errors.Is(err, service.ErrTaskApprovalDisabled):
		writeError(w, http.StatusConflict, err.Error())
	case errors.Is(err, pgx.ErrNoRows):
		writeError(w, http.StatusNotFound, "task approval not found")
	default:
		slog.Warn("native task approval failed", "error", err)
		writeError(w, http.StatusInternalServerError, "failed to process task approval")
	}
}

// DaemonRegisterTaskApproval registers an immutable request from the task's authorized runtime.
func (h *Handler) DaemonRegisterTaskApproval(w http.ResponseWriter, r *http.Request) {
	task, scope, ok := h.requireDaemonTaskAccessWithScope(w, r, chi.URLParam(r, "taskId"))
	if !ok {
		return
	}
	var req struct {
		taskApprovalExecutionRequest
		service.TaskApprovalRegistration
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxTaskApprovalBodyBytes)).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	execution, ok := req.taskApprovalExecutionRequest.parse(w)
	if !ok {
		return
	}
	approval, err := h.TaskService.RegisterTaskApproval(r.Context(), task, parseUUID(scope.WorkspaceID), execution, req.TaskApprovalRegistration)
	if err != nil {
		writeTaskApprovalError(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, taskApprovalToResponse(approval, false))
}

// DaemonListTaskApprovals returns pending/decided requests from exactly one process, never a cold retry's predecessor.
func (h *Handler) DaemonListTaskApprovals(w http.ResponseWriter, r *http.Request) {
	task, scope, ok := h.requireDaemonTaskAccessWithScope(w, r, chi.URLParam(r, "taskId"))
	if !ok {
		return
	}
	query := r.URL.Query()
	execution, ok := (taskApprovalExecutionRequest{
		RuntimeID: query.Get("runtime_id"), DispatchedAt: query.Get("dispatched_at"), ExecutionID: query.Get("execution_id"),
	}).parse(w)
	if !ok {
		return
	}
	approvals, _, err := h.TaskService.ListTaskApprovals(r.Context(), task, parseUUID(scope.WorkspaceID), &execution)
	if err != nil {
		writeTaskApprovalError(w, err)
		return
	}
	response := make([]TaskApprovalResponse, 0, len(approvals))
	for _, approval := range approvals {
		response = append(response, taskApprovalToResponse(approval, false))
	}
	writeJSON(w, http.StatusOK, response)
}

// DaemonAckTaskApproval confirms delivery to the original native process or expires its canceled request.
func (h *Handler) DaemonAckTaskApproval(w http.ResponseWriter, r *http.Request) {
	task, scope, ok := h.requireDaemonTaskAccessWithScope(w, r, chi.URLParam(r, "taskId"))
	if !ok {
		return
	}
	approvalID, ok := parseUUIDOrBadRequest(w, chi.URLParam(r, "approvalId"), "approval_id")
	if !ok {
		return
	}
	var req struct {
		taskApprovalExecutionRequest
		Status string `json:"status"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxTaskApprovalBodyBytes)).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	execution, ok := req.taskApprovalExecutionRequest.parse(w)
	if !ok {
		return
	}
	if req.Status == "" {
		req.Status = "processed"
	}
	approval, err := h.TaskService.AckTaskApproval(r.Context(), task, parseUUID(scope.WorkspaceID), approvalID, execution, req.Status)
	if err != nil {
		writeTaskApprovalError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, taskApprovalToResponse(approval, false))
}

// DaemonExpireTaskApprovals acknowledges native process exit without clearing another execution's requests.
func (h *Handler) DaemonExpireTaskApprovals(w http.ResponseWriter, r *http.Request) {
	task, ok := h.requireDaemonTaskAccess(w, r, chi.URLParam(r, "taskId"))
	if !ok {
		return
	}
	var req taskApprovalExecutionRequest
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxTaskApprovalBodyBytes)).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	execution, ok := req.parse(w)
	if !ok {
		return
	}
	if err := h.TaskService.ExpireTaskApprovalExecution(r.Context(), task, execution); err != nil {
		writeTaskApprovalError(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// loadTaskApprovalForUser reuses task/project/session visibility and rejects machine readers of private chats.
func (h *Handler) loadTaskApprovalForUser(w http.ResponseWriter, r *http.Request) (db.AgentTaskQueue, bool) {
	task, ok := h.loadTaskForUser(w, r)
	if !ok {
		return db.AgentTaskQueue{}, false
	}
	if task.ChatSessionID.Valid && !taskApprovalHuman(r) {
		writeError(w, http.StatusNotFound, "task not found")
		return db.AgentTaskQueue{}, false
	}
	if _, ok := h.workspaceMember(w, r, middleware.WorkspaceIDFromContext(r.Context())); !ok {
		return db.AgentTaskQueue{}, false
	}
	return task, true
}

// taskApprovalHuman distinguishes owner-bound machine credentials from an authenticated human.
func taskApprovalHuman(r *http.Request) bool {
	return requestUserID(r) != "" && !isMachineCredentialActor(r) &&
		middleware.DaemonIDFromContext(r.Context()) == "" && middleware.DaemonWorkspaceIDFromContext(r.Context()) == ""
}

// requireHumanChatApprovalConfig restricts explicit policy edits without blocking model overrides.
// Creation has no existing policy to clear; only an update treats top-level null as a reset.
// Invalid shapes remain the existing configuration validator's responsibility.
func requireHumanChatApprovalConfig(w http.ResponseWriter, r *http.Request, config json.RawMessage, resetClearsPolicy bool) bool {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(config, &fields); err != nil {
		return true
	}
	_, hasPolicy := fields["approval_policy"]
	if !hasPolicy && !(resetClearsPolicy && fields == nil) {
		return true
	}
	if !taskApprovalHuman(r) {
		writeError(w, http.StatusForbidden, "only human actors may change chat approval policy")
		return false
	}
	return true
}

// ListTaskApprovalsByUser exposes history through task visibility; only live human requests are decidable.
func (h *Handler) ListTaskApprovalsByUser(w http.ResponseWriter, r *http.Request) {
	task, ok := h.loadTaskApprovalForUser(w, r)
	if !ok {
		return
	}
	approvals, current, err := h.TaskService.ListTaskApprovals(r.Context(), task, parseUUID(middleware.WorkspaceIDFromContext(r.Context())), nil)
	if err != nil {
		writeTaskApprovalError(w, err)
		return
	}
	response := make([]TaskApprovalResponse, 0, len(approvals))
	for _, approval := range approvals {
		canDecide := taskApprovalHuman(r) && current.Status == "running" && approval.Status == "pending" &&
			approval.RuntimeID == current.RuntimeID && current.DispatchedAt.Valid && approval.DispatchedAt.Time.Equal(current.DispatchedAt.Time)
		response = append(response, taskApprovalToResponse(approval, canDecide))
	}
	writeJSON(w, http.StatusOK, response)
}

// DecideTaskApprovalByUser rejects machine credentials before they can act with their owner's identity.
func (h *Handler) DecideTaskApprovalByUser(w http.ResponseWriter, r *http.Request) {
	if !taskApprovalHuman(r) {
		writeError(w, http.StatusForbidden, "only human actors may decide native approvals")
		return
	}
	task, ok := h.loadTaskApprovalForUser(w, r)
	if !ok {
		return
	}
	approvalID, ok := parseUUIDOrBadRequest(w, chi.URLParam(r, "approvalId"), "approval_id")
	if !ok {
		return
	}
	var req struct {
		taskApprovalExecutionRequest
		Decision string `json:"decision"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxTaskApprovalBodyBytes)).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	execution, ok := req.taskApprovalExecutionRequest.parse(w)
	if !ok {
		return
	}
	actorID, ok := parseUUIDOrBadRequest(w, requestUserID(r), "user_id")
	if !ok {
		return
	}
	approval, err := h.TaskService.DecideTaskApproval(r.Context(), task, parseUUID(middleware.WorkspaceIDFromContext(r.Context())), approvalID, execution, req.Decision, actorID)
	if err != nil {
		writeTaskApprovalError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, taskApprovalToResponse(approval, false))
}
