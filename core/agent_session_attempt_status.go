package core

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
)

type SessionInboxAttemptQuery struct {
	Session   AgentSessionRef `json:"session"`
	AttemptID string          `json:"attempt_id"`
}

// SessionInboxAttemptStatus reads an exact retained attempt, including a
// finished attempt or one from an earlier execution of this conversation.
// The current session authenticates the read; it never replaces the attempt's
// historical execution. Missing or inaccessible attempts are unknown, not
// evidence that a native turn or its tools have stopped.
func (s *Store) SessionInboxAttemptStatus(ctx context.Context, req SessionInboxAttemptQuery, dest Destination) (SessionInboxAttempt, error) {
	var out SessionInboxAttempt
	if err := s.nativeInboxProfile(req.Session, dest); err != nil {
		return out, err
	}
	if validID(req.AttemptID) != nil {
		return out, failure("INVALID_REQUEST", "native inbox attempt UUID required")
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	a, err := currentAgentSession(ctx, tx, req.Session, s.channel.Principal, s.channel.Repo, false, dest)
	if err != nil {
		return out, err
	}
	if err = activeAgent(a); err != nil {
		return out, err
	}
	// Authorize the historical association before using the transaction-local
	// reader. An attempt UUID alone never selects another owner or agent.
	var execution string
	err = tx.QueryRow(ctx, `SELECT execution_id::text FROM cairn.agent_session_attempt
		WHERE attempt_id=$1 AND owner=$2 AND agent_id=$3`, req.AttemptID, s.channel.Principal, a.AgentID).Scan(&execution)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, failure("NOT_FOUND", "native inbox attempt not found for this agent")
	}
	if err != nil {
		return out, err
	}
	attempt, err := s.readSessionInboxAttempt(ctx, tx, req.AttemptID, AgentSessionRef{AgentID: a.AgentID, ExecutionID: execution}, dest)
	if err != nil {
		return out, err
	}
	// Inspection does not convey a lease or a deadline usable by its holder.
	attempt.Delivery.LeaseID, attempt.Delivery.LeaseUntil = "", nil
	if err = tx.Commit(ctx); err != nil {
		return out, err
	}
	return attempt, nil
}
