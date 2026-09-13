package localapi_test

import (
	"bytes"
	"context"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/runner"
)

func TestAuthenticatedRetainedRunUsesOriginalReceiptAndNoCompile(t *testing.T) {
	for _, destination := range []string{"local", "hosted"} {
		t.Run(destination, func(t *testing.T) {
			var compiles atomic.Int32
			client, writer, repo, home := authenticatedHost(t, "observer", destination, func(next http.Handler) http.Handler {
				return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.URL.Path == "/v1/compile" {
						compiles.Add(1)
					}
					next.ServeHTTP(w, r)
				})
			})
			ctx := context.Background()
			draft := core.Draft{Kind: "note", Body: "Retained API guidance", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, Sensitivity: "shareable", ClaimType: "self"}
			if _, err := writer.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: draft}); err != nil {
				t.Fatal(err)
			}
			req := runner.Request{Compile: core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Query: "retained", Purpose: "context", AvailableTokens: 32000}, Destination: core.Destination{Name: destination, AllowLocal: destination == "local"},
				Command: []string{"/bin/cat"}, Carrier: "stdin", Prompt: "Use this exact package", Timeout: time.Second, TaskClass: "build", BindingID: "retained-api", CapabilityID: "shell", ArtifactDirectory: home}
			req.Compile.Context = &core.ContextPins{TaskClass: req.TaskClass, BindingID: req.BindingID, CapabilityID: req.CapabilityID}
			pkg, err := client.Compile(ctx, req.Compile, req.Destination)
			if err != nil || len(pkg.Semantic.Selected) != 1 {
				t.Fatalf("compile fixture: %+v, %v", pkg, err)
			}
			draft.Body = "New optional retained API note"
			if _, err = writer.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: draft}); err != nil {
				t.Fatal(err)
			}
			req.Retained = &core.RunPackageRequest{ReceiptID: pkg.ReceiptID, Seal: pkg.Seal}
			var output bytes.Buffer
			result, err := runner.Run(ctx, client, req, &output, &output)
			if err != nil || result.ReceiptID != pkg.ReceiptID || result.Seal != pkg.Seal || result.ProcessState != "exited" {
				t.Fatalf("retained API run: %+v, %v", result, err)
			}
			rendered, err := pkg.Render()
			if err != nil || output.String() != rendered+"\nTASK\n"+req.Prompt || compiles.Load() != 1 {
				t.Fatalf("retained input replaced or recompiled: %d calls, %v", compiles.Load(), err)
			}
			status, err := client.RunStatus(ctx, pkg.ReceiptID)
			if err != nil || !status.LaunchClaimed || status.Outcome == nil || status.Outcome.ExitCode == nil || *status.Outcome.ExitCode != 0 {
				t.Fatalf("outcome did not join retained receipt: %+v, %v", status, err)
			}
		})
	}
}

func TestRunPackageAPIRejectsOrdinaryAgent(t *testing.T) {
	client, _, repo, _ := authenticatedHost(t, "agent", "local", nil)
	ctx := context.Background()
	pkg, err := client.Compile(ctx, core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Purpose: "context", AvailableTokens: 32000}, core.Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatal(err)
	}
	_, err = client.RunPackage(ctx, core.RunPackageRequest{ReceiptID: pkg.ReceiptID, Seal: pkg.Seal}, pkg.Semantic.Destination)
	if core.Code(err) != "AUTHORITY_DENIED" {
		t.Fatalf("agent acquired observer package access: %v", err)
	}
}
