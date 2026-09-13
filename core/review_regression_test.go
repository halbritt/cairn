package core

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestInspectionValidatesInputBeforeRepositoryScope(t *testing.T) {
	s := &Store{channel: Channel{Principal: "scoped", Repo: "allowed"}}
	calls := map[string]func(string) error{
		"runs": func(repo string) error {
			_, err := s.RunReport(context.Background(), RunReportRequest{Repo: repo, Limit: 100})
			return err
		},
		"uses": func(repo string) error {
			_, err := s.UseReport(context.Background(), UseReportRequest{Repo: repo, Limit: 100})
			return err
		},
		"conflicts": func(repo string) error {
			_, err := s.Conflicts(context.Background(), ConflictsRequest{Repo: repo, Limit: 100})
			return err
		},
		"proposal group": func(repo string) error {
			_, err := s.ProposalGroup(context.Background(), ProposalGroupRequest{Repo: repo, Key: strings.Repeat("a", 64), Limit: 100})
			return err
		},
		"list": func(repo string) error {
			_, err := s.List(context.Background(), ListRequest{Repo: repo, Limit: 100})
			return err
		},
	}
	for name, call := range calls {
		t.Run(name, func(t *testing.T) {
			requireCode(t, call(""), "INVALID_REQUEST")
			requireCode(t, call("other"), "AUTHORITY_DENIED")
		})
	}
}

func TestResolveMissingConflict(t *testing.T) {
	op, root := testOperator(t)
	_, err := op.Resolve(context.Background(), ResolveRequest{RequestID: uuid.NewString(), ConflictID: uuid.NewString(), ExpectedVersion: 1, GrantID: root.ID, Reason: "Resolve absent conflict fixture"})
	requireCode(t, err, "NOT_FOUND")
}

func TestRefusalRetainsCandidateTotal(t *testing.T) {
	s := testStore(t, Channel{Principal: "candidate-count-fixture"})
	for _, count := range []int{0, 1000, 1001} {
		r := Refusal{RequestID: uuid.NewString(), Operation: "compile", Scope: Scope{Repo: uuid.NewString(), TaskID: "t", RunID: "r"}, Candidates: make([]CandidateEvaluation, count)}
		for i := range r.Candidates {
			r.Candidates[i] = CandidateEvaluation{RecordID: uuid.NewString(), Version: 1}
		}
		err := s.retainRefusal(context.Background(), r.RequestID, r, failure("BUDGET_REFUSED", "candidate total fixture"))
		var refusalErr *Error
		if !errors.As(err, &refusalErr) || refusalErr.RefusalID == "" {
			t.Fatal(err)
		}
		retained, err := s.Refusal(context.Background(), refusalErr.RefusalID)
		if err != nil || retained.CandidatesCount == nil || *retained.CandidatesCount != count || len(retained.Candidates) != min(count, 1000) {
			t.Fatalf("candidate count %d: %+v %v", count, retained, err)
		}
	}
	var old Refusal
	if err := json.Unmarshal([]byte(`{"candidates":[]}`), &old); err != nil || old.CandidatesCount != nil {
		t.Fatalf("historical unknown count became zero: %+v %v", old, err)
	}
}
