package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func TestEventReviewSeparatesHoldExecutionAndHandling(t *testing.T) {
	ctx := context.Background()
	a, b, r, d := eventFixture(t)
	op, _ := testOperator(t)
	event := publishFixture(t, a, b, r, d)
	req := EventReviewRequest{Repo: r.Scope.Repo, Limit: 1}
	page, err := op.ReviewEvents(ctx, req)
	if err != nil || len(page.Deliveries) != 1 || page.Deliveries[0].Execution != "never_launched" || page.Deliveries[0].Held || page.Deliveries[0].TaskOutcome != "unknown" {
		t.Fatalf("pending review: %+v %v", page, err)
	}
	_, err = b.ReviewEvents(ctx, req)
	requireCode(t, err, "AUTHORITY_DENIED")
	wake, err := b.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString()}, d)
	if err != nil {
		t.Fatal(err)
	}
	req.State = "held"
	page, err = op.ReviewEvents(ctx, req)
	if err != nil || len(page.Deliveries) != 1 || !page.Deliveries[0].Held || page.Deliveries[0].LatestWake == nil || page.Deliveries[0].LatestWake.ID != wake.Attempt.ID {
		t.Fatalf("held review: %+v %v", page, err)
	}
	change := func(operation string) {
		t.Helper()
		_, err := b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: wake.Attempt.ID, Operation: operation}, d)
		if err != nil {
			t.Fatal(err)
		}
	}
	change("start")
	change("enter")
	receipt, err := b.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{Repo: r.Scope.Repo, TaskID: "review", RunID: "review"}, Query: "source", Purpose: "context", AvailableTokens: 64000}, Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: wake.Attempt.ID, Operation: "link", ReceiptID: receipt.ReceiptID}, d); err != nil {
		t.Fatal(err)
	}
	if _, err = b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: wake.Attempt.Delivery.DeliveryID, LeaseID: wake.Attempt.Delivery.LeaseID, Disposition: "handled"}, d); err != nil {
		t.Fatal(err)
	}
	page, err = op.ReviewEvents(ctx, req)
	if err != nil || len(page.Deliveries) != 1 || page.Deliveries[0].State != "handled" || page.Deliveries[0].Execution != "may_have_executed" || page.Deliveries[0].TaskOutcome != "unknown" {
		t.Fatalf("handling incorrectly implied acceptance: %+v %v", page, err)
	}
	// A retained assessment is shown separately, with its witness and evidence.
	evidence := testEvidence(t, op, r.Scope.Repo)
	assessmentReq := AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: receipt.ReceiptID, TaskOutcome: "accepted", FailureDomain: "none", Method: "fixture-review", EvidenceIDs: []string{evidence.ID}, Reason: "Explicitly reviewed fixture outcome"}
	if _, err = b.AssessRun(ctx, assessmentReq); err != nil {
		t.Fatal(err)
	}
	page, err = op.ReviewEvents(ctx, req)
	if err != nil || page.Deliveries[0].TaskOutcome != "accepted" || page.Deliveries[0].Assessment == nil || page.Deliveries[0].Assessment.Observer != b.channel.Principal || page.Deliveries[0].LatestWake.ReceiptID != receipt.ReceiptID {
		t.Fatalf("explicit assessment missing: %+v %v", page, err)
	}
	assessmentReq.RequestID = uuid.NewString()
	assessmentReq.ExpectedVersion = 1
	assessmentReq.TaskOutcome = "rejected"
	assessmentReq.FailureDomain = "task"
	if _, err = b.AssessRun(ctx, assessmentReq); err != nil {
		t.Fatal(err)
	}
	page, err = op.ReviewEvents(ctx, req)
	if err != nil || page.Deliveries[0].TaskOutcome != "rejected" || page.Deliveries[0].Assessment.Version != 2 {
		t.Fatalf("review correction lost: %+v %v", page, err)
	}
	change("finish")
	page, err = op.ReviewEvents(ctx, req)
	if err != nil || len(page.Deliveries) != 0 {
		t.Fatalf("finished hold retained: %+v %v", page, err)
	}
	req.State = "handled"
	page, err = op.ReviewEvents(ctx, req)
	if err != nil || len(page.Deliveries) != 1 || page.Deliveries[0].Event.EventID != event.EventID || page.Deliveries[0].Held {
		t.Fatalf("handled review: %+v %v", page, err)
	}
}

func TestEventReviewRetainsPrelaunchHistoryAndPages(t *testing.T) {
	ctx := context.Background()
	a, b, r, d := eventFixture(t)
	op, _ := testOperator(t)
	publishFixture(t, a, b, r, d)
	for range 3 {
		wake, err := b.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString()}, d)
		if err != nil || wake.Attempt == nil {
			t.Fatalf("claim: %+v %v", wake, err)
		}
		finished, err := b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: wake.Attempt.ID, Operation: "finish", Reason: "confirmed_no_launch"}, d)
		if err != nil {
			t.Fatal(err)
		}
		if finished.ProcessState != "prelaunch_failed" {
			t.Fatal("finished attempt lost prelaunch fact")
		}
		if _, err = b.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET available_at=clock_timestamp() WHERE delivery_id=$1`, wake.Attempt.Delivery.DeliveryID); err != nil {
			t.Fatal(err)
		}
	}
	req := EventReviewRequest{Repo: r.Scope.Repo, State: "failed"}
	first, err := op.ReviewEvents(ctx, req)
	if err != nil || len(first.Deliveries) != 1 || first.Deliveries[0].Execution != "never_launched" || first.Deliveries[0].Held {
		t.Fatalf("prelaunch review: %+v %v", first, err)
	}
	// Historical finish rows without a launch report cannot prove nonexecution.
	if _, err = b.pool.Exec(ctx, `UPDATE cairn.agent_wake_attempt SET process_state='' WHERE attempt_id=$1`, first.Deliveries[0].LatestWake.ID); err != nil {
		t.Fatal(err)
	}
	first, err = op.ReviewEvents(ctx, req)
	if err != nil || first.Deliveries[0].Execution != "may_have_executed" {
		t.Fatalf("unknown history overstated: %+v %v", first, err)
	}
	nextEvent := publishFixture(t, a, b, r, d)
	req.State = ""
	req.Limit = 1
	first, err = op.ReviewEvents(ctx, req)
	if err != nil || !first.More {
		t.Fatalf("first page: %+v %v", first, err)
	}
	req.After = first.NextAfter
	second, err := op.ReviewEvents(ctx, req)
	if err != nil || second.More || len(second.Deliveries) != 1 || second.Deliveries[0].Event.EventID != nextEvent.EventID {
		t.Fatalf("next page: %+v %v", second, err)
	}
	req.DeliveryID = first.Deliveries[0].DeliveryID
	req.After = 0
	selected, err := op.ReviewEvents(ctx, req)
	if err != nil || len(selected.Deliveries) != 1 || selected.Deliveries[0].DeliveryID != req.DeliveryID {
		t.Fatalf("exact review: %+v %v", selected, err)
	}
}

func TestEventReviewNativeHoldAndSelectedFailure(t *testing.T) {
	ctx := context.Background()
	a, b, r, d := eventFixture(t)
	op, _ := testOperator(t)
	session, err := b.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "review-native", NativeSessionID: "native", Metadata: AgentMetadata{Harness: "claude", Project: "rhumb", Workspace: "/fixture/rhumb", State: "busy", DeliveryMode: "existing-session"}}, d)
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{AgentID: session.AgentID, ExecutionID: session.ExecutionID}
	event, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{r.RecordID, r.Version}, Destination: EventDestination{Type: "agent", Name: session.Inbox}}, d)
	if err != nil {
		t.Fatal(err)
	}
	claimed, err := b.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}, d)
	if err != nil {
		t.Fatal(err)
	}
	req := EventReviewRequest{Repo: r.Scope.Repo, State: "held"}
	page, err := op.ReviewEvents(ctx, req)
	if err != nil || len(page.Deliveries) != 1 || page.Deliveries[0].LatestNative == nil || page.Deliveries[0].LatestNative.Session == nil || *page.Deliveries[0].LatestNative.Session != ref || page.Deliveries[0].LatestWake != nil || page.Deliveries[0].Execution != "may_have_executed" {
		t.Fatalf("native hold: %+v %v", page, err)
	}
	if _, err = b.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: claimed.Attempt.ID, Reason: "process_exited"}, d); err != nil {
		t.Fatal(err)
	}
	req.State = "failed"
	page, err = op.ReviewEvents(ctx, req)
	if err != nil || len(page.Deliveries) != 1 || page.Deliveries[0].Held || page.Deliveries[0].Event.EventID != event.EventID || page.Deliveries[0].LatestNative.Reason != "process_exited" {
		t.Fatalf("native failure: %+v %v", page, err)
	}
	originalID := page.Deliveries[0].DeliveryID
	reissued, err := op.ReissueEvent(ctx, ReissueEventRequest{RequestID: uuid.NewString(), Repo: r.Scope.Repo, DeliveryID: originalID, Reason: "Operator selected a fresh attempt after reviewing effects", AcceptUncertainEffects: true})
	if err != nil {
		t.Fatal(err)
	}
	req.State = ""
	req.DeliveryID = originalID
	page, err = op.ReviewEvents(ctx, req)
	if err != nil || len(page.Deliveries) != 1 || page.Deliveries[0].ReissuedAs == nil || page.Deliveries[0].ReissuedAs.EventID != reissued.EventID {
		t.Fatalf("old delivery lost reissue trace: %+v %v", page, err)
	}
	req.DeliveryID = ""
	req.State = "pending"
	page, err = op.ReviewEvents(ctx, req)
	if err != nil || len(page.Deliveries) != 1 || page.Deliveries[0].ReissuedFrom == nil || page.Deliveries[0].ReissuedFrom.OriginalDeliveryID != originalID || page.Deliveries[0].Execution != "never_launched" {
		t.Fatalf("new delivery lost origin: %+v %v", page, err)
	}
}

func TestOperatorReviewIncludesUnassignedPoolReissue(t *testing.T) {
	ctx := context.Background()
	op, a, b, r, d, worker := poolFixture(t)
	original := poolPublish(t, a, r, d, PoolRequirements{Workspace: worker.Spec.Workspace})
	queued, err := op.ReviewQueuedEvents(ctx, r.Scope.Repo, 0, 1)
	if err != nil || len(queued.Events) != 1 || queued.Events[0].EventID != original.EventID {
		t.Fatalf("queued pool work hidden: %+v %v", queued, err)
	}
	_, err = a.ReviewQueuedEvents(ctx, r.Scope.Repo, 0, 1)
	requireCode(t, err, "AUTHORITY_DENIED")
	claim := poolClaim(t, b, worker, d)
	delivery := claim.Delivery
	if _, err = b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery.DeliveryID, LeaseID: delivery.LeaseID, Disposition: "failed", Code: "processing_failed"}, d); err != nil {
		t.Fatal(err)
	}
	if _, err = b.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: claim.ID, Operation: "finish", Reason: "confirmed_stopped"}, d); err != nil {
		t.Fatal(err)
	}
	newEvent, err := op.ReissueEvent(ctx, ReissueEventRequest{RequestID: uuid.NewString(), Repo: r.Scope.Repo, DeliveryID: delivery.DeliveryID, Reason: "Retry an assigned pool failure after review", AcceptUncertainEffects: true})
	if err != nil {
		t.Fatal(err)
	}
	queued, err = op.ReviewQueuedEvents(ctx, r.Scope.Repo, queued.NextAfter, 1)
	if err != nil || len(queued.Events) != 1 || queued.Events[0].EventID != newEvent.EventID {
		t.Fatalf("operator cannot find queued successor: %+v %v", queued, err)
	}
}
