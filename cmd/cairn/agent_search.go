package main

import (
	"context"
	"encoding/json"
	"flag"
	"io"
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

type agentSearchOptions struct {
	inspection    *string
	kinds         []string
	entities      *[]core.EntityRef
	repo          *string
	task          *string
	run           *string
	request       *string
	tokens        *int
	memory        *int
	minPull       *int
	browse        *bool
	advisory      *bool
	signature     *string
	semantic      *bool
	semanticFile  *string
	semanticStdin *bool
	offset        *int
	revision      *string
	workspace     *string
	taskClass     *string
	taskPhase     *string
	binding       *string
	capability    *string
}

func newAgentSearchFlags() (*flag.FlagSet, *agentSearchOptions) {
	options := &agentSearchOptions{}
	f := flags("agent search")
	options.entities = entityFlags(f)
	options.repo = f.String("repo", defaultRepo(), "repository identity")
	f.Func("kind", "optional record kind; repeat for multiple labels (required instructions always apply)", func(value string) error { options.kinds = append(options.kinds, value); return nil })
	options.task = f.String("task", "", "host task identity (required)")
	options.run = f.String("run", "", "host run identity (required)")
	options.request = f.String("request-id", uuid.NewString(), "index retry identity")
	options.tokens = f.Int("tokens", 32000, "actual available input context in conservative UTF-8 bytes (not model capacity)")
	options.memory = f.Int("memory-budget-bytes", 0, "optional smaller search and receipt memory allowance (256 through --tokens); --tokens remains available context for policy")
	options.minPull = f.Int("min-pull-bytes", 0, "reserve 1..24000 charged expansion bytes within --memory-budget-bytes; not guaranteed body bytes or a successful pull")
	options.inspection = f.String("inspection-policy", "", "first-fitting-whole/2 uses declared room; /1 keeps the 24000-byte ceiling. Both reserve a complete source/group within --memory-budget-bytes; mutually exclusive with --min-pull-bytes")
	options.browse = f.Bool("browse", false, "browse eligible memory without a query (bounded by the memory budget)")
	options.advisory = f.Bool("advisory-conflicts", false, "include qualified competing advisory positions together; context retrieval only")
	options.signature = f.String("error-signature-sha256", "", "optional reviewed failure signature (SHA-256); a retrieval hint, not observed failure")
	options.semantic = f.Bool("semantic", false, "optional semantic discovery; labelled lexical fallback if unavailable")
	options.semanticFile = f.String("semantic-query-file", "", "bounded exact natural-language input file for distinct indexed semantic search; not raw text in argv")
	options.semanticStdin = f.Bool("semantic-query-stdin", false, "read bounded exact natural-language input from stdin; mutually exclusive with --semantic-query-file")
	options.offset = f.Int("offset", 0, "ranked search page offset (0 to start), or next browse offset")
	options.revision = f.String("revision", "", "declared repository revision")
	options.workspace = f.String("workspace-sha256", "", "workspace digest")
	options.taskClass = f.String("task-class", "", "task category")
	options.taskPhase = f.String("task-phase", "", "declared task phase (exact label)")
	options.binding = f.String("binding", "", "binding identity")
	options.capability = f.String("capability", "", "capability identity")
	return f, options
}

func agentSearch(ctx context.Context, client *localapi.Client, args []string, socket, tokenFile string, input io.Reader) (agentSearchView, error) {
	f, options := newAgentSearchFlags()
	if err := f.Parse(args); err != nil {
		return agentSearchView{}, invalid(err.Error())
	}
	query := strings.Join(f.Args(), " ")
	fileSelected := false
	var offset, memoryBudget, minPull *int
	f.Visit(func(fl *flag.Flag) {
		switch fl.Name {
		case "semantic-query-file":
			fileSelected = true
		case "memory-budget-bytes":
			memoryBudget = options.memory
		case "min-pull-bytes":
			minPull = options.minPull
		case "offset":
			offset = options.offset
		}
	})
	semanticQuery, err := readSemanticQuery(query, *options.semanticFile, fileSelected, *options.semanticStdin, input)
	if err != nil {
		return agentSearchView{}, err
	}
	prepared, err := indexview.PrepareSearch(indexview.SearchInput{
		Scope:                core.Scope{Repo: *options.repo, TaskID: *options.task, RunID: *options.run},
		RequireExplicitScope: true,
		Query:                query,
		Semantic:             *options.semantic,
		SemanticQuery:        semanticQuery,
		Browse:               *options.browse,
		Offset:               offset,
		AvailableTokens:      *options.tokens,
		MemoryBudgetBytes:    memoryBudget,
		MinPullBytes:         minPull,
		InspectionPolicy:     *options.inspection,
		AdvisoryConflicts:    *options.advisory,
		Entities:             *options.entities,
		ErrorSignature:       *options.signature,
		Kinds:                options.kinds,
		RequestID:            *options.request,
		Context:              &core.ContextPins{Revision: *options.revision, WorkspaceSHA256: *options.workspace, TaskClass: *options.taskClass, TaskPhase: *options.taskPhase, BindingID: *options.binding, CapabilityID: *options.capability},
	})
	if err != nil {
		return agentSearchView{}, err
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
	return indexview.RetrieveCLI(ctx, indexview.NewClientTransport(client, nil), prepared, []string{executable, "agent", "--socket", socket, "--token-file", tokenFile})
}

func presentAgentSearch(result core.IndexResult, request string, command []string, room int) (agentSearchView, error) {
	return indexview.Present(result, request, command, room)
}

func shellCommand(args []string) string { return indexview.ShellCommand(args) }

type agentPullOptions struct {
	request *string
	offset  *int
	length  *int
	opening *int
}

func newAgentPullFlags(operation string) (*flag.FlagSet, *agentPullOptions) {
	options := &agentPullOptions{}
	f := flags("agent " + operation)
	options.request = f.String("request-id", uuid.NewString(), "pull retry identity")
	options.offset = f.Int("offset", 0, "source byte offset (requires --length)")
	options.length = f.Int("length", 0, "maximum source bytes to return; clipped at EOF")
	if operation == "pull" {
		options.opening = f.Int("opening-bytes", 0, "override source opening length (1-768); zero uses receipt policy; requires --length")
	}
	return f, options
}

func agentPull(ctx context.Context, client *localapi.Client, operation string, args []string) (json.RawMessage, error) {
	f, options := newAgentPullFlags(operation)
	if err := f.Parse(args); err != nil {
		return nil, invalid(err.Error())
	}
	var payload any
	var span *core.ByteSpanRequest
	f.Visit(func(value *flag.Flag) {
		if value.Name == "offset" || value.Name == "length" {
			span = &core.ByteSpanRequest{Offset: *options.offset, Length: *options.length}
		}
	})
	endpoint := "expand"
	if operation == "pull" {
		if f.NArg() != 2 {
			return nil, invalid("agent pull requires RECEIPT_UUID HANDLE_UUID")
		}
		payload = core.ExpandRequest{RequestID: *options.request, ReceiptID: f.Arg(0), Handle: f.Arg(1), Span: span, OpeningBytes: *options.opening}
	} else {
		if f.NArg() != 4 {
			return nil, invalid("agent pull-evidence requires RECEIPT_UUID HANDLE_UUID EVIDENCE_UUID EXPECTED_SHA256")
		}
		endpoint = "expand-evidence"
		payload = core.ExpandEvidenceRequest{RequestID: *options.request, ReceiptID: f.Arg(0), Handle: f.Arg(1), EvidenceID: f.Arg(2), ExpectedSHA256: f.Arg(3), Span: span}
	}
	var result json.RawMessage
	err := client.Call(ctx, endpoint, payload, &result)
	return result, err
}
