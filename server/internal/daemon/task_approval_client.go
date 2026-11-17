package daemon

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/multica-ai/multica/server/pkg/agent"
	"github.com/multica-ai/multica/server/pkg/redact"
)

// nativeApprovalRecord is the bounded decision payload; the backend keeps native request data locally.
type nativeApprovalRecord struct {
	ID       string                 `json:"id"`
	Status   string                 `json:"status"`
	Decision agent.ApprovalDecision `json:"decision"`
}

// taskApprovalHandler binds one transport process to a server-issued task claim.
// A fresh handler is created for every execution, including cold-session retries.
type taskApprovalHandler struct {
	client       *Client
	task         Task
	executionID  string
	pollInterval time.Duration
	mu           sync.Mutex
	records      map[string]string
}

// newTaskApprovalHandler keeps task routing metadata out of the agent-visible environment.
func newTaskApprovalHandler(client *Client, task Task) *taskApprovalHandler {
	return &taskApprovalHandler{client: client, task: task, pollInterval: time.Second}
}

// forExecution allocates a separate namespace because native JSON-RPC IDs reset on process restart.
func (h *taskApprovalHandler) forExecution() *taskApprovalHandler {
	return &taskApprovalHandler{client: h.client, task: h.task, executionID: uuid.NewString(), pollInterval: h.pollInterval, records: make(map[string]string)}
}

// scope repeats the precise PostgreSQL claim timestamp for every state-changing callback.
func (h *taskApprovalHandler) scope() map[string]any {
	return map[string]any{"runtime_id": h.task.RuntimeID, "dispatched_at": h.task.DispatchedAt, "execution_id": h.executionID}
}

// basePath is task-scoped; the server still verifies runtime ownership and claim generation.
func (h *taskApprovalHandler) basePath() string {
	return "/api/daemon/tasks/" + url.PathEscape(h.task.ID) + "/approvals"
}

// RequestApproval registers before polling. It never runs on the protocol reader, and no error grants access.
func (h *taskApprovalHandler) RequestApproval(ctx context.Context, request agent.ApprovalRequest) (agent.ApprovalDecision, error) {
	var params map[string]any
	decoder := json.NewDecoder(bytes.NewReader(request.Params))
	decoder.UseNumber()
	if err := decoder.Decode(&params); err != nil || params == nil {
		return "", errors.New("native approval parameters must be an object")
	}
	body := h.scope()
	body["native_request_id"], body["kind"], body["method"] = request.ID, request.Kind, request.Method
	body["params"] = redact.InputMap(params)
	var registered nativeApprovalRecord
	if err := h.client.postJSON(ctx, h.basePath(), body, &registered); err != nil {
		return "", fmt.Errorf("register native approval: %w", err)
	}
	if registered.ID == "" {
		return "", errors.New("native approval registration returned no ID")
	}
	h.mu.Lock()
	h.records[request.ID] = registered.ID
	h.mu.Unlock()
	if decision, done, err := nativeApprovalDecision(registered); done || err != nil {
		return decision, err
	}
	query := url.Values{"runtime_id": {h.task.RuntimeID}, "dispatched_at": {h.task.DispatchedAt}, "execution_id": {h.executionID}}
	ticker := time.NewTicker(h.pollInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case <-ticker.C:
			var records []nativeApprovalRecord
			if err := h.client.getJSON(ctx, h.basePath()+"?"+query.Encode(), &records); err != nil {
				return "", fmt.Errorf("poll native approval: %w", err)
			}
			for _, record := range records {
				if record.ID != registered.ID {
					continue
				}
				if decision, done, err := nativeApprovalDecision(record); done || err != nil {
					return decision, err
				}
			}
		}
	}
}

// nativeApprovalDecision accepts only one still-undelivered human decision, never an expired or repeated action.
func nativeApprovalDecision(record nativeApprovalRecord) (agent.ApprovalDecision, bool, error) {
	switch record.Status {
	case "pending":
		return "", false, nil
	case "decided":
		if record.Decision == agent.ApprovalApprove || record.Decision == agent.ApprovalDeny {
			return record.Decision, true, nil
		}
		return "", false, errors.New("native approval returned an invalid decision")
	default:
		return "", false, fmt.Errorf("native approval is no longer actionable (%s)", record.Status)
	}
}

// AcknowledgeApproval records delivery or invalidation of one request for this execution only.
func (h *taskApprovalHandler) AcknowledgeApproval(ctx context.Context, request agent.ApprovalRequest, acknowledgement agent.ApprovalAcknowledgement) error {
	if acknowledgement != agent.ApprovalProcessed && acknowledgement != agent.ApprovalExpired {
		return fmt.Errorf("unsupported native approval acknowledgement %q", acknowledgement)
	}
	h.mu.Lock()
	id := h.records[request.ID]
	h.mu.Unlock()
	if id == "" {
		return nil
	}
	body := h.scope()
	body["status"] = acknowledgement
	return h.client.postJSON(ctx, h.basePath()+"/"+url.PathEscape(id)+"/ack", body, nil)
}

// expire releases unresolved approvals after process exit without touching a later cold retry.
func (h *taskApprovalHandler) expire(ctx context.Context) error {
	return h.client.postJSON(ctx, h.basePath()+"/expire", h.scope(), nil)
}
