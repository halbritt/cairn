package core

import (
	"context"
	"maps"
	"slices"
	"testing"
	"time"

	"github.com/google/uuid"
)

func swarmFlowRead(f *swarmFixture) SwarmFlow {
	f.t.Helper()
	snapshot, err := f.op.GetSwarmSnapshot(context.Background(), SwarmSnapshotRequest{Repo: f.repo, Campaign: f.campaign})
	if err != nil || len(snapshot.SectionErrors) != 0 {
		f.t.Fatalf("swarm snapshot: %v, sections: %v", err, snapshot.SectionErrors)
	}
	return snapshot.Flow
}

func swarmFlowRequest(f *swarmFixture, from *Store, to, summary, correlation string, group *ResponseGroupSpec) AgentEvent {
	f.t.Helper()
	req := f.noticeRequest(to)
	req.Kind, req.Action, req.Summary, req.CorrelationID, req.ResponseGroup = "request", "", summary, correlation, group
	event, err := from.PublishEvent(context.Background(), req, f.dest)
	if err != nil {
		f.t.Fatal(err)
	}
	return event
}

func swarmFlowSetWaits(f *swarmFixture, enabled bool) {
	f.t.Helper()
	if _, err := f.op.SetCoordinationWaitsEnabled(context.Background(), WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: enabled}); err != nil {
		f.t.Fatal(err)
	}
}

func swarmFlowRegisterWait(f *swarmFixture, owner *Store, event AgentEvent, predicate string) AgentWait {
	f.t.Helper()
	wait, err := owner.CreateWait(context.Background(), CreateWaitRequest{
		RequestID: uuid.NewString(), Collection: f.repo, PredicateKind: predicate,
		DependencyEventUUID: event.EventID, Deadline: time.Now().Add(10 * time.Minute),
	}, f.dest)
	if err != nil {
		f.t.Fatal(err)
	}
	return wait
}

// Capture every stored delivery column so a projection that claims, releases,
// settles, or updates an item cannot pass by restoring the visible state.
func swarmFlowStoredDeliveries(f *swarmFixture, events ...AgentEvent) map[string]string {
	f.t.Helper()
	ids := make([]string, 0, len(events))
	for _, event := range events {
		ids = append(ids, event.EventID)
	}
	rows, err := f.op.pool.Query(context.Background(), `SELECT event_id::text, (to_jsonb(d)||jsonb_build_object('xmin',d.xmin::text))::text
		FROM cairn.agent_delivery d WHERE event_id=ANY($1::uuid[])`, ids)
	if err != nil {
		f.t.Fatal(err)
	}
	defer rows.Close()
	stored := map[string]string{}
	for rows.Next() {
		var id, row string
		if err := rows.Scan(&id, &row); err != nil {
			f.t.Fatal(err)
		}
		stored[id] = row
	}
	if err := rows.Err(); err != nil {
		f.t.Fatal(err)
	}
	return stored
}

func swarmFlowDelivery(flow SwarmFlow, event AgentEvent, consumer string) SwarmDelivery {
	for _, d := range flow.Deliveries {
		if d.EventID == event.EventID && d.Consumer == consumer {
			return d
		}
	}
	return SwarmDelivery{}
}

func TestSwarmFlowQueueAndExactWaits(t *testing.T) {
	f := newSwarmFixture(t, "swarm-flow-queue")
	worker, workerView := f.member("flow-queue-worker")
	peer, peerView := f.member("flow-queue-peer")
	swarmFlowSetWaits(f, true)
	t.Cleanup(func() { swarmFlowSetWaits(f, false) })

	notice := f.notice(f.rootView, worker.Inbox)
	correlation := uuid.NewString()
	direct := swarmFlowRequest(f, f.rootView, worker.Inbox, "Exact direct request", correlation, nil)
	noWait := swarmFlowRequest(f, f.rootView, worker.Inbox, "Same correlation, no wait", correlation, nil)
	group := swarmFlowRequest(f, peerView, worker.Inbox, "Group request", uuid.NewString(), &ResponseGroupSpec{
		Deadline: time.Now().Add(5 * time.Minute), PartialPolicy: "all",
	})
	directWait := swarmFlowRegisterWait(f, f.rootView, direct, WaitPredicateRequestResponse)
	groupWait := swarmFlowRegisterWait(f, peerView, group, WaitPredicateResponseGroupClosed)
	before := swarmFlowStoredDeliveries(f, notice, direct, noWait, group)

	flow := swarmFlowRead(f)
	after := swarmFlowStoredDeliveries(f, notice, direct, noWait, group)
	if len(before) != 4 || !maps.Equal(before, after) {
		t.Fatalf("projection changed the delivery rows: before=%v, after=%v", before, after)
	}
	workerFlow, hasWorker := flow.ByMember[worker.Inbox]
	noticeDelivery := swarmFlowDelivery(flow, notice, worker.Inbox)
	if flow.WaitsFeature != "active" || !hasWorker || workerFlow.Pending != 4 || noticeDelivery.DeliveryID == "" || workerFlow.OldestPending != noticeDelivery.DeliveryID {
		t.Fatalf("expected the old notice at the inbox head: feature=%s, member=%+v, notice=%+v", flow.WaitsFeature, workerFlow, noticeDelivery)
	}
	if !slices.Equal(workerFlow.PeerWaits, []string{directWait.WaitUUID, groupWait.WaitUUID}) ||
		!slices.Equal(flow.ByMember[f.root.Inbox].WaitsOn, []string{directWait.WaitUUID}) ||
		!slices.Equal(flow.ByMember[peer.Inbox].WaitsOn, []string{groupWait.WaitUUID}) {
		t.Fatalf("incorrect wait attribution: %+v", flow.ByMember)
	}
	if len(flow.Waits) != 2 {
		t.Fatalf("want two exact waits: %+v", flow.Waits)
	}
	for _, w := range flow.Waits {
		wanted, event := directWait, direct
		if w.WaitID == groupWait.WaitUUID {
			wanted, event = groupWait, group
		}
		if w.WaitID != wanted.WaitUUID || w.Owner != wanted.OwnerPrincipal || w.Predicate != wanted.PredicateKind ||
			w.DependencyEventID != event.EventID || w.DependencySummary != event.Summary || !slices.Equal(w.Awaited, []string{worker.Inbox}) {
			t.Fatalf("wrong wait or correlation-only join: %+v", w)
		}
	}
	next, err := workerView.NextEvent(context.Background(), NextEventRequest{}, f.dest)
	if err != nil || next.Delivery == nil || next.Delivery.DeliveryID != workerFlow.OldestPending {
		t.Fatalf("NextEvent disagrees with the projected queue head: %+v, %v", next, err)
	}
	swarmFlowSetWaits(f, false)
	disabled := swarmFlowRead(f)
	if disabled.WaitsFeature != "disabled" || len(disabled.Waits) != 2 || !slices.Equal(disabled.ByMember[worker.Inbox].PeerWaits, workerFlow.PeerWaits) {
		t.Fatalf("disabled registration hid accepted waits: %+v", disabled)
	}
}

func TestSwarmFlowDisplayStates(t *testing.T) {
	f := newSwarmFixture(t, "swarm-flow-states")
	worker, _ := f.member("flow-state-worker")
	for _, tc := range []struct{ state, code, display string }{
		{"pending", "", "pending"}, {"leased", "", "leased"}, {"leased", "", "held"},
		{"handled", "", "settled"}, {"ignored", "superseded", "superseded"}, {"ignored", "", "ignored"},
		{"failed", "worker_failed", "failed"}, {"failed", "operator_cancelled", "cancelled"},
		{"failed", "operator_released", "cancelled"}, {"failed", "consumer_retired", "cancelled"},
		{"failed", "admission_expired", "expired"}, {"failed", "task_deadline", "expired"},
	} {
		name := tc.display
		if tc.code != "" {
			name += "_" + tc.code
		}
		t.Run(name, func(t *testing.T) {
			event := f.notice(f.rootView, worker.Inbox)
			lease := uuid.NewString()
			f.exec(`UPDATE cairn.agent_delivery SET state=$2, code=$3,
				lease_id=CASE WHEN $2='leased' THEN $4::uuid END,
				lease_until=CASE WHEN $2='leased' THEN clock_timestamp()+interval '1 minute' END,
				completed_at=CASE WHEN $2 IN ('handled','ignored','failed') THEN clock_timestamp() END
				WHERE event_id=$1`, event.EventID, tc.state, tc.code, lease)
			if tc.display == "held" {
				f.exec(`INSERT INTO cairn.agent_wake_attempt(attempt_id,delivery_id,repo,consumer,lease_id)
					SELECT $1,delivery_id,$2,consumer,lease_id FROM cairn.agent_delivery WHERE event_id=$3`, uuid.NewString(), f.repo, event.EventID)
			}
			d := swarmFlowDelivery(swarmFlowRead(f), event, worker.Inbox)
			if d.State != tc.state || d.Display != tc.display || d.Code != tc.code || d.Held != (tc.display == "held") {
				t.Fatalf("wrong display projection: %+v, want %+v", d, tc)
			}
		})
	}
}

func TestSwarmFlowSeenAndReplied(t *testing.T) {
	f := newSwarmFixture(t, "swarm-flow-flips")
	worker, workerView := f.member("flow-flips-worker")
	request := swarmFlowRequest(f, f.rootView, worker.Inbox, "Seen and replied", uuid.NewString(), nil)
	before := swarmFlowDelivery(swarmFlowRead(f), request, worker.Inbox)
	if before.Display != "pending" || before.Seen || before.Replied || before.From != f.root.Inbox || before.Kind != "request" || before.Summary != request.Summary {
		t.Fatalf("request before claim: %+v", before)
	}
	f.claim(sessionRef(worker), before.DeliveryID, "flow-native-turn", true)
	claimed := swarmFlowDelivery(swarmFlowRead(f), request, worker.Inbox)
	if !claimed.Seen || claimed.Replied || claimed.Attempts != 1 || claimed.Display != "held" {
		t.Fatalf("request after native claim: %+v", claimed)
	}
	response := publishTestResponse(t, context.Background(), workerView, f.repo, f.root.AgentID, request.EventID, request.CorrelationID, "response", f.dest)
	flow := swarmFlowRead(f)
	replied := swarmFlowDelivery(flow, request, worker.Inbox)
	other := swarmFlowDelivery(flow, response, f.root.Inbox)
	if !replied.Seen || !replied.Replied || other.CausationID != request.EventID || other.Replied || other.Kind != "response" {
		t.Fatalf("request/response after reply: %+v / %+v", replied, other)
	}
}

func TestSwarmFlowCampaignScopeAndDisabledWaits(t *testing.T) {
	f := newSwarmFixture(t, "swarm-flow-scope")
	worker, _ := f.member("flow-scope-worker")
	outsider, _ := f.session("flow-scope-outsider")
	swarmFlowSetWaits(f, false)
	outside := f.notice(f.sender, outsider.Inbox)
	beforeCampaign := f.notice(f.sender, worker.Inbox)
	// An older event to a current member does not belong to the campaign log,
	// but an open delivery still belongs to that member's inbox.
	f.exec(`UPDATE cairn.agent_event SET created_at=clock_timestamp()-interval '1 day' WHERE event_id=$1`, beforeCampaign.EventID)
	flow := swarmFlowRead(f)
	if flow.WaitsFeature != "disabled" || len(flow.Waits) != 0 || len(flow.Deliveries) != 1 ||
		flow.Deliveries[0].EventID != beforeCampaign.EventID || flow.Deliveries[0].DeliveryID != flow.ByMember[worker.Inbox].OldestPending ||
		swarmFlowDelivery(flow, outside, outsider.Inbox).DeliveryID != "" {
		t.Fatalf("wrong scope or disabled state: %+v", flow)
	}
	if _, found := flow.ByMember[outsider.Inbox]; found {
		t.Fatalf("outside session appeared as a member: %+v", flow.ByMember)
	}
}

func TestSwarmFlowOldestEligible(t *testing.T) {
	f := newSwarmFixture(t, "swarm-flow-eligibility")
	worker, workerView := f.member("flow-eligibility-worker")
	deferred := f.notice(f.rootView, worker.Inbox)
	f.exec(`UPDATE cairn.agent_delivery SET available_at=clock_timestamp()+interval '1 hour' WHERE event_id=$1`, deferred.EventID)
	expired := swarmFlowRequest(f, f.rootView, worker.Inbox, "Closed admission", "", nil)
	f.expireAdmission(expired.EventID)
	leaseExpired := f.notice(f.rootView, worker.Inbox)
	f.exec(`UPDATE cairn.agent_delivery SET state='leased', attempts=1, lease_id=$2,
		lease_until=clock_timestamp()-interval '1 minute' WHERE event_id=$1`, leaseExpired.EventID, uuid.NewString())
	f.notice(f.rootView, worker.Inbox)
	flow := swarmFlowRead(f)
	oldest := swarmFlowDelivery(flow, leaseExpired, worker.Inbox)
	if oldest.DeliveryID == "" || oldest.Display != "leased" || !oldest.Seen || flow.ByMember[worker.Inbox].OldestPending != oldest.DeliveryID {
		t.Fatalf("wrong eligibility for deferred, admission-closed and lapsed lease rows: %+v", flow)
	}
	next, err := workerView.NextEvent(context.Background(), NextEventRequest{}, f.dest)
	if err != nil || next.Delivery == nil || next.Delivery.DeliveryID != oldest.DeliveryID {
		t.Fatalf("NextEvent disagrees with the projected queue head: %+v, %v", next, err)
	}
}

func TestSwarmFlowDeliveryWindowAndLimit(t *testing.T) {
	f := newSwarmFixture(t, "swarm-flow-window")
	worker, _ := f.member("flow-window-worker")
	oldOpen := f.notice(f.rootView, worker.Inbox)
	oldSettled := f.notice(f.rootView, worker.Inbox)
	f.exec(`UPDATE cairn.agent_delivery SET state='handled',completed_at=clock_timestamp() WHERE event_id=$1`, oldSettled.EventID)
	// Fill the event window independently of delivery fanout.
	f.exec(`INSERT INTO cairn.agent_event(event_id,repo,publisher,kind,record_id,version,sensitivity,destination_type,destination_name,notice_action)
		SELECT gen_random_uuid(),repo,publisher,kind,record_id,version,sensitivity,destination_type,destination_name,notice_action
		FROM cairn.agent_event CROSS JOIN generate_series(1,$2) WHERE event_id=$1`, oldOpen.EventID, SwarmFlowEventWindow)
	newSettled := f.notice(f.rootView, worker.Inbox)
	f.exec(`UPDATE cairn.agent_delivery SET state='handled',completed_at=clock_timestamp() WHERE event_id=$1`, newSettled.EventID)
	flow := swarmFlowRead(f)
	if len(flow.Deliveries) != 2 || flow.DeliveriesTruncated || flow.Deliveries[0].EventID != newSettled.EventID || flow.Deliveries[1].EventID != oldOpen.EventID {
		t.Fatalf("window did not keep old open and recent settled deliveries: %+v", flow)
	}

	newOpen := f.notice(f.rootView, worker.Inbox)
	f.exec(`INSERT INTO cairn.agent_delivery(delivery_id,event_id,consumer)
		SELECT gen_random_uuid(),$1,'synthetic-fanout-'||n FROM generate_series(1,$2) AS n`, newOpen.EventID, swarmFlowDeliveryLimit)
	flow = swarmFlowRead(f)
	if !flow.DeliveriesTruncated || len(flow.Deliveries) != swarmFlowDeliveryLimit {
		t.Fatalf("wrong delivery limit: len=%d, truncated=%t", len(flow.Deliveries), flow.DeliveriesTruncated)
	}
	oldest := swarmFlowDelivery(flow, oldOpen, worker.Inbox)
	if oldest.DeliveryID == "" || flow.ByMember[worker.Inbox].OldestPending != oldest.DeliveryID || flow.ByMember[worker.Inbox].Pending != 2 {
		t.Fatalf("truncation lost the oldest delivery or changed counts: oldest=%+v member=%+v", oldest, flow.ByMember[worker.Inbox])
	}
}
