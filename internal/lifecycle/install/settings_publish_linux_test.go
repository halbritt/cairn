package install

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func stageTestHookGen(t *testing.T, root, harness string, rows []hookSelectedRow) Generation {
	t.Helper()
	stageIn := filepath.Join(root, "in-"+uuid.NewString())
	if err := os.MkdirAll(stageIn, 0700); err != nil {
		t.Fatal(err)
	}
	layoutName := "settings.json"
	if harness == "codex" || harness == "agy" {
		layoutName = "hooks.json"
	}
	layout := map[string]string{"hook-settings.json": layoutName}
	if harness == "opencode" {
		layout = map[string]string{"cairn-lifecycle.json": "cairn-lifecycle.json", "cairn-coordination.json": "cairn-coordination.json"}
	}
	selRaw, err := json.Marshal(hookSelectionRecord{
		Schema:  "cairn.lifecycle-hook-settings/1",
		Harness: harness,
		Layout:  layout,
		Rows:    rows,
	})
	if err != nil {
		t.Fatal(err)
	}
	selPath := filepath.Join(stageIn, "hook-selection.json")
	if err := os.WriteFile(selPath, selRaw, 0600); err != nil {
		t.Fatal(err)
	}
	genRoot := filepath.Join(root, "gens")
	if err := os.MkdirAll(genRoot, 0700); err != nil {
		t.Fatal(err)
	}
	gen, err := StageGeneration(context.Background(), genRoot, uuid.NewString(), []File{
		{Name: "hook-selection.json", Path: selPath, SHA256: agyDigest(selRaw)},
	})
	if err != nil {
		t.Fatal(err)
	}
	return gen
}

func TestPublishHookSettingsPreservesInterveningEditsAndStopsOnMidWriteRace(t *testing.T) {
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	acct := filepath.Join(root, "claude-acct")
	if err := os.MkdirAll(acct, 0700); err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(acct, "settings.json")

	// 1. Ordinary no-edit case on initial settings file.
	initial := []byte("{\n  \"opaque\": 900719925474099312345,\n  \"hooks\": {\n    \"SessionStart\": [{\"matcher\": \"*\", \"hooks\": [{\"type\": \"command\", \"command\": \"/foreign/keep\"}, {\"type\": \"command\", \"command\": \"/old/cairn-mem\"}]}]\n  }\n}\n")
	if err := os.WriteFile(target, initial, 0600); err != nil {
		t.Fatal(err)
	}
	gen := stageTestHookGen(t, root, "claude", []hookSelectedRow{
		{
			Role:            "memory",
			Event:           "SessionStart",
			Argv:            []string{"/new/cairn-mem", "hook"},
			CheckedLauncher: []string{"/new/cairn-mem"},
			Retained:        []string{"/ret/cairn-mem"},
			Hook:            json.RawMessage(`{"type":"command","command":"/new/cairn-mem hook","timeout":13}`),
		},
		{
			Role:            "coordination",
			Event:           "Stop",
			Argv:            []string{"/new/cairn-coord", "hook"},
			CheckedLauncher: []string{"/new/cairn-coord"},
			Retained:        []string{"/ret/cairn-coord"},
			Hook:            json.RawMessage(`{"type":"command","command":"/new/cairn-coord hook","timeout":15}`),
		},
	})
	plan := HookSettingsPublishPlan{
		Target:          target,
		Selection:       gen,
		ReplaceCommands: map[string][]string{"memory": {"/old/cairn-mem"}, "coordination": {}},
	}
	res1, err := PublishHookSettings(context.Background(), plan)
	if err != nil || !res1.Changed || res1.Exchange == nil {
		t.Fatalf("ordinary publish failed: res=%+v err=%v", res1, err)
	}
	again, err := PublishHookSettings(context.Background(), plan)
	if err != nil || again.Changed {
		t.Fatalf("expected idempotent repeat Changed=false, got %+v err=%v", again, err)
	}
	rev1, err := ReversePublishedSettings(context.Background(), res1.Exchange.Recovery)
	if err != nil || rev1.State != "reversed" {
		t.Fatalf("reverse failed: %+v err=%v", rev1, err)
	}
	if got, _ := os.ReadFile(target); !bytes.Equal(got, initial) {
		t.Fatalf("reverse did not restore exact bytes: %q", got)
	}
	testInterveningAndMidWriteRace(t, target, plan)
}

func testInterveningAndMidWriteRace(t *testing.T, target string, plan HookSettingsPublishPlan) {
	t.Helper()
	// 2. Intervening edit between staging and publication: user edits top-level field
	// (including big integer) AND adds a foreign hook on both SessionStart and PreToolUse.
	intervening := []byte("{\n  \"opaque\": 900719925474099399999,\n  \"userField\": \"added-after-stage\",\n  \"hooks\": {\n    \"SessionStart\": [{\"matcher\": \"*\", \"hooks\": [{\"type\": \"command\", \"command\": \"/foreign/keep\"}, {\"type\": \"command\", \"command\": \"/old/cairn-mem\"}, {\"type\": \"command\", \"command\": \"/foreign/intervening\"}]}],\n    \"PreToolUse\": [{\"hooks\": [{\"type\": \"command\", \"command\": \"/foreign/pretool\"}]}]\n  }\n}\n")
	if err := os.WriteFile(target, intervening, 0600); err != nil {
		t.Fatal(err)
	}
	res2, err := PublishHookSettings(context.Background(), plan)
	if err != nil || !res2.Changed || res2.Exchange == nil {
		t.Fatalf("publish after intervening edit failed: %+v err=%v", res2, err)
	}
	published, err := os.ReadFile(target)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{
		`"opaque": 900719925474099399999`,
		`"userField": "added-after-stage"`,
		`"/foreign/keep"`,
		`"/foreign/intervening"`,
		`"/foreign/pretool"`,
		`"/new/cairn-mem hook"`,
		`"/new/cairn-coord hook"`,
	} {
		if !strings.Contains(string(published), want) {
			t.Fatalf("published settings missing %s:\n%s", want, published)
		}
	}
	if strings.Contains(string(published), "/old/cairn-mem") {
		t.Fatalf("old Cairn command remained after publish:\n%s", published)
	}

	// 3. Post-publication foreign edit causes ReversePublishedSettings to stop without overwriting.
	postEdit := bytes.Replace(published, []byte(`"added-after-stage"`), []byte(`"edited-after-publish"`), 1)
	if err := os.WriteFile(target, postEdit, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := ReversePublishedSettings(context.Background(), res2.Exchange.Recovery); err == nil {
		t.Fatal("expected ReversePublishedSettings to refuse post-publication drift")
	}
	if got, _ := os.ReadFile(target); !bytes.Equal(got, postEdit) {
		t.Fatalf("ReversePublishedSettings mutated drifted target: %s", got)
	}

	// 4. Mid-publication concurrent edit between snapshot/candidate prep and RENAME_EXCHANGE:
	// publishHookSettings must refuse before replacing the file and leave concurrent edit intact.
	if err := os.WriteFile(target, intervening, 0600); err != nil {
		t.Fatal(err)
	}
	concurrent := bytes.Replace(intervening, []byte(`"added-after-stage"`), []byte(`"mid-write-edit"`), 1)
	_, err = publishHookSettings(context.Background(), plan, func(step string) error {
		if step == "before:publish" {
			return os.WriteFile(target, concurrent, 0600)
		}
		return errors.New("unexpected observe step: " + step)
	})
	if err == nil {
		t.Fatal("expected mid-publication edit to stop before replacing target")
	}
	if got, _ := os.ReadFile(target); !bytes.Equal(got, concurrent) {
		t.Fatalf("mid-publication refusal lost concurrent edit: %s", got)
	}
}

func TestPublishOpenCodePluginSettingsPreservesInterveningEdits(t *testing.T) {
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	acct := filepath.Join(root, "opencode-acct")
	if err := os.MkdirAll(acct, 0700); err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(acct, "cairn-lifecycle.json")
	gen := stageTestHookGen(t, root, "opencode", []hookSelectedRow{
		{
			Role:  "memory",
			Event: "SessionStart",
			Argv:  []string{"/new/cairn-mem", "--role", "memory"},
		},
		{
			Role:  "coordination",
			Event: "Stop",
			Argv:  []string{"/new/cairn-coord", "hook"},
		},
	})
	intervening := []byte("{\n  \"custom_flag\": true,\n  \"big_id\": 900719925474099377777,\n  \"commands\": {\"SessionStart\": [\"/old/cairn-mem\"]}\n}\n")
	if err := os.WriteFile(target, intervening, 0600); err != nil {
		t.Fatal(err)
	}
	res, err := PublishHookSettings(context.Background(), HookSettingsPublishPlan{
		Target:          target,
		Role:            "memory",
		Selection:       gen,
		ReplaceCommands: map[string][]string{"memory": {}, "coordination": {}},
	})
	if err != nil || !res.Changed || res.Exchange == nil {
		t.Fatalf("opencode publish failed: %+v err=%v", res, err)
	}
	got, err := os.ReadFile(target)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{`"custom_flag": true`, `"big_id": 900719925474099377777`, `"/new/cairn-mem"`} {
		if !strings.Contains(string(got), want) {
			t.Fatalf("opencode published settings missing %s:\n%s", want, got)
		}
	}
	if strings.Contains(string(got), "/new/cairn-coord") {
		t.Fatalf("memory role leaked coordination command:\n%s", got)
	}
}
