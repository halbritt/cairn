package runner

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

type outcomeFailureStore struct {
	Store
	failure error
}

func (s outcomeFailureStore) RecordOutcome(context.Context, core.OutcomeRequest) (core.Observation, error) {
	return core.Observation{}, s.failure
}

func TestCleanupFailurePreservesOutcomeAndRecovery(t *testing.T) {
	for _, failOutcome := range []bool{false, true} {
		name := "committed"
		if failOutcome {
			name = "pending"
		}
		t.Run(name, func(t *testing.T) {
			s := runStore(t)
			var store Store = s
			unavailable := errors.New("outcome store unavailable")
			if failOutcome {
				store = outcomeFailureStore{s, unavailable}
			}
			req := Request{Compile: core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: uuid.NewString(), TaskID: "task", RunID: "run"}, Purpose: "context", AvailableTokens: 32000}, Destination: core.Destination{Name: "local", AllowLocal: true}, Command: []string{"/bin/echo", "cleanup fixture"}, Carrier: "stdin", Timeout: 5 * time.Second, ArtifactDirectory: t.TempDir()}
			var stdout, stderr bytes.Buffer
			result, err := run(context.Background(), store, req, &stdout, &stderr, func(pid int, signal syscall.Signal) error {
				// Still clean up the real process group; inject only the reported failure.
				if killErr := syscall.Kill(pid, signal); killErr != nil && !errors.Is(killErr, syscall.ESRCH) {
					t.Error(killErr)
				}
				return syscall.EPERM
			})
			if !errors.Is(err, syscall.EPERM) || result.ProcessState != "exited" || result.ExitCode == nil || *result.ExitCode != 0 {
				t.Fatalf("lost cleanup error or process observation: %+v %v", result, err)
			}
			filename := "outcome.json"
			if failOutcome {
				filename = "outcome.pending.json"
				if !errors.Is(err, unavailable) || result.OutcomeID != "" {
					t.Fatalf("lost store failure: %+v %v", result, err)
				}
			} else if result.OutcomeID == "" {
				t.Fatal("outcome not committed")
			}
			encoded, err := os.ReadFile(filepath.Join(result.Artifacts, filename))
			if err != nil {
				t.Fatal(err)
			}
			var outcome core.OutcomeRequest
			if err := json.Unmarshal(encoded, &outcome); err != nil {
				t.Fatal(err)
			}
			first, err := s.RecordOutcome(context.Background(), outcome)
			if err != nil {
				t.Fatal(err)
			}
			retry, err := s.RecordOutcome(context.Background(), outcome)
			if err != nil || retry.ID != first.ID {
				t.Fatalf("outcome retry: %+v %v", retry, err)
			}
			report, err := s.Report(context.Background(), req.Compile.Scope.Repo)
			if err != nil || report.Outcomes != 1 || report.ExitZero != 1 {
				t.Fatalf("durable process observation: %+v %v", report, err)
			}
		})
	}
}
