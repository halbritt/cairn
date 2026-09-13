package core

import (
	"context"
	"sort"

	"github.com/jackc/pgx/v5"
)

type RebuildRestoreRequest struct {
	RequestID string `json:"request_id"`
	SessionID string `json:"session_id"`
}

type RestoreRebuild struct {
	SessionID       string `json:"session_id"`
	AddedExclusions int64  `json:"added_exclusions"`
	AffectedRecords int    `json:"affected_records"`
}

// RebuildRestore adds derived forgotten-source exclusions from retained exact
// relations. Historical impact snapshots and audit are authoritative inputs,
// never regenerated as if today's relation graph existed at an earlier time.
func (s *Store) RebuildRestore(ctx context.Context, req RebuildRestoreRequest) (RestoreRebuild, error) {
	if err := s.checkpointAccess(); err != nil {
		return RestoreRebuild{}, err
	}
	if err := validID(req.SessionID); err != nil {
		return RestoreRebuild{}, err
	}
	ctx = context.WithValue(s.recoveryContext(ctx), restoreTransitionKey{}, true)
	return privileged(ctx, s, "rebuild-restore", req.RequestID, req, func(tx pgx.Tx) (RestoreRebuild, error) {
		result := RestoreRebuild{SessionID: req.SessionID}
		var paused bool
		if err := tx.QueryRow(ctx, `SELECT paused FROM cairn.restore_admission WHERE singleton`).Scan(&paused); err != nil {
			return result, err
		}
		if !paused {
			return result, failure("RESTORE_NOT_PAUSED", "projection rebuild requires a paused restore session")
		}
		rows, err := tx.Query(ctx, `SELECT deletion_id::text,record_id::text FROM cairn.deletion_request ORDER BY record_id LIMIT 10001`)
		if err != nil {
			return result, err
		}
		type source struct{ deletion, record string }
		sources, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (source, error) {
			var s source
			err := row.Scan(&s.deletion, &s.record)
			return s, err
		})
		if err != nil {
			return result, err
		}
		if len(sources) > 10000 {
			return result, failure("BUDGET_REFUSED", "restore rebuild exceeds 10000 deletion requests")
		}
		affected := map[string]bool{}
		total := 0
		for _, source := range sources {
			refs, err := dependentVersions(ctx, tx, source.record)
			if err != nil {
				return result, err
			}
			total += len(refs)
			if total > 10000 {
				return result, failure("BUDGET_REFUSED", "restore rebuild exceeds 10000 source/version references")
			}
			rows, err := tx.Query(ctx, `INSERT INTO cairn.deletion_dependency(record_id,version,deletion_id) SELECT record_id,version,$1 FROM jsonb_to_recordset($2) d(record_id uuid,version integer) WHERE record_id<>$3::uuid ON CONFLICT DO NOTHING RETURNING record_id::text`, source.deletion, refs, source.record)
			if err != nil {
				return result, err
			}
			ids, err := pgx.CollectRows(rows, pgx.RowTo[string])
			if err != nil {
				return result, err
			}
			result.AddedExclusions += int64(len(ids))
			for _, id := range ids {
				affected[id] = true
			}
		}
		ids := make([]string, 0, len(affected))
		for id := range affected {
			ids = append(ids, id)
		}
		sort.Strings(ids)
		for _, id := range ids {
			if _, err = tx.Exec(ctx, `UPDATE cairn.memory_record SET use_generation=use_generation+1 WHERE record_id=$1`, id); err != nil {
				return result, err
			}
		}
		result.AffectedRecords = len(ids)
		return result, nil
	}, func(tx pgx.Tx) error { return s.restoreOwner(ctx, tx, req.SessionID) })
}
