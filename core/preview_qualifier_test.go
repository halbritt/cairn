package core

import (
	"encoding/json"
	"strings"
	"testing"
	"unicode/utf8"
)

// These fixtures characterize current excerpts, not successful qualifier repair.
func TestCurrentPreviewQualifierLimits(t *testing.T) {
	prefix := strings.Repeat("Background context. ", 12)
	cases := []struct {
		name, body, query, qualifier string
		visible                      bool
	}{
		{"short sentence leading negation", prefix + "Do not under any circumstances in the production environment reuse cached validation results. More details follow.", "reuse cached validation results", "Do not", false},
		{"long sentence trailing condition", prefix + "Reuse cached validation results " + strings.Repeat("for the retained historical report ", 6) + "only if the source revision is unchanged.", "reuse cached validation results", "only if", false},
		{"previous sentence applicability", prefix + "Only for disposable test databases. Reuse cached validation results for database migration checks.", "reuse cached validation results database migration checks", "Only for disposable", true},
		{"code path punctuation", prefix + "Do not under any circumstances in the production environment run scripts/check.v2.py with cfg.retry=false against /srv/db.v3/current.", `"scripts/check.v2.py"`, "Do not", false},
		{"unicode qualifier", prefix + "仅限测试环境。" + strings.Repeat("保留历史记录", 10) + "reuse cached validation results。", "reuse cached validation results", "仅限测试环境", false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			summary, span := indexPreview(c.body, c.query, "binary-idf-scope-recency/2")
			source := c.body[span.Offset : span.Offset+span.Length]
			if !utf8.ValidString(source) || !strings.Contains(summary, source) || len(summary) > 160 {
				t.Fatal("invalid preview provenance or bound")
			}
			if strings.Contains(source, c.qualifier) != c.visible {
				t.Fatal("current characterization changed; reassess the documented limit")
			}
			t.Logf("qualifier_visible=%v span=%+v summary=%q", strings.Contains(source, c.qualifier), span, summary)
		})
	}
}

// A full short sentence can exceed the actual cost of the existing excerpt,
// even though both text windows fit the nominal 160-byte display ceiling.
func TestCurrentPreviewQualifierSaturatedBudget(t *testing.T) {
	prefix := strings.Repeat("Background context. ", 12)
	sentence := "Do not under any circumstances in the production environment reuse cached validation results."
	body := prefix + sentence + " More details follow."
	c := candidate{selection: Selection{Record: Record{RecordID: "qualifier", Version: 1, Class: "A", Draft: Draft{Kind: "note", Body: body}}}}
	pack := func(limit int) (SemanticPackage, map[string]*CandidateEvaluation) {
		t.Helper()
		es := map[string]*CandidateEvaluation{"qualifier": {}}
		p, err := packIndex(SemanticPackage{Schema: "cairn.semantic/17", Ranking: "binary-idf-scope-recency/2", AvailableTokens: 8000, OptionalLimit: limit, Omitted: omissionCensus()}, []candidate{c}, es, "reuse cached validation results")
		if err != nil {
			t.Fatal(err)
		}
		return p, es
	}
	_, es := pack(800)
	limit := es["qualifier"].Cost
	p, _ := pack(limit)
	if len(p.Index) != 1 {
		t.Fatal("fixture did not saturate with its candidate admitted")
	}
	old := p.Index[0]
	fixed := old
	fixed.Summary = "..." + sentence + "..."
	fixed.SummarySpan = &ByteSpanRequest{Offset: len(prefix), Length: len(sentence)}
	before, err := json.Marshal(old)
	if err != nil {
		t.Fatal(err)
	}
	after, err := json.Marshal(fixed)
	if err != nil {
		t.Fatal(err)
	}
	delta := len(after) - len(before)
	if delta <= 0 || strings.Contains(old.Summary, "Do not") {
		t.Fatal("fixture no longer demonstrates the saturated-budget conflict")
	}
	t.Logf("optional=%d; existing source=%d; full sentence=%d; exact JSON repair delta=%d; new total=%d", limit, old.SummarySpan.Length, len(sentence), delta, limit+delta)
	// Even the smallest contiguous interval containing "not" and the complete
	// matched phrase exceeds this entry's actual cost; sentence snapping alone
	// is not the reason the full-sentence alternative fails.
	start := strings.Index(body, "not")
	end := strings.Index(body, "reuse cached validation results") + len("reuse cached validation results")
	fixed.Summary = "..." + body[start:end] + "..."
	fixed.SummarySpan = &ByteSpanRequest{Offset: start, Length: end - start}
	minimum, err := json.Marshal(fixed)
	if err != nil {
		t.Fatal(err)
	}
	if len(minimum) <= len(before) {
		t.Fatal("minimum qualifier-and-match interval unexpectedly fits")
	}
	t.Logf("minimum qualifier-and-match source=%d; JSON increase=%d", end-start, len(minimum)-len(before))

}

func TestCurrentSemanticPreviewQualifierLimit(t *testing.T) {
	prefix := strings.Repeat("Background. ", 20)
	passage := "Reuse cached validation results " + strings.Repeat("for the retained historical report ", 6) + "only if the source revision is unchanged."
	full := ByteSpanRequest{Offset: len(prefix), Length: len(passage)}
	summary, span := passagePreview(prefix+passage, full)
	if strings.Contains(summary, "only if") || span.Length != 154 {
		t.Fatal("current semantic characterization changed")
	}
	if full.Length <= span.Length || !strings.Contains(passage, "only if") {
		t.Fatal("fixture lacks out-of-window qualification")
	}
	t.Logf("scored passage=%d; displayed source=%d; qualifier omitted; full source remains addressable", full.Length, span.Length)
}
