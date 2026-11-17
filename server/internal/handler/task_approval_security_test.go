package handler

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/multica-ai/multica/server/internal/middleware"
	"github.com/multica-ai/multica/server/internal/testutil"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

// taskApprovalMemberRequest injects a real fixture membership, never an unrelated cached member.
func taskApprovalMemberRequest(t *testing.T, req *http.Request, userID, workspaceID string) *http.Request {
	t.Helper()
	member, err := testHandler.Queries.GetMemberByUserAndWorkspace(context.Background(), db.GetMemberByUserAndWorkspaceParams{UserID: parseUUID(userID), WorkspaceID: parseUUID(workspaceID)})
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("X-User-ID", userID)
	req.Header.Set("X-Workspace-ID", workspaceID)
	return req.WithContext(middleware.SetMemberContext(req.Context(), workspaceID, member))
}

// TestTaskApprovalRegistrationRequiresOptIn proves the persistence entry point does not change a legacy issue policy.
func TestTaskApprovalRegistrationRequiresOptIn(t *testing.T) {
	f := newTaskApprovalFixture(t)
	dbfx.Exec(t, `UPDATE agent_task_queue SET context = '{}'::jsonb WHERE id = $1`, f.taskID)
	testutil.Call(t, testHandler.DaemonRegisterTaskApproval, f.daemonRequest(http.MethodPost, "", f.registrationBody("7"))).Want(http.StatusConflict)
	if count := dbfx.Count(t, `SELECT count(*) FROM task_approval WHERE task_id = $1`, f.taskID); count != 0 {
		t.Fatalf("legacy task created %d approvals", count)
	}
	var contextJSON string
	dbfx.QueryRow(t, `SELECT context::text FROM agent_task_queue WHERE id = $1`, f.taskID).Scan(&contextJSON)
	if contextJSON != "{}" {
		t.Fatalf("approval request changed legacy task policy: %s", contextJSON)
	}
}

// TestNativeApprovalRedactsPersistenceAndAPI keeps raw diff credentials out of storage and every read surface.
func TestNativeApprovalRedactsPersistenceAndAPI(t *testing.T) {
	f := newTaskApprovalFixture(t)
	secret := "fixture-" + "credential"
	body := f.registrationBody("7")
	body["kind"], body["method"] = "file_change", "item/fileChange/requestApproval"
	body["params"] = map[string]any{
		"api_key": secret,
		"changes": []any{map[string]any{
			"path": ".env", "diff": "+DB_PASSWORD=" + secret + "\n+{\"api_key\":\"" + secret + "\"}",
		}},
	}
	var approval TaskApprovalResponse
	testutil.Call(t, testHandler.DaemonRegisterTaskApproval, f.daemonRequest(http.MethodPost, "", body)).Want(http.StatusCreated).JSON(&approval)
	var stored string
	dbfx.QueryRow(t, `SELECT params::text FROM task_approval WHERE task_id = $1 AND id = $2`, f.taskID, approval.ID).Scan(&stored)
	if strings.Contains(stored, secret) || strings.Contains(string(approval.Params), secret) {
		t.Fatal("native registration persisted or returned a raw credential")
	}
	raw, err := json.Marshal(body["params"])
	if err != nil {
		t.Fatal(err)
	}
	dbfx.Exec(t, `UPDATE task_approval SET params = $1::json WHERE task_id = $2 AND id = $3`, string(raw), f.taskID, approval.ID)
	var userRows, daemonRows []TaskApprovalResponse
	testutil.Call(t, testHandler.ListTaskApprovalsByUser, f.userRequest(t, http.MethodGet, "", nil)).Want(http.StatusOK).JSON(&userRows)
	testutil.Call(t, testHandler.DaemonListTaskApprovals, f.daemonRequest(http.MethodGet, approvalExecutionQuery(f.execution), nil)).Want(http.StatusOK).JSON(&daemonRows)
	if len(userRows) != 1 || len(daemonRows) != 1 || strings.Contains(string(userRows[0].Params), secret) || strings.Contains(string(daemonRows[0].Params), secret) {
		t.Fatal("an API read exposed historical unsanitized native parameters")
	}
}

// TestTaskApprovalPrivateChatAndMachineGate keeps private approvals creator-only and prevents owner-bound machine decisions.
func TestTaskApprovalPrivateChatAndMachineGate(t *testing.T) {
	f := newTaskApprovalFixture(t)
	sessionID := dbfx.ChatSession(t, f.agentID)
	dbfx.Exec(t, `UPDATE agent_task_queue SET issue_id = NULL, chat_session_id = $1 WHERE id = $2`, sessionID, f.taskID)
	approval := f.register(t, "7")
	otherUser := dbfx.User(t, "Approval member", "native-approval-"+uuid.NewString()+"@example.test")
	dbfx.Member(t, testWorkspaceID, otherUser, "owner")
	listAsOther := taskApprovalMemberRequest(t, f.userRequest(t, http.MethodGet, "", nil), otherUser, testWorkspaceID)
	testutil.Call(t, testHandler.ListTaskApprovalsByUser, listAsOther).Want(http.StatusNotFound)
	decideAsOther := taskApprovalMemberRequest(t, withURLParams(f.userRequest(t, http.MethodPost, "/"+approval.ID+"/decision", approvalDecisionBody(approval, "approve")), "approvalId", approval.ID), otherUser, testWorkspaceID)
	testutil.Call(t, testHandler.DecideTaskApprovalByUser, decideAsOther).Want(http.StatusNotFound)
	var privateRows []TaskApprovalResponse
	testutil.Call(t, testHandler.ListTaskApprovalsByUser, f.userRequest(t, http.MethodGet, "", nil)).Want(http.StatusOK).JSON(&privateRows)
	if len(privateRows) != 1 || !privateRows[0].CanDecide {
		t.Fatalf("creator lost private approval access: %+v", privateRows)
	}
	machineRead := f.userRequest(t, http.MethodGet, "", nil)
	machineRead.Header.Set("X-Actor-Source", "task_token")
	testutil.Call(t, testHandler.ListTaskApprovalsByUser, machineRead).Want(http.StatusNotFound)
	for _, source := range []string{"task_token", "cloud_pat"} {
		machineDecision := withURLParams(f.userRequest(t, http.MethodPost, "/"+approval.ID+"/decision", approvalDecisionBody(approval, "approve")), "approvalId", approval.ID)
		machineDecision.Header.Set("X-Actor-Source", source)
		testutil.Call(t, testHandler.DecideTaskApprovalByUser, machineDecision).Want(http.StatusForbidden)
	}
	foreignDaemon := withURLParams(newDaemonTokenRequest(http.MethodPost, "/api/daemon/tasks/"+f.taskID+"/approvals", f.registrationBody("9"), testWorkspaceID, "other-"+f.daemonID), "taskId", f.taskID)
	testutil.Call(t, testHandler.DaemonRegisterTaskApproval, foreignDaemon).Want(http.StatusNotFound)
}

// TestTaskApprovalIssueMembershipAndWorkspace reuses issue/project visibility without granting a cross-workspace view.
func TestTaskApprovalIssueMembershipAndWorkspace(t *testing.T) {
	f := newTaskApprovalFixture(t)
	approval := f.register(t, "7")
	memberUser := dbfx.User(t, "Issue approval member", "native-approval-"+uuid.NewString()+"@example.test")
	dbfx.Member(t, testWorkspaceID, memberUser, "member")
	var publicRows []TaskApprovalResponse
	memberList := taskApprovalMemberRequest(t, f.userRequest(t, http.MethodGet, "", nil), memberUser, testWorkspaceID)
	testutil.Call(t, testHandler.ListTaskApprovalsByUser, memberList).Want(http.StatusOK).JSON(&publicRows)
	if len(publicRows) != 1 || !publicRows[0].CanDecide {
		t.Fatalf("visible issue member approval: %+v", publicRows)
	}
	machineList := f.userRequest(t, http.MethodGet, "", nil)
	machineList.Header.Set("X-Actor-Source", "task_token")
	testutil.Call(t, testHandler.ListTaskApprovalsByUser, machineList).Want(http.StatusOK).JSON(&publicRows)
	if len(publicRows) != 1 || publicRows[0].CanDecide {
		t.Fatalf("machine decision affordance: %+v", publicRows)
	}
	memberDecision := taskApprovalMemberRequest(t, withURLParams(f.userRequest(t, http.MethodPost, "/"+approval.ID+"/decision", approvalDecisionBody(approval, "approve")), "approvalId", approval.ID), memberUser, testWorkspaceID)
	var decided TaskApprovalResponse
	testutil.Call(t, testHandler.DecideTaskApprovalByUser, memberDecision).Want(http.StatusOK).JSON(&decided)
	if decided.DecidedBy == nil || *decided.DecidedBy != memberUser {
		t.Fatalf("member identity not recorded: %+v", decided)
	}
	projectID := dbfx.Project(t, "Private approval project", testutil.Cols{"visibility": "private"})
	dbfx.Exec(t, `UPDATE issue SET project_id = $1 WHERE id = $2`, projectID, f.issueID)
	testutil.Call(t, testHandler.ListTaskApprovalsByUser, taskApprovalMemberRequest(t, f.userRequest(t, http.MethodGet, "", nil), memberUser, testWorkspaceID)).Want(http.StatusNotFound)
	testutil.Call(t, testHandler.DecideTaskApprovalByUser, taskApprovalMemberRequest(t, withURLParams(f.userRequest(t, http.MethodPost, "/"+approval.ID+"/decision", approvalDecisionBody(approval, "approve")), "approvalId", approval.ID), memberUser, testWorkspaceID)).Want(http.StatusNotFound)
	foreignWorkspace := dbfx.Workspace(t, "Foreign approval workspace", "native-approval-"+uuid.NewString())
	dbfx.Member(t, foreignWorkspace, testUserID, "owner")
	testutil.Call(t, testHandler.ListTaskApprovalsByUser, taskApprovalMemberRequest(t, f.userRequest(t, http.MethodGet, "", nil), testUserID, foreignWorkspace)).Want(http.StatusNotFound)
	testutil.Call(t, testHandler.DecideTaskApprovalByUser, taskApprovalMemberRequest(t, withURLParams(f.userRequest(t, http.MethodPost, "/"+approval.ID+"/decision", approvalDecisionBody(approval, "approve")), "approvalId", approval.ID), testUserID, foreignWorkspace)).Want(http.StatusNotFound)
}
