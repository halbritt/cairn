package core

import (
	"bytes"
	"context"
	"reflect"
	"strings"
	"testing"

	"github.com/fxamacker/cbor/v2"
	"github.com/google/uuid"
)

func TestPreviewExpansionStoreCurrentnessAndHistoricalReplay(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:preview-boundaries"})
	draft := projectNote(uuid.NewString())
	draft.Body = strings.Repeat("Background context. ", 12) + "Do not under any circumstances in the production environment reuse cached validation results. More details follow."
	note, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{draft.Scope.Repo, "preview", "run"}, Query: "reuse cached validation results", Purpose: "context", AvailableTokens: 8000}
	index, err := s.Index(ctx, req, Destination{"local", true})
	if err != nil || len(index.Package.Semantic.Index) != 1 {
		t.Fatalf("index: %+v %v", index, err)
	}
	entry := index.Package.Semantic.Index[0]
	if index.Package.Semantic.Presentation != previewBoundariesV1 || !strings.Contains(entry.Summary, "Do not") || entry.SummarySpan == nil {
		t.Fatalf("new index did not improve qualifier visibility: %+v", entry)
	}
	pull := ExpandRequest{uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle, entry.SummarySpan}
	partial, err := s.Expand(ctx, pull, Destination{"local", true})
	span := *entry.SummarySpan
	if err != nil || partial.Span == nil || partial.Span.Body != draft.Body[span.Offset:span.Offset+span.Length] || partial.Selection.Record.Body != "" {
		t.Fatalf("expanded preview locator is not exact source: %+v %v", partial, err)
	}
	pull.RequestID, pull.Span = uuid.NewString(), nil
	whole, err := s.Expand(ctx, pull, Destination{"local", true})
	if err != nil || whole.Selection.Record.Body != draft.Body {
		t.Fatalf("full source changed: %v", err)
	}
	oldBody := draft.Body
	draft.Body = "Different current advice."
	if _, err = s.Edit(ctx, EditRequest{uuid.NewString(), note.RecordID, note.Version, draft}); err != nil {
		t.Fatal(err)
	}
	pull.RequestID = uuid.NewString()
	_, err = s.Expand(ctx, pull, Destination{"local", true})
	requireCode(t, err, "STALE_HANDLE")
	newReplay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query})
	if err != nil || newReplay.Seal != index.Package.Seal {
		t.Fatalf("new presentation replay changed after edit: %v", err)
	}
	// Synthesize the exact pre-policy receipt from the same frozen facts and
	// legacy preview path. This DB belongs only to the disposable test suite.
	legacy := index.Package.Semantic
	legacy.Presentation = ""
	legacy.Index = append([]IndexEntry(nil), legacy.Index...)
	text, oldSpan := indexPreview(oldBody, req.Query, legacy.Ranking)
	legacy.Index[0].Summary, legacy.Index[0].SummarySpan = text, &oldSpan
	encoded, oldSeal, err := sealPackage(legacy)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_receipt SET semantic_body=$2,seal=$3 WHERE receipt_id=$1`, index.Package.ReceiptID, encoded, oldSeal); err != nil {
		t.Fatal(err)
	}
	oldReplay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query})
	if err != nil || oldReplay.Seal != oldSeal || oldReplay.Semantic.Presentation != "" || !reflect.DeepEqual(oldReplay.Semantic.Index, legacy.Index) {
		t.Fatalf("historical preview algorithm changed: %+v %v", oldReplay, err)
	}
	retained, err := s.Replay(ctx, index.Package.ReceiptID)
	if err != nil || retained.Seal != oldSeal {
		t.Fatalf("old saved bytes changed: %v", err)
	}
	legacy.Presentation = "cairn.preview-boundaries/future"
	encoded, badSeal, err := sealPackage(legacy)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_receipt SET semantic_body=$2,seal=$3 WHERE receipt_id=$1`, index.Package.ReceiptID, encoded, badSeal); err != nil {
		t.Fatal(err)
	}
	_, err = s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query})
	requireCode(t, err, "INTEGRITY_FAILURE")
	_, err = s.Replay(ctx, index.Package.ReceiptID)
	requireCode(t, err, "INTEGRITY_FAILURE")
	pull.RequestID = uuid.NewString()
	_, err = s.Expand(ctx, pull, Destination{"local", true})
	requireCode(t, err, "INTEGRITY_FAILURE")
}

func TestPreviewPresentationPreservesAbsentEncodingAndRejectsOldDecoder(t *testing.T) {
	typ := reflect.TypeOf(SemanticPackage{})
	fields := []reflect.StructField{}
	for i := range typ.NumField() {
		field := typ.Field(i)
		if field.Name != "Presentation" {
			fields = append(fields, field)
		}
	}
	legacyType := reflect.StructOf(fields)
	options := cbor.CanonicalEncOptions()
	options.Time = cbor.TimeRFC3339Nano
	encoder, err := options.EncMode()
	if err != nil {
		t.Fatal(err)
	}
	for _, policy := range []string{"", previewBoundariesV1, previewCompactV1} {
		p := SemanticPackage{Presentation: policy, Mode: "index", Purpose: "context", Schema: "cairn.semantic/17"}
		encoded, _, err := sealPackage(p)
		if err != nil {
			t.Fatal(err)
		}
		old := reflect.New(legacyType)
		if err = cbor.Unmarshal(encoded, old.Interface()); err != nil {
			t.Fatal(err)
		}
		reencoded, err := encoder.Marshal(old.Elem().Interface())
		if err != nil || bytes.Equal(encoded, reencoded) != (policy == "") {
			t.Fatalf("legacy encoding/integrity mismatch policy=%q: %v", policy, err)
		}
	}
}

func TestPreviewPolicyUpgradeRetainsStalePackageRetryContract(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:preview-policy-retry"})
	draft := projectNote(uuid.NewString())
	draft.Body = "Short unchanged advice with no expansion needed."
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft}); err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{draft.Scope.Repo, "preview", "retry"}, Query: "unchanged advice", Purpose: "context", AvailableTokens: 8000}
	index, err := s.Index(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	legacy := index.Package.Semantic
	legacy.Presentation = ""
	encoded, seal, err := sealPackage(legacy)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_receipt SET semantic_body=$2,seal=$3 WHERE receipt_id=$1`, index.Package.ReceiptID, encoded, seal); err != nil {
		t.Fatal(err)
	}
	// No record changed: changing a sealed compiler policy alone changes the
	// accepted package. Reusing its UUID must not silently replace the receipt.
	_, err = s.Index(ctx, req, Destination{"local", true})
	requireCode(t, err, "STALE_PACKAGE")
	req.RequestID = uuid.NewString()
	fresh, err := s.Index(ctx, req, Destination{"local", true})
	if err != nil || fresh.Package.ReceiptID == index.Package.ReceiptID || fresh.Package.Semantic.Presentation != previewBoundariesV1 {
		t.Fatalf("explicit fresh search failed: %+v %v", fresh, err)
	}
	repeated, err := s.Index(ctx, req, Destination{"local", true})
	if err != nil || repeated.Package.ReceiptID != fresh.Package.ReceiptID || repeated.BytesRemaining != fresh.BytesRemaining {
		t.Fatalf("same-policy retry changed receipt: %+v %v", repeated, err)
	}
}
