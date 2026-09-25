package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"github.com/jackc/pgx/v5"
	"time"
)

type EvidenceCheckRequest struct {
	RequestID  string `json:"request_id"`
	EvidenceID string `json:"evidence_id"`
}
type EvidenceCheck struct {
	EvidenceID      string    `json:"evidence_id"`
	Generation      int       `json:"generation"`
	State           string    `json:"state"`
	ExpectedSHA256  string    `json:"expected_sha256"`
	ActualSHA256    string    `json:"actual_sha256"`
	RequestedBy     string    `json:"requested_by"`
	Method          string    `json:"method"`
	CheckedAt       time.Time `json:"checked_at"`
	AffectedRecords int       `json:"affected_records"`
}

// CheckEvidence verifies retained inline bytes, never the truth of the source
// or the current contents of a locator. It cannot accept a caller-supplied state.
func (s *Store) CheckEvidence(ctx context.Context, req EvidenceCheckRequest) (EvidenceCheck, error) {
	ctx = s.recoveryContext(ctx)
	if err := validID(req.EvidenceID); err != nil {
		return EvidenceCheck{}, err
	}
	return privileged(ctx, s, "check-evidence", req.RequestID, req, func(tx pgx.Tx) (EvidenceCheck, error) {
		result := EvidenceCheck{EvidenceID: req.EvidenceID, RequestedBy: s.channel.Principal, Method: "inline-sha256/1", State: "resolvable"}
		var body, digest []byte
		var repo string
		err := tx.QueryRow(ctx, `SELECT body,digest,repo,check_generation FROM cairn.evidence WHERE evidence_id=$1 FOR UPDATE`, req.EvidenceID).Scan(&body, &digest, &repo, &result.Generation)
		if errors.Is(err, pgx.ErrNoRows) {
			return result, failure("NOT_FOUND", "evidence not found")
		}
		if err != nil {
			return result, err
		}
		if err = s.checkRepo(repo); err != nil {
			return EvidenceCheck{}, err
		}
		result.Generation++
		actual := sha256.Sum256(body)
		result.ExpectedSHA256 = hex.EncodeToString(digest)
		result.ActualSHA256 = hex.EncodeToString(actual[:])
		if !bytes.Equal(actual[:], digest) {
			result.State = "divergent"
		}
		if err = tx.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&result.CheckedAt); err != nil {
			return result, err
		}
		// Retain the check and invalidate every dependent impact preview in one
		// transaction. PostgreSQL counts the set without materializing IDs in Go.
		err = tx.QueryRow(ctx, `WITH invalidated AS (
 UPDATE cairn.memory_record m SET use_generation=m.use_generation+1
 WHERE EXISTS (SELECT 1 FROM cairn.evidence_ref r WHERE r.evidence_id=$1 AND r.record_id=m.record_id)
 RETURNING 1
) SELECT count(*) FROM invalidated`, req.EvidenceID).Scan(&result.AffectedRecords)
		if err != nil {
			return result, err
		}
		if _, err = tx.Exec(ctx, `UPDATE cairn.evidence SET state=$2,check_generation=$3,checked_at=$4 WHERE evidence_id=$1`, req.EvidenceID, result.State, result.Generation, result.CheckedAt); err != nil {
			return result, err
		}
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.evidence_check(evidence_id,generation,detail) VALUES($1,$2,$3)`, req.EvidenceID, result.Generation, result); err != nil {
			return result, err
		}
		return result, nil
	})
}
func (s *Store) EvidenceChecks(ctx context.Context, id string) ([]EvidenceCheck, error) {
	ctx = s.recoveryContext(ctx)
	if err := validID(id); err != nil {
		return nil, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	var repo string
	err = tx.QueryRow(ctx, `SELECT repo FROM cairn.evidence WHERE evidence_id=$1`, id).Scan(&repo)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, failure("NOT_FOUND", "evidence not found")
	}
	if err != nil {
		return nil, err
	}
	if err = s.checkRepo(repo); err != nil {
		return nil, err
	}
	rows, err := tx.Query(ctx, `SELECT detail FROM cairn.evidence_check WHERE evidence_id=$1 ORDER BY generation LIMIT 1001`, id)
	if err != nil {
		return nil, err
	}
	results, err := pgx.CollectRows(rows, pgx.RowTo[EvidenceCheck])
	if err != nil {
		return nil, err
	}
	if len(results) > 1000 {
		return nil, failure("BUDGET_REFUSED", "evidence check history exceeds 1000 observations")
	}
	return results, tx.Commit(ctx)
}
