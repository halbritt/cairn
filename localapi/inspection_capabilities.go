package localapi

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
	return decodeCapability(capabilityInspection, raw, func(schema string, flags map[string]bool) InspectionCapabilities {
		return InspectionCapabilities{Schema: schema, FirstFittingWholeV1: flags["first_fitting_whole_v1"]}
	})
}
