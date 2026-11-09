package wake

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync/atomic"
	"time"
	"unicode/utf8"
)

const (
	HerdrOutputBytes     = 1024 * 1024
	HerdrCommandTimeout  = 4 * time.Second
	HerdrMaxEnvironBytes = 4 * 1024 * 1024
)

type HerdrError struct {
	Code, Message string
	Cause         error
}

func (e *HerdrError) Error() string { return e.Code + ": " + e.Message }
func (e *HerdrError) Unwrap() error { return e.Cause }

type TerminalWakeRefusal struct{ Reason, Condition string }

func (e *TerminalWakeRefusal) Error() string { return e.Reason }

type HerdrTarget struct {
	PaneID, TerminalID       string
	Revision, StateChangeSeq int64
}
type HerdrCommandRunner func(context.Context, []string, []string) (stdout, stderr []byte, exitCode int, err error)

type HerdrClient struct {
	Executable string
	ProcRoot   string
	Runner     HerdrCommandRunner
	InboxReady func(context.Context, SessionRef) (string, error)
}

func (c HerdrClient) procRoot() string {
	if c.ProcRoot != "" {
		return c.ProcRoot
	}
	return "/proc"
}

func HerdrEnvironment(process ProcessRef, baseEnv []string) ([]string, bool, error) {
	return (HerdrClient{}).HerdrEnvironment(process, baseEnv)
}
func (c HerdrClient) HerdrEnvironment(process ProcessRef, baseEnv []string) ([]string, bool, error) {
	f, err := os.Open(filepath.Join(c.procRoot(), strconv.Itoa(process.PID), "environ"))
	if err != nil {
		return nil, false, err
	}
	raw, readErr := io.ReadAll(io.LimitReader(f, HerdrMaxEnvironBytes+1))
	closeErr := f.Close()
	if readErr != nil {
		return nil, false, readErr
	}
	if closeErr != nil {
		return nil, false, closeErr
	}
	if len(raw) > HerdrMaxEnvironBytes {
		return nil, false, &HerdrError{Code: "INVALID_HOST", Message: "native environment exceeds limit"}
	}
	values := map[string]string{}
	for _, entry := range bytes.Split(raw, []byte{0}) {
		key, v, _ := bytes.Cut(entry, []byte{'='})
		if string(key) == "HERDR_ENV" || string(key) == "HERDR_SOCKET_PATH" {
			if !utf8.Valid(v) {
				return nil, false, &HerdrError{Code: "INVALID_HOST", Message: "native host environment is not UTF-8"}
			}
			values[string(key)] = string(v)
		}
	}
	if values["HERDR_ENV"] != "1" || !filepath.IsAbs(values["HERDR_SOCKET_PATH"]) {
		return nil, false, nil
	}
	result := make([]string, 0, len(baseEnv)+2)
	for _, entry := range baseEnv {
		if !strings.HasPrefix(entry, "HERDR_") {
			result = append(result, entry)
		}
	}
	return append(result, "HERDR_ENV=1", "HERDR_SOCKET_PATH="+values["HERDR_SOCKET_PATH"]), true, nil
}

func herdrFailure(args []string) string {
	if len(args) >= 2 && args[0] == "agent" && args[1] == "prompt" {
		return "WAKE_UNCERTAIN"
	}
	return "HOST_UNAVAILABLE"
}

var errHerdrOutputLimit = errors.New("Herdr output exceeded limit")

type herdrOutput struct {
	buffer   bytes.Buffer
	overflow *atomic.Bool
	cancel   context.CancelFunc
}

func (w *herdrOutput) Bytes() []byte  { return w.buffer.Bytes() }
func (w *herdrOutput) String() string { return w.buffer.String() }

func (w *herdrOutput) Write(p []byte) (int, error) {
	remaining := HerdrOutputBytes - w.buffer.Len()
	if len(p) <= remaining {
		return w.buffer.Write(p)
	}
	n, _ := w.buffer.Write(p[:remaining])
	w.overflow.Store(true)
	w.cancel()
	return n, errHerdrOutputLimit
}

// DefaultHerdrCommandRunner only terminates the child it created. Each retained
// pipe has its own byte cap, and cancellation also bounds descendant-held pipes.
func DefaultHerdrCommandRunner(ctx context.Context, argv, env []string) ([]byte, []byte, int, error) {
	args := []string{}
	if len(argv) > 0 {
		args = argv[1:]
	}
	failure := herdrFailure(args)
	if env == nil {
		return nil, nil, 0, &HerdrError{Code: failure, Message: "native process has no Herdr host environment"}
	}
	if len(argv) == 0 || argv[0] == "" {
		return nil, nil, 0, &HerdrError{Code: failure, Message: "this binding has no Herdr executable"}
	}
	bounded, cancel := context.WithTimeout(ctx, HerdrCommandTimeout)
	defer cancel()
	cmd := exec.CommandContext(bounded, argv[0], argv[1:]...)
	cmd.Env = env
	cmd.WaitDelay = 100 * time.Millisecond
	cmd.Cancel = func() error {
		if cmd.Process == nil || cmd.Process.Pid <= 1 {
			return errors.New("invalid owned Herdr child PID")
		}
		return cmd.Process.Kill()
	}
	var overflow atomic.Bool
	stdout := &herdrOutput{overflow: &overflow, cancel: cancel}
	stderr := &herdrOutput{overflow: &overflow, cancel: cancel}
	cmd.Stdout, cmd.Stderr = stdout, stderr
	err := cmd.Run()
	if overflow.Load() {
		return stdout.Bytes(), stderr.Bytes(), 0, &HerdrError{Code: failure, Message: "Herdr output exceeded limit", Cause: err}
	}
	if bounded.Err() != nil || errors.Is(err, exec.ErrWaitDelay) {
		return stdout.Bytes(), stderr.Bytes(), 0, &HerdrError{Code: failure, Message: "Herdr command did not confirm the operation", Cause: errors.Join(err, bounded.Err())}
	}
	if !utf8.Valid(stdout.Bytes()) {
		return nil, stderr.Bytes(), 0, &HerdrError{Code: failure, Message: "Herdr returned an invalid response"}
	}
	stderrBytes := []byte(strings.ToValidUTF8(stderr.String(), "\ufffd"))
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		return stdout.Bytes(), stderrBytes, exit.ExitCode(), nil
	}
	if err != nil {
		return stdout.Bytes(), stderrBytes, 0, &HerdrError{Code: failure, Message: "Herdr command did not confirm the operation", Cause: err}
	}
	return stdout.Bytes(), stderrBytes, 0, nil
}

func (c HerdrClient) Call(ctx context.Context, env []string, args ...string) (map[string]any, error) {
	failure := herdrFailure(args)
	executable := c.Executable
	if strings.TrimSpace(executable) == "" || !filepath.IsAbs(executable) {
		return nil, &HerdrError{Code: failure, Message: "this binding has no Herdr executable"}
	}
	runner := c.Runner
	if runner == nil {
		runner = DefaultHerdrCommandRunner
	}
	bounded, cancel := context.WithTimeout(ctx, HerdrCommandTimeout)
	defer cancel()
	stdout, stderr, exit, err := runner(bounded, append([]string{executable}, args...), env)
	if bounded.Err() != nil {
		return nil, &HerdrError{failure, "Herdr command did not confirm the operation", errors.Join(err, bounded.Err())}
	}
	if err != nil {
		var known *HerdrError
		if errors.As(err, &known) {
			return nil, err
		}
		return nil, &HerdrError{failure, "Herdr command did not confirm the operation", err}
	}
	if len(stdout) > HerdrOutputBytes || len(stderr) > HerdrOutputBytes {
		return nil, &HerdrError{Code: failure, Message: "Herdr output exceeded limit"}
	}
	if !utf8.Valid(stdout) {
		return nil, &HerdrError{Code: failure, Message: "Herdr returned an invalid response"}
	}
	if exit != 0 {
		parsed, decodeErr := decodeStrictJSONValue([]byte(strings.ToValidUTF8(string(stderr), "\ufffd")))
		if decodeErr == nil {
			root, _ := parsed.(map[string]any)
			detail, _ := root["error"].(map[string]any)
			code, _ := detail["code"].(string)
			if code == "pane_not_found" || code == "agent_not_found" || code == "agent_not_running" {
				return nil, &HerdrError{Code: "HOST_TARGET_GONE", Message: "Herdr target no longer exists"}
			}
		}
		return nil, &HerdrError{Code: failure, Message: "Herdr refused the host operation"}
	}
	parsed, err := decodeStrictJSONValue(stdout)
	if err != nil {
		return nil, &HerdrError{failure, "Herdr returned an invalid response", err}
	}
	root, ok := parsed.(map[string]any)
	if !ok {
		return nil, &HerdrError{Code: failure, Message: "Herdr returned an invalid response"}
	}
	result, ok := root["result"].(map[string]any)
	if !ok {
		return nil, &HerdrError{Code: failure, Message: "Herdr returned an invalid response"}
	}
	valid := true
	if len(args) >= 2 {
		switch args[0] + " " + args[1] {
		case "agent list":
			agents, ok := result["agents"].([]any)
			valid = ok
			for _, v := range agents {
				agent, ok := v.(map[string]any)
				if !ok {
					valid = false
					break
				}
				pane, ok := agent["pane_id"].(string)
				if !ok || pane == "" {
					valid = false
					break
				}
				if v := agent["agent_session"]; v != nil {
					if _, ok := v.(map[string]any); !ok {
						valid = false
						break
					}
				}
			}
		case "agent get":
			_, valid = result["agent"].(map[string]any)
		case "pane process-info":
			info, ok := result["process_info"].(map[string]any)
			valid = ok
			if _, ok := parseExactInt64(info["foreground_process_group_id"]); !ok {
				valid = false
			}
			processes, ok := info["foreground_processes"].([]any)
			valid = valid && ok
			for _, v := range processes {
				p, ok := v.(map[string]any)
				if !ok {
					valid = false
					break
				}
				if _, ok := parseExactInt64(p["pid"]); !ok {
					valid = false
					break
				}
			}
		case "agent prompt":
			_, ok := result["agent"].(map[string]any)
			valid = ok && result["type"] == "agent_prompted"
		}
	}
	if !valid {
		return nil, &HerdrError{Code: failure, Message: "Herdr returned an invalid response"}
	}
	return result, nil
}

func HostEligible(host map[string]any) bool {
	status, _ := host["agent_status"].(string)
	focused, ok := host["focused"].(bool)
	return (status == "idle" || status == "done") && ok && !focused
}
func (c HerdrClient) HostEligible(host map[string]any) bool { return HostEligible(host) }

var herdrRolloutPattern = regexp.MustCompile(`/rollout-[^/]*-([0-9a-f-]{36})\.jsonl$`)

func (c HerdrClient) HostIdentifiesSession(ctx context.Context, harness, nativeSessionID string, sessionProcess ProcessRef, host map[string]any, env []string) (bool, error) {
	if host["agent"] != harness {
		return false, nil
	}
	var session map[string]any
	if v := host["agent_session"]; v != nil {
		var ok bool
		session, ok = v.(map[string]any)
		if !ok {
			return false, nil
		}
	}
	if len(session) != 0 {
		if session["kind"] != "id" || session["value"] != nativeSessionID || session["agent"] != harness {
			return false, nil
		}
	} else if harness == HarnessCodex {
		directory := filepath.Join(c.procRoot(), strconv.Itoa(sessionProcess.PID), "fd")
		entries, err := os.ReadDir(directory)
		if err != nil {
			return false, err
		}
		ids := map[string]bool{}
		for _, entry := range entries {
			target, err := os.Readlink(filepath.Join(directory, entry.Name()))
			if errors.Is(err, os.ErrNotExist) {
				continue
			}
			if err != nil {
				return false, err
			}
			if match := herdrRolloutPattern.FindStringSubmatch(target); match != nil {
				ids[match[1]] = true
			}
		}
		if len(ids) != 1 || !ids[nativeSessionID] {
			return false, nil
		}
	} else {
		return false, nil
	}
	pane, ok := host["pane_id"].(string)
	if !ok || pane == "" {
		return false, nil
	}
	response, err := c.Call(ctx, env, "pane", "process-info", "--pane", pane)
	if err != nil {
		var e *HerdrError
		if errors.As(err, &e) && e.Code == "HOST_TARGET_GONE" {
			return false, nil
		}
		return false, err
	}
	info := response["process_info"].(map[string]any)
	pgid, _ := parseExactInt64(info["foreground_process_group_id"])
	raw, err := os.ReadFile(filepath.Join(c.procRoot(), strconv.Itoa(sessionProcess.PID), "stat"))
	if err != nil {
		return false, err
	}
	end := strings.LastIndexByte(string(raw), ')')
	if end < 0 {
		return false, &HerdrError{Code: "INVALID_HOST", Message: "native process stat is malformed"}
	}
	fields := strings.Fields(string(raw[end+1:]))
	if len(fields) < 3 {
		return false, &HerdrError{Code: "INVALID_HOST", Message: "native process stat is malformed"}
	}
	expected, err := strconv.ParseInt(fields[2], 10, 64)
	if err != nil {
		return false, err
	}
	if pgid != expected {
		return false, nil
	}
	member := false
	for _, v := range info["foreground_processes"].([]any) {
		p := v.(map[string]any)
		pid, _ := parseExactInt64(p["pid"])
		if pid == int64(sessionProcess.PID) {
			member = true
			break
		}
	}
	return member && processAliveAt(c.procRoot(), sessionProcess), nil
}

func TerminalTargetDiagnosis(identified, matches []map[string]any) (string, string) {
	if len(matches) > 1 {
		return fmt.Sprintf("%d eligible Herdr panes match this session; exactly one is required", len(matches)), "terminal_target_ambiguous"
	}
	if len(identified) == 0 {
		return "no Herdr pane identifies this session (harness, native session and foreground process); exactly one eligible pane is required", "terminal_target_missing"
	}
	notIdle, focused := 0, 0
	for _, host := range identified {
		status, _ := host["agent_status"].(string)
		if status != "idle" && status != "done" {
			notIdle++
		}
		if value, ok := host["focused"].(bool); !ok || value {
			focused++
		}
	}
	condition := "terminal_target_focused"
	if notIdle != 0 {
		condition = "terminal_target_not_idle"
	}
	return fmt.Sprintf("0 eligible Herdr panes match this session (%d not idle, %d focused); exactly one idle unfocused pane is required", notIdle, focused), condition
}

// TerminalWakeRequest carries the session inputs owned by native orchestration.
// StateDir/StatePath are used only for the Agy parent-conversation diagnosis.
type TerminalWakeRequest struct {
	Harness, NativeSessionID string
	Process                  ProcessRef
	Session                  SessionRef
	DeliveryID               string
	RequestID                string
	StateDir, StatePath      string
}
type PreparedTerminalWake struct {
	Target      HerdrTarget
	Environment []string
	Wake        WakeEnvelope
	Process     ProcessRef
}

func herdrTarget(host map[string]any) (HerdrTarget, bool) {
	pane, pok := host["pane_id"].(string)
	terminal, tok := host["terminal_id"].(string)
	revision, rok := parseExactInt64(host["revision"])
	seq, sok := parseExactInt64(host["state_change_seq"])
	return HerdrTarget{pane, terminal, revision, seq}, pok && tok && rok && sok && pane != "" && terminal != ""
}

func herdrTruthy(v any) bool {
	switch v := v.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case map[string]any:
		return len(v) != 0
	case []any:
		return len(v) != 0
	case json.Number:
		n, err := v.Float64()
		return err != nil || n != 0
	}
	if n, ok := parseExactInt64(v); ok {
		return n != 0
	}
	return true
}

func (c HerdrClient) agyParent(ctx context.Context, request TerminalWakeRequest, candidates []map[string]any, env []string) (map[string]any, error) {
	if request.Harness != "agy" {
		return nil, nil
	}
	paths, err := filepath.Glob(filepath.Join(request.StateDir, "*.json"))
	if err != nil {
		return nil, err
	}
	for _, path := range paths {
		if filepath.Clean(path) == filepath.Clean(request.StatePath) {
			continue
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		parsed, err := decodeStrictJSONValue(raw)
		if err != nil {
			continue
		}
		state, ok := parsed.(map[string]any)
		if !ok {
			continue
		}
		agent, ok := state["agent"].(map[string]any)
		if !ok || herdrTruthy(state["retired"]) || herdrTruthy(state["ending"]) {
			continue
		}
		process, ok := decodeProcessProjection(state["process"])
		if !ok || process != request.Process {
			continue
		}
		native, ok := agent["native_session_id"].(string)
		if !ok || native == request.NativeSessionID {
			continue
		}
		for _, host := range candidates {
			match, err := c.HostIdentifiesSession(ctx, request.Harness, native, process, host, env)
			if err != nil {
				return nil, err
			}
			if match {
				return agent, nil
			}
		}
	}
	return nil, nil
}

func (c HerdrClient) PrepareTerminalWake(ctx context.Context, request TerminalWakeRequest, baseEnv []string) (*PreparedTerminalWake, *TerminalWakeRefusal, error) {
	if pythonStrip(request.DeliveryID) == "" {
		return nil, nil, errors.New("delivery_id must be a nonempty string")
	}
	env, available, err := c.HerdrEnvironment(request.Process, baseEnv)
	if err != nil {
		return nil, nil, err
	}
	if !available {
		return nil, &TerminalWakeRefusal{"native process has no Herdr host environment", "terminal_host_unavailable"}, nil
	}
	response, err := c.Call(ctx, env, "agent", "list")
	if err != nil {
		return nil, nil, err
	}
	candidates := []map[string]any{}
	identified := []map[string]any{}
	matches := []map[string]any{}
	for _, v := range response["agents"].([]any) {
		host := v.(map[string]any)
		candidates = append(candidates, host)
		match, err := c.HostIdentifiesSession(ctx, request.Harness, request.NativeSessionID, request.Process, host, env)
		if err != nil {
			return nil, nil, err
		}
		if match {
			identified = append(identified, host)
			if HostEligible(host) {
				matches = append(matches, host)
			}
		}
	}
	if len(matches) != 1 {
		if len(identified) == 0 {
			parent, err := c.agyParent(ctx, request, candidates, env)
			if err != nil {
				return nil, nil, err
			}
			if parent != nil {
				display, ok := parent["display_name"].(string)
				native, nok := parent["native_session_id"].(string)
				if !ok || !nok {
					return nil, nil, &HerdrError{Code: "INVALID_HOST", Message: "Agy parent session is malformed"}
				}
				return nil, &TerminalWakeRefusal{fmt.Sprintf("Agy subagent conversation inside %s's process (native %s); it runs only when its parent messages it, so it has no wake transport", display, native), "native_transport_unavailable"}, nil
			}
		}
		reason, condition := TerminalTargetDiagnosis(identified, matches)
		return nil, &TerminalWakeRefusal{reason, condition}, nil
	}
	host := matches[0]
	target, ok := herdrTarget(host)
	if !ok {
		return nil, nil, &HerdrError{Code: "HOST_UNAVAILABLE", Message: "Herdr returned an invalid target"}
	}
	response, err = c.Call(ctx, env, "agent", "get", target.PaneID)
	if err != nil {
		return nil, nil, err
	}
	current := response["agent"].(map[string]any)
	updated, ok := herdrTarget(current)
	if !ok || updated != target || !HostEligible(current) {
		return nil, nil, nil
	}
	match, err := c.HostIdentifiesSession(ctx, request.Harness, request.NativeSessionID, request.Process, current, env)
	if err != nil {
		return nil, nil, err
	}
	if !match {
		return nil, nil, nil
	}
	if c.InboxReady == nil {
		return nil, nil, &HerdrError{Code: "HOST_UNAVAILABLE", Message: "session inbox readiness is unavailable"}
	}
	delivery, err := c.InboxReady(ctx, request.Session)
	if err != nil {
		return nil, nil, err
	}
	if delivery != request.DeliveryID {
		return nil, nil, nil
	}
	// The orchestration caller persists this uncertain attempt and its timestamp
	// before SubmitTerminalWake, just as it owns the session's other state writes.
	wake := WakeEnvelope{Session: request.Session, DeliveryID: request.DeliveryID, RequestID: request.RequestID, Status: "uncertain"}
	return &PreparedTerminalWake{Target: updated, Environment: env, Wake: wake, Process: request.Process}, nil, nil
}

func (c HerdrClient) SubmitTerminalWake(ctx context.Context, prepared PreparedTerminalWake, text string) (bool, error) {
	response, err := c.Call(ctx, prepared.Environment, "agent", "prompt", prepared.Target.PaneID, text)
	if err != nil {
		return false, err
	}
	agent, _ := response["agent"].(map[string]any)
	if response["type"] != "agent_prompted" || agent["pane_id"] != prepared.Target.PaneID || agent["terminal_id"] != prepared.Target.TerminalID {
		return false, &HerdrError{Code: "WAKE_UNCERTAIN", Message: "host did not confirm prompt submission; no automatic resend"}
	}
	return true, nil
}
