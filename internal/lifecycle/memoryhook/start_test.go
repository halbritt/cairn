package memoryhook_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/memoryhook"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func TestUnexpectedBodyFaultCannotAcquireBlockingExit(t *testing.T) {
	dir := t.TempDir()
	if err := os.Mkdir(filepath.Join(dir, ".git"), 0700); err != nil {
		t.Fatal(err)
	}
	decode := func(v any) *value.Value {
		raw, err := json.Marshal(v)
		if err != nil {
			t.Fatal(err)
		}
		result, err := value.DecodeText(raw, "utf-8", 0)
		if err != nil {
			t.Fatal(err)
		}
		return result
	}
	config := decode(map[string]any{"harness": "codex", "recall_mode": "agent_tools", "state_dir": filepath.Join(dir, "state")})
	event := decode(map[string]any{"hook_event_name": "SessionStart", "cwd": dir, "session_id": "11111111-1111-4111-8111-111111111111"})
	// A nil internal context faults in the real lock boundary. No production
	// fault knob or alternate successful body is added to exercise recovery.
	result := memoryhook.RunStart(nil, config, event, true, dir, []string{"HOME=" + dir})
	if result.ExitCode != 1 || result.Stdout != "" || result.Stderr != "Cairn lifecycle diagnostic: phase=handle error_class=UnexpectedError\n" {
		t.Fatalf("unexpected fault escaped fixed boundary: %#v", result)
	}
	if result.Trace["outcome"] != "unexpected" {
		t.Fatalf("unexpected body was labelled complete: %#v", result.Trace)
	}
}
