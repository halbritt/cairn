package core

import (
	"context"

	"github.com/jackc/pgx/v5"
)

const (
	restoreRebuildDefaultSources = 1000
	restoreRebuildMaxSources     = 10000
)

// RebuildRestoreRequest examines one page of forgotten sources. The first call
// omits After; each later call passes the previous NextAfter until Complete.
type RebuildRestoreRequest struct {
	RequestID string `json:"request_id"`
	SessionID string `json:"session_id"`
	After     string `json:"after,omitempty"`
	Limit     int    `json:"limit,omitempty"`
}

// RestoreRebuild counts one page. Sources is the number examined, AddedExclusions
// the new dependency exclusions and AffectedRecords the records whose use
// generation advanced in this call. Records reachable from several pages can
// advance once per page, which is harmless: generations only need to increase.
type RestoreRebuild struct {
	SessionID       string `json:"session_id"`
	AddedExclusions int64  `json:"added_exclusions"`
	AffectedRecords int    `json:"affected_records"`
	Sources         int    `json:"sources"`
	Complete        bool   `json:"complete"`
	NextAfter       string `json:"next_after,omitempty"`
}

func (req RebuildRestoreRequest) validate() error {
	if err := validID(req.SessionID); err != nil {
		return err
	}
	if req.After != "" {
		if err := validID(req.After); err != nil {
			return err
		}
	}
	if req.Limit < 0 || req.Limit > restoreRebuildMaxSources {
		return failure("INVALID_REQUEST", "rebuild limit must be between 1 and 10000 forgotten sources")
	}
	return nil
}

// RebuildRestore adds derived forgotten-source exclusions from retained exact
// relations. Historical impact snapshots and audit are authoritative inputs,
// never regenerated as if today's relation graph existed at an earlier time.
//
// The work is divided by keyset pages of forgotten sources, each committed by
// its own request. Every page recomputes the exact version-qualified closure in
// PostgreSQL, so neither the number of sources nor the number of retained
// descendants has a ceiling, and exclusions are inserted idempotently: repeating
// or restarting a page adds nothing already present. The session stays paused
// between pages. A source added by a recovery reapplication after the cursor has
// passed its record is caught by starting a new pass or, at the latest, by the
// dependency check that restore verification performs for every forgotten source.
func (s *Store) RebuildRestore(ctx context.Context, req RebuildRestoreRequest) (RestoreRebuild, error) {
	if err := s.checkpointAccess(); err != nil {
		return RestoreRebuild{}, err
	}
	if err := req.validate(); err != nil {
		return RestoreRebuild{}, err
	}
	limit := req.Limit
	if limit == 0 {
		limit = restoreRebuildDefaultSources
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
		// One extra row says whether another page remains.
		query := `SELECT deletion_id::text,record_id::text FROM cairn.deletion_request ORDER BY record_id LIMIT $1`
		args := []any{limit + 1}
		if req.After != "" {
			query = `SELECT deletion_id::text,record_id::text FROM cairn.deletion_request WHERE record_id>$2::uuid ORDER BY record_id LIMIT $1`
			args = append(args, req.After)
		}
		rows, err := tx.Query(ctx, query, args...)
		if err != nil {
			return result, err
		}
		type source struct {
			Deletion string `json:"deletion_id"`
			Record   string `json:"record_id"`
		}
		sources, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (source, error) {
			var s source
			err := row.Scan(&s.Deletion, &s.Record)
			return s, err
		})
		if err != nil {
			return result, err
		}
		result.Complete = len(sources) <= limit
		if !result.Complete {
			sources = sources[:limit]
		}
		result.Sources = len(sources)
		if len(sources) == 0 {
			return result, nil
		}
		if !result.Complete {
			result.NextAfter = sources[len(sources)-1].Record
		}
		// Exclusions and the use-generation advance of each newly affected record
		// are one statement. UNION removes repeated (source, version) paths, so the
		// closure terminates on cyclic relation data as dependentVersions does.
		var added, affected int64
		err = tx.QueryRow(ctx, `WITH RECURSIVE source(deletion_id,record_id) AS (
 SELECT deletion_id,record_id FROM jsonb_to_recordset($1) s(deletion_id uuid,record_id uuid)
), dependents(deletion_id,source_id,record_id,version) AS (
 SELECT s.deletion_id,s.record_id,v.record_id,v.version FROM source s JOIN cairn.record_version v ON v.record_id=s.record_id
 UNION SELECT d.deletion_id,d.source_id,r.from_id,r.from_version FROM cairn.record_relation r JOIN dependents d ON r.to_id=d.record_id AND r.to_version=d.version
), inserted AS (
 INSERT INTO cairn.deletion_dependency(record_id,version,deletion_id)
 SELECT record_id,version,deletion_id FROM dependents WHERE record_id<>source_id
 ON CONFLICT DO NOTHING RETURNING record_id
), advanced AS (
 UPDATE cairn.memory_record m SET use_generation=m.use_generation+1 WHERE m.record_id IN (SELECT record_id FROM inserted) RETURNING m.record_id
) SELECT (SELECT count(*) FROM inserted),(SELECT count(*) FROM advanced)`, sources).Scan(&added, &affected)
		if err != nil {
			return result, err
		}
		result.AddedExclusions, result.AffectedRecords = added, int(affected)
		return result, nil
	}, func(tx pgx.Tx) error { return s.restoreOwner(ctx, tx, req.SessionID) })
}
