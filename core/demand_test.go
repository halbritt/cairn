package core

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestCouncilAuditBlockedDemandDocket(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	writer := testStore(t, Channel{Principal: "audit-writer:" + repo})
	_, err := writer.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	p, err := op.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "planning", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil || p.Semantic.Omitted["CLASS_NOT_CONSEQUENTIAL"] != 1 {
		t.Fatalf("fixture compile %+v %v", p, err)
	}
	docket, err := op.Docket(ctx, repo)
	if err != nil {
		t.Fatal(err)
	}
	if len(docket.Items) == 0 {
		t.Error("accepted blocked consequential demand must reach the docket; got no items")
	}
}

func TestProtectedDemandExplanationAndRelevance(t *testing.T) {
	ctx := context.Background()
	repo := uuid.NewString()
	principal := "explain:" + repo
	s := testStore(t, Channel{Principal: principal})
	draft := projectNote(repo)
	draft.Sensitivity = "shareable"
	matching, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	draft.Body = "unrelated advice"
	if _, err = s.Create(ctx, CreateRequest{uuid.NewString(), draft}); err != nil {
		t.Fatal(err)
	}
	draft.Sensitivity = "local"
	draft.Body = "fixture_error PRIVATE_EXPLANATION_CANARY"
	hidden, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error QUERY_CANARY", Purpose: "planning", AvailableTokens: 64000}
	p, err := s.Compile(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	for range 2 {
		if _, err = s.Compile(ctx, req, Destination{"hosted", false}); err != nil {
			t.Fatal(err)
		}
	}
	explanation, err := s.Explain(ctx, p.ReceiptID)
	if err != nil {
		t.Fatal(err)
	}
	if explanation.Version != 2 || len(explanation.Candidates) != 2 {
		t.Fatalf("detail missing or retry duplicated: %+v", explanation)
	}
	blocked := 0
	for _, c := range explanation.Candidates {
		if c.RecordID == hidden.RecordID {
			t.Fatal("hidden destination candidate retained")
		}
		if c.EscalationBlocked {
			blocked++
			if c.RecordID != matching.RecordID || c.LexicalMatches != 3 || c.Reason != "CLASS_NOT_CONSEQUENTIAL" {
				t.Fatalf("wrong demand: %+v", c)
			}
		}
	}
	if blocked != 1 {
		t.Fatalf("blocked demand = %d", blocked)
	}
	rendered, err := p.Render()
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(rendered, matching.RecordID) || strings.Contains(rendered, "QUERY_CANARY") {
		t.Fatal("protected detail or query entered model package")
	}
	for _, channel := range []Channel{{Principal: "other:" + repo}, {Principal: principal, Repo: "another"}} {
		other := testStore(t, channel)
		_, err = other.Explain(ctx, p.ReceiptID)
		requireCode(t, err, "AUTHORITY_DENIED")
	}
	docket, err := s.Docket(ctx, repo)
	if err != nil || len(docket.Items) != 1 || docket.Items[0].RecordID != matching.RecordID {
		t.Fatalf("demand docket: %+v %v", docket, err)
	}
	changed := matching.Draft
	changed.Body = "Corrected fixture_error advice"
	if _, err = s.Edit(ctx, EditRequest{uuid.NewString(), matching.RecordID, matching.Version, changed}); err != nil {
		t.Fatal(err)
	}
	docket, err = s.Docket(ctx, repo)
	if err != nil || len(docket.Items) != 0 {
		t.Fatalf("obsolete demand remained current: %+v %v", docket, err)
	}
	explanation, err = s.Explain(ctx, p.ReceiptID)
	if err != nil || len(explanation.Candidates) != 2 {
		t.Fatal("historical demand lost")
	}
}
