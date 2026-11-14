package coordination

import (
	"bytes"
	"context"
	"encoding/json"
	"github.com/halbritt/cairn/internal/lifecycle/install"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestUnitControlTargetReceiptLiteralShape(t *testing.T) {
	root := privateCurrentnessDirectory(t)
	digest := strings.Repeat("a", 64)
	plan := map[string]any{"schema": "cairn.unit-control/2", "current": map[string]any{"directory": "/private/current", "pins": map[string]string{"one.json": digest}}, "next": []any{map[string]any{"name": "one.json", "path": "/private/target/coordination-0.json", "sha256": digest}}, "retained": "/private/retained", "census": map[string]any{"Catalog": map[string]string{"Path": "/private/catalog.json", "SHA256": digest}, "Proposal": map[string]any{"Bindings": []any{map[string]any{"Directory": "/private/current", "Pins": map[string]string{"one.json": digest}}}, "Sessions": []string{}, "Callbacks": []string{}}, "Retained": []any{}, "TargetReceipt": map[string]string{"Path": "/private/target/receipt.json", "SHA256": digest}}}
	pin := censusTestJSON(t, filepath.Join(root, "plan.json"), plan)
	if _, _, err := readUnitControlPlan(pin.Path, pin.SHA256); err != nil {
		t.Fatal("literal target data reference refused", err)
	}
}

// These genuine produced bytes exercise public control plumbing only. The
// maintained process driver separately runs concrete authenticated API reads.
func TestR06UnboundTargetPlumbingProcess(t *testing.T) {
	path := os.Getenv("CAIRN_R06_TARGET_FIXTURE")
	if path == "" {
		t.Skip("requires NEW genuine target producer input")
	}
	var f struct {
		Root, ConfigRoot, Credential                                               string
		CatalogA, CatalogB, ReceiptA, ReceiptB, ConfigA, ConfigB, MemoryA, MemoryB WatchCensusPin
		SelectionA, SelectionB                                                     WatchBindings
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := censusDecode(raw, &f); err != nil {
		t.Fatal(err)
	}
	cli, calls := unboundDrainReadCLI(t)
	ctx := context.Background()
	lock, err := os.Stat(filepath.Join(f.SelectionA.Directory, ".watch.lock"))
	if err != nil {
		t.Fatal(err)
	}
	original := unboundControlSnapshot(t, f.Root, f.Credential)
	invoke := func(label, op string, current WatchBindings, catalog, target WatchCensusPin, next []install.File, retained []WatchBindings, destination string) UnitControlResult {
		t.Helper()
		plan := map[string]any{"schema": "cairn.unit-control/2", "current": map[string]any{"directory": current.Directory, "pins": current.Pins}, "next": next, "retained": destination, "census": map[string]any{"Catalog": catalog, "Proposal": WatchCensusProposal{Bindings: []WatchBindings{current}, Sessions: []string{}, Callbacks: []string{}}, "Retained": retained, "TargetReceipt": target}}
		pin := censusTestJSON(t, filepath.Join(filepath.Dir(f.Root), label+".plan.json"), plan)
		var out bytes.Buffer
		code := cli.RunUnitControl(ctx, []string{op, "--plan", pin.Path, "--sha256", pin.SHA256}, &out)
		var result UnitControlResult
		if err := json.Unmarshal(out.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		if code != 0 || !result.Inspected || !result.Exchange.Published {
			t.Fatalf("%s failed: code=%d error=%q", label, code, result.Error)
		}
		return result
	}
	name := "coordination.json"
	aDir := filepath.Join(f.ConfigRoot, "retained-A")
	bDir := filepath.Join(f.ConfigRoot, "retained-B")
	forward := invoke("forward", "switch", f.SelectionA, f.CatalogA, f.ReceiptB, []install.File{{Name: name, Path: f.ConfigB.Path, SHA256: f.ConfigB.SHA256}}, []WatchBindings{}, aDir)
	if !reflect.DeepEqual(forward.Exchange.Active.Pins, f.SelectionB.Pins) || !reflect.DeepEqual(forward.Exchange.Retained.Pins, f.SelectionA.Pins) {
		t.Fatal("forward pins differ from actual produced generations")
	}
	reverse := invoke("reverse", "reverse", f.SelectionB, f.CatalogB, f.ReceiptA, []install.File{{Name: name, Path: filepath.Join(aDir, name), SHA256: f.ConfigA.SHA256}}, []WatchBindings{{Directory: aDir, Pins: f.SelectionA.Pins}}, bDir)
	if !reflect.DeepEqual(reverse.Exchange.Active.Pins, f.SelectionA.Pins) || !reflect.DeepEqual(reverse.Exchange.Retained.Pins, f.SelectionB.Pins) {
		t.Fatal("reverse did not restore retained Go /3 A")
	}
	for _, directory := range []string{f.SelectionA.Directory, aDir, bDir} {
		info, err := os.Stat(filepath.Join(directory, ".watch.lock"))
		if err != nil || !os.SameFile(lock, info) {
			t.Fatal("watch-lock identity changed", directory, err)
		}
	}
	after := unboundControlSnapshot(t, f.Root, f.Credential)
	protected := func(rows map[string]string) map[string]string {
		result := map[string]string{}
		for p, v := range rows {
			if p != f.Root && p != f.ConfigRoot && !strings.HasPrefix(p, f.ConfigRoot+string(os.PathSeparator)) {
				result[p] = v
			}
		}
		return result
	}
	if !reflect.DeepEqual(protected(original), protected(after)) {
		t.Fatal("roundtrip changed protected profile/source/credential/state data")
	}
	if *calls != 8 {
		t.Fatalf("unexpected read-only status boundary count: %d", *calls)
	}
}

func TestUnitControlTargetReferenceShapeAndSnapshot(t *testing.T) {
	digest := strings.Repeat("a", 64)
	base := map[string]any{"schema": "cairn.unit-control/2", "current": map[string]any{"directory": "/private/current", "pins": map[string]string{"one.json": digest}}, "next": []any{map[string]any{"name": "one.json", "path": "/private/next.json", "sha256": digest}}, "retained": "/private/retained", "census": map[string]any{"Catalog": map[string]string{"Path": "/private/catalog.json", "SHA256": digest}, "Proposal": map[string]any{"Bindings": []any{map[string]any{"Directory": "/private/current", "Pins": map[string]string{"one.json": digest}}}, "Sessions": []string{}, "Callbacks": []string{}}, "Retained": []any{}}}
	for _, row := range []struct {
		name   string
		target any
		want   bool
	}{
		{"absent", nil, true}, {"pin", map[string]any{"Path": "/private/receipt.json", "SHA256": digest}, true},
		{"null", nil, false}, {"array", []any{}, false}, {"missing-path", map[string]any{"SHA256": digest}, false},
		{"wrong-case", map[string]any{"path": "/private/receipt.json", "SHA256": digest}, false},
		{"unknown-authority", map[string]any{"Path": "/private/receipt.json", "SHA256": digest, "ready": true}, false},
		{"noncanonical", map[string]any{"Path": "/private/../receipt.json", "SHA256": digest}, false},
		{"wrong-digest", map[string]any{"Path": "/private/receipt.json", "SHA256": "unknown"}, false},
	} {
		t.Run(row.name, func(t *testing.T) {
			raw, err := json.Marshal(base)
			if err != nil {
				t.Fatal(err)
			}
			var input map[string]any
			if err := json.Unmarshal(raw, &input); err != nil {
				t.Fatal(err)
			}
			if row.name != "absent" {
				input["census"].(map[string]any)["TargetReceipt"] = row.target
			}
			pin := censusTestJSON(t, filepath.Join(privateCurrentnessDirectory(t), "plan.json"), input)
			_, _, err = readUnitControlPlan(pin.Path, pin.SHA256)
			if (err == nil) != row.want {
				t.Fatalf("shape %s: error=%v want accepted=%v", row.name, err, row.want)
			}
		})
	}
	input := &UnitControlCensus{TargetReceipt: &WatchCensusPin{Path: "/private/original", SHA256: digest}, Proposal: WatchCensusProposal{Bindings: []WatchBindings{{Directory: "/private/current", Pins: map[string]string{"one.json": digest}}}}}
	copy := cloneUnitControlCensus(input)
	input.TargetReceipt.Path = "/private/replaced"
	input.Proposal.Bindings[0].Pins["one.json"] = "changed"
	if copy.TargetReceipt.Path != "/private/original" || copy.Proposal.Bindings[0].Pins["one.json"] != digest {
		t.Fatal("target/current snapshot shares caller mutable fields")
	}
}

func TestAgyTargetRoundtripLegacyScope(t *testing.T) {
	digest := strings.Repeat("a", 64)
	legacy := map[string]WatchCensusPin{"binding.json": {Path: "/private/legacy/binding.json", SHA256: digest}, "retained-argv.json": {Path: "/private/legacy/retained-argv.json", SHA256: digest}}
	if err := agyLegacyLifecycleRefusal(legacy); err == nil || !strings.Contains(err.Error(), "retained /2") {
		t.Fatal("legacy shape was treated as Go /3 refresh", err)
	}
	goSelection := map[string]WatchCensusPin{"binding.json": {Path: "/private/go/binding.json", SHA256: digest}, "receipt.json": {Path: "/private/go/receipt.json", SHA256: digest}, "selection.json": {Path: "/private/go/selection.json", SHA256: digest}}
	if err := agyLegacyLifecycleRefusal(goSelection); err != nil {
		t.Fatal(err)
	}
}
