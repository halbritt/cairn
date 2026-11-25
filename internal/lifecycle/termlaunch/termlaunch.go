// Package termlaunch runs an interactive terminal agent on a pseudo-terminal
// that it owns, and accepts wake prompts over a private Unix socket.
//
// Some harnesses (Agy) have no native route for queueing a prompt to an idle
// session: the only way in is the terminal. The launcher sits between the
// user's terminal and the agent, so the presence watcher can type a wake
// prompt without any terminal multiplexer. It refuses while the user has
// typed input that is not yet submitted, or typed a moment ago.
//
// When stdin or stdout is not a terminal the command is executed directly:
// print-mode and piped runs need no wake route.
package termlaunch

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

// SocketEnv names the wake socket in the agent's environment. The watcher
// reads it from the session process.
const SocketEnv = "CAIRN_TERMINAL_WAKE_SOCKET"

const (
	maxRequestBytes = 16384
	maxTextBytes    = 8192
)

const usage = "usage: cairn-term-launch [--] COMMAND [ARGUMENT ...]"

// Launcher holds the seams. The zero value uses the real terminal.
type Launcher struct {
	Exec  func(path string, argv, env []string) error // defaults to syscall.Exec
	Quiet time.Duration                               // required silence after user input; default 3s
	Enter time.Duration                               // pause between the text and Enter; default 200ms
	In    *os.File                                    // defaults to os.Stdin
	Out   *os.File                                    // defaults to os.Stdout
}

// Run is the command entry point. args excludes the program name.
func (l Launcher) Run(args, environ []string, diagnostic io.Writer) int {
	if len(args) > 0 && args[0] == "--" {
		args = args[1:]
	}
	if len(args) == 0 || !filepath.IsAbs(args[0]) {
		fmt.Fprintf(diagnostic, "%s\ncairn-term-launch: error: COMMAND must be an absolute path\n", usage)
		return 2
	}
	in, out := l.In, l.Out
	if in == nil {
		in = os.Stdin
	}
	if out == nil {
		out = os.Stdout
	}
	environ = without(environ, SocketEnv)
	if !isTerminal(in) || !isTerminal(out) {
		run := l.Exec
		if run == nil {
			run = syscall.Exec
		}
		err := run(args[0], args, environ)
		fmt.Fprintf(diagnostic, "cairn-term-launch: cannot exec %s: %v\n", args[0], err)
		return 126
	}
	code, err := l.serve(args, environ, in, out, diagnostic)
	if err != nil {
		fmt.Fprintf(diagnostic, "cairn-term-launch: %v\n", err)
		return 125
	}
	return code
}

func without(environ []string, name string) []string {
	out := make([]string, 0, len(environ))
	for _, entry := range environ {
		if key, _, _ := strings.Cut(entry, "="); key != name {
			out = append(out, entry)
		}
	}
	return out
}

func lookup(environ []string, name string) string {
	value := ""
	for _, entry := range environ {
		if key, v, ok := strings.Cut(entry, "="); ok && key == name {
			value = v
		}
	}
	return value
}

func isTerminal(file *os.File) bool {
	_, err := unix.IoctlGetTermios(int(file.Fd()), unix.TCGETS)
	return err == nil
}

// openPTY returns the master and the path of its slave.
func openPTY() (*os.File, string, error) {
	master, err := os.OpenFile("/dev/ptmx", os.O_RDWR|syscall.O_NOCTTY|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, "", err
	}
	if err := unix.IoctlSetPointerInt(int(master.Fd()), unix.TIOCSPTLCK, 0); err != nil {
		master.Close()
		return nil, "", err
	}
	number, err := unix.IoctlGetInt(int(master.Fd()), unix.TIOCGPTN)
	if err != nil {
		master.Close()
		return nil, "", err
	}
	return master, fmt.Sprintf("/dev/pts/%d", number), nil
}

func copySize(from, to *os.File) {
	if size, err := unix.IoctlGetWinsize(int(from.Fd()), unix.TIOCGWINSZ); err == nil {
		_ = unix.IoctlSetWinsize(int(to.Fd()), unix.TIOCSWINSZ, size)
	}
}

func makeRaw(file *os.File) (restore func(), err error) {
	fd := int(file.Fd())
	saved, err := unix.IoctlGetTermios(fd, unix.TCGETS)
	if err != nil {
		return nil, err
	}
	raw := *saved
	raw.Iflag &^= unix.IGNBRK | unix.BRKINT | unix.PARMRK | unix.ISTRIP | unix.INLCR | unix.IGNCR | unix.ICRNL | unix.IXON
	raw.Oflag &^= unix.OPOST
	raw.Lflag &^= unix.ECHO | unix.ECHONL | unix.ICANON | unix.ISIG | unix.IEXTEN
	raw.Cflag &^= unix.CSIZE | unix.PARENB
	raw.Cflag |= unix.CS8
	raw.Cc[unix.VMIN] = 1
	raw.Cc[unix.VTIME] = 0
	if err := unix.IoctlSetTermios(fd, unix.TCSETS, &raw); err != nil {
		return nil, err
	}
	return func() { _ = unix.IoctlSetTermios(fd, unix.TCSETS, saved) }, nil
}

// session is the shared state between the terminal copy loops and the socket.
type session struct {
	mu      sync.Mutex
	master  *os.File
	pending int       // typed bytes not yet submitted or cancelled
	last    time.Time // last user input of any kind
	paste   bool      // the agent enabled bracketed paste
	tail    []byte    // end of the previous output chunk, for split sequences
	quiet   time.Duration
	enter   time.Duration
	now     func() time.Time
}

// typed records input from the user's terminal.
func (s *session) typed(chunk []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.last = s.now()
	if bytes.Contains(chunk, []byte("\x1b[200~")) {
		s.pending++ // pasted text sits in the composer
	}
	if len(chunk) > 0 && chunk[0] == 0x1b {
		return // an escape sequence: a key, a mouse or focus report, a query reply
	}
	for _, b := range chunk {
		switch {
		case b == '\r' || b == '\n' || b == 0x03 || b == 0x15:
			s.pending = 0 // Enter, Ctrl-C, Ctrl-U
		case b == 0x7f || b == 0x08:
			if s.pending > 0 {
				s.pending--
			}
		case b >= 0x20:
			s.pending++
		}
	}
}

// shown tracks whether the agent has bracketed paste switched on.
func (s *session) shown(chunk []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	window := append(s.tail, chunk...)
	on, off := bytes.LastIndex(window, []byte("\x1b[?2004h")), bytes.LastIndex(window, []byte("\x1b[?2004l"))
	if on >= 0 || off >= 0 {
		s.paste = on > off
	}
	if len(window) > 16 {
		window = window[len(window)-16:]
	}
	s.tail = append(s.tail[:0], window...)
}

func (s *session) busy() string {
	if s.pending > 0 {
		return "the terminal has typed input that was not submitted"
	}
	if s.now().Sub(s.last) < s.quiet {
		return "the terminal received input a moment ago"
	}
	return ""
}

type reply struct {
	OK     bool   `json:"ok"`
	Ready  bool   `json:"ready"`
	Reason string `json:"reason,omitempty"`
}

func (s *session) probe() reply {
	s.mu.Lock()
	defer s.mu.Unlock()
	if reason := s.busy(); reason != "" {
		return reply{OK: true, Reason: reason}
	}
	return reply{OK: true, Ready: true}
}

func (s *session) submit(text string) reply {
	if text == "" || len(text) > maxTextBytes || strings.ContainsAny(text, "\x00\x1b\r\n") {
		return reply{Reason: "wake text must be one line of at most 8192 bytes"}
	}
	s.mu.Lock()
	if reason := s.busy(); reason != "" {
		s.mu.Unlock()
		return reply{OK: true, Reason: reason}
	}
	body := []byte(text)
	if s.paste {
		body = []byte("\x1b[200~" + text + "\x1b[201~")
	}
	_, err := s.master.Write(body)
	s.mu.Unlock()
	if err != nil {
		return reply{Reason: "the agent terminal is closed"}
	}
	// A separate Enter after a pause: an agent that reads the text and the
	// carriage return in one burst can treat the return as part of a paste.
	time.Sleep(s.enter)
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, err := s.master.Write([]byte("\r")); err != nil {
		return reply{Reason: "the agent terminal closed before the prompt was submitted"}
	}
	return reply{OK: true, Ready: true}
}

func (s *session) handle(connection *net.UnixConn) {
	defer connection.Close()
	_ = connection.SetDeadline(time.Now().Add(10 * time.Second))
	raw, err := connection.SyscallConn()
	if err != nil {
		return
	}
	var credentials *unix.Ucred
	var credErr error
	if err := raw.Control(func(fd uintptr) {
		credentials, credErr = unix.GetsockoptUcred(int(fd), unix.SOL_SOCKET, unix.SO_PEERCRED)
	}); err != nil || credErr != nil || int(credentials.Uid) != os.Geteuid() {
		return
	}
	line, err := bufio.NewReaderSize(io.LimitReader(connection, maxRequestBytes), maxRequestBytes).ReadBytes('\n')
	if err != nil {
		return
	}
	var request struct {
		Op   string `json:"op"`
		Text string `json:"text"`
	}
	answer := reply{Reason: "unknown operation"}
	if json.Unmarshal(line, &request) == nil {
		switch request.Op {
		case "probe":
			answer = s.probe()
		case "submit":
			answer = s.submit(request.Text)
		}
	}
	data, _ := json.Marshal(answer)
	_, _ = connection.Write(append(data, '\n'))
}

func (l Launcher) serve(args, environ []string, in, out *os.File, diagnostic io.Writer) (int, error) {
	runtimeDir := lookup(environ, "XDG_RUNTIME_DIR")
	if runtimeDir == "" {
		runtimeDir = fmt.Sprintf("/tmp/cairn-term-%d", os.Geteuid())
	}
	if err := os.MkdirAll(runtimeDir, 0o700); err != nil {
		return 0, err
	}
	info, err := os.Stat(runtimeDir)
	if err != nil {
		return 0, err
	}
	if stat, ok := info.Sys().(*syscall.Stat_t); !ok || int(stat.Uid) != os.Geteuid() || info.Mode().Perm()&0o022 != 0 {
		return 0, fmt.Errorf("%s is not a private directory of this user", runtimeDir)
	}
	socketPath := filepath.Join(runtimeDir, fmt.Sprintf("cairn-term-%d.sock", os.Getpid()))
	if len(socketPath)+1 > 108 {
		return 0, errors.New("socket path exceeds the Unix limit; set XDG_RUNTIME_DIR to a shorter directory")
	}
	_ = os.Remove(socketPath)
	previous := syscall.Umask(0o177)
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: socketPath, Net: "unix"})
	syscall.Umask(previous)
	if err != nil {
		return 0, err
	}
	defer os.Remove(socketPath)
	defer listener.Close()

	master, slavePath, err := openPTY()
	if err != nil {
		return 0, err
	}
	defer master.Close()
	slave, err := os.OpenFile(slavePath, os.O_RDWR|syscall.O_NOCTTY, 0)
	if err != nil {
		return 0, err
	}
	copySize(in, master)

	quiet, enter := l.Quiet, l.Enter
	if quiet == 0 {
		quiet = 3 * time.Second
	}
	if enter == 0 {
		enter = 200 * time.Millisecond
	}
	state := &session{master: master, quiet: quiet, enter: enter, now: time.Now}

	signals := make(chan os.Signal, 8)
	signal.Notify(signals, syscall.SIGTERM, syscall.SIGHUP, syscall.SIGINT, syscall.SIGWINCH)
	defer signal.Stop(signals)

	command := &exec.Cmd{Path: args[0], Args: args, Env: append(environ, SocketEnv+"="+socketPath),
		Stdin: slave, Stdout: slave, Stderr: slave,
		SysProcAttr: &syscall.SysProcAttr{Setsid: true, Setctty: true, Ctty: 0}}
	if err := command.Start(); err != nil {
		slave.Close()
		return 0, err
	}
	slave.Close()

	restore, err := makeRaw(in)
	if err != nil {
		_ = command.Process.Kill()
		_ = command.Wait()
		return 0, err
	}
	defer restore()

	go func() {
		for {
			connection, err := listener.AcceptUnix()
			if err != nil {
				return
			}
			go state.handle(connection)
		}
	}()
	go func() {
		buffer := make([]byte, 4096)
		for {
			n, err := in.Read(buffer)
			if n > 0 {
				state.typed(buffer[:n])
				if _, werr := master.Write(buffer[:n]); werr != nil {
					return
				}
			}
			if err != nil {
				return
			}
		}
	}()
	drained := make(chan struct{})
	go func() {
		defer close(drained)
		buffer := make([]byte, 32768)
		for {
			n, err := master.Read(buffer)
			if n > 0 {
				state.shown(buffer[:n])
				if _, werr := out.Write(buffer[:n]); werr != nil {
					return
				}
			}
			if err != nil {
				return // EIO once the agent and its children close the terminal
			}
		}
	}()
	exited := make(chan struct{})
	go func() {
		_ = command.Wait()
		close(exited)
	}()

	signalled := 0
	for {
		select {
		case received := <-signals:
			switch received {
			case syscall.SIGWINCH:
				copySize(in, master)
			default:
				// The agent leads its own session; pass the request on and
				// keep relaying until it has gone.
				signalled = 128 + int(received.(syscall.Signal))
				_ = syscall.Kill(-command.Process.Pid, syscall.SIGHUP)
				go func() {
					select {
					case <-exited:
					case <-time.After(5 * time.Second):
						_ = syscall.Kill(-command.Process.Pid, syscall.SIGKILL)
					}
				}()
			}
		case <-exited:
			select {
			case <-drained:
			case <-time.After(500 * time.Millisecond):
			}
			if signalled != 0 {
				return signalled, nil
			}
			status := command.ProcessState
			if wait, ok := status.Sys().(syscall.WaitStatus); ok && wait.Signaled() {
				return (256 - int(wait.Signal())) & 0xff, nil
			}
			return status.ExitCode(), nil
		}
	}
}
