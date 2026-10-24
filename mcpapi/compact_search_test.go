package mcpapi

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestCompactSearchKeepsActionableContextAndOldView(t *testing.T) {
	receipt, record, handle := uuid.NewString(), uuid.NewString(), uuid.NewString()
	entry := core.IndexEntry{RecordID: record, Version: 3, Class: "A", Kind: "lesson", Summary: `quoted "<>& 日本語 source`, BodySHA256: strings.Repeat("a", 64), SummarySpan: &core.ByteSpanRequest{Offset: 9, Length: 12}}
	selection := core.Selection{Mandatory: true, Reason: "required", Record: core.Record{RecordID: uuid.NewString(), Version: 1, Draft: core.Draft{Body: "Whole required source."}}}
	competing := core.Selection{Mandatory: true, Reason: "competing", Record: core.Record{RecordID: uuid.NewString(), Version: 4, Draft: core.Draft{Body: "Whole competing 日本語 source."}}}
	group := core.AdvisoryConflict{ID: uuid.NewString(), Version: 1, Members: []core.RecordVersionRef{
		{RecordID: selection.Record.RecordID, Version: selection.Record.Version},
		{RecordID: competing.Record.RecordID, Version: competing.Record.Version},
	}}
	selection.Conflicts, competing.Conflicts = []core.AdvisoryConflict{group}, []core.AdvisoryConflict{group}
	result := core.IndexResult{
		Package: core.Package{ReceiptID: receipt, Seal: "seal", Semantic: core.SemanticPackage{
			Schema: "cairn.semantic/19", Status: "READY", Scope: core.Scope{Repo: "repo", TaskID: "task", RunID: "run"},
			Destination: core.Destination{Name: "hosted"}, Query: "sha256:query", Ranking: "rank/1", Tokenizer: "utf8-byte-upper-bound/1",
			AdvisoryConflicts: true, Kinds: []string{"lesson"}, Context: &core.ContextPins{TaskPhase: "review"},
			Selected: []core.Selection{selection, competing}, Index: []core.IndexEntry{entry},
			Omitted:      map[string]int{"OPTIONAL_BUDGET": 0, "NO_LEXICAL_MATCH": 2, "FUTURE_REASON": 0},
			MemoryBudget: &core.MemoryBudget{Schema: "cairn.memory-budget/3", Bytes: 6000},
			Page:         &core.BrowsePage{Offset: 0, NextOffset: new(int)},
		}},
		Handles:          []core.IndexHandle{{RecordID: record, Version: 3, Handle: handle}},
		CreditsRemaining: 2, BytesRemaining: 3000,
	}
	args := searchArgs{RequestID: uuid.NewString()}
	full, room, err := searchView(result, args, "", false, 9000, 9000)
	if err != nil || room != 9000 {
		t.Fatalf("full view: %v room=%d", err, room)
	}
	legacy, err := presentSearch(result, args.RequestID)
	fullView := full.(searchResult)
	if err != nil {
		t.Fatal(err)
	}
	legacy.Index[0].PullArguments.RequestID = fullView.Index[0].PullArguments.RequestID
	if !reflect.DeepEqual(fullView, legacy) {
		t.Fatal("default search projection changed")
	}
	compact, room, err := searchView(result, args, "compact/1", false, 9000, 9000)
	if err != nil || room != 9000 {
		t.Fatalf("compact view: %v room=%d", err, room)
	}
	encoded, err := json.Marshal(compact)
	if err != nil {
		t.Fatal(err)
	}
	var got compactSearchResult
	if err = json.Unmarshal(encoded, &got); err != nil {
		t.Fatal(err)
	}
	if got.Schema != "cairn.mcp-search-compact/1" || got.Status != "READY" ||
		!reflect.DeepEqual(got.Selected, result.Package.Semantic.Selected) ||
		len(got.Selected) != 2 || got.Selected[1].Record.Body != "Whole competing 日本語 source." ||
		!reflect.DeepEqual(got.Omitted, result.Package.Semantic.Omitted) ||
		!got.AdvisoryConflicts || !reflect.DeepEqual(got.Kinds, []string{"lesson"}) ||
		got.Context == nil || got.Context.TaskPhase != "review" ||
		got.Page == nil || got.Page.NextOffset == nil ||
		got.Index[0].PullArguments.ReceiptID != receipt || got.Index[0].PullArguments.Handle != handle ||
		got.Index[0].SummarySpan.Offset != 9 || got.SourceSeal != "seal" {
		t.Fatalf("compact view lost context: %+v", got)
	}
	if _, present := got.Omitted["TOTAL_BUDGET"]; present {
		t.Fatal("absent omission reason became a known zero")
	}
	if _, err := uuid.Parse(got.Index[0].PullArguments.RequestID); err != nil {
		t.Fatalf("unusable pull request: %v", err)
	}
	fullJSON, err := json.Marshal(full)
	if err != nil {
		t.Fatal(err)
	}
	if len(encoded) >= len(fullJSON) || strings.Contains(string(encoded), `"ranking"`) {
		t.Fatalf("compact projection did not save framing: compact=%d full=%d", len(encoded), len(fullJSON))
	}
	t.Logf("search projection bytes: full=%d compact=%d", len(fullJSON), len(encoded))
	tool, wire, err := encodeToolResult(compact, 9000)
	if err != nil || tool == nil {
		t.Fatal(err)
	}
	if exact, exactWire, exactErr := encodeToolResult(compact, len(wire)); exactErr != nil || exact == nil || !reflect.DeepEqual(exactWire, wire) {
		t.Fatalf("escaped compact envelope failed at its exact serialized budget: %v", exactErr)
	}
	if _, _, err = encodeToolResult(compact, len(wire)-1); err == nil || !strings.Contains(err.Error(), "BUDGET_REFUSED") {
		t.Fatalf("final escaped/multibyte envelope ignored room: %v", err)
	}
	if result.Package.Semantic.Query != "sha256:query" || result.Package.Semantic.Ranking != "rank/1" {
		t.Fatal("projection mutated sealed source")
	}
}

func TestCompactSearchViewIsExplicitInClientInfo(t *testing.T) {
	server, err := NewServer(nil, Config{Scope: core.Scope{Repo: "repo", TaskID: "task", RunID: "run"}, AvailableTokens: 32000, SearchView: "compact/1"})
	if err != nil {
		t.Fatal(err)
	}
	result, err := connect(t, context.Background(), server).CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_client_info", Arguments: map[string]any{}})
	if err != nil || result.IsError {
		t.Fatalf("client info: %+v %v", result, err)
	}
	var info clientInfoResult
	if err = json.Unmarshal([]byte(result.Content[0].(*mcp.TextContent).Text), &info); err != nil {
		t.Fatal(err)
	}
	if info.Facade.SearchView != "compact/1" || info.API.SearchView != "" {
		t.Fatalf("presentation declaration: %+v", info)
	}
}

type compactSearchBackend struct{ index core.IndexResult }

func (b compactSearchBackend) Search(_ context.Context, _ core.CompileRequest) (core.IndexResult, error) {
	return b.index, nil
}
func (b compactSearchBackend) Pull(_ context.Context, _ core.ExpandRequest) (core.Expansion, error) {
	return core.Expansion{}, nil
}

func TestOptInReadOnlySearchReturnsCompactView(t *testing.T) {
	record := uuid.NewString()
	backend := compactSearchBackend{index: core.IndexResult{
		Package: core.Package{ReceiptID: uuid.NewString(), Semantic: core.SemanticPackage{
			Schema: "cairn.semantic/19", Status: "READY",
			Index:    []core.IndexEntry{{RecordID: record, Version: 1, Summary: "quoted 日本語"}},
			Selected: []core.Selection{}, Omitted: map[string]int{"OPTIONAL_BUDGET": 0},
		}},
		Handles: []core.IndexHandle{{RecordID: record, Version: 1, Handle: uuid.NewString()}},
	}}
	server, err := NewReadOnlyServer(backend, Config{Scope: core.Scope{Repo: "repo", TaskID: "task", RunID: "run"}, AvailableTokens: 32000, SearchView: "compact/1"})
	if err != nil {
		t.Fatal(err)
	}
	result, err := connect(t, context.Background(), server).CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_search", Arguments: map[string]any{"query": "quoted"}})
	if err != nil || result.IsError {
		t.Fatalf("compact search: %+v %v", result, err)
	}
	var got compactSearchResult
	if err = json.Unmarshal([]byte(result.Content[0].(*mcp.TextContent).Text), &got); err != nil {
		t.Fatal(err)
	}
	if got.Schema != "cairn.mcp-search-compact/1" || len(got.Index) != 1 ||
		got.Index[0].PullArguments.Handle != backend.index.Handles[0].Handle || got.Omitted["OPTIONAL_BUDGET"] != 0 {
		t.Fatalf("live compact view: %+v", got)
	}
}

func TestOptInAttemptSearchPublishesExactCompactEnvelope(t *testing.T) {
	var published string
	backend := &publicationBackend{search: func(_ context.Context, _ core.CompileRequest) (core.IndexResult, SearchPublication, error) {
		return publicationIndex("compact"), &observedPublication{
			prepared: func(_ context.Context, wire string) error { published = wire; return nil },
			cancel:   func(SearchPublicationFailure) error { t.Fatal("successful compact search cancelled"); return nil },
		}, nil
	}}
	config := publicationConfig()
	config.SearchView = "compact/1"
	server, err := NewAttemptReadOnlyServer(backend, config)
	if err != nil {
		t.Fatal(err)
	}
	result, err := connect(t, context.Background(), server).CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_search", Arguments: map[string]any{"query": "source"}})
	if err != nil || result.IsError {
		t.Fatalf("attempt search: %+v %v", result, err)
	}
	wire, err := json.Marshal(result)
	if err != nil || published != string(wire) {
		t.Fatal("attempt publication was not the exact compact result")
	}
	var view compactSearchResult
	if err = json.Unmarshal([]byte(result.Content[0].(*mcp.TextContent).Text), &view); err != nil {
		t.Fatal(err)
	}
	if view.SourceSeal != "seal-compact" || view.Index[0].PullArguments.Handle != "handle-compact" {
		t.Fatalf("attempt correlation lost: %+v", view)
	}
}
