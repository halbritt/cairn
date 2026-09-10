package core

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"github.com/google/uuid"
	"strings"
	"testing"
)

func TestEvidenceExpansionSharesBodyBudgetAndObservesExactSource(t *testing.T) {
	ctx := context.Background()
	op, _, record, evidence, index, dest := evidenceExpansionFixture(t, "local")
	repo := record.Scope.Repo
	body, err := op.Expand(ctx, ExpandRequest{uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle, nil}, dest)
	if err != nil {
		t.Fatal(err)
	}
	req := ExpandEvidenceRequest{evidence.Digest, uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle, evidence.ID, nil}
	result, err := op.ExpandEvidence(ctx, req, dest)
	if err != nil {
		t.Fatalf("agent cannot inspect indexed supporting evidence: %v", err)
	}
	if result.RecordID != record.RecordID || result.Version != record.Version || result.Evidence.Body != "compiler check passed with the explicit override" || result.Evidence.Digest != evidence.Digest || result.CreditsRemaining != body.CreditsRemaining-1 || result.BytesRemaining >= body.BytesRemaining {
		t.Fatalf("evidence pull: %+v", result)
	}
	again, err := op.ExpandEvidence(ctx, req, dest)
	if err != nil || again.CreditsRemaining != result.CreditsRemaining || again.BytesRemaining != result.BytesRemaining {
		t.Fatalf("retry: %+v %v", again, err)
	}
	report, err := op.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 100})
	if err != nil || len(report.Rows) != 1 || report.Rows[0].Usage != "expanded" || report.Rows[0].ExposureKind != "index" {
		t.Fatalf("usage: %+v %v", report, err)
	}
}

func evidenceExpansionFixture(t *testing.T, sensitivity string) (*Store, string, Record, Evidence, IndexResult, Destination) {
	return evidenceExpansionFixtureWithBody(t, sensitivity, "compiler check passed with the explicit override")
}

func evidenceExpansionFixtureWithBody(t *testing.T, sensitivity, body string) (*Store, string, Record, Evidence, IndexResult, Destination) {
	t.Helper()
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	writer := testStore(t, Channel{Principal: "evidence-pull-writer"})
	draft := projectNote(repo)
	draft.Sensitivity = sensitivity
	record, err := writer.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	evidence, err := op.CaptureEvidence(ctx, EvidenceRequest{RequestID: uuid.NewString(), Repo: repo, BodyBase64: base64.StdEncoding.EncodeToString([]byte(body)), Source: "bounded evidence pull fixture", Sensitivity: sensitivity})
	if err != nil {
		t.Fatal(err)
	}
	record, err = op.Promote(ctx, PromoteRequest{uuid.NewString(), record.RecordID, 1, root.ID, []string{evidence.ID}, "Support evidence inspection fixture", nil})
	if err != nil {
		t.Fatal(err)
	}
	dest := Destination{"local", true}
	if sensitivity == "shareable" {
		dest = Destination{"hosted", false}
	}
	index, err := op.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, dest)
	if err != nil || len(index.Handles) != 1 {
		t.Fatalf("index: %+v %v", index, err)
	}
	return op, root.ID, record, evidence, index, dest
}

func TestEvidenceExpansionCopyIsExcludedAndPurgedWithRecord(t *testing.T) {
	for name, span := range map[string]*EvidenceSpanRequest{"whole": nil, "span": {Offset: 0, Length: 4}} {
		t.Run(name, func(t *testing.T) {
			ctx := context.Background()
			op, root, record, evidence, index, dest := evidenceExpansionFixture(t, "local")
			req := ExpandEvidenceRequest{evidence.Digest, uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle, evidence.ID, span}
			if _, err := op.ExpandEvidence(ctx, req, dest); err != nil {
				t.Fatal(err)
			}
			preview, err := op.PreviewDeletion(ctx, record.RecordID)
			if err != nil {
				t.Fatal(err)
			}
			deletion, err := op.Forget(ctx, ForgetRequest{uuid.NewString(), record.RecordID, record.Version, root, preview.PreviewID})
			if err != nil {
				t.Fatal(err)
			}
			var excluded bool
			if err = op.pool.QueryRow(ctx, `SELECT payload_deleted_by=$1::uuid FROM cairn.mutation_request WHERE operation='expand-evidence' AND request_id=$2`, deletion.DeletionID, req.RequestID).Scan(&excluded); err != nil || !excluded {
				t.Fatalf("evidence response escaped deletion exclusion: %v %v", excluded, err)
			}
			if _, err = op.ExpandEvidence(ctx, req, dest); Code(err) != "PAYLOAD_UNAVAILABLE" {
				t.Fatalf("deleted retry: %v", err)
			}
			expectation, err := op.CaptureRecovery(ctx)
			if err != nil {
				t.Fatal(err)
			}
			if _, err = op.pool.Exec(ctx, `UPDATE cairn.mutation_request SET payload_deleted_by=NULL WHERE operation='expand-evidence' AND request_id=$1`, req.RequestID); err != nil {
				t.Fatal(err)
			}
			inspection, err := op.InspectRecovery(ctx, expectation)
			if err != nil {
				t.Fatal(err)
			}
			found := false
			for _, gap := range inspection.Gaps {
				found = found || (gap.SubjectID == record.RecordID && gap.Reason == "PAYLOAD_EXCLUSION_MISSING")
			}
			if !found {
				t.Fatal("recovery inspection ignored unexcluded evidence expansion copy")
			}
			if _, err = op.pool.Exec(ctx, `UPDATE cairn.mutation_request SET payload_deleted_by=$1 WHERE operation='expand-evidence' AND request_id=$2`, deletion.DeletionID, req.RequestID); err != nil {
				t.Fatal(err)
			}
			if _, err = op.PurgeDeletion(ctx, deletion.DeletionID); err != nil {
				t.Fatal(err)
			}
			var purged bool
			if err = op.pool.QueryRow(ctx, `SELECT response IS NULL FROM cairn.mutation_request WHERE operation='expand-evidence' AND request_id=$1`, req.RequestID).Scan(&purged); err != nil || !purged {
				t.Fatalf("retained evidence response not purged: %v %v", purged, err)
			}
			if _, err = op.ReadEvidence(ctx, evidence.ID); err != nil {
				t.Fatalf("independent captured evidence should remain explicit residual: %v", err)
			}
		})
	}
}

func TestEvidenceExpansionRetryRequiresSameCapturedBytes(t *testing.T) {
	for name, span := range map[string]*EvidenceSpanRequest{"whole": nil, "span": {Offset: 0, Length: 4}} {
		t.Run(name, func(t *testing.T) {
			ctx := context.Background()
			op, _, _, evidence, index, dest := evidenceExpansionFixture(t, "local")
			req := ExpandEvidenceRequest{evidence.Digest, uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle, evidence.ID, span}
			if _, err := op.ExpandEvidence(ctx, req, dest); err != nil {
				t.Fatal(err)
			}
			changed := []byte("different explicitly captured bytes")
			sum := sha256.Sum256(changed)
			if _, err := op.pool.Exec(ctx, `UPDATE cairn.evidence SET body=$2,digest=$3 WHERE evidence_id=$1`, evidence.ID, changed, sum[:]); err != nil {
				t.Fatal(err)
			}
			if _, err := op.ExpandEvidence(ctx, req, dest); Code(err) != "EVIDENCE_UNAVAILABLE" {
				t.Fatalf("retry returned cached evidence after identity bytes changed: %v", err)
			}
		})
	}
}

func TestEvidenceExpansionRefusesUnrelatedCallerAndDestination(t *testing.T) {
	for name, span := range map[string]*EvidenceSpanRequest{"whole": nil, "span": {Offset: 0, Length: 4}} {
		t.Run(name, func(t *testing.T) {
			ctx := context.Background()
			op, _, record, evidence, index, dest := evidenceExpansionFixture(t, "shareable")
			req := ExpandEvidenceRequest{evidence.Digest, uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle, evidence.ID, span}
			stranger := testStore(t, Channel{Principal: "stranger:" + record.Scope.Repo, Repo: record.Scope.Repo})
			_, err := stranger.ExpandEvidence(ctx, req, dest)
			requireCode(t, err, "AUTHORITY_DENIED")
			_, err = op.ExpandEvidence(ctx, req, Destination{"local", true})
			requireCode(t, err, "AUTHORITY_DENIED")
			unrelated := testEvidence(t, op, record.Scope.Repo)
			other := req
			other.EvidenceID = unrelated.ID
			other.ExpectedSHA256 = unrelated.Digest
			_, err = op.ExpandEvidence(ctx, other, dest)
			requireCode(t, err, "EVIDENCE_UNAVAILABLE")
			foreign := testEvidence(t, op, uuid.NewString())
			other.EvidenceID = foreign.ID
			other.ExpectedSHA256 = foreign.Digest
			_, err = op.ExpandEvidence(ctx, other, dest)
			requireCode(t, err, "EVIDENCE_UNAVAILABLE")
			result, err := op.ExpandEvidence(ctx, req, dest)
			if err != nil || result.CreditsRemaining != 3 {
				t.Fatalf("allowed shared evidence or refused-request budget: %+v %v", result, err)
			}
			// Restored/corrupt sensitivity must not be inherited from the shareable claim.
			if _, err = op.pool.Exec(ctx, `UPDATE cairn.evidence SET sensitivity='local' WHERE evidence_id=$1`, evidence.ID); err != nil {
				t.Fatal(err)
			}
			_, err = op.ExpandEvidence(ctx, req, dest)
			requireCode(t, err, "DESTINATION_PROHIBITED")
		})
	}
}

func TestEvidenceExpansionRejectsOversizeWithoutSpendingCredit(t *testing.T) {
	ctx := context.Background()
	op, _, _, evidence, index, dest := evidenceExpansionFixtureWithBody(t, "local", strings.Repeat("large evidence ", 3000))
	// Capture the large object before citing it; replacement after promotion is
	// citation divergence and must not stand in for a budget fixture.
	req := ExpandEvidenceRequest{evidence.Digest, uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle, evidence.ID, nil}
	result, err := op.ExpandEvidence(ctx, req, dest)
	requireCode(t, err, "BUDGET_REFUSED")
	if result.Evidence.Body != "" {
		t.Fatal("oversized failure returned partial evidence")
	}
	expansion, err := op.Expand(ctx, ExpandRequest{uuid.NewString(), req.ReceiptID, req.Handle, nil}, dest)
	if err != nil || expansion.CreditsRemaining != 3 {
		t.Fatalf("refused evidence spent credit: %+v %v", expansion, err)
	}
}

func TestEvidenceExpansionSharesConcurrentCreditsWithBodyPulls(t *testing.T) {
	ctx := context.Background()
	op, _, _, evidence, index, dest := evidenceExpansionFixture(t, "local")
	results := make(chan error, 8)
	for i := 0; i < 8; i++ {
		go func(evidencePull bool) {
			var err error
			if evidencePull {
				_, err = op.ExpandEvidence(ctx, ExpandEvidenceRequest{evidence.Digest, uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle, evidence.ID, nil}, dest)
			} else {
				_, err = op.Expand(ctx, ExpandRequest{uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle, nil}, dest)
			}
			results <- err
		}(i%2 == 0)
	}
	successes := 0
	for i := 0; i < 8; i++ {
		err := <-results
		if err == nil {
			successes++
		} else if Code(err) != "BUDGET_REFUSED" && Code(err) != "VERSION_CONFLICT" {
			t.Fatal(err)
		}
	}
	if successes != 4 {
		t.Fatalf("shared session admitted %d pulls, want four", successes)
	}
}

func TestEvidenceExpansionRetryRechecksWithdrawnClaim(t *testing.T) {
	for name, span := range map[string]*EvidenceSpanRequest{"whole": nil, "span": {Offset: 0, Length: 4}} {
		t.Run(name, func(t *testing.T) {
			ctx := context.Background()
			op, root, record, evidence, index, dest := evidenceExpansionFixture(t, "local")
			req := ExpandEvidenceRequest{evidence.Digest, uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle, evidence.ID, span}
			if _, err := op.ExpandEvidence(ctx, req, dest); err != nil {
				t.Fatal(err)
			}
			preview, err := op.PreviewRetraction(ctx, record.RecordID)
			if err != nil {
				t.Fatal(err)
			}
			if _, err = op.Retract(ctx, RetractRequest{uuid.NewString(), record.RecordID, record.Version, root, "Withdraw inspected evidence claim", preview.PreviewID}); err != nil {
				t.Fatal(err)
			}
			_, err = op.ExpandEvidence(ctx, req, dest)
			requireCode(t, err, "STALE_HANDLE")
		})
	}
}
