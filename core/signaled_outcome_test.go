package core

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestSignaledOutcomeRecordsTheSignal(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	if _, err := s.Bootstrap(ctx, BootstrapRequest{RequestID: uuid.NewString(), Reason: "Install isolated signaled-outcome fixture authority"}); err != nil {
		t.Fatal(err)
	}
	repo := "fixture:signaled-outcome"
	if _, err := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: projectNote(repo)}); err != nil {
		t.Fatal(err)
	}
	p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.BindRun(ctx, RunBindingRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, TaskClass: "signal-test", BindingID: "fixture", CapabilityID: "unknown", CommandSHA256: strings.Repeat("0", 64)}); err != nil {
		t.Fatal(err)
	}
	if err = s.ClaimRun(ctx, p.ReceiptID); err != nil {
		t.Fatal(err)
	}
	digest := strings.Repeat("0", 64)
	nine, one := 9, 1
	for _, invalid := range []OutcomeRequest{
		{ProcessState: "signaled"},                                // the signal is required
		{ProcessState: "signaled", Signal: &nine, ExitCode: &one}, // a signaled process has no exit code
		{ProcessState: "exited", ExitCode: &one, Signal: &nine},   // only a signaled process carries a signal
		{ProcessState: "signaled", Signal: new(int)},              // signal numbers are positive
	} {
		invalid.RequestID, invalid.ReceiptID, invalid.StdoutSHA256, invalid.StderrSHA256 = uuid.NewString(), p.ReceiptID, digest, digest
		_, err := s.RecordOutcome(ctx, invalid)
		requireCode(t, err, "INVALID_REQUEST")
	}
	if _, err = s.RecordOutcome(ctx, OutcomeRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, ProcessState: "signaled", Signal: &nine, DurationMS: 5, StdoutSHA256: digest, StderrSHA256: digest}); err != nil {
		t.Fatal(err)
	}
	status, err := s.RunStatus(ctx, p.ReceiptID)
	if err != nil || status.Outcome == nil || status.Outcome.ProcessState != "signaled" || status.Outcome.Signal == nil || *status.Outcome.Signal != 9 || status.Outcome.ExitCode != nil {
		t.Fatalf("signaled outcome not reported: %+v %v", status.Outcome, err)
	}
}

func TestOutcomeDigestIsUnchangedWithoutASignal(t *testing.T) {
	// Pending retry files written by older runners must keep replaying under
	// the same request digest, so the new field is omitted when absent.
	zero := 0
	encoded, err := json.Marshal(OutcomeRequest{RequestID: "r", ReceiptID: "p", ExitCode: &zero, ProcessState: "exited"})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "signal") {
		t.Fatalf("absent signal changed the request encoding: %s", encoded)
	}
}
