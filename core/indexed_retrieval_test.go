package core

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestIndexedDiscoveryFindsPassageBeyond64NotesAndReplays(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
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
