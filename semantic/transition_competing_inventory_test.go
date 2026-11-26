package semantic

import (
	"fmt"
	"reflect"
	"sort"
	"strings"
	"testing"
)

var transitionCompetingPhases = []string{
	"old_owners_results_held", "both_active_refused", "other_collection_active_refused",
	"inactive_restartable_refused", "old_generation_joined_fence", "retained_jobs_due_advanced",
	"selected_identity_verified", "selected_generation_ready", "selected_generation_complete",
	"selected_generation_joined_fence",
}

type transitionCompetingState struct {
	Name        string `json:"name"`
	Collection  string `json:"collection"`
	Model       string `json:"model"`
	Restartable bool   `json:"restartable"`
	Started     bool   `json:"started"`
	Joined      bool   `json:"joined"`
	Reaped      bool   `json:"reaped"`
	PID         int    `json:"owner_pid"`
}

// The expected set is the closed fixture's registered start paths. The observed
// set comes from its sealed configuration directory, rather than a caller's
// collection-specific subset. This is not a host service inventory.
func transitionCompetingFence(expected, observed []string, states map[string]transitionCompetingState) error {
	expected = append([]string(nil), expected...)
	observed = append([]string(nil), observed...)
	sort.Strings(expected)
	sort.Strings(observed)
	if len(expected) == 0 || !reflect.DeepEqual(expected, observed) {
		return fmt.Errorf("incomplete or unknown fixture writer definitions: expected=%v observed=%v", expected, observed)
	}
	if len(states) != len(expected) {
		return fmt.Errorf("fixture writer state inventory does not match definitions")
	}
	var blockers []string
	for _, name := range expected {
		state, ok := states[name]
		if !ok || state.Name != name {
			return fmt.Errorf("missing fixture writer state: %s", name)
		}
		if state.Restartable || !state.Joined || !state.Reaped || (state.Started && state.PID <= 1) || (!state.Started && state.PID != 0) {
			blockers = append(blockers, name)
		}
	}
	if len(blockers) != 0 {
		return fmt.Errorf("fixture writers remain active, restartable or unverified: %s", strings.Join(blockers, ","))
	}
	return nil
}

func TestSemanticCompetingInventory(t *testing.T) {
	names := []string{"a", "b", "inactive", "next"}
	closed := func() map[string]transitionCompetingState {
		states := map[string]transitionCompetingState{}
		for _, name := range names {
			states[name] = transitionCompetingState{Name: name, Collection: name, Joined: true, Reaped: true}
		}
		return states
	}
	t.Run("unknown_omitted_duplicate_or_empty_definitions_refuse", func(t *testing.T) {
		for _, observed := range [][]string{nil, {"a", "b", "next"}, {"a", "b", "inactive", "foreign"}, {"a", "a", "inactive", "next"}} {
			if transitionCompetingFence(names, observed, closed()) == nil {
				t.Fatalf("incomplete inventory accepted: %v", observed)
			}
		}
	})
	t.Run("another_collection_and_inactive_restartability_each_refuse", func(t *testing.T) {
		for _, name := range []string{"a", "b", "inactive"} {
			states := closed()
			state := states[name]
			state.Restartable = true
			states[name] = state
			if err := transitionCompetingFence(names, names, states); err == nil || !strings.Contains(err.Error(), name) {
				t.Fatalf("remaining definition %s did not refuse: %v", name, err)
			}
		}
	})
	t.Run("disabled_but_unjoined_or_unreaped_owner_refuses", func(t *testing.T) {
		for _, pair := range [][2]bool{{false, false}, {true, false}, {false, true}} {
			states := closed()
			state := states["a"]
			state.Started, state.PID, state.Joined, state.Reaped = true, 42, pair[0], pair[1]
			states["a"] = state
			if transitionCompetingFence(names, names, states) == nil {
				t.Fatalf("missing join/reap proof accepted: %v", pair)
			}
		}
	})
	t.Run("unsafe_or_fabricated_owner_identity_refuses", func(t *testing.T) {
		for _, pid := range []int{-42, 0, 1} {
			states := closed()
			state := states["a"]
			state.Started, state.PID = true, pid
			states["a"] = state
			if transitionCompetingFence(names, names, states) == nil {
				t.Fatalf("unsafe owner %d accepted", pid)
			}
		}
		states := closed()
		state := states["inactive"]
		state.PID = 42
		states["inactive"] = state
		if transitionCompetingFence(names, names, states) == nil {
			t.Fatal("a never-started definition acquired a fabricated PID")
		}
	})
	t.Run("complete_disabled_joined_reaped_inventory_accepts", func(t *testing.T) {
		states := closed()
		for _, name := range []string{"a", "b"} {
			state := states[name]
			state.Started, state.PID = true, 42
			states[name] = state
		}
		if err := transitionCompetingFence(names, names, states); err != nil {
			t.Fatal(err)
		}
	})
}
