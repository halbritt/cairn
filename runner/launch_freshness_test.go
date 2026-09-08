package runner

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

type editBeforeClaimStore struct {
	*core.Store
	edit core.EditRequest
}

func (s editBeforeClaimStore) ClaimRun(ctx context.Context, id string) error {
	if _, err := s.Edit(ctx, s.edit); err != nil {
		return err
	}
	return s.Store.ClaimRun(ctx, id)
}

func TestChangedMemoryPreventsChildLaunch(t *testing.T) {
	for _, carrier := range []string{"stdin", "argv"} {
		t.Run(carrier, func(t *testing.T) {
			ctx := context.Background()
			s := runStore(t)
			repo := uuid.NewString()
			draft := core.Draft{Kind: "note", Body: "Old compiler guidance", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, ClaimType: "self"}
			record, err := s.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: draft})
			if err != nil {
				t.Fatal(err)
			}
			draft.Body = "Corrected compiler guidance"
			store := editBeforeClaimStore{s, core.EditRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: record.Version, Draft: draft}}
			marker := filepath.Join(t.TempDir(), "child-started")
			var output bytes.Buffer
			result, err := Run(ctx, store, Request{
				Compile:     core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Query: "compiler", Purpose: "context", AvailableTokens: 32000},
				Destination: core.Destination{Name: "local", AllowLocal: true},
				Command:     []string{"/bin/sh", "-c", `: > "$1"`, "fixture", marker}, Carrier: carrier, Timeout: time.Second,
				ArtifactDirectory: t.TempDir(),
			}, &output, &output)
			if core.Code(err) != "STALE_PACKAGE" || result.ReceiptID == "" {
				t.Fatalf("expected stale delivery refusal with retained receipt, got %+v, %v", result, err)
			}
			if _, err = os.Stat(marker); !os.IsNotExist(err) {
				t.Fatalf("child ran despite stale memory: %v", err)
			}
			if output.Len() != 0 {
				t.Fatal("refused child produced output")
			}
		})
	}
}
