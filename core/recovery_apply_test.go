package core

import (
	"context"
	"reflect"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// Synthetic external expectations stand for events after the backup. The
// separate dump/restore drill must establish the real capture/reapply path.
func externalWithdrawals(t *testing.T, s *Store, withdrawals ...RecoveryWithdrawal) RecoveryRecord {
	t.Helper()
	record, err := s.CaptureRecovery(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	for _, w := range withdrawals {
		record.Withdrawals = append(record.Withdrawals, w)
		record.Audit = append(record.Audit, AuditMember{w.EventID, strings.Repeat("a", 64)})
	}
	record.SHA256, err = recoveryDigest(record)
	if err != nil {
		t.Fatal(err)
	}
	return record
}
func recoveryRoot(t *testing.T, s *Store) Grant {
	t.Helper()
	root, err := s.Bootstrap(context.Background(), BootstrapRequest{uuid.NewString(), "Bootstrap isolated recovery test"})
	if err != nil {
		t.Fatal(err)
	}
	return root
}

func TestRecoveryReapplyRestrictsWithoutInventingHistory(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	root := recoveryRoot(t, s)
	repo := uuid.NewString()
	grant, err := s.Grant(ctx, GrantRequest{RequestID: uuid.NewString(), ParentID: root.ID, Principal: "agent:recovery", Repo: repo, Capabilities: []string{"issue"}, Reason: "Grant synthetic delegated authority"})
	if err != nil {
		t.Fatal(err)
	}
	instructionDraft := projectNote(repo)
	instructionDraft.Kind = "instruction"
	instruction, err := s.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: instructionDraft, GrantID: root.ID, PolicyKey: "recovery-instruction", Reason: "Issue synthetic recovery instruction"})
	if err != nil {
		t.Fatal(err)
	}
	note, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	draft := projectNote(repo)
	draft.Relations = []RecordRelation{{note.RecordID, note.Version, "derived_from"}}
	dependent, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	source := externalWithdrawals(t, s, RecoveryWithdrawal{"revoke_grant", grant.ID, repo, uuid.NewString()}, RecoveryWithdrawal{"retract", instruction.RecordID, repo, uuid.NewString()}, RecoveryWithdrawal{"forget", note.RecordID, repo, uuid.NewString()})
	before, err := s.InspectRecovery(ctx, source)
	if err != nil || before.Consistent {
		t.Fatalf("unrecovered: %+v %v", before, err)
	}
	req := RecoveryReapplyRequest{uuid.NewString(), source, "Reapply trusted external withdrawals to isolated restore"}
	result, err := s.ReapplyRecovery(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Actions) != 3 {
		t.Fatalf("actions: %+v", result)
	}
	for _, a := range result.Actions {
		if a.Outcome != "reapplied" || a.CurrentEventID == "" || a.CurrentEventID == a.EventID {
			t.Fatalf("action: %+v", a)
		}
	}
	retry, err := s.ReapplyRecovery(ctx, req)
	if err != nil || !reflect.DeepEqual(retry, result) {
		t.Fatalf("retry: %+v %v", retry, err)
	}
	req.Reason = "Different intent on same request"
	_, err = s.ReapplyRecovery(ctx, req)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	after, err := s.InspectRecovery(ctx, source)
	if err != nil {
		t.Fatal(err)
	}
	if after.Consistent || len(after.Gaps) != 3 {
		t.Fatalf("history gaps: %+v", after)
	}
	for _, gap := range after.Gaps {
		if gap.Reason != "AUDIT_MISSING" {
			t.Fatalf("restriction gap: %+v", gap)
		}
	}
	pkg, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	for _, sel := range pkg.Semantic.Selected {
		if sel.Record.RecordID == note.RecordID || sel.Record.RecordID == dependent.RecordID || sel.Record.RecordID == instruction.RecordID {
			t.Fatalf("withdrawn memory selected: %+v", sel)
		}
	}
	exported, err := s.CaptureRecovery(ctx)
	if err != nil {
		t.Fatal(err)
	}
	byEvent := map[string]string{}
	for _, m := range exported.Audit {
		byEvent[m.EventID] = m.Digest
	}
	for _, m := range source.Audit {
		if byEvent[m.EventID] != m.Digest {
			t.Fatalf("prior audit expectation changed: %+v", m)
		}
	}
	fresh, err := s.InspectRecovery(ctx, exported)
	if err != nil || fresh.Consistent || len(fresh.Gaps) != 3 {
		t.Fatalf("new export erased known missing history: %+v %v", fresh, err)
	}
	// A different request is a new adoption audit, without repeated withdrawals.
	req.RequestID = uuid.NewString()
	next, err := s.ReapplyRecovery(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	for _, a := range next.Actions {
		if a.Outcome != "already_restricted" {
			t.Fatalf("repeated restriction: %+v", a)
		}
	}
	// Payload purge remains resumable under the ordinary deletion worker.
	for _, a := range result.Actions {
		if a.DeletionID != "" {
			d, err := s.PurgeDeletion(ctx, a.DeletionID)
			if err != nil || d.State == "pending" {
				t.Fatalf("purge: %+v %v", d, err)
			}
		}
	}
}

func TestRecoveryReapplyRollsBackRestrictionsOnLateScopeMismatch(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	root := recoveryRoot(t, s)
	repo := uuid.NewString()
	grant, err := s.Grant(ctx, GrantRequest{RequestID: uuid.NewString(), ParentID: root.ID, Principal: "agent:rollback", Repo: repo, Capabilities: []string{"issue"}, Reason: "Grant rollback fixture"})
	if err != nil {
		t.Fatal(err)
	}
	note, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	source := externalWithdrawals(t, s, RecoveryWithdrawal{"revoke_grant", grant.ID, repo, uuid.NewString()}, RecoveryWithdrawal{"forget", note.RecordID, "wrong-repo", uuid.NewString()})
	_, err = s.ReapplyRecovery(ctx, RecoveryReapplyRequest{uuid.NewString(), source, "Test all-or-nothing withdrawal reapplication"})
	requireCode(t, err, "INTEGRITY_FAILURE")
	var revoked bool
	var apps int
	if err = s.pool.QueryRow(ctx, `SELECT revoked FROM cairn.authority_grant WHERE grant_id=$1`, grant.ID).Scan(&revoked); err != nil {
		t.Fatal(err)
	}
	if err = s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.recovery_application`).Scan(&apps); err != nil {
		t.Fatal(err)
	}
	if revoked || apps != 0 {
		t.Fatalf("partial restriction committed: revoked=%v applications=%d", revoked, apps)
	}
}

func TestRecoveryReapplyRequiresLiveRootAndTrustedMatchingExpectations(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	root := recoveryRoot(t, s)
	source := externalWithdrawals(t, s, RecoveryWithdrawal{"revoke_grant", uuid.NewString(), "missing-repo", uuid.NewString()})
	req := RecoveryReapplyRequest{uuid.NewString(), source, "Test recovery authority and absence"}
	for _, channel := range []Channel{{Principal: s.channel.Principal}, {Principal: s.channel.Principal, Operator: true, Repo: "limited"}, {Principal: "other:operator", Operator: true}} {
		caller := &Store{pool: s.pool, channel: channel}
		_, err := caller.ReapplyRecovery(ctx, req)
		requireCode(t, err, "AUTHORITY_DENIED")
	}
	bad := req
	bad.Record.RootGrantID = uuid.NewString()
	bad.Record.SHA256, _ = recoveryDigest(bad.Record)
	_, err := s.ReapplyRecovery(ctx, bad)
	requireCode(t, err, "INTEGRITY_FAILURE")
	result, err := s.ReapplyRecovery(ctx, req)
	if err != nil || len(result.Actions) != 1 || result.Actions[0].Outcome != "absent" {
		t.Fatalf("absent: %+v %v", result, err)
	}
	// Cached success does not bypass current root authority.
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.authority_grant SET revoked=true WHERE grant_id=$1`, root.ID); err != nil {
		t.Fatal(err)
	}
	_, err = s.ReapplyRecovery(ctx, req)
	requireCode(t, err, "AUTHORITY_DENIED")
}

func TestRecoveryReapplyRejectsChangedExpectationsAndUnsafeCustody(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	recoveryRoot(t, s)
	repo := uuid.NewString()
	note, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	source := externalWithdrawals(t, s, RecoveryWithdrawal{"forget", note.RecordID, repo, uuid.NewString()})
	receipt := uuid.NewString()
	source.Contexts = []RecoveryContext{{RecordID: note.RecordID, ManagedContext: ManagedContext{ReceiptID: receipt, OwnershipID: uuid.NewString(), Directory: "relative/" + receipt, DirectoryDevice: "1", DirectoryInode: "2", BodySHA256: strings.Repeat("a", 64)}}}
	source.SHA256, err = recoveryDigest(source)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.ReapplyRecovery(ctx, RecoveryReapplyRequest{uuid.NewString(), source, "Reject unsafe imported custody without partial deletion"})
	requireCode(t, err, "INVALID_REQUEST")
	var lifecycle string
	if err = s.pool.QueryRow(ctx, `SELECT lifecycle FROM cairn.memory_record WHERE record_id=$1`, note.RecordID).Scan(&lifecycle); err != nil {
		t.Fatal(err)
	}
	if lifecycle != "active" {
		t.Fatalf("late custody refusal committed forgetting: %s", lifecycle)
	}
	source.Contexts = nil
	source.SHA256, err = recoveryDigest(source)
	if err != nil {
		t.Fatal(err)
	}
	result, err := s.ReapplyRecovery(ctx, RecoveryReapplyRequest{uuid.NewString(), source, "Apply isolated deletion fixture"})
	if err != nil {
		t.Fatal(err)
	}
	// A second source cannot redefine the already-retained digest of an absent
	// source event, even when it supplies its own valid outer checksum.
	changed := source
	changed.Audit = append([]AuditMember(nil), source.Audit...)
	changed.Audit[len(changed.Audit)-1].Digest = strings.Repeat("b", 64)
	changed.SHA256, err = recoveryDigest(changed)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.ReapplyRecovery(ctx, RecoveryReapplyRequest{uuid.NewString(), changed, "Try contradictory external expectations"})
	requireCode(t, err, "INTEGRITY_FAILURE")
	// The original immutable application remains inspectable after a rejected
	// replacement; its associated source event is still absent from local audit.
	var event string
	if err = s.pool.QueryRow(ctx, `SELECT event_id::text FROM cairn.recovery_application WHERE application_id=$1`, result.ApplicationID).Scan(&event); err != nil || event != result.EventID {
		t.Fatalf("application changed: %s %v", event, err)
	}
}

func TestRecoveryReapplyRollsBackWhenNewAuditExceedsExportBudget(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	recoveryRoot(t, s)
	repo := uuid.NewString()
	note, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	source := externalWithdrawals(t, s, RecoveryWithdrawal{"forget", note.RecordID, repo, uuid.NewString()})
	for len(source.Audit) < 10000 {
		source.Audit = append(source.Audit, AuditMember{uuid.NewString(), strings.Repeat("a", 64)})
	}
	source.SHA256, err = recoveryDigest(source)
	if err != nil {
		t.Fatal(err)
	}
	if err = source.validate(); err != nil {
		t.Fatal(err)
	}
	_, err = s.ReapplyRecovery(ctx, RecoveryReapplyRequest{uuid.NewString(), source, "Refuse application that cannot retain its new audit in an export"})
	requireCode(t, err, "BUDGET_REFUSED")
	var lifecycle string
	if err = s.pool.QueryRow(ctx, `SELECT lifecycle FROM cairn.memory_record WHERE record_id=$1`, note.RecordID).Scan(&lifecycle); err != nil {
		t.Fatal(err)
	}
	if lifecycle != "active" {
		t.Fatalf("unexportable deletion committed: %s", lifecycle)
	}
}
