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

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

const recoverySchema = "cairn.recovery-record/1"

// MaxRecoveryBytes bounds the canonical exported JSON, including its newline.
const MaxRecoveryBytes = 16 * 1024 * 1024

// One recovery record holds at most this many audit members, withdrawals or
// context references. A larger expectation set is exported as a segment set.
const maxRecoveryEntries = 10000

// Segment limits keep every record independently valid and applicable: one
// segment's withdrawals run in a single serializable transaction and its JSON
// stays far below MaxRecoveryBytes even with maximum-length context directories.
const (
	recoverySegmentAudit       = 4000
	recoverySegmentWithdrawals = 2000
	recoverySegmentContexts    = 1000
	maxRecoverySegments        = 100000
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

// CaptureRecovery returns the expectations as one record. A set too large for one
// valid record is refused rather than exported partially; use CaptureRecoverySet.
func (s *Store) CaptureRecovery(ctx context.Context) (RecoveryRecord, error) {
	set, err := s.CaptureRecoverySet(ctx)
	if err != nil {
		return RecoveryRecord{}, err
	}
	if len(set) != 1 {
		return RecoveryRecord{}, failure("BUDGET_REFUSED", "recovery expectations exceed one exportable record; export the complete segment set")
	}
	return set[0], nil
}

// CaptureRecoverySet captures every expectation in one snapshot. It returns one
// unmarked record when that fits the single-record bounds, otherwise bounded
// segments that share a set identity and must be retained together. Memory grows
// with the number of governance withdrawals and retained custody references.
func (s *Store) CaptureRecoverySet(ctx context.Context) ([]RecoveryRecord, error) {
	ctx = s.recoveryContext(ctx)
	if err := s.checkpointAccess(); err != nil {
		return nil, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	known := &recoverySegments{}
	record, err := captureRecovery(ctx, tx, known)
	if err != nil {
		return nil, err
	}
	if missing := known.incomplete(); len(missing) != 0 {
		return nil, failure("INTEGRITY_FAILURE", "retained recovery segment set is incomplete; retain its missing original segments before exporting")
	}
	set, err := splitRecovery(record)
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(ctx); err != nil {
		return nil, err
	}
	return set, nil
}

func recoveryFitsOne(record RecoveryRecord) (bool, error) {
	if len(record.Audit) > maxRecoveryEntries || len(record.Withdrawals) > maxRecoveryEntries || len(record.Contexts) > maxRecoveryEntries {
		return false, nil
	}
	if err := recoverySizeValid(record); err != nil {
		if Code(err) == "BUDGET_REFUSED" {
			return false, nil
		}
		return false, err
	}
	return true, nil
}

// splitRecovery keeps the union of expectations and divides only its transport.
// Range segments carry audit members with their withdrawals. A context segment
// also carries the forget withdrawal (and audit member) that anchors each of its
// records, so every segment validates and reapplies on its own; merging repeated
// identical entries is idempotent. Nothing is dropped: the assigned counts are
// checked before any partial set could be returned.
func splitRecovery(full RecoveryRecord) ([]RecoveryRecord, error) {
	fits, err := recoveryFitsOne(full)
	if err != nil {
		return nil, err
	}
	if fits {
		return []RecoveryRecord{full}, nil
	}
	digests := make(map[string]string, len(full.Audit))
	for _, member := range full.Audit {
		digests[member.EventID] = member.Digest
	}
	byEvent := make(map[string]RecoveryWithdrawal, len(full.Withdrawals))
	anchors := map[string]string{}
	// Withdrawals are sorted by event, so the first forget per subject is lowest.
	for _, w := range full.Withdrawals {
		byEvent[w.EventID] = w
		if _, ok := anchors[w.SubjectID]; !ok && w.Kind == "forget" {
			anchors[w.SubjectID] = w.EventID
		}
	}
	fresh := func() RecoveryRecord {
		return RecoveryRecord{Schema: recoverySchema, RootGrantID: full.RootGrantID, CapturedAt: full.CapturedAt, Audit: []AuditMember{}, Withdrawals: []RecoveryWithdrawal{}, Contexts: []RecoveryContext{}}
	}
	var segments []RecoveryRecord
	current := fresh()
	assigned := 0
	for _, member := range full.Audit {
		current.Audit = append(current.Audit, member)
		if w, ok := byEvent[member.EventID]; ok {
			current.Withdrawals = append(current.Withdrawals, w)
			assigned++
		}
		if len(current.Audit) >= recoverySegmentAudit || len(current.Withdrawals) >= recoverySegmentWithdrawals {
			segments = append(segments, current)
			current = fresh()
		}
	}
	if len(current.Audit) > 0 {
		segments = append(segments, current)
	}
	if assigned != len(full.Withdrawals) {
		return nil, failure("INTEGRITY_FAILURE", "recovery withdrawal lacks audit membership; no partial segment set exported")
	}
	for start := 0; start < len(full.Contexts); start += recoverySegmentContexts {
		end := min(start+recoverySegmentContexts, len(full.Contexts))
		segment := fresh()
		segment.Contexts = append(segment.Contexts, full.Contexts[start:end]...)
		anchored := map[string]bool{}
		for _, c := range segment.Contexts {
			event, ok := anchors[c.RecordID]
			if !ok {
				return nil, failure("INTEGRITY_FAILURE", "context custody lacks a forgotten-record withdrawal; no partial segment set exported")
			}
			if !anchored[event] {
				anchored[event] = true
				segment.Audit = append(segment.Audit, AuditMember{EventID: event, Digest: digests[event]})
				segment.Withdrawals = append(segment.Withdrawals, byEvent[event])
			}
		}
		sort.Slice(segment.Audit, func(i, j int) bool { return segment.Audit[i].EventID < segment.Audit[j].EventID })
		sort.Slice(segment.Withdrawals, func(i, j int) bool { return segment.Withdrawals[i].EventID < segment.Withdrawals[j].EventID })
		segments = append(segments, segment)
	}
	if len(segments) == 0 {
		return []RecoveryRecord{full}, nil
	}
	setID := uuid.NewString()
	for i := range segments {
		if len(segments) > 1 {
			segments[i].Segment = &RecoverySegment{SetID: setID, Index: i + 1, Count: len(segments)}
		}
		if segments[i].SHA256, err = recoveryDigest(segments[i]); err != nil {
			return nil, err
		}
		if err = segments[i].validate(); err != nil {
			return nil, err
		}
	}
	return segments, nil
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
		if r.Segment.Count < 2 || r.Segment.Count > maxRecoverySegments || r.Segment.Index < 1 || r.Segment.Index > r.Segment.Count {
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

// captureRecoveryTx returns every known expectation: local authority events,
// retained custody and each retained external source. It has no count ceiling;
// callers that export the result split it with splitRecovery.
func captureRecoveryTx(ctx context.Context, tx pgx.Tx) (RecoveryRecord, error) {
	return captureRecovery(ctx, tx, &recoverySegments{})
}

// captureRecovery also records which positions of retained segment sets are
// present, so verification can refuse a set it holds only in part.
func captureRecovery(ctx context.Context, tx pgx.Tx, segments *recoverySegments) (RecoveryRecord, error) {
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
 WHERE e.event_type IN ('revoke_grant','forget') OR (e.event_type='retract' AND v.version_class='C') ORDER BY e.event_id`)
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
 FROM cairn.deletion_request d JOIN cairn.deletion_effect e USING(deletion_id) JOIN cairn.managed_context c ON e.target_type='managed_context' AND e.target_id=c.receipt_id::text ORDER BY d.record_id::text,c.receipt_id::text`)
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
	// Retained external sources merge one at a time, so only the union and the
	// current source are held; each source was validated when it was applied.
	rows, err = tx.Query(ctx, `SELECT source_record FROM cairn.recovery_application ORDER BY application_id`)
	if err != nil {
		return record, err
	}
	merger := newRecoveryMerger(&record)
	for rows.Next() {
		var source RecoveryRecord
		if err = rows.Scan(&source); err == nil {
			err = source.validate()
		}
		if err == nil {
			err = merger.add(source)
		}
		if err == nil {
			err = segments.add(source)
		}
		if err != nil {
			rows.Close()
			return record, err
		}
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return record, err
	}
	merger.finish()
	record.SHA256, err = recoveryDigest(record)
	return record, err
}

// recoveryActionKey identifies the original withdrawal a retained reapplication
// action answers. The source root is fixed by the query that loads the index.
type recoveryActionKey struct{ event, digest, subject, repo, kind string }

func loadRecoveryActions(ctx context.Context, tx pgx.Tx, root string) (map[recoveryActionKey][]RecoveryAction, error) {
	rows, err := tx.Query(ctx, `SELECT actions FROM cairn.recovery_application WHERE source_root=$1::uuid ORDER BY application_id`, root)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	index := map[recoveryActionKey][]RecoveryAction{}
	for rows.Next() {
		var actions []RecoveryAction
		if err = rows.Scan(&actions); err != nil {
			return nil, err
		}
		for _, a := range actions {
			key := recoveryActionKey{a.EventID, a.SourceDigest, a.SubjectID, a.Repo, a.Kind}
			index[key] = append(index[key], a)
		}
	}
	return index, rows.Err()
}

// mappedDeletionEvent returns the current deletion event that a retained action
// recorded for an original forget, only while that deletion still exists for the
// same record and carries exactly that event.
func mappedDeletionEvent(ctx context.Context, tx pgx.Tx, actions map[recoveryActionKey][]RecoveryAction, w RecoveryWithdrawal, digest string) (string, error) {
	for _, action := range actions[recoveryActionKey{w.EventID, digest, w.SubjectID, w.Repo, w.Kind}] {
		if action.DeletionID == "" || action.CurrentEventID == "" {
			continue
		}
		var event string
		err := tx.QueryRow(ctx, `SELECT event_id::text FROM cairn.deletion_request WHERE deletion_id=$1::uuid AND record_id=$2::uuid`, action.DeletionID, w.SubjectID).Scan(&event)
		if errors.Is(err, pgx.ErrNoRows) {
			continue
		}
		if err != nil {
			return "", err
		}
		if event == action.CurrentEventID {
			return event, nil
		}
	}
	return "", nil
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
	// Retained reapplication actions are indexed once. Looking each forgotten
	// withdrawal up by scanning every application would grow with the product of
	// both counts.
	var actions map[recoveryActionKey][]RecoveryAction
	for _, w := range record.Withdrawals {
		local := w
		if w.Kind == "forget" {
			if actions == nil {
				actions, err = loadRecoveryActions(ctx, tx, record.RootGrantID)
				if err != nil {
					return report, err
				}
			}
			mapped, err := mappedDeletionEvent(ctx, tx, actions, w, expectedDigests[w.EventID])
			if err != nil {
				return report, err
			}
			if mapped != "" {
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
