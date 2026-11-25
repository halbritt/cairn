// Package codexlaunch starts an interactive Codex TUI on its own local
// app-server, so the presence watcher can queue a wake to an idle session
// without terminal input.
//
// It is the Go port of the ordinary route in scripts/launch-codex-coordination.py.
// Interactive starts (bare TUI, prompt, resume, fork) run
// `codex app-server --listen unix://SOCK` and then `codex --remote unix://SOCK`.
// Every non-interactive subcommand, and any invocation that already names a
// --remote endpoint, execs the real codex unchanged. The owner-origin route
// (a patched Codex selected by manifest) is not ported: those options are
// refused here and remain with the Python launcher.
package codexlaunch

import (
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"slices"
	"strings"
	"syscall"
	"time"
)

// ownerEnv is never passed to Codex: without a verified manifest an inherited
// value would be forged capability metadata.
var ownerEnv = []string{
	"CAIRN_CODEX_OWNER_ORIGIN_MANIFEST",
	"CAIRN_CODEX_OWNER_ORIGIN_RUNTIME_SHA256",
	"CAIRN_CODEX_OWNER_ORIGIN_CONTRACT",
	"CAIRN_CODEX_OWNER_ORIGIN_LAUNCH_ID",
}

// Global Codex options. A value option consumes the next argument unless it is
// written as --name=value; --image also consumes further non-option values.
var valueOptions = set("-c", "--config", "--enable", "--disable", "--remote",
	"--remote-auth-token-env", "-i", "--image", "-m", "--model",
	"--local-provider", "-p", "--profile", "-s", "--sandbox",
	"-C", "--cd", "--add-dir", "-a", "--ask-for-approval")

// FlagOptions take no value. The set is not consulted while classifying; it
// records which documented options are known, for the installed-help check.
var flagOptions = set("--strict-config", "--oss", "--approve-for-me", "--no-daemon",
	"--dangerously-bypass-approvals-and-sandbox",
	"--dangerously-bypass-hook-trust", "--worktree", "--search",
	"--no-alt-screen", "-h", "--help", "-V", "--version",
	"--cairn-owner-origin-capabilities")

// Subcommands that never take the served route: they are non-interactive, or
// administration over the shared daemon.
var directCommands = set("exec", "e", "review", "login", "logout", "mcp", "plugin",
	"app-server", "remote-control", "completion", "update",
	"doctor", "sandbox", "debug", "apply", "a", "queue",
	"archive", "delete", "migrate-rollouts", "unarchive",
	"cloud", "exec-server", "features", "help", "version",
	"agents")

// --no-daemon means "run without the shared background server" and Codex
// refuses it next to --remote. The private app-server is not that daemon, so
// the flag is dropped from the option head, never from prompt text after --.
var servedDrop = set("--no-daemon")

// Options that change configuration loading go to the app-server as well.
// As in the Python launcher, a bare --strict-config is not forwarded: only
// value options and --name=value forms are collected.
var configOptions = set("-c", "--config", "--enable", "--disable", "--strict-config",
	"-p", "--profile")

func set(names ...string) map[string]bool {
	out := make(map[string]bool, len(names))
	for _, name := range names {
		out[name] = true
	}
	return out
}

// Classification is the routing decision for one codex argument list.
type Classification struct {
	Direct         bool
	ExplicitRemote bool
	Command        string // first non-option argument; "" when there is none
	ServerConfig   []string
}

// KnownOption reports whether name is a classified global option.
func KnownOption(name string) bool { return valueOptions[name] || flagOptions[name] }

// Classify routes one codex argv without disturbing literal prompt arguments.
func Classify(arguments []string) Classification {
	// Everything after a bare -- is a literal prompt; only the head counts.
	literal := slices.Index(arguments, "--")
	if literal < 0 {
		literal = len(arguments)
	}
	result := Classification{ServerConfig: []string{}}
	helpOrVersion := false
	for _, argument := range arguments[:literal] {
		if argument == "--remote" || strings.HasPrefix(argument, "--remote=") {
			result.ExplicitRemote = true
		}
		switch argument {
		case "-h", "--help", "-V", "--version", "--cairn-owner-origin-capabilities":
			helpOrVersion = true
		}
	}
	hasCommand := false
	for index := 0; index < len(arguments); {
		argument := arguments[index]
		if argument == "--" {
			break
		}
		if !strings.HasPrefix(argument, "-") {
			result.Command, hasCommand = argument, true
			break
		}
		name, _, equals := strings.Cut(argument, "=")
		if name == "--remote" {
			result.ExplicitRemote = true
		}
		if valueOptions[argument] {
			if configOptions[name] {
				result.ServerConfig = append(result.ServerConfig, argument)
			}
			if index+1 < len(arguments) {
				index++
				if configOptions[name] {
					result.ServerConfig = append(result.ServerConfig, arguments[index])
				}
				if name == "-i" || name == "--image" {
					for index+1 < len(arguments) && !strings.HasPrefix(arguments[index+1], "-") {
						index++
					}
				}
			}
			index++
			continue
		}
		if equals && configOptions[name] {
			result.ServerConfig = append(result.ServerConfig, argument)
		}
		index++
	}
	result.Direct = result.ExplicitRemote || helpOrVersion || (hasCommand && directCommands[result.Command])
	return result
}

// Served returns the TUI arguments for the served route.
func Served(forwarded []string) []string {
	literal := slices.Index(forwarded, "--")
	if literal < 0 {
		literal = len(forwarded)
	}
	served := make([]string, 0, len(forwarded))
	for _, argument := range forwarded[:literal] {
		if !servedDrop[argument] {
			served = append(served, argument)
		}
	}
	return append(served, forwarded[literal:]...)
}

// Launcher holds the process seams. The zero value uses the real ones.
type Launcher struct {
	Exec      func(path string, argv, env []string) error // defaults to syscall.Exec
	StartWait time.Duration                               // socket wait; default 5s
	StopWait  time.Duration                               // SIGTERM grace; default 5s
}

const usage = "usage: cairn-codex-launch --codex PATH [--] [CODEX ARGUMENT ...]"

// Run is the command entry point. args excludes the program name.
func (l Launcher) Run(args, environ []string, diagnostic io.Writer) int {
	codex, forwarded, err := parse(args)
	if err != nil {
		fmt.Fprintf(diagnostic, "%s\ncairn-codex-launch: error: %v\n", usage, err)
		return 2
	}
	env := withoutOwnerEnv(environ)
	if Classify(forwarded).Direct {
		run := l.Exec
		if run == nil {
			run = syscall.Exec
		}
		err := run(codex, append([]string{codex}, forwarded...), env)
		fmt.Fprintf(diagnostic, "cairn-codex-launch: cannot exec %s: %v\n", codex, err)
		return 126
	}
	return l.session(codex, forwarded, env, lookup(environ, "XDG_RUNTIME_DIR"), diagnostic)
}

func parse(args []string) (codex string, forwarded []string, err error) {
	index := 0
	for index < len(args) {
		argument := args[index]
		name, value, equals := strings.Cut(argument, "=")
		switch {
		case argument == "--":
			index++
			return finish(codex, args[index:])
		case name == "--codex" && equals:
			codex = value
			index++
		case argument == "--codex":
			if index+1 >= len(args) {
				return "", nil, errors.New("argument --codex: expected one argument")
			}
			codex = args[index+1]
			index += 2
		case name == "--owner-origin-manifest" || name == "--owner-origin-host-config":
			return "", nil, errors.New("owner-origin launches are not ported; use scripts/launch-codex-coordination.py")
		default:
			// The first argument that is not a launcher option starts the
			// forwarded codex arguments.
			return finish(codex, args[index:])
		}
	}
	return finish(codex, nil)
}

func finish(codex string, forwarded []string) (string, []string, error) {
	if codex == "" {
		return "", nil, errors.New("an installed codex is required (--codex PATH)")
	}
	if !filepath.IsAbs(codex) {
		return "", nil, errors.New("--codex must be an absolute path")
	}
	return codex, append([]string{}, forwarded...), nil
}

func withoutOwnerEnv(environ []string) []string {
	out := make([]string, 0, len(environ))
	for _, entry := range environ {
		name, _, _ := strings.Cut(entry, "=")
		if !slices.Contains(ownerEnv, name) {
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

func (l Launcher) session(codex string, forwarded, env []string, runtimeDir string, diagnostic io.Writer) (code int) {
	if runtimeDir == "" {
		runtimeDir = fmt.Sprintf("/tmp/codex-cairn-%d", os.Geteuid())
	}
	if err := os.MkdirAll(runtimeDir, 0o700); err != nil {
		fmt.Fprintf(diagnostic, "cairn codex launcher: launch failed: %v\n", err)
		return 125
	}
	if err := os.Chmod(runtimeDir, 0o700); err != nil {
		fmt.Fprintf(diagnostic, "cairn codex launcher: launch failed: %v\n", err)
		return 125
	}
	resolved, err := filepath.EvalSymlinks(runtimeDir)
	if err == nil {
		resolved, err = filepath.Abs(resolved)
	}
	if err != nil {
		fmt.Fprintf(diagnostic, "cairn codex launcher: launch failed: %v\n", err)
		return 125
	}
	socketPath := filepath.Join(resolved, fmt.Sprintf("queue-%d.sock", os.Getpid()))
	if len(socketPath)+1 > 108 {
		fmt.Fprintf(diagnostic, "%s\ncairn-codex-launch: error: socket path exceeds the Unix limit; set XDG_RUNTIME_DIR to a shorter directory\n", usage)
		return 2
	}
	if err := os.Remove(socketPath); err != nil && !errors.Is(err, os.ErrNotExist) {
		fmt.Fprintf(diagnostic, "cairn codex launcher: launch failed: %v\n", err)
		return 125
	}

	// Armed before any child exists: termination during the startup wait must
	// still stop the app-server.
	signals := make(chan os.Signal, 4)
	signal.Notify(signals, syscall.SIGTERM, syscall.SIGHUP, syscall.SIGINT)
	defer signal.Stop(signals)

	var server, tui *child
	defer func() {
		l.stop(tui)
		l.stop(server)
		if err := os.Remove(socketPath); err != nil && !errors.Is(err, os.ErrNotExist) {
			fmt.Fprintf(diagnostic, "cairn codex launcher: cannot remove %s: %v\n", socketPath, err)
		}
	}()

	endpoint := "unix://" + socketPath
	serverArgs := append([]string{codex, "app-server", "--listen", endpoint}, Classify(forwarded).ServerConfig...)
	if server, err = start(codex, serverArgs, env); err != nil {
		fmt.Fprintf(diagnostic, "cairn codex launcher: launch failed: %v\n", err)
		return 125
	}
	startWait := l.StartWait
	if startWait == 0 {
		startWait = 5 * time.Second
	}
	deadline := time.After(startWait)
	tick := time.NewTicker(50 * time.Millisecond)
	defer tick.Stop()
	for !exists(socketPath) {
		select {
		case received := <-signals:
			return 128 + int(received.(syscall.Signal))
		case <-server.done:
			fmt.Fprintln(diagnostic, "cairn codex launcher: the app-server exited before listening")
			return max(server.code(), 1)
		case <-deadline:
			fmt.Fprintln(diagnostic, "cairn codex launcher: the app-server socket never appeared")
			return 125
		case <-tick.C:
		}
	}

	tuiArgs := append([]string{codex, "--remote", endpoint}, Served(forwarded)...)
	if tui, err = start(codex, tuiArgs, env); err != nil {
		fmt.Fprintf(diagnostic, "cairn codex launcher: launch failed: %v\n", err)
		return 125
	}
	select {
	case received := <-signals:
		return 128 + int(received.(syscall.Signal))
	case <-tui.done:
		return tui.code() & 0xff
	case <-server.done:
		// The serving app-server died under the TUI: end the TUI and report
		// the failure instead of hanging.
		select {
		case <-tui.done:
			return tui.code() & 0xff
		default:
		}
		fmt.Fprintln(diagnostic, "cairn codex launcher: the app-server exited while the TUI was running")
		l.stop(tui)
		return max(server.code(), 1)
	}
}

func exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

type child struct {
	command *exec.Cmd
	done    chan struct{}
}

func start(path string, argv, env []string) (*child, error) {
	command := &exec.Cmd{Path: path, Args: argv, Env: env, Stdin: os.Stdin, Stdout: os.Stdout, Stderr: os.Stderr}
	if err := command.Start(); err != nil {
		return nil, err
	}
	started := &child{command: command, done: make(chan struct{})}
	go func() {
		_ = command.Wait()
		close(started.done)
	}()
	return started, nil
}

// code is the exit status after done is closed; a child ended by signal N
// reports -N, as subprocess.Popen.returncode does.
func (c *child) code() int {
	state := c.command.ProcessState
	if state == nil {
		return 1
	}
	if status, ok := state.Sys().(syscall.WaitStatus); ok && status.Signaled() {
		return -int(status.Signal())
	}
	return state.ExitCode()
}

func (l Launcher) stop(c *child) {
	if c == nil {
		return
	}
	select {
	case <-c.done:
		return
	default:
	}
	_ = c.command.Process.Signal(syscall.SIGTERM)
	grace := l.StopWait
	if grace == 0 {
		grace = 5 * time.Second
	}
	select {
	case <-c.done:
	case <-time.After(grace):
		_ = c.command.Process.Kill()
		<-c.done
	}
}
