package core

import (
	"context"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestPoolAssignmentCreatesOneSlotDeliveryAtomically(t *testing.T) {
	ctx := context.Background()
	publisher, slot, source, dest := eventFixture(t)
	op, _ := testOperator(t)
	_, err := op.ConfigureWorkerPool(ctx, WorkerPoolConfigRequest{RequestID: uuid.NewString(), Repo: source.Scope.Repo,
		Name: "coding", Enabled: true, MaxPending: 20, MaxPendingPerPublisher: 10})
	if err != nil {
		t.Fatal(err)
	}
	worker, err := slot.RegisterWorkerSlot(ctx, WorkerRegisterRequest{RequestID: uuid.NewString(), Spec: WorkerSpec{
		Name: "worker-one", Harness: "codex", Workspace: "/fixture/rhumb", Pools: []string{"coding"}, Capabilities: []string{"review"}, LaunchSpacingSeconds: 60}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	req := PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, 1},
		Destination: EventDestination{"pool", "coding"}, Pool: &PoolRequirements{Workspace: "/fixture/rhumb", Capabilities: []string{"review"}}}
	event, err := publisher.PublishEvent(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	status, err := publisher.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
	if err != nil || len(status.Deliveries) != 0 || status.Pool == nil || status.Pool.State != "queued" {
		t.Fatalf("queued pool request: %+v %v", status, err)
	}
	claims := make(chan WakeResult, 8)
	errs := make(chan error, 8)
	for range 8 {
		go func() {
			w, e := slot.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString(), WorkerID: worker.SupervisorID}, dest)
			claims <- w
			errs <- e
		}()
	}
	var won *WakeAttempt
	for range 8 {
		w, e := <-claims, <-errs
		if e != nil {
			t.Fatal(e)
		}
		if w.Attempt != nil {
			if won != nil {
				t.Fatal("two concurrent pool launches")
			}
			won = w.Attempt
		}
	}
	if won == nil || won.Delivery.Event.EventID != event.EventID || won.Delivery.Consumer != slot.channel.Principal {
		t.Fatalf("pool assignment: %+v", won)
	}
	status, err = publisher.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
	if err != nil || len(status.Deliveries) != 1 || status.Pool.State != "assigned" || status.Pool.DeliveryID != won.Delivery.DeliveryID {
		t.Fatalf("assignment view: %+v %v", status, err)
	}
	again, err := publisher.PublishEvent(ctx, req, dest)
	if err != nil || again.EventID != event.EventID || again.Destination.Type != "pool" {
		t.Fatalf("publication retry retargeted: %+v %v", again, err)
	}
	replay, err := slot.ClaimWake(ctx, WakeClaimRequest{RequestID: won.ID, WorkerID: worker.SupervisorID}, dest)
	if err != nil || replay.Attempt.ID != won.ID {
		t.Fatalf("claim retry: %+v %v", replay, err)
	}
}

func poolFixture(t *testing.T) (*Store, *Store, *Store, Record, Destination, WorkerSlot) {
	t.Helper()
	a, b, r, d := eventFixture(t)
	op, _ := testOperator(t)
	if _, err := op.ConfigureWorkerPool(context.Background(), WorkerPoolConfigRequest{RequestID: uuid.NewString(), Repo: r.Scope.Repo, Name: "coding", Enabled: true, MaxPending: 20, MaxPendingPerPublisher: 10}); err != nil {
		t.Fatal(err)
	}
	w, err := b.RegisterWorkerSlot(context.Background(), WorkerRegisterRequest{RequestID: uuid.NewString(), Spec: WorkerSpec{Name: "worker-one", Harness: "codex", Workspace: "/fixture/rhumb", Pools: []string{"coding"}, Capabilities: []string{"review"}, LaunchSpacingSeconds: 60}}, d)
	if err != nil {
		t.Fatal(err)
	}
	return op, a, b, r, d, w
}
func poolPublish(t *testing.T, a *Store, r Record, d Destination, needs PoolRequirements) AgentEvent {
	t.Helper()
	e, err := a.PublishEvent(context.Background(), PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"pool", "coding"}, Pool: &needs}, d)
	if err != nil {
		t.Fatal(err)
	}
	return e
}
func poolClaim(t *testing.T, s *Store, w WorkerSlot, d Destination) *WakeAttempt {
	t.Helper()
	c, err := s.ClaimWake(context.Background(), WakeClaimRequest{RequestID: uuid.NewString(), WorkerID: w.SupervisorID}, d)
	if err != nil {
		t.Fatal(err)
	}
	return c.Attempt
}
func finishPool(t *testing.T, s *Store, w WakeAttempt, d Destination) {
	t.Helper()
	ctx := context.Background()
	if _, err := s.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: w.Delivery.DeliveryID, LeaseID: w.Delivery.LeaseID, Disposition: "handled"}, d); err != nil {
		t.Fatal(err)
	}
	if _, err := s.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: w.ID, Operation: "finish", Reason: "fixture_cgroup_stopped"}, d); err != nil {
		t.Fatal(err)
	}
}
func allowFixtureLaunch(t *testing.T, s *Store) {
	t.Helper()
	if _, err := s.pool.Exec(context.Background(), `UPDATE cairn.agent_worker_slot SET next_launch_at=clock_timestamp()-interval '1 second' WHERE repo=$1 AND consumer=$2`, s.channel.Repo, s.channel.Principal); err != nil {
		t.Fatal(err)
	}
}
func TestPoolMatchingAndIndependentAccountHealth(t *testing.T) {
	ctx := context.Background()
	_, a, b, r, d, w := poolFixture(t)
	second := testStore(t, Channel{Principal: "other-" + uuid.NewString(), Repo: r.Scope.Repo})
	w2, err := second.RegisterWorkerSlot(ctx, WorkerRegisterRequest{RequestID: uuid.NewString(), Spec: w.Spec}, d)
	if err != nil {
		t.Fatal(err)
	}
	// First account is live but unavailable; neither heartbeat nor retry time heals it.
	past := w.HeartbeatAt.Add(-time.Hour)
	w, err = b.ChangeWorkerHealth(ctx, WorkerHealthRequest{RequestID: uuid.NewString(), SupervisorID: w.SupervisorID, ExpectedRevision: w.Revision, Health: "unavailable", Reason: "operator observed provider quota", RetryAt: &past}, d)
	if err != nil {
		t.Fatal(err)
	}
	w, err = b.HeartbeatWorker(ctx, WorkerHeartbeatRequest{SupervisorID: w.SupervisorID}, d)
	if err != nil || w.Health != "unavailable" {
		t.Fatalf("heartbeat healed account: %+v %v", w, err)
	}
	prior := w
	w, err = b.RegisterWorkerSlot(ctx, WorkerRegisterRequest{RequestID: uuid.NewString(), Spec: w.Spec}, d)
	if err != nil || w.Health != "unavailable" || w.SupervisorID == prior.SupervisorID || !w.NextLaunchAt.Equal(prior.NextLaunchAt) {
		t.Fatalf("restart cleared health/backoff: %+v %v", w, err)
	}
	bad := []PoolRequirements{
		{Workspace: "/other", Capabilities: []string{"review"}},
		{Workspace: w.Spec.Workspace, Capabilities: []string{"deploy"}},
		{Workspace: w.Spec.Workspace, Harness: "claude"},
		{Workspace: w.Spec.Workspace, Model: "different"},
	}
	for _, needs := range bad {
		poolPublish(t, a, r, d, needs)
	}
	if got := poolClaim(t, second, w2, d); got != nil {
		t.Fatalf("ineligible pool request: %+v", got)
	}
	eligible := poolPublish(t, a, r, d, PoolRequirements{Workspace: w.Spec.Workspace, Capabilities: []string{"review"}})
	if got := poolClaim(t, b, w, d); got != nil {
		t.Fatal("unavailable account launched")
	}
	got := poolClaim(t, second, w2, d)
	if got == nil || got.Delivery.Event.EventID != eligible.EventID {
		t.Fatalf("healthy account skipped eligible work: %+v", got)
	}
	// Queued payload is hidden until an assignment belongs to this profile.
	_, err = b.AgentEventStatus(ctx, EventStatusRequest{EventID: eligible.EventID}, d)
	requireCode(t, err, "NOT_FOUND")
	finishPool(t, second, *got, d)
	_, err = b.ChangeWorkerHealth(ctx, WorkerHealthRequest{RequestID: uuid.NewString(), SupervisorID: w.SupervisorID, ExpectedRevision: w.Revision, Health: "available", Reason: "operator verified account recovery"}, d)
	if err != nil {
		t.Fatal(err)
	}
	recovery := poolPublish(t, a, r, d, PoolRequirements{Workspace: w.Spec.Workspace})
	got = poolClaim(t, b, w, d)
	if got == nil || got.Delivery.Event.EventID != recovery.EventID {
		t.Fatalf("explicit recovery: %+v", got)
	}
}
func TestPoolBoundsFairnessAndConcurrentSlots(t *testing.T) {
	ctx := context.Background()
	op, a, b, r, d, w := poolFixture(t)
	config := WorkerPoolConfigRequest{RequestID: uuid.NewString(), Repo: r.Scope.Repo, Name: "coding", Enabled: true, MaxPending: 3, MaxPendingPerPublisher: 2, ExpectedRevision: 1}
	if _, err := op.ConfigureWorkerPool(ctx, config); err != nil {
		t.Fatal(err)
	}
	first := poolPublish(t, a, r, d, PoolRequirements{Workspace: w.Spec.Workspace})
	poolPublish(t, a, r, d, PoolRequirements{Workspace: w.Spec.Workspace})
	_, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"pool", "coding"}, Pool: &PoolRequirements{Workspace: w.Spec.Workspace}}, d)
	requireCode(t, err, "POOL_FULL")
	other := testStore(t, Channel{Principal: "other-publisher-" + uuid.NewString(), Repo: r.Scope.Repo})
	third := poolPublish(t, other, r, d, PoolRequirements{Workspace: w.Spec.Workspace})
	// Global bound also refuses a different publisher without leaving an event.
	_, err = other.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"pool", "coding"}, Pool: &PoolRequirements{Workspace: w.Spec.Workspace}}, d)
	requireCode(t, err, "POOL_FULL")
	var count int
	if err = b.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event WHERE repo=$1`, r.Scope.Repo).Scan(&count); err != nil || count != 3 {
		t.Fatalf("refused publication leaked event: %d %v", count, err)
	}
	got := poolClaim(t, b, w, d)
	if got == nil || got.Delivery.Event.EventID != first.EventID {
		t.Fatalf("first publisher turn: %+v", got)
	}
	finishPool(t, b, *got, d)
	if got := poolClaim(t, b, w, d); got != nil {
		t.Fatal("launch spacing bypassed")
	}
	allowFixtureLaunch(t, b)
	got = poolClaim(t, b, w, d)
	if got == nil || got.Delivery.Event.EventID != third.EventID {
		t.Fatalf("publisher fairness failed: %+v", got)
	}
	finishPool(t, b, *got, d)
	// Two distinct slots contend for the remaining single request.
	allowFixtureLaunch(t, b)
	b2 := testStore(t, Channel{Principal: "second-slot-" + uuid.NewString(), Repo: r.Scope.Repo})
	w2, err := b2.RegisterWorkerSlot(ctx, WorkerRegisterRequest{RequestID: uuid.NewString(), Spec: w.Spec}, d)
	if err != nil {
		t.Fatal(err)
	}
	type answer struct {
		w WakeResult
		e error
	}
	answers := make(chan answer, 2)
	for _, pair := range []struct {
		s *Store
		w WorkerSlot
	}{{b, w}, {b2, w2}} {
		go func() {
			v, e := pair.s.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString(), WorkerID: pair.w.SupervisorID}, d)
			answers <- answer{v, e}
		}()
	}
	won := 0
	for range 2 {
		v := <-answers
		if v.e != nil {
			t.Fatal(v.e)
		}
		if v.w.Attempt != nil {
			won++
		}
	}
	if won != 1 {
		t.Fatalf("distinct slots assigned same request %d times", won)
	}
}
func TestPoolSupervisorFencingRestoreAndRetainedAssignment(t *testing.T) {
	ctx := context.Background()
	op, a, b, r, d, w := poolFixture(t)
	req := WorkerRegisterRequest{RequestID: uuid.NewString(), Spec: w.Spec}
	replacement, err := b.RegisterWorkerSlot(ctx, req, d)
	if err != nil {
		t.Fatal(err)
	}
	replay, err := b.RegisterWorkerSlot(ctx, req, d)
	if err != nil || replay.SupervisorID != replacement.SupervisorID {
		t.Fatalf("registration retry: %+v %v", replay, err)
	}
	_, err = b.HeartbeatWorker(ctx, WorkerHeartbeatRequest{SupervisorID: w.SupervisorID}, d)
	requireCode(t, err, "STALE_WORKER")
	_, err = b.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString(), WorkerID: w.SupervisorID}, d)
	requireCode(t, err, "STALE_WORKER")
	_, err = b.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString()}, d)
	requireCode(t, err, "STALE_WORKER")
	e := poolPublish(t, a, r, d, PoolRequirements{Workspace: w.Spec.Workspace})
	got := poolClaim(t, b, replacement, d)
	if got == nil {
		t.Fatal("missing pool claim")
	}
	_, err = b.RegisterWorkerSlot(ctx, WorkerRegisterRequest{RequestID: uuid.NewString(), Spec: w.Spec}, d)
	requireCode(t, err, "VERSION_CONFLICT")
	if _, err = op.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "disposable pool restore probe"}); err != nil {
		t.Fatal(err)
	}
	_, err = b.HeartbeatWorker(ctx, WorkerHeartbeatRequest{SupervisorID: replacement.SupervisorID}, d)
	requireCode(t, err, "STALE_WORKER")
	// A lost claim response still locates the original assignment after restore.
	replayWake, err := b.ClaimWake(ctx, WakeClaimRequest{RequestID: got.ID, WorkerID: replacement.SupervisorID}, d)
	if err != nil || replayWake.Attempt.ID != got.ID {
		t.Fatalf("restore claim retry: %+v %v", replayWake, err)
	}
	_, err = b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: got.ID, Operation: "start"}, d)
	requireCode(t, err, "STALE_WORKER")
	// Cleanup remains possible; restore does not create a second assignment.
	if _, err = b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: got.ID, Operation: "finish", Reason: "fixture_stopped_after_restore"}, d); err != nil {
		t.Fatal(err)
	}
	replacement, err = b.RegisterWorkerSlot(ctx, WorkerRegisterRequest{RequestID: uuid.NewString(), Spec: w.Spec}, d)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = b.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET available_at=clock_timestamp()-interval '1 second' WHERE delivery_id=$1`, got.Delivery.DeliveryID); err != nil {
		t.Fatal(err)
	}
	allowFixtureLaunch(t, b)
	retry := poolClaim(t, b, replacement, d)
	if retry == nil || retry.Delivery.Event.EventID != e.EventID || retry.Delivery.DeliveryID != got.Delivery.DeliveryID {
		t.Fatalf("prelaunch retry lost assignment: %+v", retry)
	}
}

func TestPoolAssignedRetryCannotChangeWorkspaceOrBypassPause(t *testing.T) {
	ctx := context.Background()
	op, a, b, r, d, w := poolFixture(t)
	poolPublish(t, a, r, d, PoolRequirements{Workspace: w.Spec.Workspace, Capabilities: []string{"review"}})
	got := poolClaim(t, b, w, d)
	if got == nil {
		t.Fatal("missing claim")
	}
	if _, err := b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: got.ID, Operation: "finish", Reason: "confirmed_never_started"}, d); err != nil {
		t.Fatal(err)
	}
	if _, err := b.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET available_at=clock_timestamp()-interval '1 second' WHERE delivery_id=$1`, got.Delivery.DeliveryID); err != nil {
		t.Fatal(err)
	}
	spec := w.Spec
	spec.Workspace = "/fixture/other"
	moved, err := b.RegisterWorkerSlot(ctx, WorkerRegisterRequest{RequestID: uuid.NewString(), Spec: spec}, d)
	if err != nil {
		t.Fatal(err)
	}
	allowFixtureLaunch(t, b)
	if got := poolClaim(t, b, moved, d); got != nil {
		t.Fatal("assigned retry launched under a different workspace")
	}
	restored, err := b.RegisterWorkerSlot(ctx, WorkerRegisterRequest{RequestID: uuid.NewString(), Spec: w.Spec}, d)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = op.ConfigureWorkerPool(ctx, WorkerPoolConfigRequest{RequestID: uuid.NewString(), Repo: r.Scope.Repo, Name: "coding", Enabled: false, MaxPending: 20, MaxPendingPerPublisher: 10, ExpectedRevision: 1}); err != nil {
		t.Fatal(err)
	}
	if got := poolClaim(t, b, restored, d); got != nil {
		t.Fatal("assigned retry bypassed disabled pool")
	}
}

func TestPoolAssignmentRollbackDoesNotStrandQueue(t *testing.T) {
	ctx := context.Background()
	_, a, b, r, d, w := poolFixture(t)
	event := poolPublish(t, a, r, d, PoolRequirements{Workspace: w.Spec.Workspace})
	// Inject failure after delivery creation, at the wake insertion boundary.
	trigger := "pool_probe_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	sql := fmt.Sprintf(`CREATE FUNCTION cairn.%s() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected pool wake failure'; END $$; CREATE TRIGGER %s BEFORE INSERT ON cairn.agent_wake_attempt FOR EACH ROW WHEN (NEW.repo='%s') EXECUTE FUNCTION cairn.%s()`, trigger, trigger, r.Scope.Repo, trigger)
	if _, err := b.pool.Exec(ctx, sql); err != nil {
		t.Fatal(err)
	}
	defer b.pool.Exec(ctx, fmt.Sprintf(`DROP TRIGGER IF EXISTS %s ON cairn.agent_wake_attempt; DROP FUNCTION IF EXISTS cairn.%s()`, trigger, trigger))
	request := WakeClaimRequest{RequestID: uuid.NewString(), WorkerID: w.SupervisorID}
	if _, err := b.ClaimWake(ctx, request, d); err == nil {
		t.Fatal("injected wake insertion succeeded")
	}
	status, err := a.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, d)
	if err != nil || status.Pool.State != "queued" || len(status.Deliveries) != 0 {
		t.Fatalf("failed transaction left an assignment: %+v %v", status, err)
	}
	if _, err = b.pool.Exec(ctx, fmt.Sprintf(`DROP TRIGGER %s ON cairn.agent_wake_attempt`, trigger)); err != nil {
		t.Fatal(err)
	}
	got, err := b.ClaimWake(ctx, request, d)
	if err != nil || got.Attempt == nil || got.Attempt.Delivery.Event.EventID != event.EventID {
		t.Fatalf("retry after rollback: %+v %v", got, err)
	}
}

func TestPoolConcurrentAdmissionHonorsLimit(t *testing.T) {
	ctx := context.Background()
	op, a, _, r, d, w := poolFixture(t)
	if _, err := op.ConfigureWorkerPool(ctx, WorkerPoolConfigRequest{RequestID: uuid.NewString(), Repo: r.Scope.Repo, Name: "coding", Enabled: true, MaxPending: 3, MaxPendingPerPublisher: 3, ExpectedRevision: 1}); err != nil {
		t.Fatal(err)
	}
	results := make(chan error, 12)
	for range 12 {
		go func() {
			_, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"pool", "coding"}, Pool: &PoolRequirements{Workspace: w.Spec.Workspace}}, d)
			results <- err
		}()
	}
	admitted := 0
	for range 12 {
		err := <-results
		if err == nil {
			admitted++
		} else {
			requireCode(t, err, "POOL_FULL")
		}
	}
	if admitted != 3 {
		t.Fatalf("concurrent admission accepted %d requests", admitted)
	}
}
