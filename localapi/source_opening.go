package localapi

import (
	"encoding/json"
	"net/http"

	"github.com/halbritt/cairn/core"
)

// SourceOpeningHeader declares that the response reader preserves source_opening.
// Absence means unsupported. Relays may forward it but must never originate it.
// It is transport metadata, not authentication, authorization or request intent.
const SourceOpeningHeader = "Cairn-Accept-Source-Opening"

func acceptsSourceOpening(header http.Header) (bool, error) {
	values := header.Values(SourceOpeningHeader)
	if len(values) == 0 {
		return false, nil
	}
	if len(values) != 1 || values[0] != "1" {
		return false, &core.Error{Code: "INVALID_REQUEST", Message: "Cairn-Accept-Source-Opening must be one value: 1"}
	}
	return true, nil
}

// Declare support only for response destinations which retain the whole pair.
// A caller decoding an older or unrelated struct must not claim support merely
// because it links the current transport client.
func preservesSourceOpening(response any) bool {
	switch response.(type) {
	case *core.Expansion, *json.RawMessage:
		return true
	default:
		return false
	}
}
