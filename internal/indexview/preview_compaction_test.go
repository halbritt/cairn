package indexview

import (
	"encoding/json"
	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"strings"
	"testing"
)

func TestCompactPreviewMarkerAndCompleteHandleSurviveCLIEnvelope(t *testing.T) {
	id, receipt, handle := uuid.NewString(), uuid.NewString(), uuid.NewString()
	span := &core.ByteSpanRequest{Offset: 7, Length: 20}
	entry := core.IndexEntry{RecordID: id, Version: 160, Class: "A", Kind: "note", Summary: "quoted \"é\" \\ source", BodySHA256: strings.Repeat("a", 64), SummarySpan: span, EntitiesOmitted: 2}
	result := core.IndexResult{Package: core.Package{ReceiptID: receipt, Seal: "seal", Semantic: core.SemanticPackage{Presentation: "cairn.preview-compact/1", Index: []core.IndexEntry{entry}}}, Handles: []core.IndexHandle{{RecordID: id, Version: 160, Handle: handle}}}
	request := uuid.NewString()
	view, err := Present(result, request, nil, 9500)
	if err != nil {
		t.Fatal(err)
	}
	wire, _ := json.Marshal(struct {
		Schema string `json:"schema"`
		OK     bool   `json:"ok"`
		Status string `json:"status"`
		Data   View   `json:"data"`
	}{"cairn.response/1", true, "OK", view})
	if _, err = Present(result, request, nil, len(wire)+1); err != nil {
		t.Fatal(err)
	}
	if _, err = Present(result, request, nil, len(wire)); err == nil {
		t.Fatal("CLI newline boundary cap bypass")
	}
	got := view.Index[0]
	if got.EntitiesOmitted != 2 || len(got.Entities) != 0 || got.RecordID != id || got.BodySHA256 != entry.BodySHA256 || got.Summary != entry.Summary || *got.SummarySpan != *span || got.PullArguments.Handle != handle || got.PullArguments.ReceiptID != receipt || got.PullArguments.RequestID == "" {
		t.Fatal("metadata/source/handle lost")
	}
}
