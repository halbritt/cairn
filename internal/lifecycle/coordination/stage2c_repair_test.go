package coordination

import (
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func stage2CRepairInbox(t *testing.T) (*CLI, *value.Value, string, string) {
	t.Helper()
	p := stage2ServicesProfile()
	dir := t.TempDir()
	c := &CLI{Profile: p, Host: NewFileHost(FileHostOptions{Profile: p}), Command: []string{"/synthetic/coordination"}, ReadInput: ReadUTF8Input}
	c.Journal.Runner = journal.RunnerFunc(func(context.Context, []string, string) (journal.Result, error) {
		return journal.Result{Stdout: `{"ok":true,"data":{"result":{"record_id":"` + stage2Turn + `","version":1}}}`}, nil
	})
	delivery := nativeMap("delivery_id", stage2Delivery, "lease_id", stage2Attempt, "event", nativeMap("event_id", stage2Delivery, "kind", "request", "from", "agent/synthetic", "ref", nativeMap("record_id", stage2Turn, "version", 1)))
	path, native, err := c.InboxHandoff(stage2CLIConfig(dir), nativeMap("agent_id", stage2Agent, "execution_id", stage2Execution), delivery, stage2Attempt, nativeMap("completion", stage2Turn, "response", stage2Execution))
	if err != nil {
		t.Fatal(err)
	}
	return c, native, path, filepath.Join(dir, "intents", stage2Attempt+".json")
}

func TestStage2CRepairDurableRawArgv(t *testing.T) {
	for _, kind := range []string{"ack", "finish-reply"} {
		t.Run(kind, func(t *testing.T) {
			c, native, path, intent := stage2CRepairInbox(t)
			bad := "ack"
			if kind == "finish-reply" {
				bad = "respond"
			}
			native.Get("commands").Get(bad).Items = append(native.Get("commands").Get(bad).Items, value.Boolean(true))
			stage2WriteValue(t, c.profile(), path, native)
			runs := 0
			base := c.Journal.Runner
			c.Journal.Runner = journal.RunnerFunc(func(ctx context.Context, args []string, body string) (journal.Result, error) {
				runs++
				return base.Run(ctx, args, body)
			})
			code, result, _ := stage2CLIResult(t, c, "inbox", "--context", path, kind)
			if code != 7 || !strings.Contains(nativeText(result.Get("message")), "TypeError") {
				t.Fatal(code, result)
			}
			v, err := c.read(intent, SiteCompactJournalRead)
			if err != nil {
				t.Fatalf("invalid argv must remain durable: %v", err)
			}
			if _, err := os.Stat(strings.TrimSuffix(intent, ".json") + ".lock"); err != nil {
				t.Fatal(err)
			}
			slot := v.Get("completion")
			if kind == "finish-reply" {
				if runs != 1 || !value.SameText(slot.Get("status"), "committed") {
					t.Fatal(runs, v)
				}
				slot = v.Get("response")
			} else if runs != 0 {
				t.Fatal(runs)
			}
			if !value.SameText(slot.Get("status"), "pending") {
				t.Fatal(v)
			}
			found := false
			for _, arg := range slot.Get("argv").Items {
				found = found || arg.Kind == value.Bool
			}
			if !found {
				t.Fatal("raw non-string argument lost", v)
			}
		})
	}
}

func TestStage2CRepairLazyContextAndRequestIDs(t *testing.T) {
	c, native, path, intent := stage2CRepairInbox(t)
	native.Set("delivery_id", value.IntegerText("17"))
	native.Get("request_ids").Set("unused", value.Boolean(true))
	native.Get("request_ids").Set("completion", nativeMap("raw", true))
	stage2WriteValue(t, c.profile(), path, native)
	code, result, _ := stage2CLIResult(t, c, "inbox", "--context", path, "ack")
	if code != 0 || !exactTrue(result.Get("ok")) {
		t.Fatal(code, result)
	}
	v, err := c.read(intent, SiteCompactJournalRead)
	if err != nil || v.Get("delivery_id").Integer != "17" || v.Get("completion").Get("request_id").Kind != value.Object {
		t.Fatal(v, err)
	}
	nativePop(native, "delivery_id", "kind", "agent_id", "execution_id", "request_ids")
	stage2WriteValue(t, c.profile(), path, native)
	code, result, _ = stage2CLIResult(t, c, "inbox", "--context", path, "ack")
	if code != 0 || !exactTrue(result.Get("ok")) {
		t.Fatal("existing slot must not read unused fields", code, result)
	}
}

func TestStage2CRepairPreStdinOrder(t *testing.T) {
	for _, version := range [][]string{{"bad", stage2Turn}, {"1", "not-a-uuid"}} {
		c, native, path, _ := stage2CRepairInbox(t)
		native.Get("commands").Get("respond").Items = append(native.Get("commands").Get("respond").Items, value.Text("--stdin"))
		nativePop(native, "state_dir", "attempt_id")
		stage2WriteValue(t, c.profile(), path, native)
		reads := 0
		c.ReadInput = func(io.Reader, int) (string, error) { reads++; return "", nil }
		code, result, _ := stage2CLIResult(t, c, "inbox", "--context", path, "respond", "--version", version[0], version[1])
		if reads != 0 || code == 0 || strings.Contains(nativeText(result.Get("message")), "KeyError") {
			t.Fatal(reads, code, result)
		}
		code, result, _ = stage2CLIResult(t, c, "inbox", "--context", path, "respond", "--version", version[0], version[1], "--disposition", "failed")
		if reads != 0 || code == 0 || strings.Contains(nativeText(result.Get("message")), "disposition") {
			t.Fatal("reply validation must win over disposition", reads, code, result)
		}
	}
	c, native, path, _ := stage2CRepairInbox(t)
	nativePop(native, "state_dir", "attempt_id")
	stage2WriteValue(t, c.profile(), path, native)
	c.ReadInput = func(io.Reader, int) (string, error) { return strings.Repeat("é", 1024*1024+1), nil }
	code, result, _ := stage2CLIResult(t, c, "inbox", "--context", path, "complete")
	if code != 2 || !strings.Contains(nativeText(result.Get("message")), "input exceeds limit") {
		t.Fatal(code, result)
	}
}

func TestStage2CRepairUnboundGrantNeverTouchesFiles(t *testing.T) {
	for _, binding := range []*value.Value{nil, value.Boolean(true), value.Text("/synthetic/binding")} {
		dir := t.TempDir()
		g := GrantBindings{Profile: stage2ServicesProfile()}
		config, state := stage2CLIConfig(dir), stage2State()
		if binding != nil {
			config.Set("inbox_recall_binding", binding)
		}
		status, kind, err := g.GrantEvidence(context.Background(), config, state, state.Get("inbox_attempt"))
		var unavailable *value.ProfileUnavailable
		if binding != nil && binding.Kind == value.String {
			if !errors.As(err, &unavailable) {
				t.Fatal(status, kind, err)
			}
		} else if err != nil || status != "unknown" || !value.SameText(kind, "unknown") {
			t.Fatal(status, kind, err)
		}
		ok, err := g.PreGrantLedgerAbsent(context.Background(), config, state, state.Get("inbox_attempt"), nativeMap("recall_phase", "required_search", "grant", "not_checked"))
		if ok || binding != nil && binding.Kind == value.String && !errors.As(err, &unavailable) || (binding == nil || binding.Kind != value.String) && err != nil {
			t.Fatal(ok, err)
		}
		files, err := os.ReadDir(dir)
		if err != nil || len(files) != 0 {
			t.Fatal("unbound bridge touched coordinator directory", files, err)
		}
	}
}

func TestStage2CRepairErrorNames(t *testing.T) {
	for errno, name := range map[syscall.Errno]string{syscall.ENOENT: "FileNotFoundError", syscall.EACCES: "PermissionError", syscall.EISDIR: "IsADirectoryError", syscall.ENOTDIR: "NotADirectoryError", syscall.EEXIST: "FileExistsError", syscall.ESRCH: "ProcessLookupError", syscall.ETIMEDOUT: "TimeoutError", syscall.EPIPE: "BrokenPipeError"} {
		err := &os.PathError{Op: "read", Path: "owned-fixture", Err: errno}
		if sourceErrorName(err) != name || !sourceReadCaught(err) {
			t.Fatal(errno, sourceErrorName(err), sourceReadCaught(err))
		}
	}
	c, native, path, intent := stage2CRepairInbox(t)
	stage2WriteValue(t, c.profile(), path, native)
	c.Journal.Runner = journal.RunnerFunc(func(context.Context, []string, string) (journal.Result, error) {
		return journal.Result{}, &journal.UnicodeDecodeError{Message: "invalid fixture output"}
	})
	code, result, _ := stage2CLIResult(t, c, "inbox", "--context", path, "ack")
	v, err := c.read(intent, SiteCompactJournalRead)
	if code != 7 || !strings.Contains(nativeText(result.Get("message")), "UnicodeDecodeError") || err != nil || !value.SameText(v.Get("completion").Get("status"), "pending") {
		t.Fatal(code, result, v, err)
	}
}

func TestStage2CRepairCompactGuardOrder(t *testing.T) {
	for _, change := range []func(*stage2CompactFixture){
		func(f *stage2CompactFixture) { f.state.Set("process", value.Mapping()) },
		func(f *stage2CompactFixture) { f.state.Set("ending", value.Boolean(true)) },
		func(f *stage2CompactFixture) { f.state.Get("agent").Set("native_session_id", value.Text("other")) },
		func(f *stage2CompactFixture) { f.native.Set("compact_handle", value.Text("other")) },
	} {
		f := stage2Compact(t)
		f.state.Set("inbox_attempt", value.Text("truthy non-object"))
		change(f)
		f.save(t)
		code, result, _ := stage2CLIResult(t, f.c, "inbox", "compact", "read", "abcdef0123456789")
		if code != 7 || !value.SameText(result.Get("status"), "STALE_SESSION") {
			t.Fatal(code, result)
		}
	}
	f := stage2Compact(t)
	nativePop(f.native, "request_ids")
	f.save(t)
	f.c.Host.Call = func(context.Context, *value.Value, string, *value.Value, NativeCallOptions) (*value.Value, error) {
		return nil, nativeRefusal("STALE_LEASE", "fixture")
	}
	code, result, _ := stage2CLIResult(t, f.c, "inbox", "compact", "read", "abcdef0123456789")
	if code != 7 || !value.SameText(result.Get("status"), "STALE_LEASE") {
		t.Fatal(code, result)
	}
}

func TestStage2CRepairEmptyRootAndDirectoryErrors(t *testing.T) {
	t.Setenv("CAIRN_INBOX_ROOT", "")
	c := &CLI{Profile: stage2ServicesProfile()}
	root, err := c.compactRoot()
	want, _ := os.Getwd()
	if err != nil || root != want {
		t.Fatal(root, want, err)
	}
	file := filepath.Join(t.TempDir(), "file")
	if err := os.WriteFile(file, nil, 0600); err != nil {
		t.Fatal(err)
	}
	c.Root = file
	code, result, _ := stage2CLIResult(t, c, "inbox", "compact", "read", "abcdef0123456789")
	if code != 7 || !value.SameText(result.Get("status"), "NOT_FOUND") {
		t.Fatal(code, result)
	}
	c.Host = NewFileHost(FileHostOptions{Profile: c.profile()})
	configPath := filepath.Join(t.TempDir(), "config.json")
	stage2WriteValue(t, c.profile(), configPath, stage2CLIConfig(file))
	code, result, _ = stage2CLIResult(t, c, "inbox", "drain", "--config", configPath, "--agent-id", stage2Agent, "--execution-id", stage2Execution)
	if code != 7 || !value.SameText(result.Get("status"), "NOT_THIS_SESSION") {
		t.Fatal(code, result)
	}
}

func TestStage2CRepairParser(t *testing.T) {
	for _, text := range []string{"-x", "-h", "--unknown"} {
		_, err := parseCLI([]string{"--config", text}, "fixture", map[string]int{"--config": 1})
		var parse *cliParseError
		if !errors.As(err, &parse) || !strings.Contains(parse.message, "expected one argument") {
			t.Fatal(text, err)
		}
	}
	for _, text := range []string{"-1", "-.5", "-1.5", "--x y", "-"} {
		a, err := parseCLI([]string{"--config", text}, "fixture", map[string]int{"--config": 1})
		if err != nil || a.get("--config") != text {
			t.Fatal(text, a, err)
		}
	}
	a, err := parseCLI([]string{"--config=--x y"}, "fixture", map[string]int{"--config": 1})
	if err != nil || a.get("--config") != "--x y" {
		t.Fatal(a, err)
	}
	var parse *cliParseError
	if err := choiceCLI("fixture", "mode", "x", []string{"a", "b"}); !errors.As(err, &parse) || !strings.Contains(parse.message, "choose from a, b)") {
		t.Fatal(err)
	}
	for _, mode := range []string{"hook", "watch"} {
		_, err := (&CLI{}).runRecovery(context.Background(), []string{mode}, &bytes.Buffer{})
		var unavailable *value.ProfileUnavailable
		if !errors.As(err, &unavailable) {
			t.Fatal(mode, err)
		}
	}
}

func TestStage2CRepairHandoffRawIdentity(t *testing.T) {
	c, _, _, _ := stage2CRepairInbox(t)
	delivery := nativeMap("delivery_id", 17, "lease_id", stage2Attempt, "event", nativeMap("event_id", false, "kind", "request", "from", nativeMap("raw", true), "ref", value.Mapping(), "correlation_id", stringArray("raw")))
	_, native, err := c.InboxHandoff(stage2CLIConfig(t.TempDir()), nativeMap("agent_id", stage2Agent, "execution_id", stage2Execution), delivery, stage2Attempt, nativeMap("completion", stage2Turn, "response", stage2Execution))
	if err != nil {
		t.Fatal(err)
	}
	ack := native.Get("commands").Get("ack").Items
	respond := native.Get("commands").Get("respond").Items
	if ack[len(ack)-1].Integer != "17" || respond[len(respond)-1].Kind != value.Array || respond[len(respond)-3].Kind != value.Bool || respond[len(respond)-7].Kind != value.Object {
		t.Fatal(native)
	}
}

func TestStage2CRepairIntegralProcessStart(t *testing.T) {
	for _, start := range []*value.Value{value.IntegerText("12"), {Kind: value.Float, Number: 12}} {
		n, err := codexPeerStart(start)
		if err != nil || n != 12 {
			t.Fatal(n, err)
		}
	}
	for _, start := range []*value.Value{{Kind: value.Float, Number: 12.5}, value.Text("12")} {
		_, err := codexPeerStart(start)
		stage2ErrorClass(t, err, "TypeError")
	}
}

func TestStage2CRepairPreGrantLexistsErrors(t *testing.T) {
	g, config, state, _, _ := stage2GrantFixture(t)
	name := strings.Repeat("a", 231)
	state.Get("agent").Set("native_session_id", value.Text(name))
	budget := nativeMap("schema", CodexLedgerSchema, "session_id", name, "inbox_activation", nativeMap(stage2Turn, true), "turns", value.Mapping())
	stage2WriteValue(t, g.Profile, filepath.Join(nativeText(config.Get("state_dir")), name+".memory-budget.json"), budget)
	ok, err := g.PreGrantLedgerAbsent(context.Background(), config, state, state.Get("inbox_attempt"), nativeMap("recall_phase", "required_search", "grant", "not_checked"))
	if err != nil || !ok {
		t.Fatal(ok, err)
	}
	g.Loader = func(string, *value.Value) (*value.Value, error) { return nativeMap("grants", value.Mapping()), nil }
	status, kind, err := g.Evidence(context.Background(), config, state, state.Get("inbox_attempt"), nil, false)
	if err != nil || status != "absent" || kind != nil {
		t.Fatal(status, kind, err)
	}
}
