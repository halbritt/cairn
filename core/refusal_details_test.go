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
	if refusal.ExplanationVersion != 2 || refusal.TraceComplete || len(refusal.Candidates) != 2 || refusal.ConsideredCount != 2 {
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

func TestRefusalRetainsKnownInstructionGate(t *testing.T) {
	for _, mode := range []string{"", "index"} {
		for _, gate := range []string{"runtime", "context", "dispute"} {
			t.Run(mode+"/"+gate, func(t *testing.T) {
				ctx := context.Background()
				op, root := testOperator(t)
				repo := uuid.NewString()
				draft := projectNote(repo)
				draft.Kind = "instruction"
				draft.Sensitivity = "shareable"
				if gate == "context" {
					draft.Pins = &Applicability{TaskClass: "repair"}
				}
				issued, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: root.ID, Mandatory: true, RequiresRuntime: gate == "runtime", PolicyKey: "required-gate", Reason: "Retain known refusal gate"})
				if err != nil {
					t.Fatal(err)
				}
				if gate == "dispute" {
					other, err := op.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
					if err != nil {
						t.Fatal(err)
					}
					_, err = op.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{issued.RecordID, other.RecordID}, "Synthetic instruction dispute"})
					if err != nil {
						t.Fatal(err)
					}
				}
				req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000, Mode: mode}
				_, err = op.Compile(ctx, req, Destination{"hosted", false})
				wantCode, wantReason := "POLICY_UNENFORCEABLE", "POLICY_UNENFORCEABLE"
				if gate == "context" {
					wantReason = "CONTEXT_MISSING"
				}
				if gate == "dispute" {
					wantCode, wantReason = "OPEN_CONFLICT", "OPEN_CONFLICT"
				}
				requireCode(t, err, wantCode)
				var denied *Error
				if !errors.As(err, &denied) || denied.RefusalID == "" {
					t.Fatalf("missing refusal: %v", err)
				}
				refusal, err := op.Refusal(ctx, denied.RefusalID)
				if err != nil {
					t.Fatal(err)
				}
				var found *CandidateEvaluation
				for i := range refusal.Candidates {
					if refusal.Candidates[i].RecordID == issued.RecordID {
						found = &refusal.Candidates[i]
					}
				}
				if found == nil || !found.Mandatory || found.Reason != wantReason {
					t.Fatalf("known gate lost: want mandatory %s, got %+v", wantReason, found)
				}
				if refusal.TraceComplete {
					t.Fatal("aborted scan reported complete")
				}
				if mode == "" && gate == "runtime" {
					// A retry must preserve a previously retained v1 explanation,
					// even though current compilation now knows more diagnostics.
					legacy := refusal
					legacy.ExplanationVersion = 1
					legacy.Candidates[0].Reason = "EVALUATION_INCOMPLETE"
					legacy.Candidates[0].Mandatory = false
					if _, err := op.pool.Exec(ctx, `UPDATE cairn.refusal SET detail=$2 WHERE refusal_id=$1`, legacy.ID, legacy); err != nil {
						t.Fatal(err)
					}
					_, err = op.Compile(ctx, req, Destination{"hosted", false})
					var retried *Error
					if !errors.As(err, &retried) || retried.RefusalID != legacy.ID {
						t.Fatalf("legacy retry identity: %v", err)
					}
					stored, err := op.Refusal(ctx, legacy.ID)
					if err != nil || !reflect.DeepEqual(stored, legacy) {
						t.Fatalf("legacy explanation rewritten: %+v %v", stored, err)
					}
				}
			})
		}
	}
}

func TestRefusalStopsAtFirstDisputedPolicy(t *testing.T) {
	for _, mode := range []string{"", "index"} {
		t.Run(mode, func(t *testing.T) {
			ctx := context.Background()
			op, root := testOperator(t)
			repo := uuid.NewString()
			for _, body := range []string{"Use the first policy.", "Use the other policy."} {
				d := projectNote(repo)
				d.Kind = "instruction"
				d.Body = body
				d.Sensitivity = "shareable"
				if _, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: root.ID, PolicyKey: "same-policy", Mandatory: true, Reason: "Conflicting policy fixture"}); err != nil {
					t.Fatal(err)
				}
			}
			_, err := op.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", Mode: mode, AvailableTokens: 64000}, Destination{"hosted", false})
			requireCode(t, err, "OPEN_CONFLICT")
			var denied *Error
			if !errors.As(err, &denied) {
				t.Fatal(err)
			}
			refusal, err := op.Refusal(ctx, denied.RefusalID)
			if err != nil {
				t.Fatal(err)
			}
			if len(refusal.Candidates) != 1 || refusal.TraceComplete {
				t.Fatalf("refusal invented evaluation of later positions: %+v", refusal)
			}
			for _, e := range refusal.Candidates {
				if !e.Mandatory || e.Reason != "OPEN_CONFLICT" {
					t.Fatalf("conflicting position was not labelled: %+v", e)
				}
			}
		})
	}
}

func TestRefusalMarksMandatoryCategoryLimitInsteadOfSelection(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	rules := categoryRules()
	rules.InstructionLimits.Security.MaxTokens = 5
	if _, err := op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, GrantID: root.ID, Rules: rules, Reason: "Bound required category fixture"}); err != nil {
		t.Fatal(err)
	}
	d := projectNote(repo)
	d.Kind = "instruction"
	d.Body = "界界"
	d.Sensitivity = "shareable"
	if _, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: root.ID, Mandatory: true, Category: "security", PolicyKey: "category-limit", Reason: "Required category fixture"}); err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"", "index"} {
		_, err := op.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", Mode: mode, AvailableTokens: 64000}, Destination{"hosted", false})
		requireCode(t, err, "BUDGET_REFUSED")
		var denied *Error
		if !errors.As(err, &denied) {
			t.Fatal(err)
		}
		refusal, err := op.Refusal(ctx, denied.RefusalID)
		if err != nil {
			t.Fatal(err)
		}
		if len(refusal.Candidates) != 1 || !refusal.Candidates[0].Mandatory || refusal.Candidates[0].Reason != "CATEGORY_BUDGET" {
			t.Fatalf("known category refusal lost in %q: %+v", mode, refusal.Candidates)
		}
	}
}
