package runner

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

type noCompileStore struct{ *core.Store }

func (noCompileStore) Compile(context.Context, core.CompileRequest, core.Destination) (core.Package, error) {
	return core.Package{}, fmt.Errorf("retained execution must not compile")
}

func retainedFixture(t *testing.T) (*core.Store, Request, core.Package) {
	t.Helper()
	s := runStore(t)
	repo := uuid.NewString()
	draft := core.Draft{Kind: "note", Body: "Retained compiler guidance", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, ClaimType: "self"}
	if _, err := s.Create(context.Background(), core.CreateRequest{RequestID: uuid.NewString(), Draft: draft}); err != nil {
		t.Fatal(err)
	}
	request := Request{Compile: core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Query: "compiler", Purpose: "context", AvailableTokens: 32000},
		Destination: core.Destination{Name: "local", AllowLocal: true}, Command: []string{"/bin/cat"}, Carrier: "stdin", Prompt: "Inspect this retained input", Timeout: time.Second,
		TaskClass: "build", BindingID: "retained-fixture", CapabilityID: "shell", ArtifactDirectory: t.TempDir()}
	request.Compile.Context = &core.ContextPins{TaskClass: request.TaskClass, BindingID: request.BindingID, CapabilityID: request.CapabilityID}
	pkg, err := s.Compile(context.Background(), request.Compile, request.Destination)
	if err != nil || len(pkg.Semantic.Selected) != 1 {
		t.Fatalf("fixture did not select memory: %+v, %v", pkg, err)
	}
	request.Retained = &core.RunPackageRequest{ReceiptID: pkg.ReceiptID, Seal: pkg.Seal}
	return s, request, pkg
}

func TestRetainedRunExecutesExactPackageWithoutCompile(t *testing.T) {
	for _, carrier := range []string{"stdin", "argv"} {
		t.Run(carrier, func(t *testing.T) {
			s, req, pkg := retainedFixture(t)
			req.Carrier = carrier
			if carrier == "argv" {
				req.Command = []string{"/bin/sh", "-c", `printf '%s' "$1"`, "fixture"}
			}
			draft := core.Draft{Kind: "note", Body: "New optional compiler note", Scope: core.Scope{Repo: req.Compile.Scope.Repo, TaskID: "*", RunID: "*"}, ClaimType: "self"}
			if _, err := s.Create(context.Background(), core.CreateRequest{RequestID: uuid.NewString(), Draft: draft}); err != nil {
				t.Fatal(err)
			}
			var output bytes.Buffer
			result, err := Run(context.Background(), noCompileStore{s}, req, &output, &output)
			if err != nil || result.ReceiptID != pkg.ReceiptID || result.Seal != pkg.Seal || result.ProcessState != "exited" || result.ExitCode == nil || *result.ExitCode != 0 {
				t.Fatalf("retained execution failed: %+v, %v", result, err)
			}
			rendered, err := pkg.Render()
			if err != nil || output.String() != rendered+"\nTASK\n"+req.Prompt {
				t.Fatalf("child did not receive exact retained bytes: %q, %v", output.String(), err)
			}
			contextBytes, err := os.ReadFile(filepath.Join(result.Artifacts, "context.txt"))
			if err != nil || string(contextBytes) != rendered {
				t.Fatalf("managed context changed: %v", err)
			}
			_, err = Run(context.Background(), noCompileStore{s}, req, &output, &output)
			if core.Code(err) != "RUN_ALREADY_STARTED" {
				t.Fatalf("retained receipt launched twice: %v", err)
			}
		})
	}
}

func TestRetainedRunRejectsContextChangesBeforeBindingOrChild(t *testing.T) {
	s, request, pkg := retainedFixture(t)
	for _, change := range []string{"repo", "task", "run", "query", "purpose", "budget", "binding", "capability", "revision", "workspace", "destination", "seal", "mode"} {
		t.Run(change, func(t *testing.T) {
			req := request
			req.Compile.Context = nil
			ref := *request.Retained
			req.Retained = &ref
			marker := filepath.Join(t.TempDir(), "started")
			req.Command = []string{"/bin/sh", "-c", `: > "$1"`, "fixture", marker}
			switch change {
			case "repo":
				req.Compile.Scope.Repo = "other"
			case "task":
				req.Compile.Scope.TaskID = "other"
			case "run":
				req.Compile.Scope.RunID = "other"
			case "query":
				req.Compile.Query = "other"
			case "purpose":
				req.Compile.Purpose = "planning"
			case "budget":
				req.Compile.AvailableTokens--
			case "binding":
				req.BindingID = "other"
			case "capability":
				req.CapabilityID = "other"
			case "revision":
				req.Revision = strings.Repeat("a", 40)
			case "workspace":
				req.WorkspaceSHA256 = strings.Repeat("b", 64)
			case "destination":
				req.Destination = core.Destination{Name: "hosted"}
			case "seal":
				req.Retained.Seal = "wrong"
			case "mode":
				req.Compile.Mode = "index"
			}
			var output bytes.Buffer
			_, err := Run(context.Background(), noCompileStore{s}, req, &output, &output)
			code := "INVALID_REQUEST"
			if change == "destination" {
				code = "AUTHORITY_DENIED"
			}
			if change == "seal" {
				code = "INTEGRITY_FAILURE"
			}
			if core.Code(err) != code {
				t.Fatalf("want %s for mismatched context, got %v", code, err)
			}
			if _, err := os.Stat(marker); !os.IsNotExist(err) {
				t.Fatalf("refused child started: %v", err)
			}
			status, err := s.RunStatus(context.Background(), pkg.ReceiptID)
			if err != nil || status.BindingObserved || status.LaunchClaimed {
				t.Fatalf("refused input bound or claimed: %+v, %v", status, err)
			}
		})
	}
}

func TestRetainedRunRechecksAfterLoading(t *testing.T) {
	s, req, pkg := retainedFixture(t)
	record := pkg.Semantic.Selected[0].Record
	draft := record.Draft
	draft.Body = "Changed after the retained package was loaded"
	store := editBeforeClaimStore{s, core.EditRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: record.Version, Draft: draft}}
	marker := filepath.Join(t.TempDir(), "started")
	req.Command = []string{"/bin/sh", "-c", `: > "$1"`, "fixture", marker}
	var output bytes.Buffer
	_, err := Run(context.Background(), store, req, &output, &output)
	if core.Code(err) != "STALE_PACKAGE" {
		t.Fatalf("stale prepared memory launched: %v", err)
	}
	if _, err := os.Stat(marker); !os.IsNotExist(err) {
		t.Fatalf("child started with stale prepared memory: %v", err)
	}
	status, err := s.RunStatus(context.Background(), pkg.ReceiptID)
	if err != nil || !status.BindingObserved || status.LaunchClaimed {
		t.Fatalf("unexpected staged refusal state: %+v, %v", status, err)
	}
}
