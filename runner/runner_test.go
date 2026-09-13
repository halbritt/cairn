package runner

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func runStore(t *testing.T) *core.Store {
	t.Helper()
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	store, err := core.Open(context.Background(), dsn, core.Channel{Principal: "service:runner-test", Instrumented: true})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(store.Close)
	if err = store.Migrate(context.Background()); err != nil {
		t.Fatal(err)
	}
	return store
}

type pausedOutput struct {
	started chan struct{}
	release chan struct{}
	once    sync.Once
}

func (w *pausedOutput) Write(p []byte) (int, error) {
	w.once.Do(func() { close(w.started) })
	<-w.release
	return len(p), nil
}

func TestOutcomeSurvivesStoreFailure(t *testing.T) {
	s := runStore(t)
	repo := uuid.NewString()
	req := Request{Compile: core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Purpose: "context", AvailableTokens: 32000}, Destination: core.Destination{Name: "local", AllowLocal: true}, Command: []string{"/bin/echo", "observed output"}, Carrier: "stdin", Timeout: 5 * time.Second, ArtifactDirectory: t.TempDir()}
	output := &pausedOutput{started: make(chan struct{}), release: make(chan struct{})}
	type completed struct {
		result Result
		err    error
	}
	done := make(chan completed, 1)
	go func() {
		var stderr bytes.Buffer
		r, err := Run(context.Background(), s, req, output, &stderr)
		done <- completed{r, err}
	}()
	select {
	case <-output.started:
	case <-time.After(5 * time.Second):
		close(output.release)
		t.Fatal("process did not start")
	}
	s.Close()
	close(output.release)
	completion := <-done
	if completion.err == nil || completion.result.ProcessState != "exited" {
		t.Fatalf("outcome persistence failure hidden: %+v", completion)
	}
	pending, err := os.ReadFile(filepath.Join(completion.result.Artifacts, "outcome.pending.json"))
	if err != nil {
		t.Fatal(err)
	}
	var outcome core.OutcomeRequest
	if err = json.Unmarshal(pending, &outcome); err != nil {
		t.Fatal(err)
	}
	recovered := runStore(t)
	first, err := recovered.RecordOutcome(context.Background(), outcome)
	if err != nil {
		t.Fatal(err)
	}
	retry, err := recovered.RecordOutcome(context.Background(), outcome)
	if err != nil || retry.ID != first.ID {
		t.Fatalf("outcome retry duplicated %+v %v", retry, err)
	}
	report, err := recovered.Report(context.Background(), repo)
	if err != nil || report.Outcomes != 1 || report.ExitZero != 1 {
		t.Fatalf("recovery report %+v %v", report, err)
	}
}
func TestBootstrapBeforeLaunchAndObservedOutcome(t *testing.T) {
	s := runStore(t)
	repo := uuid.NewString()
	ctx := context.Background()
	_, err := s.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "CAIRN-BOOTSTRAP-CANARY", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, ClaimType: "self"}})
	if err != nil {
		t.Fatal(err)
	}
	req := Request{Compile: core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Purpose: "context", AvailableTokens: 32000}, Destination: core.Destination{Name: "local", AllowLocal: true}, Command: []string{"/bin/cat"}, Carrier: "stdin", Prompt: "synthetic task", Timeout: time.Second, ArtifactDirectory: t.TempDir()}
	var out, stderr bytes.Buffer
	result, err := Run(ctx, s, req, &out, &stderr)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out.String(), "CAIRN-BOOTSTRAP-CANARY") || result.OutcomeID == "" || result.ExitCode == nil || *result.ExitCode != 0 {
		t.Fatalf("bad observed run %+v", result)
	}
	replay, err := s.Replay(ctx, result.ReceiptID)
	if err != nil {
		t.Fatal(err)
	}
	if replay.Seal != result.Seal {
		t.Fatal("replay did not retain semantics")
	}
	_, err = Run(ctx, s, req, &out, &stderr)
	if core.Code(err) != "RUN_ALREADY_STARTED" {
		t.Fatalf("duplicate process launch allowed: %v", err)
	}
	report, err := s.Report(ctx, repo)
	if err != nil || report.Outcomes != 1 || report.ExitZero != 1 || report.UnknownTaskOutcomes != 1 {
		t.Fatalf("report %+v %v", report, err)
	}
}
func TestLaunchFailureAndTimeout(t *testing.T) {
	s := runStore(t)
	for _, scenario := range []struct {
		name    string
		command []string
		state   string
	}{{"launch", []string{"/nonexistent/cairn-test"}, "launch_failed"}, {"timeout", []string{"/bin/sh", "-c", "sleep 30 & wait"}, "timeout"}} {
		t.Run(scenario.name, func(t *testing.T) {
			req := Request{Compile: core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: uuid.NewString(), TaskID: "t", RunID: "r"}, Purpose: "context", AvailableTokens: 32000}, Destination: core.Destination{Name: "local", AllowLocal: true}, Command: scenario.command, Carrier: "stdin", Timeout: 100 * time.Millisecond, ArtifactDirectory: t.TempDir()}
			var out bytes.Buffer
			result, err := Run(context.Background(), s, req, &out, &out)
			if result.ProcessState != scenario.state || result.OutcomeID == "" {
				t.Fatalf("%+v %v", result, err)
			}
		})
	}
}

func TestPreparationFailureRetainsUnattemptedOutcome(t *testing.T) {
	for _, name := range []string{"parent-is-file", "run-directory-symlink"} {
		t.Run(name, func(t *testing.T) {
			s := runStore(t)
			ctx := context.Background()
			repo, root := uuid.NewString(), t.TempDir()
			_, err := s.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "preparation failure fixture", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, ClaimType: "self"}})
			if err != nil {
				t.Fatal(err)
			}
			marker := filepath.Join(root, "process-started")
			parent := filepath.Join(root, "artifacts")
			req := Request{Compile: core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Purpose: "context", AvailableTokens: 32000}, Destination: core.Destination{Name: "local", AllowLocal: true}, Command: []string{"/bin/sh", "-c", `printf started > "$1"`, "sh", marker}, Carrier: "stdin", Timeout: time.Second, ArtifactDirectory: parent, TaskClass: "repair", BindingID: "fixture", CapabilityID: "shell"}
			outside := t.TempDir()
			if name == "parent-is-file" {
				if err = os.WriteFile(parent, []byte("keep"), 0600); err != nil {
					t.Fatal(err)
				}
			} else {
				req.Compile.Context = &core.ContextPins{TaskClass: req.TaskClass, BindingID: req.BindingID, CapabilityID: req.CapabilityID}
				pkg, compileErr := s.Compile(ctx, req.Compile, req.Destination)
				if compileErr != nil {
					t.Fatal(compileErr)
				}
				if err = os.Mkdir(parent, 0700); err != nil {
					t.Fatal(err)
				}
				if err = os.Symlink(outside, filepath.Join(parent, pkg.ReceiptID)); err != nil {
					t.Fatal(err)
				}
			}
			var output bytes.Buffer
			result, err := Run(ctx, s, req, &output, &output)
			var pathErr *os.PathError
			if !errors.As(err, &pathErr) || result.ProcessState != "launch_failed" || result.OutcomeID == "" || result.ExitCode != nil {
				t.Fatalf("preparation failure was not retained: %+v %v", result, err)
			}
			if _, err = os.Stat(marker); !errors.Is(err, os.ErrNotExist) || output.Len() != 0 {
				t.Fatalf("process started despite failed preparation: %v", err)
			}
			entries, err := os.ReadDir(outside)
			if err != nil || len(entries) != 0 {
				t.Fatalf("failure handling wrote through refused path: %v %v", entries, err)
			}
			uses, err := s.UseReport(ctx, core.UseReportRequest{Repo: repo, Limit: 10})
			if err != nil || len(uses.Rows) != 1 {
				t.Fatalf("use report: %+v %v", uses, err)
			}
			row := uses.Rows[0]
			if row.ProcessState != "launch_failed" || row.TaskOutcome != "not_attempted" || row.Delivery != "unknown" || row.ExitCode != nil {
				t.Fatalf("invented execution or lost known failure: %+v", row)
			}
			if _, err = Run(ctx, s, req, &output, &output); core.Code(err) != "RUN_ALREADY_STARTED" {
				t.Fatalf("failed preparation reopened launch claim: %v", err)
			}
		})
	}
}
