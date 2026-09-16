package core

import (
	"context"
	"sync"
	"testing"

	"github.com/google/uuid"
)

func TestSessionInboxRetainsOneOwnerUntilExplicitCompletion(t *testing.T) {
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	registration := RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "codex-one", NativeSessionID: "native-inbox", Metadata: AgentMetadata{Harness: "codex", Project: "rhumb", Workspace: "/work/rhumb", State: "busy", DeliveryMode: "existing-session"}}
	agent, err := receiver.RegisterAgent(ctx, registration, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{agent.AgentID, agent.ExecutionID}
	view, err := receiver.ForAgentSession(ref, dest)
	if err != nil {
		t.Fatal(err)
	}
	emptyRequest := SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}
	empty, err := receiver.ClaimSessionInbox(ctx, emptyRequest, dest)
	if err != nil || empty.Attempt != nil {
		t.Fatalf("empty: %+v %v", empty, err)
	}
	for _, kind := range []string{"request", "response"} {
		_, err = sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: kind, Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "agent", Name: agent.Inbox}}, dest)
		if err != nil {
			t.Fatal(err)
		}
	}
	empty, err = receiver.ClaimSessionInbox(ctx, emptyRequest, dest)
	if err != nil || empty.Attempt != nil {
		t.Fatalf("lost empty response claimed a later arrival: %+v %v", empty, err)
	}
	claim := SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}
	first, err := receiver.ClaimSessionInbox(ctx, claim, dest)
	if err != nil || first.Attempt == nil || first.Attempt.Delivery.Event.Kind != "request" {
		t.Fatalf("claim: %+v %v", first, err)
	}
	retry, err := receiver.ClaimSessionInbox(ctx, claim, dest)
	if err != nil || retry.Attempt.ID != first.Attempt.ID || retry.Attempt.Delivery.LeaseID != first.Attempt.Delivery.LeaseID {
		t.Fatalf("retry: %+v %v", retry, err)
	}
	busy, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}, dest)
	if err != nil || busy.Attempt != nil {
		t.Fatalf("second owner: %+v %v", busy, err)
	}
	manual, err := view.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil || manual.Delivery != nil {
		t.Fatalf("manual consumer bypassed native hold: %+v %v", manual, err)
	}
	_, err = view.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString()}, dest)
	requireCode(t, err, "INVALID_REQUEST")
	registration.RequestID = uuid.NewString()
	_, err = receiver.RegisterAgent(ctx, registration, dest)
	requireCode(t, err, "AGENT_BUSY")
	d := first.Attempt.Delivery
	_, err = view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: d.DeliveryID, LeaseID: d.LeaseID, Disposition: "handled"}, dest)
	if err != nil {
		t.Fatal(err)
	}
	_, err = receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: first.Attempt.ID, Reason: "delivery_completed"}, dest)
	if err != nil {
		t.Fatal(err)
	}
	next, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}, dest)
	if err != nil || next.Attempt == nil || next.Attempt.Delivery.Event.Kind != "response" {
		t.Fatalf("response not delivered: %+v %v", next, err)
	}
}

func TestSessionInboxCrashRestoreAndOldReconciliation(t *testing.T) {
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	registration := RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "claude-two", NativeSessionID: "native-crash", Metadata: AgentMetadata{Harness: "claude", Project: "rhumb", Workspace: "/work/rhumb", State: "busy", DeliveryMode: "existing-session"}}
	agent, err := receiver.RegisterAgent(ctx, registration, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{agent.AgentID, agent.ExecutionID}
	view, err := receiver.ForAgentSession(ref, dest)
	if err != nil {
		t.Fatal(err)
	}
	publishFixture(t, sender, view, source, dest)
	claim := SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}
	first, err := receiver.ClaimSessionInbox(ctx, claim, dest)
	if err != nil || first.Attempt == nil {
		t.Fatalf("claim: %+v %v", first, err)
	}
	reconcile := SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: first.Attempt.ID, Reason: "delivery_completed"}
	_, err = receiver.ReconcileSessionInbox(ctx, reconcile, dest)
	requireCode(t, err, "DELIVERY_ACTIVE")
	_, err = sender.ReconcileSessionInbox(ctx, reconcile, dest)
	requireCode(t, err, "NOT_FOUND")
	if _, err = receiver.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET lease_until=clock_timestamp()-interval '1 second' WHERE delivery_id=$1`, first.Attempt.Delivery.DeliveryID); err != nil {
		t.Fatal(err)
	}
	next, err := view.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil || next.Delivery != nil {
		t.Fatalf("expired native lease replayed: %+v %v", next, err)
	}
	operator := testStore(t, Channel{Principal: "native-restore-operator", Operator: true})
	if _, err = operator.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "fence disposable native inbox test"}); err != nil {
		t.Fatal(err)
	}
	_, err = receiver.ClaimSessionInbox(ctx, claim, dest)
	requireCode(t, err, "STALE_SESSION")
	registration.RequestID = uuid.NewString()
	_, err = receiver.RegisterAgent(ctx, registration, dest)
	requireCode(t, err, "AGENT_BUSY")
	reconcile.RequestID, reconcile.Reason = uuid.NewString(), "process_exited"
	ended, err := receiver.ReconcileSessionInbox(ctx, reconcile, dest)
	if err != nil || ended.FinishedAt == nil || ended.Delivery.State != "failed" {
		t.Fatalf("crash reconciliation: %+v %v", ended, err)
	}
	resumed, err := receiver.RegisterAgent(ctx, registration, dest)
	if err != nil || resumed.AgentID != agent.AgentID || resumed.ExecutionID == agent.ExecutionID {
		t.Fatalf("resume: %+v %v", resumed, err)
	}
	current := AgentSessionRef{resumed.AgentID, resumed.ExecutionID}
	fresh, err := receiver.ForAgentSession(current, dest)
	if err != nil {
		t.Fatal(err)
	}
	publishFixture(t, sender, fresh, source, dest)
	replacement, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: current}, dest)
	if err != nil || replacement.Attempt == nil {
		t.Fatalf("replacement: %+v %v", replacement, err)
	}
	reconcile.RequestID = uuid.NewString()
	if _, err = receiver.ReconcileSessionInbox(ctx, reconcile, dest); err != nil {
		t.Fatal(err)
	}
	same, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: replacement.Attempt.ID, Session: current}, dest)
	if err != nil || same.Attempt.FinishedAt != nil || same.Attempt.Delivery.State != "leased" {
		t.Fatalf("old close affected replacement: %+v %v", same, err)
	}
}

func TestSessionInboxConcurrentClaimHasOneConsumer(t *testing.T) {
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	agent, err := receiver.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "codex", NativeSessionID: "concurrent", Metadata: AgentMetadata{Harness: "codex", Project: "rhumb", Workspace: "/work/rhumb", State: "busy", DeliveryMode: "existing-session"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{agent.AgentID, agent.ExecutionID}
	view, err := receiver.ForAgentSession(ref, dest)
	if err != nil {
		t.Fatal(err)
	}
	publishFixture(t, sender, view, source, dest)
	publishFixture(t, sender, view, source, dest)
	var wg sync.WaitGroup
	results := make(chan SessionInboxResult, 2)
	errs := make(chan error, 2)
	for range 2 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			result, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}, dest)
			results <- result
			errs <- err
		}()
	}
	wg.Wait()
	close(results)
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	count := 0
	for result := range results {
		if result.Attempt != nil {
			count++
		}
	}
	if count != 1 {
		t.Fatalf("concurrent claimants: %d", count)
	}
}
