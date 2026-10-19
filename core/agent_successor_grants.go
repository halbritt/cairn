package core

import (
	"context"
	"errors"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type AuthorizeSessionSuccessorRequest struct {
	RequestID          string          `json:"request_id"`
	Repo               string          `json:"repo"`
	PredecessorAgentID string          `json:"predecessor_agent_id"`
	Successor          AgentSessionRef `json:"successor"`
	EventIDs           []string        `json:"event_ids"`
	TTLSeconds         int             `json:"ttl_seconds,omitempty"`
	Reason             string          `json:"reason"`
}

type RevokeSessionSuccessorRequest struct {
	RequestID string `json:"request_id"`
	GrantID   string `json:"grant_id"`
	Reason    string `json:"reason"`
}

type SessionSuccessorGrant struct {
	GrantID                string          `json:"grant_id"`
	RequestID              string          `json:"request_id"`
	Repo                   string          `json:"repo"`
	PredecessorAgentID     string          `json:"predecessor_agent_id"`
	PredecessorExecutionID string          `json:"predecessor_execution_id"`
	Successor              AgentSessionRef `json:"successor"`
	DatabaseGeneration     int64           `json:"database_generation"`
	EventIDs               []string        `json:"event_ids"`
	GrantedBy              string          `json:"granted_by"`
	GrantedAt              time.Time       `json:"granted_at"`
	ExpiresAt              time.Time       `json:"expires_at"`
	Reason                 string          `json:"reason"`
	RevokedAt              *time.Time      `json:"revoked_at,omitempty"`
	RevokedBy              string          `json:"revoked_by,omitempty"`
	RevokeReason           string          `json:"revoke_reason,omitempty"`
}

const successorGrantColumns = `grant_id::text,request_id::text,repo,predecessor_agent_id::text,predecessor_execution_id::text,successor_agent_id::text,successor_execution_id::text,database_generation,ARRAY(SELECT unnest(event_ids)::text),granted_by,granted_at,expires_at,reason,revoked_at,COALESCE(revoked_by,''),COALESCE(revoke_reason,'')`

func scanSuccessorGrant(row pgx.Row) (SessionSuccessorGrant, error) {
	var g SessionSuccessorGrant
	err := row.Scan(
		&g.GrantID, &g.RequestID, &g.Repo,
		&g.PredecessorAgentID, &g.PredecessorExecutionID,
		&g.Successor.AgentID, &g.Successor.ExecutionID,
		&g.DatabaseGeneration, &g.EventIDs,
		&g.GrantedBy, &g.GrantedAt, &g.ExpiresAt, &g.Reason,
		&g.RevokedAt, &g.RevokedBy, &g.RevokeReason,
	)
	if g.EventIDs == nil {
		g.EventIDs = []string{}
	}
	return g, err
}

func successorTTLSeconds(seconds int) (int, error) {
	if seconds == 0 {
		return 3600, nil
	}
	if seconds < 1 || seconds > 86400 {
		return 0, failure("INVALID_REQUEST", "ttl_seconds must be 1-86400")
	}
	return seconds, nil
}

// AuthorizeSessionSuccessor records an explicit, bounded operator grant allowing
// a designated successor session execution to inspect selected predecessor events
// and publish linked follow-ups attributed to itself.
func (s *Store) AuthorizeSessionSuccessor(ctx context.Context, req AuthorizeSessionSuccessorRequest) (SessionSuccessorGrant, error) {
	if !s.channel.Operator || s.channel.Repo != "" {
		return SessionSuccessorGrant{}, failure("AUTHORITY_DENIED", "successor authorization requires an unscoped operator channel")
	}
	if err := validID(req.RequestID); err != nil {
		return SessionSuccessorGrant{}, err
	}
	if strings.TrimSpace(req.Repo) == "" || req.Repo == "*" || len(req.Repo) > 256 {
		return SessionSuccessorGrant{}, failure("INVALID_REQUEST", "event collection repository required")
	}
	if err := s.checkRepo(req.Repo); err != nil {
		return SessionSuccessorGrant{}, err
	}
	if err := validID(req.PredecessorAgentID); err != nil {
		return SessionSuccessorGrant{}, failure("INVALID_REQUEST", "predecessor_agent_id must be a UUID")
	}
	if err := req.Successor.Validate(); err != nil {
		return SessionSuccessorGrant{}, err
	}
	if req.PredecessorAgentID == req.Successor.AgentID {
		return SessionSuccessorGrant{}, failure("INVALID_REQUEST", "successor session must be distinct from predecessor session")
	}
	if len(req.EventIDs) < 1 || len(req.EventIDs) > 64 {
		return SessionSuccessorGrant{}, failure("INVALID_REQUEST", "successor grant requires 1-64 event UUIDs")
	}
	seen := make(map[string]bool, len(req.EventIDs))
	for _, id := range req.EventIDs {
		if validID(id) != nil {
			return SessionSuccessorGrant{}, failure("INVALID_REQUEST", "event_ids must be canonical UUIDs")
		}
		if seen[id] {
			return SessionSuccessorGrant{}, failure("INVALID_REQUEST", "event_ids must be distinct")
		}
		seen[id] = true
	}
	ttl, err := successorTTLSeconds(req.TTLSeconds)
	if err != nil {
		return SessionSuccessorGrant{}, err
	}
	if err := reasonValid(req.Reason); err != nil {
		return SessionSuccessorGrant{}, err
	}
	req.TTLSeconds = ttl

	return mutate(ctx, s, "session-successor-authorize", req.RequestID, req, func(tx pgx.Tx) (SessionSuccessorGrant, error) {
		generation, err := retrievalGeneration(ctx, tx)
		if err != nil {
			return SessionSuccessorGrant{}, err
		}
		if err := lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
			return SessionSuccessorGrant{}, err
		}
		return s.authorizeSessionSuccessorTx(ctx, tx, req, generation, ttl)
	})
}

func (s *Store) authorizeSessionSuccessorTx(ctx context.Context, tx pgx.Tx, req AuthorizeSessionSuccessorRequest, generation int64, ttl int) (SessionSuccessorGrant, error) {
	var predRepo, predExec string
	var predLive bool
	err := tx.QueryRow(ctx, `SELECT repo,execution_id::text,NOT stopped AND expires_at>clock_timestamp() AND database_generation=$2 FROM cairn.agent_session WHERE agent_id=$1 FOR SHARE`, req.PredecessorAgentID, generation).Scan(&predRepo, &predExec, &predLive)
	if errors.Is(err, pgx.ErrNoRows) {
		return SessionSuccessorGrant{}, failure("NOT_FOUND", "predecessor session not found")
	}
	if err != nil {
		return SessionSuccessorGrant{}, err
	}
	if predRepo != req.Repo {
		return SessionSuccessorGrant{}, failure("INVALID_REQUEST", "predecessor session does not belong to the specified repository")
	}
	if predLive {
		return SessionSuccessorGrant{}, failure("SESSION_LIVE", "predecessor session is still online; stop or wait for expiry before authorizing a successor")
	}
	var predHeld bool
	if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_wake_attempt WHERE repo=$1 AND (consumer=$2 OR agent_id=$3) AND finished_at IS NULL) OR EXISTS(SELECT 1 FROM cairn.agent_session_attempt WHERE agent_id=$3 AND finished_at IS NULL)`, req.Repo, "agent/"+req.PredecessorAgentID, req.PredecessorAgentID).Scan(&predHeld); err != nil {
		return SessionSuccessorGrant{}, err
	}
	if predHeld {
		return SessionSuccessorGrant{}, failure("VERSION_CONFLICT", "predecessor session has unfinished delivery attempts")
	}

	var succRepo, succExec string
	var succGen int64
	var succStopped bool
	err = tx.QueryRow(ctx, `SELECT repo,execution_id::text,database_generation,stopped FROM cairn.agent_session WHERE agent_id=$1 FOR SHARE`, req.Successor.AgentID).Scan(&succRepo, &succExec, &succGen, &succStopped)
	if errors.Is(err, pgx.ErrNoRows) {
		return SessionSuccessorGrant{}, failure("NOT_FOUND", "successor session not found")
	}
	if err != nil {
		return SessionSuccessorGrant{}, err
	}
	if succRepo != req.Repo {
		return SessionSuccessorGrant{}, failure("INVALID_REQUEST", "successor session does not belong to the specified repository")
	}
	if succStopped || succExec != req.Successor.ExecutionID || succGen != generation {
		return SessionSuccessorGrant{}, failure("STALE_SESSION", "successor session execution is stopped, replaced, or predates restore")
	}

	predPrincipal := "agent/" + req.PredecessorAgentID
	for _, eventID := range req.EventIDs {
		var eligible bool
		err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_event e WHERE e.event_id=$1 AND e.repo=$2 AND (e.publisher=$3 OR EXISTS(SELECT 1 FROM cairn.agent_delivery d WHERE d.event_id=e.event_id AND d.consumer=$3)))`, eventID, req.Repo, predPrincipal).Scan(&eligible)
		if err != nil {
			return SessionSuccessorGrant{}, err
		}
		if !eligible {
			return SessionSuccessorGrant{}, failure("NOT_FOUND", "selected event is not an eligible predecessor event in this collection")
		}
	}

	return scanSuccessorGrant(tx.QueryRow(ctx, `INSERT INTO cairn.agent_successor_grant(grant_id,request_id,repo,predecessor_agent_id,predecessor_execution_id,successor_agent_id,successor_execution_id,database_generation,event_ids,granted_by,granted_at,expires_at,reason) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::uuid[],$10,clock_timestamp(),clock_timestamp()+make_interval(secs=>$11),$12) RETURNING `+successorGrantColumns,
		uuid.NewString(), req.RequestID, req.Repo, req.PredecessorAgentID, predExec, req.Successor.AgentID, req.Successor.ExecutionID, generation, req.EventIDs, s.channel.Principal, ttl, req.Reason))
}

// RevokeSessionSuccessor revokes an existing successor grant. It requires an
// unscoped operator channel and is idempotent on request_id.
func (s *Store) RevokeSessionSuccessor(ctx context.Context, req RevokeSessionSuccessorRequest) (SessionSuccessorGrant, error) {
	if !s.channel.Operator || s.channel.Repo != "" {
		return SessionSuccessorGrant{}, failure("AUTHORITY_DENIED", "successor revocation requires an unscoped operator channel")
	}
	if err := validID(req.RequestID); err != nil {
		return SessionSuccessorGrant{}, err
	}
	if err := validID(req.GrantID); err != nil {
		return SessionSuccessorGrant{}, failure("INVALID_REQUEST", "grant_id must be a UUID")
	}
	if err := reasonValid(req.Reason); err != nil {
		return SessionSuccessorGrant{}, err
	}
	return mutate(ctx, s, "session-successor-revoke", req.RequestID, req, func(tx pgx.Tx) (SessionSuccessorGrant, error) {
		g, err := scanSuccessorGrant(tx.QueryRow(ctx, `SELECT `+successorGrantColumns+` FROM cairn.agent_successor_grant WHERE grant_id=$1 FOR UPDATE`, req.GrantID))
		if errors.Is(err, pgx.ErrNoRows) {
			return SessionSuccessorGrant{}, failure("NOT_FOUND", "successor grant not found")
		}
		if err != nil {
			return SessionSuccessorGrant{}, err
		}
		if g.RevokedAt != nil {
			if g.RevokeReason == req.Reason && g.RevokedBy == s.channel.Principal {
				return g, nil
			}
			return SessionSuccessorGrant{}, failure("VERSION_CONFLICT", "successor grant is already revoked")
		}
		return scanSuccessorGrant(tx.QueryRow(ctx, `UPDATE cairn.agent_successor_grant SET revoked_at=clock_timestamp(),revoked_by=$2,revoke_reason=$3 WHERE grant_id=$1 RETURNING `+successorGrantColumns, req.GrantID, s.channel.Principal, req.Reason))
	})
}

// successorAuthorizedPredecessorsTx returns the distinct predecessor principals
// ("agent/<predecessor_agent_id>") whose active, unrevoked, unexpired,
// generation-fenced grants authorize s.session to inspect eventID in repo while
// the predecessor remains offline/stopped at its granted execution.
func (s *Store) successorAuthorizedPredecessorsTx(ctx context.Context, tx pgx.Tx, eventID string) ([]string, error) {
	if s.session == nil {
		return nil, nil
	}
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return nil, err
	}
	rows, err := tx.Query(ctx, `SELECT DISTINCT 'agent/'||g.predecessor_agent_id::text
 FROM cairn.agent_successor_grant g
 JOIN cairn.agent_session pred ON pred.agent_id=g.predecessor_agent_id
 JOIN cairn.agent_event e ON e.event_id=$1 AND e.repo=g.repo
 WHERE g.successor_agent_id=$2
  AND g.successor_execution_id=$3
  AND g.database_generation=$4
  AND g.revoked_at IS NULL
  AND g.expires_at>clock_timestamp()
  AND $1::uuid=ANY(g.event_ids)
  AND pred.repo=g.repo
  AND pred.execution_id=g.predecessor_execution_id
  AND (pred.stopped OR pred.expires_at<=clock_timestamp() OR pred.database_generation<>$4)
  AND (e.publisher='agent/'||g.predecessor_agent_id::text OR EXISTS(
   SELECT 1 FROM cairn.agent_delivery v WHERE v.event_id=e.event_id AND v.consumer='agent/'||g.predecessor_agent_id::text
  ))`, eventID, s.session.Ref.AgentID, s.session.Ref.ExecutionID, generation)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var p string
		if err = rows.Scan(&p); err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, rows.Err()
}
