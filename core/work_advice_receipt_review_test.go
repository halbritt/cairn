package core

import (
	"context"
	"encoding/json"
	"reflect"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestWorkAdviceReviewFreshApplicationMatchesRetainedRetry(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	f := newAdviceReviewCase(t, ctx, restoreTestStore(t), "Exact receipt fixture")
	intent := WorkAdviceApplicationReceipt{RequestUUID: uuid.New(), ProposalUUID: f.proposal.ProposalUUID}
	apply := func() *WorkAdviceApplicationReceipt {
		t.Helper()
		request := intent
		r, err := f.store.ApplyAdviceProposal(ctx, &request, f.proposal.ReviewVersion, []uuid.UUID{f.actions[0].ActionUUID})
		if err != nil {
			t.Fatal(err)
		}
		return r
	}
	first := apply()
	if first.AppliedAt.IsZero() {
		t.Fatal("fresh Apply omitted its database-owned application timestamp")
	}
	retry := apply()
	read, err := f.store.GetAdviceReceipt(ctx, intent.RequestUUID)
	if err != nil {
		t.Fatal(err)
	}
	jsonValue := func(value any) any {
		t.Helper()
		raw, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		var result any
		if err := json.Unmarshal(raw, &result); err != nil {
			t.Fatal(err)
		}
		return result
	}
	if !reflect.DeepEqual(jsonValue(first), jsonValue(retry)) || !reflect.DeepEqual(jsonValue(first), jsonValue(read)) {
		t.Fatal("fresh Apply, identical retry and retained read disagree on the complete receipt")
	}
}

func TestWorkAdviceReviewForgetPreservesIndependentEditRetry(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	f := newAdviceReviewCase(t, ctx, restoreTestStore(t), "Private source text "+uuid.NewString())
	_, old := applyCapturedReview(t, ctx, f, capturedReviewGeneration{f.job, f.proposal, f.actions[0]}, "progress", f.source.Body)
	request := EditProgressAnnotationRequest{RequestID: uuid.NewString(), Collection: f.collection,
		ProjectSlug: f.project.LocalSlug, ItemUUID: f.item, AnnotationID: old.AnnotationID,
		ExpectedRevision: old.Revision, Body: "Independent selected replacement " + uuid.NewString()}
	first, err := f.store.EditProgressAnnotation(ctx, request)
	if err != nil {
		t.Fatal(err)
	}
	forgetCapturedReviewSource(t, ctx, f)
	retry, err := f.store.EditProgressAnnotation(ctx, request)
	if err != nil {
		t.Fatal(err)
	}
	if first.AnnotationID != retry.AnnotationID || first.Revision != retry.Revision || first.Body != retry.Body || first.Body != request.Body {
		t.Fatal("Forget altered the retained retry of an independent manual replacement")
	}
}

func TestWorkAdviceReviewBlockerCurrentnessReadsClosedActionResults(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	f, source, blocker, _ := manualCitedBlockerReview(t, ctx, "Synthetic blocker currentness")
	raw, err := json.Marshal(map[string]any{"actions": []any{map[string]any{
		"action_type": "annotate_blocker", "blocker_action": "resolve",
		"existing_blocker_id": blocker.AnnotationID.String(), "expected_revision": blocker.Revision,
	}}})
	if err != nil {
		t.Fatal(err)
	}
	provider := &capturedReviewAdviceProvider{workflowParsedAdviceProvider: &workflowParsedAdviceProvider{result: string(raw)}}
	generated := generateCapturedReview(t, ctx, f, source, provider)
	if err := f.store.VerifyAdviceCurrentness(ctx, generated.proposal.ProposalUUID); err != nil {
		t.Fatalf("unchanged blocker resolution is not reviewable: %v", err)
	}
	if _, err := f.store.ResolveBlockerAnnotation(ctx, ResolveBlockerAnnotationRequest{RequestID: uuid.NewString(),
		Collection: f.collection, ProjectSlug: f.project.LocalSlug, ItemUUID: f.item,
		AnnotationID: blocker.AnnotationID, ExpectedRevision: blocker.Revision}); err != nil {
		t.Fatal(err)
	}
	if err := f.store.VerifyAdviceCurrentness(ctx, generated.proposal.ProposalUUID); Code(err) != "STALE_OBSERVATION" {
		t.Fatalf("actual intervening resolution did not make the proposal stale: %v", err)
	}
}
