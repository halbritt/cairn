package core

import (
	"context"
	"encoding/json"
	"fmt"
	"slices"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestSearchPagesReachLowerRankedNotesWithoutSkippingMandatoryContext(t *testing.T) {
	for _, semantic := range []bool{false, true} {
		t.Run(fmt.Sprint("semantic=", semantic), func(t *testing.T) { checkSearchPages(t, semantic) })
	}
}

func checkSearchPages(t *testing.T, semantic bool) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Kind, draft.Body, draft.Sensitivity = "instruction", "Keep source references with the answer.", "shareable"
	required, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: root.ID, Mandatory: true, PolicyKey: "sources", Reason: "Required on every search page"})
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]bool{}
	draft.Kind = "note"
	for i := 0; i < 7; i++ {
		draft.Body = fmt.Sprintf("Procedure %d: %s", i, strings.Repeat("selected reusable knowledge ", 12))
		if i == 3 {
			draft.Body = "A short procedure among longer previews."
		}
		record, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft})
		if err != nil {
			t.Fatal(err)
		}
		want[record.RecordID] = true
	}
	draft.Kind, draft.Body = "procedure", "Excluded kind must not consume a page position."
	if _, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft}); err != nil {
		t.Fatal(err)
	}
	draft.Kind = "note"
	draft.Sensitivity, draft.Body = "local", "Private procedure must not appear or consume a hosted page position."
	if _, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft}); err != nil {
		t.Fatal(err)
	}
	draft.Sensitivity = "shareable"
	draft.Pins = &Applicability{TaskPhase: "implementation"}
	draft.Body = "Procedure from a different task phase must not consume a page position."
	if _, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft}); err != nil {
		t.Fatal(err)
	}
	draft.Pins = nil
	if !semantic {
		draft.Body = "Unrelated fruit preferences."
		if _, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft}); err != nil {
			t.Fatal(err)
		}
	}
	calls := 0
	op.semanticRanker = func(_ context.Context, req SemanticRankRequest) (SemanticRankResult, error) {
		calls++
		if len(req.Notes) != len(want) {
			t.Fatalf("scorer saw a pre-truncated or ineligible set: %d", len(req.Notes))
		}
		result := SemanticRankResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "fixture/1"}
		for _, n := range req.Notes {
			if !want[n.RecordID] {
				t.Fatalf("ineligible candidate: %s", n.RecordID)
			}
			result.Scores = append(result.Scores, SemanticScore{n.RecordID, n.Version, n.BodySHA256, int(n.BodySHA256[0]) * 1000})
		}
		return result, nil
	}
	full, err := op.Index(ctx, CompileRequest{Kinds: []string{"note"}, RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", Query: "procedure", AvailableTokens: 100000, Semantic: semantic, Context: &ContextPins{TaskPhase: "validation"}}, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	var expected, actual []string
	for _, entry := range full.Package.Semantic.Index {
		expected = append(expected, entry.RecordID)
	}
	seen := map[string]bool{}
	offset, pages := 0, 0
	for {
		if pages >= 10 {
			t.Fatal("browse failed to make bounded progress")
		}
		req := CompileRequest{Kinds: []string{"note"}, RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 10000, Query: "procedure", Semantic: semantic, Context: &ContextPins{TaskPhase: "validation"}}
		if err := json.Unmarshal([]byte(fmt.Sprintf(`{"page_offset":%d}`, offset)), &req); err != nil {
			t.Fatal(err)
		}
		index, err := op.Index(ctx, req, Destination{"hosted", false})
		if err != nil {
			t.Fatal(err)
		}
		retried, err := op.Index(ctx, req, Destination{"hosted", false})
		if err != nil || retried.Package.ReceiptID != index.Package.ReceiptID || retried.Package.Seal != index.Package.Seal {
			t.Fatalf("page retry: %v", err)
		}
		different := req
		next := offset + 1
		different.PageOffset = &next
		_, err = op.Index(ctx, different, Destination{"hosted", false})
		requireCode(t, err, "IDEMPOTENCY_CONFLICT")
		p := index.Package.Semantic
		var wire struct {
			Page *BrowsePage `json:"page"`
		}
		rawPage, err := json.Marshal(p)
		if err != nil {
			t.Fatal(err)
		}
		if err = json.Unmarshal(rawPage, &wire); err != nil {
			t.Fatal(err)
		}
		page := wire.Page
		if p.Schema != "cairn.semantic/11" || page == nil || page.Offset != offset || len(p.Selected) != 1 || p.Selected[0].Record.RecordID != required.RecordID || !p.Selected[0].Mandatory {
			t.Fatalf("page lost its offset or required instruction: %+v", p)
		}
		if len(p.Index) == 0 || p.AvailableTokens != 10000 || p.OptionalLimit != 1000 {
			t.Fatalf("page changed its budget or made no progress: %+v", p)
		}
		for _, entry := range p.Index {
			if !want[entry.RecordID] || seen[entry.RecordID] {
				t.Fatalf("unexpected or repeated optional record: %s", entry.RecordID)
			}
			seen[entry.RecordID] = true
			actual = append(actual, entry.RecordID)
		}
		beforeReplay := calls
		replay, err := op.Recompile(ctx, RecompileRequest{index.Package.ReceiptID, "procedure"})
		if err != nil || replay.Seal != index.Package.Seal || calls != beforeReplay {
			t.Fatalf("page replay: %v", err)
		}
		pulled, err := op.Expand(ctx, ExpandRequest{uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle, nil}, Destination{"hosted", false})
		if err != nil || !want[pulled.Selection.Record.RecordID] {
			t.Fatalf("page pull: %+v %v", pulled, err)
		}
		pages++
		if page.NextOffset == nil {
			break
		}
		if *page.NextOffset <= offset {
			t.Fatalf("non-advancing page: %+v", page)
		}
		offset = *page.NextOffset
	}
	if !slices.Equal(expected, actual) {
		t.Fatalf("paging changed full ranking: %v != %v", actual, expected)
	}
	if len(seen) != len(want) || pages < 2 {
		t.Fatalf("older notes unreachable: pages=%d reached=%d want=%d", pages, len(seen), len(want))
	}
}

func TestSearchPageRejectsInvalidIntentBeforeStoreAccess(t *testing.T) {
	for _, raw := range []string{
		`{"mode":"index","purpose":"context","query":"q","page_offset":-1}`,
		`{"mode":"index","purpose":"context","query":"q","page_offset":10001}`,
		`{"mode":"index","purpose":"context","query":" ","page_offset":0}`,
		`{"mode":"index","purpose":"consequential","query":"q","page_offset":0}`,
		`{"mode":"index","purpose":"context","query":"q","page_offset":0,"browse_offset":0}`,
		`{"purpose":"context","query":"q","page_offset":0}`,
	} {
		var req CompileRequest
		if err := json.Unmarshal([]byte(raw), &req); err != nil {
			t.Fatal(err)
		}
		_, err := (*Store)(nil).Compile(context.Background(), req, Destination{})
		requireCode(t, err, "INVALID_REQUEST")
	}
}
