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
	ProviderFailure   *ProviderFailure `json:"provider_failure,omitempty"`
	ProviderFailureAt *time.Time       `json:"provider_failure_at,omitempty"`
	WorkerID          string           `json:"worker_id,omitempty"`
	ID                string           `json:"attempt_id"`
	State             string           `json:"state"`
	CreatedAt         time.Time        `json:"created_at"`
	FinishedAt        *time.Time       `json:"finished_at,omitempty"`
	ReceiptID         string           `json:"receipt_id,omitempty"`
	ProcessState      string           `json:"process_state,omitempty"`
	Reason            string           `json:"reason,omitempty"`
	Session           *AgentSessionRef `json:"session,omitempty"`
	Delivery          AgentDelivery    `json:"delivery"`
}
type WakeResult struct {
	Attempt *WakeAttempt `json:"attempt"`
}
type WakeClaimRequest struct {
	WorkerID  string `json:"worker_id,omitempty"`
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
	ProviderFailure *ProviderFailure `json:"provider_failure,omitempty"`
	RequestID       string           `json:"request_id"`
	AttemptID       string           `json:"attempt_id"`
	Operation       string           `json:"operation"`
	ReceiptID       string           `json:"receipt_id,omitempty"`
	ProcessState    string           `json:"process_state,omitempty"`
	Reason          string           `json:"reason,omitempty"`
	Session         *AgentSessionRef `json:"session,omitempty"`
}

const wakeHold = `NOT EXISTS(SELECT 1 FROM cairn.agent_wake_attempt w WHERE w.delivery_id=d.delivery_id AND w.finished_at IS NULL)`

func (s *Store) readWake(ctx context.Context, tx pgx.Tx, id string, dest Destination) (WakeAttempt, error) {
	var w WakeAttempt
	var delivery, lease string
	var agentID, executionID *string
	err := tx.QueryRow(ctx, `SELECT attempt_id::text,delivery_id::text,lease_id::text,state,created_at,finished_at,COALESCE(receipt_id::text,''),process_state,reason,agent_id::text,execution_id::text,COALESCE(worker_id::text,''),provider_failure,provider_failure_at FROM cairn.agent_wake_attempt WHERE attempt_id=$1 AND consumer=$2`, id, s.channel.Principal).Scan(&w.ID, &delivery, &lease, &w.State, &w.CreatedAt, &w.FinishedAt, &w.ReceiptID, &w.ProcessState, &w.Reason, &agentID, &executionID, &w.WorkerID, &w.ProviderFailure, &w.ProviderFailureAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return w, failure("NOT_FOUND", "wake attempt not found")
	}
	if err != nil {
		return w, err
	}
	if agentID != nil {
		w.Session = &AgentSessionRef{*agentID, *executionID}
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
	if dest.Name != "hosted" {
		return WakeResult{}, failure("DESTINATION_PROHIBITED", "fresh wake workers currently require a hosted profile")
	}
	if validID(req.RequestID) != nil || (req.WorkerID != "" && validID(req.WorkerID) != nil) {
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
	if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_session WHERE 'agent/'||agent_id::text=$1)`, s.channel.Principal).Scan(&native); err != nil {
		return WakeResult{}, err
	}
	if native {
		return WakeResult{}, failure("INVALID_REQUEST", "session inboxes cannot be consumed by fresh wake workers")
	}
	// Direct/topic publication uses this same collection lock. Hold it through
	// pool assignment commit so a watch cannot pass a later committed delivery
	// while an earlier delivery position remains uncommitted.
	if err = lock(ctx, tx, "agent-events:"+repo); err != nil {
		return WakeResult{}, err
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
		if w.WorkerID != req.WorkerID {
			return WakeResult{}, failure("IDEMPOTENCY_CONFLICT", "wake claim supervisor differs")
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
	worker, err := s.claimWorker(ctx, tx, repo, req.WorkerID)
	if err != nil {
		return WakeResult{}, err
	}
	if worker != nil {
		var ready bool
		if err = tx.QueryRow(ctx, `SELECT $1::timestamptz<=clock_timestamp()`, worker.NextLaunchAt).Scan(&ready); err != nil {
			return WakeResult{}, err
		}
		if worker.Health != "available" || !ready {
			return WakeResult{}, nil
		}
	}
	if _, err = expireRequestDeliveries(ctx, tx, repo, s.channel.Principal, dest.AllowLocal); err != nil {
		return WakeResult{}, err
	}
	var candidate poolCandidate
	var spec WorkerSpec
	if worker != nil {
		spec = worker.Spec
		candidate, err = selectPoolCandidate(ctx, tx, *worker)
		if err != nil {
			return WakeResult{}, err
		}
	}
	var id string
	var position int64
	err = tx.QueryRow(ctx, `SELECT d.delivery_id::text,e.position FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) LEFT JOIN cairn.agent_worker_pool p ON e.destination_type='pool' AND p.repo=e.repo AND p.name=e.destination_name WHERE e.repo=$1 AND d.consumer=$2 AND (e.sensitivity='shareable' OR $3) AND e.kind='request' AND `+requestAdmissionOpen+` AND d.available_at<=clock_timestamp() AND (d.state='pending' OR (d.state='leased' AND d.lease_until<=clock_timestamp())) AND (e.destination_type<>'pool' OR (p.enabled AND e.destination_name=ANY($4::text[]) AND e.pool_requirements->>'workspace'=$5 AND COALESCE(e.pool_requirements->>'harness','') IN ('',$6) AND COALESCE(e.pool_requirements->>'model','') IN ('',$7) AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(COALESCE(e.pool_requirements->'capabilities','[]'::jsonb)) cap WHERE NOT(cap=ANY(COALESCE($8::text[],ARRAY[]::text[])))))) AND `+wakeHold+` AND `+sessionInboxHold+` ORDER BY e.position FOR UPDATE OF d SKIP LOCKED LIMIT 1`, repo, s.channel.Principal, dest.AllowLocal, spec.Pools, spec.Workspace, spec.Harness, spec.Model, spec.Capabilities).Scan(&id, &position)
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return WakeResult{}, err
	}
	if candidate.id != "" && (id == "" || candidate.position < position) {
		id, err = assignPool(ctx, tx, *worker, candidate)
		if err != nil {
			return WakeResult{}, err
		}
	}
	if id == "" {
		return WakeResult{}, tx.Commit(ctx)
	}
	if worker != nil {
		if _, err = tx.Exec(ctx, `UPDATE cairn.agent_worker_slot SET next_launch_at=clock_timestamp()+make_interval(secs=>$3) WHERE repo=$1 AND consumer=$2`, repo, s.channel.Principal, worker.Spec.LaunchSpacingSeconds); err != nil {
			return WakeResult{}, err
		}
	}
	lease := uuid.NewString()
	if _, err = tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='leased',lease_id=$2,lease_until=clock_timestamp()+interval '90 seconds',attempts=attempts+1 WHERE delivery_id=$1`, id, lease); err != nil {
		return WakeResult{}, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.agent_wake_attempt(attempt_id,delivery_id,repo,lease_id,worker_id) VALUES($1,$2,$3,$4,NULLIF($5,'')::uuid)`, req.RequestID, id, repo, lease, req.WorkerID); err != nil {
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
	case "start", "enter", "link", "session", "report", "finish":
	default:
		return WakeAttempt{}, failure("INVALID_REQUEST", "unknown wake operation")
	}
	if (req.Operation == "start" || req.Operation == "enter" || req.Operation == "link") && dest.Name != "hosted" {
		return WakeAttempt{}, failure("DESTINATION_PROHIBITED", "fresh wake workers currently require a hosted profile")
	}
	if req.Operation == "session" {
		if req.Session == nil {
			return WakeAttempt{}, failure("INVALID_REQUEST", "native session association required")
		}
		if err := req.Session.Validate(); err != nil {
			return WakeAttempt{}, err
		}
	} else if req.Session != nil {
		return WakeAttempt{}, failure("INVALID_REQUEST", "session is only valid for the session operation")
	}
	if req.ProviderFailure != nil {
		if (req.Operation != "report" && req.Operation != "finish") || req.ProcessState == "prelaunch_failed" {
			return WakeAttempt{}, failure("INVALID_REQUEST", "provider failure belongs only to an executed worker report or confirmed finish")
		}
		if err := req.ProviderFailure.Validate(); err != nil {
			return WakeAttempt{}, err
		}
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
		if req.Operation == "start" || req.Operation == "enter" {
			stop, _, err := applyWakeControl(ctx, tx, w)
			if err != nil {
				return w, err
			}
			if stop != "" {
				return s.readWake(ctx, tx, w.ID, dest)
			}
		}
		if req.Operation == "start" || req.Operation == "enter" || req.Operation == "link" || req.Operation == "session" {
			if err := s.checkWakeWorker(ctx, tx, w); err != nil {
				return w, err
			}
		}
		switch req.Operation {
		case "session":
			if err := s.attachWakeSession(ctx, tx, w, *req.Session, dest); err != nil {
				return w, err
			}
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
				if errors.Is(err, pgx.ErrNoRows) {
					return w, failure("NOT_FOUND", "receipt not found")
				}
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
			if err = s.recordWakeProviderFailure(ctx, tx, w, req.ProviderFailure); err != nil {
				return w, err
			}
			if _, err = tx.Exec(ctx, `UPDATE cairn.agent_wake_attempt SET process_state=$2,reason=$3 WHERE attempt_id=$1`, w.ID, req.ProcessState, req.Reason); err != nil {
				return w, err
			}
		case "finish":
			if _, _, err = applyWakeControl(ctx, tx, w); err != nil {
				return w, err
			}
			if err = s.recordWakeProviderFailure(ctx, tx, w, req.ProviderFailure); err != nil {
				return w, err
			}
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
			// Preserve the prelaunch fact before replacing the state with finished.
			// Older finished attempts without this report remain execution-uncertain.
			if _, err = tx.Exec(ctx, `UPDATE cairn.agent_wake_attempt SET process_state=CASE WHEN state='prepared' AND process_state='' THEN 'prelaunch_failed' ELSE process_state END,state='finished',finished_at=clock_timestamp(),reason=CASE WHEN reason='' THEN $2 ELSE reason END WHERE attempt_id=$1`, w.ID, req.Reason); err != nil {
				return w, err
			}
			if w.WorkerID != "" && d.State != "handled" && d.State != "ignored" && d.Control == nil {
				if _, err = tx.Exec(ctx, `UPDATE cairn.agent_worker_slot SET next_launch_at=greatest(next_launch_at,clock_timestamp()+interval '30 seconds') WHERE repo=$1 AND consumer=$2 AND supervisor_id=$3`, w.Delivery.Event.Repo, s.channel.Principal, w.WorkerID); err != nil {
					return w, err
				}
			}
			if w.Session != nil {
				if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session SET stopped=true,expires_at=clock_timestamp() WHERE agent_id=$1 AND execution_id=$2`, w.Session.AgentID, w.Session.ExecutionID); err != nil {
					return w, err
				}
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
