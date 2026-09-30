package core

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/google/uuid"
)

func TestRecoveryStagePagesBoundBothRowsAndBytes(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	recoveryRoot(t, s)
	tx, err := s.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	staged, err := newRecoveryStage(ctx, tx)
	if err != nil {
		t.Fatal(err)
	}
	// Large independent row bodies would exceed the byte budget well before the
	// row limit. No real context path or production file is involved.
	if _, err = tx.Exec(ctx, `INSERT INTO pg_temp.cairn_recovery_expected SELECT 'context',lpad(i::text,3,'0'),jsonb_build_object('padding',repeat('x',4*1024*1024)) FROM generate_series(1,10) i`); err != nil {
		t.Fatal(err)
	}
	after := ""
	seen := 0
	pages := 0
	for {
		bodies, keys, err := staged.page(ctx, "context", after, recoveryPageSize)
		if err != nil {
			t.Fatal(err)
		}
		if len(keys) == 0 {
			break
		}
		bytes := 0
		for _, body := range bodies {
			bytes += len(body)
		}
		if bytes > MaxRecoveryBytes || len(keys) > recoveryPageSize || keys[len(keys)-1] <= after {
			t.Fatalf("unbounded/stalled page: bytes=%d rows=%d cursor=%s", bytes, len(keys), after)
		}
		seen += len(keys)
		pages++
		after = keys[len(keys)-1]
	}
	if seen != 10 || pages < 3 {
		t.Fatalf("page bound dropped rows: %d in %d pages", seen, pages)
	}
	if _, err = tx.Exec(ctx, `INSERT INTO pg_temp.cairn_recovery_expected VALUES('context','oversized',jsonb_build_object('padding',repeat('x',$1::int)))`, MaxRecoveryBytes+1); err != nil {
		t.Fatal(err)
	}
	_, _, err = staged.page(ctx, "context", "n", recoveryPageSize)
	requireCode(t, err, "BUDGET_REFUSED")
}

// Retained recovery records accept bounded historical labels independently of
// today's grant-input length. Small audit pages must still split large anchors.
func TestRecoveryStreamBoundsAssociatedWithdrawalBytes(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	recoveryRoot(t, s)
	tx, err := s.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	staged, err := newRecoveryStage(ctx, tx)
	if err != nil {
		t.Fatal(err)
	}
	_, err = tx.Exec(ctx, `INSERT INTO pg_temp.cairn_recovery_expected
 SELECT 'audit',md5(i::text)::uuid::text,jsonb_build_object('event_id',md5(i::text)::uuid,'sha256',repeat('a',64)) FROM generate_series(1,10) i;
 INSERT INTO pg_temp.cairn_recovery_expected
 SELECT 'withdrawal',md5(i::text)::uuid::text,jsonb_build_object('event_id',md5(i::text)::uuid,'subject_id',md5(('subject'||i)::text)::uuid,'kind','revoke_grant','repo',repeat('r',2*1024*1024)) FROM generate_series(1,10) i`)
	if err != nil {
		t.Fatal(err)
	}
	seen := map[string]bool{}
	parts := 0
	_, err = streamStagedRecovery(ctx, staged, func(record RecoveryRecord) error {
		if err := record.validate(); err != nil {
			return err
		}
		parts++
		for _, w := range record.Withdrawals {
			if seen[w.EventID] {
				t.Fatalf("duplicate withdrawal %s", w.EventID)
			}
			seen[w.EventID] = true
		}
		return nil
	})
	if err != nil || parts < 2 || len(seen) != 10 {
		t.Fatalf("split anchor union: parts=%d members=%d error=%v", parts, len(seen), err)
	}
}

func TestRecoveryStreamContinuesBeyondCompatibilityBudgetAndResumes(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	recoveryRoot(t, s)
	insertSyntheticRevokedGrants(t, s, 4*maxRecoveryEntries+1)
	// The same real audit/withdrawal inventories cross both the old count ceiling
	// and the bounded legacy collector. Maximum supported repository labels keep the fixture
	// small in rows; they are data, never file paths or authority tokens.
	if _, err := s.pool.Exec(ctx, `UPDATE cairn.authority_grant SET repo=repeat('r',256) WHERE principal='fixture:scale'`); err != nil {
		t.Fatal(err)
	}
	cp, err := s.CheckpointHeader(ctx, CheckpointRequest{uuid.NewString(), "streamed-recovery-backup"})
	if err != nil {
		t.Fatal(err)
	}
	session, err := s.BeginRestore(ctx, BeginRestoreRequest{uuid.NewString(), cp.ExportID, "Exercise bounded recovery continuation"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.CaptureRecoverySet(ctx)
	requireCode(t, err, "BUDGET_REFUSED")
	interrupted := errors.New("stop after first emitted record")
	callbacks := 0
	_, err = s.StreamRecovery(ctx, func(r RecoveryRecord) error { callbacks++; return interrupted })
	if !errors.Is(err, interrupted) || callbacks != 1 {
		t.Fatalf("stream interruption lost: %d %v", callbacks, err)
	}
	status, err := s.RestoreStatus(ctx)
	if err != nil || !status.Paused {
		t.Fatalf("interruption released pause: %+v %v", status, err)
	}
	directory := t.TempDir()
	emitted := 0
	maxBytes := 0
	summary, err := s.StreamRecovery(ctx, func(r RecoveryRecord) error {
		if err := r.validate(); err != nil {
			return err
		}
		body, err := json.MarshalIndent(r, "", "  ")
		if err != nil {
			return err
		}
		if len(body)+1 > MaxRecoveryBytes {
			return fmt.Errorf("oversized stream record: %d", len(body))
		}
		maxBytes = max(maxBytes, len(body)+1)
		emitted++
		return os.WriteFile(filepath.Join(directory, fmt.Sprintf("%06d.json", emitted)), body, 0600)
	})
	if err != nil {
		t.Fatal(err)
	}
	if emitted < 2 || emitted != summary.Count || maxBytes == 0 {
		t.Fatalf("incomplete stream: %+v records=%d", summary, emitted)
	}
	read := func(index int) RecoveryRecord {
		t.Helper()
		body, err := os.ReadFile(filepath.Join(directory, fmt.Sprintf("%06d.json", index)))
		if err != nil {
			t.Fatal(err)
		}
		var r RecoveryRecord
		if err = json.Unmarshal(body, &r); err != nil {
			t.Fatal(err)
		}
		return r
	}
	nextIndex := 1
	inspected, err := s.InspectRecoveryStream(ctx, func() (RecoveryRecord, bool, error) {
		if nextIndex > summary.Count {
			return RecoveryRecord{}, false, nil
		}
		r := read(nextIndex)
		nextIndex++
		return r, true, nil
	})
	if err != nil || !inspected.Consistent {
		t.Fatalf("stream inspection: %+v %v", inspected, err)
	}
	first := read(1)
	verify := VerifyRestoreRequest{SessionID: session.SessionID, Checkpoint: VerifyCheckpointRequest{cp.ID, cp.Digest, cp.ExportID}, Recovery: first}
	for i := 1; i <= summary.Count; i++ {
		req := RecoveryReapplyRequest{RequestID: uuid.NewSHA1(uuid.MustParse(summary.SetID), []byte(fmt.Sprint(i))).String(), Record: read(i), Reason: "Retain original streamed segment"}
		applied, err := s.ReapplyRecovery(ctx, req)
		if err != nil {
			t.Fatal(err)
		}
		if i == 1 {
			retry, err := s.ReapplyRecovery(ctx, req)
			if err != nil || retry.ApplicationID != applied.ApplicationID {
				t.Fatalf("segment retry changed effect: %+v %v", retry, err)
			}
		}
	}
	ready, err := s.VerifyRestore(ctx, verify)
	if err != nil || !ready.Ready {
		t.Fatalf("complete bounded union refused: %+v %v", ready, err)
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.authority_grant SET revoked=false WHERE grant_id=(SELECT grant_id FROM cairn.authority_grant WHERE principal='fixture:scale' ORDER BY event_id DESC LIMIT 1)`); err != nil {
		t.Fatal(err)
	}
	resume := ResumeRestoreRequest{RequestID: uuid.NewString(), Verification: verify, Policy: "local-restore/1", Reason: "Resume after current complete verification"}
	_, err = s.ResumeRestore(ctx, resume)
	requireCode(t, err, "RESTORE_INCOMPLETE")
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.authority_grant SET revoked=true WHERE principal='fixture:scale' AND NOT revoked`); err != nil {
		t.Fatal(err)
	}
	resume.RequestID = uuid.NewString()
	resumed, err := s.ResumeRestore(ctx, resume)
	if err != nil || !resumed.Verification.Ready {
		t.Fatalf("bounded continuation did not resume: %+v %v", resumed, err)
	}
	status, err = s.RestoreStatus(ctx)
	if err != nil || status.Paused {
		t.Fatalf("valid resume failed: %+v %v", status, err)
	}
}
