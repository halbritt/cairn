package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func TestLastHandledDeliveriesReportsOnlyHandledCompletionPerConsumer(t *testing.T) {
	ctx := context.Background()
	a, b, r, dest := eventFixture(t)
	op, _ := testOperator(t)
	repo := r.Scope.Repo
	consumer := b.channel.Principal

	got, err := op.LastHandledDeliveries(ctx, repo, []string{consumer, "agent/never"})
	if err != nil || len(got) != 0 {
		t.Fatalf("no handled delivery yet: %+v %v", got, err)
	}
	publishFixture(t, a, b, r, dest)
	first := nextFixture(t, b, dest)
	done, err := b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: first.DeliveryID, LeaseID: first.LeaseID, Disposition: "handled"}, dest)
	if err != nil || done.CompletedAt == nil {
		t.Fatalf("handle: %+v %v", done, err)
	}
	publishFixture(t, a, b, r, dest)
	second := nextFixture(t, b, dest)
	if _, err = b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: second.DeliveryID, LeaseID: second.LeaseID, Disposition: "failed", Code: "source_unavailable"}, dest); err != nil {
		t.Fatal(err)
	}
	got, err = op.LastHandledDeliveries(ctx, repo, []string{consumer, "agent/never"})
	if err != nil || len(got) != 1 || !got[consumer].Equal(*done.CompletedAt) {
		t.Fatalf("a later failure is not handling; want %v, got %+v %v", done.CompletedAt, got, err)
	}
	other, err := op.LastHandledDeliveries(ctx, uuid.NewString(), []string{consumer})
	if err != nil || len(other) != 0 {
		t.Fatalf("another repository must not see this inbox: %+v %v", other, err)
	}
	_, err = b.LastHandledDeliveries(ctx, repo, []string{consumer})
	requireCode(t, err, "AUTHORITY_DENIED")
	many := make([]string, 257)
	for i := range many {
		many[i] = "agent/" + uuid.NewString()
	}
	_, err = op.LastHandledDeliveries(ctx, repo, many)
	requireCode(t, err, "INVALID_REQUEST")
}
