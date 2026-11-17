package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
)

type SessionQuiescenceQuery struct {
	Session       AgentSessionRef `json:"session"`
	PollRequestID string          `json:"poll_request_id,omitempty"`
}

type SessionQuiescenceHold struct {
	AttemptID    string          `json:"attempt_id"`
	Session      AgentSessionRef `json:"session"`
	DeliveryID   string          `json:"delivery_id"`
	NativeTurnID string          `json:"native_turn_id"`
	Cancel       *SessionCancel  `json:"cancel,omitempty"`
	TurnStop     string          `json:"turn_stop"`
	CaptureState string          `json:"capture_state"`
	ToolCount    int             `json:"tool_count"`
}

type SessionQuiescenceLease struct {
	DeliveryID string    `json:"delivery_id"`
	EventID    string    `json:"event_id"`
	LeaseUntil time.Time `json:"lease_until"`
}

type SessionQuiescenceWake struct {
	AttemptID string          `json:"attempt_id"`
	State     string          `json:"state"`
	Session   AgentSessionRef `json:"session"`
}

// SessionQuiescence is a complete, bounded store observation of one owned
// inbox, including holds from prior executions. It is not native authority,
// a lease, a publication fence, or a statement about repository-wide wakes.
type SessionQuiescence struct {
	Schema          string                   `json:"schema"`
	Coverage        string                   `json:"coverage"`
	Complete        bool                     `json:"complete"`
	Repo            string                   `json:"repo"`
	Session         AgentSessionRef          `json:"session"`
	NativeSessionID string                   `json:"native_session_id"`
	Binding         string                   `json:"binding"`
	Generation      int64                    `json:"database_generation"`
	ContextRevision int64                    `json:"context_revision"`
	Stopped         bool                     `json:"stopped"`
	ReportedState   string                   `json:"reported_state"`
	PresenceEnds    time.Time                `json:"presence_ends"`
	Hold            *SessionQuiescenceHold   `json:"hold"`
	Leases          []SessionQuiescenceLease `json:"leases"`
	Wakes           []SessionQuiescenceWake  `json:"wakes"`
	Pending         int64                    `json:"pending"`
	Poll            *SessionInboxPoll        `json:"poll,omitempty"`
	PollTerminal    bool                     `json:"poll_terminal"`
	ObservedAt      time.Time                `json:"observed_at"`
	FactsSHA256     string                   `json:"facts_sha256"`
}

// ReadSessionQuiescence is deliberately separate from WakeAttempts and D4.
// The base authenticated profile chooses an exact owned session. Stopped
// sessions remain inspectable, but replaced executions and restore generations
// refuse. No state is settled, claimed, renewed, released or normalized here.
func (s *Store) ReadSessionQuiescence(ctx context.Context, req SessionQuiescenceQuery, dest Destination) (SessionQuiescence, error) {
	var out SessionQuiescence
	if err := s.nativeInboxProfile(req.Session, dest); err != nil {
		return out, err
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
	if a.Metadata.DeliveryMode != "existing-session" {
		return out, failure("INVALID_REQUEST", "quiescence reads require an existing-session inbox")
	}
	if req.PollRequestID != "" && validID(req.PollRequestID) != nil {
		return out, failure("INVALID_REQUEST", "quiescence poll UUID required")
	}
	inbox := "agent/" + a.AgentID
	var inconsistent bool
	err = tx.QueryRow(ctx, `SELECT
		EXISTS(SELECT 1 FROM cairn.agent_wake_attempt WHERE agent_id=$1 AND repo<>$2 AND finished_at IS NULL)
		OR EXISTS(SELECT 1 FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)
		WHERE d.consumer=$3 AND e.repo<>$2 AND d.state='leased')`, a.AgentID, a.Repo, inbox).Scan(&inconsistent)
	if err != nil {
		return out, err
	}
	if inconsistent {
		return out, failure("INVALID_REQUEST", "inbox quiescence association scope is inconsistent")
	}
	var hidden bool
	err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_delivery d
		JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$2 AND
		(d.consumer=$1 OR EXISTS(SELECT 1 FROM cairn.agent_wake_attempt w
		WHERE w.delivery_id=d.delivery_id AND w.agent_id=$4))
		AND e.sensitivity<>'shareable' AND NOT $3 AND
		(d.state='leased' OR EXISTS(SELECT 1 FROM cairn.agent_wake_attempt w
		WHERE w.delivery_id=d.delivery_id AND w.finished_at IS NULL)))`, inbox, a.Repo, dest.AllowLocal, a.AgentID).Scan(&hidden)
	if err != nil {
		return out, err
	}
	if hidden {
		return out, failure("DESTINATION_PROHIBITED", "inbox quiescence visibility is incomplete")
	}
	result := SessionQuiescence{Schema: "cairn.session-quiescence/1",
		Coverage: "exact-owned-inbox-and-associated-worker-custody-across-executions; pending-count-is-destination-eligible-only; repository-wide-wakes-not-covered",
		Repo:     a.Repo, Session: req.Session, NativeSessionID: a.NativeSessionID,
		Generation: a.DatabaseGeneration, ContextRevision: a.ContextRevision, Stopped: a.Stopped,
		ReportedState: a.Metadata.State, PresenceEnds: a.ExpiresAt,
		Leases: []SessionQuiescenceLease{}, Wakes: []SessionQuiescenceWake{}}
	if err := tx.QueryRow(ctx, `SELECT binding FROM cairn.agent_session WHERE agent_id=$1`, a.AgentID).Scan(&result.Binding); err != nil {
		return out, err
	}
	var attemptID, execution string
	err = tx.QueryRow(ctx, `SELECT attempt_id::text,execution_id::text FROM cairn.agent_session_attempt
		WHERE agent_id=$1 AND finished_at IS NULL`, a.AgentID).Scan(&attemptID, &execution)
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return out, err
	}
	if err == nil {
		hold, err := s.readSessionInboxAttempt(ctx, tx, attemptID, AgentSessionRef{a.AgentID, execution}, dest)
		if err != nil {
			return out, err
		}
		result.Hold = &SessionQuiescenceHold{AttemptID: hold.ID, Session: hold.Session,
			DeliveryID: hold.Delivery.DeliveryID, NativeTurnID: hold.NativeTurnID, Cancel: hold.Cancel,
			TurnStop: hold.TurnStopState, CaptureState: hold.CaptureState, ToolCount: len(hold.Tools)}
	}
	if result.Leases, err = sessionQuiescenceLeases(ctx, tx, inbox, a.Repo, a.AgentID); err != nil {
		return out, err
	}
	if result.Wakes, err = sessionQuiescenceWakes(ctx, tx, inbox, a.Repo, a.AgentID); err != nil {
		return out, err
	}
	if err := tx.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)
		WHERE d.consumer=$1 AND e.repo=$2 AND d.state='pending' AND (e.sensitivity='shareable' OR $3)`, inbox, a.Repo, dest.AllowLocal).Scan(&result.Pending); err != nil {
		return out, err
	}
	if req.PollRequestID != "" {
		poll := &SessionInboxPoll{RequestID: req.PollRequestID, Session: req.Session}
		var execution string
		if err := tx.QueryRow(ctx, `SELECT execution_id::text,attempt_id::text FROM cairn.agent_session_poll
			WHERE request_id=$1 AND owner=$2 AND agent_id=$3`, req.PollRequestID, s.channel.Principal, a.AgentID).Scan(&execution, &poll.AttemptID); err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				return out, failure("NOT_FOUND", "owned quiescence poll not found")
			}
			return out, err
		}
		if execution != a.ExecutionID {
			return out, failure("STALE_SESSION", "quiescence poll execution differs")
		}
		result.Poll, result.PollTerminal = poll, poll.AttemptID == nil
		if poll.AttemptID != nil {
			if err := tx.QueryRow(ctx, `SELECT finished_at IS NOT NULL FROM cairn.agent_session_attempt
				WHERE attempt_id=$1 AND agent_id=$2 AND execution_id=$3`, *poll.AttemptID, a.AgentID, a.ExecutionID).Scan(&result.PollTerminal); err != nil {
				return out, err
			}
		}
	}
	result.Complete = true
	facts, err := json.Marshal(result)
	if err != nil {
		return out, err
	}
	digest := sha256.Sum256(facts)
	result.FactsSHA256 = hex.EncodeToString(digest[:])
	if err := tx.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&result.ObservedAt); err != nil {
		return out, err
	}
	if err := tx.Commit(ctx); err != nil {
		return out, err
	}
	return result, nil
}

func sessionQuiescenceLeases(ctx context.Context, tx pgx.Tx, inbox, repo, agentID string) ([]SessionQuiescenceLease, error) {
	rows, err := tx.Query(ctx, `SELECT d.delivery_id::text,d.event_id::text,d.lease_until
		FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)
		WHERE e.repo=$2 AND d.state='leased' AND (d.consumer=$1 OR EXISTS(
		SELECT 1 FROM cairn.agent_wake_attempt w WHERE w.delivery_id=d.delivery_id AND w.agent_id=$3))
		ORDER BY d.delivery_id LIMIT 129`, inbox, repo, agentID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []SessionQuiescenceLease{}
	for rows.Next() {
		var lease SessionQuiescenceLease
		if err := rows.Scan(&lease.DeliveryID, &lease.EventID, &lease.LeaseUntil); err != nil {
			return nil, err
		}
		result = append(result, lease)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	if len(result) > 128 {
		return nil, failure("INVALID_REQUEST", "inbox quiescence lease coverage exceeds 128 rows")
	}
	return result, nil
}

func sessionQuiescenceWakes(ctx context.Context, tx pgx.Tx, inbox, repo, agentID string) ([]SessionQuiescenceWake, error) {
	rows, err := tx.Query(ctx, `SELECT attempt_id::text,state,COALESCE(agent_id::text,''),COALESCE(execution_id::text,'')
		FROM cairn.agent_wake_attempt WHERE (consumer=$1 OR agent_id=$3) AND repo=$2 AND finished_at IS NULL
		ORDER BY attempt_id LIMIT 129`, inbox, repo, agentID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []SessionQuiescenceWake{}
	for rows.Next() {
		var wake SessionQuiescenceWake
		if err := rows.Scan(&wake.AttemptID, &wake.State, &wake.Session.AgentID, &wake.Session.ExecutionID); err != nil {
			return nil, err
		}
		result = append(result, wake)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	if len(result) > 128 {
		return nil, failure("INVALID_REQUEST", "inbox quiescence wake coverage exceeds 128 rows")
	}
	return result, nil
}
