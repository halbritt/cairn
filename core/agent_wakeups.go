package core

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// Wake attempts coordinate a host supervisor. Their labels are reports, not
// execution attestation; a linked runner receipt holds process observations.
type WakeAttempt struct {
	ID           string        `json:"attempt_id"`
	State        string        `json:"state"`
	CreatedAt    time.Time     `json:"created_at"`
	FinishedAt   *time.Time    `json:"finished_at,omitempty"`
	ReceiptID    string        `json:"receipt_id,omitempty"`
	ProcessState string        `json:"process_state,omitempty"`
	Reason       string        `json:"reason,omitempty"`
	Delivery     AgentDelivery `json:"delivery"`
}
type WakeResult struct {
	Attempt *WakeAttempt `json:"attempt"`
}
type WakeClaimRequest struct {
	RequestID string `json:"request_id"`
	Repo      string `json:"repo,omitempty"`
}
type WakeQuery struct {
	Repo      string `json:"repo,omitempty"`
	AttemptID string `json:"attempt_id,omitempty"`
	After     string `json:"after,omitempty"`
	Limit     int    `json:"limit,omitempty"`
	Active    bool   `json:"active,omitempty"`
}
type WakePage struct {
	Attempts  []WakeAttempt `json:"attempts"`
	More      bool          `json:"more"`
	NextAfter string        `json:"next_after,omitempty"`
}
type WakeChangeRequest struct {
	RequestID    string `json:"request_id"`
	AttemptID    string `json:"attempt_id"`
	Operation    string `json:"operation"`
	ReceiptID    string `json:"receipt_id,omitempty"`
	ProcessState string `json:"process_state,omitempty"`
	Reason       string `json:"reason,omitempty"`
}

const wakeHold = `NOT EXISTS(SELECT 1 FROM cairn.agent_wake_attempt w WHERE w.delivery_id=d.delivery_id AND w.finished_at IS NULL)`

func (s *Store) readWake(ctx context.Context, tx pgx.Tx, id string, dest Destination) (WakeAttempt, error) {
	var w WakeAttempt
	var delivery, lease string
	err := tx.QueryRow(ctx, `SELECT attempt_id::text,delivery_id::text,lease_id::text,state,created_at,finished_at,COALESCE(receipt_id::text,''),process_state,reason FROM cairn.agent_wake_attempt WHERE attempt_id=$1 AND consumer=$2`, id, s.channel.Principal).Scan(&w.ID, &delivery, &lease, &w.State, &w.CreatedAt, &w.FinishedAt, &w.ReceiptID, &w.ProcessState, &w.Reason)
	if errors.Is(err, pgx.ErrNoRows) {
		return w, failure("NOT_FOUND", "wake attempt not found")
	}
	if err != nil {
		return w, err
	}
	w.Delivery, err = s.ownedDelivery(ctx, tx, delivery, dest)
	// The attempt's lease is retained for recovery even after completion/fencing.
	w.Delivery.LeaseID = lease
	return w, err
}

func (s *Store) ClaimWake(ctx context.Context, req WakeClaimRequest, dest Destination) (WakeResult, error) {
	if err := validEventDestination(dest); err != nil {
		return WakeResult{}, err
	}
	if validID(req.RequestID) != nil {
		return WakeResult{}, failure("INVALID_REQUEST", "wake claim UUID required")
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return WakeResult{}, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return WakeResult{}, err
	}
	defer tx.Rollback(ctx)
	if _, err = retrievalGeneration(ctx, tx); err != nil {
		return WakeResult{}, err
	}
	var native bool
	if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_session WHERE 'agent/'||agent_id::text=$1 AND metadata->>'delivery_mode'='existing-session')`, s.channel.Principal).Scan(&native); err != nil {
		return WakeResult{}, err
	}
	if native {
		return WakeResult{}, failure("INVALID_REQUEST", "existing sessions cannot be consumed by fresh wake workers")
	}
	if err = lock(ctx, tx, "wake:"+repo+":"+s.channel.Principal); err != nil {
		return WakeResult{}, err
	}
	var existingRepo, consumer string
	err = tx.QueryRow(ctx, `SELECT repo,consumer FROM cairn.agent_wake_attempt WHERE attempt_id=$1`, req.RequestID).Scan(&existingRepo, &consumer)
	if err == nil {
		if existingRepo != repo || consumer != s.channel.Principal {
			return WakeResult{}, failure("IDEMPOTENCY_CONFLICT", "wake claim UUID already used")
		}
		w, err := s.readWake(ctx, tx, req.RequestID, dest)
		if err != nil {
			return WakeResult{}, err
		}
		return WakeResult{&w}, tx.Commit(ctx)
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return WakeResult{}, err
	}
	var busy bool
	if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_wake_attempt WHERE repo=$1 AND consumer=$2 AND finished_at IS NULL)`, repo, s.channel.Principal).Scan(&busy); err != nil {
		return WakeResult{}, err
	}
	if busy {
		return WakeResult{}, nil
	}
	var id string
	err = tx.QueryRow(ctx, `SELECT d.delivery_id::text FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.consumer=$2 AND (e.sensitivity='shareable' OR $3) AND e.kind='request' AND d.available_at<=clock_timestamp() AND (d.state='pending' OR (d.state='leased' AND d.lease_until<=clock_timestamp())) AND `+wakeHold+` AND `+sessionInboxHold+` ORDER BY e.position FOR UPDATE OF d SKIP LOCKED LIMIT 1`, repo, s.channel.Principal, dest.AllowLocal).Scan(&id)
	if errors.Is(err, pgx.ErrNoRows) {
		return WakeResult{}, nil
	}
	if err != nil {
		return WakeResult{}, err
	}
	lease := uuid.NewString()
	if _, err = tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='leased',lease_id=$2,lease_until=clock_timestamp()+interval '90 seconds',attempts=attempts+1 WHERE delivery_id=$1`, id, lease); err != nil {
		return WakeResult{}, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.agent_wake_attempt(attempt_id,delivery_id,repo,lease_id) VALUES($1,$2,$3,$4)`, req.RequestID, id, repo, lease); err != nil {
		return WakeResult{}, err
	}
	w, err := s.readWake(ctx, tx, req.RequestID, dest)
	if err != nil {
		return WakeResult{}, err
	}
	return WakeResult{&w}, tx.Commit(ctx)
}

func (s *Store) WakeAttempts(ctx context.Context, req WakeQuery, dest Destination) (WakePage, error) {
	out := WakePage{Attempts: []WakeAttempt{}}
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return out, err
	}
	limit, err := eventLimit(req.Limit)
	if err != nil {
		return out, err
	}
	if (req.AttemptID != "" && validID(req.AttemptID) != nil) || (req.After != "" && validID(req.After) != nil) {
		return out, failure("INVALID_REQUEST", "wake cursor and attempt must be UUIDs")
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT w.attempt_id::text FROM cairn.agent_wake_attempt w JOIN cairn.agent_delivery d USING(delivery_id) JOIN cairn.agent_event e USING(event_id) WHERE w.repo=$1 AND w.consumer=$2 AND (e.sensitivity='shareable' OR $3) AND ($4='' OR w.attempt_id=NULLIF($4,'')::uuid) AND ($5='' OR w.attempt_id>NULLIF($5,'')::uuid) AND (NOT $6 OR w.finished_at IS NULL) ORDER BY w.attempt_id LIMIT $7`, repo, s.channel.Principal, dest.AllowLocal, req.AttemptID, req.After, req.Active, limit+1)
	if err != nil {
		return out, err
	}
	ids := []string{}
	for rows.Next() {
		var id string
		if err = rows.Scan(&id); err != nil {
			rows.Close()
			return out, err
		}
		ids = append(ids, id)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return out, err
	}
	if len(ids) > limit {
		out.More = true
		ids = ids[:limit]
	}
	for _, id := range ids {
		w, err := s.readWake(ctx, tx, id, dest)
		if err != nil {
			return out, err
		}
		out.Attempts = append(out.Attempts, w)
		out.NextAfter = id
	}
	return out, nil
}

func (s *Store) ChangeWake(ctx context.Context, req WakeChangeRequest, dest Destination) (WakeAttempt, error) {
	if err := validEventDestination(dest); err != nil {
		return WakeAttempt{}, err
	}
	if validID(req.AttemptID) != nil || (req.ReceiptID != "" && validID(req.ReceiptID) != nil) {
		return WakeAttempt{}, failure("INVALID_REQUEST", "wake and receipt UUIDs required")
	}
	switch req.Operation {
	case "start", "enter", "link", "report", "finish":
	default:
		return WakeAttempt{}, failure("INVALID_REQUEST", "unknown wake operation")
	}
	if len(req.Reason) > 128 || (req.Reason != "" && !eventName.MatchString(req.Reason)) {
		return WakeAttempt{}, failure("INVALID_REQUEST", "bounded wake reason code required")
	}
	if req.Operation == "report" && req.ProcessState != "prelaunch_failed" && req.ProcessState != "exited" && req.ProcessState != "timeout" && req.ProcessState != "cancelled" && req.ProcessState != "unknown" {
		return WakeAttempt{}, failure("INVALID_REQUEST", "invalid wake process state")
	}
	return mutate(ctx, s, "wake-change", req.RequestID, req, func(tx pgx.Tx) (WakeAttempt, error) {
		w, err := s.readWake(ctx, tx, req.AttemptID, dest)
		if err != nil {
			return w, err
		}
		if w.State == "finished" {
			return w, failure("VERSION_CONFLICT", "wake already finished")
		}
		switch req.Operation {
		case "start", "enter", "link":
			d, err := s.ownedDelivery(ctx, tx, w.Delivery.DeliveryID, dest)
			if err != nil {
				return w, err
			}
			if err = checkEventLease(ctx, tx, d, w.Delivery.LeaseID); err != nil {
				return w, err
			}
			if req.Operation == "start" && w.State == "prepared" {
				_, err = tx.Exec(ctx, `UPDATE cairn.agent_wake_attempt SET state='starting' WHERE attempt_id=$1`, w.ID)
			} else if req.Operation == "enter" && w.State == "starting" {
				_, err = tx.Exec(ctx, `UPDATE cairn.agent_wake_attempt SET state='running' WHERE attempt_id=$1`, w.ID)
			} else if req.Operation == "link" && w.State == "running" && w.ReceiptID == "" && req.ReceiptID != "" {
				var sameRepo bool
				err = tx.QueryRow(ctx, `SELECT scope->>'repo'=$2 FROM cairn.retrieval_receipt WHERE receipt_id=$1`, req.ReceiptID, w.Delivery.Event.Repo).Scan(&sameRepo)
				if err == nil && !sameRepo {
					return w, failure("AUTHORITY_DENIED", "receipt collection differs")
				}
				if err == nil {
					_, err = tx.Exec(ctx, `UPDATE cairn.agent_wake_attempt SET receipt_id=$2 WHERE attempt_id=$1`, w.ID, req.ReceiptID)
				}
			} else {
				return w, failure("VERSION_CONFLICT", "wake transition already consumed or out of order")
			}
			if err != nil {
				return w, err
			}
		case "report":
			if w.State != "running" || w.ProcessState != "" {
				return w, failure("VERSION_CONFLICT", "wake report already recorded or worker not running")
			}
			if _, err = tx.Exec(ctx, `UPDATE cairn.agent_wake_attempt SET process_state=$2,reason=$3 WHERE attempt_id=$1`, w.ID, req.ProcessState, req.Reason); err != nil {
				return w, err
			}
		case "finish":
			// This reports host-side confirmation that the entire unit has stopped.
			// Lease validity is deliberately not required for crash reconciliation.
			d, err := s.ownedDelivery(ctx, tx, w.Delivery.DeliveryID, dest)
			if err != nil {
				return w, err
			}
			if d.State == "pending" || d.State == "leased" {
				retry := (w.State == "prepared" || w.ProcessState == "prelaunch_failed") && d.Attempts < 3
				if retry {
					delay := 10
					if d.Attempts > 1 {
						delay = 30
					}
					_, err = tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='pending',lease_id=NULL,lease_until=NULL,available_at=clock_timestamp()+make_interval(secs=>$2) WHERE delivery_id=$1`, d.DeliveryID, delay)
				} else {
					_, err = tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='failed',lease_id=NULL,lease_until=NULL,completed_at=clock_timestamp(),code='processing_failed' WHERE delivery_id=$1`, d.DeliveryID)
				}
				if err != nil {
					return w, err
				}
			}
			if _, err = tx.Exec(ctx, `UPDATE cairn.agent_wake_attempt SET state='finished',finished_at=clock_timestamp(),reason=CASE WHEN reason='' THEN $2 ELSE reason END WHERE attempt_id=$1`, w.ID, req.Reason); err != nil {
				return w, err
			}
		}
		return s.readWake(ctx, tx, w.ID, dest)
	}, func(tx pgx.Tx) error {
		if _, err := retrievalGeneration(ctx, tx); err != nil {
			return err
		}
		if err := lock(ctx, tx, "wake-attempt:"+req.AttemptID); err != nil {
			return err
		}
		_, err := s.readWake(ctx, tx, req.AttemptID, dest)
		return err
	})
}
