package core

import (
	"bytes"
	"context"
	"reflect"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestPreviewCompactionStoreHandlesReplayAndCurrentness(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:preview-compact"})
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Kind = "note"
	d.Sensitivity = "shareable"
	fixture := metadataCandidates("note")
	d.Body = fixture[0].selection.Record.Body[:1200]
	d.Entities = fixture[0].selection.Record.Entities
	first, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	other := projectNote(repo)
	other.Kind = "note"
	other.Sensitivity = "shareable"
	other.Body = "CAPLAB handoff retrieval usefulness context. " + strings.Repeat("Synthetic lower-ranked context. ", 50)
	second, err := s.Create(ctx, CreateRequest{uuid.NewString(), other})
	if err != nil {
		t.Fatal(err)
	}
	private := d
	private.Sensitivity = "local"
	private.Body = "Private Handoff: Cairn retrieval usefulness."
	privateNote, err := s.Create(ctx, CreateRequest{uuid.NewString(), private})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "preview", "compact"}, Query: `"Handoff: Cairn retrieval usefulness"`, Purpose: "context", AvailableTokens: 6500}
	dest := Destination{"hosted", false}
	index, err := s.Index(ctx, req, dest)
	if err != nil || len(index.Package.Semantic.Index) != 1 || len(index.Handles) != 1 {
		t.Fatalf("index %+v %v", index, err)
	}
	entry := index.Package.Semantic.Index[0]
	if entry.RecordID != first.RecordID || entry.EntitiesOmitted != 2 || entry.SummarySpan == nil || entry.RecordID == privateNote.RecordID {
		t.Fatalf("wrong compact source: %+v", entry)
	}
	pull := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, Handle: index.Handles[0].Handle}
	expanded, err := s.Expand(ctx, pull, dest)
	if err != nil || expanded.Selection.Record.Body != d.Body || !reflect.DeepEqual(expanded.Selection.Record.Entities, d.Entities) {
		t.Fatalf("full checked source/association lost: %+v %v", expanded, err)
	}
	retry, err := s.Expand(ctx, pull, dest)
	if err != nil || retry.CreditsRemaining != expanded.CreditsRemaining || retry.BytesRemaining != expanded.BytesRemaining {
		t.Fatalf("pull retry changed allowance: %v", err)
	}
	outsider := testStore(t, Channel{Principal: "outsider:" + repo, Repo: repo})
	_, err = outsider.Expand(ctx, pull, dest)
	requireCode(t, err, "AUTHORITY_DENIED")
	oldBody := d.Body
	d.Body = "Corrected current source."
	if _, err = s.Edit(ctx, EditRequest{uuid.NewString(), first.RecordID, first.Version, d}); err != nil {
		t.Fatal(err)
	}
	pull.RequestID = uuid.NewString()
	_, err = s.Expand(ctx, pull, dest)
	requireCode(t, err, "STALE_HANDLE")
	replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query})
	if err != nil || replay.Seal != index.Package.Seal {
		t.Fatalf("compact historical replay: %v", err)
	}
	// Synthesize old sealed receipts using their preserved presentation path and
	// the original retained facts. Never rewrite production receipts.
	first.Body = oldBody
	candidates := []candidate{{literal: true, score: 9, selection: Selection{Record: first}}, {score: 2, selection: Selection{Record: second}}}
	for _, policy := range []string{"", previewBoundariesV1} {
		p := index.Package.Semantic
		p.Presentation = policy
		p.Omitted = omissionCensus()
		evals := map[string]*CandidateEvaluation{first.RecordID: {}, second.RecordID: {}}
		p, err = packIndex(p, candidates, evals, req.Query)
		if err != nil {
			t.Fatal(err)
		}
		// Preserve unrelated eligibility omissions from the original read set.
		for reason, count := range index.Package.Semantic.Omitted {
			if reason != "OPTIONAL_BUDGET" && reason != "TOTAL_BUDGET" {
				p.Omitted[reason] = count
			}
		}
		raw, seal, err := sealPackage(p)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_receipt SET semantic_body=$2,seal=$3 WHERE receipt_id=$1`, index.Package.ReceiptID, raw, seal); err != nil {
			t.Fatal(err)
		}
		old, err := s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query})
		if err != nil || old.Seal != seal {
			t.Fatalf("old policy replay %q: %v", policy, err)
		}
		encoded, _, err := sealPackage(old.Semantic)
		if err != nil || !bytes.Equal(encoded, raw) {
			t.Fatal("old canonical bytes changed")
		}
		saved, err := s.Replay(ctx, index.Package.ReceiptID)
		if err != nil || saved.Seal != seal {
			t.Fatalf("old saved replay: %v", err)
		}
	}
}
