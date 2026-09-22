package wakeup

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
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

func TestServeShutdownDuringControlStillFinishesCleanup(t *testing.T) {
	for _, refuseFinish := range []bool{false, true} {
		t.Run(map[bool]string{false: "confirmed", true: "finish_refused"}[refuseFinish], func(t *testing.T) {
			home := t.TempDir()
			t.Setenv("XDG_RUNTIME_DIR", home)
			t.Setenv("CAIRN_SHUTDOWN_FIXTURE", home)
			t.Setenv("PATH", home+string(os.PathListSeparator)+os.Getenv("PATH"))
			// No real service or worker is launched. The command boundary
			// records whether cleanup stopped the simulated owned unit.
			for name, script := range map[string]string{
				"systemd-run": "#!/bin/sh\ntouch \"$CAIRN_SHUTDOWN_FIXTURE/started\"\n",
				"systemctl": `#!/bin/sh
if [ "$2" = stop ]; then
  touch "$CAIRN_SHUTDOWN_FIXTURE/stopped"
  exit 0
fi
if [ -f "$CAIRN_SHUTDOWN_FIXTURE/stopped" ]; then
  printf 'LoadState=loaded\nActiveState=inactive\nControlGroup=\n'
else
  printf 'LoadState=loaded\nActiveState=active\nControlGroup=\n'
fi
`,
			} {
				if err := os.WriteFile(filepath.Join(home, name), []byte(script), 0700); err != nil {
					t.Fatal(err)
				}
			}
			listener, err := net.Listen("unix", filepath.Join(home, "api.sock"))
			if err != nil {
				t.Fatal(err)
			}
			entered, release := make(chan struct{}), make(chan struct{})
			finished := make(chan core.WakeChangeRequest, 1)
			attempt := core.WakeAttempt{ID: "shutdown-fixture", Delivery: core.AgentDelivery{DeliveryID: "fixture-delivery"}}
			server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var data any = map[string]any{}
				switch r.URL.Path {
				case "/v1/wake-claim":
					data = core.WakeResult{Attempt: &attempt}
				case "/v1/wake-control":
					close(entered)
					select {
					case <-r.Context().Done():
					case <-release:
					}
					return
				case "/v1/wake-change":
					var request core.WakeChangeRequest
					if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
						http.Error(w, err.Error(), 400)
						return
					}
					data = attempt
					if request.Operation == "finish" {
						finished <- request
						_, stopErr := os.Stat(filepath.Join(home, "stopped"))
						if refuseFinish || stopErr != nil {
							_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": false, "status": "CLEANUP_FAILED", "message": "fixture refused cleanup"})
							return
						}
					}
				}
				_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": data})
			}))
			server.Listener = listener
			server.Start()
			ctx, cancel := context.WithCancel(context.Background())
			defer func() {
				cancel()
				close(release)
				server.Close()
			}()
			token := filepath.Join(home, "agent.token")
			if err := os.WriteFile(token, []byte("fixture-token"), 0600); err != nil {
				t.Fatal(err)
			}
			config := Config{Name: "shutdown-fixture", Principal: "fixture", Repo: "fixture", Socket: listener.Addr().String(),
				AgentToken: token, ObserverToken: token, Directory: home, Command: []string{"/bin/true"}, TimeoutSeconds: 10, StateDirectory: home}
			done := make(chan error, 1)
			path := configPath(t, config)
			go func() { done <- Serve(ctx, path, io.Discard) }()
			select {
			case <-entered:
			case err := <-done:
				t.Fatalf("supervisor exited before control: %v", err)
			case <-time.After(5 * time.Second):
				t.Fatal("supervisor did not reach control")
			}
			cancel()
			select {
			case err := <-done:
				if refuseFinish {
					if core.Code(err) != "CLEANUP_FAILED" {
						t.Fatalf("cleanup failure was hidden: %v", err)
					}
				} else if err != nil {
					t.Fatalf("confirmed shutdown cleanup became a failure: %v", err)
				}
			case <-time.After(5 * time.Second):
				t.Fatal("shutdown did not finish cleanup")
			}
			select {
			case request := <-finished:
				if request.Reason != "supervisor_stopped" || request.AttemptID != attempt.ID {
					t.Fatalf("wrong cleanup report: %+v", request)
				}
			default:
				t.Fatal("shutdown did not report cleanup through its independent context")
			}
		})
	}
}

func TestOrderlyShutdownPreservesIndependentErrors(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if orderlyShutdown(ctx, context.Canceled) {
		t.Fatal("another operation's cancellation was treated as supervisor shutdown")
	}
	cancel()
	for _, err := range []error{errors.New("transport failure"), context.DeadlineExceeded} {
		if orderlyShutdown(ctx, err) {
			t.Fatalf("independent error disappeared during shutdown: %v", err)
		}
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
