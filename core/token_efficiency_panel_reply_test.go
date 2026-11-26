package core

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"testing"

	"github.com/google/uuid"
)

type panelReplyTransition struct {
	Phase  string `json:"phase"`
	Action string `json:"action"`
	Target string `json:"target"`
}

func panelReplyTransitions(t *testing.T, caseID string) []panelReplyTransition {
	t.Helper()
	raw, err := os.ReadFile("../trials/token-efficiency/FIXTURE.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Cases []struct {
			ID    string `json:"id"`
			Setup struct {
				Transitions []panelReplyTransition `json:"transitions"`
			} `json:"setup"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	for _, row := range fixture.Cases {
		if row.ID == caseID {
			return row.Setup.Transitions
		}
	}
	t.Fatalf("missing panel case %q", caseID)
	return nil
}

type panelReplyRequest struct {
	sender        *Store
	worker        *Store
	senderProfile *Store
	workerProfile *Store
	senderSession AgentSessionRef
	workerSession AgentSessionRef
	workerAttempt string
	event         AgentEvent
	claim         AgentDelivery
	dest          Destination
}

func setupPanelReplyRequest(t *testing.T, fixture panelNativeEventFixture, caseID, eventID, wantBody string) panelReplyRequest {
	t.Helper()
	ctx := context.Background()
	row := fixture.eventCase(t, caseID)
	if len(row.Setup.Events) != 1 || row.Setup.Events[0].ID != eventID ||
		row.Setup.Events[0].Kind != "request" || row.Setup.Events[0].Recipient != "worker" ||
		row.Setup.Events[0].Body != wantBody {
		t.Fatalf("%s request fixture changed: %+v", caseID, row.Setup.Events)
	}
	repo := fixture.EventSourceDefaults.Scope.Repo
	senderProfile := testStore(t, Channel{Principal: "agent:panel-reply-sender-" + uuid.NewString(), Repo: repo})
	workerProfile := testStore(t, Channel{Principal: "agent:panel-reply-worker-" + uuid.NewString(), Repo: repo})
	dest := Destination{Name: "hosted", AllowLocal: false}
	register := func(profile *Store) (AgentSessionRef, string, *Store) {
		t.Helper()
		binding := "panel-reply-" + uuid.NewString()
		session, err := profile.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(),
			Binding: binding, NativeSessionID: binding,
			Metadata: AgentMetadata{Harness: "codex", Project: "cairn", Workspace: "/fixture/cairn",
				State: "idle", DeliveryMode: "existing-session"}}, dest)
		if err != nil {
			t.Fatal(err)
		}
		ref := AgentSessionRef{AgentID: session.AgentID, ExecutionID: session.ExecutionID}
		view, err := profile.ForAgentSession(ref, dest)
		if err != nil {
			t.Fatal(err)
		}
		return ref, session.Inbox, view
	}
	senderSession, senderInbox, sender := register(senderProfile)
	workerSession, workerInbox, worker := register(workerProfile)
	if senderInbox != sender.channel.Principal || workerInbox != worker.channel.Principal {
		t.Fatal("registered inbox does not match session view")
	}
	ref := fixture.createEventSource(t, senderProfile, row.Setup.Events[0])
	event, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request",
		Ref: ref, Destination: EventDestination{Type: "agent", Name: workerInbox}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ready, err := workerProfile.SessionInboxReady(ctx, workerSession, dest)
	if err != nil || ready.EventID != event.EventID || ready.DeliveryID == "" {
		t.Fatalf("%s readiness: %+v %v", caseID, ready, err)
	}
	workerTurn := "turn-" + uuid.NewString()
	claim, err := workerProfile.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(),
		Session: workerSession, DeliveryID: ready.DeliveryID, NativeTurnID: workerTurn,
		TurnExclusive: true}, dest)
	if err != nil || claim.Attempt == nil || claim.Attempt.Delivery.Event.EventID != event.EventID ||
		claim.Attempt.Delivery.DeliveryID != ready.DeliveryID || claim.Attempt.NativeTurnID != workerTurn {
		t.Fatalf("%s native claim: %+v %v", caseID, claim, err)
	}
	history, err := worker.History(ctx, RecordHistoryRequest{RecordID: ref.RecordID, Version: ref.Version}, dest)
	if err != nil || len(history.Versions) != 1 || history.Versions[0].Body == nil ||
		*history.Versions[0].Body != row.Setup.Events[0].Body ||
		history.Versions[0].ObservedWriter != senderProfile.channel.Principal {
		t.Fatalf("%s exact store-owned source: %+v %v", caseID, history, err)
	}
	return panelReplyRequest{sender: sender, worker: worker, senderProfile: senderProfile,
		workerProfile: workerProfile, senderSession: senderSession, workerSession: workerSession,
		workerAttempt: claim.Attempt.ID, event: event, claim: claim.Attempt.Delivery, dest: dest}
}

func (p panelReplyRequest) complete(t *testing.T, body string) (CompleteEventRequest, AgentDelivery) {
	t.Helper()
	draft := Draft{Kind: "note", Body: body,
		Scope:     Scope{Repo: p.event.Repo, TaskID: "coordination-result/" + p.claim.DeliveryID, RunID: "*"},
		ClaimType: "self", Sensitivity: "shareable"}
	req := CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: p.claim.DeliveryID,
		LeaseID: p.claim.LeaseID, Disposition: "handled", Draft: &draft}
	done, err := p.worker.CompleteEvent(context.Background(), req, p.dest)
	if err != nil || done.State != "handled" || done.Result == nil || done.CompletedAt == nil {
		t.Fatalf("complete request: %+v %v", done, err)
	}
	history, err := p.worker.History(context.Background(), RecordHistoryRequest{
		RecordID: done.Result.RecordID, Version: done.Result.Version}, p.dest)
	if err != nil || len(history.Versions) != 1 || history.Versions[0].Body == nil ||
		*history.Versions[0].Body != body || history.Versions[0].ObservedWriter != p.worker.channel.Principal {
		t.Fatalf("exact store-owned result: %+v %v", history, err)
	}
	return req, done
}

func (p panelReplyRequest) responseRequest(result RecordVersionRef, requestID string) PublishEventRequest {
	return PublishEventRequest{RequestID: requestID, Kind: "response", Ref: result,
		Destination: EventDestination{Type: "agent", Name: p.sender.channel.Principal},
		CausationID: p.event.EventID}
}

func (p panelReplyRequest) checkResponse(t *testing.T, response AgentEvent, result RecordVersionRef) {
	t.Helper()
	ctx := context.Background()
	if response.Kind != "response" || response.Ref != result || response.CausationID != p.event.EventID ||
		response.From != p.worker.channel.Principal {
		t.Fatalf("response identity: %+v", response)
	}
	status, err := p.worker.AgentEventStatus(ctx, EventStatusRequest{EventID: response.EventID}, p.dest)
	if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].State != "pending" ||
		status.Deliveries[0].Consumer != p.sender.channel.Principal {
		t.Fatalf("durable response receipt: %+v %v", status, err)
	}
	ready, err := p.senderProfile.SessionInboxReady(ctx, p.senderSession, p.dest)
	if err != nil || ready.DeliveryID != status.Deliveries[0].DeliveryID || ready.EventID != response.EventID {
		t.Fatalf("response readiness: %+v %v", ready, err)
	}
	senderTurn := "turn-" + uuid.NewString()
	claim, err := p.senderProfile.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(),
		Session: p.senderSession, DeliveryID: ready.DeliveryID, NativeTurnID: senderTurn,
		TurnExclusive: true}, p.dest)
	if err != nil || claim.Attempt == nil || claim.Attempt.Delivery.Event.EventID != response.EventID ||
		claim.Attempt.Delivery.DeliveryID != ready.DeliveryID || claim.Attempt.NativeTurnID != senderTurn {
		t.Fatalf("native response claim: %+v %v", claim, err)
	}
	history, err := p.sender.History(ctx, RecordHistoryRequest{RecordID: result.RecordID, Version: result.Version}, p.dest)
	if err != nil || len(history.Versions) != 1 || history.Versions[0].Body == nil ||
		history.Versions[0].ObservedWriter != p.worker.channel.Principal {
		t.Fatalf("response result source: %+v %v", history, err)
	}
	ack, err := p.sender.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(),
		DeliveryID: claim.Attempt.Delivery.DeliveryID, LeaseID: claim.Attempt.Delivery.LeaseID,
		Disposition: "handled"}, p.dest)
	if err != nil || ack.State != "handled" {
		t.Fatalf("response settlement: %+v %v", ack, err)
	}
	senderClosed, err := p.senderProfile.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(),
		Session: p.senderSession, AttemptID: claim.Attempt.ID, Reason: "delivery_completed"}, p.dest)
	if err != nil || senderClosed.FinishedAt == nil || senderClosed.Reason != "delivery_completed" {
		t.Fatalf("response attempt reconciliation: %+v %v", senderClosed, err)
	}
	workerClosed, err := p.workerProfile.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(),
		Session: p.workerSession, AttemptID: p.workerAttempt, Reason: "delivery_completed"}, p.dest)
	if err != nil || workerClosed.FinishedAt == nil || workerClosed.Reason != "delivery_completed" {
		t.Fatalf("request attempt reconciliation: %+v %v", workerClosed, err)
	}
	old, err := p.workerProfile.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(),
		Session: p.workerSession, DeliveryID: p.claim.DeliveryID, NativeTurnID: "turn-" + uuid.NewString()}, p.dest)
	if err != nil || old.Attempt != nil {
		t.Fatalf("handled request was claimable again: %+v %v", old, err)
	}
}

// These disposable Store probes exercise native session claims, exact sources,
// completion, response, and idempotency. They do not run an actor or transport.
func TestTokenEfficiencyPanelNativeReplyProbes(t *testing.T) {
	ctx := context.Background()
	fixture := readPanelNativeEventFixture(t)

	t.Run("small_request_result_reply", func(t *testing.T) {
		if transitions := panelReplyTransitions(t, "small_request_result_reply"); len(transitions) != 0 {
			t.Fatalf("small request transitions changed: %+v", transitions)
		}
		p := setupPanelReplyRequest(t, fixture, "small_request_result_reply", "request_hash", "Return SHA-256 of cedar.")
		const digest = "0c274639936717793d7695ef0373039e397acdabc247d9953d3c9209a95044cd"
		computed := fmt.Sprintf("%x", sha256.Sum256([]byte("cedar")))
		if computed != digest {
			t.Fatalf("exact UTF-8 cedar digest changed: %s", computed)
		}
		_, done := p.complete(t, computed)
		response, err := p.worker.PublishEvent(ctx, p.responseRequest(*done.Result, uuid.NewString()), p.dest)
		if err != nil {
			t.Fatal(err)
		}
		p.checkResponse(t, response, *done.Result)
	})

	t.Run("completion_reply_lost", func(t *testing.T) {
		transitions := panelReplyTransitions(t, "completion_reply_lost")
		if len(transitions) != 2 || transitions[0] != (panelReplyTransition{"after_completion", "drop_reply", "request_lost_reply"}) ||
			transitions[1] != (panelReplyTransition{"after_drop", "retry_delivery", "request_lost_reply"}) {
			t.Fatalf("lost-reply fixture transitions changed: %+v", transitions)
		}
		p := setupPanelReplyRequest(t, fixture, "completion_reply_lost", "request_lost_reply", "Report checksum CK-9.")
		completion, done := p.complete(t, "CK-9")
		// Drop the outward response after completion commits. The persisted
		// request is handled; journal replay, not delivery claim, owns recovery.
		status, err := p.sender.AgentEventStatus(ctx, EventStatusRequest{EventID: p.event.EventID}, p.dest)
		if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].State != "handled" ||
			status.Deliveries[0].Result == nil || *status.Deliveries[0].Result != *done.Result {
			t.Fatalf("committed request status: %+v %v", status, err)
		}
		recovered, err := p.worker.CompleteEvent(ctx, completion, p.dest)
		if err != nil || recovered.Result == nil || *recovered.Result != *done.Result ||
			recovered.CompletedAt == nil || !recovered.CompletedAt.Equal(*done.CompletedAt) {
			t.Fatalf("same completion result recovery: %+v %v", recovered, err)
		}
		response, err := p.worker.PublishEvent(ctx, p.responseRequest(*recovered.Result, uuid.NewString()), p.dest)
		if err != nil {
			t.Fatal(err)
		}
		p.checkResponse(t, response, *done.Result)
	})

	t.Run("relay_uncertain_response", func(t *testing.T) {
		transitions := panelReplyTransitions(t, "relay_uncertain_response")
		if len(transitions) != 2 || transitions[0] != (panelReplyTransition{"after_send_before_receipt", "hide_receipt", "request_relay_uncertain"}) ||
			transitions[1] != (panelReplyTransition{"after_uncertain", "retry_same_response", "request_relay_uncertain"}) {
			t.Fatalf("uncertain-response fixture transitions changed: %+v", transitions)
		}
		p := setupPanelReplyRequest(t, fixture, "relay_uncertain_response", "request_relay_uncertain", "Return marker RY-5.")
		_, done := p.complete(t, "RY-5")
		responseReq := p.responseRequest(*done.Result, uuid.NewString())
		first, err := p.worker.PublishEvent(ctx, responseReq, p.dest)
		if err != nil {
			t.Fatal(err)
		}
		// The caller does not act on the first returned receipt. Retry the
		// immutable publication identity, then inspect the durable receipt.
		recovered, err := p.worker.PublishEvent(ctx, responseReq, p.dest)
		if err != nil || recovered.EventID != first.EventID || recovered.Position != first.Position {
			t.Fatalf("same-response retry: first=%+v recovered=%+v err=%v", first, recovered, err)
		}
		p.checkResponse(t, recovered, *done.Result)
	})
}
