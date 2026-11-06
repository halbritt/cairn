package core

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestPoolAdmissionTransferUsesStoredPublisherAndOneCollectionCharge(t *testing.T) {
	ctx := context.Background()
	publisher, worker, source, dest := eventFixture(t)
	repo := source.Scope.Repo
	state := testAdmissionState(t, publisher, repo)
	state.Policy.Limits.MaxCollectionOutstanding = 1
	state.Policy.Limits.MaxProducerRecipientOutstanding = 1
	op := testStore(t, Channel{Principal: "operator:pool-transfer", Operator: true})
	if _, err := op.ConfigureWorkerPool(ctx, WorkerPoolConfigRequest{RequestID: uuid.NewString(), Repo: repo, Name: "transfer", Enabled: true, MaxPending: 4, MaxPendingPerPublisher: 4}); err != nil {
		t.Fatal(err)
	}
	pending, err := publisher.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Repo: repo, Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "pool", Name: "transfer"}, Pool: &PoolRequirements{Workspace: "/fixture"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	// The queued event already exhausts this candidate collection bound. Transfer
	// checks only the newly known recipient, then keeps the same pool reply row.
	before, err := publisher.ClosureReservations(ctx, ClosureReservationQuery{}, dest)
	if err != nil || len(before.Reservations) != 1 {
		t.Fatalf("pool reply reserve: %+v %v", before, err)
	}
	tx, err := publisher.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	if err = lock(ctx, tx, "agent-events:"+repo); err != nil {
		t.Fatal(err)
	}
	if err = checkPoolDeliveryTransferTx(ctx, tx, repo, pending.EventID, worker.channel.Principal, state); err != nil {
		t.Fatal(err)
	}
	// Actual assignment runs in observe mode but exercises the real transfer.
	id, err := assignPool(ctx, tx, WorkerSlot{Repo: repo, Consumer: worker.channel.Principal}, poolCandidate{id: pending.EventID, pool: "transfer", publisher: publisher.channel.Principal})
	if err != nil {
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	after, err := publisher.ClosureReservations(ctx, ClosureReservationQuery{}, dest)
	if err != nil || len(after.Reservations) != 1 || after.Reservations[0].ReservationID != before.Reservations[0].ReservationID || after.Reservations[0].RequestDeliveryID != id || after.Reservations[0].ExpectedSender != worker.channel.Principal {
		t.Fatalf("pool reserve charged twice: %+v %v", after, err)
	}
	second, err := publisher.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Repo: repo, Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "pool", Name: "transfer"}, Pool: &PoolRequirements{Workspace: "/fixture"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	tx2, err := worker.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx2.Rollback(ctx)
	if err = lock(ctx, tx2, "agent-events:"+repo); err != nil {
		t.Fatal(err)
	}
	err = checkPoolDeliveryTransferTx(ctx, tx2, repo, second.EventID, worker.channel.Principal, state)
	requireAdmissionDimension(t, err, "producer_recipient_outstanding")
	if err = tx2.Rollback(ctx); err != nil {
		t.Fatal(err)
	}
	status, err := publisher.AgentEventStatus(ctx, EventStatusRequest{EventID: second.EventID}, dest)
	if err != nil || status.Pool == nil || status.Pool.State != "queued" || len(status.Deliveries) != 0 {
		t.Fatalf("refused transfer lost pool work: %+v %v", status, err)
	}
}

func TestQuietMaterializersDrainAcceptedChargesUnderLoweredLimits(t *testing.T) {
	ctx := context.Background()
	_, openStore := waitTestDB(t)
	f := setupOrganicFixtureWithStores(t, "quiet-transfer:"+uuid.NewString(), openStore)
	f.activateCapability(t)
	first := f.publishNotice(t, "first accepted quiet notice")
	second := f.publishNotice(t, "second accepted quiet notice")
	flushed := f.publishNotice(t, "publisher explicitly flushes this accepted notice")
	policy := admissionFixturePolicy()
	policy.Limits.MaxCollectionOutstanding = 1
	policy.Limits.MaxRecipientOutstanding = 1
	policy.Limits.MaxProducerRecipientOutstanding = 1
	enforceClosureTestPolicy(t, f.operator, f.repo, policy)
	// A queued notice already owns its normal recipient and collection unit;
	// lowering policy never makes this materialization a fresh publication.
	att, payload := f.signAttestation(t, "lowered-quiet-owner-turn", "owner_composer", "fresh_start", uuid.NewString(), time.Now().UnixMilli())
	claimed, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{RequestID: uuid.NewString(), Session: f.sessionRef, NativeOwnerTurnID: payload.NativeTurnID, OwnerInputAttestation: &att}, f.dest)
	if err != nil || claimed.Attempt == nil || claimed.Attempt.Delivery.Event.EventID != first.EventID {
		t.Fatalf("initial quiet transfer refused: %+v %v", claimed, err)
	}
	consumer := f.consumerStore(t)
	if _, err = consumer.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: claimed.Attempt.Delivery.DeliveryID, LeaseID: claimed.Attempt.Delivery.LeaseID, Disposition: "handled"}, f.dest); err != nil {
		t.Fatal(err)
	}
	advanced, err := f.recipient.AdvanceOrganicNotice(ctx, SessionOrganicNoticeAdvance{RequestID: uuid.NewString(), Session: f.sessionRef, AttemptID: claimed.Attempt.ID, RootID: claimed.RootID, Version: 1}, f.dest)
	if err != nil || advanced.Attempt == nil || advanced.Attempt.Delivery.Event.EventID != second.EventID {
		t.Fatalf("quiet continuation refused: %+v %v", advanced, err)
	}
	flush, err := f.publisher.FlushNotice(ctx, NoticeFlushRequest{RequestID: uuid.NewString(), EventID: flushed.EventID}, f.dest)
	if err != nil || flush.Released != 1 {
		t.Fatalf("quiet explicit flush refused: %+v %v", flush, err)
	}
	pressure, err := f.operator.CollectionEventPressure(ctx, EventPressureQuery{Repo: f.repo})
	if err != nil || pressure.Reservoirs.QueuedOrganicNotices != 0 || pressure.Reservoirs.PendingDeliveries != 1 || pressure.Reservoirs.LeasedDeliveries != 1 {
		t.Fatalf("quiet transferred charge duplicated: %+v %v", pressure, err)
	}
}

func TestPoolCapacityRefusalRotatesPublisherAndPreservesReplyReserve(t *testing.T) {
	ctx := context.Background()
	publisher, worker, source, dest := eventFixture(t)
	repo := source.Scope.Repo
	state := testAdmissionState(t, publisher, repo)
	state.Policy.Mode = "enforce"
	state.Policy.Limits.MaxRecipientOutstanding = 4
	state.Policy.Limits.MaxProducerRecipientOutstanding = 1
	op := testStore(t, Channel{Principal: "operator:pool-fairness", Operator: true})
	if _, err := op.ConfigureWorkerPool(ctx, WorkerPoolConfigRequest{RequestID: uuid.NewString(), Repo: repo, Name: "fair", Enabled: true, MaxPending: 8, MaxPendingPerPublisher: 4}); err != nil {
		t.Fatal(err)
	}
	slot, err := worker.RegisterWorkerSlot(ctx, WorkerRegisterRequest{RequestID: uuid.NewString(), Spec: WorkerSpec{Name: "fair-worker", Harness: "codex", Workspace: "/fixture", Pools: []string{"fair"}, LaunchSpacingSeconds: 1}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	// A notice consumes this producer/worker pair without creating executable
	// worker work, so another publisher can still use the worker's spare slots.
	_, err = publisher.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "agent", Name: worker.channel.Principal}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	publishPool := func(s *Store) AgentEvent {
		t.Helper()
		event, err := s.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "pool", Name: "fair"}, Pool: &PoolRequirements{Workspace: "/fixture"}}, dest)
		if err != nil {
			t.Fatal(err)
		}
		return event
	}
	blocked := publishPool(publisher)
	other := testStore(t, Channel{Principal: "healthy-pool-publisher:" + repo, Repo: repo})
	healthy := publishPool(other)
	before, err := publisher.ClosureReservations(ctx, ClosureReservationQuery{}, dest)
	if err != nil || len(before.Reservations) != 1 {
		t.Fatalf("blocked reserve before: %+v %v", before, err)
	}
	tx, err := worker.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	if err = lock(ctx, tx, "agent-events:"+repo); err != nil {
		t.Fatal(err)
	}
	candidate, err := selectPoolCandidateTx(ctx, tx, slot, state)
	if err != nil || candidate.id != healthy.EventID {
		t.Fatalf("blocked publisher starved healthy work: %+v %v", candidate, err)
	}
	if _, err = assignPool(ctx, tx, slot, candidate); err != nil {
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	blockedStatus, err := publisher.AgentEventStatus(ctx, EventStatusRequest{EventID: blocked.EventID}, dest)
	if err != nil || blockedStatus.Pool.State != "queued" || len(blockedStatus.Deliveries) != 0 {
		t.Fatalf("blocked pool was lost: %+v %v", blockedStatus, err)
	}
	after, err := publisher.ClosureReservations(ctx, ClosureReservationQuery{}, dest)
	if err != nil || len(after.Reservations) != 1 || after.Reservations[0].ReservationID != before.Reservations[0].ReservationID || after.Reservations[0].State != "reserved" {
		t.Fatalf("refused assignment consumed reply capacity: %+v %v", after, err)
	}
	refusals, err := op.EventAdmissionRefusals(ctx, EventAdmissionPolicyQuery{Repo: repo})
	if err != nil || len(refusals) != 1 || refusals[0].Dimension != "producer_recipient_outstanding" {
		t.Fatalf("committed deferred diagnosis: %+v %v", refusals, err)
	}
}
