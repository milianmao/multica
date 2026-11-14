package agent

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

const codexApprovalFixtureEnv = "MULTICA_TEST_CODEX_APPROVAL_CASE"
const codexApprovalFixtureDiff = "@@ -1 +1 @@\n-old approval text\n+new approval text\n"
const codexApprovalFixtureCommand = "echo native-command"

// approvalFixtureConfig creates a fake CLI from this test-produced executable;
// neither the fixture nor the backend resolves an installed agent command.
func approvalFixtureConfig(t *testing.T, scenario string) Config {
	t.Helper()
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(self)
	if err != nil {
		t.Fatal(err)
	}
	name := "fake-codex"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	dir := t.TempDir()
	fake := filepath.Join(dir, name)
	if err := os.WriteFile(fake, data, 0o755); err != nil {
		t.Fatal(err)
	}
	return Config{
		ExecutablePath: fake,
		LaunchPrefix:   []string{"-test.run=^TestCodexApprovalFakeProcess$", "--"},
		CodexVersion:   "0.149.0-test",
		Env: map[string]string{
			codexApprovalFixtureEnv:            scenario,
			"CODEX_HOME":                       dir,
			"MULTICA_TEST_CODEX_APPROVAL_WIRE": filepath.Join(dir, "wire.jsonl"),
		},
	}
}

// approvalFixtureWire reads actual requests and native replies observed by the
// fake app-server, preserving JSON IDs rather than coercing them to float64.
func approvalFixtureWire(t *testing.T, cfg Config) []map[string]json.RawMessage {
	t.Helper()
	f, err := os.Open(cfg.Env["MULTICA_TEST_CODEX_APPROVAL_WIRE"])
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var wire []map[string]json.RawMessage
	s := bufio.NewScanner(f)
	for s.Scan() {
		var message map[string]json.RawMessage
		if err := json.Unmarshal(s.Bytes(), &message); err != nil {
			t.Fatal(err)
		}
		wire = append(wire, message)
	}
	if err := s.Err(); err != nil {
		t.Fatal(err)
	}
	return wire
}

// TestCodexApprovalFakeProcess is a subprocess fixture for the real backend
// assembly. Protocol progress continues while a native request is outstanding.
func TestCodexApprovalFakeProcess(t *testing.T) {
	scenario := os.Getenv(codexApprovalFixtureEnv)
	if scenario == "" {
		t.Skip("subprocess fixture")
	}
	for _, arg := range os.Args[1:] {
		if arg == "--version" {
			fmt.Println("codex-cli 0.149.0-test")
			os.Exit(0)
		}
	}
	wire, err := os.Create(os.Getenv("MULTICA_TEST_CODEX_APPROVAL_WIRE"))
	if err != nil {
		os.Exit(2)
	}
	defer wire.Close()
	argv, _ := json.Marshal(os.Args[1:])
	if os.WriteFile(filepath.Join(os.Getenv("CODEX_HOME"), "argv.json"), argv, 0o600) != nil {
		os.Exit(2)
	}
	reviewer := codexFixtureApprovalReviewer()
	encoder := json.NewEncoder(os.Stdout)
	emit := func(message any) {
		if encoder.Encode(message) != nil {
			os.Exit(3)
		}
	}
	notify := func(method string, params any) {
		emit(map[string]any{"method": method, "params": params})
	}
	complete := func(status string) {
		notify("turn/completed", map[string]any{"threadId": "approval-thread", "turn": map[string]any{"id": "approval-turn", "status": status}})
	}
	ids := []json.RawMessage{json.RawMessage(`"command-one"`), json.RawMessage("900719925474099312345"), json.RawMessage(`"permissions-grant"`), json.RawMessage(`"permissions-deny"`)}
	methods := []string{"item/commandExecution/requestApproval", "item/fileChange/requestApproval", "item/permissions/requestApproval", "item/permissions/requestApproval"}
	if scenario == "resolved" {
		ids[0] = ids[1]
	}
	if scenario == "permissions-progress" {
		ids[0], methods[0] = json.RawMessage(`"permissions-progress"`), "item/permissions/requestApproval"
	}
	index := 0
	selection := map[string]any{"model": "native-old-model", "reasoningEffort": "xhigh", "serviceTier": "priority"}
	requestApproval := func() {
		cwd, _ := os.Getwd()
		method := methods[index]
		if scenario == "elicitation" {
			method = "mcpServer/elicitation/request"
		}
		params := map[string]any{"threadId": "approval-thread", "turnId": "approval-turn", "itemId": fmt.Sprint(index), "command": "test-created-command", "cwd": os.Getenv("CODEX_HOME")}
		if scenario == "exchange" && index < 2 {
			item := map[string]any{"id": fmt.Sprint(index), "status": "inProgress"}
			if index == 0 {
				item["type"], item["command"], item["cwd"] = "commandExecution", codexApprovalFixtureCommand, cwd
				params["command"], params["cwd"] = nil, nil
			} else {
				item["type"] = "fileChange"
				item["changes"] = []any{map[string]any{"path": filepath.Join(cwd, "approval.txt"), "kind": map[string]any{"type": "update"}, "diff": codexApprovalFixtureDiff}}
				delete(params, "command")
				delete(params, "cwd")
				params["reason"] = "Review the native patch"
			}
			notify("item/started", map[string]any{"threadId": "approval-thread", "turnId": "approval-turn", "item": item})
		}
		if method == "item/permissions/requestApproval" {
			delete(params, "command")
			params["cwd"], params["environmentId"], params["startedAtMs"] = cwd, nil, time.Now().UnixMilli()
			params["permissions"] = map[string]any{"network": map[string]any{"enabled": true}, "fileSystem": map[string]any{"write": []string{cwd}}}
		}
		emit(map[string]any{"id": ids[index], "method": method, "params": params})
		if scenario == "resolved" {
			notify("serverRequest/resolved", map[string]any{"threadId": "approval-thread", "requestId": ids[index]})
		}
		if scenario == "exchange" && index == 0 {
			notify("item/agentMessage/delta", map[string]any{"threadId": "approval-thread", "turnId": "approval-turn", "itemId": "stream", "delta": "reader kept streaming"})
		}
		if scenario == "exit" {
			time.Sleep(120 * time.Millisecond)
			os.Exit(0)
		}
	}
	decoder := json.NewDecoder(os.Stdin)
	for {
		var message map[string]json.RawMessage
		if decoder.Decode(&message) != nil {
			os.Exit(0)
		}
		encoded, _ := json.Marshal(message)
		_, _ = wire.Write(append(encoded, '\n'))
		var method string
		_ = json.Unmarshal(message["method"], &method)
		if method == "initialized" {
			continue
		}
		var params map[string]json.RawMessage
		_ = json.Unmarshal(message["params"], &params)
		respond := func(result any) { emit(map[string]any{"id": message["id"], "result": result}) }
		switch method {
		case "initialize":
			respond(map[string]any{})
		case "config/read":
			var model any
			if configured := os.Getenv(codexSnapshotFixtureModelEnv); configured != "" {
				model = configured
			}
			respond(map[string]any{"config": map[string]any{"model": model}, "origins": map[string]any{}})
		case "model/list":
			respond(map[string]any{"data": []any{map[string]any{
				"id": "native-default-model", "model": "native-default-model", "displayName": "Native default", "description": "Test model",
				"hidden": false, "isDefault": true, "defaultReasoningEffort": "low",
				"supportedReasoningEfforts": []any{map[string]any{"reasoningEffort": "low", "description": "Test default"}},
			}}, "nextCursor": nil})
		case "windowsSandbox/readiness":
			// Native readiness accepts optional unit params, never an object.
			if p, ok := message["params"]; ok && string(p) != "null" {
				emit(map[string]any{"id": message["id"], "error": map[string]any{"code": -32602, "message": "windowsSandbox/readiness expects optional unit params"}})
				continue
			}
			status := "ready"
			if scenario == "unready" {
				status = "updateRequired"
			}
			respond(map[string]any{"status": status})
		case "thread/start", "thread/resume":
			policy := "on-request"
			sandbox := map[string]any{"type": "workspaceWrite", "networkAccess": true}
			if scenario == "unsupported" {
				policy = "never"
				sandbox["type"] = "dangerFullAccess"
			}
			if scenario == "network-disabled" {
				sandbox["networkAccess"] = false
			}
			_ = json.Unmarshal(params["approvalsReviewer"], &reviewer)
			if scenario == "auto-reviewer" {
				reviewer = "auto_review"
			}
			response := map[string]any{"thread": map[string]any{"id": "approval-thread"}, "approvalPolicy": policy, "approvalsReviewer": reviewer, "sandbox": sandbox, "cwd": params["cwd"]}
			if scenario == "selection" {
				if method == "thread/start" {
					selection = map[string]any{"model": "native-default-model", "reasoningEffort": "low", "serviceTier": nil}
				}
				applyCodexFixtureSelection(selection, params)
				for key, value := range selection {
					response[key] = value
				}
			}
			respond(response)
		case "thread/name/set":
			respond(map[string]any{})
		case "turn/start":
			respond(map[string]any{"turn": map[string]any{"id": "approval-turn"}})
			notify("turn/started", map[string]any{"threadId": "approval-thread", "turn": map[string]any{"id": "approval-turn", "status": "inProgress"}})
			_ = json.Unmarshal(params["approvalsReviewer"], &reviewer)
			if scenario == "human-reviewer" && reviewer != "user" {
				complete("completed")
				continue
			}
			if scenario == "selection" {
				applyCodexFixtureSelection(selection, params)
				text, _ := json.Marshal(selection)
				notify("item/completed", map[string]any{"threadId": "approval-thread", "turnId": "approval-turn", "item": map[string]any{"id": "selection", "type": "agentMessage", "phase": "final_answer", "text": string(text)}})
				complete("completed")
				continue
			}
			requestApproval()
		case "turn/interrupt":
			respond(map[string]any{})
			complete("interrupted")
		case "":
			notify("serverRequest/resolved", map[string]any{"threadId": "approval-thread", "requestId": message["id"]})
			if scenario == "permissions-progress" {
				_, _ = fmt.Fprintln(os.Stderr, codexModelCatalogRefreshFailureSignal+": fixture remains silent after native permissions")
				continue
			}
			if scenario == "elicitation" {
				complete("interrupted")
				continue
			}
			index++
			if index < len(ids) && scenario == "exchange" {
				requestApproval()
				continue
			}
			notify("item/completed", map[string]any{"threadId": "approval-thread", "turnId": "approval-turn", "item": map[string]any{"id": "final", "type": "agentMessage", "phase": "final_answer", "text": "approval finished"}})
			complete("completed")
		default:
			_, _ = io.WriteString(os.Stderr, "unsupported fixture RPC: "+method)
			os.Exit(4)
		}
	}
}
