package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

// Stranded deliveries to an explicitly stopped session are failed only by an
// explicit operator decision, after review, and never while the consumer could
// still be live or a host still holds the delivery.

type stoppedInboxFixture struct {
	*nativeFixture
	op *Store
}

func newStoppedInboxFixture(t *testing.T) stoppedInboxFixture {
	t.Helper()
	f := newNativeFixture(t)
	return stoppedInboxFixture{f, f.operator()}
}

// stopAgo records an explicit stop and backdates it by the given interval.

func (f stoppedInboxFixture) expire(agentIDs ...string) (StoppedInboxExpiry, error) {
	return f.op.ExpireStoppedInboxes(context.Background(), StoppedInboxExpiryRequest{RequestID: uuid.NewString(), Repo: f.repo, AgentIDs: agentIDs,
		Reason: "Reviewed dry run; the stopped session will not return"})
}

func (f stoppedInboxFixture) dryRun(t *testing.T, agentIDs ...string) StoppedInboxExpiry {
	t.Helper()
	out, err := f.op.ExpireStoppedInboxes(context.Background(), StoppedInboxExpiryRequest{Repo: f.repo, DryRun: true, AgentIDs: agentIDs})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func TestStoppedInboxExpiryFailsReviewedDeliveriesVisiblyAndAllowsReissue(t *testing.T) {
	ctx := context.Background()
	f := newStoppedInboxFixture(t)
	a := f.register(nativeAgent{Binding: "inbox-expiry", Native: "stopped-long-ago", Harness: "claude"})
	request, requestDelivery := f.publish("request", a.Inbox)
	_, noticeDelivery := f.publish("notice", a.Inbox)
	f.stopAgo(a, "2 days")

	plan := f.dryRun(t)
	if !plan.DryRun || plan.Expired != 2 || len(plan.Sessions) != 1 {
		t.Fatalf("dry run: %+v", plan)
	}
	s := plan.Sessions[0]
	if s.AgentID != a.AgentID || s.DisplayName != a.DisplayName || s.Requests != 1 || s.Notices != 1 || len(s.Deliveries) != 2 ||
		s.Deliveries[0].DeliveryID != requestDelivery || s.Deliveries[1].DeliveryID != noticeDelivery || s.Deliveries[0].Execution != "never_launched" {
		t.Fatalf("dry-run session: %+v", s)
	}
	// A dry run changes nothing.
	st, err := f.sender.AgentEventStatus(ctx, EventStatusRequest{EventID: request.EventID}, f.dest)
	if err != nil || st.Deliveries[0].State != "pending" {
		t.Fatalf("dry run mutated: %+v %v", st, err)
	}

	applied, err := f.expire(a.AgentID)
	if err != nil || applied.DryRun || applied.Expired != 2 || len(applied.Sessions) != 1 || len(applied.Sessions[0].Deliveries) != 2 {
		t.Fatalf("apply: %+v %v", applied, err)
	}
	// The publisher sees a distinct failure code and the operator's control.
	st, err = f.sender.AgentEventStatus(ctx, EventStatusRequest{EventID: request.EventID}, f.dest)
	if err != nil || st.Deliveries[0].State != "failed" || st.Deliveries[0].Code != "consumer_retired" || st.Deliveries[0].Control == nil ||
		st.Deliveries[0].Control.By != f.op.channel.Principal {
		t.Fatalf("publisher status: %+v %v", st, err)
	}
	// Reissue stays the explicit recovery path.
	if _, err = f.op.ReissueEvent(ctx, ReissueEventRequest{RequestID: uuid.NewString(), Repo: f.repo, DeliveryID: requestDelivery,
		Reason: "Reviewed the retired consumer; repeat the request", AcceptUncertainEffects: true}); err != nil {
		t.Fatal(err)
	}
}

func TestStoppedInboxExpiryLeavesPossiblyLiveAndNonSessionConsumers(t *testing.T) {
	ctx := context.Background()
	f := newStoppedInboxFixture(t)
	live := f.register(nativeAgent{Binding: "inbox-expiry", Native: "live", Harness: "claude"})
	offline := f.register(nativeAgent{Binding: "inbox-expiry", Native: "offline-long-ago", Harness: "claude"})
	recent := f.register(nativeAgent{Binding: "inbox-expiry", Native: "stopped-recently", Harness: "claude"})
	_, liveDelivery := f.publish("notice", live.Inbox)
	_, offlineDelivery := f.publish("request", offline.Inbox)
	_, recentDelivery := f.publish("notice", recent.Inbox)
	// A profile inbox that is not a registered session keeps its history.
	_, legacyDelivery := f.publish("response", f.receiver.channel.Principal)
	// Presence that merely expired long ago does not prove the agent stopped.
	f.lapsePresence(offline.AgentID, "30 days")
	f.stopAgo(recent, "10 minutes")

	plan := f.dryRun(t)
	if plan.Expired != 0 || len(plan.Sessions) != 0 {
		t.Fatalf("nothing should be eligible: %+v", plan)
	}
	classes := map[string]int{}
	for _, c := range plan.Classification {
		classes[c.Class] = c.Total
	}
	want := map[string]int{"session_live": 1, "session_offline": 1, "session_stopped_recently": 1, "legacy_inbox": 1}
	for k, v := range want {
		if classes[k] != v {
			t.Fatalf("classification %s: want %d got %+v", k, v, plan.Classification)
		}
	}

	applied, err := f.expire(live.AgentID, offline.AgentID, recent.AgentID, uuid.NewString())
	if err != nil || applied.Expired != 0 {
		t.Fatalf("apply: %+v %v", applied, err)
	}
	status := map[string]string{}
	for _, s := range applied.Sessions {
		status[s.AgentID] = s.Status
	}
	if status[live.AgentID] != "not_stopped" || status[offline.AgentID] != "not_stopped" || status[recent.AgentID] != "stopped_recently" || len(status) != 4 {
		t.Fatalf("statuses: %+v", applied.Sessions)
	}
	for _, id := range []string{liveDelivery, offlineDelivery, recentDelivery, legacyDelivery} {
		var state string
		if err := f.op.pool.QueryRow(ctx, `SELECT state FROM cairn.agent_delivery WHERE delivery_id=$1`, id).Scan(&state); err != nil || state != "pending" {
			t.Fatalf("delivery %s changed: %s %v", id, state, err)
		}
	}
}

func (f stoppedInboxFixture) state(t *testing.T, delivery string) string {
	t.Helper()
	var state string
	if err := f.op.pool.QueryRow(context.Background(), `SELECT state FROM cairn.agent_delivery WHERE delivery_id=$1`, delivery).Scan(&state); err != nil {
		t.Fatal(err)
	}
	return state
}

func TestStoppedInboxExpiryRetainsHeldAndTopicDeliveries(t *testing.T) {
	ctx := context.Background()
	f := newStoppedInboxFixture(t)
	a := f.register(nativeAgent{Binding: "inbox-expiry", Native: "stopped-with-hold", Harness: "claude"})
	ref := AgentSessionRef{a.AgentID, a.ExecutionID}
	view, err := f.receiver.ForAgentSession(ref, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	_, heldDelivery := f.publish("request", a.Inbox)
	claim, err := f.receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}, f.dest)
	if err != nil || claim.Attempt == nil || claim.Attempt.Delivery.DeliveryID != heldDelivery {
		t.Fatalf("claim: %+v %v", claim, err)
	}
	if _, err = view.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: "retired-review", Active: true}); err != nil {
		t.Fatal(err)
	}
	topic, err := f.sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{f.source.RecordID, f.source.Version}, Destination: EventDestination{"topic", "retired-review"}}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	_, freeDelivery := f.publish("notice", a.Inbox)
	f.stopAgo(a, "3 days")

	plan := f.dryRun(t, a.AgentID)
	if plan.Expired != 1 || len(plan.Sessions) != 1 || plan.Sessions[0].Retained.Held != 1 || plan.Sessions[0].Deliveries[0].DeliveryID != freeDelivery {
		t.Fatalf("plan: %+v", plan)
	}
	topicClass := false
	for _, c := range plan.Classification {
		topicClass = topicClass || (c.Class == "topic_subscriber" && c.Total == 1)
	}
	if !topicClass {
		t.Fatalf("topic delivery not classified: %+v", plan.Classification)
	}
	applied, err := f.expire(a.AgentID)
	if err != nil || applied.Expired != 1 || applied.Sessions[0].Status != "expired" {
		t.Fatalf("apply: %+v %v", applied, err)
	}
	st, err := f.sender.AgentEventStatus(ctx, EventStatusRequest{EventID: topic.EventID}, f.dest)
	if err != nil || len(st.Deliveries) != 1 || st.Deliveries[0].State != "pending" {
		t.Fatalf("topic delivery changed: %+v %v", st, err)
	}
	if got := f.state(t, heldDelivery); got != "leased" && got != "pending" {
		t.Fatalf("held delivery changed: %s", got)
	}
	if got := f.state(t, freeDelivery); got != "failed" {
		t.Fatalf("free delivery: %s", got)
	}
}

func TestStoppedInboxExpiryRetainsLeasedAndSuccessorGrantedDeliveries(t *testing.T) {
	ctx := context.Background()
	f := newStoppedInboxFixture(t)
	a := f.register(nativeAgent{Binding: "inbox-expiry", Native: "stopped-with-lease", Harness: "claude"})
	ref := AgentSessionRef{a.AgentID, a.ExecutionID}
	view, err := f.receiver.ForAgentSession(ref, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	_, leasedDelivery := f.publish("notice", a.Inbox)
	next, err := view.NextEvent(ctx, NextEventRequest{}, f.dest)
	if err != nil || next.Delivery == nil || next.Delivery.DeliveryID != leasedDelivery {
		t.Fatalf("lease: %+v %v", next, err)
	}
	granted, grantedDelivery := f.publish("request", a.Inbox)
	_, freeDelivery := f.publish("response", a.Inbox)
	successor := f.register(nativeAgent{Binding: "inbox-expiry", Native: "successor", Harness: "claude"})
	f.stopAgo(a, "3 days")
	if _, err = f.op.AuthorizeSessionSuccessor(ctx, AuthorizeSessionSuccessorRequest{RequestID: uuid.NewString(), Repo: f.repo, PredecessorAgentID: a.AgentID,
		Successor: AgentSessionRef{successor.AgentID, successor.ExecutionID}, EventIDs: []string{granted.EventID}, Reason: "Successor continues the granted request"}); err != nil {
		t.Fatal(err)
	}

	applied, err := f.expire(a.AgentID)
	if err != nil || applied.Expired != 1 || applied.Sessions[0].Retained != (StoppedInboxRetained{Leased: 1, SuccessorGrant: 1}) ||
		applied.Sessions[0].Responses != 1 || applied.Sessions[0].Deliveries[0].DeliveryID != freeDelivery {
		t.Fatalf("apply: %+v %v", applied, err)
	}
	if f.state(t, leasedDelivery) != "leased" || f.state(t, grantedDelivery) != "pending" || f.state(t, freeDelivery) != "failed" {
		t.Fatal("retained deliveries changed")
	}
}

func TestStoppedInboxExpiryAuthorityValidationReplayAndResume(t *testing.T) {
	ctx := context.Background()
	f := newStoppedInboxFixture(t)
	a := f.register(nativeAgent{Binding: "inbox-expiry", Native: "resumable", Harness: "claude"})
	_, first := f.publish("notice", a.Inbox)
	f.stopAgo(a, "2 days")
	reason := "Reviewed dry run; the stopped session will not return"

	_, err := f.receiver.ExpireStoppedInboxes(ctx, StoppedInboxExpiryRequest{Repo: f.repo, DryRun: true})
	requireCode(t, err, "AUTHORITY_DENIED")
	for _, bad := range []StoppedInboxExpiryRequest{
		{RequestID: uuid.NewString(), Repo: f.repo, Reason: reason},                                           // no reviewed sessions
		{RequestID: uuid.NewString(), Repo: f.repo, AgentIDs: []string{a.AgentID}},                            // no reason
		{Repo: f.repo, AgentIDs: []string{a.AgentID}, Reason: reason},                                         // no request ID
		{Repo: f.repo, DryRun: true, RequestID: uuid.NewString()},                                             // dry run takes no request ID
		{Repo: f.repo, DryRun: true, StoppedForSeconds: 60},                                                   // below the minimum
		{Repo: f.repo, DryRun: true, AgentIDs: []string{a.AgentID, a.AgentID}},                                // duplicate
		{RequestID: uuid.NewString(), Repo: f.repo, AgentIDs: []string{"agent/" + a.AgentID}, Reason: reason}, // not a UUID
	} {
		_, err = f.op.ExpireStoppedInboxes(ctx, bad)
		requireCode(t, err, "INVALID_REQUEST")
	}
	if f.state(t, first) != "pending" {
		t.Fatal("refused request changed state")
	}

	req := StoppedInboxExpiryRequest{RequestID: uuid.NewString(), Repo: f.repo, AgentIDs: []string{a.AgentID}, Reason: reason}
	applied, err := f.op.ExpireStoppedInboxes(ctx, req)
	if err != nil || applied.Expired != 1 {
		t.Fatalf("apply: %+v %v", applied, err)
	}
	again, err := f.op.ExpireStoppedInboxes(ctx, req)
	if err != nil || again.Expired != 1 || len(again.Sessions) != 1 || again.Sessions[0].Deliveries[0].DeliveryID != first {
		t.Fatalf("replay: %+v %v", again, err)
	}

	// The conversation can still resume. Its failed delivery stays failed and
	// is not replayed; new mail flows normally, and a resumed session is not
	// eligible for expiry.
	resumed := f.register(nativeAgent{Binding: "inbox-expiry", Native: "resumable", Harness: "claude"})
	if resumed.AgentID != a.AgentID {
		t.Fatalf("resume changed identity: %+v", resumed)
	}
	_, second := f.publish("notice", a.Inbox)
	next, err := f.receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: AgentSessionRef{resumed.AgentID, resumed.ExecutionID}}, f.dest)
	if err != nil || next.Attempt == nil || next.Attempt.Delivery.DeliveryID != second {
		t.Fatalf("resumed claim: %+v %v", next, err)
	}
	if f.state(t, first) != "failed" {
		t.Fatal("expired delivery was revived")
	}
	later, err := f.expire(a.AgentID)
	if err != nil || later.Expired != 0 || later.Sessions[0].Status != "not_stopped" {
		t.Fatalf("resumed session expiry: %+v %v", later, err)
	}
}

func TestStoppedInboxExpiryRacesResumeConsistently(t *testing.T) {
	f := newStoppedInboxFixture(t)
	for i := 0; i < 8; i++ {
		a := f.register(nativeAgent{Binding: "inbox-expiry", Native: "race-" + uuid.NewString(), Harness: "claude"})
		_, delivery := f.publish("notice", a.Inbox)
		f.stopAgo(a, "2 days")
		var applied StoppedInboxExpiry
		var applyErr, resumeErr error
		done := make(chan struct{})
		go func() {
			defer close(done)
			applied, applyErr = f.expire(a.AgentID)
		}()
		_, resumeErr = f.receiver.RegisterAgent(context.Background(), RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "inbox-expiry", NativeSessionID: a.NativeSessionID,
			Metadata: existingSessionMetadata("claude", "idle")}, f.dest)
		<-done
		if applyErr != nil || resumeErr != nil {
			t.Fatalf("race: %v %v", applyErr, resumeErr)
		}
		// Either the expiry saw the stopped session and failed its delivery, or
		// the resume committed first and the delivery stayed pending.
		want := map[string]string{"expired": "failed", "not_stopped": "pending"}[applied.Sessions[0].Status]
		if got := f.state(t, delivery); want == "" || got != want {
			t.Fatalf("status %s with delivery %s", applied.Sessions[0].Status, got)
		}
	}
}
