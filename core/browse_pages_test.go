package core

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestBrowsePagesReachOlderNotesWithoutSkippingMandatoryContext(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Kind, draft.Body, draft.Sensitivity = "instruction", "Keep source references with the answer.", "shareable"
	required, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: root.ID, Mandatory: true, PolicyKey: "sources", Reason: "Required on every browse page"})
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]bool{}
	draft.Kind = "note"
	for i := 0; i < 7; i++ {
		draft.Body = fmt.Sprintf("Procedure %d: %s", i, strings.Repeat("selected reusable knowledge ", 12))
		if i == 3 {
			draft.Body = "A short procedure among longer previews."
		}
		record, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft})
		if err != nil {
			t.Fatal(err)
		}
		want[record.RecordID] = true
	}
	draft.Sensitivity, draft.Body = "local", "Private note must not appear or consume a hosted page position."
	if _, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft}); err != nil {
		t.Fatal(err)
	}
	seen := map[string]bool{}
	offset, pages := 0, 0
	for {
		if pages >= 10 {
			t.Fatal("browse failed to make bounded progress")
		}
		index, err := op.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 10000, BrowseOffset: &offset}, Destination{"hosted", false})
		if err != nil {
			t.Fatal(err)
		}
		p := index.Package.Semantic
		if p.Schema != "cairn.semantic/6" || p.Browse == nil || p.Browse.Offset != offset || len(p.Selected) != 1 || p.Selected[0].Record.RecordID != required.RecordID || !p.Selected[0].Mandatory {
			t.Fatalf("page lost its offset or required instruction: %+v", p)
		}
		if len(p.Index) == 0 || p.AvailableTokens != 10000 || p.OptionalLimit != 1000 {
			t.Fatalf("page changed its budget or made no progress: %+v", p)
		}
		for _, entry := range p.Index {
			if !want[entry.RecordID] || seen[entry.RecordID] {
				t.Fatalf("unexpected or repeated optional record: %s", entry.RecordID)
			}
			seen[entry.RecordID] = true
		}
		replay, err := op.Recompile(ctx, RecompileRequest{index.Package.ReceiptID, ""})
		if err != nil || replay.Seal != index.Package.Seal {
			t.Fatalf("page replay: %v", err)
		}
		pulled, err := op.Expand(ctx, ExpandRequest{uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle}, Destination{"hosted", false})
		if err != nil || !want[pulled.Selection.Record.RecordID] {
			t.Fatalf("page pull: %+v %v", pulled, err)
		}
		pages++
		if p.Browse.NextOffset == nil {
			break
		}
		if *p.Browse.NextOffset <= offset {
			t.Fatalf("non-advancing page: %+v", p.Browse)
		}
		offset = *p.Browse.NextOffset
	}
	if len(seen) != len(want) || pages < 2 {
		t.Fatalf("older notes unreachable: pages=%d reached=%d want=%d", pages, len(seen), len(want))
	}
	offset = 10000
	last, err := op.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 10000, BrowseOffset: &offset}, Destination{"hosted", false})
	if err != nil || len(last.Package.Semantic.Index) != 0 || len(last.Package.Semantic.Selected) != 1 || last.Package.Semantic.Browse.NextOffset != nil || last.Package.Semantic.Omitted["BROWSE_OFFSET"] != len(want) {
		t.Fatalf("end page or private-note count: %+v %v", last, err)
	}
}

func TestBrowseOffsetRequiresBoundedEmptyQueryIndex(t *testing.T) {
	for _, test := range []struct {
		mode, query string
		offset      int
	}{{"", "", 0}, {"index", "query", 0}, {"index", " ", 0}, {"index", "", -1}, {"index", "", 10001}} {
		// Invalid intent is rejected before store access.
		_, err := (*Store)(nil).Compile(context.Background(), CompileRequest{Mode: test.mode, Query: test.query, BrowseOffset: &test.offset}, Destination{})
		requireCode(t, err, "INVALID_REQUEST")
	}
}
