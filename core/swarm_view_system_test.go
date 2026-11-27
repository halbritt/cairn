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

	request := swarmFlowRequest(f, f.rootView, worker.Inbox, "Request with a registered wait", "", nil)
	wait := swarmFlowRegisterWait(f, f.rootView, request, WaitPredicateRequestResponse)
	f.exec(`UPDATE cairn.agent_wait SET deadline=clock_timestamp()-interval '1 second' WHERE wait_uuid=$1`, wait.WaitUUID)
	if n, err := f.op.SweepWaitTimeouts(ctx, f.repo); err != nil || n != 1 {
		t.Fatalf("sweep wait: %d, %v", n, err)
	}
	if n, err := f.op.DrainWaitOutbox(ctx, f.repo, 10); err != nil || n != 1 {
		t.Fatalf("publish wait notice: %d, %v", n, err)
	}

	if _, err := f.rootView.SetSubscription(ctx, SubscriptionRequest{
		RequestID: uuid.NewString(), Topic: "workstream-overdue", Active: true,
	}); err != nil {
		t.Fatal(err)
	}
	checkpoint := time.Now().UTC().Add(-5 * time.Minute).Truncate(time.Second)
	if _, err := workerView.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{RequestID: uuid.NewString(), Repo: f.repo,
		Stream: "system-event-fixture", State: "active", Current: "Overdue publication", NextCheckpoint: &checkpoint}, f.dest); err != nil {
		t.Fatal(err)
	}
	overdue, err := f.op.SweepOverdueDeadlines(ctx, OverdueSweepRequest{Repo: f.repo, GraceSeconds: intPtr(60)})
	if err != nil || overdue.EmittedNotices != 1 || len(overdue.Published) != 1 {
		t.Fatalf("publish overdue checkpoint: %+v, %v", overdue, err)
	}

	scheduled, err := f.op.ScheduleEvent(ctx, ScheduleEventRequest{RequestID: uuid.NewString(),
		NotBefore: time.Now().Add(-time.Second), GraceSeconds: 300, Publication: PublishEventRequest{
			Repo: f.repo, Kind: "request", Ref: RecordVersionRef{f.source.RecordID, f.source.Version},
			Destination: EventDestination{Type: "agent", Name: worker.Inbox},
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
		wait.ReservedNoticeEventUUID: "wait_notice", overdue.Published[0].EventID: "overdue_checkpoint",
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
	}
	for _, event := range f.read("").Events {
		swarmRequireEventSystem(t, event, want[event.EventID])
	}

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

func swarmRequireEventSystem(t *testing.T, event SwarmEvent, want string) {
	t.Helper()
	if event.System != want {
		t.Fatalf("event %s system = %q, want %q", event.EventID, event.System, want)
	}
	raw, err := json.Marshal(event)
	if err != nil {
		t.Fatal(err)
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil {
		t.Fatal(err)
	}
	if _, present := fields["system"]; present != (want != "") {
		t.Fatalf("event %s serialized with incorrect system field: %s", event.EventID, fields["system"])
	}
}
