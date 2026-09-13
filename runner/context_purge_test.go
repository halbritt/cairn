package runner

import (
	"context"
	"io"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/artifacts"
	"github.com/halbritt/cairn/core"
)

func purgeOperator(t *testing.T) (*core.Store, core.Grant) {
	t.Helper()
	s, err := core.Open(context.Background(), os.Getenv("CAIRN_TEST_DATABASE_URL"), core.Channel{Principal: "operator:tests", Operator: true, Instrumented: true})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.Close)
	root, err := s.Bootstrap(context.Background(), core.BootstrapRequest{RequestID: "82bf5bce-dc6c-4d03-a49f-1677bdcc9a32", Reason: "Install synthetic test operator root"})
	if err != nil {
		t.Fatal(err)
	}
	return s, root
}

func TestRunContextIsRegisteredAndPurgedWithItsRecord(t *testing.T) {
	ctx := context.Background()
	runnerStore := runStore(t)
	op, root := purgeOperator(t)
	repo := uuid.NewString()
	record, err := op.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "managed_context_canary", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, ClaimType: "self"}})
	if err != nil {
		t.Fatal(err)
	}
	result, err := Run(ctx, runnerStore, Request{Compile: core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Purpose: "context", AvailableTokens: 64000}, Destination: core.Destination{Name: "local", AllowLocal: true}, Command: []string{"/bin/cat"}, Carrier: "stdin", Timeout: time.Second, ArtifactDirectory: t.TempDir()}, io.Discard, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = os.Stat(filepath.Join(result.Artifacts, "context.txt")); err != nil {
		t.Fatal(err)
	}
	preview, err := op.PreviewDeletion(ctx, record.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, target := range preview.DeletionTargets {
		if target.TargetType == "managed_context" && target.TargetID == result.ReceiptID {
			found = true
		}
	}
	if !found {
		t.Fatalf("run context is not a managed purge target: %+v", preview.DeletionTargets)
	}
	deletion, err := op.Forget(ctx, core.ForgetRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: record.Version, GrantID: root.ID, PreviewID: preview.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	done, err := artifacts.PurgeDeletion(ctx, op, deletion.DeletionID)
	if err != nil {
		t.Fatal(err)
	}
	for _, effect := range done.Effects {
		if effect.TargetType == "managed_context" && effect.Status != "completed" {
			t.Fatalf("file purge incomplete: %+v", effect)
		}
	}
	if _, err = os.Lstat(filepath.Join(result.Artifacts, "context.txt")); !os.IsNotExist(err) {
		t.Fatalf("context retained: %v", err)
	}
	if _, err = os.Stat(filepath.Join(result.Artifacts, "outcome.json")); err != nil {
		t.Fatalf("unrelated outcome removed: %v", err)
	}
	if _, err = artifacts.PurgeDeletion(ctx, op, deletion.DeletionID); err != nil {
		t.Fatal(err)
	}
}
