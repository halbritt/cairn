package core

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/google/uuid"
)

// A readable handoff is not a capability to inspect its linked events, and
// handled requests do not settle outstanding response or task obligations.
func TestHandoffLinksDoNotConferEventAccessOrTaskClosure(t *testing.T) {
	ctx := context.Background()
	publisher, worker, source, dest := eventFixture(t)
	later := testStore(t, Channel{Principal: "later-" + source.Scope.Repo, Repo: source.Scope.Repo})
	events := make([]AgentEvent, 2)
	for i := range events {
		var err error
		events[i], err = publisher.PublishEvent(ctx, PublishEventRequest{
			RequestID: uuid.NewString(), Kind: "request",
			Ref:           RecordVersionRef{source.RecordID, source.Version},
			Destination:   EventDestination{"agent", worker.channel.Principal},
			ResponseGroup: &ResponseGroupSpec{Deadline: time.Now().Add(time.Hour), PartialPolicy: "all"},
		}, dest)
		if err != nil {
			t.Fatal(err)
		}
	}
	draft := projectNote(source.Scope.Repo)
	draft.Sensitivity = "shareable"
	draft.Body = fmt.Sprintf("Handoff: fixture\nOpen: review %s; deliver %s", events[0].EventID, events[1].EventID)
	handoff, err := publisher.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	before, err := later.History(ctx, RecordHistoryRequest{RecordID: handoff.RecordID, Version: 1}, dest)
	if err != nil || len(before.Versions) != 1 || before.Versions[0].Body == nil || *before.Versions[0].Body != draft.Body {
		t.Fatalf("shared handoff not readable: %+v %v", before, err)
	}
	for _, event := range events {
		delivery := nextFixture(t, worker, dest)
		if delivery.Event.EventID != event.EventID {
			t.Fatal("unexpected request order")
		}
		_, err = worker.CompleteEvent(ctx, CompleteEventRequest{
			RequestID: uuid.NewString(), DeliveryID: delivery.DeliveryID,
			LeaseID: delivery.LeaseID, Disposition: "handled",
		}, dest)
		if err != nil {
			t.Fatal(err)
		}
		status, err := publisher.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
		if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].State != "handled" || status.Deliveries[0].Result != nil {
			t.Fatalf("handled acknowledgment: %+v %v", status, err)
		}
		group, err := publisher.ResponseGroup(ctx, ResponseGroupQuery{EventID: event.EventID}, dest)
		if err != nil || group.State != "open" || group.Responded != 0 || group.TaskOutcome != "unknown" {
			t.Fatalf("handling closed outstanding response/task: %+v %v", group, err)
		}
		_, err = later.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
		requireCode(t, err, "NOT_FOUND")
		_, err = later.ResponseGroup(ctx, ResponseGroupQuery{EventID: event.EventID}, dest)
		requireCode(t, err, "NOT_FOUND")
	}
	after, err := later.History(ctx, RecordHistoryRequest{RecordID: handoff.RecordID, Version: 1}, dest)
	if err != nil || after.CurrentVersion != 1 || len(after.Versions) != 1 || after.Versions[0].Body == nil ||
		*after.Versions[0].Body != draft.Body || after.Versions[0].BodySHA256 != before.Versions[0].BodySHA256 {
		t.Fatalf("event handling rewrote handoff history: %+v %v", after, err)
	}
}
