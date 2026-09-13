package core

import (
	"context"

	"github.com/jackc/pgx/v5"
)

// Recheck eligibility without reranking or modifying the retained context.
// The query is deliberately not retained, and new optional memory need not
// invalidate a package whose selected facts and required context still hold.
func (s *Store) receiptSelectionCurrent(ctx context.Context, tx pgx.Tx, id string) error {
	pkg, err := readPackage(ctx, tx, id)
	if err != nil {
		return err
	}
	original := pkg.Semantic
	_, candidates, err := s.collectCandidates(ctx, tx, CompileRequest{
		Context: original.Context, Scope: original.Scope, Purpose: original.Purpose,
		AvailableTokens: original.AvailableTokens, AdvisoryConflicts: original.AdvisoryConflicts,
	}, original.Destination, map[string]*CandidateEvaluation{})
	if err != nil {
		return err
	}
	current := make(map[string]Selection, len(candidates))
	var mandatory []Selection
	for _, candidate := range candidates {
		entry := candidate.selection
		current[entry.Record.RecordID] = entry
		if entry.Mandatory {
			mandatory = append(mandatory, entry)
		}
	}
	var originalMandatory []Selection
	for _, entry := range original.Selected {
		if entry.Mandatory {
			originalMandatory = append(originalMandatory, entry)
		}
		fresh, ok := current[entry.Record.RecordID]
		if !ok || !sameSelectionFacts([]Selection{entry}, []Selection{fresh}) {
			return failure("STALE_PACKAGE", "selected memory changed or is no longer eligible; compile with a new request ID")
		}
	}
	if !sameSelectionFacts(originalMandatory, mandatory) {
		return failure("STALE_PACKAGE", "mandatory context changed; compile with a new request ID")
	}
	for _, entry := range original.Index {
		fresh, ok := current[entry.RecordID]
		if !ok || fresh.Record.Version != entry.Version || fresh.Record.Class != entry.Class ||
			!sameAdvisoryConflicts(fresh.Conflicts, entry.Conflicts) || fresh.Record.Kind != entry.Kind || fresh.Category != entry.Category || indexEntry(fresh.Record).BodySHA256 != entry.BodySHA256 {
			return failure("STALE_PACKAGE", "indexed memory changed or is no longer eligible; compile with a new request ID")
		}
	}
	return nil
}
