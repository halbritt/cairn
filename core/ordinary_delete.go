package core

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

type DeleteRequest struct {
	RequestID       string `json:"request_id"`
	RecordID        string `json:"record_id"`
	ExpectedVersion int    `json:"expected_version"`
}

type DeletedRecord struct {
	RecordID       string `json:"record_id"`
	DeletedVersion int    `json:"deleted_version"`
}

func (s *Store) Delete(ctx context.Context, req DeleteRequest) (DeletedRecord, error) {
	if err := validID(req.RecordID); err != nil {
		return DeletedRecord{}, err
	}
	if req.ExpectedVersion < 1 {
		return DeletedRecord{}, failure("INVALID_REQUEST", "expected_version must be positive")
	}
	var scope Scope
	result, err := privileged(ctx, s, "delete", req.RequestID, req, func(tx pgx.Tx) (DeletedRecord, error) {
		current, err := lockRecord(ctx, tx, req.RecordID, req.ExpectedVersion)
		if err != nil {
			return DeletedRecord{}, err
		}
		if err = s.checkRepo(current.Scope.Repo); err != nil {
			return DeletedRecord{}, err
		}
		scope = current.Scope
		var conflict, protected bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.conflict_member m JOIN cairn.conflict_group g USING(conflict_id) WHERE m.record_id=$1 AND g.resolved_event IS NULL)`, req.RecordID).Scan(&conflict); err != nil {
			return DeletedRecord{}, err
		}
		if conflict {
			return DeletedRecord{}, failure("OPEN_CONFLICT", "resolve the open conflict before deleting its record")
		}
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.record_version WHERE record_id=$1 AND version_class<>'A') OR EXISTS(SELECT 1 FROM cairn.memory_record WHERE record_id=$1 AND recovered_attempt_id IS NOT NULL)`, req.RecordID).Scan(&protected); err != nil {
			return DeletedRecord{}, err
		}
		if current.Class != "A" || current.Lifecycle != "active" || protected {
			return DeletedRecord{}, failure("FORGET_REQUIRED", "ordinary deletion requires an active A record with no privileged or service-recovery history; use the audited forgetting workflow")
		}
		// These are this note's own metadata. Incoming references, retained read
		// sets, uses, conflicts and audit rows remain protected by foreign keys.
		for _, query := range []string{
			`DELETE FROM cairn.record_applicability WHERE record_id=$1`,
			`DELETE FROM cairn.evidence_ref WHERE record_id=$1`,
			`DELETE FROM cairn.record_relation WHERE from_id=$1`,
			`DELETE FROM cairn.record_version WHERE record_id=$1`,
			`DELETE FROM cairn.memory_record WHERE record_id=$1`,
		} {
			if _, err = tx.Exec(ctx, query, req.RecordID); err != nil {
				return DeletedRecord{}, err
			}
		}
		// Keep request identity/digest so ambiguous create/edit retries cannot
		// resurrect the note. Their cached bodies are removed in this transaction.
		if _, err = tx.Exec(ctx, `UPDATE cairn.mutation_request SET response=NULL,ordinary_deleted=true WHERE operation IN ('create','edit') AND response->>'record_id'=$1`, req.RecordID); err != nil {
			return DeletedRecord{}, err
		}
		return DeletedRecord{req.RecordID, req.ExpectedVersion}, nil
	})
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) && pgErr.Code == "23503" {
		return DeletedRecord{}, failure("FORGET_REQUIRED", "retained references prevent ordinary deletion; use preview-delete and the audited forgetting workflow")
	}
	if durablePolicyRefusal(err) {
		err = s.retainRefusal(ctx, req, Refusal{RequestID: req.RequestID, Operation: "delete", Scope: scope, Considered: []RecordVersionRef{{req.RecordID, req.ExpectedVersion}}, TraceComplete: false}, err)
	}
	return result, err
}
