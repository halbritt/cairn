package wake

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"

	"golang.org/x/sys/unix"
)

// TerminalWakeSocketEnv is set by cairn-term-launch in the agent it runs. It
// names the launcher's private socket for typing a wake prompt.
const TerminalWakeSocketEnv = "CAIRN_TERMINAL_WAKE_SOCKET"

const terminalWakeReplyBytes = 4096

// LauncherTarget is a session whose terminal belongs to cairn-term-launch.
type LauncherTarget struct {
	Socket      string
	LauncherPID int
}

// LauncherWake talks to cairn-term-launch. It needs no terminal multiplexer.
type LauncherWake struct {
	ProcRoot string
	Timeout  time.Duration // per exchange; default 5s
}

func (w LauncherWake) procRoot() string {
	if w.ProcRoot != "" {
		return w.ProcRoot
	}
	return "/proc"
}

// Socket returns the launcher socket named in the session process's
// environment. ok is false when the session was not started by the launcher.
func (w LauncherWake) Socket(process ProcessRef) (string, bool, error) {
	f, err := os.Open(filepath.Join(w.procRoot(), strconv.Itoa(process.PID), "environ"))
	if err != nil {
		return "", false, err
	}
	raw, readErr := io.ReadAll(io.LimitReader(f, HerdrMaxEnvironBytes+1))
	closeErr := f.Close()
	if readErr != nil {
		return "", false, readErr
	}
	if closeErr != nil {
		return "", false, closeErr
	}
	if len(raw) > HerdrMaxEnvironBytes {
		return "", false, nil
	}
	socket := ""
	for _, entry := range bytes.Split(raw, []byte{0}) {
		if key, v, found := bytes.Cut(entry, []byte{'='}); found && string(key) == TerminalWakeSocketEnv && utf8.Valid(v) {
			socket = string(v)
		}
	}
	if !filepath.IsAbs(socket) || filepath.Clean(socket) != socket {
		return "", false, nil
	}
	return socket, true, nil
}

// ancestor reports whether pid is process or one of its ancestors.
func (w LauncherWake) ancestor(pid, process int) bool {
	for current, steps := process, 0; current > 1 && steps < 64; steps++ {
		if current == pid {
			return true
		}
		raw, err := os.ReadFile(filepath.Join(w.procRoot(), strconv.Itoa(current), "stat"))
		if err != nil {
			return false
		}
		// pid (comm) state ppid ...; comm may contain spaces and parentheses.
		end := bytes.LastIndexByte(raw, ')')
		fields := strings.Fields(string(raw[end+1:]))
		if end < 0 || len(fields) < 2 {
			return false
		}
		parent, err := strconv.Atoi(fields[1])
		if err != nil {
			return false
		}
		current = parent
	}
	return false
}

type launcherReply struct {
	OK     bool   `json:"ok"`
	Ready  bool   `json:"ready"`
	Reason string `json:"reason"`
}

// exchange sends one request to the launcher that owns process's terminal.
// The socket must belong to this user, and its listening process must be an
// ancestor of the session process: a stale or foreign socket path proves
// nothing.
func (w LauncherWake) exchange(ctx context.Context, socket string, process ProcessRef, request map[string]string) (LauncherTarget, launcherReply, error) {
	unavailable := func(format string, args ...any) (LauncherTarget, launcherReply, error) {
		return LauncherTarget{}, launcherReply{}, &HerdrError{Code: "HOST_UNAVAILABLE", Message: fmt.Sprintf(format, args...)}
	}
	info, err := os.Lstat(socket)
	if err != nil {
		return unavailable("terminal launcher socket is missing")
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || info.Mode()&os.ModeSocket == 0 || int(stat.Uid) != os.Geteuid() {
		return unavailable("terminal launcher socket is not a socket of this user")
	}
	timeout := w.Timeout
	if timeout == 0 {
		timeout = 5 * time.Second
	}
	dialer := net.Dialer{Timeout: timeout}
	connection, err := dialer.DialContext(ctx, "unix", socket)
	if err != nil {
		return unavailable("terminal launcher is not listening")
	}
	defer connection.Close()
	_ = connection.SetDeadline(time.Now().Add(timeout))
	raw, err := connection.(*net.UnixConn).SyscallConn()
	if err != nil {
		return unavailable("terminal launcher connection is unusable")
	}
	var credentials *unix.Ucred
	var credErr error
	if err := raw.Control(func(fd uintptr) {
		credentials, credErr = unix.GetsockoptUcred(int(fd), unix.SOL_SOCKET, unix.SO_PEERCRED)
	}); err != nil || credErr != nil {
		return unavailable("terminal launcher identity is unavailable")
	}
	if int(credentials.Uid) != os.Geteuid() || !w.ancestor(int(credentials.Pid), process.PID) {
		return unavailable("terminal launcher socket does not belong to this session's launcher")
	}
	target := LauncherTarget{Socket: socket, LauncherPID: int(credentials.Pid)}
	data, _ := json.Marshal(request)
	if _, err := connection.Write(append(data, '\n')); err != nil {
		return target, launcherReply{}, &HerdrError{Code: "WAKE_UNCERTAIN", Message: "terminal launcher did not accept the request", Cause: err}
	}
	line, err := bufio.NewReaderSize(io.LimitReader(connection, terminalWakeReplyBytes), terminalWakeReplyBytes).ReadBytes('\n')
	var answer launcherReply
	if err != nil || json.Unmarshal(line, &answer) != nil {
		return target, launcherReply{}, &HerdrError{Code: "WAKE_UNCERTAIN", Message: "terminal launcher did not confirm; no automatic resend"}
	}
	return target, answer, nil
}

// Prepare asks the launcher whether the terminal can take a wake prompt now.
// A refusal means the user has unsent or recent input.
func (w LauncherWake) Prepare(ctx context.Context, socket string, process ProcessRef) (LauncherTarget, *TerminalWakeRefusal, error) {
	target, answer, err := w.exchange(ctx, socket, process, map[string]string{"op": "probe"})
	if err != nil {
		if he, ok := err.(*HerdrError); ok && he.Code == "HOST_UNAVAILABLE" {
			return LauncherTarget{}, &TerminalWakeRefusal{he.Message, "terminal_host_unavailable"}, nil
		}
		return LauncherTarget{}, nil, err
	}
	if !answer.OK || !answer.Ready {
		reason := answer.Reason
		if reason == "" {
			reason = "the terminal launcher refused the wake"
		}
		return LauncherTarget{}, &TerminalWakeRefusal{reason, "terminal_target_focused"}, nil
	}
	return target, nil, nil
}

// Submit types text into the session's terminal and presses Enter. Any
// failure after the request was sent is uncertain and is never resent.
func (w LauncherWake) Submit(ctx context.Context, target LauncherTarget, process ProcessRef, text string) error {
	got, answer, err := w.exchange(ctx, target.Socket, process, map[string]string{"op": "submit", "text": text})
	if err != nil {
		return err
	}
	if got.LauncherPID != target.LauncherPID {
		return &HerdrError{Code: "WAKE_UNCERTAIN", Message: "terminal launcher changed after preparation; no automatic resend"}
	}
	if !answer.OK || !answer.Ready {
		return &HerdrError{Code: "WAKE_UNCERTAIN", Message: "terminal launcher did not submit the prompt: " + answer.Reason}
	}
	return nil
}
