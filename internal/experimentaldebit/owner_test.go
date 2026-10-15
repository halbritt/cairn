package experimentaldebit

import (
	"context"
	"errors"
	"math"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"
)

// All synthetic holds, principals, qualifications and transports live in tests.
type fakeStore struct {
	mu              sync.Mutex
	book            Book
	now             int64
	failOp          string
	applyThenLose   bool
	badAck          string
	quarantineFails bool
	dispatchHook    func()
}

func (f *fakeStore) read(_ context.Context, slot string) (snapshot, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	b, e := cloneBook(f.book)
	if e != nil {
		return snapshot{}, e
	}
	if f.failOp == "read" {
		return snapshot{}, errors.New("synthetic read")
	}
	return snapshot{b.Revision, identity(b), b.Slots[slot]}, nil
}
func (f *fakeStore) apply(_ context.Context, c command) (acknowledgment, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if c.Op == "quarantine" && f.quarantineFails {
		return acknowledgment{}, errors.New("synthetic cleanup")
	}
	if f.failOp == c.Op && !f.applyThenLose {
		return acknowledgment{}, errors.New("synthetic before commit")
	}
	after, ack, e := transition(f.book, c, f.now)
	if e != nil {
		return acknowledgment{}, e
	}
	f.book = after
	if f.failOp == c.Op {
		return acknowledgment{}, errors.New("synthetic lost ack")
	}
	if f.badAck == c.Op {
		ack.CommandSHA = strings.Repeat("0", 64)
	}
	return ack, nil
}
func (f *fakeStore) dispatch(_ context.Context, c command, call func() error) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.dispatchHook != nil {
		f.dispatchHook()
	}
	if e := dispatchAllowed(f.book, c, f.now); e != nil {
		return e
	}
	return call()
}
func (f *fakeStore) observe(_ context.Context, c command, obs Observation) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	s := f.book.Slots[c.Slot]
	if f.failOp == "observe" {
		return errors.New("synthetic observation loss")
	}
	s.Outcome = obs.Outcome
	s.Observation = obs
	f.book.Revision++
	f.book.Slots[c.Slot] = s
	return nil
}

type fakeAuthority struct{ err error }

func (f fakeAuthority) authorizeRecovery(Identity) error  { return f.err }
func (f fakeAuthority) authorize(Identity, Binding) error { return f.err }

type fakeQualifier struct{ err error }

func (f fakeQualifier) qualify(Binding, Debit) error { return f.err }

type fakeTransport struct {
	sent                      int
	body                      []byte
	sendErr, outErr, closeErr error
	answer                    string
	panicSend                 bool
	closed                    int
}

func (t *fakeTransport) send(b []byte) error {
	t.sent++
	t.body = append([]byte(nil), b...)
	if t.panicSend {
		panic("synthetic panic")
	}
	return t.sendErr
}
func (t *fakeTransport) outcome() (string, error) { return t.answer, t.outErr }
func (t *fakeTransport) close() error             { t.closed++; return t.closeErr }
func syntheticSlot() Slot {
	h := strings.Repeat("a", 64)
	return Slot{Attempt: "attempt-1", Binding: Binding{Pins: Pins{h, h, h, h, h, h, h, h, h, h}, Hold: "hold-1", Job: "actor", Instance: "actor-1", Role: "CURRENT", Model: "fixed-model", Account: "synthetic-account", Provider: "fake", Endpoint: "fake", Tier: "fake", Route: h, Currency: "USD", UnitsPerCurrency: 1000, BodyBytes: 2, BodySHA: bodyHash([]byte("{}"))}, Debit: Debit{Spend: 7, Counters: [5]int64{1, 2, 3, 4, 5}, CurrencyKeys: [2]string{"currency-global", "currency-account"}, CounterKeys: [5]string{"job-requests", "job-input", "job-output", "job-cache-read", "job-cache-write"}, Resources: []Allocation{{"shared-pool", 9}}, Pacing: []Allocation{{"pacing-window", 1}}}, NotBefore: 10, ValidUntil: 100, Phase: Selected}
}
func syntheticBook() Book {
	s := syntheticSlot()
	amounts, _ := charges(s.Debit)
	balances := map[string]Balance{}
	for k := range amounts {
		balances[k] = Balance{Remaining: 100}
	}
	return Book{Currency: "USD", UnitsPerCurrency: 1000, Revision: 1, Generation: 1, Recovery: 1, Principal: "fake-owner", Incarnation: "old", Active: true, Balances: balances, Slots: map[string]Slot{"slot-1": s}}
}
func start(t *testing.T) (*Owner, *fakeStore, *fakeTransport, *int) {
	t.Helper()
	f := &fakeStore{book: syntheticBook(), now: 20}
	tr := &fakeTransport{answer: "complete"}
	calls := new(int)
	a := adapters{f, fakeAuthority{}, fakeQualifier{}, func() (transport, error) { *calls++; return tr, nil }}
	o, e := assemble(context.Background(), a, Identity{Principal: "fake-owner", Incarnation: "fresh", Recovery: 1})
	if e != nil {
		t.Fatal(e)
	}
	// Explicit dummy selection after recovery, not a shipped provisioning API.
	f.book.Slots = map[string]Slot{"slot-1": syntheticSlot()}
	return o, f, tr, calls
}
func cmd(b Book, op string) command {
	s := b.Slots["slot-1"]
	return command{Op: op, Slot: "slot-1", Attempt: s.Attempt, ExpectedRevision: b.Revision, Identity: identity(b), BindingSHA: digest(s.Binding), DebitSHA: digest(s.Debit)}
}
func mustArm(t *testing.T, b Book) Book {
	t.Helper()
	a, _, e := transition(b, cmd(b, "arm"), 20)
	if e != nil {
		t.Fatal(e)
	}
	return a
}
func code(t *testing.T, err error, want string) {
	t.Helper()
	if err == nil || err.Error() != want {
		t.Fatalf("error=%v want=%s", err, want)
	}
}

func TestDefaultCannotReachAnyAdapter(t *testing.T) {
	o, e := New()
	if o != nil || !errors.Is(e, ErrUnavailable) {
		t.Fatal("available default")
	}
	var z Owner
	_, e = z.Prepare(context.Background(), "slot-1")
	if !errors.Is(e, ErrUnavailable) {
		t.Fatal(e)
	}
}
func TestWholeDebitAndConsumePreserveExposure(t *testing.T) {
	b := mustArm(t, syntheticBook())
	original := digest(b)
	after, ack, e := transition(b, cmd(b, "debit"), 20)
	if e != nil {
		t.Fatal(e)
	}
	amounts, _ := charges(b.Slots["slot-1"].Debit)
	for k, n := range amounts {
		if after.Balances[k] != (Balance{100 - n, n}) {
			t.Fatalf("wrong balance %s", k)
		}
	}
	if digest(b) != original || ack.StateSHA != digest(after) {
		t.Fatal("mutation/hash")
	}
	consumed, _, e := transition(after, cmd(after, "consume"), 20)
	if e != nil {
		t.Fatal(e)
	}
	if !reflect.DeepEqual(after.Balances, consumed.Balances) || consumed.Slots["slot-1"].Phase != Consumed {
		t.Fatal("consume changed exposure")
	}
	_, _, e = transition(consumed, cmd(consumed, "consume"), 20)
	code(t, e, "not_debited")
}
func TestInsufficientAllocationNeverPartiallyDebits(t *testing.T) {
	for _, key := range []string{"currency-global", "currency-account", "job-requests", "job-input", "job-output", "job-cache-read", "job-cache-write", "shared-pool", "pacing-window"} {
		t.Run(key, func(t *testing.T) {
			b := mustArm(t, syntheticBook())
			b.Balances[key] = Balance{}
			before := digest(b)
			_, _, e := transition(b, cmd(b, "debit"), 20)
			code(t, e, "allocation_insufficient")
			if digest(b) != before {
				t.Fatal("partial mutation")
			}
		})
	}
}
func TestChangedIdentityBindingRevisionAndTimeRefuse(t *testing.T) {
	cases := map[string]func(*command){"body": func(c *command) { c.BindingSHA = strings.Repeat("b", 64) }, "debit": func(c *command) { c.DebitSHA = strings.Repeat("b", 64) }, "attempt": func(c *command) { c.Attempt = "replacement" }, "sender": func(c *command) { c.Identity.Generation++ }, "recovery": func(c *command) { c.Identity.Recovery++ }, "principal": func(c *command) { c.Identity.Principal = "other" }, "incarnation": func(c *command) { c.Identity.Incarnation = "other" }, "revision": func(c *command) { c.ExpectedRevision++ }}
	for label, change := range cases {
		t.Run(label, func(t *testing.T) {
			b := syntheticBook()
			c := cmd(b, "arm")
			change(&c)
			_, _, e := transition(b, c, 20)
			if e == nil {
				t.Fatal("accepted substitution")
			}
		})
	}
	for _, now := range []int64{-1, 9, 100, 101} {
		b := syntheticBook()
		_, _, e := transition(b, cmd(b, "arm"), now)
		if e == nil {
			t.Fatal("accepted expired")
		}
	}
}
func TestFactoryOnlyAfterCommittedConsumeAndOneRight(t *testing.T) {
	o, f, tr, calls := start(t)
	r, e := o.Prepare(context.Background(), "slot-1")
	if e != nil {
		t.Fatal(e)
	}
	if *calls != 0 || f.book.Slots["slot-1"].Phase != Debited {
		t.Fatal("early factory")
	}
	f.dispatchHook = func() {
		if f.book.Slots["slot-1"].Phase != Consumed {
			t.Error("uncommitted consume")
		}
	}
	obs, e := o.Forward(context.Background(), r, []byte("{}"))
	if e != nil {
		t.Fatal(e)
	}
	if obs.Outcome != "complete" || *calls != 1 || tr.sent != 1 || tr.closed != 1 || string(tr.body) != "{}" {
		t.Fatal("forward facts")
	}
	_, e = o.Forward(context.Background(), r, []byte("{}"))
	code(t, e, "right_unavailable")
	if *calls != 1 {
		t.Fatal("right replay")
	}
	// Observation is not a capability; an unrelated pointer cannot recover a right.
	_ = inspect(t, o, "slot-1")
	_, e = o.Forward(context.Background(), &Right{1}, []byte("{}"))
	code(t, e, "right_unavailable")
}
func TestUnknownCommitAndMalformedAckQuarantineBeforeFactory(t *testing.T) {
	for _, op := range []string{"arm", "debit", "consume"} {
		for _, mode := range []string{"before", "after", "bad-ack"} {
			t.Run(op+"/"+mode, func(t *testing.T) {
				o, f, _, calls := start(t)
				var r *Right
				var e error
				if op == "consume" {
					r, e = o.Prepare(context.Background(), "slot-1")
					if e != nil {
						t.Fatal(e)
					}
				}
				if mode == "bad-ack" {
					f.badAck = op
				} else {
					f.failOp = op
					f.applyThenLose = mode == "after"
				}
				if op == "consume" {
					_, e = o.Forward(context.Background(), r, []byte("{}"))
				} else {
					_, e = o.Prepare(context.Background(), "slot-1")
				}
				if e == nil || *calls != 0 || !o.closed || !f.book.Slots["slot-1"].Quarantined {
					t.Fatal("uncertainty reopened")
				}
				old := f.book.Slots["slot-1"]
				old.Attempt = "new-id"
				f.book.Slots["slot-1"] = old
				_, e = o.Prepare(context.Background(), "slot-1")
				if !errors.Is(e, ErrUnavailable) {
					t.Fatal("replacement allowed")
				}
			})
		}
	}
}
func TestCompoundPrimaryAndQuarantineFailureRemainVisible(t *testing.T) {
	o, f, _, calls := start(t)
	f.failOp = "debit"
	f.applyThenLose = true
	f.quarantineFails = true
	_, e := o.Prepare(context.Background(), "slot-1")
	code(t, e, "debit_unknown")
	obs := inspect(t, o, "slot-1")
	if obs.Failure != "debit_unknown" || !obs.QuarantineUnknown || *calls != 0 {
		t.Fatal(obs)
	}
	page, e := o.MetadataPage(context.Background(), "")
	raw, _ := checkedBytes(page, MetadataBound)
	if e != nil || len(raw) == 0 {
		t.Fatal("lost metadata")
	}
	if f.book.Balances["currency-global"].Committed != 7 {
		t.Fatal("unknown debit refunded")
	}
}
func TestBodyMismatchBurnsRightAndExposure(t *testing.T) {
	o, f, _, calls := start(t)
	r, e := o.Prepare(context.Background(), "slot-1")
	if e != nil {
		t.Fatal(e)
	}
	_, e = o.Forward(context.Background(), r, []byte("[]"))
	code(t, e, "body_mismatch")
	if *calls != 0 || f.book.Balances["currency-global"].Committed != 7 {
		t.Fatal("effect/refund")
	}
}
func TestTransportFailuresPreserveExposurePrimaryAndClose(t *testing.T) {
	for _, stage := range []string{"factory", "send", "outcome", "close", "panic", "unknown-response"} {
		t.Run(stage, func(t *testing.T) {
			o, f, tr, calls := start(t)
			x := errors.New("private synthetic text")
			switch stage {
			case "factory":
				o.adapters.factory = func() (transport, error) { *calls++; return nil, x }
			case "send":
				tr.sendErr = x
				tr.closeErr = x
			case "outcome":
				tr.outErr = x
			case "close":
				tr.closeErr = x
			case "panic":
				tr.panicSend = true
				tr.closeErr = x
			case "unknown-response":
				tr.answer = "unknown"
			}
			r, e := o.Prepare(context.Background(), "slot-1")
			if e != nil {
				t.Fatal(e)
			}
			obs, e := o.Forward(context.Background(), r, []byte("{}"))
			if e == nil {
				t.Fatal("lost failure")
			}
			if *calls != 1 || f.book.Balances["currency-global"].Committed != 7 || !o.closed {
				t.Fatal("refunded/reopened")
			}
			if stage != "factory" && tr.closed != 1 {
				t.Fatal("close missing")
			}
			if stage == "send" && obs.Failure != "send_unknown" {
				t.Fatal(obs)
			}
			if strings.Contains(e.Error(), "private") {
				t.Fatal("raw exception")
			}
		})
	}
}
func TestRecoveryRetiresOldSlotsAndCannotResumeDebited(t *testing.T) {
	o, f, tr, calls := start(t)
	r, e := o.Prepare(context.Background(), "slot-1")
	if e != nil {
		t.Fatal(e)
	}
	a := o.adapters
	newOwner, e := assemble(context.Background(), a, Identity{Principal: "fake-owner", Incarnation: "restart", Recovery: 1})
	if e != nil {
		t.Fatal(e)
	}
	if !f.book.Slots["slot-1"].Quarantined {
		t.Fatal("old slot not retired")
	}
	_, e = newOwner.Prepare(context.Background(), "slot-1")
	if e == nil {
		t.Fatal("restart resumed")
	}
	_, e = o.Forward(context.Background(), r, []byte("{}"))
	if e == nil || *calls != 0 || tr.sent != 0 {
		t.Fatal("old sender survived fence")
	}
}
func TestUnknownRecoveryNeverReturnsOwner(t *testing.T) {
	for _, after := range []bool{false, true} {
		f := &fakeStore{book: syntheticBook(), now: 20, failOp: "recover", applyThenLose: after}
		o, e := assemble(context.Background(), adapters{f, fakeAuthority{}, fakeQualifier{}, func() (transport, error) { t.Fatal("factory"); return nil, nil }}, Identity{Principal: "fake-owner", Incarnation: "new", Recovery: 1})
		if e == nil || o != nil {
			t.Fatal("unknown recovery available")
		}
	}
}
func TestConcurrentUseOfSameLiveRightHasOneFactory(t *testing.T) {
	o, _, _, calls := start(t)
	r, e := o.Prepare(context.Background(), "slot-1")
	if e != nil {
		t.Fatal(e)
	}
	var wg sync.WaitGroup
	errs := make(chan error, 2)
	for range 2 {
		wg.Add(1)
		go func() { defer wg.Done(); _, e := o.Forward(context.Background(), r, []byte("{}")); errs <- e }()
	}
	wg.Wait()
	close(errs)
	successes := 0
	for e := range errs {
		if e == nil {
			successes++
		}
	}
	if successes != 1 || *calls != 1 {
		t.Fatal("duplicate forward")
	}
}
func TestExpiryAfterConsumeBeforeDispatchRefusesFactory(t *testing.T) {
	o, f, _, calls := start(t)
	r, e := o.Prepare(context.Background(), "slot-1")
	if e != nil {
		t.Fatal(e)
	}
	f.dispatchHook = func() { f.now = 100 }
	_, e = o.Forward(context.Background(), r, []byte("{}"))
	code(t, e, "allocation_expired")
	if *calls != 0 || f.book.Slots["slot-1"].Phase != Consumed {
		t.Fatal("wrong boundary")
	}
}
func TestQualifiedShapesOverflowAndBounds(t *testing.T) {
	for _, mutate := range []func(*Book){func(b *Book) { b.Revision = math.MaxInt64 }, func(b *Book) { s := b.Slots["slot-1"]; s.Debit.Counters[0] = 0; b.Slots["slot-1"] = s }, func(b *Book) { s := b.Slots["slot-1"]; s.Debit.Pacing[0].Key = "shared-pool"; b.Slots["slot-1"] = s }, func(b *Book) { b.Balances["shared-pool"] = Balance{math.MaxInt64, 1} }, func(b *Book) { s := b.Slots["slot-1"]; s.Binding.UnitsPerCurrency = 0; b.Slots["slot-1"] = s }} {
		b := syntheticBook()
		mutate(&b)
		_, _, e := transition(b, cmd(b, "arm"), 20)
		if e == nil {
			t.Fatal("accepted malformed")
		}
	}
	_, e := decodeSlot(make([]byte, SourceBound+1))
	code(t, e, "representation_bound")
	raw, _ := checkedBytes(syntheticSlot(), SourceBound)
	_, e = decodeSlot(append(raw, 'x'))
	code(t, e, "slot_tail")
}

func TestQualifiedCurrencyScaleCannotAliasSharedBalances(t *testing.T) {
	b := syntheticBook()
	s := b.Slots["slot-1"]
	s.Binding.UnitsPerCurrency = 1000000
	b.Slots["slot-1"] = s
	_, _, e := transition(b, cmd(b, "arm"), 20)
	code(t, e, "ledger_currency_mismatch")
}
func TestQualificationRefusalOccursBeforeArm(t *testing.T) {
	o, f, _, calls := start(t)
	o.adapters.qualifier = fakeQualifier{errors.New("unqualified")}
	_, e := o.Prepare(context.Background(), "slot-1")
	code(t, e, "qualification_unknown")
	if f.book.Slots["slot-1"].Phase != Selected || f.book.Balances["currency-global"].Committed != 0 || *calls != 0 {
		t.Fatal("unqualified debit")
	}
}

func inspect(t *testing.T, o *Owner, slot string) Observation {
	t.Helper()
	obs, e := o.Inspect(context.Background(), slot)
	if e != nil {
		t.Fatal(e)
	}
	return obs
}
func (f *fakeStore) page(_ context.Context, after string) (Page, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	keys := []string{}
	for key := range f.book.Slots {
		if key > after {
			keys = append(keys, key)
		}
	}
	sort.Strings(keys)
	page := Page{Next: after}
	for _, key := range keys {
		if len(page.Records) >= 128 {
			return page, nil
		}
		s := f.book.Slots[key]
		next, ok := appendPage(page, Retained{key, s.Phase, s.Quarantined, s.Observation})
		if !ok {
			return page, nil
		}
		page = next
	}
	page.Done = true
	return page, nil
}
