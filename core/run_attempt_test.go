package core

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestRunBindingLinksExactObservedHostAttempt(t *testing.T) {
	s := testStore(t, Channel{Principal: "host:attempt-link", Instrumented: true})
	ctx := context.Background()
	scope := Scope{Repo: uuid.NewString(), TaskID: "task", RunID: "run"}
	attempt := SpawnRequest{RequestID: uuid.NewString(), AttemptID: uuid.NewString(), Dispatcher: "dispatcher", Delegate: "delegate", Scope: scope}
	if _, err := s.RecordSpawn(ctx, attempt); err != nil {
		t.Fatal(err)
	}
	compile := func() Package {
		t.Helper()
		p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: scope, Purpose: "context", AvailableTokens: 32000}, Destination{Name: "local", AllowLocal: true})
		if err != nil {
			t.Fatal(err)
		}
		return p
	}
	p := compile()
	bind := RunBindingRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, AttemptID: attempt.AttemptID, TaskClass: "task", BindingID: "binding", CapabilityID: "unknown", CommandSHA256: strings.Repeat("0", 64)}
	if _, err := s.BindRun(ctx, bind); err != nil {
		t.Fatal(err)
	}
	if _, err := s.BindRun(ctx, bind); err != nil {
		t.Fatal(err)
	}
	// Preparing another receipt does not consume the host execution reservation.
	other := compile()
	duplicate := bind
	duplicate.RequestID = uuid.NewString()
	duplicate.ReceiptID = other.ReceiptID
	_, err := s.BindRun(ctx, duplicate)
	if err != nil {
		t.Fatal(err)
	}
	if err = s.ClaimRun(ctx, p.ReceiptID); err != nil {
		t.Fatal(err)
	}
	requireCode(t, s.ClaimRun(ctx, other.ReceiptID), "RUN_ALREADY_STARTED")
	report, err := s.RunReport(ctx, RunReportRequest{Repo: scope.Repo, Limit: 10})
	if err != nil || len(report.Rows) != 1 || report.Rows[0].AttemptID != attempt.AttemptID {
		t.Fatalf("exact attempt lost from run report: %+v %v", report, err)
	}
	// Process observation does not close or complete the native host attempt.
	zero := 0
	_, err = s.RecordOutcome(ctx, OutcomeRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, ProcessState: "exited", ExitCode: &zero, StdoutSHA256: strings.Repeat("0", 64), StderrSHA256: strings.Repeat("0", 64)})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.RecordTerminal(ctx, TerminalRequest{RequestID: uuid.NewString(), AttemptID: attempt.AttemptID, State: "completed", ResultRef: "host:actual-result"}); err != nil {
		t.Fatalf("process result manufactured host terminal: %v", err)
	}
}

func TestAttemptBindingRejectsForeignScopeAndClosedLaunch(t *testing.T) {
	s := testStore(t, Channel{Principal: "host:attempt-boundary", Instrumented: true})
	ctx := context.Background()
	scope := Scope{Repo: uuid.NewString(), TaskID: "task", RunID: "run"}
	p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: scope, Purpose: "context", AvailableTokens: 32000}, Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatal(err)
	}
	bind := RunBindingRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, TaskClass: "task", BindingID: "binding", CapabilityID: "unknown", CommandSHA256: strings.Repeat("0", 64)}
	for _, v := range []struct {
		name, owner string
		scope       Scope
	}{
		{"observer", "host:other", scope},
		{"repo", s.channel.Principal, Scope{Repo: uuid.NewString(), TaskID: scope.TaskID, RunID: scope.RunID}},
		{"task", s.channel.Principal, Scope{Repo: scope.Repo, TaskID: "other", RunID: scope.RunID}},
		{"run", s.channel.Principal, Scope{Repo: scope.Repo, TaskID: scope.TaskID, RunID: "other"}},
		{"wildcard-task", s.channel.Principal, Scope{Repo: scope.Repo, TaskID: "*", RunID: scope.RunID}},
		{"wildcard-run", s.channel.Principal, Scope{Repo: scope.Repo, TaskID: scope.TaskID, RunID: "*"}},
	} {
		t.Run(v.name, func(t *testing.T) {
			owner := testStore(t, Channel{Principal: v.owner, Instrumented: true})
			id := uuid.NewString()
			_, err := owner.RecordSpawn(ctx, SpawnRequest{RequestID: uuid.NewString(), AttemptID: id, Dispatcher: "dispatcher", Delegate: "delegate", Scope: v.scope})
			if err != nil {
				t.Fatal(err)
			}
			request := bind
			request.RequestID = uuid.NewString()
			request.AttemptID = id
			_, err = s.BindRun(ctx, request)
			requireCode(t, err, "AUTHORITY_DENIED")
		})
	}
	for _, id := range []string{uuid.NewString(), "malformed"} {
		request := bind
		request.RequestID = uuid.NewString()
		request.AttemptID = id
		_, err = s.BindRun(ctx, request)
		expected := "NOT_FOUND"
		if id == "malformed" {
			expected = "INVALID_REQUEST"
		}
		requireCode(t, err, expected)
	}
	bind.AttemptID = uuid.NewString()
	_, err = s.RecordSpawn(ctx, SpawnRequest{RequestID: uuid.NewString(), AttemptID: bind.AttemptID, Dispatcher: "dispatcher", Delegate: "delegate", Scope: scope})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.BindRun(ctx, bind); err != nil {
		t.Fatal(err)
	}
	_, err = s.RecordTerminal(ctx, TerminalRequest{RequestID: uuid.NewString(), AttemptID: bind.AttemptID, State: "completed", ResultRef: "host:actual-result"})
	if err != nil {
		t.Fatal(err)
	}
	// Both original binding retries and claims check the current host state.
	_, err = s.BindRun(ctx, bind)
	requireCode(t, err, "ATTEMPT_TERMINAL")
	requireCode(t, s.ClaimRun(ctx, p.ReceiptID), "ATTEMPT_TERMINAL")
	status, err := s.RunStatus(ctx, p.ReceiptID)
	if err != nil || status.LaunchClaimed || !status.BindingObserved {
		t.Fatalf("closed attempt consumed launch: %+v %v", status, err)
	}
}

func TestHostTerminalRacesLinkedLaunchClaim(t *testing.T) {
	s := testStore(t, Channel{Principal: "host:attempt-race", Instrumented: true})
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	for i := 0; i < 12; i++ {
		scope := Scope{Repo: uuid.NewString(), TaskID: "task", RunID: "run"}
		attempt := uuid.NewString()
		if _, err := s.RecordSpawn(ctx, SpawnRequest{RequestID: uuid.NewString(), AttemptID: attempt, Dispatcher: "dispatcher", Delegate: "delegate", Scope: scope}); err != nil {
			t.Fatal(err)
		}
		p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: scope, Purpose: "context", AvailableTokens: 32000}, Destination{Name: "local", AllowLocal: true})
		if err != nil {
			t.Fatal(err)
		}
		_, err = s.BindRun(ctx, RunBindingRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, AttemptID: attempt, TaskClass: "task", BindingID: "binding", CapabilityID: "unknown", CommandSHA256: strings.Repeat("0", 64)})
		if err != nil {
			t.Fatal(err)
		}
		start := make(chan struct{})
		claimDone := make(chan error, 1)
		terminalDone := make(chan error, 1)
		go func() { <-start; claimDone <- s.ClaimRun(ctx, p.ReceiptID) }()
		go func() {
			<-start
			_, err := s.RecordTerminal(ctx, TerminalRequest{RequestID: uuid.NewString(), AttemptID: attempt, State: "completed", ResultRef: "host:result"})
			terminalDone <- err
		}()
		close(start)
		claimErr, terminalErr := <-claimDone, <-terminalDone
		if terminalErr != nil {
			t.Fatal(terminalErr)
		}
		if claimErr != nil && Code(claimErr) != "ATTEMPT_TERMINAL" {
			t.Fatalf("unexpected concurrent result: %v", claimErr)
		}
		// Either the claim preceded terminal commit, or it was refused. Once terminal
		// is committed, no later claim may authorize execution.
		requireCode(t, s.ClaimRun(ctx, p.ReceiptID), "ATTEMPT_TERMINAL")
		status, err := s.RunStatus(ctx, p.ReceiptID)
		if err != nil || status.LaunchClaimed != (claimErr == nil) {
			t.Fatalf("claim result and retained observation disagree: %+v %v", status, err)
		}
	}
}

func TestPreparedReceiptsRaceForOneHostExecution(t *testing.T) {
	s := testStore(t, Channel{Principal: "host:prepared-race", Instrumented: true})
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	scope := Scope{Repo: uuid.NewString(), TaskID: "task", RunID: "run"}
	attempt := uuid.NewString()
	if _, err := s.RecordSpawn(ctx, SpawnRequest{RequestID: uuid.NewString(), AttemptID: attempt, Dispatcher: "dispatcher", Delegate: "delegate", Scope: scope}); err != nil {
		t.Fatal(err)
	}
	var receipts []string
	for i := 0; i < 2; i++ {
		p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: scope, Purpose: "context", AvailableTokens: 32000}, Destination{Name: "local", AllowLocal: true})
		if err != nil {
			t.Fatal(err)
		}
		_, err = s.BindRun(ctx, RunBindingRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, AttemptID: attempt, TaskClass: "task", BindingID: "binding", CapabilityID: "unknown", CommandSHA256: strings.Repeat("0", 64)})
		if err != nil {
			t.Fatal(err)
		}
		receipts = append(receipts, p.ReceiptID)
	}
	start := make(chan struct{})
	results := make(chan error, 2)
	for _, id := range receipts {
		go func() { <-start; results <- s.ClaimRun(ctx, id) }()
	}
	close(start)
	successes := 0
	for i := 0; i < 2; i++ {
		err := <-results
		if err == nil {
			successes++
		} else {
			requireCode(t, err, "RUN_ALREADY_STARTED")
		}
	}
	if successes != 1 {
		t.Fatalf("expected one host execution reservation, got %d", successes)
	}
	report, err := s.RunReport(ctx, RunReportRequest{Repo: scope.Repo, Limit: 10})
	if err != nil || len(report.Rows) != 1 || report.Rows[0].AttemptID != attempt {
		t.Fatalf("reservation changed run population: %+v %v", report, err)
	}
}
