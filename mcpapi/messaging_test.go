package mcpapi

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

var messagingToolNames = []string{"cairn_ack", "cairn_agents", "cairn_complete", "cairn_event_status", "cairn_inbox", "cairn_renew", "cairn_send"}

type fakeCall struct {
	Operation string
	AgentID   string
	Execution string
	Body      map[string]any
}

// fakeAPI is a synthetic local API. Every value it serves is a fixture.
type fakeAPI struct {
	t       *testing.T
	mu      sync.Mutex
	calls   []fakeCall
	handler func(call fakeCall) (any, *core.Error)
}

func (f *fakeAPI) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	raw, err := io.ReadAll(r.Body)
	if err != nil {
		f.t.Error(err)
	}
	call := fakeCall{Operation: strings.TrimPrefix(r.URL.Path, "/v1/"), AgentID: r.Header.Get("Cairn-Agent-ID"), Execution: r.Header.Get("Cairn-Execution-ID")}
	if err := json.Unmarshal(raw, &call.Body); err != nil {
		f.t.Errorf("%s: %v", call.Operation, err)
	}
	f.mu.Lock()
	f.calls = append(f.calls, call)
	f.mu.Unlock()
	data, failure := f.handler(call)
	w.Header().Set("Content-Type", "application/json")
	if failure != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": false, "status": failure.Code, "message": failure.Message})
		return
	}
	json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": data})
}

func (f *fakeAPI) taken() []fakeCall {
	f.mu.Lock()
	defer f.mu.Unlock()
	calls := f.calls
	f.calls = nil
	return calls
}

func operations(calls []fakeCall) []string {
	names := make([]string, 0, len(calls))
	for _, call := range calls {
		names = append(names, call.Operation)
	}
	return names
}

func serveAPI(t *testing.T, handler http.Handler) *localapi.Client {
	t.Helper()
	directory, err := os.MkdirTemp("", "cairn-mcp")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(directory) })
	socket := filepath.Join(directory, "api.sock")
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	server := &http.Server{Handler: handler}
	served := make(chan error, 1)
	go func() { served <- server.Serve(listener) }()
	t.Cleanup(func() {
		if err := server.Close(); err != nil {
			t.Error(err)
		}
		if err := <-served; !errors.Is(err, http.ErrServerClosed) {
			t.Error(err)
		}
	})
	token := filepath.Join(directory, "agent.token")
	if err = os.WriteFile(token, []byte("synthetic-mcp-token"), 0600); err != nil {
		t.Fatal(err)
	}
	client, err := localapi.NewClient(socket, token)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(client.Close)
	return client
}

const (
	selfAgent, selfExecution   = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
	peerAgent, peerExecution   = "33333333-3333-4333-8333-333333333333", "44444444-4444-4444-8444-444444444444"
	sourceRecord, resultRecord = "55555555-5555-4555-8555-555555555555", "66666666-6666-4666-8666-666666666666"
	deliveryID, leaseID        = "77777777-7777-4777-8777-777777777777", "88888888-8888-4888-8888-888888888888"
	requestEvent               = "99999999-9999-4999-8999-999999999999"
)

func fixtureAgent(ordinal int64, agent, execution, native string, online, stopped bool) core.AgentInstance {
	return core.AgentInstance{AgentID: agent, Ordinal: ordinal, DisplayName: "agent-" + itoa(ordinal), NativeSessionID: native, Inbox: "agent/" + agent, Repo: "repo", ExecutionID: execution, Metadata: core.AgentMetadata{Harness: "claude", Project: "cairn", State: "busy", DeliveryMode: "existing-session"}, Online: online, Stopped: stopped}
}

func itoa(n int64) string {
	encoded, _ := json.Marshal(n)
	return string(encoded)
}

// fixtureDirectory answers agent-directory like the store: ordinal order,
// exclusive cursor, online only unless include_offline, optional agent filter.
func fixtureDirectory(agents []core.AgentInstance, body map[string]any) core.AgentDirectoryPage {
	after, _ := body["after"].(float64)
	limit, _ := body["limit"].(float64)
	offline, _ := body["include_offline"].(bool)
	only, _ := body["agent_id"].(string)
	page := core.AgentDirectoryPage{Agents: []core.AgentInstance{}}
	for _, agent := range agents {
		if agent.Ordinal <= int64(after) || (!offline && (!agent.Online || agent.Stopped)) || (only != "" && agent.AgentID != only) {
			continue
		}
		if len(page.Agents) == int(limit) {
			page.More = true
			break
		}
		page.Agents = append(page.Agents, agent)
	}
	if len(page.Agents) > 0 {
		page.NextAfter = page.Agents[len(page.Agents)-1].Ordinal
	}
	return page
}

type messagingFixture struct {
	api     *fakeAPI
	session *mcp.ClientSession
	agents  []core.AgentInstance
	// failPublish refuses event-publish for these request kinds.
	failPublish map[string]bool
	body        string
}

func newMessagingFixture(t *testing.T, config Config) *messagingFixture {
	t.Helper()
	f := &messagingFixture{failPublish: map[string]bool{}, body: "Please review the synthetic change.", agents: []core.AgentInstance{
		fixtureAgent(7, selfAgent, selfExecution, "native-self", true, false),
		fixtureAgent(9, peerAgent, peerExecution, "native-peer", true, false),
	}}
	f.api = &fakeAPI{t: t}
	f.api.handler = func(call fakeCall) (any, *core.Error) {
		switch call.Operation {
		case "agent-directory":
			return fixtureDirectory(f.agents, call.Body), nil
		case "create":
			draft := call.Body["draft"].(map[string]any)
			return core.Record{RecordID: sourceRecord, Version: 1, Draft: core.Draft{Body: draft["body"].(string)}}, nil
		case "event-publish":
			kind := call.Body["kind"].(string)
			if f.failPublish[kind] {
				return nil, &core.Error{Code: "ADMISSION_REFUSED", Message: "synthetic refusal"}
			}
			var req core.PublishEventRequest
			encoded, _ := json.Marshal(call.Body)
			if err := json.Unmarshal(encoded, &req); err != nil {
				t.Error(err)
			}
			return core.AgentEvent{EventID: uuid.NewSHA1(uuid.Nil, []byte(req.RequestID)).String(), From: "agent/" + call.AgentID, Kind: req.Kind, Ref: req.Ref, Destination: req.Destination, CausationID: req.CausationID}, nil
		case "session-inbox-pending":
			return core.SessionInboxPending{Requests: 1, Notices: 2}, nil
		case "event-next":
			until := time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)
			return core.NextEventResult{Delivery: &core.AgentDelivery{DeliveryID: deliveryID, State: "leased", LeaseID: leaseID, LeaseUntil: &until, Attempts: 1, Event: core.AgentEvent{EventID: requestEvent, Kind: "request", From: "agent/" + peerAgent, Ref: core.RecordVersionRef{RecordID: sourceRecord, Version: 3}}}}, nil
		case "history":
			return core.RecordHistory{RecordID: sourceRecord, Versions: []core.HistoricalVersion{{Version: 3, PayloadAvailable: true, Body: &f.body}}}, nil
		case "event-complete", "event-renew":
			kind := "request"
			if call.Body["draft"] == nil {
				kind = "notice"
			}
			delivery := core.AgentDelivery{DeliveryID: deliveryID, State: "completed", LeaseID: leaseID, Event: core.AgentEvent{EventID: requestEvent, Kind: kind, From: "agent/" + peerAgent, Ref: core.RecordVersionRef{RecordID: sourceRecord, Version: 3}}}
			if call.Body["draft"] != nil {
				delivery.Result = &core.RecordVersionRef{RecordID: resultRecord, Version: 1}
			}
			return delivery, nil
		case "event-inspect":
			return core.EventStatus{Event: core.AgentEvent{EventID: requestEvent, Kind: "request", From: "agent/" + selfAgent, Destination: core.EventDestination{Type: "agent", Name: "agent/" + peerAgent}}, Deliveries: []core.AgentDelivery{{DeliveryID: deliveryID, Consumer: "agent/" + peerAgent, State: "completed", Result: &core.RecordVersionRef{RecordID: resultRecord, Version: 1}}}}, nil
		}
		t.Errorf("unexpected operation %s", call.Operation)
		return nil, &core.Error{Code: "INVALID_REQUEST", Message: "unexpected"}
	}
	config.Scope.Repo, config.Messaging = "repo", true
	if !config.CodexThread {
		config.Scope.TaskID, config.Scope.RunID = "task", "run"
	}
	if config.AvailableTokens == 0 {
		config.AvailableTokens = 32000
	}
	server, err := NewServer(serveAPI(t, f.api), config)
	if err != nil {
		t.Fatal(err)
	}
	f.session = connect(t, context.Background(), server)
	return f
}

// call returns the decoded result, or the error text when the tool refused.
func (f *messagingFixture) call(t *testing.T, meta mcp.Meta, name string, args any) (map[string]any, string) {
	t.Helper()
	result, err := f.session.CallTool(context.Background(), &mcp.CallToolParams{Meta: meta, Name: name, Arguments: args})
	if err != nil {
		t.Fatal(err)
	}
	text := result.Content[0].(*mcp.TextContent).Text
	if result.IsError {
		return nil, text
	}
	var decoded map[string]any
	if err := json.Unmarshal([]byte(text), &decoded); err != nil {
		t.Fatalf("%s: %v: %s", name, err, text)
	}
	return decoded, ""
}

func (f *messagingFixture) ok(t *testing.T, name string, args any) map[string]any {
	t.Helper()
	decoded, refusal := f.call(t, nil, name, args)
	if refusal != "" {
		t.Fatalf("%s refused: %s", name, refusal)
	}
	return decoded
}

func (f *messagingFixture) refused(t *testing.T, name string, args any, want string) {
	t.Helper()
	if _, refusal := f.call(t, nil, name, args); !strings.Contains(refusal, want) {
		t.Fatalf("%s: want refusal %q, got %q", name, want, refusal)
	}
}

func TestMessagingToolsAreOptIn(t *testing.T) {
	list := func(config Config) map[string]*mcp.Tool {
		config.Scope, config.AvailableTokens = core.Scope{Repo: "repo", TaskID: "task", RunID: "run"}, 32000
		server, err := NewServer(nil, config)
		if err != nil {
			t.Fatal(err)
		}
		listed, err := connect(t, context.Background(), server).ListTools(context.Background(), nil)
		if err != nil {
			t.Fatal(err)
		}
		tools := map[string]*mcp.Tool{}
		for _, tool := range listed.Tools {
			tools[tool.Name] = tool
		}
		return tools
	}
	plain := list(Config{})
	for _, name := range messagingToolNames {
		if _, ok := plain[name]; ok {
			t.Fatalf("%s exposed without Messaging", name)
		}
	}
	full, compact := list(Config{Messaging: true}), list(Config{Messaging: true, ToolProfile: "compact/2"})
	if len(full) != len(plain)+len(messagingToolNames) || len(compact) != len(full) {
		t.Fatalf("tool counts: plain=%d full=%d compact=%d", len(plain), len(full), len(compact))
	}
	var added []string
	for name, tool := range full {
		if previous, ok := plain[name]; ok {
			if !reflect.DeepEqual(previous, tool) {
				t.Fatalf("Messaging changed the %s declaration", name)
			}
			continue
		}
		added = append(added, name)
		short := compact[name]
		if short == nil || len(short.Description) == 0 || len(short.Description) >= len(tool.Description) {
			t.Fatalf("%s compact description did not shrink", name)
		}
		if !reflect.DeepEqual(short.InputSchema, tool.InputSchema) || !reflect.DeepEqual(short.Annotations, tool.Annotations) {
			t.Fatalf("%s compact profile changed its contract", name)
		}
	}
	sort.Strings(added)
	if !reflect.DeepEqual(added, messagingToolNames) {
		t.Fatal(added)
	}
	for _, name := range []string{"cairn_agents", "cairn_event_status"} {
		if !full[name].Annotations.ReadOnlyHint {
			t.Fatalf("%s is not declared read-only", name)
		}
	}
	for _, config := range []Config{{NativeSessionID: "native"}, {Messaging: true, CodexThread: true, NativeSessionID: "native"}, {Messaging: true, NativeSessionID: strings.Repeat("x", 257)}} {
		config.Scope = core.Scope{Repo: "repo", TaskID: "task", RunID: "run"}
		if config.CodexThread {
			config.Scope = core.Scope{Repo: "repo"}
		}
		config.AvailableTokens = 32000
		if _, err := NewServer(nil, config); err == nil {
			t.Fatalf("accepted %+v", config)
		}
	}
}

func TestSendResolvesSessionAndRecipient(t *testing.T) {
	f := newMessagingFixture(t, Config{NativeSessionID: "native-self"})
	request := uuid.NewString()
	args := map[string]any{"request_id": request, "to": "agent-9", "body": "Synthetic notice.", "urgency": "urgent"}
	result := f.ok(t, "cairn_send", args)
	calls := f.api.taken()
	if got := operations(calls); !reflect.DeepEqual(got, []string{"agent-directory", "agent-directory", "create", "event-publish"}) {
		t.Fatal(got)
	}
	// Directory reads use the base profile; writes select the session inbox.
	for _, call := range calls[:2] {
		if call.AgentID != "" || call.Execution != "" {
			t.Fatalf("directory read carried a session: %+v", call)
		}
	}
	if calls[1].Body["after"] != float64(8) || calls[1].Body["limit"] != float64(1) || calls[1].Body["include_offline"] != true {
		t.Fatalf("recipient lookup: %v", calls[1].Body)
	}
	create, publish := calls[2], calls[3]
	for _, call := range []fakeCall{create, publish} {
		if call.AgentID != selfAgent || call.Execution != selfExecution {
			t.Fatalf("%s did not select the caller's session: %+v", call.Operation, call)
		}
	}
	draft := create.Body["draft"].(map[string]any)
	if draft["body"] != "Synthetic notice." || draft["sensitivity"] != "shareable" || !reflect.DeepEqual(draft["scope"], map[string]any{"repo": "repo", "task_id": "coordination/" + request, "run_id": "*"}) {
		t.Fatalf("source draft: %v", draft)
	}
	sourceRequest := create.Body["request_id"].(string)
	if uuid.Validate(sourceRequest) != nil || sourceRequest == request || publish.Body["request_id"] != request {
		t.Fatalf("request IDs: source=%s publish=%v", sourceRequest, publish.Body["request_id"])
	}
	if !reflect.DeepEqual(publish.Body["destination"], map[string]any{"type": "agent", "name": "agent/" + peerAgent}) || publish.Body["kind"] != "notice" ||
		!reflect.DeepEqual(publish.Body["ref"], map[string]any{"record_id": sourceRecord, "version": float64(1)}) ||
		!reflect.DeepEqual(publish.Body["notice_policy"], map[string]any{"version": float64(1), "urgency": "urgent", "defer_seconds": float64(0)}) {
		t.Fatalf("publication: %v", publish.Body)
	}
	if result["from"] != "agent/"+selfAgent || result["recipient"].(map[string]any)["display_name"] != "agent-9" || result["request_id"] != request {
		t.Fatalf("result: %v", result)
	}
	// A retry repeats both mutations under the same keys.
	f.ok(t, "cairn_send", args)
	retry := f.api.taken()
	if retry[2].Body["request_id"] != sourceRequest || retry[3].Body["request_id"] != request {
		t.Fatal("retry changed a request ID")
	}
	// An existing source is published without a write; local text stays local.
	f.ok(t, "cairn_send", map[string]any{"request_id": uuid.NewString(), "to": peerAgent, "kind": "response", "causation_id": requestEvent, "source": map[string]any{"record_id": "cairn:" + resultRecord, "version": 2}})
	if got := f.api.taken(); !reflect.DeepEqual(operations(got), []string{"agent-directory", "event-publish"}) || got[1].Body["causation_id"] != requestEvent || !reflect.DeepEqual(got[1].Body["ref"], map[string]any{"record_id": resultRecord, "version": float64(2)}) {
		t.Fatalf("source publication: %+v", got)
	}
	f.ok(t, "cairn_send", map[string]any{"request_id": uuid.NewString(), "to": "topic/builds", "body": "Local.", "shareable": false})
	if got := f.api.taken(); got[1].Body["draft"].(map[string]any)["sensitivity"] != "local" || !reflect.DeepEqual(got[2].Body["destination"], map[string]any{"type": "topic", "name": "builds"}) {
		t.Fatalf("local topic send: %+v", got)
	}
}

func TestSendRefusesBeforeWriting(t *testing.T) {
	f := newMessagingFixture(t, Config{NativeSessionID: "native-self"})
	f.agents = append(f.agents, fixtureAgent(12, uuid.NewString(), uuid.NewString(), "native-stopped", false, true))
	base := func(change map[string]any) map[string]any {
		args := map[string]any{"request_id": uuid.NewString(), "to": "agent-9", "body": "Synthetic."}
		for key, value := range change {
			if value == nil {
				delete(args, key)
			} else {
				args[key] = value
			}
		}
		return args
	}
	for _, c := range []struct {
		change map[string]any
		want   string
	}{
		{map[string]any{"request_id": "not-a-uuid"}, "request_id must be a UUID"},
		{map[string]any{"body": nil}, "exactly one of body and source"},
		{map[string]any{"source": map[string]any{"record_id": sourceRecord, "version": 1}}, "exactly one of body and source"},
		{map[string]any{"body": " \n"}, "nonblank UTF-8"},
		{map[string]any{"kind": "two words"}, "kind must be"},
		{map[string]any{"kind": "request", "urgency": "urgent"}, "notices only"},
		{map[string]any{"urgency": "soon"}, "routine or urgent"},
		{map[string]any{"to": "agent-12"}, "RECIPIENT_STOPPED"},
		{map[string]any{"to": "agent-8"}, "NOT_FOUND"},
		{map[string]any{"to": "agent-0"}, "to must be"},
		{map[string]any{"to": "everyone"}, "to must be"},
		{map[string]any{"to": "topic/two words"}, "to must be"},
		{map[string]any{"agent_id": selfAgent}, "INVALID_REQUEST"},
	} {
		f.refused(t, "cairn_send", base(c.change), c.want)
		for _, call := range f.api.taken() {
			if call.Operation != "agent-directory" {
				t.Fatalf("%v reached %s", c.change, call.Operation)
			}
		}
	}
	// A refused publication reports the refusal; the retry reuses the source key.
	f.failPublish["notice"] = true
	f.refused(t, "cairn_send", base(nil), "ADMISSION_REFUSED: synthetic refusal")
}

func TestSessionIdentityResolution(t *testing.T) {
	peek := map[string]any{"peek": true}
	t.Run("explicit UUIDs skip the directory", func(t *testing.T) {
		f := newMessagingFixture(t, Config{})
		f.ok(t, "cairn_inbox", map[string]any{"peek": true, "agent_id": peerAgent, "execution_id": peerExecution})
		calls := f.api.taken()
		if len(calls) != 1 || calls[0].Operation != "session-inbox-pending" || calls[0].AgentID != "" || !reflect.DeepEqual(calls[0].Body, map[string]any{"agent_id": peerAgent, "execution_id": peerExecution}) {
			t.Fatalf("%+v", calls)
		}
	})
	t.Run("no native session", func(t *testing.T) {
		f := newMessagingFixture(t, Config{})
		f.refused(t, "cairn_inbox", peek, "IDENTITY_UNKNOWN")
		if calls := f.api.taken(); len(calls) != 0 {
			t.Fatalf("%+v", calls)
		}
	})
	t.Run("offline, stopped and unknown sessions do not match", func(t *testing.T) {
		f := newMessagingFixture(t, Config{NativeSessionID: "native-gone"})
		f.agents = append(f.agents, fixtureAgent(12, uuid.NewString(), uuid.NewString(), "native-gone", false, false), fixtureAgent(13, uuid.NewString(), uuid.NewString(), "native-gone", true, true))
		f.refused(t, "cairn_inbox", peek, "IDENTITY_UNKNOWN")
	})
	t.Run("ambiguous", func(t *testing.T) {
		f := newMessagingFixture(t, Config{NativeSessionID: "native-self"})
		f.agents = append(f.agents, fixtureAgent(12, uuid.NewString(), uuid.NewString(), "native-self", true, false))
		f.refused(t, "cairn_inbox", peek, "IDENTITY_AMBIGUOUS")
	})
	t.Run("pages past the first hundred online sessions", func(t *testing.T) {
		f := newMessagingFixture(t, Config{NativeSessionID: "native-late"})
		for ordinal := int64(100); ordinal < 250; ordinal++ {
			f.agents = append(f.agents, fixtureAgent(ordinal, uuid.NewString(), uuid.NewString(), "native-other", true, false))
		}
		late := uuid.NewString()
		f.agents = append(f.agents, fixtureAgent(250, late, uuid.NewString(), "native-late", true, false))
		f.ok(t, "cairn_inbox", peek)
		calls := f.api.taken()
		if got := operations(calls); !reflect.DeepEqual(got, []string{"agent-directory", "agent-directory", "session-inbox-pending"}) || calls[1].Body["after"] != float64(197) || calls[2].Body["agent_id"] != late {
			t.Fatalf("%v %+v", got, calls)
		}
	})
	t.Run("codex thread metadata", func(t *testing.T) {
		f := newMessagingFixture(t, Config{CodexThread: true})
		if _, refusal := f.call(t, mcp.Meta{"threadId": "native-peer"}, "cairn_inbox", peek); refusal != "" {
			t.Fatal(refusal)
		}
		if calls := f.api.taken(); calls[1].Body["agent_id"] != peerAgent || calls[1].Body["execution_id"] != peerExecution {
			t.Fatalf("%+v", calls)
		}
		if _, refusal := f.call(t, nil, "cairn_inbox", peek); !strings.Contains(refusal, "IDENTITY_UNKNOWN") {
			t.Fatal(refusal)
		}
	})
}

func TestAgentsLookup(t *testing.T) {
	f := newMessagingFixture(t, Config{NativeSessionID: "native-self"})
	f.agents = append(f.agents, fixtureAgent(12, uuid.NewString(), uuid.NewString(), "native-stopped", false, true))
	self := f.ok(t, "cairn_agents", map[string]any{"agent": "self"})["agents"].([]any)[0].(map[string]any)
	if self["display_name"] != "agent-7" || self["execution_id"] != selfExecution || self["inbox"] != "agent/"+selfAgent {
		t.Fatalf("self: %v", self)
	}
	stopped := f.ok(t, "cairn_agents", map[string]any{"agent": "agent-12"})["agents"].([]any)[0].(map[string]any)
	if stopped["stopped"] != true || stopped["online"] != false || stopped["execution_id"] != nil {
		t.Fatalf("stopped: %v", stopped)
	}
	byID := f.ok(t, "cairn_agents", map[string]any{"agent": peerAgent})["agents"].([]any)
	if len(byID) != 1 || byID[0].(map[string]any)["display_name"] != "agent-9" || byID[0].(map[string]any)["execution_id"] != nil {
		t.Fatalf("by UUID: %v", byID)
	}
	f.api.taken()
	listed := f.ok(t, "cairn_agents", map[string]any{"project": "cairn", "limit": 1})
	if calls := f.api.taken(); len(calls) != 1 || calls[0].Body["project"] != "cairn" || calls[0].Body["limit"] != float64(1) {
		t.Fatalf("%+v", calls)
	}
	if len(listed["agents"].([]any)) != 1 || listed["more"] != true || listed["next_after"] != float64(7) {
		t.Fatalf("listing: %v", listed)
	}
	f.ok(t, "cairn_agents", map[string]any{})
	if calls := f.api.taken(); calls[0].Body["limit"] != float64(25) || calls[0].Body["include_offline"] != nil {
		t.Fatalf("default listing: %+v", calls)
	}
	f.refused(t, "cairn_agents", map[string]any{"agent": "agent-9", "project": "cairn"}, "cannot combine")
	f.refused(t, "cairn_agents", map[string]any{"agent": "nobody"}, "agent must be")
	f.refused(t, "cairn_agents", map[string]any{"agent": "agent-8"}, "NOT_FOUND")
}

func TestInboxClaimReturnsSource(t *testing.T) {
	f := newMessagingFixture(t, Config{NativeSessionID: "native-self"})
	if pending := f.ok(t, "cairn_inbox", map[string]any{"peek": true}); pending["requests"] != float64(1) || pending["notices"] != float64(2) {
		t.Fatalf("peek: %v", pending)
	}
	f.refused(t, "cairn_inbox", map[string]any{"peek": true, "lease_seconds": 60}, "peek claims nothing")
	f.api.taken()
	claimed := f.ok(t, "cairn_inbox", map[string]any{"lease_seconds": 600})
	calls := f.api.taken()
	if got := operations(calls); !reflect.DeepEqual(got, []string{"agent-directory", "event-next", "history"}) {
		t.Fatal(got)
	}
	if calls[1].AgentID != selfAgent || calls[1].Execution != selfExecution || calls[1].Body["lease_seconds"] != float64(600) || calls[1].Body["agent"] != nil {
		t.Fatalf("claim: %+v", calls[1])
	}
	if calls[2].Body["record_id"] != sourceRecord || calls[2].Body["version"] != float64(3) {
		t.Fatalf("source read: %v", calls[2].Body)
	}
	delivery := claimed["delivery"].(map[string]any)
	if claimed["body"] != f.body || delivery["delivery_id"] != deliveryID || delivery["lease_id"] != leaseID || delivery["event_id"] != requestEvent || delivery["from"] != "agent/"+peerAgent || delivery["kind"] != "request" {
		t.Fatalf("claimed: %v", claimed)
	}
	// A source too large for the result keeps the claimed delivery and its lease.
	f.body = strings.Repeat("x", 40000)
	large := f.ok(t, "cairn_inbox", map[string]any{})
	if large["body"] != nil || !strings.Contains(large["body_omitted"].(string), "cairn_history") || large["delivery"].(map[string]any)["lease_id"] != leaseID {
		t.Fatalf("large source: %v", large)
	}
}

func TestInboxEmptyAndUnreadableSource(t *testing.T) {
	f := newMessagingFixture(t, Config{NativeSessionID: "native-self"})
	inner := f.api.handler
	empty, unreadable := true, false
	f.api.handler = func(call fakeCall) (any, *core.Error) {
		if call.Operation == "event-next" && empty {
			return core.NextEventResult{}, nil
		}
		if call.Operation == "history" && unreadable {
			return nil, &core.Error{Code: "NOT_FOUND", Message: "synthetic"}
		}
		return inner(call)
	}
	if got := f.ok(t, "cairn_inbox", map[string]any{}); len(got) != 1 || got["delivery"] != nil {
		t.Fatalf("empty inbox: %v", got)
	}
	empty, unreadable = false, true
	got := f.ok(t, "cairn_inbox", map[string]any{})
	if got["body"] != nil || !strings.Contains(got["body_omitted"].(string), "source_unavailable") || got["delivery"].(map[string]any)["lease_id"] != leaseID {
		t.Fatalf("unreadable source: %v", got)
	}
}

func TestCompleteRepliesToRequester(t *testing.T) {
	f := newMessagingFixture(t, Config{NativeSessionID: "native-self"})
	request := uuid.NewString()
	args := map[string]any{"request_id": request, "delivery_id": deliveryID, "lease_id": leaseID, "body": "Synthetic result."}
	done := f.ok(t, "cairn_complete", args)
	calls := f.api.taken()
	if got := operations(calls); !reflect.DeepEqual(got, []string{"agent-directory", "event-complete", "event-publish"}) {
		t.Fatal(got)
	}
	complete, reply := calls[1], calls[2]
	draft := complete.Body["draft"].(map[string]any)
	if complete.AgentID != selfAgent || complete.Body["request_id"] != request || complete.Body["disposition"] != "handled" || draft["body"] != "Synthetic result." || draft["sensitivity"] != "shareable" ||
		!reflect.DeepEqual(draft["scope"], map[string]any{"repo": "repo", "task_id": "*", "run_id": "*"}) {
		t.Fatalf("completion: %+v", complete)
	}
	replyRequest := reply.Body["request_id"].(string)
	if reply.AgentID != selfAgent || reply.Body["kind"] != "response" || reply.Body["causation_id"] != requestEvent || replyRequest == request || uuid.Validate(replyRequest) != nil ||
		!reflect.DeepEqual(reply.Body["destination"], map[string]any{"type": "agent", "name": "agent/" + peerAgent}) ||
		!reflect.DeepEqual(reply.Body["ref"], map[string]any{"record_id": resultRecord, "version": float64(1)}) {
		t.Fatalf("reply: %+v", reply)
	}
	if done["reply_event_id"] == nil || done["reply_pending"] != nil || done["delivery"].(map[string]any)["result"].(map[string]any)["record_id"] != resultRecord {
		t.Fatalf("result: %v", done)
	}
	// A failed reply is reported after the committed completion, and the retry
	// repeats the same response key.
	f.failPublish["response"] = true
	pending := f.ok(t, "cairn_complete", args)
	if pending["reply_pending"] != true || pending["reply_error"] != "ADMISSION_REFUSED: synthetic refusal" || pending["reply_event_id"] != nil {
		t.Fatalf("pending reply: %v", pending)
	}
	if retry := f.api.taken(); retry[2].Body["request_id"] != replyRequest {
		t.Fatal("retry changed the response request ID")
	}
	f.failPublish["response"] = false
	f.ok(t, "cairn_complete", map[string]any{"request_id": uuid.NewString(), "delivery_id": deliveryID, "lease_id": leaseID, "body": "Quiet.", "reply": false, "shareable": false})
	if got := f.api.taken(); !reflect.DeepEqual(operations(got), []string{"agent-directory", "event-complete"}) || got[1].Body["draft"].(map[string]any)["sensitivity"] != "local" {
		t.Fatalf("no reply: %+v", got)
	}
	f.refused(t, "cairn_complete", map[string]any{"request_id": uuid.NewString(), "delivery_id": deliveryID, "lease_id": leaseID, "body": " "}, "nonblank UTF-8")
	f.refused(t, "cairn_complete", map[string]any{"request_id": "x", "delivery_id": deliveryID, "lease_id": leaseID, "body": "Result."}, "request_id must be a UUID")
}

func TestAckRenewAndStatus(t *testing.T) {
	f := newMessagingFixture(t, Config{NativeSessionID: "native-self"})
	request := uuid.NewString()
	acked := f.ok(t, "cairn_ack", map[string]any{"request_id": request, "delivery_id": deliveryID, "lease_id": leaseID})
	calls := f.api.taken()
	// An acknowledgment never writes a result and never replies.
	if got := operations(calls); !reflect.DeepEqual(got, []string{"agent-directory", "event-complete"}) || calls[1].Body["draft"] != nil || calls[1].Body["disposition"] != "handled" || calls[1].Body["request_id"] != request || calls[1].AgentID != selfAgent {
		t.Fatalf("ack: %+v", calls)
	}
	if acked["state"] != "completed" || acked["delivery_id"] != deliveryID {
		t.Fatalf("ack result: %v", acked)
	}
	f.ok(t, "cairn_ack", map[string]any{"request_id": uuid.NewString(), "delivery_id": deliveryID, "lease_id": leaseID, "disposition": "failed", "code": "source_unavailable"})
	if calls = f.api.taken(); calls[1].Body["disposition"] != "failed" || calls[1].Body["code"] != "source_unavailable" {
		t.Fatalf("failed ack: %+v", calls[1])
	}
	f.ok(t, "cairn_renew", map[string]any{"delivery_id": deliveryID, "lease_id": leaseID, "lease_seconds": 900})
	if calls = f.api.taken(); calls[1].Operation != "event-renew" || calls[1].AgentID != selfAgent || !reflect.DeepEqual(calls[1].Body, map[string]any{"delivery_id": deliveryID, "lease_id": leaseID, "lease_seconds": float64(900)}) {
		t.Fatalf("renew: %+v", calls[1])
	}
	status := f.ok(t, "cairn_event_status", map[string]any{"event_id": "cairn:" + requestEvent, "limit": 5})
	if calls = f.api.taken(); calls[1].Operation != "event-inspect" || calls[1].AgentID != selfAgent || !reflect.DeepEqual(calls[1].Body, map[string]any{"event_id": requestEvent, "limit": float64(5)}) {
		t.Fatalf("status: %+v", calls[1])
	}
	deliveries := status["deliveries"].([]any)
	if status["event_id"] != requestEvent || len(deliveries) != 1 || deliveries[0].(map[string]any)["state"] != "completed" || deliveries[0].(map[string]any)["consumer"] != "agent/"+peerAgent {
		t.Fatalf("status result: %v", status)
	}
}

// TestMessagingAgainstStore sends a request between two registered sessions
// through the real API and store.
func TestMessagingAgainstStore(t *testing.T) {
	dsn := testDatabase(t)
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	op, err := core.Open(ctx, dsn, core.Channel{Principal: "operator:mcp-test", Operator: true})
	if err != nil {
		t.Fatal(err)
	}
	defer op.Close()
	if err = op.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err = op.Bootstrap(ctx, core.BootstrapRequest{RequestID: uuid.NewString(), Reason: "Install isolated MCP messaging test operator"}); err != nil {
		t.Fatal(err)
	}
	repo := uuid.NewString()
	digest := sha256.Sum256([]byte("synthetic-mcp-token"))
	api, err := localapi.New(ctx, dsn, []localapi.Identity{{TokenSHA256: hex.EncodeToString(digest[:]), Principal: "agent:mcp-test", Repo: repo, Role: "agent", Destination: "hosted"}})
	if err != nil {
		t.Fatal(err)
	}
	defer api.Close()
	client := serveAPI(t, api)
	register := func(native string) core.AgentInstance {
		var instance core.AgentInstance
		if err := client.Call(ctx, "agent-register", core.RegisterAgentRequest{RequestID: uuid.NewString(), Repo: repo, Binding: "mcp-test", NativeSessionID: native, Metadata: core.AgentMetadata{Harness: "claude", Project: "cairn", Workspace: "/work/cairn", State: "idle", DeliveryMode: "existing-session"}}, &instance); err != nil {
			t.Fatal(err)
		}
		return instance
	}
	sender, receiver := register("native-sender"), register("native-receiver")
	session := func(native string) *messagingFixture {
		server, err := NewServer(client, Config{Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, AvailableTokens: 32000, Messaging: true, NativeSessionID: native})
		if err != nil {
			t.Fatal(err)
		}
		return &messagingFixture{session: connect(t, ctx, server)}
	}
	a, b := session("native-sender"), session("native-receiver")

	if self := a.ok(t, "cairn_agents", map[string]any{"agent": "self"})["agents"].([]any)[0].(map[string]any); self["agent_id"] != sender.AgentID || self["execution_id"] != sender.ExecutionID {
		t.Fatalf("self: %v", self)
	}
	sendArgs := map[string]any{"request_id": uuid.NewString(), "to": receiver.DisplayName, "kind": "request", "body": "Synthetic request: report the fixture state."}
	sent := a.ok(t, "cairn_send", sendArgs)
	if sent["from"] != sender.Inbox || sent["to"].(map[string]any)["name"] != receiver.Inbox {
		t.Fatalf("sent: %v", sent)
	}
	if again := a.ok(t, "cairn_send", sendArgs); again["event_id"] != sent["event_id"] || !reflect.DeepEqual(again["source"], sent["source"]) {
		t.Fatalf("retry published again: %v / %v", again, sent)
	}
	if pending := b.ok(t, "cairn_inbox", map[string]any{"peek": true}); pending["requests"] != float64(1) {
		t.Fatalf("pending: %v", pending)
	}
	if other := a.ok(t, "cairn_inbox", map[string]any{}); other["delivery"] != nil {
		t.Fatalf("sender claimed the receiver's request: %v", other)
	}
	claimed := b.ok(t, "cairn_inbox", map[string]any{})
	delivery := claimed["delivery"].(map[string]any)
	if claimed["body"] != sendArgs["body"] || delivery["event_id"] != sent["event_id"] || delivery["from"] != sender.Inbox || delivery["kind"] != "request" {
		t.Fatalf("claimed: %v", claimed)
	}
	renewed := b.ok(t, "cairn_renew", map[string]any{"delivery_id": delivery["delivery_id"], "lease_id": delivery["lease_id"], "lease_seconds": 600})
	if renewed["lease_id"] != delivery["lease_id"] {
		t.Fatalf("renewed: %v", renewed)
	}
	// Another session cannot settle this delivery.
	if _, refusal := a.call(t, nil, "cairn_ack", map[string]any{"request_id": uuid.NewString(), "delivery_id": delivery["delivery_id"], "lease_id": delivery["lease_id"]}); refusal == "" {
		t.Fatal("another session settled the delivery")
	} else {
		t.Logf("foreign ack refused: %s", refusal)
	}
	completeArgs := map[string]any{"request_id": uuid.NewString(), "delivery_id": delivery["delivery_id"], "lease_id": delivery["lease_id"], "body": "Synthetic result: fixture state is nominal."}
	done := b.ok(t, "cairn_complete", completeArgs)
	if done["reply_event_id"] == nil || done["reply_pending"] != nil {
		t.Fatalf("completion: %v", done)
	}
	if again := b.ok(t, "cairn_complete", completeArgs); again["reply_event_id"] != done["reply_event_id"] {
		t.Fatalf("retry replied again: %v / %v", again, done)
	}
	status := a.ok(t, "cairn_event_status", map[string]any{"event_id": sent["event_id"]})
	if deliveries := status["deliveries"].([]any); len(deliveries) != 1 || deliveries[0].(map[string]any)["result"] == nil {
		t.Fatalf("status: %v", status)
	}
	response := a.ok(t, "cairn_inbox", map[string]any{})
	answer := response["delivery"].(map[string]any)
	if response["body"] != completeArgs["body"] || answer["kind"] != "response" || answer["causation_id"] != sent["event_id"] || answer["from"] != receiver.Inbox || answer["event_id"] != done["reply_event_id"] {
		t.Fatalf("response: %v", response)
	}
	if acked := a.ok(t, "cairn_ack", map[string]any{"request_id": uuid.NewString(), "delivery_id": answer["delivery_id"], "lease_id": answer["lease_id"]}); acked["state"] == "leased" {
		t.Fatalf("ack left the delivery leased: %v", acked)
	}
	for _, f := range []*messagingFixture{a, b} {
		if rest := f.ok(t, "cairn_inbox", map[string]any{}); rest["delivery"] != nil {
			t.Fatalf("inbox not empty: %v", rest)
		}
	}
}
