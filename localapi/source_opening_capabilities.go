package localapi

const (
	SourceOpeningCapabilitiesSchema = "cairn.source-opening-capabilities/1"
	ReaderCapabilitiesSchema        = "cairn.reader-capabilities/1"
)

// SourceOpeningCapabilities is a server's declaration about paired source
// openings, not access, health or a guarantee that a read will succeed.
// ReaderGuard says the server refuses a paired read to a reader that did not
// declare support. ReceiptBinding says new context-index receipts for the
// authenticated principal bind the opening policy now; receipts already sealed
// keep the policy they were sealed with. It is separate from the closed
// retrieval-capabilities/1 contract so existing readers keep working.
type SourceOpeningCapabilities struct {
	Schema         string `json:"schema"`
	ReaderGuard    bool   `json:"reader_guard"`
	ReceiptBinding bool   `json:"receipt_binding"`
}

// ReaderCapabilities is a client's self-reported claim, carried in its
// diagnostics declaration. Absence or an unusable value reads as unknown, never
// as support, and it is never authentication or authorization.
type ReaderCapabilities struct {
	Schema        string `json:"schema"`
	SourceOpening bool   `json:"source_opening"`
}

// CurrentReaderCapabilities is what the Go CLI and MCP facade of this build
// report: both decode an expansion without dropping source_opening.
func CurrentReaderCapabilities() *ReaderCapabilities {
	return &ReaderCapabilities{Schema: ReaderCapabilitiesSchema, SourceOpening: true}
}

// ParseSourceOpeningCapabilities reads only the bounded, complete declaration.
// A malformed one is unknown, distinct from an explicit false.
func ParseSourceOpeningCapabilities(raw []byte) (SourceOpeningCapabilities, bool) {
	return decodeCapability(capabilitySourceOpening, raw, func(schema string, flags map[string]bool) SourceOpeningCapabilities {
		return SourceOpeningCapabilities{Schema: schema, ReaderGuard: flags["reader_guard"], ReceiptBinding: flags["receipt_binding"]}
	})
}

// ParseReaderCapabilities reads only the bounded, complete declaration.
func ParseReaderCapabilities(raw []byte) (ReaderCapabilities, bool) {
	return decodeCapability(capabilityReader, raw, func(schema string, flags map[string]bool) ReaderCapabilities {
		return ReaderCapabilities{Schema: schema, SourceOpening: flags["source_opening"]}
	})
}
