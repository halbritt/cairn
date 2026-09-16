package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func TestWakeupClaimsOnlyRequestsAndHoldsExpiredDelivery(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	notice, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "response", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"agent", b.channel.Principal}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	request := publishFixture(t, a, b, r, dest)
	req := WakeClaimRequest{RequestID: uuid.NewString(), Repo: r.Scope.Repo}
	wake, err := b.ClaimWake(ctx, req, dest)
	if err != nil || wake.Attempt == nil || wake.Attempt.Delivery.Event.EventID != request.EventID {
		t.Fatalf("claim: %+v %v", wake, err)
	}
	again, err := b.ClaimWake(ctx, req, dest)
	if err != nil || again.Attempt.ID != wake.Attempt.ID {
		t.Fatalf("claim retry: %+v %v", again, err)
	}
	busy, err := b.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString()}, dest)
	if err != nil || busy.Attempt != nil {
		t.Fatalf("second worker: %+v %v", busy, err)
	}
	if _, err = b.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET lease_until=clock_timestamp()-interval '1 second' WHERE delivery_id=$1`, wake.Attempt.Delivery.DeliveryID); err != nil {
		t.Fatal(err)
	}
	ordinary := nextFixture(t, b, dest)
	if ordinary.Event.EventID != notice.EventID {
		t.Fatal("wake hold did not fence ordinary claim")
	}
	next, err := b.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil || next.Delivery != nil {
		t.Fatalf("expired held delivery reclaimed: %+v %v", next, err)
	}
}

func TestWakeupLaunchIsOneShotAndExitDoesNotAcknowledge(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	publishFixture(t, a, b, r, dest)
	claim, err := b.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString()}, dest)
	if err != nil {
		t.Fatal(err)
	}
	id := claim.Attempt.ID
	change := func(op, state string) (WakeAttempt, error) {
		return b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: id, Operation: op, ProcessState: state}, dest)
	}
	if _, err = change("start", ""); err != nil {
		t.Fatal(err)
	}
	errs := make(chan error, 2)
	for range 2 {
		go func() { _, e := change("enter", ""); errs <- e }()
	}
	first, second := <-errs, <-errs
	if (first == nil) == (second == nil) {
		t.Fatalf("launch entered more than once: %v / %v", first, second)
	}
	if _, err = change("report", "exited"); err != nil {
		t.Fatal(err)
	}
	done, err := change("finish", "")
	if err != nil {
		t.Fatal(err)
	}
	if done.Delivery.State != "failed" {
		t.Fatalf("exit fabricated handling: %+v", done)
	}
}

func TestWakeupPrelaunchBackoffAndCompletionSurviveRecovery(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	publishFixture(t, a, b, r, dest)
	for attempt := 1; attempt <= 3; attempt++ {
		claim, err := b.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString()}, dest)
		if err != nil || claim.Attempt == nil {
			t.Fatalf("claim: %+v %v", claim, err)
		}
		w := claim.Attempt
		done, err := b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: w.ID, Operation: "finish", Reason: "interrupted_before_launch"}, dest)
		if err != nil {
			t.Fatal(err)
		}
		next, err := b.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString()}, dest)
		if err != nil || next.Attempt != nil {
			t.Fatalf("backoff bypassed: %+v %v", next, err)
		}
		if attempt == 3 && done.Delivery.State != "failed" {
			t.Fatal("retry limit not enforced")
		}
		if _, err = b.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET available_at=clock_timestamp() WHERE delivery_id=$1`, w.Delivery.DeliveryID); err != nil {
			t.Fatal(err)
		}
	}
	publishFixture(t, a, b, r, dest)
	claim, err := b.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString()}, dest)
	if err != nil {
		t.Fatal(err)
	}
	w := claim.Attempt
	_, err = b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: w.Delivery.DeliveryID, LeaseID: w.Delivery.LeaseID, Disposition: "handled"}, dest)
	if err != nil {
		t.Fatal(err)
	}
	done, err := b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: w.ID, Operation: "finish", Reason: "supervisor_restarted"}, dest)
	if err != nil || done.Delivery.State != "handled" {
		t.Fatalf("completion lost: %+v %v", done, err)
	}
}

func TestWakeupConcurrentClaimsAndRestoreHold(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	publishFixture(t, a, b, r, dest)
	publishFixture(t, a, b, r, dest)
	results := make(chan WakeResult, 8)
	errs := make(chan error, 8)
	for range 8 {
		go func() {
			w, e := b.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString()}, dest)
			results <- w
			errs <- e
		}()
	}
	var w *WakeAttempt
	for range 8 {
		result := <-results
		if err := <-errs; err != nil {
			t.Fatal(err)
		}
		if result.Attempt != nil {
			if w != nil {
				t.Fatal("concurrent workers claimed")
			}
			w = result.Attempt
		}
	}
	if w == nil {
		t.Fatal("no worker claimed")
	}
	other := testStore(t, Channel{Principal: "other", Repo: r.Scope.Repo})
	_, err := other.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: w.ID, Operation: "finish"}, dest)
	requireCode(t, err, "NOT_FOUND")
	op, _ := testOperator(t)
	if _, err = op.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "Disposable wake fencing test"}); err != nil {
		t.Fatal(err)
	}
	_, err = b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: w.ID, Operation: "start"}, dest)
	requireCode(t, err, "STALE_LEASE")
	// The restored pending delivery must retain its wake hold.
	next := nextFixture(t, b, dest)
	if next.DeliveryID == w.Delivery.DeliveryID {
		t.Fatal("restore dropped wake hold")
	}
	page, err := b.WakeAttempts(ctx, WakeQuery{Active: true}, dest)
	if err != nil || len(page.Attempts) != 1 {
		t.Fatalf("restore lost attempt: %+v %v", page, err)
	}
}
