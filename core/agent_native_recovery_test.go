package core

import (
	"context"
	"sync"
	"testing"

	"github.com/google/uuid"
)

// Native completion recovery: a result produced across a partition longer
// than the lease is accepted only under the same still-open hold, execution
// and generation, and says it was late. Every closed or foreign hold refuses.

type nativeRecovery struct {
	op, receiver, sender, view *Store
	ref                        AgentSessionRef
	attempt, delivery, lease   string
	repo                       string
	event                      string
	dest                       Destination
}

func nativeRecoveryFixture(t *testing.T) nativeRecovery {
	t.Helper()
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	a, err := receiver.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "native-recovery", NativeSessionID: "native-recovery", Metadata: AgentMetadata{Harness: "claude", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{a.AgentID, a.ExecutionID}
	event, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", a.Inbox}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	claim, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}, dest)
	if err != nil || claim.Attempt == nil {
		t.Fatalf("claim: %+v %v", claim, err)
	}
	view, err := receiver.ForAgentSession(ref, dest)
	if err != nil {
		t.Fatal(err)
	}
	return nativeRecovery{
		op:       testStore(t, Channel{Principal: "recovery-operator-" + source.Scope.Repo, Operator: true}),
		receiver: receiver, sender: sender, view: view, ref: ref, attempt: claim.Attempt.ID,
		delivery: claim.Attempt.Delivery.DeliveryID, lease: claim.Attempt.Delivery.LeaseID,
		repo: source.Scope.Repo, event: event.EventID, dest: dest,
	}
}

func (f nativeRecovery) expireLease(t *testing.T) {
	t.Helper()
	if _, err := f.receiver.pool.Exec(context.Background(), `UPDATE cairn.agent_delivery SET lease_until=clock_timestamp()-interval '1 second' WHERE delivery_id=$1`, f.delivery); err != nil {
		t.Fatal(err)
	}
}

func (f nativeRecovery) expirePresence(t *testing.T) {
	t.Helper()
	if _, err := f.receiver.pool.Exec(context.Background(), `UPDATE cairn.agent_session SET expires_at=clock_timestamp()-interval '1 second' WHERE agent_id=$1`, f.ref.AgentID); err != nil {
		t.Fatal(err)
	}
}

func (f nativeRecovery) complete(requestID string) (AgentDelivery, error) {
	draft := projectNote(f.repo)
	draft.Sensitivity = "shareable"
	draft.Body = "Selected result produced across a partition"
	return f.view.CompleteEvent(context.Background(), CompleteEventRequest{RequestID: requestID, DeliveryID: f.delivery, LeaseID: f.lease, Disposition: "handled", Draft: &draft}, f.dest)
}

func (f nativeRecovery) release(requestID string) (NativeHoldRelease, error) {
	return f.op.ReleaseNativeHold(context.Background(), NativeHoldReleaseRequest{RequestID: requestID, Repo: f.repo, AttemptID: f.attempt,
		Reason: "Host did not return after the partition; operator accepts uncertain effects", AcceptUncertainEffects: true})
}

func TestNativeLateCompletionUnderUnreleasedHold(t *testing.T) {
	ctx := context.Background()
	f := nativeRecoveryFixture(t)
	f.expireLease(t)
	// Renewal after expiry stays refused: late completion is explicit, never a silent reacquisition.
	_, err := f.view.ChangeEventLease(ctx, EventLeaseRequest{DeliveryID: f.delivery, LeaseID: f.lease, LeaseSeconds: 90}, true, f.dest)
	requireCode(t, err, "STALE_LEASE")
	// A wrong lease never qualifies, even while the hold is open.
	_, err = f.view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: f.delivery, LeaseID: uuid.NewString(), Disposition: "handled"}, f.dest)
	requireCode(t, err, "STALE_LEASE")
	// While the lease is lapsed and the hold is open, no other consumer can take the work.
	next, err := f.view.NextEvent(ctx, NextEventRequest{}, f.dest)
	if err != nil || next.Delivery != nil {
		t.Fatalf("second consumer acquired held work: %+v %v", next, err)
	}
	empty, err := f.receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: f.ref}, f.dest)
	if err != nil || empty.Attempt != nil {
		t.Fatalf("second native claim acquired held work: %+v %v", empty, err)
	}

	request := uuid.NewString()
	done, err := f.complete(request)
	if err != nil || done.State != "handled" || !done.Late || done.Result == nil {
		t.Fatalf("late completion: %+v %v", done, err)
	}
	// A lost reply replays the committed response with the same result record.
	again, err := f.complete(request)
	if err != nil || again.Result == nil || *again.Result != *done.Result || !again.Late {
		t.Fatalf("late replay: %+v %v", again, err)
	}
	status, err := f.sender.AgentEventStatus(ctx, EventStatusRequest{EventID: f.event}, f.dest)
	if err != nil || len(status.Deliveries) != 1 || !status.Deliveries[0].Late || status.Deliveries[0].State != "handled" {
		t.Fatalf("status does not show late completion: %+v %v", status, err)
	}
	review, err := f.op.ReviewEvents(ctx, EventReviewRequest{Repo: f.repo, DeliveryID: f.delivery})
	if err != nil || len(review.Deliveries) != 1 || !review.Deliveries[0].Late || !review.Deliveries[0].Held {
		t.Fatalf("review does not show the late completion under its hold: %+v %v", review, err)
	}
	closed, err := f.receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: f.ref, AttemptID: f.attempt, Reason: "delivery_completed"}, f.dest)
	if err != nil || closed.FinishedAt == nil || closed.Delivery.State != "handled" || !closed.Delivery.Late {
		t.Fatalf("reconcile after late completion: %+v %v", closed, err)
	}
}

func TestNativeLateCompletionRefusedAfterHoldCloses(t *testing.T) {
	ctx := context.Background()
	// The host reported the turn ended before the result arrived.
	f := nativeRecoveryFixture(t)
	f.expireLease(t)
	if _, err := f.receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: f.ref, AttemptID: f.attempt, Reason: "turn_ended"}, f.dest); err != nil {
		t.Fatal(err)
	}
	_, err := f.complete(uuid.NewString())
	requireCode(t, err, "HOLD_RELEASED")
	_, err = f.view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: f.delivery, LeaseID: f.lease, Disposition: "handled"}, f.dest)
	requireCode(t, err, "HOLD_RELEASED")

	// A session that left cannot complete: the execution check precedes any lease rule.
	g := nativeRecoveryFixture(t)
	g.expireLease(t)
	if _, err = g.receiver.LeaveAgent(ctx, g.ref, g.dest); err != nil {
		t.Fatal(err)
	}
	_, err = g.complete(uuid.NewString())
	requireCode(t, err, "STALE_SESSION")

	// Pending cancellation still wins over a lapsed lease.
	op, receiver, _, ref, attemptID, dest := nativeCancelFixture(t)
	view, err := receiver.ForAgentSession(ref, dest)
	if err != nil {
		t.Fatal(err)
	}
	delivery := attemptDelivery(t, receiver, ref, attemptID, dest)
	if _, err = op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: attemptRepo(t, receiver, ref, attemptID, dest), DeliveryID: delivery, Reason: "Operator stops work before the partition heals"}); err != nil {
		t.Fatal(err)
	}
	if _, err = receiver.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET lease_until=clock_timestamp()-interval '1 second' WHERE delivery_id=$1`, delivery); err != nil {
		t.Fatal(err)
	}
	_, err = view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery, LeaseID: attemptLease(t, receiver, ref, attemptID, dest), Disposition: "handled"}, dest)
	requireCode(t, err, "REQUEST_CANCELLED")
}

func TestNativeHoldReleaseRequiresOperatorOfflineSessionAndAcceptance(t *testing.T) {
	ctx := context.Background()
	f := nativeRecoveryFixture(t)
	_, err := f.receiver.ReleaseNativeHold(ctx, NativeHoldReleaseRequest{RequestID: uuid.NewString(), Repo: f.repo, AttemptID: f.attempt, Reason: "An agent profile cannot release holds", AcceptUncertainEffects: true})
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = f.op.ReleaseNativeHold(ctx, NativeHoldReleaseRequest{RequestID: uuid.NewString(), Repo: f.repo, AttemptID: f.attempt, Reason: "Missing uncertainty acceptance"})
	requireCode(t, err, "INVALID_REQUEST")
	// A present session keeps ownership; its own host must reconcile.
	_, err = f.release(uuid.NewString())
	requireCode(t, err, "SESSION_LIVE")

	f.expirePresence(t)
	request := uuid.NewString()
	released, err := f.release(request)
	if err != nil || released.Reason != "operator_released" || released.DeliveryState != "failed" || released.Code != "operator_released" ||
		released.ReleasedBy != f.op.channel.Principal || released.Session != f.ref {
		t.Fatalf("release: %+v %v", released, err)
	}
	again, err := f.release(request)
	if err != nil || again != released {
		t.Fatalf("release replay: %+v %v", again, err)
	}
	_, err = f.release(uuid.NewString())
	requireCode(t, err, "VERSION_CONFLICT")
	review, err := f.op.ReviewEvents(ctx, EventReviewRequest{Repo: f.repo, DeliveryID: f.delivery})
	if err != nil || len(review.Deliveries) != 1 || review.Deliveries[0].Held || review.Deliveries[0].LatestNative == nil ||
		review.Deliveries[0].LatestNative.Reason != "operator_released" || review.Deliveries[0].LatestNative.ReleasedBy != f.op.channel.Principal ||
		review.Deliveries[0].Control == nil {
		t.Fatalf("review after release: %+v %v", review, err)
	}
	// The returning host is told its result was not accepted.
	_, err = f.complete(uuid.NewString())
	requireCode(t, err, "HOLD_RELEASED")
	// With no hold left, the operator may reissue as before.
	if _, err = f.op.ReissueEvent(ctx, ReissueEventRequest{RequestID: uuid.NewString(), Repo: f.repo, DeliveryID: f.delivery, Reason: "Reviewed the lost host; repeat the request", AcceptUncertainEffects: true}); err != nil {
		t.Fatal(err)
	}
	// A registration can resume the conversation once the hold is closed.
	if _, err = f.receiver.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "native-recovery", NativeSessionID: "native-recovery", Metadata: AgentMetadata{Harness: "claude", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"}}, f.dest); err != nil {
		t.Fatal(err)
	}
}

func TestNativeHoldReleaseAfterCompletionKeepsResult(t *testing.T) {
	f := nativeRecoveryFixture(t)
	done, err := f.complete(uuid.NewString())
	if err != nil || done.Late {
		t.Fatalf("on-time completion: %+v %v", done, err)
	}
	f.expirePresence(t)
	released, err := f.release(uuid.NewString())
	if err != nil || released.DeliveryState != "handled" || released.Code != "" {
		t.Fatalf("release after completion changed the result: %+v %v", released, err)
	}
}

func TestNativeHoldReleaseRacesLateCompletion(t *testing.T) {
	ctx := context.Background()
	for range 8 {
		f := nativeRecoveryFixture(t)
		f.expireLease(t)
		f.expirePresence(t)
		var done AgentDelivery
		var completeErr, releaseErr error
		var wg sync.WaitGroup
		wg.Add(2)
		go func() {
			defer wg.Done()
			done, completeErr = f.complete(uuid.NewString())
		}()
		go func() {
			defer wg.Done()
			_, releaseErr = f.release(uuid.NewString())
		}()
		wg.Wait()
		if releaseErr != nil {
			t.Fatalf("release failed: %v", releaseErr)
		}
		status, err := f.sender.AgentEventStatus(ctx, EventStatusRequest{EventID: f.event}, f.dest)
		if err != nil || len(status.Deliveries) != 1 {
			t.Fatalf("status: %+v %v", status, err)
		}
		final := status.Deliveries[0]
		switch {
		case completeErr == nil:
			// Completion committed first; the release only closed the hold.
			if !done.Late || final.State != "handled" || !final.Late || final.Result == nil {
				t.Fatalf("accepted completion was not retained: %+v / %+v", done, final)
			}
		case Code(completeErr) == "HOLD_RELEASED":
			if final.State != "failed" || final.Result != nil || final.Late {
				t.Fatalf("refused completion left a result: %+v", final)
			}
		default:
			t.Fatalf("unexpected completion outcome: %v", completeErr)
		}
	}
}
