package main

import (
	"context"
	"encoding/json"
	"encoding/pem"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

// TestMachineEnrollmentEndToEnd provisions a machine on a central identity
// configuration, serves it through the real remote handler over TLS, enrolls a
// joining home whose relay forwards the local socket, and exercises shared
// memory, directory attribution, remote limits and revocation.
func TestMachineEnrollmentEndToEnd(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	fixture, err := core.Open(ctx, dsn, core.Channel{Principal: "machine-fixture"})
	if err != nil {
		t.Fatal(err)
	}
	if err = fixture.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	fixture.Close()

	var remote atomic.Value
	upstreamServer := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		remote.Load().(http.Handler).ServeHTTP(w, r)
	}))
	upstreamServer.StartTLS()
	defer upstreamServer.Close()
	roots := filepath.Join(t.TempDir(), "roots.pem")
	if err = os.WriteFile(roots, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: upstreamServer.Certificate().Raw}), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("SSL_CERT_FILE", roots) // the relay verifies with ordinary system roots

	collection := "/collections/" + uuid.NewString()
	localToken := "local-agent-token-" + uuid.NewString()
	central := machineHome(t, `[{"token_sha256":"`+tokenDigest(localToken)+`","principal":"agent/local-`+uuid.NewString()[:8]+`","repo":"`+collection+`","role":"agent","destination":"hosted"}]`)
	out := filepath.Join(central, "box.enroll")
	if _, err = changeMachine(ctx, "provision", []string{"--machine", "box-b", "--upstream", upstreamServer.URL, "--collection", collection, "--observer", "--out", out}); err != nil {
		t.Fatal(err)
	}
	load := func() *localapi.Server {
		t.Helper()
		body, err := os.ReadFile(filepath.Join(central, "identities.json"))
		if err != nil {
			t.Fatal(err)
		}
		var identities []localapi.Identity
		decoder := json.NewDecoder(strings.NewReader(string(body)))
		decoder.DisallowUnknownFields() // the same strict decoding cairn serve uses
		if err = decoder.Decode(&identities); err != nil {
			t.Fatal(err)
		}
		server, err := localapi.New(ctx, dsn, identities)
		if err != nil {
			t.Fatal(err)
		}
		if err = server.SetLocalMachineID("central"); err != nil {
			t.Fatal(err)
		}
		remote.Store(server.RemoteHandler())
		return server
	}
	server := load() // what --restart does for the real service
	defer func() { server.Close() }()

	home := machineHome(t, "")
	fakeSystemctl(t, home)
	relayDone := make(chan error, 1)
	go func() {
		for {
			if _, err := os.Stat(filepath.Join(home, "restarted")); err == nil {
				break
			}
			time.Sleep(20 * time.Millisecond)
		}
		relayDone <- relayRemote(ctx, []string{"--socket", filepath.Join(home, "api.sock"), "--upstream", upstreamServer.URL})
	}()
	result, err := enrollMachine(ctx, []string{"--file", out})
	// Test binaries carry no VCS stamp. Build identity is diagnostic only, so
	// enrollment succeeds on the real relay's protocol check and notes the gap.
	if err != nil || !strings.Contains(strings.Join(result.Notes, "\n"), "cannot confirm the same build") {
		t.Fatalf("enroll: %+v %v", result, err)
	}
	if len(result.Checks) != 2 || !result.Checks[0].OK || !result.Checks[1].OK {
		t.Fatalf("connectivity through relay: %+v", result.Checks)
	}

	// Shared memory: the enrolled machine saves with its defaults from another
	// directory; the central local profile reads the same collection.
	t.Chdir(t.TempDir())
	saved, err := agentRequest(ctx, []string{"remember", "--shareable", "--request-id", uuid.NewString(), "remote machine note " + collection}, strings.NewReader(""))
	if err != nil {
		t.Fatal(err)
	}
	record, _ := json.Marshal(saved)
	var created core.Record
	if err = json.Unmarshal(record, &created); err != nil || created.Scope.Repo != collection {
		t.Fatalf("remote note: %s %v", record, err)
	}
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/v1/history", strings.NewReader(`{"record_id":"`+created.RecordID+`","version":1}`))
	request.Header.Set("Authorization", "Bearer "+localToken)
	server.ServeHTTP(recorder, request)
	if recorder.Code != 200 || !strings.Contains(recorder.Body.String(), "remote machine note") {
		t.Fatalf("central read of remote note: %d %s", recorder.Code, recorder.Body)
	}

	// Directory attribution comes from server configuration.
	if _, err = agentsCommand(ctx, []string{"register", "--request-id", uuid.NewString(), "--binding", "box", "--native-session", "same-native-id", "--harness", "claude", "--project", "cairn", "--workspace", "/elsewhere/cairn"}); err != nil {
		t.Fatal(err)
	}
	listed, err := agentsCommand(ctx, []string{"list", "--machine-id", "box-b"})
	if encoded, _ := json.Marshal(listed); err != nil || !strings.Contains(string(encoded), `"machine_id":"box-b"`) || !strings.Contains(string(encoded), "same-native-id") {
		t.Fatalf("machine directory: %s %v", listed, err)
	}
	other, err := agentsCommand(ctx, []string{"list", "--machine-id", "central"})
	if encoded, _ := json.Marshal(other); err != nil || strings.Contains(string(encoded), "same-native-id") {
		t.Fatalf("central filter returned the remote session: %s %v", other, err)
	}

	// Remote limits: operations outside the allowlist, and local profiles on the
	// network listener, are refused.
	if _, err = agentRequest(ctx, []string{"--token-file", filepath.Join(home, "hosted-observer.token"), "register-context"}, strings.NewReader("{}")); core.Code(err) != "AUTHORITY_DENIED" {
		t.Fatalf("remote register-context: %v", err)
	}
	if _, err = versionOverSocket(ctx, filepath.Join(home, "api.sock"), localToken, 0); core.Code(err) != "AUTHORITY_DENIED" {
		t.Fatalf("local profile on the network listener: %v", err)
	}

	// Revocation takes effect when the API reloads its configuration.
	if _, err = changeMachine(ctx, "revoke", []string{"--machine", "box-b", "--identities", filepath.Join(central, "identities.json")}); err != nil {
		t.Fatal(err)
	}
	server.Close()
	server = load()
	status, err := machineStatus(ctx, nil)
	if core.Code(err) != "API_CONNECTION_FAILED" || len(status.Checks) != 2 || status.Checks[0].OK || status.Checks[0].Status != "AUTHORITY_DENIED" {
		t.Fatalf("revoked machine status: %+v %v", status, err)
	}
	cancel()
	if err := <-relayDone; err != nil {
		t.Fatalf("relay: %v", err)
	}
}
