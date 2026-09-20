package core

import (
	"context"
	"errors"
	"strings"
	"sync"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgconn"
)

// Operator cancellation of native interactive work: the intent fences
// completion immediately, ownership stays pinned to the exact execution,
// delivery and native turn, and the hold survives until a host report
// confirms the turn stopped and captured tools are terminal.

func nativeCancelFixture(t *testing.T) (*Store, *Store, *Store, AgentSessionRef, string, Destination) {
	t.Helper()
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	op := testStore(t, Channel{Principal: "cancel-operator-" + source.Scope.Repo, Operator: true})
	a, err := receiver.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "native-cancel", NativeSessionID: "native-cancel", Metadata: AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{a.AgentID, a.ExecutionID}
	event, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", a.Inbox}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	claim, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref, DeliveryID: eventDeliveries(t, sender, event.EventID)[0], NativeTurnID: "turn-one", TurnExclusive: true}, dest)
	if err != nil || claim.Attempt == nil {
		t.Fatalf("claim: %+v %v", claim, err)
	}
	return op, receiver, sender, ref, claim.Attempt.ID, dest
}

func nativeCancelFixtureNoExclusive(t *testing.T) (*Store, *Store, *Store, AgentSessionRef, string, Destination) {
	t.Helper()
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	op := testStore(t, Channel{Principal: "cancel-operator-" + source.Scope.Repo, Operator: true})
	a, err := receiver.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "native-cancel-shared", NativeSessionID: "native-cancel-shared", Metadata: AgentMetadata{Harness: "claude", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{a.AgentID, a.ExecutionID}
	event, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", a.Inbox}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	// A plain turn-less boundary claim, as the hook reports at a supported
	// boundary without a wake binding: no prompt id is pinned at all.
	claim, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}, dest)
	if err != nil || claim.Attempt == nil {
		t.Fatalf("plain claim: %+v %v", claim, err)
	}
	_ = event
	return op, receiver, sender, ref, claim.Attempt.ID, dest
}

func eventDeliveries(t *testing.T, s *Store, eventID string) []string {
	t.Helper()
	status, err := s.AgentEventStatus(context.Background(), EventStatusRequest{EventID: eventID}, Destination{"hosted", false})
	if err != nil || len(status.Deliveries) != 1 {
		t.Fatalf("status: %+v %v", status, err)
	}
	return []string{status.Deliveries[0].DeliveryID}
}

func TestNativeCancelFencesCompletionUntilCleanupConfirmed(t *testing.T) {
	ctx := context.Background()
	op, receiver, sender, ref, attemptID, dest := nativeCancelFixture(t)
	view, err := receiver.ForAgentSession(ref, dest)
	if err != nil {
		t.Fatal(err)
	}
	delivery := attemptDelivery(t, receiver, ref, attemptID, dest)
	lease := attemptLease(t, receiver, ref, attemptID, dest)

	attempt, err := receiver.CaptureSessionTools(ctx, SessionToolCapture{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Items: []SessionToolItem{
		{ItemID: "exec-one", ProcessID: "701", Command: "/bin/sleep 120", NativeTurnID: "turn-one"},
		{ItemID: "exec-two", ProcessID: "702", Command: "/bin/sleep 180", NativeTurnID: "turn-one"},
	}}, dest)
	if err != nil || len(attempt.Tools) != 2 {
		t.Fatalf("capture: %+v %v", attempt, err)
	}

	cancel, err := op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: attempt.Delivery.Event.Repo, DeliveryID: delivery, Reason: "Operator stops this interactive request for the cancellation test"})
	if err != nil || cancel.Held != true || cancel.Native == nil || cancel.Native.State != "cancel_pending" || cancel.Native.NativeTurnID != "turn-one" || cancel.Control != nil {
		t.Fatalf("cancel: %+v %v", cancel, err)
	}
	again, err := op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: attempt.Delivery.Event.Repo, DeliveryID: delivery, Reason: "Second operator decision observes the same pending cancellation"})
	if err != nil || again.Native == nil || !again.Native.RequestedAt.Equal(cancel.Native.RequestedAt) {
		t.Fatalf("repeat cancel drifted: %+v %v", again, err)
	}

	_, err = view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery, LeaseID: lease, Disposition: "handled"}, dest)
	requireCode(t, err, "REQUEST_CANCELLED")
	_, err = view.ChangeEventLease(ctx, EventLeaseRequest{DeliveryID: delivery, LeaseID: lease, LeaseSeconds: 90}, true, dest)
	requireCode(t, err, "REQUEST_CANCELLED")
	_, err = view.ChangeEventLease(ctx, EventLeaseRequest{DeliveryID: delivery, LeaseID: lease}, false, dest)
	requireCode(t, err, "REQUEST_CANCELLED")

	// The delivery stays leased under its holder while cleanup is unconfirmed.
	if d := attemptDeliveryState(t, receiver, ref, attemptID, dest); d != "leased" {
		t.Fatalf("cancel pending changed delivery state: %s", d)
	}
	_, err = receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Reason: "turn_ended"}, dest)
	requireCode(t, err, "CLEANUP_UNCONFIRMED")
	_, err = receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Reason: "cancel_confirmed"}, dest)
	requireCode(t, err, "CLEANUP_UNCONFIRMED")

	// Ambiguity and unknown terminal scans hold even with terminal tools.
	if _, err = receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, TurnStop: "ambiguous", TerminalScan: "unknown_remaining", Tools: []SessionToolStop{
		{ItemID: "exec-one", StopState: "terminated"},
	}}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err = receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Reason: "cancel_confirmed"}, dest); err == nil {
		t.Fatal("ambiguous transport released the hold")
	} else if Code(err) != "CLEANUP_UNCONFIRMED" {
		t.Fatalf("ambiguous transport: %v", err)
	}
	stopped, err := receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, TurnStop: "interrupted", TerminalScan: "clear", Tools: []SessionToolStop{
		{ItemID: "exec-one", StopState: "terminated"},
		{ItemID: "exec-two", StopState: "unavailable"},
	}}, dest)
	if err != nil || stopped.TurnStopState != "interrupted" || len(stopped.Tools) != 2 {
		t.Fatalf("stop report: %+v %v", stopped, err)
	}
	confirmed, err := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Reason: "cancel_confirmed"}, dest)
	if err != nil || confirmed.FinishedAt == nil || confirmed.Reason != "cancel_confirmed" || confirmed.Cancel == nil || confirmed.Cancel.ConfirmedAt == nil {
		t.Fatalf("confirm: %+v %v", confirmed, err)
	}
	if confirmed.Delivery.State != "failed" || confirmed.Delivery.Code != "operator_cancelled" || confirmed.Delivery.Control == nil || confirmed.Delivery.Control.Code != "operator_cancelled" {
		t.Fatalf("controlled delivery: %+v", confirmed.Delivery)
	}

	// Late completion of cancelled work cannot land through any lease.
	_, err = view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery, LeaseID: lease, Disposition: "handled"}, dest)
	requireCode(t, err, "STALE_LEASE")

	// Hold released: the inbox can accept a fresh delivery.
	next, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{confirmed.Delivery.Event.Ref.RecordID, confirmed.Delivery.Event.Ref.Version}, Destination: EventDestination{"agent", "agent/" + ref.AgentID}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	if next.EventID == "" {
		t.Fatal("republication failed")
	}
}

func TestNativeCancelRefusesForeignTurnAndExecution(t *testing.T) {
	ctx := context.Background()
	op, receiver, _, ref, attemptID, dest := nativeCancelFixture(t)
	delivery := attemptDelivery(t, receiver, ref, attemptID, dest)

	_, err := receiver.CaptureSessionTools(ctx, SessionToolCapture{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Items: []SessionToolItem{
		{ItemID: "exec-later", ProcessID: "703", Command: "/bin/sleep 60", NativeTurnID: "turn-two"},
	}}, dest)
	requireCode(t, err, "INVALID_REQUEST")

	foreign := ref
	foreign.ExecutionID = uuid.NewString()
	_, err = receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: foreign, AttemptID: attemptID, TurnStop: "interrupted"}, dest)
	requireCode(t, err, "NOT_FOUND")
	_, err = receiver.CaptureSessionTools(ctx, SessionToolCapture{RequestID: uuid.NewString(), Session: foreign, AttemptID: attemptID, Items: []SessionToolItem{{ItemID: "exec-one", ProcessID: "701", NativeTurnID: "turn-one"}}}, dest)
	requireCode(t, err, "NOT_FOUND")

	// A stale watcher incarnation cannot observe the inbox.
	_, err = receiver.SessionInboxControl(ctx, foreign, dest)
	requireCode(t, err, "STALE_SESSION")

	// Turn stop state never moves backward or sideways once recorded.
	if _, err = op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: attemptRepo(t, receiver, ref, attemptID, dest), DeliveryID: delivery, Reason: "Cancel for the ownership refusal test"}); err != nil {
		t.Fatal(err)
	}
	if _, err = receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, TurnStop: "interrupted"}, dest); err != nil {
		t.Fatal(err)
	}
	drifted, err := receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, TurnStop: "ended"}, dest)
	if err != nil || drifted.TurnStopState != "interrupted" {
		t.Fatalf("turn stop drifted: %+v %v", drifted, err)
	}
	_, err = receiver.CaptureSessionTools(ctx, SessionToolCapture{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Items: []SessionToolItem{{ItemID: "exec-one", ProcessID: "701", NativeTurnID: "turn-one"}}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	_, err = receiver.CaptureSessionTools(ctx, SessionToolCapture{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Items: []SessionToolItem{{ItemID: "exec-one", ProcessID: "999", NativeTurnID: "turn-one"}}}, dest)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
}

func TestNativeCancelAfterCompletionRefusedAndRecoverableAfterRestart(t *testing.T) {
	ctx := context.Background()
	_, receiver, sender, ref, attemptID, dest := nativeCancelFixture(t)
	view, err := receiver.ForAgentSession(ref, dest)
	if err != nil {
		t.Fatal(err)
	}
	delivery := attemptDelivery(t, receiver, ref, attemptID, dest)
	lease := attemptLease(t, receiver, ref, attemptID, dest)

	if _, err := view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery, LeaseID: lease, Disposition: "handled"}, dest); err != nil {
		t.Fatal(err)
	}
	op := testStore(t, Channel{Principal: "cancel-operator-late-" + ref.AgentID, Operator: true})
	_, err = op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: attemptRepo(t, receiver, ref, attemptID, dest), DeliveryID: delivery, Reason: "Late operator decision after completion"})
	requireCode(t, err, "VERSION_CONFLICT")

	// Restart recovery: a fresh watcher incarnation observes the pending
	// cancellation through the control read and finishes the durable cleanup.
	op2, receiver2, _, ref2, attempt2, _ := nativeCancelFixture(t)
	delivery2 := attemptDelivery(t, receiver2, ref2, attempt2, dest)
	if _, err = op2.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: attemptRepo(t, receiver2, ref2, attempt2, dest), DeliveryID: delivery2, Reason: "Cancel before a simulated adapter restart"}); err != nil {
		t.Fatal(err)
	}
	// The lease expires; the durable hold still pins the work.
	if _, err = receiver2.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET lease_until=clock_timestamp()-interval '1 second' WHERE delivery_id=$1`, delivery2); err != nil {
		t.Fatal(err)
	}
	control, err := receiver2.SessionInboxControl(ctx, ref2, dest)
	if err != nil || control.Attempt == nil || control.Attempt.Cancel == nil || control.Attempt.Cancel.ConfirmedAt != nil {
		t.Fatalf("control after expiry: %+v %v", control, err)
	}
	if _, err = receiver2.CaptureSessionTools(ctx, SessionToolCapture{RequestID: uuid.NewString(), Session: ref2, AttemptID: attempt2, Items: []SessionToolItem{{ItemID: "exec-restart", ProcessID: "704", Command: "/bin/sleep 90", NativeTurnID: control.Attempt.NativeTurnID}}}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err = receiver2.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref2, AttemptID: attempt2, TurnStop: "ended", Tools: []SessionToolStop{{ItemID: "exec-restart", StopState: "terminated"}}}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err = receiver2.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref2, AttemptID: attempt2, Reason: "cancel_confirmed"}, dest); err == nil || Code(err) != "CLEANUP_UNCONFIRMED" {
		t.Fatalf("restart confirm without coverage evidence: %v", err)
	}
	if _, err = receiver2.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref2, AttemptID: attempt2, TerminalScan: "clear"}, dest); err != nil {
		t.Fatal(err)
	}
	confirmed, err := receiver2.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref2, AttemptID: attempt2, Reason: "cancel_confirmed"}, dest)
	if err != nil || confirmed.Delivery.Code != "operator_cancelled" || confirmed.FinishedAt == nil {
		t.Fatalf("restart confirm: %+v %v", confirmed, err)
	}
	_ = sender
}

func TestNativeCancelRacesCompletion(t *testing.T) {
	ctx := context.Background()
	for range 8 {
		op, receiver, _, ref, attemptID, dest := nativeCancelFixture(t)
		view, err := receiver.ForAgentSession(ref, dest)
		if err != nil {
			t.Fatal(err)
		}
		delivery := attemptDelivery(t, receiver, ref, attemptID, dest)
		lease := attemptLease(t, receiver, ref, attemptID, dest)
		repo := attemptRepo(t, receiver, ref, attemptID, dest)

		var cancelErr, completeErr error
		var wg sync.WaitGroup
		wg.Add(2)
		go func() {
			defer wg.Done()
			_, cancelErr = op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: repo, DeliveryID: delivery, Reason: "Concurrent operator cancellation racing completion"})
		}()
		go func() {
			defer wg.Done()
			_, completeErr = view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery, LeaseID: lease, Disposition: "handled"}, dest)
		}()
		wg.Wait()
		if cancelErr == nil && completeErr == nil {
			t.Fatal("cancel and completion both succeeded")
		}
		if completeErr == nil {
			// Completion won the race; cancellation must observe terminal work.
			requireCode(t, cancelErr, "VERSION_CONFLICT")
			released, err := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Reason: "turn_ended"}, dest)
			if err != nil || released.FinishedAt == nil || released.Delivery.State != "handled" {
				t.Fatalf("post-completion reconcile: %+v %v", released, err)
			}
			continue
		}
		if cancelErr != nil {
			t.Fatalf("completion failed while cancellation also failed: %v / %v", cancelErr, completeErr)
		}
		switch Code(completeErr) {
		case "REQUEST_CANCELLED":
			// Cancellation won; the hold survives until confirmed cleanup.
		case "STALE_LEASE":
			// Completion lost the race against reconcile-style fencing; also safe.
			if _, err = receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, TurnStop: "ended", TerminalScan: "clear"}, dest); err != nil {
				t.Fatal(err)
			}
		default:
			t.Fatalf("unexpected completion outcome: %v", completeErr)
		}
		if _, err = receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, TurnStop: "interrupted", TerminalScan: "clear"}, dest); err != nil {
			t.Fatal(err)
		}
		confirmed, err := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Reason: "cancel_confirmed"}, dest)
		if err != nil || confirmed.FinishedAt == nil || confirmed.Delivery.Code != "operator_cancelled" {
			t.Fatalf("confirm after race: %+v %v", confirmed, err)
		}
	}
}

func TestSharedPromptCannotOwnTwoRequests(t *testing.T) {
	ctx := context.Background()
	_, receiver, sender, ref, attemptID, dest := nativeCancelFixture(t)
	repo := attemptRepo(t, receiver, ref, attemptID, dest)
	source := sourceRef(t, receiver, ref, attemptID, dest)

	// The same native prompt id must never own two unfinished attempts, even
	// across conversations: a joined Claude channel prompt is not per-request
	// identity. The partial unique index enforces the invariant defensively.
	foreignAgent, foreignExecution := uuid.NewString(), uuid.NewString()
	event, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", "agent/" + foreignAgent}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = receiver.pool.Exec(ctx, `INSERT INTO cairn.agent_session(agent_id,repo,owner,binding,native_session_id,visibility,execution_id,database_generation,metadata) VALUES($1,$2,$3,'shared-prompt',$4,'hosted',$5,1,'{}'::jsonb)`,
		foreignAgent, repo, receiver.channel.Principal, uuid.NewString(), foreignExecution); err != nil {
		t.Fatal(err)
	}
	_, err = receiver.pool.Exec(ctx, `INSERT INTO cairn.agent_session_attempt(attempt_id,agent_id,execution_id,repo,owner,delivery_id,lease_id,native_turn_id,turn_exclusive) VALUES($1,$2,$3,$4,$5,$6,$7,'turn-one',true)`,
		uuid.NewString(), foreignAgent, foreignExecution, repo, receiver.channel.Principal, eventDeliveries(t, sender, event.EventID)[0], uuid.NewString())
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) || pgErr.Code != "23505" || !errContains(err, "agent_session_one_exclusive_turn") {
		t.Fatalf("shared prompt owned two requests: %v", err)
	}
}

func TestExclusiveBindingRequiresPinnedTurn(t *testing.T) {
	_, receiver, _, ref, _, dest := nativeCancelFixture(t)
	_, err := receiver.ClaimSessionInbox(context.Background(), SessionInboxClaim{RequestID: uuid.NewString(), Session: ref, TurnExclusive: true}, dest)
	requireCode(t, err, "INVALID_REQUEST")
}

// Pinned-but-unproven turns keep their 048 semantics: the binding stays
// valid for delivery identity, but the attempt never qualifies for
// cancellation because it attests no exclusive request ownership.
func TestPinnedUnprovenTurnClaimsButNeverCancels(t *testing.T) {
	ctx := context.Background()
	op, receiver, sender, ref, attemptID, dest := nativeCancelFixturePinnedUnproven(t)
	delivery := attemptDelivery(t, receiver, ref, attemptID, dest)
	_, err := op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: attemptRepo(t, receiver, ref, attemptID, dest), DeliveryID: delivery, Reason: "Unproven pinned turn must refuse"})
	requireCode(t, err, "UNSUPPORTED_CONTROL")
	view, err := receiver.ForAgentSession(ref, dest)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery, LeaseID: attemptLease(t, receiver, ref, attemptID, dest), Disposition: "handled"}, dest); err != nil {
		t.Fatalf("unproven pinned completion must remain possible: %v", err)
	}
	_ = sender
}

// A stop report progresses captured -> stop_issued -> terminal without a
// timing violation, and an intermediate stop_issued tool holds cleanup.
func TestStopIssuedProgressHoldsCleanup(t *testing.T) {
	ctx := context.Background()
	_, receiver, _, ref, attemptID, dest := nativeCancelFixture(t)
	delivery := attemptDelivery(t, receiver, ref, attemptID, dest)
	op := testStore(t, Channel{Principal: "cancel-operator-progress-" + ref.AgentID, Operator: true})
	if _, err := receiver.CaptureSessionTools(ctx, SessionToolCapture{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Items: []SessionToolItem{{ItemID: "exec-progress", ProcessID: "808", NativeTurnID: "turn-one"}}}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err := op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: attemptRepo(t, receiver, ref, attemptID, dest), DeliveryID: delivery, Reason: "Progress-state cancellation probe"}); err != nil {
		t.Fatal(err)
	}
	progress, err := receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, TurnStop: "interrupted", Tools: []SessionToolStop{{ItemID: "exec-progress", StopState: "stop_issued"}}}, dest)
	if err != nil || progress.Tools[0].StopState != "stop_issued" || progress.Tools[0].StopAt != nil {
		t.Fatalf("stop_issued progress: %+v %v", progress, err)
	}
	_, err = receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Reason: "cancel_confirmed"}, dest)
	requireCode(t, err, "CLEANUP_UNCONFIRMED")
	done, err := receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, TerminalScan: "clear", Tools: []SessionToolStop{{ItemID: "exec-progress", StopState: "terminated"}}}, dest)
	if err != nil || done.Tools[0].StopState != "terminated" || done.Tools[0].StopAt == nil {
		t.Fatalf("terminal transition: %+v %v", done, err)
	}
	confirmed, err := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Reason: "cancel_confirmed"}, dest)
	if err != nil || confirmed.FinishedAt == nil {
		t.Fatalf("confirm after terminal progress: %+v %v", confirmed, err)
	}
}

// A latched coverage gap survives a later complete capture: only the final
// clear scan proves cleanup.
func TestCaptureGapSurvivesCompleteReport(t *testing.T) {
	ctx := context.Background()
	_, receiver, _, ref, attemptID, dest := nativeCancelFixture(t)
	if _, err := receiver.CaptureSessionTools(ctx, SessionToolCapture{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Items: []SessionToolItem{{ItemID: "exec-gap", ProcessID: "909", NativeTurnID: "turn-one"}}}, dest); err != nil {
		t.Fatal(err)
	}
	gapped, err := receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, CaptureState: "lost"}, dest)
	if err != nil || !gapped.CaptureGap {
		t.Fatalf("gap not latched: %+v %v", gapped, err)
	}
	resumed, err := receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, CaptureState: "attached"}, dest)
	if err != nil || !resumed.CaptureGap {
		t.Fatalf("gap erased by resubscribe: %+v %v", resumed, err)
	}
	completed, err := receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, CaptureState: "complete"}, dest)
	if err != nil || !completed.CaptureGap {
		t.Fatalf("gap erased by complete: %+v %v", completed, err)
	}
}

func TestNonExclusiveTurnStaysUnsupportedForCancellation(t *testing.T) {
	ctx := context.Background()
	_, receiver, _, ref, attemptID, dest := nativeCancelFixtureNoExclusive(t)
	delivery := attemptDelivery(t, receiver, ref, attemptID, dest)
	op := testStore(t, Channel{Principal: "cancel-operator-shared-" + ref.AgentID, Operator: true})
	_, err := op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: attemptRepo(t, receiver, ref, attemptID, dest), DeliveryID: delivery, Reason: "Shared prompt cancellation must refuse"})
	requireCode(t, err, "UNSUPPORTED_CONTROL")
	// Completion still works for the non-exclusive attempt: nothing was fenced.
	view, err := receiver.ForAgentSession(ref, dest)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery, LeaseID: attemptLease(t, receiver, ref, attemptID, dest), Disposition: "handled"}, dest); err != nil {
		t.Fatalf("non-exclusive completion must remain possible: %v", err)
	}
}

func nativeCancelFixturePinnedUnproven(t *testing.T) (*Store, *Store, *Store, AgentSessionRef, string, Destination) {
	t.Helper()
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	op := testStore(t, Channel{Principal: "cancel-operator-unproven-" + source.Scope.Repo, Operator: true})
	a, err := receiver.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "native-unproven", NativeSessionID: "native-unproven", Metadata: AgentMetadata{Harness: "claude", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{a.AgentID, a.ExecutionID}
	event, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", a.Inbox}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	claim, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref, DeliveryID: eventDeliveries(t, sender, event.EventID)[0], NativeTurnID: "prompt-shared"}, dest)
	if err != nil || claim.Attempt == nil {
		t.Fatalf("pinned unproven claim: %+v %v", claim, err)
	}
	return op, receiver, sender, ref, claim.Attempt.ID, dest
}

func sourceRef(t *testing.T, s *Store, ref AgentSessionRef, attemptID string, dest Destination) RecordVersionRef {
	t.Helper()
	attempt, err := s.readSessionInboxAttemptForTest(context.Background(), ref, attemptID, dest)
	if err != nil {
		t.Fatal(err)
	}
	return attempt.Delivery.Event.Ref
}

func attemptDelivery(t *testing.T, s *Store, ref AgentSessionRef, attemptID string, dest Destination) string {
	t.Helper()
	attempt, err := s.readSessionInboxAttemptForTest(context.Background(), ref, attemptID, dest)
	if err != nil {
		t.Fatal(err)
	}
	return attempt.Delivery.DeliveryID
}
func attemptLease(t *testing.T, s *Store, ref AgentSessionRef, attemptID string, dest Destination) string {
	t.Helper()
	attempt, err := s.readSessionInboxAttemptForTest(context.Background(), ref, attemptID, dest)
	if err != nil {
		t.Fatal(err)
	}
	return attempt.Delivery.LeaseID
}
func attemptDeliveryState(t *testing.T, s *Store, ref AgentSessionRef, attemptID string, dest Destination) string {
	t.Helper()
	attempt, err := s.readSessionInboxAttemptForTest(context.Background(), ref, attemptID, dest)
	if err != nil {
		t.Fatal(err)
	}
	return attempt.Delivery.State
}
func attemptRepo(t *testing.T, s *Store, ref AgentSessionRef, attemptID string, dest Destination) string {
	t.Helper()
	attempt, err := s.readSessionInboxAttemptForTest(context.Background(), ref, attemptID, dest)
	if err != nil {
		t.Fatal(err)
	}
	return attempt.Delivery.Event.Repo
}

// readSessionInboxAttemptForTest opens a plain read transaction for inspection.
func (s *Store) readSessionInboxAttemptForTest(ctx context.Context, ref AgentSessionRef, attemptID string, dest Destination) (SessionInboxAttempt, error) {
	tx, err := s.begin(ctx)
	if err != nil {
		return SessionInboxAttempt{}, err
	}
	defer tx.Rollback(ctx)
	return s.readSessionInboxAttempt(ctx, tx, attemptID, ref, dest)
}

func errContains(err error, needle string) bool {
	return err != nil && strings.Contains(err.Error(), needle)
}

// Older clear-scan evidence must never satisfy final cleanup after new tool
// capture, capture loss, or the cancellation decision itself.
func TestStaleClearScanCannotReleaseHold(t *testing.T) {
	ctx := context.Background()
	_, receiver, _, ref, attemptID, dest := nativeCancelFixture(t)
	delivery := attemptDelivery(t, receiver, ref, attemptID, dest)
	op := testStore(t, Channel{Principal: "cancel-operator-stale-" + ref.AgentID, Operator: true})
	if _, err := receiver.CaptureSessionTools(ctx, SessionToolCapture{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Items: []SessionToolItem{{ItemID: "exec-stale", ProcessID: "606", NativeTurnID: "turn-one"}}}, dest); err != nil {
		t.Fatal(err)
	}
	// A clear scan recorded BEFORE the operator decision.
	if _, err := receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, TerminalScan: "clear"}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err := op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: attemptRepo(t, receiver, ref, attemptID, dest), DeliveryID: delivery, Reason: "Stale evidence probe"}); err != nil {
		t.Fatal(err)
	}
	stopped, err := receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, TurnStop: "interrupted", Tools: []SessionToolStop{{ItemID: "exec-stale", StopState: "terminated"}}}, dest)
	if err != nil || stopped.TerminalScan != "" {
		t.Fatalf("pre-cancel clear scan survived the operator decision: %+v %v", stopped, err)
	}
	if _, err = receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Reason: "cancel_confirmed"}, dest); err == nil || Code(err) != "CLEANUP_UNCONFIRMED" {
		t.Fatalf("stale scan released the hold: %v", err)
	}
	// A fresh clear scan, then a NEW captured tool invalidates it again.
	if _, err = receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, TerminalScan: "clear"}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err = receiver.CaptureSessionTools(ctx, SessionToolCapture{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Items: []SessionToolItem{{ItemID: "exec-late", ProcessID: "707", NativeTurnID: "turn-one"}}}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err = receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Tools: []SessionToolStop{{ItemID: "exec-late", StopState: "terminated"}}}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err = receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Reason: "cancel_confirmed"}, dest); err == nil || Code(err) != "CLEANUP_UNCONFIRMED" {
		t.Fatalf("scan older than a new capture released the hold: %v", err)
	}
	// A real attached capture-loss transition invalidates a clear scan:
	// while detached, unseen tools may have started.
	if _, err = receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, CaptureState: "attached"}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err = receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, TerminalScan: "clear"}, dest); err != nil {
		t.Fatal(err)
	}
	rescanned, err := receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, CaptureState: "lost"}, dest)
	if err != nil || rescanned.TerminalScan != "" {
		t.Fatalf("clear scan survived capture loss: %+v %v", rescanned, err)
	}
	if _, err = receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Reason: "cancel_confirmed"}, dest); err == nil || Code(err) != "CLEANUP_UNCONFIRMED" {
		t.Fatalf("scan older than capture loss released the hold: %v", err)
	}
	// Only a scan that postdates everything releases.
	if _, err = receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, TerminalScan: "clear"}, dest); err != nil {
		t.Fatal(err)
	}
	confirmed, err := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Reason: "cancel_confirmed"}, dest)
	if err != nil || confirmed.FinishedAt == nil {
		t.Fatalf("fresh evidence must confirm: %+v %v", confirmed, err)
	}
}

// A host-observed owner join revokes exclusivity: confirmation is refused and
// the honest terminal path records the revocation instead of a cleanup claim.
func TestOwnerJoinRevokesExclusivity(t *testing.T) {
	ctx := context.Background()
	_, receiver, _, ref, attemptID, dest := nativeCancelFixture(t)
	delivery := attemptDelivery(t, receiver, ref, attemptID, dest)
	op := testStore(t, Channel{Principal: "cancel-operator-join-" + ref.AgentID, Operator: true})
	if _, err := op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: attemptRepo(t, receiver, ref, attemptID, dest), DeliveryID: delivery, Reason: "Owner-join probe"}); err != nil {
		t.Fatal(err)
	}
	if _, err := receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, TerminalScan: "clear", TurnStop: "interrupted"}, dest); err != nil {
		t.Fatal(err)
	}
	revoked, err := receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, OwnerJoin: true}, dest)
	if err != nil || revoked.TurnExclusive {
		t.Fatalf("owner join did not revoke exclusivity: %+v %v", revoked, err)
	}
	if _, err = receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Reason: "cancel_confirmed"}, dest); err == nil || Code(err) != "CLEANUP_UNCONFIRMED" {
		t.Fatalf("revoked exclusivity confirmed cleanup: %v", err)
	}
	closed, err := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: attemptID, Reason: "exclusivity_revoked"}, dest)
	if err != nil || closed.FinishedAt == nil || closed.Reason != "exclusivity_revoked" {
		t.Fatalf("revocation terminal path: %+v %v", closed, err)
	}
	if closed.Delivery.Code != "operator_cancelled" || closed.Delivery.Control == nil {
		t.Fatalf("revoked delivery outcome: %+v", closed.Delivery)
	}
	// A later work-cancel on the revoked attempt refuses again.
	if _, err = op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: attemptRepo(t, receiver, ref, attemptID, dest), DeliveryID: delivery, Reason: "post-revocation refusal"}); err == nil || Code(err) != "VERSION_CONFLICT" {
		t.Fatalf("terminal delivery accepted another cancel: %v", err)
	}
}
