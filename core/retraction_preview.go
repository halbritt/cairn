package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"errors"
	"slices"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type RetractionPreview struct {
	SupportingEvidence []RecordSupportingEvidence `json:"supporting_evidence"`
	DeletionTargets    []DeletionTarget           `json:"deletion_targets,omitempty"`
	Dependents         []RecordVersionRef         `json:"dependents"`
	PreviewID          string                     `json:"preview_id"`
	RecordID           string                     `json:"record_id"`
	Version            int                        `json:"version"`
	ExpiresAt          time.Time                  `json:"expires_at"`
	Uses               []Impact                   `json:"uses"`
	Coverage           string                     `json:"coverage"`
}

// RecordSupportingEvidence keeps each citation attached to its exact retained
// version. Evidence metadata excludes captured source bodies and labels.
type RecordSupportingEvidence struct {
	RecordVersionRef
	Evidence []Evidence `json:"evidence"`
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
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
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
	err := tx.QueryRow(ctx, `SELECT p.version=m.current_version AND p.use_generation=cairn.record_exposure_generation(m.record_id) AND p.expires_at>clock_timestamp(),p.dependency_digest
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
	return advanceExposureBarriersTx(ctx, tx, refs)
}

func advanceExposureBarriersTx(ctx context.Context, tx pgx.Tx, refs []RecordVersionRef) error {
	seen := make(map[string]bool, len(refs))
	ids := make([]string, 0, len(refs))
	for _, r := range refs {
		if !seen[r.RecordID] {
			seen[r.RecordID] = true
			ids = append(ids, r.RecordID)
		}
	}
	slices.Sort(ids)
	mRows, err := tx.Query(ctx, `SELECT record_id::text FROM cairn.memory_record WHERE record_id = ANY($1::uuid[]) ORDER BY record_id FOR NO KEY UPDATE`, ids)
	if err != nil {
		return err
	}
	for mRows.Next() {
	}
	err = mRows.Err()
	mRows.Close()
	if err != nil {
		return err
	}
	if _, err := tx.Exec(ctx, `INSERT INTO cairn.record_exposure_barrier(record_id, exposure_epoch)
 SELECT unnest($1::uuid[]), 0 ON CONFLICT (record_id) DO UPDATE SET exposure_epoch = cairn.record_exposure_barrier.exposure_epoch`, ids); err != nil {
		return err
	}
	rows, err := tx.Query(ctx, `SELECT record_id::text FROM cairn.record_exposure_barrier WHERE record_id = ANY($1::uuid[]) ORDER BY record_id FOR UPDATE`, ids)
	if err != nil {
		return err
	}
	for rows.Next() {
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `WITH drained AS (
 DELETE FROM cairn.record_exposure WHERE record_id = ANY($1::uuid[]) RETURNING record_id
), counts AS (
 SELECT record_id, count(*)::bigint AS n FROM drained GROUP BY record_id
)
UPDATE cairn.memory_record m SET use_generation = m.use_generation + c.n
FROM counts c WHERE m.record_id = c.record_id`, ids); err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `UPDATE cairn.record_exposure_barrier SET exposure_epoch = exposure_epoch + 1 WHERE record_id = ANY($1::uuid[])`, ids)
	return err
}

func dependencyStateDigest(ctx context.Context, tx pgx.Tx, refs []RecordVersionRef) ([]byte, error) {
	var state []byte
	err := tx.QueryRow(ctx, `WITH ids AS (
 SELECT DISTINCT record_id FROM jsonb_to_recordset($1) AS d(record_id uuid,version integer)
), exp AS (
 SELECT e.record_id, count(*)::bigint AS n FROM cairn.record_exposure e JOIN ids USING(record_id) GROUP BY e.record_id
)
SELECT jsonb_agg(jsonb_build_array(d.record_id,d.version,m.current_version,m.lifecycle,m.use_generation + COALESCE(exp.n,0)) ORDER BY d.record_id,d.version)::text
 FROM jsonb_to_recordset($1) AS d(record_id uuid,version integer) JOIN cairn.memory_record m ON m.record_id=d.record_id LEFT JOIN exp ON exp.record_id=m.record_id`, refs).Scan(&state)
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
	result := RetractionPreview{PreviewID: uuid.NewString(), RecordID: recordID, Version: record.Version, Uses: []Impact{}, Coverage: "Retained versions, known versioned relation dependents, their supporting evidence and recorded exposures; at most 1000 versions, 1000 evidence references and 1000 uses. Evidence metadata checks retained inline bytes, not source truth or upstream freshness. Unknown derivations and unmanaged dependencies are not inferred."}
	var generation int64
	if err = tx.QueryRow(ctx, `SELECT cairn.record_exposure_generation($1)`, recordID).Scan(&generation); err != nil {
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
	result.SupportingEvidence, err = previewSupportingEvidence(ctx, tx, result.Dependents)
	if err != nil {
		return RetractionPreview{}, err
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

func previewSupportingEvidence(ctx context.Context, tx pgx.Tx, refs []RecordVersionRef) ([]RecordSupportingEvidence, error) {
	// Count links before loading any captured bodies for their integrity check.
	// One shared source cited by two versions counts twice: both citations matter.
	rows, err := tx.Query(ctx, `SELECT e.record_id::text,e.version,count(*)
 FROM cairn.evidence_ref e JOIN jsonb_to_recordset($1) AS d(record_id uuid,version integer)
 ON d.record_id=e.record_id AND d.version=e.version
 GROUP BY e.record_id,e.version ORDER BY e.record_id,e.version`, refs)
	if err != nil {
		return nil, err
	}
	total := int64(0)
	versions, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (RecordVersionRef, error) {
		var ref RecordVersionRef
		var count int64
		err := row.Scan(&ref.RecordID, &ref.Version, &count)
		total += count
		return ref, err
	})
	if err != nil {
		return nil, err
	}
	if total > 1000 {
		return nil, failure("BUDGET_REFUSED", "retraction impact exceeds 1000 evidence references; no preview token issued")
	}
	result := make([]RecordSupportingEvidence, 0, len(versions))
	for _, ref := range versions {
		evidence, err := supportingEvidence(ctx, tx, ref.RecordID, ref.Version)
		if err != nil {
			return nil, err
		}
		result = append(result, RecordSupportingEvidence{ref, evidence})
	}
	return result, nil
}
