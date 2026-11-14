package service

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgtype"
	"github.com/multica-ai/multica/server/internal/util"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

// TestTaskApprovalClaimFence rejects reclaims and terminal callbacks even when the process UUID is valid.
func TestTaskApprovalClaimFence(t *testing.T) {
	runtimeID := util.MustParseUUID("00000000-0000-4000-8000-000000000001")
	dispatchedAt := pgtype.Timestamptz{Time: time.Date(2026, time.November, 14, 17, 35, 24, 123456000, time.UTC), Valid: true}
	task := db.AgentTaskQueue{RuntimeID: runtimeID, Attempt: 1, DispatchedAt: dispatchedAt, Status: "running",
		Context: []byte(`{"execution_config":{"approval_policy":"on-request"}}`),
	}
	execution := TaskApprovalExecution{
		RuntimeID: runtimeID, DispatchedAt: dispatchedAt,
		ExecutionID: util.MustParseUUID("00000000-0000-4000-8000-000000000002"),
	}
	if !taskApprovalExecutionMatches(task, task, execution) {
		t.Fatal("current claim rejected")
	}
	authorized := task
	task.DispatchedAt.Time = task.DispatchedAt.Time.Add(time.Microsecond)
	if taskApprovalExecutionMatches(task, authorized, execution) {
		t.Fatal("reclaim accepted an old generation")
	}
	task = authorized
	task.RuntimeID = util.MustParseUUID("00000000-0000-4000-8000-000000000003")
	if taskApprovalExecutionMatches(task, authorized, execution) {
		t.Fatal("runtime rebind accepted an old authorization snapshot")
	}
	task = authorized
	task.Status = "cancelled"
	if taskApprovalExecutionMatches(task, authorized, execution) || !taskApprovalClaimMatches(task, authorized, execution) {
		t.Fatal("terminal claim must reject decisions but still allow expiration")
	}
}

// TestNativeApprovalRegistration keeps opaque IDs and JSON numbers distinct without float rounding.
func TestNativeApprovalRegistration(t *testing.T) {
	req := TaskApprovalRegistration{
		NativeRequestID: " 9007199254740993 ", Kind: "file_change", Method: "item/fileChange/requestApproval",
		Params: json.RawMessage(`{"z":9007199254740993,"patch":"a\u0000b","a":1}`),
	}
	if err := req.validate(); err != nil {
		t.Fatal(err)
	}
	if req.NativeRequestID != "9007199254740993" {
		t.Fatalf("native id lost precision: %s", req.NativeRequestID)
	}
	var params struct {
		Z     json.Number `json:"z"`
		Patch string      `json:"patch"`
	}
	if err := json.Unmarshal(req.Params, &params); err != nil {
		t.Fatal(err)
	}
	if params.Z.String() != "9007199254740993" || params.Patch != "a\x00b" {
		t.Fatalf("params changed: %+v", params)
	}
	numeric := req.NativeRequestID
	req.NativeRequestID = `"9007199254740993"`
	if err := req.validate(); err != nil || req.NativeRequestID == numeric {
		t.Fatalf("string id collapsed into numeric id: %q, %v", req.NativeRequestID, err)
	}
}

// TestNativeApprovalRegistrationRejectsWrongProtocol prevents a kind label from authorizing an unrelated protocol.
func TestNativeApprovalRegistrationRejectsWrongProtocol(t *testing.T) {
	req := TaskApprovalRegistration{
		NativeRequestID: "1", Kind: "command", Method: "mcpServer/elicitation/request", Params: json.RawMessage(`{}`),
	}
	if err := req.validate(); !errors.Is(err, ErrTaskApprovalInvalid) {
		t.Fatalf("unsupported method accepted: %v", err)
	}
	req.Method = "item/fileChange/requestApproval"
	if err := req.validate(); !errors.Is(err, ErrTaskApprovalInvalid) {
		t.Fatalf("mismatched kind accepted: %v", err)
	}
	req.Kind = "file_change"
	req.NativeRequestID = `{"id":1}`
	if err := req.validate(); !errors.Is(err, ErrTaskApprovalInvalid) {
		t.Fatalf("non-scalar native id accepted: %v", err)
	}
}

// TestNativeApprovalRegistrationRedactsParams covers persisted nested diffs and keeps permission scopes intact.
func TestNativeApprovalRegistrationRedactsParams(t *testing.T) {
	secret := "fixture-" + "credential"
	raw, err := json.Marshal(map[string]any{
		"api_key":     secret,
		"command":     "export TOKEN=" + secret,
		"changes":     []any{map[string]any{"path": ".env", "diff": "+DB_PASSWORD=" + secret + "\n"}},
		"permissions": map[string]any{"network": map[string]any{"enabled": true}, "fileSystem": map[string]any{"read": []string{"E:/work/repo"}}},
	})
	if err != nil {
		t.Fatal(err)
	}
	req := TaskApprovalRegistration{NativeRequestID: "1", Kind: "permissions", Method: "item/permissions/requestApproval", Params: raw}
	if err := req.validate(); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(req.Params), secret) {
		t.Fatal("native parameters retained a credential")
	}
	var params struct {
		Command string `json:"command"`
		Changes []struct {
			Diff string `json:"diff"`
		} `json:"changes"`
		Permissions struct {
			Network struct {
				Enabled bool `json:"enabled"`
			} `json:"network"`
			FileSystem struct {
				Read []string `json:"read"`
			} `json:"fileSystem"`
		} `json:"permissions"`
	}
	if err := json.Unmarshal(req.Params, &params); err != nil {
		t.Fatal(err)
	}
	if params.Command != "export [REDACTED CREDENTIAL]" || len(params.Changes) != 1 || params.Changes[0].Diff != "+[REDACTED CREDENTIAL]\n" {
		t.Fatalf("nested display parameters were not scrubbed: %+v", params)
	}
	if !params.Permissions.Network.Enabled || len(params.Permissions.FileSystem.Read) != 1 || params.Permissions.FileSystem.Read[0] != "E:/work/repo" {
		t.Fatal("permission scope changed during display redaction")
	}
}

// TestTaskApprovalRequiresOnRequest rejects legacy and full-access tasks without changing their policy.
func TestTaskApprovalRequiresOnRequest(t *testing.T) {
	task := db.AgentTaskQueue{Context: []byte(`{"execution_config":{"approval_policy":"on-request"}}`)}
	if !taskApprovalEnabled(task) {
		t.Fatal("on-request task not enabled")
	}
	task.Context = nil
	if taskApprovalEnabled(task) {
		t.Fatal("legacy issue task opted into native approval")
	}
	task.Context = []byte(`{"execution_config":{"approval_policy":"full-access"}}`)
	if taskApprovalEnabled(task) {
		t.Fatal("full-access task opted into native approval")
	}
	task.Context = []byte(`{"execution_config":{"approval_policy":17}}`)
	if taskApprovalEnabled(task) {
		t.Fatal("malformed frozen task policy accepted")
	}
}
