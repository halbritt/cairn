// Package mcpapi exposes ordinary memory tools through the authenticated local API.
package mcpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
	"unicode"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type Config struct {
	Scope           core.Scope
	AvailableTokens int
	Context         *core.ContextPins
	CodexThread     bool
}

// Validate checks startup scope and room; the API validates pins on retrieval.
func (c Config) Validate() error {
	if strings.TrimSpace(c.Scope.Repo) == "" || c.Scope.Repo == "*" {
		return fmt.Errorf("MCP requires an explicit repository")
	}
	if c.CodexThread {
		if c.Scope.TaskID != "" || c.Scope.RunID != "" {
			return fmt.Errorf("--codex-thread cannot be combined with --task or --run")
		}
	} else if strings.TrimSpace(c.Scope.TaskID) == "" || strings.TrimSpace(c.Scope.RunID) == "" || c.Scope.TaskID == "*" || c.Scope.RunID == "*" {
		return fmt.Errorf("MCP requires an explicit repository, task and run")
	}
	if c.AvailableTokens < 256 || c.AvailableTokens > 1000000 {
		return fmt.Errorf("MCP memory input room must be between 256 and 1000000")
	}
	return nil
}

type searchArgs struct {
	Kinds     []string `json:"kinds,omitempty" jsonschema:"Optional labels: note, observation, claim, lesson, procedure, decision, preference, instruction. Matches any listed label; empty means all. Required instructions always apply. Labels do not establish authority."`
	Semantic  bool     `json:"semantic,omitempty" jsonschema:"Optional semantic discovery for vocabulary mismatches. Requires a query, cannot browse. Similarity is not answer confidence; an unavailable backend returns labelled lexical fallback."`
	Query     string   `json:"query,omitempty" jsonschema:"Words describing the memory needed. Omit only when browse is true."`
	Browse    bool     `json:"browse,omitempty" jsonschema:"Browse eligible memory without a query when its vocabulary is unknown. Results are bounded, ordered by scope and recency, and may omit older notes."`
	Offset    int      `json:"offset,omitempty" jsonschema:"For browsing, pass the preceding result's browse.next_offset to continue with the same scope and budget. Default 0. Pages read current state; restart if notes change."`
	RequestID string   `json:"request_id,omitempty" jsonschema:"Optional UUID for retrying the same search."`
}

type rememberArgs struct {
	Pins      *core.Applicability `json:"pins,omitempty" jsonschema:"Explicit applicability restrictions. Omit for reusable unpinned guidance. Never inherited from search context. All supplied pins must match; edits cannot change them."`
	RequestID string              `json:"request_id" jsonschema:"A UUID chosen before capture; reuse exactly for retries of this note."`
	Body      string              `json:"body" jsonschema:"Selected reusable knowledge with source and verification context. Never raw sessions or secrets."`
	Kind      string              `json:"kind,omitempty" jsonschema:"Ordinary record kind; defaults to note."`
	Shareable bool                `json:"shareable,omitempty" jsonschema:"Explicitly allow this content to reach hosted models. Default false keeps it local."`
}

type editArgs struct {
	RequestID       string      `json:"request_id"`
	RecordID        string      `json:"record_id"`
	ExpectedVersion int         `json:"expected_version"`
	Body            *string     `json:"body,omitempty" jsonschema:"New body only; preserves all stored draft metadata. Supply body or draft, never both."`
	Draft           *core.Draft `json:"draft,omitempty" jsonschema:"Complete replacement draft for edits beyond the body. Omit when supplying body."`
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
	mcp.AddTool(server, &mcp.Tool{Annotations: &mcp.ToolAnnotations{DestructiveHint: new(bool), OpenWorldHint: new(bool)}, Name: "cairn_search", Description: "Search scoped memory with a query, or set browse=true without a query to inspect available topics. Browsing is bounded by the same budget and is not a complete inventory or relevance ranking. Read mandatory context in selected and inspect relevant index entries with cairn_pull using their complete pull_arguments. A notes are fallible; verify before relying on them. Search records exposure, not proven use."}, tools.search)
	mcp.AddTool(server, &mcp.Tool{Annotations: &mcp.ToolAnnotations{DestructiveHint: new(bool), OpenWorldHint: new(bool)}, Name: "cairn_pull", Description: "Pull a memory body using complete pull_arguments from cairn_search. Optional span selects byte offset and maximum length for a partial A/B source; bytes and hashes appear in span with record.body empty. Copy an index entry's summary_span into span to read its exact preview source bytes without omission markers. Instructions require a whole pull. Use a new request UUID for a different range. A stale handle requires a fresh search. Shares the receipt's expansion budget. Read the complete note before replacing its body."}, tools.pull)
	mcp.AddTool(server, &mcp.Tool{Annotations: &mcp.ToolAnnotations{DestructiveHint: new(bool), OpenWorldHint: new(bool)}, Name: "cairn_pull_evidence", Description: "Pull evidence referenced by an expanded memory, using its evidence ID and full-object expected SHA256 plus the original receipt and handle. Optional span selects byte offset and maximum length, clipped at EOF; selected bytes and their checksum appear in span. Reuse a request UUID only for identical retries. Shares the same expansion budget."}, tools.pullEvidence)
	mcp.AddTool(server, &mcp.Tool{Annotations: &mcp.ToolAnnotations{DestructiveHint: new(bool), OpenWorldHint: new(bool)}, Name: "cairn_remember", Description: "Save an explicitly selected reusable repository note as ordinary A testimony, applicable across tasks and runs. Does not promote claims or grant authority. Choose shareable only for content suitable for hosted models; default local notes will not appear in hosted searches. Preserve the request UUID when retrying."}, tools.remember)
	mcp.AddTool(server, &mcp.Tool{Annotations: &mcp.ToolAnnotations{DestructiveHint: &destructive, OpenWorldHint: new(bool)}, Name: "cairn_edit", Description: "Revise a previously pulled active Class A note. Supply its record_id and expected_version, a new request_id UUID, and body to change only the text while preserving all stored metadata. Alternatively supply the complete replacement draft, never both body and draft. For a full draft, copy kind, scope, pins, sensitivity, relations and attribution fields from the pulled record; change only the intended content. Scope and sensitivity changes and privileged records are refused. The authenticated writer is recorded. Retry with exactly the same arguments; VERSION_CONFLICT requires a fresh search/pull and reconciliation, not blind overwrite. Returns identifiers without echoing the body."}, tools.edit)
	return server, nil
}

type memoryTools struct {
	client *localapi.Client
	config Config
}

func (t memoryTools) search(ctx context.Context, request *mcp.CallToolRequest, args searchArgs) (*mcp.CallToolResult, any, error) {
	if args.Semantic && args.Browse {
		return nil, nil, fmt.Errorf("semantic discovery cannot be combined with browsing")
	}
	if (args.Browse && args.Query != "") || (!args.Browse && strings.TrimSpace(args.Query) == "") {
		return nil, nil, errors.New("search requires a nonempty query or browse=true without a query")
	}
	if args.Offset < 0 || args.Offset > 10000 || (!args.Browse && args.Offset != 0) {
		return nil, nil, errors.New("offset must be 0-10000 and requires browse=true")
	}
	var browseOffset *int
	if args.Browse {
		browseOffset = &args.Offset
	}
	scope := t.config.Scope
	if t.config.CodexThread {
		thread, _ := request.Params.Meta["threadId"].(string)
		if thread == "" || thread == "*" || len(thread) > 240 || strings.ContainsFunc(thread, func(r rune) bool { return unicode.IsSpace(r) || unicode.IsControl(r) }) {
			return nil, nil, errors.New("--codex-thread requires tool-call _meta.threadId: a nonempty identifier of at most 240 bytes without whitespace, control characters or wildcard")
		}
		// Host-declared conversation grouping, not an authenticated run observation.
		scope.TaskID, scope.RunID = "codex/"+thread, thread
	}
	if args.RequestID == "" {
		args.RequestID = uuid.NewString()
	}
	var index core.IndexResult
	err := t.client.Call(ctx, "index", core.CompileRequest{Kinds: args.Kinds, RequestID: args.RequestID, BrowseOffset: browseOffset, Semantic: args.Semantic, Scope: scope, Query: args.Query, Purpose: "context", AvailableTokens: t.config.AvailableTokens, Context: t.config.Context}, &index)
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
	err := t.client.Call(ctx, "create", core.CreateRequest{RequestID: args.RequestID, Draft: core.Draft{Kind: args.Kind, Body: args.Body, Scope: core.Scope{Repo: t.config.Scope.Repo, TaskID: "*", RunID: "*"}, Pins: args.Pins, Sensitivity: sensitivity, ClaimType: "self"}}, &result)
	// Capture returns only its identifier and retry key; do not echo a large or
	// local-only body into the harness after the write has already committed.
	return toolResult(recordWriteResult{result.RecordID, result.Version, args.RequestID}, err, t.config.AvailableTokens)
}

func (t memoryTools) edit(ctx context.Context, _ *mcp.CallToolRequest, args editArgs) (*mcp.CallToolResult, any, error) {
	if (args.Body == nil) == (args.Draft == nil) {
		return toolResult(nil, &core.Error{Code: "INVALID_REQUEST", Message: "supply exactly one of body or draft"}, t.config.AvailableTokens)
	}
	if args.Body != nil {
		var result core.Revision
		err := t.client.Call(ctx, "revise", core.ReviseRequest{RequestID: args.RequestID, RecordID: args.RecordID, ExpectedVersion: args.ExpectedVersion, Repo: t.config.Scope.Repo, Body: *args.Body}, &result)
		return toolResult(recordWriteResult{result.RecordID, result.Version, args.RequestID}, err, t.config.AvailableTokens)
	}
	if args.Draft.Scope.Repo != t.config.Scope.Repo {
		return toolResult(nil, &core.Error{Code: "AUTHORITY_DENIED", Message: "edit draft must use the configured repository"}, t.config.AvailableTokens)
	}
	var result core.Record
	err := t.client.Call(ctx, "edit", core.EditRequest{RequestID: args.RequestID, RecordID: args.RecordID, ExpectedVersion: args.ExpectedVersion, Draft: *args.Draft}, &result)
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
