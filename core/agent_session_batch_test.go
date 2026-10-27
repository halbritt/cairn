package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func batchFixture(t *testing.T) (*Store, *Store, *Store, AgentInstance, RecordVersionRef, Destination) {
	t.Helper()
	f := newNativeFixture(t)
	sender, receiver, source, dest := f.sender, f.receiver, f.source, f.dest
	a := f.register(nativeAgent{Binding: "batch-notices", Native: "batch-turn", State: "busy"})
	ref := sessionRef(a)
	view, err := receiver.ForAgentSession(ref, dest)
	if err != nil {
		t.Fatal(err)
	}
	return sender, receiver, view, a, RecordVersionRef{RecordID: source.RecordID, Version: source.Version}, dest
}

func publishBatchEvent(t *testing.T, sender *Store, agent AgentInstance, source RecordVersionRef, dest Destination, kind string) AgentEvent {
	t.Helper()
	event, err := sender.PublishEvent(context.Background(), PublishEventRequest{RequestID: uuid.NewString(), Kind: kind, Ref: source, Destination: EventDestination{Type: "agent", Name: agent.Inbox}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	return event
}

func settleBatchItem(t *testing.T, view *Store, attempt *SessionInboxAttempt, dest Destination, disposition string) {
	t.Helper()
	code := ""
	if disposition != "handled" {
		code = "processing_failed"
	}
	if _, err := view.CompleteEvent(context.Background(), CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: attempt.Delivery.DeliveryID, LeaseID: attempt.Delivery.LeaseID, Disposition: disposition, Code: code}, dest); err != nil {
		t.Fatal(err)
	}
}

func TestNativeNoticeBatchAtomicTransferAndPerItemOutcomes(t *testing.T) {
	ctx := context.Background()
	sender, receiver, view, agent, source, dest := batchFixture(t)
	ref := AgentSessionRef{AgentID: agent.AgentID, ExecutionID: agent.ExecutionID}
	firstEvent := publishBatchEvent(t, sender, agent, source, dest, "notice")
	secondEvent := publishBatchEvent(t, sender, agent, source, dest, "notice")
	thirdEvent := publishBatchEvent(t, sender, agent, source, dest, "notice")
	claim, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref, NativeTurnID: "batch-native-turn", DeliveryID: deliveryForEvent(t, sender, firstEvent, dest)}, dest)
	if err != nil || claim.Attempt == nil {
		t.Fatalf("first claim: %+v %v", claim, err)
	}
	first := claim.Attempt
	if first.Delivery.Event.EventID != firstEvent.EventID {
		t.Fatalf("wrong first delivery: %+v", first.Delivery)
	}
	_, err = receiver.AdvanceSessionInbox(ctx, SessionInboxAdvance{RequestID: uuid.NewString(), Session: ref, AttemptID: first.ID, Version: 1}, dest)
	requireCode(t, err, "DELIVERY_ACTIVE")
	settleBatchItem(t, view, first, dest, "handled")
	advance := SessionInboxAdvance{RequestID: uuid.NewString(), Session: ref, AttemptID: first.ID, Version: 1}
	second, err := receiver.AdvanceSessionInbox(ctx, advance, dest)
	if err != nil || second.Attempt == nil || second.Attempt.Delivery.Event.EventID != secondEvent.EventID || second.Attempt.BatchIndex != 2 || second.Attempt.BatchRoot != first.ID {
		t.Fatalf("second item: %+v %v", second, err)
	}
	// A lost response repeats the same durable item, not another claim.
	retry, err := receiver.AdvanceSessionInbox(ctx, advance, dest)
	if err != nil || retry.Attempt == nil || retry.Attempt.ID != second.Attempt.ID {
		t.Fatalf("advance retry: %+v %v", retry, err)
	}
	manual, err := view.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil || manual.Delivery != nil {
		t.Fatalf("manual claim escaped active batch hold: %+v %v", manual, err)
	}
	fresh, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref, NativeTurnID: "another-native-turn", DeliveryID: deliveryForEvent(t, sender, thirdEvent, dest)}, dest)
	if err != nil || fresh.Attempt != nil {
		t.Fatalf("fresh native turn escaped active batch hold: %+v %v", fresh, err)
	}
	settleBatchItem(t, view, second.Attempt, dest, "failed")
	third, err := receiver.AdvanceSessionInbox(ctx, SessionInboxAdvance{RequestID: uuid.NewString(), Session: ref, AttemptID: second.Attempt.ID, Version: 1}, dest)
	if err != nil || third.Attempt == nil || third.Attempt.Delivery.Event.EventID != thirdEvent.EventID || third.Attempt.BatchIndex != 3 {
		t.Fatalf("third item: %+v %v", third, err)
	}
	for _, attempt := range []*SessionInboxAttempt{first, second.Attempt, third.Attempt} {
		if attempt.Delivery.Event.Ref != source {
			t.Fatalf("source identity changed for %s: %+v", attempt.ID, attempt.Delivery.Event.Ref)
		}
	}
	if first.Delivery.LeaseID == second.Attempt.Delivery.LeaseID || second.Attempt.Delivery.LeaseID == third.Attempt.Delivery.LeaseID {
		t.Fatal("batch items shared a lease")
	}
	settleBatchItem(t, view, third.Attempt, dest, "ignored")
	limit, err := receiver.AdvanceSessionInbox(ctx, SessionInboxAdvance{RequestID: uuid.NewString(), Session: ref, AttemptID: third.Attempt.ID, Version: 1}, dest)
	if err != nil || limit.Attempt != nil || limit.Remainder != "batch_limit" {
		t.Fatalf("batch limit: %+v %v", limit, err)
	}
	for _, item := range []struct {
		event AgentEvent
		state string
	}{{firstEvent, "handled"}, {secondEvent, "failed"}, {thirdEvent, "ignored"}} {
		status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: item.event.EventID}, dest)
		if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].State != item.state || status.Deliveries[0].Attempts != 1 {
			t.Fatalf("per-item settlement: %+v %v", status, err)
		}
	}
	closed, err := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: third.Attempt.ID, Reason: "delivery_completed"}, dest)
	if err != nil || closed.FinishedAt == nil {
		t.Fatalf("final hold: %+v %v", closed, err)
	}
}

func TestNativeNoticeBatchStopsAtOlderRequest(t *testing.T) {
	ctx := context.Background()
	sender, receiver, view, agent, source, dest := batchFixture(t)
	ref := AgentSessionRef{AgentID: agent.AgentID, ExecutionID: agent.ExecutionID}
	first := publishBatchEvent(t, sender, agent, source, dest, "notice")
	request := publishBatchEvent(t, sender, agent, source, dest, "request")
	publishBatchEvent(t, sender, agent, source, dest, "notice")
	claim, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref, NativeTurnID: "batch-native-turn", DeliveryID: deliveryForEvent(t, sender, first, dest)}, dest)
	if err != nil || claim.Attempt == nil {
		t.Fatalf("claim: %+v %v", claim, err)
	}
	settleBatchItem(t, view, claim.Attempt, dest, "handled")
	result, err := receiver.AdvanceSessionInbox(ctx, SessionInboxAdvance{RequestID: uuid.NewString(), Session: ref, AttemptID: claim.Attempt.ID, Version: 1}, dest)
	if err != nil || result.Attempt != nil || result.Remainder != "priority_waiting" {
		t.Fatalf("request fairness: %+v %v", result, err)
	}
	status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: request.EventID}, dest)
	if err != nil || status.Deliveries[0].State != "pending" {
		t.Fatalf("request changed: %+v %v", status, err)
	}
}

func TestNativeNoticeBatchStopsForNewUrgentNotice(t *testing.T) {
	ctx := context.Background()
	sender, receiver, view, agent, source, dest := batchFixture(t)
	ref := AgentSessionRef{AgentID: agent.AgentID, ExecutionID: agent.ExecutionID}
	first := publishBatchEvent(t, sender, agent, source, dest, "notice")
	olderRoutine := publishBatchEvent(t, sender, agent, source, dest, "notice")
	claim, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref, NativeTurnID: "batch-native-turn", DeliveryID: deliveryForEvent(t, sender, first, dest)}, dest)
	if err != nil || claim.Attempt == nil {
		t.Fatalf("claim: %+v %v", claim, err)
	}
	urgent, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: source,
		Destination:  EventDestination{Type: "agent", Name: agent.Inbox},
		NoticePolicy: &NoticeWakePolicyRequest{Version: 1, Urgency: "urgent", DeferSeconds: 0}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	settleBatchItem(t, view, claim.Attempt, dest, "handled")
	result, err := receiver.AdvanceSessionInbox(ctx, SessionInboxAdvance{RequestID: uuid.NewString(), Session: ref, AttemptID: claim.Attempt.ID, Version: 1}, dest)
	if err != nil || result.Attempt != nil || result.Remainder != "priority_waiting" {
		t.Fatalf("urgent arrival was delayed by batch: %+v %v", result, err)
	}
	for _, event := range []AgentEvent{olderRoutine, urgent} {
		status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
		if err != nil || status.Deliveries[0].State != "pending" {
			t.Fatalf("unsettled notice changed: %+v %v", status, err)
		}
	}
}

func TestNativeNoticeBatchDoesNotAbsorbLaterArrival(t *testing.T) {
	ctx := context.Background()
	sender, receiver, view, agent, source, dest := batchFixture(t)
	ref := AgentSessionRef{AgentID: agent.AgentID, ExecutionID: agent.ExecutionID}
	first := publishBatchEvent(t, sender, agent, source, dest, "notice")
	claim, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref, NativeTurnID: "batch-native-turn", DeliveryID: deliveryForEvent(t, sender, first, dest)}, dest)
	if err != nil || claim.Attempt == nil {
		t.Fatalf("claim: %+v %v", claim, err)
	}
	later := publishBatchEvent(t, sender, agent, source, dest, "notice")
	settleBatchItem(t, view, claim.Attempt, dest, "handled")
	result, err := receiver.AdvanceSessionInbox(ctx, SessionInboxAdvance{RequestID: uuid.NewString(), Session: ref, AttemptID: claim.Attempt.ID, Version: 1}, dest)
	if err != nil || result.Attempt != nil || result.Remainder != "empty" {
		t.Fatalf("late routine notice entered batch: %+v %v", result, err)
	}
	again, err := receiver.AdvanceSessionInbox(ctx, SessionInboxAdvance{RequestID: uuid.NewString(), Session: ref, AttemptID: claim.Attempt.ID, Version: 1}, dest)
	if err != nil || again.Attempt != nil || again.Remainder != "batch_ended" {
		t.Fatalf("closed batch reopened: %+v %v", again, err)
	}
	status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: later.EventID}, dest)
	if err != nil || status.Deliveries[0].State != "pending" {
		t.Fatalf("later delivery changed: %+v %v", status, err)
	}
}

func TestNativeNoticeBatchExpiredLeaseNeedsItsOwnLateSettlement(t *testing.T) {
	ctx := context.Background()
	sender, receiver, view, agent, source, dest := batchFixture(t)
	ref := AgentSessionRef{AgentID: agent.AgentID, ExecutionID: agent.ExecutionID}
	first := publishBatchEvent(t, sender, agent, source, dest, "notice")
	second := publishBatchEvent(t, sender, agent, source, dest, "notice")
	claim, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref, NativeTurnID: "batch-native-turn", DeliveryID: deliveryForEvent(t, sender, first, dest)}, dest)
	if err != nil || claim.Attempt == nil {
		t.Fatalf("claim: %+v %v", claim, err)
	}
	fixtureFor(t, receiver).expireLease(claim.Attempt.Delivery.DeliveryID)
	_, err = receiver.AdvanceSessionInbox(ctx, SessionInboxAdvance{RequestID: uuid.NewString(), Session: ref, AttemptID: claim.Attempt.ID, Version: 1}, dest)
	requireCode(t, err, "DELIVERY_ACTIVE")
	completed, err := view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: claim.Attempt.Delivery.DeliveryID, LeaseID: claim.Attempt.Delivery.LeaseID, Disposition: "handled"}, dest)
	if err != nil || !completed.Late || completed.State != "handled" {
		t.Fatalf("late native settlement: %+v %v", completed, err)
	}
	advanced, err := receiver.AdvanceSessionInbox(ctx, SessionInboxAdvance{RequestID: uuid.NewString(), Session: ref, AttemptID: claim.Attempt.ID, Version: 1}, dest)
	if err != nil || advanced.Attempt == nil || advanced.Attempt.Delivery.Event.EventID != second.EventID {
		t.Fatalf("next item after late settlement: %+v %v", advanced, err)
	}
	status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: second.EventID}, dest)
	if err != nil || status.Deliveries[0].State != "leased" {
		t.Fatalf("next item lost its own lease: %+v %v", status, err)
	}
}

func TestNativeNoticeBatchCrashAfterFirstHandoffRetainsOtherItems(t *testing.T) {
	ctx := context.Background()
	sender, receiver, view, agent, source, dest := batchFixture(t)
	ref := AgentSessionRef{AgentID: agent.AgentID, ExecutionID: agent.ExecutionID}
	first := publishBatchEvent(t, sender, agent, source, dest, "notice")
	second := publishBatchEvent(t, sender, agent, source, dest, "notice")
	third := publishBatchEvent(t, sender, agent, source, dest, "notice")
	claim, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref, NativeTurnID: "batch-native-turn", DeliveryID: deliveryForEvent(t, sender, first, dest)}, dest)
	if err != nil || claim.Attempt == nil {
		t.Fatalf("claim: %+v %v", claim, err)
	}
	settleBatchItem(t, view, claim.Attempt, dest, "handled")
	advanced, err := receiver.AdvanceSessionInbox(ctx, SessionInboxAdvance{RequestID: uuid.NewString(), Session: ref, AttemptID: claim.Attempt.ID, Version: 1}, dest)
	if err != nil || advanced.Attempt == nil {
		t.Fatalf("advance: %+v %v", advanced, err)
	}
	_, err = receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: advanced.Attempt.ID, Reason: "turn_ended"}, dest)
	if err != nil {
		t.Fatal(err)
	}
	for _, item := range []struct {
		event AgentEvent
		state string
	}{{first, "handled"}, {second, "failed"}, {third, "pending"}} {
		status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: item.event.EventID}, dest)
		if err != nil || status.Deliveries[0].State != item.state {
			t.Fatalf("crash changed item outcome: %+v %v", status, err)
		}
	}
}

func deliveryForEvent(t *testing.T, sender *Store, event AgentEvent, dest Destination) string {
	t.Helper()
	status, err := sender.AgentEventStatus(context.Background(), EventStatusRequest{EventID: event.EventID}, dest)
	if err != nil || len(status.Deliveries) != 1 {
		t.Fatalf("event delivery: %+v %v", status, err)
	}
	return status.Deliveries[0].DeliveryID
}
