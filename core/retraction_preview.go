package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type RetractionPreview struct {
	DeletionTargets []DeletionTarget   `json:"deletion_targets,omitempty"`
	Dependents      []RecordVersionRef `json:"dependents"`
	PreviewID       string             `json:"preview_id"`
	RecordID        string             `json:"record_id"`
	Version         int                `json:"version"`
	ExpiresAt       time.Time          `json:"expires_at"`
	Uses            []Impact           `json:"uses"`
	Coverage        string             `json:"coverage"`
}

// PreviewRetraction covers retained versions, explicit versioned dependencies
// and their exposures. Oversized results are refused rather than certified as
// complete. The token proves this caller obtained the preview, not human review.
func (s *Store) PreviewRetraction(ctx context.Context, recordID string) (RetractionPreview, error) {
	return s.previewRetraction(ctx, recordID, false)
}

func (s *Store) previewRetraction(ctx context.Context, recordID string, deletion bool) (RetractionPreview, error) {
	if err := validID(recordID); err != nil {
		return RetractionPreview{}, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return RetractionPreview{}, err
	}
	defer tx.Rollback(ctx)
	result, err := s.previewRetractionTx(ctx, tx, recordID, deletion)
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
	var expectedDigest []byte
	err := tx.QueryRow(ctx, `SELECT p.version=m.current_version AND p.use_generation=m.use_generation AND p.expires_at>clock_timestamp(),p.dependency_digest
 FROM cairn.retraction_preview p JOIN cairn.memory_record m USING(record_id)
 WHERE p.preview_id=$1 AND p.record_id=$2 AND p.caller=$3`, req.PreviewID, req.RecordID, s.channel.Principal).Scan(&current, &expectedDigest)
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("IMPACT_PREVIEW_REQUIRED", "no matching caller-owned retraction preview")
	}
	if err != nil {
		return err
	}
	refs, err := dependentVersions(ctx, tx, req.RecordID)
	if err != nil {
		return err
	}
	actualDigest, err := dependencyStateDigest(ctx, tx, refs)
	if err != nil {
		return err
	}
	current = current && bytes.Equal(actualDigest, expectedDigest)
	if !current {
		return failure("STALE_PREVIEW", "record or exposure state changed, or preview expired; obtain a new preview")
	}
	return nil
}

func dependencyStateDigest(ctx context.Context, tx pgx.Tx, refs []RecordVersionRef) ([]byte, error) {
	var state []byte
	err := tx.QueryRow(ctx, `SELECT jsonb_agg(jsonb_build_array(d.record_id,d.version,m.current_version,m.lifecycle,m.use_generation) ORDER BY d.record_id,d.version)::text
 FROM jsonb_to_recordset($1) AS d(record_id uuid,version integer) JOIN cairn.memory_record m ON m.record_id=d.record_id`, refs).Scan(&state)
	if err != nil {
		return nil, err
	}
	sum := sha256.Sum256(state)
	return sum[:], nil
}

func (s *Store) previewRetractionTx(ctx context.Context, tx pgx.Tx, recordID string, deletion bool) (RetractionPreview, error) {
	record, err := readRecord(ctx, tx, recordID)
	if err != nil {
		return RetractionPreview{}, err
	}
	if err = s.checkRepo(record.Scope.Repo); err != nil {
		return RetractionPreview{}, err
	}
	if record.Lifecycle == "tombstoned" || (!deletion && record.Lifecycle != "active") {
		return RetractionPreview{}, failure("VERSION_CONFLICT", "record is already inactive")
	}
	result := RetractionPreview{PreviewID: uuid.NewString(), RecordID: recordID, Version: record.Version, Uses: []Impact{}, Coverage: "Retained versions, known versioned relation dependents and their exposures; at most 1000 versions and 1000 uses. Unknown derivations and unmanaged evidence dependencies are not inferred."}
	var generation int64
	if err = tx.QueryRow(ctx, `SELECT use_generation FROM cairn.memory_record WHERE record_id=$1`, recordID).Scan(&generation); err != nil {
		return result, err
	}
	result.Dependents, err = dependentVersions(ctx, tx, recordID)
	if err != nil {
		return result, err
	}
	dependencyDigest, err := dependencyStateDigest(ctx, tx, result.Dependents)
	if err != nil {
		return result, err
	}
	rows, err := tx.Query(ctx, `SELECT u.record_id::text,u.receipt_id::text,u.version,u.purpose,r.scope FROM cairn.record_use u JOIN cairn.retrieval_receipt r USING(receipt_id)
 JOIN jsonb_to_recordset($1) AS d(record_id uuid,version integer) ON d.record_id=u.record_id AND d.version=u.version
 ORDER BY u.used_at,u.receipt_id,u.record_id,u.version LIMIT 1001`, result.Dependents)
	if err != nil {
		return result, err
	}
	for rows.Next() {
		var item Impact
		if err = rows.Scan(&item.RecordID, &item.ReceiptID, &item.Version, &item.Purpose, &item.Scope); err != nil {
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
	var inventoryDigest []byte
	if deletion {
		result.DeletionTargets, err = deletionInventory(ctx, tx, recordID, result.Dependents)
		if err != nil {
			return result, err
		}
		inventoryDigest, err = deletionInventoryDigest(result.DeletionTargets)
		if err != nil {
			return result, err
		}
	}
	err = tx.QueryRow(ctx, `INSERT INTO cairn.retraction_preview(preview_id,record_id,version,use_generation,dependency_digest,deletion_inventory_digest) VALUES($1,$2,$3,$4,$5,$6) RETURNING expires_at`, result.PreviewID, recordID, record.Version, generation, dependencyDigest, inventoryDigest).Scan(&result.ExpiresAt)
	if err != nil {
		return result, err
	}
	return result, nil
}
