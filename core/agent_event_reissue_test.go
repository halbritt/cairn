package core

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func reissueFixture(t *testing.T) (*Store, *Store, *Store, Record, Destination) {
	t.Helper()
	repo := "repo-" + strings.ReplaceAll(uuid.NewString(), "-", "")
	a := testStore(t, Channel{Principal: "agent:publisher", Repo: repo})
	b := testStore(t, Channel{Principal: "agent:consumer", Repo: repo})
	op := testStore(t, Channel{Principal: "operator:admin", Operator: true})
	draft := projectNote(repo)
	draft.Sensitivity = "shareable"
	draft.Body = "source version for reissue test"
	r, err := a.Create(context.Background(), CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}
	return a, b, op, r, Destination{Name: "hosted", AllowLocal: false}
}

func failDelivery(t *testing.T, s *Store, deliveryID string) {
	t.Helper()
	_, err := s.pool.Exec(context.Background(), `UPDATE cairn.agent_delivery SET state='failed', completed_at=clock_timestamp(), code='processing_failed', lease_id=NULL, lease_until=NULL WHERE delivery_id=$1`, deliveryID)
	if err != nil {
		t.Fatal(err)
	}
}

func TestReissueEventAttributionAndCausation(t *testing.T) {
	ctx := context.Background()
	a, b, op, r, dest := reissueFixture(t)

	pubEvent := publishFixture(t, a, b, r, dest)
	// The successor must retain the published version after the source changes.
	newDraft := r.Draft
	newDraft.Body = "New source wording must not replace the pinned task"
	if _, err := a.Edit(ctx, EditRequest{RequestID: uuid.NewString(), RecordID: r.RecordID, ExpectedVersion: r.Version, Draft: newDraft}); err != nil {
		t.Fatal(err)
	}
	delivery := nextFixture(t, b, dest)
	failDelivery(t, b, delivery.DeliveryID)

	reqID := uuid.NewString()
	reason := "reissuing failed direct task for consumer retry"
	reissued, err := op.ReissueEvent(ctx, ReissueEventRequest{
		RequestID:              reqID,
		Repo:                   r.Scope.Repo,
		DeliveryID:             delivery.DeliveryID,
		Reason:                 reason,
		AcceptUncertainEffects: true,
	})
	if err != nil {
		t.Fatalf("ReissueEvent: %v", err)
	}

	if reissued.Kind != "request" {
		t.Errorf("kind: want request, got %s", reissued.Kind)
	}
	if reissued.From != "operator:admin" {
		t.Errorf("from: want operator:admin, got %s", reissued.From)
	}
	if reissued.CausationID != pubEvent.EventID {
		t.Errorf("causation: want %s, got %s", pubEvent.EventID, reissued.CausationID)
	}
	if reissued.CorrelationID != reqID {
		t.Errorf("correlation: want %s, got %s", reqID, reissued.CorrelationID)
	}
	if reissued.Ref.RecordID != r.RecordID || reissued.Ref.Version != r.Version {
		t.Errorf("ref: want %v v%d, got %v v%d", r.RecordID, r.Version, reissued.Ref.RecordID, reissued.Ref.Version)
	}

	// Verify trace table.
	var traceDelID, traceEvtID, traceOp, traceReason string
	err = op.pool.QueryRow(ctx, `SELECT delivery_id::text, event_id::text, reissued_by, reason FROM cairn.agent_event_reissue WHERE delivery_id=$1`, delivery.DeliveryID).Scan(
		&traceDelID, &traceEvtID, &traceOp, &traceReason,
	)
	if err != nil {
		t.Fatalf("trace query: %v", err)
	}
	if traceDelID != delivery.DeliveryID || traceEvtID != reissued.EventID || traceOp != "operator:admin" || traceReason != reason {
		t.Fatalf("trace mismatch: del=%s evt=%s op=%s reason=%s", traceDelID, traceEvtID, traceOp, traceReason)
	}

	// Verify new delivery exists for consumer in pending state.
	var newDelID, newConsumer, newState string
	err = b.pool.QueryRow(ctx, `SELECT delivery_id::text, consumer, state FROM cairn.agent_delivery WHERE event_id=$1`, reissued.EventID).Scan(&newDelID, &newConsumer, &newState)
	if err != nil {
		t.Fatalf("new delivery query: %v", err)
	}
	if newConsumer != b.channel.Principal || newState != "pending" {
		t.Fatalf("new delivery: consumer=%s state=%s", newConsumer, newState)
	}
}

func TestReissueEventSourceVersionUnavailable(t *testing.T) {
	ctx := context.Background()
	a, b, op, r, dest := reissueFixture(t)

	publishFixture(t, a, b, r, dest)
	delivery := nextFixture(t, b, dest)
	failDelivery(t, b, delivery.DeliveryID)

	// Mark source record lifecycle as retracted.
	if _, err := a.pool.Exec(ctx, `UPDATE cairn.memory_record SET lifecycle='retracted' WHERE record_id=$1`, r.RecordID); err != nil {
		t.Fatal(err)
	}

	_, err := op.ReissueEvent(ctx, ReissueEventRequest{
		RequestID:              uuid.NewString(),
		Repo:                   r.Scope.Repo,
		DeliveryID:             delivery.DeliveryID,
		Reason:                 "reissuing delivery with deleted source",
		AcceptUncertainEffects: true,
	})
	requireCode(t, err, "NOT_FOUND")
}

func TestReissueEventNoTopicRefanout(t *testing.T) {
	ctx := context.Background()
	repo := "repo-" + strings.ReplaceAll(uuid.NewString(), "-", "")
	a := testStore(t, Channel{Principal: "publisher", Repo: repo})
	b1 := testStore(t, Channel{Principal: "consumer-1", Repo: repo})
	b2 := testStore(t, Channel{Principal: "consumer-2", Repo: repo})
	op := testStore(t, Channel{Principal: "operator", Operator: true})
	dest := Destination{Name: "hosted", AllowLocal: false}

	draft := projectNote(repo)
	draft.Sensitivity = "shareable"
	draft.Body = "topic source version"
	r, err := a.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}

	// Subscribe both consumers to topic.
	if _, err = b1.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: repo, Topic: "work", Active: true}); err != nil {
		t.Fatal(err)
	}
	if _, err = b2.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: repo, Topic: "work", Active: true}); err != nil {
		t.Fatal(err)
	}

	// Publish to topic.
	topicPub, err := a.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Repo:        repo,
		Kind:        "request",
		Ref:         RecordVersionRef{RecordID: r.RecordID, Version: r.Version},
		Destination: EventDestination{Type: "topic", Name: "work"},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}

	// Both consumers have deliveries.
	var b1Delivery, b2Delivery string
	err = b1.pool.QueryRow(ctx, `SELECT delivery_id::text FROM cairn.agent_delivery WHERE event_id=$1 AND consumer='consumer-1'`, topicPub.EventID).Scan(&b1Delivery)
	if err != nil {
		t.Fatal(err)
	}
	err = b2.pool.QueryRow(ctx, `SELECT delivery_id::text FROM cairn.agent_delivery WHERE event_id=$1 AND consumer='consumer-2'`, topicPub.EventID).Scan(&b2Delivery)
	if err != nil {
		t.Fatal(err)
	}

	// Fail consumer-1 delivery.
	failDelivery(t, b1, b1Delivery)

	// Reissue consumer-1 delivery.
	reissued, err := op.ReissueEvent(ctx, ReissueEventRequest{
		RequestID:              uuid.NewString(),
		Repo:                   repo,
		DeliveryID:             b1Delivery,
		Reason:                 "reissue failed topic delivery for consumer-1 only",
		AcceptUncertainEffects: true,
	})
	if err != nil {
		t.Fatalf("ReissueEvent: %v", err)
	}
	if reissued.Destination.Type != "agent" || reissued.Destination.Name != "consumer-1" {
		t.Fatalf("destination: want agent/consumer-1, got %+v", reissued.Destination)
	}

	// Assert only ONE delivery was created, strictly for consumer-1.
	rows, err := op.pool.Query(ctx, `SELECT consumer FROM cairn.agent_delivery WHERE event_id=$1`, reissued.EventID)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var consumers []string
	for rows.Next() {
		var c string
		if err = rows.Scan(&c); err != nil {
			t.Fatal(err)
		}
		consumers = append(consumers, c)
	}
	if err = rows.Err(); err != nil {
		t.Fatal(err)
	}
	if len(consumers) != 1 || consumers[0] != "consumer-1" {
		t.Fatalf("expected single delivery for consumer-1, got: %v", consumers)
	}
}

func TestReissueEventPoolSelectors(t *testing.T) {
	ctx := context.Background()
	_, a, b, r, dest, worker := poolFixture(t)
	op := testStore(t, Channel{Principal: "operator", Operator: true})

	pooled := poolPublish(t, a, r, dest, PoolRequirements{
		Workspace: worker.Spec.Workspace,
		Harness:   worker.Spec.Harness,
		Model:     worker.Spec.Model,
	})

	claim := poolClaim(t, b, worker, dest)
	if claim == nil || claim.Delivery.Event.EventID != pooled.EventID {
		t.Fatalf("pool claim: %+v", claim)
	}

	// Fail the pool delivery and finish its wake attempt.
	failDelivery(t, b, claim.Delivery.DeliveryID)
	if _, err := b.pool.Exec(ctx, `UPDATE cairn.agent_wake_attempt SET state='finished', finished_at=clock_timestamp() WHERE delivery_id=$1`, claim.Delivery.DeliveryID); err != nil {
		t.Fatal(err)
	}

	reissued, err := op.ReissueEvent(ctx, ReissueEventRequest{
		RequestID:              uuid.NewString(),
		Repo:                   r.Scope.Repo,
		DeliveryID:             claim.Delivery.DeliveryID,
		Reason:                 "reissuing pool request after execution failure",
		AcceptUncertainEffects: true,
	})
	if err != nil {
		t.Fatalf("ReissueEvent: %v", err)
	}

	if reissued.Destination.Type != "pool" || reissued.Destination.Name != worker.Spec.Pools[0] {
		t.Fatalf("destination: %+v", reissued.Destination)
	}
	if reissued.Pool == nil || reissued.Pool.Workspace != worker.Spec.Workspace {
		t.Fatalf("pool requirements not preserved: %+v", reissued.Pool)
	}

	// Verify agent_pool_request has the new event.
	var poolReqID string
	err = op.pool.QueryRow(ctx, `SELECT event_id::text FROM cairn.agent_pool_request WHERE event_id=$1`, reissued.EventID).Scan(&poolReqID)
	if err != nil {
		t.Fatalf("pool request missing: %v", err)
	}

	// Another pool claim must find this reissued event.
	allowFixtureLaunch(t, b)
	newClaim := poolClaim(t, b, worker, dest)
	if newClaim == nil || newClaim.Delivery.Event.EventID != reissued.EventID {
		t.Fatalf("second pool claim: %+v", newClaim)
	}
}

func TestReissueEventNoReplayOfActiveHolds(t *testing.T) {
	ctx := context.Background()
	a, b, op, r, dest := reissueFixture(t)

	pubEvent := publishFixture(t, a, b, r, dest)
	var deliveryID string
	err := b.pool.QueryRow(ctx, `SELECT delivery_id::text FROM cairn.agent_delivery WHERE event_id=$1`, pubEvent.EventID).Scan(&deliveryID)
	if err != nil {
		t.Fatal(err)
	}

	baseReq := ReissueEventRequest{
		RequestID:              uuid.NewString(),
		Repo:                   r.Scope.Repo,
		DeliveryID:             deliveryID,
		Reason:                 "testing hold prevention on delivery",
		AcceptUncertainEffects: true,
	}

	// 1. Pending delivery must be refused.
	_, err = op.ReissueEvent(ctx, baseReq)
	requireCode(t, err, "INVALID_REQUEST")

	// 2. Leased delivery must be refused.
	lease := uuid.NewString()
	_, err = b.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET state='leased', lease_id=$1, lease_until=clock_timestamp()+interval '60s' WHERE delivery_id=$2`, lease, deliveryID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.ReissueEvent(ctx, baseReq)
	requireCode(t, err, "INVALID_REQUEST")

	// 3. Handled delivery must be refused.
	_, err = b.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET state='handled', completed_at=clock_timestamp(), lease_id=NULL, lease_until=NULL WHERE delivery_id=$1`, deliveryID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.ReissueEvent(ctx, baseReq)
	requireCode(t, err, "INVALID_REQUEST")

	// 4. Failed delivery with active wake attempt must be refused.
	failDelivery(t, b, deliveryID)
	wakeAttemptID := uuid.NewString()
	_, err = b.pool.Exec(ctx, `INSERT INTO cairn.agent_wake_attempt(attempt_id,repo,consumer,delivery_id,lease_id) VALUES($1,$2,$3,$4,$5)`,
		wakeAttemptID, r.Scope.Repo, b.channel.Principal, deliveryID, uuid.NewString())
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.ReissueEvent(ctx, baseReq)
	requireCode(t, err, "VERSION_CONFLICT")

	// Finish the wake attempt.
	_, err = b.pool.Exec(ctx, `UPDATE cairn.agent_wake_attempt SET state='finished', finished_at=clock_timestamp() WHERE attempt_id=$1`, wakeAttemptID)
	if err != nil {
		t.Fatal(err)
	}

	// 5. Failed delivery with active session attempt must be refused.
	agentID := uuid.NewString()
	_, err = b.pool.Exec(ctx, `INSERT INTO cairn.agent_session(agent_id,repo,owner,binding,native_session_id,visibility,execution_id,database_generation,metadata) VALUES($1,$2,$3,'test-binding',$4,'hosted',$5,1,'{}'::jsonb)`,
		agentID, r.Scope.Repo, b.channel.Principal, uuid.NewString(), uuid.NewString())
	if err != nil {
		t.Fatal(err)
	}
	sessionAttemptID := uuid.NewString()
	_, err = b.pool.Exec(ctx, `INSERT INTO cairn.agent_session_attempt(attempt_id,agent_id,execution_id,owner,delivery_id,lease_id) VALUES($1,$2,$3,$4,$5,$6)`,
		sessionAttemptID, agentID, uuid.NewString(), b.channel.Principal, deliveryID, uuid.NewString())
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.ReissueEvent(ctx, baseReq)
	requireCode(t, err, "VERSION_CONFLICT")

	// Finish the session attempt.
	_, err = b.pool.Exec(ctx, `UPDATE cairn.agent_session_attempt SET finished_at=clock_timestamp() WHERE attempt_id=$1`, sessionAttemptID)
	if err != nil {
		t.Fatal(err)
	}

	// 6. AcceptUncertainEffects=false must be refused.
	noAccept := baseReq
	noAccept.AcceptUncertainEffects = false
	_, err = op.ReissueEvent(ctx, noAccept)
	requireCode(t, err, "INVALID_REQUEST")

	// 7. Non-request event kind must be refused.
	_, err = b.pool.Exec(ctx, `UPDATE cairn.agent_event SET kind='notice' WHERE event_id=$1`, pubEvent.EventID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.ReissueEvent(ctx, baseReq)
	requireCode(t, err, "INVALID_REQUEST")
}

func TestReissueEventAtomicRollback(t *testing.T) {
	ctx := context.Background()
	a, b, op, r, dest := reissueFixture(t)

	publishFixture(t, a, b, r, dest)
	delivery := nextFixture(t, b, dest)
	failDelivery(t, b, delivery.DeliveryID)

	// Inject a trigger failure on cairn.agent_event_reissue insertion.
	trigger := "reissue_probe_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	sql := `CREATE FUNCTION cairn.` + trigger + `() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected reissue failure'; END $$; CREATE TRIGGER ` + trigger + ` BEFORE INSERT ON cairn.agent_event_reissue FOR EACH ROW EXECUTE FUNCTION cairn.` + trigger + `()`
	if _, err := op.pool.Exec(ctx, sql); err != nil {
		t.Fatal(err)
	}
	defer op.pool.Exec(ctx, `DROP TRIGGER IF EXISTS `+trigger+` ON cairn.agent_event_reissue; DROP FUNCTION IF EXISTS cairn.`+trigger+`()`)

	req := ReissueEventRequest{
		RequestID:              uuid.NewString(),
		Repo:                   r.Scope.Repo,
		DeliveryID:             delivery.DeliveryID,
		Reason:                 "testing atomic rollback upon trigger error",
		AcceptUncertainEffects: true,
	}
	if _, err := op.ReissueEvent(ctx, req); err == nil {
		t.Fatal("expected injected failure, got success")
	}

	// Verify no new event was created with this request ID as correlation.
	var eventCount int
	err := op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event WHERE correlation_id=$1::uuid`, req.RequestID).Scan(&eventCount)
	if err != nil || eventCount != 0 {
		t.Fatalf("rollback failed, found %d events", eventCount)
	}

	// Verify trace table has 0 rows for this delivery.
	var traceCount int
	err = op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event_reissue WHERE delivery_id=$1`, delivery.DeliveryID).Scan(&traceCount)
	if err != nil || traceCount != 0 {
		t.Fatalf("rollback failed, found %d trace entries", traceCount)
	}
}

func TestReissueEventExactRetry(t *testing.T) {
	ctx := context.Background()
	a, b, op, r, dest := reissueFixture(t)

	publishFixture(t, a, b, r, dest)
	delivery := nextFixture(t, b, dest)
	failDelivery(t, b, delivery.DeliveryID)

	req := ReissueEventRequest{
		RequestID:              uuid.NewString(),
		Repo:                   r.Scope.Repo,
		DeliveryID:             delivery.DeliveryID,
		Reason:                 "testing exact retry idempotency",
		AcceptUncertainEffects: true,
	}
	first, err := op.ReissueEvent(ctx, req)
	if err != nil {
		t.Fatalf("first call: %v", err)
	}
	second, err := op.ReissueEvent(ctx, req)
	if err != nil {
		t.Fatalf("second call: %v", err)
	}
	if first.EventID != second.EventID {
		t.Fatalf("event ID mismatch on retry: %s vs %s", first.EventID, second.EventID)
	}
}

func TestReissueEventConflictingRequestUUIDAndAlreadyReissued(t *testing.T) {
	ctx := context.Background()
	a, b, op, r, dest := reissueFixture(t)

	publishFixture(t, a, b, r, dest)
	delivery := nextFixture(t, b, dest)
	failDelivery(t, b, delivery.DeliveryID)

	sameReqID := uuid.NewString()
	req := ReissueEventRequest{
		RequestID:              sameReqID,
		Repo:                   r.Scope.Repo,
		DeliveryID:             delivery.DeliveryID,
		Reason:                 "initial reissue reason for conflict test",
		AcceptUncertainEffects: true,
	}
	_, err := op.ReissueEvent(ctx, req)
	if err != nil {
		t.Fatalf("initial reissue: %v", err)
	}

	// 1. Same request ID with different payload -> IDEMPOTENCY_CONFLICT.
	conflictReq := req
	conflictReq.Reason = "different reason with same request UUID"
	_, err = op.ReissueEvent(ctx, conflictReq)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")

	// 2. New request ID attempting to reissue already reissued delivery -> VERSION_CONFLICT.
	newReq := ReissueEventRequest{
		RequestID:              uuid.NewString(),
		Repo:                   r.Scope.Repo,
		DeliveryID:             delivery.DeliveryID,
		Reason:                 "attempting second reissue on same delivery",
		AcceptUncertainEffects: true,
	}
	_, err = op.ReissueEvent(ctx, newReq)
	requireCode(t, err, "VERSION_CONFLICT")
}

func TestReissueEventOrdinaryProfileRefusal(t *testing.T) {
	ctx := context.Background()
	repo := "repo-" + strings.ReplaceAll(uuid.NewString(), "-", "")
	ordinary := testStore(t, Channel{Principal: "agent:worker", Repo: repo, Operator: false})
	scopedOp := testStore(t, Channel{Principal: "operator:scoped", Repo: repo, Operator: true})

	req := ReissueEventRequest{
		RequestID:              uuid.NewString(),
		Repo:                   repo,
		DeliveryID:             uuid.NewString(),
		Reason:                 "testing operator authority gate",
		AcceptUncertainEffects: true,
	}

	// Non-operator store.
	_, err := ordinary.ReissueEvent(ctx, req)
	requireCode(t, err, "AUTHORITY_DENIED")

	// Scoped operator store.
	_, err = scopedOp.ReissueEvent(ctx, req)
	requireCode(t, err, "AUTHORITY_DENIED")
}
