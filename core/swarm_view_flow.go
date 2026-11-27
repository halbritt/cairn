package core

import (
	"cmp"
	"context"
	"fmt"
	"slices"
	"time"

	"github.com/jackc/pgx/v5"
)

// Message flow of a campaign (CAIRN-830): the delivery state of each
// recipient and the registered waits. Deliveries and waits are current state,
// not a log, so they are part of the snapshot.
//
// The types below are the wire contract of snapshot.flow. The page and the
// other sections read them. Add a field when a reader needs one; do not
// rename or remove one without the lead.

// SwarmFlow is snapshot.flow.
type SwarmFlow struct {
	// WaitsFeature is the status of coordination-waits/1: active, disabled
	// or retired. Agents can only register waits while it is active.
	WaitsFeature string `json:"waits_feature"`
	// Deliveries includes open deliveries of campaign events or to members,
	// and deliveries in the newest SwarmFlowEventWindow campaign events.
	Deliveries          []SwarmDelivery `json:"deliveries"`
	DeliveriesTruncated bool            `json:"deliveries_truncated,omitempty"`
	// Waits holds every wait that is still waiting, and recently resolved
	// waits whose owner or dependency belongs to the campaign.
	Waits          []SwarmWait `json:"waits"`
	WaitsTruncated bool        `json:"waits_truncated,omitempty"`
	// ByMember is keyed by member principal.
	ByMember map[string]SwarmMemberFlow `json:"by_member"`
}

// SwarmFlowEventWindow is how many of the newest campaign events keep their
// terminal deliveries in the snapshot. The page keeps the same number of log
// events.
const SwarmFlowEventWindow = 300

const (
	swarmFlowDeliveryLimit     = 2000
	swarmFlowResolvedWaitLimit = 300
)

// SwarmDelivery is one recipient's delivery of one campaign event. The
// envelope is repeated so that a row renders without the event log.
type SwarmDelivery struct {
	DeliveryID    string    `json:"delivery_id"`
	EventID       string    `json:"event_id"`
	EventPosition int64     `json:"event_position"`
	Kind          string    `json:"kind"`
	From          string    `json:"from"`
	Consumer      string    `json:"consumer"`
	Summary       string    `json:"summary,omitempty"`
	Action        string    `json:"action,omitempty"`
	Blocker       bool      `json:"blocker,omitempty"`
	CausationID   string    `json:"causation_id,omitempty"`
	CreatedAt     time.Time `json:"created_at"`
	// State is the stored state: pending, leased, handled, ignored, failed.
	State string `json:"state"`
	// Display tells the states apart for the page: pending, leased, held,
	// settled, superseded, ignored, failed, cancelled, expired.
	//   held       an unfinished wake or native attempt holds the delivery
	//   settled    handled
	//   superseded ignored with code superseded
	//   cancelled  failed with operator_cancelled, operator_released or consumer_retired
	//   expired    failed with admission_expired or task_deadline
	Display      string     `json:"display"`
	Code         string     `json:"code,omitempty"`
	Held         bool       `json:"held,omitempty"`
	Attempts     int        `json:"attempts"`
	AvailableAt  *time.Time `json:"available_at,omitempty"`
	LeaseUntil   *time.Time `json:"lease_until,omitempty"`
	CompletedAt  *time.Time `json:"completed_at,omitempty"`
	SupersededBy string     `json:"superseded_by,omitempty"`
	// Seen is false until the recipient has claimed the delivery at least
	// once. An owner action is shown as sent and not yet seen while false.
	Seen bool `json:"seen"`
	// Replied is set for a request when a response event from this consumer
	// names the request as its causation.
	Replied bool `json:"replied,omitempty"`
}

// SwarmWait is one registered wait. A wait joins to its request by
// DependencyEventID, never by correlation alone.
type SwarmWait struct {
	WaitID string `json:"wait_id"`
	// Owner is the principal that waits.
	Owner string `json:"owner"`
	// Predicate is request_response (a direct reply) or
	// response_group_closed (a group closure). The page labels them apart.
	Predicate         string     `json:"predicate"`
	DependencyEventID string     `json:"dependency_event_id"`
	DependencySummary string     `json:"dependency_summary,omitempty"`
	State             string     `json:"state"`
	TerminalReason    string     `json:"terminal_reason,omitempty"`
	Deadline          time.Time  `json:"deadline"`
	Overdue           bool       `json:"overdue,omitempty"`
	CreatedAt         time.Time  `json:"created_at"`
	ResolvedAt        *time.Time `json:"resolved_at,omitempty"`
	// Awaited lists the recipients of the dependency that have not replied
	// while the wait is waiting. They are the agents the owner waits on.
	Awaited []string `json:"awaited"`
}

// SwarmMemberFlow is what one member is waiting on and what waits on it.
type SwarmMemberFlow struct {
	Pending int `json:"pending"`
	Leased  int `json:"leased"`
	Held    int `json:"held"`
	Failed  int `json:"failed"`
	// OldestPending is the delivery ID of the oldest eligible delivery of
	// the member, as its inbox would claim it. Queue age does not prove
	// that a peer is blocked: PeerWaits says where a peer really waits.
	OldestPending string `json:"oldest_pending,omitempty"`
	// WaitsOn lists the waiting waits this member owns.
	WaitsOn []string `json:"waits_on"`
	// PeerWaits lists the waiting waits whose dependency this member has
	// not answered.
	PeerWaits []string `json:"peer_waits"`
}

func emptySwarmFlow() SwarmFlow {
	return SwarmFlow{WaitsFeature: "disabled", Deliveries: []SwarmDelivery{}, Waits: []SwarmWait{}, ByMember: map[string]SwarmMemberFlow{}}
}

// swarmFlowTx reads all flow panels from the caller's snapshot.
func (s *Store) swarmFlowTx(ctx context.Context, tx pgx.Tx, sc swarmScope) (SwarmFlow, error) {
	flow := emptySwarmFlow()
	status, err := s.getWorkFeatureStatusTx(ctx, tx, "coordination-waits/1")
	if err != nil {
		return flow, fmt.Errorf("read waits feature: %w", err)
	}
	flow.WaitsFeature = status
	oldest, err := swarmFlowMembers(ctx, tx, sc, flow.ByMember)
	if err != nil {
		return flow, fmt.Errorf("read member inboxes: %w", err)
	}
	flow.Deliveries, flow.DeliveriesTruncated, err = swarmFlowDeliveries(ctx, tx, sc, oldest)
	if err != nil {
		return flow, fmt.Errorf("read deliveries: %w", err)
	}
	flow.Waits, flow.WaitsTruncated, err = swarmFlowWaits(ctx, tx, sc)
	if err != nil {
		return flow, fmt.Errorf("read waits: %w", err)
	}
	if err := swarmFlowReplies(ctx, tx, sc.Repo, &flow); err != nil {
		return flow, fmt.Errorf("read replies: %w", err)
	}
	return flow, nil
}

const swarmFlowHeld = `NOT (` + wakeHold + ` AND ` + sessionInboxHold + `)`

func swarmFlowMembers(ctx context.Context, tx pgx.Tx, sc swarmScope, members map[string]SwarmMemberFlow) ([]string, error) {
	// Count inbox rows independently of the delivery window. Use the same
	// admission and hold predicates as the inbox when choosing its oldest row.
	rows, err := tx.Query(ctx, `
WITH inbox AS MATERIALIZED (
	SELECT d.consumer, d.delivery_id, e.position, d.state, `+swarmFlowHeld+` AS held,
		((e.sensitivity='shareable' OR COALESCE(a.visibility='local',false))
		 AND d.available_at<=$3 AND e.task_deadline IS NULL AND `+requestAdmissionOpen+`
		 AND `+waitNotificationAdmissionOpen+`
		 AND (d.state='pending' OR (d.state='leased' AND d.lease_until<=$3))) AS eligible
	FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)
	LEFT JOIN cairn.agent_session a ON a.repo=$1 AND 'agent/'||a.agent_id::text=d.consumer
	WHERE e.repo=$1 AND d.consumer=ANY($2)
		AND (d.state IN ('pending','leased','failed') OR `+swarmFlowHeld+`)
), counts AS (
	SELECT consumer, count(*) FILTER (WHERE state='pending' AND NOT held) AS pending,
		count(*) FILTER (WHERE state='leased' AND NOT held) AS leased,
		count(*) FILTER (WHERE held) AS held,
		count(*) FILTER (WHERE state='failed' AND NOT held) AS failed
	FROM inbox GROUP BY consumer
), oldest AS (
	SELECT DISTINCT ON (consumer) consumer, delivery_id
	FROM inbox WHERE eligible AND NOT held ORDER BY consumer, position
)
SELECT m.consumer, COALESCE(c.pending,0), COALESCE(c.leased,0), COALESCE(c.held,0), COALESCE(c.failed,0),
	COALESCE(o.delivery_id::text,'')
FROM unnest($2::text[]) AS m(consumer) LEFT JOIN counts c USING(consumer) LEFT JOIN oldest o USING(consumer)`,
		sc.Repo, sc.MemberPrincipals, sc.Now)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	oldest := []string{}
	for rows.Next() {
		var principal string
		m := SwarmMemberFlow{WaitsOn: []string{}, PeerWaits: []string{}}
		if err := rows.Scan(&principal, &m.Pending, &m.Leased, &m.Held, &m.Failed, &m.OldestPending); err != nil {
			return nil, err
		}
		members[principal] = m
		if m.OldestPending != "" {
			oldest = append(oldest, m.OldestPending)
		}
	}
	return oldest, rows.Err()
}

func swarmFlowDeliveries(ctx context.Context, tx pgx.Tx, sc swarmScope, oldest []string) ([]SwarmDelivery, bool, error) {
	// Prefer the named inbox heads and other open rows if the delivery limit
	// cuts the window. Return the retained rows in event order for the page.
	rows, err := tx.Query(ctx, `
WITH recent AS MATERIALIZED (
	SELECT e.event_id FROM cairn.agent_event e WHERE `+swarmEventInCampaign+`
	ORDER BY e.position DESC LIMIT $5
)
	SELECT d.delivery_id::text, e.event_id::text, e.position, e.kind, e.publisher, d.consumer,
		COALESCE(e.summary,''), COALESCE(e.notice_action,''), e.blocker, COALESCE(e.causation_id::text,''), e.created_at,
		d.state, d.code, h.held, d.attempts, d.available_at, d.lease_until, d.completed_at, COALESCE(d.superseded_by::text,'')
	FROM cairn.agent_event e JOIN cairn.agent_delivery d USING(event_id)
	CROSS JOIN LATERAL (SELECT `+swarmFlowHeld+` AS held) h
	WHERE e.repo=$1 AND (e.event_id IN (SELECT event_id FROM recent)
		OR ((d.state IN ('pending','leased') OR h.held) AND (d.consumer=ANY($6) OR (`+swarmEventInCampaign+`))))
	ORDER BY (d.delivery_id=ANY($7::uuid[])) DESC, (d.state IN ('pending','leased') OR h.held) DESC,
		e.position DESC, d.consumer
	LIMIT $8`,
		sc.Repo, sc.Principals, sc.Campaign.Campaign, sc.Campaign.CreatedAt,
		SwarmFlowEventWindow, sc.MemberPrincipals, oldest, swarmFlowDeliveryLimit+1)
	if err != nil {
		return nil, false, err
	}
	defer rows.Close()
	out := []SwarmDelivery{}
	for rows.Next() {
		var d SwarmDelivery
		if err := rows.Scan(&d.DeliveryID, &d.EventID, &d.EventPosition, &d.Kind, &d.From, &d.Consumer,
			&d.Summary, &d.Action, &d.Blocker, &d.CausationID, &d.CreatedAt, &d.State, &d.Code, &d.Held,
			&d.Attempts, &d.AvailableAt, &d.LeaseUntil, &d.CompletedAt, &d.SupersededBy); err != nil {
			return nil, false, err
		}
		d.Display = swarmDeliveryDisplay(d)
		d.Seen = d.Attempts > 0
		out = append(out, d)
	}
	if err := rows.Err(); err != nil {
		return nil, false, err
	}
	truncated := len(out) > swarmFlowDeliveryLimit
	if truncated {
		out = out[:swarmFlowDeliveryLimit]
	}
	slices.SortFunc(out, func(a, b SwarmDelivery) int {
		if a.EventPosition != b.EventPosition {
			return cmp.Compare(b.EventPosition, a.EventPosition)
		}
		return cmp.Compare(a.Consumer, b.Consumer)
	})
	return out, truncated, nil
}

func swarmDeliveryDisplay(d SwarmDelivery) string {
	switch {
	case d.Held:
		return "held"
	case d.State == "handled":
		return "settled"
	case d.State == "ignored" && d.Code == "superseded":
		return "superseded"
	case d.State == "failed":
		switch d.Code {
		case "operator_cancelled", "operator_released", "consumer_retired":
			return "cancelled"
		case "admission_expired", "task_deadline":
			return "expired"
		}
	}
	return d.State
}

// swarmWaitInCampaign uses the wait as w and its dependency event as e.
// Parameters $1–$4 match swarmEventInCampaign.
// A delivery alone can bring a peer's wait into scope while both are open.
func swarmWaitInCampaign() string {
	return `w.collection=$1 AND e.repo=$1 AND (
	w.owner_principal=ANY($2) OR (` + swarmEventInCampaign + `)
	OR (w.state='waiting' AND EXISTS(
		SELECT 1 FROM cairn.agent_delivery d
		JOIN cairn.agent_team_member m ON m.repo=$1 AND m.campaign=$3 AND m.member=d.consumer AND m.active
		WHERE d.event_id=e.event_id AND (d.state IN ('pending','leased') OR ` + swarmFlowHeld + `))))`
}

func swarmFlowWaits(ctx context.Context, tx pgx.Tx, sc swarmScope) ([]SwarmWait, bool, error) {
	rows, err := tx.Query(ctx, `
WITH relevant AS (
	SELECT w.wait_uuid, w.owner_principal, w.predicate_kind, w.dependency_event_uuid,
		COALESCE(e.summary,'') AS summary, w.state, w.terminal_reason, w.deadline, w.created_at, w.resolved_at
	FROM cairn.agent_wait w JOIN cairn.agent_event e ON e.event_id=w.dependency_event_uuid
	WHERE `+swarmWaitInCampaign()+`
), selected AS (
	SELECT * FROM relevant WHERE state='waiting'
	UNION ALL
	(SELECT * FROM relevant WHERE state<>'waiting' ORDER BY resolved_at DESC, wait_uuid DESC LIMIT $5)
)
SELECT wait_uuid::text, owner_principal, predicate_kind, dependency_event_uuid::text, summary, state, terminal_reason,
	deadline, created_at, resolved_at,
	CASE WHEN state='waiting' THEN ARRAY(SELECT d.consumer FROM cairn.agent_delivery d
		WHERE d.event_id=dependency_event_uuid ORDER BY d.consumer) ELSE ARRAY[]::text[] END
FROM selected ORDER BY resolved_at DESC NULLS FIRST, CASE WHEN resolved_at IS NULL THEN deadline END, wait_uuid DESC`,
		sc.Repo, sc.Principals, sc.Campaign.Campaign, sc.Campaign.CreatedAt, swarmFlowResolvedWaitLimit+1)
	if err != nil {
		return nil, false, err
	}
	defer rows.Close()
	out := []SwarmWait{}
	resolved := 0
	for rows.Next() {
		var w SwarmWait
		if err := rows.Scan(&w.WaitID, &w.Owner, &w.Predicate, &w.DependencyEventID, &w.DependencySummary, &w.State,
			&w.TerminalReason, &w.Deadline, &w.CreatedAt, &w.ResolvedAt, &w.Awaited); err != nil {
			return nil, false, err
		}
		if w.State == WaitStateWaiting {
			w.Overdue = !w.Deadline.After(sc.Now)
		} else {
			resolved++
			if resolved > swarmFlowResolvedWaitLimit {
				continue
			}
		}
		out = append(out, w)
	}
	return out, resolved > swarmFlowResolvedWaitLimit, rows.Err()
}

func swarmFlowReplies(ctx context.Context, tx pgx.Tx, repo string, flow *SwarmFlow) error {
	// Use a set of request IDs so one read can find replies for both panels,
	// including replies that are older than the visible event window.
	requests := map[string]struct{}{}
	for _, d := range flow.Deliveries {
		if d.Kind == "request" {
			requests[d.EventID] = struct{}{}
		}
	}
	for _, w := range flow.Waits {
		if w.State == WaitStateWaiting {
			requests[w.DependencyEventID] = struct{}{}
		}
	}
	ids := make([]string, 0, len(requests))
	for id := range requests {
		ids = append(ids, id)
	}
	type replyKey struct{ request, consumer string }
	replies := map[replyKey]bool{}
	if len(ids) > 0 {
		rows, err := tx.Query(ctx, `SELECT DISTINCT causation_id::text, publisher FROM cairn.agent_event
			WHERE repo=$1 AND kind='response' AND causation_id=ANY($2::uuid[])`, repo, ids)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var key replyKey
			if err := rows.Scan(&key.request, &key.consumer); err != nil {
				return err
			}
			replies[key] = true
		}
		if err := rows.Err(); err != nil {
			return err
		}
	}
	for i := range flow.Deliveries {
		d := &flow.Deliveries[i]
		d.Replied = d.Kind == "request" && replies[replyKey{d.EventID, d.Consumer}]
	}
	for i := range flow.Waits {
		w := &flow.Waits[i]
		if w.State != WaitStateWaiting {
			continue
		}
		w.Awaited = slices.DeleteFunc(w.Awaited, func(recipient string) bool {
			return replies[replyKey{w.DependencyEventID, recipient}]
		})
		if m, ok := flow.ByMember[w.Owner]; ok {
			m.WaitsOn = append(m.WaitsOn, w.WaitID)
			flow.ByMember[w.Owner] = m
		}
		for _, recipient := range w.Awaited {
			if m, ok := flow.ByMember[recipient]; ok {
				m.PeerWaits = append(m.PeerWaits, w.WaitID)
				flow.ByMember[recipient] = m
			}
		}
	}
	return nil
}
