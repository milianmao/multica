package daemon

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/multica-ai/multica/server/pkg/agent"
)

// lostApprovalRegistrationBackend exits when a committed approval loses its HTTP response.
type lostApprovalRegistrationBackend struct{}

// Execute uses the injected transport handler rather than reproducing its registration logic.
func (lostApprovalRegistrationBackend) Execute(ctx context.Context, _ string, opts agent.ExecOptions) (*agent.Session, error) {
	_, err := opts.ApprovalHandler.RequestApproval(ctx, agent.ApprovalRequest{ID: "1", Kind: "command", Method: "item/commandExecution/requestApproval", Params: json.RawMessage("{}")})
	return nil, err
}

// TestNativeApprovalExitExpiresLostRegistration invalidates server records even when the client never received their ID.
func TestNativeApprovalExitExpiresLostRegistration(t *testing.T) {
	var scopeMu sync.Mutex
	var registeredExecution, expiredExecution string
	var expired atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			ExecutionID string `json:"execution_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Error(err)
		}
		if strings.HasSuffix(r.URL.Path, "/expire") {
			scopeMu.Lock()
			expiredExecution = body.ExecutionID
			scopeMu.Unlock()
			expired.Add(1)
			w.WriteHeader(http.StatusNoContent)
			return
		}
		scopeMu.Lock()
		registeredExecution = body.ExecutionID
		scopeMu.Unlock()
		http.Error(w, "response lost after registration", http.StatusBadGateway)
	}))
	defer server.Close()
	d := newTestDaemon(t)
	d.client = NewClient(server.URL)
	handler := newTaskApprovalHandler(d.client, Task{ID: "task-1", RuntimeID: "runtime-1", DispatchedAt: "2026-11-14T00:00:00Z"})
	_, _, err := d.executeAndDrain(context.Background(), lostApprovalRegistrationBackend{}, "test", agent.ExecOptions{ApprovalHandler: handler}, slog.Default(), "task-1", "", new(atomic.Int32))
	scopeMu.Lock()
	defer scopeMu.Unlock()
	if err == nil || registeredExecution == "" {
		t.Fatalf("lost response did not reach the real registration path: execution=%q err=%v", registeredExecution, err)
	}
	if expired.Load() != 1 || expiredExecution != registeredExecution {
		t.Fatalf("exited execution kept its pending approval: registered=%q expired=%q calls=%d", registeredExecution, expiredExecution, expired.Load())
	}
}
