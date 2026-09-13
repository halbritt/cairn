package core

import (
	"context"
	"github.com/google/uuid"
	"testing"
)

func TestDemotionPreservesConsumedBAndHasNoAuthorityEvent(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	writer := testStore(t, Channel{Principal: "demotion-writer"})
	a, err := writer.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	e := testEvidence(t, op, repo)
	b, err := op.Promote(ctx, PromoteRequest{uuid.NewString(), a.RecordID, 1, root.ID, []string{e.ID}, "Promote independently supported fixture", nil})
	if err != nil {
		t.Fatal(err)
	}
	query := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "compiler", Purpose: "planning", AvailableTokens: 64000}
	pkg, err := op.Compile(ctx, query, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	var before, after int
	if err = op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.authority_event WHERE subject_id=$1`, b.RecordID).Scan(&before); err != nil {
		t.Fatal(err)
	}
	demoted, err := op.Demote(ctx, DemoteRequest{uuid.NewString(), b.RecordID, b.Version, root.ID})
	if err != nil || demoted.Class != "A" || demoted.Version != 3 {
		t.Fatalf("demotion: %+v %v", demoted, err)
	}
	if err = op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.authority_event WHERE subject_id=$1`, b.RecordID).Scan(&after); err != nil || after != before {
		t.Fatal("cheap demotion created authority audit")
	}
	query.RequestID = uuid.NewString()
	live, err := op.Compile(ctx, query, Destination{"local", true})
	if err != nil || len(live.Semantic.Selected) != 0 {
		t.Fatal("demoted A remained consequential")
	}
	historical, err := op.Recompile(ctx, RecompileRequest{ReceiptID: pkg.ReceiptID, Query: query.Query})
	if err != nil || historical.Seal != pkg.Seal || historical.Semantic.Selected[0].Record.Class != "B" {
		t.Fatalf("consumed B was rewritten: %v", err)
	}
}
func TestDemotionRefusesActiveInstructionDependenciesAndOpenConflict(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	writer := testStore(t, Channel{Principal: "citation-writer"})
	a, err := writer.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	e := testEvidence(t, op, repo)
	b, err := op.Promote(ctx, PromoteRequest{uuid.NewString(), a.RecordID, 1, root.ID, []string{e.ID}, "Promote citation fixture", nil})
	if err != nil {
		t.Fatal(err)
	}
	middle := projectNote(repo)
	middle.Relations = []RecordRelation{{b.RecordID, b.Version, "derived_from"}}
	derived, err := writer.Create(ctx, CreateRequest{uuid.NewString(), middle})
	if err != nil {
		t.Fatal(err)
	}
	d := projectNote(repo)
	d.Kind = "instruction"
	d.Relations = []RecordRelation{{derived.RecordID, derived.Version, "derived_from"}}
	instruction, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: root.ID, Mandatory: true, RequiresRuntime: false, PolicyKey: "citation", Reason: "Issue instruction citing derived fixture"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.Demote(ctx, DemoteRequest{uuid.NewString(), b.RecordID, b.Version, root.ID})
	requireCode(t, err, "DEPENDENCY_CONFLICT")
	preview, err := op.PreviewRetraction(ctx, instruction.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = op.Retract(ctx, RetractRequest{uuid.NewString(), instruction.RecordID, 1, root.ID, "Retract citing instruction first", preview.PreviewID}); err != nil {
		t.Fatal(err)
	}
	conflict, err := op.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{b.RecordID, derived.RecordID}, "Compare unresolved fixture interpretations"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.Demote(ctx, DemoteRequest{uuid.NewString(), b.RecordID, b.Version, root.ID})
	requireCode(t, err, "OPEN_CONFLICT")
	if _, err = op.Resolve(ctx, ResolveRequest{uuid.NewString(), conflict.ID, 1, root.ID, "Resolve fixture before demotion"}); err != nil {
		t.Fatal(err)
	}
	if _, err = op.Demote(ctx, DemoteRequest{uuid.NewString(), b.RecordID, b.Version, root.ID}); err != nil {
		t.Fatal(err)
	}
}
func TestRelationsCannotLaunderScopeOrSensitivity(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Scope.TaskID = "narrow-task"
	source, err := op.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	broad := projectNote(repo)
	broad.Relations = []RecordRelation{{source.RecordID, 1, "derived_from"}}
	_, err = op.Create(ctx, CreateRequest{uuid.NewString(), broad})
	requireCode(t, err, "AUTHORITY_DENIED")
	broad.Scope = d.Scope
	broad.Sensitivity = "shareable"
	_, err = op.Create(ctx, CreateRequest{uuid.NewString(), broad})
	requireCode(t, err, "AUTHORITY_DENIED")
	broad.Sensitivity = "local"
	broad.Relations[0].Version = 2
	_, err = op.Create(ctx, CreateRequest{uuid.NewString(), broad})
	requireCode(t, err, "NOT_FOUND")
}

func TestRelationsOnEditUseStoredSensitivityAndInheritedCurrentness(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	source, err := op.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	target, err := op.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	d.Sensitivity = ""
	d.Relations = []RecordRelation{{source.RecordID, 1, "derived_from"}}
	_, err = op.Edit(ctx, EditRequest{uuid.NewString(), target.RecordID, 1, d})
	requireCode(t, err, "AUTHORITY_DENIED")
	pinned := projectNote(repo)
	pinned.Pins = &Applicability{TaskClass: "build"}
	source, err = op.Create(ctx, CreateRequest{uuid.NewString(), pinned})
	if err != nil {
		t.Fatal(err)
	}
	loose := projectNote(repo)
	loose.Relations = []RecordRelation{{source.RecordID, 1, "derived_from"}}
	_, err = op.Create(ctx, CreateRequest{uuid.NewString(), loose})
	requireCode(t, err, "AUTHORITY_DENIED")
}
func TestRetractionPreviewIncludesTransitiveUsesAndInvalidatesOnTheirExposure(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	source, err := op.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	middle := projectNote(repo)
	middle.Body = "middlemarker"
	middle.Relations = []RecordRelation{{source.RecordID, 1, "derived_from"}}
	mid, err := op.Create(ctx, CreateRequest{uuid.NewString(), middle})
	if err != nil {
		t.Fatal(err)
	}
	leaf := projectNote(repo)
	leaf.Body = "leafmarker"
	leaf.Relations = []RecordRelation{{mid.RecordID, 1, "derived_from"}}
	end, err := op.Create(ctx, CreateRequest{uuid.NewString(), leaf})
	if err != nil {
		t.Fatal(err)
	}
	query := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "leafmarker", Purpose: "context", AvailableTokens: 64000}
	if _, err = op.Compile(ctx, query, Destination{"local", true}); err != nil {
		t.Fatal(err)
	}
	preview, err := op.PreviewRetraction(ctx, source.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if len(preview.Uses) != 1 || preview.Uses[0].RecordID != end.RecordID || len(preview.Dependents) != 3 {
		t.Fatalf("missing transitive impact: %+v", preview)
	}
	query.RequestID = uuid.NewString()
	if _, err = op.Compile(ctx, query, Destination{"local", true}); err != nil {
		t.Fatal(err)
	}
	_, err = op.Retract(ctx, RetractRequest{uuid.NewString(), source.RecordID, 1, root.ID, "Refuse stale transitive exposure preview", preview.PreviewID})
	requireCode(t, err, "STALE_PREVIEW")
}

func TestHistoricalRelationsSurviveLaterEditsAndDemotionRequiresGrant(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	source, err := op.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	d := projectNote(repo)
	d.Body = "retained_citation"
	d.Relations = []RecordRelation{{source.RecordID, 1, "derived_from"}}
	cited, err := op.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	request := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "retained_citation", Purpose: "context", AvailableTokens: 64000}
	original, err := op.Compile(ctx, request, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	d.Relations = nil
	if _, err = op.Edit(ctx, EditRequest{uuid.NewString(), cited.RecordID, 1, d}); err != nil {
		t.Fatal(err)
	}
	replay, err := op.Recompile(ctx, RecompileRequest{ReceiptID: original.ReceiptID, Query: request.Query})
	if err != nil || replay.Seal != original.Seal || len(replay.Semantic.Selected[0].Record.Relations) != 1 {
		t.Fatalf("historical citation changed: %v", err)
	}
	outsider := testStore(t, Channel{Principal: "unauthorized-demoter"})
	_, err = outsider.Demote(ctx, DemoteRequest{uuid.NewString(), source.RecordID, 1, root.ID})
	requireCode(t, err, "AUTHORITY_DENIED")
}
