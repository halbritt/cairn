package mcpapi

import (
	"context"
	"encoding/json"
	"github.com/halbritt/cairn/core"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestInspectionPolicyForwardingAndValidation(t *testing.T) {
	var calls atomic.Int32
	client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/version" {
			w.Write([]byte(`{"schema":"cairn.response/1","ok":true,"data":{}}`))
			return
		}
		calls.Add(1)
		var req map[string]any
		json.NewDecoder(r.Body).Decode(&req)
		if req["inspection_policy"] != "first-fitting-whole/1" || req["memory_budget_bytes"] != float64(4300) || req["min_pull_bytes"] != nil {
			t.Errorf("inspection intent changed: %+v", req)
		}
		w.Write([]byte(`{"schema":"cairn.response/1","ok":true,"data":{"package":{"semantic":{"memory_budget":{"schema":"cairn.memory-budget/3","bytes":4300,"inspection_policy":"first-fitting-whole/1","inspection_status":"no_candidates"}}},"bytes_remaining":2000}}`))
	})
	session := diagnosticSession(t, client, 32000)
	result, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_search", Arguments: map[string]any{"query": "fixture", "memory_budget_bytes": 4300, "inspection_policy": "first-fitting-whole/1"}})
	if err != nil || result.IsError {
		t.Fatalf("inspection policy invocation: %v %+v", err, result)
	}
	if !strings.Contains(result.Content[0].(*mcp.TextContent).Text, `"inspection_status":"no_candidates"`) {
		t.Fatal("inspection status missing")
	}
	for _, args := range []map[string]any{
		{"inspection_policy": "first-fitting-whole/1"},
		{"memory_budget_bytes": 4300, "inspection_policy": "unknown"},
		{"memory_budget_bytes": 4300, "inspection_policy": "first-fitting-whole/1", "min_pull_bytes": 1800},
	} {
		args["query"] = "fixture"
		result, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_search", Arguments: args})
		if err != nil || !result.IsError {
			t.Fatalf("invalid inspection accepted: %+v %v %+v", args, err, result)
		}
	}
	if calls.Load() != 1 {
		t.Fatalf("invalid request reached API: %d", calls.Load())
	}
}

func TestInspectionPolicyEnforcesNativeEnvelopeAndNoFallback(t *testing.T) {
	for _, name := range []string{"cairn_search", "cairn_prepare_note"} {
		for _, scenario := range []string{"ignored_policy", "oversized_envelope", "wrong_cap"} {
			t.Run(name+"/"+scenario, func(t *testing.T) {
				var calls atomic.Int32
				client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
					if r.URL.Path == "/v1/version" {
						w.Write([]byte(`{"schema":"cairn.response/1","ok":true,"data":{}}`))
						return
					}
					calls.Add(1)
					var req core.CompileRequest
					if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
						t.Error(err)
					}
					if req.InspectionPolicy != "first-fitting-whole/1" || req.MemoryBudgetBytes == nil {
						t.Errorf("lost intent: %+v", req)
						return
					}
					cap := *req.MemoryBudgetBytes
					want := 4300
					if name == "cairn_prepare_note" {
						want -= currentPreparationOverhead(t)
					}
					if cap != want {
						t.Errorf("preparation guidance cap wrong: %d want %d", cap, want)
					}
					var b *core.MemoryBudget
					result := core.IndexResult{}
					if scenario != "ignored_policy" {
						b = &core.MemoryBudget{Schema: "cairn.memory-budget/3", Bytes: cap, InspectionPolicy: req.InspectionPolicy, InspectionStatus: "ready", MinPullBytes: cap - 10}
						result.Package.Semantic.Index = []core.IndexEntry{{RecordID: "source", Version: 1, BodySHA256: strings.Repeat("a", 64)}}
						result.Handles = []core.IndexHandle{{RecordID: "source", Version: 1, Handle: "handle"}}
						result.BytesRemaining = cap - 10
						if scenario == "wrong_cap" {
							b.Bytes++
						}
					}
					result.Package.Semantic.MemoryBudget = b
					json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": result})
				})
				session := diagnosticSession(t, client, 32000)
				result, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: name, Arguments: map[string]any{"query": "fixture", "memory_budget_bytes": 4300, "inspection_policy": "first-fitting-whole/1"}})
				code := "INVALID_RESPONSE"
				if scenario == "oversized_envelope" {
					code = "BUDGET_REFUSED"
				}
				if err != nil || !result.IsError || !strings.Contains(result.Content[0].(*mcp.TextContent).Text, code) {
					t.Fatalf("native allocation not enforced: %v %+v", err, result)
				}
				if calls.Load() != 1 {
					t.Fatalf("silent fallback/retry: %d", calls.Load())
				}
			})
		}
	}
}
