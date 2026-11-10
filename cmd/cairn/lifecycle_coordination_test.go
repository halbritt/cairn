package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/lifecycle/binding"
	"github.com/halbritt/cairn/internal/lifecycle/coordination"
	"github.com/halbritt/cairn/internal/lifecycle/transcript"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func TestLifecycleCoordinationPublicRegistrationClosed(t *testing.T) {
	for _, args := range [][]string{
		{"lifecycle", "coordination"},
		{"lifecycle", "coordination", "inbox"},
		{"lifecycle", "coordination", "inbox", "drain"},
		{"lifecycle", "coordination", "inbox", "compact"},
		{"lifecycle", "coordination", "inspect-held-refusal"},
	} {
		t.Run(strings.Join(args, "_"), func(t *testing.T) {
			t.Setenv("CAIRN_LIFECYCLE_ENABLED", "1")
			_, err := run(context.Background(), args, lifecyclePanicReader{})
			if core.Code(err) != "LIFECYCLE_DISABLED" {
				t.Fatalf("disabled coordination registration: %v", err)
			}
			if _, code := commandEnvelope(nil, err); code == 0 {
				t.Fatal("disabled coordination registration reported success")
			}
		})
	}
}

func TestLifecycleCoordinationPlanDispatch(t *testing.T) {
	res, err := executeLifecycle(context.Background(), []string{"coordination", "inbox", "--help"}, strings.NewReader(""))
	if err != nil {
		t.Fatalf("executeLifecycle coordination: %v", err)
	}
	plan, ok := res.(lifecycleCoordinationPlan)
	if !ok {
		t.Fatalf("expected lifecycleCoordinationPlan, got %T", res)
	}
	var out, errOut bytes.Buffer
	code, err := plan.execute(context.Background(), &out, &errOut)
	if err != nil || code != 0 || !strings.Contains(out.String(), "usage: coordination.py inbox") || errOut.Len() != 0 {
		t.Fatalf("plan.execute(--help): code=%d out=%q errOut=%q err=%v", code, out.String(), errOut.String(), err)
	}
}

func TestLifecycleCoordinationClassAPythonGoDifferential(t *testing.T) {
	if os.Getenv("HOME") != "/tmp/home" {
		t.Fatalf("refusing to run outside campaign isolation sandbox (HOME=%q)", os.Getenv("HOME"))
	}
	scriptPath, err := filepath.Abs(filepath.Join("..", "..", "integrations", "lifecycle", "coordination.py"))
	if err != nil {
		t.Fatalf("resolve coordination.py: %v", err)
	}
	base := t.TempDir()
	if err := os.Chmod(base, 0o700); err != nil {
		t.Fatal(err)
	}
	fakeBin := filepath.Join(base, "fake-cairn")
	fakeScript := "#!/bin/sh\nfor arg in \"$@\"; do\n  case \"$arg\" in\n    ack|session-inbox-turn-ended)\n      printf '{\"schema\":\"cairn.response/1\",\"ok\":true,\"data\":{\"state\":\"handled\",\"attempts\":1}}\\n'\n      exit 0\n      ;;\n    session-inbox-claim|event-next)\n      printf '{\"schema\":\"cairn.response/1\",\"ok\":true,\"data\":{\"delivery\":null}}\\n'\n      exit 0\n      ;;\n  esac\ndone\nprintf '{\"schema\":\"cairn.response/1\",\"ok\":true,\"data\":{}}\\n'\n"
	if err := os.WriteFile(fakeBin, []byte(fakeScript), 0o700); err != nil {
		t.Fatalf("write fake-cairn: %v", err)
	}
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	rawSelf, err := os.ReadFile(self)
	if err != nil {
		t.Fatal(err)
	}
	boundBin := filepath.Join(base, "bound-cairn")
	if err := os.Link(self, boundBin); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(boundBin, 0o500); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(rawSelf)
	pyState, goState := filepath.Join(base, "py-state"), filepath.Join(base, "go-state")
	pyCtx, pyCfg := setupLifecycleCoordFixture(t, pyState, fakeBin, boundBin, hex.EncodeToString(sum[:]), false, true)
	goCtx, goCfg := setupLifecycleCoordFixture(t, goState, fakeBin, boundBin, hex.EncodeToString(sum[:]), false, false)
	_, pyHeldCfg := setupLifecycleCoordFixture(t, filepath.Join(base, "py-held"), fakeBin, boundBin, hex.EncodeToString(sum[:]), true, true)
	_, goHeldCfg := setupLifecycleCoordFixture(t, filepath.Join(base, "go-held"), fakeBin, boundBin, hex.EncodeToString(sum[:]), true, false)

	cases := []struct {
		name   string
		pyArgs []string
		goArgs []string
		pyDir  string
		goDir  string
	}{
		{
			name:   "inbox_ack",
			pyArgs: []string{"inbox", "--context", pyCtx, "ack"},
			goArgs: []string{"inbox", "--context", goCtx, "ack"},
			pyDir:  pyState,
			goDir:  goState,
		},
		{
			name:   "inbox_drain_live_session",
			pyArgs: []string{"inbox", "drain", "--config", pyCfg, "--agent-id", "301e246e-a3b7-498b-b6a7-3d84e592097f", "--execution-id", "da3e0447-4f10-40f7-b64d-6b66de691ef2"},
			goArgs: []string{"inbox", "drain", "--config", goCfg, "--agent-id", "301e246e-a3b7-498b-b6a7-3d84e592097f", "--execution-id", "da3e0447-4f10-40f7-b64d-6b66de691ef2"},
			pyDir:  pyState,
			goDir:  goState,
		},
		{
			name:   "inspect_held_refusal",
			pyArgs: []string{"inspect-held-refusal", "--config", pyHeldCfg, "--native-session-id", "301e246e-a3b7-498b-b6a7-3d84e592097f"},
			goArgs: []string{"inspect-held-refusal", "--config", goHeldCfg, "--native-session-id", "301e246e-a3b7-498b-b6a7-3d84e592097f"},
			pyDir:  filepath.Join(base, "py-held"),
			goDir:  filepath.Join(base, "go-held"),
		},
		{
			name:   "recover_refused_wake",
			pyArgs: []string{"recover-refused-wake", "--config", pyHeldCfg, "--native-session-id", "301e246e-a3b7-498b-b6a7-3d84e592097f", "--attempt-id", "att-1", "--native-turn-id", "claude-channel:1"},
			goArgs: []string{"recover-refused-wake", "--config", goHeldCfg, "--native-session-id", "301e246e-a3b7-498b-b6a7-3d84e592097f", "--attempt-id", "att-1", "--native-turn-id", "claude-channel:1"},
			pyDir:  filepath.Join(base, "py-held"),
			goDir:  filepath.Join(base, "go-held"),
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			assertPythonGoLifecycleCoordParity(t, scriptPath, tc.pyArgs, tc.goArgs, tc.pyDir, tc.goDir, 0)
		})
	}
}

func setupLifecycleCoordFixture(t *testing.T, stateDir, fakeBin, boundBin, shaHex string, held, isPython bool) (string, string) {
	t.Helper()
	memStateDir := filepath.Join(stateDir, "mem-state")
	for _, sub := range []string{"", "inbox", "intents", "mem-state"} {
		dir := filepath.Join(stateDir, sub)
		if err := os.MkdirAll(dir, 0o700); err != nil {
			t.Fatalf("mkdir %s: %v", dir, err)
		}
		if err := os.Chmod(dir, 0o700); err != nil {
			t.Fatalf("chmod %s: %v", dir, err)
		}
	}
	tokenPath := filepath.Join(stateDir, "token")
	if err := os.WriteFile(tokenPath, []byte("test-token\n"), 0o600); err != nil {
		t.Fatalf("write token: %v", err)
	}
	initPath := filepath.Join(memStateDir, "301e246e-a3b7-498b-b6a7-3d84e592097f.inbox-recall.initialized")
	if err := os.WriteFile(initPath, []byte(`{"schema":"cairn.inbox-recall-initialized/1","session":"301e246e-a3b7-498b-b6a7-3d84e592097f"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	ledgerPath := filepath.Join(memStateDir, "301e246e-a3b7-498b-b6a7-3d84e592097f.inbox-recall.json")
	if err := os.WriteFile(ledgerPath, []byte(`{"schema":"cairn.inbox-recall-grants/1","session":"301e246e-a3b7-498b-b6a7-3d84e592097f","grants":{}}`), 0o600); err != nil {
		t.Fatal(err)
	}
	manifestPath := filepath.Join(stateDir, "binding.json")
	memPath := filepath.Join(stateDir, "memory.json")
	memDoc := map[string]any{
		"recall_mode":          "agent_tools",
		"harness":              "claude",
		"cairn":                fakeBin,
		"socket":               filepath.Join(stateDir, "api.sock"),
		"token_file":           tokenPath,
		"state_dir":            memStateDir,
		"repo":                 "/tmp/test-repo",
		"context_bytes":        4000,
		"inbox_recall_binding": manifestPath,
	}
	rawMem, _ := json.Marshal(memDoc)
	if err := os.WriteFile(memPath, rawMem, 0o600); err != nil {
		t.Fatal(err)
	}
	memSum := sha256.Sum256(rawMem)
	cfgDoc := map[string]any{
		"cairn":                fakeBin,
		"socket":               filepath.Join(stateDir, "api.sock"),
		"token_file":           tokenPath,
		"state_dir":            stateDir,
		"harness":              "claude",
		"repo":                 "/tmp/test-repo",
		"binding":              "fixture",
		"native_delivery":      true,
		"inbox_recall_binding": manifestPath,
	}
	rawCfg, _ := json.Marshal(cfgDoc)
	cfgPath := filepath.Join(stateDir, "config.json")
	if err := os.WriteFile(cfgPath, rawCfg, 0o600); err != nil {
		t.Fatalf("write config: %v", err)
	}
	cfgSum := sha256.Sum256(rawCfg)
	manifestSchema := "cairn.inbox-recall-binding/3"
	if isPython {
		manifestSchema = "cairn.inbox-recall-binding/2"
	}
	lcDir, err := filepath.Abs(filepath.Join("..", "..", "integrations", "lifecycle"))
	if err != nil {
		t.Fatal(err)
	}
	bundleDir := filepath.Join(stateDir, "bundle")
	if err := os.MkdirAll(bundleDir, 0o700); err != nil {
		t.Fatal(err)
	}
	bundleFiles := map[string]string{}
	for _, m := range []string{
		"lifecycle_bundle.py", "memory.py", "coordination.py", "inbox_recall.py", "inbox_binding.py",
		"grant_ledger.py", "transcript_recovery.py", "unix_peer_transport.py",
		"wake_transport.py", "process_scan.py", "codex_queue.py", "claude_channel.py",
		"opencode_queue.py", "hermes_queue.py", "recall_dispatch.py", "hook_admission.py",
		"native_session.py",
	} {
		rawMod, err := os.ReadFile(filepath.Join(lcDir, m))
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(bundleDir, m), rawMod, 0o600); err != nil {
			t.Fatal(err)
		}
		ms := sha256.Sum256(rawMod)
		bundleFiles[m] = hex.EncodeToString(ms[:])
	}
	rawBundleFiles, _ := json.Marshal(bundleFiles)
	bundleSum := sha256.Sum256(rawBundleFiles)
	bundleDesc := map[string]any{
		"schema": "cairn.lifecycle-bundle/1",
		"sha256": hex.EncodeToString(bundleSum[:]),
		"files":  bundleFiles,
	}
	v3Doc := map[string]any{
		"schema":               manifestSchema,
		"enabled":              true,
		"binary":               map[string]any{"path": boundBin, "sha256": shaHex},
		"bridge":               map[string]any{"path": filepath.Join(bundleDir, "inbox_recall.py"), "sha256": bundleFiles["inbox_recall.py"], "bundle": bundleDesc},
		"engine":               map[string]any{"path": filepath.Join(bundleDir, "memory.py"), "sha256": bundleFiles["memory.py"], "bundle": bundleDesc},
		"memory_config":        map[string]any{"path": memPath, "sha256": hex.EncodeToString(memSum[:])},
		"coordination_configs": []any{map[string]any{"path": cfgPath, "sha256": hex.EncodeToString(cfgSum[:])}},
	}
	rawV3, _ := json.Marshal(v3Doc)
	if err := os.WriteFile(manifestPath, rawV3, 0o600); err != nil {
		t.Fatal(err)
	}
	statRaw, err := os.ReadFile("/proc/self/stat")
	if err != nil {
		t.Fatalf("read /proc/self/stat: %v", err)
	}
	afterComm := strings.Fields(string(statRaw)[strings.LastIndexByte(string(statRaw), ')')+2:])
	bootRaw, err := os.ReadFile("/proc/sys/kernel/random/boot_id")
	if err != nil {
		t.Fatalf("read boot_id: %v", err)
	}
	procMap := map[string]any{
		"pid":    os.Getpid(),
		"start":  mustAtoi64(t, afterComm[19]),
		"state":  afterComm[0],
		"parent": mustAtoi64(t, afterComm[1]),
		"boot":   strings.TrimSpace(string(bootRaw)),
	}
	sessionRef := map[string]any{
		"agent_id":     "301e246e-a3b7-498b-b6a7-3d84e592097f",
		"execution_id": "da3e0447-4f10-40f7-b64d-6b66de691ef2",
	}
	stateDoc := map[string]any{
		"native_session_id": "301e246e-a3b7-498b-b6a7-3d84e592097f",
		"agent": map[string]any{
			"agent_id":          "301e246e-a3b7-498b-b6a7-3d84e592097f",
			"execution_id":      "da3e0447-4f10-40f7-b64d-6b66de691ef2",
			"native_session_id": "301e246e-a3b7-498b-b6a7-3d84e592097f",
			"metadata":          map[string]any{"state": "idle"},
		},
		"process":       procMap,
		"active_prompt": map[string]any{},
	}
	if held {
		stateDoc["inbox_intent"] = map[string]any{
			"request_id":     "att-1",
			"session":        sessionRef,
			"native_turn_id": "claude-channel:1",
		}
		stateDoc["inbox_attempt"] = map[string]any{
			"attempt_id":     "att-1",
			"session":        sessionRef,
			"native_turn_id": "claude-channel:1",
			"delivery": map[string]any{
				"delivery_id": "11111111-1111-1111-1111-111111111111",
				"event_id":    "11111111-1111-1111-1111-111111111111",
				"lease_id":    "22222222-2222-2222-2222-222222222222",
			},
		}
		stateDoc["wake_refusal_diagnostic"] = map[string]any{
			"phase":          "bridge_after_claim",
			"origin":         "hook",
			"settlement":     "held",
			"error_class":    "CoordinationError",
			"cause_class":    nil,
			"attempt_id":     "att-1",
			"delivery_id":    "11111111-1111-1111-1111-111111111111",
			"native_turn_id": "claude-channel:1",
			"grant":          "absent",
		}
	}
	rawState, _ := json.Marshal(stateDoc)
	stateKey := sha256.Sum256([]byte("fixture\x00301e246e-a3b7-498b-b6a7-3d84e592097f"))
	if err := os.WriteFile(filepath.Join(stateDir, hex.EncodeToString(stateKey[:])+".json"), rawState, 0o600); err != nil {
		t.Fatalf("write state: %v", err)
	}
	ctxDoc := map[string]any{
		"schema":       "cairn.session-inbox/1",
		"state_dir":    stateDir,
		"attempt_id":   "att-1",
		"agent_id":     "301e246e-a3b7-498b-b6a7-3d84e592097f",
		"execution_id": "da3e0447-4f10-40f7-b64d-6b66de691ef2",
		"event_id":     "11111111-1111-1111-1111-111111111111",
		"delivery_id":  "11111111-1111-1111-1111-111111111111",
		"lease_id":     "22222222-2222-2222-2222-222222222222",
		"kind":         "notice",
		"sender":       "agent/291f7647-91e2-4e8b-9817-1870a469258b",
		"source":       map[string]any{"record_id": "33333333-3333-3333-3333-333333333333", "version": 1},
		"commands": map[string]any{
			"ack":      []any{fakeBin, "ack", "11111111-1111-1111-1111-111111111111"},
			"complete": []any{fakeBin, "complete", "--stdin", "11111111-1111-1111-1111-111111111111"},
		},
		"request_ids": map[string]any{"completion": "44444444-4444-4444-4444-444444444444"},
	}
	rawCtx, _ := json.Marshal(ctxDoc)
	ctxPath := filepath.Join(stateDir, "inbox", "att-1.json")
	if err := os.WriteFile(ctxPath, rawCtx, 0o600); err != nil {
		t.Fatalf("write context: %v", err)
	}
	return ctxPath, cfgPath
}

func assertPythonGoLifecycleCoordParity(t *testing.T, scriptPath string, pyArgs, goArgs []string, pyState, goState string, wantCode int) {
	t.Helper()
	cmd := exec.Command("python3", append([]string{"-I", "-B", scriptPath}, pyArgs...)...)
	var pyOut, pyErr bytes.Buffer
	cmd.Stdout = &pyOut
	cmd.Stderr = &pyErr
	err := cmd.Run()
	pyCode := 0
	if err != nil {
		exitErr, ok := err.(*exec.ExitError)
		if !ok {
			t.Fatalf("python exec failed: %v", err)
		}
		pyCode = exitErr.ExitCode()
	}

	var goOut, goErr bytes.Buffer
	goCode, err := executeLifecycleCoordination(context.Background(), goArgs, strings.NewReader(""), &goOut, &goErr)
	if err != nil {
		t.Fatalf("go executeLifecycleCoordination error: %v", err)
	}
	if pyCode != goCode {
		t.Fatalf("exit code mismatch: py=%d (%s) go=%d (%s)", pyCode, pyErr.String(), goCode, goErr.String())
	}
	if wantCode >= 0 && pyCode != wantCode {
		t.Fatalf("expected exit code %d, got py=%d (out=%s err=%s) go=%d (out=%s err=%s)", wantCode, pyCode, pyOut.String(), pyErr.String(), goCode, goOut.String(), goErr.String())
	} else if wantCode < 0 && pyCode == 0 {
		t.Fatalf("expected non-zero refusal exit code, got 0 (out=%s)", pyOut.String())
	}
	if strings.ReplaceAll(pyErr.String(), pyState, "<STATE>") != strings.ReplaceAll(goErr.String(), goState, "<STATE>") {
		t.Fatalf("stderr mismatch:\npy=%q\ngo=%q", pyErr.String(), goErr.String())
	}
	if pyOut.Len() > 0 || goOut.Len() > 0 {
		var pyJSON, goJSON any
		if err := json.Unmarshal([]byte(strings.ReplaceAll(pyOut.String(), pyState, "<STATE>")), &pyJSON); err != nil {
			t.Fatalf("unmarshal py stdout %q: %v", pyOut.String(), err)
		}
		if err := json.Unmarshal([]byte(strings.ReplaceAll(goOut.String(), goState, "<STATE>")), &goJSON); err != nil {
			t.Fatalf("unmarshal go stdout %q: %v", goOut.String(), err)
		}
		scrubCoordTimestamps(pyJSON)
		scrubCoordTimestamps(goJSON)
		if !reflect.DeepEqual(pyJSON, goJSON) {
			t.Fatalf("stdout JSON mismatch:\npy=%s\ngo=%s", pyOut.String(), goOut.String())
		}
	}
	stateKey := sha256.Sum256([]byte("fixture\x00301e246e-a3b7-498b-b6a7-3d84e592097f"))
	for _, rel := range []string{
		hex.EncodeToString(stateKey[:]) + ".json",
		filepath.Join("intents", "att-1.json"),
		filepath.Join("inbox", "att-1.json"),
	} {
		pyRaw, pyReadErr := os.ReadFile(filepath.Join(pyState, rel))
		goRaw, goReadErr := os.ReadFile(filepath.Join(goState, rel))
		if (pyReadErr == nil) != (goReadErr == nil) {
			t.Fatalf("disk file presence mismatch for %s: pyErr=%v goErr=%v", rel, pyReadErr, goReadErr)
		}
		if pyReadErr == nil {
			var pyDoc, goDoc any
			if err := json.Unmarshal([]byte(strings.ReplaceAll(string(pyRaw), pyState, "<STATE>")), &pyDoc); err != nil {
				t.Fatalf("unmarshal py disk file %s: %v", rel, err)
			}
			if err := json.Unmarshal([]byte(strings.ReplaceAll(string(goRaw), goState, "<STATE>")), &goDoc); err != nil {
				t.Fatalf("unmarshal go disk file %s: %v", rel, err)
			}
			scrubCoordTimestamps(pyDoc)
			scrubCoordTimestamps(goDoc)
			if !reflect.DeepEqual(pyDoc, goDoc) {
				t.Fatalf("disk file %s mismatch:\npy=%s\ngo=%s", rel, string(pyRaw), string(goRaw))
			}
		}
	}
}

func scrubCoordTimestamps(v any) {
	switch m := v.(type) {
	case map[string]any:
		for k, child := range m {
			if k == "at" || k == "updated_at" || k == "finished_at" || k == "observed_at" || k == "age_seconds" || k == "recorded_at" {
				if _, ok := child.(float64); ok {
					m[k] = float64(0)
					continue
				}
			}
			scrubCoordTimestamps(child)
		}
	case []any:
		for _, item := range m {
			scrubCoordTimestamps(item)
		}
	}
}

func mustAtoi64(t *testing.T, s string) int64 {
	t.Helper()
	n, err := strconv.ParseInt(s, 10, 64)
	if err != nil {
		t.Fatalf("ParseInt(%q): %v", s, err)
	}
	return n
}

func TestLifecycleCoordinationClassBRefusals(t *testing.T) {
	if os.Getenv("HOME") != "/tmp/home" {
		t.Fatalf("refusing to run outside campaign isolation sandbox (HOME=%q)", os.Getenv("HOME"))
	}
	scriptPath, err := filepath.Abs(filepath.Join("..", "..", "integrations", "lifecycle", "coordination.py"))
	if err != nil {
		t.Fatalf("resolve coordination.py: %v", err)
	}
	base := t.TempDir()
	pyState, goState := filepath.Join(base, "py-state"), filepath.Join(base, "go-state")
	for _, st := range []string{pyState, goState} {
		_ = os.MkdirAll(st, 0o700)
		_ = os.WriteFile(filepath.Join(st, "bad-ctx.json"), []byte(`{"state_dir":"`+st+`"}`), 0o660)
	}
	for _, tc := range []struct {
		name   string
		pyArgs []string
		goArgs []string
	}{
		{
			name:   "missing_config_refusal",
			pyArgs: []string{"inspect-held-refusal"},
			goArgs: []string{"inspect-held-refusal"},
		},
		{
			name:   "group_writable_context_refusal",
			pyArgs: []string{"inbox", "--context", filepath.Join(pyState, "bad-ctx.json"), "ack"},
			goArgs: []string{"inbox", "--context", filepath.Join(goState, "bad-ctx.json"), "ack"},
		},
		{
			name:   "invalid_locator_refusal",
			pyArgs: []string{"recover-codex-ended-turn", "--config", filepath.Join(pyState, "cfg.json"), "--native-session-id", "301e246e-a3b7-498b-b6a7-3d84e592097f", "--attempt-id", "att-1", "--start-record", "bad", "--end-record", "bad"},
			goArgs: []string{"recover-codex-ended-turn", "--config", filepath.Join(goState, "cfg.json"), "--native-session-id", "301e246e-a3b7-498b-b6a7-3d84e592097f", "--attempt-id", "att-1", "--start-record", "bad", "--end-record", "bad"},
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			assertPythonGoLifecycleCoordParity(t, scriptPath, tc.pyArgs, tc.goArgs, pyState, goState, -1)
		})
	}
}

func TestLifecycleCoordinationMemoryBindingValidation(t *testing.T) {
	if os.Getenv("HOME") != "/tmp/home" {
		t.Fatalf("refusing to run outside campaign isolation sandbox (HOME=%q)", os.Getenv("HOME"))
	}
	base := t.TempDir()
	if err := os.Chmod(base, 0o700); err != nil {
		t.Fatal(err)
	}
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	rawBin, err := os.ReadFile(self)
	if err != nil {
		t.Fatal(err)
	}
	fakeBin := filepath.Join(base, "fake-cairn")
	if err := os.Link(self, fakeBin); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(fakeBin, 0o500); err != nil {
		t.Fatal(err)
	}
	binSum := sha256.Sum256(rawBin)
	binSHA := hex.EncodeToString(binSum[:])

	memStateDir := filepath.Join(base, "mem-state")
	coordStateDir := filepath.Join(base, "coord-state")
	for _, d := range []string{memStateDir, coordStateDir} {
		if err := os.MkdirAll(d, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	manifestPath := filepath.Join(base, "binding.json")
	writeChecked := func(path string, doc map[string]any) string {
		raw, _ := json.Marshal(doc)
		if err := os.WriteFile(path, raw, 0o600); err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256(raw)
		return hex.EncodeToString(sum[:])
	}
	memPath := filepath.Join(base, "memory.json")
	memDoc := map[string]any{
		"recall_mode":          "agent_tools",
		"harness":              "codex",
		"cairn":                fakeBin,
		"socket":               filepath.Join(base, "api.sock"),
		"token_file":           filepath.Join(base, "token"),
		"state_dir":            memStateDir,
		"repo":                 "/tmp/test-repo",
		"context_bytes":        4000,
		"inbox_recall_binding": manifestPath,
	}
	memSHA := writeChecked(memPath, memDoc)
	coordPath := filepath.Join(base, "coord.json")
	coordDoc := map[string]any{
		"cairn":                fakeBin,
		"socket":               filepath.Join(base, "api.sock"),
		"token_file":           filepath.Join(base, "token"),
		"state_dir":            coordStateDir,
		"harness":              "codex",
		"repo":                 "/tmp/test-repo",
		"binding":              "fixture",
		"native_delivery":      true,
		"inbox_recall_binding": manifestPath,
	}
	coordSHA := writeChecked(coordPath, coordDoc)

	profile := newLifecycleCoordinationProfile(base, os.Getenv, os.LookupEnv)
	uid := uint32(os.Getuid())
	coordRaw, _ := os.ReadFile(coordPath)
	cfg, _ := profile.Decode(coordination.SiteConfigRead, coordRaw)

	v2Body := `{"schema":"cairn.inbox-recall-binding/2","enabled":true}`
	if err := os.WriteFile(manifestPath, []byte(v2Body), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := resolveBoundMemoryStateDir(profile, cfg, uid); err == nil || !strings.Contains(err.Error(), binding.ErrLegacyBinding.Error()) {
		t.Fatalf("legacy /2 binding must fail closed with ErrLegacyBinding, got: %v", err)
	}

	writeManifest := func(mSHA, cSHA string) {
		v3Doc := map[string]any{
			"schema":               "cairn.inbox-recall-binding/3",
			"enabled":              true,
			"binary":               map[string]any{"path": fakeBin, "sha256": binSHA},
			"memory_config":        map[string]any{"path": memPath, "sha256": mSHA},
			"coordination_configs": []any{map[string]any{"path": coordPath, "sha256": cSHA}},
		}
		v3Raw, _ := json.Marshal(v3Doc)
		if err := os.WriteFile(manifestPath, v3Raw, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	writeManifest(memSHA, coordSHA)
	got, err := resolveBoundMemoryStateDir(profile, cfg, uid)
	if err != nil || got == nil || !value.SameText(got, memStateDir) {
		t.Fatalf("valid /3 binding must resolve memStateDir, got=%v err=%v", got, err)
	}

	for _, neg := range []struct {
		name   string
		mutate func(m, c map[string]any)
	}{
		{"mismatched_harness", func(m, c map[string]any) { c["harness"] = "claude" }},
		{"mismatched_socket", func(m, c map[string]any) { c["socket"] = filepath.Join(base, "other.sock") }},
		{"mismatched_repo", func(m, c map[string]any) { c["repo"] = "/tmp/other-repo" }},
		{"shared_state_dir", func(m, c map[string]any) { c["state_dir"] = memStateDir }},
		{"invalid_context_bytes", func(m, c map[string]any) { m["context_bytes"] = 500 }},
		{"missing_native_delivery", func(m, c map[string]any) { c["native_delivery"] = false }},
	} {
		t.Run(neg.name, func(t *testing.T) {
			mCopy, cCopy := map[string]any{}, map[string]any{}
			for k, v := range memDoc {
				mCopy[k] = v
			}
			for k, v := range coordDoc {
				cCopy[k] = v
			}
			neg.mutate(mCopy, cCopy)
			mSHA := writeChecked(memPath, mCopy)
			cSHA := writeChecked(coordPath, cCopy)
			writeManifest(mSHA, cSHA)
			cRaw, _ := os.ReadFile(coordPath)
			curCfg, _ := profile.Decode(coordination.SiteConfigRead, cRaw)
			if _, err := resolveBoundMemoryStateDir(profile, curCfg, uid); err == nil || !strings.Contains(err.Error(), "incompatible inbox recall profile binding") {
				t.Fatalf("expected incompatible profile validation failure for %s, got: %v", neg.name, err)
			}
		})
	}
	for _, nonCanon := range []string{base + "//binding.json", base + "/./binding.json"} {
		badCfg, _ := profile.Decode(coordination.SiteConfigRead, []byte(`{"inbox_recall_binding":"`+nonCanon+`"}`))
		if _, err := resolveBoundMemoryStateDir(profile, badCfg, uid); err == nil {
			t.Fatalf("non-canonical manifest path %q must be refused", nonCanon)
		}
	}
}

func TestLifecycleCoordinationHomeAndClaimInstant(t *testing.T) {
	fsEmpty := lifecycleFilesystemRoutes{
		lookupEnv: func(k string) (string, bool) {
			if k == "HOME" {
				return "", true
			}
			return "", false
		},
	}
	homeRunes, err := fsEmpty.Home()
	if err != nil || string(homeRunes) != "/" {
		t.Fatalf("HOME=\"\" must return \"/\" (matching Path.home()), got=%q err=%v", string(homeRunes), err)
	}
	for _, bad := range []string{"0000-01-01T00:00:00Z", "2026-11-10T19:31:20z", "2026-11-10T19:31z", "2026-11-10 19:31:20z"} {
		for _, site := range []transcript.ClaimSite{transcript.ClaimNative, transcript.ClaimSite("daemon_start_claim")} {
			if _, err := parseLifecycleClaimInstant(site, value.Text(bad)); err == nil {
				t.Fatalf("malformed timestamp %q at site %v must be rejected", bad, site)
			}
		}
	}
	for _, ts := range []string{"2026-11-10T12:00:00Z", "2026-11-10 12:00:00+00:00", "2026-11-10T12:00:00-0700"} {
		if _, err := parseLifecycleClaimInstant(transcript.ClaimNative, value.Text(ts)); err != nil {
			t.Fatalf("valid timestamp %q rejected: %v", ts, err)
		}
	}
}
