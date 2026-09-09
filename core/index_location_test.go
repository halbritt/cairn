package core

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestIndexPreviewLocationSupportsBoundedPull(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:preview-location"})
	draft := projectNote(uuid.NewString())
	draft.Body = strings.Repeat("Ordinary procedure context. ", 2200) + "Use cairn opencode-config for setup."
	note, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{draft.Scope.Repo, "location", "run"}, Query: "opencode-config", Purpose: "context", AvailableTokens: 32000}
	index, err := s.Index(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if len(index.Package.Semantic.Index) != 1 {
		t.Fatalf("missing note: %+v", index)
	}
	entry := index.Package.Semantic.Index[0]
	encoded, err := json.Marshal(entry)
	if err != nil {
		t.Fatal(err)
	}
	var view struct {
		SummarySpan *ByteSpanRequest `json:"summary_span"`
	}
	if err := json.Unmarshal(encoded, &view); err != nil {
		t.Fatal(err)
	}
	if view.SummarySpan == nil || view.SummarySpan.Offset < 24000 {
		t.Fatalf("matching preview cannot locate its buried source: %s", encoded)
	}
	pull := ExpandRequest{uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle, nil}
	if _, err := s.Expand(ctx, pull, Destination{"local", true}); err == nil || !strings.Contains(err.Error(), "BUDGET_REFUSED") {
		t.Fatalf("expected long whole-body refusal: %v", err)
	}
	pull.RequestID, pull.Span = uuid.NewString(), view.SummarySpan
	result, err := s.Expand(ctx, pull, Destination{"local", true})
	if err != nil || result.Span == nil || !strings.Contains(result.Span.Body, "opencode-config") || result.Selection.Record.Body != "" || result.CreditsRemaining != 3 {
		t.Fatalf("located excerpt failed: %+v, %v", result, err)
	}
	span := view.SummarySpan
	if result.Span.Body != draft.Body[span.Offset:span.Offset+span.Length] {
		t.Fatalf("location did not identify exact source bytes: %+v", result.Span)
	}
	draft.Body = "A corrected procedure."
	if _, err := s.Edit(ctx, EditRequest{uuid.NewString(), note.RecordID, note.Version, draft}); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Expand(ctx, pull, Destination{"local", true}); err == nil || !strings.Contains(err.Error(), "STALE_HANDLE") {
		t.Fatalf("cached located pull survived revision: %v", err)
	}
	recompiled, err := s.Recompile(ctx, RecompileRequest{index.Package.ReceiptID, req.Query})
	if err != nil || recompiled.Seal != index.Package.Seal {
		t.Fatalf("historical location changed after edit: %+v, %v", recompiled, err)
	}
}
