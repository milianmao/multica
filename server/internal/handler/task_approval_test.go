package handler

import (
	"net/http"
	"net/url"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/multica-ai/multica/server/internal/testutil"
)

// taskApprovalFixture binds HTTP calls to one real task claim and native process.
type taskApprovalFixture struct {
	taskID, runtimeID, daemonID, agentID, issueID string
	execution                                     taskApprovalExecutionRequest
}

// newTaskApprovalFixture uses shared database builders and cleans up only this task's approval rows.
func newTaskApprovalFixture(t *testing.T) *taskApprovalFixture {
	t.Helper()
	if testHandler == nil || testPool == nil {
		t.Skip("database not available")
	}
	f := &taskApprovalFixture{daemonID: "native-approval-" + uuid.NewString()}
	f.runtimeID = dbfx.Runtime(t, "Native approval runtime", testutil.Cols{"provider": "codex", "daemon_id": f.daemonID})
	// Agent names are unique per workspace (migration 046), and one test builds two
	// fixtures in the same workspace, so each one needs its own name.
	f.agentID = dbfx.Agent(t, "Native approval agent "+uuid.NewString(), f.runtimeID)
	f.issueID = dbfx.Issue(t, "Native approval issue")
	claim := time.Now().UTC().Truncate(time.Microsecond)
	f.taskID = dbfx.Task(t, f.agentID, testutil.Cols{
		"issue_id": f.issueID, "runtime_id": f.runtimeID, "status": "running", "attempt": 1,
		"dispatched_at": claim, "started_at": claim,
		"context": testutil.Raw("'{\"execution_config\":{\"approval_policy\":\"on-request\"}}'::jsonb"),
	})
	f.execution = taskApprovalExecutionRequest{RuntimeID: f.runtimeID, DispatchedAt: claim.Format(time.RFC3339Nano), ExecutionID: uuid.NewString()}
	dbfx.Cleanup(t, `DELETE FROM task_approval WHERE task_id = $1`, f.taskID)
	return f
}

// registrationBody mirrors the native registration wire shape without a request ID numeric conversion.
func (f *taskApprovalFixture) registrationBody(nativeID string) map[string]any {
	return map[string]any{
		"runtime_id": f.execution.RuntimeID, "dispatched_at": f.execution.DispatchedAt, "execution_id": f.execution.ExecutionID,
		"native_request_id": nativeID, "kind": "command", "method": "item/commandExecution/requestApproval",
		"params": map[string]any{"command": "git status"},
	}
}

// daemonRequest supplies the existing daemon workspace/owner authorization and chi task parameter.
func (f *taskApprovalFixture) daemonRequest(method, suffix string, body any) *http.Request {
	return withURLParams(newDaemonTokenRequest(method, "/api/daemon/tasks/"+f.taskID+"/approvals"+suffix, body, testWorkspaceID, f.daemonID), "taskId", f.taskID)
}

// userRequest supplies the same workspace/member context as the protected user route.
func (f *taskApprovalFixture) userRequest(t *testing.T, method, suffix string, body any) *http.Request {
	t.Helper()
	return withURLParams(withChatTestWorkspaceCtx(t, newRequest(method, "/api/tasks/"+f.taskID+"/approvals"+suffix, body)), "taskId", f.taskID)
}

// register exercises the HTTP persistence path; behavior assertions remain in each test.
func (f *taskApprovalFixture) register(t *testing.T, nativeID string) TaskApprovalResponse {
	t.Helper()
	var out TaskApprovalResponse
	testutil.Call(t, testHandler.DaemonRegisterTaskApproval, f.daemonRequest(http.MethodPost, "", f.registrationBody(nativeID))).Want(http.StatusCreated).JSON(&out)
	return out
}

// approvalExecutionQuery encodes the full claim timestamp and the exact process scope.
func approvalExecutionQuery(execution taskApprovalExecutionRequest) string {
	return "?" + url.Values{"runtime_id": {execution.RuntimeID}, "dispatched_at": {execution.DispatchedAt}, "execution_id": {execution.ExecutionID}}.Encode()
}

// approvalDecisionBody submits the scope the UI received rather than a fresh execution UUID.
func approvalDecisionBody(approval TaskApprovalResponse, decision string) map[string]any {
	return map[string]any{"runtime_id": approval.RuntimeID, "dispatched_at": approval.DispatchedAt, "execution_id": approval.ExecutionID, "decision": decision}
}

// TestTaskApprovalLifecycle covers registration, single human decision, daemon delivery and receipt retry.
func TestTaskApprovalLifecycle(t *testing.T) {
	f := newTaskApprovalFixture(t)
	approval := f.register(t, "7")
	duplicate := f.register(t, " 7 ")
	if approval.ID == "" || duplicate.ID != approval.ID || approval.Status != "pending" || approval.CanDecide {
		t.Fatalf("registered native approval: %+v, duplicate: %+v", approval, duplicate)
	}
	changed := f.registrationBody("7")
	changed["params"] = map[string]any{"command": "git reset --hard"}
	testutil.Call(t, testHandler.DaemonRegisterTaskApproval, f.daemonRequest(http.MethodPost, "", changed)).Want(http.StatusConflict)
	ackRequest := func() *http.Request {
		return withURLParams(f.daemonRequest(http.MethodPost, "/"+approval.ID+"/ack", f.execution), "approvalId", approval.ID)
	}
	testutil.Call(t, testHandler.DaemonAckTaskApproval, ackRequest()).Want(http.StatusConflict)
	var history []TaskApprovalResponse
	testutil.Call(t, testHandler.ListTaskApprovalsByUser, f.userRequest(t, http.MethodGet, "", nil)).Want(http.StatusOK).JSON(&history)
	if len(history) != 1 || !history[0].CanDecide || history[0].ExecutionID != f.execution.ExecutionID || history[0].DispatchedAt != f.execution.DispatchedAt {
		t.Fatalf("pending user projection: %+v", history)
	}
	decisionRequest := func(decision string) *http.Request {
		return withURLParams(f.userRequest(t, http.MethodPost, "/"+approval.ID+"/decision", approvalDecisionBody(approval, decision)), "approvalId", approval.ID)
	}
	var decided, repeated TaskApprovalResponse
	testutil.Call(t, testHandler.DecideTaskApprovalByUser, decisionRequest("approve")).Want(http.StatusOK).JSON(&decided)
	testutil.Call(t, testHandler.DecideTaskApprovalByUser, decisionRequest("approve")).Want(http.StatusOK).JSON(&repeated)
	testutil.Call(t, testHandler.DecideTaskApprovalByUser, decisionRequest("deny")).Want(http.StatusConflict)
	if decided.Status != "decided" || decided.Decision == nil || *decided.Decision != "approve" || decided.DecidedBy == nil || *decided.DecidedBy != testUserID || decided.DecidedAt == nil || repeated.DecidedAt == nil || *repeated.DecidedAt != *decided.DecidedAt {
		t.Fatalf("single decision and retry: %+v, %+v", decided, repeated)
	}
	var live []TaskApprovalResponse
	testutil.Call(t, testHandler.DaemonListTaskApprovals, f.daemonRequest(http.MethodGet, approvalExecutionQuery(f.execution), nil)).Want(http.StatusOK).JSON(&live)
	if len(live) != 1 || live[0].Status != "decided" || live[0].Decision == nil || *live[0].Decision != "approve" {
		t.Fatalf("daemon decision: %+v", live)
	}
	var processed TaskApprovalResponse
	testutil.Call(t, testHandler.DaemonAckTaskApproval, ackRequest()).Want(http.StatusOK).JSON(&processed)
	testutil.Call(t, testHandler.DaemonAckTaskApproval, ackRequest()).Want(http.StatusOK)
	if processed.Status != "processed" || processed.ProcessedAt == nil {
		t.Fatalf("receipt: %+v", processed)
	}
	testutil.Call(t, testHandler.DecideTaskApprovalByUser, decisionRequest("approve")).Want(http.StatusOK)
	testutil.Call(t, testHandler.DecideTaskApprovalByUser, decisionRequest("deny")).Want(http.StatusConflict)
	testutil.Call(t, testHandler.DaemonListTaskApprovals, f.daemonRequest(http.MethodGet, approvalExecutionQuery(f.execution), nil)).Want(http.StatusOK).JSON(&live)
	if len(live) != 0 {
		t.Fatalf("processed request remains live: %+v", live)
	}
	testutil.Call(t, testHandler.ListTaskApprovalsByUser, f.userRequest(t, http.MethodGet, "", nil)).Want(http.StatusOK).JSON(&history)
	if len(history) != 1 || history[0].Status != "processed" || history[0].CanDecide {
		t.Fatalf("receipt history: %+v", history)
	}
}
