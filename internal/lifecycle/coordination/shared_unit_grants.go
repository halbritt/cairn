package coordination

import (
	"errors"
	"fmt"
	"path/filepath"

	"github.com/halbritt/cairn/internal/lifecycle/ledger"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

const retainedGrantRefusal = "D1 retained positive or unknown activation grant needs owning-session disposition"

type watchDrainGrant struct {
	directory, path, turn string
	ledger                *value.Value
}

func (r *watchDrainRead) collectGrantFacts(directory, path string, ledger *value.Value) error {
	decisions := ledger.Get("inbox_activation")
	if decisions == nil {
		return nil
	}
	if decisions.Kind != value.Object {
		return errors.New("D1 activation ledger is unknown")
	}
	for _, decision := range decisions.Members {
		if decision.Value == nil || decision.Value.Kind != value.Bool {
			return errors.New(retainedGrantRefusal)
		}
		if decision.Value.Boolean {
			r.grants = append(r.grants, watchDrainGrant{directory: directory, path: path, turn: string(decision.Key), ledger: ledger})
		}
	}
	return nil
}

func (r *watchDrainRead) grantFacts() error {
	for _, grant := range r.grants {
		if err := r.settledGrant(grant); err != nil {
			return fmt.Errorf("%s: %w", retainedGrantRefusal, err)
		}
	}
	return nil
}

func (r *watchDrainRead) settledGrant(grant watchDrainGrant) error {
	var owner *watchDrainSession
	for i := range r.sessions {
		session := &r.sessions[i]
		memory := r.memories[session.config]
		if !value.SameText(memory.Get("state_dir"), grant.directory) {
			continue
		}
		native := session.state.Get("native_session_id")
		identity := grant.ledger.Get("session_id")
		if value.SameText(session.config.Get("harness"), "claude") {
			identity = grant.ledger.Get("session")
		}
		if !canonicalUUIDValue(native) || !nativeEqual(identity, native) {
			continue
		}
		if recorded := session.state.Get("agent").Get("native_session_id"); !nativeNone(recorded) && !nativeEqual(recorded, native) {
			return errors.New("owning native session correspondence is unknown")
		}
		if owner != nil {
			return errors.New("owning native session is ambiguous")
		}
		owner = session
	}
	if owner == nil {
		return errors.New("owning native session is unknown")
	}
	native := nativeText(owner.state.Get("native_session_id"))
	harness := nativeText(owner.config.Get("harness"))
	var checked *value.Value
	var err error
	switch harness {
	case "codex":
		if grant.path != filepath.Join(grant.directory, native+".memory-budget.json") {
			return errors.New("owning Codex ledger path is unknown")
		}
		checked, err = ledger.CodexBudget(grant.path, native, 9500)
	case "claude":
		if grant.path != filepath.Join(grant.directory, native+".inbox-recall.json") {
			return errors.New("owning Claude ledger path is unknown")
		}
		checked, err = ledger.InboxGrants(grant.path, native)
	default:
		return errors.New("owning harness turn visibility is unknown")
	}
	if err != nil || !nativeEqual(checked, grant.ledger) {
		return errors.Join(err, errors.New("owning grant ledger is malformed or changed"))
	}
	return endedGrantTurn(harness, grant.turn, checked, owner.state, r.settled[owner.state])
}

// remote is admitted only after the existing historical D2-D4 verifier has
// checked the exact session, attempt, delivery, close, completion and reply.
func endedGrantTurn(harness, turn string, grants, state, remote *value.Value) error {
	if turn == "" || remote == nil || remote.Kind != value.Object ||
		!value.SameText(remote.Get("native_turn_id"), turn) ||
		!nativeEqual(state.Get("inbox_attempt").Get("attempt_id"), remote.Get("attempt_id")) {
		return errors.New("exact owning turn/settled attempt correspondence is unknown")
	}
	if active := state.Get("active_prompt"); value.Truth(active) {
		return errors.New("owning session still has an active prompt")
	}
	ended := value.SameText(remote.Get("turn_stop_state"), "ended") || value.SameText(remote.Get("turn_stop_state"), "interrupted")
	switch harness {
	case "codex":
		active := grants.Get("active_turn_id")
		if value.SameText(active, turn) {
			return errors.New("owning Codex turn is still current")
		}
		if active != nil && active.Kind != value.Null {
			if !canonicalUUIDValue(active) || !canonicalUUIDValue(value.Text(turn)) ||
				grants.Get("turns").Get(turn) == nil || grants.Get("turns").Get(nativeText(active)) == nil {
				return errors.New("Codex retired-turn membership is unknown")
			}
			ended = true
		}
	case "claude":
		// No insertion order, later false decision, or absence of active_prompt
		// proves a Claude turn ended. Its exact authoritative stop must do so.
	default:
		return errors.New("owning harness turn visibility is unknown")
	}
	if !ended {
		return errors.New("owning native turn end is unknown")
	}
	return nil
}
