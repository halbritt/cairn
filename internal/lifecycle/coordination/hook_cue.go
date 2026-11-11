package coordination

import (
	"context"
	"errors"
	"path/filepath"
	"strconv"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

const InboxCueLookupTimeout = 500 * time.Millisecond

func inboxPendingRecord(counts, agent *value.Value, now time.Time) *value.Value {
	if counts == nil || counts.Kind != value.Object || agent == nil || agent.Kind != value.Object {
		return nil
	}
	for _, key := range []string{"requests", "notices", "responses"} {
		if _, ok := nonnegativeInt(counts.Get(key)); !ok {
			return nil
		}
	}
	latestPos := value.IntegerText("0")
	if lp := counts.Get("latest_position"); lp != nil {
		if _, ok := nonnegativeInt(lp); !ok {
			return nil
		}
		latestPos = lp
	}
	truncated := false
	if tr := counts.Get("truncated"); tr != nil {
		if tr.Kind != value.Bool {
			return nil
		}
		truncated = tr.Boolean
	}
	nowFloat := float64(now.Unix()) + float64(now.Nanosecond())/1e9
	return nativeMap(
		"requests", counts.Get("requests"),
		"notices", counts.Get("notices"),
		"responses", counts.Get("responses"),
		"truncated", value.Boolean(truncated),
		"latest_position", latestPos,
		"execution_id", agent.Get("execution_id"),
		"observed_at", nowFloat,
	)
}

func inboxCueSignature(pending *value.Value) *value.Value {
	return nativeList(
		pending.Get("execution_id"),
		pending.Get("latest_position"),
		pending.Get("requests"),
		pending.Get("notices"),
		pending.Get("responses"),
	)
}

func cueArrival(previous, signature *value.Value) bool {
	if signature == nil || signature.Kind != value.Array || len(signature.Items) != 5 {
		return false
	}
	var sigVals [4]int64
	for i := 1; i < 5; i++ {
		it := signature.Items[i]
		if it == nil || it.Kind != value.Int {
			return false
		}
		n, err := strconv.ParseInt(it.Integer, 10, 64)
		if err != nil {
			return false
		}
		sigVals[i-1] = n
	}
	if sigVals[1] == 0 && sigVals[2] == 0 && sigVals[3] == 0 {
		return false
	}
	if previous == nil || previous.Kind != value.Array || len(previous.Items) != 5 || !nativeEqual(previous.Items[0], signature.Items[0]) {
		return true
	}
	var prevVals [4]int64
	for i := 1; i < 5; i++ {
		it := previous.Items[i]
		if it == nil || it.Kind != value.Int {
			return true
		}
		n, err := strconv.ParseInt(it.Integer, 10, 64)
		if err != nil {
			return true
		}
		prevVals[i-1] = n
	}
	return sigVals[0] > prevVals[0] || (sigVals[1]+sigVals[2]+sigVals[3]) > (prevVals[1]+prevVals[2]+prevVals[3])
}

func (c *CLI) cueSession(path string, process *value.Value) (*value.Value, *value.Value, bool) {
	state, err := c.read(path, SiteStateRead)
	if err != nil || state == nil || state.Kind != value.Object || value.Truth(state.Get("retired")) || value.Truth(state.Get("ending")) || !value.Truth(state.Get("process")) {
		return nil, nil, false
	}
	same, err := c.sameProcess(process, state.Get("process"))
	if err != nil || !same {
		return nil, nil, false
	}
	agent := state.Get("agent")
	if agent == nil || agent.Kind != value.Object {
		return nil, nil, false
	}
	for _, k := range []string{"agent_id", "execution_id"} {
		v := agent.Get(k)
		if v == nil || v.Kind != value.String || len(v.String) == 0 {
			return nil, nil, false
		}
	}
	meta := agent.Get("metadata")
	if meta == nil || meta.Kind != value.Object || !value.SameText(meta.Get("delivery_mode"), "existing-session") || !value.SameText(meta.Get("state"), "busy") {
		return nil, nil, false
	}
	return state, agent, true
}

func (c *CLI) coordinationExcluded(workspace string) (bool, error) {
	resolved, err := resolveSourcePath(c.profile(), workspace)
	if err != nil {
		return false, err
	}
	cur := resolved
	for {
		for _, marker := range []string{".cairn-no-coordination", ".cairn-no-memory"} {
			exists, err := sourceExists(sourceJoin(cur, marker), false)
			if err != nil {
				return false, err
			}
			if exists {
				return true, nil
			}
		}
		parent := filepath.Dir(cur)
		if parent == cur {
			break
		}
		cur = parent
	}
	return false, nil
}

func (c *CLI) workspacesExcluded(workspaces []*value.Value) bool {
	for _, ws := range workspaces {
		if value.Truth(ws) {
			wsPath, err := sourcePathText(c.profile(), ws)
			if err != nil {
				return true
			}
			excluded, err := c.coordinationExcluded(wsPath)
			if err != nil || excluded {
				return true
			}
		}
	}
	return false
}

func (c *CLI) toolCue(ctx context.Context, config, event *value.Value, hookConfigPath string) (*value.Value, error) {
	if event == nil || event.Kind != value.Object {
		return value.Mapping(), nil
	}
	native := nativeText(event.Get("session_id"))
	if !value.Truth(config.Get("native_delivery")) || !validNativeSessionID(native) {
		return value.Mapping(), nil
	}
	res, err := c.toolCueInner(ctx, config, event, native, hookConfigPath)
	if err != nil {
		var preflight *CompatibilityPreflightError
		var profile *value.ProfileUnavailable
		if errors.As(err, &preflight) || errors.As(err, &profile) {
			return nil, err
		}
		return value.Mapping(), nil
	}
	return res, nil
}

func (c *CLI) toolCueInner(ctx context.Context, config, event *value.Value, native, hookConfigPath string) (*value.Value, error) {
	path, err := c.locate(config, native)
	if err != nil {
		return value.Mapping(), nil
	}
	process, err := c.OwnerProcess(config, event)
	if err != nil {
		return value.Mapping(), nil
	}
	state, agent, ok := c.cueSession(path, process)
	if !ok {
		return value.Mapping(), nil
	}
	if c.workspacesExcluded([]*value.Value{event.Get("cwd"), state.Get("workspace"), agent.Get("metadata").Get("workspace")}) {
		return value.Mapping(), nil
	}
	lease, owned, err := TryAcquireCueLock(path)
	if err != nil || !owned {
		return value.Mapping(), nil
	}
	defer func() { _ = lease.Release() }()
	ref, err := c.sessionRef(agent)
	if err != nil {
		return value.Mapping(), nil
	}
	counts, err := c.call(ctx, config, "session-inbox-pending", ref, NativeCallOptions{Timeout: InboxCueLookupTimeout})
	if err != nil {
		return value.Mapping(), nil
	}
	pending := inboxPendingRecord(counts, agent, c.clock())
	if pending == nil {
		return value.Mapping(), nil
	}
	currentState, currentAgent, ok := c.cueSession(path, process)
	if !ok {
		return value.Mapping(), nil
	}
	curRef, err := c.sessionRef(currentAgent)
	if err != nil || !nativeEqual(curRef, ref) {
		return value.Mapping(), nil
	}
	if c.workspacesExcluded([]*value.Value{event.Get("cwd"), currentState.Get("workspace"), currentAgent.Get("metadata").Get("workspace")}) {
		return value.Mapping(), nil
	}
	signature := inboxCueSignature(pending)
	marker := stateSibling(path, ".cue")
	var previous *value.Value
	if mObj, err := c.read(marker, SiteCueRead); err == nil && mObj != nil && mObj.Kind == value.Object {
		previous = mObj.Get("signature")
	}
	arrival := cueArrival(previous, signature)
	if arrival || (previous != nil && !nativeEqual(signature, previous)) {
		if err := c.writeState(marker, nativeMap("signature", signature)); err != nil {
			return value.Mapping(), nil
		}
	}
	if !arrival {
		return value.Mapping(), nil
	}
	cueText, err := c.InboxCueText(pending, config, currentAgent, hookConfigPath)
	if err != nil {
		return nil, err
	}
	return nativeMap("hookSpecificOutput", nativeMap("hookEventName", "PostToolUse", "additionalContext", cueText)), nil
}
