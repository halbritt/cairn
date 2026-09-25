package core

import (
	"context"
	"encoding/base64"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

func TestEvidenceCheckHistoryPagesBeyondWholeHistoryLimit(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	evidence := testEvidence(t, op, repo)
	for generation := 1; generation <= 1001; generation++ {
		check, err := op.CheckEvidence(ctx, EvidenceCheckRequest{RequestID: uuid.NewString(), EvidenceID: evidence.ID})
		if err != nil || check.Generation != generation {
			t.Fatalf("check generation %d: %+v %v", generation, check, err)
		}
	}
	_, err := op.EvidenceChecks(ctx, evidence.ID)
	requireCode(t, err, "BUDGET_REFUSED")

	after, seen := 0, 0
	for {
		page, err := op.EvidenceChecksPage(ctx, evidence.ID, after, 127)
		if err != nil {
			t.Fatal(err)
		}
		if len(page.Checks) == 0 || len(page.Checks) > 127 {
			t.Fatalf("invalid page length %d after %d", len(page.Checks), after)
		}
		for _, check := range page.Checks {
			seen++
			if check.Generation != seen {
				t.Fatalf("generation %d at position %d", check.Generation, seen)
			}
		}
		if page.NextAfter != seen {
			t.Fatalf("cursor %d after generation %d", page.NextAfter, seen)
		}
		if !page.More {
			break
		}
		after = page.NextAfter
	}
	if seen != 1001 {
		t.Fatalf("read %d of 1001 checks", seen)
	}
	outsider := testStore(t, Channel{Principal: "check-page-outsider", Repo: uuid.NewString()})
	_, err = outsider.EvidenceChecksPage(ctx, evidence.ID, 0, 127)
	requireCode(t, err, "AUTHORITY_DENIED")
}

func TestEvidenceRecheckInvalidatesMoreThanThousandCitingRecords(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	evidence := testEvidence(t, op, repo)
	const citations = 1001
	ids := make([]string, citations)
	for i := range ids {
		record, err := op.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
		if err != nil {
			t.Fatal(err)
		}
		ids[i] = record.RecordID
	}
	// Populate the large citation set directly to isolate the check boundary
	// from the qualification workflow; all references name retained versions.
	_, err := op.pool.CopyFrom(ctx, pgx.Identifier{"cairn", "evidence_ref"},
		[]string{"record_id", "version", "evidence_id"},
		pgx.CopyFromSlice(len(ids), func(i int) ([]any, error) {
			return []any{ids[i], 1, evidence.ID}, nil
		}))
	if err != nil {
		t.Fatal(err)
	}
	req := EvidenceCheckRequest{uuid.NewString(), evidence.ID}
	check, err := op.CheckEvidence(ctx, req)
	if err != nil || check.AffectedRecords != citations || check.Generation != 1 {
		t.Fatalf("large evidence check: %+v %v", check, err)
	}
	retry, err := op.CheckEvidence(ctx, req)
	if err != nil || retry.Generation != check.Generation || retry.AffectedRecords != citations {
		t.Fatalf("large evidence check retry: %+v %v", retry, err)
	}
	var invalidated int
	if err := op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.memory_record m JOIN cairn.evidence_ref r USING(record_id) WHERE r.evidence_id=$1 AND m.use_generation=1`, evidence.ID).Scan(&invalidated); err != nil {
		t.Fatal(err)
	}
	if invalidated != citations {
		t.Fatalf("invalidated %d of %d citing records", invalidated, citations)
	}
}

func TestEvidenceRefreshPersistsGenerationAndInvalidatesPreview(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	writer := testStore(t, Channel{Principal: "evidence-check-writer"})
	a, err := writer.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	evidence := testEvidence(t, op, repo)
	b, err := op.Promote(ctx, PromoteRequest{uuid.NewString(), a.RecordID, 1, root.ID, []string{evidence.ID}, "Promote evidence check fixture", nil})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := op.PreviewRetraction(ctx, b.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	req := EvidenceCheckRequest{uuid.NewString(), evidence.ID}
	first, err := op.CheckEvidence(ctx, req)
	if err != nil || first.Generation != 1 || first.State != "resolvable" {
		t.Fatalf("check: %+v %v", first, err)
	}
	retry, err := op.CheckEvidence(ctx, req)
	if err != nil || retry.Generation != 1 {
		t.Fatal("transport retry repeated check")
	}
	_, err = op.Retract(ctx, RetractRequest{uuid.NewString(), b.RecordID, b.Version, root.ID, "Refuse changed evidence preview", preview.PreviewID})
	requireCode(t, err, "STALE_PREVIEW")
	query := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "compiler", Purpose: "planning", AvailableTokens: 64000}
	historical, err := op.Compile(ctx, query, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = op.pool.Exec(ctx, `UPDATE cairn.evidence SET body='altered fixture bytes' WHERE evidence_id=$1`, evidence.ID); err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	changed, err := op.CheckEvidence(ctx, req)
	if err != nil || changed.Generation != 2 || changed.State != "divergent" {
		t.Fatalf("changed evidence: %+v %v", changed, err)
	}
	replay, err := op.Recompile(ctx, RecompileRequest{ReceiptID: historical.ReceiptID, Query: query.Query})
	if err != nil || replay.Seal != historical.Seal {
		t.Fatalf("refresh rewrote historical gate: %v", err)
	}
	query.RequestID = uuid.NewString()
	live, err := op.Compile(ctx, query, Destination{"local", true})
	if err != nil || len(live.Semantic.Selected) != 0 {
		t.Fatal("divergent evidence stayed consequential")
	}
	history, err := op.EvidenceChecks(ctx, evidence.ID)
	if err != nil || len(history) != 2 {
		t.Fatalf("check history missing: %v", err)
	}
	outsider := testStore(t, Channel{Principal: "check-outsider", Repo: uuid.NewString()})
	_, err = outsider.CheckEvidence(ctx, req)
	requireCode(t, err, "AUTHORITY_DENIED")
}

func TestEvidenceInspectionPreservesExplicitBinaryBytes(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	original := []byte{0xff, 0x00, 0x01}
	evidence, err := op.CaptureEvidence(ctx, EvidenceRequest{RequestID: uuid.NewString(), Repo: uuid.NewString(), BodyBase64: base64.StdEncoding.EncodeToString(original), Source: "explicit binary fixture", Sensitivity: "local"})
	if err != nil {
		t.Fatal(err)
	}
	inspected, err := op.ReadEvidence(ctx, evidence.ID)
	if err != nil {
		t.Fatal(err)
	}
	if inspected.Body != "" || inspected.BodyBase64 != "/wAB" {
		t.Fatalf("binary evidence was not lossless: %+v", inspected)
	}
}

func TestBinaryEvidenceRequiresLosslessRequestIntent(t *testing.T) {
	ctx := context.Background()
	store := testStore(t, Channel{Principal: "binary-capture-intent"})
	request := EvidenceRequest{RequestID: uuid.NewString(), Repo: uuid.NewString(), Source: "explicit binary intent"}
	// JSON request hashing would map both invalid strings to the same replacement
	// character. Refuse them before reservation and require the lossless input form.
	for _, body := range []string{string([]byte{0xff}), string([]byte{0xfe})} {
		request.Body = body
		_, err := store.CaptureEvidence(ctx, request)
		requireCode(t, err, "INVALID_REQUEST")
	}
	request.Body, request.BodyBase64 = "", "/w=="
	first, err := store.CaptureEvidence(ctx, request)
	if err != nil {
		t.Fatal(err)
	}
	again, err := store.CaptureEvidence(ctx, request)
	if err != nil || again.ID != first.ID {
		t.Fatalf("retry: %+v %v", again, err)
	}
	request.BodyBase64 = "/g=="
	_, err = store.CaptureEvidence(ctx, request)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
}
