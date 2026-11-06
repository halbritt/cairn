package core

import (
	"context"
	"reflect"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
)

// Candidate workload: one coordinator, two workers, two queued publications
// each. These deliberately small limits expose boundaries without model calls.
func admissionFixturePolicy() EventAdmissionPolicy {
	rate := EventAdmissionRate{PerSecond: 2, Burst: 4}
	return EventAdmissionPolicy{Version: 1, Mode: "observe", Workload: "synthetic: coordinator and two workers; two queued publications per worker", Limits: &EventAdmissionLimits{MaxTopicRecipients: 2, MaxRecipientOutstanding: 2, MaxProducerRecipientOutstanding: 2, MaxCollectionOutstanding: 4, MaxClosureReserved: 4, MaxRecipientClosureReserved: 2, ProducerPublications: rate, ProducerDeliveries: rate, OwnerPublications: rate, OwnerDeliveries: rate, CollectionPublications: rate, CollectionDeliveries: rate}}
}
func TestEventAdmissionPolicyFiniteBounds(t *testing.T) {
	if err := (EventAdmissionPolicy{Version: 1, Mode: "disabled"}).validate(); err != nil {
		t.Fatal(err)
	}
	p := admissionFixturePolicy()
	if err := p.validate(); err != nil {
		t.Fatal(err)
	}
	for _, change := range []func(*EventAdmissionPolicy){func(p *EventAdmissionPolicy) { p.Version = 2 }, func(p *EventAdmissionPolicy) { p.Mode = "unknown" }, func(p *EventAdmissionPolicy) { p.Limits = nil }, func(p *EventAdmissionPolicy) { p.Workload = "" }, func(p *EventAdmissionPolicy) { p.Limits.MaxTopicRecipients = 10001 }, func(p *EventAdmissionPolicy) { p.Limits.MaxRecipientOutstanding = 0 }, func(p *EventAdmissionPolicy) { p.Limits.OwnerDeliveries.PerSecond = -1 }, func(p *EventAdmissionPolicy) { p.Limits.CollectionPublications.Burst = 1000000001 }} {
		p = admissionFixturePolicy()
		change(&p)
		requireCode(t, p.validate(), "INVALID_REQUEST")
	}
}
func TestEventAdmissionPolicyRevisionAndObserveOnly(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	op := testStore(t, Channel{Principal: "operator:admission", Operator: true})
	initial, err := op.EventAdmissionPolicy(ctx, EventAdmissionPolicyQuery{Repo: r.Scope.Repo})
	if err != nil || initial.Revision != 0 || initial.Policy.Mode != "disabled" || initial.Policy.Limits != nil {
		t.Fatalf("initial: %+v %v", initial, err)
	}
	req := SetEventAdmissionPolicyRequest{RequestID: uuid.NewString(), Repo: r.Scope.Repo, Policy: admissionFixturePolicy()}
	_, err = a.SetEventAdmissionPolicy(ctx, req)
	requireCode(t, err, "AUTHORITY_DENIED")
	state, err := op.SetEventAdmissionPolicy(ctx, req)
	if err != nil || state.Revision != 1 || state.EnforcementReady {
		t.Fatalf("set: %+v %v", state, err)
	}
	again, err := op.SetEventAdmissionPolicy(ctx, req)
	if err != nil || !reflect.DeepEqual(state, again) {
		t.Fatalf("retry: %+v %v", again, err)
	}
	changed := req
	changed.Policy.Workload = "different"
	_, err = op.SetEventAdmissionPolicy(ctx, changed)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	changed.RequestID = uuid.NewString()
	_, err = op.SetEventAdmissionPolicy(ctx, changed)
	requireCode(t, err, "VERSION_CONFLICT")
	changed.Policy.Mode = "enforce"
	changed.ExpectedRevision = 1
	_, err = op.SetEventAdmissionPolicy(ctx, changed)
	requireCode(t, err, "FEATURE_UNAVAILABLE")
	for i := 0; i < 3; i++ {
		publishFixture(t, a, b, r, dest)
	}
	preview, err := a.PreviewEventAdmission(ctx, EventAdmissionPreviewRequest{Destination: EventDestination{Type: "agent", Name: b.channel.Principal}}, dest)
	if err != nil || preview.PolicyRevision != 1 || !reflect.DeepEqual(preview.WouldRefuse, []string{"recipient_outstanding", "producer_recipient_outstanding"}) {
		t.Fatalf("overload preview: %+v %v", preview, err)
	}
	// Preview cannot reserve capacity, and observe mode does not refuse writes.
	publishFixture(t, a, b, r, dest)
	own, err := b.EventPressure(ctx, EventPressureQuery{}, dest)
	if err != nil || own.Reservoirs.PendingDeliveries != 4 || own.Scope != "inbox" || own.Reservoirs.ClosureReserved != nil || own.Reservoirs.ClosureState != "unsupported" || own.Policy.Policy.Workload != "" || own.Policy.UpdatedBy != "" {
		t.Fatalf("own: %+v %v", own, err)
	}
	// Duplicate policy editors have one winner under the collection lock.
	var wg sync.WaitGroup
	codes := make(chan string, 2)
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			q := req
			q.RequestID = uuid.NewString()
			q.ExpectedRevision = 1
			_, err := op.SetEventAdmissionPolicy(ctx, q)
			if err == nil {
				codes <- ""
			} else {
				codes <- Code(err)
			}
		}()
	}
	wg.Wait()
	close(codes)
	ok, conflict := 0, 0
	for code := range codes {
		if code == "" {
			ok++
		} else if code == "VERSION_CONFLICT" {
			conflict++
		} else {
			t.Fatalf("unexpected editor result %s", code)
		}
	}
	if ok != 1 || conflict != 1 {
		t.Fatalf("edit race %d/%d", ok, conflict)
	}
}
func TestEventPressureScopedAndFanoutSnapshot(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	op := testStore(t, Channel{Principal: "operator:pressure", Operator: true})
	c := testStore(t, Channel{Principal: "agent:third:" + uuid.NewString(), Repo: r.Scope.Repo})
	for _, s := range []*Store{b, c} {
		if _, err := s.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: "pressure", Active: true}); err != nil {
			t.Fatal(err)
		}
	}
	topic, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{r.RecordID, r.Version}, Destination: EventDestination{Type: "topic", Name: "pressure"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = c.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: "pressure", Active: false}); err != nil {
		t.Fatal(err)
	}
	page, err := a.EventFanout(ctx, EventFanoutQuery{Limit: 1}, dest)
	if err != nil || len(page.Events) != 1 || page.Events[0].EventID != topic.EventID || page.Events[0].Deliveries != 2 {
		t.Fatalf("fanout snapshot: %+v %v", page, err)
	}
	other, err := b.EventFanout(ctx, EventFanoutQuery{}, dest)
	if err != nil || len(other.Events) != 0 {
		t.Fatalf("recipient learned sibling fanout: %+v %v", other, err)
	}
	all, err := op.CollectionEventPressure(ctx, EventPressureQuery{Repo: r.Scope.Repo, Limit: 1})
	if err != nil || !all.More || len(all.Recipients) != 1 || all.Reservoirs.PendingDeliveries != 2 {
		t.Fatalf("collection page: %+v %v", all, err)
	}
	second, err := op.CollectionEventPressure(ctx, EventPressureQuery{Repo: r.Scope.Repo, After: all.NextAfter, Limit: 1})
	if err != nil || second.More || len(second.Recipients) != 1 || second.Recipients[0].Identity.Principal == all.Recipients[0].Identity.Principal {
		t.Fatalf("second page: %+v %v", second, err)
	}
	own, err := b.EventPressure(ctx, EventPressureQuery{}, dest)
	if err != nil || len(own.Recipients) != 1 || own.Recipients[0].Identity.Principal != b.channel.Principal || own.Reservoirs.PendingDeliveries != 1 {
		t.Fatalf("privacy: %+v %v", own, err)
	}

	published, err := a.EventPressure(ctx, EventPressureQuery{}, dest)
	if err != nil || published.PublishedLastMinute != 1 || published.DeliveriesLastMinute != 2 {
		t.Fatalf("sender publication window: %+v %v", published, err)
	}
	if own.PublishedLastMinute != 0 || own.DeliveriesLastMinute != 0 {
		t.Fatalf("receiver inherited publication fanout: %+v", own)
	}
	_, err = b.CollectionEventPressure(ctx, EventPressureQuery{})
	requireCode(t, err, "AUTHORITY_DENIED")
	nextFixture(t, b, dest)
	own, err = b.EventPressure(ctx, EventPressureQuery{}, dest)
	if err != nil || own.Reservoirs.PendingDeliveries != 0 || own.Reservoirs.LeasedDeliveries != 1 {
		t.Fatalf("leased: %+v %v", own, err)
	}
	// Source sensitivity still controls the read destination.
	local := projectNote(r.Scope.Repo)
	record, err := a.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: local})
	if err != nil {
		t.Fatal(err)
	}
	_, err = a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{record.RecordID, 1}, Destination: EventDestination{Type: "agent", Name: b.channel.Principal}}, Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatal(err)
	}
	own, err = b.EventPressure(ctx, EventPressureQuery{}, dest)
	if err != nil || own.Reservoirs.PendingDeliveries != 0 {
		t.Fatalf("local metadata leaked: %+v %v", own, err)
	}
}
func TestEventPressureStoppedSubscriptionAndBoundedPreview(t *testing.T) {
	ctx := context.Background()
	a, _, r, dest := eventFixture(t)
	op := testStore(t, Channel{Principal: "operator:stopped", Operator: true})
	for i := 0; i < 3; i++ {
		agent, err := a.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "pressure-fixture", NativeSessionID: uuid.NewString(), Metadata: AgentMetadata{Harness: "codex", Project: "synthetic", Workspace: "/fixture", State: "idle", DeliveryMode: "existing-session"}}, dest)
		if err != nil {
			t.Fatal(err)
		}
		ref := AgentSessionRef{AgentID: agent.AgentID, ExecutionID: agent.ExecutionID}
		view, err := a.ForAgentSession(ref, dest)
		if err != nil {
			t.Fatal(err)
		}
		_, err = view.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: "pressure", Active: true})
		if err != nil {
			t.Fatal(err)
		}
		if i == 0 {
			_, err = a.LeaveAgent(ctx, ref, dest)
			if err != nil {
				t.Fatal(err)
			}
		} else if i == 1 {
			_, err = a.pool.Exec(ctx, `UPDATE cairn.agent_session SET expires_at=clock_timestamp()-interval '1 second' WHERE agent_id=$1`, agent.AgentID)
			if err != nil {
				t.Fatal(err)
			}
		}
	}
	_, err := op.SetEventAdmissionPolicy(ctx, SetEventAdmissionPolicyRequest{RequestID: uuid.NewString(), Repo: r.Scope.Repo, Policy: admissionFixturePolicy()})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := a.PreviewEventAdmission(ctx, EventAdmissionPreviewRequest{Destination: EventDestination{Type: "topic", Name: "pressure"}}, dest)
	if err != nil || preview.RecipientCount != nil || preview.RecipientCountExact || preview.RecipientCountState != "withheld" || !reflect.DeepEqual(preview.WouldRefuse, []string{"topic_recipients"}) {
		t.Fatalf("bounded forecast: %+v %v", preview, err)
	}

	operatorPreview, err := op.PreviewEventAdmission(ctx, EventAdmissionPreviewRequest{Repo: r.Scope.Repo, Destination: EventDestination{Type: "topic", Name: "pressure"}}, Destination{Name: "local", AllowLocal: true})
	if err != nil || operatorPreview.RecipientCount == nil || *operatorPreview.RecipientCount != 3 || operatorPreview.RecipientCountExact || operatorPreview.RecipientCountState != "lower_bound" {
		t.Fatalf("operator fanout forecast: %+v %v", operatorPreview, err)
	}
	_, err = a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "progress_update", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{Type: "topic", Name: "pressure"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	pressure, err := op.CollectionEventPressure(ctx, EventPressureQuery{Repo: r.Scope.Repo})
	if err != nil {
		t.Fatal(err)
	}
	found := map[string]int{}
	for _, recipient := range pressure.Recipients {
		found[recipient.Presence]++
		if recipient.Identity.Kind != "session" || recipient.ActiveSubscriptions != 1 {
			t.Fatalf("session identity: %+v", recipient)
		}
	}
	if found["stopped"] != 1 || found["offline"] != 1 || found["online"] != 1 || pressure.Reservoirs.PendingDeliveries != 3 || pressure.Reservoirs.StoppedDeliveries != 1 {
		t.Fatalf("presence: %+v %+v", found, pressure)
	}
	// Unsubscribing never changes the retained recipient snapshot.
	page, err := a.EventFanout(ctx, EventFanoutQuery{}, dest)
	if err != nil || len(page.Events) != 1 || page.Events[0].Deliveries != 3 {
		t.Fatalf("fanout: %+v %v", page, err)
	}
}

func TestEventPressureDisposableMixedWorkload(t *testing.T) {
	ctx := context.Background()
	_, openStore := waitTestDB(t)
	repo := "pressure-load:" + uuid.NewString()
	f := setupOrganicFixtureWithStores(t, repo, openStore)
	f.activateCapability(t)
	policy := admissionFixturePolicy()
	if _, err := f.operator.SetEventAdmissionPolicy(ctx, SetEventAdmissionPolicyRequest{RequestID: uuid.NewString(), Repo: repo, Policy: policy}); err != nil {
		t.Fatal(err)
	}
	consumer := f.consumerStore(t)
	if _, err := consumer.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: repo, Topic: "load", Active: true}); err != nil {
		t.Fatal(err)
	}
	source := RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version}
	base := PublishEventRequest{Kind: "notice", Ref: source, Repo: repo, Destination: EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID}}
	for _, kind := range []string{"direct", "topic"} {
		req := base
		req.RequestID = uuid.NewString()
		if kind == "topic" {
			req.Destination = EventDestination{Type: "topic", Name: "load"}
		}
		first, err := f.publisher.PublishEvent(ctx, req, f.dest)
		if err != nil {
			t.Fatal(err)
		}
		second, err := f.publisher.PublishEvent(ctx, req, f.dest)
		if err != nil || first.EventID != second.EventID {
			t.Fatalf("retry fanout: %+v %+v %v", first, second, err)
		}
	}
	if _, err := f.operator.ConfigureWorkerPool(ctx, WorkerPoolConfigRequest{RequestID: uuid.NewString(), Repo: repo, Name: "pressure", Enabled: true, MaxPending: 4, MaxPendingPerPublisher: 4}); err != nil {
		t.Fatal(err)
	}
	poolEvent, err := f.publisher.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Repo: repo, Kind: "request", Ref: source, Destination: EventDestination{Type: "pool", Name: "pressure"}, Pool: &PoolRequirements{Workspace: "/fixture"}}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	scheduled := base
	scheduled.RequestID = ""
	if _, err = f.operator.ScheduleEvent(ctx, ScheduleEventRequest{RequestID: uuid.NewString(), NotBefore: time.Now().Add(time.Hour), GraceSeconds: 60, Publication: scheduled}); err != nil {
		t.Fatal(err)
	}
	f.publishNotice(t, "quiet workload result")
	all, err := f.operator.CollectionEventPressure(ctx, EventPressureQuery{Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	if all.Reservoirs.PendingDeliveries != 2 || all.Reservoirs.QueuedOrganicNotices != 1 || all.Reservoirs.UnassignedPoolRequests != 1 || all.Reservoirs.PendingSchedules != 1 || all.PublishedLastMinute != 4 {
		t.Fatalf("mixed reservoirs: %+v", all)
	}
	own, err := consumer.EventPressure(ctx, EventPressureQuery{}, f.dest)
	if err != nil || own.Reservoirs.PendingDeliveries != 2 || own.Reservoirs.QueuedOrganicNotices != 1 || own.Reservoirs.UnassignedPoolRequests != 0 || own.Reservoirs.PendingSchedules != 0 {
		t.Fatalf("mixed own view: %+v %v", own, err)
	}
	preview, err := f.publisher.PreviewEventAdmission(ctx, EventAdmissionPreviewRequest{Repo: repo, Destination: base.Destination}, f.dest)
	if err != nil || !reflect.DeepEqual(preview.WouldRefuse, []string{"recipient_outstanding", "producer_recipient_outstanding"}) {
		t.Fatalf("queued organic accounting: %+v %v", preview, err)
	}
	fanout, err := f.publisher.EventFanout(ctx, EventFanoutQuery{Repo: repo, Limit: 2}, f.dest)
	if err != nil || !fanout.More || len(fanout.Events) != 2 {
		t.Fatalf("fanout page: %+v %v", fanout, err)
	}
	rest, err := f.publisher.EventFanout(ctx, EventFanoutQuery{Repo: repo, After: fanout.NextAfter, Limit: 2}, f.dest)
	if err != nil || rest.More || len(rest.Events) != 2 || rest.Events[0].RecipientSnapshotState != "awaiting_assignment" || rest.Events[1].QueuedOrganicNotices != 1 {
		t.Fatalf("fanout tail: %+v %v", rest, err)
	}

	if _, err = f.operator.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: repo, PoolEventID: poolEvent.EventID, Reason: "disposable pressure fixture done"}); err != nil {
		t.Fatal(err)
	}
	all, err = f.operator.CollectionEventPressure(ctx, EventPressureQuery{Repo: repo})
	if err != nil || all.Reservoirs.UnassignedPoolRequests != 0 {
		t.Fatalf("closed pool counted as pending: %+v %v", all, err)
	}
	rest, err = f.publisher.EventFanout(ctx, EventFanoutQuery{Repo: repo, After: fanout.NextAfter, Limit: 2}, f.dest)
	if err != nil || rest.Events[0].RecipientSnapshotState == "awaiting_assignment" {
		t.Fatalf("closed pool awaiting assignment: %+v %v", rest, err)
	}
	if _, err = f.operator.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatal(err)
	}
	request, err := consumer.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Repo: repo, Kind: "request", Ref: source, Destination: EventDestination{Type: "agent", Name: f.publisher.channel.Principal}}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	_, err = consumer.CreateWait(ctx, CreateWaitRequest{RequestID: uuid.NewString(), Collection: repo, PredicateKind: WaitPredicateRequestResponse, DependencyEventUUID: request.EventID, Deadline: time.Now().Add(time.Hour)}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	own, err = consumer.EventPressure(ctx, EventPressureQuery{}, f.dest)
	if err != nil || own.Reservoirs.WaitingTerminalNotices != 1 || own.Reservoirs.PendingWaitOutbox != 0 {
		t.Fatalf("waiting reservoir: %+v %v", own, err)
	}
	_, err = f.publisher.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Repo: repo, Kind: "response", Ref: source, Destination: base.Destination, CausationID: request.EventID}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	own, err = consumer.EventPressure(ctx, EventPressureQuery{}, f.dest)
	if err != nil || own.Reservoirs.WaitingTerminalNotices != 0 || own.Reservoirs.PendingWaitOutbox != 1 {
		t.Fatalf("outbox transfer: %+v %v", own, err)
	}
}
