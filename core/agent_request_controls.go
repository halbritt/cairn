package core

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
)

type WorkControl struct {
	At     time.Time `json:"at"`
	By     string    `json:"by"`
	Reason string    `json:"reason,omitempty"`
	Code   string    `json:"code"`
}

type WakeControlRequest struct {
	AttemptID string `json:"attempt_id"`
}
type WakeControlResult struct {
	Attempt    WakeAttempt `json:"attempt"`
	ServerTime time.Time   `json:"server_time"`
	StopReason string      `json:"stop_reason,omitempty"`
}

func (s *Store) WakeControl(ctx context.Context, req WakeControlRequest, dest Destination) (WakeControlResult, error) {
	var out WakeControlResult
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	if err := validID(req.AttemptID); err != nil {
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
	if err = lock(ctx, tx, "wake-attempt:"+req.AttemptID); err != nil {
		return out, err
	}
	w, err := s.readWake(ctx, tx, req.AttemptID, dest)
	if err != nil {
		return out, err
	}
	out.StopReason, out.ServerTime, err = applyWakeControl(ctx, tx, w)
	if err != nil {
		return out, err
	}
	out.Attempt, err = s.readWake(ctx, tx, req.AttemptID, dest)
	if err != nil {
		return out, err
	}
	return out, tx.Commit(ctx)
}

func applyWakeControl(ctx context.Context, tx pgx.Tx, w WakeAttempt) (string, time.Time, error) {
	var now time.Time
	if err := tx.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&now); err != nil {
		return "", now, err
	}
	if w.State == "finished" {
		return "attempt_finished", now, nil
	}
	if w.Delivery.Control != nil {
		return w.Delivery.Control.Code, now, nil
	}
	code := ""
	if deadline := w.Delivery.Event.TaskDeadline; deadline != nil && !now.Before(*deadline) {
		code = "task_deadline"
	}
	if expiry := w.Delivery.Event.AdmissionExpiresAt; code == "" && expiry != nil && !now.Before(*expiry) && (w.State == "prepared" || w.State == "starting") {
		code = "admission_expired"
	}
	if code != "" && (w.Delivery.State == "pending" || w.Delivery.State == "leased") {
		if err := stopRequestDelivery(ctx, tx, w.Delivery.DeliveryID, code, code); err != nil {
			return "", now, err
		}
	}
	return code, now, nil
}

type CancelWorkRequest struct {
	RequestID   string `json:"request_id"`
	Repo        string `json:"repo"`
	DeliveryID  string `json:"delivery_id,omitempty"`
	PoolEventID string `json:"pool_event_id,omitempty"`
	Reason      string `json:"reason"`
}
type WorkCancellation struct {
	EventID    string       `json:"event_id"`
	DeliveryID string       `json:"delivery_id,omitempty"`
	Held       bool         `json:"held"`
	Control    *WorkControl `json:"control"`
}

// CancelWork records the winner against completion. A wake's hold is independent
// of delivery terminal state and can only be released by confirmed host cleanup.
func (s *Store) CancelWork(ctx context.Context, req CancelWorkRequest) (WorkCancellation, error) {
	var zero WorkCancellation
	var err error
	if req.Repo, err = s.requestControlScope(req.Repo); err != nil {
		return zero, err
	}
	if (req.DeliveryID == "") == (req.PoolEventID == "") {
		return zero, failure("INVALID_REQUEST", "choose delivery_id or pool_event_id")
	}
	for _, id := range []string{req.DeliveryID, req.PoolEventID} {
		if id != "" && validID(id) != nil {
			return zero, failure("INVALID_REQUEST", "work identifiers must be UUIDs")
		}
	}
	if err = reasonValid(req.Reason); err != nil {
		return zero, err
	}
	return mutate(ctx, s, "work-cancel", req.RequestID, req, func(tx pgx.Tx) (WorkCancellation, error) {
		if _, err := retrievalGeneration(ctx, tx); err != nil {
			return zero, err
		}
		if err := lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
			return zero, err
		}
		delivery := req.DeliveryID
		if req.PoolEventID != "" {
			var closed bool
			err := tx.QueryRow(ctx, `SELECT COALESCE(q.delivery_id::text,''),q.closed_at IS NOT NULL FROM cairn.agent_pool_request q JOIN cairn.agent_event e USING(event_id) WHERE q.event_id=$1 AND e.repo=$2 FOR UPDATE OF q`, req.PoolEventID, req.Repo).Scan(&delivery, &closed)
			if errors.Is(err, pgx.ErrNoRows) {
				return zero, failure("NOT_FOUND", "pool request not found")
			}
			if err != nil {
				return zero, err
			}
			if closed {
				return zero, failure("VERSION_CONFLICT", "pool request already terminal")
			}
			if delivery == "" {
				out := WorkCancellation{EventID: req.PoolEventID, Control: &WorkControl{}}
				err = tx.QueryRow(ctx, `UPDATE cairn.agent_pool_request SET closed_at=clock_timestamp(),closed_code='operator_cancelled',closed_by=current_setting('cairn.caller'),closed_reason=$2 WHERE event_id=$1 RETURNING closed_at,closed_by,closed_code,closed_reason`, req.PoolEventID, req.Reason).Scan(&out.Control.At, &out.Control.By, &out.Control.Code, &out.Control.Reason)
				return out, err
			}
		}
		var eventID, state, kind string
		var leased bool
		err := tx.QueryRow(ctx, `SELECT d.event_id::text,d.state,e.kind,COALESCE(d.state='leased' AND d.lease_until>clock_timestamp(),false) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE d.delivery_id=$1 AND e.repo=$2 FOR UPDATE OF d`, delivery, req.Repo).Scan(&eventID, &state, &kind, &leased)
		if errors.Is(err, pgx.ErrNoRows) {
			return zero, failure("NOT_FOUND", "delivery not found")
		}
		if err != nil {
			return zero, err
		}
		if kind != "request" {
			return zero, failure("INVALID_REQUEST", "only requests can be cancelled")
		}
		if state != "pending" && state != "leased" {
			return zero, failure("VERSION_CONFLICT", "delivery already terminal")
		}
		var wake, native bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_wake_attempt WHERE delivery_id=$1 AND finished_at IS NULL),EXISTS(SELECT 1 FROM cairn.agent_session_attempt WHERE delivery_id=$1 AND finished_at IS NULL)`, delivery).Scan(&wake, &native); err != nil {
			return zero, err
		}
		if native || (leased && !wake) {
			return zero, failure("UNSUPPORTED_CONTROL", "running native/manual work lacks a supervisor stop contract; reconcile its actual turn/process end")
		}
		if err = stopRequestDelivery(ctx, tx, delivery, "operator_cancelled", req.Reason); err != nil {
			return zero, err
		}
		out := WorkCancellation{EventID: eventID, DeliveryID: delivery, Held: wake, Control: &WorkControl{}}
		err = tx.QueryRow(ctx, `SELECT control_at,control_by,code,control_reason FROM cairn.agent_delivery WHERE delivery_id=$1`, delivery).Scan(&out.Control.At, &out.Control.By, &out.Control.Code, &out.Control.Reason)
		return out, err
	})
}

func stopRequestDelivery(ctx context.Context, tx pgx.Tx, id, code, reason string) error {
	_, err := tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='failed',lease_id=NULL,lease_until=NULL,completed_at=clock_timestamp(),code=$2,control_at=clock_timestamp(),control_by=current_setting('cairn.caller'),control_reason=$3 WHERE delivery_id=$1 AND state IN ('pending','leased')`, id, code, reason)
	return err
}

type RequestControlSweep struct {
	Repo string `json:"repo"`
}
type RequestControlSweepResult struct {
	ResponseGroups int64 `json:"response_groups"`
	Deliveries     int64 `json:"deliveries"`
	PoolRequests   int64 `json:"pool_requests"`
}

func (s *Store) requestControlScope(repo string) (string, error) {
	if !s.channel.Operator || s.channel.Repo != "" {
		return "", failure("AUTHORITY_DENIED", "request control requires the existing unscoped operator channel")
	}
	return s.eventScope(repo, "")
}
func (s *Store) SweepRequestControls(ctx context.Context, req RequestControlSweep) (RequestControlSweepResult, error) {
	var out RequestControlSweepResult
	repo, err := s.requestControlScope(req.Repo)
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
	if err = lock(ctx, tx, "agent-events:"+repo); err != nil {
		return out, err
	}
	if out.PoolRequests, err = expirePoolRequests(ctx, tx, repo); err != nil {
		return out, err
	}
	if out.Deliveries, err = expireRequestDeliveries(ctx, tx, repo, "", true); err != nil {
		return out, err
	}
	if out.ResponseGroups, err = expireResponseGroups(ctx, tx, repo, ""); err != nil {
		return out, err
	}
	return out, tx.Commit(ctx)
}

func expirePoolRequests(ctx context.Context, tx pgx.Tx, repo string) (int64, error) {
	tag, err := tx.Exec(ctx, `WITH due AS (SELECT q.event_id,CASE WHEN e.task_deadline<=clock_timestamp() THEN 'task_deadline' ELSE 'admission_expired' END AS code FROM cairn.agent_pool_request q JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND q.delivery_id IS NULL AND q.closed_at IS NULL AND NOT (`+requestAdmissionOpen+`) ORDER BY e.position LIMIT 100 FOR UPDATE OF q SKIP LOCKED) UPDATE cairn.agent_pool_request q SET closed_at=clock_timestamp(),closed_code=due.code,closed_by=current_setting('cairn.caller'),closed_reason=due.code FROM due WHERE q.event_id=due.event_id`, repo)
	return tag.RowsAffected(), err
}

const requestAdmissionOpen = `(e.admission_expires_at IS NULL OR e.admission_expires_at>clock_timestamp()) AND (e.task_deadline IS NULL OR e.task_deadline>clock_timestamp())`

// Stop decisions fence result completion, but leave attempt holds intact until
// their host confirms termination. Admission expiry excludes live work.
func expireRequestDeliveries(ctx context.Context, tx pgx.Tx, repo, consumer string, allowLocal bool) (int64, error) {
	tag, err := tx.Exec(ctx, `WITH due AS (
 SELECT d.delivery_id,CASE WHEN e.task_deadline<=clock_timestamp() THEN 'task_deadline' ELSE 'admission_expired' END AS code
 FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)
 WHERE e.repo=$1 AND ($2='' OR d.consumer=$2) AND (e.sensitivity='shareable' OR $3) AND e.kind='request' AND d.state IN ('pending','leased') AND (
 e.task_deadline<=clock_timestamp() OR (e.admission_expires_at<=clock_timestamp() AND (d.state='pending' OR d.lease_until<=clock_timestamp()) AND `+wakeHold+` AND `+sessionInboxHold+`))
 ORDER BY d.position LIMIT 100 FOR UPDATE OF d SKIP LOCKED)
 UPDATE cairn.agent_delivery d SET state='failed',lease_id=NULL,lease_until=NULL,completed_at=clock_timestamp(),code=due.code,control_at=clock_timestamp(),control_by=current_setting('cairn.caller'),control_reason=due.code FROM due WHERE d.delivery_id=due.delivery_id`, repo, consumer, allowLocal)
	return tag.RowsAffected(), err
}
