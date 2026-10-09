package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestDistinctQueryRoutesNaturalTextAndReplaysWithoutBackend(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	d.Body = "Keep storage files in the private application directory."
	note, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	semantic := "Do not put files in the public folder. Use the private application directory."
	calls := 0
	s.semanticRetriever = func(_ context.Context, input SemanticRankRequest) (SemanticRetrievalResult, error) {
		calls++
		if input.Query != semantic {
			t.Fatalf("semantic worker saw %q; want exact task text %q", input.Query, semantic)
		}
		if len(input.Notes) != 1 || input.Notes[0].RecordID != note.RecordID {
			t.Fatalf("eligible input changed: %+v", input.Notes)
		}
		n := input.Notes[0]
		hash := sha256.Sum256([]byte(semantic))
		return SemanticRetrievalResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "fixture/1", Indexed: 1,
			QueryProjection: &SemanticQueryProjection{Method: "original-prefix/1", OriginalTokens: 20, EmbeddedTokens: 20, PrefixBytes: len(semantic), PrefixSHA256: hex.EncodeToString(hash[:])},
			Hits:            []SemanticPassageHit{{SemanticScore: SemanticScore{n.RecordID, n.Version, n.BodySHA256, 900000}, Span: ByteSpanRequest{Length: len(n.Body)}}}}, nil
	}
	// Exercise the incoming JSON boundary too: an older decoder silently drops
	// the field, which must not produce a successful opted-in request.
	wire, err := json.Marshal(map[string]any{"request_id": uuid.NewString(), "scope": Scope{repo, "task", "run"}, "query": "storage", "semantic_query": semantic, "purpose": "context", "available_tokens": 32000, "semantic": true})
	if err != nil {
		t.Fatal(err)
	}
	var req CompileRequest
	if err := json.Unmarshal(wire, &req); err != nil {
		t.Fatal(err)
	}
	index, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	if calls != 1 || index.Package.Semantic.Schema != "cairn.semantic/20" || len(index.Package.Semantic.Index) != 1 {
		t.Fatalf("distinct query not honored: calls=%d package=%+v", calls, index.Package.Semantic)
	}
	s.semanticRetriever = nil // replay must need no worker or configured backend
	raw, _ := json.Marshal(map[string]any{"receipt_id": index.Package.ReceiptID, "query": req.Query, "semantic_query": semantic})
	var replayReq RecompileRequest
	if err := json.Unmarshal(raw, &replayReq); err != nil {
		t.Fatal(err)
	}
	for _, replay := range []func() (Package, error){
		func() (Package, error) { return s.Recompile(ctx, replayReq) },
		func() (Package, error) {
			return s.RecompileForDestination(ctx, replayReq, Destination{"hosted", false})
		},
	} {
		p, err := replay()
		if err != nil || p.Seal != index.Package.Seal || p.Semantic.Schema != "cairn.semantic/20" {
			t.Fatalf("exact backend-free replay: %v %+v", err, p)
		}
	}
}

func TestDistinctQueryRetryAfterForgetPreservesPayloadUnavailable(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Body = "storage guidance to be forgotten"
	record, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	calls := 0
	s.semanticRetriever = func(context.Context, SemanticRankRequest) (SemanticRetrievalResult, error) {
		calls++
		return SemanticRetrievalResult{}, errors.New("offline fixture")
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "storage", SemanticQuery: "Keep storage private.", Semantic: true, Purpose: "context", AvailableTokens: 32000}
	index, err := s.Index(ctx, req, Destination{"local", true})
	if err != nil || len(index.Package.Semantic.Index) != 1 || calls != 1 {
		t.Fatalf("compile: %+v %v calls%d", index, err, calls)
	}
	preview, err := s.PreviewDeletion(ctx, record.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	deletion, err := s.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: record.Version, GrantID: root.ID, PreviewID: preview.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	for _, phase := range []string{"excluded", "purged"} {
		if phase == "purged" {
			if _, err := s.PurgeDeletion(ctx, deletion.DeletionID); err != nil {
				t.Fatal(err)
			}
		}
		_, err := s.Index(ctx, req, Destination{"local", true})
		requireCode(t, err, "PAYLOAD_UNAVAILABLE")
		if calls != 1 {
			t.Fatalf("%s retry called provider: %d", phase, calls)
		}
	}
}

func TestDistinctQueryFallbackKeepsLexicalPackageAndPrivateInput(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	for _, tc := range []struct {
		name, state string
		worker      SemanticRetriever
		withNote    bool
	}{
		{"unavailable", "unavailable", func(context.Context, SemanticRankRequest) (SemanticRetrievalResult, error) {
			return SemanticRetrievalResult{}, errors.New("offline")
		}, true},
		{"empty index", "unavailable", func(context.Context, SemanticRankRequest) (SemanticRetrievalResult, error) {
			return SemanticRetrievalResult{}, nil
		}, true},
		{"invalid projection", "invalid_result", func(_ context.Context, r SemanticRankRequest) (SemanticRetrievalResult, error) {
			n := r.Notes[0]
			return SemanticRetrievalResult{Indexed: 1, ModelSHA256: strings.Repeat("a", 64), Algorithm: "fixture/1", QueryProjection: &SemanticQueryProjection{Method: "original-prefix/1", OriginalTokens: 1, EmbeddedTokens: 1, PrefixBytes: 1, PrefixSHA256: strings.Repeat("0", 64)}, Hits: []SemanticPassageHit{{SemanticScore: SemanticScore{n.RecordID, n.Version, n.BodySHA256, 1}, Span: ByteSpanRequest{Length: 1}}}}, nil
		}, true},
		{"not needed", "not_needed", func(context.Context, SemanticRankRequest) (SemanticRetrievalResult, error) {
			t.Fatal("unneeded worker called")
			return SemanticRetrievalResult{}, nil
		}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			repo := uuid.NewString()
			if tc.withNote {
				d := projectNote(repo)
				d.Sensitivity = "shareable"
				d.Body = "storage directory guidance"
				if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
					t.Fatal(err)
				}
			}
			s.semanticRetriever = tc.worker
			req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "storage", Semantic: true, Purpose: "context", AvailableTokens: 32000}
			legacy, err := s.Index(ctx, req, Destination{"hosted", false})
			if err != nil {
				t.Fatal(err)
			}
			req.RequestID = uuid.NewString()
			req.SemanticQuery = "Private task text: do not use public storage."
			current, err := s.Index(ctx, req, Destination{"hosted", false})
			if err != nil {
				t.Fatal(err)
			}
			p := current.Package.Semantic
			if p.Schema != distinctQuerySchema || p.QueryIntent == nil || p.Discovery.State != tc.state {
				t.Fatalf("missing bound fallback: %+v", p)
			}
			p.Schema = legacy.Package.Semantic.Schema
			p.QueryIntent = nil
			oldBytes, _, err := sealPackage(legacy.Package.Semantic)
			if err != nil {
				t.Fatal(err)
			}
			newBytes, _, err := sealPackage(p)
			if err != nil || !bytes.Equal(oldBytes, newBytes) {
				t.Fatalf("lexical fallback changed for %s: %v", tc.name, err)
			}
			wire, err := json.Marshal(current.Package)
			if err != nil {
				t.Fatal(err)
			}
			if bytes.Contains(wire, []byte(req.SemanticQuery)) || bytes.Contains(wire, []byte("query_intent")) {
				t.Fatal("semantic input leaked to public package")
			}
			for _, input := range []string{"", strings.ToUpper(req.SemanticQuery), req.SemanticQuery + " "} {
				_, err = s.Recompile(ctx, RecompileRequest{ReceiptID: current.Package.ReceiptID, Query: req.Query, SemanticQuery: input})
				requireCode(t, err, "INVALID_REQUEST")
			}
			s.semanticRetriever = nil
			for _, replay := range []func() (Package, error){
				func() (Package, error) {
					return s.Recompile(ctx, RecompileRequest{ReceiptID: current.Package.ReceiptID, Query: req.Query, SemanticQuery: req.SemanticQuery})
				},
				func() (Package, error) {
					return s.RecompileForDestination(ctx, RecompileRequest{ReceiptID: current.Package.ReceiptID, Query: req.Query, SemanticQuery: req.SemanticQuery}, Destination{"hosted", false})
				},
			} {
				p, err := replay()
				if err != nil || p.Seal != current.Package.Seal {
					t.Fatalf("fallback replay: %v", err)
				}
			}
		})
	}
}

func TestDistinctQueryRetryRejectsChangedIntentBeforeWorker(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	d.Body = "storage private application directory"
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name    string
		initial string
		change  func(*CompileRequest)
	}{
		{"changed semantic", "Use private storage.", func(r *CompileRequest) { r.SemanticQuery = "Use public storage." }},
		{"removed semantic", "Use private storage.", func(r *CompileRequest) { r.SemanticQuery = "" }},
		{"removed semantic route", "Use private storage.", func(r *CompileRequest) { r.SemanticQuery = ""; r.Semantic = false }},
		{"changed lexical", "Use private storage.", func(r *CompileRequest) { r.Query = "application" }},
		{"added semantic", "", func(r *CompileRequest) { r.SemanticQuery = "Use private storage." }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			calls := 0
			s.semanticRetriever = func(context.Context, SemanticRankRequest) (SemanticRetrievalResult, error) {
				calls++
				return SemanticRetrievalResult{}, errors.New("fixture unavailable")
			}
			req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "storage", SemanticQuery: tc.initial, Semantic: true, Purpose: "context", AvailableTokens: 32000}
			first, err := s.Index(ctx, req, Destination{"hosted", false})
			if err != nil {
				t.Fatal(err)
			}
			req.Mode = "index"
			// An identical retry remains valid; changing intent must stop before
			// the worker even if it would currently produce the same fallback.
			exact, err := s.Index(ctx, req, Destination{"hosted", false})
			if err != nil || exact.Package.Seal != first.Package.Seal {
				t.Fatalf("exact retry: %v", err)
			}
			before := calls
			tc.change(&req)
			_, err = s.Index(ctx, req, Destination{"hosted", false})
			var failure *Error
			if !errors.As(err, &failure) || failure.Code != "IDEMPOTENCY_CONFLICT" || calls != before {
				t.Fatalf("changed intent: error=%v provider calls=%d want=%d", err, calls, before)
			}
		})
	}
}

func TestDistinctQueryRejectsInvalidInputWithoutConsumingRequest(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	d.Body = "storage private application directory"
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name    string
		change  func(*CompileRequest)
		backend bool
		code    string
	}{
		{"invalid UTF8", func(r *CompileRequest) { r.SemanticQuery = string([]byte{0xff}) }, true, "INVALID_REQUEST"},
		{"NUL", func(r *CompileRequest) { r.SemanticQuery = "storage\x00private" }, true, "INVALID_REQUEST"},
		{"blank", func(r *CompileRequest) { r.SemanticQuery = " \t\n" }, true, "INVALID_REQUEST"},
		{"combined overflow", func(r *CompileRequest) { r.SemanticQuery = strings.Repeat("x", 4090) }, true, "INVALID_REQUEST"},
		{"disabled semantic", func(r *CompileRequest) { r.Semantic = false }, true, "INVALID_REQUEST"},
		{"nonindex mode", func(r *CompileRequest) { r.Mode = "" }, true, "INVALID_REQUEST"},
		{"noncontext purpose", func(r *CompileRequest) { r.Purpose = "planning" }, true, "INVALID_REQUEST"},
		{"empty lexical", func(r *CompileRequest) { r.Query = " " }, true, "INVALID_REQUEST"},
		{"browse", func(r *CompileRequest) { zero := 0; r.BrowseOffset = &zero }, true, "INVALID_REQUEST"},
		{"no indexed backend", func(*CompileRequest) {}, false, "UNSUPPORTED_QUERY_INPUT"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			calls := 0
			worker := SemanticRetriever(func(context.Context, SemanticRankRequest) (SemanticRetrievalResult, error) {
				calls++
				return SemanticRetrievalResult{}, errors.New("fixture unavailable")
			})
			s.semanticRetriever = nil
			if tc.backend {
				s.semanticRetriever = worker
			}
			req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Mode: "index", Query: "storage", SemanticQuery: "Use private storage.", Semantic: true, Purpose: "context", AvailableTokens: 32000}
			bad := req
			tc.change(&bad)
			_, err := s.Compile(ctx, bad, Destination{"hosted", false})
			requireCode(t, err, tc.code)
			if calls != 0 {
				t.Fatal("invalid input invoked worker")
			}
			s.semanticRetriever = worker
			// Reusing the same ID after validation refusal must be fresh.
			_, err = s.Compile(ctx, req, Destination{"hosted", false})
			if err != nil || calls != 1 {
				t.Fatalf("refusal consumed request: %v calls=%d", err, calls)
			}
		})
	}
	// Exactly the aggregate ceiling is accepted without truncating worker input.
	exact := strings.Repeat("é", 2044) + "x" // 4089 bytes, plus 7 lexical bytes
	s.semanticRetriever = func(_ context.Context, r SemanticRankRequest) (SemanticRetrievalResult, error) {
		if r.Query != exact {
			t.Fatal("boundary input truncated or changed")
		}
		return SemanticRetrievalResult{}, errors.New("offline")
	}
	_, err := s.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "storage", SemanticQuery: exact, Semantic: true, Purpose: "context", AvailableTokens: 32000}, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
}

func TestDistinctQueryReadersRejectIllegalBoundSchema(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	d.Body = "storage guidance"
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	s.semanticRetriever = func(context.Context, SemanticRankRequest) (SemanticRetrievalResult, error) {
		return SemanticRetrievalResult{}, errors.New("offline")
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "storage", SemanticQuery: "Use private storage.", Semantic: true, Purpose: "context", AvailableTokens: 32000}
	for _, tc := range []struct {
		name   string
		change func(*SemanticPackage)
	}{
		{"unsupported base", func(p *SemanticPackage) { p.QueryIntent.BaseSchema = "cairn.semantic/15" }},
		{"missing binding", func(p *SemanticPackage) { p.QueryIntent = nil }},
		{"old schema with binding", func(p *SemanticPackage) { p.Schema = "cairn.semantic/16" }},
		{"unknown binding", func(p *SemanticPackage) { p.QueryIntent.Schema = "cairn.semantic-query-intent/2" }},
		{"invalid digest", func(p *SemanticPackage) { p.QueryIntent.SHA256 = "not-a-digest" }},
		{"wrong ready base", func(p *SemanticPackage) { p.QueryIntent.BaseSchema = "cairn.semantic/19" }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			req.RequestID = uuid.NewString()
			index, err := s.Index(ctx, req, Destination{"hosted", false})
			if err != nil {
				t.Fatal(err)
			}
			if len(index.Handles) != 1 {
				t.Fatalf("fixture handles: %+v", index.Handles)
			}
			p := index.Package.Semantic
			tc.change(&p)
			body, seal, err := sealPackage(p)
			if err != nil {
				t.Fatal(err)
			}
			// A hostile fixture replaces both bytes and seal to exercise format
			// validation independently of the existing checksum check.
			_, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_receipt SET semantic_body=$1,seal=$2 WHERE receipt_id=$3`, body, seal, index.Package.ReceiptID)
			if err != nil {
				t.Fatal(err)
			}
			_, err = s.Replay(ctx, index.Package.ReceiptID)
			requireCode(t, err, "INTEGRITY_FAILURE")
			_, err = s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query, SemanticQuery: req.SemanticQuery})
			requireCode(t, err, "INTEGRITY_FAILURE")
			_, err = s.Expand(ctx, ExpandRequest{RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, Handle: index.Handles[0].Handle}, Destination{"hosted", false})
			requireCode(t, err, "INTEGRITY_FAILURE")
		})
	}
}

func TestDistinctQueryReadyProjectionAndReaderInvariants(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	d.Body = "storage guidance"
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "storage", SemanticQuery: "Do not use public storage.", Semantic: true, Purpose: "context", AvailableTokens: 32000}
	projectionInput := req.Query
	s.semanticRetriever = func(_ context.Context, r SemanticRankRequest) (SemanticRetrievalResult, error) {
		n := r.Notes[0]
		return SemanticRetrievalResult{Indexed: 1, ModelSHA256: strings.Repeat("a", 64), Algorithm: "fixture/1", QueryProjection: projectionFixture(projectionInput, len(projectionInput), 10, 10), Hits: []SemanticPassageHit{{SemanticScore: SemanticScore{n.RecordID, n.Version, n.BodySHA256, 900000}, Span: ByteSpanRequest{Length: len(n.Body)}}}}, nil
	}
	bad, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil || bad.Package.Semantic.Discovery.State != "invalid_result" {
		t.Fatalf("projection matching only lexical input accepted: %v", err)
	}
	projectionInput = req.SemanticQuery
	for _, tc := range []struct {
		name   string
		change func(*SemanticPackage)
	}{
		{"base16 with projection", func(p *SemanticPackage) {
			p.QueryIntent.BaseSchema = "cairn.semantic/16"
			p.Ranking = "interleaved-scope-recency/1"
			p.IDF = nil
		}},
		{"base18 with IDF", func(p *SemanticPackage) {
			p.QueryIntent.BaseSchema = "cairn.semantic/18"
			p.Ranking = "interleaved-scope-recency/1"
		}},
		{"base18 without projection", func(p *SemanticPackage) {
			p.QueryIntent.BaseSchema = "cairn.semantic/18"
			p.Ranking = "interleaved-scope-recency/1"
			p.IDF = nil
			p.Discovery.QueryProjection = nil
		}},
		{"base19 without IDF", func(p *SemanticPackage) { p.IDF = nil }},
		{"base19 with nonIDF ranking", func(p *SemanticPackage) { p.Ranking = "interleaved-scope-recency/1" }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			req.RequestID = uuid.NewString()
			index, err := s.Index(ctx, req, Destination{"hosted", false})
			if err != nil {
				t.Fatal(err)
			}
			if index.Package.Semantic.Discovery.State != "ready" || index.Package.Semantic.QueryIntent.BaseSchema != "cairn.semantic/19" {
				t.Fatal("fixture did not reach ready IDF projection")
			}
			// A correct ready receipt replays backend-free before deliberate damage.
			worker := s.semanticRetriever
			s.semanticRetriever = nil
			if p, err := s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query, SemanticQuery: req.SemanticQuery}); err != nil || p.Seal != index.Package.Seal {
				t.Fatalf("ready replay: %v", err)
			}
			s.semanticRetriever = worker
			p := index.Package.Semantic
			tc.change(&p)
			body, seal, err := sealPackage(p)
			if err != nil {
				t.Fatal(err)
			}
			if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_receipt SET semantic_body=$1,seal=$2 WHERE receipt_id=$3`, body, seal, index.Package.ReceiptID); err != nil {
				t.Fatal(err)
			}
			_, err = s.Replay(ctx, index.Package.ReceiptID)
			requireCode(t, err, "INTEGRITY_FAILURE")
			_, err = s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query, SemanticQuery: req.SemanticQuery})
			requireCode(t, err, "INTEGRITY_FAILURE")
			_, err = s.Expand(ctx, ExpandRequest{RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, Handle: index.Handles[0].Handle}, Destination{"hosted", false})
			requireCode(t, err, "INTEGRITY_FAILURE")
		})
	}
	req.RequestID = uuid.NewString()
	index, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	p := index.Package.Semantic
	p.Discovery.QueryProjection = projectionFixture(req.Query, len(req.Query), 10, 10)
	body, seal, err := sealPackage(p)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_receipt SET semantic_body=$1,seal=$2 WHERE receipt_id=$3`, body, seal, index.Package.ReceiptID); err != nil {
		t.Fatal(err)
	}
	_, err = s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query, SemanticQuery: req.SemanticQuery})
	requireCode(t, err, "INTEGRITY_FAILURE")
}
