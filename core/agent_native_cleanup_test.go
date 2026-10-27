package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func TestNativeCleanupReportRollsBackAllEvidenceOnUnknownTool(t *testing.T) {
	ctx := context.Background()
	op, receiver, _, ref, attemptID, dest := nativeCancelFixture(t)
	captured, err := receiver.CaptureSessionTools(ctx, SessionToolCapture{
		RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID,
		Items: []SessionToolItem{{ItemID: "known", ProcessID: "901", NativeTurnID: "turn-one"}},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(),
		Repo: captured.Delivery.Event.Repo, DeliveryID: captured.Delivery.DeliveryID,
		Reason: "Preserve cancellation while a host report is refused"}); err != nil {
		t.Fatal(err)
	}
	if _, err = receiver.ReportSessionToolStop(ctx, SessionToolStopReport{
		RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, TerminalScan: "unknown_remaining",
	}, dest); err != nil {
		t.Fatal(err)
	}

	// The known tool appears first: refusing the later unknown item must undo
	// its terminal update as well as the report's turn, capture and owner evidence.
	_, err = receiver.ReportSessionToolStop(ctx, SessionToolStopReport{
		RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID,
		TurnStop: "ended", CaptureState: "lost", TerminalScan: "clear", OwnerJoin: true,
		Tools: []SessionToolStop{{ItemID: "known", StopState: "terminated"}, {ItemID: "unknown", StopState: "terminated"}},
	}, dest)
	requireCode(t, err, "NOT_FOUND")
	persisted, err := receiver.readSessionInboxAttemptForTest(ctx, ref, attemptID, dest)
	if err != nil {
		t.Fatal(err)
	}
	if persisted.TurnStopState != "" || persisted.CaptureState != "attached" || persisted.CaptureGap ||
		persisted.TerminalScan != "unknown_remaining" || !persisted.TurnExclusive {
		t.Fatalf("refused report changed cleanup evidence: %+v", persisted)
	}
	if len(persisted.Tools) != 1 || persisted.Tools[0].StopState != "captured" || persisted.Tools[0].StopAt != nil {
		t.Fatalf("refused report changed the earlier known tool: %+v", persisted.Tools)
	}
	if persisted.FinishedAt != nil || persisted.Delivery.State != "leased" ||
		persisted.Cancel == nil || persisted.Cancel.ConfirmedAt != nil {
		t.Fatalf("refused report changed cancellation or delivery: %+v", persisted)
	}
	control, err := receiver.SessionInboxControl(ctx, ref, dest)
	if err != nil || control.Attempt == nil || control.Attempt.ID != attemptID {
		t.Fatalf("refused report released the hold: %+v %v", control, err)
	}
}

func TestNativeCleanupLateReportsPreserveTerminalEvidenceAndAttribution(t *testing.T) {
	for _, terminal := range []string{"terminated", "unavailable"} {
		t.Run(terminal, func(t *testing.T) {
			ctx := context.Background()
			op, receiver, _, ref, attemptID, dest := nativeCancelFixture(t)
			captured, err := receiver.CaptureSessionTools(ctx, SessionToolCapture{
				RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID,
				Items: []SessionToolItem{{ItemID: "owned-tool", ProcessID: "902", NativeTurnID: "turn-one"}},
			}, dest)
			if err != nil {
				t.Fatal(err)
			}
			const reason = "Original operator cancellation survives host cleanup"
			if _, err = op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(),
				Repo: captured.Delivery.Event.Repo, DeliveryID: captured.Delivery.DeliveryID, Reason: reason}); err != nil {
				t.Fatal(err)
			}
			other := testStore(t, Channel{Principal: "later-cancel-operator-" + ref.AgentID, Operator: true})
			if _, err = other.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(),
				Repo: captured.Delivery.Event.Repo, DeliveryID: captured.Delivery.DeliveryID,
				Reason: "Later operator observes the already pending cancellation"}); err != nil {
				t.Fatal(err)
			}
			// A clear scan reported together with the new stop and capture loss is
			// fresh evidence. The gap remains recorded even though cleanup is clear.
			stopped, err := receiver.ReportSessionToolStop(ctx, SessionToolStopReport{
				RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID,
				TurnStop: "interrupted", CaptureState: "lost", TerminalScan: "clear",
				Tools: []SessionToolStop{{ItemID: "owned-tool", StopState: terminal}},
			}, dest)
			if err != nil || len(stopped.Tools) != 1 || stopped.Tools[0].StopAt == nil {
				t.Fatalf("terminal report: %+v %v", stopped, err)
			}
			for _, lateState := range []string{"stop_issued", "terminated", "unavailable"} {
				late, err := receiver.ReportSessionToolStop(ctx, SessionToolStopReport{
					RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID,
					TurnStop: "ended", CaptureState: "complete",
					Tools: []SessionToolStop{{ItemID: "owned-tool", StopState: lateState}},
				}, dest)
				if err != nil {
					t.Fatal(err)
				}
				if late.TurnStopState != "interrupted" || late.CaptureState != "lost" || !late.CaptureGap || late.TerminalScan != "clear" ||
					len(late.Tools) != 1 || late.Tools[0].StopState != terminal || late.Tools[0].StopAt == nil ||
					!late.Tools[0].StopAt.Equal(*stopped.Tools[0].StopAt) {
					t.Fatalf("late %s report changed terminal evidence: %+v", lateState, late)
				}
			}
			reconcile := SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Reason: "cancel_confirmed"}
			closed, err := receiver.ReconcileSessionInbox(ctx, reconcile, dest)
			if err != nil || closed.FinishedAt == nil || closed.Cancel == nil || closed.Cancel.ConfirmedAt == nil {
				t.Fatalf("fresh combined evidence did not confirm cleanup: %+v %v", closed, err)
			}
			if closed.Delivery.DeliveryID != captured.Delivery.DeliveryID || closed.Delivery.Event.EventID != captured.Delivery.Event.EventID ||
				closed.Delivery.Event.Ref != captured.Delivery.Event.Ref || closed.Delivery.Result != nil ||
				closed.Cancel.By != op.channel.Principal || closed.Cancel.Reason != reason ||
				closed.Delivery.Control == nil || closed.Delivery.Control.By != op.channel.Principal || closed.Delivery.Control.Reason != "" {
				t.Fatalf("cleanup changed source/cancel attribution or exposed private reason: %+v", closed)
			}
			var by, persistedReason string
			if err = op.pool.QueryRow(ctx, `SELECT control_by,control_reason FROM cairn.agent_delivery WHERE delivery_id=$1`, closed.Delivery.DeliveryID).Scan(&by, &persistedReason); err != nil {
				t.Fatal(err)
			}
			if by != op.channel.Principal || persistedReason != reason {
				t.Fatalf("cleanup lost operator attribution: %q %q", by, persistedReason)
			}
			replayed, err := receiver.ReconcileSessionInbox(ctx, reconcile, dest)
			if err != nil || replayed.FinishedAt == nil || !replayed.FinishedAt.Equal(*closed.FinishedAt) ||
				replayed.Cancel == nil || replayed.Cancel.ConfirmedAt == nil || !replayed.Cancel.ConfirmedAt.Equal(*closed.Cancel.ConfirmedAt) {
				t.Fatalf("reconciliation replay changed the committed outcome: %+v %v", replayed, err)
			}
		})
	}
}
