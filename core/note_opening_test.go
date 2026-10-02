package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func openingRequest(t *testing.T, id, receipt, handle string, offset, length, opening int) ExpandRequest {
	t.Helper()
	wire := fmt.Sprintf(`{"request_id":%q,"receipt_id":%q,"handle":%q,"span":{"offset":%d,"length":%d},"opening_bytes":%d}`, id, receipt, handle, offset, length, opening)
	var req ExpandRequest
	if err := json.Unmarshal([]byte(wire), &req); err != nil {
		t.Fatal(err)
	}
	return req
}

func TestNoteOpeningSharesExactCurrentSourceAndRetry(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	d.Body = "Project ai-newsroom.\n" + strings.Repeat("background ", 150) + "editorial quality condition"
	created, err := op.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	dest := Destination{"hosted", false}
	idx, err := op.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, dest)
	if err != nil {
		t.Fatal(err)
	}
	if len(idx.Handles) != 1 {
		t.Fatalf("expected indexed source, got %d handles", len(idx.Handles))
	}
	offset := strings.Index(d.Body, "editorial")
	req := openingRequest(t, uuid.NewString(), idx.Package.ReceiptID, idx.Handles[0].Handle, offset, 65536, 96)
	result, err := op.Expand(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	wire, err := json.Marshal(result)
	if err != nil {
		t.Fatal(err)
	}
	var view struct {
		Opening *ByteSpan `json:"source_opening"`
	}
	if err = json.Unmarshal(wire, &view); err != nil {
		t.Fatal(err)
	}
	if view.Opening == nil {
		t.Fatalf("requested source context missing: %s", wire)
	}
	got := view.Opening
	h := sha256.Sum256([]byte(d.Body[:96]))
	full := sha256.Sum256([]byte(d.Body))
	if got.Offset != 0 || got.End != 96 || got.TotalBytes != len(d.Body) || got.Body != d.Body[:96] || got.SHA256 != hex.EncodeToString(h[:]) {
		t.Fatalf("opening: %+v", got)
	}
	if result.Span.Body != d.Body[offset:] || result.Span.Offset != offset || result.Span.SourceSHA256 != hex.EncodeToString(full[:]) || result.Selection.Record.Body != "" || result.CreditsRemaining != 3 {
		t.Fatalf("span/credit: %+v", result)
	}
	again, err := op.Expand(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	againWire, _ := json.Marshal(again)
	if !bytes.Equal(wire, againWire) {
		t.Fatal("retry differs")
	}
	changed := openingRequest(t, req.RequestID, req.ReceiptID, req.Handle, offset, 65536, 97)
	_, err = op.Expand(ctx, changed, dest)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	d.Body = strings.Replace(d.Body, "background", "changedxxx", 1)
	if _, err = op.Edit(ctx, EditRequest{uuid.NewString(), created.RecordID, 1, d}); err != nil {
		t.Fatal(err)
	}
	_, err = op.Expand(ctx, req, dest)
	requireCode(t, err, "STALE_HANDLE")
}

func TestNoteOpeningUTF8AndNonoverlap(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
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
	for _, tc := range []struct {
		offset, opening, end int
		body, b64            string
	}{{3, 2, 2, "", "YcM="}, {3, 768, 3, "aé", ""}, {1, 768, 1, "a", ""}, {0, 96, 0, "", ""}} {
		req := openingRequest(t, uuid.NewString(), idx.Package.ReceiptID, idx.Handles[0].Handle, tc.offset, 1, tc.opening)
		got, err := op.Expand(ctx, req, dest)
		if err != nil {
			t.Fatal(err)
		}
		if tc.offset == 0 {
			if got.SourceOpening != nil {
				t.Fatal("duplicated opening")
			}
			continue
		}
		if got.SourceOpening == nil || got.SourceOpening.End != tc.end || got.SourceOpening.End > got.Span.Offset || got.SourceOpening.Body != tc.body || got.SourceOpening.BodyBase64 != tc.b64 {
			t.Fatalf("unexpected prefix: %+v", got.SourceOpening)
		}
	}
}

func TestNoteOpeningBudgetRefusalIsAtomic(t *testing.T) {
	for _, explicit := range []bool{false, true} {
		t.Run(fmt.Sprint(explicit), func(t *testing.T) {
			ctx := context.Background()
			op, _ := testOperator(t)
			repo := uuid.NewString()
			d := projectNote(repo)
			d.Body = strings.Repeat("quote\"<&é>\n", 100) + "tail"
			if _, err := op.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
				t.Fatal(err)
			}
			dest := Destination{"local", true}
			search := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}
			if explicit {
				search.MemoryBudgetBytes = ptrInt(9000)
			}
			idx, err := op.Index(ctx, search, dest)
			if err != nil {
				t.Fatal(err)
			}
			setTestSpanContext(t, op, &idx, "") // Exercise explicit opt-in on a legacy receipt.
			before := 0
			if err = op.pool.QueryRow(ctx, `SELECT remaining_bytes FROM cairn.index_session WHERE receipt_id=$1`, idx.Package.ReceiptID).Scan(&before); err != nil {
				t.Fatal(err)
			}
			req := openingRequest(t, uuid.NewString(), idx.Package.ReceiptID, idx.Handles[0].Handle, len(d.Body)-4, 4, 0)
			plain, err := op.Expand(ctx, req, dest)
			if err != nil {
				t.Fatal(err)
			}
			plainCost := before - plain.BytesRemaining
			// Limit this owned disposable receipt to exactly the old response's cost.
			if _, err = op.pool.Exec(ctx, `UPDATE cairn.index_session SET remaining_bytes=$2 WHERE receipt_id=$1`, req.ReceiptID, plainCost); err != nil {
				t.Fatal(err)
			}
			with := openingRequest(t, uuid.NewString(), req.ReceiptID, req.Handle, len(d.Body)-4, 4, 96)
			_, err = op.Expand(ctx, with, dest)
			requireCode(t, err, "BUDGET_REFUSED")
			credits, remaining := 0, 0
			if err = op.pool.QueryRow(ctx, `SELECT credits,remaining_bytes FROM cairn.index_session WHERE receipt_id=$1`, req.ReceiptID).Scan(&credits, &remaining); err != nil {
				t.Fatal(err)
			}
			if credits != 3 || remaining != plainCost {
				t.Fatalf("refusal spent receipt: %d/%d", credits, remaining)
			}
			// Restore room and verify the pair is charged including JSON escaping.
			if _, err = op.pool.Exec(ctx, `UPDATE cairn.index_session SET remaining_bytes=8000 WHERE receipt_id=$1`, req.ReceiptID); err != nil {
				t.Fatal(err)
			}
			paired, err := op.Expand(ctx, with, dest)
			if err != nil {
				t.Fatal(err)
			}
			payload, err := json.Marshal(struct {
				Selection Selection `json:"selection"`
				Span      *NoteSpan `json:"span"`
				Opening   *ByteSpan `json:"source_opening"`
			}{paired.Selection, paired.Span, paired.SourceOpening})
			if err != nil {
				t.Fatal(err)
			}
			if explicit {
				payload, err = json.Marshal(string(payload))
				if err != nil {
					t.Fatal(err)
				}
			}
			if paired.CreditsRemaining != 2 || paired.BytesRemaining != 8000-len(payload)-256 {
				t.Fatalf("pair was not fully charged: %+v", paired)
			}
			outsider := testStore(t, Channel{Principal: "opening-outsider:" + repo, Repo: repo})
			_, err = outsider.Expand(ctx, with, dest)
			requireCode(t, err, "AUTHORITY_DENIED")
			_, err = op.Expand(ctx, with, Destination{"hosted", false})
			requireCode(t, err, "AUTHORITY_DENIED")
		})
	}
}

func TestNoteOpeningValidationAndLegacyWireIdentity(t *testing.T) {
	var store *Store
	for _, req := range []ExpandRequest{
		{OpeningBytes: -1, Span: &ByteSpanRequest{0, 1}},
		{OpeningBytes: 769, Span: &ByteSpanRequest{0, 1}},
		{OpeningBytes: 1},
	} {
		_, err := store.Expand(context.Background(), req, Destination{})
		requireCode(t, err, "INVALID_REQUEST")
	}
	req := ExpandRequest{RequestID: "request", ReceiptID: "receipt", Handle: "handle", Span: &ByteSpanRequest{Offset: 3, Length: 7}}
	got, err := json.Marshal(req)
	if err != nil {
		t.Fatal(err)
	}
	want := `{"request_id":"request","receipt_id":"receipt","handle":"handle","span":{"offset":3,"length":7}}`
	if string(got) != want {
		t.Fatalf("old retry intent changed: %s", got)
	}
}
