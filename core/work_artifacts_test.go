package core

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

func setupTestWorkProjectAndItems(t *testing.T, s *Store, ctx context.Context, slug string, count int) (uuid.UUID, []uuid.UUID) {
	t.Helper()
	ws := uuid.New()
	proj := uuid.New()

	p, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      slug,
		Collection:     s.channel.Repo,
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  ws,
		ProjectUUID:    proj,
		ConfigRevision: "rev-test",
	})
	if err != nil {
		t.Fatalf("AddWorkProject failed: %v", err)
	}

	itemUUIDs := make([]uuid.UUID, count)
	for i := 0; i < count; i++ {
		itemUUIDs[i] = uuid.New()
		_, err := s.pool.Exec(ctx, `
			INSERT INTO cairn.work_item (binding_id, plane_item_uuid, identifier, title, state_name, state_group, cache_generation)
			VALUES ($1, $2, $3, $4, 'In Progress', 'started', 1)
		`, p.BindingID, itemUUIDs[i], fmt.Sprintf("%s-%d", strings.ToUpper(slug), i+1), fmt.Sprintf("Item %d for %s", i+1, slug))
		if err != nil {
			t.Fatalf("Insert work item failed: %v", err)
		}
	}
	return p.BindingID, itemUUIDs
}

func enableWorkArtifactFeature(t *testing.T, s *Store, ctx context.Context) {
	t.Helper()
	err := s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
		FeatureName: "work-artifacts/1",
		Status:      "active",
	})
	if err != nil {
		t.Fatalf("Failed to enable work-artifacts/1: %v", err)
	}
}

// AC-12: Many items can link to one revision and one item to many revisions with independent
// input/output/evidence roles; exact duplicate links retry safely.
func TestAC12_ManyItemsAndRevisionsIndependentRoles(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true, Repo: "repo:test"})
	ctx := context.Background()
	enableWorkArtifactFeature(t, s, ctx)

	_, items := setupTestWorkProjectAndItems(t, s, ctx, "ac12-proj", 3)
	itemA := items[0]
	itemB := items[1]

	// 1. Register Git artifact revision
	gitOID := "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
	revGit, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/halbritt/cairn",
		GitCommitOID:    gitOID,
		GitObjectFormat: "sha1",
	})
	if err != nil {
		t.Fatalf("RegisterWorkArtifact git failed: %v", err)
	}

	// 2. Link item A to revGit with role 'input'
	linkA_in, err := s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		ProjectSlug: "ac12-proj",
		ItemUUID:    itemA,
		RevisionID:  revGit.RevisionID,
		Role:        "input",
		Rationale:   "Input spec for task",
	})
	if err != nil {
		t.Fatalf("Link item A (input) failed: %v", err)
	}

	// 3. Link item B to revGit with role 'input' (Many items -> one revision)
	linkB_in, err := s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		ProjectSlug: "ac12-proj",
		ItemUUID:    itemB,
		RevisionID:  revGit.RevisionID,
		Role:        "input",
		Rationale:   "Same input for parallel task",
	})
	if err != nil {
		t.Fatalf("Link item B (input) failed: %v", err)
	}
	if linkA_in.LinkID == linkB_in.LinkID {
		t.Fatal("Distinct items must have distinct link IDs")
	}

	// 4. Link item A to revGit with role 'output' (One item -> multiple roles for same revision)
	linkA_out, err := s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		ProjectSlug: "ac12-proj",
		ItemUUID:    itemA,
		RevisionID:  revGit.RevisionID,
		Role:        "output",
		Rationale:   "Produced artifact",
	})
	if err != nil {
		t.Fatalf("Link item A (output) failed: %v", err)
	}
	if linkA_in.LinkID == linkA_out.LinkID {
		t.Fatal("Distinct roles on same item must have distinct link IDs")
	}

	// 5. Register CI artifact and link item A with role 'evidence' (One item -> many revisions)
	revCI, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection: "repo:test",
		Kind:       "ci",
		CIProvider: "github-actions",
		CIRepo:     "halbritt/cairn",
		CIRunID:    "987654",
		CIAttempt:  1,
		CIStatus:   "success",
	})
	if err != nil {
		t.Fatalf("RegisterWorkArtifact ci failed: %v", err)
	}

	linkA_ev, err := s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		ProjectSlug: "ac12-proj",
		ItemUUID:    itemA,
		RevisionID:  revCI.RevisionID,
		Role:        "evidence",
		Rationale:   "CI verification evidence",
	})
	if err != nil {
		t.Fatalf("Link item A (evidence) failed: %v", err)
	}

	// 6. Verify item A has 3 active links with independent roles
	linksA, err := s.ListWorkArtifactLinks(ctx, ListWorkArtifactLinksRequest{
		ProjectSlug: "ac12-proj",
		ItemUUID:    itemA,
	})
	if err != nil {
		t.Fatalf("ListWorkArtifactLinks failed: %v", err)
	}
	if len(linksA) != 3 {
		t.Fatalf("Expected 3 links for item A, got %d", len(linksA))
	}

	// 7. Exact duplicate link retries safely
	retryLink, err := s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		RequestID:   uuid.NewString(),
		ProjectSlug: "ac12-proj",
		ItemUUID:    itemA,
		RevisionID:  revGit.RevisionID,
		Role:        "input",
	})
	if err != nil {
		t.Fatalf("Duplicate link retry failed: %v", err)
	}
	if retryLink.LinkID != linkA_in.LinkID {
		t.Fatalf("Duplicate link retry should return existing link ID %s, got %s", linkA_in.LinkID, retryLink.LinkID)
	}

	// Verify count is still 3
	linksAAfter, _ := s.ListWorkArtifactLinks(ctx, ListWorkArtifactLinksRequest{
		ProjectSlug: "ac12-proj",
		ItemUUID:    itemA,
	})
	if len(linksAAfter) != 3 {
		t.Fatalf("Expected still 3 links after duplicate retry, got %d", len(linksAAfter))
	}
	_ = linkA_ev
}

// AC-13: Registering a newer revision does not alter prior links, provenance or task state.
// Mutable/unverified report locators are labelled as such.
func TestAC13_RevisionImmutabilityAndReportLocators(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true, Repo: "repo:test"})
	ctx := context.Background()
	enableWorkArtifactFeature(t, s, ctx)

	_, items := setupTestWorkProjectAndItems(t, s, ctx, "ac13-proj", 1)
	itemA := items[0]

	// 1. Report without digest: must be labelled mutable/unverified
	revRep1, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:       "repo:test",
		Kind:             "report",
		ReportLocator:    "https://reports.example.com/coverage/report.html",
		RevisionLabel:    "2026-11-05-v1",
		ReportProvenance: "unverified",
	})
	if err != nil {
		t.Fatalf("Register report 1 failed: %v", err)
	}
	if !revRep1.ReportMutable || revRep1.ReportProvenance != "unverified" {
		t.Fatalf("Report without verified digest must be mutable/unverified; got mutable=%v, prov=%s", revRep1.ReportMutable, revRep1.ReportProvenance)
	}

	// Link item A to revision 1
	link1, err := s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		ProjectSlug: "ac13-proj",
		ItemUUID:    itemA,
		RevisionID:  revRep1.RevisionID,
		Role:        "output",
		Rationale:   "Initial report",
	})
	if err != nil {
		t.Fatalf("Link report 1 failed: %v", err)
	}

	// 2. Register newer revision for same report artifact
	revRep2, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:       "repo:test",
		Kind:             "report",
		ReportLocator:    "https://reports.example.com/coverage/report.html",
		RevisionLabel:    "2026-11-05-v2",
		ReportDigest:     "sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
		ReportProvenance: "asserted",
	})
	if err != nil {
		t.Fatalf("Register report 2 failed: %v", err)
	}
	if revRep2.ArtifactID != revRep1.ArtifactID {
		t.Fatal("Revisions with same report locator must belong to same artifact ID")
	}
	if revRep2.RevisionID == revRep1.RevisionID {
		t.Fatal("Different revision labels must produce distinct revision IDs")
	}
	if !revRep2.ReportMutable || revRep2.ReportProvenance != "asserted" {
		t.Fatalf("Report with asserted digest must be mutable; got mutable=%v, prov=%s", revRep2.ReportMutable, revRep2.ReportProvenance)
	}

	// 3. Verify prior link was NOT altered by registering newer revision
	links, err := s.ListWorkArtifactLinks(ctx, ListWorkArtifactLinksRequest{
		ProjectSlug: "ac13-proj",
		ItemUUID:    itemA,
	})
	if err != nil {
		t.Fatalf("List links failed: %v", err)
	}
	if len(links) != 1 {
		t.Fatalf("Expected 1 link, got %d", len(links))
	}
	if links[0].RevisionID != revRep1.RevisionID {
		t.Fatalf("Prior link should remain pointing to revRep1 (%s), got %s", revRep1.RevisionID, links[0].RevisionID)
	}
	if links[0].AssertedAt != link1.AssertedAt {
		t.Fatal("Prior link asserted_at timestamp was modified")
	}
	if links[0].Rationale != "Initial report" {
		t.Fatal("Prior link rationale was modified")
	}

	// 4. Digest alone with 'asserted' (not 'verified') provenance must still be labelled mutable
	revRepAsserted, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:       "repo:test",
		Kind:             "report",
		ReportLocator:    "https://reports.example.com/other.html",
		RevisionLabel:    "v1",
		ReportDigest:     "sha256:112233445566778899aabbccddeeff00112233445566778899aabbccddeeff00",
		ReportProvenance: "asserted",
	})
	if err != nil {
		t.Fatalf("Register report asserted failed: %v", err)
	}
	if !revRepAsserted.ReportMutable {
		t.Fatal("Digest with 'asserted' provenance must still be labelled mutable")
	}
}

// AC-14: Reverse lookup paginates all visible matches and exposes source timestamps.
// A digest, basename or item display ID cannot merge unrelated identities.
func TestAC14_ReverseImpactPaginationAndIdentitySeparation(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true, Repo: "repo:test"})
	ctx := context.Background()
	enableWorkArtifactFeature(t, s, ctx)

	// Create 2 projects
	_, items1 := setupTestWorkProjectAndItems(t, s, ctx, "ac14-p1", 2)
	_, items2 := setupTestWorkProjectAndItems(t, s, ctx, "ac14-p2", 2)

	// Register 2 Git artifacts in different repositories with the SAME commit OID
	sameOID := "1234567890abcdef1234567890abcdef12345678"
	revRepo1, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org1/project",
		GitCommitOID:    sameOID,
		GitObjectFormat: "sha1",
	})
	if err != nil {
		t.Fatalf("Register git 1 failed: %v", err)
	}

	revRepo2, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org2/project", // Same basename "project"!
		GitCommitOID:    sameOID,                           // Same commit OID!
		GitObjectFormat: "sha1",
	})
	if err != nil {
		t.Fatalf("Register git 2 failed: %v", err)
	}

	if revRepo1.ArtifactID == revRepo2.ArtifactID {
		t.Fatal("Different repository URLs must have distinct artifact IDs despite same commit and basename")
	}
	if revRepo1.RevisionID == revRepo2.RevisionID {
		t.Fatal("Different repository URLs must have distinct revision IDs")
	}

	// Link 3 items across projects to revRepo1
	t1 := time.Now().Add(-10 * time.Minute)
	t2 := time.Now().Add(-5 * time.Minute)
	t3 := time.Now().Add(-1 * time.Minute)

	_, err = s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		ProjectSlug: "ac14-p1",
		ItemUUID:    items1[0],
		RevisionID:  revRepo1.RevisionID,
		Role:        "input",
		AssertedAt:  &t1,
	})
	if err != nil {
		t.Fatalf("Link 1 failed: %v", err)
	}

	_, err = s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		ProjectSlug: "ac14-p1",
		ItemUUID:    items1[1],
		RevisionID:  revRepo1.RevisionID,
		Role:        "output",
		AssertedAt:  &t2,
	})
	if err != nil {
		t.Fatalf("Link 2 failed: %v", err)
	}

	_, err = s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		ProjectSlug: "ac14-p2",
		ItemUUID:    items2[0],
		RevisionID:  revRepo1.RevisionID,
		Role:        "evidence",
		AssertedAt:  &t3,
	})
	if err != nil {
		t.Fatalf("Link 3 failed: %v", err)
	}

	// Link item2[1] to revRepo2
	_, err = s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		ProjectSlug: "ac14-p2",
		ItemUUID:    items2[1],
		RevisionID:  revRepo2.RevisionID,
		Role:        "input",
	})
	if err != nil {
		t.Fatalf("Link repo 2 failed: %v", err)
	}

	// Paginated Reverse Impact for revRepo1: Limit=2
	page1, err := s.ReverseImpact(ctx, ReverseImpactRequest{
		RevisionID: revRepo1.RevisionID,
		Limit:      2,
	})
	if err != nil {
		t.Fatalf("ReverseImpact page 1 failed: %v", err)
	}
	if len(page1.Impacts) != 2 {
		t.Fatalf("Expected 2 impacts in page 1, got %d", len(page1.Impacts))
	}
	if !page1.More || page1.NextCursor == "" {
		t.Fatal("Expected More=true and populated NextCursor on page 1")
	}
	if page1.Impacts[0].ItemIdentifier != "AC14-P1-1" {
		t.Fatalf("Expected first impact to be AC14-P1-1, got %s", page1.Impacts[0].ItemIdentifier)
	}
	if page1.Impacts[1].ItemIdentifier != "AC14-P1-2" {
		t.Fatalf("Expected second impact to be AC14-P1-2, got %s", page1.Impacts[1].ItemIdentifier)
	}

	// Page 2 using NextCursor
	page2, err := s.ReverseImpact(ctx, ReverseImpactRequest{
		RevisionID: revRepo1.RevisionID,
		Limit:      2,
		Cursor:     page1.NextCursor,
	})
	if err != nil {
		t.Fatalf("ReverseImpact page 2 failed: %v", err)
	}
	if len(page2.Impacts) != 1 {
		t.Fatalf("Expected 1 impact in page 2, got %d", len(page2.Impacts))
	}
	if page2.More {
		t.Fatal("Expected More=false on last page")
	}
	if page2.Impacts[0].ItemIdentifier != "AC14-P2-1" {
		t.Fatalf("Expected third impact to be AC14-P2-1, got %s", page2.Impacts[0].ItemIdentifier)
	}

	// Verify revRepo2 impacts do NOT include revRepo1 items
	pageRepo2, err := s.ReverseImpact(ctx, ReverseImpactRequest{
		RevisionID: revRepo2.RevisionID,
	})
	if err != nil {
		t.Fatalf("ReverseImpact repo 2 failed: %v", err)
	}
	if len(pageRepo2.Impacts) != 1 {
		t.Fatalf("Expected exactly 1 impact for repo 2, got %d", len(pageRepo2.Impacts))
	}
	if pageRepo2.Impacts[0].ItemIdentifier != "AC14-P2-2" {
		t.Fatalf("Expected impact on repo 2 to be AC14-P2-2, got %s", pageRepo2.Impacts[0].ItemIdentifier)
	}

	// Test stale cursor refusal (tampered collection or generation)
	_, err = s.ReverseImpact(ctx, ReverseImpactRequest{
		RevisionID: revRepo1.RevisionID,
		Cursor:     "invalid-base64-cursor",
	})
	if err == nil || Code(err) != "INVALID_REQUEST" {
		t.Fatalf("Expected INVALID_REQUEST for malformed cursor, got %v", err)
	}
}

// AC-15: A source becoming unavailable masks its referenced data; unlink and purge have
// distinct effects and neither deletes the external artifact.
func TestAC15_SourceUnavailableMaskingUnlinkAndPurge(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true, Repo: "repo:test"})
	ctx := context.Background()
	enableWorkArtifactFeature(t, s, ctx)

	bID, items := setupTestWorkProjectAndItems(t, s, ctx, "ac15-proj", 2)
	itemA := items[0]
	itemB := items[1]

	// 1. Create a Cairn memory record
	cairnDraft := Draft{
		Kind: "decision",
		Body: "Secret body content",
		Scope: Scope{
			Repo:   "repo:test",
			TaskID: uuid.NewString(),
			RunID:  uuid.NewString(),
		},
		ClaimType: "self",
	}
	created, err := s.Create(ctx, CreateRequest{
		RequestID: uuid.NewString(),
		Draft:     cairnDraft,
	})
	if err != nil {
		t.Fatalf("Create memory record failed: %v", err)
	}
	recID, err := uuid.Parse(created.RecordID)
	if err != nil {
		t.Fatalf("Parse created record ID failed: %v", err)
	}
	ver := created.Version

	// 2. Register Cairn result artifact
	revCairn, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:    "repo:test",
		Kind:          "cairn_result",
		CairnRecordID: &recID,
		CairnVersion:  &ver,
	})
	if err != nil {
		t.Fatalf("Register cairn result failed: %v", err)
	}

	// 3. Link item A and item B to the Cairn result revision
	linkA, err := s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		ProjectSlug: "ac15-proj",
		ItemUUID:    itemA,
		RevisionID:  revCairn.RevisionID,
		Role:        "evidence",
	})
	if err != nil {
		t.Fatalf("Link A failed: %v", err)
	}

	linkB, err := s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		ProjectSlug: "ac15-proj",
		ItemUUID:    itemB,
		RevisionID:  revCairn.RevisionID,
		Role:        "input",
	})
	if err != nil {
		t.Fatalf("Link B failed: %v", err)
	}

	// 4. Unlink item A: item A's link is removed, item B's link remains active!
	unlinkedA, err := s.UnlinkWorkArtifact(ctx, UnlinkWorkArtifactRequest{
		ProjectSlug:      "ac15-proj",
		ItemUUID:         itemA,
		LinkID:           linkA.LinkID,
		ExpectedRevision: 1,
	})
	if err != nil {
		t.Fatalf("Unlink A failed: %v", err)
	}
	if unlinkedA.State != "removed" {
		t.Fatalf("Expected unlinked state='removed', got %s", unlinkedA.State)
	}

	// Item B link remains active
	linksB, err := s.ListWorkArtifactLinks(ctx, ListWorkArtifactLinksRequest{
		ProjectSlug: "ac15-proj",
		ItemUUID:    itemB,
	})
	if err != nil {
		t.Fatalf("List links B failed: %v", err)
	}
	if len(linksB) != 1 || linksB[0].LinkID != linkB.LinkID || linksB[0].State != "active" {
		t.Fatal("Item B link must remain active after unlinking item A")
	}

	// 5. Simulate source becoming unavailable (forgetting / payload exclusion)
	_, err = s.pool.Exec(ctx, `UPDATE cairn.memory_record SET lifecycle = 'tombstoned' WHERE record_id = $1`, recID)
	if err != nil {
		t.Fatalf("Tombstone record failed: %v", err)
	}

	// Read item B detail: artifact revision must be masked with Unavailable = true
	detailB, err := s.GetWorkItemDetail(ctx, "repo:test", "ac15-proj", itemB.String())
	if err != nil {
		t.Fatalf("GetWorkItemDetail failed: %v", err)
	}
	if len(detailB.Artifacts) != 1 {
		t.Fatalf("Expected 1 artifact in detail, got %d", len(detailB.Artifacts))
	}
	artB := detailB.Artifacts[0].ArtifactRevision
	if artB == nil || !artB.Unavailable {
		t.Fatalf("Expected ArtifactRevision to be marked Unavailable=true after tombstone, got %v", artB)
	}

	// 6. Ordinary deletion refusal (ordinary_delete.go:52-75)
	// Because cairn.work_artifact has a foreign key to memory_record with ON DELETE RESTRICT,
	// ordinary deletion of the memory record will refuse with FORGET_REQUIRED!
	_, err = s.pool.Exec(ctx, `DELETE FROM cairn.memory_record WHERE record_id = $1`, recID)
	if err == nil {
		t.Fatal("Direct deletion of memory_record referenced by work_artifact must fail with foreign key violation")
	}

	// 7. Purge artifact: explicit operator maintenance removes local link/revision rows,
	// but does not delete external deliverable.
	err = s.PurgeWorkArtifact(ctx, PurgeWorkArtifactRequest{
		Collection: "repo:test",
		ArtifactID: revCairn.ArtifactID,
	})
	if err != nil {
		t.Fatalf("PurgeWorkArtifact failed: %v", err)
	}
	_, err = s.GetWorkArtifact(ctx, "repo:test", revCairn.ArtifactID)
	if err == nil || Code(err) != "NOT_FOUND" {
		t.Fatalf("Expected NOT_FOUND after PurgeWorkArtifact, got %v", err)
	}

	_ = bID
}

// AC-16: Malicious schemes, credential-bearing URLs, invalid object IDs and unknown
// source revisions refuse atomically. No registration fetches URLs, runs hooks, executes content or edits Plane.
func TestAC16_ValidationAndAtomicity(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true, Repo: "repo:test"})
	ctx := context.Background()
	enableWorkArtifactFeature(t, s, ctx)

	// 1. Report with non-HTTPS scheme
	_, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:    "repo:test",
		Kind:          "report",
		ReportLocator: "http://insecure.example.com/report.html",
		RevisionLabel: "v1",
	})
	if err == nil || Code(err) != "INVALID_REQUEST" {
		t.Fatalf("Expected INVALID_REQUEST for http scheme, got %v", err)
	}

	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:    "repo:test",
		Kind:          "report",
		ReportLocator: "javascript:alert(1)",
		RevisionLabel: "v1",
	})
	if err == nil || Code(err) != "INVALID_REQUEST" {
		t.Fatalf("Expected INVALID_REQUEST for javascript scheme, got %v", err)
	}

	// 2. Report with user credentials
	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:    "repo:test",
		Kind:          "report",
		ReportLocator: "https://user:password@example.com/report.html",
		RevisionLabel: "v1",
	})
	if err == nil || Code(err) != "INVALID_REQUEST" {
		t.Fatalf("Expected INVALID_REQUEST for credential URL, got %v", err)
	}

	// 3. Git commit with short OID / branch / invalid hex
	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:   "repo:test",
		Kind:         "git",
		GitRepo:      "https://github.com/halbritt/cairn",
		GitCommitOID: "abc123", // 6 chars (abbreviated)
	})
	if err == nil || Code(err) != "INVALID_REQUEST" {
		t.Fatalf("Expected INVALID_REQUEST for short commit OID, got %v", err)
	}

	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:   "repo:test",
		Kind:         "git",
		GitRepo:      "https://github.com/halbritt/cairn",
		GitCommitOID: "main", // branch name
	})
	if err == nil || Code(err) != "INVALID_REQUEST" {
		t.Fatalf("Expected INVALID_REQUEST for branch name as commit OID, got %v", err)
	}

	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:   "repo:test",
		Kind:         "git",
		GitRepo:      "https://github.com/halbritt/cairn",
		GitCommitOID: "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", // 40 non-hex chars
	})
	if err == nil || Code(err) != "INVALID_REQUEST" {
		t.Fatalf("Expected INVALID_REQUEST for non-hex commit OID, got %v", err)
	}

	// 4. Cairn result with invalid / non-positive version
	negVer := -1
	recUUID := uuid.New()
	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:    "repo:test",
		Kind:          "cairn_result",
		CairnRecordID: &recUUID,
		CairnVersion:  &negVer,
	})
	if err == nil || Code(err) != "INVALID_REQUEST" {
		t.Fatalf("Expected INVALID_REQUEST for negative version, got %v", err)
	}

	// 5. Cairn result with unknown / nonexistent record
	posVer := 1
	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:    "repo:test",
		Kind:          "cairn_result",
		CairnRecordID: &recUUID,
		CairnVersion:  &posVer,
	})
	if err == nil || Code(err) != "NOT_FOUND" {
		t.Fatalf("Expected NOT_FOUND for nonexistent Cairn record, got %v", err)
	}

	// 6. Feature disabled check
	err = s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
		FeatureName: "work-artifacts/1",
		Status:      "disabled",
	})
	if err != nil {
		t.Fatalf("Disable feature failed: %v", err)
	}

	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/halbritt/cairn",
		GitCommitOID:    "1111222233334444555566667777888899990000",
		GitObjectFormat: "sha1",
	})
	if err == nil || Code(err) != "FEATURE_DISABLED" {
		t.Fatalf("Expected FEATURE_DISABLED when feature is disabled, got %v", err)
	}
}

func TestART_Identity_TupleAndConflictRefusal(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true, Repo: "repo:test"})
	ctx := context.Background()
	enableWorkArtifactFeature(t, s, ctx)

	// 1. CI canonical key injectivity: colon-containing tuples must not collide
	revA, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection: "repo:test",
		Kind:       "ci",
		CIProvider: "gh:actions",
		CIRepo:     "repo",
		CIRunID:    "1",
		CIAttempt:  1,
	})
	if err != nil {
		t.Fatalf("Register CI A failed: %v", err)
	}

	revB, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection: "repo:test",
		Kind:       "ci",
		CIProvider: "gh",
		CIRepo:     "actions:repo",
		CIRunID:    "1",
		CIAttempt:  1,
	})
	if err != nil {
		t.Fatalf("Register CI B failed: %v", err)
	}

	if revA.ArtifactID == revB.ArtifactID {
		t.Fatalf("Injective CI tuple encoding failure: colon-containing tuples collided into same artifact ID")
	}

	// 2. Exact duplicate retry succeeds idempotently
	revARetry, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection: "repo:test",
		Kind:       "ci",
		CIProvider: "gh:actions",
		CIRepo:     "repo",
		CIRunID:    "1",
		CIAttempt:  1,
	})
	if err != nil {
		t.Fatalf("Exact duplicate retry failed: %v", err)
	}
	if revARetry.RevisionID != revA.RevisionID {
		t.Fatalf("Exact duplicate retry returned different revision ID: %s vs %s", revARetry.RevisionID, revA.RevisionID)
	}

	// 3. Conflicting revision fields must refuse with CONFLICT
	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:    "repo:test",
		Kind:          "ci",
		CIProvider:    "gh:actions",
		CIRepo:        "repo",
		CIRunID:       "1",
		RevisionLabel: "attempt/1",
		CIAttempt:     2,
	})
	if err == nil || Code(err) != "CONFLICT" {
		t.Fatalf("Expected CONFLICT on conflicting CI attempt, got %v", err)
	}

	// Conflicting git commit OID
	revGit, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org/test",
		GitCommitOID:    "1111111111111111111111111111111111111111",
		GitObjectFormat: "sha1",
		RevisionLabel:   "release-v1",
	})
	if err != nil {
		t.Fatalf("Register git failed: %v", err)
	}
	_ = revGit
	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org/test",
		GitCommitOID:    "2222222222222222222222222222222222222222",
		GitObjectFormat: "sha1",
		RevisionLabel:   "release-v1",
	})
	if err == nil || Code(err) != "CONFLICT" {
		t.Fatalf("Expected CONFLICT on conflicting Git commit OID, got %v", err)
	}

	// Conflicting report digest
	revRep, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:       "repo:test",
		Kind:             "report",
		ReportLocator:    "https://reports.org/test.html",
		RevisionLabel:    "2026-v1",
		ReportDigest:     "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
		ReportProvenance: "asserted",
	})
	if err != nil {
		t.Fatalf("Register report failed: %v", err)
	}
	_ = revRep
	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:       "repo:test",
		Kind:             "report",
		ReportLocator:    "https://reports.org/test.html",
		RevisionLabel:    "2026-v1",
		ReportDigest:     "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
		ReportProvenance: "asserted",
	})
	if err == nil || Code(err) != "CONFLICT" {
		t.Fatalf("Expected CONFLICT on conflicting report digest, got %v", err)
	}

	// 4. Report digest format validation
	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:    "repo:test",
		Kind:          "report",
		ReportLocator: "https://reports.org/format.html",
		RevisionLabel: "f1",
		ReportDigest:  "invalid-digest-format",
	})
	if err == nil || Code(err) != "INVALID_REQUEST" {
		t.Fatalf("Expected INVALID_REQUEST for invalid report digest format, got %v", err)
	}

	// 5. Non-operator caller cannot assert verified report provenance
	nonOpStore := testStore(t, Channel{Principal: "user:test", Operator: false, Repo: "repo:test"})
	_, err = nonOpStore.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:       "repo:test",
		Kind:             "report",
		ReportLocator:    "https://reports.org/unauth.html",
		RevisionLabel:    "u1",
		ReportDigest:     "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
		ReportProvenance: "verified",
	})
	if err == nil || Code(err) != "AUTHORITY_DENIED" {
		t.Fatalf("Expected AUTHORITY_DENIED for non-operator asserting verified report provenance, got %v", err)
	}
}

func TestART_Read_RepeatedRolesAndCompleteContext(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true, Repo: "repo:test"})
	ctx := context.Background()
	enableWorkArtifactFeature(t, s, ctx)

	bID, items := setupTestWorkProjectAndItems(t, s, ctx, "art-read-proj", 1)
	itemID := items[0]

	// Add session association
	_, err := s.pool.Exec(ctx, `
		INSERT INTO cairn.work_association (binding_id, plane_item_uuid, target_type, target_id, state, author, created_at)
		VALUES ($1, $2, 'session', 'sess-xyz-99', 'active', 'operator:test', clock_timestamp())
	`, bID, itemID)
	if err != nil {
		t.Fatalf("Insert session association failed: %v", err)
	}

	// Add blocker annotation
	_, err = s.pool.Exec(ctx, `
		INSERT INTO cairn.work_annotation (binding_id, plane_item_uuid, kind, body, state, author, created_at)
		VALUES ($1, $2, 'blocker', 'upstream dependency failed', 'open', 'operator:test', clock_timestamp())
	`, bID, itemID)
	if err != nil {
		t.Fatalf("Insert blocker annotation failed: %v", err)
	}

	// Register artifact revision
	rev, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org/read-test",
		GitCommitOID:    "1234567890123456789012345678901234567890",
		GitObjectFormat: "sha1",
	})
	if err != nil {
		t.Fatalf("Register git artifact failed: %v", err)
	}

	// Link item under role "input"
	t1 := time.Now().Add(-5 * time.Minute)
	_, err = s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		ProjectSlug: "art-read-proj",
		ItemUUID:    itemID,
		RevisionID:  rev.RevisionID,
		Role:        "input",
		AssertedAt:  &t1,
	})
	if err != nil {
		t.Fatalf("Link input failed: %v", err)
	}

	// Link SAME item under role "output"
	t2 := time.Now().Add(-1 * time.Minute)
	_, err = s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		ProjectSlug: "art-read-proj",
		ItemUUID:    itemID,
		RevisionID:  rev.RevisionID,
		Role:        "output",
		AssertedAt:  &t2,
	})
	if err != nil {
		t.Fatalf("Link output failed: %v", err)
	}

	// Run ReverseImpact: must return BOTH roles, and BOTH must have session and blocker context!
	page, err := s.ReverseImpact(ctx, ReverseImpactRequest{
		RevisionID: rev.RevisionID,
	})
	if err != nil {
		t.Fatalf("ReverseImpact failed: %v", err)
	}
	if len(page.Impacts) != 2 {
		t.Fatalf("Expected 2 impacts for repeated roles, got %d", len(page.Impacts))
	}

	for idx, imp := range page.Impacts {
		if len(imp.LinkedSessions) != 1 || imp.LinkedSessions[0] != "sess-xyz-99" {
			t.Fatalf("Impact[%d] role %s missing LinkedSession, got %+v", idx, imp.Role, imp.LinkedSessions)
		}
		if len(imp.OpenBlockers) != 1 || imp.OpenBlockers[0] != "upstream dependency failed" {
			t.Fatalf("Impact[%d] role %s missing OpenBlockers, got %+v", idx, imp.Role, imp.OpenBlockers)
		}
	}
}

func TestART_Privacy_SensitivityChangeAndMasking(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true, Repo: "repo:test"})
	ctx := context.Background()
	enableWorkArtifactFeature(t, s, ctx)

	bID, items := setupTestWorkProjectAndItems(t, s, ctx, "art-priv-proj", 1)
	itemID := items[0]
	_ = bID

	// Create memory record with shareable sensitivity
	created, err := s.Create(ctx, CreateRequest{
		RequestID: uuid.NewString(),
		Draft: Draft{
			Sensitivity: "shareable",
			Kind:        "decision",
			Body:        "Shareable body content",
			Scope: Scope{
				Repo:   "repo:test",
				TaskID: uuid.NewString(),
				RunID:  uuid.NewString(),
			},
			ClaimType: "self",
		},
	})
	if err != nil {
		t.Fatalf("Create memory record failed: %v", err)
	}
	recID, err := uuid.Parse(created.RecordID)
	if err != nil {
		t.Fatalf("Parse created record ID failed: %v", err)
	}
	cVer := created.Version

	// Register as cairn_result
	reqID := uuid.NewString()
	rev, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		RequestID:     reqID,
		Collection:    "repo:test",
		Kind:          "cairn_result",
		DisplayName:   "Shareable Secret Finding",
		CairnRecordID: &recID,
		CairnVersion:  &cVer,
		Metadata:      map[string]any{"source_topic": "security_audit"},
	})
	if err != nil {
		t.Fatalf("Register cairn_result failed: %v", err)
	}

	// Link to work item with rationale and request ID
	linkReqID := uuid.NewString()
	link, err := s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		RequestID:   linkReqID,
		ProjectSlug: "art-priv-proj",
		ItemUUID:    itemID,
		RevisionID:  rev.RevisionID,
		Role:        "evidence",
		Rationale:   "Confidential investigation note",
	})
	if err != nil {
		t.Fatalf("Link cairn_result failed: %v", err)
	}
	_ = link

	// Unlink from work item with request ID
	unlinkReqID := uuid.NewString()
	_, err = s.UnlinkWorkArtifact(ctx, UnlinkWorkArtifactRequest{
		RequestID:        unlinkReqID,
		ProjectSlug:      "art-priv-proj",
		ItemUUID:         itemID,
		LinkID:           link.LinkID,
		ExpectedRevision: link.Revision,
	})
	if err != nil {
		t.Fatalf("Unlink cairn_result failed: %v", err)
	}

	// Forget the memory record and purge deletion effects
	var rootGrantID string
	err = s.pool.QueryRow(ctx, `SELECT grant_id::text FROM cairn.authority_grant WHERE parent_id IS NULL`).Scan(&rootGrantID)
	if errors.Is(err, pgx.ErrNoRows) {
		root, bErr := s.Bootstrap(ctx, BootstrapRequest{"82bf5bce-dc6c-4d03-a49f-1677bdcc9a32", "Install synthetic test operator root"})
		if bErr != nil {
			t.Fatalf("Bootstrap failed: %v", bErr)
		}
		rootGrantID = root.ID
	} else if err != nil {
		t.Fatalf("Query root grant failed: %v", err)
	}
	prev, err := s.PreviewDeletion(ctx, created.RecordID)
	if err != nil {
		t.Fatalf("PreviewDeletion failed: %v", err)
	}
	del, err := s.Forget(ctx, ForgetRequest{
		RequestID:       uuid.NewString(),
		RecordID:        created.RecordID,
		ExpectedVersion: created.Version,
		GrantID:         rootGrantID,
		PreviewID:       prev.PreviewID,
	})
	if err != nil {
		t.Fatalf("Forget failed: %v", err)
	}
	_, err = s.PurgeDeletion(ctx, del.DeletionID)
	if err != nil {
		t.Fatalf("PurgeDeletion failed: %v", err)
	}

	// 1. Fresh show of revision: must show Unavailable=true, Metadata masked (nil), Artifact.DisplayName masked ("")
	revShow, err := s.GetWorkArtifactRevision(ctx, "repo:test", rev.RevisionID)
	if err != nil {
		t.Fatalf("GetWorkArtifactRevision failed: %v", err)
	}
	if !revShow.Unavailable {
		t.Fatal("Expected Unavailable=true for forgotten referenced record")
	}
	if revShow.Metadata != nil {
		t.Fatalf("Expected Metadata to be masked (nil), got %+v", revShow.Metadata)
	}
	if revShow.Artifact != nil && revShow.Artifact.DisplayName != "" {
		t.Fatalf("Expected DisplayName to be masked (''), got %s", revShow.Artifact.DisplayName)
	}

	// 2. Fresh show of artifact: DisplayName masked ("")
	artShow, err := s.GetWorkArtifact(ctx, "repo:test", rev.ArtifactID)
	if err != nil {
		t.Fatalf("GetWorkArtifact failed: %v", err)
	}
	if artShow.DisplayName != "" {
		t.Fatalf("Expected DisplayName to be masked ('') in GetWorkArtifact, got %s", artShow.DisplayName)
	}

	// 3. Show by canonical key: DisplayName masked ("")
	artByKey, err := s.GetWorkArtifactByCanonicalKey(ctx, "repo:test", artShow.CanonicalKey)
	if err != nil {
		t.Fatalf("GetWorkArtifactByCanonicalKey failed: %v", err)
	}
	if artByKey.DisplayName != "" {
		t.Fatalf("Expected DisplayName to be masked ('') in GetWorkArtifactByCanonicalKey, got %s", artByKey.DisplayName)
	}

	// 4. List links: rationale and metadata must be masked
	links, err := s.ListWorkArtifactLinks(ctx, ListWorkArtifactLinksRequest{
		ProjectSlug:    "art-priv-proj",
		ItemUUID:       itemID,
		IncludeRemoved: true,
	})
	if err != nil {
		t.Fatalf("ListWorkArtifactLinks failed: %v", err)
	}
	if len(links) != 1 {
		t.Fatalf("Expected 1 link, got %d", len(links))
	}
	if links[0].Rationale != "" {
		t.Fatalf("Expected link rationale to be masked, got %s", links[0].Rationale)
	}
	if links[0].ArtifactRevision == nil || !links[0].ArtifactRevision.Unavailable {
		t.Fatal("Expected link ArtifactRevision Unavailable=true")
	}

	// 5. ReverseImpact: revision must be masked
	page, err := s.ReverseImpact(ctx, ReverseImpactRequest{
		RevisionID: rev.RevisionID,
	})
	if err != nil {
		t.Fatalf("ReverseImpact failed: %v", err)
	}
	if !page.Revision.Unavailable {
		t.Fatal("Expected ReverseImpact Revision.Unavailable=true")
	}

	// 6. Cached-reply scrubbing / live disclosure:
	// Exact retry of registration: returns PAYLOAD_UNAVAILABLE
	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		RequestID:     reqID,
		Collection:    "repo:test",
		Kind:          "cairn_result",
		DisplayName:   "Shareable Secret Finding",
		CairnRecordID: &recID,
		CairnVersion:  &cVer,
		Metadata:      map[string]any{"source_topic": "security_audit"},
	})
	if err == nil || Code(err) != "PAYLOAD_UNAVAILABLE" {
		t.Fatalf("Expected PAYLOAD_UNAVAILABLE on exact register retry after forget, got: %v", err)
	}

	// Exact retry of link: returns PAYLOAD_UNAVAILABLE
	_, err = s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		RequestID:   linkReqID,
		ProjectSlug: "art-priv-proj",
		ItemUUID:    itemID,
		RevisionID:  rev.RevisionID,
		Role:        "evidence",
		Rationale:   "Confidential investigation note",
	})
	if err == nil || Code(err) != "PAYLOAD_UNAVAILABLE" {
		t.Fatalf("Expected PAYLOAD_UNAVAILABLE on exact link retry after forget, got: %v", err)
	}

	// Exact retry of unlink: returns PAYLOAD_UNAVAILABLE
	_, err = s.UnlinkWorkArtifact(ctx, UnlinkWorkArtifactRequest{
		RequestID:        unlinkReqID,
		ProjectSlug:      "art-priv-proj",
		ItemUUID:         itemID,
		LinkID:           link.LinkID,
		ExpectedRevision: link.Revision,
	})
	if err == nil || Code(err) != "PAYLOAD_UNAVAILABLE" {
		t.Fatalf("Expected PAYLOAD_UNAVAILABLE on exact unlink retry after forget, got: %v", err)
	}

	// Altered retry of registration: returns IDEMPOTENCY_CONFLICT
	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		RequestID:       reqID,
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org/other",
		GitCommitOID:    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
		GitObjectFormat: "sha1",
	})
	if err == nil || Code(err) != "IDEMPOTENCY_CONFLICT" {
		t.Fatalf("Expected IDEMPOTENCY_CONFLICT on altered register retry, got: %v", err)
	}
}

func TestART_Purge_BackupRestoreExclusionFence(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true, Repo: "repo:test"})
	ctx := context.Background()
	enableWorkArtifactFeature(t, s, ctx)

	bID, items := setupTestWorkProjectAndItems(t, s, ctx, "art-purge-proj", 1)
	itemID := items[0]
	_ = bID

	// Register an artifact
	rev, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org/purge-test",
		GitCommitOID:    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
		GitObjectFormat: "sha1",
	})
	if err != nil {
		t.Fatalf("Register failed: %v", err)
	}

	// Link artifact to item
	_, err = s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		Collection:  "repo:test",
		ProjectSlug: "art-purge-proj",
		ItemUUID:    itemID,
		RevisionID:  rev.RevisionID,
		Role:        "output",
	})
	if err != nil {
		t.Fatalf("Link failed: %v", err)
	}

	// 1. Take pre-purge backup using pg_dump
	dumpFile, err := os.CreateTemp("", "cairn-pre-purge-*.sql")
	if err != nil {
		t.Fatalf("Create temp dump file failed: %v", err)
	}
	defer os.Remove(dumpFile.Name())
	dumpFile.Close()

	connConfig := s.pool.Config().ConnConfig
	cmd := exec.CommandContext(ctx, "pg_dump",
		"-h", connConfig.Host,
		fmt.Sprintf("-p%d", connConfig.Port),
		"-d", connConfig.Database,
		"--data-only",
		"--column-inserts",
		"--on-conflict-do-nothing",
		"-t", "cairn.work_artifact",
		"-t", "cairn.work_artifact_revision",
		"-t", "cairn.work_artifact_link",
		"-f", dumpFile.Name(),
	)
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("pg_dump failed: %v: %s", err, string(out))
	}

	// 2. Purge the artifact
	purgeReqID := uuid.NewString()
	err = s.PurgeWorkArtifact(ctx, PurgeWorkArtifactRequest{
		RequestID:  purgeReqID,
		Collection: "repo:test",
		ArtifactID: rev.ArtifactID,
	})
	if err != nil {
		t.Fatalf("PurgeWorkArtifact failed: %v", err)
	}

	// Verify artifact is gone
	_, err = s.GetWorkArtifact(ctx, "repo:test", rev.ArtifactID)
	if err == nil || Code(err) != "NOT_FOUND" {
		t.Fatalf("Expected NOT_FOUND after purge, got %v", err)
	}

	// Verify durable exclusion fence exists
	var fenced bool
	err = s.pool.QueryRow(ctx, `
		SELECT true FROM cairn.work_artifact_purge_fence
		WHERE collection = 'repo:test' AND artifact_id = $1
	`, rev.ArtifactID).Scan(&fenced)
	if err != nil || !fenced {
		t.Fatalf("Expected work_artifact_purge_fence entry, got %v", err)
	}

	// 3. Restore pre-purge dump via psql (simulating database restore bringing back purged rows)
	restoreCmd := exec.CommandContext(ctx, "psql",
		"-h", connConfig.Host,
		fmt.Sprintf("-p%d", connConfig.Port),
		"-d", connConfig.Database,
		"-v", "ON_ERROR_STOP=1",
		"-f", dumpFile.Name(),
	)
	if out, err := restoreCmd.CombinedOutput(); err != nil {
		t.Fatalf("psql restore failed: %v: %s", err, string(out))
	}

	// Positively assert the purged artifact was revived by restore before fencing
	var revivedID uuid.UUID
	err = s.pool.QueryRow(ctx, `SELECT artifact_id FROM cairn.work_artifact WHERE artifact_id = $1`, rev.ArtifactID).Scan(&revivedID)
	if err != nil || revivedID != rev.ArtifactID {
		t.Fatalf("Expected artifact to be revived before fencing, got %v", err)
	}

	// 4. Trigger restore recovery fence (which calls ReapplyExclusions)
	unscopedStore := testStore(t, Channel{Principal: "operator:test", Operator: true, Repo: ""})
	_, err = unscopedStore.FenceRestore(ctx, RestoreFenceRequest{
		RequestID: uuid.NewString(),
		Reason:    "reapplying exclusions after test restore",
	})
	if err != nil {
		t.Fatalf("FenceRestore failed: %v", err)
	}

	// 5. Verify the purged artifact and its links were eliminated by ReapplyExclusions!
	_, err = s.GetWorkArtifact(ctx, "repo:test", rev.ArtifactID)
	if err == nil || Code(err) != "NOT_FOUND" {
		t.Fatalf("Expected NOT_FOUND after restore + fence recovery, got %v", err)
	}

	// 6. Verify replay / re-registration is refused by the exclusion fence!
	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org/purge-test",
		GitCommitOID:    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
		GitObjectFormat: "sha1",
	})
	if err == nil || Code(err) != "INVALID_REQUEST" {
		t.Fatalf("Expected INVALID_REQUEST attempting to re-register purged artifact, got %v", err)
	}
}

func TestART_Admission_ProjectStatusAndConcurrency(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true, Repo: "repo:test"})
	ctx := context.Background()
	enableWorkArtifactFeature(t, s, ctx)

	bID, items := setupTestWorkProjectAndItems(t, s, ctx, "art-admiss-proj", 1)
	itemID := items[0]

	// Register an artifact
	rev, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org/admiss-test",
		GitCommitOID:    "cccccccccccccccccccccccccccccccccccccccc",
		GitObjectFormat: "sha1",
	})
	if err != nil {
		t.Fatalf("Register git artifact failed: %v", err)
	}

	// Disable the project
	_, err = s.pool.Exec(ctx, `UPDATE cairn.work_project SET status = 'disabled' WHERE binding_id = $1`, bID)
	if err != nil {
		t.Fatalf("Disable project failed: %v", err)
	}

	// Linking to a disabled project must refuse with PROJECT_DISABLED
	_, err = s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		ProjectSlug: "art-admiss-proj",
		ItemUUID:    itemID,
		RevisionID:  rev.RevisionID,
		Role:        "input",
	})
	if err == nil || Code(err) != "PROJECT_DISABLED" {
		t.Fatalf("Expected PROJECT_DISABLED when linking to disabled project, got %v", err)
	}

	// Re-enable project
	_, err = s.pool.Exec(ctx, `UPDATE cairn.work_project SET status = 'active' WHERE binding_id = $1`, bID)
	if err != nil {
		t.Fatalf("Enable project failed: %v", err)
	}

	// Concurrent duplicate linking: 10 concurrent goroutines linking the same item & revision
	concurrency := 10
	var wg sync.WaitGroup
	errs := make([]error, concurrency)
	links := make([]WorkArtifactLink, concurrency)

	for i := 0; i < concurrency; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			l, e := s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
				ProjectSlug: "art-admiss-proj",
				ItemUUID:    itemID,
				RevisionID:  rev.RevisionID,
				Role:        "input",
			})
			errs[idx] = e
			links[idx] = l
		}(i)
	}
	wg.Wait()

	for idx, e := range errs {
		if e != nil {
			t.Fatalf("Concurrent link[%d] failed: %v", idx, e)
		}
		if links[idx].LinkID != links[0].LinkID {
			t.Fatalf("Concurrent links produced inconsistent link IDs: %s vs %s", links[idx].LinkID, links[0].LinkID)
		}
	}

	// Verify only 1 history entry was recorded for creation
	var histCount int
	err = s.pool.QueryRow(ctx, `
		SELECT count(*) FROM cairn.work_artifact_link_history
		WHERE link_id = $1
	`, links[0].LinkID).Scan(&histCount)
	if err != nil || histCount != 1 {
		t.Fatalf("Expected exactly 1 link history record for idempotent concurrent creation, got %d (err: %v)", histCount, err)
	}
}

func TestART_OperatorBoundary(t *testing.T) {
	s := testStore(t, Channel{Principal: "user:non-operator", Operator: false, Repo: "repo:test"})
	ctx := context.Background()

	dummyUUID := uuid.New()

	// 1. GetWorkArtifact
	_, err := s.GetWorkArtifact(ctx, "repo:test", dummyUUID)
	if err == nil || Code(err) != "AUTHORITY_DENIED" {
		t.Fatalf("Expected AUTHORITY_DENIED on GetWorkArtifact, got: %v", err)
	}

	// 2. GetWorkArtifactByCanonicalKey
	_, err = s.GetWorkArtifactByCanonicalKey(ctx, "repo:test", "git:github.com/test/repo:abcdef")
	if err == nil || Code(err) != "AUTHORITY_DENIED" {
		t.Fatalf("Expected AUTHORITY_DENIED on GetWorkArtifactByCanonicalKey, got: %v", err)
	}

	// 3. GetWorkArtifactRevision
	_, err = s.GetWorkArtifactRevision(ctx, "repo:test", dummyUUID)
	if err == nil || Code(err) != "AUTHORITY_DENIED" {
		t.Fatalf("Expected AUTHORITY_DENIED on GetWorkArtifactRevision, got: %v", err)
	}

	// 4. ListWorkArtifactLinks
	_, err = s.ListWorkArtifactLinks(ctx, ListWorkArtifactLinksRequest{
		Collection:  "repo:test",
		ProjectSlug: "test-proj",
		ItemUUID:    dummyUUID,
	})
	if err == nil || Code(err) != "AUTHORITY_DENIED" {
		t.Fatalf("Expected AUTHORITY_DENIED on ListWorkArtifactLinks, got: %v", err)
	}

	// 5. ReverseImpact
	_, err = s.ReverseImpact(ctx, ReverseImpactRequest{
		Collection: "repo:test",
		RevisionID: dummyUUID,
	})
	if err == nil || Code(err) != "AUTHORITY_DENIED" {
		t.Fatalf("Expected AUTHORITY_DENIED on ReverseImpact, got: %v", err)
	}

	// 6. GetWorkArtifactLinkHistory
	_, err = s.GetWorkArtifactLinkHistory(ctx, GetWorkArtifactLinkHistoryRequest{
		Collection:  "repo:test",
		ProjectSlug: "test-proj",
		ItemUUID:    dummyUUID,
		LinkID:      dummyUUID,
	})
	if err == nil || Code(err) != "AUTHORITY_DENIED" {
		t.Fatalf("Expected AUTHORITY_DENIED on GetWorkArtifactLinkHistory, got: %v", err)
	}
}

func TestART_MaintenanceWhenDisabled(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true, Repo: "repo:test"})
	ctx := context.Background()
	enableWorkArtifactFeature(t, s, ctx)

	bID, items := setupTestWorkProjectAndItems(t, s, ctx, "art-maint-proj", 2)
	itemID := items[0]
	wrongItemID := items[1]
	_ = bID

	// 1. Register and link while active
	rev, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org/maint-test",
		GitCommitOID:    "1111111111111111111111111111111111111111",
		GitObjectFormat: "sha1",
	})
	if err != nil {
		t.Fatalf("RegisterWorkArtifact failed: %v", err)
	}

	link, err := s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		ProjectSlug: "art-maint-proj",
		ItemUUID:    itemID,
		RevisionID:  rev.RevisionID,
		Role:        "output",
		Rationale:   "Initial deliverable",
	})
	if err != nil {
		t.Fatalf("LinkWorkArtifact failed: %v", err)
	}

	// 2. Disable work-artifacts/1 feature
	err = s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
		FeatureName: "work-artifacts/1",
		Status:      "disabled",
	})
	if err != nil {
		t.Fatalf("SetWorkFeatureStatus to disabled failed: %v", err)
	}

	// 3. Assert retained-state read routes succeed while disabled
	artShow, err := s.GetWorkArtifact(ctx, "repo:test", rev.ArtifactID)
	if err != nil {
		t.Fatalf("GetWorkArtifact failed when disabled: %v", err)
	}
	if artShow.ArtifactID != rev.ArtifactID {
		t.Fatalf("Unexpected artifact ID: %s", artShow.ArtifactID)
	}

	artByKey, err := s.GetWorkArtifactByCanonicalKey(ctx, "repo:test", artShow.CanonicalKey)
	if err != nil {
		t.Fatalf("GetWorkArtifactByCanonicalKey failed when disabled: %v", err)
	}
	if artByKey.ArtifactID != rev.ArtifactID {
		t.Fatalf("Unexpected artifact ID from canonical key: %s", artByKey.ArtifactID)
	}

	revShow, err := s.GetWorkArtifactRevision(ctx, "repo:test", rev.RevisionID)
	if err != nil {
		t.Fatalf("GetWorkArtifactRevision failed when disabled: %v", err)
	}
	if revShow.RevisionID != rev.RevisionID {
		t.Fatalf("Unexpected revision ID: %s", revShow.RevisionID)
	}

	links, err := s.ListWorkArtifactLinks(ctx, ListWorkArtifactLinksRequest{
		ProjectSlug: "art-maint-proj",
		ItemUUID:    itemID,
	})
	if err != nil {
		t.Fatalf("ListWorkArtifactLinks failed when disabled: %v", err)
	}
	if len(links) != 1 || links[0].LinkID != link.LinkID {
		t.Fatalf("Unexpected links returned when disabled: %+v", links)
	}

	page, err := s.ReverseImpact(ctx, ReverseImpactRequest{
		RevisionID: rev.RevisionID,
	})
	if err != nil {
		t.Fatalf("ReverseImpact failed when disabled: %v", err)
	}
	if len(page.Impacts) != 1 || page.Impacts[0].LinkID != link.LinkID {
		t.Fatalf("Unexpected impacts returned when disabled: %+v", page.Impacts)
	}

	hist, err := s.GetWorkArtifactLinkHistory(ctx, GetWorkArtifactLinkHistoryRequest{
		ProjectSlug: "art-maint-proj",
		ItemUUID:    itemID,
		LinkID:      link.LinkID,
	})
	if err != nil {
		t.Fatalf("GetWorkArtifactLinkHistory failed when disabled: %v", err)
	}
	if len(hist.History) != 1 {
		t.Fatalf("Expected 1 history entry, got %d", len(hist.History))
	}

	// 4. Assert new admissions are refused while disabled
	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org/another",
		GitCommitOID:    "2222222222222222222222222222222222222222",
		GitObjectFormat: "sha1",
	})
	if err == nil || Code(err) != "FEATURE_DISABLED" {
		t.Fatalf("Expected FEATURE_DISABLED on RegisterWorkArtifact when disabled, got: %v", err)
	}

	_, err = s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		ProjectSlug: "art-maint-proj",
		ItemUUID:    itemID,
		RevisionID:  rev.RevisionID,
		Role:        "evidence",
	})
	if err == nil || Code(err) != "FEATURE_DISABLED" {
		t.Fatalf("Expected FEATURE_DISABLED on LinkWorkArtifact when disabled, got: %v", err)
	}

	// 5. Assert UnlinkWorkArtifact rejects stale/wrong-item guards
	// Stale expected_revision:
	_, err = s.UnlinkWorkArtifact(ctx, UnlinkWorkArtifactRequest{
		ProjectSlug:      "art-maint-proj",
		ItemUUID:         itemID,
		LinkID:           link.LinkID,
		ExpectedRevision: 999,
	})
	if err == nil || Code(err) != "VERSION_CONFLICT" {
		t.Fatalf("Expected VERSION_CONFLICT for stale expected_revision, got: %v", err)
	}

	// Wrong item:
	_, err = s.UnlinkWorkArtifact(ctx, UnlinkWorkArtifactRequest{
		ProjectSlug:      "art-maint-proj",
		ItemUUID:         wrongItemID,
		LinkID:           link.LinkID,
		ExpectedRevision: link.Revision,
	})
	if err == nil || Code(err) != "NOT_FOUND" {
		t.Fatalf("Expected NOT_FOUND for wrong item UUID, got: %v", err)
	}

	// 6. Assert UnlinkWorkArtifact SUCCEEDS with valid expected_revision while disabled
	unlinked, err := s.UnlinkWorkArtifact(ctx, UnlinkWorkArtifactRequest{
		ProjectSlug:      "art-maint-proj",
		ItemUUID:         itemID,
		LinkID:           link.LinkID,
		ExpectedRevision: link.Revision,
	})
	if err != nil {
		t.Fatalf("UnlinkWorkArtifact failed during permitted maintenance when disabled: %v", err)
	}
	if unlinked.State != "removed" || unlinked.Revision != link.Revision+1 {
		t.Fatalf("Unexpected unlinked state: %+v", unlinked)
	}

	// 7. Assert PurgeWorkArtifact succeeds while disabled
	err = s.PurgeWorkArtifact(ctx, PurgeWorkArtifactRequest{
		RequestID:  uuid.NewString(),
		Collection: "repo:test",
		ArtifactID: rev.ArtifactID,
	})
	if err != nil {
		t.Fatalf("PurgeWorkArtifact failed when disabled: %v", err)
	}
}

func TestART_CollectionIsolation_PurgeAndRecovery(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true, Repo: ""})
	ctx := context.Background()
	enableWorkArtifactFeature(t, s, ctx)

	reqIDA := uuid.NewString()
	revA, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		RequestID:        reqIDA,
		Collection:       "collection-a",
		Kind:             "report",
		ReportLocator:    "https://example.com/reports/common-audit.html",
		ReportProvenance: "asserted",
		RevisionLabel:    "v1.0.0",
	})
	if err != nil {
		t.Fatalf("Register in collection-a failed: %v", err)
	}

	reqIDB := uuid.NewString()
	revB, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		RequestID:        reqIDB,
		Collection:       "collection-b",
		Kind:             "report",
		ReportLocator:    "https://example.com/reports/common-audit.html",
		ReportProvenance: "asserted",
		RevisionLabel:    "v1.0.0",
	})
	if err != nil {
		t.Fatalf("Register in collection-b failed: %v", err)
	}

	// Purge collection-a
	purgeReqID := uuid.NewString()
	err = s.PurgeWorkArtifact(ctx, PurgeWorkArtifactRequest{
		RequestID:  purgeReqID,
		Collection: "collection-a",
		ArtifactID: revA.ArtifactID,
	})
	if err != nil {
		t.Fatalf("PurgeWorkArtifact in collection-a failed: %v", err)
	}

	// collection-a is gone
	_, err = s.GetWorkArtifact(ctx, "collection-a", revA.ArtifactID)
	if err == nil || Code(err) != "NOT_FOUND" {
		t.Fatalf("Expected NOT_FOUND for collection-a artifact, got %v", err)
	}

	// collection-a cached retry returns PAYLOAD_UNAVAILABLE
	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		RequestID:        reqIDA,
		Collection:       "collection-a",
		Kind:             "report",
		ReportLocator:    "https://example.com/reports/common-audit.html",
		ReportProvenance: "asserted",
		RevisionLabel:    "v1.0.0",
	})
	if err == nil || Code(err) != "PAYLOAD_UNAVAILABLE" {
		t.Fatalf("Expected PAYLOAD_UNAVAILABLE for collection-a retry, got %v", err)
	}

	// collection-b artifact still exists
	artB, err := s.GetWorkArtifact(ctx, "collection-b", revB.ArtifactID)
	if err != nil {
		t.Fatalf("collection-b artifact missing after collection-a purge: %v", err)
	}
	if artB.ArtifactID != revB.ArtifactID {
		t.Fatalf("collection-b artifact ID mismatch: %s vs %s", artB.ArtifactID, revB.ArtifactID)
	}

	// collection-b cached retry still succeeds idempotently
	revBRetry, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		RequestID:        reqIDB,
		Collection:       "collection-b",
		Kind:             "report",
		ReportLocator:    "https://example.com/reports/common-audit.html",
		ReportProvenance: "asserted",
		RevisionLabel:    "v1.0.0",
	})
	if err != nil {
		t.Fatalf("collection-b retry failed: %v", err)
	}
	if revBRetry.RevisionID != revB.RevisionID {
		t.Fatalf("collection-b retry returned different revision ID: %s vs %s", revBRetry.RevisionID, revB.RevisionID)
	}

	// ReapplyExclusions must leave collection-b untouched
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatalf("Begin tx failed: %v", err)
	}
	defer tx.Rollback(ctx)
	if err := ReapplyExclusions(ctx, tx); err != nil {
		t.Fatalf("ReapplyExclusions failed: %v", err)
	}
	if err := tx.Commit(ctx); err != nil {
		t.Fatalf("Commit failed: %v", err)
	}

	// Verify collection-b cached mutation_request response is still intact
	var bResponse []byte
	err = s.pool.QueryRow(ctx, `SELECT response FROM cairn.mutation_request WHERE request_id = $1`, reqIDB).Scan(&bResponse)
	if err != nil || bResponse == nil {
		t.Fatalf("collection-b mutation_request response was incorrectly purged: %v", err)
	}
}

func TestART_CachedRetry_ActiveAndDisabled_PurgeIdempotency(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true, Repo: "repo:test"})
	ctx := context.Background()
	enableWorkArtifactFeature(t, s, ctx)

	reqID := uuid.NewString()
	rev, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		RequestID:       reqID,
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org/retry-test",
		GitCommitOID:    "3333333333333333333333333333333333333333",
		GitObjectFormat: "sha1",
	})
	if err != nil {
		t.Fatalf("Register failed: %v", err)
	}

	// Exact retry when active: returns original
	retryActive, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		RequestID:       reqID,
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org/retry-test",
		GitCommitOID:    "3333333333333333333333333333333333333333",
		GitObjectFormat: "sha1",
	})
	if err != nil || retryActive.RevisionID != rev.RevisionID {
		t.Fatalf("Exact retry active failed: %v", err)
	}

	// Changed intent when active: returns IDEMPOTENCY_CONFLICT
	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		RequestID:       reqID,
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org/retry-test-changed",
		GitCommitOID:    "4444444444444444444444444444444444444444",
		GitObjectFormat: "sha1",
	})
	if err == nil || Code(err) != "IDEMPOTENCY_CONFLICT" {
		t.Fatalf("Expected IDEMPOTENCY_CONFLICT, got: %v", err)
	}

	// Disable feature
	err = s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
		FeatureName: "work-artifacts/1",
		Status:      "disabled",
	})
	if err != nil {
		t.Fatalf("Disable feature failed: %v", err)
	}

	// Exact retry when disabled: still returns original cached replay!
	retryDisabled, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		RequestID:       reqID,
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org/retry-test",
		GitCommitOID:    "3333333333333333333333333333333333333333",
		GitObjectFormat: "sha1",
	})
	if err != nil || retryDisabled.RevisionID != rev.RevisionID {
		t.Fatalf("Exact retry when disabled failed: %v", err)
	}

	// Changed intent when disabled: still returns IDEMPOTENCY_CONFLICT!
	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		RequestID:       reqID,
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org/retry-test-changed",
		GitCommitOID:    "4444444444444444444444444444444444444444",
		GitObjectFormat: "sha1",
	})
	if err == nil || Code(err) != "IDEMPOTENCY_CONFLICT" {
		t.Fatalf("Expected IDEMPOTENCY_CONFLICT when disabled, got: %v", err)
	}

	// Purge artifact while disabled
	purgeReqID := uuid.NewString()
	err = s.PurgeWorkArtifact(ctx, PurgeWorkArtifactRequest{
		RequestID:  purgeReqID,
		Collection: "repo:test",
		ArtifactID: rev.ArtifactID,
	})
	if err != nil {
		t.Fatalf("PurgeWorkArtifact failed: %v", err)
	}

	// Idempotent retry of purge: succeeds!
	err = s.PurgeWorkArtifact(ctx, PurgeWorkArtifactRequest{
		RequestID:  purgeReqID,
		Collection: "repo:test",
		ArtifactID: rev.ArtifactID,
	})
	if err != nil {
		t.Fatalf("Idempotent retry of purge failed: %v", err)
	}

	// Changed intent on purge retry: IDEMPOTENCY_CONFLICT
	err = s.PurgeWorkArtifact(ctx, PurgeWorkArtifactRequest{
		RequestID:  purgeReqID,
		Collection: "repo:test",
		ArtifactID: uuid.New(),
	})
	if err == nil || Code(err) != "IDEMPOTENCY_CONFLICT" {
		t.Fatalf("Expected IDEMPOTENCY_CONFLICT on purge retry with changed artifact, got: %v", err)
	}

	// Retry original registration after purge: PAYLOAD_UNAVAILABLE
	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		RequestID:       reqID,
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org/retry-test",
		GitCommitOID:    "3333333333333333333333333333333333333333",
		GitObjectFormat: "sha1",
	})
	if err == nil || Code(err) != "PAYLOAD_UNAVAILABLE" {
		t.Fatalf("Expected PAYLOAD_UNAVAILABLE on retry after purge, got: %v", err)
	}

	// Changed intent on registration retry after purge: IDEMPOTENCY_CONFLICT (digest preserved!)
	_, err = s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		RequestID:       reqID,
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org/retry-test-changed",
		GitCommitOID:    "4444444444444444444444444444444444444444",
		GitObjectFormat: "sha1",
	})
	if err == nil || Code(err) != "IDEMPOTENCY_CONFLICT" {
		t.Fatalf("Expected IDEMPOTENCY_CONFLICT on changed retry after purge, got: %v", err)
	}
}

func TestART_Recovery_SurvivingReceiptGapNegative(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true, Repo: ""})
	ctx := context.Background()
	enableWorkArtifactFeature(t, s, ctx)

	reqID := uuid.NewString()
	rev, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		RequestID:       reqID,
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org/gap-test",
		GitCommitOID:    "5555555555555555555555555555555555555555",
		GitObjectFormat: "sha1",
	})
	if err != nil {
		t.Fatalf("Register failed: %v", err)
	}

	// Purge the artifact
	purgeReqID := uuid.NewString()
	err = s.PurgeWorkArtifact(ctx, PurgeWorkArtifactRequest{
		RequestID:  purgeReqID,
		Collection: "repo:test",
		ArtifactID: rev.ArtifactID,
	})
	if err != nil {
		t.Fatalf("Purge failed: %v", err)
	}

	// Capture recovery record while in healthy purged state
	rec, err := s.CaptureRecovery(ctx)
	if err != nil {
		t.Fatalf("CaptureRecovery failed: %v", err)
	}

	// Simulate partial restore: an older unpurged mutation_request response survives while
	// work_artifact is absent and purge_fence is present
	revBytes, _ := json.Marshal(rev)
	_, err = s.pool.Exec(ctx, `
		UPDATE cairn.mutation_request
		SET response = $1, ordinary_deleted = false
		WHERE request_id = $2
	`, revBytes, reqID)
	if err != nil {
		t.Fatalf("Update mutation_request to simulate surviving receipt failed: %v", err)
	}

	// Inspect recovery against current DB: must detect gap ARTIFACT_PURGE_FENCE_MISSING
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatalf("Begin tx failed: %v", err)
	}
	defer tx.Rollback(ctx)
	report, err := s.inspectRecoveryTx(ctx, tx, rec)
	if err != nil {
		t.Fatalf("inspectRecoveryTx failed: %v", err)
	}
	var foundGap bool
	for _, g := range report.Gaps {
		if g.Kind == "work_artifact_purge_fence" && g.SubjectID == rev.ArtifactID.String() && g.Reason == "ARTIFACT_PURGE_FENCE_MISSING" {
			foundGap = true
			break
		}
	}
	if !foundGap {
		t.Fatalf("Expected ARTIFACT_PURGE_FENCE_MISSING gap for surviving cached receipt, got gaps: %+v", report.Gaps)
	}
	_ = tx.Rollback(ctx)

	// Now reapply exclusions via ReapplyExclusions
	tx2, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatalf("Begin tx2 failed: %v", err)
	}
	defer tx2.Rollback(ctx)
	if err := ReapplyExclusions(ctx, tx2); err != nil {
		t.Fatalf("ReapplyExclusions failed: %v", err)
	}
	if err := tx2.Commit(ctx); err != nil {
		t.Fatalf("Commit tx2 failed: %v", err)
	}

	// Verify the surviving receipt was scrubbed
	var resp []byte
	var ordinaryDeleted bool
	err = s.pool.QueryRow(ctx, `SELECT response, ordinary_deleted FROM cairn.mutation_request WHERE request_id = $1`, reqID).Scan(&resp, &ordinaryDeleted)
	if err != nil {
		t.Fatalf("Query mutation_request failed: %v", err)
	}
	if resp != nil || !ordinaryDeleted {
		t.Fatalf("Expected response=NULL and ordinary_deleted=true after ReapplyExclusions, got resp=%s, deleted=%v", resp, ordinaryDeleted)
	}

	// Re-inspect recovery: must now be consistent with 0 gaps!
	tx3, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatalf("Begin tx3 failed: %v", err)
	}
	defer tx3.Rollback(ctx)
	reportClean, err := s.inspectRecoveryTx(ctx, tx3, rec)
	if err != nil {
		t.Fatalf("inspectRecoveryTx after reapply failed: %v", err)
	}
	for _, g := range reportClean.Gaps {
		if g.Kind == "work_artifact_purge_fence" && g.SubjectID == rev.ArtifactID.String() {
			t.Fatalf("Unexpected surviving gap after ReapplyExclusions: %+v", g)
		}
	}
}

func TestART_CairnResult_CurrentScopeChange_ReplayRefusal(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true, Repo: "repo:initial"})
	ctx := context.Background()
	enableWorkArtifactFeature(t, s, ctx)

	bID, items := setupTestWorkProjectAndItems(t, s, ctx, "cairn-scope-proj", 1)
	itemID := items[0]
	_ = bID

	// Create a memory record in collection repo:initial
	created, err := s.Create(ctx, CreateRequest{
		RequestID: uuid.NewString(),
		Draft: Draft{
			Kind: "note",
			Body: "Audit deliverable note",
			Scope: Scope{
				Repo:   "repo:initial",
				TaskID: "task-1",
				RunID:  "run-1",
			},
			Sensitivity: "shareable",
			ClaimType:   "self",
		},
	})
	if err != nil {
		t.Fatalf("Create record failed: %v", err)
	}

	regReqID := uuid.NewString()
	recUUID := uuid.MustParse(created.RecordID)
	regReq := RegisterWorkArtifactRequest{
		RequestID:     regReqID,
		Collection:    "repo:initial",
		Kind:          "cairn_result",
		DisplayName:   "Deliverable Note",
		CairnRecordID: &recUUID,
		CairnVersion:  &created.Version,
	}
	rev, err := s.RegisterWorkArtifact(ctx, regReq)
	if err != nil {
		t.Fatalf("RegisterWorkArtifact failed: %v", err)
	}

	linkReqID := uuid.NewString()
	linkReq := LinkWorkArtifactRequest{
		RequestID:   linkReqID,
		Collection:  "repo:initial",
		ProjectSlug: "cairn-scope-proj",
		ItemUUID:    itemID,
		RevisionID:  rev.RevisionID,
		Role:        "output",
		Rationale:   "Delivered note",
	}
	_, err = s.LinkWorkArtifact(ctx, linkReq)
	if err != nil {
		t.Fatalf("LinkWorkArtifact failed: %v", err)
	}
	// Now move the current version of the memory record to repo:moved
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatalf("Begin failed: %v", err)
	}
	defer tx.Rollback(ctx)
	if _, err := tx.Exec(ctx, "SET LOCAL cairn.caller = 'operator:test'"); err != nil {
		t.Fatalf("SET cairn.caller failed: %v", err)
	}
	if _, err := tx.Exec(ctx, "SET LOCAL cairn.witness = 'testimony'"); err != nil {
		t.Fatalf("SET cairn.witness failed: %v", err)
	}
	_, err = tx.Exec(ctx, `
		INSERT INTO cairn.record_version(record_id, version, kind, body, repo, task_id, run_id, claim_type)
		VALUES($1, 2, 'note', 'Moved note body', 'repo:moved', 't', 'r', 'self')
	`, created.RecordID)
	if err != nil {
		t.Fatalf("Insert version 2 failed: %v", err)
	}
	_, err = tx.Exec(ctx, `
		UPDATE cairn.memory_record
		SET current_version = 2
		WHERE record_id = $1
	`, created.RecordID)
	if err != nil {
		t.Fatalf("Update current_version failed: %v", err)
	}
	if err := tx.Commit(ctx); err != nil {
		t.Fatalf("Commit failed: %v", err)
	}

	// 1. Replaying the original register request_id MUST return AUTHORITY_DENIED
	_, err = s.RegisterWorkArtifact(ctx, regReq)
	if err == nil || Code(err) != "AUTHORITY_DENIED" {
		t.Fatalf("Expected AUTHORITY_DENIED on register replay after scope move, got: %v", err)
	}

	// 2. Replaying the original link request_id MUST return AUTHORITY_DENIED
	_, err = s.LinkWorkArtifact(ctx, linkReq)
	if err == nil || Code(err) != "AUTHORITY_DENIED" {
		t.Fatalf("Expected AUTHORITY_DENIED on link replay after scope move, got: %v", err)
	}

	// 3. Reads show Unavailable = true and mask DisplayName, Metadata, and Rationale
	revShow, err := s.GetWorkArtifactRevision(ctx, "repo:initial", rev.RevisionID)
	if err != nil {
		t.Fatalf("GetWorkArtifactRevision failed: %v", err)
	}
	if !revShow.Unavailable {
		t.Fatal("Expected revShow.Unavailable = true after source scope move")
	}
	if revShow.Artifact != nil && revShow.Artifact.DisplayName != "" {
		t.Fatalf("Expected masked DisplayName, got: %q", revShow.Artifact.DisplayName)
	}

	links, err := s.ListWorkArtifactLinks(ctx, ListWorkArtifactLinksRequest{
		ProjectSlug: "cairn-scope-proj",
		ItemUUID:    itemID,
	})
	if err != nil {
		t.Fatalf("ListWorkArtifactLinks failed: %v", err)
	}
	if len(links) != 1 {
		t.Fatalf("Expected 1 link, got %d", len(links))
	}
	if links[0].ArtifactRevision == nil || !links[0].ArtifactRevision.Unavailable || links[0].Rationale != "" {
		t.Fatalf("Expected link ArtifactRevision.Unavailable=true and Rationale='', got: %+v", links[0])
	}

	impact, err := s.ReverseImpact(ctx, ReverseImpactRequest{
		RevisionID: rev.RevisionID,
	})
	if err != nil {
		t.Fatalf("ReverseImpact failed: %v", err)
	}
	if !impact.Revision.Unavailable {
		t.Fatal("Expected impact.Revision.Unavailable = true after source scope move")
	}
}

func TestART_Unlink_Refusals(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true, Repo: "repo:test"})
	ctx := context.Background()
	enableWorkArtifactFeature(t, s, ctx)

	bID, items := setupTestWorkProjectAndItems(t, s, ctx, "unlink-refusal-proj", 2)
	itemID := items[0]
	wrongItemID := items[1]
	_ = bID

	rev, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		Collection:      "repo:test",
		Kind:            "git",
		GitRepo:         "https://github.com/org/unlink-repo",
		GitCommitOID:    "1111111111111111111111111111111111111111",
		GitObjectFormat: "sha1",
	})
	if err != nil {
		t.Fatalf("Register failed: %v", err)
	}

	link, err := s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		ProjectSlug: "unlink-refusal-proj",
		ItemUUID:    itemID,
		RevisionID:  rev.RevisionID,
		Role:        "output",
	})
	if err != nil {
		t.Fatalf("Link failed: %v", err)
	}

	// 1. Wrong project slug -> NOT_FOUND
	_, err = s.UnlinkWorkArtifact(ctx, UnlinkWorkArtifactRequest{
		ProjectSlug:      "wrong-proj-slug",
		ItemUUID:         itemID,
		LinkID:           link.LinkID,
		ExpectedRevision: link.Revision,
	})
	if err == nil || Code(err) != "NOT_FOUND" {
		t.Fatalf("Expected NOT_FOUND on wrong project slug, got: %v", err)
	}

	// 2. Wrong item UUID -> NOT_FOUND
	_, err = s.UnlinkWorkArtifact(ctx, UnlinkWorkArtifactRequest{
		ProjectSlug:      "unlink-refusal-proj",
		ItemUUID:         wrongItemID,
		LinkID:           link.LinkID,
		ExpectedRevision: link.Revision,
	})
	if err == nil || Code(err) != "NOT_FOUND" {
		t.Fatalf("Expected NOT_FOUND on wrong item UUID, got: %v", err)
	}

	// 3. Stale ExpectedRevision -> VERSION_CONFLICT
	_, err = s.UnlinkWorkArtifact(ctx, UnlinkWorkArtifactRequest{
		ProjectSlug:      "unlink-refusal-proj",
		ItemUUID:         itemID,
		LinkID:           link.LinkID,
		ExpectedRevision: link.Revision + 10,
	})
	if err == nil || Code(err) != "VERSION_CONFLICT" {
		t.Fatalf("Expected VERSION_CONFLICT on stale revision, got: %v", err)
	}

	// 4. Successful unlink
	unlinkReqID := uuid.NewString()
	unlinked, err := s.UnlinkWorkArtifact(ctx, UnlinkWorkArtifactRequest{
		RequestID:        unlinkReqID,
		ProjectSlug:      "unlink-refusal-proj",
		ItemUUID:         itemID,
		LinkID:           link.LinkID,
		ExpectedRevision: link.Revision,
	})
	if err != nil {
		t.Fatalf("Unlink failed: %v", err)
	}
	if unlinked.State != "removed" {
		t.Fatalf("Expected state removed, got: %s", unlinked.State)
	}

	// 5. Exact same request_id retry replays successfully
	replayed, err := s.UnlinkWorkArtifact(ctx, UnlinkWorkArtifactRequest{
		RequestID:        unlinkReqID,
		ProjectSlug:      "unlink-refusal-proj",
		ItemUUID:         itemID,
		LinkID:           link.LinkID,
		ExpectedRevision: link.Revision,
	})
	if err != nil {
		t.Fatalf("Replay of exact unlink request failed: %v", err)
	}
	if replayed.State != "removed" || replayed.Revision != unlinked.Revision {
		t.Fatalf("Replay mismatch: %+v vs %+v", replayed, unlinked)
	}

	// 6. Distinct request_id on already removed link -> VERSION_CONFLICT
	_, err = s.UnlinkWorkArtifact(ctx, UnlinkWorkArtifactRequest{
		RequestID:        uuid.NewString(),
		ProjectSlug:      "unlink-refusal-proj",
		ItemUUID:         itemID,
		LinkID:           link.LinkID,
		ExpectedRevision: unlinked.Revision,
	})
	if err == nil || Code(err) != "VERSION_CONFLICT" {
		t.Fatalf("Expected VERSION_CONFLICT on distinct request_id on removed link, got: %v", err)
	}
}
