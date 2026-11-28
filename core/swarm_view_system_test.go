package core

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestSwarmSystemEventPublication(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "swarm-system-events")
	worker, workerView := f.member("flow-system-worker")
	client := newSwarmClient(f)
	client.connect(2, -1)
	swarmFlowSetWaits(f, true)
	t.Cleanup(func() { swarmFlowSetWaits(f, false) })

	owner, err := f.op.SendSwarmEvent(ctx, SwarmSendRequest{
		SwarmActionRequest: SwarmActionRequest{RequestID: uuid.NewString(), Repo: f.repo, Campaign: f.campaign},
		To:                 worker.Inbox, Kind: "notice", Action: "review", Summary: "1 declared checkpoint or expect_by deadline is overdue.",
		Body: `{"schema":"cairn.stopped-delivery-notice/1"}`,
	})
	if err != nil || owner.Event.System != "" {
		t.Fatalf("owner's page publication: %+v, %v", owner.Event, err)
	}

	swarmRequireSystemDeliveries(t, owner.Deliveries, owner.Event.EventID, "", 1)
	request, waitNotice := swarmPublishTestWaitNotice(f, worker.Inbox)
	overdueNotice := swarmPublishTestOverdueNotice(f, workerView)

	const scheduleTopic = "swarm-system-schedule"
	for _, subscriber := range []*Store{f.rootView, workerView, f.op} {
		if _, err := subscriber.SetSubscription(ctx, SubscriptionRequest{
			RequestID: uuid.NewString(), Repo: f.repo, Topic: scheduleTopic, Active: true,
		}); err != nil {
			t.Fatal(err)
		}
	}

	scheduled, err := f.op.ScheduleEvent(ctx, ScheduleEventRequest{RequestID: uuid.NewString(),
		NotBefore: time.Now().Add(-time.Second), GraceSeconds: 300, Publication: PublishEventRequest{
			Repo: f.repo, Kind: "request", Ref: RecordVersionRef{f.source.RecordID, f.source.Version},
			Destination: EventDestination{Type: "topic", Name: scheduleTopic},
		}})
	if err != nil {
		t.Fatal(err)
	}
	tick, err := f.op.TickSchedules(ctx, ScheduleTickRequest{Repo: f.repo})
	if err != nil || len(tick.Occurrences) != 1 || tick.Occurrences[0].EventID != scheduled.OccurrenceID {
		t.Fatalf("fire the schedule: %+v, %v", tick, err)
	}

	agentNotice := f.notice(f.rootView, worker.Inbox)
	status, err := f.rootView.AgentEventStatus(ctx, EventStatusRequest{EventID: agentNotice.EventID}, f.dest)
	if err != nil || len(status.Deliveries) != 1 {
		t.Fatalf("get the agent's delivery: %+v, %v", status, err)
	}
	if _, err := f.receiver.LeaveAgent(ctx, sessionRef(worker), f.dest); err != nil {
		t.Fatal(err)
	}
	stoppedRequestID := uuid.NewString()
	stopped, err := f.op.NotifyStoppedDeliveryPublisher(ctx, StoppedDeliveryNotificationRequest{
		RequestID: stoppedRequestID, Repo: f.repo, DeliveryID: status.Deliveries[0].DeliveryID,
		RecipientAgentID: worker.AgentID, RecipientExecution: worker.ExecutionID, Stage: "waiting",
		Fingerprint: "waiting|never_launched|not_admitted||",
	})
	if err != nil || stopped.Status != "sent" || stopped.NotificationEventID == "" {
		t.Fatalf("publish stopped delivery notice: %+v, %v", stopped, err)
	}
	// Operator notices can share the publisher and summary of a system
	// notice and still carry a regular event cause.
	operatorReq := f.noticeRequest(f.root.Inbox)
	operatorReq.CausationID = owner.Event.EventID
	operatorReq.Summary = "1 declared checkpoint or expect_by deadline is overdue."
	operatorNotice, err := f.op.PublishEvent(ctx, operatorReq, swarmOperatorDestination)
	if err != nil {
		t.Fatal(err)
	}

	want := map[string]string{
		owner.Event.EventID: "", request.EventID: "", agentNotice.EventID: "", operatorNotice.EventID: "",
		waitNotice: "wait_notice", overdueNotice: "overdue_checkpoint",
		scheduled.OccurrenceID: "scheduled_event", stopped.NotificationEventID: "stopped_delivery_notice",
	}
	wantIDs := make([]string, 0, len(want))
	for id := range want {
		wantIDs = append(wantIDs, id)
	}
	// Disconnect halfway through a batch. The stored cursor must replay only
	// the suffix, with the same origin labels as the direct event-detail read.
	if first := client.connect(2, 1); len(first.Events) != 2 || !first.More {
		t.Fatalf("expected the first connection to receive a partial log: %+v", first.Events)
	}
	for range len(want) {
		if !client.connect(2, -1).More {
			break
		}
	}
	client.requireExactlyOnce(wantIDs)
	if latest := client.connect(2, -1); len(latest.Events) != 0 {
		t.Fatalf("reconnect replayed committed events: %+v", latest.Events)
	}
	for _, event := range client.log {
		swarmRequireEventSystem(t, event, want[event.EventID])
		detail, err := f.op.GetSwarmEvent(ctx, f.repo, f.campaign, event.EventID)
		if err != nil || detail.Event.Cursor != event.Cursor || detail.Event.From != event.From {
			t.Fatalf("event detail disagrees with the stream for %s: %+v, %v", event.EventID, detail.Event, err)
		}
		swarmRequireEventSystem(t, detail.Event, want[event.EventID])
		n := 1
		if event.EventID == scheduled.OccurrenceID {
			n = 3
		}
		swarmRequireSystemDeliveries(t, detail.Deliveries, event.EventID, want[event.EventID], n)
	}
	for _, event := range f.read("").Events {
		swarmRequireEventSystem(t, event, want[event.EventID])
	}

	flow := swarmFlowRead(f)
	if len(flow.Deliveries) != len(want)+2 {
		t.Fatalf("expected one delivery for each event and two more from schedule fanout: %+v", flow.Deliveries)
	}
	for _, d := range flow.Deliveries {
		kind, ok := want[d.EventID]
		if !ok || d.System != kind {
			t.Fatalf("unexpected flow delivery origin: %+v", d)
		}
	}
	selected := swarmFlowDelivery(flow, AgentEvent{EventID: scheduled.OccurrenceID}, worker.Inbox)
	preview, err := f.op.GetSwarmCancelPreview(ctx, SwarmCancelPreviewRequest{Repo: f.repo, Campaign: f.campaign, DeliveryID: selected.DeliveryID})
	if err != nil || preview.Delivery.DeliveryID != selected.DeliveryID || !preview.Cancellable {
		t.Fatalf("preview scheduled delivery: %+v, %v", preview, err)
	}
	swarmRequireSystemDeliveries(t, []SwarmDelivery{preview.Delivery}, scheduled.OccurrenceID, "scheduled_event", 1)
	swarmRequireSystemDeliveries(t, preview.Siblings, scheduled.OccurrenceID, "scheduled_event", 2)
	cancelled, err := f.op.CancelSwarmDelivery(ctx, SwarmCancelDeliveryRequest{
		SwarmDeliveryActionRequest: SwarmDeliveryActionRequest{
			SwarmActionRequest: SwarmActionRequest{RequestID: uuid.NewString(), Repo: f.repo, Campaign: f.campaign},
			DeliveryID:         selected.DeliveryID,
		},
		Reason: "Cancel the scheduled delivery in the fixture", PreviewToken: preview.PreviewToken,
	})
	if err != nil || cancelled.Outcome != "cancelled" || cancelled.Delivery.System != "scheduled_event" {
		t.Fatalf("cancel reply lost the delivery origin: %+v, %v", cancelled, err)
	}

	operatorDelivery := swarmFlowDelivery(flow, AgentEvent{EventID: scheduled.OccurrenceID}, f.op.channel.Principal)
	if operatorDelivery.DeliveryID == "" {
		t.Fatal("missing operator delivery of the scheduled event")
	}
	ack := SwarmDeliveryActionRequest{
		// The scheduler's topic event is in the campaign log through its
		// subscribers, while the operator delivery is owned by landing.
		SwarmActionRequest: SwarmActionRequest{RequestID: uuid.NewString(), Repo: f.repo},
		DeliveryID:         operatorDelivery.DeliveryID,
	}
	checkAck := func() {
		t.Helper()
		result, err := f.op.AcknowledgeSwarmDelivery(ctx, ack)
		if err != nil || result.Delivery == nil || result.Delivery.System != "scheduled_event" || !result.Delivery.Seen {
			t.Fatalf("acknowledgement reply lost the delivery origin: %+v, %v", result, err)
		}
	}
	checkAck()
	// Simulate an acknowledgement receipt written before delivery annotation.
	f.exec(`UPDATE cairn.mutation_request SET response=response #- '{delivery,system}'
		WHERE caller=$1 AND operation='swarm-acknowledge' AND request_id=$2`, f.op.channel.Principal, ack.RequestID)
	checkAck()

	active := false
	if _, err := f.rootView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: f.campaign, Active: &active,
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	landing, err := f.op.GetSwarmLanding(ctx, f.repo)
	if err != nil || len(landing.Events) != len(want) {
		t.Fatalf("landing should now contain every tested event: got %d, %v", len(landing.Events), err)
	}
	for _, event := range landing.Events {
		kind, ok := want[event.EventID]
		if !ok {
			t.Fatalf("unexpected landing event: %+v", event)
		}
		swarmRequireEventSystem(t, event, kind)
	}

	// Erasing the store-owned link should not cause a fallback to inference.
	f.exec(`UPDATE cairn.mutation_request SET response=NULL,ordinary_deleted=true
		WHERE caller=$1 AND operation='stopped-delivery-notify' AND request_id=$2`, f.op.channel.Principal, stoppedRequestID)
	landing, err = f.op.GetSwarmLanding(ctx, f.repo)
	if err != nil {
		t.Fatal(err)
	}
	for _, event := range landing.Events {
		if event.EventID == stopped.NotificationEventID {
			swarmRequireEventSystem(t, event, "")
			return
		}
	}
	t.Fatal("the stopped-delivery event disappeared after erasing its publication link")
}

func TestSwarmSystemDeliveriesOutsideLogWindow(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "swarm-system-delivery-window")
	worker, workerView := f.member("flow-system-window-worker")
	swarmFlowSetWaits(f, true)
	t.Cleanup(func() { swarmFlowSetWaits(f, false) })
	request, waitNotice := swarmPublishTestWaitNotice(f, worker.Inbox)
	overdueNotice := swarmPublishTestOverdueNotice(f, workerView)
	want := map[string]string{waitNotice: "wait_notice", overdueNotice: "overdue_checkpoint"}
	for id, kind := range want {
		detail, err := f.op.GetSwarmEvent(ctx, f.repo, f.campaign, id)
		if err != nil {
			t.Fatal(err)
		}
		swarmRequireEventSystem(t, detail.Event, kind)
		swarmRequireSystemDeliveries(t, detail.Deliveries, id, kind, 1)
	}

	// Fill the campaign window with later events that have no delivery rows.
	f.exec(`INSERT INTO cairn.agent_event(event_id,repo,publisher,kind,record_id,version,sensitivity,destination_type,destination_name,notice_action)
		SELECT gen_random_uuid(),repo,publisher,'notice',record_id,version,sensitivity,destination_type,destination_name,'review'
		FROM cairn.agent_event CROSS JOIN generate_series(1,$2) WHERE event_id=$1`, request.EventID, SwarmFlowEventWindow)
	read := f.read("")
	if len(read.Events) != SwarmFlowEventWindow {
		t.Fatalf("expected a full campaign log window, got %d events", len(read.Events))
	}
	for _, event := range read.Events {
		if _, included := want[event.EventID]; included {
			t.Fatalf("the system event %s is still in the log window", event.EventID)
		}
	}
	waitDelivery := swarmFlowDelivery(read.Snapshot.Flow, AgentEvent{EventID: waitNotice}, f.root.Inbox)
	overdueDelivery := swarmFlowDelivery(read.Snapshot.Flow, AgentEvent{EventID: overdueNotice}, f.root.Inbox)
	for _, d := range []SwarmDelivery{waitDelivery, overdueDelivery} {
		if d.State != "pending" || d.Seen || d.System != want[d.EventID] {
			t.Fatalf("old pending delivery lost its system value: %+v", d)
		}
	}
	if read.Snapshot.Flow.ByMember[f.root.Inbox].OldestPending != waitDelivery.DeliveryID {
		t.Fatalf("member's oldest pending item is not the annotated wait delivery: %+v", read.Snapshot.Flow.ByMember[f.root.Inbox])
	}
	claimed, err := f.rootView.NextEvent(ctx, NextEventRequest{}, f.dest)
	if err != nil || claimed.Delivery == nil || claimed.Delivery.DeliveryID != waitDelivery.DeliveryID {
		t.Fatalf("claim the wait notice: %+v, %v", claimed, err)
	}
	if _, err := f.rootView.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), Disposition: "handled",
		DeliveryID: waitDelivery.DeliveryID, LeaseID: claimed.Delivery.LeaseID}, f.dest); err != nil {
		t.Fatal(err)
	}
	flow := swarmFlowRead(f)
	if flow.ByMember[f.root.Inbox].OldestPending != overdueDelivery.DeliveryID ||
		swarmFlowDelivery(flow, AgentEvent{EventID: overdueNotice}, f.root.Inbox).System != "overdue_checkpoint" {
		t.Fatalf("expected annotated overdue delivery at the head after settlement: %+v", flow.ByMember[f.root.Inbox])
	}
}

func TestSwarmNeedsSystemPublisher(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "swarm-needs-system")
	_, worker := f.member("needs-system-worker")
	_, outsider := f.session("needs-system-outsider")
	operator := f.op.channel.Principal
	campaignMessage := f.notice(f.rootView, operator)
	landingMessage := f.notice(outsider, operator)

	const topic = "swarm-needs-scheduled"
	for _, name := range []string{topic, "request-overdue", "workstream-overdue"} {
		if _, err := f.op.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: f.repo, Topic: name, Active: true}); err != nil {
			t.Fatal(err)
		}
	}
	for _, name := range []string{"request-overdue", "workstream-overdue"} {
		if _, err := f.rootView.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: f.repo, Topic: name, Active: true}); err != nil {
			t.Fatal(err)
		}
	}
	deadline := time.Now().UTC().Add(-5 * time.Minute)
	if _, err := worker.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{RequestID: uuid.NewString(), Repo: f.repo,
		Stream: "system-needs-fixture", State: "active", NextCheckpoint: &deadline}, f.dest); err != nil {
		t.Fatal(err)
	}
	scheduled, err := f.op.ScheduleEvent(ctx, ScheduleEventRequest{RequestID: uuid.NewString(),
		NotBefore: time.Now().Add(-time.Second), GraceSeconds: 300, Publication: PublishEventRequest{
			Repo: f.repo, Kind: "request", Ref: RecordVersionRef{f.source.RecordID, f.source.Version},
			Destination: EventDestination{Type: "topic", Name: topic}, ExpectBy: &deadline,
		}})
	if err != nil {
		t.Fatal(err)
	}
	tick, err := f.op.TickSchedules(ctx, ScheduleTickRequest{Repo: f.repo})
	if err != nil || len(tick.Occurrences) != 1 || tick.Occurrences[0].EventID != scheduled.OccurrenceID {
		t.Fatalf("fire the schedule: %+v, %v", tick, err)
	}
	_, err = f.op.CreateWait(ctx, CreateWaitRequest{RequestID: uuid.NewString(), Collection: f.repo,
		DependencyEventUUID: scheduled.OccurrenceID, PredicateKind: WaitPredicateRequestResponse, Deadline: time.Now().Add(time.Minute)}, swarmOperatorDestination)
	requireCode(t, err, "AUTHORITY_DENIED")
	overdue, err := f.op.SweepOverdueDeadlines(ctx, OverdueSweepRequest{Repo: f.repo, GraceSeconds: intPtr(0)})
	if err != nil || len(overdue.Published) != 1 || overdue.EmittedItems != 2 {
		t.Fatalf("emit one overdue notice for the scheduled request and workstream: %+v, %v", overdue, err)
	}
	alertID := overdue.Published[0].EventID
	alert, err := f.op.GetSwarmEvent(ctx, f.repo, f.campaign, alertID)
	if err != nil {
		t.Fatal(err)
	}
	swarmRequireEventSystem(t, alert.Event, "overdue_checkpoint")
	if len(alert.Deliveries) != 1 || alert.Deliveries[0].Consumer != f.root.Inbox {
		t.Fatalf("overdue sweep should deliver only to the subscribed agent inbox: %+v", alert.Deliveries)
	}

	checkNeed := func(needs SwarmNeedsYou, rowType, eventID, subject, system string) SwarmNeed {
		t.Helper()
		for _, item := range needs.Items {
			if item.Type != rowType || item.Refs.EventID != eventID {
				continue
			}
			if item.Subject != subject || item.System != system {
				t.Fatalf("needs-you %s subject=%q system=%q, want %q and %q", item.ID, item.Subject, item.System, subject, system)
			}
			swarmRequireSystemJSON(t, item, system)
			return item
		}
		t.Fatalf("missing %s for %s: %+v", rowType, eventID, needs)
		return SwarmNeed{}
	}
	landing := swarmNeedsTestLanding(f)
	checkNeed(landing, SwarmNeedOperatorDelivery, scheduled.OccurrenceID, operator, "scheduled_event")
	checkNeed(landing, SwarmNeedOperatorDelivery, landingMessage.EventID, landingMessage.From, "")
	missed := checkNeed(landing, SwarmNeedMissedCheckpoint, alertID, operator, "scheduled_event")
	if len(missed.Refs.EventIDs) != 2 || missed.Refs.EventIDs[1] != scheduled.OccurrenceID {
		t.Fatalf("missed checkpoint lacks its scheduled source event: %+v", missed.Refs)
	}
	campaign := swarmNeedsTestSnapshot(f)
	checkNeed(campaign, SwarmNeedOperatorDelivery, campaignMessage.EventID, campaignMessage.From, "")
	checkNeed(campaign, SwarmNeedMissedCheckpoint, alertID, worker.channel.Principal, "")
}

func swarmRequireEventSystem(t *testing.T, event SwarmEvent, want string) {
	t.Helper()
	if event.System != want {
		t.Fatalf("event %s system = %q, want %q", event.EventID, event.System, want)
	}
	swarmRequireSystemJSON(t, event, want)
}

func swarmRequireSystemDeliveries(t *testing.T, deliveries []SwarmDelivery, eventID, want string, count int) {
	t.Helper()
	if len(deliveries) != count {
		t.Fatalf("event %s has %d deliveries, want %d", eventID, len(deliveries), count)
	}
	for _, d := range deliveries {
		if d.EventID != eventID || d.System != want {
			t.Fatalf("delivery %s of event %s has origin %q, want %q for event %s", d.DeliveryID, d.EventID, d.System, want, eventID)
		}
		swarmRequireSystemJSON(t, d, want)
	}
}

func swarmRequireSystemJSON(t *testing.T, envelope any, want string) {
	t.Helper()
	raw, err := json.Marshal(envelope)
	if err != nil {
		t.Fatal(err)
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil {
		t.Fatal(err)
	}
	if _, present := fields["system"]; present != (want != "") {
		t.Fatalf("%T serialized with incorrect system field: %s", envelope, fields["system"])
	}
}

func swarmPublishTestWaitNotice(f *swarmFixture, to string) (AgentEvent, string) {
	t := f.t
	t.Helper()
	ctx := context.Background()
	request := swarmFlowRequest(f, f.rootView, to, "Request with a registered wait", "", nil)
	wait := swarmFlowRegisterWait(f, f.rootView, request, WaitPredicateRequestResponse)
	f.exec(`UPDATE cairn.agent_wait SET deadline=clock_timestamp()-interval '1 second' WHERE wait_uuid=$1`, wait.WaitUUID)
	if n, err := f.op.SweepWaitTimeouts(ctx, f.repo); err != nil || n != 1 {
		t.Fatalf("sweep wait: %d, %v", n, err)
	}
	if n, err := f.op.DrainWaitOutbox(ctx, f.repo, 10); err != nil || n != 1 {
		t.Fatalf("publish wait notice: %d, %v", n, err)
	}
	return request, wait.ReservedNoticeEventUUID
}

func swarmPublishTestOverdueNotice(f *swarmFixture, producer *Store) string {
	t := f.t
	t.Helper()
	ctx := context.Background()
	if _, err := f.rootView.SetSubscription(ctx, SubscriptionRequest{
		RequestID: uuid.NewString(), Topic: "workstream-overdue", Active: true,
	}); err != nil {
		t.Fatal(err)
	}
	checkpoint := time.Now().UTC().Add(-5 * time.Minute).Truncate(time.Second)
	if _, err := producer.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{RequestID: uuid.NewString(), Repo: f.repo,
		Stream: "system-event-fixture", State: "active", Current: "Overdue publication", NextCheckpoint: &checkpoint}, f.dest); err != nil {
		t.Fatal(err)
	}
	overdue, err := f.op.SweepOverdueDeadlines(ctx, OverdueSweepRequest{Repo: f.repo, GraceSeconds: intPtr(60)})
	if err != nil || overdue.EmittedNotices != 1 || len(overdue.Published) != 1 {
		t.Fatalf("publish overdue checkpoint: %+v, %v", overdue, err)
	}
	return overdue.Published[0].EventID
}
