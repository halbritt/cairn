package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"errors"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

const ManagedEvidenceInlineThresholdBytes = 65536

type EvidenceFreshnessItem struct {
	EvidenceID      string     `json:"evidence_id"`
	Source          string     `json:"source"`
	BodyBytes       int        `json:"body_bytes"`
	StorageTier     string     `json:"storage_tier"`
	State           string     `json:"state"`
	CheckGeneration int        `json:"check_generation"`
	CheckedAt       *time.Time `json:"checked_at,omitempty"`
	CheckStatus     string     `json:"check_status"`
	Stale           bool       `json:"stale"`
}

type DependentInvalidationCandidate struct {
	RecordID   string `json:"record_id"`
	Version    int    `json:"version"`
	Class      string `json:"class"`
	EvidenceID string `json:"evidence_id"`
	Reason     string `json:"reason"`
}

type EvidenceFreshnessReport struct {
	Repo                   string                           `json:"repo"`
	MaxAgeSeconds          int64                            `json:"max_age_seconds"`
	TotalEvidence          int                              `json:"total_evidence"`
	InlineCount            int                              `json:"inline_count"`
	ManagedThresholdCount  int                              `json:"managed_threshold_count"`
	StaleOrFailedCount     int                              `json:"stale_or_failed_count"`
	Items                  []EvidenceFreshnessItem          `json:"items"`
	InvalidationCandidates []DependentInvalidationCandidate `json:"invalidation_candidates"`
}

func (s *Store) AuditEvidenceFreshness(ctx context.Context, repo string, maxAge time.Duration) (EvidenceFreshnessReport, error) {
	if err := s.checkRepo(repo); err != nil {
		return EvidenceFreshnessReport{}, err
	}
	if strings.TrimSpace(repo) == "" || repo == "*" || maxAge <= 0 {
		return EvidenceFreshnessReport{}, failure("INVALID_REQUEST", "explicit repo and positive maxAge required")
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return EvidenceFreshnessReport{}, err
	}
	defer tx.Rollback(ctx)
	var now time.Time
	if err = tx.QueryRow(ctx, `SELECT transaction_timestamp()`).Scan(&now); err != nil {
		return EvidenceFreshnessReport{}, err
	}
	rows, err := tx.Query(ctx, `SELECT evidence_id::text,source,body,digest,state,check_generation,checked_at
		FROM cairn.evidence WHERE repo=$1 ORDER BY evidence_id`, repo)
	if err != nil {
		return EvidenceFreshnessReport{}, err
	}
	defer rows.Close()
	report := EvidenceFreshnessReport{
		Repo:                   repo,
		MaxAgeSeconds:          int64(maxAge / time.Second),
		Items:                  []EvidenceFreshnessItem{},
		InvalidationCandidates: []DependentInvalidationCandidate{},
	}
	staleReasons := map[string]string{}
	for rows.Next() {
		var item EvidenceFreshnessItem
		var body, digest []byte
		if err = rows.Scan(&item.EvidenceID, &item.Source, &body, &digest, &item.State, &item.CheckGeneration, &item.CheckedAt); err != nil {
			return EvidenceFreshnessReport{}, err
		}
		item.BodyBytes = len(body)
		if item.BodyBytes > ManagedEvidenceInlineThresholdBytes {
			item.StorageTier = "managed_threshold"
			report.ManagedThresholdCount++
		} else {
			item.StorageTier = "inline"
			report.InlineCount++
		}
		actual := sha256.Sum256(body)
		switch {
		case item.State != "resolvable" || !bytes.Equal(actual[:], digest):
			item.CheckStatus = "failed"
		case item.CheckGeneration == 0 || item.CheckedAt == nil:
			item.CheckStatus = "unverified"
		default:
			item.CheckStatus = "verified"
		}
		if item.CheckStatus != "verified" || now.Sub(*item.CheckedAt) > maxAge {
			item.Stale = true
			report.StaleOrFailedCount++
			staleReasons[item.EvidenceID] = "evidence_" + item.CheckStatus
			if item.CheckStatus == "verified" {
				staleReasons[item.EvidenceID] = "evidence_stale"
			}
		}
		report.Items = append(report.Items, item)
	}
	if err = rows.Err(); err != nil {
		return EvidenceFreshnessReport{}, err
	}
	report.TotalEvidence = len(report.Items)
	return s.collectEvidenceInvalidations(ctx, tx, report, staleReasons)
}

func (s *Store) collectEvidenceInvalidations(ctx context.Context, tx pgx.Tx, report EvidenceFreshnessReport, staleReasons map[string]string) (EvidenceFreshnessReport, error) {
	if len(staleReasons) == 0 {
		return report, tx.Commit(ctx)
	}
	rows, err := tx.Query(ctx, `SELECT m.record_id::text,m.current_version,m.class,r.evidence_id::text
		FROM cairn.evidence_ref r
		JOIN cairn.memory_record m ON m.record_id=r.record_id AND m.current_version=r.version
		JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version
		WHERE v.repo=$1 AND m.lifecycle='active' AND m.class IN ('B','C')
		ORDER BY m.record_id,r.evidence_id`, report.Repo)
	if err != nil {
		return EvidenceFreshnessReport{}, err
	}
	defer rows.Close()
	for rows.Next() {
		var c DependentInvalidationCandidate
		if err = rows.Scan(&c.RecordID, &c.Version, &c.Class, &c.EvidenceID); err != nil {
			return EvidenceFreshnessReport{}, err
		}
		if reason, ok := staleReasons[c.EvidenceID]; ok {
			c.Reason = reason
			report.InvalidationCandidates = append(report.InvalidationCandidates, c)
		}
	}
	if err = rows.Err(); err != nil {
		return EvidenceFreshnessReport{}, err
	}
	return report, tx.Commit(ctx)
}

type TaskContextBudgetStatus struct {
	Repo                    string `json:"repo"`
	TaskID                  string `json:"task_id"`
	CeilingBytes            int    `json:"ceiling_bytes"`
	ReceiptCount            int    `json:"receipt_count"`
	CompactionOrResumeCount int    `json:"compaction_or_resume_count"`
	ExpansionCount          int    `json:"expansion_count"`
	ReceiptBytes            int    `json:"receipt_bytes"`
	ExpansionBytes          int    `json:"expansion_bytes"`
	TotalBytes              int    `json:"total_bytes"`
	RemainingBytes          int    `json:"remaining_bytes"`
	InterlockRefused        bool   `json:"interlock_refused"`
}

func (s *Store) EvaluateTaskContextBudget(ctx context.Context, repo, taskID string, ceilingBytes int) (TaskContextBudgetStatus, error) {
	if err := s.checkRepo(repo); err != nil {
		return TaskContextBudgetStatus{}, err
	}
	if strings.TrimSpace(repo) == "" || repo == "*" || strings.TrimSpace(taskID) == "" || taskID == "*" || ceilingBytes <= 0 {
		return TaskContextBudgetStatus{}, failure("INVALID_REQUEST", "explicit repo, task_id, and positive ceilingBytes required")
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return TaskContextBudgetStatus{}, err
	}
	defer tx.Rollback(ctx)
	status := TaskContextBudgetStatus{Repo: repo, TaskID: taskID, CeilingBytes: ceilingBytes}
	err = tx.QueryRow(ctx, `SELECT count(*),
		count(*) FILTER (WHERE r.purpose IN ('resume','compaction','handoff') OR EXISTS (SELECT 1 FROM cairn.managed_context mc WHERE mc.receipt_id=r.receipt_id)),
		COALESCE(sum(octet_length(r.semantic_body)),0)
		FROM cairn.retrieval_receipt r
		WHERE r.scope->>'repo'=$1 AND r.scope->>'task_id'=$2`, repo, taskID).Scan(&status.ReceiptCount, &status.CompactionOrResumeCount, &status.ReceiptBytes)
	if err != nil {
		return TaskContextBudgetStatus{}, err
	}
	err = tx.QueryRow(ctx, `SELECT count(*),COALESCE(sum(octet_length(rv.body)),0)
		FROM cairn.usage_observation uo
		JOIN cairn.retrieval_receipt r USING(receipt_id)
		JOIN cairn.record_version rv ON rv.record_id=uo.record_id AND rv.version=uo.version
		WHERE uo.signal='expanded' AND r.scope->>'repo'=$1 AND r.scope->>'task_id'=$2`, repo, taskID).Scan(&status.ExpansionCount, &status.ExpansionBytes)
	if err != nil {
		return TaskContextBudgetStatus{}, err
	}
	status.TotalBytes = status.ReceiptBytes + status.ExpansionBytes
	status.RemainingBytes = max(0, ceilingBytes-status.TotalBytes)
	status.InterlockRefused = status.TotalBytes > ceilingBytes
	return status, tx.Commit(ctx)
}

type ActedUnderOpenConflictExposure struct {
	ConflictID string    `json:"conflict_id"`
	ReceiptID  string    `json:"receipt_id"`
	RecordID   string    `json:"record_id"`
	Version    int       `json:"version"`
	UsedAt     time.Time `json:"used_at"`
}

type ActiveScopeWaiverSummary struct {
	RecordID        string `json:"record_id"`
	Version         int    `json:"version"`
	PreviousVersion int    `json:"previous_version"`
	Class           string `json:"class"`
	GrantID         string `json:"grant_id"`
	EventID         string `json:"event_id"`
}

type GovernanceConflictWaiverReport struct {
	Repo                    string                           `json:"repo"`
	OpenConflictCount       int                              `json:"open_conflict_count"`
	ResolvedConflictCount   int                              `json:"resolved_conflict_count"`
	ActedUnderOpenConflicts []ActedUnderOpenConflictExposure `json:"acted_under_open_conflicts"`
	TotalRefusals           int                              `json:"total_refusals"`
	CompleteRefusalTraces   int                              `json:"complete_refusal_traces"`
	IncompleteRefusalTraces int                              `json:"incomplete_refusal_traces"`
	ActiveScopeWaivers      []ActiveScopeWaiverSummary       `json:"active_scope_waivers"`
}

func (s *Store) InspectGovernanceConflictAndWaiverStatus(ctx context.Context, repo string) (GovernanceConflictWaiverReport, error) {
	if err := s.checkRepo(repo); err != nil {
		return GovernanceConflictWaiverReport{}, err
	}
	if strings.TrimSpace(repo) == "" || repo == "*" {
		return GovernanceConflictWaiverReport{}, failure("INVALID_REQUEST", "explicit repo required")
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return GovernanceConflictWaiverReport{}, err
	}
	defer tx.Rollback(ctx)
	report := GovernanceConflictWaiverReport{
		Repo:                    repo,
		ActedUnderOpenConflicts: []ActedUnderOpenConflictExposure{},
		ActiveScopeWaivers:      []ActiveScopeWaiverSummary{},
	}
	err = tx.QueryRow(ctx, `SELECT count(*) FILTER (WHERE resolved_event IS NULL),
		count(*) FILTER (WHERE resolved_event IS NOT NULL)
		FROM cairn.conflict_group WHERE repo=$1`, repo).Scan(&report.OpenConflictCount, &report.ResolvedConflictCount)
	if err != nil {
		return GovernanceConflictWaiverReport{}, err
	}
	rows, err := tx.Query(ctx, `SELECT cg.conflict_id::text,u.receipt_id::text,u.record_id::text,u.version,u.used_at
		FROM cairn.record_use u
		JOIN cairn.conflict_member cm ON cm.record_id=u.record_id AND cm.version=u.version
		JOIN cairn.conflict_group cg ON cg.conflict_id=cm.conflict_id
		LEFT JOIN cairn.authority_event ae ON ae.event_id=cg.resolved_event
		WHERE cg.repo=$1 AND u.used_at>=cg.opened_at AND (cg.resolved_event IS NULL OR u.used_at<ae.occurred_at)
		ORDER BY u.used_at,cg.conflict_id,u.receipt_id,u.record_id`, repo)
	if err != nil {
		return GovernanceConflictWaiverReport{}, err
	}
	for rows.Next() {
		var exp ActedUnderOpenConflictExposure
		if err = rows.Scan(&exp.ConflictID, &exp.ReceiptID, &exp.RecordID, &exp.Version, &exp.UsedAt); err != nil {
			rows.Close()
			return GovernanceConflictWaiverReport{}, err
		}
		report.ActedUnderOpenConflicts = append(report.ActedUnderOpenConflicts, exp)
	}
	if err = rows.Err(); err != nil {
		rows.Close()
		return GovernanceConflictWaiverReport{}, err
	}
	rows.Close()
	return s.collectRefusalsAndScopeWaivers(ctx, tx, report)
}

func (s *Store) collectRefusalsAndScopeWaivers(ctx context.Context, tx pgx.Tx, report GovernanceConflictWaiverReport) (GovernanceConflictWaiverReport, error) {
	err := tx.QueryRow(ctx, `SELECT count(*),
		count(*) FILTER (WHERE COALESCE((detail->>'trace_complete')::boolean,false)=true),
		count(*) FILTER (WHERE COALESCE((detail->>'trace_complete')::boolean,false)=false)
		FROM cairn.refusal WHERE detail->'scope'->>'repo'=$1`, report.Repo).Scan(&report.TotalRefusals, &report.CompleteRefusalTraces, &report.IncompleteRefusalTraces)
	if err != nil {
		return GovernanceConflictWaiverReport{}, err
	}
	rows, err := tx.Query(ctx, `SELECT sa.record_id::text,sa.version,sa.previous_version,m.class,sa.grant_id::text,sa.event_id::text
		FROM cairn.scope_authorization sa
		JOIN cairn.memory_record m ON m.record_id=sa.record_id AND m.current_version=sa.version
		JOIN cairn.record_version v ON v.record_id=sa.record_id AND v.version=sa.version
		WHERE v.repo=$1 AND m.lifecycle='active'
		ORDER BY sa.record_id,sa.version DESC`, report.Repo)
	if err != nil {
		return GovernanceConflictWaiverReport{}, err
	}
	defer rows.Close()
	for rows.Next() {
		var w ActiveScopeWaiverSummary
		if err = rows.Scan(&w.RecordID, &w.Version, &w.PreviousVersion, &w.Class, &w.GrantID, &w.EventID); err != nil {
			return GovernanceConflictWaiverReport{}, err
		}
		report.ActiveScopeWaivers = append(report.ActiveScopeWaivers, w)
	}
	if err = rows.Err(); err != nil {
		return GovernanceConflictWaiverReport{}, err
	}
	return report, tx.Commit(ctx)
}

type ImportQuarantineAssessment struct {
	RecordID           string   `json:"record_id"`
	Version            int      `json:"version"`
	Repo               string   `json:"repo"`
	Sensitivity        string   `json:"sensitivity"`
	InspectedRelations int      `json:"inspected_relations"`
	Quarantined        bool     `json:"quarantined"`
	Violations         []string `json:"violations"`
}

func (s *Store) ValidateImportQuarantine(ctx context.Context, repo, recordID string) (ImportQuarantineAssessment, error) {
	if err := s.checkRepo(repo); err != nil {
		return ImportQuarantineAssessment{}, err
	}
	if strings.TrimSpace(repo) == "" || repo == "*" {
		return ImportQuarantineAssessment{}, failure("INVALID_REQUEST", "explicit repo required")
	}
	if err := validID(recordID); err != nil {
		return ImportQuarantineAssessment{}, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return ImportQuarantineAssessment{}, err
	}
	defer tx.Rollback(ctx)
	rec, err := readRecord(ctx, tx, recordID)
	if err != nil {
		return ImportQuarantineAssessment{}, err
	}
	if rec.Scope.Repo != repo {
		return ImportQuarantineAssessment{}, failure("AUTHORITY_DENIED", "record belongs to a different repository")
	}
	return s.evaluateRecordImportQuarantine(ctx, tx, rec)
}

func (s *Store) evaluateRecordImportQuarantine(ctx context.Context, tx pgx.Tx, rec Record) (ImportQuarantineAssessment, error) {
	out := ImportQuarantineAssessment{
		RecordID:           rec.RecordID,
		Version:            rec.Version,
		Repo:               rec.Scope.Repo,
		Sensitivity:        rec.Sensitivity,
		InspectedRelations: len(rec.Relations),
		Violations:         []string{},
	}
	for _, rel := range rec.Relations {
		var targetRepo, targetTask, targetRun, targetSens string
		err := tx.QueryRow(ctx, `SELECT v.repo,v.task_id,v.run_id,m.sensitivity
			FROM cairn.record_version v JOIN cairn.memory_record m USING(record_id)
			WHERE v.record_id=$1 AND v.version=$2`, rel.RecordID, rel.Version).Scan(&targetRepo, &targetTask, &targetRun, &targetSens)
		if err != nil {
			return ImportQuarantineAssessment{}, err
		}
		if (targetSens == "local" || targetSens == "local-only") && rec.Sensitivity == "shareable" {
			out.Violations = append(out.Violations, "sensitivity_laundering:"+rel.RecordID)
		}
		if targetRepo != rec.Scope.Repo || (targetTask != "*" && targetTask != rec.Scope.TaskID) || (targetRun != "*" && targetRun != rec.Scope.RunID) {
			out.Violations = append(out.Violations, "scope_broadened:"+rel.RecordID)
		}
		var targetPins *Applicability
		err = tx.QueryRow(ctx, `SELECT pins FROM cairn.record_applicability WHERE record_id=$1 AND version=$2`, rel.RecordID, rel.Version).Scan(&targetPins)
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return ImportQuarantineAssessment{}, err
		}
		if !applicabilityContains(targetPins, rec.Pins) {
			out.Violations = append(out.Violations, "applicability_broadened:"+rel.RecordID)
		}
	}
	rows, err := tx.Query(ctx, `SELECT e.evidence_id::text,e.sensitivity FROM cairn.evidence_ref r JOIN cairn.evidence e USING(evidence_id) WHERE r.record_id=$1 AND r.version=$2 ORDER BY e.evidence_id`, rec.RecordID, rec.Version)
	if err != nil {
		return ImportQuarantineAssessment{}, err
	}
	defer rows.Close()
	for rows.Next() {
		var evID, evSens string
		if err = rows.Scan(&evID, &evSens); err != nil {
			return ImportQuarantineAssessment{}, err
		}
		if (evSens == "local" || evSens == "local-only") && rec.Sensitivity == "shareable" {
			out.Violations = append(out.Violations, "evidence_sensitivity_laundering:"+evID)
		}
	}
	if err = rows.Err(); err != nil {
		return ImportQuarantineAssessment{}, err
	}
	out.Quarantined = len(out.Violations) > 0
	return out, tx.Commit(ctx)
}

const MinimumUseRetentionFloorDays = 90

type UseRetentionEntry struct {
	ReceiptID               string    `json:"receipt_id"`
	RecordID                string    `json:"record_id"`
	Version                 int       `json:"version"`
	UsedAt                  time.Time `json:"used_at"`
	AgeDays                 int       `json:"age_days"`
	PastRetentionFloor      bool      `json:"past_retention_floor"`
	ProtectedByLiveDecision bool      `json:"protected_by_live_decision"`
	ProtectionReasons       []string  `json:"protection_reasons"`
	EligibleForPrune        bool      `json:"eligible_for_prune"`
}

type UseRetentionReport struct {
	Repo               string              `json:"repo"`
	RetentionFloorDays int                 `json:"retention_floor_days"`
	TotalUseRows       int                 `json:"total_use_rows"`
	ProtectedCount     int                 `json:"protected_count"`
	PruneEligibleCount int                 `json:"prune_eligible_count"`
	Entries            []UseRetentionEntry `json:"entries"`
}

func (s *Store) EvaluateUseRetentionFloor(ctx context.Context, repo string, now time.Time) (UseRetentionReport, error) {
	if err := s.checkRepo(repo); err != nil {
		return UseRetentionReport{}, err
	}
	if strings.TrimSpace(repo) == "" || repo == "*" || now.IsZero() {
		return UseRetentionReport{}, failure("INVALID_REQUEST", "explicit repo and non-zero evaluation timestamp required")
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return UseRetentionReport{}, err
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT u.receipt_id::text,u.record_id::text,u.version,u.used_at,
		(m.lifecycle='active' AND m.class IN ('B','C')) AS active_priv,
		EXISTS (SELECT 1 FROM cairn.conflict_member cm JOIN cairn.conflict_group cg USING(conflict_id)
			WHERE cm.record_id=u.record_id AND cg.resolved_event IS NULL) AS open_conflict,
		EXISTS (SELECT 1 FROM cairn.lesson_proposal lp
			WHERE lp.disposition IN ('open','deferred')
			AND (lp.failure_receipt=u.receipt_id OR lp.recovery_receipt=u.receipt_id OR lp.result_record=u.record_id)) AS pending_proposal
		FROM cairn.record_use u
		JOIN cairn.retrieval_receipt r USING(receipt_id)
		JOIN cairn.memory_record m ON m.record_id=u.record_id
		WHERE r.scope->>'repo'=$1
		ORDER BY u.used_at,u.receipt_id,u.record_id,u.version`, repo)
	if err != nil {
		return UseRetentionReport{}, err
	}
	defer rows.Close()
	cutoff := now.Add(-MinimumUseRetentionFloorDays * 24 * time.Hour)
	report := UseRetentionReport{Repo: repo, RetentionFloorDays: MinimumUseRetentionFloorDays, Entries: []UseRetentionEntry{}}
	for rows.Next() {
		var e UseRetentionEntry
		var activePriv, openConflict, pendingProposal bool
		if err = rows.Scan(&e.ReceiptID, &e.RecordID, &e.Version, &e.UsedAt, &activePriv, &openConflict, &pendingProposal); err != nil {
			return UseRetentionReport{}, err
		}
		e.AgeDays = max(0, int(now.Sub(e.UsedAt)/(24*time.Hour)))
		e.PastRetentionFloor = !e.UsedAt.After(cutoff)
		e.ProtectionReasons = []string{}
		if activePriv {
			e.ProtectionReasons = append(e.ProtectionReasons, "active_privileged_record")
		}
		if openConflict {
			e.ProtectionReasons = append(e.ProtectionReasons, "open_conflict")
		}
		if pendingProposal {
			e.ProtectionReasons = append(e.ProtectionReasons, "pending_proposal")
		}
		e.ProtectedByLiveDecision = len(e.ProtectionReasons) > 0
		if e.ProtectedByLiveDecision {
			report.ProtectedCount++
		}
		e.EligibleForPrune = e.PastRetentionFloor && !e.ProtectedByLiveDecision
		if e.EligibleForPrune {
			report.PruneEligibleCount++
		}
		report.Entries = append(report.Entries, e)
	}
	if err = rows.Err(); err != nil {
		return UseRetentionReport{}, err
	}
	report.TotalUseRows = len(report.Entries)
	return report, tx.Commit(ctx)
}

type RestoreIntegrityStatus struct {
	CurrentGeneration      int64    `json:"current_generation"`
	ExpectedGeneration     int64    `json:"expected_generation"`
	LatestCheckpointID     string   `json:"latest_checkpoint_id"`
	ExpectedCheckpointID   string   `json:"expected_checkpoint_id"`
	RestorePaused          bool     `json:"restore_paused"`
	ActiveRestoreSessionID string   `json:"active_restore_session_id,omitempty"`
	OutOfProcedureRestore  bool     `json:"out_of_procedure_restore"`
	Reasons                []string `json:"reasons"`
}

func (s *Store) DetectOutOfProcedureRestore(ctx context.Context, expectedGeneration int64, expectedLatestCheckpointID string) (RestoreIntegrityStatus, error) {
	if expectedGeneration < 0 {
		return RestoreIntegrityStatus{}, failure("INVALID_REQUEST", "expectedGeneration must be nonnegative")
	}
	if expectedLatestCheckpointID != "" {
		if err := validID(expectedLatestCheckpointID); err != nil {
			return RestoreIntegrityStatus{}, err
		}
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return RestoreIntegrityStatus{}, err
	}
	defer tx.Rollback(ctx)
	status := RestoreIntegrityStatus{
		ExpectedGeneration:   expectedGeneration,
		ExpectedCheckpointID: expectedLatestCheckpointID,
		Reasons:              []string{},
	}
	if err = tx.QueryRow(ctx, `SELECT generation FROM cairn.retrieval_generation WHERE singleton`).Scan(&status.CurrentGeneration); err != nil {
		return RestoreIntegrityStatus{}, err
	}
	if err = tx.QueryRow(ctx, `SELECT paused,COALESCE(session_id::text,'') FROM cairn.restore_admission WHERE singleton`).Scan(&status.RestorePaused, &status.ActiveRestoreSessionID); err != nil {
		return RestoreIntegrityStatus{}, err
	}
	err = tx.QueryRow(ctx, `SELECT checkpoint_id::text FROM cairn.audit_checkpoint ORDER BY created_at DESC,checkpoint_id DESC LIMIT 1`).Scan(&status.LatestCheckpointID)
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return RestoreIntegrityStatus{}, err
	}
	if status.CurrentGeneration < expectedGeneration {
		status.Reasons = append(status.Reasons, "generation_regressed")
	}
	if status.CurrentGeneration > 0 {
		var fenceExists bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.restore_fence WHERE generation=$1)`, status.CurrentGeneration).Scan(&fenceExists); err != nil {
			return RestoreIntegrityStatus{}, err
		}
		if !fenceExists {
			status.Reasons = append(status.Reasons, "unrecorded_restore_fence")
		}
	}
	if expectedLatestCheckpointID != "" && status.LatestCheckpointID != expectedLatestCheckpointID {
		status.Reasons = append(status.Reasons, "checkpoint_lineage_diverged")
	}
	status.OutOfProcedureRestore = len(status.Reasons) > 0
	return status, tx.Commit(ctx)
}
