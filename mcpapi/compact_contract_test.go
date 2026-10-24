package mcpapi

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// The compact search response may omit framing, but it must still lead to the
// same authorized whole or partial source and the same actionable refusals.
func TestCompactSearchPullContractForRequiredConflictAndPartialSource(t *testing.T) {
	ctx := context.Background()
	repo, receipt := uuid.NewString(), uuid.NewString()
	requiredID, primaryID, competingID, longID := uuid.NewString(), uuid.NewString(), uuid.NewString(), uuid.NewString()
	primaryHandle, longHandle := uuid.NewString(), uuid.NewString()
	required := core.Selection{Mandatory: true, Reason: "current instruction", Record: core.Record{
		RecordID: requiredID, Version: 2, Class: "C", Sensitivity: "shareable", Draft: core.Draft{Kind: "instruction", Body: "Read the current source.", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}},
	}}
	primary := core.Selection{Record: core.Record{
		RecordID: primaryID, Version: 3, Class: "A", Sensitivity: "shareable", Draft: core.Draft{Kind: "lesson", Body: "First complete position.", Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "*"}},
	}}
	competing := core.Selection{Record: core.Record{
		RecordID: competingID, Version: 4, Class: "A", Sensitivity: "shareable", Draft: core.Draft{Kind: "lesson", Body: "Competing complete position.", Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "*"}},
	}}
	conflict := core.AdvisoryConflict{ID: uuid.NewString(), Version: 1, Members: []core.RecordVersionRef{
		{RecordID: primaryID, Version: 3}, {RecordID: competingID, Version: 4},
	}}
	primary.Conflicts, competing.Conflicts = []core.AdvisoryConflict{conflict}, []core.AdvisoryConflict{conflict}
	longBody := "prefix-日本語-tail"
	sourceSum := sha256.Sum256([]byte(longBody))
	spanSum := sha256.Sum256([]byte("日本語"))
	openingSum := sha256.Sum256([]byte("prefix"))
	longSelection := core.Selection{Record: core.Record{
		RecordID: longID, Version: 5, Class: "A", Sensitivity: "shareable", Draft: core.Draft{Kind: "note", Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "*"}},
	}}
	whole := core.Expansion{Selection: primary, Competing: []core.Selection{competing}, CreditsRemaining: 3, BytesRemaining: 4000}
	partial := core.Expansion{Selection: longSelection, CreditsRemaining: 2, BytesRemaining: 3900,
		Span:          &core.NoteSpan{ByteSpan: core.ByteSpan{Offset: 7, End: 16, TotalBytes: len(longBody), Body: "日本語", SHA256: hex.EncodeToString(spanSum[:])}, SourceSHA256: hex.EncodeToString(sourceSum[:])},
		SourceOpening: &core.ByteSpan{Offset: 0, End: 6, TotalBytes: len(longBody), Body: "prefix", SHA256: hex.EncodeToString(openingSum[:])},
	}
	index := core.IndexResult{Package: core.Package{ReceiptID: receipt, Seal: "sealed-fixture", Semantic: core.SemanticPackage{
		Schema: "cairn.semantic/19", Status: "READY", Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"},
		Destination: core.Destination{Name: "hosted"}, Query: "sha256:query", Ranking: "rank/1", Tokenizer: "utf8-byte-upper-bound/1",
		Selected: []core.Selection{required}, Omitted: map[string]int{"OPTIONAL_BUDGET": 0},
		Index: []core.IndexEntry{
			{RecordID: primaryID, Version: 3, Class: "A", Kind: "lesson", Summary: "First complete position", Conflicts: []core.AdvisoryConflict{conflict}},
			{RecordID: longID, Version: 5, Class: "A", Kind: "note", Summary: "日本語", BodySHA256: hex.EncodeToString(sourceSum[:]), SummarySpan: &core.ByteSpanRequest{Offset: 7, Length: 9}},
		},
	}}, Handles: []core.IndexHandle{{RecordID: primaryID, Version: 3, Handle: primaryHandle}, {RecordID: longID, Version: 5, Handle: longHandle}},
		ExpiresAt: time.Now().Add(time.Hour), CreditsRemaining: 4, BytesRemaining: 4200,
	}
	client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
		write := func(value any) {
			t.Helper()
			if err := json.NewEncoder(w).Encode(value); err != nil {
				t.Error(err)
			}
		}
		switch r.URL.Path {
		case "/v1/version":
			write(map[string]any{"schema": "cairn.response/1", "ok": true, "data": map[string]any{"preview_capabilities": localapi.PreviewCapabilities{Schema: localapi.PreviewCapabilitiesSchema, EntitiesOmitted: true}}})
		case "/v1/index":
			var request core.CompileRequest
			if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
				t.Error(err)
			}
			if request.Scope != index.Package.Semantic.Scope || request.Query != "source" || request.Purpose != "context" {
				t.Errorf("search contract changed: %+v", request)
			}
			write(map[string]any{"schema": "cairn.response/1", "ok": true, "data": index})
		case "/v1/expand":
			var request core.ExpandRequest
			if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
				t.Error(err)
			}
			if request.ReceiptID != receipt {
				t.Errorf("lost receipt identity: %+v", request)
			}
			switch {
			case request.Handle == primaryHandle && request.Span == nil && request.OpeningBytes == 0:
				write(map[string]any{"schema": "cairn.response/1", "ok": true, "data": whole})
			case request.Handle == longHandle && reflect.DeepEqual(request.Span, &core.ByteSpanRequest{Offset: 7, Length: 9}) && request.OpeningBytes == 6:
				write(map[string]any{"schema": "cairn.response/1", "ok": true, "data": partial})
			case request.Handle == primaryHandle && request.Span != nil:
				w.WriteHeader(http.StatusBadRequest)
				write(map[string]any{"schema": "cairn.response/1", "ok": false, "status": "INVALID_REQUEST", "message": "marked conflict requires whole delivery", "refusal_id": "conflict-refusal"})
			default:
				w.WriteHeader(http.StatusConflict)
				write(map[string]any{"schema": "cairn.response/1", "ok": false, "status": "STALE_HANDLE", "message": "source handle is stale", "refusal_id": "stale-refusal"})
			}
		default:
			t.Errorf("unexpected API route %s", r.URL.Path)
		}
	})
	var observed []struct{ whole, partial core.Expansion }
	for _, profile := range []string{"", "compact/1"} {
		server, err := NewReadOnlyServer(localRetrievalBackend{client: client, previewChoice: new(localapi.PreviewCapabilityChoice)}, Config{
			Scope: index.Package.Semantic.Scope, AvailableTokens: 32000, SearchView: profile,
		})
		if err != nil {
			t.Fatal(err)
		}
		session := connect(t, ctx, server)
		call := func(name string, args any, wantError string) string {
			t.Helper()
			result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: name, Arguments: args})
			if err != nil || len(result.Content) != 1 || result.IsError != (wantError != "") {
				t.Fatalf("%s %s: %+v %v", profile, name, result, err)
			}
			body := result.Content[0].(*mcp.TextContent).Text
			if wantError != "" && !strings.Contains(body, wantError) {
				t.Fatalf("%s lost %s: %s", profile, wantError, body)
			}
			return body
		}
		search := call("cairn_search", searchArgs{Query: "source"}, "")
		var view struct {
			Schema       string           `json:"schema"`
			Status       string           `json:"status"`
			SourceSeal   string           `json:"source_seal"`
			SourceSchema string           `json:"source_schema"`
			ReceiptID    string           `json:"receipt_id"`
			Selected     []core.Selection `json:"selected"`
			Index        []searchEntry    `json:"index"`
			Omitted      map[string]int   `json:"omitted"`
			Scope        core.Scope       `json:"scope"`
			Destination  core.Destination `json:"destination"`
		}
		if err := json.Unmarshal([]byte(search), &view); err != nil {
			t.Fatal(err)
		}
		var fields map[string]json.RawMessage
		if err := json.Unmarshal([]byte(search), &fields); err != nil {
			t.Fatal(err)
		}
		if view.Status != "READY" || view.ReceiptID != receipt || view.SourceSeal != "sealed-fixture" || view.SourceSchema != "cairn.semantic/19" || view.Scope != index.Package.Semantic.Scope || view.Destination.Name != "hosted" ||
			!reflect.DeepEqual(view.Selected, []core.Selection{required}) || len(view.Index) != 2 || !reflect.DeepEqual(view.Index[0].Conflicts, []core.AdvisoryConflict{conflict}) ||
			view.Index[1].SummarySpan.Offset != 7 || view.Index[1].SummarySpan.Length != 9 || view.Index[1].BodySHA256 != hex.EncodeToString(sourceSum[:]) || view.Omitted["OPTIONAL_BUDGET"] != 0 {
			t.Fatalf("%s search lost source contract: %+v", profile, view)
		}
		if _, present := view.Omitted["TOTAL_BUDGET"]; present {
			t.Fatalf("%s made an absent omission reason a known zero", profile)
		}
		if profile == "" {
			if view.Schema != "cairn.mcp-search/1" || fields["ranking"] == nil {
				t.Fatal("default full view changed")
			}
		} else if view.Schema != "cairn.mcp-search-compact/1" || fields["ranking"] != nil {
			t.Fatal("compact view failed to omit only framing")
		}
		for i, handle := range []string{primaryHandle, longHandle} {
			if view.Index[i].PullArguments.ReceiptID != receipt || view.Index[i].PullArguments.Handle != handle {
				t.Fatalf("%s pull identity lost: %+v", profile, view.Index[i].PullArguments)
			}
		}
		var got struct{ whole, partial core.Expansion }
		if err := json.Unmarshal([]byte(call("cairn_pull", view.Index[0].PullArguments, "")), &got.whole); err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(got.whole, whole) {
			t.Fatalf("%s competing whole source changed: %+v", profile, got.whole)
		}
		refused := view.Index[0].PullArguments
		refused.RequestID = uuid.NewString()
		refused.Span = &core.ByteSpanRequest{Offset: 0, Length: 4}
		call("cairn_pull", refused, "INVALID_REQUEST: marked conflict requires whole delivery; refusal_id=conflict-refusal")
		partialRequest := view.Index[1].PullArguments
		partialRequest.Span = &core.ByteSpanRequest{Offset: 7, Length: 9}
		partialRequest.OpeningBytes = 6
		if err := json.Unmarshal([]byte(call("cairn_pull", partialRequest, "")), &got.partial); err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(got.partial, partial) || got.partial.Selection.Record.Body != "" || got.partial.Span.SourceSHA256 != hex.EncodeToString(sourceSum[:]) {
			t.Fatalf("%s partial source identity or extent changed: %+v", profile, got.partial)
		}
		stale := view.Index[1].PullArguments
		stale.RequestID = uuid.NewString()
		stale.Handle = uuid.NewString()
		call("cairn_pull", stale, "STALE_HANDLE: source handle is stale; refusal_id=stale-refusal")
		observed = append(observed, got)
	}
	if !reflect.DeepEqual(observed[0], observed[1]) {
		t.Fatal("compact and full pulls differ")
	}
}
