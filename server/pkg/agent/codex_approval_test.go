package agent

import (
	"bytes"
	"context"
	"encoding/json"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// approvalTestReceipt observes receipt status and context at the parent boundary.
type approvalTestReceipt struct {
	ApprovalRequest
	Status     ApprovalAcknowledgement
	ContextErr error
	Deadline   time.Time
}

// approvalTestHandler exposes the parent-owned registration, decision and ACK
// boundaries without introducing a second polling implementation in the test.
type approvalTestHandler struct {
	requests  chan ApprovalRequest
	decisions chan ApprovalDecision
	acks      chan approvalTestReceipt
	cancelled chan ApprovalRequest
	ackError  error
}

// newApprovalTestHandler exposes request, reply and cancellation boundaries.
func newApprovalTestHandler() *approvalTestHandler {
	return &approvalTestHandler{
		requests: make(chan ApprovalRequest, 8), decisions: make(chan ApprovalDecision, 8),
		acks: make(chan approvalTestReceipt, 8), cancelled: make(chan ApprovalRequest, 8),
	}
}

// RequestApproval blocks only its native worker, never the transport reader.
func (h *approvalTestHandler) RequestApproval(ctx context.Context, request ApprovalRequest) (ApprovalDecision, error) {
	h.requests <- request
	select {
	case decision := <-h.decisions:
		return decision, nil
	case <-ctx.Done():
		h.cancelled <- request
		return "", ctx.Err()
	}
}

// AcknowledgeApproval captures the exact context even when delivery fails.
func (h *approvalTestHandler) AcknowledgeApproval(ctx context.Context, request ApprovalRequest, status ApprovalAcknowledgement) error {
	deadline, _ := ctx.Deadline()
	h.acks <- approvalTestReceipt{request, status, ctx.Err(), deadline}
	if err := ctx.Err(); err != nil {
		return err
	}
	return h.ackError
}

func approvalFixtureOptions(t *testing.T, h ApprovalHandler, pending *atomic.Int64) ExecOptions {
	t.Helper()
	return ExecOptions{
		Cwd: t.TempDir(), ApprovalPolicy: "on-request", ApprovalHandler: h,
		OnApprovalPending: func(delta int) { pending.Add(int64(delta)) },
		Timeout:           4 * time.Second, HandshakeTimeout: time.Second,
		SemanticInactivityTimeout:  80 * time.Millisecond,
		FirstTurnNoProgressTimeout: 70 * time.Millisecond,
		TurnInterruptTimeout:       200 * time.Millisecond,
	}
}

func startApprovalFixture(t *testing.T, ctx context.Context, cfg Config, opts ExecOptions) *Session {
	t.Helper()
	backend, err := New("codex", cfg)
	if err != nil {
		t.Fatal(err)
	}
	session, err := backend.Execute(ctx, "approval fixture", opts)
	if err != nil {
		t.Fatal(err)
	}
	return session
}

func nextFixtureApproval(t *testing.T, h *approvalTestHandler, session *Session) ApprovalRequest {
	t.Helper()
	select {
	case request := <-h.requests:
		return request
	case result := <-session.Result:
		t.Fatalf("execution ended before requesting approval: %+v", result)
	case <-time.After(3 * time.Second):
		t.Fatal("native approval was not registered")
	}
	return ApprovalRequest{}
}

func fixtureApprovalResult(t *testing.T, session *Session) Result {
	t.Helper()
	select {
	case result := <-session.Result:
		return result
	case <-time.After(5 * time.Second):
		t.Fatal("execution did not release its approval wait")
	}
	return Result{}
}

// TestCodexOnRequestApprovalExchange covers the native wire, reader liveness,
// single-action decisions and semantic-inactivity pause in one real assembly.
func TestCodexOnRequestApprovalExchange(t *testing.T) {
	cfg := approvalFixtureConfig(t, "exchange")
	h := newApprovalTestHandler()
	var pending atomic.Int64
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	opts := approvalFixtureOptions(t, h, &pending)
	session := startApprovalFixture(t, ctx, cfg, opts)
	stream := make(chan string, 16)
	go func() {
		for message := range session.Messages {
			if message.Type == MessageText {
				stream <- message.Content
			}
		}
		close(stream)
	}()
	ids := []string{`"command-one"`, "900719925474099312345", `"permissions-grant"`, `"permissions-deny"`}
	kinds := []string{"command", "file_change", "permissions", "permissions"}
	decisions := []ApprovalDecision{ApprovalApprove, ApprovalDeny, ApprovalApprove, ApprovalDeny}
	var commandRequest ApprovalRequest
	for i, decision := range decisions {
		request := nextFixtureApproval(t, h, session)
		if request.ID != ids[i] || request.Kind != kinds[i] || !json.Valid(request.Params) {
			t.Fatalf("native request = %+v, want id=%s kind=%s", request, ids[i], kinds[i])
		}
		if pending.Load() != 1 {
			t.Fatalf("pending before registration = %d, want 1", pending.Load())
		}
		if i == 0 {
			commandRequest = request
			select {
			case text := <-stream:
				if text != "reader kept streaming" {
					t.Fatalf("reader text = %q", text)
				}
			case <-time.After(time.Second):
				t.Fatal("stdout reader blocked on human approval")
			}
			time.Sleep(240 * time.Millisecond)
		}
		if i == 1 {
			var display struct {
				Cwd     string
				Changes []struct{ Path, Diff string }
			}
			if err := json.Unmarshal(request.Params, &display); err != nil {
				t.Fatal(err)
			}
			if display.Cwd != opts.Cwd || len(display.Changes) != 1 || display.Changes[0].Path != filepath.Join(opts.Cwd, "approval.txt") || display.Changes[0].Diff != codexApprovalFixtureDiff {
				t.Fatalf("file approval display lost its native item diff: %s", request.Params)
			}
			var command struct{ Command, Cwd string }
			if err := json.Unmarshal(commandRequest.Params, &command); err != nil {
				t.Fatal(err)
			}
			if command.Command != codexApprovalFixtureCommand || command.Cwd != opts.Cwd {
				t.Fatalf("nullable command display lost its native item: %s", commandRequest.Params)
			}
		}
		if i >= 2 {
			var display struct{ Scope string }
			if err := json.Unmarshal(request.Params, &display); err != nil {
				t.Fatal(err)
			}
			if display.Scope != "turn" {
				t.Fatalf("permission display scope = %q", display.Scope)
			}
			// A display redactor must not alter the native permission grant.
			copy(request.Params, bytes.ReplaceAll(request.Params, []byte(`"enabled":true`), []byte(`"enabled":null`)))
		}
		h.decisions <- decision
		select {
		case ack := <-h.acks:
			if ack.ID != request.ID || ack.Status != ApprovalProcessed {
				t.Fatalf("ACK id=%s status=%s, want %s processed", ack.ID, ack.Status, request.ID)
			}
		case <-time.After(time.Second):
			t.Fatal("native reply was not acknowledged")
		}
	}
	result := fixtureApprovalResult(t, session)
	if result.Status != "completed" || pending.Load() != 0 {
		t.Fatalf("result=%+v pending=%d", result, pending.Load())
	}
	var replies []map[string]json.RawMessage
	for _, message := range approvalFixtureWire(t, cfg) {
		if _, ok := message["method"]; !ok {
			replies = append(replies, message)
		}
	}
	if len(replies) != len(ids) {
		t.Fatalf("native replies = %d, want %d", len(replies), len(ids))
	}
	for i, reply := range replies {
		if string(reply["id"]) != ids[i] {
			t.Fatalf("reply id = %s, want %s", reply["id"], ids[i])
		}
		var body map[string]json.RawMessage
		if err := json.Unmarshal(reply["result"], &body); err != nil {
			t.Fatal(err)
		}
		if i < 2 {
			want := `"accept"`
			if i == 1 {
				want = `"decline"`
			}
			if string(body["decision"]) != want {
				t.Fatalf("single-action reply = %s, want %s", reply["result"], want)
			}
		} else {
			if string(body["scope"]) != `"turn"` {
				t.Fatalf("permission scope = %s", body["scope"])
			}
			var granted map[string]json.RawMessage
			if err := json.Unmarshal(body["permissions"], &granted); err != nil {
				t.Fatal(err)
			}
			if (i == 2 && len(granted) != 2) || (i == 3 && len(granted) != 0) {
				t.Fatalf("permission reply = %s", reply["result"])
			}
			if i == 2 {
				var network struct{ Enabled bool }
				if err := json.Unmarshal(granted["network"], &network); err != nil || !network.Enabled {
					t.Fatalf("display redaction changed native permissions: %s", reply["result"])
				}
			}
		}
	}
}

// TestCodexOnRequestApprovalStopsWaiting verifies that first-turn inactivity is
// paused, but cancellation, total timeout and process exit still release waits.
func TestCodexOnRequestApprovalStopsWaiting(t *testing.T) {
	for _, scenario := range []string{"cancel", "timeout", "exit"} {
		t.Run(scenario, func(t *testing.T) {
			mode := "quiet"
			if scenario == "exit" {
				mode = "exit"
			}
			cfg := approvalFixtureConfig(t, mode)
			h := newApprovalTestHandler()
			var pending atomic.Int64
			opts := approvalFixtureOptions(t, h, &pending)
			if scenario == "timeout" {
				// Allow cold startup to reach a pending native approval before expiring.
				opts.Timeout = 2 * time.Second
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			session := startApprovalFixture(t, ctx, cfg, opts)
			go func() {
				for range session.Messages {
				}
			}()
			request := nextFixtureApproval(t, h, session)
			if scenario == "cancel" {
				time.Sleep(220 * time.Millisecond)
				cancel()
			}
			result := fixtureApprovalResult(t, session)
			want := map[string]string{"cancel": "aborted", "timeout": "timeout", "exit": "failed"}[scenario]
			if result.Status != want || strings.Contains(result.Error, "no_progress") || pending.Load() != 0 {
				t.Fatalf("result=%+v, want %s; pending=%d", result, want, pending.Load())
			}
			select {
			case cancelled := <-h.cancelled:
				if cancelled.ID != request.ID {
					t.Fatalf("cancelled %s, want %s", cancelled.ID, request.ID)
				}
			case <-time.After(time.Second):
				t.Fatal("approval handler context did not cancel")
			}
			select {
			case ack := <-h.acks:
				if ack.ID != request.ID || ack.Status != ApprovalExpired || ack.ContextErr != nil || ack.Deadline.IsZero() {
					t.Fatalf("cancelled request receipt = %+v, want expired with an independent bounded context", ack)
				}
			default:
				t.Fatal("cancelled request did not expire its receipt")
			}
		})
	}
}
