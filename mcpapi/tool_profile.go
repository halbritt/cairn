package mcpapi

import (
	"fmt"

	"github.com/google/jsonschema-go/jsonschema"
)

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
