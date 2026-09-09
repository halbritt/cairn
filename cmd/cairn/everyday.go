package main

import (
	"context"
	"flag"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/runner"
)

func flags(name string) *flag.FlagSet {
	f := flag.NewFlagSet(name, flag.ContinueOnError)
	f.SetOutput(io.Discard)
	return f
}
func defaultRepo() string {
	cwd, err := os.Getwd()
	if err != nil {
		return ""
	}
	return cwd
}
func remember(ctx context.Context, s *core.Store, args []string, input io.Reader) (core.Record, error) {
	req, err := rememberRequest(args, input)
	if err != nil {
		return core.Record{}, err
	}
	return s.Create(ctx, req)
}
func rememberRequest(args []string, input io.Reader) (core.CreateRequest, error) {
	f := flags("remember")
	fromStdin := f.Bool("stdin", false, "read note text from stdin instead of arguments")
	repo := f.String("repo", defaultRepo(), "repository identity")
	kind := f.String("kind", "note", "record kind")
	share := f.Bool("shareable", false, "allow hosted delivery")
	task := f.String("task", "*", "task scope")
	run := f.String("run", "*", "run scope")
	request := f.String("request-id", uuid.NewString(), "retry identity")
	if err := f.Parse(args); err != nil {
		return core.CreateRequest{}, invalid(err.Error())
	}
	body := strings.Join(f.Args(), " ")
	if *fromStdin {
		if f.NArg() != 0 {
			return core.CreateRequest{}, invalid("remember accepts either --stdin or note arguments")
		}
		encoded, err := io.ReadAll(io.LimitReader(input, 65536+1))
		if err != nil {
			return core.CreateRequest{}, err
		}
		body = string(encoded)
	}
	if strings.TrimSpace(body) == "" || len(body) > 65536 || !utf8.ValidString(body) {
		return core.CreateRequest{}, invalid("remember requires 1-65536 bytes of nonblank UTF-8 text")
	}
	sensitivity := "local"
	if *share {
		sensitivity = "shareable"
	}
	return core.CreateRequest{RequestID: *request, Draft: core.Draft{Kind: *kind, Body: body, Scope: core.Scope{Repo: *repo, TaskID: *task, RunID: *run}, ClaimType: "self", Sensitivity: sensitivity}}, nil
}
func search(ctx context.Context, s *core.Store, args []string) (core.Package, error) {
	var kinds []string
	f := flags("search")
	repo := f.String("repo", defaultRepo(), "repository identity")
	f.Func("kind", "optional record kind; repeat for multiple labels (required instructions always apply)", func(value string) error { kinds = append(kinds, value); return nil })
	purpose := f.String("purpose", "context", "consumer purpose")
	dest := f.String("destination", "local", "destination")
	tokens := f.Int("tokens", 32000, "available memory input room")
	revision := f.String("revision", "", "Git object ID")
	workspace := f.String("workspace-sha256", "", "workspace digest")
	taskClass := f.String("task-class", "", "task category")
	binding := f.String("binding", "", "binding identity")
	capability := f.String("capability", "", "capability identity")
	task := f.String("task", "interactive", "task pin")
	run := f.String("run", uuid.NewString(), "run pin")
	if err := f.Parse(args); err != nil {
		return core.Package{}, invalid(err.Error())
	}
	return s.Compile(ctx, core.CompileRequest{Kinds: kinds, Context: &core.ContextPins{Revision: *revision, WorkspaceSHA256: *workspace, TaskClass: *taskClass, BindingID: *binding, CapabilityID: *capability}, RequestID: uuid.NewString(), Scope: core.Scope{Repo: *repo, TaskID: *task, RunID: *run}, Query: strings.Join(f.Args(), " "), Purpose: *purpose, AvailableTokens: *tokens}, core.Destination{Name: *dest, AllowLocal: *dest == "local"})
}
func runTask(ctx context.Context, s runner.Store, args []string) (runner.Result, error) {
	f := flags("run")
	attempt := f.String("attempt-id", "", "existing host-observed attempt UUID")
	repo := f.String("repo", defaultRepo(), "repository identity")
	directory := f.String("dir", defaultRepo(), "working directory")
	prompt := f.String("prompt", "", "task prompt")
	query := f.String("query", "", "retrieval query (default prompt for fresh compilation)")
	receipt := f.String("receipt-id", "", "execute this retained receipt instead of compiling")
	seal := f.String("seal", "", "expected seal of the retained receipt")
	carrier := f.String("carrier", "stdin", "input carrier")
	dest := f.String("destination", "local", "destination")
	tokens := f.Int("tokens", 32000, "available input room reserved for memory")
	timeout := f.Duration("timeout", 10*time.Minute, "process timeout")
	taskClass := f.String("task-class", "unknown", "comparable task category")
	binding := f.String("binding", "", "execution binding identity")
	capability := f.String("capability", "unknown", "capability identity; distinct from binding")
	revision := f.String("revision", "", "declared repository revision")
	workspace := f.String("workspace-sha256", "", "declared dirty-workspace digest")
	task := f.String("task", "interactive", "task identity")
	run := f.String("run", uuid.NewString(), "run identity")
	request := f.String("request-id", uuid.NewString(), "compile and launch identity")
	if err := f.Parse(args); err != nil {
		return runner.Result{}, invalid(err.Error())
	}
	if f.NArg() == 0 {
		return runner.Result{}, invalid("run requires -- COMMAND ARGS...")
	}
	retainedRequested := false
	f.Visit(func(fl *flag.Flag) {
		if fl.Name == "receipt-id" || fl.Name == "seal" {
			retainedRequested = true
		}
	})
	if retainedRequested && (*receipt == "" || *seal == "") {
		return runner.Result{}, invalid("retained execution requires both --receipt-id and --seal")
	}
	if *query == "" && !retainedRequested {
		*query = *prompt
	}
	command := f.Args()
	if strings.Contains(strings.ToLower(filepath.Base(command[0])), "opencode") && *dest != "hosted" {
		return runner.Result{}, invalid("OpenCode must use --destination hosted; its provider is not a trusted local binding")
	}
	artifacts, err := dataDirectory()
	if err != nil {
		return runner.Result{}, err
	}
	req := runner.Request{AttemptID: *attempt, Compile: core.CompileRequest{RequestID: *request, Scope: core.Scope{Repo: *repo, TaskID: *task, RunID: *run}, Query: *query, Purpose: "context", AvailableTokens: *tokens}, Destination: core.Destination{Name: *dest, AllowLocal: *dest == "local"}, Command: command, Directory: *directory, Carrier: *carrier, Prompt: *prompt, Timeout: *timeout, TaskClass: *taskClass, BindingID: *binding, CapabilityID: *capability, Revision: *revision, WorkspaceSHA256: *workspace, ArtifactDirectory: filepath.Join(artifacts, "runs")}
	if retainedRequested {
		req.Retained = &core.RunPackageRequest{ReceiptID: *receipt, Seal: *seal}
	}
	result, err := runner.Run(ctx, s, req, os.Stdout, os.Stderr)
	if err != nil && core.Code(err) == "STORE_ERROR" && result.ReceiptID != "" {
		err = &core.Error{Code: "RUN_FAILED", Message: "wrapped task failed; inspect process_state and the run artifact directory", Cause: err}
	}
	return result, err
}
