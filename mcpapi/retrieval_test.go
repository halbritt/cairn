package mcpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestReadOnlyServerOffersOnlyExistingRetrievalContracts(t *testing.T) {
	config := Config{Scope: core.Scope{Repo: "repo", TaskID: "task", RunID: "run"}, AvailableTokens: 32000}
	ordinary, err := NewServer(nil, config)
	if err != nil {
		t.Fatal(err)
	}
	selected, err := NewReadOnlyServer(localRetrievalBackend{previewChoice: new(localapi.PreviewCapabilityChoice)}, config)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	all, err := connect(t, ctx, ordinary).ListTools(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	session := connect(t, ctx, selected)
	listed, err := session.ListTools(ctx, nil)
	if err != nil || len(listed.Tools) != 2 {
		t.Fatalf("selected tools: %+v %v", listed, err)
	}
	want := map[string]json.RawMessage{}
	for _, tool := range all.Tools {
		if tool.Name == "cairn_search" || tool.Name == "cairn_pull" {
			want[tool.Name], err = json.Marshal(tool)
			if err != nil {
				t.Fatal(err)
			}
		}
	}
	for _, tool := range listed.Tools {
		got, err := json.Marshal(tool)
		if err != nil || !reflect.DeepEqual([]byte(want[tool.Name]), got) {
			t.Fatalf("retrieval declaration changed or extra tool exposed: %s", tool.Name)
		}
		delete(want, tool.Name)
	}
	if len(want) != 0 {
		t.Fatal("missing retrieval tool")
	}
	for _, name := range []string{"cairn_remember", "cairn_prepare_note", "cairn_history", "cairn_pull_evidence", "cairn_client_info"} {
		result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: name, Arguments: map[string]any{}})
		if err == nil && !result.IsError {
			t.Fatalf("unregistered tool became callable: %s", name)
		}
	}
}

func TestReadOnlyServerRequiresAnActualBackend(t *testing.T) {
	config := Config{Scope: core.Scope{Repo: "repo", TaskID: "task", RunID: "run"}, AvailableTokens: 32000}
	var absent *localRetrievalBackend
	for _, backend := range []RetrievalBackend{nil, absent} {
		server, err := NewReadOnlyServer(backend, config)
		if err == nil || server != nil {
			t.Fatal("missing backend created a callable facade")
		}
	}
}

func TestReadOnlySearchAndPullPreserveScopeRequestsAndSourceView(t *testing.T) {
	const requestID = "b2ae2453-2a52-4109-87db-e3856cab33ed"
	ctx := context.Background()
	fixed := core.ContextPins{Revision: "fixed-revision"}
	config := Config{Scope: core.Scope{Repo: "repo", TaskID: "task", RunID: "run"}, Context: &fixed, AvailableTokens: 9500}
	var searches, pulls, versions atomic.Int32
	var wantPull core.ExpandRequest
	expansion := core.Expansion{CreditsRemaining: 3, BytesRemaining: 2048}
	expansion.Selection.Record.Body = "source 日本語 \\\"\n"
	client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
		var answer any
		switch r.URL.Path {
		case "/v1/version":
			versions.Add(1)
			answer = map[string]any{"preview_capabilities": localapi.PreviewCapabilities{Schema: localapi.PreviewCapabilitiesSchema, EntitiesOmitted: true}}
		case "/v1/index":
			n := searches.Add(1)
			var got core.CompileRequest
			if err := json.NewDecoder(r.Body).Decode(&got); err != nil {
				t.Error(err)
			}
			want := core.CompileRequest{Scope: config.Scope, Purpose: "context", AvailableTokens: 9500, RequestID: requestID, CompactPreviewEntities: true, Context: &core.ContextPins{Revision: "fixed-revision"}}
			if n == 1 {
				want.Query = "quoted source"
				want.AvailableTokens = 9000
				want.MemoryBudgetBytes, want.MinPullBytes = ptrInt(8000), ptrInt(1000)
				want.PageOffset = ptrInt(0)
				want.AdvisoryConflicts = true
				want.Entities = []core.EntityRef{{Kind: "file", Name: "source.go"}}
				want.Kinds = []string{"lesson"}
				want.ErrorSignature = strings.Repeat("a", 64)
				want.Context.TaskPhase = "review"
			} else {
				want.BrowseOffset = ptrInt(0)
			}
			if !reflect.DeepEqual(got, want) {
				t.Errorf("normalized API request changed: got %+v want %+v", got, want)
			}
			answer = core.IndexResult{Package: core.Package{ReceiptID: "receipt", Seal: "seal", Semantic: core.SemanticPackage{Schema: "cairn.semantic/19", Index: []core.IndexEntry{{RecordID: "source", Version: 3, Summary: "日本語 source", SummarySpan: &core.ByteSpanRequest{Offset: 40, Length: 20}}}}}, Handles: []core.IndexHandle{{RecordID: "source", Version: 3, Handle: "handle"}}, CreditsRemaining: 4, BytesRemaining: 3000}
		case "/v1/expand":
			pulls.Add(1)
			var got core.ExpandRequest
			if err := json.NewDecoder(r.Body).Decode(&got); err != nil {
				t.Error(err)
			}
			if !reflect.DeepEqual(got, wantPull) {
				t.Errorf("pull request changed: %+v want %+v", got, wantPull)
			}
			answer = expansion
		default:
			t.Errorf("unexpected backend route %s", r.URL.Path)
		}
		if err := json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": answer}); err != nil {
			t.Error(err)
		}
	})
	server, err := NewReadOnlyServer(localRetrievalBackend{client: client, previewChoice: new(localapi.PreviewCapabilityChoice)}, config)
	if err != nil {
		t.Fatal(err)
	}
	// Constructor retains its own fixed context rather than the caller's pointer.
	fixed.Revision = "later-mutation"
	session := connect(t, ctx, server)
	args := searchArgs{Query: "quoted source", AvailableTokens: ptrInt(9000), MemoryBudgetBytes: ptrInt(8000), MinPullBytes: ptrInt(1000), Offset: ptrInt(0), AdvisoryConflicts: true, Entities: []core.EntityRef{{Kind: "file", Name: "source.go"}}, Kinds: []string{"lesson"}, ErrorSignature: strings.Repeat("a", 64), Context: &core.ContextPins{TaskPhase: "review"}, RequestID: requestID}
	result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "cairn_search", Arguments: args})
	if err != nil || result.IsError {
		t.Fatalf("search: %+v %v", result, err)
	}
	var view searchResult
	if err := json.Unmarshal([]byte(result.Content[0].(*mcp.TextContent).Text), &view); err != nil {
		t.Fatal(err)
	}
	if view.Schema != "cairn.mcp-search/1" || view.SourceSchema != "cairn.semantic/19" || view.SourceSeal != "seal" || view.ReceiptID != "receipt" || view.RequestID != requestID || len(view.Index) != 1 || view.Index[0].SummarySpan.Offset != 40 {
		t.Fatalf("lost source view: %+v", view)
	}
	wantPull = view.Index[0].PullArguments
	if _, err := uuid.Parse(wantPull.RequestID); err != nil || wantPull.ReceiptID != "receipt" || wantPull.Handle != "handle" {
		t.Fatalf("unusable pull arguments: %+v", wantPull)
	}
	wantPull.Span = &core.ByteSpanRequest{Offset: 40, Length: 20}
	wantPull.OpeningBytes = 96
	result, err = session.CallTool(ctx, &mcp.CallToolParams{Name: "cairn_pull", Arguments: wantPull})
	if err != nil || result.IsError {
		t.Fatalf("pull: %+v %v", result, err)
	}
	var got core.Expansion
	if err := json.Unmarshal([]byte(result.Content[0].(*mcp.TextContent).Text), &got); err != nil || !reflect.DeepEqual(got, expansion) {
		t.Fatalf("expansion changed: %+v %v", got, err)
	}
	result, err = session.CallTool(ctx, &mcp.CallToolParams{Name: "cairn_search", Arguments: searchArgs{Browse: true, RequestID: requestID}})
	if err != nil || result.IsError || searches.Load() != 2 || pulls.Load() != 1 || versions.Load() != 1 {
		t.Fatalf("browse or backend lifetime changed: %+v %v calls %d/%d/%d", result, err, searches.Load(), pulls.Load(), versions.Load())
	}
}

func TestReadOnlyRetrievalPreservesRefusalsAndFinalEnvelopeBound(t *testing.T) {
	for _, tc := range []struct {
		name, tool, want string
		wantCalls        int32
	}{
		{"probe-denied", "cairn_search", "AUTHORITY_DENIED: denied; refusal_id=retained", 1},
		{"pull-denied", "cairn_pull", "AUTHORITY_DENIED: denied; refusal_id=retained", 1},
		{"stale-pull", "cairn_pull", "STALE_HANDLE", 1},
		{"transport-detail", "cairn_pull", "Cairn operation failed", 1},
		{"missing-handle", "cairn_search", "Cairn operation failed", 2},
		{"oversized-view", "cairn_search", "BUDGET_REFUSED", 2},
	} {
		for _, profile := range []string{"", "compact/1"} {
			name := "full"
			if profile != "" {
				name = profile
			}
			t.Run(tc.name+"/"+name, func(t *testing.T) {
				var calls atomic.Int32
				client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
					calls.Add(1)
					if tc.name == "probe-denied" || tc.name == "pull-denied" {
						w.WriteHeader(http.StatusForbidden)
						w.Write([]byte(`{"schema":"cairn.response/1","ok":false,"status":"AUTHORITY_DENIED","message":"denied","refusal_id":"retained"}`))
						return
					}
					if tc.name == "stale-pull" {
						w.WriteHeader(http.StatusConflict)
						w.Write([]byte(`{"schema":"cairn.response/1","ok":false,"status":"STALE_HANDLE","message":"stale source handle"}`))
						return
					}
					if tc.name == "transport-detail" {
						w.Write([]byte("private-backend-canary"))
						return
					}
					var answer any = map[string]any{}
					if r.URL.Path == "/v1/index" {
						index := core.IndexResult{Package: core.Package{ReceiptID: "receipt", Semantic: core.SemanticPackage{Index: []core.IndexEntry{{RecordID: "record", Version: 1, Summary: "source"}}}}}
						if tc.name == "oversized-view" {
							index.Handles = []core.IndexHandle{{RecordID: "record", Version: 1, Handle: "handle"}}
							index.Package.Semantic.Index[0].Summary = strings.Repeat("日本語 \\\"", 1000)
						}
						answer = index
					}
					if err := json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": answer}); err != nil {
						t.Error(err)
					}
				})
				server, err := NewReadOnlyServer(localRetrievalBackend{client: client, previewChoice: new(localapi.PreviewCapabilityChoice)}, Config{Scope: core.Scope{Repo: "repo", TaskID: "task", RunID: "run"}, AvailableTokens: 2000, SearchView: profile})
				if err != nil {
					t.Fatal(err)
				}
				var args any = searchArgs{Query: "source"}
				if tc.tool == "cairn_pull" {
					args = core.ExpandRequest{RequestID: "request", ReceiptID: "receipt", Handle: "handle"}
				}
				result, err := connect(t, context.Background(), server).CallTool(context.Background(), &mcp.CallToolParams{Name: tc.tool, Arguments: args})
				if err != nil || !result.IsError || len(result.Content) != 1 {
					t.Fatalf("refusal lost: %+v %v", result, err)
				}
				message := result.Content[0].(*mcp.TextContent).Text
				if !strings.Contains(message, tc.want) || strings.Contains(message, "private-backend-canary") || calls.Load() != tc.wantCalls {
					t.Fatalf("refusal/retry/secret mismatch: %s calls %d", message, calls.Load())
				}
			})
		}
	}
}

func TestReadOnlySearchValidationPrecedesBackend(t *testing.T) {
	var calls atomic.Int32
	client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.WriteHeader(http.StatusInternalServerError)
	})
	server, err := NewReadOnlyServer(localRetrievalBackend{client: client, previewChoice: new(localapi.PreviewCapabilityChoice)}, Config{Scope: core.Scope{Repo: "repo", TaskID: "task", RunID: "run"}, Context: &core.ContextPins{Revision: "fixed"}, AvailableTokens: 9500})
	if err != nil {
		t.Fatal(err)
	}
	session := connect(t, context.Background(), server)
	for _, tc := range []struct {
		args searchArgs
		want string
	}{
		{searchArgs{Query: "q", AvailableTokens: ptrInt(9501)}, "available_tokens"},
		{searchArgs{Query: "q", MinPullBytes: ptrInt(1)}, "min_pull_bytes"},
		{searchArgs{Browse: true, Query: "q"}, "search requires"},
		{searchArgs{Query: "q", Context: &core.ContextPins{Revision: "changed"}}, "context"},
	} {
		result, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_search", Arguments: tc.args})
		if err != nil || !result.IsError || !strings.Contains(result.Content[0].(*mcp.TextContent).Text, tc.want) {
			t.Fatalf("validation lost: %+v %v", result, err)
		}
	}
	if calls.Load() != 0 {
		t.Fatal("invalid facade request reached backend")
	}
}
