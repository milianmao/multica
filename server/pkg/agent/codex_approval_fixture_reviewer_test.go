package agent

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/pelletier/go-toml/v2"
)

// codexFixtureApprovalReviewer models copied root config and top-level CLI
// overrides. Per-profile override stripping is inspected separately in argv.
// Thread/turn approvalsReviewer overrides follow the official v2 schema.
func codexFixtureApprovalReviewer() string {
	config := struct {
		Reviewer string `toml:"approvals_reviewer"`
	}{Reviewer: "user"}
	data, err := os.ReadFile(filepath.Join(os.Getenv("CODEX_HOME"), "config.toml"))
	if err == nil {
		if toml.Unmarshal(data, &config) != nil {
			os.Exit(5)
		}
	} else if !os.IsNotExist(err) {
		os.Exit(5)
	}
	args := os.Args[1:]
	for i := 0; i < len(args); i++ {
		flag, value, inline := strings.Cut(args[i], "=")
		if flag != "-c" && flag != "--config" {
			continue
		}
		if !inline && i+1 < len(args) {
			i++
			value = args[i]
		}
		var override struct {
			Reviewer string `toml:"approvals_reviewer"`
		}
		if toml.Unmarshal([]byte(value), &override) == nil && override.Reviewer != "" {
			config.Reviewer = override.Reviewer
		}
	}
	return config.Reviewer
}

// approvalFixtureArgs returns the actual test-created process argv, not a
// separately reconstructed launch list that could hide prefix/custom overrides.
func approvalFixtureArgs(t *testing.T, cfg Config) []string {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(cfg.Env["CODEX_HOME"], "argv.json"))
	if err != nil {
		t.Fatal(err)
	}
	var args []string
	if err := json.Unmarshal(data, &args); err != nil {
		t.Fatal(err)
	}
	return args
}
