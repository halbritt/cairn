package core

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
)

// A stage lives only inside the caller's snapshot/transaction. PostgreSQL holds
// the complete union; Go holds one source record or one fixed-size page. A failed
// pass is restarted from immutable sources, never resumed with a stale cursor.
const recoveryPageSize = 256

type recoveryStage struct {
	tx       pgx.Tx
	root     string
	captured time.Time
}

func newRecoveryStage(ctx context.Context, tx pgx.Tx) (*recoveryStage, error) {
	stage := &recoveryStage{tx: tx}
	err := tx.QueryRow(ctx, `SELECT grant_id::text,transaction_timestamp() FROM cairn.authority_grant WHERE parent_id IS NULL`).Scan(&stage.root, &stage.captured)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, failure("RECOVERY_UNINITIALIZED", "install the operator root before capturing a recovery record")
	}
	if err != nil {
		return nil, err
	}
	stage.captured = stage.captured.UTC()
	_, err = tx.Exec(ctx, `CREATE TEMP TABLE IF NOT EXISTS cairn_recovery_expected(kind text NOT NULL,key text COLLATE "C" NOT NULL,body jsonb NOT NULL,PRIMARY KEY(kind,key)) ON COMMIT DROP;
 TRUNCATE pg_temp.cairn_recovery_expected;
 CREATE TEMP TABLE IF NOT EXISTS cairn_recovery_actions(event_id text,digest text,subject_id text,repo text,kind text,outcome text,deletion_id text,current_event_id text) ON COMMIT DROP;
 TRUNCATE pg_temp.cairn_recovery_actions;CREATE INDEX IF NOT EXISTS cairn_recovery_actions_identity ON cairn_recovery_actions(event_id,digest)`)
	if err != nil {
		return nil, err
	}
	if err = stageActualAudit(ctx, tx); err != nil {
		return nil, err
	}
	// Actions remain data. Matching includes root plus the full original identity.
	_, err = tx.Exec(ctx, `INSERT INTO pg_temp.cairn_recovery_actions SELECT a->>'event_id',a->>'source_digest',a->>'subject_id',a->>'repo',a->>'kind',a->>'outcome',a->>'deletion_id',a->>'current_event_id' FROM cairn.recovery_application r CROSS JOIN LATERAL jsonb_array_elements(r.actions) a WHERE source_root=$1::uuid`, stage.root)
	if err != nil {
		return nil, err
	}
	return stage, nil
}

func stageKnownRecovery(ctx context.Context, tx pgx.Tx) (*recoveryStage, error) {
	stage, err := newRecoveryStage(ctx, tx)
	if err != nil {
		return nil, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO pg_temp.cairn_recovery_expected SELECT 'audit',event_id,jsonb_build_object('event_id',event_id,'sha256',digest) FROM pg_temp.cairn_actual_audit;
 INSERT INTO pg_temp.cairn_recovery_expected
 SELECT 'withdrawal',e.event_id::text,jsonb_build_object('event_id',e.event_id,'kind',e.event_type,'subject_id',e.subject_id,'repo',COALESCE(g.repo,v.repo)) FROM cairn.authority_event e
 LEFT JOIN cairn.authority_grant g ON e.event_type='revoke_grant' AND g.grant_id=e.subject_id
 LEFT JOIN cairn.record_version v ON e.event_type IN ('forget','retract') AND v.record_id=e.subject_id AND v.version=e.resulting_version
 WHERE e.event_type IN ('revoke_grant','forget') OR (e.event_type='retract' AND v.version_class='C');
 INSERT INTO pg_temp.cairn_recovery_expected
 SELECT DISTINCT 'context',d.record_id::text||':'||c.receipt_id::text,jsonb_build_object('record_id',d.record_id,'ownership_id',c.ownership_id,'receipt_id',c.receipt_id,'directory',c.directory,'directory_device',c.directory_device,'directory_inode',c.directory_inode,'body_sha256',c.body_sha256)
 FROM cairn.deletion_request d JOIN cairn.deletion_effect e USING(deletion_id) JOIN cairn.managed_context c ON e.target_type='managed_context' AND e.target_id=c.receipt_id::text`)
	if err != nil {
		return nil, err
	}
	var hasWorkExcl bool
	if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='cairn' AND table_name='work_metadata_exclusion')`).Scan(&hasWorkExcl); err != nil {
		return nil, err
	}
	if hasWorkExcl {
		if _, err = tx.Exec(ctx, `INSERT INTO pg_temp.cairn_recovery_expected
			SELECT 'work_exclusion', binding_id::text || ':' || field_name || ':' || pattern,
			       jsonb_build_object('binding_id', binding_id, 'field_name', field_name, 'pattern', pattern, 'revision', revision)
			FROM cairn.work_metadata_exclusion`); err != nil {
			return nil, err
		}
	}
	var hasWorkPurge bool
	if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='cairn' AND table_name='work_metadata_purge')`).Scan(&hasWorkPurge); err != nil {
		return nil, err
	}
	if hasWorkPurge {
		if _, err = tx.Exec(ctx, `INSERT INTO pg_temp.cairn_recovery_expected
			SELECT 'work_purge', binding_id::text || ':' || field_name || ':' || pattern,
			       jsonb_build_object('binding_id', binding_id, 'field_name', field_name, 'pattern', pattern, 'revision', revision)
			FROM cairn.work_metadata_purge`); err != nil {
			return nil, err
		}
	}
	var hasArtifactFence bool
	if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='cairn' AND table_name='work_artifact_purge_fence')`).Scan(&hasArtifactFence); err != nil {
		return nil, err
	}
	if hasArtifactFence {
		if _, err = tx.Exec(ctx, `INSERT INTO pg_temp.cairn_recovery_expected
			SELECT 'work_artifact_purge_fence', collection || ':' || artifact_id::text,
			       jsonb_build_object('collection', collection, 'artifact_id', artifact_id, 'canonical_key', canonical_key, 'purged_by', purged_by, 'purged_at', purged_at)
			FROM cairn.work_artifact_purge_fence`); err != nil {
			return nil, err
		}
	}
	after := "00000000-0000-0000-0000-000000000000"
	for {
		var id string
		var body []byte
		// A damaged retained source cannot force an unbounded application allocation.
		err = tx.QueryRow(ctx, `SELECT application_id::text,CASE WHEN octet_length(source_record::text)<=$2 THEN source_record ELSE NULL END FROM cairn.recovery_application WHERE application_id>$1::uuid ORDER BY application_id LIMIT 1`, after, MaxRecoveryBytes).Scan(&id, &body)
		if errors.Is(err, pgx.ErrNoRows) {
			break
		}
		if err != nil {
			return nil, err
		}
		if body == nil {
			return nil, failure("BUDGET_REFUSED", "retained recovery source exceeds one bounded record")
		}
		var source RecoveryRecord
		if err = json.Unmarshal(body, &source); err != nil {
			return nil, err
		}
		if err = stage.add(ctx, source); err != nil {
			return nil, err
		}
		after = id
	}
	return stage, nil
}

func (s *recoveryStage) add(ctx context.Context, record RecoveryRecord) error {
	if err := record.validate(); err != nil {
		return err
	}
	if record.RootGrantID != s.root {
		return failure("INTEGRITY_FAILURE", "recovery expectations have different roots")
	}
	inputs := []struct {
		kind, key string
		value     any
		count     int
	}{
		{"audit", "x->>'event_id'", record.Audit, len(record.Audit)},
		{"withdrawal", "x->>'event_id'", record.Withdrawals, len(record.Withdrawals)},
		{"context", "(x->>'record_id')||':'||(x->>'receipt_id')", record.Contexts, len(record.Contexts)},
		{"work_exclusion", "(x->>'binding_id')||':'||(x->>'field_name')||':'||(x->>'pattern')", record.WorkExclusions, len(record.WorkExclusions)},
		{"work_purge", "(x->>'binding_id')||':'||(x->>'field_name')||':'||(x->>'pattern')", record.WorkPurges, len(record.WorkPurges)},
		{"work_artifact_purge_fence", "(x->>'collection')||':'||(x->>'artifact_id')", record.WorkArtifactPurgeFences, len(record.WorkArtifactPurgeFences)},
	}
	if record.Segment != nil {
		body := struct {
			SetID  string `json:"set_id"`
			Index  int    `json:"index"`
			Count  int    `json:"count"`
			SHA256 string `json:"sha256"`
		}{record.Segment.SetID, record.Segment.Index, record.Segment.Count, record.SHA256}
		inputs = append(inputs, struct {
			kind, key string
			value     any
			count     int
		}{"segment", "(x->>'set_id')||':'||(x->>'index')", []any{body}, 1})
	}
	for _, input := range inputs {
		if input.count == 0 {
			continue
		}
		// Rows with equal content are idempotent. A conflict returns fewer affected
		// rows and refuses the caller transaction; no last-writer-wins union exists.
		tag, err := s.tx.Exec(ctx, `INSERT INTO pg_temp.cairn_recovery_expected AS retained(kind,key,body) SELECT $1,`+input.key+`,x FROM jsonb_array_elements($2::jsonb) x ON CONFLICT(kind,key) DO UPDATE SET body=retained.body WHERE retained.body=EXCLUDED.body`, input.kind, input.value)
		if err != nil {
			return err
		}
		if tag.RowsAffected() != int64(input.count) {
			return failure("INTEGRITY_FAILURE", "conflicting recovery "+input.kind+" expectations")
		}
	}
	var conflict bool
	if err := s.tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_temp.cairn_recovery_expected WHERE kind='segment' GROUP BY body->>'set_id' HAVING min((body->>'count')::int)<>max((body->>'count')::int))`).Scan(&conflict); err != nil {
		return err
	}
	if conflict {
		return failure("INTEGRITY_FAILURE", "recovery segments disagree about their set size")
	}
	return nil
}

func (s *recoveryStage) incomplete(ctx context.Context, visit func(string) error) error {
	rows, err := s.tx.Query(ctx, `SELECT body->>'set_id',count(*),max((body->>'count')::int) FROM pg_temp.cairn_recovery_expected WHERE kind='segment' GROUP BY body->>'set_id' HAVING count(*)<>max((body->>'count')::int) ORDER BY body->>'set_id'`)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var id string
		var have, total int
		if err = rows.Scan(&id, &have, &total); err != nil {
			return err
		}
		if err = visit(fmt.Sprintf("%s:%d/%d", id, have, total)); err != nil {
			return err
		}
	}
	return rows.Err()
}

// page closes rows before returning so consumers can perform checks in the same
// transaction. Every returned body is part of one validated bounded source.
func (s *recoveryStage) page(ctx context.Context, kind, after string, limit int) ([]json.RawMessage, []string, error) {
	rows, err := s.tx.Query(ctx, `WITH page AS (SELECT key,body FROM pg_temp.cairn_recovery_expected WHERE kind=$1 AND key>$2 ORDER BY key LIMIT $3), sized AS (SELECT key,body,sum(octet_length(body::text)) OVER(ORDER BY key) bytes,row_number() OVER(ORDER BY key) n FROM page) SELECT key,CASE WHEN octet_length(body::text)<=$4 THEN body ELSE NULL END FROM sized WHERE bytes<=$4 OR n=1 ORDER BY key`, kind, after, limit, MaxRecoveryBytes)
	if err != nil {
		return nil, nil, err
	}
	var bodies []json.RawMessage
	var keys []string
	for rows.Next() {
		var key string
		var body json.RawMessage
		if err = rows.Scan(&key, &body); err != nil {
			rows.Close()
			return nil, nil, err
		}
		if body == nil || len(body) > MaxRecoveryBytes {
			rows.Close()
			return nil, nil, failure("BUDGET_REFUSED", "one recovery expectation exceeds the record byte bound")
		}
		keys = append(keys, key)
		bodies = append(bodies, body)
	}
	err = rows.Err()
	rows.Close()
	return bodies, keys, err
}

func (s *recoveryStage) covers(ctx context.Context, event string) (bool, error) {
	var covered bool
	err := s.tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_temp.cairn_recovery_expected w JOIN pg_temp.cairn_recovery_expected d ON d.kind='audit' AND d.key=w.key JOIN pg_temp.cairn_recovery_actions a ON a.event_id=w.key AND a.digest=d.body->>'sha256' AND a.subject_id=w.body->>'subject_id' AND a.repo=w.body->>'repo' AND a.kind=w.body->>'kind' WHERE w.kind='withdrawal' AND w.key=$1 AND a.outcome IN ('reapplied','already_restricted','absent'))`, event).Scan(&covered)
	return covered, err
}

func (s *recoveryStage) mappedDeletion(ctx context.Context, w RecoveryWithdrawal) (string, error) {
	var event string
	err := s.tx.QueryRow(ctx, `SELECT d.event_id::text FROM pg_temp.cairn_recovery_actions a JOIN cairn.deletion_request d ON d.deletion_id::text=a.deletion_id AND d.record_id::text=a.subject_id AND d.event_id::text=a.current_event_id JOIN pg_temp.cairn_recovery_expected m ON m.kind='audit' AND m.key=a.event_id AND m.body->>'sha256'=a.digest WHERE a.event_id=$1 AND a.subject_id=$2 AND a.repo=$3 AND a.kind=$4 ORDER BY d.event_id LIMIT 1`, w.EventID, w.SubjectID, w.Repo, w.Kind).Scan(&event)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", nil
	}
	return event, err
}

func (s *recoveryStage) inspect(ctx context.Context, visit func(RecoveryGap) error) error {
	after := ""
	for {
		rows, err := s.tx.Query(ctx, `SELECT e.key,CASE WHEN a.event_id IS NULL THEN 'AUDIT_MISSING' ELSE 'AUDIT_CHANGED' END FROM pg_temp.cairn_recovery_expected e LEFT JOIN pg_temp.cairn_actual_audit a ON e.key=a.event_id WHERE e.kind='audit' AND e.key>$1 AND (a.event_id IS NULL OR a.digest<>e.body->>'sha256') ORDER BY e.key LIMIT $2`, after, recoveryPageSize)
		if err != nil {
			return err
		}
		gaps := make([]RecoveryGap, 0, recoveryPageSize)
		for rows.Next() {
			g := RecoveryGap{Kind: "audit"}
			if err = rows.Scan(&g.EventID, &g.Reason); err != nil {
				rows.Close()
				return err
			}
			gaps = append(gaps, g)
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return err
		}
		if len(gaps) == 0 {
			break
		}
		for _, g := range gaps {
			if err = visit(g); err != nil {
				return err
			}
		}
		after = gaps[len(gaps)-1].EventID
	}
	for _, kind := range []string{"withdrawal", "context", "work_exclusion", "work_purge", "work_artifact_purge_fence"} {
		after = ""
		for {
			bodies, keys, err := s.page(ctx, kind, after, recoveryPageSize)
			if err != nil {
				return err
			}
			if len(keys) == 0 {
				break
			}
			for _, body := range bodies {
				var gap RecoveryGap
				if kind == "withdrawal" {
					var w RecoveryWithdrawal
					if err = json.Unmarshal(body, &w); err != nil {
						return err
					}
					local := w
					if w.Kind == "forget" {
						event, err := s.mappedDeletion(ctx, w)
						if err != nil {
							return err
						}
						if event != "" {
							local.EventID = event
						}
					}
					reason, err := inspectWithdrawal(ctx, s.tx, local)
					if err != nil {
						return err
					}
					gap = RecoveryGap{Kind: w.Kind, SubjectID: w.SubjectID, EventID: w.EventID, Reason: reason}
				} else if kind == "context" {
					var c RecoveryContext
					if err = json.Unmarshal(body, &c); err != nil {
						return err
					}
					found, err := inspectRecoveryContext(ctx, s.tx, c)
					if err != nil {
						return err
					}
					if !found {
						gap = RecoveryGap{Kind: "managed_context", SubjectID: c.ReceiptID, Reason: "CONTEXT_CUSTODY_MISSING"}
					}
				} else if kind == "work_exclusion" || kind == "work_purge" {
					var we RecoveryWorkExclusion
					if err = json.Unmarshal(body, &we); err != nil {
						return err
					}
					patternParam := "%" + we.Pattern + "%"
					var unredacted bool
					err := s.tx.QueryRow(ctx, `SELECT EXISTS(
						SELECT 1 FROM cairn.work_item WHERE binding_id = $1 AND title ILIKE $2
						UNION ALL
						SELECT 1 FROM cairn.work_annotation WHERE binding_id = $1 AND body ILIKE $2
						UNION ALL
						SELECT 1 FROM cairn.work_annotation_history h JOIN cairn.work_annotation a ON h.annotation_id = a.annotation_id WHERE a.binding_id = $1 AND h.body ILIKE $2
						UNION ALL
						SELECT 1 FROM cairn.work_association WHERE binding_id = $1 AND rationale ILIKE $2
						UNION ALL
						SELECT 1 FROM cairn.work_association_history h JOIN cairn.work_association a ON h.association_id = a.association_id WHERE a.binding_id = $1 AND h.rationale ILIKE $2
						UNION ALL
						SELECT 1 FROM cairn.mutation_request WHERE operation LIKE 'work-%' AND response IS NOT NULL AND response->>'binding_id' = $1::text
						  AND (response->>'body' ILIKE $2 OR response->>'text' ILIKE $2 OR response->>'rationale' ILIKE $2 OR response->'item'->>'title' ILIKE $2)
					)`, we.BindingID, patternParam).Scan(&unredacted)
					if err != nil {
						return err
					}
					if unredacted {
						gap = RecoveryGap{Kind: kind, SubjectID: we.BindingID.String(), Reason: "METADATA_EXCLUSION_MISSING"}
					}
				} else if kind == "work_artifact_purge_fence" {
					var pf RecoveryWorkArtifactPurgeFence
					if err = json.Unmarshal(body, &pf); err != nil {
						return err
					}
					var missing bool
					err := s.tx.QueryRow(ctx, `SELECT (
						NOT EXISTS(SELECT 1 FROM cairn.work_artifact_purge_fence WHERE collection = $1 AND artifact_id = $2)
						OR EXISTS(SELECT 1 FROM cairn.work_artifact WHERE collection = $1 AND (artifact_id = $2 OR canonical_key = $3))
						OR EXISTS(
							SELECT 1 FROM cairn.mutation_request m
							WHERE m.operation IN ('work-artifact-register','work-artifact-link','work-artifact-unlink')
							  AND m.response IS NOT NULL
							  AND (
								jsonb_path_exists(m.response, '$.**.artifact_id ? (@ == $id)', jsonb_build_object('id', $2::text))
								OR (
								  m.response->>'collection' = $1
								  AND jsonb_path_exists(m.response, '$.**.canonical_key ? (@ == $key)', jsonb_build_object('key', $3))
								)
							  )
						)
					)`, pf.Collection, pf.ArtifactID, pf.CanonicalKey).Scan(&missing)
					if err != nil {
						return err
					}
					if missing {
						gap = RecoveryGap{Kind: kind, SubjectID: pf.ArtifactID.String(), Reason: "ARTIFACT_PURGE_FENCE_MISSING"}
					}
				}
				if gap.Reason != "" {
					if err = visit(gap); err != nil {
						return err
					}
				}
			}
			after = keys[len(keys)-1]
		}
	}
	return nil
}

func inspectRecoveryContext(ctx context.Context, tx pgx.Tx, c RecoveryContext) (bool, error) {
	var found bool
	err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.managed_context c JOIN cairn.record_use u USING(receipt_id) JOIN cairn.deletion_request d USING(record_id) JOIN cairn.deletion_effect e ON e.deletion_id=d.deletion_id AND e.target_type='managed_context' AND e.target_id=c.receipt_id::text WHERE c.receipt_id=$1 AND u.record_id=$7 AND directory=$2 AND directory_device=$3 AND directory_inode=$4 AND ownership_id=$5 AND body_sha256=$6)`, c.ReceiptID, c.Directory, c.DirectoryDevice, c.DirectoryInode, c.OwnershipID, c.BodySHA256, c.RecordID).Scan(&found)
	if err != nil || found {
		return found, err
	}
	err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.recovery_context c JOIN cairn.deletion_request d USING(deletion_id) JOIN cairn.deletion_effect e ON e.deletion_id=c.deletion_id AND e.target_type='managed_context' AND e.target_id=c.receipt_id::text WHERE c.receipt_id=$1 AND d.record_id=$7 AND c.directory=$2 AND c.directory_device=$3 AND c.directory_inode=$4 AND c.ownership_id=$5 AND c.body_sha256=$6)`, c.ReceiptID, c.Directory, c.DirectoryDevice, c.DirectoryInode, c.OwnershipID, c.BodySHA256, c.RecordID).Scan(&found)
	return found, err
}
