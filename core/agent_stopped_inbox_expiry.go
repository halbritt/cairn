package core

import (
	"context"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5"
)

// StoppedInboxExpiryRequest selects stranded deliveries to explicitly stopped
// sessions. A dry run reports what would fail and needs no request ID; applying
// requires a request ID, a reason and the exact sessions reviewed.
type StoppedInboxExpiryRequest struct {
	RequestID         string   `json:"request_id,omitempty"`
	Repo              string   `json:"repo"`
	DryRun            bool     `json:"dry_run,omitempty"`
	AgentIDs          []string `json:"agent_ids,omitempty"`
	StoppedForSeconds int      `json:"stopped_for_seconds,omitempty"`
	Reason            string   `json:"reason,omitempty"`
}

type StoppedInboxExpiry struct {
	DryRun            bool                      `json:"dry_run"`
	StoppedForSeconds int                       `json:"stopped_for_seconds"`
	Expired           int                       `json:"expired"`
	Sessions          []StoppedInbox            `json:"sessions"`
	Classification    []UnresolvedConsumerClass `json:"classification,omitempty"`
}

// StoppedInbox describes one session. Status is eligible (dry run), expired
// (applied) or the reason the session was left alone: not_found, not_stopped,
// replaced or stopped_recently. Retained counts deliveries that stay unresolved
// because a host still holds them, a lease was taken, or an operator granted a
// successor access to their event.
type StoppedInbox struct {
	AgentID     string                 `json:"agent_id"`
	DisplayName string                 `json:"display_name,omitempty"`
	ExecutionID string                 `json:"execution_id,omitempty"`
	StoppedAt   *time.Time             `json:"stopped_at,omitempty"`
	Status      string                 `json:"status"`
	Requests    int                    `json:"requests"`
	Responses   int                    `json:"responses"`
	Notices     int                    `json:"notices"`
	Other       int                    `json:"other"`
	Retained    StoppedInboxRetained   `json:"retained"`
	Deliveries  []StoppedInboxDelivery `json:"deliveries"`
}

type StoppedInboxRetained struct {
	Held           int `json:"held"`
	Leased         int `json:"leased"`
	SuccessorGrant int `json:"successor_grant"`
}

type StoppedInboxDelivery struct {
	DeliveryID  string    `json:"delivery_id"`
	EventID     string    `json:"event_id"`
	Kind        string    `json:"kind"`
	Publisher   string    `json:"publisher"`
	AvailableAt time.Time `json:"available_at"`
	Attempts    int       `json:"attempts"`
	// Execution is never_launched when no claim or attempt was ever recorded;
	// otherwise may_have_executed, as in operator review.
	Execution string `json:"execution"`
}

// UnresolvedConsumerClass counts pending and leased deliveries in the
// repository by what their consumer is. Only session_stopped deliveries are
// candidates; every other class is reported for review only.
type UnresolvedConsumerClass struct {
	Class     string    `json:"class"`
	Total     int       `json:"total"`
	Requests  int       `json:"requests"`
	Held      int       `json:"held"`
	Consumers int       `json:"consumers"`
	Oldest    time.Time `json:"oldest"`
}

const (
	defaultStoppedForSeconds = 86400
	minStoppedForSeconds     = 3600
	maxStoppedForSeconds     = 366 * 86400
	maxStoppedInboxSessions  = 100
)

// ExpireStoppedInboxes fails unresolved deliveries addressed directly to a
// session that explicitly left (stopped=true) at least StoppedForSeconds ago,
// in the current database generation, with code consumer_retired. Offline
// presence alone never qualifies: it does not prove the agent stopped. Topic
// subscriptions, pools, profile and other non-session inboxes are never
// selected. A delivery with an open wake or native hold, a lease, or a live
// successor grant on its event is retained. Nothing is replayed; the publisher
// sees the failure and can reissue it explicitly.
func (s *Store) ExpireStoppedInboxes(ctx context.Context, req StoppedInboxExpiryRequest) (StoppedInboxExpiry, error) {
	var zero StoppedInboxExpiry
	repo, err := s.requestControlScope(req.Repo)
	if err != nil {
		return zero, err
	}
	req.Repo = repo
	if req.StoppedForSeconds == 0 {
		req.StoppedForSeconds = defaultStoppedForSeconds
	}
	if req.StoppedForSeconds < minStoppedForSeconds || req.StoppedForSeconds > maxStoppedForSeconds {
		return zero, failure("INVALID_REQUEST", "stopped_for_seconds must be between 3600 and 31622400")
	}
	if len(req.AgentIDs) > maxStoppedInboxSessions {
		return zero, failure("INVALID_REQUEST", "agent_ids accepts at most 100 sessions")
	}
	seen := map[string]bool{}
	for _, id := range req.AgentIDs {
		if validID(id) != nil {
			return zero, failure("INVALID_REQUEST", "agent_ids must be UUIDs")
		}
		if seen[id] {
			return zero, failure("INVALID_REQUEST", "agent_ids must be distinct")
		}
		seen[id] = true
	}
	if req.DryRun {
		if req.RequestID != "" || req.Reason != "" {
			return zero, failure("INVALID_REQUEST", "a dry run takes no request_id or reason")
		}
		tx, err := s.begin(ctx)
		if err != nil {
			return zero, err
		}
		defer tx.Rollback(ctx)
		out, err := stoppedInboxPlan(ctx, tx, req, false)
		if err != nil {
			return zero, err
		}
		if out.Classification, err = unresolvedConsumerClasses(ctx, tx, req); err != nil {
			return zero, err
		}
		return out, tx.Commit(ctx)
	}
	if len(req.AgentIDs) == 0 {
		return zero, failure("INVALID_REQUEST", "applying requires the reviewed agent_ids; run with dry_run first")
	}
	if err = reasonValid(req.Reason); err != nil {
		return zero, err
	}
	return mutate(ctx, s, "stopped-inbox-expire", req.RequestID, req, func(tx pgx.Tx) (StoppedInboxExpiry, error) {
		if err := lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
			return zero, err
		}
		out, err := stoppedInboxPlan(ctx, tx, req, true)
		if err != nil {
			return zero, err
		}
		ids := []string{}
		for i := range out.Sessions {
			if out.Sessions[i].Status != "eligible" {
				continue
			}
			out.Sessions[i].Status = "expired"
			for _, d := range out.Sessions[i].Deliveries {
				ids = append(ids, d.DeliveryID)
			}
		}
		if len(ids) > 0 {
			tag, err := tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='failed',lease_id=NULL,lease_until=NULL,completed_at=clock_timestamp(),code='consumer_retired',control_at=clock_timestamp(),control_by=current_setting('cairn.caller'),control_reason=$2 WHERE delivery_id=ANY($1::uuid[]) AND state='pending'`, ids, req.Reason)
			if err != nil {
				return zero, err
			}
			if tag.RowsAffected() != int64(len(ids)) {
				return zero, failure("VERSION_CONFLICT", "a selected delivery changed during expiry; review again")
			}
		}
		return out, nil
	})
}

// stoppedInboxPlan selects sessions and their deliveries. When locking, session
// rows are locked first so a concurrent resume either commits before (and the
// session is no longer stopped) or waits; delivery rows are then locked before
// a separate statement reads holds, so a hold committed while waiting is seen.
func stoppedInboxPlan(ctx context.Context, tx pgx.Tx, req StoppedInboxExpiryRequest, locking bool) (StoppedInboxExpiry, error) {
	out := StoppedInboxExpiry{DryRun: !locking, StoppedForSeconds: req.StoppedForSeconds, Sessions: []StoppedInbox{}}
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return out, err
	}
	var filter []string
	if len(req.AgentIDs) > 0 {
		filter = req.AgentIDs
	}
	lockClause := ""
	if locking {
		lockClause = " FOR UPDATE"
	}
	rows, err := tx.Query(ctx, `SELECT agent_id::text,ordinal,execution_id::text,expires_at,
 CASE WHEN repo<>$1 THEN 'not_found' WHEN NOT stopped THEN 'not_stopped' WHEN database_generation<>$2 THEN 'replaced'
  WHEN expires_at>clock_timestamp()-make_interval(secs=>$3) THEN 'stopped_recently' ELSE 'eligible' END
 FROM cairn.agent_session
 WHERE CASE WHEN $4::uuid[] IS NULL THEN repo=$1 AND stopped AND database_generation=$2 AND expires_at<=clock_timestamp()-make_interval(secs=>$3)
  ELSE agent_id=ANY($4::uuid[]) END
 ORDER BY ordinal`+lockClause, req.Repo, generation, req.StoppedForSeconds, filter)
	if err != nil {
		return out, err
	}
	index := map[string]int{}
	for rows.Next() {
		var s StoppedInbox
		var ordinal int64
		var stoppedAt time.Time
		if err = rows.Scan(&s.AgentID, &ordinal, &s.ExecutionID, &stoppedAt, &s.Status); err != nil {
			rows.Close()
			return out, err
		}
		if s.Status == "not_found" {
			continue
		}
		s.DisplayName = "agent-" + strconv.FormatInt(ordinal, 10)
		s.StoppedAt = &stoppedAt
		s.Deliveries = []StoppedInboxDelivery{}
		index[s.AgentID] = len(out.Sessions)
		out.Sessions = append(out.Sessions, s)
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return out, err
	}
	for _, id := range req.AgentIDs {
		if _, ok := index[id]; !ok {
			index[id] = len(out.Sessions)
			out.Sessions = append(out.Sessions, StoppedInbox{AgentID: id, Status: "not_found", Deliveries: []StoppedInboxDelivery{}})
		}
	}
	eligible := []string{}
	for _, s := range out.Sessions {
		if s.Status == "eligible" {
			eligible = append(eligible, "agent/"+s.AgentID)
		}
	}
	if len(eligible) > 0 {
		if locking {
			if _, err = tx.Exec(ctx, `SELECT 1 FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE d.consumer=ANY($1) AND e.repo=$2 AND e.destination_type='agent' AND d.state IN ('pending','leased') FOR UPDATE OF d`, eligible, req.Repo); err != nil {
				return out, err
			}
		}
		rows, err = tx.Query(ctx, `SELECT substr(d.consumer,7),d.delivery_id::text,d.event_id::text,e.kind,e.publisher,d.available_at,d.attempts,d.state,
 d.attempts>0 OR EXISTS(SELECT 1 FROM cairn.agent_wake_attempt w WHERE w.delivery_id=d.delivery_id) OR EXISTS(SELECT 1 FROM cairn.agent_session_attempt n WHERE n.delivery_id=d.delivery_id),
 NOT (`+wakeHold+`) OR NOT (`+sessionInboxHold+`),
 EXISTS(SELECT 1 FROM cairn.agent_successor_grant g WHERE g.repo=e.repo AND 'agent/'||g.predecessor_agent_id::text=d.consumer AND d.event_id=ANY(g.event_ids) AND g.revoked_at IS NULL AND g.expires_at>clock_timestamp())
 FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)
 WHERE d.consumer=ANY($1) AND e.repo=$2 AND e.destination_type='agent' AND d.state IN ('pending','leased')
 ORDER BY d.position`, eligible, req.Repo)
		if err != nil {
			return out, err
		}
		defer rows.Close()
		for rows.Next() {
			var agentID, state string
			var d StoppedInboxDelivery
			var attempted, held, granted bool
			if err = rows.Scan(&agentID, &d.DeliveryID, &d.EventID, &d.Kind, &d.Publisher, &d.AvailableAt, &d.Attempts, &state, &attempted, &held, &granted); err != nil {
				return out, err
			}
			s := &out.Sessions[index[agentID]]
			switch {
			case held:
				s.Retained.Held++
				continue
			case state != "pending":
				s.Retained.Leased++
				continue
			case granted:
				s.Retained.SuccessorGrant++
				continue
			}
			d.Execution = "never_launched"
			if attempted {
				d.Execution = "may_have_executed"
			}
			switch d.Kind {
			case "request":
				s.Requests++
			case "response":
				s.Responses++
			case "notice":
				s.Notices++
			default:
				s.Other++
			}
			s.Deliveries = append(s.Deliveries, d)
			out.Expired++
		}
		if err = rows.Err(); err != nil {
			return out, err
		}
	}
	if len(req.AgentIDs) == 0 {
		// An unfiltered review lists only stopped sessions with something to decide.
		kept := out.Sessions[:0]
		for _, s := range out.Sessions {
			if len(s.Deliveries) > 0 || s.Retained != (StoppedInboxRetained{}) {
				kept = append(kept, s)
			}
		}
		out.Sessions = kept
	}
	return out, nil
}

func unresolvedConsumerClasses(ctx context.Context, tx pgx.Tx, req StoppedInboxExpiryRequest) ([]UnresolvedConsumerClass, error) {
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return nil, err
	}
	rows, err := tx.Query(ctx, `SELECT CASE
  WHEN e.destination_type='topic' THEN 'topic_subscriber'
  WHEN e.destination_type='pool' THEN 'worker_pool'
  WHEN a.agent_id IS NULL THEN 'legacy_inbox'
  WHEN a.database_generation<>$2 THEN 'session_replaced'
  WHEN NOT a.stopped AND a.expires_at>clock_timestamp() THEN 'session_live'
  WHEN NOT a.stopped THEN 'session_offline'
  WHEN a.expires_at>clock_timestamp()-make_interval(secs=>$3) THEN 'session_stopped_recently'
  ELSE 'session_stopped' END AS class,
 count(*),count(*) FILTER (WHERE e.kind='request'),
 count(*) FILTER (WHERE NOT (`+wakeHold+`) OR NOT (`+sessionInboxHold+`)),
 count(DISTINCT d.consumer),min(d.available_at)
 FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)
 LEFT JOIN cairn.agent_session a ON d.consumer='agent/'||a.agent_id::text AND a.repo=e.repo
 WHERE e.repo=$1 AND d.state IN ('pending','leased')
 GROUP BY 1 ORDER BY 1`, req.Repo, generation, req.StoppedForSeconds)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []UnresolvedConsumerClass{}
	for rows.Next() {
		var c UnresolvedConsumerClass
		if err = rows.Scan(&c.Class, &c.Total, &c.Requests, &c.Held, &c.Consumers, &c.Oldest); err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}
