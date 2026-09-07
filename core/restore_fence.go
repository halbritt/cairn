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
	if !s.channel.Operator || s.channel.Repo != "" {
		return RestoreFence{}, failure("AUTHORITY_DENIED", "restore fencing requires an unscoped operator channel")
	}
	if err := reasonValid(req.Reason); err != nil {
		return RestoreFence{}, err
	}
	return mutate(ctx, s, "fence-restore", req.RequestID, req, func(tx pgx.Tx) (RestoreFence, error) {
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
		_, err = tx.Exec(ctx, `INSERT INTO cairn.restore_fence(fence_id,generation,reason) VALUES($1,$2,$3)`, result.FenceID, result.Generation, req.Reason)
		return result, err
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
