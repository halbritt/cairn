package core

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestSemanticDiscoveryUsesEligibleNotesAndFrozenScores(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	d.Body = "The database lives in the private application directory."
	want, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	d.Body = "Unrelated fruit preferences."
	other, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	d.Sensitivity = "local"
	d.Body = "Do not send this local content to the scorer."
	if _, err = s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	d.Sensitivity = "shareable"
	d.Scope.TaskID = "another-task"
	if _, err = s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	d.Scope.TaskID = "*"
	d.Pins = &Applicability{Revision: strings.Repeat("b", 40)}
	if _, err = s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	d.Pins = nil
	d.Kind = "instruction"
	d.Body = "Always include source references."
	mandatory, err := s.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: root.ID, Mandatory: true, PolicyKey: "source", Reason: "Required context"})
	if err != nil {
		t.Fatal(err)
	}
	calls := 0
	s.semanticRanker = func(_ context.Context, req SemanticRankRequest) (SemanticRankResult, error) {
		calls++
		if req.Query != "storage" || len(req.Notes) != 2 {
			t.Fatalf("unexpected scorer input: %+v", req)
		}
		result := SemanticRankResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "fixture/1"}
		for _, n := range req.Notes {
			score := 100000
			if n.RecordID == want.RecordID {
				score = 900000
			} else if n.RecordID != other.RecordID {
				t.Fatalf("ineligible or mandatory note sent to model: %s", n.RecordID)
			}
			result.Scores = append(result.Scores, SemanticScore{n.RecordID, n.Version, n.BodySHA256, score})
		}
		return result, nil
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "storage", Purpose: "context", AvailableTokens: 32000, Semantic: true, Context: &ContextPins{Revision: strings.Repeat("a", 40)}}
	index, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	p := index.Package
	if calls != 1 || p.Semantic.Schema != "cairn.semantic/8" || p.Semantic.Discovery.State != "ready" || len(p.Semantic.Index) != 2 || p.Semantic.Index[0].RecordID != want.RecordID || len(p.Semantic.Selected) != 1 || p.Semantic.Selected[0].Record.RecordID != mandatory.RecordID {
		t.Fatalf("unexpected package: %+v", p)
	}
	s.semanticRanker = func(context.Context, SemanticRankRequest) (SemanticRankResult, error) {
		t.Fatal("historical recompile invoked model")
		return SemanticRankResult{}, nil
	}
	replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID, Query: req.Query})
	if err != nil || replay.Seal != p.Seal {
		t.Fatalf("recompile: %v", err)
	}
	var handle string
	for _, h := range index.Handles {
		if h.RecordID == want.RecordID {
			handle = h.Handle
		}
	}
	pull := ExpandRequest{uuid.NewString(), p.ReceiptID, handle, nil}
	expanded, err := s.Expand(ctx, pull, Destination{"hosted", false})
	if err != nil || expanded.Selection.Record.Body != want.Body {
		t.Fatalf("exact source pull: %v", err)
	}
	_, err = s.Revise(ctx, ReviseRequest{RequestID: uuid.NewString(), RecordID: want.RecordID, ExpectedVersion: 1, Repo: repo, Body: "Corrected database directory."})
	if err != nil {
		t.Fatal(err)
	}
	pull.RequestID = uuid.NewString()
	_, err = s.Expand(ctx, pull, Destination{"hosted", false})
	requireCode(t, err, "STALE_HANDLE")
	replay, err = s.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID, Query: req.Query})
	if err != nil || replay.Seal != p.Seal {
		t.Fatalf("recompile after source edit: %v", err)
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_candidate SET detail=jsonb_set(detail,'{semantic_score}','500000') WHERE receipt_id=$1 AND record_id=$2`, p.ReceiptID, want.RecordID); err != nil {
		t.Fatal(err)
	}
	_, err = s.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID, Query: req.Query})
	requireCode(t, err, "INTEGRITY_FAILURE")
}

func TestSemanticFailureFallsBackWithoutChangingLexicalSelection(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "semantic-fallback"})
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Body = "database directory"
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	for _, malformed := range []bool{false, true} {
		s.semanticRanker = func(_ context.Context, req SemanticRankRequest) (SemanticRankResult, error) {
			if !malformed {
				return SemanticRankResult{}, errors.New("model unavailable")
			}
			return SemanticRankResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "fixture/1", Scores: []SemanticScore{{uuid.NewString(), 1, req.Notes[0].BodySHA256, 900000}}}, nil
		}
		for _, query := range []string{"database", "storage"} {
			req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: query, Purpose: "context", AvailableTokens: 32000, Semantic: true}
			index, err := s.Index(ctx, req, Destination{"local", true})
			if err != nil {
				t.Fatal(err)
			}
			want := 0
			if query == "database" {
				want = 1
			}
			state := "unavailable"
			if malformed {
				state = "invalid_result"
			}
			if index.Package.Semantic.Status != "DEGRADED_NO_EMBEDDINGS" || index.Package.Semantic.Discovery.State != state || len(index.Package.Semantic.Index) != want || index.Package.Semantic.Ranking != "lexical-scope-recency/4" {
				t.Fatalf("fallback: %+v", index)
			}
			replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: query})
			if err != nil || replay.Seal != index.Package.Seal {
				t.Fatalf("fallback replay: %v", err)
			}
		}
	}
}

func TestSemanticFallbackBannerFitsTheActualBudget(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "semantic-banner"})
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Body = "database directory"
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "storage", Purpose: "context", AvailableTokens: 64000, Semantic: true}
	first, err := s.Index(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	rendered, err := first.Package.Render()
	if err != nil {
		t.Fatal(err)
	}
	boundary := len(rendered) + 512
	success := false
	for budget := boundary - 8; budget <= boundary+1; budget++ {
		req.RequestID = uuid.NewString()
		req.AvailableTokens = budget
		index, err := s.Index(ctx, req, Destination{"local", true})
		if Code(err) == "BUDGET_REFUSED" {
			continue
		}
		if err != nil {
			t.Fatal(err)
		}
		success = true
		actual, err := index.Package.Render()
		if err != nil {
			t.Fatal(err)
		}
		if len(actual)+512 > budget || index.Package.Semantic.Status != "DEGRADED_NO_EMBEDDINGS" {
			t.Fatalf("fallback exceeded budget %d with %d bytes", budget, len(actual)+512)
		}
	}
	if !success {
		t.Fatal("no fitting fallback envelope admitted")
	}
}

func TestSemanticDiscoveryCannotBeUsedForAuthorityOrBrowsing(t *testing.T) {
	s := &Store{}
	for _, req := range []CompileRequest{{Semantic: true}, {Semantic: true, Mode: "index", Purpose: "placement", Query: "x"}, {Semantic: true, Mode: "index", Purpose: "context", Query: " "}} {
		_, err := s.Compile(context.Background(), req, Destination{"local", true})
		requireCode(t, err, "INVALID_REQUEST")
	}
}
