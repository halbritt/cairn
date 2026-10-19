package core

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestStoppedSessionDiagnosisVisibilityAndPrivacy(t *testing.T) {
	ctx := context.Background()
	f := newHealthFixture(t)
	other := testStore(t, Channel{Principal: "other-pub-" + f.source.Scope.Repo, Repo: f.source.Scope.Repo})
	otherDraft := projectNote(f.source.Scope.Repo)
	otherDraft.Sensitivity = "shareable"
	otherNote, err := other.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: otherDraft})
	if err != nil {
		t.Fatal(err)
	}
	otherNotice, otherDelivery := func() (AgentEvent, string) {
		e, err := other.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{otherNote.RecordID, otherNote.Version}, Destination: EventDestination{Type: "agent", Name: f.agent.Inbox}}, f.dest)
		if err != nil {
			t.Fatal(err)
		}
		st, err := other.AgentEventStatus(ctx, EventStatusRequest{EventID: e.EventID}, f.dest)
		if err != nil || len(st.Deliveries) != 1 {
			t.Fatalf("other status: %+v %v", st, err)
		}
		return e, st.Deliveries[0].DeliveryID
	}()
	noticeEvt, _ := f.publish(t, "notice", f.agent.Inbox)
	respEvt, _ := f.publish(t, "response", f.agent.Inbox)
	reqEvt, _ := f.publish(t, "request", f.agent.Inbox)

	// Host observes a retained wake on otherNotice before the session stops.
	f.observe(t, SessionDeliveryObservation{DeliveryID: otherDelivery, Condition: "wake_retained", Wake: &ObservedWake{Transport: "codex-queue", Status: "refused"}, ObservedAt: dbNow(t, f.receiver)})

	// While online or transiently offline, non-request event-status omits diagnosis.
	if _, err := f.receiver.pool.Exec(ctx, `UPDATE cairn.agent_session SET expires_at=clock_timestamp()-interval '1 second' WHERE agent_id=$1`, f.agent.AgentID); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{noticeEvt.EventID, respEvt.EventID} {
		st, err := f.sender.AgentEventStatus(ctx, EventStatusRequest{EventID: id}, f.dest)
		if err != nil || st.Deliveries[0].Diagnosis != nil {
			t.Fatalf("offline non-request diagnosis should be nil: %+v %v", st, err)
		}
	}

	// Once confirmed stopped, publishers of notice, response, and request see stopped diagnosis.
	if _, err := f.receiver.LeaveAgent(ctx, f.ref, f.dest); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{noticeEvt.EventID, respEvt.EventID, reqEvt.EventID} {
		st, err := f.sender.AgentEventStatus(ctx, EventStatusRequest{EventID: id}, f.dest)
		if err != nil || len(st.Deliveries) != 1 || st.Deliveries[0].Diagnosis == nil {
			t.Fatalf("stopped status missing diagnosis: %+v %v", st, err)
		}
		diag := st.Deliveries[0].Diagnosis
		if diag.Recipient.Presence != "stopped" || diag.Stage != "waiting" || diag.WaitingSeconds == nil {
			t.Fatalf("unexpected stopped diagnosis: %+v", diag)
		}
		if diag.Host == nil || diag.Host.DeliveryID != "" || diag.Host.Applies != "other_delivery" {
			t.Fatalf("foreign delivery ID leaked on host diagnosis: %+v", diag.Host)
		}
	}
	// Other publisher sees exact applicability on its own notice and cannot read f.sender's event.
	otherSt, err := other.AgentEventStatus(ctx, EventStatusRequest{EventID: otherNotice.EventID}, f.dest)
	if err != nil || otherSt.Deliveries[0].Diagnosis == nil || otherSt.Deliveries[0].Diagnosis.Host.Applies != "exact" || otherSt.Deliveries[0].Diagnosis.Host.Reported != "wake_retained" {
		t.Fatalf("other publisher status: %+v %v", otherSt, err)
	}
	_, err = other.AgentEventStatus(ctx, EventStatusRequest{EventID: noticeEvt.EventID}, f.dest)
	requireCode(t, err, "NOT_FOUND")
}

func TestStoppedSessionPublisherNotificationLifecycle(t *testing.T) {
	ctx := context.Background()
	f := newHealthFixture(t)
	op := testStore(t, Channel{Principal: "stopped-op-" + f.source.Scope.Repo, Operator: true})

	// Register a session-backed publisher as well as using f.sender profile.
	pubAgent, err := f.sender.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "pub-session", NativeSessionID: uuid.NewString(), Metadata: AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"}}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	pubRef := AgentSessionRef{pubAgent.AgentID, pubAgent.ExecutionID}
	pubView, err := f.sender.ForAgentSession(pubRef, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	pubEvt, err := pubView.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{f.source.RecordID, f.source.Version}, Destination: EventDestination{Type: "agent", Name: f.agent.Inbox}}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	pubSt, err := pubView.AgentEventStatus(ctx, EventStatusRequest{EventID: pubEvt.EventID}, f.dest)
	if err != nil || len(pubSt.Deliveries) != 1 {
		t.Fatalf("pub status: %+v %v", pubSt, err)
	}
	pubDelID := pubSt.Deliveries[0].DeliveryID
	_, senderDelID := f.publish(t, "response", f.agent.Inbox)

	// Offline recipient (not stopped) is skipped.
	if _, err := f.receiver.pool.Exec(ctx, `UPDATE cairn.agent_session SET expires_at=clock_timestamp()-interval '1 second' WHERE agent_id=$1`, f.agent.AgentID); err != nil {
		t.Fatal(err)
	}
	offlineRes, err := op.NotifyStoppedDeliveryPublisher(ctx, StoppedDeliveryNotificationRequest{
		RequestID: uuid.NewString(), Repo: f.source.Scope.Repo, DeliveryID: senderDelID,
		RecipientAgentID: f.agent.AgentID, RecipientExecution: f.agent.ExecutionID, Stage: "waiting", Fingerprint: "waiting|never_launched|not_admitted||",
	})
	if err != nil || offlineRes.Status != "skipped" || offlineRes.Reason != "recipient_not_stopped" {
		t.Fatalf("offline recipient should be skipped: %+v %v", offlineRes, err)
	}

	// Stop the recipient session.
	if _, err := f.receiver.LeaveAgent(ctx, f.ref, f.dest); err != nil {
		t.Fatal(err)
	}
	reqID := uuid.NewString()
	sent, err := op.NotifyStoppedDeliveryPublisher(ctx, StoppedDeliveryNotificationRequest{
		RequestID: reqID, Repo: f.source.Scope.Repo, DeliveryID: senderDelID,
		RecipientAgentID: f.agent.AgentID, RecipientExecution: f.agent.ExecutionID, Stage: "waiting", Fingerprint: "waiting|never_launched|not_admitted||",
	})
	if err != nil || sent.Status != "sent" || sent.NotificationEventID == "" {
		t.Fatalf("expected sent notification: %+v %v", sent, err)
	}
	// Duplicate call with same RequestID is suppressed.
	dup, err := op.NotifyStoppedDeliveryPublisher(ctx, StoppedDeliveryNotificationRequest{
		RequestID: reqID, Repo: f.source.Scope.Repo, DeliveryID: senderDelID,
		RecipientAgentID: f.agent.AgentID, RecipientExecution: f.agent.ExecutionID, Stage: "waiting", Fingerprint: "waiting|never_launched|not_admitted||",
	})
	if err != nil || dup.Status != "duplicate_suppressed" || dup.NotificationEventID != sent.NotificationEventID {
		t.Fatalf("expected duplicate_suppressed: %+v %v", dup, err)
	}

	// If publisher session itself stops, notification is marked recipient_unavailable and creates no delivery.
	if _, err := f.sender.LeaveAgent(ctx, pubRef, f.dest); err != nil {
		t.Fatal(err)
	}
	unavail, err := op.NotifyStoppedDeliveryPublisher(ctx, StoppedDeliveryNotificationRequest{
		RequestID: uuid.NewString(), Repo: f.source.Scope.Repo, DeliveryID: pubDelID,
		RecipientAgentID: f.agent.AgentID, RecipientExecution: f.agent.ExecutionID, Stage: "waiting", Fingerprint: "waiting|never_launched|not_admitted||",
	})
	if err != nil || unavail.Status != "recipient_unavailable" || unavail.Reason != "publisher_stopped" {
		t.Fatalf("expected recipient_unavailable: %+v %v", unavail, err)
	}
	// Original deliveries remain pending with attempts=0.
	page, err := op.ReviewEvents(ctx, EventReviewRequest{Repo: f.source.Scope.Repo, Consumer: f.agent.Inbox})
	if err != nil || len(page.Deliveries) != 2 || page.Deliveries[0].State != "pending" || page.Deliveries[0].Attempts != 0 {
		t.Fatalf("original deliveries mutated: %+v %v", page, err)
	}
}

func TestStoppedSessionAgent463ThirtyFiveNoticesFixture(t *testing.T) {
	ctx := context.Background()
	f := newHealthFixture(t)
	op := testStore(t, Channel{Principal: "oversight-op-" + f.source.Scope.Repo, Operator: true})
	const oldestID = "473dfc21-3361-4ea3-8563-7849d13720c6"

	agent463, err := f.receiver.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID: uuid.NewString(), Binding: "agent-463", NativeSessionID: uuid.NewString(),
		Metadata: AgentMetadata{Harness: "codex", Project: "placer", Workspace: "/work/placer", State: "idle", DeliveryMode: "existing-session", TaskSummary: "placer search coordination"},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	ref463 := AgentSessionRef{agent463.AgentID, agent463.ExecutionID}

	var firstEvent AgentEvent
	for i := 0; i < 35; i++ {
		evt, delID := f.publish(t, "notice", agent463.Inbox)
		if i == 0 {
			firstEvent = evt
			if _, err := f.receiver.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET delivery_id=$1 WHERE delivery_id=$2`, oldestID, delID); err != nil {
				t.Fatal(err)
			}
			t0 := dbNow(t, f.receiver)
			if _, err := f.receiver.ObserveSessionDelivery(ctx, SessionDeliveryObservation{
				Session: ref463, DeliveryID: oldestID, Condition: "wake_retained",
				Wake: &ObservedWake{Transport: "codex-queue", Status: "refused", AttemptedAt: &t0}, ObservedAt: t0,
			}, f.dest); err != nil {
				t.Fatal(err)
			}
		}
	}
	if _, err := f.receiver.LeaveAgent(ctx, ref463, f.dest); err != nil {
		t.Fatal(err)
	}
	// Starting successor agent-469 with matching project/workspace/task does not mark agent-463 deliveries handled.
	if _, err := f.receiver.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID: uuid.NewString(), Binding: "agent-469", NativeSessionID: uuid.NewString(),
		Metadata: AgentMetadata{Harness: "codex", Project: "placer", Workspace: "/work/placer", State: "idle", DeliveryMode: "existing-session", TaskSummary: "placer search coordination"},
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	page, err := op.ReviewEvents(ctx, EventReviewRequest{Repo: f.source.Scope.Repo, Consumer: agent463.Inbox, State: "pending", Limit: 100})
	if err != nil || len(page.Deliveries) != 35 || page.More {
		t.Fatalf("expected 35 pending deliveries for agent-463: len=%d more=%v err=%v", len(page.Deliveries), page.More, err)
	}
	oldest := page.Deliveries[0]
	if oldest.DeliveryID != oldestID || oldest.Attempts != 0 || oldest.Diagnosis == nil || oldest.Diagnosis.Recipient.Presence != "stopped" {
		t.Fatalf("unexpected oldest delivery: %+v %+v", oldest, oldest.Diagnosis)
	}
	if h := oldest.Diagnosis.Host; h == nil || h.Reported != "wake_retained" || h.Freshness != "offline" || h.Applies != "exact" || h.Wake == nil || h.Wake.Transport != "codex-queue" || h.Wake.Status != "refused" {
		t.Fatalf("unexpected oldest host diagnosis: %+v", h)
	}
	// Publisher sees stopped diagnosis and receives deduplicated notification payload.
	pubStatus, err := f.sender.AgentEventStatus(ctx, EventStatusRequest{EventID: firstEvent.EventID}, f.dest)
	if err != nil || pubStatus.Deliveries[0].Diagnosis == nil || pubStatus.Deliveries[0].Diagnosis.Recipient.Presence != "stopped" {
		t.Fatalf("publisher status on oldest notice: %+v %v", pubStatus, err)
	}
	notif, err := op.NotifyStoppedDeliveryPublisher(ctx, StoppedDeliveryNotificationRequest{
		RequestID: uuid.NewString(), Repo: f.source.Scope.Repo, DeliveryID: oldestID,
		RecipientAgentID: agent463.AgentID, RecipientExecution: agent463.ExecutionID, Stage: "waiting", Fingerprint: "waiting|never_launched|unknown|wake_retained|codex-queue:refused",
	})
	if err != nil || notif.Status != "sent" {
		t.Fatalf("notify oldest notice: %+v %v", notif, err)
	}
	claimed, err := f.sender.NextEvent(ctx, NextEventRequest{}, f.dest)
	if err != nil || claimed.Delivery == nil || claimed.Delivery.Event.EventID != notif.NotificationEventID || claimed.Delivery.Event.CausationID != firstEvent.EventID {
		t.Fatalf("publisher inbox missing notice: %+v %v", claimed, err)
	}
	rec, err := f.sender.History(ctx, RecordHistoryRequest{RecordID: claimed.Delivery.Event.Ref.RecordID, Version: 1}, f.dest)
	if err != nil || len(rec.Versions) != 1 || rec.Versions[0].Body == nil || !strings.Contains(*rec.Versions[0].Body, oldestID) || !strings.Contains(*rec.Versions[0].Body, `"admission":"unknown"`) {
		t.Fatalf("unexpected notice body: %+v %v", rec, err)
	}
	var parsed stoppedDeliveryNoticePayload
	if err := json.Unmarshal([]byte(*rec.Versions[0].Body), &parsed); err != nil || parsed.RetainedWake == nil || parsed.RetainedWake.Transport != "codex-queue" || parsed.RetainedWake.Status != "refused" {
		t.Fatalf("parsed notice payload: %+v %v", parsed, err)
	}
}
