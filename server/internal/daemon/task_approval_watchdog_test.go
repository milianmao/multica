package daemon

import (
	"context"
	"log/slog"
	"sync/atomic"
	"testing"
	"time"

	"github.com/multica-ai/multica/server/pkg/agent"
)

// pendingApprovalBackend produces no output while a native action awaits a human decision.
type pendingApprovalBackend struct {
	wait    time.Duration
	decided atomic.Bool
}

// Execute exercises the daemon's real inactivity loop rather than a separate timer helper.
func (b *pendingApprovalBackend) Execute(ctx context.Context, _ string, opts agent.ExecOptions) (*agent.Session, error) {
	messages := make(chan agent.Message)
	results := make(chan agent.Result, 1)
	opts.OnApprovalPending(1)
	go func() {
		defer close(messages)
		select {
		case <-time.After(b.wait):
			b.decided.Store(true)
			opts.OnApprovalPending(-1)
		case <-ctx.Done():
			opts.OnApprovalPending(-1)
			results <- agent.Result{Status: "aborted"}
			return
		}
		<-ctx.Done()
		results <- agent.Result{Status: "aborted"}
	}()
	return &agent.Session{Messages: messages, Result: results}, nil
}

// TestNativeApprovalWaitPausesAndResetsIdle proves both the wait exemption and its finite post-decision budget.
func TestNativeApprovalWaitPausesAndResetsIdle(t *testing.T) {
	d := newTestDaemon(t)
	d.cfg.AgentIdleWatchdog = 60 * time.Millisecond
	d.cfg.AgentToolWatchdog = 60 * time.Millisecond
	backend := &pendingApprovalBackend{wait: 350 * time.Millisecond}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	started := time.Now()
	result, _, err := d.executeAndDrain(ctx, backend, "test", agent.ExecOptions{}, slog.Default(), "approval-wait", "", new(atomic.Int32))
	if err != nil || result.Status != "idle_watchdog" || !backend.decided.Load() {
		t.Fatalf("approval wait was interrupted or idle budget never resumed: result=%+v decided=%v err=%v", result, backend.decided.Load(), err)
	}
	if elapsed := time.Since(started); elapsed < backend.wait+d.cfg.AgentIdleWatchdog {
		t.Fatalf("decision did not reset idle budget: %s", elapsed)
	}
}

// TestNativeApprovalWaitStillStops leaves caller cancellation independent of the human wait.
func TestNativeApprovalWaitStillStops(t *testing.T) {
	d := newTestDaemon(t)
	d.cfg.AgentIdleWatchdog = 60 * time.Millisecond
	backend := &pendingApprovalBackend{wait: time.Hour}
	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
	defer cancel()
	result, _, _ := d.executeAndDrain(ctx, backend, "test", agent.ExecOptions{}, slog.Default(), "approval-cancel", "", new(atomic.Int32))
	if backend.decided.Load() || result.Status == "idle_watchdog" {
		t.Fatalf("cancellation was replaced by an approval or idle timeout: %+v", result)
	}
}
