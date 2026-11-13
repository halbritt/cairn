package coordination

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// WatchCensusPin selects bytes, never authority or a declaration of liveness.
type WatchCensusPin struct{ Path, SHA256 string }

// WatchCensusCatalog is an explicit observation universe. Only a disposable
// fixture may claim this finite universe is closed. No catalog proves absence
// of external live users, supported hook refresh, or store drain.
type WatchCensusCatalog struct {
	Schema          string
	Scope           string
	Root            string
	ConfigRoot      string
	CallbackRoot    string
	Selections      []WatchBindings
	Sessions        []WatchCensusPin
	Callbacks       []WatchCensusPin
	CredentialPaths []string
	Bindings        []WatchCensusPin
	Clients         []WatchCensusPin
	HookSelections  []WatchCensusPin
	UnboundReceipts []WatchCensusPin
}

// WatchCensusProposal names all members, sessions and callbacks proposed for
// this unit. Actual files and the catalog determine the required closure.
type WatchCensusProposal struct {
	Bindings  []WatchBindings
	Sessions  []string
	Callbacks []string
}

type WatchCensusMember struct {
	Config                           WatchCensusPin
	Binding                          WatchCensusPin
	Memory                           WatchCensusPin
	Account, Repository, Mode, Image string
	Resources                        []string
	Sessions                         []string
}

type WatchCensusCallback struct {
	Reference string
	Role      string
	Bindings  []string
	Config    WatchCensusPin
	Binding   WatchCensusPin
	Process   WatchCensusProcess
	Joined    bool
}

type WatchCensusSession struct{ Path, Binding, NativeSession, AgentID, ExecutionID, SHA256, Workspace string }

// WatchCensusDrainRequirement names an observation requiring authenticated D2
// correspondence. It is output evidence, never caller-supplied authority.
type WatchCensusDrainRequirement struct {
	Session WatchCensusSession
	Field   string
}

type WatchUnitCensus struct {
	Scope             string
	ExternalCoverage  string
	NativeRefresh     string
	FixtureReady      bool
	RequiredBindings  []string
	Members           []WatchCensusMember
	Callbacks         []WatchCensusCallback
	Sessions          []WatchCensusSession
	PositiveGrants    []string
	Facts             map[string]string
	Blockers          []string
	Digest            string
	RetainedCopies    []WatchBindings
	DrainRequirements []WatchCensusDrainRequirement
	HookObservations  []WatchCensusHookObservation
}

type watchCensusRead struct {
	cli                   *CLI
	local                 *watchDrainRead
	catalog               WatchCensusCatalog
	report                WatchUnitCensus
	nodes                 map[string]*WatchCensusMember
	files                 map[string]string
	directories           map[string][]string
	inodes                map[string]string
	images                map[string]bool
	nodeBlockers          map[string][]string
	nodeGrants            map[string][]string
	nodeDrainRequirements map[string][]WatchCensusDrainRequirement
	sessionRefs           map[string]WatchCensusSession
	callbackRecords       map[string]watchCensusCallbackRecord
	configPaths           map[*value.Value]string
	bindingPins           map[string]string
	clientPins            map[string]string
	control               *unitCensusScope
	hookSelections        []WatchCensusClaudeHooks
	associations          map[string]*watchCensusAssociation
}

// InspectWatchUnitCensus performs filesystem and process observations only.
// It does not call the API, acquire a lock, settle a journal, change a grant,
// signal a process, or publish a selection. Its returned digest is selected
// evidence, not a drain receipt. Observations and final readback are non-atomic.
func (c *CLI) InspectWatchUnitCensus(ctx context.Context, catalog WatchCensusPin, proposed WatchCensusProposal) (WatchUnitCensus, error) {
	return c.inspectWatchUnitCensus(ctx, catalog, proposed, nil)
}

func (c *CLI) inspectWatchUnitCensus(ctx context.Context, catalog WatchCensusPin, proposed WatchCensusProposal, control *unitCensusScope) (WatchUnitCensus, error) {
	r := &watchCensusRead{cli: c, nodes: map[string]*WatchCensusMember{}, files: map[string]string{},
		directories: map[string][]string{}, inodes: map[string]string{}, images: map[string]bool{},
		nodeBlockers: map[string][]string{}, nodeGrants: map[string][]string{}, sessionRefs: map[string]WatchCensusSession{},
		nodeDrainRequirements: map[string][]WatchCensusDrainRequirement{},
		callbackRecords:       map[string]watchCensusCallbackRecord{},
		configPaths:           map[*value.Value]string{}, control: control,
		report: WatchUnitCensus{ExternalCoverage: "unknown", NativeRefresh: "unobserved", Facts: map[string]string{}}}
	if c == nil || c.Host == nil || c.profile() == nil {
		return r.report, &value.ProfileUnavailable{}
	}
	r.local = &watchDrainRead{cli: c, facts: r.report.Facts, tokenPaths: map[string]bool{}, locks: map[string]string{}}
	if err := r.loadCatalog(ctx, catalog); err != nil {
		return r.report, err
	}
	if err := r.loadHookSelections(); err != nil {
		return r.report, err
	}
	if err := r.members(ctx); err != nil {
		return r.report, err
	}
	if err := r.inspectHookSelections(ctx); err != nil {
		return r.report, err
	}
	if err := r.callbacks(ctx); err != nil {
		return r.report, err
	}
	if err := r.sessions(ctx); err != nil {
		return r.report, err
	}
	if err := r.closure(proposed); err != nil {
		return r.report, err
	}
	if err := r.readback(ctx); err != nil {
		return r.report, err
	}
	if err := r.inspectHookSelections(ctx); err != nil {
		return r.report, err
	}
	r.report.Scope = r.catalog.Scope
	if r.catalog.Scope != "disposable-fixture" {
		r.block("external observation coverage is unknown")
	}
	slices.Sort(r.report.Blockers)
	slices.Sort(r.report.PositiveGrants)
	r.report.PositiveGrants = slices.Compact(r.report.PositiveGrants)
	r.report.Blockers = slices.Compact(r.report.Blockers)
	r.report.FixtureReady = len(r.report.Blockers) == 0 && len(r.report.DrainRequirements) == 0
	if err := sealWatchCensusReport(&r.report); err != nil {
		return r.report, err
	}
	if len(r.report.Blockers) != 0 {
		return r.report, errors.New(strings.Join(r.report.Blockers, "; "))
	}
	if len(r.report.DrainRequirements) != 0 {
		return r.report, &watchCensusD2Required{requirements: r.report.DrainRequirements}
	}
	return r.report, nil
}

func sealWatchCensusReport(report *WatchUnitCensus) error {
	slices.SortFunc(report.DrainRequirements, func(a, b WatchCensusDrainRequirement) int {
		if order := strings.Compare(a.Session.Path, b.Session.Path); order != 0 {
			return order
		}
		return strings.Compare(a.Field, b.Field)
	})
	report.Digest = ""
	raw, err := json.Marshal(report)
	if err != nil {
		return err
	}
	sum := sha256.Sum256(raw)
	report.Digest = hex.EncodeToString(sum[:])
	return nil
}

func (r *watchCensusRead) block(message string) {
	r.report.Blockers = append(r.report.Blockers, message)
}

func censusWithin(root, path string) bool {
	return compatibilityCanonical(root) && compatibilityCanonical(path) && path != root && strings.HasPrefix(path, root+string(os.PathSeparator))
}

func censusOverlap(a, b string) bool { return a == b || censusWithin(a, b) || censusWithin(b, a) }

func censusSameStrings(a, b []string) bool {
	a, b = slices.Clone(a), slices.Clone(b)
	slices.Sort(a)
	slices.Sort(b)
	return slices.Equal(a, b) && len(slices.Compact(a)) == len(a)
}

func (r *watchCensusRead) directory(path string) ([]string, error) {
	if !censusWithin(r.catalog.Root, path) {
		return nil, fmt.Errorf("census directory outside the pinned universe: %s", path)
	}
	if err := r.safePath(path, true); err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(path)
	if err != nil {
		return nil, err
	}
	names := make([]string, 0, len(entries))
	for _, entry := range entries {
		if entry.Type()&os.ModeSymlink != 0 {
			return nil, fmt.Errorf("census unsafe alias: %s", filepath.Join(path, entry.Name()))
		}
		names = append(names, entry.Name())
	}
	if old, seen := r.directories[path]; seen && !slices.Equal(old, names) {
		return nil, errors.New("census directory membership changed between observations")
	}
	r.directories[path] = names
	sum := sha256.Sum256([]byte(strings.Join(names, "\x00")))
	r.report.Facts["directory:"+path] = hex.EncodeToString(sum[:])
	return names, nil
}

func (r *watchCensusRead) closure(p WatchCensusProposal) error {
	wanted := map[string]bool{}
	for _, selection := range p.Bindings {
		if err := readWatchBindings(selection); err != nil {
			return err
		}
		for name, pin := range selection.Pins {
			path := filepath.Join(selection.Directory, name)
			node := r.nodes[path]
			if node == nil || node.Config.SHA256 != pin || wanted[path] {
				return fmt.Errorf("unknown, duplicate or mixed proposed binding: %s", path)
			}
			wanted[path] = true
		}
	}
	if len(wanted) == 0 {
		return errors.New("census proposal has no bindings")
	}
	closed := maps.Clone(wanted)
	for changed := true; changed; {
		changed = false
		for a, left := range r.nodes {
			if !closed[a] {
				continue
			}
			for b, right := range r.nodes {
				if closed[b] {
					continue
				}
				shared := false
				for _, x := range left.Resources {
					for _, y := range right.Resources {
						shared = shared || censusOverlap(x, y)
					}
				}
				for _, callback := range r.report.Callbacks {
					shared = shared || (slices.Contains(callback.Bindings, a) && slices.Contains(callback.Bindings, b))
				}
				if shared {
					closed[b] = true
					changed = true
				}
			}
		}
	}
	var sessions, callbacks []string
	mode, image := "", ""
	for _, path := range slices.Sorted(maps.Keys(closed)) {
		node := *r.nodes[path]
		r.report.RequiredBindings = append(r.report.RequiredBindings, path)
		r.report.Members = append(r.report.Members, node)
		sessions = append(sessions, node.Sessions...)
		r.report.Blockers = append(r.report.Blockers, r.nodeBlockers[path]...)
		r.report.PositiveGrants = append(r.report.PositiveGrants, r.nodeGrants[path]...)
		r.report.DrainRequirements = append(r.report.DrainRequirements, r.nodeDrainRequirements[path]...)
		if mode != "" && (mode != node.Mode || image != node.Image) {
			r.block("mixed runtime selection in the required unit")
		}
		mode, image = node.Mode, node.Image
	}
	for _, path := range slices.Compact(slices.Sorted(slices.Values(sessions))) {
		r.report.Sessions = append(r.report.Sessions, r.sessionRefs[path])
	}
	var selectedCallbacks []WatchCensusCallback
	for _, callback := range r.report.Callbacks {
		for _, binding := range callback.Bindings {
			if closed[binding] {
				callbacks = append(callbacks, callback.Reference)
				selectedCallbacks = append(selectedCallbacks, callback)
				break
			}
		}
	}
	r.report.Callbacks = selectedCallbacks
	if !censusSameStrings(slices.Sorted(maps.Keys(wanted)), r.report.RequiredBindings) {
		r.block("proposed half unit omits shared users")
	}
	if !censusSameStrings(p.Sessions, slices.Compact(slices.Sorted(slices.Values(sessions)))) {
		r.block("proposed session coverage is unknown or incomplete")
	}
	if !censusSameStrings(p.Callbacks, callbacks) {
		r.block("proposed callback coverage is unknown or incomplete")
	}
	return nil
}
