package core

import (
	"context"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestRoutineNoticePolicyDefersOnlyItsOwnWakeAndKeepsRetryDeadline(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	sender, receiver, source, dest := f.sender, f.receiver, f.source, f.dest
	agent := f.register(nativeAgent{Binding: "routine-notice"})
	ref := sessionRef(agent)
	request := PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice",
		Ref:          RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination:  EventDestination{Type: "agent", Name: agent.Inbox},
		NoticePolicy: &NoticeWakePolicyRequest{Version: 1, Urgency: "routine", DeferSeconds: 60}}
	notice, err := sender.PublishEvent(ctx, request, dest)
	if err != nil {
		t.Fatal(err)
	}
	if notice.NoticePolicy == nil || notice.NoticePolicy.Owner != sender.channel.Principal ||
		notice.NoticePolicy.Version != 1 || notice.NoticePolicy.DeferSeconds != 60 {
		t.Fatalf("publisher-owned policy missing: %+v", notice.NoticePolicy)
	}
	status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: notice.EventID}, dest)
	if err != nil || len(status.Deliveries) != 1 {
		t.Fatalf("notice status: %+v %v", status, err)
	}
	deadline := status.Deliveries[0].AvailableAt
	if deadline == nil {
		t.Fatal("opt-in notice availability missing")
	}
	if delay := deadline.Sub(notice.CreatedAt); delay != 60*time.Second {
		t.Fatalf("fixed availability: %s", delay)
	}
	ready, err := receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.DeliveryID != "" {
		t.Fatalf("routine notice woke early: %+v %v", ready, err)
	}
	again, err := sender.PublishEvent(ctx, request, dest)
	if err != nil || again.EventID != notice.EventID {
		t.Fatalf("retry changed publication: %+v %v", again, err)
	}
	status, err = sender.AgentEventStatus(ctx, EventStatusRequest{EventID: notice.EventID}, dest)
	if err != nil || status.Deliveries[0].AvailableAt == nil || !status.Deliveries[0].AvailableAt.Equal(*deadline) {
		t.Fatalf("retry slid routine deadline: %+v %v", status, err)
	}
	changed := request
	changed.NoticePolicy = &NoticeWakePolicyRequest{Version: 1, Urgency: "urgent", DeferSeconds: 0}
	_, err = sender.PublishEvent(ctx, changed, dest)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	urgent, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(),
		Kind: "request", Ref: request.Ref, Destination: request.Destination}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ready, err = receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.EventID != urgent.EventID {
		t.Fatalf("urgent request blocked by deferred notice: %+v %v", ready, err)
	}
	// Move only this fixture's deadline to the past; the source and delivery ID
	// remain stable, so readiness exposes the original notice after deferral.
	f.makeEventAvailable(notice.EventID)
	ready, err = receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.EventID != notice.EventID {
		t.Fatalf("expired routine deferral remained hidden: %+v %v", ready, err)
	}
}

func TestSustainedRoutineArrivalsKeepFixedDeadlinesAndDrainOriginalOrder(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	sender, receiver, source, dest := f.sender, f.receiver, f.source, f.dest
	agent := f.register(nativeAgent{Binding: "routine-arrivals"})
	ref := sessionRef(agent)
	view, err := receiver.ForAgentSession(ref, dest)
	if err != nil {
		t.Fatal(err)
	}
	type publishedNotice struct {
		event    AgentEvent
		delivery AgentDelivery
		deadline time.Time
	}
	items := make([]publishedNotice, 0, 3)
	for i := 0; i < 3; i++ {
		event, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice",
			Ref:          RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
			Destination:  EventDestination{Type: "agent", Name: agent.Inbox},
			NoticePolicy: &NoticeWakePolicyRequest{Version: 1, Urgency: "routine", DeferSeconds: 60}}, dest)
		if err != nil {
			t.Fatal(err)
		}
		status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
		if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].AvailableAt == nil {
			t.Fatalf("routine arrival %d status: %+v %v", i, status, err)
		}
		delivery := status.Deliveries[0]
		deadline := event.CreatedAt.Add(60 * time.Second)
		if !delivery.AvailableAt.Equal(deadline) {
			t.Fatalf("routine arrival %d deadline slid from its own publication: got=%s want=%s",
				i, delivery.AvailableAt, deadline)
		}
		for prior, item := range items {
			current, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: item.event.EventID}, dest)
			if err != nil || len(current.Deliveries) != 1 || current.Deliveries[0].DeliveryID != item.delivery.DeliveryID ||
				current.Deliveries[0].AvailableAt == nil || !current.Deliveries[0].AvailableAt.Equal(item.deadline) {
				t.Fatalf("arrival %d moved earlier delivery %d: %+v %v", i, prior, current, err)
			}
		}
		items = append(items, publishedNotice{event: event, delivery: delivery, deadline: deadline})
	}
	// Advance only these disposable deadlines to test queue behavior without a
	// minute-long wait. Every original event and delivery must remain eligible
	// in position order after the burst.
	for _, item := range items {
		f.makeAvailable(item.delivery.DeliveryID)
	}
	for i, item := range items {
		ready, err := receiver.SessionInboxReady(ctx, ref, dest)
		if err != nil || ready.EventID != item.event.EventID || ready.DeliveryID != item.delivery.DeliveryID {
			t.Fatalf("eligible burst item %d was lost or reordered: %+v %v", i, ready, err)
		}
		claim, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref,
			DeliveryID: item.delivery.DeliveryID, NativeTurnID: fmt.Sprintf("routine-arrival-%d", i), TurnExclusive: true}, dest)
		if err != nil || claim.Attempt == nil || claim.Attempt.Delivery.Event.EventID != item.event.EventID ||
			claim.Attempt.Delivery.Event.Ref != item.event.Ref {
			t.Fatalf("eligible burst item %d claim changed identity: %+v %v", i, claim, err)
		}
		if _, err := view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(),
			DeliveryID: item.delivery.DeliveryID, LeaseID: claim.Attempt.Delivery.LeaseID,
			Disposition: "failed", Code: "processing_failed"}, dest); err != nil {
			t.Fatal(err)
		}
		if _, err := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(),
			Session: ref, AttemptID: claim.Attempt.ID, Reason: "delivery_completed"}, dest); err != nil {
			t.Fatal(err)
		}
	}
	ready, err := receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.DeliveryID != "" {
		t.Fatalf("burst queue retained an unexpected extra delivery: %+v %v", ready, err)
	}
}

func TestRoutineNoticePressureOverflowKeepsDeadlinesUrgentAndEverySource(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	sender, receiver, seed, dest := f.sender, f.receiver, f.source, f.dest
	agent := f.register(nativeAgent{Binding: "routine-pressure-overflow"})
	ref := sessionRef(agent)
	view, err := receiver.ForAgentSession(ref, dest)
	if err != nil {
		t.Fatal(err)
	}
	type item struct {
		event      AgentEvent
		deliveryID string
		deadline   time.Time
		source     RecordVersionRef
	}
	publishRoutine := func(index int) item {
		t.Helper()
		draft := projectNote(seed.Scope.Repo)
		draft.Sensitivity = "shareable"
		draft.Body = fmt.Sprintf("routine pressure source %03d", index)
		source, err := sender.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
		if err != nil {
			t.Fatal(err)
		}
		sourceRef := RecordVersionRef{RecordID: source.RecordID, Version: source.Version}
		event, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice",
			Ref: sourceRef, Destination: EventDestination{Type: "agent", Name: agent.Inbox},
			NoticePolicy: &NoticeWakePolicyRequest{Version: 1, Urgency: "routine", DeferSeconds: 60}}, dest)
		if err != nil {
			t.Fatal(err)
		}
		status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
		if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].AvailableAt == nil ||
			!status.Deliveries[0].AvailableAt.Equal(event.CreatedAt.Add(60*time.Second)) ||
			status.Deliveries[0].State != "pending" || status.Deliveries[0].Attempts != 0 {
			t.Fatalf("routine pressure item %d status: %+v %v", index, status, err)
		}
		return item{event: event, deliveryID: status.Deliveries[0].DeliveryID,
			deadline: *status.Deliveries[0].AvailableAt, source: sourceRef}
	}
	settle := func(attempt *SessionInboxAttempt) {
		t.Helper()
		if _, err := view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(),
			DeliveryID: attempt.Delivery.DeliveryID, LeaseID: attempt.Delivery.LeaseID,
			Disposition: "failed", Code: "processing_failed"}, dest); err != nil {
			t.Fatal(err)
		}
		if _, err := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(),
			Session: ref, AttemptID: attempt.ID, Reason: "delivery_completed"}, dest); err != nil {
			t.Fatal(err)
		}
	}

	// Keep an older routine item ineligible, then prove the immediate request
	// bypasses it. This is the actual policy distinction: priority is explicit
	// publication metadata/kind, never inferred from its source text.
	items := make([]item, 0, 111)
	first := publishRoutine(0)
	items = append(items, first)
	urgent, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request",
		Ref: first.source, Destination: EventDestination{Type: "agent", Name: agent.Inbox}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	urgentStatus, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: urgent.EventID}, dest)
	if err != nil || len(urgentStatus.Deliveries) != 1 || urgentStatus.Deliveries[0].AvailableAt != nil {
		t.Fatalf("urgent request status: %+v %v", urgentStatus, err)
	}
	ready, err := receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.EventID != urgent.EventID || ready.DeliveryID != urgentStatus.Deliveries[0].DeliveryID {
		t.Fatalf("urgent request did not bypass deferred routine notice: %+v %v", ready, err)
	}
	urgentClaim, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}, dest)
	if err != nil || urgentClaim.Attempt == nil || urgentClaim.Attempt.Delivery.Event.EventID != urgent.EventID {
		t.Fatalf("urgent request claim: %+v %v", urgentClaim, err)
	}
	settle(urgentClaim.Attempt)

	// 101 distinct routine publications exceed the pending cue's 100-item
	// window. Each has its own source and fixed deadline; later arrivals must
	// not move the first event's deadline.
	for i := 1; i < 101; i++ {
		items = append(items, publishRoutine(i))
	}
	firstStatus, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: first.event.EventID}, dest)
	if err != nil || len(firstStatus.Deliveries) != 1 || firstStatus.Deliveries[0].AvailableAt == nil ||
		!firstStatus.Deliveries[0].AvailableAt.Equal(first.deadline) {
		t.Fatalf("later arrivals slid first deadline: %+v %v", firstStatus, err)
	}
	// No test clock is injected into PostgreSQL. Advance only this disposable
	// fixture's availability rows to model the fixed deadlines expiring.
	f.makeAvailable(first.deliveryID)
	ready, err = receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.EventID != first.event.EventID || ready.DeliveryID != first.deliveryID {
		t.Fatalf("first expired deadline did not expose oldest original: %+v %v", ready, err)
	}
	pending, err := receiver.SessionInboxPending(ctx, ref, dest)
	if err != nil || pending.Notices < 1 || pending.LatestPosition < first.event.Position {
		t.Fatalf("first expired item missing from pending cue: %+v %v", pending, err)
	}
	if _, err = sender.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET available_at=clock_timestamp()-interval '1 second' WHERE consumer=$1 AND delivery_id<>$2 AND event_id IN (SELECT event_id FROM cairn.agent_event WHERE repo=$3 AND kind='notice')`, agent.Inbox, first.deliveryID, seed.Scope.Repo); err != nil {
		t.Fatal(err)
	}
	pending, err = receiver.SessionInboxPending(ctx, ref, dest)
	if err != nil || pending.Notices != sessionInboxPendingLimit || !pending.Truncated ||
		pending.LatestPosition != items[len(items)-1].event.Position {
		t.Fatalf("overflow hint did not report truncation/latest arrival: %+v %v", pending, err)
	}

	// Add arrivals while the finite original overflow is draining. They retain
	// later positions and their own non-sliding deadlines; they cannot displace
	// any of the 101 earlier eligible source/delivery identities.
	pressure := make([]item, 0, 10)
	for i := 0; i < len(items); i++ {
		want := items[i]
		ready, err = receiver.SessionInboxReady(ctx, ref, dest)
		if err != nil || ready.EventID != want.event.EventID || ready.DeliveryID != want.deliveryID {
			t.Fatalf("overflow item %d was dropped/reordered: %+v %v", i, ready, err)
		}
		claim, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}, dest)
		if err != nil || claim.Attempt == nil || claim.Attempt.Delivery.Event.EventID != want.event.EventID ||
			claim.Attempt.Delivery.DeliveryID != want.deliveryID || claim.Attempt.Delivery.Event.Ref != want.source {
			t.Fatalf("overflow item %d source/delivery changed: %+v %v", i, claim, err)
		}
		settle(claim.Attempt)
		if (i+1)%10 == 0 {
			pressure = append(pressure, publishRoutine(101+len(pressure)))
		}
	}
	for _, late := range pressure {
		f.makeAvailable(late.deliveryID)
		ready, err = receiver.SessionInboxReady(ctx, ref, dest)
		if err != nil || ready.EventID != late.event.EventID || ready.DeliveryID != late.deliveryID {
			t.Fatalf("late pressure item was dropped/reordered: %+v %v", ready, err)
		}
		claim, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}, dest)
		if err != nil || claim.Attempt == nil || claim.Attempt.Delivery.Event.EventID != late.event.EventID ||
			claim.Attempt.Delivery.Event.Ref != late.source {
			t.Fatalf("late pressure source changed: %+v %v", claim, err)
		}
		settle(claim.Attempt)
	}
	ready, err = receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.DeliveryID != "" {
		t.Fatalf("pressure queue did not finitely drain: %+v %v", ready, err)
	}
}

func TestNoticePolicyValidationAndImmediateDefault(t *testing.T) {
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	base := PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice",
		Ref:         RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination: EventDestination{Type: "agent", Name: receiver.channel.Principal}}
	for _, tc := range []struct {
		kind   string
		policy NoticeWakePolicyRequest
	}{
		{"request", NoticeWakePolicyRequest{Version: 1, Urgency: "routine", DeferSeconds: 60}},
		{"notice", NoticeWakePolicyRequest{Version: 2, Urgency: "routine", DeferSeconds: 60}},
		{"notice", NoticeWakePolicyRequest{Version: 1, Urgency: "routine", DeferSeconds: 61}},
		{"notice", NoticeWakePolicyRequest{Version: 1, Urgency: "urgent", DeferSeconds: 1}},
		{"notice", NoticeWakePolicyRequest{Version: 1, Urgency: "unspecified", DeferSeconds: 0}},
	} {
		bad := base
		bad.RequestID = uuid.NewString()
		bad.Kind = tc.kind
		bad.NoticePolicy = &tc.policy
		_, err := sender.PublishEvent(ctx, bad, dest)
		requireCode(t, err, "INVALID_REQUEST")
	}
	ordinary, err := sender.PublishEvent(ctx, base, dest)
	if err != nil || ordinary.NoticePolicy != nil {
		t.Fatalf("default publication changed: %+v %v", ordinary, err)
	}
	status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: ordinary.EventID}, dest)
	if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].AvailableAt != nil {
		t.Fatalf("default notice should be immediate: %+v %v", status, err)
	}
}

func TestRoutineNoticePublicationRollsBackWhenDeliveryInsertFails(t *testing.T) {
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	trigger := "notice_publish_probe_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	sql := fmt.Sprintf(`CREATE FUNCTION cairn.%s() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected notice delivery failure'; END $$; CREATE TRIGGER %s BEFORE INSERT ON cairn.agent_delivery FOR EACH ROW WHEN (NEW.consumer='%s') EXECUTE FUNCTION cairn.%s()`,
		trigger, trigger, receiver.channel.Principal, trigger)
	if _, err := sender.pool.Exec(ctx, sql); err != nil {
		t.Fatal(err)
	}
	defer sender.pool.Exec(ctx, fmt.Sprintf(`DROP TRIGGER IF EXISTS %s ON cairn.agent_delivery; DROP FUNCTION IF EXISTS cairn.%s()`, trigger, trigger))
	request := PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice",
		Ref:          RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination:  EventDestination{Type: "agent", Name: receiver.channel.Principal},
		NoticePolicy: &NoticeWakePolicyRequest{Version: 1, Urgency: "routine", DeferSeconds: 60}}
	if _, err := sender.PublishEvent(ctx, request, dest); err == nil {
		t.Fatal("injected delivery insert failure succeeded")
	}
	var events, deliveries int
	if err := sender.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event WHERE publisher=$1 AND repo=$2 AND record_id=$3 AND version=$4 AND kind='notice'`,
		sender.channel.Principal, source.Scope.Repo, source.RecordID, source.Version).Scan(&events); err != nil {
		t.Fatal(err)
	}
	if err := sender.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.publisher=$1 AND e.repo=$2 AND e.record_id=$3 AND e.version=$4 AND e.kind='notice'`,
		sender.channel.Principal, source.Scope.Repo, source.RecordID, source.Version).Scan(&deliveries); err != nil {
		t.Fatal(err)
	}
	if events != 0 || deliveries != 0 {
		t.Fatalf("failed publication left partial state: events=%d deliveries=%d", events, deliveries)
	}
	if _, err := sender.pool.Exec(ctx, fmt.Sprintf(`DROP TRIGGER %s ON cairn.agent_delivery`, trigger)); err != nil {
		t.Fatal(err)
	}
	event, err := sender.PublishEvent(ctx, request, dest)
	if err != nil {
		t.Fatalf("retry after rollback: %v", err)
	}
	status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
	if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].AvailableAt == nil ||
		!status.Deliveries[0].AvailableAt.Equal(event.CreatedAt.Add(60*time.Second)) {
		t.Fatalf("retry did not commit one fixed-deadline delivery: %+v %v", status, err)
	}
}

func TestRoutineTopicFanoutSharesOneFixedAvailability(t *testing.T) {
	ctx := context.Background()
	sender, first, source, dest := eventFixture(t)
	second := testStore(t, Channel{Principal: "second-" + uuid.NewString(), Repo: source.Scope.Repo})
	for _, subscriber := range []*Store{first, second} {
		if _, err := subscriber.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(),
			Topic: "routine-status", Active: true}); err != nil {
			t.Fatal(err)
		}
	}
	event, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(),
		Kind: "notice", Ref: RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination:  EventDestination{Type: "topic", Name: "routine-status"},
		NoticePolicy: &NoticeWakePolicyRequest{Version: 1, Urgency: "routine", DeferSeconds: 60}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
	if err != nil || len(status.Deliveries) != 2 {
		t.Fatalf("topic fanout: %+v %v", status, err)
	}
	for _, delivery := range status.Deliveries {
		if delivery.AvailableAt == nil || !delivery.AvailableAt.Equal(event.CreatedAt.Add(60*time.Second)) {
			t.Fatalf("fanout availability drifted: %+v", delivery)
		}
	}
	for _, subscriber := range []*Store{first, second} {
		next, err := subscriber.NextEvent(ctx, NextEventRequest{}, dest)
		if err != nil || next.Delivery != nil {
			t.Fatalf("routine topic notice claimed early: %+v %v", next, err)
		}
	}
}

func TestRoutineNoticeSurvivesStoreReopenUrgentNoticeAndPublisherFlush(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	sender, receiver, source, dest := f.sender, f.receiver, f.source, f.dest
	agent := f.register(nativeAgent{Binding: "routine-reopen"})
	ref := sessionRef(agent)
	publication := PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice",
		Ref:          RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination:  EventDestination{Type: "agent", Name: agent.Inbox},
		NoticePolicy: &NoticeWakePolicyRequest{Version: 1, Urgency: "routine", DeferSeconds: 60}}
	routine, err := sender.PublishEvent(ctx, publication, dest)
	if err != nil {
		t.Fatal(err)
	}
	before, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: routine.EventID}, dest)
	if err != nil || len(before.Deliveries) != 1 || before.Deliveries[0].AvailableAt == nil {
		t.Fatalf("deferred notice: %+v %v", before, err)
	}
	deadline, deliveryID := *before.Deliveries[0].AvailableAt, before.Deliveries[0].DeliveryID
	senderPrincipal, receiverPrincipal := sender.channel.Principal, receiver.channel.Principal
	sender.Close()
	receiver.Close()
	sender = testStore(t, Channel{Principal: senderPrincipal, Repo: source.Scope.Repo})
	receiver = testStore(t, Channel{Principal: receiverPrincipal, Repo: source.Scope.Repo})
	if ready, err := receiver.SessionInboxReady(ctx, ref, dest); err != nil || ready.DeliveryID != "" {
		t.Fatalf("reopen woke deferred routine notice early: %+v %v", ready, err)
	}
	urgent, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice",
		Ref: publication.Ref, Destination: publication.Destination,
		NoticePolicy: &NoticeWakePolicyRequest{Version: 1, Urgency: "urgent", DeferSeconds: 0}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ready, err := receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.EventID != urgent.EventID {
		t.Fatalf("urgent notice did not interrupt deferred routine notice: %+v %v", ready, err)
	}
	claimed, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref,
		DeliveryID: ready.DeliveryID, NativeTurnID: "urgent-native-turn", TurnExclusive: true}, dest)
	if err != nil || claimed.Attempt == nil || claimed.Attempt.Delivery.Event.EventID != urgent.EventID {
		t.Fatalf("urgent notice not claimable: %+v %v", claimed, err)
	}
	view, err := receiver.ForAgentSession(ref, dest)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(),
		DeliveryID: claimed.Attempt.Delivery.DeliveryID, LeaseID: claimed.Attempt.Delivery.LeaseID, Disposition: "handled"}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err := receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(),
		Session: ref, AttemptID: claimed.Attempt.ID, Reason: "delivery_completed"}, dest); err != nil {
		t.Fatal(err)
	}
	if ready, err := receiver.SessionInboxReady(ctx, ref, dest); err != nil || ready.DeliveryID != "" {
		t.Fatalf("routine deadline slid after urgent handling: %+v %v", ready, err)
	}
	flushed, err := sender.FlushNotice(ctx, NoticeFlushRequest{RequestID: uuid.NewString(), EventID: routine.EventID}, dest)
	if err != nil || flushed.Released != 1 || flushed.FlushedBy != senderPrincipal {
		t.Fatalf("reopened publisher flush: %+v %v", flushed, err)
	}
	after, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: routine.EventID}, dest)
	if err != nil || len(after.Deliveries) != 1 || after.Event.Ref != routine.Ref ||
		after.Event.NoticePolicy == nil || after.Event.NoticePolicy.Owner != senderPrincipal ||
		after.Event.NoticePolicy.Version != 1 || after.Deliveries[0].DeliveryID != deliveryID ||
		after.Deliveries[0].AvailableAt == nil || !after.Deliveries[0].AvailableAt.Equal(flushed.FlushedAt) ||
		!flushed.FlushedAt.Before(deadline) {
		t.Fatalf("flush changed durable notice identity or deadline: before=%+v after=%+v err=%v", before, after, err)
	}
	ready, err = receiver.SessionInboxReady(ctx, ref, dest)
	if err != nil || ready.EventID != routine.EventID || ready.DeliveryID != deliveryID {
		t.Fatalf("flushed original notice was not ready: %+v %v", ready, err)
	}
	claimed, err = receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(), Session: ref,
		DeliveryID: deliveryID, NativeTurnID: "routine-native-turn", TurnExclusive: true}, dest)
	if err != nil || claimed.Attempt == nil || claimed.Attempt.Delivery.DeliveryID != deliveryID || claimed.Attempt.Delivery.Attempts != 1 {
		t.Fatalf("flushed original notice was lost or duplicated: %+v %v", claimed, err)
	}
}
