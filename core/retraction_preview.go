package core

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type RetractionPreview struct {
	PreviewID string    `json:"preview_id"`
	RecordID  string    `json:"record_id"`
	Version   int       `json:"version"`
	ExpiresAt time.Time `json:"expires_at"`
	Uses      []Impact  `json:"uses"`
	Coverage  string    `json:"coverage"`
}

// PreviewRetraction covers all retained versions and direct runs. Cairn does not
// yet represent cross-record supersession/dependency edges; do not imply a
// transitive impact analysis. Refuse an oversized preview rather than certify
// an incomplete list. The token confirms that this caller obtained the preview,
// not that a human read it or accepted its consequences.
func (s *Store) PreviewRetraction(ctx context.Context, recordID string) (RetractionPreview, error) {
	if err := validID(recordID); err != nil {
		return RetractionPreview{}, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return RetractionPreview{}, err
	}
	defer tx.Rollback(ctx)
	record, err := readRecord(ctx, tx, recordID)
	if err != nil {
		return RetractionPreview{}, err
	}
	if err = s.checkRepo(record.Scope.Repo); err != nil {
		return RetractionPreview{}, err
	}
	if record.Lifecycle != "active" {
		return RetractionPreview{}, failure("VERSION_CONFLICT", "record is already inactive")
	}
	result := RetractionPreview{PreviewID: uuid.NewString(), RecordID: recordID, Version: record.Version, Uses: []Impact{}, Coverage: "All retained versions and direct exposures; cross-record dependency impact is not implemented."}
	var generation int64
	if err = tx.QueryRow(ctx, `SELECT use_generation FROM cairn.memory_record WHERE record_id=$1`, recordID).Scan(&generation); err != nil {
		return result, err
	}
	rows, err := tx.Query(ctx, `SELECT u.receipt_id::text,u.version,u.purpose,r.scope FROM cairn.record_use u JOIN cairn.retrieval_receipt r USING(receipt_id) WHERE u.record_id=$1 ORDER BY u.used_at,u.receipt_id LIMIT 1001`, recordID)
	if err != nil {
		return result, err
	}
	for rows.Next() {
		var item Impact
		if err = rows.Scan(&item.ReceiptID, &item.Version, &item.Purpose, &item.Scope); err != nil {
			rows.Close()
			return result, err
		}
		result.Uses = append(result.Uses, item)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return result, err
	}
	if len(result.Uses) > 1000 {
		return RetractionPreview{}, failure("BUDGET_REFUSED", "retraction impact exceeds 1000 uses; no preview token issued")
	}
	err = tx.QueryRow(ctx, `INSERT INTO cairn.retraction_preview(preview_id,record_id,version,use_generation) VALUES($1,$2,$3,$4) RETURNING expires_at`, result.PreviewID, recordID, record.Version, generation).Scan(&result.ExpiresAt)
	if err != nil {
		return result, err
	}
	return result, tx.Commit(ctx)
}

// Called after locking the record in the privileged transaction. Concurrent
// exposure updates its generation and forces either stale-token refusal or a
// serialization retry against fresh state.
func (s *Store) checkRetractionPreview(ctx context.Context, tx pgx.Tx, req RetractRequest) error {
	if req.PreviewID == "" {
		return failure("IMPACT_PREVIEW_REQUIRED", "obtain a retraction preview before committing")
	}
	if err := validID(req.PreviewID); err != nil {
		return err
	}
	var current bool
	err := tx.QueryRow(ctx, `SELECT p.version=m.current_version AND p.use_generation=m.use_generation AND p.expires_at>clock_timestamp()
 FROM cairn.retraction_preview p JOIN cairn.memory_record m USING(record_id)
 WHERE p.preview_id=$1 AND p.record_id=$2 AND p.caller=$3`, req.PreviewID, req.RecordID, s.channel.Principal).Scan(&current)
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("IMPACT_PREVIEW_REQUIRED", "no matching caller-owned retraction preview")
	}
	if err != nil {
		return err
	}
	if !current {
		return failure("STALE_PREVIEW", "record or exposure state changed, or preview expired; obtain a new preview")
	}
	return nil
}
