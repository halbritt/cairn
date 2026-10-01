package localapi_test

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/buildinfo"
	"github.com/halbritt/cairn/localapi"
)

type capturedRequest struct {
	header http.Header
	body   string
}

// recordingAPI answers every request with an empty success and records it.
func recordingAPI(t *testing.T, handler func(http.ResponseWriter, *http.Request)) (*localapi.Client, func() []capturedRequest) {
	t.Helper()
	dir, err := os.MkdirTemp("", "cairn-clientdiag-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	token := filepath.Join(dir, "token")
	if err = os.WriteFile(token, []byte("synthetic-token"), 0600); err != nil {
		t.Fatal(err)
	}
	socket := filepath.Join(dir, "api.sock")
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	var seen []capturedRequest
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		buffer := make([]byte, 1<<16)
		n, _ := r.Body.Read(buffer)
		mu.Lock()
		seen = append(seen, capturedRequest{header: r.Header.Clone(), body: string(buffer[:n])})
		mu.Unlock()
		if handler != nil {
			handler(w, r)
			return
		}
		_, _ = w.Write([]byte(`{"schema":"cairn.response/1","ok":true,"status":"OK","data":{},"protocol":{"min":1,"current":2}}`))
	})}
	go server.Serve(listener)
	t.Cleanup(func() { server.Close() })
	client, err := localapi.NewClient(socket, token)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(client.Close)
	return client, func() []capturedRequest {
		mu.Lock()
		defer mu.Unlock()
		return append([]capturedRequest(nil), seen...)
	}
}

func decodeDeclaration(t *testing.T, value string) map[string]any {
	t.Helper()
	decoded, err := base64.RawURLEncoding.Strict().DecodeString(value)
	if err != nil || len(value) > 4096 || len(decoded) > 3072 {
		t.Fatalf("not a bounded unpadded base64url value: %v %d", err, len(value))
	}
	var out map[string]any
	if err = json.Unmarshal(decoded, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func TestClientDeclaresItselfOnlyWhenAskedAndNeverInRequestBodies(t *testing.T) {
	client, seen := recordingAPI(t, nil)
	ctx := context.Background()
	request := map[string]string{"request_id": "fixed-request-identity", "text": "hello"}
	if err := client.Call(ctx, "create", request, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	declared := client.WithDiagnostics(localapi.ClientDiagnostics{Surface: "mcp", Harness: "codex", RetrievalCapabilities: localapi.CurrentRetrievalCapabilities(),
		TransportBuild: buildinfo.Info{Schema: "cairn.build/1", GoVersion: "go9.9", Revision: "caller-chosen"}})
	for i := 0; i < 2; i++ {
		if err := declared.Call(ctx, "create", request, &struct{}{}); err != nil {
			t.Fatal(err)
		}
	}
	// A view for another surface changes the header but not the request identity.
	other := client.WithDiagnostics(localapi.ClientDiagnostics{Surface: "cli", Harness: "unknown"})
	if err := other.Call(ctx, "create", request, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	if err := client.Call(ctx, "create", request, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	calls := seen()
	if len(calls) != 5 {
		t.Fatalf("%d requests: a declaration must not add or retry any", len(calls))
	}
	if calls[0].header.Get(localapi.ClientDiagnosticsHeader) != "" || calls[4].header.Get(localapi.ClientDiagnosticsHeader) != "" {
		t.Fatal("the base client must not declare, before or after a view exists")
	}
	for i, call := range calls[1:] {
		if call.body != calls[0].body {
			t.Fatalf("a declaration altered the request body:\n%s\n%s", call.body, calls[0].body)
		}
		if len(call.header.Values("Authorization")) != 1 || (i < 3 && len(call.header.Values(localapi.ClientDiagnosticsHeader)) != 1) {
			t.Fatalf("headers: %v", call.header)
		}
	}
	decoded := decodeDeclaration(t, calls[1].header.Get(localapi.ClientDiagnosticsHeader))
	build := decoded["transport_build"].(map[string]any)
	executing := buildinfo.Read()
	if decoded["schema"] != localapi.ClientDiagnosticsSchema || decoded["surface"] != "mcp" || decoded["harness"] != "codex" ||
		build["go_version"] != executing.GoVersion || build["vcs_revision"] == "caller-chosen" || build["go_version"] == "go9.9" {
		t.Fatalf("the transport build must come from the executing process: %v", decoded)
	}
	if capabilities := decoded["retrieval_capabilities"].(map[string]any); capabilities["search_memory_budget_bytes"] != true {
		t.Fatalf("%v", capabilities)
	}
	if decoded["origin"] != nil {
		t.Fatalf("an origin appeared that nothing reported: %v", decoded)
	}
	if again := decodeDeclaration(t, calls[2].header.Get(localapi.ClientDiagnosticsHeader)); again["surface"] != "mcp" {
		t.Fatal("the identity is fixed when the view is made")
	}
	if cli := decodeDeclaration(t, calls[3].header.Get(localapi.ClientDiagnosticsHeader)); cli["surface"] != "cli" || cli["harness"] != "unknown" {
		t.Fatalf("%v", cli)
	}
}

func TestSessionViewsKeepTheDeclarationAndTheirSessionHeaders(t *testing.T) {
	client, seen := recordingAPI(t, nil)
	declared := client.WithDiagnostics(localapi.ClientDiagnostics{Surface: "cli", Harness: "unknown"})
	session := declared.ForAgentSession(core.AgentSessionRef{AgentID: "11111111-1111-4111-8111-111111111111", ExecutionID: "22222222-2222-4222-8222-222222222222"})
	if err := session.Call(context.Background(), "version", struct{}{}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	call := seen()[0]
	if call.header.Get(localapi.ClientDiagnosticsHeader) == "" || call.header.Get("Cairn-Agent-ID") == "" || call.header.Get("Cairn-Execution-ID") == "" {
		t.Fatalf("%v", call.header)
	}
}

func TestADeclarationNeverEnablesRetryOrReplay(t *testing.T) {
	var attempts atomic.Int32
	client, seen := recordingAPI(t, func(w http.ResponseWriter, r *http.Request) {
		attempts.Add(1)
		connection, _, err := w.(http.Hijacker).Hijack()
		if err == nil {
			connection.Close() // the reply is lost after the request was received
		}
	})
	declared := client.WithDiagnostics(localapi.ClientDiagnostics{Surface: "cli", Harness: "unknown"})
	err := declared.Call(context.Background(), "create", map[string]string{"request_id": "fixed"}, &struct{}{})
	if err == nil {
		t.Fatal("a lost reply must surface as an error")
	}
	time.Sleep(200 * time.Millisecond)
	if attempts.Load() != 1 || len(seen()) != 1 {
		t.Fatalf("the client replayed a request: %d attempts", attempts.Load())
	}
	// A server-side refusal is final too.
	refusing, count := recordingAPI(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(400)
		_, _ = w.Write([]byte(`{"schema":"cairn.response/1","ok":false,"status":"INVALID_REQUEST","message":"no","protocol":{"min":1,"current":2}}`))
	})
	if err := refusing.WithDiagnostics(localapi.ClientDiagnostics{Surface: "cli"}).Call(context.Background(), "create", map[string]string{"request_id": "x"}, &struct{}{}); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("%v", err)
	}
	if len(count()) != 1 {
		t.Fatal("a refusal was retried")
	}
}

func TestAnUnsendableDeclarationIsOmittedWithoutFailingTheCall(t *testing.T) {
	if localapi.EncodeClientDiagnostics(localapi.ClientDiagnostics{Surface: "cli", TransportBuild: buildinfo.Info{Schema: "cairn.build/1", GoVersion: "<bad>"}}) != "" {
		t.Fatal("an invalid build was encoded")
	}
	client, seen := recordingAPI(t, nil)
	declared := client.WithDiagnostics(localapi.ClientDiagnostics{Surface: "cli", Origin: &localapi.ClientOrigin{Component: "unlisted", Basis: "reported"}})
	if err := declared.Call(context.Background(), "version", struct{}{}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	decoded := decodeDeclaration(t, seen()[0].header.Get(localapi.ClientDiagnosticsHeader))
	if decoded["origin"] != nil {
		t.Fatalf("an unlisted origin was sent: %v", decoded)
	}
}
