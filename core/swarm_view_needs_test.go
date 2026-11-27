package core

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

const swarmNeedsTestBoard = "Choose the cooler setting?\n\n```board-card\n" +
	`{"shape":"question","verbs":[{"id":"no","label":"Leave it alone"}],"default":"no","ttl":600}` + "\n```\n"

func swarmNeedsTestPublish(f *swarmFixture, from *Store, kind, destType, dest, body, cause string) AgentEvent {
	f.t.Helper()
	return swarmNeedsTestPublishRequest(f, from, body, PublishEventRequest{
		Kind: kind, Destination: EventDestination{destType, dest}, CausationID: cause,
	})
}

func swarmNeedsTestPublishRequest(f *swarmFixture, from *Store, body string, req PublishEventRequest) AgentEvent {
	f.t.Helper()
	ctx := context.Background()
	draft := projectNote(f.repo)
	draft.Body, draft.Sensitivity = body, "shareable"
	source, err := from.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		f.t.Fatal(err)
	}
	req.RequestID, req.Repo = uuid.NewString(), f.repo
	req.Ref = RecordVersionRef{source.RecordID, source.Version}
	if req.Kind == "notice" {
		req.Action = "review"
	}
	event, err := from.PublishEvent(ctx, req, f.dest)
	if err != nil {
		f.t.Fatal(err)
	}
	return event
}

func swarmNeedsTestDelivery(f *swarmFixture, event AgentEvent) string {
	f.t.Helper()
	var id string
	err := f.op.pool.QueryRow(context.Background(), `SELECT delivery_id::text FROM cairn.agent_delivery WHERE event_id=$1`, event.EventID).Scan(&id)
	if err != nil {
		f.t.Fatal(err)
	}
	return id
}

func swarmNeedsTestSnapshot(f *swarmFixture) SwarmNeedsYou {
	f.t.Helper()
	snap, err := f.op.GetSwarmSnapshot(context.Background(), SwarmSnapshotRequest{Repo: f.repo, Campaign: f.campaign})
	if err != nil || len(snap.SectionErrors) != 0 || len(snap.NeedsYou.Unavailable) != 0 {
		f.t.Fatalf("snapshot err=%v sections=%v unavailable=%+v", err, snap.SectionErrors, snap.NeedsYou.Unavailable)
	}
	return snap.NeedsYou
}

func swarmNeedsTestLanding(f *swarmFixture) SwarmNeedsYou {
	f.t.Helper()
	landing, err := f.op.GetSwarmLanding(context.Background(), f.repo)
	if err != nil || len(landing.SectionErrors) != 0 || len(landing.NeedsYou.Unavailable) != 0 {
		f.t.Fatalf("landing err=%v sections=%v unavailable=%+v", err, landing.SectionErrors, landing.NeedsYou.Unavailable)
	}
	return landing.NeedsYou
}

func swarmNeedsTestItem(t *testing.T, needs SwarmNeedsYou, kind, subject, action string, since time.Time) SwarmNeed {
	t.Helper()
	for _, item := range needs.Items {
		if item.Type == kind && item.Subject == subject {
			var wantPrincipal string
			switch kind {
			case SwarmNeedHandoffDue, SwarmNeedMissedCheckpoint, SwarmNeedWaitTimedOut,
				SwarmNeedDecision, SwarmNeedEscalation, SwarmNeedWorkstreamDecision:
				wantPrincipal = subject
			case SwarmNeedOperatorDelivery:
				if action == "reply" {
					wantPrincipal = subject
				}
			}
			if item.Refs.Principal != wantPrincipal {
				t.Fatalf("%s follow-up principal is %q, want %q", kind, item.Refs.Principal, wantPrincipal)
			}
			if item.ID == "" || item.Title == "" || !item.Since.Equal(since) || item.Action.Kind != action ||
				item.Action.Label == "" {
				t.Fatalf("wrong needs row: %+v, want subject %s action %s since %s", item, subject, action, since)
			}
			return item
		}
	}
	t.Fatalf("missing %s for %s in %+v", kind, subject, needs)
	return SwarmNeed{}
}

func swarmNeedsTestAbsent(t *testing.T, needs SwarmNeedsYou, kind string) {
	t.Helper()
	for _, item := range needs.Items {
		if item.Type == kind {
			t.Fatalf("unexpected %s: %+v", kind, item)
		}
	}
}

func swarmNeedsTestLedger(f *swarmFixture, kind, key, owner, status string, revision int64) CampaignLedgerItem {
	f.t.Helper()
	item, err := f.rootView.SetCampaignLedgerItem(context.Background(), SetCampaignLedgerItemRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: f.campaign, Kind: kind, ItemKey: key,
		Owner: owner, Status: status, Summary: "Need " + key, ExpectedRevision: revision,
	}, f.dest)
	if err != nil {
		f.t.Fatal(err)
	}
	return item
}

func TestSwarmNeedsBoardQuestionOnlyOwnerAction(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "attention")
	lead, _ := f.session("attention-lead")
	if _, err := f.rootView.SetTeamMember(ctx, SetTeamMemberRequest{RequestID: uuid.NewString(),
		Repo: f.repo, Campaign: f.campaign, Member: lead.Inbox, Lead: f.root.Inbox, Role: "lead"}, f.dest); err != nil {
		t.Fatal(err)
	}
	board := swarmNeedsTestPublish(f, f.rootView, "request", "topic", "agent-board", swarmNeedsTestBoard, "")
	decision := swarmNeedsTestLedger(f, "decision", "lead-judgment", lead.Inbox, "open", 0)
	request := swarmNeedsTestPublish(f, f.rootView, "request", "agent", lead.Inbox, "Do a thing", "")
	delivery := swarmNeedsTestDelivery(f, request)
	if _, err := f.op.CancelWork(ctx, CancelWorkRequest{Repo: f.repo, RequestID: uuid.NewString(), DeliveryID: delivery, Reason: "Owner no longer needs it"}); err != nil {
		t.Fatal(err)
	}
	needs := swarmNeedsTestSnapshot(f)
	want := swarmNeedsTestItem(t, needs, SwarmNeedBoardQuestion, f.root.Inbox, "answer_board", board.CreatedAt)
	if len(needs.Items) != 1 || want.Refs.EventID != board.EventID || want.Refs.Record == nil ||
		*want.Refs.Record != board.Ref || want.ExpiresAt == nil || !want.ExpiresAt.Equal(board.CreatedAt.Add(10*time.Minute)) ||
		want.Title != "Board question from "+f.root.DisplayName || want.Detail != "Unattended default declared" {
		t.Fatalf("board-only needs rows: %+v and %+v", needs, want)
	}
	if got := needs.LeadDecisions[lead.Inbox]; len(got) != 1 || got[0].ItemKey != decision.ItemKey ||
		got[0].Summary != decision.Summary || !got[0].UpdatedAt.Equal(decision.UpdatedAt) {
		t.Fatalf("lead decisions: %+v", needs.LeadDecisions)
	}
	if len(swarmNeedsTestLanding(f).Items) != 0 {
		t.Fatal("campaign signals leaked to landing")
	}
}

func TestSwarmNeedsBoardExpiryAndDefault(t *testing.T) {
	for _, page := range []string{"campaign", "landing"} {
		t.Run(page, func(t *testing.T) {
			f := newSwarmFixture(t, "board-expiry")
			publisher, view := f.root, f.rootView
			read := swarmNeedsTestSnapshot
			if page == "landing" {
				publisher, view = f.session("outside-board")
				read = swarmNeedsTestLanding
			}
			if needs := read(f); needs.Items == nil || len(needs.Items) != 0 {
				t.Fatalf("initially empty: %+v", needs)
			}
			assertAbsentFromBoth := func() {
				t.Helper()
				swarmNeedsTestAbsent(t, swarmNeedsTestSnapshot(f), SwarmNeedBoardQuestion)
				swarmNeedsTestAbsent(t, swarmNeedsTestLanding(f), SwarmNeedBoardQuestion)
			}

			expired := swarmNeedsTestPublish(f, view, "request", "topic", "agent-board", swarmNeedsTestBoard, "")
			swarmNeedsTestItem(t, read(f), SwarmNeedBoardQuestion, publisher.Inbox, "answer_board", expired.CreatedAt)
			f.exec(`UPDATE cairn.agent_event SET created_at=clock_timestamp()-interval '11 minutes' WHERE event_id=$1`, expired.EventID)
			assertAbsentFromBoth()

			if _, err := f.sender.SetSubscription(context.Background(), SubscriptionRequest{
				RequestID: uuid.NewString(), Repo: f.repo, Topic: "agent-board", Active: true,
			}); err != nil {
				t.Fatal(err)
			}
			for _, source := range []string{"touch", "default"} {
				answered := swarmNeedsTestPublish(f, view, "request", "topic", "agent-board", swarmNeedsTestBoard, "")
				swarmNeedsTestItem(t, read(f), SwarmNeedBoardQuestion, publisher.Inbox, "answer_board", answered.CreatedAt)
				swarmNeedsTestPublish(f, f.sender, "response", "agent", publisher.Inbox, "Board answer: Leave it alone\n\n"+
					"```board-answer\n"+`{"verb":"no","source":"`+source+`","request_event":"`+answered.EventID+`"}`+"\n```", answered.EventID)
				assertAbsentFromBoth()
			}
		})
	}
}

func TestSwarmNeedsLedgerAndHandoff(t *testing.T) {
	f := newSwarmFixture(t, "ledger-attention")
	worker, _ := f.member("attention-worker")
	decision := swarmNeedsTestLedger(f, "decision", "root-decision", f.root.Inbox, "open", 0)
	escalation := swarmNeedsTestLedger(f, "blocker", "escalation", worker.Inbox, "escalated", 0)
	needs := swarmNeedsTestSnapshot(f)
	if got := swarmNeedsTestItem(t, needs, SwarmNeedDecision, f.root.Inbox, "decide", decision.UpdatedAt); got.Refs.LedgerKey != decision.ItemKey || got.Refs.LedgerKind != decision.Kind {
		t.Fatalf("decision refs: %+v", got)
	}
	if got := swarmNeedsTestItem(t, needs, SwarmNeedEscalation, worker.Inbox, "decide", escalation.UpdatedAt); got.Refs.LedgerKey != escalation.ItemKey || got.Refs.LedgerKind != escalation.Kind {
		t.Fatalf("escalation refs: %+v", got)
	}
	swarmNeedsTestLedger(f, "decision", decision.ItemKey, f.root.Inbox, "settled", decision.Revision)
	swarmNeedsTestLedger(f, "blocker", escalation.ItemKey, worker.Inbox, "resolved", escalation.Revision)
	start := time.Now().UTC().Add(-5 * time.Hour).Truncate(time.Microsecond)
	f.exec(`UPDATE cairn.agent_session SET started_at=$2 WHERE agent_id=$1`, f.root.AgentID, start)
	snap, err := f.op.GetSwarmSnapshot(context.Background(), SwarmSnapshotRequest{Repo: f.repo, Campaign: f.campaign})
	if err != nil || snap.Handoff == nil || snap.Handoff.Since == nil {
		t.Fatalf("handoff: %+v %v", snap.Handoff, err)
	}
	crossed := start.Add(time.Duration(DefaultMaxCoordinatorAgeSeconds) * time.Second)
	handoff := swarmNeedsTestItem(t, snap.NeedsYou, SwarmNeedHandoffDue, f.root.Inbox, "hand_off", crossed)
	if handoff.Detail != strings.Join(snap.Handoff.Reasons, ". ") {
		t.Fatalf("handoff reasons: %+v", handoff)
	}
	swarmNeedsTestAbsent(t, snap.NeedsYou, SwarmNeedDecision)
	swarmNeedsTestAbsent(t, snap.NeedsYou, SwarmNeedEscalation)
	f.exec(`UPDATE cairn.agent_session SET started_at=clock_timestamp() WHERE agent_id=$1`, f.root.AgentID)
	swarmNeedsTestAbsent(t, swarmNeedsTestSnapshot(f), SwarmNeedHandoffDue)
}

func TestSwarmNeedsHandoffCompactionTimestampWhenRecorded(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "handoff-compaction")
	setThreshold := func(limit int) {
		t.Helper()
		if _, err := f.rootView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
			RequestID: uuid.NewString(), Repo: f.repo, Campaign: f.campaign,
			MaxCoordinatorAgeSeconds: intPtr(0), MaxCoordinatorCompactions: &limit,
		}, f.dest); err != nil {
			t.Fatal(err)
		}
	}
	compact := func() {
		t.Helper()
		if _, err := f.rootView.RecordCoordinatorCompaction(ctx, RecordCoordinatorCompactionRequest{
			RequestID: uuid.NewString(), Repo: f.repo, Campaign: f.campaign,
		}, f.dest); err != nil {
			t.Fatal(err)
		}
	}
	assertWireSince := func(at time.Time, present bool) {
		t.Helper()
		row := swarmNeedsTestItem(t, swarmNeedsTestSnapshot(f), SwarmNeedHandoffDue, f.root.Inbox, "hand_off", at)
		wire, err := json.Marshal(row)
		if err != nil {
			t.Fatal(err)
		}
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(wire, &fields); err != nil {
			t.Fatal(err)
		}
		value, ok := fields["since"]
		if ok != present {
			t.Fatalf("handoff since presence wanted %t, got %s", present, wire)
		}
		if ok {
			var got time.Time
			if err := json.Unmarshal(value, &got); err != nil || !got.Equal(at) {
				t.Errorf("since was %s, want %s (%v)", got, at, err)
			}
		}
	}

	setThreshold(1)
	compact()
	var first time.Time
	if err := f.op.pool.QueryRow(ctx, `SELECT first_compacted_at FROM cairn.agent_session WHERE agent_id=$1`, f.root.AgentID).Scan(&first); err != nil {
		t.Fatal(err)
	}
	assertWireSince(first, true)

	setThreshold(2)
	compact()
	assertWireSince(time.Time{}, false)

	// Older sessions can have a compaction count without a stored timestamp.
	setThreshold(1)
	f.exec(`UPDATE cairn.agent_session SET first_compacted_at=NULL WHERE agent_id=$1`, f.root.AgentID)
	assertWireSince(time.Time{}, false)
}

func TestSwarmNeedsOperatorDeliveryAndWorkstream(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "operator-attention")
	member, view := f.member("stream-owner")
	request := swarmNeedsTestPublish(f, view, "request", "agent", f.op.channel.Principal, "Owner task", "")
	delivery := swarmNeedsTestDelivery(f, request)
	statusReq := SetWorkstreamStatusRequest{RequestID: uuid.NewString(), Repo: f.repo, Stream: "release",
		State: "blocked", DecisionNeeded: "Choose which release to pursue"}
	ws, err := view.SetWorkstreamStatus(ctx, statusReq, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	needs := swarmNeedsTestSnapshot(f)
	if got := swarmNeedsTestItem(t, needs, SwarmNeedOperatorDelivery, member.Inbox, "reply", request.CreatedAt); got.Refs.EventID != request.EventID ||
		len(got.Refs.DeliveryIDs) != 1 || got.Refs.DeliveryIDs[0] != delivery || got.Refs.Record == nil || *got.Refs.Record != request.Ref {
		t.Fatalf("operator refs: %+v", got)
	}
	if got := swarmNeedsTestItem(t, needs, SwarmNeedWorkstreamDecision, member.Inbox, "decide", ws.UpdatedAt); got.Refs.Stream != ws.Stream || got.Detail != ws.DecisionNeeded {
		t.Fatalf("workstream refs: %+v", got)
	}
	if len(swarmNeedsTestLanding(f).Items) != 0 {
		t.Fatal("campaign needs appeared on landing")
	}
	f.exec(`UPDATE cairn.agent_delivery SET state='handled',completed_at=clock_timestamp() WHERE delivery_id=$1`, delivery)
	statusReq.RequestID, statusReq.State, statusReq.DecisionNeeded, statusReq.ExpectedRevision = uuid.NewString(), "active", "", ws.Revision
	if _, err := view.SetWorkstreamStatus(ctx, statusReq, f.dest); err != nil {
		t.Fatal(err)
	}
	needs = swarmNeedsTestSnapshot(f)
	swarmNeedsTestAbsent(t, needs, SwarmNeedOperatorDelivery)
	swarmNeedsTestAbsent(t, needs, SwarmNeedWorkstreamDecision)
}

func TestSwarmNeedsOwnerTurnFreshness(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "owner-turn")
	a, _ := f.member("turn-needed")
	at := time.Now().UTC().Truncate(time.Microsecond)
	obs, err := f.receiver.ObserveSessionDelivery(ctx, SessionDeliveryObservation{
		Session: sessionRef(a), Condition: "owner_turn_required", ObservedAt: at,
	}, f.dest)
	if err != nil || !obs.Applied {
		t.Fatalf("observe owner turn: %+v %v", obs, err)
	}
	swarmNeedsTestAbsent(t, swarmNeedsTestSnapshot(f), SwarmNeedOwnerTurn)
	event := swarmNeedsTestPublish(f, f.rootView, "request", "agent", a.Inbox, "Work awaiting an owner turn", "")
	delivery := swarmNeedsTestDelivery(f, event)
	if got := swarmNeedsTestItem(t, swarmNeedsTestSnapshot(f), SwarmNeedOwnerTurn, a.Inbox, "take_turn", at); got.Refs.Code != "owner_turn_required" ||
		got.Refs.EventID != event.EventID || got.Refs.DeliveryIDs[0] != delivery {
		t.Fatalf("owner turn refs: %+v", got)
	}
	f.staleObservationReceipt(a.AgentID)
	swarmNeedsTestAbsent(t, swarmNeedsTestSnapshot(f), SwarmNeedOwnerTurn)

	obs, err = f.receiver.ObserveSessionDelivery(ctx, SessionDeliveryObservation{
		Session: sessionRef(a), Condition: "owner_turn_required", ObservedAt: time.Now().UTC(),
	}, f.dest)
	if err != nil || !obs.Applied {
		t.Fatalf("refresh owner turn: %+v %v", obs, err)
	}
	f.staleObservationHostTime(a.AgentID)
	swarmNeedsTestAbsent(t, swarmNeedsTestSnapshot(f), SwarmNeedOwnerTurn)

	obs, err = f.receiver.ObserveSessionDelivery(ctx, SessionDeliveryObservation{
		Session: sessionRef(a), Condition: "owner_turn_required", ObservedAt: time.Now().UTC(),
	}, f.dest)
	if err != nil || !obs.Applied {
		t.Fatalf("refresh owner turn: %+v %v", obs, err)
	}
	f.fenceObservationGeneration(a.AgentID)
	swarmNeedsTestAbsent(t, swarmNeedsTestSnapshot(f), SwarmNeedOwnerTurn)

	obs, err = f.receiver.ObserveSessionDelivery(ctx, SessionDeliveryObservation{
		Session: sessionRef(a), Condition: "owner_turn_required", ObservedAt: time.Now().UTC(),
	}, f.dest)
	if err != nil || !obs.Applied {
		t.Fatalf("refresh owner turn after fence: %+v %v", obs, err)
	}
	f.exec(`UPDATE cairn.agent_delivery SET state='handled',completed_at=clock_timestamp() WHERE delivery_id=$1`, delivery)
	swarmNeedsTestAbsent(t, swarmNeedsTestSnapshot(f), SwarmNeedOwnerTurn)
}

func TestSwarmNeedsFailedDeliveryGroups(t *testing.T) {
	f := newSwarmFixture(t, "failed-deliveries")
	a, _ := f.member("failed-attempts")
	at := time.Now().UTC().Add(-time.Minute).Truncate(time.Microsecond)
	codes := []string{"processing_failed", "processing_failed", "task_deadline", "operator_cancelled", "operator_released", "consumer_retired"}
	events, ids := make([]AgentEvent, len(codes)), make([]string, len(codes))
	for i, code := range codes {
		summary := ""
		if i < 2 {
			summary = "Process material"
		}
		events[i] = swarmNeedsTestPublishRequest(f, f.rootView, "Note body should not title the failure", PublishEventRequest{
			Kind: "request", Destination: EventDestination{"agent", a.Inbox}, Summary: summary,
		})
		ids[i] = swarmNeedsTestDelivery(f, events[i])
		f.exec(`UPDATE cairn.agent_delivery SET state='failed',code=$2,completed_at=$3 WHERE delivery_id=$1`,
			ids[i], code, at.Add(time.Duration(i)*time.Second))
	}
	needs := swarmNeedsTestSnapshot(f)
	if len(needs.Items) != 2 {
		t.Fatalf("failure groups: %+v", needs.Items)
	}
	for i, expected := range []struct {
		first, count int
	}{{0, 2}, {2, 1}} {
		item := swarmNeedsTestItem(t, SwarmNeedsYou{Items: []SwarmNeed{needs.Items[i]}}, SwarmNeedFailedDelivery,
			a.Inbox, "inspect", at.Add(time.Duration(expected.first)*time.Second))
		wantTitle := "Process material to " + a.DisplayName + " (+1 more)"
		if expected.first == 2 {
			wantTitle = "request " + events[2].EventID + " to " + a.DisplayName
		}
		if item.Title != wantTitle || item.Count != expected.count || item.Refs.Code != codes[expected.first] || item.Refs.EventID != events[expected.first].EventID ||
			strings.Join(item.Refs.DeliveryIDs, ",") != strings.Join(ids[expected.first:expected.first+expected.count], ",") {
			t.Fatalf("failure group: %+v", item)
		}
	}
	next, err := f.op.ReissueEvent(context.Background(), ReissueEventRequest{Repo: f.repo, RequestID: uuid.NewString(),
		DeliveryID: ids[0], Reason: "Prior effects reviewed", AcceptUncertainEffects: true})
	if err != nil {
		t.Fatalf("reissue: %+v %v", next, err)
	}
	reissued := swarmNeedsTestSnapshot(f)
	if len(reissued.Items) != 2 || reissued.Items[0].ID != needs.Items[0].ID || reissued.Items[0].Count != 1 ||
		reissued.Items[0].Refs.DeliveryIDs[0] != ids[1] {
		t.Fatalf("group should retain identity after reissuing one delivery: %+v", reissued)
	}
	f.exec(`UPDATE cairn.agent_delivery SET state='handled',code='' WHERE delivery_id=ANY($1::uuid[])`, ids[1:3])
	swarmNeedsTestAbsent(t, swarmNeedsTestSnapshot(f), SwarmNeedFailedDelivery)
}

func TestSwarmNeedsStarvationAndStoppedBacklog(t *testing.T) {
	f := newSwarmFixture(t, "stalled-agents")
	idle, _ := f.member("stalled-idle")
	busy := f.register(nativeAgent{Binding: "stalled-busy", State: "busy"})
	f.join(busy)
	older := time.Now().UTC().Add(-12 * time.Minute).Truncate(time.Microsecond)
	idleEvent := swarmNeedsTestPublish(f, f.rootView, "request", "agent", idle.Inbox, "Idle inbox work", "")
	busyEvent := swarmNeedsTestPublish(f, f.rootView, "request", "agent", busy.Inbox, "Busy inbox work", "")
	idleID, busyID := swarmNeedsTestDelivery(f, idleEvent), swarmNeedsTestDelivery(f, busyEvent)
	f.exec(`UPDATE cairn.agent_delivery SET available_at=$2 WHERE delivery_id=ANY($1::uuid[])`, []string{idleID, busyID}, older)
	needs := swarmNeedsTestSnapshot(f)
	stalled := swarmNeedsTestItem(t, needs, SwarmNeedStalledAgent, idle.Inbox, "inspect", older)
	if len(needs.Items) != 1 || stalled.Refs.EventID != idleEvent.EventID || stalled.Refs.DeliveryIDs[0] != idleID ||
		stalled.Refs.Code != "pending_starved" {
		t.Fatalf("idle queue at 12 minutes: %+v", needs)
	}

	busyOlder := older.Add(-20 * time.Minute)
	f.exec(`UPDATE cairn.agent_delivery SET available_at=$2 WHERE delivery_id=$1`, busyID, busyOlder)
	swarmNeedsTestItem(t, swarmNeedsTestSnapshot(f), SwarmNeedStalledAgent, busy.Inbox, "inspect", busyOlder)

	// A handled delivery that completed recently suppresses pending starvation
	// for that inbox. Leaving the session makes its unresolved work stranded.
	progress := swarmNeedsTestPublish(f, f.rootView, "notice", "agent", idle.Inbox, "Recent progress", "")
	progressID := swarmNeedsTestDelivery(f, progress)
	f.exec(`UPDATE cairn.agent_delivery SET state='handled',completed_at=clock_timestamp() WHERE delivery_id=$1`, progressID)
	needs = swarmNeedsTestSnapshot(f)
	if len(needs.Items) != 1 || needs.Items[0].Subject != busy.Inbox {
		t.Fatalf("recent handling suppression: %+v", needs)
	}
	f.stopAgo(idle, "1 minute")
	stopped := swarmNeedsTestItem(t, swarmNeedsTestSnapshot(f), SwarmNeedStoppedBacklog, idle.Inbox, "inspect", older)
	if stopped.Refs.EventID != idleEvent.EventID || stopped.Refs.DeliveryIDs[0] != idleID || stopped.Refs.Code != "recipient_stopped" {
		t.Fatalf("stopped refs: %+v", stopped)
	}
	f.exec(`UPDATE cairn.agent_delivery SET state='handled',completed_at=clock_timestamp() WHERE delivery_id=ANY($1::uuid[])`, []string{idleID, busyID})
	needs = swarmNeedsTestSnapshot(f)
	swarmNeedsTestAbsent(t, needs, SwarmNeedStoppedBacklog)
	swarmNeedsTestAbsent(t, needs, SwarmNeedStalledAgent)
}

func TestSwarmNeedsHoldStalled(t *testing.T) {
	f := newSwarmFixture(t, "held-agent")
	a, _ := f.member("held-session")
	event := swarmNeedsTestPublish(f, f.rootView, "request", "agent", a.Inbox, "Held work", "")
	delivery := swarmNeedsTestDelivery(f, event)
	claim := f.claim(sessionRef(a), delivery, "held-turn", true)
	at := time.Now().UTC().Add(-61 * time.Minute).Truncate(time.Microsecond)
	f.exec(`UPDATE cairn.agent_session_attempt SET created_at=$2 WHERE attempt_id=$1`, claim.Attempt.ID, at)
	got := swarmNeedsTestItem(t, swarmNeedsTestSnapshot(f), SwarmNeedStalledAgent, a.Inbox, "inspect", at)
	if got.Refs.Code != "hold_stalled" || got.Refs.EventID != event.EventID || got.Refs.DeliveryIDs[0] != delivery {
		t.Fatalf("hold refs: %+v", got)
	}
	f.exec(`UPDATE cairn.agent_session_attempt SET created_at=clock_timestamp() WHERE attempt_id=$1`, claim.Attempt.ID)
	swarmNeedsTestAbsent(t, swarmNeedsTestSnapshot(f), SwarmNeedStalledAgent)
}

func swarmNeedsTestWait(f *swarmFixture, a AgentInstance, eventID, state string, resolved *time.Time) string {
	f.t.Helper()
	id := uuid.NewString()
	reason, predicate := "", "request_response"
	if state == "timed_out" {
		reason = "timeout"
	}
	f.exec(`INSERT INTO cairn.agent_wait (wait_uuid,collection,owner_principal,owner_agent_uuid,creation_execution_uuid,
		registration_destination,sensitivity_ceiling,profile_binding_id,predicate_kind,dependency_event_uuid,deadline,
		generation,state,resolved_at,terminal_reason,reserved_notice_event_uuid,reserved_source_record_uuid)
		VALUES ($1,$2,$3,$4,$5,'{"name":"hosted","allow_local":false}','shareable','fixture',$6,$7,
		COALESCE($9,clock_timestamp()+interval '1 day'),$8,$10,$9,$11,$12,$13)`,
		id, f.repo, a.Inbox, a.AgentID, a.ExecutionID, predicate, eventID, a.DatabaseGeneration, resolved, state, reason, uuid.NewString(), uuid.NewString())
	return id
}

func TestSwarmNeedsTimedOutWait(t *testing.T) {
	f := newSwarmFixture(t, "timed-out-wait")
	a, _ := f.member("wait-owner")
	request := swarmNeedsTestPublish(f, f.rootView, "request", "agent", a.Inbox, "Waiting for evidence", "")
	at := time.Now().UTC().Add(-time.Hour).Truncate(time.Microsecond)
	waitID := swarmNeedsTestWait(f, a, request.EventID, "timed_out", &at)
	got := swarmNeedsTestItem(t, swarmNeedsTestSnapshot(f), SwarmNeedWaitTimedOut, a.Inbox, "inspect", at)
	if got.Refs.WaitID != waitID || got.Refs.EventID != request.EventID || got.Refs.Code != "timeout" {
		t.Fatalf("wait refs: %+v", got)
	}
	f.exec(`UPDATE cairn.agent_wait SET resolved_at=clock_timestamp()-interval '25 hours' WHERE wait_uuid=$1`, waitID)
	swarmNeedsTestAbsent(t, swarmNeedsTestSnapshot(f), SwarmNeedWaitTimedOut)
}

func TestSwarmNeedsMissedCheckpoint(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "missed-checkpoint")
	a, view := f.member("checkpoint-writer")
	if _, err := f.rootView.SetSubscription(ctx, SubscriptionRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Topic: "workstream-overdue", Active: true,
	}); err != nil {
		t.Fatal(err)
	}
	at := time.Now().UTC().Add(-5 * time.Minute).Truncate(time.Second)
	req := SetWorkstreamStatusRequest{RequestID: uuid.NewString(), Repo: f.repo, Stream: "delivery", State: "active", NextCheckpoint: &at}
	set, err := view.SetWorkstreamStatus(ctx, req, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	out, err := f.op.SweepOverdueDeadlines(ctx, OverdueSweepRequest{Repo: f.repo, GraceSeconds: intPtr(0)})
	if err != nil || len(out.Published) != 1 {
		t.Fatalf("emit overdue checkpoint: %+v %v", out, err)
	}
	got := swarmNeedsTestItem(t, swarmNeedsTestSnapshot(f), SwarmNeedMissedCheckpoint, a.Inbox, "inspect", at)
	if got.Refs.Stream != req.Stream || got.Refs.EventID != out.Published[0].EventID || got.Refs.Record == nil {
		t.Fatalf("overdue refs: %+v", got)
	}
	req.RequestID, req.ExpectedRevision, req.NextCheckpoint = uuid.NewString(), set.Revision, nil
	if _, err := view.SetWorkstreamStatus(ctx, req, f.dest); err != nil {
		t.Fatal(err)
	}
	swarmNeedsTestAbsent(t, swarmNeedsTestSnapshot(f), SwarmNeedMissedCheckpoint)
	var emitted int
	if err := f.op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_overdue_checkpoint WHERE repo=$1 AND state='emitted'`, f.repo).Scan(&emitted); err != nil || emitted != 1 {
		t.Fatalf("snapshot changed the emitted checkpoint: %d %v", emitted, err)
	}
}

func TestSwarmNeedsScopeAndDirectWaiterOrder(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "scope-and-order")
	a, outside := f.session("out-of-campaign")
	b, _ := f.session("other-waiter")
	c, _ := f.member("member-waiter")
	d, _ := f.member("other-member-waiter")
	outsideOld := swarmNeedsTestPublish(f, outside, "request", "topic", "agent-board", swarmNeedsTestBoard, "")
	outsideNew := swarmNeedsTestPublish(f, outside, "request", "topic", "agent-board", swarmNeedsTestBoard, "")
	insideOld := swarmNeedsTestPublish(f, f.rootView, "request", "topic", "agent-board", swarmNeedsTestBoard, "")
	insideNew := swarmNeedsTestPublish(f, f.rootView, "request", "topic", "agent-board", swarmNeedsTestBoard, "")
	swarmNeedsTestWait(f, a, outsideNew.EventID, "waiting", nil)
	swarmNeedsTestWait(f, b, outsideNew.EventID, "waiting", nil)
	swarmNeedsTestWait(f, c, insideNew.EventID, "waiting", nil)
	swarmNeedsTestWait(f, d, insideNew.EventID, "waiting", nil)
	operator := swarmNeedsTestPublish(f, outside, "request", "agent", f.op.channel.Principal, "Action outside the campaign", "")
	outsideRequest := swarmNeedsTestPublish(f, outside, "request", "agent", b.Inbox, "Pending outside the campaign", "")
	outsideDelivery := swarmNeedsTestDelivery(f, outsideRequest)
	f.exec(`UPDATE cairn.agent_delivery SET state='failed',code='processing_failed',completed_at=clock_timestamp() WHERE delivery_id=$1`, outsideDelivery)
	if _, err := outside.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{RequestID: uuid.NewString(),
		Repo: f.repo, Stream: "outside", State: "blocked", DecisionNeeded: "Pick outside option"}, f.dest); err != nil {
		t.Fatal(err)
	}
	swarmNeedsTestPublish(f, outside, "request", "agent", b.Inbox, "Still pending outside the campaign", "")
	if _, err := f.receiver.ObserveSessionDelivery(ctx, SessionDeliveryObservation{
		Session: sessionRef(b), Condition: "owner_turn_required", ObservedAt: time.Now().UTC(),
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	// GetSwarmLanding reads current waiters itself. The campaign uses the flow
	// it is handed, so feed it the stored waits independently of the flow slice.
	landing := swarmNeedsTestLanding(f)
	if len(landing.Items) != 6 || landing.Items[0].Refs.EventID != outsideNew.EventID || landing.Items[0].Waiters != 2 {
		t.Fatalf("landing scope/order: %+v", landing.Items)
	}
	found := map[string]bool{}
	for _, item := range landing.Items {
		found[item.Refs.EventID] = true
	}
	if !found[outsideOld.EventID] || !found[outsideRequest.EventID] || !found[operator.EventID] || found[insideOld.EventID] || found[insideNew.EventID] {
		t.Fatalf("landing refs: %+v", landing.Items)
	}
	campaign := swarmNeedsTestSnapshot(f)
	if len(campaign.Items) != 2 {
		t.Fatalf("campaign scope: %+v", campaign.Items)
	}
	tx, err := f.op.beginSwarmRead(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	sc, _, _, err := f.op.swarmScopeTx(ctx, tx, f.repo, f.campaign)
	if err != nil {
		t.Fatal(err)
	}
	rows, err := tx.Query(ctx, `SELECT owner_principal,dependency_event_uuid::text,state FROM cairn.agent_wait
		WHERE collection=$1 AND dependency_event_uuid=$2::uuid`, f.repo, insideNew.EventID)
	if err != nil {
		t.Fatal(err)
	}
	waits, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (SwarmWait, error) {
		var w SwarmWait
		err := row.Scan(&w.Owner, &w.DependencyEventID, &w.State)
		return w, err
	})
	if err != nil {
		t.Fatal(err)
	}
	// A second predicate from the same session remains one waiter.
	waits = append(waits, SwarmWait{Owner: c.Inbox, State: "waiting", DependencyEventID: insideNew.EventID, Predicate: "response_group_closed"})
	needs, err := f.op.swarmNeedsYouTx(ctx, tx, sc, SwarmSnapshot{Flow: SwarmFlow{Waits: waits}})
	if err != nil || len(needs.Items) != 2 || needs.Items[0].Refs.EventID != insideNew.EventID ||
		needs.Items[0].Waiters != 2 || needs.Items[1].Refs.EventID != insideOld.EventID || needs.Items[1].Waiters != 0 {
		t.Fatalf("inside waiter order: %+v %v", needs, err)
	}
}

func TestSwarmNeedsSourceFailureIsolated(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "source-isolation")
	event := swarmNeedsTestPublish(f, f.rootView, "request", "topic", "agent-board", swarmNeedsTestBoard, "")
	tx, err := f.op.beginSwarmRead(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	r := swarmNeedsReader{repo: f.repo, now: time.Now(), principals: []string{f.root.Inbox}, out: emptySwarmNeedsYou()}
	r.read(ctx, tx, []string{SwarmNeedWorkstreamDecision}, func(sp pgx.Tx) ([]SwarmNeed, error) {
		var ignored int
		err := sp.QueryRow(ctx, `SELECT 1/0`).Scan(&ignored)
		return nil, err
	})
	r.read(ctx, tx, []string{SwarmNeedBoardQuestion}, func(sp pgx.Tx) ([]SwarmNeed, error) {
		return r.board(ctx, sp)
	})
	if len(r.out.Unavailable) != 1 || r.out.Unavailable[0].Type != SwarmNeedWorkstreamDecision ||
		!strings.Contains(r.out.Unavailable[0].Reason, "division by zero") {
		t.Fatalf("source failure: %+v", r.out.Unavailable)
	}
	if got := swarmNeedsTestItem(t, r.out, SwarmNeedBoardQuestion, f.root.Inbox, "answer_board", event.CreatedAt); got.Refs.EventID != event.EventID {
		t.Fatalf("board read after source failure: %+v", got)
	}
	if err := tx.Commit(ctx); err != nil {
		t.Fatalf("snapshot transaction left aborted: %v", err)
	}
}

func TestSwarmNeedsBoardSnapshotDoesNotExposeRecordText(t *testing.T) {
	const noteMarker = "PRIVATE-BOARD-NOTE-PROSE"
	const cardMarker = "PRIVATE-BOARD-CARD-FIELD"
	const labelMarker = "PRIVATE-BOARD-DEFAULT-LABEL"
	const summary = "Choose a setting"
	f := newSwarmFixture(t, "board-metadata")
	body := noteMarker + "\n\n```board-card\n{\n" +
		`"ttl":600,"default":"no","detail":"` + cardMarker +
		`","verbs":[{"id":"no","label":"` + labelMarker + `"}]` + "\n}\n```\n" +
		"\n```board-card\n{\"ttl\":5}\n```"
	card := swarmNeedsTestPublishRequest(f, f.rootView, body, PublishEventRequest{
		Kind: "request", Destination: EventDestination{"topic", "agent-board"}, Summary: summary,
	})
	note := swarmNeedsTestPublish(f, f.rootView, "request", "topic", "agent-board", noteMarker, "")
	malformed := swarmNeedsTestPublish(f, f.rootView, "request", "topic", "agent-board",
		noteMarker+"\n```board-card\n{invalid json\n```", "")
	snap, err := f.op.GetSwarmSnapshot(context.Background(), SwarmSnapshotRequest{Repo: f.repo, Campaign: f.campaign})
	if err != nil || len(snap.SectionErrors) != 0 || len(snap.NeedsYou.Unavailable) != 0 {
		t.Fatalf("snapshot err=%v sections=%v unavailable=%+v", err, snap.SectionErrors, snap.NeedsYou.Unavailable)
	}
	wire, err := json.Marshal(snap)
	if err != nil {
		t.Fatal(err)
	}
	for _, marker := range []string{noteMarker, cardMarker, labelMarker} {
		if strings.Contains(string(wire), marker) {
			t.Errorf("snapshot exposed %s", marker)
		}
	}
	items := map[string]SwarmNeed{}
	for _, item := range snap.NeedsYou.Items {
		items[item.Refs.EventID] = item
	}
	if item := items[card.EventID]; item.Title != summary || item.Detail != "Unattended default declared" ||
		item.ExpiresAt == nil || !item.ExpiresAt.Equal(card.CreatedAt.Add(10*time.Minute)) {
		t.Errorf("expected envelope title and metadata from the first card, got %+v", item)
	}
	if item := items[note.EventID]; item.Title != "Board question from "+f.root.DisplayName || item.Detail != "" ||
		item.ExpiresAt == nil || !item.ExpiresAt.Equal(note.CreatedAt.Add(24*time.Hour)) {
		t.Errorf("expected fixed title and default lifetime for a request without a card, got %+v", item)
	}
	if item := items[malformed.EventID]; item.Title != "Board question from "+f.root.DisplayName || item.Detail != "" || item.ExpiresAt == nil ||
		!item.ExpiresAt.Equal(malformed.CreatedAt.Add(24*time.Hour)) {
		t.Errorf("expected fixed title and fallback lifetime for a malformed card, got %+v", item)
	}
}

func TestSwarmNeedsBoardRequestPredatingCampaignFollowsPublisher(t *testing.T) {
	ctx := context.Background()
	n := newNativeFixture(t)
	root := n.register(nativeAgent{Binding: "future-board-campaign-root"})
	view := n.view(sessionRef(root))
	f := &swarmFixture{nativeFixture: n, campaign: "board-created-later", root: root, rootView: view, op: n.operator()}

	body := "Which option should we use?\n```board-card\n" + `{"ttl":86400}` + "\n```"
	event := swarmNeedsTestPublish(f, view, "request", "topic", "agent-board", body, "")
	publishedAt := event.CreatedAt.Add(-time.Minute)
	f.exec(`UPDATE cairn.agent_event SET created_at=$2 WHERE event_id=$1`, event.EventID, publishedAt)
	wantExpiry := publishedAt.Add(24 * time.Hour)
	checkQuestion := func(needs SwarmNeedsYou) {
		t.Helper()
		item := swarmNeedsTestItem(t, needs, SwarmNeedBoardQuestion, root.Inbox, "answer_board", publishedAt)
		if item.Refs.EventID != event.EventID || item.ExpiresAt == nil || !item.ExpiresAt.Equal(wantExpiry) {
			t.Fatalf("board question: got %+v, want event %s with expiry %s", item, event.EventID, wantExpiry)
		}
	}
	checkQuestion(swarmNeedsTestLanding(f))

	campaign, err := view.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: f.campaign, RootAgent: root.Inbox,
		OutOfChainPolicy: "allow", Sensitivity: "shareable",
	}, f.dest)
	if err != nil || campaign.CreatedAt.Before(publishedAt.Add(time.Minute)) {
		t.Fatalf("campaign must be created after the request: %+v %v", campaign, err)
	}
	checkQuestion(swarmNeedsTestSnapshot(f))
	swarmNeedsTestAbsent(t, swarmNeedsTestLanding(f), SwarmNeedBoardQuestion)
}

func TestSwarmNeedsBoardRequestDefaultAndMaximumLifetimes(t *testing.T) {
	f := newSwarmFixture(t, "board-lifetimes")
	outside, view := f.session("outside-board")
	const cardless = "Board request without a card"
	const longTTL = "Board request with a long lifetime\n```board-card\n" + `{"ttl":360000}` + "\n```"
	publish := func(body string, age time.Duration) (AgentEvent, time.Time) {
		t.Helper()
		event := swarmNeedsTestPublish(f, view, "request", "topic", "agent-board", body, "")
		publishedAt := event.CreatedAt.Add(-age)
		f.exec(`UPDATE cairn.agent_event SET created_at=$2 WHERE event_id=$1`, event.EventID, publishedAt)
		return event, publishedAt
	}
	oldCardless, _ := publish(cardless, 25*time.Hour)
	oldLongTTL, _ := publish(longTTL, 73*time.Hour)
	cardlessEvent, cardlessAt := publish(cardless, 23*time.Hour)
	longEvent, longAt := publish(longTTL, 71*time.Hour)

	landing := swarmNeedsTestLanding(f)
	want := map[string]time.Time{
		cardlessEvent.EventID: cardlessAt.Add(24 * time.Hour),
		longEvent.EventID:     longAt.Add(72 * time.Hour),
	}
	if len(landing.Items) != len(want) {
		t.Fatalf("expected two live questions, got %+v. Expired requests are %s and %s", landing.Items, oldCardless.EventID, oldLongTTL.EventID)
	}
	for _, item := range landing.Items {
		expires, ok := want[item.Refs.EventID]
		if !ok || item.Type != SwarmNeedBoardQuestion || item.Subject != outside.Inbox || item.ExpiresAt == nil || !item.ExpiresAt.Equal(expires) {
			t.Errorf("board question: got %+v, want expiry %s for a live request", item, expires)
		}
		delete(want, item.Refs.EventID)
	}
	if len(want) != 0 {
		t.Errorf("missing live requests: %v", want)
	}
	swarmNeedsTestAbsent(t, swarmNeedsTestSnapshot(f), SwarmNeedBoardQuestion)
}

func TestSwarmNeedsFormerMemberRemainsWithCampaign(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "former-member-needs")
	former, formerView := f.member("former-member")
	outside, outsideView := f.session("never-enrolled")
	formerQuestion := swarmNeedsTestPublish(f, formerView, "request", "topic", "agent-board", swarmNeedsTestBoard, "")
	outsideQuestion := swarmNeedsTestPublish(f, outsideView, "request", "topic", "agent-board", swarmNeedsTestBoard, "")
	for _, stream := range []struct {
		name string
		view *Store
	}{{"former", formerView}, {"outside", outsideView}} {
		if _, err := stream.view.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{
			RequestID: uuid.NewString(), Repo: f.repo, Stream: stream.name, State: "blocked", DecisionNeeded: "Select an option",
		}, f.dest); err != nil {
			t.Fatal(err)
		}
	}
	inactive := false
	member, err := f.rootView.SetTeamMember(ctx, SetTeamMemberRequest{RequestID: uuid.NewString(),
		Repo: f.repo, Campaign: f.campaign, Member: former.Inbox, Active: &inactive}, f.dest)
	if err != nil || member.Active {
		t.Fatalf("deactivate member: %+v %v", member, err)
	}
	campaign := swarmNeedsTestSnapshot(f)
	landing, err := f.op.GetSwarmLanding(ctx, f.repo)
	if err != nil || len(landing.SectionErrors) != 0 || len(landing.NeedsYou.Unavailable) != 0 {
		t.Fatalf("landing err=%v sections=%v unavailable=%+v", err, landing.SectionErrors, landing.NeedsYou.Unavailable)
	}
	for _, page := range []struct {
		name, subject, event, stream string
		items                        []SwarmNeed
	}{
		{"campaign", former.Inbox, formerQuestion.EventID, "former", campaign.Items},
		{"landing", outside.Inbox, outsideQuestion.EventID, "outside", landing.NeedsYou.Items},
	} {
		if len(page.items) != 2 {
			t.Fatalf("%s: expected two rows, got %+v", page.name, page.items)
		}
		board, workstream := 0, 0
		for _, item := range page.items {
			if item.Subject != page.subject {
				t.Errorf("%s: unexpected subject for %+v", page.name, item)
			}
			if item.Type == SwarmNeedBoardQuestion && item.Refs.EventID == page.event {
				board++
			}
			if item.Type == SwarmNeedWorkstreamDecision && item.Refs.Stream == page.stream {
				workstream++
			}
		}
		if board != 1 || workstream != 1 {
			t.Errorf("%s: expected one question and one decision, got %+v", page.name, page.items)
		}
	}
	found := false
	for _, session := range landing.Unaffiliated {
		found = found || session.AgentID == former.AgentID
	}
	if !found {
		t.Error("the former member should still be listed as a currently unaffiliated session")
	}
}

func TestSwarmNeedsOperatorInboxHeadUsesClaimEligibilityBeforePageScope(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "operator-inbox-head")
	_, outside := f.session("inbox-head-outsider")
	operator := f.op.channel.Principal
	deferred := swarmNeedsTestPublish(f, f.rootView, "notice", "agent", operator, "Deferred", "")
	deferredID := swarmNeedsTestDelivery(f, deferred)
	f.deferDelivery(deferredID, "1 hour")

	leased := swarmNeedsTestPublish(f, f.rootView, "notice", "agent", operator, "Leased", "")
	leasedID := swarmNeedsTestDelivery(f, leased)
	f.exec(`UPDATE cairn.agent_delivery SET state='leased',lease_id=$2,lease_until=clock_timestamp()+interval '1 hour' WHERE delivery_id=$1`, leasedID, uuid.NewString())

	expired := swarmNeedsTestPublish(f, f.rootView, "request", "agent", operator, "Admission expired", "")
	expiredID := swarmNeedsTestDelivery(f, expired)
	f.exec(`UPDATE cairn.agent_event SET admission_expires_at=clock_timestamp()-interval '1 second' WHERE event_id=$1`, expired.EventID)

	foreign := swarmNeedsTestPublish(f, outside, "notice", "agent", operator, "Outside head", "")
	foreignID := swarmNeedsTestDelivery(f, foreign)
	inside := swarmNeedsTestPublish(f, f.rootView, "request", "agent", operator, "Inside head", "")
	insideID := swarmNeedsTestDelivery(f, inside)

	check := func(needs SwarmNeedsYou, want string, visible ...string) {
		t.Helper()
		rows := make(map[string]SwarmNeed, len(needs.Items))
		for _, item := range needs.Items {
			if item.Type != SwarmNeedOperatorDelivery {
				continue
			}
			if len(item.Refs.DeliveryIDs) != 1 {
				t.Fatalf("unexpected operator need: %+v", item)
			}
			rows[item.Refs.DeliveryIDs[0]] = item
		}
		if len(rows) != len(visible) {
			t.Fatalf("operator rows: got %+v, want deliveries %v", rows, visible)
		}
		for _, id := range visible {
			item, ok := rows[id]
			if !ok || item.InboxHead != (id == want) {
				t.Errorf("delivery %s: got head=%t, want %s as head", id, item.InboxHead, want)
			}
			wire, err := json.Marshal(item)
			if err != nil || strings.Contains(string(wire), `"inbox_head"`) != item.InboxHead {
				t.Errorf("inbox_head wire omission: %s %v", wire, err)
			}
		}
	}

	check(swarmNeedsTestSnapshot(f), "", deferredID, leasedID, expiredID, insideID)
	check(swarmNeedsTestLanding(f), foreignID, foreignID)
	claim := func(want string) {
		t.Helper()
		got, err := f.op.NextEvent(ctx, NextEventRequest{Repo: f.repo}, swarmOperatorDestination)
		if err != nil || got.Delivery == nil || got.Delivery.DeliveryID != want {
			t.Fatalf("claim wanted %s, got %+v %v", want, got.Delivery, err)
		}
	}
	claim(foreignID)
	check(swarmNeedsTestSnapshot(f), insideID, deferredID, leasedID, insideID)
	check(swarmNeedsTestLanding(f), "", foreignID)

	f.expireLease(leasedID)
	check(swarmNeedsTestSnapshot(f), leasedID, deferredID, leasedID, insideID)
	claim(leasedID)
	check(swarmNeedsTestSnapshot(f), insideID, deferredID, leasedID, insideID)
	claim(insideID)
	check(swarmNeedsTestSnapshot(f), "", deferredID, leasedID, insideID)
	check(swarmNeedsTestLanding(f), "", foreignID)
}
