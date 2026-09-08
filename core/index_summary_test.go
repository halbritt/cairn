package core

import (
	"context"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/google/uuid"
)

func TestIndexSummaryShowsMatchingSourcePassage(t *testing.T) {
	preamble := strings.Repeat("General setup information. ", 20)
	for _, test := range []struct {
		name, body, query, want string
	}{
		{"late command", preamble + "Use cairn opencode-config to generate the server configuration.", "opencode-config", "opencode-config"},
		{"identifier words", preamble + "Set CAIRN_HOME to select the data directory.", "cairn home", "CAIRN_HOME"},
		{"case", preamble + "Use PostgreSQL for operational storage.", "postgresql", "PostgreSQL"},
		{"unicode", strings.Repeat("前置き\u2003説明です。 ", 30) + "保管場所を確認する。", "保管場所を確認する", "保管場所を確認する"},
		{"negation", preamble + "Never send local-only records to hosted models.", "never", "Never"},
		{"distinct terms", strings.Repeat("configuration ", 40) + "Set executable permissions before starting the server.", "configuration executable permissions", "executable permissions"},
	} {
		t.Run(test.name, func(t *testing.T) {
			got := indexSummary(test.body, test.query, "lexical-scope-recency/4")
			if !strings.Contains(got, test.want) || len(got) > 160 || !utf8.ValidString(got) {
				t.Fatalf("unusable preview: %q", got)
			}
			if !strings.Contains(test.body, strings.TrimSuffix(strings.TrimPrefix(got, "..."), "...")) {
				t.Fatalf("preview synthesized text: %q", got)
			}
		})
	}
	for _, query := range []string{"", "what?", "unmatched_query"} {
		if got := indexSummary(preamble, query, "lexical-scope-recency/4"); got != indexEntry(Record{Draft: Draft{Body: preamble}}).Summary {
			t.Fatalf("query %q changed the unmatched prefix: %q", query, got)
		}
	}
	body := "configuration instructions. " + preamble + "configuration"
	if got := indexSummary(body, "configuration", "lexical-scope-recency/4"); got != body[:160] {
		t.Fatalf("equal matches did not preserve prefix: %q", got)
	}
}

func TestIndexMatchingPreviewKeepsExactPullAndHistoricalVersion(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:preview"})
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Body = strings.Repeat("General procedure context. ", 20) + "Generate configuration with opencode-config."
	note, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "preview", "run"}, Query: "opencode-config", Purpose: "context", AvailableTokens: 32000}
	index, err := s.Index(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if len(index.Package.Semantic.Index) != 1 || !strings.Contains(index.Package.Semantic.Index[0].Summary, "opencode-config") {
		t.Fatalf("search hid the matching command: %+v", index.Package.Semantic.Index)
	}
	expanded, err := s.Expand(ctx, ExpandRequest{uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle}, Destination{"local", true})
	if err != nil || expanded.Selection.Record.Body != draft.Body {
		t.Fatalf("preview replaced full body: %+v, %v", expanded, err)
	}
	draft.Body = "New procedure no longer mentioning the old command."
	if _, err := s.Edit(ctx, EditRequest{uuid.NewString(), note.RecordID, note.Version, draft}); err != nil {
		t.Fatal(err)
	}
	recompiled, err := s.Recompile(ctx, RecompileRequest{index.Package.ReceiptID, req.Query})
	if err != nil || recompiled.Seal != index.Package.Seal {
		t.Fatalf("historical preview changed after edit: %+v, %v", recompiled, err)
	}
}

func FuzzIndexSummarySourceBounds(f *testing.F) {
	f.Add(strings.Repeat("前文。 ", 40)+"Use CAIRN_HOME for storage.", "cairn home")
	f.Add(strings.Repeat("setup ", 100)+"target instruction", "target")
	f.Fuzz(func(t *testing.T, body, query string) {
		if !utf8.ValidString(body) || !utf8.ValidString(query) || len(body) > 65536 || len(query) > 4096 {
			t.Skip()
		}
		got := indexSummary(body, query, "lexical-scope-recency/4")
		if len(got) > 160 || !utf8.ValidString(got) || !strings.Contains(body, strings.TrimSuffix(strings.TrimPrefix(got, "..."), "...")) {
			t.Fatalf("invalid source excerpt: %q", got)
		}
	})
}
