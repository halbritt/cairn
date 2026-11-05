package core

import (
	"context"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// waitTestDB creates an isolated disposable database on the test PostgreSQL cluster.
func waitTestDB(t *testing.T) (*Store, func(Channel) *Store) {
	t.Helper()
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("PostgreSQL integration test: run make test-integration")
	}
	ctx := context.Background()
	host := testStore(t, Channel{Principal: "operator:wait-host", Operator: true})

	dbName := "cairn_wait_" + strings.ReplaceAll(uuid.NewString(), "-", "")[:16]
	if _, err := host.pool.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{dbName}.Sanitize()); err != nil {
		t.Fatalf("create wait db %s: %v", dbName, err)
	}
	t.Cleanup(func() {
		if _, err := host.pool.Exec(context.Background(), "DROP DATABASE IF EXISTS "+pgx.Identifier{dbName}.Sanitize()+" WITH (FORCE)"); err != nil {
			t.Errorf("drop wait db %s: %v", dbName, err)
		}
	})

	poolConfig, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	poolConfig.ConnConfig.Database = dbName

	openStore := func(ch Channel) *Store {
		pool, err := pgxpool.NewWithConfig(ctx, poolConfig)
		if err != nil {
			t.Fatal(err)
		}
		s := &Store{pool: pool, channel: ch}
		t.Cleanup(s.Close)
		return s
	}

	bootstrap := openStore(Channel{Principal: "operator:bootstrap", Operator: true})
	if err := bootstrap.Migrate(ctx); err != nil {
		t.Fatalf("migrate wait db %s: %v", dbName, err)
	}
	return bootstrap, openStore
}

func waitFixture(t *testing.T, openStore func(Channel) *Store) (*Store, *Store, AgentInstance, AgentInstance, *Store, *Store, Destination) {
	t.Helper()
	ctx := context.Background()
	repo := "repo:" + uuid.NewString()

	profileA := openStore(Channel{Principal: "agent:profile-a-" + repo, Repo: repo})
	profileB := openStore(Channel{Principal: "agent:profile-b-" + repo, Repo: repo})
	dest := Destination{Name: "local", AllowLocal: true}

	regA := RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Binding:         "binding-a",
		NativeSessionID: "native-a-" + uuid.NewString(),
		Metadata:        AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"},
	}
	agentA, err := profileA.RegisterAgent(ctx, regA, dest)
	if err != nil {
		t.Fatalf("register agent A: %v", err)
	}

	regB := RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Binding:         "binding-b",
		NativeSessionID: "native-b-" + uuid.NewString(),
		Metadata:        AgentMetadata{Harness: "claude", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"},
	}
	agentB, err := profileB.RegisterAgent(ctx, regB, dest)
	if err != nil {
		t.Fatalf("register agent B: %v", err)
	}

	sessionStoreA, err := profileA.ForAgentSession(AgentSessionRef{AgentID: agentA.AgentID, ExecutionID: agentA.ExecutionID}, dest)
	if err != nil {
		t.Fatalf("forAgentSession A: %v", err)
	}
	sessionStoreB, err := profileB.ForAgentSession(AgentSessionRef{AgentID: agentB.AgentID, ExecutionID: agentB.ExecutionID}, dest)
	if err != nil {
		t.Fatalf("forAgentSession B: %v", err)
	}

	return profileA, profileB, agentA, agentB, sessionStoreA, sessionStoreB, dest
}

func publishTestRequest(t *testing.T, ctx context.Context, sessionStore *Store, repo, destAgentUUID, corrID string, dest Destination) AgentEvent {
	t.Helper()
	draft := projectNote(repo)
	draft.Sensitivity = "shareable"
	rec, err := sessionStore.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatalf("create note: %v", err)
	}
	pubRes, err := sessionStore.PublishEvent(ctx, PublishEventRequest{
		RequestID:     uuid.NewString(),
		Repo:          repo,
		Kind:          "request",
		Ref:           RecordVersionRef{RecordID: rec.RecordID, Version: 1},
		Destination:   EventDestination{Type: "agent", Name: "agent/" + destAgentUUID},
		CorrelationID: corrID,
	}, dest)
	if err != nil {
		t.Fatalf("publish request: %v", err)
	}
	return pubRes
}

func publishTestResponse(t *testing.T, ctx context.Context, sessionStore *Store, repo, destAgentUUID, causationID, corrID, kind string, dest Destination) AgentEvent {
	t.Helper()
	draft := projectNote(repo)
	draft.Sensitivity = "shareable"
	rec, err := sessionStore.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatalf("create response note: %v", err)
	}
	respRes, err := sessionStore.PublishEvent(ctx, PublishEventRequest{
		RequestID:     uuid.NewString(),
		Repo:          repo,
		Kind:          kind,
		Ref:           RecordVersionRef{RecordID: rec.RecordID, Version: 1},
		Destination:   EventDestination{Type: "agent", Name: "agent/" + destAgentUUID},
		CausationID:   causationID,
		CorrelationID: corrID,
	}, dest)
	if err != nil {
		t.Fatalf("publish response: %v", err)
	}
	return respRes
}

// AC-18: Response/closure before registration satisfies from retained state;
// a concurrent registration/publication loses no notification. Both absent and present
// correlation are checked exactly; handling alone does not satisfy a wait.
func TestAC18_ResponseBeforeRegistrationAndCorrelationMatching(t *testing.T) {
	bootstrap, openStore := waitTestDB(t)
	ctx := context.Background()

	_, _, agentA, agentB, sessionA, sessionB, dest := waitFixture(t, openStore)

	// Part A: Feature disabled by default (WAIT-1)
	req1 := CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: uuid.NewString(),
		Deadline:            time.Now().Add(10 * time.Minute),
	}
	_, err := sessionA.CreateWait(ctx, req1, dest)
	requireCode(t, err, "FEATURE_DISABLED")

	// Enable feature
	operator := openStore(Channel{Principal: "operator:test", Operator: true})
	if _, err = operator.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatalf("enable coordination-waits: %v", err)
	}

	// Part B: Response published before registration satisfies from retained state
	corrID := uuid.NewString()
	pubReq := publishTestRequest(t, ctx, sessionA, agentA.Repo, agentB.AgentID, corrID, dest)
	// B claims request
	nextB, err := sessionB.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil || nextB.Delivery == nil {
		t.Fatalf("session B next event: %+v %v", nextB, err)
	}
	// B publishes response BEFORE A creates wait
	publishTestResponse(t, ctx, sessionB, agentA.Repo, agentA.AgentID, pubReq.EventID, corrID, "response", dest)

	// A creates wait after response exists
	wait1, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, dest)
	if err != nil {
		t.Fatalf("create wait after response: %v", err)
	}
	if wait1.State != WaitStateSatisfied || wait1.TerminalReason != "response" || wait1.ResponseRef == nil {
		t.Fatalf("expected immediate satisfaction from retained state: %+v", wait1)
	}

	// Part C: Correlation ID matching
	corrX := uuid.NewString()
	pubReqX := publishTestRequest(t, ctx, sessionA, agentA.Repo, agentB.AgentID, corrX, dest)
	waitCorr, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReqX.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, dest)
	if err != nil {
		t.Fatalf("create wait for correlation test: %v", err)
	}
	if waitCorr.State != WaitStateWaiting {
		t.Fatalf("expected waiting state: %+v", waitCorr)
	}

	// Publish response with wrong correlation ID Y
	corrY := uuid.NewString()
	publishTestResponse(t, ctx, sessionB, agentA.Repo, agentA.AgentID, pubReqX.EventID, corrY, "response", dest)
	gotWait, err := sessionA.GetWait(ctx, GetWaitRequest{WaitUUID: waitCorr.WaitUUID, Collection: agentA.Repo}, dest)
	if err != nil || gotWait.State != WaitStateWaiting {
		t.Fatalf("wait should remain waiting when correlation differs: %+v %v", gotWait, err)
	}

	// Now publish response with correct correlation ID X
	publishTestResponse(t, ctx, sessionB, agentA.Repo, agentA.AgentID, pubReqX.EventID, corrX, "response", dest)
	gotWaitResolved, err := sessionA.GetWait(ctx, GetWaitRequest{WaitUUID: waitCorr.WaitUUID, Collection: agentA.Repo}, dest)
	if err != nil || gotWaitResolved.State != WaitStateSatisfied {
		t.Fatalf("wait should satisfy when correlation matches: %+v %v", gotWaitResolved, err)
	}

	// Part D: Handling/ack alone does not satisfy
	pubReqAck := publishTestRequest(t, ctx, sessionA, agentA.Repo, agentB.AgentID, "", dest)
	waitAck, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReqAck.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, dest)
	if err != nil {
		t.Fatalf("create wait for ack test: %v", err)
	}
	var nextAck NextEventResult
	for {
		res, err := sessionB.NextEvent(ctx, NextEventRequest{}, dest)
		if err != nil || res.Delivery == nil {
			t.Fatalf("next event: %+v %v", res, err)
		}
		if res.Delivery.Event.EventID == pubReqAck.EventID {
			nextAck = res
			break
		}
		_, _ = sessionB.CompleteEvent(ctx, CompleteEventRequest{
			RequestID:   uuid.NewString(),
			DeliveryID:  res.Delivery.DeliveryID,
			LeaseID:     res.Delivery.LeaseID,
			Disposition: "handled",
		}, dest)
	}
	// Acknowledge delivery
	_, err = sessionB.CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  nextAck.Delivery.DeliveryID,
		LeaseID:     nextAck.Delivery.LeaseID,
		Disposition: "handled",
	}, dest)
	if err != nil {
		t.Fatalf("ack delivery: %v", err)
	}
	// Wait MUST remain waiting: handling alone does not satisfy!
	gotAckWait, err := sessionA.GetWait(ctx, GetWaitRequest{WaitUUID: waitAck.WaitUUID, Collection: agentA.Repo}, dest)
	if err != nil || gotAckWait.State != WaitStateWaiting {
		t.Fatalf("handling alone must not satisfy wait: %+v %v", gotAckWait, err)
	}
	_ = bootstrap
}

// AC-19: A first qualifying failure report is a response without becoming task success.
// Wrong responder, target, dependency owner or unavailable source refuses/is restricted according to the design.
func TestAC19_FailureReportAndAuthorizationRefusals(t *testing.T) {
	_, openStore := waitTestDB(t)
	ctx := context.Background()

	operator := openStore(Channel{Principal: "operator:test", Operator: true})
	if _, err := operator.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatalf("enable waits: %v", err)
	}

	_, profileB, agentA, agentB, sessionA, sessionB, dest := waitFixture(t, openStore)

	// Register a third agent C
	regC := RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Binding:         "binding-c",
		NativeSessionID: "native-c-" + uuid.NewString(),
		Metadata:        AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"},
	}
	agentC, err := profileB.RegisterAgent(ctx, regC, dest)
	if err != nil {
		t.Fatalf("register agent C: %v", err)
	}
	sessionC, err := profileB.ForAgentSession(AgentSessionRef{AgentID: agentC.AgentID, ExecutionID: agentC.ExecutionID}, dest)
	if err != nil {
		t.Fatalf("session C store: %v", err)
	}

	// 1. Qualifying failure report satisfies wait with terminal_reason='response'
	pubReq := publishTestRequest(t, ctx, sessionA, agentA.Repo, agentB.AgentID, "", dest)
	waitFail, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, dest)
	if err != nil {
		t.Fatalf("create wait for failure test: %v", err)
	}

	// B publishes a failure event
	publishTestResponse(t, ctx, sessionB, agentA.Repo, agentA.AgentID, pubReq.EventID, "", "failure", dest)
	gotWait, err := sessionA.GetWait(ctx, GetWaitRequest{WaitUUID: waitFail.WaitUUID, Collection: agentA.Repo}, dest)
	if err != nil || gotWait.State != WaitStateSatisfied || gotWait.TerminalReason != "response" {
		t.Fatalf("failure event should satisfy wait: %+v %v", gotWait, err)
	}

	// 2. Wrong responder: request sent to B, but C tries to respond
	pubReq2 := publishTestRequest(t, ctx, sessionA, agentA.Repo, agentB.AgentID, "", dest)
	waitWrong, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq2.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, dest)
	if err != nil {
		t.Fatalf("create wait: %v", err)
	}
	// 2. Wrong responder: request sent to B, but C tries to respond with causationID -> refused with NOT_FOUND
	draftC := projectNote(agentA.Repo)
	recC, err := sessionC.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draftC})
	if err != nil {
		t.Fatalf("create note C: %v", err)
	}
	_, err = sessionC.PublishEvent(ctx, PublishEventRequest{
		RequestID:     uuid.NewString(),
		Repo:          agentA.Repo,
		Kind:          "response",
		Ref:           RecordVersionRef{RecordID: recC.RecordID, Version: 1},
		Destination:   EventDestination{Type: "agent", Name: "agent/" + agentA.AgentID},
		CausationID:   pubReq2.EventID,
		CorrelationID: "",
	}, dest)
	requireCode(t, err, "NOT_FOUND")

	gotWrong, err := sessionA.GetWait(ctx, GetWaitRequest{WaitUUID: waitWrong.WaitUUID, Collection: agentA.Repo}, dest)
	if err != nil || gotWrong.State != WaitStateWaiting {
		t.Fatalf("wrong responder must not satisfy wait: %+v %v", gotWrong, err)
	}

	// 3. Dependency owner refusal: C tries to create wait on dependency owned by A
	_, err = sessionC.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq2.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, dest)
	requireCode(t, err, "AUTHORITY_DENIED")

	// 4. Operator cannot register wait directly (WAIT-2)
	_, err = operator.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq2.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, dest)
	requireCode(t, err, "AUTHORITY_DENIED")

	// 5. Foreign-owner isolation: B cannot GetWait, ListWaits, or CancelWait on A's wait (WAIT-3)
	_, err = sessionB.GetWait(ctx, GetWaitRequest{WaitUUID: waitFail.WaitUUID, Collection: agentA.Repo}, dest)
	requireCode(t, err, "AUTHORITY_DENIED")

	listB, err := sessionB.ListWaits(ctx, ListWaitsRequest{Collection: agentA.Repo, OwnerAgentUUID: agentA.AgentID}, dest)
	requireCode(t, err, "AUTHORITY_DENIED")
	_ = listB

	_, err = sessionB.CancelWait(ctx, CancelWaitRequest{RequestID: uuid.NewString(), WaitUUID: waitFail.WaitUUID, Collection: agentA.Repo}, dest)
	requireCode(t, err, "AUTHORITY_DENIED")
}

// AC-20: Deadline, cancellation and closure races have one documented winner;
// a timely retained event is not converted into timeout by scheduler downtime.
func TestAC20_DeadlineAndSchedulerDowntimePreservesTimelyResponse(t *testing.T) {
	bootstrap, openStore := waitTestDB(t)
	ctx := context.Background()

	operator := openStore(Channel{Principal: "operator:test", Operator: true})
	if _, err := operator.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatalf("enable waits: %v", err)
	}

	_, _, agentA, agentB, sessionA, sessionB, dest := waitFixture(t, openStore)

	// 1. Timely response: created_at < deadline satisfies wait
	pubReq := publishTestRequest(t, ctx, sessionA, agentA.Repo, agentB.AgentID, "", dest)
	wait1, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq.EventID,
		Deadline:            time.Now().Add(5 * time.Second),
	}, dest)
	if err != nil {
		t.Fatalf("create wait: %v", err)
	}

	// Publish response immediately (before deadline)
	publishTestResponse(t, ctx, sessionB, agentA.Repo, agentA.AgentID, pubReq.EventID, "", "response", dest)
	got1, err := sessionA.GetWait(ctx, GetWaitRequest{WaitUUID: wait1.WaitUUID, Collection: agentA.Repo}, dest)
	if err != nil || got1.State != WaitStateSatisfied {
		t.Fatalf("timely response should satisfy: %+v %v", got1, err)
	}

	// 2. Scheduler downtime preserves timely response:
	// Suppose a response arrived at T1 < deadline, but wait was not resolved by inline hook (or overdue).
	// When scheduler sweeps, it MUST evaluate whether an eligible response occurred before deadline,
	// and SATISFY the wait instead of timing it out!
	pubReq2 := publishTestRequest(t, ctx, sessionA, agentA.Repo, agentB.AgentID, "", dest)
	wait2, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq2.EventID,
		Deadline:            time.Now().Add(5 * time.Second),
	}, dest)
	if err != nil {
		t.Fatalf("create wait2: %v", err)
	}

	// Publish timely response
	resp2 := publishTestResponse(t, ctx, sessionB, agentA.Repo, agentA.AgentID, pubReq2.EventID, "", "response", dest)

	// Simulate that scheduler was down and deadline passed with wait still in 'waiting' state:
	// Set wait state back to 'waiting', terminal_reason to '', and deadline to 100ms AFTER response, but in the PAST relative to now!
	_, err = bootstrap.pool.Exec(ctx, `UPDATE cairn.agent_wait 
SET state='waiting', terminal_reason='', resolved_at=NULL, 
    deadline=clock_timestamp() - interval '1 second'
WHERE wait_uuid=$1`, wait2.WaitUUID)
	if err != nil {
		t.Fatalf("simulate downtime: %v", err)
	}
	_, err = bootstrap.pool.Exec(ctx, `UPDATE cairn.agent_event 
SET created_at=clock_timestamp() - interval '2 seconds'
WHERE event_id=$1`, resp2.EventID)
	if err != nil {
		t.Fatalf("simulate downtime event: %v", err)
	}

	// Wait is now overdue (deadline < now), but response.created_at < deadline!
	// Sweep timeouts:
	sweptSatisfied, err := operator.SweepWaitTimeouts(ctx, agentA.Repo)
	if err != nil {
		t.Fatalf("sweep wait timeouts: %v", err)
	}
	_ = sweptSatisfied

	// The sweep MUST have satisfied the wait with 'response', NOT 'timeout'!
	got2, err := sessionA.GetWait(ctx, GetWaitRequest{WaitUUID: wait2.WaitUUID, Collection: agentA.Repo}, dest)
	if err != nil || got2.State != WaitStateSatisfied || got2.TerminalReason != "response" {
		t.Fatalf("timely response before deadline must satisfy wait on sweep: %+v %v", got2, err)
	}

	// Now test scheduler sweep on overdue wait with no response:
	pubReq3 := publishTestRequest(t, ctx, sessionA, agentA.Repo, agentB.AgentID, "", dest)
	wait3, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq3.EventID,
		Deadline:            time.Now().Add(100 * time.Millisecond),
	}, dest)
	if err != nil {
		t.Fatalf("create wait3: %v", err)
	}
	time.Sleep(200 * time.Millisecond)
	// Run sweep:
	swept, err := operator.SweepWaitTimeouts(ctx, agentA.Repo)
	if err != nil {
		t.Fatalf("sweep wait timeouts: %v", err)
	}
	if swept == 0 {
		t.Fatalf("expected at least 1 wait swept, got %d", swept)
	}
	got3, err := sessionA.GetWait(ctx, GetWaitRequest{WaitUUID: wait3.WaitUUID, Collection: agentA.Repo}, dest)
	if err != nil || got3.State != WaitStateTimedOut || got3.TerminalReason != "timeout" {
		t.Fatalf("expected timed_out: %+v %v", got3, err)
	}

	// 3. Cancellation race: once cancelled, cannot be satisfied by later response
	pubReq4 := publishTestRequest(t, ctx, sessionA, agentA.Repo, agentB.AgentID, "", dest)
	wait4, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq4.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, dest)
	if err != nil {
		t.Fatalf("create wait4: %v", err)
	}
	// Cancel wait
	cancelled, err := sessionA.CancelWait(ctx, CancelWaitRequest{
		RequestID:  uuid.NewString(),
		WaitUUID:   wait4.WaitUUID,
		Collection: agentA.Repo,
	}, dest)
	if err != nil || cancelled.State != WaitStateCancelled {
		t.Fatalf("cancel wait: %+v %v", cancelled, err)
	}
	// Late response arrives after cancellation
	publishTestResponse(t, ctx, sessionB, agentA.Repo, agentA.AgentID, pubReq4.EventID, "", "response", dest)
	got4, err := sessionA.GetWait(ctx, GetWaitRequest{WaitUUID: wait4.WaitUUID, Collection: agentA.Repo}, dest)
	if err != nil || got4.State != WaitStateCancelled {
		t.Fatalf("cancelled wait must remain cancelled: %+v %v", got4, err)
	}
}

// AC-21: Crash between resolution and outbox processing recovers to one notice.
// Crash after publication/lost response does not allocate a second source/event.
func TestAC21_CrashRecoveryAndOutboxIdempotency(t *testing.T) {
	bootstrap, openStore := waitTestDB(t)
	ctx := context.Background()

	operator := openStore(Channel{Principal: "operator:test", Operator: true})
	if _, err := operator.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatalf("enable waits: %v", err)
	}

	_, _, agentA, agentB, sessionA, sessionB, dest := waitFixture(t, openStore)

	pubReq := publishTestRequest(t, ctx, sessionA, agentA.Repo, agentB.AgentID, "", dest)
	wait, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, dest)
	if err != nil {
		t.Fatalf("create wait: %v", err)
	}

	// Resolve wait by publishing response
	publishTestResponse(t, ctx, sessionB, agentA.Repo, agentA.AgentID, pubReq.EventID, "", "response", dest)

	// Verify outbox state is pending
	gotWait, err := sessionA.GetWait(ctx, GetWaitRequest{WaitUUID: wait.WaitUUID, Collection: agentA.Repo}, dest)
	if err != nil || gotWait.OutboxState != OutboxStatePending {
		t.Fatalf("expected pending outbox: %+v %v", gotWait, err)
	}

	// Drain outbox: first execution
	drained, err := operator.DrainWaitOutbox(ctx, agentA.Repo, 50)
	if err != nil || drained != 1 {
		t.Fatalf("drain outbox 1: %d %v", drained, err)
	}

	// Verify outbox is now published
	gotWait2, err := sessionA.GetWait(ctx, GetWaitRequest{WaitUUID: wait.WaitUUID, Collection: agentA.Repo}, dest)
	if err != nil || gotWait2.OutboxState != OutboxStatePublished {
		t.Fatalf("expected published outbox: %+v %v", gotWait2, err)
	}

	// Assert exactly ONE notice event exists in database with the reserved UUID
	var eventCount int
	err = bootstrap.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event WHERE event_id=$1`, wait.ReservedNoticeEventUUID).Scan(&eventCount)
	if err != nil || eventCount != 1 {
		t.Fatalf("expected exactly 1 notice event: count=%d %v", eventCount, err)
	}

	// Assert exactly ONE delivery exists for this notice event
	var deliveryCount int
	err = bootstrap.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_delivery WHERE event_id=$1`, wait.ReservedNoticeEventUUID).Scan(&deliveryCount)
	if err != nil || deliveryCount != 1 {
		t.Fatalf("expected exactly 1 delivery: count=%d %v", deliveryCount, err)
	}

	// Assert exactly ONE source record exists with ReservedSourceRecordUUID
	var recCount, verCount int
	err = bootstrap.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.memory_record WHERE record_id=$1`, wait.ReservedSourceRecordUUID).Scan(&recCount)
	if err != nil || recCount != 1 {
		t.Fatalf("expected exactly 1 source memory_record: count=%d %v", recCount, err)
	}
	err = bootstrap.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.record_version WHERE record_id=$1`, wait.ReservedSourceRecordUUID).Scan(&verCount)
	if err != nil || verCount != 1 {
		t.Fatalf("expected exactly 1 source record_version: count=%d %v", verCount, err)
	}

	// Assert exactly 1 notice event in entire collection
	var totalNotices int
	err = bootstrap.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event WHERE repo=$1 AND kind='notice'`, agentA.Repo).Scan(&totalNotices)
	if err != nil || totalNotices != 1 {
		t.Fatalf("expected exactly 1 total notice in collection, got %d", totalNotices)
	}

	// Crash/retry boundary: simulate uncommitted or crashed outbox drain by reopening outbox to 'pending'
	_, err = bootstrap.pool.Exec(ctx, `UPDATE cairn.agent_wait_outbox SET state='pending' WHERE wait_uuid=$1`, wait.WaitUUID)
	if err != nil {
		t.Fatalf("reopen outbox: %v", err)
	}

	// Re-run drain: should idempotently succeed without creating duplicate events or sources
	drainedAgain, err := operator.DrainWaitOutbox(ctx, agentA.Repo, 50)
	if err != nil {
		t.Fatalf("second drain after crash recovery: %v", err)
	}
	_ = drainedAgain

	// Still exactly 1 event and 1 source record
	err = bootstrap.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event WHERE event_id=$1`, wait.ReservedNoticeEventUUID).Scan(&eventCount)
	if err != nil || eventCount != 1 {
		t.Fatalf("expected exactly 1 notice event after recovery: count=%d", eventCount)
	}
	err = bootstrap.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.memory_record WHERE record_id=$1`, wait.ReservedSourceRecordUUID).Scan(&recCount)
	if err != nil || recCount != 1 {
		t.Fatalf("expected exactly 1 source record after recovery: count=%d", recCount)
	}
}

// AC-22: A real disposable native recipient reads and acknowledges the exact generated notice.
// Acknowledgment, wait satisfaction and original task outcome are distinct.
func TestAC22_RealNativeRecipientDeliveryAndAcknowledgment(t *testing.T) {
	_, openStore := waitTestDB(t)
	ctx := context.Background()

	operator := openStore(Channel{Principal: "operator:test", Operator: true})
	if _, err := operator.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatalf("enable waits: %v", err)
	}

	profileA, _, agentA, agentB, sessionA, sessionB, dest := waitFixture(t, openStore)

	pubReq := publishTestRequest(t, ctx, sessionA, agentA.Repo, agentB.AgentID, "", dest)
	wait, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, dest)
	if err != nil {
		t.Fatalf("create wait: %v", err)
	}

	// Respondent publishes response
	publishTestResponse(t, ctx, sessionB, agentA.Repo, agentA.AgentID, pubReq.EventID, "", "response", dest)

	// Drain outbox to publish notice
	drained, err := operator.DrainWaitOutbox(ctx, agentA.Repo, 50)
	if err != nil || drained != 1 {
		t.Fatalf("drain outbox: %d %v", drained, err)
	}

	// Recipient (sessionA) reads its session inbox
	// First event in inbox is the response event from B
	firstDelivery, err := sessionA.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil || firstDelivery.Delivery == nil {
		t.Fatalf("session A must receive response delivery: %+v %v", firstDelivery, err)
	}
	if firstDelivery.Delivery.Event.Kind != "response" {
		t.Fatalf("expected first event to be response, got %s", firstDelivery.Delivery.Event.Kind)
	}
	_, err = sessionA.CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  firstDelivery.Delivery.DeliveryID,
		LeaseID:     firstDelivery.Delivery.LeaseID,
		Disposition: "handled",
	}, dest)
	if err != nil {
		t.Fatalf("ack response delivery: %v", err)
	}

	// 2. Real native recipient lifecycle for the wait notice:
	// A) Readiness
	ready, err := profileA.SessionInboxReady(ctx, AgentSessionRef{AgentID: agentA.AgentID, ExecutionID: agentA.ExecutionID}, dest)
	if err != nil || ready.EventID != wait.ReservedNoticeEventUUID {
		t.Fatalf("expected native readiness for notice event %s, got %+v %v", wait.ReservedNoticeEventUUID, ready, err)
	}

	// B) Begin/claim with native turn binding
	claim, err := profileA.ClaimSessionInbox(ctx, SessionInboxClaim{
		RequestID:     uuid.NewString(),
		Session:       AgentSessionRef{AgentID: agentA.AgentID, ExecutionID: agentA.ExecutionID},
		DeliveryID:    ready.DeliveryID,
		NativeTurnID:  "native-turn-wc03",
		TurnExclusive: true,
	}, dest)
	if err != nil || claim.Attempt == nil {
		t.Fatalf("claim session inbox: %+v %v", claim, err)
	}
	if claim.Attempt.Delivery.Event.EventID != wait.ReservedNoticeEventUUID {
		t.Fatalf("expected claim of notice event %s, got %s", wait.ReservedNoticeEventUUID, claim.Attempt.Delivery.Event.EventID)
	}
	if claim.Attempt.Delivery.Event.Ref.RecordID != wait.ReservedSourceRecordUUID {
		t.Fatalf("expected source record %s, got %s", wait.ReservedSourceRecordUUID, claim.Attempt.Delivery.Event.Ref.RecordID)
	}

	// C) Exact full source read
	history, err := sessionA.History(ctx, RecordHistoryRequest{
		RecordID: wait.ReservedSourceRecordUUID,
		Repo:     agentA.Repo,
		Version:  1,
	}, dest)
	if err != nil || len(history.Versions) == 0 {
		t.Fatalf("exact source read: %+v %v", history, err)
	}

	// D) Finish/acknowledge notice delivery
	ackRes, err := sessionA.CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  claim.Attempt.Delivery.DeliveryID,
		LeaseID:     claim.Attempt.Delivery.LeaseID,
		Disposition: "handled",
	}, dest)
	if err != nil || ackRes.State != "handled" {
		t.Fatalf("acknowledging notice delivery: %+v %v", ackRes, err)
	}

	// Distinctness assertion:
	// 1. Wait state: satisfied
	gotWait, err := sessionA.GetWait(ctx, GetWaitRequest{WaitUUID: wait.WaitUUID, Collection: agentA.Repo}, dest)
	if err != nil || gotWait.State != WaitStateSatisfied {
		t.Fatalf("wait state must be satisfied: %+v", gotWait)
	}
	// 2. Delivery state: handled
	if ackRes.State != "handled" {
		t.Fatalf("delivery state must be handled")
	}
	// 3. Outbox state: published
	if gotWait.OutboxState != OutboxStatePublished {
		t.Fatalf("outbox state must be published: %+v", gotWait)
	}
}

// AC-23: Legitimate conversation resume can inspect/cancel; stale execution cannot.
// Offline recipients remain addressed without starting a replacement session.
// Also covers response_group_closed predicate: normal closures satisfy, restore_fenced excluded.
func TestAC23_ConversationResumeAndOfflineDelivery(t *testing.T) {
	bootstrap, openStore := waitTestDB(t)
	ctx := context.Background()

	operator := openStore(Channel{Principal: "operator:test", Operator: true})
	if _, err := operator.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatalf("enable waits: %v", err)
	}

	profileA, _, agentA, agentB, sessionA, sessionB, dest := waitFixture(t, openStore)

	pubReq := publishTestRequest(t, ctx, sessionA, agentA.Repo, agentB.AgentID, "", dest)
	wait, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, dest)
	if err != nil {
		t.Fatalf("create wait: %v", err)
	}

	// 1. Resume agent A's conversation: new execution ID
	resumedA, err := profileA.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Binding:         "binding-a",
		NativeSessionID: agentA.NativeSessionID,
		Metadata:        agentA.Metadata,
	}, dest)
	if err != nil {
		t.Fatalf("resume agent A: %v", err)
	}
	if resumedA.ExecutionID == agentA.ExecutionID {
		t.Fatalf("resumed agent must have new execution ID")
	}

	// Stale execution (sessionA has old execution ID) tries to GetWait -> STALE_SESSION
	_, err = sessionA.GetWait(ctx, GetWaitRequest{WaitUUID: wait.WaitUUID, Collection: agentA.Repo}, dest)
	requireCode(t, err, "STALE_SESSION")

	// Stale execution tries to CancelWait -> STALE_SESSION
	_, err = sessionA.CancelWait(ctx, CancelWaitRequest{
		RequestID:  uuid.NewString(),
		WaitUUID:   wait.WaitUUID,
		Collection: agentA.Repo,
	}, dest)
	requireCode(t, err, "STALE_SESSION")

	// Resumed execution store
	resumedSessionStoreA, err := profileA.ForAgentSession(AgentSessionRef{AgentID: resumedA.AgentID, ExecutionID: resumedA.ExecutionID}, dest)
	if err != nil {
		t.Fatalf("forAgentSession resumed: %v", err)
	}

	// Resumed execution CAN inspect wait
	gotWait, err := resumedSessionStoreA.GetWait(ctx, GetWaitRequest{WaitUUID: wait.WaitUUID, Collection: agentA.Repo}, dest)
	if err != nil || gotWait.State != WaitStateWaiting {
		t.Fatalf("resumed execution must be able to inspect wait: %+v %v", gotWait, err)
	}

	// 2. Offline delivery: expire agent A's presence (set expires_at in past, but NOT stopped)
	_, err = bootstrap.pool.Exec(ctx, `UPDATE cairn.agent_session SET expires_at=clock_timestamp()-interval '2 hours' WHERE agent_id=$1`, agentA.AgentID)
	if err != nil {
		t.Fatalf("expire presence: %v", err)
	}

	// Cancel wait to resolve it with outbox notice
	cancelledWait, err := resumedSessionStoreA.CancelWait(ctx, CancelWaitRequest{
		RequestID:  uuid.NewString(),
		WaitUUID:   wait.WaitUUID,
		Collection: agentA.Repo,
	}, dest)
	if err != nil || cancelledWait.State != WaitStateCancelled {
		t.Fatalf("resumed execution cancel wait: %+v %v", cancelledWait, err)
	}

	// Outbox row is pending; drain it while recipient is offline
	drained, err := operator.DrainWaitOutbox(ctx, agentA.Repo, 50)
	if err != nil {
		t.Fatalf("drain outbox for offline recipient: %v", err)
	}
	_ = drained

	// 3. Response group closed predicate:
	draftGroup := projectNote(agentA.Repo)
	draftGroup.Sensitivity = "shareable"
	recGroup, err := resumedSessionStoreA.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draftGroup})
	if err != nil {
		t.Fatalf("create group note: %v", err)
	}
	groupReq, err := resumedSessionStoreA.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Repo:        agentA.Repo,
		Kind:        "request",
		Ref:         RecordVersionRef{RecordID: recGroup.RecordID, Version: 1},
		Destination: EventDestination{Type: "agent", Name: "agent/" + agentB.AgentID},
		ResponseGroup: &ResponseGroupSpec{
			Deadline:      time.Now().Add(5 * time.Minute),
			PartialPolicy: "all",
		},
	}, dest)
	if err != nil {
		t.Fatalf("publish response group request: %v", err)
	}

	waitGroup, err := resumedSessionStoreA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateResponseGroupClosed,
		DependencyEventUUID: groupReq.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, dest)
	if err != nil {
		t.Fatalf("create wait on response group: %v", err)
	}
	if waitGroup.State != WaitStateWaiting {
		t.Fatalf("expected waiting on group: %+v", waitGroup)
	}

	// B responds -> group all_responded and closes
	publishTestResponse(t, ctx, sessionB, agentA.Repo, resumedA.AgentID, groupReq.EventID, groupReq.EventID, "response", dest)

	gotWaitGroup, err := resumedSessionStoreA.GetWait(ctx, GetWaitRequest{WaitUUID: waitGroup.WaitUUID, Collection: agentA.Repo}, dest)
	if err != nil || gotWaitGroup.State != WaitStateSatisfied || gotWaitGroup.TerminalReason != "group_closed" {
		t.Fatalf("response group closure must satisfy wait: %+v %v", gotWaitGroup, err)
	}

	// Test restore_fenced closure does NOT satisfy response_group_closed
	recGroup2, err := resumedSessionStoreA.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draftGroup})
	if err != nil {
		t.Fatalf("create group note 2: %v", err)
	}
	groupReq2, err := resumedSessionStoreA.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Repo:        agentA.Repo,
		Kind:        "request",
		Ref:         RecordVersionRef{RecordID: recGroup2.RecordID, Version: 1},
		Destination: EventDestination{Type: "agent", Name: "agent/" + agentB.AgentID},
		ResponseGroup: &ResponseGroupSpec{
			Deadline:      time.Now().Add(5 * time.Minute),
			PartialPolicy: "all",
		},
	}, dest)
	if err != nil {
		t.Fatalf("publish group 2: %v", err)
	}
	waitGroup2, err := resumedSessionStoreA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateResponseGroupClosed,
		DependencyEventUUID: groupReq2.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, dest)
	if err != nil {
		t.Fatalf("create wait group 2: %v", err)
	}
	// Simulate restore fence on the group
	_, err = bootstrap.pool.Exec(ctx, `UPDATE cairn.agent_response_group SET state='incomplete', code='restore_fenced', closed_at=clock_timestamp() WHERE event_id=$1`, groupReq2.EventID)
	if err != nil {
		t.Fatalf("fence group: %v", err)
	}
	gotWaitGroup2, err := resumedSessionStoreA.GetWait(ctx, GetWaitRequest{WaitUUID: waitGroup2.WaitUUID, Collection: agentA.Repo}, dest)
	if err != nil {
		t.Fatalf("get wait group 2: %v", err)
	}
	if gotWaitGroup2.State == WaitStateSatisfied {
		t.Fatalf("restore_fenced group must NEVER satisfy wait!")
	}
}

// AC-24: Restore of waiting, resolved-unpublished and published-unacknowledged cases
// does not automatically repeat a continuation; state remains inspectable.
func TestAC24_RestoreFencingOfWaitsAndOutbox(t *testing.T) {
	bootstrap, openStore := waitTestDB(t)
	ctx := context.Background()

	operator := openStore(Channel{Principal: "operator:test", Operator: true})
	if _, err := operator.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatalf("enable waits: %v", err)
	}

	_, _, agentA, agentB, sessionA, sessionB, dest := waitFixture(t, openStore)

	// Wait 1: in 'waiting' state
	pubReq1 := publishTestRequest(t, ctx, sessionA, agentA.Repo, agentB.AgentID, "", dest)
	wait1, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq1.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, dest)
	if err != nil {
		t.Fatalf("create wait 1: %v", err)
	}

	// Wait 2: resolved, outbox in 'pending' state
	pubReq2 := publishTestRequest(t, ctx, sessionA, agentA.Repo, agentB.AgentID, "", dest)
	wait2, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq2.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, dest)
	if err != nil {
		t.Fatalf("create wait 2: %v", err)
	}
	publishTestResponse(t, ctx, sessionB, agentA.Repo, agentA.AgentID, pubReq2.EventID, "", "response", dest)

	// Trigger restore fence
	fence, err := operator.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "simulated disaster restore"})
	if err != nil {
		t.Fatalf("fence restore: %v", err)
	}
	if fence.Generation < 1 {
		t.Fatalf("expected incremented generation, got %d", fence.Generation)
	}

	// Assert Wait 1 is restore_fenced
	var w1State, w1Reason string
	err = bootstrap.pool.QueryRow(ctx, `SELECT state, terminal_reason FROM cairn.agent_wait WHERE wait_uuid=$1`, wait1.WaitUUID).Scan(&w1State, &w1Reason)
	if err != nil || w1State != WaitStateRestoreFenced || w1Reason != "restore_fenced" {
		t.Fatalf("wait1 must be restore_fenced: state=%s reason=%s %v", w1State, w1Reason, err)
	}

	// Assert Wait 2 outbox is restore_fenced
	var o2State string
	err = bootstrap.pool.QueryRow(ctx, `SELECT state FROM cairn.agent_wait_outbox WHERE wait_uuid=$1`, wait2.WaitUUID).Scan(&o2State)
	if err != nil || o2State != OutboxStateRestoreFenced {
		t.Fatalf("wait2 outbox must be restore_fenced: state=%s %v", o2State, err)
	}

	// State remains inspectable by operator
	gotW1, err := operator.GetWait(ctx, GetWaitRequest{WaitUUID: wait1.WaitUUID, Collection: agentA.Repo}, dest)
	if err != nil || gotW1.State != WaitStateRestoreFenced {
		t.Fatalf("inspect restore_fenced wait: %+v %v", gotW1, err)
	}
}

// AC-25: Restore a published, unclaimed notice, then legitimately resume the recipient;
// generic polling and native readiness both honor its persistent admission fence.
func TestAC25_PersistentAdmissionFenceBlocksPublishedNoticeAfterRestore(t *testing.T) {
	_, openStore := waitTestDB(t)
	ctx := context.Background()

	operator := openStore(Channel{Principal: "operator:test", Operator: true})
	if _, err := operator.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatalf("enable waits: %v", err)
	}

	profileA, profileB, agentA, agentB, sessionA, sessionB, dest := waitFixture(t, openStore)

	pubReq := publishTestRequest(t, ctx, sessionA, agentA.Repo, agentB.AgentID, "", dest)
	wait, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, dest)
	if err != nil {
		t.Fatalf("create wait: %v", err)
	}
	_ = wait.WaitUUID

	// Resolve wait
	publishTestResponse(t, ctx, sessionB, agentA.Repo, agentA.AgentID, pubReq.EventID, "", "response", dest)

	// 1. Drain/acknowledge all earlier deliveries (e.g. response from B) so ONLY the wait notice remains in inbox
	for {
		earlier, err := sessionA.NextEvent(ctx, NextEventRequest{}, dest)
		if err != nil || earlier.Delivery == nil {
			break
		}
		_, err = sessionA.CompleteEvent(ctx, CompleteEventRequest{
			RequestID:   uuid.NewString(),
			DeliveryID:  earlier.Delivery.DeliveryID,
			LeaseID:     earlier.Delivery.LeaseID,
			Disposition: "handled",
		}, dest)
		if err != nil {
			t.Fatalf("ack earlier event: %v", err)
		}
	}

	// Drain outbox -> notice published to recipient inbox
	drained, err := operator.DrainWaitOutbox(ctx, agentA.Repo, 50)
	if err != nil || drained != 1 {
		t.Fatalf("drain outbox: %d %v", drained, err)
	}

	// 2. Trigger restore fence!
	fence, err := operator.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "test restore"})
	if err != nil {
		t.Fatalf("fence restore: %v", err)
	}
	_ = fence

	// 3. Resume recipient legitimately
	resumedA, err := profileA.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Binding:         "binding-a",
		NativeSessionID: agentA.NativeSessionID,
		Metadata:        agentA.Metadata,
	}, dest)
	if err != nil {
		t.Fatalf("resume recipient: %v", err)
	}
	resumedStore, err := profileA.ForAgentSession(AgentSessionRef{AgentID: resumedA.AgentID, ExecutionID: resumedA.ExecutionID}, dest)
	if err != nil {
		t.Fatalf("forAgentSession: %v", err)
	}

	// 4. Prove persistent admission fence blocks the published unclaimed notice:
	// A) Generic NextEvent returns NO delivery!
	next, err := resumedStore.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil {
		t.Fatalf("next event: %v", err)
	}
	if next.Delivery != nil {
		t.Fatalf("persistent admission fence failed on NextEvent: %+v", next.Delivery)
	}

	// B) Native SessionInboxReady returns NO delivery!
	ready, err := profileA.SessionInboxReady(ctx, AgentSessionRef{AgentID: resumedA.AgentID, ExecutionID: resumedA.ExecutionID}, dest)
	if err != nil {
		t.Fatalf("session inbox ready: %v", err)
	}
	if ready.DeliveryID != "" || ready.EventID != "" {
		t.Fatalf("persistent admission fence failed on SessionInboxReady: %+v", ready)
	}

	// C) Native SessionInboxPending returns 0 pending!
	pending, err := profileA.SessionInboxPending(ctx, AgentSessionRef{AgentID: resumedA.AgentID, ExecutionID: resumedA.ExecutionID}, dest)
	if err != nil {
		t.Fatalf("session inbox pending: %v", err)
	}
	if pending.Notices != 0 {
		t.Fatalf("persistent admission fence failed on SessionInboxPending: count=%d", pending.Notices)
	}

	// 5. Positive control: Fresh post-restore wait succeeds and notice is delivered!
	resumedB, err := profileB.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Binding:         "binding-b",
		NativeSessionID: agentB.NativeSessionID,
		Metadata:        agentB.Metadata,
	}, dest)
	if err != nil {
		t.Fatalf("resume B: %v", err)
	}
	resumedSessionB, err := profileB.ForAgentSession(AgentSessionRef{AgentID: resumedB.AgentID, ExecutionID: resumedB.ExecutionID}, dest)
	if err != nil {
		t.Fatalf("forAgentSession resumed B: %v", err)
	}

	pubReqPost := publishTestRequest(t, ctx, resumedStore, agentA.Repo, resumedB.AgentID, "", dest)
	waitPost, err := resumedStore.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReqPost.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, dest)
	if err != nil {
		t.Fatalf("create post-restore wait: %v", err)
	}
	publishTestResponse(t, ctx, resumedSessionB, agentA.Repo, resumedA.AgentID, pubReqPost.EventID, "", "response", dest)
	drainedPost, err := operator.DrainWaitOutbox(ctx, agentA.Repo, 50)
	if err != nil || drainedPost != 1 {
		t.Fatalf("drain post-restore outbox: %d %v", drainedPost, err)
	}
	// Drain the response event from B
	nextPostResp, err := resumedStore.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil || nextPostResp.Delivery == nil {
		t.Fatalf("next post resp: %+v %v", nextPostResp, err)
	}
	_, _ = resumedStore.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: nextPostResp.Delivery.DeliveryID, LeaseID: nextPostResp.Delivery.LeaseID, Disposition: "handled"}, dest)

	// Post-restore wait notice IS delivered!
	nextPostNotice, err := resumedStore.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil || nextPostNotice.Delivery == nil || nextPostNotice.Delivery.Event.EventID != waitPost.ReservedNoticeEventUUID {
		t.Fatalf("post-restore wait notice must be delivered! %+v %v", nextPostNotice, err)
	}
}

// AC-26: A revoked binding or widened successor profile cannot expose a result
// beyond the wait's original authenticated destination and sensitivity ceiling.
func TestAC26_DestinationCeilingAndSensitivityRevalidation(t *testing.T) {
	bootstrap, openStore := waitTestDB(t)
	ctx := context.Background()

	operator := openStore(Channel{Principal: "operator:test", Operator: true})
	if _, err := operator.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatalf("enable waits: %v", err)
	}

	repo := "repo:" + uuid.NewString()
	profileA := openStore(Channel{Principal: "agent:profile-a-" + repo, Repo: repo})
	profileB := openStore(Channel{Principal: "agent:profile-b-" + repo, Repo: repo})

	// Waiter session registered with hosted destination (AllowLocal = false)
	hostedDest := Destination{Name: "hosted", AllowLocal: false}
	localDest := Destination{Name: "local", AllowLocal: true}

	regA := RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Binding:         "binding-a",
		NativeSessionID: "native-a-" + uuid.NewString(),
		Metadata:        AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"},
	}
	agentA, err := profileA.RegisterAgent(ctx, regA, hostedDest)
	if err != nil {
		t.Fatalf("register agent A: %v", err)
	}

	regB := RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Binding:         "binding-b",
		NativeSessionID: "native-b-" + uuid.NewString(),
		Metadata:        AgentMetadata{Harness: "claude", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"},
	}
	agentB, err := profileB.RegisterAgent(ctx, regB, localDest)
	if err != nil {
		t.Fatalf("register agent B: %v", err)
	}

	sessionA, err := profileA.ForAgentSession(AgentSessionRef{AgentID: agentA.AgentID, ExecutionID: agentA.ExecutionID}, hostedDest)
	if err != nil {
		t.Fatalf("forAgentSession A: %v", err)
	}
	sessionB, err := profileB.ForAgentSession(AgentSessionRef{AgentID: agentB.AgentID, ExecutionID: agentB.ExecutionID}, localDest)
	if err != nil {
		t.Fatalf("forAgentSession B: %v", err)
	}

	// 1. Dependency or response with 'local' sensitivity vs hosted ceiling ('shareable')
	pubReq := publishTestRequest(t, ctx, sessionA, repo, agentB.AgentID, "", hostedDest)
	wait, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, hostedDest)
	if err != nil {
		t.Fatalf("create wait: %v", err)
	}
	if wait.SensitivityCeiling != "shareable" {
		t.Fatalf("expected shareable ceiling for hosted dest: %s", wait.SensitivityCeiling)
	}

	// Respondent creates a 'local' sensitivity note for the response
	localDraft := projectNote(repo)
	localDraft.Sensitivity = "local"
	localRec, err := sessionB.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: localDraft})
	if err != nil {
		t.Fatalf("create local note: %v", err)
	}
	// Respondent publishes response with 'local' source
	_, err = sessionB.PublishEvent(ctx, PublishEventRequest{
		RequestID:     uuid.NewString(),
		Repo:          repo,
		Kind:          "response",
		Ref:           RecordVersionRef{RecordID: localRec.RecordID, Version: 1},
		Destination:   EventDestination{Type: "agent", Name: "agent/" + agentA.AgentID},
		CausationID:   pubReq.EventID,
		CorrelationID: "",
	}, localDest)
	if err != nil {
		t.Fatalf("publish local response: %v", err)
	}

	// Drain outbox: outbox drain MUST detect ceiling exceeded and mark outbox restricted!
	drained, err := operator.DrainWaitOutbox(ctx, repo, 50)
	if err != nil {
		t.Fatalf("drain outbox: %v", err)
	}
	if drained != 0 {
		t.Fatalf("expected 0 successful outbox deliveries due to ceiling restriction, got %d", drained)
	}

	var outboxState, errReason string
	err = bootstrap.pool.QueryRow(ctx, `SELECT state, error_reason FROM cairn.agent_wait_outbox WHERE wait_uuid=$1`, wait.WaitUUID).Scan(&outboxState, &errReason)
	if err != nil || outboxState != OutboxStateRestricted || errReason != "ceiling_exceeded" {
		t.Fatalf("expected outbox restricted with ceiling_exceeded: state=%s reason=%s %v", outboxState, errReason, err)
	}

	// 2. Revoked binding:
	pubReqRev := publishTestRequest(t, ctx, sessionA, repo, agentB.AgentID, "", hostedDest)
	waitRev, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReqRev.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, hostedDest)
	if err != nil {
		t.Fatalf("create waitRev: %v", err)
	}
	publishTestResponse(t, ctx, sessionB, repo, agentA.AgentID, pubReqRev.EventID, "", "response", localDest)

	// Revoke binding in the database
	_, err = bootstrap.pool.Exec(ctx, `UPDATE cairn.agent_session SET binding='revoked-binding' WHERE agent_id=$1`, agentA.AgentID)
	if err != nil {
		t.Fatalf("revoke binding: %v", err)
	}

	// Drain outbox: must mark outbox restricted with 'binding_revoked'!
	_, err = operator.DrainWaitOutbox(ctx, repo, 50)
	if err != nil {
		t.Fatalf("drain outbox on revoked binding: %v", err)
	}
	var outboxRevState, revReason string
	err = bootstrap.pool.QueryRow(ctx, `SELECT state, error_reason FROM cairn.agent_wait_outbox WHERE wait_uuid=$1`, waitRev.WaitUUID).Scan(&outboxRevState, &revReason)
	if err != nil || outboxRevState != OutboxStateRestricted || revReason != "binding_revoked" {
		t.Fatalf("expected outbox restricted with binding_revoked: state=%s reason=%s %v", outboxRevState, revReason, err)
	}

	// 3. Normal stopped presence: LeaveAgent is offline presence, NOT revocation!
	_, err = bootstrap.pool.Exec(ctx, `UPDATE cairn.agent_session SET binding='binding-a' WHERE agent_id=$1`, agentA.AgentID)
	if err != nil {
		t.Fatalf("restore binding: %v", err)
	}
	pubReqOffline := publishTestRequest(t, ctx, sessionA, repo, agentB.AgentID, "", hostedDest)
	waitOffline, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReqOffline.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, hostedDest)
	if err != nil || waitOffline.WaitUUID == "" {
		t.Fatalf("create waitOffline: %+v %v", waitOffline, err)
	}
	publishTestResponse(t, ctx, sessionB, repo, agentA.AgentID, pubReqOffline.EventID, "", "response", hostedDest)

	_, err = bootstrap.pool.Exec(ctx, `UPDATE cairn.agent_session SET stopped=true WHERE agent_id=$1`, agentA.AgentID)
	if err != nil {
		t.Fatalf("set stopped: %v", err)
	}

	// Drain outbox while stopped: offline delivery IS preserved!
	drainedOffline, err := operator.DrainWaitOutbox(ctx, repo, 50)
	if err != nil || drainedOffline != 1 {
		t.Fatalf("drain outbox for offline session must succeed: %d %v", drainedOffline, err)
	}

	// 4. Widened successor profile cannot expose local response from hosted wait registration
	gotWaitHosted, err := operator.GetWait(ctx, GetWaitRequest{WaitUUID: wait.WaitUUID, Collection: repo}, localDest)
	if err != nil {
		t.Fatalf("get wait hosted: %v", err)
	}
	if gotWaitHosted.ResponseRef != nil {
		t.Fatalf("widened successor must not expose local response from hosted wait: %+v", gotWaitHosted.ResponseRef)
	}
}

func TestConcurrentSameSessionWaitRegistration(t *testing.T) {
	_, openStore := waitTestDB(t)
	ctx := context.Background()

	operator := openStore(Channel{Principal: "operator:test", Operator: true})
	if _, err := operator.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatalf("enable waits: %v", err)
	}

	_, _, agentA, agentB, sessionA, _, dest := waitFixture(t, openStore)

	const concurrency = 5
	errCh := make(chan error, concurrency)

	for i := 0; i < concurrency; i++ {
		go func(idx int) {
			pubReq := publishTestRequest(t, ctx, sessionA, agentA.Repo, agentB.AgentID, "", dest)
			_, err := sessionA.CreateWait(ctx, CreateWaitRequest{
				RequestID:           uuid.NewString(),
				Collection:          agentA.Repo,
				PredicateKind:       WaitPredicateRequestResponse,
				DependencyEventUUID: pubReq.EventID,
				Deadline:            time.Now().Add(10 * time.Minute),
			}, dest)
			errCh <- err
		}(i)
	}

	for i := 0; i < concurrency; i++ {
		select {
		case err := <-errCh:
			if err != nil {
				t.Fatalf("concurrent wait creation failed: %v", err)
			}
		case <-time.After(5 * time.Second):
			t.Fatalf("deadlock detected during concurrent same-session wait creation!")
		}
	}
}

// AC-27: Disabling new registration continues maintenance of accepted work.
// A pure get reports overdue state without claiming it has persisted timeout.
func TestAC27_MaintenanceContinuityAndPureGetOverdue(t *testing.T) {
	bootstrap, openStore := waitTestDB(t)
	ctx := context.Background()

	operator := openStore(Channel{Principal: "operator:test", Operator: true})
	if _, err := operator.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatalf("enable waits: %v", err)
	}

	_, _, agentA, agentB, sessionA, _, dest := waitFixture(t, openStore)

	// 1. Pure get reports overdue without persisting timeout
	pubReq := publishTestRequest(t, ctx, sessionA, agentA.Repo, agentB.AgentID, "", dest)
	wait, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq.EventID,
		Deadline:            time.Now().Add(100 * time.Millisecond),
	}, dest)
	if err != nil {
		t.Fatalf("create wait: %v", err)
	}

	// Let deadline pass without running scheduler sweep
	time.Sleep(200 * time.Millisecond)

	// Pure GetWait must report Overdue: true
	gotWait, err := sessionA.GetWait(ctx, GetWaitRequest{WaitUUID: wait.WaitUUID, Collection: agentA.Repo}, dest)
	if err != nil {
		t.Fatalf("get wait: %v", err)
	}
	if !gotWait.Overdue {
		t.Fatalf("expected Overdue: true, got %+v", gotWait)
	}
	// Assert database state is STILL 'waiting' (pure get does not write or persist timeout)
	var dbState string
	err = bootstrap.pool.QueryRow(ctx, `SELECT state FROM cairn.agent_wait WHERE wait_uuid=$1`, wait.WaitUUID).Scan(&dbState)
	if err != nil || dbState != WaitStateWaiting {
		t.Fatalf("pure get must not persist timeout in database: state=%s %v", dbState, err)
	}

	// 2. Disabling new registration continues maintenance of accepted work
	if _, err = operator.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: false}); err != nil {
		t.Fatalf("disable coordination waits: %v", err)
	}

	// New wait creation fails closed with FEATURE_DISABLED
	_, err = sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          agentA.Repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, dest)
	requireCode(t, err, "FEATURE_DISABLED")

	// Maintenance continues: scheduler sweeps existing overdue wait
	swept, err := operator.SweepWaitTimeouts(ctx, agentA.Repo)
	if err != nil || swept != 1 {
		t.Fatalf("maintenance sweep under disabled registration: %d %v", swept, err)
	}

	// Wait is now persisted as timed_out
	err = bootstrap.pool.QueryRow(ctx, `SELECT state FROM cairn.agent_wait WHERE wait_uuid=$1`, wait.WaitUUID).Scan(&dbState)
	if err != nil || dbState != WaitStateTimedOut {
		t.Fatalf("wait must be timed_out after sweep: state=%s", dbState)
	}

	// Outbox drain continues maintenance
	drained, err := operator.DrainWaitOutbox(ctx, agentA.Repo, 50)
	if err != nil || drained != 1 {
		t.Fatalf("maintenance drain under disabled registration: %d %v", drained, err)
	}
}

func TestDependencySensitivityDemotionRestrictsOutbox(t *testing.T) {
	_, openStore := waitTestDB(t)
	ctx := context.Background()

	operator := openStore(Channel{Principal: "operator:test", Operator: true})
	if _, err := operator.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatalf("enable waits: %v", err)
	}

	repo := "repo:" + uuid.NewString()
	hostedDest := Destination{Name: "hosted", AllowLocal: false}
	profileA := openStore(Channel{Principal: "agent:profile-a-" + repo, Repo: repo})
	profileB := openStore(Channel{Principal: "agent:profile-b-" + repo, Repo: repo})
	agentA, err := profileA.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Binding:         "binding-a",
		NativeSessionID: "native-a-" + uuid.NewString(),
		Metadata:        AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"},
	}, hostedDest)
	if err != nil {
		t.Fatalf("register agent A: %v", err)
	}
	agentB, err := profileB.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Binding:         "binding-b",
		NativeSessionID: "native-b-" + uuid.NewString(),
		Metadata:        AgentMetadata{Harness: "claude", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"},
	}, hostedDest)
	if err != nil {
		t.Fatalf("register agent B: %v", err)
	}
	sessionA, err := profileA.ForAgentSession(AgentSessionRef{AgentID: agentA.AgentID, ExecutionID: agentA.ExecutionID}, hostedDest)
	if err != nil {
		t.Fatalf("forAgentSession A: %v", err)
	}
	sessionB, err := profileB.ForAgentSession(AgentSessionRef{AgentID: agentB.AgentID, ExecutionID: agentB.ExecutionID}, hostedDest)
	if err != nil {
		t.Fatalf("forAgentSession B: %v", err)
	}

	// 1. Create shareable dependency record and publish request
	depNote, err := sessionA.Create(ctx, CreateRequest{
		RequestID: uuid.NewString(),
		Draft: Draft{
			Kind:        "note",
			Body:        "shareable request",
			ClaimType:   "self",
			Sensitivity: "shareable",
			Scope:       Scope{Repo: repo, TaskID: "*", RunID: "*"},
		},
	})
	if err != nil {
		t.Fatalf("create dep note: %v", err)
	}
	pubReq, err := sessionA.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Repo:        repo,
		Kind:        "request",
		Ref:         RecordVersionRef{RecordID: depNote.RecordID, Version: 1},
		Destination: EventDestination{Type: "agent", Name: "agent/" + agentB.AgentID},
	}, hostedDest)
	if err != nil {
		t.Fatalf("publish request: %v", err)
	}

	// 2. Register hosted-ceiling wait
	wait, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, hostedDest)
	if err != nil {
		t.Fatalf("create wait: %v", err)
	}

	// 3. Satisfy the wait with a shareable response
	respNote, err := sessionB.Create(ctx, CreateRequest{
		RequestID: uuid.NewString(),
		Draft: Draft{
			Kind:        "note",
			Body:        "shareable response",
			ClaimType:   "self",
			Sensitivity: "shareable",
			Scope:       Scope{Repo: repo, TaskID: "*", RunID: "*"},
		},
	})
	if err != nil {
		t.Fatalf("create resp note: %v", err)
	}
	_, err = sessionB.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Repo:        repo,
		Kind:        "response",
		CausationID: pubReq.EventID,
		Ref:         RecordVersionRef{RecordID: respNote.RecordID, Version: 1},
		Destination: EventDestination{Type: "agent", Name: "agent/" + agentA.AgentID},
	}, hostedDest)
	if err != nil {
		t.Fatalf("publish response: %v", err)
	}

	// Wait is satisfied in DB and outbox is pending
	gotWait, err := sessionA.GetWait(ctx, GetWaitRequest{WaitUUID: wait.WaitUUID, Collection: repo}, hostedDest)
	if err != nil || gotWait.State != WaitStateSatisfied || gotWait.OutboxState != OutboxStatePending {
		t.Fatalf("expected satisfied wait with pending outbox: %+v %v", gotWait, err)
	}

	// 4. Demote dependency record's memory_record.sensitivity from 'shareable' to 'local'
	_, err = operator.pool.Exec(ctx, `UPDATE cairn.memory_record SET sensitivity='local' WHERE record_id=$1`, depNote.RecordID)
	if err != nil {
		t.Fatalf("demote dependency sensitivity: %v", err)
	}

	// 5. DrainWaitOutbox: must detect that dependency record is local while ceiling is hosted!
	drained, err := operator.DrainWaitOutbox(ctx, repo, 50)
	if err != nil {
		t.Fatalf("drain outbox: %v", err)
	}
	if drained != 0 {
		t.Fatalf("expected 0 published notices, got %d", drained)
	}

	// 6. Assert outbox marked restricted with ceiling_exceeded and 0 event/delivery rows published
	var outboxState, outboxError string
	err = operator.pool.QueryRow(ctx, `SELECT state, error_reason FROM cairn.agent_wait_outbox WHERE wait_uuid=$1`, wait.WaitUUID).Scan(&outboxState, &outboxError)
	if err != nil {
		t.Fatalf("query outbox: %v", err)
	}
	if outboxState != OutboxStateRestricted || outboxError != "ceiling_exceeded" {
		t.Fatalf("expected outbox restricted with ceiling_exceeded, got state=%s error=%s", outboxState, outboxError)
	}

	var eventCount, deliveryCount int
	err = operator.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event WHERE event_id=$1`, wait.ReservedNoticeEventUUID).Scan(&eventCount)
	if err != nil || eventCount != 0 {
		t.Fatalf("expected 0 notice events, got count=%d err=%v", eventCount, err)
	}
	err = operator.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_delivery WHERE event_id=$1`, wait.ReservedNoticeEventUUID).Scan(&deliveryCount)
	if err != nil || deliveryCount != 0 {
		t.Fatalf("expected 0 deliveries, got count=%d err=%v", deliveryCount, err)
	}
}

// Behavioral Test 1: Hosted waiter calling CreateWait AFTER a local-only or forgotten response:
// assert State == "satisfied" and ResponseRef == nil.
func TestCreateWait_RetainedResponseSensitivityCeiling(t *testing.T) {
	_, openStore := waitTestDB(t)
	ctx := context.Background()

	operator := openStore(Channel{Principal: "operator:test", Operator: true})
	if _, err := operator.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatalf("enable waits: %v", err)
	}

	repo := "repo:" + uuid.NewString()
	hostedDest := Destination{Name: "hosted", AllowLocal: false}
	localDest := Destination{Name: "local", AllowLocal: true}
	profileA := openStore(Channel{Principal: "agent:profile-a-" + repo, Repo: repo})
	profileB := openStore(Channel{Principal: "agent:profile-b-" + repo, Repo: repo})
	agentA, err := profileA.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Binding:         "binding-a",
		NativeSessionID: "native-a-" + uuid.NewString(),
		Metadata:        AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"},
	}, hostedDest)
	if err != nil {
		t.Fatalf("register A: %v", err)
	}
	agentB, err := profileB.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Binding:         "binding-b",
		NativeSessionID: "native-b-" + uuid.NewString(),
		Metadata:        AgentMetadata{Harness: "claude", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"},
	}, localDest)
	if err != nil {
		t.Fatalf("register B: %v", err)
	}
	sessionA, err := profileA.ForAgentSession(AgentSessionRef{AgentID: agentA.AgentID, ExecutionID: agentA.ExecutionID}, hostedDest)
	if err != nil {
		t.Fatalf("forAgentSession A: %v", err)
	}
	sessionB, err := profileB.ForAgentSession(AgentSessionRef{AgentID: agentB.AgentID, ExecutionID: agentB.ExecutionID}, localDest)
	if err != nil {
		t.Fatalf("forAgentSession B: %v", err)
	}

	// Publish request
	pubReq := publishTestRequest(t, ctx, sessionA, repo, agentB.AgentID, "", hostedDest)

	// Publish response with local-only sensitivity
	localDraft := projectNote(repo)
	localDraft.Sensitivity = "local"
	localRec, err := sessionB.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: localDraft})
	if err != nil {
		t.Fatalf("create local note: %v", err)
	}
	_, err = sessionB.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Repo:        repo,
		Kind:        "response",
		Ref:         RecordVersionRef{RecordID: localRec.RecordID, Version: 1},
		Destination: EventDestination{Type: "agent", Name: "agent/" + agentA.AgentID},
		CausationID: pubReq.EventID,
	}, localDest)
	if err != nil {
		t.Fatalf("publish local response: %v", err)
	}

	// Hosted waiter calls CreateWait AFTER the response already exists in DB:
	wait, err := sessionA.CreateWait(ctx, CreateWaitRequest{
		RequestID:           uuid.NewString(),
		Collection:          repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}, hostedDest)
	if err != nil {
		t.Fatalf("create wait: %v", err)
	}

	// Must be satisfied, but ResponseRef must be masked to nil because response is local!
	if wait.State != WaitStateSatisfied {
		t.Fatalf("expected state satisfied, got: %s", wait.State)
	}
	if wait.ResponseRef != nil {
		t.Fatalf("expected ResponseRef nil due to ceiling check, got: %+v", wait.ResponseRef)
	}
}

// Behavioral Test 2: Hosted waiter calling CreateWait after a shareable response (ResponseRef != nil),
// then demoting the response record to local, then retrying CreateWait with the SAME RequestID:
// cached retry MUST assert State == "satisfied" and ResponseRef == nil!
func TestCreateWait_CachedIdempotentRetryMasksDemotedResponseRef(t *testing.T) {
	_, openStore := waitTestDB(t)
	ctx := context.Background()

	operator := openStore(Channel{Principal: "operator:test", Operator: true})
	if _, err := operator.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatalf("enable waits: %v", err)
	}

	repo := "repo:" + uuid.NewString()
	hostedDest := Destination{Name: "hosted", AllowLocal: false}
	profileA := openStore(Channel{Principal: "agent:profile-a-" + repo, Repo: repo})
	profileB := openStore(Channel{Principal: "agent:profile-b-" + repo, Repo: repo})
	agentA, err := profileA.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Binding:         "binding-a",
		NativeSessionID: "native-a-" + uuid.NewString(),
		Metadata:        AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"},
	}, hostedDest)
	if err != nil {
		t.Fatalf("register A: %v", err)
	}
	agentB, err := profileB.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Binding:         "binding-b",
		NativeSessionID: "native-b-" + uuid.NewString(),
		Metadata:        AgentMetadata{Harness: "claude", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"},
	}, hostedDest)
	if err != nil {
		t.Fatalf("register B: %v", err)
	}
	sessionA, err := profileA.ForAgentSession(AgentSessionRef{AgentID: agentA.AgentID, ExecutionID: agentA.ExecutionID}, hostedDest)
	if err != nil {
		t.Fatalf("forAgentSession A: %v", err)
	}
	sessionB, err := profileB.ForAgentSession(AgentSessionRef{AgentID: agentB.AgentID, ExecutionID: agentB.ExecutionID}, hostedDest)
	if err != nil {
		t.Fatalf("forAgentSession B: %v", err)
	}

	pubReq := publishTestRequest(t, ctx, sessionA, repo, agentB.AgentID, "", hostedDest)

	// Publish shareable response
	respNote, err := sessionB.Create(ctx, CreateRequest{
		RequestID: uuid.NewString(),
		Draft: Draft{
			Kind:        "note",
			Body:        "shareable response",
			ClaimType:   "self",
			Sensitivity: "shareable",
			Scope:       Scope{Repo: repo, TaskID: "*", RunID: "*"},
		},
	})
	if err != nil {
		t.Fatalf("create resp note: %v", err)
	}
	_, err = sessionB.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Repo:        repo,
		Kind:        "response",
		CausationID: pubReq.EventID,
		Ref:         RecordVersionRef{RecordID: respNote.RecordID, Version: 1},
		Destination: EventDestination{Type: "agent", Name: "agent/" + agentA.AgentID},
	}, hostedDest)
	if err != nil {
		t.Fatalf("publish response: %v", err)
	}

	// 1. Initial CreateWait returns satisfied with non-nil ResponseRef
	requestID := uuid.NewString()
	req := CreateWaitRequest{
		RequestID:           requestID,
		Collection:          repo,
		PredicateKind:       WaitPredicateRequestResponse,
		DependencyEventUUID: pubReq.EventID,
		Deadline:            time.Now().Add(10 * time.Minute),
	}
	initialWait, err := sessionA.CreateWait(ctx, req, hostedDest)
	if err != nil {
		t.Fatalf("initial CreateWait: %v", err)
	}
	if initialWait.State != WaitStateSatisfied || initialWait.ResponseRef == nil {
		t.Fatalf("expected satisfied with non-nil ResponseRef, got: %+v", initialWait)
	}

	// 2. Demote the response record's memory_record.sensitivity to 'local'
	_, err = operator.pool.Exec(ctx, `UPDATE cairn.memory_record SET sensitivity='local' WHERE record_id=$1`, respNote.RecordID)
	if err != nil {
		t.Fatalf("demote sensitivity: %v", err)
	}

	// 3. Retry CreateWait with the EXACT SAME RequestID (hits cached mutation):
	retryWait, err := sessionA.CreateWait(ctx, req, hostedDest)
	if err != nil {
		t.Fatalf("retry CreateWait: %v", err)
	}
	if retryWait.State != WaitStateSatisfied {
		t.Fatalf("expected state satisfied on retry, got: %s", retryWait.State)
	}
	if retryWait.ResponseRef != nil {
		t.Fatalf("cached mutation retry must re-check eligibility and mask demoted ResponseRef to nil! Got: %+v", retryWait.ResponseRef)
	}
}
