package core

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestAdmissionExpiryPreventsClaimButDoesNotRevokeLiveLease(t *testing.T) {
	ctx := context.Background()
	a, b, source, dest := eventFixture(t)
	expired := time.Now().Add(-time.Second)
	event, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, 1}, Destination: EventDestination{"agent", b.channel.Principal}, AdmissionExpiresAt: &expired}, dest)
	if err != nil {
		t.Fatal(err)
	}
	next, err := b.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil || next.Delivery != nil {
		t.Fatalf("expired claim: %+v %v", next, err)
	}
	status, err := a.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
	if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].State != "failed" || status.Deliveries[0].Code != "admission_expired" || status.Deliveries[0].Attempts != 0 {
		t.Fatalf("expiry audit: %+v %v", status, err)
	}
	later := time.Now().Add(time.Hour)
	event, err = a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, 1}, Destination: EventDestination{"agent", b.channel.Principal}, AdmissionExpiresAt: &later}, dest)
	if err != nil {
		t.Fatal(err)
	}
	live := nextFixture(t, b, dest)
	if _, err = b.pool.Exec(ctx, `UPDATE cairn.agent_event SET admission_expires_at=clock_timestamp()-interval '1 second' WHERE event_id=$1`, event.EventID); err != nil {
		t.Fatal(err)
	}
	renewed, err := b.ChangeEventLease(ctx, EventLeaseRequest{DeliveryID: live.DeliveryID, LeaseID: live.LeaseID}, true, dest)
	if err != nil || renewed.State != "leased" {
		t.Fatalf("admission expiry revoked live work: %+v %v", renewed, err)
	}
	done, err := b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: live.DeliveryID, LeaseID: live.LeaseID, Disposition: "handled"}, dest)
	if err != nil || done.State != "handled" {
		t.Fatalf("completion after admission expiry: %+v %v", done, err)
	}
}

func TestRequestSweepExpiresUnassignedPoolWithoutConsumingCapacity(t *testing.T) {
	ctx := context.Background()
	op, a, b, source, dest, w := poolFixture(t)
	expired := time.Now().Add(-time.Second)
	event, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, 1}, Destination: EventDestination{"pool", "coding"}, Pool: &PoolRequirements{Workspace: w.Spec.Workspace}, AdmissionExpiresAt: &expired}, dest)
	if err != nil {
		t.Fatal(err)
	}
	if got := poolClaim(t, b, w, dest); got != nil {
		t.Fatalf("expired pool assigned: %+v", got)
	}
	if _, err = op.SweepRequestControls(ctx, RequestControlSweep{Repo: source.Scope.Repo}); err != nil {
		t.Fatal(err)
	}
	status, err := a.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
	if err != nil || status.Pool == nil || status.Pool.State != "failed" || status.Pool.Control == nil || status.Pool.Control.Code != "admission_expired" || len(status.Deliveries) != 0 {
		t.Fatalf("pool expiry: %+v %v", status, err)
	}
	queued, err := op.ReviewQueuedEvents(ctx, source.Scope.Repo, 0, 100)
	if err != nil || len(queued.Events) != 0 {
		t.Fatalf("expired still queued: %+v %v", queued, err)
	}
	closed, err := op.ReviewClosedPoolRequests(ctx, source.Scope.Repo, 0, 1)
	if err != nil || len(closed.Events) != 1 || closed.Events[0].EventID != event.EventID || closed.Events[0].Control.Code != "admission_expired" || closed.Events[0].Control.Reason != "admission_expired" {
		t.Fatalf("closed pool audit missing: %+v %v", closed, err)
	}
	next, err := op.ReviewClosedPoolRequests(ctx, source.Scope.Repo, closed.NextAfter, 1)
	if err != nil || len(next.Events) != 0 {
		t.Fatalf("closed pool cursor: %+v %v", next, err)
	}
	_, err = a.ReviewClosedPoolRequests(ctx, source.Scope.Repo, 0, 1)
	requireCode(t, err, "AUTHORITY_DENIED")
}

func TestCancelWorkFencesCompletionButRetainsWakeHold(t *testing.T) {
	ctx := context.Background()
	op, a, b, source, dest, w := poolFixture(t)
	event := poolPublish(t, a, source, dest, PoolRequirements{Workspace: w.Spec.Workspace})
	attempt := poolClaim(t, b, w, dest)
	if attempt == nil {
		t.Fatal("no wake")
	}
	if _, err := b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: attempt.ID, Operation: "start"}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err := b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: attempt.ID, Operation: "enter"}, dest); err != nil {
		t.Fatal(err)
	}
	req := CancelWorkRequest{RequestID: uuid.NewString(), Repo: source.Scope.Repo, PoolEventID: event.EventID, Reason: "Operator selected cancellation"}
	stopped, err := op.CancelWork(ctx, req)
	if err != nil || !stopped.Held || stopped.Control.Code != "operator_cancelled" {
		t.Fatalf("cancel: %+v %v", stopped, err)
	}
	_, err = b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: attempt.Delivery.DeliveryID, LeaseID: attempt.Delivery.LeaseID, Disposition: "handled"}, dest)
	requireCode(t, err, "STALE_LEASE")
	active, err := b.WakeAttempts(ctx, WakeQuery{Active: true}, dest)
	if err != nil || len(active.Attempts) != 1 {
		t.Fatalf("hold lost before stop: %+v %v", active, err)
	}
	if _, err = b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: attempt.ID, Operation: "finish", Reason: "fixture_cgroup_stopped"}, dest); err != nil {
		t.Fatal(err)
	}
	status, err := a.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
	if err != nil || status.Deliveries[0].Code != "operator_cancelled" || status.Deliveries[0].Control.Reason != "" {
		t.Fatalf("control changed or operator reason leaked: %+v %v", status, err)
	}
	again, err := op.CancelWork(ctx, req)
	if err != nil || again.Control == nil || !again.Control.At.Equal(stopped.Control.At) || again.Control.Code != stopped.Control.Code {
		t.Fatalf("cancel retry: %+v %v", again, err)
	}
}

func TestTaskDeadlineFencesCompletionAndRetainsHold(t *testing.T) {
	ctx := context.Background()
	_, a, b, source, dest, w := poolFixture(t)
	future := time.Now().Add(time.Hour)
	event, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, 1}, Destination: EventDestination{"pool", "coding"}, Pool: &PoolRequirements{Workspace: w.Spec.Workspace}, TaskDeadline: &future}, dest)
	if err != nil {
		t.Fatal(err)
	}
	attempt := poolClaim(t, b, w, dest)
	if attempt == nil {
		t.Fatal("no wake")
	}
	for _, operation := range []string{"start", "enter"} {
		if _, err = b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: attempt.ID, Operation: operation}, dest); err != nil {
			t.Fatal(err)
		}
	}
	if _, err = b.pool.Exec(ctx, `UPDATE cairn.agent_event SET task_deadline=clock_timestamp()-interval '1 second' WHERE event_id=$1`, event.EventID); err != nil {
		t.Fatal(err)
	}
	_, err = b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: attempt.Delivery.DeliveryID, LeaseID: attempt.Delivery.LeaseID, Disposition: "handled"}, dest)
	requireCode(t, err, "DEADLINE_EXCEEDED")
	control, err := b.WakeControl(ctx, WakeControlRequest{AttemptID: attempt.ID}, dest)
	if err != nil || control.StopReason != "task_deadline" || control.Attempt.Delivery.Code != "task_deadline" || control.Attempt.FinishedAt != nil {
		t.Fatalf("deadline control: %+v %v", control, err)
	}
}
