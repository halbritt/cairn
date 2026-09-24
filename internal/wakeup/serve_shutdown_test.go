package wakeup

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// A SIGTERM that lands while the outer wake-claim RPC is in flight must not
// turn an orderly supervisor shutdown into a failure exit. This reproduces
// the race deterministically by holding the claim response until the serve
// context is canceled. The fixture's lifetime is owned by the test, not by
// connection-disconnect observation.
func TestServeShutdownDuringInFlightClaim(t *testing.T) {
	runtime := t.TempDir()
	t.Setenv("XDG_RUNTIME_DIR", runtime)
	home := t.TempDir()
	listener, err := net.Listen("unix", filepath.Join(home, "api.sock"))
	if err != nil {
		t.Fatal(err)
	}
	claimStarted := make(chan struct{})
	release := make(chan struct{})
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/wake-claim" {
			_, _ = io.Copy(io.Discard, r.Body)
			close(claimStarted)
			select {
			case <-r.Context().Done():
			case <-release:
			}
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": map[string]any{}})
	}))
	server.Listener = listener
	server.Start()

	token := filepath.Join(home, "agent.token")
	if err = os.WriteFile(token, []byte("probe-token"), 0o600); err != nil {
		t.Fatal(err)
	}
	config := Config{
		Name: "shutdown-probe", Principal: "shutdown-probe", Repo: "shutdown-probe",
		Socket: listener.Addr().String(), AgentToken: token, ObserverToken: token, Directory: home,
		Command:        []string{"/bin/true"},
		TimeoutSeconds: 10, StateDirectory: home,
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer func() {
		cancel()       // Release the supervisor on every failure path.
		close(release) // Release the held claim before closing the server.
		server.Close()
	}()
	done := make(chan error, 1)
	go func() { done <- Serve(ctx, configPath(t, config), &strings.Builder{}) }()
	select {
	case <-claimStarted:
	case err := <-done:
		t.Fatalf("supervisor exited before claiming: %v", err)
	case <-time.After(5 * time.Second):
		t.Fatal("supervisor never issued its first claim")
	}
	cancel() // SIGTERM arrives while the claim RPC is in flight.
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("in-flight claim cancellation became a supervisor failure: %v", err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("supervisor did not shut down")
	}
}

func configPath(t *testing.T, config Config) string {
	t.Helper()
	body, err := json.Marshal(config)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "binding.json")
	if err = os.WriteFile(path, body, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}
