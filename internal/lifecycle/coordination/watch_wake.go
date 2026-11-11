package coordination

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/lifecycle/wake"
	"github.com/halbritt/cairn/internal/procpeer"
)

func (c *CLI) claudeChannelAdmission(config, nativeID, process *value.Value) (*value.Value, string, error) {
	if _, err := nativeObj(config, "config"); err != nil {
		return nil, "", err
	}
	if !value.SameText(config.Get("harness"), "claude") {
		return config, "", nil
	}
	if !value.Truth(config.Get("claude_channel_dir")) {
		return config, "binding has no claude_channel_dir", nil
	}
	var reason string
	if sessions := config.Get("claude_channel_sessions"); sessions != nil {
		contains, err := sourceContains(sessions, nativeID)
		if err != nil {
			return nil, "", err
		}
		if !contains {
			reason = "native conversation is not listed in claude_channel_sessions"
		}
	}
	if reason == "" && !c.claudeChannelEnabled(config, process) {
		srv := "cairn-events"
		if s := config.Get("claude_channel_server"); s != nil && s.Kind == value.String {
			srv = nativeText(s)
		}
		reason = fmt.Sprintf("Claude process was not launched with --dangerously-load-development-channels=server:%s (or --channels naming it), so it cannot receive channel wakes", srv)
	}
	if reason == "" {
		return config, "", nil
	}
	out := nativeClone(config)
	nativePop(out, "claude_channel_dir")
	return out, reason, nil
}

func (c *CLI) noteWakeRefusal(config, state *value.Value, delivery, reason, condition string, cycle *value.Value, errOut io.Writer) error {
	if condition == "" {
		condition = "unknown"
	}
	if cycle != nil && cycle.Kind == value.Object {
		cycle.Set("refusal_delivery", value.Text(delivery))
	}
	record := nativeMap("delivery_id", delivery, "reason", reason, "condition", condition)
	if nativeEqual(state.Get("wake_refusal"), record) {
		return nil
	}
	state.Set("wake_refusal", record)
	agent, err := nativeRequired(state, "agent")
	if err != nil {
		return err
	}
	proc, err := nativeRequired(state, "process")
	if err != nil {
		return err
	}
	pid, err := nativeRequired(proc, "pid")
	if err != nil {
		return err
	}
	display := nativeOr(agent.Get("display_name"), agent.Get("agent_id"))
	if errOut != nil {
		_, _ = fmt.Fprintf(errOut, "Cairn presence %s: no automatic wake for %s (native %s, pid %s): %s. Delivery %s remains pending.\n",
			nativeText(config.Get("binding")), pythonRepr(display, false), pythonRepr(agent.Get("native_session_id"), false), pythonRepr(pid, false), reason, delivery)
	}
	return nil
}

func (c *CLI) dropIdleWake(path string, wakeObj *value.Value) error {
	lease, err := c.lockState(path)
	if err != nil {
		return err
	}
	defer func() { _ = lease.Close() }()
	state, err := c.read(path, SiteSessionRead)
	if err != nil {
		return err
	}
	if nativeEqual(state.Get("idle_wake"), wakeObj) {
		nativePop(state, "idle_wake")
		return c.writeState(path, state)
	}
	return nil
}

func watchProcessIdentity(ref *value.Value) []*value.Value {
	if ref == nil || ref.Kind != value.Object {
		return nil
	}
	out := make([]*value.Value, 0, 3)
	for _, k := range []string{"pid", "start", "boot"} {
		v := ref.Get(k)
		if v == nil || (v.Kind != value.Int && v.Kind != value.Bool && v.Kind != value.String) {
			return nil
		}
		out = append(out, v)
	}
	return out
}

func sameWatchProcessIdentity(a, b *value.Value) bool {
	ia, ib := watchProcessIdentity(a), watchProcessIdentity(b)
	if ia == nil || ib == nil {
		return ia == nil && ib == nil
	}
	for i := range ia {
		if !nativeEqual(ia[i], ib[i]) {
			return false
		}
	}
	return true
}

func (c *CLI) claudeChannelEndpoint(config, process *value.Value) (*value.Value, error) {
	harness, err := nativeRequired(config, "harness")
	if err != nil {
		return nil, err
	}
	dir := config.Get("claude_channel_dir")
	if nativeText(harness) != "claude" || !value.Truth(dir) {
		return nil, nil
	}
	dirText, err := c.pathText(dir)
	if err != nil {
		return nil, err
	}
	pidVal, err := nativeRequired(process, "pid")
	if err != nil {
		return nil, err
	}
	rec, err := c.readJSONObject(filepath.Join(dirText, pythonRepr(pidVal, false)+".json"))
	if err != nil || rec == nil {
		return nil, err
	}
	parent := rec.Get("parent")
	bridge := rec.Get("process")
	sock := rec.Get("socket")
	sockText := nativeText(sock)
	if nativeText(rec.Get("schema")) != wake.ClaudeChannelSchema || !sameWatchProcessIdentity(parent, process) || bridge == nil || bridge.Kind != value.Object || sock == nil || sock.Kind != value.String || !filepath.IsAbs(sockText) {
		return nil, nil
	}
	if alive, err := c.processAlive(bridge); err != nil || !alive {
		return nil, err
	}
	return nativeMap("socket", sock, "process", bridge, "parent", parent), nil
}

func (c *CLI) codexQueueEndpoint(config, process *value.Value) (*value.Value, error) {
	if _, err := nativeRequired(config, "harness"); err != nil {
		return nil, err
	}
	return (CodexHealthBindings{ProcRoot: c.ProcRoot}).endpoint(context.Background(), config, process)
}

func (c *CLI) opencodeQueueEndpoint(config, process *value.Value) (string, error) {
	if _, err := nativeObj(config, "config"); err != nil {
		return "", err
	}
	if !value.SameText(config.Get("harness"), "opencode") {
		return "", nil
	}
	pidVal, err := nativeRequired(process, "pid")
	if err != nil {
		return "", err
	}
	sock := filepath.Join("/tmp", fmt.Sprintf("cairn-opencode-%s.sock", pythonRepr(pidVal, false)))
	info, err := os.Stat(sock)
	if err != nil || info.Mode()&os.ModeSocket == 0 {
		return "", nil
	}
	return sock, nil
}

func (c *CLI) opencodeIdleEndpoint(ctx context.Context, config, process, nativeID, state *value.Value) (string, error) {
	if _, err := nativeObj(process, "process"); err != nil {
		return "", err
	}
	identity := nativeMap("pid", process.Get("pid"), "start", process.Get("start"), "boot", process.Get("boot"))
	if state != nil && state.Kind == value.Object && nativeEqual(state.Get("opencode_unsupported"), identity) {
		return "", nil
	}
	ep, err := c.opencodeQueueEndpoint(config, process)
	if err != nil || ep == "" {
		return "", err
	}
	client := wake.OpenCodeQueueClient{ProcRoot: c.ProcRoot}
	if !client.SupportsIdle(ctx, ep, wakeProcessRef(process), nativeText(nativeID)) {
		return "", nil
	}
	return ep, nil
}

func (c *CLI) hermesQueueEndpoint(config, process *value.Value) (string, error) {
	if _, err := nativeObj(config, "config"); err != nil {
		return "", err
	}
	if !value.SameText(config.Get("harness"), "hermes") {
		return "", nil
	}
	pidVal, err := nativeRequired(process, "pid")
	if err != nil {
		return "", err
	}
	sock := filepath.Join("/tmp", fmt.Sprintf("cairn-hermes-%s.sock", pythonRepr(pidVal, false)))
	info, err := os.Stat(sock)
	if err != nil || info.Mode()&os.ModeSocket == 0 {
		return "", nil
	}
	return sock, nil
}

func attachEventID(wakeRec, ready *value.Value) {
	if ready != nil && ready.Kind == value.Object {
		if ev := ready.Get("event_id"); ev != nil && ev.Kind == value.String && validUUIDText(nativeText(ev)) {
			wakeRec.Set("event_id", ev)
		}
	}
}

func canResumeCodexQueued(prior, endpoint, agent *value.Value) (bool, error) {
	if !value.Truth(endpoint) || prior == nil || prior.Kind != value.Object {
		return false, nil
	}
	epVal, err := nativeRequired(endpoint, "endpoint")
	if err != nil {
		return false, err
	}
	nativeID, err := nativeRequired(agent, "native_session_id")
	if err != nil {
		return false, err
	}
	return nativeText(prior.Get("transport")) == wake.TransportCodexQueue &&
		nativeText(prior.Get("status")) == "queued" &&
		value.Truth(prior.Get("queued_submission_id")) &&
		nativeEqual(prior.Get("endpoint"), epVal) &&
		nativeEqual(prior.Get("native_id"), nativeID), nil
}

func (c *CLI) probeCodexNativeHealth(ctx context.Context, config, state *value.Value, force bool) (*value.Value, error) {
	return (CodexHealthBindings{ProcRoot: c.ProcRoot, Now: c.clock}).ProbeCodex(ctx, config, state, force)
}

func (c *CLI) prepareIdleWake(ctx context.Context, config, state *value.Value, path string, cycle *value.Value, errOut io.Writer) (*value.Value, error) {
	if _, err := nativeObj(state, "state"); err != nil {
		return nil, err
	}
	agentInit, err := nativeDefaultObj(state.Get("agent"), "agent")
	if err != nil {
		return nil, err
	}
	nativeID := agentInit.Get("native_session_id")
	process, err := nativeRequired(state, "process")
	if err != nil {
		return nil, err
	}
	originalConfig := config
	config, refusal, err := c.claudeChannelAdmission(config, nativeID, process)
	if err != nil {
		return nil, err
	}
	refusalCondition := ""
	if refusal != "" {
		if !value.Truth(originalConfig.Get("claude_channel_dir")) {
			refusalCondition = "channel_unconfigured"
		} else if sessions := originalConfig.Get("claude_channel_sessions"); sessions != nil {
			if contains, err := sourceContains(sessions, nativeID); err != nil {
				return nil, err
			} else if !contains {
				refusalCondition = "channel_not_selected"
			} else {
				refusalCondition = "channel_not_launched"
			}
		} else {
			refusalCondition = "channel_not_launched"
		}
	}
	if !value.Truth(config.Get("idle_wakeup")) || !value.Truth(config.Get("native_delivery")) ||
		value.Truth(state.Get("inbox_intent")) || value.Truth(state.Get("ending")) || value.Truth(state.Get("retired")) {
		return nil, nil
	}
	return c.prepareIdleWakeEligible(ctx, config, state, path, cycle, refusal, refusalCondition, errOut)
}

func (c *CLI) prepareIdleWakeEligible(ctx context.Context, config, state *value.Value, path string, cycle *value.Value, refusal, refusalCondition string, errOut io.Writer) (*value.Value, error) {
	agent, err := nativeRequired(state, "agent")
	if err != nil {
		return nil, err
	}
	meta, err := nativeRequired(agent, "metadata")
	if err != nil {
		return nil, err
	}
	ws, err := nativeRequired(state, "workspace")
	if err != nil {
		return nil, err
	}
	metaWS, err := nativeRequired(meta, "workspace")
	if err != nil {
		return nil, err
	}
	if c.workspacesExcluded([]*value.Value{ws, metaWS}) {
		return nil, nil
	}
	delivMode, err := nativeRequired(meta, "delivery_mode")
	if err != nil {
		return nil, err
	}
	if nativeText(delivMode) != "existing-session" {
		return nil, nil
	}
	agentState, err := nativeRequired(meta, "state")
	if err != nil {
		return nil, err
	}
	if nativeText(agentState) != "idle" {
		if nativeText(config.Get("harness")) == "codex" && nativeText(agentState) == "busy" {
			var stop bool
			agent, stop, err = c.prepareIdleWakeBusyCodex(ctx, config, state, agent, path, cycle, errOut)
			if err != nil || stop {
				return nil, err
			}
			meta, err = nativeRequired(agent, "metadata")
			if err != nil {
				return nil, err
			}
			agentState, err = nativeRequired(meta, "state")
			if err != nil {
				return nil, err
			}
		}
		if nativeText(agentState) != "idle" {
			return nil, nil
		}
	}
	return c.prepareIdleWakeReady(ctx, config, state, agent, path, cycle, refusal, refusalCondition, errOut)
}

func (c *CLI) prepareIdleWakeBusyCodex(ctx context.Context, config, state, agent *value.Value, path string, cycle *value.Value, errOut io.Writer) (*value.Value, bool, error) {
	health, err := c.probeCodexNativeHealth(ctx, config, state, false)
	if err != nil {
		return nil, false, err
	}
	if !nativeNone(health) {
		if err := c.writeState(path, state); err != nil {
			return nil, false, err
		}
	}
	sref, err := c.sessionRef(agent)
	if err != nil {
		return nil, false, err
	}
	if value.Truth(health) && nativeText(health.Get("status_type")) == "systemError" {
		if nativeEqual(health.Get("session"), sref) {
			cew, err := c.codexErrorWake(ctx, config, state, float64(c.clock().UnixNano())/1e9, errOut)
			if err != nil {
				return nil, false, err
			}
			if nativeText(cew.Get("decision")) == "wake_allowed" {
				rev, err := nativeRequired(agent, "context_revision")
				if err != nil {
					return nil, false, err
				}
				meta, err := nativeRequired(agent, "metadata")
				if err != nil {
					return nil, false, err
				}
				newMeta := nativeClone(meta)
				newMeta.Set("state", value.Text("idle"))
				updated, err := c.call(ctx, config, "agent-context", nativeMap("request_id", c.newUUID(), "session", sref, "expected_revision", rev, "metadata", newMeta), NativeCallOptions{})
				if err != nil {
					return nil, false, err
				}
				state.Set("agent", updated)
				agent = updated
				state.Get("codex_error_wake").Set("presence", value.Text("busy_corrected_to_idle"))
				if err := c.writeState(path, state); err != nil {
					return nil, false, err
				}
			} else if err := c.writeState(path, state); err != nil {
				return nil, false, err
			}
		} else if err := c.writeState(path, state); err != nil {
			return nil, false, err
		}
	}
	meta, err := nativeRequired(agent, "metadata")
	if err != nil {
		return nil, false, err
	}
	if value.Truth(health) && nativeText(health.Get("status_type")) == "systemError" && nativeText(meta.Get("state")) != "idle" {
		ready, err := c.call(ctx, config, "session-inbox-ready", sref, NativeCallOptions{})
		if err != nil {
			return nil, false, err
		}
		deliv := ready.Get("delivery_id")
		if cycle != nil && cycle.Kind == value.Object {
			cycle.Set("ready_delivery", deliv)
		}
		if !value.Truth(deliv) {
			nativePop(state, "wake_refusal")
			return agent, true, nil
		}
		err = c.noteWakeRefusal(config, state, nativeText(deliv), "native Codex thread reports systemError behind busy metadata; review the pending delivery and provider state before owner-authorized recovery", "native_transport_unavailable", cycle, errOut)
		return agent, true, err
	}
	return agent, false, nil
}

func (c *CLI) prepareIdleWakeReady(ctx context.Context, config, state, agent *value.Value, path string, cycle *value.Value, refusal, refusalCondition string, errOut io.Writer) (*value.Value, error) {
	sref, err := c.sessionRef(agent)
	if err != nil {
		return nil, err
	}
	if nativeText(config.Get("harness")) == "codex" {
		if nh := state.Get("native_health"); nh != nil && nh.Kind == value.Object && nativeText(nh.Get("status_type")) == "systemError" {
			health, err := c.probeCodexNativeHealth(ctx, config, state, false)
			if err != nil {
				return nil, err
			}
			if value.Truth(health) && nativeText(health.Get("status_type")) == "systemError" && nativeEqual(health.Get("session"), sref) {
				allowed, err := c.codexErrorWakeAllowed(config, state)
				if err != nil {
					return nil, err
				}
				if !allowed {
					if _, err := c.codexErrorWake(ctx, config, state, float64(c.clock().UnixNano())/1e9, errOut); err != nil {
						return nil, err
					}
				}
			}
		}
	}
	if value.Truth(state.Get("wake_rejection")) && value.Truth(state.Get("idle_wake")) {
		if _, err := c.ReconcileRejectedWake(ctx, config, state, path); err != nil {
			return nil, err
		}
	}
	ready, err := c.call(ctx, config, "session-inbox-ready", sref, NativeCallOptions{})
	if err != nil {
		return nil, err
	}
	delivVal := ready.Get("delivery_id")
	if cycle != nil && cycle.Kind == value.Object {
		cycle.Set("ready_delivery", delivVal)
	}
	if !value.Truth(delivVal) {
		nativePop(state, "wake_refusal", "opencode_replay")
		return nil, nil
	}
	return c.prepareIdleWakeAfterGuards(ctx, config, state, agent, sref, ready, nativeText(delivVal), path, cycle, refusal, refusalCondition, errOut)
}

func (c *CLI) prepareIdleWakeAfterGuards(ctx context.Context, config, state, agent, sref, ready *value.Value, delivery, path string, cycle *value.Value, refusal, refusalCondition string, errOut io.Writer) (*value.Value, error) {
	process, err := nativeRequired(state, "process")
	if err != nil {
		return nil, err
	}
	if nativeText(config.Get("harness")) == "claude" {
		if dm, err := c.claudeDaemonManaged(process); err != nil {
			return nil, err
		} else if dm {
			return nil, c.noteWakeRefusal(config, state, delivery, "daemon-managed background Claude session has no wake transport; delivery waits for its next prompt", "native_transport_unavailable", cycle, errOut)
		}
	}
	if nativeText(config.Get("harness")) == "codex" {
		if nh := state.Get("native_health"); nh != nil && nh.Kind == value.Object && nativeEqual(nh.Get("session"), sref) && nativeText(nh.Get("status_type")) == "systemError" {
			allowed, err := c.codexErrorWakeAllowed(config, state)
			if err != nil {
				return nil, err
			}
			if !allowed {
				return nil, c.noteWakeRefusal(config, state, delivery, "native Codex thread reports systemError; review the pending delivery and provider state before owner-authorized recovery", "native_transport_unavailable", cycle, errOut)
			}
		}
	}
	replay, err := nativeDefaultObj(state.Get("opencode_replay"), "opencode_replay")
	if err != nil {
		return nil, err
	}
	if nativeText(replay.Get("delivery_id")) == delivery && nativeEqual(replay.Get("session"), sref) {
		return nil, c.noteWakeRefusal(config, state, delivery, "native OpenCode already admitted this request; the next owner prompt can recover pending delivery", "native_admission_retained", cycle, errOut)
	}
	nativePop(state, "opencode_replay")
	endpoint, err := c.codexQueueEndpoint(config, process)
	if err != nil {
		return nil, err
	}
	prior, err := nativeDefaultObj(state.Get("idle_wake"), "idle_wake")
	if err != nil {
		return nil, err
	}
	if nativeEqual(prior.Get("session"), sref) && nativeText(prior.Get("reconciliation_required")) == "queue_item_missing" {
		cond := "unknown"
		if nativeText(prior.Get("delivery_id")) == delivery {
			cond = "wake_retained"
		}
		return nil, c.noteWakeRefusal(config, state, delivery, "retained native queue item is missing; prior admission is unknown; reconciliation required", cond, cycle, errOut)
	}
	if nativeText(prior.Get("delivery_id")) == delivery && nativeEqual(prior.Get("session"), sref) {
		canResume, err := canResumeCodexQueued(prior, endpoint, agent)
		if err != nil {
			return nil, err
		}
		if canResume {
			return nativeMap("wake", prior, "process", process), nil
		}
		tr := nativeText(prior.Get("transport"))
		if tr == "" {
			tr = "terminal"
		}
		st := nativeText(prior.Get("status"))
		cond := "unknown"
		if st == "submitted" || st == "uncertain" || st == "queued" || st == "refused" {
			cond = "wake_retained"
		}
		return nil, c.noteWakeRefusal(config, state, delivery, fmt.Sprintf("a %s wake for this delivery is already %s; only native handling of that wake permits another", tr, pythonRepr(prior.Get("status"), false)), cond, cycle, errOut)
	}
	return c.prepareIdleWakeTransport(ctx, config, state, agent, process, sref, ready, endpoint, delivery, path, cycle, refusal, refusalCondition, errOut)
}

func unavailableWakeReason(harness string) string {
	switch harness {
	case wake.HarnessCodex:
		return wake.UnavailableReasonCodex
	case wake.HarnessClaude:
		return wake.UnavailableReasonClaude
	case wake.HarnessOpenCode:
		return wake.UnavailableReasonOpenCode
	case wake.HarnessHermes:
		return wake.UnavailableReasonHermes
	default:
		return wake.UnavailableReasonDefault
	}
}

func isNativeWakeHarness(harness string) bool {
	return harness == wake.HarnessCodex || harness == wake.HarnessClaude || harness == wake.HarnessOpenCode || harness == wake.HarnessHermes
}

func (c *CLI) prepareIdleWakeTransport(ctx context.Context, config, state, agent, process, sref, ready, codexEndpoint *value.Value, delivery, path string, cycle *value.Value, refusal, refusalCondition string, errOut io.Writer) (*value.Value, error) {
	reqID := nativeText(ready.Get("request_id"))
	if reqID == "" {
		reqID = c.newUUID()
	}
	preparedNative, err := c.prepareNativeWake(ctx, config, state, agent, process, sref, ready, codexEndpoint, delivery, reqID, path)
	if err != nil || preparedNative != nil {
		return preparedNative, err
	}
	harness := nativeText(config.Get("harness"))
	if value.Truth(config.Get("claude_channel_dir")) || isNativeWakeHarness(harness) {
		reason := refusal
		if reason == "" {
			reason = unavailableWakeReason(harness)
		}
		cond := refusalCondition
		if cond == "" {
			cond = "native_transport_unavailable"
		}
		return nil, c.noteWakeRefusal(config, state, delivery, reason, cond, cycle, errOut)
	}
	return c.prepareTerminalWake(ctx, config, state, agent, process, sref, delivery, reqID, path, cycle, errOut)
}

func (c *CLI) prepareNativeWake(ctx context.Context, config, state, agent, process, sref, ready, codexEndpoint *value.Value, delivery, reqID, path string) (*value.Value, error) {
	if prep, err := c.prepareCodexQueueWake(config, state, agent, process, sref, ready, codexEndpoint, delivery, reqID, path); err != nil || prep != nil {
		return prep, err
	}
	if prep, err := c.prepareClaudeChannelWake(config, state, agent, process, sref, ready, delivery, reqID, path); err != nil || prep != nil {
		return prep, err
	}
	if prep, err := c.prepareOpenCodeQueueWake(ctx, config, state, agent, process, sref, delivery, path); err != nil || prep != nil {
		return prep, err
	}
	return c.prepareHermesQueueWake(config, state, agent, process, sref, delivery, reqID, path)
}

func (c *CLI) prepareCodexQueueWake(config, state, agent, process, sref, ready, endpoint *value.Value, delivery, reqID, path string) (*value.Value, error) {
	resolved := endpoint
	if !value.Truth(resolved) {
		var err error
		resolved, err = c.codexQueueEndpoint(config, process)
		if err != nil || !value.Truth(resolved) {
			return nil, err
		}
	}
	epVal, err := nativeRequired(resolved, "endpoint")
	if err != nil {
		return nil, err
	}
	ownerVal, err := nativeRequired(resolved, "owner")
	if err != nil {
		return nil, err
	}
	nativeID, err := nativeRequired(agent, "native_session_id")
	if err != nil {
		return nil, err
	}
	nativePop(state, "wake_refusal")
	if err := c.noteCodexErrorWake(config, state, delivery); err != nil {
		return nil, err
	}
	wakeRec := nativeMap("delivery_id", delivery, "client_id", delivery, "request_id", reqID, "session", sref, "transport", wake.TransportCodexQueue, "endpoint", epVal, "owner", ownerVal, "native_id", nativeID, "status", "uncertain", "attempted_at", float64(c.clock().UnixNano())/1e9)
	attachEventID(wakeRec, ready)
	state.Set("idle_wake", wakeRec)
	if err := c.writeState(path, state); err != nil {
		return nil, err
	}
	return nativeMap("wake", wakeRec, "process", process), nil
}

func (c *CLI) prepareClaudeChannelWake(config, state, agent, process, sref, ready *value.Value, delivery, reqID, path string) (*value.Value, error) {
	channel, err := c.claudeChannelEndpoint(config, process)
	if err != nil || !value.Truth(channel) {
		return nil, err
	}
	nativeID, err := nativeRequired(agent, "native_session_id")
	if err != nil {
		return nil, err
	}
	nativePop(state, "wake_refusal")
	wakeRec := nativeMap("delivery_id", delivery, "request_id", reqID, "session", sref, "transport", wake.TransportClaudeChannel, "endpoint", channel.Get("socket"), "bridge", channel.Get("process"), "parent", channel.Get("parent"), "native_id", nativeID, "status", "uncertain", "attempted_at", float64(c.clock().UnixNano())/1e9)
	attachEventID(wakeRec, ready)
	state.Set("idle_wake", wakeRec)
	if err := c.writeState(path, state); err != nil {
		return nil, err
	}
	return nativeMap("wake", wakeRec, "process", process), nil
}

func (c *CLI) prepareOpenCodeQueueWake(ctx context.Context, config, state, agent, process, sref *value.Value, delivery, path string) (*value.Value, error) {
	nativeID, err := nativeRequired(agent, "native_session_id")
	if err != nil {
		return nil, err
	}
	opencode, err := c.opencodeIdleEndpoint(ctx, config, process, nativeID, state)
	if err != nil || opencode == "" {
		return nil, err
	}
	nativePop(state, "wake_refusal")
	cancelCapable := false
	if value.Truth(config.Get("opencode_cancel_enabled")) && c.opencodeCaptureAvailable() {
		client := wake.OpenCodeQueueClient{ProcRoot: c.ProcRoot}
		pref := wakeProcessRef(process)
		nid := nativeText(nativeID)
		cancelCapable = client.SupportsCancel(ctx, opencode, pref, nid) && client.SupportsToolCapture(ctx, opencode, pref, nid)
	}
	wakeRec := nativeMap("delivery_id", delivery, "request_id", delivery, "session", sref, "transport", wake.TransportOpenCodeQueue, "endpoint", opencode, "native_id", nativeID, "cancel_capable", cancelCapable, "status", "uncertain", "attempted_at", float64(c.clock().UnixNano())/1e9)
	state.Set("idle_wake", wakeRec)
	if err := c.writeState(path, state); err != nil {
		return nil, err
	}
	return nativeMap("wake", wakeRec, "process", process), nil
}

func (c *CLI) prepareHermesQueueWake(config, state, agent, process, sref *value.Value, delivery, reqID, path string) (*value.Value, error) {
	hermes, err := c.hermesQueueEndpoint(config, process)
	if err != nil || hermes == "" {
		return nil, err
	}
	nativeID, err := nativeRequired(agent, "native_session_id")
	if err != nil {
		return nil, err
	}
	nativePop(state, "wake_refusal")
	wakeRec := nativeMap("delivery_id", delivery, "request_id", reqID, "session", sref, "transport", wake.TransportHermesQueue, "endpoint", hermes, "native_id", nativeID, "status", "uncertain", "attempted_at", float64(c.clock().UnixNano())/1e9)
	state.Set("idle_wake", wakeRec)
	if err := c.writeState(path, state); err != nil {
		return nil, err
	}
	return nativeMap("wake", wakeRec, "process", process), nil
}

func wrapHerdrErr(err error) error {
	var he *wake.HerdrError
	if errors.As(err, &he) {
		return &CoordinationError{Code: he.Code, Message: he.Message, Cause: he}
	}
	return err
}

func (c *CLI) herdrClient(config *value.Value) (wake.HerdrClient, error) {
	var execPath string
	if iw := config.Get("idle_wakeup"); iw != nil && iw.Kind == value.String {
		var err error
		execPath, err = c.pathText(iw)
		if err != nil {
			return wake.HerdrClient{}, err
		}
	}
	return wake.HerdrClient{
		Executable: execPath,
		ProcRoot:   c.ProcRoot,
		InboxReady: func(ctx context.Context, s wake.SessionRef) (string, error) {
			res, err := c.call(ctx, config, "session-inbox-ready", nativeMap("agent_id", s.AgentID, "execution_id", s.ExecutionID), NativeCallOptions{})
			if err != nil {
				return "", err
			}
			return nativeText(res.Get("delivery_id")), nil
		},
	}, nil
}

func (c *CLI) prepareTerminalWake(ctx context.Context, config, state, agent, process, sref *value.Value, delivery, reqID, path string, cycle *value.Value, errOut io.Writer) (*value.Value, error) {
	client, err := c.herdrClient(config)
	if err != nil {
		return nil, err
	}
	stateDir, err := c.pathText(config.Get("state_dir"))
	if err != nil {
		return nil, err
	}
	req := wake.TerminalWakeRequest{
		Harness:         nativeText(config.Get("harness")),
		NativeSessionID: nativeText(agent.Get("native_session_id")),
		Process:         wakeProcessRef(process),
		Session:         wake.SessionRef{AgentID: nativeText(sref.Get("agent_id")), ExecutionID: nativeText(sref.Get("execution_id"))},
		DeliveryID:      delivery,
		RequestID:       reqID,
		StateDir:        stateDir,
		StatePath:       path,
	}
	prep, refusal, err := client.PrepareTerminalWake(ctx, req, os.Environ())
	if err != nil {
		return nil, wrapHerdrErr(err)
	}
	if refusal != nil {
		return nil, c.noteWakeRefusal(config, state, delivery, refusal.Reason, refusal.Condition, cycle, errOut)
	}
	if prep == nil {
		return nil, nil
	}
	nativePop(state, "wake_refusal")
	targetVal := nativeMap("pane_id", prep.Target.PaneID, "terminal_id", prep.Target.TerminalID, "revision", prep.Target.Revision, "state_change_seq", prep.Target.StateChangeSeq)
	wakeRec := nativeMap("delivery_id", delivery, "request_id", reqID, "session", sref, "target", targetVal, "status", "uncertain", "attempted_at", float64(c.clock().UnixNano())/1e9)
	state.Set("idle_wake", wakeRec)
	if err := c.writeState(path, state); err != nil {
		return nil, err
	}
	return nativeMap("environment", stringArray(prep.Environment...), "wake", wakeRec, "process", process), nil
}

func (c *CLI) submitIdleWake(ctx context.Context, config *value.Value, path string, prepared *value.Value, errOut io.Writer) error {
	wakeRec, err := nativeRequired(prepared, "wake")
	if err != nil {
		return err
	}
	process, err := nativeRequired(prepared, "process")
	if err != nil {
		return err
	}
	if alive, err := c.processAlive(process); err != nil || !alive {
		return err
	}
	sref, err := nativeRequired(wakeRec, "session")
	if err != nil {
		return err
	}
	deliv, err := nativeRequired(wakeRec, "delivery_id")
	if err != nil {
		return err
	}
	text := wake.WakeMessage(wake.SessionRef{AgentID: nativeText(sref.Get("agent_id")), ExecutionID: nativeText(sref.Get("execution_id"))}, nativeText(deliv))
	outcome, err := c.dispatchIdleWakeSubmit(ctx, config, path, prepared, wakeRec, process, text, errOut)
	if err != nil || outcome == nil {
		return err
	}
	return c.recordSubmittedIdleWake(path, prepared, wakeRec, outcome)
}

func (c *CLI) dispatchIdleWakeSubmit(ctx context.Context, config *value.Value, path string, prepared, wakeRec, process *value.Value, text string, errOut io.Writer) (*value.Value, error) {
	switch nativeText(wakeRec.Get("transport")) {
	case wake.TransportCodexQueue:
		return c.submitCodexQueueWake(ctx, config, path, wakeRec, process, text, errOut)
	case wake.TransportClaudeChannel:
		return c.submitClaudeChannelWake(ctx, config, path, wakeRec, text, errOut)
	case wake.TransportOpenCodeQueue:
		return c.submitOpenCodeQueueWake(ctx, config, path, wakeRec, process, text, errOut)
	case wake.TransportHermesQueue:
		return c.submitHermesQueueWake(ctx, config, path, wakeRec, process, text, errOut)
	default:
		return c.submitTerminalWake(ctx, config, prepared, wakeRec, text)
	}
}

func (c *CLI) submitCodexQueueWake(ctx context.Context, config *value.Value, path string, wakeRec, process *value.Value, text string, errOut io.Writer) (*value.Value, error) {
	owner := procpeer.ProcessOwner
	if nativeText(wakeRec.Get("owner")) == "uid" {
		owner = procpeer.UserOwner
	}
	ep := nativeText(wakeRec.Get("endpoint"))
	nid := nativeText(wakeRec.Get("native_id"))
	pref := wakeProcessRef(process)
	if nativeText(wakeRec.Get("status")) == "queued" && value.Truth(wakeRec.Get("queued_submission_id")) {
		qid := nativeText(wakeRec.Get("queued_submission_id"))
		so, err := wake.CodexStartOutcome(ctx, ep, pref, nid, qid, owner)
		if err != nil {
			if wake.IsCodexQueueUnavailable(err) {
				if errOut != nil {
					_, _ = fmt.Fprintf(errOut, "Cairn presence %s: %v\n", nativeText(config.Get("binding")), err)
				}
				so = "unavailable"
			} else {
				return nil, &CoordinationError{Code: "WAKE_UNCERTAIN", Message: err.Error(), Cause: err}
			}
		}
		return nativeMap("queued_id", nil, "started", so == "started", "start_outcome", so), nil
	}
	qid, started, err := wake.CodexEnqueue(ctx, ep, pref, nid, text, nativeText(wakeRec.Get("delivery_id")), owner)
	if err != nil {
		if wake.IsCodexQueueUnavailable(err) {
			if errOut != nil {
				_, _ = fmt.Fprintf(errOut, "Cairn presence %s: %v\n", nativeText(config.Get("binding")), err)
			}
			return nil, c.dropIdleWake(path, wakeRec)
		}
		return nil, &CoordinationError{Code: "WAKE_UNCERTAIN", Message: err.Error(), Cause: err}
	}
	return nativeMap("queued_id", qid, "started", started, "start_outcome", nil), nil
}

func (c *CLI) submitClaudeChannelWake(ctx context.Context, config *value.Value, path string, wakeRec *value.Value, text string, errOut io.Writer) (*value.Value, error) {
	sref := wakeRec.Get("session")
	meta := wake.ClaudeChannelMeta{
		NativeSessionID: nativeText(wakeRec.Get("native_id")),
		AgentID:         nativeText(sref.Get("agent_id")),
		ExecutionID:     nativeText(sref.Get("execution_id")),
		DeliveryID:      nativeText(wakeRec.Get("delivery_id")),
	}
	err := wake.WriteClaudeChannel(ctx, nativeText(wakeRec.Get("endpoint")), wakeProcessRef(wakeRec.Get("bridge")), wakeProcessRef(wakeRec.Get("parent")), text, meta)
	if err != nil {
		if wake.IsClaudeChannelUnavailable(err) {
			if errOut != nil {
				_, _ = fmt.Fprintf(errOut, "Cairn presence %s: %v\n", nativeText(config.Get("binding")), err)
			}
			return nil, c.dropIdleWake(path, wakeRec)
		}
		return nil, &CoordinationError{Code: "WAKE_UNCERTAIN", Message: err.Error(), Cause: err}
	}
	return nativeMap("queued_id", nil, "started", true, "start_outcome", nil), nil
}

func (c *CLI) submitOpenCodeQueueWake(ctx context.Context, config *value.Value, path string, wakeRec, process *value.Value, text string, errOut io.Writer) (*value.Value, error) {
	client := wake.OpenCodeQueueClient{ProcRoot: c.ProcRoot}
	nid := nativeText(wakeRec.Get("native_id"))
	deliv := nativeText(wakeRec.Get("delivery_id"))
	reqID := nativeText(wakeRec.Get("request_id"))
	qid, started, err := client.Enqueue(ctx, nativeText(wakeRec.Get("endpoint")), wakeProcessRef(process), nid, text, deliv, nid, reqID)
	if err == nil {
		return nativeMap("queued_id", qid, "started", started, "start_outcome", nil), nil
	}
	if wake.IsOpenCodeQueueUnsupported(err) || wake.IsOpenCodeQueueUnavailable(err) || wake.IsOpenCodeQueueRefused(err) || wake.IsOpenCodeQueueAlreadyAdmitted(err) {
		if errOut != nil {
			_, _ = fmt.Fprintf(errOut, "Cairn presence %s: %v\n", nativeText(config.Get("binding")), err)
		}
		return nil, c.handleOpenCodeQueueSubmitFailure(path, wakeRec, process, err)
	}
	return nil, &CoordinationError{Code: "WAKE_UNCERTAIN", Message: err.Error(), Cause: err}
}

func (c *CLI) handleOpenCodeQueueSubmitFailure(path string, wakeRec, process *value.Value, qerr error) error {
	if wake.IsOpenCodeQueueUnavailable(qerr) && !wake.IsOpenCodeQueueUnsupported(qerr) {
		return c.dropIdleWake(path, wakeRec)
	}
	lease, err := c.lockState(path)
	if err != nil {
		return err
	}
	defer func() { _ = lease.Close() }()
	state, err := c.read(path, SiteSessionRead)
	if err != nil {
		return err
	}
	if !nativeEqual(state.Get("idle_wake"), wakeRec) {
		return nil
	}
	if wake.IsOpenCodeQueueUnsupported(qerr) {
		nativePop(state, "idle_wake")
		state.Set("opencode_unsupported", nativeMap("pid", process.Get("pid"), "start", process.Get("start"), "boot", process.Get("boot")))
	} else if wake.IsOpenCodeQueueRefused(qerr) {
		state.Get("idle_wake").Set("status", value.Text("refused"))
		state.Get("idle_wake").Set("refusal", value.Text(qerr.Error()))
	} else if wake.IsOpenCodeQueueAlreadyAdmitted(qerr) {
		nativePop(state, "idle_wake")
		state.Set("opencode_replay", nativeMap("delivery_id", wakeRec.Get("delivery_id"), "session", wakeRec.Get("session")))
	}
	return c.writeState(path, state)
}

func (c *CLI) submitHermesQueueWake(ctx context.Context, config *value.Value, path string, wakeRec, process *value.Value, text string, errOut io.Writer) (*value.Value, error) {
	client := wake.HermesQueueClient{ProcRoot: c.ProcRoot}
	nid := nativeText(wakeRec.Get("native_id"))
	deliv := nativeText(wakeRec.Get("delivery_id"))
	reqID := nativeText(wakeRec.Get("request_id"))
	qid, started, err := client.Enqueue(ctx, nativeText(wakeRec.Get("endpoint")), wakeProcessRef(process), nid, text, deliv, nid, reqID, deliv)
	if err != nil {
		if wake.IsHermesQueueUnavailable(err) {
			if errOut != nil {
				_, _ = fmt.Fprintf(errOut, "Cairn presence %s: %v\n", nativeText(config.Get("binding")), err)
			}
			return nil, c.dropIdleWake(path, wakeRec)
		}
		return nil, &CoordinationError{Code: "WAKE_UNCERTAIN", Message: err.Error(), Cause: err}
	}
	return nativeMap("queued_id", qid, "started", started, "start_outcome", nil), nil
}

func (c *CLI) submitTerminalWake(ctx context.Context, config, prepared, wakeRec *value.Value, text string) (*value.Value, error) {
	client, err := c.herdrClient(config)
	if err != nil {
		return nil, err
	}
	var env []string
	if envVal := prepared.Get("environment"); envVal != nil && envVal.Kind == value.Array {
		for _, item := range envVal.Items {
			env = append(env, nativeText(item))
		}
	}
	target := wakeRec.Get("target")
	prep := wake.PreparedTerminalWake{
		Target:      wake.HerdrTarget{PaneID: nativeText(target.Get("pane_id")), TerminalID: nativeText(target.Get("terminal_id"))},
		Environment: env,
	}
	if _, err := client.SubmitTerminalWake(ctx, prep, text); err != nil {
		return nil, wrapHerdrErr(err)
	}
	return nativeMap("queued_id", nil, "started", true, "start_outcome", nil), nil
}

func (c *CLI) recordSubmittedIdleWake(path string, prepared, wakeRec, outcome *value.Value) error {
	lease, err := c.lockState(path)
	if err != nil {
		if IsSessionBusy(err) {
			return nil
		}
		return err
	}
	defer func() { _ = lease.Close() }()
	state, err := c.read(path, SiteSessionRead)
	if err != nil {
		return err
	}
	startOutcome := outcome.Get("start_outcome")
	if !nativeNone(startOutcome) {
		agent, err := nativeRequired(state, "agent")
		if err != nil {
			return err
		}
		sref, err := c.sessionRef(agent)
		if err != nil {
			return err
		}
		proc, err := nativeRequired(state, "process")
		if err != nil {
			return err
		}
		sameProc, err := c.sameProcess(proc, prepared.Get("process"))
		if err != nil {
			return err
		}
		if value.Truth(state.Get("inbox_intent")) || value.Truth(state.Get("inbox_attempt")) || !nativeEqual(sref, wakeRec.Get("session")) || !sameProc {
			return nil
		}
	}
	if nativeEqual(state.Get("idle_wake"), wakeRec) {
		iw := state.Get("idle_wake")
		if value.Truth(outcome.Get("started")) {
			iw.Set("status", value.Text("submitted"))
		} else {
			iw.Set("status", value.Text("queued"))
		}
		if !nativeNone(startOutcome) {
			iw.Set("last_start", startOutcome)
		}
		if nativeText(startOutcome) == "missing" {
			iw.Set("status", value.Text("refused"))
			iw.Set("reconciliation_required", value.Text("queue_item_missing"))
		}
		if qid := outcome.Get("queued_id"); value.Truth(qid) {
			iw.Set("queued_submission_id", qid)
		}
		return c.writeState(path, state)
	}
	return nil
}
