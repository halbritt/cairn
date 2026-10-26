package localapi

import (
	"encoding/json"

	"github.com/halbritt/cairn/core"
)

const QueryInputCapabilitiesSchema = "cairn.query-input-capabilities/1"

// QueryInputDeclaration rejects duplicate/ambiguous declarations in the whole
// version response, before ordinary JSON decoding can silently choose one.
func QueryInputDeclaration(version json.RawMessage) json.RawMessage {
	return extractCapabilityField(version, capabilityQueryInput)
}

// A known /1 declaration includes schema20 readers and backend-free replay.
// The flag additionally declares indexed compile support, not worker health.
type QueryInputCapabilities struct {
	Schema                 string `json:"schema"`
	IndexedSemanticQueryV1 bool   `json:"indexed_semantic_query_v1"`
}

func ParseQueryInputCapabilities(raw []byte) (QueryInputCapabilities, bool) {
	return decodeCapability(capabilityQueryInput, raw, func(schema string, flags map[string]bool) QueryInputCapabilities {
		return QueryInputCapabilities{Schema: schema, IndexedSemanticQueryV1: flags["indexed_semantic_query_v1"]}
	})
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
	return &core.Error{Code: distinctQueryErrorCode, Message: "peer does not declare the required distinct query input contract"}
}

// ConfirmDistinctQuerySchema detects rollback or an ignored field after a
// positive preflight. A refusal does not undo effects or permit an ID retry.
func ConfirmDistinctQuerySchema(schema string) error {
	return confirmDistinctSchema(schema, false)
}

// ConfirmDistinctReplaySchema checks a read-only historical response without
// implying that replay committed a new receipt.
func ConfirmDistinctReplaySchema(schema string) error {
	return confirmDistinctSchema(schema, true)
}
