package localapi

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/buildinfo"
)

func TestSourceOpeningCapabilityParsersAreClosedAndBounded(t *testing.T) {
	server := `{"schema":"cairn.source-opening-capabilities/1","reader_guard":true,"receipt_binding":false}`
	reader := `{"schema":"cairn.reader-capabilities/1","source_opening":true}`
	if got, ok := ParseSourceOpeningCapabilities([]byte(server)); !ok || !got.ReaderGuard || got.ReceiptBinding {
		t.Fatalf("valid server declaration: %+v %v", got, ok)
	}
	if got, ok := ParseReaderCapabilities([]byte(reader)); !ok || !got.SourceOpening {
		t.Fatalf("valid reader declaration: %+v %v", got, ok)
	}
	if got, ok := ParseReaderCapabilities([]byte(strings.Replace(reader, "true", "false", 1))); !ok || got.SourceOpening {
		t.Fatalf("an explicit false is a declaration, not unknown: %+v %v", got, ok)
	}
	for name, raw := range map[string]string{
		"empty":            ``,
		"null":             `null`,
		"extra member":     strings.Replace(server, `}`, `,"extra":true}`, 1),
		"missing member":   `{"schema":"cairn.source-opening-capabilities/1","reader_guard":true}`,
		"duplicate member": `{"schema":"cairn.source-opening-capabilities/1","reader_guard":true,"reader_guard":true,"receipt_binding":false}`,
		"wrong schema":     strings.Replace(server, "/1", "/2", 1),
		"case variant":     strings.Replace(server, `"schema"`, `"Schema"`, 1),
		"string flag":      strings.Replace(server, `true`, `"true"`, 1),
		"numeric flag":     strings.Replace(server, `false`, `0`, 1),
		"null flag":        strings.Replace(server, `false`, `null`, 1),
		"trailing data":    server + ` {}`,
		"truncated":        strings.TrimSuffix(server, "}"),
		"oversized":        server[:len(server)-1] + `,"padding":"` + strings.Repeat("x", 300) + `"}`,
	} {
		if _, ok := ParseSourceOpeningCapabilities([]byte(raw)); ok {
			t.Fatalf("server declaration %s accepted: %s", name, raw)
		}
	}
	for name, raw := range map[string]string{
		"extra member":   strings.Replace(reader, `}`, `,"extra":true}`, 1),
		"wrong schema":   strings.Replace(reader, "/1", "/9", 1),
		"string flag":    strings.Replace(reader, `true`, `"yes"`, 1),
		"server schema":  strings.Replace(reader, "cairn.reader-capabilities", "cairn.source-opening-capabilities", 1),
		"duplicate flag": `{"schema":"cairn.reader-capabilities/1","source_opening":true,"source_opening":false}`,
	} {
		if _, ok := ParseReaderCapabilities([]byte(raw)); ok {
			t.Fatalf("reader declaration %s accepted: %s", name, raw)
		}
	}
}

func declaration(t *testing.T, readerCapabilities string) []string {
	t.Helper()
	build, err := json.Marshal(buildinfo.Read())
	if err != nil {
		t.Fatal(err)
	}
	object := `{"schema":"cairn.client-diagnostics/1","surface":"cli","harness":"unknown","transport_build":` + string(build)
	if readerCapabilities != "" {
		object += `,"reader_capabilities":` + readerCapabilities
	}
	return []string{base64.RawURLEncoding.EncodeToString([]byte(object + `}`))}
}

func TestReaderCapabilityIsAdditiveAndUnusableMeansUnknown(t *testing.T) {
	encoded := EncodeClientDiagnostics(ClientDiagnostics{Surface: "cli", Harness: "unknown", TransportBuild: buildinfo.Read(), ReaderCapabilities: CurrentReaderCapabilities()})
	got, state := parseDiagnostics([]string{encoded})
	if state != MetadataPresent || got.ReaderCapabilities == nil || !got.ReaderCapabilities.SourceOpening {
		t.Fatalf("round trip: %v %+v", state, got.ReaderCapabilities)
	}
	// An older client sends no member: present and unknown, never ready.
	if got, state = parseDiagnostics(declaration(t, "")); state != MetadataPresent || got.ReaderCapabilities != nil {
		t.Fatalf("older client declaration: %v %+v", state, got.ReaderCapabilities)
	}
	// An unusable member costs only itself: the rest of the declaration is kept.
	for _, raw := range []string{`{"schema":"cairn.reader-capabilities/1","source_opening":"yes"}`, `{"schema":"cairn.reader-capabilities/2","source_opening":true}`,
		`{"schema":"cairn.reader-capabilities/1","source_opening":true,"extra":1}`, `true`, `[]`, `null`} {
		if got, state = parseDiagnostics(declaration(t, raw)); state != MetadataPresent || got.ReaderCapabilities != nil || got.Surface != "cli" {
			t.Fatalf("%s: %v %+v", raw, state, got)
		}
	}
	// The client never sends a malformed value either.
	dropped := EncodeClientDiagnostics(ClientDiagnostics{Surface: "cli", Harness: "unknown", TransportBuild: buildinfo.Read(), ReaderCapabilities: &ReaderCapabilities{Schema: "other", SourceOpening: true}})
	if got, state = parseDiagnostics([]string{dropped}); state != MetadataPresent || got.ReaderCapabilities != nil {
		t.Fatalf("malformed local value was sent: %v %+v", state, got.ReaderCapabilities)
	}
}

func TestOlderPeersReadTheNewMembersAsUnknownFields(t *testing.T) {
	// An older API decodes only these members of a recognized diagnostics schema.
	var older struct {
		Surface               *string         `json:"surface"`
		Harness               *string         `json:"harness"`
		TransportBuild        *buildinfo.Info `json:"transport_build"`
		Origin                *ClientOrigin   `json:"origin"`
		RetrievalCapabilities json.RawMessage `json:"retrieval_capabilities"`
	}
	decoded, err := base64.RawURLEncoding.DecodeString(declaration(t, `{"schema":"cairn.reader-capabilities/1","source_opening":true}`)[0])
	if err != nil || checkBoundedJSON(decoded) != nil || json.Unmarshal(decoded, &older) != nil || older.Surface == nil || older.TransportBuild == nil || !older.TransportBuild.Valid() {
		t.Fatalf("an older API must still accept the declaration: %v", err)
	}
	// An older client reads the version response through the build/protocol reader
	// and the closed retrieval declaration, neither of which may notice the new member.
	response := VersionResponse{VersionInfo: VersionInfo{Info: buildinfo.Read(), Protocol: &Protocol}, RetrievalCapabilities: CurrentRetrievalCapabilities(),
		SourceOpeningCapabilities: &SourceOpeningCapabilities{Schema: SourceOpeningCapabilitiesSchema, ReaderGuard: true, ReceiptBinding: true}}
	raw, err := json.Marshal(response)
	if err != nil {
		t.Fatal(err)
	}
	var legacy VersionInfo
	var members map[string]json.RawMessage
	if err = json.Unmarshal(raw, &legacy); err != nil || legacy.Protocol == nil || json.Unmarshal(raw, &members) != nil {
		t.Fatalf("legacy version reader: %v", err)
	}
	if _, ok := ParseRetrievalCapabilities(members["retrieval_capabilities"]); !ok {
		t.Fatal("the closed retrieval declaration changed")
	}
	if got, ok := ParseSourceOpeningCapabilities(members["source_opening_capabilities"]); !ok || !got.ReceiptBinding {
		t.Fatalf("new declaration: %+v %v", got, ok)
	}
	// A server without the member reads as unknown, not as unsupported or ready.
	delete(members, "source_opening_capabilities")
	if _, ok := ParseSourceOpeningCapabilities(members["source_opening_capabilities"]); ok {
		t.Fatal("an absent declaration must be unknown")
	}
}

// The client view an operator reads (`cairn clients`) carries each cohort's reader
// claim, keeps a capable and an older cohort apart, and survives the parser a
// facade applies before exposing it.
func TestObservedClientsShowReaderCapabilityAndKeepOlderCohortsUnknown(t *testing.T) {
	token := "synthetic-reader-capability-token"
	digest := sha256.Sum256([]byte(token))
	s := &Server{clients: map[[32]byte]client{digest: {principal: "agent:cohort", role: "agent"}}, obs: newObserver()}
	capable := declare(1, func(d *ClientDiagnostics) { d.ReaderCapabilities = CurrentReaderCapabilities() })
	older := declare(1)
	for _, header := range []string{capable, older} {
		if got := post(s, "/v1/version", token, `{}`, map[string][]string{ClientDiagnosticsHeader: {header}}); got.Code != http.StatusOK {
			t.Fatalf("observation status %d", got.Code)
		}
	}
	response := post(s, "/v1/clients", token, `{}`, nil)
	var envelope struct {
		Data json.RawMessage `json:"data"`
	}
	if response.Code != http.StatusOK || json.Unmarshal(response.Body.Bytes(), &envelope) != nil {
		t.Fatalf("clients status %d", response.Code)
	}
	view, err := ParseClientsResponse(envelope.Data, 50)
	if err != nil || len(view.Rows) != 2 {
		t.Fatalf("two declarations are two cohorts the facade accepts: %v %d", err, len(view.Rows))
	}
	claims := map[bool]int{}
	for _, row := range view.Rows {
		claims[row.Reported.ReaderCapabilities != nil && row.Reported.ReaderCapabilities.SourceOpening]++
	}
	if claims[true] != 1 || claims[false] != 1 {
		t.Fatalf("the capable cohort and the older one must differ: %v", claims)
	}
}
