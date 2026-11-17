package execenv

import (
	"bytes"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestCodexOnRequestHomeEnforcement exercises both preparation and reuse of a
// task-local home. Removing the approval-policy dispatch restores the legacy
// unsandboxed fallback; rejecting MXC also rejects a documented native choice.
func TestCodexOnRequestHomeEnforcement(t *testing.T) {
	sharedHome := t.TempDir()
	t.Setenv("CODEX_HOME", sharedHome)
	sharedConfig := filepath.Join(sharedHome, "config.toml")
	original := []byte("model = \"fixture-model\"\n")
	if err := os.WriteFile(sharedConfig, original, 0o600); err != nil {
		t.Fatal(err)
	}
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	cases := []struct {
		name, goos, policy string
		config             string
		args               []string
		reuse, reject      bool
	}{
		{name: "windows_strict_no_native", goos: "windows", policy: "on-request", reject: true},
		{name: "windows_inherited", goos: "windows"},
		{name: "windows_full_access", goos: "windows", policy: "full-access"},
		{name: "linux_strict", goos: "linux", policy: "on-request"},
		{name: "macos_unknown_network_support", goos: "darwin", policy: "on-request", reject: true},
		{name: "windows_mxc_prepare", goos: "windows", policy: "on-request", args: []string{"-c", `windows.sandbox="mxc"`}},
		{name: "windows_prefer_mxc", goos: "windows", policy: "on-request", config: "[features]\nprefer_mxc = true\n"},
		{name: "windows_mxc_reuse", goos: "windows", policy: "on-request", args: []string{"-c", `windows.sandbox="mxc"`}, reuse: true},
		{name: "windows_strict_reuse_no_native", goos: "windows", policy: "on-request", reuse: true, reject: true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			shared := original
			if tc.config != "" {
				shared = []byte(tc.config)
			}
			if err := os.WriteFile(sharedConfig, shared, 0o600); err != nil {
				t.Fatal(err)
			}
			home := filepath.Join(t.TempDir(), "task-codex-home")
			opts := CodexHomeOptions{GOOS: tc.goos, ApprovalPolicy: tc.policy, CodexCustomArgs: tc.args}
			var err error
			if tc.reuse {
				if err := prepareCodexHomeWithOpts(home, CodexHomeOptions{GOOS: tc.goos}, logger); err != nil {
					t.Fatal(err)
				}
				before, readErr := os.ReadFile(filepath.Join(home, "config.toml"))
				if readErr != nil {
					t.Fatal(readErr)
				}
				err = ApplyCodexApprovalPolicy(home, opts, logger)
				if tc.reject {
					after, readErr := os.ReadFile(filepath.Join(home, "config.toml"))
					if readErr != nil || !bytes.Equal(before, after) {
						t.Fatal("rejected strict reuse changed the existing home")
					}
				}
			} else {
				err = prepareCodexHomeWithOpts(home, opts, logger)
			}
			if tc.reject {
				if err == nil || !strings.Contains(err.Error(), "on-request") {
					t.Fatalf("strict unsupported sandbox was not rejected: %v", err)
				}
			} else if err != nil {
				t.Fatalf("supported execution was rejected: %v", err)
			}
			after, readErr := os.ReadFile(sharedConfig)
			if readErr != nil || !bytes.Equal(shared, after) {
				t.Fatal("execution changed the shared Codex config")
			}
		})
	}
}

// TestCodexApprovalPolicyRejectsSharedHome protects the owning boundary rather
// than asserting managed TOML spelling. Removing the guard writes globally.
func TestCodexApprovalPolicyRejectsSharedHome(t *testing.T) {
	home := t.TempDir()
	t.Setenv("CODEX_HOME", home)
	path := filepath.Join(home, "config.toml")
	original := []byte("model = \"fixture-model\"\n")
	if err := os.WriteFile(path, original, 0o600); err != nil {
		t.Fatal(err)
	}
	err := ApplyCodexApprovalPolicy(home, CodexHomeOptions{ApprovalPolicy: "on-request", GOOS: "linux"}, nil)
	if err == nil || !strings.Contains(err.Error(), "task-local") {
		t.Fatalf("global home was accepted: %v", err)
	}
	after, err := os.ReadFile(path)
	if err != nil || !bytes.Equal(original, after) {
		t.Fatal("global home was modified")
	}
}

// TestCodexApprovalInheritedMXCPreference prevents strict-mode capability
// recognition from changing the empty policy's historical unattended fallback.
func TestCodexApprovalInheritedMXCPreference(t *testing.T) {
	state := windowsSandboxFromConfig("[features]\nprefer_mxc = true\n")
	if policy := codexSandboxPolicyForWindows(state); policy.Mode != "danger-full-access" {
		t.Fatalf("empty approval policy changed its legacy fallback: %+v", policy)
	}
}
