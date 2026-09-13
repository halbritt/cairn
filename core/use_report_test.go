package core

import (
	"context"
	"github.com/google/uuid"
	"strings"
	"testing"
)

func TestUseReportKeepsObservedExpansionAlongsideReportedUsage(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "expansion-observation"})
	repo := uuid.NewString()
	note, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	other := projectNote(repo)
	other.Body = "Another independent lesson"
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), other}); err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}
	index, err := s.Index(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	var handle string
	for _, h := range index.Handles {
		if h.RecordID == note.RecordID {
			handle = h.Handle
		}
	}
	check := func(observed bool, signal, witness string) {
		t.Helper()
		report, err := s.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 100})
		if err != nil || len(report.Rows) != 2 {
			t.Fatalf("report: %+v %v", report, err)
		}
		for _, row := range report.Rows {
			if row.RecordID == note.RecordID {
				if row.ExpansionObserved != observed || row.Usage != signal || row.UsageWitness != witness {
					t.Fatalf("lost expansion observation or changed usage qualification: %+v", row)
				}
			} else if row.ExpansionObserved || row.Usage != "unknown" {
				t.Fatalf("observation leaked to another record: %+v", row)
			}
		}
	}
	check(false, "unknown", "unknown")
	reportUsage := func(signal string) {
		t.Helper()
		_, err := s.RecordUsage(ctx, UsageRequest{RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, RecordID: note.RecordID, Version: note.Version, Signal: signal})
		if err != nil {
			t.Fatal(err)
		}
	}
	reportUsage("expanded")
	check(false, "expanded", "testimony")
	pull := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, Handle: handle}
	if _, err := s.Expand(ctx, pull, Destination{"local", true}); err != nil {
		t.Fatal(err)
	}
	reportUsage("expanded")
	check(true, "expanded", "testimony")
	reportUsage("cited")
	check(true, "cited", "testimony")
	if _, err := s.Expand(ctx, pull, Destination{"local", true}); err != nil {
		t.Fatal(err)
	}
	check(true, "cited", "testimony")
	req.RequestID = uuid.NewString()
	req.Scope.RunID = "another-retrieval"
	if _, err := s.Index(ctx, req, Destination{"local", true}); err != nil {
		t.Fatal(err)
	}
	report, err := s.UseReport(ctx, UseReportRequest{Repo: repo, RunID: req.Scope.RunID, Limit: 100})
	if err != nil || len(report.Rows) != 2 {
		t.Fatalf("another retrieval: %+v %v", report, err)
	}
	for _, row := range report.Rows {
		if row.ExpansionObserved || row.Usage != "unknown" {
			t.Fatalf("observation leaked to another retrieval: %+v", row)
		}
	}
}

func TestUseReportJoinsWithoutMultiplyingObservations(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "use-report", Instrumented: true})
	repo := uuid.NewString()
	a, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	d := projectNote(repo)
	d.Body = "Another independent lesson"
	if _, err = s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "", Purpose: "context", AvailableTokens: 64000}
	p, err := s.Compile(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	for _, assurance := range []string{"unknown", "available", "delivered", "delivered"} {
		_, err = s.RecordDelivery(ctx, DeliveryRequest{uuid.NewString(), p.ReceiptID, "fixture", "stdin", assurance, strings.Repeat("a", 64), "Internal use not observed"})
		if err != nil {
			t.Fatal(err)
		}
	}
	for range 2 {
		_, err = s.RecordUsage(ctx, UsageRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, RecordID: a.RecordID, Version: a.Version, Signal: "cited"})
		if err != nil {
			t.Fatal(err)
		}
	}
	zero := 0
	_, err = s.RecordOutcome(ctx, OutcomeRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, ExitCode: &zero, DurationMS: 123, ProcessState: "exited", StdoutSHA256: strings.Repeat("b", 64), StderrSHA256: strings.Repeat("c", 64)})
	if err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	req.Scope.RunID = "unobserved"
	if _, err = s.Compile(ctx, req, Destination{"local", true}); err != nil {
		t.Fatal(err)
	}
	report, err := s.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 100})
	if err != nil {
		t.Fatal(err)
	}
	if len(report.Rows) != 4 {
		t.Fatalf("expected four exposure units, got %+v", report)
	}
	for _, row := range report.Rows {
		if row.ReceiptID == p.ReceiptID {
			if row.Delivery != "delivered" || row.TaskOutcome != "unknown" || row.DurationMS == nil || *row.DurationMS != 123 {
				t.Fatalf("lost outcome/coverage: %+v", row)
			}
			if row.RecordID == a.RecordID && row.Usage != "cited" {
				t.Fatal("citation missing")
			}
			if row.RecordID != a.RecordID && row.Usage != "unknown" {
				t.Fatal("invented absence of internal use")
			}
		} else if row.DurationMS != nil || row.Delivery != "unknown" || row.ProcessState != "unknown" {
			t.Fatalf("invented observations: %+v", row)
		}
	}
}

func TestRunAssessmentIsVersionedAndDoesNotInferCapabilityFromBinding(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "assessment", Instrumented: true})
	repo := uuid.NewString()
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)}); err != nil {
		t.Fatal(err)
	}
	p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "", Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	binding := RunBindingRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, TaskClass: "build", BindingID: "fixture:quota-binding", CapabilityID: "fixture:model", CommandSHA256: strings.Repeat("d", 64)}
	if _, err = s.BindRun(ctx, binding); err != nil {
		t.Fatal(err)
	}
	e := testEvidence(t, s, repo)
	req := AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, TaskOutcome: "rejected", FailureDomain: "binding", FailureKind: "quota", Method: "fixture-assessment/1", EvidenceIDs: []string{e.ID}, Reason: "Binding failure is not a capability result"}
	_, err = s.AssessRun(ctx, req)
	requireCode(t, err, "INVALID_REQUEST")
	req.TaskOutcome = "not_attempted"
	first, err := s.AssessRun(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	again, err := s.AssessRun(ctx, req)
	if err != nil || again.Version != first.Version {
		t.Fatal("assessment retry changed version")
	}
	req.RequestID = uuid.NewString()
	req.TaskOutcome = "accepted"
	req.FailureDomain = "none"
	req.FailureKind = ""
	req.Reason = "Later independent check accepted the task"
	_, err = s.AssessRun(ctx, req)
	requireCode(t, err, "VERSION_CONFLICT")
	req.ExpectedVersion = first.Version
	corrected, err := s.AssessRun(ctx, req)
	if err != nil || corrected.Version != 2 {
		t.Fatalf("correction: %+v %v", corrected, err)
	}
	report, err := s.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 100})
	if err != nil {
		t.Fatal(err)
	}
	row := report.Rows[0]
	if row.TaskOutcome != "accepted" || row.AssessmentVersion != 2 || row.TaskClass != "build" || row.CapabilityID != "fixture:model" || row.AssessmentWitness != "instrumented" {
		t.Fatalf("missing joined assessment: %+v", row)
	}
	history, err := s.Assessments(ctx, p.ReceiptID)
	if err != nil || len(history) != 2 || history[0].FailureDomain != "binding" {
		t.Fatalf("assessment history: %+v %v", history, err)
	}
}

func TestUsageInferenceAndCoverageRemainExplicit(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "usage-ladder", Instrumented: true})
	repo := uuid.NewString()
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "t", "r"}, Query: "", Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.RecordDelivery(ctx, DeliveryRequest{uuid.NewString(), p.ReceiptID, "fixture", "stdin", "delivered", strings.Repeat("a", 64), "instrumented fixture"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.RecordUsageCoverage(ctx, UsageCoverageRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, Coverage: "complete", Method: "fixture-observer/1"}); err != nil {
		t.Fatal(err)
	}
	report, err := s.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 100})
	if err != nil || report.Rows[0].Usage != "delivered_only" {
		t.Fatalf("coverage: %+v %v", report, err)
	}
	req := UsageRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, RecordID: r.RecordID, Version: r.Version, Signal: "behaviorally_implicated"}
	_, err = s.RecordUsage(ctx, req)
	requireCode(t, err, "INVALID_REQUEST")
	req.Method = "selected-diff-match/1"
	if _, err = s.RecordUsage(ctx, req); err != nil {
		t.Fatal(err)
	}
	report, err = s.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 100})
	if err != nil || report.Rows[0].Usage != "behaviorally_implicated" || report.Rows[0].UsageWitness != "inferred" || report.Rows[0].UsageMethod != req.Method {
		t.Fatalf("inference qualification: %+v %v", report, err)
	}
	req.RequestID = uuid.NewString()
	req.Signal = "cited"
	req.Method = ""
	if _, err = s.RecordUsage(ctx, req); err != nil {
		t.Fatal(err)
	}
	report, err = s.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 100})
	if err != nil || report.Rows[0].Usage != "cited" || report.Rows[0].UsageWitness != "testimony" {
		t.Fatalf("ladder: %+v %v", report, err)
	}
}
