package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/advice"
	"github.com/halbritt/cairn/internal/plane"
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

func TestWorkAdviceHTTP_APIEndpoints(t *testing.T) {
	repo := "repo-advice-http-" + uuid.NewString()[:8]
	s := testWorkAdviceStore(t, repo)
	ctx := context.Background()
	enableWorkAdviceFeatureCLI(t, s, ctx)

	slug := "proj-" + uuid.NewString()[:8]
	bindingID, itemUUID := setupAdviceCLIWorkProjectAndItem(t, s, ctx, slug)

	// 1. POST /api/work/advice/profile/register
	profBody := map[string]any{
		"profile":          "http-prof",
		"route":            "codex-subscription",
		"executable":       "/usr/bin/codex",
		"binary_sha256":    "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
		"codex_home":       "/tmp/codex-home",
		"model":            "o3-mini",
		"timeout_ms":       120000,
		"max_output_bytes": 65536,
	}
	bodyBytes, _ := json.Marshal(profBody)
	req := httptest.NewRequest(http.MethodPost, "/api/work/advice/profile/register", bytes.NewReader(bodyBytes))
	rec := httptest.NewRecorder()
	HandleWorkAdviceAPI(s, repo, rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("profile register HTTP status %d: %s", rec.Code, rec.Body.String())
	}

	// 2. GET /api/work/advice/profile/list
	req = httptest.NewRequest(http.MethodGet, "/api/work/advice/profile/list", nil)
	rec = httptest.NewRecorder()
	HandleWorkAdviceAPI(s, repo, rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("profile list HTTP status %d: %s", rec.Code, rec.Body.String())
	}

	// 3. GET /api/work/advice/profile/show
	req = httptest.NewRequest(http.MethodGet, "/api/work/advice/profile/show?profile=http-prof", nil)
	rec = httptest.NewRecorder()
	HandleWorkAdviceAPI(s, repo, rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("profile show HTTP status %d: %s", rec.Code, rec.Body.String())
	}

	// 4. GET /api/work/advice/policy
	req = httptest.NewRequest(http.MethodGet, "/api/work/advice/policy?project="+slug, nil)
	rec = httptest.NewRecorder()
	HandleWorkAdviceAPI(s, repo, rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("policy get HTTP status %d: %s", rec.Code, rec.Body.String())
	}

	// 5. POST /api/work/advice/policy
	polBody := map[string]any{
		"project": slug,
		"fields":  []string{"identifier", "title", "blockers"},
	}
	pBytes, _ := json.Marshal(polBody)
	req = httptest.NewRequest(http.MethodPost, "/api/work/advice/policy", bytes.NewReader(pBytes))
	rec = httptest.NewRecorder()
	HandleWorkAdviceAPI(s, repo, rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("policy post HTTP status %d: %s", rec.Code, rec.Body.String())
	}

	// Seed completed proposal for HTTP apply/undo/reject
	jobUUID := uuid.New()
	job := &core.WorkAdviceJob{
		JobUUID:             jobUUID,
		BindingID:           bindingID,
		PlaneItemUUID:       itemUUID,
		ProfileName:         "http-prof",
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
		TargetPayload: json.RawMessage(`{"text":"http progress test"}`),
	}
	if err := s.RecordAdviceProposal(ctx, prop, []*core.WorkAdviceAction{act}); err != nil {
		t.Fatalf("RecordAdviceProposal failed: %v", err)
	}

	// 6. GET /api/work/advice/list
	req = httptest.NewRequest(http.MethodGet, fmt.Sprintf("/api/work/advice/list?project=%s&item=%s", slug, itemUUID), nil)
	rec = httptest.NewRecorder()
	HandleWorkAdviceAPI(s, repo, rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("advice list HTTP status %d: %s", rec.Code, rec.Body.String())
	}

	// 7. GET /api/work/advice/show
	req = httptest.NewRequest(http.MethodGet, "/api/work/advice/show?proposal="+propUUID.String(), nil)
	rec = httptest.NewRecorder()
	HandleWorkAdviceAPI(s, repo, rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("advice show HTTP status %d: %s", rec.Code, rec.Body.String())
	}

	// 8. POST /api/work/advice/apply
	applyReqUUID := uuid.New()
	applyBody := map[string]any{
		"proposal_uuid": propUUID,
		"version":       1,
		"actions":       []uuid.UUID{actUUID},
		"request_id":    applyReqUUID.String(),
	}
	aBytes, _ := json.Marshal(applyBody)
	req = httptest.NewRequest(http.MethodPost, "/api/work/advice/apply", bytes.NewReader(aBytes))
	rec = httptest.NewRecorder()
	HandleWorkAdviceAPI(s, repo, rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("advice apply HTTP status %d: %s", rec.Code, rec.Body.String())
	}

	// 9. POST /api/work/advice/undo
	undoBody := map[string]any{
		"request_uuid": applyReqUUID,
	}
	uBytes, _ := json.Marshal(undoBody)
	req = httptest.NewRequest(http.MethodPost, "/api/work/advice/undo", bytes.NewReader(uBytes))
	rec = httptest.NewRecorder()
	HandleWorkAdviceAPI(s, repo, rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("advice undo HTTP status %d: %s", rec.Code, rec.Body.String())
	}

	// 10. POST /api/work/advice/reconcile
	req = httptest.NewRequest(http.MethodPost, "/api/work/advice/reconcile", nil)
	rec = httptest.NewRecorder()
	HandleWorkAdviceAPI(s, repo, rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("advice reconcile HTTP status %d: %s", rec.Code, rec.Body.String())
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

	// 3. HTTP: POST /api/work/advice/control with both nil should fail
	emptyBody := map[string]any{}
	eb, _ := json.Marshal(emptyBody)
	req := httptest.NewRequest(http.MethodPost, "/api/work/advice/control", bytes.NewReader(eb))
	rec := httptest.NewRecorder()
	HandleWorkAdviceAPI(s, repo, rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for empty control body, got %d", rec.Code)
	}

	// 4. HTTP: POST with only apply_enabled: true should preserve generation_enabled: true
	applyOnlyBody := map[string]any{"apply_enabled": true}
	ab, _ := json.Marshal(applyOnlyBody)
	req = httptest.NewRequest(http.MethodPost, "/api/work/advice/control", bytes.NewReader(ab))
	rec = httptest.NewRecorder()
	HandleWorkAdviceAPI(s, repo, rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 for apply-only control body, got %d: %s", rec.Code, rec.Body.String())
	}
	ctrl, err = s.GetAdviceControl(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if !ctrl.GenerationEnabled || !ctrl.ApplyEnabled {
		t.Fatalf("expected both true, got gen=%v apply=%v", ctrl.GenerationEnabled, ctrl.ApplyEnabled)
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
		Sensitivity: "shareable",
		Scope:       core.Scope{Repo: repo, TaskID: "*", RunID: "*"},
	}
	src, err := s.Create(ctx, core.CreateRequest{Draft: noteDraft})
	if err != nil {
		t.Fatal(err)
	}
	noteDraft2 := core.Draft{
		Kind:        "note",
		Body:        "Updated source version 2 with specific guidance",
		Sensitivity: "shareable",
		Scope:       core.Scope{Repo: repo, TaskID: "*", RunID: "*"},
	}
	src2, err := s.Edit(ctx, core.EditRequest{
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

	url := fmt.Sprintf("/api/work/advice?binding_id=%s&item_uuid=%s", bindingID, itemUUID)
	req := httptest.NewRequest(http.MethodGet, url, nil)
	rec := httptest.NewRecorder()
	HandleWorkAdviceAPI(s, repo, rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("GET advice failed with code %d: %s", rec.Code, rec.Body.String())
	}

	var show WorkAdviceShowEnvelope
	if err := json.Unmarshal(rec.Body.Bytes(), &show); err != nil {
		t.Fatalf("failed to decode show envelope: %v", err)
	}

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
	srcCitation := actProg.Review.SourceReferences[0]
	if srcCitation.Version != 2 || srcCitation.ObservedAt == "" || srcCitation.BodySHA256 == "" {
		t.Fatalf("expected source citation to reflect v2 metadata, got version=%d obsAt=%q bodySha=%q",
			srcCitation.Version, srcCitation.ObservedAt, srcCitation.BodySHA256)
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
		ExpectedRevision: prog.Revision,
		Body:             "manual independent progress edit",
	})
	if err != nil {
		t.Fatal(err)
	}

	req2 := httptest.NewRequest(http.MethodGet, url, nil)
	rec2 := httptest.NewRecorder()
	HandleWorkAdviceAPI(s, repo, rec2, req2)
	if rec2.Code != http.StatusOK {
		t.Fatalf("GET advice failed with code %d: %s", rec2.Code, rec2.Body.String())
	}

	var show2 WorkAdviceShowEnvelope
	if err := json.Unmarshal(rec2.Body.Bytes(), &show2); err != nil {
		t.Fatalf("failed to decode show envelope: %v", err)
	}

	if show2.Currentness.State != "stale" {
		t.Fatalf("expected currentness state 'stale' after manual progress edit, got %q", show2.Currentness.State)
	}

	actProg2 := show2.Actions[0]
	beforeProg2, ok := actProg2.Review.Before.(map[string]any)
	if !ok || beforeProg2["body"] != "captured initial progress" || int64(beforeProg2["revision"].(float64)) != prog.Revision {
		t.Fatalf("expected Before to remain unchanged at captured state, got %v", actProg2.Review.Before)
	}
}
