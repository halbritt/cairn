package core

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/google/uuid"
)

type successorFixture struct {
	op            *Store
	senderProfile *Store
	senderView    *Store
	senderSession AgentInstance
	predProfile   *Store
	predView      *Store
	predSession   AgentInstance
	succProfile   *Store
	succView      *Store
	succSession   AgentInstance
	source        Record
	reqEvent      AgentEvent
	deliveryID    string
	attemptID     string
	handoff       Record
	repo          string
	dest          Destination
}

// setupSuccessorTakeoverFixture builds the observed takeover scenario:
// sender (agent-469) sends a request to predecessor (agent-464), whose native
// turn ends and reconciles the delivery as failed; predecessor leaves a
// shareable handoff note; successor (agent-470) registers to resume oversight.
func setupSuccessorTakeoverFixture(t *testing.T) successorFixture {
	t.Helper()
	ctx := context.Background()
	senderProfile, predProfile, source, dest := eventFixture(t)
	repo := source.Scope.Repo
	succProfile := testStore(t, Channel{Principal: "succ-owner-" + repo, Repo: repo})
	op := testStore(t, Channel{Principal: "operator-" + repo, Operator: true})

	senderSession, err := senderProfile.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID: uuid.NewString(), Binding: "codex-sender", NativeSessionID: "thread-469",
		Metadata: AgentMetadata{Harness: "codex", Project: "cairn", Workspace: "/work/cairn", TaskSummary: "sender agent-469", State: "busy", DeliveryMode: "existing-session"},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	senderView, err := senderProfile.ForAgentSession(AgentSessionRef{AgentID: senderSession.AgentID, ExecutionID: senderSession.ExecutionID}, dest)
	if err != nil {
		t.Fatal(err)
	}

	predSession, err := predProfile.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID: uuid.NewString(), Binding: "claude-oversight", NativeSessionID: "01a150a3-pred",
		Metadata: AgentMetadata{Harness: "claude", Project: "cairn", Workspace: "/work/cairn", TaskSummary: "predecessor agent-464", State: "idle", DeliveryMode: "existing-session"},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	predRef := AgentSessionRef{AgentID: predSession.AgentID, ExecutionID: predSession.ExecutionID}
	predView, err := predProfile.ForAgentSession(predRef, dest)
	if err != nil {
		t.Fatal(err)
	}

	reqEvent, err := senderView.PublishEvent(ctx, PublishEventRequest{
		RequestID:     uuid.NewString(),
		Kind:          "request",
		Ref:           RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination:   EventDestination{Type: "agent", Name: predSession.Inbox},
		ResponseGroup: &ResponseGroupSpec{Deadline: time.Now().Add(time.Hour), PartialPolicy: "all"},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}

	claim, err := predProfile.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: predRef}, dest)
	if err != nil || claim.Attempt == nil {
		t.Fatalf("claim predecessor inbox: %+v %v", claim, err)
	}
	reconciled, err := predProfile.ReconcileSessionInbox(ctx, SessionInboxReconcile{
		RequestID: uuid.NewString(), Session: predRef, AttemptID: claim.Attempt.ID, Reason: "turn_ended",
	}, dest)
	if err != nil || reconciled.Delivery.State != "failed" {
		t.Fatalf("reconcile turn_ended: %+v %v", reconciled, err)
	}
	if _, err = predProfile.LeaveAgent(ctx, predRef, dest); err != nil {
		t.Fatal(err)
	}
	return finishSuccessorTakeoverFixture(t, op, senderProfile, senderView, senderSession, predProfile, predView, predSession, succProfile, source, reqEvent, reconciled.Delivery.DeliveryID, claim.Attempt.ID, repo, dest)
}

func finishSuccessorTakeoverFixture(
	t *testing.T,
	op, senderProfile, senderView *Store, senderSession AgentInstance,
	predProfile, predView *Store, predSession AgentInstance,
	succProfile *Store, source Record, reqEvent AgentEvent,
	deliveryID, attemptID, repo string, dest Destination,
) successorFixture {
	t.Helper()
	ctx := context.Background()
	handoffDraft := projectNote(repo)
	handoffDraft.Sensitivity = "shareable"
	handoffDraft.Body = fmt.Sprintf("Handoff from %s (session 01a150a3): pending request %s from %s", predSession.DisplayName, reqEvent.EventID, senderSession.DisplayName)
	handoff, err := predProfile.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: handoffDraft})
	if err != nil {
		t.Fatal(err)
	}

	succSession, err := succProfile.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID: uuid.NewString(), Binding: "claude-oversight", NativeSessionID: "01a150a3-succ",
		Metadata: AgentMetadata{Harness: "claude", Project: "cairn", Workspace: "/work/cairn", TaskSummary: "resumed oversight 01a150a3 as successor to " + predSession.AgentID, State: "busy", DeliveryMode: "existing-session"},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	succView, err := succProfile.ForAgentSession(AgentSessionRef{AgentID: succSession.AgentID, ExecutionID: succSession.ExecutionID}, dest)
	if err != nil {
		t.Fatal(err)
	}

	return successorFixture{
		op: op, senderProfile: senderProfile, senderView: senderView, senderSession: senderSession,
		predProfile: predProfile, predView: predView, predSession: predSession,
		succProfile: succProfile, succView: succView, succSession: succSession,
		source: source, reqEvent: reqEvent, deliveryID: deliveryID, attemptID: attemptID,
		handoff: handoff, repo: repo, dest: dest,
	}
}

func TestSuccessorDefaultBoundaryAndEndToEndTakeover(t *testing.T) {
	ctx := context.Background()
	f := setupSuccessorTakeoverFixture(t)

	// 1. Successor can read the shared handoff and source note, but default boundary
	// refuses event inspection and causal-parent follow-up publication with NOT_FOUND.
	hist, err := f.succProfile.History(ctx, RecordHistoryRequest{RecordID: f.handoff.RecordID, Version: 1}, f.dest)
	if err != nil || len(hist.Versions) != 1 || hist.Versions[0].Body == nil {
		t.Fatalf("handoff not readable: %+v %v", hist, err)
	}
	_, err = f.succView.AgentEventStatus(ctx, EventStatusRequest{EventID: f.reqEvent.EventID}, f.dest)
	requireCode(t, err, "NOT_FOUND")

	replyDraft := projectNote(f.repo)
	replyDraft.Sensitivity = "shareable"
	replyDraft.Body = "Continuity follow-up from successor agent-470"
	replyRec, err := f.succProfile.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: replyDraft})
	if err != nil {
		t.Fatal(err)
	}
	_, err = f.succView.PublishEvent(ctx, PublishEventRequest{
		RequestID:     uuid.NewString(),
		Kind:          "notice",
		Ref:           RecordVersionRef{RecordID: replyRec.RecordID, Version: replyRec.Version},
		Destination:   EventDestination{Type: "agent", Name: f.senderSession.Inbox},
		CausationID:   f.reqEvent.EventID,
		CorrelationID: f.reqEvent.CorrelationID,
	}, f.dest)
	requireCode(t, err, "NOT_FOUND")

	verifyAuthorizedTakeover(t, f, replyRec)
}

func verifyAuthorizedTakeover(t *testing.T, f successorFixture, replyRec Record) {
	t.Helper()
	ctx := context.Background()

	// Unselected event in the same repo stays NOT_FOUND even after authorization.
	otherEvent, err := f.senderView.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Kind:        "notice",
		Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{Type: "agent", Name: f.predSession.Inbox},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}

	grantReq := AuthorizeSessionSuccessorRequest{
		RequestID:          uuid.NewString(),
		Repo:               f.repo,
		PredecessorAgentID: f.predSession.AgentID,
		Successor:          AgentSessionRef{AgentID: f.succSession.AgentID, ExecutionID: f.succSession.ExecutionID},
		EventIDs:           []string{f.reqEvent.EventID},
		TTLSeconds:         1800,
		Reason:             "Owner resumed oversight session 01a150a3 in successor agent",
	}
	grant, err := f.op.AuthorizeSessionSuccessor(ctx, grantReq)
	if err != nil {
		t.Fatalf("AuthorizeSessionSuccessor: %v", err)
	}
	if grant.PredecessorAgentID != f.predSession.AgentID || grant.PredecessorExecutionID != f.predSession.ExecutionID ||
		grant.Successor.AgentID != f.succSession.AgentID || grant.Successor.ExecutionID != f.succSession.ExecutionID {
		t.Fatalf("unexpected grant binding: %+v", grant)
	}

	// Base profile without ForAgentSession does NOT gain successor access.
	_, err = f.succProfile.AgentEventStatus(ctx, EventStatusRequest{EventID: f.reqEvent.EventID}, f.dest)
	requireCode(t, err, "NOT_FOUND")
	// Unselected predecessor event stays NOT_FOUND.
	_, err = f.succView.AgentEventStatus(ctx, EventStatusRequest{EventID: otherEvent.EventID}, f.dest)
	requireCode(t, err, "NOT_FOUND")

	// Authorized successor session can inspect the selected predecessor event.
	status, err := f.succView.AgentEventStatus(ctx, EventStatusRequest{EventID: f.reqEvent.EventID}, f.dest)
	if err != nil {
		t.Fatalf("successor AgentEventStatus: %v", err)
	}
	if status.Event.EventID != f.reqEvent.EventID || len(status.Deliveries) != 1 ||
		status.Deliveries[0].DeliveryID != f.deliveryID || status.Deliveries[0].State != "failed" ||
		status.Deliveries[0].Consumer != f.predSession.Inbox || status.Deliveries[0].LeaseID != "" {
		t.Fatalf("unexpected inspected status: %+v", status)
	}

	// Authorized successor publishes a linked follow-up visibly attributed to itself.
	followUp, err := f.succView.PublishEvent(ctx, PublishEventRequest{
		RequestID:     uuid.NewString(),
		Kind:          "response",
		Ref:           RecordVersionRef{RecordID: replyRec.RecordID, Version: replyRec.Version},
		Destination:   EventDestination{Type: "agent", Name: f.senderSession.Inbox},
		CausationID:   f.reqEvent.EventID,
		CorrelationID: f.reqEvent.CorrelationID,
	}, f.dest)
	if err != nil {
		t.Fatalf("successor PublishEvent: %v", err)
	}
	if followUp.From != f.succSession.Inbox || followUp.CausationID != f.reqEvent.EventID {
		t.Fatalf("followUp attribution or causation wrong: %+v", followUp)
	}
	verifyOriginalStateAndResponseGroupUnchanged(t, f, followUp)
}

func verifyOriginalStateAndResponseGroupUnchanged(t *testing.T, f successorFixture, followUp AgentEvent) {
	t.Helper()
	ctx := context.Background()

	// Original delivery failure, attempts, and publisher identity remain unchanged.
	review, err := f.op.ReviewEvents(ctx, EventReviewRequest{Repo: f.repo, DeliveryID: f.deliveryID})
	if err != nil || len(review.Deliveries) != 1 {
		t.Fatalf("ReviewEvents: %+v %v", review, err)
	}
	orig := review.Deliveries[0]
	if orig.State != "failed" || orig.Consumer != f.predSession.Inbox || orig.Event.From != f.senderSession.Inbox ||
		orig.LatestNative == nil || orig.LatestNative.ID != f.attemptID || orig.LatestNative.Reason != "turn_ended" {
		t.Fatalf("original delivery/attempt modified: %+v", orig)
	}

	// Response group on the original request retains its original history:
	// the successor response does NOT fill predecessor's member slot and is
	// recorded as "unmatched".
	group, err := f.senderView.ResponseGroup(ctx, ResponseGroupQuery{EventID: f.reqEvent.EventID}, f.dest)
	if err != nil {
		t.Fatalf("ResponseGroup: %v", err)
	}
	if group.State != "open" || group.Expected != 1 || group.Responded != 0 || len(group.Members) != 1 ||
		group.Members[0].Consumer != f.predSession.Inbox || group.Members[0].Responded || group.Members[0].ResponseEventID != "" ||
		group.Members[0].DeliveryState != "failed" {
		t.Fatalf("successor reply mutated predecessor response-group slot: %+v", group)
	}
	if len(group.Observations) != 1 || group.Observations[0].EventID != followUp.EventID ||
		group.Observations[0].From != f.succSession.Inbox || group.Observations[0].Disposition != "unmatched" {
		t.Fatalf("expected unmatched observation for successor response: %+v", group.Observations)
	}
}

func TestSuccessorClosedResponseGroupNotReopenedOrRefilled(t *testing.T) {
	ctx := context.Background()
	f := setupSuccessorTakeoverFixture(t)

	// Expire the response group so it closes as incomplete.
	if _, err := f.op.pool.Exec(ctx, `UPDATE cairn.agent_response_group SET deadline=clock_timestamp()-interval '1 minute' WHERE event_id=$1`, f.reqEvent.EventID); err != nil {
		t.Fatal(err)
	}
	if _, err := f.op.SweepRequestControls(ctx, RequestControlSweep{Repo: f.repo}); err != nil {
		t.Fatal(err)
	}
	beforeGroup, err := f.senderView.ResponseGroup(ctx, ResponseGroupQuery{EventID: f.reqEvent.EventID}, f.dest)
	if err != nil || beforeGroup.State != "incomplete" || beforeGroup.Responded != 0 || beforeGroup.ClosedAt == nil {
		t.Fatalf("expected closed incomplete group: %+v %v", beforeGroup, err)
	}

	_, err = f.op.AuthorizeSessionSuccessor(ctx, AuthorizeSessionSuccessorRequest{
		RequestID:          uuid.NewString(),
		Repo:               f.repo,
		PredecessorAgentID: f.predSession.AgentID,
		Successor:          AgentSessionRef{AgentID: f.succSession.AgentID, ExecutionID: f.succSession.ExecutionID},
		EventIDs:           []string{f.reqEvent.EventID},
		Reason:             "Authorize follow-up on expired response group",
	})
	if err != nil {
		t.Fatal(err)
	}

	draft := projectNote(f.repo)
	draft.Sensitivity = "shareable"
	draft.Body = "Successor response after group closed"
	rec, err := f.succProfile.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}
	reply, err := f.succView.PublishEvent(ctx, PublishEventRequest{
		RequestID:     uuid.NewString(),
		Kind:          "response",
		Ref:           RecordVersionRef{RecordID: rec.RecordID, Version: rec.Version},
		Destination:   EventDestination{Type: "agent", Name: f.senderSession.Inbox},
		CausationID:   f.reqEvent.EventID,
		CorrelationID: f.reqEvent.CorrelationID,
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	afterGroup, err := f.senderView.ResponseGroup(ctx, ResponseGroupQuery{EventID: f.reqEvent.EventID}, f.dest)
	if err != nil || afterGroup.State != "incomplete" || afterGroup.Responded != 0 ||
		!afterGroup.ClosedAt.Equal(*beforeGroup.ClosedAt) || afterGroup.Members[0].Responded ||
		len(afterGroup.Observations) != 1 || afterGroup.Observations[0].EventID != reply.EventID ||
		afterGroup.Observations[0].Disposition != "unmatched" {
		t.Fatalf("closed response group reopened or refilled: %+v %v", afterGroup, err)
	}
}

func TestSuccessorIdempotencyRevocationFencingAndResumption(t *testing.T) {
	ctx := context.Background()
	f := setupSuccessorTakeoverFixture(t)

	// Ordinary profile or repo-scoped operator cannot authorize or revoke.
	grantReq := AuthorizeSessionSuccessorRequest{
		RequestID:          uuid.NewString(),
		Repo:               f.repo,
		PredecessorAgentID: f.predSession.AgentID,
		Successor:          AgentSessionRef{AgentID: f.succSession.AgentID, ExecutionID: f.succSession.ExecutionID},
		EventIDs:           []string{f.reqEvent.EventID},
		Reason:             "Operator takeover authorization",
	}
	_, err := f.succProfile.AuthorizeSessionSuccessor(ctx, grantReq)
	requireCode(t, err, "AUTHORITY_DENIED")
	scopedOp := testStore(t, Channel{Principal: "scoped-op", Repo: f.repo, Operator: true})
	_, err = scopedOp.AuthorizeSessionSuccessor(ctx, grantReq)
	requireCode(t, err, "AUTHORITY_DENIED")

	// Exact retry returns the identical grant; conflicting payload with same UUID refuses.
	g1, err := f.op.AuthorizeSessionSuccessor(ctx, grantReq)
	if err != nil {
		t.Fatal(err)
	}
	g2, err := f.op.AuthorizeSessionSuccessor(ctx, grantReq)
	if err != nil || g2.GrantID != g1.GrantID || !g2.ExpiresAt.Equal(g1.ExpiresAt) {
		t.Fatalf("idempotent retry mismatch: %+v %+v %v", g1, g2, err)
	}
	conflictReq := grantReq
	conflictReq.Reason = "Changed reason with same request UUID"
	_, err = f.op.AuthorizeSessionSuccessor(ctx, conflictReq)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")

	// Competing successor session can receive an independent grant for the same event.
	compSession, err := f.succProfile.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID: uuid.NewString(), Binding: "claude-oversight", NativeSessionID: "01a150a3-competing",
		Metadata: AgentMetadata{Harness: "claude", Project: "cairn", Workspace: "/work/cairn", State: "busy", DeliveryMode: "existing-session"},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	compView, err := f.succProfile.ForAgentSession(AgentSessionRef{AgentID: compSession.AgentID, ExecutionID: compSession.ExecutionID}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	compGrant, err := f.op.AuthorizeSessionSuccessor(ctx, AuthorizeSessionSuccessorRequest{
		RequestID:          uuid.NewString(),
		Repo:               f.repo,
		PredecessorAgentID: f.predSession.AgentID,
		Successor:          AgentSessionRef{AgentID: compSession.AgentID, ExecutionID: compSession.ExecutionID},
		EventIDs:           []string{f.reqEvent.EventID},
		Reason:             "Second competing successor grant",
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = compView.AgentEventStatus(ctx, EventStatusRequest{EventID: f.reqEvent.EventID}, f.dest); err != nil {
		t.Fatalf("competing successor inspect: %v", err)
	}

	verifyRevocationAndLifecycleFences(t, f, g1, compGrant, compView)
}

func verifyRevocationAndLifecycleFences(t *testing.T, f successorFixture, g1, compGrant SessionSuccessorGrant, compView *Store) {
	t.Helper()
	ctx := context.Background()

	// Revoke g1; idempotent retry succeeds, different request on revoked grant fails with VERSION_CONFLICT.
	revReq := RevokeSessionSuccessorRequest{
		RequestID: uuid.NewString(),
		GrantID:   g1.GrantID,
		Reason:    "Revoke first successor grant",
	}
	rev1, err := f.op.RevokeSessionSuccessor(ctx, revReq)
	if err != nil || rev1.RevokedAt == nil || rev1.RevokeReason != revReq.Reason {
		t.Fatalf("RevokeSessionSuccessor: %+v %v", rev1, err)
	}
	rev2, err := f.op.RevokeSessionSuccessor(ctx, revReq)
	if err != nil || !rev2.RevokedAt.Equal(*rev1.RevokedAt) {
		t.Fatalf("idempotent revoke retry: %+v %v", rev2, err)
	}
	_, err = f.op.RevokeSessionSuccessor(ctx, RevokeSessionSuccessorRequest{
		RequestID: uuid.NewString(),
		GrantID:   g1.GrantID,
		Reason:    "Different revocation reason",
	})
	requireCode(t, err, "VERSION_CONFLICT")

	// Revoked successor loses access immediately; competing successor keeps its own active grant.
	_, err = f.succView.AgentEventStatus(ctx, EventStatusRequest{EventID: f.reqEvent.EventID}, f.dest)
	requireCode(t, err, "NOT_FOUND")
	if _, err = compView.AgentEventStatus(ctx, EventStatusRequest{EventID: f.reqEvent.EventID}, f.dest); err != nil {
		t.Fatalf("competing successor should still have access: %v", err)
	}

	// If competing successor re-registers (new execution_id), the old execution is STALE_SESSION
	// and the new execution has no grant until explicitly authorized.
	resumedComp, err := f.succProfile.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID: uuid.NewString(), Binding: "claude-oversight", NativeSessionID: "01a150a3-competing",
		Metadata: AgentMetadata{Harness: "claude", Project: "cairn", Workspace: "/work/cairn", State: "busy", DeliveryMode: "existing-session"},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	_, err = compView.AgentEventStatus(ctx, EventStatusRequest{EventID: f.reqEvent.EventID}, f.dest)
	requireCode(t, err, "STALE_SESSION")
	resumedCompView, err := f.succProfile.ForAgentSession(AgentSessionRef{AgentID: resumedComp.AgentID, ExecutionID: resumedComp.ExecutionID}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	_, err = resumedCompView.AgentEventStatus(ctx, EventStatusRequest{EventID: f.reqEvent.EventID}, f.dest)
	requireCode(t, err, "NOT_FOUND")

	// Authorize the new execution, then test predecessor resumption.
	_, err = f.op.AuthorizeSessionSuccessor(ctx, AuthorizeSessionSuccessorRequest{
		RequestID:          uuid.NewString(),
		Repo:               f.repo,
		PredecessorAgentID: f.predSession.AgentID,
		Successor:          AgentSessionRef{AgentID: resumedComp.AgentID, ExecutionID: resumedComp.ExecutionID},
		EventIDs:           []string{f.reqEvent.EventID},
		Reason:             "Authorize resumed competing successor execution",
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = resumedCompView.AgentEventStatus(ctx, EventStatusRequest{EventID: f.reqEvent.EventID}, f.dest); err != nil {
		t.Fatalf("resumed competing successor inspect: %v", err)
	}

	// Predecessor resumes registration -> successor grant is immediately fenced!
	resumedPred, err := f.predProfile.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID: uuid.NewString(), Binding: "claude-oversight", NativeSessionID: "01a150a3-pred",
		Metadata: AgentMetadata{Harness: "claude", Project: "cairn", Workspace: "/work/cairn", State: "idle", DeliveryMode: "existing-session"},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	_, err = resumedCompView.AgentEventStatus(ctx, EventStatusRequest{EventID: f.reqEvent.EventID}, f.dest)
	requireCode(t, err, "NOT_FOUND")

	// While predecessor is live, operator cannot authorize a new successor grant (SESSION_LIVE).
	_, err = f.op.AuthorizeSessionSuccessor(ctx, AuthorizeSessionSuccessorRequest{
		RequestID:          uuid.NewString(),
		Repo:               f.repo,
		PredecessorAgentID: resumedPred.AgentID,
		Successor:          AgentSessionRef{AgentID: resumedComp.AgentID, ExecutionID: resumedComp.ExecutionID},
		EventIDs:           []string{f.reqEvent.EventID},
		Reason:             "Attempt takeover while predecessor is live",
	})
	requireCode(t, err, "SESSION_LIVE")
}

func TestSuccessorSensitivityCrossCollectionAndExpiry(t *testing.T) {
	ctx := context.Background()
	f := setupSuccessorTakeoverFixture(t)

	// Create a local-only source and a local predecessor event.
	localDraft := projectNote(f.repo)
	localDraft.Sensitivity = "local"
	localDraft.Body = "Local-only request body"
	localRec, err := f.senderProfile.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: localDraft})
	if err != nil {
		t.Fatal(err)
	}
	localDest := Destination{Name: "local", AllowLocal: true}
	localEvent, err := f.senderView.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Kind:        "request",
		Ref:         RecordVersionRef{RecordID: localRec.RecordID, Version: localRec.Version},
		Destination: EventDestination{Type: "agent", Name: f.predSession.Inbox},
	}, localDest)
	if err != nil {
		t.Fatal(err)
	}

	// Cross-collection event cannot be included in a grant for f.repo.
	foreignRepo := "foreign-" + uuid.NewString()
	foreignSender := testStore(t, Channel{Principal: "foreign-sender-" + foreignRepo, Repo: foreignRepo})
	foreignDraft := projectNote(foreignRepo)
	foreignDraft.Sensitivity = "shareable"
	foreignRec, err := foreignSender.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: foreignDraft})
	if err != nil {
		t.Fatal(err)
	}
	foreignEvent, err := foreignSender.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Kind:        "notice",
		Ref:         RecordVersionRef{RecordID: foreignRec.RecordID, Version: foreignRec.Version},
		Destination: EventDestination{Type: "agent", Name: f.predSession.Inbox},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	_, err = f.op.AuthorizeSessionSuccessor(ctx, AuthorizeSessionSuccessorRequest{
		RequestID:          uuid.NewString(),
		Repo:               f.repo,
		PredecessorAgentID: f.predSession.AgentID,
		Successor:          AgentSessionRef{AgentID: f.succSession.AgentID, ExecutionID: f.succSession.ExecutionID},
		EventIDs:           []string{foreignEvent.EventID},
		Reason:             "Reject foreign collection event",
	})
	requireCode(t, err, "NOT_FOUND")

	// Authorize localEvent for successor.
	grant, err := f.op.AuthorizeSessionSuccessor(ctx, AuthorizeSessionSuccessorRequest{
		RequestID:          uuid.NewString(),
		Repo:               f.repo,
		PredecessorAgentID: f.predSession.AgentID,
		Successor:          AgentSessionRef{AgentID: f.succSession.AgentID, ExecutionID: f.succSession.ExecutionID},
		EventIDs:           []string{localEvent.EventID, f.reqEvent.EventID},
		Reason:             "Authorize local and shareable events",
	})
	if err != nil {
		t.Fatal(err)
	}

	// Hosted destination view cannot inspect localEvent or publish a follow-up to it.
	_, err = f.succView.AgentEventStatus(ctx, EventStatusRequest{EventID: localEvent.EventID}, Destination{Name: "hosted"})
	requireCode(t, err, "NOT_FOUND")

	// Local destination view on a local-registered successor can inspect localEvent,
	// but still cannot publish a shareable event disclosing a local causal parent.
	localSuccSession, err := f.succProfile.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID: uuid.NewString(), Binding: "claude-local", NativeSessionID: "01a150a3-local-succ",
		Metadata: AgentMetadata{Harness: "claude", Project: "cairn", Workspace: "/work/cairn", State: "busy", DeliveryMode: "existing-session"},
	}, localDest)
	if err != nil {
		t.Fatal(err)
	}
	localSuccView, err := f.succProfile.ForAgentSession(AgentSessionRef{AgentID: localSuccSession.AgentID, ExecutionID: localSuccSession.ExecutionID}, localDest)
	if err != nil {
		t.Fatal(err)
	}
	_, err = f.op.AuthorizeSessionSuccessor(ctx, AuthorizeSessionSuccessorRequest{
		RequestID:          uuid.NewString(),
		Repo:               f.repo,
		PredecessorAgentID: f.predSession.AgentID,
		Successor:          AgentSessionRef{AgentID: localSuccSession.AgentID, ExecutionID: localSuccSession.ExecutionID},
		EventIDs:           []string{localEvent.EventID},
		Reason:             "Authorize local successor for local event",
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = localSuccView.AgentEventStatus(ctx, EventStatusRequest{EventID: localEvent.EventID}, localDest); err != nil {
		t.Fatalf("local successor inspect local event: %v", err)
	}
	_, err = localSuccView.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Kind:        "notice",
		Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{Type: "agent", Name: f.senderSession.Inbox},
		CausationID: localEvent.EventID,
	}, localDest)
	requireCode(t, err, "DESTINATION_PROHIBITED")

	// Expire grant -> successor loses access.
	if _, err = f.op.pool.Exec(ctx, `UPDATE cairn.agent_successor_grant SET granted_at=clock_timestamp()-interval '10 minutes',expires_at=clock_timestamp()-interval '1 second' WHERE grant_id=$1`, grant.GrantID); err != nil {
		t.Fatal(err)
	}
	_, err = f.succView.AgentEventStatus(ctx, EventStatusRequest{EventID: f.reqEvent.EventID}, f.dest)
	requireCode(t, err, "NOT_FOUND")

	// Restore fence advances database_generation and fences any prior successor grants.
	if _, err = f.op.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "Fence after restore"}); err != nil {
		t.Fatal(err)
	}
	_, err = localSuccView.AgentEventStatus(ctx, EventStatusRequest{EventID: localEvent.EventID}, localDest)
	requireCode(t, err, "STALE_SESSION")
}
