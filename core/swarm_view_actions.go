package core

import (
	"context"
	"errors"
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
	Schema     string    `json:"schema"`
	ObservedAt time.Time `json:"observed_at"`
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
	out := SwarmCancelPreview{Schema: SwarmCancelPreviewSchema, ObservedAt: sc.Now, Delivery: delivery}
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
	return out, tx.Commit(ctx)
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

func swarmActionDeliveryTx(ctx context.Context, tx pgx.Tx, sc swarmScope, id string) (SwarmDelivery, error) {
	rows, err := tx.Query(ctx, `SELECT `+swarmActionDeliveryColumns+swarmActionDeliveryFrom+
		" WHERE e.repo=$1 AND d.delivery_id=$5 AND (d.consumer=ANY($6) OR ("+swarmEventInCampaign+"))",
		sc.Repo, sc.Principals, sc.Campaign.Campaign, sc.Campaign.CreatedAt, id, sc.MemberPrincipals)
	if err != nil {
		return SwarmDelivery{}, err
	}
	out, err := pgx.CollectOneRow(rows, scanSwarmActionDelivery)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, failure("NOT_FOUND", "delivery not found in this campaign")
	}
	return out, err
}

const swarmActionDeliveryColumns = `d.delivery_id::text,d.event_id::text,e.position,e.kind,e.publisher,
	d.consumer,COALESCE(e.summary,''),COALESCE(e.notice_action,''),e.blocker,COALESCE(e.causation_id::text,''),
	e.created_at,d.state,COALESCE(d.code,''),` + swarmFlowHeld + `,
	d.attempts,d.available_at,d.lease_until,d.completed_at,COALESCE(d.superseded_by::text,''),
	e.kind='request' AND EXISTS(SELECT 1 FROM cairn.agent_event r
		WHERE r.repo=e.repo AND r.causation_id=e.event_id AND r.publisher=d.consumer AND r.kind='response')`

const swarmActionDeliveryFrom = ` FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)`

func scanSwarmActionDelivery(row pgx.CollectableRow) (SwarmDelivery, error) {
	var d SwarmDelivery
	err := row.Scan(&d.DeliveryID, &d.EventID, &d.EventPosition, &d.Kind, &d.From, &d.Consumer,
		&d.Summary, &d.Action, &d.Blocker, &d.CausationID, &d.CreatedAt, &d.State, &d.Code,
		&d.Held, &d.Attempts, &d.AvailableAt, &d.LeaseUntil, &d.CompletedAt, &d.SupersededBy, &d.Replied)
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

func swarmActionWaitLookup(ctx context.Context, tx pgx.Tx, sc swarmScope, eventID, predicate string) SwarmWaitLookup {
	out := SwarmWaitLookup{Status: "empty", Waits: []SwarmWait{}}
	errs := make(map[string]string)
	swarmSection(ctx, tx, errs, "waits", func(sp pgx.Tx) error {
		rows, err := sp.Query(ctx, `SELECT `+swarmActionWaitColumns+`
			FROM cairn.agent_wait w JOIN cairn.agent_event e ON e.event_id=w.dependency_event_uuid
			WHERE w.collection=$1 AND w.dependency_event_uuid=$2 AND w.predicate_kind=$3
			ORDER BY w.created_at,w.wait_uuid`, sc.Repo, eventID, predicate)
		if err != nil {
			return err
		}
		out.Waits, err = pgx.CollectRows(rows, scanSwarmActionWait)
		return err
	})
	if reason, unavailable := errs["waits"]; unavailable {
		return SwarmWaitLookup{Status: "unavailable", Reason: reason, Waits: []SwarmWait{}}
	}
	if len(out.Waits) > 0 {
		out.Status = "found"
		for i := range out.Waits {
			w := &out.Waits[i]
			w.Overdue = w.State == WaitStateWaiting && !w.Deadline.After(sc.Now)
		}
	}
	return out
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
	Oldest   SwarmDelivery
	refusal  *Error
}

func (e *SwarmAcknowledgeConflict) Error() string { return e.refusal.Error() }
func (e *SwarmAcknowledgeConflict) Unwrap() error { return e.refusal }

type SwarmCancelDeliveryRequest struct {
	SwarmDeliveryActionRequest
	Reason string `json:"reason"`
}

type SwarmCancelDeliveryResult struct {
	Outcome  string        `json:"outcome"`
	Delivery SwarmDelivery `json:"delivery"`
	Detail   string        `json:"detail,omitempty"`
}

type SwarmCancelWaitRequest struct {
	SwarmActionRequest
	WaitID string `json:"wait_id"`
}

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

func (s *Store) SendSwarmEvent(ctx context.Context, req SwarmSendRequest) (SwarmEventDetails, error) {
	if err := s.normalizeSwarmAction(&req.SwarmActionRequest); err != nil {
		return SwarmEventDetails{}, err
	}
	if req.Kind != "notice" && req.Kind != "request" {
		return SwarmEventDetails{}, failure("INVALID_REQUEST", "kind must be request or notice")
	}
	if req.Kind == "notice" && req.Action == "" {
		return SwarmEventDetails{}, failure("INVALID_REQUEST", "notice events require explicit action: none, review, or decide")
	}
	noteRequest := uuid.NewSHA1(uuid.MustParse(req.RequestID), []byte("cairn.swarm.message")).String()
	note, err := s.normalizeCreateRequest(CreateRequest{RequestID: noteRequest, Draft: Draft{
		Kind: "note", ClaimType: "self", Sensitivity: "shareable", Body: req.Body,
		Scope: Scope{Repo: req.Repo, TaskID: "coordination/swarm/" + req.Campaign + "/" + req.RequestID, RunID: "*"},
	}})
	if err != nil {
		return SwarmEventDetails{}, err
	}
	noteDigest, err := mutationDigest(noteRequest, note)
	if err != nil {
		return SwarmEventDetails{}, err
	}
	var sc swarmScope
	guard := s.swarmActionGuard(ctx, req.SwarmActionRequest, func(_ pgx.Tx, scope swarmScope) error {
		sc = scope
		if !sc.isMember(req.To) {
			return failure("AUTHORITY_DENIED", "recipient is outside this campaign")
		}
		return nil
	})
	checkTarget := func(resolved EventDestination, routing *EventRouting) error {
		if !sc.isMember(resolved.Name) || (routing != nil && routing.Campaign != sc.Campaign.Campaign) {
			return failure("AUTHORITY_DENIED", "publication would be routed outside this campaign")
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
		record, err := applyMutationTx(ctx, s, tx, "create", noteRequest, noteDigest, func(tx pgx.Tx) (Record, error) {
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
		return s.publishEventTx(ctx, tx, eventReq, swarmOperatorDestination, "", checkTarget)
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
	var sc swarmScope
	guard := s.swarmActionGuard(ctx, req.SwarmActionRequest, func(tx pgx.Tx, scope swarmScope) error {
		sc = scope
		d, err := swarmActionDeliveryTx(ctx, tx, sc, req.DeliveryID)
		if err != nil {
			return err
		}
		if d.Consumer != s.channel.Principal {
			return failure("AUTHORITY_DENIED", "can only acknowledge a delivery addressed to the operator")
		}
		return nil
	})
	intent := struct {
		SwarmDeliveryActionRequest
		Collection string `json:"collection"`
	}{req, req.Repo}
	return mutate(ctx, s, "swarm-acknowledge", req.RequestID, intent, func(tx pgx.Tx) (SwarmAcknowledgeResult, error) {
		out := SwarmAcknowledgeResult{}
		seconds, _ := leaseSeconds(0)
		// Check the oldest eligible row under its claim lock, before attempts
		// and seen change. Never expose the envelope of another campaign.
		next, err := s.nextEventTx(ctx, tx, sc.Repo, swarmOperatorDestination, seconds, func(id string) error {
			if id == req.DeliveryID {
				return nil
			}
			oldest, err := swarmActionDeliveryTx(ctx, tx, sc, id)
			if Code(err) == "NOT_FOUND" {
				return failure("VERSION_CONFLICT", "the operator's oldest eligible delivery belongs to another campaign")
			}
			if err != nil {
				return err
			}
			return &SwarmAcknowledgeConflict{
				Expected: req.DeliveryID, Oldest: oldest,
				refusal: &Error{Code: "VERSION_CONFLICT", Message: "the operator's oldest eligible delivery is another item"},
			}
		})
		if err != nil || next.Delivery == nil {
			return out, err
		}
		d := next.Delivery
		if _, err := s.completeEventTx(ctx, tx, CompleteEventRequest{
			DeliveryID: d.DeliveryID, LeaseID: d.LeaseID, Disposition: "handled",
		}, swarmOperatorDestination); err != nil {
			return out, err
		}
		seen, err := swarmActionDeliveryTx(ctx, tx, sc, d.DeliveryID)
		out.Delivery = &seen
		return out, err
	}, guard)
}

func (s *Store) CancelSwarmDelivery(ctx context.Context, req SwarmCancelDeliveryRequest) (SwarmCancelDeliveryResult, error) {
	var out SwarmCancelDeliveryResult
	if err := s.normalizeSwarmAction(&req.SwarmActionRequest); err != nil {
		return out, err
	}
	guard := s.swarmActionGuard(ctx, req.SwarmActionRequest, func(tx pgx.Tx, sc swarmScope) error {
		_, err := swarmActionDeliveryTx(ctx, tx, sc, req.DeliveryID)
		return err
	})
	cancel, cancelErr := s.cancelWork(ctx, CancelWorkRequest{
		RequestID: req.RequestID, Repo: req.Repo, DeliveryID: req.DeliveryID, Reason: req.Reason,
	}, guard)
	if cancelErr != nil && Code(cancelErr) != "VERSION_CONFLICT" {
		return out, cancelErr
	}
	tx, sc, err := s.beginSwarmActionRead(ctx, req.Repo, req.Campaign)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(context.Background())
	out.Delivery, err = swarmActionDeliveryTx(ctx, tx, sc, req.DeliveryID)
	if err != nil {
		return out, err
	}
	switch {
	case cancelErr != nil:
		if out.Delivery.State == "pending" || out.Delivery.State == "leased" {
			return out, cancelErr
		}
		out.Outcome = "already_terminal"
		out.Detail = cancelErr.Error()
	case cancel.Native != nil:
		out.Outcome = "cancel_pending"
	default:
		out.Outcome = "cancelled"
	}
	return out, tx.Commit(ctx)
}

func swarmActionWaitTx(ctx context.Context, tx pgx.Tx, sc swarmScope, waitID string) (SwarmWait, error) {
	// Match the flow snapshot: a wait belongs through its owner, its
	// dependency event, or a delivery of that dependency to a member.
	rows, err := tx.Query(ctx, `SELECT `+swarmActionWaitColumns+`
		FROM cairn.agent_wait w JOIN cairn.agent_event e ON e.event_id=w.dependency_event_uuid
		WHERE w.collection=$1 AND e.repo=$1 AND w.wait_uuid=$5 AND
		      (w.owner_principal=ANY($2) OR (`+swarmEventInCampaign+`)
		       OR EXISTS(SELECT 1 FROM cairn.agent_delivery d WHERE d.event_id=e.event_id AND d.consumer=ANY($6)))`,
		sc.Repo, sc.Principals, sc.Campaign.Campaign, sc.Campaign.CreatedAt, waitID, sc.MemberPrincipals)
	if err != nil {
		return SwarmWait{}, err
	}
	out, err := pgx.CollectOneRow(rows, scanSwarmActionWait)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, failure("NOT_FOUND", "wait not found in this campaign")
	}
	out.Overdue = out.State == WaitStateWaiting && !out.Deadline.After(sc.Now)
	return out, err
}

func (s *Store) CancelSwarmWait(ctx context.Context, req SwarmCancelWaitRequest) (SwarmCancelWaitResult, error) {
	var out SwarmCancelWaitResult
	if err := s.normalizeSwarmAction(&req.SwarmActionRequest); err != nil {
		return out, err
	}
	if err := validID(req.WaitID); err != nil {
		return out, err
	}
	guard := s.swarmActionGuard(ctx, req.SwarmActionRequest, func(tx pgx.Tx, sc swarmScope) error {
		_, err := swarmActionWaitTx(ctx, tx, sc, req.WaitID)
		return err
	})
	_, cancelErr := s.cancelWait(ctx, CancelWaitRequest{
		RequestID: req.RequestID, Collection: req.Repo, WaitUUID: req.WaitID,
	}, swarmOperatorDestination, guard)
	if cancelErr != nil && Code(cancelErr) != "VERSION_CONFLICT" {
		return out, cancelErr
	}
	tx, sc, err := s.beginSwarmActionRead(ctx, req.Repo, req.Campaign)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(context.Background())
	out.Wait, err = swarmActionWaitTx(ctx, tx, sc, req.WaitID)
	if err != nil {
		return out, err
	}
	out.Outcome = "cancelled"
	if cancelErr != nil {
		if out.Wait.State == WaitStateWaiting {
			return out, cancelErr
		}
		out.Outcome = "already_terminal"
	}
	return out, tx.Commit(ctx)
}
