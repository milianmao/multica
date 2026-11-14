package agent

import (
	"context"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strconv"
	"sync/atomic"
	"testing"
	"time"
)

// TestCodexApprovalCancelUnblocksNativePipeWrite exercises an interrupt waiting
// behind a native write. Removing write-time cancellation leaves it holding up cleanup.
func TestCodexApprovalCancelUnblocksNativePipeWrite(t *testing.T) {
	runCodexBlockedApprovalIO(t, false)
}

// TestCodexApprovalCleanupUnblocksNativePipeWrite reaches cleanup without an
// interrupt. Joining native workers before closing stdin deadlocks this path.
func TestCodexApprovalCleanupUnblocksNativePipeWrite(t *testing.T) {
	runCodexBlockedApprovalIO(t, true)
}

// runCodexBlockedApprovalIO uses the actual backend, StdinPipe and native worker;
// the subprocess confirms a response entered its pipe before it stops reading.
func runCodexBlockedApprovalIO(t *testing.T, complete bool) {
	t.Helper()
	grace := codexGracefulShutdownTimeoutNanos.Swap(int64(100 * time.Millisecond))
	waitDelay := codexProcessWaitDelayNanos.Swap(int64(100 * time.Millisecond))
	t.Cleanup(func() {
		codexGracefulShutdownTimeoutNanos.Store(grace)
		codexProcessWaitDelayNanos.Store(waitDelay)
	})

	cfg := approvalFixtureConfig(t, "blocked-io")
	cfg.LaunchPrefix = []string{"-test.run=^TestCodexApprovalBlockedIOFakeProcess$", "--"}
	cfg.Logger = slog.New(slog.NewTextHandler(io.Discard, nil))
	dir := cfg.Env["CODEX_HOME"]
	pidFile := filepath.Join(dir, "blocked-io-pid")
	readyFile := filepath.Join(dir, "blocked-io-writing")
	finishFile := filepath.Join(dir, "blocked-io-complete")
	cfg.Env["MULTICA_TEST_CODEX_BLOCKED_IO_PID"] = pidFile
	cfg.Env["MULTICA_TEST_CODEX_BLOCKED_IO_READY"] = readyFile
	cfg.Env["MULTICA_TEST_CODEX_BLOCKED_IO_COMPLETE"] = finishFile

	h := newApprovalTestHandler()
	var pending atomic.Int64
	opts := approvalFixtureOptions(t, h, &pending)
	opts.Timeout, opts.HandshakeTimeout = testFallbackTimeout, testFallbackTimeout
	opts.SemanticInactivityTimeout, opts.FirstTurnNoProgressTimeout = testFallbackTimeout, testFallbackTimeout
	opts.TurnInterruptTimeout = 80 * time.Millisecond
	ctx, cancel := context.WithCancel(context.Background())
	session := startApprovalFixture(t, ctx, cfg, opts)
	messagesDone := make(chan struct{})
	go func() {
		for range session.Messages {
		}
		close(messagesDone)
	}()
	// Even the red run joins its backend after killing only its own fake process.
	t.Cleanup(func() {
		cancel()
		if data, err := os.ReadFile(pidFile); err == nil {
			if pid, err := strconv.Atoi(string(data)); err == nil {
				if process, err := os.FindProcess(pid); err == nil {
					_ = process.Kill()
				}
			}
		}
		select {
		case <-session.Result:
		case <-time.After(5 * time.Second):
			t.Error("终止 fake 进程后 backend 仍未退出")
		}
		select {
		case <-messagesDone:
		case <-time.After(5 * time.Second):
			t.Error("fake backend 的 message goroutine 未退出")
		}
		if got := pending.Load(); got != 0 {
			t.Errorf("native worker 未释放：pending=%d", got)
		}
	})

	select {
	case <-h.requests:
	case result := <-session.Result:
		t.Fatalf("尚未进入 native approval：status=%s error=%s", result.Status, result.Error)
	case <-time.After(testFallbackTimeout):
		t.Fatal("fake 未注册 native approval")
	}
	h.decisions <- ApprovalApprove
	waitForTestFile(t, readyFile)
	if got := pending.Load(); got != 1 {
		t.Fatalf("首字节已读后应仍有阻塞的 native worker：pending=%d", got)
	}

	started := time.Now()
	if complete {
		if err := os.WriteFile(finishFile, nil, 0o600); err != nil {
			t.Fatal(err)
		}
	} else {
		cancel()
	}
	select {
	case result := <-session.Result:
		if !complete && result.Status != "aborted" {
			t.Fatalf("取消结果应为 aborted：status=%s error=%s", result.Status, result.Error)
		}
		t.Logf("真实 stdin pipe：interrupt budget=%s，返回耗时=%s，status=%s，pending=%d",
			opts.TurnInterruptTimeout, time.Since(started).Round(time.Millisecond), result.Status, pending.Load())
	case <-time.After(time.Second):
		t.Fatalf("native response 已开始写入但 CLI 停读，complete=%t 后 interrupt/cleanup 超过 1s", complete)
	}
	if got := pending.Load(); got != 0 {
		t.Fatalf("结果返回后仍有 native worker：pending=%d", got)
	}
	select {
	case <-messagesDone:
	case <-time.After(time.Second):
		t.Fatal("结果返回后 message goroutine 未退出")
	}
}
