package mcpapi

import (
	"context"
	"encoding/json"
	"reflect"
	"testing"

	"github.com/halbritt/cairn/core"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func retrievalDeclarations(t *testing.T, profile string) map[string]mcp.Tool {
	t.Helper()
	server, err := NewReadOnlyServer(compactSearchBackend{}, Config{
		Scope:           core.Scope{Repo: "repo", TaskID: "task", RunID: "run"},
		AvailableTokens: 32000, ToolProfile: profile,
	})
	if err != nil {
		t.Fatal(err)
	}
	listed, err := connect(t, context.Background(), server).ListTools(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	result := make(map[string]mcp.Tool, len(listed.Tools))
	for _, tool := range listed.Tools {
		result[tool.Name] = *tool
	}
	return result
}

func withoutDescriptions(value any) any {
	switch typed := value.(type) {
	case map[string]any:
		copy := make(map[string]any, len(typed))
		for name, item := range typed {
			if name != "description" {
				copy[name] = withoutDescriptions(item)
			}
		}
		return copy
	case []any:
		copy := make([]any, len(typed))
		for i, item := range typed {
			copy[i] = withoutDescriptions(item)
		}
		return copy
	default:
		return value
	}
}

func TestCompactToolProfilePreservesRetrievalArgumentContract(t *testing.T) {
	full, compact := retrievalDeclarations(t, ""), retrievalDeclarations(t, "compact/1")
	if len(full) != 2 || len(compact) != 2 {
		t.Fatalf("profile changed tool availability: %d/%d", len(full), len(compact))
	}
	for name, previous := range full {
		current, ok := compact[name]
		if !ok {
			t.Fatalf("compact profile hid %s", name)
		}
		if !reflect.DeepEqual(previous.Annotations, current.Annotations) {
			t.Fatalf("%s changed annotations", name)
		}
		oldJSON, err := json.Marshal(previous.InputSchema)
		if err != nil {
			t.Fatal(err)
		}
		newJSON, err := json.Marshal(current.InputSchema)
		if err != nil {
			t.Fatal(err)
		}
		var oldSchema, newSchema any
		if err = json.Unmarshal(oldJSON, &oldSchema); err != nil {
			t.Fatal(err)
		}
		if err = json.Unmarshal(newJSON, &newSchema); err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(withoutDescriptions(oldSchema), withoutDescriptions(newSchema)) {
			t.Fatalf("%s changed input validation schema", name)
		}
		oldTool, _ := json.Marshal(previous)
		newTool, _ := json.Marshal(current)
		if len(newTool) >= len(oldTool) {
			t.Fatalf("%s compact tool grew: %d/%d", name, len(newTool), len(oldTool))
		}
		t.Logf("%s declaration bytes: full=%d compact=%d", name, len(oldTool), len(newTool))
	}
}

func TestCompactToolProfileRejectsUnknownVersion(t *testing.T) {
	_, err := NewReadOnlyServer(compactSearchBackend{}, Config{
		Scope:           core.Scope{Repo: "repo", TaskID: "task", RunID: "run"},
		AvailableTokens: 32000, ToolProfile: "compact/2",
	})
	if err == nil {
		t.Fatal("unknown tool profile accepted")
	}
}

func TestCompactToolProfileIsReportedByClientInfo(t *testing.T) {
	server, err := NewServer(nil, Config{Scope: core.Scope{Repo: "repo", TaskID: "task", RunID: "run"}, AvailableTokens: 32000, ToolProfile: "compact/1"})
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
	if info.Facade.ToolProfile != "compact/1" || info.API.ToolProfile != "" {
		t.Fatalf("tool profile declaration: %+v", info)
	}
}

func TestCompactToolProfileUsesUnchangedSearchHandler(t *testing.T) {
	for _, profile := range []string{"", "compact/1"} {
		server, err := NewReadOnlyServer(compactSearchBackend{index: core.IndexResult{
			Package: core.Package{ReceiptID: "receipt", Semantic: core.SemanticPackage{
				Schema: "cairn.semantic/19", Status: "READY", Selected: []core.Selection{},
				Omitted: map[string]int{"OPTIONAL_BUDGET": 0},
			}},
		}}, Config{Scope: core.Scope{Repo: "repo", TaskID: "task", RunID: "run"}, AvailableTokens: 32000, ToolProfile: profile})
		if err != nil {
			t.Fatal(err)
		}
		session := connect(t, context.Background(), server)
		result, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_search", Arguments: map[string]any{"query": "source"}})
		if err != nil || result.IsError {
			t.Fatalf("profile %q search: %+v %v", profile, result, err)
		}
		var view searchResult
		if err = json.Unmarshal([]byte(result.Content[0].(*mcp.TextContent).Text), &view); err != nil ||
			view.Schema != "cairn.mcp-search/1" || view.Status != "READY" {
			t.Fatalf("profile %q changed handler output: %+v %v", profile, view, err)
		}
		result, err = session.CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_search", Arguments: map[string]any{"browse": true, "query": "forbidden"}})
		if err != nil || !result.IsError {
			t.Fatalf("profile %q changed invalid-input refusal: %+v %v", profile, result, err)
		}
	}
}
