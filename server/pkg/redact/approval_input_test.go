package redact

import (
	"encoding/json"
	"strings"
	"testing"
)

// TestInputMapApprovalCredentials keeps the human review payload useful without exposing embedded credentials.
func TestInputMapApprovalCredentials(t *testing.T) {
	t.Parallel()
	input := map[string]any{
		"command": "curl --data '{\"apiKey\":\"unprefixed-json-value\",\"password\":\"two secret words\"}' --retry 3",
		"env":     map[string]string{"API_KEY": "unprefixed-env-value", "MODE": "test"},
		"params":  map[string]any{"apiKey": "unprefixed-field-value", "cwd": "/workspace"},
	}
	got := InputMap(input)
	encoded, err := json.Marshal(got)
	if err != nil {
		t.Fatal(err)
	}
	for _, secret := range []string{"unprefixed-json-value", "unprefixed-env-value", "unprefixed-field-value", "secret words"} {
		if strings.Contains(string(encoded), secret) {
			t.Fatalf("approval payload exposes a credential: %s", encoded)
		}
	}
	if !strings.HasSuffix(got["command"].(string), " --retry 3") || got["env"].(map[string]string)["MODE"] != "test" || got["params"].(map[string]any)["cwd"] != "/workspace" {
		t.Fatalf("redaction removed the non-secret review context: %s", encoded)
	}
}
