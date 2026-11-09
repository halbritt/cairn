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
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func stage2CLIConfig(dir string) *value.Value {
	return nativeMap("cairn", "/synthetic/cairn", "socket", "/synthetic/api.sock", "token_file", "/synthetic/profile-selector", "state_dir", dir, "harness", "codex", "repo", "synthetic", "binding", "fixture", "native_delivery", true)
}
func stage2CLIResult(t *testing.T, c *CLI, args ...string) (int, *value.Value, string) {
	t.Helper()
	var out, errOut bytes.Buffer
	code, err := c.RunCLI(context.Background(), args, strings.NewReader("selected result"), &out, &errOut)
	if err != nil {
		t.Fatal(err)
	}
	var v *value.Value
	if out.Len() > 0 {
		v, err = value.DecodeBytes(out.Bytes(), 0)
		if err != nil {
			t.Fatal(out.String(), err)
		}
	}
	return code, v, errOut.String()
}
func TestStage2ValidateConfigSourceOrder(t *testing.T) {
	for _, tc := range []struct {
		name, message string
		change        func(*value.Value)
	}{
		{"first_absolute", "absolute cairn required", func(c *value.Value) { c.Set("cairn", value.Text("relative")); c.Set("harness", value.Text("bad")) }},
		{"collection_truth", "harness, collection and binding required", func(c *value.Value) { c.Set("repo", value.IntegerText("0")); c.Set("binding", value.Text("..")) }},
		{"cancel_first", "opencode_cancel_enabled requires an OpenCode boolean", func(c *value.Value) {
			c.Set("opencode_cancel_enabled", value.Boolean(false))
			c.Set("remind_unacknowledged_turn_end", value.IntegerText("1"))
		}},
		{"reminder_bool", "remind_unacknowledged_turn_end requires a boolean", func(c *value.Value) { c.Set("remind_unacknowledged_turn_end", value.IntegerText("1")) }},
		{"compact_exact_int", "compact_inbox_version 1 requires Codex or Claude native delivery", func(c *value.Value) { c.Set("compact_inbox_version", value.Boolean(true)) }},
		{"batch_compact", "notice_batch_version 1 requires compact Codex or Claude native delivery", func(c *value.Value) { c.Set("notice_batch_version", value.IntegerText("1")) }},
		{"wake_true", "idle_wakeup requires an absolute Herdr executable and native_delivery (true is accepted only for the OpenCode bridge)", func(c *value.Value) { c.Set("idle_wakeup", value.Boolean(true)) }},
		{"channel_dir_first", "claude_channel_sessions requires an absolute claude_channel_dir", func(c *value.Value) {
			c.Set("claude_channel_sessions", value.Boolean(true))
			c.Set("binding", value.Text(".."))
		}},
		{"channel_bound", "claude_channel_sessions requires a Claude list of bounded native session IDs", func(c *value.Value) {
			c.Set("harness", value.Text("claude"))
			c.Set("claude_channel_dir", value.Text("/synthetic"))
			c.Set("claude_channel_sessions", stringArray(strings.Repeat("x", 257)))
		}},
		{"channel_duplicate", "claude_channel_sessions must not contain duplicate native session IDs", func(c *value.Value) {
			c.Set("harness", value.Text("claude"))
			c.Set("claude_channel_dir", value.Text("/synthetic"))
			c.Set("claude_channel_sessions", stringArray("id", "id"))
		}},
		{"server_chars", "claude_channel_server requires a bounded Claude MCP server name", func(c *value.Value) {
			c.Set("harness", value.Text("claude"))
			c.Set("claude_channel_server", value.Text("bad:name"))
		}},
		{"binding_last", "invalid binding name", func(c *value.Value) { c.Set("binding", value.Text("..")) }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := stage2CLIConfig(t.TempDir())
			tc.change(c)
			_, err := ValidateConfig(c)
			var e *CoordinationError
			if !errors.As(err, &e) || e.Code != "INVALID_CONFIG" || e.Message != tc.message {
				t.Fatal(err)
			}
		})
	}
	c := stage2CLIConfig(t.TempDir())
	c.Set("repo", stringArray("truthy"))
	c.Set("harness", value.Text("opencode"))
	c.Set("idle_wakeup", value.Boolean(true))
	c.Set("opencode_cancel_enabled", value.Boolean(false))
	if out, err := ValidateConfig(c); err != nil || out != c {
		t.Fatal(out, err)
	}
	c = stage2CLIConfig(t.TempDir())
	c.Set("harness", value.Text("claude"))
	c.Set("claude_channel_dir", value.Text("/synthetic"))
	c.Set("claude_channel_sessions", stringArray("  id  ", strings.Repeat("é", 128)))
	if _, err := ValidateConfig(c); err != nil {
		t.Fatal(err)
	}
	c.Get("claude_channel_sessions").Items[0] = &value.Value{Kind: value.String, String: []rune{0xd800}}
	_, err := ValidateConfig(c)
	stage2Code(t, err, "INVALID_CONFIG")
	_, err = ValidateConfig(value.Boolean(true))
	stage2ErrorClass(t, err, "AttributeError")
}

func TestStage2RecoveryCLI(t *testing.T) {
	state := stage2State()
	e, h, config := stage2Engine(t, "codex", state)
	for _, key := range []string{"cairn", "socket", "token_file"} {
		config.Set(key, value.Text("/synthetic/"+key))
	}
	h.ReadConfig = func(string) (*value.Value, error) { return config, nil }
	c := &CLI{Host: &h.Host, Engine: e, Profile: stage2ServicesProfile()}
	e.Hooks.ProbeCodex = func(context.Context, *value.Value, *value.Value, bool) (*value.Value, error) {
		return nativeMap("z", true, "a", nativeMap("z", value.IntegerText(strings.Repeat("9", 90)), "a", &value.Value{Kind: value.Float, Number: 1})), nil
	}
	var out, errOut bytes.Buffer
	code, err := c.RunCLI(context.Background(), []string{"check-codex-native-health", "--config", "/synthetic/config", "--native-session-id", stage2Agent}, nil, &out, &errOut)
	if err != nil || code != 0 || errOut.Len() != 0 || !strings.HasPrefix(out.String(), `{"a": {"a": 1.0, "z": `) || !strings.HasSuffix(out.String(), "\n") {
		t.Fatal(code, out.String(), errOut.String(), err)
	}
	for _, mode := range []string{"check-codex-native-health", "reconcile-codex-queue-wake", "inspect-held-refusal", "recover-daemon-start-claim", "recover-codex-ended-turn", "recover-codex-refused-wake", "recover-native-refused-wake", "recover-refused-wake"} {
		code, v, stderr := stage2CLIResult(t, c, mode)
		if code != 2 || v != nil || !strings.Contains(stderr, mode+" requires --config") {
			t.Fatal(code, v, stderr)
		}
	}
	h.ReadConfig = func(string) (*value.Value, error) { return nil, &journal.KeyError{Key: "synthetic"} }
	code, v, stderr := stage2CLIResult(t, c, "inspect-held-refusal", "--config", "/synthetic", "--native-session-id", stage2Agent)
	if code != 1 || v != nil || stderr != "Cairn coordination: KeyError; session presence unavailable\n" {
		t.Fatal(code, v, stderr)
	}
	h.ReadConfig = func(string) (*value.Value, error) { return nil, nativeRefusal("NATIVE_PROMPT_REFUSED", "synthetic") }
	code, _, stderr = stage2CLIResult(t, c, "inspect-held-refusal", "--config", "/synthetic", "--native-session-id", stage2Agent)
	if code != 2 || stderr != "Cairn coordination: NATIVE_PROMPT_REFUSED: synthetic; channel text rejected, owner prompt continues\n" {
		t.Fatal(code, stderr)
	}
	for _, tc := range []struct {
		locator string
		ok      bool
	}{{"0:65536:" + strings.Repeat("a", 64), true}, {"9999999999999999:1:" + strings.Repeat("0", 64), true}, {"0:65537:" + strings.Repeat("a", 64), false}, {"00:1:" + strings.Repeat("a", 64), false}} {
		_, err := ParseLocator(tc.locator)
		if (err == nil) != tc.ok {
			t.Fatal(tc, err)
		}
	}
}

func TestStage2DrainAndInboxCLI(t *testing.T) {
	p := stage2ServicesProfile()
	dir := t.TempDir()
	config := stage2CLIConfig(dir)
	configPath := filepath.Join(t.TempDir(), "config.json")
	stage2WriteValue(t, p, configPath, config)
	state := stage2State()
	nativePop(state, "inbox_attempt", "inbox_intent")
	stage2WriteValue(t, p, filepath.Join(dir, "own.json"), state)
	delivery := nativeMap("delivery_id", stage2Delivery, "lease_id", stage2Attempt, "event", nativeMap("event_id", stage2Delivery, "kind", "request", "from", "agent/other", "ref", nativeMap("record_id", stage2Turn, "version", 1), "correlation_id", "correlation"))
	calls := 0
	host := NewFileHost(FileHostOptions{Profile: p, Call: func(_ context.Context, _ *value.Value, op string, body *value.Value, options NativeCallOptions) (*value.Value, error) {
		calls++
		if op != "event-next" || !nativeEqual(options.Session, nativeMap("agent_id", stage2Agent, "execution_id", stage2Execution)) || body.Get("lease_seconds").Integer != "900" {
			t.Fatal(op, body, options)
		}
		return nativeMap("delivery", delivery), nil
	}})
	c := &CLI{Host: host, Profile: p, Command: []string{"/synthetic/coordination-go"}, Engine: &RecoveryEngine{Hooks: RecoveryHooks{Alive: func(*value.Value) (bool, error) { return true, nil }}}}
	args := []string{"inbox", "drain", "--config", configPath, "--agent-id", stage2Agent, "--execution-id", stage2Execution}
	code, result, stderr := stage2CLIResult(t, c, args...)
	if code != 0 || calls != 1 || stderr != "" {
		t.Fatal(code, result, stderr, calls)
	}
	contextPath := nativeText(result.Get("context"))
	info, err := os.Stat(contextPath)
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatal(info, err)
	}
	native, err := c.read(contextPath, SiteInboxContextRead)
	if err != nil || !value.SameText(native.Get("route"), "drain") || !value.SameText(native.Get("source").Get("record_id"), stage2Turn) || nativeText(native.Get("response").Items[0]) != "/synthetic/coordination-go" {
		t.Fatal(native, err)
	}
	// The actual journal observes the immutable command before the fake runner.
	runs := 0
	c.Journal.Runner = journal.RunnerFunc(func(_ context.Context, argv []string, input string) (journal.Result, error) {
		runs++
		if argv[1] != "ack" || input != "" {
			t.Fatal(argv, input)
		}
		raw, err := os.ReadFile(filepath.Join(dir, "intents", "drain-"+stage2Attempt+".json"))
		if err != nil {
			t.Fatal(string(raw), err)
		}
		intent, e := value.DecodeBytes(raw, 0)
		if e != nil || !value.SameText(intent.Get("completion").Get("status"), "pending") {
			t.Fatal(intent, e)
		}
		return journal.Result{Stdout: `{"schema":"cairn.response/1","ok":true,"data":{"state":"handled","attempts":1}}` + "\n"}, nil
	})
	code, result, stderr = stage2CLIResult(t, c, "inbox", "--context", contextPath, "ack")
	if code != 0 || !exactTrue(result.Get("ok")) || runs != 1 || stderr != "" {
		t.Fatal(code, result, stderr, runs)
	}
	if err = os.Chmod(contextPath, 0640); err != nil {
		t.Fatal(err)
	}
	code, result, _ = stage2CLIResult(t, c, "inbox", "--context", contextPath, "ack")
	if code != 7 || !value.SameText(result.Get("status"), "INVALID_HOST") || runs != 1 {
		t.Fatal(code, result, runs)
	}
	state.Set("inbox_intent", nativeMap("request_id", "held"))
	stage2WriteValue(t, p, filepath.Join(dir, "own.json"), state)
	code, result, _ = stage2CLIResult(t, c, args...)
	if code != 7 || !value.SameText(result.Get("status"), "NATIVE_ACTIVE") || calls != 1 {
		t.Fatal(code, result, calls)
	}
	args[len(args)-1] = "other-execution"
	code, result, _ = stage2CLIResult(t, c, args...)
	if code != 7 || !value.SameText(result.Get("status"), "NOT_THIS_SESSION") || calls != 1 {
		t.Fatal(code, result, calls)
	}
}

type stage2CompactFixture struct {
	c                                   *CLI
	p                                   *value.SourceReaderProfile
	config, state, native, delivery     *value.Value
	root, statePath, contextPath, index string
	calls                               []string
}

func stage2Compact(t *testing.T) *stage2CompactFixture {
	t.Helper()
	f := &stage2CompactFixture{p: stage2ServicesProfile(), root: t.TempDir()}
	dir := filepath.Join(f.root, "state", "fixture")
	f.config = stage2CLIConfig(dir)
	f.config.Set("compact_inbox_version", value.IntegerText("1"))
	f.config.Set("notice_batch_version", value.IntegerText("1"))
	f.state = stage2State()
	f.delivery = f.state.Get("inbox_attempt").Get("delivery")
	f.delivery.Get("event").Set("from", value.Text("agent/sender"))
	f.delivery.Get("event").Set("ref", nativeMap("record_id", stage2Turn, "version", 1))
	f.statePath = filepath.Join(dir, "own.json")
	f.contextPath = filepath.Join(dir, "inbox", stage2Attempt+".json")
	f.index = filepath.Join(dir, "compact", "abcdef0123456789.json")
	host := NewFileHost(FileHostOptions{Profile: f.p, Call: func(_ context.Context, _ *value.Value, op string, _ *value.Value, _ NativeCallOptions) (*value.Value, error) {
		f.calls = append(f.calls, op)
		switch op {
		case "event-renew":
			d := nativeClone(f.delivery)
			d.Set("state", value.Text("leased"))
			return d, nil
		case "history":
			body := "é source"
			sum := sha256.Sum256([]byte(body))
			return nativeMap("record_id", stage2Turn, "historical", true, "versions", &value.Value{Kind: value.Array, Items: []*value.Value{nativeMap("version", 1, "payload_available", true, "body", body, "body_bytes", len([]byte(body)), "body_sha256", hex.EncodeToString(sum[:]))}}), nil
		}
		t.Fatal("unexpected operation", op)
		return nil, nil
	}})
	f.c = &CLI{Host: host, Profile: f.p, Command: []string{"/synthetic/coordination"}, Root: f.root, Now: func() time.Time { return time.Unix(100, 0) }, Env: func(string) string { return "" }, CallerDescends: func(*value.Value) (bool, error) { return true, nil }, Engine: &RecoveryEngine{Hooks: RecoveryHooks{Alive: func(*value.Value) (bool, error) { return true, nil }}}}
	_, native, err := f.c.InboxHandoff(f.config, f.state.Get("inbox_attempt").Get("session"), f.delivery, stage2Attempt, nativeMap("completion", stage2Turn, "response", stage2Execution))
	if err != nil {
		t.Fatal(err)
	}
	f.native = native
	native.Set("native_turn_id", value.Text(stage2Turn))
	native.Set("compact_handle", value.Text("abcdef0123456789"))
	native.Set("compact_issued_at", value.IntegerText("90"))
	native.Set("batch_advance_request_id", value.Text(stage2Execution))
	f.save(t)
	stage2WriteValue(t, f.p, filepath.Join(f.root, "bindings", "fixture.json"), f.config)
	stage2WriteValue(t, f.p, f.index, nativeMap("schema", "cairn.compact-inbox-handle/1", "binding", "fixture", "state", f.statePath, "context", f.contextPath, "native_session_id", stage2Agent, "issued_at", 90))
	return f
}
func (f *stage2CompactFixture) save(t *testing.T) {
	stage2WriteValue(t, f.p, f.statePath, f.state)
	stage2WriteValue(t, f.p, f.contextPath, f.native)
}
func TestStage2CompactReadAndFences(t *testing.T) {
	f := stage2Compact(t)
	code, result, stderr := stage2CLIResult(t, f.c, "inbox", "compact", "read", "abcdef0123456789")
	if code != 0 || stderr != "" || !value.SameText(result.Get("body"), "é source") || strings.Join(f.calls, ",") != "event-renew,history" {
		t.Fatal(code, result, stderr, f.calls)
	}
	f.calls = nil
	f.native.Set("sender", value.Text("changed"))
	f.save(t)
	code, result, _ = stage2CLIResult(t, f.c, "inbox", "compact", "inspect", "abcdef0123456789")
	if code != 7 || !value.SameText(result.Get("status"), "INVALID_HOST") || len(f.calls) != 0 {
		t.Fatal(code, result, f.calls)
	}
	f = stage2Compact(t)
	f.c.CallerDescends = func(*value.Value) (bool, error) { return false, nil }
	code, result, _ = stage2CLIResult(t, f.c, "inbox", "compact", "read", "abcdef0123456789")
	if code != 7 || !value.SameText(result.Get("status"), "NOT_THIS_SESSION") || len(f.calls) != 0 {
		t.Fatal(code, result, f.calls)
	}
	f = stage2Compact(t)
	f.c.Now = func() time.Time { return time.Unix(3690, 0) }
	code, result, _ = stage2CLIResult(t, f.c, "inbox", "compact", "read", "abcdef0123456789")
	if code != 7 || !value.SameText(result.Get("status"), "HANDLE_EXPIRED") || len(f.calls) != 0 {
		t.Fatal(code, result, f.calls)
	}
}
func TestStage2CompactSettleAndAdvance(t *testing.T) {
	f := stage2Compact(t)
	runs := 0
	f.c.Journal.Runner = journal.RunnerFunc(func(_ context.Context, args []string, input string) (journal.Result, error) {
		runs++
		if !strings.Contains(strings.Join(args, " "), "--disposition failed --code source_unavailable") {
			t.Fatal(args)
		}
		return journal.Result{Stdout: `{"ok":true,"data":{"state":"failed","attempts":1}}` + "\n"}, nil
	})
	code, result, _ := stage2CLIResult(t, f.c, "inbox", "compact", "settle", "abcdef0123456789", "failed", "--code", "source_unavailable")
	if code != 0 || !value.SameText(result.Get("status"), "failed") || runs != 1 || strings.Join(f.calls, ",") != "event-renew" {
		t.Fatal(code, result, runs, f.calls)
	}
	// A committed acknowledgement may retry through authentication after its
	// lease is gone; it cannot be inferred from unrelated journal testimony.
	f.c.Host.Call = func(context.Context, *value.Value, string, *value.Value, NativeCallOptions) (*value.Value, error) {
		return nil, nativeRefusal("STALE_LEASE", "settled")
	}
	code, result, _ = stage2CLIResult(t, f.c, "inbox", "compact", "settle", "abcdef0123456789", "failed", "--code", "source_unavailable")
	if code != 0 || runs != 2 || !exactTrue(result.Get("ok")) {
		t.Fatal(code, result, runs)
	}
	f = stage2Compact(t)
	next := nativeClone(f.state.Get("inbox_attempt"))
	next.Set("attempt_id", value.Text(stage2Execution))
	next.Set("batch_root", value.Text(stage2Attempt))
	next.Set("batch_index", value.IntegerText("2"))
	next.Set("batch_limit", value.IntegerText("3"))
	f.c.Host.Call = func(_ context.Context, _ *value.Value, op string, request *value.Value, _ NativeCallOptions) (*value.Value, error) {
		if op != "session-inbox-advance" || !value.SameText(request.Get("request_id"), stage2Execution) {
			t.Fatal(op, request)
		}
		return nativeMap("attempt", next, "remainder", "none"), nil
	}
	rendered := false
	f.c.NextContext = func(_ context.Context, _ *value.Value, state *value.Value, path string, observation *value.Value) (*value.Value, error) {
		rendered = true
		if path != f.statePath || !nativeEqual(state.Get("inbox_attempt"), next) || !value.SameText(observation.Get("event"), "PostToolUse") || !value.SameText(observation.Get("native_turn_id"), stage2Turn) {
			t.Fatal(path, state, observation)
		}
		return value.Text("native frontend context"), nil
	}
	code, result, _ = stage2CLIResult(t, f.c, "inbox", "compact", "advance", "abcdef0123456789")
	if code != 0 || !rendered || !value.SameText(result.Get("next"), "native frontend context") || result.Get("batch_index").Integer != "2" {
		t.Fatal(code, result, rendered)
	}
}

func TestStage2CLITextAndEpochBoundaries(t *testing.T) {
	c := &CLI{Env: func(string) string { return "80" }}
	var help, diagnostic bytes.Buffer
	code, e := c.RunCLI(context.Background(), []string{"inbox", "drain", "--help"}, nil, &help, &diagnostic)
	if e != nil || code != 0 || diagnostic.Len() != 0 || !strings.Contains(help.String(), "--lease-seconds LEASE_SECONDS") || !strings.Contains(help.String(), "show this help message and exit") {
		t.Fatal(code, help.String(), diagnostic.String(), e)
	}
	text, err := ReadUTF8Input(strings.NewReader("éé"), 1)
	if err != nil || text != "é" {
		t.Fatal(text, err)
	}
	_, err = ReadUTF8Input(strings.NewReader("\xff"), 1)
	stage2ErrorClass(t, err, "UnicodeDecodeError")
	for _, n := range []string{strings.Repeat("9", 500), "-" + strings.Repeat("9", 500)} {
		fresh, err := compactFresh(value.IntegerText(n), 100)
		if err != nil || fresh {
			t.Fatal(fresh, err)
		}
	}
	p := stage2ServicesProfile()
	points := []rune{'/', 0xdc80}
	original := journalText(points)
	called := false
	runner := sourceJournalRunner{profile: p, base: journal.RunnerFunc(func(_ context.Context, argv []string, _ string) (journal.Result, error) {
		called = true
		if argv[0] != "/\x80" {
			t.Fatalf("filesystem encoding lost the surrogate: %q", argv[0])
		}
		return journal.Result{}, nil
	})}
	_, err = runner.Run(context.Background(), []string{original}, "")
	if err != nil || !called || !nativeEqual(stringArray(original).Items[0], &value.Value{Kind: value.String, String: points}) {
		t.Fatal(called, err)
	}
}
