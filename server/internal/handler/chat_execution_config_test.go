package handler

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"

	"github.com/google/uuid"
	"github.com/multica-ai/multica/server/internal/service"
	"github.com/multica-ai/multica/server/internal/testutil"
	"github.com/multica-ai/multica/server/pkg/protocol"
)

// TestChatExecutionConfigRoundTripAndSendSnapshot exercises storage and both
// enqueue paths through claim, including stale callers and later settings edits.
func TestChatExecutionConfigRoundTripAndSendSnapshot(t *testing.T) {
	if testHandler == nil || testPool == nil {
		t.Skip("database not available")
	}
	ctx := context.Background()
	dbfx := testutil.New(testPool, testWorkspaceID, testUserID)
	runtimeID := dbfx.Runtime(t, "Execution config Codex", testutil.Cols{"provider": "codex"})
	agentID := dbfx.Agent(t, "Execution config agent", runtimeID, testutil.Cols{
		"model": "gpt-agent", "thinking_level": "low", "service_tier": "default",
	})

	create := withChatTestWorkspaceCtx(t, newRequest(http.MethodPost, "/api/chat/sessions", map[string]any{
		"agent_id": agentID, "execution_config": map[string]any{"approval_policy": "on-request"},
	}))
	var session ChatSessionResponse
	testutil.Call(t, testHandler.CreateChatSession, create).Want(http.StatusCreated).JSON(&session)
	dbfx.Cleanup(t, `DELETE FROM chat_session WHERE id = $1`, session.ID)
	dbfx.Cleanup(t, `DELETE FROM chat_message WHERE chat_session_id = $1`, session.ID)
	dbfx.Cleanup(t, `DELETE FROM agent_task_queue WHERE chat_session_id = $1`, session.ID)
	if !session.ExecutionConfigSupported || !session.ExecutionConfigCapabilities.ApprovalPolicy ||
		session.EffectiveExecutionConfig.ApprovalPolicy != "on-request" || session.EffectiveExecutionConfig.Model != "gpt-agent" {
		t.Fatalf("created execution config: %+v", session)
	}

	// The old create shape must not silently opt into a new approval policy.
	legacyCreate := withChatTestWorkspaceCtx(t, newRequest(http.MethodPost, "/api/chat/sessions", map[string]any{"agent_id": agentID}))
	var legacy ChatSessionResponse
	testutil.Call(t, testHandler.CreateChatSession, legacyCreate).Want(http.StatusCreated).JSON(&legacy)
	dbfx.Cleanup(t, `DELETE FROM chat_session WHERE id = $1`, legacy.ID)
	if legacy.EffectiveExecutionConfig.ApprovalPolicy != "" {
		t.Fatalf("legacy create changed approval behavior: %+v", legacy.EffectiveExecutionConfig)
	}

	patch := func(config any) ChatSessionResponse {
		t.Helper()
		req := withURLParam(withChatTestWorkspaceCtx(t, newRequest(http.MethodPatch, "/api/chat/sessions/"+session.ID,
			map[string]any{"execution_config": config})), "sessionId", session.ID)
		var out ChatSessionResponse
		testutil.Call(t, testHandler.UpdateChatSession, req).Want(http.StatusOK).JSON(&out)
		return out
	}
	patch(map[string]any{"model": "gpt-turn-one", "thinking_level": "high", "service_tier": "priority"})
	firstTaskID := sendDirectChat(t, ctx, agentID, session.ID, "first configured turn")
	staleSession, err := testHandler.Queries.GetChatSession(ctx, parseUUID(session.ID))
	if err != nil {
		t.Fatal(err)
	}
	carrier, err := testHandler.Queries.GetAgent(ctx, parseUUID(agentID))
	if err != nil {
		t.Fatal(err)
	}
	patch(map[string]any{"model": "gpt-turn-two"})
	second, err := testHandler.TaskService.SendDirectChatMessage(ctx, staleSession, carrier, parseUUID(testUserID), "queued configured turn", nil, "member", parseUUID(testUserID))
	if err != nil {
		t.Fatal(err)
	}
	secondTaskID := uuidToString(second.Task.ID)

	legacySession, err := testHandler.Queries.GetChatSession(ctx, parseUUID(legacy.ID))
	if err != nil {
		t.Fatal(err)
	}
	dbfx.Cleanup(t, `DELETE FROM agent_task_queue WHERE chat_session_id = $1`, legacy.ID)
	dbfx.Cleanup(t, `DELETE FROM chat_message WHERE chat_session_id = $1`, legacy.ID)
	dbfx.Exec(t, `INSERT INTO chat_message (chat_session_id, role, content) VALUES ($1, 'user', 'legacy configured turn')`, legacy.ID)
	legacyTask, err := testHandler.TaskService.EnqueueChatTask(ctx, legacySession, parseUUID(testUserID), false)
	if err != nil {
		t.Fatal(err)
	}

	dbfx.Exec(t, `UPDATE agent SET model = 'gpt-agent-later', thinking_level = 'medium', service_tier = 'priority' WHERE id = $1`, agentID)
	reset := patch(nil)
	if reset.EffectiveExecutionConfig.Model != "gpt-agent-later" || reset.EffectiveExecutionConfig.ApprovalPolicy != "" {
		t.Fatalf("reset did not restore current agent inheritance: %+v", reset.EffectiveExecutionConfig)
	}
	resetTaskID := sendDirectChat(t, ctx, agentID, session.ID, "inherited turn after reset")
	dbfx.Exec(t, `UPDATE agent SET model = 'gpt-agent-final', thinking_level = 'high', service_tier = 'default' WHERE id = $1`, agentID)
	dbfx.Cleanup(t, `DELETE FROM task_token WHERE agent_id = $1`, agentID)
	for _, turn := range []struct {
		id   string
		want service.ExecutionConfig
	}{
		{firstTaskID, service.ExecutionConfig{Model: "gpt-turn-one", ThinkingLevel: "high", ServiceTier: "priority", ApprovalPolicy: "on-request"}},
		{secondTaskID, service.ExecutionConfig{Model: "gpt-turn-two", ApprovalPolicy: "on-request"}},
		{uuidToString(legacyTask.ID), service.ExecutionConfig{Model: "gpt-agent", ThinkingLevel: "low", ServiceTier: "default"}},
		{resetTaskID, service.ExecutionConfig{Model: "gpt-agent-later", ThinkingLevel: "medium", ServiceTier: "priority"}},
	} {
		task, err := testHandler.Queries.GetAgentTask(ctx, parseUUID(turn.id))
		if err != nil {
			t.Fatal(err)
		}
		var stored struct {
			ExecutionConfig service.ExecutionConfig `json:"execution_config"`
		}
		if err := json.Unmarshal(task.Context, &stored); err != nil {
			t.Fatalf("task %s context: %v", turn.id, err)
		}
		if stored.ExecutionConfig != turn.want {
			t.Fatalf("task %s snapshot = %+v, want %+v", turn.id, stored.ExecutionConfig, turn.want)
		}
		var contextFields map[string]json.RawMessage
		if err := json.Unmarshal(task.Context, &contextFields); err != nil {
			t.Fatal(err)
		}
		var configFields map[string]json.RawMessage
		if err := json.Unmarshal(contextFields["execution_config"], &configFields); err != nil {
			t.Fatal(err)
		}
		if _, present := configFields["thinking_level"]; !present {
			t.Fatalf("task %s dropped explicit empty reasoning from its snapshot", turn.id)
		}

		req := withURLParam(newOwnerDaemonRequest(http.MethodPost, "/api/daemon/runtimes/"+runtimeID+"/claim", nil), "runtimeId", runtimeID)
		req.Header.Set("X-Client-Capabilities", protocol.DaemonCapabilityNativeApprovalV1)
		var claimed struct {
			Task *AgentTaskResponse `json:"task"`
		}
		testutil.Call(t, testHandler.ClaimTaskByRuntime, req).Want(http.StatusOK).JSON(&claimed)
		if claimed.Task == nil || claimed.Task.ID != turn.id || claimed.Task.ExecutionConfig == nil || *claimed.Task.ExecutionConfig != turn.want {
			t.Fatalf("claim lost send-time config for %s: %+v, want %+v", turn.id, claimed.Task, turn.want)
		}
		if claimed.Task.Agent == nil || claimed.Task.Agent.Model != turn.want.Model ||
			claimed.Task.Agent.ThinkingLevel != turn.want.ThinkingLevel || claimed.Task.Agent.ServiceTier != turn.want.ServiceTier {
			t.Fatalf("claim re-inherited current agent settings for %s: %+v, want %+v", turn.id, claimed.Task.Agent, turn.want)
		}
		queued, err := testHandler.Queries.GetAgentTask(ctx, parseUUID(turn.id))
		if err != nil {
			t.Fatal(err)
		}
		frozen, err := service.TaskExecutionConfig(queued.Context)
		if err != nil || queued.Status != "dispatched" || frozen == nil || *frozen != turn.want {
			t.Fatalf("claimed queue %s changed snapshot: status=%s config=%+v err=%v", turn.id, queued.Status, frozen, err)
		}
		// Release this turn to inspect the next queued snapshot without executing an agent.
		dbfx.Exec(t, `UPDATE agent_task_queue SET status = 'completed', completed_at = now() WHERE id = $1`, turn.id)
	}

	reset = patch(nil)
	var listed []ChatSessionResponse
	listReq := withChatTestWorkspaceCtx(t, newRequest(http.MethodGet, "/api/chat/sessions", nil))
	testutil.Call(t, testHandler.ListChatSessions, listReq).Want(http.StatusOK).JSON(&listed)
	found := false
	for _, row := range listed {
		if row.ID == session.ID {
			found = true
			if row.EffectiveExecutionConfig != reset.EffectiveExecutionConfig || !row.ExecutionConfigSupported {
				t.Fatalf("list lost execution metadata: %+v", row)
			}
		}
	}
	if !found {
		t.Fatalf("session %s missing from list", session.ID)
	}

	carrier, err = testHandler.Queries.GetAgent(ctx, parseUUID(agentID))
	if err != nil {
		t.Fatal(err)
	}
	mikaReq := withChatTestWorkspaceCtx(t, newRequest(http.MethodPost, "/api/workspace-agent", nil))
	var mika mikaAgentResponse
	testutil.Call(t, func(w http.ResponseWriter, r *http.Request) {
		testHandler.writeMikaAgentResponse(w, r, carrier, testWorkspaceID, testUserID, "Existing conversation", false)
	}, mikaReq).Want(http.StatusOK).JSON(&mika)
	if mika.OnboardingSession == nil || mika.OnboardingSession.ID != session.ID ||
		!mika.OnboardingSession.ExecutionConfigSupported || mika.OnboardingSession.EffectiveExecutionConfig != reset.EffectiveExecutionConfig ||
		mika.OnboardingSession.ExecutionConfigCapabilities != session.ExecutionConfigCapabilities {
		t.Fatalf("Mika response lost execution metadata: %+v", mika.OnboardingSession)
	}
}

// TestChatExecutionConfigGatesOwnershipAndRuntime proves rejected patches cannot mutate storage.
func TestChatExecutionConfigGatesOwnershipAndRuntime(t *testing.T) {
	if testHandler == nil || testPool == nil {
		t.Skip("database not available")
	}
	dbfx := testutil.New(testPool, testWorkspaceID, testUserID)
	runtimeID := dbfx.Runtime(t, "Execution config Claude", testutil.Cols{"provider": "claude"})
	agentID := dbfx.Agent(t, "Unsupported approval agent", runtimeID)
	foreignUser := dbfx.User(t, "Other chat creator", "other-chat-"+uuid.NewString()+"@example.test")
	foreignSession := dbfx.ChatSession(t, agentID, testutil.Cols{"creator_id": foreignUser, "explicitly_created_at": testutil.Raw("now()")})
	ownSession := dbfx.ChatSession(t, agentID, testutil.Cols{"explicitly_created_at": testutil.Raw("now()")})
	for _, tc := range []struct {
		id   string
		want int
	}{{foreignSession, http.StatusForbidden}, {ownSession, http.StatusBadRequest}} {
		req := withURLParam(withChatTestWorkspaceCtx(t, newRequest(http.MethodPatch, "/api/chat/sessions/"+tc.id,
			map[string]any{"execution_config": map[string]any{"approval_policy": "on-request"}})), "sessionId", tc.id)
		testutil.Call(t, testHandler.UpdateChatSession, req).Want(tc.want)
		var stored []byte
		dbfx.QueryRow(t, `SELECT execution_config FROM chat_session WHERE id = $1`, tc.id).Scan(&stored)
		var overrides map[string]any
		if err := json.Unmarshal(stored, &overrides); err != nil || len(overrides) != 0 {
			t.Fatalf("rejected patch modified session %s: %s, %v", tc.id, stored, err)
		}
	}
}
