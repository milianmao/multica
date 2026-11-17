package handler

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// TestChatApprovalConfigHumanGuard owns the field-presence and reset boundaries, not database behavior.
func TestChatApprovalConfigHumanGuard(t *testing.T) {
	owner := "00000000-0000-4000-8000-000000000001"
	machine := httptest.NewRequest(http.MethodPatch, "/api/chat/sessions/session", nil)
	machine.Header.Set("X-User-ID", owner)
	machine.Header.Set("X-Actor-Source", "task_token")
	for _, tc := range []struct {
		name   string
		config string
		update bool
	}{
		{"update policy", `{"approval_policy":"full-access"}`, true},
		{"clear policy", `{"approval_policy":null}`, true},
		{"reset session", `null`, true},
		{"create policy", `{"approval_policy":"on-request"}`, false},
		{"create inherited policy", `{"approval_policy":null}`, false},
	} {
		rec := httptest.NewRecorder()
		if requireHumanChatApprovalConfig(rec, machine, json.RawMessage(tc.config), tc.update) || rec.Code != http.StatusForbidden {
			t.Errorf("%s: owner-bound machine accepted, HTTP %d, body %s", tc.name, rec.Code, rec.Body.String())
		}
	}
	for _, tc := range []struct {
		name   string
		config string
		update bool
	}{
		{"model overrides", `{"model":"gpt-machine","thinking_level":"high","service_tier":"priority"}`, true},
		{"default create", "", false},
		{"null create", `null`, false},
	} {
		rec := httptest.NewRecorder()
		if !requireHumanChatApprovalConfig(rec, machine, json.RawMessage(tc.config), tc.update) {
			t.Errorf("%s: ordinary machine configuration rejected, HTTP %d", tc.name, rec.Code)
		}
	}
	human := machine.Clone(machine.Context())
	human.Header.Del("X-Actor-Source")
	for _, config := range []string{`{"approval_policy":"full-access"}`, `null`} {
		rec := httptest.NewRecorder()
		if !requireHumanChatApprovalConfig(rec, human, json.RawMessage(config), true) {
			t.Errorf("human owner rejected for %s, HTTP %d", config, rec.Code)
		}
	}
}
