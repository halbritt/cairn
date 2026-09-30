package core

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestIncompleteRecoverySetCannotBeReexportedUntilCompleted(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	recoveryRoot(t, s)
	r, err := s.CaptureRecovery(ctx)
	if err != nil {
		t.Fatal(err)
	}
	r.Segment = &RecoverySegment{SetID: uuid.NewString(), Index: 1, Count: 2}
	r.SHA256, err = recoveryDigest(r)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ReapplyRecovery(ctx, RecoveryReapplyRequest{RequestID: uuid.NewString(), Record: r, Reason: "Retain only first segment of a required external set"}); err != nil {
		t.Fatal(err)
	}
	_, err = s.CaptureRecoverySet(ctx)
	requireCode(t, err, "INTEGRITY_FAILURE")
	r.Segment.Index = 2
	r.SHA256, err = recoveryDigest(r)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ReapplyRecovery(ctx, RecoveryReapplyRequest{RequestID: uuid.NewString(), Record: r, Reason: "Retain the missing original segment before recapture"}); err != nil {
		t.Fatal(err)
	}
	exported, err := s.CaptureRecoverySet(ctx)
	if err != nil || len(exported) != 1 || exported[0].Segment != nil {
		t.Fatalf("complete retained set cannot be re-exported: %+v %v", exported, err)
	}
	if err = exported[0].validate(); err != nil {
		t.Fatal(err)
	}
}

func TestRecoverySegmentPositionCannotChangeDigest(t *testing.T) {
	r := RecoveryRecord{Schema: recoverySchema, RootGrantID: uuid.NewString(), CapturedAt: time.Now().UTC(), Audit: []AuditMember{}, Withdrawals: []RecoveryWithdrawal{}, Contexts: []RecoveryContext{}, Segment: &RecoverySegment{SetID: uuid.NewString(), Index: 1, Count: 2}}
	r.SHA256, _ = recoveryDigest(r)
	second := r
	second.Segment = &RecoverySegment{SetID: r.Segment.SetID, Index: 2, Count: 2}
	second.SHA256, _ = recoveryDigest(second)
	if err := CheckRecoverySegments([]RecoveryRecord{r, second, r}); err != nil {
		t.Fatalf("identical duplicate refused: %v", err)
	}
	changed := r
	changed.CapturedAt = changed.CapturedAt.Add(time.Second)
	changed.SHA256, _ = recoveryDigest(changed)
	if err := changed.validate(); err != nil {
		t.Fatal(err)
	}
	requireCode(t, CheckRecoverySegments([]RecoveryRecord{r, second, changed}), "INTEGRITY_FAILURE")
}

func TestRecoveryReapplyRefusesConflictingSegmentBeforeRetention(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	recoveryRoot(t, s)
	r, err := s.CaptureRecovery(ctx)
	if err != nil {
		t.Fatal(err)
	}
	r.Segment = &RecoverySegment{SetID: uuid.NewString(), Index: 1, Count: 2}
	r.SHA256, err = recoveryDigest(r)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ReapplyRecovery(ctx, RecoveryReapplyRequest{RequestID: uuid.NewString(), Record: r, Reason: "Retain first segment"}); err != nil {
		t.Fatal(err)
	}
	changed := r
	changed.CapturedAt = changed.CapturedAt.Add(time.Second)
	changed.SHA256, err = recoveryDigest(changed)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.ReapplyRecovery(ctx, RecoveryReapplyRequest{RequestID: uuid.NewString(), Record: changed, Reason: "Conflicting content at the retained position must refuse"})
	requireCode(t, err, "INTEGRITY_FAILURE")
	var retained int
	if err = s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.recovery_application`).Scan(&retained); err != nil || retained != 1 {
		t.Fatalf("conflicting application changed retained state: count=%d err=%v", retained, err)
	}
	r.Segment.Index = 2
	r.SHA256, err = recoveryDigest(r)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ReapplyRecovery(ctx, RecoveryReapplyRequest{RequestID: uuid.NewString(), Record: r, Reason: "Complete original set after refused conflict"}); err != nil {
		t.Fatal(err)
	}
	if _, err = s.CaptureRecoverySet(ctx); err != nil {
		t.Fatalf("refused conflict poisoned subsequent complete export: %v", err)
	}
}
