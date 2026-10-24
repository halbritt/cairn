package core

import (
	"context"
	"fmt"
	"strings"
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

func TestRoutineNoticePublicationRollsBackWhenDeliveryInsertFails(t *testing.T) {
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	trigger := "notice_publish_probe_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	sql := fmt.Sprintf(`CREATE FUNCTION cairn.%s() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected notice delivery failure'; END $$; CREATE TRIGGER %s BEFORE INSERT ON cairn.agent_delivery FOR EACH ROW WHEN (NEW.consumer='%s') EXECUTE FUNCTION cairn.%s()`,
		trigger, trigger, receiver.channel.Principal, trigger)
	if _, err := sender.pool.Exec(ctx, sql); err != nil {
		t.Fatal(err)
	}
	defer sender.pool.Exec(ctx, fmt.Sprintf(`DROP TRIGGER IF EXISTS %s ON cairn.agent_delivery; DROP FUNCTION IF EXISTS cairn.%s()`, trigger, trigger))
	request := PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice",
		Ref:          RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination:  EventDestination{Type: "agent", Name: receiver.channel.Principal},
		NoticePolicy: &NoticeWakePolicyRequest{Version: 1, Urgency: "routine", DeferSeconds: 60}}
	if _, err := sender.PublishEvent(ctx, request, dest); err == nil {
		t.Fatal("injected delivery insert failure succeeded")
	}
	var events, deliveries int
	if err := sender.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event WHERE publisher=$1 AND repo=$2 AND record_id=$3 AND version=$4 AND kind='notice'`,
		sender.channel.Principal, source.Scope.Repo, source.RecordID, source.Version).Scan(&events); err != nil {
		t.Fatal(err)
	}
	if err := sender.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.publisher=$1 AND e.repo=$2 AND e.record_id=$3 AND e.version=$4 AND e.kind='notice'`,
		sender.channel.Principal, source.Scope.Repo, source.RecordID, source.Version).Scan(&deliveries); err != nil {
		t.Fatal(err)
	}
	if events != 0 || deliveries != 0 {
		t.Fatalf("failed publication left partial state: events=%d deliveries=%d", events, deliveries)
	}
	if _, err := sender.pool.Exec(ctx, fmt.Sprintf(`DROP TRIGGER %s ON cairn.agent_delivery`, trigger)); err != nil {
		t.Fatal(err)
	}
	event, err := sender.PublishEvent(ctx, request, dest)
	if err != nil {
		t.Fatalf("retry after rollback: %v", err)
	}
	status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
	if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].AvailableAt == nil ||
		!status.Deliveries[0].AvailableAt.Equal(event.CreatedAt.Add(60*time.Second)) {
		t.Fatalf("retry did not commit one fixed-deadline delivery: %+v %v", status, err)
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

func TestRoutineNoticeSurvivesStoreReopenUrgentNoticeAndPublisherFlush(t *testing.T) {
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	agent, err := receiver.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(),
		Binding: "routine-reopen", NativeSessionID: "routine-reopen",
		Metadata: AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test",
			State: "idle", DeliveryMode: "existing-session"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{AgentID: agent.AgentID, ExecutionID: agent.ExecutionID}
	publication := PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice",
		Ref:          RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination:  EventDestination{Type: "agent", Name: agent.Inbox},
		NoticePolicy: &NoticeWakePolicyRequest{Version: 1, Urgency: "routine", DeferSeconds: 60}}
	routine, err := sender.PublishEvent(ctx, publication, dest)
	if err != nil {
		t.Fatal(err)
	}
	before, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: routine.EventID}, dest)
	if err != nil || len(before.Deliveries) != 1 || before.Deliveries[0].AvailableAt == nil {
		t.Fatalf("deferred notice: %+v %v", before, err)
	}
	deadline, deliveryID := *before.Deliveries[0].AvailableAt, before.Deliveries[0].DeliveryID
	senderPrincipal, receiverPrincipal := sender.channel.Principal, receiver.channel.Principal
	sender.Close()
	receiver.Close()
	sender = testStore(t, Channel{Principal: senderPrincipal, Repo: source.Scope.Repo})
	receiver = testStore(t, Channel{Principal: receiverPrincipal, Repo: source.Scope.Repo})
	if ready, err := receiver.SessionInboxReady(ctx, ref, dest); err != nil || ready.DeliveryID != "" {
		t.Fatalf("reopen woke deferred routine notice early: %+v %v", ready, err)
	}
	urgent, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice",
		Ref: publication.Ref, Destination: publication.Destination,
		NoticePolicy: &NoticeWakePolicyRequest{Version: 1, Urgency: "urgent", DeferSeconds: 0}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ready, err := receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.EventID != urgent.EventID {
		t.Fatalf("urgent notice did not interrupt deferred routine notice: %+v %v", ready, err)
	}
	claimed, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref,
		DeliveryID: ready.DeliveryID, NativeTurnID: "urgent-native-turn", TurnExclusive: true}, dest)
	if err != nil || claimed.Attempt == nil || claimed.Attempt.Delivery.Event.EventID != urgent.EventID {
		t.Fatalf("urgent notice not claimable: %+v %v", claimed, err)
	}
	view, err := receiver.ForAgentSession(ref, dest)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(),
		DeliveryID: claimed.Attempt.Delivery.DeliveryID, LeaseID: claimed.Attempt.Delivery.LeaseID, Disposition: "handled"}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(),
		Session: ref, AttemptID: claimed.Attempt.ID, Reason: "delivery_completed"}, dest); err != nil {
		t.Fatal(err)
	}
	if ready, err := receiver.SessionInboxReady(ctx, ref, dest); err != nil || ready.DeliveryID != "" {
		t.Fatalf("routine deadline slid after urgent handling: %+v %v", ready, err)
	}
	flushed, err := sender.FlushNotice(ctx, NoticeFlushRequest{RequestID: uuid.NewString(), EventID: routine.EventID}, dest)
	if err != nil || flushed.Released != 1 || flushed.FlushedBy != senderPrincipal {
		t.Fatalf("reopened publisher flush: %+v %v", flushed, err)
	}
	after, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: routine.EventID}, dest)
	if err != nil || len(after.Deliveries) != 1 || after.Event.Ref != routine.Ref ||
		after.Event.NoticePolicy == nil || after.Event.NoticePolicy.Owner != senderPrincipal ||
		after.Event.NoticePolicy.Version != 1 || after.Deliveries[0].DeliveryID != deliveryID ||
		after.Deliveries[0].AvailableAt == nil || !after.Deliveries[0].AvailableAt.Equal(flushed.FlushedAt) ||
		!flushed.FlushedAt.Before(deadline) {
		t.Fatalf("flush changed durable notice identity or deadline: before=%+v after=%+v err=%v", before, after, err)
	}
	ready, err = receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.EventID != routine.EventID || ready.DeliveryID != deliveryID {
		t.Fatalf("flushed original notice was not ready: %+v %v", ready, err)
	}
	claimed, err = receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref,
		DeliveryID: deliveryID, NativeTurnID: "routine-native-turn", TurnExclusive: true}, dest)
	if err != nil || claimed.Attempt == nil || claimed.Attempt.Delivery.DeliveryID != deliveryID || claimed.Attempt.Delivery.Attempts != 1 {
		t.Fatalf("flushed original notice was lost or duplicated: %+v %v", claimed, err)
	}
}
