package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func TestNoticeFlushReleasesOriginalDeliveryWithPublisherAuthority(t *testing.T) {
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	event, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice",
		Ref:          RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination:  EventDestination{Type: "agent", Name: receiver.channel.Principal},
		NoticePolicy: &NoticeWakePolicyRequest{Version: 1, Urgency: "routine", DeferSeconds: 60}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	before, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
	if err != nil || len(before.Deliveries) != 1 {
		t.Fatalf("before flush: %+v %v", before, err)
	}
	if next, err := receiver.NextEvent(ctx, NextEventRequest{}, dest); err != nil || next.Delivery != nil {
		t.Fatalf("notice ready before flush: %+v %v", next, err)
	}
	req := NoticeFlushRequest{RequestID: uuid.NewString(), EventID: event.EventID}
	_, err = receiver.FlushNotice(ctx, req, dest)
	requireCode(t, err, "AUTHORITY_DENIED")
	flushed, err := sender.FlushNotice(ctx, req, dest)
	if err != nil || flushed.Released != 1 || flushed.AlreadyReady != 0 || flushed.Leased != 0 || flushed.Terminal != 0 || flushed.FlushedBy != sender.channel.Principal {
		t.Fatalf("flush result: %+v %v", flushed, err)
	}
	after, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
	if err != nil || len(after.Deliveries) != 1 || after.Event.NoticeFlush == nil {
		t.Fatalf("flush status: %+v %v", after, err)
	}
	if after.Event.Ref != event.Ref || after.Event.NoticePolicy == nil || *after.Event.NoticeFlush != flushed ||
		after.Deliveries[0].DeliveryID != before.Deliveries[0].DeliveryID ||
		after.Deliveries[0].AvailableAt == nil || !after.Deliveries[0].AvailableAt.Equal(flushed.FlushedAt) {
		t.Fatalf("flush changed original event or delivery: before=%+v after=%+v", before, after)
	}
	if !flushed.FlushedAt.Before(*before.Deliveries[0].AvailableAt) {
		t.Fatal("flush did not advance the notice deadline")
	}
	next, err := receiver.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil || next.Delivery == nil || next.Delivery.DeliveryID != before.Deliveries[0].DeliveryID {
		t.Fatalf("original delivery not claimable after flush: %+v %v", next, err)
	}
	for _, retry := range []NoticeFlushRequest{req, {RequestID: uuid.NewString(), EventID: event.EventID}} {
		again, err := sender.FlushNotice(ctx, retry, dest)
		if err != nil || again != flushed {
			t.Fatalf("retry changed flush decision: %+v %v", again, err)
		}
	}
	other, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice",
		Ref:          RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination:  EventDestination{Type: "agent", Name: receiver.channel.Principal},
		NoticePolicy: &NoticeWakePolicyRequest{Version: 1, Urgency: "routine", DeferSeconds: 60}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	_, err = sender.FlushNotice(ctx, NoticeFlushRequest{RequestID: req.RequestID, EventID: other.EventID}, dest)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
}

func TestNoticeFlushClassifiesReadyLeasedAndTerminalFanout(t *testing.T) {
	ctx := context.Background()
	sender, first, source, dest := eventFixture(t)
	consumers := []*Store{first}
	for i := 0; i < 3; i++ {
		consumers = append(consumers, testStore(t, Channel{Principal: "flush-" + uuid.NewString(), Repo: source.Scope.Repo}))
	}
	for _, consumer := range consumers {
		if _, err := consumer.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: "flush-test", Active: true}); err != nil {
			t.Fatal(err)
		}
	}
	event, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice",
		Ref:          RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination:  EventDestination{Type: "topic", Name: "flush-test"},
		NoticePolicy: &NoticeWakePolicyRequest{Version: 1, Urgency: "routine", DeferSeconds: 60}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	for _, consumer := range consumers[:3] {
		if _, err := sender.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET available_at=clock_timestamp()-interval '1 second' WHERE event_id=$1 AND consumer=$2`, event.EventID, consumer.channel.Principal); err != nil {
			t.Fatal(err)
		}
	}
	for i := 0; i < 2; i++ {
		claimed, err := consumers[i].NextEvent(ctx, NextEventRequest{}, dest)
		if err != nil || claimed.Delivery == nil {
			t.Fatalf("claim %d: %+v %v", i, claimed, err)
		}
		if i == 0 {
			if _, err := consumers[i].CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: claimed.Delivery.DeliveryID, LeaseID: claimed.Delivery.LeaseID, Disposition: "handled"}, dest); err != nil {
				t.Fatal(err)
			}
		}
	}
	flushed, err := sender.FlushNotice(ctx, NoticeFlushRequest{RequestID: uuid.NewString(), EventID: event.EventID}, dest)
	if err != nil || flushed.Released != 1 || flushed.AlreadyReady != 1 || flushed.Leased != 1 || flushed.Terminal != 1 {
		t.Fatalf("wrong fanout disposition: %+v %v", flushed, err)
	}
	status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
	if err != nil || len(status.Deliveries) != 4 {
		t.Fatalf("fanout status: %+v %v", status, err)
	}
	for _, delivery := range status.Deliveries {
		if delivery.Consumer == consumers[3].channel.Principal && (delivery.State != "pending" || delivery.AvailableAt == nil || !delivery.AvailableAt.Equal(flushed.FlushedAt)) {
			t.Fatalf("future delivery was not released in place: %+v", delivery)
		}
	}
	if next, err := consumers[3].NextEvent(ctx, NextEventRequest{}, dest); err != nil || next.Delivery == nil || next.Delivery.Event.EventID != event.EventID {
		t.Fatalf("future subscriber cannot claim after flush: %+v %v", next, err)
	}
}

func TestNoticeFlushRejectsImmediatePolicy(t *testing.T) {
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	event, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice",
		Ref:         RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination: EventDestination{Type: "agent", Name: receiver.channel.Principal}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	_, err = sender.FlushNotice(ctx, NoticeFlushRequest{RequestID: uuid.NewString(), EventID: event.EventID}, dest)
	requireCode(t, err, "INVALID_REQUEST")
}
