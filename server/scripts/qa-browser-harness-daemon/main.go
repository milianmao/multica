// Command qa-browser-harness-daemon is the daemon half of the ORIO-7 stage-7
// harness (see scripts/qa-browser-harness.mjs). Not product code.
//
// It serves the real browser planes — the same handlers, guards and routing
// table the daemon mounts — on a loopback port, with an in-memory attachment
// sink that records the exact bytes it was handed. The harness can therefore
// assert "this PNG produced this attachment id and this byte count" against
// production code rather than a stub of it.
//
// Output on stdout: `HARNESS_READY {"port":N,"secret":"...","task_id":"..."}`,
// then nothing until it is killed.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"os"

	"github.com/multica-ai/multica/server/internal/daemon"
)

const (
	harnessTaskID   = "task-harness"
	harnessIssueID  = "issue-harness"
	harnessWorkspce = "ws-harness"
)

func main() {
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	secret := "harness-host-secret-value-long-enough"
	daemon, mux := daemon.NewBrowserHarnessServer(logger, secret)

	var lastAttachment struct {
		ID     string `json:"id"`
		Bytes  int    `json:"bytes"`
		SHA256 string `json:"sha256"`
	}
	daemon.SetBrowserHarnessEvidenceUploader(func(_ context.Context, workspaceID, issueID, filename string, data []byte) (string, error) {
		sum := sha256.Sum256(data)
		lastAttachment.ID = "att_harness_" + hex.EncodeToString(sum[:6])
		lastAttachment.Bytes = len(data)
		lastAttachment.SHA256 = hex.EncodeToString(sum[:])
		logger.Info("harness stored evidence", "workspace", workspaceID, "issue", issueID, "file", filename, "bytes", len(data))
		return lastAttachment.ID, nil
	})
	daemon.RegisterBrowserHarnessTask(harnessTaskID, "full")

	// One extra route only this harness needs: the sink's own record, so the
	// Node side can assert on what the daemon actually stored.
	mux.HandleFunc("/harness/evidence", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(lastAttachment)
	})

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		fmt.Fprintf(os.Stderr, "listen: %v\n", err)
		os.Exit(1)
	}
	port := listener.Addr().(*net.TCPAddr).Port

	payload, _ := json.Marshal(map[string]any{
		"port":      port,
		"secret":    secret,
		"task_id":   harnessTaskID,
		"issue":     harnessIssueID,
		"workspace": harnessWorkspce,
	})
	fmt.Printf("HARNESS_READY %s\n", payload)
	os.Stdout.Sync()

	server := &http.Server{Handler: mux}
	if err := server.Serve(listener); err != nil {
		fmt.Fprintf(os.Stderr, "serve: %v\n", err)
		os.Exit(1)
	}
}
