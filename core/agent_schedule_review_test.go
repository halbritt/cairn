package core

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
)

// TestScheduleSimultaneousTicksProduceOneEvent asserts that concurrent TickSchedules
// calls for the same due schedule safely serialize under the collection advisory lock,
// emitting exactly one event and exactly one delivery.
func TestScheduleSimultaneousTicksProduceOneEvent(t *testing.T) {
	ctx := context.Background()
	_, consumer, op, source, dest := reissueFixture(t)

	req := ScheduleEventRequest{
		RequestID:    uuid.NewString(),
		NotBefore:    time.Now().Add(time.Hour),
		GraceSeconds: 300,
		Publication: PublishEventRequest{
			Repo:        source.Scope.Repo,
			Kind:        "request",
			Ref:         RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
			Destination: EventDestination{Type: "agent", Name: consumer.channel.Principal},
		},
	}
	scheduled, err := op.ScheduleEvent(ctx, req)
	if err != nil {
		t.Fatal(err)
	}

	// Advance the schedule to be due without wall-clock sleep.
	if _, err := op.pool.Exec(ctx, `UPDATE cairn.agent_schedule SET not_before=clock_timestamp()-interval '1 second' WHERE occurrence_id=$1`, scheduled.OccurrenceID); err != nil {
		t.Fatal(err)
	}

	// Launch multiple concurrent ticks simultaneously.
	const workers = 4
	start := make(chan struct{})
	var wg sync.WaitGroup
	type tickOutcome struct {
		result ScheduleTickResult
		err    error
	}
	outcomes := make([]tickOutcome, workers)
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			<-start
			res, err := op.TickSchedules(ctx, ScheduleTickRequest{Repo: source.Scope.Repo})
			outcomes[idx] = tickOutcome{result: res, err: err}
		}(i)
	}
	close(start)
	wg.Wait()

	var totalFired int
	for i, o := range outcomes {
		if o.err != nil {
			t.Fatalf("worker %d returned error: %v", i, o.err)
		}
		for _, occ := range o.result.Occurrences {
			if occ.State == "fired" {
				totalFired++
				if occ.OccurrenceID != scheduled.OccurrenceID {
					t.Fatalf("unexpected occurrence ID: %s", occ.OccurrenceID)
				}
				if occ.EventID != scheduled.OccurrenceID {
					t.Fatalf("occurrence EventID must equal OccurrenceID, got: %s", occ.EventID)
				}
			}
		}
	}
	if totalFired != 1 {
		t.Fatalf("expected exactly 1 fired occurrence across concurrent ticks, got %d", totalFired)
	}

	// Verify database row counts: exactly 1 event and 1 delivery.
	var eventCount, deliveryCount int
	if err := op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event WHERE event_id=$1`, scheduled.OccurrenceID).Scan(&eventCount); err != nil {
		t.Fatal(err)
	}
	if eventCount != 1 {
		t.Fatalf("agent_event count: got %d, want 1", eventCount)
	}
	if err := op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_delivery WHERE event_id=$1`, scheduled.OccurrenceID).Scan(&deliveryCount); err != nil {
		t.Fatal(err)
	}
	if deliveryCount != 1 {
		t.Fatalf("agent_delivery count: got %d, want 1", deliveryCount)
	}

	// Consumer receives the single delivery.
	delivery := nextFixture(t, consumer, dest)
	if delivery.Event.EventID != scheduled.OccurrenceID {
		t.Fatalf("delivery event mismatch: got %s, want %s", delivery.Event.EventID, scheduled.OccurrenceID)
	}

	// Subsequent tick finds no pending due schedules.
	again, err := op.TickSchedules(ctx, ScheduleTickRequest{Repo: source.Scope.Repo})
	if err != nil || len(again.Occurrences) != 0 {
		t.Fatalf("repeated tick: %+v %v", again, err)
	}
}

// TestScheduleInjectedFailureRollsBackAndRetries asserts that an atomic failure
// during the schedule's terminal state update rolls back both event publication
// and fanout delivery, leaving the schedule pending for a successful retry.
func TestScheduleInjectedFailureRollsBackAndRetries(t *testing.T) {
	ctx := context.Background()
	_, consumer, op, source, dest := reissueFixture(t)

	req := ScheduleEventRequest{
		RequestID:    uuid.NewString(),
		NotBefore:    time.Now().Add(-time.Second),
		GraceSeconds: 300,
		Publication: PublishEventRequest{
			Repo:        source.Scope.Repo,
			Kind:        "request",
			Ref:         RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
			Destination: EventDestination{Type: "agent", Name: consumer.channel.Principal},
		},
	}
	scheduled, err := op.ScheduleEvent(ctx, req)
	if err != nil {
		t.Fatal(err)
	}

	trigger := "schedule_failure_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	_, err = op.pool.Exec(ctx, fmt.Sprintf(`CREATE FUNCTION cairn.%s() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected failure during schedule terminal update'; END $$; CREATE TRIGGER %s BEFORE UPDATE ON cairn.agent_schedule FOR EACH ROW WHEN (OLD.occurrence_id='%s'::uuid AND NEW.state='fired') EXECUTE FUNCTION cairn.%s()`, trigger, trigger, scheduled.OccurrenceID, trigger))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := op.pool.Exec(context.Background(), fmt.Sprintf(`DROP TRIGGER IF EXISTS %s ON cairn.agent_schedule; DROP FUNCTION IF EXISTS cairn.%s()`, trigger, trigger)); err != nil {
			t.Errorf("injection cleanup: %v", err)
		}
	})

	// Tick should fail due to the injected trigger exception.
	tick := ScheduleTickRequest{Repo: source.Scope.Repo}
	_, err = op.TickSchedules(ctx, tick)
	if err == nil || !strings.Contains(err.Error(), "injected failure during schedule terminal update") {
		t.Fatalf("expected injected failure error, got: %v", err)
	}

	// Verify complete rollback: no event, no delivery, schedule remains pending.
	var eventCount, deliveryCount int
	if err := op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event WHERE event_id=$1`, scheduled.OccurrenceID).Scan(&eventCount); err != nil {
		t.Fatal(err)
	}
	if eventCount != 0 {
		t.Fatalf("agent_event must be rolled back, found %d rows", eventCount)
	}
	if err := op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_delivery WHERE event_id=$1`, scheduled.OccurrenceID).Scan(&deliveryCount); err != nil {
		t.Fatal(err)
	}
	if deliveryCount != 0 {
		t.Fatalf("agent_delivery must be rolled back, found %d rows", deliveryCount)
	}

	var state, code, eventID string
	var finishedAt *time.Time
	if err := op.pool.QueryRow(ctx, `SELECT state, code, finished_at, COALESCE(event_id::text,'') FROM cairn.agent_schedule WHERE occurrence_id=$1`, scheduled.OccurrenceID).Scan(&state, &code, &finishedAt, &eventID); err != nil {
		t.Fatal(err)
	}
	if state != "pending" || finishedAt != nil || eventID != "" {
		t.Fatalf("schedule must remain pending: state=%s, code=%s, finishedAt=%v, eventID=%s", state, code, finishedAt, eventID)
	}

	// Consumer has received nothing.
	next, err := consumer.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil || next.Delivery != nil {
		t.Fatalf("consumer must have no deliveries: %+v %v", next, err)
	}

	// Remove injected trigger to permit the retry.
	if _, err := op.pool.Exec(ctx, fmt.Sprintf(`DROP TRIGGER %s ON cairn.agent_schedule`, trigger)); err != nil {
		t.Fatal(err)
	}

	// Retry the tick: now it must succeed and fire exactly once.
	result, err := op.TickSchedules(ctx, tick)
	if err != nil || len(result.Occurrences) != 1 || result.Occurrences[0].State != "fired" {
		t.Fatalf("retry tick failed: %+v %v", result, err)
	}
	if result.Occurrences[0].OccurrenceID != scheduled.OccurrenceID || result.Occurrences[0].EventID != scheduled.OccurrenceID {
		t.Fatalf("retry occurrence mismatch: %+v", result.Occurrences[0])
	}

	// Verify exactly 1 event and 1 delivery exist now.
	if err := op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event WHERE event_id=$1`, scheduled.OccurrenceID).Scan(&eventCount); err != nil {
		t.Fatal(err)
	}
	if eventCount != 1 {
		t.Fatalf("agent_event count after retry: got %d, want 1", eventCount)
	}
	if err := op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_delivery WHERE event_id=$1`, scheduled.OccurrenceID).Scan(&deliveryCount); err != nil {
		t.Fatal(err)
	}
	if deliveryCount != 1 {
		t.Fatalf("agent_delivery count after retry: got %d, want 1", deliveryCount)
	}

	delivery := nextFixture(t, consumer, dest)
	if delivery.Event.EventID != scheduled.OccurrenceID {
		t.Fatalf("delivery event mismatch: got %s, want %s", delivery.Event.EventID, scheduled.OccurrenceID)
	}

	// Subsequent tick finds nothing.
	again, err := op.TickSchedules(ctx, tick)
	if err != nil || len(again.Occurrences) != 0 {
		t.Fatalf("repeated tick: %+v %v", again, err)
	}
}

// TestScheduleSourceRetractionOrDeletionSkipsEvent asserts that if a scheduled
// source version is retracted or deleted before the due time, ticking
// the schedule transitions it to state='skipped' with code='NOT_FOUND' without
// emitting any event or deliveries.
func TestScheduleSourceRetractionOrDeletionSkipsEvent(t *testing.T) {
	ctx := context.Background()

	t.Run("RetractedSource", func(t *testing.T) {
		a, consumer, op, source, dest := reissueFixture(t)
		req := ScheduleEventRequest{
			RequestID:    uuid.NewString(),
			NotBefore:    time.Now().Add(time.Hour),
			GraceSeconds: 300,
			Publication: PublishEventRequest{
				Repo:        source.Scope.Repo,
				Kind:        "request",
				Ref:         RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
				Destination: EventDestination{Type: "agent", Name: consumer.channel.Principal},
			},
		}
		scheduled, err := op.ScheduleEvent(ctx, req)
		if err != nil {
			t.Fatal(err)
		}

		// Retract source record before due time.
		if _, err := a.pool.Exec(ctx, `UPDATE cairn.memory_record SET lifecycle='retracted' WHERE record_id=$1`, source.RecordID); err != nil {
			t.Fatal(err)
		}

		// Advance schedule due instant.
		if _, err := op.pool.Exec(ctx, `UPDATE cairn.agent_schedule SET not_before=clock_timestamp()-interval '1 second' WHERE occurrence_id=$1`, scheduled.OccurrenceID); err != nil {
			t.Fatal(err)
		}

		result, err := op.TickSchedules(ctx, ScheduleTickRequest{Repo: source.Scope.Repo})
		if err != nil || len(result.Occurrences) != 1 {
			t.Fatalf("tick result: %+v %v", result, err)
		}
		occ := result.Occurrences[0]
		if occ.State != "skipped" || occ.Code != "NOT_FOUND" || occ.EventID != "" || occ.FinishedAt == nil {
			t.Fatalf("expected skipped with NOT_FOUND: %+v", occ)
		}

		// Verify no agent_event or agent_delivery was created.
		var eventCount, deliveryCount int
		if err := op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event WHERE event_id=$1`, scheduled.OccurrenceID).Scan(&eventCount); err != nil {
			t.Fatal(err)
		}
		if eventCount != 0 {
			t.Fatalf("agent_event should not exist, got %d", eventCount)
		}
		if err := op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_delivery WHERE event_id=$1`, scheduled.OccurrenceID).Scan(&deliveryCount); err != nil {
			t.Fatal(err)
		}
		if deliveryCount != 0 {
			t.Fatalf("agent_delivery should not exist, got %d", deliveryCount)
		}

		next, err := consumer.NextEvent(ctx, NextEventRequest{}, dest)
		if err != nil || next.Delivery != nil {
			t.Fatalf("consumer should have no deliveries: %+v %v", next, err)
		}
	})

	t.Run("DeletedSourceRecord", func(t *testing.T) {
		a, consumer, op, source, dest := reissueFixture(t)
		req := ScheduleEventRequest{
			RequestID:    uuid.NewString(),
			NotBefore:    time.Now().Add(time.Hour),
			GraceSeconds: 300,
			Publication: PublishEventRequest{
				Repo:        source.Scope.Repo,
				Kind:        "request",
				Ref:         RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
				Destination: EventDestination{Type: "agent", Name: consumer.channel.Principal},
			},
		}
		scheduled, err := op.ScheduleEvent(ctx, req)
		if err != nil {
			t.Fatal(err)
		}

		// Delete the source memory record before due time.
		if _, err := a.Delete(ctx, DeleteRequest{RequestID: uuid.NewString(), RecordID: source.RecordID, ExpectedVersion: source.Version}); err != nil {
			t.Fatal(err)
		}

		// Advance schedule due instant.
		if _, err := op.pool.Exec(ctx, `UPDATE cairn.agent_schedule SET not_before=clock_timestamp()-interval '1 second' WHERE occurrence_id=$1`, scheduled.OccurrenceID); err != nil {
			t.Fatal(err)
		}

		result, err := op.TickSchedules(ctx, ScheduleTickRequest{Repo: source.Scope.Repo})
		if err != nil || len(result.Occurrences) != 1 {
			t.Fatalf("tick result: %+v %v", result, err)
		}
		occ := result.Occurrences[0]
		if occ.State != "skipped" || occ.Code != "NOT_FOUND" || occ.EventID != "" || occ.FinishedAt == nil {
			t.Fatalf("expected skipped with NOT_FOUND: %+v", occ)
		}

		var eventCount int
		if err := op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event WHERE event_id=$1`, scheduled.OccurrenceID).Scan(&eventCount); err != nil {
			t.Fatal(err)
		}
		if eventCount != 0 {
			t.Fatalf("agent_event should not exist, got %d", eventCount)
		}

		next, err := consumer.NextEvent(ctx, NextEventRequest{}, dest)
		if err != nil || next.Delivery != nil {
			t.Fatalf("consumer should have no deliveries: %+v %v", next, err)
		}
	})
}

// TestScheduleIdempotentRetryAndConflict verifies that repeating a ScheduleEvent
// request with the same request_id returns the identical ScheduledEvent, while
// mutating fields under the same request_id fails with IDEMPOTENCY_CONFLICT.
func TestScheduleIdempotentRetryAndConflict(t *testing.T) {
	ctx := context.Background()
	_, consumer, op, source, _ := reissueFixture(t)

	req := ScheduleEventRequest{
		RequestID:    uuid.NewString(),
		NotBefore:    time.Now().Add(time.Hour).Truncate(time.Microsecond),
		GraceSeconds: 300,
		Publication: PublishEventRequest{
			Repo:        source.Scope.Repo,
			Kind:        "request",
			Ref:         RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
			Destination: EventDestination{Type: "agent", Name: consumer.channel.Principal},
		},
	}

	scheduled, err := op.ScheduleEvent(ctx, req)
	if err != nil {
		t.Fatal(err)
	}

	// Idempotent retry returns identical occurrence.
	retry, err := op.ScheduleEvent(ctx, req)
	if err != nil {
		t.Fatalf("idempotent retry error: %v", err)
	}
	if retry.OccurrenceID != scheduled.OccurrenceID || retry.State != scheduled.State ||
		retry.Repo != scheduled.Repo || retry.GraceSeconds != scheduled.GraceSeconds {
		t.Fatalf("retry mismatch: got %+v, want %+v", retry, scheduled)
	}

	// Conflict 1: changed GraceSeconds with same RequestID.
	conflictGrace := req
	conflictGrace.GraceSeconds = 600
	_, err = op.ScheduleEvent(ctx, conflictGrace)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")

	// Conflict 2: changed NotBefore with same RequestID.
	conflictTime := req
	conflictTime.NotBefore = req.NotBefore.Add(10 * time.Minute)
	_, err = op.ScheduleEvent(ctx, conflictTime)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")

	// Conflict 3: changed publication Kind with same RequestID.
	conflictPub := req
	conflictPub.Publication.Kind = "notice"
	_, err = op.ScheduleEvent(ctx, conflictPub)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")

	// Resubmitting with a new RequestID succeeds and generates a distinct occurrence.
	distinctReq := req
	distinctReq.RequestID = uuid.NewString()
	distinctReq.GraceSeconds = 600
	distinct, err := op.ScheduleEvent(ctx, distinctReq)
	if err != nil {
		t.Fatal(err)
	}
	if distinct.OccurrenceID == scheduled.OccurrenceID {
		t.Fatalf("expected distinct occurrence ID, got %s", distinct.OccurrenceID)
	}
}

// TestScheduleTopicMembershipSnapshottedAtFiring asserts that subscribers to a topic
// are snapshotted dynamically at firing time, not when the schedule was created.
// Subscribers added after schedule creation receive the event; subscribers removed
// before firing do not receive it.
func TestScheduleTopicMembershipSnapshottedAtFiring(t *testing.T) {
	ctx := context.Background()
	repo := "repo-" + strings.ReplaceAll(uuid.NewString(), "-", "")
	publisher := testStore(t, Channel{Principal: "agent:publisher", Repo: repo})
	cEarlyUnsub := testStore(t, Channel{Principal: "agent:subscriber-early-unsub", Repo: repo})
	cLateJoin := testStore(t, Channel{Principal: "agent:subscriber-late-join", Repo: repo})
	cNeverSub := testStore(t, Channel{Principal: "agent:non-subscriber", Repo: repo})
	op := testStore(t, Channel{Principal: "operator:admin", Operator: true})

	dest := Destination{Name: "hosted", AllowLocal: false}

	draft := projectNote(repo)
	draft.Sensitivity = "shareable"
	draft.Body = "topic broadcast scheduled item"
	source, err := publisher.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}

	topic := "ops-broadcast"

	// 1. Prior to schedule creation, only cEarlyUnsub is subscribed.
	if _, err := cEarlyUnsub.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: repo, Topic: topic, Active: true}); err != nil {
		t.Fatal(err)
	}

	// 2. Operator schedules event destined for the topic.
	req := ScheduleEventRequest{
		RequestID:    uuid.NewString(),
		NotBefore:    time.Now().Add(time.Hour),
		GraceSeconds: 300,
		Publication: PublishEventRequest{
			Repo:        repo,
			Kind:        "notice",
			Ref:         RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
			Destination: EventDestination{Type: "topic", Name: topic},
		},
	}
	scheduled, err := op.ScheduleEvent(ctx, req)
	if err != nil {
		t.Fatal(err)
	}

	// 3. AFTER schedule creation but BEFORE tick:
	// cEarlyUnsub unsubscribes.
	if _, err := cEarlyUnsub.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: repo, Topic: topic, Active: false}); err != nil {
		t.Fatal(err)
	}
	// cLateJoin subscribes.
	if _, err := cLateJoin.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: repo, Topic: topic, Active: true}); err != nil {
		t.Fatal(err)
	}
	// cNeverSub subscribes and then immediately unsubscribes to verify active=false check.
	if _, err := cNeverSub.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: repo, Topic: topic, Active: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := cNeverSub.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: repo, Topic: topic, Active: false}); err != nil {
		t.Fatal(err)
	}

	// 4. Advance due instant.
	if _, err := op.pool.Exec(ctx, `UPDATE cairn.agent_schedule SET not_before=clock_timestamp()-interval '1 second' WHERE occurrence_id=$1`, scheduled.OccurrenceID); err != nil {
		t.Fatal(err)
	}

	// 5. Fire the schedule.
	result, err := op.TickSchedules(ctx, ScheduleTickRequest{Repo: repo})
	if err != nil || len(result.Occurrences) != 1 || result.Occurrences[0].State != "fired" {
		t.Fatalf("tick failed: %+v %v", result, err)
	}

	// 6. Verify deliveries in database. Only cLateJoin should have a delivery row.
	rows, err := op.pool.Query(ctx, `SELECT consumer FROM cairn.agent_delivery WHERE event_id=$1`, scheduled.OccurrenceID)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var deliveredConsumers []string
	for rows.Next() {
		var c string
		if err := rows.Scan(&c); err != nil {
			t.Fatal(err)
		}
		deliveredConsumers = append(deliveredConsumers, c)
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}

	if len(deliveredConsumers) != 1 || deliveredConsumers[0] != cLateJoin.channel.Principal {
		t.Fatalf("expected delivery only to %s, got: %v", cLateJoin.channel.Principal, deliveredConsumers)
	}

	// 7. Verify via NextEvent on each consumer store:
	// cLateJoin receives delivery.
	delivery := nextFixture(t, cLateJoin, dest)
	if delivery.Event.EventID != scheduled.OccurrenceID {
		t.Fatalf("delivery event mismatch: got %s, want %s", delivery.Event.EventID, scheduled.OccurrenceID)
	}

	// cEarlyUnsub receives nothing.
	nextEarly, err := cEarlyUnsub.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil || nextEarly.Delivery != nil {
		t.Fatalf("cEarlyUnsub must receive nothing, got %+v %v", nextEarly, err)
	}

	// cNeverSub receives nothing.
	nextNever, err := cNeverSub.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil || nextNever.Delivery != nil {
		t.Fatalf("cNeverSub must receive nothing, got %+v %v", nextNever, err)
	}
}
