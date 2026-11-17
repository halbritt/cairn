package core

import (
	"context"
	"reflect"
	"testing"

	"github.com/google/uuid"
)

func TestSessionQuiescenceOwnInboxAcrossExecutionsAndStoppedReadIsPure(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	a := f.register(nativeAgent{Binding: "native-a", Harness: "claude"})
	b := f.register(nativeAgent{Binding: "native-b", Harness: "claude"})
	_, delivery := f.publish("request", b.Inbox)
	claim := f.claim(sessionRef(b), delivery, "native-turn-b", true)
	before := attemptStatusState(t, f.receiver, b.AgentID, claim.Attempt.ID, delivery)
	if _, err := f.receiver.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET lease_until=clock_timestamp()-interval '1 second' WHERE delivery_id=$1`, delivery); err != nil {
		t.Fatal(err)
	}
	before = attemptStatusState(t, f.receiver, b.AgentID, claim.Attempt.ID, delivery)
	quiet, err := f.receiver.ReadSessionQuiescence(ctx, SessionQuiescenceQuery{Session: sessionRef(a)}, f.dest)
	if err != nil || !quiet.Complete || quiet.Hold != nil || len(quiet.Leases) != 0 || len(quiet.Wakes) != 0 || quiet.NativeSessionID != a.NativeSessionID || quiet.Binding != "native-a" {
		t.Fatalf("other inbox custody contaminated exact scope: %+v %v", quiet, err)
	}
	busy, err := f.receiver.ReadSessionQuiescence(ctx, SessionQuiescenceQuery{Session: sessionRef(b)}, f.dest)
	if err != nil || !busy.Complete || busy.Hold == nil || busy.Hold.AttemptID != claim.Attempt.ID || busy.Hold.NativeTurnID != "native-turn-b" || len(busy.Leases) != 1 || busy.Leases[0].DeliveryID != delivery {
		t.Fatalf("active custody disappeared: %+v %v", busy, err)
	}
	if after := attemptStatusState(t, f.receiver, b.AgentID, claim.Attempt.ID, delivery); after != before {
		t.Fatal("quiescence read modified retained state")
	}
	_, err = f.receiver.RegisterAgent(ctx, f.registration(nativeAgent{Binding: "native-b", Harness: "claude"}), f.dest)
	requireCode(t, err, "AGENT_BUSY")
	if _, err := f.receiver.LeaveAgent(ctx, sessionRef(b), f.dest); err != nil {
		t.Fatal(err)
	}
	stopped, err := f.receiver.ReadSessionQuiescence(ctx, SessionQuiescenceQuery{Session: sessionRef(b)}, f.dest)
	if err != nil || !stopped.Stopped || stopped.Hold == nil || stopped.Hold.Session != sessionRef(b) || len(stopped.Leases) != 1 {
		t.Fatalf("stopped registration erased custody: %+v %v", stopped, err)
	}
}

func TestSessionQuiescenceOverLimitRefusesInsteadOfTruncatingCustody(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	a := f.register(nativeAgent{Binding: "bounded-read", Harness: "claude"})
	view := f.view(sessionRef(a))
	for i := 0; i < 129; i++ {
		_, delivery := f.publish("notice", a.Inbox)
		leased, err := view.NextEvent(ctx, NextEventRequest{}, f.dest)
		if err != nil || leased.Delivery == nil || leased.Delivery.DeliveryID != delivery {
			t.Fatal(leased, err)
		}
	}
	got, err := f.receiver.ReadSessionQuiescence(ctx, SessionQuiescenceQuery{Session: sessionRef(a)}, f.dest)
	requireCode(t, err, "INVALID_REQUEST")
	if !reflect.DeepEqual(got, SessionQuiescence{}) {
		t.Fatal("partial over-limit observation escaped", got)
	}
	var count int
	if err := f.receiver.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_delivery WHERE consumer=$1 AND state='leased'`, a.Inbox).Scan(&count); err != nil || count != 129 {
		t.Fatal("bounded read changed custody", count, err)
	}
}

func TestSessionQuiescenceIncludesAssociatedWorkerCustodyAndRetainsGlobalWakeRead(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	a := f.register(nativeAgent{Binding: "worker-native", Store: f.sender, Mode: "fresh-worker", Harness: "claude"})
	publishFixture(t, f.sender, f.receiver, f.source, f.dest)
	wake, err := f.receiver.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString()}, f.dest)
	if err != nil || wake.Attempt == nil {
		t.Fatal(wake, err)
	}
	for _, op := range []string{"start", "enter"} {
		if _, err := f.receiver.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: wake.Attempt.ID, Operation: op}, f.dest); err != nil {
			t.Fatal(err)
		}
	}
	ref := sessionRef(a)
	if _, err := f.receiver.ChangeWake(ctx, WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: wake.Attempt.ID, Operation: "session", Session: &ref}, f.dest); err != nil {
		t.Fatal(err)
	}
	metadata := a.Metadata
	metadata.DeliveryMode = "existing-session"
	current, err := f.sender.UpdateAgent(ctx, UpdateAgentRequest{RequestID: uuid.NewString(), Session: ref,
		ExpectedRevision: a.ContextRevision, Metadata: metadata}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	read, err := f.sender.ReadSessionQuiescence(ctx, SessionQuiescenceQuery{Session: sessionRef(current)}, f.dest)
	if err != nil || len(read.Wakes) != 1 || read.Wakes[0].AttemptID != wake.Attempt.ID || read.Wakes[0].Session != ref || len(read.Leases) != 1 {
		t.Fatalf("worker-slot custody escaped the exact native scope: %+v %v", read, err)
	}
	global, err := f.receiver.WakeAttempts(ctx, WakeQuery{Repo: f.repo, Active: true}, f.dest)
	if err != nil || len(global.Attempts) != 1 || global.Attempts[0].ID != wake.Attempt.ID {
		t.Fatalf("existing repository/consumer wake read changed: %+v %v", global, err)
	}
}

func TestSessionQuiescenceAuthenticationAndRestoreBoundaries(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	a := f.register(nativeAgent{Binding: "native-read", Harness: "claude"})
	ref := sessionRef(a)
	foreign := testStore(t, Channel{Principal: "foreign-" + f.repo, Repo: f.repo})
	wrongRepo := testStore(t, Channel{Principal: f.receiver.channel.Principal, Repo: uuid.NewString()})
	instrumented := testStore(t, Channel{Principal: f.receiver.channel.Principal, Repo: f.repo, Instrumented: true})
	for _, tc := range []struct {
		name  string
		store *Store
		ref   AgentSessionRef
		code  string
	}{
		{"foreign", foreign, ref, "NOT_FOUND"},
		{"wrong-repo", wrongRepo, ref, "NOT_FOUND"},
		{"instrumented", instrumented, ref, "INVALID_REQUEST"},
		{"session-view", f.view(ref), ref, "INVALID_REQUEST"},
		{"wrong-execution", f.receiver, AgentSessionRef{ref.AgentID, uuid.NewString()}, "STALE_SESSION"},
		{"invalid-ref", f.receiver, AgentSessionRef{}, "INVALID_REQUEST"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := tc.store.ReadSessionQuiescence(ctx, SessionQuiescenceQuery{Session: tc.ref}, f.dest)
			requireCode(t, err, tc.code)
			if !reflect.DeepEqual(got, SessionQuiescence{}) {
				t.Fatal("refused read disclosed selected state", got)
			}
		})
	}
	if _, err := f.operator().FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "disposable quiescence read boundary"}); err != nil {
		t.Fatal(err)
	}
	_, err := f.receiver.ReadSessionQuiescence(ctx, SessionQuiescenceQuery{Session: ref}, f.dest)
	requireCode(t, err, "STALE_SESSION")
}

func TestSessionQuiescenceChecksDurablePollAfterStop(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	a := f.register(nativeAgent{Binding: "poll-read", Harness: "claude"})
	ref, pollID := sessionRef(a), uuid.NewString()
	polled, err := f.receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: pollID, Session: ref}, f.dest)
	if err != nil || polled.Attempt != nil {
		t.Fatal(polled, err)
	}
	if _, err := f.receiver.LeaveAgent(ctx, ref, f.dest); err != nil {
		t.Fatal(err)
	}
	read, err := f.receiver.ReadSessionQuiescence(ctx, SessionQuiescenceQuery{Session: ref, PollRequestID: pollID}, f.dest)
	if err != nil || !read.Stopped || read.Poll == nil || !read.PollTerminal || read.Poll.AttemptID != nil || read.Poll.RequestID != pollID || read.Poll.Session != ref {
		t.Fatalf("stopped empty poll lost terminal correspondence: %+v %v", read, err)
	}
	for _, id := range []string{uuid.NewString(), "invalid"} {
		if got, err := f.receiver.ReadSessionQuiescence(ctx, SessionQuiescenceQuery{Session: ref, PollRequestID: id}, f.dest); err == nil || got.Complete {
			t.Fatal("unknown policy certified", got, err)
		}
	}
	b, _, claim := f.claimed(nativeAgent{Binding: "held-poll", Harness: "claude"}, "request", "turn", true)
	read, err = f.receiver.ReadSessionQuiescence(ctx, SessionQuiescenceQuery{Session: sessionRef(b), PollRequestID: claim.Attempt.ID}, f.dest)
	if err != nil || read.Poll == nil || read.PollTerminal || read.Hold == nil {
		t.Fatal("held poll became terminal", read, err)
	}
}
