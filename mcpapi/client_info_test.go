package mcpapi

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/buildinfo"
	"github.com/halbritt/cairn/localapi"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func diagnosticClient(t *testing.T, handler http.HandlerFunc) (*localapi.Client, func()) {
	t.Helper()
	dir, err := os.MkdirTemp("", "cairn-diag-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	token := filepath.Join(dir, "private-token-path")
	if err = os.WriteFile(token, []byte("private-token-canary"), 0600); err != nil {
		t.Fatal(err)
	}
	socket := filepath.Join(dir, "private-socket-path")
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer private-token-canary" {
			t.Error("missing authentication")
		}
		handler(w, r)
	})}
	go server.Serve(listener)
	t.Cleanup(func() { server.Close() })
	client, err := localapi.NewClient(socket, token)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(client.Close)
	return client, func() { server.Close() }
}

func diagnosticSession(t *testing.T, client *localapi.Client, room int) *mcp.ClientSession {
	t.Helper()
	server, err := NewServer(client, Config{Scope: core.Scope{Repo: "/private-repository-canary", TaskID: "/private-session-canary", RunID: "private-run-canary"}, AvailableTokens: room})
	if err != nil {
		t.Fatal(err)
	}
	return connect(t, context.Background(), server)
}
func invokeDiagnostic(t *testing.T, session *mcp.ClientSession) clientInfoResult {
	t.Helper()
	result, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_client_info", Arguments: map[string]any{}})
	if err != nil || result.IsError {
		t.Fatalf("diagnostic failed: %v %+v", err, result)
	}
	encoded, err := json.Marshal(result)
	if err != nil || len(encoded) > 4096 {
		t.Fatalf("unbounded result: %v %d", err, len(encoded))
	}
	for _, private := range []string{"private-", "/tmp/", "Bearer", "token_file", "socket"} {
		if strings.Contains(string(encoded), private) {
			t.Fatalf("private data in result: %s", encoded)
		}
	}
	var info clientInfoResult
	if err = json.Unmarshal([]byte(result.Content[0].(*mcp.TextContent).Text), &info); err != nil {
		t.Fatal(err)
	}
	if info.Schema != "cairn.client-info/1" || info.Facade.SearchMemoryBudgetBytes != "supported" || !reflect.DeepEqual(info.Facade.Build, ptrBuild(buildinfo.Read())) {
		t.Fatalf("local identity lost: %+v", info)
	}
	if info.API.SearchMemoryBudgetBytes != "unknown" {
		t.Fatalf("invented API support: %+v", info)
	}
	return info
}
func ptrBuild(b buildinfo.Info) *buildinfo.Info { return &b }

func TestClientInfoLegacyAndForwarding(t *testing.T) {
	var versions, searches atomic.Int32
	client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			t.Error("non-POST diagnostic")
		}
		switch r.URL.Path {
		case "/v1/version":
			versions.Add(1)
			var args map[string]any
			if err := json.NewDecoder(r.Body).Decode(&args); err != nil || len(args) != 0 {
				t.Error("version request is not empty")
			}
			// Extra fields are not a recognized capability declaration or safe output.
			w.Write([]byte(`{"schema":"cairn.response/1","ok":true,"status":"OK","data":{"schema":"cairn.build/1","go_version":"go1.25.0","vcs_revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","vcs_modified":null,"socket":"/private-socket-path","capabilities":{"memory_budget_bytes":true}}}`))
		case "/v1/index":
			searches.Add(1)
			var req core.CompileRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				t.Error(err)
			}
			if req.AvailableTokens != 32000 || req.MemoryBudgetBytes == nil || *req.MemoryBudgetBytes != 4000 {
				t.Errorf("support declaration disagrees with forwarding: %+v", req)
			}
			json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": core.IndexResult{}})
		default:
			t.Errorf("unexpected operation: %s", r.URL.Path)
			w.WriteHeader(404)
		}
	})
	session := diagnosticSession(t, client, 32000)
	list, err := session.ListTools(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	foundInfo, foundBudget := false, false
	for _, tool := range list.Tools {
		if tool.Name == "cairn_client_info" {
			foundInfo = true
			if tool.Annotations == nil || !tool.Annotations.ReadOnlyHint {
				t.Fatal("diagnostic not read-only")
			}
		}
		if tool.Name == "cairn_search" {
			b, _ := json.Marshal(tool.InputSchema)
			var schema struct {
				Properties map[string]json.RawMessage `json:"properties"`
			}
			json.Unmarshal(b, &schema)
			_, foundBudget = schema.Properties["memory_budget_bytes"]
		}
	}
	if !foundInfo || !foundBudget {
		t.Fatal("declared support absent from registered tools")
	}
	info := invokeDiagnostic(t, session)
	if info.API.State != "available" || info.API.Build == nil || info.API.Build.Revision != strings.Repeat("a", 40) || info.API.Build.Modified != nil {
		t.Fatalf("legacy identity: %+v", info)
	}
	result, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_search", Arguments: map[string]any{"query": "synthetic", "available_tokens": 32000, "memory_budget_bytes": 4000}})
	if err != nil || result.IsError {
		t.Fatalf("search invocation: %v %+v", err, result)
	}
	invalid, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_search", Arguments: map[string]any{"query": "synthetic", "available_tokens": 32000, "memory_budget_bytes": 40001}})
	if err != nil || !invalid.IsError {
		t.Fatalf("declared field did not enforce bounds: %v %+v", err, invalid)
	}
	if versions.Load() != 1 || searches.Load() != 1 {
		t.Fatal("unexpected retries or probes")
	}
}

func TestClientInfoFailuresKeepLocalInformation(t *testing.T) {
	for _, tc := range []struct {
		name, body, code string
		stop, timeout    bool
	}{
		{name: "connection", stop: true, code: "API_CONNECTION_FAILED"},
		{name: "timeout", timeout: true, code: "API_TIMEOUT"},
		{name: "legacy-endpoint", body: `{"schema":"cairn.response/1","ok":false,"status":"NOT_FOUND","message":"private-token-canary /private-socket-path"}`, code: "VERSION_UNAVAILABLE"},
		{name: "denied", body: `{"schema":"cairn.response/1","ok":false,"status":"AUTHORITY_DENIED","message":"/private-repository-canary"}`, code: "API_ACCESS_DENIED"},
		{name: "malformed", body: `private-token-canary`, code: "API_PROBE_FAILED"},
		{name: "bad-build", body: `{"schema":"cairn.response/1","ok":true,"data":{"schema":"cairn.build/1","go_version":"/private-socket-path"}}`, code: "INVALID_BUILD_IDENTITY"},
		{name: "unrecognized-code", body: `{"schema":"cairn.response/1","ok":false,"status":"private-token-canary","message":"private-token-canary"}`, code: "API_PROBE_FAILED"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var calls atomic.Int32
			client, stop := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				if tc.timeout {
					<-r.Context().Done()
					return
				}
				w.Write([]byte(tc.body))
			})
			if tc.stop {
				stop()
			}
			info := invokeDiagnostic(t, diagnosticSession(t, client, 32000))
			if info.API.Diagnostic != tc.code || info.API.Build != nil {
				t.Fatalf("unexpected failure: %+v", info)
			}
			if calls.Load() > 1 {
				t.Fatal("probe retried")
			}
		})
	}
}

func TestClientInfoRespectsOutputCeiling(t *testing.T) {
	session := diagnosticSession(t, nil, 256)
	result, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_client_info", Arguments: map[string]any{}})
	if err != nil || !result.IsError || !strings.Contains(result.Content[0].(*mcp.TextContent).Text, "BUDGET_REFUSED") {
		t.Fatalf("expected bounded refusal: %v %+v", err, result)
	}
}

func TestClientInfoUnstampedAPIWithoutCapabilities(t *testing.T) {
	client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": buildinfo.Info{Schema: "cairn.build/1", GoVersion: "go1.25.0"}})
	})
	info := invokeDiagnostic(t, diagnosticSession(t, client, 32000))
	if info.API.State != "available" || info.API.Build == nil || info.API.Build.Revision != "" || info.API.Build.Modified != nil {
		t.Fatalf("unknown provenance invented: %+v", info)
	}
}

func TestClientInfoDevelopmentAndSVNBuilds(t *testing.T) {
	for _, build := range []buildinfo.Info{
		{Schema: "cairn.build/1", GoVersion: "go1.26-devel_abcdef0 Wed Sep 30 11:20:00 2026 +0000"},
		{Schema: "cairn.build/1", GoVersion: "devel go1.25-abcdef0 Wed Sep 30 11:20:00 2026 +0000"},
		{Schema: "cairn.build/1", GoVersion: "go1.25.0", VCS: "svn", Revision: "123456"},
	} {
		t.Run(build.GoVersion+build.VCS, func(t *testing.T) {
			client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
				json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": build})
			})
			info := invokeDiagnostic(t, diagnosticSession(t, client, 32000))
			if info.API.State != "available" || !reflect.DeepEqual(info.API.Build, &build) {
				t.Fatalf("legitimate producer identity rejected: %+v", info)
			}
		})
	}
}
