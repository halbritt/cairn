package core

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestSummaryUsesLatestTaskAssessmentWithoutMemoryExposure(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "summary-assessment", Instrumented: true})
	repo := uuid.NewString()
	compile := func() Package {
		t.Helper()
		p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", uuid.NewString()}, Purpose: "context", AvailableTokens: 32000}, Destination{"local", true})
		if err != nil {
			t.Fatal(err)
		}
		return p
	}
	p := compile()
	zero := 0
	if _, err := s.RecordOutcome(ctx, OutcomeRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, ExitCode: &zero, ProcessState: "exited", StdoutSHA256: strings.Repeat("a", 64), StderrSHA256: strings.Repeat("b", 64)}); err != nil {
		t.Fatal(err)
	}
	failed := compile()
	if _, err := s.RecordOutcome(ctx, OutcomeRequest{RequestID: uuid.NewString(), ReceiptID: failed.ReceiptID, ProcessState: "launch_failed", StdoutSHA256: strings.Repeat("a", 64), StderrSHA256: strings.Repeat("b", 64)}); err != nil {
		t.Fatal(err)
	}
	compile() // A compile-only receipt does not add a process outcome.
	check := func(unknown int) {
		t.Helper()
		report, err := s.Report(ctx, repo)
		if err != nil {
			t.Fatal(err)
		}
		if report.Exposures != 0 || report.Outcomes != 2 || report.ExitZero != 1 || report.UnknownTaskOutcomes != unknown {
			t.Fatalf("summary must count current task assessments once per process outcome: %+v; want unknown=%d", report, unknown)
		}
	}
	check(1)
	e := testEvidence(t, s, repo)
	for i, step := range []struct {
		outcome string
		domain  string
		unknown int
	}{{"rejected", "task", 0}, {"accepted", "none", 0}, {"unknown", "unknown", 1}} {
		_, err := s.AssessRun(ctx, AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, ExpectedVersion: i, TaskOutcome: step.outcome, FailureDomain: step.domain, Method: "summary-fixture/1", EvidenceIDs: []string{e.ID}, Reason: "Revise the independent fixture assessment"})
		if err != nil {
			t.Fatal(err)
		}
		check(step.unknown)
	}
}
