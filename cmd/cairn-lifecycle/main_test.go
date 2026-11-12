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
	"strconv"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/coordination"
	"github.com/halbritt/cairn/internal/lifecycle/transcript"
)

func TestPrivateUnitControlDispatch(t *testing.T) {
	var out, diagnostic bytes.Buffer
	code := run([]string{"unit-control", "force"}, strings.NewReader(""), &out, &diagnostic)
	var result coordination.UnitControlResult
	if err := json.Unmarshal(out.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if code != 2 || result.Schema != "cairn.unit-control-result/1" || result.Exchange.Published || diagnostic.Len() != 0 {
		t.Fatalf("private control route lost structured refusal: code=%d out=%s err=%s", code, out.String(), diagnostic.String())
	}
}

func TestPrivateActivationRoutePreserved(t *testing.T) {
	var out, errOut bytes.Buffer
	code := run([]string{"lifecycle", "binding", "activation", "--bad-flag"}, strings.NewReader(""), &out, &errOut)
	if code == 0 || out.Len() != 0 || !strings.Contains(errOut.String(), `"reason":"activation"`) {
		t.Fatalf("expected activationipc refusal frame, got code=%d out=%q err=%q", code, out.String(), errOut.String())
	}
	out.Reset()
	errOut.Reset()
	code = run([]string{"unknown-op"}, strings.NewReader(""), &out, &errOut)
	if code == 0 || out.Len() != 0 || !strings.Contains(errOut.String(), `"reason":"activation"`) {
		t.Fatalf("expected activationipc refusal on non-coordination prefix, got code=%d out=%q err=%q", code, out.String(), errOut.String())
	}
}

func TestCoordinationDispatchAndCommandPrefix(t *testing.T) {
	cli, err := transcript.NewCoordinationCLI(transcript.CoordinationCLIOptions{
		Executable: "/opt/cairn/bin/cairn-lifecycle",
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(cli.Command) != 2 || cli.Command[0] != "/opt/cairn/bin/cairn-lifecycle" || cli.Command[1] != "coordination" {
		t.Fatalf("unexpected CLI.Command: %v", cli.Command)
	}

	var out, errOut bytes.Buffer
	code := run([]string{"coordination", "inbox", "--help"}, strings.NewReader(""), &out, &errOut)
	if code != 0 || errOut.Len() != 0 || !strings.Contains(out.String(), "usage: coordination.py inbox") {
		t.Fatalf("coordination inbox --help failed: code=%d out=%q err=%q", code, out.String(), errOut.String())
	}

	// Ported watch mode requires --config-dir and succeeds on an empty --config-dir with --once.
	out.Reset()
	errOut.Reset()
	code = run([]string{"coordination", "watch", "--config", "/nonexistent.json"}, strings.NewReader(""), &out, &errOut)
	if code != 2 || out.Len() != 0 || !strings.Contains(errOut.String(), "watch requires --config-dir") {
		t.Fatalf("expected watch without --config-dir to exit 2, got code=%d out=%q err=%q", code, out.String(), errOut.String())
	}
	out.Reset()
	errOut.Reset()
	code = run([]string{"coordination", "watch", "--config-dir", t.TempDir(), "--once"}, strings.NewReader(""), &out, &errOut)
	if code != 0 || out.Len() != 0 || errOut.Len() != 0 {
		t.Fatalf("expected watch --once on empty dir to succeed, got code=%d out=%q err=%q", code, out.String(), errOut.String())
	}
	// Ported hook mode fails closed when the config file does not exist.
	out.Reset()
	errOut.Reset()
	code = run([]string{"coordination", "hook", "--config", "/nonexistent.json"}, strings.NewReader("{}"), &out, &errOut)
	if code != 1 || out.Len() != 0 || errOut.String() != "Cairn coordination: FileNotFoundError; session presence unavailable\n" {
		t.Fatalf("expected hook to fail closed on missing config, got code=%d out=%q err=%q", code, out.String(), errOut.String())
	}

	// Compatibility selection flags (`--receipt ...`) are verified before dispatch and fail closed on invalid receipts.
	out.Reset()
	errOut.Reset()
	code = run([]string{"coordination", "--receipt", "/nonexistent.json", "--role", "coordination"}, strings.NewReader(""), &out, &errOut)
	if code != 1 || out.Len() != 0 || errOut.String() != "Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n" {
		t.Fatalf("expected CompatibilityPreflightError diagnostic on bad --receipt --role coordination, got code=%d out=%q err=%q", code, out.String(), errOut.String())
	}
	out.Reset()
	errOut.Reset()
	code = run([]string{"coordination", "--receipt", "/nonexistent.json"}, strings.NewReader(""), &out, &errOut)
	if code != 1 || out.Len() != 0 || errOut.String() != "Cairn lifecycle diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n" {
		t.Fatalf("expected Cairn lifecycle prefix when --role coordination is absent, got code=%d out=%q err=%q", code, out.String(), errOut.String())
	}
}

func TestCoordinationSubprocessInboxAndRecovery(t *testing.T) {
	root := t.TempDir()
	if err := os.Chmod(root, 0o700); err != nil {
		t.Fatal(err)
	}
	binPath := filepath.Join(root, "cairn-lifecycle")
	buildCmd := exec.Command("go", "build", "-o", binPath, ".")
	if out, err := buildCmd.CombinedOutput(); err != nil {
		t.Fatalf("go build cairn-lifecycle failed: %v\n%s", err, out)
	}
	if err := os.Chmod(binPath, 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", filepath.Join(root, "home"))
	t.Setenv("CAIRN_INBOX_ROOT", filepath.Join(root, "inbox-journal"))

	fakeCairn := filepath.Join(root, "fake-cairn")
	fakeScript := "#!/bin/sh\nfor arg in \"$@\"; do\n  case \"$arg\" in\n    ack)\n      printf '{\"schema\":\"cairn.response/1\",\"ok\":true,\"data\":{\"state\":\"handled\",\"attempts\":1}}\\n'\n      exit 0\n      ;;\n    agent-heartbeat|agent-context|agent-register)\n      printf '{\"schema\":\"cairn.response/1\",\"ok\":true,\"data\":{\"agent_id\":\"11111111-1111-4111-8111-111111111111\",\"execution_id\":\"22222222-2222-4222-8222-222222222222\",\"native_session_id\":\"conv-1\",\"display_name\":\"agent-1\",\"inbox\":\"agent/11111111-1111-4111-8111-111111111111\",\"metadata\":{}}}\\n'\n      exit 0\n      ;;\n    session-inbox-pending)\n      printf '{\"schema\":\"cairn.response/1\",\"ok\":true,\"data\":{\"events\":[]}}\\n'\n      exit 0\n      ;;\n    event-next)\n      printf '{\"schema\":\"cairn.response/1\",\"ok\":true,\"data\":{\"delivery\":{\"delivery_id\":\"22222222-2222-4222-8222-222222222222\",\"lease_id\":\"33333333-3333-4333-8333-333333333333\",\"event\":{\"event_id\":\"22222222-2222-4222-8222-222222222222\",\"kind\":\"notice\",\"from\":\"agent/sender\",\"ref\":{\"record_id\":\"44444444-4444-4444-8444-444444444444\",\"version\":1}}}}}\\n'\n      exit 0\n      ;;\n  esac\ndone\nprintf '{\"schema\":\"cairn.response/1\",\"ok\":true,\"data\":{}}\\n'\n"
	if err := os.WriteFile(fakeCairn, []byte(fakeScript), 0o700); err != nil {
		t.Fatal(err)
	}
	stateDir := filepath.Join(root, "state", "fixture")
	if err := os.MkdirAll(filepath.Join(stateDir, "inbox"), 0o700); err != nil {
		t.Fatal(err)
	}
	sockPath := filepath.Join(root, "api.sock")
	_ = os.WriteFile(sockPath, []byte("sock"), 0o600)
	tokPath := filepath.Join(root, "token")
	_ = os.WriteFile(tokPath, []byte("tok"), 0o600)
	bindingPath := filepath.Join(root, "binding.json")
	cfgPath := filepath.Join(root, "config.json")
	cfgData, _ := json.Marshal(map[string]any{
		"cairn":                          fakeCairn,
		"socket":                         sockPath,
		"token_file":                     tokPath,
		"state_dir":                      stateDir,
		"harness":                        "codex",
		"repo":                           filepath.Join(root, "repo"),
		"binding":                        "fixture",
		"native_delivery":                true,
		"compact_inbox_version":          1,
		"notice_batch_version":           1,
		"remind_unacknowledged_turn_end": true,
		"inbox_recall_binding":           bindingPath,
	})
	if err := os.WriteFile(cfgPath, cfgData, 0o600); err != nil {
		t.Fatal(err)
	}
	statRaw, err := os.ReadFile("/proc/self/stat")
	if err != nil {
		t.Fatal(err)
	}
	afterComm := strings.Fields(string(statRaw)[strings.LastIndexByte(string(statRaw), ')')+2:])
	startTicks, _ := strconv.ParseInt(afterComm[19], 10, 64)
	bootRaw, err := os.ReadFile("/proc/sys/kernel/random/boot_id")
	if err != nil {
		t.Fatal(err)
	}
	ownData, _ := json.Marshal(map[string]any{
		"native_session_id": "conv-1",
		"agent": map[string]any{
			"agent_id":          "11111111-1111-4111-8111-111111111111",
			"execution_id":      "22222222-2222-4222-8222-222222222222",
			"native_session_id": "conv-1",
		},
		"process": map[string]any{
			"pid":   os.Getpid(),
			"start": startTicks,
			"boot":  strings.TrimSpace(string(bootRaw)),
		},
	})
	stateKey := sha256.Sum256([]byte("fixture\x00conv-1"))
	if err := os.WriteFile(filepath.Join(stateDir, hex.EncodeToString(stateKey[:])+".json"), ownData, 0o600); err != nil {
		t.Fatal(err)
	}

	// Run `cairn-lifecycle coordination inbox drain ...`
	drainCmd := exec.Command(binPath, "coordination", "inbox", "drain", "--config", cfgPath, "--agent-id", "11111111-1111-4111-8111-111111111111", "--execution-id", "22222222-2222-4222-8222-222222222222")
	drainOut, err := drainCmd.CombinedOutput()
	if err != nil {
		t.Fatalf("drain failed: %v\n%s", err, drainOut)
	}
	var drainEnv map[string]any
	if err := json.Unmarshal(drainOut, &drainEnv); err != nil {
		t.Fatal(err, string(drainOut))
	}
	ackArgvRaw, _ := drainEnv["acknowledgement"].([]any)
	ackStrings := make([]string, len(ackArgvRaw))
	for i, v := range ackArgvRaw {
		ackStrings[i], _ = v.(string)
	}
	if len(ackStrings) < 2 || ackStrings[0] != binPath || ackStrings[1] != "coordination" {
		t.Fatalf("expected [binPath, coordination, ...] in drain acknowledgement, got %v", ackStrings)
	}

	// Run the emitted acknowledgement vector verbatim
	ackCmd := exec.Command(ackStrings[0], ackStrings[1:]...)
	ackOut, err := ackCmd.CombinedOutput()
	if err != nil || !strings.Contains(string(ackOut), `"state":"handled"`) {
		t.Fatalf("ack failed: %v\n%s", err, ackOut)
	}

	// Also verify J-06 recovery inspection through both mode-first and option-first `cairn-lifecycle coordination`
	for _, argv := range [][]string{
		{"coordination", "inspect-held-refusal", "--config", cfgPath, "--native-session-id", "conv-1"},
		{"coordination", "--config", cfgPath, "inspect-held-refusal", "--native-session-id", "conv-1"},
	} {
		inspectCmd := exec.Command(binPath, argv...)
		inspectOut, err := inspectCmd.CombinedOutput()
		if err == nil || !strings.Contains(string(inspectOut), "NOT_FOUND: no active held-attempt refusal diagnostic matches this session") {
			t.Fatalf("expected NOT_FOUND for %v when no held refusal exists, got err=%v\n%s", argv, err, inspectOut)
		}
	}
	missingCfgCmd := exec.Command(binPath, "coordination", "--config", filepath.Join(root, "missing.json"), "inspect-held-refusal", "--native-session-id", "conv-1")
	missingCfgOut, err := missingCfgCmd.CombinedOutput()
	if err == nil || !strings.Contains(string(missingCfgOut), "Cairn coordination: FileNotFoundError; session presence unavailable") {
		t.Fatalf("expected FileNotFoundError on option-first missing config, got err=%v\n%s", err, missingCfgOut)
	}

	// Verify /3 receipt selection: fails closed before event-next when vector is absent, and succeeds end-to-end when CompatibilityVector is provided.
	shaHex := func(b []byte) string { s := sha256.Sum256(b); return hex.EncodeToString(s[:]) }
	memCfg := filepath.Join(root, "memory.json")
	memCfgData, _ := json.Marshal(map[string]any{
		"cairn":                fakeCairn,
		"socket":               sockPath,
		"token_file":           tokPath,
		"state_dir":            filepath.Join(root, "mem-state"),
		"harness":              "codex",
		"repo":                 filepath.Join(root, "repo"),
		"binding":              "mem-fixture",
		"recall_mode":          "agent_tools",
		"context_bytes":        2000,
		"inbox_recall_binding": bindingPath,
	})
	_ = os.WriteFile(memCfg, memCfgData, 0o600)
	binRaw, _ := os.ReadFile(binPath)
	binDesc := map[string]string{"path": binPath, "sha256": shaHex(binRaw)}
	memDesc := map[string]string{"path": memCfg, "sha256": shaHex(memCfgData)}
	coordDesc := []any{map[string]string{"path": cfgPath, "sha256": shaHex(cfgData)}}
	bindingRaw, _ := json.Marshal(map[string]any{
		"schema":               "cairn.inbox-recall-binding/3",
		"enabled":              true,
		"binary":               binDesc,
		"memory_config":        memDesc,
		"coordination_configs": coordDesc,
	})
	_ = os.WriteFile(bindingPath, bindingRaw, 0o600)
	hashes := map[string]string{}
	members := map[string]any{}
	for _, m := range coordination.CompatibilityClosureMembers {
		raw := []byte("# " + m + "\n")
		hashes[m] = shaHex(raw)
		members[m] = map[string]any{"sha256": hashes[m], "bytes": len(raw)}
	}
	snapRoot := filepath.Join(root, coordination.CompatibilitySnapshotIdentity(hashes))
	_ = os.MkdirAll(snapRoot, 0o700)
	_ = os.Chmod(snapRoot, 0o700)
	for _, m := range coordination.CompatibilityClosureMembers {
		p := filepath.Join(snapRoot, m)
		_ = os.WriteFile(p, []byte("# "+m+"\n"), 0o600)
		_ = os.Chmod(p, 0o600)
	}
	receiptPath := filepath.Join(root, "receipt.json")
	receiptRaw, _ := json.Marshal(map[string]any{
		"schema": "cairn.lifecycle-compatibility/1", "source_revision": "rev-1", "snapshot_root": snapRoot,
		"members": members, "bootstrap": "lifecycle_compatibility.py", "loader": "lifecycle_bundle.py",
		"roles":                map[string]string{"memory": "memory.py", "coordination": "coordination.py", "inbox_recall": "inbox_recall.py", "inbox_binding": "inbox_binding.py"},
		"aliases":              map[string]string{},
		"binary":               binDesc,
		"binding":              map[string]string{"path": bindingPath, "sha256": shaHex(bindingRaw), "schema": "cairn.inbox-recall-binding/3"},
		"memory_config":        memDesc,
		"coordination_configs": coordDesc,
	})
	_ = os.WriteFile(receiptPath, receiptRaw, 0o600)
	receiptSHA := shaHex(receiptRaw)
	launchRoot := filepath.Join(root, "launchers")
	vector := []string{"/usr/bin/python3", "-I", "-S", "-c", "VERIFIER", "--receipt", receiptPath}
	lBytes, _ := coordination.LauncherBytes(vector, "/bin/sh")
	launcherDir := filepath.Join(launchRoot, shaHex(lBytes))
	_ = os.MkdirAll(launcherDir, 0o700)
	launcherPath := filepath.Join(launcherDir, "inbox")
	_ = os.WriteFile(launcherPath, lBytes, 0o500)

	_ = os.RemoveAll(filepath.Join(stateDir, "inbox"))
	_ = os.RemoveAll(filepath.Join(stateDir, "intents"))
	_ = os.MkdirAll(filepath.Join(stateDir, "inbox"), 0o700)
	baseSel := []string{"--receipt", receiptPath, "--receipt-sha256", receiptSHA, "--role", "coordination", "--entrypoint", "coordination.py", "--config", cfgPath, "--hook-timeout", "15", "--launch-root", launchRoot, "--shell", "/bin/sh", "--"}
	noVecOut, err := exec.Command(binPath, append([]string{"coordination"}, append(baseSel, "inbox", "drain", "--config", cfgPath, "--agent-id", "11111111-1111-4111-8111-111111111111", "--execution-id", "22222222-2222-4222-8222-222222222222")...)...).CombinedOutput()
	if err == nil || string(noVecOut) != "Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n" {
		t.Fatalf("expected direct /3 CLI without vector to fail closed in preflight, got err=%v out=%q", err, noVecOut)
	}
	if entries, _ := os.ReadDir(filepath.Join(stateDir, "inbox")); len(entries) != 0 {
		t.Fatalf("expected 0 inbox context files when /3 preflight fails closed, got %d", len(entries))
	}
	var cOut, cErr bytes.Buffer
	code, err := transcript.ExecuteCoordinationCLIWithOptions(context.Background(), transcript.CoordinationCLIOptions{CompatibilityVector: vector}, append(baseSel, "inbox", "drain", "--config", cfgPath, "--agent-id", "11111111-1111-4111-8111-111111111111", "--execution-id", "22222222-2222-4222-8222-222222222222"), strings.NewReader(""), &cOut, &cErr)
	if err != nil || code != 0 {
		t.Fatalf("expected /3 drain with CompatibilityVector to succeed, got code=%d err=%v stderr=%q", code, err, cErr.String())
	}
	var cDrain map[string]any
	_ = json.Unmarshal(cOut.Bytes(), &cDrain)
	cAck, _ := cDrain["acknowledgement"].([]any)
	if len(cAck) != 5 || cAck[0] != launcherPath || cAck[1] != "inbox" || cAck[2] != "--context" || cAck[4] != "ack" {
		t.Fatalf("expected /3 launcher acknowledgement vector, got %v", cAck)
	}
	cOut.Reset()
	cErr.Reset()
	code, err = transcript.ExecuteCoordinationCLIWithOptions(context.Background(), transcript.CoordinationCLIOptions{CompatibilityVector: vector}, append(baseSel, "inbox", "--context", cAck[3].(string), "ack"), strings.NewReader(""), &cOut, &cErr)
	if err != nil || code != 0 || !strings.Contains(cOut.String(), `"state":"handled"`) {
		t.Fatalf("expected /3 ack with CompatibilityVector to succeed, got code=%d err=%v out=%q stderr=%q", code, err, cOut.String(), cErr.String())
	}

	// Verify RunCoordinationProcess subprocess resolves the 21-element /3 launcher vector directly from launch_root without out-of-band options.
	fullVec := append([]string{"/usr/bin/python3", "-I", "-S", "-c", "VERIFIER"}, baseSel[:16]...)
	fullLBytes, _ := coordination.LauncherBytes(fullVec, "/bin/sh")
	fullLDir := filepath.Join(launchRoot, shaHex(fullLBytes))
	_ = os.MkdirAll(fullLDir, 0o700)
	_ = os.Chmod(fullLDir, 0o700)
	fullLPath := filepath.Join(fullLDir, "inbox")
	_ = os.WriteFile(fullLPath, fullLBytes, 0o500)
	_ = os.Chmod(fullLPath, 0o500)
	procDrainOut, err := exec.Command(binPath, append([]string{"coordination"}, append(baseSel, "inbox", "drain", "--config", cfgPath, "--agent-id", "11111111-1111-4111-8111-111111111111", "--execution-id", "22222222-2222-4222-8222-222222222222")...)...).CombinedOutput()
	if err != nil {
		t.Fatalf("expected subprocess /3 drain with resolved CompatibilityVector to succeed, got err=%v out=%q", err, procDrainOut)
	}
	var procDrain map[string]any
	_ = json.Unmarshal(procDrainOut, &procDrain)
	procAck, _ := procDrain["acknowledgement"].([]any)
	if len(procAck) != 5 || procAck[0] != fullLPath {
		t.Fatalf("expected resolved /3 launcher acknowledgement vector, got %v", procAck)
	}

	// Verify direct staged hook entry `[image, coordination, hook, --config, PATH]` on an unbound profile.
	unboundCfgDir := filepath.Join(root, "unbound-configs")
	_ = os.MkdirAll(unboundCfgDir, 0o700)
	unboundCfgPath := filepath.Join(unboundCfgDir, "coordination-0.json")
	unboundCfgData, _ := json.Marshal(map[string]any{
		"cairn":      fakeCairn,
		"socket":     sockPath,
		"token_file": tokPath,
		"state_dir":  stateDir,
		"harness":    "codex",
		"repo":       filepath.Join(root, "repo"),
		"binding":    "fixture",
	})
	_ = os.WriteFile(unboundCfgPath, unboundCfgData, 0o600)
	hookCmd := exec.Command(binPath, "coordination", "hook", "--config", unboundCfgPath)
	hookCmd.Stdin = strings.NewReader(`{"hook_event_name":"SessionStart","session_id":"conv-1","cwd":"` + root + `","host_pid":` + strconv.Itoa(os.Getpid()) + `}`)
	hookOut, err := hookCmd.CombinedOutput()
	if err != nil || !strings.Contains(string(hookOut), "Cairn session") {
		t.Fatalf("expected direct subprocess coordination hook to succeed, got err=%v out=%q", err, hookOut)
	}

	// Verify /3 --receipt hook entry with UnboundProfiles (`stage_lifecycle_compatibility.py` unbound role profiles).
	unboundMemCfg := filepath.Join(root, "unbound-memory.json")
	unboundMemData, _ := json.Marshal(map[string]any{
		"cairn":         fakeCairn,
		"socket":        sockPath,
		"token_file":    tokPath,
		"state_dir":     filepath.Join(root, "mem-state"),
		"harness":       "codex",
		"repo":          filepath.Join(root, "repo"),
		"binding":       "mem-fixture",
		"recall_mode":   "agent_tools",
		"context_bytes": 2000,
	})
	_ = os.WriteFile(unboundMemCfg, unboundMemData, 0o600)
	unboundMemDesc := map[string]string{"path": unboundMemCfg, "sha256": shaHex(unboundMemData)}
	unboundCoordDesc := []any{map[string]string{"path": unboundCfgPath, "sha256": shaHex(unboundCfgData)}}
	unboundBindingPath := filepath.Join(root, "unbound-binding.json")
	unboundBindingRaw, _ := json.Marshal(map[string]any{
		"schema":               "cairn.inbox-recall-binding/3",
		"enabled":              false,
		"binary":               binDesc,
		"memory_config":        unboundMemDesc,
		"coordination_configs": unboundCoordDesc,
	})
	_ = os.WriteFile(unboundBindingPath, unboundBindingRaw, 0o600)
	unboundReceiptPath := filepath.Join(root, "unbound-receipt.json")
	unboundReceiptRaw, _ := json.Marshal(map[string]any{
		"schema": "cairn.lifecycle-compatibility/1", "source_revision": "rev-1", "snapshot_root": snapRoot,
		"members": members, "bootstrap": "lifecycle_compatibility.py", "loader": "lifecycle_bundle.py",
		"roles":                map[string]string{"memory": "memory.py", "coordination": "coordination.py", "inbox_recall": "inbox_recall.py", "inbox_binding": "inbox_binding.py"},
		"aliases":              map[string]string{},
		"binary":               binDesc,
		"binding":              map[string]string{"path": unboundBindingPath, "sha256": shaHex(unboundBindingRaw), "schema": "cairn.inbox-recall-binding/3"},
		"memory_config":        unboundMemDesc,
		"coordination_configs": unboundCoordDesc,
	})
	_ = os.WriteFile(unboundReceiptPath, unboundReceiptRaw, 0o600)
	unboundReceiptSHA := shaHex(unboundReceiptRaw)
	unboundSel := []string{
		"--receipt", unboundReceiptPath, "--receipt-sha256", unboundReceiptSHA,
		"--role", "coordination", "--entrypoint", "coordination.py",
		"--config", unboundCfgPath, "--hook-timeout", "15",
		"--launch-root", launchRoot, "--shell", "/bin/sh",
	}
	unboundVec := append([]string{"/usr/bin/python3", "-I", "-S", "-c", "VERIFIER"}, unboundSel...)
	unboundLBytes, _ := coordination.LauncherBytes(unboundVec, "/bin/sh")
	unboundLDir := filepath.Join(launchRoot, shaHex(unboundLBytes))
	_ = os.MkdirAll(unboundLDir, 0o700)
	_ = os.Chmod(unboundLDir, 0o700)
	unboundLPath := filepath.Join(unboundLDir, "inbox")
	_ = os.WriteFile(unboundLPath, unboundLBytes, 0o500)
	_ = os.Chmod(unboundLPath, 0o500)
	compatHookCmd := exec.Command(binPath, append([]string{"coordination"}, append(unboundSel, "--", "hook", "--config", unboundCfgPath)...)...)
	compatHookCmd.Stdin = strings.NewReader(`{"hook_event_name":"SessionStart","session_id":"conv-1","cwd":"` + root + `","host_pid":` + strconv.Itoa(os.Getpid()) + `}`)
	compatHookOut, err := compatHookCmd.CombinedOutput()
	if err != nil || !strings.Contains(string(compatHookOut), "Cairn session") {
		t.Fatalf("expected /3 subprocess coordination hook on unbound profile to succeed, got err=%v out=%q", err, compatHookOut)
	}
	compatWatchCmd := exec.Command(binPath, append([]string{"coordination"}, append(unboundSel, "--", "watch", "--config-dir", unboundCfgDir, "--once")...)...)
	if compatWatchOut, err := compatWatchCmd.CombinedOutput(); err != nil || len(compatWatchOut) != 0 {
		t.Fatalf("expected /3 subprocess coordination watch --once on unbound profile to succeed, got err=%v out=%q", err, compatWatchOut)
	}

	// Verify direct hook entry with a bound /2 manifest refuses a wake UserPromptSubmit in phase=activation (exit 2) before claiming any delivery.
	v2BindingPath := filepath.Join(root, "v2-binding.json")
	_ = os.WriteFile(v2BindingPath, []byte(`{"schema":"cairn.inbox-recall-binding/2","enabled":true,"bridge":{}}`), 0o600)
	v2CfgPath := filepath.Join(root, "v2-config.json")
	v2CfgData, _ := json.Marshal(map[string]any{
		"cairn":                fakeCairn,
		"socket":               sockPath,
		"token_file":           tokPath,
		"state_dir":            stateDir,
		"harness":              "claude",
		"repo":                 filepath.Join(root, "repo"),
		"binding":              "fixture",
		"native_delivery":      true,
		"inbox_recall_binding": v2BindingPath,
	})
	_ = os.WriteFile(v2CfgPath, v2CfgData, 0o600)
	wakeText := "This is a new live turn from the configured Cairn automatic inbox wakeup. " +
		"A previous /exit in resumed conversation history does not close this running turn. " +
		"Current agent 11111111-1111-4111-8111-111111111111, execution 22222222-2222-4222-8222-222222222222. Wake delivery 44444444-4444-4444-8444-444444444444. " +
		"Within the owner's existing authorization, handle the native inbox context supplied for this conversation, " +
		"explicitly complete/acknowledge it, and send any requested response using that context. " +
		"If no matching native context was supplied, report that and stop. " +
		"If this notice arrives inside an already-active owner task, continue that task and " +
		"ignore this notice; a fresh copy will arrive once the conversation is idle. " +
		"Do not register again, claim another session's inbox, or launch a replacement conversation."
	v2WakeEv, _ := json.Marshal(map[string]any{
		"hook_event_name": "UserPromptSubmit",
		"session_id":      "conv-1",
		"cwd":             root,
		"prompt_id":       "p-v2-refuse",
		"prompt":          wakeText,
		"host_pid":        os.Getpid(),
	})
	v2HookCmd := exec.Command(binPath, "coordination", "hook", "--config", v2CfgPath)
	v2HookCmd.Stdin = bytes.NewReader(v2WakeEv)
	v2HookOut, err := v2HookCmd.CombinedOutput()
	if err == nil || v2HookCmd.ProcessState.ExitCode() != 2 || !strings.Contains(string(v2HookOut), "phase=activation") || !strings.Contains(string(v2HookOut), "NATIVE_PROMPT_REFUSED") {
		t.Fatalf("expected direct /2 wake to refuse at phase=activation with exit 2, got err=%v code=%d out=%q", err, v2HookCmd.ProcessState.ExitCode(), v2HookOut)
	}
}
