package handler

import (
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/multica-ai/multica/server/internal/auth"
	"github.com/multica-ai/multica/server/internal/middleware"
	"github.com/multica-ai/multica/server/internal/testutil"
)

// TestChatApprovalConfigOwnerBoundTaskToken exercises Auth and both chat entry points with the creator's machine token.
func TestChatApprovalConfigOwnerBoundTaskToken(t *testing.T) {
	if testHandler == nil || testPool == nil {
		t.Skip("database not available")
	}
	fx := testutil.New(testPool, testWorkspaceID, testUserID)
	runtimeID := fx.Runtime(t, "Chat approval guard Codex", testutil.Cols{"provider": "codex"})
	agentID := fx.Agent(t, "Chat approval guard agent", runtimeID, testutil.Cols{"model": "gpt-agent"})
	sessionID := fx.ChatSession(t, agentID, testutil.Cols{
		"explicitly_created_at": testutil.Raw("now()"),
		"execution_config":      testutil.Raw("'{\"model\":\"gpt-session\",\"approval_policy\":\"on-request\"}'::jsonb"),
	})
	taskID := fx.Task(t, agentID, testutil.Cols{
		"status": "running", "runtime_id": runtimeID, "chat_session_id": sessionID,
		"originator_user_id": testUserID,
	})
	token := "mat_chat_approval_" + uuid.NewString()
	fx.Insert(t, "task_token", testutil.Cols{
		"token_hash": auth.HashToken(token), "task_id": taskID, "agent_id": agentID,
		"workspace_id": testWorkspaceID, "user_id": testUserID, "expires_at": time.Now().Add(time.Hour),
	})
	router := chi.NewRouter()
	router.Use(middleware.Auth(testHandler.Queries, nil, nil, nil))
	router.Use(middleware.RequireWorkspaceMember(testHandler.Queries))
	router.Post("/api/chat/sessions", testHandler.CreateChatSession)
	router.Patch("/api/chat/sessions/{sessionId}", testHandler.UpdateChatSession)
	request := func(method, path string, body any) *http.Request {
		req := testutil.JSONRequest(method, path, body)
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("X-Workspace-ID", testWorkspaceID)
		req.Header.Set("X-Actor-Source", "member")
		return req
	}
	for _, config := range []any{map[string]any{"approval_policy": "full-access"}, map[string]any{"approval_policy": nil}, nil} {
		testutil.Call(t, router.ServeHTTP, request(http.MethodPatch, "/api/chat/sessions/"+sessionID,
			map[string]any{"execution_config": config})).Want(http.StatusForbidden)
	}
	var stored []byte
	fx.QueryRow(t, `SELECT execution_config FROM chat_session WHERE id = $1`, sessionID).Scan(&stored)
	var overrides struct {
		Model          string `json:"model"`
		ApprovalPolicy string `json:"approval_policy"`
	}
	if err := json.Unmarshal(stored, &overrides); err != nil || overrides.Model != "gpt-session" || overrides.ApprovalPolicy != "on-request" {
		t.Fatalf("denied machine policy edits changed storage: %s, %v", stored, err)
	}
	for _, policy := range []any{"full-access", nil} {
		testutil.Call(t, router.ServeHTTP, request(http.MethodPost, "/api/chat/sessions", map[string]any{
			"agent_id": agentID, "execution_config": map[string]any{"approval_policy": policy},
		})).Want(http.StatusForbidden)
	}
	var updated ChatSessionResponse
	testutil.Call(t, router.ServeHTTP, request(http.MethodPatch, "/api/chat/sessions/"+sessionID,
		map[string]any{"execution_config": map[string]any{"model": "gpt-machine"}})).Want(http.StatusOK).JSON(&updated)
	if updated.EffectiveExecutionConfig.Model != "gpt-machine" || updated.EffectiveExecutionConfig.ApprovalPolicy != "on-request" {
		t.Fatalf("machine model edit changed approval policy: %+v", updated.EffectiveExecutionConfig)
	}
	for _, body := range []map[string]any{
		{"agent_id": agentID},
		{"agent_id": agentID, "execution_config": nil},
		{"agent_id": agentID, "execution_config": map[string]any{"model": "gpt-new"}},
	} {
		var created ChatSessionResponse
		testutil.Call(t, router.ServeHTTP, request(http.MethodPost, "/api/chat/sessions", body)).Want(http.StatusCreated).JSON(&created)
		fx.Cleanup(t, `DELETE FROM chat_session WHERE id = $1`, created.ID)
		if created.EffectiveExecutionConfig.ApprovalPolicy != "" {
			t.Fatalf("machine default creation changed approval policy: %+v", created.EffectiveExecutionConfig)
		}
	}
}
