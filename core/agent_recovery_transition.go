package core

import (
	"context"

	"github.com/jackc/pgx/v5"
)

// recordRecoveryTransition appends one row to the recovery ledger for a
// Store-mediated stop or recovery of a delivery: native hold release, event
// reissue, operator cancellation (manual or confirmed native), retirement of a
// stopped session's inbox, and the request-control sweep. Its revision counts
// ledger rows, not every delivery lifecycle change; ordinary leasing,
// settlement and release stay outside it. A sweep carries no request id.
func recordRecoveryTransition(ctx context.Context, tx pgx.Tx, s *Store, deliveryID, eventID, relatedEventID string, attemptID *string, requestID, action, outcome, before, after string) error {
	var revision int64
	if err := tx.QueryRow(ctx, `UPDATE cairn.agent_delivery SET recovery_revision=recovery_revision+1 WHERE delivery_id=$1 RETURNING recovery_revision`, deliveryID).Scan(&revision); err != nil {
		return err
	}
	_, err := tx.Exec(ctx, `INSERT INTO cairn.agent_recovery_transition(delivery_id,revision,event_id,related_event_id,attempt_id,request_id,actor,action_kind,observed_outcome,state_before,state_after)
 VALUES($1,$2,$3,NULLIF($4,'')::uuid,$5,NULLIF($6,'')::uuid,$7,$8,$9,$10,$11)`, deliveryID, revision, eventID, relatedEventID, attemptID, requestID, s.channel.Principal, action, outcome, before, after)
	return err
}
