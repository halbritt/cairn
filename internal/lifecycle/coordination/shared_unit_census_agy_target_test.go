package coordination

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// This entry consumes genuine maintained Go /3 producer bytes. It observes
// DATA only: no API, registration, native state, trace or Agy is fabricated.
// Pure current-readback codec tests elsewhere remain synthetic UNIT tests.
func TestAgyTargetObservationGenuineProcess(t *testing.T) {
	path := os.Getenv("CAIRN_R06_AGY_TARGET_FIXTURE")
	if path == "" {
		t.Skip("private genuine producer input; use --prepare-target-observation")
	}
	var fixture struct {
		Root, Account, Workspace, Installed    string
		CatalogA, CatalogB, ReceiptA, ReceiptB WatchCensusPin
		ConfigA, ConfigB, MemoryA, MemoryB     WatchCensusPin
		SelectionA, SelectionB                 WatchBindings
		A, B                                   WatchCensusAgyCurrentSelection
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	if fixture.ConfigA.SHA256 == fixture.ConfigB.SHA256 {
		t.Fatal("genuine target control needs different A/B profile hashes")
	}
	bSettings, err := os.ReadFile(fixture.B.Settings.Path)
	if err != nil {
		t.Fatal(err)
	}
	installed := WatchCensusPin{fixture.Installed, writePrivateFile(t, fixture.Installed, bSettings, 0600)}
	ctx := context.Background()
	newRead := func(t *testing.T, reverse bool) (*watchCensusRead, WatchCensusAgyHooks, WatchCensusAgyLayer) {
		t.Helper()
		directory, err := os.MkdirTemp(fixture.Root, "target-observation-")
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = os.RemoveAll(directory) })
		var catalog WatchCensusCatalog
		catalogPin, selection, currentPin, receipt := fixture.CatalogA, fixture.SelectionA, fixture.ConfigA, fixture.ReceiptB
		if reverse {
			catalogPin, selection, currentPin, receipt = fixture.CatalogB, fixture.SelectionB, fixture.ConfigB, fixture.ReceiptA
		}
		raw, err := os.ReadFile(catalogPin.Path)
		if err != nil {
			t.Fatal(err)
		}
		if err := censusDecode(raw, &catalog); err != nil {
			t.Fatal(err)
		}
		layer := WatchCensusAgyLayer{Layer: "account", Generation: fixture.B.Generation, Settings: fixture.B.Settings, Installed: installed}
		current := WatchCensusPin{filepath.Join(selection.Directory, "coordination.json"), currentPin.SHA256}
		selected := WatchCensusAgyHooks{Schema: "cairn.watch-agy-hooks/1", Harness: "agy", Account: fixture.Account,
			Workspace: fixture.Workspace, Config: current, Layers: []WatchCensusAgyLayer{layer,
				{Layer: "project", Installed: WatchCensusPin{Path: filepath.Join(fixture.Workspace, ".agents", "hooks.json")}}},
			LayerFiles: []WatchCensusPin{installed}}
		catalog.HookSelections = []WatchCensusPin{censusTestJSON(t, filepath.Join(directory, "hook-selection.json"), selected)}
		profile := stage2ServicesProfile()
		host := NewFileHost(FileHostOptions{Profile: profile})
		host.Call = func(context.Context, *value.Value, string, *value.Value, NativeCallOptions) (*value.Value, error) {
			t.Fatal("target observation attempted an API call")
			return nil, nil
		}
		r := &watchCensusRead{cli: &CLI{Host: host, Profile: profile},
			nodes: map[string]*WatchCensusMember{}, files: map[string]string{}, directories: map[string][]string{},
			inodes: map[string]string{}, images: map[string]bool{}, nodeBlockers: map[string][]string{},
			nodeGrants: map[string][]string{}, nodeDrainRequirements: map[string][]WatchCensusDrainRequirement{},
			sessionRefs: map[string]WatchCensusSession{}, callbackRecords: map[string]watchCensusCallbackRecord{},
			configPaths: map[*value.Value]string{}, control: &unitCensusScope{current: selection, target: &receipt},
			report: WatchUnitCensus{Facts: map[string]string{}, ExternalCoverage: "unknown", NativeRefresh: "unobserved"}}
		r.local = &watchDrainRead{cli: r.cli, facts: r.report.Facts, tokenPaths: map[string]bool{}, locks: map[string]string{}}
		if err := r.loadCatalog(ctx, censusTestJSON(t, filepath.Join(directory, "catalog.json"), catalog)); err != nil {
			t.Fatal(err)
		}
		if err := r.loadHookSelections(); err != nil {
			t.Fatal(err)
		}
		if err := r.members(ctx); err != nil {
			t.Fatal(err)
		}
		if err := r.verifyUnitTarget(); err != nil {
			t.Fatal(err)
		}
		return r, selected, layer
	}

	t.Run("A-stays-current-through-B-observation", func(t *testing.T) {
		r, selected, layer := newRead(t, false)
		before, err := json.Marshal(r.nodes)
		if err != nil {
			t.Fatal(err)
		}
		association := r.associations[selected.Config.Path]
		if err := r.agyLayerInventory(ctx, selected); err != nil {
			t.Fatal(err)
		}
		observed, err := r.agyProducedLayer(ctx, selected, layer)
		if err != nil {
			t.Fatal(err)
		}
		if observed.target == nil || observed.target.config != fixture.ConfigB || observed.target.receipt != fixture.ReceiptB ||
			observed.target.memory != fixture.MemoryB || len(observed.commands) != 2 || string(observed.settings) != string(bSettings) {
			t.Fatal("B observation lacks its exact actual target tuple")
		}
		readback := WatchCensusAgyCurrentReadback{A: fixture.A, B: fixture.B}
		if _, err := agyCurrentLayer(selected, readback, observed.commands, observed.target); err != nil {
			t.Fatal(err)
		}
		if _, _, err := r.agyCurrentA(ctx, selected, fixture.A, observed.target); err != nil {
			t.Fatal(err)
		}
		after, err := json.Marshal(r.nodes)
		if err != nil || string(before) != string(after) || r.associations[selected.Config.Path] != association ||
			len(r.nodes) != 1 || r.nodes[fixture.ConfigB.Path] != nil || !reflect.DeepEqual(r.control.current, fixture.SelectionA) ||
			!reflect.DeepEqual(r.catalog.UnboundReceipts, []WatchCensusPin{fixture.ReceiptA}) {
			t.Fatal("target was promoted into current nodes/receipt/association")
		}
		if err := r.readback(ctx); err != nil {
			t.Fatal("unchanged current/target failed final reread", err)
		}
	})
	t.Run("current-B-with-reverse-target-A-stays-strict", func(t *testing.T) {
		active := filepath.Join(fixture.SelectionA.Directory, "coordination.json")
		before, err := os.ReadFile(active)
		if err != nil {
			t.Fatal(err)
		}
		b, err := os.ReadFile(fixture.ConfigB.Path)
		if err != nil {
			t.Fatal(err)
		}
		// Unit setup only, not public publication or native reverse evidence.
		if err := os.Chmod(active, 0600); err != nil {
			t.Fatal(err)
		}
		writePrivateFile(t, active, b, 0600)
		t.Cleanup(func() { writePrivateFile(t, active, before, 0400) })
		r, selected, layer := newRead(t, true)
		observed, err := r.agyProducedLayer(ctx, selected, layer)
		if err != nil || observed.target != nil {
			t.Fatal("selected B was treated as its retained A target", err)
		}
		readback := WatchCensusAgyCurrentReadback{A: fixture.A, B: fixture.B}
		if _, err := agyCurrentLayer(selected, readback, observed.commands, observed.target); err != nil {
			t.Fatal(err)
		}
		readback.B.Config = fixture.ConfigA
		if _, err := agyCurrentLayer(selected, readback, observed.commands, nil); err == nil {
			t.Fatal("retained reverse target A substituted for current B")
		}
	})
	for _, mutation := range []string{"missing-target", "unverified-target", "wrong-target-receipt", "mixed-target-memory", "target-path-substitution", "wrong-current", "wrong-layer"} {
		t.Run(mutation, func(t *testing.T) {
			r, selected, layer := newRead(t, false)
			switch mutation {
			case "missing-target":
				r.target = nil
			case "unverified-target":
				r.report.target = nil
			case "wrong-target-receipt":
				r.control.target = &fixture.ReceiptA
			case "mixed-target-memory":
				r.target.memory = fixture.MemoryA
			case "target-path-substitution":
				r.target.profiles["coordination.json"] = fixture.ConfigA
			case "wrong-current":
				selected.Config = fixture.ConfigB
			case "wrong-layer":
				selected.Layers[0].Installed.Path = selected.Layers[1].Installed.Path
				if _, err := r.inspectAgyHooks(ctx, selected); err == nil {
					t.Fatal("wrong layer accepted")
				}
				return
			}
			if _, err := r.agyProducedLayer(ctx, selected, layer); err == nil {
				t.Fatal("mixed/unverified target observation accepted")
			}
		})
	}
	for _, member := range []string{"target-memory", "current-profile", "installed-hook", "target-profile", "target-receipt", "target-launcher"} {
		t.Run("final-drift-"+member, func(t *testing.T) {
			r, selected, layer := newRead(t, false)
			observed, err := r.agyProducedLayer(ctx, selected, layer)
			if err != nil {
				t.Fatal(err)
			}
			if _, _, err := r.agyCurrentA(ctx, selected, fixture.A, observed.target); err != nil {
				t.Fatal(err)
			}
			path := map[string]string{"target-memory": fixture.MemoryB.Path, "current-profile": selected.Config.Path,
				"installed-hook": fixture.Installed, "target-profile": fixture.ConfigB.Path,
				"target-receipt": fixture.ReceiptB.Path, "target-launcher": fixture.B.Launcher.Path}[member]
			raw, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			info, err := os.Stat(path)
			if err != nil {
				t.Fatal(err)
			}
			if err := os.Chmod(path, 0600); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(path, append(raw, ' '), 0600); err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() {
				_ = os.WriteFile(path, raw, 0600)
				_ = os.Chmod(path, info.Mode().Perm())
			})
			if err := r.readback(ctx); err == nil {
				t.Fatal("current/target/hook drift survived final reread")
			}
		})
	}
}
