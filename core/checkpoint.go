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

// Only emitted governance and C/D transition events participate. B promotion,
// correction and retraction and all ordinary A history are excluded. Reasons and
// content are excluded; this detects audit metadata damage, not payload damage.
// The member set has no count ceiling: memory grows with the number of these
// events (a few hundred bytes each), never with records, evidence or receipts.
func auditMembers(ctx context.Context, tx pgx.Tx) ([]AuditMember, error) {
	rows, err := tx.Query(ctx, `SELECT e.event_id::text,
 (jsonb_build_object('event_id',e.event_id,'event_type',e.event_type,'subject_id',e.subject_id,
 'previous_version',e.previous_version,'resulting_version',e.resulting_version,'actor',e.actor,
 'basis',e.basis,'transaction_id',e.transaction_id::text,
 'occurred_at_us',(extract(epoch from e.occurred_at)*1000000)::bigint)
 || CASE WHEN e.event_type='reapply_recovery' THEN jsonb_build_object('reapplication',
 (SELECT jsonb_build_object('source_root',a.source_root,'source_sha256',a.source_sha256,'actions',a.actions)
 FROM cairn.recovery_application a WHERE a.event_id=e.event_id))
 WHEN e.event_type='resume_restore' THEN jsonb_build_object('restore_resume',
 (SELECT jsonb_build_object('session_id',r.session_id,'policy',r.policy,'verification',r.verification)
 FROM cairn.restore_resume r WHERE r.event_id=e.event_id)) ELSE '{}'::jsonb END)::text
 FROM cairn.authority_event e
 WHERE e.event_type IN ('bootstrap','grant','revoke_grant','issue','authorize_scope','policy_revise','reapply_recovery','resume_restore','resolve','redact','forget')
 OR EXISTS(SELECT 1 FROM cairn.record_version v WHERE v.record_id=e.subject_id AND v.version=e.resulting_version AND v.version_class='C')
 ORDER BY e.event_id`)
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
func (s *Store) Checkpoint(ctx context.Context, req CheckpointRequest) (AuditCheckpoint, error) {
	ctx = s.recoveryContext(ctx)
	if err := s.checkpointAccess(); err != nil {
		return AuditCheckpoint{}, err
	}
	if strings.TrimSpace(req.ExportID) == "" || len(req.ExportID) > 256 {
		return AuditCheckpoint{}, failure("INVALID_REQUEST", "bounded export identity required")
	}
	// The member query is one snapshot. Serializable retry also protects request
	// retries across simultaneous checkpoint creation.
	return privileged(ctx, s, "checkpoint", req.RequestID, req, func(tx pgx.Tx) (AuditCheckpoint, error) {
		cp := AuditCheckpoint{Schema: checkpointSchema, ID: uuid.NewString(), ExportID: req.ExportID}
		var err error
		cp.Members, err = auditMembers(ctx, tx)
		if err != nil {
			return cp, err
		}
		cp.Count = len(cp.Members)
		cp.Digest, err = checkpointDigest(cp.Members)
		if err != nil {
			return cp, err
		}
		if err = tx.QueryRow(ctx, `SELECT transaction_timestamp()`).Scan(&cp.CreatedAt); err != nil {
			return cp, err
		}
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.audit_checkpoint(checkpoint_id,export_id,manifest) VALUES($1,$2,$3)`, cp.ID, cp.ExportID, cp); err != nil {
			return cp, err
		}
		// The manifest retains every member. The operator's backup catalog needs
		// only the identity, count and digest, so a large response omits the list.
		if len(cp.Members) > maxCheckpointMembers {
			cp.Members, cp.MembersOmitted = []AuditMember{}, true
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
	err := tx.QueryRow(ctx, `SELECT manifest FROM cairn.audit_checkpoint WHERE checkpoint_id=$1`, req.CheckpointID).Scan(&cp)
	if errors.Is(err, pgx.ErrNoRows) {
		return result, failure("CHECKPOINT_MISMATCH", "expected checkpoint is missing from the restore")
	}
	if err != nil {
		return result, err
	}
	if cp.Schema != checkpointSchema || cp.ID != req.CheckpointID || cp.ExportID != req.ExpectedExportID || cp.Count != len(cp.Members) {
		return result, failure("CHECKPOINT_MISMATCH", "checkpoint metadata differs from expected restore set")
	}
	digest, err := checkpointDigest(cp.Members)
	if err != nil {
		return result, err
	}
	if digest != cp.Digest || digest != strings.ToLower(req.ExpectedDigest) {
		return result, failure("CHECKPOINT_MISMATCH", "checkpoint membership digest differs from backup catalog")
	}
	current, err := auditMembers(ctx, tx)
	if err != nil {
		return result, err
	}
	byID := map[string]string{}
	for _, m := range current {
		byID[m.EventID] = m.Digest
	}
	seen := map[string]bool{}
	for _, m := range cp.Members {
		if seen[m.EventID] {
			return result, failure("CHECKPOINT_MISMATCH", "duplicate checkpoint member")
		}
		seen[m.EventID] = true
		actual, ok := byID[m.EventID]
		if !ok {
			result.MissingTotal++
			if len(result.Missing) < maxReportedDetails {
				result.Missing = append(result.Missing, m.EventID)
			}
		} else if actual != m.Digest {
			result.AlteredTotal++
			if len(result.Altered) < maxReportedDetails {
				result.Altered = append(result.Altered, m.EventID)
			}
		}
	}
	for id := range byID {
		if !seen[id] {
			result.UncoveredCount++
		}
	}
	result.Valid = result.MissingTotal == 0 && result.AlteredTotal == 0
	return result, nil
}
