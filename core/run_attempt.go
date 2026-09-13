package core

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
)

// A host attempt is an observed spawn, not a caller's proposed delegation.
// Lock it through binding/claim commit so terminal observation is ordered
// against authorization. Neither binding nor process exit finalizes the host.
func (s *Store) requireOpenAttempt(ctx context.Context, tx pgx.Tx, id string, scope Scope) error {
	if id == "" {
		return nil
	}
	if err := validID(id); err != nil {
		return err
	}
	var owner string
	var actual Scope
	var terminal *string
	err := tx.QueryRow(ctx, `SELECT observed_by,repo,task_id,run_id,terminal_state
 FROM cairn.delegation_attempt WHERE attempt_id=$1 FOR UPDATE`, id).Scan(&owner, &actual.Repo, &actual.TaskID, &actual.RunID, &terminal)
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("NOT_FOUND", "host attempt not observed")
	}
	if err != nil {
		return err
	}
	if owner != s.channel.Principal || actual != scope {
		return failure("AUTHORITY_DENIED", "host attempt must match the receipt observer and exact scope")
	}
	if terminal != nil {
		return failure("ATTEMPT_TERMINAL", "host attempt is already terminal; it cannot authorize a new launch")
	}
	return nil
}

func (s *Store) boundAttemptCurrent(ctx context.Context, tx pgx.Tx, receiptID string) error {
	var attemptID *string
	var scope Scope
	err := tx.QueryRow(ctx, `SELECT b.attempt_id::text,r.scope FROM cairn.retrieval_receipt r
 JOIN cairn.run_binding b USING(receipt_id) WHERE r.receipt_id=$1`, receiptID).Scan(&attemptID, &scope)
	// A legacy receipt can claim without a run binding.
	if errors.Is(err, pgx.ErrNoRows) {
		return nil
	}
	if err != nil {
		return err
	}
	if attemptID == nil {
		return nil
	}
	if err := s.requireOpenAttempt(ctx, tx, *attemptID, scope); err != nil {
		return err
	}
	// Binding is preparation. Reserve at most one execution per host attempt
	// only when claiming, while holding the same attempt row lock as terminal.
	var claimed bool
	if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.run_binding b
    JOIN cairn.retrieval_receipt r USING(receipt_id) WHERE b.attempt_id=$1 AND r.launch_claimed)`, *attemptID).Scan(&claimed); err != nil {
		return err
	}
	if claimed {
		return failure("RUN_ALREADY_STARTED", "host attempt already claimed a wrapped execution")
	}
	return nil
}
