package agent

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"
)

// TestCodexApprovalBlockedIOFakeProcess reuses the test-produced CLI executable.
// It reads one response byte, then leaves the native pipe open without draining it.
func TestCodexApprovalBlockedIOFakeProcess(t *testing.T) {
	if os.Getenv(codexApprovalFixtureEnv) != "blocked-io" {
		t.Skip("subprocess fixture")
	}
	for _, arg := range os.Args[1:] {
		if arg == "--version" {
			fmt.Println("codex-cli 0.149.0-test")
			os.Exit(0)
		}
	}
	if err := os.WriteFile(os.Getenv("MULTICA_TEST_CODEX_BLOCKED_IO_PID"), []byte(strconv.Itoa(os.Getpid())), 0o600); err != nil {
		os.Exit(2)
	}
	encoder := json.NewEncoder(os.Stdout)
	emit := func(message any) {
		if encoder.Encode(message) != nil {
			os.Exit(3)
		}
	}
	notify := func(method string, params any) {
		emit(map[string]any{"method": method, "params": params})
	}
	decoder := json.NewDecoder(os.Stdin)
	for {
		var message map[string]json.RawMessage
		if decoder.Decode(&message) != nil {
			os.Exit(0)
		}
		var method string
		_ = json.Unmarshal(message["method"], &method)
		respond := func(result any) { emit(map[string]any{"id": message["id"], "result": result}) }
		switch method {
		case "initialize", "thread/name/set":
			respond(map[string]any{})
		case "initialized":
		case "config/read":
			respond(map[string]any{"config": map[string]any{"model": "native-default-model"}, "origins": map[string]any{}})
		case "model/list":
			respond(map[string]any{"data": []any{map[string]any{
				"id": "native-default-model", "model": "native-default-model", "displayName": "Native default", "description": "Test model",
				"hidden": false, "isDefault": true, "defaultReasoningEffort": "low",
				"supportedReasoningEfforts": []any{map[string]any{"reasoningEffort": "low", "description": "Test default"}},
			}}, "nextCursor": nil})
		case "windowsSandbox/readiness":
			respond(map[string]any{"status": "ready"})
		case "thread/start", "thread/resume":
			var params map[string]json.RawMessage
			_ = json.Unmarshal(message["params"], &params)
			respond(map[string]any{"thread": map[string]any{"id": "blocked-thread"}, "approvalPolicy": "on-request", "approvalsReviewer": params["approvalsReviewer"],
				"sandbox": map[string]any{"type": "workspaceWrite", "networkAccess": true}, "cwd": params["cwd"],
				"model": "native-default-model", "reasoningEffort": "low", "serviceTier": nil})
		case "turn/start":
			respond(map[string]any{"turn": map[string]any{"id": "blocked-turn"}})
			notify("turn/started", map[string]any{"threadId": "blocked-thread", "turn": map[string]any{"id": "blocked-turn", "status": "inProgress"}})
			// Echoing this supported string ID exceeds pipe capacity, not the stdout
			// line limit. A partial response read cannot complete the native Write.
			emit(map[string]any{"id": strings.Repeat("x", 2*1024*1024), "method": "item/commandExecution/requestApproval",
				"params": map[string]any{"threadId": "blocked-thread", "turnId": "blocked-turn", "itemId": "blocked-item"}})
			if _, err := io.ReadFull(os.Stdin, make([]byte, 1)); err != nil {
				os.Exit(4)
			}
			if err := os.WriteFile(os.Getenv("MULTICA_TEST_CODEX_BLOCKED_IO_READY"), nil, 0o600); err != nil {
				os.Exit(5)
			}
			for {
				if _, err := os.Stat(os.Getenv("MULTICA_TEST_CODEX_BLOCKED_IO_COMPLETE")); err == nil {
					notify("turn/completed", map[string]any{"threadId": "blocked-thread", "turn": map[string]any{"id": "blocked-turn", "status": "completed"}})
					break
				}
				time.Sleep(5 * time.Millisecond)
			}
			for {
				time.Sleep(time.Hour)
			}
		default:
			_, _ = io.WriteString(os.Stderr, "unsupported fixture RPC: "+method)
			os.Exit(6)
		}
	}
}
