package core

import (
	"errors"
	"maps"
	"slices"
)

var errInspectionTargetDoesNotFit = errors.New("inspection target exceeds final encoded room")

// Normalize the actual whole expansion before both reservation and delivery.
// Ranking reasons do not describe a pull, and companion order is source-stable.
func wholeExpansionPayload(selection Selection, companions []Selection) (Selection, []Selection, any) {
	selection.Reason = "indexed record; current eligibility revalidated"
	companions = slices.Clone(companions)
	for i := range companions {
		companions[i].Reason = "competing indexed position; current eligibility revalidated"
	}
	sortSelections(companions)
	if len(companions) == 0 {
		return selection, companions, selection
	}
	return selection, companions, struct {
		Selection Selection   `json:"selection"`
		Competing []Selection `json:"competing"`
	}{selection, companions}
}

func packInspectionIndex(p SemanticPackage, candidates []candidate, evaluations map[string]*CandidateEvaluation, query string) (SemanticPackage, error) {
	excluded := map[string]bool{}
	wholeCosts := map[string]int{}
	for {
		trial := p
		trial.Omitted = maps.Clone(p.Omitted)
		result, err := packIndexPass(trial, candidates, evaluations, query, excluded, wholeCosts)
		if !errors.Is(err, errInspectionTargetDoesNotFit) {
			return result, err
		}
		// Later omission counters can add digits to the final envelope. If even the
		// protected unit alone no longer fits, record that exact failure and resume
		// selection; never trim the target or return a misleading ready allocation.
		id := result.Index[0].RecordID
		if excluded[id] {
			return result, failure("INTEGRITY_FAILURE", "inspection target selection did not advance")
		}
		excluded[id] = true
	}
}
