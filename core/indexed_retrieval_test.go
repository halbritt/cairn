package core

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

func TestIndexedDiscoveryFindsPassageBeyond64NotesAndReplays(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	prefix := strings.Repeat("Unrelated historical introduction. ", 30)
	passage := "Keep the database in the private application directory."
	d.Body = prefix + passage
	want, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	for i := range 64 {
		d.Body = fmt.Sprintf("Unrelated fruit preferences %d.", i)
		if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
			t.Fatal(err)
		}
	}
	d.Sensitivity = "local"
	d.Body = "Private guidance must never reach the hosted retrieval candidate set."
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	d.Sensitivity = "shareable"
	d.Scope.TaskID = "another-task"
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	d.Scope.TaskID = "*"
	d.Pins = &Applicability{Revision: strings.Repeat("b", 40)}
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	d.Pins = nil
	d.Kind, d.Body = "instruction", "Always include source references."
	mandatory, err := s.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: root.ID, Mandatory: true, PolicyKey: "source", Reason: "Required context"})
	if err != nil {
		t.Fatal(err)
	}
	calls := 0
	s.semanticRetriever = func(_ context.Context, req SemanticRankRequest) (SemanticRetrievalResult, error) {
		calls++
		if len(req.Notes) != 65 {
			t.Fatalf("eligible sources = %d, want 65", len(req.Notes))
		}
		for _, n := range req.Notes {
			if n.RecordID == want.RecordID {
				return SemanticRetrievalResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "passage-fixture/1", Indexed: 65, Hits: []SemanticPassageHit{{SemanticScore: SemanticScore{n.RecordID, n.Version, n.BodySHA256, 900000}, Span: ByteSpanRequest{Offset: len(prefix), Length: len(passage)}}}}, nil
			}
		}
		t.Fatal("expected source absent from eligible candidates")
		return SemanticRetrievalResult{}, nil
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "storage location", Purpose: "context", AvailableTokens: 32000, Semantic: true}
	index, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	p := index.Package
	if p.Semantic.Schema != "cairn.semantic/16" || p.Semantic.Ranking != "interleaved-scope-recency/1" || len(p.Semantic.Selected) != 1 || p.Semantic.Selected[0].Record.RecordID != mandatory.RecordID {
		t.Fatalf("new contract or mandatory context lost: %+v", p.Semantic)
	}
	if calls != 1 || p.Semantic.Discovery.State != "ready" || len(p.Semantic.Index) != 1 || p.Semantic.Index[0].RecordID != want.RecordID {
		t.Fatalf("semantic discovery beyond 64 notes failed: calls=%d package=%+v", calls, p.Semantic)
	}
	entry := p.Semantic.Index[0]
	if !strings.Contains(entry.Summary, passage) || entry.SummarySpan == nil || entry.SummarySpan.Offset != len(prefix) {
		t.Fatalf("winning passage missing from preview: %+v", entry)
	}
	s.semanticRetriever = func(context.Context, SemanticRankRequest) (SemanticRetrievalResult, error) {
		t.Fatal("recompile invoked retrieval")
		return SemanticRetrievalResult{}, nil
	}
	replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID, Query: req.Query})
	if err != nil || replay.Seal != p.Seal {
		t.Fatalf("frozen replay: %v", err)
	}
	if _, err := s.Revise(ctx, ReviseRequest{RequestID: uuid.NewString(), RecordID: want.RecordID, ExpectedVersion: 1, Repo: repo, Body: "The directory has moved."}); err != nil {
		t.Fatal(err)
	}
	_, err = s.Expand(ctx, ExpandRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, Handle: index.Handles[0].Handle}, Destination{"hosted", false})
	requireCode(t, err, "STALE_HANDLE")
}

func TestIndexedDiscoveryInvalidOrUnavailableIndexKeepsLexicalAndReplay(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	d.Body = "database storage location"
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"failure", "empty", "wrong-version", "unknown-source", "bad-span", "duplicate"} {
		t.Run(mode, func(t *testing.T) {
			s.semanticRetriever = func(_ context.Context, req SemanticRankRequest) (SemanticRetrievalResult, error) {
				if mode == "failure" {
					return SemanticRetrievalResult{}, errors.New("worker unavailable")
				}
				if mode == "empty" {
					return SemanticRetrievalResult{}, nil
				}
				n := req.Notes[0]
				h := SemanticPassageHit{SemanticScore: SemanticScore{n.RecordID, n.Version, n.BodySHA256, 900000}, Span: ByteSpanRequest{Length: len(n.Body)}}
				switch mode {
				case "wrong-version":
					h.Version++
				case "unknown-source":
					h.RecordID = uuid.NewString()
				case "bad-span":
					h.Span.Offset = 65536
				}
				result := SemanticRetrievalResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "fixture/1", Indexed: 1, Hits: []SemanticPassageHit{h}}
				if mode == "duplicate" {
					result.Hits = append(result.Hits, h)
				}
				return result, nil
			}
			req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "storage", Purpose: "context", AvailableTokens: 32000, Semantic: true}
			idx, err := s.Index(ctx, req, Destination{"hosted", false})
			if err != nil {
				t.Fatal(err)
			}
			if idx.Package.Semantic.Status != "DEGRADED_NO_EMBEDDINGS" || len(idx.Package.Semantic.Index) != 1 || idx.Package.Semantic.Index[0].RecordID != r.RecordID {
				t.Fatalf("lexical fallback lost: %+v", idx.Package.Semantic)
			}
			replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: idx.Package.ReceiptID, Query: req.Query})
			if err != nil || replay.Seal != idx.Package.Seal {
				t.Fatalf("fallback replay: %v", err)
			}
		})
	}
}

func TestIndexedDiscoverySupportsCombinedHintsAndPartialCoverage(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	d.Kind = "lesson"
	d.Body = "Database directory guidance."
	d.Entities = []EntityRef{{Kind: "file", Name: "store.go"}}
	one, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	d.Body = "Storage guidance captured while indexing catches up."
	d.Entities = nil
	two, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	s.semanticRetriever = func(_ context.Context, req SemanticRankRequest) (SemanticRetrievalResult, error) {
		for _, n := range req.Notes {
			if n.RecordID == one.RecordID {
				return SemanticRetrievalResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "fixture/1", Indexed: 1, Hits: []SemanticPassageHit{{SemanticScore: SemanticScore{n.RecordID, n.Version, n.BodySHA256, 900000}, Span: ByteSpanRequest{Length: len(n.Body)}}}}, nil
			}
		}
		t.Fatal("missing indexed source")
		return SemanticRetrievalResult{}, nil
	}
	offset := 0
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "storage \"directory\"", Purpose: "context", AvailableTokens: 32000, Semantic: true, Entities: []EntityRef{{Kind: "file", Name: "store.go"}}, Kinds: []string{"lesson"}, PageOffset: &offset, AdvisoryConflicts: true}
	idx, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	p := idx.Package.Semantic
	if p.Discovery.Coverage.Indexed != 1 || p.Discovery.Coverage.Eligible != 2 || len(p.Index) != 2 || p.Index[0].RecordID != one.RecordID || p.Index[1].RecordID != two.RecordID {
		t.Fatalf("partial coverage/hints: %+v", p)
	}
	replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: idx.Package.ReceiptID, Query: req.Query, Entities: req.Entities})
	if err != nil || replay.Seal != idx.Package.Seal {
		t.Fatalf("combined replay: %v", err)
	}
}

func TestIndexedDiscoveryExposesBothChannelsBeforeOverlappingMatches(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	create := func(body string) Record {
		t.Helper()
		d.Body = body
		r, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
		if err != nil {
			t.Fatal(err)
		}
		return r
	}
	lexical := create("anchor topic guidance: use the established procedure")
	dense := create("Independent operational constraint without shared query words")
	for i := range 32 {
		create(fmt.Sprintf("anchor topic status update %d", i))
	}
	s.semanticRetriever = func(_ context.Context, req SemanticRankRequest) (SemanticRetrievalResult, error) {
		result := SemanticRetrievalResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "fixture/1", Indexed: len(req.Notes)}
		for _, n := range req.Notes {
			if n.RecordID == lexical.RecordID {
				continue
			}
			score := 500000
			if n.RecordID == dense.RecordID {
				score = 900000
			}
			result.Hits = append(result.Hits, SemanticPassageHit{SemanticScore: SemanticScore{n.RecordID, n.Version, n.BodySHA256, score}, Span: ByteSpanRequest{Length: len(n.Body)}})
		}
		return result, nil
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "anchor topic guidance", Purpose: "context", AvailableTokens: 32000, Semantic: true, PageOffset: new(int)}
	index, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	entries := index.Package.Semantic.Index
	if len(entries) < 2 || entries[0].RecordID != lexical.RecordID || entries[1].RecordID != dense.RecordID {
		t.Fatalf("each channel's strongest source must precede overlapping mediocre sources: %+v", entries)
	}
	seen := map[string]bool{}
	page := index
	for {
		for _, entry := range page.Package.Semantic.Index {
			if seen[entry.RecordID] {
				t.Fatal("pagination duplicated an exposed record")
			}
			seen[entry.RecordID] = true
		}
		replayed, err := s.Recompile(ctx, RecompileRequest{ReceiptID: page.Package.ReceiptID, Query: req.Query})
		if err != nil || replayed.Seal != page.Package.Seal {
			t.Fatalf("page replay: %v", err)
		}
		if page.Package.Semantic.Page.NextOffset == nil {
			break
		}
		req.PageOffset = page.Package.Semantic.Page.NextOffset
		req.RequestID = uuid.NewString()
		page, err = s.Index(ctx, req, Destination{"hosted", false})
		if err != nil {
			t.Fatal(err)
		}
	}
	if len(seen) != 34 {
		t.Fatalf("pagination omitted channel candidates: %d", len(seen))
	}
	s.semanticRetriever = func(context.Context, SemanticRankRequest) (SemanticRetrievalResult, error) {
		t.Fatal("replay called semantic retriever")
		return SemanticRetrievalResult{}, nil
	}
	replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query})
	if err != nil || replay.Seal != index.Package.Seal {
		t.Fatalf("replay: %v", err)
	}
}

func TestIndexedDiscoveryHistoricalRRFAndInterleavedReceiptsReplaySeparately(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	ids := []string{}
	for _, body := range []string{"alpha beta gamma", "independent constraint", "alpha beta"} {
		d.Body = body
		r, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
		if err != nil {
			t.Fatal(err)
		}
		ids = append(ids, r.RecordID)
	}
	s.semanticRetriever = func(_ context.Context, req SemanticRankRequest) (SemanticRetrievalResult, error) {
		r := SemanticRetrievalResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "fixture/1", Indexed: len(req.Notes)}
		for _, n := range req.Notes {
			if n.RecordID == ids[0] {
				continue
			}
			score := 500000
			if n.RecordID == ids[1] {
				score = 900000
			}
			r.Hits = append(r.Hits, SemanticPassageHit{SemanticScore: SemanticScore{n.RecordID, n.Version, n.BodySHA256, score}, Span: ByteSpanRequest{Length: len(n.Body)}})
		}
		return r, nil
	}
	for _, contract := range []struct{ schema, ranking, first string }{
		{"cairn.semantic/15", "hybrid-scope-recency/1", ids[2]},
		{"cairn.semantic/16", "interleaved-scope-recency/1", ids[0]},
	} {
		t.Run(contract.ranking, func(t *testing.T) {
			// Construct a synthetic historical receipt under its explicit contract.
			// The same source facts distinguish old RRF from new exposure ordering.
			req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "alpha beta gamma", Purpose: "context", AvailableTokens: 32000, Semantic: true, Mode: "index"}
			tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
			if err != nil {
				t.Fatal(err)
			}
			defer tx.Rollback(ctx)
			evaluations := map[string]*CandidateEvaluation{}
			p, candidates, err := s.collectCandidates(ctx, tx, req, Destination{"hosted", false}, evaluations)
			if err != nil {
				t.Fatal(err)
			}
			p.Mode = "index"
			if _, err = s.rankIndexed(ctx, req.Query, &p, candidates, evaluations); err != nil {
				t.Fatal(err)
			}
			p.Schema, p.Ranking = contract.schema, contract.ranking
			candidates, err = rankIndexedCandidates(&p, candidates, evaluations)
			if err != nil {
				t.Fatal(err)
			}
			p, err = packIndex(p, candidates, evaluations, req.Query)
			if err != nil {
				t.Fatal(err)
			}
			if len(p.Index) != 3 || p.Index[0].RecordID != contract.first {
				t.Fatalf("wrong contract ordering: %+v", p.Index)
			}
			canonical, seal, err := sealPackage(p)
			if err != nil {
				t.Fatal(err)
			}
			pkg, err := s.commitRetrieval(ctx, tx, req, p, canonical, seal, evaluations)
			if err != nil {
				t.Fatal(err)
			}
			if err = tx.Commit(ctx); err != nil {
				t.Fatal(err)
			}
			retriever := s.semanticRetriever
			s.semanticRetriever = func(context.Context, SemanticRankRequest) (SemanticRetrievalResult, error) {
				t.Fatal("replay invoked a model")
				return SemanticRetrievalResult{}, nil
			}
			defer func() { s.semanticRetriever = retriever }()
			replayed, err := s.Recompile(ctx, RecompileRequest{ReceiptID: pkg.ReceiptID, Query: req.Query})
			if err != nil || replayed.Seal != pkg.Seal || replayed.Semantic.Index[0].RecordID != contract.first {
				t.Fatalf("contract replay: %v", err)
			}
			// The schema and ranking form one contract, never interchangeable labels.
			mismatched := p
			if contract.schema == "cairn.semantic/15" {
				mismatched.Schema = "cairn.semantic/16"
			} else {
				mismatched.Schema = "cairn.semantic/15"
			}
			requireCode(t, validateFrozenDiscovery(mismatched, evaluations, req.Query), "INTEGRITY_FAILURE")
			if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_candidate SET detail=jsonb_set(detail,'{passage_hit,score}','1') WHERE receipt_id=$1 AND record_id=$2`, pkg.ReceiptID, ids[1]); err != nil {
				t.Fatal(err)
			}
			_, err = s.Recompile(ctx, RecompileRequest{ReceiptID: pkg.ReceiptID, Query: req.Query})
			requireCode(t, err, "INTEGRITY_FAILURE")
		})
	}
}

func TestInterleavedExposureBoundsAndDuplicateMembership(t *testing.T) {
	p := SemanticPackage{Ranking: "interleaved-scope-recency/1", Omitted: omissionCensus()}
	candidates := []candidate{}
	evaluations := map[string]*CandidateEvaluation{}
	// Two disjoint bounded lists plus one lexical overflow candidate. The dense
	// first hit is also the lexical first: it must not consume a second slot.
	for i := range 201 {
		id := fmt.Sprintf("record-%03d", i)
		matches := 0
		if i < 101 {
			matches = 101 - i
		}
		candidates = append(candidates, candidate{selection: Selection{Record: Record{RecordID: id}}, score: matches})
		e := &CandidateEvaluation{RecordID: id, LexicalMatches: matches}
		if i == 0 || i > 101 {
			e.PassageHit = &SemanticPassageHit{SemanticScore: SemanticScore{RecordID: id, Score: 1000000 - i}}
		}
		evaluations[id] = e
	}
	kept, err := rankIndexedCandidates(&p, candidates, evaluations)
	if err != nil {
		t.Fatal(err)
	}
	sortCandidates(kept)
	if len(kept) != 199 {
		t.Fatalf("bounded union got %d records, want 199", len(kept))
	}
	seen := map[string]bool{}
	for _, c := range kept {
		id := c.selection.Record.RecordID
		if seen[id] || id == "record-100" || id == "record-101" {
			t.Fatalf("duplicate or unbounded exposure: %s", id)
		}
		seen[id] = true
	}
	want := []string{"record-000", "record-001", "record-102", "record-002", "record-103"}
	for i, id := range want {
		if kept[i].selection.Record.RecordID != id {
			t.Fatalf("rank %d: got %s want %s", i+1, kept[i].selection.Record.RecordID, id)
		}
	}
}
