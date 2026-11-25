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
	f.env = append(os.Environ(),
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
