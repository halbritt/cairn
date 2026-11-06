package advice

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

// ProcessResult contains the captured output and exit information of the child process.
type ProcessResult struct {
	Stdout   []byte
	Stderr   []byte
	ExitCode int
	Identity ProcessIdentity
}

// Allowlisted environment variable names for child process execution.
var allowedEnvVars = map[string]bool{
	"PATH":              true,
	"HOME":              true,
	"USER":              true,
	"LOGNAME":           true,
	"SHELL":             true,
	"LANG":              true,
	"LC_ALL":            true,
	"TERM":              true,
	"TMPDIR":            true,
	"XDG_CONFIG_HOME":   true,
	"XDG_DATA_HOME":     true,
	"XDG_CACHE_HOME":    true,
	"CLAUDE_CONFIG_DIR": true,
}

// BuildAllowlistedEnv constructs an isolated child environment, explicitly omitting
// API keys, provider routing overrides, and third-party credentials.
func BuildAllowlistedEnv(extraEnv map[string]string) []string {
	var env []string
	for _, entry := range os.Environ() {
		parts := strings.SplitN(entry, "=", 2)
		if len(parts) == 2 && allowedEnvVars[parts[0]] {
			env = append(env, entry)
		}
	}

	// Always mandate safe mode and lifecycle markers.
	env = append(env, "CLAUDE_CODE_SAFE_MODE=1")
	env = append(env, "CAIRN_LIFECYCLE_CHILD=1")

	for k, v := range extraEnv {
		env = append(env, fmt.Sprintf("%s=%s", k, v))
	}
	return env
}

type boundedReader struct {
	r        io.Reader
	limit    int64
	read     int64
	limitErr error
	kill     func()
}

func (b *boundedReader) Read(p []byte) (int, error) {
	n, err := b.r.Read(p)
	if n > 0 {
		b.read += int64(n)
		if b.read > b.limit {
			if b.kill != nil {
				b.kill()
			}
			return n, b.limitErr
		}
	}
	return n, err
}

var (
	errStdoutTooLarge = errors.New("stdout exceeded maximum output limit")
	errStderrTooLarge = errors.New("stderr exceeded maximum output limit")
)

func readStartTicks(pid int) uint64 {
	data, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if err != nil {
		return 0
	}
	s := string(data)
	idx := strings.LastIndex(s, ")")
	if idx < 0 || idx+2 >= len(s) {
		return 0
	}
	fields := strings.Fields(s[idx+2:])
	// fields[0] is state (field 3 of /proc/pid/stat)
	// fields[19] is starttime (field 22 of /proc/pid/stat)
	if len(fields) > 19 {
		var ticks uint64
		_, _ = fmt.Sscanf(fields[19], "%d", &ticks)
		return ticks
	}
	return 0
}

func readBootID() string {
	data, err := os.ReadFile("/proc/sys/kernel/random/boot_id")
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}

// RunIsolatedProcess executes a command in a dedicated process group with bounded I/O,
// owned pipes, armed deadline drain across Wait, and group-exit verification.
func RunIsolatedProcess(ctx context.Context, cmdPath string, args []string, input []byte, env []string, cwd string, onLaunch func(ProcessIdentity) error) (*ProcessResult, error) {
	cmd := exec.Command(cmdPath, args...)
	if cwd != "" {
		cmd.Dir = cwd
	}
	cmd.Env = env

	// Launch in a dedicated process group.
	cmd.SysProcAttr = &syscall.SysProcAttr{
		Setpgid: true,
	}

	stdinR, stdinW, err := os.Pipe()
	if err != nil {
		return nil, NewAdviceError(ErrModelExecutionFailed, "failed to open stdin pipe", err)
	}
	cmd.Stdin = stdinR

	stdoutR, stdoutW, err := os.Pipe()
	if err != nil {
		_ = stdinR.Close()
		_ = stdinW.Close()
		return nil, NewAdviceError(ErrModelExecutionFailed, "failed to open stdout pipe", err)
	}
	stderrR, stderrW, err := os.Pipe()
	if err != nil {
		_ = stdinR.Close()
		_ = stdinW.Close()
		_ = stdoutR.Close()
		_ = stdoutW.Close()
		return nil, NewAdviceError(ErrModelExecutionFailed, "failed to open stderr pipe", err)
	}

	cmd.Stdout = stdoutW
	cmd.Stderr = stderrW

	if err := cmd.Start(); err != nil {
		_ = stdinR.Close()
		_ = stdinW.Close()
		_ = stdoutR.Close()
		_ = stdoutW.Close()
		_ = stderrR.Close()
		_ = stderrW.Close()
		return nil, NewAdviceError(ErrModelExecutionFailed, fmt.Sprintf("failed to start child command %q: %v", cmdPath, err), err)
	}
	_ = stdinR.Close()
	_ = stdoutW.Close()
	_ = stderrW.Close()

	pid := cmd.Process.Pid
	pgid := pid
	identity := ProcessIdentity{
		PID:        pid,
		PGID:       pgid,
		StartTicks: readStartTicks(pid),
		BootID:     readBootID(),
	}

	if onLaunch != nil {
		if err := onLaunch(identity); err != nil {
			_ = stdinW.Close()
			var killErr error
			if pgid > 1 {
				kErr := syscall.Kill(-pgid, syscall.SIGKILL)
				if kErr != nil && !errors.Is(kErr, syscall.ESRCH) {
					killErr = kErr
				}
			}
			if cmd.Process != nil {
				_ = cmd.Process.Kill()
			}
			_ = stdoutR.Close()
			_ = stderrR.Close()
			_ = cmd.Wait()

			var finalProbeErr error
			if pgid > 1 {
				deadline := time.Now().Add(500 * time.Millisecond)
				for {
					finalProbeErr = syscall.Kill(-pgid, 0)
					if errors.Is(finalProbeErr, syscall.ESRCH) {
						break
					}
					if time.Now().After(deadline) {
						break
					}
					time.Sleep(10 * time.Millisecond)
				}
			}

			if pgid <= 1 || killErr != nil || !errors.Is(finalProbeErr, syscall.ESRCH) {
				cause := err
				if killErr != nil {
					cause = errors.Join(cause, killErr)
				}
				if finalProbeErr != nil && !errors.Is(finalProbeErr, syscall.ESRCH) {
					cause = errors.Join(cause, finalProbeErr)
				} else if finalProbeErr == nil {
					cause = errors.Join(cause, errors.New("process group still active after onLaunch failure"))
				}
				return nil, &CleanupUnconfirmedError{
					Identity: identity,
					Cause:    cause,
				}
			}
			return nil, NewAdviceError(ErrModelExecutionFailed, fmt.Sprintf("onLaunch gate failed: %v", err), err)
		}
	}

	go func() {
		defer stdinW.Close()
		if len(input) > 0 {
			_, _ = stdinW.Write(input)
		}
	}()

	var lastKillErr error
	var killMu sync.Mutex
	recordKillErr := func(err error) {
		if err != nil && !errors.Is(err, syscall.ESRCH) {
			killMu.Lock()
			if lastKillErr == nil {
				lastKillErr = err
			}
			killMu.Unlock()
		}
	}
	killProcessGroup := func(sig syscall.Signal) error {
		if pgid <= 1 {
			return nil
		}
		err := syscall.Kill(-pgid, sig)
		if err != nil && !errors.Is(err, syscall.ESRCH) {
			recordKillErr(err)
			return err
		}
		return nil
	}

	doneCh := make(chan struct{})
	var killedByCtx atomic.Bool
	go func() {
		select {
		case <-ctx.Done():
			killedByCtx.Store(true)
			_ = killProcessGroup(syscall.SIGKILL)
			if cmd.Process != nil {
				_ = cmd.Process.Kill()
			}
			_ = stdoutR.Close()
			_ = stderrR.Close()
		case <-doneCh:
		}
	}()

	var stdoutBuf, stderrBuf bytes.Buffer
	boundedStdout := &boundedReader{
		r:        stdoutR,
		limit:    MaxRawStdoutBytes,
		limitErr: errStdoutTooLarge,
		kill:     func() { _ = killProcessGroup(syscall.SIGKILL) },
	}
	boundedStderr := &boundedReader{
		r:        stderrR,
		limit:    MaxCLIErrOutputBytes,
		limitErr: errStderrTooLarge,
		kill:     func() { _ = killProcessGroup(syscall.SIGKILL) },
	}

	var wg sync.WaitGroup
	var stdoutErr, stderrErr error

	wg.Add(2)
	go func() {
		defer wg.Done()
		_, stdoutErr = io.Copy(&stdoutBuf, boundedStdout)
		if stdoutErr != nil {
			_ = stdoutR.Close()
			_ = killProcessGroup(syscall.SIGKILL)
		}
	}()
	go func() {
		defer wg.Done()
		_, stderrErr = io.Copy(&stderrBuf, boundedStderr)
		if stderrErr != nil {
			_ = stderrR.Close()
			_ = killProcessGroup(syscall.SIGKILL)
		}
	}()

	waitErr := cmd.Wait()

	// Verify group cleanup and check for lingering descendants.
	descendantRemained := false
	if pgid > 1 {
		probe := syscall.Kill(-pgid, 0)
		if probe == nil {
			descendantRemained = true
			_ = killProcessGroup(syscall.SIGKILL)
		}
		// Poll up to 500ms until syscall.Kill(-pgid, 0) returns ESRCH.
		deadline := time.Now().Add(500 * time.Millisecond)
		for time.Now().Before(deadline) {
			if errors.Is(syscall.Kill(-pgid, 0), syscall.ESRCH) {
				break
			}
			time.Sleep(5 * time.Millisecond)
		}
	}

	wg.Wait() // Drain buffered output while ctx remains armed on pipes
	close(doneCh)
	_ = stdoutR.Close()
	_ = stderrR.Close()

	var finalProbeErr error
	if pgid > 1 {
		finalProbeErr = syscall.Kill(-pgid, 0)
	}

	killMu.Lock()
	kErr := lastKillErr
	killMu.Unlock()

	var exitCode int
	if waitErr != nil {
		var exitErr *exec.ExitError
		if errors.As(waitErr, &exitErr) {
			exitCode = exitErr.ExitCode()
		} else {
			exitCode = -1
		}
	}

	result := &ProcessResult{
		Stdout:   stdoutBuf.Bytes(),
		Stderr:   stderrBuf.Bytes(),
		ExitCode: exitCode,
		Identity: identity,
	}

	// If any kill error occurred OR final probe is not ESRCH, return explicit ErrCleanupUnconfirmed.
	// ONLY errors.Is(finalProbeErr, syscall.ESRCH) proves group death (nil or EPERM is NOT death proof).
	if pgid > 1 && (!errors.Is(finalProbeErr, syscall.ESRCH) || kErr != nil) {
		cause := kErr
		if cause == nil {
			if finalProbeErr == nil {
				cause = errors.New("processes still active in process group after bounded cleanup")
			} else {
				cause = fmt.Errorf("process group probe returned: %w", finalProbeErr)
			}
		}
		return result, &CleanupUnconfirmedError{
			Identity: identity,
			Result:   result,
			Cause:    cause,
		}
	}

	if errors.Is(stdoutErr, errStdoutTooLarge) || stdoutBuf.Len() > MaxRawStdoutBytes {
		return result, NewAdviceError(ErrOutputLimit, fmt.Sprintf("stdout exceeded maximum limit of %d bytes", MaxRawStdoutBytes), nil)
	}
	if errors.Is(stderrErr, errStderrTooLarge) || stderrBuf.Len() > MaxCLIErrOutputBytes {
		return result, NewAdviceError(ErrOutputLimit, fmt.Sprintf("stderr exceeded maximum limit of %d bytes", MaxCLIErrOutputBytes), nil)
	}

	if killedByCtx.Load() || ctx.Err() != nil {
		if errors.Is(ctx.Err(), context.Canceled) {
			return result, NewAdviceError(ErrModelExecutionFailed, "model execution cancelled", context.Canceled)
		}
		return result, NewAdviceError(ErrModelTimeout, "model execution timed out", context.DeadlineExceeded)
	}

	if stdoutErr != nil && !errors.Is(stdoutErr, io.EOF) {
		return result, NewAdviceError(ErrModelExecutionFailed, "failed reading stdout: "+stdoutErr.Error(), stdoutErr)
	}
	if stderrErr != nil && !errors.Is(stderrErr, io.EOF) {
		return result, NewAdviceError(ErrModelExecutionFailed, "failed reading stderr: "+stderrErr.Error(), stderrErr)
	}

	if descendantRemained {
		return result, NewAdviceError(ErrModelExecutionFailed, "leader process exited while background descendant remained active in process group", nil)
	}

	if waitErr != nil {
		var exitErr *exec.ExitError
		if !errors.As(waitErr, &exitErr) {
			return result, NewAdviceError(ErrModelExecutionFailed, fmt.Sprintf("command execution failed: %v", waitErr), waitErr)
		}
	}

	return result, nil
}

// TerminateProcessGroup sends SIGKILL to the process group -pgid and confirms cleanup.
// If the process group remains active or cleanup cannot be confirmed (probe is not ESRCH),
// it returns an explicit non-nil error.
func TerminateProcessGroup(pgid int) error {
	if pgid <= 1 {
		return nil
	}
	_ = syscall.Kill(-pgid, syscall.SIGKILL)
	deadline := time.Now().Add(500 * time.Millisecond)
	for time.Now().Before(deadline) {
		if errors.Is(syscall.Kill(-pgid, 0), syscall.ESRCH) {
			return nil
		}
		time.Sleep(5 * time.Millisecond)
	}
	probe := syscall.Kill(-pgid, 0)
	if errors.Is(probe, syscall.ESRCH) {
		return nil
	}
	if probe == nil {
		return errors.New("process group still active after SIGKILL termination")
	}
	return fmt.Errorf("process group cleanup unconfirmed: %w", probe)
}
