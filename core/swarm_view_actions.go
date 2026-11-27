package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// Operator actions of the swarm view (CAIRN-833) use verbs the store already
// has: PublishEvent, NextEvent, CompleteEvent, CancelWork and CancelWait, all
// as the local operator principal. The action reads use the same campaign
// scope as the snapshot and the log.
//
// The types below are the wire contract of the cancel preview. Add a field
// when a reader needs one; do not rename or remove one without the lead.

const SwarmCancelPreviewSchema = "cairn.swarm-cancel-preview/1"

// SwarmCancelPreviewRequest names one delivery of one campaign event.
type SwarmCancelPreviewRequest struct {
	Repo       string `json:"repo,omitempty"`
	Campaign   string `json:"campaign"`
	DeliveryID string `json:"delivery_id"`
}

// SwarmCancelPreview is what cancelling one recipient's delivery affects.
// Cancel is one delivery of one recipient: other recipients of the same
// request, and a reply that already counts in a group, are not affected.
type SwarmCancelPreview struct {
	Schema       string    `json:"schema"`
	PreviewToken string    `json:"preview_token"`
	ObservedAt   time.Time `json:"observed_at"`
	// Delivery is the exact delivery and recipient, as stored now.
	Delivery SwarmDelivery `json:"delivery"`
	// Cancellable is false when the store would refuse the cancel now, for
	// example while the recipient holds a live lease. Refusal says why.
	Cancellable bool          `json:"cancellable"`
	Refusal     *SwarmRefusal `json:"refusal,omitempty"`
	// DirectReplyWaits are the waits registered directly on the request
	// with the request_response predicate. They keep waiting after a
	// cancel, until their own deadline.
	DirectReplyWaits SwarmWaitLookup `json:"direct_reply_waits"`
	// Group is nil when the request has no response group.
	Group *SwarmCancelGroup `json:"group,omitempty"`
	// Siblings are the deliveries of the same event to other recipients.
	Siblings []SwarmDelivery `json:"siblings"`
}

// SwarmWaitLookup tells an empty lookup from an unavailable one. Status is
// found, empty or unavailable; Reason is set when it is unavailable.
type SwarmWaitLookup struct {
	Status string      `json:"status"`
	Reason string      `json:"reason,omitempty"`
	Waits  []SwarmWait `json:"waits"`
}

// SwarmCancelGroup is the response group of a grouped request.
type SwarmCancelGroup struct {
	EventID   string    `json:"event_id"`
	State     string    `json:"state"`
	Policy    string    `json:"policy"`
	Deadline  time.Time `json:"deadline"`
	Expected  int       `json:"expected"`
	Responded int       `json:"responded"`
	// RecipientReplyCounts is set when this recipient's reply already
	// counts in the group. The cancel does not remove it.
	RecipientReplyCounts bool `json:"recipient_reply_counts"`
	// ClosureWaits are the waits on the group's closure.
	ClosureWaits SwarmWaitLookup `json:"closure_waits"`
}

// SwarmRefusal is the store's own refusal code and reason.
type SwarmRefusal struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// GetSwarmCancelPreview reads what a cancel of one delivery would affect.
func (s *Store) GetSwarmCancelPreview(ctx context.Context, req SwarmCancelPreviewRequest) (SwarmCancelPreview, error) {
	if err := validID(req.DeliveryID); err != nil {
		return SwarmCancelPreview{}, err
	}
	tx, sc, err := s.beginSwarmActionRead(ctx, req.Repo, req.Campaign)
	if err != nil {
		return SwarmCancelPreview{}, err
	}
	defer tx.Rollback(context.Background())

	delivery, err := swarmActionDeliveryTx(ctx, tx, sc, req.DeliveryID)
	if err != nil {
		return SwarmCancelPreview{}, err
	}
	if !delivery.canCancel(sc) {
		return SwarmCancelPreview{}, swarmDeliveryNotFound()
	}
	out, err := swarmCancelPreviewTx(ctx, tx, sc, delivery.SwarmDelivery)
	if err != nil {
		return SwarmCancelPreview{}, err
	}
	return out, tx.Commit(ctx)
}

func swarmCancelPreviewTx(ctx context.Context, tx pgx.Tx, sc swarmScope, delivery SwarmDelivery) (SwarmCancelPreview, error) {
	out := SwarmCancelPreview{Schema: SwarmCancelPreviewSchema, ObservedAt: sc.Now, Delivery: delivery}
	var err error
	out.Refusal, err = swarmActionCancelRefusal(ctx, tx, sc.Now, delivery)
	if err != nil {
		return SwarmCancelPreview{}, err
	}
	out.Cancellable = out.Refusal == nil
	out.Siblings, err = swarmActionDeliveriesTx(ctx, tx, delivery.EventID, delivery.DeliveryID)
	if err != nil {
		return SwarmCancelPreview{}, err
	}
	out.DirectReplyWaits = swarmActionWaitLookup(ctx, tx, sc, delivery.EventID, WaitPredicateRequestResponse)
	out.Group, err = swarmActionCancelGroup(ctx, tx, sc, delivery)
	if err != nil {
		return SwarmCancelPreview{}, err
	}
	out.PreviewToken = swarmCancelPreviewToken(sc.Campaign.Campaign, out)
	return out, nil
}

type swarmPreviewWait struct {
	ID, Owner, Predicate, DependencyEventID string
}

type swarmPreviewWaits struct {
	Status string
	Waits  []swarmPreviewWait
}

func swarmPreviewWaitIdentity(lookup SwarmWaitLookup) swarmPreviewWaits {
	out := swarmPreviewWaits{Status: lookup.Status, Waits: make([]swarmPreviewWait, 0, len(lookup.Waits))}
	for _, wait := range lookup.Waits {
		out.Waits = append(out.Waits, swarmPreviewWait{wait.WaitID, wait.Owner, wait.Predicate, wait.DependencyEventID})
	}
	sort.Slice(out.Waits, func(i, j int) bool { return out.Waits[i].ID < out.Waits[j].ID })
	return out
}

// Hash fixed fields in a stable order. Lease and delivery state can change
// while the owner reads the confirmation, without changing who or what it affects.
func swarmCancelPreviewToken(campaign string, preview SwarmCancelPreview) string {
	type siblingIdentity struct{ DeliveryID, Consumer string }
	type groupIdentity struct {
		EventID              string
		Responded            int
		RecipientReplyCounts bool
		ClosureWaits         swarmPreviewWaits
	}
	intent := struct {
		Campaign, DeliveryID, Consumer, EventID string
		Siblings                                []siblingIdentity
		DirectWaits                             swarmPreviewWaits
		Group                                   *groupIdentity
	}{
		Campaign: campaign, DeliveryID: preview.Delivery.DeliveryID, Consumer: preview.Delivery.Consumer, EventID: preview.Delivery.EventID,
		Siblings: make([]siblingIdentity, 0, len(preview.Siblings)), DirectWaits: swarmPreviewWaitIdentity(preview.DirectReplyWaits),
	}
	for _, sibling := range preview.Siblings {
		intent.Siblings = append(intent.Siblings, siblingIdentity{sibling.DeliveryID, sibling.Consumer})
	}
	sort.Slice(intent.Siblings, func(i, j int) bool { return intent.Siblings[i].DeliveryID < intent.Siblings[j].DeliveryID })
	if group := preview.Group; group != nil {
		intent.Group = &groupIdentity{group.EventID, group.Responded, group.RecipientReplyCounts, swarmPreviewWaitIdentity(group.ClosureWaits)}
	}
	canonical, _ := json.Marshal(intent) // This fixed schema contains only JSON scalar values and slices.
	digest := sha256.Sum256(canonical)
	return hex.EncodeToString(digest[:])
}

func (s *Store) beginSwarmActionRead(ctx context.Context, repo, campaign string) (pgx.Tx, swarmScope, error) {
	repo, err := s.swarmRepo(repo)
	if err != nil {
		return nil, swarmScope{}, err
	}
	tx, err := s.beginSwarmRead(ctx)
	if err != nil {
		return nil, swarmScope{}, err
	}
	sc, _, _, err := s.swarmScopeTx(ctx, tx, repo, campaign)
	if err != nil {
		tx.Rollback(context.Background())
		return nil, swarmScope{}, err
	}
	return tx, sc, nil
}

type swarmActionDelivery struct {
	SwarmDelivery
	eventInCampaign bool
}

func swarmDeliveryNotFound() error {
	return failure("NOT_FOUND", "delivery not found in this campaign")
}

func swarmActiveMember(sc swarmScope, principal string) bool {
	if principal == sc.Campaign.RootAgent {
		return true
	}
	for _, member := range sc.Campaign.Members {
		if member.Member == principal && member.Active {
			return true
		}
	}
	return false
}

func (d swarmActionDelivery) canCancel(sc swarmScope) bool {
	live := d.State == "pending" || d.State == "leased" || d.Held
	return sc.isMember(d.Consumer) && (d.eventInCampaign || (live && swarmActiveMember(sc, d.Consumer)))
}

func (d swarmActionDelivery) canReadCancellation(sc swarmScope) bool {
	return sc.isMember(d.Consumer) && (d.eventInCampaign || swarmActiveMember(sc, d.Consumer))
}

func swarmActionDeliveryTx(ctx context.Context, tx pgx.Tx, sc swarmScope, id string) (swarmActionDelivery, error) {
	rows, err := tx.Query(ctx, `SELECT `+swarmActionDeliveryColumns+", ("+swarmEventInCampaign+") IS TRUE"+swarmActionDeliveryFrom+
		" WHERE e.repo=$1 AND d.delivery_id=$5",
		sc.Repo, sc.Principals, sc.Campaign.Campaign, sc.Campaign.CreatedAt, id)
	if err != nil {
		return swarmActionDelivery{}, err
	}
	out, err := pgx.CollectOneRow(rows, func(row pgx.CollectableRow) (swarmActionDelivery, error) {
		var out swarmActionDelivery
		var scanErr error
		out.SwarmDelivery, scanErr = scanSwarmActionDeliveryFields(row, &out.eventInCampaign)
		return out, scanErr
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return out, swarmDeliveryNotFound()
	}
	return out, err
}

func swarmActionInboxDeliveryTx(ctx context.Context, tx pgx.Tx, sc swarmScope, id string) (SwarmDelivery, error) {
	filter, args := sc.inboxEventFilter()
	query := `SELECT ` + swarmActionDeliveryColumns + swarmActionDeliveryFrom +
		` WHERE ` + filter + fmt.Sprintf(` AND d.delivery_id=$%d`, len(args)+1)
	rows, err := tx.Query(ctx, query, append(args, id)...)
	if err != nil {
		return SwarmDelivery{}, err
	}
	d, err := pgx.CollectOneRow(rows, scanSwarmActionDelivery)
	if errors.Is(err, pgx.ErrNoRows) {
		if sc.Campaign.Campaign == "" {
			return d, failure("NOT_FOUND", "delivery not found on the landing page")
		}
		return d, swarmDeliveryNotFound()
	}
	return d, err
}

const swarmActionDeliveryColumns = `d.delivery_id::text,d.event_id::text,e.position,e.kind,` + swarmSystemEvent + `,e.publisher,
	d.consumer,COALESCE(e.summary,''),COALESCE(e.notice_action,''),e.blocker,COALESCE(e.causation_id::text,''),
	e.created_at,d.state,COALESCE(d.code,''),` + swarmFlowHeld + `,
	d.attempts,d.available_at,d.lease_until,d.completed_at,COALESCE(d.superseded_by::text,''),
	e.kind='request' AND EXISTS(SELECT 1 FROM cairn.agent_event r
		WHERE r.repo=e.repo AND r.causation_id=e.event_id AND r.publisher=d.consumer AND r.kind='response')`

const swarmActionDeliveryFrom = ` FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)`

func scanSwarmActionDelivery(row pgx.CollectableRow) (SwarmDelivery, error) {
	return scanSwarmActionDeliveryFields(row)
}

func scanSwarmActionDeliveryFields(row pgx.CollectableRow, extra ...any) (SwarmDelivery, error) {
	var d SwarmDelivery
	fields := []any{&d.DeliveryID, &d.EventID, &d.EventPosition, &d.Kind, &d.System, &d.From, &d.Consumer,
		&d.Summary, &d.Action, &d.Blocker, &d.CausationID, &d.CreatedAt, &d.State, &d.Code,
		&d.Held, &d.Attempts, &d.AvailableAt, &d.LeaseUntil, &d.CompletedAt, &d.SupersededBy, &d.Replied}
	err := row.Scan(append(fields, extra...)...)
	d.Seen = d.Attempts > 0
	d.Display = swarmDeliveryDisplay(d)
	return d, err
}

func swarmActionDeliveriesTx(ctx context.Context, tx pgx.Tx, eventID, exceptID string) ([]SwarmDelivery, error) {
	rows, err := tx.Query(ctx, `SELECT `+swarmActionDeliveryColumns+swarmActionDeliveryFrom+
		` WHERE d.event_id=$1 AND d.delivery_id IS DISTINCT FROM NULLIF($2::text,'')::uuid
		ORDER BY d.consumer,d.delivery_id`, eventID, exceptID)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, scanSwarmActionDelivery)
}

// The preview observes the same conditions as CancelWork without a row lock.
// The later mutation still decides under the delivery row lock.
func swarmActionCancelRefusal(ctx context.Context, tx pgx.Tx, now time.Time, d SwarmDelivery) (*SwarmRefusal, error) {
	if d.State != "pending" && d.State != "leased" {
		return &SwarmRefusal{"VERSION_CONFLICT", deliveryAlreadyTerminal}, nil
	}
	if d.Kind != "request" {
		if d.State != "pending" || d.Attempts != 0 || d.Held {
			return &SwarmRefusal{"UNSUPPORTED_CONTROL", claimedNotificationCancelRefusal}, nil
		}
		return nil, nil
	}
	wake, native, err := deliveryHeld(ctx, tx, d.DeliveryID)
	if err != nil {
		return nil, err
	}
	if native {
		var cancellable bool
		err := tx.QueryRow(ctx, `SELECT turn_exclusive AND native_turn_id<>'' FROM cairn.agent_session_attempt
			WHERE delivery_id=$1 AND finished_at IS NULL`, d.DeliveryID).Scan(&cancellable)
		if err != nil {
			return nil, err
		}
		if !cancellable {
			return &SwarmRefusal{"UNSUPPORTED_CONTROL", nativeTurnCancelRefusal}, nil
		}
	} else if !wake && d.State == "leased" && d.LeaseUntil != nil && d.LeaseUntil.After(now) {
		return &SwarmRefusal{"UNSUPPORTED_CONTROL", manualLeaseCancelRefusal}, nil
	}
	return nil, nil
}

func swarmActionCancelGroup(ctx context.Context, tx pgx.Tx, sc swarmScope, d SwarmDelivery) (*SwarmCancelGroup, error) {
	var group SwarmCancelGroup
	err := tx.QueryRow(ctx, `SELECT g.event_id::text,g.state,g.partial_policy,g.deadline,
		count(m.delivery_id),count(m.response_event_id),
		COALESCE(bool_or(m.delivery_id=$2 AND m.response_event_id IS NOT NULL),false)
		FROM cairn.agent_response_group g LEFT JOIN cairn.agent_response_group_member m USING(event_id)
		WHERE g.event_id=$1 GROUP BY g.event_id`, d.EventID, d.DeliveryID).Scan(
		&group.EventID, &group.State, &group.Policy, &group.Deadline, &group.Expected, &group.Responded, &group.RecipientReplyCounts)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	group.ClosureWaits = swarmActionWaitLookup(ctx, tx, sc, d.EventID, WaitPredicateResponseGroupClosed)
	return &group, nil
}

const swarmActionWaitColumns = `w.wait_uuid::text,w.owner_principal,w.predicate_kind,w.dependency_event_uuid::text,
	COALESCE(e.summary,''),w.state,w.terminal_reason,w.deadline,
	w.created_at,w.resolved_at,
	ARRAY(SELECT d.consumer FROM cairn.agent_delivery d
		WHERE d.event_id=w.dependency_event_uuid AND w.state='waiting' AND
		CASE WHEN w.predicate_kind='response_group_closed' THEN EXISTS(
			SELECT 1 FROM cairn.agent_response_group_member m WHERE m.delivery_id=d.delivery_id AND m.response_event_id IS NULL)
		ELSE NOT EXISTS(SELECT 1 FROM cairn.agent_event r
			WHERE r.repo=w.collection AND r.causation_id=d.event_id AND r.publisher=d.consumer AND r.kind IN ('response','failure'))
		END ORDER BY d.consumer)`

func scanSwarmActionWait(row pgx.CollectableRow) (SwarmWait, error) {
	var w SwarmWait
	err := row.Scan(&w.WaitID, &w.Owner, &w.Predicate, &w.DependencyEventID, &w.DependencySummary, &w.State,
		&w.TerminalReason, &w.Deadline, &w.CreatedAt, &w.ResolvedAt, &w.Awaited)
	return w, err
}

const swarmWaitReadFailureReason = "The waits could not be read."

func swarmActionWaitLookup(ctx context.Context, tx pgx.Tx, sc swarmScope, eventID, predicate string) SwarmWaitLookup {
	waits, err := readSwarmActionWaitingTx(ctx, tx, sc, eventID, predicate)
	if err != nil {
		slog.WarnContext(ctx, "swarm cancellation preview could not read waits", "event_id", eventID, "predicate", predicate, "error", err)
		return SwarmWaitLookup{Status: "unavailable", Reason: swarmWaitReadFailureReason, Waits: []SwarmWait{}}
	}
	status := "empty"
	if len(waits) != 0 {
		status = "found"
		for i := range waits {
			waits[i].Overdue = !waits[i].Deadline.After(sc.Now)
		}
	}
	return SwarmWaitLookup{Status: status, Waits: waits}
}

func readSwarmActionWaitingTx(ctx context.Context, tx pgx.Tx, sc swarmScope, eventID, predicate string) ([]SwarmWait, error) {
	// An unavailable wait lookup must leave the rest of the preview readable.
	sp, err := tx.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer sp.Rollback(ctx)
	rows, err := sp.Query(ctx, `SELECT `+swarmActionWaitColumns+`
		FROM cairn.agent_wait w JOIN cairn.agent_event e ON e.event_id=w.dependency_event_uuid
		WHERE `+swarmWaitInCampaign()+` AND w.dependency_event_uuid=$5 AND w.predicate_kind=$6 AND w.state='waiting'
		ORDER BY w.created_at,w.wait_uuid`, sc.Repo, sc.Principals, sc.Campaign.Campaign, sc.Campaign.CreatedAt, eventID, predicate)
	if err != nil {
		return nil, err
	}
	waits, err := pgx.CollectRows(rows, scanSwarmActionWait)
	if err != nil {
		return nil, err
	}
	return waits, sp.Commit(ctx)
}

// SwarmActionRequest carries the browser's retry identity. Repo is set by the
// local listener, and all attribution comes from its Store channel.
type SwarmActionRequest struct {
	Repo      string `json:"-"`
	Campaign  string `json:"campaign"`
	RequestID string `json:"request_id"`
}

type SwarmSendRequest struct {
	SwarmActionRequest
	To         string `json:"to"`
	Kind       string `json:"kind"`
	Action     string `json:"action,omitempty"`
	Summary    string `json:"summary"`
	Body       string `json:"body"`
	Supersedes string `json:"supersedes,omitempty"`
}

type SwarmEventDetails struct {
	Event           SwarmEvent      `json:"event"`
	Deliveries      []SwarmDelivery `json:"deliveries"`
	Body            *string         `json:"body,omitempty"`
	BodyTruncated   bool            `json:"body_truncated,omitempty"`
	BodyUnavailable string          `json:"body_unavailable,omitempty"`
}

type SwarmDeliveryActionRequest struct {
	SwarmActionRequest
	DeliveryID string `json:"delivery_id"`
}

type SwarmAcknowledgeResult struct {
	Delivery *SwarmDelivery `json:"delivery"`
}

type SwarmAcknowledgeConflict struct {
	Expected string
	Oldest   *SwarmDelivery
	refusal  *Error
}

func (e *SwarmAcknowledgeConflict) Error() string { return e.refusal.Error() }
func (e *SwarmAcknowledgeConflict) Unwrap() error { return e.refusal }

type SwarmCancelDeliveryRequest struct {
	SwarmDeliveryActionRequest
	Reason       string `json:"reason"`
	PreviewToken string `json:"preview_token"`
}

type SwarmCancelDeliveryResult struct {
	Outcome  string        `json:"outcome"`
	Delivery SwarmDelivery `json:"delivery"`
	Detail   string        `json:"detail,omitempty"`
}

type SwarmCancelWaitRequest struct {
	SwarmActionRequest
	WaitID        string `json:"wait_id"`
	ExpectedOwner string `json:"expected_owner"`
}

type SwarmPreviewConflict struct {
	Preview SwarmCancelPreview
	refusal *Error
}

func (e *SwarmPreviewConflict) Error() string { return e.refusal.Error() }
func (e *SwarmPreviewConflict) Unwrap() error { return e.refusal }

type SwarmWaitOwnerConflict struct {
	Wait    SwarmWait
	refusal *Error
}

func (e *SwarmWaitOwnerConflict) Error() string { return e.refusal.Error() }
func (e *SwarmWaitOwnerConflict) Unwrap() error { return e.refusal }

type SwarmCancelWaitResult struct {
	Outcome string    `json:"outcome"`
	Wait    SwarmWait `json:"wait"`
}

var swarmOperatorDestination = Destination{Name: "local", AllowLocal: true}

func (s *Store) normalizeSwarmAction(req *SwarmActionRequest) error {
	if err := s.requireOperator(); err != nil {
		return err
	}
	if err := validID(req.RequestID); err != nil {
		return err
	}
	var err error
	req.Repo, err = s.swarmRepo(req.Repo)
	return err
}

func (s *Store) normalizeSwarmCampaignAction(req *SwarmActionRequest) error {
	if err := s.normalizeSwarmAction(req); err != nil {
		return err
	}
	if !swarmCampaignName.MatchString(req.Campaign) {
		return failure("INVALID_REQUEST", "campaign name is required")
	}
	return nil
}

// The scope check shares the existing collection lock with publication, team
// edits and handoff. A pending delivery moved by handoff cannot leave the
// campaign between this check and the mutation.
func (s *Store) swarmActionGuard(ctx context.Context, req SwarmActionRequest, check func(pgx.Tx, swarmScope) error) func(pgx.Tx) error {
	return func(tx pgx.Tx) error {
		if err := lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
			return err
		}
		sc, _, _, err := s.swarmScopeTx(ctx, tx, req.Repo, req.Campaign)
		if err != nil {
			return err
		}
		return check(tx, sc)
	}
}

func (s *Store) swarmActionEventTx(ctx context.Context, tx pgx.Tx, sc swarmScope, eventID string) (SwarmEvent, error) {
	query := "SELECT " + eventColumns + " FROM cairn.agent_event e WHERE " + swarmEventInCampaign + " AND e.event_id=$5"
	events, err := s.swarmEventsTx(ctx, tx, sc, query, eventID)
	if err != nil {
		return SwarmEvent{}, err
	}
	if len(events) == 0 {
		return SwarmEvent{}, failure("NOT_FOUND", "event not found in this campaign")
	}
	return events[0], nil
}

const swarmEventBodyLimit = 16 << 10

func (s *Store) GetSwarmEvent(ctx context.Context, repo, campaign, eventID string) (SwarmEventDetails, error) {
	return s.swarmEventDetails(ctx, repo, campaign, eventID, true)
}

func (s *Store) swarmEventDetails(ctx context.Context, repo, campaign, eventID string, includeBody bool) (SwarmEventDetails, error) {
	if err := validID(eventID); err != nil {
		return SwarmEventDetails{}, err
	}
	tx, sc, err := s.beginSwarmActionRead(ctx, repo, campaign)
	if err != nil {
		return SwarmEventDetails{}, err
	}
	defer tx.Rollback(context.Background())
	event, err := s.swarmActionEventTx(ctx, tx, sc, eventID)
	if err != nil {
		return SwarmEventDetails{}, err
	}
	deliveries, err := swarmActionDeliveriesTx(ctx, tx, eventID, "")
	if err != nil {
		return SwarmEventDetails{}, err
	}
	out := SwarmEventDetails{Event: event, Deliveries: deliveries}
	if includeBody {
		if err := swarmActionEventBody(ctx, tx, sc.Repo, &out); err != nil {
			return SwarmEventDetails{}, err
		}
	}
	return out, tx.Commit(ctx)
}

func swarmActionEventBody(ctx context.Context, tx pgx.Tx, repo string, out *SwarmEventDetails) error {
	var body string
	var total int
	err := tx.QueryRow(ctx, `SELECT left(v.body,$4),octet_length(v.body)
		FROM cairn.record_version v JOIN cairn.memory_record m USING(record_id)
		WHERE v.record_id=$1 AND v.version=$2 AND v.repo=$3
		  AND m.lifecycle<>'tombstoned' AND v.payload_deleted_by IS NULL`,
		out.Event.Ref.RecordID, out.Event.Ref.Version, repo, swarmEventBodyLimit).Scan(&body, &total)
	if errors.Is(err, pgx.ErrNoRows) {
		out.BodyUnavailable = "the referenced record version is no longer available"
		return nil
	}
	if err != nil {
		return err
	}
	prefix, count, _ := TruncateCleanUTF8(body, swarmEventBodyLimit)
	out.Body = &prefix
	out.BodyTruncated = total > count
	return nil
}

func swarmSendRecipientRefusal() error {
	return failure("AUTHORITY_DENIED", "recipient is not a current principal of this campaign")
}

func swarmCheckSendRecipient(ctx context.Context, tx pgx.Tx, sc swarmScope, recipient string) error {
	if !sc.Campaign.Active {
		return failure("AUTHORITY_DENIED", "campaign is inactive")
	}
	if swarmActiveMember(sc, recipient) {
		return nil
	}
	role, isRole, err := canonicalRoleAddress(recipient)
	if err != nil {
		return err
	}
	if isRole {
		var bound bool
		err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.campaign_role_binding
			WHERE repo=$1 AND campaign=$2 AND role_address=$3)`, sc.Repo, sc.Campaign.Campaign, role).Scan(&bound)
		if err != nil {
			return err
		}
		if bound {
			return nil
		}
	}
	return swarmSendRecipientRefusal()
}

func swarmCheckSupersededEvents(ctx context.Context, tx pgx.Tx, sc swarmScope, ids []string) error {
	var outside bool
	err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_event e
		WHERE e.event_id=ANY($5::uuid[]) AND (`+swarmEventInCampaign+`) IS NOT TRUE)`,
		sc.Repo, sc.Principals, sc.Campaign.Campaign, sc.Campaign.CreatedAt, ids).Scan(&outside)
	if err != nil {
		return err
	}
	if outside {
		return failure("AUTHORITY_DENIED", "this notice cannot replace an earlier one from this page")
	}
	return nil
}

func (s *Store) SendSwarmEvent(ctx context.Context, req SwarmSendRequest) (SwarmEventDetails, error) {
	if err := s.normalizeSwarmCampaignAction(&req.SwarmActionRequest); err != nil {
		return SwarmEventDetails{}, err
	}
	var sc swarmScope
	guard := s.swarmActionGuard(ctx, req.SwarmActionRequest, func(tx pgx.Tx, scope swarmScope) error {
		sc = scope
		return swarmCheckSendRecipient(ctx, tx, sc, req.To)
	})
	checkTarget := func(resolved EventDestination, routing *EventRouting) error {
		// Role resolution always yields an agent principal by this point.
		if !swarmActiveMember(sc, resolved.Name) || (routing != nil && routing.Campaign != sc.Campaign.Campaign) {
			return swarmSendRecipientRefusal()
		}
		return nil
	}
	// Use the existing create and publication mutations in one transaction.
	// A refusal rolls back the note and its receipt along with the publication.
	intent := struct {
		SwarmSendRequest
		Collection string `json:"collection"`
	}{req, req.Repo}
	event, err := mutate(ctx, s, "event-publish", req.RequestID, intent, func(tx pgx.Tx) (AgentEvent, error) {
		if req.Kind != "notice" && req.Kind != "request" {
			return AgentEvent{}, failure("INVALID_REQUEST", "kind must be request or notice")
		}
		if req.Kind == "notice" && req.Action == "" {
			return AgentEvent{}, failure("INVALID_REQUEST", "notice events require explicit action: none, review, or decide")
		}
		noteRequest := uuid.NewSHA1(uuid.MustParse(req.RequestID), []byte("cairn.swarm.message")).String()
		note, err := s.normalizeCreateRequest(CreateRequest{RequestID: noteRequest, Draft: Draft{
			Kind: "note", ClaimType: "self", Sensitivity: "shareable", Body: req.Body,
			Scope: Scope{Repo: req.Repo, TaskID: "coordination/swarm/" + req.Campaign + "/" + req.RequestID, RunID: "*"},
		}})
		if err != nil {
			return AgentEvent{}, err
		}
		noteDigest, err := mutationDigest(noteRequest, note)
		if err != nil {
			return AgentEvent{}, err
		}
		record, err := applyMutationTx(ctx, s, tx, "create", noteRequest, noteDigest, nil, func(tx pgx.Tx) (Record, error) {
			return createRecordTx(ctx, tx, note.Draft)
		})
		if err != nil {
			return AgentEvent{}, err
		}
		eventReq, err := s.normalizePublication(PublishEventRequest{
			RequestID: req.RequestID, Repo: req.Repo, Destination: EventDestination{Type: "agent", Name: req.To},
			Kind: req.Kind, Action: req.Action, Summary: req.Summary, Supersedes: req.Supersedes,
			Ref: RecordVersionRef{RecordID: record.RecordID, Version: record.Version},
		}, swarmOperatorDestination)
		if err != nil {
			return AgentEvent{}, err
		}
		return s.publishEventInternalTx(ctx, tx, eventReq, swarmOperatorDestination, "", false, eventPublicationChecks{
			target: checkTarget,
			superseded: func(ids []string) error {
				return swarmCheckSupersededEvents(ctx, tx, sc, ids)
			},
		})
	}, guard)
	if err != nil {
		return SwarmEventDetails{}, s.retainEventAdmissionRefusal(ctx, req.Repo, err)
	}
	if err := checkTarget(event.Destination, event.Routing); err != nil {
		return SwarmEventDetails{}, err
	}
	return s.swarmEventDetails(ctx, req.Repo, req.Campaign, event.EventID, false)
}

func (s *Store) AcknowledgeSwarmDelivery(ctx context.Context, req SwarmDeliveryActionRequest) (SwarmAcknowledgeResult, error) {
	if err := s.normalizeSwarmAction(&req.SwarmActionRequest); err != nil {
		return SwarmAcknowledgeResult{}, err
	}
	if err := validID(req.DeliveryID); err != nil {
		return SwarmAcknowledgeResult{}, err
	}
	var system string
	readScope := func(tx pgx.Tx) (swarmScope, error) {
		if req.Campaign == "" {
			return swarmScope{Repo: req.Repo}, nil
		}
		sc, _, _, err := s.swarmScopeTx(ctx, tx, req.Repo, req.Campaign)
		return sc, err
	}
	checkTarget := func(tx pgx.Tx, sc swarmScope) error {
		d, err := swarmActionInboxDeliveryTx(ctx, tx, sc, req.DeliveryID)
		if err != nil {
			return err
		}
		if d.Consumer != s.channel.Principal {
			return failure("AUTHORITY_DENIED", "can only acknowledge a delivery addressed to the operator")
		}
		system = d.System
		return nil
	}
	replay := func(tx pgx.Tx) error {
		sc, err := readScope(tx)
		if err != nil {
			return err
		}
		return checkTarget(tx, sc)
	}
	intent := struct {
		SwarmDeliveryActionRequest
		Collection string `json:"collection"`
	}{req, req.Repo}
	out, err := mutateWithReplayGuard(ctx, s, "swarm-acknowledge", req.RequestID, intent, replay, func(tx pgx.Tx) (SwarmAcknowledgeResult, error) {
		out := SwarmAcknowledgeResult{}
		sc, err := readScope(tx)
		if err != nil {
			return out, err
		}
		targetErr := checkTarget(tx, sc)
		if targetErr != nil && Code(targetErr) != "NOT_FOUND" {
			return out, targetErr
		}
		seconds, _ := leaseSeconds(0)
		// Check the oldest eligible row under its claim lock, before attempts
		// and seen change. The target must also have passed the page's scope.
		next, err := s.nextEventTx(ctx, tx, sc.Repo, swarmOperatorDestination, seconds, func(id string) error {
			if id == req.DeliveryID && targetErr == nil {
				return nil
			}
			conflict := &SwarmAcknowledgeConflict{
				Expected: req.DeliveryID,
				refusal:  &Error{Code: "VERSION_CONFLICT", Message: "the delivery is not the oldest in the operator's inbox"},
			}
			if targetErr != nil || req.Campaign == "" {
				return conflict
			}
			oldest, err := swarmActionInboxDeliveryTx(ctx, tx, sc, id)
			if Code(err) == "NOT_FOUND" {
				return conflict
			}
			if err != nil {
				return err
			}
			conflict.Oldest = &oldest
			return conflict
		})
		if err != nil {
			return out, err
		}
		if next.Delivery == nil {
			return out, targetErr
		}
		d := next.Delivery
		if _, err := s.completeEventTx(ctx, tx, CompleteEventRequest{
			DeliveryID: d.DeliveryID, LeaseID: d.LeaseID, Disposition: "handled",
		}, swarmOperatorDestination); err != nil {
			return out, err
		}
		seen, err := swarmActionInboxDeliveryTx(ctx, tx, sc, d.DeliveryID)
		out.Delivery = &seen
		return out, err
	}, func(tx pgx.Tx) error {
		return lock(ctx, tx, "agent-events:"+req.Repo)
	})
	// Cached acknowledgement replies may predate the field or outlive the
	// origin receipt. Use the origin from the current authorized read.
	if err == nil && out.Delivery != nil {
		out.Delivery.System = system
	}
	return out, err
}

func (s *Store) CancelSwarmDelivery(ctx context.Context, req SwarmCancelDeliveryRequest) (SwarmCancelDeliveryResult, error) {
	var out SwarmCancelDeliveryResult
	if err := s.normalizeSwarmCampaignAction(&req.SwarmActionRequest); err != nil {
		return out, err
	}
	if err := validID(req.DeliveryID); err != nil {
		return out, err
	}
	replay := s.swarmActionGuard(ctx, req.SwarmActionRequest, func(tx pgx.Tx, sc swarmScope) error {
		delivery, err := swarmActionDeliveryTx(ctx, tx, sc, req.DeliveryID)
		if err == nil && !delivery.canReadCancellation(sc) {
			return swarmDeliveryNotFound()
		}
		return err
	})
	intent := struct {
		SwarmCancelDeliveryRequest
		Collection string `json:"collection"`
	}{req, req.Repo}
	confirm := func(tx pgx.Tx, sc swarmScope) error {
		delivery, err := swarmActionDeliveryTx(ctx, tx, sc, req.DeliveryID)
		if err != nil {
			return err
		}
		if !delivery.canCancel(sc) {
			return swarmDeliveryNotFound()
		}
		if err := reasonValid(req.Reason); err != nil {
			return err
		}
		if req.PreviewToken == "" {
			return failure("INVALID_REQUEST", "preview_token is required")
		}
		if delivery.State != "pending" && delivery.State != "leased" {
			return nil // The existing cancellation reports the recorded terminal winner.
		}
		fresh, err := swarmCancelPreviewTx(ctx, tx, sc, delivery.SwarmDelivery)
		if err != nil {
			return err
		}
		if req.PreviewToken != fresh.PreviewToken {
			return &SwarmPreviewConflict{Preview: fresh, refusal: &Error{
				Code: "VERSION_CONFLICT", Message: "the cancellation preview changed",
			}}
		}
		return nil
	}
	scope := &scopedCancellation{
		intent: intent, authorizeReplay: replay, notFound: swarmDeliveryNotFound,
		confirm: s.swarmActionGuard(ctx, req.SwarmActionRequest, confirm),
	}
	cancel, cancelErr := s.cancelWork(ctx, CancelWorkRequest{
		RequestID: req.RequestID, Repo: req.Repo, DeliveryID: req.DeliveryID, Reason: req.Reason,
	}, scope)
	var refusal *Error
	terminal := errors.As(cancelErr, &refusal) && refusal.Code == "VERSION_CONFLICT" && refusal.Message == deliveryAlreadyTerminal
	if cancelErr != nil && !terminal {
		return out, cancelErr
	}
	tx, sc, err := s.beginSwarmActionRead(ctx, req.Repo, req.Campaign)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(context.Background())
	delivery, err := swarmActionDeliveryTx(ctx, tx, sc, req.DeliveryID)
	if err != nil {
		return out, err
	}
	// A terminal refusal did not commit a cancellation receipt.
	if (terminal && !delivery.canCancel(sc)) || (!terminal && !delivery.canReadCancellation(sc)) {
		return out, swarmDeliveryNotFound()
	}
	out.Delivery = delivery.SwarmDelivery
	switch {
	case terminal:
		out.Outcome = "already_terminal"
		out.Detail = refusal.Error()
	case cancel.Native != nil && (delivery.State == "pending" || delivery.State == "leased"):
		out.Outcome = "cancel_pending"
	case cancel.Native != nil && delivery.Code != "operator_cancelled":
		out.Outcome = "already_terminal"
	default:
		out.Outcome = "cancelled"
	}
	return out, tx.Commit(ctx)
}

func swarmWaitNotFound() error {
	return failure("NOT_FOUND", "wait not found in this campaign")
}

func swarmActionWaitTx(ctx context.Context, tx pgx.Tx, sc swarmScope, waitID string) (SwarmWait, error) {
	rows, err := tx.Query(ctx, `SELECT `+swarmActionWaitColumns+`
		FROM cairn.agent_wait w JOIN cairn.agent_event e ON e.event_id=w.dependency_event_uuid
		WHERE `+swarmWaitInCampaign()+` AND w.wait_uuid=$5`,
		sc.Repo, sc.Principals, sc.Campaign.Campaign, sc.Campaign.CreatedAt, waitID)
	if err != nil {
		return SwarmWait{}, err
	}
	out, err := pgx.CollectOneRow(rows, scanSwarmActionWait)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, swarmWaitNotFound()
	}
	out.Overdue = out.State == WaitStateWaiting && !out.Deadline.After(sc.Now)
	return out, err
}

func (s *Store) CancelSwarmWait(ctx context.Context, req SwarmCancelWaitRequest) (SwarmCancelWaitResult, error) {
	var out SwarmCancelWaitResult
	if err := s.normalizeSwarmCampaignAction(&req.SwarmActionRequest); err != nil {
		return out, err
	}
	if err := validID(req.WaitID); err != nil {
		return out, err
	}
	readOwned := func(tx pgx.Tx, scope swarmScope) (SwarmWait, error) {
		w, err := swarmActionWaitTx(ctx, tx, scope, req.WaitID)
		if err == nil && !scope.isMember(w.Owner) {
			return SwarmWait{}, swarmWaitNotFound()
		}
		return w, err
	}
	replay := s.swarmActionGuard(ctx, req.SwarmActionRequest, func(tx pgx.Tx, sc swarmScope) error {
		_, err := readOwned(tx, sc)
		return err
	})
	intent := struct {
		SwarmCancelWaitRequest
		Collection string `json:"collection"`
	}{req, req.Repo}
	confirm := func(tx pgx.Tx, sc swarmScope) error {
		w, err := readOwned(tx, sc)
		if err != nil {
			return err
		}
		if w.State != WaitStateWaiting {
			return swarmWaitNotFound()
		}
		if strings.TrimSpace(req.ExpectedOwner) == "" {
			return failure("INVALID_REQUEST", "expected_owner is required")
		}
		if w.Owner != req.ExpectedOwner {
			return &SwarmWaitOwnerConflict{Wait: w, refusal: &Error{
				Code: "VERSION_CONFLICT", Message: "the wait owner changed",
			}}
		}
		return nil
	}
	scope := &scopedCancellation{
		intent: intent, authorizeReplay: replay, notFound: swarmWaitNotFound,
		confirm: s.swarmActionGuard(ctx, req.SwarmActionRequest, confirm),
	}
	_, err := s.cancelWait(ctx, CancelWaitRequest{
		RequestID: req.RequestID, Collection: req.Repo, WaitUUID: req.WaitID,
	}, swarmOperatorDestination, scope)
	if err != nil {
		return out, err
	}
	tx, sc, err := s.beginSwarmActionRead(ctx, req.Repo, req.Campaign)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(context.Background())
	out.Wait, err = readOwned(tx, sc)
	if err != nil {
		return out, err
	}
	out.Outcome = "cancelled"
	return out, tx.Commit(ctx)
}
