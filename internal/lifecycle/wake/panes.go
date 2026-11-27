package wake

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"slices"
)

// PaneSession identifies a local Cairn session and its native process.
// StateDir and StatePath support Agy conversations sharing a parent process.
type PaneSession struct {
	AgentID, Harness, NativeSessionID string
	Process                           ProcessRef
	StateDir, StatePath               string
}

// PaneMatch is the Herdr state observed for one Cairn session.
type PaneMatch struct {
	AgentID, PaneID, TabID, WorkspaceID, AgentStatus, TerminalTitle string
	Focused                                                         bool
}

// ProcessAlive checks a session process using this client's proc root.
func (c HerdrClient) ProcessAlive(ref ProcessRef) bool {
	return processAliveAt(c.procRoot(), ref)
}

// ObservePanes reads one Herdr agent list and returns the unambiguous matches.
// The command runner may take a short time after cancellation to close its
// pipes. The caller can return while that cleanup completes.
func (c HerdrClient) ObservePanes(ctx context.Context, sessions []PaneSession, baseEnv []string) ([]PaneMatch, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	type result struct {
		matches []PaneMatch
		err     error
	}
	done := make(chan result, 1)
	sessions, baseEnv = slices.Clone(sessions), slices.Clone(baseEnv)
	go func() {
		matches, err := c.observePanes(ctx, sessions, baseEnv)
		done <- result{matches, err}
	}()
	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case observed := <-done:
		return observed.matches, observed.err
	}
}

func (c HerdrClient) observePanes(ctx context.Context, sessions []PaneSession, baseEnv []string) ([]PaneMatch, error) {
	env, err := c.paneEnvironment(ctx, sessions, baseEnv)
	if err != nil {
		return nil, err
	}
	result, err := c.Call(ctx, env, "agent", "list")
	if err != nil {
		return nil, err
	}
	observation := paneObservation{
		client:     c,
		env:        env,
		foreground: make(map[string]map[string]any),
		rollouts:   make(map[ProcessRef]string),
		agyStates:  make(map[string]map[ProcessRef][]agySessionState),
	}
	for _, agent := range result["agents"].([]any) {
		observation.hosts = append(observation.hosts, agent.(map[string]any))
	}
	var matches []PaneMatch
	for _, session := range sessions {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if session.AgentID == "" || session.NativeSessionID == "" || !c.ProcessAlive(session.Process) {
			continue
		}
		host, err := observation.resolve(ctx, session)
		if err != nil {
			return nil, err
		}
		if host != nil {
			matches = append(matches, paneMatch(session.AgentID, host))
		}
	}
	return matches, ctx.Err()
}

func (c HerdrClient) paneEnvironment(ctx context.Context, sessions []PaneSession, baseEnv []string) ([]string, error) {
	tried := make(map[ProcessRef]bool)
	for _, session := range sessions {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if tried[session.Process] || !c.ProcessAlive(session.Process) {
			continue
		}
		tried[session.Process] = true
		env, ok, err := c.HerdrEnvironment(session.Process, baseEnv)
		if err == nil && ok && c.ProcessAlive(session.Process) {
			return env, nil
		}
	}
	return nil, &HerdrError{Code: "HOST_UNAVAILABLE", Message: "no live session has a Herdr socket"}
}

type paneObservation struct {
	client     HerdrClient
	env        []string
	hosts      []map[string]any
	foreground map[string]map[string]any
	rollouts   map[ProcessRef]string
	agyStates  map[string]map[ProcessRef][]agySessionState
}

func (o *paneObservation) resolve(ctx context.Context, session PaneSession) (map[string]any, error) {
	var matched map[string]any
	for _, host := range o.hosts {
		explicit, rollout := herdrSessionIdentity(session.Harness, session.NativeSessionID, host)
		if rollout {
			var err error
			explicit, err = o.matchCodexPane(ctx, session, host["pane_id"].(string))
			if err != nil {
				return nil, err
			}
		}
		if explicit {
			if matched != nil && matched["pane_id"] != host["pane_id"] {
				return nil, nil
			}
			matched = host
		}
	}
	if matched == nil && session.Harness == "agy" && session.StateDir != "" {
		return o.agyParentPane(ctx, session)
	}
	return matched, nil
}

func (o *paneObservation) matchCodexPane(ctx context.Context, session PaneSession, pane string) (bool, error) {
	process := session.Process
	nativeID, ok := o.rollouts[process]
	if !ok {
		var err error
		nativeID, err = o.client.codexRolloutSession(process)
		if errors.Is(err, os.ErrNotExist) && !o.client.ProcessAlive(process) {
			return false, nil
		}
		if err != nil {
			return false, err
		}
		o.rollouts[process] = nativeID
	}
	if nativeID != session.NativeSessionID {
		return false, nil
	}
	info, ok := o.foreground[pane]
	if !ok {
		var err error
		info, err = o.client.herdrForeground(ctx, o.env, pane)
		if err != nil {
			var hostErr *HerdrError
			if !errors.As(err, &hostErr) || hostErr.Code != "HOST_TARGET_GONE" {
				return false, err
			}
		}
		// A nil entry records a pane that closed after the agent list.
		o.foreground[pane] = info
	}
	if info == nil {
		return false, nil
	}
	match, err := o.client.foregroundIdentifiesSession(info, process)
	if errors.Is(err, os.ErrNotExist) && !o.client.ProcessAlive(process) {
		return false, nil
	}
	return match, err
}

func (o *paneObservation) agyParentPane(ctx context.Context, session PaneSession) (map[string]any, error) {
	directory := filepath.Clean(session.StateDir)
	states, ok := o.agyStates[directory]
	if !ok {
		states = make(map[ProcessRef][]agySessionState)
		err := visitAgySessionStates(ctx, directory, "", func(state agySessionState) (bool, error) {
			states[state.process] = append(states[state.process], state)
			return false, nil
		})
		if err != nil {
			return nil, err
		}
		o.agyStates[directory] = states
	}
	var matched map[string]any
	for _, state := range states[session.Process] {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if !state.parentOf(session.StatePath, session.NativeSessionID, session.Process) {
			continue
		}
		for _, host := range o.hosts {
			if explicit, _ := herdrSessionIdentity(session.Harness, state.nativeID, host); explicit {
				if matched != nil && matched["pane_id"] != host["pane_id"] {
					return nil, nil
				}
				matched = host
			}
		}
	}
	return matched, nil
}

func paneMatch(agentID string, host map[string]any) PaneMatch {
	pane := PaneMatch{AgentID: agentID, PaneID: host["pane_id"].(string)}
	pane.TabID, _ = host["tab_id"].(string)
	pane.WorkspaceID, _ = host["workspace_id"].(string)
	pane.AgentStatus, _ = host["agent_status"].(string)
	pane.Focused, _ = host["focused"].(bool)
	var stripped bool
	pane.TerminalTitle, stripped = host["terminal_title_stripped"].(string)
	if !stripped {
		pane.TerminalTitle, _ = host["terminal_title"].(string)
	}
	return pane
}
