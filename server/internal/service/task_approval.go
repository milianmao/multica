package service

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
	"github.com/multica-ai/multica/server/pkg/dbid"
	"github.com/multica-ai/multica/server/pkg/redact"
)

var (
	ErrTaskApprovalInvalid  = errors.New("invalid native approval request")
	ErrTaskApprovalConflict = errors.New("native approval is stale or already decided")
	ErrTaskApprovalDisabled = errors.New("native approvals require execution_config.approval_policy on-request")
)

// TaskApprovalExecution separates the server-issued claim from a daemon's individual app-server process.
// The process UUID is a namespace, not authority; runtime ownership and dispatched_at fence every request.
type TaskApprovalExecution struct {
	RuntimeID    pgtype.UUID
	DispatchedAt pgtype.Timestamptz
	ExecutionID  pgtype.UUID
}

// TaskApprovalRegistration captures one immutable, redacted Codex request.
// NativeRequestID contains JSON, so the numeric ID 1 and the string ID "1" stay distinct.
type TaskApprovalRegistration struct {
	NativeRequestID string          `json:"native_request_id"`
	Kind            string          `json:"kind"`
	Method          string          `json:"method"`
	Params          json.RawMessage `json:"params"`
}

// validate keeps native IDs exact while reducing parameters to redacted display data.
func (req *TaskApprovalRegistration) validate() error {
	kind := ""
	switch req.Method {
	case "item/commandExecution/requestApproval", "execCommandApproval":
		kind = "command"
	case "item/fileChange/requestApproval", "applyPatchApproval":
		kind = "file_change"
	case "item/permissions/requestApproval":
		kind = "permissions"
	}
	if kind == "" || req.Kind != kind || len(req.NativeRequestID) > 512 {
		return ErrTaskApprovalInvalid
	}
	if !json.Valid([]byte(req.NativeRequestID)) || !json.Valid(req.Params) {
		return ErrTaskApprovalInvalid
	}
	var nativeID any
	decoder := json.NewDecoder(strings.NewReader(req.NativeRequestID))
	decoder.UseNumber()
	if err := decoder.Decode(&nativeID); err != nil {
		return ErrTaskApprovalInvalid
	}
	switch nativeID.(type) {
	case string, json.Number:
	default:
		return ErrTaskApprovalInvalid
	}
	encodedID, err := json.Marshal(nativeID)
	if err != nil {
		return ErrTaskApprovalInvalid
	}
	if len(encodedID) > 512 {
		return ErrTaskApprovalInvalid
	}
	encodedParams, err := RedactTaskApprovalParams(req.Params)
	if err != nil {
		return err
	}
	req.NativeRequestID = string(encodedID)
	req.Params = encodedParams
	return nil
}

// RedactTaskApprovalParams protects both persistence and API reads, including nested command/diff strings.
// Parameters are display data only; the daemon retains its original native request for protocol replies.
func RedactTaskApprovalParams(raw []byte) (json.RawMessage, error) {
	if !json.Valid(raw) {
		return nil, ErrTaskApprovalInvalid
	}
	var params map[string]any
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := decoder.Decode(&params); err != nil || params == nil {
		return nil, ErrTaskApprovalInvalid
	}
	encoded, err := json.Marshal(redact.InputMap(params))
	if err != nil {
		return nil, ErrTaskApprovalInvalid
	}
	return encoded, nil
}

// taskApprovalEnabled requires the accepted task snapshot to opt into interactive native approval.
func taskApprovalEnabled(task db.AgentTaskQueue) bool {
	config, err := TaskExecutionConfig(task.Context)
	return err == nil && config != nil && config.ApprovalPolicy == "on-request"
}

// beginTaskApproval locks the task before touching approval rows; every caller owns commit/rollback.
func (s *TaskService) beginTaskApproval(ctx context.Context, taskID pgtype.UUID) (pgx.Tx, *db.Queries, db.AgentTaskQueue, error) {
	if s.TxStarter == nil {
		return nil, nil, db.AgentTaskQueue{}, errors.New("native approvals require database transactions")
	}
	tx, err := s.TxStarter.Begin(ctx)
	if err != nil {
		return nil, nil, db.AgentTaskQueue{}, err
	}
	q := s.Queries.WithTx(tx)
	task, err := q.LockTaskApprovalTask(ctx, taskID)
	if err != nil {
		tx.Rollback(ctx)
		return nil, nil, db.AgentTaskQueue{}, err
	}
	return tx, q, task, nil
}

// taskApprovalClaimMatches prevents a post-authorization reclaim or runtime rebind from accepting old callbacks.
func taskApprovalClaimMatches(task, authorized db.AgentTaskQueue, execution TaskApprovalExecution) bool {
	return execution.ExecutionID.Valid && execution.RuntimeID.Valid && execution.DispatchedAt.Valid &&
		execution.DispatchedAt.InfinityModifier == pgtype.Finite && task.RuntimeID == authorized.RuntimeID &&
		task.RuntimeID == execution.RuntimeID && task.Attempt == authorized.Attempt &&
		task.DispatchedAt.Valid && authorized.DispatchedAt.Valid &&
		task.DispatchedAt.Time.Equal(execution.DispatchedAt.Time) && authorized.DispatchedAt.Time.Equal(execution.DispatchedAt.Time)
}

// taskApprovalExecutionMatches permits native delivery and decisions only while that claim is running.
func taskApprovalExecutionMatches(task, authorized db.AgentTaskQueue, execution TaskApprovalExecution) bool {
	return task.Status == "running" && taskApprovalEnabled(task) && taskApprovalClaimMatches(task, authorized, execution)
}

// matchesApproval prevents an approval from an old app-server process from being reused after a cold retry.
func (execution TaskApprovalExecution) matchesApproval(approval db.TaskApproval) bool {
	return approval.RuntimeID == execution.RuntimeID && approval.ExecutionID == execution.ExecutionID &&
		approval.DispatchedAt.Valid && approval.DispatchedAt.Time.Equal(execution.DispatchedAt.Time)
}

// expireStaleTaskApprovals makes stopped or superseded claims terminal without merging cold-retry executions.
func expireStaleTaskApprovals(ctx context.Context, q *db.Queries, task db.AgentTaskQueue) error {
	dispatchedAt := pgtype.Timestamptz{}
	if task.Status == "running" && taskApprovalEnabled(task) {
		dispatchedAt = task.DispatchedAt
	}
	return q.ExpireStaleTaskApprovals(ctx, db.ExpireStaleTaskApprovalsParams{
		TaskID: task.ID, DispatchedAt: dispatchedAt, RuntimeID: task.RuntimeID,
	})
}

// RegisterTaskApproval serializes persistence and duplicate detection against the current task claim and process.
func (s *TaskService) RegisterTaskApproval(ctx context.Context, authorized db.AgentTaskQueue, workspaceID pgtype.UUID, execution TaskApprovalExecution, req TaskApprovalRegistration) (db.TaskApproval, error) {
	if err := req.validate(); err != nil || !workspaceID.Valid {
		return db.TaskApproval{}, ErrTaskApprovalInvalid
	}
	tx, q, task, err := s.beginTaskApproval(ctx, authorized.ID)
	if err != nil {
		return db.TaskApproval{}, err
	}
	defer tx.Rollback(ctx)
	if !taskApprovalEnabled(task) {
		return db.TaskApproval{}, ErrTaskApprovalDisabled
	}
	if !taskApprovalExecutionMatches(task, authorized, execution) {
		return db.TaskApproval{}, ErrTaskApprovalConflict
	}
	if err := expireStaleTaskApprovals(ctx, q, task); err != nil {
		return db.TaskApproval{}, err
	}
	approval, err := q.RegisterTaskApproval(ctx, db.RegisterTaskApprovalParams{
		ID: dbid.NewV7(), TaskID: task.ID, WorkspaceID: workspaceID, IssueID: task.IssueID,
		RuntimeID: execution.RuntimeID, DispatchedAt: execution.DispatchedAt, ExecutionID: execution.ExecutionID,
		NativeRequestID: req.NativeRequestID, Kind: req.Kind, Method: req.Method, Params: req.Params,
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return db.TaskApproval{}, ErrTaskApprovalConflict
	}
	if err != nil {
		return db.TaskApproval{}, err
	}
	return approval, tx.Commit(ctx)
}

// ListTaskApprovals returns a locked snapshot after expiring obsolete claims.
// A nil execution is the user's history view; daemon callers must specify one exact process scope.
func (s *TaskService) ListTaskApprovals(ctx context.Context, authorized db.AgentTaskQueue, workspaceID pgtype.UUID, execution *TaskApprovalExecution) ([]db.TaskApproval, db.AgentTaskQueue, error) {
	tx, q, task, err := s.beginTaskApproval(ctx, authorized.ID)
	if err != nil {
		return nil, task, err
	}
	defer tx.Rollback(ctx)
	if execution != nil && !taskApprovalExecutionMatches(task, authorized, *execution) {
		return nil, task, ErrTaskApprovalConflict
	}
	if err := expireStaleTaskApprovals(ctx, q, task); err != nil {
		return nil, task, err
	}
	var approvals []db.TaskApproval
	if execution == nil {
		approvals, err = q.ListTaskApprovals(ctx, db.ListTaskApprovalsParams{TaskID: task.ID, WorkspaceID: workspaceID})
	} else {
		approvals, err = q.ListLiveTaskApprovals(ctx, db.ListLiveTaskApprovalsParams{
			TaskID: task.ID, WorkspaceID: workspaceID, RuntimeID: execution.RuntimeID, DispatchedAt: execution.DispatchedAt, ExecutionID: execution.ExecutionID,
		})
	}
	if err != nil {
		return nil, task, err
	}
	return approvals, task, tx.Commit(ctx)
}

// DecideTaskApproval allows one human decision; matching retries remain idempotent after acknowledgement.
func (s *TaskService) DecideTaskApproval(ctx context.Context, authorized db.AgentTaskQueue, workspaceID, approvalID pgtype.UUID, execution TaskApprovalExecution, decision string, actorID pgtype.UUID) (db.TaskApproval, error) {
	if !actorID.Valid || (decision != "approve" && decision != "deny") {
		return db.TaskApproval{}, ErrTaskApprovalInvalid
	}
	tx, q, task, err := s.beginTaskApproval(ctx, authorized.ID)
	if err != nil {
		return db.TaskApproval{}, err
	}
	defer tx.Rollback(ctx)
	if !taskApprovalExecutionMatches(task, authorized, execution) {
		return db.TaskApproval{}, ErrTaskApprovalConflict
	}
	approval, err := q.GetTaskApproval(ctx, db.GetTaskApprovalParams{TaskID: task.ID, WorkspaceID: workspaceID, ApprovalID: approvalID})
	if err != nil {
		return db.TaskApproval{}, err
	}
	if !execution.matchesApproval(approval) {
		return db.TaskApproval{}, ErrTaskApprovalConflict
	}
	if task.ChatSessionID.Valid {
		session, err := q.GetChatSession(ctx, task.ChatSessionID)
		if err != nil {
			return db.TaskApproval{}, err
		}
		if session.CreatorID != actorID {
			return db.TaskApproval{}, pgx.ErrNoRows
		}
	}
	if approval.Status == "pending" {
		approval, err = q.DecideTaskApproval(ctx, db.DecideTaskApprovalParams{
			TaskID: task.ID, WorkspaceID: workspaceID, ApprovalID: approvalID, RuntimeID: execution.RuntimeID,
			DispatchedAt: execution.DispatchedAt, ExecutionID: execution.ExecutionID, Decision: decision, DecidedBy: actorID,
		})
		if errors.Is(err, pgx.ErrNoRows) {
			return db.TaskApproval{}, ErrTaskApprovalConflict
		}
		if err != nil {
			return db.TaskApproval{}, err
		}
	} else if (approval.Status != "decided" && approval.Status != "processed") || !approval.Decision.Valid || approval.Decision.String != decision {
		return db.TaskApproval{}, ErrTaskApprovalConflict
	}
	return approval, tx.Commit(ctx)
}

// AckTaskApproval records delivery only after a decision and only for its original app-server process.
func (s *TaskService) AckTaskApproval(ctx context.Context, authorized db.AgentTaskQueue, workspaceID, approvalID pgtype.UUID, execution TaskApprovalExecution, status string) (db.TaskApproval, error) {
	if status != "processed" && status != "expired" {
		return db.TaskApproval{}, ErrTaskApprovalInvalid
	}
	tx, q, task, err := s.beginTaskApproval(ctx, authorized.ID)
	if err != nil {
		return db.TaskApproval{}, err
	}
	defer tx.Rollback(ctx)
	if !taskApprovalExecutionMatches(task, authorized, execution) {
		return db.TaskApproval{}, ErrTaskApprovalConflict
	}
	approval, err := q.GetTaskApproval(ctx, db.GetTaskApprovalParams{TaskID: task.ID, WorkspaceID: workspaceID, ApprovalID: approvalID})
	if err != nil {
		return db.TaskApproval{}, err
	}
	if !execution.matchesApproval(approval) {
		return db.TaskApproval{}, ErrTaskApprovalConflict
	}
	if approval.Status != status {
		if approval.Status == "processed" || approval.Status == "expired" || (status == "processed" && approval.Status != "decided") {
			return db.TaskApproval{}, ErrTaskApprovalConflict
		}
		approval, err = q.AckTaskApproval(ctx, db.AckTaskApprovalParams{
			TaskID: task.ID, WorkspaceID: workspaceID, ApprovalID: approvalID, RuntimeID: execution.RuntimeID,
			DispatchedAt: execution.DispatchedAt, ExecutionID: execution.ExecutionID, Status: status,
		})
		if errors.Is(err, pgx.ErrNoRows) {
			return db.TaskApproval{}, ErrTaskApprovalConflict
		}
		if err != nil {
			return db.TaskApproval{}, err
		}
	}
	return approval, tx.Commit(ctx)
}

// ExpireTaskApprovalExecution handles process exit without touching a cold retry's separate execution.
// Expiration only removes authority, so an already-terminal current claim can still acknowledge cleanup.
func (s *TaskService) ExpireTaskApprovalExecution(ctx context.Context, authorized db.AgentTaskQueue, execution TaskApprovalExecution) error {
	tx, q, task, err := s.beginTaskApproval(ctx, authorized.ID)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if !taskApprovalClaimMatches(task, authorized, execution) {
		return ErrTaskApprovalConflict
	}
	if err := q.ExpireTaskApprovalExecution(ctx, db.ExpireTaskApprovalExecutionParams{
		TaskID: task.ID, RuntimeID: execution.RuntimeID, DispatchedAt: execution.DispatchedAt, ExecutionID: execution.ExecutionID,
	}); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
