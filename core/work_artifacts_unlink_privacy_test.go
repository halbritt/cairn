package core

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/plane"
)

func TestWorkArtifactUnlinkRespectsCurrentSourcePrivacy(t *testing.T) {
	for _, scenario := range []string{
		"forgotten_before_first_unlink",
		"forgotten_after_unlink",
		"current_scope_moved_after_unlink",
	} {
		t.Run(scenario, func(t *testing.T) {
			s, root := testOperator(t)
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			enableWorkArtifactFeature(t, s, ctx)
			collection := "repo:unlink-privacy:" + uuid.NewString()
			project, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
				RequestID: uuid.NewString(), Collection: collection, LocalSlug: "unlink-privacy",
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
				PlaneItemUUID: itemID, Identifier: "UNLINK-1", Title: "Local relationship fixture",
			}}, false, nil); err != nil {
				t.Fatal(err)
			}
			source, err := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: Draft{
				Kind: "note", Body: "Synthetic deliverable", ClaimType: "self", Sensitivity: "shareable",
				Scope: Scope{Repo: collection, TaskID: "*", RunID: "*"},
			}})
			if err != nil {
				t.Fatal(err)
			}
			sourceID := uuid.MustParse(source.RecordID)
			artifact, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
				RequestID: uuid.NewString(), Collection: collection, Kind: "cairn_result",
				CairnRecordID: &sourceID, CairnVersion: &source.Version, DisplayName: "Synthetic deliverable",
			})
			if err != nil {
				t.Fatal(err)
			}
			link, err := s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
				RequestID: uuid.NewString(), Collection: collection, ProjectSlug: project.LocalSlug,
				ItemUUID: itemID, RevisionID: artifact.RevisionID, Role: "evidence",
				Rationale: "Source-derived unlink rationale sentinel",
			})
			if err != nil {
				t.Fatal(err)
			}
			request := UnlinkWorkArtifactRequest{
				RequestID: uuid.NewString(), Collection: collection, ProjectSlug: project.LocalSlug,
				ItemUUID: itemID, LinkID: link.LinkID, ExpectedRevision: link.Revision,
			}
			checkRemoved := func(t *testing.T, got WorkArtifactLink, masked bool) {
				t.Helper()
				if got.LinkID != link.LinkID || got.BindingID != project.BindingID || got.PlaneItemUUID != itemID ||
					got.ArtifactID != artifact.ArtifactID || got.RevisionID != artifact.RevisionID || got.Role != link.Role ||
					got.Revision != link.Revision+1 || got.State != "removed" || got.Author != link.Author ||
					got.RemovedBy != s.channel.Principal || got.RemovedAt == nil {
					t.Errorf("removal lost structural identity: %+v", got)
				}
				if masked && got.Rationale != "" {
					t.Errorf("removal disclosed unavailable source rationale: %q", got.Rationale)
				}
				if !masked && got.Rationale != link.Rationale {
					t.Errorf("eligible removal unexpectedly changed rationale: %q", got.Rationale)
				}
			}
			if scenario != "forgotten_before_first_unlink" {
				removed, err := s.UnlinkWorkArtifact(ctx, request)
				if err != nil {
					t.Fatal(err)
				}
				checkRemoved(t, removed, false)
			}

			if scenario == "current_scope_moved_after_unlink" {
				// Defensive storage-boundary coverage only: no public API can move a
				// source to another repository. Model a recovered/current version that
				// differs from the exact linked version, without altering the receipt.
				tx, err := s.pool.Begin(ctx)
				if err != nil {
					t.Fatal(err)
				}
				defer tx.Rollback(ctx)
				if _, err := tx.Exec(ctx, "SELECT set_config('cairn.caller', $1, true), set_config('cairn.witness', 'testimony', true)", s.channel.Principal); err != nil {
					t.Fatal(err)
				}
				if _, err := tx.Exec(ctx, `INSERT INTO cairn.record_version(record_id, version, kind, body, repo, task_id, run_id, claim_type)
					VALUES($1, 2, 'note', 'Synthetic moved version', $2, '*', '*', 'self')`, source.RecordID, collection+":moved"); err != nil {
					t.Fatal(err)
				}
				if _, err := tx.Exec(ctx, `UPDATE cairn.memory_record SET current_version = 2 WHERE record_id = $1`, source.RecordID); err != nil {
					t.Fatal(err)
				}
				if err := tx.Commit(ctx); err != nil {
					t.Fatal(err)
				}
			} else {
				preview, err := s.PreviewDeletion(ctx, source.RecordID)
				if err != nil {
					t.Fatal(err)
				}
				if _, err := s.Forget(ctx, ForgetRequest{
					RequestID: uuid.NewString(), RecordID: source.RecordID, ExpectedVersion: source.Version,
					GrantID: root.ID, PreviewID: preview.PreviewID,
				}); err != nil {
					t.Fatal(err)
				}
			}
			unavailable, err := s.GetWorkArtifactRevision(ctx, collection, artifact.RevisionID)
			if err != nil || !unavailable.Unavailable {
				t.Fatalf("source eligibility did not change through the selected fixture: %+v %v", unavailable, err)
			}

			switch scenario {
			case "forgotten_before_first_unlink":
				// Removal remains possible after Forget. A blanket source refusal
				// would strand the local relationship instead of fixing disclosure.
				for _, phase := range []string{"first", "exact_retry"} {
					t.Run(phase, func(t *testing.T) {
						removed, err := s.UnlinkWorkArtifact(ctx, request)
						if err != nil {
							t.Fatalf("removing an unavailable-source link must succeed: %v", err)
						}
						checkRemoved(t, removed, true)
					})
				}
			case "forgotten_after_unlink":
				_, err := s.UnlinkWorkArtifact(ctx, request)
				requireCode(t, err, "PAYLOAD_UNAVAILABLE")
			case "current_scope_moved_after_unlink":
				_, err := s.UnlinkWorkArtifact(ctx, request)
				requireCode(t, err, "AUTHORITY_DENIED")
			}
			page, err := s.GetWorkArtifactLinkHistory(ctx, GetWorkArtifactLinkHistoryRequest{
				Collection: collection, ProjectSlug: project.LocalSlug, ItemUUID: itemID, LinkID: link.LinkID,
			})
			if err != nil {
				t.Fatal(err)
			}
			checkRemoved(t, page.Link, true)
			if len(page.History) != 2 || page.History[0].State != "active" || page.History[1].State != "removed" || page.More {
				t.Fatalf("removal/retry changed local transition history: %+v", page.History)
			}
			for _, row := range page.History {
				if row.LinkID != link.LinkID || row.Rationale != "" {
					t.Errorf("unavailable-source history lost identity or disclosed rationale: %+v", row)
				}
			}
		})
	}
}
