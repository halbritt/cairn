// Package mcpapi exposes ordinary memory tools through the authenticated local API.
package mcpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type Config struct {
	Scope           core.Scope
	AvailableTokens int
	Context         *core.ContextPins
}

// Validate checks startup scope and room; the API validates pins on retrieval.
func (c Config) Validate() error {
	if strings.TrimSpace(c.Scope.Repo) == "" || strings.TrimSpace(c.Scope.TaskID) == "" || strings.TrimSpace(c.Scope.RunID) == "" || c.Scope.Repo == "*" || c.Scope.TaskID == "*" || c.Scope.RunID == "*" {
		return fmt.Errorf("MCP requires an explicit repository, task and run")
	}
	if c.AvailableTokens < 256 || c.AvailableTokens > 1000000 {
		return fmt.Errorf("MCP memory input room must be between 256 and 1000000")
	}
	return nil
}

type searchArgs struct {
	Query     string `json:"query" jsonschema:"Words describing the memory needed. Refine a query if it misses."`
	RequestID string `json:"request_id,omitempty" jsonschema:"Optional UUID for retrying the same search."`
}

type rememberArgs struct {
	RequestID string `json:"request_id" jsonschema:"A UUID chosen before capture; reuse exactly for retries of this note."`
	Body      string `json:"body" jsonschema:"Selected reusable knowledge with source and verification context. Never raw sessions or secrets."`
	Kind      string `json:"kind,omitempty" jsonschema:"Ordinary record kind; defaults to note."`
	Shareable bool   `json:"shareable,omitempty" jsonschema:"Explicitly allow this content to reach hosted models. Default false keeps it local."`
}

type recordWriteResult struct {
	RecordID  string `json:"record_id"`
	Version   int    `json:"version"`
	RequestID string `json:"request_id"`
}

type searchEntry struct {
	core.IndexEntry
	PullArguments core.ExpandRequest `json:"pull_arguments"`
}

type searchResult struct {
	core.SemanticPackage
	Schema           string        `json:"schema"`
	Index            []searchEntry `json:"index"`
	SourceSchema     string        `json:"source_schema"`
	SourceSeal       string        `json:"source_seal"`
	ReceiptID        string        `json:"receipt_id"`
	RequestID        string        `json:"request_id"`
	ExpiresAt        time.Time     `json:"expires_at"`
	CreditsRemaining int           `json:"credits_remaining"`
	BytesRemaining   int           `json:"bytes_remaining"`
}

// NewServer borrows client; its caller owns the connection and process lifetime.
// The API profile controls writer identity, role, repository and destination.
func NewServer(client *localapi.Client, config Config) (*mcp.Server, error) {
	if err := config.Validate(); err != nil {
		return nil, err
	}
	// Keep the host's startup declarations fixed even if it reuses its config.
	if config.Context != nil {
		pins := *config.Context
		config.Context = &pins
	}
	server := mcp.NewServer(&mcp.Implementation{Name: "cairn", Version: "1"}, nil)
	tools := memoryTools{client: client, config: config}
	destructive := true
	mcp.AddTool(server, &mcp.Tool{Annotations: &mcp.ToolAnnotations{DestructiveHint: new(bool), OpenWorldHint: new(bool)}, Name: "cairn_search", Description: "Search scoped memory. Read mandatory context in selected and inspect relevant index entries with cairn_pull using their complete pull_arguments. A notes are fallible; verify before relying on them. Search records exposure, not proven use."}, tools.search)
	mcp.AddTool(server, &mcp.Tool{Annotations: &mcp.ToolAnnotations{DestructiveHint: new(bool), OpenWorldHint: new(bool)}, Name: "cairn_pull", Description: "Retrieve a full memory body using the complete pull_arguments from cairn_search. Reuse those arguments for retries. Handles expire and stale records require a new search. This spends the search receipt's shared expansion budget."}, tools.pull)
	mcp.AddTool(server, &mcp.Tool{Annotations: &mcp.ToolAnnotations{DestructiveHint: new(bool), OpenWorldHint: new(bool)}, Name: "cairn_pull_evidence", Description: "Pull evidence referenced by an expanded memory, using its evidence ID and expected SHA256 plus the original receipt and handle. Supply a request UUID and reuse it for retries. Shares the same expansion budget."}, tools.pullEvidence)
	mcp.AddTool(server, &mcp.Tool{Annotations: &mcp.ToolAnnotations{DestructiveHint: new(bool), OpenWorldHint: new(bool)}, Name: "cairn_remember", Description: "Save an explicitly selected reusable repository note as ordinary A testimony, applicable across tasks and runs. Does not promote claims or grant authority. Choose shareable only for content suitable for hosted models; default local notes will not appear in hosted searches. Preserve the request UUID when retrying."}, tools.remember)
	mcp.AddTool(server, &mcp.Tool{Annotations: &mcp.ToolAnnotations{DestructiveHint: &destructive, OpenWorldHint: new(bool)}, Name: "cairn_edit", Description: "Revise a previously pulled active Class A note. Supply its record_id and expected_version, a new request_id UUID, and the complete replacement draft. Copy kind, scope, pins, sensitivity, relations and attribution fields from the pulled record; change only the intended content. Scope and sensitivity changes and privileged records are refused. The authenticated writer is recorded. Retry with exactly the same arguments; VERSION_CONFLICT requires a fresh search/pull and reconciliation, not blind overwrite. Returns identifiers without echoing the body."}, tools.edit)
	return server, nil
}

type memoryTools struct {
	client *localapi.Client
	config Config
}

func (t memoryTools) search(ctx context.Context, _ *mcp.CallToolRequest, args searchArgs) (*mcp.CallToolResult, any, error) {
	if strings.TrimSpace(args.Query) == "" {
		return nil, nil, errors.New("search requires a nonempty query")
	}
	if args.RequestID == "" {
		args.RequestID = uuid.NewString()
	}
	var index core.IndexResult
	err := t.client.Call(ctx, "index", core.CompileRequest{RequestID: args.RequestID, Scope: t.config.Scope, Query: args.Query, Purpose: "context", AvailableTokens: t.config.AvailableTokens, Context: t.config.Context}, &index)
	if err != nil {
		return toolResult(nil, err, t.config.AvailableTokens)
	}
	view, err := presentSearch(index, args.RequestID)
	return toolResult(view, err, t.config.AvailableTokens)
}

func (t memoryTools) pull(ctx context.Context, _ *mcp.CallToolRequest, args core.ExpandRequest) (*mcp.CallToolResult, any, error) {
	var result core.Expansion
	err := t.client.Call(ctx, "expand", args, &result)
	return toolResult(result, err, t.config.AvailableTokens)
}

func (t memoryTools) pullEvidence(ctx context.Context, _ *mcp.CallToolRequest, args core.ExpandEvidenceRequest) (*mcp.CallToolResult, any, error) {
	var result json.RawMessage
	err := t.client.Call(ctx, "expand-evidence", args, &result)
	return toolResult(result, err, t.config.AvailableTokens)
}

func (t memoryTools) remember(ctx context.Context, _ *mcp.CallToolRequest, args rememberArgs) (*mcp.CallToolResult, any, error) {
	if args.Kind == "" {
		args.Kind = "note"
	}
	sensitivity := "local"
	if args.Shareable {
		sensitivity = "shareable"
	}
	var result core.Record
	err := t.client.Call(ctx, "create", core.CreateRequest{RequestID: args.RequestID, Draft: core.Draft{Kind: args.Kind, Body: args.Body, Scope: core.Scope{Repo: t.config.Scope.Repo, TaskID: "*", RunID: "*"}, Sensitivity: sensitivity, ClaimType: "self"}}, &result)
	// Capture returns only its identifier and retry key; do not echo a large or
	// local-only body into the harness after the write has already committed.
	return toolResult(recordWriteResult{result.RecordID, result.Version, args.RequestID}, err, t.config.AvailableTokens)
}

func (t memoryTools) edit(ctx context.Context, _ *mcp.CallToolRequest, args core.EditRequest) (*mcp.CallToolResult, any, error) {
	if args.Draft.Scope.Repo != t.config.Scope.Repo {
		return toolResult(nil, &core.Error{Code: "AUTHORITY_DENIED", Message: "edit draft must use the configured repository"}, t.config.AvailableTokens)
	}
	var result core.Record
	err := t.client.Call(ctx, "edit", args, &result)
	return toolResult(recordWriteResult{result.RecordID, result.Version, args.RequestID}, err, t.config.AvailableTokens)
}

func presentSearch(result core.IndexResult, requestID string) (searchResult, error) {
	view := searchResult{SemanticPackage: result.Package.Semantic, Schema: "cairn.mcp-search/1", Index: []searchEntry{}, SourceSchema: result.Package.Semantic.Schema, SourceSeal: result.Package.Seal, ReceiptID: result.Package.ReceiptID, RequestID: requestID, ExpiresAt: result.ExpiresAt, CreditsRemaining: result.CreditsRemaining, BytesRemaining: result.BytesRemaining}
	type versionKey struct {
		id      string
		version int
	}
	handles := map[versionKey]string{}
	for _, h := range result.Handles {
		key := versionKey{h.RecordID, h.Version}
		if _, exists := handles[key]; exists || h.Handle == "" {
			return view, errors.New("index has duplicate or empty handles")
		}
		handles[key] = h.Handle
	}
	for _, entry := range result.Package.Semantic.Index {
		handle, ok := handles[versionKey{entry.RecordID, entry.Version}]
		if !ok {
			return view, errors.New("index has no handle for a record version")
		}
		view.Index = append(view.Index, searchEntry{entry, core.ExpandRequest{RequestID: uuid.NewString(), ReceiptID: result.Package.ReceiptID, Handle: handle}})
	}
	return view, nil
}

func toolResult(value any, err error, room int) (*mcp.CallToolResult, any, error) {
	if err != nil {
		var failure *core.Error
		if errors.As(err, &failure) {
			message := failure.Code + ": " + strings.TrimPrefix(failure.Message, failure.Code+": ")
			if failure.RefusalID != "" {
				message += "; refusal_id=" + failure.RefusalID
			}
			return nil, nil, errors.New(message)
		}
		return nil, nil, errors.New("Cairn operation failed; inspect the local API connection and service")
	}
	body, err := json.Marshal(value)
	if err != nil {
		return nil, nil, err
	}
	result := &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: string(body)}}}
	encoded, err := json.Marshal(result)
	if err != nil {
		return nil, nil, err
	}
	if len(encoded) > room {
		return nil, nil, errors.New("BUDGET_REFUSED: MCP tool result exceeds configured memory input room")
	}
	return result, nil, nil
}
