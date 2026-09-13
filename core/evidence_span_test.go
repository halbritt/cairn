package core

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"reflect"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestEvidenceSpanMakesLargeCapturedSourceReadable(t *testing.T) {
	ctx := context.Background()
	marker := "TAIL: use the explicit override"
	body := "SOURCE START\n" + strings.Repeat("retained source line\n", 3000) + marker
	op, _, _, evidence, index, dest := evidenceExpansionFixtureWithBody(t, "shareable", body)
	req := ExpandEvidenceRequest{evidence.Digest, uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle, evidence.ID, nil}
	_, err := op.ExpandEvidence(ctx, req, dest)
	requireCode(t, err, "BUDGET_REFUSED")
	req.Span = &EvidenceSpanRequest{Offset: 0, Length: 128}
	firstRequestID := req.RequestID
	first, err := op.ExpandEvidence(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	assertEvidenceSpan(t, first, body, 0, 128)
	if first.CreditsRemaining != 3 {
		t.Fatalf("failed full pull spent credit: %+v", first)
	}
	again, err := op.ExpandEvidence(ctx, req, dest)
	if err != nil || !reflect.DeepEqual(first, again) {
		t.Fatalf("span retry changed response: %+v %v", again, err)
	}
	req.Span = &EvidenceSpanRequest{Offset: len(body) - len(marker), Length: 128}
	_, err = op.ExpandEvidence(ctx, req, dest)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	req.RequestID = uuid.NewString()
	tail, err := op.ExpandEvidence(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	assertEvidenceSpan(t, tail, body, len(body)-len(marker), len(body))
	if tail.Span.Body != marker || tail.CreditsRemaining != 2 || tail.BytesRemaining >= first.BytesRemaining {
		t.Fatalf("tail read: %+v", tail)
	}
	var observations int
	if err := op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.usage_observation WHERE receipt_id=$1 AND method='authorized-evidence-span-pull/1'`, req.ReceiptID).Scan(&observations); err != nil || observations != 2 {
		t.Fatalf("span observations: %d %v", observations, err)
	}
	// A change outside the requested prefix must invalidate its cached response.
	changed := []byte(body[:len(body)-1] + "!")
	digest := sha256.Sum256(changed)
	if _, err := op.pool.Exec(ctx, `UPDATE cairn.evidence SET body=$2,digest=$3 WHERE evidence_id=$1`, evidence.ID, changed, digest[:]); err != nil {
		t.Fatal(err)
	}
	req.RequestID = firstRequestID
	req.Span = &EvidenceSpanRequest{Offset: 0, Length: 128}
	_, err = op.ExpandEvidence(ctx, req, dest)
	requireCode(t, err, "EVIDENCE_UNAVAILABLE")
}

func assertEvidenceSpan(t *testing.T, result EvidenceExpansion, body string, offset, end int) {
	t.Helper()
	whole := sha256.Sum256([]byte(body))
	selected := sha256.Sum256([]byte(body[offset:end]))
	span := result.Span
	if span == nil || span.Offset != offset || span.End != end || span.TotalBytes != len(body) || span.SHA256 != hex.EncodeToString(selected[:]) || result.Evidence.Digest != hex.EncodeToString(whole[:]) || result.Evidence.ActualSHA256 != result.Evidence.Digest {
		t.Fatalf("span identity: %+v", result)
	}
	if result.Evidence.Body != "" || result.Evidence.BodyBase64 != "" {
		t.Fatal("span response also exposed whole source")
	}
	got := []byte(span.Body)
	if span.BodyBase64 != "" {
		var err error
		got, err = base64.StdEncoding.DecodeString(span.BodyBase64)
		if err != nil || span.Body != "" {
			t.Fatalf("ambiguous/invalid span encoding: %+v %v", span, err)
		}
	}
	if string(got) != body[offset:end] {
		t.Fatalf("selected source bytes changed: %q", got)
	}
}

func TestEvidenceSpanByteBoundariesAndInvalidRanges(t *testing.T) {
	ctx := context.Background()
	body := "aéz"
	op, _, _, evidence, index, dest := evidenceExpansionFixtureWithBody(t, "local", body)
	req := ExpandEvidenceRequest{evidence.Digest, uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle, evidence.ID, nil}
	for _, span := range []EvidenceSpanRequest{{-1, 1}, {0, 0}, {0, -1}, {1048576, 1}, {0, 1048577}, {len(body), 1}} {
		req.Span = &span
		_, err := op.ExpandEvidence(ctx, req, dest)
		requireCode(t, err, "INVALID_REQUEST")
	}
	for i, span := range []EvidenceSpanRequest{{1, 1}, {2, 1}, {1, 2}} {
		req.RequestID, req.Span = uuid.NewString(), &span
		result, err := op.ExpandEvidence(ctx, req, dest)
		if err != nil {
			t.Fatal(err)
		}
		assertEvidenceSpan(t, result, body, span.Offset, span.Offset+span.Length)
		if result.CreditsRemaining != 3-i || (i < 2 && result.Span.BodyBase64 == "") || (i == 2 && result.Span.Body != "é") {
			t.Fatalf("encoding or credits: %+v", result)
		}
	}
}

func TestEvidenceSpanBinarySourceAndOversizeSelection(t *testing.T) {
	ctx := context.Background()
	body := strings.Repeat("\xff\x00", 20000)
	op, _, _, evidence, index, dest := evidenceExpansionFixtureWithBody(t, "local", body)
	req := ExpandEvidenceRequest{evidence.Digest, uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle, evidence.ID, &EvidenceSpanRequest{Offset: 0, Length: len(body)}}
	_, err := op.ExpandEvidence(ctx, req, dest)
	requireCode(t, err, "BUDGET_REFUSED")
	req.Span = &EvidenceSpanRequest{Offset: 1, Length: 5}
	result, err := op.ExpandEvidence(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	assertEvidenceSpan(t, result, body, 1, 6)
	if result.CreditsRemaining != 3 || result.Span.BodyBase64 == "" {
		t.Fatalf("binary span/failed pull budget: %+v", result)
	}
}
