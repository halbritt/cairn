package core

import (
	"context"
	"reflect"
	"testing"

	"github.com/google/uuid"
)

func TestSessionInboxPollStatusEmptyAndHistoricalExecution(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	a := f.register(nativeAgent{Binding: "empty-poll-status"})
	ref := sessionRef(a)
	id := uuid.NewString()
	claim, err := f.receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: id, Session: ref}, f.dest)
	if err != nil || claim.Attempt != nil {
		t.Fatalf("empty poll: %+v %v", claim, err)
	}
	want := SessionInboxPoll{RequestID: id, Session: ref}
	before := attemptStatusState(t, f.receiver, ref.AgentID, uuid.NewString(), uuid.NewString())
	got, err := f.receiver.SessionInboxPollStatus(ctx, SessionInboxPollQuery{Session: ref, RequestID: id}, f.dest)
	if err != nil || !reflect.DeepEqual(got, want) {
		t.Fatalf("empty poll read: got %+v, want %+v: %v", got, want, err)
	}
	if after := attemptStatusState(t, f.receiver, ref.AgentID, uuid.NewString(), uuid.NewString()); after != before {
		t.Fatal("poll inspection mutated session/poll state")
	}
	resumed := f.register(nativeAgent{Binding: "empty-poll-status"})
	got, err = f.receiver.SessionInboxPollStatus(ctx, SessionInboxPollQuery{Session: sessionRef(resumed), RequestID: id}, f.dest)
	if err != nil || !reflect.DeepEqual(got, want) {
		t.Fatalf("historical empty poll execution changed: %+v %v", got, err)
	}
	_, err = f.receiver.SessionInboxPollStatus(ctx, SessionInboxPollQuery{Session: ref, RequestID: id}, f.dest)
	requireCode(t, err, "STALE_SESSION")
}

func TestSessionInboxPollStatusOpenedAndForeignRefusals(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	a, _, claim := f.claimed(nativeAgent{Binding: "poll-opened"}, "request", "turn-one", true)
	ref, id := sessionRef(a), claim.Attempt.ID
	before := attemptStatusState(t, f.receiver, ref.AgentID, id, claim.Attempt.Delivery.DeliveryID)
	got, err := f.receiver.SessionInboxPollStatus(ctx, SessionInboxPollQuery{Session: ref, RequestID: id}, f.dest)
	if err != nil || got.RequestID != id || got.Session != ref || got.AttemptID == nil || *got.AttemptID != id {
		t.Fatalf("opened poll: %+v %v", got, err)
	}
	other := f.register(nativeAgent{Binding: "other-poll-agent"})
	foreign := testStore(t, Channel{Principal: "foreign-" + f.repo, Repo: f.repo})
	wrongRepo := testStore(t, Channel{Principal: f.receiver.channel.Principal, Repo: uuid.NewString()})
	instrumented := testStore(t, Channel{Principal: f.receiver.channel.Principal, Repo: f.repo, Instrumented: true})
	for _, tc := range []struct {
		name     string
		store    *Store
		ref      AgentSessionRef
		id, code string
	}{
		{"foreign-owner", foreign, ref, id, "NOT_FOUND"},
		{"other-agent", f.receiver, sessionRef(other), id, "NOT_FOUND"},
		{"wrong-repo", wrongRepo, ref, id, "NOT_FOUND"},
		{"session-view", f.view(ref), ref, id, "INVALID_REQUEST"},
		{"instrumented", instrumented, ref, id, "INVALID_REQUEST"},
		{"missing", f.receiver, ref, uuid.NewString(), "NOT_FOUND"},
		{"invalid", f.receiver, ref, "not-a-uuid", "INVALID_REQUEST"},
		{"zero", f.receiver, ref, uuid.Nil.String(), "INVALID_REQUEST"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := tc.store.SessionInboxPollStatus(ctx, SessionInboxPollQuery{Session: tc.ref, RequestID: tc.id}, f.dest)
			requireCode(t, err, tc.code)
			if !reflect.DeepEqual(got, SessionInboxPoll{}) {
				t.Fatalf("refusal disclosed poll: %+v", got)
			}
		})
	}
	if after := attemptStatusState(t, f.receiver, ref.AgentID, id, claim.Attempt.Delivery.DeliveryID); after != before {
		t.Fatal("poll reads modified attempt/session/delivery")
	}
}

func TestSessionInboxPollStatusSessionBoundaries(t *testing.T) {
	for _, boundary := range []string{"stopped", "restore", "local-only"} {
		t.Run(boundary, func(t *testing.T) {
			ctx := context.Background()
			f := newNativeFixture(t)
			dest := f.dest
			if boundary == "local-only" {
				dest = Destination{Name: "local", AllowLocal: true}
			}
			a := f.register(nativeAgent{Binding: "poll-boundary", Dest: &dest})
			ref, id := sessionRef(a), uuid.NewString()
			if claim, err := f.receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: id, Session: ref}, dest); err != nil || claim.Attempt != nil {
				t.Fatalf("empty poll: %+v %v", claim, err)
			}
			code := "STALE_SESSION"
			switch boundary {
			case "stopped":
				if _, err := f.receiver.LeaveAgent(ctx, ref, dest); err != nil {
					t.Fatal(err)
				}
			case "restore":
				if _, err := f.operator().FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "disposable poll status boundary"}); err != nil {
					t.Fatal(err)
				}
			case "local-only":
				code = "NOT_FOUND"
				if got, err := f.receiver.SessionInboxPollStatus(ctx, SessionInboxPollQuery{Session: ref, RequestID: id}, dest); err != nil || got.RequestID != id {
					t.Fatalf("local poll unavailable to local destination: %+v %v", got, err)
				}
			}
			before := attemptStatusState(t, f.receiver, ref.AgentID, uuid.NewString(), uuid.NewString())
			got, err := f.receiver.SessionInboxPollStatus(ctx, SessionInboxPollQuery{Session: ref, RequestID: id}, f.dest)
			requireCode(t, err, code)
			if !reflect.DeepEqual(got, SessionInboxPoll{}) || attemptStatusState(t, f.receiver, ref.AgentID, uuid.NewString(), uuid.NewString()) != before {
				t.Fatal("refused poll read disclosed or changed retained state")
			}
		})
	}
}

func TestSessionInboxAttemptStatusActiveAndHistoricalExecution(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	a, _, claim := f.claimed(nativeAgent{Binding: "attempt-status"}, "request", "turn-one", true)
	ref := sessionRef(a)
	query := SessionInboxAttemptQuery{Session: ref, AttemptID: claim.Attempt.ID}
	want := *claim.Attempt
	want.Delivery.LeaseID, want.Delivery.LeaseUntil = "", nil
	before := attemptStatusState(t, f.receiver, ref.AgentID, query.AttemptID, want.Delivery.DeliveryID)
	got, err := f.receiver.SessionInboxAttemptStatus(ctx, query, f.dest)
	if err != nil || !reflect.DeepEqual(got, want) {
		t.Fatalf("active attempt: got %+v, want %+v: %v", got, want, err)
	}
	if after := attemptStatusState(t, f.receiver, ref.AgentID, query.AttemptID, want.Delivery.DeliveryID); after != before {
		t.Fatalf("active read changed session, attempt, tools, poll or delivery: before %s after %s", before, after)
	}
	if _, err = f.view(ref).CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: want.Delivery.DeliveryID, LeaseID: claim.Attempt.Delivery.LeaseID, Disposition: "handled"}, f.dest); err != nil {
		t.Fatal(err)
	}
	want, err = f.receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: query.AttemptID, Reason: "delivery_completed"}, f.dest)
	if err != nil || want.FinishedAt == nil {
		t.Fatalf("finished historical fixture: %+v %v", want, err)
	}
	want.Delivery.LeaseID, want.Delivery.LeaseUntil = "", nil
	// Registering the same conversation advances its execution. The current
	// reference authenticates the read; the retained attempt keeps its old one.
	resumed := f.register(nativeAgent{Binding: "attempt-status"})
	query.Session = sessionRef(resumed)
	before = attemptStatusState(t, f.receiver, ref.AgentID, query.AttemptID, want.Delivery.DeliveryID)
	got, err = f.receiver.SessionInboxAttemptStatus(ctx, query, f.dest)
	if err != nil || !reflect.DeepEqual(got, want) || got.Session.ExecutionID == resumed.ExecutionID {
		t.Fatalf("historical execution: got %+v, want %+v: %v", got, want, err)
	}
	if after := attemptStatusState(t, f.receiver, ref.AgentID, query.AttemptID, want.Delivery.DeliveryID); after != before {
		t.Fatalf("historical read changed retained state: before %s after %s", before, after)
	}
	query.Session = ref
	_, err = f.receiver.SessionInboxAttemptStatus(ctx, query, f.dest)
	requireCode(t, err, "STALE_SESSION")
	query.AttemptID = uuid.NewString()
	_, err = f.receiver.SessionInboxAttemptStatus(ctx, query, f.dest)
	requireCode(t, err, "STALE_SESSION") // Authenticate before looking for the attempt.
	query.Session = sessionRef(resumed)
	_, err = f.receiver.SessionInboxAttemptStatus(ctx, query, f.dest)
	requireCode(t, err, "NOT_FOUND")
}

func TestSessionInboxAttemptStatusRejectsStoppedAndRestoreFencedSessions(t *testing.T) {
	for _, boundary := range []string{"stopped", "restore"} {
		t.Run(boundary, func(t *testing.T) {
			ctx := context.Background()
			f := newNativeFixture(t)
			a, _, claim := f.claimed(nativeAgent{Binding: "status-fence"}, "request", "turn-one", true)
			ref := sessionRef(a)
			if boundary == "stopped" {
				if _, err := f.receiver.LeaveAgent(ctx, ref, f.dest); err != nil {
					t.Fatal(err)
				}
			} else if _, err := f.operator().FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "disposable attempt status generation boundary"}); err != nil {
				t.Fatal(err)
			}
			before := attemptStatusState(t, f.receiver, ref.AgentID, claim.Attempt.ID, claim.Attempt.Delivery.DeliveryID)
			got, err := f.receiver.SessionInboxAttemptStatus(ctx, SessionInboxAttemptQuery{ref, claim.Attempt.ID}, f.dest)
			requireCode(t, err, "STALE_SESSION")
			if !reflect.DeepEqual(got, SessionInboxAttempt{}) {
				t.Fatalf("fenced session exposed historical data: %+v", got)
			}
			if after := attemptStatusState(t, f.receiver, ref.AgentID, claim.Attempt.ID, claim.Attempt.Delivery.DeliveryID); after != before {
				t.Fatalf("stale read changed retained state: before %s after %s", before, after)
			}
		})
	}
}

func TestSessionInboxAttemptStatusRetainsFinishedCancelAndTools(t *testing.T) {
	ctx := context.Background()
	op, receiver, _, ref, id, dest := nativeCancelFixture(t)
	attempt, err := receiver.CaptureSessionTools(ctx, SessionToolCapture{RequestID: uuid.NewString(), Session: ref, AttemptID: id, Items: []SessionToolItem{
		{ItemID: "retained-tool", ProcessID: "701", Command: "synthetic command", NativeTurnID: "turn-one"},
	}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: attempt.Delivery.Event.Repo, DeliveryID: attempt.Delivery.DeliveryID, Reason: "selected historical cancellation"})
	if err != nil {
		t.Fatal(err)
	}
	pending, err := receiver.SessionInboxAttemptStatus(ctx, SessionInboxAttemptQuery{ref, id}, dest)
	if err != nil || pending.Cancel == nil || pending.Cancel.ConfirmedAt != nil || pending.FinishedAt != nil {
		t.Fatalf("pending cancellation was not retained: %+v %v", pending, err)
	}
	_, err = receiver.ReportSessionToolStop(ctx, SessionToolStopReport{RequestID: uuid.NewString(), Session: ref, AttemptID: id, TurnStop: "interrupted", TerminalScan: "clear", Tools: []SessionToolStop{{ItemID: "retained-tool", StopState: "terminated"}}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	want, err := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: id, Reason: "cancel_confirmed"}, dest)
	if err != nil || want.FinishedAt == nil || want.Cancel == nil || want.Cancel.ConfirmedAt == nil || len(want.Tools) != 1 || want.Tools[0].StopAt == nil {
		t.Fatalf("finished fixture: %+v %v", want, err)
	}
	control, err := receiver.SessionInboxControl(ctx, ref, dest)
	if err != nil || control.Attempt != nil {
		t.Fatalf("finished attempt still controls inbox: %+v %v", control, err)
	}
	want.Delivery.LeaseID, want.Delivery.LeaseUntil = "", nil
	before := attemptStatusState(t, receiver, ref.AgentID, id, want.Delivery.DeliveryID)
	for range 2 {
		got, err := receiver.SessionInboxAttemptStatus(ctx, SessionInboxAttemptQuery{ref, id}, dest)
		if err != nil || !reflect.DeepEqual(got, want) {
			t.Fatalf("finished cancellation: got %+v, want %+v: %v", got, want, err)
		}
	}
	if after := attemptStatusState(t, receiver, ref.AgentID, id, want.Delivery.DeliveryID); after != before {
		t.Fatalf("finished read changed retained state: before %s after %s", before, after)
	}
}

func TestSessionInboxAttemptStatusRetainsCompletionResult(t *testing.T) {
	ctx := context.Background()
	f := nativeRecoveryFixture(t)
	delivery, err := f.complete(uuid.NewString())
	if err != nil || delivery.Result == nil {
		t.Fatalf("completion: %+v %v", delivery, err)
	}
	want, err := f.receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: f.ref, AttemptID: f.attempt, Reason: "delivery_completed"}, f.dest)
	if err != nil || want.FinishedAt == nil || want.Reason != "delivery_completed" || !reflect.DeepEqual(want.Delivery.Result, delivery.Result) {
		t.Fatalf("finished result fixture: %+v %v", want, err)
	}
	want.Delivery.LeaseID, want.Delivery.LeaseUntil = "", nil
	before := attemptStatusState(t, f.receiver, f.ref.AgentID, f.attempt, f.delivery)
	got, err := f.receiver.SessionInboxAttemptStatus(ctx, SessionInboxAttemptQuery{f.ref, f.attempt}, f.dest)
	if err != nil || !reflect.DeepEqual(got, want) {
		t.Fatalf("finished result: got %+v, want %+v: %v", got, want, err)
	}
	if after := attemptStatusState(t, f.receiver, f.ref.AgentID, f.attempt, f.delivery); after != before {
		t.Fatalf("result read changed retained state: before %s after %s", before, after)
	}
}

func TestSessionInboxAttemptStatusRejectsForeignAssociations(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	a, _, claim := f.claimed(nativeAgent{Binding: "status-owner"}, "request", "turn-one", true)
	ref := sessionRef(a)
	other := f.register(nativeAgent{Binding: "other-agent"})
	foreign := testStore(t, Channel{Principal: "foreign-" + f.repo, Repo: f.repo})
	foreignAgent := f.register(nativeAgent{Binding: "foreign-agent", Store: foreign})
	wrongRepo := testStore(t, Channel{Principal: f.receiver.channel.Principal, Repo: uuid.NewString()})
	instrumented := testStore(t, Channel{Principal: f.receiver.channel.Principal, Repo: f.repo, Instrumented: true})
	for _, tc := range []struct {
		name  string
		store *Store
		ref   AgentSessionRef
		id    string
		code  string
	}{
		{"foreign-profile", foreign, ref, claim.Attempt.ID, "NOT_FOUND"},
		{"same-owner-other-agent", f.receiver, sessionRef(other), claim.Attempt.ID, "NOT_FOUND"},
		{"other-owner-own-agent", foreign, sessionRef(foreignAgent), claim.Attempt.ID, "NOT_FOUND"},
		{"wrong-repository", wrongRepo, ref, claim.Attempt.ID, "NOT_FOUND"},
		{"session-view", f.view(ref), ref, claim.Attempt.ID, "INVALID_REQUEST"},
		{"instrumented-profile", instrumented, ref, claim.Attempt.ID, "INVALID_REQUEST"},
		{"missing-attempt", f.receiver, ref, uuid.NewString(), "NOT_FOUND"},
		{"invalid-attempt", f.receiver, ref, "not-a-uuid", "INVALID_REQUEST"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			before := attemptStatusState(t, f.receiver, ref.AgentID, claim.Attempt.ID, claim.Attempt.Delivery.DeliveryID)
			got, err := tc.store.SessionInboxAttemptStatus(ctx, SessionInboxAttemptQuery{tc.ref, tc.id}, f.dest)
			requireCode(t, err, tc.code)
			if !reflect.DeepEqual(got, SessionInboxAttempt{}) {
				t.Fatalf("refusal disclosed a partial attempt: %+v", got)
			}
			if after := attemptStatusState(t, f.receiver, ref.AgentID, claim.Attempt.ID, claim.Attempt.Delivery.DeliveryID); after != before {
				t.Fatalf("refused read changed retained state: before %s after %s", before, after)
			}
		})
	}
}

func TestSessionInboxAttemptStatusRespectsDestinationPrivacy(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	a := f.register(nativeAgent{Binding: "hosted-status"})
	ref := sessionRef(a)
	local := Destination{Name: "local", AllowLocal: true}
	draft := projectNote(f.repo)
	draft.Sensitivity, draft.Body = "local", "PRIVATE-ATTEMPT-SOURCE"
	private, err := f.sender.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	_, err = f.sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{private.RecordID, private.Version}, Destination: EventDestination{Type: "agent", Name: a.Inbox}}, local)
	if err != nil {
		t.Fatal(err)
	}
	claim, err := f.receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}, local)
	if err != nil || claim.Attempt == nil {
		t.Fatalf("private attempt fixture: %+v %v", claim, err)
	}
	query := SessionInboxAttemptQuery{ref, claim.Attempt.ID}
	before := attemptStatusState(t, f.receiver, ref.AgentID, query.AttemptID, claim.Attempt.Delivery.DeliveryID)
	got, err := f.receiver.SessionInboxAttemptStatus(ctx, query, f.dest)
	requireCode(t, err, "NOT_FOUND")
	if !reflect.DeepEqual(got, SessionInboxAttempt{}) {
		t.Fatalf("hosted read exposed a local attempt: %+v", got)
	}
	got, err = f.receiver.SessionInboxAttemptStatus(ctx, query, local)
	if err != nil || got.Delivery.Event.Ref.RecordID != private.RecordID || got.Delivery.LeaseID != "" || got.Delivery.LeaseUntil != nil {
		t.Fatalf("authorized local read: %+v %v", got, err)
	}
	if after := attemptStatusState(t, f.receiver, ref.AgentID, query.AttemptID, claim.Attempt.Delivery.DeliveryID); after != before {
		t.Fatalf("privacy read changed retained state: before %s after %s", before, after)
	}
	// A local-only registration remains invisible even when its attempt's
	// event itself is shareable.
	b := f.register(nativeAgent{Binding: "local-status", Dest: &local})
	_, delivery := f.publish("request", b.Inbox)
	claim, err = f.receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: sessionRef(b), DeliveryID: delivery, NativeTurnID: "local-turn"}, local)
	if err != nil || claim.Attempt == nil {
		t.Fatalf("local session fixture: %+v %v", claim, err)
	}
	_, err = f.receiver.SessionInboxAttemptStatus(ctx, SessionInboxAttemptQuery{sessionRef(b), claim.Attempt.ID}, f.dest)
	requireCode(t, err, "NOT_FOUND")
}

// Snapshot the actual targeted rows, including the unfinished-attempt hold,
// lease, session presence and poll identities. Reading must not refresh any of
// them or convert an unknown attempt into an admission/settlement decision.
func attemptStatusState(t *testing.T, s *Store, agentID, attemptID, deliveryID string) string {
	t.Helper()
	var out string
	err := s.pool.QueryRow(context.Background(), `SELECT jsonb_build_object(
		'session',(SELECT to_jsonb(s) FROM cairn.agent_session s WHERE agent_id=$1),
		'attempts',(SELECT jsonb_agg(to_jsonb(a) ORDER BY attempt_id) FROM cairn.agent_session_attempt a WHERE agent_id=$1),
		'polls',(SELECT jsonb_agg(to_jsonb(p) ORDER BY request_id) FROM cairn.agent_session_poll p WHERE agent_id=$1),
		'tools',(SELECT jsonb_agg(to_jsonb(t) ORDER BY item_id) FROM cairn.agent_session_tool t WHERE attempt_id=$2),
		'delivery',(SELECT to_jsonb(d) FROM cairn.agent_delivery d WHERE delivery_id=$3))::text`, agentID, attemptID, deliveryID).Scan(&out)
	if err != nil {
		t.Fatal(err)
	}
	return out
}
