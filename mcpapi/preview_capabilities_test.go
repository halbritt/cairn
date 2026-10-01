package mcpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestPreviewChoiceCachedAcrossSearchPreparationAndVersion(t *testing.T) {
	for _, first := range []bool{false, true} {
		for _, diagnosticFirst := range []bool{false, true} {
			t.Run(map[bool]string{false: "legacy", true: "capable"}[first]+map[bool]string{false: "-search", true: "-info"}[diagnosticFirst], func(t *testing.T) {
				declaration, rollback := first, false
				versions, indexes := 0, 0
				requestID := "b2ae2453-2a52-4109-87db-e3856cab33ed"
				client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
					if r.URL.Path == "/v1/version" {
						versions++
						json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": map[string]any{"schema": "cairn.build/1", "go_version": "go1.25.0", "preview_capabilities": localapi.PreviewCapabilities{Schema: localapi.PreviewCapabilitiesSchema, EntitiesOmitted: declaration}}})
						return
					}
					if r.URL.Path != "/v1/index" {
						t.Errorf("unexpected path %s", r.URL.Path)
						return
					}
					indexes++
					var req core.CompileRequest
					if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
						t.Error(err)
					}
					if req.CompactPreviewEntities != first || req.RequestID != requestID {
						t.Errorf("choice or retry mutated: %+v", req)
					}
					if rollback && req.CompactPreviewEntities {
						w.WriteHeader(400)
						w.Write([]byte(`{"schema":"cairn.response/1","ok":false,"status":"INVALID_REQUEST","message":"unknown field compact_preview_entities"}`))
						return
					}
					entry := core.IndexEntry{RecordID: "source", Version: 3, Class: "A", Kind: "note", BodySHA256: strings.Repeat("a", 64), Summary: "日本語 \\\" source", SummarySpan: &core.ByteSpanRequest{Offset: 40, Length: 20}}
					if first {
						entry.EntitiesOmitted = 2
					} else {
						entry.Entities = []core.EntityRef{{Kind: "file", Name: "source.go"}}
					}
					json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": core.IndexResult{Package: core.Package{ReceiptID: "receipt", Seal: "seal", Semantic: core.SemanticPackage{Index: []core.IndexEntry{entry}}}, Handles: []core.IndexHandle{{RecordID: "source", Version: 3, Handle: "handle"}}}})
				})
				session := diagnosticSession(t, client, 9500)
				if diagnosticFirst {
					invokeDiagnostic(t, session)
				}
				for _, name := range []string{"cairn_search", "cairn_prepare_note"} {
					result, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: name, Arguments: map[string]any{"query": "subject", "request_id": requestID}})
					if err != nil || result.IsError {
						t.Fatalf("%s: %v %+v", name, err, result)
					}
					var view searchResult
					if err = json.Unmarshal([]byte(result.Content[0].(*mcp.TextContent).Text), &view); err != nil {
						t.Fatal(err)
					}
					if len(view.Index) != 1 || view.Index[0].PullArguments.Handle != "handle" || view.Index[0].SummarySpan.Offset != 40 || (view.Index[0].EntitiesOmitted == 2) != first {
						t.Fatalf("lost marker/source: %+v", view)
					}
					declaration = !first
				}
				if versions != 1 || indexes != 2 {
					t.Fatalf("redundant probe: %d/%d", versions, indexes)
				}
				invokeDiagnostic(t, session) // newer declaration must not mutate a live facade's choice
				rollback = true
				result, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_search", Arguments: map[string]any{"query": "subject", "request_id": requestID}})
				if err != nil || result.IsError != first || indexes != 3 || versions != 2 {
					t.Fatalf("rollback retried/downgraded: %v %+v calls %d/%d", err, result, indexes, versions)
				}
			})
		}
	}
}

func TestPreviewProbeFailureDoesNotSearchOrLatch(t *testing.T) {
	calls := []string{}
	fail := true
	client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
		calls = append(calls, r.URL.Path)
		if fail {
			w.WriteHeader(403)
			w.Write([]byte(`{"schema":"cairn.response/1","ok":false,"status":"AUTHORITY_DENIED","message":"denied"}`))
			return
		}
		if r.URL.Path == "/v1/version" {
			json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": map[string]any{"preview_capabilities": localapi.PreviewCapabilities{Schema: localapi.PreviewCapabilitiesSchema, EntitiesOmitted: true}}})
			return
		}
		var req core.CompileRequest
		json.NewDecoder(r.Body).Decode(&req)
		if !req.CompactPreviewEntities {
			t.Error("failed version response latched a downgrade")
		}
		json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": core.IndexResult{}})
	})
	session := diagnosticSession(t, client, 9500)
	args := &mcp.CallToolParams{Name: "cairn_search", Arguments: map[string]any{"query": "subject"}}
	result, err := session.CallTool(context.Background(), args)
	if err != nil || !result.IsError || len(calls) != 1 {
		t.Fatalf("probe failure searched: %+v %v %v", result, err, calls)
	}
	fail = false
	result, err = session.CallTool(context.Background(), args)
	if err != nil || result.IsError || strings.Join(calls, ",") != "/v1/version,/v1/version,/v1/index" {
		t.Fatalf("retry: %+v %v %v", result, err, calls)
	}
}
