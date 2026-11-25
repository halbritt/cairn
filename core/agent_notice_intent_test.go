package core

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestNoticeIntentPublishValidationAndOmissionCompatibility(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	sender, source, dest := f.sender, f.source, f.dest
	agent := f.register(nativeAgent{Binding: "notice-intent-validation"})
	ref := RecordVersionRef{RecordID: source.RecordID, Version: source.Version}
	to := EventDestination{Type: "agent", Name: agent.Inbox}
	due := time.Date(2026, 11, 20, 12, 0, 0, 0, time.UTC)

	_, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Ref: ref, Destination: to,
	}, dest)
	requireCode(t, err, "INVALID_REQUEST")

	t.Run("legacy_optional_action_env_compatibility", func(t *testing.T) {
		t.Setenv("CAIRN_ALLOW_OPTIONAL_NOTICE_ACTION", "1")
		legacy, err := sender.PublishEvent(ctx, PublishEventRequest{
			RequestID: uuid.NewString(), Kind: "notice", Ref: ref, Destination: to,
		}, dest)
		if err != nil || legacy.Action != "" || legacy.Supersedes != "" || legacy.ExpectNextBy != nil {
			t.Fatalf("omitted intent fields changed legacy notice under compatibility env: %+v %v", legacy, err)
		}
	})

	reqID := uuid.NewString()
	validReq := PublishEventRequest{
		RequestID: reqID, Kind: "notice", Ref: ref, Destination: to,
		Action: "review", Supersedes: "stream/alpha", ExpectNextBy: &due,
	}
	published, err := sender.PublishEvent(ctx, validReq, dest)
	if err != nil {
		t.Fatal(err)
	}
	if published.Action != "review" || published.Supersedes != "stream/alpha" ||
		published.ExpectNextBy == nil || !published.ExpectNextBy.Equal(due) {
		t.Fatalf("unexpected notice intent fields: %+v", published)
	}
	replay, err := sender.PublishEvent(ctx, validReq, dest)
	if err != nil || replay.EventID != published.EventID || replay.Action != "review" {
		t.Fatalf("idempotent publish replay mismatch: %+v %v", replay, err)
	}
	conflict := validReq
	conflict.Action = "decide"
	_, err = sender.PublishEvent(ctx, conflict, dest)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")

	for _, bad := range []PublishEventRequest{
		{RequestID: uuid.NewString(), Kind: "request", Ref: ref, Destination: to, Action: "decide"},
		{RequestID: uuid.NewString(), Kind: "request", Ref: ref, Destination: to, Supersedes: "stream/alpha"},
		{RequestID: uuid.NewString(), Kind: "request", Ref: ref, Destination: to, ExpectNextBy: &due},
		{RequestID: uuid.NewString(), Kind: "notice", Ref: ref, Destination: to, Action: "acknowledge"},
		{RequestID: uuid.NewString(), Kind: "notice", Ref: ref, Destination: to, Action: "review", Supersedes: "bad topic space"},
		{RequestID: uuid.NewString(), Kind: "notice", Ref: ref, Destination: to, Action: "none",
			NoticePolicy: &NoticeWakePolicyRequest{Version: 1, Urgency: "urgent"}},
	} {
		_, err := sender.PublishEvent(ctx, bad, dest)
		requireCode(t, err, "INVALID_REQUEST")
	}
}

func TestNoticeIntentSupersedesPendingSameSenderAndTopicOnly(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	sender, receiver, source, dest := f.sender, f.receiver, f.source, f.dest
	otherSender := testStore(t, Channel{Principal: "other-sender-" + f.repo, Repo: f.repo})
	r1 := f.register(nativeAgent{Binding: "supersede-r1"})
	r2 := f.register(nativeAgent{Binding: "supersede-r2"})
	ref := RecordVersionRef{RecordID: source.RecordID, Version: source.Version}
	toR1 := EventDestination{Type: "agent", Name: r1.Inbox}
	toR2 := EventDestination{Type: "agent", Name: r2.Inbox}

	n1, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "review", Ref: ref, Destination: toR1, Supersedes: "build/status",
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	// Another sender with the same topic must not supersede sender's pending notice.
	nOther, err := otherSender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "review", Ref: ref, Destination: toR1, Supersedes: "build/status",
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	// Same sender with a different topic or different recipient must not supersede n1.
	if _, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "review", Ref: ref, Destination: toR1, Supersedes: "build/other",
	}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "review", Ref: ref, Destination: toR2, Supersedes: "build/status",
	}, dest); err != nil {
		t.Fatal(err)
	}
	st1, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: n1.EventID}, dest)
	if err != nil || len(st1.Deliveries) != 1 || st1.Deliveries[0].State != "pending" {
		t.Fatalf("unrelated publish superseded n1: %+v %v", st1, err)
	}

	// Same sender + same topic + same recipient settles n1 as superseded with recorded reason and provenance.
	n2, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "review", Ref: ref, Destination: toR1, Supersedes: "build/status",
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	st1, err = sender.AgentEventStatus(ctx, EventStatusRequest{EventID: n1.EventID}, dest)
	if err != nil || st1.Deliveries[0].State != "ignored" || st1.Deliveries[0].Code != "superseded" ||
		st1.Deliveries[0].SupersededBy != n2.EventID {
		t.Fatalf("expected n1 pending delivery superseded by n2: %+v %v", st1, err)
	}
	r1View := f.view(sessionRef(r1))
	stats, err := r1View.AgentEventStats(ctx, EventQuery{}, dest)
	if err != nil || stats.Superseded != 1 || stats.Ignored != 1 {
		t.Fatalf("expected Superseded=1 in EventStats: %+v %v", stats, err)
	}

	// Lease nOther's delivery, then publish a newer notice from otherSender with the same topic: leased item is untouched.
	stOther, err := otherSender.AgentEventStatus(ctx, EventStatusRequest{EventID: nOther.EventID}, dest)
	if err != nil || len(stOther.Deliveries) != 1 {
		t.Fatalf("other status: %+v %v", stOther, err)
	}
	claimed, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{
		RequestID: uuid.NewString(), Session: sessionRef(r1), DeliveryID: stOther.Deliveries[0].DeliveryID,
		NativeTurnID: "turn-leased", TurnExclusive: true,
	}, dest)
	if err != nil || claimed.Attempt == nil {
		t.Fatalf("claim nOther: %+v %v", claimed, err)
	}
	if _, err := otherSender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "review", Ref: ref, Destination: toR1, Supersedes: "build/status",
	}, dest); err != nil {
		t.Fatal(err)
	}
	stOther, err = otherSender.AgentEventStatus(ctx, EventStatusRequest{EventID: nOther.EventID}, dest)
	if err != nil || stOther.Deliveries[0].State != "leased" || stOther.Deliveries[0].SupersededBy != "" {
		t.Fatalf("leased item changed on supersede: %+v %v", stOther, err)
	}
}

func TestNoticeIntentAutoSettleActionNoneWithoutModelTurn(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	sender, receiver, source, dest := f.sender, f.receiver, f.source, f.dest
	defaultAgent := f.register(nativeAgent{Binding: "auto-settle-off"})
	optInAgent := f.register(nativeAgent{Binding: "auto-settle-on"})
	srcRef := RecordVersionRef{RecordID: source.RecordID, Version: source.Version}

	// Non-opted-in binding keeps current behavior: action:none is claimed normally.
	offNotice, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Ref: srcRef,
		Destination: EventDestination{Type: "agent", Name: defaultAgent.Inbox}, Action: "none",
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	offReady, err := receiver.SessionInboxReady(ctx, sessionRef(defaultAgent), dest)
	if err != nil || offReady.EventID != offNotice.EventID || offReady.Action != "none" {
		t.Fatalf("unexpected readiness for default binding: %+v %v", offReady, err)
	}
	offClaim, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{
		RequestID: uuid.NewString(), Session: sessionRef(defaultAgent), NativeTurnID: "turn-off",
	}, dest)
	if err != nil || offClaim.Attempt == nil || offClaim.Attempt.Delivery.Event.EventID != offNotice.EventID {
		t.Fatalf("default binding failed to claim action:none notice normally: %+v %v", offClaim, err)
	}

	// Opted-in binding: SettleSessionActionNone settles consecutive head-of-queue action:none notices without a wake/attempt.
	toOpt := EventDestination{Type: "agent", Name: optInAgent.Inbox}
	optRef := sessionRef(optInAgent)
	none1, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Ref: srcRef, Destination: toOpt, Action: "none",
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	none2, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Ref: srcRef, Destination: toOpt, Action: "none",
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	review1, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Ref: srcRef, Destination: toOpt, Action: "review",
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	none3, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Ref: srcRef, Destination: toOpt, Action: "none",
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	decide1, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Ref: srcRef, Destination: toOpt, Action: "decide",
	}, dest)
	if err != nil {
		t.Fatal(err)
	}

	settleReq := SessionInboxSettleNone{RequestID: uuid.NewString(), Session: optRef}
	settled, err := receiver.SettleSessionActionNone(ctx, settleReq, dest)
	if err != nil || len(settled.Settled) != 2 || settled.Ready.EventID != review1.EventID || settled.Ready.Action != "review" {
		t.Fatalf("SettleSessionActionNone mismatch: %+v %v", settled, err)
	}
	replayed, err := receiver.SettleSessionActionNone(ctx, settleReq, dest)
	if err != nil || len(replayed.Settled) != 2 || replayed.Settled[0] != settled.Settled[0] || replayed.Ready.EventID != review1.EventID {
		t.Fatalf("SettleSessionActionNone replay mismatch: %+v %v", replayed, err)
	}
	for _, evID := range []string{none1.EventID, none2.EventID} {
		st, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: evID}, dest)
		if err != nil || st.Deliveries[0].State != "handled" || st.Deliveries[0].Code != "auto_settled_none" {
			t.Fatalf("expected handled/auto_settled_none for %s: %+v %v", evID, st, err)
		}
	}
	// History and Events still expose the auto-settled notices.
	optView := f.view(optRef)
	evPage, err := optView.Events(ctx, EventQuery{}, dest)
	if err != nil || len(evPage.Events) < 5 {
		t.Fatalf("auto-settled notices missing from Events history: %+v %v", evPage, err)
	}
	hist, err := optView.History(ctx, RecordHistoryRequest{RecordID: source.RecordID, Version: source.Version}, dest)
	if err != nil || len(hist.Versions) != 1 || hist.Versions[0].Body == nil || *hist.Versions[0].Body != source.Body {
		t.Fatalf("source version unreadable in History after auto-settle: %+v %v", hist, err)
	}

	// Claim review1 with batching + AutoSettleActionNone, complete it, then AdvanceSessionInbox skips none3 and lands on decide1!
	claimReq := SessionInboxClaim{
		RequestID: uuid.NewString(), Session: optRef, NativeTurnID: "turn-batch",
		AutoSettleActionNone: true,
	}
	claimRes, err := receiver.ClaimSessionInbox(ctx, claimReq, dest)
	if err != nil || claimRes.Attempt == nil || claimRes.Attempt.Delivery.Event.EventID != review1.EventID {
		t.Fatalf("claim review1: %+v %v", claimRes, err)
	}
	conflictClaim := claimReq
	conflictClaim.AutoSettleActionNone = false
	_, err = receiver.ClaimSessionInbox(ctx, conflictClaim, dest)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")

	if _, err := optView.CompleteEvent(ctx, CompleteEventRequest{
		RequestID: uuid.NewString(), DeliveryID: claimRes.Attempt.Delivery.DeliveryID,
		LeaseID: claimRes.Attempt.Delivery.LeaseID, Disposition: "handled",
	}, dest); err != nil {
		t.Fatal(err)
	}
	adv, err := receiver.AdvanceSessionInbox(ctx, SessionInboxAdvance{
		RequestID: uuid.NewString(), Session: optRef, AttemptID: claimRes.Attempt.ID,
		Version: 1, AutoSettleActionNone: true,
	}, dest)
	if err != nil || adv.Attempt == nil || adv.Attempt.Delivery.Event.EventID != decide1.EventID || adv.Attempt.BatchIndex != 2 {
		t.Fatalf("AdvanceSessionInbox failed to auto-settle none3 and advance to decide1: %+v %v", adv, err)
	}
	stNone3, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: none3.EventID}, dest)
	if err != nil || stNone3.Deliveries[0].State != "handled" || stNone3.Deliveries[0].Code != "auto_settled_none" {
		t.Fatalf("expected none3 auto-settled during batch advance: %+v %v", stNone3, err)
	}
}

func TestNoticeIntentSensitivityForgottenSourceTopicAndReplayGuards(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	sender, receiver, source, dest := f.sender, f.receiver, f.source, f.dest
	r1 := f.register(nativeAgent{Binding: "guard-r1"})
	r2 := f.register(nativeAgent{Binding: "guard-r2"})
	r1View := f.view(sessionRef(r1))
	srcRef := RecordVersionRef{RecordID: source.RecordID, Version: source.Version}

	// 1. Topic fanout: supersedes affects subscribed r1, not unsubscribed r2, and not r1 after unsubscribe.
	if _, err := r1View.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: "builds", Active: true}); err != nil {
		t.Fatal(err)
	}
	t1, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "review", Ref: srcRef,
		Destination: EventDestination{Type: "topic", Name: "builds"}, Supersedes: "builds/main",
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	t2, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "review", Ref: srcRef,
		Destination: EventDestination{Type: "topic", Name: "builds"}, Supersedes: "builds/main",
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	stT1, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: t1.EventID}, dest)
	if err != nil || len(stT1.Deliveries) != 1 || stT1.Deliveries[0].State != "ignored" || stT1.Deliveries[0].SupersededBy != t2.EventID {
		t.Fatalf("expected topic notice t1 superseded by t2: %+v %v", stT1, err)
	}
	if _, err := r1View.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: "builds", Active: false}); err != nil {
		t.Fatal(err)
	}
	if _, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "review", Ref: srcRef,
		Destination: EventDestination{Type: "topic", Name: "builds"}, Supersedes: "builds/main",
	}, dest); err != nil {
		t.Fatal(err)
	}
	stT2, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: t2.EventID}, dest)
	if err != nil || stT2.Deliveries[0].State != "pending" {
		t.Fatalf("unsubscribed recipient delivery should remain pending: %+v %v", stT2, err)
	}

	// 2. Cross-sensitivity: a local notice must never supersede a shareable pending notice.
	toR2 := EventDestination{Type: "agent", Name: r2.Inbox}
	sNotice, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "review", Ref: srcRef, Destination: toR2, Supersedes: "sec/lane",
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	localDraft := projectNote(f.repo)
	localDraft.Sensitivity = "local"
	localRec, err := sender.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: localDraft})
	if err != nil {
		t.Fatal(err)
	}
	localDest := Destination{Name: "local", AllowLocal: true}
	if _, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "review", Ref: RecordVersionRef{RecordID: localRec.RecordID, Version: 1},
		Destination: toR2, Supersedes: "sec/lane",
	}, localDest); err != nil {
		t.Fatal(err)
	}
	stShareable, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: sNotice.EventID}, dest)
	if err != nil || stShareable.Deliveries[0].State != "pending" || stShareable.Deliveries[0].SupersededBy != "" {
		t.Fatalf("local notice leaked/superseded shareable notice: %+v %v", stShareable, err)
	}

	// 3. SettleSessionActionNone cross-destination replay refuses with IDEMPOTENCY_CONFLICT; forgotten source settles failed/source_unavailable.
	forgetDraft := projectNote(f.repo)
	forgetDraft.Sensitivity = "shareable"
	forgetRec, err := sender.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: forgetDraft})
	if err != nil {
		t.Fatal(err)
	}
	fAgent := f.register(nativeAgent{Binding: "forget-autosettle"})
	fNotice, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{RecordID: forgetRec.RecordID, Version: 1},
		Destination: EventDestination{Type: "agent", Name: fAgent.Inbox}, Action: "none",
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	op, root := testOperator(t)
	preview, err := op.PreviewDeletion(ctx, forgetRec.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := op.Forget(ctx, ForgetRequest{
		RequestID: uuid.NewString(), RecordID: forgetRec.RecordID, ExpectedVersion: 1, GrantID: root.ID, PreviewID: preview.PreviewID,
	}); err != nil {
		t.Fatal(err)
	}
	settleID := uuid.NewString()
	fRes, err := receiver.SettleSessionActionNone(ctx, SessionInboxSettleNone{RequestID: settleID, Session: sessionRef(fAgent)}, localDest)
	if err != nil || len(fRes.Settled) != 0 {
		t.Fatalf("forgotten source should not be counted in Settled: %+v %v", fRes, err)
	}
	_, err = receiver.SettleSessionActionNone(ctx, SessionInboxSettleNone{RequestID: settleID, Session: sessionRef(fAgent)}, dest)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	stForgotten, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: fNotice.EventID}, localDest)
	if err != nil || stForgotten.Deliveries[0].State != "failed" || stForgotten.Deliveries[0].Code != "source_unavailable" {
		t.Fatalf("expected failed/source_unavailable for forgotten auto-settled notice: %+v %v", stForgotten, err)
	}
}

func TestNoticeIntentSafeAutoSettleGuardsAndIdempotency(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	sender, receiver, source, dest := f.sender, f.receiver, f.source, f.dest
	ag := f.register(nativeAgent{Binding: "safe-autosettle"})
	agRef := sessionRef(ag)
	to := EventDestination{Type: "agent", Name: ag.Inbox}
	op, _ := testOperator(t)

	// 1. >280B notice body with summary auto-settles safely once; idempotent retry preserves Settled.
	longDraft := projectNote(f.repo)
	longDraft.Sensitivity = "shareable"
	longDraft.Body = strings.Repeat("safe detail line\n", 25)
	longRec, err := sender.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: longDraft})
	if err != nil {
		t.Fatal(err)
	}
	safeEv, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "none",
		Summary:     "Concise summary for >280B action:none notice",
		Ref:         RecordVersionRef{RecordID: longRec.RecordID, Version: 1},
		Destination: to,
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	settleID := uuid.NewString()
	s1, err := receiver.SettleSessionActionNone(ctx, SessionInboxSettleNone{RequestID: settleID, Session: agRef}, dest)
	if err != nil || len(s1.Settled) != 1 || s1.Ready.DeliveryID != "" {
		t.Fatalf("expected safe >280B summarized notice to auto-settle: %+v %v", s1, err)
	}
	s1Replay, err := receiver.SettleSessionActionNone(ctx, SessionInboxSettleNone{RequestID: settleID, Session: agRef}, dest)
	if err != nil || len(s1Replay.Settled) != 1 || s1Replay.Settled[0] != s1.Settled[0] {
		t.Fatalf("expected idempotent settle replay: %+v %v", s1Replay, err)
	}
	stSafe, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: safeEv.EventID}, dest)
	if err != nil || stSafe.Deliveries[0].State != "handled" || stSafe.Deliveries[0].Code != "auto_settled_none" {
		t.Fatalf("expected handled/auto_settled_none: %+v %v", stSafe, err)
	}

	// 2. Legacy notice missing action, unknown policy version, truncated body, or >280B without summary NEVER auto-settles!
	truncDraft := projectNote(f.repo)
	truncDraft.Sensitivity = "shareable"
	truncDraft.Body = "Partial output\n[... truncated 50 of 200 UTF-8 bytes; pull full source with cairn-inbox read abcdef0123456789 ...]"
	truncRec, err := sender.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: truncDraft})
	if err != nil {
		t.Fatal(err)
	}
	truncEv, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "none",
		Summary:     "Notice pointing to truncated content",
		Ref:         RecordVersionRef{RecordID: truncRec.RecordID, Version: 1},
		Destination: to,
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("CAIRN_ALLOW_OPTIONAL_NOTICE_ACTION", "1")
	missingActionEv, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice",
		Ref: RecordVersionRef{RecordID: source.RecordID, Version: source.Version}, Destination: to,
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	noSummaryEv, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "none",
		Summary: "Temp summary", Ref: RecordVersionRef{RecordID: longRec.RecordID, Version: 1}, Destination: to,
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := op.pool.Exec(ctx, `UPDATE cairn.agent_event SET summary = NULL WHERE event_id = $1`, noSummaryEv.EventID); err != nil {
		t.Fatal(err)
	}

	for _, ev := range []AgentEvent{truncEv, missingActionEv, noSummaryEv} {
		// Neither SettleSessionActionNone nor SessionInboxDigestReady nor ClaimSessionInbox may auto-settle these!
		res, err := receiver.SettleSessionActionNone(ctx, SessionInboxSettleNone{RequestID: uuid.NewString(), Session: agRef}, dest)
		if err != nil || len(res.Settled) != 0 || res.Ready.EventID != ev.EventID {
			t.Fatalf("unsafe notice %s was auto-settled or hidden: %+v %v", ev.EventID, res, err)
		}
		claim, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{
			RequestID: uuid.NewString(), Session: agRef, NativeTurnID: "turn-" + ev.EventID, AutoSettleActionNone: true,
		}, dest)
		if err != nil || claim.Attempt == nil || claim.Attempt.Delivery.Event.EventID != ev.EventID {
			t.Fatalf("expected unsafe notice %s to remain claimable: %+v %v", ev.EventID, claim, err)
		}
		if _, err := f.view(agRef).CompleteEvent(ctx, CompleteEventRequest{
			RequestID: uuid.NewString(), DeliveryID: claim.Attempt.Delivery.DeliveryID,
			LeaseID: claim.Attempt.Delivery.LeaseID, Disposition: "handled",
		}, dest); err != nil {
			t.Fatal(err)
		}
		if _, err := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{
			RequestID: uuid.NewString(), Session: agRef, AttemptID: claim.Attempt.ID, Reason: "delivery_completed",
		}, dest); err != nil {
			t.Fatal(err)
		}
	}
}
