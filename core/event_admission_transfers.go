package core

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
)

// A pool request already owns a collection unit. Selecting a worker adds only
// the recipient and producer/recipient charge, using the stored publisher.
func preparePoolDeliveryTransferTx(ctx context.Context, tx pgx.Tx, repo, eventID, consumer string) error {
	policy, err := admissionPolicyTx(ctx, tx, repo)
	if err != nil {
		return err
	}
	if policy.Policy.Mode != "enforce" {
		return nil
	}
	if !policy.EnforcementReady {
		return failure("FEATURE_UNAVAILABLE", "event admission enforcement requires every delivery writer")
	}
	return checkPoolDeliveryTransferTx(ctx, tx, repo, eventID, consumer, policy)
}
func checkPoolDeliveryTransferTx(ctx context.Context, tx pgx.Tx, repo, eventID, consumer string, policy EventAdmissionPolicyState) error {
	var publisher string
	err := tx.QueryRow(ctx, `SELECT e.publisher FROM cairn.agent_pool_request p JOIN cairn.agent_event e USING(event_id) WHERE p.event_id=$1 AND e.repo=$2 AND p.delivery_id IS NULL AND p.closed_at IS NULL FOR UPDATE OF p`, eventID, repo).Scan(&publisher)
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("VERSION_CONFLICT", "pool request is no longer awaiting assignment")
	}
	if err != nil {
		return err
	}
	if policy.Policy.Limits == nil {
		return failure("FEATURE_UNAVAILABLE", "enforced event admission requires finite limits")
	}
	return checkEventRecipientCapacityTx(ctx, tx, repo, publisher, []string{consumer}, policy.Policy.Limits, policy.Revision)
}
