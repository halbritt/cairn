package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

const recoverySchema = "cairn.recovery-record/1"

// MaxRecoveryBytes bounds the canonical exported JSON, including its newline.
const MaxRecoveryBytes = 16 * 1024 * 1024

// One recovery record holds at most this many audit members, withdrawals or
// context references. A larger expectation set is exported as a segment set.
const maxRecoveryEntries = 10000

// MaxRecoverySegments is the existing recovery-record/1 position bound.
const MaxRecoverySegments = 100000

// Segment limits keep every record independently valid and applicable: one
// segment's withdrawals run in a single serializable transaction and its JSON
// stays far below MaxRecoveryBytes even with maximum-length context directories.
const (
	recoverySegmentWithdrawals = 2000
	recoverySegmentContexts    = 1000
)

// maxReportedDetails bounds the divergences named in any one report. Counts keep
// covering every divergence, so a large damaged restore cannot hide behind the
// output bound or produce unbounded output.
const maxReportedDetails = 100

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

// RecoverySegment marks one record of an exported set. The records of a set come
// from one snapshot; retaining or supplying every position is what lets a
// verifier know it holds the complete expectations.
type RecoverySegment struct {
	SetID string `json:"set_id"`
	Index int    `json:"index"`
	Count int    `json:"count"`
}
type RecoveryRecord struct {
	Schema      string               `json:"schema"`
	RootGrantID string               `json:"root_grant_id"`
	CapturedAt  time.Time            `json:"captured_at"`
	Audit       []AuditMember        `json:"audit"`
	Withdrawals []RecoveryWithdrawal `json:"withdrawals"`
	Contexts    []RecoveryContext    `json:"contexts"`
	Segment     *RecoverySegment     `json:"segment,omitempty"`
	SHA256      string               `json:"sha256,omitempty"`
}
type RecoveryGap struct {
	Kind      string `json:"kind"`
	SubjectID string `json:"subject_id,omitempty"`
	EventID   string `json:"event_id,omitempty"`
	Reason    string `json:"reason"`
}

// Gaps names at most the first 100 findings and AdditionalGaps counts the rest;
// Consistent always reflects every finding.
type RecoveryInspection struct {
	RootGrantID        string        `json:"root_grant_id"`
	RecordSHA256       string        `json:"record_sha256"`
	Consistent         bool          `json:"consistent"`
	Gaps               []RecoveryGap `json:"gaps"`
	AdditionalGaps     int           `json:"additional_gaps,omitempty"`
	OutstandingEffects int           `json:"outstanding_effects"`
	ResidualEffects    int           `json:"residual_effects"`
	Coverage           string        `json:"coverage"`
}

// CaptureRecovery is the one-record compatibility API. Larger stores use the
// streaming operator path; refusal never means a prefix was captured.
func (s *Store) CaptureRecovery(ctx context.Context) (RecoveryRecord, error) {
	var result RecoveryRecord
	_, err := s.StreamRecovery(ctx, func(record RecoveryRecord) error {
		if record.Segment != nil {
			return failure("BUDGET_REFUSED", "use streaming recovery-export --directory for this recovery set")
		}
		result = record
		return nil
	})
	return result, err
}

// CaptureRecoverySet is a bounded compatibility collector. Directory export
// streams larger sets without this aggregate allocation or collector bound.
func (s *Store) CaptureRecoverySet(ctx context.Context) ([]RecoveryRecord, error) {
	var records []RecoveryRecord
	total := 0
	_, err := s.StreamRecovery(ctx, func(record RecoveryRecord) error {
		body, err := json.MarshalIndent(record, "", "  ")
		if err != nil {
			return err
		}
		total += len(body) + 1
		if total > MaxRecoveryBytes {
			return failure("BUDGET_REFUSED", "recovery collector exceeds 16 MiB; use streaming recovery-export --directory")
		}
		records = append(records, record)
		return nil
	})
	if err != nil {
		return nil, err
	}
	return records, nil
}

// recoverySegments tracks which positions of each segment set are retained or
// supplied, so a verifier cannot certify expectations it only partly holds.
type recoverySegments struct {
	sets map[string]*recoverySegmentSet
}
type recoverySegmentSet struct {
	count int
	seen  map[int]string
}

func (t *recoverySegments) add(record RecoveryRecord) error {
	if record.Segment == nil {
		return nil
	}
	if t.sets == nil {
		t.sets = map[string]*recoverySegmentSet{}
	}
	set := t.sets[record.Segment.SetID]
	if set == nil {
		set = &recoverySegmentSet{count: record.Segment.Count, seen: map[int]string{}}
		t.sets[record.Segment.SetID] = set
	}
	if set.count != record.Segment.Count {
		return failure("INTEGRITY_FAILURE", "recovery segments disagree about their set size")
	}
	if prior, ok := set.seen[record.Segment.Index]; ok && prior != record.SHA256 {
		return failure("INTEGRITY_FAILURE", "recovery segments disagree about the same set position")
	}
	set.seen[record.Segment.Index] = record.SHA256
	return nil
}

// incomplete names each referenced set that lacks positions as "set:have/count".
func (t *recoverySegments) incomplete() []string {
	var missing []string
	for id, set := range t.sets {
		if len(set.seen) != set.count {
			missing = append(missing, fmt.Sprintf("%s:%d/%d", id, len(set.seen), set.count))
		}
	}
	sort.Strings(missing)
	return missing
}

// CheckRecoverySegments refuses a supplied file list that names a segment set but
// omits one of its positions. Unsegmented records are always complete.
func CheckRecoverySegments(records []RecoveryRecord) error {
	var tracker recoverySegments
	for _, record := range records {
		if err := tracker.add(record); err != nil {
			return err
		}
	}
	if missing := tracker.incomplete(); len(missing) > 0 {
		return failure("INTEGRITY_FAILURE", "recovery segment set is incomplete: "+strings.Join(missing, ","))
	}
	return nil
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
	if r.Schema != recoverySchema || r.CapturedAt.IsZero() || len(r.Audit) > maxRecoveryEntries || len(r.Withdrawals) > maxRecoveryEntries || len(r.Contexts) > maxRecoveryEntries {
		return failure("INVALID_REQUEST", "unsupported or oversized recovery record")
	}
	if err := validID(r.RootGrantID); err != nil {
		return err
	}
	if r.Segment != nil {
		if err := validID(r.Segment.SetID); err != nil {
			return err
		}
		if r.Segment.Count < 2 || r.Segment.Count > MaxRecoverySegments || r.Segment.Index < 1 || r.Segment.Index > r.Segment.Count {
			return failure("INVALID_REQUEST", "invalid recovery segment position")
		}
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
	if len(result.Gaps) > maxReportedDetails {
		result.AdditionalGaps = len(result.Gaps) - maxReportedDetails
		result.Gaps = result.Gaps[:maxReportedDetails]
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
 OR EXISTS(SELECT 1 FROM cairn.mutation_request WHERE operation IN ('create','edit','promote','demote','issue','correct','retract','expand','expand-evidence','replace','revise','append','cite','supersede') AND payload_deleted_by IS NULL AND jsonb_path_exists(response,'$.**.record_id ? (@ == $id)',jsonb_build_object('id',$1::text)))`, w.SubjectID).Scan(&unsafe)
	if err != nil {
		return "", err
	}
	if unsafe {
		return "PAYLOAD_EXCLUSION_MISSING", nil
	}
	// The closure is evaluated by PostgreSQL, so descendants added after the
	// forget cannot push this check past the ordinary 1,000-version preview bound.
	err = tx.QueryRow(ctx, dependentClosureSQL+`SELECT EXISTS(
 SELECT 1 FROM dependents ref
 WHERE ref.record_id<>$1::uuid AND NOT EXISTS(
  SELECT 1 FROM cairn.deletion_dependency dep JOIN cairn.deletion_request d USING(deletion_id)
  WHERE dep.record_id=ref.record_id AND dep.version=ref.version AND d.record_id=$1::uuid AND d.event_id=$2::uuid))`, w.SubjectID, w.EventID).Scan(&unsafe)
	if err != nil {
		return "", err
	}
	if unsafe {
		return "DEPENDENCY_EXCLUSION_MISSING", nil
	}
	return "", nil
}

func (s *Store) inspectRecoveryTx(ctx context.Context, tx pgx.Tx, record RecoveryRecord) (RecoveryInspection, error) {
	report := RecoveryInspection{RootGrantID: record.RootGrantID, RecordSHA256: record.SHA256, Gaps: []RecoveryGap{}, Coverage: "Known governance/C/D audit metadata, irreversible withdrawals and retained context custody at the external capture. Does not establish capture freshness, physical file state, complete recovery or permission to resume service."}
	var root string
	err := tx.QueryRow(ctx, `SELECT grant_id::text FROM cairn.authority_grant WHERE parent_id IS NULL`).Scan(&root)
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return report, err
	}
	if root != record.RootGrantID {
		report.Gaps = append(report.Gaps, RecoveryGap{Kind: "store", SubjectID: record.RootGrantID, Reason: "ROOT_MISMATCH"})
		return report, nil
	}
	staged, err := newRecoveryStage(ctx, tx)
	if err != nil {
		return report, err
	}
	if err = staged.add(ctx, record); err != nil {
		return report, err
	}
	gaps := 0
	if err = staged.inspect(ctx, func(g RecoveryGap) error {
		if len(report.Gaps) < maxReportedDetails {
			report.Gaps = append(report.Gaps, g)
		}
		gaps++
		return nil
	}); err != nil {
		return report, err
	}
	report.AdditionalGaps = max(0, gaps-maxReportedDetails)
	if err = tx.QueryRow(ctx, `SELECT count(*) FILTER(WHERE status IN ('pending','running','failed')),count(*) FILTER(WHERE status='not_possible') FROM cairn.deletion_effect`).Scan(&report.OutstandingEffects, &report.ResidualEffects); err != nil {
		return report, err
	}
	report.Consistent = gaps == 0
	return report, nil
}
