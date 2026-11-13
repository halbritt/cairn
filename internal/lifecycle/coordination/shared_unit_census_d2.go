package coordination

import (
	"errors"
	"fmt"
	"slices"
	"strings"
)

type watchCensusD2Required struct {
	requirements []WatchCensusDrainRequirement
}

func (e *watchCensusD2Required) Error() string {
	var reasons []string
	for _, requirement := range e.requirements {
		reasons = append(reasons, fmt.Sprintf("retained native %s at %s needs its authenticated drain correspondence", requirement.Field, requirement.Session.Path))
	}
	return strings.Join(reasons, "; ")
}

func censusOnlyNeedsD2(err error) bool {
	_, required := err.(*watchCensusD2Required)
	return required
}

// Produced only from the existing reader's successful historical checks.
// Neither this result nor the receipt can be supplied through a /2 plan.
type watchDrainD2Session struct {
	Path, NativeSession, AgentID, ExecutionID, SHA256 string
	Fields                                            []string
}

func (r *watchDrainRead) d2SessionResults() map[string]watchDrainD2Session {
	passed := map[string]watchDrainD2Session{}
	for _, session := range r.sessions {
		if r.settled[session.state] == nil {
			continue
		}
		native := nativeText(session.state.Get("native_session_id"))
		path := SessionStatePath(nativeText(session.config.Get("state_dir")), nativeText(session.config.Get("binding")), native)
		result := watchDrainD2Session{Path: path, NativeSession: native, AgentID: nativeText(session.ref.Get("agent_id")), ExecutionID: nativeText(session.ref.Get("execution_id")), SHA256: r.facts["file:"+path]}
		for _, field := range AttemptKeys {
			if !nativeNone(session.state.Get(field)) {
				result.Fields = append(result.Fields, field)
			}
		}
		passed[path] = result
	}
	return passed
}

func satisfyCensusD2(report *WatchUnitCensus, passed map[string]watchDrainD2Session) error {
	if len(report.Blockers) != 0 {
		return errors.New(strings.Join(report.Blockers, "; "))
	}
	for _, requirement := range report.DrainRequirements {
		session := requirement.Session
		actual, ok := passed[session.Path]
		if !ok || actual.Path != session.Path || actual.NativeSession != session.NativeSession ||
			actual.AgentID != session.AgentID || actual.ExecutionID != session.ExecutionID ||
			actual.SHA256 == "" || actual.SHA256 != session.SHA256 || !slices.Contains(actual.Fields, requirement.Field) {
			return fmt.Errorf("authenticated D2 correspondence missing or mismatched for %s at %s", requirement.Field, session.Path)
		}
	}
	report.FixtureReady = len(report.Blockers) == 0
	return sealWatchCensusReport(report)
}
