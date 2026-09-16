package core

import (
	"context"
	"errors"
	"regexp"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// Agent events are operational observations, not qualified memory or authority.
type AgentEvent struct {
	EventID       string           `json:"event_id"`
	Position      int64            `json:"position"`
	Repo          string           `json:"repo"`
	From          string           `json:"from"`
	CreatedAt     time.Time        `json:"created_at"`
	Kind          string           `json:"kind"`
	Ref           RecordVersionRef `json:"ref"`
	Destination   EventDestination `json:"destination"`
	CausationID   string           `json:"causation_id,omitempty"`
	CorrelationID string           `json:"correlation_id,omitempty"`
	Resolution    *AgentResolution `json:"resolution,omitempty"`
}
type EventDestination struct {
	Type string `json:"type"`
	Name string `json:"name"`
}
type PublishEventRequest struct {
	RequestID     string           `json:"request_id"`
	Repo          string           `json:"repo,omitempty"`
	Kind          string           `json:"kind"`
	Ref           RecordVersionRef `json:"ref"`
	Destination   EventDestination `json:"destination"`
	CausationID   string           `json:"causation_id,omitempty"`
	CorrelationID string           `json:"correlation_id,omitempty"`
	Resolution    *AgentResolution `json:"resolution,omitempty"`
}
type EventQuery struct {
	Repo  string `json:"repo,omitempty"`
	Agent string `json:"agent,omitempty"`
	Topic string `json:"topic,omitempty"`
	After int64  `json:"after,omitempty"`
	Limit int    `json:"limit,omitempty"`
}
type EventPage struct {
	Events    []AgentEvent `json:"events"`
	NextAfter int64        `json:"next_after"`
	More      bool         `json:"more"`
}
type SubscriptionRequest struct {
	RequestID string `json:"request_id"`
	Repo      string `json:"repo,omitempty"`
	Agent     string `json:"agent,omitempty"`
	Topic     string `json:"topic"`
	Active    bool   `json:"active"`
}
type AgentSubscription struct {
	Consumer  string    `json:"consumer"`
	Topic     string    `json:"topic"`
	Active    bool      `json:"active"`
	ChangedAt time.Time `json:"changed_at"`
}
type NextEventRequest struct {
	Repo         string `json:"repo,omitempty"`
	Agent        string `json:"agent,omitempty"`
	LeaseSeconds int    `json:"lease_seconds,omitempty"`
}
type AgentDelivery struct {
	DeliveryID  string            `json:"delivery_id"`
	Consumer    string            `json:"consumer"`
	State       string            `json:"state"`
	LeaseID     string            `json:"lease_id,omitempty"`
	LeaseUntil  *time.Time        `json:"lease_until,omitempty"`
	Attempts    int               `json:"attempts"`
	CompletedAt *time.Time        `json:"completed_at,omitempty"`
	Code        string            `json:"code,omitempty"`
	Result      *RecordVersionRef `json:"result,omitempty"`
	Event       AgentEvent        `json:"event"`
}
type NextEventResult struct {
	Delivery *AgentDelivery `json:"delivery"`
}
type EventLeaseRequest struct {
	DeliveryID   string `json:"delivery_id"`
	LeaseID      string `json:"lease_id"`
	LeaseSeconds int    `json:"lease_seconds,omitempty"`
}
type CompleteEventRequest struct {
	RequestID   string `json:"request_id"`
	DeliveryID  string `json:"delivery_id"`
	LeaseID     string `json:"lease_id"`
	Disposition string `json:"disposition"`
	Code        string `json:"code,omitempty"`
	Draft       *Draft `json:"draft,omitempty"`
}
type EventStatusRequest struct {
	EventID string `json:"event_id"`
	After   string `json:"after,omitempty"`
	Limit   int    `json:"limit,omitempty"`
}
type EventStatus struct {
	Event      AgentEvent      `json:"event"`
	Deliveries []AgentDelivery `json:"deliveries"`
	NextAfter  string          `json:"next_after,omitempty"`
	More       bool            `json:"more"`
}
type EventStats struct {
	Published     int64   `json:"published"`
	Deliveries    int64   `json:"deliveries"`
	Pending       int64   `json:"pending"`
	Leased        int64   `json:"leased"`
	Expired       int64   `json:"expired_leases"`
	Handled       int64   `json:"handled"`
	Ignored       int64   `json:"ignored"`
	Failed        int64   `json:"failed"`
	Redeliveries  int64   `json:"redeliveries"`
	OldestSeconds float64 `json:"oldest_outstanding_seconds"`
}

var eventName = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,128}$`)

func (s *Store) eventScope(repo, agent string) (string, error) {
	if repo == "" {
		repo = s.channel.Repo
	}
	if strings.TrimSpace(repo) == "" || repo == "*" || len(repo) > 256 {
		return "", failure("INVALID_REQUEST", "event collection repository required")
	}
	if err := s.checkRepo(repo); err != nil {
		return "", err
	}
	if agent != "" && agent != s.channel.Principal {
		return "", failure("AUTHORITY_DENIED", "agent must match the authenticated inbox owner")
	}
	return repo, nil
}
func validEventDestination(dest Destination) error {
	if (dest.Name != "local" && dest.Name != "hosted") || (dest.Name == "hosted" && dest.AllowLocal) {
		return failure("INVALID_REQUEST", "invalid event read destination")
	}
	return nil
}
func eventLimit(limit int) (int, error) {
	if limit < 0 || limit > 100 {
		return 0, failure("INVALID_REQUEST", "limit must be 1-100")
	}
	if limit == 0 {
		limit = 50
	}
	return limit, nil
}
func leaseSeconds(seconds int) (int, error) {
	if seconds == 0 {
		seconds = 300
	}
	if seconds < 1 || seconds > 3600 {
		return 0, failure("INVALID_REQUEST", "lease_seconds must be 1-3600")
	}
	return seconds, nil
}

const eventColumns = `e.event_id::text,e.position,e.repo,e.publisher,e.created_at,e.kind,e.record_id::text,e.version,e.destination_type,e.destination_name,COALESCE(e.causation_id::text,''),COALESCE(e.correlation_id::text,''),e.resolved_session`
const eventVisible = `(e.sensitivity='shareable' OR $3) AND (e.publisher=$2 OR EXISTS(SELECT 1 FROM cairn.agent_delivery v WHERE v.event_id=e.event_id AND v.consumer=$2))`

func scanEvent(row pgx.Row) (AgentEvent, error) {
	var e AgentEvent
	err := row.Scan(&e.EventID, &e.Position, &e.Repo, &e.From, &e.CreatedAt, &e.Kind, &e.Ref.RecordID, &e.Ref.Version, &e.Destination.Type, &e.Destination.Name, &e.CausationID, &e.CorrelationID, &e.Resolution)
	return e, err
}
func (s *Store) readEvent(ctx context.Context, tx pgx.Tx, id string, dest Destination) (AgentEvent, error) {
	e, err := scanEvent(tx.QueryRow(ctx, `SELECT `+eventColumns+` FROM cairn.agent_event e WHERE e.event_id=$1 AND `+eventVisible, id, s.channel.Principal, dest.AllowLocal))
	if errors.Is(err, pgx.ErrNoRows) {
		return e, failure("NOT_FOUND", "event not found")
	}
	if err == nil {
		err = s.checkRepo(e.Repo)
	}
	return e, err
}

func (s *Store) PublishEvent(ctx context.Context, req PublishEventRequest, dest Destination) (AgentEvent, error) {
	var err error
	if err = validEventDestination(dest); err != nil {
		return AgentEvent{}, err
	}
	if req.Repo, err = s.eventScope(req.Repo, ""); err != nil {
		return AgentEvent{}, err
	}
	if !eventName.MatchString(req.Kind) || validID(req.Ref.RecordID) != nil || req.Ref.Version < 1 || req.Ref.Version > 2147483647 {
		return AgentEvent{}, failure("INVALID_REQUEST", "valid kind and exact record version required")
	}
	if req.Destination.Type != "agent" && req.Destination.Type != "topic" {
		return AgentEvent{}, failure("INVALID_REQUEST", "destination must be agent or topic")
	}
	if strings.TrimSpace(req.Destination.Name) == "" || len(req.Destination.Name) > 256 || (req.Destination.Type == "topic" && !eventName.MatchString(req.Destination.Name)) {
		return AgentEvent{}, failure("INVALID_REQUEST", "invalid destination name")
	}
	for _, id := range []string{req.CausationID, req.CorrelationID} {
		if id != "" && validID(id) != nil {
			return AgentEvent{}, failure("INVALID_REQUEST", "causation and correlation must be UUIDs")
		}
	}
	if req.Resolution != nil {
		if err := req.Resolution.validate(); err != nil {
			return AgentEvent{}, err
		}
		if req.Resolution.Repo != req.Repo || req.Destination.Type != "agent" || req.Destination.Name != "agent/"+req.Resolution.AgentID {
			return AgentEvent{}, failure("INVALID_REQUEST", "resolution must match the publication collection and recipient")
		}
	}
	return mutate(ctx, s, "event-publish", req.RequestID, req, func(tx pgx.Tx) (AgentEvent, error) {
		if err := lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
			return AgentEvent{}, err
		}
		var sensitivity string
		err := tx.QueryRow(ctx, `SELECT m.sensitivity FROM cairn.memory_record m JOIN cairn.record_version v ON v.record_id=m.record_id WHERE m.record_id=$1 AND v.version=$2 AND v.repo=$3 AND m.lifecycle='active' AND v.payload_deleted_by IS NULL AND (m.sensitivity='shareable' OR $4) FOR SHARE OF m`, req.Ref.RecordID, req.Ref.Version, req.Repo, dest.AllowLocal).Scan(&sensitivity)
		if errors.Is(err, pgx.ErrNoRows) {
			return AgentEvent{}, failure("NOT_FOUND", "source version unavailable")
		}
		if err != nil {
			return AgentEvent{}, err
		}
		if req.Resolution != nil {
			metadataDest := dest
			// A shareable event must not retain a local-only presence reference.
			if sensitivity == "shareable" {
				metadataDest = Destination{Name: "hosted"}
			}
			if err := revalidateAgentResolution(ctx, tx, *req.Resolution, metadataDest); err != nil {
				return AgentEvent{}, err
			}
		}
		if req.CausationID != "" {
			parent, err := s.readEvent(ctx, tx, req.CausationID, dest)
			if err != nil {
				return AgentEvent{}, err
			}
			if parent.Repo != req.Repo {
				return AgentEvent{}, failure("INVALID_REQUEST", "causal parent must be in the same collection")
			}
			var parentSensitivity string
			if err = tx.QueryRow(ctx, `SELECT sensitivity FROM cairn.agent_event WHERE event_id=$1`, parent.EventID).Scan(&parentSensitivity); err != nil {
				return AgentEvent{}, err
			}
			if sensitivity == "shareable" && parentSensitivity == "local" {
				return AgentEvent{}, failure("DESTINATION_PROHIBITED", "a shareable event cannot disclose a local causal parent")
			}
		}
		id := uuid.NewString()
		_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_event(event_id,repo,kind,record_id,version,sensitivity,destination_type,destination_name,causation_id,correlation_id,resolved_session) VALUES($1,$2,$3,$4,$5,$6,$7,$8,NULLIF($9,'')::uuid,NULLIF($10,'')::uuid,$11)`, id, req.Repo, req.Kind, req.Ref.RecordID, req.Ref.Version, sensitivity, req.Destination.Type, req.Destination.Name, req.CausationID, req.CorrelationID, req.Resolution)
		if err != nil {
			return AgentEvent{}, err
		}
		if req.Destination.Type == "agent" {
			_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_delivery(delivery_id,event_id,consumer) VALUES($1,$2,$3)`, uuid.NewString(), id, req.Destination.Name)
		} else {
			_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_delivery(delivery_id,event_id,consumer) SELECT gen_random_uuid(),$1,consumer FROM cairn.agent_subscription WHERE repo=$2 AND topic=$3 AND active`, id, req.Repo, req.Destination.Name)
		}
		if err != nil {
			return AgentEvent{}, err
		}
		return s.readEvent(ctx, tx, id, dest)
	}, func(tx pgx.Tx) error {
		// Cached publication responses must not bypass a profile's current destination.
		var visible bool
		err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.memory_record m JOIN cairn.record_version v USING(record_id) WHERE m.record_id=$1 AND v.version=$2 AND v.repo=$3 AND (m.sensitivity='shareable' OR $4))`, req.Ref.RecordID, req.Ref.Version, req.Repo, dest.AllowLocal).Scan(&visible)
		if err != nil {
			return err
		}
		if !visible {
			return failure("NOT_FOUND", "source version unavailable")
		}
		return nil
	})
}

func (s *Store) SetSubscription(ctx context.Context, req SubscriptionRequest) (AgentSubscription, error) {
	var err error
	if req.Repo, err = s.eventScope(req.Repo, req.Agent); err != nil {
		return AgentSubscription{}, err
	}
	if !eventName.MatchString(req.Topic) {
		return AgentSubscription{}, failure("INVALID_REQUEST", "invalid topic")
	}
	return mutate(ctx, s, "event-subscribe", req.RequestID, req, func(tx pgx.Tx) (AgentSubscription, error) {
		if err := lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
			return AgentSubscription{}, err
		}
		var old bool
		err := tx.QueryRow(ctx, `SELECT active FROM cairn.agent_subscription WHERE repo=$1 AND consumer=$2 AND topic=$3`, req.Repo, s.channel.Principal, req.Topic).Scan(&old)
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return AgentSubscription{}, err
		}
		if errors.Is(err, pgx.ErrNoRows) || old != req.Active {
			if _, err = tx.Exec(ctx, `INSERT INTO cairn.agent_subscription_change(repo,topic,active) VALUES($1,$2,$3)`, req.Repo, req.Topic, req.Active); err != nil {
				return AgentSubscription{}, err
			}
		}
		var result AgentSubscription
		err = tx.QueryRow(ctx, `INSERT INTO cairn.agent_subscription(repo,topic,active) VALUES($1,$2,$3) ON CONFLICT(repo,consumer,topic) DO UPDATE SET active=excluded.active,changed_at=CASE WHEN agent_subscription.active<>excluded.active THEN clock_timestamp() ELSE agent_subscription.changed_at END RETURNING consumer,topic,active,changed_at`, req.Repo, req.Topic, req.Active).Scan(&result.Consumer, &result.Topic, &result.Active, &result.ChangedAt)
		return result, err
	})
}

type EventSubscriptionPage struct {
	Subscriptions []AgentSubscription `json:"subscriptions"`
	NextTopic     string              `json:"next_topic"`
	More          bool                `json:"more"`
}

func (s *Store) EventSubscriptions(ctx context.Context, req EventQuery) (EventSubscriptionPage, error) {
	out := EventSubscriptionPage{Subscriptions: []AgentSubscription{}, NextTopic: req.Topic}
	repo, err := s.eventScope(req.Repo, req.Agent)
	if err != nil {
		return out, err
	}
	limit, err := eventLimit(req.Limit)
	if err != nil {
		return out, err
	}
	if req.After != 0 || (req.Topic != "" && !eventName.MatchString(req.Topic)) {
		return out, failure("INVALID_REQUEST", "subscriptions use topic as an exclusive alphabetical cursor")
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT consumer,topic,active,changed_at FROM cairn.agent_subscription WHERE repo=$1 AND consumer=$2 AND topic>$3 ORDER BY topic LIMIT $4`, repo, s.channel.Principal, req.Topic, limit+1)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		var sub AgentSubscription
		if err = rows.Scan(&sub.Consumer, &sub.Topic, &sub.Active, &sub.ChangedAt); err != nil {
			return out, err
		}
		if len(out.Subscriptions) == limit {
			out.More = true
			break
		}
		out.Subscriptions = append(out.Subscriptions, sub)
		out.NextTopic = sub.Topic
	}
	return out, rows.Err()
}

func (s *Store) Events(ctx context.Context, req EventQuery, dest Destination) (EventPage, error) {
	var out EventPage
	out.Events = []AgentEvent{}
	out.NextAfter = req.After
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	repo, err := s.eventScope(req.Repo, req.Agent)
	if err != nil {
		return out, err
	}
	limit, err := eventLimit(req.Limit)
	if err != nil {
		return out, err
	}
	if req.After < 0 || (req.Topic != "" && !eventName.MatchString(req.Topic)) {
		return out, failure("INVALID_REQUEST", "invalid event query")
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT `+eventColumns+` FROM cairn.agent_event e WHERE e.repo=$1 AND `+eventVisible+` AND e.position>$4 AND ($5='' OR (e.destination_type='topic' AND e.destination_name=$5)) ORDER BY e.position LIMIT $6`, repo, s.channel.Principal, dest.AllowLocal, req.After, req.Topic, limit+1)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		e, err := scanEvent(rows)
		if err != nil {
			return out, err
		}
		if len(out.Events) == limit {
			out.More = true
			break
		}
		out.Events = append(out.Events, e)
		out.NextAfter = e.Position
	}
	return out, rows.Err()
}

const deliveryColumns = `d.delivery_id::text,d.consumer,d.state,COALESCE(d.lease_id::text,''),d.lease_until,d.attempts,d.completed_at,d.code,d.result_id::text,d.result_version`

func scanDelivery(row pgx.Row) (AgentDelivery, error) {
	var d AgentDelivery
	var id *string
	var version *int
	err := row.Scan(&d.DeliveryID, &d.Consumer, &d.State, &d.LeaseID, &d.LeaseUntil, &d.Attempts, &d.CompletedAt, &d.Code, &id, &version)
	if id != nil && version != nil {
		d.Result = &RecordVersionRef{RecordID: *id, Version: *version}
	}
	return d, err
}
func (s *Store) ownedDelivery(ctx context.Context, tx pgx.Tx, id string, dest Destination) (AgentDelivery, error) {
	d, err := scanDelivery(tx.QueryRow(ctx, `SELECT `+deliveryColumns+` FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE d.delivery_id=$1 AND d.consumer=$2 AND (e.sensitivity='shareable' OR $3) FOR UPDATE OF d`, id, s.channel.Principal, dest.AllowLocal))
	if errors.Is(err, pgx.ErrNoRows) {
		return d, failure("NOT_FOUND", "delivery not found")
	}
	if err != nil {
		return d, err
	}
	var eventID string
	if err = tx.QueryRow(ctx, `SELECT event_id::text FROM cairn.agent_delivery WHERE delivery_id=$1`, id).Scan(&eventID); err != nil {
		return d, err
	}
	d.Event, err = s.readEvent(ctx, tx, eventID, dest)
	return d, err
}

func (s *Store) NextEvent(ctx context.Context, req NextEventRequest, dest Destination) (NextEventResult, error) {
	var out NextEventResult
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	repo, err := s.eventScope(req.Repo, req.Agent)
	if err != nil {
		return out, err
	}
	seconds, err := leaseSeconds(req.LeaseSeconds)
	if err != nil {
		return out, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	if _, err = retrievalGeneration(ctx, tx); err != nil {
		return out, err
	}
	var nativeBusy bool
	if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_session_attempt n JOIN cairn.agent_session a USING(agent_id) WHERE 'agent/'||a.agent_id::text=$1 AND n.finished_at IS NULL) OR EXISTS(SELECT 1 FROM cairn.agent_wake_attempt WHERE 'agent/'||agent_id::text=$1 AND finished_at IS NULL)`, s.channel.Principal).Scan(&nativeBusy); err != nil {
		return out, err
	}
	if nativeBusy {
		return out, nil
	}
	var id string
	err = tx.QueryRow(ctx, `SELECT d.delivery_id::text FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.consumer=$2 AND (e.sensitivity='shareable' OR $3) AND d.available_at<=clock_timestamp() AND `+wakeHold+` AND (d.state='pending' OR (d.state='leased' AND d.lease_until<=clock_timestamp())) ORDER BY e.position FOR UPDATE OF d SKIP LOCKED LIMIT 1`, repo, s.channel.Principal, dest.AllowLocal).Scan(&id)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, nil
	}
	if err != nil {
		return out, err
	}
	_, err = tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='leased',lease_id=$2,lease_until=clock_timestamp()+make_interval(secs=>$3),attempts=attempts+1 WHERE delivery_id=$1`, id, uuid.NewString(), seconds)
	if err != nil {
		return out, err
	}
	d, err := s.ownedDelivery(ctx, tx, id, dest)
	if err != nil {
		return out, err
	}
	out.Delivery = &d
	return out, tx.Commit(ctx)
}

func checkEventLease(ctx context.Context, tx pgx.Tx, d AgentDelivery, lease string) error {
	var live bool
	if d.State != "leased" || d.LeaseID != lease {
		return failure("STALE_LEASE", "delivery is not owned by this lease")
	}
	if err := tx.QueryRow(ctx, `SELECT lease_until>clock_timestamp() FROM cairn.agent_delivery WHERE delivery_id=$1`, d.DeliveryID).Scan(&live); err != nil {
		return err
	}
	if !live {
		return failure("STALE_LEASE", "delivery lease expired")
	}
	return nil
}

func (s *Store) ChangeEventLease(ctx context.Context, req EventLeaseRequest, renew bool, dest Destination) (AgentDelivery, error) {
	if err := validEventDestination(dest); err != nil {
		return AgentDelivery{}, err
	}
	if validID(req.DeliveryID) != nil || validID(req.LeaseID) != nil {
		return AgentDelivery{}, failure("INVALID_REQUEST", "delivery and lease UUIDs required")
	}
	seconds, err := leaseSeconds(req.LeaseSeconds)
	if err != nil {
		return AgentDelivery{}, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return AgentDelivery{}, err
	}
	defer tx.Rollback(ctx)
	if _, err = retrievalGeneration(ctx, tx); err != nil {
		return AgentDelivery{}, err
	}
	d, err := s.ownedDelivery(ctx, tx, req.DeliveryID, dest)
	if err != nil {
		return d, err
	}
	if err = checkEventLease(ctx, tx, d, req.LeaseID); err != nil {
		return d, err
	}
	if renew {
		_, err = tx.Exec(ctx, `UPDATE cairn.agent_delivery SET lease_until=clock_timestamp()+make_interval(secs=>$2) WHERE delivery_id=$1`, d.DeliveryID, seconds)
	} else {
		_, err = tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='pending',lease_id=NULL,lease_until=NULL WHERE delivery_id=$1`, d.DeliveryID)
	}
	if err != nil {
		return d, err
	}
	d, err = s.ownedDelivery(ctx, tx, d.DeliveryID, dest)
	if err != nil {
		return d, err
	}
	return d, tx.Commit(ctx)
}

func (s *Store) CompleteEvent(ctx context.Context, req CompleteEventRequest, dest Destination) (AgentDelivery, error) {
	if err := validEventDestination(dest); err != nil {
		return AgentDelivery{}, err
	}
	if validID(req.DeliveryID) != nil || validID(req.LeaseID) != nil {
		return AgentDelivery{}, failure("INVALID_REQUEST", "delivery and lease UUIDs required")
	}
	if req.Disposition != "handled" && req.Disposition != "ignored" && req.Disposition != "failed" {
		return AgentDelivery{}, failure("INVALID_REQUEST", "disposition must be handled, ignored or failed")
	}
	if (req.Disposition == "handled" && req.Code != "") || (req.Disposition != "handled" && req.Code != "unsupported_kind" && req.Code != "source_unavailable" && req.Code != "processing_failed") {
		return AgentDelivery{}, failure("INVALID_REQUEST", "invalid disposition code")
	}
	if req.Draft != nil {
		draft := *req.Draft
		req.Draft = &draft
		if req.Draft.Scope.Repo == "" {
			req.Draft.Scope.Repo = s.channel.Repo
		}
		if req.Disposition != "handled" {
			return AgentDelivery{}, failure("INVALID_REQUEST", "only handled events can create results")
		}
		if req.Draft.Sensitivity == "" {
			req.Draft.Sensitivity = "local"
		}
		if req.Draft.Sensitivity != "local" && req.Draft.Sensitivity != "shareable" {
			return AgentDelivery{}, failure("INVALID_REQUEST", "invalid result sensitivity")
		}
		if err := req.Draft.validate(); err != nil {
			return AgentDelivery{}, err
		}
	}
	return mutate(ctx, s, "event-complete", req.RequestID, req, func(tx pgx.Tx) (AgentDelivery, error) {
		d, err := s.ownedDelivery(ctx, tx, req.DeliveryID, dest)
		if err != nil {
			return d, err
		}
		if err = checkEventLease(ctx, tx, d, req.LeaseID); err != nil {
			return d, err
		}
		var resultID *string
		var resultVersion *int
		if req.Draft != nil {
			if req.Draft.Scope.Repo != d.Event.Repo {
				return d, failure("AUTHORITY_DENIED", "result must use the event collection")
			}
			if req.Draft.AttemptID != "" {
				if err = lock(ctx, tx, "attempt:"+req.Draft.AttemptID); err != nil {
					return d, err
				}
			}
			id := uuid.NewString()
			if _, err = tx.Exec(ctx, `INSERT INTO cairn.memory_record(record_id,current_version,sensitivity) VALUES($1,1,$2)`, id, req.Draft.Sensitivity); err != nil {
				return d, err
			}
			r, err := insertVersion(ctx, tx, id, 1, *req.Draft)
			if err != nil {
				return d, err
			}
			resultID = &r.RecordID
			resultVersion = &r.Version
		}
		_, err = tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state=$2,lease_id=NULL,lease_until=NULL,completed_at=clock_timestamp(),code=$3,result_id=$4,result_version=$5 WHERE delivery_id=$1`, d.DeliveryID, req.Disposition, req.Code, resultID, resultVersion)
		if err != nil {
			return d, err
		}
		return s.ownedDelivery(ctx, tx, d.DeliveryID, dest)
	}, func(tx pgx.Tx) error { _, err := s.ownedDelivery(ctx, tx, req.DeliveryID, dest); return err })
}

func (s *Store) AgentEventStatus(ctx context.Context, req EventStatusRequest, dest Destination) (EventStatus, error) {
	var out EventStatus
	out.Deliveries = []AgentDelivery{}
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	if validID(req.EventID) != nil || (req.After != "" && validID(req.After) != nil) {
		return out, failure("INVALID_REQUEST", "event and after must be UUIDs")
	}
	limit, err := eventLimit(req.Limit)
	if err != nil {
		return out, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	out.Event, err = s.readEvent(ctx, tx, req.EventID, dest)
	if err != nil {
		return out, err
	}
	rows, err := tx.Query(ctx, `SELECT `+strings.Replace(deliveryColumns, "d.result_id::text,d.result_version", "CASE WHEN rm.sensitivity='shareable' OR $6 THEN d.result_id::text END,CASE WHEN rm.sensitivity='shareable' OR $6 THEN d.result_version END", 1)+` FROM cairn.agent_delivery d LEFT JOIN cairn.memory_record rm ON rm.record_id=d.result_id WHERE event_id=$1 AND ($2 OR consumer=$3) AND ($4='' OR delivery_id>NULLIF($4,'')::uuid) ORDER BY delivery_id LIMIT $5`, req.EventID, out.Event.From == s.channel.Principal, s.channel.Principal, req.After, limit+1, dest.AllowLocal)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		d, err := scanDelivery(rows)
		if err != nil {
			return out, err
		}
		if len(out.Deliveries) == limit {
			out.More = true
			break
		}
		d.Event = out.Event
		d.LeaseID = ""
		d.LeaseUntil = nil
		out.Deliveries = append(out.Deliveries, d)
		out.NextAfter = d.DeliveryID
	}
	return out, rows.Err()
}

func (s *Store) AgentEventStats(ctx context.Context, req EventQuery, dest Destination) (EventStats, error) {
	var out EventStats
	if req.Topic != "" || req.After != 0 || req.Limit != 0 {
		return out, failure("INVALID_REQUEST", "event metrics accept only repo and agent")
	}
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	repo, err := s.eventScope(req.Repo, req.Agent)
	if err != nil {
		return out, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	err = tx.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event e WHERE repo=$1 AND publisher=$2 AND (sensitivity='shareable' OR $3)`, repo, s.channel.Principal, dest.AllowLocal).Scan(&out.Published)
	if err != nil {
		return out, err
	}
	err = tx.QueryRow(ctx, `SELECT count(*),count(*) FILTER(WHERE d.state='pending'),count(*) FILTER(WHERE d.state='leased'),count(*) FILTER(WHERE d.state='leased' AND lease_until<=clock_timestamp()),count(*) FILTER(WHERE d.state='handled'),count(*) FILTER(WHERE d.state='ignored'),count(*) FILTER(WHERE d.state='failed'),COALESCE(sum(greatest(d.attempts-1,0)),0),COALESCE(extract(epoch FROM clock_timestamp()-min(e.created_at) FILTER(WHERE d.state IN ('pending','leased'))),0) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.consumer=$2 AND (e.sensitivity='shareable' OR $3)`, repo, s.channel.Principal, dest.AllowLocal).Scan(&out.Deliveries, &out.Pending, &out.Leased, &out.Expired, &out.Handled, &out.Ignored, &out.Failed, &out.Redeliveries, &out.OldestSeconds)
	return out, err
}
