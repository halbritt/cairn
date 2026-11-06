package core

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/advice"
	"github.com/halbritt/cairn/internal/plane"
)

type adviceReviewCase struct {
	store      *Store
	collection string
	project    WorkProject
	item       uuid.UUID
	source     Record
	prior      WorkAnnotation
	job        *WorkAdviceJob
	spec       AdviceInputSpec
	provider   *workflowParsedAdviceProvider
	proposal   *WorkAdviceProposal
	actions    []*WorkAdviceAction
}

// All domain rows come from Store entrypoints. Only model-result bytes are a
// deterministic boundary, parsed by the same parser as a real provider result.
func newAdviceReviewCase(t *testing.T, ctx context.Context, s *Store, text string) *adviceReviewCase {
	t.Helper()
	f := &adviceReviewCase{store: s, collection: "fixture:advice-review:" + uuid.NewString(), item: uuid.New()}
	for _, name := range []string{"work-view/1", "work-advice/1"} {
		if err := s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: name, Status: "active"}); err != nil {
			t.Fatal(err)
		}
	}
	p, err := s.AddWorkProject(ctx, AddWorkProjectRequest{RequestID: uuid.NewString(), Collection: f.collection,
		LocalSlug: "review", PlaneInstance: "work", PlaneOrigin: "https://plane.example.com", WorkspaceUUID: uuid.New(), ProjectUUID: uuid.New(), ConfigRevision: "review-v1"})
	if err != nil {
		t.Fatal(err)
	}
	f.project = p
	refresh, err := s.PrepareRefresh(ctx, f.collection, p.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.CommitRefreshSnapshot(ctx, refresh, []plane.NormalizedItem{{PlaneItemUUID: f.item, Identifier: "REVIEW-1", Title: "Synthetic review item"}}, false, nil); err != nil {
		t.Fatal(err)
	}
	f.prior, err = s.AddProgressAnnotation(ctx, AddProgressAnnotationRequest{RequestID: uuid.NewString(), Collection: f.collection, ProjectSlug: p.LocalSlug, ItemUUID: f.item, Body: "Manual selected progress before generation"})
	if err != nil {
		t.Fatal(err)
	}
	f.source, err = s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: Draft{Kind: "note", Body: text,
		ClaimType: "self", Sensitivity: "shareable", Scope: Scope{Repo: f.collection, TaskID: "*", RunID: "*"}}})
	if err != nil {
		t.Fatal(err)
	}
	if err := s.SetWorkAdvicePolicy(ctx, p.BindingID, []string{"title", "sources"}); err != nil {
		t.Fatal(err)
	}
	// Omitting optional source_references is valid model output. The selected
	// input still establishes a known dependency for copied output on Forget.
	raw, err := json.Marshal(map[string]any{"actions": []any{map[string]any{
		"action_type": advice.ActionAnnotateProgress, "text": text, "rationale": "Selected input says " + text,
	}}})
	if err != nil {
		t.Fatal(err)
	}
	f.provider = &workflowParsedAdviceProvider{result: string(raw)}
	f.job = &WorkAdviceJob{BindingID: p.BindingID, PlaneItemUUID: f.item, ProfileName: advice.RouteCodexSubscription,
		ModelRoute: "fixture-model", TemplateVersion: 1, RequestUUID: uuid.New()}
	f.spec = AdviceInputSpec{BindingID: p.BindingID, PlaneItemUUID: f.item,
		SelectedSources: []SourceRefSpec{{RecordID: uuid.MustParse(f.source.RecordID), Version: f.source.Version}}}
	f.proposal, f.actions, err = s.GenerateWorkAdvice(ctx, f.job, f.spec, f.provider)
	if err != nil || f.proposal == nil || len(f.actions) != 1 {
		t.Fatalf("actual parsed generation failed: proposal=%+v actions=%+v error=%v", f.proposal, f.actions, err)
	}
	t.Cleanup(func() {
		if f.provider.calls != 1 {
			t.Errorf("provider invoked %d times; want exactly one", f.provider.calls)
		}
	})
	return f
}

func TestWorkAdviceReviewSelectedSourceIntentIsImmutable(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	f := newAdviceReviewCase(t, ctx, restoreTestStore(t), "Selected source A")
	b, err := f.store.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: Draft{Kind: "note", Body: "Eligible source B",
		ClaimType: "self", Sensitivity: "shareable", Scope: Scope{Repo: f.collection, TaskID: "*", RunID: "*"}}})
	if err != nil {
		t.Fatal(err)
	}
	replayed, _, err := f.store.GenerateWorkAdvice(ctx, f.job, f.spec, f.provider)
	if err != nil || replayed.ProposalUUID != f.proposal.ProposalUUID {
		t.Fatalf("identical request positive control: %+v %v", replayed, err)
	}
	changed := f.spec
	changed.SelectedSources = []SourceRefSpec{{RecordID: uuid.MustParse(b.RecordID), Version: b.Version}}
	_, _, err = f.store.GenerateWorkAdvice(ctx, f.job, changed, f.provider)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
}

func TestWorkAdviceReviewManualProgressWinsBeforeApply(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	f := newAdviceReviewCase(t, ctx, restoreTestStore(t), "Proposed progress")
	manual, err := f.store.EditProgressAnnotation(ctx, EditProgressAnnotationRequest{RequestID: uuid.NewString(), Collection: f.collection,
		ProjectSlug: f.project.LocalSlug, ItemUUID: f.item, AnnotationID: f.prior.AnnotationID, ExpectedRevision: f.prior.Revision, Body: "Later operator progress"})
	if err != nil {
		t.Fatal(err)
	}
	receipt := &WorkAdviceApplicationReceipt{RequestUUID: uuid.New(), ProposalUUID: f.proposal.ProposalUUID}
	_, err = f.store.ApplyAdviceProposal(ctx, receipt, f.proposal.ReviewVersion, []uuid.UUID{f.actions[0].ActionUUID})
	requireCode(t, err, "STALE_OBSERVATION")
	detail, err := f.store.GetWorkItemDetail(ctx, f.collection, f.project.LocalSlug, f.item.String())
	if err != nil || len(detail.Annotations) != 1 || detail.Annotations[0].AnnotationID != manual.AnnotationID ||
		detail.Annotations[0].Revision != manual.Revision || detail.Annotations[0].Body != manual.Body || detail.Annotations[0].State != "active" {
		t.Fatalf("manual progress changed after refused apply: %+v %v", detail, err)
	}
	var count int
	if err := f.store.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.work_advice_application_receipt WHERE request_uuid=$1`, receipt.RequestUUID).Scan(&count); err != nil || count != 0 {
		t.Fatalf("refused apply retained an application: count=%d error=%v", count, err)
	}
}

func TestWorkAdviceReviewForgetScrubsKnownDerivedTextWithoutOutputSourceID(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	s := restoreTestStore(t)
	sentinel := "ADVICE_FORGET_SENTINEL_" + uuid.NewString()
	f := newAdviceReviewCase(t, ctx, s, sentinel)
	control := newAdviceReviewCase(t, ctx, s, "Independent control proposal")
	intent := &WorkAdviceApplicationReceipt{RequestUUID: uuid.New(), ProposalUUID: f.proposal.ProposalUUID}
	applied, err := s.ApplyAdviceProposal(ctx, intent, f.proposal.ReviewVersion, []uuid.UUID{f.actions[0].ActionUUID})
	if err != nil || applied.ReceiptUUID == uuid.Nil {
		t.Fatalf("generated apply positive control: %+v %v", applied, err)
	}
	before, err := s.GetWorkItemDetail(ctx, f.collection, f.project.LocalSlug, f.item.String())
	if err != nil {
		t.Fatal(err)
	}
	var copiedProgress WorkAnnotation
	for _, annotation := range before.Annotations {
		if annotation.Kind == "progress" && annotation.State == "active" && annotation.Body == sentinel {
			copiedProgress = annotation
		}
	}
	if copiedProgress.AnnotationID == uuid.Nil || !strings.Contains(string(f.actions[0].TargetPayload), sentinel) {
		t.Fatal("pre-Forget positive control did not retain actual source-derived action and applied progress")
	}
	var copiedHistoryID uuid.UUID
	if err := s.pool.QueryRow(ctx, `SELECT history_id FROM cairn.work_annotation_history
		WHERE annotation_id=$1 AND revision=$2 AND body=$3`, copiedProgress.AnnotationID, copiedProgress.Revision, sentinel).Scan(&copiedHistoryID); err != nil {
		t.Fatalf("pre-Forget copied annotation history missing: %v", err)
	}
	const controlPayloadQuery = `SELECT jsonb_build_array(to_jsonb(j),to_jsonb(p),to_jsonb(a))::text
		FROM cairn.work_advice_job j JOIN cairn.work_advice_proposal p USING(job_uuid)
		JOIN cairn.work_advice_action a USING(proposal_uuid)
		WHERE j.job_uuid=$1 AND p.proposal_uuid=$2 AND a.action_uuid=$3`
	var controlBefore string
	if err := s.pool.QueryRow(ctx, controlPayloadQuery, control.job.JobUUID, control.proposal.ProposalUUID, control.actions[0].ActionUUID).Scan(&controlBefore); err != nil {
		t.Fatalf("pre-Forget unrelated stored control missing: %v", err)
	}
	grant, err := s.Bootstrap(ctx, BootstrapRequest{RequestID: uuid.NewString(), Reason: "Owned disposable advice Forget regression"})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := s.PreviewDeletion(ctx, f.source.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: f.source.RecordID, ExpectedVersion: f.source.Version, GrantID: grant.ID, PreviewID: preview.PreviewID}); err != nil {
		t.Fatal(err)
	}
	check := func(label string, value any) {
		t.Helper()
		encoded, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(encoded), sentinel) {
			t.Errorf("%s returned text copied from a forgotten selected input", label)
		}
	}
	proposal, actions, proposalErr := s.GetAdviceProposal(ctx, f.proposal.ProposalUUID)
	check("retained proposal/actions", []any{proposal, actions, proposalErr})
	listed, listErr := s.ListAdviceProposals(ctx, f.project.BindingID, f.item)
	check("proposal list", []any{listed, listErr})
	job, jobErr := s.GetAdviceJob(ctx, f.job.JobUUID)
	check("retained job", []any{job, jobErr})
	detail, detailErr := s.GetWorkItemDetail(ctx, f.collection, f.project.LocalSlug, f.item.String())
	check("applied work annotations", []any{detail, detailErr})
	retry, retryErr := s.ApplyAdviceProposal(ctx, intent, f.proposal.ReviewVersion, []uuid.UUID{f.actions[0].ActionUUID})
	check("cached apply result", []any{retry, retryErr})
	// Read the exact retained rows, not only their visibility-filtered Store
	// projections. Full-row JSON includes every stored payload column (including
	// input_snapshot when present); these reads never fabricate domain state.
	// Requiring each original identity also prevents deletion from passing as scrub.
	for _, row := range []struct {
		label string
		query string
		args  []any
	}{
		{"stored job", `SELECT to_jsonb(j)::text FROM cairn.work_advice_job j
			WHERE job_uuid=$1 AND request_uuid=$2 AND binding_id=$3 AND plane_item_uuid=$4`,
			[]any{f.job.JobUUID, f.job.RequestUUID, f.project.BindingID, f.item}},
		{"stored proposal", `SELECT to_jsonb(p)::text FROM cairn.work_advice_proposal p
			WHERE proposal_uuid=$1 AND job_uuid=$2 AND binding_id=$3 AND plane_item_uuid=$4`,
			[]any{f.proposal.ProposalUUID, f.job.JobUUID, f.project.BindingID, f.item}},
		{"stored action", `SELECT to_jsonb(a)::text FROM cairn.work_advice_action a
			WHERE action_uuid=$1 AND proposal_uuid=$2`, []any{f.actions[0].ActionUUID, f.proposal.ProposalUUID}},
		{"stored annotation", `SELECT to_jsonb(a)::text FROM cairn.work_annotation a
			WHERE annotation_id=$1 AND binding_id=$2 AND plane_item_uuid=$3 AND kind='progress'`,
			[]any{copiedProgress.AnnotationID, f.project.BindingID, f.item}},
		{"stored original annotation history", `SELECT to_jsonb(h)::text FROM cairn.work_annotation_history h
			WHERE history_id=$1 AND annotation_id=$2 AND revision=$3`,
			[]any{copiedHistoryID, copiedProgress.AnnotationID, copiedProgress.Revision}},
		{"stored application receipt", `SELECT to_jsonb(r)::text FROM cairn.work_advice_application_receipt r
			WHERE receipt_uuid=$1 AND request_uuid=$2 AND proposal_uuid=$3`,
			[]any{applied.ReceiptUUID, intent.RequestUUID, f.proposal.ProposalUUID}},
	} {
		var stored string
		if err := s.pool.QueryRow(ctx, row.query, row.args...).Scan(&stored); err != nil {
			t.Errorf("%s did not retain its exact identity after Forget: %v", row.label, err)
			continue
		}
		if strings.Contains(stored, sentinel) {
			t.Errorf("%s retained text copied from a forgotten selected input", row.label)
		}
	}
	var copies int
	if err := s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.work_annotation_history h JOIN cairn.work_annotation a USING(annotation_id) WHERE a.binding_id=$1 AND h.body LIKE $2`, f.project.BindingID, "%"+sentinel+"%").Scan(&copies); err != nil {
		t.Fatal(err)
	} else if copies != 0 {
		t.Errorf("known copied text survives in %d annotation history rows", copies)
	}
	retainedControl, controlActions, err := s.GetAdviceProposal(ctx, control.proposal.ProposalUUID)
	if err != nil || retainedControl.ProposalUUID != control.proposal.ProposalUUID || len(controlActions) != 1 ||
		!strings.Contains(string(controlActions[0].TargetPayload), "Independent control proposal") {
		t.Fatalf("Forget damaged unrelated collection control: %+v %+v %v", retainedControl, controlActions, err)
	}
	var controlAfter string
	if err := s.pool.QueryRow(ctx, controlPayloadQuery, control.job.JobUUID, control.proposal.ProposalUUID, control.actions[0].ActionUUID).Scan(&controlAfter); err != nil {
		t.Fatalf("Forget removed unrelated stored control: %v", err)
	}
	if controlAfter != controlBefore {
		t.Error("Forget changed unrelated collection's stored job/proposal/action")
	}
}
