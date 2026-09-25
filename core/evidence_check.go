package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
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
type EvidenceCheckPage struct {
	Checks    []EvidenceCheck `json:"checks"`
	More      bool            `json:"more"`
	NextAfter int             `json:"next_after,omitempty"`
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
	page, err := s.EvidenceChecksPage(ctx, id, 0, 1000)
	if err != nil {
		return nil, err
	}
	if page.More {
		return nil, failure("BUDGET_REFUSED", "evidence check history exceeds 1000 observations; use a paged read")
	}
	return page.Checks, nil
}

// EvidenceChecksPage reads an append-only history with a generation cursor.
// Each page repeats the current repository check; an earlier page is not access.
func (s *Store) EvidenceChecksPage(ctx context.Context, id string, after, limit int) (EvidenceCheckPage, error) {
	out := EvidenceCheckPage{Checks: []EvidenceCheck{}}
	ctx = s.recoveryContext(ctx)
	if err := validID(id); err != nil {
		return out, err
	}
	if after < 0 || after > 2147483647 || limit < 1 || limit > 1000 {
		return out, failure("INVALID_REQUEST", "evidence check page requires after generation 0-2147483647 and limit 1-1000")
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	var repo string
	err = tx.QueryRow(ctx, `SELECT repo FROM cairn.evidence WHERE evidence_id=$1`, id).Scan(&repo)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, failure("NOT_FOUND", "evidence not found")
	}
	if err != nil {
		return out, err
	}
	if err = s.checkRepo(repo); err != nil {
		return out, err
	}
	rows, err := tx.Query(ctx, `SELECT detail FROM cairn.evidence_check WHERE evidence_id=$1 AND generation>$2 ORDER BY generation LIMIT $3`, id, after, limit+1)
	if err != nil {
		return out, err
	}
	results, err := pgx.CollectRows(rows, pgx.RowTo[EvidenceCheck])
	if err != nil {
		return out, err
	}
	if len(results) > limit {
		out.More = true
		results = results[:limit]
	}
	out.Checks = results
	if len(results) > 0 {
		out.NextAfter = results[len(results)-1].Generation
	}
	return out, tx.Commit(ctx)
}
