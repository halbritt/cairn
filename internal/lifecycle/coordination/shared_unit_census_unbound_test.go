package coordination

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// Selected by the bounded producer driver with actual installed R02 material.
// No fabricated receipt, API registration, native state or native client is used.
func TestR06UnboundCensusAssociationProcess(t *testing.T) {
	path := os.Getenv("CAIRN_R06_UNBOUND_FIXTURE")
	if path == "" {
		t.Skip("private genuine-producer fixture; run check_r06_agy_readback.py first")
	}
	var fixture struct {
		Catalog         WatchCensusPin
		Proposal        WatchCensusProposal
		Receipt, Config WatchCensusPin
		Root            string
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := censusDecode(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	var base WatchCensusCatalog
	raw, err = os.ReadFile(fixture.Catalog.Path)
	if err != nil {
		t.Fatal(err)
	}
	if err := censusDecode(raw, &base); err != nil {
		t.Fatal(err)
	}
	object := func(path string) map[string]any {
		t.Helper()
		raw, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		var decoded map[string]any
		decoder := json.NewDecoder(bytes.NewReader(raw))
		decoder.UseNumber()
		if err := decoder.Decode(&decoded); err != nil {
			t.Fatal(err)
		}
		return decoded
	}
	cli := func() *CLI {
		profile := stage2ServicesProfile()
		host := NewFileHost(FileHostOptions{Profile: profile})
		host.Call = func(context.Context, *value.Value, string, *value.Value, NativeCallOptions) (*value.Value, error) {
			t.Fatal("association inspection attempted an API call")
			return nil, nil
		}
		return &CLI{Host: host, Profile: profile}
	}
	for _, mutation := range []string{"none", "missing", "duplicate", "strict-catalog", "ambiguous", "tampered-pin", "external-source", "mixed-profile", "enabled-manifest", "missing-user", "source-pin", "positive-grant", "readback"} {
		t.Run(mutation, func(t *testing.T) {
			directory, err := os.MkdirTemp(fixture.Root, "unbound-unit-")
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = os.RemoveAll(directory) })
			var catalog WatchCensusCatalog
			if err := censusDecode(raw, &catalog); err != nil {
				t.Fatal(err)
			}
			receipt := object(fixture.Receipt.Path)
			want := ""
			switch mutation {
			case "missing":
				catalog.UnboundReceipts = nil
				want = "no selected receipt association"
			case "duplicate":
				catalog.UnboundReceipts = append(catalog.UnboundReceipts, fixture.Receipt)
				want = "duplicate or invalid census descriptor"
			case "strict-catalog":
				want = "duplicate key"
			case "ambiguous":
				copy := censusTestJSON(t, filepath.Join(directory, "receipt.json"), receipt)
				catalog.UnboundReceipts = append(catalog.UnboundReceipts, copy)
				want = "ambiguous unbound receipt/profile association"
			case "tampered-pin":
				copy := censusTestJSON(t, filepath.Join(directory, "receipt.json"), receipt)
				copy.SHA256 = strings.Repeat("0", 64)
				catalog.UnboundReceipts = []WatchCensusPin{copy}
				want = "census pin changed"
			case "external-source":
				receipt["snapshot_root"] = t.TempDir()
				catalog.UnboundReceipts = []WatchCensusPin{censusTestJSON(t, filepath.Join(directory, "receipt.json"), receipt)}
				want = "outside the census universe"
			case "source-pin":
				members := receipt["members"].(map[string]any)
				members["coordination.py"].(map[string]any)["sha256"] = strings.Repeat("0", 64)
				catalog.UnboundReceipts = []WatchCensusPin{censusTestJSON(t, filepath.Join(directory, "receipt.json"), receipt)}
				want = "census pin changed"
			case "missing-user":
				catalog.Selections = nil
				want = "unknown or omitted selected config directory"
			case "mixed-profile", "enabled-manifest", "readback":
				manifestPath := receipt["binding"].(map[string]any)["path"].(string)
				manifest := object(manifestPath)
				if mutation == "enabled-manifest" {
					manifest["enabled"] = true
					want = "private disabled /3 manifest"
				} else {
					memory := object(receipt["memory_config"].(map[string]any)["path"].(string))
					if mutation == "mixed-profile" {
						memory["repo"] = "foreign:unbound"
						want = "mixed unbound profile selectors"
					}
					memoryPin := censusTestJSON(t, filepath.Join(directory, "memory.json"), memory)
					desc := map[string]any{"path": memoryPin.Path, "sha256": memoryPin.SHA256}
					receipt["memory_config"], manifest["memory_config"] = desc, desc
				}
				manifestPin := censusTestJSON(t, filepath.Join(directory, "binding.json"), manifest)
				receipt["binding"] = map[string]any{"path": manifestPin.Path, "sha256": manifestPin.SHA256, "schema": "cairn.inbox-recall-binding/3"}
				catalog.Bindings = []WatchCensusPin{manifestPin}
				catalog.UnboundReceipts = []WatchCensusPin{censusTestJSON(t, filepath.Join(directory, "receipt.json"), receipt)}
			case "positive-grant":
				memory := object(receipt["memory_config"].(map[string]any)["path"].(string))
				ledger := filepath.Join(memory["state_dir"].(string), "unit-test.memory-budget.json")
				censusTestJSON(t, ledger, map[string]any{"inbox_activation": map[string]bool{"retained-turn": true}})
				t.Cleanup(func() { _ = os.Remove(ledger) })
				want = "retained positive activation grant requires its owner"
			}
			if mutation == "readback" {
				c := cli()
				r := &watchCensusRead{cli: c, local: &watchDrainRead{tokenPaths: map[string]bool{}}, catalog: catalog,
					files: map[string]string{}, images: map[string]bool{}, inodes: map[string]string{},
					report: WatchUnitCensus{Facts: map[string]string{}}, bindingPins: map[string]string{}}
				for _, path := range catalog.CredentialPaths {
					r.local.tokenPaths[path] = true
				}
				for _, pin := range catalog.Bindings {
					r.bindingPins[pin.Path] = pin.SHA256
				}
				_, _, err := r.unboundReceipt(catalog.UnboundReceipts[0])
				if err != nil {
					t.Fatal("genuine derived selection refused before readback", err)
				}
				memory := filepath.Join(directory, "memory.json")
				file, err := os.OpenFile(memory, os.O_APPEND|os.O_WRONLY, 0600)
				if err != nil {
					t.Fatal(err)
				}
				if _, err := file.WriteString(" "); err != nil {
					t.Fatal(err)
				}
				if err := file.Close(); err != nil {
					t.Fatal(err)
				}
				if err := r.readback(context.Background()); err == nil || !strings.Contains(err.Error(), "census pin changed") {
					t.Fatalf("changed tracked profile survived readback: %v", err)
				}
				return
			}
			pin := censusTestJSON(t, filepath.Join(directory, "catalog.json"), catalog)
			if mutation == "strict-catalog" {
				encoded, err := json.Marshal(catalog)
				if err != nil {
					t.Fatal(err)
				}
				extra, err := json.Marshal(map[string]string{"Root": fixture.Root})
				if err != nil {
					t.Fatal(err)
				}
				duplicate := append(append(encoded[:len(encoded)-1], ','), extra[1:]...)
				pin.SHA256 = writePrivateFile(t, pin.Path, duplicate, 0600)
			}
			result, err := cli().InspectWatchUnitCensus(context.Background(), pin, fixture.Proposal)
			if mutation == "none" {
				if err != nil || !result.FixtureReady || result.ExternalCoverage != "unknown" || result.NativeRefresh != "unobserved" {
					t.Fatalf("genuine empty-hook association refused: %+v %v", result, err)
				}
				for _, field := range []string{"binding", "binary", "memory_config"} {
					desc := receipt[field].(map[string]any)
					if result.Facts["file:"+desc["path"].(string)] != desc["sha256"] {
						t.Fatalf("missing tracked %s", field)
					}
				}
				for name, member := range receipt["members"].(map[string]any) {
					path := filepath.Join(receipt["snapshot_root"].(string), name)
					if result.Facts["file:"+path] != member.(map[string]any)["sha256"] {
						t.Fatalf("untracked source member %s", name)
					}
				}
			} else if err == nil || !strings.Contains(err.Error(), want) || result.FixtureReady {
				t.Fatalf("%s: unexpected association result %+v, %v; want %q", mutation, result, err, want)
			}
		})
	}
}
