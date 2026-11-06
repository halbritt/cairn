package core

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

// enablePublicEnforcePolicy configures finite limits and enables public "enforce"
// mode via SetEventAdmissionPolicy. It never skips or bypasses the public gate:
// any regression to FEATURE_UNAVAILABLE fails the test immediately.
func enablePublicEnforcePolicy(t *testing.T, op *Store, repo string, configure func(*EventAdmissionLimits)) EventAdmissionPolicyState {
	t.Helper()
	ctx := context.Background()
	if _, err := op.ReconcileClosureReservations(ctx, ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: repo}); err != nil {
		t.Fatalf("ReconcileClosureReservations: %v", err)
	}
	cur, err := op.EventAdmissionPolicy(ctx, EventAdmissionPolicyQuery{Repo: repo})
	if err != nil {
		t.Fatalf("EventAdmissionPolicy: %v", err)
	}
	policy := admissionFixturePolicy()
	policy.Mode = "enforce"
	highRate := EventAdmissionRate{PerSecond: 1000, Burst: 1000}
	policy.Limits.ProducerPublications = highRate
	policy.Limits.ProducerDeliveries = highRate
	policy.Limits.OwnerPublications = highRate
	policy.Limits.OwnerDeliveries = highRate
	policy.Limits.CollectionPublications = highRate
	policy.Limits.CollectionDeliveries = highRate
	if configure != nil {
		configure(policy.Limits)
	}
	state, err := op.SetEventAdmissionPolicy(ctx, SetEventAdmissionPolicyRequest{
		RequestID:        uuid.NewString(),
		Repo:             repo,
		ExpectedRevision: cur.Revision,
		Policy:           policy,
	})
	if err != nil {
		t.Fatalf("SetEventAdmissionPolicy(enforce): %v", err)
	}
	return state
}

// requireCapacityRefusal verifies Sol's typed EVENT_ADMISSION_REFUSED contract
// and safe dimension metadata.
func requireCapacityRefusal(t *testing.T, err error, wantDimension string) string {
	t.Helper()
	if err == nil {
		t.Fatalf("expected EVENT_ADMISSION_REFUSED (%q), got nil", wantDimension)
	}
	var e *Error
	if !errors.As(err, &e) {
		t.Fatalf("expected *core.Error, got %T: %v", err, err)
	}
	if e.Code != "EVENT_ADMISSION_REFUSED" || e.Admission == nil || !e.Admission.Retryable {
		t.Fatalf("expected retryable EVENT_ADMISSION_REFUSED with Admission details, got code=%s admission=%+v err=%v", e.Code, e.Admission, err)
	}
	if wantDimension != "" && !strings.Contains(e.Admission.Dimension, wantDimension) {
		t.Fatalf("expected refusal dimension containing %q, got %q (%v)", wantDimension, e.Admission.Dimension, err)
	}
	return e.Code
}

func TestAlternateAdmission_ScheduleFairSweepRotatesBlockedAndFencesRestore(t *testing.T) {
	ctx := context.Background()
	_, consumerA, op, source, _ := reissueFixture(t)
	repo := source.Scope.Repo
	consumerB := testStore(t, Channel{Principal: "agent:consumer-b:" + uuid.NewString(), Repo: repo})
	consumerC := testStore(t, Channel{Principal: "agent:consumer-c:" + uuid.NewString(), Repo: repo})

	for _, s := range []*Store{consumerA, consumerB} {
		if _, err := s.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: repo, Topic: "sched-topic", Active: true}); err != nil {
			t.Fatal(err)
		}
	}

	enablePublicEnforcePolicy(t, op, repo, func(l *EventAdmissionLimits) {
		l.MaxTopicRecipients = 2
		l.MaxRecipientOutstanding = 4
		l.MaxProducerRecipientOutstanding = 4
		l.MaxCollectionOutstanding = 8
	})

	// Schedule S1 (topic) while topic has 2 subscribers, then add a 3rd subscriber
	// before firing so S1 evaluates actual firing-time snapshot (> MaxTopicRecipients).
	s1, err := op.ScheduleEvent(ctx, ScheduleEventRequest{
		RequestID:    uuid.NewString(),
		NotBefore:    time.Now().Add(-2 * time.Second),
		GraceSeconds: 300,
		Publication: PublishEventRequest{
			Repo:        repo,
			Kind:        "notice",
			Ref:         RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
			Destination: EventDestination{Type: "topic", Name: "sched-topic"},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	// Schedule S2 (direct to consumerB) behind S1; S2 is healthy and must not starve.
	s2, err := op.ScheduleEvent(ctx, ScheduleEventRequest{
		RequestID:    uuid.NewString(),
		NotBefore:    time.Now().Add(-1 * time.Second),
		GraceSeconds: 300,
		Publication: PublishEventRequest{
			Repo:        repo,
			Kind:        "notice",
			Ref:         RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
			Destination: EventDestination{Type: "agent", Name: consumerB.channel.Principal},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = consumerC.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: repo, Topic: "sched-topic", Active: true}); err != nil {
		t.Fatal(err)
	}

	// Bounded Limit=1 tick1 hits S1, rolls back savepoint, keeps S1 pending, rotates last_checked_at.
	tick1, err := op.TickSchedules(ctx, ScheduleTickRequest{Repo: repo, Limit: 1})
	if err != nil || len(tick1.Occurrences) != 0 {
		t.Fatalf("tick1 expected 0 terminal occurrences on transient capacity refusal: %+v %v", tick1, err)
	}
	s1Page, err := op.Schedules(ctx, ScheduleQuery{Repo: repo, OccurrenceID: s1.OccurrenceID})
	if err != nil || len(s1Page.Occurrences) != 1 || s1Page.Occurrences[0].State != "pending" || s1Page.Occurrences[0].Code != "EVENT_ADMISSION_REFUSED" || s1Page.Occurrences[0].EventID != "" {
		t.Fatalf("S1 should remain pending with EVENT_ADMISSION_REFUSED and no committed event: %+v %v", s1Page, err)
	}
	schedRefusals, err := op.EventAdmissionRefusals(ctx, EventAdmissionPolicyQuery{Repo: repo})
	if err != nil || len(schedRefusals) != 1 || schedRefusals[0].Dimension != "topic_recipients" || schedRefusals[0].RefusalCount != 1 {
		t.Fatalf("expected retained schedule topic_recipients refusal diagnostic: %+v %v", schedRefusals, err)
	}

	// Bounded Limit=1 tick2 rotates past S1 and fires healthy S2 immediately.
	tick2, err := op.TickSchedules(ctx, ScheduleTickRequest{Repo: repo, Limit: 1})
	if err != nil || len(tick2.Occurrences) != 1 || tick2.Occurrences[0].OccurrenceID != s2.OccurrenceID || tick2.Occurrences[0].State != "fired" {
		t.Fatalf("tick2 failed fair rotation to healthy S2: %+v %v", tick2, err)
	}

	// Post-commit telemetry fault: when S1 is refused alongside healthy S2b in a single tick
	// and refusal diagnostic persistence fails, S2b stays durably committed while TickSchedules
	// surfaces REFUSAL_UNRECORDED with committed Occurrences intact.
	s2b, err := op.ScheduleEvent(ctx, ScheduleEventRequest{
		RequestID:    uuid.NewString(),
		NotBefore:    time.Now().Add(-time.Second),
		GraceSeconds: 300,
		Publication: PublishEventRequest{
			Repo:        repo,
			Kind:        "notice",
			Ref:         RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
			Destination: EventDestination{Type: "agent", Name: consumerB.channel.Principal},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	trig := "sched_refusal_probe_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	trigSQL := fmt.Sprintf(`CREATE FUNCTION cairn.%s() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected refusal telemetry fault'; END $$; CREATE TRIGGER %s BEFORE INSERT OR UPDATE ON cairn.agent_event_admission_refusal FOR EACH ROW WHEN (NEW.repo='%s') EXECUTE FUNCTION cairn.%s()`, trig, trig, repo, trig)
	if _, err = op.pool.Exec(ctx, trigSQL); err != nil {
		t.Fatal(err)
	}
	defer op.pool.Exec(ctx, fmt.Sprintf(`DROP TRIGGER IF EXISTS %s ON cairn.agent_event_admission_refusal; DROP FUNCTION IF EXISTS cairn.%s()`, trig, trig))
	tickFault, err := op.TickSchedules(ctx, ScheduleTickRequest{Repo: repo, Limit: 2})
	requireCode(t, err, "REFUSAL_UNRECORDED")
	if len(tickFault.Occurrences) != 1 || tickFault.Occurrences[0].OccurrenceID != s2b.OccurrenceID || tickFault.Occurrences[0].State != "fired" {
		t.Fatalf("expected committed S2b preserved across post-commit telemetry fault: %+v %v", tickFault, err)
	}
	if _, err = op.pool.Exec(ctx, fmt.Sprintf(`DROP TRIGGER %s ON cairn.agent_event_admission_refusal; DROP FUNCTION cairn.%s()`, trig, trig)); err != nil {
		t.Fatal(err)
	}

	// Unsubscribing consumerC brings topic back to 2 recipients; S1 fires on next tick.
	if _, err = consumerC.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: repo, Topic: "sched-topic", Active: false}); err != nil {
		t.Fatal(err)
	}
	tick3, err := op.TickSchedules(ctx, ScheduleTickRequest{Repo: repo, Limit: 1})
	if err != nil || len(tick3.Occurrences) != 1 || tick3.Occurrences[0].OccurrenceID != s1.OccurrenceID || tick3.Occurrences[0].State != "fired" {
		t.Fatalf("tick3 expected S1 to fire once topic fanout returned within limit: %+v %v", tick3, err)
	}

	// RATE_UNRECONCILED on one bucket (e.g. producer delivery bucket) must keep sUnrec
	// pending with actionable Code="RATE_UNRECONCILED" and rotate without aborting unrelated
	// due work (sEmptyTopic, cost 0 deliveries) in the same TickSchedules batch.
	if _, err = op.pool.Exec(ctx, `INSERT INTO cairn.agent_event_rate_bucket(repo,identity_kind,identity,unit,tokens_nano,refill_at,policy_revision,restore_generation)
		SELECT $1,'producer',$2,'delivery',1000000000000,clock_timestamp(),1,generation+1 FROM cairn.retrieval_generation WHERE singleton
		ON CONFLICT(repo,identity_kind,identity,unit) DO UPDATE SET restore_generation=EXCLUDED.restore_generation`,
		repo, op.channel.Principal); err != nil {
		t.Fatal(err)
	}
	sUnrec, err := op.ScheduleEvent(ctx, ScheduleEventRequest{
		RequestID:    uuid.NewString(),
		NotBefore:    time.Now().Add(-2 * time.Second),
		GraceSeconds: 300,
		Publication: PublishEventRequest{
			Repo:        repo,
			Kind:        "notice",
			Ref:         RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
			Destination: EventDestination{Type: "agent", Name: consumerB.channel.Principal},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	sEmptyTopic, err := op.ScheduleEvent(ctx, ScheduleEventRequest{
		RequestID:    uuid.NewString(),
		NotBefore:    time.Now().Add(-time.Second),
		GraceSeconds: 300,
		Publication: PublishEventRequest{
			Repo:        repo,
			Kind:        "notice",
			Ref:         RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
			Destination: EventDestination{Type: "topic", Name: "empty-unreconciled-peer"},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	tickUnrec, err := op.TickSchedules(ctx, ScheduleTickRequest{Repo: repo, Limit: 2})
	if err != nil || len(tickUnrec.Occurrences) != 1 || tickUnrec.Occurrences[0].OccurrenceID != sEmptyTopic.OccurrenceID || tickUnrec.Occurrences[0].State != "fired" {
		t.Fatalf("expected RATE_UNRECONCILED sUnrec not to abort sEmptyTopic in same tick: %+v %v", tickUnrec, err)
	}
	sUnrecPage, err := op.Schedules(ctx, ScheduleQuery{Repo: repo, OccurrenceID: sUnrec.OccurrenceID})
	if err != nil || len(sUnrecPage.Occurrences) != 1 || sUnrecPage.Occurrences[0].State != "pending" || sUnrecPage.Occurrences[0].Code != "RATE_UNRECONCILED" || sUnrecPage.Occurrences[0].EventID != "" {
		t.Fatalf("expected sUnrec pending with actionable RATE_UNRECONCILED code and no fabricated completion: %+v %v", sUnrecPage, err)
	}
	if _, err = op.pool.Exec(ctx, `UPDATE cairn.agent_event_rate_bucket SET restore_generation=(SELECT generation FROM cairn.retrieval_generation WHERE singleton) WHERE repo=$1 AND identity_kind='producer' AND identity=$2 AND unit='delivery'`, repo, op.channel.Principal); err != nil {
		t.Fatal(err)
	}
	tickRepaired, err := op.TickSchedules(ctx, ScheduleTickRequest{Repo: repo, Limit: 1})
	if err != nil || len(tickRepaired.Occurrences) != 1 || tickRepaired.Occurrences[0].OccurrenceID != sUnrec.OccurrenceID || tickRepaired.Occurrences[0].State != "fired" {
		t.Fatalf("expected sUnrec to fire once rate bucket generation was repaired: %+v %v", tickRepaired, err)
	}

	// Schedule S3 blocked by closure saturation, then FenceRestore must fence S3 as skipped/restore_fenced.
	enablePublicEnforcePolicy(t, op, repo, func(l *EventAdmissionLimits) {
		l.MaxClosureReserved = 1
		l.MaxRecipientClosureReserved = 1
		l.MaxCollectionOutstanding = 16
		l.MaxRecipientOutstanding = 8
		l.MaxProducerRecipientOutstanding = 8
	})
	if _, err = op.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Repo:        repo,
		Kind:        "request",
		Ref:         RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination: EventDestination{Type: "agent", Name: consumerA.channel.Principal},
	}, Destination{Name: "local", AllowLocal: true}); err != nil {
		t.Fatal(err)
	}
	s3, err := op.ScheduleEvent(ctx, ScheduleEventRequest{
		RequestID:    uuid.NewString(),
		NotBefore:    time.Now().Add(-time.Second),
		GraceSeconds: 300,
		Publication: PublishEventRequest{
			Repo:        repo,
			Kind:        "request",
			Ref:         RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
			Destination: EventDestination{Type: "agent", Name: consumerA.channel.Principal},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = op.TickSchedules(ctx, ScheduleTickRequest{Repo: repo, Limit: 1}); err != nil {
		t.Fatal(err)
	}
	if _, err = op.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "schedule saturation restore fence"}); err != nil {
		t.Fatal(err)
	}
	s3Page, err := op.Schedules(ctx, ScheduleQuery{Repo: repo, OccurrenceID: s3.OccurrenceID})
	if err != nil || len(s3Page.Occurrences) != 1 || s3Page.Occurrences[0].State != "skipped" || s3Page.Occurrences[0].Code != "restore_fenced" {
		t.Fatalf("FenceRestore did not fence capacity-deferred schedule S3: %+v %v", s3Page, err)
	}
}

func TestAlternateAdmission_ReissueRollbackAndSameUUIDRetry(t *testing.T) {
	ctx := context.Background()
	pub, consumer, op, source, dest := reissueFixture(t)
	repo := source.Scope.Repo

	// Create and fail an original request delivery to consumer.
	origEvt := publishFixture(t, pub, consumer, source, dest)
	failedDel := nextFixture(t, consumer, dest)
	forgeFailedDelivery(t, consumer, failedDel.DeliveryID)

	// Fill consumer's normal backlog to MaxRecipientOutstanding=1 with a pending notice.
	blockerEvt, err := pub.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Repo:        repo,
		Kind:        "notice",
		Ref:         RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination: EventDestination{Type: "agent", Name: consumer.channel.Principal},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	enablePublicEnforcePolicy(t, op, repo, func(l *EventAdmissionLimits) {
		l.MaxRecipientOutstanding = 1
		l.MaxProducerRecipientOutstanding = 2
		l.MaxCollectionOutstanding = 4
		l.MaxClosureReserved = 4
		l.MaxRecipientClosureReserved = 2
	})

	reissueReq := ReissueEventRequest{
		RequestID:              uuid.NewString(),
		Repo:                   repo,
		DeliveryID:             failedDel.DeliveryID,
		Reason:                 "reissue under saturated consumer backlog",
		AcceptUncertainEffects: true,
	}
	_, err = op.ReissueEvent(ctx, reissueReq)
	requireCapacityRefusal(t, err, "recipient_outstanding")

	// Refusal must leave zero trace, recovery, event, delivery, closure, or mutation_request rows.
	var reissuedCount, mutCount, eventCount int
	if err = op.pool.QueryRow(ctx, `SELECT
		(SELECT count(*) FROM cairn.agent_event_reissue WHERE delivery_id=$1),
		(SELECT count(*) FROM cairn.mutation_request WHERE request_id=$2),
		(SELECT count(*) FROM cairn.agent_event WHERE repo=$3)`,
		failedDel.DeliveryID, reissueReq.RequestID, repo,
	).Scan(&reissuedCount, &mutCount, &eventCount); err != nil {
		t.Fatal(err)
	}
	if reissuedCount != 0 || mutCount != 0 || eventCount != 2 {
		t.Fatalf("refused ReissueEvent left partial state: reissued=%d mut=%d events=%d (orig=%s blocker=%s)", reissuedCount, mutCount, eventCount, origEvt.EventID, blockerEvt.EventID)
	}

	// Drain the blocking notice on consumer; retry ReissueEvent with the SAME RequestID.
	blockerDel := nextFixture(t, consumer, dest)
	if _, err = consumer.CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  blockerDel.DeliveryID,
		LeaseID:     blockerDel.LeaseID,
		Disposition: "handled",
	}, dest); err != nil {
		t.Fatal(err)
	}

	reissued, err := op.ReissueEvent(ctx, reissueReq)
	if err != nil {
		t.Fatalf("same-UUID ReissueEvent after drain failed: %v", err)
	}
	// Idempotent replay of committed ReissueEvent returns the same event with no extra obligation.
	replay, err := op.ReissueEvent(ctx, reissueReq)
	if err != nil || replay.EventID != reissued.EventID {
		t.Fatalf("committed ReissueEvent replay mismatch: %+v %v", replay, err)
	}
	resProbe := closureForRequest(t, op, reissued.EventID)
	if resProbe.state != "reserved" || resProbe.recipient != op.channel.Principal || resProbe.sender != consumer.channel.Principal {
		t.Fatalf("reissued request closure obligation mismatch: %+v", resProbe)
	}
}

func TestAlternateAdmission_PoolAdmitAndAssignTransferUnderLoweredLimits(t *testing.T) {
	ctx := context.Background()
	op, pub, workerAStore, source, dest, workerA := poolFixture(t)
	repo := source.Scope.Repo
	workerBStore := testStore(t, Channel{Principal: "worker-b-" + uuid.NewString(), Repo: repo})
	workerB, err := workerBStore.RegisterWorkerSlot(ctx, WorkerRegisterRequest{
		RequestID: uuid.NewString(),
		Spec:      workerA.Spec,
	}, dest)
	if err != nil {
		t.Fatal(err)
	}

	enablePublicEnforcePolicy(t, op, repo, func(l *EventAdmissionLimits) {
		l.MaxCollectionOutstanding = 2
		l.MaxRecipientOutstanding = 1
		l.MaxProducerRecipientOutstanding = 2
		l.MaxClosureReserved = 4
		l.MaxRecipientClosureReserved = 4
	})

	// Saturate workerA's direct inbox (1 pending notice == MaxRecipientOutstanding=1).
	if _, err = pub.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Repo:        repo,
		Kind:        "notice",
		Ref:         RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination: EventDestination{Type: "agent", Name: workerAStore.channel.Principal},
	}, dest); err != nil {
		t.Fatal(err)
	}

	// Admit 1 pool request (consumes the 2nd collection slot: 1 direct + 1 unassigned pool == 2).
	needs := PoolRequirements{Workspace: "/fixture/rhumb", Capabilities: []string{"review"}}
	poolEvt := poolPublish(t, pub, source, dest, needs)

	// A 2nd pool request must be refused at admitPool / PublishEvent because collection outstanding == 2.
	_, err = pub.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Repo:        repo,
		Kind:        "request",
		Ref:         RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination: EventDestination{Type: "pool", Name: "coding"},
		Pool:        &needs,
	}, dest)
	requireCapacityRefusal(t, err, "collection_outstanding")

	// Lower MaxCollectionOutstanding to 1 (queue is now above limit: 1 direct + 1 pool = 2 > 1).
	// Already-accepted pool work must STILL transfer cleanly into a worker delivery without double-charging!
	enablePublicEnforcePolicy(t, op, repo, func(l *EventAdmissionLimits) {
		l.MaxCollectionOutstanding = 1
		l.MaxRecipientOutstanding = 1
		l.MaxProducerRecipientOutstanding = 2
		l.MaxClosureReserved = 4
		l.MaxRecipientClosureReserved = 4
	})

	// workerB has 0 direct deliveries (< MaxRecipientOutstanding=1) and claims the pool request;
	// pool_reply transfers from unbound -> bound without a second collection or closure charge.
	claimed := poolClaim(t, workerBStore, workerB, dest)
	if claimed == nil || claimed.Delivery.Event.EventID != poolEvt.EventID || claimed.Delivery.Consumer != workerBStore.channel.Principal {
		t.Fatalf("expected pool transfer to unsaturated workerB, got %+v", claimed)
	}
	probe := closureForRequest(t, pub, poolEvt.EventID)
	if probe.kind != ClosurePoolReply || probe.state != "reserved" || probe.sender != workerBStore.channel.Principal || probe.revision != 2 {
		t.Fatalf("pool closure reservation did not transfer cleanly on assignPool: %+v", probe)
	}
	finishPool(t, workerBStore, *claimed, dest)
}

func TestAlternateAdmission_QueuedQuietNoticeTransfersOnClaimAdvanceFlushAndRestore(t *testing.T) {
	ctx := context.Background()
	repo := "quiet-transfer-" + uuid.NewString()
	f := setupOrganicFixture(t, repo)
	f.activateCapability(t)

	enablePublicEnforcePolicy(t, f.operator, repo, func(l *EventAdmissionLimits) {
		l.MaxRecipientOutstanding = 3
		l.MaxProducerRecipientOutstanding = 3
		l.MaxCollectionOutstanding = 3
	})

	// Admit 3 quiet next-turn notices (reaching MaxRecipientOutstanding=3 & MaxCollectionOutstanding=3).
	n1 := f.publishNotice(t, "quiet-1")
	n2 := f.publishNotice(t, "quiet-2")
	n3 := f.publishNotice(t, "quiet-3")

	// 4th quiet notice must be refused at PublishEvent because queued organic notices count toward outstanding.
	expiresAt := time.Now().UTC().Add(2 * time.Hour)
	_, err := f.publisher.PublishEvent(ctx, PublishEventRequest{
		RequestID:       uuid.NewString(),
		Repo:            repo,
		Kind:            "notice",
		Ref:             RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
		NoticeDelivery:  "next_organic_turn",
		NoticeExpiresAt: &expiresAt,
	}, f.dest)
	requireCapacityRefusal(t, err, "outstanding")

	// Lower limits to 1 while 3 quiet notices are queued: ClaimOrganicNotice, AdvanceOrganicNotice,
	// and FlushNotice MUST transfer the existing queued charge into agent_delivery without deadlocking!
	enablePublicEnforcePolicy(t, f.operator, repo, func(l *EventAdmissionLimits) {
		l.MaxRecipientOutstanding = 1
		l.MaxProducerRecipientOutstanding = 1
		l.MaxCollectionOutstanding = 1
	})

	att, payload := f.signAttestation(t, "turn-transfer-1", "owner_composer", "fresh_start", uuid.NewString(), time.Now().UnixMilli())
	claim1, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               f.sessionRef,
		NativeOwnerTurnID:     payload.NativeTurnID,
		OwnerInputAttestation: &att,
	}, f.dest)
	if err != nil || claim1.Decision != "admitted" || claim1.Attempt == nil || claim1.Attempt.Delivery.Event.EventID != n1.EventID {
		t.Fatalf("ClaimOrganicNotice failed reserve-to-delivery transfer under lowered limit: %+v %v", claim1, err)
	}
	if _, err = f.consumerStore(t).CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  claim1.Attempt.Delivery.DeliveryID,
		LeaseID:     claim1.Attempt.Delivery.LeaseID,
		Disposition: "handled",
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	adv2, err := f.recipient.AdvanceOrganicNotice(ctx, SessionOrganicNoticeAdvance{
		RequestID: uuid.NewString(),
		Session:   f.sessionRef,
		AttemptID: claim1.Attempt.ID,
		RootID:    claim1.RootID,
		Version:   1,
	}, f.dest)
	if err != nil || adv2.Attempt == nil || adv2.Attempt.Delivery.Event.EventID != n2.EventID {
		t.Fatalf("AdvanceOrganicNotice failed reserve-to-delivery transfer: %+v %v", adv2, err)
	}
	if _, err = f.consumerStore(t).CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  adv2.Attempt.Delivery.DeliveryID,
		LeaseID:     adv2.Attempt.Delivery.LeaseID,
		Disposition: "handled",
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	// FlushNotice on n3 transfers queued -> pending agent_delivery without double-charging.
	flushed, err := f.publisher.FlushNotice(ctx, NoticeFlushRequest{
		RequestID: uuid.NewString(),
		EventID:   n3.EventID,
	}, f.dest)
	if err != nil || flushed.Released != 1 {
		t.Fatalf("FlushNotice failed reserve-to-delivery transfer: %+v %v", flushed, err)
	}
	d3 := nextFixture(t, f.consumerStore(t), f.dest)
	if _, err = f.consumerStore(t).CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  d3.DeliveryID,
		LeaseID:     d3.LeaseID,
		Disposition: "handled",
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	// Queue one more quiet notice, then FenceRestore must fence it so post-restore capacity is freed.
	_ = f.publishNotice(t, "quiet-before-restore")
	if _, err = f.operator.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "quiet notice restore fence"}); err != nil {
		t.Fatal(err)
	}
	pressure, err := f.operator.CollectionEventPressure(ctx, EventPressureQuery{Repo: repo})
	if err != nil || pressure.Reservoirs.QueuedOrganicNotices != 0 {
		t.Fatalf("FenceRestore left queued organic notice charge active: %+v %v", pressure, err)
	}
	if _, err = f.operator.ReconcileClosureReservations(ctx, ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: repo}); err != nil {
		t.Fatal(err)
	}
	if _, err = f.publisher.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Repo:        repo,
		Kind:        "notice",
		Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
	}, f.dest); err != nil {
		t.Fatalf("post-restore publish blocked by restore_fenced organic notice under MaxRecipientOutstanding=1: %v", err)
	}
}

func TestAlternateAdmission_StoppedNoticeRollbackAndDeterministicSameUUIDRetry(t *testing.T) {
	ctx := context.Background()
	f := newHealthFixture(t)
	repo := f.source.Scope.Repo
	op := testStore(t, Channel{Principal: "stopped-cap-op-" + repo, Operator: true})

	// Send 1 notice from f.sender to stopped-candidate session f.agent, then stop f.agent.
	origEvt, stoppedDelID := f.publish("notice", f.agent.Inbox)
	if _, err := f.receiver.LeaveAgent(ctx, f.ref, f.dest); err != nil {
		t.Fatal(err)
	}

	// Fill f.sender's own inbox with 1 pending notice from a peer so f.sender hits MaxRecipientOutstanding=1.
	peer := testStore(t, Channel{Principal: "peer-" + repo, Repo: repo})
	peerDraft := projectNote(repo)
	peerDraft.Sensitivity = "shareable"
	peerNote, err := peer.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: peerDraft})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = peer.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Repo:        repo,
		Kind:        "notice",
		Ref:         RecordVersionRef{RecordID: peerNote.RecordID, Version: peerNote.Version},
		Destination: EventDestination{Type: "agent", Name: f.sender.channel.Principal},
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	enablePublicEnforcePolicy(t, op, repo, func(l *EventAdmissionLimits) {
		l.MaxRecipientOutstanding = 1
		l.MaxProducerRecipientOutstanding = 2
		l.MaxCollectionOutstanding = 8
		l.MaxClosureReserved = 4
		l.MaxRecipientClosureReserved = 4
	})

	var recordsBefore, versionsBefore, eventsBefore int
	if err = op.pool.QueryRow(ctx, `SELECT
		(SELECT count(*) FROM cairn.memory_record),
		(SELECT count(*) FROM cairn.record_version WHERE repo=$1),
		(SELECT count(*) FROM cairn.agent_event WHERE repo=$1)`, repo,
	).Scan(&recordsBefore, &versionsBefore, &eventsBefore); err != nil {
		t.Fatal(err)
	}

	review, err := op.ReviewEvents(ctx, EventReviewRequest{Repo: repo, Consumer: f.agent.Inbox, State: "pending"})
	if err != nil || len(review.Deliveries) != 1 || review.Deliveries[0].DeliveryID != stoppedDelID {
		t.Fatalf("expected 1 stopped pending delivery: %+v %v", review, err)
	}
	now := dbNow(t, op).Add(2 * time.Second)
	prev := AttentionCache{Schema: "cairn.delivery-attention/1", Scope: repo, Deliveries: map[string]AttentionEntry{}}
	cur := AttentionCache{Schema: "cairn.delivery-attention/1", Scope: repo, Deliveries: map[string]AttentionEntry{}}

	// First review under saturated publisher inbox: must defer cleanly with 0 committed rows and 0 closure reservations.
	batch1, cache1, err := NotifyStoppedPublishers(ctx, op, repo, review.Deliveries, prev, cur, now)
	if err != nil {
		t.Fatalf("NotifyStoppedPublishers under saturation returned error instead of deferring: %v", err)
	}
	if batch1.Attempted != 1 || batch1.Deferred != 1 || batch1.Sent != 0 || len(batch1.Results) != 1 || batch1.Results[0].Status != "deferred_capacity" || batch1.Results[0].Reason != "recipient_outstanding" {
		t.Fatalf("expected deferred_capacity(recipient_outstanding) batch: %+v", batch1)
	}
	detReqID := batch1.Results[0].NotificationID
	if cache1.Deliveries[stoppedDelID].NotifiedFingerprint != "" {
		t.Fatalf("deferred notification poisoned AttentionCache: %+v", cache1)
	}

	var recordsAfter, versionsAfter, eventsAfter, mutCount, closureCount int
	if err = op.pool.QueryRow(ctx, `SELECT
		(SELECT count(*) FROM cairn.memory_record),
		(SELECT count(*) FROM cairn.record_version WHERE repo=$1),
		(SELECT count(*) FROM cairn.agent_event WHERE repo=$1),
		(SELECT count(*) FROM cairn.mutation_request WHERE request_id=$2),
		(SELECT count(*) FROM cairn.agent_closure_reservation WHERE repo=$1)`,
		repo, detReqID,
	).Scan(&recordsAfter, &versionsAfter, &eventsAfter, &mutCount, &closureCount); err != nil {
		t.Fatal(err)
	}
	if recordsAfter != recordsBefore || versionsAfter != versionsBefore || eventsAfter != eventsBefore || mutCount != 0 || closureCount != 0 {
		t.Fatalf("deferred stopped notice leaked rows: records=%d->%d versions=%d->%d events=%d->%d mut=%d closures=%d", recordsBefore, recordsAfter, versionsBefore, versionsAfter, eventsBefore, eventsAfter, mutCount, closureCount)
	}
	refusals, err := op.EventAdmissionRefusals(ctx, EventAdmissionPolicyQuery{Repo: repo})
	if err != nil || len(refusals) != 1 || refusals[0].Dimension != "recipient_outstanding" || refusals[0].RefusalCount != 1 {
		t.Fatalf("expected retained stopped-notice refusal diagnostic: %+v %v", refusals, err)
	}

	// Drain f.sender's blocking notice, then re-run NotifyStoppedPublishers: SAME deterministic UUID must succeed!
	claimedBlocker, err := f.sender.NextEvent(ctx, NextEventRequest{}, f.dest)
	if err != nil || claimedBlocker.Delivery == nil {
		t.Fatalf("claim blocker on sender: %+v %v", claimedBlocker, err)
	}
	if _, err = f.sender.CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  claimedBlocker.Delivery.DeliveryID,
		LeaseID:     claimedBlocker.Delivery.LeaseID,
		Disposition: "handled",
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	cur2 := AttentionCache{Schema: "cairn.delivery-attention/1", Scope: repo, Deliveries: map[string]AttentionEntry{}}
	batch2, cache2, err := NotifyStoppedPublishers(ctx, op, repo, review.Deliveries, cache1, cur2, now.Add(time.Second))
	if err != nil || batch2.Sent != 1 || batch2.Deferred != 0 || len(batch2.Results) != 1 || batch2.Results[0].NotificationID != detReqID || batch2.Results[0].Status != "sent" {
		t.Fatalf("deterministic retry after drain failed: %+v %v", batch2, err)
	}
	claimedNotice, err := f.sender.NextEvent(ctx, NextEventRequest{}, f.dest)
	if err != nil || claimedNotice.Delivery == nil || claimedNotice.Delivery.Event.EventID != batch2.Results[0].NotificationEventID || claimedNotice.Delivery.Event.CausationID != origEvt.EventID {
		t.Fatalf("sender did not receive retried stopped-delivery notice: %+v %v", claimedNotice, err)
	}
	// Third pass with updated cache suppresses duplicate with same deterministic NotificationID.
	cur3 := AttentionCache{Schema: "cairn.delivery-attention/1", Scope: repo, Deliveries: map[string]AttentionEntry{}}
	batch3, _, err := NotifyStoppedPublishers(ctx, op, repo, review.Deliveries, cache2, cur3, now.Add(2*time.Second))
	if err != nil || batch3.Suppressed != 1 || batch3.Results[0].NotificationID != detReqID || batch3.Results[0].Status != "duplicate_suppressed" {
		t.Fatalf("expected duplicate_suppressed on 3rd review: %+v %v", batch3, err)
	}
}

func TestEventAdmissionEvaluator_ScheduleStoppedAndOrganicAccounting(t *testing.T) {
	ctx := context.Background()
	repo := "alt-evaluator-" + uuid.NewString()
	f := setupOrganicFixture(t, repo)
	f.activateCapability(t)
	op := f.operator

	subA := testStore(t, Channel{Principal: "sched-sub-a-" + uuid.NewString(), Repo: repo})
	subB := testStore(t, Channel{Principal: "sched-sub-b-" + uuid.NewString(), Repo: repo})
	for _, s := range []*Store{subA, subB} {
		if _, err := s.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: repo, Topic: "eval-topic", Active: true}); err != nil {
			t.Fatal(err)
		}
	}

	state := testAdmissionState(t, f.publisher, repo)
	state.Policy.Limits.MaxTopicRecipients = 1
	state.Policy.Limits.MaxCollectionOutstanding = 3
	state.Policy.Limits.MaxRecipientOutstanding = 1
	state.Policy.Limits.MaxProducerRecipientOutstanding = 2

	// 1. Scheduled topic publication evaluates firing-time active subscriber snapshot.
	sched, err := op.ScheduleEvent(ctx, ScheduleEventRequest{
		RequestID:    uuid.NewString(),
		NotBefore:    time.Now().Add(-time.Second),
		GraceSeconds: 300,
		Publication: PublishEventRequest{
			Repo:        repo,
			Kind:        "notice",
			Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
			Destination: EventDestination{Type: "topic", Name: "eval-topic"},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	tx1, err := op.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.checkEventAdmissionTx(ctx, tx1, sched.Publication, state, nil)
	_ = tx1.Rollback(ctx)
	requireCapacityRefusal(t, err, "topic_recipients")

	// 2. Queued quiet notice charges recipient_outstanding; Stopped-notice shape to saturated
	// recipient refuses cleanly, and FenceRestore releases the queued organic charge immediately.
	n1 := f.publishNotice(t, "quiet-eval-1")
	stoppedLikeReq := PublishEventRequest{
		Repo:        repo,
		Kind:        "notice",
		Destination: EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
	}
	tx2, err := op.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.checkEventAdmissionTx(ctx, tx2, stoppedLikeReq, state, nil)
	_ = tx2.Rollback(ctx)
	requireCapacityRefusal(t, err, "recipient_outstanding")

	if _, err = f.publisher.FlushNotice(ctx, NoticeFlushRequest{RequestID: uuid.NewString(), EventID: n1.EventID}, f.dest); err != nil {
		t.Fatal(err)
	}
	d1 := nextFixture(t, f.consumerStore(t), f.dest)
	if _, err = f.consumerStore(t).CompleteEvent(ctx, CompleteEventRequest{
		RequestID: uuid.NewString(), DeliveryID: d1.DeliveryID, LeaseID: d1.LeaseID, Disposition: "handled",
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	_ = f.publishNotice(t, "quiet-eval-before-restore")
	if _, err = op.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "evaluator restore fence"}); err != nil {
		t.Fatal(err)
	}
	tx3, err := op.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.checkEventAdmissionTx(ctx, tx3, stoppedLikeReq, state, nil)
	_ = tx3.Rollback(ctx)
	if err != nil {
		t.Fatalf("FenceRestore left queued organic notice or pending schedule blocking admission: %v", err)
	}
}

func TestAlternateAdmission_StoppedNoticeBlockedRaceDefersOnConcurrentPublish(t *testing.T) {
	ctx := context.Background()
	f := newHealthFixture(t)
	repo := f.source.Scope.Repo
	op := testStore(t, Channel{Principal: "stopped-blocked-race-op-" + repo, Operator: true})

	_, stoppedDelID := f.publish("notice", f.agent.Inbox)
	if _, err := f.receiver.LeaveAgent(ctx, f.ref, f.dest); err != nil {
		t.Fatal(err)
	}

	peer := testStore(t, Channel{Principal: "peer-race-" + repo, Repo: repo})
	peerDraft := projectNote(repo)
	peerDraft.Sensitivity = "shareable"
	peerNote, err := peer.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: peerDraft})
	if err != nil {
		t.Fatal(err)
	}

	enablePublicEnforcePolicy(t, op, repo, func(l *EventAdmissionLimits) {
		l.MaxRecipientOutstanding = 1
		l.MaxProducerRecipientOutstanding = 2
		l.MaxCollectionOutstanding = 8
		l.MaxClosureReserved = 4
		l.MaxRecipientClosureReserved = 4
	})

	var recordsBefore, versionsBefore, eventsBefore int
	if err = op.pool.QueryRow(ctx, `SELECT
		(SELECT count(*) FROM cairn.memory_record),
		(SELECT count(*) FROM cairn.record_version WHERE repo=$1),
		(SELECT count(*) FROM cairn.agent_event WHERE repo=$1)`, repo,
	).Scan(&recordsBefore, &versionsBefore, &eventsBefore); err != nil {
		t.Fatal(err)
	}

	// peer holds collection lock while NotifyStoppedDeliveryPublisher begins its transaction
	// and blocks waiting on agent-events:<repo>. Competing ordinary publish commits first
	// (filling f.sender to MaxRecipientOutstanding=1); under ReadCommitted, the unblocked
	// notification must see the new delivery and defer with zero committed source/event/receipt.
	holdTx, err := peer.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer holdTx.Rollback(ctx)
	if err = lock(ctx, holdTx, "agent-events:"+repo); err != nil {
		t.Fatal(err)
	}

	notifyReq := StoppedDeliveryNotificationRequest{
		RequestID: uuid.NewString(), Repo: repo, DeliveryID: stoppedDelID,
		RecipientAgentID: f.agent.AgentID, RecipientExecution: f.agent.ExecutionID,
		Stage: "waiting", Fingerprint: "waiting|never_launched|not_admitted||",
	}
	type notifyOut struct {
		res StoppedDeliveryNotificationResult
		err error
	}
	done := make(chan notifyOut, 1)
	early := make(chan struct{})
	go func() {
		defer close(early)
		r, e := op.NotifyStoppedDeliveryPublisher(ctx, notifyReq)
		done <- notifyOut{r, e}
	}()
	_ = waitForAdvisoryLockPIDs(t, ctx, op, "agent-events:"+repo, 1, early)

	if _, err = peer.publishEventTx(ctx, holdTx, PublishEventRequest{
		Repo:        repo,
		Kind:        "notice",
		Ref:         RecordVersionRef{RecordID: peerNote.RecordID, Version: peerNote.Version},
		Destination: EventDestination{Type: "agent", Name: f.sender.channel.Principal},
	}, f.dest, ""); err != nil {
		t.Fatal(err)
	}
	if err = holdTx.Commit(ctx); err != nil {
		t.Fatal(err)
	}

	out := <-done
	if out.err != nil || out.res.Status != "deferred_capacity" || out.res.Reason != "recipient_outstanding" {
		t.Fatalf("expected ReadCommitted blocked notify to capacity-defer (recipient_outstanding), got %+v %v", out.res, out.err)
	}

	var senderPending, recordsAfter, versionsAfter, eventsAfter, mutCount int
	if err = op.pool.QueryRow(ctx, `SELECT
		(SELECT count(*) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.consumer=$2 AND d.state IN ('pending','leased')),
		(SELECT count(*) FROM cairn.memory_record),
		(SELECT count(*) FROM cairn.record_version WHERE repo=$1),
		(SELECT count(*) FROM cairn.agent_event WHERE repo=$1),
		(SELECT count(*) FROM cairn.mutation_request WHERE request_id=$3)`,
		repo, f.sender.channel.Principal, notifyReq.RequestID,
	).Scan(&senderPending, &recordsAfter, &versionsAfter, &eventsAfter, &mutCount); err != nil {
		t.Fatal(err)
	}
	if senderPending != 1 || recordsAfter != recordsBefore || versionsAfter != versionsBefore+0 || eventsAfter != eventsBefore+1 || mutCount != 0 {
		t.Fatalf("blocked notify overshot cap or leaked rows: senderPending=%d records=%d->%d versions=%d->%d events=%d->%d mut=%d",
			senderPending, recordsBefore, recordsAfter, versionsBefore, versionsAfter, eventsBefore, eventsAfter, mutCount)
	}
}
