package core

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestWakeProviderFailureSuspendsOnlyOwningBindingAtomically(t *testing.T) {
	ctx := context.Background()
	_, a, b, r, d, w := poolFixture(t)
	second := testStore(t, Channel{Principal: "second-" + uuid.NewString(), Repo: r.Scope.Repo})
	w2, err := second.RegisterWorkerSlot(ctx, WorkerRegisterRequest{RequestID: uuid.NewString(), Spec: w.Spec}, d)
	if err != nil {
		t.Fatal(err)
	}
	poolPublish(t, a, r, d, PoolRequirements{Workspace: w.Spec.Workspace})
	claim := poolClaim(t, b, w, d)
	if claim == nil {
		t.Fatal("missing wake")
	}
	for _, op := range []string{"start", "enter"} {
		if _, err = b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: claim.ID, Operation: op}, d); err != nil {
			t.Fatal(err)
		}
	}
	req := WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: claim.ID, Operation: "report", ProcessState: "exited", Reason: "provider_failed",
		ProviderFailure: &ProviderFailure{Harness: "codex", Source: "native-event", Kind: "quota", Code: "usage_limit_reached"}}
	reported, err := b.ChangeWake(ctx, req, d)
	if err != nil || reported.ProviderFailure == nil || reported.ProviderFailure.Kind != "quota" {
		t.Fatalf("failure report: %+v %v", reported, err)
	}
	slots, err := b.WorkerSlots(ctx, WorkerListRequest{}, d)
	if err != nil {
		t.Fatal(err)
	}
	var own WorkerSlot
	for _, slot := range slots.Workers {
		if slot.SupervisorID == w.SupervisorID {
			own = slot
			if slot.Health != "unavailable" || slot.Revision != w.Revision+1 {
				t.Fatalf("quota failed to suspend owner: %+v", slot)
			}
		}
		if slot.SupervisorID == w2.SupervisorID && slot.Health != "available" {
			t.Fatalf("quota contaminated second account: %+v", slot)
		}
	}
	heartbeat, err := b.HeartbeatWorker(ctx, WorkerHeartbeatRequest{SupervisorID: w.SupervisorID}, d)
	if err != nil || heartbeat.Health != "unavailable" {
		t.Fatalf("heartbeat healed quota: %+v %v", heartbeat, err)
	}
	_, err = b.ChangeWorkerHealth(ctx, WorkerHealthRequest{RequestID: uuid.NewString(), SupervisorID: w.SupervisorID, ExpectedRevision: w.Revision, Health: "available", Reason: "stale operator recovery"}, d)
	requireCode(t, err, "VERSION_CONFLICT")
	// Replay preserves the report but cannot repeat the health mutation after recovery.
	if _, err = b.ChangeWorkerHealth(ctx, WorkerHealthRequest{RequestID: uuid.NewString(), SupervisorID: w.SupervisorID, ExpectedRevision: own.Revision, Health: "available", Reason: "operator verified recovery"}, d); err != nil {
		t.Fatal(err)
	}
	if _, err = b.ChangeWake(ctx, req, d); err != nil {
		t.Fatal(err)
	}
	heartbeat, err = b.HeartbeatWorker(ctx, WorkerHeartbeatRequest{SupervisorID: w.SupervisorID}, d)
	if err != nil || heartbeat.Health != "available" {
		t.Fatalf("report retry re-suspended recovered account: %+v %v", heartbeat, err)
	}
	if _, err = b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: claim.ID, Operation: "finish", ProviderFailure: req.ProviderFailure}, d); err != nil {
		t.Fatal(err)
	}
	heartbeat, err = b.HeartbeatWorker(ctx, WorkerHeartbeatRequest{SupervisorID: w.SupervisorID}, d)
	if err != nil || heartbeat.Health != "available" {
		t.Fatalf("finish re-suspended recovered account: %+v %v", heartbeat, err)
	}
}

func TestProviderHealthRollsBackWithFailedWakeReport(t *testing.T) {
	ctx := context.Background()
	_, a, b, r, d, w := poolFixture(t)
	poolPublish(t, a, r, d, PoolRequirements{Workspace: w.Spec.Workspace})
	claim := poolClaim(t, b, w, d)
	if claim == nil {
		t.Fatal("missing wake")
	}
	for _, op := range []string{"start", "enter"} {
		if _, err := b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: claim.ID, Operation: op}, d); err != nil {
			t.Fatal(err)
		}
	}
	req := WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: claim.ID, Operation: "report", ProcessState: "exited", ProviderFailure: &ProviderFailure{Harness: "claude", Source: "native-event", Kind: "rate_limit", Code: "rate_limit", Status: 429}}
	_, err := b.ChangeWake(ctx, req, d)
	requireCode(t, err, "INVALID_REQUEST")
	req.ProviderFailure.Harness = "codex"
	trigger := "provider_failure_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	_, err = b.pool.Exec(ctx, fmt.Sprintf(`CREATE FUNCTION cairn.%s() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected provider report failure'; END $$; CREATE TRIGGER %s BEFORE UPDATE ON cairn.agent_wake_attempt FOR EACH ROW WHEN (NEW.attempt_id='%s' AND NEW.provider_failure IS NOT NULL) EXECUTE FUNCTION cairn.%s()`, trigger, trigger, claim.ID, trigger))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := b.pool.Exec(ctx, fmt.Sprintf(`DROP TRIGGER IF EXISTS %s ON cairn.agent_wake_attempt; DROP FUNCTION IF EXISTS cairn.%s()`, trigger, trigger)); err != nil {
			t.Error(err)
		}
	})
	if _, err = b.ChangeWake(ctx, req, d); err == nil {
		t.Fatal("expected injected failure")
	}
	slot, err := b.HeartbeatWorker(ctx, WorkerHeartbeatRequest{SupervisorID: w.SupervisorID}, d)
	if err != nil || slot.Health != "available" || slot.Revision != w.Revision {
		t.Fatalf("health escaped rollback: %+v %v", slot, err)
	}
	if _, err = b.pool.Exec(ctx, fmt.Sprintf(`DROP TRIGGER %s ON cairn.agent_wake_attempt`, trigger)); err != nil {
		t.Fatal(err)
	}
	reported, err := b.ChangeWake(ctx, req, d)
	if err != nil || reported.ProviderFailure == nil {
		t.Fatalf("identical retry after rollback: %+v %v", reported, err)
	}
	changed := *req.ProviderFailure
	changed.Code = "different_observation"
	_, err = b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: claim.ID, Operation: "finish", ProviderFailure: &changed}, d)
	requireCode(t, err, "VERSION_CONFLICT")
	if _, err = b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: claim.ID, Operation: "finish", ProviderFailure: req.ProviderFailure}, d); err != nil {
		t.Fatal(err)
	}
}

func TestWakeCleanupRetainsQuotaAfterLostReportAndRestore(t *testing.T) {
	ctx := context.Background()
	op, a, b, r, d, w := poolFixture(t)
	poolPublish(t, a, r, d, PoolRequirements{Workspace: w.Spec.Workspace})
	claim := poolClaim(t, b, w, d)
	if claim == nil {
		t.Fatal("missing claim")
	}
	for _, operation := range []string{"start", "enter"} {
		if _, err := b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: claim.ID, Operation: operation}, d); err != nil {
			t.Fatal(err)
		}
	}
	// The native observation was retained locally, but its API report was lost.
	if _, err := op.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "disposable provider observation restore"}); err != nil {
		t.Fatal(err)
	}
	done, err := b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: claim.ID, Operation: "finish", Reason: "host_confirmed_stopped", ProviderFailure: &ProviderFailure{Harness: "codex", Source: "native-event", Kind: "quota", Code: "usage_limit_reached"}}, d)
	if err != nil || done.ProviderFailure == nil || done.State != "finished" || done.Delivery.State != "failed" {
		t.Fatalf("lost report cleanup: %+v %v", done, err)
	}
	replacement, err := b.RegisterWorkerSlot(ctx, WorkerRegisterRequest{RequestID: uuid.NewString(), Spec: w.Spec}, d)
	if err != nil || replacement.Health != "unavailable" {
		t.Fatalf("restart lost quota observation: %+v %v", replacement, err)
	}
}
