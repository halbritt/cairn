package core

import (
	"context"
	"errors"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type SessionInboxClaim struct {
	RequestID    string          `json:"request_id"`
	Session      AgentSessionRef `json:"session"`
	DeliveryID   string          `json:"delivery_id,omitempty"`
	NativeTurnID string          `json:"native_turn_id,omitempty"`
}
type SessionInboxReconcile struct {
	RequestID string          `json:"request_id"`
	Session   AgentSessionRef `json:"session"`
	AttemptID string          `json:"attempt_id"`
	Reason    string          `json:"reason"`
}
type SessionInboxAttempt struct {
	ID           string          `json:"attempt_id"`
	Session      AgentSessionRef `json:"session"`
	CreatedAt    time.Time       `json:"created_at"`
	FinishedAt   *time.Time      `json:"finished_at,omitempty"`
	Reason       string          `json:"reason,omitempty"`
	Delivery     AgentDelivery   `json:"delivery"`
	NativeTurnID string          `json:"native_turn_id,omitempty"`
}
type SessionInboxResult struct {
	Attempt *SessionInboxAttempt `json:"attempt"`
}

type SessionInboxReadiness struct {
	DeliveryID string `json:"delivery_id,omitempty"`
}

const sessionInboxHold = `NOT EXISTS(SELECT 1 FROM cairn.agent_session_attempt n WHERE n.delivery_id=d.delivery_id AND n.finished_at IS NULL)`

const sessionInboxOccupied = `SELECT EXISTS(SELECT 1 FROM cairn.agent_session_attempt WHERE agent_id=$1 AND finished_at IS NULL) OR EXISTS(SELECT 1 FROM cairn.agent_wake_attempt WHERE (consumer=$2 OR agent_id=$1) AND finished_at IS NULL) OR EXISTS(SELECT 1 FROM cairn.agent_delivery WHERE consumer=$2 AND state='leased' AND lease_until>clock_timestamp())`

const sessionInboxNext = `SELECT d.delivery_id::text FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.consumer=$2 AND (e.sensitivity='shareable' OR $3) AND d.available_at<=clock_timestamp() AND e.task_deadline IS NULL AND ` + requestAdmissionOpen + ` AND ` + wakeHold + ` AND ` + sessionInboxHold + ` AND (d.state='pending' OR (d.state='leased' AND d.lease_until<=clock_timestamp())) ORDER BY e.position`

// SessionInboxReady is a bounded hint for an idle host wakeup, never a claim.
// The native turn still acquires ownership through ClaimSessionInbox.
func (s *Store) SessionInboxReady(ctx context.Context, ref AgentSessionRef, dest Destination) (SessionInboxReadiness, error) {
	var out SessionInboxReadiness
	if err := s.nativeInboxProfile(ref, dest); err != nil {
		return out, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	a, err := currentAgentSession(ctx, tx, ref, s.channel.Principal, s.channel.Repo, false, dest)
	if err != nil {
		return out, err
	}
	if err = activeAgent(a); err != nil {
		return out, err
	}
	if !a.Online || a.Metadata.DeliveryMode != "existing-session" || a.Metadata.State != "idle" {
		return out, nil
	}
	var held bool
	if err = tx.QueryRow(ctx, sessionInboxOccupied, a.AgentID, a.Inbox).Scan(&held); err != nil {
		return out, err
	}
	if held {
		return out, nil
	}
	err = tx.QueryRow(ctx, sessionInboxNext+` LIMIT 1`, a.Repo, a.Inbox, dest.AllowLocal).Scan(&out.DeliveryID)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, nil
	}
	return out, err
}

func (s *Store) nativeInboxProfile(ref AgentSessionRef, dest Destination) error {
	if err := validEventDestination(dest); err != nil {
		return err
	}
	if err := ref.Validate(); err != nil {
		return err
	}
	if s.session != nil || s.channel.Instrumented {
		return failure("INVALID_REQUEST", "native inbox operations require the existing base profile and explicit session")
	}
	return nil
}

// The caller's association is checked before constructing this transaction-local
// view. It cannot begin a transaction or borrow another profile's session.
func (s *Store) readSessionInboxAttempt(ctx context.Context, tx pgx.Tx, id string, ref AgentSessionRef, dest Destination) (SessionInboxAttempt, error) {
	var out SessionInboxAttempt
	var deliveryID, leaseID string
	err := tx.QueryRow(ctx, `SELECT attempt_id::text,agent_id::text,execution_id::text,delivery_id::text,lease_id::text,created_at,finished_at,reason,native_turn_id FROM cairn.agent_session_attempt WHERE attempt_id=$1 AND owner=$2 AND agent_id=$3 AND execution_id=$4`, id, s.channel.Principal, ref.AgentID, ref.ExecutionID).Scan(&out.ID, &out.Session.AgentID, &out.Session.ExecutionID, &deliveryID, &leaseID, &out.CreatedAt, &out.FinishedAt, &out.Reason, &out.NativeTurnID)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, failure("NOT_FOUND", "native inbox attempt not found for this execution")
	}
	if err != nil {
		return out, err
	}
	view := *s
	view.channel.Principal = "agent/" + ref.AgentID
	out.Delivery, err = view.ownedDelivery(ctx, tx, deliveryID, dest)
	out.Delivery.LeaseID = leaseID
	return out, err
}

func (s *Store) ClaimSessionInbox(ctx context.Context, req SessionInboxClaim, dest Destination) (SessionInboxResult, error) {
	var out SessionInboxResult
	if err := s.nativeInboxProfile(req.Session, dest); err != nil {
		return out, err
	}
	if validID(req.RequestID) != nil {
		return out, failure("INVALID_REQUEST", "stable native inbox claim UUID required")
	}
	if (req.DeliveryID == "") != (req.NativeTurnID == "") ||
		(req.DeliveryID != "" && (validID(req.DeliveryID) != nil || strings.TrimSpace(req.NativeTurnID) == "" || len(req.NativeTurnID) > 256 || strings.ContainsRune(req.NativeTurnID, 0))) {
		return out, failure("INVALID_REQUEST", "a native wake claim requires both an exact delivery UUID and a bounded native turn ID")
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	if err = lock(ctx, tx, "native-inbox-claim:"+req.RequestID); err != nil {
		return out, err
	}
	a, err := currentAgentSession(ctx, tx, req.Session, s.channel.Principal, s.channel.Repo, true, dest)
	if err != nil {
		return out, err
	}
	if err = activeAgent(a); err != nil {
		return out, err
	}
	if a.Metadata.DeliveryMode != "existing-session" {
		return out, failure("INVALID_REQUEST", "native inbox requires existing-session delivery mode")
	}
	var owner, agentID, executionID, requestedDelivery, nativeTurn string
	var prior *string
	err = tx.QueryRow(ctx, `SELECT owner,agent_id::text,execution_id::text,attempt_id::text,COALESCE(requested_delivery_id::text,''),native_turn_id FROM cairn.agent_session_poll WHERE request_id=$1`, req.RequestID).Scan(&owner, &agentID, &executionID, &prior, &requestedDelivery, &nativeTurn)
	if err == nil {
		if owner != s.channel.Principal || agentID != req.Session.AgentID || executionID != req.Session.ExecutionID {
			return out, failure("IDEMPOTENCY_CONFLICT", "native poll UUID already belongs to another execution")
		}
		if requestedDelivery != req.DeliveryID || nativeTurn != req.NativeTurnID {
			return out, failure("IDEMPOTENCY_CONFLICT", "native poll UUID already binds another delivery or turn")
		}
		if prior == nil {
			return out, tx.Commit(ctx)
		}
		attempt, err := s.readSessionInboxAttempt(ctx, tx, req.RequestID, req.Session, dest)
		if err != nil {
			return out, err
		}
		return SessionInboxResult{&attempt}, tx.Commit(ctx)
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return out, err
	}
	commit := func(attempt *SessionInboxAttempt) (SessionInboxResult, error) {
		var id *string
		if attempt != nil {
			id = &attempt.ID
		}
		if _, err := tx.Exec(ctx, `INSERT INTO cairn.agent_session_poll(request_id,agent_id,execution_id,attempt_id,requested_delivery_id,native_turn_id) VALUES($1,$2,$3,$4,NULLIF($5,'')::uuid,$6)`, req.RequestID, req.Session.AgentID, req.Session.ExecutionID, id, req.DeliveryID, req.NativeTurnID); err != nil {
			return out, err
		}
		return SessionInboxResult{attempt}, tx.Commit(ctx)
	}
	var exists bool
	if err = tx.QueryRow(ctx, sessionInboxOccupied, a.AgentID, a.Inbox).Scan(&exists); err != nil {
		return out, err
	}
	if exists {
		return commit(nil)
	}
	if _, err = expireRequestDeliveries(ctx, tx, a.Repo, a.Inbox, dest.AllowLocal); err != nil {
		return out, err
	}
	var id string
	err = tx.QueryRow(ctx, sessionInboxNext+` FOR UPDATE OF d SKIP LOCKED LIMIT 1`, a.Repo, a.Inbox, dest.AllowLocal).Scan(&id)
	if errors.Is(err, pgx.ErrNoRows) {
		return commit(nil)
	}
	if err != nil {
		return out, err
	}
	if req.DeliveryID != "" && id != req.DeliveryID {
		return commit(nil) // A stale wake must not acquire a different request.
	}
	lease := uuid.NewString()
	if _, err = tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='leased',lease_id=$2,lease_until=clock_timestamp()+interval '90 seconds',attempts=attempts+1 WHERE delivery_id=$1`, id, lease); err != nil {
		return out, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.agent_session_attempt(attempt_id,agent_id,execution_id,delivery_id,lease_id,native_turn_id) VALUES($1,$2,$3,$4,$5,$6)`, req.RequestID, a.AgentID, a.ExecutionID, id, lease, req.NativeTurnID); err != nil {
		return out, err
	}
	attempt, err := s.readSessionInboxAttempt(ctx, tx, req.RequestID, req.Session, dest)
	if err != nil {
		return out, err
	}
	return commit(&attempt)
}

// Reconciliation is a trusted-host report. It accepts an obsolete execution only
// to close that execution's own attempt after a confirmed process/turn end. It
// never transfers work or establishes that external effects did not happen.
func (s *Store) ReconcileSessionInbox(ctx context.Context, req SessionInboxReconcile, dest Destination) (SessionInboxAttempt, error) {
	if err := s.nativeInboxProfile(req.Session, dest); err != nil {
		return SessionInboxAttempt{}, err
	}
	if validID(req.AttemptID) != nil {
		return SessionInboxAttempt{}, failure("INVALID_REQUEST", "native inbox attempt UUID required")
	}
	if req.Reason != "delivery_completed" && req.Reason != "process_exited" && req.Reason != "turn_ended" {
		return SessionInboxAttempt{}, failure("INVALID_REQUEST", "native reconciliation requires completion or an observed process/turn end")
	}
	guard := func(tx pgx.Tx) error {
		var repo string
		err := tx.QueryRow(ctx, `SELECT repo FROM cairn.agent_session WHERE agent_id=$1 AND owner=$2 AND (visibility='hosted' OR $3) FOR UPDATE`, req.Session.AgentID, s.channel.Principal, dest.AllowLocal).Scan(&repo)
		if errors.Is(err, pgx.ErrNoRows) {
			return failure("NOT_FOUND", "native session not found for this profile")
		}
		if err != nil {
			return err
		}
		if err = s.checkRepo(repo); err != nil {
			return err
		}
		_, err = s.readSessionInboxAttempt(ctx, tx, req.AttemptID, req.Session, dest)
		return err
	}
	return mutate(ctx, s, "session-inbox-reconcile", req.RequestID, req, func(tx pgx.Tx) (SessionInboxAttempt, error) {
		attempt, err := s.readSessionInboxAttempt(ctx, tx, req.AttemptID, req.Session, dest)
		if err != nil || attempt.FinishedAt != nil {
			return attempt, err
		}
		if attempt.Delivery.State == "pending" || attempt.Delivery.State == "leased" {
			if req.Reason == "delivery_completed" {
				return attempt, failure("DELIVERY_ACTIVE", "native delivery has not been explicitly completed")
			}
			if _, err = tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='failed',lease_id=NULL,lease_until=NULL,completed_at=clock_timestamp(),code='processing_failed' WHERE delivery_id=$1`, attempt.Delivery.DeliveryID); err != nil {
				return attempt, err
			}
		}
		if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET finished_at=clock_timestamp(),reason=$2 WHERE attempt_id=$1`, req.AttemptID, req.Reason); err != nil {
			return attempt, err
		}
		return s.readSessionInboxAttempt(ctx, tx, req.AttemptID, req.Session, dest)
	}, guard)
}
