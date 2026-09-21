package core

import (
	"context"
	"github.com/google/uuid"
	"testing"
)

func TestReviewRevocationRequiresFullCleanup(t *testing.T) {
	for _, tc := range []struct {
		name                                   string
		unavailable, lost, ended, clear, ready bool
	}{
		{name: "running_turn_no_captured_tools"},
		{name: "unavailable_tool_lost_capture", unavailable: true, lost: true},
		{name: "ended_turn_without_final_scan", ended: true},
		{name: "ended_turn_terminal_tools_fresh_scan", unavailable: true, lost: true, ended: true, clear: true, ready: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx := context.Background()
			op, r, _, ref, aid, dest := nativeCancelFixture(t)
			report := func(req SessionToolStopReport) {
				t.Helper()
				req.RequestID = uuid.NewString()
				req.Session = ref
				req.AttemptID = aid
				if _, e := r.ReportSessionToolStop(ctx, req, dest); e != nil {
					t.Fatal(e)
				}
			}
			if tc.unavailable {
				_, e := r.CaptureSessionTools(ctx, SessionToolCapture{RequestID: uuid.NewString(), Session: ref, AttemptID: aid, Items: []SessionToolItem{{ItemID: "captured-tool", ProcessID: "922", NativeTurnID: "turn-one"}}}, dest)
				if e != nil {
					t.Fatal(e)
				}
			}
			_, e := op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: attemptRepo(t, r, ref, aid, dest), DeliveryID: attemptDelivery(t, r, ref, aid, dest), Reason: "Independent revocation cleanup review"})
			if e != nil {
				t.Fatal(e)
			}
			report(SessionToolStopReport{OwnerJoin: true})
			if tc.unavailable {
				report(SessionToolStopReport{Tools: []SessionToolStop{{ItemID: "captured-tool", StopState: "unavailable"}}})
			}
			if tc.lost {
				report(SessionToolStopReport{CaptureState: "lost", TerminalScan: "unknown_remaining"})
			}
			if tc.ended {
				report(SessionToolStopReport{TurnStop: "ended"})
			}
			if tc.clear {
				report(SessionToolStopReport{TerminalScan: "clear"})
			}
			got, e := r.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: aid, Reason: "exclusivity_revoked"}, dest)
			control, readErr := r.SessionInboxControl(ctx, ref, dest)
			if readErr != nil {
				t.Fatal(readErr)
			}
			persisted, readErr := r.readSessionInboxAttemptForTest(ctx, ref, aid, dest)
			if readErr != nil {
				t.Fatal(readErr)
			}
			// Persisted-state invariants, asserted rather than logged: a
			// refused release leaves the hold active, the attempt unfinished,
			// the delivery leased and the cancellation unconfirmed; a valid
			// release durably removes the hold, finishes with the separate
			// revocation outcome, fails the delivery operator_cancelled and
			// still leaves the cancellation itself unconfirmed.
			if !tc.ready {
				if control.Attempt == nil {
					t.Fatal("refused cleanup released the persisted hold")
				}
				if persisted.FinishedAt != nil {
					t.Fatal("refused cleanup finished the attempt")
				}
				if persisted.Delivery.State != "leased" {
					t.Fatalf("refused cleanup changed the delivery: %s", persisted.Delivery.State)
				}
				if persisted.Cancel == nil || persisted.Cancel.ConfirmedAt != nil {
					t.Fatalf("cancellation not pending after refusal: %+v", persisted.Cancel)
				}
			} else {
				if control.Attempt != nil {
					t.Fatal("valid release retained the persisted hold")
				}
				if persisted.FinishedAt == nil || persisted.Reason != "exclusivity_revoked" {
					t.Fatalf("attempt outcome not persisted: %+v", persisted)
				}
				if persisted.Delivery.State != "failed" || persisted.Delivery.Code != "operator_cancelled" {
					t.Fatalf("delivery outcome not persisted: %+v", persisted.Delivery)
				}
				if persisted.Cancel == nil || persisted.Cancel.ConfirmedAt != nil {
					t.Fatalf("valid release manufactured a confirmation: %+v", persisted.Cancel)
				}
			}
			if !tc.ready && Code(e) != "CLEANUP_UNCONFIRMED" {
				t.Fatalf("incomplete cleanup released revocation: err=%v finished=%v turn=%q scan=%q capture=%q gap=%v tools=%+v", e, got.FinishedAt, got.TurnStopState, got.TerminalScan, got.CaptureState, got.CaptureGap, got.Tools)
			}
			if tc.ready && (e != nil || got.FinishedAt == nil || got.Reason != "exclusivity_revoked" || got.Delivery.Code != "operator_cancelled" || got.Cancel.ConfirmedAt != nil) {
				t.Fatalf("positive revoked cleanup incorrect: attempt=%+v err=%v", got, e)
			}
		})
	}
}
