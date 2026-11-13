package coordination

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func TestWatchCensusClaudeLayerInventory(t *testing.T) {
	for _, mutation := range []string{"none", "extra-file", "changed-file", "extra-hook", "missing-file", "external-pin", "alias", "ancestor-hook"} {
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
			case "ancestor-hook":
				censusTestJSON(t, filepath.Join(f.root, ".claude", "settings.json"), map[string]any{"hooks": map[string]any{"UserPromptSubmit": []any{}}})
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
	for _, mutation := range []string{"harness", "layer", "unknown-config", "duplicate", "foreign-account", "external-coverage", "foreign-workspace", "foreign-session"} {
		t.Run(mutation, func(t *testing.T) {
			f := censusFixtureAt(t, t.TempDir(), "claude")
			path := filepath.Join(f.catalog.Selections[0].Directory, "one.json")
			selected := WatchCensusClaudeHooks{Schema: "cairn.watch-claude-hooks/1", Harness: "claude", Layer: "flagSettings", Config: WatchCensusPin{path, f.catalog.Selections[0].Pins["one.json"]}, Session: f.catalog.Sessions[0], Account: f.configs[path]["config_home"].(string), Workspace: filepath.Join(f.root, "workspaces", "a-one.json")}
			selected.Generation, selected.Settings, selected.Readback = selected.Session, selected.Session, selected.Session
			switch mutation {
			case "external-coverage":
				f.catalog.Scope = "external-unknown"
			case "harness":
				selected.Harness = "codex"
			case "layer":
				selected.Layer = "accountSettings"
			case "unknown-config":
				selected.Config = f.catalog.Sessions[0]
			case "foreign-account":
				selected.Account = "/unknown-account"
			case "foreign-workspace":
				selected.Workspace = filepath.Join(f.root, "workspaces", "a-two.json")
			case "foreign-session":
				selected.Session = f.catalog.Sessions[2]
			}
			pin := censusTestJSON(t, filepath.Join(f.root, "native-hooks.json"), selected)
			f.catalog.HookSelections = []WatchCensusPin{pin}
			if mutation == "duplicate" {
				f.catalog.HookSelections = append(f.catalog.HookSelections, pin)
			}
			got, err := f.inspect(t)
			want := map[string]string{"harness": "unsupported native harness/layer", "layer": "unsupported native harness/layer", "unknown-config": "unknown or mixed unit/config", "duplicate": "duplicate or external native hook selection", "foreign-account": "outside the finite universe", "external-coverage": "only for a disposable fixture", "foreign-workspace": "session/workspace/config identity disagrees", "foreign-session": "session/workspace/config identity disagrees"}[mutation]
			if err == nil || !strings.Contains(err.Error(), want) || got.FixtureReady || len(got.HookObservations) != 0 {
				t.Fatalf("invalid selection admitted: %+v %v", got, err)
			}
		})
	}
}

func TestWatchCensusProducedSessionIdentity(t *testing.T) {
	for _, mutation := range []string{"none", "opaque-outer", "missing-nested", "outer-only", "outer-rescue", "foreign-agent", "foreign-workspace"} {
		t.Run(mutation, func(t *testing.T) {
			f := newCensusFixture(t)
			pin := f.catalog.Sessions[0]
			raw, err := os.ReadFile(pin.Path)
			if err != nil {
				t.Fatal(err)
			}
			var state map[string]any
			if err := json.Unmarshal(raw, &state); err != nil {
				t.Fatal(err)
			}
			agent := state["agent"].(map[string]any)
			native := agent["native_session_id"].(string)
			switch mutation {
			case "opaque-outer":
				state["native_session_id"] = "unrelated opaque extension"
			case "missing-nested":
				delete(agent, "native_session_id")
			case "outer-only":
				delete(agent, "native_session_id")
				state["native_session_id"] = native
			case "outer-rescue":
				agent["native_session_id"] = "foreign-native"
				state["native_session_id"] = native
			case "foreign-agent":
				other, err := os.ReadFile(f.catalog.Sessions[1].Path)
				if err != nil {
					t.Fatal(err)
				}
				var foreign map[string]any
				if err := json.Unmarshal(other, &foreign); err != nil {
					t.Fatal(err)
				}
				state["agent"] = foreign["agent"]
			case "foreign-workspace":
				agent["metadata"].(map[string]any)["workspace"] = "/foreign-workspace"
			}
			f.catalog.Sessions[0] = censusTestJSON(t, pin.Path, state)
			before := censusTestSnapshot(t, f.root)
			got, err := f.inspect(t)
			positive := mutation == "none" || mutation == "opaque-outer"
			if positive {
				if err != nil || !got.FixtureReady {
					t.Fatalf("producer state refused: %+v %v", got, err)
				}
				found := false
				for _, session := range got.Sessions {
					if session.Path == pin.Path {
						found = session.NativeSession == native
					}
				}
				if !found {
					t.Fatal("census lost the producer's nested identity")
				}
			} else if err == nil || got.FixtureReady {
				t.Fatalf("foreign/missing nested identity admitted: %+v %v", got, err)
			}
			after := censusTestSnapshot(t, f.root)
			// inspect creates only its selected catalog input, never state bytes.
			delete(before, filepath.Join(f.root, "catalog.json"))
			delete(after, filepath.Join(f.root, "catalog.json"))
			if before[pin.Path] != after[pin.Path] {
				t.Fatal("inspection rewrote producer state")
			}
		})
	}
}

func TestWatchCensusProducedSessionReadbackMismatch(t *testing.T) {
	f := newCensusFixture(t)
	if err := os.Chmod(f.root, 0700); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(f.catalog.Sessions[0].Path)
	if err != nil {
		t.Fatal(err)
	}
	state, err := value.DecodeTextWithObjectPolicy(raw, "utf-8", 0, value.DuplicateReject)
	if err != nil {
		t.Fatal(err)
	}
	native := nativeText(state.Get("agent").Get("native_session_id"))
	// A tempting outer value cannot rescue a readback for a different agent.
	state.Set("native_session_id", value.Text("different-native"))
	selected := WatchCensusClaudeHooks{Settings: f.catalog.Sessions[0]}
	selected.Readback = censusTestJSON(t, filepath.Join(f.root, "mismatched-readback.json"), WatchCensusClaudeReadback{Schema: "cairn.watch-claude-readback/1", Harness: "claude", Layer: "flagSettings", NativeSession: "different-native", Settings: selected.Settings})
	r := &watchCensusRead{catalog: f.catalog, files: map[string]string{}, directories: map[string][]string{}, inodes: map[string]string{}, images: map[string]bool{}, report: WatchUnitCensus{Facts: map[string]string{}}, local: &watchDrainRead{tokenPaths: map[string]bool{}}}
	if err := r.claudeHookReadback(selected, state, nil, nil); err == nil || !strings.Contains(err.Error(), "mismatched native hook readback") {
		t.Fatalf("readback gained clearance over nested identity %q: %v", native, err)
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
