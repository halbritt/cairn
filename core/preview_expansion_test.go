package core

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

func TestPreviewExpansionRestoresShortNegationWithinOrdinaryRoom(t *testing.T) {
	body := strings.Repeat("Background context. ", 12) + "Do not under any circumstances in the production environment reuse cached validation results. More details follow."
	c := candidate{selection: Selection{Record: Record{RecordID: "qualifier", Version: 1, Class: "A", Draft: Draft{Kind: "note", Body: body}}}}
	p := SemanticPackage{Presentation: "cairn.preview-boundaries/1", Mode: "index", Purpose: "context", Schema: "cairn.semantic/17", Ranking: "binary-idf-scope-recency/2", AvailableTokens: 8000, OptionalLimit: 800, Omitted: omissionCensus()}
	got, err := packIndex(p, []candidate{c}, map[string]*CandidateEvaluation{"qualifier": {}}, "reuse cached validation results")
	if err != nil || len(got.Index) != 1 {
		t.Fatalf("index: %+v %v", got, err)
	}
	if !strings.Contains(got.Index[0].Summary, "Do not") {
		t.Fatalf("governing negation still missing with spare room: %q", got.Index[0].Summary)
	}
	old, oldSpan := indexPreview(body, "reuse cached validation results", p.Ranking)
	span := got.Index[0].SummarySpan
	if span == nil || span.Offset > oldSpan.Offset || span.Offset+span.Length < oldSpan.Offset+oldSpan.Length || !strings.Contains(got.Index[0].Summary, body[oldSpan.Offset:oldSpan.Offset+oldSpan.Length]) || len(got.Index[0].Summary) > 160 {
		t.Fatalf("lost visible source or exceeded display bound: old=%q new=%+v", old, got.Index[0])
	}
}

func TestPreviewExpansionPreservesAdmissionAndBudgetFallback(t *testing.T) {
	body := strings.Repeat("Background context. ", 12) + "Do not under any circumstances in the production environment reuse cached validation results. More details follow."
	candidates := []candidate{}
	for _, id := range []string{"first", "second"} {
		candidates = append(candidates, candidate{selection: Selection{Record: Record{RecordID: id, Version: 1, Class: "A", Draft: Draft{Kind: "note", Body: body + id}}}})
	}
	pack := func(policy string, optional, room int, explicit bool) SemanticPackage {
		t.Helper()
		p := SemanticPackage{Presentation: policy, Mode: "index", Purpose: "context", Schema: "cairn.semantic/17", Ranking: "binary-idf-scope-recency/2", AvailableTokens: room, OptionalLimit: optional, Omitted: omissionCensus()}
		if explicit {
			p.AvailableTokens = 8000
			p.MemoryBudget = &MemoryBudget{Schema: "cairn.memory-budget/1", Bytes: room}
		}
		got, err := packIndex(p, candidates, map[string]*CandidateEvaluation{"first": {}, "second": {}}, "reuse cached validation results")
		if err != nil {
			t.Fatal(err)
		}
		return got
	}
	baseline := pack("", 1000, 8000, false)
	if len(baseline.Index) != 2 {
		t.Fatal("fixture must admit both candidates")
	}
	cost := 0
	for _, entry := range baseline.Index {
		encoded, _ := json.Marshal(entry)
		cost += len(encoded) + 160
	}
	for _, explicit := range []bool{false, true} {
		ordinary := pack(previewBoundariesV1, 1000, 8000, explicit)
		for i, entry := range ordinary.Index {
			old := baseline.Index[i]
			if entry.RecordID != old.RecordID || entry.Version != old.Version || entry.BodySHA256 != old.BodySHA256 || !strings.Contains(entry.Summary, "Do not") || entry.SummarySpan.Offset > old.SummarySpan.Offset || entry.SummarySpan.Offset+entry.SummarySpan.Length < old.SummarySpan.Offset+old.SummarySpan.Length {
				t.Fatal("lost admission, provenance or existing visible text")
			}
		}
		if len(ordinary.Index) != len(baseline.Index) || !reflect.DeepEqual(ordinary.Omitted, baseline.Omitted) {
			t.Fatal("presentation changed admission")
		}
		saturated := pack(previewBoundariesV1, cost, 8000, explicit)
		if !reflect.DeepEqual(saturated.Index, baseline.Index) {
			t.Fatal("expansion spent optional room reserved by a later candidate")
		}
		unexpanded := pack("", 1000, 8000, explicit)
		envelope, err := indexMemoryCost(unexpanded)
		if err != nil {
			t.Fatal(err)
		}
		// Recompute at this room: the number's encoded width affects the envelope.
		unexpanded = pack("", 1000, envelope, explicit)
		envelope, _ = indexMemoryCost(unexpanded)
		atTotalLimit := pack(previewBoundariesV1, 1000, envelope, explicit)
		if !reflect.DeepEqual(atTotalLimit.Index, baseline.Index) {
			t.Fatal("expansion exceeded the exact total envelope budget")
		}
	}
}

func TestPreviewExpansionSaturated445IsUnchanged(t *testing.T) {
	body := strings.Repeat("Background context. ", 12) + "Do not under any circumstances in the production environment reuse cached validation results. More details follow."
	c := candidate{selection: Selection{Record: Record{RecordID: "qualifier", Version: 1, Class: "A", Draft: Draft{Kind: "note", Body: body}}}}
	p := SemanticPackage{Mode: "index", Purpose: "context", Schema: "cairn.semantic/17", Ranking: "binary-idf-scope-recency/2", AvailableTokens: 8000, OptionalLimit: 445, Omitted: omissionCensus()}
	old, err := packIndex(p, []candidate{c}, map[string]*CandidateEvaluation{"qualifier": {}}, "reuse cached validation results")
	if err != nil || len(old.Index) != 1 {
		t.Fatalf("legacy saturated fixture: %v", err)
	}
	p.Presentation = previewBoundariesV1
	got, err := packIndex(p, []candidate{c}, map[string]*CandidateEvaluation{"qualifier": {}}, "reuse cached validation results")
	if err != nil || !reflect.DeepEqual(got.Index, old.Index) || !reflect.DeepEqual(got.Omitted, old.Omitted) {
		t.Fatalf("saturated fallback changed: %+v %v", got, err)
	}
}

func TestPreviewExpansionBoundariesAndSemanticLocator(t *testing.T) {
	for _, tc := range []struct {
		name, sentence, visible string
		expand                  bool
	}{
		{"path punctuation", "Do not run scripts/check.v2.py with cfg.retry=false against /srv/db.v3/current.", "run scripts/check.v2.py with cfg.retry=false against /srv/db.v3/current.", true},
		{"unicode", "仅限测试环境。不得reuse cached validation results。", "reuse cached validation results", true},
		{"long scope", "Do not " + strings.Repeat("unchanged historical scope ", 8) + "reuse cached validation results.", "reuse cached validation results.", false},
		{"code", "Do not execute `reuse cached validation results`.", "reuse cached validation results", false},
		{"quoted", "Do not execute \"reuse cached validation results\".", "reuse cached validation results", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body := strings.Repeat("Background. ", 20) + tc.sentence
			original := ByteSpanRequest{Offset: strings.Index(body, tc.visible), Length: len(tc.visible)}
			text, span, ok := enclosingPreview(body, original)
			if ok != tc.expand {
				t.Fatalf("expansion=%v text=%q", ok, text)
			}
			if ok && (span.Offset > original.Offset || span.Offset+span.Length < original.Offset+original.Length || !strings.Contains(text, tc.visible) || len(text) > 160) {
				t.Fatal("expansion lost visible source or exceeded display")
			}
		})
	}
	body := strings.Repeat("Background. ", 20) + "Do not reuse cached validation results."
	hit := ByteSpanRequest{Offset: strings.Index(body, "reuse"), Length: len("reuse cached validation results.")}
	c := candidate{selection: Selection{Record: Record{RecordID: "semantic", Version: 1, Class: "A", Draft: Draft{Kind: "note", Body: body}}}}
	p := SemanticPackage{Presentation: previewBoundariesV1, Mode: "index", Purpose: "context", Schema: "cairn.semantic/16", Ranking: "interleaved-scope-recency/1", AvailableTokens: 8000, OptionalLimit: 800, Omitted: omissionCensus()}
	got, err := packIndex(p, []candidate{c}, map[string]*CandidateEvaluation{"semantic": {PassageHit: &SemanticPassageHit{Span: hit}}}, "reuse")
	if err != nil || len(got.Index) != 1 || !strings.Contains(got.Index[0].Summary, "Do not") || got.Index[0].MatchSpan == nil || *got.Index[0].MatchSpan != hit {
		t.Fatalf("semantic expansion lost full scored locator: %+v %v", got, err)
	}
}
