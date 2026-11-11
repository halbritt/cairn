package coordination

import (
	"context"
	"fmt"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/lifecycle/wake"
)

func (c *CLI) attemptCommandsAbsent(config, attempt *value.Value) (bool, error) {
	if c.Host != nil {
		if ja, ok := c.Host.Journal.(interface {
			AttemptCommandsAbsent(*value.Value, *value.Value) (bool, error)
		}); ok && ja != nil {
			return ja.AttemptCommandsAbsent(config, attempt)
		}
	}
	ja := &SessionJournalAdapter{PathText: func(v *value.Value) (string, error) { return c.pathText(v) }}
	return ja.AttemptCommandsAbsent(config, attempt)
}

func (c *CLI) journalCompletionState(config, attempt *value.Value) (string, error) {
	if c.Host != nil {
		if ja, ok := c.Host.Journal.(interface {
			JournalCompletionState(*value.Value, *value.Value) (string, error)
		}); ok && ja != nil {
			return ja.JournalCompletionState(config, attempt)
		}
	}
	ja := &SessionJournalAdapter{PathText: func(v *value.Value) (string, error) { return c.pathText(v) }}
	return ja.JournalCompletionState(config, attempt)
}

func (c *CLI) shlexJoinValue(v *value.Value) (string, error) {
	if v == nil || v.Kind != value.Array {
		return "", &journal.TypeError{Field: "argv"}
	}
	argv := make([]string, 0, len(v.Items))
	for _, item := range v.Items {
		s, err := c.pathText(item)
		if err != nil {
			return "", err
		}
		argv = append(argv, s)
	}
	return shlexJoinStrings(argv)
}

func wakeProcessRef(v *value.Value) wake.ProcessRef {
	if v == nil || v.Kind != value.Object {
		return wake.ProcessRef{}
	}
	var ref wake.ProcessRef
	if pid := v.Get("pid"); pid != nil && pid.Kind == value.Int {
		if n, err := strconv.Atoi(pid.Integer); err == nil {
			ref.PID = n
		}
	}
	if start := v.Get("start"); start != nil && start.Kind == value.Int {
		if n, err := strconv.ParseUint(start.Integer, 10, 64); err == nil {
			ref.Start = n
		}
	}
	if boot := v.Get("boot"); boot != nil && boot.Kind == value.String {
		ref.Boot = string(boot.String)
	}
	return ref
}

func (c *CLI) unacknowledgedTurnEndReminder(ctx context.Context, ns *NativeSession, config, state *value.Value, statePath string, observation *value.Value) (string, error) {
	if !value.Truth(config.Get("remind_unacknowledged_turn_end")) {
		return "", nil
	}
	if value.Truth(state.Get("inbox_turn_ended")) || value.Truth(state.Get("inbox_close")) || value.Truth(state.Get("ending")) || value.Truth(state.Get("retired")) {
		return "", nil
	}
	intent, attempt := state.Get("inbox_intent"), state.Get("inbox_attempt")
	if intent == nil || intent.Kind != value.Object || attempt == nil || attempt.Kind != value.Object {
		return "", nil
	}
	attemptID := attempt.Get("attempt_id")
	if !value.Truth(attemptID) || !nativeEqual(intent.Get("request_id"), attemptID) {
		return "", nil
	}
	agent := state.Get("agent")
	if agent == nil || agent.Kind != value.Object {
		return "", nil
	}
	ref, err := c.sessionRef(agent)
	if err != nil || !nativeEqual(attempt.Get("session"), ref) {
		return "", nil
	}
	if value.Truth(attempt.Get("finished_at")) || value.Truth(attempt.Get("cancel")) || value.Truth(attempt.Get("lease_lapsed")) {
		return "", nil
	}
	if ap := state.Get("active_prompt"); ap != nil && ap.Kind == value.Object && value.Truth(ap.Get("joined")) {
		return "", nil
	}
	absent, err := c.attemptCommandsAbsent(config, attempt)
	if err != nil {
		return "", err
	}
	if !absent {
		return "", nil
	}
	stateDir, err := c.pathText(config.Get("state_dir"))
	if err != nil {
		return "", err
	}
	attemptIDText, err := c.pathText(attemptID)
	if err != nil {
		return "", err
	}
	target := filepath.Join(stateDir, "inbox", attemptIDText+".json")
	ctxVal, err := c.readContextJSON(target)
	if err != nil {
		if sourceClass(err, "OSError", "ValueError", "JSONDecodeError", "UnicodeDecodeError") || journal.Classified(err) {
			return "", nil
		}
		return "", err
	}
	if ctxVal == nil || ctxVal.Kind != value.Object || !nativeEqual(ctxVal.Get("attempt_id"), attemptID) {
		return "", nil
	}
	return c.formatUnacknowledgedTurnEndReminder(ctx, ns, config, state, statePath, observation, attempt, attemptID, attemptIDText, ctxVal)
}

func (c *CLI) formatUnacknowledgedTurnEndReminder(ctx context.Context, ns *NativeSession, config, state *value.Value, statePath string, observation, attempt, attemptID *value.Value, attemptIDText string, ctxVal *value.Value) (string, error) {
	var fallback string
	if s := ctxVal.Get("compact_source_unavailable"); s != nil && s.Kind == value.Bool && s.Boolean {
		fb := ctxVal.Get("compact_fallback_control")
		if fb == nil || fb.Kind != value.String || len(fb.String) == 0 {
			return "", nativeRefusal("INVALID_HOST", "unavailable source has no settlement instructions")
		}
		fallback = string(fb.String)
	}
	kind := nativeText(attempt.Get("delivery").Get("event").Get("kind"))
	if kind == "" {
		kind = nativeText(ctxVal.Get("kind"))
	}
	if kind == "" {
		kind = "inbox"
	}
	var reminder string
	switch {
	case fallback != "":
		reminder = fmt.Sprintf("Cairn inbox reminder: attempt %s still needs an explicit outcome. %s Ending the turn again without settlement will record this delivery as processing_failed.", attemptIDText, fallback)
	case (kind == "response" || kind == "notice") && ctxVal.Get("acknowledgement") != nil && ctxVal.Get("acknowledgement").Kind == value.Array:
		ackCmd, err := c.shlexJoinValue(ctxVal.Get("acknowledgement"))
		if err != nil {
			return "", err
		}
		reminder = fmt.Sprintf("Cairn inbox reminder: this turn received a %s (attempt %s) that has not been acknowledged yet. Run the exact acknowledgement command before ending your turn: %s. Ending the turn again without acknowledging will record this delivery as processing_failed.", kind, attemptIDText, ackCmd)
	case ctxVal.Get("completion") != nil && ctxVal.Get("completion").Kind == value.Array:
		cmdVal := ctxVal.Get("finish_reply")
		if !value.Truth(cmdVal) {
			cmdVal = ctxVal.Get("completion")
		}
		compCmd, err := c.shlexJoinValue(cmdVal)
		if err != nil {
			return "", err
		}
		replyHint := ""
		if !value.Truth(ctxVal.Get("finish_reply")) && ctxVal.Get("response") != nil && ctxVal.Get("response").Kind == value.Array {
			respCmd, err := c.shlexJoinValue(ctxVal.Get("response"))
			if err != nil {
				return "", err
			}
			replyHint = fmt.Sprintf(" Then send the reply with %s --version RESULT_VERSION RESULT_RECORD_UUID.", respCmd)
		}
		reminder = fmt.Sprintf("Cairn inbox reminder: this turn received a %s (attempt %s) that has no journaled completion yet. Run the exact command with a concise selected result on stdin before ending your turn: %s.%s Ending the turn again without completion will record this delivery as processing_failed.", kind, attemptIDText, compCmd, replyHint)
	default:
		return "", nil
	}
	reminder, err := c.CompatibilityControl(reminder)
	if err != nil {
		return "", err
	}
	prior := state.Get("inbox_ack_reminder_revision")
	if nativeEqual(state.Get("inbox_ack_reminded"), attemptID) {
		if (prior == nil || prior.Kind != value.Object || !nativeEqual(prior.Get("attempt_id"), attemptID)) && fallback == "" {
			return "", nil
		}
	}
	jState, err := c.journalCompletionState(config, attempt)
	if err != nil {
		return "", err
	}
	revision, err := canonicalSortedJSONSHA256(nativeMap("attempt_id", attemptID, "journal_state", jState, "reminder", reminder))
	if err != nil {
		return "", err
	}
	if prior != nil && prior.Kind == value.Object && nativeEqual(prior.Get("attempt_id"), attemptID) && value.SameText(prior.Get("sha256"), revision) {
		return "", nil
	}
	if _, err := ns.Renew(ctx, attempt); err != nil {
		return "", err
	}
	if value.Truth(attempt.Get("lease_lapsed")) {
		return "", nil
	}
	state.Set("inbox_ack_reminded", attemptID)
	state.Set("inbox_ack_reminder_revision", nativeMap("attempt_id", attemptID, "sha256", revision))
	state.Set("delivered_since_idle", value.Boolean(true))
	if value.SameText(config.Get("harness"), "claude") && value.Truth(observation.Get("native_turn_id")) {
		state.Set("active_prompt", nativeMap("id", observation.Get("native_turn_id")))
	}
	return reminder, c.writeState(statePath, state)
}

func (c *CLI) nativeWakeOwnsAdmission(ctx context.Context, config, state, wakeBinding *value.Value) (bool, bool, error) {
	ref, err := c.sessionRef(state.Get("agent"))
	if err != nil {
		return false, false, err
	}
	idleWake := state.Get("idle_wake")
	opencodePending := idleWake != nil && idleWake.Kind == value.Object &&
		value.SameText(idleWake.Get("transport"), "opencode-queue") &&
		nativeEqual(idleWake.Get("session"), ref)
	replay := state.Get("opencode_replay")
	opencodeReplay := replay != nil && replay.Kind == value.Object && nativeEqual(replay.Get("session"), ref)
	if value.Truth(state.Get("inbox_intent")) || !value.Truth(config.Get("idle_wakeup")) || value.Truth(wakeBinding) {
		return false, opencodeReplay, nil
	}
	harness := nativeText(config.Get("harness"))
	procVal := state.Get("process")
	procRef := wakeProcessRef(procVal)
	if _, ok := wake.SelectCodexQueueEndpoint(harness, procRef); ok {
		return true, opencodeReplay, nil
	}
	if _, ok := wake.SelectHermesQueueEndpoint(harness, procRef); ok {
		return true, opencodeReplay, nil
	}
	if harness == "opencode" {
		if opencodePending {
			return true, opencodeReplay, nil
		}
		if !opencodeReplay {
			var procIdentity *value.Value
			if procVal != nil && procVal.Kind == value.Object {
				procIdentity = nativeMap("pid", procVal.Get("pid"), "start", procVal.Get("start"), "boot", procVal.Get("boot"))
			}
			if !nativeEqual(state.Get("opencode_unsupported"), procIdentity) {
				nativeID := nativeText(state.Get("agent").Get("native_session_id"))
				if _, ok := wake.SelectOpenCodeIdleEndpoint(ctx, harness, procRef, nativeID, nil); ok {
					return true, opencodeReplay, nil
				}
			}
		}
	}
	if harness == "claude" && value.Truth(config.Get("claude_channel_dir")) {
		return true, opencodeReplay, nil
	}
	return false, opencodeReplay, nil
}

func (c *CLI) newNativeSession(config, state *value.Value, statePath string) *NativeSession {
	host := c.Host
	if c.Compatibility != nil && (host == nil || host.Compatibility == nil) {
		var hc Host
		if host != nil {
			hc = *host
		}
		hc.Compatibility = c.Compatibility
		host = &hc
	}
	return &NativeSession{Config: config, Record: state, Path: statePath, Host: host}
}

func (c *CLI) settleIdleInboxContext(ctx context.Context, ns *NativeSession, config, state *value.Value, statePath string, observation *value.Value) (*value.Value, error) {
	if value.Truth(state.Get("delivered_since_idle")) || value.Truth(state.Get("inbox_intent")) {
		attempt, err := c.opencodeInboxControl(ctx, ns, config, state)
		if err != nil {
			return nil, err
		}
		closeObj, _ := nativeObj(state.Get("inbox_close"), "inbox_close")
		reason := nativeText(closeObj.Get("reason"))
		if !value.Truth(attempt) && (reason == "cancel_confirmed" || reason == "exclusivity_revoked") {
			if _, err := ns.Release(ctx, reason, false); err != nil {
				return nil, err
			}
		} else if value.Truth(attempt) && value.Truth(attempt.Get("cancel")) && !value.Truth(attempt.Get("cancel").Get("confirmed_at")) {
			if _, err := c.opencodeCancelledTurnEnd(ctx, config, state, statePath, observation, attempt); err != nil {
				return nil, err
			}
		} else {
			reminder, err := c.unacknowledgedTurnEndReminder(ctx, ns, config, state, statePath, observation)
			if err != nil {
				return nil, err
			}
			if reminder != "" {
				return value.Text(reminder), nil
			}
			if value.Truth(state.Get("inbox_intent")) {
				if err := ns.MarkTurnEnded(); err != nil {
					return nil, err
				}
			}
			if err := c.flushAndEndTurnIdle(ctx, ns, config, state, statePath, observation); err != nil {
				return nil, err
			}
		}
		state.Set("delivered_since_idle", value.Boolean(false))
		return value.Text(""), ns.Save()
	}
	return value.Text(""), nil
}

func (c *CLI) flushAndEndTurnIdle(ctx context.Context, ns *NativeSession, config, state *value.Value, statePath string, observation *value.Value) error {
	if c.Host == nil || c.Host.Journal == nil {
		return &value.ProfileUnavailable{}
	}
	settled, err := c.Host.Journal.Flush(ctx, config, state, FlushAttempt)
	if err == nil && settled {
		_, err = ns.EndTurn(ctx, false)
	}
	if err != nil {
		if !value.SameText(config.Get("harness"), "opencode") || nativeCode(err) != "CLEANUP_UNCONFIRMED" {
			return err
		}
		attempt, ctrlErr := c.opencodeInboxControl(ctx, ns, config, state)
		if ctrlErr != nil {
			return ctrlErr
		}
		if !value.Truth(attempt) || !value.Truth(attempt.Get("cancel")) {
			return err
		}
		_, err = c.opencodeCancelledTurnEnd(ctx, config, state, statePath, observation, attempt)
		return err
	}
	return nil
}

func validOpenCodeTurnID(v *value.Value) bool {
	if v == nil || v.Kind != value.String {
		return false
	}
	s := string(v.String)
	if strings.TrimSpace(s) == "" || len(s) > 256 {
		return false
	}
	for _, r := range v.String {
		if r < 32 {
			return false
		}
	}
	return true
}

func (c *CLI) inboxContext(ctx context.Context, config, state *value.Value, statePath string, observation, wakeBinding *value.Value) (*value.Value, error) {
	var err error
	config, err = c.claudeSessionConfig(config, state.Get("agent").Get("native_session_id"), state.Get("process"))
	if err != nil {
		return nil, err
	}
	if !value.Truth(config.Get("native_delivery")) {
		return value.Text(""), nil
	}
	if value.SameText(config.Get("harness"), "claude") && value.SameText(observation.Get("event"), "SessionStart") {
		daemon, err := c.claudeDaemonManaged(state.Get("process"))
		if err != nil {
			return nil, err
		}
		if daemon {
			return value.Text(""), nil
		}
	}
	ns := c.newNativeSession(config, state, statePath)
	if value.Truth(state.Get("organic_claim_intent")) || value.Truth(state.Get("organic_advance_intent")) {
		if _, err := ns.Recover(ctx); err != nil {
			return nil, err
		}
	}
	if value.SameText(config.Get("harness"), "codex") && value.SameText(observation.Get("event"), "UserPromptSubmit") && value.Truth(observation.Get("owner_input_attestation")) {
		if _, err := ns.Recover(ctx); err != nil {
			return nil, err
		}
		if _, err := ns.AdmitOrganicNotice(ctx, observation.Get("native_turn_id"), observation.Get("owner_input_origin"), observation.Get("owner_input_attestation")); err != nil {
			return nil, err
		}
	}
	if done, err := c.checkOwnerTurnMismatch(ctx, ns, config, state, observation); err != nil || done {
		return value.Text(""), err
	}
	if value.SameText(observation.Get("event"), "Stop") && !value.SameText(observation.Get("phase"), "idle") {
		return value.Text(""), nil
	}
	if value.SameText(observation.Get("phase"), "idle") {
		if value.Truth(state.Get("delivered_since_idle")) || value.Truth(state.Get("inbox_intent")) {
			return c.settleIdleInboxContext(ctx, ns, config, state, statePath, observation)
		}
		if !nativeContains([]string{"codex", "claude", "hermes", "agy"}, nativeText(config.Get("harness"))) {
			return value.Text(""), nil
		}
	}
	owns, opencodeReplay, err := c.nativeWakeOwnsAdmission(ctx, config, state, wakeBinding)
	if err != nil {
		return nil, err
	}
	if owns {
		return value.Text(""), nil
	}
	if _, err := ns.Recover(ctx); err != nil {
		return nil, err
	}
	if !value.Truth(state.Get("inbox_attempt")) {
		freshChannel := value.SameText(config.Get("harness"), "claude") && value.Truth(wakeBinding) && value.SameText(observation.Get("event"), "UserPromptSubmit") && nativeEqual(wakeBinding.Get("native_turn_id"), observation.Get("native_turn_id"))
		if value.Truth(state.Get("delivered_since_idle")) && !freshChannel {
			return value.Text(""), nil
		}
		binding := c.selectAdmissionBinding(config, state, observation, wakeBinding, opencodeReplay)
		if !value.Truth(state.Get("inbox_attempt")) {
			if _, err := ns.AdmitInbox(ctx, binding); err != nil {
				return nil, err
			}
		}
	}
	return c.RenderActiveAttemptContext(ctx, config, state, statePath)
}

func (c *CLI) checkOwnerTurnMismatch(ctx context.Context, ns *NativeSession, config, state, observation *value.Value) (bool, error) {
	intent := state.Get("inbox_intent")
	if intent == nil || intent.Kind != value.Object {
		return false, nil
	}
	ownerTurn := intent.Get("native_turn_id")
	if !value.Truth(ownerTurn) || nativeEqual(ownerTurn, observation.Get("native_turn_id")) {
		return false, nil
	}
	if value.SameText(config.Get("harness"), "opencode") && value.SameText(observation.Get("event"), "TurnStart") {
		attempt, err := c.opencodeInboxControl(ctx, ns, config, state)
		if err != nil {
			return false, err
		}
		if value.Truth(attempt) && value.Truth(attempt.Get("cancel")) && !value.Truth(attempt.Get("cancel").Get("confirmed_at")) {
			if !nativeContains([]string{"ended", "interrupted"}, nativeText(attempt.Get("turn_stop_state"))) {
				req := state.Get("cancel_owner_join")
				if !value.Truth(req) || req.Kind != value.Object || !nativeEqual(req.Get("attempt_id"), attempt.Get("attempt_id")) {
					req = nativeMap("request_id", c.newUUID(), "session", attempt.Get("session"), "attempt_id", attempt.Get("attempt_id"), "owner_join", value.Boolean(true), "tools", nativeList())
					state.Set("cancel_owner_join", req)
					if err := ns.Save(); err != nil {
						return false, err
					}
				}
				updated, err := c.call(ctx, config, "session-tool-stop", req, NativeCallOptions{})
				if err != nil {
					return false, err
				}
				state.Set("inbox_attempt", updated)
				if err := ns.Save(); err != nil {
					return false, err
				}
			}
			return true, nil
		}
	}
	return false, nativeRefusal("NATIVE_TURN_MISMATCH", "another native turn cannot take over or end this request")
}

func (c *CLI) selectAdmissionBinding(config, state, observation, wakeBinding *value.Value, opencodeReplay bool) *value.Value {
	if value.Truth(wakeBinding) {
		return wakeBinding
	}
	if opencodeReplay && value.Truth(observation.Get("native_turn_id")) {
		return nativeMap("delivery_id", state.Get("opencode_replay").Get("delivery_id"), "native_turn_id", observation.Get("native_turn_id"))
	}
	nt := observation.Get("native_turn_id")
	if value.SameText(config.Get("harness"), "codex") && value.SameText(observation.Get("event"), "UserPromptSubmit") && validOpenCodeTurnID(nt) {
		return nativeMap("native_turn_id", nt)
	}
	return value.Mapping()
}
