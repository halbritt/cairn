package core

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestCheckpointVerifiesExpectedAuditSetAndExcludesOrdinaryMemory(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Kind = "instruction"
	c, err := op.Issue(ctx, IssueRequest{uuid.NewString(), d, root.ID, false, false, "checkpoint", "Issue checkpoint fixture instruction"})
	if err != nil {
		t.Fatal(err)
	}
	cp, err := op.Checkpoint(ctx, CheckpointRequest{uuid.NewString(), "fixture:backup"})
	if err != nil {
		t.Fatal(err)
	}
	req := VerifyCheckpointRequest{cp.ID, cp.Digest, cp.ExportID}
	verified, err := op.VerifyCheckpoint(ctx, req)
	if err != nil || !verified.Valid {
		t.Fatalf("checkpoint: %+v %v", verified, err)
	}
	ordinary, err := op.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = op.Edit(ctx, EditRequest{uuid.NewString(), ordinary.RecordID, 1, ordinary.Draft}); err != nil {
		t.Fatal(err)
	}
	next, err := op.Checkpoint(ctx, CheckpointRequest{uuid.NewString(), "fixture:next"})
	if err != nil || next.Digest != cp.Digest {
		t.Fatalf("ordinary memory entered audit digest: %v", err)
	}
	// Simulate replica corruption inside a rolled-back transaction. Production
	// writes cannot update the immutable audit, and this test leaves no damage.
	tx, err := op.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `ALTER TABLE cairn.authority_event DISABLE TRIGGER immutable_audit`); err != nil {
		t.Fatal(err)
	}
	if _, err = tx.Exec(ctx, `UPDATE cairn.authority_event SET resulting_version=99 WHERE subject_id=$1`, c.RecordID); err != nil {
		t.Fatal(err)
	}
	damaged, err := verifyCheckpoint(ctx, tx, req)
	if err != nil || damaged.Valid || len(damaged.Altered) != 1 {
		t.Fatalf("altered audit accepted: %+v %v", damaged, err)
	}
	if _, err = tx.Exec(ctx, `UPDATE cairn.authority_event SET event_type='promote' WHERE subject_id=$1`, c.RecordID); err != nil {
		t.Fatal(err)
	}
	missing, err := verifyCheckpoint(ctx, tx, req)
	if err != nil || missing.Valid || len(missing.Missing) != 1 {
		t.Fatalf("missing audit member accepted: %+v %v", missing, err)
	}
	if err = tx.Rollback(ctx); err != nil {
		t.Fatal(err)
	}
	req.ExpectedDigest = strings.Repeat("0", 64)
	_, err = op.VerifyCheckpoint(ctx, req)
	requireCode(t, err, "CHECKPOINT_MISMATCH")
	req.CheckpointID = uuid.NewString()
	req.ExpectedDigest = cp.Digest
	_, err = op.VerifyCheckpoint(ctx, req)
	requireCode(t, err, "CHECKPOINT_MISMATCH")
	agent := testStore(t, Channel{Principal: "agent:checkpoint"})
	_, err = agent.Checkpoint(ctx, CheckpointRequest{uuid.NewString(), "fixture:denied"})
	requireCode(t, err, "AUTHORITY_DENIED")
}

func TestCheckpointClosedMembershipDoesNotAbsorbLaterEvents(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	cp, err := op.Checkpoint(ctx, CheckpointRequest{uuid.NewString(), "fixture:old-export"})
	if err != nil {
		t.Fatal(err)
	}
	d := projectNote(uuid.NewString())
	d.Kind = "instruction"
	if _, err = op.Issue(ctx, IssueRequest{uuid.NewString(), d, root.ID, false, false, "later", "Issue after older checkpoint"}); err != nil {
		t.Fatal(err)
	}
	result, err := op.VerifyCheckpoint(ctx, VerifyCheckpointRequest{cp.ID, cp.Digest, cp.ExportID})
	if err != nil || !result.Valid || result.UncoveredCount < 1 {
		t.Fatalf("later event changed closed set: %+v %v", result, err)
	}
	newer, err := op.Checkpoint(ctx, CheckpointRequest{uuid.NewString(), "fixture:new-export"})
	if err != nil || newer.Digest == cp.Digest {
		t.Fatal("new checkpoint failed to cover new event")
	}
}
