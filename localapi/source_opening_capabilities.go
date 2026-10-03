package localapi

import (
	"bytes"
	"encoding/json"
)

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
	flags, ok := parseClosedFlags(raw, SourceOpeningCapabilitiesSchema, "reader_guard", "receipt_binding")
	if !ok {
		return SourceOpeningCapabilities{}, false
	}
	return SourceOpeningCapabilities{Schema: SourceOpeningCapabilitiesSchema, ReaderGuard: flags["reader_guard"], ReceiptBinding: flags["receipt_binding"]}, true
}

// ParseReaderCapabilities reads only the bounded, complete declaration.
func ParseReaderCapabilities(raw []byte) (ReaderCapabilities, bool) {
	flags, ok := parseClosedFlags(raw, ReaderCapabilitiesSchema, "source_opening")
	if !ok {
		return ReaderCapabilities{}, false
	}
	return ReaderCapabilities{Schema: ReaderCapabilitiesSchema, SourceOpening: flags["source_opening"]}, true
}

// parseClosedFlags accepts exactly the schema member and the named boolean
// members, each once, as JSON true or false.
func parseClosedFlags(raw []byte, schema string, names ...string) (map[string]bool, bool) {
	if len(raw) == 0 || len(raw) > 256 {
		return nil, false
	}
	members, tail, ok := topLevelMembers(raw)
	if !ok || len(bytes.TrimSpace(tail)) != 0 || len(members) != len(names)+1 {
		return nil, false
	}
	allowed := map[string]bool{"schema": true}
	for _, name := range names {
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
	var got string
	if json.Unmarshal(fields["schema"], &got) != nil || got != schema {
		return nil, false
	}
	flags := map[string]bool{}
	for _, name := range names {
		switch string(bytes.TrimSpace(fields[name])) {
		case "true":
			flags[name] = true
		case "false":
			flags[name] = false
		default:
			return nil, false
		}
	}
	return flags, true
}
