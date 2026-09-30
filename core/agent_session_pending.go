package core

import (
	"context"

	"github.com/jackc/pgx/v5"
)

// SessionInboxPending counts what native delivery would hand this session at
// a later boundary. It carries kinds and a position only, never senders,
// sources or bodies, and it claims, leases and acknowledges nothing.
type SessionInboxPending struct {
	Requests  int `json:"requests"`
	Notices   int `json:"notices"`
	Responses int `json:"responses"`
	// Truncated reports that at least sessionInboxPendingLimit items wait.
	Truncated bool `json:"truncated,omitempty"`
	// LatestPosition is the newest counted event's position, so a host can
	// tell a new arrival from an unchanged backlog without reading events.
	LatestPosition int64 `json:"latest_position,omitempty"`
}

const sessionInboxPendingLimit = 100

// SessionInboxPending is a read-only hint for a busy session. Unlike
// SessionInboxReady it answers whatever the session's presence state, since a
// long turn is exactly when the conversation cannot see its inbox.
func (s *Store) SessionInboxPending(ctx context.Context, ref AgentSessionRef, dest Destination) (SessionInboxPending, error) {
	var out SessionInboxPending
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
	if a.Metadata.DeliveryMode != "existing-session" {
		return out, tx.Commit(ctx)
	}
	var total int
	err = tx.QueryRow(ctx, `SELECT count(*) FILTER (WHERE kind='request'), count(*) FILTER (WHERE kind='notice'),
		count(*) FILTER (WHERE kind='response'), count(*), COALESCE(max(position),0)
		FROM (SELECT e.kind, e.position FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)
		WHERE e.repo=$1 AND d.consumer=$2 AND (e.sensitivity='shareable' OR $3) AND d.available_at<=clock_timestamp()
		AND e.task_deadline IS NULL AND `+requestAdmissionOpen+` AND `+wakeHold+` AND `+sessionInboxHold+`
		AND (d.state='pending' OR (d.state='leased' AND d.lease_until<=clock_timestamp()))
		ORDER BY e.position LIMIT $4) pending`, a.Repo, a.Inbox, dest.AllowLocal, sessionInboxPendingLimit).
		Scan(&out.Requests, &out.Notices, &out.Responses, &total, &out.LatestPosition)
	if err != nil {
		return out, err
	}
	out.Truncated = total >= sessionInboxPendingLimit
	return out, tx.Commit(ctx)
}
