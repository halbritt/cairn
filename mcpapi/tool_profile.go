package mcpapi

import (
	"fmt"

	"github.com/google/jsonschema-go/jsonschema"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

var compactAllToolDescriptions = map[string]string{
	"cairn_prepare_note":  "Search for possible predecessor notes before a save or correction. Use a short subject query and known entities, then pull and compare complete current sources. This creates a receipt; it does not write or authorize change. Similarity is not supersession. Formal supersession needs the protected impact preview and authorized operator workflow, unavailable to hosted profiles.",
	"cairn_client_info":   "Inspect this running MCP facade and authenticated API build, active profiles, and declared budget/reader capabilities. API errors retain local data and add a sanitized diagnostic. Unknown stays unknown; revisions do not prove compatibility or reader readiness. Does not identify other clients and performs no retries, restarts, or writes.",
	"cairn_assess":        "Append testimony to a receipt owned by this profile after reading its assessment history. Use the latest expected_version, or 0 if empty, and reuse exact request_id and arguments for retries; VERSION_CONFLICT needs a fresh read and reconciliation. A failed reply may follow a commit. Review of an owned receipt is agent testimony, never the host task assessment; returns no reason.",
	"cairn_assessments":   "Read assessment history for an owned receipt before writing or interpreting a review. A linked retrieval does not grant host assessment access. Includes reasons and evidence IDs, not evidence bodies; max 1000 versions, no pagination. Empty returns []; oversized output refuses, never truncates.",
	"cairn_history":       "Read retained versions for comparison. Omit version for paged metadata; use one positive version for a body or bounded span. The authenticated profile fixes repository and destination; excluded content refuses. Historical text is not current eligibility or authority; pull current sources before editing. Uses a separate output budget and spends no search expansion credits.",
	"cairn_pull_evidence": "Read evidence from an expanded memory with its evidence ID, full-object SHA-256, original receipt, and handle. Optional spans are partial. Reuse request UUIDs only for exact retries; this shares the receipt expansion budget.",
	"cairn_remember":      "Save selected knowledge as ordinary A testimony with source and verification context. Scope defaults to repository; choose task or run explicitly. Use cairn_prepare_note before replacing guidance and cairn_edit to correct the same note. Does not promote claims or grant authority. Keep raw sessions and secrets out; shareable permits hosted delivery, local notes stay out of hosted searches.",
	"cairn_edit":          "Revise a pulled active A note. Supply its ID, expected version, a new request UUID, and exactly one of body, append, replace, set_sections, draft, or evidence_citations. Text edits preserve citations and earlier versions retain sources; full drafts must preserve unrelated metadata. Scope, sensitivity, and privileged changes are refused; authenticated writer is recorded. Reconcile version conflicts; retry only identical arguments. Edits remain testimony, not qualification. Returns identifiers only.",
}

func profiledTool(profile string, tool *mcp.Tool) *mcp.Tool {
	if profile == "compact/2" {
		if description, ok := compactAllToolDescriptions[tool.Name]; ok {
			tool.Description = description
		}
	}
	return tool
}

// compactSearchSchema keeps the inferred argument contract and replaces only
// descriptions. New arguments retain their full description until reviewed.
func compactSearchSchema() (*jsonschema.Schema, error) {
	schema, err := jsonschema.For[searchArgs](&jsonschema.ForOptions{})
	if err != nil {
		return nil, err
	}
	short := map[string]string{
		"query":                  "Memory query; ASCII double quotes prefer exact text. Omit for browse or signature-only search.",
		"browse":                 "Browse eligible notes without a query. Bounded, not a complete inventory.",
		"semantic":               "Semantic discovery for vocabulary gaps; requires query. Check discovery.state for fallback.",
		"semantic_query":         "Separate semantic input; requires semantic=true and lexical query, with no browsing.",
		"available_tokens":       "Actual free input room in UTF-8 bytes, 256 through the host ceiling; not model capacity.",
		"memory_budget_bytes":    "Smaller total search and receipt allowance within available_tokens; repeat on retries.",
		"inspection_policy":      "first-fitting-whole/1 or /2; needs memory_budget_bytes, no min_pull_bytes. Fit is not relevance.",
		"min_pull_bytes":         "Charged pull reserve within memory_budget_bytes, 1 through min(24000, budget).",
		"request_id":             "Retry UUID for the same search intent.",
		"offset":                 "Ranked page offset; repeat the same query, filters, context and semantic mode.",
		"entities":               "Up to 16 exact file or symbol hints; fallible metadata, not authority or workspace evidence.",
		"context":                "Declared search pins; host conflicts refuse. Not execution attestation.",
		"advisory_conflicts":     "Include eligible competing advisory positions as whole groups; pull marked groups whole.",
		"kinds":                  "Optional kind labels; required instructions still apply.",
		"error_signature_sha256": "Reviewed failure signature hint; not proof the current task failed.",
	}
	for name, description := range short {
		field, ok := schema.Properties[name]
		if !ok {
			return nil, fmt.Errorf("compact search schema lacks %s", name)
		}
		field.Description = description
	}
	return schema, nil
}
