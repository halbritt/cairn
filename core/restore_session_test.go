package core

import (
	"context"
	"reflect"
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

func TestRestoreVerificationPagesEvidenceBeyondOldCeiling(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	verify, record, _ := restoreVerificationFixture(t, s)
	_, err := s.pool.Exec(ctx, `INSERT INTO cairn.evidence(evidence_id,repo,body,digest,source,witness,captured_by,sensitivity)
 SELECT gen_random_uuid(),$1,convert_to('x','UTF8'),sha256(convert_to('x','UTF8')),
 'fixture:restore','testimony','fixture:restore','local' FROM generate_series(1,10001)`, record.Scope.Repo)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.pool.Exec(ctx, `UPDATE cairn.evidence SET digest=decode(repeat('0',64),'hex')
	 WHERE evidence_id IN (SELECT evidence_id FROM cairn.evidence ORDER BY evidence_id LIMIT 100)
	 OR evidence_id=(SELECT evidence_id FROM cairn.evidence ORDER BY evidence_id DESC LIMIT 1)`)
	if err != nil {
		t.Fatal(err)
	}
	bad, err := s.VerifyRestore(ctx, verify)
	if err != nil || bad.Ready || len(bad.Problems) != 101 || !strings.HasPrefix(bad.Problems[0], "EVIDENCE_DIVERGENCE_UNMARKED:") || bad.Problems[100] != "EVIDENCE_DIVERGENCE_UNMARKED_ADDITIONAL:1" {
		t.Fatalf("large evidence set hid divergence: %+v %v", bad, err)
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.evidence SET digest=sha256(body) WHERE digest=decode(repeat('0',64),'hex')`); err != nil {
		t.Fatal(err)
	}
	ready, err := s.VerifyRestore(ctx, verify)
	if err != nil || !ready.Ready {
		t.Fatalf("large verified evidence set refused restore: %+v %v", ready, err)
	}
	if _, err = s.ResumeRestore(ctx, ResumeRestoreRequest{uuid.NewString(), verify, "local-restore/1", "Resume after complete paged evidence verification"}); err != nil {
		t.Fatal(err)
	}
}

func TestRestoreResumeTriggerRejectsMissingVerificationSession(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	recoveryRoot(t, s)
	session, err := s.BeginRestore(ctx, BeginRestoreRequest{uuid.NewString(), "fixture:missing-session", "Pause before checking direct restore resume invariant"})
	if err != nil {
		t.Fatal(err)
	}
	ctx = s.recoveryContext(ctx)
	tx, err := s.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	resumeID, eventID := uuid.NewString(), uuid.NewString()
	_, err = tx.Exec(ctx, `INSERT INTO cairn.authority_event(event_id,event_type,subject_id,previous_version,resulting_version,basis,reason)
 VALUES($1,'resume_restore',$2,0,1,'{}'::jsonb,'Synthetic restore resume event for constraint check')`, eventID, resumeID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = tx.Exec(ctx, `INSERT INTO cairn.restore_resume(resume_id,session_id,event_id,policy,verification)
 VALUES($1,$2,$3,'local-restore/1','{"ready":true}'::jsonb)`, resumeID, session.SessionID, eventID)
	if err != nil {
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err == nil || !strings.Contains(err.Error(), "restore resume requires matching atomic authority and verification") {
		t.Fatalf("missing verification session was accepted: %v", err)
	}
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
	req := RebuildRestoreRequest{RequestID: uuid.NewString(), SessionID: session.SessionID}
	result, err := s.RebuildRestore(ctx, req)
	if err != nil || result.AddedExclusions != 1 || result.AffectedRecords != 1 || result.Sources != 1 || !result.Complete || result.NextAfter != "" {
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

func restoreProblemWithPrefix(problems []string, prefix string) bool {
	for _, problem := range problems {
		if strings.HasPrefix(problem, prefix) {
			return true
		}
	}
	return false
}

// Synthetic revoked grants carry real retained revocation events, so they fill
// the same audit and withdrawal inventories as a store with many revocations
// without issuing ten thousand operations through the API. Only the disposable
// per-test database is written.
func insertSyntheticRevokedGrants(t *testing.T, s *Store, count int) {
	t.Helper()
	ctx := context.Background()
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.authority_event(event_id,event_type,subject_id,previous_version,resulting_version,actor,basis,reason)
 SELECT md5('event:'||i)::uuid,'revoke_grant',md5('grant:'||i)::uuid,1,2,'fixture:scale','[]'::jsonb,'Synthetic revocation for restore scale verification'
 FROM generate_series(1,$1::int) i`, count); err != nil {
		t.Fatal(err)
	}
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.authority_grant(grant_id,principal,repo,capabilities,parent_id,depth,version,revoked,event_id)
 SELECT md5('grant:'||i)::uuid,'fixture:scale','synthetic-scale-repo',root.capabilities,root.grant_id,1,2,true,md5('event:'||i)::uuid
 FROM generate_series(1,$1::int) i CROSS JOIN (SELECT grant_id,capabilities FROM cairn.authority_grant WHERE parent_id IS NULL) root`, count); err != nil {
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
}

// The store has more than 10,000 revocation withdrawals and audit members: above
// the old checkpoint, recovery-capture and export ceilings. Verification still
// checks every expectation, refuses a partly supplied export and a revived grant
// past the old ceiling, keeps the session paused until repaired, then resumes
// with the delivery fence intact.
func TestRestoreVerifiesAndResumesBeyondRecoveryCeilings(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	recoveryRoot(t, s)
	note, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(uuid.NewString())})
	if err != nil {
		t.Fatal(err)
	}
	pkg, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{note.Scope.Repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	insertSyntheticRevokedGrants(t, s, maxRecoveryEntries+1)
	cp, err := s.CheckpointHeader(ctx, CheckpointRequest{RequestID: uuid.NewString(), ExportID: "synthetic-scale-backup"})
	if err != nil || cp.Count <= maxCheckpointMembers || cp.Schema != "cairn.audit-checkpoint-header/1" {
		t.Fatalf("checkpoint above the old member ceiling: %+v %v", cp, err)
	}
	expected := VerifyCheckpointRequest{CheckpointID: cp.ID, ExpectedDigest: cp.Digest, ExpectedExportID: cp.ExportID}
	checked, err := s.VerifyCheckpoint(ctx, expected)
	if err != nil || !checked.Valid || checked.MissingTotal != 0 || checked.AlteredTotal != 0 {
		t.Fatalf("large checkpoint does not verify: %+v %v", checked, err)
	}
	set, err := s.CaptureRecoverySet(ctx)
	if err != nil || len(set) < 3 {
		t.Fatalf("recovery export above one record: %d records %v", len(set), err)
	}
	_, err = s.CaptureRecovery(ctx)
	requireCode(t, err, "BUDGET_REFUSED")
	session, err := s.BeginRestore(ctx, BeginRestoreRequest{uuid.NewString(), "synthetic-scale-backup", "Pause synthetic restore above the old recovery ceilings"})
	if err != nil {
		t.Fatal(err)
	}
	verify := VerifyRestoreRequest{SessionID: session.SessionID, Checkpoint: expected, Recovery: set[0], Fixtures: []RecompileRequest{{ReceiptID: pkg.ReceiptID, Query: ""}}}

	// Supplying one segment while the others are neither supplied nor retained
	// must not certify a partial expectation set.
	partial, err := s.VerifyRestore(ctx, verify)
	if err != nil || partial.Ready || !restoreProblemWithPrefix(partial.Problems, "RECOVERY_SEGMENTS_INCOMPLETE:") {
		t.Fatalf("partial segment set accepted: %+v %v", partial, err)
	}
	for _, segment := range set {
		if _, err = s.ReapplyRecovery(ctx, RecoveryReapplyRequest{uuid.NewString(), segment, "Retain each exported segment before restore verification"}); err != nil {
			t.Fatal(err)
		}
	}
	ready, err := s.VerifyRestore(ctx, verify)
	if err != nil || !ready.Ready || len(ready.Problems) != 0 {
		t.Fatalf("complete segment set refused: %+v %v", ready, err)
	}

	// Revive the last grant in event order, well beyond the old 10,000 ceiling.
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.authority_grant SET revoked=false WHERE grant_id=(SELECT grant_id FROM cairn.authority_grant WHERE principal='fixture:scale' ORDER BY event_id DESC LIMIT 1)`); err != nil {
		t.Fatal(err)
	}
	bad, err := s.VerifyRestore(ctx, verify)
	if err != nil || bad.Ready || !restoreProblemWithPrefix(bad.Problems, "GRANT_REVIVED:") {
		t.Fatalf("late revived grant escaped verification: %+v %v", bad, err)
	}
	resume := ResumeRestoreRequest{RequestID: uuid.NewString(), Verification: verify, Policy: "local-restore/1", Reason: "Resume only after every synthetic revocation is restored"}
	_, err = s.ResumeRestore(ctx, resume)
	requireCode(t, err, "RESTORE_INCOMPLETE")
	status, err := s.RestoreStatus(ctx)
	if err != nil || !status.Paused || status.SessionID != session.SessionID {
		t.Fatalf("failed verification released the pause: %+v %v", status, err)
	}
	_, err = s.Get(ctx, note.RecordID)
	requireCode(t, err, "RESTORE_PAUSED")

	if _, err = s.pool.Exec(ctx, `UPDATE cairn.authority_grant SET revoked=true WHERE principal='fixture:scale' AND NOT revoked`); err != nil {
		t.Fatal(err)
	}
	resume.RequestID = uuid.NewString()
	resumed, err := s.ResumeRestore(ctx, resume)
	if err != nil || !resumed.Verification.Ready {
		t.Fatalf("repaired store did not resume: %+v %v", resumed, err)
	}
	status, err = s.RestoreStatus(ctx)
	if err != nil || status.Paused {
		t.Fatalf("resume left the store paused: %+v %v", status, err)
	}
	if _, err = s.Get(ctx, note.RecordID); err != nil {
		t.Fatal(err)
	}
	requireCode(t, s.ClaimRun(ctx, pkg.ReceiptID), "STALE_PACKAGE")
	after, err := s.CaptureRecoverySet(ctx)
	if err != nil || len(after) < len(set) || CheckRecoverySegments(after) != nil {
		t.Fatalf("resumed store cannot export a complete set: %d records %v", len(after), err)
	}
}

// A source whose retained descendants outgrew the ordinary 1,000-version preview
// bound after it was forgotten is checked and rebuilt without that bound, and
// the rebuild resumes page by page from its cursor.
func TestRestoreRebuildPagesAndClosesDescendantsBeyondPreviewBound(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	root := recoveryRoot(t, s)
	repo := uuid.NewString()
	create := func(relations ...RecordRelation) Record {
		t.Helper()
		draft := projectNote(repo)
		draft.Relations = relations
		record, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
		if err != nil {
			t.Fatal(err)
		}
		return record
	}
	forget := func(record Record) string {
		t.Helper()
		preview, err := s.PreviewDeletion(ctx, record.RecordID)
		if err != nil {
			t.Fatal(err)
		}
		deletion, err := s.Forget(ctx, ForgetRequest{uuid.NewString(), record.RecordID, record.Version, root.ID, preview.PreviewID})
		if err != nil {
			t.Fatal(err)
		}
		return deletion.DeletionID
	}
	source := create()
	direct := create(RecordRelation{source.RecordID, source.Version, "derived_from"})
	otherSource := create()
	otherDirect := create(RecordRelation{otherSource.RecordID, otherSource.Version, "derived_from"})
	sourceDeletion := forget(source)
	otherDeletion := forget(otherSource)
	// Forgetting is bounded by its preview, so the growth happens afterward: new
	// versions cite a retained dependent, which carries the exclusion forward.
	for i := 0; i < 1000; i++ {
		create(RecordRelation{direct.RecordID, direct.Version, "derived_from"})
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	_, err = dependentVersions(ctx, tx, source.RecordID)
	tx.Rollback(ctx)
	requireCode(t, err, "BUDGET_REFUSED")
	var before int
	if err = s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.deletion_dependency WHERE deletion_id IN ($1,$2)`, sourceDeletion, otherDeletion).Scan(&before); err != nil || before <= 1000 {
		t.Fatalf("fixture did not exceed the preview bound: %d %v", before, err)
	}
	// Simulate a restore that lost the derived projection.
	if _, err = s.pool.Exec(ctx, `DELETE FROM cairn.deletion_dependency WHERE deletion_id IN ($1,$2)`, sourceDeletion, otherDeletion); err != nil {
		t.Fatal(err)
	}
	snapshot, err := s.CaptureRecovery(ctx)
	if err != nil {
		t.Fatal(err)
	}
	lost, err := s.InspectRecovery(ctx, snapshot)
	if err != nil || lost.Consistent {
		t.Fatalf("lost exclusions accepted or refused for size: %+v %v", lost, err)
	}
	named := false
	for _, gap := range lost.Gaps {
		named = named || (gap.SubjectID == source.RecordID && gap.Reason == "DEPENDENCY_EXCLUSION_MISSING")
	}
	if !named {
		t.Fatalf("large closure did not report its missing exclusion: %+v", lost)
	}

	session, err := s.BeginRestore(ctx, BeginRestoreRequest{uuid.NewString(), "restore-descendant-fixture", "Rebuild descendants beyond the preview bound"})
	if err != nil {
		t.Fatal(err)
	}
	for _, bad := range []RebuildRestoreRequest{{RequestID: uuid.NewString(), SessionID: session.SessionID, After: "not-a-uuid"}, {RequestID: uuid.NewString(), SessionID: session.SessionID, Limit: -1}, {RequestID: uuid.NewString(), SessionID: session.SessionID, Limit: restoreRebuildMaxSources + 1}} {
		_, err = s.RebuildRestore(ctx, bad)
		requireCode(t, err, "INVALID_REQUEST")
	}
	first, err := s.RebuildRestore(ctx, RebuildRestoreRequest{RequestID: uuid.NewString(), SessionID: session.SessionID, Limit: 1})
	if err != nil || first.Complete || first.Sources != 1 || first.NextAfter == "" {
		t.Fatalf("first page: %+v %v", first, err)
	}
	secondRequest := RebuildRestoreRequest{RequestID: uuid.NewString(), SessionID: session.SessionID, After: first.NextAfter, Limit: 1}
	second, err := s.RebuildRestore(ctx, secondRequest)
	if err != nil || !second.Complete || second.Sources != 1 || second.NextAfter != "" {
		t.Fatalf("second page: %+v %v", second, err)
	}
	if first.AddedExclusions+second.AddedExclusions != int64(before) || first.AffectedRecords+second.AffectedRecords == 0 {
		t.Fatalf("pages restored %d+%d of %d exclusions", first.AddedExclusions, second.AddedExclusions, before)
	}
	retry, err := s.RebuildRestore(ctx, secondRequest)
	if err != nil || retry != second {
		t.Fatalf("page retry: %+v %v", retry, err)
	}
	// Restarting from the beginning is idempotent and adds nothing.
	again, err := s.RebuildRestore(ctx, RebuildRestoreRequest{RequestID: uuid.NewString(), SessionID: session.SessionID})
	if err != nil || !again.Complete || again.Sources != 2 || again.AddedExclusions != 0 || again.AffectedRecords != 0 {
		t.Fatalf("restarted pass: %+v %v", again, err)
	}
	var excluded bool
	if err = s.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.deletion_dependency WHERE record_id=$1 AND version=$2 AND deletion_id=$3)`, otherDirect.RecordID, otherDirect.Version, otherDeletion).Scan(&excluded); err != nil || !excluded {
		t.Fatalf("second source lost its exact exclusion: %v %v", excluded, err)
	}
	restored, err := s.InspectRecovery(ctx, snapshot)
	if err != nil || !restored.Consistent {
		t.Fatalf("rebuilt projection still inconsistent: %+v %v", restored, err)
	}
	// A rebuild page names the current session, like every restore operation.
	_, err = s.RebuildRestore(ctx, RebuildRestoreRequest{RequestID: uuid.NewString(), SessionID: uuid.NewString()})
	requireCode(t, err, "STALE_RESTORE")
}

// Splitting is transport only: every expectation survives, each segment stands
// alone (a context segment carries its forgotten record's withdrawal) and a set
// missing any position is detectable.
func TestSplitRecoveryAnchorsContextsAndPreservesEveryExpectation(t *testing.T) {
	store := restoreTestStore(t)
	root := recoveryRoot(t, store).ID
	small := RecoveryRecord{Schema: recoverySchema, RootGrantID: root, CapturedAt: time.Now().UTC(), Audit: []AuditMember{}, Withdrawals: []RecoveryWithdrawal{}, Contexts: []RecoveryContext{}}
	var err error
	if small.SHA256, err = recoveryDigest(small); err != nil {
		t.Fatal(err)
	}
	one, err := splitRecoveryStagedForTest(t, store, small)
	if err != nil || len(one) != 1 || one[0].Segment != nil || one[0].SHA256 != small.SHA256 {
		t.Fatalf("record that fits was divided: %+v %v", one, err)
	}

	unsorted := RecoveryRecord{Schema: recoverySchema, RootGrantID: root, CapturedAt: time.Now().UTC(), Audit: []AuditMember{}, Withdrawals: []RecoveryWithdrawal{}, Contexts: []RecoveryContext{}}
	forgotten := uuid.NewString()
	for i := 0; i <= maxRecoveryEntries; i++ {
		event := uuid.NewString()
		w := RecoveryWithdrawal{"revoke_grant", uuid.NewString(), "synthetic-repo", event}
		if i == 0 {
			w = RecoveryWithdrawal{"forget", forgotten, "synthetic-repo", event}
		}
		unsorted.Audit = append(unsorted.Audit, AuditMember{event, strings.Repeat("b", 64)})
		unsorted.Withdrawals = append(unsorted.Withdrawals, w)
	}
	for i := 0; i < recoverySegmentContexts+500; i++ {
		receipt := uuid.NewString()
		unsorted.Contexts = append(unsorted.Contexts, RecoveryContext{forgotten, ManagedContext{OwnershipID: uuid.NewString(), ReceiptID: receipt, Directory: "/synthetic/" + receipt, DirectoryDevice: "1", DirectoryInode: "2", BodySHA256: strings.Repeat("c", 64)}})
	}
	full := RecoveryRecord{Schema: recoverySchema, RootGrantID: root, CapturedAt: unsorted.CapturedAt, Audit: []AuditMember{}, Withdrawals: []RecoveryWithdrawal{}, Contexts: []RecoveryContext{}}
	if err = mergeRecovery(&full, unsorted); err != nil {
		t.Fatal(err)
	}
	if full.SHA256, err = recoveryDigest(full); err != nil {
		t.Fatal(err)
	}
	if err = full.validate(); err == nil {
		t.Fatal("test fixture must exceed one record")
	}
	set, err := splitRecoveryStagedForTest(t, store, full)
	if err != nil || len(set) < 4 {
		t.Fatalf("split: %d records %v", len(set), err)
	}
	union := RecoveryRecord{Schema: recoverySchema, RootGrantID: root, Audit: []AuditMember{}, Withdrawals: []RecoveryWithdrawal{}, Contexts: []RecoveryContext{}}
	for i, segment := range set {
		if segment.Segment == nil || segment.Segment.Index != i+1 || segment.Segment.Count != len(set) {
			t.Fatalf("segment %d position: %+v", i, segment.Segment)
		}
		if err = segment.validate(); err != nil {
			t.Fatalf("segment %d is not independently valid: %v", i, err)
		}
		if err = mergeRecovery(&union, segment); err != nil {
			t.Fatal(err)
		}
	}
	if !reflect.DeepEqual(union.Audit, full.Audit) || !reflect.DeepEqual(union.Withdrawals, full.Withdrawals) || !reflect.DeepEqual(union.Contexts, full.Contexts) {
		t.Fatalf("segments changed the expectation set: %d/%d audit %d/%d withdrawals %d/%d contexts", len(union.Audit), len(full.Audit), len(union.Withdrawals), len(full.Withdrawals), len(union.Contexts), len(full.Contexts))
	}
	if err = CheckRecoverySegments(set); err != nil {
		t.Fatal(err)
	}
	for position := range set {
		partial := append(append([]RecoveryRecord(nil), set[:position]...), set[position+1:]...)
		requireCode(t, CheckRecoverySegments(partial), "INTEGRITY_FAILURE")
	}
	// A context without any forgotten-record withdrawal cannot be exported alone.
	orphan := full
	orphan.Withdrawals = nil
	for _, w := range full.Withdrawals {
		if w.Kind != "forget" {
			orphan.Withdrawals = append(orphan.Withdrawals, w)
		}
	}
	_, err = splitRecoveryStagedForTest(t, store, orphan)
	requireCode(t, err, "INTEGRITY_FAILURE")
}
