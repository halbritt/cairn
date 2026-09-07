package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func TestRecoveryRecordChecksWithdrawalStateBeyondAuditPresence(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	grant, err := s.Grant(ctx, GrantRequest{RequestID: uuid.NewString(), ParentID: root.ID, Principal: "recovery-agent:" + repo, Repo: repo, Capabilities: []string{"issue"}, Reason: "Grant recovery inspection fixture"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.RevokeGrant(ctx, RevokeGrantRequest{RequestID: uuid.NewString(), GrantID: grant.ID, AuthorityID: root.ID, ExpectedVersion: grant.Version, Reason: "Revoke recovery inspection fixture"}); err != nil {
		t.Fatal(err)
	}
	preview, err := s.PreviewDeletion(ctx, r.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: r.RecordID, ExpectedVersion: r.Version, GrantID: root.ID, PreviewID: preview.PreviewID}); err != nil {
		t.Fatal(err)
	}
	snapshot, err := s.CaptureRecovery(ctx)
	if err != nil {
		t.Fatal(err)
	}
	report, err := s.InspectRecovery(ctx, snapshot)
	if err != nil || !report.Consistent {
		t.Fatalf("current recovery record: %+v %v", report, err)
	}
	// Fault only mutable grant state, retaining the authentic revocation audit.
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.authority_grant SET revoked=false WHERE grant_id=$1`, grant.ID); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := s.pool.Exec(ctx, `UPDATE cairn.authority_grant SET revoked=true WHERE grant_id=$1`, grant.ID); err != nil {
			t.Error(err)
		}
	})
	report, err = s.InspectRecovery(ctx, snapshot)
	if err != nil || report.Consistent {
		t.Fatalf("revived grant accepted because audit still exists: %+v %v", report, err)
	}
	found := false
	for _, gap := range report.Gaps {
		if gap.SubjectID == grant.ID && gap.Reason == "GRANT_REVIVED" {
			found = true
		}
	}
	if !found {
		t.Fatalf("missing grant state explanation: %+v", report)
	}
}

func TestRecoveryRecordRejectsTamperingWrongRootAndScopedChannels(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	snapshot, err := s.CaptureRecovery(ctx)
	if err != nil {
		t.Fatal(err)
	}
	tampered := snapshot
	tampered.CapturedAt = tampered.CapturedAt.Add(1)
	_, err = s.InspectRecovery(ctx, tampered)
	requireCode(t, err, "INTEGRITY_FAILURE")
	snapshot.RootGrantID = uuid.NewString()
	snapshot.SHA256, err = recoveryDigest(snapshot)
	if err != nil {
		t.Fatal(err)
	}
	report, err := s.InspectRecovery(ctx, snapshot)
	if err != nil || report.Consistent || len(report.Gaps) != 1 || report.Gaps[0].Reason != "ROOT_MISMATCH" {
		t.Fatalf("wrong root: %+v %v", report, err)
	}
	for _, channel := range []Channel{{Principal: "agent:recovery"}, {Principal: "operator:scoped", Operator: true, Repo: "fixture:scope"}} {
		agent := testStore(t, channel)
		_, err = agent.CaptureRecovery(ctx)
		requireCode(t, err, "AUTHORITY_DENIED")
		_, err = agent.InspectRecovery(ctx, snapshot)
		requireCode(t, err, "AUTHORITY_DENIED")
	}
}

func TestRecoveryRecordDetectsMissingPayloadExclusion(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	request := CreateRequest{uuid.NewString(), projectNote(uuid.NewString())}
	r, err := s.Create(ctx, request)
	if err != nil {
		t.Fatal(err)
	}
	preview, err := s.PreviewDeletion(ctx, r.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	deleted, err := s.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: r.RecordID, ExpectedVersion: r.Version, GrantID: root.ID, PreviewID: preview.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err := s.CaptureRecovery(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.mutation_request SET payload_deleted_by=NULL WHERE request_id=$1`, request.RequestID); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := s.pool.Exec(ctx, `UPDATE cairn.mutation_request SET payload_deleted_by=$1 WHERE request_id=$2`, deleted.DeletionID, request.RequestID); err != nil {
			t.Error(err)
		}
	})
	report, err := s.InspectRecovery(ctx, snapshot)
	if err != nil {
		t.Fatal(err)
	}
	for _, gap := range report.Gaps {
		if gap.SubjectID == r.RecordID && gap.Reason == "PAYLOAD_EXCLUSION_MISSING" {
			return
		}
	}
	t.Fatalf("missing cached payload exclusion accepted: %+v", report)
}
