package core

import (
	"context"
	"github.com/google/uuid"
	"testing"
)

func TestHistoricalRecompileUsesFrozenEligibilityAndVersions(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	writer := testStore(t, Channel{Principal: "replay-writer"})
	r, err := writer.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	e := testEvidence(t, op, repo)
	b, err := op.Promote(ctx, PromoteRequest{uuid.NewString(), r.RecordID, 1, root.ID, []string{e.ID}, "Historical replay fixture support", nil})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "planning", AvailableTokens: 64000}
	original, err := op.Compile(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	next := b.Draft
	next.Body = "Corrected advice without the original matching term"
	if _, err = op.Correct(ctx, CorrectRequest{uuid.NewString(), b.RecordID, b.Version, root.ID, next, []string{e.ID}, "Correct advice after the historical cutoff", nil}); err != nil {
		t.Fatal(err)
	}
	replay, err := op.Recompile(ctx, RecompileRequest{ReceiptID: original.ReceiptID, Query: req.Query})
	if err != nil {
		t.Fatal(err)
	}
	if replay.Seal != original.Seal || replay.Semantic.Selected[0].Record.Version != b.Version {
		t.Fatal("future correction changed historical selection")
	}
	_, err = op.Recompile(ctx, RecompileRequest{ReceiptID: original.ReceiptID, Query: "different query"})
	requireCode(t, err, "INVALID_REQUEST")
	req.RequestID = uuid.NewString()
	current, err := op.Compile(ctx, req, Destination{"local", true})
	if err != nil || len(current.Semantic.Selected) != 0 {
		t.Fatal("fixture correction did not change current retrieval")
	}
}

func TestHistoricalRecompileFreezesEvidenceAndDetectsMissingInputs(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	writer := testStore(t, Channel{Principal: "replay-evidence"})
	record, err := writer.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	evidence := testEvidence(t, op, repo)
	_, err = op.Promote(ctx, PromoteRequest{uuid.NewString(), record.RecordID, 1, root.ID, []string{evidence.ID}, "Retain historical gate evidence", nil})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "planning", AvailableTokens: 64000}
	original, err := op.Compile(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = op.pool.Exec(ctx, `UPDATE cairn.evidence SET state='dangling' WHERE evidence_id=$1`, evidence.ID); err != nil {
		t.Fatal(err)
	}
	historical, err := op.Recompile(ctx, RecompileRequest{ReceiptID: original.ReceiptID, Query: req.Query})
	if err != nil || historical.Seal != original.Seal {
		t.Fatalf("future evidence state entered old read set: %v", err)
	}
	req.RequestID = uuid.NewString()
	live, err := op.Compile(ctx, req, Destination{"local", true})
	if err != nil || len(live.Semantic.Selected) != 0 {
		t.Fatal("live compile missed evidence change")
	}
	if _, err = op.pool.Exec(ctx, `UPDATE cairn.record_version SET body='corrupt retained version' WHERE record_id=$1 AND version=2`, record.RecordID); err != nil {
		t.Fatal(err)
	}
	_, err = op.Recompile(ctx, RecompileRequest{ReceiptID: original.ReceiptID, Query: req.Query})
	requireCode(t, err, "INTEGRITY_FAILURE")
}

// A later recurrence can use newly captured advice. Recompiling the original
// incident's receipt must never acquire that advice or a later required policy.
func TestHistoricalRecompileExcludesLaterNotesAndInstructions(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	originalNote, err := op.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}
	original, err := op.Compile(ctx, req, Destination{"local", true})
	if err != nil || len(original.Semantic.Selected) != 1 {
		t.Fatalf("original read set: %+v %v", original, err)
	}
	later := projectNote(repo)
	later.Body = "A preventing lesson written after the original incident"
	if _, err = op.Create(ctx, CreateRequest{uuid.NewString(), later}); err != nil {
		t.Fatal(err)
	}
	later.Kind, later.Body = "instruction", "A mandatory instruction issued after the original incident"
	if _, err = op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: later, GrantID: root.ID, Mandatory: true, PolicyKey: "later-policy", Reason: "Test the historical cutoff"}); err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	current, err := op.Compile(ctx, req, Destination{"local", true})
	if err != nil || len(current.Semantic.Selected) != 3 {
		t.Fatalf("later advice and required policy must affect a fresh recurrence: %+v %v", current, err)
	}
	historical, err := op.Recompile(ctx, RecompileRequest{ReceiptID: original.ReceiptID})
	if err != nil || historical.Seal != original.Seal || len(historical.Semantic.Selected) != 1 || historical.Semantic.Selected[0].Record.RecordID != originalNote.RecordID {
		t.Fatalf("later material leaked into the original read set: %+v %v", historical, err)
	}
}
