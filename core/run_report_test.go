package core

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestRunReportIncludesBaselineAndIncompleteRunsOnce(t *testing.T) {
	ctx := context.Background()
	repo := uuid.NewString()
	s := testStore(t, Channel{Principal: "run-report:" + repo, Repo: repo, Instrumented: true})
	compile := func() Package {
		t.Helper()
		p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", uuid.NewString()}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
		if err != nil {
			t.Fatal(err)
		}
		return p
	}
	baseline := compile()
	zero := 0
	if _, err := s.RecordOutcome(ctx, OutcomeRequest{RequestID: uuid.NewString(), ReceiptID: baseline.ReceiptID, ExitCode: &zero, ProcessState: "exited", DurationMS: 123, StdoutSHA256: strings.Repeat("a", 64), StderrSHA256: strings.Repeat("b", 64)}); err != nil {
		t.Fatal(err)
	}
	e := testEvidence(t, s, repo)
	for i, step := range []struct{ outcome, domain string }{{"accepted", "none"}, {"rejected", "task"}} {
		if _, err := s.AssessRun(ctx, AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: baseline.ReceiptID, ExpectedVersion: i, TaskOutcome: step.outcome, FailureDomain: step.domain, Method: "run-report-fixture/1", EvidenceIDs: []string{e.ID}, Reason: "Correct bounded task assessment"}); err != nil {
			t.Fatal(err)
		}
	}
	compile() // Pure retrieval without run observations is outside this report.
	for range 2 {
		draft := projectNote(repo)
		draft.Body += " Distinct fixture " + uuid.NewString()
		if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft}); err != nil {
			t.Fatal(err)
		}
	}
	incomplete := compile()
	if _, err := s.BindRun(ctx, RunBindingRequest{RequestID: uuid.NewString(), ReceiptID: incomplete.ReceiptID, TaskClass: "repair", BindingID: "host:fixture", CapabilityID: "repair:v1", CommandSHA256: strings.Repeat("c", 64), Revision: "revision-fixture"}); err != nil {
		t.Fatal(err)
	}
	if err := s.ClaimRun(ctx, incomplete.ReceiptID); err != nil {
		t.Fatal(err)
	}
	report, err := s.RunReport(ctx, RunReportRequest{Repo: repo, Limit: 1})
	if err != nil || len(report.Rows) != 1 || !report.More || report.NextOffset != 1 {
		t.Fatalf("first page: %+v %v", report, err)
	}
	row := report.Rows[0]
	if row.ReceiptID != baseline.ReceiptID || row.ExposureRows != 0 || !row.OutcomeObserved || row.LaunchClaimed || row.BindingObserved || row.ProcessState != "exited" || row.TaskOutcome != "rejected" || row.AssessmentVersion != 2 || row.AssessmentWitness != "instrumented" || row.DurationMS == nil || *row.DurationMS != 123 {
		t.Fatalf("baseline lost or multiplied: %+v", row)
	}
	next, err := s.RunReport(ctx, RunReportRequest{Repo: repo, Limit: 1, Offset: report.NextOffset})
	if err != nil || len(next.Rows) != 1 || next.More || next.NextOffset != 2 {
		t.Fatalf("second page: %+v %v", next, err)
	}
	row = next.Rows[0]
	if row.ReceiptID != incomplete.ReceiptID || row.ExposureRows != 2 || row.OutcomeObserved || !row.LaunchClaimed || !row.BindingObserved || row.ProcessState != "unknown" || row.TaskOutcome != "unknown" || row.ExitCode != nil || row.DurationMS != nil || row.TaskClass != "repair" || row.Revision != "revision-fixture" {
		t.Fatalf("launch claim fabricated completion: %+v", row)
	}
	_, err = s.RunReport(ctx, RunReportRequest{Repo: uuid.NewString(), Limit: 100})
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = s.RunReport(ctx, RunReportRequest{Repo: repo, Limit: 201})
	requireCode(t, err, "INVALID_REQUEST")
}
