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

// gateFixture is one note with a passage far enough from the start to carry an
// opening, and two stores that share a principal and differ only in the gate.
type gateFixture struct {
	off, on *Store
	note    Draft
	span    *ByteSpanRequest
	dest    Destination
}

func newGateFixture(t *testing.T) gateFixture {
	t.Helper()
	principal := "gate:" + uuid.NewString()
	f := gateFixture{
		off:  testStore(t, Channel{Principal: principal}),
		on:   testStore(t, Channel{Principal: principal, SourceOpeningBinding: true}),
		note: projectNote(uuid.NewString()),
		dest: Destination{"local", true},
	}
	f.note.Body = "Project ai-newsroom.\n" + strings.Repeat("background ", 50) + "editorial condition"
	if _, err := f.off.Create(context.Background(), CreateRequest{uuid.NewString(), f.note}); err != nil {
		t.Fatal(err)
	}
	f.span = &ByteSpanRequest{Offset: strings.Index(f.note.Body, "editorial"), Length: 100}
	return f
}

func (f gateFixture) compile(t *testing.T, s *Store, request string, semantic bool) IndexResult {
	t.Helper()
	req := CompileRequest{RequestID: request, Scope: Scope{f.note.Scope.Repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}
	if semantic {
		req.Query, req.Semantic = "editorial", true
	}
	idx, err := s.Index(context.Background(), req, f.dest)
	if err != nil || len(idx.Handles) != 1 {
		t.Fatalf("index: %+v %v", idx, err)
	}
	return idx
}

func (f gateFixture) pull(idx IndexResult, opening int) ExpandRequest {
	return ExpandRequest{RequestID: uuid.NewString(), ReceiptID: idx.Package.ReceiptID, Handle: idx.Handles[0].Handle, Span: f.span, OpeningBytes: opening}
}

func storedBody(t *testing.T, s *Store, receipt string) []byte {
	t.Helper()
	var body []byte
	if err := s.pool.QueryRow(context.Background(), `SELECT semantic_body FROM cairn.retrieval_receipt WHERE receipt_id=$1`, receipt).Scan(&body); err != nil {
		t.Fatal(err)
	}
	return body
}

// encodesLikePreFeatureCode decodes a stored body with the package type as it
// was before SpanContext existed and re-encodes it: equal bytes mean the body
// holds nothing that code could not reproduce.
func encodesLikePreFeatureCode(t *testing.T, body []byte) bool {
	t.Helper()
	typ := reflect.TypeOf(SemanticPackage{})
	var fields []reflect.StructField
	for i := range typ.NumField() {
		if f := typ.Field(i); f.Name != "SpanContext" {
			fields = append(fields, f)
		}
	}
	old := reflect.New(reflect.StructOf(fields))
	if err := cbor.Unmarshal(body, old.Interface()); err != nil {
		t.Fatal(err)
	}
	options := cbor.CanonicalEncOptions()
	options.Time = cbor.TimeRFC3339Nano
	encoder, err := options.EncMode()
	if err != nil {
		t.Fatal(err)
	}
	again, err := encoder.Marshal(old.Elem().Interface())
	if err != nil {
		t.Fatal(err)
	}
	return bytes.Equal(body, again)
}

func hasSpanContextKey(t *testing.T, body []byte) bool {
	t.Helper()
	var fields map[string]any
	if err := cbor.Unmarshal(body, &fields); err != nil {
		t.Fatal(err)
	}
	_, ok := fields["span_context"]
	return ok
}

func TestSourceOpeningBindingIsOffByDefault(t *testing.T) {
	ctx := context.Background()
	f := newGateFixture(t)
	if f.off.channel.SourceOpeningBinding || !f.on.channel.SourceOpeningBinding {
		t.Fatalf("gate state: off=%v on=%v", f.off.channel.SourceOpeningBinding, f.on.channel.SourceOpeningBinding)
	}
	idx := f.compile(t, f.off, uuid.NewString(), false)
	body := storedBody(t, f.off, idx.Package.ReceiptID)
	if idx.Package.Semantic.SpanContext != "" || hasSpanContextKey(t, body) || !encodesLikePreFeatureCode(t, body) {
		t.Fatalf("an unbound receipt must hold exactly the pre-feature bytes: %q", idx.Package.Semantic.SpanContext)
	}
	got, err := f.off.ExpandForReader(ctx, f.pull(idx, 0), f.dest, true)
	if err != nil || got.SourceOpening != nil || got.Span == nil {
		t.Fatalf("a declared reader must get the plain span: %+v %v", got, err)
	}
	got, err = f.off.ExpandForReader(ctx, f.pull(idx, 0), f.dest, false)
	if err != nil || got.SourceOpening != nil {
		t.Fatalf("an undeclared reader reads an unbound receipt as before: %+v %v", got, err)
	}
	// The guard and explicit openings do not depend on the gate.
	_, err = f.off.ExpandForReader(ctx, f.pull(idx, 40), f.dest, false)
	requireCode(t, err, "PROTOCOL_UNSUPPORTED")
	got, err = f.off.ExpandForReader(ctx, f.pull(idx, 40), f.dest, true)
	if err != nil || got.SourceOpening == nil || got.SourceOpening.End != 40 {
		t.Fatalf("explicit opening: %+v %v", got, err)
	}
}

func TestSourceOpeningBindingOnBindsOnlyTheSealedPolicy(t *testing.T) {
	ctx := context.Background()
	f := newGateFixture(t)
	idx := f.compile(t, f.on, uuid.NewString(), false)
	body := storedBody(t, f.on, idx.Package.ReceiptID)
	if idx.Package.Semantic.SpanContext != sourceOpening96 || !hasSpanContextKey(t, body) || encodesLikePreFeatureCode(t, body) {
		t.Fatalf("a bound receipt must carry the sealed policy: %q", idx.Package.Semantic.SpanContext)
	}
	// Clearing the field must reproduce the unbound encoding: it is the only difference.
	unbound := idx.Package.Semantic
	unbound.SpanContext = ""
	withoutField, _, err := sealPackage(unbound)
	if err != nil || !encodesLikePreFeatureCode(t, withoutField) {
		t.Fatalf("policy removal: %v", err)
	}
	got, err := f.on.ExpandForReader(ctx, f.pull(idx, 0), f.dest, true)
	if err != nil || got.SourceOpening == nil || got.SourceOpening.Body != f.note.Body[:96] {
		t.Fatalf("default pair: %+v %v", got, err)
	}
	_, err = f.on.ExpandForReader(ctx, f.pull(idx, 0), f.dest, false)
	requireCode(t, err, "PROTOCOL_UNSUPPORTED")
	whole, err := f.on.ExpandForReader(ctx, ExpandRequest{RequestID: uuid.NewString(), ReceiptID: idx.Package.ReceiptID, Handle: idx.Handles[0].Handle}, f.dest, false)
	if err != nil || whole.SourceOpening != nil || whole.Selection.Record.Body != f.note.Body {
		t.Fatalf("whole read needs no declaration: %+v %v", whole, err)
	}
}

func TestSourceOpeningBindingFlipsKeepEachReceiptsContract(t *testing.T) {
	ctx := context.Background()
	f := newGateFixture(t)
	bound := f.compile(t, f.on, uuid.NewString(), false)
	unbound := f.compile(t, f.off, uuid.NewString(), false)
	for name, s := range map[string]*Store{"gate off": f.off, "gate on": f.on} {
		// A receipt keeps the policy it was sealed with, whichever way the gate now points.
		_, err := s.ExpandForReader(ctx, f.pull(bound, 0), f.dest, false)
		requireCode(t, err, "PROTOCOL_UNSUPPORTED")
		pair, err := s.ExpandForReader(ctx, f.pull(bound, 0), f.dest, true)
		if err != nil || pair.SourceOpening == nil {
			t.Fatalf("%s: bound receipt lost its pair: %+v %v", name, pair, err)
		}
		plain, err := s.ExpandForReader(ctx, f.pull(unbound, 0), f.dest, true)
		if err != nil || plain.SourceOpening != nil {
			t.Fatalf("%s: unbound receipt gained a pair: %+v %v", name, plain, err)
		}
		if _, err = s.Replay(ctx, bound.Package.ReceiptID); err != nil {
			t.Fatalf("%s: replay of a bound receipt: %v", name, err)
		}
		if _, err = s.Recompile(ctx, RecompileRequest{ReceiptID: bound.Package.ReceiptID}); err != nil {
			t.Fatalf("%s: recompile of a bound receipt: %v", name, err)
		}
	}
}

func TestSourceOpeningBindingFlipsAreStaleForLexicalAndRetainedForSemanticRetries(t *testing.T) {
	f := newGateFixture(t)
	for _, direction := range []struct {
		name         string
		first, retry *Store
		policy       string
	}{{"off then on", f.off, f.on, ""}, {"on then off", f.on, f.off, sourceOpening96}} {
		t.Run(direction.name, func(t *testing.T) {
			lexical := uuid.NewString()
			f.compile(t, direction.first, lexical, false)
			_, err := direction.retry.Index(context.Background(), CompileRequest{RequestID: lexical, Scope: Scope{f.note.Scope.Repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, f.dest)
			requireCode(t, err, "STALE_PACKAGE")
			semantic := uuid.NewString()
			first := f.compile(t, direction.first, semantic, true)
			retried := f.compile(t, direction.retry, semantic, true)
			if retried.Package.ReceiptID != first.Package.ReceiptID || retried.Package.Seal != first.Package.Seal || retried.Package.Semantic.SpanContext != direction.policy {
				t.Fatalf("a semantic retry must keep its sealed policy %q: %+v", direction.policy, retried.Package.Semantic.SpanContext)
			}
		})
	}
}

func TestSourceOpeningBindingOnlyFollowsContextIndexes(t *testing.T) {
	f := newGateFixture(t)
	other, err := f.on.Index(context.Background(), CompileRequest{RequestID: uuid.NewString(), Scope: Scope{f.note.Scope.Repo, "task", "run"}, Purpose: "planning", AvailableTokens: 64000}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	if other.Package.Semantic.SpanContext != "" || hasSpanContextKey(t, storedBody(t, f.on, other.Package.ReceiptID)) {
		t.Fatalf("only context indexes bind the policy: %q", other.Package.Semantic.SpanContext)
	}
}

func TestAgentSessionViewCarriesOnlyTheProfileGate(t *testing.T) {
	ref := AgentSessionRef{AgentID: uuid.NewString(), ExecutionID: uuid.NewString()}
	dest := Destination{"local", true}
	for _, enabled := range []bool{false, true} {
		base := testStore(t, Channel{Principal: "gate:profile:" + uuid.NewString(), Operator: true, Repo: "fixture:repo", SourceOpeningBinding: enabled})
		view, err := base.ForAgentSession(ref, dest)
		if err != nil {
			t.Fatal(err)
		}
		want := Channel{Principal: "agent/" + ref.AgentID, Repo: "fixture:repo", SourceOpeningBinding: enabled}
		if view.channel != want {
			t.Fatalf("gate %v: session view channel %+v, want %+v", enabled, view.channel, want)
		}
		if view.channel.Operator || view.channel.Instrumented {
			t.Fatalf("authority fields must stay stripped: %+v", view.channel)
		}
	}
}
