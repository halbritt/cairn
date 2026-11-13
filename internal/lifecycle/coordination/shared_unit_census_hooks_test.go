package coordination

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestWatchCensusClaudeLayerInventory(t *testing.T) {
	for _, mutation := range []string{"none", "extra-file", "changed-file", "extra-hook", "missing-file", "external-pin", "alias"} {
		t.Run(mutation, func(t *testing.T) {
			f := newCensusFixture(t)
			if err := os.Chmod(f.root, 0700); err != nil {
				t.Fatal(err)
			}
			account := filepath.Join(f.root, "claude-account")
			workspace := filepath.Join(f.root, "claude-workspace")
			for _, path := range []string{account, workspace} {
				if err := os.Mkdir(path, 0700); err != nil {
					t.Fatal(err)
				}
			}
			settings := censusTestJSON(t, filepath.Join(account, "settings.json"), map[string]any{"unrelated": []any{"café", json.Number("900719925474099312345")}})
			selected := WatchCensusClaudeHooks{Account: account, Workspace: workspace, LayerFiles: []WatchCensusPin{settings}}
			switch mutation {
			case "extra-file":
				censusTestJSON(t, filepath.Join(workspace, "unlisted.json"), map[string]bool{})
			case "changed-file":
				censusTestJSON(t, settings.Path, map[string]bool{"changed": true})
			case "extra-hook":
				selected.LayerFiles[0] = censusTestJSON(t, settings.Path, map[string]any{"hooks": map[string]any{"UserPromptSubmit": []any{map[string]any{"hooks": []any{map[string]string{"command": "cairn hook extra"}}}}}})
			case "missing-file":
				if err := os.Remove(settings.Path); err != nil {
					t.Fatal(err)
				}
			case "external-pin":
				selected.LayerFiles = append(selected.LayerFiles, f.catalog.Sessions[0])
			case "alias":
				if err := os.Symlink(settings.Path, filepath.Join(workspace, "settings.json")); err != nil {
					t.Fatal(err)
				}
			}
			r := &watchCensusRead{catalog: f.catalog, directories: map[string][]string{}, files: map[string]string{}, inodes: map[string]string{}, images: map[string]bool{}, report: WatchUnitCensus{Facts: map[string]string{}}, local: &watchDrainRead{tokenPaths: map[string]bool{}}}
			err := r.hookLayers(context.Background(), selected)
			if (err == nil) != (mutation == "none") {
				t.Fatalf("mutation=%s: %v", mutation, err)
			}
		})
	}
}

func TestWatchCensusClaudeSelectedInputsRefuse(t *testing.T) {
	for _, mutation := range []string{"harness", "layer", "unknown-config", "duplicate", "foreign-account"} {
		t.Run(mutation, func(t *testing.T) {
			f := newCensusFixture(t)
			path := filepath.Join(f.catalog.Selections[0].Directory, "one.json")
			selected := WatchCensusClaudeHooks{Schema: "cairn.watch-claude-hooks/1", Harness: "claude", Layer: "flagSettings", Config: WatchCensusPin{path, f.catalog.Selections[0].Pins["one.json"]}, Session: f.catalog.Sessions[0], Account: f.configs[path]["config_home"].(string), Workspace: filepath.Join(f.root, "workspaces", "a-one.json")}
			selected.Generation, selected.Settings, selected.Readback = selected.Session, selected.Session, selected.Session
			switch mutation {
			case "harness":
				selected.Harness = "codex"
			case "layer":
				selected.Layer = "accountSettings"
			case "unknown-config":
				selected.Config = f.catalog.Sessions[0]
			case "unknown-session":
				selected.Session = selected.Config
			case "foreign-account":
				selected.Account = "/unknown-account"
			}
			pin := censusTestJSON(t, filepath.Join(f.root, "native-hooks.json"), selected)
			f.catalog.HookSelections = []WatchCensusPin{pin}
			if mutation == "duplicate" {
				f.catalog.HookSelections = append(f.catalog.HookSelections, pin)
			}
			got, err := f.inspect(t)
			if err == nil || got.FixtureReady || len(got.HookObservations) != 0 {
				t.Fatalf("invalid selection admitted: %+v %v", got, err)
			}
		})
	}
}

func TestWatchCensusClaudeStrictObservationJSON(t *testing.T) {
	for _, raw := range []string{`{"Schema":"a","Schema":"b"}`, `{"Unknown":true}`, `{"Schema":"\ud800"}`, `{} {}`} {
		var selected WatchCensusClaudeHooks
		if err := hookDecode([]byte(raw), &selected); err == nil {
			t.Fatalf("invalid input admitted: %s", raw)
		}
	}
	if !hookJSONEqual([]byte(`{"x":900719925474099312345,"y":"café"}`), []byte(`{"y":"café","x":900719925474099312345}`)) {
		t.Fatal("unrelated settings lost number precision or depend on key order")
	}
	if hookJSONEqual([]byte(`{"x":900719925474099312345}`), []byte(`{"x":900719925474099312346}`)) {
		t.Fatal("different selected integer admitted")
	}
}

func TestWatchCensusClaudeNoInputRetainsEmptyLayerRefusal(t *testing.T) {
	f := newCensusFixture(t)
	for _, config := range f.configs {
		censusTestJSON(t, filepath.Join(config["config_home"].(string), "settings.json"), map[string]bool{"unrelated": true})
		break
	}
	got, err := f.inspect(t)
	if err == nil || got.FixtureReady || !strings.Contains(err.Error(), "settings/readback seam is unavailable") {
		t.Fatalf("legacy refusal changed: %+v %v", got, err)
	}
}
