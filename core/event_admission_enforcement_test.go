package core

import (
	"context"
	"errors"
	"reflect"
	"sync"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// This composes the real SQL evaluator and existing writer under one real
// transaction while the public enforcement capability remains unavailable.
func testAdmittedPublish(ctx context.Context, s *Store, req PublishEventRequest, policy EventAdmissionPolicyState, dest Destination) (AgentEvent, error) {
	req, err := s.normalizePublication(req, dest)
	if err != nil {
		return AgentEvent{}, err
	}
	out, err := mutate(ctx, s, "event-publish", req.RequestID, req, func(tx pgx.Tx) (AgentEvent, error) {
		if err := lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
			return AgentEvent{}, err
		}
		if _, err := s.eventSource(ctx, tx, req, dest); err != nil {
			return AgentEvent{}, err
		}
		closure, err := matchReplyClosureTx(ctx, tx, req, s.channel.Principal)
		if err != nil {
			return AgentEvent{}, err
		}
		if _, err = s.checkEventAdmissionTx(ctx, tx, req, policy, closure); err != nil {
			return AgentEvent{}, err
		}
		return s.publishEventTx(ctx, tx, req, dest, "")
	})
	if err != nil {
		err = s.retainEventAdmissionRefusal(ctx, req.Repo, err)
	}
	return out, err
}
func testAdmissionState(t *testing.T, s *Store, repo string) EventAdmissionPolicyState {
	t.Helper()
	op := testStore(t, Channel{Principal: "operator:admission-helper:" + repo, Operator: true})
	policy, err := op.SetEventAdmissionPolicy(context.Background(), SetEventAdmissionPolicyRequest{RequestID: uuid.NewString(), Repo: repo, Policy: admissionFixturePolicy()})
	if err != nil {
		t.Fatal(err)
	}
	return policy
}
func requireAdmissionDimension(t *testing.T, err error, dimension string) {
	t.Helper()
	var refusal *Error
	if !errors.As(err, &refusal) || refusal.Code != "EVENT_ADMISSION_REFUSED" || refusal.Admission == nil || refusal.Admission.Dimension != dimension || !refusal.Admission.Retryable {
		t.Fatalf("want admission dimension %s; got %v", dimension, err)
	}
}
func TestEventAdmissionAtomicSaturatedMemberAndRetry(t *testing.T) {
	ctx := context.Background()
	a, b, source, dest := eventFixture(t)
	repo := source.Scope.Repo
	state := testAdmissionState(t, a, repo)
	c := testStore(t, Channel{Principal: "other-recipient:" + repo, Repo: repo})
	for _, recipient := range []*Store{b, c} {
		if _, err := recipient.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: "bounded", Active: true}); err != nil {
			t.Fatal(err)
		}
	}
	req := PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "agent", Name: b.channel.Principal}}
	for i := 0; i < 2; i++ {
		req.RequestID = uuid.NewString()
		if _, err := testAdmittedPublish(ctx, a, req, state, dest); err != nil {
			t.Fatal(err)
		}
	}
	topic := req
	topic.RequestID = uuid.NewString()
	topic.Destination = EventDestination{Type: "topic", Name: "bounded"}
	_, err := testAdmittedPublish(ctx, a, topic, state, dest)
	requireAdmissionDimension(t, err, "recipient_outstanding")
	own, err := c.EventPressure(ctx, EventPressureQuery{}, dest)
	if err != nil || own.Reservoirs.PendingDeliveries != 0 {
		t.Fatalf("partial topic delivery: %+v %v", own, err)
	}
	var receiptCount int
	if err = a.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.mutation_request WHERE caller=$1 AND operation='event-publish' AND request_id=$2`, a.channel.Principal, topic.RequestID).Scan(&receiptCount); err != nil || receiptCount != 0 {
		t.Fatalf("refusal retained receipt: %d %v", receiptCount, err)
	}
	// Drain one member, then retry exactly the refused UUID and intent.
	delivery := nextFixture(t, b, dest)
	if _, err = b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery.DeliveryID, LeaseID: delivery.LeaseID, Disposition: "handled"}, dest); err != nil {
		t.Fatal(err)
	}
	accepted, err := testAdmittedPublish(ctx, a, topic, state, dest)
	if err != nil {
		t.Fatal(err)
	}
	repeated, err := testAdmittedPublish(ctx, a, topic, state, dest)
	if err != nil || accepted.EventID != repeated.EventID {
		t.Fatalf("committed retry charged again: %+v %+v %v", accepted, repeated, err)
	}
	status, err := a.AgentEventStatus(ctx, EventStatusRequest{EventID: accepted.EventID}, dest)
	if err != nil || len(status.Deliveries) != 2 {
		t.Fatalf("atomic recipients: %+v %v", status, err)
	}
	// The refusal survives rollback independently of event and UUID receipt.
	op := testStore(t, Channel{Principal: "operator:refusals:" + repo, Operator: true})
	refusals, err := op.EventAdmissionRefusals(ctx, EventAdmissionPolicyQuery{Repo: repo})
	if err != nil || len(refusals) != 1 || refusals[0].Dimension != "recipient_outstanding" || refusals[0].RefusalCount != 1 {
		t.Fatalf("durable refusal diagnostic: %+v %v", refusals, err)
	}
	_, err = b.EventAdmissionRefusals(ctx, EventAdmissionPolicyQuery{})
	requireCode(t, err, "AUTHORITY_DENIED")
}
func TestEventAdmissionConcurrentPublishersCannotOverspend(t *testing.T) {
	ctx := context.Background()
	a, b, source, dest := eventFixture(t)
	state := testAdmissionState(t, a, source.Scope.Repo)
	state.Policy.Limits.MaxCollectionOutstanding = 1
	state.Policy.Limits.MaxRecipientOutstanding = 1
	state.Policy.Limits.MaxProducerRecipientOutstanding = 1
	c := testStore(t, Channel{Principal: "publisher-two:" + source.Scope.Repo, Repo: source.Scope.Repo})
	var wg sync.WaitGroup
	errs := make(chan error, 2)
	for _, publisher := range []*Store{a, c} {
		wg.Add(1)
		go func(p *Store) {
			defer wg.Done()
			_, err := testAdmittedPublish(ctx, p, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "agent", Name: b.channel.Principal}}, state, dest)
			errs <- err
		}(publisher)
	}
	wg.Wait()
	close(errs)
	accepted, refused := 0, 0
	for err := range errs {
		if err == nil {
			accepted++
		} else {
			requireAdmissionDimension(t, err, "collection_outstanding")
			refused++
		}
	}
	if accepted != 1 || refused != 1 {
		t.Fatalf("overspent: accepted %d refused %d", accepted, refused)
	}
	own, err := b.EventPressure(ctx, EventPressureQuery{}, dest)
	if err != nil || own.Reservoirs.PendingDeliveries != 1 {
		t.Fatalf("queue bound: %+v %v", own, err)
	}
}
func TestEventAdmissionFanoutPairAndEmptyTopic(t *testing.T) {
	ctx := context.Background()
	a, b, source, dest := eventFixture(t)
	state := testAdmissionState(t, a, source.Scope.Repo)
	req := PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "agent", Name: b.channel.Principal}}
	state.Policy.Limits.MaxRecipientOutstanding = 4
	state.Policy.Limits.MaxProducerRecipientOutstanding = 1
	if _, err := testAdmittedPublish(ctx, a, req, state, dest); err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	_, err := testAdmittedPublish(ctx, a, req, state, dest)
	requireAdmissionDimension(t, err, "producer_recipient_outstanding")
	// Lowered limits do not change existing work. Empty topic delivery cost is
	// zero even while retained queues are already above the lower bound.
	state.Policy.Limits.MaxCollectionOutstanding = 1
	publishFixture(t, a, b, source, dest)
	empty := req
	empty.RequestID = uuid.NewString()
	empty.Destination = EventDestination{Type: "topic", Name: "empty"}
	if _, err = testAdmittedPublish(ctx, a, empty, state, dest); err != nil {
		t.Fatalf("empty topic charged nonexistent deliveries: %v", err)
	}
	for i := 0; i < 3; i++ {
		recipient := testStore(t, Channel{Principal: "subscriber:" + uuid.NewString(), Repo: source.Scope.Repo})
		if _, err = recipient.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: "three", Active: true}); err != nil {
			t.Fatal(err)
		}
	}
	three := empty
	three.RequestID = uuid.NewString()
	three.Destination.Name = "three"
	_, err = testAdmittedPublish(ctx, a, three, state, dest)
	requireAdmissionDimension(t, err, "topic_recipients")
	// Finite diagnostic rows depend on the fixed dimension vocabulary, not
	// refusal attempts or caller-selected request/correlation UUIDs.
	for i := 0; i < 5; i++ {
		three.RequestID = uuid.NewString()
		three.CorrelationID = uuid.NewString()
		_, err = testAdmittedPublish(ctx, a, three, state, dest)
		requireAdmissionDimension(t, err, "topic_recipients")
	}
	op := testStore(t, Channel{Principal: "operator:bounded-refusals", Operator: true})
	counts, err := op.EventAdmissionRefusals(ctx, EventAdmissionPolicyQuery{Repo: source.Scope.Repo})
	if err != nil || len(counts) != 2 {
		t.Fatalf("unbounded dimensions: %+v %v", counts, err)
	}
	dimensions := []string{}
	for _, count := range counts {
		dimensions = append(dimensions, count.Dimension)
		if count.Dimension == "topic_recipients" && count.RefusalCount != 6 {
			t.Fatalf("lost refusal telemetry: %+v", count)
		}
	}
	if !reflect.DeepEqual(dimensions, []string{"producer_recipient_outstanding", "topic_recipients"}) {
		t.Fatalf("unexpected dimensions: %+v", dimensions)
	}
}

func TestEventAdmissionValidatedClosureUsesProtectedCapacity(t *testing.T) {
	ctx := context.Background()
	a, b, source, dest := eventFixture(t)
	state := testAdmissionState(t, a, source.Scope.Repo)
	state.Policy.Limits.MaxCollectionOutstanding = 1
	req := PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "agent", Name: b.channel.Principal}}
	request, err := testAdmittedPublish(ctx, a, req, state, dest)
	if err != nil {
		t.Fatal(err)
	}
	delivery := nextFixture(t, b, dest)
	if _, err = b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery.DeliveryID, LeaseID: delivery.LeaseID, Disposition: "handled"}, dest); err != nil {
		t.Fatal(err)
	}
	reply := PublishEventRequest{RequestID: uuid.NewString(), Kind: "response", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "agent", Name: a.channel.Principal}, CausationID: request.EventID}
	if _, err = testAdmittedPublish(ctx, b, reply, state, dest); err != nil {
		t.Fatalf("validated reply blocked: %v", err)
	}
	req.Kind = "notice"
	req.RequestID = uuid.NewString()
	if _, err = testAdmittedPublish(ctx, a, req, state, dest); err != nil {
		t.Fatalf("protected reply double charged as ordinary delivery: %v", err)
	}
	// A kind label without a store-validated retained obligation earns no reserve.
	reply.RequestID = uuid.NewString()
	reply.CausationID = ""
	_, err = testAdmittedPublish(ctx, b, reply, state, dest)
	requireAdmissionDimension(t, err, "collection_outstanding")
}

func TestEventAdmissionSubscriptionRaceKeepsAtomicSnapshot(t *testing.T) {
	ctx := context.Background()
	a, b, source, dest := eventFixture(t)
	state := testAdmissionState(t, a, source.Scope.Repo)
	state.Policy.Limits.MaxTopicRecipients = 1
	state.Policy.Limits.MaxCollectionOutstanding = 100
	state.Policy.Limits.MaxRecipientOutstanding = 100
	state.Policy.Limits.MaxProducerRecipientOutstanding = 100
	if _, err := b.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: "race", Active: true}); err != nil {
		t.Fatal(err)
	}
	c := testStore(t, Channel{Principal: "changing-subscriber:" + source.Scope.Repo, Repo: source.Scope.Repo})
	for i := 0; i < 8; i++ {
		start := make(chan struct{})
		changes := make(chan error, 1)
		go func(active bool) {
			<-start
			_, err := c.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: "race", Active: active})
			changes <- err
		}(i%2 == 0)
		close(start)
		event, err := testAdmittedPublish(ctx, a, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "topic", Name: "race"}}, state, dest)
		if err != nil {
			requireAdmissionDimension(t, err, "topic_recipients")
		} else {
			status, err := a.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
			if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].Consumer != b.channel.Principal {
				t.Fatalf("recipient set split during membership change: %+v %v", status, err)
			}
		}
		if err = <-changes; err != nil {
			t.Fatal(err)
		}
	}
}
