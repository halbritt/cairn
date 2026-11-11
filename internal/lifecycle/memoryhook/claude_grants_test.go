package memoryhook

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/statefile"
)

func TestClaudePromptOwnerDoesNotUseCallerSelectedNativeFields(t *testing.T) {
	for _, prompt := range []*value.Value{nil, integer(7), value.Text(""), value.Text(" \t"), value.Text("owner\x00other"), value.Text(string(bytes.Repeat([]byte{'x'}, 257)))} {
		event := value.Mapping(value.Field("prompt_id", prompt), value.Field("turn_id", value.Text("11111111-1111-4111-8111-111111111111")), value.Field("native_owner", value.Text("forged")))
		if got := claudePromptOwner(event); got != "" {
			t.Fatalf("caller-selected native metadata supplied owner %q", got)
		}
	}
	event := value.Mapping(value.Field("prompt_id", value.Text("actual 日本語 😀")))
	if got := claudePromptOwner(event); got != "claude-channel:actual 日本語 😀" {
		t.Fatal(got)
	}
}

func TestClaudeRequiredBoundaryReadsOnlyUntilSessionLockIsHeld(t *testing.T) {
	directory := t.TempDir()
	session := "11111111-1111-4111-8111-111111111111"
	config := value.Mapping(value.Field("harness", value.Text("claude")), value.Field("inbox_recall_binding", value.Text("selected-fixture-binding")), value.Field("state_dir", value.Text(directory)), value.Field("recall_mode", value.Text("agent_tools")), value.Field("context_bytes", integer(9500)))
	event := value.Mapping(value.Field("session_id", value.Text(session)), value.Field("hook_event_name", value.Text("UserPromptSubmit")), value.Field("prompt_id", value.Text("next-prompt")))
	grant := value.Mapping(value.Field("identity", value.Mapping(value.Field("native_owner", value.Text("claude-channel:first-prompt")))), value.Field("limit_bytes", integer(9500)), value.Field("output_bytes", integer(1000)), value.Field("native_allowance_bytes", integer(8500)), value.Field("required", value.Mapping(value.Field("count", value.IntegerText("999999999999999999999999999999999")), value.Field("selection_sha256", value.Text("")), value.Field("pending", value.Boolean(false)))))
	key := string(bytes.Repeat([]byte{'a'}, 64))
	state := value.Mapping(value.Field("schema", value.Text("cairn.inbox-recall-grants/1")), value.Field("session", value.Text(session)), value.Field("active", value.Text(key)), value.Field("grants", value.Mapping(value.Field(key, grant))), value.Field("opaque", value.IntegerText("999999999999999999999999999999999999999")))
	path := filepath.Join(directory, session+".inbox-recall.json")
	if err := saveClaudeLedger(path, state); err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	original := fail("optional retrieval failed")
	if !isRequired(claudeRequiredError(config, event, original, false)) {
		t.Fatal("large known required count was lost at the read-only guard")
	}
	if err := statefile.LegacyLock(context.Background(), filepath.Join(directory, session+".lock"), 0, func() error {
		if !isRequired(claudeBoundaryError(config, event, original)) {
			t.Fatal("busy lock lost known requirement")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	unchanged, err := os.ReadFile(path)
	if err != nil || !bytes.Equal(before, unchanged) {
		t.Fatal("read-only/busy boundary changed grant bytes")
	}
	if !isRequired(claudeBoundaryError(config, event, original)) {
		t.Fatal("unlocked boundary did not preserve required refusal")
	}
	after, err := loadClaudeLedger(path, session)
	if err != nil {
		t.Fatal(err)
	}
	actual := after.Get("grants").Get(key)
	if !value.Truth(actual.Get("required").Get("pending")) || number(actual.Get("output_bytes")) != 1000 || number(actual.Get("native_allowance_bytes")) != 8500 || len(after.Get("grants").Members) != 1 || after.Get("opaque").Integer != state.Get("opaque").Integer {
		t.Fatal("pending preservation renewed, replaced or truncated an existing grant")
	}
}

func TestClaudeAndCodexRequiredStartResultsStayDistinct(t *testing.T) {
	event := value.Mapping(value.Field("hook_event_name", value.Text("SessionStart")))
	for _, harness := range []string{"claude", "codex"} {
		config := value.Mapping(value.Field("harness", value.Text(harness)), value.Field("inbox_recall_binding", value.Text("selected-fixture-binding")))
		result := requiredMemoryOutput(config, event, required("current context could not be restored"), "fixed diagnostic\n")
		if harness == "claude" && (result.ExitCode != 1 || result.Stdout != "") || harness == "codex" && (result.ExitCode != 0 || result.Stdout != stopOutput) {
			t.Fatalf("%s refusal crossed harness contract: %+v", harness, result)
		}
	}
}
