package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

type panelRecoveryProbe struct {
	sender, receiver, operator, view *Store
	registration                     RegisterAgentRequest
	repo                             string
	dest                             Destination
	source                           RecordVersionRef
	event                            AgentEvent
	agent                            AgentInstance
	ref                              AgentSessionRef
	attempt                          SessionInboxAttempt
}

func newPanelRecoveryProbe(t *testing.T, fixture panelOwnershipFixture, caseID, eventID, action string) panelRecoveryProbe {
	t.Helper()
	row := fixture.selectedCase(t, caseID)
	if len(row.Setup.Events) != 1 || row.Setup.Events[0].ID != eventID ||
		row.Setup.Events[0].Kind != "request" || row.Setup.Events[0].Recipient != "worker" ||
		len(row.Setup.Transitions) != 1 || row.Setup.Transitions[0].Phase != "after_claim_before_settlement" ||
		row.Setup.Transitions[0].Action != action || row.Setup.Transitions[0].Target != eventID {
		t.Fatalf("%s fixture setup/transition changed", caseID)
	}
	ctx := context.Background()
	repo := fixture.EventSourceDefaults.Scope.Repo
	dest := Destination{Name: "hosted", AllowLocal: false}
	sender := testStore(t, Channel{Principal: "agent:panel-" + caseID + "-sender-" + uuid.NewString(), Repo: repo})
	receiver := testStore(t, Channel{Principal: "agent:panel-" + caseID + "-worker-" + uuid.NewString(), Repo: repo})
	operator := testStore(t, Channel{Principal: "panel-" + caseID + "-operator-" + uuid.NewString(), Operator: true})
	sessionID := "panel-" + caseID + "-" + uuid.NewString()
	registration := RegisterAgentRequest{RequestID: uuid.NewString(), Binding: sessionID, NativeSessionID: sessionID,
		Metadata: AgentMetadata{Harness: "codex", Project: "fixture", Workspace: "/work/fixture",
			State: "idle", DeliveryMode: "existing-session"}}
	agent, err := receiver.RegisterAgent(ctx, registration, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{AgentID: agent.AgentID, ExecutionID: agent.ExecutionID}
	source := fixture.createSource(t, sender, row.Setup.Events[0])
	event, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request",
		Ref: source, Destination: EventDestination{Type: "agent", Name: agent.Inbox}}, dest)
	if err != nil || event.Ref != source {
		t.Fatalf("%s publication: %+v %v", caseID, event, err)
	}
	status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
	if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].State != "pending" ||
		status.Deliveries[0].Consumer != agent.Inbox {
		t.Fatalf("%s pending delivery: %+v %v", caseID, status, err)
	}
	claimed, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref,
		DeliveryID: status.Deliveries[0].DeliveryID, NativeTurnID: "panel-turn-" + uuid.NewString(), TurnExclusive: true}, dest)
	if err != nil || claimed.Attempt == nil || claimed.Attempt.Delivery.Event.EventID != event.EventID ||
		claimed.Attempt.Delivery.DeliveryID != status.Deliveries[0].DeliveryID {
		t.Fatalf("%s native claim: %+v %v", caseID, claimed, err)
	}
	view, err := receiver.ForAgentSession(ref, dest)
	if err != nil {
		t.Fatal(err)
	}
	return panelRecoveryProbe{sender: sender, receiver: receiver, operator: operator, view: view,
		registration: registration, repo: repo, dest: dest, source: source, event: event,
		agent: agent, ref: ref, attempt: *claimed.Attempt}
}

func (p panelRecoveryProbe) review(t *testing.T) ReviewDelivery {
	t.Helper()
	page, err := p.operator.ReviewEvents(context.Background(), EventReviewRequest{Repo: p.repo,
		DeliveryID: p.attempt.Delivery.DeliveryID})
	if err != nil || len(page.Deliveries) != 1 {
		t.Fatalf("review original: %+v %v", page, err)
	}
	return page.Deliveries[0]
}

func (p panelRecoveryProbe) expireLease(t *testing.T) {
	t.Helper()
	if _, err := p.receiver.pool.Exec(context.Background(),
		`UPDATE cairn.agent_delivery SET lease_until=clock_timestamp()-interval '1 second' WHERE delivery_id=$1`,
		p.attempt.Delivery.DeliveryID); err != nil {
		t.Fatal(err)
	}
}

// This changes only disposable test presence. It represents the observable
// offline boundary; it does not prove that the recipient process crashed.
func (p panelRecoveryProbe) expirePresence(t *testing.T) {
	t.Helper()
	if _, err := p.receiver.pool.Exec(context.Background(),
		`UPDATE cairn.agent_session SET expires_at=clock_timestamp()-interval '1 second' WHERE agent_id=$1`,
		p.ref.AgentID); err != nil {
		t.Fatal(err)
	}
}

func (p panelRecoveryProbe) release(t *testing.T) NativeHoldRelease {
	t.Helper()
	released, err := p.operator.ReleaseNativeHold(context.Background(), NativeHoldReleaseRequest{
		RequestID: uuid.NewString(), Repo: p.repo, AttemptID: p.attempt.ID,
		Reason: "Reviewed offline native host; prior effects remain uncertain", AcceptUncertainEffects: true})
	if err != nil || released.Reason != "operator_released" || released.DeliveryState != "failed" ||
		released.Code != "operator_released" || released.Session != p.ref ||
		released.ReleasedBy != p.operator.channel.Principal {
		t.Fatalf("operator release: %+v %v", released, err)
	}
	return released
}

func (p panelRecoveryProbe) reissue(t *testing.T) (AgentEvent, string) {
	t.Helper()
	ctx := context.Background()
	requestID := uuid.NewString()
	event, err := p.operator.ReissueEvent(ctx, ReissueEventRequest{RequestID: requestID, Repo: p.repo,
		DeliveryID: p.attempt.Delivery.DeliveryID, Reason: "Reviewed failed native attempt; repeat request",
		AcceptUncertainEffects: true})
	if err != nil || event.EventID == p.event.EventID || event.Kind != "request" || event.Ref != p.source ||
		event.CausationID != p.event.EventID || event.CorrelationID != requestID {
		t.Fatalf("operator reissue: %+v %v", event, err)
	}
	old := p.review(t)
	if old.Held || old.State != "failed" || old.Result != nil || old.ReissuedAs == nil ||
		old.ReissuedAs.EventID != event.EventID || old.LatestNative == nil ||
		old.LatestNative.Reason != "operator_released" {
		t.Fatalf("original recovery lineage: %+v", old)
	}
	// The operator publishes the successor, so the original sender cannot read
	// its event-status. Review the exact consumer's next delivery instead.
	successor, err := p.operator.ReviewEvents(ctx, EventReviewRequest{Repo: p.repo,
		Consumer: p.agent.Inbox, After: old.Position, Limit: 1})
	if err != nil || len(successor.Deliveries) != 1 ||
		successor.Deliveries[0].Event.EventID != event.EventID ||
		successor.Deliveries[0].State != "pending" ||
		successor.Deliveries[0].ReissuedFrom == nil ||
		successor.Deliveries[0].ReissuedFrom.OriginalDeliveryID != p.attempt.Delivery.DeliveryID {
		t.Fatalf("reissued pending delivery: %+v %v", successor, err)
	}
	return event, successor.Deliveries[0].DeliveryID
}

func (p panelRecoveryProbe) resumeAndClaim(t *testing.T, event AgentEvent, deliveryID string) (AgentSessionRef, SessionInboxAttempt) {
	t.Helper()
	ctx := context.Background()
	registration := p.registration
	registration.RequestID = uuid.NewString()
	resumed, err := p.receiver.RegisterAgent(ctx, registration, p.dest)
	if err != nil || resumed.AgentID != p.agent.AgentID || resumed.ExecutionID == p.ref.ExecutionID {
		t.Fatalf("new execution: %+v %v", resumed, err)
	}
	current := AgentSessionRef{AgentID: resumed.AgentID, ExecutionID: resumed.ExecutionID}
	claimed, err := p.receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: current,
		DeliveryID: deliveryID, NativeTurnID: "panel-turn-" + uuid.NewString(), TurnExclusive: true}, p.dest)
	if err != nil || claimed.Attempt == nil || claimed.Attempt.Delivery.Event.EventID != event.EventID ||
		claimed.Attempt.Delivery.DeliveryID != deliveryID || claimed.Attempt.Session != current {
		t.Fatalf("successor claim: %+v %v", claimed, err)
	}
	return current, *claimed.Attempt
}

// These probes exercise native Store setup and recovery transitions only.
// No recipient model, process crash, external task, or outcome is fabricated.
func TestTokenEfficiencyPanelNativeRecoveryTransitions(t *testing.T) {
	ctx := context.Background()
	fixture := readPanelOwnershipFixture(t)

	t.Run("lease_expiry", func(t *testing.T) {
		p := newPanelRecoveryProbe(t, fixture, "lease_expiry", "request_lease", "expire_lease")
		p.expireLease(t)
		d := p.attempt.Delivery
		_, err := p.view.ChangeEventLease(ctx, EventLeaseRequest{DeliveryID: d.DeliveryID,
			LeaseID: d.LeaseID, LeaseSeconds: 90}, true, p.dest)
		requireCode(t, err, "STALE_LEASE")
		_, err = p.view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(),
			DeliveryID: d.DeliveryID, LeaseID: uuid.NewString(), Disposition: "failed", Code: "processing_failed"}, p.dest)
		requireCode(t, err, "STALE_LEASE")
		second, err := p.receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: p.ref,
			DeliveryID: d.DeliveryID, NativeTurnID: "panel-second-turn-" + uuid.NewString(), TurnExclusive: true}, p.dest)
		if err != nil || second.Attempt != nil {
			t.Fatalf("expired held request was claimed twice: %+v %v", second, err)
		}
		requestID := uuid.NewString()
		complete := CompleteEventRequest{RequestID: requestID, DeliveryID: d.DeliveryID,
			LeaseID: d.LeaseID, Disposition: "failed", Code: "processing_failed"}
		settled, err := p.view.CompleteEvent(ctx, complete, p.dest)
		if err != nil || settled.State != "failed" || settled.Code != "processing_failed" ||
			!settled.Late || settled.Result != nil {
			t.Fatalf("late native settlement: %+v %v", settled, err)
		}
		replayed, err := p.view.CompleteEvent(ctx, complete, p.dest)
		if err != nil || replayed.DeliveryID != settled.DeliveryID || !replayed.Late || replayed.Result != nil {
			t.Fatalf("late settlement replay: %+v %v", replayed, err)
		}
		status, err := p.sender.AgentEventStatus(ctx, EventStatusRequest{EventID: p.event.EventID}, p.dest)
		if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].State != "failed" ||
			!status.Deliveries[0].Late || status.Deliveries[0].Attempts != 1 {
			t.Fatalf("late durable status: %+v %v", status, err)
		}
		closed, err := p.receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(),
			Session: p.ref, AttemptID: p.attempt.ID, Reason: "delivery_completed"}, p.dest)
		if err != nil || closed.FinishedAt == nil || closed.Delivery.State != "failed" || !closed.Delivery.Late {
			t.Fatalf("late attempt reconciliation: %+v %v", closed, err)
		}
		// A separate hold closed without settlement cannot accept the old lease.
		q := newPanelRecoveryProbe(t, fixture, "lease_expiry", "request_lease", "expire_lease")
		q.expireLease(t)
		q.expirePresence(t)
		q.release(t)
		_, err = q.view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(),
			DeliveryID: q.attempt.Delivery.DeliveryID, LeaseID: q.attempt.Delivery.LeaseID,
			Disposition: "failed", Code: "processing_failed"}, q.dest)
		requireCode(t, err, "HOLD_RELEASED")
	})

	t.Run("process_crash", func(t *testing.T) {
		p := newPanelRecoveryProbe(t, fixture, "process_crash", "request_crash", "crash_recipient")
		prior := p.review(t)
		if !prior.Held || prior.State != "leased" || prior.Result != nil || prior.LatestNative == nil ||
			prior.LatestNative.ID != p.attempt.ID || prior.LatestNative.FinishedAt != nil {
			t.Fatalf("unfinished persisted claim: %+v", prior)
		}
		_, err := p.operator.ReleaseNativeHold(ctx, NativeHoldReleaseRequest{RequestID: uuid.NewString(),
			Repo: p.repo, AttemptID: p.attempt.ID, Reason: "Host still present", AcceptUncertainEffects: true})
		requireCode(t, err, "SESSION_LIVE")
		p.expirePresence(t)
		p.release(t)
		_, err = p.view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(),
			DeliveryID: p.attempt.Delivery.DeliveryID, LeaseID: p.attempt.Delivery.LeaseID,
			Disposition: "failed", Code: "processing_failed"}, p.dest)
		requireCode(t, err, "HOLD_RELEASED")
		original, err := p.sender.AgentEventStatus(ctx, EventStatusRequest{EventID: p.event.EventID}, p.dest)
		if err != nil || len(original.Deliveries) != 1 || original.Deliveries[0].State != "failed" ||
			original.Deliveries[0].Code != "operator_released" || original.Deliveries[0].Result != nil {
			t.Fatalf("uncompleted original status: %+v %v", original, err)
		}
		event, deliveryID := p.reissue(t)
		p.resumeAndClaim(t, event, deliveryID)
	})

	t.Run("stale_execution", func(t *testing.T) {
		p := newPanelRecoveryProbe(t, fixture, "stale_execution", "request_stale", "rotate_execution")
		registration := p.registration
		registration.RequestID = uuid.NewString()
		_, err := p.receiver.RegisterAgent(ctx, registration, p.dest)
		requireCode(t, err, "AGENT_BUSY")
		p.expirePresence(t)
		p.release(t)
		event, deliveryID := p.reissue(t)
		current, successor := p.resumeAndClaim(t, event, deliveryID)
		_, err = p.receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(),
			Session: p.ref, DeliveryID: deliveryID, NativeTurnID: "panel-old-turn-" + uuid.NewString(),
			TurnExclusive: true}, p.dest)
		requireCode(t, err, "STALE_SESSION")
		_, err = p.view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(),
			DeliveryID: p.attempt.Delivery.DeliveryID, LeaseID: p.attempt.Delivery.LeaseID,
			Disposition: "failed", Code: "processing_failed"}, p.dest)
		requireCode(t, err, "STALE_SESSION")
		_, err = p.view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(),
			DeliveryID: successor.Delivery.DeliveryID, LeaseID: successor.Delivery.LeaseID,
			Disposition: "failed", Code: "processing_failed"}, p.dest)
		requireCode(t, err, "STALE_SESSION")
		old := p.review(t)
		if old.State != "failed" || old.Result != nil || old.ReissuedAs == nil ||
			old.ReissuedAs.EventID != event.EventID || successor.Session != current {
			t.Fatalf("old execution changed current owner or result: %+v %+v", old, successor)
		}
		page, err := p.operator.ReviewEvents(ctx, EventReviewRequest{Repo: p.repo,
			DeliveryID: successor.Delivery.DeliveryID})
		if err != nil || len(page.Deliveries) != 1 || page.Deliveries[0].Event.EventID != event.EventID ||
			page.Deliveries[0].State != "leased" || page.Deliveries[0].Attempts != 1 ||
			page.Deliveries[0].LatestNative == nil || page.Deliveries[0].LatestNative.Session == nil ||
			*page.Deliveries[0].LatestNative.Session != current {
			t.Fatalf("current execution successor status: %+v %v", page, err)
		}
	})
}
