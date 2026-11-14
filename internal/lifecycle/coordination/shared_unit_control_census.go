package coordination

import (
	"context"
	"errors"
	"fmt"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"syscall"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// Retained names complete immutable copies, not another selected executor.
// The private swap may add only its own fully validated preparation.
type UnitControlCensus struct {
	Catalog  WatchCensusPin
	Proposal WatchCensusProposal
	Retained []WatchBindings
}

type unitCensusScope struct {
	current  WatchBindings
	retained []WatchBindings
}
type unitCensusDrain struct {
	cli               *CLI
	input             UnitControlCensus
	current           WatchBindings
	initial           WatchUnitCensus
	final             *WatchUnitCensus
	planPath, planPin string
	drain             *WatchDrainReceipt
	associationFacts  map[string]string
}

func validateUnitCensusInput(v *value.Value, input *UnitControlCensus) error {
	if err := unitControlFields(v, "Catalog", "Proposal", "Retained"); err != nil {
		return err
	}
	if input == nil {
		return errors.New("explicit unit census input required")
	}
	if err := unitControlFields(v.Get("Catalog"), "Path", "SHA256"); err != nil {
		return err
	}
	if !compatibilityCanonical(input.Catalog.Path) || !censusDigest(input.Catalog.SHA256) {
		return errors.New("unit census catalog requires canonical pinned bytes")
	}
	p := v.Get("Proposal")
	if err := unitControlFields(p, "Bindings", "Sessions", "Callbacks"); err != nil {
		return err
	}
	for key, limit := range map[string]int{"Bindings": 1, "Sessions": 1024, "Callbacks": 256} {
		a := p.Get(key)
		if a == nil || a.Kind != value.Array || len(a.Items) > limit {
			return fmt.Errorf("unit census explicit bounded %s array required", key)
		}
	}
	copies := v.Get("Retained")
	if copies == nil || copies.Kind != value.Array || len(copies.Items) > 32 {
		return errors.New("unit census explicit bounded Retained array required")
	}
	for _, a := range []*value.Value{p.Get("Bindings"), copies} {
		for _, s := range a.Items {
			if err := unitControlFields(s, "Directory", "Pins"); err != nil {
				return err
			}
		}
	}
	if len(input.Proposal.Bindings) != 1 {
		return errors.New("unit census atomic exchange supports exactly one selected directory")
	}
	return nil
}

func cloneCensusSelection(s WatchBindings) WatchBindings {
	return WatchBindings{Directory: s.Directory, Pins: maps.Clone(s.Pins)}
}

func (c *CLI) captureUnitCensusDrain(ctx context.Context, current WatchBindings, input *UnitControlCensus) (*WatchDrainReceipt, WatchUnitCensus, error) {
	unknown := WatchUnitCensus{ExternalCoverage: "unknown", NativeRefresh: "unobserved"}
	proposed := input.Proposal.Bindings[0]
	if proposed.Directory != current.Directory || !maps.Equal(proposed.Pins, current.Pins) {
		return nil, unknown, errors.New("unit census proposal differs from actual current directory/pins")
	}
	if err := unitCensusExistingLock(current.Directory); err != nil {
		return nil, unknown, err
	}
	scope := &unitCensusScope{current: current, retained: input.Retained}
	report, err := c.inspectWatchUnitCensus(ctx, input.Catalog, input.Proposal, scope)
	if err != nil && !censusOnlyNeedsD2(err) {
		return nil, report, err
	}
	callbacks := make([]*value.Value, 0, len(report.Callbacks))
	for _, callback := range report.Callbacks {
		callbacks = append(callbacks, censusNativeProcess(callback.Process))
	}
	bound := *input
	bound.Proposal.Bindings = []WatchBindings{cloneCensusSelection(proposed)}
	bound.Proposal.Sessions = slices.Clone(input.Proposal.Sessions)
	bound.Proposal.Callbacks = slices.Clone(input.Proposal.Callbacks)
	bound.Retained = make([]WatchBindings, len(input.Retained))
	for i, s := range input.Retained {
		bound.Retained[i] = cloneCensusSelection(s)
	}
	initial := report
	initial.Facts = maps.Clone(report.Facts)
	source := &unitCensusDrain{cli: c, input: bound, current: cloneCensusSelection(current), initial: initial}
	members, err := source.unboundDrainMembers(report)
	if err != nil {
		return nil, report, err
	}
	if len(members) != 0 {
		_, facts, err := source.observeDrainAssociations(ctx, nil)
		if err != nil {
			return nil, report, err
		}
		source.associationFacts = maps.Clone(facts)
	}
	receipt, err := c.captureWatchDrainCensus(ctx, current, callbacks, source)
	if err != nil {
		return nil, report, err
	}
	if err := satisfyCensusD2(&report, receipt.d2Sessions); err != nil {
		return nil, report, err
	}
	source.drain = receipt
	return receipt, report, nil
}

func unitCensusExistingLock(directory string) error {
	info, err := os.Lstat(filepath.Join(directory, ".watch.lock"))
	if err != nil {
		return fmt.Errorf("unit census requires an existing watcher lock: %w", err)
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || stat.Uid != uint32(os.Getuid()) || info.Mode() != 0600 {
		return errors.New("unit census watcher lock mode/type is unknown")
	}
	return nil
}

func (r *watchCensusRead) controlCopies(expected *[]string) error {
	s := r.control
	if err := unitControlDirectory(s.current.Directory); err != nil {
		return err
	}
	if err := unitCensusExistingLock(s.current.Directory); err != nil {
		return err
	}
	if filepath.Dir(s.current.Directory) != r.catalog.ConfigRoot {
		return errors.New("unit census current directory is outside catalog selection root")
	}
	lock, err := os.Lstat(filepath.Join(s.current.Directory, ".watch.lock"))
	if err != nil {
		return err
	}
	paths := []string{s.current.Directory, filepath.Join(s.current.Directory, ".watch.lock")}
	for name := range s.current.Pins {
		paths = append(paths, filepath.Join(s.current.Directory, name))
	}
	for _, path := range paths {
		info, err := os.Lstat(path)
		if err != nil {
			return err
		}
		stat := info.Sys().(*syscall.Stat_t)
		r.report.Facts["selected-identity:"+path] = fmt.Sprintf("%d:%d:%d:%d", stat.Dev, stat.Ino, stat.Mode, stat.Uid)
	}
	for _, copy := range s.retained {
		if len(copy.Pins) != len(s.current.Pins) {
			return errors.New("unit census retained copy must keep every binding name")
		}
		name := filepath.Base(copy.Directory)
		if filepath.Dir(copy.Directory) != r.catalog.ConfigRoot || slices.Contains(*expected, name) {
			return errors.New("unit census retained copy overlaps a selected/retained directory")
		}
		if err := unitControlDirectory(copy.Directory); err != nil {
			return err
		}
		if err := readWatchBindings(copy); err != nil {
			return err
		}
		if _, err := r.directory(copy.Directory); err != nil {
			return err
		}
		other, err := os.Lstat(filepath.Join(copy.Directory, ".watch.lock"))
		if err != nil || other.Mode() != 0600 || !os.SameFile(lock, other) {
			return errors.New("unit census retained copy watch-lock identity differs")
		}
		stat := lock.Sys().(*syscall.Stat_t)
		r.report.Facts["retained-lock:"+copy.Directory] = fmt.Sprintf("%d:%d", stat.Dev, stat.Ino)
		for name, pin := range copy.Pins {
			path := filepath.Join(copy.Directory, name)
			if _, ok := s.current.Pins[name]; !ok {
				return errors.New("unit census retained copy has an unknown binding name")
			}
			if r.local.tokenPaths[path] {
				return errors.New("unit census retained copy overlaps a credential selector")
			}
			current, err := r.jsonFile(filepath.Join(s.current.Directory, name))
			if err != nil {
				return err
			}
			if nativeNone(current.Get("inbox_recall_binding")) {
				// The complete member/receipt validator still verifies current.
				// A retained unbound copy may only retain those exact bytes;
				// it cannot introduce a different profile or association.
				if pin != s.current.Pins[name] {
					return errors.New("unit census unbound retained copy must keep exact selected bytes")
				}
			} else if err := r.cli.unitControlConfigPair(filepath.Join(s.current.Directory, name), path); err != nil {
				return err
			}
			info, err := os.Lstat(path)
			if err != nil || info.Mode() != 0400 {
				return errors.New("unit census retained copy is not immutable data")
			}
			if _, err := r.pin(WatchCensusPin{path, pin}, false); err != nil {
				return err
			}
		}
		*expected = append(*expected, name)
		r.report.RetainedCopies = append(r.report.RetainedCopies, cloneCensusSelection(copy))
	}
	return nil
}

func (r *unitCensusDrain) recheck(ctx context.Context, swap *watchBindingsSwap) error {
	fail := func(err error) error { return fmt.Errorf("D6 unit census receipt invalidated: %w", err) }
	if _, _, err := readUnitControlPlan(r.planPath, r.planPin); err != nil {
		return fail(err)
	}
	if err := swap.verifyPrepared(); err != nil {
		return fail(err)
	}
	prepared := cloneCensusSelection(swap.selected)
	prepared.Directory = swap.retainedDir
	copies := append(slices.Clone(r.input.Retained), prepared)
	report, err := r.cli.inspectWatchUnitCensus(ctx, r.input.Catalog, r.input.Proposal, &unitCensusScope{current: r.current, retained: copies})
	r.final = &report
	if err != nil && !censusOnlyNeedsD2(err) {
		return fail(err)
	}
	var passed map[string]watchDrainD2Session
	if r.drain != nil {
		passed = r.drain.d2Sessions
	}
	if err := satisfyCensusD2(&report, passed); err != nil {
		return fail(err)
	}
	// Retain raw final observations. Only this swap's validated preparation is
	// projected out of the comparison; no caller can serialize that exception.
	facts := maps.Clone(report.Facts)
	for name := range prepared.Pins {
		delete(facts, "file:"+filepath.Join(prepared.Directory, name))
	}
	delete(facts, "directory:"+prepared.Directory)
	delete(facts, "retained-lock:"+prepared.Directory)
	rootKey := "directory:" + filepath.Dir(r.current.Directory)
	facts[rootKey] = r.initial.Facts[rootKey]
	if !maps.Equal(facts, r.initial.Facts) {
		return fail(errors.New("declared census facts changed"))
	}
	return swap.verifyPrepared()
}
