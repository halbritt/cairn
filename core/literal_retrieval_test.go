package core

import (
	"context"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/google/uuid"
)

func TestQuotedFileMatchPrecedesLexicalOverlap(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:literal-retrieval"})
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Body = "Inspect core/currentness.go for applicability precedence."
	exact, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	draft.Body = "The core module uses Go. Currentness repair review checks belong here."
	lexical, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	query := `repair review "core/currentness.go"`
	for _, mode := range []string{"", "index"} {
		p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Mode: mode, Query: query, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
		if err != nil {
			t.Fatal(err)
		}
		var ids []string
		for _, entry := range p.Semantic.Selected {
			ids = append(ids, entry.Record.RecordID)
		}
		for _, entry := range p.Semantic.Index {
			ids = append(ids, entry.RecordID)
		}
		if len(ids) != 2 || ids[0] != exact.RecordID || ids[1] != lexical.RecordID {
			t.Fatalf("mode %q: wanted exact path before lexical fallback, got %v", mode, ids)
		}
		replayed, err := s.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID, Query: query})
		if err != nil || replayed.Seal != p.Seal {
			t.Fatalf("quoted intent did not recompile: %v", err)
		}
	}
}

func TestQuotedMatchPreviewShowsExactSourceSpan(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:literal-preview"})
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Body = "Core currentness Go repair review overview. " + strings.Repeat("Unrelated background. ", 20) + "Inspect core/currentness.go for precedence."
	note, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	query := `repair review "core/currentness.go"`
	p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Mode: "index", Query: query, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if len(p.Semantic.Index) != 1 {
		t.Fatal("note missing")
	}
	entry := p.Semantic.Index[0]
	if !strings.Contains(entry.Summary, "core/currentness.go") || entry.SummarySpan == nil || entry.SummarySpan.Offset == 0 {
		t.Fatalf("preview hides literal match: %+v", entry)
	}
	span := entry.SummarySpan
	if !strings.Contains(entry.Summary, note.Body[span.Offset:span.Offset+span.Length]) || len(entry.Summary) > 160 {
		t.Fatal("preview does not preserve bounded source bytes")
	}
	replayed, err := s.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID, Query: query})
	if err != nil || replayed.Seal != p.Seal {
		t.Fatalf("preview did not recompile: %v", err)
	}
}

func TestQuotedMatchingPreservesGatesAndSemanticFallback(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity, d.Body = "shareable", "The phrase to be is the relevant literal."
	exact, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	d.Body = "An unrelated lexical fallback."
	other, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	d.Body, d.Kind = "Required citations.", "instruction"
	required, err := s.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: root.ID, Mandatory: true, PolicyKey: "references", Reason: "Required context"})
	if err != nil {
		t.Fatal(err)
	}
	d.Kind, d.Body, d.Pins = "note", "to be stale", &Applicability{TaskPhase: "other"}
	if _, err = s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	d.Pins, d.Body, d.Sensitivity = nil, "to be private", "local"
	private, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	d.Sensitivity, d.Body, d.Kind = "shareable", "to be filtered", "decision"
	if _, err = s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"body", "index", "semantic-ready", "semantic-unavailable", "page"} {
		req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", mode}, Query: `"to be" fallback`, Kinds: []string{"note"}, Context: &ContextPins{TaskPhase: "implementation"}, Purpose: "context", AvailableTokens: 64000}
		s.semanticRanker = nil
		if mode != "body" {
			req.Mode = "index"
		}
		if strings.HasPrefix(mode, "semantic") {
			req.Semantic = true
		}
		if mode == "page" {
			req.PageOffset = new(int)
		}
		if mode == "semantic-ready" {
			s.semanticRanker = func(_ context.Context, req SemanticRankRequest) (SemanticRankResult, error) {
				if len(req.Notes) != 2 {
					t.Fatalf("gated notes reached scorer: %+v", req.Notes)
				}
				result := SemanticRankResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "fixture/1"}
				for _, n := range req.Notes {
					score := 900000
					if n.RecordID == exact.RecordID {
						score = -1000000
					} else if n.RecordID != other.RecordID {
						t.Fatal("unexpected note reached scorer")
					}
					result.Scores = append(result.Scores, SemanticScore{n.RecordID, n.Version, n.BodySHA256, score})
				}
				return result, nil
			}
		}
		p, err := s.Compile(ctx, req, Destination{"hosted", false})
		if err != nil {
			t.Fatal(mode, err)
		}
		if len(p.Semantic.Selected) == 0 || p.Semantic.Selected[0].Record.RecordID != required.RecordID {
			t.Fatal(mode, "mandatory context missing")
		}
		ids := []string{}
		for _, e := range p.Semantic.Selected {
			if !e.Mandatory {
				ids = append(ids, e.Record.RecordID)
			}
		}
		for _, e := range p.Semantic.Index {
			ids = append(ids, e.RecordID)
		}
		if len(ids) != 2 || ids[0] != exact.RecordID || ids[1] != other.RecordID {
			t.Fatalf("%s: exact match or fallback lost: %v", mode, ids)
		}
		explanation, err := s.Explain(ctx, p.ReceiptID)
		if err != nil {
			t.Fatal(err)
		}
		for _, e := range explanation.Candidates {
			if e.RecordID == private.RecordID {
				t.Fatal("private candidate leaked")
			}
			if e.RecordID == exact.RecordID && (!e.ExactTextMatch || e.LexicalMatches != 0) {
				t.Fatalf("literal match with zero lexical terms lost: %+v", e)
			}
		}
		s.semanticRanker = func(context.Context, SemanticRankRequest) (SemanticRankResult, error) {
			t.Fatal("replay invoked scorer")
			return SemanticRankResult{}, nil
		}
		replayed, err := s.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID, Query: req.Query})
		if err != nil || replayed.Seal != p.Seal {
			t.Fatalf("%s replay: %v", mode, err)
		}
	}
}

func TestQuotedPreferencesAreBoundedAndDoNotMultiplyMatches(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:literal-bounds"})
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Body = "Earlier CAIRN_HOME note."
	earlier, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	d.Body = "Later CAIRN_HOME note."
	later, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	for _, query := range []string{`"CAIRN_HOME"`, `"CAIRN_HOME" "CAIRN_HOME"`, `"cairn_home"`, `"missing" CAIRN_HOME`, `CAIRN_HOME "unterminated`, `" " CAIRN_HOME`, `"` + strings.Repeat("x", 257) + `" CAIRN_HOME`} {
		p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: query, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
		if err != nil {
			t.Fatal(err)
		}
		selected := p.Semantic.Selected
		if len(selected) != 2 || selected[0].Record.RecordID != later.RecordID || selected[1].Record.RecordID != earlier.RecordID {
			t.Fatalf("query %q lost lexical fallback or changed equal-match tiebreak: %+v", query, selected)
		}
		replayed, err := s.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID, Query: query})
		if err != nil || replayed.Seal != p.Seal {
			t.Fatal(query, err)
		}
	}
	// A ninth distinct literal is ordinary lexical text; duplicate hints do not
	// spend the eight distinct literal slots or create a relevance multiplier.
	query := `"absent1" "absent2" "absent3" "absent4" "absent5" "absent6" "absent7" "absent8" "CAIRN_HOME"`
	p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: query, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(p.Semantic.Selected[0].Reason, "exact quoted") {
		t.Fatal("ninth literal exceeded matching bound")
	}
}

func TestQuotedLiteralSourcesRetainExactBytesAndLivePullChecks(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:literal-source"})
	for _, literal := range []string{"quota exceeded for binding", "src/Config.go", strings.Repeat("abcdef01", 8), strings.Repeat("é", 128)} {
		repo := uuid.NewString()
		d := projectNote(repo)
		d.Body = strings.Repeat("Earlier context. ", 20) + literal
		exact, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
		if err != nil {
			t.Fatal(err)
		}
		d.Body = "Different case: " + strings.ToUpper(literal)
		other, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
		if err != nil {
			t.Fatal(err)
		}
		query := `"` + literal + `"`
		index, err := s.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: query, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
		if err != nil {
			t.Fatal(err)
		}
		entries := index.Package.Semantic.Index
		if len(entries) != 2 || entries[0].RecordID != exact.RecordID || entries[1].RecordID != other.RecordID {
			t.Fatal("exact case lost precedence")
		}
		span := entries[0].SummarySpan
		if span == nil || !utf8.ValidString(entries[0].Summary) || len(entries[0].Summary) > 160 || !strings.Contains(entries[0].Summary, exact.Body[span.Offset:span.Offset+span.Length]) {
			t.Fatal("invalid literal preview span")
		}
		_, err = s.Revise(ctx, ReviseRequest{RequestID: uuid.NewString(), RecordID: exact.RecordID, ExpectedVersion: 1, Repo: repo, Body: "Revised unrelated guidance."})
		if err != nil {
			t.Fatal(err)
		}
		for _, h := range index.Handles {
			if h.RecordID == exact.RecordID {
				_, err = s.Expand(ctx, ExpandRequest{RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, Handle: h.Handle}, Destination{"local", true})
				requireCode(t, err, "STALE_HANDLE")
			}
		}
		replayed, err := s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: query})
		if err != nil || replayed.Seal != index.Package.Seal {
			t.Fatalf("literal history changed after edit: %v", err)
		}
	}
}
