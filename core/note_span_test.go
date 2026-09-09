package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestNoteSpanMakesLongNoteReadable(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	marker := "TAIL: keep the explicit override"
	d.Body = "procedure\n" + strings.Repeat("retained procedure line\n", 2500) + marker
	r, err := op.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	dest := Destination{"local", true}
	idx, err := op.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "procedure", Purpose: "context", AvailableTokens: 1000000}, dest)
	if err != nil {
		t.Fatal(err)
	}
	req := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: idx.Package.ReceiptID, Handle: idx.Handles[0].Handle}
	_, err = op.Expand(ctx, req, dest)
	requireCode(t, err, "BUDGET_REFUSED")
	req.Span = &ByteSpanRequest{Offset: 0, Length: len(d.Body)}
	_, err = op.Expand(ctx, req, dest)
	requireCode(t, err, "BUDGET_REFUSED")
	input, err := json.Marshal(map[string]any{"request_id": req.RequestID, "receipt_id": req.ReceiptID, "handle": req.Handle, "span": map[string]int{"offset": len(d.Body) - len(marker), "length": 256}})
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(input, &req); err != nil {
		t.Fatal(err)
	}
	result, err := op.Expand(ctx, req, dest)
	if err != nil {
		t.Fatalf("accepted %d-byte note remains unreadable through bounded pull: %v", len(d.Body), err)
	}
	if result.Selection.Record.Body != "" || result.Selection.Record.RecordID != r.RecordID || result.CreditsRemaining != 3 {
		t.Fatalf("partial response or credit: %+v", result)
	}
	// JSON snake_case tags belong to the wire contract, independent of Go field names.
	var decoded struct {
		Span struct {
			Offset       int    `json:"offset"`
			End          int    `json:"end"`
			TotalBytes   int    `json:"total_bytes"`
			Body         string `json:"body"`
			SHA256       string `json:"sha256"`
			SourceSHA256 string `json:"source_sha256"`
		} `json:"span"`
	}
	encoded, err := json.Marshal(result)
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(encoded, &decoded); err != nil {
		t.Fatal(err)
	}
	whole, part := sha256.Sum256([]byte(d.Body)), sha256.Sum256([]byte(marker))
	if decoded.Span.Body != marker || decoded.Span.Offset != len(d.Body)-len(marker) || decoded.Span.End != len(d.Body) || decoded.Span.TotalBytes != len(d.Body) || decoded.Span.SHA256 != hex.EncodeToString(part[:]) || decoded.Span.SourceSHA256 != hex.EncodeToString(whole[:]) {
		t.Fatalf("wrong source span: %s", encoded)
	}
	again, err := op.Expand(ctx, req, dest)
	retryBytes, encodeErr := json.Marshal(again)
	if err != nil || encodeErr != nil || !bytes.Equal(encoded, retryBytes) {
		t.Fatalf("retry changed: %+v %v", again, err)
	}
	d.Body = "changed outside the selected tail\n" + d.Body
	if _, err = op.Edit(ctx, EditRequest{uuid.NewString(), r.RecordID, 1, d}); err != nil {
		t.Fatal(err)
	}
	_, err = op.Expand(ctx, req, dest)
	requireCode(t, err, "STALE_HANDLE")
}

func TestNoteSpanBoundariesAndInstructionRefusal(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Body = "aéz"
	if _, err := op.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	dest := Destination{"local", true}
	idx, err := op.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, dest)
	if err != nil {
		t.Fatal(err)
	}
	req := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: idx.Package.ReceiptID, Handle: idx.Handles[0].Handle}
	for _, span := range []ByteSpanRequest{{-1, 1}, {0, 0}, {0, -1}, {65536, 1}, {0, 65537}, {4, 1}} {
		req.Span = &span
		_, err = op.Expand(ctx, req, dest)
		requireCode(t, err, "INVALID_REQUEST")
	}
	for i, span := range []ByteSpanRequest{{1, 1}, {2, 1}, {1, 2}, {3, 65536}} {
		req.RequestID = uuid.NewString()
		req.Span = &span
		r, err := op.Expand(ctx, req, dest)
		if err != nil {
			t.Fatal(err)
		}
		if r.CreditsRemaining != 3-i || r.Span.TotalBytes != 4 || r.Selection.Record.Body != "" {
			t.Fatalf("span result: %+v", r)
		}
		wantBase64, wantBody := "", ""
		switch i {
		case 0:
			wantBase64 = "ww=="
		case 1:
			wantBase64 = "qQ=="
		case 2:
			wantBody = "é"
		case 3:
			wantBody = "z"
		}
		if r.Span.BodyBase64 != wantBase64 || r.Span.Body != wantBody {
			t.Fatalf("UTF8 boundary lost: %+v", r.Span)
		}
	}
	req.RequestID = uuid.NewString()
	_, err = op.Expand(ctx, req, dest)
	requireCode(t, err, "BUDGET_REFUSED")
	instructionRepo := uuid.NewString()
	d = projectNote(instructionRepo)
	d.Kind = "instruction"
	instruction, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: root.ID, PolicyKey: "whole-instruction", Reason: "Verify instructions cannot be fragmented"})
	if err != nil {
		t.Fatal(err)
	}
	idx, err = op.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{instructionRepo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, dest)
	if err != nil {
		t.Fatal(err)
	}
	if len(idx.Package.Semantic.Index) != 1 || idx.Package.Semantic.Index[0].SummarySpan != nil {
		t.Fatalf("instruction preview offers an unsupported partial pull: %+v", idx.Package.Semantic.Index)
	}
	req = ExpandRequest{RequestID: uuid.NewString(), ReceiptID: idx.Package.ReceiptID, Handle: idx.Handles[0].Handle, Span: &ByteSpanRequest{Offset: 0, Length: 8}}
	_, err = op.Expand(ctx, req, dest)
	requireCode(t, err, "INVALID_REQUEST")
	req.Span = nil
	full, err := op.Expand(ctx, req, dest)
	if err != nil || full.CreditsRemaining != 3 || full.Selection.Record.Body != instruction.Body || full.Span != nil {
		t.Fatalf("instruction whole delivery: %+v %v", full, err)
	}
}

func TestNoteSpanRechecksCompleteSourceAndCallerOnRetry(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Body = "prefix: retained procedure with a later condition"
	r, err := op.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	dest := Destination{"local", true}
	idx, err := op.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, dest)
	if err != nil {
		t.Fatal(err)
	}
	req := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: idx.Package.ReceiptID, Handle: idx.Handles[0].Handle, Span: &ByteSpanRequest{Offset: 0, Length: 6}}
	if _, err = op.Expand(ctx, req, dest); err != nil {
		t.Fatal(err)
	}
	outsider := testStore(t, Channel{Principal: "span-outsider:" + repo, Repo: repo})
	_, err = outsider.Expand(ctx, req, dest)
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = op.Expand(ctx, req, Destination{"hosted", false})
	requireCode(t, err, "AUTHORITY_DENIED")
	// Corrupt bytes outside the selected prefix without advancing the version.
	if _, err = op.pool.Exec(ctx, `UPDATE cairn.record_version SET body=$2 WHERE record_id=$1 AND version=1`, r.RecordID, d.Body+" changed"); err != nil {
		t.Fatal(err)
	}
	_, err = op.Expand(ctx, req, dest)
	requireCode(t, err, "INTEGRITY_FAILURE")
}
