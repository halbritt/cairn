package core

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

// EventReviewRequest selects a bounded operator snapshot, not a live cursor.
type EventReviewRequest struct {
	Repo       string `json:"repo"`
	Consumer   string `json:"consumer,omitempty"`
	State      string `json:"state,omitempty"`
	DeliveryID string `json:"delivery_id,omitempty"`
	After      int64  `json:"after,omitempty"`
	Limit      int    `json:"limit,omitempty"`
}

type ReviewAttempt struct {
	ID              string           `json:"attempt_id"`
	State           string           `json:"state"`
	CreatedAt       time.Time        `json:"created_at"`
	FinishedAt      *time.Time       `json:"finished_at,omitempty"`
	ReceiptID       string           `json:"receipt_id,omitempty"`
	ProcessState    string           `json:"process_state,omitempty"`
	Reason          string           `json:"reason,omitempty"`
	ProviderFailure *ProviderFailure `json:"provider_failure,omitempty"`
	Session         *AgentSessionRef `json:"session,omitempty"`
	NativeTurnID    string           `json:"native_turn_id,omitempty"`
}

type ReviewReissue struct {
	OriginalDeliveryID string    `json:"original_delivery_id"`
	EventID            string    `json:"event_id"`
	Operator           string    `json:"operator"`
	At                 time.Time `json:"at"`
	Reason             string    `json:"reason"`
}

type ReviewDelivery struct {
	Control      *WorkControl      `json:"control,omitempty"`
	ReissuedAs   *ReviewReissue    `json:"reissued_as,omitempty"`
	ReissuedFrom *ReviewReissue    `json:"reissued_from,omitempty"`
	Position     int64             `json:"position"`
	DeliveryID   string            `json:"delivery_id"`
	Consumer     string            `json:"consumer"`
	State        string            `json:"state"`
	Attempts     int               `json:"attempts"`
	AvailableAt  time.Time         `json:"available_at"`
	CompletedAt  *time.Time        `json:"completed_at,omitempty"`
	Code         string            `json:"code,omitempty"`
	Result       *RecordVersionRef `json:"result,omitempty"`
	Event        AgentEvent        `json:"event"`
	Held         bool              `json:"held"`
	Execution    string            `json:"execution"`
	TaskOutcome  string            `json:"reported_task_outcome"`
	Assessment   *Assessment       `json:"assessment,omitempty"`
	LatestWake   *ReviewAttempt    `json:"latest_wake,omitempty"`
	LatestNative *ReviewAttempt    `json:"latest_native,omitempty"`
}

type EventReviewPage struct {
	Deliveries []ReviewDelivery `json:"deliveries"`
	More       bool             `json:"more"`
	NextAfter  int64            `json:"next_after,omitempty"`
}

// ReviewEvents is an explicitly local operator read. It exposes selected attempt
// and review metadata, without payload bodies or lease/completion capabilities.
func (s *Store) ReviewEvents(ctx context.Context, req EventReviewRequest) (EventReviewPage, error) {
	out := EventReviewPage{Deliveries: []ReviewDelivery{}}
	if !s.channel.Operator || s.channel.Repo != "" {
		return out, failure("AUTHORITY_DENIED", "event review requires an unscoped operator channel")
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return out, err
	}
	limit, err := eventLimit(req.Limit)
	if err != nil {
		return out, err
	}
	if req.After < 0 || len(req.Consumer) > 256 || (req.DeliveryID != "" && validID(req.DeliveryID) != nil) {
		return out, failure("INVALID_REQUEST", "invalid review cursor or delivery selector")
	}
	switch req.State {
	case "", "pending", "leased", "held", "failed", "handled", "ignored":
	default:
		return out, failure("INVALID_REQUEST", "unknown delivery review state")
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(context.Background())
	rows, err := tx.Query(ctx, `SELECT d.position,d.delivery_id::text,d.consumer,d.state,d.attempts,d.available_at,d.completed_at,d.code,d.result_id::text,d.result_version,`+eventColumns+`,
 flags.held,
 CASE WHEN d.attempts=0 OR (
  d.attempts=(SELECT count(*) FROM cairn.agent_wake_attempt w WHERE w.delivery_id=d.delivery_id)
  AND NOT EXISTS(SELECT 1 FROM cairn.agent_wake_attempt w WHERE w.delivery_id=d.delivery_id AND w.state<>'prepared' AND w.process_state<>'prelaunch_failed')
  AND NOT EXISTS(SELECT 1 FROM cairn.agent_session_attempt n WHERE n.delivery_id=d.delivery_id)
 ) THEN 'never_launched' ELSE 'may_have_executed' END,
 w.detail,n.detail,a.detail,reissued.detail,origin.detail,
 CASE WHEN d.control_at IS NULL THEN NULL ELSE jsonb_build_object('at',d.control_at,'by',d.control_by,'code',d.code,'reason',d.control_reason) END
 FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)
 CROSS JOIN LATERAL (SELECT NOT (`+wakeHold+`) OR NOT (`+sessionInboxHold+`) AS held) flags
 LEFT JOIN LATERAL (SELECT w.receipt_id,jsonb_build_object('attempt_id',w.attempt_id,'state',w.state,'created_at',w.created_at,'finished_at',w.finished_at,'receipt_id',COALESCE(w.receipt_id::text,''),'process_state',w.process_state,'reason',w.reason,'provider_failure',w.provider_failure,'session',CASE WHEN w.agent_id IS NOT NULL THEN jsonb_build_object('agent_id',w.agent_id,'execution_id',w.execution_id) END) AS detail FROM cairn.agent_wake_attempt w WHERE w.delivery_id=d.delivery_id ORDER BY w.created_at DESC,w.attempt_id DESC LIMIT 1) w ON true
 LEFT JOIN LATERAL (SELECT jsonb_build_object('attempt_id',n.attempt_id,'state',CASE WHEN n.finished_at IS NULL THEN 'running' ELSE 'finished' END,'created_at',n.created_at,'finished_at',n.finished_at,'reason',n.reason,'native_turn_id',n.native_turn_id,'session',jsonb_build_object('agent_id',n.agent_id,'execution_id',n.execution_id)) AS detail FROM cairn.agent_session_attempt n WHERE n.delivery_id=d.delivery_id ORDER BY n.created_at DESC,n.attempt_id DESC LIMIT 1) n ON true
 LEFT JOIN LATERAL (SELECT a.detail FROM cairn.run_assessment a WHERE a.receipt_id=w.receipt_id ORDER BY a.version DESC LIMIT 1) a ON true
 LEFT JOIN LATERAL (SELECT jsonb_build_object('original_delivery_id',r.delivery_id,'event_id',r.event_id,'operator',r.reissued_by,'at',r.reissued_at,'reason',r.reason) AS detail FROM cairn.agent_event_reissue r WHERE r.delivery_id=d.delivery_id) reissued ON true
 LEFT JOIN LATERAL (SELECT jsonb_build_object('original_delivery_id',r.delivery_id,'event_id',r.event_id,'operator',r.reissued_by,'at',r.reissued_at,'reason',r.reason) AS detail FROM cairn.agent_event_reissue r WHERE r.event_id=e.event_id) origin ON true
 WHERE e.repo=$1 AND ($2='' OR d.consumer=$2) AND ($3='' OR ($3='held' AND flags.held) OR d.state=$3) AND d.position>$4 AND ($5='' OR d.delivery_id=NULLIF($5,'')::uuid)
 ORDER BY d.position LIMIT $6`, repo, req.Consumer, req.State, req.After, req.DeliveryID, limit+1)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		if len(out.Deliveries) == limit {
			out.More = true
			break
		}
		var d ReviewDelivery
		var resultID *string
		var resultVersion *int
		fields := []any{&d.Position, &d.DeliveryID, &d.Consumer, &d.State, &d.Attempts, &d.AvailableAt, &d.CompletedAt, &d.Code, &resultID, &resultVersion}
		fields = append(fields, eventScanFields(&d.Event)...)
		fields = append(fields, &d.Held, &d.Execution, &d.LatestWake, &d.LatestNative, &d.Assessment, &d.ReissuedAs, &d.ReissuedFrom, &d.Control)
		if err = rows.Scan(fields...); err != nil {
			return out, err
		}
		if resultID != nil {
			d.Result = &RecordVersionRef{*resultID, *resultVersion}
		}
		d.TaskOutcome = "unknown"
		if d.Assessment != nil {
			d.TaskOutcome = d.Assessment.TaskOutcome
		}
		out.Deliveries = append(out.Deliveries, d)
		out.NextAfter = d.Position
	}
	return out, rows.Err()
}

// ReviewQueuedEvents covers accepted pool work that has no delivery yet.
func (s *Store) ReviewQueuedEvents(ctx context.Context, repo string, after int64, pageLimit int) (EventPage, error) {
	page, err := s.reviewUnassignedPool(ctx, repo, after, pageLimit, false)
	out := EventPage{Events: []AgentEvent{}, More: page.More, NextAfter: page.NextAfter}
	for _, event := range page.Events {
		out.Events = append(out.Events, event.AgentEvent)
	}
	return out, err
}

type ClosedPoolEvent struct {
	AgentEvent
	Control *WorkControl `json:"control,omitempty"`
}
type ClosedPoolPage struct {
	Events    []ClosedPoolEvent `json:"events"`
	More      bool              `json:"more"`
	NextAfter int64             `json:"next_after,omitempty"`
}

// ReviewClosedPoolRequests retains operator visibility when work expired or was
// cancelled before assignment and therefore never acquired a delivery.
func (s *Store) ReviewClosedPoolRequests(ctx context.Context, repo string, after int64, pageLimit int) (ClosedPoolPage, error) {
	return s.reviewUnassignedPool(ctx, repo, after, pageLimit, true)
}

func (s *Store) reviewUnassignedPool(ctx context.Context, repo string, after int64, pageLimit int, closed bool) (ClosedPoolPage, error) {
	out := ClosedPoolPage{Events: []ClosedPoolEvent{}}
	if !s.channel.Operator || s.channel.Repo != "" {
		return out, failure("AUTHORITY_DENIED", "queued request review requires an unscoped operator channel")
	}
	var err error
	repo, err = s.eventScope(repo, "")
	if err != nil {
		return out, err
	}
	limit, err := eventLimit(pageLimit)
	if err != nil {
		return out, err
	}
	if after < 0 {
		return out, failure("INVALID_REQUEST", "invalid queued request cursor")
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(context.Background())
	rows, err := tx.Query(ctx, `SELECT `+eventColumns+`,CASE WHEN q.closed_at IS NULL THEN NULL ELSE jsonb_build_object('at',q.closed_at,'by',q.closed_by,'code',q.closed_code,'reason',q.closed_reason) END FROM cairn.agent_pool_request q JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND q.delivery_id IS NULL AND (q.closed_at IS NOT NULL)=$4 AND e.position>$2 ORDER BY e.position LIMIT $3`, repo, after, limit+1, closed)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		if len(out.Events) == limit {
			out.More = true
			break
		}
		var e ClosedPoolEvent
		if err := rows.Scan(append(eventScanFields(&e.AgentEvent), &e.Control)...); err != nil {
			return out, err
		}
		out.Events = append(out.Events, e)
		out.NextAfter = e.Position
	}
	return out, rows.Err()
}
