package coordination

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func censusD2RetainPolicy(t *testing.T, f *censusFixture, path string) WatchCensusPin {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var state map[string]any
	if err := json.Unmarshal(raw, &state); err != nil {
		t.Fatal(err)
	}
	state["inbox_result_policy"] = map[string]string{"claim_request_id": "retained-claim"}
	pin := censusTestJSON(t, path, state)
	for i := range f.catalog.Sessions {
		if f.catalog.Sessions[i].Path == path {
			f.catalog.Sessions[i] = pin
		}
	}
	return pin
}

func TestWatchCensusRetainedD2RequirementIsNamedAndReadOnly(t *testing.T) {
	f := newCensusFixture(t)
	path := f.proposal.Sessions[0]
	pin := censusD2RetainPolicy(t, f, path)
	before := censusTestSnapshot(t, f.root)
	report, err := f.inspect(t)
	if err == nil || !censusOnlyNeedsD2(err) || report.FixtureReady || len(report.Blockers) != 0 || len(report.DrainRequirements) != 1 {
		t.Fatalf("retained field must require D2, not become ready or a permanent blocker: %+v %v", report, err)
	}
	requirement := report.DrainRequirements[0]
	if requirement.Field != "inbox_result_policy" || requirement.Session.Path != path || requirement.Session.SHA256 != pin.SHA256 || requirement.Session.AgentID == "" || requirement.Session.ExecutionID == "" {
		t.Fatalf("lost named/pinned session requirement: %+v", requirement)
	}
	if !strings.Contains(err.Error(), path) || !strings.Contains(err.Error(), requirement.Field) {
		t.Fatalf("unresolved requirement is not diagnosed: %v", err)
	}
	if !reflect.DeepEqual(before, censusTestSnapshot(t, f.root)) {
		t.Fatal("census mutated retained fields or fixture files")
	}
}

func TestWatchCensusDisjointRetainedD2RequirementStaysDisjoint(t *testing.T) {
	f := newCensusFixture(t)
	path := f.catalog.Sessions[len(f.catalog.Sessions)-1].Path
	censusD2RetainPolicy(t, f, path)
	report, err := f.inspect(t)
	if err != nil || !report.FixtureReady || len(report.DrainRequirements) != 0 {
		t.Fatalf("disjoint retained state changed selected readiness: %+v %v", report, err)
	}
}

// These are identity-join refusal tests, not synthetic authenticated D2 passes.
// The passing authenticated path is covered by the real API/PG process fixture.
func TestUnitCensusD2JoinRejectsMissingOrMismatchedEvidence(t *testing.T) {
	session := WatchCensusSession{Path: "/private/session.json", NativeSession: "native", AgentID: "agent", ExecutionID: "execution", SHA256: strings.Repeat("a", 64)}
	for _, kind := range []string{"missing", "path", "native", "agent", "execution", "pin", "field"} {
		t.Run(kind, func(t *testing.T) {
			report := WatchUnitCensus{DrainRequirements: []WatchCensusDrainRequirement{{Session: session, Field: "inbox_result_policy"}}}
			proof := watchDrainD2Session{Path: session.Path, NativeSession: session.NativeSession, AgentID: session.AgentID, ExecutionID: session.ExecutionID, SHA256: session.SHA256, Fields: []string{"inbox_result_policy"}}
			passed := map[string]watchDrainD2Session{}
			switch kind {
			case "path":
				proof.Path = "/private/other.json"
			case "native":
				proof.NativeSession = "other-native"
			case "agent":
				proof.AgentID = "other-agent"
			case "execution":
				proof.ExecutionID = "other-execution"
			case "pin":
				proof.SHA256 = strings.Repeat("b", 64)
			case "field":
				proof.Fields = []string{"inbox_response"}
			}
			if kind != "missing" {
				passed[session.Path] = proof
			}
			if err := satisfyCensusD2(&report, passed); err == nil || report.FixtureReady {
				t.Fatalf("%s correspondence was accepted: %+v %v", kind, report, err)
			}
		})
	}
}

func TestUnitCensusD2CannotClearUnrelatedBlocker(t *testing.T) {
	report := WatchUnitCensus{Blockers: []string{"unrelated blocker"}}
	if err := satisfyCensusD2(&report, nil); err == nil || report.FixtureReady || !reflect.DeepEqual(report.Blockers, []string{"unrelated blocker"}) {
		t.Fatalf("unrelated blocker changed: %+v %v", report, err)
	}
}

func TestUnitCensusD2RejectsCallerSuppliedAuthentication(t *testing.T) {
	for _, field := range []string{"DrainRequirements", "AuthenticatedD2", "FixtureReady"} {
		t.Run(field, func(t *testing.T) {
			root := t.TempDir()
			digest := strings.Repeat("a", 64)
			current := map[string]any{"directory": "/private/current", "pins": map[string]string{"one.json": digest}}
			input := map[string]any{"Catalog": map[string]string{"Path": "/private/catalog", "SHA256": digest},
				"Proposal": map[string]any{"Bindings": []any{map[string]any{"Directory": "/private/current", "Pins": map[string]string{"one.json": digest}}}, "Sessions": []string{}, "Callbacks": []string{}},
				"Retained": []any{}, field: true}
			plan := map[string]any{"schema": "cairn.unit-control/2", "current": current,
				"next": []any{map[string]string{"name": "one.json", "path": "/private/next", "sha256": digest}}, "retained": "/private/retained", "census": input}
			pin := censusTestJSON(t, filepath.Join(root, "plan.json"), plan)
			if _, _, err := readUnitControlPlan(pin.Path, pin.SHA256); err == nil {
				t.Fatalf("caller %s was accepted as authenticated evidence", field)
			}
		})
	}
}
