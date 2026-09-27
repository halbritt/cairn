package apicontract

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"sort"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

const decodeRefusal = "invalid bounded JSON request"

type conformance struct {
	t         *testing.T
	server    *localapi.Server
	remote    http.Handler
	validator *Validator
	ops       map[string]map[string]any
	errors    map[string]bool
	repo      string
	tokens    map[string]string
}

func newConformance(t *testing.T) *conformance {
	t.Helper()
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	ctx := context.Background()
	fixture, err := core.Open(ctx, dsn, core.Channel{Principal: "contract-fixture"})
	if err != nil {
		t.Fatal(err)
	}
	if err = fixture.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	fixture.Close()
	doc, raw := document(t)
	validator, err := NewValidator(raw)
	if err != nil {
		t.Fatal(err)
	}
	validator.Strict = true
	c := &conformance{t: t, validator: validator, ops: operations(t, doc), errors: map[string]bool{}, repo: "/contract/" + uuid.NewString(), tokens: map[string]string{}}
	// Store codes serveJSON does not map use the default status.
	for _, code := range doc["x-cairn-store-codes"].([]any) {
		c.errors[code.(string)+"/"+jsonString(doc["x-cairn-default-error-status"])] = true
	}
	for _, entry := range doc["x-cairn-errors"].([]any) {
		e := entry.(map[string]any)
		c.errors[e["code"].(string)+"/"+jsonString(e["http_status"])] = true
		c.errors["*/"+jsonString(e["http_status"])] = c.errors["*/"+jsonString(e["http_status"])] || e["code"] == "*"
	}
	suffix := uuid.NewString()[:8]
	var identities []localapi.Identity
	add := func(name, principal, role, destination string, remote bool, machine string) {
		token := name + "-" + uuid.NewString()
		digest := sha256.Sum256([]byte(token))
		c.tokens[name] = token
		identities = append(identities, localapi.Identity{TokenSHA256: hex.EncodeToString(digest[:]), Principal: principal, Repo: c.repo, Role: role, Destination: destination, Remote: remote, MachineID: machine})
	}
	add("local", "agent/contract-local-"+suffix, "agent", "local", false, "")
	add("hosted", "agent/contract-hosted-"+suffix, "agent", "hosted", false, "")
	add("remote", "machine:contract-"+suffix+"/agent", "agent", "hosted", true, "contract-"+suffix)
	add("remote-observer", "machine:contract-"+suffix+"/observer", "observer", "hosted", true, "contract-"+suffix)
	if c.server, err = localapi.New(ctx, dsn, identities); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(c.server.Close)
	c.remote = c.server.RemoteHandler()
	return c
}

type reply struct {
	status int
	body   []byte
	parsed struct {
		OK      bool            `json:"ok"`
		Status  string          `json:"status"`
		Message string          `json:"message"`
		Data    json.RawMessage `json:"data"`
	}
}

func (c *conformance) call(handler http.Handler, token, operation, body string, headers map[string]string) reply {
	c.t.Helper()
	request := httptest.NewRequest("POST", "/v1/"+operation, strings.NewReader(body))
	request.Header.Set("Authorization", "Bearer "+c.tokens[token])
	request.Header.Set("Content-Type", "application/json")
	for name, value := range headers {
		request.Header.Set(name, value)
	}
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	out := reply{status: recorder.Code, body: recorder.Body.Bytes()}
	if err := json.Unmarshal(out.body, &out.parsed); err != nil {
		c.t.Fatalf("%s: reply is not JSON: %s", operation, out.body)
	}
	c.conforms(operation, out)
	return out
}

// conforms checks a real reply against the documented envelope and schemas.
func (c *conformance) conforms(operation string, out reply) {
	c.t.Helper()
	op := c.ops[operation]
	if out.status == 200 {
		schema := op["responses"].(map[string]any)["200"].(map[string]any)["content"].(map[string]any)["application/json"].(map[string]any)["schema"]
		if err := c.validator.Validate(schema, out.body); err != nil {
			c.t.Errorf("%s success reply violates the contract: %v\n%s", operation, err, out.body)
		}
		return
	}
	if err := c.validator.Validate(map[string]any{"$ref": "#/components/schemas/Error"}, out.body); err != nil {
		c.t.Errorf("%s error reply violates the contract: %v\n%s", operation, err, out.body)
	}
	pair := out.parsed.Status + "/" + jsonString(out.status)
	if !c.errors[pair] && !c.errors["*/"+jsonString(out.status)] {
		c.t.Errorf("%s: %s is not in x-cairn-errors", operation, pair)
	}
}

func (c *conformance) ok(token, operation, body string) json.RawMessage {
	c.t.Helper()
	out := c.call(c.server, token, operation, body, nil)
	if out.status != 200 {
		c.t.Fatalf("%s: HTTP %d %s", operation, out.status, out.body)
	}
	return out.parsed.Data
}

func sortedOperations(ops map[string]map[string]any) []string {
	names := make([]string, 0, len(ops))
	for name := range ops {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// TestRealRepliesConformToContract exercises real handlers on a disposable
// database and validates every reply strictly against the generated schemas.
func TestRealRepliesConformToContract(t *testing.T) {
	c := newConformance(t)
	c.ok("local", "version", `{}`)
	created := c.ok("local", "create", `{"request_id":"`+uuid.NewString()+`","draft":{"kind":"note","body":"contract fixture","scope":{"repo":"`+c.repo+`","task_id":"t","run_id":"r"},"claim_type":"self","sensitivity":"shareable"}}`)
	var record core.Record
	if err := json.Unmarshal(created, &record); err != nil {
		t.Fatal(err)
	}
	c.ok("local", "get", `{"record_id":"`+record.RecordID+`"}`)
	c.ok("local", "history", `{"record_id":"`+record.RecordID+`","version":1}`)
	c.ok("hosted", "get", `{"record_id":"`+record.RecordID+`"}`)
	c.ok("local", "index", `{"request_id":"`+uuid.NewString()+`","purpose":"context","query":"contract fixture","available_tokens":4000,"scope":{"repo":"`+c.repo+`","task_id":"t","run_id":"r"}}`)
	registered := c.ok("remote", "agent-register", `{"request_id":"`+uuid.NewString()+`","binding":"contract","native_session_id":"`+uuid.NewString()+`","metadata":{"harness":"codex","project":"cairn","workspace":"/tmp/contract","state":"idle","delivery_mode":"existing-session"}}`)
	var agent core.AgentInstance
	if err := json.Unmarshal(registered, &agent); err != nil {
		t.Fatal(err)
	}
	c.ok("remote", "agent-heartbeat", `{"agent_id":"`+agent.AgentID+`","execution_id":"`+agent.ExecutionID+`"}`)
	c.ok("local", "agent-directory", `{}`)
	c.ok("local", "agent-resolve", `{"harness":"codex"}`)
	c.ok("local", "event-publish", `{"request_id":"`+uuid.NewString()+`","destination":{"type":"agent","name":"agent/`+agent.AgentID+`"},"kind":"notice","ref":{"record_id":"`+record.RecordID+`","version":1}}`)
	c.ok("local", "event-list", `{}`)
	c.ok("local", "event-metrics", `{}`)
	c.ok("local", "event-subscribe", `{"request_id":"`+uuid.NewString()+`","topic":"contract","active":true}`)
	c.ok("local", "event-subscriptions", `{}`)
	session := map[string]string{"Cairn-Agent-ID": agent.AgentID, "Cairn-Execution-ID": agent.ExecutionID}
	if out := c.call(c.server, "remote", "session-inbox-ready", `{"agent_id":"`+agent.AgentID+`","execution_id":"`+agent.ExecutionID+`"}`, nil); out.status != 200 {
		t.Fatalf("session-inbox-ready: %d %s", out.status, out.body)
	}
	c.call(c.server, "remote", "event-watch", `{}`, session)
	c.ok("local", "preview-retract", `{"record_id":"`+record.RecordID+`"}`)
	// Representative refusals must also match the error envelope and inventory.
	c.call(c.server, "local", "get", `{"record_id":"`+uuid.NewString()+`"}`, nil)
	c.call(c.server, "local", "create", `{"request_id":"not-a-uuid"}`, nil)
	c.call(c.server, "local", "agent-directory", `{}`, session)
}

// TestAcceptedInputMatchesDecoder sends documented accepted and rejected wire
// forms to every real handler.
func TestAcceptedInputMatchesDecoder(t *testing.T) {
	c := newConformance(t)
	for _, operation := range sortedOperations(c.ops) {
		out := c.call(c.server, "local", operation, `{"x_contract_unknown_field":true}`, nil)
		if out.status != 400 || out.parsed.Message != decodeRefusal {
			t.Errorf("%s: unknown field was not refused by decoding: %d %s", operation, out.status, out.body)
		}
		accepted := c.ops[operation]["requestBody"].(map[string]any)["content"].(map[string]any)["application/json"].(map[string]any)["schema"]
		if c.validator.Validate(accepted, []byte(`{"x_contract_unknown_field":true}`)) == nil {
			t.Errorf("%s: accepted schema admits an unknown field", operation)
		}
		for _, body := range []string{`null`, `{}`} {
			if c.validator.Validate(accepted, []byte(body)) != nil {
				t.Errorf("%s: accepted schema refuses %s", operation, body)
			}
			if out := c.call(c.server, "local", operation, body, nil); out.parsed.Message == decodeRefusal {
				t.Errorf("%s: decoder refused %s that the contract accepts", operation, body)
			}
		}
		oversized := `{"x":"` + strings.Repeat("a", int(c.ops[operation]["x-cairn-request-limit-bytes"].(float64))) + `"}`
		if out := c.call(c.server, "local", operation, oversized, nil); out.status != 400 || out.parsed.Message != decodeRefusal {
			t.Errorf("%s: body over x-cairn-request-limit-bytes was not refused: %d", operation, out.status)
		}
	}
	// encoding/json matches keys case-insensitively; the contract documents it.
	created := c.ok("local", "create", `{"request_id":"`+uuid.NewString()+`","draft":{"kind":"note","body":"case fixture","scope":{"repo":"`+c.repo+`","task_id":"t","run_id":"r"},"claim_type":"self","sensitivity":"shareable"}}`)
	var record core.Record
	_ = json.Unmarshal(created, &record)
	c.ok("local", "get", `{"RECORD_ID":"`+record.RecordID+`"}`)
}

// TestRemoteAndDestinationClassificationMatchesServer checks every documented
// classification against the real handlers rather than the generator's inputs.
func TestRemoteAndDestinationClassificationMatchesServer(t *testing.T) {
	c := newConformance(t)
	const remoteRefusal = "operation is unavailable to remote machine profiles"
	for _, operation := range sortedOperations(c.ops) {
		op := c.ops[operation]
		out := c.call(c.remote, "remote", operation, `{}`, nil)
		refused := out.status == 403 && out.parsed.Message == remoteRefusal
		if (op["x-cairn-remote"] == "allowed") == refused {
			t.Errorf("%s: documented remote %v but server refused=%v", operation, op["x-cairn-remote"], refused)
		}
		observer := c.call(c.remote, "remote-observer", operation, `{}`, nil)
		observerRefused := observer.status == 403 && observer.parsed.Message == remoteRefusal
		observerAllowed := false
		for _, role := range op["x-cairn-remote-roles"].([]any) {
			observerAllowed = observerAllowed || role == "observer"
		}
		if observerAllowed == observerRefused {
			t.Errorf("%s: documented remote observer %v but server refused=%v", operation, observerAllowed, observerRefused)
		}
		local := c.call(c.remote, "local", operation, `{}`, nil)
		if local.status != 403 || local.parsed.Status != "AUTHORITY_DENIED" {
			t.Errorf("%s: local profile reached the network handler: %d", operation, local.status)
		}
		hosted := c.call(c.server, "hosted", operation, `{}`, nil)
		guarded := hosted.status == 403 && strings.Contains(hosted.parsed.Message, "a local profile")
		if guarded != (op["x-cairn-requires-local-destination"] == true) {
			t.Errorf("%s: documented local-destination guard %v but server guarded=%v", operation, op["x-cairn-requires-local-destination"], guarded)
		}
		if op["x-cairn-session-headers"] == "forbidden" {
			with := c.call(c.server, "local", operation, `{}`, map[string]string{"Cairn-Agent-ID": uuid.NewString(), "Cairn-Execution-ID": uuid.NewString()})
			if with.status != 400 {
				t.Errorf("%s: session headers are documented as forbidden but got %d", operation, with.status)
			}
		}
	}
}
