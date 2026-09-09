package core

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestRestoreSessionBlocksFreshAndCachedConsumers(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	recoveryRoot(t, s)
	create := CreateRequest{uuid.NewString(), projectNote(uuid.NewString())}
	record, err := s.Create(ctx, create)
	if err != nil {
		t.Fatal(err)
	}
	compile := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{record.Scope.Repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}
	_, err = s.Compile(ctx, compile, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	request := BeginRestoreRequest{uuid.NewString(), "synthetic isolated older backup", "Pause restored store before reconciliation"}
	session, err := s.BeginRestore(ctx, request)
	if err != nil {
		t.Fatal(err)
	}
	// Fencing alone still permits this fresh compile on the preceding release.
	compile.RequestID = uuid.NewString()
	_, err = s.Compile(ctx, compile, Destination{"local", true})
	requireCode(t, err, "RESTORE_PAUSED")
	_, err = s.Create(ctx, create)
	requireCode(t, err, "RESTORE_PAUSED")
	// Existing administrative credentials must not accidentally serve data.
	_, err = s.Get(ctx, record.RecordID)
	requireCode(t, err, "RESTORE_PAUSED")
	_, err = s.History(ctx, RecordHistoryRequest{RecordID: record.RecordID}, Destination{"local", true})
	requireCode(t, err, "RESTORE_PAUSED")
	// Recovery itself remains available while ordinary consumers are paused.
	_, err = s.CaptureRecovery(ctx)
	if err != nil {
		t.Fatal(err)
	}
	retry, err := s.BeginRestore(ctx, request)
	if err != nil || retry != session {
		t.Fatalf("begin retry: %+v %v", retry, err)
	}
}

func restoreVerificationFixture(t *testing.T, s *Store) (VerifyRestoreRequest, Record, BeginRestoreRequest) {
	t.Helper()
	ctx := context.Background()
	recoveryRoot(t, s)
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(uuid.NewString())})
	if err != nil {
		t.Fatal(err)
	}
	pkg, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{r.Scope.Repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	cp, err := s.Checkpoint(ctx, CheckpointRequest{RequestID: uuid.NewString(), ExportID: "synthetic-backup"})
	if err != nil {
		t.Fatal(err)
	}
	recovery, err := s.CaptureRecovery(ctx)
	if err != nil {
		t.Fatal(err)
	}
	begin := BeginRestoreRequest{uuid.NewString(), "synthetic-backup", "Pause fixture restore before verification"}
	session, err := s.BeginRestore(ctx, begin)
	if err != nil {
		t.Fatal(err)
	}
	return VerifyRestoreRequest{SessionID: session.SessionID, Checkpoint: VerifyCheckpointRequest{CheckpointID: cp.ID, ExpectedDigest: cp.Digest, ExpectedExportID: cp.ExportID}, Recovery: recovery, Fixtures: []RecompileRequest{{ReceiptID: pkg.ReceiptID, Query: ""}}}, r, begin
}

func TestRestoreSessionResumeRechecksEvidenceAndFencesOldDelivery(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	verify, record, begin := restoreVerificationFixture(t, s)
	report, err := s.VerifyRestore(ctx, verify)
	if err != nil || !report.Ready {
		t.Fatalf("intact restore: %+v %v", report, err)
	}
	req := ResumeRestoreRequest{RequestID: uuid.NewString(), Verification: verify, Policy: "local-restore/1", Reason: "Resume isolated verified fixture under approved operations policy"}
	result, err := s.ResumeRestore(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	status, err := s.RestoreStatus(ctx)
	if err != nil || status.Paused {
		t.Fatalf("not resumed: %+v %v", status, err)
	}
	retry, err := s.ResumeRestore(ctx, req)
	if err != nil || retry.ResumeID != result.ResumeID {
		t.Fatalf("resume retry: %+v %v", retry, err)
	}
	_, err = s.BeginRestore(ctx, begin)
	requireCode(t, err, "STALE_RESTORE")
	_, err = s.Get(ctx, record.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	requireCode(t, s.ClaimRun(ctx, verify.Fixtures[0].ReceiptID), "STALE_PACKAGE")
	fresh, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{record.Scope.Repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if err = s.ClaimRun(ctx, fresh.ReceiptID); err != nil {
		t.Fatal(err)
	}
	_, err = s.BeginRestore(ctx, BeginRestoreRequest{uuid.NewString(), "next-backup", "Begin distinct subsequent isolated restore"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.ResumeRestore(ctx, req)
	requireCode(t, err, "STALE_RESTORE")
}

func TestRestoreSessionCannotWaiveUnknownMissingAudit(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	verify, _, _ := restoreVerificationFixture(t, s)
	verify.Recovery.Audit = append(verify.Recovery.Audit, AuditMember{uuid.NewString(), strings.Repeat("a", 64)})
	var err error
	verify.Recovery.SHA256, err = recoveryDigest(verify.Recovery)
	if err != nil {
		t.Fatal(err)
	}
	report, err := s.VerifyRestore(ctx, verify)
	if err != nil || report.Ready {
		t.Fatalf("unknown audit accepted: %+v %v", report, err)
	}
	_, err = s.ResumeRestore(ctx, ResumeRestoreRequest{uuid.NewString(), verify, "local-restore/1", "Must not resume over an unknown lost governance event"})
	requireCode(t, err, "RESTORE_INCOMPLETE")
	status, err := s.RestoreStatus(ctx)
	if err != nil || !status.Paused {
		t.Fatalf("refusal unpaused restore: %+v %v", status, err)
	}
}

func TestRestoreSessionDoesNotTrustPriorGreenVerification(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	verify, record, _ := restoreVerificationFixture(t, s)
	report, err := s.VerifyRestore(ctx, verify)
	if err != nil || !report.Ready {
		t.Fatalf("before corruption: %+v %v", report, err)
	}
	// A green report is not admission. Corrupt retained fixture bytes before the
	// explicit resume; its in-transaction recompilation must notice the change.
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.record_version SET body='changed fixture bytes' WHERE record_id=$1`, record.RecordID); err != nil {
		t.Fatal(err)
	}
	_, err = s.ResumeRestore(ctx, ResumeRestoreRequest{uuid.NewString(), verify, "local-restore/1", "Recheck live restore facts before admission"})
	requireCode(t, err, "RESTORE_INCOMPLETE")
	status, err := s.RestoreStatus(ctx)
	if err != nil || !status.Paused {
		t.Fatalf("stale verification admitted: %+v %v", status, err)
	}
}

func TestRestoreSessionDrainsTransactionsBeforePausing(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	recoveryRoot(t, s)
	held, err := s.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer held.Rollback(ctx)
	req := BeginRestoreRequest{uuid.NewString(), "isolated-restore", "Drain existing consumers before pausing restored state"}
	blocked, cancel := context.WithTimeout(ctx, 150*time.Millisecond)
	defer cancel()
	_, err = s.BeginRestore(blocked, req)
	if err == nil || blocked.Err() != context.DeadlineExceeded {
		t.Fatalf("pause crossed a held ordinary transaction: %v", err)
	}
	var sessions int
	if err = s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.restore_session`).Scan(&sessions); err != nil {
		t.Fatal(err)
	}
	if sessions != 0 {
		t.Fatalf("timed out pause committed: %d", sessions)
	}
	if err = held.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err = s.BeginRestore(ctx, req); err != nil {
		t.Fatal(err)
	}
}

func TestRestoreSessionRebuildsKnownDependencyProjection(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	root := recoveryRoot(t, s)
	source, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(uuid.NewString())})
	if err != nil {
		t.Fatal(err)
	}
	draft := projectNote(source.Scope.Repo)
	draft.Relations = []RecordRelation{{source.RecordID, source.Version, "derived_from"}}
	dependent, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := s.PreviewDeletion(ctx, source.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	deletion, err := s.Forget(ctx, ForgetRequest{uuid.NewString(), source.RecordID, source.Version, root.ID, preview.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.pool.Exec(ctx, `DELETE FROM cairn.deletion_dependency WHERE deletion_id=$1`, deletion.DeletionID); err != nil {
		t.Fatal(err)
	}
	session, err := s.BeginRestore(ctx, BeginRestoreRequest{uuid.NewString(), "restore-projection-fixture", "Rebuild retained derived exclusions in isolated restore"})
	if err != nil {
		t.Fatal(err)
	}
	req := RebuildRestoreRequest{uuid.NewString(), session.SessionID}
	result, err := s.RebuildRestore(ctx, req)
	if err != nil || result.AddedExclusions != 1 || result.AffectedRecords != 1 {
		t.Fatalf("rebuild: %+v %v", result, err)
	}
	var excluded bool
	if err = s.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.deletion_dependency WHERE record_id=$1 AND version=$2 AND deletion_id=$3)`, dependent.RecordID, dependent.Version, deletion.DeletionID).Scan(&excluded); err != nil || !excluded {
		t.Fatalf("missing exact exclusion: %v %v", excluded, err)
	}
	retry, err := s.RebuildRestore(ctx, req)
	if err != nil || retry != result {
		t.Fatalf("rebuild retry: %+v %v", retry, err)
	}
	req.RequestID = uuid.NewString()
	again, err := s.RebuildRestore(ctx, req)
	if err != nil || again.AddedExclusions != 0 {
		t.Fatalf("repeat rebuild changes state: %+v %v", again, err)
	}
}

func TestRestoreSessionRetainsKnownGapsEvenWithOlderExternalInput(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	verify, _, _ := restoreVerificationFixture(t, s)
	imported := verify.Recovery
	imported.Audit = append([]AuditMember(nil), verify.Recovery.Audit...)
	lost := uuid.NewString()
	imported.Audit = append(imported.Audit, AuditMember{lost, strings.Repeat("c", 64)})
	var err error
	imported.SHA256, err = recoveryDigest(imported)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ReapplyRecovery(ctx, RecoveryReapplyRequest{uuid.NewString(), imported, "Retain separately known missing governance expectation"}); err != nil {
		t.Fatal(err)
	}
	// The source supplied here predates that separately retained expectation.
	report, err := s.VerifyRestore(ctx, verify)
	if err != nil || report.Ready {
		t.Fatalf("older input hid retained gap: %+v %v", report, err)
	}
	found := false
	for _, problem := range report.Problems {
		if strings.Contains(problem, lost) {
			found = true
		}
	}
	if !found {
		t.Fatalf("lost expectation not reported: %+v", report)
	}
}

func TestRestoreSessionRejectsScopedAndDifferentRootOperators(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	verify, _, _ := restoreVerificationFixture(t, s)
	for _, channel := range []Channel{{Principal: s.channel.Principal}, {Principal: s.channel.Principal, Operator: true, Repo: "limited"}, {Principal: "different:operator", Operator: true}} {
		caller := &Store{pool: s.pool, channel: channel}
		_, err := caller.VerifyRestore(ctx, verify)
		requireCode(t, err, "AUTHORITY_DENIED")
		_, err = caller.ResumeRestore(ctx, ResumeRestoreRequest{uuid.NewString(), verify, "local-restore/1", "Do not create admission from caller-selected identity"})
		requireCode(t, err, "AUTHORITY_DENIED")
	}
}

func TestRestoreSessionEmptyStoreDoesNotRequireInventedFixture(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	recoveryRoot(t, s)
	cp, err := s.Checkpoint(ctx, CheckpointRequest{uuid.NewString(), "empty-store-backup"})
	if err != nil {
		t.Fatal(err)
	}
	recovery, err := s.CaptureRecovery(ctx)
	if err != nil {
		t.Fatal(err)
	}
	session, err := s.BeginRestore(ctx, BeginRestoreRequest{uuid.NewString(), "empty-store-backup", "Reconcile an empty restored memory store"})
	if err != nil {
		t.Fatal(err)
	}
	req := VerifyRestoreRequest{SessionID: session.SessionID, Checkpoint: VerifyCheckpointRequest{cp.ID, cp.Digest, cp.ExportID}, Recovery: recovery}
	report, err := s.VerifyRestore(ctx, req)
	if err != nil || !report.Ready {
		t.Fatalf("empty restore: %+v %v", report, err)
	}
	if _, err = s.ResumeRestore(ctx, ResumeRestoreRequest{uuid.NewString(), req, "local-restore/1", "Resume empty store after all applicable recovery checks"}); err != nil {
		t.Fatal(err)
	}
}

func TestRestoreSessionRejectsPromotionReclassifiedAsInstruction(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	root := recoveryRoot(t, s)
	repo := uuid.NewString()
	writer := &Store{pool: s.pool, channel: Channel{Principal: "restore:independent-writer"}}
	r, err := writer.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	e := testEvidence(t, s, repo)
	promoted, err := s.Promote(ctx, PromoteRequest{uuid.NewString(), r.RecordID, r.Version, root.ID, []string{e.ID}, "Promote independently authored restore fixture", nil})
	if err != nil {
		t.Fatal(err)
	}
	control, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(uuid.NewString())})
	if err != nil {
		t.Fatal(err)
	}
	pkg, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{control.Scope.Repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	cp, err := s.Checkpoint(ctx, CheckpointRequest{uuid.NewString(), "class-damage-fixture"})
	if err != nil {
		t.Fatal(err)
	}
	recovery, err := s.CaptureRecovery(ctx)
	if err != nil {
		t.Fatal(err)
	}
	session, err := s.BeginRestore(ctx, BeginRestoreRequest{uuid.NewString(), cp.ExportID, "Verify class correspondence with retained transition authority"})
	if err != nil {
		t.Fatal(err)
	}
	// Matching current/version flags alone can agree on the wrong class. The
	// immutable promotion event still says this was a B transition, not C issue.
	// The retained fixture belongs to an independent control; its good seal must
	// not hide this unrelated authority-state mismatch.
	fault, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer fault.Rollback(ctx)
	if _, err = fault.Exec(ctx, `UPDATE cairn.record_version SET version_class='C',kind='instruction' WHERE record_id=$1 AND version=$2`, promoted.RecordID, promoted.Version); err != nil {
		t.Fatal(err)
	}
	if _, err = fault.Exec(ctx, `UPDATE cairn.memory_record SET class='C' WHERE record_id=$1`, promoted.RecordID); err != nil {
		t.Fatal(err)
	}
	if err = fault.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	req := VerifyRestoreRequest{SessionID: session.SessionID, Checkpoint: VerifyCheckpointRequest{cp.ID, cp.Digest, cp.ExportID}, Recovery: recovery, Fixtures: []RecompileRequest{{ReceiptID: pkg.ReceiptID, Query: ""}}}
	report, err := s.VerifyRestore(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	if report.Ready {
		t.Fatalf("matching corrupt flags promoted B into C admission: %+v", report)
	}
}
