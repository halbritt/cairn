package core

import (
	"context"
	"github.com/google/uuid"
	"testing"
)

func TestReviewClearScanBeforeTurnStopCannotRelease(t *testing.T) {
	ctx := context.Background()
	op, receiver, _, ref, aid, dest := nativeCancelFixture(t)
	report := func(r SessionToolStopReport) {
		t.Helper()
		r.RequestID = uuid.NewString()
		r.Session = ref
		r.AttemptID = aid
		if _, e := receiver.ReportSessionToolStop(ctx, r, dest); e != nil {
			t.Fatal(e)
		}
	}
	report(SessionToolStopReport{CaptureState: "attached", TerminalScan: "clear"})
	_, e := op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: attemptRepo(t, receiver, ref, aid, dest), DeliveryID: attemptDelivery(t, receiver, ref, aid, dest), Reason: "Review cancellation after earlier scan"})
	if e != nil {
		t.Fatal(e)
	}
	report(SessionToolStopReport{CaptureState: "lost"})
	report(SessionToolStopReport{TurnStop: "ended"})
	got, e := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: aid, Reason: "cancel_confirmed"}, dest)
	if Code(e) != "CLEANUP_UNCONFIRMED" {
		t.Fatalf("stale scan released cancellation: err=%v finished=%v capture_gap=%v capture_state=%q scan=%q", e, got.FinishedAt, got.CaptureGap, got.CaptureState, got.TerminalScan)
	}
}
