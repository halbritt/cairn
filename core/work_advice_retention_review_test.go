package core

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

// These cases create domain state through the real generated-proposal workflow.
// SQL changes only timestamps to cross retention boundaries without a day-long
// sleep; it never inserts payloads, changes eligibility, or invents process IDs.
func TestWorkAdviceReviewExpiredInputHiddenBeforeSweep(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	s := restoreTestStore(t)
	sentinel := "EXPIRED_ADVICE_INPUT_" + uuid.NewString()
	f := newAdviceReviewCase(t, ctx, s, sentinel)
	controlText := "UNEXPIRED_ADVICE_INPUT_" + uuid.NewString()
	control := newAdviceReviewCase(t, ctx, s, controlText)
	before, err := s.GetAdviceJob(ctx, f.job.JobUUID)
	if err != nil || !strings.Contains(string(before.InputSnapshot), sentinel) {
		t.Fatalf("actual generated input was not retained before expiry: %v", err)
	}
	tag, err := s.pool.Exec(ctx, `UPDATE cairn.work_advice_job
		SET input_expires_at=clock_timestamp()-interval '1 second' WHERE job_uuid=$1`, f.job.JobUUID)
	if err != nil || tag.RowsAffected() != 1 {
		t.Fatalf("could not age exact generated input deadline: %v", err)
	}
	for _, reader := range []struct {
		name string
		get  func(uuid.UUID) (*WorkAdviceJob, error)
		id   uuid.UUID
		keep uuid.UUID
	}{
		{"job UUID", func(id uuid.UUID) (*WorkAdviceJob, error) { return s.GetAdviceJob(ctx, id) }, f.job.JobUUID, control.job.JobUUID},
		{"request UUID", func(id uuid.UUID) (*WorkAdviceJob, error) { return s.GetAdviceJobByRequestUUID(ctx, id) }, f.job.RequestUUID, control.job.RequestUUID},
	} {
		job, err := reader.get(reader.id)
		if err != nil || job == nil || job.JobUUID != f.job.JobUUID || job.RequestUUID != f.job.RequestUUID {
			t.Errorf("%s read lost retained expired-job identity: %v", reader.name, err)
			continue
		}
		encoded, err := json.Marshal(job)
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(encoded), sentinel) {
			t.Errorf("%s read exposed expired canonical input before any sweep", reader.name)
		}
		if len(job.InputSnapshot) != 0 || job.InputUnavailableReason == nil || *job.InputUnavailableReason != "EXPIRED" {
			t.Errorf("%s read did not mark the expired input unavailable", reader.name)
		}
		kept, err := reader.get(reader.keep)
		if err != nil || kept == nil || kept.JobUUID != control.job.JobUUID || !strings.Contains(string(kept.InputSnapshot), controlText) {
			t.Errorf("%s read damaged the independent unexpired input control: %v", reader.name, err)
		}
	}
}

func TestWorkAdviceReviewSweepPurgesUnappliedTextKeepsAppliedForgetDependency(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	s := restoreTestStore(t)
	unappliedText := "UNAPPLIED_ADVICE_RETENTION_" + uuid.NewString()
	unapplied := newAdviceReviewCase(t, ctx, s, unappliedText)
	appliedText := "APPLIED_ADVICE_RETENTION_" + uuid.NewString()
	applied := newAdviceReviewCase(t, ctx, s, appliedText)
	controlText := "FRESH_ADVICE_RETENTION_" + uuid.NewString()
	control := newAdviceReviewCase(t, ctx, s, controlText)
	receipt, err := s.ApplyAdviceProposal(ctx, &WorkAdviceApplicationReceipt{RequestUUID: uuid.New(), ProposalUUID: applied.proposal.ProposalUUID},
		applied.proposal.ReviewVersion, []uuid.UUID{applied.actions[0].ActionUUID})
	if err != nil || receipt == nil || receipt.ReceiptUUID == uuid.Nil {
		t.Fatalf("generated applied-annotation positive control failed: %v", err)
	}
	var annotationID uuid.UUID
	if err := s.pool.QueryRow(ctx, `SELECT annotation_id FROM cairn.work_annotation
		WHERE binding_id=$1 AND plane_item_uuid=$2 AND kind='progress' AND state='active' AND body=$3`,
		applied.project.BindingID, applied.item, appliedText).Scan(&annotationID); err != nil {
		t.Fatalf("actual generated annotation missing before retention: %v", err)
	}
	for _, f := range []*adviceReviewCase{unapplied, applied} {
		tag, err := s.pool.Exec(ctx, `UPDATE cairn.work_advice_job
			SET created_at=clock_timestamp()-interval '8 days', input_expires_at=clock_timestamp()-interval '7 days'
			WHERE job_uuid=$1`, f.job.JobUUID)
		if err != nil || tag.RowsAffected() != 1 {
			t.Fatalf("could not age exact generated job: %v", err)
		}
		tag, err = s.pool.Exec(ctx, `UPDATE cairn.work_advice_proposal
			SET created_at=clock_timestamp()-interval '8 days', expires_at=clock_timestamp()-interval '7 days'
			WHERE proposal_uuid=$1`, f.proposal.ProposalUUID)
		if err != nil || tag.RowsAffected() != 1 {
			t.Fatalf("could not age exact generated proposal: %v", err)
		}
		tag, err = s.pool.Exec(ctx, `UPDATE cairn.work_advice_action
			SET created_at=clock_timestamp()-interval '8 days' WHERE action_uuid=$1 AND proposal_uuid=$2`,
			f.actions[0].ActionUUID, f.proposal.ProposalUUID)
		if err != nil || tag.RowsAffected() != 1 {
			t.Fatalf("could not age exact generated action: %v", err)
		}
	}
	if err := s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: "work-advice/1", Status: "disabled"}); err != nil {
		t.Fatal(err)
	}
	if swept, err := s.SweepExpiredAdvice(ctx); err != nil || swept == 0 {
		t.Fatalf("actual retention sweep while generation disabled failed: count=%d error=%v", swept, err)
	}
	for _, row := range []struct{ label, query string }{
		{"unapplied proposal", `SELECT to_jsonb(p)::text FROM cairn.work_advice_proposal p WHERE proposal_uuid=$1`},
		{"unapplied action", `SELECT to_jsonb(a)::text FROM cairn.work_advice_action a WHERE proposal_uuid=$1`},
	} {
		var stored string
		if err := s.pool.QueryRow(ctx, row.query, unapplied.proposal.ProposalUUID).Scan(&stored); err != nil {
			t.Errorf("%s lost retained identity before 30 days: %v", row.label, err)
		} else if strings.Contains(stored, unappliedText) {
			t.Errorf("%s retained text past the seven-day limit", row.label)
		}
	}
	for _, f := range []*adviceReviewCase{unapplied, applied} {
		var snapshot []byte
		var manifest []byte
		if err := s.pool.QueryRow(ctx, `SELECT input_snapshot,input_manifest FROM cairn.work_advice_job WHERE job_uuid=$1`, f.job.JobUUID).Scan(&snapshot, &manifest); err != nil {
			t.Fatal(err)
		}
		if len(snapshot) != 0 {
			t.Error("retention sweep kept expired stored input")
		}
		if !strings.Contains(string(manifest), f.source.RecordID) {
			t.Error("retention erased the known source dependency while derived text remains")
		}
	}
	var body string
	if err := s.pool.QueryRow(ctx, `SELECT body FROM cairn.work_annotation WHERE annotation_id=$1`, annotationID).Scan(&body); err != nil || body != appliedText {
		t.Fatalf("seven-day proposal retention damaged ordinary applied annotation: %v", err)
	}
	_, controlActions, err := s.GetAdviceProposal(ctx, control.proposal.ProposalUUID)
	if err != nil || len(controlActions) != 1 || !strings.Contains(string(controlActions[0].TargetPayload), controlText) {
		t.Fatalf("retention damaged independent fresh proposal: %v", err)
	}
	grant, err := s.Bootstrap(ctx, BootstrapRequest{RequestID: uuid.NewString(), Reason: "Owned disposable post-retention Forget regression"})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := s.PreviewDeletion(ctx, applied.source.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: applied.source.RecordID, ExpectedVersion: applied.source.Version,
		GrantID: grant.ID, PreviewID: preview.PreviewID}); err != nil {
		t.Fatal(err)
	}
	if err := s.pool.QueryRow(ctx, `SELECT body FROM cairn.work_annotation WHERE annotation_id=$1`, annotationID).Scan(&body); err != nil {
		t.Fatalf("post-retention Forget lost applied annotation identity: %v", err)
	} else if strings.Contains(body, appliedText) {
		t.Error("post-retention Forget left known derived annotation text")
	}
	var copies int
	if err := s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.work_annotation_history WHERE annotation_id=$1 AND position($2 in body)>0`, annotationID, appliedText).Scan(&copies); err != nil {
		t.Fatal(err)
	} else if copies != 0 {
		t.Errorf("post-retention Forget left %d known derived history payloads", copies)
	}
}
