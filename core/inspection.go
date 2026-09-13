package core

import (
	"context"

	"github.com/fxamacker/cbor/v2"
	"github.com/jackc/pgx/v5"
)

type ListRequest struct {
	Repo   string `json:"repo"`
	Limit  int    `json:"limit"`
	Offset int    `json:"offset"`
}
type RecordPage struct {
	Records    []Record `json:"records"`
	More       bool     `json:"more"`
	NextOffset int      `json:"next_offset"`
}

func (s *Store) List(ctx context.Context, req ListRequest) (RecordPage, error) {
	if req.Repo == "" || req.Limit < 1 || req.Limit > 200 || req.Offset < 0 {
		return RecordPage{}, failure("INVALID_REQUEST", "exact repo, limit 1-200 and nonnegative offset required")
	}
	if err := s.checkRepo(req.Repo); err != nil {
		return RecordPage{}, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return RecordPage{}, err
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT m.record_id::text FROM cairn.memory_record m JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version WHERE v.repo=$1 AND m.lifecycle<>'tombstoned' ORDER BY m.created_at DESC,m.record_id LIMIT $2 OFFSET $3`, req.Repo, req.Limit+1, req.Offset)
	if err != nil {
		return RecordPage{}, err
	}
	ids, err := pgx.CollectRows(rows, pgx.RowTo[string])
	if err != nil {
		return RecordPage{}, err
	}
	page := RecordPage{Records: []Record{}, More: len(ids) > req.Limit, NextOffset: req.Offset + min(len(ids), req.Limit)}
	for _, id := range ids[:min(len(ids), req.Limit)] {
		record, err := readRecord(ctx, tx, id)
		if err != nil {
			return page, err
		}
		page.Records = append(page.Records, record)
	}
	return page, tx.Commit(ctx)
}

// Replay is historical inspection, never a current-authorized package delivery.
// It verifies retained canonical bytes and does not invoke a harness.
func (s *Store) Replay(ctx context.Context, receiptID string) (Package, error) {
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return Package{}, err
	}
	defer tx.Rollback(ctx)
	if err = s.receiptAccess(ctx, tx, receiptID); err != nil {
		return Package{}, err
	}
	if err = receiptPayloadAvailable(ctx, tx, receiptID); err != nil {
		return Package{}, err
	}
	p, err := readPackage(ctx, tx, receiptID)
	if err != nil {
		return Package{}, err
	}
	return p, tx.Commit(ctx)
}

func readPackage(ctx context.Context, tx pgx.Tx, receiptID string) (Package, error) {
	var err error
	var p Package
	var encoded []byte
	p.ReceiptID = receiptID
	if err = tx.QueryRow(ctx, `SELECT semantic_body,seal,nonce::text FROM cairn.retrieval_receipt WHERE receipt_id=$1`, receiptID).Scan(&encoded, &p.Seal, &p.Nonce); err != nil {
		return p, err
	}
	if err = cbor.Unmarshal(encoded, &p.Semantic); err != nil {
		return p, err
	}
	_, seal, err := sealPackage(p.Semantic)
	if err != nil {
		return p, err
	}
	if seal != p.Seal {
		return p, failure("INTEGRITY_FAILURE", "stored package does not reproduce its seal")
	}
	return p, nil
}

type Impact struct {
	RecordID  string `json:"record_id"`
	ReceiptID string `json:"receipt_id"`
	Version   int    `json:"version"`
	Purpose   string `json:"purpose"`
	Scope     Scope  `json:"scope"`
}
type ImpactPage struct {
	Uses       []Impact `json:"uses"`
	Truncated  bool     `json:"truncated"`
	NextOffset int      `json:"next_offset"`
}

func (s *Store) Impact(ctx context.Context, recordID string, offset int) (ImpactPage, error) {
	if err := validID(recordID); err != nil {
		return ImpactPage{}, err
	}
	if offset < 0 {
		return ImpactPage{}, failure("INVALID_REQUEST", "negative offset")
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return ImpactPage{}, err
	}
	defer tx.Rollback(ctx)
	record, err := readRecord(ctx, tx, recordID)
	if err != nil {
		return ImpactPage{}, err
	}
	if err = s.checkRepo(record.Scope.Repo); err != nil {
		return ImpactPage{}, err
	}
	rows, err := tx.Query(ctx, `SELECT u.record_id::text,u.receipt_id::text,u.version,u.purpose,r.scope FROM cairn.record_use u JOIN cairn.retrieval_receipt r USING(receipt_id) WHERE u.record_id=$1 ORDER BY u.used_at,u.receipt_id LIMIT 101 OFFSET $2`, recordID, offset)
	if err != nil {
		return ImpactPage{}, err
	}
	defer rows.Close()
	page := ImpactPage{Uses: []Impact{}}
	for rows.Next() {
		var impact Impact
		if err = rows.Scan(&impact.RecordID, &impact.ReceiptID, &impact.Version, &impact.Purpose, &impact.Scope); err != nil {
			return page, err
		}
		page.Uses = append(page.Uses, impact)
	}
	if err = rows.Err(); err != nil {
		return page, err
	}
	page.Truncated = len(page.Uses) > 100
	if page.Truncated {
		page.Uses = page.Uses[:100]
	}
	page.NextOffset = offset + len(page.Uses)
	return page, tx.Commit(ctx)
}

type Report struct {
	Exposures           int    `json:"exposures"`
	Available           int    `json:"available"`
	Delivered           int    `json:"delivered"`
	Outcomes            int    `json:"outcomes"`
	ExitZero            int    `json:"exit_zero"`
	ExitNonzero         int    `json:"exit_nonzero"`
	UnknownTaskOutcomes int    `json:"unknown_task_outcomes"`
	Citations           int    `json:"citations"`
	Interpretation      string `json:"interpretation"`
}

func (s *Store) Report(ctx context.Context, repo string) (Report, error) {
	if err := s.checkRepo(repo); err != nil {
		return Report{}, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return Report{}, err
	}
	defer tx.Rollback(ctx)
	report := Report{Interpretation: "Exposure and process outcomes are observational. Task outcome counts use the latest assessment when present. Exit zero is not task acceptance; citation is testimony, not proof of benefit."}
	err = tx.QueryRow(ctx, `SELECT
 (SELECT count(*) FROM cairn.record_use u JOIN cairn.retrieval_receipt r USING(receipt_id) WHERE r.scope->>'repo'=$1),
 (SELECT count(*) FROM cairn.delivery_receipt d JOIN cairn.retrieval_receipt r USING(receipt_id) WHERE r.scope->>'repo'=$1 AND d.assurance='available'),
 (SELECT count(*) FROM cairn.delivery_receipt d JOIN cairn.retrieval_receipt r USING(receipt_id) WHERE r.scope->>'repo'=$1 AND d.assurance='delivered'),
 (SELECT count(*) FROM cairn.run_outcome o JOIN cairn.retrieval_receipt r USING(receipt_id) WHERE r.scope->>'repo'=$1),
 (SELECT count(*) FROM cairn.run_outcome o JOIN cairn.retrieval_receipt r USING(receipt_id) WHERE r.scope->>'repo'=$1 AND o.exit_code=0),
 (SELECT count(*) FROM cairn.run_outcome o JOIN cairn.retrieval_receipt r USING(receipt_id) WHERE r.scope->>'repo'=$1 AND o.exit_code<>0),
 (SELECT count(*) FROM cairn.run_outcome o JOIN cairn.retrieval_receipt r USING(receipt_id)
 LEFT JOIN LATERAL (SELECT task_outcome FROM cairn.run_assessment WHERE receipt_id=o.receipt_id ORDER BY version DESC LIMIT 1) a ON true
 WHERE r.scope->>'repo'=$1 AND COALESCE(a.task_outcome,o.task_outcome)='unknown'),
 (SELECT count(*) FROM cairn.usage_observation u JOIN cairn.retrieval_receipt r USING(receipt_id) WHERE r.scope->>'repo'=$1 AND u.signal='cited')`, repo).Scan(&report.Exposures, &report.Available, &report.Delivered, &report.Outcomes, &report.ExitZero, &report.ExitNonzero, &report.UnknownTaskOutcomes, &report.Citations)
	if err != nil {
		return report, err
	}
	return report, tx.Commit(ctx)
}
