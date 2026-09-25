package core

import (
	"context"
	"sync"
	"testing"

	"github.com/google/uuid"
)

func TestNativeWakeClaimBindsDeliveryAndTurnAcrossReadinessRace(t *testing.T) {
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	a, err := receiver.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "native-queue", NativeSessionID: "native-queue", Metadata: AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{a.AgentID, a.ExecutionID}
	var deliveries []string
	for range 2 {
		event, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "agent", Name: a.Inbox}}, dest)
		if err != nil {
			t.Fatal(err)
		}
		status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
		if err != nil {
			t.Fatal(err)
		}
		deliveries = append(deliveries, status.Deliveries[0].DeliveryID)
	}
	ready, err := receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.DeliveryID != deliveries[0] {
		t.Fatalf("readiness: %+v %v", ready, err)
	}
	// The queued wake arrives after its hinted delivery becomes unavailable.
	if _, err = receiver.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET available_at=clock_timestamp()+interval '1 hour' WHERE delivery_id=$1`, deliveries[0]); err != nil {
		t.Fatal(err)
	}
	claim := SessionInboxClaim{RequestID: uuid.NewString(), Session: ref, DeliveryID: deliveries[0], NativeTurnID: "native-turn-one"}
	empty, err := receiver.ClaimSessionInbox(ctx, claim, dest)
	if err != nil || empty.Attempt != nil {
		t.Fatalf("old wake claimed unrelated work: %+v %v", empty, err)
	}
	changed := claim
	changed.DeliveryID = deliveries[1]
	_, err = receiver.ClaimSessionInbox(ctx, changed, dest)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	changed = claim
	changed.NativeTurnID = "native-turn-two"
	_, err = receiver.ClaimSessionInbox(ctx, changed, dest)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	changed = claim
	changed.TurnExclusive = true
	_, err = receiver.ClaimSessionInbox(ctx, changed, dest)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	empty, err = receiver.ClaimSessionInbox(ctx, claim, dest)
	if err != nil || empty.Attempt != nil {
		t.Fatalf("lost empty reply changed ownership: %+v %v", empty, err)
	}
	claim.RequestID, claim.DeliveryID, claim.NativeTurnID, claim.TurnExclusive = uuid.NewString(), deliveries[1], "native-turn-two", true
	got, err := receiver.ClaimSessionInbox(ctx, claim, dest)
	if err != nil || got.Attempt == nil || got.Attempt.NativeTurnID != claim.NativeTurnID || got.Attempt.Delivery.DeliveryID != deliveries[1] {
		t.Fatalf("bound claim: %+v %v", got, err)
	}
	again, err := receiver.ClaimSessionInbox(ctx, claim, dest)
	if err != nil || again.Attempt == nil || again.Attempt.NativeTurnID != claim.NativeTurnID || again.Attempt.ID != got.Attempt.ID {
		t.Fatalf("bound retry: %+v %v", again, err)
	}
	changed = claim
	changed.TurnExclusive = false
	_, err = receiver.ClaimSessionInbox(ctx, changed, dest)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	claim.RequestID, claim.DeliveryID = uuid.NewString(), ""
	_, err = receiver.ClaimSessionInbox(ctx, claim, dest)
	requireCode(t, err, "INVALID_REQUEST")
}

func TestSessionInboxReadinessDoesNotClaimAndRespectsOwners(t *testing.T) {
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	a, err := receiver.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "idle-probe", NativeSessionID: "idle-probe", Metadata: AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{a.AgentID, a.ExecutionID}
	ready, err := receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.DeliveryID != "" {
		t.Fatalf("empty readiness: %+v %v", ready, err)
	}
	event, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "agent", Name: a.Inbox}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ready, err = receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.DeliveryID == "" {
		t.Fatalf("pending readiness: %+v %v", ready, err)
	}
	again, err := receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || again != ready {
		t.Fatalf("read changed readiness: %+v %v", again, err)
	}
	status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
	if err != nil || status.Deliveries[0].State != "pending" || status.Deliveries[0].Attempts != 0 {
		t.Fatalf("read claimed work: %+v %v", status, err)
	}
	_, err = sender.SessionInboxReady(ctx, ref, dest)
	requireCode(t, err, "NOT_FOUND")
	claimed, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}, dest)
	if err != nil || claimed.Attempt == nil {
		t.Fatalf("claim: %+v %v", claimed, err)
	}
	ready, err = receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.DeliveryID != "" {
		t.Fatalf("held readiness: %+v %v", ready, err)
	}
}

func TestSessionInboxReadinessRequiresCurrentIdlePresence(t *testing.T) {
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	registration := RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "idle-state", NativeSessionID: "idle-state", Metadata: AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test", State: "busy", DeliveryMode: "existing-session"}}
	a, err := receiver.RegisterAgent(ctx, registration, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{a.AgentID, a.ExecutionID}
	_, err = sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "agent", Name: a.Inbox}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ready, err := receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.DeliveryID != "" {
		t.Fatalf("busy: %+v %v", ready, err)
	}
	metadata := a.Metadata
	metadata.State = "idle"
	a, err = receiver.UpdateAgent(ctx, UpdateAgentRequest{RequestID: uuid.NewString(), Session: ref, ExpectedRevision: a.ContextRevision, Metadata: metadata}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ready, err = receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.DeliveryID == "" {
		t.Fatalf("idle notice: %+v %v", ready, err)
	}
	if _, err = receiver.pool.Exec(ctx, `UPDATE cairn.agent_session SET expires_at=clock_timestamp()-interval '1 second' WHERE agent_id=$1`, a.AgentID); err != nil {
		t.Fatal(err)
	}
	ready, err = receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.DeliveryID != "" {
		t.Fatalf("expired: %+v %v", ready, err)
	}
	registration.RequestID = uuid.NewString()
	if _, err = receiver.RegisterAgent(ctx, registration, dest); err != nil {
		t.Fatal(err)
	}
	_, err = receiver.SessionInboxReady(ctx, ref, dest)
	requireCode(t, err, "STALE_SESSION")
}

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
	conflicting := reconcile
	conflicting.RequestID, conflicting.Reason = uuid.NewString(), "delivery_completed"
	_, err = receiver.ReconcileSessionInbox(ctx, conflicting, dest)
	requireCode(t, err, "VERSION_CONFLICT")
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
