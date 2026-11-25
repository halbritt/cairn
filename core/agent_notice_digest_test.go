package core

import (
	"context"
	"fmt"
	"testing"

	"github.com/google/uuid"
)

func TestNoticeDigestTwentyRoutineNoticesFromFourSendersOneTurn(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	receiver, source, dest := f.receiver, f.source, f.dest
	coord := f.register(nativeAgent{Binding: "digest-20-coord"})
	sess := AgentSessionRef{AgentID: coord.AgentID, ExecutionID: coord.ExecutionID}
	ref := RecordVersionRef{RecordID: source.RecordID, Version: source.Version}
	toCoord := EventDestination{Type: "agent", Name: coord.Inbox}

	senders := make([]*Store, 4)
	for i := range senders {
		senders[i] = testStore(t, Channel{Principal: fmt.Sprintf("digest-sender-%d-%s", i+1, f.repo), Repo: f.repo})
	}
	eventIDs := make([]string, 0, 20)
	for round := 0; round < 5; round++ {
		for sIdx, s := range senders {
			action := ""
			if (round+sIdx)%2 == 1 {
				action = "review"
			}
			ev, err := s.PublishEvent(ctx, PublishEventRequest{
				RequestID: uuid.NewString(), Kind: "notice", Ref: ref, Destination: toCoord, Action: action,
			}, dest)
			if err != nil {
				t.Fatal(err)
			}
			eventIDs = append(eventIDs, ev.EventID)
		}
	}

	// Before the 300s digest window expires, routine and review notices are held.
	readyHeld, err := receiver.SessionInboxDigestReady(ctx, SessionInboxDigestReadyRequest{
		Session: sess, DigestWindowSeconds: 300,
	}, dest)
	if err != nil || readyHeld.DeliveryID != "" {
		t.Fatalf("expected held digest before timer, got %+v err=%v", readyHeld, err)
	}
	claimHeld, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{
		RequestID: uuid.NewString(), Session: sess, NativeTurnID: "turn-early", DigestWindowSeconds: 300,
	}, dest)
	if err != nil || claimHeld.Attempt != nil {
		t.Fatalf("expected nil claim while digest window is open, got %+v err=%v", claimHeld, err)
	}

	// Age oldest notice past the 300s window so the timer fires.
	if _, err := receiver.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET available_at=clock_timestamp()-interval '301 seconds' WHERE consumer=$1`, coord.Inbox); err != nil {
		t.Fatal(err)
	}
	readyDue, err := receiver.SessionInboxDigestReady(ctx, SessionInboxDigestReadyRequest{
		Session: sess, DigestWindowSeconds: 300,
	}, dest)
	if err != nil || !readyDue.Digest || readyDue.Count != 20 || readyDue.DeliveryID == "" {
		t.Fatalf("expected due 20-item digest, got %+v err=%v", readyDue, err)
	}

	claimed, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{
		RequestID: uuid.NewString(), Session: sess, DeliveryID: readyDue.DeliveryID,
		NativeTurnID: "turn-digest-1", DigestWindowSeconds: 300,
	}, dest)
	if err != nil || claimed.Attempt == nil {
		t.Fatalf("expected claimed digest attempt, got %+v err=%v", claimed, err)
	}
	att := *claimed.Attempt
	if att.AdmissionKind != "digest" || len(att.DigestItems) != 20 || att.DigestSupersededCount != 0 {
		t.Fatalf("unexpected digest attempt shape: kind=%s len=%d sup=%d", att.AdmissionKind, len(att.DigestItems), att.DigestSupersededCount)
	}
	if att.Delivery.DeliveryID != readyDue.DeliveryID || att.DigestItems[0].Delivery.DeliveryID != readyDue.DeliveryID {
		t.Fatalf("primary delivery mismatch: %s vs %s", att.Delivery.DeliveryID, readyDue.DeliveryID)
	}

	outcomes := make([]SessionDigestItemOutcome, 0, 20)
	expectedState := make(map[string][2]string, 20)
	for i, item := range att.DigestItems {
		if item.Ordinal != i+1 || item.Delivery.Event.EventID != eventIDs[i] {
			t.Fatalf("item %d order mismatch: ord=%d ev=%s want=%s", i, item.Ordinal, item.Delivery.Event.EventID, eventIDs[i])
		}
		disp, code := "handled", ""
		switch i {
		case 3, 14:
			disp, code = "ignored", "unsupported_kind"
		case 7:
			disp, code = "failed", "processing_failed"
		case 18:
			disp, code = "failed", "source_unavailable"
		}
		expectedState[item.Delivery.DeliveryID] = [2]string{disp, code}
		outcomes = append(outcomes, SessionDigestItemOutcome{
			DeliveryID: item.Delivery.DeliveryID, LeaseID: item.Delivery.LeaseID,
			Disposition: disp, Code: code,
		})
	}

	// Settle first 5 items, renew remaining 15, then settle all 20 idempotently.
	partReq := uuid.NewString()
	partRes, err := receiver.SettleSessionDigest(ctx, SessionInboxDigestSettle{
		RequestID: partReq, Session: sess, AttemptID: att.ID, Outcomes: outcomes[:5],
	}, dest)
	if err != nil || partRes.Remaining != 15 || len(partRes.Items) != 5 {
		t.Fatalf("partial settle failed: %+v err=%v", partRes, err)
	}
	renewed, err := receiver.RenewSessionDigest(ctx, SessionInboxDigestRenew{
		Session: sess, AttemptID: att.ID, LeaseSeconds: 90,
	}, dest)
	if err != nil || len(renewed.DigestItems) != 20 {
		t.Fatalf("digest renew failed: %+v err=%v", renewed, err)
	}
	fullReq := uuid.NewString()
	fullRes, err := receiver.SettleSessionDigest(ctx, SessionInboxDigestSettle{
		RequestID: fullReq, Session: sess, AttemptID: att.ID, Outcomes: outcomes,
	}, dest)
	if err != nil || fullRes.Remaining != 0 || len(fullRes.Items) != 20 || !fullRes.Items[0].AlreadySettled || fullRes.Items[5].AlreadySettled {
		t.Fatalf("full digest settle mismatch: %+v err=%v", fullRes, err)
	}
	reconciled, err := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{
		RequestID: uuid.NewString(), Session: sess, AttemptID: att.ID, Reason: "delivery_completed",
	}, dest)
	if err != nil || reconciled.FinishedAt == nil || reconciled.Reason != "delivery_completed" {
		t.Fatalf("reconcile delivery_completed failed: %+v err=%v", reconciled, err)
	}

	for id, want := range expectedState {
		var gotState, gotCode string
		if err := receiver.pool.QueryRow(ctx, `SELECT state,COALESCE(code,'') FROM cairn.agent_delivery WHERE delivery_id=$1`, id).Scan(&gotState, &gotCode); err != nil {
			t.Fatal(err)
		}
		if gotState != want[0] || gotCode != want[1] {
			t.Fatalf("delivery %s state=%s/%s, want %s/%s", id, gotState, gotCode, want[0], want[1])
		}
	}
}

func TestNoticeDigestLatestPerSenderAndTopicAndSupersededCount(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	sender1, receiver, source, dest := f.sender, f.receiver, f.source, f.dest
	sender2 := testStore(t, Channel{Principal: "digest-topic-s2-" + f.repo, Repo: f.repo})
	coord := f.register(nativeAgent{Binding: "digest-topic-coord"})
	sess := AgentSessionRef{AgentID: coord.AgentID, ExecutionID: coord.ExecutionID}
	ref := RecordVersionRef{RecordID: source.RecordID, Version: source.Version}
	toCoord := EventDestination{Type: "agent", Name: coord.Inbox}

	coordStore := testStore(t, Channel{Principal: "agent/" + coord.AgentID, Repo: f.repo})
	if _, err := coordStore.SetSubscription(ctx, SubscriptionRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Topic: "build-feed", Active: true,
	}); err != nil {
		t.Fatal(err)
	}
	toTopic := EventDestination{Type: "topic", Name: "build-feed"}

	// sender1 publishes 3 notices on topic build-feed (no explicit supersedes -> deduplicated by topic/build-feed at digest claim)
	// and 2 notices with explicit Supersedes="slot/alpha" (deduplicated at send time, still counted in DigestSupersededCount!).
	var s1TopicLatest AgentEvent
	for i := 0; i < 3; i++ {
		ev, err := sender1.PublishEvent(ctx, PublishEventRequest{
			RequestID: uuid.NewString(), Kind: "notice", Ref: ref, Destination: toTopic,
		}, dest)
		if err != nil {
			t.Fatal(err)
		}
		s1TopicLatest = ev
	}
	var s1AlphaFirst, s1AlphaLatest AgentEvent
	for i := 0; i < 5; i++ {
		ev, err := sender1.PublishEvent(ctx, PublishEventRequest{
			RequestID: uuid.NewString(), Kind: "notice", Ref: ref, Destination: toCoord, Supersedes: "slot/alpha",
		}, dest)
		if err != nil {
			t.Fatal(err)
		}
		if i == 0 {
			s1AlphaFirst = ev
		}
		s1AlphaLatest = ev
	}
	// Age ONLY the initial superseded v1 delivery past 300s: recursive lineage must make the digest due (no timer starvation!).
	if _, err := receiver.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET available_at=clock_timestamp()-interval '305 seconds' WHERE event_id=$1`, s1AlphaFirst.EventID); err != nil {
		t.Fatal(err)
	}
	readyChain, err := receiver.SessionInboxDigestReady(ctx, SessionInboxDigestReadyRequest{
		Session: sess, DigestWindowSeconds: 300,
	}, dest)
	if err != nil || !readyChain.Digest || readyChain.Count != 2 {
		t.Fatalf("expected superseded lineage to make digest due without starvation, got %+v err=%v", readyChain, err)
	}
	// sender2 publishes 1 notice on the same topic build-feed (kept separate because different sender!).
	s2Topic, err := sender2.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Ref: ref, Destination: toTopic,
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	// Simulate in-flight supersession after Ready returned readyChain.DeliveryID (s1TopicLatest):
	// sender1 publishes one more notice on topic build-feed before ClaimSessionInbox runs!
	s1TopicInFlight, err := sender1.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Ref: ref, Destination: toTopic,
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	_ = s1TopicLatest

	claimed, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{
		RequestID: uuid.NewString(), Session: sess, DeliveryID: readyChain.DeliveryID,
		NativeTurnID: "turn-dedup", DigestWindowSeconds: 300,
	}, dest)
	if err != nil || claimed.Attempt == nil {
		t.Fatalf("expected digest claim across in-flight supersession, got %+v err=%v", claimed, err)
	}
	att := *claimed.Attempt
	// Survivors: s1AlphaLatest (superseded 4), s2Topic (0), s1TopicInFlight (superseded 3) => 3 items, superseded_count = 7!
	if len(att.DigestItems) != 3 || att.DigestSupersededCount != 7 || att.WakeDeliveryID != readyChain.DeliveryID {
		t.Fatalf("expected 3 survivors, superseded_count=7, WakeDeliveryID=%s; got len=%d sup=%d wake=%s", readyChain.DeliveryID, len(att.DigestItems), att.DigestSupersededCount, att.WakeDeliveryID)
	}
	// SessionInboxAttemptStatus must strip LeaseID and LeaseUntil from all DigestItems.
	inspected, err := receiver.SessionInboxAttemptStatus(ctx, SessionInboxAttemptQuery{
		Session: sess, AttemptID: att.ID,
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	for _, di := range inspected.DigestItems {
		if di.Delivery.LeaseID != "" || di.Delivery.LeaseUntil != nil {
			t.Fatalf("SessionInboxAttemptStatus leaked digest item lease: %+v", di.Delivery)
		}
	}
	gotEvents := []string{
		att.DigestItems[0].Delivery.Event.EventID,
		att.DigestItems[1].Delivery.Event.EventID,
		att.DigestItems[2].Delivery.Event.EventID,
	}
	wantEvents := []string{s1AlphaLatest.EventID, s2Topic.EventID, s1TopicInFlight.EventID}
	for i := range wantEvents {
		if gotEvents[i] != wantEvents[i] {
			t.Fatalf("survivor %d = %s, want %s", i, gotEvents[i], wantEvents[i])
		}
	}
}

func TestNoticeDigestCrashPartialSettlementAndLeaseExpiry(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	sender, receiver, source, dest := f.sender, f.receiver, f.source, f.dest
	coord := f.register(nativeAgent{Binding: "digest-crash-coord"})
	sess := AgentSessionRef{AgentID: coord.AgentID, ExecutionID: coord.ExecutionID}
	ref := RecordVersionRef{RecordID: source.RecordID, Version: source.Version}
	toCoord := EventDestination{Type: "agent", Name: coord.Inbox}

	// 1. Crash during partial digest settlement preserves exact settled states and fails only unsettled items.
	for i := 0; i < 5; i++ {
		if _, err := sender.PublishEvent(ctx, PublishEventRequest{
			RequestID: uuid.NewString(), Kind: "notice", Ref: ref, Destination: toCoord,
		}, dest); err != nil {
			t.Fatal(err)
		}
	}
	claimed, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{
		RequestID: uuid.NewString(), Session: sess, NativeTurnID: "turn-crash",
		DigestWindowSeconds: 300, DigestFlushNow: true,
	}, dest)
	if err != nil || claimed.Attempt == nil || len(claimed.Attempt.DigestItems) != 5 {
		t.Fatalf("expected 5-item digest, got %+v err=%v", claimed, err)
	}
	att := *claimed.Attempt
	_, err = receiver.SettleSessionDigest(ctx, SessionInboxDigestSettle{
		RequestID: uuid.NewString(), Session: sess, AttemptID: att.ID,
		Outcomes: []SessionDigestItemOutcome{
			{DeliveryID: att.DigestItems[0].Delivery.DeliveryID, LeaseID: att.DigestItems[0].Delivery.LeaseID, Disposition: "handled"},
			{DeliveryID: att.DigestItems[1].Delivery.DeliveryID, LeaseID: att.DigestItems[1].Delivery.LeaseID, Disposition: "ignored", Code: "unsupported_kind"},
			{DeliveryID: att.DigestItems[2].Delivery.DeliveryID, LeaseID: att.DigestItems[2].Delivery.LeaseID, Disposition: "failed", Code: "source_unavailable"},
		},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	// delivery_completed must refuse while items 4 and 5 are still leased!
	_, err = receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{
		RequestID: uuid.NewString(), Session: sess, AttemptID: att.ID, Reason: "delivery_completed",
	}, dest)
	requireCode(t, err, "DELIVERY_ACTIVE")

	// Crash / process_exited reconciles unsettled items 4 & 5 as processing_failed while preserving items 1..3.
	if _, err := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{
		RequestID: uuid.NewString(), Session: sess, AttemptID: att.ID, Reason: "process_exited",
	}, dest); err != nil {
		t.Fatal(err)
	}
	wantCrash := [][2]string{
		{"handled", ""},
		{"ignored", "unsupported_kind"},
		{"failed", "source_unavailable"},
		{"failed", "processing_failed"},
		{"failed", "processing_failed"},
	}
	for i, w := range wantCrash {
		var st, cd string
		if err := receiver.pool.QueryRow(ctx, `SELECT state,COALESCE(code,'') FROM cairn.agent_delivery WHERE delivery_id=$1`, att.DigestItems[i].Delivery.DeliveryID).Scan(&st, &cd); err != nil {
			t.Fatal(err)
		}
		if st != w[0] || cd != w[1] {
			t.Fatalf("crash item %d = %s/%s, want %s/%s", i+1, st, cd, w[0], w[1])
		}
	}
	// Late post-crash settlement refuses HOLD_RELEASED.
	_, err = receiver.SettleSessionDigest(ctx, SessionInboxDigestSettle{
		RequestID: uuid.NewString(), Session: sess, AttemptID: att.ID,
		Outcomes: []SessionDigestItemOutcome{
			{DeliveryID: att.DigestItems[3].Delivery.DeliveryID, LeaseID: att.DigestItems[3].Delivery.LeaseID, Disposition: "handled"},
		},
	}, dest)
	requireCode(t, err, "HOLD_RELEASED")

	// 2. Lease expiry mid-digest: native hold excludes other consumers and accepts late settlement.
	for i := 0; i < 3; i++ {
		if _, err := sender.PublishEvent(ctx, PublishEventRequest{
			RequestID: uuid.NewString(), Kind: "notice", Ref: ref, Destination: toCoord,
		}, dest); err != nil {
			t.Fatal(err)
		}
	}
	claimed2, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{
		RequestID: uuid.NewString(), Session: sess, NativeTurnID: "turn-lapsed",
		DigestWindowSeconds: 300, DigestFlushNow: true,
	}, dest)
	if err != nil || claimed2.Attempt == nil || len(claimed2.Attempt.DigestItems) != 3 {
		t.Fatalf("expected 3-item digest, got %+v err=%v", claimed2, err)
	}
	att2 := *claimed2.Attempt
	if _, err := receiver.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET lease_until=clock_timestamp()-interval '15 seconds' WHERE consumer=$1 AND state='leased'`, coord.Inbox); err != nil {
		t.Fatal(err)
	}
	_, err = receiver.RenewSessionDigest(ctx, SessionInboxDigestRenew{Session: sess, AttemptID: att2.ID, LeaseSeconds: 90}, dest)
	requireCode(t, err, "STALE_LEASE")

	// Publisher/consumer poll cannot steal lapsed digest items while native hold is open.
	sessView, err := receiver.ForAgentSession(sess, dest)
	if err != nil {
		t.Fatal(err)
	}
	polled, err := sessView.NextEvent(ctx, NextEventRequest{LeaseSeconds: 60}, dest)
	if err != nil || polled.Delivery != nil {
		t.Fatalf("expected native hold to exclude NextEvent on lapsed digest items, got %+v err=%v", polled, err)
	}
	lateOutcomes := make([]SessionDigestItemOutcome, 3)
	for i, it := range att2.DigestItems {
		lateOutcomes[i] = SessionDigestItemOutcome{DeliveryID: it.Delivery.DeliveryID, LeaseID: it.Delivery.LeaseID, Disposition: "handled"}
	}
	lateRes, err := receiver.SettleSessionDigest(ctx, SessionInboxDigestSettle{
		RequestID: uuid.NewString(), Session: sess, AttemptID: att2.ID, Outcomes: lateOutcomes,
	}, dest)
	if err != nil || lateRes.Remaining != 0 || !lateRes.Items[0].CompletedAfterLease || !lateRes.Items[2].CompletedAfterLease {
		t.Fatalf("expected late digest settlement success, got %+v err=%v", lateRes, err)
	}
	if _, err := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{
		RequestID: uuid.NewString(), Session: sess, AttemptID: att2.ID, Reason: "delivery_completed",
	}, dest); err != nil {
		t.Fatal(err)
	}
}

func TestNoticeDigestPriorityPreemptionAndGoalBoundary(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	sender, receiver, source, dest := f.sender, f.receiver, f.source, f.dest
	coord := f.register(nativeAgent{Binding: "digest-preempt-coord"})
	sess := AgentSessionRef{AgentID: coord.AgentID, ExecutionID: coord.ExecutionID}
	ref := RecordVersionRef{RecordID: source.RecordID, Version: source.Version}
	toCoord := EventDestination{Type: "agent", Name: coord.Inbox}

	// Publish 3 routine/review FYI notices -> held while digest window (300s) is open.
	for i := 0; i < 3; i++ {
		if _, err := sender.PublishEvent(ctx, PublishEventRequest{
			RequestID: uuid.NewString(), Kind: "notice", Ref: ref, Destination: toCoord, Action: "review",
		}, dest); err != nil {
			t.Fatal(err)
		}
	}
	ready0, err := receiver.SessionInboxDigestReady(ctx, SessionInboxDigestReadyRequest{
		Session: sess, DigestWindowSeconds: 300,
	}, dest)
	if err != nil || ready0.DeliveryID != "" {
		t.Fatalf("expected held FYI notices before timer, got %+v err=%v", ready0, err)
	}

	// Now an urgent notice arrives AFTER the 3 held FYI notices: it MUST preempt immediately!
	urgentEv, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Ref: ref, Destination: toCoord,
		NoticePolicy: &NoticeWakePolicyRequest{Version: 1, Urgency: "urgent"},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	// Goal-continuation digest_only yields while a priority item is waiting so priority is never delayed.
	goalYield, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{
		RequestID: uuid.NewString(), Session: sess, NativeTurnID: "turn-goal-yield",
		DigestWindowSeconds: 300, DigestFlushNow: true, DigestOnly: true,
	}, dest)
	if err != nil || goalYield.Attempt != nil {
		t.Fatalf("expected DigestOnly to yield to waiting urgent notice, got %+v err=%v", goalYield, err)
	}

	readyUrgent, err := receiver.SessionInboxDigestReady(ctx, SessionInboxDigestReadyRequest{
		Session: sess, DigestWindowSeconds: 300,
	}, dest)
	if err != nil || readyUrgent.EventID != urgentEv.EventID || readyUrgent.Digest {
		t.Fatalf("expected urgent notice to preempt immediately, got %+v err=%v", readyUrgent, err)
	}
	claimedUrgent, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{
		RequestID: uuid.NewString(), Session: sess, DeliveryID: readyUrgent.DeliveryID,
		NativeTurnID: "turn-urgent", DigestWindowSeconds: 300,
	}, dest)
	if err != nil || claimedUrgent.Attempt == nil || claimedUrgent.Attempt.AdmissionKind != "ordinary" ||
		claimedUrgent.Attempt.Delivery.Event.EventID != urgentEv.EventID {
		t.Fatalf("expected ordinary single-item claim for urgent notice, got %+v err=%v", claimedUrgent, err)
	}
	sessView, err := receiver.ForAgentSession(sess, dest)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := sessView.CompleteEvent(ctx, CompleteEventRequest{
		RequestID: uuid.NewString(), DeliveryID: claimedUrgent.Attempt.Delivery.DeliveryID,
		LeaseID: claimedUrgent.Attempt.Delivery.LeaseID, Disposition: "handled",
	}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{
		RequestID: uuid.NewString(), Session: sess, AttemptID: claimedUrgent.Attempt.ID, Reason: "delivery_completed",
	}, dest); err != nil {
		t.Fatal(err)
	}

	// Because an urgent notice arrived since the 3 FYI notices were created, the held digest is NOW immediately due!
	readyFlush, err := receiver.SessionInboxDigestReady(ctx, SessionInboxDigestReadyRequest{
		Session: sess, DigestWindowSeconds: 300,
	}, dest)
	if err != nil || !readyFlush.Digest || readyFlush.Count != 3 {
		t.Fatalf("expected held 3-item digest to flush immediately after urgent arrival, got %+v err=%v", readyFlush, err)
	}
	claimedDigest, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{
		RequestID: uuid.NewString(), Session: sess, DeliveryID: readyFlush.DeliveryID,
		NativeTurnID: "turn-flushed-digest", DigestWindowSeconds: 300,
	}, dest)
	if err != nil || claimedDigest.Attempt == nil || len(claimedDigest.Attempt.DigestItems) != 3 {
		t.Fatalf("expected 3-item flushed digest, got %+v err=%v", claimedDigest, err)
	}
}

func TestNoticeDigestOverflowBeyond64EmitsCountAndRefsAndLeavesPending(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	sender, receiver, source, dest := f.sender, f.receiver, f.source, f.dest
	coord := f.register(nativeAgent{Binding: "digest-overflow-coord"})
	sess := AgentSessionRef{AgentID: coord.AgentID, ExecutionID: coord.ExecutionID}
	ref := RecordVersionRef{RecordID: source.RecordID, Version: source.Version}
	toCoord := EventDestination{Type: "agent", Name: coord.Inbox}

	// Publish 68 distinct FYI notices -> 64 claimed in first digest, 4 overflow deferred!
	var eventIDs []string
	for i := 0; i < 68; i++ {
		ev, err := sender.PublishEvent(ctx, PublishEventRequest{
			RequestID: uuid.NewString(), Kind: "notice", Ref: ref, Destination: toCoord, Action: "review",
		}, dest)
		if err != nil {
			t.Fatal(err)
		}
		eventIDs = append(eventIDs, ev.EventID)
	}

	firstClaim, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{
		RequestID: uuid.NewString(), Session: sess, NativeTurnID: "turn-overflow-1",
		DigestWindowSeconds: 300, DigestFlushNow: true,
	}, dest)
	if err != nil || firstClaim.Attempt == nil {
		t.Fatalf("first digest claim failed: %+v err=%v", firstClaim, err)
	}
	att1 := firstClaim.Attempt
	if att1.AdmissionKind != "digest" || len(att1.DigestItems) != 64 {
		t.Fatalf("expected 64 digest items, got kind=%q count=%d", att1.AdmissionKind, len(att1.DigestItems))
	}
	if att1.DigestOverflowCount != 4 || len(att1.DigestOverflowRefs) != 4 {
		t.Fatalf("expected DigestOverflowCount=4 and 4 refs, got count=%d refs=%+v", att1.DigestOverflowCount, att1.DigestOverflowRefs)
	}
	for i, ov := range att1.DigestOverflowRefs {
		if ov.EventID != eventIDs[64+i] || ov.DeliveryID == "" || ov.RecordID != source.RecordID || ov.Version != int64(source.Version) || ov.Kind != "notice" {
			t.Fatalf("unexpected overflow ref[%d]: %+v (want event %s)", i, ov, eventIDs[64+i])
		}
	}

	// Settle all 64 items (including a retry on item 1 to verify idempotency) and reconcile.
	outcomes := make([]SessionDigestItemOutcome, len(att1.DigestItems))
	for i, it := range att1.DigestItems {
		outcomes[i] = SessionDigestItemOutcome{DeliveryID: it.Delivery.DeliveryID, LeaseID: it.Delivery.LeaseID, Disposition: "handled"}
	}
	if _, err := receiver.SettleSessionDigest(ctx, SessionInboxDigestSettle{
		RequestID: uuid.NewString(), Session: sess, AttemptID: att1.ID, Outcomes: outcomes[:1],
	}, dest); err != nil {
		t.Fatal(err)
	}
	retryRes, err := receiver.SettleSessionDigest(ctx, SessionInboxDigestSettle{
		RequestID: uuid.NewString(), Session: sess, AttemptID: att1.ID, Outcomes: outcomes,
	}, dest)
	if err != nil || retryRes.Remaining != 0 || !retryRes.Items[0].AlreadySettled {
		t.Fatalf("expected idempotent retry settle with remaining=0, got %+v err=%v", retryRes, err)
	}
	if _, err := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{
		RequestID: uuid.NewString(), Session: sess, AttemptID: att1.ID, Reason: "delivery_completed",
	}, dest); err != nil {
		t.Fatal(err)
	}

	// The 4 deferred overflow items remain pending and arrive cleanly in the next digest turn!
	secondClaim, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{
		RequestID: uuid.NewString(), Session: sess, NativeTurnID: "turn-overflow-2",
		DigestWindowSeconds: 300, DigestFlushNow: true,
	}, dest)
	if err != nil || secondClaim.Attempt == nil {
		t.Fatalf("second digest claim failed: %+v err=%v", secondClaim, err)
	}
	att2 := secondClaim.Attempt
	if len(att2.DigestItems) != 4 || att2.DigestOverflowCount != 0 {
		t.Fatalf("expected 4 remaining items with 0 overflow, got items=%d overflow=%d", len(att2.DigestItems), att2.DigestOverflowCount)
	}
	for i, it := range att2.DigestItems {
		if it.Delivery.Event.EventID != eventIDs[64+i] {
			t.Fatalf("second digest item[%d]=%s, want %s", i, it.Delivery.Event.EventID, eventIDs[64+i])
		}
	}
}

func TestNoticeDigestResponsePreemptsOpenWindowAndTriggersFlush(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	sender, receiver, source, dest := f.sender, f.receiver, f.source, f.dest
	coord := f.register(nativeAgent{Binding: "digest-resp-coord"})
	sess := AgentSessionRef{AgentID: coord.AgentID, ExecutionID: coord.ExecutionID}
	ref := RecordVersionRef{RecordID: source.RecordID, Version: source.Version}
	toCoord := EventDestination{Type: "agent", Name: coord.Inbox}

	// Publish a prior request from coord -> sender so sender can publish a genuine response to coord.
	coordView, err := receiver.ForAgentSession(sess, dest)
	if err != nil {
		t.Fatal(err)
	}
	reqEv, err := coordView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "request", Ref: ref, Destination: EventDestination{Type: "agent", Name: sender.channel.Principal},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}

	// Publish 2 routine FYI notices to coord -> held while 300s digest window is open.
	for i := 0; i < 2; i++ {
		if _, err := sender.PublishEvent(ctx, PublishEventRequest{
			RequestID: uuid.NewString(), Kind: "notice", Ref: ref, Destination: toCoord, Action: "review",
		}, dest); err != nil {
			t.Fatal(err)
		}
	}
	ready0, err := receiver.SessionInboxDigestReady(ctx, SessionInboxDigestReadyRequest{
		Session: sess, DigestWindowSeconds: 300,
	}, dest)
	if err != nil || ready0.DeliveryID != "" {
		t.Fatalf("expected held FYI notices before response, got %+v err=%v", ready0, err)
	}

	// Now a response arrives: it must NOT be digested and must preempt immediately!
	respEv, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "response", Ref: ref, Destination: toCoord,
		CausationID: reqEv.EventID, CorrelationID: reqEv.CorrelationID,
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	readyResp, err := receiver.SessionInboxDigestReady(ctx, SessionInboxDigestReadyRequest{
		Session: sess, DigestWindowSeconds: 300,
	}, dest)
	if err != nil || readyResp.EventID != respEv.EventID || readyResp.Kind != "response" || readyResp.Digest {
		t.Fatalf("expected immediate ordinary preemption for response, got %+v err=%v", readyResp, err)
	}
	claimedResp, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{
		RequestID: uuid.NewString(), Session: sess, DeliveryID: readyResp.DeliveryID,
		NativeTurnID: "turn-response", DigestWindowSeconds: 300,
	}, dest)
	if err != nil || claimedResp.Attempt == nil || claimedResp.Attempt.AdmissionKind != "ordinary" ||
		claimedResp.Attempt.Delivery.Event.EventID != respEv.EventID {
		t.Fatalf("expected ordinary single-item claim for response, got %+v err=%v", claimedResp, err)
	}
	if _, err := coordView.CompleteEvent(ctx, CompleteEventRequest{
		RequestID: uuid.NewString(), DeliveryID: claimedResp.Attempt.Delivery.DeliveryID,
		LeaseID: claimedResp.Attempt.Delivery.LeaseID, Disposition: "handled",
	}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{
		RequestID: uuid.NewString(), Session: sess, AttemptID: claimedResp.Attempt.ID, Reason: "delivery_completed",
	}, dest); err != nil {
		t.Fatal(err)
	}

	// digestDueTx must see the recent response (ue.kind='response') and flush the 2 held FYI notices immediately!
	readyFlush, err := receiver.SessionInboxDigestReady(ctx, SessionInboxDigestReadyRequest{
		Session: sess, DigestWindowSeconds: 300,
	}, dest)
	if err != nil || !readyFlush.Digest || readyFlush.Count != 2 {
		t.Fatalf("expected held 2-item digest to flush immediately after response arrival, got %+v err=%v", readyFlush, err)
	}
}
