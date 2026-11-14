package agent

import (
	"context"
	"encoding/json"
	"io"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// TestCodexOnRequestApprovalNativeResolution checks that a native-cleared wait
// expires while execution is still running, without replying to the cleared ID.
// Removing resolved cancellation or the deferred receipt makes this fail.
func TestCodexOnRequestApprovalNativeResolution(t *testing.T) {
	cfg := approvalFixtureConfig(t, "resolved")
	h := newApprovalTestHandler()
	var pending atomic.Int64
	opts := approvalFixtureOptions(t, h, &pending)
	opts.SemanticInactivityTimeout, opts.FirstTurnNoProgressTimeout = 2*time.Second, 2*time.Second
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	session := startApprovalFixture(t, ctx, cfg, opts)
	go func() {
		for range session.Messages {
		}
	}()
	request := nextFixtureApproval(t, h, session)
	select {
	case cancelled := <-h.cancelled:
		if cancelled.ID != request.ID {
			t.Errorf("native cancellation ID=%s, want %s", cancelled.ID, request.ID)
		}
	case <-time.After(time.Second):
		t.Error("native resolution did not cancel the handler")
	}
	select {
	case receipt := <-h.acks:
		if receipt.ID != "900719925474099312345" || receipt.Status != ApprovalExpired || receipt.ContextErr != nil || receipt.Deadline.IsZero() {
			t.Errorf("native resolution receipt=%+v, want one expired receipt with a live bounded context", receipt)
		}
	case <-time.After(time.Second):
		t.Error("native resolution left its receipt pending while execution was running")
	}
	if pending.Load() != 0 {
		t.Errorf("native resolution pending=%d, want 0 before execution ends", pending.Load())
	}
	cancel()
	if result := fixtureApprovalResult(t, session); result.Status != "aborted" {
		t.Fatalf("resolved execution result=%+v, want aborted after client cancellation", result)
	}
	if len(h.acks) != 0 {
		t.Fatal("native resolution generated a second receipt on cleanup")
	}
	for _, message := range approvalFixtureWire(t, cfg) {
		if _, method := message["method"]; !method && string(message["id"]) == request.ID {
			t.Fatalf("cleared native approval received a reply: %s", message["result"])
		}
	}
}

// TestCodexOnRequestApprovalAcknowledgementLoss preserves completed native work
// despite a failed receipt. Restoring ACK setTurnError would fail this real run.
func TestCodexOnRequestApprovalAcknowledgementLoss(t *testing.T) {
	cfg := approvalFixtureConfig(t, "quiet")
	h := newApprovalTestHandler()
	h.ackError = io.ErrUnexpectedEOF
	h.decisions <- ApprovalApprove
	var pending atomic.Int64
	session := startApprovalFixture(t, context.Background(), cfg, approvalFixtureOptions(t, h, &pending))
	go func() {
		for range session.Messages {
		}
	}()
	result := fixtureApprovalResult(t, session)
	if result.Status != "completed" || result.Output != "approval finished" || result.Error != "" || pending.Load() != 0 {
		t.Fatalf("ACK loss changed native completion: result=%+v pending=%d", result, pending.Load())
	}
	if len(h.acks) != 1 {
		t.Fatalf("ACK attempts=%d, want one receipt independent of execution retries", len(h.acks))
	}
	if receipt := <-h.acks; receipt.Status != ApprovalProcessed || receipt.ContextErr != nil || receipt.Deadline.IsZero() {
		t.Fatalf("delivered human decision receipt=%+v, want processed with a live bounded context", receipt)
	}
	turns, replies := 0, 0
	for _, message := range approvalFixtureWire(t, cfg) {
		if string(message["method"]) == `"turn/start"` {
			turns++
		}
		if string(message["id"]) == `"command-one"` {
			var body struct{ Decision string }
			_ = json.Unmarshal(message["result"], &body)
			if body.Decision != "accept" {
				t.Fatalf("human decision was not delivered: %s", message["result"])
			}
			replies++
		}
	}
	if turns != 1 || replies != 1 {
		t.Fatalf("ACK loss replayed work: turn/start=%d native replies=%d", turns, replies)
	}
}

// TestCodexOnRequestApprovalIsSemanticProgress covers permissions without any
// preceding item/text. Removing the native activity signal revives first-turn
// no-progress classification, the startup-only catalog replay prerequisite.
func TestCodexOnRequestApprovalIsSemanticProgress(t *testing.T) {
	cfg := approvalFixtureConfig(t, "permissions-progress")
	h := newApprovalTestHandler()
	h.decisions <- ApprovalApprove
	var pending atomic.Int64
	opts := approvalFixtureOptions(t, h, &pending)
	opts.SemanticInactivityTimeout = 300 * time.Millisecond
	session := startApprovalFixture(t, context.Background(), cfg, opts)
	go func() {
		for range session.Messages {
		}
	}()
	result := fixtureApprovalResult(t, session)
	if result.Status != "timeout" || !strings.Contains(result.Error, CodexSemanticInactivityMarker) || strings.Contains(result.Error, CodexFirstTurnNoProgressMarker) || result.codexStartupRefreshRetrySafe {
		t.Fatalf("native permissions were treated as replayable startup silence: %+v", result)
	}
	if !strings.Contains(result.Error, codexModelCatalogRefreshFailureSignal) || result.Output != "" {
		t.Fatalf("expected catalog failure after permissions without item/text: %+v", result)
	}
	if len(h.requests) != 1 || len(h.acks) != 1 || pending.Load() != 0 {
		t.Fatalf("permissions attempts: requests=%d ACKs=%d pending=%d", len(h.requests), len(h.acks), pending.Load())
	}
	if request := <-h.requests; request.Kind != "permissions" {
		t.Fatalf("expected item-less permissions request, got %+v", request)
	}
	if receipt := <-h.acks; receipt.Status != ApprovalProcessed {
		t.Fatalf("valid delivered permissions receipt=%s, want processed", receipt.Status)
	}
	turns, replies := 0, 0
	for _, message := range approvalFixtureWire(t, cfg) {
		if string(message["method"]) == `"turn/start"` {
			turns++
		}
		if string(message["id"]) == `"permissions-progress"` {
			replies++
		}
	}
	if turns != 1 || replies != 1 {
		t.Fatalf("native permissions were replayed: turn/start=%d native replies=%d", turns, replies)
	}
}
