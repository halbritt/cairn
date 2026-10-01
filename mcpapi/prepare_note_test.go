package mcpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestPrepareNoteUsesOnlyBoundedAuthenticatedIndex(t *testing.T) {
	calls := 0
	client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/version" {
			json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": map[string]any{}})
			return
		}
		calls++
		if r.URL.Path != "/v1/index" {
			t.Errorf("preparation performed mutation or another read: %s", r.URL.Path)
		}
		var req core.CompileRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Fatal(err)
		}
		if req.Query != "config subject" || req.Semantic || req.AvailableTokens != 32000 || req.MemoryBudgetBytes == nil || *req.MemoryBudgetBytes != 6000-preparationOverhead || req.MinPullBytes != nil || req.Scope.TaskID != "/private-session-canary" || len(req.Entities) != 1 {
			t.Errorf("preparation changed retrieval contract: %+v", req)
		}
		entry := core.IndexEntry{RecordID: "old", Version: 7, Class: "A", Kind: "procedure", Summary: "日本語 \\\" configuration", BodySHA256: "digest"}
		index := core.IndexResult{Package: core.Package{ReceiptID: "receipt", Seal: "seal", Semantic: core.SemanticPackage{Schema: "cairn.semantic/17", Index: []core.IndexEntry{entry}}}, Handles: []core.IndexHandle{{RecordID: "old", Version: 7, Handle: "complete-handle"}}}
		json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": index})
	})
	session := diagnosticSession(t, client, 32000)
	result, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_prepare_note", Arguments: map[string]any{"query": "config subject", "memory_budget_bytes": 6000, "entities": []core.EntityRef{{Kind: "file", Name: "src/config.go"}}}})
	if err != nil || result.IsError {
		t.Fatalf("prepare: %+v %v", result, err)
	}
	var view searchResult
	if err = json.Unmarshal([]byte(result.Content[0].(*mcp.TextContent).Text), &view); err != nil {
		t.Fatal(err)
	}
	if view.Preparation == nil || view.Preparation.NoteSaved || len(view.Index) != 1 || view.Index[0].PullArguments.Handle != "complete-handle" || view.Index[0].PullArguments.ReceiptID != "receipt" || view.Index[0].PullArguments.RequestID == "" || view.SourceSeal != "seal" {
		t.Fatalf("lost workflow or source identity: %+v", view)
	}
	encoded, _ := json.Marshal(result)
	if len(encoded) > 6000 || calls != 1 {
		t.Fatalf("budget/call count: %d/%d", len(encoded), calls)
	}
	view.Preparation = nil
	plain, _, err := toolResult(view, nil, 6000)
	if err != nil {
		t.Fatal(err)
	}
	plainBytes, _ := json.Marshal(plain)
	if len(encoded)-len(plainBytes) > preparationOverhead {
		t.Fatal("guidance exceeds its reserved serialized overhead")
	}
}

func TestPrepareNoteRefusesInvalidInputBeforeAPI(t *testing.T) {
	client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
		t.Error("invalid preparation reached API")
	})
	session := diagnosticSession(t, client, 8000)
	for _, args := range []map[string]any{
		{"query": " "}, {"query": "subject", "memory_budget_bytes": 1279},
		{"query": "subject", "available_tokens": 8001},
		{"query": "subject", "available_tokens": 2000, "memory_budget_bytes": 2001},
	} {
		result, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_prepare_note", Arguments: args})
		if err != nil || !result.IsError {
			t.Fatalf("invalid arguments accepted: %+v %+v %v", args, result, err)
		}
	}
}

func TestPrepareNoteUnavailableAndOversizeDoNotBecomeClearance(t *testing.T) {
	for _, mode := range []string{"unavailable", "oversize", "empty"} {
		t.Run(mode, func(t *testing.T) {
			calls := 0
			client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/v1/version" {
					json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": map[string]any{}})
					return
				}
				calls++
				if mode == "unavailable" {
					w.WriteHeader(http.StatusServiceUnavailable)
					w.Write([]byte("private diagnostic"))
					return
				}
				index := core.IndexResult{}
				if mode == "oversize" {
					index.Package.Semantic.Index = []core.IndexEntry{{RecordID: "old", Version: 1, Summary: strings.Repeat("日\"\\", 2000)}}
					index.Handles = []core.IndexHandle{{RecordID: "old", Version: 1, Handle: "handle"}}
				}
				json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": index})
			})
			result, err := diagnosticSession(t, client, 8000).CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_prepare_note", Arguments: map[string]any{"query": "subject", "memory_budget_bytes": 4000}})
			if err != nil || result.IsError != (mode != "empty") || calls != 1 {
				t.Fatalf("unexpected fallback/retry: %+v %v calls=%d", result, err, calls)
			}
			encoded, _ := json.Marshal(result)
			if len(encoded) > 4000 || strings.Contains(string(encoded), "private diagnostic") {
				t.Fatal("unbounded or private error")
			}
			if mode == "empty" && !strings.Contains(string(encoded), "do not prove no predecessor") {
				t.Fatal("empty result lost uncertainty guidance")
			}
		})
	}
}

func TestPrepareNoteReserveRefusesInvalidBoundsBeforeAPI(t *testing.T) {
	client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
		t.Error("invalid reserve reached API")
	})
	session := diagnosticSession(t, client, 32000)
	for _, tc := range []struct {
		args map[string]any
		want string
	}{
		// A reserve needs the caller's explicit total; available_tokens is only policy room.
		{map[string]any{"query": "subject", "min_pull_bytes": 1000}, "min_pull_bytes"},
		{map[string]any{"query": "subject", "available_tokens": 8000, "min_pull_bytes": 1000}, "min_pull_bytes"},
		{map[string]any{"query": "subject", "memory_budget_bytes": 8000, "min_pull_bytes": 0}, "min_pull_bytes"},
		{map[string]any{"query": "subject", "memory_budget_bytes": 8000, "min_pull_bytes": -1}, "min_pull_bytes"},
		{map[string]any{"query": "subject", "memory_budget_bytes": 32000, "min_pull_bytes": 24001}, "min_pull_bytes"},
		// The receipt cap is the total less guidance, so the search limit is one byte too large.
		{map[string]any{"query": "subject", "memory_budget_bytes": 4000, "min_pull_bytes": 4000 - preparationOverhead + 1}, "min_pull_bytes"},
		{map[string]any{"query": "subject", "memory_budget_bytes": 4000, "min_pull_bytes": 4000}, "min_pull_bytes"},
		{map[string]any{"query": "subject", "memory_budget_bytes": 1279, "min_pull_bytes": 1}, "BUDGET_REFUSED"},
		{map[string]any{"query": "subject", "memory_budget_bytes": 4000, "min_pull_bytes": 1.5}, "min_pull_bytes"},
		{map[string]any{"query": "subject", "memory_budget_bytes": 4000, "min_pull_bytes": "2000"}, "min_pull_bytes"},
		{map[string]any{"query": "subject", "memory_budget_bytes": 4000, "min_pull_bytes": true}, "min_pull_bytes"},
	} {
		result, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_prepare_note", Arguments: tc.args})
		if err != nil || !result.IsError || !strings.Contains(result.Content[0].(*mcp.TextContent).Text, tc.want) {
			t.Fatalf("invalid reserve accepted or misreported: %+v %+v %v", tc.args, result, err)
		}
	}
}

func TestPrepareNoteReserveChargesGuidanceAndEnvelopeOnce(t *testing.T) {
	var forwarded []core.CompileRequest
	filler := 0
	client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
		var req core.CompileRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Fatal(err)
		}
		forwarded = append(forwarded, req)
		entry := core.IndexEntry{RecordID: "old", Version: 1, Class: "A", Kind: "procedure", Summary: strings.Repeat("a", filler)}
		index := core.IndexResult{Package: core.Package{ReceiptID: "receipt", Seal: "seal", Semantic: core.SemanticPackage{Schema: "cairn.semantic/17", Index: []core.IndexEntry{entry}}}, Handles: []core.IndexHandle{{RecordID: "old", Version: 1, Handle: "handle"}}}
		json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": index})
	})
	session := diagnosticSession(t, client, 32000)
	// The facade checks the tool result before the SDK adds transport metadata,
	// so measure the same encoding from the returned text.
	call := func(args map[string]any) (*mcp.CallToolResult, int) {
		t.Helper()
		result, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_prepare_note", Arguments: args})
		if err != nil || len(result.Content) != 1 {
			t.Fatalf("prepare: %+v %v", result, err)
		}
		encoded, err := json.Marshal(&mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: result.Content[0].(*mcp.TextContent).Text}}})
		if err != nil {
			t.Fatal(err)
		}
		return result, len(encoded)
	}
	const total, reserve = 6000, 2500
	// Serialized bytes grow one for one with ASCII summary text, so measure the
	// facade's envelope, including preparation guidance, and fit it exactly.
	_, empty := call(map[string]any{"query": "subject", "memory_budget_bytes": total})
	forwarded = forwarded[:0]
	fit := total - reserve - empty

	filler = fit
	result, size := call(map[string]any{"query": "subject", "memory_budget_bytes": total, "min_pull_bytes": reserve})
	if result.IsError || size != total-reserve {
		t.Fatalf("envelope at the reserved boundary refused or misfit: size=%d error=%v %+v", size, result.IsError, result.Content)
	}
	if !strings.Contains(result.Content[0].(*mcp.TextContent).Text, "do not prove no predecessor") {
		t.Fatal("reserved result lost preparation guidance")
	}
	// The API receives the total less guidance and the reserve once. Deducting
	// the reserve here as well would make it subtract twice.
	if len(forwarded) != 1 || forwarded[0].MemoryBudgetBytes == nil || *forwarded[0].MemoryBudgetBytes != total-preparationOverhead || forwarded[0].MinPullBytes == nil || *forwarded[0].MinPullBytes != reserve || forwarded[0].AvailableTokens != 32000 || forwarded[0].Semantic {
		t.Fatalf("reserve changed forwarded retrieval contract: %+v", forwarded)
	}

	filler = fit + 1
	result, _ = call(map[string]any{"query": "subject", "memory_budget_bytes": total, "min_pull_bytes": reserve})
	if !result.IsError || !strings.Contains(result.Content[0].(*mcp.TextContent).Text, "BUDGET_REFUSED") || len(forwarded) != 2 {
		t.Fatalf("final envelope crossed the reserved allowance: %+v calls=%d", result, len(forwarded))
	}
	// Omission keeps the historical allowance: the same envelope fits the total.
	result, size = call(map[string]any{"query": "subject", "memory_budget_bytes": total})
	if result.IsError || size != total-reserve+1 || len(forwarded) != 3 || forwarded[2].MinPullBytes != nil || forwarded[2].MemoryBudgetBytes == nil || *forwarded[2].MemoryBudgetBytes != total-preparationOverhead {
		t.Fatalf("omission changed legacy preparation: size=%d %+v %+v", size, result, forwarded)
	}

	// Range endpoints are valid requests and reach the API. A reserve that
	// leaves less than the guidance and envelope can hold then fails the final
	// check instead of truncating the result.
	filler = 0
	for _, tc := range []struct {
		total, reserve int
		refused        bool
	}{{4000, 4000 - preparationOverhead, true}, {32000, 24000, false}, {2000, 1, false}} {
		forwarded = forwarded[:0]
		result, _ = call(map[string]any{"query": "subject", "memory_budget_bytes": tc.total, "min_pull_bytes": tc.reserve})
		text := result.Content[0].(*mcp.TextContent).Text
		if len(forwarded) != 1 || *forwarded[0].MemoryBudgetBytes != tc.total-preparationOverhead || *forwarded[0].MinPullBytes != tc.reserve ||
			result.IsError != tc.refused || (tc.refused && !strings.Contains(text, "BUDGET_REFUSED")) {
			t.Fatalf("reserve endpoint %+v misreported: %s calls=%+v", tc, text, forwarded)
		}
	}
}

func TestPrepareNoteReserveUnsupportedAPIDoesNotRetryWithoutReserve(t *testing.T) {
	calls := 0
	client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/version" {
			json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": map[string]any{}})
			return
		}
		calls++
		w.Write([]byte(`{"schema":"cairn.response/1","ok":false,"status":"INVALID_REQUEST","message":"unknown field min_pull_bytes"}`))
	})
	result, err := diagnosticSession(t, client, 8000).CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_prepare_note", Arguments: map[string]any{"query": "subject", "memory_budget_bytes": 4000, "min_pull_bytes": 1000}})
	if err != nil || !result.IsError || calls != 1 || !strings.Contains(result.Content[0].(*mcp.TextContent).Text, "INVALID_REQUEST") {
		t.Fatalf("unsupported reserve was hidden or retried: %+v %v calls=%d", result, err, calls)
	}
}

func ptrInt(v int) *int { return &v }
