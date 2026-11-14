package handler

import (
	"net/http"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/multica-ai/multica/server/internal/testutil"
)

// taskApprovalDeleteFixture isolates destructive API calls from the shared test workspace.
type taskApprovalDeleteFixture struct {
	fx                          *testutil.Fixture
	runtimeID, agentID, issueID string
	claim                       time.Time
}

// newTaskApprovalDeleteFixture creates real owners without resolving or starting an agent CLI.
func newTaskApprovalDeleteFixture(t *testing.T) *taskApprovalDeleteFixture {
	t.Helper()
	if testHandler == nil || testPool == nil {
		t.Skip("database not available")
	}
	workspaceID := dbfx.Workspace(t, "Approval deletion", "approval-delete-"+uuid.NewString())
	fx := testutil.New(testPool, workspaceID, testUserID)
	fx.Member(t, workspaceID, testUserID, "owner")
	runtimeID := fx.Runtime(t, "Approval deletion runtime", testutil.Cols{
		"provider": "codex", "daemon_id": "approval-delete-" + uuid.NewString(),
	})
	agentID := fx.Agent(t, "Approval deletion agent", runtimeID, testutil.Cols{"max_concurrent_tasks": 8})
	return &taskApprovalDeleteFixture{
		fx: fx, runtimeID: runtimeID, agentID: agentID, issueID: fx.Issue(t, "Approval deletion issue"),
		claim: time.Now().UTC().Truncate(time.Microsecond),
	}
}

// task binds every fixture row to the same claim while allowing chat and historical task shapes.
func (f *taskApprovalDeleteFixture) task(t *testing.T, over testutil.Cols) string {
	t.Helper()
	cols := testutil.Cols{
		"runtime_id": f.runtimeID, "issue_id": f.issueID, "status": "running", "attempt": 1,
		"dispatched_at": f.claim, "started_at": f.claim,
		"context": testutil.Raw("'{\"execution_config\":{\"approval_policy\":\"on-request\"}}'::jsonb"),
	}
	for key, value := range over {
		cols[key] = value
	}
	return f.fx.Task(t, f.agentID, cols)
}

// approval supplies the native scope; each test owns its status-specific fields and assertions.
func (f *taskApprovalDeleteFixture) approval(t *testing.T, taskID string, over testutil.Cols) string {
	t.Helper()
	cols := testutil.Cols{
		"task_id": taskID, "workspace_id": f.fx.WorkspaceID, "issue_id": f.issueID,
		"runtime_id": f.runtimeID, "dispatched_at": f.claim, "execution_id": uuid.NewString(),
		"native_request_id": "7", "kind": "command", "method": "item/commandExecution/requestApproval",
		"params": testutil.Raw("'{}'::json"),
	}
	for key, value := range over {
		cols[key] = value
	}
	return f.fx.Insert(t, "task_approval", cols)
}

// request attaches the fixture's actual membership instead of the global workspace's cached member.
func (f *taskApprovalDeleteFixture) request(t *testing.T, method, path string, body any) *http.Request {
	t.Helper()
	return taskApprovalMemberRequest(t, newRequest(method, path, body), testUserID, f.fx.WorkspaceID)
}

// TestTaskApprovalWorkspaceDeletion removes live, processed and orphan receipts without crossing tenants.
func TestTaskApprovalWorkspaceDeletion(t *testing.T) {
	f := newTaskApprovalDeleteFixture(t)
	pending := f.approval(t, f.task(t, nil), nil)
	processed := f.approval(t, f.task(t, testutil.Cols{"status": "completed"}), testutil.Cols{
		"status": "processed", "decision": "approve", "decided_by": testUserID,
		"decided_at": f.claim, "processed_at": f.claim,
	})
	orphan := f.approval(t, uuid.NewString(), nil)
	other := newTaskApprovalDeleteFixture(t)
	retained := other.approval(t, other.task(t, nil), nil)
	req := withURLParams(f.request(t, http.MethodDelete, "/api/workspaces/"+f.fx.WorkspaceID, nil), "id", f.fx.WorkspaceID)
	testutil.Call(t, testHandler.DeleteWorkspace, req).Want(http.StatusNoContent)
	for _, id := range []string{pending, processed, orphan} {
		if count := f.fx.Count(t, `SELECT count(*) FROM task_approval WHERE id = $1`, id); count != 0 {
			t.Fatalf("workspace deletion retained approval %s", id)
		}
	}
	if count := other.fx.Count(t, `SELECT count(*) FROM task_approval WHERE id = $1 AND status = 'pending'`, retained); count != 1 {
		t.Fatalf("workspace deletion touched neighboring approval %s", retained)
	}
}

// TestTaskApprovalIssueDeletion covers both API entries through their shared deletion transaction.
func TestTaskApprovalIssueDeletion(t *testing.T) {
	f := newTaskApprovalDeleteFixture(t)
	parentApproval := f.approval(t, f.task(t, nil), nil)
	childID := f.fx.Issue(t, "Retained child", testutil.Cols{"parent_issue_id": f.issueID})
	childApproval := f.approval(t, f.task(t, testutil.Cols{"issue_id": childID}), testutil.Cols{"issue_id": childID})
	req := withURLParams(f.request(t, http.MethodDelete, "/api/issues/"+f.issueID, nil), "id", f.issueID)
	testutil.Call(t, testHandler.DeleteIssue, req).Want(http.StatusNoContent)
	if count := f.fx.Count(t, `SELECT count(*) FROM task_approval WHERE id = $1`, parentApproval); count != 0 {
		t.Fatalf("single issue deletion retained approval %s", parentApproval)
	}
	if count := f.fx.Count(t, `SELECT count(*) FROM task_approval WHERE id = $1 AND status = 'pending'`, childApproval); count != 1 {
		t.Fatalf("parent deletion touched retained child approval %s", childApproval)
	}
	siblingID := f.fx.Issue(t, "Batch deletion sibling")
	siblingApproval := f.approval(t, f.task(t, testutil.Cols{"issue_id": siblingID}), testutil.Cols{"issue_id": siblingID})
	body := map[string]any{"issue_ids": []string{childID, siblingID}}
	var result struct {
		Deleted int `json:"deleted"`
	}
	testutil.Call(t, testHandler.BatchDeleteIssues, f.request(t, http.MethodPost, "/api/issues", body)).Want(http.StatusOK).JSON(&result)
	if result.Deleted != 2 {
		t.Fatalf("batch deletion removed %d issues, want 2", result.Deleted)
	}
	for _, id := range []string{childApproval, siblingApproval} {
		if count := f.fx.Count(t, `SELECT count(*) FROM task_approval WHERE id = $1`, id); count != 0 {
			t.Fatalf("batch issue deletion retained approval %s", id)
		}
	}
}

// TestTaskApprovalChatDeletion removes processed history before the session-to-task link disappears.
func TestTaskApprovalChatDeletion(t *testing.T) {
	f := newTaskApprovalDeleteFixture(t)
	sessionID := f.fx.ChatSession(t, f.agentID)
	pending := f.approval(t, f.task(t, testutil.Cols{"issue_id": nil, "chat_session_id": sessionID}), testutil.Cols{"issue_id": nil})
	historicalTask := f.task(t, testutil.Cols{"issue_id": nil, "chat_session_id": sessionID, "status": "completed"})
	processed := f.approval(t, historicalTask, testutil.Cols{
		"issue_id": nil, "status": "processed", "decision": "approve", "decided_by": testUserID,
		"decided_at": f.claim, "processed_at": f.claim,
	})
	otherSessionID := f.fx.ChatSession(t, f.agentID)
	otherTask := f.task(t, testutil.Cols{"issue_id": nil, "chat_session_id": otherSessionID})
	retained := f.approval(t, otherTask, testutil.Cols{"issue_id": nil})
	req := withURLParams(f.request(t, http.MethodDelete, "/api/chat/sessions/"+sessionID, nil), "sessionId", sessionID)
	testutil.Call(t, testHandler.DeleteChatSession, req).Want(http.StatusNoContent)
	for _, id := range []string{pending, processed} {
		if count := f.fx.Count(t, `SELECT count(*) FROM task_approval WHERE id = $1`, id); count != 0 {
			t.Fatalf("chat deletion retained approval %s", id)
		}
	}
	if count := f.fx.Count(t, `SELECT count(*) FROM task_approval WHERE id = $1 AND status = 'pending'`, retained); count != 1 {
		t.Fatalf("chat deletion touched another session's approval %s", retained)
	}
}
