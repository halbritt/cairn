package coordination

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"maps"
	"strconv"
	"strings"

	"github.com/halbritt/cairn/internal/lifecycle/install"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// WatchDrainReceipt records inspected D1-D5 facts, not settlement authority.
// Its private scope can only be constructed by CaptureWatchDrain. The release
// owner still establishes complete unit membership and effective hook refresh.
type WatchDrainReceipt struct {
	reader     *CLI
	selection  WatchBindings
	callbacks  []*value.Value
	facts      map[string]string
	census     *unitCensusDrain
	d2Sessions map[string]watchDrainD2Session
}

// Facts returns fingerprints for the retained release evidence, never tokens
// or raw session bodies. It is a copy, not an activation or permission flag.
func (r *WatchDrainReceipt) Facts() map[string]string {
	facts := maps.Clone(r.facts)
	if r.census != nil {
		facts["unit-census"] = r.census.initial.Digest
	}
	return facts
}

// CaptureWatchDrain only reads existing files and authenticated status views.
// Missing coverage, an unresolved reply, or a nonterminal obligation refuses.
// callbacks is the observed watcher/wake/callback/capture process census; nil
// means unknown. Conversation owner processes are not callback processes.
func (c *CLI) CaptureWatchDrain(ctx context.Context, selected WatchBindings, callbacks []*value.Value) (*WatchDrainReceipt, error) {
	return c.captureWatchDrainCensus(ctx, selected, callbacks, nil)
}

func (c *CLI) captureWatchDrainCensus(ctx context.Context, selected WatchBindings, callbacks []*value.Value, census *unitCensusDrain) (*WatchDrainReceipt, error) {
	if callbacks == nil {
		return nil, errors.New("D5 callback process census is unknown")
	}
	selected.Pins = maps.Clone(selected.Pins)
	refs := make([]*value.Value, len(callbacks))
	for i, ref := range callbacks {
		if ref == nil || ref.Kind != value.Object {
			return nil, errors.New("D5 callback process identity is unknown")
		}
		refs[i] = nativeClone(ref)
	}
	read, err := c.readWatchDrainCensus(ctx, selected, refs, census, nil)
	if err != nil {
		return nil, err
	}
	return &WatchDrainReceipt{reader: c, selection: selected, callbacks: refs, facts: read.facts, d2Sessions: read.d2SessionResults(), census: census}, nil
}

// ExchangeWatchBindings publishes the whole stopped watcher selection and
// retains the old one with the SAME flock inode. Immediately before the atomic
// rename it re-reads the receipt's D1-D5 facts and refuses any change/unknown.
// The final re-read and rename are NOT one transaction: activity in that final
// interval, or activity leaving no retained observable, remains a qualification
// limit. This implements the coordinator's receipt-invalidation form, not a new
// lock or a claim that legacy callbacks participate in the watcher lock.
// Each fact's window starts at its own final read; the composite read itself
// is not an atomic view across files and API operations.
func ExchangeWatchBindings(ctx context.Context, receipt *WatchDrainReceipt, retainedDir string, next []install.File) (WatchBindingsExchange, error) {
	return exchangeDrainedWatchBindings(ctx, receipt, retainedDir, next, nil)
}

func exchangeDrainedWatchBindings(ctx context.Context, receipt *WatchDrainReceipt, retainedDir string, next []install.File, observe func(string) error) (WatchBindingsExchange, error) {
	if receipt == nil || receipt.reader == nil || receipt.facts == nil {
		return WatchBindingsExchange{}, errors.New("D6 inspected drain receipt required")
	}
	swap, err := openWatchBindingsSwap(ctx, receipt.selection, retainedDir, next)
	if err != nil {
		return WatchBindingsExchange{}, err
	}
	swap.observe = observe
	swap.verifyDrain = func(ctx context.Context) error {
		if err := receipt.recheckPrepared(ctx, swap); err != nil {
			return err
		}
		if receipt.census != nil {
			return receipt.census.recheck(ctx, swap)
		}
		return nil
	}
	return runWatchBindingsSwap(swap)
}

func (r *WatchDrainReceipt) recheck(ctx context.Context) error {
	return r.recheckPrepared(ctx, nil)
}
func (r *WatchDrainReceipt) recheckPrepared(ctx context.Context, swap *watchBindingsSwap) error {
	current, err := r.reader.readWatchDrainCensus(ctx, r.selection, r.callbacks, r.census, swap)
	if err != nil {
		return fmt.Errorf("D6 drain receipt invalidated: %w", err)
	}
	if !maps.Equal(r.facts, current.facts) {
		return errors.New("D6 drain receipt invalidated: D1-D5 facts changed")
	}
	r.d2Sessions = current.d2SessionResults()
	return nil
}

type watchDrainRead struct {
	configPaths   map[*value.Value]string
	censusMembers map[string]WatchCensusMember
	cli           *CLI
	facts         map[string]string
	configs       []*value.Value
	sessions      []watchDrainSession
	locks         map[string]string
	tokenPaths    map[string]bool
	memories      map[*value.Value]*value.Value
	grants        []watchDrainGrant
	settled       map[*value.Value]*value.Value
}

type watchDrainSession struct {
	config, ref, state *value.Value
}

func (c *CLI) readWatchDrain(ctx context.Context, selected WatchBindings, callbacks []*value.Value) (*watchDrainRead, error) {
	return c.readWatchDrainCensus(ctx, selected, callbacks, nil, nil)
}
func (c *CLI) readWatchDrainCensus(ctx context.Context, selected WatchBindings, callbacks []*value.Value, census *unitCensusDrain, swap *watchBindingsSwap) (*watchDrainRead, error) {
	if c.Host == nil || c.profile() == nil {
		return nil, &value.ProfileUnavailable{}
	}
	read := &watchDrainRead{cli: c, facts: map[string]string{}, locks: map[string]string{}, tokenPaths: map[string]bool{}, memories: map[*value.Value]*value.Value{}, settled: map[*value.Value]*value.Value{}}
	if census != nil && len(census.associationFacts) != 0 {
		members, facts, err := census.observeDrainAssociations(ctx, swap)
		if err != nil {
			return nil, err
		}
		if !maps.Equal(census.associationFacts, facts) {
			return nil, errors.New("D1 private census association lost or changed")
		}
		read.censusMembers = members
		for key, v := range facts {
			read.facts["unbound-association:"+key] = v
			if strings.HasPrefix(key, "credential-reference:") {
				read.tokenPaths[strings.TrimPrefix(key, "credential-reference:")] = true
			}
		}
	}
	if err := read.local(selected); err != nil {
		return nil, err
	}
	if err := read.store(ctx); err != nil {
		return nil, err
	}
	if err := read.grantFacts(); err != nil {
		return nil, err
	}
	if err := read.lockOwners(); err != nil {
		return nil, err
	}
	for i, ref := range callbacks {
		pid := ref.Get("pid")
		if pid == nil || pid.Kind != value.Int {
			return nil, errors.New("D5 callback process identity is unknown")
		}
		number, err := strconv.Atoi(pid.Integer)
		if err != nil || number <= 1 {
			return nil, errors.New("D5 callback PID must exceed 1")
		}
		alive, err := c.processAlive(ref)
		if err != nil || alive {
			return nil, errors.Join(err, errors.New("D5 callback process has not been joined"))
		}
		if err := read.record(fmt.Sprintf("callback:%d", i), ref); err != nil {
			return nil, err
		}
	}
	if census != nil && len(census.associationFacts) != 0 {
		_, facts, err := census.observeDrainAssociations(ctx, swap)
		if err != nil {
			return nil, err
		}
		if !maps.Equal(census.associationFacts, facts) {
			return nil, errors.New("D1 private census association changed during drain read")
		}
	}
	return read, nil
}

func (r *watchDrainRead) record(key string, body *value.Value) error {
	raw, err := canonicalSortedJSONBytes(body)
	if err != nil {
		return err
	}
	r.remember(key, raw)
	return nil
}

func (r *watchDrainRead) remember(key string, raw []byte) {
	sum := sha256.Sum256(raw)
	r.facts[key] = hex.EncodeToString(sum[:])
}
