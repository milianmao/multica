package agent

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
)

// TestCodexOnRequestStrictStartup rejects a native sandbox downgrade instead
// of treating a successful but incompatible app-server handshake as success.
func TestCodexOnRequestStrictStartup(t *testing.T) {
	cases := []string{"unsupported", "network-disabled", "auto-reviewer"}
	if runtime.GOOS == "windows" {
		cases = append(cases, "unready")
	}
	for _, scenario := range cases {
		t.Run(scenario, func(t *testing.T) {
			cfg := approvalFixtureConfig(t, scenario)
			h := newApprovalTestHandler()
			h.decisions <- ApprovalDeny
			var pending atomic.Int64
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			session := startApprovalFixture(t, ctx, cfg, approvalFixtureOptions(t, h, &pending))
			go func() {
				for range session.Messages {
				}
			}()
			result := fixtureApprovalResult(t, session)
			if result.Status != "failed" || !strings.Contains(result.Error, "on-request") {
				t.Fatalf("strict startup = %+v, want actionable on-request failure", result)
			}
			for _, message := range approvalFixtureWire(t, cfg) {
				if string(message["method"]) == `"turn/start"` {
					t.Fatal("strict startup submitted a turn after sandbox rejection")
				}
			}
		})
	}
}

// TestCodexOnRequestRequiresHandler checks the local execution boundary before
// starting any process; unattended approval is never an on-request fallback.
func TestCodexOnRequestRequiresHandler(t *testing.T) {
	cfg := approvalFixtureConfig(t, "quiet")
	backend, err := New("codex", cfg)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	_, err = backend.Execute(ctx, "prompt", ExecOptions{Cwd: t.TempDir(), ApprovalPolicy: "on-request"})
	if err == nil || !strings.Contains(err.Error(), "approval handler") {
		t.Fatalf("missing handler error = %v", err)
	}
}

// TestCodexOnRequestThreadSettings covers start/resume and turn overrides with
// copied auto-review config and conflicting prefix/profile/custom args. Removing
// reviewer ownership would bypass human decisions or leak a conflicting argv.
func TestCodexOnRequestThreadSettings(t *testing.T) {
	for _, resume := range []bool{false, true} {
		name := "start"
		if resume {
			name = "resume"
		}
		t.Run(name, func(t *testing.T) {
			cfg := approvalFixtureConfig(t, "human-reviewer")
			cfg.LaunchPrefix = append(cfg.LaunchPrefix, "-c", `profiles."unattended".approvals_reviewer="auto_review"`)
			copiedConfig := "approvals_reviewer=\"auto_review\"\n[profiles.unattended]\napprovals_reviewer=\"auto_review\"\n"
			if err := os.WriteFile(filepath.Join(cfg.Env["CODEX_HOME"], "config.toml"), []byte(copiedConfig), 0o600); err != nil {
				t.Fatal(err)
			}
			h := newApprovalTestHandler()
			h.decisions <- ApprovalDeny
			var pending atomic.Int64
			opts := approvalFixtureOptions(t, h, &pending)
			opts.ExtraArgs = []string{"-c", `approvals_reviewer="auto_review"`}
			opts.CustomArgs = []string{"--profile=unattended", "--config", `"approvals_reviewer"="auto_review"`, `--config=profiles.unattended.approvals_reviewer="auto_review"`}
			if resume {
				opts.ResumeSessionID = "prior-thread"
			}
			session := startApprovalFixture(t, context.Background(), cfg, opts)
			go func() {
				for range session.Messages {
				}
			}()
			result := fixtureApprovalResult(t, session)
			if result.Status != "completed" {
				t.Fatalf("thread setup = %+v", result)
			}
			if len(h.requests) != 1 || len(h.acks) != 1 || pending.Load() != 0 {
				t.Fatalf("human reviewer bypassed: requests=%d ACKs=%d pending=%d", len(h.requests), len(h.acks), pending.Load())
			}
			reviewerArgs := 0
			for _, arg := range approvalFixtureArgs(t, cfg) {
				if strings.Contains(arg, "approvals_reviewer") {
					reviewerArgs++
					if arg != `approvals_reviewer="user"` {
						t.Fatalf("conflicting reviewer launch argument survived: %s", arg)
					}
				}
			}
			if reviewerArgs != 1 {
				t.Fatalf("managed reviewer launch args=%d, want one user override", reviewerArgs)
			}
			setupFound, turnFound := false, false
			for _, message := range approvalFixtureWire(t, cfg) {
				var method string
				_ = json.Unmarshal(message["method"], &method)
				if method != "thread/"+name && method != "turn/start" {
					continue
				}
				var params map[string]json.RawMessage
				if err := json.Unmarshal(message["params"], &params); err != nil {
					t.Fatal(err)
				}
				if string(params["approvalPolicy"]) != `"on-request"` {
					t.Fatalf("%s approvalPolicy = %s", method, params["approvalPolicy"])
				}
				if string(params["approvalsReviewer"]) != `"user"` {
					t.Fatalf("%s approvalsReviewer = %s, want user", method, params["approvalsReviewer"])
				}
				if method == "thread/"+name {
					setupFound = true
					if string(params["sandbox"]) != `"workspace-write"` {
						t.Fatalf("thread sandbox = %s", params["sandbox"])
					}
					var config map[string]json.RawMessage
					if err := json.Unmarshal(params["config"], &config); err != nil {
						t.Fatal(err)
					}
					if string(config["sandbox_workspace_write.network_access"]) != "true" {
						t.Fatal("thread network access was not enabled")
					}
				} else {
					turnFound = true
					var sandbox struct {
						Type          string
						NetworkAccess bool
						WritableRoots []string
					}
					if err := json.Unmarshal(params["sandboxPolicy"], &sandbox); err != nil {
						t.Fatal(err)
					}
					if sandbox.Type != "workspaceWrite" || !sandbox.NetworkAccess || len(sandbox.WritableRoots) != 1 || sandbox.WritableRoots[0] != opts.Cwd {
						t.Fatalf("turn sandbox = %+v", sandbox)
					}
				}
			}
			if !setupFound || !turnFound {
				t.Fatalf("protocol setup=%v turn=%v", setupFound, turnFound)
			}
		})
	}
}

// TestCodexOnRequestRejectsElicitation keeps non-approval interactions outside
// the supported bridge rather than silently accepting an MCP form or URL.
func TestCodexOnRequestRejectsElicitation(t *testing.T) {
	cfg := approvalFixtureConfig(t, "elicitation")
	h := newApprovalTestHandler()
	var pending atomic.Int64
	session := startApprovalFixture(t, context.Background(), cfg, approvalFixtureOptions(t, h, &pending))
	go func() {
		for range session.Messages {
		}
	}()
	result := fixtureApprovalResult(t, session)
	if result.Status != "failed" || !strings.Contains(result.Error, "unsupported") {
		t.Fatalf("elicitation result = %+v", result)
	}
	var rejected bool
	for _, message := range approvalFixtureWire(t, cfg) {
		if _, ok := message["method"]; !ok {
			_, rejected = message["error"]
			if _, accepted := message["result"]; accepted {
				t.Fatal("unsupported interaction was accepted")
			}
		}
	}
	if !rejected || pending.Load() != 0 {
		t.Fatalf("rejected=%v pending=%d", rejected, pending.Load())
	}
	select {
	case request := <-h.requests:
		t.Fatalf("elicitation entered approval handler: %+v", request)
	default:
	}
}

// TestCodexApprovalFullAccessKeepsUnattendedBehavior preserves the historical
// automatic native approval when policy is inherited or explicitly full-access.
func TestCodexApprovalFullAccessKeepsUnattendedBehavior(t *testing.T) {
	for _, policy := range []string{"", "full-access"} {
		t.Run("policy="+policy, func(t *testing.T) {
			cfg := approvalFixtureConfig(t, "quiet")
			h := newApprovalTestHandler()
			var pending atomic.Int64
			opts := approvalFixtureOptions(t, h, &pending)
			opts.ApprovalPolicy = policy
			opts.CustomArgs = []string{"-c", `approvals_reviewer="auto_review"`}
			session := startApprovalFixture(t, context.Background(), cfg, opts)
			go func() {
				for range session.Messages {
				}
			}()
			if result := fixtureApprovalResult(t, session); result.Status != "completed" {
				t.Fatalf("unattended result=%+v", result)
			}
			select {
			case request := <-h.requests:
				t.Fatalf("unattended policy requested human approval: %+v", request)
			default:
			}
			if pending.Load() != 0 {
				t.Fatalf("unattended pending=%d", pending.Load())
			}
			var keptReviewer bool
			for _, arg := range approvalFixtureArgs(t, cfg) {
				if strings.Contains(arg, "approvals_reviewer") {
					if arg != `approvals_reviewer="auto_review"` {
						t.Fatalf("unattended policy unexpectedly changed reviewer: %s", arg)
					}
					keptReviewer = true
				}
			}
			if !keptReviewer {
				t.Fatal("unattended policy removed the existing reviewer override")
			}
			var accepted bool
			for _, message := range approvalFixtureWire(t, cfg) {
				if string(message["method"]) == `"thread/start"` || string(message["method"]) == `"turn/start"` {
					var params map[string]json.RawMessage
					_ = json.Unmarshal(message["params"], &params)
					if _, changed := params["approvalsReviewer"]; changed {
						t.Fatal("unattended policy unexpectedly locked the native reviewer")
					}
				}
				if _, native := message["result"]; native {
					var body struct{ Decision string }
					_ = json.Unmarshal(message["result"], &body)
					accepted = body.Decision == "accept"
				}
			}
			if !accepted {
				t.Fatal("unattended native command was not accepted")
			}
		})
	}
}
