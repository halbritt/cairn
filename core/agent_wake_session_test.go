package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func TestWakeLaunchRequiresTheHostedRunnerDestination(t *testing.T) {
	ctx := context.Background()
	host, slot, source, dest := eventFixture(t)
	local := Destination{Name: "local", AllowLocal: true}
	publishFixture(t, host, slot, source, dest)
	_, err := slot.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString()}, local)
	requireCode(t, err, "DESTINATION_PROHIBITED")
	w, err := slot.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString()}, dest)
	if err != nil {
		t.Fatal(err)
	}
	for _, op := range []string{"start", "enter", "link"} {
		_, err = slot.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: w.Attempt.ID, Operation: op}, local)
		requireCode(t, err, "DESTINATION_PROHIBITED")
	}
	// Unsupported launch destinations can still reconcile existing holds.
	_, err = slot.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: w.Attempt.ID, Operation: "finish", Reason: "profile_destination_corrected"}, local)
	if err != nil {
		t.Fatal(err)
	}
}

func TestWakeSessionKeepsNativeIdentityAndSlotDelivery(t *testing.T) {
	ctx := context.Background()
	host, slot, source, dest := eventFixture(t)
	registration := RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "codex-two", NativeSessionID: "actual-native-thread",
		Metadata: AgentMetadata{Harness: "codex", Project: "rhumb", Workspace: "/fixture/rhumb", State: "busy", DeliveryMode: "fresh-worker"}}
	agent, err := host.RegisterAgent(ctx, registration, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{agent.AgentID, agent.ExecutionID}
	request := publishFixture(t, host, slot, source, dest)
	claimed, err := slot.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString()}, dest)
	if err != nil {
		t.Fatal(err)
	}
	id := claimed.Attempt.ID
	for _, op := range []string{"start", "enter"} {
		if _, err = slot.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: id, Operation: op}, dest); err != nil {
			t.Fatal(err)
		}
	}
	attach := WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: id, Operation: "session", Session: &ref}
	w, err := slot.ChangeWake(ctx, attach, dest)
	if err != nil || w.Session == nil || *w.Session != ref || w.Delivery.Consumer != slot.channel.Principal || w.Delivery.Event.EventID != request.EventID {
		t.Fatalf("native association changed slot delivery: %+v %v", w, err)
	}
	again, err := slot.ChangeWake(ctx, attach, dest)
	if err != nil || again.Session == nil || *again.Session != ref {
		t.Fatalf("association retry: %+v %v", again, err)
	}
	view, err := host.ForAgentSession(ref, dest)
	if err != nil {
		t.Fatal(err)
	}
	_, err = view.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString()}, dest)
	requireCode(t, err, "INVALID_REQUEST")
	_, err = host.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, 1}, Destination: EventDestination{"agent", agent.Inbox}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	next, err := view.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil || next.Delivery != nil {
		t.Fatalf("fresh worker claimed second request: %+v %v", next, err)
	}
	metadata := agent.Metadata
	metadata.DeliveryMode = "existing-session"
	_, err = host.UpdateAgent(ctx, UpdateAgentRequest{RequestID: uuid.NewString(), Session: ref, ExpectedRevision: agent.ContextRevision, Metadata: metadata}, dest)
	if err != nil {
		t.Fatal(err)
	}
	native, err := host.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}, dest)
	if err != nil || native.Attempt != nil {
		t.Fatalf("metadata change bypassed active wake: %+v %v", native, err)
	}
	registration.RequestID = uuid.NewString()
	_, err = host.RegisterAgent(ctx, registration, dest)
	requireCode(t, err, "AGENT_BUSY")
	operator, _ := testOperator(t)
	if _, err = operator.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "Disposable linked wake restore"}); err != nil {
		t.Fatal(err)
	}
	registration.RequestID = uuid.NewString()
	_, err = host.RegisterAgent(ctx, registration, dest)
	requireCode(t, err, "AGENT_BUSY")
	finish := WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: id, Operation: "finish", Reason: "worker_stopped"}
	_, err = slot.ChangeWake(ctx, finish, dest)
	if err != nil {
		t.Fatal(err)
	}
	page, err := host.AgentDirectory(ctx, AgentDirectoryQuery{AgentID: agent.AgentID, IncludeOffline: true}, dest)
	if err != nil || len(page.Agents) != 1 || !page.Agents[0].Stopped {
		t.Fatalf("ended worker still present: %+v %v", page, err)
	}
	registration.RequestID = uuid.NewString()
	registration.Metadata.DeliveryMode = "existing-session"
	resumed, err := host.RegisterAgent(ctx, registration, dest)
	if err != nil || resumed.AgentID != agent.AgentID || resumed.ExecutionID == agent.ExecutionID {
		t.Fatalf("native resume lost identity: %+v %v", resumed, err)
	}
	if _, err = slot.ChangeWake(ctx, finish, dest); err != nil {
		t.Fatal(err)
	}
	page, err = host.AgentDirectory(ctx, AgentDirectoryQuery{AgentID: agent.AgentID}, dest)
	if err != nil || len(page.Agents) != 1 || page.Agents[0].ExecutionID != resumed.ExecutionID {
		t.Fatalf("old finish stopped newer execution: %+v %v", page, err)
	}
}

func TestWakeSessionRejectsStaleAndCompetingAssociations(t *testing.T) {
	ctx := context.Background()
	host, slot, source, dest := eventFixture(t)
	registration := RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "claude-two", NativeSessionID: "native-current",
		Metadata: AgentMetadata{Harness: "claude", Project: "rhumb", Workspace: "/fixture/rhumb", State: "busy", DeliveryMode: "fresh-worker"}}
	agent, err := host.RegisterAgent(ctx, registration, dest)
	if err != nil {
		t.Fatal(err)
	}
	old := AgentSessionRef{agent.AgentID, agent.ExecutionID}
	registration.RequestID = uuid.NewString()
	agent, err = host.RegisterAgent(ctx, registration, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{agent.AgentID, agent.ExecutionID}
	launch := func(s *Store) string {
		t.Helper()
		publishFixture(t, host, s, source, dest)
		w, err := s.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString()}, dest)
		if err != nil {
			t.Fatal(err)
		}
		for _, op := range []string{"start", "enter"} {
			if _, err = s.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: w.Attempt.ID, Operation: op}, dest); err != nil {
				t.Fatal(err)
			}
		}
		return w.Attempt.ID
	}
	id := launch(slot)
	attach := func(s *Store, id string, ref AgentSessionRef) error {
		_, err := s.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: id, Operation: "session", Session: &ref}, dest)
		return err
	}
	requireCode(t, attach(slot, id, old), "STALE_SESSION")
	requireCode(t, attach(host, id, ref), "NOT_FOUND")
	private := registration
	private.RequestID, private.NativeSessionID = uuid.NewString(), "private-native"
	localAgent, err := host.RegisterAgent(ctx, private, Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatal(err)
	}
	localRef := AgentSessionRef{localAgent.AgentID, localAgent.ExecutionID}
	_, err = slot.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: id, Operation: "session", Session: &localRef}, Destination{Name: "local", AllowLocal: true})
	requireCode(t, err, "NOT_FOUND")
	other := testStore(t, Channel{Principal: "slot-other:" + uuid.NewString(), Repo: source.Scope.Repo})
	otherID := launch(other)
	errs := make(chan error, 2)
	go func() { errs <- attach(slot, id, ref) }()
	go func() { errs <- attach(other, otherID, ref) }()
	one, two := <-errs, <-errs
	if (one == nil) == (two == nil) {
		t.Fatalf("concurrent native ownership: %v / %v", one, two)
	}
	if one != nil {
		requireCode(t, one, "AGENT_BUSY")
	} else {
		requireCode(t, two, "AGENT_BUSY")
	}
}
