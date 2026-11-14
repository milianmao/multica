package service

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/multica-ai/multica/server/pkg/agent"
)

// ErrChatExecutionConfigInvalid identifies rejected user-owned overrides.
var ErrChatExecutionConfigInvalid = errors.New("invalid chat execution_config")

// ExecutionConfig is a turn's resolved configuration; empty values must survive
// serialization so claim never replaces them with newer agent settings.
type ExecutionConfig struct {
	Model          string `json:"model"`
	ThinkingLevel  string `json:"thinking_level"`
	ServiceTier    string `json:"service_tier"`
	ApprovalPolicy string `json:"approval_policy"`
}

// ChatExecutionConfig stores session-local overrides; nil inherits from the
// agent, while a non-nil empty string selects the runtime's own default.
type ChatExecutionConfig struct {
	Model          *string `json:"model,omitempty"`
	ThinkingLevel  *string `json:"thinking_level,omitempty"`
	ServiceTier    *string `json:"service_tier,omitempty"`
	ApprovalPolicy *string `json:"approval_policy,omitempty"`
}

// resolve overlays only explicitly owned fields without mutating the agent.
func (config ChatExecutionConfig) resolve(inherited ExecutionConfig) ExecutionConfig {
	if config.Model != nil {
		inherited.Model = *config.Model
	}
	if config.ThinkingLevel != nil {
		inherited.ThinkingLevel = *config.ThinkingLevel
	}
	if config.ServiceTier != nil {
		inherited.ServiceTier = *config.ServiceTier
	}
	if config.ApprovalPolicy != nil {
		inherited.ApprovalPolicy = *config.ApprovalPolicy
	}
	return inherited
}

// decodeChatExecutionConfig keeps malformed persisted data from becoming an
// apparently inherited configuration at the next send.
func decodeChatExecutionConfig(raw []byte) (ChatExecutionConfig, error) {
	var config ChatExecutionConfig
	if len(raw) == 0 {
		return config, nil
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&config); err != nil {
		return config, fmt.Errorf("%w: %v", ErrChatExecutionConfigInvalid, err)
	}
	return config, nil
}

// ResolveChatExecutionConfig supplies a complete read or send snapshot from
// session overrides and the caller's current agent settings.
func ResolveChatExecutionConfig(raw []byte, inherited ExecutionConfig) (ExecutionConfig, error) {
	config, err := decodeChatExecutionConfig(raw)
	if err != nil {
		return ExecutionConfig{}, err
	}
	return config.resolve(inherited), nil
}

// TaskExecutionConfig distinguishes legacy tasks from frozen send-time settings.
// A present snapshot must be an object of strings with a supported approval
// policy; invalid snapshots fail closed instead of falling back to live agents.
// Model catalogs are intentionally not consulted again after acceptance.
func TaskExecutionConfig(rawContext []byte) (*ExecutionConfig, error) {
	if len(bytes.TrimSpace(rawContext)) == 0 {
		return nil, nil
	}
	var taskContext map[string]json.RawMessage
	if err := json.Unmarshal(rawContext, &taskContext); err != nil {
		return nil, fmt.Errorf("%w: task context must be an object: %v", ErrChatExecutionConfigInvalid, err)
	}
	raw, present := taskContext["execution_config"]
	if !present {
		return nil, nil
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil || fields == nil {
		return nil, fmt.Errorf("%w: task execution_config must be an object", ErrChatExecutionConfigInvalid)
	}
	for field, value := range fields {
		if bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
			return nil, fmt.Errorf("%w: task execution_config field %q must be a string", ErrChatExecutionConfigInvalid, field)
		}
	}
	var config ExecutionConfig
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&config); err != nil {
		return nil, fmt.Errorf("%w: %v", ErrChatExecutionConfigInvalid, err)
	}
	if config.ApprovalPolicy != "" && config.ApprovalPolicy != "on-request" && config.ApprovalPolicy != "full-access" {
		return nil, fmt.Errorf("%w: approval_policy must be empty, on-request or full-access", ErrChatExecutionConfigInvalid)
	}
	return &config, nil
}

// PatchChatExecutionConfig preserves omitted fields and restores inheritance
// for null. A model change clears carried effort/tier unless explicitly set in
// the same patch, preventing an old model's settings from being re-inherited.
func PatchChatExecutionConfig(raw, patch []byte, inherited ExecutionConfig, provider string) ([]byte, error) {
	config, err := decodeChatExecutionConfig(raw)
	if err != nil {
		return nil, err
	}
	before := config.resolve(inherited)
	if bytes.Equal(bytes.TrimSpace(patch), []byte("null")) {
		return json.Marshal(ChatExecutionConfig{})
	}
	if len(patch) == 0 {
		return json.Marshal(config)
	}
	var updates map[string]*string
	if err := json.Unmarshal(patch, &updates); err != nil {
		return nil, fmt.Errorf("%w: fields must be strings or null", ErrChatExecutionConfigInvalid)
	}
	for field, value := range updates {
		switch field {
		case "model":
			config.Model = value
		case "thinking_level":
			config.ThinkingLevel = value
		case "service_tier":
			config.ServiceTier = value
		case "approval_policy":
			config.ApprovalPolicy = value
		default:
			return nil, fmt.Errorf("%w: unknown field %q", ErrChatExecutionConfigInvalid, field)
		}
	}
	if _, changed := updates["model"]; changed && config.resolve(inherited).Model != before.Model {
		empty := ""
		if _, explicit := updates["thinking_level"]; !explicit {
			config.ThinkingLevel = &empty
		}
		if _, explicit := updates["service_tier"]; !explicit {
			config.ServiceTier = &empty
		}
	}
	if err := validateChatExecutionConfig(config, inherited, provider); err != nil {
		return nil, err
	}
	return json.Marshal(config)
}

// validateChatExecutionConfig uses the existing server literal gates; exact
// installed-model compatibility remains the daemon's pre-execution check.
func validateChatExecutionConfig(config ChatExecutionConfig, inherited ExecutionConfig, provider string) error {
	if config.ThinkingLevel != nil && !agent.IsKnownThinkingValue(provider, *config.ThinkingLevel) {
		return fmt.Errorf("%w: thinking_level is not supported by runtime %q", ErrChatExecutionConfigInvalid, provider)
	}
	if config.ServiceTier != nil && !agent.IsKnownServiceTier(provider, *config.ServiceTier) {
		return fmt.Errorf("%w: service_tier is not supported by runtime %q", ErrChatExecutionConfigInvalid, provider)
	}
	if config.ApprovalPolicy != nil && *config.ApprovalPolicy != "" {
		if provider != "codex" || (*config.ApprovalPolicy != "on-request" && *config.ApprovalPolicy != "full-access") {
			return fmt.Errorf("%w: approval_policy requires Codex and on-request or full-access", ErrChatExecutionConfigInvalid)
		}
	}
	effective := config.resolve(inherited)
	if (config.Model != nil || config.ThinkingLevel != nil) && effective.ThinkingLevel != "" &&
		strings.TrimSpace(effective.Model) == "" && agent.ThinkingLevelRejectedWithoutModel(provider) {
		return fmt.Errorf("%w: thinking_level requires an explicit model for runtime %q", ErrChatExecutionConfigInvalid, provider)
	}
	return nil
}
