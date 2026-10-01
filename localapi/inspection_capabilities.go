package localapi

import (
	"bytes"
	"encoding/json"
)

const InspectionCapabilitiesSchema = "cairn.inspection-capabilities/1"

// InspectionCapabilities declares exact search allocation policy support, not
// eligibility, relevance or a guarantee that any candidate can fit.
// Separate from the closed retrieval-capabilities/1 contract for old readers.
type InspectionCapabilities struct {
	Schema              string `json:"schema"`
	FirstFittingWholeV1 bool   `json:"first_fitting_whole_v1"`
}

func CurrentInspectionCapabilities() *InspectionCapabilities {
	return &InspectionCapabilities{Schema: InspectionCapabilitiesSchema, FirstFittingWholeV1: true}
}

// ParseInspectionCapabilities reads only the bounded, complete declaration.
// A malformed declaration is unknown, distinct from explicit false support.
func ParseInspectionCapabilities(raw []byte) (InspectionCapabilities, bool) {
	var result InspectionCapabilities
	if len(raw) == 0 || len(raw) > 256 {
		return result, false
	}
	members, tail, ok := topLevelMembers(raw)
	if !ok || len(bytes.TrimSpace(tail)) != 0 || len(members) != 2 {
		return result, false
	}
	seen := map[string]bool{}
	for _, member := range members {
		if seen[member.key] || (member.key != "schema" && member.key != "first_fitting_whole_v1") {
			return result, false
		}
		seen[member.key] = true
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil {
		return result, false
	}
	if json.Unmarshal(fields["schema"], &result.Schema) != nil || result.Schema != InspectionCapabilitiesSchema {
		return result, false
	}
	flag := bytes.TrimSpace(fields["first_fitting_whole_v1"])
	if !bytes.Equal(flag, []byte("true")) && !bytes.Equal(flag, []byte("false")) {
		return result, false
	}
	result.FirstFittingWholeV1 = bytes.Equal(flag, []byte("true"))
	return result, true
}
