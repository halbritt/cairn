package core

import (
	"context"
	"errors"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// SessionInboxAdvance transfers one native session hold to the next already
// eligible routine notice only after the current item was explicitly settled.
// It never applies a disposition to the next item or releases the final hold.
type SessionInboxAdvance struct {
	RequestID string          `json:"request_id"`
	Session   AgentSessionRef `json:"session"`
	AttemptID string          `json:"attempt_id"`
	Version   int             `json:"version"`
}

type SessionInboxAdvanceResult struct {
	Attempt   *SessionInboxAttempt `json:"attempt"`
	Remainder string               `json:"remainder,omitempty"`
}

func (s *Store) AdvanceSessionInbox(ctx context.Context, req SessionInboxAdvance, dest Destination) (SessionInboxAdvanceResult, error) {
	var empty SessionInboxAdvanceResult
	if err := s.nativeInboxProfile(req.Session, dest); err != nil {
		return empty, err
	}
	if validID(req.RequestID) != nil || validID(req.AttemptID) != nil || req.Version != 1 {
		return empty, failure("INVALID_REQUEST", "native notice advance requires stable UUIDs and version 1")
	}
	guard := func(tx pgx.Tx) error {
		_, err := currentAgentSession(ctx, tx, req.Session, s.channel.Principal, s.channel.Repo, true, dest)
		return err
	}
	return mutate(ctx, s, "session-inbox-advance", req.RequestID, req,
		func(tx pgx.Tx) (SessionInboxAdvanceResult, error) {
			a, err := currentAgentSession(ctx, tx, req.Session, s.channel.Principal, s.channel.Repo, true, dest)
			if err != nil {
				return empty, err
			}
			if err = activeAgent(a); err != nil {
				return empty, err
			}
			if a.Metadata.DeliveryMode != "existing-session" {
				return empty, failure("INVALID_REQUEST", "native batch requires existing-session delivery mode")
			}
			current, err := s.lockedSessionAttempt(ctx, tx, req.AttemptID, req.Session, dest)
			if err != nil {
				return empty, err
			}
			if current.FinishedAt != nil || current.Cancel != nil || current.NativeTurnID == "" || current.TurnExclusive ||
				current.Delivery.Event.Kind != "notice" {
				return empty, failure("INVALID_REQUEST", "only one live pinned nonexclusive notice may advance")
			}
			if current.Delivery.State != "handled" && current.Delivery.State != "ignored" && current.Delivery.State != "failed" {
				return empty, failure("DELIVERY_ACTIVE", "settle this notice explicitly before advancing")
			}
			var closed bool
			if err = tx.QueryRow(ctx, `SELECT batch_closed FROM cairn.agent_session_attempt WHERE attempt_id=$1`, current.ID).Scan(&closed); err != nil {
				return empty, err
			}
			if closed {
				return SessionInboxAdvanceResult{Remainder: "batch_ended"}, nil
			}
			root, index := current.BatchRoot, current.BatchIndex
			if root == "" {
				root, index = current.ID, 1
			}
			// A committed outcome and fixed root are retained even when there is
			// no next item. A later retry of this same intent cannot slide the cap.
			if current.BatchRoot == "" {
				if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET batch_root=$2,batch_index=1,batch_limit=3 WHERE attempt_id=$1`, current.ID, root); err != nil {
					return empty, err
				}
			}
			endBatch := func(reason string) (SessionInboxAdvanceResult, error) {
				if _, err := tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET batch_closed=true WHERE attempt_id=$1`, current.ID); err != nil {
					return empty, err
				}
				return SessionInboxAdvanceResult{Remainder: reason}, nil
			}
			if index >= 3 {
				return endBatch("batch_limit")
			}
			// A priority arrival anywhere in the eligible queue ends the batch.
			// Normal native admission then handles the queue without another
			// batched model step delaying that request or urgent notice.
			var priority bool
			err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1`+sessionInboxEligibleBase+
				` AND (e.kind='request' OR e.notice_policy->>'urgency'='urgent'))`, a.Repo, a.Inbox, dest.AllowLocal).Scan(&priority)
			if err != nil {
				return empty, err
			}
			if priority {
				return endBatch("priority_waiting")
			}
			cutoff := current.CreatedAt
			if root != current.ID {
				err = tx.QueryRow(ctx, `SELECT created_at FROM cairn.agent_session_attempt WHERE attempt_id=$1`, root).Scan(&cutoff)
				if err != nil {
					return empty, err
				}
			}
			// The root attempt timestamp freezes the burst. A routine notice
			// published during handling waits for its own turn.
			var nextID, kind, urgency string
			err = tx.QueryRow(ctx, `SELECT d.delivery_id::text,e.kind,COALESCE(e.notice_policy->>'urgency','')`+
				sessionInboxEligibleBase+` AND e.created_at<=$4 AND d.available_at<=$4`+
				` AND (d.state='pending' OR d.lease_until<=$4) ORDER BY e.position FOR UPDATE OF d LIMIT 1`,
				a.Repo, a.Inbox, dest.AllowLocal, cutoff).Scan(&nextID, &kind, &urgency)
			if errors.Is(err, pgx.ErrNoRows) {
				return endBatch("empty")
			}
			if err != nil {
				return empty, err
			}
			if kind != "notice" || urgency == "urgent" {
				return endBatch("priority_waiting")
			}
			lease := uuid.NewString()
			if _, err = finishAttempt(ctx, tx, current.ID, "delivery_completed", attemptClose{}); err != nil {
				return empty, err
			}
			if err = leaseDelivery(ctx, tx, nextID, lease, nativeLeaseTTL); err != nil {
				return empty, err
			}
			if err = openAttempt(ctx, tx, nativeAttemptOpen{AttemptID: req.RequestID, AgentID: a.AgentID, ExecutionID: a.ExecutionID, Repo: a.Repo, DeliveryID: nextID, LeaseID: lease, NativeTurnID: current.NativeTurnID, Batch: &nativeBatchPosition{Root: root, Index: int(index) + 1, Limit: 3}}); err != nil {
				return empty, err
			}
			attempt, err := s.readSessionInboxAttempt(ctx, tx, req.RequestID, req.Session, dest)
			if err != nil {
				return empty, err
			}
			return SessionInboxAdvanceResult{Attempt: &attempt}, nil
		}, guard)
}
