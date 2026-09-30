package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

const checkpointSchema = "cairn.audit-checkpoint/1"

// maxCheckpointMembers bounds the member list returned to a caller. A larger
// checkpoint still retains every member in its manifest and is identified by the
// same digest; its response carries the count and digest only.
const maxCheckpointMembers = 10000

type CheckpointRequest struct {
	RequestID string `json:"request_id"`
	ExportID  string `json:"export_id"`
}
type AuditMember struct {
	EventID string `json:"event_id"`
	Digest  string `json:"sha256"`
}
type AuditCheckpoint struct {
	Schema         string        `json:"schema"`
	ID             string        `json:"checkpoint_id"`
	ExportID       string        `json:"export_id"`
	CreatedAt      time.Time     `json:"created_at"`
	Members        []AuditMember `json:"members"`
	Count          int           `json:"count"`
	Digest         string        `json:"sha256"`
	MembersOmitted bool          `json:"members_omitted,omitempty"`
	StorageSchema  string        `json:"storage_schema,omitempty"`
}
type VerifyCheckpointRequest struct {
	CheckpointID     string `json:"checkpoint_id"`
	ExpectedDigest   string `json:"expected_sha256"`
	ExpectedExportID string `json:"expected_export_id"`
}

// Missing and Altered name at most the first 100 members each; the totals count
// every divergence, so a large damaged set cannot produce unbounded output.
type CheckpointVerification struct {
	CheckpointID   string   `json:"checkpoint_id"`
	Valid          bool     `json:"valid"`
	Missing        []string `json:"missing"`
	Altered        []string `json:"altered"`
	MissingTotal   int      `json:"missing_total"`
	AlteredTotal   int      `json:"altered_total"`
	UncoveredCount int      `json:"uncovered_count"`
	Limit          string   `json:"limit"`
}

func checkpointDigest(members []AuditMember) (string, error) {
	body, err := json.Marshal(struct {
		Schema  string        `json:"schema"`
		Members []AuditMember `json:"members"`
	}{checkpointSchema, members})
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(body)
	return hex.EncodeToString(sum[:]), nil
}

// auditMetadataSQL preserves the PostgreSQL metadata text used by checkpoint/1.
const auditMetadataSQL = `SELECT e.event_id::text AS event_id,
 (jsonb_build_object('event_id',e.event_id,'event_type',e.event_type,'subject_id',e.subject_id,
 'previous_version',e.previous_version,'resulting_version',e.resulting_version,'actor',e.actor,
 'basis',e.basis,'transaction_id',e.transaction_id::text,
 'occurred_at_us',(extract(epoch from e.occurred_at)*1000000)::bigint)
 || CASE WHEN e.event_type='reapply_recovery' THEN jsonb_build_object('reapplication',
 (SELECT jsonb_build_object('source_root',a.source_root,'source_sha256',a.source_sha256,'actions',a.actions)
 FROM cairn.recovery_application a WHERE a.event_id=e.event_id))
 WHEN e.event_type='resume_restore' THEN jsonb_build_object('restore_resume',
 (SELECT jsonb_build_object('session_id',r.session_id,'policy',r.policy,'verification',r.verification)
 FROM cairn.restore_resume r WHERE r.event_id=e.event_id)) ELSE '{}'::jsonb END)::text AS metadata
 FROM cairn.authority_event e
 WHERE e.event_type IN ('bootstrap','grant','revoke_grant','issue','authorize_scope','policy_revise','reapply_recovery','resume_restore','resolve','redact','forget')
 OR EXISTS(SELECT 1 FROM cairn.record_version v WHERE v.record_id=e.subject_id AND v.version=e.resulting_version AND v.version_class='C')
`

// Only emitted governance and C/D transition events participate. B promotion,
// correction and retraction and all ordinary A history are excluded. Reasons and
// content are excluded; this detects audit metadata damage, not payload damage.
// The member set has no count ceiling: memory grows with the number of these
// events (a few hundred bytes each), never with records, evidence or receipts.
func auditMembers(ctx context.Context, tx pgx.Tx) ([]AuditMember, error) {
	rows, err := tx.Query(ctx, auditMetadataSQL+" ORDER BY e.event_id")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	members := []AuditMember{}
	for rows.Next() {
		var id, metadata string
		if err = rows.Scan(&id, &metadata); err != nil {
			return nil, err
		}
		sum := sha256.Sum256([]byte(metadata))
		members = append(members, AuditMember{id, hex.EncodeToString(sum[:])})
	}
	return members, rows.Err()
}
func (s *Store) checkpointAccess() error {
	if !s.channel.Operator || s.channel.Repo != "" {
		return failure("AUTHORITY_DENIED", "whole-store checkpoint requires an unscoped operator channel")
	}
	return nil
}

// stageActualAudit hashes metadata inside PostgreSQL using the exact UTF-8 text
// previously hashed by Go. The temporary relation can spill to database storage;
// no application slice grows with the total audit membership.
func stageActualAudit(ctx context.Context, tx pgx.Tx) error {
	_, err := tx.Exec(ctx, `CREATE TEMP TABLE IF NOT EXISTS cairn_actual_audit(event_id text PRIMARY KEY,digest text NOT NULL) ON COMMIT DROP;
 TRUNCATE pg_temp.cairn_actual_audit`)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `INSERT INTO pg_temp.cairn_actual_audit SELECT event_id,encode(sha256(convert_to(metadata,'UTF8')),'hex') FROM (`+auditMetadataSQL+`) a`)
	return err
}

// streamCheckpointDigest reconstructs checkpoint/1 JSON byte-for-byte. Query
// order is significant: legacy manifests retain their original array order.
func streamCheckpointDigest(ctx context.Context, tx pgx.Tx, query string, args ...any) (string, int, error) {
	rows, err := tx.Query(ctx, query, args...)
	if err != nil {
		return "", 0, err
	}
	defer rows.Close()
	h := sha256.New()
	h.Write([]byte(`{"schema":"cairn.audit-checkpoint/1","members":[`))
	count := 0
	for rows.Next() {
		var m AuditMember
		if err = rows.Scan(&m.EventID, &m.Digest); err != nil {
			return "", 0, err
		}
		body, err := json.Marshal(m)
		if err != nil {
			return "", 0, err
		}
		if count > 0 {
			h.Write([]byte(","))
		}
		h.Write(body)
		count++
	}
	if err = rows.Err(); err != nil {
		return "", 0, err
	}
	h.Write([]byte("]}"))
	return hex.EncodeToString(h.Sum(nil)), count, nil
}

const checkpointInlineStorage = "cairn.audit-checkpoint-inline/1"
const checkpointMemberStorage = "cairn.audit-checkpoint-members/1"

func (s *Store) Checkpoint(ctx context.Context, req CheckpointRequest) (AuditCheckpoint, error) {
	ctx = s.recoveryContext(ctx)
	if err := s.checkpointAccess(); err != nil {
		return AuditCheckpoint{}, err
	}
	if strings.TrimSpace(req.ExportID) == "" || len(req.ExportID) > 256 {
		return AuditCheckpoint{}, failure("INVALID_REQUEST", "bounded export identity required")
	}
	return privileged(ctx, s, "checkpoint", req.RequestID, req, func(tx pgx.Tx) (AuditCheckpoint, error) {
		cp := AuditCheckpoint{Schema: checkpointSchema, ID: uuid.NewString(), ExportID: req.ExportID, Members: []AuditMember{}, StorageSchema: checkpointMemberStorage, MembersOmitted: true}
		if err := stageActualAudit(ctx, tx); err != nil {
			return cp, err
		}
		var err error
		cp.Digest, cp.Count, err = streamCheckpointDigest(ctx, tx, `SELECT event_id,digest FROM pg_temp.cairn_actual_audit ORDER BY event_id`)
		if err != nil {
			return cp, err
		}
		if err = tx.QueryRow(ctx, `SELECT transaction_timestamp()`).Scan(&cp.CreatedAt); err != nil {
			return cp, err
		}
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.audit_checkpoint(checkpoint_id,export_id,manifest,storage_schema) VALUES($1,$2,$3,$4)`, cp.ID, cp.ExportID, cp, checkpointMemberStorage); err != nil {
			return cp, err
		}
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.audit_checkpoint_member SELECT $1::uuid,event_id::uuid,digest FROM pg_temp.cairn_actual_audit`, cp.ID); err != nil {
			return cp, err
		}
		if cp.Count <= maxCheckpointMembers {
			rows, err := tx.Query(ctx, `SELECT event_id,digest FROM pg_temp.cairn_actual_audit ORDER BY event_id`)
			if err != nil {
				return cp, err
			}
			for rows.Next() {
				var m AuditMember
				if err = rows.Scan(&m.EventID, &m.Digest); err != nil {
					rows.Close()
					return cp, err
				}
				cp.Members = append(cp.Members, m)
			}
			err = rows.Err()
			rows.Close()
			if err != nil {
				return cp, err
			}
			cp.MembersOmitted = false
		}
		return cp, nil
	})
}

func (s *Store) VerifyCheckpoint(ctx context.Context, req VerifyCheckpointRequest) (CheckpointVerification, error) {
	ctx = s.recoveryContext(ctx)
	if err := s.checkpointAccess(); err != nil {
		return CheckpointVerification{}, err
	}
	if err := validID(req.CheckpointID); err != nil {
		return CheckpointVerification{}, err
	}
	if !digestValid(req.ExpectedDigest) || req.ExpectedExportID == "" {
		return CheckpointVerification{}, failure("INVALID_REQUEST", "expected checkpoint digest and export identity from the backup catalog are required")
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return CheckpointVerification{}, err
	}
	defer tx.Rollback(ctx)
	result, err := verifyCheckpoint(ctx, tx, req)
	if err != nil {
		return result, err
	}
	return result, tx.Commit(ctx)
}
func verifyCheckpoint(ctx context.Context, tx pgx.Tx, req VerifyCheckpointRequest) (CheckpointVerification, error) {
	result := CheckpointVerification{CheckpointID: req.CheckpointID, Missing: []string{}, Altered: []string{}, Limit: "Verifies the expected emitted C/D audit metadata set only. Payloads, state projections, and newer lost commits require separate verification."}
	var cp AuditCheckpoint
	var storage string
	// Read only header fields; even legacy manifests stay in PostgreSQL.
	err := tx.QueryRow(ctx, `SELECT manifest-'members',storage_schema FROM cairn.audit_checkpoint WHERE checkpoint_id=$1`, req.CheckpointID).Scan(&cp, &storage)
	if errors.Is(err, pgx.ErrNoRows) {
		return result, failure("CHECKPOINT_MISMATCH", "expected checkpoint is missing from the restore")
	}
	if err != nil {
		return result, err
	}
	if cp.Schema != checkpointSchema || cp.ID != req.CheckpointID || cp.ExportID != req.ExpectedExportID {
		return result, failure("CHECKPOINT_MISMATCH", "checkpoint metadata differs from expected restore set")
	}
	if _, err = tx.Exec(ctx, `CREATE TEMP TABLE IF NOT EXISTS cairn_checkpoint_expected(position bigint PRIMARY KEY,event_id text NOT NULL,digest text NOT NULL) ON COMMIT DROP;TRUNCATE pg_temp.cairn_checkpoint_expected`); err != nil {
		return result, err
	}
	switch storage {
	case checkpointInlineStorage:
		if cp.StorageSchema != "" && cp.StorageSchema != storage {
			return result, failure("CHECKPOINT_MISMATCH", "checkpoint storage representation differs")
		}
		_, err = tx.Exec(ctx, `INSERT INTO pg_temp.cairn_checkpoint_expected SELECT n,m->>'event_id',m->>'sha256' FROM cairn.audit_checkpoint c CROSS JOIN LATERAL jsonb_array_elements(c.manifest->'members') WITH ORDINALITY AS a(m,n) WHERE checkpoint_id=$1`, req.CheckpointID)
	case checkpointMemberStorage:
		var inline int
		if err = tx.QueryRow(ctx, `SELECT jsonb_array_length(manifest->'members') FROM cairn.audit_checkpoint WHERE checkpoint_id=$1`, req.CheckpointID).Scan(&inline); err != nil {
			return result, err
		}
		if cp.StorageSchema != storage || inline != 0 || !cp.MembersOmitted {
			return result, failure("CHECKPOINT_MISMATCH", "invalid member-backed checkpoint header")
		}
		_, err = tx.Exec(ctx, `INSERT INTO pg_temp.cairn_checkpoint_expected SELECT row_number() OVER(ORDER BY event_id),event_id::text,digest FROM cairn.audit_checkpoint_member WHERE checkpoint_id=$1`, req.CheckpointID)
	default:
		return result, failure("CHECKPOINT_MISMATCH", "unsupported checkpoint storage representation")
	}
	if err != nil {
		return result, err
	}
	var duplicate bool
	if err = tx.QueryRow(ctx, `SELECT count(*)<>count(DISTINCT event_id) FROM pg_temp.cairn_checkpoint_expected`).Scan(&duplicate); err != nil {
		return result, err
	}
	if duplicate {
		return result, failure("CHECKPOINT_MISMATCH", "duplicate checkpoint member")
	}
	digest, count, err := streamCheckpointDigest(ctx, tx, `SELECT event_id,digest FROM pg_temp.cairn_checkpoint_expected ORDER BY position`)
	if err != nil {
		return result, err
	}
	if count != cp.Count || digest != cp.Digest || digest != strings.ToLower(req.ExpectedDigest) {
		return result, failure("CHECKPOINT_MISMATCH", "checkpoint membership digest differs from backup catalog")
	}
	if err = stageActualAudit(ctx, tx); err != nil {
		return result, err
	}
	if err = tx.QueryRow(ctx, `SELECT count(*) FILTER(WHERE a.event_id IS NULL),count(*) FILTER(WHERE a.event_id IS NOT NULL AND a.digest<>e.digest) FROM pg_temp.cairn_checkpoint_expected e LEFT JOIN pg_temp.cairn_actual_audit a USING(event_id)`).Scan(&result.MissingTotal, &result.AlteredTotal); err != nil {
		return result, err
	}
	if err = tx.QueryRow(ctx, `SELECT count(*) FROM pg_temp.cairn_actual_audit a WHERE NOT EXISTS(SELECT 1 FROM pg_temp.cairn_checkpoint_expected e WHERE e.event_id=a.event_id)`).Scan(&result.UncoveredCount); err != nil {
		return result, err
	}
	rows, err := tx.Query(ctx, `(SELECT e.event_id,true FROM pg_temp.cairn_checkpoint_expected e LEFT JOIN pg_temp.cairn_actual_audit a USING(event_id) WHERE a.event_id IS NULL ORDER BY e.position LIMIT $1)
 UNION ALL (SELECT e.event_id,false FROM pg_temp.cairn_checkpoint_expected e JOIN pg_temp.cairn_actual_audit a USING(event_id) WHERE a.digest<>e.digest ORDER BY e.position LIMIT $1)`, maxReportedDetails)
	if err != nil {
		return result, err
	}
	for rows.Next() {
		var id string
		var missing bool
		if err = rows.Scan(&id, &missing); err != nil {
			rows.Close()
			return result, err
		}
		if missing && len(result.Missing) < maxReportedDetails {
			result.Missing = append(result.Missing, id)
		} else if !missing && len(result.Altered) < maxReportedDetails {
			result.Altered = append(result.Altered, id)
		}
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return result, err
	}
	result.Valid = result.MissingTotal == 0 && result.AlteredTotal == 0
	return result, nil
}
