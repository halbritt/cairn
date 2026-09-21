package core

import (
	"context"
	"github.com/google/uuid"
	"testing"
)

func TestReviewPostCancelScanMustFollowTurnStop(t *testing.T) {
	ctx := context.Background()
	op, r, _, ref, aid, dest := nativeCancelFixture(t)
	_, e := op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: attemptRepo(t, r, ref, aid, dest), DeliveryID: attemptDelivery(t, r, ref, aid, dest), Reason: "Review postcancel scan ordering"})
	if e != nil {
		t.Fatal(e)
	}
	_, e = r.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: aid, TerminalScan: "clear"}, dest)
	if e != nil {
		return
	}
	_, e = r.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: aid, TurnStop: "ended"}, dest)
	if e != nil {
		t.Fatal(e)
	}
	got, e := r.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: aid, Reason: "cancel_confirmed"}, dest)
	if Code(e) != "CLEANUP_UNCONFIRMED" {
		t.Fatalf("pre-stop scan released hold: error=%v finished=%v", e, got.FinishedAt)
	}
}
func TestReviewRevocationMustNotReleaseRunningTools(t *testing.T) {
	ctx := context.Background()
	op, r, _, ref, aid, dest := nativeCancelFixture(t)
	_, e := r.CaptureSessionTools(ctx, SessionToolCapture{RequestID: uuid.NewString(), Session: ref, AttemptID: aid, Items: []SessionToolItem{{ItemID: "still-running", ProcessID: "991", NativeTurnID: "turn-one"}}}, dest)
	if e != nil {
		t.Fatal(e)
	}
	_, e = op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: attemptRepo(t, r, ref, aid, dest), DeliveryID: attemptDelivery(t, r, ref, aid, dest), Reason: "Review revoke while tools run"})
	if e != nil {
		t.Fatal(e)
	}
	_, e = r.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: aid, OwnerJoin: true}, dest)
	if e != nil {
		t.Fatal(e)
	}
	got, e := r.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: aid, Reason: "exclusivity_revoked"}, dest)
	if Code(e) != "CLEANUP_UNCONFIRMED" {
		t.Fatalf("revocation released running tools: error=%v finished=%v turn=%q tools=%+v", e, got.FinishedAt, got.TurnStopState, got.Tools)
	}
}
func TestReviewUnrevokedAttemptRejectsRevocationReconcile(t *testing.T) {
	_, r, _, ref, aid, dest := nativeCancelFixture(t)
	got, e := r.ReconcileSessionInbox(context.Background(), SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: aid, Reason: "exclusivity_revoked"}, dest)
	if e == nil {
		t.Fatalf("unrevoked uncancelled running attempt closed: finished=%v exclusive=%v", got.FinishedAt, got.TurnExclusive)
	}
}
