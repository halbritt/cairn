package core

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestResponseGroupCollectsExplicitReplyNotAcknowledgment(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	event, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"agent", b.channel.Principal}, ResponseGroup: &ResponseGroupSpec{Deadline: time.Now().Add(time.Hour), PartialPolicy: "all"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	if event.CorrelationID != event.EventID {
		t.Fatalf("default correlation: %+v", event)
	}
	d := nextFixture(t, b, dest)
	_, err = b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: d.DeliveryID, LeaseID: d.LeaseID, Disposition: "handled"}, dest)
	if err != nil {
		t.Fatal(err)
	}
	group, err := a.ResponseGroup(ctx, ResponseGroupQuery{EventID: event.EventID}, dest)
	if err != nil || group.State != "open" || group.Responded != 0 || len(group.Members) != 1 {
		t.Fatalf("ack treated as reply: %+v %v", group, err)
	}
	reply, err := b.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "response", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"agent", a.channel.Principal}, CausationID: event.EventID, CorrelationID: event.CorrelationID}, dest)
	if err != nil {
		t.Fatal(err)
	}
	group, err = a.ResponseGroup(ctx, ResponseGroupQuery{EventID: event.EventID}, dest)
	if err != nil || group.State != "collected" || group.Responded != 1 || group.Members[0].ResponseEventID != reply.EventID || !group.Members[0].PayloadAvailable {
		t.Fatalf("explicit reply missing: %+v %v", group, err)
	}
	_, err = b.ResponseGroup(ctx, ResponseGroupQuery{EventID: event.EventID}, dest)
	requireCode(t, err, "NOT_FOUND")
}

func TestResponseGroupSnapshotDuplicatesDeadlineAndPartialPolicy(t *testing.T) {
	for _, policy := range []string{"all", "partial"} {
		t.Run(policy, func(t *testing.T) {
			ctx := context.Background()
			a, b, r, dest := eventFixture(t)
			c := testStore(t, Channel{Principal: "third-" + r.Scope.Repo, Repo: r.Scope.Repo})
			for _, s := range []*Store{b, c} {
				if _, err := s.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: "review", Active: true}); err != nil {
					t.Fatal(err)
				}
			}
			e, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"topic", "review"}, ResponseGroup: &ResponseGroupSpec{Deadline: time.Now().Add(time.Hour), PartialPolicy: policy}}, dest)
			if err != nil {
				t.Fatal(err)
			}
			if _, err = c.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: "review", Active: false}); err != nil {
				t.Fatal(err)
			}
			reply := PublishEventRequest{RequestID: uuid.NewString(), Kind: "response", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"agent", a.channel.Principal}, CausationID: e.EventID, CorrelationID: e.CorrelationID}
			first, err := b.PublishEvent(ctx, reply, dest)
			if err != nil {
				t.Fatal(err)
			}
			same, err := b.PublishEvent(ctx, reply, dest)
			if err != nil || same.EventID != first.EventID {
				t.Fatalf("reply retry: %+v %v", same, err)
			}
			reply.RequestID = uuid.NewString()
			if _, err = b.PublishEvent(ctx, reply, dest); err != nil {
				t.Fatal(err)
			}
			missing := nextFixture(t, c, dest)
			if _, err = c.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: missing.DeliveryID, LeaseID: missing.LeaseID, Disposition: "failed", Code: "processing_failed"}, dest); err != nil {
				t.Fatal(err)
			}
			g, err := a.ResponseGroup(ctx, ResponseGroupQuery{EventID: e.EventID}, dest)
			if err != nil || g.State != "open" || g.Expected != 2 || g.Responded != 1 || len(g.Observations) != 2 || g.Observations[1].Disposition != "duplicate" {
				t.Fatalf("snapshot/duplicates: %+v %v", g, err)
			}
			if _, err = a.pool.Exec(ctx, `UPDATE cairn.agent_response_group SET deadline=clock_timestamp()-interval '1 second' WHERE event_id=$1`, e.EventID); err != nil {
				t.Fatal(err)
			}
			op := testStore(t, Channel{Principal: "group-operator", Operator: true})
			if _, err = op.SweepRequestControls(ctx, RequestControlSweep{Repo: r.Scope.Repo}); err != nil {
				t.Fatal(err)
			}
			g, err = a.ResponseGroup(ctx, ResponseGroupQuery{EventID: e.EventID}, dest)
			want := "incomplete"
			if policy == "partial" {
				want = "partial"
			}
			if err != nil || g.State != want || g.Code != "deadline" || g.ClosedAt == nil {
				t.Fatalf("deadline: %+v %v", g, err)
			}
			reply.RequestID = uuid.NewString()
			if _, err = c.PublishEvent(ctx, reply, dest); err != nil {
				t.Fatal(err)
			}
			g, err = a.ResponseGroup(ctx, ResponseGroupQuery{EventID: e.EventID, After: g.Observations[1].Position, Limit: 1}, dest)
			if err != nil || g.State != want || g.Responded != 1 || len(g.Observations) != 1 || g.Observations[0].Disposition != "late" {
				t.Fatalf("late reply reopened group: %+v %v", g, err)
			}
		})
	}
}

func TestResponseGroupRestoreClosesOpenCollection(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	e, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"agent", b.channel.Principal}, ResponseGroup: &ResponseGroupSpec{Deadline: time.Now().Add(time.Hour), PartialPolicy: "partial"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	op := testStore(t, Channel{Principal: "group-restore", Operator: true})
	if _, err = op.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "Fence uncertain post-backup response history"}); err != nil {
		t.Fatal(err)
	}
	g, err := a.ResponseGroup(ctx, ResponseGroupQuery{EventID: e.EventID}, dest)
	if err != nil || g.State != "incomplete" || g.Code != "restore_fenced" {
		t.Fatalf("restored group reopened: %+v %v", g, err)
	}
	reply, err := b.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "response", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"agent", a.channel.Principal}, CausationID: e.EventID, CorrelationID: e.CorrelationID}, dest)
	if err != nil {
		t.Fatal(err)
	}
	g, err = a.ResponseGroup(ctx, ResponseGroupQuery{EventID: e.EventID}, dest)
	if err != nil || g.State != "incomplete" || g.Responded != 0 || g.Observations[0].Disposition != "late" || g.Observations[0].EventID != reply.EventID {
		t.Fatalf("late restored response: %+v %v", g, err)
	}
}

func TestResponseGroupForgottenReplyAndUnmatchedMetadata(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	e, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"agent", b.channel.Principal}, ResponseGroup: &ResponseGroupSpec{Deadline: time.Now().Add(time.Hour), PartialPolicy: "all"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	draft := projectNote(r.Scope.Repo)
	draft.Body = "selected response"
	draft.Sensitivity = "shareable"
	result, err := b.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	reply := PublishEventRequest{RequestID: uuid.NewString(), Kind: "response", Ref: RecordVersionRef{result.RecordID, 1}, Destination: EventDestination{"agent", a.channel.Principal}, CausationID: e.EventID, CorrelationID: uuid.NewString()}
	if _, err = b.PublishEvent(ctx, reply, dest); err != nil {
		t.Fatal(err)
	}
	g, err := a.ResponseGroup(ctx, ResponseGroupQuery{EventID: e.EventID}, dest)
	if err != nil || g.Responded != 0 || g.Observations[0].Disposition != "unmatched" {
		t.Fatalf("wrong correlation counted: %+v %v", g, err)
	}
	reply.RequestID = uuid.NewString()
	reply.CorrelationID = e.CorrelationID
	if _, err = b.PublishEvent(ctx, reply, dest); err != nil {
		t.Fatal(err)
	}
	op, root := testOperator(t)
	preview, err := op.PreviewDeletion(ctx, result.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = op.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: result.RecordID, ExpectedVersion: 1, GrantID: root.ID, PreviewID: preview.PreviewID}); err != nil {
		t.Fatal(err)
	}
	g, err = a.ResponseGroup(ctx, ResponseGroupQuery{EventID: e.EventID}, dest)
	if err != nil || g.State != "collected" || g.Responded != 1 || !g.Members[0].Responded || g.Members[0].PayloadAvailable || g.Members[0].Ref != nil || g.Members[0].ResponseEventID != "" || g.TaskOutcome != "unknown" {
		t.Fatalf("forgotten reply availability or acceptance: %+v %v", g, err)
	}
	list, err := a.ResponseGroups(ctx, ResponseGroupListRequest{Limit: 1}, dest)
	if err != nil || len(list.Groups) != 1 || list.Groups[0].EventID != e.EventID {
		t.Fatalf("owned list: %+v %v", list, err)
	}
	other, err := b.ResponseGroups(ctx, ResponseGroupListRequest{}, dest)
	if err != nil || len(other.Groups) != 0 {
		t.Fatalf("recipient saw owner groups: %+v %v", other, err)
	}
}

func TestResponseGroupAdmissionRefusalsRollbackPublication(t *testing.T) {
	ctx := context.Background()
	a, _, r, dest := eventFixture(t)
	req := PublishEventRequest{RequestID: uuid.NewString(), Repo: r.Scope.Repo, Kind: "request", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"topic", "bounded-group"}, ResponseGroup: &ResponseGroupSpec{Deadline: time.Now().Add(time.Hour), PartialPolicy: "all"}}
	_, err := a.PublishEvent(ctx, req, dest)
	requireCode(t, err, "GROUP_EMPTY")
	for i := 0; i < 101; i++ {
		s := testStore(t, Channel{Principal: uuid.NewString(), Repo: r.Scope.Repo})
		if _, err = s.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: "bounded-group", Active: true}); err != nil {
			t.Fatal(err)
		}
		s.Close()
	}
	req.RequestID = uuid.NewString()
	_, err = a.PublishEvent(ctx, req, dest)
	requireCode(t, err, "GROUP_LIMIT")
	page, err := a.Events(ctx, EventQuery{}, dest)
	if err != nil || len(page.Events) != 0 {
		t.Fatalf("refused group left an event: %+v %v", page, err)
	}
	req.Destination = EventDestination{"agent", "offline-consumer"}
	req.ResponseGroup.Deadline = time.Now().Add(-time.Second)
	req.RequestID = uuid.NewString()
	_, err = a.PublishEvent(ctx, req, dest)
	requireCode(t, err, "GROUP_EXPIRED")
	op := testStore(t, Channel{Principal: "group-schedule-operator", Operator: true})
	req.RequestID = ""
	scheduled, err := op.ScheduleEvent(ctx, ScheduleEventRequest{RequestID: uuid.NewString(), NotBefore: time.Now().Add(-time.Second), GraceSeconds: 60, Publication: req})
	if err != nil {
		t.Fatal(err)
	}
	tick, err := op.TickSchedules(ctx, ScheduleTickRequest{Repo: r.Scope.Repo})
	if err != nil || len(tick.Occurrences) != 1 || tick.Occurrences[0].OccurrenceID != scheduled.OccurrenceID || tick.Occurrences[0].State != "skipped" || tick.Occurrences[0].Code != "GROUP_EXPIRED" {
		t.Fatalf("expired scheduled group poisoned tick: %+v %v", tick, err)
	}
}
