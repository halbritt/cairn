package core

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/google/uuid"
)

type admissionBucketRow struct {
	kind, identity, unit string
	tokens               int64
	refillAt             time.Time
	revision, generation int64
}

func admissionBuckets(t *testing.T, s *Store, repo string) []admissionBucketRow {
	t.Helper()
	rows, err := s.pool.Query(context.Background(), `SELECT identity_kind, identity, unit, tokens_nano, refill_at, policy_revision, restore_generation FROM cairn.agent_event_rate_bucket WHERE repo=$1 ORDER BY identity_kind, identity, unit`, repo)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var out []admissionBucketRow
	for rows.Next() {
		var b admissionBucketRow
		if err = rows.Scan(&b.kind, &b.identity, &b.unit, &b.tokens, &b.refillAt, &b.revision, &b.generation); err != nil {
			t.Fatal(err)
		}
		out = append(out, b)
	}
	if err = rows.Err(); err != nil {
		t.Fatal(err)
	}
	return out
}

func sameAdmissionBuckets(a, b []admissionBucketRow) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i].kind != b[i].kind || a[i].identity != b[i].identity || a[i].unit != b[i].unit || a[i].tokens != b[i].tokens || !a[i].refillAt.Equal(b[i].refillAt) || a[i].revision != b[i].revision || a[i].generation != b[i].generation {
			return false
		}
	}
	return true
}

func collectionPublicationBucket(t *testing.T, rows []admissionBucketRow) admissionBucketRow {
	t.Helper()
	for _, b := range rows {
		if b.kind == "collection" && b.unit == "publication" {
			return b
		}
	}
	t.Fatalf("collection publication bucket missing: %+v", rows)
	return admissionBucketRow{}
}

func requireRecipientOutstanding(t *testing.T, err error) {
	t.Helper()
	var refusal *Error
	if !errors.As(err, &refusal) || refusal.Code != "EVENT_ADMISSION_REFUSED" || refusal.Admission == nil || refusal.Admission.Dimension != "recipient_outstanding" {
		t.Fatalf("want recipient_outstanding refusal; got %v", err)
	}
}

// Disabling an enforced policy stops refusing new work but neither releases
// accepted obligations nor spends or resets rate budgets. Re-enabling applies
// the caps again over the backlog and resumes rate accounting from the
// re-enable time, without crediting the disabled interval or refilling the
// spent burst.
func TestEventAdmissionDisableAndReenableWithOutstandingWork(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	openStore := func(ch Channel) *Store { return testStore(t, ch) }
	_, _, _, worker, coordinator, executor, dest := waitFixture(t, openStore)
	repo := worker.Repo
	op := openStore(Channel{Principal: "operator:disable:" + repo, Operator: true})
	if _, err := op.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatal(err)
	}
	draft := projectNote(repo)
	draft.Sensitivity = "shareable"
	source, err := coordinator.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}
	ref := RecordVersionRef{RecordID: source.RecordID, Version: source.Version}
	inbox := "agent/" + worker.AgentID
	request := func() PublishEventRequest {
		return PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: ref, Destination: EventDestination{Type: "agent", Name: inbox}, CorrelationID: uuid.NewString()}
	}

	generous := EventAdmissionRate{PerSecond: 1000000, Burst: 1000000}
	limits := EventAdmissionLimits{
		MaxTopicRecipients: 10, MaxRecipientOutstanding: 1, MaxProducerRecipientOutstanding: 100, MaxCollectionOutstanding: 100,
		MaxClosureReserved: 100, MaxRecipientClosureReserved: 100,
		ProducerPublications: generous, ProducerDeliveries: generous, OwnerPublications: generous, OwnerDeliveries: generous,
		// One token per second with a large burst: spending is visible and no
		// step can be refused by rate within the test's runtime.
		CollectionPublications: EventAdmissionRate{PerSecond: 1, Burst: 1000}, CollectionDeliveries: generous,
	}
	configured := EventAdmissionPolicyState{}
	setMode := func(mode string) {
		t.Helper()
		l := limits
		next, err := op.SetEventAdmissionPolicy(ctx, SetEventAdmissionPolicyRequest{RequestID: uuid.NewString(), Repo: repo, ExpectedRevision: configured.Revision,
			Policy: EventAdmissionPolicy{Version: 1, Mode: mode, Workload: "disable/re-enable fixture: one worker inbox capped at one, one wait", Limits: &l}})
		if err != nil {
			t.Fatalf("set %s: %v", mode, err)
		}
		if next.Revision != configured.Revision+1 || next.Policy.Mode != mode || next.UpdatedAt == nil {
			t.Fatalf("policy %s state: %+v", mode, next)
		}
		configured = next
	}
	if out, err := op.ReconcileClosureReservations(ctx, ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: repo}); err != nil || !out.Complete {
		t.Fatalf("reconcile: %+v %v", out, err)
	}
	setMode("enforce")

	// Accepted under enforcement: one request at the worker's cap and a wait.
	first, err := coordinator.PublishEvent(ctx, request(), dest)
	if err != nil {
		t.Fatal(err)
	}
	wait, err := coordinator.CreateWait(ctx, CreateWaitRequest{RequestID: uuid.NewString(), Collection: repo, PredicateKind: WaitPredicateRequestResponse, DependencyEventUUID: first.EventID, Deadline: time.Now().Add(time.Hour)}, dest)
	if err != nil || wait.State != WaitStateWaiting {
		t.Fatalf("wait: %+v %v", wait, err)
	}
	overCap := request()
	_, err = coordinator.PublishEvent(ctx, overCap, dest)
	requireRecipientOutstanding(t, err)
	if rows := closureRows(t, op, first.EventID); len(rows) != 1 || rows[0].state != "reserved" {
		t.Fatalf("accepted request closure: %+v", rows)
	}
	enforcedBuckets := admissionBuckets(t, op, repo)
	if len(enforcedBuckets) == 0 {
		t.Fatal("enforced admission charged no rate bucket")
	}

	// Disabling retains every obligation and every bucket unchanged.
	setMode("disabled")
	if !sameAdmissionBuckets(enforcedBuckets, admissionBuckets(t, op, repo)) {
		t.Fatal("disabling changed rate buckets")
	}
	var waitReservation string
	if err = op.pool.QueryRow(ctx, `SELECT state FROM cairn.agent_closure_reservation WHERE wait_uuid=$1`, wait.WaitUUID).Scan(&waitReservation); err != nil || waitReservation != "reserved" {
		t.Fatalf("wait closure after disable: %q %v", waitReservation, err)
	}

	// While disabled, the same refused intent is admitted over the old cap,
	// and no rate bucket is created, debited or refilled.
	second, err := coordinator.PublishEvent(ctx, overCap, dest)
	if err != nil {
		t.Fatalf("refused intent not admitted while disabled: %v", err)
	}
	if rows := closureRows(t, op, second.EventID); len(rows) != 1 || rows[0].state != "reserved" {
		t.Fatalf("disabled-mode request closure: %+v", rows)
	}
	if !sameAdmissionBuckets(enforcedBuckets, admissionBuckets(t, op, repo)) {
		t.Fatal("disabled admission debited or refilled a rate bucket")
	}

	// Maintenance of accepted work continues: claim and settle the first
	// request; settlement alone completes nothing.
	claimed, err := executor.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil || claimed.Delivery == nil || claimed.Delivery.Event.EventID != first.EventID {
		t.Fatalf("claim first request: %+v %v", claimed, err)
	}
	if _, err = executor.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: claimed.Delivery.DeliveryID, LeaseID: claimed.Delivery.LeaseID, Disposition: "handled"}, dest); err != nil {
		t.Fatal(err)
	}
	if rows := closureRows(t, op, first.EventID); len(rows) != 1 || rows[0].state != "reserved" {
		t.Fatalf("settlement released the reply obligation: %+v", rows)
	}
	if got, err := coordinator.GetWait(ctx, GetWaitRequest{WaitUUID: wait.WaitUUID}, dest); err != nil || got.State != WaitStateWaiting {
		t.Fatalf("wait completed without a reply: %+v %v", got, err)
	}
	if drained, err := op.DrainWaitOutbox(ctx, repo, 10); err != nil || drained != 0 {
		t.Fatalf("notice published before the wait resolved: %d %v", drained, err)
	}

	// The exact reply consumes its reservation and resolves the wait; the
	// terminal notice drains once.
	reply, err := executor.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "response", Ref: ref, Destination: EventDestination{Type: "agent", Name: first.From}, CausationID: first.EventID, CorrelationID: first.CorrelationID}, dest)
	if err != nil {
		t.Fatal(err)
	}
	if rows := closureRows(t, op, first.EventID); len(rows) != 1 || rows[0].state != "delivered" {
		t.Fatalf("reply did not consume its closure: %+v", rows)
	}
	got, err := coordinator.GetWait(ctx, GetWaitRequest{WaitUUID: wait.WaitUUID}, dest)
	if err != nil || got.State != WaitStateSatisfied || got.ResponseRef == nil || *got.ResponseRef != reply.Ref {
		t.Fatalf("wait not satisfied by the exact reply: %+v %v", got, err)
	}
	for want := int64(1); want >= 0; want-- {
		if drained, err := op.DrainWaitOutbox(ctx, repo, 10); err != nil || drained != want {
			t.Fatalf("drain want %d: %d %v", want, drained, err)
		}
	}
	if err = op.pool.QueryRow(ctx, `SELECT state FROM cairn.agent_closure_reservation WHERE wait_uuid=$1`, wait.WaitUUID).Scan(&waitReservation); err != nil || waitReservation != "delivered" {
		t.Fatalf("wait closure after drain: %q %v", waitReservation, err)
	}
	// The request admitted while disabled is still unanswered and pending.
	if rows := closureRows(t, op, second.EventID); len(rows) != 1 || rows[0].state != "reserved" {
		t.Fatalf("unanswered request closure changed: %+v", rows)
	}
	var secondState string
	if err = op.pool.QueryRow(ctx, `SELECT state FROM cairn.agent_delivery WHERE event_id=$1`, second.EventID).Scan(&secondState); err != nil || secondState != "pending" {
		t.Fatalf("disabled-mode request delivery: %q %v", secondState, err)
	}
	if !sameAdmissionBuckets(enforcedBuckets, admissionBuckets(t, op, repo)) {
		t.Fatal("disabled maintenance changed rate buckets")
	}

	// Re-enabling resets nothing and applies the cap again over the backlog.
	setMode("enforce")
	if !sameAdmissionBuckets(enforcedBuckets, admissionBuckets(t, op, repo)) {
		t.Fatal("re-enabling reset rate buckets")
	}
	_, err = coordinator.PublishEvent(ctx, request(), dest)
	requireRecipientOutstanding(t, err)

	// One charged publication after re-enable refills only from the policy
	// update, never across the disabled interval, and never to a full burst.
	before := collectionPublicationBucket(t, enforcedBuckets)
	if _, err = coordinator.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: ref, Destination: EventDestination{Type: "topic", Name: "disable-empty-root"}}, dest); err != nil {
		t.Fatal(err)
	}
	after := collectionPublicationBucket(t, admissionBuckets(t, op, repo))
	start := before.refillAt
	if configured.UpdatedAt.After(start) {
		start = *configured.UpdatedAt
	}
	credited := after.refillAt.Sub(start).Nanoseconds() * limits.CollectionPublications.PerSecond
	capacity := limits.CollectionPublications.Burst * eventTokenScale
	want := min(capacity, before.tokens+credited) - eventTokenScale
	if credited < 0 || after.tokens != want || after.revision != configured.Revision {
		t.Fatalf("re-enabled bucket: before=%+v after=%+v start=%s want_tokens=%d", before, after, start, want)
	}
	if !before.refillAt.Before(*configured.UpdatedAt) {
		t.Fatalf("fixture did not separate the disabled interval: refill=%s update=%s", before.refillAt, configured.UpdatedAt)
	}
	if after.tokens >= capacity-eventTokenScale {
		t.Fatalf("re-enable refilled the spent burst: %+v", after)
	}
}
