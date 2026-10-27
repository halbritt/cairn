package core

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
)

// The delivery transition module is exercised through the Store interface.
// Each case names a drift the per-file SQL had allowed and checks the one
// behaviour the helpers now own, so the rule is proven once instead of being
// re-asserted per caller.

func ledgerRows(t *testing.T, op *Store, repo, consumer, deliveryID string) []RecoveryTransition {
	t.Helper()
	page, err := op.ReviewEvents(context.Background(), EventReviewRequest{Repo: repo, Consumer: consumer})
	if err != nil {
		t.Fatal(err)
	}
	for _, d := range page.Deliveries {
		if d.DeliveryID == deliveryID {
			return d.RecoveryTransitions
		}
	}
	t.Fatalf("delivery %s not in review: %+v", deliveryID, page.Deliveries)
	return nil
}

func TestDeliveryTransitionsOwnStopSemantics(t *testing.T) {
	ctx := context.Background()

	t.Run("fail keeps the control attribution it was given", func(t *testing.T) {
		f := newNativeFixture(t)
		a := f.register(nativeAgent{Binding: "stop-attribution"})
		event, delivery := f.publish("notice", a.Inbox)
		op := f.operator()
		reason := "Reviewed: the notice is obsolete"
		got, err := op.CancelWork(ctx, CancelWorkRequest{RequestID: uuid.NewString(), Repo: f.repo, DeliveryID: delivery, Reason: reason})
		if err != nil || got.Control == nil || got.Control.By != op.channel.Principal || got.Control.Reason != reason || got.Control.Code != "operator_cancelled" {
			t.Fatalf("cancel control: %+v %v", got, err)
		}
		d := f.status(event.EventID).Deliveries[0]
		if d.State != "failed" || d.Code != "operator_cancelled" || d.Control == nil || d.Control.By != op.channel.Principal {
			t.Fatalf("publisher view lost the attribution: %+v", d)
		}
		rows := ledgerRows(t, op, f.repo, a.Inbox, delivery)
		if len(rows) != 1 || rows[0].Action != "work_cancel" || rows[0].Outcome != "cancelled" || rows[0].StateBefore != "pending" ||
			rows[0].StateAfter != "failed" || rows[0].RequestID == "" || rows[0].Actor != op.channel.Principal || rows[0].AttemptID != "" {
			t.Fatalf("cancel ledger: %+v", rows)
		}
	})

	t.Run("native claims hold the native lease while publisher polls lease what they ask", func(t *testing.T) {
		f := newNativeFixture(t)
		a := f.register(nativeAgent{Binding: "lease-ttl"})
		_, delivery := f.publish("request", a.Inbox)
		before := dbNow(t, f.receiver)
		claim := f.claim(sessionRef(a), delivery, "turn-ttl", true)
		if claim.Attempt.Delivery.LeaseUntil == nil {
			t.Fatalf("native claim without lease: %+v", claim.Attempt.Delivery)
		}
		if ttl := claim.Attempt.Delivery.LeaseUntil.Sub(before); ttl < nativeLeaseTTL-5*time.Second || ttl > nativeLeaseTTL+5*time.Second {
			t.Fatalf("native lease ttl %v, want about %v", ttl, nativeLeaseTTL)
		}
		_, profileDelivery := f.publish("notice", f.receiver.channel.Principal)
		before = dbNow(t, f.receiver)
		next, err := f.receiver.NextEvent(ctx, NextEventRequest{LeaseSeconds: 600}, f.dest)
		if err != nil || next.Delivery == nil || next.Delivery.DeliveryID != profileDelivery || next.Delivery.LeaseUntil == nil {
			t.Fatalf("profile poll: %+v %v", next, err)
		}
		if ttl := next.Delivery.LeaseUntil.Sub(before); ttl < 595*time.Second || ttl > 605*time.Second {
			t.Fatalf("publisher lease ttl %v, want about 600s", ttl)
		}
	})

	t.Run("a held delivery is never offered to a poll until its attempt closes", func(t *testing.T) {
		f := newNativeFixture(t)
		a := f.register(nativeAgent{Binding: "held-poll"})
		ref := sessionRef(a)
		first, firstDelivery := f.publish("request", a.Inbox)
		claim := f.claim(ref, firstDelivery, "turn-held", true)
		_, secondDelivery := f.publish("notice", a.Inbox)
		view := f.view(ref)
		if next, err := view.NextEvent(ctx, NextEventRequest{}, f.dest); err != nil || next.Delivery != nil {
			t.Fatalf("held inbox offered work: %+v %v", next, err)
		}
		f.expireLease(firstDelivery)
		if next, err := view.NextEvent(ctx, NextEventRequest{}, f.dest); err != nil || next.Delivery != nil {
			t.Fatalf("lapsed lease under an open attempt offered work: %+v %v", next, err)
		}
		if _, err := f.receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: claim.Attempt.ID, Reason: "turn_ended"}, f.dest); err != nil {
			t.Fatal(err)
		}
		next, err := view.NextEvent(ctx, NextEventRequest{}, f.dest)
		if err != nil || next.Delivery == nil || next.Delivery.DeliveryID != secondDelivery {
			t.Fatalf("closed attempt did not release the inbox: %+v %v", next, err)
		}
		if d := f.status(first.EventID).Deliveries[0]; d.State != "failed" || d.Code != "processing_failed" || d.Control != nil {
			t.Fatalf("turn end must fail without a control record: %+v", d)
		}
	})

	t.Run("retiring a stopped inbox and sweeping expired admission are ledgered", func(t *testing.T) {
		f := newNativeFixture(t)
		op := f.operator()
		stopped := f.register(nativeAgent{Binding: "retire", Harness: "claude"})
		_, retireDelivery := f.publish("request", stopped.Inbox)
		f.stopAgo(stopped, "2 days")
		retireRequest := uuid.NewString()
		if _, err := op.ExpireStoppedInboxes(ctx, StoppedInboxExpiryRequest{RequestID: retireRequest, Repo: f.repo, AgentIDs: []string{stopped.AgentID},
			Reason: "Reviewed dry run; the stopped session will not return"}); err != nil {
			t.Fatal(err)
		}
		rows := ledgerRows(t, op, f.repo, stopped.Inbox, retireDelivery)
		if len(rows) != 1 || rows[0].Action != "stopped_inbox_expiry" || rows[0].Outcome != "consumer_retired" || rows[0].RequestID != retireRequest ||
			rows[0].StateBefore != "pending" || rows[0].StateAfter != "failed" {
			t.Fatalf("retire ledger: %+v", rows)
		}

		live := f.register(nativeAgent{Binding: "sweep"})
		admission := time.Now().Add(time.Hour)
		sweepEvent, sweepDelivery := f.publishRequest(f.sender, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request",
			Ref: RecordVersionRef{f.source.RecordID, f.source.Version}, Destination: EventDestination{Type: "agent", Name: live.Inbox}, AdmissionExpiresAt: &admission})
		f.expireAdmission(sweepEvent.EventID)
		if _, err := op.SweepRequestControls(ctx, RequestControlSweep{Repo: f.repo}); err != nil {
			t.Fatal(err)
		}
		rows = ledgerRows(t, op, f.repo, live.Inbox, sweepDelivery)
		if len(rows) != 1 || rows[0].Action != "request_control_sweep" || rows[0].Outcome != "admission_expired" || rows[0].RequestID != "" ||
			rows[0].StateBefore != "pending" || rows[0].StateAfter != "failed" || rows[0].Actor != op.channel.Principal {
			t.Fatalf("sweep ledger: %+v", rows)
		}
		if d := f.status(sweepEvent.EventID).Deliveries[0]; d.State != "failed" || d.Code != "admission_expired" || d.Control == nil || d.Control.By != op.channel.Principal {
			t.Fatalf("sweep control: %+v", d)
		}
	})
}
