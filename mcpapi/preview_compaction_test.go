package mcpapi

import (
	"encoding/json"
	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"strings"
	"testing"
)

func TestCompactPreviewMarkerAndCompleteHandleSurviveMCPEnvelope(t *testing.T) {
	id, receipt, handle := uuid.NewString(), uuid.NewString(), uuid.NewString()
	span := &core.ByteSpanRequest{Offset: 7, Length: 20}
	entry := core.IndexEntry{RecordID: id, Version: 160, Class: "A", Kind: "note", Summary: "quoted \"é\" \\ source", BodySHA256: strings.Repeat("a", 64), SummarySpan: span, EntitiesOmitted: 2}
	required := core.Selection{Mandatory: true, Record: core.Record{Class: "C", Draft: core.Draft{Body: "Required whole instruction."}}}
	result := core.IndexResult{Package: core.Package{ReceiptID: receipt, Seal: "seal", Semantic: core.SemanticPackage{Presentation: "cairn.preview-compact/1", Index: []core.IndexEntry{entry}, Selected: []core.Selection{required}}}, Handles: []core.IndexHandle{{RecordID: id, Version: 160, Handle: handle}}}
	view, err := presentSearch(result, uuid.NewString())
	if err != nil {
		t.Fatal(err)
	}
	encoded, _, err := toolResult(view, nil, 9500)
	if err != nil {
		t.Fatal(err)
	}
	wire, _ := json.Marshal(encoded)
	if _, _, err = toolResult(view, nil, len(wire)); err != nil {
		t.Fatal(err)
	}
	if _, _, err = toolResult(view, nil, len(wire)-1); err == nil || !strings.Contains(err.Error(), "BUDGET_REFUSED") {
		t.Fatalf("escaped native cap bypass: %v", err)
	}
	text := encoded.Content[0].(*mcp.TextContent).Text
	var decoded searchResult
	if err = json.Unmarshal([]byte(text), &decoded); err != nil {
		t.Fatal(err)
	}
	got := decoded.Index[0]
	if got.EntitiesOmitted != 2 || len(got.Entities) != 0 || got.RecordID != id || got.BodySHA256 != entry.BodySHA256 || got.Summary != entry.Summary || *got.SummarySpan != *span || got.PullArguments.Handle != handle || got.PullArguments.ReceiptID != receipt || got.PullArguments.RequestID == "" {
		t.Fatal("metadata/source/handle lost in actual MCP text")
	}
	if decoded.Selected[0].Record.Body != required.Record.Body {
		t.Fatal("mandatory source shortened")
	}
}
