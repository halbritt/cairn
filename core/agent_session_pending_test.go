package core

import (
	"context"
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
