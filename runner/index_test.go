package runner

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestObservedIndexDeliversPullArgumentsAndRetainsReceipt(t *testing.T) {
	for _, retained := range []bool{false, true} {
		s := runStore(t)
		ctx := context.Background()
		repo := uuid.NewString()
		body := "compiler procedure " + strings.Repeat("selected details ", 200)
		if _, err := s.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: body, Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, ClaimType: "self"}}); err != nil {
			t.Fatal(err)
		}
		req := Request{Compile: core.CompileRequest{Mode: "index", ExpansionReader: "reader:" + repo, RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Query: "compiler", Purpose: "context", AvailableTokens: 32000}, Destination: core.Destination{Name: "local", AllowLocal: true}, Command: []string{"/bin/cat"}, Carrier: "stdin", Prompt: "Inspect the procedure", Timeout: time.Second, TaskClass: "review", BindingID: "cat", CapabilityID: "shell", ArtifactDirectory: t.TempDir()}
		req.IndexTools = &IndexTools{Pull: "cairn_pull", Search: "cairn_search"}
		req.Compile.Context = &core.ContextPins{TaskClass: req.TaskClass, BindingID: req.BindingID, CapabilityID: req.CapabilityID}
		var index core.IndexResult
		var store Store = s
		if retained {
			var err error
			index, err = s.Index(ctx, req.Compile, req.Destination)
			if err != nil {
				t.Fatal(err)
			}
			req.Retained = &core.RunPackageRequest{ReceiptID: index.Package.ReceiptID, Seal: index.Package.Seal}
			store = noCompileStore{s}
		}
		var output bytes.Buffer
		result, err := Run(ctx, store, req, &output, &output)
		if err != nil || result.ProcessState != "exited" || result.ExitCode == nil || *result.ExitCode != 0 {
			t.Fatalf("observed index retained=%v: %+v %v", retained, result, err)
		}
		if retained && (result.ReceiptID != index.Package.ReceiptID || result.Seal != index.Package.Seal) {
			t.Fatal("retained receipt changed")
		}
		if strings.Contains(output.String(), body) || !strings.Contains(output.String(), "cairn_pull") || !strings.HasSuffix(output.String(), "\nTASK\n"+req.Prompt) {
			t.Fatalf("invalid compact delivery: %s", output.String())
		}
		start := strings.Index(output.String(), "{\"")
		end := strings.LastIndex(output.String(), "\nTASK\n")
		var view struct {
			Index []struct {
				PullArguments core.ExpandRequest `json:"pull_arguments"`
			} `json:"index"`
		}
		if start < 0 || end < start {
			t.Fatal("missing index JSON")
		}
		if err := json.Unmarshal([]byte(output.String()[start:end]), &view); err != nil || len(view.Index) != 1 {
			t.Fatalf("pull instructions: %+v %v", view, err)
		}
		reader, err := core.Open(ctx, os.Getenv("CAIRN_TEST_DATABASE_URL"), core.Channel{Principal: req.Compile.ExpansionReader, Repo: repo})
		if err != nil {
			t.Fatal(err)
		}
		pulled, err := reader.Expand(ctx, view.Index[0].PullArguments, req.Destination)
		reader.Close()
		if err != nil || pulled.Selection.Record.Body != body {
			t.Fatalf("delivered arguments unusable: %+v %v", pulled, err)
		}
	}
}
