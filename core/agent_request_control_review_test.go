package core

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
)

// 1. Sequential orderings of CancelWork vs CompleteEvent on a running managed wake:
// each path has exactly one durable winner and both retain wake hold (agent_wake_attempt.finished_at IS NULL)
// until supervisor cleanup/finish.
func TestCancelWorkVsCompleteEventRaceManagedWake(t *testing.T) {
	ctx := context.Background()

	t.Run("CancelWorkWinsFencesCompleteEventRetainsHold", func(t *testing.T) {
		op, a, b, source, dest, w := poolFixture(t)
		event := poolPublish(t, a, source, dest, PoolRequirements{Workspace: w.Spec.Workspace})
		attempt := poolClaim(t, b, w, dest)
		if attempt == nil {
			t.Fatal("expected wake attempt")
		}
		for _, opName := range []string{"start", "enter"} {
			if _, err := b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: attempt.ID, Operation: opName}, dest); err != nil {
				t.Fatal(err)
			}
		}

		// Operator cancels running wake.
		req := CancelWorkRequest{
			RequestID:  uuid.NewString(),
			Repo:       source.Scope.Repo,
			DeliveryID: attempt.Delivery.DeliveryID,
			Reason:     "Operator cancelled running wake",
		}
		stopped, err := op.CancelWork(ctx, req)
		if err != nil {
			t.Fatalf("cancel work: %v", err)
		}
		if !stopped.Held || stopped.Control == nil || stopped.Control.Code != "operator_cancelled" {
			t.Fatalf("unexpected cancel result: %+v", stopped)
		}
		if stopped.Control.Reason != "Operator cancelled running wake" {
			t.Fatalf("operator reason missing in response: %+v", stopped.Control)
		}

		// Public event status must omit operator reason.
		status, err := a.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
		if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].State != "failed" || status.Deliveries[0].Code != "operator_cancelled" || status.Deliveries[0].Control == nil || status.Deliveries[0].Control.Reason != "" {
			t.Fatalf("public status leaked operator reason or bad state: %+v %v", status, err)
		}

		// Worker completion is fenced by STALE_LEASE.
		_, err = b.CompleteEvent(ctx, CompleteEventRequest{
			RequestID:   uuid.NewString(),
			DeliveryID:  attempt.Delivery.DeliveryID,
			LeaseID:     attempt.Delivery.LeaseID,
			Disposition: "handled",
		}, dest)
		requireCode(t, err, "STALE_LEASE")

		// Wake hold is strictly retained until supervisor explicitly reports finish.
		active, err := b.WakeAttempts(ctx, WakeQuery{Active: true}, dest)
		if err != nil || len(active.Attempts) != 1 || active.Attempts[0].FinishedAt != nil {
			t.Fatalf("wake hold lost before finish: %+v %v", active, err)
		}

		// Supervisor confirms stop.
		finished, err := b.ChangeWake(ctx, WakeChangeRequest{
			RequestID: uuid.NewString(),
			AttemptID: attempt.ID,
			Operation: "finish",
			Reason:    "fixture_cgroup_stopped",
		}, dest)
		if err != nil || finished.FinishedAt == nil || finished.State != "finished" {
			t.Fatalf("wake finish: %+v %v", finished, err)
		}

		// Hold is now released.
		activeAfter, err := b.WakeAttempts(ctx, WakeQuery{Active: true}, dest)
		if err != nil || len(activeAfter.Attempts) != 0 {
			t.Fatalf("active wakes remain after finish: %+v %v", activeAfter, err)
		}
	})

	t.Run("CompleteEventWinsFencesCancelWorkRetainsHold", func(t *testing.T) {
		op, a, b, source, dest, w := poolFixture(t)
		event := poolPublish(t, a, source, dest, PoolRequirements{Workspace: w.Spec.Workspace})
		attempt := poolClaim(t, b, w, dest)
		if attempt == nil {
			t.Fatal("expected wake attempt")
		}
		for _, opName := range []string{"start", "enter"} {
			if _, err := b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: attempt.ID, Operation: opName}, dest); err != nil {
				t.Fatal(err)
			}
		}

		// Worker successfully completes event first.
		done, err := b.CompleteEvent(ctx, CompleteEventRequest{
			RequestID:   uuid.NewString(),
			DeliveryID:  attempt.Delivery.DeliveryID,
			LeaseID:     attempt.Delivery.LeaseID,
			Disposition: "handled",
		}, dest)
		if err != nil || done.State != "handled" {
			t.Fatalf("complete event: %+v %v", done, err)
		}

		// Operator attempt to cancel after completion fails with VERSION_CONFLICT.
		_, err = op.CancelWork(ctx, CancelWorkRequest{
			RequestID:  uuid.NewString(),
			Repo:       source.Scope.Repo,
			DeliveryID: attempt.Delivery.DeliveryID,
			Reason:     "Late operator cancel",
		})
		requireCode(t, err, "VERSION_CONFLICT")

		// Completed delivery remains handled, not failed.
		status, err := a.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
		if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].State != "handled" {
			t.Fatalf("handled status mutated: %+v %v", status, err)
		}

		// Wake hold is retained until supervisor finish.
		active, err := b.WakeAttempts(ctx, WakeQuery{Active: true}, dest)
		if err != nil || len(active.Attempts) != 1 || active.Attempts[0].FinishedAt != nil {
			t.Fatalf("wake hold lost after completion before finish: %+v %v", active, err)
		}

		// Supervisor confirms finish.
		finished, err := b.ChangeWake(ctx, WakeChangeRequest{
			RequestID: uuid.NewString(),
			AttemptID: attempt.ID,
			Operation: "finish",
			Reason:    "fixture_cgroup_stopped",
		}, dest)
		if err != nil || finished.FinishedAt == nil || finished.State != "finished" {
			t.Fatalf("wake finish: %+v %v", finished, err)
		}
	})
}

// 2. Cancel while wake is starting prevents enter (returns controlled delivery, no running transition).
func TestCancelWhileWakeStartingPreventsEnter(t *testing.T) {
	ctx := context.Background()
	op, a, b, source, dest, w := poolFixture(t)
	event := poolPublish(t, a, source, dest, PoolRequirements{Workspace: w.Spec.Workspace})
	attempt := poolClaim(t, b, w, dest)
	if attempt == nil {
		t.Fatal("expected wake attempt")
	}

	// Advance to starting.
	started, err := b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: attempt.ID, Operation: "start"}, dest)
	if err != nil || started.State != "starting" {
		t.Fatalf("start transition: %+v %v", started, err)
	}

	// Cancel while in starting state.
	cancelReq := CancelWorkRequest{
		RequestID:  uuid.NewString(),
		Repo:       source.Scope.Repo,
		DeliveryID: attempt.Delivery.DeliveryID,
		Reason:     "Cancel while starting",
	}
	stopped, err := op.CancelWork(ctx, cancelReq)
	if err != nil || !stopped.Held || stopped.Control.Code != "operator_cancelled" {
		t.Fatalf("cancel while starting: %+v %v", stopped, err)
	}

	// Worker attempt to enter is prevented: remains starting, returns controlled delivery.
	entered, err := b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: attempt.ID, Operation: "enter"}, dest)
	if err != nil {
		t.Fatalf("enter call: %v", err)
	}
	if entered.State != "starting" {
		t.Fatalf("expected state to remain starting, got %q", entered.State)
	}
	if entered.Delivery.State != "failed" || entered.Delivery.Code != "operator_cancelled" || entered.Delivery.Control == nil {
		t.Fatalf("expected controlled delivery, got: %+v", entered.Delivery)
	}
	if entered.FinishedAt != nil {
		t.Fatal("wake hold released prematurely before finish")
	}

	// Verify public event status reflects failed delivery with operator reason omitted.
	status, err := a.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
	if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].State != "failed" || status.Deliveries[0].Code != "operator_cancelled" || status.Deliveries[0].Control == nil || status.Deliveries[0].Control.Reason != "" {
		t.Fatalf("public status leaked reason or bad state: %+v %v", status, err)
	}

	// Completion attempt is blocked.
	_, err = b.CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  attempt.Delivery.DeliveryID,
		LeaseID:     attempt.Delivery.LeaseID,
		Disposition: "handled",
	}, dest)
	requireCode(t, err, "STALE_LEASE")

	// Supervisor confirms finish and releases hold.
	finished, err := b.ChangeWake(ctx, WakeChangeRequest{
		RequestID: uuid.NewString(),
		AttemptID: attempt.ID,
		Operation: "finish",
		Reason:    "fixture_cgroup_stopped",
	}, dest)
	if err != nil || finished.FinishedAt == nil || finished.State != "finished" {
		t.Fatalf("finish attempt: %+v %v", finished, err)
	}
}

// 3. Admission expiry between claim/start/enter prevents execution, whereas expiry after enter never revokes already-running work.
func TestAdmissionExpiryBetweenClaimAndEnterVsAfterEnter(t *testing.T) {
	ctx := context.Background()

	t.Run("ExpiryBeforeEnterPreventsEnterAndExecution", func(t *testing.T) {
		_, a, b, source, dest, w := poolFixture(t)
		future := time.Now().Add(time.Hour)
		event, err := a.PublishEvent(ctx, PublishEventRequest{
			RequestID:          uuid.NewString(),
			Kind:               "request",
			Ref:                RecordVersionRef{RecordID: source.RecordID, Version: 1},
			Destination:        EventDestination{Type: "pool", Name: "coding"},
			Pool:               &PoolRequirements{Workspace: w.Spec.Workspace},
			AdmissionExpiresAt: &future,
		}, dest)
		if err != nil {
			t.Fatal(err)
		}

		attempt := poolClaim(t, b, w, dest)
		if attempt == nil {
			t.Fatal("expected wake attempt")
		}
		if _, err = b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: attempt.ID, Operation: "start"}, dest); err != nil {
			t.Fatal(err)
		}

		// Admission expiry arrives before enter.
		if _, err = b.pool.Exec(ctx, `UPDATE cairn.agent_event SET admission_expires_at=clock_timestamp()-interval '1 second' WHERE event_id=$1`, event.EventID); err != nil {
			t.Fatal(err)
		}

		// Worker attempts enter: prevented, returns starting with failed/admission_expired delivery.
		entered, err := b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: attempt.ID, Operation: "enter"}, dest)
		if err != nil {
			t.Fatalf("enter call: %v", err)
		}
		if entered.State != "starting" {
			t.Fatalf("expected state starting, got %q", entered.State)
		}
		if entered.Delivery.State != "failed" || entered.Delivery.Code != "admission_expired" {
			t.Fatalf("expected delivery admission_expired, got: %+v", entered.Delivery)
		}
		if entered.FinishedAt != nil {
			t.Fatal("wake hold released before supervisor cleanup")
		}

		// Cannot complete event.
		_, err = b.CompleteEvent(ctx, CompleteEventRequest{
			RequestID:   uuid.NewString(),
			DeliveryID:  attempt.Delivery.DeliveryID,
			LeaseID:     attempt.Delivery.LeaseID,
			Disposition: "handled",
		}, dest)
		requireCode(t, err, "STALE_LEASE")

		// Supervisor finishes.
		finished, err := b.ChangeWake(ctx, WakeChangeRequest{
			RequestID: uuid.NewString(),
			AttemptID: attempt.ID,
			Operation: "finish",
			Reason:    "fixture_cgroup_stopped",
		}, dest)
		if err != nil || finished.FinishedAt == nil || finished.State != "finished" {
			t.Fatalf("finish: %+v %v", finished, err)
		}
	})

	t.Run("ExpiryAfterEnterNeverRevokesRunningWork", func(t *testing.T) {
		_, a, b, source, dest, w := poolFixture(t)
		future := time.Now().Add(time.Hour)
		event, err := a.PublishEvent(ctx, PublishEventRequest{
			RequestID:          uuid.NewString(),
			Kind:               "request",
			Ref:                RecordVersionRef{RecordID: source.RecordID, Version: 1},
			Destination:        EventDestination{Type: "pool", Name: "coding"},
			Pool:               &PoolRequirements{Workspace: w.Spec.Workspace},
			AdmissionExpiresAt: &future,
		}, dest)
		if err != nil {
			t.Fatal(err)
		}

		attempt := poolClaim(t, b, w, dest)
		if attempt == nil {
			t.Fatal("expected wake attempt")
		}
		for _, opName := range []string{"start", "enter"} {
			if _, err = b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: attempt.ID, Operation: opName}, dest); err != nil {
				t.Fatal(err)
			}
		}

		// Admission expiry arrives after work is successfully running.
		if _, err = b.pool.Exec(ctx, `UPDATE cairn.agent_event SET admission_expires_at=clock_timestamp()-interval '1 second' WHERE event_id=$1`, event.EventID); err != nil {
			t.Fatal(err)
		}

		// WakeControl check does not stop running work for admission expiry.
		ctrl, err := b.WakeControl(ctx, WakeControlRequest{AttemptID: attempt.ID}, dest)
		if err != nil {
			t.Fatalf("wake control: %v", err)
		}
		if ctrl.StopReason != "" {
			t.Fatalf("unexpected stop reason for running work: %q", ctrl.StopReason)
		}
		if ctrl.Attempt.Delivery.State != "leased" {
			t.Fatalf("running delivery revoked: %+v", ctrl.Attempt.Delivery)
		}

		// Lease can still be renewed.
		renewed, err := b.ChangeEventLease(ctx, EventLeaseRequest{
			DeliveryID: attempt.Delivery.DeliveryID,
			LeaseID:    attempt.Delivery.LeaseID,
		}, true, dest)
		if err != nil || renewed.State != "leased" {
			t.Fatalf("lease renewal failed: %+v %v", renewed, err)
		}

		// Work can complete normally.
		done, err := b.CompleteEvent(ctx, CompleteEventRequest{
			RequestID:   uuid.NewString(),
			DeliveryID:  attempt.Delivery.DeliveryID,
			LeaseID:     attempt.Delivery.LeaseID,
			Disposition: "handled",
		}, dest)
		if err != nil || done.State != "handled" {
			t.Fatalf("complete running work: %+v %v", done, err)
		}

		// Supervisor finishes.
		finished, err := b.ChangeWake(ctx, WakeChangeRequest{
			RequestID: uuid.NewString(),
			AttemptID: attempt.ID,
			Operation: "finish",
			Reason:    "fixture_cgroup_stopped",
		}, dest)
		if err != nil || finished.FinishedAt == nil || finished.State != "finished" {
			t.Fatalf("finish: %+v %v", finished, err)
		}
	})
}

// 4. Native held or manual live-leased cancellation returns UNSUPPORTED_CONTROL and retains original work and hold;
// hard deadline publication to unmanaged/native/topic destinations refuses with UNSUPPORTED_CONTROL.
func TestNativeAndManualCancellationRefusalAndDeadlineValidation(t *testing.T) {
	ctx := context.Background()

	t.Run("NativeHeldCancellationRefusalRetainsWorkAndHold", func(t *testing.T) {
		a, b, source, dest := eventFixture(t)
		op, _ := testOperator(t)
		event, err := a.PublishEvent(ctx, PublishEventRequest{
			RequestID:   uuid.NewString(),
			Kind:        "request",
			Ref:         RecordVersionRef{RecordID: source.RecordID, Version: 1},
			Destination: EventDestination{Type: "agent", Name: b.channel.Principal},
		}, dest)
		if err != nil {
			t.Fatal(err)
		}

		next, err := b.NextEvent(ctx, NextEventRequest{}, dest)
		if err != nil || next.Delivery == nil {
			t.Fatalf("next event: %+v %v", next, err)
		}

		// Simulate active native session attempt holding this delivery.
		agentID := uuid.NewString()
		_, err = b.pool.Exec(ctx, `INSERT INTO cairn.agent_session(agent_id,repo,owner,binding,native_session_id,visibility,execution_id,database_generation,metadata) VALUES($1,$2,$3,'test-binding',$4,'hosted',$5,1,'{}'::jsonb)`,
			agentID, source.Scope.Repo, b.channel.Principal, uuid.NewString(), uuid.NewString())
		if err != nil {
			t.Fatal(err)
		}
		sessionAttemptID := uuid.NewString()
		_, err = b.pool.Exec(ctx, `INSERT INTO cairn.agent_session_attempt(attempt_id,agent_id,execution_id,owner,delivery_id,lease_id) VALUES($1,$2,$3,$4,$5,$6)`,
			sessionAttemptID, agentID, uuid.NewString(), b.channel.Principal, next.Delivery.DeliveryID, next.Delivery.LeaseID)
		if err != nil {
			t.Fatal(err)
		}

		// Operator cancel on native held work must be refused.
		_, err = op.CancelWork(ctx, CancelWorkRequest{
			RequestID:  uuid.NewString(),
			Repo:       source.Scope.Repo,
			DeliveryID: next.Delivery.DeliveryID,
			Reason:     "Operator cancel native",
		})
		requireCode(t, err, "UNSUPPORTED_CONTROL")

		// Delivery remains leased.
		status, err := a.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
		if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].State != "leased" {
			t.Fatalf("delivery state changed: %+v %v", status, err)
		}

		// Native session attempt hold remains active.
		var finishedAt *time.Time
		err = b.pool.QueryRow(ctx, `SELECT finished_at FROM cairn.agent_session_attempt WHERE attempt_id=$1`, sessionAttemptID).Scan(&finishedAt)
		if err != nil || finishedAt != nil {
			t.Fatalf("native hold lost: %v", err)
		}

		// Cleanup: finish session attempt and complete event.
		if _, err = b.pool.Exec(ctx, `UPDATE cairn.agent_session_attempt SET finished_at=clock_timestamp() WHERE attempt_id=$1`, sessionAttemptID); err != nil {
			t.Fatal(err)
		}
		if _, err = b.CompleteEvent(ctx, CompleteEventRequest{
			RequestID:   uuid.NewString(),
			DeliveryID:  next.Delivery.DeliveryID,
			LeaseID:     next.Delivery.LeaseID,
			Disposition: "handled",
		}, dest); err != nil {
			t.Fatal(err)
		}
	})

	t.Run("ManualLiveLeasedCancellationRefusalRetainsWork", func(t *testing.T) {
		a, b, source, dest := eventFixture(t)
		op, _ := testOperator(t)
		event, err := a.PublishEvent(ctx, PublishEventRequest{
			RequestID:   uuid.NewString(),
			Kind:        "request",
			Ref:         RecordVersionRef{RecordID: source.RecordID, Version: 1},
			Destination: EventDestination{Type: "agent", Name: b.channel.Principal},
		}, dest)
		if err != nil {
			t.Fatal(err)
		}

		next, err := b.NextEvent(ctx, NextEventRequest{}, dest)
		if err != nil || next.Delivery == nil {
			t.Fatalf("next event: %+v %v", next, err)
		}

		// CancelWork on manual live lease (without wake) must be refused.
		_, err = op.CancelWork(ctx, CancelWorkRequest{
			RequestID:  uuid.NewString(),
			Repo:       source.Scope.Repo,
			DeliveryID: next.Delivery.DeliveryID,
			Reason:     "Operator cancel manual lease",
		})
		requireCode(t, err, "UNSUPPORTED_CONTROL")

		// Delivery remains leased and can be completed.
		status, err := a.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
		if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].State != "leased" {
			t.Fatalf("delivery state changed: %+v %v", status, err)
		}

		done, err := b.CompleteEvent(ctx, CompleteEventRequest{
			RequestID:   uuid.NewString(),
			DeliveryID:  next.Delivery.DeliveryID,
			LeaseID:     next.Delivery.LeaseID,
			Disposition: "handled",
		}, dest)
		if err != nil || done.State != "handled" {
			t.Fatalf("manual complete: %+v %v", done, err)
		}
	})

	t.Run("TaskDeadlineDestinationRefusalAndAcceptance", func(t *testing.T) {
		_, a, b, source, dest, w := poolFixture(t)
		future := time.Now().Add(time.Hour)

		// 1. Topic destination refuses hard deadline.
		_, err := a.PublishEvent(ctx, PublishEventRequest{
			RequestID:    uuid.NewString(),
			Kind:         "request",
			Ref:          RecordVersionRef{RecordID: source.RecordID, Version: 1},
			Destination:  EventDestination{Type: "topic", Name: "engineering"},
			TaskDeadline: &future,
		}, dest)
		requireCode(t, err, "UNSUPPORTED_CONTROL")

		// 2. Unmanaged agent destination refuses hard deadline.
		_, err = a.PublishEvent(ctx, PublishEventRequest{
			RequestID:    uuid.NewString(),
			Kind:         "request",
			Ref:          RecordVersionRef{RecordID: source.RecordID, Version: 1},
			Destination:  EventDestination{Type: "agent", Name: "unmanaged-agent-" + uuid.NewString()},
			TaskDeadline: &future,
		}, dest)
		requireCode(t, err, "UNSUPPORTED_CONTROL")

		// 3. Pool destination accepts hard deadline.
		poolEvent, err := a.PublishEvent(ctx, PublishEventRequest{
			RequestID:    uuid.NewString(),
			Kind:         "request",
			Ref:          RecordVersionRef{RecordID: source.RecordID, Version: 1},
			Destination:  EventDestination{Type: "pool", Name: "coding"},
			Pool:         &PoolRequirements{Workspace: w.Spec.Workspace},
			TaskDeadline: &future,
		}, dest)
		if err != nil || poolEvent.TaskDeadline == nil {
			t.Fatalf("pool task deadline publish: %+v %v", poolEvent, err)
		}

		// 4. Managed agent destination (with configured worker slot) accepts hard deadline.
		agentEvent, err := a.PublishEvent(ctx, PublishEventRequest{
			RequestID:    uuid.NewString(),
			Kind:         "request",
			Ref:          RecordVersionRef{RecordID: source.RecordID, Version: 1},
			Destination:  EventDestination{Type: "agent", Name: b.channel.Principal},
			TaskDeadline: &future,
		}, dest)
		if err != nil || agentEvent.TaskDeadline == nil {
			t.Fatalf("managed agent task deadline publish: %+v %v", agentEvent, err)
		}
	})
}

// 5. Sequential orderings of pool cancellation vs worker assignment: yields one retained cancelled target, never another delivery, exact retry stable.
func TestPoolCancellationRacingAssignment(t *testing.T) {
	ctx := context.Background()

	t.Run("CancelBeforeAssignmentClosesPoolRequestIdempotently", func(t *testing.T) {
		op, a, b, source, dest, w := poolFixture(t)
		event := poolPublish(t, a, source, dest, PoolRequirements{Workspace: w.Spec.Workspace})

		// Cancel unassigned pool request by PoolEventID.
		cancelReqID := uuid.NewString()
		req := CancelWorkRequest{
			RequestID:   cancelReqID,
			Repo:        source.Scope.Repo,
			PoolEventID: event.EventID,
			Reason:      "Operator cancel unassigned pool request",
		}
		stopped, err := op.CancelWork(ctx, req)
		if err != nil {
			t.Fatalf("cancel unassigned pool: %v", err)
		}
		if stopped.Held || stopped.DeliveryID != "" || stopped.EventID != event.EventID {
			t.Fatalf("unexpected cancel shape: %+v", stopped)
		}
		if stopped.Control == nil || stopped.Control.Code != "operator_cancelled" {
			t.Fatalf("expected operator_cancelled code, got: %+v", stopped.Control)
		}
		if stopped.Control.Reason != "Operator cancel unassigned pool request" {
			t.Fatalf("operator reason missing: %+v", stopped.Control)
		}

		// Exact retry with identical RequestID is stable.
		retry, err := op.CancelWork(ctx, req)
		if err != nil || retry.EventID != stopped.EventID || retry.Control.Code != stopped.Control.Code || !retry.Control.At.Equal(stopped.Control.At) {
			t.Fatalf("cancel retry mismatch: %+v vs %+v (%v)", retry, stopped, err)
		}

		// Repeat with new RequestID fails with VERSION_CONFLICT (already terminal).
		_, err = op.CancelWork(ctx, CancelWorkRequest{
			RequestID:   uuid.NewString(),
			Repo:        source.Scope.Repo,
			PoolEventID: event.EventID,
			Reason:      "Second cancel attempt",
		})
		requireCode(t, err, "VERSION_CONFLICT")

		// Public status shows pool closed with operator_cancelled; operator reason omitted.
		status, err := a.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
		if err != nil || status.Pool == nil || status.Pool.State != "failed" || status.Pool.Control == nil || status.Pool.Control.Code != "operator_cancelled" || status.Pool.Control.Reason != "" || len(status.Deliveries) != 0 {
			t.Fatalf("public pool status: %+v %v", status, err)
		}

		// Worker claim never yields a delivery for this cancelled request.
		attempt := poolClaim(t, b, w, dest)
		if attempt != nil {
			t.Fatalf("cancelled pool request assigned delivery: %+v", attempt)
		}

		// Queued events review shows 0 items.
		queued, err := op.ReviewQueuedEvents(ctx, source.Scope.Repo, 0, 100)
		if err != nil || len(queued.Events) != 0 {
			t.Fatalf("cancelled event remains in queue review: %+v %v", queued, err)
		}
	})

	t.Run("CancelAfterAssignmentCancelsDeliveryAndRetainsHold", func(t *testing.T) {
		op, a, b, source, dest, w := poolFixture(t)
		event := poolPublish(t, a, source, dest, PoolRequirements{Workspace: w.Spec.Workspace})

		// Worker claims request -> assigned.
		attempt := poolClaim(t, b, w, dest)
		if attempt == nil {
			t.Fatal("expected wake attempt")
		}

		// Operator cancels via PoolEventID while delivery is assigned.
		cancelReqID := uuid.NewString()
		req := CancelWorkRequest{
			RequestID:   cancelReqID,
			Repo:        source.Scope.Repo,
			PoolEventID: event.EventID,
			Reason:      "Operator cancel assigned pool request",
		}
		stopped, err := op.CancelWork(ctx, req)
		if err != nil {
			t.Fatalf("cancel assigned pool: %v", err)
		}
		if !stopped.Held || stopped.DeliveryID != attempt.Delivery.DeliveryID || stopped.EventID != event.EventID {
			t.Fatalf("unexpected cancel shape: %+v", stopped)
		}
		if stopped.Control == nil || stopped.Control.Code != "operator_cancelled" {
			t.Fatalf("expected operator_cancelled code, got: %+v", stopped.Control)
		}

		// Exact retry is stable.
		retry, err := op.CancelWork(ctx, req)
		if err != nil || retry.DeliveryID != stopped.DeliveryID || retry.Control.Code != stopped.Control.Code || !retry.Control.At.Equal(stopped.Control.At) {
			t.Fatalf("retry mismatch: %+v vs %+v (%v)", retry, stopped, err)
		}

		// Public status shows delivery failed; operator reason omitted.
		status, err := a.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
		if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].State != "failed" || status.Deliveries[0].Code != "operator_cancelled" || status.Deliveries[0].Control == nil || status.Deliveries[0].Control.Reason != "" {
			t.Fatalf("public delivery status leaked reason or bad state: %+v %v", status, err)
		}

		// Worker completion is fenced.
		_, err = b.CompleteEvent(ctx, CompleteEventRequest{
			RequestID:   uuid.NewString(),
			DeliveryID:  attempt.Delivery.DeliveryID,
			LeaseID:     attempt.Delivery.LeaseID,
			Disposition: "handled",
		}, dest)
		requireCode(t, err, "STALE_LEASE")

		// Never another delivery generated.
		allowFixtureLaunch(t, b)
		secondAttempt := poolClaim(t, b, w, dest)
		if secondAttempt != nil {
			t.Fatalf("unexpected second delivery: %+v", secondAttempt)
		}

		// Wake hold retained until supervisor finishes.
		active, err := b.WakeAttempts(ctx, WakeQuery{Active: true}, dest)
		if err != nil || len(active.Attempts) != 1 || active.Attempts[0].FinishedAt != nil {
			t.Fatalf("wake hold lost: %+v %v", active, err)
		}

		finished, err := b.ChangeWake(ctx, WakeChangeRequest{
			RequestID: uuid.NewString(),
			AttemptID: attempt.ID,
			Operation: "finish",
			Reason:    "fixture_cgroup_stopped",
		}, dest)
		if err != nil || finished.FinishedAt == nil || finished.State != "finished" {
			t.Fatalf("finish: %+v %v", finished, err)
		}

		activeAfter, err := b.WakeAttempts(ctx, WakeQuery{Active: true}, dest)
		if err != nil || len(activeAfter.Attempts) != 0 {
			t.Fatalf("active wakes remain: %+v %v", activeAfter, err)
		}
	})
}

// 6. Actual concurrent CancelWork vs CompleteEvent race using goroutines, barrier, and separate store transactions.
// Each iteration asserts exactly one durable winner/completion fence, retained wake hold until finish, and stable retry.
func TestConcurrentCancelWorkVsCompleteEventRace(t *testing.T) {
	ctx := context.Background()
	const iterations = 8

	for i := 0; i < iterations; i++ {
		op, a, b, source, dest, w := poolFixture(t)
		event := poolPublish(t, a, source, dest, PoolRequirements{Workspace: w.Spec.Workspace})
		attempt := poolClaim(t, b, w, dest)
		if attempt == nil {
			t.Fatalf("iter %d: expected wake attempt", i)
		}
		for _, opName := range []string{"start", "enter"} {
			if _, err := b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: attempt.ID, Operation: opName}, dest); err != nil {
				t.Fatalf("iter %d: %v", i, err)
			}
		}

		var (
			cancelErr    error
			cancelResult WorkCancellation
			completeErr  error
			completeRes  AgentDelivery
			wg           sync.WaitGroup
			barrier      = make(chan struct{})
		)

		cancelReq := CancelWorkRequest{
			RequestID:  uuid.NewString(),
			Repo:       source.Scope.Repo,
			DeliveryID: attempt.Delivery.DeliveryID,
			Reason:     "Concurrent cancel race",
		}
		completeReq := CompleteEventRequest{
			RequestID:   uuid.NewString(),
			DeliveryID:  attempt.Delivery.DeliveryID,
			LeaseID:     attempt.Delivery.LeaseID,
			Disposition: "handled",
		}

		wg.Add(2)
		go func() {
			defer wg.Done()
			<-barrier
			cancelResult, cancelErr = op.CancelWork(ctx, cancelReq)
		}()
		go func() {
			defer wg.Done()
			<-barrier
			completeRes, completeErr = b.CompleteEvent(ctx, completeReq, dest)
		}()

		close(barrier)
		wg.Wait()

		cancelWon := cancelErr == nil && Code(completeErr) == "STALE_LEASE"
		completeWon := completeErr == nil && Code(cancelErr) == "VERSION_CONFLICT"

		if !cancelWon && !completeWon {
			t.Fatalf("iter %d: expected exactly one durable winner; cancelErr=%v (%+v), completeErr=%v (%+v)", i, cancelErr, cancelResult, completeErr, completeRes)
		}

		if cancelWon {
			if !cancelResult.Held || cancelResult.Control == nil || cancelResult.Control.Code != "operator_cancelled" {
				t.Fatalf("iter %d: unexpected cancel winner result: %+v", i, cancelResult)
			}
			// Stable exact retry.
			retry, err := op.CancelWork(ctx, cancelReq)
			if err != nil || retry.DeliveryID != cancelResult.DeliveryID || retry.Control.Code != cancelResult.Control.Code || !retry.Control.At.Equal(cancelResult.Control.At) {
				t.Fatalf("iter %d: retry mismatch: %+v vs %+v (%v)", i, retry, cancelResult, err)
			}
		} else {
			if completeRes.State != "handled" {
				t.Fatalf("iter %d: expected handled completion, got %+v", i, completeRes)
			}
		}

		// Verify public event status matches winner.
		status, err := a.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
		if err != nil || len(status.Deliveries) != 1 {
			t.Fatalf("iter %d: bad status: %+v %v", i, status, err)
		}
		if cancelWon && (status.Deliveries[0].State != "failed" || status.Deliveries[0].Code != "operator_cancelled") {
			t.Fatalf("iter %d: expected failed status on cancel win: %+v", i, status)
		}
		if completeWon && status.Deliveries[0].State != "handled" {
			t.Fatalf("iter %d: expected handled status on complete win: %+v", i, status)
		}

		// Wake hold is strictly retained until supervisor finish in both outcomes.
		active, err := b.WakeAttempts(ctx, WakeQuery{Active: true}, dest)
		if err != nil || len(active.Attempts) != 1 || active.Attempts[0].FinishedAt != nil {
			t.Fatalf("iter %d: wake hold lost before supervisor finish: %+v %v", i, active, err)
		}

		finished, err := b.ChangeWake(ctx, WakeChangeRequest{
			RequestID: uuid.NewString(),
			AttemptID: attempt.ID,
			Operation: "finish",
			Reason:    "fixture_cgroup_stopped",
		}, dest)
		if err != nil || finished.FinishedAt == nil || finished.State != "finished" {
			t.Fatalf("iter %d: finish wake: %+v %v", i, finished, err)
		}

		activeAfter, err := b.WakeAttempts(ctx, WakeQuery{Active: true}, dest)
		if err != nil || len(activeAfter.Attempts) != 0 {
			t.Fatalf("iter %d: active wakes remain after finish: %+v %v", i, activeAfter, err)
		}
	}
}

// 7. Actual concurrent CancelWork vs ClaimWake race on a pool request.
// Each iteration asserts exactly one retained target, never duplicate deliveries, retained wake hold if assigned, and stable retry.
func TestConcurrentCancelWorkVsClaimWakeRace(t *testing.T) {
	ctx := context.Background()
	const iterations = 8

	for i := 0; i < iterations; i++ {
		op, a, b, source, dest, w := poolFixture(t)
		event := poolPublish(t, a, source, dest, PoolRequirements{Workspace: w.Spec.Workspace})

		var (
			cancelErr    error
			cancelResult WorkCancellation
			claimErr     error
			claimResult  WakeResult
			wg           sync.WaitGroup
			barrier      = make(chan struct{})
		)

		cancelReq := CancelWorkRequest{
			RequestID:   uuid.NewString(),
			Repo:        source.Scope.Repo,
			PoolEventID: event.EventID,
			Reason:      "Concurrent pool cancel race",
		}
		claimReq := WakeClaimRequest{
			RequestID: uuid.NewString(),
			WorkerID:  w.SupervisorID,
		}

		wg.Add(2)
		go func() {
			defer wg.Done()
			<-barrier
			cancelResult, cancelErr = op.CancelWork(ctx, cancelReq)
		}()
		go func() {
			defer wg.Done()
			<-barrier
			claimResult, claimErr = b.ClaimWake(ctx, claimReq, dest)
		}()

		close(barrier)
		wg.Wait()

		if cancelErr != nil {
			t.Fatalf("iter %d: cancel work failed: %v", i, cancelErr)
		}
		if claimErr != nil {
			t.Fatalf("iter %d: claim wake failed: %v", i, claimErr)
		}

		cancelFirst := cancelResult.DeliveryID == "" && !cancelResult.Held && claimResult.Attempt == nil
		claimFirst := claimResult.Attempt != nil && cancelResult.DeliveryID == claimResult.Attempt.Delivery.DeliveryID && cancelResult.Held

		if !cancelFirst && !claimFirst {
			t.Fatalf("iter %d: unexpected concurrent outcome: cancel=%+v, claim=%+v", i, cancelResult, claimResult)
		}

		// Exact retry of cancellation is stable.
		retry, err := op.CancelWork(ctx, cancelReq)
		if err != nil || retry.DeliveryID != cancelResult.DeliveryID || retry.Control.Code != cancelResult.Control.Code || !retry.Control.At.Equal(cancelResult.Control.At) {
			t.Fatalf("iter %d: retry mismatch: %+v vs %+v (%v)", i, retry, cancelResult, err)
		}

		// Verify no duplicate delivery ever created.
		allowFixtureLaunch(t, b)
		secondClaim, err := b.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString(), WorkerID: w.SupervisorID}, dest)
		if err != nil || secondClaim.Attempt != nil {
			t.Fatalf("iter %d: unexpected second claim: %+v %v", i, secondClaim, err)
		}

		if claimFirst {
			// If assigned, completion must be fenced.
			_, err = b.CompleteEvent(ctx, CompleteEventRequest{
				RequestID:   uuid.NewString(),
				DeliveryID:  claimResult.Attempt.Delivery.DeliveryID,
				LeaseID:     claimResult.Attempt.Delivery.LeaseID,
				Disposition: "handled",
			}, dest)
			requireCode(t, err, "STALE_LEASE")

			// Wake hold retained until finish.
			active, err := b.WakeAttempts(ctx, WakeQuery{Active: true}, dest)
			if err != nil || len(active.Attempts) != 1 || active.Attempts[0].FinishedAt != nil {
				t.Fatalf("iter %d: wake hold lost on assigned cancel: %+v %v", i, active, err)
			}

			finished, err := b.ChangeWake(ctx, WakeChangeRequest{
				RequestID: uuid.NewString(),
				AttemptID: claimResult.Attempt.ID,
				Operation: "finish",
				Reason:    "fixture_cgroup_stopped",
			}, dest)
			if err != nil || finished.FinishedAt == nil || finished.State != "finished" {
				t.Fatalf("iter %d: finish wake: %+v %v", i, finished, err)
			}

			activeAfter, err := b.WakeAttempts(ctx, WakeQuery{Active: true}, dest)
			if err != nil || len(activeAfter.Attempts) != 0 {
				t.Fatalf("iter %d: active wakes remain: %+v %v", i, activeAfter, err)
			}
		} else {
			// If cancelled before assignment, review queue shows 0 events.
			queued, err := op.ReviewQueuedEvents(ctx, source.Scope.Repo, 0, 100)
			if err != nil || len(queued.Events) != 0 {
				t.Fatalf("iter %d: cancelled event remains in queue: %+v %v", i, queued, err)
			}
		}
	}
}
