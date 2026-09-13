package core

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestRunStatusSurvivesPolicyRestoreAndPayloadFences(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	root, err := s.Bootstrap(ctx, BootstrapRequest{RequestID: uuid.NewString(), Reason: "Install isolated run-status fixture authority"})
	if err != nil {
		t.Fatal(err)
	}
	repo := "fixture:run-status"
	record, err := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.BindRun(ctx, RunBindingRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, TaskClass: "status-test", BindingID: "fixture", CapabilityID: "unknown", CommandSHA256: strings.Repeat("0", 64)})
	if err != nil {
		t.Fatal(err)
	}
	if err = s.ClaimRun(ctx, p.ReceiptID); err != nil {
		t.Fatal(err)
	}
	_, err = s.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, GrantID: root.ID, Rules: &PolicyRules{}, Reason: "Change current policy without hiding prior launch evidence"})
	if err != nil {
		t.Fatal(err)
	}
	requireCode(t, s.ClaimRun(ctx, p.ReceiptID), "STALE_PACKAGE")
	status, err := s.RunStatus(ctx, p.ReceiptID)
	if err != nil || !status.LaunchClaimed || !status.BindingObserved || status.Outcome != nil {
		t.Fatalf("policy change hid pending status: %+v %v", status, err)
	}
	if _, err = s.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "Fence restored receipts before traffic admission"}); err != nil {
		t.Fatal(err)
	}
	requireCode(t, s.ClaimRun(ctx, p.ReceiptID), "STALE_PACKAGE")
	status, err = s.RunStatus(ctx, p.ReceiptID)
	if err != nil || !status.LaunchClaimed || status.Outcome != nil {
		t.Fatalf("restore hid historical claim: %+v %v", status, err)
	}
	// A late observation can be reconciled even when new delivery is fenced.
	outcome, err := s.RecordOutcome(ctx, OutcomeRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, ProcessState: "timeout", DurationMS: 100, StdoutSHA256: strings.Repeat("0", 64), StderrSHA256: strings.Repeat("0", 64)})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := s.PreviewDeletion(ctx, record.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	deletion, err := s.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: record.Version, GrantID: root.ID, PreviewID: preview.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.PurgeDeletion(ctx, deletion.DeletionID); err != nil {
		t.Fatal(err)
	}
	_, err = s.Replay(ctx, p.ReceiptID)
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")
	status, err = s.RunStatus(ctx, p.ReceiptID)
	if err != nil || status.Outcome == nil || status.Outcome.ObservationID != outcome.ID || status.Outcome.ProcessState != "timeout" || status.Outcome.ExitCode != nil || status.Outcome.DurationMS != 100 {
		t.Fatalf("payload purge hid late process evidence: %+v %v", status, err)
	}
	requireCode(t, s.ClaimRun(ctx, p.ReceiptID), "STALE_PACKAGE")
}
