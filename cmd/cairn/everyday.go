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
	pinsJSON := f.String("pins", "", "explicit applicability JSON object; omitted means unpinned")
	if err := f.Parse(args); err != nil {
		return core.CreateRequest{}, invalid(err.Error())
	}
	var pins *core.Applicability
	pinsProvided := false
	f.Visit(func(fl *flag.Flag) { pinsProvided = pinsProvided || fl.Name == "pins" })
	if pinsProvided {
		if err := decode(strings.NewReader(*pinsJSON), &pins); err != nil {
			return core.CreateRequest{}, invalid(err.Error())
		}
		if pins == nil {
			return core.CreateRequest{}, invalid("pins must be a JSON object")
		}
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
	return core.CreateRequest{RequestID: *request, Draft: core.Draft{Kind: *kind, Body: body, Scope: core.Scope{Repo: *repo, TaskID: *task, RunID: *run}, Pins: pins, ClaimType: "self", Sensitivity: sensitivity}}, nil
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
	taskPhase := f.String("task-phase", "", "declared task phase (exact label)")
	binding := f.String("binding", "", "binding identity")
	capability := f.String("capability", "", "capability identity")
	task := f.String("task", "interactive", "task pin")
	run := f.String("run", uuid.NewString(), "run pin")
	if err := f.Parse(args); err != nil {
		return core.Package{}, invalid(err.Error())
	}
	return s.Compile(ctx, core.CompileRequest{Kinds: kinds, Context: &core.ContextPins{Revision: *revision, WorkspaceSHA256: *workspace, TaskClass: *taskClass, TaskPhase: *taskPhase, BindingID: *binding, CapabilityID: *capability}, RequestID: uuid.NewString(), Scope: core.Scope{Repo: *repo, TaskID: *task, RunID: *run}, Query: strings.Join(f.Args(), " "), Purpose: *purpose, AvailableTokens: *tokens}, core.Destination{Name: *dest, AllowLocal: *dest == "local"})
}
func runTask(ctx context.Context, s runner.Store, args []string) (runner.Result, error) {
	var kinds []string
	f := flags("run")
	var outputs []runner.OutputArtifact
	f.Func("artifact", "fingerprint a selected file after the process: LABEL=PATH (repeat, at most 16)", func(value string) error {
		label, path, ok := strings.Cut(value, "=")
		if !ok {
			return invalid("artifact requires LABEL=PATH")
		}
		outputs = append(outputs, runner.OutputArtifact{Label: label, Path: path})
		return nil
	})
	shareArtifacts := f.Bool("share-artifact-evidence", false, "allow hosted delivery of the selected fingerprint manifest (contents are never captured)")
	f.Func("kind", "optional record kind; repeat for multiple labels (required instructions always apply)", func(value string) error { kinds = append(kinds, value); return nil })
	attempt := f.String("attempt-id", "", "existing host-observed attempt UUID")
	repo := f.String("repo", defaultRepo(), "repository identity")
	directory := f.String("dir", defaultRepo(), "working directory")
	prompt := f.String("prompt", "", "task prompt")
	promptFile := f.String("prompt-file", "", "read task text from a regular UTF-8 file instead of --prompt")
	query := f.String("query", "", "retrieval query (defaults to inline prompt for fresh compilation; file input stays separate)")
	semantic := f.Bool("semantic", false, "optional semantic index discovery")
	browse := f.Bool("browse", false, "browse an index without a query")
	offset := f.Int("offset", 0, "explicit ranked or browse page offset")
	index := f.Bool("index", false, "deliver compact context with declared native pull tools")
	reader := f.String("expansion-reader", "", "configured ordinary principal for index pulls")
	pullTool := f.String("pull-tool", "", "existing harness pull tool name")
	searchTool := f.String("search-tool", "", "existing harness search tool name")
	receipt := f.String("receipt-id", "", "execute this retained receipt instead of compiling")
	seal := f.String("seal", "", "expected seal of the retained receipt")
	carrier := f.String("carrier", "stdin", "input carrier")
	dest := f.String("destination", "local", "destination")
	tokens := f.Int("tokens", 32000, "available input room reserved for memory")
	timeout := f.Duration("timeout", 10*time.Minute, "process timeout")
	taskClass := f.String("task-class", "unknown", "comparable task category")
	taskPhase := f.String("task-phase", "", "declared task phase (exact label)")
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
	var hasPrompt, hasFile bool
	f.Visit(func(fl *flag.Flag) {
		hasPrompt = hasPrompt || fl.Name == "prompt"
		hasFile = hasFile || fl.Name == "prompt-file"
		if fl.Name == "receipt-id" || fl.Name == "seal" {
			retainedRequested = true
		}
	})
	if retainedRequested && (*receipt == "" || *seal == "") {
		return runner.Result{}, invalid("retained execution requires both --receipt-id and --seal")
	}
	if hasPrompt && hasFile {
		return runner.Result{}, invalid("run accepts only one of --prompt or --prompt-file")
	}
	if hasFile {
		text, err := readTaskFile(*promptFile, runner.MaxPromptBytes)
		if err != nil {
			return runner.Result{}, err
		}
		if strings.TrimSpace(text) == "" || !utf8.ValidString(text) || strings.ContainsRune(text, 0) {
			return runner.Result{}, invalid("task file must contain nonempty UTF-8 without NUL bytes")
		}
		*prompt = text
	}
	// A selected task file goes to the child, never implicitly to the memory API.
	if *query == "" && !retainedRequested && !*browse && !hasFile {
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
	req := runner.Request{AttemptID: *attempt, Compile: core.CompileRequest{Kinds: kinds, RequestID: *request, Scope: core.Scope{Repo: *repo, TaskID: *task, RunID: *run}, Query: *query, Purpose: "context", AvailableTokens: *tokens}, Destination: core.Destination{Name: *dest, AllowLocal: *dest == "local"}, Command: command, Directory: *directory, Carrier: *carrier, Prompt: *prompt, Timeout: *timeout, TaskClass: *taskClass, TaskPhase: *taskPhase, BindingID: *binding, CapabilityID: *capability, Revision: *revision, WorkspaceSHA256: *workspace, ArtifactDirectory: filepath.Join(artifacts, "runs")}
	if *index {
		req.Compile.Mode = "index"
	}
	req.Compile.Semantic = *semantic
	if *browse {
		req.Compile.BrowseOffset = offset
	} else {
		f.Visit(func(fl *flag.Flag) {
			if fl.Name == "offset" {
				req.Compile.PageOffset = offset
			}
		})
	}
	req.Compile.ExpansionReader = *reader
	if *pullTool != "" || *searchTool != "" {
		req.IndexTools = &runner.IndexTools{Pull: *pullTool, Search: *searchTool}
	}
	req.OutputArtifacts, req.ShareArtifactEvidence = outputs, *shareArtifacts
	if retainedRequested {
		req.Retained = &core.RunPackageRequest{ReceiptID: *receipt, Seal: *seal}
	}
	result, err := runner.Run(ctx, s, req, os.Stdout, os.Stderr)
	if err != nil && core.Code(err) == "STORE_ERROR" && result.ReceiptID != "" {
		err = &core.Error{Code: "RUN_FAILED", Message: "wrapped task failed; inspect process_state and the run artifact directory", Cause: err}
	}
	return result, err
}
