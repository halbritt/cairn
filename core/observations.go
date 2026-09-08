package core

import (
	"context"
	"encoding/hex"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type DeliveryRequest struct {
	RequestID      string `json:"request_id"`
	ReceiptID      string `json:"receipt_id"`
	Adapter        string `json:"adapter"`
	Carrier        string `json:"carrier"`
	Assurance      string `json:"assurance"`
	RenderedSHA256 string `json:"rendered_sha256"`
	BlindSpots     string `json:"blind_spots"`
}
type OutcomeRequest struct {
	RequestID    string `json:"request_id"`
	ReceiptID    string `json:"receipt_id"`
	ExitCode     *int   `json:"exit_code"`
	DurationMS   int64  `json:"duration_ms"`
	ProcessState string `json:"process_state"`
	StdoutSHA256 string `json:"stdout_sha256"`
	StderrSHA256 string `json:"stderr_sha256"`
}
type Observation struct {
	ID string `json:"observation_id"`
}

func digestValid(digest string) bool {
	decoded, err := hex.DecodeString(digest)
	return err == nil && len(decoded) == 32
}

// ClaimRun is deliberately not replay-successful: after an ambiguous commit,
// executing the process again would duplicate an external effect.
func (s *Store) ClaimRun(ctx context.Context, id string) error {
	if !s.channel.Instrumented {
		return failure("AUTHORITY_DENIED", "launch requires a service channel")
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if err = s.receiptAccess(ctx, tx, id); err != nil {
		return err
	}
	if err = receiptDeliveryCurrent(ctx, tx, id); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `SELECT receipt_id FROM cairn.retrieval_receipt WHERE receipt_id=$1 FOR UPDATE`, id); err != nil {
		return err
	}
	if err = s.boundAttemptCurrent(ctx, tx, id); err != nil {
		return err
	}
	if err = receiptPayloadAvailable(ctx, tx, id); err != nil {
		return err
	}
	tag, err := tx.Exec(ctx, `UPDATE cairn.retrieval_receipt SET launch_claimed=true WHERE receipt_id=$1 AND NOT launch_claimed`, id)
	if err != nil {
		return err
	}
	if tag.RowsAffected() != 1 {
		return failure("RUN_ALREADY_STARTED", "this receipt already claimed a launch; inspect its outcome before starting a new run")
	}
	return tx.Commit(ctx)
}
func (s *Store) receiptAccess(ctx context.Context, tx pgx.Tx, id string) error {
	if err := validID(id); err != nil {
		return err
	}
	var caller, repo string
	err := tx.QueryRow(ctx, `SELECT caller,scope->>'repo' FROM cairn.retrieval_receipt WHERE receipt_id=$1`, id).Scan(&caller, &repo)
	if err == pgx.ErrNoRows {
		return failure("NOT_FOUND", "receipt not found")
	}
	if err != nil {
		return err
	}
	if caller != s.channel.Principal {
		return failure("AUTHORITY_DENIED", "receipt belongs to a different channel")
	}
	return s.checkRepo(repo)
}

func (s *Store) RecordDelivery(ctx context.Context, req DeliveryRequest) (Observation, error) {
	if !s.channel.Instrumented {
		return Observation{}, failure("AUTHORITY_DENIED", "delivery observation requires a service channel")
	}
	if req.Adapter == "" || len(req.Adapter) > 256 || !digestValid(req.RenderedSHA256) || len(req.BlindSpots) > 2000 {
		return Observation{}, failure("INVALID_REQUEST", "invalid delivery metadata")
	}
	switch req.Carrier {
	case "stdin", "argv", "file":
	default:
		return Observation{}, failure("INVALID_REQUEST", "unknown carrier")
	}
	switch req.Assurance {
	case "available", "delivered", "failed", "unknown":
	default:
		return Observation{}, failure("INVALID_REQUEST", "unknown assurance")
	}
	return mutate(ctx, s, "delivery", req.RequestID, req, func(tx pgx.Tx) (Observation, error) {
		if err := s.receiptAccess(ctx, tx, req.ReceiptID); err != nil {
			return Observation{}, err
		}
		id := uuid.NewString()
		_, err := tx.Exec(ctx, `INSERT INTO cairn.delivery_receipt(delivery_id,receipt_id,adapter,carrier,assurance,rendered_sha256,blind_spots) VALUES($1,$2,$3,$4,$5,$6,$7)`, id, req.ReceiptID, req.Adapter, req.Carrier, req.Assurance, req.RenderedSHA256, req.BlindSpots)
		return Observation{id}, err
	})
}
func (s *Store) RecordOutcome(ctx context.Context, req OutcomeRequest) (Observation, error) {
	ctx = context.WithValue(ctx, recoveryTransactionKey{}, true)
	if !s.channel.Instrumented {
		return Observation{}, failure("AUTHORITY_DENIED", "outcome requires a service channel")
	}
	if req.DurationMS < 0 || !digestValid(req.StdoutSHA256) || !digestValid(req.StderrSHA256) {
		return Observation{}, failure("INVALID_REQUEST", "invalid outcome metadata")
	}
	switch req.ProcessState {
	case "exited", "launch_failed", "timeout", "cancelled", "unknown":
	default:
		return Observation{}, failure("INVALID_REQUEST", "unknown process state")
	}
	if req.ProcessState == "exited" && req.ExitCode == nil {
		return Observation{}, failure("INVALID_REQUEST", "exited process requires an observed exit code")
	}
	return mutate(ctx, s, "outcome", req.RequestID, req, func(tx pgx.Tx) (Observation, error) {
		if err := s.receiptAccess(ctx, tx, req.ReceiptID); err != nil {
			return Observation{}, err
		}
		taskOutcome := "unknown"
		if req.ProcessState == "launch_failed" {
			taskOutcome = "not_attempted"
		}
		id := uuid.NewString()
		_, err := tx.Exec(ctx, `INSERT INTO cairn.run_outcome(outcome_id,receipt_id,exit_code,duration_ms,process_state,task_outcome,stdout_sha256,stderr_sha256) VALUES($1,$2,$3,$4,$5,$6,$7,$8)`, id, req.ReceiptID, req.ExitCode, req.DurationMS, req.ProcessState, taskOutcome, req.StdoutSHA256, req.StderrSHA256)
		return Observation{id}, err
	})
}

type UsageRequest struct {
	RequestID string `json:"request_id"`
	ReceiptID string `json:"receipt_id"`
	RecordID  string `json:"record_id"`
	Version   int    `json:"version"`
	Signal    string `json:"signal"`
	Method    string `json:"method,omitempty"`
}

func (s *Store) RecordUsage(ctx context.Context, req UsageRequest) (Observation, error) {
	witness := "testimony"
	if req.Signal == "behaviorally_implicated" {
		if strings.TrimSpace(req.Method) == "" || len(req.Method) > 256 {
			return Observation{}, failure("INVALID_REQUEST", "inferred usage requires a bounded method/version label")
		}
		if !s.channel.Instrumented {
			return Observation{}, failure("AUTHORITY_DENIED", "inferred usage requires a service observer")
		}
		witness = "inferred"
	} else if req.Signal != "cited" && req.Signal != "expanded" {
		return Observation{}, failure("INVALID_REQUEST", "unknown usage signal")
	}
	if len(req.Method) > 256 {
		return Observation{}, failure("INVALID_REQUEST", "usage method too long")
	}

	if err := validID(req.RecordID); err != nil {
		return Observation{}, err
	}
	return mutate(ctx, s, "usage", req.RequestID, req, func(tx pgx.Tx) (Observation, error) {
		if err := s.receiptAccess(ctx, tx, req.ReceiptID); err != nil {
			return Observation{}, err
		}
		var selected bool
		if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.record_use WHERE receipt_id=$1 AND record_id=$2 AND version=$3)`, req.ReceiptID, req.RecordID, req.Version).Scan(&selected); err != nil {
			return Observation{}, err
		}
		if !selected {
			return Observation{}, failure("INVALID_REQUEST", "citation must name an exposed record version")
		}
		id := uuid.NewString()
		_, err := tx.Exec(ctx, `INSERT INTO cairn.usage_observation(observation_id,receipt_id,record_id,version,signal,witness,method) VALUES($1,$2,$3,$4,$5,$6,$7)`, id, req.ReceiptID, req.RecordID, req.Version, req.Signal, witness, req.Method)
		return Observation{id}, err
	})
}

type UsageCoverageRequest struct {
	RequestID string `json:"request_id"`
	ReceiptID string `json:"receipt_id"`
	Coverage  string `json:"coverage"`
	Method    string `json:"method"`
}

func (s *Store) RecordUsageCoverage(ctx context.Context, req UsageCoverageRequest) (Observation, error) {
	if !s.channel.Instrumented {
		return Observation{}, failure("AUTHORITY_DENIED", "coverage requires a service observer")
	}
	if (req.Coverage != "unknown" && req.Coverage != "partial" && req.Coverage != "complete") || strings.TrimSpace(req.Method) == "" || len(req.Method) > 256 {
		return Observation{}, failure("INVALID_REQUEST", "valid coverage and observer method/version required")
	}
	return mutate(ctx, s, "usage-coverage", req.RequestID, req, func(tx pgx.Tx) (Observation, error) {
		if err := s.receiptAccess(ctx, tx, req.ReceiptID); err != nil {
			return Observation{}, err
		}
		if _, err := tx.Exec(ctx, `SELECT 1 FROM cairn.retrieval_receipt WHERE receipt_id=$1 FOR UPDATE`, req.ReceiptID); err != nil {
			return Observation{}, err
		}
		id := uuid.NewString()
		_, err := tx.Exec(ctx, `INSERT INTO cairn.usage_coverage(observation_id,receipt_id,coverage,method) VALUES($1,$2,$3,$4)`, id, req.ReceiptID, req.Coverage, req.Method)
		return Observation{id}, err
	})
}
