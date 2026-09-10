package core

import (
	"context"
	"encoding/base64"
	"github.com/google/uuid"
	"testing"
)

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
	replay, err := op.Recompile(ctx, RecompileRequest{historical.ReceiptID, query.Query})
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
