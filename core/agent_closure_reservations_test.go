package core

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func closureOperator(t *testing.T, repo string) *Store {
	t.Helper()
	return testStore(t, Channel{Principal: "operator-" + repo, Operator: true})
}

// setClosurePolicy configures a policy through the public operator setter with
// the same validation as production. Closure tests constrain only closure
// capacity: rate budgets and ordinary queue caps are finite but generous so
// that only the closure dimension under test can refuse.
func setClosurePolicy(t *testing.T, repo, mode string, collection, recipient int64) {
	t.Helper()
	ctx := context.Background()
	op := closureOperator(t, repo)
	generous := EventAdmissionRate{PerSecond: 1000000, Burst: 1000000}
	policy := EventAdmissionPolicy{Version: 1, Mode: mode, Workload: "closure capacity test; rates and queues deliberately unconstraining", Limits: &EventAdmissionLimits{
		MaxTopicRecipients: 100, MaxRecipientOutstanding: 100000, MaxProducerRecipientOutstanding: 100000, MaxCollectionOutstanding: 100000,
		MaxClosureReserved: collection, MaxRecipientClosureReserved: recipient,
		ProducerPublications: generous, ProducerDeliveries: generous, OwnerPublications: generous,
		OwnerDeliveries: generous, CollectionPublications: generous, CollectionDeliveries: generous,
	}}
	current, err := op.EventAdmissionPolicy(ctx, EventAdmissionPolicyQuery{Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = op.SetEventAdmissionPolicy(ctx, SetEventAdmissionPolicyRequest{RequestID: uuid.NewString(), Repo: repo, ExpectedRevision: current.Revision, Policy: policy}); err != nil {
		t.Fatalf("configure %s policy: %v", mode, err)
	}
}

type closureRow struct {
	id, obligation, state, reason, sender, recipient string
	revision                                         int
	legacy                                           bool
}

func closureRows(t *testing.T, s *Store, eventID string) []closureRow {
	t.Helper()
	rows, err := s.pool.Query(context.Background(), `SELECT reservation_id::text, obligation, state, release_reason, COALESCE(expected_sender,''), recipient, revision, legacy FROM cairn.agent_closure_reservation WHERE request_event_id=$1 ORDER BY created_at, reservation_id`, eventID)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var out []closureRow
	for rows.Next() {
		var r closureRow
		if err = rows.Scan(&r.id, &r.obligation, &r.state, &r.reason, &r.sender, &r.recipient, &r.revision, &r.legacy); err != nil {
			t.Fatal(err)
		}
		out = append(out, r)
	}
	return out
}

func requestTo(r Record, consumer string) PublishEventRequest {
	return PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"agent", consumer}}
}

func replyTo(r Record, request AgentEvent) PublishEventRequest {
	return PublishEventRequest{RequestID: uuid.NewString(), Kind: "response", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"agent", request.From}, CausationID: request.EventID, CorrelationID: request.CorrelationID}
}

func TestClosureDeclaredTaskDeadlineReleasesSettledRequest(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	op := closureOperator(t, r.Scope.Repo)
	// Task deadlines need a managed consumer, so declare the deadline on an
	// ordinary request directly; the release rule reads the event column.
	e, err := a.PublishEvent(ctx, requestTo(r, b.channel.Principal), dest)
	if err != nil {
		t.Fatal(err)
	}
	d := nextFixture(t, b, dest)
	if _, err = b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: d.DeliveryID, LeaseID: d.LeaseID, Disposition: "handled"}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err = op.SweepRequestControls(ctx, RequestControlSweep{Repo: r.Scope.Repo}); err != nil {
		t.Fatal(err)
	}
	if rows := closureRows(t, a, e.EventID); len(rows) != 1 || rows[0].state != "reserved" {
		t.Fatalf("settled request without deadline lost its reply closure: %+v", rows)
	}
	if _, err = a.pool.Exec(ctx, `UPDATE cairn.agent_event SET task_deadline=clock_timestamp()-interval '1 second' WHERE event_id=$1`, e.EventID); err != nil {
		t.Fatal(err)
	}
	if _, err = op.SweepRequestControls(ctx, RequestControlSweep{Repo: r.Scope.Repo}); err != nil {
		t.Fatal(err)
	}
	rows := closureRows(t, a, e.EventID)
	if len(rows) != 1 || rows[0].state != "released" || rows[0].reason != "request_expired" || rows[0].revision != 2 {
		t.Fatalf("declared deadline did not release the settled request: %+v", rows)
	}
	late, err := b.PublishEvent(ctx, replyTo(r, e), dest)
	if err != nil {
		t.Fatal(err)
	}
	var consumed bool
	if err = a.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_closure_reservation WHERE consumed_event_id=$1)`, late.EventID).Scan(&consumed); err != nil || consumed {
		t.Fatalf("late reply consumed a released closure: %v %v", consumed, err)
	}
}

func TestClosureOperatorCancellationReleasesDirectRequest(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	op := closureOperator(t, r.Scope.Repo)
	e, err := a.PublishEvent(ctx, requestTo(r, b.channel.Principal), dest)
	if err != nil {
		t.Fatal(err)
	}
	var delivery string
	if err = a.pool.QueryRow(ctx, `SELECT delivery_id::text FROM cairn.agent_delivery WHERE event_id=$1`, e.EventID).Scan(&delivery); err != nil {
		t.Fatal(err)
	}
	if _, err = op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: r.Scope.Repo, DeliveryID: delivery, Reason: "no longer needed by publisher"}); err != nil {
		t.Fatal(err)
	}
	rows := closureRows(t, a, e.EventID)
	if len(rows) != 1 || rows[0].state != "released" || rows[0].reason != "work_cancelled" {
		t.Fatalf("cancellation did not release: %+v", rows)
	}
}

func TestClosureEnforcementRequiresReconciliationAndBoundsRecipient(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	repo := r.Scope.Repo
	op := closureOperator(t, repo)
	setClosurePolicy(t, repo, "enforce", 100, 1)
	_, err := a.PublishEvent(ctx, requestTo(r, b.channel.Principal), dest)
	requireCode(t, err, "CLOSURE_UNRECONCILED")
	// Notices create no obligation and are not gated by closure capacity.
	if _, err = a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"agent", b.channel.Principal}}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err = op.ReconcileClosureReservations(ctx, ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: repo}); err != nil {
		t.Fatal(err)
	}
	first, err := a.PublishEvent(ctx, requestTo(r, b.channel.Principal), dest)
	if err != nil {
		t.Fatal(err)
	}
	refused := requestTo(r, b.channel.Principal)
	_, err = a.PublishEvent(ctx, refused, dest)
	requireCode(t, err, "CLOSURE_RESERVE_FULL")
	var leaked int
	if err = a.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event WHERE repo=$1 AND kind='request'`, repo).Scan(&leaked); err != nil || leaked != 1 {
		t.Fatalf("refused publication left effects: %d %v", leaked, err)
	}
	// Another principal's closure budget is independent of the saturated one.
	c := testStore(t, Channel{Principal: "third-" + repo, Repo: repo})
	if _, err = c.PublishEvent(ctx, requestTo(r, b.channel.Principal), dest); err != nil {
		t.Fatal(err)
	}
	// The exact reply always lands while its requester is saturated.
	if _, err = b.PublishEvent(ctx, replyTo(r, first), dest); err != nil {
		t.Fatal(err)
	}
	rows := closureRows(t, a, first.EventID)
	if len(rows) != 1 || rows[0].state != "delivered" {
		t.Fatalf("reply not consumed: %+v", rows)
	}
	// Still charged until the requester settles the reply delivery.
	_, err = a.PublishEvent(ctx, refused, dest)
	requireCode(t, err, "CLOSURE_RESERVE_FULL")
	reply := nextFixture(t, a, dest)
	if reply.Event.Kind != "response" {
		if _, err = a.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: reply.DeliveryID, LeaseID: reply.LeaseID, Disposition: "handled"}, dest); err != nil {
			t.Fatal(err)
		}
		reply = nextFixture(t, a, dest)
	}
	if _, err = a.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: reply.DeliveryID, LeaseID: reply.LeaseID, Disposition: "handled"}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err = a.PublishEvent(ctx, refused, dest); err != nil {
		t.Fatalf("same request UUID after capacity returned: %v", err)
	}
}

func TestClosureReconcileBackfillsAdoptsAndCountsLegacy(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	repo := r.Scope.Repo
	op := closureOperator(t, repo)
	answered, err := a.PublishEvent(ctx, requestTo(r, b.channel.Principal), dest)
	if err != nil {
		t.Fatal(err)
	}
	settled, err := a.PublishEvent(ctx, requestTo(r, b.channel.Principal), dest)
	if err != nil {
		t.Fatal(err)
	}
	open, err := a.PublishEvent(ctx, requestTo(r, b.channel.Principal), dest)
	if err != nil {
		t.Fatal(err)
	}
	// Simulate obligations accepted before reservations existed.
	if _, err = a.pool.Exec(ctx, `DELETE FROM cairn.agent_closure_reservation WHERE repo=$1`, repo); err != nil {
		t.Fatal(err)
	}
	if _, err = b.PublishEvent(ctx, replyTo(r, answered), dest); err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{answered.EventID, settled.EventID} {
		d := nextFixture(t, b, dest)
		if d.Event.EventID != want {
			t.Fatalf("delivery order: got %s want %s", d.Event.EventID, want)
		}
		if _, err = b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: d.DeliveryID, LeaseID: d.LeaseID, Disposition: "handled"}, dest); err != nil {
			t.Fatal(err)
		}
	}
	page, err := a.ClosureReservations(ctx, ClosureReservationQuery{}, dest)
	if err != nil || page.Counts.Available || page.Counts.Reserved != nil {
		t.Fatalf("unreconciled counts must be unavailable: %+v %v", page.Counts, err)
	}
	out, err := op.ReconcileClosureReservations(ctx, ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	if out.LegacyReserved != 1 || out.Adopted != 0 || !out.Complete || out.LegacyUnreserved == nil || *out.LegacyUnreserved != 1 || *out.LegacyRows != 1 {
		t.Fatalf("reconciliation: %+v", out)
	}
	if rows := closureRows(t, a, open.EventID); len(rows) != 1 || rows[0].state != "reserved" || !rows[0].legacy {
		t.Fatalf("open legacy obligation: %+v", rows)
	}
	if rows := closureRows(t, a, answered.EventID); len(rows) != 0 {
		t.Fatalf("answered request must not be reserved again: %+v", rows)
	}
	page, err = a.ClosureReservations(ctx, ClosureReservationQuery{}, dest)
	if err != nil || !page.Counts.Available || *page.Counts.Reserved != 1 || *page.Counts.Legacy != 1 || page.Counts.LegacyUnreserved != nil {
		t.Fatalf("session counts after reconciliation: %+v %v", page.Counts, err)
	}
	// An older writer's reply to the now-reserved obligation is adopted.
	if _, err = a.pool.Exec(ctx, `DELETE FROM cairn.agent_closure_reservation WHERE request_event_id=$1`, open.EventID); err != nil {
		t.Fatal(err)
	}
	if _, err = b.PublishEvent(ctx, replyTo(r, open), dest); err != nil {
		t.Fatal(err)
	}
	if _, err = a.pool.Exec(ctx, `INSERT INTO cairn.agent_closure_reservation(reservation_id,repo,obligation,request_event_id,request_delivery_id,expected_sender,recipient,legacy,restore_generation)
 SELECT gen_random_uuid(),e.repo,'request_reply',e.event_id,d.delivery_id,d.consumer,e.publisher,true,1 FROM cairn.agent_event e JOIN cairn.agent_delivery d USING(event_id) WHERE e.event_id=$1`, open.EventID); err != nil {
		t.Fatal(err)
	}
	if out, err = op.ReconcileClosureReservations(ctx, ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: repo}); err != nil || out.Adopted != 1 {
		t.Fatalf("adoption: %+v %v", out, err)
	}
	if rows := closureRows(t, a, open.EventID); len(rows) != 1 || rows[0].state != "delivered" {
		t.Fatalf("adopted reply: %+v", rows)
	}
}

func TestClosureSessionCountsExcludeLocalOnlyObligations(t *testing.T) {
	ctx := context.Background()
	a, b, r, hosted := eventFixture(t)
	repo := r.Scope.Repo
	op := closureOperator(t, repo)
	local := Destination{Name: "local", AllowLocal: true}
	draft := projectNote(repo)
	draft.Sensitivity = "local"
	draft.Body = "local-only request source"
	private, err := a.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = a.PublishEvent(ctx, requestTo(private, b.channel.Principal), local); err != nil {
		t.Fatal(err)
	}
	if _, err = a.PublishEvent(ctx, requestTo(r, b.channel.Principal), hosted); err != nil {
		t.Fatal(err)
	}
	if _, err = op.ReconcileClosureReservations(ctx, ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: repo}); err != nil {
		t.Fatal(err)
	}
	page, err := a.ClosureReservations(ctx, ClosureReservationQuery{}, hosted)
	if err != nil || len(page.Reservations) != 1 || *page.Counts.Reserved != 1 || page.Scope != "inbox" {
		t.Fatalf("hosted view disclosed local obligation: %+v %v", page, err)
	}
	page, err = a.ClosureReservations(ctx, ClosureReservationQuery{}, local)
	if err != nil || len(page.Reservations) != 2 || *page.Counts.Reserved != 2 {
		t.Fatalf("local view: %+v %v", page, err)
	}
	// The consumer is not the recipient and sees none of the requester's obligations.
	page, err = b.ClosureReservations(ctx, ClosureReservationQuery{}, local)
	if err != nil || len(page.Reservations) != 0 || *page.Counts.Reserved != 0 {
		t.Fatalf("non-recipient view: %+v %v", page, err)
	}
	all, err := op.ClosureReservations(ctx, ClosureReservationQuery{Repo: repo}, local)
	if err != nil || all.Scope != "collection" || *all.Counts.Reserved != 2 || all.Counts.LegacyUnreserved == nil {
		t.Fatalf("operator aggregate: %+v %v", all, err)
	}
}

func TestClosureReissueReservesForReissuingOperator(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	repo := r.Scope.Repo
	op := closureOperator(t, repo)
	e, err := a.PublishEvent(ctx, requestTo(r, b.channel.Principal), dest)
	if err != nil {
		t.Fatal(err)
	}
	d := nextFixture(t, b, dest)
	if _, err = b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: d.DeliveryID, LeaseID: d.LeaseID, Disposition: "failed", Code: "processing_failed"}, dest); err != nil {
		t.Fatal(err)
	}
	if rows := closureRows(t, a, e.EventID); len(rows) != 1 || rows[0].state != "reserved" {
		t.Fatalf("consumer failure released the reply obligation: %+v", rows)
	}
	again, err := op.ReissueEvent(ctx, ReissueEventRequest{RequestID: uuid.NewString(), Repo: repo, DeliveryID: d.DeliveryID, Reason: "retry after transient failure", AcceptUncertainEffects: true})
	if err != nil {
		t.Fatal(err)
	}
	rows := closureRows(t, a, again.EventID)
	if len(rows) != 1 || rows[0].state != "reserved" || rows[0].recipient != op.channel.Principal || rows[0].sender != b.channel.Principal {
		t.Fatalf("reissue reservation: %+v", rows)
	}
	if original := closureRows(t, a, e.EventID); len(original) != 1 || original[0].state != "reserved" {
		t.Fatalf("reissue changed the original obligation: %+v", original)
	}
}

func TestClosureScheduledRequestStaysPendingWhenReserveFull(t *testing.T) {
	ctx := context.Background()
	a, b, r, _ := eventFixture(t)
	repo := r.Scope.Repo
	op := closureOperator(t, repo)
	if _, err := op.ReconcileClosureReservations(ctx, ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: repo}); err != nil {
		t.Fatal(err)
	}
	setClosurePolicy(t, repo, "enforce", 100, 1)
	local := Destination{Name: "local", AllowLocal: true}
	first := requestTo(r, b.channel.Principal)
	first.Repo = repo
	if _, err := op.PublishEvent(ctx, first, local); err != nil {
		t.Fatal(err)
	}
	pub := requestTo(r, b.channel.Principal)
	pub.Repo, pub.RequestID = repo, ""
	scheduled, err := op.ScheduleEvent(ctx, ScheduleEventRequest{RequestID: uuid.NewString(), NotBefore: time.Now().Add(-time.Second), GraceSeconds: 3600, Publication: pub})
	if err != nil {
		t.Fatal(err)
	}
	tick, err := op.TickSchedules(ctx, ScheduleTickRequest{Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	if len(tick.Occurrences) != 0 {
		t.Fatalf("refused occurrence finished instead of staying pending: %+v", tick.Occurrences)
	}
	var state, code string
	if err = a.pool.QueryRow(ctx, `SELECT state, code FROM cairn.agent_schedule WHERE occurrence_id=$1`, scheduled.OccurrenceID).Scan(&state, &code); err != nil || state != "pending" || code != "CLOSURE_RESERVE_FULL" {
		t.Fatalf("schedule state: %s %s %v", state, code, err)
	}
}

func TestClosureReconcileBatchesCertifyOnlyWhenExhausted(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	repo := r.Scope.Repo
	op := closureOperator(t, repo)
	for i := 0; i < 2; i++ {
		if _, err := a.PublishEvent(ctx, requestTo(r, b.channel.Principal), dest); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := a.pool.Exec(ctx, `DELETE FROM cairn.agent_closure_reservation WHERE repo=$1`, repo); err != nil {
		t.Fatal(err)
	}
	setClosurePolicy(t, repo, "enforce", 100, 100)
	for i := 0; i < 2; i++ {
		out, err := op.ReconcileClosureReservations(ctx, ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: repo, Limit: 1})
		if err != nil || !out.More || out.Complete || out.LegacyReserved != 1 || out.ReconciledAt != nil {
			t.Fatalf("batch %d: %+v %v", i, out, err)
		}
		// A partial pass certifies nothing: enforcement still refuses.
		_, err = a.PublishEvent(ctx, requestTo(r, b.channel.Principal), dest)
		requireCode(t, err, "CLOSURE_UNRECONCILED")
	}
	out, err := op.ReconcileClosureReservations(ctx, ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: repo, Limit: 1})
	if err != nil || out.More || !out.Complete || *out.LegacyRows != 2 {
		t.Fatalf("final batch: %+v %v", out, err)
	}
	if _, err = a.PublishEvent(ctx, requestTo(r, b.channel.Principal), dest); err != nil {
		t.Fatal(err)
	}
	_, err = op.ReconcileClosureReservations(ctx, ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: repo, Limit: closureReconcileMaxLimit + 1})
	requireCode(t, err, "INVALID_REQUEST")
}

func TestClosureAdmitCarriesTypedRefusalDetails(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	repo := r.Scope.Repo
	op := closureOperator(t, repo)
	if _, err := a.PublishEvent(ctx, requestTo(r, b.channel.Principal), dest); err != nil {
		t.Fatal(err)
	}
	if out, err := op.ReconcileClosureReservations(ctx, ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: repo}); err != nil || !out.Complete {
		t.Fatalf("reconcile: %+v %v", out, err)
	}
	setClosurePolicy(t, repo, "enforce", 2, 1)
	check := func(recipient string, n int64, dimension string, retryable bool) {
		t.Helper()
		tx, err := a.begin(ctx)
		if err != nil {
			t.Fatal(err)
		}
		defer tx.Rollback(ctx)
		err = closureAdmitTx(ctx, tx, repo, recipient, n)
		var refusal *Error
		if !errors.As(err, &refusal) || refusal.Code != "CLOSURE_RESERVE_FULL" || refusal.Admission == nil {
			t.Fatalf("refusal for %s n=%d: %v", recipient, n, err)
		}
		d := refusal.Admission
		if d.Dimension != dimension || d.PolicyRevision != 1 || d.Retryable != retryable || (d.RetryAfterSeconds > 0) != retryable {
			t.Fatalf("details for %s n=%d: %+v", recipient, n, d)
		}
		if strings.Contains(refusal.Message, a.channel.Principal) || strings.Contains(refusal.Message, b.channel.Principal) {
			t.Fatalf("refusal names a principal: %s", refusal.Message)
		}
	}
	check(a.channel.Principal, 1, "closure_recipient", true)
	check("other-"+repo, 2, "closure_collection", true)
	check("other-"+repo, 3, "closure_collection", false)
	tx, err := a.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	if err = closureAdmitTx(ctx, tx, repo, "other-"+repo, 1); err != nil {
		t.Fatalf("capacity remained: %v", err)
	}
}

func TestClosureLegacyUnreservedExcludesGroupedRequests(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	repo := r.Scope.Repo
	op := closureOperator(t, repo)
	grouped := requestTo(r, b.channel.Principal)
	grouped.ResponseGroup = &ResponseGroupSpec{Deadline: time.Now().Add(time.Hour), PartialPolicy: "all"}
	g, err := a.PublishEvent(ctx, grouped, dest)
	if err != nil {
		t.Fatal(err)
	}
	plain, err := a.PublishEvent(ctx, requestTo(r, b.channel.Principal), dest)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{g.EventID, plain.EventID} {
		d := nextFixture(t, b, dest)
		if d.Event.EventID != want {
			t.Fatalf("delivery order: got %s want %s", d.Event.EventID, want)
		}
		if _, err = b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: d.DeliveryID, LeaseID: d.LeaseID, Disposition: "handled"}, dest); err != nil {
			t.Fatal(err)
		}
	}
	if _, err = a.pool.Exec(ctx, `UPDATE cairn.agent_response_group SET deadline=clock_timestamp()-interval '1 second' WHERE event_id=$1`, g.EventID); err != nil {
		t.Fatal(err)
	}
	if _, err = op.SweepRequestControls(ctx, RequestControlSweep{Repo: repo}); err != nil {
		t.Fatal(err)
	}
	if _, err = a.pool.Exec(ctx, `DELETE FROM cairn.agent_closure_reservation WHERE repo=$1`, repo); err != nil {
		t.Fatal(err)
	}
	out, err := op.ReconcileClosureReservations(ctx, ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: repo})
	if err != nil || !out.Complete || *out.LegacyUnreserved != 1 {
		t.Fatalf("closed group overstated legacy_unreserved: %+v %v", out, err)
	}
}
