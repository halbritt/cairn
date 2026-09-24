package core

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

type RunStatus struct {
	ReceiptID       string            `json:"receipt_id"`
	ObservedAt      time.Time         `json:"observed_at"`
	LaunchClaimed   bool              `json:"launch_claimed"`
	BindingObserved bool              `json:"binding_observed"`
	Outcome         *RunOutcomeStatus `json:"outcome"`
}

type RunOutcomeStatus struct {
	ObservationID string `json:"observation_id"`
	ProcessState  string `json:"process_state"`
	ExitCode      *int   `json:"exit_code"`
	Signal        *int   `json:"signal,omitempty"`
	DurationMS    int64  `json:"duration_ms"`
}

// RunStatus is an owner-only historical observation, never launch permission.
// It reads no package payload and remains useful after policy or restore fences.
func (s *Store) RunStatus(ctx context.Context, id string) (RunStatus, error) {
	ctx = context.WithValue(ctx, recoveryTransactionKey{}, true)
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return RunStatus{}, err
	}
	defer tx.Rollback(ctx)
	if err = s.receiptAccess(ctx, tx, id); err != nil {
		return RunStatus{}, err
	}
	result := RunStatus{ReceiptID: id}
	var outcomeID *string
	var outcome RunOutcomeStatus
	err = tx.QueryRow(ctx, `SELECT transaction_timestamp(),r.launch_claimed,b.receipt_id IS NOT NULL,
 o.outcome_id::text,COALESCE(o.process_state,''),o.exit_code,o.signal::int,COALESCE(o.duration_ms,0)
 FROM cairn.retrieval_receipt r LEFT JOIN cairn.run_binding b USING(receipt_id)
 LEFT JOIN cairn.run_outcome o USING(receipt_id) WHERE r.receipt_id=$1`, id).Scan(&result.ObservedAt, &result.LaunchClaimed, &result.BindingObserved, &outcomeID, &outcome.ProcessState, &outcome.ExitCode, &outcome.Signal, &outcome.DurationMS)
	if err != nil {
		return RunStatus{}, err
	}
	if outcomeID != nil {
		outcome.ObservationID = *outcomeID
		result.Outcome = &outcome
	}
	return result, tx.Commit(ctx)
}
