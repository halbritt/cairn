package core

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

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
	noticeEvt, _ := f.publish("notice", f.agent.Inbox)
	respEvt, _ := f.publish("response", f.agent.Inbox)
	reqEvt, _ := f.publish("request", f.agent.Inbox)

	// Host observes a retained wake on otherNotice before the session stops.
	f.observe(t, SessionDeliveryObservation{DeliveryID: otherDelivery, Condition: "wake_retained", Wake: &ObservedWake{Transport: "codex-queue", Status: "refused"}, ObservedAt: dbNow(t, f.receiver)})

	// While online or transiently offline, non-request event-status omits diagnosis.
	f.expireSession(f.agent.AgentID)
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
	pubAgent, err := f.sender.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "pub-session", NativeSessionID: uuid.NewString(), Metadata: existingSessionMetadata("codex", "idle")}, f.dest)
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
	_, senderDelID := f.publish("response", f.agent.Inbox)

	// Offline recipient (not stopped) is skipped.
	f.expireSession(f.agent.AgentID)
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
		evt, delID := f.publish("notice", agent463.Inbox)
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

func waitForAdvisoryLockPIDs(t *testing.T, ctx context.Context, s *Store, key string, want int, earlyDone <-chan struct{}) []int {
	t.Helper()
	waitCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	for {
		rows, err := s.pool.Query(waitCtx, `SELECT pid FROM pg_locks
			WHERE locktype='advisory' AND NOT granted
			  AND classid=((hashtextextended($1,0)>>32)&4294967295)::oid
			  AND objid=(hashtextextended($1,0)&4294967295)::oid`, key)
		if err != nil {
			t.Fatalf("query pg_locks(%s): %v", key, err)
		}
		var pids []int
		for rows.Next() {
			var pid int
			if err = rows.Scan(&pid); err != nil {
				rows.Close()
				t.Fatal(err)
			}
			pids = append(pids, pid)
		}
		rows.Close()
		if len(pids) >= want {
			return pids
		}
		select {
		case <-earlyDone:
			t.Fatalf("contender exited before reaching advisory lock %s", key)
		case <-waitCtx.Done():
			t.Fatalf("timed out waiting for %d advisory waiters on %s (got %d)", want, key, len(pids))
		case <-time.After(2 * time.Millisecond):
		}
	}
}

func TestStoppedNotificationCancelLockOrderAndReadCommittedRace(t *testing.T) {
	ctx := context.Background()
	f := newHealthFixture(t)
	repo := f.source.Scope.Repo
	op := testStore(t, Channel{Principal: "stopped-race-op-1-" + repo, Operator: true})
	opCancel := testStore(t, Channel{Principal: "stopped-race-op-2-" + repo, Operator: true})

	_, stoppedDel1 := f.publish("request", f.agent.Inbox)
	_, stoppedDel2 := f.publish("request", f.agent.Inbox)
	if _, err := f.receiver.LeaveAgent(ctx, f.ref, f.dest); err != nil {
		t.Fatal(err)
	}

	// 1. Hold collection lock + FOR UPDATE OF d; start NotifyStoppedDeliveryPublisher.
	// Prove it waits on collection lock BEFORE touching agent_delivery, and under
	// ReadCommitted observes cancellation committed before lock release.
	holdTx, err := op.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer holdTx.Rollback(ctx)
	if err = lock(ctx, holdTx, "agent-events:"+repo); err != nil {
		t.Fatal(err)
	}
	var lockedID string
	if err = holdTx.QueryRow(ctx, `SELECT delivery_id::text FROM cairn.agent_delivery WHERE delivery_id=$1 FOR UPDATE`, stoppedDel1).Scan(&lockedID); err != nil {
		t.Fatal(err)
	}

	req1 := StoppedDeliveryNotificationRequest{
		RequestID: uuid.NewString(), Repo: repo, DeliveryID: stoppedDel1,
		RecipientAgentID: f.agent.AgentID, RecipientExecution: f.agent.ExecutionID,
		Stage: "waiting", Fingerprint: "waiting|never_launched|not_admitted||",
	}
	type notifyOut struct {
		res StoppedDeliveryNotificationResult
		err error
	}
	done1 := make(chan notifyOut, 1)
	early1 := make(chan struct{})
	go func() {
		defer close(early1)
		r, e := op.NotifyStoppedDeliveryPublisher(ctx, req1)
		done1 <- notifyOut{r, e}
	}()
	pids := waitForAdvisoryLockPIDs(t, ctx, op, "agent-events:"+repo, 1, early1)
	var holdsDeliveryLock bool
	if err = op.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1 AND relation='cairn.agent_delivery'::regclass)`, pids[0]).Scan(&holdsDeliveryLock); err != nil {
		t.Fatal(err)
	}
	if holdsDeliveryLock {
		t.Fatalf("NotifyStoppedDeliveryPublisher locked agent_delivery before acquiring collection lock")
	}
	if _, err = failDelivery(ctx, holdTx, stoppedDel1, "operator_cancelled", &stopControl{Reason: "cancelled while notify queued"}); err != nil {
		t.Fatal(err)
	}
	if err = holdTx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	out1 := <-done1
	if out1.err != nil || out1.res.Status != "skipped" || out1.res.Reason != "delivery_resolved" {
		t.Fatalf("expected ReadCommitted notify to see post-lock cancellation (skipped/delivery_resolved), got %+v %v", out1.res, out1.err)
	}

	// 2. Queue NotifyStoppedDeliveryPublisher and CancelWork concurrently behind collection lock;
	// release together and prove zero deadlock and consistent outcomes.
	gateTx, err := op.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer gateTx.Rollback(ctx)
	if err = lock(ctx, gateTx, "agent-events:"+repo); err != nil {
		t.Fatal(err)
	}
	doneNotify2 := make(chan notifyOut, 1)
	doneCancel2 := make(chan error, 1)
	early2 := make(chan struct{}, 2)
	go func() {
		r, e := op.NotifyStoppedDeliveryPublisher(ctx, StoppedDeliveryNotificationRequest{
			RequestID: uuid.NewString(), Repo: repo, DeliveryID: stoppedDel2,
			RecipientAgentID: f.agent.AgentID, RecipientExecution: f.agent.ExecutionID,
			Stage: "waiting", Fingerprint: "waiting|never_launched|not_admitted||",
		})
		early2 <- struct{}{}
		doneNotify2 <- notifyOut{r, e}
	}()
	go func() {
		_, e := opCancel.CancelWork(ctx, CancelWorkRequest{
			RequestID: uuid.NewString(), Repo: repo, DeliveryID: stoppedDel2, Reason: "concurrent cancel vs notify",
		})
		early2 <- struct{}{}
		doneCancel2 <- e
	}()
	pids2 := waitForAdvisoryLockPIDs(t, ctx, op, "agent-events:"+repo, 2, early2)
	for _, pid := range pids2 {
		if err = op.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1 AND relation='cairn.agent_delivery'::regclass)`, pid).Scan(&holdsDeliveryLock); err != nil {
			t.Fatal(err)
		}
		if holdsDeliveryLock {
			t.Fatalf("contender pid %d locked agent_delivery before collection lock", pid)
		}
	}
	if err = gateTx.Rollback(ctx); err != nil {
		t.Fatal(err)
	}
	if cancelErr := <-doneCancel2; cancelErr != nil {
		t.Fatalf("concurrent CancelWork failed: %v", cancelErr)
	}
	out2 := <-doneNotify2
	if out2.err != nil || (out2.res.Status != "sent" && (out2.res.Status != "skipped" || out2.res.Reason != "delivery_resolved")) {
		t.Fatalf("concurrent NotifyStoppedDeliveryPublisher failed: %+v %v", out2.res, out2.err)
	}
}
