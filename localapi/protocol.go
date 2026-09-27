package localapi

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"strconv"

	"github.com/halbritt/cairn/internal/buildinfo"
)

// Protocol headers. A protocol claim is declaration metadata: it never selects
// a principal, role, destination or operation. Request constraints travel in
// the JSON body, because a legacy relay forwards only its fixed header list.
const (
	ProtocolHeader      = "Cairn-Protocol"
	RelayProtocolHeader = "Cairn-Relay-Protocol"
)

// Exported bounds for generated contracts; Protocol is built from them.
const (
	ProtocolMin     = protocolMin
	ProtocolCurrent = 2
)

// BodyProtocolField is a reserved top-level request field. A client whose
// minimum exceeds 1 sends it so that any server unable to honor that minimum
// refuses before executing: servers since protocol 2 verify and strip it, and
// older servers reject it as an unknown field under strict decoding. Unlike a
// header, it survives legacy relays and a server swapped after a preflight.
const BodyProtocolField = "cairn_protocol"

// ProtocolRange is an exact supported interval of wire protocol versions.
// Protocol 1 is the deployed b46a0e1 behavior, which sends no header.
// Protocol 2 adds only this declaration; operation bodies are unchanged.
type ProtocolRange struct {
	Min     int `json:"min"`
	Current int `json:"current"`
}

// Protocol is what this binary speaks as client, relay and server. The
// minimum is fixed at build time; raising it is a separate breaking release.
var Protocol = ProtocolRange{Min: ProtocolMin, Current: ProtocolCurrent}

func (p ProtocolRange) Supports(version int) bool { return version >= p.Min && version <= p.Current }

// Overlap returns the newest version both ranges support, or 0.
func (p ProtocolRange) Overlap(other ProtocolRange) int {
	best := min(p.Current, other.Current)
	if best < max(p.Min, other.Min) {
		return 0
	}
	return best
}

// VersionInfo is the version route's reply: the build identity, which is
// diagnostic only, and the protocol range, which decides compatibility. A
// legacy server replies with the build fields alone.
type VersionInfo struct {
	buildinfo.Info
	Protocol *ProtocolRange `json:"protocol,omitempty"`
}

// ServerProtocol is a peer's declared range, or protocol 1 when it declared none.
func (v VersionInfo) ServerProtocol() (ProtocolRange, bool) {
	if v.Protocol == nil {
		return LegacyProtocol, false
	}
	return *v.Protocol, true
}

// LegacyProtocol is how a peer that declares nothing is treated.
var LegacyProtocol = ProtocolRange{Min: 1, Current: 1}

// ParseProtocol reads one canonical decimal protocol header. Absence means
// protocol 1. Several values, signs, leading zeros or over four digits are
// malformed rather than guessed at.
func ParseProtocol(header http.Header, name string) (version int, present bool, ok bool) {
	values := header.Values(name)
	if len(values) == 0 {
		return 1, false, true
	}
	value := values[0]
	if len(values) != 1 || value == "" || len(value) > 4 || value[0] == '0' {
		return 0, true, false
	}
	for _, c := range value {
		if c < '0' || c > '9' {
			return 0, true, false
		}
	}
	version, err := strconv.Atoi(value)
	return version, true, err == nil
}

// guardBody adds the reserved minimum to a JSON object request when this
// client's minimum exceeds 1. With minimum 1 the body is unchanged, so a
// legacy server still accepts it.
func guardBody(body []byte) []byte {
	if Protocol.Min <= 1 || len(body) < 2 || body[0] != '{' {
		return body
	}
	guard := `"` + BodyProtocolField + `":` + strconv.Itoa(Protocol.Min)
	if bytes.Equal(bytes.TrimSpace(body[1:]), []byte("}")) {
		return []byte("{" + guard + "}")
	}
	return append([]byte("{"+guard+","), body[1:]...)
}

// admitBodyProtocol verifies and removes the reserved body guard before the
// request is strictly decoded. Only an exact top-level key is recognized; any
// other spelling stays an unknown field and is refused by strict decoding.
func admitBodyProtocol(w http.ResponseWriter, r *http.Request, body []byte) ([]byte, bool) {
	legacy, _ := r.Context().Value(undeclaredProtocol{}).(string)
	refuseLegacy := func() ([]byte, bool) {
		if legacy == "" {
			return body, true
		}
		writeError(w, 426, "PROTOCOL_UNSUPPORTED", legacy)
		return nil, false
	}
	if !bytes.Contains(body, []byte(`"`+BodyProtocolField+`"`)) {
		return refuseLegacy()
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(body, &fields); err != nil {
		return refuseLegacy() // Not an object: no guard.
	}
	raw, ok := fields[BodyProtocolField]
	if !ok {
		return refuseLegacy()
	}
	text := string(raw)
	version, err := strconv.Atoi(text)
	if err != nil || text != strconv.Itoa(version) || version < 1 || version > 9999 {
		writeError(w, 400, "INVALID_REQUEST", BodyProtocolField+" must be one positive integer protocol version")
		return nil, false
	}
	if declared, present, _ := ParseProtocol(r.Header, ProtocolHeader); present && declared < version {
		writeError(w, 400, "INVALID_REQUEST", "Cairn-Protocol is below the request's "+BodyProtocolField+" minimum")
		return nil, false
	}
	if !Protocol.Supports(version) {
		writeError(w, 426, "PROTOCOL_UNSUPPORTED", "the request requires protocol "+text+"; this server supports protocols "+
			strconv.Itoa(Protocol.Min)+" to "+strconv.Itoa(Protocol.Current))
		return nil, false
	}
	delete(fields, BodyProtocolField)
	stripped, err := json.Marshal(fields)
	if err != nil {
		writeError(w, 400, "INVALID_REQUEST", "invalid bounded JSON request")
		return nil, false
	}
	return stripped, true
}

// admitProtocol refuses an unsupported or malformed declaration before any
// session binding, decoding or store work. It runs after authentication, so
// a claim never substitutes for identity.
func admitProtocol(w http.ResponseWriter, r *http.Request, network bool) bool {
	version, present, ok := ParseProtocol(r.Header, ProtocolHeader)
	if !ok {
		writeError(w, 400, "INVALID_REQUEST", "Cairn-Protocol must be one canonical decimal version")
		return false
	}
	if _, _, relayOK := ParseProtocol(r.Header, RelayProtocolHeader); !relayOK {
		writeError(w, 400, "INVALID_REQUEST", "Cairn-Relay-Protocol must be one canonical decimal version")
		return false
	}
	if Protocol.Supports(version) {
		return true
	}
	if !present {
		// A legacy relay drops the header but forwards the body. The body guard,
		// checked before decoding, decides; without one this is protocol 1.
		*r = *r.WithContext(context.WithValue(r.Context(), undeclaredProtocol{}, legacyRefusal(r, network)))
		return true
	}
	writeError(w, 426, "PROTOCOL_UNSUPPORTED", "protocol "+strconv.Itoa(version)+" is unsupported; this server supports protocols "+
		strconv.Itoa(Protocol.Min)+" to "+strconv.Itoa(Protocol.Current))
	return false
}

type undeclaredProtocol struct{}

func legacyRefusal(r *http.Request, network bool) string {
	message := "the request declares no protocol (legacy protocol 1); this server supports protocols " +
		strconv.Itoa(Protocol.Min) + " to " + strconv.Itoa(Protocol.Current) + "; upgrade the client"
	if network && len(r.Header.Values(RelayProtocolHeader)) == 0 {
		message += " and this host's relay, which may be a legacy relay that drops Cairn-Protocol"
	}
	return message
}
