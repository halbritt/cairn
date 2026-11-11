package coordination

import (
	"context"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"

	"github.com/google/uuid"
	"golang.org/x/sys/unix"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/lifecycle/wake"
)

var providerCodePattern = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,128}$`)

func (c *CLI) registration(config, observation, metadata *value.Value) *value.Value {
	meta := metadata
	if !value.Truth(meta) {
		stateVal := observation.Get("phase")
		if value.SameText(stateVal, "start") {
			stateVal = value.Text("idle")
		}
		delivMode := "existing-session"
		if value.Truth(config.Get("_wake")) {
			delivMode = "fresh-worker"
		}
		modelVal := config.Get("model")
		if modelVal == nil {
			modelVal = value.Text("")
		}
		meta = nativeMap(
			"harness", config.Get("harness"),
			"model", modelVal,
			"observed_model", observation.Get("observed_model"),
			"project", observation.Get("project"),
			"workspace", observation.Get("workspace"),
			"state", stateVal,
			"delivery_mode", delivMode,
		)
	}
	req := nativeMap(
		"request_id", c.newUUID(),
		"repo", config.Get("repo"),
		"binding", config.Get("binding"),
		"native_session_id", observation.Get("native_id"),
		"metadata", meta,
	)
	if launchID := os.Getenv("CAIRN_CODEX_OWNER_ORIGIN_LAUNCH_ID"); launchID != "" && value.SameText(config.Get("harness"), "codex") {
		req.Set("launch_id", value.Text(launchID))
	}
	return req
}

func (c *CLI) wakeContext(config *value.Value) (*value.Value, error) {
	path := os.Getenv("CAIRN_WAKE_CONTEXT")
	if path == "" {
		return nil, nil
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	raw, readErr := io.ReadAll(io.LimitReader(f, 32769))
	_ = f.Close()
	if readErr != nil {
		return nil, readErr
	}
	if len(raw) > 32768 {
		return nil, nativeRefusal("INVALID_HOST", "wake context is oversized")
	}
	wakeObj, err := decodeCompatibilityJSON(c.profile(), SiteStateRead, raw)
	if err != nil {
		return nil, err
	}
	if wakeObj == nil || wakeObj.Kind != value.Object {
		return nil, nativeRefusal("INVALID_HOST", "wake context must be a JSON object")
	}
	if !value.SameText(wakeObj.Get("schema"), "cairn.wake-context/1") || !value.Truth(wakeObj.Get("native_registration")) {
		return nil, nil
	}
	if wakeObj.Get("native_registration").Kind != value.Bool || !wakeObj.Get("native_registration").Boolean {
		return nil, nativeRefusal("INVALID_HOST", "wake association needs typed identifiers and paths")
	}
	for _, key := range []string{"attempt_id", "collection", "socket", "token_file"} {
		v := wakeObj.Get(key)
		if v == nil || v.Kind != value.String {
			return nil, nativeRefusal("INVALID_HOST", "wake association needs typed identifiers and paths")
		}
	}
	if _, err := uuid.Parse(nativeText(wakeObj.Get("attempt_id"))); err != nil {
		return nil, &value.Error{Class: "ValueError", Message: err.Error()}
	}
	if !nativeEqual(wakeObj.Get("collection"), config.Get("repo")) || !nativeEqual(wakeObj.Get("socket"), config.Get("socket")) {
		return nil, nativeRefusal("INVALID_HOST", "wake context and native profile must share API and collection")
	}
	tfPath, err := sourcePathText(c.profile(), wakeObj.Get("token_file"))
	if err != nil {
		return nil, err
	}
	if !filepath.IsAbs(tfPath) {
		return nil, nativeRefusal("INVALID_HOST", "wake profile path must be absolute")
	}
	out := nativeClone(wakeObj)
	out.Set("path", value.Text(path))
	return out, nil
}

func (c *CLI) associateWake(ctx context.Context, config, state *value.Value, statePath string, observation *value.Value) error {
	wakeObj := config.Get("_wake")
	if !value.Truth(wakeObj) || wakeObj.Kind != value.Object {
		return nil
	}
	agent, err := nativeRequired(state, "agent")
	if err != nil {
		return err
	}
	ref, err := c.sessionRef(agent)
	if err != nil {
		return err
	}
	request := state.Get("wake_link")
	if !value.Truth(request) || request.Kind != value.Object || !nativeEqual(request.Get("attempt_id"), wakeObj.Get("attempt_id")) || !nativeEqual(request.Get("session"), ref) {
		request = nativeMap("request_id", c.newUUID(), "attempt_id", wakeObj.Get("attempt_id"), "operation", "session", "session", ref)
		state.Set("wake_link", request)
		if err := c.writeState(statePath, state); err != nil {
			return err
		}
	}
	callCfg := nativeClone(config)
	callCfg.Set("token_file", wakeObj.Get("token_file"))
	if _, err := c.call(ctx, callCfg, "wake-change", request, NativeCallOptions{}); err != nil {
		return err
	}
	ctxOut := nativeClone(wakeObj)
	nativePop(ctxOut, "path")
	ctxOut.Set("session", ref)
	ctxOut.Set("native_session_id", observation.Get("native_id"))
	ctxOut.Set("session_inbox", agent.Get("inbox"))
	wakePath, err := sourcePathText(c.profile(), wakeObj.Get("path"))
	if err != nil {
		return err
	}
	return c.writeState(wakePath, ctxOut)
}

func (c *CLI) observeProvider(config, wakeObj, event *value.Value) error {
	if !value.Truth(wakeObj) || wakeObj.Kind != value.Object || !value.Truth(wakeObj.Get("provider_observation_file")) {
		return nil
	}
	target, err := sourcePathText(c.profile(), wakeObj.Get("provider_observation_file"))
	if err != nil {
		return err
	}
	wakePath, err := sourcePathText(c.profile(), wakeObj.Get("path"))
	if err != nil {
		return err
	}
	expected := sourceJoin(filepath.Dir(wakePath), nativeText(wakeObj.Get("attempt_id"))+".provider.json")
	if purePosixPath(target) != purePosixPath(expected) || !nativeEqual(wakeObj.Get("provider_harness"), config.Get("harness")) {
		return nativeRefusal("INVALID_HOST", "provider observation differs from wake binding")
	}
	failure := event.Get("provider_failure")
	if !nativeNone(failure) {
		if !exactReceiptObject(failure, "harness", "source", "kind", "code", "status") ||
			!nativeEqual(failure.Get("harness"), config.Get("harness")) ||
			!value.SameText(failure.Get("source"), "native-hook") ||
			!nativeContains([]string{"quota", "rate_limit", "billing"}, nativeText(failure.Get("kind"))) ||
			failure.Get("code") == nil || failure.Get("code").Kind != value.String ||
			!providerCodePattern.MatchString(nativeText(failure.Get("code"))) ||
			failure.Get("status") == nil || failure.Get("status").Kind != value.Int {
			return nativeRefusal("INVALID_HOST", "invalid selected provider failure")
		}
		stNum, parseErr := strconv.ParseInt(failure.Get("status").Integer, 10, 64)
		if parseErr != nil || (stNum != 0 && (stNum < 100 || stNum > 599)) {
			return nativeRefusal("INVALID_HOST", "invalid selected provider failure")
		}
	}
	if err := c.writeState(target, nativeMap("schema", "cairn.provider-observation/1", "attempt_id", wakeObj.Get("attempt_id"), "harness", config.Get("harness"), "failure", failure)); err != nil {
		return err
	}
	fd, err := syscall.Open(filepath.Dir(target), syscall.O_RDONLY|syscall.O_DIRECTORY, 0)
	if err != nil {
		return err
	}
	defer func() { _ = syscall.Close(fd) }()
	return syscall.Fsync(fd)
}

func (c *CLI) opencodeCancelledTurnEnd(ctx context.Context, config, state *value.Value, statePath string, observation, attempt *value.Value) (bool, error) {
	if !value.SameText(observation.Get("event"), "TurnEnd") || !nativeEqual(observation.Get("native_turn_id"), attempt.Get("native_turn_id")) || !value.Truth(attempt.Get("native_turn_id")) {
		return false, nil
	}
	if nativeContains([]string{"ended", "interrupted"}, nativeText(attempt.Get("turn_stop_state"))) {
		return true, nil
	}
	request := state.Get("cancel_turn_end")
	if !value.Truth(request) || request.Kind != value.Object || !nativeEqual(request.Get("attempt_id"), attempt.Get("attempt_id")) {
		request = nativeMap("request_id", c.newUUID(), "session", attempt.Get("session"), "attempt_id", attempt.Get("attempt_id"), "turn_stop", "ended", "tools", nativeList())
		state.Set("cancel_turn_end", request)
		if err := c.writeState(statePath, state); err != nil {
			return false, err
		}
	}
	updated, err := c.call(ctx, config, "session-tool-stop", request, NativeCallOptions{})
	if err != nil {
		return false, err
	}
	state.Set("inbox_attempt", updated)
	nativePop(state, "cancel_turn_end")
	return true, c.writeState(statePath, state)
}

func validOpenCodeField(v *value.Value, maxBytes int) (string, bool) {
	if v == nil || v.Kind != value.String {
		return "", false
	}
	s := string(v.String)
	if strings.TrimSpace(s) == "" || len(s) > maxBytes {
		return "", false
	}
	for _, r := range v.String {
		if r < 32 {
			return "", false
		}
	}
	return s, true
}

func (c *CLI) opencodeToolEvent(ctx context.Context, config, state *value.Value, statePath string, observation, event *value.Value) (*value.Value, error) {
	if !value.Truth(config.Get("opencode_cancel_enabled")) {
		return nil, nativeRefusal("UNSUPPORTED_CONTROL", "OpenCode tool capture is not enabled")
	}
	callID, ok1 := validOpenCodeField(event.Get("call_id"), 128)
	tool, ok2 := validOpenCodeField(event.Get("tool"), 1024)
	reqID, ok3 := validOpenCodeField(event.Get("request_id"), 256)
	if !ok1 || !ok2 || !ok3 {
		return nil, nativeRefusal("INVALID_HOST", "invalid OpenCode tool identity")
	}
	if tool != "bash" {
		return nil, nativeRefusal("UNSUPPORTED_CONTROL", "only marked bash tools can run in a cancellable turn")
	}
	attempt, err := c.opencodeInboxControl(ctx, &NativeSession{Config: config, Record: state, Path: statePath, Host: c.Host}, config, state)
	if err != nil {
		return nil, err
	}
	if !value.Truth(attempt) || !value.Truth(attempt.Get("turn_exclusive")) || !nativeEqual(attempt.Get("native_turn_id"), observation.Get("native_turn_id")) || !value.SameText(attempt.Get("delivery").Get("delivery_id"), reqID) {
		return nil, nativeRefusal("NATIVE_TURN_MISMATCH", "tool does not belong to this exclusive request")
	}
	calls := state.Get("tool_calls")
	if calls == nil || calls.Kind != value.Object {
		calls = value.Mapping()
		state.Set("tool_calls", calls)
	}
	if value.SameText(observation.Get("event"), "ToolEnd") {
		rec := calls.Get(callID)
		if rec == nil || rec.Kind != value.Object {
			return nil, nativeRefusal("INVALID_HOST", "tool end has no captured start")
		}
		rec.Set("ended", value.Boolean(true))
		return value.Mapping(), c.writeState(statePath, state)
	}
	if value.Truth(attempt.Get("cancel")) {
		return nil, nativeRefusal("REQUEST_CANCELLED", "a cancelled request cannot start another tool")
	}
	rec := calls.Get(callID)
	if value.Truth(rec) && !nativeEqual(rec.Get("attempt_id"), attempt.Get("attempt_id")) {
		return nil, nativeRefusal("INVALID_HOST", "tool call ID belongs to another attempt")
	}
	if !value.Truth(rec) {
		var ts unix.Timespec
		_ = unix.ClockGettime(unix.CLOCK_BOOTTIME, &ts)
		since := ts.Sec*100 + ts.Nsec/10_000_000
		capture := nativeMap("request_id", c.newUUID(), "session", attempt.Get("session"), "attempt_id", attempt.Get("attempt_id"), "items", nativeList(nativeMap("item_id", callID, "process_id", "opencode-call:"+callID, "command", tool, "native_turn_id", attempt.Get("native_turn_id"))))
		rec = nativeMap("attempt_id", attempt.Get("attempt_id"), "since", since, "capture", capture)
		calls.Set(callID, rec)
		if err := c.writeState(statePath, state); err != nil {
			return nil, err
		}
	}
	updated, err := c.call(ctx, config, "session-tool-capture", rec.Get("capture"), NativeCallOptions{})
	if err != nil {
		return nil, err
	}
	state.Set("inbox_attempt", updated)
	return value.Mapping(), c.writeState(statePath, state)
}

const SeenPromptLimit = 256

func (c *CLI) claudePromptAdmission(state, observation, event *value.Value) (*value.Value, error) {
	key := nativeText(observation.Get("native_turn_id"))
	name := nativeText(observation.Get("event"))
	if name == "UserPromptSubmit" && key != "" {
		active := state.Get("active_prompt")
		if active != nil && active.Kind != value.Object {
			active = nil
		}
		var seen []string
		if seenVal := state.Get("seen_prompts"); seenVal != nil && seenVal.Kind == value.Array {
			for _, it := range seenVal.Items {
				if it != nil && it.Kind == value.String {
					seen = append(seen, string(it.String))
				}
			}
		}
		inSeen := nativeContains(seen, key)
		reused := !(active != nil && value.SameText(active.Get("id"), key)) && inSeen
		if reused {
			active = nativeMap("id", key)
		}
		if !inSeen {
			seen = append(seen, key)
			if len(seen) > SeenPromptLimit {
				seen = seen[len(seen)-SeenPromptLimit:]
			}
			items := make([]*value.Value, len(seen))
			for i, s := range seen {
				items[i] = value.Text(s)
			}
			state.Set("seen_prompts", &value.Value{Kind: value.Array, Items: items})
		}
		if active != nil && value.SameText(active.Get("id"), key) {
			wakeObj, _ := nativeObj(state.Get("idle_wake"), "idle_wake")
			agent, err := nativeRequired(state, "agent")
			if err != nil {
				return nil, err
			}
			ref, err := c.sessionRef(agent)
			if err != nil {
				return nil, err
			}
			var refusal *value.Value
			promptStr := nativeText(event.Get("prompt"))
			if value.SameText(wakeObj.Get("transport"), "claude-channel") &&
				nativeEqual(wakeObj.Get("session"), ref) &&
				event.Get("prompt") != nil && event.Get("prompt").Kind == value.String &&
				strings.HasPrefix(promptStr, "<channel") {
				msgs, err := wakeMessagesValue(wakeObj)
				if err != nil {
					return nil, err
				}
				for _, m := range msgs {
					if strings.Contains(promptStr, m) {
						nativePop(state, "idle_wake")
						refusal = wakeObj.Get("delivery_id")
						break
					}
				}
			}
			shared := nativeClone(active)
			shared.Set("joined", value.Boolean(true))
			if refusal != nil {
				shared.Set("refused_delivery", refusal)
				if reused {
					now := c.clock()
					nowFloat := float64(now.Unix()) + float64(now.Nanosecond())/1e9
					state.Set("wake_preclaim_refusal", nativeMap("phase", "before_claim", "reason", "seen_prompt", "delivery_id", refusal, "native_turn_id", key, "at", nowFloat))
				}
			}
			state.Set("active_prompt", shared)
			return nativeMap("joined", true, "refused_delivery", refusal), nil
		}
		state.Set("active_prompt", nativeMap("id", key))
		return nativeMap("joined", false, "refused_delivery", nil), nil
	}
	if name == "Stop" && key != "" {
		active := state.Get("active_prompt")
		if active != nil && active.Kind == value.Object && value.SameText(active.Get("id"), key) {
			nativePop(state, "active_prompt")
		}
	}
	return nil, nil
}

func wakeMessagesValue(wakeObj *value.Value) ([]string, error) {
	if wakeObj == nil || wakeObj.Kind != value.Object {
		return nil, &journal.NonObjectError{Field: "idle_wake"}
	}
	sess, err := nativeRequired(wakeObj, "session")
	if err != nil {
		return nil, err
	}
	agentID, err := nativeRequired(sess, "agent_id")
	if err != nil {
		return nil, err
	}
	execID, err := nativeRequired(sess, "execution_id")
	if err != nil {
		return nil, err
	}
	delivID, err := nativeRequired(wakeObj, "delivery_id")
	if err != nil {
		return nil, err
	}
	pair := wake.WakeMessages(wake.SessionRef{AgentID: nativeText(agentID), ExecutionID: nativeText(execID)}, nativeText(delivID))
	return []string{pair[0], pair[1]}, nil
}

func promptEventFromValue(event *value.Value) wake.PromptEvent {
	if event == nil || event.Kind != value.Object {
		return wake.PromptEvent{}
	}
	var sessionID *string
	if sv := event.Get("session_id"); !nativeNone(sv) {
		if sv.Kind == value.String {
			s := string(sv.String)
			sessionID = &s
		} else {
			empty := ""
			sessionID = &empty
		}
	}
	return wake.PromptEvent{
		HookEventName: nativeText(event.Get("hook_event_name")),
		Prompt:        nativeText(event.Get("prompt")),
		TurnID:        nativeText(event.Get("turn_id")),
		PromptID:      nativeText(event.Get("prompt_id")),
		SessionID:     sessionID,
		RequestID:     nativeText(event.Get("request_id")),
		DeliveryID:    nativeText(event.Get("delivery_id")),
	}
}

func inboxRecallWakeValue(event *value.Value) bool {
	if event == nil || event.Kind != value.Object || event.Get("prompt") == nil || event.Get("prompt").Kind != value.String {
		return false
	}
	return wake.InboxRecallWake(promptEventFromValue(event))
}

func wakeEnvelopeFromValue(wakeObj *value.Value) wake.WakeEnvelope {
	if wakeObj == nil || wakeObj.Kind != value.Object {
		return wake.WakeEnvelope{}
	}
	var sess wake.SessionRef
	if s := wakeObj.Get("session"); s != nil && s.Kind == value.Object {
		sess = wake.SessionRef{
			AgentID:     nativeText(s.Get("agent_id")),
			ExecutionID: nativeText(s.Get("execution_id")),
		}
	}
	return wake.WakeEnvelope{
		Session:            sess,
		DeliveryID:         nativeText(wakeObj.Get("delivery_id")),
		Transport:          nativeText(wakeObj.Get("transport")),
		Endpoint:           nativeText(wakeObj.Get("endpoint")),
		Owner:              nativeText(wakeObj.Get("owner")),
		NativeID:           nativeText(wakeObj.Get("native_id")),
		QueuedSubmissionID: nativeText(wakeObj.Get("queued_submission_id")),
		Status:             nativeText(wakeObj.Get("status")),
		RequestID:          nativeText(wakeObj.Get("request_id")),
		CancelCapable:      value.Truth(wakeObj.Get("cancel_capable")),
	}
}

func (c *CLI) wakeBinding(config, state, event *value.Value, joined bool) (*value.Value, error) {
	if state == nil || state.Kind != value.Object || event == nil || event.Kind != value.Object {
		return value.Mapping(), nil
	}
	agent, err := nativeRequired(state, "agent")
	if err != nil {
		return nil, err
	}
	ref, err := c.sessionRef(agent)
	if err != nil {
		return nil, err
	}
	sessRef := wake.SessionRef{AgentID: nativeText(ref.Get("agent_id")), ExecutionID: nativeText(ref.Get("execution_id"))}
	idleWake := wakeEnvelopeFromValue(state.Get("idle_wake"))
	ev := promptEventFromValue(event)
	harness := nativeText(config.Get("harness"))
	switch harness {
	case "codex":
		if ev.Prompt != "" && event.Get("prompt").Kind == value.String && event.Get("turn_id") != nil && event.Get("turn_id").Kind == value.String {
			if b, ok := wake.BindCodexQueuedWake(sessRef, idleWake, ev); ok {
				return nativeMap("delivery_id", b.DeliveryID, "native_turn_id", b.NativeTurnID), nil
			}
		}
	case "claude":
		if event.Get("prompt") != nil && event.Get("prompt").Kind == value.String && event.Get("prompt_id") != nil && event.Get("prompt_id").Kind == value.String {
			if b, ok := wake.BindClaudeChannelWake(sessRef, idleWake, ev, joined); ok {
				return nativeMap("delivery_id", b.DeliveryID, "native_turn_id", b.NativeTurnID), nil
			}
		}
	case "hermes":
		if event.Get("prompt") != nil && event.Get("prompt").Kind == value.String && event.Get("turn_id") != nil && event.Get("turn_id").Kind == value.String {
			if !nativeNone(event.Get("session_id")) && event.Get("session_id").Kind != value.String {
				return value.Mapping(), nil
			}
			if b, ok := wake.BindHermesQueuedWake(sessRef, idleWake, ev); ok {
				return nativeMap("delivery_id", b.DeliveryID, "native_turn_id", b.NativeTurnID), nil
			}
		}
	case "opencode":
		if event.Get("turn_id") != nil && event.Get("turn_id").Kind == value.String {
			if b, ok := wake.BindOpenCodeQueuedWake(sessRef, idleWake, ev); ok {
				return nativeMap("delivery_id", b.DeliveryID, "native_turn_id", b.NativeTurnID, "turn_exclusive", b.TurnExclusive), nil
			}
		}
	}
	return value.Mapping(), nil
}

func (c *CLI) refuseConflictingClaudeWake(ctx context.Context, config, state *value.Value, statePath string, binding *value.Value) error {
	if !value.Truth(config.Get("inbox_recall_binding")) || value.Truth(state.Get("inbox_intent")) || value.Truth(state.Get("inbox_attempt")) {
		return nil
	}
	owner := binding.Get("native_turn_id")
	status, kind, err := c.grants().Evidence(ctx, config, state, nil, owner, false)
	if err != nil {
		return err
	}
	if status == "absent" || status == "busy" {
		return nil
	}
	nativePop(state, "idle_wake")
	active := state.Get("active_prompt")
	if !value.Truth(active) || active.Kind != value.Object {
		active = nativeMap("id", owner)
	} else {
		active = nativeClone(active)
	}
	active.Set("joined", value.Boolean(true))
	active.Set("refused_delivery", binding.Get("delivery_id"))
	state.Set("active_prompt", active)
	grantKind := kind
	if !value.Truth(grantKind) {
		grantKind = value.Text("unknown")
	}
	now := c.clock()
	nowFloat := float64(now.Unix()) + float64(now.Nanosecond())/1e9
	state.Set("wake_preclaim_refusal", nativeMap(
		"phase", "before_claim",
		"reason", "ledger_grant",
		"grant", status,
		"grant_kind", grantKind,
		"delivery_id", binding.Get("delivery_id"),
		"native_turn_id", owner,
		"at", nowFloat,
	))
	if err := c.writeState(statePath, state); err != nil {
		return err
	}
	return nativePromptRefused("channel wake prompt already holds a recall grant; nothing claimed, a fresh copy retries when idle")
}

func (c *CLI) claudeChannelEnabled(config, process *value.Value) bool {
	if process == nil || process.Kind != value.Object {
		return false
	}
	pidVal := process.Get("pid")
	if pidVal == nil || pidVal.Kind != value.Int {
		return false
	}
	pid, err := strconv.Atoi(pidVal.Integer)
	if err != nil || pid <= 0 {
		return false
	}
	procRoot := "/proc"
	if c.ProcRoot != "" {
		procRoot = c.ProcRoot
	}
	raw, err := os.ReadFile(filepath.Join(procRoot, strconv.Itoa(pid), "cmdline"))
	if err != nil {
		return false
	}
	alive, err := c.processAlive(process)
	if err != nil || !alive {
		return false
	}
	server := nativeText(config.Get("claude_channel_server"))
	if server == "" {
		server = "cairn-events"
	}
	target := "server:" + server
	parts := strings.Split(string(raw), "\x00")
	for i := 0; i < len(parts); i++ {
		arg := parts[i]
		for _, flag := range []string{"--channels", "--dangerously-load-development-channels"} {
			var list string
			if arg == flag && i+1 < len(parts) {
				i++
				list = parts[i]
			} else if strings.HasPrefix(arg, flag+"=") {
				list = arg[len(flag)+1:]
			} else {
				continue
			}
			for _, item := range strings.Split(list, ",") {
				if strings.TrimSpace(item) == target {
					return true
				}
			}
		}
	}
	return false
}

func (c *CLI) claudeSessionConfig(config, nativeID, process *value.Value) (*value.Value, error) {
	if !value.SameText(config.Get("harness"), "claude") || !value.Truth(config.Get("claude_channel_dir")) {
		return config, nil
	}
	if sessions := config.Get("claude_channel_sessions"); sessions != nil {
		found := false
		if sessions.Kind == value.Array {
			for _, it := range sessions.Items {
				if nativeEqual(it, nativeID) {
					found = true
					break
				}
			}
		}
		if !found {
			out := nativeClone(config)
			nativePop(out, "claude_channel_dir")
			return out, nil
		}
	}
	if !c.claudeChannelEnabled(config, process) {
		out := nativeClone(config)
		nativePop(out, "claude_channel_dir")
		return out, nil
	}
	return config, nil
}

func (c *CLI) claudeDaemonManaged(process *value.Value) (bool, error) {
	if process == nil || process.Kind != value.Object {
		return false, nil
	}
	parentVal := process.Get("parent")
	if parentVal == nil || parentVal.Kind != value.Int {
		return false, nil
	}
	parentPID, err := strconv.Atoi(parentVal.Integer)
	if err != nil || parentPID < 2 {
		return false, nil
	}
	procRoot := "/proc"
	if c.ProcRoot != "" {
		procRoot = c.ProcRoot
	}
	view, err := ReadProcViewAt(procRoot, parentPID)
	if err != nil {
		return false, nil
	}
	return ClaudeSupervisorRole(view) == "bg-pty-host", nil
}
