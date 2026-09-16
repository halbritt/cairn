package core

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestSchedulePublishesOnceAtDueTime(t *testing.T) {
	ctx := context.Background()
	_, consumer, op, source, dest := reissueFixture(t)
	req := ScheduleEventRequest{RequestID: uuid.NewString(), NotBefore: time.Now().Add(time.Hour), GraceSeconds: 300,
		Publication: PublishEventRequest{Repo: source.Scope.Repo, Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", consumer.channel.Principal}}}
	scheduled, err := op.ScheduleEvent(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	again, err := op.ScheduleEvent(ctx, req)
	if err != nil || again.OccurrenceID != scheduled.OccurrenceID {
		t.Fatalf("retry: %+v %v", again, err)
	}
	tick := ScheduleTickRequest{Repo: source.Scope.Repo}
	result, err := op.TickSchedules(ctx, tick)
	if err != nil || len(result.Occurrences) != 0 {
		t.Fatalf("early: %+v %v", result, err)
	}
	// Advance this disposable fixture's due instant without a wall-clock sleep.
	if _, err = op.pool.Exec(ctx, `UPDATE cairn.agent_schedule SET not_before=clock_timestamp()-interval '1 second' WHERE occurrence_id=$1`, scheduled.OccurrenceID); err != nil {
		t.Fatal(err)
	}
	result, err = op.TickSchedules(ctx, tick)
	if err != nil || len(result.Occurrences) != 1 || result.Occurrences[0].State != "fired" {
		t.Fatalf("due: %+v %v", result, err)
	}
	delivery := nextFixture(t, consumer, dest)
	if delivery.Event.EventID != scheduled.OccurrenceID || delivery.Event.Ref != req.Publication.Ref || delivery.Event.From != op.channel.Principal {
		t.Fatalf("wrong event: %+v", delivery.Event)
	}
	result, err = op.TickSchedules(ctx, tick)
	if err != nil || len(result.Occurrences) != 0 {
		t.Fatalf("repeat: %+v %v", result, err)
	}
}

func TestScheduleCancellationRacesWithFiring(t *testing.T) {
	ctx := context.Background()
	_, consumer, op, source, dest := reissueFixture(t)
	for range 8 {
		scheduled, err := op.ScheduleEvent(ctx, ScheduleEventRequest{RequestID: uuid.NewString(), NotBefore: time.Now().Add(-time.Second), GraceSeconds: 300, Publication: PublishEventRequest{Repo: source.Scope.Repo, Kind: "notice", Ref: RecordVersionRef{source.RecordID, 1}, Destination: EventDestination{"agent", consumer.channel.Principal}}})
		if err != nil {
			t.Fatal(err)
		}
		ticked := make(chan error, 1)
		cancelled := make(chan error, 1)
		req := CancelScheduleRequest{RequestID: uuid.NewString(), Repo: source.Scope.Repo, OccurrenceID: scheduled.OccurrenceID}
		go func() { _, e := op.TickSchedules(ctx, ScheduleTickRequest{Repo: source.Scope.Repo}); ticked <- e }()
		go func() { _, e := op.CancelSchedule(ctx, req); cancelled <- e }()
		if err = <-ticked; err != nil {
			t.Fatal(err)
		}
		cancelErr := <-cancelled
		page, err := op.Schedules(ctx, ScheduleQuery{Repo: source.Scope.Repo, OccurrenceID: scheduled.OccurrenceID})
		if err != nil || len(page.Occurrences) != 1 {
			t.Fatalf("list: %+v %v", page, err)
		}
		item := page.Occurrences[0]
		status, statusErr := op.AgentEventStatus(ctx, EventStatusRequest{EventID: scheduled.OccurrenceID}, dest)
		if cancelErr == nil {
			if item.State != "cancelled" {
				t.Fatalf("cancel won but %+v", item)
			}
			requireCode(t, statusErr, "NOT_FOUND")
			again, e := op.CancelSchedule(ctx, req)
			if e != nil || again.State != "cancelled" {
				t.Fatalf("cancel retry: %+v %v", again, e)
			}
		} else {
			requireCode(t, cancelErr, "VERSION_CONFLICT")
			if item.State != "fired" || statusErr != nil || len(status.Deliveries) != 1 {
				t.Fatalf("fire won but %+v %+v %v", item, status, statusErr)
			}
		}
	}
}

func TestScheduleMisfireTimezoneAndRestore(t *testing.T) {
	ctx := context.Background()
	_, consumer, op, source, _ := reissueFixture(t)
	// The DST fold has two different instants, both explicit in the timestamp.
	var ids []string
	for _, stamp := range []string{"2020-11-01T01:30:00-07:00", "2020-11-01T01:30:00-08:00"} {
		due, err := time.Parse(time.RFC3339, stamp)
		if err != nil {
			t.Fatal(err)
		}
		item, err := op.ScheduleEvent(ctx, ScheduleEventRequest{RequestID: uuid.NewString(), NotBefore: due, GraceSeconds: 300, Publication: PublishEventRequest{Repo: source.Scope.Repo, Kind: "request", Ref: RecordVersionRef{source.RecordID, 1}, Destination: EventDestination{"agent", consumer.channel.Principal}}})
		if err != nil || !item.NotBefore.Equal(due) {
			t.Fatalf("offset changed instant: %+v %v", item, err)
		}
		ids = append(ids, item.OccurrenceID)
	}
	result, err := op.TickSchedules(ctx, ScheduleTickRequest{Repo: source.Scope.Repo, Limit: 1})
	if err != nil || len(result.Occurrences) != 1 || result.Occurrences[0].OccurrenceID != ids[0] || result.Occurrences[0].State != "skipped" || result.Occurrences[0].Code != "misfire" {
		t.Fatalf("first missed occurrence: %+v %v", result, err)
	}
	if _, err = op.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "Disposable schedule restore fencing"}); err != nil {
		t.Fatal(err)
	}
	result, err = op.TickSchedules(ctx, ScheduleTickRequest{Repo: source.Scope.Repo})
	if err != nil || len(result.Occurrences) != 0 {
		t.Fatalf("restore executed old intent: %+v %v", result, err)
	}
	retained, err := op.Schedules(ctx, ScheduleQuery{Repo: source.Scope.Repo, OccurrenceID: ids[1]})
	if err != nil || len(retained.Occurrences) != 1 || retained.Occurrences[0].State != "skipped" || retained.Occurrences[0].Code != "restore_fenced" {
		t.Fatalf("restore audit: %+v %v", retained, err)
	}
	page, err := op.Schedules(ctx, ScheduleQuery{Repo: source.Scope.Repo, Limit: 1})
	if err != nil || !page.More || len(page.Occurrences) != 1 {
		t.Fatalf("first page: %+v %v", page, err)
	}
	next, err := op.Schedules(ctx, ScheduleQuery{Repo: source.Scope.Repo, Limit: 1, After: page.NextAfter})
	if err != nil || next.More || len(next.Occurrences) != 1 || next.Occurrences[0].OccurrenceID == page.Occurrences[0].OccurrenceID {
		t.Fatalf("next page: %+v %v", next, err)
	}
}

func TestScheduleOperatorOwnershipAndInvalidInput(t *testing.T) {
	ctx := context.Background()
	publisher, consumer, op, source, _ := reissueFixture(t)
	req := ScheduleEventRequest{RequestID: uuid.NewString(), NotBefore: time.Now().Add(time.Hour), GraceSeconds: 300, Publication: PublishEventRequest{Repo: source.Scope.Repo, Kind: "request", Ref: RecordVersionRef{source.RecordID, 1}, Destination: EventDestination{"agent", consumer.channel.Principal}}}
	_, err := publisher.ScheduleEvent(ctx, req)
	requireCode(t, err, "AUTHORITY_DENIED")
	item, err := op.ScheduleEvent(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	other := testStore(t, Channel{Principal: "other-schedule-operator", Operator: true})
	page, err := other.Schedules(ctx, ScheduleQuery{Repo: source.Scope.Repo})
	if err != nil || len(page.Occurrences) != 0 {
		t.Fatalf("wrong owner listing: %+v %v", page, err)
	}
	_, err = other.CancelSchedule(ctx, CancelScheduleRequest{RequestID: uuid.NewString(), Repo: source.Scope.Repo, OccurrenceID: item.OccurrenceID})
	requireCode(t, err, "NOT_FOUND")
	for _, mutate := range []func(*ScheduleEventRequest){func(r *ScheduleEventRequest) { r.GraceSeconds = 0 }, func(r *ScheduleEventRequest) { r.NotBefore = time.Time{} }, func(r *ScheduleEventRequest) { r.Publication.RequestID = uuid.NewString() }, func(r *ScheduleEventRequest) { r.Publication.Resolution = &AgentResolution{} }} {
		invalid := req
		invalid.RequestID = uuid.NewString()
		mutate(&invalid)
		_, err = op.ScheduleEvent(ctx, invalid)
		var failure *Error
		if !errors.As(err, &failure) || failure.Code != "INVALID_REQUEST" {
			t.Fatalf("invalid request: %v", err)
		}
	}
}

func TestScheduleFullPoolDoesNotStarveOtherDueWork(t *testing.T) {
	ctx := context.Background()
	op, _, consumer, source, _, _ := poolFixture(t)
	if _, err := op.ConfigureWorkerPool(ctx, WorkerPoolConfigRequest{RequestID: uuid.NewString(), Repo: source.Scope.Repo, Name: "coding", Enabled: false, MaxPending: 20, MaxPendingPerPublisher: 10, ExpectedRevision: 1}); err != nil {
		t.Fatal(err)
	}
	req := ScheduleEventRequest{RequestID: uuid.NewString(), NotBefore: time.Now().Add(-time.Second), GraceSeconds: 300, Publication: PublishEventRequest{Repo: source.Scope.Repo, Kind: "request", Ref: RecordVersionRef{source.RecordID, 1}, Destination: EventDestination{"pool", "coding"}, Pool: &PoolRequirements{Workspace: "/fixture/rhumb"}}}
	blocked, err := op.ScheduleEvent(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	req.NotBefore = time.Now()
	req.Publication.Destination = EventDestination{"agent", consumer.channel.Principal}
	req.Publication.Pool = nil
	ready, err := op.ScheduleEvent(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	first, err := op.TickSchedules(ctx, ScheduleTickRequest{Repo: source.Scope.Repo, Limit: 1})
	if err != nil || len(first.Occurrences) != 0 {
		t.Fatalf("blocked: %+v %v", first, err)
	}
	second, err := op.TickSchedules(ctx, ScheduleTickRequest{Repo: source.Scope.Repo, Limit: 1})
	if err != nil || len(second.Occurrences) != 1 || second.Occurrences[0].OccurrenceID != ready.OccurrenceID {
		t.Fatalf("starved behind %s: %+v %v", blocked.OccurrenceID, second, err)
	}
}
