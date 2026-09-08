package localapi_test

import (
	"context"
	"os"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestHostedOwnerCanInspectClaimAndOutcomeWithoutProtectedReport(t *testing.T) {
	client, _, repo, _ := authenticatedHost(t, "observer", "hosted", nil)
	ctx := context.Background()
	p, err := client.Compile(ctx, core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Purpose: "context", AvailableTokens: 32000}, core.Destination{Name: "hosted"})
	if err != nil {
		t.Fatal(err)
	}
	status, err := client.RunStatus(ctx, p.ReceiptID)
	if err != nil || status.LaunchClaimed || status.Outcome != nil || status.BindingObserved {
		t.Fatalf("unclaimed status changed: %+v %v", status, err)
	}
	if err = client.ClaimRun(ctx, p.ReceiptID); err != nil {
		t.Fatal(err)
	}
	status, err = client.RunStatus(ctx, p.ReceiptID)
	if err != nil || !status.LaunchClaimed || status.Outcome != nil {
		t.Fatalf("claim confused with observed process: %+v %v", status, err)
	}
	zero := 0
	outcome, err := client.RecordOutcome(ctx, core.OutcomeRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, ProcessState: "exited", ExitCode: &zero, DurationMS: 1, StdoutSHA256: strings.Repeat("0", 64), StderrSHA256: strings.Repeat("0", 64)})
	if err != nil {
		t.Fatal(err)
	}
	status, err = client.RunStatus(ctx, p.ReceiptID)
	if err != nil || status.Outcome == nil || status.Outcome.ObservationID != outcome.ID || status.Outcome.ProcessState != "exited" || status.Outcome.ExitCode == nil || *status.Outcome.ExitCode != 0 {
		t.Fatalf("outcome confirmation lost: %+v %v", status, err)
	}
	if status.ObservedAt.IsZero() || status.ReceiptID != p.ReceiptID {
		t.Fatal("status missing observation identity")
	}
	// Inspect the wire shape: decoding into RunStatus would silently discard
	// unexpected fields and could hide a server-side disclosure regression.
	var wire map[string]any
	if err = client.Call(ctx, "run-status", map[string]string{"receipt_id": p.ReceiptID}, &wire); err != nil {
		t.Fatal(err)
	}
	assertKeys := func(value map[string]any, keys ...string) {
		t.Helper()
		if len(value) != len(keys) {
			t.Fatalf("unexpected status field count: %v", value)
		}
		for _, key := range keys {
			if _, ok := value[key]; !ok {
				t.Fatalf("missing status field %q", key)
			}
		}
	}
	assertKeys(wire, "receipt_id", "observed_at", "launch_claimed", "binding_observed", "outcome")
	wireOutcome, ok := wire["outcome"].(map[string]any)
	if !ok {
		t.Fatal("missing wire outcome")
	}
	assertKeys(wireOutcome, "observation_id", "process_state", "exit_code", "duration_ms")
	var report core.RunReport
	err = client.Call(ctx, "run-report", core.RunReportRequest{Repo: repo, Limit: 10}, &report)
	if core.Code(err) != "AUTHORITY_DENIED" {
		t.Fatalf("protected report widened: %v", err)
	}
	if err = client.ClaimRun(ctx, p.ReceiptID); core.Code(err) != "RUN_ALREADY_STARTED" {
		t.Fatalf("status read authorized duplicate: %v", err)
	}
}

func TestRunStatusEnforcesOwnerAndRepository(t *testing.T) {
	client, _, repo, _ := authenticatedHost(t, "observer", "hosted", nil)
	ctx := context.Background()
	for _, scope := range []struct{ principal, repo string }{
		{"another-host", repo},
		{"host:test", uuid.NewString()},
	} {
		s, err := core.Open(ctx, os.Getenv("CAIRN_TEST_DATABASE_URL"), core.Channel{Principal: scope.principal, Repo: scope.repo, Instrumented: true})
		if err != nil {
			t.Fatal(err)
		}
		p, err := s.Compile(ctx, core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: scope.repo, TaskID: "task", RunID: "run"}, Purpose: "context", AvailableTokens: 32000}, core.Destination{Name: "hosted"})
		s.Close()
		if err != nil {
			t.Fatal(err)
		}
		_, err = client.RunStatus(ctx, p.ReceiptID)
		if core.Code(err) != "AUTHORITY_DENIED" {
			t.Fatalf("other caller/repository receipt exposed: %v", err)
		}
	}
	for _, id := range []string{uuid.NewString(), "not-a-uuid"} {
		_, err := client.RunStatus(ctx, id)
		expected := "NOT_FOUND"
		if id == "not-a-uuid" {
			expected = "INVALID_REQUEST"
		}
		if core.Code(err) != expected {
			t.Fatalf("bad receipt result %q: %v", id, err)
		}
	}
}
