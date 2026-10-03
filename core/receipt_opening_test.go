package core

import (
	"bytes"
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/fxamacker/cbor/v2"
	"github.com/google/uuid"
)

func TestReceiptOpeningSuppliesContextWithoutCallerOptIn(t *testing.T) {
	ctx := context.Background()
	s, _ := testBoundOperator(t)
	d := projectNote(uuid.NewString())
	d.Body = "Project ai-newsroom.\n" + strings.Repeat("background ", 50) + "editorial condition"
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	dest := Destination{"local", true}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{d.Scope.Repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}
	idx, err := s.Index(ctx, req, dest)
	if err != nil || len(idx.Handles) != 1 {
		t.Fatalf("index: %+v %v", idx, err)
	}
	pull := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: idx.Package.ReceiptID, Handle: idx.Handles[0].Handle, Span: &ByteSpanRequest{Offset: strings.Index(d.Body, "editorial"), Length: 100}}
	got, err := s.Expand(ctx, pull, dest)
	if err != nil {
		t.Fatal(err)
	}
	if got.SourceOpening == nil || got.SourceOpening.Body != d.Body[:96] || got.Span.Body != "editorial condition" || got.CreditsRemaining != 3 {
		t.Fatalf("new receipt did not supply exact opening beside passage: %+v", got)
	}
	again, err := s.Expand(ctx, pull, dest)
	if err != nil || !sameOpeningWire(t, again, got) {
		t.Fatalf("default pair retry changed: %+v %v", again, err)
	}
	replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: idx.Package.ReceiptID})
	if err != nil || replay.Seal != idx.Package.Seal || replay.Semantic.SpanContext != sourceOpening96 {
		t.Fatalf("default delivery contract lost on recompile: %+v %v", replay, err)
	}
	pull.RequestID, pull.Span = uuid.NewString(), nil
	whole, err := s.Expand(ctx, pull, dest)
	if err != nil || whole.SourceOpening != nil || whole.Selection.Record.Body != d.Body {
		t.Fatalf("whole pull changed: %+v %v", whole, err)
	}
}

func sameOpeningWire(t *testing.T, a, b Expansion) bool {
	t.Helper()
	left, err := json.Marshal(a)
	if err != nil {
		t.Fatal(err)
	}
	right, err := json.Marshal(b)
	if err != nil {
		t.Fatal(err)
	}
	return bytes.Equal(left, right)
}

// Only reseal a receipt inside this test's owned disposable database.
func setTestSpanContext(t *testing.T, s *Store, idx *IndexResult, policy string) {
	t.Helper()
	idx.Package.Semantic.SpanContext = policy
	encoded, seal, err := sealPackage(idx.Package.Semantic)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.pool.Exec(context.Background(), `UPDATE cairn.retrieval_receipt SET semantic_body=$2,seal=$3 WHERE receipt_id=$1`, idx.Package.ReceiptID, encoded, seal); err != nil {
		t.Fatal(err)
	}
	idx.Package.Seal = seal
}

func TestReceiptOpeningOldContractsAndUnknownPolicy(t *testing.T) {
	for _, semantic := range []bool{false, true} {
		t.Run(map[bool]string{false: "lexical", true: "semantic"}[semantic], func(t *testing.T) {
			ctx := context.Background()
			s, _ := testBoundOperator(t)
			d := projectNote(uuid.NewString())
			d.Body = "Project fixture. " + strings.Repeat("context ", 30) + "tail"
			if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
				t.Fatal(err)
			}
			dest := Destination{"local", true}
			req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{d.Scope.Repo, "task", "run"}, Query: "context", Purpose: "context", AvailableTokens: 64000, Semantic: semantic}
			idx, err := s.Index(ctx, req, dest)
			if err != nil || len(idx.Handles) != 1 {
				t.Fatalf("index: %+v %v", idx, err)
			}
			setTestSpanContext(t, s, &idx, "")
			pull := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: idx.Package.ReceiptID, Handle: idx.Handles[0].Handle, Span: &ByteSpanRequest{Offset: len(d.Body) - 4, Length: 4}}
			old, err := s.Expand(ctx, pull, dest)
			if err != nil || old.SourceOpening != nil || old.Span.Body != "tail" {
				t.Fatalf("old contract changed: %+v %v", old, err)
			}
			again, err := s.Expand(ctx, pull, dest)
			if err != nil || !sameOpeningWire(t, old, again) {
				t.Fatalf("old cached retry changed: %v", err)
			}
			replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: idx.Package.ReceiptID, Query: req.Query})
			if err != nil || replay.Seal != idx.Package.Seal || replay.Semantic.SpanContext != "" {
				t.Fatalf("old recompile: %+v %v", replay, err)
			}
			// The existing lexical upgrade contract refuses a changed sealed policy.
			// Semantic retries retain their earlier policy alongside their ranking.
			retry, err := s.Index(ctx, req, dest)
			if semantic {
				if err != nil || retry.Package.Seal != idx.Package.Seal {
					t.Fatalf("semantic retry: %+v %v", retry, err)
				}
			} else {
				requireCode(t, err, "STALE_PACKAGE")
			}
			req.RequestID = uuid.NewString()
			fresh, err := s.Index(ctx, req, dest)
			if err != nil || fresh.Package.Semantic.SpanContext != sourceOpening96 {
				t.Fatalf("fresh default: %+v %v", fresh, err)
			}
			setTestSpanContext(t, s, &idx, "cairn.source-opening/future")
			_, err = s.Expand(ctx, pull, dest)
			requireCode(t, err, "INTEGRITY_FAILURE")
			_, err = s.Replay(ctx, idx.Package.ReceiptID)
			requireCode(t, err, "INTEGRITY_FAILURE")
			_, err = s.Recompile(ctx, RecompileRequest{ReceiptID: idx.Package.ReceiptID, Query: req.Query})
			requireCode(t, err, "INTEGRITY_FAILURE")
		})
	}
}

func TestReceiptOpeningAbsentEncodingAndHistoricalValidation(t *testing.T) {
	typ := reflect.TypeOf(SemanticPackage{})
	var fields []reflect.StructField
	for i := range typ.NumField() {
		if f := typ.Field(i); f.Name != "SpanContext" {
			fields = append(fields, f)
		}
	}
	oldType := reflect.StructOf(fields)
	options := cbor.CanonicalEncOptions()
	options.Time = cbor.TimeRFC3339Nano
	encoder, err := options.EncMode()
	if err != nil {
		t.Fatal(err)
	}
	for _, policy := range []string{"", sourceOpening96} {
		p := SemanticPackage{SpanContext: policy, Mode: "index", Purpose: "context"}
		encoded, _, err := sealPackage(p)
		if err != nil {
			t.Fatal(err)
		}
		old := reflect.New(oldType)
		if err = cbor.Unmarshal(encoded, old.Interface()); err != nil {
			t.Fatal(err)
		}
		reencoded, err := encoder.Marshal(old.Elem().Interface())
		if err != nil || bytes.Equal(encoded, reencoded) != (policy == "") {
			t.Fatalf("old decoder/absent bytes: %q %v", policy, err)
		}
		public, err := json.Marshal(p)
		if err != nil || bytes.Contains(public, []byte("span_context")) {
			t.Fatalf("internal policy leaked: %s %v", public, err)
		}
	}
	for _, p := range []SemanticPackage{{SpanContext: "future", Mode: "index", Purpose: "context"}, {SpanContext: sourceOpening96, Purpose: "context"}, {SpanContext: sourceOpening96, Mode: "index", Purpose: "security"}} {
		requireCode(t, validatePresentation(p), "INTEGRITY_FAILURE")
	}
}
