package core

import (
	"context"
	"errors"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

type RunRetrievalRequest struct {
	RequestID          string `json:"request_id"`
	RunReceiptID       string `json:"run_receipt_id"`
	RetrievalReceiptID string `json:"retrieval_receipt_id"`
	ExpectedReader     string `json:"expected_reader"`
	Method             string `json:"method"`
}

type RunRetrieval struct {
	RunReceiptID       string    `json:"run_receipt_id"`
	RetrievalReceiptID string    `json:"retrieval_receipt_id"`
	Reader             string    `json:"reader"`
	Observer           string    `json:"observer"`
	Method             string    `json:"method"`
	ObservedAt         time.Time `json:"observed_at"`
}

// LinkRunRetrieval records the host's observation of another caller's retrieval.
// It grants neither receipt access nor execution rights to either caller.
func (s *Store) LinkRunRetrieval(ctx context.Context, req RunRetrievalRequest) (RunRetrieval, error) {
	if !s.channel.Instrumented {
		return RunRetrieval{}, failure("AUTHORITY_DENIED", "retrieval association requires a host observer")
	}
	for _, id := range []string{req.RunReceiptID, req.RetrievalReceiptID} {
		if err := validID(id); err != nil {
			return RunRetrieval{}, err
		}
	}
	if req.RunReceiptID == req.RetrievalReceiptID || strings.TrimSpace(req.ExpectedReader) == "" || len(req.ExpectedReader) > 256 || strings.TrimSpace(req.Method) == "" || len(req.Method) > 256 {
		return RunRetrieval{}, failure("INVALID_REQUEST", "distinct run/retrieval receipts and bounded reader/method required")
	}
	guard := func(tx pgx.Tx) error {
		if err := s.receiptAccess(ctx, tx, req.RunReceiptID); err != nil {
			return err
		}
		// BindRun, ClaimRun and RecordOutcome take the same receipt lock before
		// deciding whether a receipt can become an execution.
		rows, err := tx.Query(ctx, `SELECT receipt_id::text FROM cairn.retrieval_receipt
 WHERE receipt_id IN ($1,$2) ORDER BY receipt_id FOR UPDATE`, req.RunReceiptID, req.RetrievalReceiptID)
		if err != nil {
			return err
		}
		_, err = pgx.CollectRows(rows, pgx.RowTo[string])
		if err != nil {
			return err
		}
		var scope, attemptScope Scope
		var destination, owner string
		var started, spawned time.Time
		var terminal, outcome *time.Time
		var execution bool
		err = tx.QueryRow(ctx, `SELECT r.scope,r.destination,r.created_at,a.observed_by,
 a.repo,a.task_id,a.run_id,a.spawned_at,a.terminal_at,o.observed_at,
 r.launch_claimed OR o.receipt_id IS NOT NULL
 FROM cairn.retrieval_receipt r JOIN cairn.run_binding b USING(receipt_id)
 JOIN cairn.delegation_attempt a ON a.attempt_id=b.attempt_id
 LEFT JOIN cairn.run_outcome o USING(receipt_id)
 WHERE r.receipt_id=$1 FOR SHARE OF a`, req.RunReceiptID).Scan(&scope, &destination, &started, &owner,
			&attemptScope.Repo, &attemptScope.TaskID, &attemptScope.RunID, &spawned, &terminal, &outcome, &execution)
		if errors.Is(err, pgx.ErrNoRows) || (err == nil && !execution) {
			return failure("INVALID_REQUEST", "host run needs an observed attempt and a launch claim or outcome")
		}
		if err != nil {
			return err
		}
		if owner != s.channel.Principal || attemptScope != scope {
			return failure("AUTHORITY_DENIED", "host attempt must match observer and exact run scope")
		}
		var reader, readDestination string
		var readScope Scope
		var created time.Time
		var readIsExecution bool
		err = tx.QueryRow(ctx, `SELECT caller,scope,destination,created_at,
 launch_claimed OR EXISTS(SELECT 1 FROM cairn.run_binding b WHERE b.receipt_id=r.receipt_id)
 OR EXISTS(SELECT 1 FROM cairn.run_outcome o WHERE o.receipt_id=r.receipt_id)
 FROM cairn.retrieval_receipt r WHERE receipt_id=$1`, req.RetrievalReceiptID).Scan(&reader, &readScope, &readDestination, &created, &readIsExecution)
		if errors.Is(err, pgx.ErrNoRows) {
			return failure("NOT_FOUND", "retrieval receipt not found")
		}
		if err != nil {
			return err
		}
		if reader != req.ExpectedReader || readScope != scope || readDestination != destination {
			return failure("AUTHORITY_DENIED", "retrieval reader, scope and destination must match the observed run")
		}
		if readIsExecution || created.Before(started) || created.Before(spawned) ||
			(terminal != nil && created.After(*terminal)) || (outcome != nil && created.After(*outcome)) {
			return failure("INVALID_REQUEST", "retrieval must be a non-executing receipt created during the host run")
		}
		return nil
	}
	return mutate(ctx, s, "link-run-retrieval", req.RequestID, req, func(tx pgx.Tx) (RunRetrieval, error) {
		result := RunRetrieval{RunReceiptID: req.RunReceiptID, RetrievalReceiptID: req.RetrievalReceiptID, Method: req.Method}
		err := tx.QueryRow(ctx, `INSERT INTO cairn.run_retrieval(retrieval_receipt_id,run_receipt_id,reader,method)
 SELECT receipt_id,$2,caller,$3 FROM cairn.retrieval_receipt WHERE receipt_id=$1
 ON CONFLICT DO NOTHING RETURNING reader,observer,observed_at`, req.RetrievalReceiptID, req.RunReceiptID, req.Method).Scan(&result.Reader, &result.Observer, &result.ObservedAt)
		if errors.Is(err, pgx.ErrNoRows) {
			return result, failure("VERSION_CONFLICT", "retrieval already associated; retry its original request")
		}
		return result, err
	}, guard)
}

// Caller holds the receipt row lock. A dynamic retrieval cannot later be
// repurposed as an execution and counted a second time in outcome reports.
func requireExecutionReceipt(ctx context.Context, tx pgx.Tx, id string) error {
	var linked bool
	if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.run_retrieval WHERE retrieval_receipt_id=$1)`, id).Scan(&linked); err != nil {
		return err
	}
	if linked {
		return failure("INVALID_REQUEST", "linked retrieval is not an execution receipt")
	}
	return nil
}
