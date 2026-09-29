package core

import (
	"context"
	"crypto/sha256"
	"fmt"
	"reflect"
	"slices"
	"testing"

	"github.com/google/uuid"
)

func TestIndexKeepsSourceIdentityAndVisibilityAcrossReadChunks(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "candidate-chunks"})
	repo := uuid.NewString()
	records := map[string]Record{}
	var first Record
	for count := 1; count <= 513; count++ {
		draft := projectNote(repo)
		draft.Sensitivity = "shareable"
		draft.Body = fmt.Sprintf("Guidance for source number %d.", count)
		if count == 2 {
			draft.Sensitivity = "local"
		}
		if count == 3 {
			draft.Pins = &Applicability{TaskClass: "other"}
		}
		if count > 3 {
			draft.Entities = []EntityRef{{Kind: "file", Name: fmt.Sprintf("source/%d.go", count)}}
			draft.Relations = []RecordRelation{{RecordID: first.RecordID, Version: 1, Relation: "derived_from"}}
		}
		record, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
		if err != nil {
			t.Fatal(err)
		}
		records[record.RecordID] = record
		if count == 1 {
			first = record
		}
		if !slices.Contains([]int{255, 256, 257, 512, 513}, count) {
			continue
		}
		req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "guidance", Purpose: "context", AvailableTokens: 32000, Context: &ContextPins{TaskClass: "build"}}
		index, err := s.Index(ctx, req, Destination{Name: "hosted"})
		if err != nil {
			t.Fatal(err)
		}
		if index.Package.Semantic.Omitted["CURRENTNESS_MISMATCH"] != 1 {
			t.Fatalf("%d sources: applicability changed: %+v", count, index.Package.Semantic.Omitted)
		}
		explanation, err := s.Explain(ctx, index.Package.ReceiptID)
		if err != nil || len(explanation.Candidates) != count-1 {
			t.Fatalf("%d sources: hidden source leaked or source lost: %+v %v", count, explanation, err)
		}
		for _, candidate := range explanation.Candidates {
			want, ok := records[candidate.RecordID]
			if !ok || want.Sensitivity == "local" || candidate.Version != want.Version {
				t.Fatalf("%d sources: wrong identity: %+v", count, candidate)
			}
			if want.Pins != nil {
				continue
			}
			if candidate.Facts == nil || candidate.Facts.BodySHA256 != fmt.Sprintf("%x", sha256.Sum256([]byte(want.Body))) || len(candidate.Facts.Evidence) != 0 || len(candidate.Facts.Authority) != 0 {
				t.Fatalf("%d sources: facts attached to wrong source: %+v", count, candidate)
			}
		}
		if len(index.Handles) == 0 {
			t.Fatal("no source delivered")
		}
		pulled, err := s.Expand(ctx, ExpandRequest{uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle, nil}, Destination{Name: "hosted"})
		if err != nil {
			t.Fatal(err)
		}
		got := pulled.Selection.Record
		want := records[got.RecordID]
		if got.Body != want.Body || !slices.Equal(got.Entities, want.Entities) || !slices.Equal(got.Relations, want.Relations) || !reflect.DeepEqual(got.Pins, want.Pins) {
			t.Fatalf("%d sources: delivered metadata changed", count)
		}
		replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query})
		if err != nil || replay.Seal != index.Package.Seal {
			t.Fatalf("%d sources: replay changed: %v", count, err)
		}
	}
}
