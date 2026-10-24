package core

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestRoutineNoticePolicyDefersOnlyItsOwnWakeAndKeepsRetryDeadline(t *testing.T) {
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	agent, err := receiver.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(),
		Binding: "routine-notice", NativeSessionID: "routine-notice",
		Metadata: AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test",
			State: "idle", DeliveryMode: "existing-session"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{AgentID: agent.AgentID, ExecutionID: agent.ExecutionID}
	request := PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice",
		Ref:          RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination:  EventDestination{Type: "agent", Name: agent.Inbox},
		NoticePolicy: &NoticeWakePolicyRequest{Version: 1, Urgency: "routine", DeferSeconds: 60}}
	notice, err := sender.PublishEvent(ctx, request, dest)
	if err != nil {
		t.Fatal(err)
	}
	if notice.NoticePolicy == nil || notice.NoticePolicy.Owner != sender.channel.Principal ||
		notice.NoticePolicy.Version != 1 || notice.NoticePolicy.DeferSeconds != 60 {
		t.Fatalf("publisher-owned policy missing: %+v", notice.NoticePolicy)
	}
	status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: notice.EventID}, dest)
	if err != nil || len(status.Deliveries) != 1 {
		t.Fatalf("notice status: %+v %v", status, err)
	}
	deadline := status.Deliveries[0].AvailableAt
	if deadline == nil {
		t.Fatal("opt-in notice availability missing")
	}
	if delay := deadline.Sub(notice.CreatedAt); delay != 60*time.Second {
		t.Fatalf("fixed availability: %s", delay)
	}
	ready, err := receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.DeliveryID != "" {
		t.Fatalf("routine notice woke early: %+v %v", ready, err)
	}
	again, err := sender.PublishEvent(ctx, request, dest)
	if err != nil || again.EventID != notice.EventID {
		t.Fatalf("retry changed publication: %+v %v", again, err)
	}
	status, err = sender.AgentEventStatus(ctx, EventStatusRequest{EventID: notice.EventID}, dest)
	if err != nil || status.Deliveries[0].AvailableAt == nil || !status.Deliveries[0].AvailableAt.Equal(*deadline) {
		t.Fatalf("retry slid routine deadline: %+v %v", status, err)
	}
	changed := request
	changed.NoticePolicy = &NoticeWakePolicyRequest{Version: 1, Urgency: "urgent", DeferSeconds: 0}
	_, err = sender.PublishEvent(ctx, changed, dest)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	urgent, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(),
		Kind: "request", Ref: request.Ref, Destination: request.Destination}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ready, err = receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.EventID != urgent.EventID {
		t.Fatalf("urgent request blocked by deferred notice: %+v %v", ready, err)
	}
	// Move only this fixture's deadline to the past; the source and delivery ID
	// remain stable, so readiness exposes the original notice after deferral.
	if _, err = sender.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET available_at=clock_timestamp()-interval '1 second' WHERE event_id=$1`, notice.EventID); err != nil {
		t.Fatal(err)
	}
	ready, err = receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.EventID != notice.EventID {
		t.Fatalf("expired routine deferral remained hidden: %+v %v", ready, err)
	}
}

func TestNoticePolicyValidationAndImmediateDefault(t *testing.T) {
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	base := PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice",
		Ref:         RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination: EventDestination{Type: "agent", Name: receiver.channel.Principal}}
	for _, tc := range []struct {
		kind   string
		policy NoticeWakePolicyRequest
	}{
		{"request", NoticeWakePolicyRequest{Version: 1, Urgency: "routine", DeferSeconds: 60}},
		{"notice", NoticeWakePolicyRequest{Version: 2, Urgency: "routine", DeferSeconds: 60}},
		{"notice", NoticeWakePolicyRequest{Version: 1, Urgency: "routine", DeferSeconds: 61}},
		{"notice", NoticeWakePolicyRequest{Version: 1, Urgency: "urgent", DeferSeconds: 1}},
		{"notice", NoticeWakePolicyRequest{Version: 1, Urgency: "unspecified", DeferSeconds: 0}},
	} {
		bad := base
		bad.RequestID = uuid.NewString()
		bad.Kind = tc.kind
		bad.NoticePolicy = &tc.policy
		_, err := sender.PublishEvent(ctx, bad, dest)
		requireCode(t, err, "INVALID_REQUEST")
	}
	ordinary, err := sender.PublishEvent(ctx, base, dest)
	if err != nil || ordinary.NoticePolicy != nil {
		t.Fatalf("default publication changed: %+v %v", ordinary, err)
	}
	status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: ordinary.EventID}, dest)
	if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].AvailableAt != nil {
		t.Fatalf("default notice should be immediate: %+v %v", status, err)
	}
}

func TestRoutineTopicFanoutSharesOneFixedAvailability(t *testing.T) {
	ctx := context.Background()
	sender, first, source, dest := eventFixture(t)
	second := testStore(t, Channel{Principal: "second-" + uuid.NewString(), Repo: source.Scope.Repo})
	for _, subscriber := range []*Store{first, second} {
		if _, err := subscriber.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(),
			Topic: "routine-status", Active: true}); err != nil {
			t.Fatal(err)
		}
	}
	event, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(),
		Kind: "notice", Ref: RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination:  EventDestination{Type: "topic", Name: "routine-status"},
		NoticePolicy: &NoticeWakePolicyRequest{Version: 1, Urgency: "routine", DeferSeconds: 60}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
	if err != nil || len(status.Deliveries) != 2 {
		t.Fatalf("topic fanout: %+v %v", status, err)
	}
	for _, delivery := range status.Deliveries {
		if delivery.AvailableAt == nil || !delivery.AvailableAt.Equal(event.CreatedAt.Add(60*time.Second)) {
			t.Fatalf("fanout availability drifted: %+v", delivery)
		}
	}
	for _, subscriber := range []*Store{first, second} {
		next, err := subscriber.NextEvent(ctx, NextEventRequest{}, dest)
		if err != nil || next.Delivery != nil {
			t.Fatalf("routine topic notice claimed early: %+v %v", next, err)
		}
	}
}
