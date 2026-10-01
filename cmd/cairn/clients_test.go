package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

type clientsCall struct {
	path   string
	header http.Header
	body   string
}

// clientsAPI is a fake authenticated API on a private socket; it records calls.
func clientsAPI(t *testing.T, handler func(http.ResponseWriter, *http.Request)) (socket, token string, calls func() []clientsCall) {
	t.Helper()
	directory, err := os.MkdirTemp("/tmp", "cairn-clients-test-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(directory) })
	token, socket = filepath.Join(directory, "agent.token"), filepath.Join(directory, "api.sock")
	if err = os.WriteFile(token, []byte("synthetic-clients-token"), 0600); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	var seen []clientsCall
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		buffer := make([]byte, 1<<14)
		n, _ := r.Body.Read(buffer)
		mu.Lock()
		seen = append(seen, clientsCall{path: r.URL.Path, header: r.Header.Clone(), body: string(buffer[:n])})
		mu.Unlock()
		handler(w, r)
	})}
	go server.Serve(listener)
	t.Cleanup(func() { server.Close() })
	return socket, token, func() []clientsCall {
		mu.Lock()
		defer mu.Unlock()
		return append([]clientsCall(nil), seen...)
	}
}

const emptyView = `{"schema":"cairn.response/1","ok":true,"status":"OK","protocol":{"min":1,"current":2},"data":{"schema":"cairn.clients/1","storage":"volatile","rows":[],"returned":0,"eligible_rows":0}}`

func answer(body string) func(http.ResponseWriter, *http.Request) {
	return func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(body)) }
}

func declarationOf(t *testing.T, header http.Header) map[string]any {
	t.Helper()
	value := header.Get(localapi.ClientDiagnosticsHeader)
	decoded, err := base64.RawURLEncoding.Strict().DecodeString(value)
	if value == "" || err != nil {
		t.Fatalf("no usable declaration: %q %v", value, err)
	}
	var out map[string]any
	if err = json.Unmarshal(decoded, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func TestClientsListsTheBoundedViewAndSendsOnlyALimit(t *testing.T) {
	t.Setenv("CAIRN_CALLER_DIAGNOSTICS", "")
	socket, token, calls := clientsAPI(t, answer(emptyView))
	for _, args := range [][]string{
		{"clients", "--socket", socket, "--token-file", token, "--limit", "7"},
		{"agent", "--socket", socket, "--token-file", token, "clients", "--limit", "7"},
	} {
		result, err := run(context.Background(), args, strings.NewReader(""))
		if err != nil {
			t.Fatal(err)
		}
		encoded, _ := json.Marshal(result)
		if !strings.Contains(string(encoded), `"schema":"cairn.clients/1"`) || !strings.Contains(string(encoded), `"storage":"volatile"`) {
			t.Fatalf("%s", encoded)
		}
	}
	if _, err := run(context.Background(), []string{"clients", "--socket", socket, "--token-file", token}, strings.NewReader("")); err != nil {
		t.Fatal(err)
	}
	seen := calls()
	if len(seen) != 3 {
		t.Fatalf("%d calls", len(seen))
	}
	for i, call := range seen {
		if call.path != "/v1/clients" || call.header.Get("Authorization") != "Bearer synthetic-clients-token" {
			t.Fatalf("%+v", call)
		}
		var body map[string]any
		if err := json.Unmarshal([]byte(call.body), &body); err != nil {
			t.Fatalf("%v: %s", err, call.body)
		}
		delete(body, "cairn_protocol")
		if i < 2 && (len(body) != 1 || body["limit"] != float64(7)) || i == 2 && len(body) != 0 {
			t.Fatalf("the request may carry only a limit: %s", call.body)
		}
	}
}

func TestClientsChecksItsArgumentsBeforeAnyConnection(t *testing.T) {
	socket, token, calls := clientsAPI(t, answer(emptyView))
	for _, args := range [][]string{
		{"clients", "--socket", socket, "--token-file", token, "--limit", "0"},
		{"clients", "--socket", socket, "--token-file", token, "--limit", "101"},
		{"clients", "--socket", socket, "--token-file", token, "--limit", "-3"},
		{"clients", "--socket", socket, "--token-file", token, "--limit", "many"},
		{"clients", "--socket", socket, "--token-file", token, "extra"},
		{"clients", "--socket", socket, "--token-file", token, "--all"},
		{"clients", "--socket", socket, "--token-file", token, "--principal", "other"},
		{"clients", "--socket", socket, "--token-file", token, "--profile", "x"},
		{"agent", "--socket", socket, "--token-file", token, "clients", "--limit", "500"},
		{"agent", "--socket", socket, "--token-file", token, "clients", "extra"},
		{"agent", "--socket", socket, "--token-file", token, "clients", "--cursor", "x"},
	} {
		if _, err := run(context.Background(), args, strings.NewReader("")); core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("%v: %v", args, err)
		}
	}
	if len(calls()) != 0 {
		t.Fatalf("an invalid command reached the API: %v", calls())
	}
}

func TestClientsAgainstAnOlderAPIIsUnsupportedNotEmpty(t *testing.T) {
	socket, token, _ := clientsAPI(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(404)
		_, _ = w.Write([]byte(`{"schema":"cairn.response/1","ok":false,"status":"NOT_FOUND","message":"unknown endpoint","protocol":{"min":1,"current":2}}`))
	})
	_, err := run(context.Background(), []string{"clients", "--socket", socket, "--token-file", token}, strings.NewReader(""))
	if core.Code(err) != "UNSUPPORTED_DIAGNOSTICS" || !strings.Contains(err.Error(), "unknown") {
		t.Fatalf("an old API must be reported as unsupported, never an empty list: %v", err)
	}
	for status, code := range map[string]string{"AUTHORITY_DENIED": "AUTHORITY_DENIED", "INVALID_REQUEST": "INVALID_REQUEST"} {
		denied, deniedToken, _ := clientsAPI(t, func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(400)
			_, _ = w.Write([]byte(`{"schema":"cairn.response/1","ok":false,"status":"` + status + `","message":"no","protocol":{"min":1,"current":2}}`))
		})
		if _, err = run(context.Background(), []string{"clients", "--socket", denied, "--token-file", deniedToken}, strings.NewReader("")); core.Code(err) != code {
			t.Fatalf("%s: %v", status, err)
		}
	}
	home := t.TempDir()
	missing := filepath.Join(home, "absent.sock")
	if _, err = run(context.Background(), []string{"clients", "--socket", missing, "--token-file", token}, strings.NewReader("")); core.Code(err) != "API_CONNECTION_FAILED" || strings.Contains(err.Error(), home) {
		t.Fatalf("an unavailable API stays unavailable, without a path: %v", err)
	}
}

func TestClientsHelpNeedsNoCredentialsOrConnection(t *testing.T) {
	t.Setenv("HOME", "")
	t.Setenv("CAIRN_HOME", "")
	for _, args := range [][]string{{"clients", "--help"}, {"agent", "clients", "--help"}} {
		result, err := run(context.Background(), args, strings.NewReader(""))
		if err != nil {
			t.Fatalf("%v: %v", args, err)
		}
		text, ok := result.(commandHelp)
		if !ok || !strings.Contains(string(text), "reported claim") || !strings.Contains(string(text), "volatile") || strings.Contains(string(text), "--all") {
			t.Fatalf("%v: %T %v", args, result, result)
		}
	}
}

func TestTopLevelClientsResolvesAProvisionedProfileToken(t *testing.T) {
	socket, _, calls := clientsAPI(t, answer(emptyView))
	home := t.TempDir()
	if err := os.MkdirAll(filepath.Join(home, "event-profiles"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(home, "event-profiles", "reader.token"), []byte("profile-token-value"), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("CAIRN_HOME", home)
	if _, err := run(context.Background(), []string{"clients", "--profile", "reader", "--socket", socket}, strings.NewReader("")); err != nil {
		t.Fatal(err)
	}
	if seen := calls(); len(seen) != 1 || seen[0].header.Get("Authorization") != "Bearer profile-token-value" {
		t.Fatalf("%+v", seen)
	}
}

func TestEveryCLICallDeclaresItsOwnSurfaceAndOnlyAValidatedCallerOrigin(t *testing.T) {
	socket, token, calls := clientsAPI(t, answer(`{"schema":"cairn.response/1","ok":true,"status":"OK","data":{"schema":"cairn.build/1","go_version":"go1.25.0","vcs_modified":null},"protocol":{"min":1,"current":2}}`))
	cases := map[string]struct {
		env    string
		origin bool
	}{
		"none":           {"", false},
		"valid":          {`{"component":"lifecycle-memory","implementation_id":"memory.v3","basis":"reported"}`, true},
		"not allowlist":  {`{"component":"somewhere","basis":"reported"}`, false},
		"carries secret": {`{"component":"lifecycle-memory","basis":"reported","token":"TOKEN-canary"}`, false},
		"not json":       {"TOKEN-canary /home/user/path", false},
	}
	for name, test := range cases {
		t.Run(name, func(t *testing.T) {
			t.Setenv("CAIRN_CALLER_DIAGNOSTICS", test.env)
			before := len(calls())
			if _, err := run(context.Background(), []string{"agent", "--socket", socket, "--token-file", token, "version"}, strings.NewReader("")); err != nil {
				t.Fatal(err)
			}
			call := calls()[before]
			declared := declarationOf(t, call.header)
			if declared["surface"] != "cli" || declared["harness"] != "unknown" || declared["transport_build"] == nil {
				t.Fatalf("%v", declared)
			}
			if _, present := declared["origin"]; present != test.origin {
				t.Fatalf("origin present=%v want %v: %v", present, test.origin, declared)
			}
			if test.origin {
				origin := declared["origin"].(map[string]any)
				if origin["component"] != "lifecycle-memory" || origin["implementation_id"] != "memory.v3" || origin["basis"] != "reported" {
					t.Fatalf("%v", origin)
				}
			}
			decoded, _ := base64.RawURLEncoding.DecodeString(call.header.Get(localapi.ClientDiagnosticsHeader))
			if strings.Contains(string(decoded), "TOKEN-canary") || strings.Contains(string(decoded), "/home/user") {
				t.Fatal("caller input outside the allowlisted origin reached the declaration")
			}
			if build, _ := declared["transport_build"].(map[string]any); build["schema"] != "cairn.build/1" {
				t.Fatalf("the CLI reports its own build as transport, never as an origin: %v", declared)
			}
		})
	}
}
