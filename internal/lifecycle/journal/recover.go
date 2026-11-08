package journal

import (
	"context"
	"errors"
	"path/filepath"
	"slices"
	"sort"
)

// Busy distinguishes an in-flight explicit command from an empty pending set.
var Busy = errors.New("journal command is active")

type SlotDiagnostic struct {
	Slot, Kind, Status, Code, Target string
}

type Recovery struct {
	Pending  []string
	Outcomes []SlotDiagnostic
}

func (r Recovery) Uncertain() bool {
	for _, name := range r.Pending {
		if name != "reply_possible" && name != "unused" {
			return true
		}
	}
	return false
}

// Recover never replays a final slot except to retrieve an omitted exact result
// for a committed finish_reply with no response. Busy is a nonblocking sentinel.
func Recover(ctx context.Context, stateDir, attemptID string, options Options) (Recovery, error) {
	result := Recovery{}
	target := path(stateDir, attemptID)
	if !exists(target) {
		return Recovery{Pending: []string{"unused"}}, nil
	}
	locked, err := lock(ctx, target, 0)
	var public *Error
	if errors.As(err, &public) && public.Code == "SESSION_BUSY" {
		return result, Busy
	}
	if err != nil {
		return result, err
	}
	defer locked.Close()
	v, err := load(target)
	if err != nil {
		return result, err
	}
	journal, err := object(v, "journal")
	if err != nil {
		return result, err
	}
	pending := map[string]bool{}
	for _, name := range []string{"completion", "response"} {
		if name == "response" && truth(journal["finish_reply"]) && !truth(journal["response"]) {
			completion, err := orObject(journal["completion"], "completion")
			if err != nil {
				return result, err
			}
			if text(completion["status"]) == "committed" && finishRef(completion) == nil {
				if _, err := send(ctx, completion, recoverTimeout, options); err != nil {
					return result, err
				}
				if err := write(target, journal, options); err != nil {
					return result, err
				}
			}
			if _, err := materialize(journal, target, options); err != nil {
				return result, err
			}
		}
		if !truth(journal[name]) {
			continue
		}
		slot, err := indexedObject(journal[name], name)
		if err != nil {
			return result, err
		}
		status, err := required(slot, "status")
		if err != nil {
			return result, err
		}
		if status != "pending" {
			continue
		}
		if name == "response" && pending["completion"] {
			pending[name] = true
			continue
		}
		if _, err := send(ctx, slot, recoverTimeout, options); err != nil {
			return result, err
		}
		if err := write(target, journal, options); err != nil {
			return result, err
		}
		if text(slot["status"]) == "pending" {
			pending[name] = true
		}
		if status := text(slot["status"]); status == "pending" || status == "refused" {
			kind, err := required(slot, "kind")
			if err != nil {
				return result, err
			}
			code, err := required(slot, "code")
			if err != nil {
				return result, err
			}
			result.Outcomes = append(result.Outcomes, SlotDiagnostic{Slot: name, Kind: scalarString(kind),
				Status: status, Code: scalarString(code), Target: target})
		}
	}
	completion, err := orObject(journal["completion"], "completion")
	if err != nil {
		return result, err
	}
	if truth(journal["finish_reply"]) && !truth(journal["response"]) && text(completion["status"]) != "refused" {
		pending["response"] = true
	}
	kind := journalKind(stateDir, attemptID, journal)
	if (kind == nil || equal(kind, "request")) && !truth(journal["finish_reply"]) && !truth(journal["response"]) &&
		text(completion["status"]) == "committed" && value(completion["result"]) != nil {
		pending["reply_possible"] = true
	}
	names := make([]string, 0, len(pending))
	for name := range pending {
		names = append(names, name)
	}
	sort.Strings(names)
	result.Pending = names
	return result, nil
}

func journalKind(stateDir, attemptID string, journal map[string]any) any {
	if truth(journal["kind"]) {
		return value(journal["kind"])
	}
	v, err := load(filepath.Join(stateDir, "inbox", attemptID+".json"))
	if err != nil {
		return nil
	}
	context, ok := value(v).(map[string]any)
	if !ok || !equal(context["attempt_id"], attemptID) {
		return nil
	}
	return value(context["kind"])
}

// SessionJournals seeds only when the index is absent, matching agent_id rather
// than execution_id. The caller owns persisting the updated state and pruning it.
// Non-object journals/subfields escape; an adopted missing attempt_id is KeyError.
func SessionJournals(stateDir string, state map[string]any) ([]any, error) {
	var ids []any
	if existing, present := state["inbox_journals"]; present {
		var ok bool
		ids, ok = value(existing).([]any)
		if !ok {
			if strings, isStrings := existing.([]string); isStrings {
				ids = make([]any, len(strings))
				for i, id := range strings {
					ids[i] = id
				}
			} else {
				return nil, &TypeError{"inbox_journals"}
			}
		}
	} else {
		ids = []any{}
		agent := map[string]any{}
		if v, present := state["agent"]; present {
			var err error
			agent, err = object(v, "agent")
			if err != nil {
				return nil, err
			}
		}
		agentID := text(agent["agent_id"])
		if agentID != "" {
			entries, err := filepath.Glob(filepath.Join(stateDir, "intents", "*.json"))
			if err != nil {
				return nil, err
			}
			for _, target := range entries {
				id := filepath.Base(target)
				id = id[:len(id)-len(".json")]
				s := Inspect(stateDir, id)
				if s.readError() != nil {
					continue
				}
				matched, err := s.MatchesAgent(agentID)
				if err != nil {
					return nil, err
				}
				if matched {
					id, err := required(s.journal, "attempt_id")
					if err != nil {
						return nil, err
					}
					ids = append(ids, id)
				}
			}
		}
		state["inbox_journals"] = ids
	}
	if truth(state["inbox_attempt"]) {
		attempt, ok := value(state["inbox_attempt"]).(map[string]any)
		if !ok {
			return nil, &TypeError{"inbox_attempt"}
		}
		id, err := required(attempt, "attempt_id")
		if err != nil {
			return nil, err
		}
		if !slices.ContainsFunc(ids, func(existing any) bool { return equal(existing, id) }) {
			ids = append(ids, id)
			state["inbox_journals"] = ids
		}
	}
	return ids, nil
}

// Flush gates only the active attempt under "attempt", or every uncertain
// journal under "all". Final historical notice/response journals are pruned.
// The returned state mutation is in memory; this function never writes session files.
func Flush(ctx context.Context, stateDir string, state map[string]any, scope string, options Options) (bool, error) {
	if stateDir == "" || !truth(state["agent"]) {
		return true, nil
	}
	attempt, err := orObject(state["inbox_attempt"], "inbox_attempt")
	if err != nil {
		return false, err
	}
	active := value(attempt["attempt_id"])
	ids, err := SessionJournals(stateDir, state)
	if err != nil {
		return false, err
	}
	settled := true
	for _, v := range append([]any(nil), ids...) {
		id, ok := value(v).(string)
		if !ok {
			return false, &TypeError{"attempt_id"}
		}
		result, err := Recover(ctx, stateDir, id, options)
		busy := errors.Is(err, Busy)
		if err != nil && !busy {
			return false, err
		}
		if (busy || result.Uncertain()) && (scope == "all" || equal(id, active)) {
			settled = false
		}
		if !busy && len(result.Pending) == 0 && !equal(id, active) {
			ids = slices.DeleteFunc(ids, func(existing any) bool { return equal(existing, id) })
			state["inbox_journals"] = ids
		}
	}
	return settled, nil
}
