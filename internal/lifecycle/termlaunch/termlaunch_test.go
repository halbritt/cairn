package termlaunch

import (
	"bufio"
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/wake"
)

// The test binary doubles as the launcher and as a fake terminal agent, so
// these tests use a real pseudo-terminal, real processes and the real socket.
func TestMain(m *testing.M) {
	switch os.Getenv("TERMLAUNCH_ROLE") {
	case "launcher":
		os.Unsetenv("TERMLAUNCH_ROLE")
		env := append(os.Environ(), "TERMLAUNCH_ROLE=agent")
		os.Exit(Launcher{Quiet: 300 * time.Millisecond, Enter: 20 * time.Millisecond}.Run(os.Args[1:], env, os.Stderr))
	case "agent":
		os.Exit(fakeAgent())
	}
	os.Exit(m.Run())
}

// fakeAgent echoes each submitted line in a marked form, as a TUI would act
// on a prompt. It reports its own pid and the socket it was given.
func fakeAgent() int {
	if os.Getenv("AGENT_PASTE") != "" {
		fmt.Print("\x1b[?2004h")
	}
	fmt.Printf("AGENT pid=%d socket=%s tty=%v\r\n", os.Getpid(), os.Getenv(SocketEnv), isTerminal(os.Stdin))
	restore, err := makeRaw(os.Stdin)
	if err != nil {
		fmt.Printf("NOTTY %v\r\n", err)
		return 3
	}
	defer restore()
	reader := bufio.NewReader(os.Stdin)
	var line []byte
	for {
		b, err := reader.ReadByte()
		if err != nil {
			return 0
		}
		switch b {
		case '\r':
			if string(line) == "quit" {
				return 7
			}
			fmt.Printf("GOT[%q]\r\n", line)
			line = line[:0]
		default:
			line = append(line, b)
		}
	}
}

type terminal struct {
	t       *testing.T
	master  *os.File
	command *exec.Cmd
	mu      sync.Mutex
	output  bytes.Buffer
}

// start runs the launcher with a pseudo-terminal as its own terminal.
func start(t *testing.T, extra ...string) *terminal {
	t.Helper()
	master, slavePath, err := openPTY()
	if err != nil {
		t.Skipf("no pseudo-terminal: %v", err)
	}
	slave, err := os.OpenFile(slavePath, os.O_RDWR|syscall.O_NOCTTY, 0)
	if err != nil {
		t.Fatal(err)
	}
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	runtime := t.TempDir()
	if err := os.Chmod(runtime, 0o700); err != nil {
		t.Fatal(err)
	}
	command := exec.Command(self, "--", self)
	command.Env = append(append(os.Environ(), "TERMLAUNCH_ROLE=launcher", "XDG_RUNTIME_DIR="+runtime), extra...)
	command.Stdin, command.Stdout, command.Stderr = slave, slave, slave
	command.SysProcAttr = &syscall.SysProcAttr{Setsid: true, Setctty: true, Ctty: 0}
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	slave.Close()
	term := &terminal{t: t, master: master, command: command}
	go func() {
		buffer := make([]byte, 4096)
		for {
			n, err := master.Read(buffer)
			term.mu.Lock()
			term.output.Write(buffer[:n])
			term.mu.Unlock()
			if err != nil {
				return
			}
		}
	}()
	t.Cleanup(func() {
		_ = command.Process.Kill()
		_ = command.Wait()
		master.Close()
	})
	return term
}

func (term *terminal) text() string {
	term.mu.Lock()
	defer term.mu.Unlock()
	return term.output.String()
}

func (term *terminal) wait(substring string) string {
	term.t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if text := term.text(); strings.Contains(text, substring) {
			return text
		}
		time.Sleep(10 * time.Millisecond)
	}
	term.t.Fatalf("terminal never showed %q; it shows %q", substring, term.text())
	return ""
}

// agent returns the fake agent's pid and wake socket from its banner.
func (term *terminal) agent() (wake.ProcessRef, string) {
	term.t.Helper()
	banner := term.wait(" tty=true")
	var pid int
	var socket string
	line := banner[strings.Index(banner, "AGENT pid="):]
	if _, err := fmt.Sscanf(line, "AGENT pid=%d socket=%s", &pid, &socket); err != nil {
		term.t.Fatalf("banner %q: %v", line, err)
	}
	return wake.ProcessRef{PID: pid}, socket
}

func TestWatcherRouteTypesTheWakePromptIntoTheIdleAgent(t *testing.T) {
	term := start(t)
	process, advertised := term.agent()
	client := wake.LauncherWake{}
	socket, ok, err := client.Socket(process)
	if err != nil || !ok || socket != advertised {
		t.Fatalf("Socket() = %q, %v, %v; the agent was given %q", socket, ok, err, advertised)
	}
	if info, err := os.Stat(socket); err != nil || info.Mode().Perm() != 0o600 {
		t.Fatalf("socket mode: %v %v", info, err)
	}
	time.Sleep(350 * time.Millisecond) // past the quiet period after start-up
	target, refusal, err := client.Prepare(context.Background(), socket, process)
	if err != nil || refusal != nil {
		t.Fatalf("Prepare: %v %v", refusal, err)
	}
	if target.LauncherPID != term.command.Process.Pid {
		t.Fatalf("launcher pid %d, want %d", target.LauncherPID, term.command.Process.Pid)
	}
	if err := client.Submit(context.Background(), target, process, "wake delivery 42."); err != nil {
		t.Fatal(err)
	}
	term.wait(`GOT["wake delivery 42."]`)
}

func TestBracketedPasteIsUsedWhenTheAgentEnabledIt(t *testing.T) {
	term := start(t, "AGENT_PASTE=1")
	process, socket := term.agent()
	time.Sleep(350 * time.Millisecond)
	client := wake.LauncherWake{}
	target, refusal, err := client.Prepare(context.Background(), socket, process)
	if err != nil || refusal != nil {
		t.Fatalf("Prepare: %v %v", refusal, err)
	}
	if err := client.Submit(context.Background(), target, process, "pasted wake"); err != nil {
		t.Fatal(err)
	}
	term.wait(`GOT["\x1b[200~pasted wake\x1b[201~"]`)
}

func TestUnsentOrRecentUserInputRefusesTheWake(t *testing.T) {
	term := start(t)
	process, socket := term.agent()
	client := wake.LauncherWake{}
	time.Sleep(350 * time.Millisecond)
	term.master.WriteString("half a thou")
	time.Sleep(400 * time.Millisecond) // quiet again, but the text is unsent
	_, refusal, err := client.Prepare(context.Background(), socket, process)
	if err != nil || refusal == nil || refusal.Condition != "terminal_target_focused" || !strings.Contains(refusal.Reason, "not submitted") {
		t.Fatalf("unsent input: %+v %v", refusal, err)
	}
	// A prepared target does not let a submit through once the user types.
	if err := client.Submit(context.Background(), wake.LauncherTarget{Socket: socket, LauncherPID: term.command.Process.Pid}, process, "wake"); err == nil || !strings.Contains(err.Error(), "WAKE_UNCERTAIN") {
		t.Fatalf("submit over unsent input: %v", err)
	}
	term.master.WriteString("ght\r")
	term.wait(`GOT["half a thought"]`)
	_, refusal, err = client.Prepare(context.Background(), socket, process)
	if err != nil || refusal == nil || !strings.Contains(refusal.Reason, "a moment ago") {
		t.Fatalf("recent input: %+v %v", refusal, err)
	}
	time.Sleep(350 * time.Millisecond)
	if _, refusal, err = client.Prepare(context.Background(), socket, process); err != nil || refusal != nil {
		t.Fatalf("after the quiet period: %+v %v", refusal, err)
	}
	if strings.Contains(term.text(), `GOT["wake"]`) {
		t.Fatal("a refused wake reached the agent")
	}
}

func TestSocketOfAnotherLauncherIsNotThisSessions(t *testing.T) {
	first, second := start(t), start(t)
	process, _ := first.agent()
	_, foreign := second.agent()
	_, refusal, err := wake.LauncherWake{}.Prepare(context.Background(), foreign, process)
	if err != nil || refusal == nil || refusal.Condition != "terminal_host_unavailable" {
		t.Fatalf("foreign socket: %+v %v", refusal, err)
	}
	missing := filepath.Join(t.TempDir(), "gone.sock")
	if _, refusal, err = (wake.LauncherWake{}).Prepare(context.Background(), missing, process); err != nil || refusal == nil {
		t.Fatalf("missing socket: %+v %v", refusal, err)
	}
}

func TestExitStatusAndSocketCleanup(t *testing.T) {
	term := start(t)
	_, socket := term.agent()
	term.master.WriteString("quit\r")
	if err := term.command.Wait(); err == nil || term.command.ProcessState.ExitCode() != 7 {
		t.Fatalf("exit %v, want the agent's status 7", term.command.ProcessState)
	}
	if _, err := os.Lstat(socket); !os.IsNotExist(err) {
		t.Fatalf("socket survived: %v", err)
	}
}

func TestSIGTERMEndsTheAgentAndRemovesTheSocket(t *testing.T) {
	term := start(t)
	process, socket := term.agent()
	term.command.Process.Signal(syscall.SIGTERM)
	_ = term.command.Wait()
	if code := term.command.ProcessState.ExitCode(); code != 128+int(syscall.SIGTERM) {
		t.Fatalf("exit %d", code)
	}
	deadline := time.Now().Add(3 * time.Second)
	for syscall.Kill(process.PID, 0) == nil && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	if syscall.Kill(process.PID, 0) == nil {
		t.Fatal("the agent outlived the launcher")
	}
	if _, err := os.Lstat(socket); !os.IsNotExist(err) {
		t.Fatalf("socket survived: %v", err)
	}
}

func TestNonTerminalRunsExecTheCommandUnchanged(t *testing.T) {
	var argv, env []string
	launcher := Launcher{Exec: func(path string, a, e []string) error {
		argv, env = append([]string{path}, a...), e
		return syscall.ENOEXEC
	}}
	reader, writer, _ := os.Pipe()
	defer reader.Close()
	defer writer.Close()
	launcher.In, launcher.Out = reader, writer
	var diagnostic bytes.Buffer
	code := launcher.Run([]string{"--", "/bin/agy", "-p", "hi"}, []string{"A=1", SocketEnv + "=/stale"}, &diagnostic)
	if code != 126 || strings.Join(argv, " ") != "/bin/agy /bin/agy -p hi" || strings.Join(env, " ") != "A=1" {
		t.Fatalf("code %d argv %v env %v", code, argv, env)
	}
	if code := launcher.Run([]string{"agy"}, nil, &diagnostic); code != 2 {
		t.Fatalf("relative command: exit %d", code)
	}
}

func TestSharedRuntimeDirectoryIsRefused(t *testing.T) {
	shared := t.TempDir()
	if err := os.Chmod(shared, 0o777); err != nil {
		t.Fatal(err)
	}
	master, slavePath, err := openPTY()
	if err != nil {
		t.Skipf("no pseudo-terminal: %v", err)
	}
	defer master.Close()
	slave, err := os.OpenFile(slavePath, os.O_RDWR|syscall.O_NOCTTY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer slave.Close()
	var diagnostic bytes.Buffer
	code := Launcher{In: slave, Out: slave}.Run([]string{"/bin/true"}, []string{"XDG_RUNTIME_DIR=" + shared}, &diagnostic)
	if code != 125 || !strings.Contains(diagnostic.String(), "not a private directory") {
		t.Fatalf("exit %d: %q", code, diagnostic.String())
	}
}

func TestInputAccounting(t *testing.T) {
	now := time.Unix(1000, 0)
	s := &session{quiet: time.Second, now: func() time.Time { return now }}
	step := func(input string, wantBusy bool) {
		t.Helper()
		if input != "" {
			s.typed([]byte(input))
		}
		now = now.Add(2 * time.Second)
		if got := s.busy() != ""; got != wantBusy {
			t.Fatalf("after %q: busy=%v, want %v (pending %d)", input, got, wantBusy, s.pending)
		}
	}
	step("", false)
	step("\x1b[I", false)             // focus report
	step("\x1b[?62;4c", false)        // terminal query reply
	step("ab", true)                  // typed, unsent
	step("\x7f\x7f", false)           // erased
	step("abc\x15", false)            // Ctrl-U
	step("abc\x03", false)            // Ctrl-C
	step("abc\r", false)              // submitted
	step("\x1b[200~x\x1b[201~", true) // pasted, unsent
	step("\r", false)
}
