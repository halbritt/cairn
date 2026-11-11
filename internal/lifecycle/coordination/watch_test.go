package coordination

import (
	"bytes"
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func newTestWatchCLI(t *testing.T, root, procRoot string) (*CLI, *value.SourceReaderProfile, *[]string) {
	t.Helper()
	cli, prof := newTestHookCLI(t, root)
	prof.Definition.Readers[siteLastJSONLRecord] = value.ReaderRoute{
		Input:  "bytes-json",
		Codec:  &value.Codec{Name: "utf-8", Errors: "strict"},
		Domain: "synthetic",
	}
	cli.ProcRoot = procRoot
	cli.Engine = nil
	var ops []string
	cli.Host.Call = func(_ context.Context, _ *value.Value, op string, body *value.Value, _ NativeCallOptions) (*value.Value, error) {
		ops = append(ops, op)
		switch op {
		case "agent-register":
			return nativeMap(
				"agent_id", "301e246e-a3b7-498b-b6a7-3d84e592097f",
				"execution_id", "da3e0447-4f10-40f7-b64d-6b66de691ef2",
				"native_session_id", body.Get("native_session_id"),
				"display_name", "agent-1268",
				"context_revision", 1,
				"metadata", body.Get("metadata"),
			), nil
		case "agent-heartbeat":
			return nativeMap(
				"agent_id", body.Get("agent_id"),
				"execution_id", body.Get("execution_id"),
				"native_session_id", "11111111-1111-4111-8111-111111111111",
				"display_name", "agent-1268",
				"context_revision", 2,
				"metadata", nativeMap("state", "idle", "delivery_mode", "existing-session", "workspace", root),
			), nil
		case "session-inbox-ready":
			return nativeMap("delivery_id", nil), nil
		case "session-delivery-observe":
			return nativeMap("ok", true), nil
		case "agent-retire":
			return nativeMap("ok", true), nil
		default:
			return nativeMap("ok", true), nil
		}
	}
	return cli, prof, &ops
}

func TestWatchOnceRegisterHeartbeatRetireAndQ10(t *testing.T) {
	root := t.TempDir()
	procRoot := procFixtureRoot(t)
	cli, _, ops := newTestWatchCLI(t, root, procRoot)
	liveRef := writeProcessFixture(t, procRoot, processFixture{pid: 2100, parent: 1, start: 500, state: "S", comm: "claude", exe: "/usr/bin/claude", argv: []string{"claude"}})
	stateDir := filepath.Join(root, "state")
	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		t.Fatal(err)
	}
	cfgDir := filepath.Join(root, "configs")
	if err := os.MkdirAll(cfgDir, 0o700); err != nil {
		t.Fatal(err)
	}
	cfg := nativeMap("binding", "claude-test", "harness", "claude", "repo", "/repo", "cairn", "/bin/true", "state_dir", stateDir, "socket", filepath.Join(root, "api.sock"), "token_file", filepath.Join(root, "token"), "native_delivery", true, "idle_wakeup", "/bin/true")
	cfgPath := filepath.Join(cfgDir, "claude-test.json")
	writePrivateFile(t, cfgPath, encodeFixtureJSON(t, cfg), 0o600)

	s1Path := filepath.Join(stateDir, "s1.json")
	procVal := nativeMap("pid", liveRef.PID, "start", int64(liveRef.Start), "boot", liveRef.Boot)
	regVal := nativeMap("native_session_id", "11111111-1111-4111-8111-111111111111", "metadata", nativeMap("state", "idle", "delivery_mode", "existing-session", "workspace", root))
	writePrivateFile(t, s1Path, encodeFixtureJSON(t, nativeMap("native_session_id", "11111111-1111-4111-8111-111111111111", "workspace", root, "process", procVal, "registration", regVal)), 0o600)

	// 1. First WatchCommand --once registers the live session.
	var out, errOut bytes.Buffer
	code, err := cli.RunCLI(context.Background(), []string{"watch", "--config-dir", cfgDir, "--once"}, strings.NewReader(""), &out, &errOut)
	if err != nil || code != 0 || len(*ops) != 1 || (*ops)[0] != "agent-register" {
		t.Fatalf("register cycle failed: code=%d err=%v ops=%v errOut=%q", code, err, *ops, errOut.String())
	}

	// 2. Second WatchOnce heartbeats, checks inbox ready, and reports delivery observation.
	*ops = nil
	if err := cli.WatchOnce(context.Background(), cfg, &errOut); err != nil {
		t.Fatal(err)
	}
	if len(*ops) < 2 || (*ops)[0] != "agent-heartbeat" || (*ops)[1] != "session-inbox-ready" {
		t.Fatalf("heartbeat cycle unexpected ops: %v (errOut=%q)", *ops, errOut.String())
	}

	// 3. Dead process retires the registered session.
	deadProc := nativeMap("pid", 9999, "start", 500, "boot", liveRef.Boot)
	s1State, err := cli.read(s1Path, SiteSessionRead)
	if err != nil {
		t.Fatal(err)
	}
	s1State.Set("process", deadProc)
	if err := cli.writeState(s1Path, s1State); err != nil {
		t.Fatal(err)
	}
	*ops = nil
	if err := cli.WatchOnce(context.Background(), cfg, &errOut); err != nil {
		t.Fatal(err)
	}
	s1After, err := cli.read(s1Path, SiteSessionRead)
	if err != nil || !value.Truth(s1After.Get("retired")) {
		t.Fatalf("expected retired state after dead process, got %v (err=%v)", s1After, err)
	}

	// 4. Q10: NonObjectError (AttributeError) on non-object state (including falsy []) escapes WatchOnce.
	badPath := filepath.Join(stateDir, "s2.json")
	writePrivateFile(t, badPath, []byte("[]"), 0o600)
	err = cli.WatchOnce(context.Background(), cfg, &errOut)
	var nonObj *journal.NonObjectError
	if !errors.As(err, &nonObj) {
		t.Fatalf("expected NonObjectError to escape WatchOnce, got %v", err)
	}
}

func TestWatchCodexErrorWakeAndAgySubagentFinished(t *testing.T) {
	if cls, name := codexErrorClass(value.Text("server_overloaded")); cls != "transient" || name != "server_overloaded" {
		t.Fatalf("unexpected codexErrorClass: %s, %s", cls, name)
	}
	if cls, name := codexErrorClass(nativeMap("http_connection_failed", nativeMap("http_status_code", 401))); cls != "persistent" || name != "http_connection_failed_401" {
		t.Fatalf("unexpected codexErrorClass 401: %s, %s", cls, name)
	}
	if cls, name := codexErrorClass(nativeMap("http_connection_failed", nativeMap("http_status_code", 503))); cls != "transient" || name != "http_connection_failed" {
		t.Fatalf("unexpected codexErrorClass 503: %s, %s", cls, name)
	}

	root := t.TempDir()
	procRoot := procFixtureRoot(t)
	cli, _, _ := newTestWatchCLI(t, root, procRoot)
	now := time.Date(2026, 11, 11, 18, 0, 0, 0, time.UTC)
	cli.Now = func() time.Time { return now }

	liveRef := writeProcessFixture(t, procRoot, processFixture{pid: 2200, parent: 1, start: 600, state: "S", comm: "agy", exe: "/usr/bin/agy", argv: []string{"agy"}})
	procVal := nativeMap("pid", liveRef.PID, "start", int64(liveRef.Start), "boot", liveRef.Boot)
	stateDir := filepath.Join(root, "state")
	agyHomeDir := filepath.Join(root, "agy_home")
	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		t.Fatal(err)
	}
	cfg := nativeMap("binding", "agy-test", "harness", "agy", "state_dir", stateDir, "agy_home", agyHomeDir, "api_socket", filepath.Join(root, "api.sock"), "token_file", filepath.Join(root, "token"), "native_delivery", true, "idle_wakeup", true)

	parentID := "11111111-1111-4111-8111-111111111111"
	subID := "22222222-2222-4222-8222-222222222222"
	parentPath := filepath.Join(stateDir, "parent.json")
	subPath := filepath.Join(stateDir, "sub.json")
	parentAgent := nativeMap("agent_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "execution_id", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "native_session_id", parentID, "display_name", "parent-agent", "metadata", nativeMap("state", "busy", "delivery_mode", "existing-session", "workspace", root))
	subAgent := nativeMap("agent_id", "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "execution_id", "dddddddd-dddd-4ddd-8ddd-dddddddddddd", "native_session_id", subID, "display_name", "sub-agent", "metadata", nativeMap("state", "idle", "delivery_mode", "existing-session", "workspace", root))
	writePrivateFile(t, parentPath, encodeFixtureJSON(t, nativeMap("native_session_id", parentID, "workspace", root, "process", procVal, "agent", parentAgent)), 0o600)
	writePrivateFile(t, subPath, encodeFixtureJSON(t, nativeMap("native_session_id", subID, "workspace", root, "process", procVal, "agent", subAgent)), 0o600)

	subMetaDir := filepath.Join(agyHomeDir, "brain", parentID, ".system_generated", "subagents")
	if err := os.MkdirAll(subMetaDir, 0o700); err != nil {
		t.Fatal(err)
	}
	writePrivateFile(t, filepath.Join(subMetaDir, subID+".json"), encodeFixtureJSON(t, nativeMap("conversationId", subID, "state", "SUBAGENT_STATE_KILLED")), 0o600)

	var errOut bytes.Buffer
	if err := cli.WatchOnce(context.Background(), cfg, &errOut); err != nil {
		t.Fatal(err)
	}
	subAfter, err := cli.read(subPath, SiteSessionRead)
	if err != nil || !value.Truth(subAfter.Get("retired")) || nativeText(subAfter.Get("ending_evidence").Get("parent_native_session_id")) != parentID {
		t.Fatalf("expected killed agy subagent to retire with ending_evidence, got %v (err=%v)", subAfter, err)
	}
}

func TestWatchIdleWakeInboxAndOpenCodeCancel(t *testing.T) {
	root := t.TempDir()
	procRoot := procFixtureRoot(t)
	cli, _, ops := newTestWatchCLI(t, root, procRoot)
	now := time.Date(2026, 11, 11, 18, 30, 0, 0, time.UTC)
	cli.Now = func() time.Time { return now }

	liveRef := writeProcessFixture(t, procRoot, processFixture{pid: 2300, parent: 1, start: 700, state: "S", comm: "agy", exe: "/usr/bin/agy", argv: []string{"agy"}, environ: []byte("HERDR_SOCKET=/tmp/herdr.sock\x00HERDR_PANE_ID=w4V:p9\x00")})
	procVal := nativeMap("pid", liveRef.PID, "start", int64(liveRef.Start), "boot", liveRef.Boot)
	stateDir := filepath.Join(root, "state")
	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		t.Fatal(err)
	}
	deliveryID := "33333333-3333-4333-8333-333333333333"
	attemptID := "44444444-4444-4444-8444-444444444444"
	leaseID := "55555555-5555-4555-8555-555555555555"

	baseCall := cli.Host.Call
	cli.Host.Call = func(ctx context.Context, cfg *value.Value, op string, body *value.Value, opts NativeCallOptions) (*value.Value, error) {
		switch op {
		case "session-inbox-ready":
			return nativeMap("delivery_id", deliveryID), nil
		case "session-inbox-reconcile":
			if value.SameText(body.Get("reason"), "delivery_completed") {
				return nil, &CoordinationError{Code: "DELIVERY_ACTIVE", Message: "delivery is still active"}
			}
			return nativeMap("released", true), nil
		case "event-renew":
			*ops = append(*ops, op)
			return nativeMap("delivery_id", deliveryID, "lease_id", leaseID, "state", "leased", "event", nativeMap("event_id", deliveryID, "kind", "request")), nil
		default:
			return baseCall(ctx, cfg, op, body, opts)
		}
	}

	cfg := nativeMap("binding", "agy-wake", "harness", "agy", "repo", "/repo", "cairn", "/bin/true", "state_dir", stateDir, "socket", filepath.Join(root, "api.sock"), "token_file", filepath.Join(root, "token"), "native_delivery", true, "idle_wakeup", "/bin/true")
	sPath := filepath.Join(stateDir, "s1.json")
	agentVal := nativeMap("agent_id", "301e246e-a3b7-498b-b6a7-3d84e592097f", "execution_id", "da3e0447-4f10-40f7-b64d-6b66de691ef2", "native_session_id", "11111111-1111-4111-8111-111111111111", "display_name", "agent-1268", "metadata", nativeMap("state", "idle", "delivery_mode", "existing-session", "workspace", root))
	writePrivateFile(t, sPath, encodeFixtureJSON(t, nativeMap("native_session_id", "11111111-1111-4111-8111-111111111111", "workspace", root, "process", procVal, "agent", agentVal)), 0o600)

	var errOut bytes.Buffer
	if err := cli.WatchOnce(context.Background(), cfg, &errOut); err != nil {
		t.Fatal(err)
	}
	afterWake, err := cli.read(sPath, SiteSessionRead)
	if err != nil || nativeText(afterWake.Get("wake_refusal").Get("delivery_id")) != deliveryID || nativeText(afterWake.Get("delivery_observation").Get("value").Get("condition")) != "terminal_host_unavailable" {
		t.Fatalf("expected wake_refusal and terminal_host_unavailable delivery_observation for delivery_id %s, got %v (errOut=%q)", deliveryID, afterWake, errOut.String())
	}

	// Active inbox_intent without turn end renews lease during watchInbox.
	sessRef := nativeMap("agent_id", "301e246e-a3b7-498b-b6a7-3d84e592097f", "execution_id", "da3e0447-4f10-40f7-b64d-6b66de691ef2")
	afterWake.Set("inbox_intent", nativeMap("request_id", attemptID, "session", sessRef))
	afterWake.Set("inbox_attempt", nativeMap("attempt_id", attemptID, "session", sessRef, "delivery", nativeMap("delivery_id", deliveryID, "lease_id", leaseID, "event", nativeMap("kind", "request"))))
	if err := cli.writeState(sPath, afterWake); err != nil {
		t.Fatal(err)
	}
	*ops = nil
	if err := cli.WatchOnce(context.Background(), cfg, &errOut); err != nil {
		t.Fatal(err)
	}
	if !nativeContains(*ops, "event-renew") {
		t.Fatalf("expected event-renew in watchInbox, got ops=%v", *ops)
	}
}

func TestWatchIntervalsPinned(t *testing.T) {
	if watchIntervalSeconds != 30.0 || cancelPollIntervalSeconds != 2.0 {
		t.Fatalf("expected watch intervals (30.0, 2.0), got (%v, %v)", watchIntervalSeconds, cancelPollIntervalSeconds)
	}
}

func TestWatchInboxFlushAttemptScope(t *testing.T) {
	root := t.TempDir()
	procRoot := procFixtureRoot(t)
	cli, _, ops := newTestWatchCLI(t, root, procRoot)
	stateDir := filepath.Join(root, "state")
	if err := os.MkdirAll(filepath.Join(stateDir, "intents"), 0o700); err != nil {
		t.Fatal(err)
	}
	deliveryID := "33333333-3333-4333-8333-333333333333"
	oldAttempt := "66666666-6666-4666-8666-666666666666"
	attemptID := "44444444-4444-4444-8444-444444444444"
	leaseID := "55555555-5555-4555-8555-555555555555"

	baseCall := cli.Host.Call
	cli.Host.Call = func(ctx context.Context, cfg *value.Value, op string, body *value.Value, opts NativeCallOptions) (*value.Value, error) {
		switch op {
		case "session-inbox-reconcile":
			*ops = append(*ops, op)
			if value.SameText(body.Get("reason"), "delivery_completed") {
				return nil, &CoordinationError{Code: "DELIVERY_ACTIVE", Message: "delivery is still active"}
			}
			return nativeMap("released", true), nil
		case "event-renew":
			*ops = append(*ops, op)
			return nativeMap("delivery_id", deliveryID, "lease_id", leaseID, "state", "leased", "event", nativeMap("event_id", deliveryID, "kind", "request")), nil
		default:
			return baseCall(ctx, cfg, op, body, opts)
		}
	}

	cfg := nativeMap("binding", "claude-scope", "harness", "claude", "repo", "/repo", "cairn", "/bin/true", "state_dir", stateDir, "socket", filepath.Join(root, "api.sock"), "token_file", filepath.Join(root, "token"), "native_delivery", true)
	sessRef := nativeMap("agent_id", "301e246e-a3b7-498b-b6a7-3d84e592097f", "execution_id", "da3e0447-4f10-40f7-b64d-6b66de691ef2")
	agentVal := nativeMap("agent_id", "301e246e-a3b7-498b-b6a7-3d84e592097f", "execution_id", "da3e0447-4f10-40f7-b64d-6b66de691ef2", "native_session_id", "11111111-1111-4111-8111-111111111111", "display_name", "agent-1268", "metadata", nativeMap("state", "busy", "delivery_mode", "existing-session", "workspace", root))
	state := nativeMap(
		"native_session_id", "11111111-1111-4111-8111-111111111111",
		"workspace", root,
		"agent", agentVal,
		"inbox_intent", nativeMap("request_id", attemptID, "session", sessRef),
		"inbox_attempt", nativeMap("attempt_id", attemptID, "session", sessRef, "delivery", nativeMap("delivery_id", deliveryID, "lease_id", leaseID, "event", nativeMap("kind", "request"))),
		"inbox_journals", stringArray(oldAttempt, attemptID),
	)
	writePrivateFile(t, filepath.Join(stateDir, "intents", oldAttempt+".json"), []byte("{}"), 0o600)
	oldLock, err := AcquireIntentLock(context.Background(), stateDir, oldAttempt, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = oldLock.Release() }()

	sPath := filepath.Join(stateDir, "s1.json")
	writePrivateFile(t, sPath, encodeFixtureJSON(t, state), 0o600)
	if _, err := cli.watchInbox(context.Background(), cfg, state, sPath); err != nil || !nativeContains(*ops, "event-renew") {
		t.Fatalf("expected old busy journal not to block active attempt renew, got err=%v ops=%v", err, *ops)
	}

	// Busy journal of the ACTIVE attempt blocks SettleInbox and Renew.
	writePrivateFile(t, filepath.Join(stateDir, "intents", attemptID+".json"), []byte("{}"), 0o600)
	activeLock, err := AcquireIntentLock(context.Background(), stateDir, attemptID, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = activeLock.Release() }()
	*ops = nil
	if _, err := cli.watchInbox(context.Background(), cfg, state, sPath); err != nil || len(*ops) != 0 {
		t.Fatalf("expected busy active journal to block settle/renew, got err=%v ops=%v", err, *ops)
	}
}

func TestWatchOpenCodeCancelPass(t *testing.T) {
	root := t.TempDir()
	procRoot := procFixtureRoot(t)
	cli, _, ops := newTestWatchCLI(t, root, procRoot)
	liveRef := writeProcessFixture(t, procRoot, processFixture{pid: 2400, parent: 1, start: 800, state: "S", comm: "opencode", exe: "/usr/bin/opencode", argv: []string{"opencode"}})
	procVal := nativeMap("pid", liveRef.PID, "start", int64(liveRef.Start), "boot", liveRef.Boot)
	stateDir := filepath.Join(root, "state")
	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		t.Fatal(err)
	}
	deliveryID := "33333333-3333-4333-8333-333333333333"
	attemptID := "44444444-4444-4444-8444-444444444444"
	leaseID := "55555555-5555-4555-8555-555555555555"

	baseCall := cli.Host.Call
	cli.Host.Call = func(ctx context.Context, cfg *value.Value, op string, body *value.Value, opts NativeCallOptions) (*value.Value, error) {
		switch op {
		case "session-inbox-control":
			*ops = append(*ops, op)
			sessRef := nativeMap("agent_id", "301e246e-a3b7-498b-b6a7-3d84e592097f", "execution_id", "da3e0447-4f10-40f7-b64d-6b66de691ef2")
			return nativeMap(
				"attempt", nativeMap(
					"attempt_id", attemptID,
					"session", sessRef,
					"turn_exclusive", true,
					"native_turn_id", "turn-1",
					"cancel", nativeMap("requested_at", "2026-11-11T18:00:00Z"),
					"delivery", nativeMap("delivery_id", deliveryID, "lease_id", leaseID, "event", nativeMap("kind", "request")),
				),
			), nil
		default:
			return baseCall(ctx, cfg, op, body, opts)
		}
	}

	cfg := nativeMap("binding", "oc-test", "harness", "opencode", "repo", "/repo", "cairn", "/bin/true", "state_dir", stateDir, "socket", filepath.Join(root, "api.sock"), "token_file", filepath.Join(root, "token"), "native_delivery", true, "opencode_cancel_enabled", true)
	sessRef := nativeMap("agent_id", "301e246e-a3b7-498b-b6a7-3d84e592097f", "execution_id", "da3e0447-4f10-40f7-b64d-6b66de691ef2")
	agentVal := nativeMap("agent_id", "301e246e-a3b7-498b-b6a7-3d84e592097f", "execution_id", "da3e0447-4f10-40f7-b64d-6b66de691ef2", "native_session_id", "11111111-1111-4111-8111-111111111111", "display_name", "agent-1268", "metadata", nativeMap("state", "busy", "delivery_mode", "existing-session", "workspace", root))
	state := nativeMap(
		"native_session_id", "11111111-1111-4111-8111-111111111111",
		"workspace", root,
		"process", procVal,
		"agent", agentVal,
		"opencode_request_endpoint", filepath.Join(root, "missing-opencode.sock"),
		"inbox_intent", nativeMap("request_id", attemptID, "session", sessRef),
		"inbox_attempt", nativeMap("attempt_id", attemptID, "session", sessRef, "turn_exclusive", true, "native_turn_id", "turn-1", "cancel", nativeMap("requested_at", "2026-11-11T18:00:00Z"), "delivery", nativeMap("delivery_id", deliveryID, "lease_id", leaseID, "event", nativeMap("kind", "request"))),
	)
	sPath := filepath.Join(stateDir, "s1.json")
	writePrivateFile(t, sPath, encodeFixtureJSON(t, state), 0o600)

	pending, err := cli.pendingCancelConfigs([]*value.Value{cfg})
	if err != nil || len(pending) != 1 {
		t.Fatalf("expected 1 pendingCancelConfig, got %d (err=%v)", len(pending), err)
	}
	var errOut bytes.Buffer
	if stillPending, err := cli.watchPendingCancels(context.Background(), pending[0], &errOut); err != nil || !stillPending {
		t.Fatalf("watchPendingCancels failed: stillPending=%v err=%v", stillPending, err)
	}
	if !nativeContains(*ops, "session-inbox-control") {
		t.Fatalf("expected session-inbox-control in watchPendingCancels, got ops=%v", *ops)
	}
	afterCancel, err := cli.read(sPath, SiteSessionRead)
	if err != nil || nativeText(afterCancel.Get("cancel_submission").Get("status")) != "refused:unavailable" {
		t.Fatalf("expected cancel_submission status refused:unavailable after missing socket submit, got %v (err=%v)", afterCancel, err)
	}
}

type stubCodexEvidence struct{ end, loc *value.Value }

func (s stubCodexEvidence) Read(_ context.Context, k EvidenceKind, _ EvidenceRequest) (*value.Value, error) {
	if k != EvidenceCodexLatest {
		return nil, &value.ProfileUnavailable{}
	}
	return nativeList(s.end, s.loc), nil
}

func TestWatchCodexErrorWakeAlgorithmAndClaudeChannelIdentity(t *testing.T) {
	root := t.TempDir()
	cli, _ := newTestHookCLI(t, root)
	cfg := nativeMap("binding", "codex-test", "harness", "codex")
	agentVal := nativeMap("agent_id", "301e246e-a3b7-498b-b6a7-3d84e592097f", "execution_id", "da3e0447-4f10-40f7-b64d-6b66de691ef2", "native_session_id", "11111111-1111-4111-8111-111111111111", "display_name", "agent-1268")
	sessRef := nativeMap("agent_id", "301e246e-a3b7-498b-b6a7-3d84e592097f", "execution_id", "da3e0447-4f10-40f7-b64d-6b66de691ef2")

	// 1. Assistant message present -> unverified (not persistent).
	endMsg := nativeMap("timestamp", "2026-11-11T18:00:00.000Z", "payload", nativeMap("turn_id", "t1", "last_agent_message", "done", "error", nativeMap("codex_error_info", "server_overloaded")))
	loc1 := nativeMap("offset", 0, "length", 10, "sha256", strings.Repeat("a", 64))
	cli.Engine = &RecoveryEngine{Host: cli.Host, Evidence: stubCodexEvidence{endMsg, loc1}}
	st1 := nativeMap("agent", agentVal)
	var errOut bytes.Buffer
	rec1, err := cli.codexErrorWake(context.Background(), cfg, st1, 1762884000, &errOut)
	if err != nil || nativeText(rec1.Get("decision")) != "unverified" {
		t.Fatalf("expected unverified when last_agent_message present, got %v (err=%v)", rec1, err)
	}

	// 2. Same error with wakes=1 and last_wake_at=ended+200 -> due = max(ended+300, last+600) = ended+800 (backoff at ended+600).
	endErr := nativeMap("timestamp", "2026-11-11T18:00:00.000Z", "payload", nativeMap("turn_id", "t2", "error", nativeMap("codex_error_info", "server_overloaded")))
	endedEpoch := float64(time.Date(2026, 11, 11, 18, 0, 0, 0, time.UTC).Unix())
	cli.Engine = &RecoveryEngine{Host: cli.Host, Evidence: stubCodexEvidence{endErr, loc1}}
	st2 := nativeMap("agent", agentVal, "codex_error_wake", nativeMap("session", sessRef, "wakes", 1, "last_wake_at", endedEpoch+200, "error", nativeMap("record", loc1)))
	rec2, err := cli.codexErrorWake(context.Background(), cfg, st2, endedEpoch+600, &errOut)
	if err != nil || nativeText(rec2.Get("decision")) != "backoff" || rec2.Get("next_eligible_at") == nil {
		t.Fatalf("expected backoff with next_eligible_at at ended+600 for wakes=1, got %v (err=%v)", rec2, err)
	}

	// 3. Unrelated later error outside [last, last+3600] resets wakes to 0 -> wake_allowed at ended2+301.
	endErr2 := nativeMap("timestamp", "2026-11-11T20:00:00.000Z", "payload", nativeMap("turn_id", "t3", "error", nativeMap("codex_error_info", "server_overloaded")))
	ended2Epoch := float64(time.Date(2026, 11, 11, 20, 0, 0, 0, time.UTC).Unix())
	loc2 := nativeMap("offset", 10, "length", 10, "sha256", strings.Repeat("b", 64))
	cli.Engine = &RecoveryEngine{Host: cli.Host, Evidence: stubCodexEvidence{endErr2, loc2}}
	st3 := nativeMap("agent", agentVal, "codex_error_wake", nativeMap("session", sessRef, "wakes", 3, "last_wake_at", endedEpoch, "delivery_id", "d1", "error", nativeMap("record", loc1)))
	rec3, err := cli.codexErrorWake(context.Background(), cfg, st3, ended2Epoch+301, &errOut)
	if err != nil || nativeText(rec3.Get("decision")) != "wake_allowed" || rec3.Get("wakes").Integer != "0" || rec3.Get("last_wake_at") != nil {
		t.Fatalf("expected chain reset to wakes=0 and wake_allowed, got %v (err=%v)", rec3, err)
	}

	// 3b. Fractional .002Z end timestamp computes exact Python UnixMicro/1e6 float (1762899899.002).
	endErr002 := nativeMap("timestamp", "2025-11-11T22:19:59.002Z", "payload", nativeMap("turn_id", "t4", "error", nativeMap("codex_error_info", "server_overloaded")))
	cli.Engine = &RecoveryEngine{Host: cli.Host, Evidence: stubCodexEvidence{endErr002, loc2}}
	st4 := nativeMap("agent", agentVal)
	rec4, err := cli.codexErrorWake(context.Background(), cfg, st4, 1762899900, &errOut)
	if err != nil || rec4.Get("next_eligible_epoch") == nil || rec4.Get("next_eligible_epoch").Number != 1762899899.002 {
		t.Fatalf("expected exact next_eligible_epoch 1762899899.002 for .002Z timestamp, got %v (err=%v)", rec4.Get("next_eligible_epoch"), err)
	}

	// 4. claudeChannelEndpoint with a live bridge accepts valid int identity and rejects float start, missing boot, and mismatched start.
	procRoot := procFixtureRoot(t)
	cli.ProcRoot = procRoot
	cli.Engine = nil
	parentRef := writeProcessFixture(t, procRoot, processFixture{pid: 4100, parent: 1, start: 13203067, comm: "claude", exe: "/usr/bin/claude", argv: []string{"claude"}})
	bridgeRef := writeProcessFixture(t, procRoot, processFixture{pid: 4101, parent: 4100, start: 13203100, comm: "cairn", exe: "/usr/bin/cairn", argv: []string{"cairn", "claude-channel"}})
	chDir := filepath.Join(root, "channels")
	if err := os.MkdirAll(chDir, 0o700); err != nil {
		t.Fatal(err)
	}
	chCfg := nativeMap("harness", "claude", "claude_channel_dir", chDir)
	proc := nativeMap("pid", parentRef.PID, "start", int64(parentRef.Start), "boot", parentRef.Boot)
	for _, tc := range []struct {
		name, parent string
		want         bool
	}{
		{"valid_int_identity", `{"pid":4100,"start":13203067,"boot":"` + parentRef.Boot + `"}`, true},
		{"float_start", `{"pid":4100,"start":13203067.0,"boot":"` + parentRef.Boot + `"}`, false},
		{"missing_boot", `{"pid":4100,"start":13203067}`, false},
		{"other_start", `{"pid":4100,"start":13203068,"boot":"` + parentRef.Boot + `"}`, false},
	} {
		body := `{"schema":"cairn.claude-channel/1","socket":"/tmp/ch.sock","process":{"pid":4101,"start":13203100,"boot":"` + bridgeRef.Boot + `"},"parent":` + tc.parent + `}`
		writePrivateFile(t, filepath.Join(chDir, "4100.json"), []byte(body), 0o600)
		ep, err := cli.claudeChannelEndpoint(chCfg, proc)
		if err != nil || (ep != nil) != tc.want {
			t.Fatalf("%s: want endpoint=%v, got endpoint=%v err=%v", tc.name, tc.want, ep != nil, err)
		}
	}
}

func TestFlushJournalStderrDiagnosticsParityWithPython(t *testing.T) {
	pyCoord, err := filepath.Abs(filepath.Join("..", "..", "..", "integrations", "lifecycle", "coordination.py"))
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name     string
		code     string
		exitCode int
	}{
		{name: "uncertain_pending", code: "API_UNAVAILABLE", exitCode: 7},
		{name: "final_refused", code: "STALE_LEASE", exitCode: 4},
	} {
		t.Run(tc.name, func(t *testing.T) {
			pyDir := filepath.Join(t.TempDir(), "state")
			goDir := filepath.Join(t.TempDir(), "state")
			attemptID := "11111111-1111-4111-8111-111111111111"
			fakeCairn := filepath.Join(t.TempDir(), "fake-cairn.sh")
			script := "#!/bin/sh\nprintf '{\"schema\":\"cairn.response/1\",\"ok\":false,\"status\":\"%s\",\"message\":\"synthetic\"}\\n'\nexit %d\n"
			writePrivateFile(t, fakeCairn, []byte(strings.Replace(strings.Replace(script, "%s", tc.code, 1), "%d", string(rune('0'+tc.exitCode)), 1)), 0o700)

			writeIntent := func(dir string) {
				intentPath := filepath.Join(dir, "intents", attemptID+".json")
				body := `{"schema":"cairn.inbox-intent/1","attempt_id":"` + attemptID + `","delivery_id":"22222222-2222-4222-8222-222222222222","kind":"request","session":{"agent_id":"301e246e-a3b7-498b-b6a7-3d84e592097f","execution_id":"da3e0447-4f10-40f7-b64d-6b66de691ef2"},"completion":{"kind":"complete","status":"pending","code":"` + tc.code + `","request_id":"33333333-3333-4333-8333-333333333333","argv":["` + fakeCairn + `"],"input":"ok"}}`
				writePrivateFile(t, intentPath, []byte(body), 0o600)
			}
			writeIntent(pyDir)
			writeIntent(goDir)

			pySnippet := `import importlib.util, sys
spec = importlib.util.spec_from_file_location("coordination", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
cfg = {"binding": "b1", "state_dir": sys.argv[2]}
state = {"agent": {"agent_id": "301e246e-a3b7-498b-b6a7-3d84e592097f", "execution_id": "da3e0447-4f10-40f7-b64d-6b66de691ef2"}, "inbox_journals": [sys.argv[3]]}
m.flush_inbox_intents(cfg, state, scope="attempt")
`
			cmd := exec.Command("python3", "-c", pySnippet, pyCoord, pyDir, attemptID)
			var pyStderr bytes.Buffer
			cmd.Stderr = &pyStderr
			if err := cmd.Run(); err != nil {
				t.Fatalf("python flush_inbox_intents failed: %v (stderr=%q)", err, pyStderr.String())
			}

			var goLines []string
			adapter := &SessionJournalAdapter{
				Log: func(msg string) error {
					goLines = append(goLines, msg+"\n")
					return nil
				},
			}
			goCfg := nativeMap("binding", "b1", "state_dir", goDir)
			goState := nativeMap(
				"agent", nativeMap("agent_id", "301e246e-a3b7-498b-b6a7-3d84e592097f", "execution_id", "da3e0447-4f10-40f7-b64d-6b66de691ef2"),
				"inbox_journals", stringArray(attemptID),
			)
			if _, err := adapter.FlushInboxIntents(context.Background(), goCfg, goState, FlushAttempt); err != nil {
				t.Fatalf("go FlushInboxIntents failed: %v", err)
			}
			gotGo := strings.ReplaceAll(strings.Join(goLines, ""), goDir, "<STATE>")
			wantPy := strings.ReplaceAll(pyStderr.String(), pyDir, "<STATE>")
			if gotGo != wantPy || gotGo == "" {
				t.Fatalf("flush_journal stderr mismatch:\ngo: %q\npy: %q", gotGo, wantPy)
			}
		})
	}
}
