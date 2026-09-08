package localapi_test

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net"
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
	"github.com/halbritt/cairn/runner"
)

func authenticatedHost(t *testing.T, role, destination string, wrap func(http.Handler) http.Handler) (*localapi.Client, *core.Store, string, string) {
	t.Helper()
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	ctx := context.Background()
	repo := uuid.NewString()
	admin, err := core.Open(ctx, dsn, core.Channel{Principal: "host-test-admin", Operator: true})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(admin.Close)
	if err = admin.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	_, err = admin.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "AUTHENTICATED-HOST-CONTEXT", ClaimType: "self", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}})
	if err != nil {
		t.Fatal(err)
	}
	token := "synthetic-observer-token"
	hash := sha256.Sum256([]byte(token))
	api, err := localapi.New(ctx, dsn, []localapi.Identity{{TokenSHA256: hex.EncodeToString(hash[:]), Principal: "host:test", Repo: repo, Role: role, Destination: destination}})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(api.Close)
	home, err := os.MkdirTemp("/tmp", "cairn-host-test-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.RemoveAll(home); err != nil {
			t.Error(err)
		}
	})
	socket := filepath.Join(home, "api.sock")
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	var handler http.Handler = api
	if wrap != nil {
		handler = wrap(handler)
	}
	server := &http.Server{Handler: handler}
	done := make(chan error, 1)
	go func() { done <- server.Serve(listener) }()
	t.Cleanup(func() {
		if err := server.Close(); err != nil {
			t.Error(err)
		}
		if err := <-done; !errors.Is(err, http.ErrServerClosed) {
			t.Error(err)
		}
	})
	tokenFile := filepath.Join(home, "observer.token")
	if err = os.WriteFile(tokenFile, []byte(token), 0600); err != nil {
		t.Fatal(err)
	}
	client, err := localapi.NewClient(socket, tokenFile)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(client.Close)
	return client, admin, repo, home
}

func TestAuthenticatedRunnerOwnsProcessAndContextWithoutOperator(t *testing.T) {
	client, _, repo, home := authenticatedHost(t, "observer", "local", nil)
	ctx := context.Background()
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	req := runner.Request{Compile: core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Purpose: "context", AvailableTokens: 32000}, Destination: core.Destination{Name: "local", AllowLocal: true}, Command: []string{"/bin/cat"}, Carrier: "stdin", Prompt: "HOST-PROMPT-CANARY", Timeout: time.Second, ArtifactDirectory: filepath.Join(home, "runs")}
	var output, stderr bytes.Buffer
	result, err := runner.Run(ctx, client, req, &output, &stderr)
	if err != nil {
		t.Fatal(err)
	}
	if result.ExitCode == nil || *result.ExitCode != 0 || result.OutcomeID == "" || !strings.Contains(output.String(), "AUTHENTICATED-HOST-CONTEXT") || !strings.Contains(output.String(), req.Prompt) {
		t.Fatalf("missing observed delivery/outcome: %+v", result)
	}
	observer, err := core.Open(ctx, dsn, core.Channel{Principal: "host:test", Repo: repo, Instrumented: true})
	if err != nil {
		t.Fatal(err)
	}
	defer observer.Close()
	replay, err := observer.Replay(ctx, result.ReceiptID)
	if err != nil {
		t.Fatal(err)
	}
	if replay.Seal != result.Seal {
		t.Fatal("receipt changed")
	}
	body, err := os.ReadFile(filepath.Join(result.Artifacts, "context.txt"))
	if err != nil || strings.Contains(string(body), req.Prompt) {
		t.Fatalf("context custody failed or retained prompt: %v", err)
	}
	var report core.RunReport
	if err = client.Call(ctx, "run-report", core.RunReportRequest{Repo: repo, Limit: 10}, &report); err != nil {
		t.Fatal(err)
	}
	encoded, _ := json.Marshal(report)
	if strings.Contains(string(encoded), req.Prompt) || len(report.Rows) != 1 {
		t.Fatalf("run report changed population or retained prompt: %s", encoded)
	}
	_, err = runner.Run(ctx, client, req, &output, &stderr)
	if core.Code(err) != "RUN_ALREADY_STARTED" {
		t.Fatalf("duplicate launch permitted: %v", err)
	}
}

func TestAuthenticatedRunnerRejectsAgentAndDestinationMismatch(t *testing.T) {
	for _, scenario := range []struct {
		name, role, destination string
		declared                core.Destination
	}{
		{"agent cannot observe", "agent", "local", core.Destination{Name: "local", AllowLocal: true}},
		{"local profile cannot be declared hosted", "observer", "local", core.Destination{Name: "hosted"}},
		{"hosted profile cannot be declared local", "observer", "hosted", core.Destination{Name: "local", AllowLocal: true}},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			client, _, repo, home := authenticatedHost(t, scenario.role, scenario.destination, nil)
			marker := filepath.Join(home, "launched")
			req := runner.Request{Compile: core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Purpose: "context", AvailableTokens: 32000}, Destination: scenario.declared, Command: []string{"/bin/sh", "-c", "touch \"$1\"", "fixture", marker}, Carrier: "stdin", Timeout: time.Second, ArtifactDirectory: filepath.Join(home, "runs")}
			var out bytes.Buffer
			_, err := runner.Run(context.Background(), client, req, &out, &out)
			if core.Code(err) != "AUTHORITY_DENIED" {
				t.Fatalf("expected refusal, got %v", err)
			}
			if scenario.role == "agent" {
				_, err = client.RegisterManagedContext(context.Background(), core.ManagedContextRequest{})
				if core.Code(err) != "AUTHORITY_DENIED" {
					t.Fatalf("agent obtained filesystem observation authority: %v", err)
				}
			}
			if _, err = os.Stat(marker); !errors.Is(err, os.ErrNotExist) {
				t.Fatalf("unauthorized process launched: %v", err)
			}
		})
	}
}

func TestAuthenticatedRunnerDoesNotRetryAmbiguousCommit(t *testing.T) {
	for _, operation := range []string{"claim-run", "outcome"} {
		t.Run(operation, func(t *testing.T) {
			var lost atomic.Bool
			var requests atomic.Int32
			wrap := func(next http.Handler) http.Handler {
				return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.URL.Path != "/v1/"+operation {
						next.ServeHTTP(w, r)
						return
					}
					requests.Add(1)
					if lost.CompareAndSwap(false, true) {
						recorded := httptest.NewRecorder()
						next.ServeHTTP(recorded, r)
						if recorded.Code != http.StatusOK {
							t.Errorf("fault did not follow a committed operation: %d", recorded.Code)
						}
						conn, _, err := w.(http.Hijacker).Hijack()
						if err != nil {
							t.Error(err)
							return
						}
						if err = conn.Close(); err != nil {
							t.Error(err)
						}
						return
					}
					next.ServeHTTP(w, r)
				})
			}
			client, _, repo, home := authenticatedHost(t, "observer", "local", wrap)
			marker := filepath.Join(home, "launches")
			req := runner.Request{Compile: core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Purpose: "context", AvailableTokens: 32000}, Destination: core.Destination{Name: "local", AllowLocal: true}, Command: []string{"/bin/sh", "-c", "printf x >> \"$1\"", "fixture", marker}, Carrier: "stdin", Timeout: time.Second, ArtifactDirectory: filepath.Join(home, "runs")}
			var output bytes.Buffer
			result, err := runner.Run(context.Background(), client, req, &output, &output)
			if err == nil || requests.Load() != 1 {
				t.Fatalf("ambiguous commit was hidden or retried: count=%d err=%v", requests.Load(), err)
			}
			status, statusErr := client.RunStatus(context.Background(), result.ReceiptID)
			if statusErr != nil || !status.LaunchClaimed {
				t.Fatalf("ambiguous commit cannot be inspected: %+v %v", status, statusErr)
			}
			if operation == "claim-run" {
				if result.ReceiptID == "" || result.Seal == "" || status.Outcome != nil {
					t.Fatal("ambiguous claim lost its receipt for host inspection")
				}
				if _, err = os.Stat(marker); !errors.Is(err, os.ErrNotExist) {
					t.Fatalf("launched after ambiguous claim: %v", err)
				}
			} else {
				body, err := os.ReadFile(marker)
				if err != nil || string(body) != "x" {
					t.Fatalf("expected exactly one execution: %q %v", body, err)
				}
				pending, err := os.ReadFile(filepath.Join(result.Artifacts, "outcome.pending.json"))
				if err != nil {
					t.Fatal(err)
				}
				var request core.OutcomeRequest
				if err = json.Unmarshal(pending, &request); err != nil {
					t.Fatal(err)
				}
				if status.Outcome == nil {
					t.Fatal("committed outcome hidden after lost response")
				}
				first, err := client.RecordOutcome(context.Background(), request)
				if err != nil {
					t.Fatal(err)
				}
				retry, err := client.RecordOutcome(context.Background(), request)
				if err != nil || first.ID != retry.ID || first.ID != status.Outcome.ObservationID {
					t.Fatalf("outcome recovery lost idempotence: %v", err)
				}
				var report core.RunReport
				if err = client.Call(context.Background(), "run-report", core.RunReportRequest{Repo: repo, Limit: 10}, &report); err != nil || len(report.Rows) != 1 || !report.Rows[0].OutcomeObserved {
					t.Fatalf("missing recovered observation: %+v %v", report, err)
				}
			}
			_, err = runner.Run(context.Background(), client, req, &output, &output)
			if core.Code(err) != "RUN_ALREADY_STARTED" {
				t.Fatalf("ambiguous operation permitted a new launch: %v", err)
			}
		})
	}
}
