package core

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestIndexedIDFOrdersCompoundEvidenceBeforeCommonTermCount(t *testing.T) {
	ctx := context.Background()
	repo := uuid.NewString()
	s := testStore(t, Channel{Principal: "agent:semantic-idf", Repo: repo})
	create := func(body, sensitivity string) Record {
		t.Helper()
		r, err := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: Draft{Kind: "note", Body: body, Scope: Scope{repo, "*", "*"}, ClaimType: "self", Sensitivity: sensitivity}})
		if err != nil {
			t.Fatal(err)
		}
		return r
	}
	spaced := create("rare key maintenance guidance", "shareable")
	hyphen := create("rare-key operational guidance", "shareable")
	for i := 0; i < 5; i++ {
		create(fmt.Sprintf("alpha beta gamma common procedure %d", i), "shareable")
	}
	dense := create("unrelated remembered topic", "shareable")
	private := create("rare-key confidential source", "local")
	s.semanticRetriever = func(_ context.Context, r SemanticRankRequest) (SemanticRetrievalResult, error) {
		if len(r.Notes) != 8 {
			t.Fatalf("eligible cohort includes private/missing source: %d", len(r.Notes))
		}
		hits := []SemanticPassageHit{}
		for _, n := range r.Notes {
			if n.RecordID == private.RecordID {
				t.Fatal("private source exposed to retriever")
			}
			if n.RecordID == dense.RecordID {
				hits = append(hits, SemanticPassageHit{SemanticScore: SemanticScore{n.RecordID, n.Version, n.BodySHA256, 900000}, Span: ByteSpanRequest{0, len(n.Body)}})
			}
		}
		return SemanticRetrievalResult{QueryProjection: projectionFixture(r.Query, len(r.Query), 8, 8), ModelSHA256: strings.Repeat("a", 64), Algorithm: "synthetic-passages/1", Indexed: 8, Hits: hits}, nil
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "rare-key alpha beta gamma", Purpose: "context", Semantic: true, AvailableTokens: 64000}
	result, err := s.Index(ctx, req, Destination{Name: "hosted"})
	if err != nil {
		t.Fatal(err)
	}
	p := result.Package.Semantic
	if len(p.Index) < 3 || p.Index[0].RecordID != hyphen.RecordID || p.Index[1].RecordID != dense.RecordID || p.Index[2].RecordID != spaced.RecordID {
		t.Fatalf("rare adjacency did not order lexical channel while retaining dense rank: %+v", p.Index)
	}
	if p.Schema != "cairn.semantic/19" || p.Ranking != "interleaved-scope-recency/5" || p.IDF == nil || p.IDF.N != 8 {
		t.Fatalf("new sealed scoring contract: %+v", p)
	}
	replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: result.Package.ReceiptID, Query: req.Query})
	if err != nil || replay.Seal != result.Package.Seal {
		t.Fatalf("new replay: %v", err)
	}
	retry, err := s.Index(ctx, req, Destination{Name: "hosted"})
	if err != nil || retry.Package.ReceiptID != result.Package.ReceiptID || retry.Package.Seal != result.Package.Seal {
		t.Fatalf("same-request retry: %v", err)
	}
}

func TestIndexedIDFAvailabilityTransitionsAndCurrentGuards(t *testing.T) {
	ctx := context.Background()
	repo := uuid.NewString()
	s := testStore(t, Channel{Principal: "agent:semantic-idf-transitions", Repo: repo})
	record, err := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: Draft{Kind: "note", Body: "service key restart", Scope: Scope{repo, "*", "*"}, ClaimType: "self", Sensitivity: "shareable"}})
	if err != nil {
		t.Fatal(err)
	}
	mode := "ready"
	s.semanticRetriever = func(_ context.Context, r SemanticRankRequest) (SemanticRetrievalResult, error) {
		if mode == "unavailable" {
			return SemanticRetrievalResult{}, fmt.Errorf("synthetic unavailable")
		}
		hits := []SemanticPassageHit{}
		for _, n := range r.Notes {
			score := 500000
			if mode == "invalid" {
				score = 2000000
			}
			hits = append(hits, SemanticPassageHit{SemanticScore: SemanticScore{n.RecordID, n.Version, n.BodySHA256, score}, Span: ByteSpanRequest{0, len(n.Body)}})
		}
		return SemanticRetrievalResult{QueryProjection: projectionFixture(r.Query, len(r.Query), 3, 3), ModelSHA256: strings.Repeat("a", 64), Algorithm: "synthetic-passages/1", Indexed: len(r.Notes), Hits: hits}, nil
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "service-key restart", Purpose: "context", AvailableTokens: 32000, Semantic: true}
	ready, err := s.Index(ctx, req, Destination{Name: "hosted"})
	if err != nil {
		t.Fatal(err)
	}
	for _, state := range []string{"unavailable", "invalid"} {
		mode = state
		if _, err = s.Index(ctx, req, Destination{Name: "hosted"}); Code(err) != "STALE_PACKAGE" {
			t.Fatalf("ready-to-%s retry: %v", state, err)
		}
		replay, e := s.Recompile(ctx, RecompileRequest{ReceiptID: ready.Package.ReceiptID, Query: req.Query})
		if e != nil || replay.Seal != ready.Package.Seal {
			t.Fatalf("replay without working model: %v", e)
		}
		fallbackReq := req
		fallbackReq.RequestID = uuid.NewString()
		fallback, e := s.Index(ctx, fallbackReq, Destination{Name: "hosted"})
		if e != nil {
			t.Fatal(e)
		}
		if fallback.Package.Semantic.IDF != nil || fallback.Package.Semantic.Ranking != "lexical-scope-recency/4" || fallback.Package.Semantic.Status != "DEGRADED_NO_EMBEDDINGS" {
			t.Fatalf("fallback changed contract: %+v", fallback.Package.Semantic)
		}
		repeated, e := s.Index(ctx, fallbackReq, Destination{Name: "hosted"})
		if e != nil || repeated.Package.Seal != fallback.Package.Seal {
			t.Fatalf("stable fallback retry: %v", e)
		}
		mode = "ready"
		if _, e = s.Index(ctx, fallbackReq, Destination{Name: "hosted"}); Code(e) != "STALE_PACKAGE" {
			t.Fatalf("fallback-to-ready retry: %v", e)
		}
	}
	changed := req
	changed.Query = "different intent"
	if _, err = s.Index(ctx, changed, Destination{Name: "hosted"}); Code(err) != "IDEMPOTENCY_CONFLICT" {
		t.Fatalf("changed intent: %v", err)
	}
	if _, err = s.Revise(ctx, ReviseRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: record.Version, Repo: repo, Body: "service key restart amended"}); err != nil {
		t.Fatal(err)
	}
	if _, err = s.Index(ctx, req, Destination{Name: "hosted"}); Code(err) != "STALE_PACKAGE" {
		t.Fatalf("changed body retry: %v", err)
	}
	replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: ready.Package.ReceiptID, Query: req.Query})
	if err != nil || replay.Seal != ready.Package.Seal {
		t.Fatalf("retained version replay: %v", err)
	}
}

func TestIndexedIDFKeepsFilteredCompanionsWholeAndRequiredOutsideDF(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	create := func(kind, body string) Record {
		t.Helper()
		r, e := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: Draft{Kind: kind, Body: body, Scope: Scope{repo, "*", "*"}, ClaimType: "self", Sensitivity: "shareable"}})
		if e != nil {
			t.Fatal(e)
		}
		return r
	}
	a := create("decision", "service key restart allowed")
	b := create("note", "service-key restart prohibited")
	filtered := create("note", "service-key unrelated filtered source")
	zero := create("decision", "different vocabulary")
	if _, e := s.Dispute(ctx, DisputeRequest{RequestID: uuid.NewString(), RecordIDs: []string{a.RecordID, b.RecordID}, Reason: "synthetic competing advice"}); e != nil {
		t.Fatal(e)
	}
	mandatory, e := s.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: Draft{Kind: "instruction", Body: "service-key keep required safety context", Scope: Scope{repo, "*", "*"}, ClaimType: "self", Sensitivity: "shareable"}, GrantID: root.ID, Mandatory: true, PolicyKey: "synthetic-safety", Reason: "synthetic required context"})
	if e != nil {
		t.Fatal(e)
	}
	s.semanticRetriever = func(_ context.Context, r SemanticRankRequest) (SemanticRetrievalResult, error) {
		got := map[string]bool{}
		hits := []SemanticPassageHit{}
		for _, n := range r.Notes {
			got[n.RecordID] = true
			if n.RecordID == a.RecordID {
				hits = append(hits, SemanticPassageHit{SemanticScore: SemanticScore{n.RecordID, n.Version, n.BodySHA256, 900000}, Span: ByteSpanRequest{0, len(n.Body)}})
			}
		}
		if len(got) != 3 || !got[a.RecordID] || !got[b.RecordID] || !got[zero.RecordID] || got[mandatory.RecordID] || got[filtered.RecordID] {
			t.Fatalf("qualified cohort changed: %+v", got)
		}
		return SemanticRetrievalResult{QueryProjection: projectionFixture(r.Query, len(r.Query), 3, 3), ModelSHA256: strings.Repeat("a", 64), Algorithm: "synthetic-passages/1", Indexed: 3, Hits: hits}, nil
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "service-key restart", Purpose: "context", AvailableTokens: 64000, Semantic: true, AdvisoryConflicts: true, Kinds: []string{"decision"}}
	result, e := s.Index(ctx, req, Destination{Name: "hosted"})
	if e != nil {
		t.Fatal(e)
	}
	p := result.Package.Semantic
	if p.IDF == nil || p.IDF.N != 3 || len(p.Index) != 2 || len(p.Selected) != 1 || p.Selected[0].Record.RecordID != mandatory.RecordID {
		t.Fatalf("DF/group/required changed: %+v", p)
	}
	for _, entry := range p.Index {
		if len(entry.Conflicts) != 1 || entry.MatchSpan != nil || entry.SummarySpan != nil {
			t.Fatalf("partial conflicting guidance: %+v", entry)
		}
	}
	replay, e := s.Recompile(ctx, RecompileRequest{ReceiptID: result.Package.ReceiptID, Query: req.Query})
	if e != nil || replay.Seal != result.Package.Seal {
		t.Fatalf("filtered companion replay: %v", e)
	}
}

func TestIndexedIDFVariantReplayAndUnknownRefusal(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	d.Body = "panel board procedure"
	adjacent, err := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: d})
	if err != nil {
		t.Fatal(err)
	}
	d.Body = "panel has unrelated board words"
	if _, err = s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: d}); err != nil {
		t.Fatal(err)
	}
	s.semanticRetriever = func(_ context.Context, req SemanticRankRequest) (SemanticRetrievalResult, error) {
		return SemanticRetrievalResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "synthetic-passages/1", Indexed: len(req.Notes), Hits: []SemanticPassageHit{}}, nil
	}
	for _, c := range []struct {
		ranking, query, signature string
		entities                  []EntityRef
	}{
		{"interleaved-scope-recency/6", `"panel-board"`, "", nil},
		{"interleaved-scope-recency/7", "panel-board", strings.Repeat("a", 64), nil},
		{"interleaved-scope-recency/8", "panel-board", "", []EntityRef{{Kind: "file", Name: "src/panel.go"}}},
	} {
		req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: c.query, ErrorSignature: c.signature, Entities: c.entities, Purpose: "context", AvailableTokens: 32000, Semantic: true}
		got, err := s.Index(ctx, req, Destination{Name: "hosted"})
		if err != nil {
			t.Fatal(err)
		}
		p := got.Package
		if p.Semantic.Ranking != c.ranking || len(p.Semantic.IDF.Terms) != 3 || len(p.Semantic.Index) != 2 || p.Semantic.Index[0].RecordID != adjacent.RecordID {
			t.Fatalf("variant alias ranking: %+v", p.Semantic)
		}
		replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID, Query: req.Query, Entities: req.Entities})
		if err != nil || replay.Seal != p.Seal {
			t.Fatalf("variant %s replay: %v", c.ranking, err)
		}
		retry, err := s.Index(ctx, req, Destination{Name: "hosted"})
		if err != nil || retry.Package.Seal != p.Seal {
			t.Fatalf("variant retry: %v", err)
		}
		p.Semantic.Ranking = "interleaved-scope-recency/9"
		raw, seal, err := sealPackage(p.Semantic)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_receipt SET semantic_body=$2,seal=$3 WHERE receipt_id=$1`, p.ReceiptID, raw, seal); err != nil {
			t.Fatal(err)
		}
		if _, err = s.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID, Query: req.Query, Entities: req.Entities}); Code(err) != "REPLAY_INCOMPLETE" {
			t.Fatalf("unknown variant accepted: %v", err)
		}
	}
}
