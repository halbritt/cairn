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

// Every row and transition is produced by Store APIs, including parsed model
// output. SQL only inspects retained identities and copied history payloads.
func TestWorkAdviceReviewLinkRationaleForgetLineage(t *testing.T) {
	for _, tc := range []struct {
		name     string
		existing bool
		manual   bool
	}{
		{"new_link_unlinked", false, false},
		{"existing_link_unlinked", true, false},
		{"new_link_later_operator_rationale", false, true},
		{"existing_link_later_operator_rationale", true, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
			defer cancel()
			s := restoreTestStore(t)
			for _, feature := range []string{"work-view/1", "work-artifacts/1", "work-advice/1"} {
				if err := s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: feature, Status: "active"}); err != nil {
					t.Fatal(err)
				}
			}
			collection := "fixture:advice-link-lineage:" + uuid.NewString()
			project, err := s.AddWorkProject(ctx, AddWorkProjectRequest{RequestID: uuid.NewString(), Collection: collection,
				LocalSlug: "lineage", PlaneInstance: "work", PlaneOrigin: "https://plane.example.com", WorkspaceUUID: uuid.New(),
				ProjectUUID: uuid.New(), ConfigRevision: "lineage-v1"})
			if err != nil {
				t.Fatal(err)
			}
			item := uuid.New()
			refresh, err := s.PrepareRefresh(ctx, collection, project.LocalSlug)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := s.CommitRefreshSnapshot(ctx, refresh, []plane.NormalizedItem{{PlaneItemUUID: item, Identifier: "LINEAGE-1", Title: "Synthetic rationale lineage"}}, false, nil); err != nil {
				t.Fatal(err)
			}
			sentinel := "FORGOTTEN_ADVICE_RATIONALE_" + uuid.NewString()
			source, err := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: Draft{Kind: "note", Body: sentinel,
				ClaimType: "self", Sensitivity: "shareable", Scope: Scope{Repo: collection, TaskID: "*", RunID: "*"}}})
			if err != nil {
				t.Fatal(err)
			}
			revision, err := s.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{RequestID: uuid.NewString(), Collection: collection,
				Kind: "report", ReportLocator: "https://reports.example.com/lineage/" + uuid.NewString(), RevisionLabel: "v1"})
			if err != nil {
				t.Fatal(err)
			}
			link := func(rationale string) WorkArtifactLink {
				t.Helper()
				result, err := s.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{RequestID: uuid.NewString(), Collection: collection,
					ProjectSlug: project.LocalSlug, ItemUUID: item, RevisionID: revision.RevisionID, Role: "evidence", Rationale: rationale})
				if err != nil {
					t.Fatal(err)
				}
				return result
			}
			const priorText = "Independent operator rationale before advice"
			var prior WorkArtifactLink
			if tc.existing {
				prior = link(priorText)
			}
			if err := s.SetWorkAdvicePolicy(ctx, project.BindingID, []string{"title", "sources", "artifacts"}); err != nil {
				t.Fatal(err)
			}
			raw, err := json.Marshal(map[string]any{"actions": []any{map[string]any{
				"action_type": advice.ActionLinkArtifact, "artifact_revision_id": revision.RevisionID.String(),
				"role": "evidence", "rationale": sentinel,
			}}})
			if err != nil {
				t.Fatal(err)
			}
			provider := &workflowParsedAdviceProvider{result: string(raw)}
			t.Cleanup(func() {
				if provider.calls != 1 {
					t.Errorf("provider invoked %d times; want one parsed generation", provider.calls)
				}
			})
			job := &WorkAdviceJob{BindingID: project.BindingID, PlaneItemUUID: item, RequestUUID: uuid.New(),
				ProfileName: advice.RouteCodexSubscription, ModelRoute: "fixture-model", TemplateVersion: 1}
			proposal, actions, err := s.GenerateWorkAdvice(ctx, job, AdviceInputSpec{BindingID: project.BindingID, PlaneItemUUID: item,
				SelectedSources:      []SourceRefSpec{{RecordID: uuid.MustParse(source.RecordID), Version: source.Version}},
				CandidateRevisionIDs: []uuid.UUID{revision.RevisionID}}, provider)
			if err != nil || proposal == nil || len(actions) != 1 || actions[0].Rationale != sentinel {
				t.Fatalf("actual parsed rationale generation failed: %v", err)
			}
			receipt, err := s.ApplyAdviceProposal(ctx, &WorkAdviceApplicationReceipt{RequestUUID: uuid.New(), ProposalUUID: proposal.ProposalUUID},
				proposal.ReviewVersion, []uuid.UUID{actions[0].ActionUUID})
			if err != nil || receipt == nil {
				t.Fatalf("generated link application failed: %v", err)
			}
			listReq := ListWorkArtifactLinksRequest{Collection: collection, ProjectSlug: project.LocalSlug, ItemUUID: item, IncludeRemoved: true}
			rows, err := s.ListWorkArtifactLinks(ctx, listReq)
			if err != nil || len(rows) != 1 || rows[0].Rationale != sentinel || rows[0].State != "active" || rows[0].RevisionID != revision.RevisionID {
				t.Fatalf("actual applied rationale positive control missing: %v", err)
			}
			applied := rows[0]
			if tc.existing && (applied.LinkID != prior.LinkID || applied.Revision <= prior.Revision) {
				t.Fatal("advice did not update the exact existing link as a new revision")
			}
			unlinkReq := UnlinkWorkArtifactRequest{RequestID: uuid.NewString(), Collection: collection, ProjectSlug: project.LocalSlug,
				ItemUUID: item, LinkID: applied.LinkID, ExpectedRevision: applied.Revision}
			removed, err := s.UnlinkWorkArtifact(ctx, unlinkReq)
			if err != nil || removed.State != "removed" || removed.LinkID != applied.LinkID || removed.Revision <= applied.Revision || removed.Rationale != sentinel {
				t.Fatalf("real unlink did not retain exact copied rationale in its successor revision: %v", err)
			}
			const manualText = "Independent later operator rationale must survive source Forget"
			latest := removed
			if tc.manual {
				latest = link(manualText)
				if latest.LinkID != applied.LinkID || latest.Revision <= removed.Revision || latest.Rationale != manualText || latest.State != "active" {
					t.Fatal("real relink did not persist independent later rationale")
				}
			}
			var copiesBefore int
			if err := s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.work_artifact_link_history
				WHERE link_id=$1 AND revision=ANY($2) AND rationale=$3`, applied.LinkID, []int64{applied.Revision, removed.Revision}, sentinel).Scan(&copiesBefore); err != nil || copiesBefore != 2 {
				t.Fatalf("history positive control lacks both real copied revisions: count=%d error=%v", copiesBefore, err)
			}
			grant, err := s.Bootstrap(ctx, BootstrapRequest{RequestID: uuid.NewString(), Reason: "Owned disposable advice rationale lineage regression"})
			if err != nil {
				t.Fatal(err)
			}
			preview, err := s.PreviewDeletion(ctx, source.RecordID)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := s.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: source.RecordID, ExpectedVersion: source.Version,
				GrantID: grant.ID, PreviewID: preview.PreviewID}); err != nil {
				t.Fatal(err)
			}
			rows, err = s.ListWorkArtifactLinks(ctx, listReq)
			if err != nil || len(rows) != 1 || rows[0].LinkID != latest.LinkID || rows[0].Revision != latest.Revision || rows[0].State != latest.State {
				t.Fatalf("Forget damaged current opaque link identity/state: %v", err)
			}
			if strings.Contains(rows[0].Rationale, sentinel) {
				t.Error("current link exposes forgotten copied rationale after a later revision")
			}
			if tc.manual && rows[0].Rationale != manualText {
				t.Error("Forget erased independent later operator rationale")
			}
			var storedCurrent string
			if err := s.pool.QueryRow(ctx, `SELECT rationale FROM cairn.work_artifact_link WHERE link_id=$1`, applied.LinkID).Scan(&storedCurrent); err != nil {
				t.Fatal(err)
			} else if strings.Contains(storedCurrent, sentinel) {
				t.Error("stored current link retains forgotten copied rationale")
			}
			history, err := s.GetWorkArtifactLinkHistory(ctx, GetWorkArtifactLinkHistoryRequest{Collection: collection,
				ProjectSlug: project.LocalSlug, ItemUUID: item, LinkID: applied.LinkID, Limit: 20})
			if err != nil || history.More {
				t.Fatalf("bounded exact link history failed after Forget: %v", err)
			}
			for _, entry := range history.History {
				if strings.Contains(entry.Rationale, sentinel) {
					t.Errorf("history revision %d exposes forgotten copied rationale", entry.Revision)
				}
			}
			for _, retained := range []WorkArtifactLink{applied, removed} {
				var stored string
				if err := s.pool.QueryRow(ctx, `SELECT rationale FROM cairn.work_artifact_link_history WHERE link_id=$1 AND revision=$2`, retained.LinkID, retained.Revision).Scan(&stored); err != nil {
					t.Errorf("Forget removed known historical identity %d: %v", retained.Revision, err)
				} else if strings.Contains(stored, sentinel) {
					t.Errorf("stored history revision %d retains forgotten copied rationale", retained.Revision)
				}
			}
			for _, control := range []struct {
				check bool
				row   WorkArtifactLink
				text  string
			}{{tc.existing, prior, priorText}, {tc.manual, latest, manualText}} {
				if !control.check {
					continue
				}
				var stored string
				if err := s.pool.QueryRow(ctx, `SELECT rationale FROM cairn.work_artifact_link_history WHERE link_id=$1 AND revision=$2`, control.row.LinkID, control.row.Revision).Scan(&stored); err != nil || stored != control.text {
					t.Errorf("Forget damaged independent operator history revision %d: %v", control.row.Revision, err)
				}
			}
			replayed, err := s.UnlinkWorkArtifact(ctx, unlinkReq)
			if err != nil {
				requireCode(t, err, "PAYLOAD_UNAVAILABLE")
			} else if replayed.LinkID != removed.LinkID || replayed.Revision != removed.Revision || strings.Contains(replayed.Rationale, sentinel) {
				t.Error("exact unlink replay exposes forgotten copied rationale or changes frozen link identity")
			}
		})
	}
}
