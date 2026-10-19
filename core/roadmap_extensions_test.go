package core

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestRoadmapCAIRN18InspectH1HandleProfile(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := "roadmap-18:" + uuid.NewString()
	d := projectNote(repo)
	d.Body = "h1 conformance handle note " + strings.Repeat("handle payload ", 30)
	if _, err := op.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: d}); err != nil {
		t.Fatal(err)
	}
	idx, err := op.Index(ctx, CompileRequest{
		RequestID:       uuid.NewString(),
		Scope:           Scope{Repo: repo, TaskID: "t18", RunID: "r18"},
		Query:           "conformance",
		Purpose:         "context",
		AvailableTokens: 64000,
	}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	before, err := op.InspectH1HandleProfile(ctx, idx.Package.ReceiptID)
	if err != nil {
		t.Fatal(err)
	}
	if !before.ConformanceReady || before.HandleCount != 1 || before.ExpandedCount != 0 || before.CreditsRemaining != 4 {
		t.Fatalf("unexpected H1 profile before expansion: %+v", before)
	}
	if _, err := op.Expand(ctx, ExpandRequest{
		RequestID: uuid.NewString(),
		ReceiptID: idx.Package.ReceiptID,
		Handle:    idx.Handles[0].Handle,
	}, Destination{"local", true}); err != nil {
		t.Fatal(err)
	}
	after, err := op.InspectH1HandleProfile(ctx, idx.Package.ReceiptID)
	if err != nil {
		t.Fatal(err)
	}
	if after.ExpandedCount != 1 || after.CreditsRemaining != 3 {
		t.Fatalf("unexpected H1 profile after expansion: %+v", after)
	}
}

func TestRoadmapCAIRN19PreviewOfflineSessionConsolidation(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := "roadmap-19:" + uuid.NewString()
	taskID := "task-consolidation-19"
	d := Draft{Kind: "note", Body: "session handoff v1", Scope: Scope{Repo: repo, TaskID: taskID, RunID: "run-1"}, ClaimType: "self"}
	r1, err := op.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: d})
	if err != nil {
		t.Fatal(err)
	}
	d.Body = "session handoff v2 with follow-up notes"
	if _, err := op.Edit(ctx, EditRequest{RequestID: uuid.NewString(), RecordID: r1.RecordID, ExpectedVersion: 1, Draft: d}); err != nil {
		t.Fatal(err)
	}
	proposals, err := op.PreviewOfflineSessionConsolidation(ctx, repo)
	if err != nil {
		t.Fatal(err)
	}
	if len(proposals) != 1 || proposals[0].TaskID != taskID || proposals[0].TotalVersions != 2 || proposals[0].Disposition != "review_required" || proposals[0].AutomaticAuthorityGain {
		t.Fatalf("unexpected offline groomer proposals: %+v", proposals)
	}
}

func TestRoadmapCAIRN20EvaluateDecayExperiment(t *testing.T) {
	now := time.Date(2026, 10, 19, 12, 0, 0, 0, time.UTC)
	candidates := []CandidateEvaluation{
		{RecordID: "rec-older", Version: 1, LexicalMatches: 3, ScopeSpecificity: 2, WrittenAt: now.Add(-60 * 24 * time.Hour)},
		{RecordID: "rec-fresh", Version: 1, LexicalMatches: 2, ScopeSpecificity: 2, WrittenAt: now.Add(-1 * 24 * time.Hour)},
	}
	_, err := EvaluateDecayExperiment(candidates, now, 14.0, true)
	requireCode(t, err, "POLICY_UNENFORCEABLE")

	comp, err := EvaluateDecayExperiment(candidates, now, 14.0, false)
	if err != nil {
		t.Fatal(err)
	}
	if comp.ProductionRankingMutated || comp.EvaluatedCount != 2 || comp.RankShiftCount != 2 {
		t.Fatalf("expected rank shift without production mutation: %+v", comp)
	}
	if comp.Candidates[0].RecordID != "rec-fresh" || comp.Candidates[0].BaselineRank != 2 || comp.Candidates[0].ExperimentRank != 1 {
		t.Fatalf("expected fresh candidate to move to rank 1 under 14d half-life: %+v", comp.Candidates)
	}
}

func TestRoadmapCAIRN21CheckH3RuntimeMediation(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := "roadmap-21:" + uuid.NewString()
	writer := testStore(t, Channel{Principal: "writer:" + repo, Repo: repo})
	qualifier := testStore(t, Channel{Principal: "qualifier:" + repo, Repo: repo})
	grant, err := op.Grant(ctx, GrantRequest{
		RequestID:    uuid.NewString(),
		ParentID:     root.ID,
		Principal:    qualifier.channel.Principal,
		Repo:         repo,
		Capabilities: []string{"promote"},
		Reason:       "Grant promote for H3 runtime mediation test",
	})
	if err != nil {
		t.Fatal(err)
	}
	localDraft := Draft{Kind: "note", Body: "local-only note", Sensitivity: "local", Scope: Scope{Repo: repo, TaskID: "t21", RunID: "r21"}, ClaimType: "self"}
	localA, err := writer.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: localDraft})
	if err != nil {
		t.Fatal(err)
	}
	shareableDraft := Draft{Kind: "note", Body: "shareable note to promote", Sensitivity: "shareable", Scope: Scope{Repo: repo, TaskID: "t21", RunID: "r21"}, ClaimType: "self"}
	shareableA, err := writer.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: shareableDraft})
	if err != nil {
		t.Fatal(err)
	}
	ev, err := op.CaptureEvidence(ctx, EvidenceRequest{RequestID: uuid.NewString(), Repo: repo, Body: "shareable evidence", Source: "fixture", Sensitivity: "shareable"})
	if err != nil {
		t.Fatal(err)
	}
	shareableB, err := qualifier.Promote(ctx, PromoteRequest{
		RequestID:       uuid.NewString(),
		RecordID:        shareableA.RecordID,
		ExpectedVersion: shareableA.Version,
		GrantID:         grant.ID,
		EvidenceIDs:     []string{ev.ID},
		Reason:          "Promote shareable B record for consequential H3 mediation",
	})
	if err != nil {
		t.Fatal(err)
	}
	blocked, err := op.CheckH3RuntimeMediation(ctx, repo, "hosted", "consequential", []string{localA.RecordID, shareableB.RecordID})
	if err != nil {
		t.Fatal(err)
	}
	if blocked.Allowed || blocked.BlockReasons[localA.RecordID] != "DESTINATION_PROHIBITED" {
		t.Fatalf("expected localA to be blocked from hosted consequential mediation: %+v", blocked)
	}
	allowed, err := op.CheckH3RuntimeMediation(ctx, repo, "hosted", "consequential", []string{shareableB.RecordID})
	if err != nil || !allowed.Allowed {
		t.Fatalf("expected shareableB to be allowed for hosted consequential mediation: %+v %v", allowed, err)
	}
}
