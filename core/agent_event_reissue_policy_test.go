package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func TestReissueRacingOperatorsCreateOnlyOneSuccessor(t *testing.T) {
	ctx := context.Background()
	a, b, r, d := eventFixture(t)
	op, _ := testOperator(t)
	publishFixture(t, a, b, r, d)
	delivery := nextFixture(t, b, d)
	if _, err := b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery.DeliveryID, LeaseID: delivery.LeaseID, Disposition: "failed", Code: "processing_failed"}, d); err != nil {
		t.Fatal(err)
	}
	errors := make(chan error, 2)
	for range 2 {
		go func() {
			_, err := op.ReissueEvent(ctx, ReissueEventRequest{RequestID: uuid.NewString(), Repo: r.Scope.Repo, DeliveryID: delivery.DeliveryID, Reason: "Concurrent operator recovery fixture", AcceptUncertainEffects: true})
			errors <- err
		}()
	}
	first, second := <-errors, <-errors
	if first == nil {
		requireCode(t, second, "VERSION_CONFLICT")
	} else {
		requireCode(t, first, "VERSION_CONFLICT")
		if second != nil {
			t.Fatal(second)
		}
	}
	page, err := b.InboxEvents(ctx, InboxWatchRequest{}, d)
	if err != nil || len(page.Deliveries) != 2 || page.Deliveries[0].State != "failed" || page.Deliveries[1].State != "pending" {
		t.Fatalf("duplicate successor or rewritten original: %+v %v", page, err)
	}
}

func TestReissueCannotExposeLocalCausalHistory(t *testing.T) {
	ctx := context.Background()
	a, b, r, _ := eventFixture(t)
	op, _ := testOperator(t)
	draft := projectNote(r.Scope.Repo)
	private, err := a.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}
	local := Destination{Name: "local", AllowLocal: true}
	publishFixture(t, a, b, private, local)
	delivery := nextFixture(t, b, local)
	if _, err = b.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery.DeliveryID, LeaseID: delivery.LeaseID, Disposition: "failed", Code: "processing_failed"}, local); err != nil {
		t.Fatal(err)
	}
	// A later classification change must not expose the older local event link.
	if _, err = a.pool.Exec(ctx, `UPDATE cairn.memory_record SET sensitivity='shareable' WHERE record_id=$1`, private.RecordID); err != nil {
		t.Fatal(err)
	}
	_, err = op.ReissueEvent(ctx, ReissueEventRequest{RequestID: uuid.NewString(), Repo: r.Scope.Repo, DeliveryID: delivery.DeliveryID, Reason: "Inspect causal privacy during explicit recovery", AcceptUncertainEffects: true})
	requireCode(t, err, "DESTINATION_PROHIBITED")
	page, err := op.ReviewEvents(ctx, EventReviewRequest{Repo: r.Scope.Repo})
	if err != nil || len(page.Deliveries) != 1 || page.Deliveries[0].ReissuedAs != nil {
		t.Fatalf("refusal retained partial successor: %+v %v", page, err)
	}
}
