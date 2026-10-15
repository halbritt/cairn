package experimentaldebit

import (
	"context"
	"errors"
	"testing"
)

type fakeTx struct {
	commitErr, rollbackErr error
	commits, rollbacks     int
}

func (t *fakeTx) Commit(context.Context) error   { t.commits++; return t.commitErr }
func (t *fakeTx) Rollback(context.Context) error { t.rollbacks++; return t.rollbackErr }

type lifecycleStore struct {
	*fakeStore
	stage           string
	quarantineFault bool
	tx              *fakeTx
}

func (s *lifecycleStore) apply(ctx context.Context, c command) (acknowledgment, error) {
	if c.Op == s.stage || (s.quarantineFault && c.Op == "quarantine") {
		tx := &fakeTx{commitErr: errors.New("synthetic commit details"), rollbackErr: errors.New("synthetic rollback details")}
		s.tx = tx
		return acknowledgment{}, finishTransaction(ctx, tx, func() error { return nil })
	}
	return s.fakeStore.apply(ctx, c)
}
func (s *lifecycleStore) read(ctx context.Context, slot string) (snapshot, error) {
	if s.stage == "read" {
		tx := &fakeTx{rollbackErr: errors.New("private rollback")}
		s.tx = tx
		return snapshot{}, finishTransaction(ctx, tx, func() error { return fail("load_unknown") })
	}
	return s.fakeStore.read(ctx, slot)
}
func (s *lifecycleStore) dispatch(ctx context.Context, c command, call func() error) error {
	if s.stage == "dispatch" {
		tx := &fakeTx{rollbackErr: errors.New("private rollback")}
		s.tx = tx
		return finishTransaction(ctx, tx, func() error { return fail("dispatch_storage_unknown") })
	}
	return s.fakeStore.dispatch(ctx, c, call)
}
func (s *lifecycleStore) observe(ctx context.Context, c command, obs Observation) error {
	if s.stage == "observe" {
		tx := &fakeTx{rollbackErr: errors.New("private rollback")}
		s.tx = tx
		return finishTransaction(ctx, tx, func() error { return fail("audit_unknown") })
	}
	return s.fakeStore.observe(ctx, c, obs)
}
func TestTransactionPrimaryAndRollbackFactsReachOwner(t *testing.T) {
	for _, stage := range []string{"read", "arm", "debit", "consume", "dispatch", "observe"} {
		t.Run(stage, func(t *testing.T) {
			o, f, _, calls := start(t)
			s := &lifecycleStore{fakeStore: f, quarantineFault: true}
			o.adapters.store = s
			var r *Right
			var e error
			if stage == "consume" || stage == "dispatch" || stage == "observe" {
				r, e = o.Prepare(context.Background(), "slot-1")
				if e != nil {
					t.Fatal(e)
				}
			}
			s.stage = stage
			if r != nil {
				_, e = o.Forward(context.Background(), r, []byte("{}"))
			} else {
				_, e = o.Prepare(context.Background(), "slot-1")
			}
			var failure *Failure
			if !errors.As(e, &failure) || !failure.StorageCleanupUnknown || !failure.QuarantineUnknown {
				t.Fatalf("lost compound error: %#v", e)
			}
			obs := inspect(t, o, "slot-1")
			if !obs.StorageCleanupUnknown || !obs.QuarantineUnknown || obs.Failure != failure.Code {
				t.Fatal(obs)
			}
			if s.tx.rollbacks != 1 {
				t.Fatal("real lifecycle did not roll back")
			}
			if stage != "observe" && *calls != 0 {
				t.Fatal("factory on refused transaction")
			}
			if obs.CleanupUnknown {
				t.Fatal("storage cleanup conflated with transport close")
			}
		})
	}
}
func TestQuarantineRollbackUnknownAugmentsOriginalPrimary(t *testing.T) {
	o, f, _, _ := start(t)
	s := &lifecycleStore{fakeStore: f, quarantineFault: true}
	o.adapters.store = s
	r, e := o.Prepare(context.Background(), "slot-1")
	if e != nil {
		t.Fatal(e)
	}
	obs, e := o.Forward(context.Background(), r, []byte("[]"))
	code(t, e, "body_mismatch")
	if !obs.StorageCleanupUnknown || !obs.QuarantineUnknown || obs.Failure != "body_mismatch" {
		t.Fatal(obs)
	}
}
func TestLifecycleDoesNotCommitAfterWorkRefusal(t *testing.T) {
	tx := &fakeTx{rollbackErr: errors.New("rollback")}
	e := finishTransaction(context.Background(), tx, func() error { return fail("allocation_insufficient") })
	if fixed(e) != "allocation_insufficient" || !storageCleanup(e) || tx.commits != 0 || tx.rollbacks != 1 {
		t.Fatal(e)
	}
	tx = &fakeTx{}
	if e := finishTransaction(context.Background(), tx, func() error { return nil }); e != nil || tx.commits != 1 || tx.rollbacks != 0 {
		t.Fatal("success lifecycle")
	}
}

func TestRecoveryRetainsStorageCleanupUnknown(t *testing.T) {
	f := &fakeStore{book: syntheticBook(), now: 20}
	s := &lifecycleStore{fakeStore: f, stage: "recover"}
	o, e := assemble(context.Background(), adapters{s, fakeAuthority{}, fakeQualifier{}, func() (transport, error) { t.Fatal("factory"); return nil, nil }}, Identity{Principal: "fake-owner", Incarnation: "restart", Recovery: 1})
	var failure *Failure
	if o != nil || !errors.As(e, &failure) || failure.Code != "recovery_unknown" || !failure.StorageCleanupUnknown {
		t.Fatalf("%#v", e)
	}
}
func TestWorkPanicStillAttemptsRollback(t *testing.T) {
	tx := &fakeTx{rollbackErr: errors.New("rollback")}
	e := finishTransaction(context.Background(), tx, func() error { panic("private panic") })
	if fixed(e) != "transaction_panic_unknown" || !storageCleanup(e) || tx.rollbacks != 1 {
		t.Fatal(e)
	}
}
