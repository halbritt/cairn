package main

import (
	"context"
	"encoding/json"
	"flag"
	"os"
	"path/filepath"
	"strings"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/indexview"
	"github.com/halbritt/cairn/localapi"
)

type agentSearchEntry = indexview.Entry
type agentSearchView = indexview.View

func agentSearch(ctx context.Context, client *localapi.Client, args []string, socket, tokenFile string) (agentSearchView, error) {
	var kinds []string
	f := flags("agent search")
	entities := entityFlags(f)
	repo := f.String("repo", defaultRepo(), "repository identity")
	f.Func("kind", "optional record kind; repeat for multiple labels (required instructions always apply)", func(value string) error { kinds = append(kinds, value); return nil })
	task := f.String("task", "", "host task identity (required)")
	run := f.String("run", "", "host run identity (required)")
	request := f.String("request-id", uuid.NewString(), "index retry identity")
	tokens := f.Int("tokens", 32000, "available memory input room")
	browse := f.Bool("browse", false, "browse eligible memory without a query (bounded by the memory budget)")
	signature := f.String("error-signature-sha256", "", "optional reviewed failure signature (SHA-256); a retrieval hint, not observed failure")
	semantic := f.Bool("semantic", false, "optional semantic discovery; labelled lexical fallback if unavailable")
	offset := f.Int("offset", 0, "ranked search page offset (0 to start), or next browse offset")
	revision := f.String("revision", "", "declared repository revision")
	workspace := f.String("workspace-sha256", "", "workspace digest")
	taskClass := f.String("task-class", "", "task category")
	taskPhase := f.String("task-phase", "", "declared task phase (exact label)")
	binding := f.String("binding", "", "binding identity")
	capability := f.String("capability", "", "capability identity")
	if err := f.Parse(args); err != nil {
		return agentSearchView{}, invalid(err.Error())
	}
	query := strings.Join(f.Args(), " ")
	if *semantic && *browse {
		return agentSearchView{}, invalid("semantic discovery cannot be combined with browsing")
	}
	if strings.TrimSpace(*task) == "" || strings.TrimSpace(*run) == "" || *task == "*" || *run == "*" {
		return agentSearchView{}, invalid("agent search requires explicit --task and --run")
	}
	if (*browse && (query != "" || *signature != "" || len(*entities) > 0)) || (!*browse && strings.TrimSpace(query) == "" && *signature == "" && len(*entities) == 0) {
		return agentSearchView{}, invalid("agent search requires a query, entity hints, --error-signature-sha256, or --browse without search hints")
	}
	if *offset < 0 || *offset > 10000 {
		return agentSearchView{}, invalid("offset must be 0-10000")
	}
	var browseOffset, pageOffset *int
	if *browse {
		browseOffset = offset
	} else {
		f.Visit(func(fl *flag.Flag) {
			if fl.Name == "offset" {
				pageOffset = offset
			}
		})
	}
	executable, err := os.Executable()
	if err != nil {
		return agentSearchView{}, err
	}
	socket, err = filepath.Abs(socket)
	if err != nil {
		return agentSearchView{}, err
	}
	tokenFile, err = filepath.Abs(tokenFile)
	if err != nil {
		return agentSearchView{}, err
	}
	var result core.IndexResult
	if err = client.Call(ctx, "index", core.CompileRequest{Entities: *entities, ErrorSignature: *signature, Kinds: kinds, RequestID: *request, BrowseOffset: browseOffset, PageOffset: pageOffset, Semantic: *semantic,
		Scope: core.Scope{Repo: *repo, TaskID: *task, RunID: *run}, Query: query, Purpose: "context", AvailableTokens: *tokens,
		Context: &core.ContextPins{Revision: *revision, WorkspaceSHA256: *workspace, TaskClass: *taskClass, TaskPhase: *taskPhase, BindingID: *binding, CapabilityID: *capability}}, &result); err != nil {
		return agentSearchView{}, err
	}
	return presentAgentSearch(result, *request, []string{executable, "agent", "--socket", socket, "--token-file", tokenFile}, *tokens)
}

func presentAgentSearch(result core.IndexResult, request string, command []string, room int) (agentSearchView, error) {
	return indexview.Present(result, request, command, room)
}

func shellCommand(args []string) string { return indexview.ShellCommand(args) }

func agentPull(ctx context.Context, client *localapi.Client, operation string, args []string) (json.RawMessage, error) {
	f := flags("agent " + operation)
	request := f.String("request-id", uuid.NewString(), "pull retry identity")
	var offset, length int
	f.IntVar(&offset, "offset", 0, "source byte offset (requires --length)")
	f.IntVar(&length, "length", 0, "maximum source bytes to return; clipped at EOF")
	if err := f.Parse(args); err != nil {
		return nil, invalid(err.Error())
	}
	var payload any
	var span *core.ByteSpanRequest
	f.Visit(func(value *flag.Flag) {
		if value.Name == "offset" || value.Name == "length" {
			span = &core.ByteSpanRequest{Offset: offset, Length: length}
		}
	})
	endpoint := "expand"
	if operation == "pull" {
		if f.NArg() != 2 {
			return nil, invalid("agent pull requires RECEIPT_UUID HANDLE_UUID")
		}
		payload = core.ExpandRequest{RequestID: *request, ReceiptID: f.Arg(0), Handle: f.Arg(1), Span: span}
	} else {
		if f.NArg() != 4 {
			return nil, invalid("agent pull-evidence requires RECEIPT_UUID HANDLE_UUID EVIDENCE_UUID EXPECTED_SHA256")
		}
		endpoint = "expand-evidence"
		payload = core.ExpandEvidenceRequest{RequestID: *request, ReceiptID: f.Arg(0), Handle: f.Arg(1), EvidenceID: f.Arg(2), ExpectedSHA256: f.Arg(3), Span: span}
	}
	var result json.RawMessage
	err := client.Call(ctx, endpoint, payload, &result)
	return result, err
}
