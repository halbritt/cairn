package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
)

const recoverySchema = "cairn.recovery-record/1"

// MaxRecoveryBytes bounds the canonical exported JSON, including its newline.
const MaxRecoveryBytes = 16 * 1024 * 1024

func recoverySizeValid(record RecoveryRecord) error {
	body, err := json.MarshalIndent(record, "", "  ")
	if err != nil {
		return err
	}
	if len(body)+1 > MaxRecoveryBytes {
		return failure("BUDGET_REFUSED", "recovery record exceeds 16 MiB of exported JSON")
	}
	return nil
}

type RecoveryWithdrawal struct {
	Kind      string `json:"kind"`
	SubjectID string `json:"subject_id"`
	Repo      string `json:"repo"`
	EventID   string `json:"event_id"`
}
type RecoveryContext struct {
	RecordID string `json:"record_id"`
	ManagedContext
}
type RecoveryRecord struct {
	Schema      string               `json:"schema"`
	RootGrantID string               `json:"root_grant_id"`
	CapturedAt  time.Time            `json:"captured_at"`
	Audit       []AuditMember        `json:"audit"`
	Withdrawals []RecoveryWithdrawal `json:"withdrawals"`
	Contexts    []RecoveryContext    `json:"contexts"`
	SHA256      string               `json:"sha256,omitempty"`
}
type RecoveryGap struct {
	Kind      string `json:"kind"`
	SubjectID string `json:"subject_id,omitempty"`
	EventID   string `json:"event_id,omitempty"`
	Reason    string `json:"reason"`
}
type RecoveryInspection struct {
	RootGrantID        string        `json:"root_grant_id"`
	RecordSHA256       string        `json:"record_sha256"`
	Consistent         bool          `json:"consistent"`
	Gaps               []RecoveryGap `json:"gaps"`
	OutstandingEffects int           `json:"outstanding_effects"`
	ResidualEffects    int           `json:"residual_effects"`
	Coverage           string        `json:"coverage"`
}

func (s *Store) CaptureRecovery(ctx context.Context) (RecoveryRecord, error) {
	ctx = s.recoveryContext(ctx)
	if err := s.checkpointAccess(); err != nil {
		return RecoveryRecord{}, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return RecoveryRecord{}, err
	}
	defer tx.Rollback(ctx)
	record, err := captureRecoveryTx(ctx, tx)
	if err != nil {
		return record, err
	}
	return record, tx.Commit(ctx)
}
func recoveryDigest(record RecoveryRecord) (string, error) {
	record.SHA256 = ""
	body, err := json.Marshal(record)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(body)
	return hex.EncodeToString(sum[:]), nil
}
func (r RecoveryRecord) validate() error {
	if err := recoverySizeValid(r); err != nil {
		return err
	}
	if r.Schema != recoverySchema || r.CapturedAt.IsZero() || len(r.Audit) > 10000 || len(r.Withdrawals) > 10000 || len(r.Contexts) > 10000 {
		return failure("INVALID_REQUEST", "unsupported or oversized recovery record")
	}
	if err := validID(r.RootGrantID); err != nil {
		return err
	}
	digest, err := recoveryDigest(r)
	if err != nil {
		return err
	}
	if !digestValid(r.SHA256) || digest != r.SHA256 {
		return failure("INTEGRITY_FAILURE", "recovery record checksum does not match its content")
	}
	audit := map[string]bool{}
	for _, member := range r.Audit {
		if err := validID(member.EventID); err != nil {
			return err
		}
		if !digestValid(member.Digest) || audit[member.EventID] {
			return failure("INVALID_REQUEST", "invalid or duplicate audit member")
		}
		audit[member.EventID] = true
	}
	seen := map[string]bool{}
	forgotten := map[string]bool{}
	for _, w := range r.Withdrawals {
		if err := validID(w.SubjectID); err != nil {
			return err
		}
		if !audit[w.EventID] || seen[w.EventID] || w.Repo == "" || (w.Kind != "forget" && w.Kind != "revoke_grant" && w.Kind != "retract") {
			return failure("INVALID_REQUEST", "invalid withdrawal or missing audit membership")
		}
		seen[w.EventID] = true
		if w.Kind == "forget" {
			forgotten[w.SubjectID] = true
		}
	}
	seen = map[string]bool{}
	for _, c := range r.Contexts {
		if err := validID(c.ReceiptID); err != nil {
			return err
		}
		if err := validID(c.OwnershipID); err != nil {
			return err
		}
		key := c.RecordID + ":" + c.ReceiptID
		if !forgotten[c.RecordID] || seen[key] || !digestValid(c.BodySHA256) {
			return failure("INVALID_REQUEST", "context custody lacks a distinct forgotten-record reference")
		}
		seen[key] = true
	}
	return nil
}

// Inspection is consistency against an external known snapshot, not permission
// to resume a restore, a complete recovery audit, or proof of later absence.
func (s *Store) InspectRecovery(ctx context.Context, record RecoveryRecord) (RecoveryInspection, error) {
	ctx = s.recoveryContext(ctx)
	if err := s.checkpointAccess(); err != nil {
		return RecoveryInspection{}, err
	}
	if err := record.validate(); err != nil {
		return RecoveryInspection{}, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return RecoveryInspection{}, err
	}
	defer tx.Rollback(ctx)
	result, err := s.inspectRecoveryTx(ctx, tx, record)
	if err != nil {
		return result, err
	}
	return result, tx.Commit(ctx)
}
func inspectWithdrawal(ctx context.Context, tx pgx.Tx, w RecoveryWithdrawal) (string, error) {
	if w.Kind == "revoke_grant" {
		var repo string
		var revoked bool
		err := tx.QueryRow(ctx, `SELECT repo,revoked FROM cairn.authority_grant WHERE grant_id=$1`, w.SubjectID).Scan(&repo, &revoked)
		if errors.Is(err, pgx.ErrNoRows) {
			return "GRANT_MISSING", nil
		}
		if err != nil {
			return "", err
		}
		if repo != w.Repo {
			return "SCOPE_CHANGED", nil
		}
		if !revoked {
			return "GRANT_REVIVED", nil
		}
		return "", nil
	}
	var repo, lifecycle string
	err := tx.QueryRow(ctx, `SELECT v.repo,m.lifecycle FROM cairn.memory_record m JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version WHERE m.record_id=$1`, w.SubjectID).Scan(&repo, &lifecycle)
	if errors.Is(err, pgx.ErrNoRows) {
		return "RECORD_MISSING", nil
	}
	if err != nil {
		return "", err
	}
	if repo != w.Repo {
		return "SCOPE_CHANGED", nil
	}
	if w.Kind == "retract" {
		if lifecycle == "active" {
			return "INSTRUCTION_REVIVED", nil
		}
		return "", nil
	}
	if lifecycle != "tombstoned" {
		return "RECORD_REVIVED", nil
	}
	var unsafe bool
	err = tx.QueryRow(ctx, `SELECT
 NOT EXISTS(SELECT 1 FROM cairn.deletion_request WHERE record_id=$1)
 OR EXISTS(SELECT 1 FROM cairn.record_version WHERE record_id=$1 AND payload_deleted_by IS NULL)
 OR EXISTS(SELECT 1 FROM cairn.record_use u JOIN cairn.retrieval_receipt r USING(receipt_id) WHERE u.record_id=$1 AND r.payload_deleted_by IS NULL)
 OR EXISTS(SELECT 1 FROM cairn.mutation_request WHERE operation IN ('create','edit','promote','demote','issue','correct','retract','expand','expand-evidence') AND payload_deleted_by IS NULL AND jsonb_path_exists(response,'$.**.record_id ? (@ == $id)',jsonb_build_object('id',$1::text)))`, w.SubjectID).Scan(&unsafe)
	if err != nil {
		return "", err
	}
	if unsafe {
		return "PAYLOAD_EXCLUSION_MISSING", nil
	}
	refs, err := dependentVersions(ctx, tx, w.SubjectID)
	if err != nil {
		return "", err
	}
	err = tx.QueryRow(ctx, `SELECT EXISTS(
 SELECT 1 FROM jsonb_to_recordset($1) AS ref(record_id uuid,version integer)
 WHERE ref.record_id<>$2::uuid AND NOT EXISTS(
  SELECT 1 FROM cairn.deletion_dependency dep JOIN cairn.deletion_request d USING(deletion_id)
  WHERE dep.record_id=ref.record_id AND dep.version=ref.version AND d.record_id=$2 AND d.event_id=$3))`, refs, w.SubjectID, w.EventID).Scan(&unsafe)
	if err != nil {
		return "", err
	}
	if unsafe {
		return "DEPENDENCY_EXCLUSION_MISSING", nil
	}
	return "", nil
}

func captureRecoveryTx(ctx context.Context, tx pgx.Tx) (RecoveryRecord, error) {
	var err error
	record := RecoveryRecord{Schema: recoverySchema, Withdrawals: []RecoveryWithdrawal{}, Contexts: []RecoveryContext{}}
	if err = tx.QueryRow(ctx, `SELECT grant_id::text,transaction_timestamp() FROM cairn.authority_grant WHERE parent_id IS NULL`).Scan(&record.RootGrantID, &record.CapturedAt); errors.Is(err, pgx.ErrNoRows) {
		return record, failure("RECOVERY_UNINITIALIZED", "install the operator root before capturing a recovery record")
	} else if err != nil {
		return record, err
	}
	record.CapturedAt = record.CapturedAt.UTC()
	record.Audit, err = auditMembers(ctx, tx)
	if err != nil {
		return record, err
	}
	// Derive withdrawals from retained authority events, not the mutable state
	// being checked. A corrupt revived flag must not erase its own expectation.
	rows, err := tx.Query(ctx, `SELECT e.event_type,e.subject_id::text,COALESCE(g.repo,v.repo),e.event_id::text FROM cairn.authority_event e
 LEFT JOIN cairn.authority_grant g ON e.event_type='revoke_grant' AND g.grant_id=e.subject_id
 LEFT JOIN cairn.record_version v ON e.event_type IN ('forget','retract') AND v.record_id=e.subject_id AND v.version=e.resulting_version
 WHERE e.event_type IN ('revoke_grant','forget') OR (e.event_type='retract' AND v.version_class='C') ORDER BY e.event_id LIMIT 10001`)
	if err != nil {
		return record, err
	}
	for rows.Next() {
		var w RecoveryWithdrawal
		if err = rows.Scan(&w.Kind, &w.SubjectID, &w.Repo, &w.EventID); err != nil {
			rows.Close()
			return record, err
		}
		record.Withdrawals = append(record.Withdrawals, w)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return record, err
	}
	rows, err = tx.Query(ctx, `SELECT DISTINCT d.record_id::text,c.ownership_id::text,c.receipt_id::text,c.directory,c.directory_device,c.directory_inode,c.body_sha256
 FROM cairn.deletion_request d JOIN cairn.deletion_effect e USING(deletion_id) JOIN cairn.managed_context c ON e.target_type='managed_context' AND e.target_id=c.receipt_id::text ORDER BY d.record_id::text,c.receipt_id::text LIMIT 10001`)
	if err != nil {
		return record, err
	}
	for rows.Next() {
		var c RecoveryContext
		if err = rows.Scan(&c.RecordID, &c.OwnershipID, &c.ReceiptID, &c.Directory, &c.DirectoryDevice, &c.DirectoryInode, &c.BodySHA256); err != nil {
			rows.Close()
			return record, err
		}
		record.Contexts = append(record.Contexts, c)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return record, err
	}
	if len(record.Withdrawals) > 10000 || len(record.Contexts) > 10000 {
		return record, failure("BUDGET_REFUSED", "recovery record exceeds 10000 withdrawals or context references; no partial record exported")
	}
	rows, err = tx.Query(ctx, `SELECT source_record FROM cairn.recovery_application ORDER BY application_id LIMIT 10001`)
	if err != nil {
		return record, err
	}
	sources, err := pgx.CollectRows(rows, pgx.RowTo[RecoveryRecord])
	if err != nil {
		return record, err
	}
	if len(sources) > 10000 {
		return record, failure("BUDGET_REFUSED", "recovery application inventory exceeds 10000")
	}
	for _, source := range sources {
		if err = source.validate(); err != nil {
			return record, err
		}
		if err = mergeRecovery(&record, source); err != nil {
			return record, err
		}
	}
	record.SHA256, err = recoveryDigest(record)
	if err != nil {
		return record, err
	}
	return record, recoverySizeValid(record)
}

func (s *Store) inspectRecoveryTx(ctx context.Context, tx pgx.Tx, record RecoveryRecord) (RecoveryInspection, error) {
	var err error
	report := RecoveryInspection{RootGrantID: record.RootGrantID, RecordSHA256: record.SHA256, Gaps: []RecoveryGap{}, Coverage: "Known governance/C/D audit metadata, irreversible withdrawals and retained context custody at the external capture. Does not establish capture freshness, physical file state, complete recovery or permission to resume service."}
	var root string
	err = tx.QueryRow(ctx, `SELECT grant_id::text FROM cairn.authority_grant WHERE parent_id IS NULL`).Scan(&root)
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return report, err
	}
	if root != record.RootGrantID {
		report.Gaps = append(report.Gaps, RecoveryGap{Kind: "store", SubjectID: record.RootGrantID, Reason: "ROOT_MISMATCH"})
		return report, nil
	}
	actual, err := auditMembers(ctx, tx)
	if err != nil {
		return report, err
	}
	members := map[string]string{}
	for _, m := range actual {
		members[m.EventID] = m.Digest
	}
	for _, m := range record.Audit {
		if members[m.EventID] == "" {
			report.Gaps = append(report.Gaps, RecoveryGap{Kind: "audit", EventID: m.EventID, Reason: "AUDIT_MISSING"})
		} else if members[m.EventID] != m.Digest {
			report.Gaps = append(report.Gaps, RecoveryGap{Kind: "audit", EventID: m.EventID, Reason: "AUDIT_CHANGED"})
		}
	}
	expectedDigests := map[string]string{}
	for _, m := range record.Audit {
		expectedDigests[m.EventID] = m.Digest
	}
	for _, w := range record.Withdrawals {
		local := w
		if w.Kind == "forget" {
			var mapped string
			err := tx.QueryRow(ctx, `SELECT d.event_id::text FROM cairn.recovery_application a
 CROSS JOIN LATERAL jsonb_array_elements(a.actions) action
 JOIN cairn.deletion_request d ON d.deletion_id::text=action->>'deletion_id'
 WHERE a.source_root=$1 AND action->>'event_id'=$2 AND action->>'source_digest'=$3
 AND action->>'subject_id'=$4 AND action->>'repo'=$5 AND action->>'kind'='forget'
 AND d.record_id=$4::uuid AND d.event_id::text=action->>'current_event_id' LIMIT 1`, record.RootGrantID, w.EventID, expectedDigests[w.EventID], w.SubjectID, w.Repo).Scan(&mapped)
			if err != nil && !errors.Is(err, pgx.ErrNoRows) {
				return report, err
			}
			if err == nil {
				local.EventID = mapped
			}
		}
		reason, err := inspectWithdrawal(ctx, tx, local)
		if err != nil {
			return report, err
		}
		if reason != "" {
			report.Gaps = append(report.Gaps, RecoveryGap{Kind: w.Kind, SubjectID: w.SubjectID, EventID: w.EventID, Reason: reason})
		}
	}
	for _, c := range record.Contexts {
		var found bool
		err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.managed_context c JOIN cairn.record_use u USING(receipt_id) JOIN cairn.deletion_request d USING(record_id) JOIN cairn.deletion_effect e ON e.deletion_id=d.deletion_id AND e.target_type='managed_context' AND e.target_id=c.receipt_id::text WHERE c.receipt_id=$1 AND u.record_id=$7 AND directory=$2 AND directory_device=$3 AND directory_inode=$4 AND ownership_id=$5 AND body_sha256=$6)`, c.ReceiptID, c.Directory, c.DirectoryDevice, c.DirectoryInode, c.OwnershipID, c.BodySHA256, c.RecordID).Scan(&found)
		if err != nil {
			return report, err
		}
		if !found {
			err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.recovery_context c
 JOIN cairn.deletion_request d USING(deletion_id)
 JOIN cairn.deletion_effect e ON e.deletion_id=c.deletion_id AND e.target_type='managed_context' AND e.target_id=c.receipt_id::text
 WHERE c.receipt_id=$1 AND d.record_id=$7 AND c.directory=$2 AND c.directory_device=$3 AND c.directory_inode=$4 AND c.ownership_id=$5 AND c.body_sha256=$6)`, c.ReceiptID, c.Directory, c.DirectoryDevice, c.DirectoryInode, c.OwnershipID, c.BodySHA256, c.RecordID).Scan(&found)
			if err != nil {
				return report, err
			}
		}
		if !found {
			report.Gaps = append(report.Gaps, RecoveryGap{Kind: "managed_context", SubjectID: c.ReceiptID, Reason: "CONTEXT_CUSTODY_MISSING"})
		}
	}
	err = tx.QueryRow(ctx, `SELECT count(*) FILTER(WHERE status IN ('pending','running','failed')),count(*) FILTER(WHERE status='not_possible') FROM cairn.deletion_effect`).Scan(&report.OutstandingEffects, &report.ResidualEffects)
	if err != nil {
		return report, err
	}
	report.Consistent = len(report.Gaps) == 0
	return report, nil
}
