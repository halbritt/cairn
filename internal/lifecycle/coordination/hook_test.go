package coordination

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func newTestHookCLI(t *testing.T, root string) (*CLI, *value.SourceReaderProfile) {
	t.Helper()
	prof := stage2ServicesProfile()
	yes, no := true, false
	codec := &value.Codec{Name: "utf-8", Errors: "surrogateescape"}
	prof.Definition.Readers[SiteHookEventRead] = value.ReaderRoute{Input: "bytes-json", Codec: codec, Domain: "synthetic"}
	prof.Definition.Writers[SiteHookCompactOutput] = value.WriterRoute{Route: "dumps", Codec: codec, EnsureASCII: &no, Compact: &yes, TrailingLF: &yes, Domain: "synthetic"}
	ja := &SessionJournalAdapter{
		PathText: func(v *value.Value) (string, error) { return sourcePathText(prof, v) },
	}
	fh := NewFileHost(FileHostOptions{
		Profile: prof,
		Journal: ja,
	})
	cli := &CLI{
		Host:     fh,
		Profile:  prof,
		Command:  []string{"python3", "/opt/cairn/coordination.py"},
		Now:      func() time.Time { return time.Unix(1700000000, 0).UTC() },
		Root:     filepath.Join(root, "journal"),
		ProcRoot: "/proc",
		Engine: NewRecoveryEngine(fh, nil, RecoveryHooks{
			Alive: func(_ *value.Value) (bool, error) { return true, nil },
		}, "/proc"),
	}
	return cli, prof
}

func TestHookCLIErrorsAndLimits(t *testing.T) {
	root := t.TempDir()
	cli, _ := newTestHookCLI(t, root)
	cfgPath := filepath.Join(root, "coord.json")
	cfgData := `{"binding":"b1","harness":"claude","repo":"/repo","state_dir":"` + filepath.Join(root, "state") + `","cairn":"/bin/true","socket":"/tmp/s.sock","token_file":"/tmp/t.tok","native_delivery":true}`
	if err := os.WriteFile(cfgPath, []byte(cfgData), 0o600); err != nil {
		t.Fatal(err)
	}

	var out, errOut bytes.Buffer
	oversized := bytes.Repeat([]byte("a"), 1024*1024+1)
	code, err := cli.RunCLI(context.Background(), []string{"hook", "--config", cfgPath}, bytes.NewReader(oversized), &out, &errOut)
	if err != nil || code != 1 || out.Len() != 0 || errOut.String() != "Cairn coordination: INVALID_HOST: hook input exceeds limit; session presence unavailable\n" {
		t.Fatalf("unexpected oversized result: code=%d err=%v out=%q errOut=%q", code, err, out.String(), errOut.String())
	}

	out.Reset()
	errOut.Reset()
	code, err = cli.RunCLI(context.Background(), []string{"hook", "--config", cfgPath}, strings.NewReader("not-json"), &out, &errOut)
	if err != nil || code != 1 || out.Len() != 0 || errOut.String() != "Cairn coordination: JSONDecodeError; session presence unavailable\n" {
		t.Fatalf("unexpected invalid JSON result: code=%d err=%v out=%q errOut=%q", code, err, out.String(), errOut.String())
	}

	out.Reset()
	errOut.Reset()
	badEv := fmt.Sprintf(`{"hook_event_name":"SessionStart","session_id":"","cwd":%q}`, root)
	code, err = cli.RunCLI(context.Background(), []string{"hook", "--config", cfgPath}, strings.NewReader(badEv), &out, &errOut)
	if err != nil || code != 1 || out.Len() != 0 || !strings.Contains(errOut.String(), "INVALID_HOST: native conversation ID required; session presence unavailable") {
		t.Fatalf("unexpected invalid session_id result: code=%d err=%v out=%q errOut=%q", code, err, out.String(), errOut.String())
	}
}

func TestHookLifecycleAndToolCue(t *testing.T) {
	root := t.TempDir()
	cli, _ := newTestHookCLI(t, root)
	stateDir := filepath.Join(root, "state")
	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		t.Fatal(err)
	}
	cfgPath := filepath.Join(root, "coord.json")
	cfgData := `{"binding":"b1","harness":"claude","repo":"/repo","state_dir":"` + stateDir + `","cairn":"/bin/true","socket":"/tmp/s.sock","token_file":"/tmp/t.tok","native_delivery":true,"remind_unacknowledged_turn_end":true}`
	if err := os.WriteFile(cfgPath, []byte(cfgData), 0o600); err != nil {
		t.Fatal(err)
	}

	cli.Host.OwnerProcess = func(_, _ *value.Value) (*value.Value, error) {
		return nativeMap("pid", 1234, "start", 5678, "state", "S", "parent", 1, "boot", "boot-1"), nil
	}

	agentID := "301e246e-a3b7-498b-b6a7-3d84e592097f"
	execID := "da3e0447-4f10-40f7-b64d-6b66de691ef2"
	nativeID := "22b421c2-2298-41e9-8b0e-659ea2a72ed0"
	deliveryID := "22222222-2222-4222-8222-222222222222"
	leaseID := "33333333-3333-4333-8333-333333333333"
	recordID := "44444444-4444-4444-8444-444444444444"

	agentVal := func(state string, rev int64) *value.Value {
		return nativeMap(
			"agent_id", agentID,
			"execution_id", execID,
			"display_name", "agent-1268",
			"inbox", "agent/"+agentID,
			"native_session_id", nativeID,
			"context_revision", nativeInt64(rev),
			"metadata", nativeMap("harness", "claude", "project", "cairn", "workspace", root, "delivery_mode", "existing-session", "state", state),
		)
	}
	var ops []string
	pendingDeliveries := 1
	pendingCounts := nativeMap("requests", 1, "notices", 0, "responses", 0, "latest_position", 5, "truncated", false)
	currentAgent := agentVal("busy", 1)

	cli.Host.Call = func(_ context.Context, _ *value.Value, op string, body *value.Value, _ NativeCallOptions) (*value.Value, error) {
		ops = append(ops, op)
		switch op {
		case "agent-register":
			currentAgent = agentVal("busy", 1)
			return currentAgent, nil
		case "agent-heartbeat":
			return currentAgent, nil
		case "agent-context":
			meta := body.Get("metadata")
			currentAgent = agentVal(nativeText(meta.Get("state")), 2)
			return currentAgent, nil
		case "session-inbox-claim":
			if pendingDeliveries == 0 {
				return nativeMap("attempt", nil), nil
			}
			pendingDeliveries--
			attemptID := body.Get("request_id")
			return nativeMap("attempt", nativeMap(
				"attempt_id", attemptID,
				"session", nativeMap("agent_id", agentID, "execution_id", execID),
				"delivery", nativeMap(
					"delivery_id", deliveryID,
					"lease_id", leaseID,
					"state", "leased",
					"event", nativeMap("event_id", deliveryID, "kind", "request", "from", "agent/sender", "ref", nativeMap("record_id", recordID, "version", 1)),
				),
			)), nil
		case "session-inbox-pending":
			return pendingCounts, nil
		case "event-renew":
			return nativeMap("delivery_id", deliveryID, "lease_id", leaseID, "state", "leased", "event", nativeMap("event_id", deliveryID, "kind", "request", "from", "agent/sender", "ref", nativeMap("record_id", recordID, "version", 1))), nil
		case "session-inbox-reconcile":
			if value.SameText(body.Get("reason"), "delivery_completed") {
				return nil, &CoordinationError{Code: "DELIVERY_ACTIVE", Message: "delivery is still active"}
			}
			return nativeMap("released", true), nil
		case "agent-leave":
			return nativeMap("unregistered", true), nil
		default:
			t.Fatalf("unexpected call op: %s", op)
			return nil, nil
		}
	}

	// 1. SessionStart registers agent and admits the waiting request delivery.
	var out, errOut bytes.Buffer
	startEv := fmt.Sprintf(`{"hook_event_name":"SessionStart","session_id":%q,"cwd":%q}`, nativeID, root)
	code, err := cli.RunCLI(context.Background(), []string{"hook", "--config", cfgPath}, strings.NewReader(startEv), &out, &errOut)
	if err != nil || code != 0 || errOut.Len() != 0 {
		t.Fatalf("SessionStart failed: code=%d err=%v errOut=%q", code, err, errOut.String())
	}
	if !strings.Contains(out.String(), "Cairn session agent-1268 has inbox agent/"+agentID) || !strings.Contains(out.String(), "Cairn has a request from agent/sender") {
		t.Fatalf("unexpected SessionStart output: %s", out.String())
	}

	// 2. PostToolUse emits tool cue on first arrival, then omits when counts are unchanged.
	out.Reset()
	toolEv := fmt.Sprintf(`{"hook_event_name":"PostToolUse","session_id":%q,"cwd":%q}`, nativeID, root)
	code, err = cli.RunCLI(context.Background(), []string{"hook", "--config", cfgPath}, strings.NewReader(toolEv), &out, &errOut)
	if err != nil || code != 0 || !strings.Contains(out.String(), "1 request waiting for this session") {
		t.Fatalf("PostToolUse cue failed: code=%d err=%v out=%q", code, err, out.String())
	}
	out.Reset()
	code, err = cli.RunCLI(context.Background(), []string{"hook", "--config", cfgPath}, strings.NewReader(toolEv), &out, &errOut)
	if err != nil || code != 0 || strings.TrimSpace(out.String()) != "{}" {
		t.Fatalf("expected second PostToolUse with same counts to return {}, got %q", out.String())
	}

	// 3. First Stop reminds about unacknowledged request, second Stop marks turn_ended and releases attempt.
	out.Reset()
	stopEv := fmt.Sprintf(`{"hook_event_name":"Stop","session_id":%q,"cwd":%q}`, nativeID, root)
	code, err = cli.RunCLI(context.Background(), []string{"hook", "--config", cfgPath}, strings.NewReader(stopEv), &out, &errOut)
	if err != nil || code != 0 || !strings.Contains(out.String(), `"decision": "block"`) || !strings.Contains(out.String(), "Cairn inbox reminder:") {
		t.Fatalf("first Stop reminder failed: code=%d err=%v out=%q", code, err, out.String())
	}
	out.Reset()
	code, err = cli.RunCLI(context.Background(), []string{"hook", "--config", cfgPath}, strings.NewReader(stopEv), &out, &errOut)
	if err != nil || code != 0 || strings.TrimSpace(out.String()) != "{}" {
		t.Fatalf("second Stop settlement failed: code=%d err=%v out=%q", code, err, out.String())
	}

	// 4. SessionEnd unregisters and removes state file.
	out.Reset()
	endEv := fmt.Sprintf(`{"hook_event_name":"SessionEnd","session_id":%q,"cwd":%q}`, nativeID, root)
	code, err = cli.RunCLI(context.Background(), []string{"hook", "--config", cfgPath}, strings.NewReader(endEv), &out, &errOut)
	if err != nil || code != 0 || strings.TrimSpace(out.String()) != "{}" {
		t.Fatalf("SessionEnd failed: code=%d err=%v out=%q", code, err, out.String())
	}
	statePath, locErr := cli.Host.Locate(nativeMap("binding", "b1", "state_dir", stateDir), nativeID)
	if locErr != nil {
		t.Fatal(locErr)
	}
	retiredState, loadErr := cli.Host.Load(statePath)
	if loadErr != nil || !value.Truth(retiredState.Get("retired")) || value.Truth(retiredState.Get("inbox_attempt")) {
		t.Fatalf("expected state file %s to be retired with cleared attempt after SessionEnd, got state=%v err=%v", statePath, retiredState, loadErr)
	}
}

func TestHookInboxRecallBridgeAndRefusalSettlement(t *testing.T) {
	root := t.TempDir()
	cli, _ := newTestHookCLI(t, root)
	stateDir := filepath.Join(root, "state")
	memStateDir := filepath.Join(root, "mem-state")
	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(memStateDir, 0o700); err != nil {
		t.Fatal(err)
	}
	manifestPath := filepath.Join(root, "binding-manifest.json")
	cfgPath := filepath.Join(root, "coord.json")
	memCfgPath := filepath.Join(root, "mem.json")

	coordMap := map[string]any{
		"binding":              "b1",
		"harness":              "claude",
		"repo":                 "/repo",
		"state_dir":            stateDir,
		"cairn":                "/bin/true",
		"socket":               "/tmp/s.sock",
		"token_file":           "/tmp/t.tok",
		"native_delivery":      true,
		"inbox_recall_binding": manifestPath,
	}
	memMap := map[string]any{
		"binding":              "m1",
		"harness":              "claude",
		"repo":                 "/repo",
		"state_dir":            memStateDir,
		"cairn":                "/bin/true",
		"socket":               "/tmp/s.sock",
		"token_file":           "/tmp/t.tok",
		"recall_mode":          "agent_tools",
		"context_bytes":        4000,
		"inbox_recall_binding": manifestPath,
	}
	coordBytes, _ := json.Marshal(coordMap)
	memBytes, _ := json.Marshal(memMap)
	if err := os.WriteFile(cfgPath, coordBytes, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(memCfgPath, memBytes, 0o600); err != nil {
		t.Fatal(err)
	}
	coordSum := sha256.Sum256(coordBytes)
	memSum := sha256.Sum256(memBytes)
	manifestMap := map[string]any{
		"schema":  "cairn.inbox-recall-binding/2",
		"enabled": true,
		"memory_config": map[string]any{
			"path":   memCfgPath,
			"sha256": hex.EncodeToString(memSum[:]),
		},
		"coordination_configs": []any{
			map[string]any{
				"path":   cfgPath,
				"sha256": hex.EncodeToString(coordSum[:]),
			},
		},
	}
	manifestBytes, _ := json.Marshal(manifestMap)
	if err := os.WriteFile(manifestPath, manifestBytes, 0o600); err != nil {
		t.Fatal(err)
	}
	cli.Host.OwnerProcess = func(_, _ *value.Value) (*value.Value, error) {
		return nativeMap("pid", 1234, "start", 5678, "state", "S", "parent", 1, "boot", "boot-1"), nil
	}
	cli.Host.Services = &ServiceBindings{
		Profile: cli.Profile,
		MemoryStateDir: func(_ *value.Value) (*value.Value, error) {
			return value.Text(memStateDir), nil
		},
	}

	agentID := "301e246e-a3b7-498b-b6a7-3d84e592097f"
	execID := "da3e0447-4f10-40f7-b64d-6b66de691ef2"
	nativeID := "22b421c2-2298-41e9-8b0e-659ea2a72ed0"
	deliveryID := "22222222-2222-4222-8222-222222222222"
	leaseID := "33333333-3333-4333-8333-333333333333"
	recordID := "44444444-4444-4444-8444-444444444444"

	currentAgent := nativeMap(
		"agent_id", agentID,
		"execution_id", execID,
		"display_name", "agent-1268",
		"inbox", "agent/"+agentID,
		"native_session_id", nativeID,
		"context_revision", nativeInt64(1),
		"metadata", nativeMap("harness", "claude", "project", "cairn", "workspace", root, "delivery_mode", "existing-session", "state", "idle"),
	)

	var renewed bool
	var observedReports []*value.Value
	cli.Host.Call = func(_ context.Context, _ *value.Value, op string, body *value.Value, _ NativeCallOptions) (*value.Value, error) {
		switch op {
		case "agent-register", "agent-heartbeat":
			return currentAgent, nil
		case "agent-context":
			meta := body.Get("metadata")
			currentAgent.Set("metadata", meta)
			return currentAgent, nil
		case "session-inbox-claim":
			attemptID := body.Get("request_id")
			return nativeMap("attempt", nativeMap(
				"attempt_id", attemptID,
				"session", nativeMap("agent_id", agentID, "execution_id", execID),
				"native_turn_id", body.Get("native_turn_id"),
				"delivery", nativeMap(
					"delivery_id", deliveryID,
					"lease_id", leaseID,
					"state", "leased",
					"event", nativeMap("event_id", deliveryID, "kind", "request", "from", "agent/sender", "ref", nativeMap("record_id", recordID, "version", 1)),
				),
			)), nil
		case "event-renew":
			renewed = true
			return nativeMap("delivery_id", deliveryID, "lease_id", leaseID, "state", "leased", "event", nativeMap("event_id", deliveryID, "kind", "request", "from", "agent/sender", "ref", nativeMap("record_id", recordID, "version", 1))), nil
		case "recall-observe":
			observedReports = append(observedReports, body)
			return nativeMap("ok", true), nil
		case "session-inbox-reconcile":
			if value.SameText(body.Get("reason"), "delivery_completed") {
				return nil, &CoordinationError{Code: "DELIVERY_ACTIVE", Message: "delivery is still active"}
			}
			return nativeMap("released", true), nil
		default:
			t.Fatalf("unexpected call op: %s", op)
			return nil, nil
		}
	}

	var bridgeFail error
	cli.Host.LoadInboxRecall = func(_ context.Context, _ *value.Value) error {
		return nil
	}
	cli.Host.InboxRecallOutput = func(ctx context.Context, req InboxRecallRequest) (string, error) {
		attempt := req.State.Get("inbox_attempt")
		if err := req.Revalidate(attempt); err != nil {
			return "", err
		}
		if req.Reports != nil {
			*req.Reports = append(*req.Reports, func() error {
				_, e := cli.Host.Call(ctx, req.Config, "recall-observe", nativeMap("outcome", "delivered", "attempt_id", attempt.Get("attempt_id")), NativeCallOptions{Session: attempt.Get("session")})
				return e
			})
		}
		if bridgeFail != nil {
			return "", bridgeFail
		}
		return req.Message + "\n[INBOX_RECALL_GRANT]", nil
	}
	wakeBody := fmt.Sprintf(
		"This is a new live turn from the configured Cairn automatic inbox wakeup. "+
			"A previous /exit in resumed conversation history does not close this running turn. "+
			"Current agent %s, execution %s. Wake delivery %s. "+
			"Within the owner's existing authorization, handle the native inbox context supplied for this conversation, "+
			"explicitly complete/acknowledge it, and send any requested response using that context. "+
			"If no matching native context was supplied, report that and stop. "+
			"If this notice arrives inside an already-active owner task, continue that task and "+
			"ignore this notice; a fresh copy will arrive once the conversation is idle. "+
			"Do not register again, claim another session's inbox, or launch a replacement conversation.",
		agentID, execID, deliveryID,
	)
	wakePrompt := fmt.Sprintf("<channel source=\"cairn-events\" native_session_id=%q agent_id=%q execution_id=%q delivery_id=%q>\n%s\n</channel>", nativeID, agentID, execID, deliveryID, wakeBody)
	wakeEv := fmt.Sprintf(`{"hook_event_name":"UserPromptSubmit","session_id":%q,"cwd":%q,"prompt_id":"p-1","prompt":%q}`, nativeID, root, wakePrompt)

	var out, errOut bytes.Buffer
	code, err := cli.RunCLI(context.Background(), []string{"hook", "--config", cfgPath}, strings.NewReader(wakeEv), &out, &errOut)
	if err != nil || code != 0 || errOut.Len() != 0 {
		t.Fatalf("reserved wake hook failed: code=%d err=%v errOut=%q", code, err, errOut.String())
	}
	if !renewed || len(observedReports) != 1 || !strings.Contains(out.String(), "[INBOX_RECALL_GRANT]") || strings.Contains(out.String(), `"hookSpecificOutput": `) {
		t.Fatalf("expected renewed=true, 1 observed report, and compact JSON output, got renewed=%v reports=%d out=%q", renewed, len(observedReports), out.String())
	}

	// Now test bridge refusal after claim: should settle the unjoined Claude channel wake and exit 2 with NATIVE_PROMPT_REFUSED.
	root2 := t.TempDir()
	cli2, _ := newTestHookCLI(t, root2)
	stateDir2 := filepath.Join(root2, "state")
	if err := os.MkdirAll(stateDir2, 0o700); err != nil {
		t.Fatal(err)
	}
	coordMap["state_dir"] = stateDir2
	coordBytes2, _ := json.Marshal(coordMap)
	cfgPath2 := filepath.Join(root2, "coord.json")
	if err := os.WriteFile(cfgPath2, coordBytes2, 0o600); err != nil {
		t.Fatal(err)
	}
	coordSum2 := sha256.Sum256(coordBytes2)
	manifestMap["coordination_configs"] = []any{map[string]any{"path": cfgPath2, "sha256": hex.EncodeToString(coordSum2[:])}}
	manifestBytes2, _ := json.Marshal(manifestMap)
	if err := os.WriteFile(manifestPath, manifestBytes2, 0o600); err != nil {
		t.Fatal(err)
	}
	cli2.Host.OwnerProcess = cli.Host.OwnerProcess
	cli2.Host.Services = cli.Host.Services
	cli2.Engine.Hooks.GrantEvidence = func(_ context.Context, _, _, _ *value.Value) (string, *value.Value, error) {
		return "absent", nil, nil
	}
	var releasedOnRefusal bool
	cli2.Host.Call = func(ctx context.Context, cfg *value.Value, op string, body *value.Value, opts NativeCallOptions) (*value.Value, error) {
		if op == "session-inbox-reconcile" {
			releasedOnRefusal = true
		}
		return cli.Host.Call(ctx, cfg, op, body, opts)
	}
	cli2.Host.LoadInboxRecall = cli.Host.LoadInboxRecall
	cli2.Host.InboxRecallOutput = func(_ context.Context, _ InboxRecallRequest) (string, error) {
		return "", &InboxRecallError{Class: "BudgetRefused", Code: "TIMEOUT", Reason: "OUTPUT_LIMIT", Message: "budget exceeded", RecallPhase: "final_render"}
	}

	out.Reset()
	errOut.Reset()
	code, err = cli2.RunCLI(context.Background(), []string{"hook", "--config", cfgPath2}, strings.NewReader(wakeEv), &out, &errOut)
	if err != nil || code != 2 || out.Len() != 0 || !strings.Contains(errOut.String(), "NATIVE_PROMPT_REFUSED: bound inbox recall unavailable; no source or memory grant delivered; channel text rejected, owner prompt continues") {
		t.Fatalf("expected exit 2 NATIVE_PROMPT_REFUSED on bridge refusal, got code=%d err=%v out=%q errOut=%q", code, err, out.String(), errOut.String())
	}
	if !releasedOnRefusal {
		t.Fatalf("expected unjoined Claude channel wake with absent grant to be released on bridge refusal")
	}
	cfg2, err := cli2.Host.ReadConfig(cfgPath2)
	if err != nil {
		t.Fatal(err)
	}
	statePath2, err := cli2.Host.Locate(cfg2, nativeID)
	if err != nil {
		t.Fatal(err)
	}
	stateVal2, err := cli2.Host.Load(statePath2)
	if err != nil {
		t.Fatal(err)
	}
	diag := stateVal2.Get("wake_refusal_diagnostic")
	if diag == nil || !value.SameText(diag.Get("origin"), "hook") || !value.SameText(diag.Get("cause_code"), "TIMEOUT") || !value.SameText(diag.Get("cause_reason"), "OUTPUT_LIMIT") {
		t.Fatalf("expected wake_refusal_diagnostic with origin=hook, cause_code=TIMEOUT, cause_reason=OUTPUT_LIMIT, got %+v", diag)
	}
}

func TestHookDifferentialParityWithPython(t *testing.T) {
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available")
	}
	pyScript, err := filepath.Abs(filepath.Join("..", "..", "..", "integrations", "lifecycle", "coordination.py"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(pyScript); err != nil {
		t.Skipf("coordination.py not found at %s", pyScript)
	}

	root := t.TempDir()
	if err := os.Chmod(root, 0o700); err != nil {
		t.Fatal(err)
	}
	fakeCairn := filepath.Join(root, "fake-cairn")
	agentID := "301e246e-a3b7-498b-b6a7-3d84e592097f"
	execID := "da3e0447-4f10-40f7-b64d-6b66de691ef2"
	nativeID := "22b421c2-2298-41e9-8b0e-659ea2a72ed0"

	script := fmt.Sprintf(`#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
raw = sys.stdin.read().strip()
body = json.loads(raw) if raw else {}
if "agent-register" in args or "agent-heartbeat" in args or "agent-context" in args:
    meta = body.get("metadata") or {"harness":"claude","project":"cairn","workspace":%q,"delivery_mode":"existing-session","state":"busy"}
    print(json.dumps({"schema":"cairn.response/1","ok":True,"data":{
        "agent_id":%q,"execution_id":%q,"display_name":"agent-1268","inbox":"agent/"+%q,
        "native_session_id":%q,"context_revision":1,"metadata":meta
    }}))
elif "session-inbox-claim" in args:
    print(json.dumps({"schema":"cairn.response/1","ok":True,"data":{"attempt":None}}))
elif "session-inbox-pending" in args:
    print(json.dumps({"schema":"cairn.response/1","ok":True,"data":{"requests":2,"notices":1,"responses":0,"latest_position":9,"truncated":False}}))
else:
    print(json.dumps({"schema":"cairn.response/1","ok":True,"data":{}}))
`, root, agentID, execID, agentID, nativeID)
	if err := os.WriteFile(fakeCairn, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}

	pyState := filepath.Join(root, "py-state")
	goState := filepath.Join(root, "go-state")
	if err := os.MkdirAll(pyState, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(goState, 0o700); err != nil {
		t.Fatal(err)
	}
	pyCfg := filepath.Join(root, "py-coord.json")
	goCfg := filepath.Join(root, "go-coord.json")
	hostPID := os.Getppid()
	exePath, err := os.Readlink(fmt.Sprintf("/proc/%d/exe", hostPID))
	if err != nil {
		t.Fatal(err)
	}
	exeResolved, err := filepath.EvalSymlinks(exePath)
	if err != nil {
		exeResolved = exePath
	}
	commBytes, _ := os.ReadFile(fmt.Sprintf("/proc/%d/comm", hostPID))
	selfComm := strings.TrimSpace(string(commBytes))
	writeCfg := func(p, sd string) {
		b, _ := json.Marshal(map[string]any{
			"binding":          "b1",
			"harness":          "claude",
			"repo":             root,
			"state_dir":        sd,
			"cairn":            fakeCairn,
			"socket":           filepath.Join(root, "api.sock"),
			"token_file":       filepath.Join(root, "token"),
			"native_delivery":  true,
			"process_names":    []string{selfComm, filepath.Base(exeResolved), "python3"},
			"process_exe_dirs": []string{filepath.Dir(exeResolved), "/usr/bin", "/bin"},
		})
		if err := os.WriteFile(p, b, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	writeCfg(pyCfg, pyState)
	writeCfg(goCfg, goState)

	cli, _ := newTestHookCLI(t, root)
	cli.Command = []string{"/usr/bin/python3", pyScript}

	events := []string{
		fmt.Sprintf(`{"hook_event_name":"SessionStart","session_id":%q,"cwd":%q,"host_pid":%d}`, nativeID, root, hostPID),
		fmt.Sprintf(`{"hook_event_name":"UserPromptSubmit","session_id":%q,"cwd":%q,"prompt":"hello","host_pid":%d}`, nativeID, root, hostPID),
		fmt.Sprintf(`{"hook_event_name":"PostToolUse","session_id":%q,"cwd":%q,"host_pid":%d}`, nativeID, root, hostPID),
		fmt.Sprintf(`{"hook_event_name":"PostToolUse","session_id":%q,"cwd":%q,"host_pid":%d}`, nativeID, root, hostPID),
		fmt.Sprintf(`{"hook_event_name":"Stop","session_id":%q,"cwd":%q,"host_pid":%d}`, nativeID, root, hostPID),
		fmt.Sprintf(`{"hook_event_name":"SessionEnd","session_id":%q,"cwd":%q,"host_pid":%d}`, nativeID, root, hostPID),
	}
	for i, ev := range events {
		pyCmd := exec.Command("/usr/bin/python3", pyScript, "hook", "--config", pyCfg)
		pyCmd.Stdin = strings.NewReader(ev)
		pyCmd.Env = append(os.Environ(), "HOME="+root, "CAIRN_INBOX_ROOT="+filepath.Join(root, "py-journal"))
		var pyOut, pyErr bytes.Buffer
		pyCmd.Stdout = &pyOut
		pyCmd.Stderr = &pyErr
		if err := pyCmd.Run(); err != nil {
			t.Fatalf("step %d python failed: %v stderr=%s", i, err, pyErr.String())
		}

		var goOut, goErr bytes.Buffer
		cli.LookupEnv = func(k string) (string, bool) {
			if k == "HOME" {
				return root, true
			}
			return os.LookupEnv(k)
		}
		code, err := cli.RunCLI(context.Background(), []string{"hook", "--config", goCfg}, strings.NewReader(ev), &goOut, &goErr)
		if err != nil || code != 0 || goErr.String() != pyErr.String() {
			t.Fatalf("step %d go failed: code=%d err=%v goErr=%q pyErr=%q", i, code, err, goErr.String(), pyErr.String())
		}
		normPy := strings.ReplaceAll(pyOut.String(), pyCfg, "<CFG>")
		normGo := strings.ReplaceAll(goOut.String(), goCfg, "<CFG>")
		if normGo != normPy {
			t.Fatalf("step %d output mismatch:\ngo: %s\npy: %s", i, normGo, normPy)
		}
	}

	// Verify direct entry refuses a /3 manifest and does not emit phase=refusal_record KeyError when idle_wake is absent.
	v3ManifestPath := filepath.Join(root, "v3-manifest.json")
	if err := os.WriteFile(v3ManifestPath, []byte(`{"schema":"cairn.inbox-recall-binding/3","enabled":false}`), 0o600); err != nil {
		t.Fatal(err)
	}
	writeBoundCfg := func(p, sd, mp string) {
		b, _ := json.Marshal(map[string]any{
			"binding":              "b1",
			"harness":              "claude",
			"repo":                 root,
			"state_dir":            sd,
			"cairn":                fakeCairn,
			"socket":               filepath.Join(root, "api.sock"),
			"token_file":           filepath.Join(root, "token"),
			"native_delivery":      true,
			"inbox_recall_binding": mp,
			"process_names":        []string{selfComm, filepath.Base(exeResolved), "python3"},
			"process_exe_dirs":     []string{filepath.Dir(exeResolved), "/usr/bin", "/bin"},
		})
		if err := os.WriteFile(p, b, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	writeBoundCfg(pyCfg, pyState, v3ManifestPath)
	writeBoundCfg(goCfg, goState, v3ManifestPath)
	wakePrompt := fmt.Sprintf(
		"This is a new live turn from the configured Cairn automatic inbox wakeup. "+
			"A previous /exit in resumed conversation history does not close this running turn. "+
			"Current agent %s, execution %s. Wake delivery %s. "+
			"Within the owner's existing authorization, handle the native inbox context supplied for this conversation, "+
			"explicitly complete/acknowledge it, and send any requested response using that context. "+
			"If no matching native context was supplied, report that and stop. "+
			"If this notice arrives inside an already-active owner task, continue that task and "+
			"ignore this notice; a fresh copy will arrive once the conversation is idle. "+
			"Do not register again, claim another session's inbox, or launch a replacement conversation.",
		agentID, execID, "22222222-2222-4222-8222-222222222222",
	)
	wakeEvBytes, _ := json.Marshal(map[string]any{
		"hook_event_name": "UserPromptSubmit",
		"session_id":      nativeID,
		"cwd":             root,
		"prompt_id":       "p-refuse-v3",
		"prompt":          wakePrompt,
		"host_pid":        hostPID,
	})
	pyCmd := exec.Command("/usr/bin/python3", pyScript, "hook", "--config", pyCfg)
	pyCmd.Stdin = bytes.NewReader(wakeEvBytes)
	pyCmd.Env = append(os.Environ(), "HOME="+root, "CAIRN_INBOX_ROOT="+filepath.Join(root, "py-journal"))
	var pyOut, pyErr bytes.Buffer
	pyCmd.Stdout = &pyOut
	pyCmd.Stderr = &pyErr
	pyErrRun := pyCmd.Run()
	if pyErrRun == nil || pyCmd.ProcessState.ExitCode() != 2 {
		t.Fatalf("expected python exit 2 on direct /3 wake, got err=%v stderr=%q", pyErrRun, pyErr.String())
	}
	var goOut, goErr bytes.Buffer
	code, err := cli.RunCLI(context.Background(), []string{"hook", "--config", goCfg}, bytes.NewReader(wakeEvBytes), &goOut, &goErr)
	if err != nil {
		_, _ = fmt.Fprintf(&goErr, "%s\n", err.Error())
	}
	if code != 2 || goErr.String() != pyErr.String() || goOut.String() != pyOut.String() {
		t.Fatalf("direct /3 wake refusal mismatch: code=%d goErr=%q pyErr=%q", code, goErr.String(), pyErr.String())
	}
}

func TestHookCompatibilityInboxRecallAndReportFlush(t *testing.T) {
	root := t.TempDir()
	if err := os.Chmod(root, 0o700); err != nil {
		t.Fatal(err)
	}
	cli, _ := newTestHookCLI(t, root)
	stateDir := filepath.Join(root, "state")
	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		t.Fatal(err)
	}
	memStateDir := filepath.Join(root, "mem-state")
	if err := os.MkdirAll(memStateDir, 0o700); err != nil {
		t.Fatal(err)
	}
	cfgPath := filepath.Join(root, "coord.json")
	memCfgPath := filepath.Join(root, "mem.json")
	manifestPath := filepath.Join(root, "manifest.json")
	receiptPath := filepath.Join(root, "receipt.json")
	binPath := filepath.Join(root, "cairn-lifecycle")
	if err := os.WriteFile(binPath, []byte("#!/bin/sh\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	shaHex := func(b []byte) string { s := sha256.Sum256(b); return hex.EncodeToString(s[:]) }
	coordMap := map[string]any{
		"binding":              "b1",
		"harness":              "claude",
		"repo":                 root,
		"state_dir":            stateDir,
		"cairn":                "/bin/true",
		"socket":               filepath.Join(root, "api.sock"),
		"token_file":           filepath.Join(root, "token"),
		"native_delivery":      true,
		"inbox_recall_binding": manifestPath,
	}
	memMap := map[string]any{
		"binding":              "b1",
		"harness":              "claude",
		"repo":                 root,
		"state_dir":            memStateDir,
		"cairn":                "/bin/true",
		"socket":               filepath.Join(root, "api.sock"),
		"token_file":           filepath.Join(root, "token"),
		"recall_mode":          "agent_tools",
		"context_bytes":        4000,
		"inbox_recall_binding": manifestPath,
	}
	coordBytes, _ := json.Marshal(coordMap)
	memBytes, _ := json.Marshal(memMap)
	_ = os.WriteFile(cfgPath, coordBytes, 0o600)
	_ = os.WriteFile(memCfgPath, memBytes, 0o600)
	binRaw, _ := os.ReadFile(binPath)
	binDesc := map[string]string{"path": binPath, "sha256": shaHex(binRaw)}
	memDesc := map[string]string{"path": memCfgPath, "sha256": shaHex(memBytes)}
	coordDesc := []any{map[string]string{"path": cfgPath, "sha256": shaHex(coordBytes)}}
	manifestBytes, _ := json.Marshal(map[string]any{
		"schema":               "cairn.inbox-recall-binding/3",
		"enabled":              true,
		"binary":               binDesc,
		"memory_config":        memDesc,
		"coordination_configs": coordDesc,
	})
	_ = os.WriteFile(manifestPath, manifestBytes, 0o600)
	hashes := map[string]string{}
	members := map[string]any{}
	for _, m := range CompatibilityClosureMembers {
		raw := []byte("# " + m + "\n")
		hashes[m] = shaHex(raw)
		members[m] = map[string]any{"sha256": hashes[m], "bytes": len(raw)}
	}
	snapRoot := filepath.Join(root, CompatibilitySnapshotIdentity(hashes))
	_ = os.MkdirAll(snapRoot, 0o700)
	for _, m := range CompatibilityClosureMembers {
		_ = os.WriteFile(filepath.Join(snapRoot, m), []byte("# "+m+"\n"), 0o600)
	}
	receiptBytes, _ := json.Marshal(map[string]any{
		"schema": "cairn.lifecycle-compatibility/1", "source_revision": "rev-1", "snapshot_root": snapRoot,
		"members": members, "bootstrap": "lifecycle_compatibility.py", "loader": "lifecycle_bundle.py",
		"roles":                map[string]string{"memory": "memory.py", "coordination": "coordination.py", "inbox_recall": "inbox_recall.py", "inbox_binding": "inbox_binding.py"},
		"aliases":              map[string]string{},
		"binary":               binDesc,
		"binding":              map[string]string{"path": manifestPath, "sha256": shaHex(manifestBytes), "schema": "cairn.inbox-recall-binding/3"},
		"memory_config":        memDesc,
		"coordination_configs": coordDesc,
	})
	_ = os.WriteFile(receiptPath, receiptBytes, 0o600)

	agentID := "301e246e-a3b7-498b-b6a7-3d84e592097f"
	execID := "da3e0447-4f10-40f7-b64d-6b66de691ef2"
	nativeID := "22b421c2-2298-41e9-8b0e-659ea2a72ed0"
	deliveryID := "22222222-2222-4222-8222-222222222222"
	leaseID := "33333333-3333-4333-8333-333333333333"
	recordID := "44444444-4444-4444-8444-444444444444"
	currentAgent := nativeMap(
		"agent_id", agentID, "execution_id", execID, "display_name", "agent-1268",
		"inbox", "agent/"+agentID, "native_session_id", nativeID, "context_revision", nativeInt64(1),
		"metadata", nativeMap("harness", "claude", "project", "cairn", "workspace", root, "delivery_mode", "existing-session", "state", "idle"),
	)
	cli.Host.OwnerProcess = func(_, _ *value.Value) (*value.Value, error) {
		return nativeMap("pid", 1234, "start", 5678, "state", "S", "parent", 1, "boot", "boot-1"), nil
	}
	cli.Engine.Hooks.GrantEvidence = func(_ context.Context, _, _, _ *value.Value) (string, *value.Value, error) {
		return "absent", nil, nil
	}
	var reconciled int
	cli.Host.Call = func(_ context.Context, _ *value.Value, op string, body *value.Value, _ NativeCallOptions) (*value.Value, error) {
		switch op {
		case "agent-register", "agent-heartbeat", "agent-context":
			return currentAgent, nil
		case "session-inbox-claim":
			return nativeMap("attempt", nativeMap(
				"attempt_id", body.Get("request_id"),
				"session", nativeMap("agent_id", agentID, "execution_id", execID),
				"native_turn_id", body.Get("native_turn_id"),
				"delivery", nativeMap("delivery_id", deliveryID, "lease_id", leaseID, "state", "leased", "event", nativeMap("event_id", deliveryID, "kind", "request", "from", "agent/sender", "ref", nativeMap("record_id", recordID, "version", 1))),
			)), nil
		case "event-renew":
			return nativeMap("delivery_id", deliveryID, "lease_id", leaseID, "state", "leased", "event", nativeMap("event_id", deliveryID, "kind", "request", "from", "agent/sender", "ref", nativeMap("record_id", recordID, "version", 1))), nil
		case "session-inbox-reconcile":
			if value.SameText(body.Get("reason"), "delivery_completed") {
				return nil, &CoordinationError{Code: "DELIVERY_ACTIVE", Message: "delivery is still active"}
			}
			reconciled++
			return nativeMap("released", true), nil
		default:
			t.Fatalf("unexpected call op: %s", op)
			return nil, nil
		}
	}

	var childCalls [][]string
	var lockFreeOnRecall, lockFreeOnReport bool
	recallReply := []byte(`{"ok":true,"message":"RECALL_OK","trace_report":{"status":"ok"},"meter_report":{"outcome":"delivered"}}` + "\n")
	reportReply := []byte(`{"ok":true,"trace_status":"written","meter_status":"recorded"}` + "\n")
	reportCode := 0
	launchRoot := filepath.Join(root, "launchers")
	vector := []string{"/usr/bin/python3", "-I", "-S", "-c", "VERIFIER", "--receipt", receiptPath}
	lBytes, _ := LauncherBytes(vector, "/bin/sh")
	launcherDir := filepath.Join(launchRoot, shaHex(lBytes))
	_ = os.MkdirAll(launcherDir, 0o700)
	launcherPath := filepath.Join(launcherDir, "inbox")
	_ = os.WriteFile(launcherPath, lBytes, 0o500)
	cli.Compatibility = &CompatibilityContext{
		Role:          "coordination",
		Entrypoint:    "coordination.py",
		ConfigPath:    cfgPath,
		HookTimeout:   15,
		ReceiptPath:   receiptPath,
		ReceiptSHA256: shaHex(receiptBytes),
		LaunchRoot:    launchRoot,
		Shell:         "/bin/sh",
		Vector:        vector,
		Launcher:      launcherPath,
		LauncherRaw:   lBytes,
		Deadline:      float64(cli.clock().Unix()) + 30,
		Profile:       cli.Profile,
		ActivationFunc: func(_ []byte) (bool, error) {
			return true, nil
		},
		Child: func(argv []string, _ []byte, _ int, _ float64) ([]byte, []byte, int, error) {
			childCalls = append(childCalls, append([]string(nil), argv...))
			sp, _ := cli.Host.Locate(cli.Compatibility.current, nativeID)
			l, lErr := AcquireSessionLock(sp)
			free := lErr == nil
			if l != nil {
				_ = l.Release()
			}
			if len(argv) >= 4 && argv[3] == "recall" {
				lockFreeOnRecall = free
				return recallReply, nil, 0, nil
			}
			lockFreeOnReport = free
			return reportReply, nil, reportCode, nil
		},
	}
	wakeBody := fmt.Sprintf(
		"This is a new live turn from the configured Cairn automatic inbox wakeup. "+
			"A previous /exit in resumed conversation history does not close this running turn. "+
			"Current agent %s, execution %s. Wake delivery %s. "+
			"Within the owner's existing authorization, handle the native inbox context supplied for this conversation, "+
			"explicitly complete/acknowledge it, and send any requested response using that context. "+
			"If no matching native context was supplied, report that and stop. "+
			"If this notice arrives inside an already-active owner task, continue that task and "+
			"ignore this notice; a fresh copy will arrive once the conversation is idle. "+
			"Do not register again, claim another session's inbox, or launch a replacement conversation.",
		agentID, execID, deliveryID,
	)
	wakePrompt := fmt.Sprintf("<channel source=\"cairn-events\" native_session_id=%q agent_id=%q execution_id=%q delivery_id=%q>\n%s\n</channel>", nativeID, agentID, execID, deliveryID, wakeBody)
	wakeEv := fmt.Sprintf(`{"hook_event_name":"UserPromptSubmit","session_id":%q,"cwd":%q,"prompt_id":"p-1","prompt":%q}`, nativeID, root, wakePrompt)

	var out, errOut bytes.Buffer
	code, err := cli.RunCLI(context.Background(), []string{"hook", "--config", cfgPath}, strings.NewReader(wakeEv), &out, &errOut)
	if err != nil || code != 0 || errOut.Len() != 0 || !strings.Contains(out.String(), `"additionalContext":"RECALL_OK"`) {
		t.Fatalf("expected /3 recall success, got code=%d err=%v out=%q err=%q", code, err, out.String(), errOut.String())
	}
	if len(childCalls) != 2 || childCalls[0][3] != "recall" || childCalls[1][3] != "report" || childCalls[0][len(childCalls[0])-1] != childCalls[1][len(childCalls[1])-1] {
		t.Fatalf("expected recall then report with matching deadline, got %v", childCalls)
	}
	if lockFreeOnRecall || !lockFreeOnReport {
		t.Fatalf("expected session lock held during recall (free=%v) and released before report (free=%v)", lockFreeOnRecall, lockFreeOnReport)
	}

	// Report failure isolation: non-zero report exit must not fail an already-delivered wake.
	sp, _ := cli.Host.Locate(cli.Compatibility.current, nativeID)
	_ = os.Remove(sp)
	cli.Compatibility.enabled = nil
	childCalls = nil
	reportCode = 1
	out.Reset()
	errOut.Reset()
	code, err = cli.RunCLI(context.Background(), []string{"hook", "--config", cfgPath}, strings.NewReader(wakeEv), &out, &errOut)
	if err != nil || code != 0 || !strings.Contains(out.String(), `"additionalContext":"RECALL_OK"`) {
		t.Fatalf("expected report failure isolation, got code=%d err=%v out=%q err=%q", code, err, out.String(), errOut.String())
	}
	reportCode = 0

	// Required-search failure with cairn_recall_failure + post-lock trace report + Claude settlement.
	seedIdleWake := func() {
		st := nativeMap(
			"agent", currentAgent,
			"process", nativeMap("pid", 1234, "start", 5678, "state", "S", "parent", 1, "boot", "boot-1"),
			"workspace", root,
			"idle_wake", nativeMap(
				"transport", "claude-channel",
				"status", "submitted",
				"session", nativeMap("agent_id", agentID, "execution_id", execID),
				"native_id", nativeID,
				"delivery_id", deliveryID,
				"event_id", deliveryID,
				"request_id", "11111111-2222-4333-8444-555555555555",
			),
		)
		if err := cli.Host.Persist(sp, st); err != nil {
			t.Fatal(err)
		}
	}
	seedIdleWake()
	cli.Compatibility.enabled = nil
	childCalls = nil
	reconciled = 0
	recallReply = []byte(`{"ok":false,"error":{"class":"ValueError","message":"required search failed","cairn_inbox_refusal":{"refusal_stage":"required_search","refusal_reason":"hosted_destination"},"cairn_recall_failure":{"recall_phase":"required_search","recall_operation":"search","recall_attempts":1,"recall_max_attempts":2,"deadline_remaining_ms":4200}},"trace_report":{"status":"error"}}` + "\n")
	out.Reset()
	errOut.Reset()
	code, err = cli.RunCLI(context.Background(), []string{"hook", "--config", cfgPath}, strings.NewReader(wakeEv), &out, &errOut)
	stVal, _ := cli.Host.Load(sp)
	diag := stVal.Get("wake_refusal_diagnostic")
	if err != nil || code != 2 || reconciled != 1 || len(childCalls) != 2 || childCalls[1][3] != "report" || !lockFreeOnReport {
		t.Fatalf("expected exit 2 settled refusal with post-lock report, got code=%d reconciled=%d calls=%v diag=%+v", code, reconciled, childCalls, diag)
	}
	if !value.SameText(diag.Get("settlement"), "turn_ended") || !value.SameText(diag.Get("error_class"), "ValueError") || !value.SameText(diag.Get("refusal_stage"), "required_search") || !value.SameText(diag.Get("refusal_reason"), "hosted_destination") || !value.SameText(diag.Get("recall_phase"), "required_search") || !value.SameText(diag.Get("recall_operation"), "search") || diag.Get("recall_attempts").Integer != "1" || diag.Get("recall_max_attempts").Integer != "2" || diag.Get("deadline_remaining_ms").Integer != "4200" {
		t.Fatalf("unexpected wake_refusal_diagnostic: %+v", diag)
	}

	// Budget refusal preserves configured_budget_bytes and budget_bytes.
	seedIdleWake()
	cli.Compatibility.enabled = nil
	recallReply = []byte(`{"ok":false,"error":{"class":"ValueError","message":"budget exceeded","cairn_inbox_refusal":{"refusal_stage":"required_render","refusal_reason":"required_context","configured_budget_bytes":1000,"budget_bytes":1000}}}` + "\n")
	out.Reset()
	errOut.Reset()
	code, err = cli.RunCLI(context.Background(), []string{"hook", "--config", cfgPath}, strings.NewReader(wakeEv), &out, &errOut)
	if err != nil || code != 2 {
		t.Fatalf("expected exit 2 on budget refusal, got code=%d err=%v", code, err)
	}
	stVal, _ = cli.Host.Load(sp)
	diag = stVal.Get("wake_refusal_diagnostic")
	if !value.SameText(diag.Get("refusal_stage"), "required_render") || !value.SameText(diag.Get("refusal_reason"), "required_context") || diag.Get("configured_budget_bytes").Integer != "1000" || diag.Get("budget_bytes").Integer != "1000" {
		t.Fatalf("unexpected budget refusal diagnostic: %+v", diag)
	}

	// Stale lease (CoordinationError), BlockingIOError, and malformed child reply (SubprocessError -> OtherError).
	for _, tc := range []struct {
		reply     string
		wantClass string
		wantStage string
	}{
		{`{"ok":false,"error":{"class":"CoordinationError","code":"STALE_LEASE","message":"expired","cairn_inbox_refusal":{"refusal_stage":"lease_revalidation","refusal_reason":"unknown"}}}` + "\n", "CoordinationError", "lease_revalidation"},
		{`{"ok":false,"error":{"class":"BlockingIOError","errno":11,"message":"busy","cairn_inbox_refusal":{"refusal_stage":"inbox_ledger","refusal_reason":"unknown"}}}` + "\n", "BlockingIOError", "inbox_ledger"},
		{`{"ok":true}` + "\n", "OtherError", "bridge"},
		{`{"ok":false,"error":{"class":"UnknownError","message":"x","cairn_inbox_refusal":{"refusal_stage":"bridge","refusal_reason":"unknown"}}}` + "\n", "OtherError", "bridge"},
	} {
		seedIdleWake()
		cli.Compatibility.enabled = nil
		recallReply = []byte(tc.reply)
		out.Reset()
		errOut.Reset()
		code, err = cli.RunCLI(context.Background(), []string{"hook", "--config", cfgPath}, strings.NewReader(wakeEv), &out, &errOut)
		if err != nil || code != 2 {
			t.Fatalf("expected exit 2 for %s, got code=%d err=%v", tc.wantClass, code, err)
		}
		stVal, _ = cli.Host.Load(sp)
		diag = stVal.Get("wake_refusal_diagnostic")
		if !value.SameText(diag.Get("error_class"), tc.wantClass) || !value.SameText(diag.Get("refusal_stage"), tc.wantStage) || !value.SameText(diag.Get("refusal_reason"), "unknown") {
			t.Fatalf("unexpected diagnostic for %s: %+v", tc.wantClass, diag)
		}
	}
}
