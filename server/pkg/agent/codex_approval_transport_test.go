package agent

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"strings"
	"testing"
	"time"
)

// approvalTestTransport observes delivered JSONL or refuses a native response.
// Tests inspect its buffer only after the native worker has exited.
type approvalTestTransport struct {
	bytes.Buffer
	failure error
}

// Write models the native-delivery gate without resolving an installed CLI.
func (w *approvalTestTransport) Write(data []byte) (int, error) {
	if w.failure != nil {
		return 0, w.failure
	}
	return w.Buffer.Write(data)
}

// approvalTransportClient assembles the real protocol dispatch and wait owner.
func approvalTransportClient(t *testing.T, handler ApprovalHandler, writer *approvalTestTransport) *codexClient {
	t.Helper()
	client := &codexClient{
		cfg:   Config{Logger: slog.New(slog.NewTextHandler(io.Discard, nil))},
		stdin: writer, pending: make(map[int]*pendingRPC), processDone: make(chan struct{}),
		approvals: newCodexApprovalState(context.Background(), ExecOptions{ApprovalPolicy: "on-request", ApprovalHandler: handler, Cwd: t.TempDir()}),
	}
	t.Cleanup(client.approvals.stopAndWait)
	return client
}

// joinApprovalWorkers bounds test diagnostics while preserving the actual join.
func joinApprovalWorkers(t *testing.T, client *codexClient) {
	t.Helper()
	done := make(chan struct{})
	go func() { client.approvals.workers.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("native approval worker did not exit")
	}
}

// TestCodexOnRequestApprovalNativeWriteGate settles only delivered decisions
// as processed; write failure must expire the receipt and release pending.
func TestCodexOnRequestApprovalNativeWriteGate(t *testing.T) {
	for _, fail := range []bool{false, true} {
		name := "delivered"
		if fail {
			name = "write_failed"
		}
		t.Run(name, func(t *testing.T) {
			h := newApprovalTestHandler()
			writer := &approvalTestTransport{}
			if fail {
				writer.failure = io.ErrClosedPipe
			}
			client := approvalTransportClient(t, h, writer)
			client.handleLine(`{"id":"native-write","method":"item/commandExecution/requestApproval","params":{"threadId":"thread","turnId":"turn","itemId":"item"}}`)
			select {
			case <-h.requests:
			case <-time.After(time.Second):
				t.Fatal("request was not registered")
			}
			h.decisions <- ApprovalApprove
			joinApprovalWorkers(t, client)
			if len(h.acks) != 1 || client.approvals.count() != 0 {
				t.Fatalf("ACKs=%d pending=%d, want one settled receipt", len(h.acks), client.approvals.count())
			}
			wantStatus := ApprovalProcessed
			if fail {
				wantStatus = ApprovalExpired
			}
			if ack := <-h.acks; ack.Status != wantStatus {
				t.Fatalf("receipt status=%s, want %s", ack.Status, wantStatus)
			}
			if fail && !strings.Contains(client.getTurnError(), "native approval response failed") {
				t.Fatalf("delivery failure disappeared: %s", client.getTurnError())
			}
		})
	}
}

// approvalErrorHandler represents a failed parent lookup returning a stale grant.
type approvalErrorHandler struct{ *approvalTestHandler }

// RequestApproval makes the error authoritative over the stale decision.
func (h approvalErrorHandler) RequestApproval(context.Context, ApprovalRequest) (ApprovalDecision, error) {
	return ApprovalApprove, io.ErrUnexpectedEOF
}

// TestCodexOnRequestApprovalHandlerFailureRefusesAction protects both failure
// branches: a failed parent lookup and a decision outside the single-action enum.
func TestCodexOnRequestApprovalHandlerFailureRefusesAction(t *testing.T) {
	for _, lookupFailure := range []bool{false, true} {
		name := "unknown_decision"
		if lookupFailure {
			name = "lookup_failed"
		}
		t.Run(name, func(t *testing.T) {
			h := newApprovalTestHandler()
			var handler ApprovalHandler = h
			if lookupFailure {
				handler = approvalErrorHandler{h}
			} else {
				h.decisions <- ApprovalDecision("approve_for_session")
			}
			writer := &approvalTestTransport{}
			client := approvalTransportClient(t, handler, writer)
			client.handleLine(`{"id":"handler-failed","method":"item/commandExecution/requestApproval","params":{"threadId":"thread","turnId":"turn","itemId":"item"}}`)
			joinApprovalWorkers(t, client)
			var reply struct{ Result struct{ Decision string } }
			if err := json.Unmarshal(writer.Bytes(), &reply); err != nil {
				t.Fatal(err)
			}
			if reply.Result.Decision != "decline" || client.getTurnError() == "" {
				t.Fatalf("parent failure granted native access: reply=%+v error=%q", reply, client.getTurnError())
			}
			if len(h.acks) != 1 || client.approvals.count() != 0 {
				t.Fatal("failed lookup did not settle its receipt and release pending")
			}
			if ack := <-h.acks; ack.Status != ApprovalExpired {
				t.Fatalf("automatic refusal receipt=%s, want expired", ack.Status)
			}
		})
	}
}
