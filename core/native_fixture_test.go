package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

// nativeFixture is the one scaffold for existing-session (native inbox)
// tests. It sits on eventFixture and owns the steps every file used to
// re-derive: registering an existing-session agent, publishing to its inbox,
// claiming natively, opening an operator channel, and moving time. The
// time-travel methods hold the only raw SQL; tests cross the Store interface
// plus this fixture.
type nativeFixture struct {
	t                *testing.T
	sender, receiver *Store
	source           Record
	dest             Destination
	repo             string
	op               *Store
}

func newNativeFixture(t *testing.T) *nativeFixture {
	t.Helper()
	sender, receiver, source, dest := eventFixture(t)
	return &nativeFixture{t: t, sender: sender, receiver: receiver, source: source, dest: dest, repo: source.Scope.Repo}
}

// nativeAgent describes one registration. Zero fields take the defaults that
// the former per-file copies shared: an idle Codex existing-session agent in
// project "test" at /work/test, registered through the receiver profile.
type nativeAgent struct {
	Binding     string
	Native      string // native session id; defaults to Binding
	Harness     string // defaults to "codex"
	State       string // defaults to "idle"
	Mode        string // delivery mode; defaults to "existing-session"
	Project     string
	Workspace   string
	TaskSummary string
	Store       *Store       // registering profile; defaults to the receiver
	Dest        *Destination // defaults to the fixture destination
}

// existingSessionMetadata is the metadata literal that 51 tests used to spell
// out by hand.
func existingSessionMetadata(harness, state string) AgentMetadata {
	return AgentMetadata{Harness: harness, Project: "test", Workspace: "/work/test", State: state, DeliveryMode: "existing-session"}
}

func (f *nativeFixture) registration(spec nativeAgent) RegisterAgentRequest {
	if spec.Binding == "" {
		spec.Binding = "native"
	}
	if spec.Native == "" {
		spec.Native = spec.Binding
	}
	if spec.Harness == "" {
		spec.Harness = "codex"
	}
	if spec.State == "" {
		spec.State = "idle"
	}
	if spec.Mode == "" {
		spec.Mode = "existing-session"
	}
	if spec.Project == "" {
		spec.Project = "test"
	}
	if spec.Workspace == "" {
		spec.Workspace = "/work/test"
	}
	return RegisterAgentRequest{RequestID: uuid.NewString(), Binding: spec.Binding, NativeSessionID: spec.Native,
		Metadata: AgentMetadata{Harness: spec.Harness, Project: spec.Project, Workspace: spec.Workspace, TaskSummary: spec.TaskSummary, State: spec.State, DeliveryMode: spec.Mode}}
}

func (f *nativeFixture) store(spec nativeAgent) *Store {
	if spec.Store != nil {
		return spec.Store
	}
	return f.receiver
}

func (f *nativeFixture) destination(spec nativeAgent) Destination {
	if spec.Dest != nil {
		return *spec.Dest
	}
	return f.dest
}

// register registers one agent and fails the test on refusal. Use
// registerRequest when a test must resume with the exact same request.
func (f *nativeFixture) register(spec nativeAgent) AgentInstance {
	f.t.Helper()
	return f.registerRequest(f.registration(spec), f.store(spec), f.destination(spec))
}

func (f *nativeFixture) registerRequest(req RegisterAgentRequest, profile *Store, dest Destination) AgentInstance {
	f.t.Helper()
	a, err := profile.RegisterAgent(context.Background(), req, dest)
	if err != nil {
		f.t.Fatal(err)
	}
	return a
}

func sessionRef(a AgentInstance) AgentSessionRef {
	return AgentSessionRef{AgentID: a.AgentID, ExecutionID: a.ExecutionID}
}

// view opens the receiver's session-scoped store for a registered agent.
func (f *nativeFixture) view(ref AgentSessionRef) *Store {
	f.t.Helper()
	v, err := f.receiver.ForAgentSession(ref, f.dest)
	if err != nil {
		f.t.Fatal(err)
	}
	return v
}

// operator lazily opens an operator channel for the fixture repository.
func (f *nativeFixture) operator() *Store {
	f.t.Helper()
	if f.op == nil {
		f.op = testStore(f.t, Channel{Principal: "operator-" + f.repo, Operator: true})
	}
	return f.op
}

// publish sends the fixture source as kind to inbox through the sender and
// returns the event with its single delivery id.
func (f *nativeFixture) publish(kind, inbox string) (AgentEvent, string) {
	f.t.Helper()
	return f.publishRequest(f.sender, PublishEventRequest{RequestID: uuid.NewString(), Kind: kind, Ref: RecordVersionRef{f.source.RecordID, f.source.Version}, Destination: EventDestination{Type: "agent", Name: inbox}})
}

// publishRequest publishes an explicit request through the given profile and
// returns the event with its single delivery id.
func (f *nativeFixture) publishRequest(profile *Store, req PublishEventRequest) (AgentEvent, string) {
	f.t.Helper()
	e, err := profile.PublishEvent(context.Background(), req, f.dest)
	if err != nil {
		f.t.Fatal(err)
	}
	return e, f.delivery(e.EventID)
}

func (f *nativeFixture) status(eventID string) EventStatus {
	f.t.Helper()
	st, err := f.sender.AgentEventStatus(context.Background(), EventStatusRequest{EventID: eventID}, f.dest)
	if err != nil {
		f.t.Fatal(err)
	}
	return st
}

// delivery returns the single delivery id of an event.
func (f *nativeFixture) delivery(eventID string) string {
	f.t.Helper()
	st := f.status(eventID)
	if len(st.Deliveries) != 1 {
		f.t.Fatalf("status: %+v", st)
	}
	return st.Deliveries[0].DeliveryID
}

// claim performs a native claim through the receiver and requires an attempt.
// deliveryID and turn may be empty for a plain boundary claim.
func (f *nativeFixture) claim(ref AgentSessionRef, deliveryID, turn string, exclusive bool) SessionInboxResult {
	f.t.Helper()
	return f.claimWith(f.receiver, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref, DeliveryID: deliveryID, NativeTurnID: turn, TurnExclusive: exclusive})
}

func (f *nativeFixture) claimWith(profile *Store, req SessionInboxClaim) SessionInboxResult {
	f.t.Helper()
	got, err := profile.ClaimSessionInbox(context.Background(), req, f.dest)
	if err != nil || got.Attempt == nil {
		f.t.Fatalf("claim: %+v %v", got, err)
	}
	return got
}

// claimed registers, publishes one event of kind and claims it in one step.
func (f *nativeFixture) claimed(spec nativeAgent, kind, turn string, exclusive bool) (AgentInstance, AgentEvent, SessionInboxResult) {
	f.t.Helper()
	a := f.register(spec)
	event, deliveryID := f.publish(kind, a.Inbox)
	if turn == "" && !exclusive {
		deliveryID = ""
	}
	return a, event, f.claim(sessionRef(a), deliveryID, turn, exclusive)
}

// --- time travel: the only raw SQL in native tests ---

func (f *nativeFixture) exec(query string, args ...any) {
	f.t.Helper()
	if _, err := f.receiver.pool.Exec(context.Background(), query, args...); err != nil {
		f.t.Fatal(err)
	}
}

// expireLease moves a delivery's lease into the past.
func (f *nativeFixture) expireLease(deliveryID string) {
	f.t.Helper()
	f.exec(`UPDATE cairn.agent_delivery SET lease_until=clock_timestamp()-interval '1 second' WHERE delivery_id=$1`, deliveryID)
}

// makeAvailable moves a deferred delivery's availability into the past.
func (f *nativeFixture) makeAvailable(deliveryID string) {
	f.t.Helper()
	f.exec(`UPDATE cairn.agent_delivery SET available_at=clock_timestamp()-interval '1 second' WHERE delivery_id=$1`, deliveryID)
}

// makeEventAvailable does the same for every delivery of an event.
func (f *nativeFixture) makeEventAvailable(eventID string) {
	f.t.Helper()
	f.exec(`UPDATE cairn.agent_delivery SET available_at=clock_timestamp()-interval '1 second' WHERE event_id=$1`, eventID)
}

// deferDelivery pushes a delivery's availability into the future.
func (f *nativeFixture) deferDelivery(deliveryID, interval string) {
	f.t.Helper()
	f.exec(`UPDATE cairn.agent_delivery SET available_at=clock_timestamp()+$2::interval WHERE delivery_id=$1`, deliveryID, interval)
}

// expireSession lets presence lapse without recording a stop or changing
// last contact: expired presence alone never proves the agent stopped.
func (f *nativeFixture) expireSession(agentID string) {
	f.t.Helper()
	f.lapsePresence(agentID, "1 second")
}

// lapsePresence moves only presence expiry into the past by interval.
func (f *nativeFixture) lapsePresence(agentID, interval string) {
	f.t.Helper()
	f.exec(`UPDATE cairn.agent_session SET expires_at=clock_timestamp()-$2::interval WHERE agent_id=$1`, agentID, interval)
}

// backdateSession moves presence expiry and last contact into the past by
// interval, the shape every stopped-session test needs.
func (f *nativeFixture) backdateSession(agentID, interval string) {
	f.t.Helper()
	f.exec(`UPDATE cairn.agent_session SET expires_at=clock_timestamp()-$2::interval,last_seen=clock_timestamp()-$2::interval WHERE agent_id=$1`, agentID, interval)
}

// stopAgo records an explicit stop through the receiver and backdates it.
func (f *nativeFixture) stopAgo(a AgentInstance, interval string) {
	f.t.Helper()
	if _, err := f.receiver.LeaveAgent(context.Background(), sessionRef(a), f.dest); err != nil {
		f.t.Fatal(err)
	}
	f.backdateSession(a.AgentID, interval)
}

// expireAdmission moves a request's admission window into the past.
func (f *nativeFixture) expireAdmission(eventID string) {
	f.t.Helper()
	f.exec(`UPDATE cairn.agent_event SET admission_expires_at=clock_timestamp()-interval '1 second' WHERE event_id=$1`, eventID)
}

// pastDeadline moves a request's managed task deadline into the past.
func (f *nativeFixture) pastDeadline(eventID string) {
	f.t.Helper()
	f.exec(`UPDATE cairn.agent_event SET task_deadline=clock_timestamp()-interval '1 second' WHERE event_id=$1`, eventID)
}

// closeResponseGroup moves a request's response-group deadline into the past.
func (f *nativeFixture) closeResponseGroup(eventID string) {
	f.t.Helper()
	f.exec(`UPDATE cairn.agent_response_group SET deadline=clock_timestamp()-interval '1 minute' WHERE event_id=$1`, eventID)
}

// expireSuccessorGrant ends a successor grant as if its window had passed.
func (f *nativeFixture) expireSuccessorGrant(grantID string) {
	f.t.Helper()
	f.exec(`UPDATE cairn.agent_successor_grant SET granted_at=clock_timestamp()-interval '10 minutes',expires_at=clock_timestamp()-interval '1 second' WHERE grant_id=$1`, grantID)
}

// --- host delivery observations (delivery health) ---

// ageObservation moves a session's observation receipt and host time into
// the past by interval.
func (f *nativeFixture) ageObservation(agentID, interval string) {
	f.t.Helper()
	f.exec(`UPDATE cairn.agent_session_delivery_observation SET observed_at=observed_at-$2::interval,received_at=received_at-$2::interval WHERE agent_id=$1`, agentID, interval)
}

// staleObservationReceipt ages only the store-side receipt.
func (f *nativeFixture) staleObservationReceipt(agentID string) {
	f.t.Helper()
	f.exec(`UPDATE cairn.agent_session_delivery_observation SET received_at=received_at-interval '5 minutes' WHERE agent_id=$1`, agentID)
}

// staleObservationHostTime refreshes the receipt but ages the host time.
func (f *nativeFixture) staleObservationHostTime(agentID string) {
	f.t.Helper()
	f.exec(`UPDATE cairn.agent_session_delivery_observation SET received_at=clock_timestamp(),observed_at=observed_at-interval '5 minutes' WHERE agent_id=$1`, agentID)
}

// fenceObservationGeneration makes the observation predate a restore fence.
func (f *nativeFixture) fenceObservationGeneration(agentID string) {
	f.t.Helper()
	f.exec(`UPDATE cairn.agent_session_delivery_observation SET observed_at=clock_timestamp(),database_generation=database_generation-1 WHERE agent_id=$1`, agentID)
}

// restoreObservationGeneration re-aligns the observation with the current
// database generation.
func (f *nativeFixture) restoreObservationGeneration(agentID string) {
	f.t.Helper()
	f.exec(`UPDATE cairn.agent_session_delivery_observation SET database_generation=(SELECT generation FROM cairn.retrieval_generation WHERE singleton) WHERE agent_id=$1`, agentID)
}

// fixtureFor binds the time-travel methods to an existing store for tests
// that receive their stores from another scaffold.
func fixtureFor(t *testing.T, s *Store) *nativeFixture {
	t.Helper()
	return &nativeFixture{t: t, receiver: s}
}

// scheduleDue moves a scheduled occurrence's not-before time into the past.
func (f *nativeFixture) scheduleDue(occurrenceID string) {
	f.t.Helper()
	f.exec(`UPDATE cairn.agent_schedule SET not_before=clock_timestamp()-interval '1 second' WHERE occurrence_id=$1`, occurrenceID)
}
