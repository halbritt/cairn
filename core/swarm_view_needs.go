package core

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

// What needs the owner (CAIRN-831). The list holds only work that the owner
// can advance, built from signals the store already has, and each row names
// the action the owner can take.
//
// The types below are the wire contract of snapshot.needs_you. Add a field
// when a reader needs one; do not rename or remove one without the lead.

// SwarmNeedsYou is snapshot.needs_you, and landing.needs_you for rows that
// belong to no campaign.
type SwarmNeedsYou struct {
	// Items are ordered by Waiters, most first, then oldest first.
	Items []SwarmNeed `json:"items"`
	// LeadDecisions is keyed by member principal. A decision owned by a
	// lead is shown on that lead's row and is not an item.
	LeadDecisions map[string][]SwarmLeadDecision `json:"lead_decisions"`
	// Unavailable names a source that could not be read. An empty list and
	// an unreadable source are shown differently.
	Unavailable []SwarmNeedSource `json:"unavailable,omitempty"`
}

// Need types.
const (
	SwarmNeedBoardQuestion      = "board_question"      // a request on the agent-board topic with no response
	SwarmNeedOperatorDelivery   = "operator_delivery"   // an event addressed to the operator principal
	SwarmNeedDecision           = "decision"            // an open decision owned by the root or the operator
	SwarmNeedEscalation         = "escalation"          // an escalated blocker
	SwarmNeedWorkstreamDecision = "workstream_decision" // a workstream with decision_needed set
	SwarmNeedHandoffDue         = "handoff_due"         // the coordinator is past its age or compaction threshold
	SwarmNeedOwnerTurn          = "owner_turn"          // host condition owner_turn_required
	SwarmNeedFailedDelivery     = "failed_delivery"     // failed deliveries, grouped by cause
	SwarmNeedStalledAgent       = "stalled_agent"       // pending_starved or hold_stalled
	SwarmNeedStoppedBacklog     = "stopped_backlog"     // work sent to a stopped session
	SwarmNeedWaitTimedOut       = "wait_timed_out"      // a wait that timed out
	SwarmNeedMissedCheckpoint   = "missed_checkpoint"   // an overdue checkpoint
)

// SwarmNeed is one row of the list.
type SwarmNeed struct {
	// ID is stable while the cause stands, so the page can keep a row open
	// across snapshots: "<type>:<key>".
	ID   string `json:"id"`
	Type string `json:"type"`
	// Title is one line. Detail is the reason the store recorded.
	Title  string `json:"title"`
	Detail string `json:"detail,omitempty"`
	// Subject is the principal the row is about: the sender of a question,
	// the recipient of a failed delivery, the stalled session.
	Subject string `json:"subject,omitempty"`
	// System identifies a Cairn publication when the subject is its sender.
	System string `json:"system,omitempty"`
	// Since is omitted when the store cannot determine when the need began.
	Since time.Time `json:"since,omitzero"`
	// ExpiresAt is set when the board question has a known expiry.
	ExpiresAt *time.Time `json:"expires_at,omitempty"`
	// Action is what the owner can do about the row.
	Action SwarmNeedAction `json:"action"`
	// InboxHead marks the operator delivery currently first in claim order.
	InboxHead bool `json:"inbox_head,omitempty"`
	// Waiters counts the sessions with a registered wait directly on the
	// row's event. One hop only.
	Waiters int `json:"waiters"`
	// Count is the number of deliveries represented by a grouped row.
	Count int           `json:"count,omitempty"`
	Refs  SwarmNeedRefs `json:"refs"`
}

// SwarmNeedAction names the owner's next step. Kind is one of: answer_board,
// acknowledge, reply, decide, hand_off, take_turn, cancel_delivery,
// cancel_wait, inspect.
type SwarmNeedAction struct {
	Kind  string `json:"kind"`
	Label string `json:"label"`
	// Command is a shell command verified against the local operator CLI.
	// It is absent when there is no local operator command for this action.
	Command string `json:"command,omitempty"`
}

// SwarmNeedRefs points at the stored rows behind a need.
type SwarmNeedRefs struct {
	// Principal is the person the owner can contact for this action.
	Principal string `json:"principal,omitempty"`
	// EventIDs identifies all events used to count direct waiters for a grouped row.
	EventIDs    []string          `json:"event_ids,omitempty"`
	EventID     string            `json:"event_id,omitempty"`
	DeliveryIDs []string          `json:"delivery_ids,omitempty"`
	WaitID      string            `json:"wait_id,omitempty"`
	LedgerKind  string            `json:"ledger_kind,omitempty"`
	LedgerKey   string            `json:"ledger_key,omitempty"`
	Stream      string            `json:"stream,omitempty"`
	Record      *RecordVersionRef `json:"record,omitempty"`
	Code        string            `json:"code,omitempty"`
}

// SwarmLeadDecision is an open decision owned by a lead.
type SwarmLeadDecision struct {
	ItemKey   string    `json:"item_key"`
	Summary   string    `json:"summary"`
	UpdatedAt time.Time `json:"updated_at"`
}

// SwarmNeedSource names an attention source and why it could not be read.
type SwarmNeedSource struct {
	Type   string `json:"type"`
	Reason string `json:"reason"`
}

func emptySwarmNeedsYou() SwarmNeedsYou {
	return SwarmNeedsYou{Items: []SwarmNeed{}, LeadDecisions: map[string][]SwarmLeadDecision{}}
}

// swarmNeedsReader uses the same read-only snapshot for every source. A landing
// read inverts membership against the active campaign principals.
type swarmNeedsReader struct {
	repo, operator string
	now            time.Time
	generation     int64
	principals     []string
	landing        bool
	out            SwarmNeedsYou
}

func (r *swarmNeedsReader) read(ctx context.Context, tx pgx.Tx, sources []string, fn func(pgx.Tx) ([]SwarmNeed, error)) {
	errs := map[string]string{}
	var items []SwarmNeed
	swarmSection(ctx, tx, errs, sources[0], func(sp pgx.Tx) error {
		var err error
		items, err = fn(sp)
		return err
	})
	if reason := errs[sources[0]]; reason != "" {
		for _, source := range sources {
			r.out.Unavailable = append(r.out.Unavailable, SwarmNeedSource{source, reason})
		}
		return
	}
	r.out.Items = append(r.out.Items, items...)
}

func (r *swarmNeedsReader) readShared(ctx context.Context, tx pgx.Tx, sc swarmScope) {
	r.read(ctx, tx, []string{SwarmNeedBoardQuestion}, func(sp pgx.Tx) ([]SwarmNeed, error) {
		return r.board(ctx, sp)
	})
	r.read(ctx, tx, []string{SwarmNeedOperatorDelivery}, func(sp pgx.Tx) ([]SwarmNeed, error) {
		return r.operatorDeliveries(ctx, sp, sc)
	})
	r.read(ctx, tx, []string{SwarmNeedWorkstreamDecision}, func(sp pgx.Tx) ([]SwarmNeed, error) {
		return r.workstreams(ctx, sp)
	})
	r.read(ctx, tx, []string{SwarmNeedOwnerTurn}, func(sp pgx.Tx) ([]SwarmNeed, error) {
		return r.ownerTurns(ctx, sp)
	})
	r.read(ctx, tx, []string{SwarmNeedFailedDelivery}, func(sp pgx.Tx) ([]SwarmNeed, error) {
		return r.failedDeliveries(ctx, sp)
	})
	r.read(ctx, tx, []string{SwarmNeedStalledAgent, SwarmNeedStoppedBacklog}, func(sp pgx.Tx) ([]SwarmNeed, error) {
		return r.sessionBacklog(ctx, sp)
	})
	r.read(ctx, tx, []string{SwarmNeedWaitTimedOut}, func(sp pgx.Tx) ([]SwarmNeed, error) {
		return r.timedOutWaits(ctx, sp)
	})
	r.read(ctx, tx, []string{SwarmNeedMissedCheckpoint}, func(sp pgx.Tx) ([]SwarmNeed, error) {
		return r.missedCheckpoints(ctx, sp)
	})
}

func (s *Store) swarmNeedsYouTx(ctx context.Context, tx pgx.Tx, sc swarmScope, snap SwarmSnapshot) (SwarmNeedsYou, error) {
	r := swarmNeedsReader{repo: sc.Repo, operator: sc.Operator, now: sc.Now, generation: sc.Generation,
		principals: sc.Principals, out: emptySwarmNeedsYou()}
	leads := map[string]bool{}
	for _, member := range sc.Campaign.Members {
		if member.Role == "lead" {
			leads[member.Member] = true
		}
	}
	r.read(ctx, tx, []string{SwarmNeedDecision, SwarmNeedEscalation}, func(sp pgx.Tx) ([]SwarmNeed, error) {
		return r.ledger(ctx, sp, sc.Campaign.Campaign, sc.Campaign.RootAgent, sc.isMember, leads)
	})
	if handoff := snap.Handoff; handoff != nil {
		r.read(ctx, tx, []string{SwarmNeedHandoffDue}, func(sp pgx.Tx) ([]SwarmNeed, error) {
			return swarmNeedHandoff(ctx, sp, sc, *handoff)
		})
	}
	r.readShared(ctx, tx, sc)
	if snap.Flow.WaitsTruncated {
		r.out.Unavailable = append(r.out.Unavailable, SwarmNeedSource{"waiters", "Campaign wait counts may be incomplete"})
	}
	orderSwarmNeeds(r.out.Items, snap.Flow.Waits)
	return r.out, nil
}

func swarmNeedHandoff(ctx context.Context, tx pgx.Tx, sc swarmScope, handoff SwarmHandoffDue) ([]SwarmNeed, error) {
	var since time.Time
	if handoff.Since != nil {
		since = *handoff.Since
	}
	principal := handoff.HolderPrincipal
	label := principal
	if session, ok := sc.Sessions[swarmAgentID(principal)]; ok {
		label = session.DisplayName
		// Only the first compaction has its own timestamp in the store.
		if limit := sc.Campaign.MaxCoordinatorCompactions; limit != nil && *limit == 1 && session.CompactionCount >= 1 {
			var first *time.Time
			err := tx.QueryRow(ctx, `SELECT first_compacted_at FROM cairn.agent_session WHERE repo=$1 AND agent_id=$2`,
				sc.Repo, session.AgentID).Scan(&first)
			if err != nil {
				return nil, err
			}
			if first != nil && !first.IsZero() && !first.After(sc.Now) && (since.IsZero() || first.Before(since)) {
				since = *first
			}
		}
	}
	return []SwarmNeed{{
		ID: SwarmNeedHandoffDue + ":" + principal, Type: SwarmNeedHandoffDue,
		Title: "Coordinator handoff due for " + label, Detail: strings.Join(handoff.Reasons, ". "),
		Subject: principal, Since: since, Refs: SwarmNeedRefs{Principal: principal},
		Action: SwarmNeedAction{"hand_off", "Review the ledger and arrange a handoff with the coordinator",
			"cairn ledger handoff-check --operator --repo " + swarmNeedShellArg(sc.Repo) + " --campaign " + swarmNeedShellArg(sc.Campaign.Campaign)},
	}}, nil
}

func (s *Store) swarmLandingNeedsYouTx(ctx context.Context, tx pgx.Tx, repo string, now time.Time, generation int64, campaignPrincipals map[string]bool) (SwarmNeedsYou, error) {
	principals := make([]string, 0, len(campaignPrincipals))
	for principal := range campaignPrincipals {
		principals = append(principals, principal)
	}
	r := swarmNeedsReader{repo: repo, operator: s.channel.Principal, now: now, generation: generation,
		principals: principals, landing: true, out: emptySwarmNeedsYou()}
	r.read(ctx, tx, []string{SwarmNeedDecision, SwarmNeedEscalation}, func(sp pgx.Tx) ([]SwarmNeed, error) {
		rows, err := sp.Query(ctx, `SELECT c.campaign,c.root_agent FROM cairn.agent_team_campaign c
			WHERE c.repo=$1 AND NOT c.active AND EXISTS (
				SELECT 1 FROM cairn.campaign_ledger_item i WHERE i.repo=c.repo AND i.campaign=c.campaign
				AND NOT (i.owner=ANY($2::text[])) AND ((i.kind='decision' AND i.status='open' AND i.owner IN (c.root_agent,$3))
					OR (i.kind='blocker' AND i.status='escalated')))`, repo, principals, r.operator)
		if err != nil {
			return nil, err
		}
		type campaign struct{ name, root string }
		campaigns, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (campaign, error) {
			var c campaign
			err := row.Scan(&c.name, &c.root)
			return c, err
		})
		if err != nil {
			return nil, err
		}
		var items []SwarmNeed
		for _, c := range campaigns {
			needs, err := r.ledger(ctx, sp, c.name, c.root, func(p string) bool { return !campaignPrincipals[p] }, nil)
			if err != nil {
				return nil, err
			}
			items = append(items, needs...)
		}
		return items, nil
	})
	r.readShared(ctx, tx, swarmScope{Repo: repo})
	var ids []string
	for _, item := range r.out.Items {
		ids = append(ids, swarmNeedEvents(item)...)
	}
	var waits []SwarmWait
	r.read(ctx, tx, []string{"waiters"}, func(sp pgx.Tx) ([]SwarmNeed, error) {
		if len(ids) == 0 {
			return nil, nil
		}
		rows, err := sp.Query(ctx, `SELECT owner_principal, dependency_event_uuid::text FROM cairn.agent_wait
			WHERE collection=$1 AND generation=$2 AND state='waiting' AND dependency_event_uuid=ANY($3::uuid[])`, repo, generation, ids)
		if err != nil {
			return nil, err
		}
		waits, err = pgx.CollectRows(rows, func(row pgx.CollectableRow) (SwarmWait, error) {
			wait := SwarmWait{State: "waiting"}
			err := row.Scan(&wait.Owner, &wait.DependencyEventID)
			return wait, err
		})
		return nil, err
	})
	orderSwarmNeeds(r.out.Items, waits)
	return r.out, nil
}

func swarmNeedEvents(item SwarmNeed) []string {
	if len(item.Refs.EventIDs) > 0 {
		return item.Refs.EventIDs
	}
	if item.Refs.EventID != "" {
		return []string{item.Refs.EventID}
	}
	return nil
}

func orderSwarmNeeds(items []SwarmNeed, waits []SwarmWait) {
	byEvent := map[string]map[string]bool{}
	for _, wait := range waits {
		if wait.State != "waiting" || wait.DependencyEventID == "" {
			continue
		}
		if byEvent[wait.DependencyEventID] == nil {
			byEvent[wait.DependencyEventID] = map[string]bool{}
		}
		byEvent[wait.DependencyEventID][wait.Owner] = true
	}
	for i := range items {
		sessions := map[string]bool{}
		for _, event := range swarmNeedEvents(items[i]) {
			for owner := range byEvent[event] {
				sessions[owner] = true
			}
		}
		items[i].Waiters = len(sessions)
	}
	sort.Slice(items, func(i, j int) bool {
		a, b := items[i], items[j]
		if a.Waiters != b.Waiters {
			return a.Waiters > b.Waiters
		}
		if a.Since.IsZero() != b.Since.IsZero() {
			return !a.Since.IsZero()
		}
		if !a.Since.Equal(b.Since) {
			return a.Since.Before(b.Since)
		}
		return a.ID < b.ID
	})
}

func swarmNeedShellArg(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "'\"'\"'") + "'"
}

func swarmNeedReviewCommand(repo, delivery string) string {
	payload, _ := json.Marshal(struct {
		Repo     string `json:"repo"`
		Delivery string `json:"delivery_id"`
	}{repo, delivery})
	return "printf '%s\\n' " + swarmNeedShellArg(string(payload)) + " | cairn coordination-review"
}

func (r *swarmNeedsReader) ledger(ctx context.Context, tx pgx.Tx, campaign, root string, belongs func(string) bool, leads map[string]bool) ([]SwarmNeed, error) {
	items, err := loadCampaignLedgerItemsTx(ctx, tx, r.repo, campaign, true)
	if err != nil {
		return nil, err
	}
	var needs []SwarmNeed
	for _, item := range items {
		if !belongs(item.Owner) && !(item.Owner == r.operator && !r.landing) {
			continue
		}
		var kind, label string
		switch {
		case item.Kind == "decision" && item.Status == "open":
			if item.Owner != root && item.Owner != r.operator {
				if leads[item.Owner] {
					r.out.LeadDecisions[item.Owner] = append(r.out.LeadDecisions[item.Owner], SwarmLeadDecision{item.ItemKey, item.Summary, item.UpdatedAt})
				}
				continue
			}
			kind, label = SwarmNeedDecision, "Decide and record the outcome in the campaign ledger"
		case item.Kind == "blocker" && item.Status == "escalated":
			kind, label = SwarmNeedEscalation, "Resolve the escalation with the ledger item owner"
		default:
			continue
		}
		refs := SwarmNeedRefs{Principal: item.Owner, LedgerKind: item.Kind, LedgerKey: item.ItemKey, EventID: item.EventID, Record: item.Source}
		if item.DeliveryID != "" {
			refs.DeliveryIDs = []string{item.DeliveryID}
		}
		command := "cairn ledger get --operator --repo " + swarmNeedShellArg(r.repo) + " --campaign " + swarmNeedShellArg(campaign)
		needs = append(needs, SwarmNeed{ID: kind + ":" + campaign + "/" + item.ItemKey, Type: kind,
			Title: item.Summary, Detail: item.Detail, Subject: item.Owner, Since: item.UpdatedAt,
			Action: SwarmNeedAction{"decide", label, command}, Refs: refs})
	}
	return needs, nil
}

const swarmTimedOutWaitAge = 24 * time.Hour

// This is agent-board/server/cards.py's BLOCK at 5ccacf9. PostgreSQL returns
// only the first capture. Neither the note's prose nor the block is exported.
const swarmBoardCardFence = "^```board-card[ \t]*?\n(.*?)\n```[ \t]*$"
const swarmBoardCardBlock = "(?w)" + swarmBoardCardFence

func (r *swarmNeedsReader) board(ctx context.Context, tx pgx.Tx) ([]SwarmNeed, error) {
	// Select envelopes before reading card metadata. Answered requests only
	// supply slots for senders with unanswered requests inside the horizon.
	// Only the fenced block is read in this transaction, and answered
	// requests contribute no row, expiry, default or source to the snapshot.
	rows, err := tx.Query(ctx, `WITH questions AS MATERIALIZED (
		SELECT e.event_id,e.repo,e.publisher,e.created_at,e.record_id,e.version,e.summary,e.kind,e.position,e.destination_name,e.causation_id,
			EXISTS (SELECT 1 FROM cairn.agent_event reply WHERE reply.repo=e.repo AND reply.kind='response' AND reply.causation_id=e.event_id) AS answered
		FROM cairn.agent_event e
		WHERE e.repo=$1 AND e.destination_type='topic' AND e.destination_name='agent-board' AND e.kind='request'
		AND e.created_at>$4 AND (e.publisher=ANY($2::text[])) <> $3
	), unanswered_senders AS MATERIALIZED (
		SELECT publisher, min(position) AS oldest FROM questions WHERE NOT answered GROUP BY publisher
	), candidates AS MATERIALIZED (
		SELECT q.* FROM questions q JOIN unanswered_senders u ON u.publisher=q.publisher
		WHERE NOT q.answered OR q.position>u.oldest
		UNION ALL
		SELECT e.event_id,e.repo,e.publisher,e.created_at,e.record_id,e.version,e.summary,e.kind,e.position,e.destination_name,e.causation_id,false
		FROM cairn.agent_event e JOIN unanswered_senders u ON u.publisher=e.publisher AND u.oldest<e.position
		WHERE e.repo=$1 AND e.destination_type='topic' AND e.destination_name='agent-board' AND e.kind<>'request' AND e.created_at>$4
	)
	SELECT e.event_id::text,e.publisher,e.created_at,e.record_id::text,e.version,COALESCE(e.summary,''),e.kind,e.answered,
		CASE WHEN e.kind='request' AND NOT e.answered THEN `+swarmSystemEvent+` ELSE '' END,
		COALESCE('agent-'||a.ordinal::text,e.publisher),COALESCE(src.available,false),
		CASE WHEN src.available THEN substring(v.body FROM $5::text) END
	FROM candidates e
	LEFT JOIN cairn.agent_session a ON e.publisher='agent/'||a.agent_id::text AND a.repo=e.repo
	LEFT JOIN cairn.memory_record m ON m.record_id=e.record_id
	LEFT JOIN cairn.record_version v ON v.record_id=e.record_id AND v.version=e.version AND v.repo=e.repo
	CROSS JOIN LATERAL (SELECT m.lifecycle='active' AND v.record_id IS NOT NULL AND v.payload_deleted_by IS NULL AS available) src
	ORDER BY e.position DESC`, r.repo, r.principals, r.landing, r.now.Add(-swarmBoardMaxTTL), swarmBoardCardBlock)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var items []SwarmNeed
	type senderSlot struct{ publisher, slot string }
	seenSlots := map[senderSlot]bool{}
	missing := 0
	for rows.Next() {
		var ref RecordVersionRef
		var available, answered bool
		var kind, publisherLabel string
		var block *string
		item := SwarmNeed{Type: SwarmNeedBoardQuestion}
		if err := rows.Scan(&item.Refs.EventID, &item.Subject, &item.Since, &ref.RecordID, &ref.Version, &item.Title, &kind,
			&answered, &item.System, &publisherLabel, &available, &block); err != nil {
			return nil, err
		}
		show := kind == "request" && !answered
		if !available {
			if show {
				missing++
			}
			continue
		}
		facts := swarmBoardCard(block, item.Since)
		if facts.slot != "" {
			key := senderSlot{item.Subject, facts.slot}
			if seenSlots[key] {
				continue
			}
			seenSlots[key] = true
		}
		if !show || !facts.keepUntil.After(r.now) {
			continue
		}
		if facts.certainty == swarmBoardUncertain {
			item.Detail = "Expiry not known exactly"
		} else {
			item.ExpiresAt = &facts.keepUntil
		}
		if item.Title == "" {
			if item.System != "" {
				publisherLabel = "Cairn"
			}
			item.Title = "Board question from " + publisherLabel
		}
		item.ID = item.Type + ":" + item.Refs.EventID
		if facts.defaultDeclared {
			item.Detail = "Unattended default declared"
		}
		item.Refs.Record = &ref
		item.Action = SwarmNeedAction{Kind: "answer_board", Label: "Answer this question on the Fernside board"}
		items = append(items, item)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	if missing > 0 {
		r.out.Unavailable = append(r.out.Unavailable, SwarmNeedSource{SwarmNeedBoardQuestion,
			fmt.Sprintf("The source notes of %d unanswered board requests are unavailable", missing)})
	}
	return items, nil
}

func swarmNeedEventTitle(summary, kind, eventID string) string {
	if summary != "" {
		return summary
	}
	return kind + " " + eventID
}

func (r *swarmNeedsReader) operatorDeliveries(ctx context.Context, tx pgx.Tx, sc swarmScope) ([]SwarmNeed, error) {
	// Check the collection inbox before filtering to the owner page.
	// A delivery on another page can be first in the operator's inbox.
	busy, err := principalWorkOpen(ctx, tx, r.operator)
	if err != nil {
		return nil, err
	}
	var head string
	if !busy {
		err := tx.QueryRow(ctx, nextEventCandidate+` LIMIT 1`, r.repo, r.operator, swarmOperatorDestination.AllowLocal).Scan(&head)
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return nil, err
		}
	}
	filter, args := sc.inboxEventFilter()
	query := `SELECT d.delivery_id::text,e.event_id::text,e.publisher,` + swarmSystemEvent + `,e.created_at,COALESCE(e.summary,''),e.kind,e.record_id::text,e.version
		FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)
		WHERE ` + filter + ` AND d.consumer=$3 AND ` + swarmInboxOpenDelivery
	rows, err := tx.Query(ctx, query, append(args, r.operator)...)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, func(row pgx.CollectableRow) (SwarmNeed, error) {
		item := SwarmNeed{Type: SwarmNeedOperatorDelivery}
		var deliveryID, kind string
		var ref RecordVersionRef
		if err := row.Scan(&deliveryID, &item.Refs.EventID, &item.Subject, &item.System, &item.Since, &item.Title, &kind, &ref.RecordID, &ref.Version); err != nil {
			return item, err
		}
		item.ID = item.Type + ":" + deliveryID
		item.Refs.DeliveryIDs, item.Refs.Record = []string{deliveryID}, &ref
		item.Title = swarmNeedEventTitle(item.Title, kind, item.Refs.EventID)
		item.InboxHead = deliveryID == head
		item.Action = SwarmNeedAction{Kind: "acknowledge", Label: "Read and acknowledge this delivery"}
		if !item.InboxHead {
			item.Action.Label = "Read this delivery and acknowledge it when it reaches the head of the inbox"
		}
		if kind == "request" && !r.landing {
			item.Refs.Principal = item.Subject
			item.Action = SwarmNeedAction{Kind: "reply", Label: "Open the request and reply to its sender"}
		}
		return item, nil
	})
}

func (r *swarmNeedsReader) workstreams(ctx context.Context, tx pgx.Tx) ([]SwarmNeed, error) {
	rows, err := tx.Query(ctx, `SELECT stream, owner, updated_at, decision_needed FROM cairn.workstream_status
		WHERE repo=$1 AND decision_needed<>''
		AND (owner=ANY($2::text[])) <> $3`, r.repo, r.principals, r.landing)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, func(row pgx.CollectableRow) (SwarmNeed, error) {
		item := SwarmNeed{Type: SwarmNeedWorkstreamDecision}
		if err := row.Scan(&item.Refs.Stream, &item.Subject, &item.Since, &item.Detail); err != nil {
			return item, err
		}
		item.ID, item.Title = item.Type+":"+item.Refs.Stream, "Workstream decision: "+item.Refs.Stream
		item.Refs.Principal = item.Subject
		item.Action = SwarmNeedAction{"decide", "Give the workstream owner a decision and clear the request",
			"cairn status get --operator --repo " + swarmNeedShellArg(r.repo) + " --stream " + swarmNeedShellArg(item.Refs.Stream)}
		return item, nil
	})
}

func (r *swarmNeedsReader) ownerTurns(ctx context.Context, tx pgx.Tx) ([]SwarmNeed, error) {
	// The watcher reports owner_turn_required when automatic idle wake is
	// disabled, even for an empty inbox. Require eligible work on that turn.
	rows, err := tx.Query(ctx, `SELECT 'agent/'||a.agent_id::text, o.observed_at, ready.delivery_id, ready.event_id, 'agent-'||a.ordinal::text
		FROM cairn.agent_session_delivery_observation o JOIN cairn.agent_session a USING(agent_id)
		JOIN LATERAL (
			SELECT d.delivery_id::text,e.event_id::text
			FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)
			WHERE e.repo=a.repo AND d.consumer='agent/'||a.agent_id::text
			AND (o.delivery_id IS NULL OR d.delivery_id=o.delivery_id)
			AND d.available_at<=$2 AND (d.state='pending' OR (d.state='leased' AND d.lease_until<=$2))
			AND e.task_deadline IS NULL AND `+requestAdmissionOpen+` AND `+waitNotificationAdmissionOpen+`
			AND `+wakeHold+` AND `+sessionInboxHold+`
			ORDER BY e.position LIMIT 1
		) ready ON true
		WHERE a.repo=$1 AND o.condition='owner_turn_required' AND o.scope='session'
		AND NOT a.stopped AND a.expires_at>$2 AND a.database_generation=$3
		AND o.execution_id=a.execution_id AND o.database_generation=a.database_generation
		AND o.observed_at>=$4 AND o.received_at>=$4
		AND (('agent/'||a.agent_id::text)=ANY($5::text[])) <> $6`,
		r.repo, r.now, r.generation, r.now.Add(-observationTTL), r.principals, r.landing)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, func(row pgx.CollectableRow) (SwarmNeed, error) {
		const code = "owner_turn_required"
		item := SwarmNeed{Type: SwarmNeedOwnerTurn, Detail: HostConditionHint(code)}
		var delivery, label string
		if err := row.Scan(&item.Subject, &item.Since, &delivery, &item.Refs.EventID, &label); err != nil {
			return item, err
		}
		item.ID, item.Refs.Code = item.Type+":"+item.Subject, code
		item.Title = "Owner turn required for " + label
		item.Refs.DeliveryIDs = []string{delivery}
		item.Action = SwarmNeedAction{Kind: "take_turn", Label: "Open the session in Herdr and take a turn"}
		return item, nil
	})
}

func swarmNeedAddDelivery(item *SwarmNeed, delivery, event string) {
	if item.Refs.EventID == "" {
		item.Refs.EventID = event
	}
	item.Refs.EventIDs = append(item.Refs.EventIDs, event)
	item.Refs.DeliveryIDs = append(item.Refs.DeliveryIDs, delivery)
	item.Count++
}

func (r *swarmNeedsReader) failedDeliveries(ctx context.Context, tx pgx.Tx) ([]SwarmNeed, error) {
	rows, err := tx.Query(ctx, `SELECT d.delivery_id::text,e.event_id::text,d.consumer,COALESCE(d.completed_at,d.available_at),COALESCE(d.code,''),
			COALESCE(e.summary,''), e.kind, COALESCE('agent-'||a.ordinal::text,d.consumer)
		FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)
		LEFT JOIN cairn.agent_session a ON d.consumer='agent/'||a.agent_id::text AND a.repo=e.repo
		WHERE e.repo=$1 AND d.state='failed'
		AND COALESCE(d.code,'') NOT IN ('operator_cancelled','operator_released','consumer_retired')
		AND (d.consumer=ANY($2::text[])) <> $3
		AND NOT EXISTS (SELECT 1 FROM cairn.agent_event_reissue rr WHERE rr.delivery_id=d.delivery_id)
		ORDER BY COALESCE(d.completed_at,d.available_at),d.delivery_id`, r.repo, r.principals, r.landing)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	groups := map[string]*SwarmNeed{}
	recipients := map[string]map[string]string{}
	for rows.Next() {
		var delivery, event, consumer, code, summary, kind, label string
		var since time.Time
		if err := rows.Scan(&delivery, &event, &consumer, &since, &code, &summary, &kind, &label); err != nil {
			return nil, err
		}
		item := groups[code]
		if item == nil {
			detail := code
			if detail == "" {
				detail = "No failure reason was recorded"
			}
			item = &SwarmNeed{ID: SwarmNeedFailedDelivery + ":" + code, Type: SwarmNeedFailedDelivery,
				Title:  swarmNeedEventTitle(summary, kind, event) + " to " + label,
				Detail: detail, Subject: consumer, Since: since, Refs: SwarmNeedRefs{Code: code},
				Action: SwarmNeedAction{"inspect", "Review the failed delivery and its prior effects", swarmNeedReviewCommand(r.repo, delivery)}}
			groups[code] = item
			recipients[code] = map[string]string{}
		}
		recipients[code][consumer] = label
		swarmNeedAddDelivery(item, delivery, event)
	}
	var items []SwarmNeed
	for code, item := range groups {
		if item.Count > 1 {
			item.Title += fmt.Sprintf(" (+%d more)", item.Count-1)
		}
		if len(recipients[code]) > 1 {
			item.Subject = ""
			names := make([]string, 0, len(recipients[code]))
			for _, label := range recipients[code] {
				names = append(names, label)
			}
			sort.Strings(names)
			item.Detail += " · Recipients: " + strings.Join(names, ", ")
		}
		items = append(items, *item)
	}
	return items, rows.Err()
}

func (r *swarmNeedsReader) sessionBacklog(ctx context.Context, tx pgx.Tx) ([]SwarmNeed, error) {
	rows, err := tx.Query(ctx, `SELECT d.delivery_id::text,d.consumer,d.state,d.available_at,e.event_id::text,e.kind,
			NOT (`+wakeHold+`) OR NOT (`+sessionInboxHold+`),
			w.created_at,n.created_at,n.open_tools,'agent-'||a.ordinal::text
		FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)
		JOIN cairn.agent_session a ON d.consumer='agent/'||a.agent_id::text AND a.repo=e.repo
		LEFT JOIN LATERAL (
			SELECT wa.created_at FROM cairn.agent_wake_attempt wa
			WHERE wa.delivery_id=d.delivery_id ORDER BY wa.created_at DESC,wa.attempt_id DESC LIMIT 1
		) w ON true
		LEFT JOIN LATERAL (
			SELECT na.created_at,(SELECT count(*) FROM cairn.agent_session_tool t
				WHERE t.attempt_id=na.attempt_id AND t.stop_state NOT IN ('terminated','unavailable')) AS open_tools
			FROM cairn.agent_session_attempt na
			WHERE na.delivery_id=d.delivery_id OR EXISTS (SELECT 1 FROM cairn.agent_session_digest_item di
				WHERE di.attempt_id=na.attempt_id AND di.delivery_id=d.delivery_id)
			ORDER BY na.created_at DESC,na.attempt_id DESC LIMIT 1
		) n ON true
		WHERE e.repo=$1 AND d.state IN ('pending','leased') AND d.available_at<=$2
		AND (a.stopped OR (a.expires_at>$2 AND a.database_generation=$3))
		AND (d.consumer=ANY($4::text[])) <> $5
		ORDER BY d.available_at,d.delivery_id`, r.repo, r.now, r.generation, r.principals, r.landing)
	if err != nil {
		return nil, err
	}
	labels := map[string]string{}
	deliveries, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (ReviewDelivery, error) {
		var d ReviewDelivery
		var label string
		var wake, native *time.Time
		var tools *int
		err := row.Scan(&d.DeliveryID, &d.Consumer, &d.State, &d.AvailableAt, &d.Event.EventID, &d.Event.Kind, &d.Held, &wake, &native, &tools, &label)
		if err != nil {
			return d, err
		}
		labels[d.Consumer] = label
		if wake != nil {
			d.LatestWake = &ReviewAttempt{CreatedAt: *wake}
		}
		if native != nil {
			d.LatestNative = &ReviewAttempt{CreatedAt: *native, OpenTools: tools}
		}
		return d, nil
	})
	if err != nil {
		return nil, err
	}
	ids := make([]string, 0, len(deliveries))
	byID := make(map[string]ReviewDelivery, len(deliveries))
	for _, d := range deliveries {
		ids = append(ids, d.DeliveryID)
		byID[d.DeliveryID] = d
	}
	diagnoses, err := deliveryDiagnoses(ctx, tx, ids, true)
	if err != nil {
		return nil, err
	}
	stopped := map[string]*SwarmNeed{}
	for i := range deliveries {
		d := &deliveries[i]
		d.Diagnosis = diagnoses[d.DeliveryID]
		if d.Diagnosis == nil || d.Diagnosis.Recipient.Presence != "stopped" {
			continue
		}
		finding, ok := AttentionCandidate(*d, 0, r.now)
		if !ok {
			continue
		}
		item := stopped[d.Consumer]
		if item == nil {
			item = &SwarmNeed{ID: SwarmNeedStoppedBacklog + ":" + d.Consumer, Type: SwarmNeedStoppedBacklog,
				Title: "Stopped session " + labels[d.Consumer] + " has unresolved deliveries", Detail: finding.NextCheck, Subject: d.Consumer, Since: d.AvailableAt,
				Action: SwarmNeedAction{"inspect", "Review the stranded work before deciding how to resolve it", swarmNeedReviewCommand(r.repo, d.DeliveryID)},
				Refs:   SwarmNeedRefs{Code: finding.Condition}}
			stopped[d.Consumer] = item
		}
		swarmNeedAddDelivery(item, d.DeliveryID, d.Event.EventID)
	}
	out := make([]SwarmNeed, 0, len(stopped))
	for _, item := range stopped {
		out = append(out, *item)
	}
	starved, err := DetectStarvation(deliveries, StarvationOptions{}, r.now, func(consumers []string) (map[string]time.Time, error) {
		return swarmNeedsLastHandled(ctx, tx, r.repo, consumers)
	})
	if err != nil {
		return nil, err
	}
	for _, finding := range starved {
		d := byID[finding.OldestDeliveryID]
		since, title := d.AvailableAt, "Pending inbox work has stalled for "+labels[d.Consumer]
		detail := fmt.Sprintf("Oldest eligible delivery has reached the %d-second threshold without recent handling. %s",
			finding.ThresholdSeconds, HostConditionHint(finding.HostCondition))
		if finding.Condition == "hold_stalled" {
			title = "Delivery hold has stalled for " + labels[d.Consumer]
			detail = fmt.Sprintf("Oldest hold has reached the %d-second threshold with no open tool call recorded.", finding.ThresholdSeconds)
			switch finding.AgeBasis {
			case "native_attempt":
				since = d.LatestNative.CreatedAt
			case "wake_attempt":
				since = d.LatestWake.CreatedAt
			}
		}
		out = append(out, SwarmNeed{
			ID: SwarmNeedStalledAgent + ":" + finding.Recipient + "/" + finding.ExecutionID + "/" + finding.Condition, Type: SwarmNeedStalledAgent,
			Title: title, Detail: detail, Subject: finding.Recipient, Since: since, Count: finding.Count,
			Action: SwarmNeedAction{"inspect", "Inspect the session and delivery before intervening", swarmNeedReviewCommand(r.repo, d.DeliveryID)},
			Refs:   SwarmNeedRefs{EventID: d.Event.EventID, DeliveryIDs: []string{d.DeliveryID}, Code: finding.Condition},
		})
	}
	return out, nil
}

func swarmNeedsLastHandled(ctx context.Context, tx pgx.Tx, repo string, consumers []string) (map[string]time.Time, error) {
	rows, err := tx.Query(ctx, `SELECT d.consumer,max(d.completed_at) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)
		WHERE e.repo=$1 AND d.consumer=ANY($2::text[]) AND d.state='handled' GROUP BY d.consumer`, repo, consumers)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]time.Time{}
	for rows.Next() {
		var consumer string
		var at time.Time
		if err := rows.Scan(&consumer, &at); err != nil {
			return nil, err
		}
		out[consumer] = at
	}
	return out, rows.Err()
}

func (r *swarmNeedsReader) timedOutWaits(ctx context.Context, tx pgx.Tx) ([]SwarmNeed, error) {
	rows, err := tx.Query(ctx, `SELECT w.wait_uuid::text,w.owner_principal,w.resolved_at,w.dependency_event_uuid::text,w.terminal_reason,
			COALESCE(e.summary,''),e.kind,COALESCE('agent-'||a.ordinal::text,w.owner_principal)
		FROM cairn.agent_wait w
		JOIN cairn.agent_event e ON e.event_id=w.dependency_event_uuid AND e.repo=w.collection
		LEFT JOIN cairn.agent_session a ON a.agent_id=w.owner_agent_uuid AND a.repo=w.collection
		WHERE w.collection=$1 AND w.generation=$2 AND w.state='timed_out' AND w.resolved_at>$3
		AND (w.owner_principal=ANY($4::text[])) <> $5`,
		r.repo, r.generation, r.now.Add(-swarmTimedOutWaitAge), r.principals, r.landing)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, func(row pgx.CollectableRow) (SwarmNeed, error) {
		item := SwarmNeed{Type: SwarmNeedWaitTimedOut}
		var summary, kind, ownerLabel string
		if err := row.Scan(&item.Refs.WaitID, &item.Subject, &item.Since, &item.Refs.EventID, &item.Refs.Code, &summary, &kind, &ownerLabel); err != nil {
			return item, err
		}
		item.ID, item.Detail = item.Type+":"+item.Refs.WaitID, item.Refs.Code
		item.Title = ownerLabel + " timed out waiting for " + swarmNeedEventTitle(summary, kind, item.Refs.EventID)
		item.Refs.Principal = item.Subject
		item.Action = SwarmNeedAction{Kind: "inspect", Label: "Open the dependency event and follow up with the wait owner"}
		return item, nil
	})
}

type swarmMissedCheckpoint struct {
	id, kind, key, sender, subscriber, sensitivity, eventID, noticeID  string
	destinationType, destinationName, supersedes, correlation, summary string
	system                                                             string
	deadline, created                                                  time.Time
	position                                                           int64
	record                                                             *RecordVersionRef
}

func (r *swarmNeedsReader) missedCheckpoints(ctx context.Context, tx pgx.Tx) ([]SwarmNeed, error) {
	rows, err := tx.Query(ctx, `SELECT oc.checkpoint_id::text,oc.source_kind,oc.source_key,oc.sender,oc.subscriber_consumer,oc.sensitivity,
			oc.deadline_at,COALESCE(e.event_id::text,''),oc.notice_event_id::text,
			COALESCE(e.destination_type,''),COALESCE(e.destination_name,''),COALESCE(e.notice_supersedes,''),COALESCE(e.correlation_id::text,''),
			COALESCE(e.position,0),COALESCE(e.created_at,oc.source_updated_at),n.record_id::text,n.version,COALESCE(e.summary,''),
			CASE WHEN oc.source_kind IN ('notice','request') AND oc.sender=e.publisher THEN `+swarmSystemEvent+` ELSE '' END
		FROM cairn.agent_overdue_checkpoint oc
		JOIN cairn.agent_event n ON n.event_id=oc.notice_event_id AND n.repo=oc.repo
		LEFT JOIN cairn.agent_event e ON e.event_id=oc.source_event_id AND e.repo=oc.repo
		WHERE oc.repo=$1 AND oc.state='emitted'
		AND (oc.sender=ANY($2::text[])) <> $3`, r.repo, r.principals, r.landing)
	if err != nil {
		return nil, err
	}
	checkpoints, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (swarmMissedCheckpoint, error) {
		var c swarmMissedCheckpoint
		c.record = &RecordVersionRef{}
		err := row.Scan(&c.id, &c.kind, &c.key, &c.sender, &c.subscriber, &c.sensitivity,
			&c.deadline, &c.eventID, &c.noticeID, &c.destinationType, &c.destinationName, &c.supersedes, &c.correlation,
			&c.position, &c.created, &c.record.RecordID, &c.record.Version, &c.summary, &c.system)
		return c, err
	})
	if err != nil {
		return nil, err
	}
	var items []SwarmNeed
	for _, c := range checkpoints {
		satisfied, err := r.checkpointSatisfied(ctx, tx, c)
		if err != nil {
			return nil, err
		}
		if satisfied {
			continue
		}
		item := SwarmNeed{ID: SwarmNeedMissedCheckpoint + ":" + c.id, Type: SwarmNeedMissedCheckpoint,
			Title: "Missed checkpoint for " + swarmNeedEventTitle(c.summary, c.kind, c.key), Subject: c.sender, System: c.system, Since: c.deadline,
			Action: SwarmNeedAction{Kind: "inspect", Label: "Open the overdue notice and follow up with the checkpoint sender"},
			Refs:   SwarmNeedRefs{Principal: c.sender, EventID: c.noticeID, Record: c.record}}
		if c.eventID != "" {
			item.Refs.EventIDs = []string{c.noticeID, c.eventID}
		}
		if c.kind == "workstream" {
			item.Detail, item.Refs.Stream = c.key, c.key
		}
		items = append(items, item)
	}
	return items, nil
}

// Emitted checkpoint rows are kept even after the source progresses. Reuse
// the scheduler's satisfaction reads without changing those retained rows.
func (r *swarmNeedsReader) checkpointSatisfied(ctx context.Context, tx pgx.Tx, c swarmMissedCheckpoint) (bool, error) {
	switch c.kind {
	case "notice":
		return isNoticeSatisfiedTx(ctx, tx, r.repo, c.eventID, c.sender, c.subscriber, c.supersedes, c.destinationType, c.destinationName, c.sensitivity, c.position, c.created)
	case "request":
		return isRequestSatisfiedTx(ctx, tx, r.repo, c.eventID, c.destinationType, c.destinationName, c.correlation, c.sensitivity, c.position)
	case "workstream":
		return isWorkstreamSatisfiedTx(ctx, tx, r.repo, c.key, c.subscriber, c.sensitivity, c.deadline)
	}
	return false, fmt.Errorf("unexpected checkpoint source kind %q", c.kind)
}
