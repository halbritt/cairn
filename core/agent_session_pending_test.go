package core

import (
	"context"
	"reflect"
	"testing"

	"github.com/google/uuid"
)

func TestSessionInboxPendingCountsBusyBacklogWithoutClaiming(t *testing.T) {
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	a, err := receiver.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "busy-cue", NativeSessionID: "busy-cue", Metadata: AgentMetadata{Harness: "claude", Project: "test", Workspace: "/work/test", State: "busy", DeliveryMode: "existing-session"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{a.AgentID, a.ExecutionID}
	pending, err := receiver.SessionInboxPending(ctx, ref, dest)
	if err != nil || pending != (SessionInboxPending{}) {
		t.Fatalf("empty inbox: %+v %v", pending, err)
	}
	var events []string
	for _, kind := range []string{"request", "notice", "notice"} {
		event, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: kind, Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "agent", Name: a.Inbox}}, dest)
		if err != nil {
			t.Fatal(err)
		}
		events = append(events, event.EventID)
	}
	// Busy sessions get no readiness hint, but the pending count still answers.
	ready, err := receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.DeliveryID != "" {
		t.Fatalf("busy readiness: %+v %v", ready, err)
	}
	pending, err = receiver.SessionInboxPending(ctx, ref, dest)
	if err != nil || pending.Requests != 1 || pending.Notices != 2 || pending.Responses != 0 || pending.Truncated || pending.LatestPosition == 0 {
		t.Fatalf("busy backlog: %+v %v", pending, err)
	}
	for _, id := range events {
		status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: id}, dest)
		if err != nil || status.Deliveries[0].State != "pending" || status.Deliveries[0].Attempts != 0 {
			t.Fatalf("count claimed work: %+v %v", status, err)
		}
	}
	// A newer arrival changes the position even when a claim keeps the count level.
	previous := pending.LatestPosition
	claimed, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}, dest)
	if err != nil || claimed.Attempt == nil {
		t.Fatalf("claim: %+v %v", claimed, err)
	}
	if _, err = sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "agent", Name: a.Inbox}}, dest); err != nil {
		t.Fatal(err)
	}
	pending, err = receiver.SessionInboxPending(ctx, ref, dest)
	if err != nil || pending.Requests+pending.Notices != 3 || pending.LatestPosition <= previous {
		t.Fatalf("held item still counted or arrival missed: %+v %v", pending, err)
	}
	// Another principal cannot read this session's counts.
	_, err = sender.SessionInboxPending(ctx, ref, dest)
	requireCode(t, err, "NOT_FOUND")
}

func TestSessionInboxPendingIgnoresFreshWorkerSessions(t *testing.T) {
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	a, err := receiver.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "fresh-cue", NativeSessionID: "fresh-cue", Metadata: AgentMetadata{Harness: "claude", Project: "test", Workspace: "/work/test", State: "busy", DeliveryMode: "fresh-worker"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "agent", Name: a.Inbox}}, dest); err != nil {
		t.Fatal(err)
	}
	pending, err := receiver.SessionInboxPending(ctx, AgentSessionRef{a.AgentID, a.ExecutionID}, dest)
	if err != nil || pending != (SessionInboxPending{}) {
		t.Fatalf("fresh worker counted: %+v %v", pending, err)
	}
}

func TestSessionInboxPendingLatestArrivalBeyondCountLimit(t *testing.T) {
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	a, err := receiver.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "bounded-cue", NativeSessionID: "bounded-cue", Metadata: AgentMetadata{Harness: "claude", Project: "test", Workspace: "/work/test", State: "busy", DeliveryMode: "existing-session"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{a.AgentID, a.ExecutionID}
	publish := func(kind string) AgentEvent {
		t.Helper()
		event, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: kind, Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "agent", Name: a.Inbox}}, dest)
		if err != nil {
			t.Fatal(err)
		}
		return event
	}
	var first, newest AgentEvent
	for i := 1; i <= 101; i++ {
		newest = publish("notice")
		if i == 1 {
			first = newest
		}
		if i >= 99 {
			got, err := receiver.SessionInboxPending(ctx, ref, dest)
			want := SessionInboxPending{Notices: min(i, 100), Truncated: i >= 100, LatestPosition: newest.Position}
			if err != nil || got != want {
				t.Errorf("%d pending: got %+v, want %+v: %v", i, got, want, err)
			}
		}
	}
	before, err := receiver.SessionInboxPending(ctx, ref, dest)
	if err != nil {
		t.Fatal(err)
	}
	// Claim order remains oldest first. Removing that item must not look like
	// another arrival, even when the bounded count remains at 100.
	claim, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}, dest)
	if err != nil || claim.Attempt == nil || claim.Attempt.Delivery.Event.EventID != first.EventID {
		t.Fatalf("oldest claim: %+v %v", claim, err)
	}
	after, err := receiver.SessionInboxPending(ctx, ref, dest)
	if err != nil || after != before {
		t.Errorf("oldest removal changed saturated summary: before %+v after %+v: %v", before, after, err)
	}
	publish("request")
	newest = publish("response")
	want := SessionInboxPending{Requests: 1, Notices: 98, Responses: 1, Truncated: true, LatestPosition: newest.Position}
	for range 2 {
		got, err := receiver.SessionInboxPending(ctx, ref, dest)
		if err != nil || got != want {
			t.Errorf("new mixed arrivals: got %+v, want %+v: %v", got, want, err)
		}
	}
	status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: newest.EventID}, dest)
	if err != nil || status.Deliveries[0].State != "pending" || status.Deliveries[0].Attempts != 0 {
		t.Fatalf("summary claimed latest arrival: %+v %v", status, err)
	}
}

func TestSessionInboxPendingLatestUsesDeliveryEligibility(t *testing.T) {
	for _, exclusion := range []string{"private", "other-inbox", "delayed", "admission-expired", "managed-deadline", "live-lease", "session-hold", "wake-hold"} {
		t.Run(exclusion, func(t *testing.T) {
			ctx := context.Background()
			sender, receiver, source, dest := eventFixture(t)
			a, err := receiver.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "eligibility", NativeSessionID: "eligibility", Metadata: AgentMetadata{Harness: "claude", Project: "test", Workspace: "/work/test", State: "busy", DeliveryMode: "existing-session"}}, dest)
			if err != nil {
				t.Fatal(err)
			}
			ref := AgentSessionRef{a.AgentID, a.ExecutionID}
			request := PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "agent", Name: a.Inbox}}
			older, err := sender.PublishEvent(ctx, request, dest)
			if err != nil {
				t.Fatal(err)
			}
			request.RequestID, request.Kind = uuid.NewString(), "request"
			publicationDest := dest
			if exclusion == "private" {
				draft := projectNote(source.Scope.Repo)
				draft.Sensitivity = "local"
				private, err := sender.Create(ctx, CreateRequest{uuid.NewString(), draft})
				if err != nil {
					t.Fatal(err)
				}
				request.Ref = RecordVersionRef{private.RecordID, private.Version}
				publicationDest = Destination{Name: "local", AllowLocal: true}
			}
			if exclusion == "other-inbox" {
				request.Destination.Name = sender.channel.Principal
			}
			newer, err := sender.PublishEvent(ctx, request, publicationDest)
			if err != nil {
				t.Fatal(err)
			}
			exec := func(query string, args ...any) {
				t.Helper()
				if _, err := receiver.pool.Exec(ctx, query, args...); err != nil {
					t.Fatal(err)
				}
			}
			// Simulate timing boundaries directly in this disposable fixture;
			// create live leases and holds through the actual claim interfaces.
			switch exclusion {
			case "delayed":
				exec(`UPDATE cairn.agent_delivery SET available_at=clock_timestamp()+interval '1 hour' WHERE event_id=$1`, newer.EventID)
			case "admission-expired":
				exec(`UPDATE cairn.agent_event SET admission_expires_at=clock_timestamp()-interval '1 second' WHERE event_id=$1`, newer.EventID)
			case "managed-deadline":
				exec(`UPDATE cairn.agent_event SET task_deadline=clock_timestamp()+interval '1 hour' WHERE event_id=$1`, newer.EventID)
			case "live-lease", "session-hold", "wake-hold":
				exec(`UPDATE cairn.agent_delivery SET available_at=clock_timestamp()+interval '1 hour' WHERE event_id=$1`, older.EventID)
				if exclusion == "wake-hold" {
					// Current public wake claims reject native session inboxes.
					// Retain the defensive exclusion for a preexisting/restored hold.
					exec(`UPDATE cairn.agent_delivery SET state='leased',lease_id=gen_random_uuid(),lease_until=clock_timestamp()-interval '1 second',attempts=1 WHERE event_id=$1`, newer.EventID)
					exec(`INSERT INTO cairn.agent_wake_attempt(attempt_id,delivery_id,repo,consumer,lease_id) SELECT gen_random_uuid(),d.delivery_id,e.repo,d.consumer,d.lease_id FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.event_id=$1`, newer.EventID)
				} else if exclusion == "session-hold" {
					claimed, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}, dest)
					if err != nil || claimed.Attempt == nil || claimed.Attempt.Delivery.Event.EventID != newer.EventID {
						t.Fatalf("native claim: %+v %v", claimed, err)
					}
				} else {
					view, err := receiver.ForAgentSession(ref, dest)
					if err != nil {
						t.Fatal(err)
					}
					claimed, err := view.NextEvent(ctx, NextEventRequest{}, dest)
					if err != nil || claimed.Delivery == nil || claimed.Delivery.Event.EventID != newer.EventID {
						t.Fatalf("manual lease: %+v %v", claimed, err)
					}
				}
				exec(`UPDATE cairn.agent_delivery SET available_at=clock_timestamp() WHERE event_id=$1`, older.EventID)
				if exclusion != "live-lease" {
					exec(`UPDATE cairn.agent_delivery SET lease_until=clock_timestamp()-interval '1 second' WHERE event_id=$1`, newer.EventID)
				}
			}
			before, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: newer.EventID}, publicationDest)
			if err != nil {
				t.Fatal(err)
			}
			want := SessionInboxPending{Notices: 1, LatestPosition: older.Position}
			for range 2 {
				got, err := receiver.SessionInboxPending(ctx, ref, dest)
				if err != nil || got != want {
					t.Fatalf("excluded newest event: got %+v, want %+v: %v", got, want, err)
				}
			}
			after, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: newer.EventID}, publicationDest)
			if err != nil {
				t.Fatal(err)
			}
			// Diagnosis includes time-varying observation metadata; compare the
			// retained delivery/event state rather than a fresh observation time.
			before.Deliveries[0].Diagnosis, after.Deliveries[0].Diagnosis = nil, nil
			if !reflect.DeepEqual(before, after) {
				t.Fatalf("pending read changed excluded delivery: before %+v after %+v: %v", before, after, err)
			}
		})
	}
}

func TestSessionInboxPendingRejectsReplacedExecution(t *testing.T) {
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	registration := RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "current", NativeSessionID: "current", Metadata: AgentMetadata{Harness: "claude", Project: "test", Workspace: "/work/test", State: "busy", DeliveryMode: "existing-session"}}
	a, err := receiver.RegisterAgent(ctx, registration, dest)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "agent", Name: a.Inbox}}, dest); err != nil {
		t.Fatal(err)
	}
	registration.RequestID = uuid.NewString()
	resumed, err := receiver.RegisterAgent(ctx, registration, dest)
	if err != nil {
		t.Fatal(err)
	}
	_, err = receiver.SessionInboxPending(ctx, AgentSessionRef{a.AgentID, a.ExecutionID}, dest)
	requireCode(t, err, "STALE_SESSION")
	got, err := receiver.SessionInboxPending(ctx, AgentSessionRef{resumed.AgentID, resumed.ExecutionID}, dest)
	if err != nil || got.Notices != 1 {
		t.Fatalf("current execution: %+v %v", got, err)
	}
}
