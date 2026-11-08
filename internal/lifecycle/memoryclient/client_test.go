package memoryclient_test

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/buildinfo"
	"github.com/halbritt/cairn/internal/lifecycle/memoryclient"
	"github.com/halbritt/cairn/localapi"
)

const receiptID = "11111111-1111-4111-8111-111111111111"
const recordID = "22222222-2222-4222-8222-222222222222"
const handleID = "33333333-3333-4333-8333-333333333333"
const requestID = "44444444-4444-4444-8444-444444444444"

var scope = core.Scope{Repo: "fixture-repository", TaskID: "fixture-task", RunID: "fixture-run"}

type requestCapture struct {
	operation string
	header    http.Header
	body      []byte
}

type recorder struct {
	mu    sync.Mutex
	calls []requestCapture
}

func (r *recorder) snapshot() []requestCapture {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]requestCapture(nil), r.calls...)
}

// The only socket and token used by these fixtures belong to this temporary API.
func fixture(t *testing.T, respond func(http.ResponseWriter, *http.Request, requestCapture)) (*localapi.Client, *recorder) {
	t.Helper()
	dir, err := os.MkdirTemp("", "memoryclient-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.RemoveAll(dir); err != nil {
			t.Error(err)
		}
	})
	token := filepath.Join(dir, "token")
	if err := os.WriteFile(token, []byte("synthetic-memory-token\n"), 0600); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("unix", filepath.Join(dir, "api.sock"))
	if err != nil {
		t.Fatal(err)
	}
	seen := new(recorder)
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
			return
		}
		call := requestCapture{strings.TrimPrefix(r.URL.Path, "/v1/"), r.Header.Clone(), body}
		seen.mu.Lock()
		seen.calls = append(seen.calls, call)
		seen.mu.Unlock()
		respond(w, r, call)
	})}
	served := make(chan error, 1)
	go func() { served <- server.Serve(listener) }()
	t.Cleanup(func() {
		if err := server.Close(); err != nil {
			t.Error(err)
		}
		if err := <-served; !errors.Is(err, http.ErrServerClosed) {
			t.Errorf("fixture serve: %v", err)
		}
	})
	api, err := localapi.NewClient(filepath.Join(dir, "api.sock"), token)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(api.Close)
	return api, seen
}

func success(t *testing.T, w http.ResponseWriter, reply any) {
	t.Helper()
	if err := json.NewEncoder(w).Encode(struct {
		Schema string `json:"schema"`
		OK     bool   `json:"ok"`
		Status string `json:"status"`
		Data   any    `json:"data"`
	}{"cairn.response/1", true, "OK", reply}); err != nil {
		t.Error(err)
	}
}

func probe(t *testing.T, w http.ResponseWriter, call requestCapture) bool {
	t.Helper()
	if call.operation != "version" {
		return false
	}
	success(t, w, map[string]any{"preview_capabilities": localapi.PreviewCapabilities{
		Schema: localapi.PreviewCapabilitiesSchema, EntitiesOmitted: true}})
	return true
}

func indexReply(entries ...core.IndexEntry) core.IndexResult {
	semantic := core.SemanticPackage{Schema: "cairn.semantic/17", Mode: "index", Status: "READY", Scope: scope,
		Purpose: "context", Destination: core.Destination{Name: "hosted"}, Query: "sha256:fixture-query",
		AvailableTokens: 8000, Selected: []core.Selection{}, Omitted: map[string]int{}, Index: entries}
	reply := core.IndexResult{Package: core.Package{ReceiptID: receiptID, Seal: "blake3:" + strings.Repeat("a", 64), Semantic: semantic},
		CreditsRemaining: 4, BytesRemaining: 24000, ExpiresAt: time.Date(2030, 1, 1, 0, 0, 0, 0, time.UTC)}
	for i, entry := range entries {
		reply.Handles = append(reply.Handles, core.IndexHandle{RecordID: entry.RecordID, Version: entry.Version, Handle: fmt.Sprintf("fixture-handle-%d", i)})
	}
	return reply
}

func entry(id, summary string) core.IndexEntry {
	return core.IndexEntry{RecordID: id, Version: 1, Class: "A", Kind: "note", Summary: summary, BodySHA256: strings.Repeat("b", 64)}
}

func client(t *testing.T, api *localapi.Client, options memoryclient.Options) *memoryclient.Client {
	t.Helper()
	c, err := memoryclient.New(api, options)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func decode(t *testing.T, raw []byte) any {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var decoded any
	if err := decoder.Decode(&decoded); err != nil {
		t.Fatal(err)
	}
	return decoded
}

func equalJSON(t *testing.T, got []byte, want any) {
	t.Helper()
	encoded, err := json.Marshal(want)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(decode(t, got), decode(t, encoded)) {
		t.Fatalf("JSON got %s; want %s", got, encoded)
	}
}

func requestBody(t *testing.T, call requestCapture, want any) {
	t.Helper()
	encoded, err := json.Marshal(want)
	if err != nil {
		t.Fatal(err)
	}
	members := decode(t, encoded).(map[string]any)
	if localapi.Protocol.Min > 1 {
		members[localapi.BodyProtocolField] = localapi.Protocol.Min
	}
	equalJSON(t, call.body, members)
}

func assertHeader(t *testing.T, call requestCapture, diagnostics localapi.ClientDiagnostics, session *core.AgentSessionRef) {
	t.Helper()
	if call.header.Get("Authorization") != "Bearer synthetic-memory-token" ||
		call.header.Get("Content-Type") != "application/json" ||
		call.header.Get(localapi.ProtocolHeader) != strconv.Itoa(localapi.Protocol.Current) {
		t.Fatal("fixture authentication or protocol headers changed")
	}
	wantOpening := ""
	if call.operation == "expand" {
		wantOpening = "1"
	}
	if call.header.Get(localapi.SourceOpeningHeader) != wantOpening {
		t.Fatal("source-opening declaration does not match the raw reader")
	}
	diagnostics.TransportBuild = buildinfo.Read()
	encoded := call.header.Get(localapi.ClientDiagnosticsHeader)
	if encoded != localapi.EncodeClientDiagnostics(diagnostics) {
		t.Fatal("diagnostic declaration changed")
	}
	decoded, err := base64.RawURLEncoding.Strict().DecodeString(encoded)
	if err != nil {
		t.Fatal(err)
	}
	var declaration localapi.ClientDiagnostics
	if err := json.Unmarshal(decoded, &declaration); err != nil || !reflect.DeepEqual(declaration.TransportBuild, buildinfo.Read()) {
		t.Fatal("diagnostics lost the executing transport build")
	}
	if session == nil {
		if call.header.Get("Cairn-Agent-ID") != "" || call.header.Get("Cairn-Execution-ID") != "" {
			t.Fatal("payload scope invented an authenticated session")
		}
	} else if call.header.Get("Cairn-Agent-ID") != session.AgentID || call.header.Get("Cairn-Execution-ID") != session.ExecutionID {
		t.Fatal("borrowed trusted session view was lost")
	}
}

func TestMemoryClientSearchContract(t *testing.T) {
	if _, err := memoryclient.New(nil, memoryclient.Options{}); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("nil SDK: %v", err)
	}
	t.Run("defaults-and-fresh-identity", func(t *testing.T) {
		api, seen := fixture(t, func(w http.ResponseWriter, _ *http.Request, call requestCapture) {
			if !probe(t, w, call) {
				success(t, w, indexReply())
			}
		})
		c := client(t, api, memoryclient.Options{Scope: scope})
		empty := ""
		var ids []string
		for _, selected := range []*string{nil, &empty, &requestIDValue} {
			view, err := c.Search(context.Background(), "chosen query", memoryclient.SearchOptions{RequestID: selected})
			if err != nil {
				t.Fatal(err)
			}
			if id, err := uuid.Parse(view.RequestID); err != nil || id == uuid.Nil || id.String() != view.RequestID {
				t.Fatal("request identity is not canonical")
			}
			ids = append(ids, view.RequestID)
		}
		if ids[0] == ids[1] || ids[2] != requestID {
			t.Fatalf("request identities: %v", ids)
		}
		calls := seen.snapshot()
		if len(calls) != 6 {
			t.Fatalf("each Search must probe then index, got %d requests", len(calls))
		}
		for i := range 3 {
			if calls[2*i].operation != "version" || calls[2*i+1].operation != "index" {
				t.Fatal("cross-search capability cache or extra request")
			}
			requestBody(t, calls[2*i], struct{}{})
			requestBody(t, calls[2*i+1], core.CompileRequest{Scope: scope, Query: "chosen query", Purpose: "context",
				AvailableTokens: 8000, Context: &core.ContextPins{}, RequestID: ids[i], CompactPreviewEntities: true})
		}
		for _, call := range calls {
			assertHeader(t, call, localapi.ClientDiagnostics{}, nil)
		}
		if got := c.Accounting(); got.OperationCalls["search"] != 3 || got.OperationCalls["pull"] != 0 || !reflect.DeepEqual(got.SearchReceipts, []string{receiptID}) {
			t.Fatalf("search accounting: %+v", got)
		}
		var raw json.RawMessage
		if err := api.Call(context.Background(), "version", struct{}{}, &raw); err != nil {
			t.Fatalf("borrowed SDK is no longer usable: %v", err)
		}
		if call := seen.snapshot()[6]; call.header.Get(localapi.ClientDiagnosticsHeader) != "" {
			t.Fatal("New mutated the caller's SDK diagnostics")
		}
	})
	t.Run("options-and-semantic-view", func(t *testing.T) {
		reply := indexReply(entry(recordID, "candidate"))
		group := core.AdvisoryConflict{ID: handleID, Version: 2, Members: []core.RecordVersionRef{{RecordID: recordID, Version: 1}}}
		reply.Package.Semantic.Index[0].Conflicts = []core.AdvisoryConflict{group}
		reply.Package.Semantic.Index[0].SummarySpan = &core.ByteSpanRequest{Offset: 3, Length: 7}
		reply.Package.Semantic.Index[0].Entities = []core.EntityRef{{Kind: "file", Name: "a.go"}}
		reply.Package.Semantic.Selected = []core.Selection{{Mandatory: true, Reason: "required", Conflicts: []core.AdvisoryConflict{group},
			Record: core.Record{RecordID: handleID, Version: 2, Class: "C", Draft: core.Draft{Body: "required fixture"}}}}
		reply.Package.Semantic.Discovery = &core.DiscoveryRanking{State: "unavailable", Algorithm: "fixture/1"}
		reply.Package.Semantic.MemoryBudget = &core.MemoryBudget{Schema: "cairn.memory-budget/4", Bytes: 24000}
		api, seen := fixture(t, func(w http.ResponseWriter, _ *http.Request, call requestCapture) {
			if !probe(t, w, call) {
				success(t, w, reply)
			}
		})
		session := core.AgentSessionRef{AgentID: recordID, ExecutionID: handleID}
		pins := core.ContextPins{Revision: strings.Repeat("a", 40), WorkspaceSHA256: strings.Repeat("b", 64),
			TaskClass: "coding", TaskPhase: "review", BindingID: "binding", CapabilityID: "capability"}
		originalPins := pins
		prefix := []string{"never-executed-fixture", "agent", "argument with space"}
		diagnostics := localapi.ClientDiagnostics{Surface: "cli", Harness: "unknown", Origin: &localapi.ClientOrigin{Component: "lifecycle-memory", Basis: "reported"},
			RetrievalCapabilities: localapi.CurrentRetrievalCapabilities(), TransportBuild: buildinfo.Info{Schema: "cairn.build/1", GoVersion: "go9.9"}}
		originalDiagnostics := diagnostics
		c := client(t, api.ForAgentSession(session), memoryclient.Options{Scope: scope, Context: &pins, CommandPrefix: prefix, Diagnostics: diagnostics})
		pins.TaskClass, prefix[0], diagnostics.Origin.Component = "changed", "changed", "changed"
		originalDiagnostics.Origin = &localapi.ClientOrigin{Component: "lifecycle-memory", Basis: "reported"}
		room, offset, timeout := 32000, 0, time.Second
		view, err := c.Search(context.Background(), "typed query", memoryclient.SearchOptions{Room: &room, Offset: &offset, Timeout: &timeout,
			RequestID: &requestIDValue, Entities: []string{"z.go", "a.go", "z.go"}, Kinds: []string{"decision", "note"}, Semantic: true})
		if err != nil {
			t.Fatal(err)
		}
		calls := seen.snapshot()
		if len(calls) != 2 {
			t.Fatalf("requests: %d", len(calls))
		}
		requestBody(t, calls[1], core.CompileRequest{Scope: scope, Query: "typed query", Purpose: "context", AvailableTokens: room,
			Context: &originalPins, RequestID: requestID, PageOffset: &offset, Semantic: true, CompactPreviewEntities: true,
			Entities: []core.EntityRef{{Kind: "file", Name: "z.go"}, {Kind: "file", Name: "a.go"}, {Kind: "file", Name: "z.go"}}, Kinds: []string{"decision", "note"}})
		for _, call := range calls {
			assertHeader(t, call, originalDiagnostics, &session)
		}
		if !reflect.DeepEqual(view.SemanticPackage, reply.Package.Semantic) || view.SourceSeal != reply.Package.Seal ||
			view.SourceSchema != reply.Package.Semantic.Schema || view.BytesRemaining != reply.BytesRemaining ||
			!reflect.DeepEqual(view.Index[0].IndexEntry, reply.Package.Semantic.Index[0]) {
			t.Fatal("search lost semantic selections, groups, spans, discovery or receipt metadata")
		}
		pull := view.Index[0].PullArguments
		if pull.ReceiptID != receiptID || pull.Handle != reply.Handles[0].Handle || pull.RequestID == "" || view.Index[0].PullCommand != "" {
			t.Fatal("paired pull or memory-budget presentation changed")
		}
	})
	t.Run("presentation-prefix-copy", func(t *testing.T) {
		api, _ := fixture(t, func(w http.ResponseWriter, _ *http.Request, call requestCapture) {
			if !probe(t, w, call) {
				success(t, w, indexReply(entry(recordID, "summary")))
			}
		})
		prefix := []string{"fixture executable", "agent"}
		c := client(t, api, memoryclient.Options{Scope: scope, CommandPrefix: prefix})
		prefix[0] = "mutated"
		view, err := c.Search(context.Background(), "q", memoryclient.SearchOptions{})
		if err != nil || !strings.HasPrefix(view.Index[0].PullCommand, "'fixture executable' agent pull --request-id ") {
			t.Fatalf("copied presentation prefix: %v", err)
		}
	})
	for _, status := range []string{"READY", "SCOPE_EMPTY", "DEGRADED_NO_EMBEDDINGS", "REFUSED", ""} {
		for _, destination := range []string{"hosted", "local"} {
			t.Run("status-"+status+"-"+destination, func(t *testing.T) {
				reply := indexReply()
				reply.Package.Semantic.Status, reply.Package.Semantic.Destination.Name = status, destination
				api, _ := fixture(t, func(w http.ResponseWriter, _ *http.Request, call requestCapture) {
					if !probe(t, w, call) {
						success(t, w, reply)
					}
				})
				c := client(t, api, memoryclient.Options{Scope: scope})
				_, err := c.Search(context.Background(), "q", memoryclient.SearchOptions{})
				if status == "REFUSED" || status == "" {
					if !errors.Is(err, memoryclient.ErrSearchStatus) {
						t.Fatalf("status must precede hosted: %v", err)
					}
				} else if destination != "hosted" {
					if !errors.Is(err, memoryclient.ErrNotHosted) {
						t.Fatalf("destination: %v", err)
					}
				} else if err != nil {
					t.Fatal(err)
				}
				if got := c.Accounting(); got.OperationCalls["search"] != 1 || !reflect.DeepEqual(got.SearchReceipts, []string{receiptID}) {
					t.Fatalf("status/hosted receipt accounting: %+v", got)
				}
			})
		}
	}
	t.Run("receipt-order-validation-and-cap", func(t *testing.T) {
		ids := []string{receiptID, receiptID, strings.ToUpper("abcdefab-cdef-4abc-8abc-abcdefabcdef"), uuid.Nil.String(), "not-a-uuid"}
		for i := range 9 {
			ids = append(ids, fmt.Sprintf("aaaaaaaa-aaaa-4aaa-8aaa-%012d", i))
		}
		position := 0
		api, _ := fixture(t, func(w http.ResponseWriter, _ *http.Request, call requestCapture) {
			if probe(t, w, call) {
				return
			}
			reply := indexReply()
			reply.Package.ReceiptID = ids[position]
			position++
			success(t, w, reply)
		})
		c := client(t, api, memoryclient.Options{Scope: scope})
		for range ids {
			if _, err := c.Search(context.Background(), "q", memoryclient.SearchOptions{}); err != nil {
				t.Fatal(err)
			}
		}
		want := append([]string{receiptID}, ids[5:12]...)
		got := c.Accounting()
		if !reflect.DeepEqual(got.SearchReceipts, want) {
			t.Fatalf("receipt order/cap: %v", got.SearchReceipts)
		}
		got.SearchReceipts[0] = "changed"
		if c.Accounting().SearchReceipts[0] != receiptID {
			t.Fatal("receipt accounting slice escaped")
		}
	})
	t.Run("local-preparation-and-presentation-refusals-count", func(t *testing.T) {
		api, seen := fixture(t, func(w http.ResponseWriter, _ *http.Request, call requestCapture) {
			if !probe(t, w, call) {
				success(t, w, indexReply())
			}
		})
		c := client(t, api, memoryclient.Options{Scope: scope})
		for _, names := range [][]string{{"../private"}, distinctEntities(17)} {
			if _, err := c.Search(context.Background(), "q", memoryclient.SearchOptions{Entities: names}); core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("entity validation: %v", err)
			}
		}
		if len(seen.snapshot()) != 0 || c.Accounting().OperationCalls["search"] != 2 {
			t.Fatal("entity refusal must precede I/O and count the search")
		}
		badScope := client(t, api, memoryclient.Options{Scope: core.Scope{Repo: "fixture"}})
		if _, err := badScope.Search(context.Background(), "q", memoryclient.SearchOptions{}); core.Code(err) != "INVALID_REQUEST" || badScope.Accounting().OperationCalls["search"] != 1 {
			t.Fatal("scope was not checked by Search")
		}
		for _, room := range []int{0, -1, 100} {
			if _, err := c.Search(context.Background(), "q", memoryclient.SearchOptions{Room: &room}); core.Code(err) != "BUDGET_REFUSED" {
				t.Fatalf("explicit room %d was defaulted: %v", room, err)
			}
		}
		calls := seen.snapshot()
		if len(calls) != 6 || len(c.Accounting().SearchReceipts) != 0 {
			t.Fatal("Present refusal retained a receipt or changed request count")
		}
		for i, room := range []int{0, -1, 100} {
			body := decode(t, calls[2*i+1].body).(map[string]any)
			if body["available_tokens"] != json.Number(strconv.Itoa(room)) {
				t.Fatal("explicit room changed on the wire")
			}
		}
	})
}

var requestIDValue = requestID

func distinctEntities(n int) []string {
	names := make([]string, n)
	for i := range names {
		names[i] = fmt.Sprintf("file%d.go", i)
	}
	return names
}

func awaitSignal(t *testing.T, signal <-chan struct{}) {
	t.Helper()
	select {
	case <-signal:
	case <-time.After(2 * time.Second):
		t.Fatal("fixture request did not reach the expected boundary")
	}
}

func awaitError(t *testing.T, finished <-chan error) error {
	t.Helper()
	select {
	case err := <-finished:
		return err
	case <-time.After(2 * time.Second):
		t.Fatal("memory operation did not honor its caller context")
		return nil
	}
}

func TestMemoryClientPullAccounting(t *testing.T) {
	request := core.ExpandRequest{RequestID: requestID, ReceiptID: receiptID, Handle: handleID,
		Span: &core.ByteSpanRequest{Offset: 7, Length: 11}, OpeningBytes: 37}
	t.Run("raw-request-and-monotone-replay", func(t *testing.T) {
		replies := []json.RawMessage{
			json.RawMessage(`{"credits_remaining":2,"opaque":{"large":900719925474099312345,"decimal":1.2300},"source_opening":{"body":"fixture"}}`),
			json.RawMessage(`{"credits_remaining":4}`), json.RawMessage(`{"credits_remaining":0}`)}
		position := 0
		api, seen := fixture(t, func(w http.ResponseWriter, _ *http.Request, _ requestCapture) {
			success(t, w, replies[position])
			position++
		})
		c := client(t, api, memoryclient.Options{})
		for i, remaining := range []int{2, 2, 0} {
			raw, err := c.Pull(context.Background(), request, time.Second)
			if err != nil || !bytes.Equal(bytes.TrimSpace(raw), replies[i]) || c.Accounting().ReceiptCredits[receiptID] != remaining {
				t.Fatalf("pull %d remaining=%d: %v", i, remaining, err)
			}
		}
		before := c.Accounting()
		if _, err := c.Pull(context.Background(), request, time.Second); core.Code(err) != "BUDGET_REFUSED" {
			t.Fatalf("exhaustion: %v", err)
		}
		if !reflect.DeepEqual(c.Accounting(), before) || len(seen.snapshot()) != 3 {
			t.Fatal("known-zero preflight performed or counted another attempt")
		}
		for _, call := range seen.snapshot() {
			if call.operation != "expand" {
				t.Fatal("pull used another operation")
			}
			requestBody(t, call, request)
			assertHeader(t, call, localapi.ClientDiagnostics{}, nil)
		}
		before.OperationCalls["pull"], before.PullCalls[receiptID], before.ReceiptCredits[receiptID] = 99, 99, 99
		if got := c.Accounting(); got.OperationCalls["pull"] != 3 || got.PullCalls[receiptID] != 3 || got.ReceiptCredits[receiptID] != 0 {
			t.Fatal("accounting maps escaped")
		}
	})
	for _, tc := range []struct {
		name, members string
		want          int
	}{
		{"missing", `"opaque":900719925474099312345`, 3}, {"zero", `"credits_remaining":0`, 0},
		{"negative-zero", `"credits_remaining":-0`, 0}, {"one", `"credits_remaining":1`, 1},
		{"three", `"credits_remaining":3`, 3}, {"four", `"credits_remaining":4`, 4},
		{"bool", `"credits_remaining":true`, 3}, {"float", `"credits_remaining":1.0`, 3},
		{"exponent", `"credits_remaining":1e0`, 3}, {"null", `"credits_remaining":null`, 3},
		{"string", `"credits_remaining":"1"`, 3}, {"negative", `"credits_remaining":-1`, 3},
		{"too-large", `"credits_remaining":5`, 3}, {"overflow", `"credits_remaining":900719925474099312345`, 3},
		{"last-duplicate", `"credits_remaining":0,"credits_remaining":2`, 2},
	} {
		t.Run(tc.name, func(t *testing.T) {
			api, seen := fixture(t, func(w http.ResponseWriter, _ *http.Request, _ requestCapture) {
				success(t, w, json.RawMessage("{"+tc.members+"}"))
			})
			c := client(t, api, memoryclient.Options{})
			if _, err := c.Pull(context.Background(), request, time.Second); err != nil || c.Accounting().ReceiptCredits[receiptID] != tc.want {
				t.Fatalf("credit literal: %v, %+v", err, c.Accounting())
			}
			if len(seen.snapshot()) != 1 || c.Accounting().PullCalls[receiptID] != 1 {
				t.Fatal("pull was replayed")
			}
		})
	}
	t.Run("empty-receipt", func(t *testing.T) {
		api, _ := fixture(t, func(w http.ResponseWriter, _ *http.Request, _ requestCapture) {
			success(t, w, map[string]int{"credits_remaining": 0})
		})
		c := client(t, api, memoryclient.Options{})
		empty := request
		empty.ReceiptID = ""
		for range 2 {
			if _, err := c.Pull(context.Background(), empty, time.Second); err != nil {
				t.Fatal(err)
			}
		}
		if got := c.Accounting(); got.OperationCalls["pull"] != 2 || len(got.PullCalls) != 0 || len(got.ReceiptCredits) != 0 {
			t.Fatalf("empty receipt: %+v", got)
		}
	})
}

func TestMemoryClientFailuresAndDeadline(t *testing.T) {
	request := core.ExpandRequest{RequestID: requestID, ReceiptID: receiptID, Handle: handleID}
	for _, tc := range []struct {
		name, envelope, code string
		notConfirmed         bool
	}{
		{"service-budget", `{"schema":"cairn.response/1","ok":false,"status":"BUDGET_REFUSED","message":"service refusal","refusal_id":"fixture-refusal"}`, "BUDGET_REFUSED", false},
		{"service-authority", `{"schema":"cairn.response/1","ok":false,"status":"AUTHORITY_DENIED","message":"service refusal"}`, "AUTHORITY_DENIED", false},
		{"absent-data", `{"schema":"cairn.response/1","ok":true}`, "", true},
		{"null", `{"schema":"cairn.response/1","ok":true,"data":null}`, "", true},
		{"array", `{"schema":"cairn.response/1","ok":true,"data":[]}`, "", true},
		{"scalar", `{"schema":"cairn.response/1","ok":true,"data":7}`, "", true},
		{"malformed", `{"schema":"cairn.response/1","ok":true,"data":{"private":"never echo this"}`, "", true},
		{"bad-schema", `{"schema":"private-schema","ok":true,"data":{}}`, "", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			api, seen := fixture(t, func(w http.ResponseWriter, _ *http.Request, _ requestCapture) {
				if _, err := io.WriteString(w, tc.envelope); err != nil {
					t.Error(err)
				}
			})
			c := client(t, api, memoryclient.Options{})
			_, err := c.Pull(context.Background(), request, time.Second)
			if err == nil || (tc.notConfirmed && !errors.Is(err, memoryclient.ErrNotConfirmed)) || (tc.code != "" && core.Code(err) != tc.code) {
				t.Fatalf("failure classification: %v", err)
			}
			if tc.name == "service-budget" {
				var service *core.Error
				if !errors.As(err, &service) || service.RefusalID != "fixture-refusal" || service.Message != "service refusal" {
					t.Fatal("typed service refusal was replaced")
				}
			}
			for _, secret := range []string{"never echo this", "private-schema", "synthetic-memory-token"} {
				if strings.Contains(err.Error(), secret) {
					t.Fatal("local failure reflected private response content")
				}
			}
			if got := c.Accounting(); got.OperationCalls["pull"] != 1 || got.PullCalls[receiptID] != 1 || len(got.ReceiptCredits) != 0 || len(seen.snapshot()) != 1 {
				t.Fatalf("unconfirmed reply changed credits or retried: %+v", got)
			}
		})
	}
	t.Run("search-unconfirmed-probe-and-index", func(t *testing.T) {
		for _, failAt := range []string{"version", "index"} {
			t.Run(failAt, func(t *testing.T) {
				api, seen := fixture(t, func(w http.ResponseWriter, _ *http.Request, call requestCapture) {
					if call.operation != failAt && probe(t, w, call) {
						return
					}
					if _, err := io.WriteString(w, `{"schema":"cairn.response/1","ok":true}`); err != nil {
						t.Error(err)
					}
				})
				c := client(t, api, memoryclient.Options{Scope: scope})
				if _, err := c.Search(context.Background(), "q", memoryclient.SearchOptions{}); !errors.Is(err, memoryclient.ErrNotConfirmed) {
					t.Fatalf("unconfirmed Search: %v", err)
				}
				wantRequests := 1
				if failAt == "index" {
					wantRequests = 2
				}
				if got := c.Accounting(); got.OperationCalls["search"] != 1 || len(got.SearchReceipts) != 0 || len(seen.snapshot()) != wantRequests {
					t.Fatal("failed search was retried or retained a receipt")
				}
			})
		}
	})
	t.Run("transport-causes-and-no-retry", func(t *testing.T) {
		api, seen := fixture(t, func(w http.ResponseWriter, _ *http.Request, _ requestCapture) {
			connection, _, err := w.(http.Hijacker).Hijack()
			if err != nil {
				t.Error(err)
				return
			}
			if err := connection.Close(); err != nil {
				t.Error(err)
			}
		})
		c := client(t, api, memoryclient.Options{})
		_, err := c.Pull(context.Background(), request, time.Second)
		var transport *url.Error
		if !errors.As(err, &transport) || errors.Is(err, memoryclient.ErrNotConfirmed) || len(seen.snapshot()) != 1 || len(c.Accounting().ReceiptCredits) != 0 {
			t.Fatalf("transport cause or replay: %v", err)
		}
	})
	t.Run("truncated-http-body-remains-a-transport-failure", func(t *testing.T) {
		api, seen := fixture(t, func(w http.ResponseWriter, _ *http.Request, _ requestCapture) {
			w.Header().Set("Content-Length", "200")
			if _, err := io.WriteString(w, `{"schema":"cairn.response/1"`); err != nil {
				t.Error(err)
			}
		})
		c := client(t, api, memoryclient.Options{})
		_, err := c.Pull(context.Background(), request, time.Second)
		if !errors.Is(err, io.ErrUnexpectedEOF) || errors.Is(err, memoryclient.ErrNotConfirmed) {
			t.Fatalf("HTTP body-read failure was classified as a malformed envelope: %v", err)
		}
		if got := c.Accounting(); got.OperationCalls["pull"] != 1 || got.PullCalls[receiptID] != 1 || len(got.ReceiptCredits) != 0 || len(seen.snapshot()) != 1 {
			t.Fatal("truncated reply changed credits or retried")
		}
	})
	t.Run("dialer-classification-and-redaction", func(t *testing.T) {
		// A private missing socket exercises the SDK dialer's typed refusal.
		dir := t.TempDir()
		token := filepath.Join(dir, "token")
		if err := os.WriteFile(token, []byte("synthetic-memory-token"), 0600); err != nil {
			t.Fatal(err)
		}
		missing := filepath.Join(dir, "private-missing.sock")
		disconnected, err := localapi.NewClient(missing, token)
		if err != nil {
			t.Fatal(err)
		}
		defer disconnected.Close()
		c := client(t, disconnected, memoryclient.Options{})
		_, err = c.Pull(context.Background(), request, time.Second)
		var dial *net.OpError
		if core.Code(err) != "API_CONNECTION_FAILED" || !errors.As(err, &dial) || strings.Contains(err.Error(), dir) {
			t.Fatalf("dialer error lost or path leaked: %v", err)
		}
	})
	t.Run("expired-and-canceled-before-io", func(t *testing.T) {
		api, seen := fixture(t, func(w http.ResponseWriter, _ *http.Request, _ requestCapture) { success(t, w, struct{}{}) })
		c := client(t, api, memoryclient.Options{Scope: scope})
		for _, timeout := range []time.Duration{0, -time.Second} {
			if _, err := c.Search(context.Background(), "q", memoryclient.SearchOptions{Timeout: &timeout}); !errors.Is(err, context.DeadlineExceeded) {
				t.Fatalf("explicit search timeout defaulted: %v", err)
			}
			if _, err := c.Pull(context.Background(), request, timeout); !errors.Is(err, context.DeadlineExceeded) {
				t.Fatalf("explicit pull timeout defaulted: %v", err)
			}
		}
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		if _, err := c.Search(ctx, "q", memoryclient.SearchOptions{}); !errors.Is(err, context.Canceled) {
			t.Fatalf("search cancellation: %v", err)
		}
		if _, err := c.Pull(ctx, request, time.Second); !errors.Is(err, context.Canceled) {
			t.Fatalf("pull cancellation: %v", err)
		}
		if got := c.Accounting(); len(seen.snapshot()) != 0 || got.OperationCalls["search"] != 3 || got.OperationCalls["pull"] != 3 || got.PullCalls[receiptID] != 3 || len(got.ReceiptCredits) != 0 {
			t.Fatalf("expired operations: %+v", got)
		}
	})
	t.Run("caller-deadline-spans-probe-and-index", func(t *testing.T) {
		probeEntered, releaseProbe, indexEntered := make(chan struct{}), make(chan struct{}), make(chan struct{})
		api, seen := fixture(t, func(w http.ResponseWriter, r *http.Request, call requestCapture) {
			if call.operation == "version" {
				close(probeEntered)
				select {
				case <-releaseProbe:
					probe(t, w, call)
				case <-r.Context().Done():
				}
				return
			}
			close(indexEntered)
			<-r.Context().Done()
		})
		c := client(t, api, memoryclient.Options{Scope: scope})
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		started := time.Now()
		finished := make(chan error, 1)
		go func() { _, err := c.Search(ctx, "q", memoryclient.SearchOptions{}); finished <- err }()
		awaitSignal(t, probeEntered)
		close(releaseProbe)
		awaitSignal(t, indexEntered)
		if err := awaitError(t, finished); !errors.Is(err, context.DeadlineExceeded) || time.Since(started) > 2*time.Second {
			t.Fatalf("outer deadline: %v", err)
		}
		if len(seen.snapshot()) != 2 || c.Accounting().OperationCalls["search"] != 1 {
			t.Fatal("deadline reset or request replay")
		}
	})
	t.Run("in-flight-cancellation", func(t *testing.T) {
		entered := make(chan struct{})
		api, seen := fixture(t, func(_ http.ResponseWriter, r *http.Request, _ requestCapture) {
			close(entered)
			<-r.Context().Done()
		})
		c := client(t, api, memoryclient.Options{})
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		finished := make(chan error, 1)
		go func() { _, err := c.Pull(ctx, request, time.Second); finished <- err }()
		awaitSignal(t, entered)
		cancel()
		if err := awaitError(t, finished); !errors.Is(err, context.Canceled) || len(seen.snapshot()) != 1 || len(c.Accounting().ReceiptCredits) != 0 {
			t.Fatalf("in-flight cancellation: %v", err)
		}
	})
	t.Run("default-five-second-cap", func(t *testing.T) {
		api, seen := fixture(t, func(_ http.ResponseWriter, r *http.Request, _ requestCapture) { <-r.Context().Done() })
		c := client(t, api, memoryclient.Options{Scope: scope})
		started := time.Now()
		_, err := c.Search(context.Background(), "q", memoryclient.SearchOptions{})
		elapsed := time.Since(started)
		if !errors.Is(err, context.DeadlineExceeded) || elapsed < 4500*time.Millisecond || elapsed > 10*time.Second || len(seen.snapshot()) != 1 {
			t.Fatalf("default cap after %v: %v", elapsed, err)
		}
	})
}

func TestMemoryClientCheckpoint(t *testing.T) {
	t.Run("ordered-consumer-and-exact-record", func(t *testing.T) {
		title := "title\"\\with quote"
		reply := indexReply(entry(recordID, "unrelated"), entry(handleID, title+"\npreview one"), entry(requestID, title+"\npreview two"))
		exactRecord := `{ "body" : "title\"\\with quote\nmatching body", "opaque":900719925474099312345, "ratio":1.2300 }`
		pulls := 0
		api, seen := fixture(t, func(w http.ResponseWriter, _ *http.Request, call requestCapture) {
			if probe(t, w, call) {
				return
			}
			if call.operation == "index" {
				success(t, w, reply)
				return
			}
			pulls++
			record := `{"body":"different title\nnot a checkpoint"}`
			if pulls == 2 {
				record = exactRecord
			}
			// Write directly: an Encoder would compact the RawMessage before the client sees it.
			if _, err := io.WriteString(w, `{"schema":"cairn.response/1","ok":true,"data":{"credits_remaining":`+strconv.Itoa(4-pulls)+`,"selection":{"record":`+record+`}}}`); err != nil {
				t.Error(err)
			}
		})
		c := client(t, api, memoryclient.Options{Scope: scope})
		record, err := c.Checkpoint(context.Background(), title)
		if err != nil || string(record) != exactRecord {
			t.Fatalf("exact checkpoint: %s, %v", record, err)
		}
		calls := seen.snapshot()
		if len(calls) != 4 || pulls != 2 {
			t.Fatal("checkpoint did not filter summaries and traverse in order")
		}
		body := decode(t, calls[1].body).(map[string]any)
		searchID := body["request_id"].(string)
		requestBody(t, calls[1], core.CompileRequest{Scope: scope, Query: `"` + title + `"`, Purpose: "context",
			AvailableTokens: 16000, Context: &core.ContextPins{}, RequestID: searchID, CompactPreviewEntities: true})
		for i, call := range calls[2:] {
			var pull core.ExpandRequest
			if err := json.Unmarshal(call.body, &pull); err != nil || pull.ReceiptID != receiptID || pull.Handle != reply.Handles[i+1].Handle || pull.RequestID == "" || pull.Span != nil || pull.OpeningBytes != 0 {
				t.Fatal("checkpoint changed paired pull arguments")
			}
			requestBody(t, call, pull)
			assertHeader(t, call, localapi.ClientDiagnostics{}, nil)
		}
		if got := c.Accounting(); got.OperationCalls["search"] != 1 || got.OperationCalls["pull"] != 2 || got.PullCalls[receiptID] != 2 || got.ReceiptCredits[receiptID] != 2 {
			t.Fatalf("checkpoint accounting: %+v", got)
		}
	})
	for _, tc := range []struct {
		name, expansion string
		malformed       bool
	}{
		{"no-match", `{"selection":{"record":{"body":"another title\nbody"}}}`, false},
		{"missing-selection", `{}`, true}, {"null-selection", `{"selection":null}`, true},
		{"missing-record", `{"selection":{}}`, true}, {"null-record", `{"selection":{"record":null}}`, true},
		{"array-record", `{"selection":{"record":[]}}`, true}, {"missing-body", `{"selection":{"record":{}}}`, true},
		{"null-body", `{"selection":{"record":{"body":null}}}`, true}, {"numeric-body", `{"selection":{"record":{"body":7}}}`, true},
		{"wrong-case", `{"Selection":{"record":{"body":"title\nbody"}}}`, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			api, seen := fixture(t, func(w http.ResponseWriter, _ *http.Request, call requestCapture) {
				if probe(t, w, call) {
					return
				}
				if call.operation == "index" {
					success(t, w, indexReply(entry(recordID, "title\nsummary")))
				} else {
					success(t, w, json.RawMessage(tc.expansion))
				}
			})
			c := client(t, api, memoryclient.Options{Scope: scope})
			record, err := c.Checkpoint(context.Background(), "title")
			if record != nil || (tc.malformed && !errors.Is(err, memoryclient.ErrCheckpointRecord)) || (!tc.malformed && err != nil) {
				t.Fatalf("checkpoint absence/record error: %s %v", record, err)
			}
			if got := c.Accounting(); got.ReceiptCredits[receiptID] != 3 || got.PullCalls[receiptID] != 1 || len(seen.snapshot()) != 3 {
				t.Fatalf("confirmed rejected record did not consume credits: %+v", got)
			}
		})
	}
	t.Run("no-candidate", func(t *testing.T) {
		api, seen := fixture(t, func(w http.ResponseWriter, _ *http.Request, call requestCapture) {
			if !probe(t, w, call) {
				success(t, w, indexReply(entry(recordID, "other\nsummary")))
			}
		})
		c := client(t, api, memoryclient.Options{Scope: scope})
		if record, err := c.Checkpoint(context.Background(), "title"); record != nil || err != nil || len(seen.snapshot()) != 2 || c.Accounting().OperationCalls["pull"] != 0 {
			t.Fatalf("no-candidate checkpoint: %s %v", record, err)
		}
	})
	t.Run("first-pull-error-propagates", func(t *testing.T) {
		api, seen := fixture(t, func(w http.ResponseWriter, _ *http.Request, call requestCapture) {
			if probe(t, w, call) {
				return
			}
			if call.operation == "index" {
				success(t, w, indexReply(entry(recordID, "title\none"), entry(handleID, "title\ntwo")))
			} else if _, err := io.WriteString(w, `{"schema":"cairn.response/1","ok":false,"status":"BUDGET_REFUSED","message":"fixture refusal"}`); err != nil {
				t.Error(err)
			}
		})
		c := client(t, api, memoryclient.Options{Scope: scope})
		if record, err := c.Checkpoint(context.Background(), "title"); record != nil || core.Code(err) != "BUDGET_REFUSED" || len(seen.snapshot()) != 3 || len(c.Accounting().ReceiptCredits) != 0 {
			t.Fatalf("checkpoint skipped a pull failure: %s %v", record, err)
		}
	})
}
