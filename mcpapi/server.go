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
	"github.com/halbritt/cairn/internal/buildinfo"
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
	AvailableTokens   *int              `json:"available_tokens,omitempty" jsonschema:"Optional memory input room for this search, in conservative UTF-8 bytes. At least 256 and no greater than the configured host ceiling. Omit for the host default. Repeat on retries and pages; changed room requires a new request UUID. Does not measure or enforce whole-conversation context usage."`
	AdvisoryConflicts bool              `json:"advisory_conflicts,omitempty" jsonschema:"Opt in to qualified competing advisory positions. All positions must be eligible together; otherwise the whole group is omitted. Pulling a marked position returns its complete competing positions under the shared budget. Repeat on retries and later pages. Does not resolve disagreement or change authority."`
	Entities          []core.EntityRef  `json:"entities,omitempty" jsonschema:"Explicit file or symbol retrieval hints within this repository, at most 16. File names are canonical relative paths; symbol names are qualified labels. Exact kind and case-sensitive name overlap prefers associated notes. May replace query text; cannot browse. Hints do not establish scope, authority or observed workspace state. Repeat on later pages."`
	Context           *core.ContextPins `json:"context,omitempty" jsonschema:"Context declared for this search only: revision, workspace_sha256, task_class, task_phase, binding_id, capability_id. May fill fields the host left unset; conflicts with configured values are refused. Observe actual task state before declaring it. This does not certify execution or change repository/session scope. Repeat the same context on later pages."`
	ErrorSignature    string            `json:"error_signature_sha256,omitempty" jsonschema:"Optional SHA-256 of a known failure signature. Prefers an eligible exact lesson version linked by an explicitly shareable operator review. Does not establish current failure or correctness. May replace the query; cannot browse. Semantic discovery still requires query text."`
	Kinds             []string          `json:"kinds,omitempty" jsonschema:"Optional labels: note, observation, claim, lesson, procedure, decision, preference, instruction. Matches any listed label; empty means all. Required instructions always apply. Labels do not establish authority."`
	Semantic          bool              `json:"semantic,omitempty" jsonschema:"Optional semantic discovery for vocabulary mismatches. Requires a query, cannot browse. Similarity is not answer confidence; an unavailable backend returns labelled lexical fallback."`
	Query             string            `json:"query,omitempty" jsonschema:"Words describing the memory needed. ASCII double quotes prefer exact case-sensitive text in a note; other lexical matches remain available. Omit when browse is true or a failure signature is supplied."`
	Browse            bool              `json:"browse,omitempty" jsonschema:"Browse eligible memory without a query when its vocabulary is unknown. Results are bounded, ordered by scope and recency, and may omit older notes."`
	Offset            *int              `json:"offset,omitempty" jsonschema:"Set 0 to start ranked pagination, then pass page.next_offset with the same query, semantic mode, kinds and scope. Browsing uses browse.next_offset. Pages read current state and each has its own budget; restart if notes change."`
	RequestID         string            `json:"request_id,omitempty" jsonschema:"Optional UUID for retrying the same search."`
}

type rememberArgs struct {
	Entities  []core.EntityRef    `json:"entities,omitempty" jsonschema:"Explicit file or symbol associations, at most 16. File names are canonical repository-relative paths; symbols are qualified labels. Omit when unknown. Fallible relevance metadata, never inherited from search context."`
	Pins      *core.Applicability `json:"pins,omitempty" jsonschema:"Explicit applicability restrictions. Omit for reusable unpinned guidance. Never inherited from search context. All supplied pins must match; edits cannot change them."`
	RequestID string              `json:"request_id" jsonschema:"A UUID chosen before capture; reuse exactly for retries of this note."`
	Body      string              `json:"body" jsonschema:"Selected reusable knowledge with source and verification context. Never raw sessions or secrets."`
	Kind      string              `json:"kind,omitempty" jsonschema:"Ordinary record kind; defaults to note."`
	Shareable bool                `json:"shareable,omitempty" jsonschema:"Explicitly allow this content to reach hosted models. Default false keeps it local."`
}

type historyArgs struct {
	RecordID      string                `json:"record_id"`
	Version       int                   `json:"version,omitempty" jsonschema:"Positive version for one exact body. Omit or zero for metadata pages."`
	BeforeVersion int                   `json:"before_version,omitempty" jsonschema:"Exclusive metadata cursor from next_before_version."`
	Limit         int                   `json:"limit,omitempty" jsonschema:"Metadata page size, maximum 100. Omitted or zero defaults to 20."`
	Span          *core.ByteSpanRequest `json:"span,omitempty" jsonschema:"Optional byte excerpt of an exact version. Offset 0-65535, length 1-65536; clipped only at EOF. Result omits full body and includes span bytes and checksum."`
}

type textReplacement struct {
	OldText string  `json:"old_text" jsonschema:"Nonempty exact passage, which must occur exactly once (including overlapping occurrences). Supply surrounding text to disambiguate."`
	NewText *string `json:"new_text" jsonschema:"Exact replacement; an explicit empty string removes the passage if the note remains nonblank."`
}

type editArgs struct {
	Replace           *textReplacement                `json:"replace,omitempty" jsonschema:"Correct one exact passage while preserving all other text and metadata. Exclusive with other edit modes."`
	EvidenceCitations *[]core.EvidenceCitationRequest `json:"evidence_citations,omitempty" jsonschema:"Replace source citations only; explicit empty list clears current references. Supply exactly one of body, append, replace, draft or evidence_citations. Requires captured source IDs and full-source digests. Citations remain testimony, not qualification."`
	RequestID         string                          `json:"request_id"`
	RecordID          string                          `json:"record_id"`
	ExpectedVersion   int                             `json:"expected_version"`
	Body              *string                         `json:"body,omitempty" jsonschema:"New body only; preserves all stored draft metadata. Supply exactly one of body, append, replace, draft or evidence_citations."`
	Append            *string                         `json:"append,omitempty" jsonschema:"Suffix added verbatim, including caller-supplied whitespace; preserves existing body and metadata. Combined body must fit 65536 bytes. Exclusive with other edit modes."`
	Draft             *core.Draft                     `json:"draft,omitempty" jsonschema:"Complete replacement draft for edits beyond the body. Omit when supplying body, append, replace or evidence_citations."`
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
	server := mcp.NewServer(&mcp.Implementation{Name: "cairn", Version: buildinfo.Read().Label()}, nil)
	tools := memoryTools{client: client, config: config}
	destructive := true
	mcp.AddTool(server, &mcp.Tool{Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true, DestructiveHint: new(bool), OpenWorldHint: new(bool)}, Name: "cairn_history", Description: "Inspect retained versions of a known record for comparison. Omit version to list newest-first metadata (limit defaults to 20, maximum 100); follow next_before_version as before_version. Supply a positive version for one exact body, without nonzero paging fields. Optional span selects a byte excerpt of that version and omits the full body; UTF-8 fragments use body_base64. Results are historical, not current eligibility or authority; pull the current note before editing. The authenticated profile controls repository and destination; forgotten or excluded payloads refuse. No request UUID or expansion handle is needed. This read has its own output budget and does not spend index expansion credits; budget combined context across calls."}, tools.history)
	mcp.AddTool(server, &mcp.Tool{Annotations: &mcp.ToolAnnotations{DestructiveHint: new(bool), OpenWorldHint: new(bool)}, Name: "cairn_search", Description: "Search scoped memory with a query, or set browse=true without a query to inspect available topics. Browsing is bounded by the same budget and is not a complete inventory or relevance ranking. Read mandatory context in selected and inspect relevant index entries with cairn_pull using their complete pull_arguments. A notes are fallible; verify before relying on them. Search records exposure, not proven use."}, tools.search)
	mcp.AddTool(server, &mcp.Tool{Annotations: &mcp.ToolAnnotations{DestructiveHint: new(bool), OpenWorldHint: new(bool)}, Name: "cairn_pull", Description: "Pull a memory body using complete pull_arguments from cairn_search. Optional span selects byte offset and maximum length for a partial A/B source; bytes and hashes appear in span with record.body empty. Copy an index entry's summary_span into span to read its exact preview source bytes without omission markers. Instructions and marked competing positions require a whole pull. A marked pull returns the requested selection plus competing positions; read all of them. Use a new request UUID for a different range. A stale handle requires a fresh search. Shares the receipt's expansion budget. Read the complete note before replacing its body."}, tools.pull)
	mcp.AddTool(server, &mcp.Tool{Annotations: &mcp.ToolAnnotations{DestructiveHint: new(bool), OpenWorldHint: new(bool)}, Name: "cairn_pull_evidence", Description: "Pull evidence referenced by an expanded memory, using its evidence ID and full-object expected SHA256 plus the original receipt and handle. Optional span selects byte offset and maximum length, clipped at EOF; selected bytes and their checksum appear in span. Reuse a request UUID only for identical retries. Shares the same expansion budget."}, tools.pullEvidence)
	mcp.AddTool(server, &mcp.Tool{Annotations: &mcp.ToolAnnotations{DestructiveHint: new(bool), OpenWorldHint: new(bool)}, Name: "cairn_remember", Description: "Save an explicitly selected reusable repository note as ordinary A testimony, applicable across tasks and runs. Does not promote claims or grant authority. Choose shareable only for content suitable for hosted models; default local notes will not appear in hosted searches. Preserve the request UUID when retrying."}, tools.remember)
	mcp.AddTool(server, &mcp.Tool{Annotations: &mcp.ToolAnnotations{DestructiveHint: &destructive, OpenWorldHint: new(bool)}, Name: "cairn_edit", Description: "Revise a previously pulled active Class A note. Supply its record_id and expected_version, a new request_id UUID, and body to change only the text while preserving all stored metadata. For an additive update, supply append with the exact suffix, including separating whitespace; existing text is preserved and the combined body must fit 65536 bytes. For a targeted correction, supply replace with old_text and new_text; old_text must occur exactly once, and all other text is preserved. Alternatively supply the complete replacement draft or evidence_citations to replace source references ([] clears them). Supply exactly one of body, append, replace, draft or evidence_citations. Text edits preserve citations and earlier versions retain their sources. Citations remain testimony, not qualification. For a full draft, copy kind, scope, pins, entities, sensitivity, relations and attribution fields from the pulled record; change only the intended content. Scope and sensitivity changes and privileged records are refused. The authenticated writer is recorded. Retry with exactly the same arguments; VERSION_CONFLICT requires a fresh search/pull and reconciliation, not blind overwrite. Returns identifiers without echoing the body."}, tools.edit)
	return server, nil
}

type memoryTools struct {
	client *localapi.Client
	config Config
}

func (t memoryTools) search(ctx context.Context, request *mcp.CallToolRequest, args searchArgs) (*mcp.CallToolResult, any, error) {
	room := t.config.AvailableTokens
	if args.AvailableTokens != nil {
		if *args.AvailableTokens < 256 || *args.AvailableTokens > room {
			return nil, nil, fmt.Errorf("INVALID_REQUEST: available_tokens must be between 256 and configured ceiling %d", room)
		}
		room = *args.AvailableTokens
	}
	if args.Semantic && args.Browse {
		return nil, nil, fmt.Errorf("semantic discovery cannot be combined with browsing")
	}
	if (args.Browse && (args.Query != "" || args.ErrorSignature != "" || len(args.Entities) > 0)) || (!args.Browse && strings.TrimSpace(args.Query) == "" && args.ErrorSignature == "" && len(args.Entities) == 0) {
		return nil, nil, errors.New("search requires a query, entities, error_signature_sha256, or browse=true without search hints")
	}
	if args.Offset != nil && (*args.Offset < 0 || *args.Offset > 10000) {
		return nil, nil, errors.New("offset must be 0-10000")
	}
	var browseOffset, pageOffset *int
	if args.Browse {
		browseOffset = args.Offset
		if browseOffset == nil {
			browseOffset = new(int)
		}
	} else {
		pageOffset = args.Offset
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
	declared, err := searchContext(t.config.Context, args.Context)
	if err != nil {
		return nil, nil, err
	}
	var index core.IndexResult
	err = t.client.Call(ctx, "index", core.CompileRequest{AdvisoryConflicts: args.AdvisoryConflicts, Entities: args.Entities, ErrorSignature: args.ErrorSignature, Kinds: args.Kinds, RequestID: args.RequestID, BrowseOffset: browseOffset, PageOffset: pageOffset, Semantic: args.Semantic, Scope: scope, Query: args.Query, Purpose: "context", AvailableTokens: room, Context: declared}, &index)
	if err != nil {
		return toolResult(nil, err, room)
	}
	view, err := presentSearch(index, args.RequestID)
	return toolResult(view, err, room)
}

func searchContext(fixed, supplied *core.ContextPins) (*core.ContextPins, error) {
	if supplied == nil {
		return fixed, nil
	}
	merged := core.ContextPins{}
	if fixed != nil {
		merged = *fixed
	}
	for _, field := range []struct {
		name   string
		value  string
		target *string
	}{
		{"revision", supplied.Revision, &merged.Revision},
		{"workspace_sha256", supplied.WorkspaceSHA256, &merged.WorkspaceSHA256},
		{"task_class", supplied.TaskClass, &merged.TaskClass},
		{"task_phase", supplied.TaskPhase, &merged.TaskPhase},
		{"binding_id", supplied.BindingID, &merged.BindingID},
		{"capability_id", supplied.CapabilityID, &merged.CapabilityID},
	} {
		if field.value == "" {
			continue
		}
		if *field.target != "" && *field.target != field.value {
			return nil, fmt.Errorf("INVALID_REQUEST: context.%s conflicts with configured context", field.name)
		}
		*field.target = field.value
	}
	return &merged, nil
}

func (t memoryTools) pull(ctx context.Context, _ *mcp.CallToolRequest, args core.ExpandRequest) (*mcp.CallToolResult, any, error) {
	var result core.Expansion
	err := t.client.Call(ctx, "expand", args, &result)
	return toolResult(result, err, t.config.AvailableTokens)
}

func (t memoryTools) history(ctx context.Context, _ *mcp.CallToolRequest, args historyArgs) (*mcp.CallToolResult, any, error) {
	var result core.RecordHistory
	err := t.client.Call(ctx, "history", core.RecordHistoryRequest{RecordID: args.RecordID, Repo: t.config.Scope.Repo, Version: args.Version, BeforeVersion: args.BeforeVersion, Limit: args.Limit, Span: args.Span}, &result)
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
	err := t.client.Call(ctx, "create", core.CreateRequest{RequestID: args.RequestID, Draft: core.Draft{Entities: args.Entities, Kind: args.Kind, Body: args.Body, Scope: core.Scope{Repo: t.config.Scope.Repo, TaskID: "*", RunID: "*"}, Pins: args.Pins, Sensitivity: sensitivity, ClaimType: "self"}}, &result)
	// Capture returns only its identifier and retry key; do not echo a large or
	// local-only body into the harness after the write has already committed.
	return toolResult(recordWriteResult{result.RecordID, result.Version, args.RequestID}, err, t.config.AvailableTokens)
}

func (t memoryTools) edit(ctx context.Context, _ *mcp.CallToolRequest, args editArgs) (*mcp.CallToolResult, any, error) {
	choices := 0
	for _, supplied := range []bool{args.Body != nil, args.Append != nil, args.Replace != nil, args.Draft != nil, args.EvidenceCitations != nil} {
		if supplied {
			choices++
		}
	}
	if choices != 1 {
		return toolResult(nil, &core.Error{Code: "INVALID_REQUEST", Message: "supply exactly one of body, append, replace, draft or evidence_citations"}, t.config.AvailableTokens)
	}
	if args.EvidenceCitations != nil {
		var result core.Revision
		err := t.client.Call(ctx, "cite", core.CiteRequest{RequestID: args.RequestID, RecordID: args.RecordID, ExpectedVersion: args.ExpectedVersion, Repo: t.config.Scope.Repo, EvidenceCitations: *args.EvidenceCitations}, &result)
		return toolResult(recordWriteResult{result.RecordID, result.Version, args.RequestID}, err, t.config.AvailableTokens)
	}
	if args.Replace != nil {
		var result core.Revision
		err := t.client.Call(ctx, "replace", core.ReplaceRequest{RequestID: args.RequestID, RecordID: args.RecordID, ExpectedVersion: args.ExpectedVersion, Repo: t.config.Scope.Repo, OldText: args.Replace.OldText, NewText: args.Replace.NewText}, &result)
		return toolResult(recordWriteResult{result.RecordID, result.Version, args.RequestID}, err, t.config.AvailableTokens)
	}
	if args.Append != nil {
		var result core.Revision
		err := t.client.Call(ctx, "append", core.AppendRequest{RequestID: args.RequestID, RecordID: args.RecordID, ExpectedVersion: args.ExpectedVersion, Repo: t.config.Scope.Repo, Body: *args.Append}, &result)
		return toolResult(recordWriteResult{result.RecordID, result.Version, args.RequestID}, err, t.config.AvailableTokens)
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
