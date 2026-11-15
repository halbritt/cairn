package coordination

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/install"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"golang.org/x/sys/unix"
)

func censusVerifiedRead(t *testing.T, root string) *watchCensusRead {
	t.Helper()
	pass := newUnitVerifiedFiles(context.Background())
	t.Cleanup(func() {
		if err := pass.Close(); err != nil {
			t.Error(err)
		}
	})
	profile := stage2ServicesProfile()
	cli := &CLI{Profile: profile, Host: NewFileHost(FileHostOptions{Profile: profile})}
	facts := map[string]string{}
	return &watchCensusRead{cli: cli, catalog: WatchCensusCatalog{Root: root},
		local: &watchDrainRead{cli: cli, verified: pass, facts: facts, tokenPaths: map[string]bool{}},
		files: map[string]string{}, images: map[string]bool{}, inodes: map[string]string{},
		directories: map[string][]string{}, report: WatchUnitCensus{Facts: facts}}
}

func TestUnitCensusPassReusesJSONAndImageBuildReads(t *testing.T) {
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatal(err)
	}
	root := unitVerifiedDirectory(t, filepath.Join(home, ".cache"))
	r := censusVerifiedRead(t, root)
	pin := censusTestJSON(t, filepath.Join(root, "selected.json"), map[string]any{"selected": "original"})
	image := WatchCensusPin{Path: filepath.Join(root, "image")}
	image.SHA256 = writePrivateFile(t, image.Path, []byte("actual image bytes"), 0500)
	for range 8 {
		if _, err := r.pin(pin, false); err != nil {
			t.Fatal(err)
		}
		if v, err := r.jsonFile(pin.Path); err != nil || !value.SameText(v.Get("selected"), "original") {
			t.Fatal("JSON view of the same verified file changed", err)
		}
		if _, err := r.pin(image, true); err != nil {
			t.Fatal(err)
		}
	}
	if r.local.verified.hashReads != 2 {
		t.Fatalf("two distinct files need two actual build hashes, got %d", r.local.verified.hashReads)
	}
	if err := r.readback(context.Background()); err != nil || r.local.verified.hashReads != 4 {
		t.Fatalf("fresh readback must hash both files again: %d %v", r.local.verified.hashReads, err)
	}
}

func TestUnitCensusPassReadbackRefusesUnchangedStatBytes(t *testing.T) {
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatal(err)
	}
	for _, base := range []string{home, filepath.Join(home, ".cache")} {
		t.Run(filepath.Base(base), func(t *testing.T) {
			root := unitVerifiedDirectory(t, base)
			r := censusVerifiedRead(t, root)
			pin := censusTestJSON(t, filepath.Join(root, "selected.json"), map[string]any{"selected": "original"})
			writer, err := os.OpenFile(pin.Path, os.O_RDWR, 0)
			if err != nil {
				t.Fatal(err)
			}
			defer writer.Close()
			info, err := writer.Stat()
			if err != nil {
				t.Fatal(err)
			}
			mapped, err := unix.Mmap(int(writer.Fd()), 0, int(info.Size()), unix.PROT_READ|unix.PROT_WRITE, unix.MAP_SHARED)
			if err != nil {
				t.Fatal(err)
			}
			defer unix.Munmap(mapped)
			mapped[0] = '{'
			if _, err := r.pin(pin, false); err != nil {
				t.Fatal(err)
			}
			before, err := unitVerifiedPathStat(pin.Path)
			if err != nil {
				t.Fatal(err)
			}
			mapped[0] = '!'
			after, err := unitVerifiedPathStat(pin.Path)
			if err != nil || !unitVerifiedSame(before, after) {
				t.Fatal("unchanged-stat fixture was not established", err)
			}
			if err := r.readback(context.Background()); err == nil || !strings.Contains(err.Error(), "census pin changed") || errors.Is(err, context.DeadlineExceeded) {
				t.Fatalf("census readback accepted actual changed bytes: %v", err)
			}
		})
	}
}

func TestUnitCensusObservationKeepsOneDescriptorOwner(t *testing.T) {
	f := newCensusFixture(t)
	profile := stage2ServicesProfile()
	cli := &CLI{Profile: profile, Host: NewFileHost(FileHostOptions{Profile: profile})}
	catalog := censusTestJSON(t, filepath.Join(f.root, "catalog.json"), f.catalog)
	pass, err := cli.openWatchUnitCensus(context.Background(), catalog, f.proposal, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer pass.close()
	entry := pass.local.verified.files[catalog.Path]
	if entry == nil {
		t.Fatal("census builder did not retain its actual file descriptor")
	}
	before := pass.local.verified.hashReads
	for range 2 {
		if err := pass.refresh(context.Background()); err != nil || pass.local.verified.files[catalog.Path] != entry {
			t.Fatal("refresh replaced the observation's descriptor owner", err)
		}
	}
	if pass.local.verified.hashReads <= before {
		t.Fatal("reusing an observation omitted fresh byte readback")
	}
	if err := pass.close(); err != nil {
		t.Fatal(err)
	}
	if _, err := entry.file.Stat(); !errors.Is(err, os.ErrClosed) {
		t.Fatal("closed observation leaked its descriptor", err)
	}
	if err := pass.refresh(context.Background()); err == nil {
		t.Fatal("closed observation was reused across a freshness boundary")
	}
}

func TestUnitCensusFinalObservationIsOneShotAndSwapBound(t *testing.T) {
	f := newCensusFixture(t)
	ctx := context.Background()
	current := f.proposal.Bindings[0]
	writePrivateFile(t, filepath.Join(current.Directory, ".watch.lock"), nil, 0600)
	profile := stage2ServicesProfile()
	cli := &CLI{Profile: profile, Host: NewFileHost(FileHostOptions{Profile: profile})}
	catalog := censusTestJSON(t, filepath.Join(f.root, "catalog.json"), f.catalog)
	initial, err := cli.inspectWatchUnitCensus(ctx, catalog, f.proposal, &unitCensusScope{current: current})
	if err != nil {
		t.Fatal(err)
	}
	var next []install.File
	for name, pin := range current.Pins {
		next = append(next, install.File{Name: name, Path: filepath.Join(current.Directory, name), SHA256: pin})
	}
	retained := filepath.Join(f.catalog.ConfigRoot, "retained")
	swap, err := openWatchBindingsSwap(ctx, current, retained, next)
	if err != nil {
		t.Fatal(err)
	}
	defer swap.parent.Close()
	defer mustRelease(t, swap.lease)
	if err := swap.prepare(); err != nil {
		t.Fatal(err)
	}
	plan := map[string]any{"schema": "cairn.unit-control/2", "current": map[string]any{"directory": current.Directory, "pins": current.Pins}, "next": next, "retained": retained, "census": map[string]any{"Catalog": catalog, "Proposal": map[string]any{"Bindings": f.proposal.Bindings, "Sessions": f.proposal.Sessions, "Callbacks": []string{}}, "Retained": []WatchBindings{}}}
	raw, err := json.Marshal(plan)
	if err != nil {
		t.Fatal(err)
	}
	planPath := filepath.Join(f.root, "plan.json")
	planPin := writePrivateFile(t, planPath, raw, 0600)
	prepared := cloneCensusSelection(swap.selected)
	prepared.Directory = retained
	final, err := cli.inspectWatchUnitCensus(ctx, catalog, f.proposal, &unitCensusScope{current: current, retained: []WatchBindings{prepared}})
	if err != nil {
		t.Fatal(err)
	}
	source := &unitCensusDrain{cli: cli, input: cloneUnitControlCensus(&UnitControlCensus{Catalog: catalog, Proposal: f.proposal}), current: current, initial: initial, planPath: planPath, planPin: planPin, pending: &unitCensusFinalObservation{swap: swap, report: final}}
	if err := source.recheck(ctx, swap); err != nil || source.pending != nil {
		t.Fatal("exact swap observation was not consumed once", err)
	}
	if err := os.WriteFile(catalog.Path, []byte("{}\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := source.recheck(ctx, swap); err == nil {
		t.Fatal("standalone second recheck replayed a prior observation")
	}
	source.pending = &unitCensusFinalObservation{swap: swap, report: final}
	otherSwap := *swap
	if err := source.recheck(ctx, &otherSwap); err == nil || source.pending != nil {
		t.Fatal("another swap borrowed the handoff or failed to consume it", err)
	}
}
