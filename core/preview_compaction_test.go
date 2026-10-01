package core

import (
	"bytes"
	"context"
	"encoding/json"
	"github.com/fxamacker/cbor/v2"
	"reflect"
	"strings"
	"testing"
)

const compactPolicy = "cairn.preview-compact/1"

func metadataCandidates(kind string) []candidate {
	body := "Handoff: Cairn retrieval usefulness. " + strings.Repeat("Synthetic context covering several unrelated details. ", 90)
	return []candidate{
		{literal: true, score: 9, selection: Selection{Record: Record{RecordID: "10000000-0000-4000-8000-000000000001", Version: 160, Class: "A", Draft: Draft{Entities: []EntityRef{{Kind: "file", Name: "core/indexed_retrieval.go"}, {Kind: "file", Name: "semantic/index.go"}}, Kind: kind, Body: body[:4237]}}}},
		{score: 2, selection: Selection{Record: Record{RecordID: "20000000-0000-4000-8000-000000000002", Version: 17, Class: "A", Draft: Draft{Kind: kind, Body: "CAPLAB context. " + strings.Repeat("Synthetic lower ranked details. ", 160)}}}},
	}
}

func packMetadata(t *testing.T, candidates []candidate, policy string, optional, room int, paged bool) (SemanticPackage, map[string]*CandidateEvaluation) {
	t.Helper()
	p := SemanticPackage{Presentation: policy, Mode: "index", Purpose: "context", Schema: "cairn.semantic/17", Ranking: "binary-idf-scope-recency/2", AvailableTokens: room, OptionalLimit: optional, Omitted: omissionCensus()}
	if paged {
		p.Page = &BrowsePage{Offset: 0}
	}
	evaluations := map[string]*CandidateEvaluation{}
	for _, c := range candidates {
		for _, m := range unitMembers(c) {
			evaluations[m.selection.Record.RecordID] = &CandidateEvaluation{}
		}
	}
	got, err := packIndex(p, candidates, evaluations, `"Handoff: Cairn retrieval usefulness"`)
	if err != nil {
		t.Fatal(err)
	}
	return got, evaluations
}

func TestPreviewCompactionAdmitsBestWithoutChangingSourceHints(t *testing.T) {
	for _, kind := range []string{"note", "decision"} {
		for _, class := range []string{"A", "B"} {
			for _, paged := range []bool{false, true} {
				candidates := metadataCandidates(kind)
				candidates[0].selection.Record.Class = class
				originalRefs := append([]EntityRef(nil), candidates[0].selection.Record.Entities...)
				got, e := packMetadata(t, candidates, compactPolicy, 650, 6500, paged)
				if len(got.Index) != 1 || got.Index[0].RecordID != candidates[0].selection.Record.RecordID {
					t.Fatalf("best source still displaced: %+v", got.Index)
				}
				entry := got.Index[0]
				encoded, _ := json.Marshal(entry)
				var public map[string]any
				json.Unmarshal(encoded, &public)
				if public["entities_omitted"] != float64(2) || len(entry.Entities) != 0 {
					t.Fatalf("compaction not explicit: %s", encoded)
				}
				if len(encoded)+160 > 650 || e[entry.RecordID].Cost != len(encoded)+160 {
					t.Fatal("marker not charged")
				}
				expected := indexEntry(candidates[0].selection.Record)
				text, span := indexPreview(candidates[0].selection.Record.Body, `"Handoff: Cairn retrieval usefulness"`, got.Ranking)
				expected.Summary, expected.SummarySpan = text, &span
				// Compare all source fields, ignoring only the two explicitly changed metadata fields.
				actualFields := map[string]any{}
				expectedFields := map[string]any{}
				original, _ := json.Marshal(expected)
				json.Unmarshal(encoded, &actualFields)
				json.Unmarshal(original, &expectedFields)
				delete(actualFields, "entities_omitted")
				delete(expectedFields, "entities")
				if !reflect.DeepEqual(actualFields, expectedFields) || !reflect.DeepEqual(candidates[0].selection.Record.Entities, originalRefs) {
					t.Fatal("source fields or stored association changed")
				}
				if cost, err := indexMemoryCost(got); err != nil || cost > 6500 {
					t.Fatalf("total cap %d %v", cost, err)
				}
			}
		}
	}
}

func TestPreviewCompactionPreservesOldPolicyAndUnaffectedPaths(t *testing.T) {
	for _, policy := range []string{"", previewBoundariesV1} {
		got, e := packMetadata(t, metadataCandidates("note"), policy, 650, 6500, false)
		if len(got.Index) != 1 || got.Index[0].RecordID != "20000000-0000-4000-8000-000000000002" || e["10000000-0000-4000-8000-000000000001"].Reason != "OPTIONAL_BUDGET" {
			t.Fatal("historical admission changed")
		}
		for _, entry := range got.Index {
			if entry.EntitiesOmitted != 0 {
				t.Fatal("old policy emits compaction")
			}
		}
	}
	for _, mode := range []string{"ample", "saturated", "mandatory", "instruction", "conflict", "group"} {
		candidates := metadataCandidates("note")
		optional, room := 650, 6500
		switch mode {
		case "ample":
			optional = 2000
			room = 16000
		case "saturated":
			optional = 540
		case "mandatory":
			candidates[0].selection.Mandatory = true
			room = 16000
		case "instruction":
			candidates[0].selection.Record.Class = "C"
		case "conflict":
			candidates[0].selection.Conflicts = []AdvisoryConflict{{ID: "conflict", Version: 1, Members: []RecordVersionRef{{candidates[0].selection.Record.RecordID, 160}, {candidates[1].selection.Record.RecordID, 17}}}}
		case "group":
			candidates[0].group = append([]candidate(nil), candidates...)
			candidates = candidates[:1]
		}
		old, _ := packMetadata(t, candidates, previewBoundariesV1, optional, room, false)
		got, _ := packMetadata(t, candidates, compactPolicy, optional, room, false)
		if !reflect.DeepEqual(old.Index, got.Index) || !reflect.DeepEqual(old.Selected, got.Selected) || !reflect.DeepEqual(old.Omitted, got.Omitted) {
			t.Fatalf("unrelated %s behavior changed", mode)
		}
	}
}

func TestPreviewCompactionKeepsSemanticPassageAndChargesReserve(t *testing.T) {
	c := metadataCandidates("note")[0]
	c.literal = false
	// Deliberately include JSON escaping and multibyte text; locators are byte offsets.
	c.selection.Record.Body = strings.Repeat("Background ", 30) + strings.Repeat("quoted \\\"é\\\" memory ", 40)
	span := ByteSpanRequest{Offset: 330, Length: 400}
	p := SemanticPackage{Presentation: compactPolicy, Mode: "index", Purpose: "context", Schema: "cairn.semantic/16", Ranking: "interleaved-scope-recency/1", AvailableTokens: 6500, OptionalLimit: 650, Omitted: omissionCensus(), MemoryBudget: &MemoryBudget{Schema: "cairn.memory-budget/2", Bytes: 4500, MinPullBytes: 2000}}
	e := map[string]*CandidateEvaluation{c.selection.Record.RecordID: {PassageHit: &SemanticPassageHit{Span: span}}}
	got, err := packIndex(p, []candidate{c}, e, "memory")
	if err != nil {
		t.Fatal(err)
	}
	if len(got.Index) != 1 || got.Index[0].EntitiesOmitted != 2 {
		t.Fatalf("semantic preview lost: %+v omitted=%+v evaluation=%+v", got.Index, got.Omitted, e[c.selection.Record.RecordID])
	}
	text, summarySpan := passagePreview(c.selection.Record.Body, span)
	entry := got.Index[0]
	if entry.Summary != text || !reflect.DeepEqual(entry.SummarySpan, &summarySpan) || !reflect.DeepEqual(entry.MatchSpan, &span) {
		t.Fatal("semantic source hints changed")
	}
	cost, err := indexMemoryCost(got)
	if err != nil || cost > 2500 || memoryRoom(got)-cost < 2000 {
		t.Fatalf("escaped envelope/reserve not preserved: %d %v", cost, err)
	}
}

func TestPreviewCompactionRejectsInvalidMarkerContract(t *testing.T) {
	valid := SemanticPackage{Presentation: compactPolicy, Mode: "index", Purpose: "context", Index: []IndexEntry{{Class: "A", EntitiesOmitted: 2}}}
	if err := validatePresentation(valid); err != nil {
		t.Fatal(err)
	}
	for _, kind := range []string{"old", "absent", "future", "negative", "excess", "entities", "instruction", "conflict"} {
		p := valid
		p.Index = append([]IndexEntry(nil), valid.Index...)
		switch kind {
		case "old":
			p.Presentation = previewBoundariesV1
		case "absent":
			p.Presentation = ""
		case "future":
			p.Presentation = "cairn.preview-compact/future"
		case "negative":
			p.Index[0].EntitiesOmitted = -1
		case "excess":
			p.Index[0].EntitiesOmitted = 17
		case "entities":
			p.Index[0].Entities = []EntityRef{{Kind: "file", Name: "a.go"}}
		case "instruction":
			p.Index[0].Class = "C"
		case "conflict":
			p.Index[0].Conflicts = []AdvisoryConflict{{ID: "conflict"}}
		}
		requireCode(t, validatePresentation(p), "INTEGRITY_FAILURE")
	}
}

// Model the old typed reader by removing only the newly added IndexEntry field.
// Both old policies must re-encode byte-for-byte; compact fields cannot disappear unnoticed.
func TestPreviewCompactionPreservesOldCanonicalBytes(t *testing.T) {
	entryFields := []reflect.StructField{}
	entryType := reflect.TypeOf(IndexEntry{})
	for i := range entryType.NumField() {
		f := entryType.Field(i)
		if f.Name != "EntitiesOmitted" {
			entryFields = append(entryFields, f)
		}
	}
	oldEntry := reflect.StructOf(entryFields)
	fields := []reflect.StructField{}
	typ := reflect.TypeOf(SemanticPackage{})
	for i := range typ.NumField() {
		f := typ.Field(i)
		if f.Name == "Index" {
			f.Type = reflect.SliceOf(oldEntry)
		}
		fields = append(fields, f)
	}
	oldPackage := reflect.StructOf(fields)
	options := cbor.CanonicalEncOptions()
	options.Time = cbor.TimeRFC3339Nano
	encoder, err := options.EncMode()
	if err != nil {
		t.Fatal(err)
	}
	for _, policy := range []string{"", previewBoundariesV1, compactPolicy} {
		p, _ := packMetadata(t, metadataCandidates("note"), policy, 650, 6500, false)
		original, _, err := sealPackage(p)
		if err != nil {
			t.Fatal(err)
		}
		old := reflect.New(oldPackage)
		if err = cbor.Unmarshal(original, old.Interface()); err != nil {
			t.Fatal(err)
		}
		roundtrip, err := encoder.Marshal(old.Elem().Interface())
		if err != nil || bytes.Equal(original, roundtrip) != (policy != compactPolicy) {
			t.Fatalf("old-reader seal boundary %s: %v", policy, err)
		}
	}
}

func TestPreviewCompactionDefersEntryThatFitsOnlyAnEmptyPage(t *testing.T) {
	candidates := metadataCandidates("note")
	candidates[1].selection.Record.Entities = candidates[0].selection.Record.Entities
	candidates[0].selection.Record.Entities = nil
	page := func(offset int) SemanticPackage {
		t.Helper()
		p := SemanticPackage{Presentation: compactPolicy, Mode: "index", Purpose: "context", Schema: "cairn.semantic/17", Ranking: "binary-idf-scope-recency/2", AvailableTokens: 6500, OptionalLimit: 650, Omitted: omissionCensus(), Page: &BrowsePage{Offset: offset}}
		e := map[string]*CandidateEvaluation{candidates[0].selection.Record.RecordID: {}, candidates[1].selection.Record.RecordID: {}}
		got, err := packIndex(p, candidates, e, `"Handoff: Cairn retrieval usefulness"`)
		if err != nil {
			t.Fatal(err)
		}
		return got
	}
	first := page(0)
	if len(first.Index) != 1 || first.Index[0].RecordID != candidates[0].selection.Record.RecordID || first.Page.NextOffset == nil || *first.Page.NextOffset != 1 {
		t.Fatalf("representable next source silently lost: %+v", first.Page)
	}
	second := page(*first.Page.NextOffset)
	if len(second.Index) != 1 || second.Index[0].RecordID != candidates[1].selection.Record.RecordID || second.Index[0].EntitiesOmitted != 2 || second.Page.NextOffset != nil {
		t.Fatalf("next page did not admit compact source: %+v", second)
	}
}

func TestPreviewOptInRequestBoundary(t *testing.T) {
	legacy, err := json.Marshal(CompileRequest{})
	if err != nil || bytes.Contains(legacy, []byte("compact_preview_entities")) {
		t.Fatalf("changed absent request bytes: %s %v", legacy, err)
	}
	var explicitFalse CompileRequest
	if err = json.Unmarshal([]byte(`{"compact_preview_entities":false}`), &explicitFalse); err != nil {
		t.Fatal(err)
	}
	again, _ := json.Marshal(explicitFalse)
	if !bytes.Equal(legacy, again) {
		t.Fatal("false differs from absence")
	}
	for _, req := range []CompileRequest{{CompactPreviewEntities: true}, {CompactPreviewEntities: true, Mode: "index", Purpose: "planning"}} {
		_, err = (*Store)(nil).Compile(context.Background(), req, Destination{})
		requireCode(t, err, "INVALID_REQUEST")
	}
}
