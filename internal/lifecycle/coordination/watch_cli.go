package coordination

import (
	"context"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"syscall"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

const (
	watchIntervalSeconds      = 30.0
	cancelPollIntervalSeconds = 2.0
)

func (c *CLI) WatchCommand(ctx context.Context, a cliArgs, errOut io.Writer) (int, error) {
	if c.profile() == nil || c.Host == nil {
		return 0, &value.ProfileUnavailable{}
	}
	configDir, err := c.pathText(value.Text(a.get("--config-dir")))
	if err != nil {
		return 0, err
	}
	if c.Compatibility != nil {
		initialMatches, _ := filepath.Glob(filepath.Join(configDir, "*.json"))
		sort.Strings(initialMatches)
		if err := c.Compatibility.VerifyWatchConfigDir(c.profile(), initialMatches); err != nil {
			return 0, err
		}
	}
	stopCh := make(chan struct{})
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGTERM, syscall.SIGINT)
	defer signal.Stop(sigCh)
	doneCh := make(chan struct{})
	defer close(doneCh)
	go func() {
		select {
		case <-sigCh:
			close(stopCh)
		case <-ctx.Done():
			close(stopCh)
		case <-doneCh:
		}
	}()
	lease, err := c.lock(filepath.Join(configDir, ".watch"))
	if err != nil {
		return 0, err
	}
	defer func() { _ = lease.Close() }()
	for !watchStopped(stopCh) {
		started := c.monotonicSeconds()
		matches, _ := filepath.Glob(filepath.Join(configDir, "*.json"))
		sort.Strings(matches)
		if c.Compatibility != nil {
			if err := c.Compatibility.VerifyWatchConfigDir(c.profile(), matches); err != nil {
				return 0, err
			}
		}
		var configs []*value.Value
		for _, p := range matches {
			cfg, err := c.loadConfig(p)
			if err != nil {
				return 0, err
			}
			configs = append(configs, cfg)
			if err := c.WatchOnce(ctx, cfg, errOut); err != nil {
				return 0, err
			}
		}
		if a.has("--once") {
			break
		}
		rem := math.Max(1.0, watchIntervalSeconds-(c.monotonicSeconds()-started))
		if err := c.waitForNextCycle(ctx, stopCh, configs, rem, errOut); err != nil {
			return 0, err
		}
	}
	return 0, nil
}

func watchStopped(stopCh <-chan struct{}) bool {
	select {
	case <-stopCh:
		return true
	default:
		return false
	}
}

func (c *CLI) watchInbox(ctx context.Context, config, state *value.Value, path string) (*value.Value, error) {
	ns := c.newNativeSession(config, state, path)
	if value.Truth(state.Get("organic_claim_intent")) || value.Truth(state.Get("organic_advance_intent")) {
		if _, err := ns.Recover(ctx); err != nil {
			return nil, err
		}
	}
	if !value.Truth(state.Get("inbox_intent")) {
		if ns.Host == nil || ns.Host.Journal == nil {
			return nil, &value.ProfileUnavailable{}
		}
		if _, err := ns.Host.Journal.Flush(ctx, config, state, FlushAttempt); err != nil {
			return nil, err
		}
		return nil, c.writeState(path, state)
	}
	if nativeText(config.Get("harness")) == "opencode" {
		handled, cancelPrepared, err := c.advanceOpenCodeCancel(ctx, ns, config, state, path)
		if err != nil || handled {
			return cancelPrepared, err
		}
	}
	if nativeText(config.Get("harness")) == "codex" && c.engine() != nil {
		settled, err := c.engine().AutoSettleCodexRefusedWake(ctx, ns)
		if err != nil || settled {
			return nil, err
		}
	}
	if ns.Host == nil || ns.Host.Journal == nil {
		return nil, &value.ProfileUnavailable{}
	}
	flushed, err := ns.Host.Journal.Flush(ctx, config, state, FlushAttempt)
	if err != nil || !flushed {
		return nil, err
	}
	settled, err := ns.SettleInbox(ctx)
	if err != nil || settled {
		return nil, err
	}
	if value.Truth(state.Get("inbox_turn_ended")) {
		return nil, nil
	}
	attempt, err := nativeRequired(state, "inbox_attempt")
	if err != nil {
		return nil, err
	}
	_, err = ns.Renew(ctx, attempt)
	return nil, err
}

func (c *CLI) refreshInboxPending(ctx context.Context, config, state *value.Value) error {
	agent, err := nativeRequired(state, "agent")
	if err != nil {
		return err
	}
	meta, err := nativeRequired(agent, "metadata")
	if err != nil {
		return err
	}
	if _, err := nativeObj(meta, "metadata"); err != nil {
		return err
	}
	if !value.Truth(config.Get("native_delivery")) || nativeText(meta.Get("delivery_mode")) != "existing-session" || nativeText(meta.Get("state")) != "busy" {
		nativePop(state, "inbox_pending")
		return nil
	}
	sref, err := c.sessionRef(agent)
	if err != nil {
		return err
	}
	counts, err := c.call(ctx, config, "session-inbox-pending", sref, NativeCallOptions{Timeout: 1 * time.Second})
	if err != nil {
		if isCoordErr(err) {
			nativePop(state, "inbox_pending")
			return nil
		}
		return err
	}
	rec := inboxPendingRecord(counts, agent, c.clock())
	if rec == nil {
		nativePop(state, "inbox_pending")
		return nil
	}
	state.Set("inbox_pending", rec)
	return nil
}

func (c *CLI) WatchOnce(ctx context.Context, config *value.Value, errOut io.Writer) error {
	sd, err := nativeRequired(config, "state_dir")
	if err != nil {
		return err
	}
	stateDir, err := c.pathText(sd)
	if err != nil {
		return err
	}
	matches, _ := filepath.Glob(filepath.Join(stateDir, "*.json"))
	sort.Strings(matches)
	for _, path := range matches {
		if err := c.watchSessionOnce(ctx, config, path, errOut); err != nil {
			if err := c.handleWatchSessionError(config, err, errOut); err != nil {
				return err
			}
		}
	}
	return nil
}

func (c *CLI) handleWatchSessionError(config *value.Value, err error, errOut io.Writer) error {
	if IsSessionBusy(err) {
		return nil
	}
	var ce *CoordinationError
	if errors.As(err, &ce) {
		if _, gerr := nativeRequired(config, "binding"); gerr != nil {
			return gerr
		}
		if errOut != nil {
			_, _ = fmt.Fprintf(errOut, "Cairn presence %s: %v\n", nativeText(config.Get("binding")), ce)
		}
		return nil
	}
	if cliCaught(err) || sourceOSError(err) || isOSPathErr(err) {
		if _, gerr := nativeRequired(config, "binding"); gerr != nil {
			return gerr
		}
		name := sourceErrorName(err)
		if (sourceOSError(err) || isOSPathErr(err)) && !sourceClass(err, "OSError") {
			name = "OSError"
		}
		if errOut != nil {
			_, _ = fmt.Fprintf(errOut, "Cairn presence %s: %s; presence may expire\n", nativeText(config.Get("binding")), name)
		}
		return nil
	}
	return err
}

func (c *CLI) watchSessionOnce(ctx context.Context, config *value.Value, path string, errOut io.Writer) error {
	prepared, cancelPrepared, err := c.watchSessionLocked(ctx, config, path, errOut)
	if err != nil {
		return err
	}
	if value.Truth(prepared) {
		if err := c.submitIdleWake(ctx, config, path, prepared, errOut); err != nil {
			return err
		}
	}
	if value.Truth(cancelPrepared) {
		if err := c.submitOpenCodeCancel(ctx, config, path, cancelPrepared, errOut); err != nil {
			return err
		}
	}
	return nil
}

func (c *CLI) watchSessionLocked(ctx context.Context, config *value.Value, path string, errOut io.Writer) (*value.Value, *value.Value, error) {
	lease, err := c.lockState(path)
	if err != nil {
		return nil, nil, err
	}
	defer func() { _ = lease.Close() }()
	state, err := c.read(path, SiteSessionRead)
	if err != nil {
		return nil, nil, err
	}
	if state == nil || state.Kind != value.Object {
		return nil, nil, &journal.NonObjectError{Field: "state"}
	}
	if value.Truth(state.Get("retired")) {
		return nil, nil, nil
	}
	ns := c.newNativeSession(config, state, path)
	var prepared, cancelPrepared *value.Value
	if value.Truth(state.Get("ending")) {
		if err := ns.Retire(ctx, "process_exited", 4*time.Second); err != nil {
			return nil, nil, err
		}
	} else {
		proc, err := nativeRequired(state, "process")
		if err != nil {
			return nil, nil, err
		}
		alive, err := c.processAlive(proc)
		if err != nil {
			return nil, nil, err
		}
		if alive {
			var finished *value.Value
			if value.Truth(state.Get("agent")) {
				finished, err = c.agySubagentFinished(config, state, path)
				if err != nil {
					return nil, nil, err
				}
			}
			if !value.Truth(state.Get("agent")) {
				reg, err := nativeRequired(state, "registration")
				if err != nil {
					return nil, nil, err
				}
				agent, err := c.call(ctx, config, "agent-register", reg, NativeCallOptions{})
				if err != nil {
					return nil, nil, err
				}
				state.Set("agent", agent)
			} else if value.Truth(finished) {
				parent, err := nativeRequired(finished, "parent")
				if err != nil {
					return nil, nil, err
				}
				nativePop(finished, "parent")
				ev := nativeClone(finished)
				ev.Set("parent_display_name", parent.Get("display_name"))
				state.Set("ending_evidence", ev)
				if err := ns.Retire(ctx, "agy_subagent_finished", 4*time.Second); err != nil {
					return nil, nil, err
				}
			} else {
				prepared, cancelPrepared, err = c.watchLiveAgentSession(ctx, config, state, path, errOut)
				if err != nil {
					return nil, nil, err
				}
			}
		} else if !value.Truth(state.Get("agent")) {
			state.Set("retired", value.Boolean(true))
		} else if err := ns.Retire(ctx, "process_exited", 4*time.Second); err != nil {
			return nil, nil, err
		}
	}
	return prepared, cancelPrepared, c.writeState(path, state)
}

func (c *CLI) watchLiveAgentSession(ctx context.Context, config, state *value.Value, path string, errOut io.Writer) (*value.Value, *value.Value, error) {
	agent, err := nativeRequired(state, "agent")
	if err != nil {
		return nil, nil, err
	}
	sref, err := c.sessionRef(agent)
	if err != nil {
		return nil, nil, err
	}
	hb, err := c.call(ctx, config, "agent-heartbeat", sref, NativeCallOptions{})
	if err != nil {
		return nil, nil, err
	}
	state.Set("agent", hb)
	cancelPrepared, err := c.watchInbox(ctx, config, state, path)
	if err != nil {
		return nil, nil, err
	}
	if err := c.refreshInboxPending(ctx, config, state); err != nil {
		return nil, nil, err
	}
	cycle := nativeMap()
	prepared, err := c.prepareIdleWake(ctx, config, state, path, cycle, errOut)
	if err != nil {
		return nil, nil, err
	}
	if err := c.reportDeliveryObservation(ctx, config, state, path, prepared, cycle, errOut); err != nil {
		return nil, nil, err
	}
	return prepared, cancelPrepared, nil
}

func opencodeCancelPending(config, state *value.Value) (bool, error) {
	if _, err := nativeObj(config, "config"); err != nil {
		return false, err
	}
	if _, err := nativeObj(state, "state"); err != nil {
		return false, err
	}
	attempt := state.Get("inbox_attempt")
	if nativeText(config.Get("harness")) != "opencode" || !value.Truth(state.Get("inbox_intent")) || attempt == nil || attempt.Kind != value.Object {
		return false, nil
	}
	cancel := attempt.Get("cancel")
	if cancel == nil || cancel.Kind != value.Object {
		return false, nil
	}
	return !value.Truth(cancel.Get("confirmed_at")), nil
}

func (c *CLI) pendingCancelConfigs(configs []*value.Value) ([]*value.Value, error) {
	var pending []*value.Value
	for _, config := range configs {
		sd, err := nativeRequired(config, "state_dir")
		if err != nil {
			return nil, err
		}
		stateDir, err := c.pathText(sd)
		if err != nil {
			return nil, err
		}
		matches, _ := filepath.Glob(filepath.Join(stateDir, "*.json"))
		sort.Strings(matches)
		for _, path := range matches {
			state, err := c.readJSONObject(path)
			if err != nil {
				return nil, err
			}
			if state == nil || value.Truth(state.Get("retired")) || value.Truth(state.Get("ending")) {
				continue
			}
			if ok, err := opencodeCancelPending(config, state); err != nil {
				return nil, err
			} else if ok {
				pending = append(pending, config)
				break
			}
		}
	}
	return pending, nil
}

func (c *CLI) watchPendingCancels(ctx context.Context, config *value.Value, errOut io.Writer) (bool, error) {
	sd, err := nativeRequired(config, "state_dir")
	if err != nil {
		return false, err
	}
	stateDir, err := c.pathText(sd)
	if err != nil {
		return false, err
	}
	matches, _ := filepath.Glob(filepath.Join(stateDir, "*.json"))
	sort.Strings(matches)
	pending := false
	failed := false
	for _, path := range matches {
		pathPending, err := c.watchPendingCancelPath(ctx, config, path, errOut)
		if err != nil {
			if IsSessionBusy(err) {
				pending = true
				continue
			}
			failed = true
			if herr := c.handleWatchSessionError(config, err, errOut); herr != nil {
				return false, herr
			}
			continue
		}
		if pathPending {
			pending = true
		}
	}
	return pending && !failed, nil
}

func (c *CLI) watchPendingCancelPath(ctx context.Context, config *value.Value, path string, errOut io.Writer) (bool, error) {
	hint, err := c.readJSONObject(path)
	if err != nil || hint == nil || value.Truth(hint.Get("retired")) || value.Truth(hint.Get("ending")) {
		return false, err
	}
	if ok, err := opencodeCancelPending(config, hint); err != nil || !ok {
		return false, err
	}
	cancelPrepared, pathPending, err := c.watchPendingCancelLocked(ctx, config, path)
	if err != nil {
		return false, err
	}
	if value.Truth(cancelPrepared) {
		if err := c.submitOpenCodeCancel(ctx, config, path, cancelPrepared, errOut); err != nil {
			return false, err
		}
	}
	return pathPending, nil
}

func (c *CLI) watchPendingCancelLocked(ctx context.Context, config *value.Value, path string) (*value.Value, bool, error) {
	lease, err := c.lockState(path)
	if err != nil {
		return nil, false, err
	}
	defer func() { _ = lease.Close() }()
	state, err := c.readJSONObject(path)
	if err != nil || state == nil || value.Truth(state.Get("retired")) || value.Truth(state.Get("ending")) || !value.Truth(state.Get("agent")) {
		return nil, false, err
	}
	if ok, err := opencodeCancelPending(config, state); err != nil || !ok {
		return nil, false, err
	}
	proc, err := nativeRequired(state, "process")
	if err != nil {
		return nil, false, err
	}
	if alive, err := c.processAlive(proc); err != nil || !alive {
		return nil, false, err
	}
	ns := c.newNativeSession(config, state, path)
	_, cancelPrepared, err := c.advanceOpenCodeCancel(ctx, ns, config, state, path)
	if err != nil {
		return nil, false, err
	}
	if err := c.writeState(path, state); err != nil {
		return nil, false, err
	}
	stillPending, err := opencodeCancelPending(config, state)
	return cancelPrepared, stillPending, err
}

func (c *CLI) waitForNextCycle(ctx context.Context, stopCh <-chan struct{}, configs []*value.Value, seconds float64, errOut io.Writer) error {
	deadline := c.monotonicSeconds() + seconds
	active, err := c.pendingCancelConfigs(configs)
	if err != nil {
		return err
	}
	for len(active) > 0 && !watchStopped(stopCh) {
		rem := deadline - c.monotonicSeconds()
		if rem <= 0 {
			return nil
		}
		if watchWait(stopCh, math.Min(cancelPollIntervalSeconds, rem)) {
			return nil
		}
		var stillPending []*value.Value
		for _, config := range active {
			if watchStopped(stopCh) || c.monotonicSeconds() >= deadline {
				return nil
			}
			ok, err := c.watchPendingCancels(ctx, config, errOut)
			if err != nil {
				return err
			}
			if ok {
				stillPending = append(stillPending, config)
			}
		}
		active = stillPending
	}
	if !watchStopped(stopCh) {
		watchWait(stopCh, math.Max(0, deadline-c.monotonicSeconds()))
	}
	return nil
}

func watchWait(stopCh <-chan struct{}, seconds float64) bool {
	if seconds <= 0 {
		return watchStopped(stopCh)
	}
	timer := time.NewTimer(time.Duration(seconds * float64(time.Second)))
	defer timer.Stop()
	select {
	case <-stopCh:
		return true
	case <-timer.C:
		return false
	}
}
