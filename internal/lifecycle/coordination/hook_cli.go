package coordination

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"os/user"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/admission"
	"github.com/halbritt/cairn/internal/lifecycle/binding"
	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"golang.org/x/sys/unix"
)

const (
	SiteHookEventRead     = "coordination.hook.event_read"
	SiteHookCompactOutput = "coordination.hook.compact_output"
)

type InboxRecallReport func() error

type InboxRecallRequest struct {
	Config        *value.Value
	Event         *value.Value
	Observation   *value.Value
	State         *value.Value
	Message       string
	Revalidate    func(*value.Value) error
	OuterDeadline float64
	Reports       *[]InboxRecallReport
}

func hookActivationCaught(err error) bool {
	if err == nil {
		return false
	}
	var inboxErr *InboxRecallError
	if errors.As(err, &inboxErr) && inboxErr != nil {
		return true
	}
	return journal.Classified(err) || isOSPathErr(err) || sourceClass(err, "ValueError", "JSONDecodeError", "KeyError", "TypeError", "OSError", "UnicodeDecodeError", "UnicodeEncodeError")
}

func hookHandleCaught(err error) bool {
	if err == nil {
		return false
	}
	var ce *CoordinationError
	var sub *SubprocessError
	return errors.As(err, &ce) || errors.As(err, &sub) || hookActivationCaught(err)
}

func (c *CLI) HookCommand(ctx context.Context, a cliArgs, input io.Reader, out, errOut io.Writer) (int, error) {
	cfgPath := a.get("--config")
	if cfgPath == "" {
		return 0, parseFail("coordination.py", "hook requires --config")
	}
	if input == nil {
		input = os.Stdin
	}
	raw, err := io.ReadAll(io.LimitReader(input, 1024*1024+1))
	if err != nil {
		return 1, err
	}
	if len(raw) > 1024*1024 {
		return 1, nativeRefusal("INVALID_HOST", "hook input exceeds limit")
	}
	config, err := c.loadConfig(cfgPath)
	if err != nil {
		return 1, err
	}
	event, err := c.decodeHookEvent(raw)
	if err != nil {
		return 1, err
	}
	hookConfigPath, err := c.resolveHookConfigPath(cfgPath)
	if err != nil {
		return 1, err
	}
	effConfig, err := c.effectiveInboxConfig(ctx, config, event, raw)
	if err != nil {
		if hookActivationCaught(err) && value.Truth(config.Get("inbox_recall_binding")) && inboxRecallWakeValue(event) {
			if errOut != nil {
				_, _ = fmt.Fprintf(errOut, "Cairn coordination diagnostic: phase=activation error_class=%s\n", refusalErrorClass(err))
			}
			if refErr := c.recordActivationRefusal(ctx, config, event, err); refErr != nil && hookHandleCaught(refErr) {
				if errOut != nil {
					_, _ = fmt.Fprintf(errOut, "Cairn coordination diagnostic: phase=refusal_record error_class=%s\n", refusalErrorClass(refErr))
				}
			}
			return 2, nativePromptRefusedWithCause("inbox activation decision unavailable", err)
		}
		return 1, err
	}
	result, err := c.handleHookResolved(ctx, effConfig, event, a.get("--event"), hookConfigPath)
	if err != nil {
		return 1, err
	}
	return c.writeHookOutput(effConfig, event, result, out)
}

func (c *CLI) resolveHookConfigPath(cfgPath string) (string, error) {
	cfgPath = purePosixPath(cfgPath)
	if prof := c.profile(); prof != nil && prof.Routes != nil && prof.Routes.Filesystem() != nil {
		fs := prof.Routes.Filesystem()
		points, err := fs.Decode(cfgPath)
		if err != nil {
			return "", err
		}
		resolved, err := fs.Resolve(&value.Value{Kind: value.String, String: points})
		if err != nil {
			return "", err
		}
		return fs.Encode(resolved)
	}
	abs, err := filepath.Abs(cfgPath)
	if err != nil {
		return "", err
	}
	if r, err := filepath.EvalSymlinks(abs); err == nil {
		return r, nil
	}
	return abs, nil
}

func (c *CLI) decodeHookEvent(raw []byte) (*value.Value, error) {
	if prof := c.profile(); prof != nil {
		if v, err := prof.Decode(SiteHookEventRead, raw); err == nil || !errors.Is(err, &value.ProfileUnavailable{}) {
			return v, err
		}
	}
	return value.DecodeBytes(raw, 0)
}

func (c *CLI) writeHookOutput(config, event, result *value.Value, out io.Writer) (int, error) {
	if out == nil {
		return 0, nil
	}
	prof := c.profile()
	if prof == nil {
		return 1, &value.ProfileUnavailable{}
	}
	site := SiteCLIOutput
	if value.Truth(config.Get("inbox_recall_binding")) && inboxRecallWakeValue(event) {
		site = SiteHookCompactOutput
	}
	var buf bytes.Buffer
	if err := prof.Write(site, &buf, result); err != nil {
		return 1, err
	}
	if c.Compatibility != nil && value.SameText(config.Get("harness"), "codex") && buf.Len() > 12000 {
		return 1, nativeRefusal("OUTPUT_LIMIT", "lifecycle context exceeds the native output allowance")
	}
	_, err := out.Write(buf.Bytes())
	if err != nil {
		return 1, err
	}
	return 0, nil
}

func (c *CLI) HandleHook(ctx context.Context, config, event *value.Value, eventName, hookConfigPath string) (*value.Value, error) {
	effConfig, err := c.effectiveInboxConfig(ctx, config, event, nil)
	if err != nil {
		if hookHandleCaught(err) && value.Truth(config.Get("inbox_recall_binding")) && inboxRecallWakeValue(event) {
			if isNativePromptRefused(err) {
				return nil, err
			}
			return nil, nativePromptRefusedWithCause("bound inbox wake refused; required context or live ownership unavailable", err)
		}
		return nil, err
	}
	return c.handleHookResolved(ctx, effConfig, event, eventName, hookConfigPath)
}

func (c *CLI) handleHookResolved(ctx context.Context, config, event *value.Value, eventName, hookConfigPath string) (*value.Value, error) {
	var reports []InboxRecallReport
	defer func() {
		for _, fn := range reports {
			if fn != nil {
				_ = fn()
			}
		}
	}()
	res, err := c.handleHookInner(ctx, config, event, eventName, hookConfigPath, &reports)
	if err != nil {
		if isNativePromptRefused(err) {
			return nil, err
		}
		if hookHandleCaught(err) && value.Truth(config.Get("inbox_recall_binding")) && inboxRecallWakeValue(event) {
			return nil, nativePromptRefusedWithCause("bound inbox wake refused; required context or live ownership unavailable", err)
		}
		return nil, err
	}
	return res, nil
}

func (c *CLI) monotonicSeconds() float64 {
	var ts unix.Timespec
	if err := unix.ClockGettime(unix.CLOCK_MONOTONIC, &ts); err == nil {
		return float64(ts.Sec) + float64(ts.Nsec)/1e9
	}
	return float64(c.clock().UnixNano()) / 1e9
}

func (c *CLI) handleHookInner(ctx context.Context, config, event *value.Value, eventName, hookConfigPath string, reports *[]InboxRecallReport) (*value.Value, error) {
	bridgeDeadline := c.monotonicSeconds() + 12
	if c.Compatibility != nil {
		bridgeDeadline = c.Compatibility.CapDeadline(bridgeDeadline)
	}
	var wakeVal *value.Value
	acceptWake := func() (bool, error) {
		var err error
		wakeVal, err = c.wakeContext(config)
		if err != nil {
			return false, err
		}
		return value.Truth(wakeVal), nil
	}
	var evNameVal *value.Value
	if eventName != "" {
		evNameVal = value.Text(eventName)
	}
	decision, err := admission.Decide(admission.Invocation{
		Kind:         "coordination",
		Config:       config,
		Event:        event,
		EventName:    evNameVal,
		Environment:  c.admissionEnvironment(),
		Home:         c.admissionHomeFunc(),
		Filesystem:   c.admissionProbe(),
		WakeAccepted: acceptWake,
	})
	if err != nil {
		return nil, err
	}
	if decision.Verdict == "invalid" {
		return nil, nativeRefusal("INVALID_HOST", decision.Message)
	}
	if decision.Verdict == "skip" && decision.Reason != "privacy_marker" {
		return value.Mapping(), nil
	}
	if decision.Phase == "cue" {
		return c.toolCue(ctx, config, event, hookConfigPath)
	}
	if value.Truth(wakeVal) {
		cfgCopy := nativeClone(config)
		cfgCopy.Set("_wake", wakeVal)
		cfgCopy.Set("native_delivery", value.Boolean(false))
		config = cfgCopy
	}
	observation := decision.Observation()
	if value.Truth(wakeVal) && !nativeNone(wakeVal.Get("native_session_id")) && !nativeEqual(wakeVal.Get("native_session_id"), observation.Get("native_id")) {
		return value.Mapping(), nil
	}
	if decision.Reason == "privacy_marker" {
		return value.Mapping(), nil
	}
	process, err := c.OwnerProcess(config, event)
	if err != nil {
		return nil, err
	}
	if value.SameText(observation.Get("phase"), "provider") {
		return value.Mapping(), c.observeProvider(config, wakeVal, event)
	}
	return c.handleHookWithLock(ctx, config, event, observation, process, wakeVal, hookConfigPath, bridgeDeadline, reports)
}

func (c *CLI) admissionEnvironment() map[string][]rune {
	env := make(map[string][]rune)
	for _, s := range os.Environ() {
		if k, v, ok := strings.Cut(s, "="); ok {
			env[k] = admission.PathPoints(v)
		}
	}
	for _, k := range []string{"CAIRN_LIFECYCLE_DISABLED", "CAIRN_COORDINATION_DISABLED", "CAIRN_MEMORY_DISABLED", "HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "HERMES_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "CAIRN_WAKE_CONTEXT"} {
		if c.LookupEnv != nil {
			if v, ok := c.LookupEnv(k); ok {
				env[k] = admission.PathPoints(v)
			} else {
				delete(env, k)
			}
		} else if c.Env != nil {
			if v := c.Env(k); v != "" {
				env[k] = admission.PathPoints(v)
			}
		}
	}
	return env
}

func (c *CLI) admissionHomeFunc() func() ([]rune, error) {
	return func() ([]rune, error) {
		if h, ok := c.lookupEnv("HOME"); ok {
			return admission.PathPoints(h), nil
		}
		u, err := user.Current()
		if err != nil {
			return nil, err
		}
		return admission.PathPoints(u.HomeDir), nil
	}
}

func (c *CLI) admissionProbe() admission.OSProbe {
	cwd, _ := os.Getwd()
	if prof := c.profile(); prof != nil && prof.Routes != nil && prof.Routes.Filesystem() != nil {
		if p, err := prof.Routes.Filesystem().Resolve(value.Text(".")); err == nil {
			if s, err := prof.Routes.Filesystem().Encode(p); err == nil && s != "" {
				cwd = s
			}
		}
	}
	return admission.OSProbe{Directory: cwd}
}

func (c *CLI) OwnerProcess(config, event *value.Value) (*value.Value, error) {
	if c.Host != nil && c.Host.OwnerProcess != nil {
		return c.Host.OwnerProcess(config, event)
	}
	var declared any
	if hp := event.Get("host_pid"); !nativeNone(hp) {
		if hp.Kind != value.Int {
			return nil, nativeRefusal("INVALID_HOST", "invalid native process ID")
		}
		n, err := strconv.ParseInt(hp.Integer, 10, 64)
		if err != nil || n < 2 {
			return nil, nativeRefusal("INVALID_HOST", "invalid native process ID")
		}
		declared = n
	}
	var names []string
	if pn := config.Get("process_names"); pn != nil && pn.Kind == value.Array {
		for _, item := range pn.Items {
			if item != nil && item.Kind == value.String {
				names = append(names, string(item.String))
			}
		}
	}
	var exeDirs []string
	hasExeDirs := config.Get("process_exe_dirs") != nil
	if ed := config.Get("process_exe_dirs"); ed != nil && ed.Kind == value.Array {
		for _, item := range ed.Items {
			if item != nil && item.Kind == value.String {
				exeDirs = append(exeDirs, string(item.String))
			}
		}
	}
	home, _ := c.lookupEnv("HOME")
	if home == "" {
		if u, err := user.Current(); err == nil {
			home = u.HomeDir
		}
	}
	view, err := OwnerProcessViewAt(c.ProcRoot, os.Getppid(), OwnerConfig{
		Harness:           nativeText(config.Get("harness")),
		ProcessNames:      names,
		HasProcessExeDirs: hasExeDirs,
		ProcessExeDirs:    exeDirs,
		HomeDir:           home,
	}, declared)
	if err != nil {
		return nil, err
	}
	rec := view.OwnerRecord()
	return nativeMap("pid", rec.PID, "start", int64(rec.Start), "state", rec.State, "parent", rec.Parent, "boot", rec.Boot), nil
}

func (c *CLI) processAlive(proc *value.Value) (bool, error) {
	if c.Engine != nil && c.Engine.Hooks.Alive != nil {
		return c.Engine.Hooks.Alive(proc)
	}
	return NewProcessHooks(c.ProcRoot).Alive(proc)
}

func (c *CLI) handleHookWithLock(ctx context.Context, config, event, observation, process, wakeVal *value.Value, hookConfigPath string, bridgeDeadline float64, reports *[]InboxRecallReport) (*value.Value, error) {
	ns, release, err := OpenSession(config, nativeText(observation.Get("native_id")), c.newNativeSession(config, nil, "").Host, true)
	if err != nil {
		return nil, err
	}
	defer func() { _ = release() }()
	state := ns.Record
	if state == nil || state.Kind != value.Object {
		return nil, &journal.NonObjectError{Field: "state"}
	}
	if value.Truth(state.Get("ending")) && !value.Truth(state.Get("retired")) {
		if err := ns.Retire(ctx, "", 0); err != nil {
			return nil, err
		}
	}
	sameProc := false
	if value.Truth(state) {
		var err error
		sameProc, err = nativeSameProcess(process, state.Get("process"))
		if err != nil {
			return nil, err
		}
		if !sameProc && !value.Truth(state.Get("retired")) {
			alive, err := c.processAlive(state.Get("process"))
			if err != nil {
				return nil, err
			}
			if !alive {
				if err := ns.Retire(ctx, "", 0); err != nil {
					return nil, err
				}
			} else if value.SameText(config.Get("harness"), "claude") && value.Truth(state.Get("process")) && DaemonWorkerOfAt(c.ProcRoot, wakeProcessRef(process), wakeProcessRef(state.Get("process"))) {
				state.Set("process", process)
				if err := ns.Save(); err != nil {
					return nil, err
				}
				sameProc = true
			} else {
				return nil, nativeRefusal("SESSION_BUSY", "this conversation is associated with another live process")
			}
		}
	}
	if value.SameText(observation.Get("phase"), "leave") {
		if value.Truth(state) && sameProc && !value.Truth(state.Get("retired")) {
			if err := ns.Retire(ctx, "turn_ended", 1500*time.Millisecond); err != nil {
				return nil, err
			}
		}
		return value.Mapping(), nil
	}
	if err := c.ensureHookAgentSession(ctx, ns, config, observation, process, wakeVal, sameProc); err != nil {
		return nil, err
	}
	return c.handleHookPostSession(ctx, ns, config, event, observation, wakeVal, hookConfigPath, bridgeDeadline, reports)
}

func (c *CLI) ensureHookAgentSession(ctx context.Context, ns *NativeSession, config, observation, process, wakeVal *value.Value, sameProc bool) error {
	state := ns.Record
	if !value.Truth(state) || value.Truth(state.Get("retired")) || !sameProc {
		var retained *value.Value
		if value.Truth(state.Get("agent")) {
			page, err := c.call(ctx, config, "agent-directory", nativeMap("repo", config.Get("repo"), "agent_id", state.Get("agent").Get("agent_id"), "include_offline", true), NativeCallOptions{})
			if err != nil {
				return err
			}
			if agents := page.Get("agents"); agents != nil && agents.Kind == value.Array && len(agents.Items) > 0 && agents.Items[0] != nil && agents.Items[0].Kind == value.Object {
				meta := agents.Items[0].Get("metadata")
				if meta != nil && meta.Kind == value.Object {
					retained = nativeClone(meta)
					model := config.Get("model")
					if model == nil {
						model = value.Text("")
					}
					retained.Set("harness", config.Get("harness"))
					retained.Set("model", model)
					mode := "existing-session"
					if value.Truth(wakeVal) {
						mode = "fresh-worker"
					}
					retained.Set("delivery_mode", value.Text(mode))
					if !nativeEqual(retained.Get("workspace"), observation.Get("workspace")) {
						retained.Set("project", observation.Get("project"))
						retained.Set("workspace", observation.Get("workspace"))
						nativePop(retained, "project_aliases")
					}
					if value.Truth(observation.Get("observed_model")) {
						retained.Set("observed_model", observation.Get("observed_model"))
					}
					st := observation.Get("phase")
					if value.SameText(st, "start") {
						st = value.Text("idle")
					}
					retained.Set("state", st)
				}
			}
		}
		fresh := nativeMap("schema", "cairn.native-session/1", "process", process, "registration", c.registration(config, observation, retained), "workspace", observation.Get("workspace"))
		state.Members = fresh.Members
		if err := ns.Save(); err != nil {
			return err
		}
	}
	if !value.Truth(state.Get("agent")) {
		agent, err := c.call(ctx, config, "agent-register", state.Get("registration"), NativeCallOptions{})
		if err != nil {
			return err
		}
		state.Set("agent", agent)
		return ns.Save()
	}
	ref, err := c.sessionRef(state.Get("agent"))
	if err != nil {
		return err
	}
	agent, err := c.call(ctx, config, "agent-heartbeat", ref, NativeCallOptions{})
	if err == nil {
		state.Set("agent", agent)
		return nil
	}
	if nativeCode(err) != "STALE_SESSION" {
		return err
	}
	return c.resumeStaleHookSession(ctx, ns, config, observation, err)
}

func (c *CLI) resumeStaleHookSession(ctx context.Context, ns *NativeSession, config, observation *value.Value, staleErr error) error {
	state := ns.Record
	page, err := c.call(ctx, config, "agent-directory", nativeMap("repo", config.Get("repo"), "agent_id", state.Get("agent").Get("agent_id"), "include_offline", true), NativeCallOptions{})
	if err != nil {
		return err
	}
	var current *value.Value
	if agents := page.Get("agents"); agents != nil && agents.Kind == value.Array && len(agents.Items) > 0 {
		current = agents.Items[0]
	}
	if current == nil || current.Kind != value.Object || !nativeEqual(current.Get("execution_id"), state.Get("agent").Get("execution_id")) || value.Truth(current.Get("stopped")) {
		return staleErr
	}
	if value.Truth(state.Get("inbox_intent")) {
		if !value.SameText(observation.Get("phase"), "idle") {
			return staleErr
		}
		if c.Host == nil || c.Host.Journal == nil {
			return &value.ProfileUnavailable{}
		}
		flushed, err := c.Host.Journal.Flush(ctx, config, state, FlushAll)
		if err != nil {
			return err
		}
		if !flushed {
			return nativeRefusal("INTENT_PENDING", "a journaled inbox command is still uncertain")
		}
		if _, err := ns.Release(ctx, "turn_ended", true); err != nil {
			return err
		}
	}
	state.Set("registration", c.registration(config, observation, current.Get("metadata")))
	nativePop(state, "agent")
	if err := ns.Save(); err != nil {
		return err
	}
	agent, err := c.call(ctx, config, "agent-register", state.Get("registration"), NativeCallOptions{})
	if err != nil {
		return err
	}
	state.Set("agent", agent)
	return ns.Save()
}

func (c *CLI) handleHookPostSession(ctx context.Context, ns *NativeSession, config, event, observation, wakeVal *value.Value, hookConfigPath string, bridgeDeadline float64, reports *[]InboxRecallReport) (*value.Value, error) {
	state := ns.Record
	if value.SameText(observation.Get("event"), "ToolStart") || value.SameText(observation.Get("event"), "ToolEnd") {
		return c.opencodeToolEvent(ctx, config, state, ns.Path, observation, event)
	}
	current := state.Get("agent")
	metadata := nativeClone(current.Get("metadata"))
	mode := "existing-session"
	if value.Truth(wakeVal) {
		mode = "fresh-worker"
	}
	metadata.Set("delivery_mode", value.Text(mode))
	if !nativeEqual(observation.Get("workspace"), state.Get("workspace")) {
		metadata.Set("project", observation.Get("project"))
		metadata.Set("workspace", observation.Get("workspace"))
		nativePop(metadata, "project_aliases")
	}
	if value.Truth(observation.Get("observed_model")) {
		metadata.Set("observed_model", observation.Get("observed_model"))
	}
	if nativeContains([]string{"busy", "idle", "interrupted"}, nativeText(observation.Get("phase"))) {
		st := "idle"
		if value.SameText(observation.Get("phase"), "busy") {
			st = "busy"
		}
		metadata.Set("state", value.Text(st))
	}
	if !nativeEqual(metadata, current.Get("metadata")) {
		ref, err := c.sessionRef(current)
		if err != nil {
			return nil, err
		}
		updated, err := c.call(ctx, config, "agent-context", nativeMap("request_id", c.newUUID(), "session", ref, "expected_revision", current.Get("context_revision"), "metadata", metadata), NativeCallOptions{})
		if err != nil {
			return nil, err
		}
		state.Set("agent", updated)
	}
	state.Set("workspace", observation.Get("workspace"))
	if err := ns.Save(); err != nil {
		return nil, err
	}
	if err := c.associateWake(ctx, config, state, ns.Path, observation); err != nil {
		return nil, err
	}
	if value.SameText(observation.Get("phase"), "interrupted") {
		return value.Mapping(), nil
	}
	return c.handleHookInboxAndOutput(ctx, ns, config, event, observation, wakeVal, hookConfigPath, bridgeDeadline, reports)
}

func (c *CLI) handleHookInboxAndOutput(ctx context.Context, ns *NativeSession, config, event, observation, wakeVal *value.Value, _ string, bridgeDeadline float64, reports *[]InboxRecallReport) (*value.Value, error) {
	state := ns.Record
	joined := false
	if value.SameText(config.Get("harness"), "claude") {
		adm, err := c.claudePromptAdmission(state, observation, event)
		if err != nil {
			return nil, err
		}
		if err := ns.Save(); err != nil {
			return nil, err
		}
		joined = adm != nil && adm.Kind == value.Object && value.Truth(adm.Get("joined"))
		if adm != nil && adm.Kind == value.Object && value.Truth(adm.Get("refused_delivery")) {
			return nil, nativePromptRefused("joined channel wake rejected; a fresh copy retries when idle")
		}
	}
	binding, err := c.wakeBinding(config, state, event, joined)
	if err != nil {
		return nil, err
	}
	if value.SameText(config.Get("harness"), "claude") && value.Truth(binding) {
		if err := c.refuseConflictingClaudeWake(ctx, config, state, ns.Path, binding); err != nil {
			return nil, err
		}
	}
	inboxVal, err := c.inboxContext(ctx, config, state, ns.Path, observation, binding)
	if err != nil {
		return nil, err
	}
	inbox := nativeText(inboxVal)
	if value.SameText(config.Get("harness"), "opencode") && value.SameText(observation.Get("event"), "TurnStart") && value.Truth(state.Get("inbox_attempt").Get("turn_exclusive")) && nativeEqual(state.Get("inbox_attempt").Get("native_turn_id"), observation.Get("native_turn_id")) {
		since, err := bootTicksNow()
		if err != nil {
			return nil, err
		}
		state.Set("opencode_turn_since", since)
		if err := ns.Save(); err != nil {
			return nil, err
		}
	}
	if value.SameText(observation.Get("phase"), "idle") {
		if inbox != "" {
			current := state.Get("agent")
			ref, err := c.sessionRef(current)
			if err != nil {
				return nil, err
			}
			meta := nativeClone(current.Get("metadata"))
			meta.Set("state", value.Text("busy"))
			updated, err := c.call(ctx, config, "agent-context", nativeMap("request_id", c.newUUID(), "session", ref, "expected_revision", current.Get("context_revision"), "metadata", meta), NativeCallOptions{})
			if err != nil {
				return nil, err
			}
			state.Set("agent", updated)
			if err := ns.Save(); err != nil {
				return nil, err
			}
			dec := "block"
			if value.SameText(config.Get("harness"), "agy") {
				dec = "continue"
			}
			return nativeMap("decision", dec, "reason", inbox), nil
		}
		return value.Mapping(), nil
	}
	return c.formatHookTurnOutput(ctx, ns, config, event, observation, wakeVal, inbox, bridgeDeadline, reports)
}

func (c *CLI) formatHookTurnOutput(ctx context.Context, ns *NativeSession, config, event, observation, wakeVal *value.Value, inbox string, bridgeDeadline float64, reports *[]InboxRecallReport) (*value.Value, error) {
	state := ns.Record
	agent := state.Get("agent")
	deliverySentence := "Native message delivery is not enabled. "
	if value.Truth(config.Get("native_delivery")) {
		deliverySentence = "Inbox delivery uses supported turn boundaries. "
	}
	message := fmt.Sprintf(
		"Cairn session %s has inbox %s. Use agent UUID %s and execution UUID %s with the existing profile token at %s for session inbox work. Use cairn agents context to maintain a concise selected task description; harness/model/project are metadata. Ordinary memory keeps its existing profile. Presence is maintained by the host watcher. %s",
		nativeText(agent.Get("display_name")),
		nativeText(agent.Get("inbox")),
		nativeText(agent.Get("agent_id")),
		nativeText(agent.Get("execution_id")),
		nativeText(config.Get("token_file")),
		deliverySentence,
	)
	if value.Truth(wakeVal) {
		message += "This conversation is linked to the current fresh-worker attempt. Complete its original slot delivery using CAIRN_WAKE_CONTEXT; do not claim a second inbox. "
	}
	if inbox != "" {
		message += "\n" + inbox
	}
	if value.Truth(config.Get("inbox_recall_binding")) && inboxRecallWakeValue(event) {
		var err error
		message, err = c.invokeInboxRecallBridge(ctx, ns, config, event, observation, message, bridgeDeadline, reports)
		if err != nil {
			return nil, err
		}
	}
	if value.SameText(config.Get("harness"), "agy") {
		return nativeMap("injectSteps", nativeList(nativeMap("ephemeralMessage", message))), nil
	}
	output := nativeMap("hookSpecificOutput", nativeMap("hookEventName", observation.Get("event"), "additionalContext", message))
	if value.SameText(config.Get("harness"), "opencode") && value.SameText(observation.Get("event"), "TurnStart") && value.Truth(config.Get("opencode_cancel_enabled")) && value.Truth(state.Get("inbox_attempt").Get("turn_exclusive")) && nativeEqual(state.Get("inbox_attempt").Get("native_turn_id"), observation.Get("native_turn_id")) {
		output.Set("cairn", nativeMap("tool_capture", true))
	}
	return output, nil
}

func (c *CLI) invokeInboxRecallBridge(ctx context.Context, ns *NativeSession, config, event, observation *value.Value, message string, bridgeDeadline float64, reports *[]InboxRecallReport) (string, error) {
	state := ns.Record
	revalidate := func(attempt *value.Value) error {
		rem := bridgeDeadline - c.monotonicSeconds()
		if rem > 2 {
			rem = 2
		}
		if rem <= 0 || value.Truth(attempt.Get("lease_lapsed")) {
			return nativeRefusal("STALE_LEASE", "inbox recall cannot confirm live ownership")
		}
		delivery := attempt.Get("delivery")
		if delivery == nil || delivery.Kind != value.Object {
			return &journal.NonObjectError{Field: "delivery"}
		}
		renewed, err := c.call(ctx, config, "event-renew", nativeMap("delivery_id", delivery.Get("delivery_id"), "lease_id", delivery.Get("lease_id"), "lease_seconds", 90), NativeCallOptions{
			Session: attempt.Get("session"),
			Timeout: time.Duration(rem * float64(time.Second)),
		})
		if err != nil {
			return err
		}
		if renewed == nil || renewed.Kind != value.Object || !nativeEqual(renewed.Get("delivery_id"), delivery.Get("delivery_id")) || !nativeEqual(renewed.Get("lease_id"), delivery.Get("lease_id")) || !value.SameText(renewed.Get("state"), "leased") || !nativeEqual(renewed.Get("event"), delivery.Get("event")) {
			return nativeRefusal("STALE_LEASE", "inbox recall delivery changed")
		}
		return nil
	}
	refusalStage := "loader"
	var err error
	if c.Host != nil && c.Host.LoadInboxRecall != nil {
		err = c.Host.LoadInboxRecall(ctx, config)
	} else if c.Compatibility != nil {
		_, err = c.Compatibility.Validate(c.profile(), config, false)
	} else {
		err = c.validateManifestInboxRecallBinding(config)
	}
	if err == nil {
		refusalStage = "bridge"
		if c.Host == nil || c.Host.InboxRecallOutput == nil {
			err = &value.ProfileUnavailable{}
		} else {
			message, err = c.Host.InboxRecallOutput(ctx, InboxRecallRequest{
				Config:        config,
				Event:         event,
				Observation:   observation,
				State:         state,
				Message:       message,
				Revalidate:    revalidate,
				OuterDeadline: bridgeDeadline,
				Reports:       reports,
			})
		}
	}
	if err != nil {
		if !hookActivationCaught(err) && !isCoordErr(err) {
			return "", err
		}
		err = annotateInboxRefusal(err, refusalStage)
		if value.Truth(state.Get("inbox_intent")) {
			_, _ = c.engine().SettleRefusedClaudeWakeWithError(ctx, ns, nativeText(observation.Get("native_turn_id")), err, "live_hook", true, nil)
		}
		return "", nativePromptRefusedWithCause("bound inbox recall unavailable; no source or memory grant delivered", err)
	}
	return message, nil
}

func (c *CLI) validateManifestInboxRecallBinding(config *value.Value) error {
	_, err := c.validateManifestMemoryConfig(config)
	return err
}

func (c *CLI) validateManifestMemoryConfig(config *value.Value) (*value.Value, error) {
	path, err := c.pathText(config.Get("inbox_recall_binding"))
	if err != nil {
		return nil, err
	}
	if !strings.HasPrefix(path, "/") {
		return nil, &InboxRecallError{Class: "ValueError", Message: "inbox recall binding must be owner-only", RefusalStage: "binding", RefusalReason: "manifest_owner"}
	}
	st, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	if !statMatchesUID(st, c.uid()) || st.Mode().Perm()&0o077 != 0 {
		return nil, &InboxRecallError{Class: "ValueError", Message: "inbox recall binding must be owner-only", RefusalStage: "binding", RefusalReason: "manifest_owner"}
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	manifest, err := decodeCompatibilityJSON(c.profile(), SiteCompatibilityManifest, raw)
	if err != nil {
		return nil, err
	}
	if manifest == nil || manifest.Kind != value.Object || (!value.SameText(manifest.Get("schema"), "cairn.inbox-recall-binding/2") && !value.SameText(manifest.Get("schema"), "cairn.inbox-recall-binding/3")) {
		return nil, &InboxRecallError{Class: "ValueError", Message: "unsupported inbox recall binding; restage its complete lifecycle bundle", RefusalStage: "binding", RefusalReason: "schema"}
	}
	if manifest.Get("enabled") == nil || manifest.Get("enabled").Kind != value.Bool {
		return nil, &InboxRecallError{Class: "ValueError", Message: "unknown inbox recall binding", RefusalStage: "binding", RefusalReason: "schema"}
	}
	_, memRaw, err := readCompatibilityCheckedFile(c.profile(), manifest.Get("memory_config"), c.uid())
	if err != nil {
		return nil, annotateBindingRefusalErr(err)
	}
	memory, err := decodeCompatibilityJSON(c.profile(), SiteCompatibilityConfig, memRaw)
	if err != nil {
		return nil, err
	}
	descriptors := manifest.Get("coordination_configs")
	if descriptors == nil || descriptors.Kind != value.Array || len(descriptors.Items) < 1 || len(descriptors.Items) > 8 {
		return nil, &InboxRecallError{Class: "ValueError", Message: "one to eight explicit coordination configurations required", RefusalStage: "binding", RefusalReason: "coordination_count"}
	}
	coordinators := make([]*value.Value, 0, len(descriptors.Items))
	for _, item := range descriptors.Items {
		_, cRaw, err := readCompatibilityCheckedFile(c.profile(), item, c.uid())
		if err != nil {
			return nil, annotateBindingRefusalErr(err)
		}
		coord, err := decodeCompatibilityJSON(c.profile(), SiteCompatibilityConfig, cRaw)
		if err != nil {
			return nil, err
		}
		coordinators = append(coordinators, coord)
	}
	if err := validateConfigurationProfiles(c.profile(), config, path, memory, coordinators); err != nil {
		return nil, annotateBindingRefusalErr(err)
	}
	return memory, nil
}

func readCompatibilityCheckedFile(prof *value.SourceReaderProfile, desc *value.Value, uid uint32) (string, []byte, error) {
	if desc == nil || desc.Kind != value.Object {
		return "", nil, &journal.TypeError{Field: "descriptor"}
	}
	dPathVal, err := nativeRequired(desc, "path")
	if err != nil {
		return "", nil, err
	}
	dSHAVal, err := nativeRequired(desc, "sha256")
	if err != nil {
		return "", nil, err
	}
	dPath, err := sourcePathText(prof, dPathVal)
	if err != nil {
		return "", nil, err
	}
	raw, err := controlledData(dPath, false, uid)
	if err != nil {
		return "", nil, &InboxRecallError{Class: "ValueError", Message: "inbox recall requires owner-controlled absolute paths", RefusalStage: "binding", RefusalReason: "config_owner", Cause: err}
	}
	sum := sha256.Sum256(raw)
	if !value.SameText(dSHAVal, hex.EncodeToString(sum[:])) {
		return "", nil, &InboxRecallError{Class: "ValueError", Message: "inbox recall binding changed", RefusalStage: "binding", RefusalReason: "config_hash"}
	}
	return dPath, raw, nil
}

func validateConfigurationProfiles(prof *value.SourceReaderProfile, config *value.Value, path string, memory *value.Value, coordinators []*value.Value) error {
	err := binding.ValidateConfigurationProfiles(binding.ConfigurationProfiles{
		Current:      config,
		Memory:       memory,
		Coordinators: coordinators,
		ManifestPath: []rune(path),
		Resolve: func(v *value.Value) ([]rune, error) {
			return resolveSourceValue(prof, v)
		},
	})
	if err != nil {
		var ve *value.Error
		if errors.As(err, &ve) && ve != nil && ve.Class == "ValueError" {
			reason := "profile"
			if strings.Contains(ve.Message, "configuration homes") {
				reason = "config_homes"
			}
			return &InboxRecallError{Class: "ValueError", Message: ve.Message, RefusalStage: "binding", RefusalReason: reason, Cause: err}
		}
		return err
	}
	return nil
}

func annotateBindingRefusalErr(err error) error {
	if err == nil {
		return nil
	}
	var ce *CoordinationError
	if errors.As(err, &ce) && ce != nil && ce.RefusalStage == "binding" {
		return &InboxRecallError{Class: "ValueError", Message: ce.Message, RefusalStage: ce.RefusalStage, RefusalReason: ce.RefusalReason, Cause: err}
	}
	return err
}
