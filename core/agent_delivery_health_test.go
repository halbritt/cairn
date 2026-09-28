package core

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
)

type healthFixture struct {
	sender, receiver *Store
	source           Record
	dest             Destination
	agent            AgentInstance
	ref              AgentSessionRef
}

func newHealthFixture(t *testing.T) healthFixture {
	t.Helper()
	sender, receiver, source, dest := eventFixture(t)
	a, err := receiver.RegisterAgent(context.Background(), RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "health", NativeSessionID: uuid.NewString(), Metadata: AgentMetadata{Harness: "claude", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	return healthFixture{sender, receiver, source, dest, a, AgentSessionRef{a.AgentID, a.ExecutionID}}
}

func (f healthFixture) publish(t *testing.T, kind, inbox string) (AgentEvent, string) {
	t.Helper()
	ctx := context.Background()
	e, err := f.sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: kind, Ref: RecordVersionRef{f.source.RecordID, f.source.Version}, Destination: EventDestination{Type: "agent", Name: inbox}}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	status, err := f.sender.AgentEventStatus(ctx, EventStatusRequest{EventID: e.EventID}, f.dest)
	if err != nil || len(status.Deliveries) != 1 {
		t.Fatalf("status: %+v %v", status, err)
	}
	return e, status.Deliveries[0].DeliveryID
}

func (f healthFixture) observe(t *testing.T, req SessionDeliveryObservation) SessionDeliveryObservationResult {
	t.Helper()
	if req.Session.AgentID == "" {
		req.Session = f.ref
	}
	out, err := f.receiver.ObserveSessionDelivery(context.Background(), req, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func (f healthFixture) diagnosis(t *testing.T, event string) *DeliveryDiagnosis {
	t.Helper()
	status, err := f.sender.AgentEventStatus(context.Background(), EventStatusRequest{EventID: event}, f.dest)
	if err != nil || len(status.Deliveries) != 1 {
		t.Fatalf("status: %+v %v", status, err)
	}
	return status.Deliveries[0].Diagnosis
}

func dbNow(t *testing.T, s *Store) time.Time {
	t.Helper()
	var now time.Time
	if err := s.pool.QueryRow(context.Background(), `SELECT clock_timestamp()`).Scan(&now); err != nil {
		t.Fatal(err)
	}
	return now
}

func TestSessionDeliveryObservationAuthorization(t *testing.T) {
	ctx := context.Background()
	f := newHealthFixture(t)
	_, delivery := f.publish(t, "request", f.agent.Inbox)
	now := dbNow(t, f.receiver)
	valid := SessionDeliveryObservation{Session: f.ref, DeliveryID: delivery, Condition: "channel_not_launched", ObservedAt: now}

	// Another profile cannot write for this session, even in the same repository.
	other := testStore(t, Channel{Principal: "other-host-" + f.source.Scope.Repo, Repo: f.source.Scope.Repo})
	_, err := other.ObserveSessionDelivery(ctx, valid, f.dest)
	requireCode(t, err, "NOT_FOUND")

	// A delivery outside this session's inbox is refused.
	_, foreign := f.publish(t, "request", f.receiver.channel.Principal)
	bad := valid
	bad.DeliveryID = foreign
	_, err = f.receiver.ObserveSessionDelivery(ctx, bad, f.dest)
	requireCode(t, err, "NOT_FOUND")

	for name, req := range map[string]SessionDeliveryObservation{
		"unknown condition":    {Session: f.ref, DeliveryID: delivery, Condition: "delivered", ObservedAt: now},
		"missing delivery":     {Session: f.ref, Condition: "wake_retained", ObservedAt: now},
		"wake on other code":   {Session: f.ref, DeliveryID: delivery, Condition: "busy", Wake: &ObservedWake{Transport: "terminal", Status: "submitted"}, ObservedAt: now},
		"unknown wake status":  {Session: f.ref, DeliveryID: delivery, Condition: "wake_retained", Wake: &ObservedWake{Transport: "terminal", Status: "delivered"}, ObservedAt: now},
		"future host clock":    {Session: f.ref, DeliveryID: delivery, Condition: "busy", ObservedAt: now.Add(time.Minute)},
		"missing observed_at":  {Session: f.ref, Condition: "busy"},
		"attempt after report": {Session: f.ref, DeliveryID: delivery, Condition: "wake_retained", Wake: &ObservedWake{Transport: "terminal", Status: "uncertain", AttemptedAt: ptrTime(now.Add(time.Second))}, ObservedAt: now},
	} {
		if _, err = f.receiver.ObserveSessionDelivery(ctx, req, f.dest); err == nil {
			t.Fatalf("%s accepted", name)
		}
		requireCode(t, err, "INVALID_REQUEST")
	}

	// A session view is not the host's base profile.
	view, err := f.receiver.ForAgentSession(f.ref, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	_, err = view.ObserveSessionDelivery(ctx, valid, f.dest)
	requireCode(t, err, "INVALID_REQUEST")

	if got := f.observe(t, valid); !got.Applied || got.Observation.Scope != "session" || got.Observation.Source != "host_reported" {
		t.Fatalf("valid observation: %+v", got)
	}

	// A replaced execution is fenced; a stopped session is refused.
	resumed, err := f.receiver.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "health", NativeSessionID: f.agent.NativeSessionID, Metadata: f.agent.Metadata}, f.dest)
	if err != nil || resumed.AgentID != f.agent.AgentID || resumed.ExecutionID == f.agent.ExecutionID {
		t.Fatalf("resume: %+v %v", resumed, err)
	}
	valid.ObservedAt = dbNow(t, f.receiver)
	_, err = f.receiver.ObserveSessionDelivery(ctx, valid, f.dest)
	requireCode(t, err, "STALE_SESSION")
	current := AgentSessionRef{resumed.AgentID, resumed.ExecutionID}
	if _, err = f.receiver.LeaveAgent(ctx, current, f.dest); err != nil {
		t.Fatal(err)
	}
	valid.Session = current
	_, err = f.receiver.ObserveSessionDelivery(ctx, valid, f.dest)
	requireCode(t, err, "STALE_SESSION")
}

func ptrTime(t time.Time) *time.Time { return &t }

func TestSessionDeliveryObservationOrderingAndTombstone(t *testing.T) {
	ctx := context.Background()
	f := newHealthFixture(t)
	_, delivery := f.publish(t, "request", f.agent.Inbox)
	t0 := dbNow(t, f.receiver)
	first := SessionDeliveryObservation{DeliveryID: delivery, Condition: "wake_retained", Wake: &ObservedWake{Transport: "claude-channel", Status: "submitted", AttemptedAt: ptrTime(t0.Add(-time.Second))}, ObservedAt: t0}
	got := f.observe(t, first)
	if !got.Applied {
		t.Fatalf("first: %+v", got)
	}
	received := got.Observation.ReceivedAt

	// An exact repeat changes nothing and cannot refresh freshness.
	again := f.observe(t, first)
	if again.Applied || !again.Observation.ReceivedAt.Equal(received) {
		t.Fatalf("repeat refreshed: %+v", again)
	}
	// The same observation time with a different payload is a conflict.
	changed := first
	changed.Session = f.ref
	changed.Condition, changed.Wake = "native_transport_unavailable", nil
	_, err := f.receiver.ObserveSessionDelivery(ctx, changed, f.dest)
	requireCode(t, err, "VERSION_CONFLICT")
	// An older observation is ignored.
	older := changed
	older.ObservedAt = t0.Add(-10 * time.Second)
	if got = f.observe(t, older); got.Applied || got.Observation.Condition != "wake_retained" {
		t.Fatalf("older applied: %+v", got)
	}
	// Clearing keeps an ordering tombstone; a delayed pre-clear retry cannot
	// resurrect the cleared diagnosis.
	cleared := f.observe(t, SessionDeliveryObservation{DeliveryID: delivery, Condition: "none", ObservedAt: t0.Add(2 * time.Second)})
	if !cleared.Applied || cleared.Observation.Condition != "none" {
		t.Fatalf("clear: %+v", cleared)
	}
	delayed := first
	delayed.ObservedAt = t0.Add(time.Second)
	if got = f.observe(t, delayed); got.Applied || got.Observation.Condition != "none" {
		t.Fatalf("delayed retry resurrected: %+v", got)
	}
	var rows int
	if err = f.receiver.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_session_delivery_observation WHERE agent_id=$1`, f.agent.AgentID).Scan(&rows); err != nil || rows != 1 {
		t.Fatalf("current-state rows: %d %v", rows, err)
	}
	var receipts int
	if err = f.receiver.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.mutation_request WHERE operation='session-delivery-observe'`).Scan(&receipts); err != nil || receipts != 0 {
		t.Fatalf("observation receipts: %d %v", receipts, err)
	}

	// An observation already older than the freshness bound creates nothing.
	g := newHealthFixture(t)
	_, other := g.publish(t, "request", g.agent.Inbox)
	stale := g.observe(t, SessionDeliveryObservation{DeliveryID: other, Condition: "busy", ObservedAt: dbNow(t, g.receiver).Add(-3 * time.Minute)})
	if stale.Applied || stale.Observation != nil {
		t.Fatalf("stale observation stored: %+v", stale)
	}
}

func TestDeliveryDiagnosisApplicabilityAndFreshness(t *testing.T) {
	ctx := context.Background()
	f := newHealthFixture(t)
	first, d1 := f.publish(t, "request", f.agent.Inbox)
	second, _ := f.publish(t, "request", f.agent.Inbox)

	// No observation: unknown and absent, never healthy.
	diag := f.diagnosis(t, first.EventID)
	if diag == nil || diag.Stage != "waiting" || diag.WaitingSeconds == nil || diag.Host == nil || diag.Host.Condition != "unknown" || diag.Host.Freshness != "absent" || diag.Host.Applies != "none" {
		t.Fatalf("absent: %+v %+v", diag, diag.Host)
	}
	if diag.Recipient.Kind != "session" || diag.Recipient.AgentID != f.agent.AgentID || diag.Recipient.ExecutionID != f.agent.ExecutionID || diag.Recipient.Presence != "online" || diag.Recipient.State != "idle" {
		t.Fatalf("recipient: %+v", diag.Recipient)
	}

	// A delivery-scope observation explains only its exact delivery.
	t0 := dbNow(t, f.receiver)
	f.observe(t, SessionDeliveryObservation{DeliveryID: d1, Condition: "wake_retained", Wake: &ObservedWake{Transport: "claude-channel", Status: "submitted"}, ObservedAt: t0})
	diag = f.diagnosis(t, first.EventID)
	if diag.Host.Applies != "exact" || diag.Host.Condition != "wake_retained" || diag.Host.Freshness != "current" || diag.Host.Wake == nil || diag.Host.Source != "host_reported" {
		t.Fatalf("exact: %+v", diag.Host)
	}
	diag = f.diagnosis(t, second.EventID)
	if diag.Host.Applies != "other_delivery" || diag.Host.DeliveryID != d1 {
		t.Fatalf("other delivery inherited: %+v", diag.Host)
	}
	// A session-scope observation applies to the session's other waiting work.
	f.observe(t, SessionDeliveryObservation{DeliveryID: d1, Condition: "channel_not_launched", ObservedAt: t0.Add(time.Second)})
	if diag = f.diagnosis(t, second.EventID); diag.Host.Applies != "session" || diag.Host.Condition != "channel_not_launched" {
		t.Fatalf("session scope: %+v", diag.Host)
	}

	// Old receipt or old host time is stale; the condition remains labelled.
	if _, err := f.receiver.pool.Exec(ctx, `UPDATE cairn.agent_session_delivery_observation SET received_at=received_at-interval '5 minutes' WHERE agent_id=$1`, f.agent.AgentID); err != nil {
		t.Fatal(err)
	}
	if diag = f.diagnosis(t, second.EventID); diag.Host.Freshness != "stale" || diag.Host.Condition != "channel_not_launched" {
		t.Fatalf("stale receipt: %+v", diag.Host)
	}
	if _, err := f.receiver.pool.Exec(ctx, `UPDATE cairn.agent_session_delivery_observation SET received_at=clock_timestamp(),observed_at=observed_at-interval '5 minutes' WHERE agent_id=$1`, f.agent.AgentID); err != nil {
		t.Fatal(err)
	}
	if diag = f.diagnosis(t, second.EventID); diag.Host.Freshness != "stale" {
		t.Fatalf("stale host time: %+v", diag.Host)
	}
	// A changed generation fences the observation.
	if _, err := f.receiver.pool.Exec(ctx, `UPDATE cairn.agent_session_delivery_observation SET observed_at=clock_timestamp(),database_generation=database_generation-1 WHERE agent_id=$1`, f.agent.AgentID); err != nil {
		t.Fatal(err)
	}
	if diag = f.diagnosis(t, second.EventID); diag.Host.Freshness != "replaced" {
		t.Fatalf("generation: %+v", diag.Host)
	}
	// Expired presence makes it offline.
	if _, err := f.receiver.pool.Exec(ctx, `UPDATE cairn.agent_session_delivery_observation SET database_generation=(SELECT generation FROM cairn.retrieval_generation WHERE singleton) WHERE agent_id=$1`, f.agent.AgentID); err != nil {
		t.Fatal(err)
	}
	if _, err := f.receiver.pool.Exec(ctx, `UPDATE cairn.agent_session SET expires_at=clock_timestamp()-interval '1 second' WHERE agent_id=$1`, f.agent.AgentID); err != nil {
		t.Fatal(err)
	}
	if diag = f.diagnosis(t, second.EventID); diag.Recipient.Presence != "offline" || diag.Host.Freshness != "offline" {
		t.Fatalf("offline: %+v %+v", diag.Recipient, diag.Host)
	}
	// A resumed execution replaces the earlier execution's observation.
	resumed, err := f.receiver.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "health", NativeSessionID: f.agent.NativeSessionID, Metadata: f.agent.Metadata}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	if diag = f.diagnosis(t, second.EventID); diag.Recipient.ExecutionID != resumed.ExecutionID || diag.Host.Freshness != "replaced" {
		t.Fatalf("replaced execution: %+v %+v", diag.Recipient, diag.Host)
	}
	f.ref = AgentSessionRef{resumed.AgentID, resumed.ExecutionID}

	// The tombstone renders unknown, not healthy.
	f.observe(t, SessionDeliveryObservation{Condition: "none", ObservedAt: dbNow(t, f.receiver)})
	if diag = f.diagnosis(t, second.EventID); diag.Host.Condition != "unknown" || diag.Host.Reported != "none" || diag.Host.Applies != "none" {
		t.Fatalf("tombstone: %+v", diag.Host)
	}

	// Future availability is intentional waiting and carries no host cause.
	if _, err = f.receiver.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET available_at=clock_timestamp()+interval '1 hour' WHERE delivery_id=$1`, d1); err != nil {
		t.Fatal(err)
	}
	if diag = f.diagnosis(t, first.EventID); diag.Stage != "scheduled" || diag.Host != nil || diag.WaitingSeconds != nil {
		t.Fatalf("scheduled: %+v", diag)
	}
}

func TestDeliveryDiagnosisStoreFactsOverrideHostCause(t *testing.T) {
	ctx := context.Background()
	f := newHealthFixture(t)
	event, delivery := f.publish(t, "request", f.agent.Inbox)
	f.observe(t, SessionDeliveryObservation{DeliveryID: delivery, Condition: "wake_retained", Wake: &ObservedWake{Transport: "claude-channel", Status: "submitted"}, ObservedAt: dbNow(t, f.receiver)})
	claim, err := f.receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: f.ref, DeliveryID: delivery, NativeTurnID: "turn"}, f.dest)
	if err != nil || claim.Attempt == nil {
		t.Fatalf("claim: %+v %v", claim, err)
	}
	if diag := f.diagnosis(t, event.EventID); diag.Stage != "held" || diag.Host != nil {
		t.Fatalf("held: %+v", diag)
	}
	view, err := f.receiver.ForAgentSession(f.ref, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery, LeaseID: claim.Attempt.Delivery.LeaseID, Disposition: "handled"}, f.dest); err != nil {
		t.Fatal(err)
	}
	if diag := f.diagnosis(t, event.EventID); diag.Stage != "handled" && diag.Stage != "held" || diag.Host != nil {
		t.Fatalf("handled: %+v", diag)
	}
	if _, err = f.receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: f.ref, AttemptID: claim.Attempt.ID, Reason: "delivery_completed"}, f.dest); err != nil {
		t.Fatal(err)
	}
	if diag := f.diagnosis(t, event.EventID); diag.Stage != "handled" || diag.Host != nil {
		t.Fatalf("handled after reconcile: %+v", diag)
	}
}

func TestDeliveryDiagnosisVisibility(t *testing.T) {
	ctx := context.Background()
	f := newHealthFixture(t)
	notice, _ := f.publish(t, "notice", f.agent.Inbox)
	status, err := f.sender.AgentEventStatus(ctx, EventStatusRequest{EventID: notice.EventID}, f.dest)
	if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].Diagnosis != nil {
		t.Fatalf("notice diagnosis on requester status: %+v %v", status, err)
	}
	request, _ := f.publish(t, "request", f.agent.Inbox)
	// The recipient's own session view keeps its existing read of its delivery.
	view, err := f.receiver.ForAgentSession(f.ref, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	own, err := view.AgentEventStatus(ctx, EventStatusRequest{EventID: request.EventID}, f.dest)
	if err != nil || len(own.Deliveries) != 1 || own.Deliveries[0].Diagnosis == nil {
		t.Fatalf("recipient status: %+v %v", own, err)
	}
	// An unrelated profile gains no delivery or diagnosis.
	other := testStore(t, Channel{Principal: "unrelated-" + f.source.Scope.Repo, Repo: f.source.Scope.Repo})
	if foreign, err := other.AgentEventStatus(ctx, EventStatusRequest{EventID: request.EventID}, f.dest); err == nil && len(foreign.Deliveries) != 0 {
		t.Fatalf("unrelated read: %+v", foreign)
	}
	// A profile inbox is not a session and carries no host explanation.
	profile, _ := f.publish(t, "request", f.receiver.channel.Principal)
	if diag := f.diagnosis(t, profile.EventID); diag == nil || diag.Recipient.Kind != "other" || diag.Host != nil {
		t.Fatalf("profile inbox: %+v", diag)
	}
	// Operator review diagnoses every event kind; ordinary profiles cannot review.
	op := testStore(t, Channel{Principal: "health-operator-" + f.source.Scope.Repo, Operator: true})
	page, err := op.ReviewEvents(ctx, EventReviewRequest{Repo: f.source.Scope.Repo, Consumer: f.agent.Inbox})
	if err != nil || len(page.Deliveries) != 2 {
		t.Fatalf("review: %+v %v", page, err)
	}
	for _, d := range page.Deliveries {
		if d.Diagnosis == nil || d.Diagnosis.Host == nil || d.Diagnosis.Host.Freshness != "absent" {
			t.Fatalf("review diagnosis: %+v", d.Diagnosis)
		}
	}
	_, err = f.sender.ReviewEvents(ctx, EventReviewRequest{Repo: f.source.Scope.Repo})
	requireCode(t, err, "AUTHORITY_DENIED")
}
