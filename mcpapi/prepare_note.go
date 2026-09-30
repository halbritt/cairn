package mcpapi

import (
	"context"
	"errors"
	"strings"

	"github.com/halbritt/cairn/core"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// This is a writer-facing view over ordinary retrieval, not a second ranking
// algorithm or a create-and-then-search mutation with ambiguous retry behavior.
type prepareNoteArgs struct {
	Query             string            `json:"query" jsonschema:"Short subject query for the proposed knowledge, including project and known identifiers. Do not send the draft body or secrets. No automatic subject or replacement inference."`
	Entities          []core.EntityRef  `json:"entities,omitempty" jsonschema:"Known file or symbol hints, at most 16; not evidence of identity or applicability."`
	Context           *core.ContextPins `json:"context,omitempty" jsonschema:"Actual current retrieval context; must not conflict with host context. Not the proposed note's capture pins."`
	AvailableTokens   *int              `json:"available_tokens,omitempty" jsonschema:"Known free input-context room within the host ceiling. Omit when unknown to use host policy default; not the memory allocation."`
	MemoryBudgetBytes *int              `json:"memory_budget_bytes,omitempty" jsonschema:"Optional total UTF-8 allowance for preparation output and receipt expansions, within available_tokens. 1024 bytes are reserved for guidance; at least 1280 bytes are needed. Count combined recall across calls yourself."`
	RequestID         string            `json:"request_id,omitempty" jsonschema:"Optional search retry UUID. Reuse only for identical preparation arguments. Later save/edit is a separate explicit mutation."`
}

type notePreparation struct {
	NoteSaved bool   `json:"note_saved"`
	Guidance  string `json:"guidance"`
}

const preparationOverhead = 1024
const preparationGuidance = "No note saved. These are possible predecessors, not verified replacements. Pull complete current bodies with pull_arguments and compare subject, scope, pins and evidence. Same active A note: use cairn_edit with its expected_version and a new request_id, preserving unrelated content. Distinct knowledge: use cairn_remember. Separate obsolete record: formal supersession requires protected preview and authorized local/operator access; do not borrow another profile. B/C changes need their authority path. Empty or budget-limited results do not prove no predecessor exists. Search failure is not clearance to save."

func (t memoryTools) prepareNote(ctx context.Context, request *mcp.CallToolRequest, args prepareNoteArgs) (*mcp.CallToolResult, any, error) {
	if strings.TrimSpace(args.Query) == "" {
		return nil, nil, errors.New("INVALID_REQUEST: preparation requires a subject query")
	}
	return t.searchWithPreparation(ctx, request, searchArgs{
		Query: args.Query, Entities: args.Entities, Context: args.Context,
		AvailableTokens: args.AvailableTokens, MemoryBudgetBytes: args.MemoryBudgetBytes,
		RequestID: args.RequestID,
	}, true)
}
