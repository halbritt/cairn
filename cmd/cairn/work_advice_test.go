package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/advice"
	"github.com/halbritt/cairn/internal/plane"
	"github.com/jackc/pgx/v5/pgxpool"
)

func testWorkAdviceStore(t *testing.T, repo string) *core.Store {
	t.Helper()
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("PostgreSQL integration test: run make test-integration")
	}
	channel := core.Channel{
		Principal: "operator:test",
		Operator:  true,
		Repo:      repo,
	}
	s, err := core.Open(context.Background(), dsn, channel)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.Close)
	if err = s.Migrate(context.Background()); err != nil {
		t.Fatal(err)
	}
	return s
}

func setupAdviceCLIWorkProjectAndItem(t *testing.T, s *core.Store, ctx context.Context, slug string) (uuid.UUID, uuid.UUID) {
	t.Helper()
	ws := uuid.New()
	proj := uuid.New()

	p, err := s.AddWorkProject(ctx, core.AddWorkProjectRequest{
		LocalSlug:      slug,
		Collection:     s.Channel().Repo,
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  ws,
		ProjectUUID:    proj,
		ConfigRevision: "rev-test",
	})
	if err != nil {
		t.Fatalf("AddWorkProject failed: %v", err)
	}

	itemUUID := uuid.New()
	refCtx, err := s.PrepareRefresh(ctx, p.Collection, p.LocalSlug)
	if err != nil {
		t.Fatalf("PrepareRefresh failed: %v", err)
	}
	items := []plane.NormalizedItem{
		{
			PlaneItemUUID: itemUUID,
			Identifier:    fmt.Sprintf("%s-1", strings.ToUpper(slug)),
			Title:         "Test Work Item",
			StateName:     "In Progress",
			StateGroup:    "started",
		},
	}
	_, err = s.CommitRefreshSnapshot(ctx, refCtx, items, false, nil)
	if err != nil {
		t.Fatalf("CommitRefreshSnapshot failed: %v", err)
	}

	_ = s.SetWorkAdvicePolicy(ctx, p.BindingID, []string{"identifier", "title", "state", "blockers", "artifacts", "sources"})
	return p.BindingID, itemUUID
}

func enableWorkAdviceFeatureCLI(t *testing.T, s *core.Store, ctx context.Context) {
	t.Helper()
	err := s.SetWorkFeatureStatus(ctx, core.SetWorkFeatureStatusRequest{
		FeatureName: "work-advice/1",
		Status:      "active",
	})
	if err != nil {
		t.Fatalf("failed to enable work-advice/1: %v", err)
	}
}

func TestWorkAdviceCLI_BasicWorkflow(t *testing.T) {
	repo := "repo-advice-cli-" + uuid.NewString()[:8]
	s := testWorkAdviceStore(t, repo)
	ctx := context.Background()
	enableWorkAdviceFeatureCLI(t, s, ctx)

	slug := "proj-" + uuid.NewString()[:8]
	bindingID, itemUUID := setupAdviceCLIWorkProjectAndItem(t, s, ctx, slug)

	// 1. Help
	res, err := workAdviceCommand(ctx, s, []string{"--help"})
	if err != nil {
		t.Fatalf("help failed: %v", err)
	}
	if !strings.Contains(fmt.Sprint(res), "cairn work advice:") {
		t.Errorf("unexpected help: %v", res)
	}

	// 2. Profile register
	res, err = workAdviceCommand(ctx, s, []string{
		"profile", "register",
		"--profile", "cli-test-prof",
		"--executable", "/usr/bin/codex",
		"--binary-sha256", "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
		"--codex-home", "/tmp/codex-home",
		"--model", "o3-mini",
	})
	if err != nil {
		t.Fatalf("profile register failed: %v", err)
	}

	// 3. Profile list
	res, err = workAdviceCommand(ctx, s, []string{"profile", "list"})
	if err != nil {
		t.Fatalf("profile list failed: %v", err)
	}

	// 4. Profile show
	res, err = workAdviceCommand(ctx, s, []string{"profile", "show", "--profile", "cli-test-prof"})
	if err != nil {
		t.Fatalf("profile show failed: %v", err)
	}

	// 5. Policy get and set
	res, err = workAdviceCommand(ctx, s, []string{"policy", "get", "--project", slug, "--collection", repo})
	if err != nil {
		t.Fatalf("policy get failed: %v", err)
	}
	res, err = workAdviceCommand(ctx, s, []string{
		"policy", "set",
		"--project", slug,
		"--fields", "identifier,title,state",
		"--collection", repo,
	})
	if err != nil {
		t.Fatalf("policy set failed: %v", err)
	}

	// Seed completed proposal to test list/show/apply/reject/undo CLI
	jobUUID := uuid.New()
	job := &core.WorkAdviceJob{
		JobUUID:             jobUUID,
		BindingID:           bindingID,
		PlaneItemUUID:       itemUUID,
		ProfileName:         "cli-test-prof",
		ModelRoute:          "o3-mini",
		TemplateVersion:     1,
		RequestUUID:         uuid.New(),
		InputManifestDigest: "m-dig",
		InputPayloadDigest:  "p-dig",
		Status:              "completed",
		ExpiresAt:           time.Now().Add(1 * time.Hour),
	}
	if err := s.CreateAdviceJob(ctx, job); err != nil {
		t.Fatalf("CreateAdviceJob failed: %v", err)
	}

	propUUID := uuid.New()
	prop := &core.WorkAdviceProposal{
		ProposalUUID:      propUUID,
		JobUUID:           jobUUID,
		BindingID:         bindingID,
		PlaneItemUUID:     itemUUID,
		ReviewVersion:     1,
		Status:            "pending",
		NormalizedPayload: json.RawMessage(`{"actions":[]}`),
		ExpiresAt:         time.Now().Add(1 * time.Hour),
	}
	actUUID := uuid.New()
	act := &core.WorkAdviceAction{
		ActionUUID:    actUUID,
		ProposalUUID:  propUUID,
		ActionIndex:   0,
		ActionType:    "annotate_progress",
		TargetPayload: json.RawMessage(`{"text":"cli progress test"}`),
	}
	if err := s.RecordAdviceProposal(ctx, prop, []*core.WorkAdviceAction{act}); err != nil {
		t.Fatalf("RecordAdviceProposal failed: %v", err)
	}

	// 6. List advice
	res, err = workAdviceCommand(ctx, s, []string{"list", "--project", slug, "--item", itemUUID.String(), "--collection", repo})
	if err != nil {
		t.Fatalf("list failed: %v", err)
	}

	// 7. Show advice
	res, err = workAdviceCommand(ctx, s, []string{"show", "--proposal", propUUID.String(), "--collection", repo})
	if err != nil {
		t.Fatalf("show failed: %v", err)
	}

	// 8. Apply advice
	applyReqUUID := uuid.New()
	res, err = workAdviceCommand(ctx, s, []string{
		"apply",
		"--proposal", propUUID.String(),
		"--version", "1",
		"--action", actUUID.String(),
		"--request-id", applyReqUUID.String(),
		"--collection", repo,
	})
	if err != nil {
		t.Fatalf("apply failed: %v", err)
	}

	// 9. Undo advice
	res, err = workAdviceCommand(ctx, s, []string{
		"undo",
		"--request-id", applyReqUUID.String(),
		"--collection", repo,
	})
	if err != nil {
		t.Fatalf("undo failed: %v", err)
	}

	// 10. Reconcile
	res, err = workAdviceCommand(ctx, s, []string{"reconcile", "--collection", repo})
	if err != nil {
		t.Fatalf("reconcile failed: %v", err)
	}
}

func TestWorkAdviceControl_FlagPreservation(t *testing.T) {
	repo := "repo-advice-ctrl-" + uuid.NewString()[:8]
	s := testWorkAdviceStore(t, repo)
	ctx := context.Background()
	enableWorkAdviceFeatureCLI(t, s, ctx)

	// Set both to false directly
	_, err := s.SetAdviceControl(ctx, false, false)
	if err != nil {
		t.Fatal(err)
	}

	// 1. CLI: Set only generation to true
	out, err := run(ctx, []string{"work", "advice", "control", "set", "--generation=true"}, strings.NewReader(""))
	if err != nil {
		t.Fatalf("work advice control set --generation failed: %v", err)
	}
	res, ok := out.(map[string]any)
	if !ok {
		t.Fatalf("expected map[string]any output, got %T", out)
	}
	if res["generation_enabled"] != true {
		t.Fatalf("expected generation_enabled=true, got %v", res["generation_enabled"])
	}
	if res["apply_enabled"] != false {
		t.Fatalf("expected apply_enabled to remain false, got %v", res["apply_enabled"])
	}

	// Verify store state directly
	ctrl, err := s.GetAdviceControl(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if !ctrl.GenerationEnabled || ctrl.ApplyEnabled {
		t.Fatalf("expected generation=true and apply=false, got gen=%v apply=%v", ctrl.GenerationEnabled, ctrl.ApplyEnabled)
	}

	// 2. CLI: Neither flag provided should fail
	_, err = run(ctx, []string{"work", "advice", "control", "set"}, strings.NewReader(""))
	if err == nil {
		t.Fatalf("expected failure when neither flag is supplied")
	}

}

type showMockAdviceProvider struct {
	resultJSON string
}

func (p *showMockAdviceProvider) Name() string { return "mock" }
func (p *showMockAdviceProvider) VerifyCapabilities(ctx context.Context) (*advice.CodexCapabilities, error) {
	return &advice.CodexCapabilities{}, nil
}
func (p *showMockAdviceProvider) GenerateAdvice(ctx context.Context, snapshot *advice.Snapshot, deadline time.Duration, onLaunch func(advice.ProcessIdentity) error) (*advice.Proposal, *advice.ProcessResult, error) {
	prop, err := advice.ParseProposal(p.resultJSON, snapshot)
	if err != nil {
		return nil, nil, err
	}
	return prop, &advice.ProcessResult{ExitCode: 0}, nil
}

func readWorkAdviceShow(t *testing.T, ctx context.Context, store *core.Store, proposal uuid.UUID) WorkAdviceShowEnvelope {
	t.Helper()
	out, err := workAdviceCommand(ctx, store, []string{"show", "--proposal", proposal.String()})
	if err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(out)
	if err != nil {
		t.Fatal(err)
	}
	var review WorkAdviceShowEnvelope
	if err := json.Unmarshal(raw, &review); err != nil {
		t.Fatal(err)
	}
	return review
}

func TestWorkAdviceShow_EnvelopeCapturedBeforeAndStaleCurrentness(t *testing.T) {
	repo := "repo-advice-show-" + uuid.NewString()[:8]
	s := testWorkAdviceStore(t, repo)
	ctx := context.Background()

	for _, f := range []string{"work-view/1", "work-artifacts/1", "work-advice/1"} {
		if err := s.SetWorkFeatureStatus(ctx, core.SetWorkFeatureStatusRequest{FeatureName: f, Status: "active"}); err != nil {
			t.Fatal(err)
		}
	}

	slug := "show-" + uuid.NewString()[:8]
	bindingID, itemUUID := setupAdviceCLIWorkProjectAndItem(t, s, ctx, slug)

	noteDraft := core.Draft{
		Kind:        "note",
		Body:        "Initial source version 1",
		ClaimType:   "self",
		Sensitivity: "shareable",
		Scope:       core.Scope{Repo: repo, TaskID: "*", RunID: "*"},
	}
	src, err := s.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: noteDraft})
	if err != nil {
		t.Fatal(err)
	}
	noteDraft2 := core.Draft{
		Kind:        "note",
		Body:        "Updated source version 2 with specific guidance",
		ClaimType:   "self",
		Sensitivity: "shareable",
		Scope:       core.Scope{Repo: repo, TaskID: "*", RunID: "*"},
	}
	src2, err := s.Edit(ctx, core.EditRequest{
		RequestID:       uuid.NewString(),
		RecordID:        src.RecordID,
		Draft:           noteDraft2,
		ExpectedVersion: src.Version,
	})
	if err != nil {
		t.Fatal(err)
	}

	rev, err := s.RegisterWorkArtifact(ctx, core.RegisterWorkArtifactRequest{
		RequestID:     uuid.NewString(),
		Collection:    repo,
		Kind:          "report",
		ReportLocator: "https://reports.example.com/" + uuid.NewString(),
		RevisionLabel: "v1",
	})
	if err != nil {
		t.Fatal(err)
	}

	linked, err := s.LinkWorkArtifact(ctx, core.LinkWorkArtifactRequest{
		RequestID:   uuid.NewString(),
		Collection:  repo,
		ProjectSlug: slug,
		ItemUUID:    itemUUID,
		RevisionID:  rev.RevisionID,
		Role:        "evidence",
		Rationale:   "captured initial link",
	})
	if err != nil {
		t.Fatal(err)
	}

	prog, err := s.AddProgressAnnotation(ctx, core.AddProgressAnnotationRequest{
		RequestID:   uuid.NewString(),
		Collection:  repo,
		ProjectSlug: slug,
		ItemUUID:    itemUUID,
		Body:        "captured initial progress",
	})
	if err != nil {
		t.Fatal(err)
	}

	rawAdvice, _ := json.Marshal(map[string]any{
		"actions": []any{
			map[string]any{
				"action_type":       "annotate_progress",
				"text":              "advised new progress",
				"source_references": []string{src2.RecordID},
			},
			map[string]any{
				"action_type":          "link_artifact",
				"artifact_revision_id": rev.RevisionID.String(),
				"role":                 "evidence",
				"rationale":            "advised link update",
			},
		},
	})
	provider := &showMockAdviceProvider{resultJSON: string(rawAdvice)}
	job := &core.WorkAdviceJob{
		BindingID:       bindingID,
		PlaneItemUUID:   itemUUID,
		RequestUUID:     uuid.New(),
		ProfileName:     "mock-profile",
		ModelRoute:      "mock",
		TemplateVersion: 1,
	}
	prop, actions, err := s.GenerateWorkAdvice(ctx, job, core.AdviceInputSpec{
		BindingID:     bindingID,
		PlaneItemUUID: itemUUID,
		SelectedSources: []core.SourceRefSpec{
			{RecordID: uuid.MustParse(src2.RecordID), Version: src2.Version},
		},
		CandidateRevisionIDs: []uuid.UUID{rev.RevisionID},
	}, provider)
	if err != nil || prop == nil || len(actions) != 2 {
		t.Fatalf("GenerateWorkAdvice failed: %v", err)
	}

	show := readWorkAdviceShow(t, ctx, s, prop.ProposalUUID)

	if show.Currentness.State != "current" {
		t.Fatalf("expected currentness state 'current', got %q", show.Currentness.State)
	}

	actProg := show.Actions[0]
	beforeProg, ok := actProg.Review.Before.(map[string]any)
	if !ok || beforeProg["body"] != "captured initial progress" || int64(beforeProg["revision"].(float64)) != prog.Revision {
		t.Fatalf("unexpected before for progress: %v", actProg.Review.Before)
	}
	if len(actProg.Review.SourceReferences) != 1 {
		t.Fatalf("expected 1 source citation on progress, got %d", len(actProg.Review.SourceReferences))
	}
	expectedObs := src2.WrittenAt.UTC().Format(time.RFC3339Nano)
	if len(show.Manifest.Sources) == 0 || show.Manifest.Sources[0].ObservedAt != expectedObs {
		t.Fatalf("expected Manifest.Sources[0].ObservedAt %q, got %q", expectedObs, show.Manifest.Sources[0].ObservedAt)
	}
	srcCitation := actProg.Review.SourceReferences[0]
	if srcCitation.Version != 2 || srcCitation.ObservedAt != expectedObs || srcCitation.BodySHA256 == "" {
		t.Fatalf("expected source citation to reflect v2 metadata, got version=%d obsAt=%q (expected %q) bodySha=%q",
			srcCitation.Version, srcCitation.ObservedAt, expectedObs, srcCitation.BodySHA256)
	}

	actLink := show.Actions[1]
	beforeLink, ok := actLink.Review.Before.(map[string]any)
	if !ok || beforeLink["link_id"] != linked.LinkID.String() || int64(beforeLink["revision"].(float64)) != linked.Revision {
		t.Fatalf("unexpected before for artifact link: %v", actLink.Review.Before)
	}

	_, err = s.EditProgressAnnotation(ctx, core.EditProgressAnnotationRequest{
		RequestID:        uuid.NewString(),
		Collection:       repo,
		ProjectSlug:      slug,
		ItemUUID:         itemUUID,
		AnnotationID:     prog.AnnotationID,
		ExpectedRevision: prog.Revision,
		Body:             "manual independent progress edit",
	})
	if err != nil {
		t.Fatal(err)
	}

	show2 := readWorkAdviceShow(t, ctx, s, prop.ProposalUUID)

	if show2.Currentness.State != "stale" {
		t.Fatalf("expected currentness state 'stale' after manual progress edit, got %q", show2.Currentness.State)
	}

	actProg2 := show2.Actions[0]
	beforeProg2, ok := actProg2.Review.Before.(map[string]any)
	if !ok || beforeProg2["body"] != "captured initial progress" || int64(beforeProg2["revision"].(float64)) != prog.Revision {
		t.Fatalf("expected Before to remain unchanged at captured state, got %v", actProg2.Review.Before)
	}
}

func TestWorkAdviceShow_UnavailableBeforeAndErrorPropagation(t *testing.T) {
	repo := "repo-advice-show-unavail-" + uuid.NewString()[:8]
	s := testWorkAdviceStore(t, repo)
	ctx := context.Background()

	for _, f := range []string{"work-view/1", "work-artifacts/1", "work-advice/1"} {
		if err := s.SetWorkFeatureStatus(ctx, core.SetWorkFeatureStatusRequest{FeatureName: f, Status: "active"}); err != nil {
			t.Fatal(err)
		}
	}

	slug := "show-unavail-" + uuid.NewString()[:8]
	bindingID, itemUUID := setupAdviceCLIWorkProjectAndItem(t, s, ctx, slug)

	rawAdvice, err := json.Marshal(map[string]any{
		"summary": "advice with missing history",
		"actions": []any{
			map[string]any{
				"action_type": "annotate_progress",
				"text":        "advised progress",
			},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	provider := &showMockAdviceProvider{resultJSON: string(rawAdvice)}
	job := &core.WorkAdviceJob{
		BindingID:       bindingID,
		PlaneItemUUID:   itemUUID,
		RequestUUID:     uuid.New(),
		ProfileName:     "mock-profile",
		ModelRoute:      "mock",
		TemplateVersion: 1,
	}
	prop, actions, err := s.GenerateWorkAdvice(ctx, job, core.AdviceInputSpec{
		BindingID:     bindingID,
		PlaneItemUUID: itemUUID,
	}, provider)
	if err != nil || prop == nil || len(actions) != 1 {
		t.Fatalf("GenerateWorkAdvice failed: %v", err)
	}

	pool, err := pgxpool.New(ctx, os.Getenv("CAIRN_TEST_DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()

	nonExistentID := uuid.New()
	manBytes, _ := json.Marshal(core.WorkAdviceInputManifest{
		PreconditionProgressPresent:  true,
		PreconditionProgressID:       &nonExistentID,
		PreconditionProgressRevision: 99,
	})
	_, err = pool.Exec(ctx, `UPDATE cairn.work_advice_job SET input_manifest = $1 WHERE job_uuid = $2`, manBytes, job.JobUUID)
	if err != nil {
		t.Fatal(err)
	}

	show := readWorkAdviceShow(t, ctx, s, prop.ProposalUUID)
	if len(show.Actions) != 1 {
		t.Fatalf("expected 1 action, got %d", len(show.Actions))
	}
	bMap, ok := show.Actions[0].Review.Before.(map[string]any)
	if !ok || bMap["state"] != "unavailable" || bMap["unavailable"] != true {
		t.Fatalf("expected unavailable before, got: %v", show.Actions[0].Review.Before)
	}
	if _, hasBody := bMap["body"]; hasBody {
		t.Fatalf("expected no body in unavailable before, got: %v", bMap)
	}
	if show.Actions[0].Review.After != nil {
		t.Fatalf("expected nil after when before is unavailable, got: %v", show.Actions[0].Review.After)
	}

	wrongRepoStore := testWorkAdviceStore(t, "wrong-repo-"+uuid.NewString()[:8])
	if _, err := workAdviceCommand(ctx, wrongRepoStore, []string{"show", "--proposal", prop.ProposalUUID.String()}); err == nil {
		t.Fatal("expected advice show to fail in the wrong collection")
	}

	_, err = pool.Exec(ctx, `UPDATE cairn.work_advice_action SET target_payload = '{}'::jsonb WHERE action_uuid = $1`, actions[0].ActionUUID)
	if err != nil {
		t.Fatal(err)
	}
	showPurged := readWorkAdviceShow(t, ctx, s, prop.ProposalUUID)
	if showPurged.Actions[0].Review.After != nil {
		t.Fatalf("expected nil after for purged payload, got: %v", showPurged.Actions[0].Review.After)
	}
}
