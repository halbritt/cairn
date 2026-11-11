package memoryhost

import (
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func TestCheckedCaptureUsesOnlySourceProducerRows(t *testing.T) {
	rows := map[string]map[string]bool{
		"codex":    {"PreCompact": true, "Stop": true},
		"claude":   {"PreCompact": true, "SessionEnd": true},
		"opencode": {"PreCompact": true, "SessionEnd": true},
		"hermes":   {"PreCompact": true, "SessionEnd": true},
	}
	for _, harness := range []string{"codex", "claude", "opencode", "hermes", "agy", "unknown", ""} {
		for _, name := range []string{"PreCompact", "Stop", "SessionEnd", "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "TurnEnd", ""} {
			t.Run(harness+"/"+name, func(t *testing.T) {
				config := value.Mapping(value.Field("harness", value.Text(harness)))
				event := value.Mapping(value.Field("hook_event_name", value.Text(name)))
				if got := checkedCaptureCell(config, event); got != rows[harness][name] {
					t.Fatalf("capture row %s/%s: admitted=%v", harness, name, got)
				}
			})
		}
	}
}
