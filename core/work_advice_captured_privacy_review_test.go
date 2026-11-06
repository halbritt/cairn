package core

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/advice"
)

// Observe the real provider input; the blocker variant copies only the blocker
// actually supplied by Store. Output still passes the production schema parser.
type capturedReviewAdviceProvider struct {
	*workflowParsedAdviceProvider
	snapshot    []byte
	copyBlocker uuid.UUID
}

func (p *capturedReviewAdviceProvider) GenerateAdvice(ctx context.Context, snapshot *advice.Snapshot, deadline time.Duration, claim func(advice.ProcessIdentity) error) (*advice.Proposal, *advice.ProcessResult, error) {
	var err error
	p.snapshot, err = json.Marshal(snapshot)
	if err != nil {
		return nil, nil, err
	}
	if p.copyBlocker != uuid.Nil {
		var text string
		for _, blocker := range snapshot.ExistingBlockers {
			if blocker.BlockerID == p.copyBlocker.String() {
				text = blocker.Text
			}
		}
		if text == "" {
			return nil, nil, fmt.Errorf("Store did not supply the selected actual blocker")
		}
		raw, err := json.Marshal(map[string]any{"actions": []any{map[string]any{
			"action_type": advice.ActionAnnotateProgress, "text": text, "rationale": "Current blocker says " + text,
		}}})
		if err != nil {
			return nil, nil, err
		}
		p.result = string(raw)
	}
	return p.workflowParsedAdviceProvider.GenerateAdvice(ctx, snapshot, deadline, claim)
}

type capturedReviewGeneration struct {
	job      *WorkAdviceJob
	proposal *WorkAdviceProposal
	action   *WorkAdviceAction
}

func generateCapturedReview(t *testing.T, ctx context.Context, f *adviceReviewCase, source Record, provider *capturedReviewAdviceProvider) capturedReviewGeneration {
	t.Helper()
	job := &WorkAdviceJob{BindingID: f.project.BindingID, PlaneItemUUID: f.item, ProfileName: advice.RouteCodexSubscription,
		ModelRoute: "fixture-model", TemplateVersion: 1, RequestUUID: uuid.New()}
	proposal, actions, err := f.store.GenerateWorkAdvice(ctx, job, AdviceInputSpec{BindingID: f.project.BindingID, PlaneItemUUID: f.item,
		SelectedSources: []SourceRefSpec{{RecordID: uuid.MustParse(source.RecordID), Version: source.Version}}}, provider)
	if err != nil || proposal == nil || len(actions) != 1 || provider.calls != 1 {
		t.Fatalf("actual parsed generation failed: proposal=%+v actions=%+v calls=%d error=%v", proposal, actions, provider.calls, err)
	}
	return capturedReviewGeneration{job, proposal, actions[0]}
}

func applyCapturedReview(t *testing.T, ctx context.Context, f *adviceReviewCase, generation capturedReviewGeneration, kind, body string) (*WorkAdviceApplicationReceipt, WorkAnnotation) {
	t.Helper()
	receipt, err := f.store.ApplyAdviceProposal(ctx, &WorkAdviceApplicationReceipt{RequestUUID: uuid.New(), ProposalUUID: generation.proposal.ProposalUUID},
		generation.proposal.ReviewVersion, []uuid.UUID{generation.action.ActionUUID})
	if err != nil || receipt == nil || receipt.ReceiptUUID == uuid.Nil {
		t.Fatalf("actual application failed: %+v %v", receipt, err)
	}
	detail, err := f.store.GetWorkItemDetail(ctx, f.collection, f.project.LocalSlug, f.item.String())
	if err != nil {
		t.Fatal(err)
	}
	for _, row := range detail.Annotations {
		if row.Kind == kind && row.Body == body && (row.State == "active" || row.State == "open") {
			return receipt, row
		}
	}
	t.Fatal("applied exact annotation positive control missing")
	return nil, WorkAnnotation{}
}

func capturedReviewRow(t *testing.T, ctx context.Context, s *Store, query string, args ...any) string {
	t.Helper()
	var raw string
	if err := s.pool.QueryRow(ctx, query, args...).Scan(&raw); err != nil {
		t.Fatalf("exact retained row missing: %v", err)
	}
	return raw
}

func assertCapturedReviewSources(t *testing.T, ctx context.Context, f *adviceReviewCase, generation capturedReviewGeneration, source Record, provider *capturedReviewAdviceProvider) WorkAdviceInputManifest {
	t.Helper()
	job, err := f.store.GetAdviceJob(ctx, generation.job.JobUUID)
	if err != nil || job == nil {
		t.Fatalf("retained capture unavailable: %v", err)
	}
	var manifest WorkAdviceInputManifest
	if err := json.Unmarshal(job.InputManifest, &manifest); err != nil {
		t.Fatal(err)
	}
	if len(manifest.Sources) != 1 || manifest.Sources[0].RecordID.String() != source.RecordID || manifest.Sources[0].Version != source.Version {
		t.Fatal("capture silently expanded or changed the explicitly selected source set")
	}
	var snapshot advice.Snapshot
	if err := json.Unmarshal(provider.snapshot, &snapshot); err != nil {
		t.Fatal(err)
	}
	if len(snapshot.SourceReferences) != 1 || snapshot.SourceReferences[0].ID != source.RecordID || snapshot.SourceReferences[0].Version != source.Version {
		t.Fatal("provider input silently expanded the explicitly selected source set")
	}
	var payload map[string]json.RawMessage
	if err := json.Unmarshal(generation.action.TargetPayload, &payload); err != nil {
		t.Fatal(err)
	}
	if refs, ok := payload["source_references"]; ok && string(refs) != "[]" && string(refs) != "null" {
		t.Fatal("fixture output unexpectedly supplied optional source citations")
	}
	return manifest
}

func forgetCapturedReviewSource(t *testing.T, ctx context.Context, f *adviceReviewCase) {
	t.Helper()
	grant, err := f.store.Bootstrap(ctx, BootstrapRequest{RequestID: uuid.NewString(), Reason: "Disposable captured advice privacy regression"})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := f.store.PreviewDeletion(ctx, f.source.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.store.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: f.source.RecordID, ExpectedVersion: f.source.Version,
		GrantID: grant.ID, PreviewID: preview.PreviewID}); err != nil {
		t.Fatal(err)
	}
}

func assertCapturedReviewScrub(t *testing.T, ctx context.Context, f *adviceReviewCase, b capturedReviewGeneration, receipt *WorkAdviceApplicationReceipt, annotation WorkAnnotation, sentinel string) {
	t.Helper()
	for _, row := range []struct {
		name, query string
		args        []any
	}{
		{"B job", `SELECT to_jsonb(j)::text FROM cairn.work_advice_job j WHERE job_uuid=$1 AND request_uuid=$2`, []any{b.job.JobUUID, b.job.RequestUUID}},
		{"B proposal", `SELECT to_jsonb(p)::text FROM cairn.work_advice_proposal p WHERE proposal_uuid=$1 AND job_uuid=$2`, []any{b.proposal.ProposalUUID, b.job.JobUUID}},
		{"B action", `SELECT to_jsonb(a)::text FROM cairn.work_advice_action a WHERE action_uuid=$1 AND proposal_uuid=$2`, []any{b.action.ActionUUID, b.proposal.ProposalUUID}},
		{"B receipt", `SELECT to_jsonb(r)::text FROM cairn.work_advice_application_receipt r WHERE receipt_uuid=$1 AND request_uuid=$2 AND proposal_uuid=$3`, []any{receipt.ReceiptUUID, receipt.RequestUUID, b.proposal.ProposalUUID}},
		{"B applied annotation", `SELECT to_jsonb(a)::text FROM cairn.work_annotation a WHERE annotation_id=$1 AND binding_id=$2 AND plane_item_uuid=$3`, []any{annotation.AnnotationID, f.project.BindingID, f.item}},
		{"B applied history", `SELECT to_jsonb(h)::text FROM cairn.work_annotation_history h WHERE annotation_id=$1 AND revision=$2`, []any{annotation.AnnotationID, annotation.Revision}},
	} {
		if strings.Contains(capturedReviewRow(t, ctx, f.store, row.query, row.args...), sentinel) {
			t.Errorf("%s retains forgotten A text through a captured dependency", row.name)
		}
	}
	job, jobErr := f.store.GetAdviceJob(ctx, b.job.JobUUID)
	proposal, actions, proposalErr := f.store.GetAdviceProposal(ctx, b.proposal.ProposalUUID)
	detail, detailErr := f.store.GetWorkItemDetail(ctx, f.collection, f.project.LocalSlug, f.item.String())
	if jobErr != nil || proposalErr != nil || detailErr != nil {
		t.Fatalf("retained structural reads failed: job=%v proposal=%v detail=%v", jobErr, proposalErr, detailErr)
	}
	encoded, err := json.Marshal([]any{job, proposal, actions, detail})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), sentinel) {
		t.Error("Store review or work detail exposes forgotten captured text")
	}
}

func newCapturedReviewControl(t *testing.T, ctx context.Context, f *adviceReviewCase) (Record, func()) {
	t.Helper()
	source, err := f.store.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: Draft{Kind: "note", Body: "Independent selected B source " + uuid.NewString(),
		ClaimType: "self", Sensitivity: "shareable", Scope: Scope{Repo: f.collection, TaskID: "*", RunID: "*"}}})
	if err != nil {
		t.Fatal(err)
	}
	manual, err := f.store.AddBlockerAnnotation(ctx, AddBlockerAnnotationRequest{RequestID: uuid.NewString(), Collection: f.collection,
		ProjectSlug: f.project.LocalSlug, ItemUUID: f.item, Body: "Independent operator blocker " + uuid.NewString()})
	if err != nil {
		t.Fatal(err)
	}
	const sourceQuery = `SELECT to_jsonb(v)::text FROM cairn.record_version v WHERE record_id=$1 AND version=$2`
	const manualQuery = `SELECT jsonb_build_array(to_jsonb(a),to_jsonb(h))::text FROM cairn.work_annotation a
		JOIN cairn.work_annotation_history h USING(annotation_id) WHERE a.annotation_id=$1 AND h.revision=$2`
	sourceBefore := capturedReviewRow(t, ctx, f.store, sourceQuery, source.RecordID, source.Version)
	manualBefore := capturedReviewRow(t, ctx, f.store, manualQuery, manual.AnnotationID, manual.Revision)
	return source, func() {
		if capturedReviewRow(t, ctx, f.store, sourceQuery, source.RecordID, source.Version) != sourceBefore ||
			capturedReviewRow(t, ctx, f.store, manualQuery, manual.AnnotationID, manual.Revision) != manualBefore {
			t.Error("Forget changed the independent selected source or manual blocker/history")
		}
	}
}

func TestWorkAdviceReviewForgetCapturedProgressBeforePreservesIndependentOutput(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	sentinel := "CAPTURED_PROGRESS_A_" + uuid.NewString()
	f := newAdviceReviewCase(t, ctx, restoreTestStore(t), sentinel)
	_, prior := applyCapturedReview(t, ctx, f, capturedReviewGeneration{f.job, f.proposal, f.actions[0]}, "progress", sentinel)
	sourceB, checkControl := newCapturedReviewControl(t, ctx, f)
	independent := "Independent B progress " + uuid.NewString()
	raw, err := json.Marshal(map[string]any{"actions": []any{map[string]any{"action_type": advice.ActionAnnotateProgress, "text": independent}}})
	if err != nil {
		t.Fatal(err)
	}
	provider := &capturedReviewAdviceProvider{workflowParsedAdviceProvider: &workflowParsedAdviceProvider{result: string(raw)}}
	b := generateCapturedReview(t, ctx, f, sourceB, provider)
	manifest := assertCapturedReviewSources(t, ctx, f, b, sourceB, provider)
	if strings.Contains(string(provider.snapshot), sentinel) {
		t.Fatal("local progress Before unexpectedly entered provider input; this case must isolate review-only capture")
	}
	if manifest.PreconditionProgressID == nil || *manifest.PreconditionProgressID != prior.AnnotationID || manifest.PreconditionProgressRevision != prior.Revision {
		t.Fatal("B did not capture the exact prior progress identity/revision needed to review Before")
	}
	// A body may be stored once in exact history rather than duplicated in B.
	const priorHistory = `SELECT to_jsonb(h)::text FROM cairn.work_annotation_history h WHERE annotation_id=$1 AND revision=$2`
	if !strings.Contains(capturedReviewRow(t, ctx, f.store, priorHistory, prior.AnnotationID, prior.Revision), sentinel) {
		t.Fatal("captured Before's exact historical body was unavailable before Forget")
	}
	const inputQuery = `SELECT input_snapshot::text FROM cairn.work_advice_job WHERE job_uuid=$1`
	independentInput := capturedReviewRow(t, ctx, f.store, inputQuery, b.job.JobUUID)
	receipt, applied := applyCapturedReview(t, ctx, f, b, "progress", independent)
	forgetCapturedReviewSource(t, ctx, f)
	assertCapturedReviewScrub(t, ctx, f, b, receipt, applied, sentinel)
	if strings.Contains(capturedReviewRow(t, ctx, f.store, priorHistory, prior.AnnotationID, prior.Revision), sentinel) {
		t.Error("captured Before can still resolve forgotten A text through exact history")
	}
	assertCapturedReviewSources(t, ctx, f, b, sourceB, provider)
	retained, actions, err := f.store.GetAdviceProposal(ctx, b.proposal.ProposalUUID)
	if err != nil || retained == nil || len(actions) != 1 || !strings.Contains(string(actions[0].TargetPayload), independent) || !strings.Contains(string(retained.NormalizedPayload), independent) {
		t.Fatalf("Forget over-purged B's independent parsed output: %v", err)
	}
	if capturedReviewRow(t, ctx, f.store, inputQuery, b.job.JobUUID) != independentInput {
		t.Error("Forget changed B's independent provider input due only to local Before metadata")
	}
	if body := capturedReviewRow(t, ctx, f.store, `SELECT body FROM cairn.work_annotation WHERE annotation_id=$1 AND revision=$2 AND state='active'`, applied.AnnotationID, applied.Revision); body != independent {
		t.Error("Forget erased independently applied B progress due only to local Before metadata")
	}
	checkControl()
}

func TestWorkAdviceReviewForgetCapturedBlockerScrubsTransitiveOutput(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	sentinel := "CAPTURED_BLOCKER_A_" + uuid.NewString()
	f := newAdviceReviewCase(t, ctx, restoreTestStore(t), sentinel)
	raw, err := json.Marshal(map[string]any{"actions": []any{map[string]any{"action_type": advice.ActionAnnotateBlocker, "blocker_action": "create", "text": sentinel}}})
	if err != nil {
		t.Fatal(err)
	}
	a := generateCapturedReview(t, ctx, f, f.source, &capturedReviewAdviceProvider{workflowParsedAdviceProvider: &workflowParsedAdviceProvider{result: string(raw)}})
	_, blocker := applyCapturedReview(t, ctx, f, a, "blocker", sentinel)
	sourceB, checkControl := newCapturedReviewControl(t, ctx, f)
	if err := f.store.SetWorkAdvicePolicy(ctx, f.project.BindingID, []string{"title", "sources", "blockers"}); err != nil {
		t.Fatal(err)
	}
	provider := &capturedReviewAdviceProvider{workflowParsedAdviceProvider: &workflowParsedAdviceProvider{}, copyBlocker: blocker.AnnotationID}
	b := generateCapturedReview(t, ctx, f, sourceB, provider)
	assertCapturedReviewSources(t, ctx, f, b, sourceB, provider)
	if !strings.Contains(string(provider.snapshot), sentinel) || !strings.Contains(string(b.action.TargetPayload), sentinel) {
		t.Fatal("B did not actually receive and copy A-derived blocker text without output citations")
	}
	if !strings.Contains(capturedReviewRow(t, ctx, f.store, `SELECT to_jsonb(j)::text FROM cairn.work_advice_job j WHERE job_uuid=$1`, b.job.JobUUID), sentinel) {
		t.Fatal("B's actual blocker-bearing input was not retained before Forget")
	}
	receipt, applied := applyCapturedReview(t, ctx, f, b, "progress", sentinel)
	forgetCapturedReviewSource(t, ctx, f)
	assertCapturedReviewScrub(t, ctx, f, b, receipt, applied, sentinel)
	assertCapturedReviewSources(t, ctx, f, b, sourceB, provider)
	checkControl()
}
