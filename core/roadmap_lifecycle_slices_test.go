package core

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestRoadmapCAIRN11AuditEvidenceFreshness(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := "roadmap-11:" + uuid.NewString()
	writer := testStore(t, Channel{Principal: "writer:" + repo, Repo: repo})
	qualifier := testStore(t, Channel{Principal: "qualifier:" + repo, Repo: repo})
	grant, err := op.Grant(ctx, GrantRequest{
		RequestID:    uuid.NewString(),
		ParentID:     root.ID,
		Principal:    qualifier.channel.Principal,
		Repo:         repo,
		Capabilities: []string{"promote"},
		Reason:       "Grant promote capability for evidence freshness test",
	})
	if err != nil {
		t.Fatal(err)
	}
	smallEv := testEvidence(t, op, repo)
	if _, err := op.CheckEvidence(ctx, EvidenceCheckRequest{RequestID: uuid.NewString(), EvidenceID: smallEv.ID}); err != nil {
		t.Fatal(err)
	}
	largeBody := strings.Repeat("managed-artifact-payload-", 3000)
	largeEv, err := op.CaptureEvidence(ctx, EvidenceRequest{
		RequestID:   uuid.NewString(),
		Repo:        repo,
		Body:        largeBody,
		Source:      "fixture:large-artifact",
		Sensitivity: "local",
	})
	if err != nil {
		t.Fatal(err)
	}
	draft := projectNote(repo)
	created, err := writer.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}
	promoted, err := qualifier.Promote(ctx, PromoteRequest{
		RequestID:       uuid.NewString(),
		RecordID:        created.RecordID,
		ExpectedVersion: created.Version,
		GrantID:         grant.ID,
		EvidenceIDs:     []string{largeEv.ID},
		Reason:          "Promote claim backed by unverified large evidence",
	})
	if err != nil {
		t.Fatal(err)
	}
	report, err := op.AuditEvidenceFreshness(ctx, repo, 24*time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	if report.TotalEvidence != 2 || report.InlineCount != 1 || report.ManagedThresholdCount != 1 || report.StaleOrFailedCount != 1 {
		t.Fatalf("unexpected evidence freshness counts: %+v", report)
	}
	if len(report.InvalidationCandidates) != 1 || report.InvalidationCandidates[0].RecordID != promoted.RecordID || report.InvalidationCandidates[0].Reason != "evidence_unverified" {
		t.Fatalf("expected invalidation candidate for promoted B record: %+v", report.InvalidationCandidates)
	}
}

func TestRoadmapCAIRN12EvaluateTaskContextBudget(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := "roadmap-12:" + uuid.NewString()
	taskID := "task-budget-12"
	d := projectNote(repo)
	d.Scope.TaskID = taskID
	d.Body = "compiler budget tracking note " + strings.Repeat("context payload ", 40)
	if _, err := op.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: d}); err != nil {
		t.Fatal(err)
	}
	idx, err := op.Index(ctx, CompileRequest{
		RequestID:       uuid.NewString(),
		Scope:           Scope{Repo: repo, TaskID: taskID, RunID: "run-1"},
		Query:           "compiler",
		Purpose:         "context",
		AvailableTokens: 64000,
	}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := op.Expand(ctx, ExpandRequest{
		RequestID: uuid.NewString(),
		ReceiptID: idx.Package.ReceiptID,
		Handle:    idx.Handles[0].Handle,
	}, Destination{"local", true}); err != nil {
		t.Fatal(err)
	}
	generous, err := op.EvaluateTaskContextBudget(ctx, repo, taskID, 100000)
	if err != nil {
		t.Fatal(err)
	}
	if generous.ReceiptCount != 1 || generous.ExpansionCount != 1 || generous.TotalBytes <= generous.ReceiptBytes || generous.InterlockRefused {
		t.Fatalf("unexpected generous budget status: %+v", generous)
	}
	tight, err := op.EvaluateTaskContextBudget(ctx, repo, taskID, 32)
	if err != nil {
		t.Fatal(err)
	}
	if !tight.InterlockRefused || tight.RemainingBytes != 0 {
		t.Fatalf("expected tight ceiling interlock refusal: %+v", tight)
	}
}

func TestRoadmapCAIRN13InspectGovernanceConflictAndWaiverStatus(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := "roadmap-13:" + uuid.NewString()
	issuer := testStore(t, Channel{Principal: "issuer:" + repo, Repo: repo})
	grant, err := op.Grant(ctx, GrantRequest{
		RequestID:    uuid.NewString(),
		ParentID:     root.ID,
		Principal:    issuer.channel.Principal,
		Repo:         repo,
		Capabilities: []string{"issue", "retract"},
		Reason:       "Grant issue and retract for governance inspection test",
	})
	if err != nil {
		t.Fatal(err)
	}
	c1, err := issuer.Issue(ctx, IssueRequest{
		RequestID: uuid.NewString(),
		Draft:     Draft{Kind: "instruction", Body: "policy variant alpha", Scope: Scope{Repo: repo, TaskID: "t1", RunID: "*"}, Pins: &Applicability{TaskClass: "build"}, ClaimType: "self"},
		GrantID:   grant.ID,
		Mandatory: true,
		PolicyKey: "deploy.mode",
		Reason:    "Issue initial instruction for scope waiver and conflict test",
	})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := issuer.PreviewRetraction(ctx, c1.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := issuer.AuthorizeScope(ctx, AuthorizeScopeRequest{
		RequestID:       uuid.NewString(),
		RecordID:        c1.RecordID,
		ExpectedVersion: c1.Version,
		Scope:           Scope{Repo: repo, TaskID: "*", RunID: "*"},
		Pins:            &Applicability{},
		GrantID:         grant.ID,
		PreviewID:       preview.PreviewID,
		Reason:          "Waive task-level restriction across repository",
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := issuer.Issue(ctx, IssueRequest{
		RequestID: uuid.NewString(),
		Draft:     Draft{Kind: "instruction", Body: "policy variant beta", Scope: Scope{Repo: repo, TaskID: "*", RunID: "*"}, ClaimType: "self"},
		GrantID:   grant.ID,
		Mandatory: true,
		PolicyKey: "deploy.mode",
		Reason:    "Issue conflicting instruction on deploy.mode",
	}); err != nil {
		t.Fatal(err)
	}
	_, _ = issuer.Retract(ctx, RetractRequest{
		RequestID:       uuid.NewString(),
		RecordID:        c1.RecordID,
		ExpectedVersion: 2,
		GrantID:         grant.ID,
		PreviewID:       preview.PreviewID,
		Reason:          "Attempt retract while conflict is open to record refusal",
	})
	report, err := op.InspectGovernanceConflictAndWaiverStatus(ctx, repo)
	if err != nil {
		t.Fatal(err)
	}
	if report.OpenConflictCount != 1 || report.TotalRefusals < 1 || len(report.ActiveScopeWaivers) != 1 {
		t.Fatalf("unexpected governance conflict/waiver report: %+v", report)
	}
}

func TestRoadmapCAIRN14ValidateImportQuarantine(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := "roadmap-14:" + uuid.NewString()
	baseDraft := projectNote(repo)
	baseDraft.Sensitivity = "local"
	baseDraft.Pins = &Applicability{TaskClass: "build"}
	upstream, err := op.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: baseDraft})
	if err != nil {
		t.Fatal(err)
	}
	depDraft := baseDraft
	depDraft.Body = "derived note inheriting local sensitivity and narrow pins"
	depDraft.Relations = []RecordRelation{{RecordID: upstream.RecordID, Version: upstream.Version, Relation: "derived_from"}}
	dependent, err := op.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: depDraft})
	if err != nil {
		t.Fatal(err)
	}
	clean, err := op.ValidateImportQuarantine(ctx, repo, dependent.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if clean.Quarantined || len(clean.Violations) != 0 || clean.InspectedRelations != 1 {
		t.Fatalf("expected clean import quarantine assessment: %+v", clean)
	}
	if _, err := op.pool.Exec(ctx, `UPDATE cairn.memory_record SET sensitivity='shareable' WHERE record_id=$1`, dependent.RecordID); err != nil {
		t.Fatal(err)
	}
	laundered, err := op.ValidateImportQuarantine(ctx, repo, dependent.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if !laundered.Quarantined || len(laundered.Violations) == 0 {
		t.Fatalf("expected quarantined violation for sensitivity laundering: %+v", laundered)
	}
}

func TestRoadmapCAIRN15EvaluateUseRetentionFloor(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := "roadmap-15:" + uuid.NewString()
	writer := testStore(t, Channel{Principal: "writer:" + repo, Repo: repo})
	qualifier := testStore(t, Channel{Principal: "qualifier:" + repo, Repo: repo})
	grant, err := op.Grant(ctx, GrantRequest{
		RequestID:    uuid.NewString(),
		ParentID:     root.ID,
		Principal:    qualifier.channel.Principal,
		Repo:         repo,
		Capabilities: []string{"promote"},
		Reason:       "Grant promote for retention floor protection test",
	})
	if err != nil {
		t.Fatal(err)
	}
	n1, err := writer.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: Draft{Kind: "note", Body: "retention note alpha", Scope: Scope{Repo: repo, TaskID: "t1", RunID: "r1"}, ClaimType: "self"}})
	if err != nil {
		t.Fatal(err)
	}
	n2, err := writer.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: Draft{Kind: "note", Body: "retention note beta", Scope: Scope{Repo: repo, TaskID: "t1", RunID: "r1"}, ClaimType: "self"}})
	if err != nil {
		t.Fatal(err)
	}
	ev := testEvidence(t, op, repo)
	if _, err := qualifier.Promote(ctx, PromoteRequest{
		RequestID:       uuid.NewString(),
		RecordID:        n2.RecordID,
		ExpectedVersion: n2.Version,
		GrantID:         grant.ID,
		EvidenceIDs:     []string{ev.ID},
		Reason:          "Promote beta so live B decision protects use retention",
	}); err != nil {
		t.Fatal(err)
	}
	verifyUseRetentionProtection(t, ctx, op, repo, n1.RecordID, n2.RecordID)
}

func verifyUseRetentionProtection(t *testing.T, ctx context.Context, op *Store, repo, aID, bID string) {
	t.Helper()
	if _, err := op.Compile(ctx, CompileRequest{
		RequestID:       uuid.NewString(),
		Scope:           Scope{Repo: repo, TaskID: "t1", RunID: "r1"},
		Query:           "retention",
		Purpose:         "context",
		AvailableTokens: 64000,
	}, Destination{"local", true}); err != nil {
		t.Fatal(err)
	}
	futureNow := time.Now().UTC().Add(100 * 24 * time.Hour)
	report, err := op.EvaluateUseRetentionFloor(ctx, repo, futureNow)
	if err != nil {
		t.Fatal(err)
	}
	if report.TotalUseRows != 2 || report.ProtectedCount != 1 || report.PruneEligibleCount != 1 {
		t.Fatalf("unexpected retention floor counts: %+v", report)
	}
	for _, entry := range report.Entries {
		if entry.RecordID == aID && !entry.EligibleForPrune {
			t.Fatalf("expected ordinary A record use past 90d to be prune-eligible: %+v", entry)
		}
		if entry.RecordID == bID && (!entry.ProtectedByLiveDecision || entry.EligibleForPrune) {
			t.Fatalf("expected active B record use to be protected from pruning: %+v", entry)
		}
	}
}

func TestRoadmapCAIRN16DetectOutOfProcedureRestore(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	var curGen int64
	if err := op.pool.QueryRow(ctx, `SELECT generation FROM cairn.retrieval_generation WHERE singleton`).Scan(&curGen); err != nil {
		t.Fatal(err)
	}
	clean, err := op.DetectOutOfProcedureRestore(ctx, curGen, "")
	if err != nil {
		t.Fatal(err)
	}
	if clean.OutOfProcedureRestore || len(clean.Reasons) != 0 {
		t.Fatalf("expected clean restore integrity status: %+v", clean)
	}
	regressed, err := op.DetectOutOfProcedureRestore(ctx, curGen+5, uuid.NewString())
	if err != nil {
		t.Fatal(err)
	}
	if !regressed.OutOfProcedureRestore || len(regressed.Reasons) < 2 {
		t.Fatalf("expected out-of-procedure detection on generation regression and checkpoint mismatch: %+v", regressed)
	}
}
