package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func panelBoundarySession(t *testing.T, repo, state string) (*Store, AgentInstance, Destination) {
	t.Helper()
	ctx := context.Background()
	profile := testStore(t, Channel{Principal: "agent:panel-boundary-worker-" + uuid.NewString(), Repo: repo})
	dest := Destination{Name: "hosted", AllowLocal: false}
	binding := "panel-boundary-" + uuid.NewString()
	session, err := profile.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: binding,
		NativeSessionID: binding, Metadata: AgentMetadata{Harness: "codex", Project: "cairn",
			Workspace: "/fixture/cairn", State: state, DeliveryMode: "existing-session"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	return profile, session, dest
}

func panelBoundaryPublish(t *testing.T, fixture panelNativeEventFixture, sender *Store, inbox string, event panelNativeEventSpec, dest Destination) AgentEvent {
	t.Helper()
	ref := fixture.createEventSource(t, sender, event)
	published, err := sender.PublishEvent(context.Background(), PublishEventRequest{RequestID: uuid.NewString(),
		Kind: event.Kind, Ref: ref, Destination: EventDestination{Type: "agent", Name: inbox}}, dest)
	if err != nil || published.Ref != ref || published.Kind != "notice" {
		t.Fatalf("publish %s: %+v %v", event.ID, published, err)
	}
	return published
}

func panelBoundaryStatus(t *testing.T, sender *Store, event AgentEvent, state string, attempts int, dest Destination) AgentDelivery {
	t.Helper()
	status, err := sender.AgentEventStatus(context.Background(), EventStatusRequest{EventID: event.EventID}, dest)
	if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].State != state ||
		status.Deliveries[0].Attempts != attempts || status.Deliveries[0].Event.EventID != event.EventID {
		t.Fatalf("%s durable state: %+v %v", event.EventID, status, err)
	}
	return status.Deliveries[0]
}

func panelBoundaryClaim(t *testing.T, profile *Store, session AgentSessionRef, delivery AgentDelivery, dest Destination) (SessionInboxClaim, SessionInboxAttempt) {
	t.Helper()
	ctx := context.Background()
	ready, err := profile.SessionInboxReady(ctx, session, dest)
	if err != nil || ready.EventID != delivery.Event.EventID || ready.DeliveryID != delivery.DeliveryID {
		t.Fatalf("exact native readiness: %+v %v", ready, err)
	}
	request := SessionInboxClaim{RequestID: uuid.NewString(), Session: session, DeliveryID: ready.DeliveryID,
		NativeTurnID: "turn-" + uuid.NewString(), TurnExclusive: true}
	claimed, err := profile.ClaimSessionInbox(ctx, request, dest)
	if err != nil || claimed.Attempt == nil || claimed.Attempt.Delivery.Event.EventID != delivery.Event.EventID ||
		claimed.Attempt.Delivery.DeliveryID != delivery.DeliveryID ||
		claimed.Attempt.NativeTurnID != request.NativeTurnID || claimed.Attempt.Delivery.Attempts != delivery.Attempts+1 {
		t.Fatalf("exact native claim: %+v %v", claimed, err)
	}
	return request, *claimed.Attempt
}

func panelBoundarySettle(t *testing.T, profile *Store, session AgentSessionRef, attempt SessionInboxAttempt, wantBody string, dest Destination) {
	t.Helper()
	ctx := context.Background()
	view, err := profile.ForAgentSession(session, dest)
	if err != nil {
		t.Fatal(err)
	}
	history, err := view.History(ctx, RecordHistoryRequest{RecordID: attempt.Delivery.Event.Ref.RecordID,
		Version: attempt.Delivery.Event.Ref.Version}, dest)
	if err != nil || len(history.Versions) != 1 || history.Versions[0].Body == nil ||
		*history.Versions[0].Body != wantBody {
		t.Fatalf("exact notice source: %+v %v", history, err)
	}
	completed, err := view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(),
		DeliveryID: attempt.Delivery.DeliveryID, LeaseID: attempt.Delivery.LeaseID, Disposition: "handled"}, dest)
	if err != nil || completed.State != "handled" || completed.CompletedAt == nil {
		t.Fatalf("notice completion: %+v %v", completed, err)
	}
	closed, err := profile.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(),
		Session: session, AttemptID: attempt.ID, Reason: "delivery_completed"}, dest)
	if err != nil || closed.FinishedAt == nil || closed.Reason != "delivery_completed" {
		t.Fatalf("native attempt reconciliation: %+v %v", closed, err)
	}
}

// This Store probe starts from reported busy presence. It does not execute the
// fixture's parser review, and a direct ClaimSessionInbox call is not busy-gated.
func TestTokenEfficiencyPanelBusyOwnerSchedulingProbe(t *testing.T) {
	ctx := context.Background()
	fixture := readPanelNativeEventFixture(t)
	row := fixture.eventCase(t, "busy_owner_task")
	transitions := panelReplyTransitions(t, row.ID)
	if len(row.Setup.Events) != 1 || row.Setup.Events[0] != (panelNativeEventSpec{ID: "notice_busy", Kind: "notice",
		Recipient: "worker", Body: "Index pass finished; no action required."}) ||
		len(transitions) != 1 || transitions[0] != (panelReplyTransition{Phase: "before_delivery", Action: "start_task", Target: "parser_review"}) {
		t.Fatal("busy owner fixture changed")
	}
	repo := fixture.EventSourceDefaults.Scope.Repo
	sender := testStore(t, Channel{Principal: "agent:panel-busy-sender-" + uuid.NewString(), Repo: repo})
	profile, session, dest := panelBoundarySession(t, repo, "busy")
	ref := AgentSessionRef{AgentID: session.AgentID, ExecutionID: session.ExecutionID}
	event := panelBoundaryPublish(t, fixture, sender, session.Inbox, row.Setup.Events[0], dest)
	pending := panelBoundaryStatus(t, sender, event, "pending", 0, dest)
	ready, err := profile.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready != (SessionInboxReadiness{}) {
		t.Fatalf("busy presence exposed readiness: %+v %v", ready, err)
	}
	// A native hook waits for an eligible turn boundary before it calls claim.
	// No task execution or parser conclusion is inferred from this store state.
	pending = panelBoundaryStatus(t, sender, event, "pending", 0, dest)
	metadata := session.Metadata
	metadata.State = "idle"
	idle, err := profile.UpdateAgent(ctx, UpdateAgentRequest{RequestID: uuid.NewString(), Session: ref,
		ExpectedRevision: session.ContextRevision, Metadata: metadata}, dest)
	if err != nil || idle.ExecutionID != ref.ExecutionID || idle.Metadata.State != "idle" {
		t.Fatalf("same execution reported idle: %+v %v", idle, err)
	}
	_, attempt := panelBoundaryClaim(t, profile, ref, pending, dest)
	panelBoundarySettle(t, profile, ref, attempt, row.Setup.Events[0].Body, dest)
	panelBoundaryStatus(t, sender, event, "handled", 1, dest)
}

// Fresh session views below model loss of local context without rotating the
// registered execution. They do not invoke a real harness PreCompact/SessionStart.
func TestTokenEfficiencyPanelCompactionStoreBoundaryProbe(t *testing.T) {
	ctx := context.Background()
	fixture := readPanelNativeEventFixture(t)
	row := fixture.eventCase(t, "compaction_before_after_settlement")
	transitions := panelReplyTransitions(t, row.ID)
	if len(row.Setup.Events) != 2 || row.Setup.Events[0] != (panelNativeEventSpec{ID: "notice_before", Kind: "notice",
		Recipient: "worker", Body: "Pre-compaction cache check complete."}) ||
		row.Setup.Events[1] != (panelNativeEventSpec{ID: "notice_after", Kind: "notice",
			Recipient: "worker", Body: "Post-compaction cache check complete."}) ||
		len(transitions) != 2 || transitions[0] != (panelReplyTransition{Phase: "after_first_delivery", Action: "compact_context", Target: "notice_before"}) ||
		transitions[1] != (panelReplyTransition{Phase: "after_first_settlement", Action: "compact_context", Target: "notice_after"}) {
		t.Fatal("compaction fixture changed")
	}
	repo := fixture.EventSourceDefaults.Scope.Repo
	sender := testStore(t, Channel{Principal: "agent:panel-compaction-sender-" + uuid.NewString(), Repo: repo})
	profile, session, dest := panelBoundarySession(t, repo, "idle")
	ref := AgentSessionRef{AgentID: session.AgentID, ExecutionID: session.ExecutionID}
	before := panelBoundaryPublish(t, fixture, sender, session.Inbox, row.Setup.Events[0], dest)
	after := panelBoundaryPublish(t, fixture, sender, session.Inbox, row.Setup.Events[1], dest)
	firstPending := panelBoundaryStatus(t, sender, before, "pending", 0, dest)
	panelBoundaryStatus(t, sender, after, "pending", 0, dest)
	claim, first := panelBoundaryClaim(t, profile, ref, firstPending, dest)
	panelBoundaryStatus(t, sender, before, "leased", 1, dest)
	panelBoundaryStatus(t, sender, after, "pending", 0, dest)

	// Simulated first context boundary: a new client view of the same execution
	// sees the persisted lease and the same pinned claim response.
	profile = testStore(t, profile.channel)
	replayed, err := profile.ClaimSessionInbox(ctx, claim, dest)
	if err != nil || replayed.Attempt == nil || replayed.Attempt.ID != first.ID ||
		replayed.Attempt.Delivery.LeaseID != first.Delivery.LeaseID {
		t.Fatalf("pending notice across view boundary: %+v %v", replayed, err)
	}
	panelBoundarySettle(t, profile, ref, first, row.Setup.Events[0].Body, dest)
	panelBoundaryStatus(t, sender, before, "handled", 1, dest)
	secondPending := panelBoundaryStatus(t, sender, after, "pending", 0, dest)

	// Simulated second boundary follows settlement, with the same execution.
	profile = testStore(t, profile.channel)
	ready, err := profile.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.EventID != after.EventID || ready.DeliveryID != secondPending.DeliveryID {
		t.Fatalf("settled notice replayed or pending notice lost: %+v %v", ready, err)
	}
	_, second := panelBoundaryClaim(t, profile, ref, secondPending, dest)
	panelBoundarySettle(t, profile, ref, second, row.Setup.Events[1].Body, dest)
	panelBoundaryStatus(t, sender, before, "handled", 1, dest)
	panelBoundaryStatus(t, sender, after, "handled", 1, dest)
	ready, err = profile.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready != (SessionInboxReadiness{}) {
		t.Fatalf("settled notices became ready again: %+v %v", ready, err)
	}
}
