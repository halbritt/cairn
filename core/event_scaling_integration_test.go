package core

import (
	"context"
	"fmt"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
)

// This scenario crosses admission, reply, wait-outbox, retry and policy
// boundaries against PostgreSQL. Per-writer tests own individual refusal cases.
func TestEventScalingSaturationPreservesClosureAndUnrelatedProgress(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	f := newEventScalingScenario(t, ctx)
	f.enableEnforcement()
	f.acceptProtectedWork()
	f.saturateWithHealthyTraffic()
	f.refuseAtomicTopic()
	f.lowerLimitsBelowBacklog()
	f.finishProtectedWork()
	f.drainAndRecoverTopic()
	sessions := f.registerChurnSessions()
	f.policy.Limits.OwnerPublications = EventAdmissionRate{PerSecond: 1, Burst: 1}
	f.setPolicy()
	rootResults, elapsed := f.publishFreshRoots(sessions)
	f.assertOwnerRateBound(rootResults, elapsed)
	f.assertAccounting(0, 0, 0)
	t.Logf("synthetic PostgreSQL saturation: roots=12 accepted=%d refused=%d storm_duration=%s; no model calls or efficiency claim", f.accepted, f.refused, f.stormDuration)
}

type eventScalingScenario struct {
	t                                                 *testing.T
	ctx                                               context.Context
	repo                                              string
	profile, workerProfile, coordinator, executor, op *Store
	stalled, healthyPublisher, healthyWorker          *Store
	worker                                            AgentInstance
	dest                                              Destination
	ref                                               RecordVersionRef
	policy                                            EventAdmissionPolicy
	configured                                        EventAdmissionPolicyState
	request, reply                                    AgentEvent
	wait                                              AgentWait
	topicIntent, replyIntent                          PublishEventRequest
	accepted, refused                                 int
	stormDuration                                     time.Duration
}

func newEventScalingScenario(t *testing.T, ctx context.Context) *eventScalingScenario {
	t.Helper()
	openStore := func(ch Channel) *Store { return testStore(t, ch) }
	profile, workerProfile, owner, worker, coordinator, executor, dest := waitFixture(t, openStore)
	repo := owner.Repo
	op := openStore(Channel{Principal: "operator:scaling:" + repo, Operator: true})
	stalled := openStore(Channel{Principal: "stalled:" + repo, Repo: repo})
	healthyPublisher := openStore(Channel{Principal: "healthy-publisher:" + repo, Repo: repo})
	healthyWorker := openStore(Channel{Principal: "healthy-worker:" + repo, Repo: repo})
	if _, err := op.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatal(err)
	}
	draft := projectNote(repo)
	draft.Sensitivity = "shareable"
	source, err := coordinator.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}
	ref := RecordVersionRef{RecordID: source.RecordID, Version: source.Version}

	return &eventScalingScenario{t: t, ctx: ctx, repo: repo, profile: profile, workerProfile: workerProfile, worker: worker, coordinator: coordinator, executor: executor, op: op, stalled: stalled, healthyPublisher: healthyPublisher, healthyWorker: healthyWorker, dest: dest, ref: ref}
}

func (f *eventScalingScenario) enableEnforcement() {
	f.t.Helper()
	f.policy = EventAdmissionPolicy{Version: 1, Mode: "enforce", Workload: "synthetic integration: 12 concurrent roots to a stalled recipient, one independent request workflow, and two preaccepted closure obligations", Limits: &EventAdmissionLimits{MaxTopicRecipients: 2, MaxRecipientOutstanding: 2, MaxProducerRecipientOutstanding: 2, MaxCollectionOutstanding: 6, MaxClosureReserved: 8, MaxRecipientClosureReserved: 4}}
	unrestrictedRate := EventAdmissionRate{PerSecond: 100000, Burst: 100000}
	f.policy.Limits.ProducerPublications, f.policy.Limits.ProducerDeliveries = unrestrictedRate, unrestrictedRate
	f.policy.Limits.OwnerPublications, f.policy.Limits.OwnerDeliveries = unrestrictedRate, unrestrictedRate
	f.policy.Limits.CollectionPublications, f.policy.Limits.CollectionDeliveries = unrestrictedRate, unrestrictedRate
	if result, err := f.op.ReconcileClosureReservations(f.ctx, ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: f.repo}); err != nil || !result.Complete {
		f.t.Fatalf("reconcile before public enforcement: %+v %v", result, err)
	}
	f.setPolicy()
	if !f.configured.EnforcementReady {
		f.t.Fatal("public enforcement was enabled without complete writer support")
	}
}

func (f *eventScalingScenario) setPolicy() {
	f.t.Helper()
	configured, err := f.op.SetEventAdmissionPolicy(f.ctx, SetEventAdmissionPolicyRequest{RequestID: uuid.NewString(), Repo: f.repo, ExpectedRevision: f.configured.Revision, Policy: f.policy})
	if err != nil {
		f.t.Fatal(err)
	}
	f.configured = configured
}

func (f *eventScalingScenario) acceptProtectedWork() {
	f.t.Helper()
	var err error
	f.request, err = f.coordinator.PublishEvent(f.ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: f.ref, Destination: EventDestination{Type: "agent", Name: "agent/" + f.worker.AgentID}, CorrelationID: uuid.NewString()}, f.dest)
	if err != nil {
		f.t.Fatal(err)
	}
	f.wait, err = f.coordinator.CreateWait(f.ctx, CreateWaitRequest{RequestID: uuid.NewString(), Collection: f.repo, PredicateKind: WaitPredicateRequestResponse, DependencyEventUUID: f.request.EventID, Deadline: time.Now().Add(time.Hour)}, f.dest)
	if err != nil {
		f.t.Fatal(err)
	}
	for _, recipient := range []*Store{f.stalled, f.healthyWorker} {
		if _, err := recipient.SetSubscription(f.ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: "scaling-snapshot", Active: true}); err != nil {
			f.t.Fatal(err)
		}
	}
}

func (f *eventScalingScenario) saturateWithHealthyTraffic() {
	f.t.Helper()
	const roots = 12
	start := make(chan struct{})
	results := make(chan scalingPublication, roots)
	progress := make(chan error, 1)
	var publishers sync.WaitGroup
	stormStarted := time.Now()
	for i := 0; i < roots; i++ {
		publisher := f.coordinator
		if i%2 != 0 {
			publisher = f.healthyPublisher
		}
		intent := PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: f.ref, Destination: EventDestination{Type: "agent", Name: f.stalled.channel.Principal}, CorrelationID: uuid.NewString()}
		publishers.Add(1)
		go func() {
			defer publishers.Done()
			<-start
			started := time.Now()
			_, err := publisher.PublishEvent(f.ctx, intent, f.dest)
			results <- scalingPublication{err: err, elapsed: time.Since(started)}
		}()
	}
	publishers.Add(1)
	go func() {
		defer publishers.Done()
		<-start
		progress <- f.healthyRoundTrip()
	}()
	close(start)
	publishers.Wait()
	close(results)
	f.collectStalledResults(results, roots)
	if err := <-progress; err != nil {
		f.t.Fatalf("healthy workflow could not progress during saturation: %v", err)
	}
	f.stormDuration = time.Since(stormStarted)
}

type scalingPublication struct {
	err     error
	elapsed time.Duration
}

func (f *eventScalingScenario) collectStalledResults(results <-chan scalingPublication, roots int) {
	f.t.Helper()
	var total, longest time.Duration
	for publication := range results {
		total += publication.elapsed
		longest = max(longest, publication.elapsed)
		if publication.err == nil {
			f.accepted++
		} else {
			requireAdmissionDimension(f.t, publication.err, "recipient_outstanding")
			f.refused++
		}
	}
	if f.accepted != 2 || f.refused != roots-2 {
		f.t.Fatalf("stalled inbox exceeded its bound: accepted=%d refused=%d", f.accepted, f.refused)
	}
	f.t.Logf("synthetic concurrent publication latency: roots=%d mean=%s maximum=%s", roots, total/time.Duration(roots), longest)
}

func (f *eventScalingScenario) refuseAtomicTopic() {
	f.t.Helper()
	f.topicIntent = PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: f.ref, Destination: EventDestination{Type: "topic", Name: "scaling-snapshot"}}
	_, err := f.coordinator.PublishEvent(f.ctx, f.topicIntent, f.dest)
	requireAdmissionDimension(f.t, err, "recipient_outstanding")
	if pressure, err := f.healthyWorker.EventPressure(f.ctx, EventPressureQuery{}, f.dest); err != nil || pressure.Reservoirs.PendingDeliveries != 0 || pressure.Reservoirs.LeasedDeliveries != 0 {
		f.t.Fatalf("refused topic partially delivered to healthy member: %+v %v", pressure, err)
	}
	var refusedEvents int
	if err = f.op.pool.QueryRow(f.ctx, `SELECT count(*) FROM cairn.agent_event WHERE repo=$1 AND destination_type='topic' AND destination_name='scaling-snapshot'`, f.repo).Scan(&refusedEvents); err != nil || refusedEvents != 0 {
		f.t.Fatalf("refused topic retained an event: count=%d err=%v", refusedEvents, err)
	}
	f.assertAccounting(3, 2, 0)
	var refusedReceipt int
	if err = f.op.pool.QueryRow(f.ctx, `SELECT count(*) FROM cairn.mutation_request WHERE caller=$1 AND operation='event-publish' AND request_id=$2`, f.coordinator.channel.Principal, f.topicIntent.RequestID).Scan(&refusedReceipt); err != nil || refusedReceipt != 0 {
		f.t.Fatalf("refused topic committed retry identity: count=%d err=%v", refusedReceipt, err)
	}
}

func (f *eventScalingScenario) lowerLimitsBelowBacklog() {
	f.t.Helper()
	for range 2 {
		if _, err := f.healthyPublisher.PublishEvent(f.ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: f.ref, Destination: EventDestination{Type: "agent", Name: f.coordinator.channel.Principal}}, f.dest); err != nil {
			f.t.Fatal(err)
		}
	}
	f.policy.Limits.MaxRecipientOutstanding, f.policy.Limits.MaxProducerRecipientOutstanding, f.policy.Limits.MaxCollectionOutstanding = 1, 1, 4
	f.setPolicy()
	f.assertAccounting(5, 2, 0)
	claimed := f.claim(f.executor)
	if claimed.Event.EventID != f.request.EventID {
		f.t.Fatalf("wrong accepted request: %+v", claimed)
	}
	if _, err := f.executor.CompleteEvent(f.ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: claimed.DeliveryID, LeaseID: claimed.LeaseID, Disposition: "handled"}, f.dest); err != nil {
		f.t.Fatalf("accepted work cannot complete above lowered normal limits: %v", err)
	}
}

func (f *eventScalingScenario) finishProtectedWork() {
	f.t.Helper()
	var err error
	f.replyIntent = PublishEventRequest{RequestID: uuid.NewString(), Kind: "response", Ref: f.ref, Destination: EventDestination{Type: "agent", Name: f.coordinator.channel.Principal}, CausationID: f.request.EventID, CorrelationID: f.request.CorrelationID}
	f.reply, err = f.executor.PublishEvent(f.ctx, f.replyIntent, f.dest)
	if err != nil {
		f.t.Fatalf("validated reply cannot use its protected reserve: %v", err)
	}
	if drained, err := f.op.DrainWaitOutbox(f.ctx, f.repo, 10); err != nil || drained != 1 {
		f.t.Fatalf("wait terminal notice cannot drain at normal saturation: drained=%d err=%v", drained, err)
	}
	if drained, err := f.op.DrainWaitOutbox(f.ctx, f.repo, 10); err != nil || drained != 0 {
		f.t.Fatalf("terminal outbox duplicated delivery: drained=%d err=%v", drained, err)
	}
	f.assertAccounting(4, 0, 2)
	// A new Store recovers the committed UUID from PostgreSQL rather than
	// acquiring another delivery or capacity charge after a lost response.
	reopenedProfile := testStore(f.t, f.workerProfile.channel)
	reopened, err := reopenedProfile.ForAgentSession(sessionRef(f.worker), f.dest)
	if err != nil {
		f.t.Fatal(err)
	}
	duplicateReply, err := reopened.PublishEvent(f.ctx, f.replyIntent, f.dest)
	if err != nil || duplicateReply.EventID != f.reply.EventID {
		f.t.Fatalf("committed reply retry after Store reopen: %+v %v", duplicateReply, err)
	}
	f.assertAccounting(4, 0, 2)
}

func (f *eventScalingScenario) drainAndRecoverTopic() {
	f.t.Helper()
	seen := map[string]int{}
	for range 4 {
		delivery := f.claim(f.coordinator)
		seen[delivery.Event.EventID]++
		if _, err := f.coordinator.CompleteEvent(f.ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery.DeliveryID, LeaseID: delivery.LeaseID, Disposition: "handled"}, f.dest); err != nil {
			f.t.Fatal(err)
		}
	}
	if seen[f.reply.EventID] != 1 || seen[f.wait.ReservedNoticeEventUUID] != 1 {
		f.t.Fatalf("closure obligations missing or duplicated: %+v", seen)
	}
	f.assertAccounting(2, 0, 0)
	for range 2 {
		delivery := f.claim(f.stalled)
		if _, err := f.stalled.CompleteEvent(f.ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery.DeliveryID, LeaseID: delivery.LeaseID, Disposition: "handled"}, f.dest); err != nil {
			f.t.Fatal(err)
		}
	}
	retried, err := f.coordinator.PublishEvent(f.ctx, f.topicIntent, f.dest)
	if err != nil {
		f.t.Fatalf("unchanged refused topic UUID cannot recover after drain: %v", err)
	}
	duplicate, err := f.coordinator.PublishEvent(f.ctx, f.topicIntent, f.dest)
	if err != nil || duplicate.EventID != retried.EventID {
		f.t.Fatalf("committed topic retry changed identity: %+v %v", duplicate, err)
	}
	fanout, err := f.coordinator.AgentEventStatus(f.ctx, EventStatusRequest{EventID: retried.EventID}, f.dest)
	if err != nil || len(fanout.Deliveries) != 2 {
		f.t.Fatalf("recovered topic lost its atomic recipient set: %+v %v", fanout, err)
	}
	for _, recipient := range []*Store{f.stalled, f.healthyWorker} {
		delivery := f.claim(recipient)
		if delivery.Event.EventID != retried.EventID {
			f.t.Fatalf("unexpected recovered topic delivery: %+v", delivery)
		}
		if _, err := recipient.CompleteEvent(f.ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery.DeliveryID, LeaseID: delivery.LeaseID, Disposition: "handled"}, f.dest); err != nil {
			f.t.Fatal(err)
		}
	}
	f.assertAccounting(0, 0, 0)
	f.t.Logf("synthetic recovered topic fanout=%d", len(fanout.Deliveries))
}

func (f *eventScalingScenario) registerChurnSessions() []*Store {
	f.t.Helper()
	// The producer session changes on every pair of roots while its validated
	// registration owner stays fixed. Empty topics still cost publication units.
	sessions := make([]*Store, 4)
	for i := range sessions {
		agent, err := f.profile.RegisterAgent(f.ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: fmt.Sprintf("scaling-churn-%d", i), NativeSessionID: uuid.NewString(), Metadata: existingSessionMetadata("codex", "idle")}, f.dest)
		if err != nil {
			f.t.Fatal(err)
		}
		sessions[i], err = f.profile.ForAgentSession(sessionRef(agent), f.dest)
		if err != nil {
			f.t.Fatal(err)
		}
	}
	return sessions
}

type scalingRootResult struct {
	publisher *Store
	intent    PublishEventRequest
	event     AgentEvent
	err       error
}

func (f *eventScalingScenario) publishFreshRoots(sessions []*Store) (<-chan scalingRootResult, time.Duration) {
	f.t.Helper()
	rateStart := make(chan struct{})
	rateResults := make(chan scalingRootResult, len(sessions)*2)
	var rootPublishers sync.WaitGroup
	for _, publisher := range sessions {
		for range 2 {
			intent := PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: f.ref, Destination: EventDestination{Type: "topic", Name: "scaling-empty-roots"}, CorrelationID: uuid.NewString()}
			rootPublishers.Add(1)
			go func() {
				defer rootPublishers.Done()
				<-rateStart
				event, err := publisher.PublishEvent(f.ctx, intent, f.dest)
				rateResults <- scalingRootResult{publisher: publisher, intent: intent, event: event, err: err}
			}()
		}
	}
	rateStarted := time.Now()
	close(rateStart)
	rootPublishers.Wait()
	rateDuration := time.Since(rateStarted)
	close(rateResults)
	return rateResults, rateDuration
}

func (f *eventScalingScenario) assertOwnerRateBound(rateResults <-chan scalingRootResult, rateDuration time.Duration) {
	f.t.Helper()
	rateAccepted, rateRefused := 0, 0
	var committed scalingRootResult
	for result := range rateResults {
		if result.err == nil {
			rateAccepted++
			committed = result
		} else {
			requireAdmissionDimension(f.t, result.err, "owner_publications")
			rateRefused++
		}
	}
	// One initial token plus elapsed refill is an upper bound, independent
	// of scheduling and how many session or correlation UUIDs were created.
	// The measured window contains every publication, so only whole elapsed
	// refill tokens can fund an additional root.
	allowance := 1 + int(rateDuration/time.Second)
	if rateAccepted == 0 || rateAccepted > allowance || rateRefused == 0 {
		f.t.Fatalf("owner rate reset through fresh roots/sessions: accepted=%d refused=%d elapsed=%s allowance=%d", rateAccepted, rateRefused, rateDuration, allowance)
	}
	duplicateRoot, err := committed.publisher.PublishEvent(f.ctx, committed.intent, f.dest)
	if err != nil || duplicateRoot.EventID != committed.event.EventID {
		f.t.Fatalf("committed root retry was throttled or republished: %+v %v", duplicateRoot, err)
	}
	var rootEvents int
	if err = f.op.pool.QueryRow(f.ctx, `SELECT count(*) FROM cairn.agent_event WHERE repo=$1 AND destination_type='topic' AND destination_name='scaling-empty-roots'`, f.repo).Scan(&rootEvents); err != nil || rootEvents != rateAccepted {
		f.t.Fatalf("rate refusal/committed retry left extra roots: count=%d accepted=%d err=%v", rootEvents, rateAccepted, err)
	}
	f.assertAccounting(0, 0, 0)
	f.t.Logf("synthetic same-owner roots: sessions=%d accepted=%d refused=%d duration=%s refill_rate=1/s burst=1", 4, rateAccepted, rateRefused, rateDuration)
}

func (f *eventScalingScenario) healthyRoundTrip() error {
	request, err := f.healthyPublisher.PublishEvent(f.ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: f.ref, Destination: EventDestination{Type: "agent", Name: f.healthyWorker.channel.Principal}, CorrelationID: uuid.NewString()}, f.dest)
	if err != nil {
		return err
	}
	claimed, err := f.healthyWorker.NextEvent(f.ctx, NextEventRequest{}, f.dest)
	if err != nil {
		return fmt.Errorf("claim healthy request: %w", err)
	}
	if claimed.Delivery == nil || claimed.Delivery.Event.EventID != request.EventID {
		return fmt.Errorf("healthy request %s claim: %+v", request.EventID, claimed)
	}
	if _, err := f.healthyWorker.CompleteEvent(f.ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: claimed.Delivery.DeliveryID, LeaseID: claimed.Delivery.LeaseID, Disposition: "handled"}, f.dest); err != nil {
		return err
	}
	response, err := f.healthyWorker.PublishEvent(f.ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "response", Ref: f.ref, Destination: EventDestination{Type: "agent", Name: f.healthyPublisher.channel.Principal}, CausationID: request.EventID, CorrelationID: request.CorrelationID}, f.dest)
	if err != nil {
		return err
	}
	received, err := f.healthyPublisher.NextEvent(f.ctx, NextEventRequest{}, f.dest)
	if err != nil {
		return fmt.Errorf("claim healthy response: %w", err)
	}
	if received.Delivery == nil || received.Delivery.Event.EventID != response.EventID {
		return fmt.Errorf("healthy response %s claim: %+v", response.EventID, received)
	}
	_, err = f.healthyPublisher.CompleteEvent(f.ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: received.Delivery.DeliveryID, LeaseID: received.Delivery.LeaseID, Disposition: "handled"}, f.dest)
	return err
}

func (f *eventScalingScenario) assertAccounting(normal, reserved, delivered int64) {
	f.t.Helper()
	pressure, err := f.op.CollectionEventPressure(f.ctx, EventPressureQuery{Repo: f.repo})
	if err != nil {
		f.t.Fatal(err)
	}
	closures, err := f.op.ClosureReservations(f.ctx, ClosureReservationQuery{Repo: f.repo}, Destination{Name: "local", AllowLocal: true})
	if err != nil {
		f.t.Fatal(err)
	}
	counts := closures.Counts
	if !counts.Available || counts.Reserved == nil || counts.DeliveredOutstanding == nil || counts.Outstanding == nil || *counts.Reserved != reserved || *counts.DeliveredOutstanding != delivered || *counts.Outstanding != reserved+delivered {
		f.t.Fatalf("closure accounting: want reserved=%d delivered=%d got %+v", reserved, delivered, counts)
	}
	reservoirs := pressure.Reservoirs
	if reservoirs.ClosureState != "available" || reservoirs.ClosureReserved == nil || *reservoirs.ClosureReserved != reserved || reservoirs.ClosureDeliveredOutstanding == nil || *reservoirs.ClosureDeliveredOutstanding != delivered || reservoirs.NormalOutstanding == nil || *reservoirs.NormalOutstanding != normal {
		f.t.Fatalf("public pressure disagrees with obligation accounting: normal=%d reserved=%d delivered=%d pressure=%+v", normal, reserved, delivered, reservoirs)
	}
	f.t.Logf("synthetic queue: normal=%d reserved=%d delivered_closure=%d oldest_seconds=%.6f", normal, reserved, delivered, pressure.OldestOutstandingSeconds)
	actual := pressure.Reservoirs.PendingDeliveries + pressure.Reservoirs.LeasedDeliveries
	if actual != normal+delivered {
		f.t.Fatalf("normal and protected charges overlap or disappear: want normal=%d delivered=%d got %+v", normal, delivered, pressure.Reservoirs)
	}
}

func (f *eventScalingScenario) claim(recipient *Store) AgentDelivery {
	f.t.Helper()
	next, err := recipient.NextEvent(f.ctx, NextEventRequest{}, f.dest)
	if err != nil || next.Delivery == nil {
		f.t.Fatalf("claim accepted delivery: %+v %v", next, err)
	}
	return *next.Delivery
}
