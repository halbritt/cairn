package core

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/plane"
)

func TestWorkArtifactAdmissionSerializesProjectDisable(t *testing.T) {
	collection := "repo:artifact-disable:" + uuid.NewString()
	s := testStore(t, Channel{Principal: "operator:test", Operator: true, Repo: collection})
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	enableWorkArtifactFeature(t, s, ctx)
	project, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		RequestID: uuid.NewString(), Collection: collection, LocalSlug: "disable-race",
		PlaneInstance: "work", PlaneOrigin: "https://plane.example.com",
		WorkspaceUUID: uuid.New(), ProjectUUID: uuid.New(), ConfigRevision: "fixture-v1",
	})
	if err != nil {
		t.Fatal(err)
	}
	itemID := uuid.New()
	refresh, err := s.PrepareRefresh(ctx, collection, project.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.CommitRefreshSnapshot(ctx, refresh, []plane.NormalizedItem{{
		PlaneItemUUID: itemID, Identifier: "DISABLE-1", Title: "Project disable race fixture",
	}}, false, nil); err != nil {
		t.Fatal(err)
	}
	artifact, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		RequestID: uuid.NewString(), Collection: collection, Kind: "git",
		GitRepo: "https://example.com/disable-race", GitObjectFormat: "sha1", GitCommitOID: strings.Repeat("b", 40),
	})
	if err != nil {
		t.Fatal(err)
	}

	// A real revision lock pauses LinkWorkArtifact after its project admission
	// read. Observe the database wait before starting the competing disable;
	// elapsed time alone never establishes either side of this race.
	gate, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer gate.Rollback(context.Background())
	var gatePID int
	if err := gate.QueryRow(ctx, `SELECT pg_backend_pid() FROM cairn.work_artifact_revision WHERE revision_id = $1 FOR UPDATE`, artifact.RevisionID).Scan(&gatePID); err != nil {
		t.Fatal(err)
	}
	request := LinkWorkArtifactRequest{
		RequestID: uuid.NewString(), Collection: collection, ProjectSlug: project.LocalSlug,
		ItemUUID: itemID, RevisionID: artifact.RevisionID, Role: "input",
	}
	type linkResult struct {
		link WorkArtifactLink
		err  error
	}
	linked := make(chan linkResult, 1)
	go func() {
		link, err := s.LinkWorkArtifact(ctx, request)
		linked <- linkResult{link, err}
	}()
	var linkPID int
	for linkPID == 0 {
		if err := s.pool.QueryRow(ctx, `SELECT COALESCE((SELECT pid FROM pg_stat_activity
			WHERE datname = current_database() AND $1::integer = ANY(pg_blocking_pids(pid)) LIMIT 1), 0)`, gatePID).Scan(&linkPID); err != nil {
			t.Fatal(err)
		}
		if linkPID != 0 {
			break
		}
		select {
		case result := <-linked:
			t.Fatalf("link finished before reaching the revision gate: %v", result.err)
		case <-ctx.Done():
			t.Fatal("link did not reach the revision gate: ", ctx.Err())
		case <-time.After(5 * time.Millisecond):
		}
	}
	type disableResult struct {
		project WorkProject
		err     error
	}
	disabled := make(chan disableResult, 1)
	go func() {
		p, err := s.DisableWorkProject(ctx, collection, project.LocalSlug, uuid.NewString())
		disabled <- disableResult{p, err}
	}()
	var resultDisable disableResult
	disableFinishedBeforeRelease := false
	for {
		select {
		case resultDisable = <-disabled:
			disableFinishedBeforeRelease = true
		default:
		}
		if disableFinishedBeforeRelease {
			break
		}
		var blocked bool
		if err := s.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity
			WHERE datname = current_database() AND $1::integer = ANY(pg_blocking_pids(pid)))`, linkPID).Scan(&blocked); err != nil {
			t.Fatal(err)
		}
		if blocked {
			break
		}
		select {
		case <-ctx.Done():
			t.Fatal("disable neither completed nor waited on admitted link: ", ctx.Err())
		case <-time.After(5 * time.Millisecond):
		}
	}
	if err := gate.Rollback(ctx); err != nil {
		t.Fatal(err)
	}
	var resultLink linkResult
	select {
	case resultLink = <-linked:
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	if !disableFinishedBeforeRelease {
		select {
		case resultDisable = <-disabled:
		case <-ctx.Done():
			t.Fatal(ctx.Err())
		}
	}
	if resultDisable.err != nil || resultDisable.project.Status != "disabled" {
		t.Fatalf("project disable did not complete: %+v %v", resultDisable.project, resultDisable.err)
	}
	if disableFinishedBeforeRelease {
		if Code(resultLink.err) != "PROJECT_DISABLED" {
			t.Errorf("disable committed before releasing the blocked admission, but link was not refused: link=%+v err=%v", resultLink.link, resultLink.err)
		}
	} else if resultLink.err != nil {
		t.Fatalf("the admitted link holding up disable must complete first: %v", resultLink.err)
	}
	current, err := s.GetWorkProject(ctx, collection, project.LocalSlug)
	if err != nil || current.Status != "disabled" {
		t.Fatalf("final project state: %+v %v", current, err)
	}
	links, err := s.ListWorkArtifactLinks(ctx, ListWorkArtifactLinksRequest{
		Collection: collection, ProjectSlug: project.LocalSlug, ItemUUID: itemID,
	})
	if err != nil {
		t.Fatal(err)
	}
	if disableFinishedBeforeRelease {
		if len(links) != 0 {
			t.Errorf("link effect was admitted after project disable committed: %+v", links)
		}
	} else if len(links) != 1 || links[0].LinkID != resultLink.link.LinkID {
		t.Errorf("serialized admission effect was lost: %+v", links)
	}
	request.RequestID = uuid.NewString()
	request.Role = "evidence"
	_, err = s.LinkWorkArtifact(ctx, request)
	requireCode(t, err, "PROJECT_DISABLED")
}
