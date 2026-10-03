package localapi

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestParseSourceOpeningBindingIsStrict(t *testing.T) {
	for value, want := range map[string][]string{"": nil, "agent:a": {"agent:a"}, "agent:a,machine:m/agent": {"agent:a", "machine:m/agent"}} {
		got, err := ParseSourceOpeningBinding(value)
		if err != nil || strings.Join(got, "|") != strings.Join(want, "|") {
			t.Fatalf("%q: %v %v", value, got, err)
		}
	}
	// Anything doubtful is an error. It must never become a partial or wider set;
	// a wildcard is just a name, and a server refuses to start unless an identity has it.
	for _, value := range []string{",", "a,", ",a", "a,,b", " a", "a ", "a, b", "a,a", "a\nb", "a\x00", "\xff", strings.Repeat("x", 257)} {
		if got, err := ParseSourceOpeningBinding(value); core.Code(err) != "INVALID_REQUEST" || got != nil {
			t.Fatalf("%q accepted: %v %v", value, got, err)
		}
	}
	many := make([]string, maxAPIIdentities+1)
	for i := range many {
		many[i] = "p" + strings.Repeat("x", i%7) + string(rune('a'+i%26)) + string(rune('a'+i/26%26))
	}
	if _, err := ParseSourceOpeningBinding(strings.Join(many, ",")); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("more principals than identities were accepted: %v", err)
	}
}

type gateAPI struct {
	api  *Server
	repo map[string]string
}

func (g gateAPI) call(t *testing.T, token, op string, headers map[string]string, request any) (int, string, json.RawMessage) {
	t.Helper()
	body, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest("POST", "/v1/"+op, strings.NewReader(string(body)))
	req.Header.Set("Authorization", "Bearer "+token)
	for name, value := range headers {
		req.Header.Set(name, value)
	}
	w := httptest.NewRecorder()
	g.api.ServeHTTP(w, req)
	var reply struct {
		Status string          `json:"status"`
		Data   json.RawMessage `json:"data"`
	}
	if err = json.Unmarshal(w.Body.Bytes(), &reply); err != nil {
		t.Fatalf("%s: %v %s", op, err, w.Body.String())
	}
	return w.Code, reply.Status, reply.Data
}

func newGateAPI(t *testing.T, enabled ...string) (gateAPI, map[string]string) {
	t.Helper()
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	g := gateAPI{repo: map[string]string{}}
	tokens := map[string]string{}
	var identities []Identity
	for _, principal := range []string{"gate:listed", "gate:unlisted"} {
		token := "synthetic-" + uuid.NewString()
		digest := sha256.Sum256([]byte(token))
		repo := uuid.NewString()
		identities = append(identities, Identity{TokenSHA256: hex.EncodeToString(digest[:]), Principal: principal, Repo: repo, Role: "agent", Destination: "hosted"})
		tokens[principal], g.repo[principal] = token, repo
	}
	var err error
	g.api, err = New(context.Background(), dsn, identities, WithSourceOpeningBinding(enabled))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(g.api.Close)
	return g, tokens
}

// An enabled profile binds its receipts whether or not its requests carry agent
// session headers, and enabling one profile never binds another, session or not.
func TestSourceOpeningBindingFollowsTheAuthenticatedProfileThroughAgentSessions(t *testing.T) {
	g, tokens := newGateAPI(t, "gate:listed")
	for principal, want := range map[string]bool{"gate:listed": true, "gate:unlisted": false} {
		token, repo := tokens[principal], g.repo[principal]
		body := "Project gate fixture. " + strings.Repeat("context ", 40) + "tail"
		draft := core.Draft{Kind: "note", Body: body, Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, Sensitivity: "shareable", ClaimType: "self"}
		if code, status, _ := g.call(t, token, "create", nil, core.CreateRequest{RequestID: uuid.NewString(), Draft: draft}); code != 200 {
			t.Fatalf("%s create: %d %s", principal, code, status)
		}
		var registered core.AgentInstance
		_, _, data := g.call(t, token, "agent-register", nil, core.RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "gate", NativeSessionID: "native-" + uuid.NewString(),
			Metadata: core.AgentMetadata{Harness: "codex", Project: "gate", Workspace: "/gate", State: "busy", DeliveryMode: "existing-session"}})
		if err := json.Unmarshal(data, &registered); err != nil || registered.AgentID == "" {
			t.Fatalf("%s register: %v %s", principal, err, data)
		}
		session := map[string]string{"Cairn-Agent-ID": registered.AgentID, "Cairn-Execution-ID": registered.ExecutionID}
		for name, headers := range map[string]map[string]string{"profile": nil, "agent session": session} {
			code, _, data := g.call(t, token, "index", headers, core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Purpose: "context", AvailableTokens: 64000})
			var idx core.IndexResult
			if code != 200 || json.Unmarshal(data, &idx) != nil || len(idx.Handles) != 1 {
				t.Fatalf("%s/%s index: %d %s", principal, name, code, data)
			}
			pull := core.ExpandRequest{RequestID: uuid.NewString(), ReceiptID: idx.Package.ReceiptID, Handle: idx.Handles[0].Handle, Span: &core.ByteSpanRequest{Offset: len(body) - 4, Length: 4}}
			code, status, _ := g.call(t, token, "expand", headers, pull)
			if bound := code == 426; bound != want || (!bound && code != 200) || (bound && status != "PROTOCOL_UNSUPPORTED") {
				t.Fatalf("%s/%s: undeclared partial read gave %d %s, bound want %v", principal, name, code, status, want)
			}
			declared := map[string]string{SourceOpeningHeader: "1"}
			for k, v := range headers {
				declared[k] = v
			}
			pull.RequestID = uuid.NewString()
			code, _, data = g.call(t, token, "expand", declared, pull)
			var got core.Expansion
			if code != 200 || json.Unmarshal(data, &got) != nil || (got.SourceOpening != nil) != want {
				t.Fatalf("%s/%s: declared read gave %d, pair present %v, want %v: %s", principal, name, code, got.SourceOpening != nil, want, data)
			}
		}
	}
}

func TestVersionDeclaresReaderGuardAndThePrincipalsOwnBinding(t *testing.T) {
	g, tokens := newGateAPI(t, "gate:listed")
	for principal, want := range map[string]bool{"gate:listed": true, "gate:unlisted": false} {
		// Session headers select a view of the store; the declaration is still the profile's.
		for _, headers := range []map[string]string{nil, {"Cairn-Agent-ID": uuid.NewString(), "Cairn-Execution-ID": uuid.NewString()}} {
			code, _, data := g.call(t, tokens[principal], "version", headers, struct{}{})
			var reply map[string]json.RawMessage
			if code != 200 || json.Unmarshal(data, &reply) != nil {
				t.Fatalf("%s version: %d %s", principal, code, data)
			}
			got, ok := ParseSourceOpeningCapabilities(reply["source_opening_capabilities"])
			if !ok || !got.ReaderGuard || got.ReceiptBinding != want {
				t.Fatalf("%s: declaration %+v %v, binding want %v", principal, got, ok, want)
			}
			if _, ok = ParseRetrievalCapabilities(reply["retrieval_capabilities"]); !ok {
				t.Fatalf("%s: the closed retrieval declaration changed", principal)
			}
		}
	}
	if code, _, _ := g.call(t, "wrong", "version", nil, struct{}{}); code != 401 {
		t.Fatalf("unauthenticated version leaked a declaration: %d", code)
	}
}

func TestServerRefusesToStartWithAnUnconfiguredBindingPrincipal(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	digest := sha256.Sum256([]byte("synthetic-start"))
	identities := []Identity{{TokenSHA256: hex.EncodeToString(digest[:]), Principal: "gate:only", Repo: uuid.NewString(), Role: "agent", Destination: "hosted"}}
	for _, principals := range [][]string{{"gate:other"}, {"gate:only", "gate:other"}, {""}, {"*"}, {"agent/" + uuid.NewString()}} {
		if api, err := New(context.Background(), dsn, identities, WithSourceOpeningBinding(principals)); err == nil || core.Code(err) != "INVALID_REQUEST" {
			if api != nil {
				api.Close()
			}
			t.Fatalf("%v started a server: %v", principals, err)
		}
	}
	api, err := New(context.Background(), dsn, identities)
	if err != nil {
		t.Fatal(err)
	}
	api.Close()
}
