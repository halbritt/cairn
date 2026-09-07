package core

import (
	"context"
	"strings"
	"sync"
	"testing"

	"github.com/google/uuid"
)

func TestReusableScopeSealsAndBudgets(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:compiler"})
	repo := uuid.NewString()
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	request := CompileRequest{nil, uuid.NewString(), Scope{repo, "next-task", "next-run"}, "fixture_error", "context", 32000}
	first, err := s.Compile(ctx, request, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if len(first.Semantic.Selected) != 1 || first.Semantic.Selected[0].Record.RecordID != r.RecordID {
		t.Fatalf("did not retrieve reusable lesson %+v", first)
	}
	explanation, err := s.Explain(ctx, first.ReceiptID)
	if err != nil || len(explanation.Candidates) != 1 {
		t.Fatalf("explanation: %+v %v", explanation, err)
	}
	chosen := explanation.Candidates[0]
	if chosen.Reason != "SELECTED" || chosen.Rank != 1 || chosen.Cost <= 0 || chosen.LexicalMatches != 1 || chosen.RecordID != r.RecordID {
		t.Fatalf("selection features: %+v", chosen)
	}
	request.RequestID = uuid.NewString()
	second, err := s.Compile(ctx, request, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if first.Seal != second.Seal || first.Nonce == second.Nonce || first.ReceiptID == second.ReceiptID {
		t.Fatal("semantic seal includes incidental delivery identity")
	}
	again, err := s.Compile(ctx, request, Destination{"local", true})
	if err != nil || again.ReceiptID != second.ReceiptID {
		t.Fatalf("retry %+v %v", again, err)
	}
	request.RequestID = uuid.NewString()
	request.AvailableTokens = 1000
	small, err := s.Compile(ctx, request, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	rendered, err := small.Render()
	if err != nil {
		t.Fatal(err)
	}
	if len(rendered) > 1000 || len(small.Semantic.Selected) != 0 {
		t.Fatal("small context overflow")
	}
	explanation, err = s.Explain(ctx, small.ReceiptID)
	if err != nil || len(explanation.Candidates) != 1 || explanation.Candidates[0].Reason != "OPTIONAL_BUDGET" {
		t.Fatalf("packing explanation: %+v %v", explanation, err)
	}
	if len(small.Semantic.Omitted) != 13 || small.Semantic.Omitted["OPTIONAL_BUDGET"] != 1 {
		t.Fatal("fixed omission census missing")
	}

}

func TestConcurrentCompileRetry(t *testing.T) {
	s := testStore(t, Channel{Principal: "compile:retry"})
	req := CompileRequest{nil, uuid.NewString(), Scope{uuid.NewString(), "task", "run"}, "", "context", 32000}
	var wg sync.WaitGroup
	results := make(chan Package, 6)
	errs := make(chan error, 6)
	for range 6 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			p, err := s.Compile(context.Background(), req, Destination{"local", true})
			results <- p
			errs <- err
		}()
	}
	wg.Wait()
	close(results)
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	id := ""
	for p := range results {
		if id == "" {
			id = p.ReceiptID
		}
		if id != p.ReceiptID {
			t.Fatal("retry created another receipt")
		}
	}
}

func TestConsequentialGatesAndEvidenceDegradation(t *testing.T) {
	ctx := context.Background()
	operator, root := testOperator(t)
	repo := uuid.NewString()
	writer := testStore(t, Channel{Principal: "writer:" + repo})
	r, err := writer.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{nil, uuid.NewString(), Scope{repo, "task", "run"}, "fixture_error", "planning", 64000}
	pkg, err := operator.Compile(ctx, req, Destination{"local", true})
	if err != nil || len(pkg.Semantic.Selected) != 0 {
		t.Fatalf("A entered planning: %+v %v", pkg, err)
	}
	e := testEvidence(t, operator, repo)
	e2 := testEvidence(t, operator, repo)
	_, err = operator.Promote(ctx, PromoteRequest{uuid.NewString(), r.RecordID, 1, root.ID, []string{e.ID, e2.ID}, "Promote a claim with deliberately retained evidence"})
	if err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	pkg, err = operator.Compile(ctx, req, Destination{"local", true})
	if err != nil || len(pkg.Semantic.Selected) != 1 {
		t.Fatalf("B missing: %+v %v", pkg, err)
	}
	var uses int
	if err = operator.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.record_use WHERE receipt_id=$1`, pkg.ReceiptID).Scan(&uses); err != nil || uses != 1 {
		t.Fatalf("exposure %d %v", uses, err)
	}
	if _, err = operator.pool.Exec(ctx, `UPDATE cairn.evidence SET body='different' WHERE evidence_id=$1`, e.ID); err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	pkg, err = operator.Compile(ctx, req, Destination{"local", true})
	if err != nil || len(pkg.Semantic.Selected) != 1 {
		t.Fatalf("one surviving reference must suffice: %+v %v", pkg, err)
	}
	if _, err = operator.pool.Exec(ctx, `UPDATE cairn.evidence SET state='dangling' WHERE evidence_id=$1`, e2.ID); err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	pkg, err = operator.Compile(ctx, req, Destination{"local", true})
	if err != nil || len(pkg.Semantic.Selected) != 0 || pkg.Semantic.Omitted["EVIDENCE_UNAVAILABLE"] != 1 {
		t.Fatalf("dangling B escaped: %+v %v", pkg, err)
	}
	req.RequestID = uuid.NewString()
	req.Purpose = "context"
	pkg, err = operator.Compile(ctx, req, Destination{"local", true})
	if err != nil || len(pkg.Semantic.Selected) != 1 {
		t.Fatalf("advisory degraded B missing: %+v %v", pkg, err)
	}
	states := map[string]bool{}
	for _, evidence := range pkg.Semantic.Selected[0].Evidence {
		states[evidence.State] = true
	}
	if !states["divergent"] || !states["dangling"] {
		t.Fatalf("degradation invisible: %v", states)
	}

}

func TestMandatoryPolicyConflictAndUnenforceable(t *testing.T) {
	ctx := context.Background()
	operator, root := testOperator(t)
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Kind = "instruction"
	draft.Body = "Always preserve human-authored files."
	policy, err := operator.Issue(ctx, IssueRequest{uuid.NewString(), draft, root.ID, true, false, "preserve", "Direct operator workflow instruction"})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{nil, uuid.NewString(), Scope{repo, "t", "r"}, "unrelated-query", "context", 32000}
	pkg, err := operator.Compile(ctx, req, Destination{"local", true})
	if err != nil || len(pkg.Semantic.Selected) != 1 {
		t.Fatalf("mandatory disappeared without lexical match: %+v %v", pkg, err)
	}
	req.RequestID = uuid.NewString()
	req.AvailableTokens = 256
	_, err = operator.Compile(ctx, req, Destination{"local", true})
	requireCode(t, err, "BUDGET_REFUSED")
	other := draft
	other.Body = "Overwrite all human-authored files."
	_, err = operator.Issue(ctx, IssueRequest{uuid.NewString(), other, root.ID, true, false, "preserve", "Deliberately conflicting fixture instruction"})
	if err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	req.AvailableTokens = 32000
	_, err = operator.Compile(ctx, req, Destination{"local", true})
	requireCode(t, err, "OPEN_CONFLICT")
	_, err = operator.Retract(ctx, RetractRequest{uuid.NewString(), policy.RecordID, policy.Version, root.ID, "Try to erase a deterministic policy conflict", ""})
	requireCode(t, err, "OPEN_CONFLICT")
	repo2 := uuid.NewString()
	draft.Scope.Repo = repo2
	_, err = operator.Issue(ctx, IssueRequest{uuid.NewString(), draft, root.ID, true, true, "runtime", "A fixture requiring actual runtime mediation"})
	if err != nil {
		t.Fatal(err)
	}
	req.Scope.Repo = repo2
	req.RequestID = uuid.NewString()
	_, err = operator.Compile(ctx, req, Destination{"local", true})
	requireCode(t, err, "POLICY_UNENFORCEABLE")
}

func TestHostedPrivacyAndScopeBoundary(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:privacy"})
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Body = "SECRET-CANARY-12345"
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{nil, uuid.NewString(), Scope{repo, "t", "r"}, "", "context", 32000}
	pkg, err := s.Compile(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	rendered, err := pkg.Render()
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(rendered, "SECRET-CANARY") {
		t.Fatal("hosted package disclosed a private record or its census")
	}
	for _, count := range pkg.Semantic.Omitted {
		if count != 0 {
			t.Fatal("private omission census leaked")
		}
	}
	d.Sensitivity = "shareable"
	d.Body = "Safe shareable fixture"
	if _, err = s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	pkg, err = s.Compile(ctx, req, Destination{"hosted", false})
	if err != nil || len(pkg.Semantic.Selected) != 1 {
		t.Fatalf("shareable not delivered %+v %v", pkg, err)
	}
	constrained := testStore(t, Channel{Principal: "agent:limited", Repo: "another"})
	_, err = constrained.Compile(ctx, req, Destination{"local", true})
	requireCode(t, err, "AUTHORITY_DENIED")
}

func TestConflictRetractionAndStaleRequest(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	r1, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	d := projectNote(repo)
	d.Body = "Different fixture_error advice"
	r2, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{nil, uuid.NewString(), Scope{repo, "t", "r"}, "fixture_error", "context", 64000}
	if _, err = s.Compile(ctx, req, Destination{"local", true}); err != nil {
		t.Fatal(err)
	}
	conflict, err := s.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{r1.RecordID, r2.RecordID}, "These two advice records disagree"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Compile(ctx, req, Destination{"local", true})
	requireCode(t, err, "STALE_PACKAGE")
	req.RequestID = uuid.NewString()
	pkg, err := s.Compile(ctx, req, Destination{"local", true})
	if err != nil || len(pkg.Semantic.Selected) != 0 {
		t.Fatalf("unqualified disputed item escaped %+v %v", pkg, err)
	}
	_, err = s.Retract(ctx, RetractRequest{uuid.NewString(), r1.RecordID, 1, root.ID, "Try to erase an open disagreement", ""})
	requireCode(t, err, "OPEN_CONFLICT")
	if _, err = s.Resolve(ctx, ResolveRequest{uuid.NewString(), conflict.ID, 1, root.ID, "Resolve explicitly while preserving the original dissent"}); err != nil {
		t.Fatal(err)
	}
	preview, err := s.PreviewRetraction(ctx, r1.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.Retract(ctx, RetractRequest{uuid.NewString(), r1.RecordID, 1, root.ID, "Retract the obsolete advice after resolution", preview.PreviewID}); err != nil {
		t.Fatal(err)
	}
}

func TestCommonWordsDoNotCreateRelevanceOrBlockedDemand(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "lexical:common"})
	repo := uuid.NewString()
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)}); err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "the and", Purpose: "context", AvailableTokens: 64000}
	p, err := s.Compile(ctx, req, Destination{"local", true})
	if err != nil || len(p.Semantic.Selected) != 0 {
		t.Fatalf("common words created relevance: %+v %v", p, err)
	}
	req.RequestID = uuid.NewString()
	req.Purpose = "planning"
	if _, err = s.Compile(ctx, req, Destination{"local", true}); err != nil {
		t.Fatal(err)
	}
	docket, err := s.Docket(ctx, repo)
	if err != nil || len(docket.Items) != 0 {
		t.Fatalf("common words generated promotion demand: %+v %v", docket, err)
	}
}
