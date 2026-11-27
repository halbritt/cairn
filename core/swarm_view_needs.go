package core

import (
	"context"
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
	Subject string    `json:"subject,omitempty"`
	Since   time.Time `json:"since"`
	// ExpiresAt is set for a board question with a lifetime. After it, the
	// row leaves the list.
	ExpiresAt *time.Time `json:"expires_at,omitempty"`
	// Action is what the owner can do about the row.
	Action SwarmNeedAction `json:"action"`
	// Waiters counts the sessions with a registered wait directly on the
	// row's event. One hop only.
	Waiters int `json:"waiters"`
	// Count is the number of grouped causes, for failed deliveries.
	Count int           `json:"count,omitempty"`
	Refs  SwarmNeedRefs `json:"refs"`
}

// SwarmNeedAction names the owner's next step. Kind is one of: answer_board,
// acknowledge, reply, decide, hand_off, take_turn, cancel_delivery,
// cancel_wait, inspect.
type SwarmNeedAction struct {
	Kind  string `json:"kind"`
	Label string `json:"label"`
	// Command is a shell command the owner can copy when the page has no
	// control for the action.
	Command string `json:"command,omitempty"`
}

// SwarmNeedRefs points at the stored rows behind a need.
type SwarmNeedRefs struct {
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

// swarmNeedsYouTx reads snapshot.needs_you for one campaign. snap already
// holds the roster, the ledger, the handoff check and the flow of this read.
// CAIRN-831 replaces this stub.
func (s *Store) swarmNeedsYouTx(ctx context.Context, tx pgx.Tx, sc swarmScope, snap SwarmSnapshot) (SwarmNeedsYou, error) {
	return emptySwarmNeedsYou(), nil
}

// swarmLandingNeedsYouTx reads the rows that belong to no active campaign.
// affiliated holds every principal that is an active member of an active
// campaign. CAIRN-831 replaces this stub.
func (s *Store) swarmLandingNeedsYouTx(ctx context.Context, tx pgx.Tx, repo string, now time.Time, generation int64, affiliated map[string]bool) (SwarmNeedsYou, error) {
	return emptySwarmNeedsYou(), nil
}
