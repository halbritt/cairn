package localapi

import (
	"bytes"
	"context"
	"encoding/json"
	"strings"

	"github.com/halbritt/cairn/core"
)

type capabilityKind string

const (
	capabilityRetrieval     capabilityKind = "retrieval_capabilities"
	capabilityPreview       capabilityKind = "preview_capabilities"
	capabilityQueryInput    capabilityKind = "query_input_capabilities"
	capabilityInspection    capabilityKind = "inspection_capabilities"
	capabilitySourceOpening capabilityKind = "source_opening_capabilities"
	capabilityReader        capabilityKind = "reader_capabilities"
)

type capabilityDescriptor struct {
	field  string
	schema string
	flags  []string
	valid  func(map[string]bool) bool
}

var capabilityTable = map[capabilityKind]capabilityDescriptor{
	capabilityRetrieval: {
		field:  "retrieval_capabilities",
		schema: RetrievalCapabilitiesSchema,
		flags:  []string{"search_memory_budget_bytes", "search_min_pull_bytes"},
		valid: func(flags map[string]bool) bool {
			return !flags["search_min_pull_bytes"] || flags["search_memory_budget_bytes"]
		},
	},
	capabilityPreview: {
		field:  "preview_capabilities",
		schema: PreviewCapabilitiesSchema,
		flags:  []string{"entities_omitted"},
	},
	capabilityQueryInput: {
		field:  "query_input_capabilities",
		schema: QueryInputCapabilitiesSchema,
		flags:  []string{"indexed_semantic_query_v1"},
	},
	capabilityInspection: {
		field:  "inspection_capabilities",
		schema: InspectionCapabilitiesSchema,
		flags:  []string{"first_fitting_whole_v1"},
	},
	capabilitySourceOpening: {
		field:  "source_opening_capabilities",
		schema: SourceOpeningCapabilitiesSchema,
		flags:  []string{"reader_guard", "receipt_binding"},
	},
	capabilityReader: {
		field:  "reader_capabilities",
		schema: ReaderCapabilitiesSchema,
		flags:  []string{"source_opening"},
	},
}

func extractCapabilityField(container json.RawMessage, kind capabilityKind) json.RawMessage {
	spec, ok := capabilityTable[kind]
	if !ok {
		return nil
	}
	members, tail, ok := topLevelMembers(container)
	if !ok || len(bytes.TrimSpace(tail)) != 0 {
		return nil
	}
	var selected json.RawMessage
	for _, member := range members {
		if !strings.EqualFold(member.key, spec.field) {
			continue
		}
		if member.key != spec.field || selected != nil {
			return nil
		}
		var field map[string]json.RawMessage
		if json.Unmarshal(append(append([]byte{'{'}, member.text...), '}'), &field) != nil {
			return nil
		}
		selected = field[member.key]
	}
	return selected
}

func parseCapabilityFlags(kind capabilityKind, raw []byte) (map[string]bool, bool) {
	spec, ok := capabilityTable[kind]
	if !ok || len(raw) == 0 || len(raw) > 256 {
		return nil, false
	}
	members, tail, ok := topLevelMembers(raw)
	if !ok || len(bytes.TrimSpace(tail)) != 0 || len(members) != len(spec.flags)+1 {
		return nil, false
	}
	allowed := map[string]bool{"schema": true}
	for _, name := range spec.flags {
		allowed[name] = true
	}
	seen := map[string]bool{}
	for _, member := range members {
		if seen[member.key] || !allowed[member.key] {
			return nil, false
		}
		seen[member.key] = true
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil {
		return nil, false
	}
	var schema string
	if json.Unmarshal(fields["schema"], &schema) != nil || schema != spec.schema {
		return nil, false
	}
	flags := make(map[string]bool, len(spec.flags))
	for _, name := range spec.flags {
		switch string(bytes.TrimSpace(fields[name])) {
		case "true":
			flags[name] = true
		case "false":
			flags[name] = false
		default:
			return nil, false
		}
	}
	if spec.valid != nil && !spec.valid(flags) {
		return nil, false
	}
	return flags, true
}

func decodeCapability[T any](kind capabilityKind, raw []byte, build func(schema string, flags map[string]bool) T) (T, bool) {
	flags, ok := parseCapabilityFlags(kind, raw)
	if !ok {
		var zero T
		return zero, false
	}
	return build(capabilityTable[kind].schema, flags), true
}

func probeVersionDeclarations(ctx context.Context, client *Client, kinds ...capabilityKind) (map[capabilityKind]json.RawMessage, error) {
	var raw json.RawMessage
	var envelope map[string]json.RawMessage
	if err := client.Call(ctx, "version", struct{}{}, &raw); err != nil {
		return nil, err
	}
	if err := json.Unmarshal(raw, &envelope); err != nil {
		return nil, err
	}
	out := make(map[capabilityKind]json.RawMessage, len(kinds))
	for _, kind := range kinds {
		out[kind] = extractCapabilityField(raw, kind)
	}
	return out, nil
}

const distinctQueryErrorCode = "UNSUPPORTED_QUERY_INPUT"

var distinctQuerySchemaMessages = map[bool]string{
	false: "peer did not acknowledge distinct query input; request may have committed, do not retry or downgrade",
	true:  "peer did not acknowledge distinct historical query input; do not retry or downgrade",
}

func confirmDistinctSchema(schema string, replay bool) error {
	if schema == core.DistinctQuerySchema {
		return nil
	}
	return &core.Error{Code: distinctQueryErrorCode, Message: distinctQuerySchemaMessages[replay]}
}

// ParseRetrievalCapabilities reads a retrieval capability declaration that crossed a trust boundary.
// Deliberately closed and bounded. Unknown schema/fields, duplicate members,
// missing flags and contradictory declarations remain unknown, not unsupported.
func ParseRetrievalCapabilities(raw []byte) (RetrievalCapabilities, bool) {
	return decodeCapability(capabilityRetrieval, raw, func(schema string, flags map[string]bool) RetrievalCapabilities {
		return RetrievalCapabilities{
			Schema:                  schema,
			SearchMemoryBudgetBytes: flags["search_memory_budget_bytes"],
			SearchMinPullBytes:      flags["search_min_pull_bytes"],
		}
	})
}
