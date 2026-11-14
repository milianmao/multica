package agent

import (
	"context"
	"encoding/json"
	"testing"
)

const codexSnapshotFixtureModelEnv = "MULTICA_TEST_CODEX_CONFIGURED_MODEL"

// TestCodexExecutionConfigSnapshotResume checks actual native inheritance,
// not just request spelling: clearing a snapshot must not revive old tiers.
// Removing config-model precedence, the null tier, or the model override is red.
func TestCodexExecutionConfigSnapshotResume(t *testing.T) {
	for _, tc := range []struct {
		name, model, configuredModel, wantModel, wantEffort, wantTier string
		snapshot, wantConfigRead, wantModelLookup                     bool
	}{
		{"changed_model", "native-new-model", "", "native-new-model", "low", "", true, false, false},
		{"configured_default", "", "native-config-model", "native-config-model", "low", "", true, true, false},
		{"runtime_default", "", "", "native-default-model", "low", "", true, true, true},
		{"legacy_inheritance", "", "", "native-old-model", "xhigh", "priority", false, false, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cfg := approvalFixtureConfig(t, "selection")
			cfg.Env[codexSnapshotFixtureModelEnv] = tc.configuredModel
			opts := ExecOptions{
				Cwd: t.TempDir(), Model: tc.model, ResumeSessionID: "prior-thread",
				ExecutionConfigPresent: tc.snapshot,
			}
			session := startApprovalFixture(t, context.Background(), cfg, opts)
			go func() {
				for range session.Messages {
				}
			}()
			result := fixtureApprovalResult(t, session)
			if result.Status != "completed" {
				t.Fatalf("snapshot resume failed: %+v", result)
			}
			var effective struct {
				Model, ReasoningEffort, ServiceTier string
			}
			if err := json.Unmarshal([]byte(result.Output), &effective); err != nil {
				t.Fatalf("native selection %q: %v", result.Output, err)
			}
			if effective.Model != tc.wantModel || effective.ReasoningEffort != tc.wantEffort || effective.ServiceTier != tc.wantTier {
				t.Fatalf("native selection = %+v, want model=%s effort=%s tier=%q", effective, tc.wantModel, tc.wantEffort, tc.wantTier)
			}
			configReads, lookups, setups, turns := 0, 0, 0, 0
			for _, message := range approvalFixtureWire(t, cfg) {
				if string(message["method"]) == `"config/read"` {
					configReads++
					var params struct {
						Cwd           string `json:"cwd"`
						IncludeLayers bool   `json:"includeLayers"`
					}
					if err := json.Unmarshal(message["params"], &params); err != nil {
						t.Fatal(err)
					}
					if params.Cwd != opts.Cwd || params.IncludeLayers {
						t.Fatalf("runtime config lookup = %+v, want cwd=%s without layers", params, opts.Cwd)
					}
				}
				if string(message["method"]) == `"model/list"` {
					lookups++
				}
				if string(message["method"]) != `"thread/resume"` && string(message["method"]) != `"turn/start"` {
					continue
				}
				if string(message["method"]) == `"thread/resume"` {
					setups++
				} else {
					turns++
				}
				var params map[string]json.RawMessage
				if err := json.Unmarshal(message["params"], &params); err != nil {
					t.Fatal(err)
				}
				if tier, present := params["serviceTier"]; present != tc.snapshot || (tc.snapshot && string(tier) != "null") {
					t.Fatalf("snapshot=%v native tier present=%v value=%s", tc.snapshot, present, tier)
				}
			}
			if setups != 1 || turns != 1 || (configReads == 1) != tc.wantConfigRead || configReads > 1 || (lookups == 1) != tc.wantModelLookup || lookups > 1 {
				t.Fatalf("native calls: resume=%d turn=%d config/read=%d model/list=%d", setups, turns, configReads, lookups)
			}
		})
	}
}

// applyCodexFixtureSelection models the documented resume override boundary:
// explicit models skip saved effort; tier null clears, but effort null inherits.
func applyCodexFixtureSelection(selection map[string]any, params map[string]json.RawMessage) {
	var model string
	_ = json.Unmarshal(params["model"], &model)
	if model != "" {
		selection["model"], selection["reasoningEffort"] = model, "low"
	}
	var config map[string]json.RawMessage
	_ = json.Unmarshal(params["config"], &config)
	var effort string
	_ = json.Unmarshal(config["model_reasoning_effort"], &effort)
	if effort != "" {
		selection["reasoningEffort"] = effort
	}
	_ = json.Unmarshal(params["effort"], &effort)
	if effort != "" {
		selection["reasoningEffort"] = effort
	}
	if tier, present := params["serviceTier"]; present {
		var value any
		_ = json.Unmarshal(tier, &value)
		selection["serviceTier"] = value
	}
}
