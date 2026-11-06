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
