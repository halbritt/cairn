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
// administration over the shared daemon. Note: "update" is handled separately
// as a child process so the wrapper can be restored atomically after exit.
var directCommands = set("exec", "e", "review", "login", "logout", "mcp", "plugin",
	"app-server", "remote-control", "completion",
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
	Update         bool
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
	result.Update = !result.ExplicitRemote && !helpOrVersion && hasCommand && result.Command == "update"
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
	Exec        func(path string, argv, env []string) error // defaults to syscall.Exec
	StartWait   time.Duration                               // socket wait; default 5s
	StopWait    time.Duration                               // SIGTERM grace; default 5s
	WrapperPath string                                      // optional override for test isolation
	SelfPath    string                                      // optional override for launcher executable path
}

const usage = "usage: cairn-codex-launch --codex PATH [--wrapper PATH] [--launcher PATH] [--ensure-wrapper] [--] [CODEX ARGUMENT ...]\n       cairn-codex-launch --disable-startup-update --config-toml PATH"

type options struct {
	codex                string
	wrapper              string
	launcher             string
	ensureWrapper        bool
	disableStartupUpdate bool
	configTOML           string
	forwarded            []string
}

// Run is the command entry point. args excludes the program name.
func (l Launcher) Run(args, environ []string, diagnostic io.Writer) int {
	opts, err := parse(args)
	if err != nil {
		fmt.Fprintf(diagnostic, "%s\ncairn-codex-launch: error: %v\n", usage, err)
		return 2
	}
	if opts.disableStartupUpdate {
		changed, uerr := DisableStartupUpdateCheck(opts.configTOML)
		if uerr != nil {
			fmt.Fprintf(diagnostic, "cairn-codex-launch: failed to update %s: %v\n", opts.configTOML, uerr)
			return 1
		}
		if changed {
			fmt.Fprintf(diagnostic, "cairn-codex-launch: disabled startup update check in %s\n", opts.configTOML)
		}
		return 0
	}
	if opts.ensureWrapper {
		wrapperPath, launcherPath, rerr := l.resolveWrapperAndLauncher(opts, environ)
		if rerr != nil {
			fmt.Fprintf(diagnostic, "cairn-codex-launch: %v\n", rerr)
			return 1
		}
		changed, reason, werr := EnsureWrapper(wrapperPath, launcherPath, opts.codex)
		if werr != nil {
			fmt.Fprintf(diagnostic, "cairn-codex-launch: failed to ensure wrapper %s: %v\n", wrapperPath, werr)
			return 1
		}
		if changed {
			fmt.Fprintf(diagnostic, "cairn-codex-launch: repaired wrapper %s (%s)\n", wrapperPath, reason)
		}
		return 0
	}
	env := withoutOwnerEnv(environ)
	cls := Classify(opts.forwarded)
	if cls.Direct {
		run := l.Exec
		if run == nil {
			run = syscall.Exec
		}
		err := run(opts.codex, append([]string{opts.codex}, opts.forwarded...), env)
		fmt.Fprintf(diagnostic, "cairn-codex-launch: cannot exec %s: %v\n", opts.codex, err)
		return 126
	}
	if cls.Update {
		wrapperPath, launcherPath, rerr := l.resolveWrapperAndLauncher(opts, environ)
		if rerr != nil {
			fmt.Fprintf(diagnostic, "cairn-codex-launch: %v\n", rerr)
			return 125
		}
		return l.runUpdate(opts.codex, wrapperPath, launcherPath, opts.forwarded, env, diagnostic)
	}
	return l.session(opts.codex, opts.forwarded, env, lookup(environ, "XDG_RUNTIME_DIR"), diagnostic)
}

func (l Launcher) resolveWrapperAndLauncher(opts options, environ []string) (string, string, error) {
	wrapperPath := opts.wrapper
	if wrapperPath == "" {
		wrapperPath = l.WrapperPath
	}
	if wrapperPath == "" {
		var err error
		wrapperPath, err = defaultWrapperPath(environ)
		if err != nil {
			return "", "", err
		}
	}
	launcherPath := opts.launcher
	if launcherPath == "" {
		launcherPath = l.SelfPath
	}
	if launcherPath == "" {
		exe, err := os.Executable()
		if err != nil {
			return "", "", err
		}
		launcherPath = filepath.Clean(exe)
	}
	return wrapperPath, launcherPath, nil
}

func defaultWrapperPath(environ []string) (string, error) {
	home := lookup(environ, "HOME")
	if home == "" {
		var err error
		home, err = os.UserHomeDir()
		if err != nil || home == "" {
			return "", errors.New("cannot resolve HOME for default Codex wrapper path")
		}
	}
	return filepath.Join(filepath.Clean(home), ".local", "bin", "codex"), nil
}

func parse(args []string) (options, error) {
	var opts options
	index := 0
	takeValue := func(flagName string) (string, error) {
		if index+1 >= len(args) {
			return "", fmt.Errorf("argument %s: expected one argument", flagName)
		}
		val := args[index+1]
		index += 2
		return val, nil
	}
	for index < len(args) {
		argument := args[index]
		name, value, equals := strings.Cut(argument, "=")
		switch {
		case argument == "--":
			index++
			return finish(opts, args[index:])
		case name == "--codex" && equals:
			opts.codex = value
			index++
		case argument == "--codex":
			val, err := takeValue("--codex")
			if err != nil {
				return options{}, err
			}
			opts.codex = val
		case name == "--wrapper" && equals:
			opts.wrapper = value
			index++
		case argument == "--wrapper":
			val, err := takeValue("--wrapper")
			if err != nil {
				return options{}, err
			}
			opts.wrapper = val
		case name == "--launcher" && equals:
			opts.launcher = value
			index++
		case argument == "--launcher":
			val, err := takeValue("--launcher")
			if err != nil {
				return options{}, err
			}
			opts.launcher = val
		case argument == "--ensure-wrapper":
			opts.ensureWrapper = true
			index++
		case argument == "--disable-startup-update":
			opts.disableStartupUpdate = true
			index++
		case name == "--config-toml" && equals:
			opts.configTOML = value
			index++
		case argument == "--config-toml":
			val, err := takeValue("--config-toml")
			if err != nil {
				return options{}, err
			}
			opts.configTOML = val
		case name == "--owner-origin-manifest" || name == "--owner-origin-host-config":
			return options{}, errors.New("owner-origin launches are not ported; use scripts/launch-codex-coordination.py")
		default:
			// The first argument that is not a launcher option starts the
			// forwarded codex arguments.
			return finish(opts, args[index:])
		}
	}
	return finish(opts, nil)
}

func validateCleanAbsPath(flagName, p string) error {
	if p == "" {
		return fmt.Errorf("%s must not be empty", flagName)
	}
	if strings.ContainsAny(p, "\x00\r\n") {
		return fmt.Errorf("%s contains invalid control characters", flagName)
	}
	if !filepath.IsAbs(p) {
		return fmt.Errorf("%s must be an absolute path", flagName)
	}
	if filepath.Clean(p) != p {
		return fmt.Errorf("%s must be a clean absolute path", flagName)
	}
	return nil
}

func finish(opts options, forwarded []string) (options, error) {
	if opts.disableStartupUpdate && opts.ensureWrapper {
		return options{}, errors.New("cannot combine --disable-startup-update and --ensure-wrapper")
	}
	if opts.disableStartupUpdate {
		if opts.configTOML == "" {
			return options{}, errors.New("--disable-startup-update requires --config-toml PATH")
		}
		if err := validateCleanAbsPath("--config-toml", opts.configTOML); err != nil {
			return options{}, err
		}
		if len(forwarded) > 0 {
			return options{}, errors.New("--disable-startup-update does not accept Codex arguments")
		}
		return opts, nil
	}
	if opts.configTOML != "" {
		return options{}, errors.New("--config-toml requires --disable-startup-update")
	}
	if opts.codex == "" {
		return options{}, errors.New("an installed codex is required (--codex PATH)")
	}
	if err := validateCleanAbsPath("--codex", opts.codex); err != nil {
		return options{}, err
	}
	if opts.wrapper != "" {
		if err := validateCleanAbsPath("--wrapper", opts.wrapper); err != nil {
			return options{}, err
		}
	}
	if opts.launcher != "" {
		if err := validateCleanAbsPath("--launcher", opts.launcher); err != nil {
			return options{}, err
		}
	}
	if opts.ensureWrapper && len(forwarded) > 0 {
		return options{}, errors.New("--ensure-wrapper does not accept Codex arguments")
	}
	opts.forwarded = append([]string{}, forwarded...)
	return opts, nil
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

func (l Launcher) runUpdate(codex, wrapperPath, launcherPath string, forwarded, env []string, diagnostic io.Writer) int {
	signals := make(chan os.Signal, 4)
	signal.Notify(signals, syscall.SIGTERM, syscall.SIGHUP, syscall.SIGINT)
	defer signal.Stop(signals)

	child, err := start(codex, append([]string{codex}, forwarded...), env)
	if err != nil {
		fmt.Fprintf(diagnostic, "cairn-codex-launch: cannot run %s: %v\n", codex, err)
		return 126
	}
	var exitCode int
	select {
	case received := <-signals:
		l.stop(child)
		exitCode = 128 + int(received.(syscall.Signal))
	case <-child.done:
		if c := child.code(); c < 0 {
			exitCode = 128 + (-c)
		} else {
			exitCode = c & 0xff
		}
	}
	if changed, reason, werr := EnsureWrapper(wrapperPath, launcherPath, codex); werr != nil {
		fmt.Fprintf(diagnostic, "cairn-codex-launch: failed to restore wrapper %s: %v\n", wrapperPath, werr)
		if exitCode == 0 {
			exitCode = 125
		}
	} else if changed {
		fmt.Fprintf(diagnostic, "cairn-codex-launch: restored wrapper %s (%s)\n", wrapperPath, reason)
	}
	return exitCode
}

func shellQuote(value string) string {
	if value == "" {
		return "''"
	}
	safe := true
	for i := 0; i < len(value); i++ {
		c := value[i]
		if !((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
			c == '_' || c == '.' || c == '/' || c == ':' || c == '-') {
			safe = false
			break
		}
	}
	if safe {
		return value
	}
	return "'" + strings.ReplaceAll(value, "'", `'"'"'`) + "'"
}

// RenderWrapper formats the shell wrapper script that invokes cairn-codex-launch
// behind the user's codex command.
func RenderWrapper(launcherPath, codexPath, wrapperPath string) (string, error) {
	if err := validateCleanAbsPath("launcherPath", launcherPath); err != nil {
		return "", err
	}
	if err := validateCleanAbsPath("codexPath", codexPath); err != nil {
		return "", err
	}
	if err := validateCleanAbsPath("wrapperPath", wrapperPath); err != nil {
		return "", err
	}
	return fmt.Sprintf("#!/bin/sh\nexec %s --codex %s --wrapper %s -- \"$@\"\n",
		shellQuote(launcherPath), shellQuote(codexPath), shellQuote(wrapperPath)), nil
}

// EnsureWrapper atomically ensures wrapperPath is a 0700 regular file
// invoking launcherPath with --codex codexPath and --wrapper wrapperPath.
// It never follows or writes through symlinks, leaving codexPath untouched.
func EnsureWrapper(wrapperPath, launcherPath, codexPath string) (bool, string, error) {
	want, err := RenderWrapper(launcherPath, codexPath, wrapperPath)
	if err != nil {
		return false, "", err
	}
	launcherInfo, err := os.Stat(launcherPath)
	if err != nil {
		return false, "", fmt.Errorf("launcher %s: %w", launcherPath, err)
	}
	if !launcherInfo.Mode().IsRegular() || launcherInfo.Mode().Perm()&0o111 == 0 {
		return false, "", fmt.Errorf("launcher %s is not an executable regular file", launcherPath)
	}

	reason := ""
	info, err := os.Lstat(wrapperPath)
	switch {
	case errors.Is(err, os.ErrNotExist):
		reason = "missing"
	case err != nil:
		return false, "", err
	case info.Mode()&os.ModeSymlink != 0:
		reason = "symlink"
	case !info.Mode().IsRegular():
		reason = "non_regular"
	default:
		got, rerr := readFileNoFollow(wrapperPath)
		if rerr != nil {
			reason = "unreadable"
		} else if string(got) != want {
			reason = "content_drift"
		} else if info.Mode().Perm() != 0o700 {
			reason = "mode"
		} else {
			return false, "", nil
		}
	}

	dir := filepath.Dir(wrapperPath)
	if _, derr := os.Lstat(dir); errors.Is(derr, os.ErrNotExist) {
		if merr := os.MkdirAll(dir, 0o700); merr != nil {
			return false, "", merr
		}
	}
	if werr := writeFileAtomic(wrapperPath, ".codex-wrapper-*", []byte(want), 0o700); werr != nil {
		return false, "", werr
	}
	return true, reason, nil
}

// DisableStartupUpdateCheck sets check_for_update_on_startup = false in the root
// table of a Codex config.toml file using an atomic symlink-safe replace.
func DisableStartupUpdateCheck(configPath string) (bool, error) {
	if err := validateCleanAbsPath("configPath", configPath); err != nil {
		return false, err
	}
	info, err := os.Lstat(configPath)
	if errors.Is(err, os.ErrNotExist) {
		dir := filepath.Dir(configPath)
		if _, derr := os.Lstat(dir); errors.Is(derr, os.ErrNotExist) {
			if merr := os.MkdirAll(dir, 0o700); merr != nil {
				return false, merr
			}
		}
		if werr := writeFileAtomic(configPath, ".codex-config-*", []byte("check_for_update_on_startup = false\n"), 0o600); werr != nil {
			return false, werr
		}
		return true, nil
	}
	if err != nil {
		return false, err
	}
	if info.Mode()&os.ModeSymlink != 0 {
		return false, fmt.Errorf("refusing symlink config %s", configPath)
	}
	if !info.Mode().IsRegular() {
		return false, fmt.Errorf("config %s is not a regular file", configPath)
	}
	raw, err := readFileNoFollow(configPath)
	if err != nil {
		return false, err
	}
	perm := info.Mode().Perm()
	if perm == 0 {
		perm = 0o600
	}
	content := string(raw)
	lines := strings.Split(content, "\n")
	firstTable := -1
	replaced := false
	for i, line := range lines {
		trimmed := strings.TrimSpace(strings.TrimSuffix(line, "\r"))
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		if strings.HasPrefix(trimmed, "[") {
			firstTable = i
			break
		}
		if key, val, ok := parseTOMLAssignment(trimmed); ok && key == "check_for_update_on_startup" {
			if val == "false" {
				return false, nil
			}
			lines[i] = "check_for_update_on_startup = false"
			replaced = true
			break
		}
	}
	if !replaced {
		const setting = "check_for_update_on_startup = false"
		if firstTable == 0 {
			lines = append([]string{setting, ""}, lines...)
		} else if firstTable > 0 {
			insertAt := firstTable
			for insertAt > 0 && strings.TrimSpace(strings.TrimSuffix(lines[insertAt-1], "\r")) == "" {
				insertAt--
			}
			updated := make([]string, 0, len(lines)+1)
			updated = append(updated, lines[:insertAt]...)
			updated = append(updated, setting)
			updated = append(updated, lines[insertAt:]...)
			lines = updated
		} else {
			if len(lines) > 0 && lines[len(lines)-1] == "" {
				lines[len(lines)-1] = setting
				lines = append(lines, "")
			} else {
				lines = append(lines, setting, "")
			}
		}
	}
	out := strings.Join(lines, "\n")
	if !strings.HasSuffix(out, "\n") {
		out += "\n"
	}
	if err := writeFileAtomic(configPath, ".codex-config-*", []byte(out), perm); err != nil {
		return false, err
	}
	return true, nil
}

func parseTOMLAssignment(trimmed string) (string, string, bool) {
	left, right, ok := strings.Cut(trimmed, "=")
	if !ok {
		return "", "", false
	}
	if hash := strings.IndexByte(right, '#'); hash >= 0 {
		right = right[:hash]
	}
	return strings.TrimSpace(left), strings.TrimSpace(right), true
}

func readFileNoFollow(path string) ([]byte, error) {
	fd, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	f := os.NewFile(uintptr(fd), path)
	defer f.Close()
	return io.ReadAll(io.LimitReader(f, 4<<20))
}

func writeFileAtomic(path, pattern string, data []byte, perm os.FileMode) error {
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, pattern)
	if err != nil {
		return err
	}
	tmpPath := tmp.Name()
	cleanup := true
	defer func() {
		if cleanup {
			_ = os.Remove(tmpPath)
		}
	}()
	if err := tmp.Chmod(perm); err != nil {
		_ = tmp.Close()
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Rename(tmpPath, path); err != nil {
		return err
	}
	cleanup = false
	return nil
}
