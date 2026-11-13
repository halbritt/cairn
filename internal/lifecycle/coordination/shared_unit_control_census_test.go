package coordination

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/install"
)

func TestUnitCensusPlanRefusesUnknownCoverage(t *testing.T) {
	for _, extra := range []string{``, `,"census":null`, `,"callbacks":[]`, `,"census":{"Catalog":{},"Proposal":{},"Retained":[]}`, `,"census":{"Catalog":{"Path":"/x","SHA256":"` + strings.Repeat("a", 64) + `"},"Proposal":{"Bindings":[],"Sessions":null,"Callbacks":[]},"Retained":[]}`} {
		root := t.TempDir()
		if err := os.Chmod(root, 0700); err != nil {
			t.Fatal(err)
		}
		raw := []byte(`{"schema":"cairn.unit-control/2","current":{"directory":"/x","pins":{"one.json":"` + strings.Repeat("a", 64) + `"}},"next":[{}],"retained":"/y"` + extra + `}`)
		path := filepath.Join(root, "plan.json")
		if err := os.WriteFile(path, raw, 0600); err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256(raw)
		if _, _, err := readUnitControlPlan(path, hex.EncodeToString(sum[:])); err == nil {
			t.Fatal("unknown census plan accepted")
		}
	}
}

func TestUnitCensusOwnPreparationStillPinsAllOtherFacts(t *testing.T) {
	f := newCensusFixture(t)
	current := f.proposal.Bindings[0]
	writePrivateFile(t, filepath.Join(current.Directory, ".watch.lock"), nil, 0600)
	profile := stage2ServicesProfile()
	cli := &CLI{Profile: profile, Host: NewFileHost(FileHostOptions{Profile: profile})}
	catalog := censusTestJSON(t, filepath.Join(f.root, "catalog.json"), f.catalog)
	report, err := cli.inspectWatchUnitCensus(context.Background(), catalog, f.proposal, &unitCensusScope{current: current})
	if err != nil {
		t.Fatal(err)
	}
	var next []install.File
	for name, pin := range current.Pins {
		next = append(next, install.File{Name: name, Path: filepath.Join(current.Directory, name), SHA256: pin})
	}
	retained := filepath.Join(f.catalog.ConfigRoot, "retained")
	swap, err := openWatchBindingsSwap(context.Background(), current, retained, next)
	if err != nil {
		t.Fatal(err)
	}
	defer swap.parent.Close()
	defer mustRelease(t, swap.lease)
	if err := swap.prepare(); err != nil {
		t.Fatal(err)
	}
	if _, err := cli.InspectWatchUnitCensus(context.Background(), catalog, f.proposal); err == nil {
		t.Fatal("unexplained preparation accepted by ordinary census")
	}
	plan := map[string]any{"schema": "cairn.unit-control/2", "current": map[string]any{"directory": current.Directory, "pins": current.Pins}, "next": next, "retained": retained, "census": map[string]any{"Catalog": catalog, "Proposal": map[string]any{"Bindings": f.proposal.Bindings, "Sessions": f.proposal.Sessions, "Callbacks": []string{}}, "Retained": []WatchBindings{}}}
	raw, err := json.Marshal(plan)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(f.root, "control-plan.json")
	pin := writePrivateFile(t, path, raw, 0600)
	receipt := &unitCensusDrain{cli: cli, input: UnitControlCensus{Catalog: catalog, Proposal: f.proposal}, current: current, initial: report, planPath: path, planPin: pin}
	if err := receipt.recheck(context.Background(), swap); err != nil {
		t.Fatal(err)
	}
	if receipt.final == nil || len(receipt.final.RetainedCopies) != 1 {
		t.Fatal("raw preparation observation lost")
	}
	// A catalog change is never normalized as a preparation effect.
	if err := os.WriteFile(catalog.Path, []byte("{}\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := receipt.recheck(context.Background(), swap); err == nil || !strings.Contains(err.Error(), "invalidated") {
		t.Fatalf("catalog mutation accepted: %v", err)
	}
}
