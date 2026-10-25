package core

import (
	"context"

	"github.com/jackc/pgx/v5"
)

// recordRecoveryTransition records only the two endpoint outcomes wired into
// this helper. Its revision is scoped to recorded recovery actions; it is not a
// complete revision of every delivery state change.
func recordRecoveryTransition(ctx context.Context, tx pgx.Tx, s *Store, deliveryID, eventID, relatedEventID string, attemptID *string, requestID, action, outcome, before, after string) error {
	var revision int64
	if err := tx.QueryRow(ctx, `UPDATE cairn.agent_delivery SET recovery_revision=recovery_revision+1 WHERE delivery_id=$1 RETURNING recovery_revision`, deliveryID).Scan(&revision); err != nil {
		return err
	}
	_, err := tx.Exec(ctx, `INSERT INTO cairn.agent_recovery_transition(delivery_id,revision,event_id,related_event_id,attempt_id,request_id,actor,action_kind,observed_outcome,state_before,state_after)
 VALUES($1,$2,$3,NULLIF($4,'')::uuid,$5,$6,$7,$8,$9,$10,$11)`, deliveryID, revision, eventID, relatedEventID, attemptID, requestID, s.channel.Principal, action, outcome, before, after)
	return err
}
