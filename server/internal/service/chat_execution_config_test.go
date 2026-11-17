package service

import (
	"encoding/json"
	"errors"
	"testing"
)

// TestChatExecutionConfigPatchPreservesAndRestoresInheritance owns sparse patch semantics.
func TestChatExecutionConfigPatchPreservesAndRestoresInheritance(t *testing.T) {
	inherited := ExecutionConfig{Model: "gpt-agent", ThinkingLevel: "low", ServiceTier: "default"}
	stored := []byte(`{"model":"gpt-chat","thinking_level":"high","approval_policy":"on-request"}`)
	updated, err := PatchChatExecutionConfig(stored, []byte(`{"service_tier":"priority"}`), inherited, "codex")
	if err != nil {
		t.Fatal(err)
	}
	got, err := ResolveChatExecutionConfig(updated, inherited)
	if err != nil {
		t.Fatal(err)
	}
	want := ExecutionConfig{Model: "gpt-chat", ThinkingLevel: "high", ServiceTier: "priority", ApprovalPolicy: "on-request"}
	if got != want {
		t.Fatalf("partial update = %+v, want %+v", got, want)
	}

	updated, err = PatchChatExecutionConfig(updated, []byte(`{"thinking_level":null}`), inherited, "codex")
	if err != nil {
		t.Fatal(err)
	}
	got, err = ResolveChatExecutionConfig(updated, inherited)
	if err != nil {
		t.Fatal(err)
	}
	if got.ThinkingLevel != "low" || got.Model != want.Model || got.ApprovalPolicy != want.ApprovalPolicy {
		t.Fatalf("field reset lost unrelated overrides: %+v", got)
	}

	updated, err = PatchChatExecutionConfig(updated, []byte(`null`), inherited, "codex")
	if err != nil {
		t.Fatal(err)
	}
	got, err = ResolveChatExecutionConfig(updated, inherited)
	if err != nil || got != inherited {
		t.Fatalf("whole reset = %+v, %v, want %+v", got, err, inherited)
	}
	var overrides map[string]any
	if err := json.Unmarshal(updated, &overrides); err != nil || len(overrides) != 0 {
		t.Fatalf("reset overrides = %s, %v", updated, err)
	}
}

// TestChatExecutionConfigModelChangeClearsDependentOverrides prevents old-model defaults from leaking.
func TestChatExecutionConfigModelChangeClearsDependentOverrides(t *testing.T) {
	inherited := ExecutionConfig{Model: "gpt-agent", ThinkingLevel: "low", ServiceTier: "default"}
	stored := []byte(`{"model":"gpt-old","thinking_level":"high","service_tier":"priority","approval_policy":"on-request"}`)
	updated, err := PatchChatExecutionConfig(stored, []byte(`{"model":"gpt-new"}`), inherited, "codex")
	if err != nil {
		t.Fatal(err)
	}
	got, err := ResolveChatExecutionConfig(updated, inherited)
	want := ExecutionConfig{Model: "gpt-new", ApprovalPolicy: "on-request"}
	if err != nil || got != want {
		t.Fatalf("model switch = %+v, %v, want %+v", got, err, want)
	}
	updated, err = PatchChatExecutionConfig(updated, []byte(`{"model":"gpt-agent","thinking_level":null,"service_tier":"priority","approval_policy":"full-access"}`), inherited, "codex")
	if err != nil {
		t.Fatal(err)
	}
	got, err = ResolveChatExecutionConfig(updated, inherited)
	if err != nil || got.ThinkingLevel != inherited.ThinkingLevel || got.ServiceTier != "priority" || got.ApprovalPolicy != "full-access" {
		t.Fatalf("explicit dependent settings were cleared: %+v, %v", got, err)
	}
}

// TestChatExecutionConfigRejectsInvalidAndUnsupportedOverrides covers independent literal gates.
func TestChatExecutionConfigRejectsInvalidAndUnsupportedOverrides(t *testing.T) {
	// Each case owns a distinct boundary, not a matrix of equivalent empty values.
	for _, tc := range []struct {
		name, provider, patch string
	}{
		{"approval on another provider", "claude", `{"approval_policy":"on-request"}`},
		{"unknown approval policy", "codex", `{"approval_policy":"always"}`},
		{"thinking token injection", "codex", `{"thinking_level":"high;echo"}`},
		{"tier on another provider", "claude", `{"service_tier":"priority"}`},
		{"non-string model", "codex", `{"model":7}`},
		{"unowned directory setting", "codex", `{"cwd":"/tmp"}`},
		{"effort without required model", "omp", `{"thinking_level":"high"}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, err := PatchChatExecutionConfig(nil, []byte(tc.patch), ExecutionConfig{}, tc.provider)
			if !errors.Is(err, ErrChatExecutionConfigInvalid) {
				t.Fatalf("patch %s: got %v, want invalid execution config", tc.patch, err)
			}
		})
	}
}

// TestChatTaskExecutionConfigPreservesSnapshotPresence owns claim's legacy/default distinction.
func TestChatTaskExecutionConfigPreservesSnapshotPresence(t *testing.T) {
	for _, tc := range []struct {
		name, context string
		want          *ExecutionConfig
	}{
		{"legacy context", `{"other_context":{"value":1}}`, nil},
		{"frozen settings", `{"execution_config":{"model":"gpt-send","thinking_level":"high","service_tier":"priority","approval_policy":"on-request"}}`, &ExecutionConfig{Model: "gpt-send", ThinkingLevel: "high", ServiceTier: "priority", ApprovalPolicy: "on-request"}},
		{"runtime defaults stay frozen", `{"execution_config":{"model":"","thinking_level":"","service_tier":"","approval_policy":""}}`, &ExecutionConfig{}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := TaskExecutionConfig([]byte(tc.context))
			if err != nil {
				t.Fatal(err)
			}
			if tc.want == nil {
				if got != nil {
					t.Fatalf("legacy task received a snapshot: %+v", got)
				}
				return
			}
			if got == nil || *got != *tc.want {
				t.Fatalf("snapshot = %+v, want %+v", got, tc.want)
			}
		})
	}
}

// TestChatTaskExecutionConfigRejectsMalformedSnapshots proves invalid frozen data cannot re-inherit.
func TestChatTaskExecutionConfigRejectsMalformedSnapshots(t *testing.T) {
	// Null objects and null fields require explicit gates: JSON otherwise silently zeroes strings.
	for _, raw := range []string{
		`{"execution_config":null}`,
		`{"execution_config":{"model":null}}`,
		`{"execution_config":{"cwd":"/unexpected"}}`,
		`{"execution_config":{"approval_policy":"always"}}`,
	} {
		got, err := TaskExecutionConfig([]byte(raw))
		if got != nil || !errors.Is(err, ErrChatExecutionConfigInvalid) {
			t.Fatalf("context %s = %+v, %v; want rejected snapshot", raw, got, err)
		}
	}
}
