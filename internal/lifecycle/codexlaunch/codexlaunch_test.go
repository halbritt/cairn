package codexlaunch

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// The test binary doubles as the fake codex and as the launcher under test,
// so the lifetime tests exercise real processes and signals.
func TestMain(m *testing.M) {
	switch os.Getenv("CODEXLAUNCH_ROLE") {
	case "codex":
		os.Exit(fakeCodex(os.Args[1:]))
	case "launcher":
		os.Unsetenv("CODEXLAUNCH_ROLE")
		env := append(os.Environ(), "CODEXLAUNCH_ROLE=codex")
		os.Exit(Launcher{StopWait: 2 * time.Second, StartWait: seconds("CODEXLAUNCH_START_WAIT")}.Run(os.Args[1:], env, os.Stderr))
	}
	os.Exit(m.Run())
}

func record(name string, value any) {
	path := os.Getenv("LAUNCHER_" + name)
	if path == "" {
		return
	}
	data, _ := json.Marshal(value)
	file, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		panic(err)
	}
	defer file.Close()
	file.Write(append(data, '\n'))
}

func seconds(name string) time.Duration {
	value, _ := strconv.ParseFloat(os.Getenv(name), 64)
	return time.Duration(value * float64(time.Second))
}

func fakeCodex(args []string) int {
	for _, name := range ownerEnv {
		if _, ok := os.LookupEnv(name); ok {
			fmt.Fprintln(os.Stderr, "owner metadata reached codex:", name)
			return 97
		}
	}
	record("LOG", args)
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGTERM)
	if len(args) >= 3 && args[0] == "app-server" && args[1] == "--listen" {
		record("SERVER_PID", os.Getpid())
		select {
		case <-time.After(seconds("LAUNCHER_SERVER_DELAY")):
		case <-stop:
			return 0
		}
		listener, err := net.Listen("unix", strings.TrimPrefix(args[2], "unix://"))
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 98
		}
		defer listener.Close()
		if code := os.Getenv("LAUNCHER_SERVER_EXIT"); code != "" {
			time.Sleep(500 * time.Millisecond)
			value, _ := strconv.Atoi(code)
			listener.(*net.UnixListener).SetUnlinkOnClose(false)
			return value
		}
		<-stop
		return 0
	}
	record("TUI_PID", os.Getpid())
	select {
	case <-time.After(seconds("LAUNCHER_TUI_SECONDS")):
	case <-stop:
		return 0
	}
	code, _ := strconv.Atoi(os.Getenv("LAUNCHER_TUI_EXIT"))
	return code
}

type fixture struct {
	t       *testing.T
	work    string
	runtime string
	env     []string
}

func newFixture(t *testing.T, extra ...string) *fixture {
	t.Helper()
	work := t.TempDir()
	f := &fixture{t: t, work: work, runtime: filepath.Join(work, "runtime")}
	env := make([]string, 0, len(os.Environ())+6)
	for _, entry := range os.Environ() {
		if strings.HasPrefix(entry, "HOME=") {
			continue
		}
		env = append(env, entry)
	}
	f.env = append(env,
		"HOME="+work,
		"CODEXLAUNCH_ROLE=launcher",
		"XDG_RUNTIME_DIR="+f.runtime,
		"LAUNCHER_LOG="+filepath.Join(work, "log.jsonl"),
		"LAUNCHER_SERVER_PID="+filepath.Join(work, "server.pid"),
		"LAUNCHER_TUI_PID="+filepath.Join(work, "tui.pid"))
	f.env = append(f.env, extra...)
	return f
}

func (f *fixture) command(forwarded ...string) (*exec.Cmd, *bytes.Buffer) {
	f.t.Helper()
	self, err := os.Executable()
	if err != nil {
		f.t.Fatal(err)
	}
	command := exec.Command(self, append([]string{"--codex", self, "--"}, forwarded...)...)
	command.Env = f.env
	stderr := &bytes.Buffer{}
	command.Stderr = stderr
	return command, stderr
}

func (f *fixture) run(forwarded ...string) (int, string) {
	f.t.Helper()
	command, stderr := f.command(forwarded...)
	err := command.Run()
	var exit *exec.ExitError
	if err != nil && !errors.As(err, &exit) {
		f.t.Fatal(err)
	}
	return command.ProcessState.ExitCode(), stderr.String()
}

func (f *fixture) entries() [][]string {
	f.t.Helper()
	data, err := os.ReadFile(filepath.Join(f.work, "log.jsonl"))
	if err != nil {
		f.t.Fatal(err)
	}
	var out [][]string
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		var entry []string
		if err := json.Unmarshal([]byte(line), &entry); err != nil {
			f.t.Fatal(err)
		}
		out = append(out, entry)
	}
	return out
}

func (f *fixture) pid(name string) (int, bool) {
	data, err := os.ReadFile(filepath.Join(f.work, name))
	if err != nil {
		return 0, false
	}
	pid, _ := strconv.Atoi(strings.TrimSpace(string(data)))
	return pid, true
}

func (f *fixture) assertDead(name string) {
	f.t.Helper()
	pid, ok := f.pid(name)
	if !ok {
		f.t.Fatalf("%s was never written", name)
	}
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if err := syscall.Kill(pid, 0); errors.Is(err, syscall.ESRCH) {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	f.t.Fatalf("%s process %d outlived the launcher", name, pid)
}

func (f *fixture) assertNoSocket() {
	f.t.Helper()
	left, _ := filepath.Glob(filepath.Join(f.runtime, "*.sock"))
	if len(left) != 0 {
		f.t.Fatalf("session socket survived launcher exit: %v", left)
	}
}

func (f *fixture) waitFor(name string) {
	f.t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if _, ok := f.pid(name); ok {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	f.t.Fatalf("%s did not appear", name)
}

func TestServedRouteSharesOneSocketAndCleansUp(t *testing.T) {
	f := newFixture(t)
	code, stderr := f.run("resume", "--last")
	if code != 0 {
		t.Fatalf("exit %d: %s", code, stderr)
	}
	entries := f.entries()
	if len(entries) != 2 {
		t.Fatalf("want app-server and TUI, got %v", entries)
	}
	server, tui := entries[0], entries[1]
	if !reflect.DeepEqual(server[:2], []string{"app-server", "--listen"}) || !strings.HasPrefix(server[2], "unix://"+f.runtime+"/queue-") {
		t.Fatalf("app-server argv %v", server)
	}
	if want := []string{"--remote", server[2], "resume", "--last"}; !reflect.DeepEqual(tui, want) {
		t.Fatalf("TUI argv %v, want %v", tui, want)
	}
	f.assertNoSocket()
	f.assertDead("server.pid")
}

func TestServedRouteDropsNoDaemonFromTheHeadOnly(t *testing.T) {
	for _, tc := range []struct{ forwarded, want []string }{
		{[]string{"--no-daemon"}, []string{}},
		{[]string{"--no-daemon", "resume", "--last"}, []string{"resume", "--last"}},
		{[]string{"-m", "gpt", "--no-daemon", "--", "explain --no-daemon"}, []string{"-m", "gpt", "--", "explain --no-daemon"}},
	} {
		f := newFixture(t)
		if code, stderr := f.run(tc.forwarded...); code != 0 {
			t.Fatalf("%v: exit %d: %s", tc.forwarded, code, stderr)
		}
		entries := f.entries()
		server, tui := entries[0], entries[1]
		for _, argument := range server {
			if argument == "--no-daemon" {
				t.Fatalf("app-server received --no-daemon: %v", server)
			}
		}
		if got := tui[2:]; !reflect.DeepEqual(append([]string{}, got...), tc.want) {
			t.Fatalf("%v: TUI arguments %v, want %v", tc.forwarded, got, tc.want)
		}
		f.assertNoSocket()
	}
}

func TestConfigOptionsReachTheAppServer(t *testing.T) {
	f := newFixture(t)
	if code, stderr := f.run("-c", "a=1", "--profile=work", "-m", "gpt", "hello"); code != 0 {
		t.Fatalf("exit %d: %s", code, stderr)
	}
	server := f.entries()[0]
	if want := []string{"-c", "a=1", "--profile=work"}; !reflect.DeepEqual(server[3:], want) {
		t.Fatalf("app-server config %v, want %v", server[3:], want)
	}
}

func TestTUIExitStatusIsPropagated(t *testing.T) {
	f := newFixture(t, "LAUNCHER_TUI_EXIT=3")
	if code, _ := f.run(); code != 3 {
		t.Fatalf("exit %d, want 3", code)
	}
	f.assertNoSocket()
	f.assertDead("server.pid")
}

func TestInheritedOwnerMetadataNeverReachesCodex(t *testing.T) {
	// The fake codex exits 97 if any owner-origin variable reaches it.
	f := newFixture(t, "CAIRN_CODEX_OWNER_ORIGIN_CONTRACT=forged")
	if code, stderr := f.run(); code != 0 {
		t.Fatalf("served: exit %d: %s", code, stderr)
	}
	f = newFixture(t, "CAIRN_CODEX_OWNER_ORIGIN_CONTRACT=forged")
	if code, stderr := f.run("exec", "true"); code != 0 {
		t.Fatalf("direct: exit %d: %s", code, stderr)
	}
}

func TestDirectCommandsExecCodexUnchanged(t *testing.T) {
	for _, forwarded := range [][]string{
		{"exec", "--json", "do it"},
		{"--remote", "unix:///elsewhere.sock", "resume"},
		{"--version"},
		{"-m", "gpt", "mcp", "list"},
	} {
		f := newFixture(t, "LAUNCHER_TUI_EXIT=5")
		code, stderr := f.run(forwarded...)
		if code != 5 {
			t.Fatalf("%v: exit %d (codex's own status must pass through): %s", forwarded, code, stderr)
		}
		entries := f.entries()
		if len(entries) != 1 || !reflect.DeepEqual(entries[0], forwarded) {
			t.Fatalf("%v: codex saw %v", forwarded, entries)
		}
		if _, ok := f.pid("server.pid"); ok {
			t.Fatalf("%v: a direct command started an app-server", forwarded)
		}
	}
}

func TestSIGTERMStopsChildrenAndRemovesSocket(t *testing.T) {
	f := newFixture(t, "LAUNCHER_TUI_SECONDS=25")
	command, _ := f.command("resume", "--last")
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	f.waitFor("tui.pid")
	command.Process.Signal(syscall.SIGTERM)
	command.Wait()
	if code := command.ProcessState.ExitCode(); code != 128+int(syscall.SIGTERM) {
		t.Fatalf("exit %d, want %d", code, 128+int(syscall.SIGTERM))
	}
	f.assertDead("server.pid")
	f.assertDead("tui.pid")
	f.assertNoSocket()
}

func TestSIGTERMDuringStartupStopsTheAppServer(t *testing.T) {
	f := newFixture(t, "LAUNCHER_SERVER_DELAY=8")
	command, _ := f.command()
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	f.waitFor("server.pid")
	f.assertNoSocket() // the fixture has not bound yet
	command.Process.Signal(syscall.SIGTERM)
	command.Wait()
	if code := command.ProcessState.ExitCode(); code != 128+int(syscall.SIGTERM) {
		t.Fatalf("exit %d, want %d", code, 128+int(syscall.SIGTERM))
	}
	f.assertDead("server.pid")
	if _, ok := f.pid("tui.pid"); ok {
		t.Fatal("the TUI was started during startup")
	}
	f.assertNoSocket()
}

func TestAppServerDeathUnderTheTUIFailsLoudly(t *testing.T) {
	f := newFixture(t, "LAUNCHER_TUI_SECONDS=25", "LAUNCHER_SERVER_EXIT=9")
	code, stderr := f.run()
	if code != 9 {
		t.Fatalf("exit %d, want the app-server's 9: %s", code, stderr)
	}
	if !strings.Contains(stderr, "app-server exited while the TUI was running") {
		t.Fatalf("missing diagnostic: %q", stderr)
	}
	f.assertDead("server.pid")
	f.assertDead("tui.pid")
	f.assertNoSocket()
}

func TestAppServerThatNeverListensIsReported(t *testing.T) {
	f := newFixture(t, "LAUNCHER_SERVER_DELAY=30")
	f.env = append(f.env, "CODEXLAUNCH_START_WAIT=1")
	started := time.Now()
	code, stderr := f.run()
	if code != 125 || !strings.Contains(stderr, "socket never appeared") {
		t.Fatalf("exit %d: %q", code, stderr)
	}
	if time.Since(started) > 15*time.Second {
		t.Fatal("startup wait was not bounded")
	}
	f.assertDead("server.pid")
}

func TestUsageRefusals(t *testing.T) {
	for _, tc := range []struct {
		args []string
		want string
	}{
		{[]string{"--", "resume"}, "an installed codex is required"},
		{[]string{"--codex"}, "expected one argument"},
		{[]string{"--codex", "codex", "--"}, "must be an absolute path"},
		{[]string{"--codex", "/bin/true", "--wrapper"}, "argument --wrapper: expected one argument"},
		{[]string{"--codex", "/bin/true", "--wrapper", "relative"}, "--wrapper must be an absolute path"},
		{[]string{"--codex", "/bin/true", "--launcher", "relative"}, "--launcher must be an absolute path"},
		{[]string{"--codex", "/bin/true", "--ensure-wrapper", "resume"}, "--ensure-wrapper does not accept Codex arguments"},
		{[]string{"--disable-startup-update"}, "--disable-startup-update requires --config-toml PATH"},
		{[]string{"--disable-startup-update", "--config-toml", "rel.toml"}, "--config-toml must be an absolute path"},
		{[]string{"--disable-startup-update", "--ensure-wrapper", "--codex", "/bin/true", "--config-toml", "/tmp/c.toml"}, "cannot combine --disable-startup-update and --ensure-wrapper"},
		{[]string{"--codex", "/bin/true", "--config-toml", "/tmp/c.toml"}, "--config-toml requires --disable-startup-update"},
		{[]string{"--codex", "/bin/true", "--owner-origin-manifest", "/m", "--"}, "owner-origin launches are not ported"},
		{[]string{"--owner-origin-host-config=/h", "--codex", "/bin/true"}, "owner-origin launches are not ported"},
	} {
		var diagnostic bytes.Buffer
		launcher := Launcher{Exec: func(string, []string, []string) error {
			t.Fatalf("%v: reached exec", tc.args)
			return nil
		}}
		if code := launcher.Run(tc.args, nil, &diagnostic); code != 2 || !strings.Contains(diagnostic.String(), tc.want) {
			t.Fatalf("%v: exit %d, %q", tc.args, code, diagnostic.String())
		}
	}
}

func TestSocketPathBeyondTheUnixLimitIsRefused(t *testing.T) {
	long := filepath.Join(t.TempDir(), strings.Repeat("d", 100))
	var diagnostic bytes.Buffer
	code := Launcher{}.Run([]string{"--codex", "/bin/true", "--"}, []string{"XDG_RUNTIME_DIR=" + long}, &diagnostic)
	if code != 2 || !strings.Contains(diagnostic.String(), "socket path exceeds the Unix limit") {
		t.Fatalf("exit %d, %q", code, diagnostic.String())
	}
}

func TestClassify(t *testing.T) {
	for _, tc := range []struct {
		argv []string
		want Classification
	}{
		{nil, Classification{ServerConfig: []string{}}},
		{[]string{"resume", "--last"}, Classification{Command: "resume", ServerConfig: []string{}}},
		{[]string{"fork"}, Classification{Command: "fork", ServerConfig: []string{}}},
		{[]string{"write a test"}, Classification{Command: "write a test", ServerConfig: []string{}}},
		{[]string{"exec", "x"}, Classification{Direct: true, Command: "exec", ServerConfig: []string{}}},
		{[]string{"-m", "exec"}, Classification{ServerConfig: []string{}}},
		{[]string{"-m", "gpt", "exec"}, Classification{Direct: true, Command: "exec", ServerConfig: []string{}}},
		{[]string{"--", "exec"}, Classification{ServerConfig: []string{}}},
		{[]string{"--", "--remote", "--help"}, Classification{ServerConfig: []string{}}},
		{[]string{"--remote", "unix:///s"}, Classification{Direct: true, ExplicitRemote: true, ServerConfig: []string{}}},
		{[]string{"--remote=unix:///s", "resume"}, Classification{Direct: true, ExplicitRemote: true, Command: "resume", ServerConfig: []string{}}},
		{[]string{"resume", "--remote", "unix:///s"}, Classification{Direct: true, ExplicitRemote: true, Command: "resume", ServerConfig: []string{}}},
		{[]string{"resume", "--help"}, Classification{Direct: true, Command: "resume", ServerConfig: []string{}}},
		{[]string{"-V"}, Classification{Direct: true, ServerConfig: []string{}}},
		{[]string{"-i", "a.png", "b.png", "-m", "gpt", "describe"}, Classification{Command: "describe", ServerConfig: []string{}}},
		{[]string{"-i", "a.png", "exec"}, Classification{ServerConfig: []string{}}},
		{[]string{"-c", "k=v", "--enable", "f", "--disable=g", "--strict-config", "-p", "work", "--profile=home", "--config=x=1", "go"},
			Classification{Command: "go", ServerConfig: []string{"-c", "k=v", "--enable", "f", "--disable=g", "-p", "work", "--profile=home", "--config=x=1"}}},
		{[]string{"-c"}, Classification{ServerConfig: []string{"-c"}}},
		{[]string{"--model=gpt", "--sandbox", "read-only"}, Classification{ServerConfig: []string{}}},
		{[]string{"update"}, Classification{Update: true, Command: "update", ServerConfig: []string{}}},
		{[]string{"update", "--help"}, Classification{Direct: true, Command: "update", ServerConfig: []string{}}},
		{[]string{"--remote", "unix:///s", "update"}, Classification{Direct: true, ExplicitRemote: true, Command: "update", ServerConfig: []string{}}},
		{[]string{"--", "update"}, Classification{ServerConfig: []string{}}},
	} {
		if got := Classify(tc.argv); !reflect.DeepEqual(got, tc.want) {
			t.Errorf("Classify(%q) = %+v, want %+v", tc.argv, got, tc.want)
		}
	}
}

// Every global option the installed codex documents must be classified, or a
// value option would be mistaken for the subcommand.
func TestInstalledCodexOptionsAreClassified(t *testing.T) {
	codex, err := exec.LookPath("codex")
	if err != nil {
		t.Skip("installed codex unavailable")
	}
	command := exec.Command(codex, "--help")
	command.Env = append(os.Environ(), "CODEXLAUNCH_ROLE=")
	output, err := command.Output()
	if err != nil {
		t.Skipf("codex --help failed: %v", err)
	}
	_, section, found := strings.Cut(string(output), "Options:")
	if !found {
		t.Skip("codex --help has no Options section")
	}
	section, _, _ = strings.Cut(section, "Arguments:")
	for _, line := range strings.Split(section, "\n") {
		trimmed := strings.TrimSpace(line)
		if !strings.HasPrefix(line, "  ") || !strings.HasPrefix(trimmed, "-") {
			continue
		}
		name := strings.Split(strings.Split(strings.Fields(trimmed)[0], ",")[0], "=")[0]
		if len(name) > 1 && !KnownOption(name) {
			t.Errorf("unclassified global option %s", name)
		}
	}
}

func inodeOf(t *testing.T, path string) uint64 {
	t.Helper()
	info, err := os.Lstat(path)
	if err != nil {
		t.Fatal(err)
	}
	st, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		t.Fatalf("unexpected stat sys type %T", info.Sys())
	}
	return st.Ino
}

func TestCodexUpdateRewrapsSymlinkWithoutTouchingTarget(t *testing.T) {
	for _, tc := range []struct {
		name     string
		exitCode int
	}{
		{name: "success", exitCode: 0},
		{name: "nonzero", exitCode: 4},
	} {
		t.Run(tc.name, func(t *testing.T) {
			work := t.TempDir()
			binDir := filepath.Join(work, ".local", "bin")
			if err := os.MkdirAll(binDir, 0o700); err != nil {
				t.Fatal(err)
			}
			wrapperPath := filepath.Join(binDir, "codex")
			launcherPath := filepath.Join(work, "cairn-codex-launch")
			if err := os.WriteFile(launcherPath, []byte("#!/bin/sh\nexit 0\n"), 0o700); err != nil {
				t.Fatal(err)
			}
			realCodex := filepath.Join(work, "real-codex")
			script := fmt.Sprintf("#!/bin/sh\nln -sfn %s %s\nexit %d\n", shellQuote(realCodex), shellQuote(wrapperPath), tc.exitCode)
			if err := os.WriteFile(realCodex, []byte(script), 0o700); err != nil {
				t.Fatal(err)
			}
			beforeBytes := []byte(script)
			beforeInode := inodeOf(t, realCodex)

			// Seed initial wrapper as a symlink or regular file; Launcher should restore after update.
			if changed, _, err := EnsureWrapper(wrapperPath, launcherPath, realCodex); err != nil || !changed {
				t.Fatalf("initial EnsureWrapper failed: changed=%v err=%v", changed, err)
			}

			var diagnostic bytes.Buffer
			launcher := Launcher{
				WrapperPath: wrapperPath,
				SelfPath:    launcherPath,
				Exec: func(string, []string, []string) error {
					t.Fatal("update must not use Exec")
					return nil
				},
			}
			gotCode := launcher.Run([]string{"--codex", realCodex, "--wrapper", wrapperPath, "--launcher", launcherPath, "--", "update"}, []string{"HOME=" + work}, &diagnostic)
			if gotCode != tc.exitCode {
				t.Fatalf("exit code = %d, want %d (diag=%q)", gotCode, tc.exitCode, diagnostic.String())
			}

			info, err := os.Lstat(wrapperPath)
			if err != nil {
				t.Fatal(err)
			}
			if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() || info.Mode().Perm() != 0o700 {
				t.Fatalf("wrapper not restored to 0700 regular file: mode=%v", info.Mode())
			}
			wantContent, err := RenderWrapper(launcherPath, realCodex, wrapperPath)
			if err != nil {
				t.Fatal(err)
			}
			gotContent, err := os.ReadFile(wrapperPath)
			if err != nil || string(gotContent) != wantContent {
				t.Fatalf("wrapper content = %q, want %q (err=%v)", string(gotContent), wantContent, err)
			}
			afterBytes, err := os.ReadFile(realCodex)
			if err != nil || !bytes.Equal(afterBytes, beforeBytes) || inodeOf(t, realCodex) != beforeInode {
				t.Fatalf("realCodex was modified through symlink!")
			}
			if !strings.Contains(diagnostic.String(), "restored wrapper") {
				t.Fatalf("expected restoration diagnostic, got %q", diagnostic.String())
			}
		})
	}
}

func TestEnsureWrapperSymlinkDriftAndNoOp(t *testing.T) {
	work := t.TempDir()
	wrapperPath := filepath.Join(work, ".local", "bin", "codex")
	launcherPath := filepath.Join(work, "cairn-codex-launch")
	realCodex := filepath.Join(work, "real-codex")
	sentinel := []byte("#!/bin/sh\n# real-codex-sentinel\n")
	if err := os.WriteFile(launcherPath, []byte("#!/bin/sh\nexit 0\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(realCodex, sentinel, 0o700); err != nil {
		t.Fatal(err)
	}
	realInode := inodeOf(t, realCodex)

	// 1. Missing wrapper -> creates 0700 regular file.
	changed, reason, err := EnsureWrapper(wrapperPath, launcherPath, realCodex)
	if err != nil || !changed || reason != "missing" {
		t.Fatalf("missing: changed=%v reason=%q err=%v", changed, reason, err)
	}
	firstInode := inodeOf(t, wrapperPath)

	// 2. Unchanged -> no-op, preserves inode.
	changed, reason, err = EnsureWrapper(wrapperPath, launcherPath, realCodex)
	if err != nil || changed || reason != "" || inodeOf(t, wrapperPath) != firstInode {
		t.Fatalf("noop: changed=%v reason=%q err=%v", changed, reason, err)
	}

	// 3. Mode drift -> repaired.
	if err := os.Chmod(wrapperPath, 0o755); err != nil {
		t.Fatal(err)
	}
	changed, reason, err = EnsureWrapper(wrapperPath, launcherPath, realCodex)
	if err != nil || !changed || reason != "mode" {
		t.Fatalf("mode drift: changed=%v reason=%q err=%v", changed, reason, err)
	}

	// 4. Content drift -> repaired.
	if err := os.WriteFile(wrapperPath, []byte("#!/bin/sh\necho tampered\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	changed, reason, err = EnsureWrapper(wrapperPath, launcherPath, realCodex)
	if err != nil || !changed || reason != "content_drift" {
		t.Fatalf("content drift: changed=%v reason=%q err=%v", changed, reason, err)
	}

	// 5. Symlink drift -> atomically replaced without modifying realCodex.
	if err := os.Remove(wrapperPath); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(realCodex, wrapperPath); err != nil {
		t.Fatal(err)
	}
	changed, reason, err = EnsureWrapper(wrapperPath, launcherPath, realCodex)
	if err != nil || !changed || reason != "symlink" {
		t.Fatalf("symlink drift: changed=%v reason=%q err=%v", changed, reason, err)
	}
	gotSentinel, err := os.ReadFile(realCodex)
	if err != nil || !bytes.Equal(gotSentinel, sentinel) || inodeOf(t, realCodex) != realInode {
		t.Fatal("realCodex modified during symlink repair")
	}

	// 6. --ensure-wrapper CLI flag repairs missing/drifted wrapper cleanly.
	if err := os.Remove(wrapperPath); err != nil {
		t.Fatal(err)
	}
	var diag bytes.Buffer
	code := Launcher{}.Run([]string{"--ensure-wrapper", "--codex", realCodex, "--wrapper", wrapperPath, "--launcher", launcherPath}, []string{"HOME=" + work}, &diag)
	if code != 0 {
		t.Fatalf("--ensure-wrapper exited %d: %s", code, diag.String())
	}
	if info, err := os.Lstat(wrapperPath); err != nil || !info.Mode().IsRegular() || info.Mode().Perm() != 0o700 {
		t.Fatalf("wrapper not restored via --ensure-wrapper: info=%v err=%v", info, err)
	}
}

func TestEnsureWrapperRefusesSymlinkedParentDirectory(t *testing.T) {
	work := t.TempDir()
	realParent := filepath.Join(work, "real_parent")
	if err := os.MkdirAll(realParent, 0o700); err != nil {
		t.Fatal(err)
	}
	symParent := filepath.Join(work, "sym_parent")
	if err := os.Symlink(realParent, symParent); err != nil {
		t.Fatal(err)
	}
	launcherPath := filepath.Join(work, "cairn-codex-launch")
	realCodex := filepath.Join(work, "real-codex")
	if err := os.WriteFile(launcherPath, []byte("#!/bin/sh\nexit 0\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(realCodex, []byte("#!/bin/sh\nexit 0\n"), 0o700); err != nil {
		t.Fatal(err)
	}

	// 1. Direct symlinked parent when target file does not exist inside real_parent.
	symWrapper := filepath.Join(symParent, "codex")
	if changed, reason, err := EnsureWrapper(symWrapper, launcherPath, realCodex); err == nil || changed || reason != "" {
		t.Fatalf("expected symlinked-parent refusal (missing leaf), got changed=%v reason=%q err=%v", changed, reason, err)
	}
	if entries, err := os.ReadDir(realParent); err != nil || len(entries) != 0 {
		t.Fatalf("real_parent modified through symlinked parent: entries=%v err=%v", entries, err)
	}

	// 2. CLI --ensure-wrapper refuses symlinked parent with non-zero exit.
	var diag bytes.Buffer
	if code := (Launcher{}).Run([]string{"--ensure-wrapper", "--codex", realCodex, "--wrapper", symWrapper, "--launcher", launcherPath}, []string{"HOME=" + work}, &diag); code != 1 {
		t.Fatalf("CLI --ensure-wrapper on symlinked parent exited %d, want 1 (%s)", code, diag.String())
	}
	if _, statErr := os.Lstat(filepath.Join(realParent, "codex")); !os.IsNotExist(statErr) {
		t.Fatalf("real_parent/codex created through symlinked parent: err=%v", statErr)
	}

	// 3. Nested non-existent subdirectory under symlinked parent (sym_parent/sub/codex) is refused before MkdirAll.
	nestedMissing := filepath.Join(symParent, "sub", "codex")
	if changed, _, err := EnsureWrapper(nestedMissing, launcherPath, realCodex); err == nil || changed {
		t.Fatalf("expected nested symlinked-parent refusal (missing sub), got changed=%v err=%v", changed, err)
	}
	if _, statErr := os.Lstat(filepath.Join(realParent, "sub")); !os.IsNotExist(statErr) {
		t.Fatalf("real_parent/sub created through symlinked parent: err=%v", statErr)
	}

	// 4. Direct symlinked parent when real_parent/codex ALREADY exists (both drifted and valid) refuses without reading/modifying.
	existingReal := filepath.Join(realParent, "codex")
	sentinel := []byte("#!/bin/sh\n# existing-real-parent-file\n")
	if err := os.WriteFile(existingReal, sentinel, 0o700); err != nil {
		t.Fatal(err)
	}
	beforeIno := inodeOf(t, existingReal)
	if changed, _, err := EnsureWrapper(symWrapper, launcherPath, realCodex); err == nil || changed {
		t.Fatalf("expected symlinked-parent refusal (existing leaf), got changed=%v err=%v", changed, err)
	}
	if got, err := os.ReadFile(existingReal); err != nil || !bytes.Equal(got, sentinel) || inodeOf(t, existingReal) != beforeIno {
		t.Fatalf("existing real_parent/codex was modified through symlinked parent")
	}

	// 5. Nested existing subdirectory under symlinked parent (real_parent/sub already exists) is also refused.
	existingSub := filepath.Join(realParent, "sub")
	if err := os.MkdirAll(existingSub, 0o700); err != nil {
		t.Fatal(err)
	}
	if changed, _, err := EnsureWrapper(nestedMissing, launcherPath, realCodex); err == nil || changed {
		t.Fatalf("expected nested symlinked-parent refusal (existing sub), got changed=%v err=%v", changed, err)
	}
	if entries, err := os.ReadDir(existingSub); err != nil || len(entries) != 0 {
		t.Fatalf("real_parent/sub modified through symlinked parent: entries=%v err=%v", entries, err)
	}

	// 6. Non-directory parent (regular file) is refused.
	fileParent := filepath.Join(work, "not-a-dir")
	if err := os.WriteFile(fileParent, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if changed, _, err := EnsureWrapper(filepath.Join(fileParent, "codex"), launcherPath, realCodex); err == nil || changed {
		t.Fatalf("expected non-directory parent refusal, got changed=%v err=%v", changed, err)
	}
}

func TestDisableStartupUpdateCheck(t *testing.T) {
	work := t.TempDir()

	// 1. Creates new file when missing.
	missingCfg := filepath.Join(work, ".codex", "config.toml")
	changed, err := DisableStartupUpdateCheck(missingCfg)
	if err != nil || !changed {
		t.Fatalf("missing config: changed=%v err=%v", changed, err)
	}
	data, err := os.ReadFile(missingCfg)
	if err != nil || string(data) != "check_for_update_on_startup = false\n" {
		t.Fatalf("unexpected new config: %q (err=%v)", string(data), err)
	}

	// 2. Idempotent no-op when already false (preserves inode).
	beforeIno := inodeOf(t, missingCfg)
	changed, err = DisableStartupUpdateCheck(missingCfg)
	if err != nil || changed || inodeOf(t, missingCfg) != beforeIno {
		t.Fatalf("expected no-op, got changed=%v err=%v", changed, err)
	}

	// 3. Inserts before first [table] header when starting with a table.
	tableFirst := filepath.Join(work, "table-first.toml")
	if err := os.WriteFile(tableFirst, []byte("[mcp_servers.cairn]\ncommand = \"/bin/cairn\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	changed, err = DisableStartupUpdateCheck(tableFirst)
	if err != nil || !changed {
		t.Fatalf("table-first: changed=%v err=%v", changed, err)
	}
	gotTableFirst, _ := os.ReadFile(tableFirst)
	wantTableFirst := "check_for_update_on_startup = false\n\n[mcp_servers.cairn]\ncommand = \"/bin/cairn\"\n"
	if string(gotTableFirst) != wantTableFirst {
		t.Fatalf("got:\n%s\nwant:\n%s", string(gotTableFirst), wantTableFirst)
	}

	// 4. Inserts into root table before [projects] when root keys exist, and updates true -> false.
	rootKeys := filepath.Join(work, "root-keys.toml")
	if err := os.WriteFile(rootKeys, []byte("model = \"gpt-5\"\ncheck_for_update_on_startup = true\n\n[projects.\"/x\"]\ntrust_level = \"trusted\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	changed, err = DisableStartupUpdateCheck(rootKeys)
	if err != nil || !changed {
		t.Fatalf("root-keys: changed=%v err=%v", changed, err)
	}
	gotRoot, _ := os.ReadFile(rootKeys)
	wantRoot := "model = \"gpt-5\"\ncheck_for_update_on_startup = false\n\n[projects.\"/x\"]\ntrust_level = \"trusted\"\n"
	if string(gotRoot) != wantRoot {
		t.Fatalf("got:\n%s\nwant:\n%s", string(gotRoot), wantRoot)
	}

	// 5. Refuses symlink config.toml.
	symCfg := filepath.Join(work, "sym.toml")
	if err := os.Symlink(rootKeys, symCfg); err != nil {
		t.Fatal(err)
	}
	if changed, err := DisableStartupUpdateCheck(symCfg); err == nil || changed {
		t.Fatalf("expected symlink refusal, got changed=%v err=%v", changed, err)
	}

	// 6. CLI flag --disable-startup-update --config-toml works.
	cliCfg := filepath.Join(work, "cli.toml")
	if err := os.WriteFile(cliCfg, []byte("model = \"o3\"\n\n[mcp_servers.cairn]\ncommand = \"cairn\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	var diag bytes.Buffer
	if code := (Launcher{}).Run([]string{"--disable-startup-update", "--config-toml", cliCfg}, []string{"HOME=" + work}, &diag); code != 0 {
		t.Fatalf("CLI --disable-startup-update exited %d: %s", code, diag.String())
	}
	gotCLI, _ := os.ReadFile(cliCfg)
	if !strings.HasPrefix(string(gotCLI), "model = \"o3\"\ncheck_for_update_on_startup = false\n\n[mcp_servers.cairn]") {
		t.Fatalf("unexpected CLI updated TOML:\n%s", string(gotCLI))
	}
}

func TestDisableStartupUpdateCheckRefusesSymlinkedParentDirectory(t *testing.T) {
	work := t.TempDir()
	realCfgDir := filepath.Join(work, "real_cfg_dir")
	if err := os.MkdirAll(realCfgDir, 0o700); err != nil {
		t.Fatal(err)
	}
	symCfgDir := filepath.Join(work, "sym_cfg_dir")
	if err := os.Symlink(realCfgDir, symCfgDir); err != nil {
		t.Fatal(err)
	}

	// 1. Missing config.toml under symlinked parent directory is refused without creating real_cfg_dir/config.toml.
	symCfg := filepath.Join(symCfgDir, "config.toml")
	if changed, err := DisableStartupUpdateCheck(symCfg); err == nil || changed {
		t.Fatalf("expected symlinked-parent refusal (missing config.toml), got changed=%v err=%v", changed, err)
	}
	if entries, err := os.ReadDir(realCfgDir); err != nil || len(entries) != 0 {
		t.Fatalf("real_cfg_dir modified through symlinked parent: entries=%v err=%v", entries, err)
	}

	// 2. CLI --disable-startup-update --config-toml under symlinked parent exits 1.
	var diag bytes.Buffer
	if code := (Launcher{}).Run([]string{"--disable-startup-update", "--config-toml", symCfg}, []string{"HOME=" + work}, &diag); code != 1 {
		t.Fatalf("CLI --disable-startup-update on symlinked parent exited %d, want 1 (%s)", code, diag.String())
	}
	if _, statErr := os.Lstat(filepath.Join(realCfgDir, "config.toml")); !os.IsNotExist(statErr) {
		t.Fatalf("real_cfg_dir/config.toml created through symlinked parent: err=%v", statErr)
	}

	// 3. Nested missing subdirectory under symlinked parent (sym_cfg_dir/sub/config.toml) is refused.
	nestedSymCfg := filepath.Join(symCfgDir, "sub", "config.toml")
	if changed, err := DisableStartupUpdateCheck(nestedSymCfg); err == nil || changed {
		t.Fatalf("expected nested symlinked-parent refusal, got changed=%v err=%v", changed, err)
	}
	if _, statErr := os.Lstat(filepath.Join(realCfgDir, "sub")); !os.IsNotExist(statErr) {
		t.Fatalf("real_cfg_dir/sub created through symlinked parent: err=%v", statErr)
	}

	// 4. Existing config.toml inside real_cfg_dir is NOT read or updated through sym_cfg_dir.
	realCfg := filepath.Join(realCfgDir, "config.toml")
	before := []byte("model = \"gpt-5\"\ncheck_for_update_on_startup = true\n")
	if err := os.WriteFile(realCfg, before, 0o600); err != nil {
		t.Fatal(err)
	}
	beforeIno := inodeOf(t, realCfg)
	if changed, err := DisableStartupUpdateCheck(symCfg); err == nil || changed {
		t.Fatalf("expected symlinked-parent refusal (existing config.toml), got changed=%v err=%v", changed, err)
	}
	if got, err := os.ReadFile(realCfg); err != nil || !bytes.Equal(got, before) || inodeOf(t, realCfg) != beforeIno {
		t.Fatalf("existing real_cfg_dir/config.toml modified through symlinked parent")
	}
}
