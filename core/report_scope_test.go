package core

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestUseReportFiltersTaskAndRunBeforePagination(t *testing.T) {
	ctx := context.Background()
	repo := uuid.NewString()
	s := testStore(t, Channel{Principal: "report-scope:" + repo, Repo: repo})
	note, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	const task = "maintenance %_' α"
	var receipts []string
	for _, scope := range []Scope{{repo, "other", "first"}, {repo, task, "first"}, {repo, "maintenance other α", "second"}, {repo, task, "second"}} {
		p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: scope, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
		if err != nil {
			t.Fatal(err)
		}
		receipts = append(receipts, p.ReceiptID)
	}
	// Decode the public request shape so this test also exercises the JSON names.
	read := func(taskID, runID string, offset int) UseReport {
		t.Helper()
		raw, err := json.Marshal(map[string]any{"repo": repo, "record_id": note.RecordID, "task_id": taskID, "run_id": runID, "limit": 1, "offset": offset})
		if err != nil {
			t.Fatal(err)
		}
		var req UseReportRequest
		if err := json.Unmarshal(raw, &req); err != nil {
			t.Fatal(err)
		}
		report, err := s.UseReport(ctx, req)
		if err != nil {
			t.Fatal(err)
		}
		return report
	}
	first := read(task, "", 0)
	if len(first.Rows) != 1 || first.Rows[0].ReceiptID != receipts[1] || !first.More || first.NextOffset != 1 {
		t.Fatalf("task filter did not precede pagination: %+v", first)
	}
	second := read(task, "", first.NextOffset)
	if len(second.Rows) != 1 || second.Rows[0].ReceiptID != receipts[3] || second.More {
		t.Fatalf("task continuation lost matching exposure: %+v", second)
	}
	if report := read(task, "second", 0); len(report.Rows) != 1 || report.Rows[0].ReceiptID != receipts[3] || report.More {
		t.Fatalf("task/run conjunction: %+v", report)
	}
	if report := read("", "first", 0); len(report.Rows) != 1 || report.Rows[0].ReceiptID != receipts[0] || !report.More {
		t.Fatalf("run-only filter: %+v", report)
	}
	if report := read(task, "absent", 0); len(report.Rows) != 0 || report.More || report.NextOffset != 0 {
		t.Fatalf("invented matching exposure: %+v", report)
	}
}

func TestRunReportFiltersTaskAndRunBeforePagination(t *testing.T) {
	ctx := context.Background()
	repo := uuid.NewString()
	s := testStore(t, Channel{Principal: "run-scope:" + repo, Repo: repo, Instrumented: true})
	var receipts []string
	for _, scope := range []Scope{{repo, "other", "first"}, {repo, "task", "first"}, {repo, "other", "second"}, {repo, "task", "second"}, {repo, "task", "retrieval-only"}} {
		p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: scope, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
		if err != nil {
			t.Fatal(err)
		}
		receipts = append(receipts, p.ReceiptID)
		if scope.RunID != "retrieval-only" {
			zero := 0
			if _, err := s.RecordOutcome(ctx, OutcomeRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, ExitCode: &zero, ProcessState: "exited", StdoutSHA256: strings.Repeat("a", 64), StderrSHA256: strings.Repeat("b", 64)}); err != nil {
				t.Fatal(err)
			}
		}
	}
	read := func(taskID, runID string, offset int) RunReport {
		t.Helper()
		raw, err := json.Marshal(map[string]any{"repo": repo, "task_id": taskID, "run_id": runID, "policy_revision": "local-loop/1", "limit": 1, "offset": offset})
		if err != nil {
			t.Fatal(err)
		}
		var req RunReportRequest
		if err := json.Unmarshal(raw, &req); err != nil {
			t.Fatal(err)
		}
		report, err := s.RunReport(ctx, req)
		if err != nil {
			t.Fatal(err)
		}
		return report
	}
	first := read("task", "", 0)
	if len(first.Rows) != 1 || first.Rows[0].ReceiptID != receipts[1] || !first.More || first.NextOffset != 1 {
		t.Fatalf("task filter did not precede pagination: %+v", first)
	}
	second := read("task", "", first.NextOffset)
	if len(second.Rows) != 1 || second.Rows[0].ReceiptID != receipts[3] || second.More || second.Rows[0].TaskOutcome != "unknown" || second.Rows[0].ExposureRows != 0 {
		t.Fatalf("filtered baseline population: %+v", second)
	}
	if report := read("task", "second", 0); len(report.Rows) != 1 || report.Rows[0].ReceiptID != receipts[3] || report.More {
		t.Fatalf("task/run conjunction: %+v", report)
	}
	if report := read("", "first", 0); len(report.Rows) != 1 || report.Rows[0].ReceiptID != receipts[0] || !report.More {
		t.Fatalf("run-only filter: %+v", report)
	}
	if report := read("task", "retrieval-only", 0); len(report.Rows) != 0 || report.More || report.NextOffset != 0 {
		t.Fatalf("pure retrieval became a run: %+v", report)
	}
}
