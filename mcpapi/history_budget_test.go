package mcpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestHistoryExplicitAllowance(t *testing.T) {
	id := uuid.NewString()
	body := strings.Repeat("日\"\n", 1000)
	var calls atomic.Int32
	client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		var args map[string]any
		if err := json.NewDecoder(r.Body).Decode(&args); err != nil {
			t.Error(err)
		}
		if r.URL.Path != "/v1/history" || len(args) != 3 || args["repo"] != "fixture:history-budget" || args["record_id"] != id || args["version"] != float64(1) {
			t.Errorf("history scope or version changed: %s %+v", r.URL.Path, args)
		}
		json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": map[string]any{
			"historical": true, "record_id": id, "current_version": 2,
			"versions": []any{map[string]any{"version": 1, "body": body, "payload_available": true}},
		}})
	})
	server, err := NewServer(client, Config{Scope: core.Scope{Repo: "fixture:history-budget", TaskID: "test", RunID: "test"}, AvailableTokens: 4096, MaxTokens: 32000})
	if err != nil {
		t.Fatal(err)
	}
	session := connect(t, context.Background(), server)
	invoke := func(extra map[string]any, want string) *mcp.CallToolResult {
		t.Helper()
		args := map[string]any{"record_id": id, "version": 1}
		for k, v := range extra {
			args[k] = v
		}
		result, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_history", Arguments: args})
		if err != nil {
			t.Fatal(err)
		}
		text := result.Content[0].(*mcp.TextContent).Text
		if result.IsError != (want != "") || (want != "" && !strings.Contains(text, want)) {
			t.Fatalf("expected %q: %.240s", want, text)
		}
		return result
	}
	invoke(nil, "BUDGET_REFUSED")
	result := invoke(map[string]any{"available_tokens": 32000, "memory_budget_bytes": 24000}, "")
	var historical core.RecordHistory
	if err := json.Unmarshal([]byte(result.Content[0].(*mcp.TextContent).Text), &historical); err != nil {
		t.Fatal(err)
	}
	if historical.RecordID != id || historical.CurrentVersion != 2 || len(historical.Versions) != 1 || historical.Versions[0].Version != 1 || historical.Versions[0].Body == nil || *historical.Versions[0].Body != body {
		t.Fatal("exact historical body was changed or truncated")
	}
	// Measure the facade-owned envelope; the SDK adds server identity metadata after it returns.
	wire, err := json.Marshal(&mcp.CallToolResult{Content: result.Content})
	if err != nil {
		t.Fatal(err)
	}
	invoke(map[string]any{"available_tokens": 32000, "memory_budget_bytes": len(wire)}, "")
	invoke(map[string]any{"available_tokens": 32000, "memory_budget_bytes": len(wire) - 1}, "BUDGET_REFUSED")
	invoke(nil, "BUDGET_REFUSED") // An explicit larger read does not change later defaults.
	for _, args := range []map[string]any{
		{"available_tokens": 255}, {"available_tokens": 32001},
		{"memory_budget_bytes": 4097}, {"memory_budget_bytes": 255},
		{"available_tokens": 8000, "memory_budget_bytes": 8001},
	} {
		before := calls.Load()
		invoke(args, "INVALID_REQUEST")
		if calls.Load() != before {
			t.Fatal("invalid allowance reached the API")
		}
	}
}
