package memoryhost

import (
	"strings"
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func TestCheckedBoundClaudeToolsRequireAgentToolsAndH13(t *testing.T) {
	for _, eventName := range []string{"PostToolUse", "PostToolUseFailure"} {
		for _, harness := range []string{"claude", "codex", "opencode", "hermes", "agy", ""} {
			for _, mode := range []string{"agent_tools", "ambient", "invalid", ""} {
				for _, seconds := range []time.Duration{0, 2, 12, 13, 14, 150} {
					config := value.Mapping(value.Field("harness", value.Text(harness)))
					if mode != "" {
						config.Set("recall_mode", value.Text(mode))
					}
					event := value.Mapping(value.Field("hook_event_name", value.Text(eventName)))
					got := checkedBoundInput(config, event, seconds*time.Second)
					if harness == "claude" && mode == "agent_tools" && seconds == 13 {
						if got != nil {
							t.Fatalf("supported %s/%s/%s H%d refused: %+v", harness, mode, eventName, seconds, got)
						}
						continue
					}
					if got == nil || got.ExitCode != 1 || got.Stdout != "" {
						t.Fatalf("unsupported %s/%s/%s H%d admitted: %+v", harness, mode, eventName, seconds, got)
					}
					if harness == "claude" && mode == "agent_tools" {
						if got.Stderr != "Cairn lifecycle diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n" {
							t.Fatalf("wrong tool budget bypassed preflight: %+v", got)
						}
					} else if !strings.Contains(got.Stderr, "checked bound memory cell remains unavailable") {
						t.Fatalf("other harness/mode changed refusal: %+v", got)
					}
				}
			}
		}
	}
}

func TestCheckedBoundExistingRowsAndUnknownEventsKeepTheirBudgets(t *testing.T) {
	for _, harness := range []string{"codex", "claude"} {
		for _, mode := range []string{"agent_tools", "ambient", ""} {
			config := value.Mapping(value.Field("harness", value.Text(harness)), value.Field("recall_mode", value.Text(mode)))
			for _, name := range []string{"SessionStart", "UserPromptSubmit", "PreCompact", "Stop", "SessionEnd", "PreToolUse", "Unknown"} {
				event := value.Mapping(value.Field("hook_event_name", value.Text(name)))
				for _, seconds := range []time.Duration{2, 13, 150} {
					recall := name == "SessionStart" || name == "UserPromptSubmit"
					capture := name == "PreCompact" || harness == "codex" && name == "Stop" || harness == "claude" && name == "SessionEnd"
					want := recall && seconds == 13 || capture && seconds == 150
					if got := checkedBoundInput(config, event, seconds*time.Second); (got == nil) != want {
						t.Fatalf("existing %s/%s/%s H%d admitted=%v, want %v", harness, mode, name, seconds, got == nil, want)
					}
				}
			}
		}
	}
}
