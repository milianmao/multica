package daemon

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/multica-ai/multica/server/pkg/agent"
)

// TestNativeApprovalClient binds real HTTP registration, decisions and ACKs to one process.
func TestNativeApprovalClient(t *testing.T) {
	var mu sync.Mutex
	var registration map[string]any
	var acknowledgement map[string]any
	expired := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		switch {
		case strings.HasSuffix(r.URL.Path, "/expire"):
			expired = true
			w.WriteHeader(http.StatusNoContent)
		case strings.HasSuffix(r.URL.Path, "/ack"):
			if err := json.NewDecoder(r.Body).Decode(&acknowledgement); err != nil {
				t.Error(err)
			}
			_, _ = w.Write([]byte("{}"))
		case r.Method == http.MethodPost:
			if err := json.NewDecoder(r.Body).Decode(&registration); err != nil {
				t.Error(err)
			}
			_, _ = w.Write([]byte(`{"id":"approval-1","status":"pending"}`))
		default:
			if r.URL.Query().Get("execution_id") != registration["execution_id"] {
				t.Error("poll used another execution")
			}
			if r.URL.Query().Get("dispatched_at") != "2026-11-14T00:00:00.123456Z" {
				t.Error("claim generation precision lost")
			}
			_, _ = w.Write([]byte(`[{"id":"approval-1","status":"decided","decision":"approve"}]`))
		}
	}))
	defer server.Close()
	task := Task{ID: "task-1", RuntimeID: "runtime-1", DispatchedAt: "2026-11-14T00:00:00.123456Z"}
	client := NewClient(server.URL)
	client.SetToken("daemon-token")
	handler := newTaskApprovalHandler(client, task)
	execution := handler.forExecution()
	execution.pollInterval = time.Millisecond
	request := agent.ApprovalRequest{ID: "9007199254740993", Kind: "command", Method: "item/commandExecution/requestApproval", Params: json.RawMessage(`{"command":"echo ready","cwd":"/workspace","api_key":"secret-value"}`)}
	decision, err := execution.RequestApproval(context.Background(), request)
	if err != nil || decision != agent.ApprovalApprove {
		t.Fatalf("decision = %q, %v", decision, err)
	}
	if err := execution.AcknowledgeApproval(context.Background(), request, agent.ApprovalProcessed); err != nil {
		t.Fatal(err)
	}
	if err := execution.expire(context.Background()); err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	defer mu.Unlock()
	if registration["runtime_id"] != task.RuntimeID || registration["native_request_id"] != request.ID {
		t.Fatalf("wrong native identity: %v", registration)
	}
	params := registration["params"].(map[string]any)
	if params["api_key"] == "secret-value" || params["command"] != "echo ready" {
		t.Fatalf("unsafe or missing display data: %v", params)
	}
	if acknowledgement["execution_id"] != registration["execution_id"] || acknowledgement["status"] != "processed" || !expired {
		t.Fatalf("native reply was not acknowledged/expired: %v", acknowledgement)
	}
	if handler.forExecution().executionID == execution.executionID {
		t.Fatal("cold retry reused native request namespace")
	}
}

// TestNativeApprovalClientCancellation stops HTTP polling without approving a pending action.
func TestNativeApprovalClientCancellation(t *testing.T) {
	var acknowledgement map[string]any
	var ackMu sync.Mutex
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if strings.HasSuffix(r.URL.Path, "/ack") {
			ackMu.Lock()
			defer ackMu.Unlock()
			if err := json.NewDecoder(r.Body).Decode(&acknowledgement); err != nil {
				t.Error(err)
			}
			w.WriteHeader(http.StatusNoContent)
		} else if r.Method == http.MethodPost {
			_, _ = w.Write([]byte(`{"id":"approval-1","status":"pending"}`))
		} else {
			_, _ = w.Write([]byte(`[]`))
		}
	}))
	defer server.Close()
	handler := newTaskApprovalHandler(NewClient(server.URL), Task{ID: "task-1", RuntimeID: "runtime-1", DispatchedAt: "2026-11-14T00:00:00Z"}).forExecution()
	handler.pollInterval = time.Millisecond
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()
	request := agent.ApprovalRequest{ID: "1", Kind: "command", Method: "item/commandExecution/requestApproval", Params: json.RawMessage("{}")}
	decision, err := handler.RequestApproval(ctx, request)
	if decision != "" || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("pending cancellation = %q, %v", decision, err)
	}
	if err := handler.AcknowledgeApproval(context.Background(), request, agent.ApprovalExpired); err != nil {
		t.Fatal(err)
	}
	ackMu.Lock()
	defer ackMu.Unlock()
	if acknowledgement["status"] != "expired" || acknowledgement["execution_id"] != handler.executionID || acknowledgement["dispatched_at"] != handler.task.DispatchedAt {
		t.Fatalf("cancelled wait was not invalidated in its original scope: %v", acknowledgement)
	}
}

// TestNativeApprovalClientRejectsExpiredDecisions prevents late replies from restarting an ended execution.
func TestNativeApprovalClientRejectsExpiredDecisions(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.Method == http.MethodPost {
			_, _ = w.Write([]byte(`{"id":"approval-1","status":"expired","decision":"approve"}`))
		}
	}))
	defer server.Close()
	handler := newTaskApprovalHandler(NewClient(server.URL), Task{ID: "task-1"}).forExecution()
	decision, err := handler.RequestApproval(context.Background(), agent.ApprovalRequest{ID: "1", Kind: "command", Method: "item/commandExecution/requestApproval", Params: json.RawMessage("{}")})
	if err == nil || decision != "" {
		t.Fatalf("expired request accepted: %q, %v", decision, err)
	}
}
