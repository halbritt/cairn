package core

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/advice"
	"github.com/halbritt/cairn/internal/plane"
)

// The provider boundary supplies model-result bytes to the real closed-schema
// parser. No native process/model is launched and no process identity is invented.
type workflowParsedAdviceProvider struct {
	result string
	calls  int
}

func (p *workflowParsedAdviceProvider) Name() string { return advice.RouteCodexSubscription }

func (p *workflowParsedAdviceProvider) VerifyCapabilities(context.Context) (*advice.CodexCapabilities, error) {
	return &advice.CodexCapabilities{
		Schema: advice.CodexAdviceCapabilitiesSchema, RequestSchema: advice.CodexAdviceRequestSchema,
		ResultSchema: advice.CodexAdviceResultSchema, Provider: "openai", AuthMethod: "chatgpt_subscription",
		OneRequest: true, UpstreamCommit: advice.RequiredCodexUpstreamCommit,
	}, nil
}

func (p *workflowParsedAdviceProvider) GenerateAdvice(_ context.Context, snapshot *advice.Snapshot, _ time.Duration, _ func(advice.ProcessIdentity) error) (*advice.Proposal, *advice.ProcessResult, error) {
	p.calls++
	parsed, err := advice.ParseProposal(p.result, snapshot)
	if err != nil {
		return nil, nil, err
	}
	return parsed, &advice.ProcessResult{Stdout: []byte(p.result), ExitCode: 0}, nil
}

// Regression: generation stores a ProposedAction containing its nested progress
// payload. A seeded flat TargetPayload would miss a broken generation/apply seam.
func TestWorkAdviceParsedProgressGenerateApplyUndo(t *testing.T) {
	s := restoreTestStore(t)
	// Deliberate trusted test configuration for the implemented Store methods.
	// Default CLI/API capability admission is a separate release requirement.
	policy := DefaultCompatibilityPolicy()
	policy.SupportedWorkFeatures["work-advice/1"] = true
	s.WithCompatibilityPolicy(policy)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
		RequestID: uuid.NewString(), FeatureName: "work-view/1", Status: "active",
	}); err != nil {
		t.Fatal(err)
	}
	if err := s.SetAdviceFeature(ctx, "active"); err != nil {
		t.Fatal(err)
	}
	collection := "fixture:advice-workflow:" + uuid.NewString()
	project, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		RequestID: uuid.NewString(), Collection: collection, LocalSlug: "workflow",
		PlaneInstance: "work", PlaneOrigin: "https://plane.example.com",
		WorkspaceUUID: uuid.New(), ProjectUUID: uuid.New(), ConfigRevision: "workflow-v1",
	})
	if err != nil {
		t.Fatalf("create real work project: %v", err)
	}
	itemID := uuid.New()
	refresh, err := s.PrepareRefresh(ctx, collection, project.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.CommitRefreshSnapshot(ctx, refresh, []plane.NormalizedItem{{
		PlaneItemUUID: itemID, Identifier: "WORKFLOW-1", Title: "Synthetic workflow card",
	}}, false, nil); err != nil {
		t.Fatal(err)
	}
	prior, err := s.AddProgressAnnotation(ctx, AddProgressAnnotationRequest{
		RequestID: uuid.NewString(), Collection: collection, ProjectSlug: project.LocalSlug,
		ItemUUID: itemID, Body: "Selected progress before advice",
	})
	if err != nil {
		t.Fatal(err)
	}
	source, err := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: Draft{
		Kind: "note", Body: "Synthetic reviewed source", ClaimType: "self", Sensitivity: "shareable",
		Scope: Scope{Repo: collection, TaskID: "*", RunID: "*"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	if err := s.SetWorkAdvicePolicy(ctx, project.BindingID, []string{"title", "sources"}); err != nil {
		t.Fatal(err)
	}
	const selectedProgress = "Selected advice progress from the reviewed source"
	raw, err := json.Marshal(map[string]any{"actions": []any{map[string]any{
		"action_type": advice.ActionAnnotateProgress, "text": selectedProgress,
		"source_references": []string{source.RecordID}, "rationale": "Review this local progress update",
	}}})
	if err != nil {
		t.Fatal(err)
	}
	job := &WorkAdviceJob{BindingID: project.BindingID, PlaneItemUUID: itemID,
		RequestUUID: uuid.New(), ProfileName: advice.RouteCodexSubscription, ModelRoute: "fixture-model", TemplateVersion: 1}
	provider := &workflowParsedAdviceProvider{result: string(raw)}
	t.Cleanup(func() {
		if provider.calls != 1 {
			t.Errorf("workflow invoked provider %d times, want exactly one", provider.calls)
		}
	})
	spec := AdviceInputSpec{
		BindingID: project.BindingID, PlaneItemUUID: itemID,
		SelectedSources: []SourceRefSpec{{RecordID: uuid.MustParse(source.RecordID), Version: source.Version}},
	}
	proposal, generated, err := s.GenerateWorkAdvice(ctx, job, spec, provider)
	if err != nil {
		t.Fatalf("generate parsed proposal through real Store: %v", err)
	}
	if proposal == nil || proposal.JobUUID != job.JobUUID || len(generated) != 1 {
		t.Fatalf("generation lost proposal/action identity: proposal=%+v actions=%+v", proposal, generated)
	}
	retained, actions, err := s.GetAdviceProposal(ctx, proposal.ProposalUUID)
	if err != nil {
		t.Fatal(err)
	}
	if retained.JobUUID != job.JobUUID || retained.BindingID != project.BindingID || retained.PlaneItemUUID != itemID ||
		retained.Status != "pending" || len(actions) != 1 || actions[0].ActionUUID != generated[0].ActionUUID ||
		actions[0].ActionType != advice.ActionAnnotateProgress || actions[0].Status != "pending" {
		t.Fatalf("retained proposal differs from generated intent: %+v %+v", retained, actions)
	}
	finished, err := s.GetAdviceJob(ctx, job.JobUUID)
	if err != nil || finished.Status != "completed" || finished.FinishedAt == nil {
		t.Fatalf("generated job was not completed: %+v %v", finished, err)
	}
	retriedProposal, retriedActions, err := s.GenerateWorkAdvice(ctx, job, spec, provider)
	if err != nil || retriedProposal.ProposalUUID != retained.ProposalUUID || len(retriedActions) != 1 ||
		retriedActions[0].ActionUUID != actions[0].ActionUUID || provider.calls != 1 {
		t.Fatalf("exact generation retry changed retained output or reinvoked provider: %+v %+v calls=%d error=%v", retriedProposal, retriedActions, provider.calls, err)
	}
	readAnnotations := func() []WorkAnnotation {
		t.Helper()
		detail, err := s.GetWorkItemDetail(ctx, collection, project.LocalSlug, itemID.String())
		if err != nil {
			t.Fatal(err)
		}
		return detail.Annotations
	}
	assertHistory := func(id uuid.UUID, expected ...WorkAnnotation) {
		t.Helper()
		// There is no public annotation-version history API. This read proves
		// compensation appends history instead of rewinding or discarding it.
		rows, err := s.pool.Query(ctx, `SELECT revision, body, state, author, source_references
			FROM cairn.work_annotation_history WHERE annotation_id=$1 ORDER BY revision`, id)
		if err != nil {
			t.Fatal(err)
		}
		defer rows.Close()
		index := 0
		for rows.Next() {
			var got WorkAnnotation
			var refs []byte
			if err := rows.Scan(&got.Revision, &got.Body, &got.State, &got.Author, &refs); err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal(refs, &got.SourceReferences); err != nil {
				t.Fatal(err)
			}
			if index >= len(expected) {
				t.Fatalf("annotation %s has unexpected extra history: %+v", id, got)
			}
			want := expected[index]
			if got.Revision != want.Revision || got.Body != want.Body || got.State != want.State ||
				got.Author != want.Author || len(got.SourceReferences) != len(want.SourceReferences) {
				t.Fatalf("annotation %s history row %d: got %+v, want %+v", id, index, got, want)
			}
			for i, ref := range want.SourceReferences {
				if got.SourceReferences[i] != ref {
					t.Fatalf("annotation %s history lost source reference %s", id, ref)
				}
			}
			index++
		}
		if err := rows.Err(); err != nil {
			t.Fatal(err)
		}
		if index != len(expected) {
			t.Fatalf("annotation %s retained %d history rows, want %d", id, index, len(expected))
		}
	}
	before := readAnnotations()
	if len(before) != 1 || before[0].AnnotationID != prior.AnnotationID || before[0].State != "active" || before[0].Body != prior.Body {
		t.Fatalf("generation must not apply progress: %+v", before)
	}
	intent := &WorkAdviceApplicationReceipt{RequestUUID: uuid.New(), ProposalUUID: retained.ProposalUUID}
	selected := []uuid.UUID{actions[0].ActionUUID}
	applied, err := s.ApplyAdviceProposal(ctx, intent, retained.ReviewVersion, selected)
	if err != nil {
		t.Fatalf("apply actually generated and retained progress action: %v", err)
	}
	if applied.ReceiptUUID == uuid.Nil || applied.ProposalUUID != retained.ProposalUUID || applied.AppliedByChannel != s.channel.Principal {
		t.Fatalf("application receipt lost attribution/identity: %+v", applied)
	}
	after := readAnnotations()
	var supersededPrior, appliedAnnotation WorkAnnotation
	if len(after) != 2 {
		t.Fatalf("apply must preserve prior progress and create one selected progress: %+v", after)
	}
	for _, annotation := range after {
		if annotation.AnnotationID == prior.AnnotationID {
			if annotation.State != "superseded" || annotation.Body != prior.Body || annotation.Revision != prior.Revision+1 {
				t.Fatalf("apply lost previous selected progress: %+v", annotation)
			}
			supersededPrior = annotation
			continue
		}
		if annotation.State != "active" || annotation.Kind != "progress" || annotation.Body != selectedProgress ||
			annotation.Author != s.channel.Principal || len(annotation.SourceReferences) != 1 || annotation.SourceReferences[0] != source.RecordID {
			t.Fatalf("applied annotation differs from parsed action: %+v", annotation)
		}
		appliedAnnotation = annotation
	}
	assertHistory(prior.AnnotationID, prior, supersededPrior)
	assertHistory(appliedAnnotation.AnnotationID, appliedAnnotation)
	replayed, err := s.ApplyAdviceProposal(ctx, intent, retained.ReviewVersion, selected)
	if err != nil || replayed.ReceiptUUID != applied.ReceiptUUID || len(readAnnotations()) != 2 {
		t.Fatalf("exact apply retry duplicated or changed application: %+v %v", replayed, err)
	}
	assertHistory(prior.AnnotationID, prior, supersededPrior)
	assertHistory(appliedAnnotation.AnnotationID, appliedAnnotation)
	if err := s.UndoAdviceApplication(ctx, intent.RequestUUID); err != nil {
		t.Fatalf("undo generated application: %v", err)
	}
	undone := readAnnotations()
	if len(undone) != 2 {
		t.Fatalf("undo must preserve historical annotations: %+v", undone)
	}
	for _, annotation := range undone {
		switch annotation.AnnotationID {
		case prior.AnnotationID:
			if annotation.State != "active" || annotation.Body != prior.Body || annotation.Revision != supersededPrior.Revision+1 {
				t.Fatalf("undo did not restore prior selected progress: %+v", annotation)
			}
			assertHistory(prior.AnnotationID, prior, supersededPrior, annotation)
		case appliedAnnotation.AnnotationID:
			if annotation.State != "superseded" || annotation.Body != selectedProgress || annotation.Revision != appliedAnnotation.Revision+1 {
				t.Fatalf("undo did not compensate the exact applied progress: %+v", annotation)
			}
			assertHistory(appliedAnnotation.AnnotationID, appliedAnnotation, annotation)
		default:
			t.Fatalf("undo introduced another annotation: %+v", annotation)
		}
	}
	_, actions, err = s.GetAdviceProposal(ctx, retained.ProposalUUID)
	if err != nil || len(actions) != 1 || actions[0].ActionUUID != selected[0] || actions[0].Status != "undone" {
		t.Fatalf("undo did not retain the exact action outcome: %+v %v", actions, err)
	}
}
