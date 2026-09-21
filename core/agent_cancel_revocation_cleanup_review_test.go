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
			t.Logf("fresh persisted read: hold_present=%v finished=%v delivery=%s reason=%s turn=%q scan=%q gap=%v", control.Attempt != nil, persisted.FinishedAt, persisted.Delivery.State, persisted.Reason, persisted.TurnStopState, persisted.TerminalScan, persisted.CaptureGap)
			if !tc.ready && Code(e) != "CLEANUP_UNCONFIRMED" {
				t.Fatalf("incomplete cleanup released revocation: err=%v finished=%v turn=%q scan=%q capture=%q gap=%v tools=%+v", e, got.FinishedAt, got.TurnStopState, got.TerminalScan, got.CaptureState, got.CaptureGap, got.Tools)
			}
			if tc.ready && (e != nil || got.FinishedAt == nil || got.Reason != "exclusivity_revoked" || got.Delivery.Code != "operator_cancelled" || got.Cancel.ConfirmedAt != nil) {
				t.Fatalf("positive revoked cleanup incorrect: attempt=%+v err=%v", got, e)
			}
		})
	}
}
