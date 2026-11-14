package handler

import (
	"net/http"
	"testing"

	"github.com/google/uuid"
	"github.com/multica-ai/multica/server/internal/testutil"
)

// TestTaskApprovalConcurrentDecision makes opposite human decisions contend on the same locked task.
func TestTaskApprovalConcurrentDecision(t *testing.T) {
	f := newTaskApprovalFixture(t)
	approval := f.register(t, "7")
	start := make(chan struct{})
	results := make(chan *testutil.Response, 2)
	for _, decision := range []string{"approve", "deny"} {
		req := withURLParams(f.userRequest(t, http.MethodPost, "/"+approval.ID+"/decision", approvalDecisionBody(approval, decision)), "approvalId", approval.ID)
		go func(req *http.Request) {
			<-start
			results <- testutil.Call(t, testHandler.DecideTaskApprovalByUser, req)
		}(req)
	}
	close(start)
	first, second := <-results, <-results
	first.WantOneOf(http.StatusOK, http.StatusConflict)
	second.WantOneOf(http.StatusOK, http.StatusConflict)
	if first.Code == second.Code {
		t.Fatalf("opposite decisions were not serialized: %d, %d", first.Code, second.Code)
	}
	winner := first
	if second.Code == http.StatusOK {
		winner = second
	}
	var decided TaskApprovalResponse
	winner.JSON(&decided)
	var status, decision string
	dbfx.QueryRow(t, `SELECT status, decision FROM task_approval WHERE task_id = $1 AND id = $2`, f.taskID, approval.ID).Scan(&status, &decision)
	if status != "decided" || decided.Decision == nil || decision != *decided.Decision {
		t.Fatalf("winning decision not persisted: %+v, %s/%s", decided, status, decision)
	}
}

// TestNativeApprovalColdRetryIsolation keeps a reset native ID separate and expires only the exiting process.
func TestNativeApprovalColdRetryIsolation(t *testing.T) {
	f := newTaskApprovalFixture(t)
	oldExecution := f.execution
	old := f.register(t, "7")
	oldPending := f.register(t, "8")
	decideOld := withURLParams(f.userRequest(t, http.MethodPost, "/"+old.ID+"/decision", approvalDecisionBody(old, "approve")), "approvalId", old.ID)
	testutil.Call(t, testHandler.DecideTaskApprovalByUser, decideOld).Want(http.StatusOK)
	f.execution.ExecutionID = uuid.NewString()
	current := f.register(t, "7")
	if current.ID == old.ID || current.Status != "pending" || current.Decision != nil || current.ExecutionID == old.ExecutionID {
		t.Fatalf("cold retry reused native authority: old=%+v current=%+v", old, current)
	}
	var live []TaskApprovalResponse
	testutil.Call(t, testHandler.DaemonListTaskApprovals, f.daemonRequest(http.MethodGet, approvalExecutionQuery(f.execution), nil)).Want(http.StatusOK).JSON(&live)
	if len(live) != 1 || live[0].ID != current.ID || live[0].Decision != nil {
		t.Fatalf("new process received old decisions: %+v", live)
	}
	wrongAck := withURLParams(f.daemonRequest(http.MethodPost, "/"+old.ID+"/ack", f.execution), "approvalId", old.ID)
	testutil.Call(t, testHandler.DaemonAckTaskApproval, wrongAck).Want(http.StatusConflict)
	testutil.Call(t, testHandler.DaemonExpireTaskApprovals, f.daemonRequest(http.MethodPost, "/expire", oldExecution)).Want(http.StatusNoContent)
	testutil.Call(t, testHandler.DaemonExpireTaskApprovals, f.daemonRequest(http.MethodPost, "/expire", oldExecution)).Want(http.StatusNoContent)
	var history []TaskApprovalResponse
	testutil.Call(t, testHandler.ListTaskApprovalsByUser, f.userRequest(t, http.MethodGet, "", nil)).Want(http.StatusOK).JSON(&history)
	byID := make(map[string]TaskApprovalResponse)
	for _, row := range history {
		byID[row.ID] = row
	}
	if byID[old.ID].Status != "expired" || byID[oldPending.ID].Status != "expired" || byID[old.ID].CanDecide || byID[current.ID].Status != "pending" || !byID[current.ID].CanDecide {
		t.Fatalf("process expiration touched wrong scope: %+v", history)
	}
	testutil.Call(t, testHandler.DecideTaskApprovalByUser, withURLParams(f.userRequest(t, http.MethodPost, "/"+old.ID+"/decision", approvalDecisionBody(old, "approve")), "approvalId", old.ID)).Want(http.StatusConflict)
	f.execution = oldExecution
	testutil.Call(t, testHandler.DaemonRegisterTaskApproval, f.daemonRequest(http.MethodPost, "", f.registrationBody("7"))).Want(http.StatusConflict)
	f.execution.ExecutionID = current.ExecutionID
	cancel := map[string]any{"runtime_id": f.runtimeID, "dispatched_at": f.execution.DispatchedAt, "execution_id": f.execution.ExecutionID, "status": "expired"}
	cancelRequest := func() *http.Request {
		return withURLParams(f.daemonRequest(http.MethodPost, "/"+current.ID+"/ack", cancel), "approvalId", current.ID)
	}
	testutil.Call(t, testHandler.DaemonAckTaskApproval, cancelRequest()).Want(http.StatusOK)
	testutil.Call(t, testHandler.DaemonAckTaskApproval, cancelRequest()).Want(http.StatusOK)
	testutil.Call(t, testHandler.DaemonRegisterTaskApproval, f.daemonRequest(http.MethodPost, "", f.registrationBody("7"))).Want(http.StatusConflict)
}

// TestTaskApprovalClaimFenceHTTP rejects stale claims while retaining history without decision authority.
func TestTaskApprovalClaimFenceHTTP(t *testing.T) {
	f := newTaskApprovalFixture(t)
	approval := f.register(t, "7")
	wrongRuntime := f.registrationBody("9")
	wrongRuntime["runtime_id"] = uuid.NewString()
	testutil.Call(t, testHandler.DaemonRegisterTaskApproval, f.daemonRequest(http.MethodPost, "", wrongRuntime)).Want(http.StatusConflict)
	dbfx.Exec(t, `UPDATE agent_task_queue SET dispatched_at = dispatched_at + interval '1 microsecond', attempt = attempt + 1 WHERE id = $1`, f.taskID)
	testutil.Call(t, testHandler.DaemonRegisterTaskApproval, f.daemonRequest(http.MethodPost, "", f.registrationBody("7"))).Want(http.StatusConflict)
	testutil.Call(t, testHandler.DaemonListTaskApprovals, f.daemonRequest(http.MethodGet, approvalExecutionQuery(f.execution), nil)).Want(http.StatusConflict)
	testutil.Call(t, testHandler.DaemonAckTaskApproval, withURLParams(f.daemonRequest(http.MethodPost, "/"+approval.ID+"/ack", f.execution), "approvalId", approval.ID)).Want(http.StatusConflict)
	testutil.Call(t, testHandler.DaemonExpireTaskApprovals, f.daemonRequest(http.MethodPost, "/expire", f.execution)).Want(http.StatusConflict)
	testutil.Call(t, testHandler.DecideTaskApprovalByUser, withURLParams(f.userRequest(t, http.MethodPost, "/"+approval.ID+"/decision", approvalDecisionBody(approval, "approve")), "approvalId", approval.ID)).Want(http.StatusConflict)
	var history []TaskApprovalResponse
	testutil.Call(t, testHandler.ListTaskApprovalsByUser, f.userRequest(t, http.MethodGet, "", nil)).Want(http.StatusOK).JSON(&history)
	if len(history) != 1 || history[0].Status != "expired" || history[0].CanDecide {
		t.Fatalf("reclaimed approval remains actionable: %+v", history)
	}
}

// TestTaskApprovalTerminalSettlement checks persistence before any approval read can expire rows lazily.
func TestTaskApprovalTerminalSettlement(t *testing.T) {
	f := newTaskApprovalFixture(t)
	pending := f.register(t, "7")
	decided := f.register(t, "8")
	processed := f.register(t, "9")
	for _, approval := range []TaskApprovalResponse{decided, processed} {
		req := withURLParams(f.userRequest(t, http.MethodPost, "/"+approval.ID+"/decision", approvalDecisionBody(approval, "approve")), "approvalId", approval.ID)
		testutil.Call(t, testHandler.DecideTaskApprovalByUser, req).Want(http.StatusOK)
	}
	ack := withURLParams(f.daemonRequest(http.MethodPost, "/"+processed.ID+"/ack", f.execution), "approvalId", processed.ID)
	testutil.Call(t, testHandler.DaemonAckTaskApproval, ack).Want(http.StatusOK)
	other := newTaskApprovalFixture(t)
	retained := other.register(t, "7")
	testHandler.TaskService.CancelTasksForIssue(t.Context(), parseUUID(f.issueID))
	if count := dbfx.Count(t, `SELECT count(*) FROM agent_task_queue WHERE id = $1 AND status = 'cancelled'`, f.taskID); count != 1 {
		t.Fatalf("issue cancellation did not commit terminal task %s", f.taskID)
	}
	for _, expected := range []struct{ id, status string }{
		{pending.ID, "expired"}, {decided.ID, "expired"}, {processed.ID, "processed"}, {retained.ID, "pending"},
	} {
		if count := dbfx.Count(t, `SELECT count(*) FROM task_approval WHERE id = $1 AND status = $2`, expected.id, expected.status); count != 1 {
			t.Fatalf("terminal settlement did not preserve approval %s as %s", expected.id, expected.status)
		}
	}
}

// TestTaskApprovalTerminalExit allows exact cleanup after cancellation but no registration or delivery.
func TestTaskApprovalTerminalExit(t *testing.T) {
	f := newTaskApprovalFixture(t)
	approval := f.register(t, "7")
	dbfx.Exec(t, `UPDATE agent_task_queue SET status = 'cancelled' WHERE id = $1`, f.taskID)
	testutil.Call(t, testHandler.DaemonRegisterTaskApproval, f.daemonRequest(http.MethodPost, "", f.registrationBody("8"))).Want(http.StatusConflict)
	testutil.Call(t, testHandler.DaemonAckTaskApproval, withURLParams(f.daemonRequest(http.MethodPost, "/"+approval.ID+"/ack", f.execution), "approvalId", approval.ID)).Want(http.StatusConflict)
	testutil.Call(t, testHandler.DaemonExpireTaskApprovals, f.daemonRequest(http.MethodPost, "/expire", f.execution)).Want(http.StatusNoContent)
	var history []TaskApprovalResponse
	testutil.Call(t, testHandler.ListTaskApprovalsByUser, f.userRequest(t, http.MethodGet, "", nil)).Want(http.StatusOK).JSON(&history)
	if len(history) != 1 || history[0].Status != "expired" || history[0].ExpiredAt == nil || history[0].CanDecide {
		t.Fatalf("terminal cleanup: %+v", history)
	}
}
