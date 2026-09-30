package core

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestCheckpointStoresMembersOutsideBoundedHeader(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	recoveryRoot(t, s)
	insertSyntheticRevokedGrants(t, s, maxCheckpointMembers+1)
	cp, err := s.Checkpoint(ctx, CheckpointRequest{uuid.NewString(), "streamed-checkpoint"})
	if err != nil {
		t.Fatal(err)
	}
	var inline int
	if err = s.pool.QueryRow(ctx, `SELECT jsonb_array_length(manifest->'members') FROM cairn.audit_checkpoint WHERE checkpoint_id=$1`, cp.ID).Scan(&inline); err != nil {
		t.Fatal(err)
	}
	if inline != 0 {
		t.Fatalf("checkpoint still materializes %d members in one manifest", inline)
	}
	var count int
	if err = s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.audit_checkpoint_member WHERE checkpoint_id=$1`, cp.ID).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != cp.Count || !cp.MembersOmitted || len(cp.Members) != 0 {
		t.Fatalf("membership/header mismatch: %d %+v", count, cp)
	}
	tx, err := s.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	members, err := auditMembers(ctx, tx)
	if err != nil {
		t.Fatal(err)
	}
	legacy, err := checkpointDigest(members)
	if err != nil || legacy != cp.Digest {
		t.Fatalf("checkpoint/1 digest changed: %s %s %v", legacy, cp.Digest, err)
	}
	report, err := verifyCheckpoint(ctx, tx, VerifyCheckpointRequest{cp.ID, cp.Digest, cp.ExportID})
	if err != nil || !report.Valid {
		t.Fatalf("streamed checkpoint invalid: %+v %v", report, err)
	}
}

func TestCheckpointReadsLegacyInlineAndRefusesStorageDamage(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	recoveryRoot(t, s)
	tx, err := s.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	members, err := auditMembers(ctx, tx)
	if err != nil {
		t.Fatal(err)
	}
	digest, err := checkpointDigest(members)
	if err != nil {
		t.Fatal(err)
	}
	legacy := AuditCheckpoint{Schema: checkpointSchema, ID: uuid.NewString(), ExportID: "legacy-inline", Members: members, Count: len(members), Digest: digest}
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.audit_checkpoint(checkpoint_id,export_id,manifest) VALUES($1,$2,$3)`, legacy.ID, legacy.ExportID, legacy); err != nil {
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	report, err := s.VerifyCheckpoint(ctx, VerifyCheckpointRequest{legacy.ID, legacy.Digest, legacy.ExportID})
	if err != nil || !report.Valid {
		t.Fatalf("legacy inline refused: %+v %v", report, err)
	}
	cp, err := s.Checkpoint(ctx, CheckpointRequest{uuid.NewString(), "member-backed"})
	if err != nil {
		t.Fatal(err)
	}
	var stored AuditCheckpoint
	if err = s.pool.QueryRow(ctx, `SELECT manifest FROM cairn.audit_checkpoint WHERE checkpoint_id=$1`, cp.ID).Scan(&stored); err != nil {
		t.Fatal(err)
	}
	// This is the pre-migration reader's count/list guard. A new representation
	// must never resemble a complete empty checkpoint to that reader.
	if stored.Count == len(stored.Members) {
		t.Fatal("pre-057 count/list guard would accept new storage")
	}
	if stored.StorageSchema != checkpointMemberStorage {
		t.Fatalf("storage version absent: %+v", stored)
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.audit_checkpoint_member SET digest=$2 WHERE checkpoint_id=$1`, cp.ID, strings.Repeat("0", 64)); err == nil {
		t.Fatal("member mutation allowed")
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.audit_checkpoint SET export_id='changed' WHERE checkpoint_id=$1`, cp.ID); err == nil {
		t.Fatal("header mutation allowed")
	}
	for _, damage := range []string{
		`ALTER TABLE cairn.audit_checkpoint_member DISABLE TRIGGER immutable_checkpoint_member;UPDATE cairn.audit_checkpoint_member SET digest=repeat('0',64)`,
		`ALTER TABLE cairn.audit_checkpoint DISABLE TRIGGER immutable_checkpoint;UPDATE cairn.audit_checkpoint SET manifest=jsonb_set(manifest,'{count}','0') WHERE storage_schema='cairn.audit-checkpoint-members/1'`,
	} {
		tx, err = s.begin(ctx)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = tx.Exec(ctx, damage); err != nil {
			t.Fatal(err)
		}
		_, err = verifyCheckpoint(ctx, tx, VerifyCheckpointRequest{cp.ID, cp.Digest, cp.ExportID})
		requireCode(t, err, "CHECKPOINT_MISMATCH")
		if err = tx.Rollback(ctx); err != nil {
			t.Fatal(err)
		}
	}
}

func TestCheckpointMemberFailureRollsBackHeaderAndRetry(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	recoveryRoot(t, s)
	if _, err := s.pool.Exec(ctx, `CREATE FUNCTION cairn.test_refuse_checkpoint_member() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture member failure';END $$;CREATE TRIGGER test_refuse_checkpoint_member BEFORE INSERT ON cairn.audit_checkpoint_member FOR EACH ROW EXECUTE FUNCTION cairn.test_refuse_checkpoint_member()`); err != nil {
		t.Fatal(err)
	}
	req := CheckpointRequest{uuid.NewString(), "atomic-member-write"}
	if _, err := s.Checkpoint(ctx, req); err == nil {
		t.Fatal("member failure accepted")
	}
	var headers int
	if err := s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.audit_checkpoint WHERE export_id=$1`, req.ExportID).Scan(&headers); err != nil || headers != 0 {
		t.Fatalf("partial header survived: %d %v", headers, err)
	}
	if _, err := s.pool.Exec(ctx, `DROP TRIGGER test_refuse_checkpoint_member ON cairn.audit_checkpoint_member;DROP FUNCTION cairn.test_refuse_checkpoint_member()`); err != nil {
		t.Fatal(err)
	}
	cp, err := s.Checkpoint(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	retry, err := s.Checkpoint(ctx, req)
	if err != nil || retry.ID != cp.ID {
		t.Fatalf("same request did not safely retry: %+v %v", retry, err)
	}
}

func TestCheckpointMembershipMigrationIsAtomic(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	// Reconstruct the previous schema only inside this disposable test database.
	if _, err := s.pool.Exec(ctx, `DROP TABLE cairn.audit_checkpoint_member;ALTER TABLE cairn.audit_checkpoint DROP COLUMN storage_schema;DELETE FROM public.cairn_migration WHERE version=57;CREATE TABLE cairn.audit_checkpoint_member(collision boolean)`); err != nil {
		t.Fatal(err)
	}
	if err := s.Migrate(ctx); err == nil {
		t.Fatal("migration collision unexpectedly succeeded")
	}
	var partial bool
	if err := s.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='cairn' AND table_name='audit_checkpoint' AND column_name='storage_schema') OR EXISTS(SELECT 1 FROM public.cairn_migration WHERE version=57)`).Scan(&partial); err != nil || partial {
		t.Fatalf("failed migration partially committed: %t %v", partial, err)
	}
	if _, err := s.pool.Exec(ctx, `DROP TABLE cairn.audit_checkpoint_member`); err != nil {
		t.Fatal(err)
	}
	if err := s.Migrate(ctx); err != nil {
		t.Fatalf("migration retry failed: %v", err)
	}
}
