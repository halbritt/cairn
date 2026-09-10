package core

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestRetractionPreviewIncludesVersionedSupportingEvidence(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	source, err := op.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	e, err := op.CaptureEvidence(ctx, EvidenceRequest{RequestID: uuid.NewString(), Repo: repo, Body: "private-source-content", Source: "private-source-label"})
	if err != nil {
		t.Fatal(err)
	}
	cited, err := op.Cite(ctx, CiteRequest{RequestID: uuid.NewString(), RecordID: source.RecordID, ExpectedVersion: source.Version, Repo: repo, EvidenceCitations: []EvidenceCitationRequest{{EvidenceID: e.ID, ExpectedSHA256: e.Digest, Spans: []ByteSpanRequest{{Offset: 1, Length: 3}}}}})
	if err != nil {
		t.Fatal(err)
	}
	draft := projectNote(repo)
	draft.Relations = []RecordRelation{{source.RecordID, cited.Version, "derived_from"}}
	child, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	childCited, err := op.Cite(ctx, CiteRequest{RequestID: uuid.NewString(), RecordID: child.RecordID, ExpectedVersion: child.Version, Repo: repo, EvidenceCitations: []EvidenceCitationRequest{{EvidenceID: e.ID, ExpectedSHA256: e.Digest}}})
	if err != nil {
		t.Fatal(err)
	}
	// A correction can remove current support without erasing the earlier link.
	current, err := op.Cite(ctx, CiteRequest{RequestID: uuid.NewString(), RecordID: cited.RecordID, ExpectedVersion: cited.Version, Repo: repo, EvidenceCitations: []EvidenceCitationRequest{}})
	if err != nil {
		t.Fatal(err)
	}
	check, err := op.CheckEvidence(ctx, EvidenceCheckRequest{uuid.NewString(), e.ID})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := op.PreviewRetraction(ctx, source.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(preview)
	if err != nil {
		t.Fatal(err)
	}
	var view struct {
		SupportingEvidence []struct {
			RecordVersionRef
			Evidence []Evidence `json:"evidence"`
		} `json:"supporting_evidence"`
	}
	if err = json.Unmarshal(encoded, &view); err != nil {
		t.Fatal(err)
	}
	if len(view.SupportingEvidence) != 2 {
		t.Fatalf("missing historical and dependent evidence: %s", encoded)
	}
	want := map[RecordVersionRef]bool{{source.RecordID, cited.Version}: true, {child.RecordID, childCited.Version}: true}
	for _, entry := range view.SupportingEvidence {
		if !want[entry.RecordVersionRef] || len(entry.Evidence) != 1 {
			t.Fatalf("unexpected supporting version: %+v", entry)
		}
		delete(want, entry.RecordVersionRef)
		support := entry.Evidence[0]
		if support.ID != e.ID || support.Digest != e.Digest || support.State != "resolvable" || support.CheckGeneration != check.Generation || support.Citation == nil {
			t.Fatalf("incomplete source identity/state: %+v", support)
		}
		if entry.RecordID == source.RecordID && (len(support.Citation.Spans) != 1 || support.Citation.Spans[0] != (ByteSpanRequest{Offset: 1, Length: 3})) {
			t.Fatalf("lost citation-time passage: %+v", support)
		}
	}
	if len(want) != 0 || strings.Contains(string(encoded), "private-source-") {
		t.Fatal("missing references or raw source material in preview")
	}
	if _, err = op.pool.Exec(ctx, `UPDATE cairn.evidence SET body='altered fixture' WHERE evidence_id=$1`, e.ID); err != nil {
		t.Fatal(err)
	}
	changed, err := op.PreviewRetraction(ctx, source.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range changed.SupportingEvidence {
		if entry.Evidence[0].State != "divergent" {
			t.Fatal("preview did not check retained source bytes")
		}
	}
	// Refreshing a dependent source invalidates the old preview even when the
	// root's latest version no longer cites it.
	if _, err = op.CheckEvidence(ctx, EvidenceCheckRequest{uuid.NewString(), e.ID}); err != nil {
		t.Fatal(err)
	}
	_, err = op.Retract(ctx, RetractRequest{uuid.NewString(), source.RecordID, current.Version, root.ID, "Withdraw only after reviewing current supporting evidence", preview.PreviewID})
	requireCode(t, err, "STALE_PREVIEW")
}

func TestRetractionEvidenceBoundRefusesWithoutIssuingPreview(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	source, err := op.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	empty, err := op.PreviewRetraction(ctx, source.RecordID)
	if err != nil || empty.SupportingEvidence == nil || len(empty.SupportingEvidence) != 0 {
		t.Fatalf("empty evidence inventory: %+v %v", empty, err)
	}
	refs := []EvidenceCitationRequest{}
	for range 32 {
		e := testEvidence(t, op, repo)
		refs = append(refs, EvidenceCitationRequest{EvidenceID: e.ID, ExpectedSHA256: e.Digest})
	}
	cite := func(record Record, count int) {
		t.Helper()
		_, err := op.Cite(ctx, CiteRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: record.Version, Repo: repo, EvidenceCitations: refs[:count]})
		if err != nil {
			t.Fatal(err)
		}
	}
	cite(source, 32)
	child := func(count int) {
		t.Helper()
		draft := projectNote(repo)
		draft.Relations = []RecordRelation{{source.RecordID, 1, "derived_from"}}
		r, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft})
		if err != nil {
			t.Fatal(err)
		}
		cite(r, count)
	}
	for range 30 {
		child(32)
	}
	child(8)
	full, err := op.PreviewRetraction(ctx, source.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	count := 0
	for _, entry := range full.SupportingEvidence {
		count += len(entry.Evidence)
	}
	if count != 1000 {
		t.Fatalf("accepted boundary lost citations: %d", count)
	}
	child(1)
	for _, deletion := range []bool{false, true} {
		var result RetractionPreview
		if deletion {
			result, err = op.PreviewDeletion(ctx, source.RecordID)
		} else {
			result, err = op.PreviewRetraction(ctx, source.RecordID)
		}
		requireCode(t, err, "BUDGET_REFUSED")
		if result.PreviewID != "" {
			t.Fatal("overflow returned a preview token")
		}
	}
	var issued int
	if err := op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.retraction_preview WHERE record_id=$1`, source.RecordID).Scan(&issued); err != nil || issued != 2 {
		t.Fatalf("overflow committed a preview: %d %v", issued, err)
	}
}

func TestRetractionPreviewRejectsChangedDependencyState(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	for _, change := range []string{"root edit", "new dependency", "dependent edit"} {
		t.Run(change, func(t *testing.T) {
			repo := uuid.NewString()
			source, err := op.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
			if err != nil {
				t.Fatal(err)
			}
			draft := projectNote(repo)
			draft.Relations = []RecordRelation{{source.RecordID, source.Version, "derived_from"}}
			dependent, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft})
			if err != nil {
				t.Fatal(err)
			}
			preview, err := op.PreviewRetraction(ctx, source.RecordID)
			if err != nil {
				t.Fatal(err)
			}
			version := source.Version
			switch change {
			case "root edit":
				next, editErr := op.Edit(ctx, EditRequest{uuid.NewString(), source.RecordID, source.Version, source.Draft})
				err, version = editErr, next.Version
			case "new dependency":
				_, err = op.Create(ctx, CreateRequest{uuid.NewString(), draft})
			case "dependent edit":
				_, err = op.Edit(ctx, EditRequest{uuid.NewString(), dependent.RecordID, dependent.Version, dependent.Draft})
			}
			if err != nil {
				t.Fatal(err)
			}
			_, err = op.Retract(ctx, RetractRequest{uuid.NewString(), source.RecordID, version, root.ID, "Refuse a changed dependency inventory", preview.PreviewID})
			requireCode(t, err, "STALE_PREVIEW")
			current, err := op.Get(ctx, source.RecordID)
			if err != nil || current.Lifecycle != "active" {
				t.Fatalf("refusal changed lifecycle: %+v %v", current, err)
			}
		})
	}
}
