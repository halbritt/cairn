package main

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/plane"
)

func testWorkArtifactStore(t *testing.T, repo string) *core.Store {
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

func setupCLIWorkProjectAndItem(t *testing.T, s *core.Store, ctx context.Context, slug string) (uuid.UUID, uuid.UUID) {
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
			StateName:     "Todo",
			StateGroup:    "unstarted",
		},
	}
	_, err = s.CommitRefreshSnapshot(ctx, refCtx, items, false, nil)
	if err != nil {
		t.Fatalf("CommitRefreshSnapshot failed: %v", err)
	}
	return p.BindingID, itemUUID
}

func enableWorkArtifactFeatureCLI(t *testing.T, s *core.Store, ctx context.Context) {
	t.Helper()
	err := s.SetWorkFeatureStatus(ctx, core.SetWorkFeatureStatusRequest{
		FeatureName: "work-artifacts/1",
		Status:      "active",
	})
	if err != nil {
		t.Fatalf("Failed to enable work-artifacts/1: %v", err)
	}
}

// AC-17: cairn work artifact CLI subcommands cover register, show, link, unlink, list, and impact.
func TestWorkArtifactCLI_Help(t *testing.T) {
	ctx := context.Background()

	// 1. Bare work artifact command returns help text
	res, err := workArtifactCommand(ctx, nil, []string{})
	if err != nil {
		t.Fatalf("Bare work artifact command returned error: %v", err)
	}
	help, ok := res.(commandHelp)
	if !ok || !strings.Contains(string(help), "cairn work artifact:") {
		t.Fatalf("Expected help text, got %v", res)
	}

	// 2. --help flag returns help text
	res, err = workArtifactCommand(ctx, nil, []string{"--help"})
	if err != nil {
		t.Fatalf("work artifact --help returned error: %v", err)
	}
	help, ok = res.(commandHelp)
	if !ok || !strings.Contains(string(help), "cairn work artifact:") {
		t.Fatalf("Expected help text, got %v", res)
	}

	// 3. Unknown subcommand returns invalid
	_, err = workArtifactCommand(ctx, nil, []string{"nonexistent"})
	if err == nil || core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("Expected INVALID_REQUEST for unknown subcommand, got %v", err)
	}
}

func TestWorkArtifactCLI_RegisterAndShow(t *testing.T) {
	s := testWorkArtifactStore(t, "repo:cli-test")
	ctx := context.Background()
	enableWorkArtifactFeatureCLI(t, s, ctx)

	// 1. Register Git artifact
	gitOID := "1234567890abcdef1234567890abcdef12345678"
	reqID := uuid.NewString()
	res, err := workArtifactCommand(ctx, s, []string{
		"register",
		"--kind", "git",
		"--repo", "https://github.com/halbritt/cairn",
		"--commit", gitOID,
		"--format", "sha1",
		"--display-name", "CLI Git Commit",
		"--request-id", reqID,
		"--collection", "repo:cli-test",
	})
	if err != nil {
		t.Fatalf("work artifact register git failed: %v", err)
	}
	revGit, ok := res.(core.WorkArtifactRevision)
	if !ok || revGit.Artifact == nil || revGit.Artifact.Kind != "git" || revGit.GitCommitOID != gitOID {
		t.Fatalf("Unexpected register git result: %+v", res)
	}

	// Idempotent retry with same request ID
	resRetry, err := workArtifactCommand(ctx, s, []string{
		"register",
		"--kind", "git",
		"--repo", "https://github.com/halbritt/cairn",
		"--commit", gitOID,
		"--format", "sha1",
		"--display-name", "CLI Git Commit",
		"--request-id", reqID,
		"--collection", "repo:cli-test",
	})
	if err != nil {
		t.Fatalf("work artifact register git retry failed: %v", err)
	}
	revGitRetry, ok := resRetry.(core.WorkArtifactRevision)
	if !ok || revGitRetry.RevisionID != revGit.RevisionID {
		t.Fatalf("Retry did not return same revision: %+v", resRetry)
	}

	// 2. Show artifact by artifact UUID
	resShowArt, err := workArtifactCommand(ctx, s, []string{
		"show",
		"--artifact", revGit.ArtifactID.String(),
		"--collection", "repo:cli-test",
	})
	if err != nil {
		t.Fatalf("work artifact show artifact failed: %v", err)
	}
	art, ok := resShowArt.(core.WorkArtifact)
	if !ok || art.ArtifactID != revGit.ArtifactID {
		t.Fatalf("Unexpected show artifact result: %+v", resShowArt)
	}

	// 3. Show artifact by revision UUID
	resShowRev, err := workArtifactCommand(ctx, s, []string{
		"show",
		"--revision", revGit.RevisionID.String(),
		"--collection", "repo:cli-test",
	})
	if err != nil {
		t.Fatalf("work artifact show revision failed: %v", err)
	}
	rev, ok := resShowRev.(core.WorkArtifactRevision)
	if !ok || rev.RevisionID != revGit.RevisionID {
		t.Fatalf("Unexpected show revision result: %+v", resShowRev)
	}

	// 4. Register Report artifact
	resRep, err := workArtifactCommand(ctx, s, []string{
		"register",
		"--kind", "report",
		"--url", "https://reports.example.com/test-run.html",
		"--revision", "rev-20261105",
		"--provenance", "asserted",
		"--collection", "repo:cli-test",
	})
	if err != nil {
		t.Fatalf("work artifact register report failed: %v", err)
	}
	revRep, ok := resRep.(core.WorkArtifactRevision)
	if !ok || revRep.Artifact == nil || revRep.Artifact.Kind != "report" || revRep.ReportProvenance != "asserted" {
		t.Fatalf("Unexpected register report result: %+v", resRep)
	}

	// 5. Register CI artifact
	resCI, err := workArtifactCommand(ctx, s, []string{
		"register",
		"--kind", "ci",
		"--provider", "github-actions",
		"--ci-repo", "halbritt/cairn",
		"--run-id", "run-456",
		"--attempt", "2",
		"--status", "passed",
		"--collection", "repo:cli-test",
	})
	if err != nil {
		t.Fatalf("work artifact register ci failed: %v", err)
	}
	revCI, ok := resCI.(core.WorkArtifactRevision)
	if !ok || revCI.Artifact == nil || revCI.Artifact.Kind != "ci" || revCI.CIStatus != "passed" {
		t.Fatalf("Unexpected register ci result: %+v", resCI)
	}

	// 6. Register Cairn Result artifact
	cairnDraft := core.Draft{
		Kind: "decision",
		Body: "Architecture decision for CLI artifact test",
		Scope: core.Scope{
			Repo:   "repo:cli-test",
			TaskID: uuid.NewString(),
			RunID:  uuid.NewString(),
		},
		ClaimType: "self",
	}
	rec, err := s.Create(ctx, core.CreateRequest{
		RequestID: uuid.NewString(),
		Draft:     cairnDraft,
	})
	if err != nil {
		t.Fatalf("Create cairn record failed: %v", err)
	}

	resCairn, err := workArtifactCommand(ctx, s, []string{
		"register",
		"--kind", "cairn_result",
		"--record", rec.RecordID,
		"--version", "1",
		"--collection", "repo:cli-test",
	})
	if err != nil {
		t.Fatalf("work artifact register cairn_result failed: %v", err)
	}
	revCairn, ok := resCairn.(core.WorkArtifactRevision)
	if !ok || revCairn.Artifact == nil || revCairn.Artifact.Kind != "cairn_result" || *revCairn.CairnVersion != 1 {
		t.Fatalf("Unexpected register cairn_result result: %+v", resCairn)
	}
}

func TestWorkArtifactCLI_LinkListUnlink(t *testing.T) {
	s := testWorkArtifactStore(t, "repo:cli-test-link")
	ctx := context.Background()
	enableWorkArtifactFeatureCLI(t, s, ctx)

	_, itemUUID := setupCLIWorkProjectAndItem(t, s, ctx, "cliproj")

	// Register Git artifact
	resArt, err := workArtifactCommand(ctx, s, []string{
		"register",
		"--kind", "git",
		"--repo", "https://github.com/halbritt/cairn",
		"--commit", "abcdef1234567890abcdef1234567890abcdef12",
		"--format", "sha1",
		"--collection", "repo:cli-test-link",
	})
	if err != nil {
		t.Fatalf("Register artifact failed: %v", err)
	}
	rev := resArt.(core.WorkArtifactRevision)

	// 1. Link artifact with role 'input'
	resLinkIn, err := workArtifactCommand(ctx, s, []string{
		"link",
		"--project", "cliproj",
		"--item", itemUUID.String(),
		"--revision", rev.RevisionID.String(),
		"--role", "input",
		"--rationale", "Input requirements spec",
		"--collection", "repo:cli-test-link",
	})
	if err != nil {
		t.Fatalf("work artifact link input failed: %v", err)
	}
	linkIn := resLinkIn.(core.WorkArtifactLink)
	if linkIn.Role != "input" || linkIn.State != "active" {
		t.Fatalf("Unexpected link input result: %+v", linkIn)
	}

	// 2. Link artifact with role 'output'
	resLinkOut, err := workArtifactCommand(ctx, s, []string{
		"link",
		"--project", "cliproj",
		"--item", itemUUID.String(),
		"--revision", rev.RevisionID.String(),
		"--role", "output",
		"--rationale", "Output implementation",
		"--collection", "repo:cli-test-link",
	})
	if err != nil {
		t.Fatalf("work artifact link output failed: %v", err)
	}
	linkOut := resLinkOut.(core.WorkArtifactLink)
	if linkOut.Role != "output" || linkOut.State != "active" {
		t.Fatalf("Unexpected link output result: %+v", linkOut)
	}

	// 3. List all active links on item
	resList, err := workArtifactCommand(ctx, s, []string{
		"list",
		"--project", "cliproj",
		"--item", itemUUID.String(),
		"--collection", "repo:cli-test-link",
	})
	if err != nil {
		t.Fatalf("work artifact list failed: %v", err)
	}
	listMap, ok := resList.(map[string]any)
	if !ok {
		t.Fatalf("Unexpected list result format: %+v", resList)
	}
	links, ok := listMap["artifacts"].([]core.WorkArtifactLink)
	if !ok || len(links) != 2 {
		t.Fatalf("Expected 2 links in list, got: %+v", listMap["artifacts"])
	}

	// 4. List filtered by role 'input'
	resListFiltered, err := workArtifactCommand(ctx, s, []string{
		"list",
		"--project", "cliproj",
		"--item", itemUUID.String(),
		"--role", "input",
		"--collection", "repo:cli-test-link",
	})
	if err != nil {
		t.Fatalf("work artifact list filtered failed: %v", err)
	}
	filteredMap := resListFiltered.(map[string]any)
	filteredLinks := filteredMap["artifacts"].([]core.WorkArtifactLink)
	if len(filteredLinks) != 1 || filteredLinks[0].Role != "input" {
		t.Fatalf("Expected 1 input link, got %+v", filteredLinks)
	}

	// 5. Unlink input link
	resUnlink, err := workArtifactCommand(ctx, s, []string{
		"unlink",
		"--project", "cliproj",
		"--item", itemUUID.String(),
		"--link-id", linkIn.LinkID.String(),
		"--expected-revision", "1",
		"--collection", "repo:cli-test-link",
	})
	if err != nil {
		t.Fatalf("work artifact unlink failed: %v", err)
	}
	unlinked := resUnlink.(core.WorkArtifactLink)
	if unlinked.State != "removed" {
		t.Fatalf("Expected unlinked state='removed', got %s", unlinked.State)
	}

	// 6. List active links: only output link remains
	resListAfter, err := workArtifactCommand(ctx, s, []string{
		"list",
		"--project", "cliproj",
		"--item", itemUUID.String(),
		"--collection", "repo:cli-test-link",
	})
	if err != nil {
		t.Fatalf("work artifact list after unlink failed: %v", err)
	}
	afterMap := resListAfter.(map[string]any)
	afterLinks := afterMap["artifacts"].([]core.WorkArtifactLink)
	if len(afterLinks) != 1 || afterLinks[0].LinkID != linkOut.LinkID {
		t.Fatalf("Expected 1 active link after unlinking, got %+v", afterLinks)
	}

	// 7. List including removed links
	resListAll, err := workArtifactCommand(ctx, s, []string{
		"list",
		"--project", "cliproj",
		"--item", itemUUID.String(),
		"--include-removed",
		"--collection", "repo:cli-test-link",
	})
	if err != nil {
		t.Fatalf("work artifact list including removed failed: %v", err)
	}
	allMap := resListAll.(map[string]any)
	allLinks := allMap["artifacts"].([]core.WorkArtifactLink)
	if len(allLinks) != 2 {
		t.Fatalf("Expected 2 links including removed, got %+v", allLinks)
	}
}

func TestWorkArtifactCLI_ReverseImpact(t *testing.T) {
	s := testWorkArtifactStore(t, "repo:cli-test-impact")
	ctx := context.Background()
	enableWorkArtifactFeatureCLI(t, s, ctx)

	_, item1 := setupCLIWorkProjectAndItem(t, s, ctx, "impact1")
	_, item2 := setupCLIWorkProjectAndItem(t, s, ctx, "impact2")

	// Register Git artifact
	resArt, err := workArtifactCommand(ctx, s, []string{
		"register",
		"--kind", "git",
		"--repo", "https://github.com/halbritt/cairn",
		"--commit", "fedcba0987654321fedcba0987654321fedcba09",
		"--format", "sha1",
		"--collection", "repo:cli-test-impact",
	})
	if err != nil {
		t.Fatalf("Register artifact failed: %v", err)
	}
	rev := resArt.(core.WorkArtifactRevision)

	// Link both items to the revision
	_, err = workArtifactCommand(ctx, s, []string{
		"link",
		"--project", "impact1",
		"--item", item1.String(),
		"--revision", rev.RevisionID.String(),
		"--role", "evidence",
		"--collection", "repo:cli-test-impact",
	})
	if err != nil {
		t.Fatalf("Link item 1 failed: %v", err)
	}

	_, err = workArtifactCommand(ctx, s, []string{
		"link",
		"--project", "impact2",
		"--item", item2.String(),
		"--revision", rev.RevisionID.String(),
		"--role", "output",
		"--collection", "repo:cli-test-impact",
	})
	if err != nil {
		t.Fatalf("Link item 2 failed: %v", err)
	}

	// 1. Run impact query with limit 1
	resImp1, err := workArtifactCommand(ctx, s, []string{
		"impact",
		"--revision", rev.RevisionID.String(),
		"--limit", "1",
		"--collection", "repo:cli-test-impact",
	})
	if err != nil {
		t.Fatalf("work artifact impact page 1 failed: %v", err)
	}
	page1 := resImp1.(core.ReverseImpactPage)
	if len(page1.Impacts) != 1 || page1.NextCursor == "" {
		t.Fatalf("Expected page 1 with 1 impact and next cursor, got %+v", page1)
	}

	// 2. Follow next cursor
	resImp2, err := workArtifactCommand(ctx, s, []string{
		"impact",
		"--revision", rev.RevisionID.String(),
		"--limit", "1",
		"--cursor", page1.NextCursor,
		"--collection", "repo:cli-test-impact",
	})
	if err != nil {
		t.Fatalf("work artifact impact page 2 failed: %v", err)
	}
	page2 := resImp2.(core.ReverseImpactPage)
	if len(page2.Impacts) != 1 {
		t.Fatalf("Expected page 2 with 1 impact, got %+v", page2)
	}
	if page2.Impacts[0].PlaneItemUUID == page1.Impacts[0].PlaneItemUUID {
		t.Fatalf("Page 2 item should differ from page 1 item, both were %s", page2.Impacts[0].PlaneItemUUID)
	}
}

func TestWorkArtifactCLI_ValidationRefusals(t *testing.T) {
	s := testWorkArtifactStore(t, "repo:cli-test-refusals")
	ctx := context.Background()
	enableWorkArtifactFeatureCLI(t, s, ctx)

	// Missing --kind
	_, err := workArtifactCommand(ctx, s, []string{
		"register",
		"--collection", "repo:cli-test-refusals",
	})
	if err == nil || core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("Expected INVALID_REQUEST for missing --kind, got %v", err)
	}

	// Insecure URL scheme
	_, err = workArtifactCommand(ctx, s, []string{
		"register",
		"--kind", "report",
		"--url", "http://insecure.example.com/report.html",
		"--collection", "repo:cli-test-refusals",
	})
	if err == nil || core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("Expected INVALID_REQUEST for http scheme, got %v", err)
	}

	// Credential URL
	_, err = workArtifactCommand(ctx, s, []string{
		"register",
		"--kind", "report",
		"--url", "https://user:pass@example.com/report.html",
		"--collection", "repo:cli-test-refusals",
	})
	if err == nil || core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("Expected INVALID_REQUEST for credential URL, got %v", err)
	}

	// Bad commit OID
	_, err = workArtifactCommand(ctx, s, []string{
		"register",
		"--kind", "git",
		"--repo", "https://github.com/halbritt/cairn",
		"--commit", "not-a-valid-hex-commit-oid",
		"--collection", "repo:cli-test-refusals",
	})
	if err == nil || core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("Expected INVALID_REQUEST for non-hex commit OID, got %v", err)
	}

	// Missing required flags for link
	_, err = workArtifactCommand(ctx, s, []string{
		"link",
		"--project", "testproj",
		"--collection", "repo:cli-test-refusals",
	})
	if err == nil || core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("Expected INVALID_REQUEST for missing link flags, got %v", err)
	}

	// Missing required flags for unlink
	_, err = workArtifactCommand(ctx, s, []string{
		"unlink",
		"--project", "testproj",
		"--collection", "repo:cli-test-refusals",
	})
	if err == nil || core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("Expected INVALID_REQUEST for missing unlink link-id, got %v", err)
	}

	// Purge CLI execution and missing flags
	_, err = workArtifactCommand(ctx, s, []string{
		"purge",
		"--collection", "repo:cli-test-refusals",
	})
	if err == nil || core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("Expected INVALID_REQUEST for missing purge artifact ID, got %v", err)
	}
}

func TestWorkArtifactHTTP_StrictJSONAndRoutes(t *testing.T) {
	s := testWorkArtifactStore(t, "repo:http-test")
	ctx := context.Background()

	// 1. decodeStrictJSON negative tests
	t.Run("decodeStrictJSON_TrailingBrace", func(t *testing.T) {
		body := []byte(fmt.Sprintf(`{"request_id":%q,"kind":"git","git_repo":"https://github.com/a/b","git_commit_oid":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}`, uuid.NewString()))
		req := httptest.NewRequest(http.MethodPost, "/api/work/artifact/register", bytes.NewReader(body))
		w := httptest.NewRecorder()
		HandleWorkArtifactAPI(s, "repo:http-test", w, req)
		if w.Code != http.StatusBadRequest {
			t.Fatalf("Expected 400 for trailing brace, got %d (body: %s)", w.Code, w.Body.String())
		}
	})

	t.Run("decodeStrictJSON_TrailingSecondValue", func(t *testing.T) {
		body := []byte(fmt.Sprintf(`{"request_id":%q,"kind":"git","git_repo":"https://github.com/a/b","git_commit_oid":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"} {"extra":1}`, uuid.NewString()))
		req := httptest.NewRequest(http.MethodPost, "/api/work/artifact/register", bytes.NewReader(body))
		w := httptest.NewRecorder()
		HandleWorkArtifactAPI(s, "repo:http-test", w, req)
		if w.Code != http.StatusBadRequest {
			t.Fatalf("Expected 400 for trailing second JSON value, got %d (body: %s)", w.Code, w.Body.String())
		}
	})

	t.Run("decodeStrictJSON_Over64KiB", func(t *testing.T) {
		padding := strings.Repeat(" ", 70*1024)
		body := []byte(fmt.Sprintf(`{"request_id":%q,"kind":"git","git_repo":"https://github.com/a/b","git_commit_oid":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"%s}`, uuid.NewString(), padding))
		req := httptest.NewRequest(http.MethodPost, "/api/work/artifact/register", bytes.NewReader(body))
		w := httptest.NewRecorder()
		HandleWorkArtifactAPI(s, "repo:http-test", w, req)
		if w.Code != http.StatusBadRequest {
			t.Fatalf("Expected 400 for >64KiB body, got %d (body: %s)", w.Code, w.Body.String())
		}
	})

	// 2. HTTP parameter validations
	t.Run("ParameterValidations", func(t *testing.T) {
		dummyUUID := uuid.NewString()

		// impact limit checks
		for _, lim := range []string{"0", "-1", "201", "abc"} {
			req := httptest.NewRequest(http.MethodGet, "/api/work/artifact/impact?revision="+dummyUUID+"&limit="+lim, nil)
			w := httptest.NewRecorder()
			HandleWorkArtifactAPI(s, "repo:http-test", w, req)
			if w.Code != http.StatusBadRequest {
				t.Fatalf("Expected 400 for impact limit %q, got %d", lim, w.Code)
			}
		}

		// history limit checks
		for _, lim := range []string{"0", "-1", "201", "abc"} {
			req := httptest.NewRequest(http.MethodGet, "/api/work/artifact/history?project=proj&item="+dummyUUID+"&link_id="+dummyUUID+"&limit="+lim, nil)
			w := httptest.NewRecorder()
			HandleWorkArtifactAPI(s, "repo:http-test", w, req)
			if w.Code != http.StatusBadRequest {
				t.Fatalf("Expected 400 for history limit %q, got %d", lim, w.Code)
			}
		}

		// list include_removed checks
		req := httptest.NewRequest(http.MethodGet, "/api/work/artifact/list?project=proj&item="+dummyUUID+"&include_removed=maybe", nil)
		w := httptest.NewRecorder()
		HandleWorkArtifactAPI(s, "repo:http-test", w, req)
		if w.Code != http.StatusBadRequest {
			t.Fatalf("Expected 400 for list invalid include_removed, got %d", w.Code)
		}

		// register unrelated kind fields
		// git with report_locator:
		gitWithReport := []byte(fmt.Sprintf(`{"request_id":%q,"kind":"git","git_repo":"https://github.com/a/b","git_commit_oid":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","report_locator":"https://example.com"}`, uuid.NewString()))
		req = httptest.NewRequest(http.MethodPost, "/api/work/artifact/register", bytes.NewReader(gitWithReport))
		w = httptest.NewRecorder()
		HandleWorkArtifactAPI(s, "repo:http-test", w, req)
		if w.Code != http.StatusBadRequest {
			t.Fatalf("Expected 400 for git artifact with report_locator, got %d", w.Code)
		}

		// report with git_repo:
		reportWithGit := []byte(fmt.Sprintf(`{"request_id":%q,"kind":"report","report_locator":"https://example.com/rep.html","revision_label":"v1","git_repo":"https://github.com/a/b"}`, uuid.NewString()))
		req = httptest.NewRequest(http.MethodPost, "/api/work/artifact/register", bytes.NewReader(reportWithGit))
		w = httptest.NewRecorder()
		HandleWorkArtifactAPI(s, "repo:http-test", w, req)
		if w.Code != http.StatusBadRequest {
			t.Fatalf("Expected 400 for report artifact with git_repo, got %d", w.Code)
		}

		// ci with cairn_record_id:
		ciWithCairn := []byte(fmt.Sprintf(`{"request_id":%q,"kind":"ci","ci_provider":"github","ci_repo":"org/repo","ci_run_id":"123","ci_attempt":1,"cairn_record_id":%q}`, uuid.NewString(), dummyUUID))
		req = httptest.NewRequest(http.MethodPost, "/api/work/artifact/register", bytes.NewReader(ciWithCairn))
		w = httptest.NewRecorder()
		HandleWorkArtifactAPI(s, "repo:http-test", w, req)
		if w.Code != http.StatusBadRequest {
			t.Fatalf("Expected 400 for ci artifact with cairn_record_id, got %d", w.Code)
		}
	})

	// 3. WorkServer blanket prefix check exemption
	t.Run("WorkServer_ExemptFromWorkViewFeatureCheck", func(t *testing.T) {
		t.Cleanup(func() {
			_ = s.SetWorkFeatureStatus(ctx, core.SetWorkFeatureStatusRequest{
				FeatureName: "work-view/1",
				Status:      "active",
			})
		})
		// Set work-view/1 to disabled
		_ = s.SetWorkFeatureStatus(ctx, core.SetWorkFeatureStatusRequest{
			FeatureName: "work-view/1",
			Status:      "disabled",
		})
		srv, err := newWorkServer(s, "repo:http-test", "testslug", 0)
		if err != nil {
			t.Fatalf("newWorkServer failed: %v", err)
		}
		// A request to /api/work/items should fail with 409 FEATURE_DISABLED
		reqItems := httptest.NewRequest(http.MethodGet, "/api/work/items", nil)
		wItems := httptest.NewRecorder()
		srv.handleAPI(wItems, reqItems)
		if wItems.Code != http.StatusConflict {
			t.Fatalf("Expected 409 FEATURE_DISABLED for /api/work/items when work-view/1 is disabled, got %d", wItems.Code)
		}

		// A request to /api/work/artifact/status must NOT be rejected with FEATURE_DISABLED!
		reqArt := httptest.NewRequest(http.MethodGet, "/api/work/artifact/status", nil)
		wArt := httptest.NewRecorder()
		srv.handleAPI(wArt, reqArt)
		if wArt.Code == http.StatusConflict {
			t.Fatalf("Expected /api/work/artifact/status to NOT be blocked by work-view/1 gate, got 409: %s", wArt.Body.String())
		}
		if wArt.Code != http.StatusOK {
			t.Fatalf("Expected 200 OK for /api/work/artifact/status, got %d: %s", wArt.Code, wArt.Body.String())
		}

		_ = s.SetWorkFeatureStatus(ctx, core.SetWorkFeatureStatusRequest{
			FeatureName: "work-view/1",
			Status:      "active",
		})
	})
}
