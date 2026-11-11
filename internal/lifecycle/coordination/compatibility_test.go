package coordination

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

type compatFixture struct {
	root        string
	stateDir    string
	configPath  string
	bindingPath string
	receiptPath string
	launchRoot  string
	launcher    string
	launcherRaw []byte
	config      *value.Value
	compat      *CompatibilityContext
	profile     *value.SourceReaderProfile
}

func writePrivateFile(t *testing.T, path string, data []byte, perm os.FileMode) string {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	_ = os.Chmod(path, 0o600)
	if err := os.WriteFile(path, data, perm); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(path, perm); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func encodeFixtureJSON(t *testing.T, v *value.Value) []byte {
	t.Helper()
	var buf bytes.Buffer
	yes, no := true, false
	err := value.WriteKernel(&buf, v, value.WriterRoute{
		Route:       "dumps",
		Codec:       &value.Codec{Name: "utf-8", Errors: "strict"},
		EnsureASCII: &yes,
		Compact:     &no,
		TrailingLF:  &yes,
	}, 0)
	if err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

func newCompatFixture(t *testing.T) *compatFixture {
	t.Helper()
	p := stage2ServicesProfile()
	root := t.TempDir()
	if err := os.Chmod(root, 0o700); err != nil {
		t.Fatal(err)
	}
	coordRoot := filepath.Join(root, "coord")
	stateDir := filepath.Join(coordRoot, "state", "fixture")
	if err := os.MkdirAll(filepath.Join(stateDir, "inbox"), 0o700); err != nil {
		t.Fatal(err)
	}
	binDir := filepath.Join(root, "bin")
	cairnBin := filepath.Join(binDir, "cairn")
	cairnBinSHA := writePrivateFile(t, cairnBin, []byte("#!/bin/sh\nexit 0\n"), 0o700)
	sockPath := filepath.Join(root, "sock", "api.sock")
	writePrivateFile(t, sockPath, []byte("sock"), 0o600)
	tokPath := filepath.Join(root, "tok", "token")
	writePrivateFile(t, tokPath, []byte("secret-token"), 0o600)

	bindingPath := filepath.Join(root, "recall", "binding.json")
	memCfg := nativeMap(
		"cairn", cairnBin,
		"socket", sockPath,
		"token_file", tokPath,
		"state_dir", filepath.Join(root, "mem-state"),
		"harness", "codex",
		"repo", filepath.Join(root, "repo"),
		"binding", "mem-fixture",
		"recall_mode", "agent_tools",
		"context_bytes", value.IntegerText("2000"),
		"inbox_recall_binding", bindingPath,
	)
	memCfgPath := filepath.Join(root, "mem", "config.json")
	memSHA := writePrivateFile(t, memCfgPath, encodeFixtureJSON(t, memCfg), 0o600)

	coordCfg := nativeMap(
		"cairn", cairnBin,
		"socket", sockPath,
		"token_file", tokPath,
		"state_dir", stateDir,
		"harness", "codex",
		"repo", filepath.Join(root, "repo"),
		"binding", "fixture",
		"native_delivery", true,
		"compact_inbox_version", value.IntegerText("1"),
		"notice_batch_version", value.IntegerText("1"),
		"remind_unacknowledged_turn_end", true,
		"inbox_recall_binding", bindingPath,
	)
	configPath := filepath.Join(coordRoot, "generations", "selection-fixture", "coordination-0.json")
	coordSHA := writePrivateFile(t, configPath, encodeFixtureJSON(t, coordCfg), 0o600)

	hashes := make(map[string]string, len(CompatibilityClosureMembers))
	memberBodies := make(map[string][]byte, len(CompatibilityClosureMembers))
	for _, name := range CompatibilityClosureMembers {
		raw := []byte("# " + name + "\ndef run_checked(*a): return 0\n")
		sum := sha256.Sum256(raw)
		hashes[name] = hex.EncodeToString(sum[:])
		memberBodies[name] = raw
	}
	snapID := CompatibilitySnapshotIdentity(hashes)
	snapshotRoot := filepath.Join(root, "snapshots", snapID)
	membersObj := value.Mapping()
	for _, name := range CompatibilityClosureMembers {
		raw := memberBodies[name]
		writePrivateFile(t, filepath.Join(snapshotRoot, name), raw, 0o400)
		membersObj.Set(name, nativeMap("sha256", hashes[name], "bytes", len(raw)))
	}

	binDesc := nativeMap("path", cairnBin, "sha256", cairnBinSHA)
	memDesc := nativeMap("path", memCfgPath, "sha256", memSHA)
	coordDesc := &value.Value{Kind: value.Array, Items: []*value.Value{nativeMap("path", configPath, "sha256", coordSHA)}}
	manifest := nativeMap(
		"schema", "cairn.inbox-recall-binding/3",
		"enabled", true,
		"binary", binDesc,
		"memory_config", memDesc,
		"coordination_configs", coordDesc,
	)
	bindingSHA := writePrivateFile(t, bindingPath, encodeFixtureJSON(t, manifest), 0o600)
	receipt := nativeMap(
		"schema", "cairn.lifecycle-compatibility/1",
		"source_revision", "test-rev",
		"snapshot_root", snapshotRoot,
		"members", membersObj,
		"bootstrap", "lifecycle_compatibility.py",
		"loader", "lifecycle_bundle.py",
		"roles", nativeMap("memory", "memory.py", "coordination", "coordination.py", "inbox_recall", "inbox_recall.py", "inbox_binding", "inbox_binding.py"),
		"aliases", value.Mapping(),
		"binding", nativeMap("path", bindingPath, "sha256", bindingSHA, "schema", "cairn.inbox-recall-binding/3"),
		"binary", binDesc,
		"memory_config", memDesc,
		"coordination_configs", coordDesc,
	)
	receiptPath := filepath.Join(root, "receipt", "receipt.json")
	receiptSHA := writePrivateFile(t, receiptPath, encodeFixtureJSON(t, receipt), 0o600)

	vector := []string{"/usr/bin/python3", "-I", "-S", "-c", "pass", "/entry.py", configPath, receiptPath, receiptSHA}
	launcherRaw, err := LauncherBytes(vector, DefaultCompatibilityShell)
	if err != nil {
		t.Fatal(err)
	}
	launchRoot := filepath.Join(root, "l")
	launcher := LauncherPath(launchRoot, launcherRaw)
	writePrivateFile(t, launcher, launcherRaw, 0o500)

	compat := &CompatibilityContext{
		Role:           "coordination",
		Entrypoint:     "coordination.py",
		ConfigPath:     configPath,
		HookTimeout:    15,
		LaunchRoot:     launchRoot,
		Shell:          DefaultCompatibilityShell,
		Tail:           []string{},
		ReceiptPath:    receiptPath,
		ReceiptSHA256:  receiptSHA,
		Deadline:       200,
		Now:            func() float64 { return 100 },
		Vector:         vector,
		Launcher:       launcher,
		LauncherRaw:    launcherRaw,
		ActivationFunc: func([]byte) (bool, error) { return true, nil },
		Profile:        p,
		RandomBytes:    func(n int) ([]byte, error) { return bytes.Repeat([]byte{0xab}, n), nil },
	}
	return &compatFixture{
		root: coordRoot, stateDir: stateDir, configPath: configPath,
		bindingPath: bindingPath, receiptPath: receiptPath,
		launchRoot: launchRoot, launcher: launcher, launcherRaw: launcherRaw,
		config: coordCfg, compat: compat, profile: p,
	}
}

func TestCompatibilityLauncherAndIssuedArgv(t *testing.T) {
	f := newCompatFixture(t)
	argv, err := f.compat.IssuedArgv([]string{"inbox", "compact", "read", "abcdef0123456789"})
	if err != nil || len(argv) != 5 || argv[0] != f.launcher || argv[3] != "read" {
		t.Fatal(argv, err)
	}
	padLen := 512 - len(shlexQuote(f.launcher)) - 1
	exact512, err := f.compat.IssuedArgv([]string{strings.Repeat("x", padLen)})
	if err != nil || len(exact512) != 2 {
		t.Fatal(exact512, err)
	}
	_, err = f.compat.IssuedArgv([]string{strings.Repeat("x", padLen+1)})
	stage2ErrorClass(t, err, "ValueError")

	if err := os.Chmod(f.launcher, 0o700); err != nil {
		t.Fatal(err)
	}
	_, err = f.compat.IssuedArgv([]string{"inbox"})
	var pe *CompatibilityPreflightError
	if !errors.As(err, &pe) {
		t.Fatalf("expected CompatibilityPreflightError on 0700 launcher, got %v", err)
	}
	if err := os.Chmod(f.launcher, 0o700); err != nil {
		t.Fatal(err)
	}
	_ = os.WriteFile(f.launcher, []byte("#!/bin/sh\nexec /tampered\n"), 0o500)
	_ = os.Chmod(f.launcher, 0o500)
	_, err = f.compat.IssuedArgv([]string{"inbox"})
	if !errors.As(err, &pe) {
		t.Fatalf("expected CompatibilityPreflightError on tampered launcher, got %v", err)
	}
}

func TestCompatibilityHandoffAndInboxCommandPreflight(t *testing.T) {
	f := newCompatFixture(t)
	state := stage2State()
	nativePop(state, "inbox_attempt", "inbox_intent")
	statePath := filepath.Join(f.stateDir, "own.json")
	stage2WriteValue(t, f.profile, statePath, state)

	delivery := nativeMap(
		"delivery_id", stage2Delivery,
		"lease_id", stage2Attempt,
		"event", nativeMap(
			"event_id", stage2Delivery,
			"kind", "request",
			"from", "agent/other",
			"ref", nativeMap("record_id", stage2Turn, "version", 1),
			"correlation_id", "corr-1",
		),
	)
	var storeCalls []string
	host := NewFileHost(FileHostOptions{
		Profile:       f.profile,
		Compatibility: f.compat,
		Call: func(_ context.Context, _ *value.Value, op string, _ *value.Value, _ NativeCallOptions) (*value.Value, error) {
			storeCalls = append(storeCalls, op)
			if op == "event-next" {
				return nativeMap("delivery", delivery), nil
			}
			return nativeMap("ok", true), nil
		},
	})
	c := &CLI{
		Host:          host,
		Profile:       f.profile,
		Compatibility: f.compat,
		Root:          f.root,
		Engine:        &RecoveryEngine{Hooks: RecoveryHooks{Alive: func(*value.Value) (bool, error) { return true, nil }}},
	}

	// If the compatibility launcher is invalid (or Vector is empty), drain fails preflight before claiming (0 store calls).
	noVecCompat := *f.compat
	noVecCompat.Vector = nil
	noVecCompat.LauncherRaw = nil
	cNoVec := *c
	cNoVec.Compatibility = &noVecCompat
	var preOut, preErr bytes.Buffer
	code, err := cNoVec.RunCLI(context.Background(), []string{"inbox", "drain", "--config", f.configPath, "--agent-id", stage2Agent, "--execution-id", stage2Execution}, strings.NewReader(""), &preOut, &preErr)
	if err != nil || code != 7 || len(storeCalls) != 0 || !strings.Contains(preOut.String(), `"status": "INVALID_HOST"`) || preErr.Len() != 0 {
		t.Fatalf("expected drain without vector to fail before event-next: code=%d err=%v calls=%v out=%q stderr=%q", code, err, storeCalls, preOut.String(), preErr.String())
	}
	code, result, stderr := stage2CLIResult(t, c, "inbox", "drain", "--config", f.configPath, "--agent-id", stage2Agent, "--execution-id", stage2Execution)
	if code != 0 || stderr != "" {
		t.Fatal(code, result, stderr)
	}
	ctxPath := nativeText(result.Get("context"))
	native, err := c.read(ctxPath, SiteInboxContextRead)
	if err != nil {
		t.Fatal(err)
	}
	if nativeText(native.Get("completion").Items[0]) != f.launcher || nativeText(native.Get("acknowledgement").Items[0]) != f.launcher || nativeText(native.Get("response").Items[0]) != f.launcher {
		t.Fatal("expected launcher argv in handoff context", native)
	}
	sc := native.Get("shell_commands")
	if sc == nil || sc.Kind != value.Object || !strings.HasPrefix(nativeText(sc.Get("completion")), f.launcher+" inbox --context ") {
		t.Fatal("missing or invalid shell_commands", sc)
	}
	for _, m := range sc.Members {
		if len(nativeText(m.Value)) > 512 {
			t.Fatal("shell_command exceeds 512 bytes", m)
		}
	}

	c.Journal.Runner = journal.RunnerFunc(func(_ context.Context, _ []string, _ string) (journal.Result, error) {
		return journal.Result{Stdout: `{"schema":"cairn.response/1","ok":true,"data":{"state":"handled","attempts":1}}` + "\n"}, nil
	})
	code, ackRes, stderr := stage2CLIResult(t, c, "inbox", "--context", ctxPath, "ack")
	if code != 0 || !exactTrue(ackRes.Get("ok")) || stderr != "" {
		t.Fatal(code, ackRes, stderr)
	}

	// Unselected context path outside <state_dir>/inbox fails preflight with exit 1, empty stdout, and exact stderr diagnostic.
	unselectedPath := filepath.Join(f.root, "outside.json")
	writePrivateFile(t, unselectedPath, encodeFixtureJSON(t, native), 0o600)
	var outBuf, errBuf bytes.Buffer
	code, err = c.RunCLI(context.Background(), []string{"inbox", "--context", unselectedPath, "ack"}, strings.NewReader(""), &outBuf, &errBuf)
	if err != nil || code != 1 || outBuf.Len() != 0 || errBuf.String() != "Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n" {
		t.Fatal(code, outBuf.String(), errBuf.String(), err)
	}

	// Tampered executable in context fails BindContext preflight before running any command.
	tampered := nativeClone(native)
	tampered.Get("commands").Get("ack").Items[0] = value.Text("/tampered/cairn")
	writePrivateFile(t, ctxPath, encodeFixtureJSON(t, tampered), 0o600)
	// Recovery mode under Compatibility fails VerifyTail preflight with exit 1, empty stdout, and exact stderr diagnostic.
	outBuf.Reset()
	errBuf.Reset()
	code, err = c.RunCLI(context.Background(), []string{"inspect-held-refusal", "--config", f.configPath}, strings.NewReader(""), &outBuf, &errBuf)
	if err != nil || code != 1 || outBuf.Len() != 0 || errBuf.String() != "Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n" {
		t.Fatal(code, outBuf.String(), errBuf.String(), err)
	}

	// Execute emitted shell text via /bin/sh -c and a real 0500 launcher whose vector echoes its tail argv (after "--") as JSON.
	echoVec := []string{"/usr/bin/python3", "-I", "-S", "-c", "import json, sys; print(json.dumps(sys.argv[2:]))"}
	echoRaw, err := LauncherBytes(echoVec, DefaultCompatibilityShell)
	if err != nil {
		t.Fatal(err)
	}
	echoLauncher := LauncherPath(f.launchRoot, echoRaw)
	writePrivateFile(t, echoLauncher, echoRaw, 0o500)
	f.compat.Vector = echoVec
	f.compat.Launcher = echoLauncher
	f.compat.LauncherRaw = echoRaw

	specialDir := filepath.Join(t.TempDir(), "選択 ' \"$()\" `tick`")
	if err := os.MkdirAll(specialDir, 0o700); err != nil {
		t.Fatal(err)
	}
	specialCtx := filepath.Join(specialDir, "ctx.json")
	_, specialNative, err := c.InboxHandoff(f.config, nativeMap("agent_id", stage2Agent, "execution_id", stage2Execution), delivery, stage2Attempt,
		nativeMap("completion", stage2Attempt, "response", stage2Turn))
	if err != nil {
		t.Fatal(err)
	}
	specialTail := []string{"inbox", "--context", specialCtx, "complete"}
	specialArgv, err := f.compat.IssuedArgv(specialTail)
	if err != nil {
		t.Fatal(err)
	}
	specialNative.Set("completion", &value.Value{Kind: value.Array, Items: []*value.Value{value.Text(specialArgv[0]), value.Text(specialArgv[1]), value.Text(specialArgv[2]), value.Text(specialArgv[3]), value.Text(specialArgv[4])}})
	if err := f.compat.ContextCommands(specialNative); err != nil {
		t.Fatal(err)
	}
	settleCmd, err := c.CompactCommandText("settle", "abababababababab", "failed", "--code", "source_unavailable")
	if err != nil {
		t.Fatal(err)
	}
	drainArgv, err := f.compat.IssuedArgv([]string{"inbox", "drain", "--config", f.configPath, "--agent-id", stage2Agent, "--execution-id", stage2Execution})
	if err != nil {
		t.Fatal(err)
	}
	drainCmd, _ := shlexJoinStrings(drainArgv)
	// Non-ASCII argument giving an exactly 512-byte command: '選' is 3 UTF-8 bytes; shlexQuote adds 2 single quotes.
	prefixLen := len(shlexQuote(echoLauncher)) + 1
	remBytes := 512 - prefixLen - 2
	nonASCIITail := "選" + strings.Repeat("a", remBytes-3)
	nonASCIIArgv, err := f.compat.IssuedArgv([]string{nonASCIITail})
	if err != nil {
		t.Fatal(err)
	}
	nonASCIICmd, _ := shlexJoinStrings(nonASCIIArgv)
	if len(nonASCIICmd) != 512 {
		t.Fatalf("expected 512-byte command, got len=%d", len(nonASCIICmd))
	}
	for _, tc := range []struct {
		cmd  string
		want []string
	}{
		{settleCmd, []string{"inbox", "compact", "settle", "abababababababab", "failed", "--code", "source_unavailable"}},
		{nativeText(specialNative.Get("shell_commands").Get("completion")), specialTail},
		{drainCmd, []string{"inbox", "drain", "--config", f.configPath, "--agent-id", stage2Agent, "--execution-id", stage2Execution}},
		{nonASCIICmd, []string{nonASCIITail}},
	} {
		shRes, err := (journal.CommandRunner{}).Run(context.Background(), []string{"/bin/sh", "-c", tc.cmd}, "")
		if err != nil || shRes.ExitCode != 0 {
			t.Fatalf("/bin/sh execution failed for %q: err=%v exit=%d stderr=%s", tc.cmd, err, shRes.ExitCode, shRes.Stderr)
		}
		gotVal, err := value.DecodeBytes([]byte(shRes.Stdout), 0)
		if err != nil || gotVal.Kind != value.Array || len(gotVal.Items) != len(tc.want) {
			t.Fatalf("/bin/sh argv decode mismatch: stdout=%q want=%v err=%v", shRes.Stdout, tc.want, err)
		}
		for i, w := range tc.want {
			if nativeText(gotVal.Items[i]) != w {
				t.Fatalf("/bin/sh argv[%d] = %q, want %q", i, nativeText(gotVal.Items[i]), w)
			}
		}
	}
}

func TestCompatibilityCompactAndAdvance(t *testing.T) {
	f := newCompatFixture(t)
	state := stage2State()
	delivery := state.Get("inbox_attempt").Get("delivery")
	delivery.Get("event").Set("from", value.Text("agent/sender"))
	delivery.Get("event").Set("ref", nativeMap("record_id", stage2Turn, "version", 1))
	statePath := filepath.Join(f.stateDir, "own.json")

	sourceBody := "short notice body"
	next := nativeClone(state.Get("inbox_attempt"))
	next.Set("attempt_id", value.Text(stage2Execution))
	next.Set("batch_root", value.Text(stage2Attempt))
	next.Set("batch_index", value.IntegerText("2"))
	next.Set("batch_limit", value.IntegerText("3"))

	host := NewFileHost(FileHostOptions{
		Profile:       f.profile,
		Compatibility: f.compat,
		Call: func(_ context.Context, _ *value.Value, op string, req *value.Value, _ NativeCallOptions) (*value.Value, error) {
			switch op {
			case "event-renew":
				d := nativeClone(delivery)
				d.Set("state", value.Text("leased"))
				return d, nil
			case "history":
				sum := sha256.Sum256([]byte(sourceBody))
				return nativeMap("record_id", stage2Turn, "historical", true, "versions", &value.Value{Kind: value.Array, Items: []*value.Value{nativeMap("version", 1, "payload_available", true, "body", sourceBody, "body_bytes", len(sourceBody), "body_sha256", hex.EncodeToString(sum[:]))}}), nil
			case "session-inbox-advance":
				n := nativeClone(next)
				n.Set("attempt_id", req.Get("request_id"))
				return nativeMap("attempt", n, "remainder", "none"), nil
			case "session-inbox-reconcile":
				return nil, nativeRefusal("DELIVERY_ACTIVE", "active")
			}
			t.Fatal("unexpected op", op)
			return nil, nil
		},
	})
	c := &CLI{
		Host:           host,
		Profile:        f.profile,
		Compatibility:  f.compat,
		Root:           f.root,
		Now:            func() time.Time { return time.Unix(100, 0) },
		Env:            func(string) string { return "" },
		CallerDescends: func(*value.Value) (bool, error) { return true, nil },
		Engine:         &RecoveryEngine{Hooks: RecoveryHooks{Alive: func(*value.Value) (bool, error) { return true, nil }}},
	}

	rendered, err := c.RenderActiveAttemptContext(context.Background(), f.config, state, statePath)
	if err != nil || rendered == nil || !strings.Contains(nativeText(rendered), "version 1.") || !strings.Contains(nativeText(rendered), f.launcher+" inbox compact settle abababababababab handled") || !strings.Contains(nativeText(rendered), f.launcher+" inbox compact advance abababababababab") {
		t.Fatal(rendered, err)
	}
	stage2WriteValue(t, f.profile, statePath, state)

	// Compact read and inspect resolve versioned generation config path via Compatibility.ConfigPath (bindings/fixture.json does not exist).
	code, readRes, stderr := stage2CLIResult(t, c, "inbox", "compact", "read", "abababababababab")
	if code != 0 || stderr != "" || !exactTrue(readRes.Get("ok")) || nativeText(readRes.Get("body")) != "short notice body" {
		t.Fatal(code, readRes, stderr)
	}
	code, inspRes, stderr := stage2CLIResult(t, c, "inbox", "compact", "inspect", "abababababababab")
	if code != 0 || stderr != "" || !exactTrue(inspRes.Get("ok")) {
		t.Fatal(code, inspRes, stderr)
	}

	// JSON-escaped body > 4096 bytes uses fallback control even when raw UTF-8 len <= 4096.
	sourceBody = strings.Repeat("\n", 2050)
	renderedEscaped, err := c.RenderActiveAttemptContext(context.Background(), f.config, state, statePath)
	if err != nil || strings.Contains(nativeText(renderedEscaped), "Source text (sender data") || !strings.Contains(nativeText(renderedEscaped), f.launcher+" inbox compact read abababababababab") {
		t.Fatal("expected JSON-escaped >4096 body to use compact read fallback", renderedEscaped, err)
	}
	sourceBody = "short notice body"
	_, _ = c.RenderActiveAttemptContext(context.Background(), f.config, state, statePath)
	stage2WriteValue(t, f.profile, statePath, state)

	// Advance uses RenderActiveAttemptContext when NextContext == nil and Compatibility != nil.
	f.compat.RandomBytes = func(n int) ([]byte, error) { return bytes.Repeat([]byte{0xcd}, n), nil }
	code, advRes, stderr := stage2CLIResult(t, c, "inbox", "compact", "advance", "abababababababab")
	if code != 0 || stderr != "" || !strings.Contains(nativeText(advRes.Get("next")), f.launcher+" inbox compact settle cdcdcdcdcdcdcdcd handled") {
		t.Fatal(code, advRes, stderr)
	}

	// Wrong CAIRN_INBOX_ROOT or unselected binding in compact-index.json fails BindLookup preflight with exit 1 and empty stdout.
	cWrongRoot := *c
	cWrongRoot.Root = t.TempDir()
	var outBuf, errBuf bytes.Buffer
	code, err = cWrongRoot.RunCLI(context.Background(), []string{"inbox", "compact", "read", "cdcdcdcdcdcdcdcd"}, strings.NewReader(""), &outBuf, &errBuf)
	if err != nil || code != 1 || outBuf.Len() != 0 || errBuf.String() != "Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n" {
		t.Fatal(code, outBuf.String(), errBuf.String(), err)
	}
	idxPath := filepath.Join(f.stateDir, "compact", "cdcdcdcdcdcdcdcd.json")
	idxObj, err := c.read(idxPath, SiteCompactIndexRead)
	if err != nil {
		t.Fatal(err)
	}
	idxObj.Set("binding", value.Text("other-binding"))
	writePrivateFile(t, idxPath, encodeFixtureJSON(t, idxObj), 0o600)
	outBuf.Reset()
	errBuf.Reset()
	code, err = c.RunCLI(context.Background(), []string{"inbox", "compact", "read", "cdcdcdcdcdcdcdcd"}, strings.NewReader(""), &outBuf, &errBuf)
	if err != nil || code != 1 || outBuf.Len() != 0 || errBuf.String() != "Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n" {
		t.Fatal(code, outBuf.String(), errBuf.String(), err)
	}

	// Legacy /2 CLI (Compatibility == nil) renders cairn-inbox commands.
	cLegacy := *c
	cLegacy.Compatibility = nil
	cLegacy.Command = []string{"/usr/bin/python3", "/coord.py"}
	legRendered, err := cLegacy.RenderActiveAttemptContext(context.Background(), f.config, state, statePath)
	if err != nil || !strings.Contains(nativeText(legRendered), "cairn-inbox settle ") {
		t.Fatal(legRendered, err)
	}

	// OpenCode cancel fence: when harness == opencode and session-inbox-control returns an unconfirmed cancel, RenderActiveAttemptContext returns "".
	ocCfg := nativeClone(f.config)
	ocCfg.Set("harness", value.Text("opencode"))
	ocHost := NewFileHost(FileHostOptions{
		Profile:       f.profile,
		Compatibility: f.compat,
		Call: func(_ context.Context, _ *value.Value, op string, _ *value.Value, _ NativeCallOptions) (*value.Value, error) {
			if op == "session-inbox-control" {
				cancelled := nativeClone(state.Get("inbox_attempt"))
				cancelled.Set("cancel", nativeMap("requested_at", 1))
				return nativeMap("attempt", cancelled), nil
			}
			t.Fatalf("unexpected op under opencode cancel fence: %s", op)
			return nil, nil
		},
	})
	cOC := *c
	cOC.Host = ocHost
	ocRendered, err := cOC.RenderActiveAttemptContext(context.Background(), ocCfg, nativeClone(state), statePath)
	if err != nil || nativeText(ocRendered) != "" {
		t.Fatalf("expected empty context under opencode cancel fence, got %v err=%v", ocRendered, err)
	}

	// allocateCompactHandle raises EISDIR if an expired candidate *.json entry in compact/ is a directory.
	probeDir := filepath.Join(f.stateDir, "compact", "probe-dir.json")
	if err := os.MkdirAll(probeDir, 0o700); err != nil {
		t.Fatal(err)
	}
	cExpired := *c
	cExpired.Now = func() time.Time { return time.Unix(10000, 0) }
	_, _, err = cExpired.CompactInboxHandle(f.config, state, statePath, filepath.Join(f.stateDir, "inbox", "nonexistent.json"), nativeMap("attempt_id", "x", "delivery_id", "y", "source", value.Mapping()))
	if !errors.Is(err, syscall.EISDIR) {
		t.Fatalf("expected EISDIR on directory candidate in compact/, got %v", err)
	}
	_ = os.Remove(probeDir)
}

func TestCompatibilityReminderCueAndDeadline(t *testing.T) {
	f := newCompatFixture(t)
	state := stage2State()
	statePath := filepath.Join(f.stateDir, "own.json")
	delivery := state.Get("inbox_attempt").Get("delivery")
	delivery.Get("event").Set("kind", value.Text("request"))
	delivery.Get("event").Set("from", value.Text("agent/sender"))
	delivery.Get("event").Set("ref", nativeMap("record_id", stage2Turn, "version", 1))

	host := NewFileHost(FileHostOptions{
		Profile:       f.profile,
		Compatibility: f.compat,
		Call: func(_ context.Context, _ *value.Value, op string, _ *value.Value, _ NativeCallOptions) (*value.Value, error) {
			if op == "event-renew" {
				d := nativeClone(delivery)
				d.Set("state", value.Text("leased"))
				return d, nil
			}
			if op == "session-inbox-reconcile" {
				return nil, nativeRefusal("DELIVERY_ACTIVE", "active")
			}
			return nativeMap("ok", true), nil
		},
	})
	c := &CLI{Host: host, Profile: f.profile, Compatibility: f.compat, Root: f.root}
	rendered, err := c.RenderActiveAttemptContext(context.Background(), f.config, state, statePath)
	if err != nil || !strings.Contains(nativeText(rendered), "Request finish/reply command: "+f.launcher+" inbox --context ") {
		t.Fatal(rendered, err)
	}

	ns := &NativeSession{Config: f.config, Path: statePath, Record: state, Host: host}
	rem, err := ns.UnacknowledgedTurnEndReminder(context.Background(), nativeMap("native_turn_id", stage2Turn))
	if err != nil || !strings.Contains(rem, f.launcher+" inbox --context ") || !strings.HasSuffix(rem, "finish-reply. Ending the turn again without completion will record this delivery as processing_failed.") || len(rem) > 2048 {
		t.Fatal(rem, err)
	}

	// Over-budget reminder (> 2048 bytes) fails Compatibility.Control with ValueError before recording inbox_ack_reminded.
	nativePop(state, "inbox_ack_reminded", "inbox_ack_reminder_revision")
	ctxPath := filepath.Join(f.stateDir, "inbox", stage2Attempt+".json")
	ctxObj, err := c.read(ctxPath, SiteInboxContextRead)
	if err != nil {
		t.Fatal(err)
	}
	ctxObj.Set("compact_source_unavailable", value.Boolean(true))
	ctxObj.Set("compact_fallback_control", value.Text(strings.Repeat("x", 2049)))
	writePrivateFile(t, ctxPath, encodeFixtureJSON(t, ctxObj), 0o600)
	_, err = ns.UnacknowledgedTurnEndReminder(context.Background(), nativeMap("native_turn_id", stage2Turn))
	stage2ErrorClass(t, err, "ValueError")
	if state.Get("inbox_ack_reminded") != nil {
		t.Fatal("over-budget reminder must not persist inbox_ack_reminded")
	}

	// InboxCueText under /3 vs /2.
	pending := nativeMap("requests", 1, "notices", 2, "responses", 0, "truncated", false)
	agent := state.Get("agent")
	cue3, err := c.InboxCueText(pending, f.config, agent, f.configPath)
	if err != nil || !strings.Contains(cue3, "`"+f.launcher+" inbox drain --config "+f.configPath) || len(cue3) > 2048 {
		t.Fatal(cue3, err)
	}
	c2 := *c
	c2.Compatibility = nil
	c2.Command = []string{"/usr/bin/python3", "/coord.py"}
	cue2, err := c2.InboxCueText(pending, f.config, agent, f.configPath)
	if err != nil || !strings.Contains(cue2, "cairn inbox next --token-file") {
		t.Fatal(cue2, err)
	}

	// Hook deadline capping in sourceCallWithCompatibility vs inbox subcommand.
	f.compat.Now = func() float64 { return 205 }
	callFn := sourceCallWithCompatibility(f.profile, journal.RunnerFunc(func(_ context.Context, _ []string, _ string) (journal.Result, error) {
		t.Fatal("runner must not execute when hook deadline is expired")
		return journal.Result{}, nil
	}), f.compat)
	_, err = callFn(context.Background(), f.config, "event-renew", value.Mapping(), NativeCallOptions{})
	if !errors.Is(err, syscall.ETIMEDOUT) {
		t.Fatalf("expected ETIMEDOUT on expired hook deadline, got %v", err)
	}
	f.compat.Tail = []string{"inbox", "drain"}
	if remDur, err := f.compat.Remaining(4 * time.Second); err != nil || remDur != 4*time.Second {
		t.Fatal("inbox subcommand must not be capped by hook deadline", remDur, err)
	}
	if got := f.compat.CapDeadline(250); got != 250 {
		t.Fatalf("inbox subcommand CapDeadline = %v, want 250", got)
	}
	f.compat.Tail = []string{"hook"}
	if got := f.compat.CapDeadline(250); got != 200 {
		t.Fatalf("hook CapDeadline = %v, want 200", got)
	}

	// EffectiveConfig validates hook_event_name type, hook timeout table, and activation result.
	loadedCfg, err := f.compat.Config(f.profile, f.configPath)
	if err != nil {
		t.Fatal(err)
	}
	_, err = f.compat.EffectiveConfig(f.profile, loadedCfg, nativeMap("hook_event_name", &value.Value{Kind: value.Array}), []byte("{}"))
	stage2ErrorClass(t, err, "TypeError")
	effCfg, err := f.compat.EffectiveConfig(f.profile, loadedCfg, nativeMap("hook_event_name", "UserPromptSubmit"), []byte("{}"))
	if err != nil || effCfg != loadedCfg {
		t.Fatal(effCfg, err)
	}
}

func TestCompatibilityDifferentialPythonParity(t *testing.T) {
	f := newCompatFixture(t)
	state := stage2State()
	statePath := filepath.Join(f.stateDir, "own.json")
	delivery := state.Get("inbox_attempt").Get("delivery")
	delivery.Get("event").Set("kind", value.Text("request"))
	delivery.Get("event").Set("from", value.Text("agent/sender"))
	delivery.Get("event").Set("ref", nativeMap("record_id", stage2Turn, "version", 1))

	sourceBody := "short notice body"
	sourceUnavailable := false
	host := NewFileHost(FileHostOptions{
		Profile:       f.profile,
		Compatibility: f.compat,
		Call: func(_ context.Context, _ *value.Value, op string, _ *value.Value, _ NativeCallOptions) (*value.Value, error) {
			switch op {
			case "event-renew":
				d := nativeClone(delivery)
				d.Set("state", value.Text("leased"))
				return d, nil
			case "history":
				if sourceUnavailable {
					return nil, nativeRefusal("SOURCE_UNAVAILABLE", "missing")
				}
				sum := sha256.Sum256([]byte(sourceBody))
				return nativeMap("record_id", stage2Turn, "historical", true, "versions", &value.Value{Kind: value.Array, Items: []*value.Value{nativeMap("version", 1, "payload_available", true, "body", sourceBody, "body_bytes", len(sourceBody), "body_sha256", hex.EncodeToString(sum[:]))}}), nil
			case "session-inbox-reconcile":
				return nil, nativeRefusal("DELIVERY_ACTIVE", "active")
			}
			return nativeMap("ok", true), nil
		},
	})
	c := &CLI{
		Host:          host,
		Profile:       f.profile,
		Compatibility: f.compat,
		Root:          f.root,
		Now:           func() time.Time { return time.Unix(100, 0) },
	}
	c.Host.NewID = func() string { return "00000000-0000-4000-8000-000000000099" }

	initialState := nativeClone(state)
	goPromptVal, err := c.RenderActiveAttemptContext(context.Background(), f.config, state, statePath)
	if err != nil {
		t.Fatal(err)
	}
	ctxPath := filepath.Join(f.stateDir, "inbox", stage2Attempt+".json")
	goReqCtxBytes, err := os.ReadFile(ctxPath)
	if err != nil {
		t.Fatal(err)
	}
	goReqStateBytes, err := os.ReadFile(statePath)
	if err != nil {
		t.Fatal(err)
	}
	goCtxObj, err := c.read(ctxPath, SiteInboxContextRead)
	if err != nil {
		t.Fatal(err)
	}
	goShellCmds := encodeFixtureJSON(t, goCtxObj.Get("shell_commands"))
	ns := &NativeSession{Config: f.config, Path: statePath, Record: state, Host: host}
	goReminder, err := ns.UnacknowledgedTurnEndReminder(context.Background(), nativeMap("native_turn_id", stage2Turn))
	if err != nil {
		t.Fatal(err)
	}
	pending := nativeMap("requests", 1, "notices", 2, "responses", 0, "truncated", true)
	goCue, err := c.InboxCueText(pending, f.config, state.Get("agent"), f.configPath)
	if err != nil {
		t.Fatal(err)
	}

	// Compact notice variants: inline, >4096 fallback, and unavailable source + reminder reuse.
	delivery.Get("event").Set("kind", value.Text("notice"))
	_ = os.Remove(ctxPath)
	_ = os.RemoveAll(filepath.Join(f.stateDir, "compact"))
	goCompactInline, err := c.RenderActiveAttemptContext(context.Background(), f.config, state, statePath)
	if err != nil {
		t.Fatal(err)
	}
	handlePath := filepath.Join(f.stateDir, "compact", "abababababababab.json")
	goCompactCtxBytes, err := os.ReadFile(ctxPath)
	if err != nil {
		t.Fatal(err)
	}
	goCompactHandleBytes, err := os.ReadFile(handlePath)
	if err != nil {
		t.Fatal(err)
	}
	sourceBody = strings.Repeat("\n", 2050)
	goCompactLarge, err := c.RenderActiveAttemptContext(context.Background(), f.config, state, statePath)
	if err != nil {
		t.Fatal(err)
	}
	sourceUnavailable = true
	goCompactUnavail, err := c.RenderActiveAttemptContext(context.Background(), f.config, state, statePath)
	if err != nil {
		t.Fatal(err)
	}
	nativePop(state, "inbox_ack_reminded", "inbox_ack_reminder_revision")
	goUnavailRem, err := ns.UnacknowledgedTurnEndReminder(context.Background(), nativeMap("native_turn_id", stage2Turn))
	if err != nil {
		t.Fatal(err)
	}
	_ = os.Remove(ctxPath)
	_ = os.RemoveAll(filepath.Join(f.stateDir, "compact"))
	stage2WriteValue(t, f.profile, statePath, initialState)

	pyScript := `import hashlib, json, os, sys, types
from pathlib import Path
sys.path.insert(0, "../../../integrations/lifecycle")
import coordination, inbox_binding, lifecycle_compatibility
cfg_path, rec_path, rec_sha, l_root, l_path, state_path, turn_id, ctx_path = sys.argv[1:9]
vec = ["/usr/bin/python3", "-I", "-S", "-c", "pass", "/entry.py", cfg_path, rec_path, rec_sha]
args = types.SimpleNamespace(
    role="coordination", entrypoint="coordination.py", config=cfg_path,
    hook_timeout="15", launch_root=l_root, shell="/bin/sh", tail=[],
    receipt=rec_path, receipt_sha256=rec_sha,
)
rec = json.loads(Path(rec_path).read_bytes())
opened = lambda p, limit=65536: (Path(p).read_bytes(), hashlib.sha256(Path(p).read_bytes()).hexdigest())
ctx = lifecycle_compatibility.InvocationContext(args, rec, {}, -1, 200.0, vec, opened)
ctx.bundle = types.SimpleNamespace(load=lambda name: inbox_binding if name == "inbox_binding" else None)
coordination._BUNDLE.compatibility_context = ctx
os.urandom = lambda n: b"\xab" * n
coordination.time.time = lambda: 100.0
cfg = coordination.load_config(Path(cfg_path))
state = json.loads(Path(state_path).read_bytes())
state.pop("inbox_ack_reminded", None)
state.pop("inbox_ack_reminder_revision", None)
coordination.write_state(Path(state_path), state)
source_body = "short notice body"
source_unavail = False
def fake_call(c, op, req, session=None, timeout=4):
    if op == "event-renew":
        return dict(state["inbox_attempt"]["delivery"], state="leased")
    if op == "history":
        if source_unavail:
            raise coordination.CoordinationError("SOURCE_UNAVAILABLE", "missing")
        b = source_body.encode("utf-8")
        return {"record_id": turn_id, "historical": True, "versions": [{"version": 1, "payload_available": True, "body": source_body, "body_bytes": len(b), "body_sha256": hashlib.sha256(b).hexdigest()}]}
    if op == "session-inbox-reconcile":
        raise coordination.CoordinationError("DELIVERY_ACTIVE", "active")
    return {"ok": True}
coordination.call = fake_call
coordination.uuid.uuid4 = lambda: "00000000-0000-4000-8000-000000000099"
obs = {"event": "UserPromptSubmit", "phase": "active", "native_turn_id": turn_id}
prompt = coordination.inbox_context(cfg, state, Path(state_path), obs)
req_ctx_raw = Path(ctx_path).read_bytes().decode("utf-8")
req_state_raw = Path(state_path).read_bytes().decode("utf-8")
shell_cmds = json.loads(req_ctx_raw)["shell_commands"]
rem = coordination.unacknowledged_turn_end_reminder(cfg, state, Path(state_path), {"native_turn_id": turn_id})
cue = coordination.inbox_cue_text({"requests": 1, "notices": 2, "responses": 0, "truncated": True}, cfg, state["agent"])
state["inbox_attempt"]["delivery"]["event"]["kind"] = "notice"
Path(ctx_path).unlink(missing_ok=True)
c_inline = coordination.inbox_context(cfg, state, Path(state_path), obs)
c_ctx_raw = Path(ctx_path).read_bytes().decode("utf-8")
c_handle_raw = (Path(cfg["state_dir"]) / "compact" / "abababababababab.json").read_bytes().decode("utf-8")
source_body = "\n" * 2050
c_large = coordination.inbox_context(cfg, state, Path(state_path), obs)
source_unavail = True
c_unavail = coordination.inbox_context(cfg, state, Path(state_path), obs)
state.pop("inbox_ack_reminded", None)
state.pop("inbox_ack_reminder_revision", None)
u_rem = coordination.unacknowledged_turn_end_reminder(cfg, state, Path(state_path), {"native_turn_id": turn_id})
print(json.dumps({"prompt": prompt, "req_ctx_raw": req_ctx_raw, "req_state_raw": req_state_raw, "shell_commands": shell_cmds, "reminder": rem, "cue": cue, "c_inline": c_inline, "c_ctx_raw": c_ctx_raw, "c_handle_raw": c_handle_raw, "c_large": c_large, "c_unavail": c_unavail, "u_rem": u_rem}))
`
	res, err := (journal.CommandRunner{}).Run(context.Background(), []string{
		"/usr/bin/python3", "-I", "-B", "-c", pyScript,
		f.configPath, f.receiptPath, f.compat.ReceiptSHA256, f.launchRoot, f.launcher, statePath, stage2Turn, ctxPath,
	}, "")
	if err != nil || res.ExitCode != 0 {
		t.Fatalf("python parity script failed: err=%v exit=%d stderr=%s", err, res.ExitCode, res.Stderr)
	}
	pyOut, err := value.DecodeBytes([]byte(res.Stdout), 0)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(encodeFixtureJSON(t, pyOut.Get("shell_commands")), goShellCmds) {
		t.Fatalf("shell_commands mismatch:\nGo: %s\nPy: %s", goShellCmds, encodeFixtureJSON(t, pyOut.Get("shell_commands")))
	}
	for _, tc := range []struct{ key, goVal string }{
		{"prompt", nativeText(goPromptVal)},
		{"req_ctx_raw", string(goReqCtxBytes)},
		{"req_state_raw", string(goReqStateBytes)},
		{"reminder", goReminder},
		{"cue", goCue},
		{"c_inline", nativeText(goCompactInline)},
		{"c_ctx_raw", string(goCompactCtxBytes)},
		{"c_handle_raw", string(goCompactHandleBytes)},
		{"c_large", nativeText(goCompactLarge)},
		{"c_unavail", nativeText(goCompactUnavail)},
		{"u_rem", goUnavailRem},
	} {
		if nativeText(pyOut.Get(tc.key)) != tc.goVal {
			t.Fatalf("%s mismatch:\nGo: %q\nPy: %q", tc.key, tc.goVal, nativeText(pyOut.Get(tc.key)))
		}
	}
}

func runPythonVerifierLiteral(t *testing.T, argv []string) bool {
	t.Helper()
	pyScript := `import sys
sys.path.insert(0, "../../../integrations/lifecycle")
import lifecycle_compatibility as lc
sys.argv = ["verifier"] + sys.argv[1:]
try:
    exec(compile(lc.VERIFIER_LITERAL, "<verifier>", "exec"), {"__name__": "__main__"})
    print("OK")
except SystemExit as e:
    print("OK" if e.code == 0 else "REFUSED")
`
	res, err := (journal.CommandRunner{}).Run(context.Background(), append([]string{"/usr/bin/python3", "-I", "-B", "-c", pyScript}, argv...), "")
	if err != nil || res.ExitCode != 0 {
		t.Fatalf("python verifier script failed: err=%v exit=%d stderr=%s", err, res.ExitCode, res.Stderr)
	}
	out := strings.TrimSpace(res.Stdout)
	if out != "OK" && out != "REFUSED" {
		t.Fatalf("unexpected python verifier output: %q (argv=%v)", out, argv)
	}
	return out == "OK"
}

func TestCompatibilityReceiptVerifierPythonParity(t *testing.T) {
	baseArgv := func(f *compatFixture) []string {
		return []string{
			"--receipt", f.receiptPath,
			"--receipt-sha256", f.compat.ReceiptSHA256,
			"--role", "coordination",
			"--entrypoint", "coordination.py",
			"--config", f.configPath,
			"--hook-timeout", "15",
			"--launch-root", f.launchRoot,
			"--shell", DefaultCompatibilityShell,
			"--", "inbox", "drain",
		}
	}
	readRaw := func(p string) []byte {
		b, err := os.ReadFile(p)
		if err != nil {
			t.Fatal(err)
		}
		return b
	}
	checkParity := func(name string, f *compatFixture, argv []string, wantOK bool) {
		t.Helper()
		_, _, goErr := ParseCompatibilityCommand(argv, f.compat.Vector, f.profile)
		goOK := goErr == nil
		pyOK := runPythonVerifierLiteral(t, argv)
		if goOK != wantOK || pyOK != wantOK {
			t.Fatalf("%s parity mismatch: wantOK=%v goOK=%v (err=%v) pyOK=%v", name, wantOK, goOK, goErr, pyOK)
		}
	}

	fValid := newCompatFixture(t)
	checkParity("valid_base", fValid, baseArgv(fValid), true)
	if err := fValid.compat.VerifyReceipt(fValid.profile); err != nil {
		t.Fatalf("VerifyReceipt on valid fixture failed: %v", err)
	}
	if _, _, err := ParseCompatibilityCommand(baseArgv(fValid), nil, fValid.profile); err == nil {
		t.Fatal("expected ParseCompatibilityCommand without a launch vector to fail closed")
	}

	// Valid receipt with non-ASCII alias (`coord_選.py` -> `coordination.py`) and dash-prefixed alias (`-x.py` -> `coordination.py`).
	fAlias := newCompatFixture(t)
	recAlias, err := value.DecodeBytes(readRaw(fAlias.receiptPath), 0)
	if err != nil {
		t.Fatal(err)
	}
	snapRootOld := nativeText(recAlias.Get("snapshot_root"))
	coordRaw := readRaw(filepath.Join(snapRootOld, "coordination.py"))
	coordSum := sha256.Sum256(coordRaw)
	coordHex := hex.EncodeToString(coordSum[:])
	recAlias.Get("aliases").Set("coord_選.py", value.Text("coordination.py"))
	recAlias.Get("aliases").Set("-x.py", value.Text("coordination.py"))
	recAlias.Get("members").Set("coord_選.py", nativeMap("sha256", coordHex, "bytes", len(coordRaw)))
	recAlias.Get("members").Set("-x.py", nativeMap("sha256", coordHex, "bytes", len(coordRaw)))
	hashes := make(map[string]string)
	for _, m := range recAlias.Get("members").Members {
		hashes[string(m.Key)] = nativeText(m.Value.Get("sha256"))
	}
	snapRootNew := filepath.Join(filepath.Dir(snapRootOld), CompatibilitySnapshotIdentity(hashes))
	if err := os.Rename(snapRootOld, snapRootNew); err != nil {
		t.Fatal(err)
	}
	writePrivateFile(t, filepath.Join(snapRootNew, "coord_選.py"), coordRaw, 0o400)
	writePrivateFile(t, filepath.Join(snapRootNew, "-x.py"), coordRaw, 0o400)
	recAlias.Set("snapshot_root", value.Text(snapRootNew))
	fAlias.compat.ReceiptSHA256 = writePrivateFile(t, fAlias.receiptPath, encodeFixtureJSON(t, recAlias), 0o600)
	aliasArgv := baseArgv(fAlias)
	aliasArgv[7] = "coord_選.py"
	checkParity("valid_non_ascii_alias", fAlias, aliasArgv, true)

	// `--entrypoint=-x.py` is accepted by both, whereas separate `--entrypoint -x.py` is refused by both.
	dashEqArgv := append([]string{"--entrypoint=-x.py"}, baseArgv(fAlias)...)
	// Remove the separate `--entrypoint coordination.py` pair at indices 7..8.
	dashEqArgv = append(dashEqArgv[:7], dashEqArgv[9:]...)
	checkParity("valid_dash_alias_equals_form", fAlias, dashEqArgv, true)
	dashSepArgv := baseArgv(fAlias)
	dashSepArgv[7] = "-x.py"
	checkParity("refuse_dash_alias_separate_form", fAlias, dashSepArgv, false)

	// Refusal cases: selection, timeout (including hex floats), receipt pin, duplicate key, fields/schema/provenance/roles/aliases/closure/pins/binary.
	badRole := baseArgv(fValid)
	badRole[5] = "other"
	checkParity("refuse_role", fValid, badRole, false)
	badTimeout := baseArgv(fValid)
	badTimeout[11] = "16"
	checkParity("refuse_timeout_range", fValid, badTimeout, false)
	for _, hexTimeout := range []string{"15x", "0xfp0", "0x1.ep3", "0XFP0"} {
		badTimeoutSyntax := baseArgv(fValid)
		badTimeoutSyntax[11] = hexTimeout
		checkParity("refuse_timeout_"+hexTimeout, fValid, badTimeoutSyntax, false)
	}
	badShell := baseArgv(fValid)
	badShell[15] = "bin/sh"
	checkParity("refuse_shell", fValid, badShell, false)
	badPin := baseArgv(fValid)
	badPin[3] = strings.Repeat("0", 64)
	checkParity("refuse_receipt_pin", fValid, badPin, false)
	noSep := append(baseArgv(fValid)[:16:16], "inbox", "drain")
	checkParity("refuse_missing_separator", fValid, noSep, false)

	mutateReceipt := func(name string, mutate func(f *compatFixture, rec *value.Value) []byte) {
		f := newCompatFixture(t)
		rec, err := value.DecodeBytes(readRaw(f.receiptPath), 0)
		if err != nil {
			t.Fatal(err)
		}
		raw := mutate(f, rec)
		if raw == nil {
			raw = encodeFixtureJSON(t, rec)
		}
		f.compat.ReceiptSHA256 = writePrivateFile(t, f.receiptPath, raw, 0o600)
		checkParity(name, f, baseArgv(f), false)
	}
	mutateReceipt("refuse_duplicate_key", func(_ *compatFixture, rec *value.Value) []byte {
		raw := encodeFixtureJSON(t, rec)
		return append([]byte(`{"schema":"cairn.lifecycle-compatibility/1",`), raw[1:]...)
	})
	mutateReceipt("refuse_extra_field", func(_ *compatFixture, rec *value.Value) []byte {
		rec.Set("extra", value.Text("1"))
		return nil
	})
	mutateReceipt("refuse_provenance_surrogate", func(_ *compatFixture, rec *value.Value) []byte {
		return bytes.Replace(encodeFixtureJSON(t, rec), []byte(`"test-rev"`), []byte(`"rev-\ud800"`), 1)
	})
	mutateReceipt("refuse_binding_schema", func(_ *compatFixture, rec *value.Value) []byte {
		rec.Get("binding").Set("schema", value.Text("cairn.inbox-recall-binding/2"))
		return nil
	})
	mutateReceipt("refuse_closure_mismatch", func(_ *compatFixture, rec *value.Value) []byte {
		rec.Get("aliases").Set("extra.py", value.Text("coordination.py"))
		return nil
	})
	mutateReceipt("refuse_snapshot_identity", func(f *compatFixture, rec *value.Value) []byte {
		oldSnap := nativeText(rec.Get("snapshot_root"))
		wrongSnap := filepath.Join(filepath.Dir(oldSnap), strings.Repeat("b", 64))
		_ = os.Rename(oldSnap, wrongSnap)
		rec.Set("snapshot_root", value.Text(wrongSnap))
		return nil
	})
	mutateReceipt("refuse_member_bytes_bool", func(_ *compatFixture, rec *value.Value) []byte {
		rec.Get("members").Get("coordination.py").Set("bytes", value.Boolean(true))
		return nil
	})
	mutateReceipt("refuse_member_disk_tamper", func(_ *compatFixture, rec *value.Value) []byte {
		snap := nativeText(rec.Get("snapshot_root"))
		p := filepath.Join(snap, "coordination.py")
		_ = os.Chmod(p, 0o600)
		_ = os.WriteFile(p, []byte("# tampered\n"), 0o400)
		return nil
	})
	mutateReceipt("refuse_binary_not_executable", func(_ *compatFixture, rec *value.Value) []byte {
		_ = os.Chmod(nativeText(rec.Get("binary").Get("path")), 0o600)
		return nil
	})
}

func TestCompatibilityWatchMultiProfileAndPreflight(t *testing.T) {
	f := newCompatFixture(t)
	cfgDir := filepath.Dir(f.configPath)
	secondStateDir := filepath.Join(f.root, "state", "fixture-two")
	if err := os.MkdirAll(filepath.Join(secondStateDir, "inbox"), 0o700); err != nil {
		t.Fatal(err)
	}
	f.config.Set("config_home", value.Text(filepath.Join(f.root, "home-0")))
	coord0SHA := writePrivateFile(t, f.configPath, encodeFixtureJSON(t, f.config), 0o600)
	secondCfg := nativeClone(f.config)
	secondCfg.Set("state_dir", value.Text(secondStateDir))
	secondCfg.Set("config_home", value.Text(filepath.Join(f.root, "home-1")))
	secondCfg.Set("binding", value.Text("fixture-two"))
	secondPath := filepath.Join(cfgDir, "coordination-1.json")
	secondSHA := writePrivateFile(t, secondPath, encodeFixtureJSON(t, secondCfg), 0o600)

	receipt, err := value.DecodeBytes(func() []byte { b, _ := os.ReadFile(f.receiptPath); return b }(), 0)
	if err != nil {
		t.Fatal(err)
	}
	coordDesc := &value.Value{Kind: value.Array, Items: []*value.Value{
		nativeMap("path", f.configPath, "sha256", coord0SHA),
		nativeMap("path", secondPath, "sha256", secondSHA),
	}}
	receipt.Set("coordination_configs", coordDesc)
	manifest, err := value.DecodeBytes(func() []byte { b, _ := os.ReadFile(f.bindingPath); return b }(), 0)
	if err != nil {
		t.Fatal(err)
	}
	manifest.Set("coordination_configs", coordDesc)
	bindingSHA := writePrivateFile(t, f.bindingPath, encodeFixtureJSON(t, manifest), 0o600)
	receipt.Get("binding").Set("sha256", value.Text(bindingSHA))
	f.compat.ReceiptSHA256 = writePrivateFile(t, f.receiptPath, encodeFixtureJSON(t, receipt), 0o600)
	f.compat.Receipt = nil
	f.compat.Vector[8] = f.compat.ReceiptSHA256
	launcherRaw, err := LauncherBytes(f.compat.Vector, DefaultCompatibilityShell)
	if err != nil {
		t.Fatal(err)
	}
	firstLauncher := LauncherPath(f.launchRoot, launcherRaw)
	writePrivateFile(t, firstLauncher, launcherRaw, 0o500)
	secondVec := vectorWithConfig(f.compat.Vector, f.configPath, secondPath)
	secondLauncherRaw, err := LauncherBytes(secondVec, DefaultCompatibilityShell)
	if err != nil {
		t.Fatal(err)
	}
	secondLauncher := LauncherPath(f.launchRoot, secondLauncherRaw)
	writePrivateFile(t, secondLauncher, secondLauncherRaw, 0o500)

	args := []string{
		"--receipt", f.receiptPath,
		"--receipt-sha256", f.compat.ReceiptSHA256,
		"--role", "coordination",
		"--entrypoint", "coordination.py",
		"--config", f.configPath,
		"--hook-timeout", "15",
		"--launch-root", f.launchRoot,
		"--shell", DefaultCompatibilityShell,
		"--", "watch", "--config-dir", cfgDir, "--once",
	}
	compat, tail, err := ParseCompatibilityCommand(args, f.compat.Vector, f.profile)
	if err != nil {
		t.Fatalf("ParseCompatibilityCommand watch failed: %v", err)
	}
	compat.Now = func() float64 { return compat.Deadline + 60 }
	if compat.isHook() {
		t.Fatal("expected isHook() == false for watch tail")
	}
	if rem, err := compat.Remaining(30 * time.Second); err != nil || rem != 30*time.Second {
		t.Fatalf("expected watch Remaining uncapped at 30s past hook deadline, got rem=%v err=%v", rem, err)
	}

	host := NewFileHost(FileHostOptions{
		Profile:       f.profile,
		Compatibility: compat,
	})
	cli := &CLI{
		Host:          host,
		Profile:       f.profile,
		Compatibility: compat,
		Root:          f.root,
	}
	var outBuf, errBuf bytes.Buffer
	code, err := cli.RunCLI(context.Background(), tail, strings.NewReader(""), &outBuf, &errBuf)
	if err != nil || code != 0 || outBuf.Len() != 0 || errBuf.Len() != 0 {
		t.Fatalf("expected watch --once exit 0, got code=%d err=%v out=%q stderr=%q", code, err, outBuf.String(), errBuf.String())
	}
	if _, err := compat.Config(f.profile, secondPath); err != nil {
		t.Fatalf("expected watch Config(secondPath) ok: %v", err)
	}
	issuedSecond, err := compat.IssuedArgv([]string{"inbox"})
	if err != nil || len(issuedSecond) != 2 || issuedSecond[0] != secondLauncher {
		t.Fatalf("expected IssuedArgv on secondPath to select secondLauncher=%q, got %v err=%v", secondLauncher, issuedSecond, err)
	}
	if _, err := compat.Config(f.profile, f.configPath); err != nil {
		t.Fatalf("expected watch Config(firstPath) ok: %v", err)
	}
	issuedFirst, err := compat.IssuedArgv([]string{"inbox"})
	if err != nil || len(issuedFirst) != 2 || issuedFirst[0] != firstLauncher {
		t.Fatalf("expected IssuedArgv on firstPath to select firstLauncher=%q, got %v err=%v", firstLauncher, issuedFirst, err)
	}

	// Missing second launcher fails preflight with exit 1 and zero lock effect.
	_ = os.Remove(secondLauncher)
	_ = os.Remove(filepath.Join(cfgDir, ".watch.lock"))
	outBuf.Reset()
	errBuf.Reset()
	code, err = cli.RunCLI(context.Background(), tail, strings.NewReader(""), &outBuf, &errBuf)
	if err != nil || code != 1 || outBuf.Len() != 0 || errBuf.String() != "Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n" {
		t.Fatalf("expected preflight failure on missing second launcher, got code=%d err=%v stderr=%q", code, err, errBuf.String())
	}
	if _, statErr := os.Stat(filepath.Join(cfgDir, ".watch.lock")); !os.IsNotExist(statErr) {
		t.Fatalf("expected zero .watch.lock effect on missing second launcher, got statErr=%v", statErr)
	}
	writePrivateFile(t, secondLauncher, secondLauncherRaw, 0o500)

	// Extra unpinned JSON file in --config-dir fails preflight with exit 1 and zero lock effect in a fresh dir.
	extraPath := filepath.Join(cfgDir, "unpinned.json")
	writePrivateFile(t, extraPath, encodeFixtureJSON(t, secondCfg), 0o600)
	_ = os.Remove(filepath.Join(cfgDir, ".watch.lock"))
	outBuf.Reset()
	errBuf.Reset()
	code, err = cli.RunCLI(context.Background(), tail, strings.NewReader(""), &outBuf, &errBuf)
	if err != nil || code != 1 || outBuf.Len() != 0 || errBuf.String() != "Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n" {
		t.Fatalf("expected preflight failure on extra unpinned config, got code=%d err=%v stderr=%q", code, err, errBuf.String())
	}
	if _, statErr := os.Stat(filepath.Join(cfgDir, ".watch.lock")); !os.IsNotExist(statErr) {
		t.Fatalf("expected zero .watch.lock effect on preflight failure, got statErr=%v", statErr)
	}
	_ = os.Remove(extraPath)

	// Tampered binding.json when coordination_configs still match fails CheckedBinding (W6) before .watch.lock.
	writePrivateFile(t, secondPath, encodeFixtureJSON(t, secondCfg), 0o600)
	writePrivateFile(t, f.bindingPath, append(encodeFixtureJSON(t, manifest), ' '), 0o600)
	_ = os.Remove(filepath.Join(cfgDir, ".watch.lock"))
	outBuf.Reset()
	errBuf.Reset()
	code, err = cli.RunCLI(context.Background(), tail, strings.NewReader(""), &outBuf, &errBuf)
	if err != nil || code != 1 || errBuf.String() != "Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n" {
		t.Fatalf("expected preflight failure on tampered binding.json (W6), got code=%d err=%v stderr=%q", code, err, errBuf.String())
	}
	if _, statErr := os.Stat(filepath.Join(cfgDir, ".watch.lock")); !os.IsNotExist(statErr) {
		t.Fatalf("expected zero .watch.lock effect on tampered binding.json, got statErr=%v", statErr)
	}
	writePrivateFile(t, f.bindingPath, encodeFixtureJSON(t, manifest), 0o600)

	// Tampered second config bytes fail preflight before acquiring .watch.lock.
	writePrivateFile(t, secondPath, append(encodeFixtureJSON(t, secondCfg), ' '), 0o600)
	outBuf.Reset()
	errBuf.Reset()
	code, err = cli.RunCLI(context.Background(), tail, strings.NewReader(""), &outBuf, &errBuf)
	if err != nil || code != 1 || errBuf.String() != "Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n" {
		t.Fatalf("expected preflight failure on tampered second config, got code=%d err=%v stderr=%q", code, err, errBuf.String())
	}
	if _, statErr := os.Stat(filepath.Join(cfgDir, ".watch.lock")); !os.IsNotExist(statErr) {
		t.Fatalf("expected zero .watch.lock effect on tampered config preflight failure, got statErr=%v", statErr)
	}

	// Unbound multi-profile selection (enabled=false, no inbox_recall_binding) also passes preflight.
	memCfgPath, err := sourcePathText(f.profile, receipt.Get("memory_config").Get("path"))
	if err != nil {
		t.Fatal(err)
	}
	memCfg, err := value.DecodeBytes(func() []byte { b, _ := os.ReadFile(memCfgPath); return b }(), 0)
	if err != nil {
		t.Fatal(err)
	}
	nativePop(memCfg, "inbox_recall_binding")
	memSHA := writePrivateFile(t, memCfgPath, encodeFixtureJSON(t, memCfg), 0o600)
	nativePop(f.config, "inbox_recall_binding")
	coord0SHA = writePrivateFile(t, f.configPath, encodeFixtureJSON(t, f.config), 0o600)
	nativePop(secondCfg, "inbox_recall_binding")
	coord1SHA := writePrivateFile(t, secondPath, encodeFixtureJSON(t, secondCfg), 0o600)
	unboundCoordDesc := &value.Value{Kind: value.Array, Items: []*value.Value{
		nativeMap("path", f.configPath, "sha256", coord0SHA),
		nativeMap("path", secondPath, "sha256", coord1SHA),
	}}
	manifest.Set("enabled", &value.Value{Kind: value.Bool, Boolean: false})
	manifest.Get("memory_config").Set("sha256", value.Text(memSHA))
	manifest.Set("coordination_configs", unboundCoordDesc)
	unboundBindingSHA := writePrivateFile(t, f.bindingPath, encodeFixtureJSON(t, manifest), 0o600)
	receipt.Get("memory_config").Set("sha256", value.Text(memSHA))
	receipt.Set("coordination_configs", unboundCoordDesc)
	receipt.Get("binding").Set("sha256", value.Text(unboundBindingSHA))
	unboundReceiptSHA := writePrivateFile(t, f.receiptPath, encodeFixtureJSON(t, receipt), 0o600)
	args[3] = unboundReceiptSHA
	unboundVector := []string{
		"/usr/bin/python3", "-I", "-S", "-c", "pass",
		"--receipt", f.receiptPath, "--receipt-sha256", unboundReceiptSHA,
		"--role", "coordination", "--entrypoint", "coordination.py",
		"--config", f.configPath, "--hook-timeout", "15",
	}
	unboundLauncherRaw, err := LauncherBytes(unboundVector, DefaultCompatibilityShell)
	if err != nil {
		t.Fatal(err)
	}
	writePrivateFile(t, LauncherPath(f.launchRoot, unboundLauncherRaw), unboundLauncherRaw, 0o500)
	unboundSecondVec := vectorWithConfig(unboundVector, f.configPath, secondPath)
	unboundSecondLauncherRaw, err := LauncherBytes(unboundSecondVec, DefaultCompatibilityShell)
	if err != nil {
		t.Fatal(err)
	}
	unboundSecondLauncher := LauncherPath(f.launchRoot, unboundSecondLauncherRaw)
	writePrivateFile(t, unboundSecondLauncher, unboundSecondLauncherRaw, 0o500)
	bindingsDir := filepath.Join(f.root, "bindings")
	firstBindingsPath := filepath.Join(bindingsDir, "codex-one.json")
	writePrivateFile(t, firstBindingsPath, encodeFixtureJSON(t, f.config), 0o600)
	secondBindingsPath := filepath.Join(bindingsDir, "codex-two.json")
	writePrivateFile(t, secondBindingsPath, encodeFixtureJSON(t, secondCfg), 0o600)
	_ = os.Remove(filepath.Join(cfgDir, ".watch.lock"))
	args[len(args)-2] = bindingsDir
	unboundCompat, unboundTail, err := ParseCompatibilityCommand(args, unboundVector, f.profile)
	if err != nil {
		t.Fatalf("ParseCompatibilityCommand unbound watch failed: %v", err)
	}
	unboundHost := NewFileHost(FileHostOptions{Profile: f.profile, Compatibility: unboundCompat})
	unboundCLI := &CLI{Host: unboundHost, Profile: f.profile, Compatibility: unboundCompat, Root: f.root}
	outBuf.Reset()
	errBuf.Reset()
	code, err = unboundCLI.RunCLI(context.Background(), unboundTail, strings.NewReader(""), &outBuf, &errBuf)
	if err != nil || code != 0 || outBuf.Len() != 0 || errBuf.Len() != 0 {
		t.Fatalf("expected unbound watch --once exit 0, got code=%d err=%v out=%q stderr=%q", code, err, outBuf.String(), errBuf.String())
	}
	if _, statErr := os.Stat(filepath.Join(cfgDir, ".watch.lock")); !os.IsNotExist(statErr) {
		t.Fatalf("expected generation dir %q to remain untouched by .watch.lock, got statErr=%v", cfgDir, statErr)
	}
	if _, err := unboundCompat.Config(f.profile, secondBindingsPath); err != nil {
		t.Fatalf("expected unbound watch Config(secondBindingsPath) ok: %v", err)
	}
	unboundIssuedSecond, err := unboundCompat.IssuedArgv([]string{"inbox"})
	if err != nil || len(unboundIssuedSecond) != 2 || unboundIssuedSecond[0] != unboundSecondLauncher {
		t.Fatalf("expected unbound IssuedArgv on secondBindingsPath to select %q, got %v err=%v", unboundSecondLauncher, unboundIssuedSecond, err)
	}

	// W4: fewer files in config-dir than receipt.coordination_configs fails count check.
	if err := unboundCompat.VerifyWatchConfigDir(f.profile, []string{firstBindingsPath}); err == nil || err.Error() != "watch config-dir membership mismatch" {
		t.Fatalf("expected W4 count mismatch error, got %v", err)
	}

	// W5: duplicate copy of profile 0 in config-dir fails 1-to-1 pin usage check.
	writePrivateFile(t, secondBindingsPath, encodeFixtureJSON(t, f.config), 0o600)
	if err := unboundCompat.VerifyWatchConfigDir(f.profile, []string{firstBindingsPath, secondBindingsPath}); err == nil || err.Error() != "config path mismatch" {
		t.Fatalf("expected W5 duplicate pin error, got %v", err)
	}
	writePrivateFile(t, secondBindingsPath, encodeFixtureJSON(t, secondCfg), 0o600)

	// W2 (N1): two-cycle watch without --once re-verifies on cycle 2 and exits 1 when a binding is removed after cycle 1.
	twoCycleTail := []string{"watch", "--config-dir", bindingsDir}
	var tick float64
	unboundCompat.Now = func() float64 {
		tick += 30
		if tick == 120 {
			_ = os.Remove(secondBindingsPath)
		}
		return tick
	}
	outBuf.Reset()
	errBuf.Reset()
	code, err = unboundCLI.RunCLI(context.Background(), twoCycleTail, strings.NewReader(""), &outBuf, &errBuf)
	if err != nil || code != 1 || outBuf.Len() != 0 || errBuf.String() != "Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n" {
		t.Fatalf("expected W2 cycle-2 preflight exit 1 on removed second binding, got code=%d err=%v stderr=%q", code, err, errBuf.String())
	}
	writePrivateFile(t, secondBindingsPath, encodeFixtureJSON(t, secondCfg), 0o600)

	// W3: two-cycle watch without --once resets cached receipt/manifest on cycle 2 and exits 1 when a snapshot member changes after cycle 1.
	coordSnapPath := filepath.Join(string(receipt.Get("snapshot_root").String), "coordination.py")
	origCoordSnap, err := os.ReadFile(coordSnapPath)
	if err != nil {
		t.Fatal(err)
	}
	tick = 0
	unboundCompat.Now = func() float64 {
		tick += 30
		if tick == 120 {
			writePrivateFile(t, coordSnapPath, append(append([]byte{}, origCoordSnap...), '\n'), 0o400)
		}
		return tick
	}
	outBuf.Reset()
	errBuf.Reset()
	code, err = unboundCLI.RunCLI(context.Background(), twoCycleTail, strings.NewReader(""), &outBuf, &errBuf)
	if err != nil || code != 1 || outBuf.Len() != 0 || errBuf.String() != "Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n" {
		t.Fatalf("expected W3 cycle-2 preflight exit 1 on tampered snapshot member, got code=%d err=%v stderr=%q", code, err, errBuf.String())
	}
	writePrivateFile(t, coordSnapPath, origCoordSnap, 0o400)
}
