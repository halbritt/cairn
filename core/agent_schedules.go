package core

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type ScheduleEventRequest struct {
	RequestID    string              `json:"request_id"`
	NotBefore    time.Time           `json:"not_before"`
	GraceSeconds int                 `json:"grace_seconds"`
	Publication  PublishEventRequest `json:"publication"`
}
type ScheduledEvent struct {
	OccurrenceID string              `json:"occurrence_id"`
	Repo         string              `json:"repo"`
	ScheduledBy  string              `json:"scheduled_by"`
	CreatedAt    time.Time           `json:"created_at"`
	NotBefore    time.Time           `json:"not_before"`
	GraceSeconds int                 `json:"grace_seconds"`
	Publication  PublishEventRequest `json:"publication"`
	State        string              `json:"state"`
	Code         string              `json:"code,omitempty"`
	FinishedAt   *time.Time          `json:"finished_at,omitempty"`
	EventID      string              `json:"event_id,omitempty"`
}
type ScheduleTickRequest struct {
	Repo  string `json:"repo"`
	Limit int    `json:"limit,omitempty"`
}
type ScheduleTickResult struct {
	Occurrences []ScheduledEvent `json:"occurrences"`
}

type ScheduleQuery struct {
	Repo         string `json:"repo"`
	OccurrenceID string `json:"occurrence_id,omitempty"`
	State        string `json:"state,omitempty"`
	After        string `json:"after,omitempty"`
	Limit        int    `json:"limit,omitempty"`
}
type SchedulePage struct {
	Occurrences []ScheduledEvent `json:"occurrences"`
	More        bool             `json:"more"`
	NextAfter   string           `json:"next_after,omitempty"`
}
type CancelScheduleRequest struct {
	RequestID    string `json:"request_id"`
	Repo         string `json:"repo"`
	OccurrenceID string `json:"occurrence_id"`
}

func (s *Store) Schedules(ctx context.Context, req ScheduleQuery) (SchedulePage, error) {
	out := SchedulePage{Occurrences: []ScheduledEvent{}, NextAfter: req.After}
	var err error
	if req.Repo, err = s.scheduleScope(req.Repo); err != nil {
		return out, err
	}
	limit, err := eventLimit(req.Limit)
	if err != nil {
		return out, err
	}
	for _, id := range []string{req.OccurrenceID, req.After} {
		if id != "" && validID(id) != nil {
			return out, failure("INVALID_REQUEST", "schedule identifiers must be UUIDs")
		}
	}
	switch req.State {
	case "", "pending", "fired", "skipped", "cancelled":
	default:
		return out, failure("INVALID_REQUEST", "invalid schedule state")
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT `+scheduleColumns+` FROM cairn.agent_schedule WHERE repo=$1 AND scheduled_by=$2 AND ($3='' OR occurrence_id=NULLIF($3,'')::uuid) AND ($4='' OR state=$4) AND ($5='' OR occurrence_id>NULLIF($5,'')::uuid) ORDER BY occurrence_id LIMIT $6`, req.Repo, s.channel.Principal, req.OccurrenceID, req.State, req.After, limit+1)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		item, e := scanSchedule(rows)
		if e != nil {
			return out, e
		}
		if len(out.Occurrences) == limit {
			out.More = true
			break
		}
		out.Occurrences = append(out.Occurrences, item)
		out.NextAfter = item.OccurrenceID
	}
	return out, rows.Err()
}

// CancelSchedule only cancels intent which has not fired. The shared collection
// lock orders it against ticks; a fired event needs delivery cancellation.
func (s *Store) CancelSchedule(ctx context.Context, req CancelScheduleRequest) (ScheduledEvent, error) {
	var err error
	if req.Repo, err = s.scheduleScope(req.Repo); err != nil {
		return ScheduledEvent{}, err
	}
	if err = validID(req.OccurrenceID); err != nil {
		return ScheduledEvent{}, err
	}
	return mutate(ctx, s, "schedule-cancel", req.RequestID, req, func(tx pgx.Tx) (ScheduledEvent, error) {
		if _, err := retrievalGeneration(ctx, tx); err != nil {
			return ScheduledEvent{}, err
		}
		if err := lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
			return ScheduledEvent{}, err
		}
		item, err := scanSchedule(tx.QueryRow(ctx, `SELECT `+scheduleColumns+` FROM cairn.agent_schedule WHERE occurrence_id=$1 AND repo=$2 AND scheduled_by=$3 FOR UPDATE`, req.OccurrenceID, req.Repo, s.channel.Principal))
		if errors.Is(err, pgx.ErrNoRows) {
			return item, failure("NOT_FOUND", "schedule not found")
		}
		if err != nil {
			return item, err
		}
		if item.State != "pending" {
			return item, failure("VERSION_CONFLICT", "schedule already terminal; fired work requires delivery review")
		}
		return scanSchedule(tx.QueryRow(ctx, `UPDATE cairn.agent_schedule SET state='cancelled',code='operator_cancelled',finished_at=clock_timestamp() WHERE occurrence_id=$1 RETURNING `+scheduleColumns, req.OccurrenceID))
	})
}

const scheduleColumns = `occurrence_id::text,repo,scheduled_by,created_at,not_before,grace_seconds,publication,state,code,finished_at,COALESCE(event_id::text,'')`

func scanSchedule(row pgx.Row) (ScheduledEvent, error) {
	var s ScheduledEvent
	err := row.Scan(&s.OccurrenceID, &s.Repo, &s.ScheduledBy, &s.CreatedAt, &s.NotBefore, &s.GraceSeconds, &s.Publication, &s.State, &s.Code, &s.FinishedAt, &s.EventID)
	return s, err
}
func (s *Store) scheduleScope(repo string) (string, error) {
	if !s.channel.Operator || s.channel.Repo != "" {
		return "", failure("AUTHORITY_DENIED", "scheduling requires the existing unscoped operator channel")
	}
	return s.eventScope(repo, "")
}

// ScheduleEvent records operator-owned one-shot intent. A scheduler under the
// same operator channel later publishes it; it never impersonates an agent.
func (s *Store) ScheduleEvent(ctx context.Context, req ScheduleEventRequest) (ScheduledEvent, error) {
	var err error
	if req.Publication.Repo, err = s.scheduleScope(req.Publication.Repo); err != nil {
		return ScheduledEvent{}, err
	}
	if req.Publication.RequestID != "" || req.Publication.Resolution != nil {
		return ScheduledEvent{}, failure("INVALID_REQUEST", "scheduled publication uses the occurrence identity and an explicit recipient, not a live resolution")
	}
	if req.NotBefore.IsZero() || req.NotBefore.Year() < 1 || req.NotBefore.Year() > 9999 || req.GraceSeconds < 1 || req.GraceSeconds > 604800 {
		return ScheduledEvent{}, failure("INVALID_REQUEST", "not_before and grace_seconds 1-604800 required")
	}
	req.NotBefore = req.NotBefore.UTC()
	dest := Destination{Name: "local", AllowLocal: true}
	if req.Publication, err = s.normalizePublication(req.Publication, dest); err != nil {
		return ScheduledEvent{}, err
	}
	return mutate(ctx, s, "event-schedule", req.RequestID, req, func(tx pgx.Tx) (ScheduledEvent, error) {
		if _, err := retrievalGeneration(ctx, tx); err != nil {
			return ScheduledEvent{}, err
		}
		if err := lock(ctx, tx, "agent-events:"+req.Publication.Repo); err != nil {
			return ScheduledEvent{}, err
		}
		if _, err := s.eventSource(ctx, tx, req.Publication, dest); err != nil {
			return ScheduledEvent{}, err
		}
		var pending int
		if err := tx.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_schedule WHERE repo=$1 AND scheduled_by=$2 AND state='pending'`, req.Publication.Repo, s.channel.Principal).Scan(&pending); err != nil {
			return ScheduledEvent{}, err
		}
		if pending >= 1000 {
			return ScheduledEvent{}, failure("BUDGET_REFUSED", "operator has 1000 pending schedules in this collection")
		}
		return scanSchedule(tx.QueryRow(ctx, `INSERT INTO cairn.agent_schedule(occurrence_id,repo,not_before,grace_seconds,publication) VALUES($1,$2,$3,$4,$5) RETURNING `+scheduleColumns, uuid.NewString(), req.Publication.Repo, req.NotBefore, req.GraceSeconds, req.Publication))
	})
}

// TickSchedules uses database time and a bounded transaction. Event creation,
// fanout and the occurrence's terminal state either all commit or all roll back.
// A lost response is reconciled by listing occurrences, never by republishing.
func (s *Store) TickSchedules(ctx context.Context, req ScheduleTickRequest) (ScheduleTickResult, error) {
	out := ScheduleTickResult{Occurrences: []ScheduledEvent{}}
	var err error
	if req.Repo, err = s.scheduleScope(req.Repo); err != nil {
		return out, err
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
	if _, err = retrievalGeneration(ctx, tx); err != nil {
		return out, err
	}
	if err = lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
		return out, err
	}
	rows, err := tx.Query(ctx, `SELECT `+scheduleColumns+` FROM cairn.agent_schedule WHERE repo=$1 AND scheduled_by=$2 AND state='pending' AND not_before<=clock_timestamp() ORDER BY last_checked_at NULLS FIRST,not_before,occurrence_id LIMIT $3 FOR UPDATE`, req.Repo, s.channel.Principal, limit)
	if err != nil {
		return out, err
	}
	var due []ScheduledEvent
	for rows.Next() {
		item, e := scanSchedule(rows)
		if e != nil {
			rows.Close()
			return out, e
		}
		due = append(due, item)
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return out, err
	}
	for _, item := range due {
		var missed bool
		if err = tx.QueryRow(ctx, `SELECT clock_timestamp()>$1::timestamptz+make_interval(secs=>$2)`, item.NotBefore, item.GraceSeconds).Scan(&missed); err != nil {
			return out, err
		}
		state, code, eventID := "skipped", "misfire", ""
		if !missed {
			nested, e := tx.Begin(ctx)
			if e != nil {
				return out, e
			}
			_, e = s.publishEventTx(ctx, nested, item.Publication, Destination{Name: "local", AllowLocal: true}, item.OccurrenceID)
			if e == nil {
				if err = nested.Commit(ctx); err != nil {
					return out, err
				}
				state, code, eventID = "fired", "", item.OccurrenceID
			} else {
				if err = nested.Rollback(ctx); err != nil {
					return out, err
				}
				var refusal *Error
				if !errors.As(e, &refusal) {
					return out, e
				}
				switch refusal.Code {
				case "POOL_FULL", "POOL_PAUSED":
					// Accepted schedule stays pending until capacity returns or grace expires.
					// Rotate blocked work behind unchecked occurrences in bounded ticks.
					if _, err = tx.Exec(ctx, `UPDATE cairn.agent_schedule SET last_checked_at=clock_timestamp(),code=$2 WHERE occurrence_id=$1`, item.OccurrenceID, refusal.Code); err != nil {
						return out, err
					}
					continue
				case "NOT_FOUND", "DESTINATION_PROHIBITED":
					code = refusal.Code
				default:
					return out, e
				}
			}
		}
		saved, e := scanSchedule(tx.QueryRow(ctx, `UPDATE cairn.agent_schedule SET state=$2,code=$3,event_id=NULLIF($4,'')::uuid,finished_at=clock_timestamp() WHERE occurrence_id=$1 RETURNING `+scheduleColumns, item.OccurrenceID, state, code, eventID))
		if e != nil {
			return out, e
		}
		out.Occurrences = append(out.Occurrences, saved)
	}
	return out, tx.Commit(ctx)
}
