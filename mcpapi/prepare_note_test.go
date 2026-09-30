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
		calls++
		if r.URL.Path != "/v1/index" {
			t.Errorf("preparation performed mutation or another read: %s", r.URL.Path)
		}
		var req core.CompileRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Fatal(err)
		}
		if req.Query != "config subject" || req.Semantic || req.AvailableTokens != 32000 || req.MemoryBudgetBytes == nil || *req.MemoryBudgetBytes != 6000-preparationOverhead || req.Scope.TaskID != "/private-session-canary" || len(req.Entities) != 1 {
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
