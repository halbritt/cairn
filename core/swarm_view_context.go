package core

import (
	"context"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
)

const swarmContextNearLimitKiB = 64

// SwarmContextRoom is snapshot.context_room. The session counters are an
// observation of their own: ObservedAt is the newest counter row read, so the
// page can mark the counters stale without hiding the rest.
type SwarmContextRoom struct {
	// ObservedAt is nil when no counter row exists for any member.
	ObservedAt *time.Time `json:"observed_at,omitempty"`
	// NearLimitRule states the cutoff for the Cairn output proxy.
	NearLimitRule string `json:"near_limit_rule"`
	// ByAgent is keyed by agent UUID. A member without an entry, or with
	// Known false, shows "unknown", never zero.
	ByAgent map[string]SwarmAgentContext `json:"by_agent"`
}

// SwarmAgentContext is the context room of one session. A nil pointer means
// the store has no data for that value.
type SwarmAgentContext struct {
	Known bool `json:"known"`
	// Compactions so far, and when the last one happened.
	Compactions      *int       `json:"compactions,omitempty"`
	LastCompactionAt *time.Time `json:"last_compaction_at,omitempty"`
	// InjectedBytes is the measured Cairn output since CountingSince.
	// It is a proxy for context growth.
	InjectedBytes      *int64 `json:"injected_bytes,omitempty"`
	InjectedDeliveries *int   `json:"injected_deliveries,omitempty"`
	InjectedRecalls    *int   `json:"injected_recalls,omitempty"`
	// CountingSince is the start of the interval InjectedBytes covers.
	CountingSince *time.Time `json:"counting_since,omitempty"`
	// NearLimit is set by NearLimitRule. Reasons says which part matched.
	NearLimit bool     `json:"near_limit"`
	Reasons   []string `json:"reasons,omitempty"`
}

func emptySwarmContextRoom(_ swarmScope) SwarmContextRoom {
	return SwarmContextRoom{
		NearLimitRule: fmt.Sprintf("Flag a session after at least %d KiB of measured Cairn output in the current interval.", swarmContextNearLimitKiB),
		ByAgent:       map[string]SwarmAgentContext{},
	}
}

// swarmContextRoomTx reads per-session observations in the snapshot transaction.
// PreCompact is a best-effort hook observation, seen on live Claude and Codex
// bindings. OpenCode and Hermes can emit it too. Post-compaction SessionStart
// updates the session counter for active campaign roots on bindings that emit
// that event. Agy and other sessions without either record show unknown.
func (s *Store) swarmContextRoomTx(ctx context.Context, tx pgx.Tx, sc swarmScope) (SwarmContextRoom, error) {
	out := emptySwarmContextRoom(sc)
	ids := make([]string, 0, len(sc.MemberPrincipals))
	for _, principal := range sc.MemberPrincipals {
		id := swarmAgentID(principal)
		if _, ok := sc.Sessions[id]; ok {
			ids = append(ids, id)
		}
	}
	if len(ids) == 0 {
		return out, nil
	}
	rows, err := tx.Query(ctx, swarmContextRoomSQL, sc.Repo, ids)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		var id string
		var observedAt *time.Time
		var entry SwarmAgentContext
		if err := rows.Scan(&id, &entry.Compactions, &entry.LastCompactionAt, &entry.CountingSince,
			&entry.InjectedBytes, &entry.InjectedDeliveries, &entry.InjectedRecalls, &observedAt); err != nil {
			return out, err
		}
		entry.Known = observedAt != nil || entry.Compactions != nil
		if !entry.Known {
			entry.CountingSince = nil
		}
		if entry.InjectedBytes != nil && *entry.InjectedBytes >= swarmContextNearLimitKiB*1024 {
			entry.NearLimit = true
			entry.Reasons = []string{fmt.Sprintf("Cairn has injected at least %d KiB in this interval", swarmContextNearLimitKiB)}
		}
		if observedAt != nil && (out.ObservedAt == nil || observedAt.After(*out.ObservedAt)) {
			out.ObservedAt = observedAt
		}
		out.ByAgent[id] = entry
	}
	return out, rows.Err()
}

// A successful ordinary recall meter measures the SessionStart or
// UserPromptSubmit hook's output. The inbox recall meter measures the rendered
// bridge output, including the control text it carries. Delivery rows can
// also report bytes. Prefer a measured delivery row when both sources measure
// the same delivery ID. Unmetered coordination cues and native memory reads
// are outside this total.
const swarmContextRoomSQL = `
WITH boundaries AS (
 SELECT a.agent_id, a.repo, a.native_session_id, a.metadata->>'harness' AS harness, a.started_at,
  a.first_compacted_at, capture.observed_at,
  NULLIF(GREATEST(a.compaction_count, capture.precompacts), 0) AS compactions,
  CASE
   WHEN a.compaction_count>1 AND capture.last_precompact_at<=a.first_compacted_at THEN NULL
   ELSE COALESCE(capture.last_precompact_at, CASE WHEN a.compaction_count=1 THEN a.first_compacted_at END)
  END AS last_compaction_at
 FROM cairn.agent_session a
 CROSS JOIN LATERAL (
  SELECT count(*) FILTER (WHERE c.hook_event='PreCompact') AS precompacts,
   max(c.observed_at) FILTER (WHERE c.hook_event='PreCompact') AS last_precompact_at,
   max(c.observed_at) AS observed_at
  FROM cairn.capture_observation c
  WHERE c.repo=a.repo AND c.native_session_id=a.native_session_id AND c.harness=a.metadata->>'harness'
   AND c.observed_at>=a.started_at
 ) capture
 WHERE a.repo=$1 AND a.agent_id=ANY($2::uuid[])
), sessions AS (
 SELECT *, COALESCE(last_compaction_at, CASE WHEN compactions IS NULL THEN started_at END) AS counting_since
 FROM boundaries
)
SELECT a.agent_id::text, a.compactions, a.last_compaction_at, a.counting_since,
 totals.injected_bytes, totals.deliveries, totals.recalls,
 GREATEST(a.observed_at, a.first_compacted_at, totals.observed_at)
FROM sessions a
CROSS JOIN LATERAL (
 WITH observations AS (
  SELECT r.observed_at,
   CASE WHEN r.method='cairn-lifecycle/inbox-recall-meter/1' THEN r.delivery_id END AS delivery_id,
   r.observation_id,
   CASE WHEN r.status='completed' AND COALESCE(r.injected_unit,'utf8_bytes')='utf8_bytes' THEN r.injected_bytes END AS measured_bytes,
   r.status='completed' AND r.method='cairn-lifecycle/inbox-recall-meter/1'
    AND r.delivery_id IS NOT NULL AND r.injected_bytes>0 AS delivered,
   r.status='completed' AND r.method<>'cairn-lifecycle/inbox-recall-meter/1' AS ordinary_recall,
   false AS delivery_row
  FROM cairn.recall_observation r
  WHERE r.repo=a.repo AND r.native_session_id=a.native_session_id AND r.harness=a.harness
   AND r.observed_at>=a.started_at
  UNION ALL
  SELECT d.observed_at, d.delivery_id, d.observation_id,
   CASE WHEN disposition.delivered AND COALESCE(d.injected_unit,'utf8_bytes')='utf8_bytes' THEN d.injected_bytes END,
   disposition.delivered, false, true
  FROM cairn.delivery_observation d
  CROSS JOIN LATERAL (
   SELECT d.disposition IN ('full','compact','digest') AND d.expansion_reason IS DISTINCT FROM 'digest_claim' AS delivered
  ) disposition
  WHERE d.repo=a.repo AND d.native_session_id=a.native_session_id AND d.harness=a.harness
   AND d.observed_at>=a.started_at
 ), current_interval AS (
  SELECT COALESCE(
    max(measured_bytes) FILTER (WHERE delivery_row),
    max(measured_bytes) FILTER (WHERE NOT delivery_row)
   ) AS measured_bytes,
   bool_or(delivered) AS delivered,
   bool_or(ordinary_recall AND measured_bytes IS NOT NULL) AS metered_recall,
   bool_or(ordinary_recall AND measured_bytes>0) AS injected_recall
  FROM observations
  WHERE observed_at>=a.counting_since AND observed_at IS DISTINCT FROM a.last_compaction_at
  GROUP BY delivery_id IS NOT NULL, COALESCE(delivery_id, observation_id)
 )
 SELECT sum(measured_bytes)::bigint AS injected_bytes,
  NULLIF(count(*) FILTER (WHERE delivered), 0) AS deliveries,
  CASE WHEN bool_or(metered_recall) THEN count(*) FILTER (WHERE injected_recall) END AS recalls,
  (SELECT max(observed_at) FROM observations) AS observed_at
 FROM current_interval
) totals`
