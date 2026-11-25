package core

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func intPtr(v int) *int { return &v }

func TestOverdueCheckpointsNoticeRequestAndBoardDeduplicationAcrossRestart(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	coord := f.register(nativeAgent{Binding: "coord-overdue"})
	lead := f.register(nativeAgent{Binding: "lead-overdue"})
	coordView := f.view(sessionRef(coord))
	leadView := f.view(sessionRef(lead))
	op := f.operator()

	if _, err := coordView.SetSubscription(ctx, SubscriptionRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Topic: "workstream-overdue", Active: true,
	}); err != nil {
		t.Fatalf("subscribe workstream-overdue: %v", err)
	}

	// 1. Missed board next_checkpoint -> exactly 1 urgent overdue notice across sweeps and restart.
	pastCP := time.Now().UTC().Add(-5 * time.Minute).Truncate(time.Second)
	if _, err := leadView.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Stream: "foundation",
		State: "active", Current: "F-04 cutover", NextCheckpoint: &pastCP,
	}, f.dest); err != nil {
		t.Fatalf("SetWorkstreamStatus: %v", err)
	}

	res1, err := op.SweepOverdueDeadlines(ctx, OverdueSweepRequest{Repo: f.repo, GraceSeconds: intPtr(60)})
	if err != nil || res1.EmittedNotices != 1 || res1.EmittedItems != 1 || len(res1.Published) != 1 {
		t.Fatalf("expected 1 overdue notice for board checkpoint, got %+v err=%v", res1, err)
	}
	// Urgent overdue notice bypasses coordinator notice digest immediately.
	ready, err := f.receiver.SessionInboxDigestReady(ctx, SessionInboxDigestReadyRequest{
		Session: sessionRef(coord), DigestWindowSeconds: 600,
	}, f.dest)
	if err != nil || ready.DeliveryID == "" || ready.Digest || ready.Kind != "notice" || ready.Action != "review" {
		t.Fatalf("expected urgent overdue notice to preempt digest window, got %+v err=%v", ready, err)
	}
	next, err := coordView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
	if err != nil || next.Delivery == nil || next.Delivery.Event.EventID != res1.Published[0].EventID {
		t.Fatalf("expected overdue delivery, got %+v err=%v", next, err)
	}
	if next.Delivery.Event.Action != "review" || next.Delivery.Event.NoticePolicy == nil || next.Delivery.Event.NoticePolicy.Urgency != "urgent" {
		t.Fatalf("expected action=review and urgency=urgent, got %+v", next.Delivery.Event)
	}
	if _, err := coordView.CompleteEvent(ctx, CompleteEventRequest{
		RequestID: uuid.NewString(), DeliveryID: next.Delivery.DeliveryID,
		LeaseID: next.Delivery.LeaseID, Disposition: "handled",
	}, f.dest); err != nil {
		t.Fatalf("CompleteEvent overdue 1: %v", err)
	}

	// Simulate host restart with a fresh operator Store: zero duplicate overdue events.
	restartedOp := testStore(t, Channel{Principal: "operator-restart-" + f.repo, Operator: true})
	resDup, err := restartedOp.SweepOverdueDeadlines(ctx, OverdueSweepRequest{Repo: f.repo, GraceSeconds: intPtr(60)})
	if err != nil || resDup.EmittedNotices != 0 || resDup.EmittedItems != 0 || resDup.CoalescedItems != 0 {
		t.Fatalf("expected 0 duplicate events across restart, got %+v err=%v", resDup, err)
	}

	// 2. Missed notice expect_next_by -> 1 urgent overdue notice, 0 duplicates.
	pastNoticeDue := time.Now().UTC().Add(-4 * time.Minute).Truncate(time.Second)
	noticeEv, err := leadView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice",
		Action: "none", Supersedes: "lane/f04", ExpectNextBy: &pastNoticeDue,
		Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{Type: "agent", Name: coord.Inbox},
	}, f.dest)
	if err != nil {
		t.Fatalf("publish notice: %v", err)
	}
	// Drain original notice so coord inbox only receives the overdue notice next.
	origClaim, err := coordView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
	if err != nil || origClaim.Delivery == nil || origClaim.Delivery.Event.EventID != noticeEv.EventID {
		t.Fatalf("claim original notice: %+v %v", origClaim, err)
	}
	if _, err := coordView.CompleteEvent(ctx, CompleteEventRequest{
		RequestID: uuid.NewString(), DeliveryID: origClaim.Delivery.DeliveryID,
		LeaseID: origClaim.Delivery.LeaseID, Disposition: "handled",
	}, f.dest); err != nil {
		t.Fatalf("ack original notice: %v", err)
	}

	res2, err := restartedOp.SweepOverdueDeadlines(ctx, OverdueSweepRequest{Repo: f.repo, GraceSeconds: intPtr(60)})
	if err != nil || res2.EmittedNotices != 1 || res2.EmittedItems != 1 {
		t.Fatalf("expected 1 overdue notice for missed expect_next_by, got %+v err=%v", res2, err)
	}
	claim2, err := coordView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
	if err != nil || claim2.Delivery == nil || claim2.Delivery.Event.EventID != res2.Published[0].EventID {
		t.Fatalf("claim overdue 2: %+v %v", claim2, err)
	}
	if _, err := coordView.CompleteEvent(ctx, CompleteEventRequest{
		RequestID: uuid.NewString(), DeliveryID: claim2.Delivery.DeliveryID,
		LeaseID: claim2.Delivery.LeaseID, Disposition: "handled",
	}, f.dest); err != nil {
		t.Fatalf("ack overdue 2: %v", err)
	}

	// 3. Missed request expect_by -> 1 urgent overdue notice to requester, 0 duplicates.
	pastReqDue := time.Now().UTC().Add(-3 * time.Minute).Truncate(time.Second)
	reqEv, err := coordView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "request", ExpectBy: &pastReqDue,
		Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{Type: "agent", Name: lead.Inbox},
	}, f.dest)
	if err != nil {
		t.Fatalf("publish request: %v", err)
	}
	if reqEv.ExpectBy == nil || !reqEv.ExpectBy.Equal(pastReqDue) {
		t.Fatalf("expected ExpectBy persisted on request, got %+v", reqEv.ExpectBy)
	}
	res3, err := restartedOp.SweepOverdueDeadlines(ctx, OverdueSweepRequest{Repo: f.repo, GraceSeconds: intPtr(60)})
	if err != nil || res3.EmittedNotices != 1 || res3.EmittedItems != 1 {
		t.Fatalf("expected 1 overdue notice for missed request expect_by, got %+v err=%v", res3, err)
	}
	res3Again, err := op.SweepOverdueDeadlines(ctx, OverdueSweepRequest{Repo: f.repo, GraceSeconds: intPtr(60)})
	if err != nil || res3Again.EmittedNotices != 0 {
		t.Fatalf("expected 0 duplicate request overdue notices, got %+v err=%v", res3Again, err)
	}
}

func TestOverdueCheckpointsNoEventWhenUpdatedWithinDeadlinePlusGrace(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	coord := f.register(nativeAgent{Binding: "coord-grace"})
	lead := f.register(nativeAgent{Binding: "lead-grace"})
	coordView := f.view(sessionRef(coord))
	leadView := f.view(sessionRef(lead))
	op := f.operator()

	if _, err := coordView.SetSubscription(ctx, SubscriptionRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Topic: "workstream-overdue", Active: true,
	}); err != nil {
		t.Fatalf("subscribe: %v", err)
	}

	// Deadlines 5 seconds in the past, grace 30 seconds -> still inside deadline + grace.
	withinGrace := time.Now().UTC().Add(-5 * time.Second).Truncate(time.Second)
	if _, err := leadView.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Stream: "lane-grace",
		State: "active", Current: "Step 1", NextCheckpoint: &withinGrace,
	}, f.dest); err != nil {
		t.Fatalf("SetWorkstreamStatus: %v", err)
	}
	notice1, err := leadView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice",
		Action: "none", Supersedes: "lane-grace-notice", ExpectNextBy: &withinGrace,
		Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{Type: "agent", Name: coord.Inbox},
	}, f.dest)
	if err != nil {
		t.Fatalf("publish notice1: %v", err)
	}
	req1, err := coordView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "request", ExpectBy: &withinGrace,
		Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{Type: "agent", Name: lead.Inbox},
	}, f.dest)
	if err != nil {
		t.Fatalf("publish req1: %v", err)
	}

	// Sweep while inside deadline + grace (5s < 30s): zero events, zero recorded rows.
	inGraceRes, err := op.SweepOverdueDeadlines(ctx, OverdueSweepRequest{Repo: f.repo, GraceSeconds: intPtr(30)})
	if err != nil || inGraceRes.EmittedNotices != 0 || inGraceRes.SatisfiedItems != 0 || inGraceRes.CoalescedItems != 0 {
		t.Fatalf("expected 0 activity inside grace window, got %+v err=%v", inGraceRes, err)
	}

	// Updates arrive AFTER deadline (-5s) and BEFORE deadline + grace (+25s):
	// 1) Board advances next_checkpoint into the future.
	nextFuture := time.Now().UTC().Add(30 * time.Minute).Truncate(time.Second)
	if _, err := leadView.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Stream: "lane-grace",
		State: "active", Current: "Step 2", NextCheckpoint: &nextFuture, ExpectedRevision: 1,
	}, f.dest); err != nil {
		t.Fatalf("update workstream: %v", err)
	}
	// 2) Notice sender publishes a newer update on the same supersedes key.
	if _, err := leadView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice",
		Action: "none", Supersedes: "lane-grace-notice",
		Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{Type: "agent", Name: coord.Inbox},
	}, f.dest); err != nil {
		t.Fatalf("publish follow-up notice: %v", err)
	}
	// 3) Request recipient completes the request.
	leadClaim, err := leadView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
	if err != nil || leadClaim.Delivery == nil || leadClaim.Delivery.Event.EventID != req1.EventID {
		t.Fatalf("lead claim request: %+v %v", leadClaim, err)
	}
	if _, err := leadView.CompleteEvent(ctx, CompleteEventRequest{
		RequestID: uuid.NewString(), DeliveryID: leadClaim.Delivery.DeliveryID,
		LeaseID: leadClaim.Delivery.LeaseID, Disposition: "handled",
		Draft: &Draft{Body: "Done on time.", Kind: "note", ClaimType: "self", Sensitivity: "shareable", Scope: Scope{Repo: f.repo, TaskID: "*", RunID: "*"}},
	}, f.dest); err != nil {
		t.Fatalf("complete request: %v", err)
	}

	// Now sweep with grace=0 (so notice1 and req1 deadlines are past grace): zero overdue notices!
	afterGraceRes, err := op.SweepOverdueDeadlines(ctx, OverdueSweepRequest{Repo: f.repo, GraceSeconds: intPtr(0)})
	if err != nil || afterGraceRes.EmittedNotices != 0 || afterGraceRes.CoalescedItems != 0 || afterGraceRes.SatisfiedItems != 2 {
		t.Fatalf("expected 0 overdue notices and 2 satisfied event deadlines, got %+v err=%v (notice=%s)", afterGraceRes, err, notice1.EventID)
	}
}

func TestOverdueCheckpointsCoalescingWhilePendingAndSensitivityIsolation(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	localDest := Destination{Name: "local", AllowLocal: true}
	hostedDest := Destination{Name: "hosted", AllowLocal: false}
	coord := f.register(nativeAgent{Binding: "coord-coalesce", Dest: &localDest})
	hostedCoord := f.register(nativeAgent{Binding: "coord-hosted", Dest: &hostedDest})
	lead := f.register(nativeAgent{Binding: "lead-coalesce", Dest: &localDest})
	coordView, err := f.receiver.ForAgentSession(sessionRef(coord), localDest)
	if err != nil {
		t.Fatalf("ForAgentSession local: %v", err)
	}
	hostedView, err := f.receiver.ForAgentSession(sessionRef(hostedCoord), hostedDest)
	if err != nil {
		t.Fatalf("ForAgentSession hosted: %v", err)
	}
	leadView, err := f.receiver.ForAgentSession(sessionRef(lead), localDest)
	if err != nil {
		t.Fatalf("ForAgentSession lead: %v", err)
	}
	op := f.operator()

	for _, v := range []*Store{coordView, hostedView} {
		if _, err := v.SetSubscription(ctx, SubscriptionRequest{
			RequestID: uuid.NewString(), Repo: f.repo, Topic: "workstream-overdue", Active: true,
		}); err != nil {
			t.Fatalf("subscribe: %v", err)
		}
	}

	past1 := time.Now().UTC().Add(-10 * time.Minute).Truncate(time.Second)
	if _, err := leadView.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Stream: "lane-a",
		State: "active", Current: "Slice A", NextCheckpoint: &past1,
	}, f.dest); err != nil {
		t.Fatalf("lane-a: %v", err)
	}
	res1, err := op.SweepOverdueDeadlines(ctx, OverdueSweepRequest{Repo: f.repo, GraceSeconds: intPtr(0)})
	if err != nil || res1.EmittedNotices != 2 {
		t.Fatalf("expected 1 overdue notice per subscriber (coord + hostedCoord), got %+v err=%v", res1, err)
	}

	// While coord's first overdue notice is still pending, 3 more deadlines miss:
	// lane-b (shareable), lane-c (shareable, will recover before E1 settles), and lane-secret (local).
	past2 := time.Now().UTC().Add(-8 * time.Minute).Truncate(time.Second)
	for _, spec := range []SetWorkstreamStatusRequest{
		{RequestID: uuid.NewString(), Repo: f.repo, Stream: "lane-b", State: "active", NextCheckpoint: &past2},
		{RequestID: uuid.NewString(), Repo: f.repo, Stream: "lane-c", State: "blocked", NextCheckpoint: &past2},
		{RequestID: uuid.NewString(), Repo: f.repo, Stream: "lane-secret", State: "active", Sensitivity: "local", NextCheckpoint: &past2},
	} {
		if _, err := leadView.SetWorkstreamStatus(ctx, spec, localDest); err != nil {
			t.Fatalf("set %s: %v", spec.Stream, err)
		}
	}
	pastNotice := time.Now().UTC().Add(-7 * time.Minute).Truncate(time.Second)
	origNotice, err := leadView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice",
		Action: "review", Supersedes: "lane-d", ExpectNextBy: &pastNotice,
		Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{Type: "agent", Name: coord.Inbox},
	}, f.dest)
	if err != nil {
		t.Fatalf("publish origNotice: %v", err)
	}

	// Sweep while E1 is pending for coord: shareable items for coord coalesce (lane-b, lane-c, lane-d = 3);
	// local item lane-secret is skipped (ceiling_exceeded) for hostedCoord and emitted separately as local for coord!
	res2, err := op.SweepOverdueDeadlines(ctx, OverdueSweepRequest{Repo: f.repo, GraceSeconds: intPtr(0)})
	if err != nil || res2.SkippedItems != 1 || res2.EmittedNotices != 1 {
		t.Fatalf("expected 1 local overdue notice for coord and 1 ceiling_exceeded skip for hostedCoord, got %+v err=%v", res2, err)
	}
	if res2.Published[0].Subscriber != coord.Inbox || len(res2.Published[0].Sources) != 1 || res2.Published[0].Sources[0] != "workstream:lane-secret" {
		t.Fatalf("expected isolated local notice for lane-secret, got %+v", res2.Published)
	}

	// Lease E1 for coord: coalesced shareable items STILL must not emit while E1 is leased.
	claimE1, err := coordView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, localDest)
	if err != nil || claimE1.Delivery == nil {
		t.Fatalf("claim E1: %+v %v", claimE1, err)
	}
	resWhileLeased, err := op.SweepOverdueDeadlines(ctx, OverdueSweepRequest{Repo: f.repo, GraceSeconds: intPtr(0)})
	if err != nil || resWhileLeased.EmittedNotices != 0 {
		t.Fatalf("expected 0 emissions while E1 is leased, got %+v err=%v", resWhileLeased, err)
	}

	// lane-c recovers (marked done) BEFORE E1 settles.
	if _, err := leadView.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Stream: "lane-c",
		State: "done", ExpectedRevision: 1,
	}, localDest); err != nil {
		t.Fatalf("lane-c done: %v", err)
	}

	// Settle E1 (and drain origNotice + local notice so we can inspect the coalesced E2 notice cleanly).
	if _, err := coordView.CompleteEvent(ctx, CompleteEventRequest{
		RequestID: uuid.NewString(), DeliveryID: claimE1.Delivery.DeliveryID,
		LeaseID: claimE1.Delivery.LeaseID, Disposition: "handled",
	}, localDest); err != nil {
		t.Fatalf("ack E1: %v", err)
	}
	for i := 0; i < 2; i++ {
		c, err := coordView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, localDest)
		if err != nil || c.Delivery == nil {
			t.Fatalf("drain item %d: %+v %v", i, c, err)
		}
		if _, err := coordView.CompleteEvent(ctx, CompleteEventRequest{
			RequestID: uuid.NewString(), DeliveryID: c.Delivery.DeliveryID,
			LeaseID: c.Delivery.LeaseID, Disposition: "handled",
		}, localDest); err != nil {
			t.Fatalf("ack drain %d: %v", i, err)
		}
	}
	_ = origNotice

	// Now E1 is settled for coord (hostedCoord's E1 is still pending, so hostedCoord's
	// coalesced rows stay lazily parked until hostedCoord settles E1):
	// coord's lane-c becomes satisfied (1 row), and coord gets ONE coalesced notice for lane-b + lane-d!
	resAfterSettle, err := op.SweepOverdueDeadlines(ctx, OverdueSweepRequest{Repo: f.repo, GraceSeconds: intPtr(0)})
	if err != nil || resAfterSettle.EmittedNotices != 1 || resAfterSettle.EmittedItems != 2 || resAfterSettle.SatisfiedItems != 1 {
		t.Fatalf("expected 1 coalesced notice (2 items) and 1 satisfied lane-c row for coord, got %+v err=%v", resAfterSettle, err)
	}
	claimE2, err := coordView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, localDest)
	if err != nil || claimE2.Delivery == nil || claimE2.Delivery.Event.EventID != resAfterSettle.Published[0].EventID {
		t.Fatalf("claim coalesced E2: %+v %v", claimE2, err)
	}
	hist, err := coordView.History(ctx, RecordHistoryRequest{RecordID: claimE2.Delivery.Event.Ref.RecordID, Version: 1}, localDest)
	if err != nil || len(hist.Versions) != 1 || hist.Versions[0].Body == nil {
		t.Fatalf("read E2 body: %+v %v", hist, err)
	}
	body := *hist.Versions[0].Body
	if !strings.Contains(body, "workstream lane-b") || !strings.Contains(body, "lane-d") || strings.Contains(body, "lane-c") || strings.Contains(body, "lane-secret") {
		t.Fatalf("unexpected coalesced E2 body: %s", body)
	}
}

func TestOverdueCheckpointsAdversarialReviewRegressions(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	coord := f.register(nativeAgent{Binding: "coord-adv"})
	leadA := f.register(nativeAgent{Binding: "lead-a"})
	leadB := f.register(nativeAgent{Binding: "lead-b"})
	coordView := f.view(sessionRef(coord))
	leadAView := f.view(sessionRef(leadA))
	leadBView := f.view(sessionRef(leadB))
	op := f.operator()

	for _, sub := range []struct {
		v *Store
		t string
	}{
		{coordView, "workstream-overdue"},
		{coordView, "team-notices"},
		{leadAView, "team-notices"},
		{leadAView, "team-requests"},
		{leadBView, "team-requests"},
	} {
		if _, err := sub.v.SetSubscription(ctx, SubscriptionRequest{
			RequestID: uuid.NewString(), Repo: f.repo, Topic: sub.t, Active: true,
		}); err != nil {
			t.Fatalf("subscribe %s: %v", sub.t, err)
		}
	}

	// 1. Same-turn notice (created_at > ws.updated_at, < next_checkpoint) + AuthorizeWorkstreamStatus
	// + subscriber ping MUST NOT silence a missed workstream next_checkpoint.
	cpSoon := time.Now().UTC().Add(5 * time.Minute).Truncate(time.Second)
	if _, err := leadAView.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Stream: "lane-adv",
		State: "active", Current: "Starting", NextCheckpoint: &cpSoon,
	}, f.dest); err != nil {
		t.Fatalf("SetWorkstreamStatus: %v", err)
	}
	// Same-turn notice without expect_next_by.
	sameTurnEv, err := leadAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice",
		Action: "none", Supersedes: "lane-adv",
		Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{Type: "agent", Name: coord.Inbox},
	}, f.dest)
	if err != nil {
		t.Fatalf("same-turn notice: %v", err)
	}
	// Move stream declaration (-10m), same-turn notice (-9m59s, i.e. > updated_at but < next_checkpoint),
	// and checkpoint (-2m) into the past; then run AuthorizeWorkstreamStatus at/after deadline (now).
	f.exec(`UPDATE cairn.workstream_status SET next_checkpoint = clock_timestamp() - interval '2 minutes', updated_at = clock_timestamp() - interval '10 minutes' WHERE repo=$1 AND stream='lane-adv'`, f.repo)
	f.exec(`UPDATE cairn.workstream_status_history SET next_checkpoint = (SELECT next_checkpoint FROM cairn.workstream_status WHERE repo=$1 AND stream='lane-adv'), written_at = clock_timestamp() - interval '10 minutes' WHERE repo=$1 AND stream='lane-adv'`, f.repo)
	f.exec(`UPDATE cairn.agent_event SET created_at = clock_timestamp() - interval '9 minutes 59 seconds' WHERE event_id=$1`, sameTurnEv.EventID)
	if _, err := leadAView.AuthorizeWorkstreamStatus(ctx, AuthorizeWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Stream: "lane-adv", Writer: leadB.Inbox,
	}, f.dest); err != nil {
		t.Fatalf("AuthorizeWorkstreamStatus: %v", err)
	}
	// Subscriber ping on stream topic also must not satisfy.
	if _, err := coordView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Action: "review", Supersedes: "lane-adv",
		Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{Type: "agent", Name: leadA.Inbox},
	}, f.dest); err != nil {
		t.Fatalf("subscriber ping: %v", err)
	}

	resWS, err := op.SweepOverdueDeadlines(ctx, OverdueSweepRequest{Repo: f.repo, GraceSeconds: intPtr(0)})
	if err != nil || resWS.EmittedNotices != 1 || resWS.SatisfiedItems != 0 {
		t.Fatalf("expected 1 overdue notice for lane-adv despite same-turn notice/authorize/ping, got %+v err=%v", resWS, err)
	}
	// Drain coord's inbox so subsequent overdue notices are not coalesced behind resWS.
	for {
		c, err := coordView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
		if err != nil {
			t.Fatalf("drain coord: %v", err)
		}
		if c.Delivery == nil {
			break
		}
		if _, err := coordView.CompleteEvent(ctx, CompleteEventRequest{
			RequestID: uuid.NewString(), DeliveryID: c.Delivery.DeliveryID,
			LeaseID: c.Delivery.LeaseID, Disposition: "handled",
		}, f.dest); err != nil {
			t.Fatalf("ack coord: %v", err)
		}
	}

	// 2. Shared correlation_id sibling reply + partial topic fanout MUST NOT satisfy hung request expect_by.
	sharedCorr := uuid.NewString()
	pastDue := time.Now().UTC().Add(-2 * time.Minute).Truncate(time.Second)
	reqA, err := coordView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "request",
		CorrelationID: sharedCorr, ExpectBy: &pastDue,
		Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{Type: "agent", Name: leadA.Inbox},
	}, f.dest)
	if err != nil {
		t.Fatalf("reqA: %v", err)
	}
	reqTopic, err := coordView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "request",
		CorrelationID: sharedCorr, ExpectBy: &pastDue,
		Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{Type: "topic", Name: "team-requests"},
	}, f.dest)
	if err != nil {
		t.Fatalf("reqTopic: %v", err)
	}
	// leadA replies to reqA AND replies to reqTopic, while leadB hangs on reqTopic!
	if _, err := leadAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "response",
		CausationID: reqA.EventID, CorrelationID: sharedCorr,
		Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{Type: "agent", Name: coord.Inbox},
	}, f.dest); err != nil {
		t.Fatalf("respA: %v", err)
	}
	if _, err := leadAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "response",
		CausationID: reqTopic.EventID, CorrelationID: sharedCorr,
		Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{Type: "agent", Name: coord.Inbox},
	}, f.dest); err != nil {
		t.Fatalf("respTopicA: %v", err)
	}

	// reqA is satisfied (1), while reqTopic still has leadB hung -> 1 overdue notice for reqTopic!
	resReq, err := op.SweepOverdueDeadlines(ctx, OverdueSweepRequest{Repo: f.repo, GraceSeconds: intPtr(0)})
	if err != nil || resReq.SatisfiedItems != 1 || resReq.EmittedNotices != 1 || resReq.Published[0].Sources[0] != "request:"+reqTopic.EventID {
		t.Fatalf("expected reqA satisfied and reqTopic overdue while leadB hung, got %+v err=%v", resReq, err)
	}

	// 3. Topic notice with expect_next_by alerts subscriber coord, NOT self-subscriber leadA.
	for {
		c, err := coordView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
		if err != nil || c.Delivery == nil {
			break
		}
		_, _ = coordView.CompleteEvent(ctx, CompleteEventRequest{
			RequestID: uuid.NewString(), DeliveryID: c.Delivery.DeliveryID,
			LeaseID: c.Delivery.LeaseID, Disposition: "handled",
		}, f.dest)
	}
	if _, err := leadAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Action: "review",
		ExpectNextBy: &pastDue,
		Ref:          RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination:  EventDestination{Type: "topic", Name: "team-notices"},
	}, f.dest); err != nil {
		t.Fatalf("topic notice: %v", err)
	}
	resTopicNotice, err := op.SweepOverdueDeadlines(ctx, OverdueSweepRequest{Repo: f.repo, GraceSeconds: intPtr(0)})
	if err != nil || resTopicNotice.EmittedNotices != 1 || resTopicNotice.Published[0].Subscriber != coord.Inbox {
		t.Fatalf("expected topic notice overdue only for coord (not self leadA), got %+v err=%v", resTopicNotice, err)
	}
}
