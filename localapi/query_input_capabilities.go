package localapi

import (
	"bytes"
	"encoding/json"
	"strings"

	"github.com/halbritt/cairn/core"
)

const QueryInputCapabilitiesSchema = "cairn.query-input-capabilities/1"

// QueryInputDeclaration rejects duplicate/ambiguous declarations in the whole
// version response, before ordinary JSON decoding can silently choose one.
func QueryInputDeclaration(version json.RawMessage) json.RawMessage {
	members, tail, ok := topLevelMembers(version)
	if !ok || len(bytes.TrimSpace(tail)) != 0 {
		return nil
	}
	var selected json.RawMessage
	for _, member := range members {
		if !strings.EqualFold(member.key, "query_input_capabilities") {
			continue
		}
		if member.key != "query_input_capabilities" || selected != nil {
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

// A known /1 declaration includes schema20 readers and backend-free replay.
// The flag additionally declares indexed compile support, not worker health.
type QueryInputCapabilities struct {
	Schema                 string `json:"schema"`
	IndexedSemanticQueryV1 bool   `json:"indexed_semantic_query_v1"`
}

func ParseQueryInputCapabilities(raw []byte) (QueryInputCapabilities, bool) {
	var result QueryInputCapabilities
	if len(raw) == 0 || len(raw) > 256 {
		return result, false
	}
	members, tail, ok := topLevelMembers(raw)
	if !ok || len(bytes.TrimSpace(tail)) != 0 || len(members) != 2 {
		return result, false
	}
	seen := map[string]bool{}
	for _, member := range members {
		if seen[member.key] || (member.key != "schema" && member.key != "indexed_semantic_query_v1") {
			return result, false
		}
		seen[member.key] = true
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || json.Unmarshal(fields["schema"], &result.Schema) != nil || result.Schema != QueryInputCapabilitiesSchema {
		return result, false
	}
	flag := bytes.TrimSpace(fields["indexed_semantic_query_v1"])
	if !bytes.Equal(flag, []byte("true")) && !bytes.Equal(flag, []byte("false")) {
		return result, false
	}
	result.IndexedSemanticQueryV1 = bytes.Equal(flag, []byte("true"))
	return result, true
}

type InputCapabilityChoice struct {
	CompactPreview  bool
	DistinctCompile bool
	DistinctReplay  bool
}

// RequireDistinctInput refuses before submitting an explicit opted-in request.
func (c InputCapabilityChoice) RequireDistinctInput(replay bool) error {
	if (!replay && c.DistinctCompile) || (replay && c.DistinctReplay) {
		return nil
	}
	return &core.Error{Code: "UNSUPPORTED_QUERY_INPUT", Message: "peer does not declare the required distinct query input contract"}
}

// ConfirmDistinctQuerySchema detects rollback or an ignored field after a
// positive preflight. A refusal does not undo effects or permit an ID retry.
func ConfirmDistinctQuerySchema(schema string) error {
	if schema == core.DistinctQuerySchema {
		return nil
	}
	return &core.Error{Code: "UNSUPPORTED_QUERY_INPUT", Message: "peer did not acknowledge distinct query input; request may have committed, do not retry or downgrade"}
}

// ConfirmDistinctReplaySchema checks a read-only historical response without
// implying that replay committed a new receipt.
func ConfirmDistinctReplaySchema(schema string) error {
	if schema == core.DistinctQuerySchema {
		return nil
	}
	return &core.Error{Code: "UNSUPPORTED_QUERY_INPUT", Message: "peer did not acknowledge distinct historical query input; do not retry or downgrade"}
}
