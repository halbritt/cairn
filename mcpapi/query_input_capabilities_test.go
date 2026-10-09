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

func TestDistinctQueryFacadeOneVersionAndRollbackRefusal(t *testing.T) {
	for _, declaration := range []string{"", `{"schema":"cairn.query-input-capabilities/1","indexed_semantic_query_v1":false}`, `{"schema":"cairn.query-input-capabilities/1","indexed_semantic_query_v1":true}`, `{"schema":"future","indexed_semantic_query_v1":true}`} {
		for _, infoFirst := range []bool{false, true} {
			t.Run(declaration+map[bool]string{false: "search", true: "info"}[infoFirst], func(t *testing.T) {
				versions, indexes := 0, 0
				rollback := false
				client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
					if r.URL.Path == "/v1/version" {
						versions++
						data := map[string]any{"schema": "cairn.build/1", "go_version": "go1.25.0", "preview_capabilities": map[string]any{"schema": "cairn.preview-capabilities/1", "entities_omitted": true}}
						if declaration != "" {
							data["query_input_capabilities"] = json.RawMessage(declaration)
						}
						json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": data})
						return
					}
					indexes++
					var req core.CompileRequest
					if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
						t.Error(err)
					}
					if r.URL.Path != "/v1/index" || req.Query != "storage" || req.SemanticQuery != "Do NOT use public storage.\n" || !req.Semantic || !req.CompactPreviewEntities {
						t.Errorf("request route/bytes changed: %+v", req)
					}
					schema := core.DistinctQuerySchema
					if rollback {
						schema = "cairn.semantic/19"
					}
					json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": core.IndexResult{Package: core.Package{Semantic: core.SemanticPackage{Schema: schema, Index: []core.IndexEntry{}}}, Handles: []core.IndexHandle{}}})
				})
				session := diagnosticSession(t, client, 9500)
				if infoFirst {
					invokeDiagnostic(t, session)
				}
				supported := strings.Contains(declaration, `/1"`) && strings.Contains(declaration, `:true`)
				params := &mcp.CallToolParams{Name: "cairn_search", Arguments: map[string]any{"query": "storage", "semantic": true, "semantic_query": "Do NOT use public storage.\n", "request_id": "b2ae2453-2a52-4109-87db-e3856cab33ed"}}
				for i := 0; i < 2; i++ {
					answer, err := session.CallTool(context.Background(), params)
					if err != nil || answer.IsError == supported {
						t.Fatalf("capability result: %v %+v", err, answer)
					}
					if !supported && !strings.Contains(answer.Content[0].(*mcp.TextContent).Text, "UNSUPPORTED_QUERY_INPUT") {
						t.Fatal("unsupported request not explicitly refused")
					}
				}
				if versions != 1 || indexes != map[bool]int{true: 2, false: 0}[supported] {
					t.Fatalf("probe/submission count: %d/%d", versions, indexes)
				}
				if supported {
					rollback = true
					answer, err := session.CallTool(context.Background(), params)
					if err != nil || !answer.IsError || !strings.Contains(answer.Content[0].(*mcp.TextContent).Text, "do not retry") || versions != 1 || indexes != 3 {
						t.Fatalf("rollback retried or accepted: %v %+v %d/%d", err, answer, versions, indexes)
					}
				}
			})
		}
	}
}
