package core

import (
	"context"
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
	// Deliveries holds every open delivery of a campaign event (pending,
	// leased or held) and every delivery of the newest SwarmFlowEventWindow
	// campaign events, whatever its state.
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

// swarmFlowTx reads snapshot.flow. CAIRN-830 replaces this stub.
func (s *Store) swarmFlowTx(ctx context.Context, tx pgx.Tx, sc swarmScope) (SwarmFlow, error) {
	return emptySwarmFlow(), nil
}
