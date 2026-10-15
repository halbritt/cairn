package experimentaldebit

import (
	"context"
	"fmt"
	"testing"
)

func completeSyntheticWork(b Book) Book {
	b.Slots = map[string]Slot{}
	for key := range b.Balances {
		b.Balances[key] = Balance{Remaining: 1000000}
	}
	// Explicit synthetic composition: not a calibration or one-call-per-real-job inference.
	roles := []struct {
		name  string
		count int
	}{{"CURRENT", 63}, {"CAND", 63}, {"primary", 63}, {"secondary", 63}, {"selector", 1}, {"control", 1}, {"retry", 1}, {"dispute", 1}, {"auxiliary", 1}}
	for _, role := range roles {
		for i := 0; i < role.count; i++ {
			key := fmt.Sprintf("%s-%03d", role.name, i)
			s := syntheticSlot()
			s.Attempt = "attempt-" + key
			s.Binding.Role = role.name
			s.Binding.Instance = key
			s.Binding.Job = role.name
			for j := range s.Debit.CounterKeys {
				s.Debit.CounterKeys[j] = fmt.Sprintf("job/%s/%d", key, j)
				b.Balances[s.Debit.CounterKeys[j]] = Balance{Remaining: 100}
			}
			b.Slots[key] = s
		}
	}
	return b
}
func TestCompleteSyntheticRolesConserveAndRetainEveryDispatch(t *testing.T) {
	o, f, _, calls := start(t)
	f.book = completeSyntheticWork(f.book)
	ctx := context.Background()
	for key := range f.book.Slots {
		right, e := o.Prepare(ctx, key)
		if e != nil {
			t.Fatalf("prepare %s: %v", key, e)
		}
		obs, e := o.Forward(ctx, right, []byte("{}"))
		if e != nil || obs.Outcome != "complete" {
			t.Fatalf("forward %s: %v", key, e)
		}
	}
	const dispatches = 257
	if *calls != dispatches || len(f.book.Slots) != dispatches {
		t.Fatal("lost dispatch/tombstone")
	}
	for _, key := range []string{"currency-global", "currency-account"} {
		if f.book.Balances[key].Committed != 7*dispatches {
			t.Fatal("currency conservation")
		}
	}
	if f.book.Balances["shared-pool"].Committed != 9*dispatches || f.book.Balances["pacing-window"].Committed != dispatches {
		t.Fatal("shared conservation")
	}
	for key, s := range f.book.Slots {
		if s.Phase != Consumed || s.Outcome != "complete" {
			t.Fatalf("lost slot %s", key)
		}
		for j, k := range s.Debit.CounterKeys {
			if f.book.Balances[k].Committed != s.Debit.Counters[j] {
				t.Fatal("per-instance counter")
			}
		}
	}
}

func TestBoundedPagesReconstructAllRetainedFacts(t *testing.T) {
	o, f, _, _ := start(t)
	f.book = completeSyntheticWork(f.book)
	want := len(f.book.Slots)
	seen := map[string]bool{}
	after := ""
	for pageNo := 0; pageNo < 10; pageNo++ {
		page, e := o.MetadataPage(context.Background(), after)
		if e != nil {
			t.Fatal(e)
		}
		raw, e := checkedBytes(page, MetadataBound)
		if e != nil || len(raw) > MetadataBound {
			t.Fatal("page bound")
		}
		for _, row := range page.Records {
			if seen[row.Slot] || row.Slot <= after {
				t.Fatal("duplicate/lost cursor")
			}
			seen[row.Slot] = true
		}
		if page.Done {
			if len(seen) != want {
				t.Fatal("dropped tombstones")
			}
			return
		}
		if page.Next <= after {
			t.Fatal("nonprogress")
		}
		after = page.Next
	}
	t.Fatal("nonfinite page traversal")
}

func TestCompletedSlotCannotReissueOrLoseDurableOutcome(t *testing.T) {
	o, f, _, calls := start(t)
	ctx := context.Background()
	r, e := o.Prepare(ctx, "slot-1")
	if e != nil {
		t.Fatal(e)
	}
	first, e := o.Forward(ctx, r, []byte("{}"))
	if e != nil {
		t.Fatal(e)
	}
	if len(o.observations) != 0 || len(o.rights) != 0 {
		t.Fatal("known completion retained as volatile lifetime gate")
	}
	persisted, e := o.Inspect(ctx, "slot-1")
	if e != nil || persisted != first {
		t.Fatal("inspect did not load durable completion")
	}
	before := f.book.Balances["currency-global"]
	r, e = o.Prepare(ctx, "slot-1")
	if e == nil || r != nil || *calls != 1 || f.book.Balances["currency-global"] != before {
		t.Fatal("reissued or redebited completed slot")
	}
	retained := f.book.Slots["slot-1"]
	if retained.Observation.Outcome != "complete" || retained.Observation.Phase != Consumed || retained.Outcome != "complete" || !retained.Quarantined {
		t.Fatal("refused replay erased prior durable outcome")
	}
	page, e := o.MetadataPage(ctx, "")
	if e != nil || len(page.Records) != 1 || page.Records[0].Observation != retained.Observation {
		t.Fatal("page lost compound retained facts")
	}
}

func TestTwoPendingRightsSurviveUnrelatedCommittedWork(t *testing.T) {
	o, f, tr, calls := start(t)
	ctx := context.Background()
	b := syntheticSlot()
	b.Attempt = "attempt-2"
	b.Binding.Instance = "actor-2"
	f.book.Slots["slot-2"] = b
	aRight, e := o.Prepare(ctx, "slot-1")
	if e != nil {
		t.Fatal(e)
	}
	bRight, e := o.Prepare(ctx, "slot-2")
	if e != nil {
		t.Fatal(e)
	}
	if len(o.rights) != 2 || *calls != 0 {
		t.Fatal("two pending rights required before any factory")
	}
	for _, right := range []*Right{aRight, bRight} {
		obs, e := o.Forward(ctx, right, []byte("{}"))
		if e != nil || obs.Outcome != "complete" {
			t.Fatalf("valid interleaved forward: %v", e)
		}
		if _, e = o.Forward(ctx, right, []byte("{}")); e == nil {
			t.Fatal("right replay")
		}
	}
	if *calls != 2 || tr.sent != 2 || len(o.rights) != 0 {
		t.Fatal("lost or duplicated dispatch")
	}
	for _, s := range f.book.Slots {
		if s.Phase != Consumed || s.Observation.Outcome != "complete" {
			t.Fatal("lost completion")
		}
	}
	if f.book.Balances["currency-global"].Committed != 14 || f.book.Balances["shared-pool"].Committed != 18 {
		t.Fatal("double debit or loss")
	}
}

// External store double injects a mutation after the returned authoritative read.
type advanceAfterRead struct{ *fakeStore }

func (s *advanceAfterRead) read(ctx context.Context, slot string) (snapshot, error) {
	snap, e := s.fakeStore.read(ctx, slot)
	if e == nil {
		s.book.Revision++
	}
	return snap, e
}
func TestConsumeRefreshPreservesBindingsFencesAndCAS(t *testing.T) {
	mutations := map[string]func(*fakeStore){
		"principal":   func(f *fakeStore) { f.book.Principal = "changed" },
		"incarnation": func(f *fakeStore) { f.book.Incarnation = "changed" },
		"generation":  func(f *fakeStore) { f.book.Generation++ },
		"recovery":    func(f *fakeStore) { f.book.Recovery++ },
		"attempt":     func(f *fakeStore) { s := f.book.Slots["slot-1"]; s.Attempt = "changed"; f.book.Slots["slot-1"] = s },
		"binding": func(f *fakeStore) {
			s := f.book.Slots["slot-1"]
			s.Binding.Model = "changed"
			f.book.Slots["slot-1"] = s
		},
		"debit":        func(f *fakeStore) { s := f.book.Slots["slot-1"]; s.Debit.Spend++; f.book.Slots["slot-1"] = s },
		"phase":        func(f *fakeStore) { s := f.book.Slots["slot-1"]; s.Phase = Armed; f.book.Slots["slot-1"] = s },
		"quarantine":   func(f *fakeStore) { s := f.book.Slots["slot-1"]; s.Quarantined = true; f.book.Slots["slot-1"] = s },
		"inactive":     func(f *fakeStore) { f.book.Active = false },
		"expired":      func(f *fakeStore) { f.now = 100 },
		"read_unknown": func(f *fakeStore) { f.failOp = "read" },
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			o, f, _, calls := start(t)
			ctx := context.Background()
			r, e := o.Prepare(ctx, "slot-1")
			if e != nil {
				t.Fatal(e)
			}
			before := f.book.Balances["currency-global"]
			mutate(f)
			_, e = o.Forward(ctx, r, []byte("{}"))
			if e == nil || *calls != 0 || len(o.rights) != 0 || f.book.Balances["currency-global"] != before {
				t.Fatal("refreshed substituted state or lost exposure")
			}
			if _, e = o.Forward(ctx, r, []byte("{}")); e == nil {
				t.Fatal("reissued refused right")
			}
		})
	}
	t.Run("mutation_after_snapshot", func(t *testing.T) {
		o, f, _, calls := start(t)
		ctx := context.Background()
		r, e := o.Prepare(ctx, "slot-1")
		if e != nil {
			t.Fatal(e)
		}
		o.adapters.store = &advanceAfterRead{f}
		_, e = o.Forward(ctx, r, []byte("{}"))
		code(t, e, "consume_unknown")
		if *calls != 0 || f.book.Slots["slot-1"].Phase != Debited || !o.closed {
			t.Fatal("CAS waived")
		}
	})
	t.Run("refresh_rollback_unknown", func(t *testing.T) {
		o, f, _, calls := start(t)
		ctx := context.Background()
		r, e := o.Prepare(ctx, "slot-1")
		if e != nil {
			t.Fatal(e)
		}
		o.adapters.store = &lifecycleStore{fakeStore: f, stage: "read"}
		obs, e := o.Forward(ctx, r, []byte("{}"))
		code(t, e, "consume_snapshot_unknown")
		if !obs.StorageCleanupUnknown || *calls != 0 {
			t.Fatal("refresh cleanup fact lost")
		}
	})
}
