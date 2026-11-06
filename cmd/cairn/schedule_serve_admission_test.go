package main

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/jackc/pgx/v5"
)

func TestScheduleServeContinuesToWaitOutboxDrainOnRefusalDiagnosticFault(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	f := newScheduleDiagnosticFixture(t, ctx)
	f.acceptTerminalWait()
	f.configureEnforcedSchedules()
	f.injectRefusalDiagnosticFault()

	var log bytes.Buffer
	raw, err := serveSchedules(ctx, f.op, []string{"--repo", f.repo, "--once"}, &log)
	if err != nil {
		t.Fatalf("scheduler stopped before accepted outbox drain: %v", err)
	}
	result, ok := raw.(ScheduleServeOnceResult)
	if !ok || result.Repo != f.repo || result.ScheduleOccurrences != 1 || result.DeferredSchedules != 1 || result.PublishedNotices != 1 || result.UnrecordedRefusalDiagnostics != 1 || result.DiagnosticWarning == "" {
		t.Fatalf("scheduler did not report committed progress and diagnostic warning: %+v", raw)
	}
	if !strings.Contains(log.String(), "schedule refusal_diagnostics_unrecorded=1") {
		t.Fatalf("missing diagnostic warning in scheduler log: %q", log.String())
	}
	f.assertCommittedProgress()

	// A postcommit diagnostic warning is recoverable; a real store failure is not.
	f.op.Close()
	if _, err := serveSchedules(ctx, f.op, []string{"--repo", f.repo, "--once"}, &log); err == nil {
		t.Fatal("scheduler swallowed a closed-store transaction failure")
	}
}

type scheduleDiagnosticFixture struct {
	t                                *testing.T
	ctx                              context.Context
	dsn, repo, responderInbox        string
	op, profile, waiter, responder   *core.Store
	dest                             core.Destination
	source                           core.RecordVersionRef
	wait                             core.AgentWait
	blockedSchedule, healthySchedule core.ScheduledEvent
}

func newScheduleDiagnosticFixture(t *testing.T, ctx context.Context) *scheduleDiagnosticFixture {
	t.Helper()
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("PostgreSQL integration test: run make test-integration")
	}
	f := &scheduleDiagnosticFixture{t: t, ctx: ctx, dsn: dsn, repo: "cli-schedule-diagnostic:" + uuid.NewString(), dest: core.Destination{Name: "hosted"}}
	f.op = f.open(core.Channel{Principal: "operator:cli-schedule-diagnostic", Operator: true})
	if err := f.op.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err := f.op.SetCoordinationWaitsEnabled(ctx, core.WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatal(err)
	}
	f.profile = f.open(core.Channel{Principal: "profile:waiter:" + f.repo, Repo: f.repo})
	f.responderInbox = "profile:responder:" + f.repo
	f.responder = f.open(core.Channel{Principal: f.responderInbox, Repo: f.repo})
	agent, err := f.profile.RegisterAgent(ctx, core.RegisterAgentRequest{
		RequestID: uuid.NewString(), Binding: "schedule-waiter", NativeSessionID: uuid.NewString(),
		Metadata: core.AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	f.waiter, err = f.profile.ForAgentSession(core.AgentSessionRef{AgentID: agent.AgentID, ExecutionID: agent.ExecutionID}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	return f
}

func (f *scheduleDiagnosticFixture) open(channel core.Channel) *core.Store {
	f.t.Helper()
	store, err := core.Open(f.ctx, f.dsn, channel)
	if err != nil {
		f.t.Fatal(err)
	}
	f.t.Cleanup(store.Close)
	return store
}

func (f *scheduleDiagnosticFixture) acceptTerminalWait() {
	f.t.Helper()
	source, err := f.waiter.Create(f.ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{
		Body: "synthetic scheduled work and accepted terminal wait", Kind: "note", ClaimType: "self", Sensitivity: "shareable", Scope: core.Scope{Repo: f.repo, TaskID: "*", RunID: "*"},
	}})
	if err != nil {
		f.t.Fatal(err)
	}
	f.source = core.RecordVersionRef{RecordID: source.RecordID, Version: source.Version}
	request, err := f.waiter.PublishEvent(f.ctx, core.PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "request", Ref: f.source, Destination: core.EventDestination{Type: "agent", Name: f.responderInbox},
	}, f.dest)
	if err != nil {
		f.t.Fatal(err)
	}
	f.wait, err = f.waiter.CreateWait(f.ctx, core.CreateWaitRequest{
		RequestID: uuid.NewString(), Collection: f.repo, PredicateKind: core.WaitPredicateRequestResponse, DependencyEventUUID: request.EventID, Deadline: time.Now().Add(10 * time.Minute),
	}, f.dest)
	if err != nil {
		f.t.Fatal(err)
	}
	if _, err := f.responder.PublishEvent(f.ctx, core.PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "response", Ref: f.source, Destination: core.EventDestination{Type: "agent", Name: "agent/" + f.wait.OwnerAgentUUID}, CausationID: request.EventID,
	}, f.dest); err != nil {
		f.t.Fatal(err)
	}
	f.assertWaitOutbox(core.OutboxStatePending)
}

func (f *scheduleDiagnosticFixture) configureEnforcedSchedules() {
	f.t.Helper()
	for _, subscriber := range []*core.Store{f.profile, f.responder} {
		if _, err := subscriber.SetSubscription(f.ctx, core.SubscriptionRequest{RequestID: uuid.NewString(), Topic: "diagnostic-overfull-topic", Active: true}); err != nil {
			f.t.Fatal(err)
		}
	}
	if reconciled, err := f.op.ReconcileClosureReservations(f.ctx, core.ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: f.repo}); err != nil || !reconciled.Complete {
		f.t.Fatalf("closure reconciliation: %+v %v", reconciled, err)
	}
	rate := core.EventAdmissionRate{PerSecond: 1000, Burst: 1000}
	if _, err := f.op.SetEventAdmissionPolicy(f.ctx, core.SetEventAdmissionPolicyRequest{
		RequestID: uuid.NewString(), Repo: f.repo,
		Policy: core.EventAdmissionPolicy{Version: 1, Mode: "enforce", Workload: "synthetic CLI tick: one refused topic, one healthy occurrence and one accepted wait notice", Limits: &core.EventAdmissionLimits{
			MaxTopicRecipients: 1, MaxRecipientOutstanding: 8, MaxProducerRecipientOutstanding: 8, MaxCollectionOutstanding: 16, MaxClosureReserved: 8, MaxRecipientClosureReserved: 8,
			ProducerPublications: rate, ProducerDeliveries: rate, OwnerPublications: rate, OwnerDeliveries: rate, CollectionPublications: rate, CollectionDeliveries: rate,
		}},
	}); err != nil {
		f.t.Fatal(err)
	}
	for i, destination := range []core.EventDestination{{Type: "topic", Name: "diagnostic-overfull-topic"}, {Type: "agent", Name: f.responderInbox}} {
		schedule, err := f.op.ScheduleEvent(f.ctx, core.ScheduleEventRequest{
			RequestID: uuid.NewString(), NotBefore: time.Now().Add(time.Duration(i-2) * time.Second), GraceSeconds: 300,
			Publication: core.PublishEventRequest{Repo: f.repo, Kind: "notice", Ref: f.source, Destination: destination},
		})
		if err != nil {
			f.t.Fatal(err)
		}
		if i == 0 {
			f.blockedSchedule = schedule
		} else {
			f.healthySchedule = schedule
		}
	}
}

func (f *scheduleDiagnosticFixture) injectRefusalDiagnosticFault() {
	f.t.Helper()
	conn, err := pgx.Connect(f.ctx, f.dsn)
	if err != nil {
		f.t.Fatal(err)
	}
	f.t.Cleanup(func() {
		if err := conn.Close(context.Background()); err != nil {
			f.t.Errorf("close fault-injection connection: %v", err)
		}
	})
	trigger := "cli_schedule_refusal_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	if _, err := conn.Exec(f.ctx, fmt.Sprintf(`CREATE FUNCTION cairn.%s() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected diagnostic persistence failure'; END $$; CREATE TRIGGER %s BEFORE INSERT OR UPDATE ON cairn.agent_event_admission_refusal FOR EACH ROW WHEN (NEW.repo='%s') EXECUTE FUNCTION cairn.%s()`, trigger, trigger, f.repo, trigger)); err != nil {
		f.t.Fatal(err)
	}
	f.t.Cleanup(func() {
		if _, err := conn.Exec(context.Background(), fmt.Sprintf(`DROP TRIGGER IF EXISTS %s ON cairn.agent_event_admission_refusal; DROP FUNCTION IF EXISTS cairn.%s()`, trigger, trigger)); err != nil {
			f.t.Errorf("remove diagnostic fault injection: %v", err)
		}
	})
}

func (f *scheduleDiagnosticFixture) assertCommittedProgress() {
	f.t.Helper()
	blocked := f.schedule(f.blockedSchedule.OccurrenceID)
	if blocked.State != "pending" || blocked.EventID != "" || blocked.Code != "EVENT_ADMISSION_REFUSED" {
		f.t.Fatalf("refused occurrence lost its pending obligation: %+v", blocked)
	}
	healthy := f.schedule(f.healthySchedule.OccurrenceID)
	if healthy.State != "fired" || healthy.EventID != healthy.OccurrenceID {
		f.t.Fatalf("unrelated healthy occurrence did not commit: %+v", healthy)
	}
	status, err := f.op.AgentEventStatus(f.ctx, core.EventStatusRequest{EventID: healthy.EventID}, f.dest)
	if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].Consumer != f.responderInbox {
		f.t.Fatalf("healthy schedule delivery missing or duplicated: %+v %v", status, err)
	}
	f.assertWaitOutbox(core.OutboxStatePublished)
	terminal, err := f.waiter.AgentEventStatus(f.ctx, core.EventStatusRequest{EventID: f.wait.ReservedNoticeEventUUID}, f.dest)
	if err != nil || len(terminal.Deliveries) != 1 || terminal.Deliveries[0].Consumer != "agent/"+f.wait.OwnerAgentUUID {
		f.t.Fatalf("accepted terminal wait notice missing or duplicated: %+v %v", terminal, err)
	}
}

func (f *scheduleDiagnosticFixture) schedule(occurrenceID string) core.ScheduledEvent {
	f.t.Helper()
	page, err := f.op.Schedules(f.ctx, core.ScheduleQuery{Repo: f.repo, OccurrenceID: occurrenceID})
	if err != nil || len(page.Occurrences) != 1 {
		f.t.Fatalf("inspect schedule %s: %+v %v", occurrenceID, page, err)
	}
	return page.Occurrences[0]
}

func (f *scheduleDiagnosticFixture) assertWaitOutbox(state string) {
	f.t.Helper()
	wait, err := f.waiter.GetWait(f.ctx, core.GetWaitRequest{Collection: f.repo, WaitUUID: f.wait.WaitUUID}, f.dest)
	if err != nil || wait.State != core.WaitStateSatisfied || wait.OutboxState != state {
		f.t.Fatalf("terminal wait outbox: want %s got %+v %v", state, wait, err)
	}
}
