package experimentaldebit

import (
	"context"
	"sync"
)

// All adapters are private: there is no production assembly/auth/hold/transport API.
// store's implementation owns load/validate/transition/commit, not caller JSON.
type store interface {
	read(context.Context, string) (snapshot, error)
	apply(context.Context, command) (acknowledgment, error)
	dispatch(context.Context, command, func() error) error
	observe(context.Context, command, Observation) error
	page(context.Context, string) (Page, error)
}
type transport interface {
	send([]byte) error
	outcome() (string, error)
	close() error
}
type authority interface {
	authorizeRecovery(Identity) error
	authorize(Identity, Binding) error
}
type qualifier interface{ qualify(Binding, Debit) error }
type adapters struct {
	store     store
	authority authority
	qualifier qualifier
	factory   func() (transport, error)
}
type Right struct{ marker byte }
type continuation struct {
	cmd     command
	binding Binding
}
type Owner struct {
	mu           sync.Mutex
	adapters     adapters
	who          Identity
	closed       bool
	rights       map[*Right]continuation
	observations map[string]Observation
}

// New is intentionally unavailable. This candidate does not authenticate a caller,
// create funds, provision a hold or connect a store/provider.
func New() (*Owner, error) { return nil, ErrUnavailable }
func assemble(ctx context.Context, a adapters, who Identity) (*Owner, error) {
	if a.store == nil || a.authority == nil || a.qualifier == nil || a.factory == nil {
		return nil, ErrUnavailable
	}
	if e := safe(func() error { return a.authority.authorizeRecovery(who) }); e != nil {
		return nil, operationFailure("recovery_authority_unavailable", e)
	}
	// Snapshot is observational; recovery does not turn old rows into continuations.
	var snap snapshot
	e := safe(func() error { var e error; snap, e = a.store.read(ctx, ""); return e })
	if e != nil {
		return nil, operationFailure("recovery_snapshot_unknown", e)
	}
	c := command{Op: "recover", ExpectedRevision: snap.Revision, Identity: who}
	var ack acknowledgment
	e = safe(func() error { var e error; ack, e = a.store.apply(ctx, c); return e })
	if e != nil || ack.CommandSHA != digest(c) || ack.Before != snap.Revision || ack.Revision != snap.Revision+1 || ack.Generation != snap.Identity.Generation+1 || ack.Recovery != who.Recovery || ack.Incarnation != who.Incarnation || !hash(ack.StateSHA) {
		return nil, operationFailure("recovery_unknown", e)
	}
	who.Generation = ack.Generation
	return &Owner{adapters: a, who: who, rights: map[*Right]continuation{}, observations: map[string]Observation{}}, nil
}
func safe(call func() error) (err error) {
	defer func() {
		if recover() != nil {
			err = fail("adapter_panic_unknown")
		}
	}()
	return call()
}
func fixed(err error) string {
	if r, ok := err.(refusal); ok {
		return string(r)
	}
	if f, ok := err.(*Failure); ok {
		return f.Code
	}
	if f, ok := err.(*transactionFailure); ok {
		return f.Stage
	}
	return "boundary_unknown"
}
func (o *Owner) stop(ctx context.Context, c command, primary error) error {
	o.closed = true
	clear(o.rights)
	obs := o.observations[c.Slot]
	obs.Failure = fixed(primary)
	obs.Quarantined = true
	obs.StorageCleanupUnknown = obs.StorageCleanupUnknown || storageCleanup(primary)
	c.Op = "quarantine"
	c.Report = obs
	e := safe(func() error {
		ack, e := o.adapters.store.apply(ctx, c)
		if e != nil {
			return e
		}
		if ack.CommandSHA != digest(c) || !hash(ack.StateSHA) || ack.Revision != ack.Before+1 {
			return fail("quarantine_ack_unknown")
		}
		return nil
	})
	obs.QuarantineUnknown = e != nil
	obs.StorageCleanupUnknown = obs.StorageCleanupUnknown || storageCleanup(e)
	o.observations[c.Slot] = obs
	return &Failure{Code: obs.Failure, StorageCleanupUnknown: obs.StorageCleanupUnknown, QuarantineUnknown: obs.QuarantineUnknown}
}
func (o *Owner) commit(ctx context.Context, c command, want Phase) (acknowledgment, error) {
	var ack acknowledgment
	e := safe(func() error { var e error; ack, e = o.adapters.store.apply(ctx, c); return e })
	if e != nil {
		return ack, operationFailure(c.Op+"_unknown", e)
	}
	if ack.CommandSHA != digest(c) || ack.Before != c.ExpectedRevision || ack.Revision != c.ExpectedRevision+1 || ack.Generation != o.who.Generation || ack.Recovery != o.who.Recovery || ack.Incarnation != o.who.Incarnation || ack.Phase != want || !hash(ack.StateSHA) {
		return ack, fail(c.Op + "_ack_unknown")
	}
	return ack, nil
}
func (o *Owner) Prepare(ctx context.Context, slot string) (*Right, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.closed || o.adapters.store == nil {
		return nil, ErrUnavailable
	}
	if !name(slot) {
		return nil, fail("slot_shape")
	}
	if _, used := o.observations[slot]; used {
		return nil, fail("slot_already_used")
	}
	o.observations[slot] = Observation{}
	var snap snapshot
	e := safe(func() error { var e error; snap, e = o.adapters.store.read(ctx, slot); return e })
	c := command{Op: "arm", Slot: slot, Attempt: snap.Slot.Attempt, ExpectedRevision: snap.Revision, Identity: o.who, BindingSHA: digest(snap.Slot.Binding), DebitSHA: digest(snap.Slot.Debit)}
	if e != nil {
		return nil, o.stop(ctx, c, operationFailure("snapshot_unknown", e))
	}
	// A refused repeat must augment, never erase, a known durable observation.
	o.observations[slot] = snap.Slot.Observation
	if snap.Identity != o.who {
		return nil, o.stop(ctx, c, fail("sender_fenced"))
	}
	e = safe(func() error {
		if e := o.adapters.authority.authorize(o.who, snap.Slot.Binding); e != nil {
			return e
		}
		return o.adapters.qualifier.qualify(snap.Slot.Binding, snap.Slot.Debit)
	})
	if e != nil {
		return nil, o.stop(ctx, c, fail("qualification_unknown"))
	}
	ack, e := o.commit(ctx, c, Armed)
	if e != nil {
		return nil, o.stop(ctx, c, e)
	}
	obs := o.observations[slot]
	obs.Phase = Armed
	o.observations[slot] = obs
	c.Op = "debit"
	c.ExpectedRevision = ack.Revision
	ack, e = o.commit(ctx, c, Debited)
	if e != nil {
		return nil, o.stop(ctx, c, e)
	}
	obs.Phase = Debited
	o.observations[slot] = obs
	c.Op = "consume"
	c.ExpectedRevision = ack.Revision
	r := &Right{1}
	o.rights[r] = continuation{c, snap.Slot.Binding}
	return r, nil
}
func (o *Owner) Forward(ctx context.Context, right *Right, body []byte) (Observation, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.closed {
		return Observation{}, ErrUnavailable
	}
	cont, ok := o.rights[right]
	delete(o.rights, right)
	if !ok {
		return Observation{}, fail("right_unavailable")
	}
	c := cont.cmd
	obs := o.observations[c.Slot]
	if int64(len(body)) != cont.binding.BodyBytes || len(body) > BodyBound {
		e := o.stop(ctx, c, fail("body_mismatch"))
		return o.observations[c.Slot], e
	}
	// Verify the same owned bytes that will be sent; never hash then copy.
	body = append([]byte(nil), body...)
	if bodyHash(body) != cont.binding.BodySHA {
		e := o.stop(ctx, c, fail("body_mismatch"))
		return o.observations[c.Slot], e
	}
	// Other valid pending slots may advance the global revision. Refresh only
	// the CAS revision, after revalidating this exact live continuation.
	var snap snapshot
	e := safe(func() error { var e error; snap, e = o.adapters.store.read(ctx, c.Slot); return e })
	if e != nil {
		e = o.stop(ctx, c, operationFailure("consume_snapshot_unknown", e))
		return o.observations[c.Slot], e
	}
	if snap.Identity != o.who || snap.Slot.Attempt != c.Attempt || snap.Slot.Phase != Debited || snap.Slot.Quarantined || digest(snap.Slot.Binding) != c.BindingSHA || digest(snap.Slot.Debit) != c.DebitSHA {
		e = o.stop(ctx, c, fail("consume_state_changed"))
		return o.observations[c.Slot], e
	}
	c.ExpectedRevision = snap.Revision
	_, e = o.commit(ctx, c, Consumed)
	if e != nil {
		e = o.stop(ctx, c, e)
		return o.observations[c.Slot], e
	}
	obs.Phase = Consumed
	o.observations[c.Slot] = obs
	entered := false
	e = safe(func() error {
		return o.adapters.store.dispatch(ctx, c, func() error {
			if entered {
				return fail("dispatch_reentry")
			}
			entered = true
			return o.send(c, body)
		})
	})
	if e == nil && !entered {
		e = fail("dispatch_missing")
	}
	if e != nil {
		e = o.stop(ctx, c, e)
		return o.observations[c.Slot], e
	}
	obs = o.observations[c.Slot]
	e = safe(func() error { return o.adapters.store.observe(ctx, c, obs) })
	if e != nil {
		e = o.stop(ctx, c, operationFailure("observation_unknown", e))
	}
	if e != nil {
		return o.observations[c.Slot], e
	}
	delete(o.observations, c.Slot) // Known durable observation; no tombstone is removed.
	return obs, nil
}
func (o *Owner) send(c command, body []byte) (primary error) {
	var t transport
	obs := o.observations[c.Slot]
	defer func() {
		if recover() != nil {
			primary = fail("transport_panic_unknown")
		}
		if t != nil {
			if e := safe(t.close); e != nil {
				obs.CleanupUnknown = true
				if primary == nil {
					primary = fail("close_unknown")
				}
			}
		}
		if primary != nil && obs.Outcome == "" {
			obs.Outcome = "unknown"
		}
		o.observations[c.Slot] = obs
	}()
	var e error
	t, e = o.adapters.factory()
	if e != nil || t == nil {
		return fail("factory_unknown")
	}
	if e = t.send(body); e != nil {
		return fail("send_unknown")
	}
	outcome, e := t.outcome()
	if e != nil || (outcome != "complete" && outcome != "unknown") {
		return fail("outcome_unknown")
	}
	obs.Outcome = outcome
	if outcome == "unknown" {
		return fail("outcome_unknown")
	}
	return nil
}

// Inspect is observation-only, including local unknown-write fallback. It issues no rights.
func (o *Owner) Inspect(ctx context.Context, slot string) (Observation, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if obs, ok := o.observations[slot]; ok {
		return obs, nil
	}
	if o.adapters.store == nil {
		return Observation{}, ErrUnavailable
	}
	snap, e := o.adapters.store.read(ctx, slot)
	if e != nil {
		return Observation{}, operationFailure("inspect_unknown", e)
	}
	return snap.Slot.Observation, nil
}

// MetadataPage reads retained rows. Unknown-write local facts are in Failure/Inspect,
// not falsely represented as a successfully persisted row.
func (o *Owner) MetadataPage(ctx context.Context, after string) (Page, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.adapters.store == nil {
		return Page{}, ErrUnavailable
	}
	return o.adapters.store.page(ctx, after)
}

type Retained struct {
	Slot        string
	Phase       Phase
	Quarantined bool
	Observation Observation
}
type Page struct {
	Records []Retained
	Next    string
	Done    bool
}

func appendPage(p Page, row Retained) (Page, bool) {
	next := p
	next.Records = append(append([]Retained(nil), p.Records...), row)
	next.Next = row.Slot
	if _, e := checkedBytes(next, MetadataBound); e != nil {
		return p, false
	}
	return next, true
}
