package core

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
)

type SessionInboxPollQuery struct {
	Session   AgentSessionRef `json:"session"`
	RequestID string          `json:"request_id"`
}

// SessionInboxPoll retains the execution that made the durable poll. A null
// AttemptID means that this request opened no attempt, not that the inbox is
// currently drained or that a different attempt has settled.
type SessionInboxPoll struct {
	RequestID string          `json:"request_id"`
	Session   AgentSessionRef `json:"session"`
	AttemptID *string         `json:"attempt_id"`
}

// SessionInboxPollStatus inspects one poll through its owner's current agent
// session. Neither the caller's request ID nor local policy authenticates it.
func (s *Store) SessionInboxPollStatus(ctx context.Context, req SessionInboxPollQuery, dest Destination) (SessionInboxPoll, error) {
	var out SessionInboxPoll
	if err := s.nativeInboxProfile(req.Session, dest); err != nil {
		return out, err
	}
	if validID(req.RequestID) != nil {
		return out, failure("INVALID_REQUEST", "native inbox poll UUID required")
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
	if err := activeAgent(a); err != nil {
		return out, err
	}
	var execution string
	var attempt *string
	err = tx.QueryRow(ctx, `SELECT execution_id::text,attempt_id::text FROM cairn.agent_session_poll
		WHERE request_id=$1 AND owner=$2 AND agent_id=$3`, req.RequestID, s.channel.Principal, a.AgentID).Scan(&execution, &attempt)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, failure("NOT_FOUND", "native inbox poll not found for this agent")
	}
	if err != nil {
		return out, err
	}
	if err := tx.Commit(ctx); err != nil {
		return out, err
	}
	return SessionInboxPoll{RequestID: req.RequestID, Session: AgentSessionRef{AgentID: a.AgentID, ExecutionID: execution}, AttemptID: attempt}, nil
}
