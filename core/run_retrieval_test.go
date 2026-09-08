package core

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestDynamicRetrievalJoinsHostOutcomeWithoutAnotherExecution(t *testing.T) {
	ctx := context.Background()
	scope := Scope{Repo: uuid.NewString(), TaskID: "task", RunID: "run"}
	host := testStore(t, Channel{Principal: "host:" + scope.Repo, Instrumented: true})
	agent := testStore(t, Channel{Principal: "agent:" + scope.Repo})
	record, err := agent.Create(ctx, CreateRequest{uuid.NewString(), projectNote(scope.Repo)})
	if err != nil {
		t.Fatal(err)
	}
	attempt := uuid.NewString()
	if _, err = host.RecordSpawn(ctx, SpawnRequest{uuid.NewString(), attempt, "host", "agent", scope}); err != nil {
		t.Fatal(err)
	}
	run, err := host.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: scope,
		Query: "bootstrapmarker", Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = host.BindRun(ctx, RunBindingRequest{RequestID: uuid.NewString(), ReceiptID: run.ReceiptID,
		AttemptID: attempt, TaskClass: "repair", BindingID: "fixture:host", CapabilityID: "fixture:model",
		CommandSHA256: strings.Repeat("a", 64)}); err != nil {
		t.Fatal(err)
	}
	if err = host.ClaimRun(ctx, run.ReceiptID); err != nil {
		t.Fatal(err)
	}
	retrieval, err := agent.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: scope,
		Query: "fixture_error", Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = agent.Expand(ctx, ExpandRequest{RequestID: uuid.NewString(), ReceiptID: retrieval.Package.ReceiptID,
		Handle: retrieval.Handles[0].Handle}, Destination{"local", true}); err != nil {
		t.Fatal(err)
	}
	zero := 0
	if _, err = host.RecordOutcome(ctx, OutcomeRequest{RequestID: uuid.NewString(), ReceiptID: run.ReceiptID,
		ProcessState: "exited", ExitCode: &zero, DurationMS: 123, StdoutSHA256: strings.Repeat("b", 64), StderrSHA256: strings.Repeat("c", 64)}); err != nil {
		t.Fatal(err)
	}
	if _, err = host.RecordTerminal(ctx, TerminalRequest{uuid.NewString(), attempt, "completed", "fixture:result"}); err != nil {
		t.Fatal(err)
	}
	before, err := host.UseReport(ctx, UseReportRequest{Repo: scope.Repo, Limit: 100})
	if err != nil || len(before.Rows) != 1 || before.Rows[0].ProcessState != "unknown" {
		t.Fatalf("unassociated retrieval inferred host outcome: %+v %v", before, err)
	}
	request := RunRetrievalRequest{RequestID: uuid.NewString(), RunReceiptID: run.ReceiptID,
		RetrievalReceiptID: retrieval.Package.ReceiptID, ExpectedReader: agent.channel.Principal, Method: "fixture-tool-observer/1"}
	linked, err := host.LinkRunRetrieval(ctx, request)
	if err != nil {
		t.Fatal(err)
	}
	again, err := host.LinkRunRetrieval(ctx, request)
	if err != nil || again.RunReceiptID != linked.RunReceiptID || !again.ObservedAt.Equal(linked.ObservedAt) || linked.Observer != host.channel.Principal || linked.Reader != agent.channel.Principal {
		t.Fatalf("association identity/retry: %+v %v", again, err)
	}
	check := func(outcome string, version int) {
		t.Helper()
		report, err := host.UseReport(ctx, UseReportRequest{Repo: scope.Repo, Limit: 100})
		if err != nil || len(report.Rows) != 1 {
			t.Fatalf("exposure population: %+v %v", report, err)
		}
		row := report.Rows[0]
		if row.ReceiptID != retrieval.Package.ReceiptID || row.RunReceiptID != run.ReceiptID || row.RecordID != record.RecordID ||
			row.ProcessState != "exited" || row.DurationMS == nil || *row.DurationMS != 123 || row.TaskOutcome != outcome ||
			row.AssessmentVersion != version || row.Usage != "expanded" || row.TaskClass != "repair" {
			t.Fatalf("lost source or host outcome: %+v", row)
		}
		runs, err := host.RunReport(ctx, RunReportRequest{Repo: scope.Repo, Limit: 100})
		if err != nil || len(runs.Rows) != 1 || runs.Rows[0].ReceiptID != run.ReceiptID || runs.Rows[0].ExposureRows != 1 || runs.Rows[0].LinkedRetrievals != 1 {
			t.Fatalf("retrieval counted as another execution or exposure lost: %+v %v", runs, err)
		}
	}
	check("unknown", 0) // Exit zero must not become task acceptance.
	evidence := testEvidence(t, host, scope.Repo)
	assessment := AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: run.ReceiptID, TaskOutcome: "accepted",
		FailureDomain: "none", Method: "fixture-gate/1", EvidenceIDs: []string{evidence.ID}, Reason: "Explicit fixture task gate passed"}
	if _, err = host.AssessRun(ctx, assessment); err != nil {
		t.Fatal(err)
	}
	check("accepted", 1)
	assessment.RequestID, assessment.ExpectedVersion = uuid.NewString(), 1
	assessment.TaskOutcome, assessment.FailureDomain = "rejected", "task"
	if _, err = host.AssessRun(ctx, assessment); err != nil {
		t.Fatal(err)
	}
	check("rejected", 2)
	// The reader can assess its own retrieval, but cannot replace the host's
	// assessment in the joined report or write to the host receipt.
	assessment.RequestID, assessment.ReceiptID, assessment.ExpectedVersion = uuid.NewString(), retrieval.Package.ReceiptID, 0
	assessment.TaskOutcome, assessment.FailureDomain = "accepted", "none"
	if _, err = agent.AssessRun(ctx, assessment); err != nil {
		t.Fatal(err)
	}
	check("rejected", 2)
	assessment.RequestID, assessment.ReceiptID = uuid.NewString(), run.ReceiptID
	_, err = agent.AssessRun(ctx, assessment)
	requireCode(t, err, "AUTHORITY_DENIED")
}

func retrievalRun(t *testing.T, host *Store, scope Scope) (string, string) {
	t.Helper()
	ctx := context.Background()
	attempt := uuid.NewString()
	if _, err := host.RecordSpawn(ctx, SpawnRequest{uuid.NewString(), attempt, "fixture:host", "fixture:agent", scope}); err != nil {
		t.Fatal(err)
	}
	run := retrievalReceipt(t, host, scope, Destination{"local", true})
	if _, err := host.BindRun(ctx, retrievalBinding(run, attempt)); err != nil {
		t.Fatal(err)
	}
	if err := host.ClaimRun(ctx, run); err != nil {
		t.Fatal(err)
	}
	return run, attempt
}

func retrievalReceipt(t *testing.T, reader *Store, scope Scope, dest Destination) string {
	t.Helper()
	p, err := reader.Compile(context.Background(), CompileRequest{RequestID: uuid.NewString(), Scope: scope,
		Query: "bootstrapmarker", Purpose: "context", AvailableTokens: 64000}, dest)
	if err != nil {
		t.Fatal(err)
	}
	return p.ReceiptID
}

func retrievalBinding(receipt, attempt string) RunBindingRequest {
	return RunBindingRequest{RequestID: uuid.NewString(), ReceiptID: receipt, AttemptID: attempt,
		TaskClass: "fixture", BindingID: "fixture:host", CapabilityID: "fixture:model", CommandSHA256: strings.Repeat("a", 64)}
}

func retrievalOutcome(receipt string) OutcomeRequest {
	zero := 0
	return OutcomeRequest{RequestID: uuid.NewString(), ReceiptID: receipt, ProcessState: "exited", ExitCode: &zero,
		StdoutSHA256: strings.Repeat("b", 64), StderrSHA256: strings.Repeat("c", 64)}
}

func TestRunRetrievalChecksObserverReaderScopeAndInterval(t *testing.T) {
	ctx := context.Background()
	scope := Scope{uuid.NewString(), "task", "run"}
	host := testStore(t, Channel{Principal: "host:" + scope.Repo, Instrumented: true})
	agent := testStore(t, Channel{Principal: "agent:" + scope.Repo})
	foreign := testStore(t, Channel{Principal: "foreign:" + scope.Repo, Instrumented: true})
	early := retrievalReceipt(t, agent, scope, Destination{"local", true})
	run, attempt := retrievalRun(t, host, scope)
	source := retrievalReceipt(t, agent, scope, Destination{"local", true})
	valid := RunRetrievalRequest{uuid.NewString(), run, source, agent.channel.Principal, "fixture:observed-tool-response"}
	for _, caller := range []*Store{agent, foreign} {
		_, err := caller.LinkRunRetrieval(ctx, valid)
		requireCode(t, err, "AUTHORITY_DENIED")
	}
	for _, tc := range []struct {
		name, code string
		change     func(*RunRetrievalRequest)
	}{
		{"wrong reader", "AUTHORITY_DENIED", func(r *RunRetrievalRequest) { r.ExpectedReader = "other" }},
		{"before run", "INVALID_REQUEST", func(r *RunRetrievalRequest) { r.RetrievalReceiptID = early }},
		{"missing source", "NOT_FOUND", func(r *RunRetrievalRequest) { r.RetrievalReceiptID = uuid.NewString() }},
		{"same receipt", "INVALID_REQUEST", func(r *RunRetrievalRequest) { r.RetrievalReceiptID = r.RunReceiptID }},
		{"different task", "AUTHORITY_DENIED", func(r *RunRetrievalRequest) {
			s := scope
			s.TaskID = "other"
			r.RetrievalReceiptID = retrievalReceipt(t, agent, s, Destination{"local", true})
		}},
		{"different run", "AUTHORITY_DENIED", func(r *RunRetrievalRequest) {
			s := scope
			s.RunID = "other"
			r.RetrievalReceiptID = retrievalReceipt(t, agent, s, Destination{"local", true})
		}},
		{"different repo", "AUTHORITY_DENIED", func(r *RunRetrievalRequest) {
			s := scope
			s.Repo = uuid.NewString()
			r.RetrievalReceiptID = retrievalReceipt(t, agent, s, Destination{"local", true})
		}},
		{"different destination", "AUTHORITY_DENIED", func(r *RunRetrievalRequest) {
			r.RetrievalReceiptID = retrievalReceipt(t, agent, scope, Destination{"hosted", false})
		}},
		{"unobserved host", "INVALID_REQUEST", func(r *RunRetrievalRequest) {
			r.RunReceiptID = retrievalReceipt(t, host, scope, Destination{"local", true})
			if err := host.ClaimRun(ctx, r.RunReceiptID); err != nil {
				t.Fatal(err)
			}
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			req := valid
			req.RequestID = uuid.NewString()
			tc.change(&req)
			_, err := host.LinkRunRetrieval(ctx, req)
			requireCode(t, err, tc.code)
		})
	}
	if _, err := host.RecordOutcome(ctx, retrievalOutcome(run)); err != nil {
		t.Fatal(err)
	}
	afterOutcome := retrievalReceipt(t, agent, scope, Destination{"local", true})
	late := valid
	late.RequestID, late.RetrievalReceiptID = uuid.NewString(), afterOutcome
	_, err := host.LinkRunRetrieval(ctx, late)
	requireCode(t, err, "INVALID_REQUEST")
	if _, err = host.RecordTerminal(ctx, TerminalRequest{uuid.NewString(), attempt, "completed", "fixture:result"}); err != nil {
		t.Fatal(err)
	}
	if _, err = host.LinkRunRetrieval(ctx, valid); err != nil {
		t.Fatal(err) // Delayed observation of a retrieval made during execution is valid.
	}
	changed := valid
	changed.Method = "changed intent"
	_, err = host.LinkRunRetrieval(ctx, changed)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	changed.RequestID = uuid.NewString()
	_, err = host.LinkRunRetrieval(ctx, changed)
	requireCode(t, err, "VERSION_CONFLICT")
	restricted := testStore(t, Channel{Principal: host.channel.Principal, Instrumented: true, Repo: "other"})
	_, err = restricted.LinkRunRetrieval(ctx, valid)
	requireCode(t, err, "AUTHORITY_DENIED") // Live access is checked before a cached retry.
}

func TestLinkedRetrievalCannotBecomeAnExecution(t *testing.T) {
	ctx := context.Background()
	scope := Scope{uuid.NewString(), "task", "run"}
	host := testStore(t, Channel{Principal: "host:" + scope.Repo, Instrumented: true})
	run, _ := retrievalRun(t, host, scope)
	source := retrievalReceipt(t, host, scope, Destination{"local", true})
	if _, err := host.LinkRunRetrieval(ctx, RunRetrievalRequest{uuid.NewString(), run, source, host.channel.Principal, "fixture:tool"}); err != nil {
		t.Fatal(err)
	}
	_, err := host.BindRun(ctx, retrievalBinding(source, ""))
	requireCode(t, err, "INVALID_REQUEST")
	requireCode(t, host.ClaimRun(ctx, source), "INVALID_REQUEST")
	_, err = host.RecordOutcome(ctx, retrievalOutcome(source))
	requireCode(t, err, "INVALID_REQUEST")
}

func TestRunRetrievalNeedsExecutionAndHonorsTerminalWithoutOutcome(t *testing.T) {
	ctx := context.Background()
	scope := Scope{uuid.NewString(), "task", "run"}
	host := testStore(t, Channel{Principal: "host:" + scope.Repo, Instrumented: true})
	agent := testStore(t, Channel{Principal: "agent:" + scope.Repo})
	attempt := uuid.NewString()
	if _, err := host.RecordSpawn(ctx, SpawnRequest{uuid.NewString(), attempt, "host", "agent", scope}); err != nil {
		t.Fatal(err)
	}
	run := retrievalReceipt(t, host, scope, Destination{"local", true})
	if _, err := host.BindRun(ctx, retrievalBinding(run, attempt)); err != nil {
		t.Fatal(err)
	}
	source := retrievalReceipt(t, agent, scope, Destination{"local", true})
	request := RunRetrievalRequest{uuid.NewString(), run, source, agent.channel.Principal, "fixture:tool"}
	_, err := host.LinkRunRetrieval(ctx, request)
	requireCode(t, err, "INVALID_REQUEST") // Prepared binding alone is not an execution.
	if _, err = host.RecordTerminal(ctx, TerminalRequest{uuid.NewString(), attempt, "completed", "fixture:result"}); err != nil {
		t.Fatal(err)
	}
	late := retrievalReceipt(t, agent, scope, Destination{"local", true})
	// Externally wrapped executions can report an outcome without a Cairn claim.
	if _, err = host.RecordOutcome(ctx, retrievalOutcome(run)); err != nil {
		t.Fatal(err)
	}
	if _, err = host.LinkRunRetrieval(ctx, request); err != nil {
		t.Fatal(err)
	}
	request.RequestID, request.RetrievalReceiptID = uuid.NewString(), late
	_, err = host.LinkRunRetrieval(ctx, request)
	requireCode(t, err, "INVALID_REQUEST") // Terminal bounds the interval even before outcome arrived.
}

func TestRunRetrievalHasOneHostEvenWithConcurrentObservers(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	scope := Scope{uuid.NewString(), "task", "run"}
	host := testStore(t, Channel{Principal: "host:" + scope.Repo, Instrumented: true})
	agent := testStore(t, Channel{Principal: "agent:" + scope.Repo})
	first, _ := retrievalRun(t, host, scope)
	second, _ := retrievalRun(t, host, scope)
	source := retrievalReceipt(t, agent, scope, Destination{"local", true})
	start := make(chan struct{})
	results := make(chan error, 2)
	for _, run := range []string{first, second} {
		go func() {
			<-start
			_, err := host.LinkRunRetrieval(ctx, RunRetrievalRequest{uuid.NewString(), run, source, agent.channel.Principal, "fixture:tool"})
			results <- err
		}()
	}
	close(start)
	a, b := <-results, <-results
	if (a == nil) == (b == nil) {
		t.Fatalf("expected one association: %v %v", a, b)
	}
	if a != nil {
		requireCode(t, a, "VERSION_CONFLICT")
	} else {
		requireCode(t, b, "VERSION_CONFLICT")
	}
	report, err := host.RunReport(ctx, RunReportRequest{Repo: scope.Repo, Limit: 100})
	if err != nil || len(report.Rows) != 2 || report.Rows[0].LinkedRetrievals+report.Rows[1].LinkedRetrievals != 1 {
		t.Fatalf("source association multiplied: %+v %v", report, err)
	}
}

func TestRetrievalAssociationRacesExecutionWithoutDualRoles(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	scope := Scope{uuid.NewString(), "task", "run"}
	host := testStore(t, Channel{Principal: "host:" + scope.Repo, Instrumented: true})
	run, _ := retrievalRun(t, host, scope)
	for _, operation := range []string{"bind", "claim", "outcome"} {
		t.Run(operation, func(t *testing.T) {
			for i := 0; i < 8; i++ {
				source := retrievalReceipt(t, host, scope, Destination{"local", true})
				start := make(chan struct{})
				linkResult, executionResult := make(chan error, 1), make(chan error, 1)
				go func() {
					<-start
					_, err := host.LinkRunRetrieval(ctx, RunRetrievalRequest{uuid.NewString(), run, source, host.channel.Principal, "fixture:tool"})
					linkResult <- err
				}()
				go func() {
					<-start
					var err error
					switch operation {
					case "bind":
						_, err = host.BindRun(ctx, retrievalBinding(source, ""))
					case "claim":
						err = host.ClaimRun(ctx, source)
					case "outcome":
						_, err = host.RecordOutcome(ctx, retrievalOutcome(source))
					}
					executionResult <- err
				}()
				close(start)
				linkErr, executeErr := <-linkResult, <-executionResult
				if (linkErr == nil) == (executeErr == nil) {
					t.Fatalf("expected one role: link=%v execute=%v", linkErr, executeErr)
				}
				if linkErr != nil {
					requireCode(t, linkErr, "INVALID_REQUEST")
				} else {
					requireCode(t, executeErr, "INVALID_REQUEST")
				}
			}
		})
	}
}
