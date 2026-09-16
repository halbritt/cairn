package core

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"testing"

	"github.com/google/uuid"
)

func TestAgentEventsCompletionRollbackAndConcurrentCompletion(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	publishFixture(t, a, b, r, dest)
	d := nextFixture(t, b, dest)
	// Fail the delivery update after its result insert. Both must roll back.
	trigger := "event_probe_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	sql := fmt.Sprintf(`CREATE FUNCTION cairn.%s() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected completion failure'; END $$; CREATE TRIGGER %s BEFORE UPDATE ON cairn.agent_delivery FOR EACH ROW WHEN (OLD.delivery_id='%s'::uuid AND NEW.state='handled') EXECUTE FUNCTION cairn.%s()`, trigger, trigger, d.DeliveryID, trigger)
	if _, err := b.pool.Exec(ctx, sql); err != nil {
		t.Fatal(err)
	}
	defer b.pool.Exec(ctx, fmt.Sprintf(`DROP TRIGGER IF EXISTS %s ON cairn.agent_delivery; DROP FUNCTION IF EXISTS cairn.%s()`, trigger, trigger))
	result := projectNote(r.Scope.Repo)
	result.Body = "atomic failure fixture"
	result.Sensitivity = "shareable"
	req := CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: d.DeliveryID, LeaseID: d.LeaseID, Disposition: "handled", Draft: &result}
	if _, err := b.CompleteEvent(ctx, req, dest); err == nil {
		t.Fatal("injected failure succeeded")
	}
	var count int
	if err := b.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.record_version WHERE repo=$1 AND body=$2`, r.Scope.Repo, result.Body).Scan(&count); err != nil || count != 0 {
		t.Fatalf("orphan result: %d %v", count, err)
	}
	if _, err := b.pool.Exec(ctx, fmt.Sprintf(`DROP TRIGGER %s ON cairn.agent_delivery`, trigger)); err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	errs := make(chan error, 2)
	results := make(chan AgentDelivery, 2)
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); out, err := b.CompleteEvent(ctx, req, dest); errs <- err; results <- out }()
	}
	wg.Wait()
	close(errs)
	close(results)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	first := <-results
	second := <-results
	if first.Result == nil || second.Result == nil || *first.Result != *second.Result {
		t.Fatal("retry created duplicate result")
	}
}

func TestAgentEventsForgettingAndSubscriptionPages(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	e := publishFixture(t, a, b, r, dest)
	op, root := testOperator(t)
	preview, err := op.PreviewDeletion(ctx, r.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: r.RecordID, ExpectedVersion: 1, GrantID: root.ID, PreviewID: preview.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	d := nextFixture(t, b, dest)
	if d.Event.EventID != e.EventID {
		t.Fatal("forgotten source lost communication")
	}
	_, err = b.History(ctx, RecordHistoryRequest{RecordID: r.RecordID, Version: 1}, dest)
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")
	_, err = b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: d.DeliveryID, LeaseID: d.LeaseID, Disposition: "failed", Code: "source_unavailable"}, dest)
	if err != nil {
		t.Fatal(err)
	}
	for _, topic := range []string{"alpha", "beta", "gamma"} {
		_, err = b.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: topic, Active: true})
		if err != nil {
			t.Fatal(err)
		}
	}
	p, err := b.EventSubscriptions(ctx, EventQuery{Limit: 2})
	if err != nil || !p.More || len(p.Subscriptions) != 2 || p.NextTopic != "beta" {
		t.Fatalf("first subscriptions: %+v %v", p, err)
	}
	p, err = b.EventSubscriptions(ctx, EventQuery{Limit: 2, Topic: p.NextTopic})
	if err != nil || p.More || len(p.Subscriptions) != 1 || p.Subscriptions[0].Topic != "gamma" {
		t.Fatalf("next subscriptions: %+v %v", p, err)
	}
}

func TestAgentEventsConcurrentPublicationCursor(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	var wg sync.WaitGroup
	errs := make(chan error, 12)
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "update", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"agent", b.channel.Principal}}, dest)
			errs <- err
		}()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	var after int64
	count := 0
	for {
		p, err := b.Events(ctx, EventQuery{After: after, Limit: 3}, dest)
		if err != nil {
			t.Fatal(err)
		}
		for _, e := range p.Events {
			if e.Position <= after {
				t.Fatal("cursor went backward")
			}
			after = e.Position
			count++
		}
		if !p.More {
			break
		}
	}
	if count != 12 {
		t.Fatalf("cursor lost events: %d", count)
	}
}

func TestAgentEventsRestoreFenceInvalidatesLease(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	publishFixture(t, a, b, r, dest)
	d := nextFixture(t, b, dest)
	op := testStore(t, Channel{Principal: "event-restore-operator", Operator: true})
	if _, err := op.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "Invalidate event leases during the disposable restore test"}); err != nil {
		t.Fatal(err)
	}
	_, err := b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: d.DeliveryID, LeaseID: d.LeaseID, Disposition: "handled"}, dest)
	requireCode(t, err, "STALE_LEASE")
	next := nextFixture(t, b, dest)
	if next.Event.EventID != d.Event.EventID || next.LeaseID == d.LeaseID {
		t.Fatal("restore did not preserve work with a fresh lease")
	}
}

func TestAgentEventsPrivateCausationAndForgottenResult(t *testing.T) {
	ctx := context.Background()
	a, b, r, hosted := eventFixture(t)
	local := Destination{"local", true}
	privateDraft := projectNote(r.Scope.Repo)
	privateDraft.Sensitivity = "local"
	private, err := a.Create(ctx, CreateRequest{uuid.NewString(), privateDraft})
	if err != nil {
		t.Fatal(err)
	}
	parent := publishFixture(t, a, b, private, local)
	_, err = a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "response", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"agent", b.channel.Principal}, CausationID: parent.EventID}, local)
	requireCode(t, err, "DESTINATION_PROHIBITED")
	e := publishFixture(t, a, b, r, hosted)
	d := nextFixture(t, b, hosted)
	result := projectNote(r.Scope.Repo)
	result.Body = "result content stays out of event rows"
	result.Sensitivity = "local"
	req := CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: d.DeliveryID, LeaseID: d.LeaseID, Disposition: "handled", Draft: &result}
	done, err := b.CompleteEvent(ctx, req, hosted)
	if err != nil {
		t.Fatal(err)
	}
	status, err := a.AgentEventStatus(ctx, EventStatusRequest{EventID: e.EventID}, hosted)
	if err != nil || status.Deliveries[0].Result != nil {
		t.Fatalf("hosted status disclosed local result: %+v %v", status, err)
	}
	op, root := testOperator(t)
	preview, err := op.PreviewDeletion(ctx, done.Result.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: done.Result.RecordID, ExpectedVersion: 1, GrantID: root.ID, PreviewID: preview.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	retried, err := b.CompleteEvent(ctx, req, hosted)
	if err != nil || *retried.Result != *done.Result {
		t.Fatalf("forgotten result retry: %+v %v", retried, err)
	}
	_, err = b.History(ctx, RecordHistoryRequest{RecordID: done.Result.RecordID, Version: 1}, local)
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")
	var retained string
	if err = b.pool.QueryRow(ctx, `SELECT response::text FROM cairn.mutation_request WHERE caller=$1 AND operation='event-complete' AND request_id=$2`, b.channel.Principal, req.RequestID).Scan(&retained); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(retained, result.Body) {
		t.Fatal("completion cache retained result body")
	}
}

func eventFixture(t *testing.T) (*Store, *Store, Record, Destination) {
	t.Helper()
	repo := uuid.NewString()
	a := testStore(t, Channel{Principal: "publisher-" + repo, Repo: repo})
	b := testStore(t, Channel{Principal: "consumer-" + repo, Repo: repo})
	draft := projectNote(repo)
	draft.Sensitivity = "shareable"
	draft.Body = "source version one"
	r, err := a.Create(context.Background(), CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	return a, b, r, Destination{"hosted", false}
}
func publishFixture(t *testing.T, a, b *Store, r Record, dest Destination) AgentEvent {
	t.Helper()
	e, err := a.PublishEvent(context.Background(), PublishEventRequest{RequestID: uuid.NewString(), Repo: r.Scope.Repo, Kind: "request", Ref: RecordVersionRef{r.RecordID, r.Version}, Destination: EventDestination{"agent", b.channel.Principal}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	return e
}
func nextFixture(t *testing.T, b *Store, dest Destination) AgentDelivery {
	t.Helper()
	next, err := b.NextEvent(context.Background(), NextEventRequest{}, dest)
	if err != nil || next.Delivery == nil {
		t.Fatalf("next: %+v %v", next, err)
	}
	return *next.Delivery
}

func TestAgentEventsPublishRetryExactVersionAndAtomicCompletion(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	req := PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"agent", b.channel.Principal}}
	e, err := a.PublishEvent(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	again, err := a.PublishEvent(ctx, req, dest)
	if err != nil || again.EventID != e.EventID {
		t.Fatalf("retry: %+v %v", again, err)
	}
	req.Kind = "notice"
	_, err = a.PublishEvent(ctx, req, dest)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	if _, err = a.Revise(ctx, ReviseRequest{uuid.NewString(), r.RecordID, 1, r.Scope.Repo, "changed source"}); err != nil {
		t.Fatal(err)
	}
	d := nextFixture(t, b, dest)
	if d.Event.Ref.Version != 1 || d.Event.From != a.channel.Principal || d.Event.EventID != e.EventID {
		t.Fatalf("event identity changed: %+v", d)
	}
	history, err := b.History(ctx, RecordHistoryRequest{RecordID: d.Event.Ref.RecordID, Version: d.Event.Ref.Version}, dest)
	if err != nil || *history.Versions[0].Body != "source version one" {
		t.Fatalf("source: %+v %v", history, err)
	}
	result := projectNote(r.Scope.Repo)
	result.Body = "one durable result"
	result.Sensitivity = "shareable"
	complete := CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: d.DeliveryID, LeaseID: d.LeaseID, Disposition: "handled", Draft: &result}
	completed, err := b.CompleteEvent(ctx, complete, dest)
	if err != nil || completed.Result == nil || completed.State != "handled" {
		t.Fatalf("complete: %+v %v", completed, err)
	}
	retry, err := b.CompleteEvent(ctx, complete, dest)
	if err != nil || *retry.Result != *completed.Result {
		t.Fatalf("lost reply: %+v %v", retry, err)
	}
	complete.RequestID = uuid.NewString()
	_, err = b.CompleteEvent(ctx, complete, dest)
	requireCode(t, err, "STALE_LEASE")
	stored, err := b.History(ctx, RecordHistoryRequest{RecordID: completed.Result.RecordID, Version: 1}, dest)
	if err != nil || stored.Versions[0].ObservedWriter != b.channel.Principal {
		t.Fatalf("result attribution: %+v %v", stored, err)
	}
	next, err := b.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil || next.Delivery != nil {
		t.Fatalf("acked redelivered: %+v %v", next, err)
	}
	status, err := a.AgentEventStatus(ctx, EventStatusRequest{EventID: e.EventID}, dest)
	if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].Result == nil || status.Deliveries[0].LeaseID != "" {
		t.Fatalf("status: %+v %v", status, err)
	}
	stats, err := b.AgentEventStats(ctx, EventQuery{}, dest)
	if err != nil || stats.Handled != 1 || stats.Deliveries != 1 {
		t.Fatalf("metrics: %+v %v", stats, err)
	}
	_, err = a.Delete(ctx, DeleteRequest{uuid.NewString(), r.RecordID, 2})
	requireCode(t, err, "FORGET_REQUIRED")
}

func TestAgentEventsTopicMembershipFanoutAndPaging(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	c := testStore(t, Channel{Principal: "third-" + r.Scope.Repo, Repo: r.Scope.Repo})
	set := func(s *Store, active bool) {
		t.Helper()
		_, err := s.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: "work", Active: active})
		if err != nil {
			t.Fatal(err)
		}
	}
	pub := func() AgentEvent {
		t.Helper()
		e, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "future-kind", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"topic", "work"}}, dest)
		if err != nil {
			t.Fatal(err)
		}
		return e
	}
	pub()
	set(b, true)
	set(c, true)
	first := pub()
	set(b, false)
	second := pub()
	set(b, true)
	third := pub()
	bd := nextFixture(t, b, dest)
	cd := nextFixture(t, c, dest)
	if bd.Event.EventID != first.EventID || cd.Event.EventID != first.EventID {
		t.Fatal("subscription backfill or missing fanout")
	}
	_, err := b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: bd.DeliveryID, LeaseID: bd.LeaseID, Disposition: "ignored", Code: "unsupported_kind"}, dest)
	if err != nil {
		t.Fatal(err)
	}
	status, err := a.AgentEventStatus(ctx, EventStatusRequest{EventID: first.EventID}, dest)
	if err != nil || len(status.Deliveries) != 2 {
		t.Fatalf("status: %+v %v", status, err)
	}
	for _, d := range status.Deliveries {
		if d.Consumer == c.channel.Principal && d.State != "leased" {
			t.Fatal("one subscriber acknowledged another")
		}
	}
	bd = nextFixture(t, b, dest)
	if bd.Event.EventID != third.EventID {
		t.Fatalf("unsubscribe gap lost: %+v second=%s", bd, second.EventID)
	}
	page, err := b.Events(ctx, EventQuery{Limit: 1}, dest)
	if err != nil || len(page.Events) != 1 || !page.More || page.Events[0].EventID != first.EventID {
		t.Fatalf("first page: %+v %v", page, err)
	}
	page, err = b.Events(ctx, EventQuery{Limit: 1, After: page.NextAfter}, dest)
	if err != nil || len(page.Events) != 1 || page.More || page.Events[0].EventID != third.EventID {
		t.Fatalf("next page: %+v %v", page, err)
	}
	subs, err := b.EventSubscriptions(ctx, EventQuery{})
	if err != nil || len(subs.Subscriptions) != 1 || !subs.Subscriptions[0].Active {
		t.Fatalf("subscriptions: %+v %v", subs, err)
	}
}

func TestAgentEventsLeaseConcurrencyRetryExpiryAndRollback(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	publishFixture(t, a, b, r, dest)
	var wg sync.WaitGroup
	claimed := make(chan AgentDelivery, 8)
	errs := make(chan error, 8)
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			n, err := b.NextEvent(ctx, NextEventRequest{}, dest)
			if err != nil {
				errs <- err
			} else if n.Delivery != nil {
				claimed <- *n.Delivery
			}
		}()
	}
	wg.Wait()
	close(claimed)
	close(errs)
	for err := range errs {
		t.Fatal(err)
	}
	if len(claimed) != 1 {
		t.Fatalf("claims: %d", len(claimed))
	}
	d := <-claimed
	renewed, err := b.ChangeEventLease(ctx, EventLeaseRequest{DeliveryID: d.DeliveryID, LeaseID: d.LeaseID, LeaseSeconds: 600}, true, dest)
	if err != nil || !renewed.LeaseUntil.After(*d.LeaseUntil) {
		t.Fatalf("renew: %+v %v", renewed, err)
	}
	if _, err = b.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET lease_until=clock_timestamp()-interval '1 second' WHERE delivery_id=$1`, d.DeliveryID); err != nil {
		t.Fatal(err)
	}
	_, err = b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: d.DeliveryID, LeaseID: d.LeaseID, Disposition: "handled"}, dest)
	requireCode(t, err, "STALE_LEASE")
	current := nextFixture(t, b, dest)
	if current.LeaseID == d.LeaseID || current.Attempts != 2 {
		t.Fatal("lease did not fence old attempt")
	}
	_, err = b.ChangeEventLease(ctx, EventLeaseRequest{DeliveryID: d.DeliveryID, LeaseID: d.LeaseID}, false, dest)
	requireCode(t, err, "STALE_LEASE")
	bad := projectNote("outside")
	_, err = b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: current.DeliveryID, LeaseID: current.LeaseID, Disposition: "handled", Draft: &bad}, dest)
	requireCode(t, err, "AUTHORITY_DENIED")
	if _, err = b.ChangeEventLease(ctx, EventLeaseRequest{DeliveryID: current.DeliveryID, LeaseID: current.LeaseID}, false, dest); err != nil {
		t.Fatal(err)
	}
	current = nextFixture(t, b, dest)
	_, err = b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: current.DeliveryID, LeaseID: current.LeaseID, Disposition: "failed", Code: "processing_failed"}, dest)
	if err != nil {
		t.Fatal(err)
	}
	stats, err := b.AgentEventStats(ctx, EventQuery{}, dest)
	if err != nil || stats.Redeliveries != 2 || stats.Failed != 1 {
		t.Fatalf("stats: %+v %v", stats, err)
	}
}

func TestAgentEventsIdentityAndVisibility(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	e := publishFixture(t, a, b, r, dest)
	c := testStore(t, Channel{Principal: "other-" + r.Scope.Repo, Repo: r.Scope.Repo})
	_, err := c.AgentEventStatus(ctx, EventStatusRequest{EventID: e.EventID}, dest)
	requireCode(t, err, "NOT_FOUND")
	_, err = c.NextEvent(ctx, NextEventRequest{Agent: b.channel.Principal}, dest)
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = c.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Agent: b.channel.Principal, Topic: "work", Active: true})
	requireCode(t, err, "AUTHORITY_DENIED")
	d := nextFixture(t, b, dest)
	_, err = c.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: d.DeliveryID, LeaseID: d.LeaseID, Disposition: "handled"}, dest)
	requireCode(t, err, "NOT_FOUND")
	local := projectNote(r.Scope.Repo)
	local.Sensitivity = "local"
	private, err := a.Create(ctx, CreateRequest{uuid.NewString(), local})
	if err != nil {
		t.Fatal(err)
	}
	req := PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{private.RecordID, 1}, Destination: EventDestination{"agent", b.channel.Principal}}
	_, err = a.PublishEvent(ctx, req, dest)
	requireCode(t, err, "NOT_FOUND")
	_, err = a.PublishEvent(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	_, err = a.PublishEvent(ctx, req, dest)
	requireCode(t, err, "NOT_FOUND")
	next, err := b.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil || next.Delivery != nil {
		t.Fatal("hosted inbox exposed local event")
	}
	page, err := b.Events(ctx, EventQuery{}, dest)
	if err != nil || len(page.Events) != 1 {
		t.Fatalf("private event in history: %+v %v", page, err)
	}
	_, err = b.Events(ctx, EventQuery{Repo: "outside"}, dest)
	requireCode(t, err, "AUTHORITY_DENIED")
}
