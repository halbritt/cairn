package core

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/advice"
)

func manualCitedBlockerReview(t *testing.T, ctx context.Context, sentinel string) (*adviceReviewCase, Record, WorkAnnotation, func()) {
	t.Helper()
	f := newAdviceReviewCase(t, ctx, restoreTestStore(t), sentinel)
	sourceB, checkControl := newCapturedReviewControl(t, ctx, f)
	blocker, err := f.store.AddBlockerAnnotation(ctx, AddBlockerAnnotationRequest{RequestID: uuid.NewString(),
		Collection: f.collection, ProjectSlug: f.project.LocalSlug, ItemUUID: f.item, Body: sentinel,
		SourceReferences: []string{f.source.RecordID}})
	if err != nil || blocker.Body != sentinel || len(blocker.SourceReferences) != 1 || blocker.SourceReferences[0] != f.source.RecordID {
		t.Fatalf("actual manual source-citing blocker was not created: %+v %v", blocker, err)
	}
	if err := f.store.SetWorkAdvicePolicy(ctx, f.project.BindingID, []string{"title", "sources", "blockers"}); err != nil {
		t.Fatal(err)
	}
	return f, sourceB, blocker, checkControl
}

func TestWorkAdviceReviewForgetManualBlockerSourceScrubsDerivedOutput(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	sentinel := "MANUAL_BLOCKER_SOURCE_A_" + uuid.NewString()
	f, sourceB, blocker, checkControl := manualCitedBlockerReview(t, ctx, sentinel)
	provider := &capturedReviewAdviceProvider{workflowParsedAdviceProvider: &workflowParsedAdviceProvider{}, copyBlocker: blocker.AnnotationID}
	b := generateCapturedReview(t, ctx, f, sourceB, provider)
	assertCapturedReviewSources(t, ctx, f, b, sourceB, provider)
	if !strings.Contains(string(provider.snapshot), sentinel) || !strings.Contains(string(b.action.TargetPayload), sentinel) {
		t.Fatal("B did not actually receive and copy the manual blocker's source-derived text")
	}
	receipt, applied := applyCapturedReview(t, ctx, f, b, "progress", sentinel)
	forgetCapturedReviewSource(t, ctx, f)
	assertCapturedReviewScrub(t, ctx, f, b, receipt, applied, sentinel)
	assertCapturedReviewSources(t, ctx, f, b, sourceB, provider)
	for _, query := range []string{
		`SELECT body FROM cairn.work_annotation WHERE annotation_id=$1 AND revision=$2`,
		`SELECT body FROM cairn.work_annotation_history WHERE annotation_id=$1 AND revision=$2`,
	} {
		if strings.Contains(capturedReviewRow(t, ctx, f.store, query, blocker.AnnotationID, blocker.Revision), sentinel) {
			t.Error("Forget retained the manual blocker's known source-derived body")
		}
	}
	checkControl()
}

func TestWorkAdviceReviewWithdrawnManualBlockerSourceRefusesBeforeProvider(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	sentinel := "WITHDRAWN_BLOCKER_SOURCE_A_" + uuid.NewString()
	f, sourceB, blocker, checkControl := manualCitedBlockerReview(t, ctx, sentinel)
	provider := &capturedReviewAdviceProvider{workflowParsedAdviceProvider: &workflowParsedAdviceProvider{}, copyBlocker: blocker.AnnotationID}
	positive := generateCapturedReview(t, ctx, f, sourceB, provider)
	assertCapturedReviewSources(t, ctx, f, positive, sourceB, provider)
	if !strings.Contains(string(provider.snapshot), sentinel) {
		t.Fatal("eligible positive control never supplied the actual blocker")
	}
	grant, err := f.store.Bootstrap(ctx, BootstrapRequest{RequestID: uuid.NewString(), Reason: "Withdraw an actual referenced source before hosted advice"})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := f.store.PreviewRetraction(ctx, f.source.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	withdrawn, err := f.store.Retract(ctx, RetractRequest{RequestID: uuid.NewString(), RecordID: f.source.RecordID,
		ExpectedVersion: f.source.Version, GrantID: grant.ID, PreviewID: preview.PreviewID, Reason: "Withdraw synthetic blocker source before another generation"})
	if err != nil || withdrawn.RecordID != f.source.RecordID || withdrawn.Lifecycle != "retracted" || withdrawn.Version <= f.source.Version {
		t.Fatalf("public retraction did not make the exact source currently ineligible: %+v %v", withdrawn, err)
	}
	// Withdrawal does not erase the existing annotation. The generation path must
	// inspect its known dependency before sending those retained bytes to a model.
	if body := capturedReviewRow(t, ctx, f.store, `SELECT body FROM cairn.work_annotation WHERE annotation_id=$1 AND revision=$2 AND state='open'`, blocker.AnnotationID, blocker.Revision); body != sentinel {
		t.Fatal("withdrawal control lost the retained blocker text before admission")
	}
	job := &WorkAdviceJob{BindingID: f.project.BindingID, PlaneItemUUID: f.item, ProfileName: advice.RouteCodexSubscription,
		ModelRoute: "fixture-model", TemplateVersion: 1, RequestUUID: uuid.New()}
	spec := AdviceInputSpec{BindingID: f.project.BindingID, PlaneItemUUID: f.item,
		SelectedSources: []SourceRefSpec{{RecordID: uuid.MustParse(sourceB.RecordID), Version: sourceB.Version}}}
	proposal, actions, err := f.store.GenerateWorkAdvice(ctx, job, spec, provider)
	if Code(err) != "SOURCE_INELIGIBLE" {
		t.Errorf("known withdrawn blocker source did not refuse generation with SOURCE_INELIGIBLE: %v", err)
	}
	if provider.calls != 1 {
		t.Errorf("withdrawn source reached provider; calls=%d, want only the earlier eligible positive control", provider.calls)
	}
	if proposal != nil || len(actions) != 0 {
		t.Error("refused generation returned a proposal or actions")
	}
	if len(spec.SelectedSources) != 1 || spec.SelectedSources[0].RecordID.String() != sourceB.RecordID || spec.SelectedSources[0].Version != sourceB.Version {
		t.Error("admission changed the explicitly selected unrelated source intent")
	}
	var retainedProposals int
	if err := f.store.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.work_advice_proposal WHERE job_uuid=$1`, job.JobUUID).Scan(&retainedProposals); err != nil {
		t.Fatal(err)
	}
	if retainedProposals != 0 {
		t.Error("withdrawn-source admission persisted a generated proposal")
	}
	checkControl()
}
