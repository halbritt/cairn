package mcpapi

import (
	"context"
	"encoding/json"
	"errors"
	"math"
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
	MemoryBudgetBytes *int              `json:"memory_budget_bytes,omitempty" jsonschema:"Optional total UTF-8 allowance for preparation output and receipt expansions, within available_tokens. The actual encoded preparation-field overhead is reserved; at least 256 receipt bytes must remain to attempt retrieval. Count combined recall across calls yourself."`
	MinPullBytes      *int              `json:"min_pull_bytes,omitempty" jsonschema:"Optional minimum charged receipt expansion allowance left after preparation, for pulling predecessor sources. Requires memory_budget_bytes; 1 through min(24000, memory_budget_bytes minus the encoded preparation-field overhead). Reserves within that total by reducing preview delivery, never by increasing total room, dropping the guidance or truncating required context; the final preparation result must fit memory_budget_bytes minus this reserve. May refuse if guidance, envelope and required context cannot fit. Not plain body bytes, a guaranteed successful pull, proof of relevance or clearance to save. Omit for existing allocation; repeat on retries and use a new request UUID when changing it."`
	RequestID         string            `json:"request_id,omitempty" jsonschema:"Optional search retry UUID. Reuse only for identical preparation arguments. Later save/edit is a separate explicit mutation."`
}

type notePreparation struct {
	NoteSaved bool   `json:"note_saved"`
	Guidance  string `json:"guidance"`
}

const preparationGuidance = "No note saved. These are possible predecessors, not verified replacements. Pull complete current bodies with pull_arguments and compare subject, scope, pins and evidence. Same active A note: use cairn_edit with its expected_version and a new request_id, preserving unrelated content. Distinct knowledge: use cairn_remember. Separate obsolete record: formal supersession requires protected preview and authorized local/operator access; do not borrow another profile. B/C changes need their authority path. Empty or budget-limited results do not prove no predecessor exists. Search failure is not clearance to save."

// Measure the field through the same JSON-in-MCP text encoding as the output.
// Other search fields are identical, so only the added preparation is reserved.
func preparationOverhead(guidance string) (int, error) {
	plain := searchResult{}
	prepared := plain
	prepared.Preparation = &notePreparation{NoteSaved: false, Guidance: guidance}
	var sizes [2]int
	for i, view := range []searchResult{plain, prepared} {
		result, _, err := toolResult(view, nil, math.MaxInt)
		if err != nil {
			return 0, err
		}
		encoded, err := json.Marshal(result)
		if err != nil {
			return 0, err
		}
		sizes[i] = len(encoded)
	}
	return sizes[1] - sizes[0], nil
}

func (t memoryTools) prepareNote(ctx context.Context, request *mcp.CallToolRequest, args prepareNoteArgs) (*mcp.CallToolResult, any, error) {
	if strings.TrimSpace(args.Query) == "" {
		return nil, nil, errors.New("INVALID_REQUEST: preparation requires a subject query")
	}
	return t.searchWithPreparation(ctx, request, searchArgs{
		Query: args.Query, Entities: args.Entities, Context: args.Context,
		AvailableTokens: args.AvailableTokens, MemoryBudgetBytes: args.MemoryBudgetBytes,
		MinPullBytes: args.MinPullBytes, RequestID: args.RequestID,
	}, true)
}
