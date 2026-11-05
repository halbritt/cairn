package main

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

func TestWaitHelpAndValidation(t *testing.T) {
	ctx := context.Background()

	// Help requests need no credentials
	for _, args := range [][]string{
		{"wait", "--help"},
		{"wait", "help"},
		{"wait"},
		{"agent", "wait", "--help"},
		{"work", "--help"},
		{"work", "features", "--help"},
	} {
		out, err := run(ctx, args, strings.NewReader(""))
		if err != nil {
			t.Fatalf("%v: unexpected error: %v", args, err)
		}
		help, ok := out.(commandHelp)
		if !ok || (!strings.Contains(string(help), "coordination waits") && !strings.Contains(string(help), "work coordination")) {
			t.Fatalf("%v: expected help, got: %v", args, out)
		}
	}

	// Subcommand validation errors
	testCases := []struct {
		name string
		args []string
	}{
		{"create-missing-request-id", []string{"wait", "create", "--predicate", "request_response", "--dependency", "b2e09aab-8d65-4548-b6a5-ef8340b5f9b9", "--deadline", "2026-12-01T00:00:00Z"}},
		{"create-missing-predicate", []string{"wait", "create", "--request-id", "d3e3903e-aebb-4a25-8ee3-7660946a20a5", "--dependency", "b2e09aab-8d65-4548-b6a5-ef8340b5f9b9", "--deadline", "2026-12-01T00:00:00Z"}},
		{"create-missing-dependency", []string{"wait", "create", "--request-id", "d3e3903e-aebb-4a25-8ee3-7660946a20a5", "--predicate", "request_response", "--deadline", "2026-12-01T00:00:00Z"}},
		{"create-missing-deadline", []string{"wait", "create", "--request-id", "d3e3903e-aebb-4a25-8ee3-7660946a20a5", "--predicate", "request_response", "--dependency", "b2e09aab-8d65-4548-b6a5-ef8340b5f9b9"}},
		{"create-invalid-deadline", []string{"wait", "create", "--request-id", "d3e3903e-aebb-4a25-8ee3-7660946a20a5", "--predicate", "request_response", "--dependency", "b2e09aab-8d65-4548-b6a5-ef8340b5f9b9", "--deadline", "not-a-date"}},
		{"get-missing-id", []string{"wait", "get"}},
		{"cancel-missing-request-id", []string{"wait", "cancel", "--wait", "d3e3903e-aebb-4a25-8ee3-7660946a20a5"}},
		{"cancel-missing-target", []string{"wait", "cancel", "--request-id", "d3e3903e-aebb-4a25-8ee3-7660946a20a5"}},
		{"unknown-subcommand", []string{"wait", "foobar"}},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := run(ctx, tc.args, strings.NewReader(""))
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("%s: expected INVALID_REQUEST, got: %v", tc.name, err)
			}
		})
	}
}

func TestWaitCLIWorkflow(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("PostgreSQL integration test: run make test-integration")
	}
	t.Setenv("CAIRN_DATABASE_URL", dsn)
	ctx := context.Background()
	repo := "cli-wait-" + uuid.NewString()

	host, err := core.Open(ctx, dsn, core.Channel{Principal: "operator:cli-test", Operator: true, Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	defer host.Close()
	if err := host.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err := host.SetCoordinationWaitsEnabled(ctx, core.WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatal(err)
	}

	token := "wait-cli-token-" + uuid.NewString()
	srv, err := localapi.New(ctx, dsn, []localapi.Identity{{
		TokenSHA256: tokenDigest(token),
		Principal:   "host:cli-test",
		Repo:        repo,
		Role:        "agent",
		Destination: "hosted",
	}})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(srv.Close)

	dir := t.TempDir()
	socketPath := filepath.Join(dir, "api.sock")
	tokenPath := filepath.Join(dir, "agent.token")
	if err := os.WriteFile(tokenPath, []byte(token+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	ln, err := net.Listen("unix", socketPath)
	if err != nil {
		t.Fatal(err)
	}
	httpSrv := &http.Server{Handler: srv}
	go func() { _ = httpSrv.Serve(ln) }()
	t.Cleanup(func() {
		_ = httpSrv.Close()
		_ = ln.Close()
	})

	agentStore, err := core.Open(ctx, dsn, core.Channel{Principal: "host:cli-test", Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	defer agentStore.Close()

	dest := core.Destination{Name: "hosted", AllowLocal: false}
	agentA, err := agentStore.RegisterAgent(ctx, core.RegisterAgentRequest{
		RequestID: uuid.NewString(), Binding: "cli-agent-a", NativeSessionID: uuid.NewString(),
		Metadata: core.AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	agentB, err := agentStore.RegisterAgent(ctx, core.RegisterAgentRequest{
		RequestID: uuid.NewString(), Binding: "cli-agent-b", NativeSessionID: uuid.NewString(),
		Metadata: core.AgentMetadata{Harness: "claude", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}

	sessionRefA := agentA.AgentID + ":" + agentA.ExecutionID

	// Create request note and publish request
	note, err := agentStore.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "work request", ClaimType: "self", Sensitivity: "shareable", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}})
	if err != nil {
		t.Fatal(err)
	}
	pubReq, err := agentStore.PublishEvent(ctx, core.PublishEventRequest{
		RequestID: uuid.NewString(), Repo: repo, Kind: "request",
		Ref:         core.RecordVersionRef{RecordID: note.RecordID, Version: 1},
		Destination: core.EventDestination{Type: "agent", Name: "agent/" + agentB.AgentID},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}

	callWait := func(subcommand string, extraArgs []string, target any) {
		t.Helper()
		args := append([]string{"wait", subcommand, "--socket", socketPath, "--token-file", tokenPath}, extraArgs...)
		out, err := run(ctx, args, strings.NewReader(""))
		if err != nil {
			t.Fatalf("%v: %v", args, err)
		}
		if target != nil {
			raw, err := json.Marshal(out)
			if err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal(raw, target); err != nil {
				t.Fatalf("unmarshal %s: %v", raw, err)
			}
		}
	}

	// 1. CLI wait create
	waitReqID := uuid.NewString()
	deadlineStr := time.Now().Add(10 * time.Minute).Format(time.RFC3339)
	var wait core.AgentWait
	callWait("create", []string{
		"--repo", repo,
		"--session", sessionRefA,
		"--predicate", "request_response",
		"--dependency", pubReq.EventID,
		"--deadline", deadlineStr,
		"--request-id", waitReqID,
	}, &wait)
	if wait.WaitUUID == "" {
		t.Fatalf("expected non-empty WaitUUID from CLI wait create")
	}

	// 2. CLI wait get
	var gotWait core.AgentWait
	callWait("get", []string{
		"--repo", repo,
		"--session", sessionRefA,
		"--wait", wait.WaitUUID,
	}, &gotWait)
	if gotWait.WaitUUID != wait.WaitUUID {
		t.Fatalf("expected AgentWait %s from CLI wait get, got: %s", wait.WaitUUID, gotWait.WaitUUID)
	}

	// 3. CLI wait list
	var listRes core.WaitListResult
	callWait("list", []string{
		"--repo", repo,
		"--session", sessionRefA,
	}, &listRes)
	if len(listRes.Waits) == 0 {
		t.Fatalf("expected non-empty waits from CLI wait list")
	}

	// 4. CLI wait cancel
	cancelReqID := uuid.NewString()
	var cancelledWait core.AgentWait
	callWait("cancel", []string{
		"--repo", repo,
		"--session", sessionRefA,
		"--wait", wait.WaitUUID,
		"--request-id", cancelReqID,
	}, &cancelledWait)
	if cancelledWait.State != core.WaitStateCancelled {
		t.Fatalf("expected cancelled AgentWait from CLI wait cancel, got: %s", cancelledWait.State)
	}
}

func TestWaitOperatorOperationsAndScheduler(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("PostgreSQL integration test: run make test-integration")
	}
	t.Setenv("CAIRN_DATABASE_URL", dsn)
	ctx := context.Background()
	repo := "cli-operator-wait-" + uuid.NewString()

	host, err := core.Open(ctx, dsn, core.Channel{Principal: "operator:cli-test", Operator: true, Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	defer host.Close()
	if err := host.Migrate(ctx); err != nil {
		t.Fatal(err)
	}

	callOperatorJSON := func(opName, inputJSON string, target any) {
		t.Helper()
		out, err := run(ctx, []string{opName}, strings.NewReader(inputJSON))
		if err != nil {
			t.Fatalf("%s failed: %v", opName, err)
		}
		if target != nil {
			raw, err := json.Marshal(out)
			if err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal(raw, target); err != nil {
				t.Fatalf("unmarshal %s: %v", raw, err)
			}
		}
	}

	// 1. Operator wait-feature-set
	var featStatus core.WorkFeatureStatus
	setJSON := `{"request_id":"` + uuid.NewString() + `","enabled":true}`
	callOperatorJSON("wait-feature-set", setJSON, &featStatus)
	if featStatus.FeatureName != "coordination-waits/1" || !featStatus.Enabled || featStatus.Status != "active" {
		t.Fatalf("unexpected wait-feature-set result: %+v", featStatus)
	}

	// 2. CLI work features status/enable/disable/retire
	var cliStatus core.WorkFeatureStatus
	out, err := run(ctx, []string{"work", "features", "status", "--feature", "coordination-waits/1"}, strings.NewReader(""))
	if err != nil {
		t.Fatalf("work features status: %v", err)
	}
	raw, _ := json.Marshal(out)
	_ = json.Unmarshal(raw, &cliStatus)
	if !cliStatus.Enabled || cliStatus.Status != "active" {
		t.Fatalf("expected active status from work features status, got: %+v", cliStatus)
	}

	out, err = run(ctx, []string{"work", "features", "disable", "--feature", "coordination-waits/1"}, strings.NewReader(""))
	if err != nil {
		t.Fatalf("work features disable: %v", err)
	}
	raw, _ = json.Marshal(out)
	_ = json.Unmarshal(raw, &cliStatus)
	if cliStatus.Enabled || cliStatus.Status != "disabled" {
		t.Fatalf("expected disabled status from work features disable, got: %+v", cliStatus)
	}

	out, err = run(ctx, []string{"work", "features", "enable", "--feature", "coordination-waits/1"}, strings.NewReader(""))
	if err != nil {
		t.Fatalf("work features enable: %v", err)
	}
	raw, _ = json.Marshal(out)
	_ = json.Unmarshal(raw, &cliStatus)
	if !cliStatus.Enabled || cliStatus.Status != "active" {
		t.Fatalf("expected active status after work features enable, got: %+v", cliStatus)
	}

	// 3. Operator wait-timeout-sweep
	var sweepRes core.WaitTimeoutSweepResult
	callOperatorJSON("wait-timeout-sweep", `{"repo":"`+repo+`"}`, &sweepRes)
	if sweepRes.Repo != repo {
		t.Fatalf("unexpected sweep result: %+v", sweepRes)
	}

	// 4. Operator wait-outbox-drain
	var drainRes core.WaitOutboxDrainResult
	callOperatorJSON("wait-outbox-drain", `{"repo":"`+repo+`","limit":50}`, &drainRes)
	if drainRes.Repo != repo {
		t.Fatalf("unexpected drain result: %+v", drainRes)
	}

	// 5. Operator wait-scheduler-tick
	var tickRes core.WaitSchedulerTickResult
	callOperatorJSON("wait-scheduler-tick", `{"repo":"`+repo+`","limit":50}`, &tickRes)
	if tickRes.Repo != repo {
		t.Fatalf("unexpected tick result: %+v", tickRes)
	}

	// 6. Installed scheduler daemon with --once flag
	outSched, err := run(ctx, []string{"schedule-serve", "--repo", repo, "--once"}, strings.NewReader(""))
	if err != nil {
		t.Fatalf("schedule-serve --once: %v", err)
	}
	schedRes, ok := outSched.(ScheduleServeOnceResult)
	if !ok || schedRes.Repo != repo {
		t.Fatalf("expected ScheduleServeOnceResult from schedule-serve --once, got: %T %+v", outSched, outSched)
	}

	// 7. LocalAPI rejects operator commands with NOT_FOUND
	token := "wait-cli-token-" + uuid.NewString()
	srv, err := localapi.New(ctx, dsn, []localapi.Identity{{
		TokenSHA256: tokenDigest(token),
		Principal:   "host:cli-test",
		Repo:        repo,
		Role:        "agent",
		Destination: "hosted",
	}})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(srv.Close)

	dir := t.TempDir()
	socketPath := filepath.Join(dir, "api.sock")
	tokenPath := filepath.Join(dir, "agent.token")
	if err := os.WriteFile(tokenPath, []byte(token+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	ln, err := net.Listen("unix", socketPath)
	if err != nil {
		t.Fatal(err)
	}
	httpSrv := &http.Server{Handler: srv}
	go func() { _ = httpSrv.Serve(ln) }()
	t.Cleanup(func() {
		_ = httpSrv.Close()
		_ = ln.Close()
	})

	client, err := localapi.NewClient(socketPath, tokenPath)
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()

	for _, unmountedOp := range []string{"wait-feature-set", "wait-timeout-sweep", "wait-outbox-drain", "wait-scheduler-tick"} {
		var ignored json.RawMessage
		err = client.Call(ctx, unmountedOp, map[string]any{"repo": repo}, &ignored)
		if core.Code(err) != "NOT_FOUND" {
			t.Fatalf("expected NOT_FOUND for operator op %s over localapi, got: %v", unmountedOp, err)
		}
	}
}
