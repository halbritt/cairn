package core

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestRefusedCompileRetainsPartialVisibleDiagnostics(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	instruction := projectNote(repo)
	instruction.Kind = "instruction"
	instruction.Sensitivity = "shareable"
	instruction.Body = strings.Repeat("required instruction text ", 100)
	policy, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: instruction, GrantID: root.ID, Mandatory: true, PolicyKey: "large", Reason: "Synthetic mandatory budget refusal"})
	if err != nil {
		t.Fatal(err)
	}
	note := projectNote(repo)
	note.Body = "visible diagnostic marker"
	note.Sensitivity = "shareable"
	visible, err := op.Create(ctx, CreateRequest{uuid.NewString(), note})
	if err != nil {
		t.Fatal(err)
	}
	note.Sensitivity = "local"
	note.Body = "PRIVATE_REFUSAL_BODY_CANARY diagnostic"
	hidden, err := op.Create(ctx, CreateRequest{uuid.NewString(), note})
	if err != nil {
		t.Fatal(err)
	}
	request := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "diagnostic QUERY_REFUSAL_CANARY", Purpose: "context", AvailableTokens: 256}
	_, err = op.Compile(ctx, request, Destination{"hosted", false})
	requireCode(t, err, "BUDGET_REFUSED")
	var denied *Error
	if !errors.As(err, &denied) || denied.RefusalID == "" {
		t.Fatal("missing refusal reference", err)
	}
	refusal, err := op.Refusal(ctx, denied.RefusalID)
	if err != nil {
		t.Fatal(err)
	}
	if refusal.ExplanationVersion != 1 || refusal.TraceComplete || len(refusal.Candidates) != 2 || refusal.ConsideredCount != 2 {
		t.Fatalf("missing partial trace: %+v", refusal)
	}
	if refusal.AvailableTokens != 256 || refusal.OptionalLimit != 25 || refusal.Ranking != "lexical-scope-recency/4" {
		t.Fatalf("missing allocation context: %+v", refusal)
	}
	seen := map[string]CandidateEvaluation{}
	for _, candidate := range refusal.Candidates {
		if candidate.RecordID == hidden.RecordID || candidate.Facts != nil || candidate.Reason == "" {
			t.Fatalf("unsafe or ambiguous candidate: %+v", candidate)
		}
		seen[candidate.RecordID] = candidate
	}
	required := seen[policy.RecordID]
	if !required.Mandatory || required.Rank != 1 || required.Cost <= request.AvailableTokens {
		t.Fatalf("missing mandatory allocation cause: %+v", required)
	}
	if seen[visible.RecordID].LexicalMatches != 1 || seen[visible.RecordID].Reason != "OPTIONAL_BUDGET" {
		t.Fatalf("lost visible optional rejection: %+v", seen[visible.RecordID])
	}
	body, err := json.Marshal(refusal)
	if err != nil {
		t.Fatal(err)
	}
	for _, absent := range []string{hidden.RecordID, "PRIVATE_REFUSAL_BODY_CANARY", "QUERY_REFUSAL_CANARY", instruction.Body, note.Body} {
		if strings.Contains(string(body), absent) {
			t.Fatalf("refusal retained forbidden text or identifier: %s", absent)
		}
	}
	_, err = op.Compile(ctx, request, Destination{"hosted", false})
	var retry *Error
	if !errors.As(err, &retry) || retry.RefusalID != denied.RefusalID {
		t.Fatal("refusal retry changed identity", err)
	}
	repeated, err := op.Refusal(ctx, denied.RefusalID)
	if err != nil || !reflect.DeepEqual(repeated, refusal) {
		t.Fatal("retry changed retained diagnostics", err)
	}
	outsider := testStore(t, Channel{Principal: "other-refusal:" + repo, Repo: repo})
	_, err = outsider.Refusal(ctx, denied.RefusalID)
	requireCode(t, err, "AUTHORITY_DENIED")
	// Old and non-compile refusals cannot manufacture details they did not retain.
	if _, err := op.pool.Exec(ctx, `UPDATE cairn.refusal SET detail=detail-'candidates'-'explanation_version' WHERE refusal_id=$1`, denied.RefusalID); err != nil {
		t.Fatal(err)
	}
	legacy, err := op.Refusal(ctx, denied.RefusalID)
	if err != nil || legacy.ExplanationVersion != 0 || len(legacy.Candidates) != 0 || legacy.ConsideredCount != 2 {
		t.Fatalf("legacy disclosure changed: %+v %v", legacy, err)
	}
}

func TestRefusalDiagnosticRetentionKeepsAnAlignedBoundedPrefix(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	r := Refusal{RequestID: uuid.NewString(), Operation: "compile", Scope: Scope{repo, "task", "run"}, ExplanationVersion: 1}
	ids := []string{}
	for range 1001 {
		id := uuid.NewString()
		ids = append(ids, id)
		r.Considered = append(r.Considered, RecordVersionRef{id, 1})
		r.Candidates = append(r.Candidates, CandidateEvaluation{RecordID: id, Version: 1, Reason: "EVALUATION_INCOMPLETE"})
	}
	err := op.retainRefusal(ctx, struct{ RequestID string }{r.RequestID}, r, failure("BUDGET_REFUSED", "synthetic retention-boundary observation"))
	var denied *Error
	if !errors.As(err, &denied) || denied.RefusalID == "" {
		t.Fatal("missing retained observation", err)
	}
	actual, err := op.Refusal(ctx, denied.RefusalID)
	if err != nil || actual.TraceComplete || actual.ConsideredCount != 1001 || len(actual.Considered) != 1000 || len(actual.Candidates) != 1000 {
		t.Fatalf("retained bound lost: %+v %v", actual, err)
	}
	slices.Sort(ids)
	for i := range actual.Considered {
		if actual.Considered[i].RecordID != ids[i] || actual.Candidates[i].RecordID != ids[i] {
			t.Fatal("diagnostic prefix diverges from considered references")
		}
	}
}
