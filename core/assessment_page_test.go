package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func TestAssessmentHistoryPagesBeyondWholeHistoryLimit(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "assessment-page"})
	repo := uuid.NewString()
	if _, err := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: projectNote(repo)}); err != nil {
		t.Fatal(err)
	}
	p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{Repo: repo, TaskID: "task", RunID: "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatal(err)
	}
	for version := 1; version <= 1001; version++ {
		a, err := s.AssessRun(ctx, AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, ExpectedVersion: version - 1, TaskOutcome: "unknown", FailureDomain: "unknown", Method: "page-fixture/1", Reason: "Retain this uncertain task review"})
		if err != nil || a.Version != version {
			t.Fatalf("assessment version %d: %+v %v", version, a, err)
		}
	}
	_, err = s.Assessments(ctx, p.ReceiptID)
	requireCode(t, err, "BUDGET_REFUSED")
	_, err = s.AssessmentHistory(ctx, p.ReceiptID, Destination{Name: "local", AllowLocal: true})
	requireCode(t, err, "BUDGET_REFUSED")

	after, seen := 0, 0
	for {
		page, err := s.AssessmentHistoryPage(ctx, p.ReceiptID, Destination{Name: "local", AllowLocal: true}, after, 127)
		if err != nil {
			t.Fatal(err)
		}
		if len(page.Assessments) == 0 || len(page.Assessments) > 127 {
			t.Fatalf("invalid page length %d after %d", len(page.Assessments), after)
		}
		for _, a := range page.Assessments {
			seen++
			if a.Version != seen || a.ReceiptID != p.ReceiptID {
				t.Fatalf("assessment %d at position %d", a.Version, seen)
			}
		}
		if page.NextAfterVersion != seen {
			t.Fatalf("cursor %d after version %d", page.NextAfterVersion, seen)
		}
		if !page.More {
			break
		}
		after = page.NextAfterVersion
	}
	if seen != 1001 {
		t.Fatalf("read %d of 1001 assessments", seen)
	}
	_, err = s.AssessmentHistoryPage(ctx, p.ReceiptID, Destination{Name: "hosted"}, 0, 127)
	requireCode(t, err, "AUTHORITY_DENIED")
	other := testStore(t, Channel{Principal: "assessment-page", Repo: uuid.NewString()})
	_, err = other.AssessmentHistoryPage(ctx, p.ReceiptID, Destination{Name: "local", AllowLocal: true}, 0, 127)
	requireCode(t, err, "AUTHORITY_DENIED")
}
