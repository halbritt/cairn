package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"math/rand"
	"regexp"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestAdjacentCompoundIndexAndReplay(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	d.Body = "white CMF reporting guidance"
	correct, err := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: d})
	if err != nil {
		t.Fatal(err)
	}
	d.Body = "white materials; unrelated CMF elsewhere"
	if _, err = s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: d}); err != nil {
		t.Fatal(err)
	}
	d.Body = "unrelated zero match"
	if _, err = s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: d}); err != nil {
		t.Fatal(err)
	}
	d.Body = "white CMF hidden alias"
	d.Sensitivity = "local"
	private, err := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: d})
	if err != nil {
		t.Fatal(err)
	}
	d.Sensitivity = "shareable"
	d.Scope.TaskID = "other"
	if _, err = s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: d}); err != nil {
		t.Fatal(err)
	}
	d.Scope.TaskID = "*"
	d.Pins = &Applicability{Revision: strings.Repeat("b", 40)}
	if _, err = s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: d}); err != nil {
		t.Fatal(err)
	}
	d.Pins = nil
	d.Kind = "instruction"
	d.Body = "Required white CMF context"
	required, err := s.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: root.ID, Mandatory: true, PolicyKey: "compound-required", Reason: "Required context"})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "white-cmf", Purpose: "context", AvailableTokens: 32000, Context: &ContextPins{Revision: strings.Repeat("a", 40)}}
	got, err := s.Index(ctx, req, Destination{Name: "hosted"})
	if err != nil {
		t.Fatal(err)
	}
	if got.Package.Semantic.Ranking != "binary-idf-scope-recency/5" || len(got.Package.Semantic.Index) != 2 || got.Package.Semantic.Index[0].RecordID != correct.RecordID {
		t.Fatalf("adjacent source must outrank distant words under new contract: %+v", got.Package.Semantic)
	}
	if got.Package.Semantic.IDF == nil || got.Package.Semantic.IDF.N != 3 || len(got.Package.Semantic.IDF.Terms) != 3 {
		t.Fatalf("alias/zero-match IDF cohort: %+v", got.Package.Semantic.IDF)
	}
	if len(got.Package.Semantic.Selected) != 1 || got.Package.Semantic.Selected[0].Record.RecordID != required.RecordID {
		t.Fatal("required context changed")
	}
	explanation, err := s.Explain(ctx, got.Package.ReceiptID)
	if err != nil {
		t.Fatal(err)
	}
	for _, candidate := range explanation.Candidates {
		if candidate.RecordID == private.RecordID {
			t.Fatal("private candidate disclosed")
		}
	}
	aliasDigest := sha256.Sum256([]byte("white-cmf"))
	found := false
	for _, term := range got.Package.Semantic.IDF.Terms {
		if term.Digest == hex.EncodeToString(aliasDigest[:]) {
			found = true
			if term.DF != 1 {
				t.Fatalf("alias DF includes ineligible/mandatory sources: %d", term.DF)
			}
		}
	}
	if !found {
		t.Fatal("alias absent from sealed statistics")
	}
	replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: got.Package.ReceiptID, Query: req.Query})
	if err != nil || replay.Seal != got.Package.Seal {
		t.Fatalf("replay: %v", err)
	}
	retry, err := s.Index(ctx, req, Destination{Name: "hosted"})
	if err != nil || retry.Package.ReceiptID != got.Package.ReceiptID || retry.Package.Seal != got.Package.Seal {
		t.Fatalf("retry: %v", err)
	}
}

func TestAdjacentCompoundBoundariesAndHistoricalTerms(t *testing.T) {
	yes := []string{"white-cmf", "white cmf", "WHITE CMF", "white\tcmf", "white\u00a0cmf", "(white cmf)", "white\u2003cmf"}
	no := []string{"white - cmf", "white--cmf", "white\ncmf", "white\rcmf", "white\vcmf", "white\fcmf", "white\u0085cmf", "white\u2028cmf", "white\u2029cmf", "white, cmf", "white very cmf", "off-white-cmf", "white-cmf-old", "foowhite cmf", "white cmfextra", "white_cmf", "-white-cmf", "white-cmf-", "²white cmf", "white cmf²"}
	l := newRankingLexicon("white-cmf", "binary-idf-scope-recency/5")
	for _, body := range yes {
		if !l.bodyTerms(body)["white-cmf"] {
			t.Errorf("missing alias: %q", body)
		}
	}
	for _, body := range no {
		if l.bodyTerms(body)["white-cmf"] {
			t.Errorf("false alias: %q", body)
		}
	}
	for _, q := range []string{"--white-cmf", "white--cmf", "white-cmf-"} {
		if len(newRankingLexicon(q, "binary-idf-scope-recency/5").compounds) != 0 {
			t.Errorf("malformed query compound: %q", q)
		}
	}
	for _, c := range []struct{ q, body, term string }{{"Ägent-BÖard", "ÄGENT böard", "ägent-böard"}, {"source_id-v2", "source_id v2", "source_id-v2"}, {"a-b-c", "a-b c", "a-b-c"}, {"İ-x", "i x", "i-x"}} {
		if !newRankingLexicon(c.q, "binary-idf-scope-recency/5").bodyTerms(c.body)[c.term] {
			t.Errorf("Unicode/underscore/multipart: %+v", c)
		}
	}
	if newRankingLexicon("source_id-v2", "binary-idf-scope-recency/5").bodyTerms("source id v2")["source_id-v2"] {
		t.Fatal("underscore alias invented")
	}
	q := `must not "white-cmf" seems stalled`
	for _, r := range []string{"binary-idf-scope-recency/1", "binary-idf-scope-recency/2", "binary-idf-scope-recency/3", "binary-idf-scope-recency/4", "interleaved-scope-recency/2", "lexical-scope-recency/5"} {
		old := newRankingLexicon(q, r)
		if old.terms["white-cmf"] || old.bodyTerms("white-cmf")["white-cmf"] {
			t.Fatalf("historical/semantic terms changed: %s", r)
		}
	}
	for _, term := range []string{"must", "not", "seems", "stalled", "white", "cmf", "white-cmf"} {
		if !newRankingLexicon(q, "binary-idf-scope-recency/6").terms[term] {
			t.Errorf("term lost: %s", term)
		}
	}
	if !matchesLiteral("white-cmf", queryLiterals(q)) || matchesLiteral("white cmf", queryLiterals(q)) {
		t.Fatal("literal preference changed")
	}
}

func TestAdjacentCompoundPreviewAndOuterTiers(t *testing.T) {
	body := strings.Repeat("filler ", 30) + "white CMF alias; " + strings.Repeat("filler ", 30) + "exact white-cmf literal."
	for _, r := range []string{"binary-idf-scope-recency/5", "binary-idf-scope-recency/6", "binary-idf-scope-recency/7", "binary-idf-scope-recency/8"} {
		old := legacyIDFRanking(r)
		got, span := indexPreview(body, `"white-cmf"`, r)
		want, wspan := indexPreview(body, `"white-cmf"`, old)
		if got != want || span != wspan {
			t.Fatalf("preview drift: %s", r)
		}
		if hasLiteralRanking(r) != hasLiteralRanking(old) || hasFailureRanking(r) != hasFailureRanking(old) || hasEntityRanking(r) != hasEntityRanking(old) {
			t.Fatalf("outer tier drift: %s", r)
		}
	}
}

func TestHistoricalIDFRetryKeepsRankingAndCurrentGuards(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	d.Body = "white CMF procedure"
	record, err := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: d})
	if err != nil {
		t.Fatal(err)
	}
	for _, q := range []string{"white CMF", `"white CMF"`} {
		req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: q, Purpose: "context", AvailableTokens: 32000}
		index, err := s.Index(ctx, req, Destination{Name: "hosted"})
		if err != nil {
			t.Fatal(err)
		}
		// With no hyphen query, all term facts and packed fields are identical to
		// the old compiler. Seal the old version to exercise a retained receipt.
		historical := index.Package.Semantic
		historical.Ranking = legacyIDFRanking(historical.Ranking)
		encoded, seal, err := sealPackage(historical)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_receipt SET semantic_body=$2,seal=$3 WHERE receipt_id=$1`, index.Package.ReceiptID, encoded, seal); err != nil {
			t.Fatal(err)
		}
		replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: q})
		if err != nil || replay.Seal != seal {
			t.Fatalf("old replay: %v", err)
		}
		retry, err := s.Index(ctx, req, Destination{Name: "hosted"})
		if err != nil || retry.Package.Seal != seal || retry.Package.Semantic.Ranking != historical.Ranking {
			t.Fatalf("old retry: %v", err)
		}
		changed := req
		changed.Query = "different intent"
		if _, err = s.Index(ctx, changed, Destination{Name: "hosted"}); Code(err) != "IDEMPOTENCY_CONFLICT" {
			t.Fatalf("changed intent: %v", err)
		}
		if _, err = s.Revise(ctx, ReviseRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: record.Version, Repo: repo, Body: d.Body + " updated"}); err != nil {
			t.Fatal(err)
		}
		record.Version++
		if _, err = s.Index(ctx, req, Destination{Name: "hosted"}); Code(err) != "STALE_PACKAGE" {
			t.Fatalf("changed source retry: %v", err)
		}
		if replay, err = s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: q}); err != nil || replay.Seal != seal {
			t.Fatalf("old version replay after revision: %v", err)
		}
	}
}

func TestAdjacentCompoundAutomatonMatchesBoundaryOracle(t *testing.T) {
	query := "a-b b-c a-b-c a-a a-a-a source_id-v2 ä-x"
	lexicon := newRankingLexicon(query, "binary-idf-scope-recency/5")
	patterns := map[string]*regexp.Regexp{}
	for term := range lexicon.compounds {
		parts := strings.Split(term, "-")
		for i := range parts {
			parts[i] = regexp.QuoteMeta(parts[i])
		}
		patterns[term] = regexp.MustCompile(`(?:^|[^\pL\pN_-])` + strings.Join(parts, `(?:-|[\t\p{Zs}]+)`) + `(?:$|[^\pL\pN_-])`)
	}
	// Includes overlapping/suffix compounds, forbidden boundary hyphens, Unicode
	// number boundaries, and mixed separators. The oracle is independent of links.
	pieces := []string{"a", "b", "c", "source_id", "v2", "Ä", "x", "²", "-", "--", " ", "\t", "\u00a0", "\n", ",", "_"}
	rng := rand.New(rand.NewSource(7))
	bodies := []string{"a b c", "a-b c", "a b-c", "a-a-a a a a", "a-b-c a-b b-c", "source_id v2"}
	for i := 0; i < 1000; i++ {
		var b strings.Builder
		for j := 0; j < 30; j++ {
			b.WriteString(pieces[rng.Intn(len(pieces))])
		}
		bodies = append(bodies, b.String())
	}
	for _, body := range bodies {
		got := lexicon.bodyTerms(body)
		for term, pattern := range patterns {
			if want := pattern.MatchString(strings.ToLower(body)); got[term] != want {
				t.Fatalf("alias %q body %q: got %v want %v", term, body, got[term], want)
			}
		}
	}
}

func TestAdjacentCompoundDoesNotChangeSemanticFallback(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	d.Body = "white CMF adjacent"
	if _, err := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: d}); err != nil {
		t.Fatal(err)
	}
	d.Body = "white unrelated words CMF"
	distant, err := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: d})
	if err != nil {
		t.Fatal(err)
	}
	s.semanticRanker = func(context.Context, SemanticRankRequest) (SemanticRankResult, error) {
		return SemanticRankResult{}, errors.New("fixture worker unavailable")
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "white-cmf", Purpose: "context", AvailableTokens: 32000, Semantic: true}
	got, err := s.Index(ctx, req, Destination{Name: "hosted"})
	if err != nil {
		t.Fatal(err)
	}
	p := got.Package
	if p.Semantic.Ranking != "lexical-scope-recency/4" || p.Semantic.IDF != nil || p.Semantic.Discovery.State != "unavailable" || len(p.Semantic.Index) != 2 || p.Semantic.Index[0].RecordID != distant.RecordID {
		t.Fatalf("semantic fallback changed: %+v", p.Semantic)
	}
	replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID, Query: req.Query})
	if err != nil || replay.Seal != p.Seal {
		t.Fatalf("fallback replay: %v", err)
	}
}

func TestAdjacentCompoundVariantReplayAndUnknownRefusal(t *testing.T) {
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
	for _, c := range []struct {
		ranking, query, signature string
		entities                  []EntityRef
	}{
		{"binary-idf-scope-recency/6", `"panel-board"`, "", nil},
		{"binary-idf-scope-recency/7", "panel-board", strings.Repeat("a", 64), nil},
		{"binary-idf-scope-recency/8", "panel-board", "", []EntityRef{{Kind: "file", Name: "src/panel.go"}}},
	} {
		req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: c.query, ErrorSignature: c.signature, Entities: c.entities, Purpose: "context", AvailableTokens: 32000}
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
		p.Semantic.Ranking = "binary-idf-scope-recency/9"
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
