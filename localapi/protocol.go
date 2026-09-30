package localapi

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"strings"

	"github.com/halbritt/cairn/core"
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

// Effective is the protocol a request from this client is evaluated at by a
// server declaring the given range: the client always declares its current
// version, so a declaring server either supports exactly that or refuses. A
// legacy server (declared=false) evaluates protocol 1, which this client may
// use only while its minimum is 1. Zero means the pair cannot talk.
func (p ProtocolRange) Effective(server ProtocolRange, declared bool) int {
	if !declared {
		if p.Min <= 1 {
			return 1
		}
		return 0
	}
	if server.Supports(p.Current) {
		return p.Current
	}
	return 0
}

const RetrievalCapabilitiesSchema = "cairn.retrieval-capabilities/1"

// RetrievalCapabilities declares implemented search request semantics, not
// authorization, current service health, or a guarantee that a body will fit.
// Both flags are required; reserve support requires memory-budget support.
type RetrievalCapabilities struct {
	Schema                  string `json:"schema"`
	SearchMemoryBudgetBytes bool   `json:"search_memory_budget_bytes"`
	SearchMinPullBytes      bool   `json:"search_min_pull_bytes"`
}

// VersionInfo is the version route's reply: the build identity, which is
// diagnostic only, and the protocol range, which decides compatibility. A
// legacy server replies with the build fields alone. Build/protocol consumers
// ignore optional capability metadata, including future declaration formats.
type VersionInfo struct {
	buildinfo.Info
	Protocol *ProtocolRange `json:"protocol,omitempty"`
}

// VersionResponse is the current producer shape. Keep it separate from the
// build/protocol reader so optional capability formats cannot break those reads.
type VersionResponse struct {
	VersionInfo
	RetrievalCapabilities *RetrievalCapabilities `json:"retrieval_capabilities,omitempty"`
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

// objectMember is one top-level member of a JSON object: its unescaped key and
// its exact source text ("key":value), without the separating comma.
type objectMember struct {
	key  string
	text []byte
}

// topLevelMembers splits a JSON object into its members, keeping every
// member's raw bytes and order so that removing one never changes how the rest
// decodes: duplicate or differently cased keys stay exactly as the caller sent
// them. The tail after the object is returned unchanged for strict decoding.
func topLevelMembers(body []byte) ([]objectMember, []byte, bool) {
	decoder := json.NewDecoder(bytes.NewReader(body))
	if token, err := decoder.Token(); err != nil || token != json.Delim('{') {
		return nil, nil, false
	}
	members := []objectMember{}
	for decoder.More() {
		start := decoder.InputOffset()
		token, err := decoder.Token()
		key, isKey := token.(string)
		if err != nil || !isKey {
			return nil, nil, false
		}
		var value json.RawMessage
		if err = decoder.Decode(&value); err != nil {
			return nil, nil, false
		}
		text := bytes.TrimLeft(body[start:decoder.InputOffset()], " \t\r\n")
		text = bytes.TrimLeft(bytes.TrimPrefix(text, []byte(",")), " \t\r\n")
		members = append(members, objectMember{key: key, text: text})
	}
	if token, err := decoder.Token(); err != nil || token != json.Delim('}') {
		return nil, nil, false
	}
	return members, body[decoder.InputOffset():], true
}

// reservedKey reports an exact use of the guard, or any other spelling that
// case-insensitive struct decoding could confuse with it.
func reservedKey(key string) (exact, ambiguous bool) {
	return key == BodyProtocolField, key != BodyProtocolField && strings.EqualFold(key, BodyProtocolField)
}

// guardBody adds the reserved minimum to a JSON object request when this
// client's minimum exceeds 1. With minimum 1 the body is unchanged, so a
// legacy server still accepts it. A caller may never supply the reserved field.
func guardBody(body []byte) ([]byte, error) {
	members, tail, ok := topLevelMembers(body)
	if !ok {
		if Protocol.Min > 1 {
			// Only an object can carry the guard; sending anything else would
			// bypass this client's minimum on a server that predates it.
			return nil, &core.Error{Code: "INVALID_REQUEST", Message: "a client with a protocol minimum sends only JSON object requests"}
		}
		return body, nil
	}
	for _, member := range members {
		if exact, ambiguous := reservedKey(member.key); exact || ambiguous {
			return nil, &core.Error{Code: "INVALID_REQUEST", Message: BodyProtocolField + " is reserved for the protocol guard"}
		}
	}
	if Protocol.Min <= 1 {
		return body, nil
	}
	guarded := []byte(`{"` + BodyProtocolField + `":` + strconv.Itoa(Protocol.Min))
	for _, member := range members {
		guarded = append(append(guarded, ','), member.text...)
	}
	return append(append(guarded, '}'), tail...), nil
}

// admitBodyProtocol verifies and removes the reserved body guard before the
// request is strictly decoded. Every other member keeps its exact bytes and
// order. A duplicate guard, or a differently cased spelling next to it, is
// refused rather than reinterpreted.
func admitBodyProtocol(w http.ResponseWriter, r *http.Request, body []byte) ([]byte, bool) {
	legacy, _ := r.Context().Value(undeclaredProtocol{}).(string)
	refuseLegacy := func() ([]byte, bool) {
		if legacy == "" {
			return body, true
		}
		writeError(w, 426, "PROTOCOL_UNSUPPORTED", legacy)
		return nil, false
	}
	// Keys are compared after JSON unescaping, so an escaped spelling of the
	// reserved name is the guard (or ambiguous), never an unnoticed field.
	members, tail, ok := topLevelMembers(body)
	if !ok {
		return refuseLegacy() // Not an object: strict decoding reports it.
	}
	var guard *objectMember
	kept := make([]objectMember, 0, len(members))
	for i, member := range members {
		exact, ambiguous := reservedKey(member.key)
		if ambiguous || (exact && guard != nil) {
			writeError(w, 400, "INVALID_REQUEST", "duplicate or ambiguous "+BodyProtocolField)
			return nil, false
		}
		if exact {
			guard = &members[i]
			continue
		}
		kept = append(kept, member)
	}
	if guard == nil {
		return refuseLegacy()
	}
	text := string(bytes.TrimSpace(bytes.SplitN(guard.text, []byte(":"), 2)[1]))
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
	stripped := []byte("{")
	for i, member := range kept {
		if i > 0 {
			stripped = append(stripped, ',')
		}
		stripped = append(stripped, member.text...)
	}
	return append(append(stripped, '}'), tail...), true
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
