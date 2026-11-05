package core

import (
	"context"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type RestoreFenceRequest struct {
	RequestID string `json:"request_id"`
	Reason    string `json:"reason"`
}
type RestoreFence struct {
	FenceID    string `json:"fence_id"`
	Generation int64  `json:"generation"`
	Receipts   int64  `json:"receipts"`
	Sessions   int64  `json:"sessions"`
}

// FenceRestore invalidates delivery capabilities from the previous database
// generation. It preserves historical receipts and does not stop live processes.
// The operator must isolate the restored service before invoking it.
func (s *Store) FenceRestore(ctx context.Context, req RestoreFenceRequest) (RestoreFence, error) {
	ctx = s.recoveryContext(ctx)
	if !s.channel.Operator || s.channel.Repo != "" {
		return RestoreFence{}, failure("AUTHORITY_DENIED", "restore fencing requires an unscoped operator channel")
	}
	if err := reasonValid(req.Reason); err != nil {
		return RestoreFence{}, err
	}
	return mutate(ctx, s, "fence-restore", req.RequestID, req, func(tx pgx.Tx) (RestoreFence, error) {
		return fenceRestore(ctx, tx, req.Reason)
	})
}

// Holding this shared row lock until commit orders compilation, launch claims,
// binding and expansion against a restore fence. Under repeatable read, a fence
// committed after the snapshot forces a serialization retry instead of stamping
// old source state with a fresh generation.
func retrievalGeneration(ctx context.Context, tx pgx.Tx) (int64, error) {
	var generation int64
	err := tx.QueryRow(ctx, `SELECT generation FROM cairn.retrieval_generation WHERE singleton FOR SHARE`).Scan(&generation)
	return generation, err
}
func receiptCurrentGeneration(ctx context.Context, tx pgx.Tx, id string) error {
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return err
	}
	var current bool
	err = tx.QueryRow(ctx, `SELECT generation=$2 FROM cairn.retrieval_receipt WHERE receipt_id=$1`, id, generation).Scan(&current)
	if err != nil {
		return err
	}
	if !current {
		return failure("STALE_PACKAGE", "receipt predates the restore fence; compile with a new request ID")
	}
	return nil
}

func fenceRestore(ctx context.Context, tx pgx.Tx, reason string) (RestoreFence, error) {
	result := RestoreFence{FenceID: uuid.NewString()}
	err := tx.QueryRow(ctx, `UPDATE cairn.retrieval_generation SET generation=generation+1 WHERE singleton RETURNING generation`).Scan(&result.Generation)
	if err != nil {
		return result, err
	}
	if err = tx.QueryRow(ctx, `SELECT count(*) FROM cairn.retrieval_receipt WHERE generation=$1`, result.Generation-1).Scan(&result.Receipts); err != nil {
		return result, err
	}
	tag, err := tx.Exec(ctx, `UPDATE cairn.index_session SET expires_at=clock_timestamp() WHERE expires_at>clock_timestamp()`)
	if err != nil {
		return result, err
	}
	result.Sessions = tag.RowsAffected()
	// Delivery leases are capabilities from the pre-restore world. Retain their
	// attempt counts and events, but require a new claim after the fence.
	if err = releaseLeasedDeliveries(ctx, tx); err != nil {
		return result, err
	}
	// A restored backup can omit later replies or scheduled effects. Close open
	// collections and pending occurrences for review without inventing their outcome.
	if _, err = tx.Exec(ctx, `UPDATE cairn.agent_response_group SET state='incomplete',code='restore_fenced',closed_at=clock_timestamp() WHERE state='open'`); err != nil {
		return result, err
	}
	if _, err = tx.Exec(ctx, `UPDATE cairn.agent_schedule SET state='skipped',code='restore_fenced',finished_at=clock_timestamp() WHERE state='pending'`); err != nil {
		return result, err
	}
	if _, err = tx.Exec(ctx, `UPDATE cairn.agent_wait SET state='restore_fenced',resolved_at=clock_timestamp(),terminal_reason='restore_fenced' WHERE state='waiting'`); err != nil {
		return result, err
	}
	if _, err = tx.Exec(ctx, `UPDATE cairn.agent_wait_outbox SET state='restore_fenced',updated_at=clock_timestamp() WHERE state='pending'`); err != nil {
		return result, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.agent_wait_notification_fence(event_uuid,generation,fenced_at)
SELECT reserved_notice_event_uuid,$1,clock_timestamp() FROM cairn.agent_wait WHERE generation<$1
ON CONFLICT (event_uuid,generation) DO NOTHING`, result.Generation); err != nil {
		return result, err
	}
	if _, err = tx.Exec(ctx, `UPDATE cairn.work_item SET cache_generation=0, refresh_error='database restore fence: projection invalidated'`); err != nil {
		return result, err
	}
	if err = ReapplyExclusions(ctx, tx); err != nil {
		return result, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO cairn.restore_fence(fence_id,generation,reason) VALUES($1,$2,$3)`, result.FenceID, result.Generation, reason)
	return result, err
}
