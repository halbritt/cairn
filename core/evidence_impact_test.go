package core

import (
	"context"
	"encoding/json"
	"fmt"
	"reflect"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestEvidenceImpactRetainsExactVersionsAndDeduplicatesPaths(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	writer := testStore(t, Channel{Principal: "impact-author:" + repo})
	evidence, err := op.CaptureEvidence(ctx, EvidenceRequest{uuid.NewString(), repo, "EVIDENCE_BODY_CANARY", "SOURCE_LABEL_CANARY", "local", ""})
	if err != nil {
		t.Fatal(err)
	}
	draft := projectNote(repo)
	draft.Body = "direct marker CLAIM_BODY_CANARY"
	a, err := writer.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	b, err := op.Promote(ctx, PromoteRequest{uuid.NewString(), a.RecordID, 1, root.ID, []string{evidence.ID}, "Promote evidence impact fixture", nil})
	if err != nil {
		t.Fatal(err)
	}
	middle := projectNote(repo)
	middle.Body = "middle marker"
	middle.Relations = []RecordRelation{{b.RecordID, b.Version, "derived_from"}}
	mid, err := writer.Create(ctx, CreateRequest{uuid.NewString(), middle})
	if err != nil {
		t.Fatal(err)
	}
	leaf := projectNote(repo)
	leaf.Kind = "instruction"
	leaf.Body = "leaf marker"
	leaf.Relations = []RecordRelation{{mid.RecordID, 1, "specializes"}, {b.RecordID, b.Version, "contradicts"}}
	c, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: leaf, GrantID: root.ID, PolicyKey: "evidence-impact", Reason: "Issue explicitly derived instruction"})
	if err != nil {
		t.Fatal(err)
	}
	query := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "marker QUERY_CANARY", Purpose: "context", AvailableTokens: 64000}
	original, err := op.Compile(ctx, query, Destination{"local", true})
	if err != nil || len(original.Semantic.Selected) != 3 {
		t.Fatalf("expected three real exposures: %v %+v", err, original.Semantic.Selected)
	}
	// A correction with independent support does not inherit the earlier
	// version's dependency merely because it shares the logical record ID.
	other := testEvidence(t, op, repo)
	draft.Body = "corrected independent"
	current, err := op.Correct(ctx, CorrectRequest{uuid.NewString(), b.RecordID, b.Version, root.ID, draft, []string{other.ID}, "Correct using different captured support", nil})
	if err != nil {
		t.Fatal(err)
	}
	query.RequestID, query.Query = uuid.NewString(), "corrected"
	if _, err = op.Compile(ctx, query, Destination{"local", true}); err != nil {
		t.Fatal(err)
	}
	req := EvidenceImpactRequest{EvidenceID: evidence.ID}
	page, err := op.InspectEvidenceImpact(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	if len(page.Records) != 3 || len(page.Uses) != 3 || page.RecordsTruncated || page.UsesTruncated {
		t.Fatalf("lost or multiplied dependencies: %+v", page)
	}
	seen := map[string]EvidenceDependent{}
	for _, r := range page.Records {
		seen[r.RecordID] = r
	}
	if r := seen[b.RecordID]; r.Version != b.Version || r.CurrentVersion != current.Version || r.Class != "B" || !r.Direct {
		t.Fatalf("historical evidence reference changed: %+v", r)
	}
	if seen[mid.RecordID].Direct || seen[c.RecordID].Direct || seen[c.RecordID].Class != "C" {
		t.Fatal("indirect links were presented as direct evidence")
	}
	if !reflect.DeepEqual(seen[mid.RecordID].Via, middle.Relations) || len(seen[c.RecordID].Via) != 2 {
		t.Fatal("indirect path explanation missing")
	}
	contradiction := false
	for _, via := range seen[c.RecordID].Via {
		if via.RecordID == b.RecordID && via.Version == b.Version && via.Relation == "contradicts" {
			contradiction = true
		}
	}
	if !contradiction {
		t.Fatal("contradiction was relabelled as derivation")
	}
	for _, use := range page.Uses {
		if use.ReceiptID != original.ReceiptID || use.Scope != query.Scope || use.ExposureKind != "body" {
			t.Fatalf("unrelated current exposure included: %+v", use)
		}
	}
	encoded, err := json.Marshal(page)
	if err != nil {
		t.Fatal(err)
	}
	for _, canary := range []string{"EVIDENCE_BODY_CANARY", "SOURCE_LABEL_CANARY", "CLAIM_BODY_CANARY", "QUERY_CANARY"} {
		if strings.Contains(string(encoded), canary) {
			t.Fatal("impact response copied source content", canary)
		}
	}
	// Inspection must retain the dependency history when supporting bytes fail.
	if _, err = op.pool.Exec(ctx, `UPDATE cairn.evidence SET body='changed fixture' WHERE evidence_id=$1`, evidence.ID); err != nil {
		t.Fatal(err)
	}
	changed, err := op.InspectEvidenceImpact(ctx, req)
	if err != nil || changed.Evidence.State != "divergent" || !reflect.DeepEqual(page.Records, changed.Records) || !reflect.DeepEqual(page.Uses, changed.Uses) {
		t.Fatalf("divergent evidence hid its impact: %v %+v", err, changed)
	}
	preview, err := op.PreviewRetraction(ctx, c.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = op.Retract(ctx, RetractRequest{uuid.NewString(), c.RecordID, c.Version, root.ID, "Withdraw instruction while retaining impact history", preview.PreviewID}); err != nil {
		t.Fatal(err)
	}
	afterRetraction, err := op.InspectEvidenceImpact(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, r := range afterRetraction.Records {
		if r.RecordID == c.RecordID && r.Version == c.Version && r.CurrentVersion == 2 && r.CurrentLifecycle == "retracted" {
			found = true
		}
	}
	if !found || !reflect.DeepEqual(afterRetraction.Uses, page.Uses) {
		t.Fatal("withdrawal concealed historical exposure")
	}
	outsider := testStore(t, Channel{Principal: "outside-impact", Repo: uuid.NewString()})
	_, err = outsider.InspectEvidenceImpact(ctx, req)
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = op.InspectEvidenceImpact(ctx, EvidenceImpactRequest{EvidenceID: uuid.NewString()})
	requireCode(t, err, "NOT_FOUND")
	_, err = op.InspectEvidenceImpact(ctx, EvidenceImpactRequest{EvidenceID: evidence.ID, RecordOffset: -1})
	requireCode(t, err, "INVALID_REQUEST")
	_, err = op.InspectEvidenceImpact(ctx, EvidenceImpactRequest{EvidenceID: evidence.ID, UseOffset: -1})
	requireCode(t, err, "INVALID_REQUEST")
	cancelled, cancel := context.WithCancel(ctx)
	cancel()
	if _, err = op.InspectEvidenceImpact(cancelled, req); err == nil {
		t.Fatal("cancelled inspection returned a report")
	}
}

func TestEvidenceImpactPaginatesRecordsAndUsesIndependently(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	writer := testStore(t, Channel{Principal: "page-author:" + repo})
	evidence := testEvidence(t, op, repo)
	empty, err := op.InspectEvidenceImpact(ctx, EvidenceImpactRequest{EvidenceID: evidence.ID})
	if err != nil || len(empty.Records) != 0 || len(empty.Uses) != 0 || empty.RecordsTruncated || empty.UsesTruncated {
		t.Fatalf("unused evidence invented impact: %v %+v", err, empty)
	}
	a, err := writer.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	b, err := op.Promote(ctx, PromoteRequest{uuid.NewString(), a.RecordID, 1, root.ID, []string{evidence.ID}, "Promote pagination source fixture", nil})
	if err != nil {
		t.Fatal(err)
	}
	expected := map[string]string{}
	for i := range 101 {
		draft := projectNote(repo)
		draft.Body = fmt.Sprintf("uniquepageword%d", i)
		draft.Relations = []RecordRelation{{b.RecordID, b.Version, "derived_from"}}
		r, err := writer.Create(ctx, CreateRequest{uuid.NewString(), draft})
		if err != nil {
			t.Fatal(err)
		}
		pkg, err := op.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: draft.Body, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
		if err != nil || len(pkg.Semantic.Selected) != 1 || pkg.Semantic.Selected[0].Record.RecordID != r.RecordID {
			t.Fatalf("pagination exposure fixture: %v", err)
		}
		expected[r.RecordID] = pkg.ReceiptID
	}
	first, err := op.InspectEvidenceImpact(ctx, EvidenceImpactRequest{EvidenceID: evidence.ID})
	if err != nil || len(first.Records) != 100 || len(first.Uses) != 100 || !first.RecordsTruncated || !first.UsesTruncated {
		t.Fatalf("first page: %v %+v", err, first)
	}
	second, err := op.InspectEvidenceImpact(ctx, EvidenceImpactRequest{evidence.ID, first.NextRecordOffset, first.NextUseOffset})
	if err != nil || len(second.Records) != 2 || len(second.Uses) != 1 || second.RecordsTruncated || second.UsesTruncated {
		t.Fatalf("last page: %v %+v", err, second)
	}
	versions := map[RecordVersionRef]bool{}
	for _, r := range append(first.Records, second.Records...) {
		if versions[r.RecordVersionRef] {
			t.Fatal("version repeated across pages")
		}
		versions[r.RecordVersionRef] = true
	}
	for _, use := range append(first.Uses, second.Uses...) {
		if expected[use.RecordID] != use.ReceiptID {
			t.Fatal("missing or repeated exposure", use)
		}
		delete(expected, use.RecordID)
	}
	if len(expected) != 0 {
		t.Fatal("pagination skipped exposures")
	}
	independent, err := op.InspectEvidenceImpact(ctx, EvidenceImpactRequest{EvidenceID: evidence.ID, UseOffset: 100})
	if err != nil || !reflect.DeepEqual(independent.Records, first.Records) || !reflect.DeepEqual(independent.Uses, second.Uses) {
		t.Fatal("record and use offsets were coupled", err)
	}
}
