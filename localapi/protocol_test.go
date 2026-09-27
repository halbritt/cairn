package localapi

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
)

func TestParseProtocolIsCanonicalAndAbsenceIsLegacy(t *testing.T) {
	for _, test := range []struct {
		values      []string
		version     int
		present, ok bool
	}{
		{nil, 1, false, true},
		{[]string{"2"}, 2, true, true},
		{[]string{"9999"}, 9999, true, true},
		{[]string{""}, 0, true, false},
		{[]string{"02"}, 0, true, false},
		{[]string{"0"}, 0, true, false},
		{[]string{"+2"}, 0, true, false},
		{[]string{"-1"}, 0, true, false},
		{[]string{"2.0"}, 0, true, false},
		{[]string{" 2"}, 0, true, false},
		{[]string{"10000"}, 0, true, false},
		{[]string{"2", "2"}, 0, true, false},
		{[]string{"٢"}, 0, true, false},
	} {
		header := http.Header{}
		for _, value := range test.values {
			header.Add(ProtocolHeader, value)
		}
		version, present, ok := ParseProtocol(header, ProtocolHeader)
		if ok != test.ok || present != test.present || (ok && version != test.version) {
			t.Fatalf("%q: got %d %v %v", test.values, version, present, ok)
		}
	}
}

// There is no negotiation: a client declares its current protocol, and
// overlapping ranges alone do not let it talk to a declaring server.
func TestEffectiveProtocolHasNoNegotiation(t *testing.T) {
	for _, test := range []struct {
		client, server ProtocolRange
		declared       bool
		want           int
	}{
		{ProtocolRange{1, 2}, ProtocolRange{1, 2}, true, 2},
		{ProtocolRange{1, 2}, ProtocolRange{2, 5}, true, 2},
		{ProtocolRange{1, 2}, ProtocolRange{1, 1}, true, 0}, // Overlaps at 1, but the client declares 2.
		{ProtocolRange{2, 3}, ProtocolRange{1, 2}, true, 0}, // Overlaps at 2, but the client declares 3.
		{ProtocolRange{1, 2}, ProtocolRange{3, 4}, true, 0},
		{ProtocolRange{1, 2}, LegacyProtocol, false, 1},
		{ProtocolRange{2, 3}, LegacyProtocol, false, 0},
	} {
		if got := test.client.Effective(test.server, test.declared); got != test.want {
			t.Fatalf("client %v server %v declared %v: %d want %d", test.client, test.server, test.declared, got, test.want)
		}
	}
}

// protocolServer has no store: any request that got past admission to a store
// route would panic, so a refusal proves nothing executed.
func protocolServer() *Server {
	return &Server{clients: map[[32]byte]client{sha256.Sum256([]byte("synthetic-protocol-token")): {}}}
}

type envelope struct {
	OK       bool           `json:"ok"`
	Status   string         `json:"status"`
	Message  string         `json:"message"`
	Protocol *ProtocolRange `json:"protocol"`
	Data     struct {
		Schema   string         `json:"schema"`
		Protocol *ProtocolRange `json:"protocol"`
	} `json:"data"`
}

func protocolCall(t *testing.T, handler http.Handler, path string, headers map[string][]string) (int, envelope) {
	t.Helper()
	request := httptest.NewRequest("POST", path, strings.NewReader("{}"))
	request.Header.Set("Authorization", "Bearer synthetic-protocol-token")
	for name, values := range headers {
		for _, value := range values {
			request.Header.Add(name, value)
		}
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	var reply envelope
	if err := json.Unmarshal(response.Body.Bytes(), &reply); err != nil {
		t.Fatalf("%s: %v: %s", path, err, response.Body)
	}
	if reply.Protocol == nil || *reply.Protocol != Protocol {
		t.Fatalf("reply lacks this server's protocol range: %s", response.Body)
	}
	return response.Code, reply
}

func TestServerAdmitsDeclaredAndLegacyProtocolsAndDeclaresItsRange(t *testing.T) {
	server := protocolServer()
	for _, headers := range []map[string][]string{nil, {ProtocolHeader: {"1"}}, {ProtocolHeader: {"2"}}, {ProtocolHeader: {"2"}, RelayProtocolHeader: {"2"}}} {
		code, reply := protocolCall(t, server, "/v1/version", headers)
		declared, _, _ := ParseProtocol(http.Header(headers), ProtocolHeader)
		if !Protocol.Supports(declared) {
			if code != 426 || reply.Status != "PROTOCOL_UNSUPPORTED" {
				t.Fatalf("legacy request after minimum raised: %d %+v", code, reply)
			}
			continue
		}
		if code != 200 || !reply.OK || reply.Data.Schema != "cairn.build/1" || reply.Data.Protocol == nil || *reply.Data.Protocol != Protocol {
			t.Fatalf("%v: %d %+v", headers, code, reply)
		}
	}
}

func TestServerRefusesUnsupportedOrMalformedProtocolBeforeDispatch(t *testing.T) {
	server := protocolServer()
	unsupported := []map[string][]string{{ProtocolHeader: {strconv.Itoa(Protocol.Current + 1)}}}
	if Protocol.Min > 1 {
		unsupported = append(unsupported, map[string][]string{ProtocolHeader: {strconv.Itoa(Protocol.Min - 1)}}, nil)
	}
	for _, headers := range unsupported {
		// A store route with no store: reaching dispatch would panic.
		code, reply := protocolCall(t, server, "/v1/create", headers)
		if code != 426 || reply.OK || reply.Status != "PROTOCOL_UNSUPPORTED" ||
			!strings.Contains(reply.Message, "supports protocols "+strconv.Itoa(Protocol.Min)+" to "+strconv.Itoa(Protocol.Current)) {
			t.Fatalf("%v: %d %+v", headers, code, reply)
		}
	}
	for _, headers := range []map[string][]string{
		{ProtocolHeader: {"02"}}, {ProtocolHeader: {"2", "2"}}, {ProtocolHeader: {"two"}},
		{ProtocolHeader: {"2"}, RelayProtocolHeader: {"x"}}, {RelayProtocolHeader: {"2", "2"}},
	} {
		code, reply := protocolCall(t, server, "/v1/create", headers)
		if code != 400 || reply.Status != "INVALID_REQUEST" {
			t.Fatalf("%v: %d %+v", headers, code, reply)
		}
	}
	// Authentication still comes first: a version claim is never identity.
	request := httptest.NewRequest("POST", "/v1/version", strings.NewReader("{}"))
	request.Header.Set(ProtocolHeader, "2")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	if response.Code != 401 {
		t.Fatalf("unauthenticated protocol claim: %d", response.Code)
	}
}

func TestRelayForwardsProtocolAndStatesItsOwn(t *testing.T) {
	seen := make(chan http.Header, 4)
	upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen <- r.Header.Clone()
		_, _ = io.WriteString(w, `{"schema":"cairn.response/1","ok":true,"status":"OK","data":{}}`)
	}))
	defer upstream.Close()
	relay := trustedRelay(t, upstream)
	for _, test := range []struct {
		client, spoofRelay []string
		status             int
		want               string
	}{
		{[]string{"2"}, []string{"99"}, 200, "2"},
		{nil, nil, 200, ""},
		{[]string{"2", "3"}, nil, 400, ""},
	} {
		request := httptest.NewRequest("POST", "/v1/version", strings.NewReader("{}"))
		for _, value := range test.client {
			request.Header.Add(ProtocolHeader, value)
		}
		for _, value := range test.spoofRelay {
			request.Header.Add(RelayProtocolHeader, value)
		}
		response := httptest.NewRecorder()
		relay.ServeHTTP(response, request)
		if response.Code != test.status {
			t.Fatalf("%v: %d %s", test.client, response.Code, response.Body)
		}
		if test.status != 200 {
			continue
		}
		header := <-seen
		if header.Get(ProtocolHeader) != test.want || len(header.Values(ProtocolHeader)) > 1 ||
			header.Values(RelayProtocolHeader)[0] != strconv.Itoa(Protocol.Current) || len(header.Values(RelayProtocolHeader)) != 1 {
			t.Fatalf("forwarded headers %v", header)
		}
	}
}

func TestClientDeclaresProtocolAndToleratesNewerReplies(t *testing.T) {
	directory := t.TempDir()
	socket := filepath.Join(directory, "api.sock")
	token := filepath.Join(directory, "token")
	if err := os.WriteFile(token, []byte("synthetic"), 0600); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	declared := make(chan string, 1)
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		declared <- r.Header.Get(ProtocolHeader)
		// A newer server adds envelope and data fields this client does not know.
		_, _ = io.WriteString(w, `{"schema":"cairn.response/1","ok":true,"status":"OK","protocol":{"min":1,"current":7},"future_envelope":true,"data":{"schema":"cairn.build/1","go_version":"go","future_field":{"nested":[1]}}}`)
	})}
	go server.Serve(listener)
	defer server.Close()
	c, err := NewClient(socket, token)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	var info VersionInfo
	if err = c.Call(context.Background(), "version", struct{}{}, &info); err != nil || info.GoVersion != "go" {
		t.Fatalf("newer reply refused: %+v %v", info, err)
	}
	if got := <-declared; got != strconv.Itoa(Protocol.Current) {
		t.Fatalf("client declared %q", got)
	}
}

func bodyCall(t *testing.T, path string, headers map[string]string, body string) (int, envelope) {
	t.Helper()
	request := httptest.NewRequest("POST", path, strings.NewReader(body))
	request.Header.Set("Authorization", "Bearer synthetic-protocol-token")
	for name, value := range headers {
		request.Header.Set(name, value)
	}
	response := httptest.NewRecorder()
	protocolServer().ServeHTTP(response, request)
	var reply envelope
	if err := json.Unmarshal(response.Body.Bytes(), &reply); err != nil {
		t.Fatalf("%s: %v: %s", body, err, response.Body)
	}
	return response.Code, reply
}

func TestServerVerifiesAndStripsTheBodyProtocolGuard(t *testing.T) {
	version := strconv.Itoa(Protocol.Current)
	// Accepted and stripped: the version route's request is an empty struct,
	// so any remaining unknown field would be refused by strict decoding.
	if code, reply := bodyCall(t, "/v1/version", nil, `{"cairn_protocol":`+version+`}`); code != 200 || !reply.OK {
		t.Fatalf("guard not accepted: %d %+v", code, reply)
	}
	if code, reply := bodyCall(t, "/v1/version", map[string]string{ProtocolHeader: version}, `{"cairn_protocol":`+version+`}`); code != 200 || !reply.OK {
		t.Fatalf("matching header and guard: %d %+v", code, reply)
	}
	// A minimum this server cannot honor is refused before dispatch (no store).
	code, reply := bodyCall(t, "/v1/create", nil, `{"cairn_protocol":`+strconv.Itoa(Protocol.Current+1)+`,"request_id":"x"}`)
	if code != 426 || reply.Status != "PROTOCOL_UNSUPPORTED" || !strings.Contains(reply.Message, "requires protocol") {
		t.Fatalf("unsupported guard: %d %+v", code, reply)
	}
	for _, test := range []struct {
		headers map[string]string
		body    string
	}{
		{nil, `{"cairn_protocol":"2"}`},
		{nil, `{"cairn_protocol":0}`},
		{nil, `{"cairn_protocol":-1}`},
		{nil, `{"cairn_protocol":2.0}`},
		{nil, `{"cairn_protocol":1e0}`},
		{nil, `{"cairn_protocol":null}`},
		{map[string]string{ProtocolHeader: "1"}, `{"cairn_protocol":2}`},
		{nil, `{"Cairn_Protocol":2}`},
	} {
		if Protocol.Min > 1 && test.headers == nil && test.body == `{"Cairn_Protocol":2}` {
			continue // Without a header a raised server refuses first with 426.
		}
		want, status := 400, "INVALID_REQUEST"
		header := http.Header{}
		for name, value := range test.headers {
			header.Set(name, value)
		}
		if declared, present, ok := ParseProtocol(header, ProtocolHeader); present && ok && !Protocol.Supports(declared) {
			want, status = 426, "PROTOCOL_UNSUPPORTED" // The header alone is already unsupported.
		}
		code, reply := bodyCall(t, "/v1/create", test.headers, test.body)
		if code != want || reply.Status != status {
			t.Fatalf("%v %s: %d %+v", test.headers, test.body, code, reply)
		}
	}
}

func TestClientGuardFollowsItsMinimum(t *testing.T) {
	for _, body := range []string{`{}`, `{"request_id":"a"}`, `[]`, `null`} {
		raw, err := guardBody([]byte(body))
		if Protocol.Min > 1 && (body == `[]` || body == `null`) {
			// A non-object cannot carry the guard, so it is never sent.
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("%s sent without a guard: %v", body, err)
			}
			continue
		}
		if err != nil {
			t.Fatal(err)
		}
		guarded := string(raw)
		if Protocol.Min <= 1 || body == `[]` || body == `null` {
			if guarded != body {
				t.Fatalf("minimum %d changed %s to %s", Protocol.Min, body, guarded)
			}
			continue
		}
		var fields map[string]any
		if err := json.Unmarshal([]byte(guarded), &fields); err != nil || fields[BodyProtocolField] != float64(Protocol.Min) {
			t.Fatalf("guard missing: %s", guarded)
		}
	}
}

// Removing the guard must not change how the rest of the request decodes:
// duplicate members and differently cased keys keep their bytes and order.
func TestBodyGuardRemovalPreservesEveryOtherMember(t *testing.T) {
	version := strconv.Itoa(Protocol.Current)
	for _, unguarded := range []string{
		`{"request_id":"r","draft":{"body":"first"},"draft":{"kind":"note"}}`,
		`{"request_id":"r","draft":{"body":"lower","BODY":"upper"}}`,
		`{"request_id":"r","draft":{"BODY":"upper","body":"lower"}}`,
		`{"request_id":"first","REQUEST_ID":"second"}`,
		` { "request_id" : "spaced" , "draft" : { "body" : "x" } } `,
		`{"draft":{"cairn_protocol":1},"note":"cairn_protocol"}`,
	} {
		trimmed := strings.TrimSpace(unguarded)
		guarded := `{"cairn_protocol":` + version + `,` + trimmed[1:]
		request := httptest.NewRequest("POST", "/v1/create", strings.NewReader(guarded))
		response := httptest.NewRecorder()
		stripped, ok := admitBodyProtocol(response, request, []byte(guarded))
		if !ok {
			t.Fatalf("%s refused: %s", guarded, response.Body)
		}
		var want, got core.CreateRequest
		wantErr := json.Unmarshal([]byte(unguarded), &want)
		gotErr := json.Unmarshal(stripped, &got)
		if (wantErr == nil) != (gotErr == nil) || !reflect.DeepEqual(want, got) {
			t.Fatalf("decoding changed:\n%s\n%s\n%+v %v\n%+v %v", unguarded, stripped, want, wantErr, got, gotErr)
		}
		members, _, _ := topLevelMembers([]byte(unguarded))
		strippedMembers, _, _ := topLevelMembers(stripped)
		if !reflect.DeepEqual(members, strippedMembers) {
			t.Fatalf("member bytes or order changed: %s -> %s", unguarded, stripped)
		}
		// Without a guard the body passes through byte-for-byte.
		plain, ok := admitBodyProtocol(httptest.NewRecorder(), httptest.NewRequest("POST", "/v1/create", nil), []byte(unguarded))
		if Protocol.Min <= 1 && (!ok || string(plain) != unguarded) {
			t.Fatalf("unguarded body changed: %s -> %s", unguarded, plain)
		}
	}
	// Keys are compared after unescaping: an escaped exact name is the guard.
	escaped := `{"cairn\u005fprotocol":` + version + `,"request_id":"r"}`
	if stripped, ok := admitBodyProtocol(httptest.NewRecorder(), httptest.NewRequest("POST", "/v1/create", nil), []byte(escaped)); !ok || string(stripped) != `{"request_id":"r"}` {
		t.Fatalf("escaped guard: %s %v", stripped, ok)
	}
	for _, refused := range []string{
		`{"CAIRN\u005fPROTOCOL":2}`,
		`{"cairn\u005fprotocol":2,"cairn_protocol":2}`,
		`{"cairn_protocol":2,"cairn_protocol":2}`,
		`{"cairn_protocol":2,"request_id":"r","cairn_protocol":1}`,
		`{"cairn_protocol":2,"CAIRN_PROTOCOL":1}`,
		`{"Cairn_Protocol":2,"cairn_protocol":2}`,
	} {
		response := httptest.NewRecorder()
		if _, ok := admitBodyProtocol(response, httptest.NewRequest("POST", "/v1/create", nil), []byte(refused)); ok || response.Code != 400 {
			t.Fatalf("%s: %d %s", refused, response.Code, response.Body)
		}
	}
}

func TestClientRefusesCallerSuppliedGuard(t *testing.T) {
	for _, body := range []string{`{"cairn_protocol":1}`, `{"request_id":"r","CAIRN_PROTOCOL":1}`, `{"cairn\u005fprotocol":1}`, `{"Cairn\u005FProtocol":1}`} {
		if _, err := guardBody([]byte(body)); core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("%s accepted: %v", body, err)
		}
	}
	// A value that merely mentions the name is not a top-level key.
	body := `{"draft":{"body":"cairn_protocol","cairn_protocol":1}}`
	guarded, err := guardBody([]byte(body))
	if err != nil || (Protocol.Min <= 1 && string(guarded) != body) {
		t.Fatalf("%s: %s %v", body, guarded, err)
	}
}
