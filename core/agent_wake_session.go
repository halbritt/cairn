package core

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
)

// The slot supervisor reports which native conversation it launched. The native
// profile may differ from the slot profile. This trusted-host linkage does not
// transfer inbox ownership or change historical event attribution.
func (s *Store) attachWakeSession(ctx context.Context, tx pgx.Tx, w WakeAttempt, ref AgentSessionRef, dest Destination) error {
	if w.State != "running" {
		return failure("VERSION_CONFLICT", "only a running wake can associate a native session")
	}
	if w.Session != nil {
		if *w.Session == ref {
			return nil
		}
		return failure("VERSION_CONFLICT", "wake already has a different native session")
	}
	if err := checkEventLease(ctx, tx, w.Delivery, w.Delivery.LeaseID); err != nil {
		return err
	}
	var localEvent bool
	if err := tx.QueryRow(ctx, `SELECT sensitivity='local' FROM cairn.agent_event WHERE event_id=$1`, w.Delivery.Event.EventID).Scan(&localEvent); err != nil {
		return err
	}
	// Visibility classifies disclosure of directory metadata, not the model's
	// execution destination. A shareable wake must not reveal a private UUID.
	agent, err := scanAgentSession(tx.QueryRow(ctx, `SELECT `+agentSessionColumns+` FROM cairn.agent_session WHERE agent_id=$1 AND repo=$2 AND (visibility='hosted' OR $3) FOR UPDATE`, ref.AgentID, w.Delivery.Event.Repo, dest.AllowLocal && localEvent))
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("NOT_FOUND", "native session not found in this collection")
	}
	if err != nil {
		return err
	}
	if agent.ExecutionID != ref.ExecutionID || !agent.Online {
		return failure("STALE_SESSION", "native execution is not current")
	}
	if agent.Metadata.DeliveryMode != "fresh-worker" {
		return failure("INVALID_REQUEST", "wake requires a fresh-worker session")
	}
	var busy bool
	if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_wake_attempt WHERE agent_id=$1 AND finished_at IS NULL) OR EXISTS(SELECT 1 FROM cairn.agent_session_attempt WHERE agent_id=$1 AND finished_at IS NULL) OR EXISTS(SELECT 1 FROM cairn.agent_delivery WHERE consumer=$2 AND state='leased' AND lease_until>clock_timestamp())`, agent.AgentID, agent.Inbox).Scan(&busy); err != nil {
		return err
	}
	if busy {
		return failure("AGENT_BUSY", "native session already has active work")
	}
	_, err = tx.Exec(ctx, `UPDATE cairn.agent_wake_attempt SET agent_id=$2,execution_id=$3 WHERE attempt_id=$1`, w.ID, ref.AgentID, ref.ExecutionID)
	return err
}
