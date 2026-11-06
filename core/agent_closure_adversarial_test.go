package core

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
)

type closureProbe struct {
	id, kind, state, recipient, sender, consumed, reason string
	revision                                             int
}

func closureForRequest(t *testing.T, s *Store, eventID string) closureProbe {
	t.Helper()
	page, err := s.ClosureReservations(context.Background(), ClosureReservationQuery{Limit: 100}, Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatalf("reply obligation for %s: %v", eventID, err)
	}
	for _, row := range page.Reservations {
		if row.RequestEventID == eventID {
			return closureProbe{id: row.ReservationID, kind: row.Obligation, state: row.State, recipient: row.Recipient, sender: row.ExpectedSender, consumed: row.ConsumedEventID, reason: row.ReleaseReason, revision: row.Revision}
		}
	}
	t.Fatalf("reply obligation for %s missing from inspection: %+v", eventID, page)
	return closureProbe{}
}

func closureForWait(t *testing.T, s *Store, waitID string) ClosureReservation {
	t.Helper()
	page, err := s.ClosureReservations(context.Background(), ClosureReservationQuery{Obligation: ClosureWaitNotice, Limit: 100}, Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatal(err)
	}
	for _, row := range page.Reservations {
		if row.WaitUUID == waitID {
			return row
		}
	}
	t.Fatalf("wait obligation for %s missing from inspection: %+v", waitID, page)
	return ClosureReservation{}
}

func enforceClosureTestPolicy(t *testing.T, op *Store, repo string, policy EventAdmissionPolicy) {
	t.Helper()
	ctx := context.Background()
	if _, err := op.ReconcileClosureReservations(ctx, ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: repo}); err != nil {
		t.Fatal(err)
	}
	current, err := op.EventAdmissionPolicy(ctx, EventAdmissionPolicyQuery{Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	// These scenarios isolate closure capacity; unrelated rate budgets must not
	// refuse their short publication/retry sequences before the closure boundary.
	rate := EventAdmissionRate{PerSecond: 10000, Burst: 10000}
	policy.Mode = "enforce"
	policy.Limits.ProducerPublications, policy.Limits.ProducerDeliveries = rate, rate
	policy.Limits.OwnerPublications, policy.Limits.OwnerDeliveries = rate, rate
	policy.Limits.CollectionPublications, policy.Limits.CollectionDeliveries = rate, rate
	if _, err = op.SetEventAdmissionPolicy(ctx, SetEventAdmissionPolicyRequest{RequestID: uuid.NewString(), Repo: repo, ExpectedRevision: current.Revision, Policy: policy}); err != nil {
		t.Fatal(err)
	}
}

func TestClosureAdversarialConsumerSettlementRetainsReply(t *testing.T) {
	for _, disposition := range []string{"handled", "ignored", "failed"} {
		t.Run(disposition, func(t *testing.T) {
			ctx := context.Background()
			a, b, source, dest := eventFixture(t)
			event := publishFixture(t, a, b, source, dest)
			before := closureForRequest(t, a, event.EventID)
			delivery := nextFixture(t, b, dest)
			code := ""
			if disposition != "handled" {
				code = "processing_failed"
			}
			_, err := b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery.DeliveryID, LeaseID: delivery.LeaseID, Disposition: disposition, Code: code}, dest)
			if err != nil {
				t.Fatal(err)
			}
			after := closureForRequest(t, a, event.EventID)
			if after != before || after.state != "reserved" || after.recipient != a.channel.Principal || after.sender != b.channel.Principal {
				t.Fatalf("consumer %s retired the independent reply obligation: before=%+v after=%+v", disposition, before, after)
			}
			op := testStore(t, Channel{Principal: "operator:closure-settlement", Operator: true})
			if _, err = op.ReconcileClosureReservations(ctx, ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: source.Scope.Repo}); err != nil {
				t.Fatal(err)
			}
			if after = closureForRequest(t, a, event.EventID); after != before {
				t.Fatalf("legacy reconciliation retired a current unanswered obligation: %+v", after)
			}
			reply, err := b.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "failure", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", a.channel.Principal}, CausationID: event.EventID}, dest)
			if err != nil {
				t.Fatal(err)
			}
			after = closureForRequest(t, a, event.EventID)
			if after.state != "delivered" || after.consumed != reply.EventID || after.revision != before.revision+1 {
				t.Fatalf("post-completion failure reply did not consume its reservation: %+v", after)
			}
		})
	}
}

func TestClosureAdversarialSaturationRetainsChargeUntilReplySettles(t *testing.T) {
	ctx := context.Background()
	a, b, source, dest := eventFixture(t)
	op := testStore(t, Channel{Principal: "operator:closure-capacity", Operator: true})
	if _, err := op.ReconcileClosureReservations(ctx, ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: source.Scope.Repo}); err != nil {
		t.Fatal(err)
	}
	policy := admissionFixturePolicy()
	policy.Limits.MaxClosureReserved = 1
	policy.Limits.MaxRecipientClosureReserved = 1
	enforceClosureTestPolicy(t, op, source.Scope.Repo, policy)
	event := publishFixture(t, a, b, source, dest)
	second := PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", b.channel.Principal}}
	_, err := a.PublishEvent(ctx, second, dest)
	requireCode(t, err, "CLOSURE_RESERVE_FULL")
	delivery := nextFixture(t, b, dest)
	if _, err = b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery.DeliveryID, LeaseID: delivery.LeaseID, Disposition: "handled"}, dest); err != nil {
		t.Fatal(err)
	}
	_, err = a.PublishEvent(ctx, second, dest)
	requireCode(t, err, "CLOSURE_RESERVE_FULL")
	replyReq := PublishEventRequest{RequestID: uuid.NewString(), Kind: "response", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", a.channel.Principal}, CausationID: event.EventID}
	reply, err := b.PublishEvent(ctx, replyReq, dest)
	if err != nil {
		t.Fatalf("valid closure refused at full reserve: %v", err)
	}
	view, err := a.ClosureReservations(ctx, ClosureReservationQuery{}, dest)
	if err != nil || !view.Counts.Available || view.Counts.Reserved == nil || *view.Counts.Reserved != 0 || view.Counts.DeliveredOutstanding == nil || *view.Counts.DeliveredOutstanding != 1 {
		t.Fatalf("reservation-to-delivery transfer lost or doubled its charge: %+v %v", view, err)
	}
	_, err = a.PublishEvent(ctx, second, dest)
	requireCode(t, err, "CLOSURE_RESERVE_FULL")
	// A fresh Store simulates an API restart: retry identity lives in PostgreSQL.
	restarted := testStore(t, b.channel)
	again, err := restarted.PublishEvent(ctx, replyReq, dest)
	if err != nil || again.EventID != reply.EventID {
		t.Fatalf("reply retry after restart: %+v %v", again, err)
	}
	replyDelivery := nextFixture(t, a, dest)
	if replyDelivery.Event.EventID != reply.EventID {
		t.Fatalf("wrong reply delivery: %+v", replyDelivery)
	}
	if _, err = a.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: replyDelivery.DeliveryID, LeaseID: replyDelivery.LeaseID, Disposition: "handled"}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err = a.PublishEvent(ctx, second, dest); err != nil {
		t.Fatalf("same refused UUID could not proceed after closure settled: %v", err)
	}
	var publications int
	if err = a.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event WHERE repo=$1 AND kind='request'`, source.Scope.Repo).Scan(&publications); err != nil || publications != 2 {
		t.Fatalf("refused transactions left partial request publications: count=%d err=%v", publications, err)
	}
}

func TestClosureAdversarialHostedVisibilityAndCachedClose(t *testing.T) {
	ctx := context.Background()
	a, b, source, hosted := eventFixture(t)
	op := testStore(t, Channel{Principal: "operator:closure-private", Operator: true})
	local := Destination{Name: "local", AllowLocal: true}
	draft := projectNote(source.Scope.Repo)
	draft.Sensitivity = "local"
	private, err := a.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}
	request := publishFixture(t, a, b, private, local)
	if _, err = op.ReconcileClosureReservations(ctx, ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: source.Scope.Repo}); err != nil {
		t.Fatal(err)
	}
	hidden, err := a.ClosureReservations(ctx, ClosureReservationQuery{}, hosted)
	if err != nil || len(hidden.Reservations) != 0 || hidden.Counts.Reserved == nil || *hidden.Counts.Reserved != 0 || hidden.Counts.Outstanding == nil || *hidden.Counts.Outstanding != 0 {
		t.Fatalf("hosted inspection disclosed local-only obligation: %+v %v", hidden, err)
	}
	owned, err := a.ClosureReservations(ctx, ClosureReservationQuery{}, local)
	if err != nil || len(owned.Reservations) != 1 {
		t.Fatalf("local publisher cannot inspect its obligation: %+v %v", owned, err)
	}
	foreign, err := b.ClosureReservations(ctx, ClosureReservationQuery{}, local)
	if err != nil || len(foreign.Reservations) != 0 || foreign.Counts.Outstanding == nil || *foreign.Counts.Outstanding != 0 {
		t.Fatalf("request consumer learned publisher-only capacity: %+v %v", foreign, err)
	}
	item := owned.Reservations[0]
	closeReq := CloseReplyObligationsRequest{RequestID: uuid.NewString(), Reason: "Publisher deliberately no longer requires a reply", Items: []CloseReplyObligationItem{{ReservationID: item.ReservationID, ExpectedRevision: item.Revision}}}
	_, err = b.CloseReplyObligations(ctx, closeReq, local)
	requireCode(t, err, "NOT_FOUND")
	_, err = a.CloseReplyObligations(ctx, closeReq, hosted)
	requireCode(t, err, "NOT_FOUND")
	closed, err := a.CloseReplyObligations(ctx, closeReq, local)
	if err != nil || len(closed.Reservations) != 1 || closed.Reservations[0].ReleaseReason != "publisher_closed" {
		t.Fatalf("authorized publisher close: %+v %v", closed, err)
	}
	_, err = a.CloseReplyObligations(ctx, closeReq, hosted)
	requireCode(t, err, "NOT_FOUND")
	if got := closureForRequest(t, a, request.EventID); got.state != "released" || got.revision != item.Revision+1 {
		t.Fatalf("failed hosted retry changed close: %+v", got)
	}
	// A shareable request may receive a local reply. Hosted inspection sees its
	// own request, but must not expose the private reply or delivery identifiers.
	sharedRequest := publishFixture(t, a, b, source, hosted)
	_, err = b.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "response", Ref: RecordVersionRef{private.RecordID, private.Version}, Destination: EventDestination{"agent", a.channel.Principal}, CausationID: sharedRequest.EventID}, local)
	if err != nil {
		t.Fatal(err)
	}
	view, err := a.ClosureReservations(ctx, ClosureReservationQuery{}, hosted)
	if err != nil || len(view.Reservations) != 1 || view.Reservations[0].ConsumedEventID != "" || view.Reservations[0].ConsumedDeliveryID != "" || !view.Reservations[0].ConsumedRedacted {
		t.Fatalf("private consumed reply was exposed: %+v %v", view, err)
	}
}

func TestClosureAdversarialRestoreRequiresFreshReconciliation(t *testing.T) {
	ctx := context.Background()
	a, b, source, dest := eventFixture(t)
	op := testStore(t, Channel{Principal: "operator:closure-restore", Operator: true})
	direct := publishFixture(t, a, b, source, dest)
	group, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", b.channel.Principal}, ResponseGroup: &ResponseGroupSpec{Deadline: time.Now().Add(time.Hour), PartialPolicy: "all"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	reconcileReq := ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: source.Scope.Repo}
	old, err := op.ReconcileClosureReservations(ctx, reconcileReq)
	if err != nil {
		t.Fatal(err)
	}
	policy := admissionFixturePolicy()
	policy.Limits.MaxClosureReserved, policy.Limits.MaxRecipientClosureReserved = 10, 10
	policy.Limits.MaxCollectionOutstanding = 100
	policy.Limits.MaxRecipientOutstanding, policy.Limits.MaxProducerRecipientOutstanding = 100, 100
	enforceClosureTestPolicy(t, op, source.Scope.Repo, policy)
	fence, err := op.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "Fence uncertain post-backup replies in disposable closure fixture"})
	if err != nil {
		t.Fatal(err)
	}
	if got := closureForRequest(t, a, direct.EventID); got.state != "reserved" {
		t.Fatalf("restore lost direct request obligation: %+v", got)
	}
	if got := closureForRequest(t, a, group.EventID); got.state != "released" || got.reason != "restore_fenced" {
		t.Fatalf("fenced group retained reply promise: %+v", got)
	}
	view, err := a.ClosureReservations(ctx, ClosureReservationQuery{}, dest)
	if err != nil || view.Counts.Available || view.Counts.Outstanding != nil || view.Counts.RestoreGeneration != fence.Generation {
		t.Fatalf("pre-restore reconciliation certified current capacity: %+v %v", view, err)
	}
	if cached, err := op.ReconcileClosureReservations(ctx, reconcileReq); err != nil || cached.RestoreGeneration != old.RestoreGeneration {
		t.Fatalf("old reconcile retry changed meaning: %+v %v", cached, err)
	}
	view, err = a.ClosureReservations(ctx, ClosureReservationQuery{}, dest)
	if err != nil || view.Counts.Available {
		t.Fatalf("cached old reconciliation certified new restore: %+v %v", view, err)
	}
	fresh := PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", b.channel.Principal}}
	_, err = a.PublishEvent(ctx, fresh, dest)
	requireCode(t, err, "CLOSURE_UNRECONCILED")
	reconcileReq.RequestID = uuid.NewString()
	current, err := op.ReconcileClosureReservations(ctx, reconcileReq)
	if err != nil || current.RestoreGeneration != fence.Generation {
		t.Fatalf("fresh reconciliation: %+v %v", current, err)
	}
	view, err = a.ClosureReservations(ctx, ClosureReservationQuery{}, dest)
	if err != nil || !view.Counts.Available || view.Counts.Outstanding == nil || *view.Counts.Outstanding != 1 {
		t.Fatalf("fresh restore accounting did not preserve direct closure: %+v %v", view, err)
	}
	if _, err = a.PublishEvent(ctx, fresh, dest); err != nil {
		t.Fatalf("same refused intent could not resume after fresh reconciliation: %v", err)
	}
}

func TestClosureAdversarialExactReplyIdentityAndRetry(t *testing.T) {
	for _, correlation := range []string{"", uuid.NewString()} {
		t.Run("correlation="+correlation, func(t *testing.T) {
			ctx := context.Background()
			a, b, source, dest := eventFixture(t)
			parent, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", b.channel.Principal}}, dest)
			if err != nil {
				t.Fatal(err)
			}
			event, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", b.channel.Principal}, CorrelationID: correlation}, dest)
			if err != nil {
				t.Fatal(err)
			}
			before := closureForRequest(t, a, event.EventID)
			valid := PublishEventRequest{RequestID: uuid.NewString(), Kind: "response", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", a.channel.Principal}, CausationID: event.EventID, CorrelationID: correlation}
			cases := []struct {
				name string
				edit func(*PublishEventRequest)
			}{
				{"wrong_correlation", func(r *PublishEventRequest) { r.CorrelationID = uuid.NewString() }},
				{"wrong_destination", func(r *PublishEventRequest) { r.Destination.Name = b.channel.Principal }},
				{"wrong_causation", func(r *PublishEventRequest) { r.CausationID = parent.EventID }},
				{"wrong_kind", func(r *PublishEventRequest) { r.Kind = "notice" }},
			}
			for _, tc := range cases {
				t.Run(tc.name, func(t *testing.T) {
					req := valid
					req.RequestID = uuid.NewString()
					tc.edit(&req)
					if _, err := b.PublishEvent(ctx, req, dest); err != nil {
						t.Fatal(err)
					}
					if after := closureForRequest(t, a, event.EventID); after != before {
						t.Fatalf("unmatched reply consumed reservation: %+v", after)
					}
				})
			}
			spoof := valid
			spoof.RequestID = uuid.NewString()
			if _, err := a.PublishEvent(ctx, spoof, dest); err != nil {
				t.Fatal(err)
			}
			if after := closureForRequest(t, a, event.EventID); after != before {
				t.Fatalf("publisher impersonation consumed consumer reservation: %+v", after)
			}
			reply, err := b.PublishEvent(ctx, valid, dest)
			if err != nil {
				t.Fatal(err)
			}
			consumed := closureForRequest(t, a, event.EventID)
			if consumed.state != "delivered" || consumed.consumed != reply.EventID || consumed.revision != before.revision+1 {
				t.Fatalf("valid reply was not charged once: %+v", consumed)
			}
			again, err := b.PublishEvent(ctx, valid, dest)
			if err != nil || again.EventID != reply.EventID || closureForRequest(t, a, event.EventID) != consumed {
				t.Fatalf("committed retry changed reservation: %+v %v", again, err)
			}
			valid.RequestID = uuid.NewString()
			if _, err := b.PublishEvent(ctx, valid, dest); err != nil {
				t.Fatal(err)
			}
			if after := closureForRequest(t, a, event.EventID); after != consumed {
				t.Fatalf("duplicate reply reused consumed capacity: %+v", after)
			}
		})
	}
}

func TestClosureAdversarialFirstLateGroupReplyDoesNotConsume(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	a, b, source, dest := f.sender, f.receiver, f.source, f.dest
	event, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", b.channel.Principal}, ResponseGroup: &ResponseGroupSpec{Deadline: time.Now().Add(time.Hour), PartialPolicy: "all"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	if before := closureForRequest(t, a, event.EventID); before.kind != "group_reply" || before.state != "reserved" {
		t.Fatalf("missing grouped obligation: %+v", before)
	}
	f.closeResponseGroup(event.EventID)
	_, err = b.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "response", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", a.channel.Principal}, CausationID: event.EventID, CorrelationID: event.CorrelationID}, dest)
	if err != nil {
		t.Fatal(err)
	}
	after := closureForRequest(t, a, event.EventID)
	if after.state != "released" || after.reason != "group_closed" || after.consumed != "" {
		t.Fatalf("first reply after deadline received closure capacity: %+v", after)
	}
	group, err := a.ResponseGroup(ctx, ResponseGroupQuery{EventID: event.EventID}, dest)
	if err != nil || group.Responded != 0 || len(group.Observations) != 1 || group.Observations[0].Disposition != "late" {
		t.Fatalf("late response changed group outcome: %+v %v", group, err)
	}
}

func TestClosureAdversarialFinalGroupReplyConsumesBeforeClosure(t *testing.T) {
	ctx := context.Background()
	a, b, source, dest := eventFixture(t)
	event, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", b.channel.Principal}, ResponseGroup: &ResponseGroupSpec{Deadline: time.Now().Add(time.Hour), PartialPolicy: "all"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	req := PublishEventRequest{RequestID: uuid.NewString(), Kind: "response", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", a.channel.Principal}, CausationID: event.EventID, CorrelationID: event.CorrelationID}
	reply, err := b.PublishEvent(ctx, req, dest)
	if err != nil {
		t.Fatalf("last group reply must consume capacity before group terminal release: %v", err)
	}
	consumed := closureForRequest(t, a, event.EventID)
	if consumed.state != "delivered" || consumed.consumed != reply.EventID || consumed.reason != "" {
		t.Fatalf("final reply reservation was released instead of delivered: %+v", consumed)
	}
	group, err := a.ResponseGroup(ctx, ResponseGroupQuery{EventID: event.EventID}, dest)
	if err != nil || group.State != "collected" || group.Responded != 1 {
		t.Fatalf("final reserved reply did not collect group: %+v %v", group, err)
	}
	again, err := b.PublishEvent(ctx, req, dest)
	if err != nil || again.EventID != reply.EventID || closureForRequest(t, a, event.EventID) != consumed {
		t.Fatalf("final group reply retry changed reservation: %+v %v", again, err)
	}
}

func TestClosureAdversarialUnassignedPoolCancellationReleases(t *testing.T) {
	ctx := context.Background()
	a, _, source, dest := eventFixture(t)
	op := testStore(t, Channel{Principal: "operator:closure-pool", Operator: true})
	_, err := op.ConfigureWorkerPool(ctx, WorkerPoolConfigRequest{RequestID: uuid.NewString(), Repo: source.Scope.Repo, Name: "closure-pool", Enabled: true, MaxPending: 3, MaxPendingPerPublisher: 3})
	if err != nil {
		t.Fatal(err)
	}
	event, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"pool", "closure-pool"}, Pool: &PoolRequirements{Workspace: "/fixture/closure"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	before := closureForRequest(t, a, event.EventID)
	if before.kind != "pool_reply" || before.sender != "" || before.state != "reserved" {
		t.Fatalf("unassigned pool obligation: %+v", before)
	}
	req := CancelWorkRequest{RequestID: uuid.NewString(), Repo: source.Scope.Repo, PoolEventID: event.EventID, Reason: "Withdraw accepted pool work before assignment"}
	if _, err = op.CancelWork(ctx, req); err != nil {
		t.Fatal(err)
	}
	after := closureForRequest(t, a, event.EventID)
	if after.state != "released" || after.reason != "work_cancelled" || after.revision != before.revision+1 {
		t.Fatalf("pool cancellation left reservation behind: %+v", after)
	}
	if _, err = op.CancelWork(ctx, req); err != nil || closureForRequest(t, a, event.EventID) != after {
		t.Fatalf("pool cancellation retry changed release: %v", err)
	}
}

func TestClosureAdversarialCloseBatchIsAtomicAndOpenGroupProtected(t *testing.T) {
	ctx := context.Background()
	a, b, source, dest := eventFixture(t)
	first := publishFixture(t, a, b, source, dest)
	second := publishFixture(t, a, b, source, dest)
	one, two := closureForRequest(t, a, first.EventID), closureForRequest(t, a, second.EventID)
	req := CloseReplyObligationsRequest{RequestID: uuid.NewString(), Reason: "Retire two unanswered requests by explicit publisher decision", Items: []CloseReplyObligationItem{{ReservationID: one.id, ExpectedRevision: one.revision}, {ReservationID: two.id, ExpectedRevision: two.revision + 1}}}
	_, err := a.CloseReplyObligations(ctx, req, dest)
	requireCode(t, err, "VERSION_CONFLICT")
	if closureForRequest(t, a, first.EventID) != one || closureForRequest(t, a, second.EventID) != two {
		t.Fatal("partially closed batch with stale expected revision")
	}
	req.Items[1].ExpectedRevision = two.revision
	closed, err := a.CloseReplyObligations(ctx, req, dest)
	if err != nil || len(closed.Reservations) != 2 {
		t.Fatalf("retry after rollback: %+v %v", closed, err)
	}
	for _, eventID := range []string{first.EventID, second.EventID} {
		got := closureForRequest(t, a, eventID)
		if got.state != "released" || got.reason != "publisher_closed" {
			t.Fatalf("explicit batch close missing: %+v", got)
		}
	}
	group, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", b.channel.Principal}, ResponseGroup: &ResponseGroupSpec{Deadline: time.Now().Add(time.Hour), PartialPolicy: "all"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	grouped := closureForRequest(t, a, group.EventID)
	_, err = a.CloseReplyObligations(ctx, CloseReplyObligationsRequest{RequestID: uuid.NewString(), Reason: "Attempt to remove reply capacity from an open collection", Items: []CloseReplyObligationItem{{ReservationID: grouped.id, ExpectedRevision: grouped.revision}}}, dest)
	requireCode(t, err, "OPEN_CONFLICT")
	if closureForRequest(t, a, group.EventID) != grouped {
		t.Fatal("explicit close orphaned an open response group")
	}
}

func TestClosureAdversarialWaitCancellationAndTerminalNotice(t *testing.T) {
	ctx := context.Background()
	openStore := func(ch Channel) *Store { return testStore(t, ch) }
	_, _, owner, worker, sessionA, sessionB, dest := waitFixture(t, openStore)
	op := testStore(t, Channel{Principal: "operator:closure-wait", Operator: true})
	if _, err := op.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatal(err)
	}
	first := publishTestRequest(t, ctx, sessionA, owner.Repo, worker.AgentID, "", dest)
	waitReq := CreateWaitRequest{RequestID: uuid.NewString(), Collection: owner.Repo, PredicateKind: WaitPredicateRequestResponse, DependencyEventUUID: first.EventID, Deadline: time.Now().Add(time.Hour)}
	wait, err := sessionA.CreateWait(ctx, waitReq, dest)
	if err != nil {
		t.Fatal(err)
	}
	requestClosure := closureForRequest(t, sessionA, first.EventID)
	closeReq := CloseReplyObligationsRequest{RequestID: uuid.NewString(), Reason: "Attempt to retire a reply still promised to a durable wait", Items: []CloseReplyObligationItem{{ReservationID: requestClosure.id, ExpectedRevision: requestClosure.revision}}}
	_, err = sessionA.CloseReplyObligations(ctx, closeReq, dest)
	requireCode(t, err, "OPEN_CONFLICT")
	if closureForRequest(t, sessionA, first.EventID) != requestClosure {
		t.Fatal("explicit close orphaned a waiting response predicate")
	}
	if _, err = sessionA.CancelWait(ctx, CancelWaitRequest{RequestID: uuid.NewString(), Collection: owner.Repo, WaitUUID: wait.WaitUUID}, dest); err != nil {
		t.Fatal(err)
	}
	if got := closureForWait(t, sessionA, wait.WaitUUID); got.State != "released" || got.ReleaseReason != "wait_cancelled" {
		t.Fatalf("cancelled wait retained capacity: %+v", got)
	}
	closeReq.RequestID = uuid.NewString()
	if _, err = sessionA.CloseReplyObligations(ctx, closeReq, dest); err != nil {
		t.Fatalf("cancelled wait still prevents deliberate reply retirement: %v", err)
	}
	second := publishTestRequest(t, ctx, sessionA, owner.Repo, worker.AgentID, uuid.NewString(), dest)
	waitReq.RequestID, waitReq.DependencyEventUUID = uuid.NewString(), second.EventID
	terminal, err := sessionA.CreateWait(ctx, waitReq, dest)
	if err != nil {
		t.Fatal(err)
	}
	publishTestResponse(t, ctx, sessionB, owner.Repo, owner.AgentID, second.EventID, second.CorrelationID, "response", dest)
	if drained, err := op.DrainWaitOutbox(ctx, owner.Repo, 10); err != nil || drained != 1 {
		t.Fatalf("terminal wait notice drain: count=%d err=%v", drained, err)
	}
	if drained, err := op.DrainWaitOutbox(ctx, owner.Repo, 10); err != nil || drained != 0 {
		t.Fatalf("terminal wait notice replayed: count=%d err=%v", drained, err)
	}
	if got := closureForWait(t, sessionA, terminal.WaitUUID); got.State != "delivered" || got.ConsumedEventID != terminal.ReservedNoticeEventUUID {
		t.Fatalf("wait notice did not transfer reserved capacity: %+v", got)
	}
	if _, err = op.ReconcileClosureReservations(ctx, ClosureReconcileRequest{RequestID: uuid.NewString(), Repo: owner.Repo}); err != nil {
		t.Fatal(err)
	}
	view, err := sessionA.ClosureReservations(ctx, ClosureReservationQuery{}, dest)
	if err != nil || view.Counts.DeliveredOutstanding == nil || *view.Counts.DeliveredOutstanding != 2 {
		t.Fatalf("reply and terminal notice should each own one outstanding charge: %+v %v", view, err)
	}
	for range 2 {
		delivery := nextFixture(t, sessionA, dest)
		if _, err = sessionA.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery.DeliveryID, LeaseID: delivery.LeaseID, Disposition: "handled"}, dest); err != nil {
			t.Fatal(err)
		}
	}
	view, err = sessionA.ClosureReservations(ctx, ClosureReservationQuery{}, dest)
	if err != nil || view.Counts.Outstanding == nil || *view.Counts.Outstanding != 0 {
		t.Fatalf("settled reply and notice retained capacity: %+v %v", view, err)
	}
}
