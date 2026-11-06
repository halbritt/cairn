package core

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/plane"
)

// Regression: WC-02 artifact joins must honor the same Plane access rejection
// as ordinary work-item reads, without discarding locally asserted relationships.
func TestWorkArtifactReadsMaskRejectedPlaneProject(t *testing.T) {
	collection := "repo:artifact-auth-privacy:" + uuid.NewString()
	s := testStore(t, Channel{Principal: "operator:test", Operator: true, Repo: collection})
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	enableWorkArtifactFeature(t, s, ctx)

	revision, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		RequestID: uuid.NewString(), Collection: collection, Kind: "git",
		GitRepo: "https://example.com/local-artifact", GitObjectFormat: "sha1",
		GitCommitOID: strings.Repeat("a", 40), DisplayName: "Locally asserted artifact",
	})
	if err != nil {
		t.Fatal(err)
	}
	type fixture struct {
		project WorkProject
		item    plane.NormalizedItem
		active  WorkArtifactLink
		removed WorkArtifactLink
	}
	fixtures := make([]fixture, 0, 2)
	for _, slug := range []string{"rejected", "control"} {
		project, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
			RequestID: uuid.NewString(), LocalSlug: slug, Collection: collection,
			PlaneInstance: "work", PlaneOrigin: "https://plane.example.com",
			WorkspaceUUID: uuid.New(), ProjectUUID: uuid.New(), ConfigRevision: "fixture-v1",
		})
		if err != nil {
			t.Fatal(err)
		}
		updated := time.Date(2026, 10, 1, 12, 0, 0, 0, time.UTC)
		stateID := uuid.New()
		item := plane.NormalizedItem{
			PlaneItemUUID: uuid.New(), Identifier: strings.ToUpper(slug) + "-471",
			Title: "Plane-only " + slug + " title", StateID: &stateID,
			StateName: "Plane-only " + slug + " state", StateGroup: "started",
			AssigneeIDs: []string{uuid.NewString()}, AssigneeNames: []string{"Plane-only " + slug + " assignee"},
			UpdatedAtSource: &updated, SourceURL: "https://plane.example.com/" + slug + "/471",
		}
		refresh, err := s.PrepareRefresh(ctx, collection, slug)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := s.CommitRefreshSnapshot(ctx, refresh, []plane.NormalizedItem{item}, false, nil); err != nil {
			t.Fatal(err)
		}
		f := fixture{project: project, item: item}
		for _, role := range []string{"input", "evidence"} {
			link, err := s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
				RequestID: uuid.NewString(), Collection: collection, ProjectSlug: slug,
				ItemUUID: item.PlaneItemUUID, RevisionID: revision.RevisionID,
				Role: role, Rationale: "Local " + role + " assertion",
			})
			if err != nil {
				t.Fatal(err)
			}
			if role == "input" {
				f.active = link
				continue
			}
			f.removed, err = s.UnlinkWorkArtifact(ctx, UnlinkWorkArtifactRequest{
				RequestID: uuid.NewString(), Collection: collection, ProjectSlug: slug,
				ItemUUID: item.PlaneItemUUID, LinkID: link.LinkID, ExpectedRevision: link.Revision,
			})
			if err != nil {
				t.Fatal(err)
			}
		}
		fixtures = append(fixtures, f)
	}

	checkFields := func(t *testing.T, f fixture, identifier, title string, masked bool) {
		t.Helper()
		if masked {
			if strings.Contains(identifier, f.item.Identifier) || strings.Contains(title, f.item.Title) {
				t.Errorf("rejected Plane metadata leaked: identifier=%q title=%q", identifier, title)
			}
		} else if identifier != f.item.Identifier || title != f.item.Title {
			t.Errorf("eligible Plane metadata changed: identifier=%q title=%q", identifier, title)
		}
	}
	checkLink := func(t *testing.T, f fixture, got, want WorkArtifactLink, masked bool) {
		t.Helper()
		if got.LinkID != want.LinkID || got.BindingID != f.project.BindingID || got.PlaneItemUUID != f.item.PlaneItemUUID ||
			got.ArtifactID != revision.ArtifactID || got.RevisionID != revision.RevisionID ||
			got.Revision != want.Revision || got.Role != want.Role || got.State != want.State ||
			got.Author != want.Author || got.Rationale != want.Rationale || !got.AssertedAt.Equal(want.AssertedAt) {
			t.Errorf("local relationship changed: got %+v, want %+v", got, want)
		}
		if got.ArtifactRevision == nil || got.ArtifactRevision.Artifact == nil ||
			got.ArtifactRevision.RevisionID != revision.RevisionID || got.ArtifactRevision.Unavailable ||
			got.ArtifactRevision.Artifact.DisplayName != "Locally asserted artifact" {
			t.Errorf("unrelated artifact was hidden or lost: %+v", got.ArtifactRevision)
		}
		checkFields(t, f, got.ItemIdentifier, got.ItemTitle, masked)
	}
	checkReads := func(t *testing.T, rejected bool) {
		for i, f := range fixtures {
			masked := rejected && i == 0
			t.Run(f.project.LocalSlug, func(t *testing.T) {
				detail, err := s.GetWorkItemDetail(ctx, collection, f.project.LocalSlug, f.item.PlaneItemUUID.String())
				if err != nil {
					t.Fatal(err)
				}
				t.Run("ordinary_item", func(t *testing.T) {
					if detail.AuthRejected != masked || detail.Item.BindingID != f.project.BindingID || detail.Item.PlaneItemUUID != f.item.PlaneItemUUID {
						t.Fatalf("ordinary detail lost identity or rejection state: %+v", detail.Item)
					}
					checkFields(t, f, detail.Item.Identifier, detail.Item.Title, masked)
					if masked && (detail.Item.StateID != nil || detail.Item.StateName != "" || detail.Item.StateGroup != "" ||
						detail.Item.UpdatedAtSource != nil || detail.Item.SourceURL != "" || len(detail.Item.AssigneeIDs) != 0 || len(detail.Item.AssigneeNames) != 0) {
						t.Errorf("ordinary detail exposes rejected Plane fields: %+v", detail.Item)
					}
				})
				t.Run("detail_artifacts", func(t *testing.T) {
					if len(detail.Artifacts) != 1 {
						t.Fatalf("want one active local artifact, got %d", len(detail.Artifacts))
					}
					checkLink(t, f, detail.Artifacts[0], f.active, masked)
				})
				for _, includeRemoved := range []bool{false, true} {
					name := "active_links"
					if includeRemoved {
						name = "including_removed_links"
					}
					t.Run(name, func(t *testing.T) {
						links, err := s.ListWorkArtifactLinks(ctx, ListWorkArtifactLinksRequest{
							Collection: collection, ProjectSlug: f.project.LocalSlug, ItemUUID: f.item.PlaneItemUUID, IncludeRemoved: includeRemoved,
						})
						if err != nil {
							t.Fatal(err)
						}
						want := map[uuid.UUID]WorkArtifactLink{f.active.LinkID: f.active}
						if includeRemoved {
							want[f.removed.LinkID] = f.removed
						}
						if len(links) != len(want) {
							t.Fatalf("local link count changed: got %d want %d", len(links), len(want))
						}
						for _, link := range links {
							expected, ok := want[link.LinkID]
							if !ok {
								t.Fatalf("unexpected or repeated link %s", link.LinkID)
							}
							checkLink(t, f, link, expected, masked)
							delete(want, link.LinkID)
						}
					})
				}
				t.Run("removed_link_history", func(t *testing.T) {
					page, err := s.GetWorkArtifactLinkHistory(ctx, GetWorkArtifactLinkHistoryRequest{
						Collection: collection, ProjectSlug: f.project.LocalSlug, ItemUUID: f.item.PlaneItemUUID, LinkID: f.removed.LinkID,
					})
					if err != nil {
						t.Fatal(err)
					}
					checkLink(t, f, page.Link, f.removed, masked)
					if len(page.History) != 2 || page.More {
						t.Fatalf("local history lost: %+v", page)
					}
					for i, row := range page.History {
						if row.LinkID != f.removed.LinkID || row.Revision != int64(i+1) || row.Role != "evidence" ||
							row.Author != f.removed.Author || row.Rationale != f.removed.Rationale || row.State != []string{"active", "removed"}[i] {
							t.Errorf("local history changed: %+v", row)
						}
					}
				})
			})
		}
		t.Run("mixed_project_reverse_impact", func(t *testing.T) {
			page, err := s.ReverseImpact(ctx, ReverseImpactRequest{Collection: collection, RevisionID: revision.RevisionID})
			if err != nil {
				t.Fatal(err)
			}
			if len(page.Impacts) != 2 || page.More || page.Revision.RevisionID != revision.RevisionID || page.Revision.Unavailable {
				t.Fatalf("reverse impact lost a local relationship or artifact: %+v", page)
			}
			seen := map[uuid.UUID]bool{}
			for _, impact := range page.Impacts {
				index := 0
				if impact.BindingID == fixtures[1].project.BindingID {
					index = 1
				}
				f := fixtures[index]
				if impact.LinkID != f.active.LinkID || impact.BindingID != f.project.BindingID || impact.ProjectSlug != f.project.LocalSlug ||
					impact.PlaneItemUUID != f.item.PlaneItemUUID || impact.Role != f.active.Role || impact.LinkState != "active" ||
					impact.LinkAuthor != f.active.Author || !impact.LinkAssertedAt.Equal(f.active.AssertedAt) || seen[impact.LinkID] {
					t.Errorf("reverse impact local relationship changed: %+v", impact)
				}
				seen[impact.LinkID] = true
				masked := rejected && index == 0
				checkFields(t, f, impact.ItemIdentifier, impact.ItemTitle, masked)
				if masked {
					if impact.StateName != "" || impact.StateGroup != "" || impact.UpdatedAtSource != nil || impact.SourceURL != "" {
						t.Errorf("reverse impact exposes rejected Plane state/source: %+v", impact)
					}
				} else if impact.StateName != f.item.StateName || impact.StateGroup != f.item.StateGroup || impact.UpdatedAtSource == nil ||
					!impact.UpdatedAtSource.Equal(*f.item.UpdatedAtSource) || impact.SourceURL != f.item.SourceURL {
					t.Errorf("eligible reverse impact Plane fields changed: %+v", impact)
				}
			}
			if rejected {
				// Inspect the serialized public response as well as the typed fields.
				body, err := json.Marshal(page)
				if err != nil {
					t.Fatal(err)
				}
				for _, secret := range []string{fixtures[0].item.Identifier, fixtures[0].item.Title, fixtures[0].item.StateName, fixtures[0].item.SourceURL} {
					if strings.Contains(string(body), secret) {
						t.Errorf("serialized reverse impact retains rejected Plane field %q", secret)
					}
				}
			}
		})
	}
	if !t.Run("before_rejection", func(t *testing.T) { checkReads(t, false) }) {
		t.Fatal("fixture must expose both eligible projects before the access change")
	}

	// Advance only the fixture's refresh-rate clock. The rejection and refresh
	// lease still go through the real APIs; no auth or projection field is seeded.
	if _, err := s.pool.Exec(ctx, `UPDATE cairn.work_project SET next_refresh_allowed_at = clock_timestamp() - interval '1 second' WHERE binding_id = $1`, fixtures[0].project.BindingID); err != nil {
		t.Fatal(err)
	}
	refresh, err := s.PrepareRefresh(ctx, collection, fixtures[0].project.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}
	if err := s.RecordRefreshFailure(ctx, refresh, true, "Plane auth rejected"); err != nil {
		t.Fatal(err)
	}
	project, err := s.GetWorkProject(ctx, collection, fixtures[0].project.LocalSlug)
	if err != nil || !project.AuthRejected || project.RefreshInFlight {
		t.Fatalf("real rejected refresh did not close its claim: %+v %v", project, err)
	}
	t.Run("after_rejection", func(t *testing.T) { checkReads(t, true) })
}
