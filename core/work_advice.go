package core

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/advice"
	"github.com/jackc/pgx/v5"
)

// WorkAdviceJob records an advice generation execution attempt.
type WorkAdviceJob struct {
	JobUUID             uuid.UUID       `json:"job_uuid"`
	BindingID           uuid.UUID       `json:"binding_id"`
	PlaneItemUUID       uuid.UUID       `json:"plane_item_uuid"`
	Caller              string          `json:"caller"`
	ProfileName         string          `json:"profile_name"`
	ModelRoute          string          `json:"model_route"`
	TemplateVersion     int             `json:"template_version"`
	RequestUUID         uuid.UUID       `json:"request_uuid"`
	InputManifest       json.RawMessage `json:"input_manifest,omitempty"`
	InputManifestDigest string          `json:"input_manifest_digest"`
	InputPayloadDigest  string          `json:"input_payload_digest"`
	Status              string          `json:"status"` // pending, running, completed, failed, timed_out, expired, cleanup_unconfirmed, uncertain
	DeadlineSeconds     int             `json:"deadline_seconds"`
	StartedAt           time.Time       `json:"started_at"`
	FinishedAt          *time.Time      `json:"finished_at,omitempty"`
	ErrorCode           string          `json:"error_code,omitempty"`
	ProcessIdentity     json.RawMessage `json:"process_identity,omitempty"`
	ExpiresAt           time.Time       `json:"expires_at"`
	CreatedAt           time.Time       `json:"created_at"`
}

// WorkAdviceProposal represents a reviewed or reviewable proposal from a completed job.
type WorkAdviceProposal struct {
	ProposalUUID      uuid.UUID       `json:"proposal_uuid"`
	JobUUID           uuid.UUID       `json:"job_uuid"`
	BindingID         uuid.UUID       `json:"binding_id"`
	PlaneItemUUID     uuid.UUID       `json:"plane_item_uuid"`
	ReviewVersion     int64           `json:"review_version"`
	Status            string          `json:"status"` // pending, partially_applied, applied, rejected, expired
	RawResult         string          `json:"raw_result"`
	NormalizedPayload json.RawMessage `json:"normalized_payload"`
	ExpiresAt         time.Time       `json:"expires_at"`
	CreatedAt         time.Time       `json:"created_at"`
	ReviewedAt        *time.Time      `json:"reviewed_at,omitempty"`
}

// WorkAdviceAction represents one action proposed within a proposal.
type WorkAdviceAction struct {
	ActionUUID    uuid.UUID       `json:"action_uuid"`
	ProposalUUID  uuid.UUID       `json:"proposal_uuid"`
	ActionIndex   int             `json:"action_index"`
	ActionType    string          `json:"action_type"` // annotate_progress, annotate_blocker, link_artifact
	TargetPayload json.RawMessage `json:"target_payload"`
	Rationale     string          `json:"rationale"`
	Status        string          `json:"status"` // pending, applied, rejected, undone
	CreatedAt     time.Time       `json:"created_at"`
}

// WorkAdviceApplicationReceipt records atomic application of advice actions.
type WorkAdviceApplicationReceipt struct {
	ReceiptUUID           uuid.UUID       `json:"receipt_uuid"`
	RequestUUID           uuid.UUID       `json:"request_uuid"`
	ProposalUUID          uuid.UUID       `json:"proposal_uuid"`
	AppliedActionUUIDs    json.RawMessage `json:"applied_action_uuids"`
	AppliedByChannel      string          `json:"applied_by_channel"`
	OutputRevisions       json.RawMessage `json:"output_revisions"`
	AppliedAt             time.Time       `json:"applied_at"`
	Undone                bool            `json:"undone"`
	UndoneAt              *time.Time      `json:"undone_at,omitempty"`
	UndoneByChannel       *string         `json:"undone_by_channel,omitempty"`
	CompensatingRevisions json.RawMessage `json:"compensating_revisions"`
}

// SourceRefSpec identifies an explicit source record and version to be included in advice snapshot.
type SourceRefSpec struct {
	RecordID uuid.UUID `json:"record_id"`
	Version  int       `json:"version"`
}

// AdviceInputSpec provides the caller-selected spec for Store to build a canonical snapshot from live DB rows.
type AdviceInputSpec struct {
	BindingID            uuid.UUID       `json:"binding_id"`
	PlaneItemUUID        uuid.UUID       `json:"plane_item_uuid"`
	SelectedSources      []SourceRefSpec `json:"selected_sources,omitempty"`
	CandidateRevisionIDs []uuid.UUID     `json:"candidate_revision_ids,omitempty"`
}

func (s *Store) requireOperator() error {
	if !s.channel.Operator {
		return failure("AUTHORITY_DENIED", "work advice requires operator channel")
	}
	return nil
}

// checkAdviceFeatureTx verifies that work-advice/1 is active in the current transaction.
func (s *Store) checkAdviceFeatureTx(ctx context.Context, tx pgx.Tx) error {
	var status string
	err := tx.QueryRow(ctx, `SELECT status FROM cairn.work_feature WHERE feature_name = 'work-advice/1'`).Scan(&status)
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("FEATURE_DISABLED", "work-advice/1 feature is disabled")
	}
	if err != nil {
		return err
	}
	if status == "retired" {
		return failure("FEATURE_RETIRED", "work-advice/1 feature is retired")
	}
	if status != "active" {
		return failure("FEATURE_DISABLED", "work-advice/1 feature is disabled")
	}
	return nil
}

// CheckAdviceFeature checks whether work-advice/1 is active.
func (s *Store) CheckAdviceFeature(ctx context.Context) (bool, error) {
	tx, err := s.begin(ctx)
	if err != nil {
		return false, err
	}
	defer tx.Rollback(ctx)
	var status string
	err = tx.QueryRow(ctx, `SELECT status FROM cairn.work_feature WHERE feature_name = 'work-advice/1'`).Scan(&status)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return status == "active", nil
}

// SetAdviceFeature updates the status of work-advice/1.
func (s *Store) SetAdviceFeature(ctx context.Context, status string) error {
	if err := s.requireOperator(); err != nil {
		return err
	}
	switch status {
	case "active", "disabled", "retired":
		// valid
	default:
		return failure("INVALID_REQUEST", "invalid feature status")
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	var currentStatus string
	err = tx.QueryRow(ctx, `SELECT status FROM cairn.work_feature WHERE feature_name = 'work-advice/1' FOR UPDATE`).Scan(&currentStatus)
	if err == nil && currentStatus == "retired" && status != "retired" {
		return failure("FEATURE_RETIRED", "work-advice/1 feature is retired and cannot be reactivated")
	}

	_, err = tx.Exec(ctx, `
		INSERT INTO cairn.work_feature (feature_name, status, updated_at)
		VALUES ('work-advice/1', $1, clock_timestamp())
		ON CONFLICT (feature_name) DO UPDATE
		SET status = EXCLUDED.status,
		    updated_at = clock_timestamp(),
		    retired_at = CASE WHEN EXCLUDED.status = 'retired' THEN clock_timestamp() ELSE cairn.work_feature.retired_at END
	`, status)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}

// CreateAdviceJob registers a new advice generation attempt in PostgreSQL, enforcing launch intent uniqueness.
func (s *Store) CreateAdviceJob(ctx context.Context, job *WorkAdviceJob) error {
	if err := s.requireOperator(); err != nil {
		return err
	}
	if job == nil {
		return failure("INVALID_REQUEST", "job is nil")
	}
	if job.BindingID == uuid.Nil || job.PlaneItemUUID == uuid.Nil {
		return failure("INVALID_REQUEST", "job missing binding_id or plane_item_uuid")
	}
	if job.RequestUUID == uuid.Nil {
		return failure("INVALID_REQUEST", "job missing request_uuid")
	}
	if job.JobUUID == uuid.Nil {
		job.JobUUID = uuid.New()
	}
	if job.Caller == "" {
		job.Caller = s.channel.Principal
	}
	if job.Status == "" {
		job.Status = "pending"
	}
	if job.DeadlineSeconds <= 0 {
		job.DeadlineSeconds = int(advice.DefaultDeadline / time.Second)
	} else if job.DeadlineSeconds < int(advice.MinDeadline/time.Second) {
		job.DeadlineSeconds = int(advice.MinDeadline / time.Second)
	} else if job.DeadlineSeconds > int(advice.MaxDeadline/time.Second) {
		job.DeadlineSeconds = int(advice.MaxDeadline / time.Second)
	}
	if job.ExpiresAt.IsZero() {
		job.ExpiresAt = time.Now().Add(1 * time.Hour)
	}
	if len(job.ProcessIdentity) == 0 {
		job.ProcessIdentity = json.RawMessage("{}")
	}
	if len(job.InputManifest) == 0 {
		job.InputManifest = json.RawMessage("{}")
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	if err := s.lockAndCheckFeatureAdmissionTx(ctx, tx, "work-advice/1"); err != nil {
		return err
	}

	// Lock work item FOR UPDATE
	var isAbsent bool
	err = tx.QueryRow(ctx, `
		SELECT is_absent
		FROM cairn.work_item
		WHERE binding_id = $1 AND plane_item_uuid = $2
		FOR UPDATE
	`, job.BindingID, job.PlaneItemUUID).Scan(&isAbsent)
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("NOT_FOUND", fmt.Sprintf("work item %s not found in project %s", job.PlaneItemUUID, job.BindingID))
	}
	if err != nil {
		return err
	}
	if isAbsent {
		return failure("ITEM_ABSENT", "cannot create advice job for absent work item")
	}

	// Check (caller, request_uuid) FOR UPDATE FIRST
	var existingJobUUID, existingBindingID, existingPlaneItemUUID uuid.UUID
	var existingProfileName, existingModelRoute, existingDigest, existingStatus string
	var existingTemplateVersion, existingDeadlineSeconds int
	var existingErrorCode *string
	var existingStartedAt, existingFinishedAt *time.Time
	var existingExpiresAt, existingCreatedAt time.Time
	var existingProcessIdentity, existingManifest []byte
	var existingManifestDigest string

	err = tx.QueryRow(ctx, `
		SELECT job_uuid, binding_id, plane_item_uuid, profile_name, model_route,
		       template_version, input_manifest, input_manifest_digest, input_payload_digest,
		       status, deadline_seconds, started_at, finished_at, error_code,
		       process_identity, expires_at, created_at
		FROM cairn.work_advice_job
		WHERE caller = $1 AND request_uuid = $2
		FOR UPDATE
	`, job.Caller, job.RequestUUID).Scan(
		&existingJobUUID, &existingBindingID, &existingPlaneItemUUID, &existingProfileName, &existingModelRoute,
		&existingTemplateVersion, &existingManifest, &existingManifestDigest, &existingDigest,
		&existingStatus, &existingDeadlineSeconds, &existingStartedAt, &existingFinishedAt, &existingErrorCode,
		&existingProcessIdentity, &existingExpiresAt, &existingCreatedAt,
	)
	if err == nil {
		if existingDigest != job.InputPayloadDigest ||
			existingBindingID != job.BindingID ||
			existingPlaneItemUUID != job.PlaneItemUUID ||
			existingProfileName != job.ProfileName ||
			existingTemplateVersion != job.TemplateVersion {
			return failure("IDEMPOTENCY_CONFLICT", "request_uuid reused with different job parameters or input payload digest")
		}
		job.JobUUID = existingJobUUID
		job.Status = existingStatus
		if existingErrorCode != nil {
			job.ErrorCode = *existingErrorCode
		}
		if existingStartedAt != nil {
			job.StartedAt = *existingStartedAt
		}
		job.FinishedAt = existingFinishedAt
		job.DeadlineSeconds = existingDeadlineSeconds
		job.ExpiresAt = existingExpiresAt
		job.ProcessIdentity = existingProcessIdentity
		job.InputManifest = existingManifest
		job.InputManifestDigest = existingManifestDigest
		job.CreatedAt = existingCreatedAt
		return nil
	} else if !errors.Is(err, pgx.ErrNoRows) {
		return err
	}

	// Only for new (caller, request_uuid):
	// Enforce active-per-item (0):
	var activeCount int
	err = tx.QueryRow(ctx, `
		SELECT COUNT(*)
		FROM cairn.work_advice_job
		WHERE binding_id = $1 AND plane_item_uuid = $2
		  AND status IN ('pending', 'running', 'cleanup_unconfirmed', 'uncertain')
	`, job.BindingID, job.PlaneItemUUID).Scan(&activeCount)
	if err != nil {
		return err
	}
	if activeCount > 0 {
		return failure("INFLIGHT_LIMIT", "an active advice job is already in progress or uncertain for this work item")
	}

	// Enforce host inflight (<=2):
	var hostInflight int
	err = tx.QueryRow(ctx, `
		SELECT COUNT(*)
		FROM cairn.work_advice_job
		WHERE status IN ('pending', 'running')
	`).Scan(&hostInflight)
	if err != nil {
		return err
	}
	if hostInflight >= 2 {
		return failure("INFLIGHT_LIMIT", "maximum host-wide concurrent advice jobs limit reached (max 2)")
	}

	// Enforce profile hourly (<=10):
	var profileHourly int
	err = tx.QueryRow(ctx, `
		SELECT COUNT(*)
		FROM cairn.work_advice_job
		WHERE profile_name = $1
		  AND created_at >= clock_timestamp() - interval '1 hour'
	`, job.ProfileName).Scan(&profileHourly)
	if err != nil {
		return err
	}
	if profileHourly >= 10 {
		return failure("QUOTA_EXHAUSTED", fmt.Sprintf("hourly advice generation limit reached for profile %q (max 10/hour)", job.ProfileName))
	}

	_, err = tx.Exec(ctx, `
		INSERT INTO cairn.work_advice_job (
			job_uuid, binding_id, plane_item_uuid, caller, profile_name, model_route,
			template_version, request_uuid, input_manifest, input_manifest_digest, input_payload_digest,
			status, deadline_seconds, started_at, error_code, process_identity, expires_at, created_at
		) VALUES (
			$1, $2, $3, $4, $5, $6,
			$7, $8, $9, $10, $11,
			$12, $13, clock_timestamp(), $14, $15, $16, clock_timestamp()
		)
	`, job.JobUUID, job.BindingID, job.PlaneItemUUID, job.Caller, job.ProfileName, job.ModelRoute,
		job.TemplateVersion, job.RequestUUID, job.InputManifest, job.InputManifestDigest, job.InputPayloadDigest,
		job.Status, job.DeadlineSeconds, job.ErrorCode, job.ProcessIdentity, job.ExpiresAt)
	if err != nil {
		return err
	}

	return tx.Commit(ctx)
}

// GetAdviceJob fetches an advice job by UUID through transaction guards.
func (s *Store) GetAdviceJob(ctx context.Context, jobUUID uuid.UUID) (*WorkAdviceJob, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	var job WorkAdviceJob
	err = tx.QueryRow(ctx, `
		SELECT job_uuid, binding_id, plane_item_uuid, caller, profile_name, model_route,
		       template_version, request_uuid, input_manifest, input_manifest_digest, input_payload_digest,
		       status, deadline_seconds, started_at, finished_at, error_code, process_identity, expires_at, created_at
		FROM cairn.work_advice_job
		WHERE job_uuid = $1
	`, jobUUID).Scan(
		&job.JobUUID, &job.BindingID, &job.PlaneItemUUID, &job.Caller, &job.ProfileName, &job.ModelRoute,
		&job.TemplateVersion, &job.RequestUUID, &job.InputManifest, &job.InputManifestDigest, &job.InputPayloadDigest,
		&job.Status, &job.DeadlineSeconds, &job.StartedAt, &job.FinishedAt, &job.ErrorCode, &job.ProcessIdentity, &job.ExpiresAt, &job.CreatedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, failure("NOT_FOUND", "advice job not found")
	}
	if err != nil {
		return nil, err
	}
	return &job, tx.Commit(ctx)
}

// FinishAdviceJob updates the status of an advice job upon completion or failure.
func (s *Store) FinishAdviceJob(ctx context.Context, jobUUID uuid.UUID, status string, errorCode string) error {
	return s.FinishAdviceJobWithProcessIdentity(ctx, jobUUID, status, errorCode, nil)
}

// FinishAdviceJobWithProcessIdentity updates status, error_code, and process_identity of an advice job.
func (s *Store) FinishAdviceJobWithProcessIdentity(ctx context.Context, jobUUID uuid.UUID, status string, errorCode string, procIdent json.RawMessage) error {
	if err := s.requireOperator(); err != nil {
		return err
	}
	if len(procIdent) == 0 {
		procIdent = json.RawMessage("{}")
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	var currentStatus string
	var expiresAt time.Time
	err = tx.QueryRow(ctx, `
		SELECT status, expires_at
		FROM cairn.work_advice_job
		WHERE job_uuid = $1
		FOR UPDATE
	`, jobUUID).Scan(&currentStatus, &expiresAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("NOT_FOUND", "advice job not found (possibly purged)")
	}
	if err != nil {
		return err
	}
	if currentStatus != "running" && currentStatus != "pending" {
		return nil
	}
	if time.Now().After(expiresAt) && status == "completed" {
		status = "expired"
		errorCode = "PROPOSAL_EXPIRED"
	}

	tag, err := tx.Exec(ctx, `
		UPDATE cairn.work_advice_job
		SET status = $2,
		    error_code = $3,
		    process_identity = CASE WHEN $4::jsonb = '{}'::jsonb THEN cairn.work_advice_job.process_identity ELSE $4::jsonb END,
		    finished_at = clock_timestamp()
		WHERE job_uuid = $1
	`, jobUUID, status, errorCode, procIdent)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return failure("NOT_FOUND", "advice job not found")
	}

	return tx.Commit(ctx)
}

func (s *Store) verifyAdviceManifestTx(ctx context.Context, tx pgx.Tx, bindingID, planeItemUUID uuid.UUID, collection string, manifest *WorkAdviceInputManifest) error {
	if manifest == nil {
		return failure("INVALID_REQUEST", "missing input manifest for revalidation")
	}

	// 1. Re-verify restore generation
	var currentRestoreGen int64
	err := tx.QueryRow(ctx, `SELECT generation FROM cairn.retrieval_generation WHERE singleton = true`).Scan(&currentRestoreGen)
	if err != nil {
		return err
	}
	if currentRestoreGen != manifest.RestoreGeneration {
		return failure("RESTORE_FENCE_TRIGGERED", fmt.Sprintf("restore generation moved from %d to %d", manifest.RestoreGeneration, currentRestoreGen))
	}

	// 2. Re-verify metadata exclusions and config revision from work_project
	var currentExclRev int64
	var currentConfigRev string
	err = tx.QueryRow(ctx, `SELECT metadata_exclusion_revision, config_revision FROM cairn.work_project WHERE binding_id = $1`, bindingID).Scan(&currentExclRev, &currentConfigRev)
	if err != nil {
		return err
	}
	if currentExclRev != manifest.MetadataExclusionRevision {
		return failure("EXCLUSION_CONFLICT", fmt.Sprintf("metadata exclusions revision moved from %d to %d", manifest.MetadataExclusionRevision, currentExclRev))
	}
	if manifest.ConfigRevision != "" && currentConfigRev != manifest.ConfigRevision {
		return failure("CONFIG_CONFLICT", fmt.Sprintf("work project config revision moved from %s to %s", manifest.ConfigRevision, currentConfigRev))
	}

	// 3. Re-verify work item state, cache generation, and refresh observation
	var cacheGen int64
	var lastRefreshedAt *time.Time
	var isAbsent bool
	err = tx.QueryRow(ctx, `
		SELECT cache_generation, last_refreshed_at, is_absent
		FROM cairn.work_item
		WHERE binding_id = $1 AND plane_item_uuid = $2
		FOR SHARE
	`, bindingID, planeItemUUID).Scan(&cacheGen, &lastRefreshedAt, &isAbsent)
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("NOT_FOUND", "work item not found during revalidation")
	}
	if err != nil {
		return err
	}
	if isAbsent {
		return failure("ITEM_ABSENT", "work item was marked absent")
	}
	if cacheGen != manifest.CacheGeneration {
		return failure("STALE_OBSERVATION", fmt.Sprintf("work item cache generation moved from %d to %d", manifest.CacheGeneration, cacheGen))
	}
	if lastRefreshedAt == nil || time.Since(*lastRefreshedAt) > 2*time.Minute {
		return failure("STALE_OBSERVATION", "work item Plane refresh observation is expired (older than 2 minutes)")
	}

	// 4. Re-verify each selected source
	for _, sRef := range manifest.Sources {
		rec, err := readRecord(ctx, tx, sRef.RecordID.String())
		if errors.Is(err, pgx.ErrNoRows) || Code(err) == "NOT_FOUND" {
			return failure("SOURCE_INELIGIBLE", fmt.Sprintf("source record %s no longer found", sRef.RecordID))
		}
		if err != nil {
			return err
		}
		if rec.Lifecycle != "active" || rec.Version != sRef.Version || rec.Scope.Repo != collection || rec.Sensitivity != "shareable" {
			return failure("SOURCE_INELIGIBLE", fmt.Sprintf("source record %s eligibility changed", sRef.RecordID))
		}
		_, reason, err := eligible(ctx, tx, rec, "context")
		if err != nil {
			return err
		}
		if reason != "" {
			return failure("SOURCE_INELIGIBLE", fmt.Sprintf("source record %s is ineligible: %s", sRef.RecordID, reason))
		}
		var payloadDeletedBy *string
		err = tx.QueryRow(ctx, `SELECT payload_deleted_by FROM cairn.record_version WHERE record_id = $1 AND version = $2`, sRef.RecordID, sRef.Version).Scan(&payloadDeletedBy)
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return err
		}
		if payloadDeletedBy != nil {
			return failure("SOURCE_INELIGIBLE", fmt.Sprintf("source record %s payload was deleted", sRef.RecordID))
		}
		bodySum := sha256.Sum256([]byte(rec.Body))
		bodyHex := fmt.Sprintf("%x", bodySum[:])
		if bodyHex != sRef.BodySHA256 {
			return failure("SOURCE_INELIGIBLE", fmt.Sprintf("source record %s body sha256 changed", sRef.RecordID))
		}
	}

	// 5. Re-verify candidate artifact revisions
	for _, revID := range manifest.CandidateRevisionIDs {
		var rCollection string
		var artID uuid.UUID
		err := tx.QueryRow(ctx, `
			SELECT r.collection, a.artifact_id
			FROM cairn.work_artifact_revision r
			JOIN cairn.work_artifact a ON a.artifact_id = r.artifact_id
			WHERE r.revision_id = $1
		`, revID).Scan(&rCollection, &artID)
		if errors.Is(err, pgx.ErrNoRows) {
			return failure("SOURCE_INELIGIBLE", fmt.Sprintf("candidate artifact revision %s no longer found", revID))
		}
		if err != nil {
			return err
		}
		if rCollection != collection {
			return failure("SOURCE_INELIGIBLE", fmt.Sprintf("candidate artifact revision %s collection mismatch", revID))
		}
		var fenced bool
		err = tx.QueryRow(ctx, `
			SELECT true FROM cairn.work_artifact_purge_fence WHERE collection = $1 AND artifact_id = $2
		`, collection, artID).Scan(&fenced)
		if err == nil && fenced {
			return failure("SOURCE_INELIGIBLE", fmt.Sprintf("candidate artifact revision %s is under purge fence", revID))
		}
	}

	return nil
}

// RecordAdviceProposal records a proposal and its proposed actions atomically.
func (s *Store) RecordAdviceProposal(ctx context.Context, proposal *WorkAdviceProposal, actions []*WorkAdviceAction) error {
	if err := s.requireOperator(); err != nil {
		return err
	}
	if proposal == nil {
		return failure("INVALID_REQUEST", "proposal is nil")
	}
	if proposal.ProposalUUID == uuid.Nil {
		proposal.ProposalUUID = uuid.New()
	}
	if proposal.ReviewVersion <= 0 {
		proposal.ReviewVersion = 1
	}
	if proposal.Status == "" {
		proposal.Status = "pending"
	}
	if proposal.ExpiresAt.IsZero() {
		proposal.ExpiresAt = time.Now().Add(1 * time.Hour)
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	var manifestRaw []byte
	var collection string
	err = tx.QueryRow(ctx, `
		SELECT j.input_manifest, p.collection
		FROM cairn.work_advice_job j
		JOIN cairn.work_project p ON p.binding_id = j.binding_id
		WHERE j.job_uuid = $1
	`, proposal.JobUUID).Scan(&manifestRaw, &collection)
	if err == nil && len(manifestRaw) > 0 {
		var manifest WorkAdviceInputManifest
		if err := json.Unmarshal(manifestRaw, &manifest); err == nil && len(manifest.AllowedFields) > 0 {
			if err := s.verifyAdviceManifestTx(ctx, tx, proposal.BindingID, proposal.PlaneItemUUID, collection, &manifest); err != nil {
				return err
			}
		}
	}

	_, err = tx.Exec(ctx, `
		INSERT INTO cairn.work_advice_proposal (
			proposal_uuid, job_uuid, binding_id, plane_item_uuid, review_version,
			status, raw_result, normalized_payload, expires_at, created_at
		) VALUES (
			$1, $2, $3, $4, $5,
			$6, $7, $8, $9, clock_timestamp()
		)
	`, proposal.ProposalUUID, proposal.JobUUID, proposal.BindingID, proposal.PlaneItemUUID,
		proposal.ReviewVersion, proposal.Status, proposal.RawResult, proposal.NormalizedPayload, proposal.ExpiresAt)
	if err != nil {
		return err
	}

	for _, act := range actions {
		if act.ActionUUID == uuid.Nil {
			act.ActionUUID = uuid.New()
		}
		act.ProposalUUID = proposal.ProposalUUID
		if act.Status == "" {
			act.Status = "pending"
		}
		_, err = tx.Exec(ctx, `
			INSERT INTO cairn.work_advice_action (
				action_uuid, proposal_uuid, action_index, action_type,
				target_payload, rationale, status, created_at
			) VALUES (
				$1, $2, $3, $4,
				$5, $6, $7, clock_timestamp()
			)
		`, act.ActionUUID, act.ProposalUUID, act.ActionIndex, act.ActionType,
			act.TargetPayload, act.Rationale, act.Status)
		if err != nil {
			return err
		}
	}

	return tx.Commit(ctx)
}

// GetAdviceProposal fetches a proposal and all its actions ordered by action_index.
func (s *Store) GetAdviceProposal(ctx context.Context, proposalUUID uuid.UUID) (*WorkAdviceProposal, []*WorkAdviceAction, error) {
	if err := s.requireOperator(); err != nil {
		return nil, nil, err
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return nil, nil, err
	}
	defer tx.Rollback(ctx)

	var prop WorkAdviceProposal
	err = tx.QueryRow(ctx, `
		SELECT proposal_uuid, job_uuid, binding_id, plane_item_uuid, review_version,
		       status, raw_result, normalized_payload, expires_at, created_at, reviewed_at
		FROM cairn.work_advice_proposal
		WHERE proposal_uuid = $1
	`, proposalUUID).Scan(
		&prop.ProposalUUID, &prop.JobUUID, &prop.BindingID, &prop.PlaneItemUUID, &prop.ReviewVersion,
		&prop.Status, &prop.RawResult, &prop.NormalizedPayload, &prop.ExpiresAt, &prop.CreatedAt, &prop.ReviewedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil, failure("NOT_FOUND", "advice proposal not found")
	}
	if err != nil {
		return nil, nil, err
	}

	rows, err := tx.Query(ctx, `
		SELECT action_uuid, proposal_uuid, action_index, action_type,
		       target_payload, rationale, status, created_at
		FROM cairn.work_advice_action
		WHERE proposal_uuid = $1
		ORDER BY action_index ASC
	`, proposalUUID)
	if err != nil {
		return nil, nil, err
	}
	defer rows.Close()

	var actions []*WorkAdviceAction
	for rows.Next() {
		var act WorkAdviceAction
		if err := rows.Scan(
			&act.ActionUUID, &act.ProposalUUID, &act.ActionIndex, &act.ActionType,
			&act.TargetPayload, &act.Rationale, &act.Status, &act.CreatedAt,
		); err != nil {
			return nil, nil, err
		}
		actions = append(actions, &act)
	}
	if err := rows.Err(); err != nil {
		return nil, nil, err
	}

	return &prop, actions, tx.Commit(ctx)
}

// ListAdviceProposals queries all proposals recorded for a given work item.
func (s *Store) ListAdviceProposals(ctx context.Context, bindingID uuid.UUID, planeItemUUID uuid.UUID) ([]*WorkAdviceProposal, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	rows, err := tx.Query(ctx, `
		SELECT proposal_uuid, job_uuid, binding_id, plane_item_uuid, review_version,
		       status, raw_result, normalized_payload, expires_at, created_at, reviewed_at
		FROM cairn.work_advice_proposal
		WHERE binding_id = $1 AND plane_item_uuid = $2
		ORDER BY created_at DESC
	`, bindingID, planeItemUUID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var proposals []*WorkAdviceProposal
	for rows.Next() {
		var prop WorkAdviceProposal
		if err := rows.Scan(
			&prop.ProposalUUID, &prop.JobUUID, &prop.BindingID, &prop.PlaneItemUUID, &prop.ReviewVersion,
			&prop.Status, &prop.RawResult, &prop.NormalizedPayload, &prop.ExpiresAt, &prop.CreatedAt, &prop.ReviewedAt,
		); err != nil {
			return nil, err
		}
		proposals = append(proposals, &prop)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return proposals, tx.Commit(ctx)
}

// RejectAdviceProposal rejects a proposal with optimistic concurrency check.
func (s *Store) RejectAdviceProposal(ctx context.Context, proposalUUID uuid.UUID, expectedReviewVersion int64) error {
	if err := s.requireOperator(); err != nil {
		return err
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	var currentStatus string
	var currentRev int64
	err = tx.QueryRow(ctx, `
		SELECT status, review_version
		FROM cairn.work_advice_proposal
		WHERE proposal_uuid = $1
		FOR UPDATE
	`, proposalUUID).Scan(&currentStatus, &currentRev)
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("NOT_FOUND", "advice proposal not found")
	}
	if err != nil {
		return err
	}

	if currentStatus != "pending" && currentStatus != "partially_applied" {
		return failure("INVALID_STATE", fmt.Sprintf("proposal is in state %q, cannot reject", currentStatus))
	}
	if currentRev != expectedReviewVersion {
		return failure("VERSION_CONFLICT", "proposal review version conflict")
	}

	_, err = tx.Exec(ctx, `
		UPDATE cairn.work_advice_proposal
		SET status = 'rejected',
		    review_version = review_version + 1,
		    reviewed_at = clock_timestamp()
		WHERE proposal_uuid = $1
	`, proposalUUID)
	if err != nil {
		return err
	}

	// Reject any remaining pending actions
	_, err = tx.Exec(ctx, `
		UPDATE cairn.work_advice_action
		SET status = 'rejected'
		WHERE proposal_uuid = $1 AND status = 'pending'
	`, proposalUUID)
	if err != nil {
		return err
	}

	return tx.Commit(ctx)
}

// WorkAdviceSourceDigest records exact source identity and body digest in the advice input manifest.
type WorkAdviceSourceDigest struct {
	RecordID   uuid.UUID `json:"record_id"`
	Version    int       `json:"version"`
	BodySHA256 string    `json:"body_sha256"`
}

// WorkAdviceInputManifest records immutable provenance facts captured at advice launch.
type WorkAdviceInputManifest struct {
	RestoreGeneration         int64                    `json:"restore_generation"`
	MetadataExclusionRevision int64                    `json:"metadata_exclusion_revision"`
	ConfigRevision            string                   `json:"config_revision,omitempty"`
	CacheGeneration           int64                    `json:"cache_generation"`
	LastRefreshedAt           time.Time                `json:"last_refreshed_at"`
	AllowedFields             []string                 `json:"allowed_fields"`
	Sources                   []WorkAdviceSourceDigest `json:"sources"`
	CandidateRevisionIDs      []uuid.UUID              `json:"candidate_revision_ids"`
}

// SetWorkAdvicePolicy configures the allowed advice field policy for a work project.
func (s *Store) SetWorkAdvicePolicy(ctx context.Context, bindingID uuid.UUID, allowedFields []string) error {
	if err := s.requireOperator(); err != nil {
		return err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	validFields := map[string]bool{
		"identifier": true,
		"title":      true,
		"state":      true,
		"blockers":   true,
		"artifacts":  true,
		"sources":    true,
	}
	for _, f := range allowedFields {
		if !validFields[f] {
			return failure("INVALID_REQUEST", fmt.Sprintf("unrecognized advice allowed field %q", f))
		}
	}

	fieldsJSON, _ := json.Marshal(allowedFields)
	_, err = tx.Exec(ctx, `
		INSERT INTO cairn.work_advice_policy (binding_id, allowed_fields, updated_at)
		VALUES ($1, $2, clock_timestamp())
		ON CONFLICT (binding_id) DO UPDATE SET
			allowed_fields = EXCLUDED.allowed_fields,
			updated_at = clock_timestamp()
	`, bindingID, fieldsJSON)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}

// GetWorkAdvicePolicy retrieves the allowed advice field policy for a work project.
func (s *Store) GetWorkAdvicePolicy(ctx context.Context, bindingID uuid.UUID) ([]string, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	var fieldsJSON []byte
	err = tx.QueryRow(ctx, `
		SELECT allowed_fields
		FROM cairn.work_advice_policy
		WHERE binding_id = $1
	`, bindingID).Scan(&fieldsJSON)
	if errors.Is(err, pgx.ErrNoRows) {
		return []string{}, nil
	}
	if err != nil {
		return nil, err
	}
	var fields []string
	if err := json.Unmarshal(fieldsJSON, &fields); err != nil {
		return nil, err
	}
	return fields, nil
}

// BuildWorkAdviceSnapshot constructs an immutable exact snapshot from live DB rows.
func (s *Store) BuildWorkAdviceSnapshot(ctx context.Context, spec AdviceInputSpec) (*advice.Snapshot, *WorkAdviceInputManifest, error) {
	if err := s.requireOperator(); err != nil {
		return nil, nil, err
	}
	if spec.BindingID == uuid.Nil || spec.PlaneItemUUID == uuid.Nil {
		return nil, nil, failure("INVALID_REQUEST", "binding_id and plane_item_uuid required")
	}
	if len(spec.CandidateRevisionIDs) > 20 {
		return nil, nil, failure("INPUT_TOO_LARGE", "candidate artifact revisions limit exceeded (max 20)")
	}
	if len(spec.SelectedSources) > 32 {
		return nil, nil, failure("INPUT_TOO_LARGE", "selected sources limit exceeded (max 32)")
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return nil, nil, err
	}
	defer tx.Rollback(ctx)

	if err := s.checkAdviceFeatureTx(ctx, tx); err != nil {
		return nil, nil, err
	}

	// 1. Fetch project collection, status, auth_rejected, metadata_exclusion_revision, and config_revision
	var collection, projectStatus, configRevision string
	var authRejected bool
	var metadataExclRev int64
	err = tx.QueryRow(ctx, `
		SELECT collection, status, auth_rejected, metadata_exclusion_revision, config_revision
		FROM cairn.work_project
		WHERE binding_id = $1
	`, spec.BindingID).Scan(&collection, &projectStatus, &authRejected, &metadataExclRev, &configRevision)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil, failure("NOT_FOUND", fmt.Sprintf("work project %s not found", spec.BindingID))
	}
	if err != nil {
		return nil, nil, err
	}
	if projectStatus != "active" {
		return nil, nil, failure("INVALID_STATE", fmt.Sprintf("work project status is %q, expected active", projectStatus))
	}
	if authRejected {
		return nil, nil, failure("SOURCE_INELIGIBLE", "work project Plane authentication is rejected")
	}
	if err := s.checkRepo(collection); err != nil {
		return nil, nil, err
	}

	// 2. Fetch project advice field policy (local by default)
	var policyFieldsRaw []byte
	err = tx.QueryRow(ctx, `SELECT allowed_fields FROM cairn.work_advice_policy WHERE binding_id = $1`, spec.BindingID).Scan(&policyFieldsRaw)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil, failure("POLICY_UNENFORCEABLE", "work advice policy not configured (metadata is local by default)")
	}
	if err != nil {
		return nil, nil, err
	}
	var allowedFields []string
	if err := json.Unmarshal(policyFieldsRaw, &allowedFields); err != nil {
		return nil, nil, err
	}
	if len(allowedFields) == 0 {
		return nil, nil, failure("POLICY_UNENFORCEABLE", "work advice policy has empty allowed_fields (metadata is local by default)")
	}
	allowedMap := make(map[string]bool)
	for _, f := range allowedFields {
		allowedMap[f] = true
	}

	// 3. Fetch work item and verify freshness (<= 2 min)
	var itemID uuid.UUID
	var identifier, title string
	var stateName, stateGroup *string
	var isAbsent bool
	var lastRefreshedAt *time.Time
	var cacheGen int64
	err = tx.QueryRow(ctx, `
		SELECT item_id, identifier, title, state_name, state_group, is_absent, last_refreshed_at, cache_generation
		FROM cairn.work_item
		WHERE binding_id = $1 AND plane_item_uuid = $2
	`, spec.BindingID, spec.PlaneItemUUID).Scan(&itemID, &identifier, &title, &stateName, &stateGroup, &isAbsent, &lastRefreshedAt, &cacheGen)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil, failure("NOT_FOUND", fmt.Sprintf("work item %s not found in project %s", spec.PlaneItemUUID, spec.BindingID))
	}
	if err != nil {
		return nil, nil, err
	}
	if isAbsent {
		return nil, nil, failure("ITEM_ABSENT", "work item is marked absent")
	}
	if lastRefreshedAt == nil {
		return nil, nil, failure("SOURCE_INELIGIBLE", "work item has no Plane refresh observation")
	}
	if time.Since(*lastRefreshedAt) > 2*time.Minute {
		return nil, nil, failure("SOURCE_INELIGIBLE", "work item refresh observation is older than 2 minutes")
	}

	var restoreGen int64
	err = tx.QueryRow(ctx, `SELECT generation FROM cairn.retrieval_generation WHERE singleton = true`).Scan(&restoreGen)
	if err != nil {
		return nil, nil, err
	}

	snapIdentifier := ""
	if allowedMap["identifier"] {
		snapIdentifier = identifier
	}
	snapTitle := ""
	if allowedMap["title"] {
		snapTitle = title
	}
	snapCurrentState := ""
	if allowedMap["state"] && stateName != nil {
		snapCurrentState = *stateName
	}

	// 3. Fetch active blockers
	var existingBlockers []advice.ExistingBlocker
	if allowedMap["blockers"] {
		rows, err := tx.Query(ctx, `
			SELECT annotation_id, revision, body
			FROM cairn.work_annotation
			WHERE binding_id = $1 AND plane_item_uuid = $2 AND kind = 'blocker' AND state = 'open'
			ORDER BY created_at ASC
		`, spec.BindingID, spec.PlaneItemUUID)
		if err != nil {
			return nil, nil, err
		}
		defer rows.Close()

		for rows.Next() {
			var eb advice.ExistingBlocker
			var annID uuid.UUID
			if err := rows.Scan(&annID, &eb.Revision, &eb.Text); err != nil {
				return nil, nil, err
			}
			eb.BlockerID = annID.String()
			existingBlockers = append(existingBlockers, eb)
		}
		if err := rows.Err(); err != nil {
			return nil, nil, err
		}
	}

	// 4. Candidate artifacts: scope to explicit/active revisions in exact collection with no purge fence
	var candidateArtifacts []advice.CandidateArtifact
	if len(spec.CandidateRevisionIDs) > 0 {
		if !allowedMap["artifacts"] {
			return nil, nil, failure("SOURCE_INELIGIBLE", "candidate artifacts not permitted by advice policy")
		}
		for _, revID := range spec.CandidateRevisionIDs {
			var ca advice.CandidateArtifact
			var rCollection string
			var artID uuid.UUID
			err = tx.QueryRow(ctx, `
				SELECT r.revision_id, r.collection, a.artifact_id, a.kind, COALESCE(a.report_locator, COALESCE(a.git_repo, '')) as locator
				FROM cairn.work_artifact_revision r
				JOIN cairn.work_artifact a ON a.artifact_id = r.artifact_id
				WHERE r.revision_id = $1
			`, revID).Scan(&ca.RevisionUUID, &rCollection, &artID, &ca.Kind, &ca.Locator)
			if errors.Is(err, pgx.ErrNoRows) {
				return nil, nil, failure("SOURCE_INELIGIBLE", fmt.Sprintf("candidate artifact revision %s not found", revID))
			}
			if err != nil {
				return nil, nil, err
			}
			if rCollection != collection {
				return nil, nil, failure("SOURCE_INELIGIBLE", fmt.Sprintf("candidate artifact revision %s belongs to collection %s, expected %s", revID, rCollection, collection))
			}
			var fenced bool
			err = tx.QueryRow(ctx, `
				SELECT true FROM cairn.work_artifact_purge_fence WHERE collection = $1 AND artifact_id = $2
			`, collection, artID).Scan(&fenced)
			if err == nil && fenced {
				return nil, nil, failure("SOURCE_INELIGIBLE", fmt.Sprintf("candidate artifact revision %s is under purge fence", revID))
			}
			ca.Role = "candidate"
			candidateArtifacts = append(candidateArtifacts, ca)
		}
	}
	if len(candidateArtifacts) > advice.MaxCandidateArtifacts {
		return nil, nil, failure("INPUT_TOO_LARGE", fmt.Sprintf("candidate artifacts count %d exceeds maximum %d", len(candidateArtifacts), advice.MaxCandidateArtifacts))
	}

	// 5. Selected sources: require explicit caller-selected SourceRefs (record_id, version) verified Store-side
	var sourceRefs []advice.SnapshotSource
	var sourceDigests []WorkAdviceSourceDigest
	if len(spec.SelectedSources) > 0 {
		if !allowedMap["sources"] {
			return nil, nil, failure("SOURCE_INELIGIBLE", "source references not permitted by advice policy")
		}
		for _, sRef := range spec.SelectedSources {
			rec, err := readRecord(ctx, tx, sRef.RecordID.String())
			if errors.Is(err, pgx.ErrNoRows) || Code(err) == "NOT_FOUND" {
				return nil, nil, failure("SOURCE_INELIGIBLE", fmt.Sprintf("source record %s not found", sRef.RecordID))
			}
			if err != nil {
				return nil, nil, err
			}
			if rec.Lifecycle != "active" {
				return nil, nil, failure("SOURCE_INELIGIBLE", fmt.Sprintf("source record %s lifecycle is %q, expected active", sRef.RecordID, rec.Lifecycle))
			}
			if rec.Version != sRef.Version {
				return nil, nil, failure("SOURCE_INELIGIBLE", fmt.Sprintf("source record %s version mismatch: requested %d, current %d", sRef.RecordID, sRef.Version, rec.Version))
			}
			if rec.Scope.Repo != collection {
				return nil, nil, failure("SOURCE_INELIGIBLE", fmt.Sprintf("source record %s belongs to repo %q, expected %q", sRef.RecordID, rec.Scope.Repo, collection))
			}
			if rec.Sensitivity != "shareable" {
				return nil, nil, failure("SOURCE_INELIGIBLE", fmt.Sprintf("source record %s sensitivity is %q, must be shareable for hosted advice", sRef.RecordID, rec.Sensitivity))
			}
			_, reason, err := eligible(ctx, tx, rec, "context")
			if err != nil {
				return nil, nil, err
			}
			if reason != "" {
				return nil, nil, failure("SOURCE_INELIGIBLE", fmt.Sprintf("source record %s not eligible: %s", sRef.RecordID, reason))
			}

			var payloadDeletedBy *string
			err = tx.QueryRow(ctx, `SELECT payload_deleted_by FROM cairn.record_version WHERE record_id = $1 AND version = $2`, sRef.RecordID, sRef.Version).Scan(&payloadDeletedBy)
			if err != nil && !errors.Is(err, pgx.ErrNoRows) {
				return nil, nil, err
			}
			if payloadDeletedBy != nil {
				return nil, nil, failure("SOURCE_INELIGIBLE", fmt.Sprintf("source record %s has deleted payload", sRef.RecordID))
			}

			bodySum := sha256.Sum256([]byte(rec.Body))
			bodyHex := fmt.Sprintf("%x", bodySum[:])
			sourceDigests = append(sourceDigests, WorkAdviceSourceDigest{
				RecordID:   sRef.RecordID,
				Version:    sRef.Version,
				BodySHA256: bodyHex,
			})
			sourceRefs = append(sourceRefs, advice.SnapshotSource{
				ID:         sRef.RecordID.String(),
				Version:    sRef.Version,
				Kind:       rec.Kind,
				BodySHA256: bodyHex,
				ObservedAt: rec.WrittenAt,
				Body:       rec.Body,
				Title:      rec.Kind,
			})
		}
	}
	if len(sourceRefs) > advice.MaxSources {
		return nil, nil, failure("INPUT_TOO_LARGE", fmt.Sprintf("source references count %d exceeds maximum %d", len(sourceRefs), advice.MaxSources))
	}

	snap := &advice.Snapshot{
		BindingID:          spec.BindingID.String(),
		PlaneItemUUID:      spec.PlaneItemUUID.String(),
		Identifier:         snapIdentifier,
		Title:              snapTitle,
		CurrentState:       snapCurrentState,
		ExistingBlockers:   existingBlockers,
		CandidateArtifacts: candidateArtifacts,
		SourceReferences:   sourceRefs,
	}

	manifest := &WorkAdviceInputManifest{
		RestoreGeneration:         restoreGen,
		MetadataExclusionRevision: metadataExclRev,
		ConfigRevision:            configRevision,
		CacheGeneration:           cacheGen,
		LastRefreshedAt:           *lastRefreshedAt,
		AllowedFields:             allowedFields,
		Sources:                   sourceDigests,
		CandidateRevisionIDs:      spec.CandidateRevisionIDs,
	}

	if err := tx.Commit(ctx); err != nil {
		return nil, nil, err
	}
	return snap, manifest, nil
}

// ExecuteAdviceJob executes an advice job through the provider and records the resulting
// proposal and discrete actions within PostgreSQL, enforcing feature gating and input eligibility.
func (s *Store) ExecuteAdviceJob(ctx context.Context, job *WorkAdviceJob, spec AdviceInputSpec, provider advice.Provider) (*WorkAdviceProposal, []*WorkAdviceAction, error) {
	if err := s.requireOperator(); err != nil {
		return nil, nil, err
	}
	if job == nil {
		return nil, nil, failure("INVALID_REQUEST", "job is required")
	}
	if job.BindingID == uuid.Nil || job.PlaneItemUUID == uuid.Nil {
		return nil, nil, failure("INVALID_REQUEST", "job missing binding_id or plane_item_uuid")
	}
	if spec.BindingID != uuid.Nil && spec.BindingID != job.BindingID {
		return nil, nil, failure("INVALID_REQUEST", "spec binding_id does not match job binding_id")
	}
	if spec.PlaneItemUUID != uuid.Nil && spec.PlaneItemUUID != job.PlaneItemUUID {
		return nil, nil, failure("INVALID_REQUEST", "spec plane_item_uuid does not match job plane_item_uuid")
	}
	spec.BindingID = job.BindingID
	spec.PlaneItemUUID = job.PlaneItemUUID

	if job.RequestUUID == uuid.Nil {
		job.RequestUUID = uuid.New()
	}

	active, err := s.CheckAdviceFeature(ctx)
	if err != nil {
		return nil, nil, err
	}
	if !active {
		return nil, nil, failure("FEATURE_DISABLED", "work-advice/1 feature is disabled")
	}

	// Always build snapshot inside Store from live DB rows
	snapshot, manifest, err := s.BuildWorkAdviceSnapshot(ctx, spec)
	if err != nil {
		return nil, nil, err
	}

	manifestBytes, err := json.Marshal(manifest)
	if err != nil {
		return nil, nil, failure("INVALID_REQUEST", "failed to marshal manifest: "+err.Error())
	}
	mDigest := fmt.Sprintf("%x", sha256.Sum256(manifestBytes))
	job.InputManifest = manifestBytes
	job.InputManifestDigest = mDigest

	canonical, err := json.Marshal(snapshot)
	if err != nil {
		return nil, nil, failure("INVALID_REQUEST", "failed to marshal snapshot: "+err.Error())
	}
	if len(canonical) > advice.MaxInputBytes {
		return nil, nil, failure("INPUT_TOO_LARGE", fmt.Sprintf("canonical snapshot size %d exceeds limit of %d bytes", len(canonical), advice.MaxInputBytes))
	}

	digest := fmt.Sprintf("%x", sha256.Sum256(canonical))
	job.InputPayloadDigest = digest

	if provider == nil {
		return nil, nil, failure("INVALID_REQUEST", "advice provider is required")
	}

	// Enforce project local-default field policy
	if job.ProfileName == "" {
		job.ProfileName = provider.Name()
		if job.ProfileName == "" {
			job.ProfileName = advice.RouteCodexSubscription
		}
	}
	if job.ModelRoute == "" {
		job.ModelRoute = advice.RouteCodexSubscription
	}
	if job.TemplateVersion <= 0 {
		job.TemplateVersion = 1
	}
	if job.DeadlineSeconds <= 0 {
		job.DeadlineSeconds = int(advice.DefaultDeadline / time.Second)
	}

	// 1. Create or check job intent
	if err := s.CreateAdviceJob(ctx, job); err != nil {
		return nil, nil, err
	}

	// If job was already finished or uncertain, return existing proposal or error
	if job.Status == "completed" {
		proposals, err := s.ListAdviceProposals(ctx, job.BindingID, job.PlaneItemUUID)
		if err == nil {
			for _, p := range proposals {
				if p.JobUUID == job.JobUUID {
					prop, acts, gErr := s.GetAdviceProposal(ctx, p.ProposalUUID)
					if gErr == nil {
						return prop, acts, nil
					}
				}
			}
		}
		return nil, nil, failure("NOT_FOUND", "proposal not found for completed job")
	}

	switch job.Status {
	case "failed", "timed_out", "expired", "cleanup_unconfirmed", "uncertain":
		code := job.ErrorCode
		if code == "" {
			code = "MODEL_EXECUTION_FAILED"
		}
		return nil, nil, failure(code, fmt.Sprintf("job %s is in state %q", job.JobUUID, job.Status))
	case "running":
		return nil, nil, failure("INFLIGHT_LIMIT", fmt.Sprintf("job %s is already running", job.JobUUID))
	}

	// CAS transition from 'pending' to 'running'
	casRes, err := s.pool.Exec(ctx, `
		UPDATE cairn.work_advice_job
		SET status = 'running', started_at = clock_timestamp()
		WHERE job_uuid = $1 AND status = 'pending'
	`, job.JobUUID)
	if err != nil {
		return nil, nil, err
	}
	if casRes.RowsAffected() == 0 {
		var curStatus, curErr string
		_ = s.pool.QueryRow(ctx, `SELECT status, COALESCE(error_code, '') FROM cairn.work_advice_job WHERE job_uuid = $1`, job.JobUUID).Scan(&curStatus, &curErr)
		if curStatus == "completed" {
			proposals, lErr := s.ListAdviceProposals(ctx, job.BindingID, job.PlaneItemUUID)
			if lErr == nil {
				for _, p := range proposals {
					if p.JobUUID == job.JobUUID {
						return s.GetAdviceProposal(ctx, p.ProposalUUID)
					}
				}
			}
		}
		if curErr != "" {
			return nil, nil, failure(curErr, fmt.Sprintf("job %s is in state %q", job.JobUUID, curStatus))
		}
		return nil, nil, failure("INFLIGHT_LIMIT", fmt.Sprintf("job %s transition conflict (current status %q)", job.JobUUID, curStatus))
	}
	job.Status = "running"

	// Define real onLaunch callback to persist child process identity before stdin is written
	onLaunch := func(identity advice.ProcessIdentity) error {
		idBytes, err := json.Marshal(identity)
		if err != nil {
			return err
		}
		r, err := s.pool.Exec(ctx, `
			UPDATE cairn.work_advice_job
			SET process_identity = $1
			WHERE job_uuid = $2
		`, idBytes, job.JobUUID)
		if err != nil {
			return err
		}
		if r.RowsAffected() == 0 {
			return errors.New("failed to record process identity: job row not found")
		}
		job.ProcessIdentity = idBytes
		return nil
	}

	// Invoke model provider with deadline and real onLaunch callback
	deadline := time.Duration(job.DeadlineSeconds) * time.Second
	proposal, procRes, err := provider.GenerateAdvice(ctx, snapshot, deadline, onLaunch)
	if err != nil {
		var clErr *advice.CleanupUnconfirmedError
		if errors.As(err, &clErr) {
			clIdent, _ := json.Marshal(clErr.Identity)
			_ = s.FinishAdviceJobWithProcessIdentity(ctx, job.JobUUID, "cleanup_unconfirmed", advice.ErrCleanupUnconfirmed, clIdent)
			return nil, nil, err
		}
		var advErr *advice.AdviceError
		errCode := "MODEL_EXECUTION_FAILED"
		if errors.As(err, &advErr) {
			errCode = advErr.Code
		}
		_ = s.FinishAdviceJob(ctx, job.JobUUID, "failed", errCode)
		return nil, nil, err
	}

	// Completed: Record proposal and actions in PostgreSQL
	proposalUUID := uuid.New()
	normPayload, _ := json.Marshal(proposal)
	rawResult := ""
	if procRes != nil {
		rawResult = string(procRes.Stdout)
	}

	workProposal := &WorkAdviceProposal{
		ProposalUUID:      proposalUUID,
		JobUUID:           job.JobUUID,
		BindingID:         job.BindingID,
		PlaneItemUUID:     job.PlaneItemUUID,
		ReviewVersion:     1,
		Status:            "pending",
		RawResult:         rawResult,
		NormalizedPayload: normPayload,
		ExpiresAt:         time.Now().Add(1 * time.Hour),
		CreatedAt:         time.Now(),
	}

	var actions []*WorkAdviceAction
	for idx, pa := range proposal.Actions {
		actPayload, _ := json.Marshal(pa)
		actions = append(actions, &WorkAdviceAction{
			ActionUUID:    uuid.New(),
			ProposalUUID:  proposalUUID,
			ActionIndex:   idx,
			ActionType:    pa.ActionType,
			TargetPayload: actPayload,
			Rationale:     pa.Rationale,
			Status:        "pending",
			CreatedAt:     time.Now(),
		})
	}

	if err := s.RecordAdviceProposal(ctx, workProposal, actions); err != nil {
		_ = s.FinishAdviceJob(ctx, job.JobUUID, "failed", "RECORD_FAILED")
		return nil, nil, err
	}

	var finishedIdent json.RawMessage
	if procRes != nil {
		finishedIdent, _ = json.Marshal(procRes.Identity)
	}
	_ = s.FinishAdviceJobWithProcessIdentity(ctx, job.JobUUID, "completed", "", finishedIdent)

	return workProposal, actions, nil
}

// GenerateWorkAdvice is the canonical Store entrypoint for generating work coordination advice.
func (s *Store) GenerateWorkAdvice(ctx context.Context, job *WorkAdviceJob, spec AdviceInputSpec, provider advice.Provider) (*WorkAdviceProposal, []*WorkAdviceAction, error) {
	return s.ExecuteAdviceJob(ctx, job, spec, provider)
}

func sameUUIDs(a, b []uuid.UUID) bool {
	if len(a) != len(b) {
		return false
	}
	sa := make([]string, len(a))
	sb := make([]string, len(b))
	for i, u := range a {
		sa[i] = u.String()
	}
	for i, u := range b {
		sb[i] = u.String()
	}
	sort.Strings(sa)
	sort.Strings(sb)
	for i := range sa {
		if sa[i] != sb[i] {
			return false
		}
	}
	return true
}

// ApplyAdviceProposal atomically applies chosen actions of a proposal, executing real domain mutations
// in work coordination tables, and deriving OutputRevisions from actual created/updated rows.
func (s *Store) ApplyAdviceProposal(ctx context.Context, receipt *WorkAdviceApplicationReceipt, expectedReviewVersion int64, actionUUIDs []uuid.UUID) (*WorkAdviceApplicationReceipt, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}
	if receipt == nil || receipt.ProposalUUID == uuid.Nil || receipt.RequestUUID == uuid.Nil {
		return nil, failure("INVALID_REQUEST", "valid receipt with request_uuid and proposal_uuid required")
	}
	if len(actionUUIDs) == 0 {
		return nil, failure("INVALID_REQUEST", "at least one action UUID must be selected for application")
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	if err := s.checkAdviceFeatureTx(ctx, tx); err != nil {
		return nil, err
	}

	// Idempotent retry check on request_uuid with canonical parameter verification
	var existing WorkAdviceApplicationReceipt
	err = tx.QueryRow(ctx, `
		SELECT receipt_uuid, request_uuid, proposal_uuid, applied_action_uuids, applied_by_channel,
		       output_revisions, applied_at, undone, undone_at, undone_by_channel, compensating_revisions
		FROM cairn.work_advice_application_receipt
		WHERE request_uuid = $1
	`, receipt.RequestUUID).Scan(
		&existing.ReceiptUUID, &existing.RequestUUID, &existing.ProposalUUID, &existing.AppliedActionUUIDs,
		&existing.AppliedByChannel, &existing.OutputRevisions, &existing.AppliedAt,
		&existing.Undone, &existing.UndoneAt, &existing.UndoneByChannel, &existing.CompensatingRevisions,
	)
	if err == nil {
		var existingActionUUIDs []uuid.UUID
		if err := json.Unmarshal(existing.AppliedActionUUIDs, &existingActionUUIDs); err != nil {
			return nil, err
		}
		if existing.ProposalUUID != receipt.ProposalUUID || existing.AppliedByChannel != s.channel.Principal || !sameUUIDs(actionUUIDs, existingActionUUIDs) {
			return nil, failure("IDEMPOTENCY_CONFLICT", "request_uuid replay parameter mismatch (caller, proposal, or selected actions differ)")
		}
		return &existing, nil
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return nil, err
	}

	// Lock and check proposal
	var currentStatus string
	var currentRev int64
	var bindingID, planeItemUUID uuid.UUID
	var expiresAt time.Time
	err = tx.QueryRow(ctx, `
		SELECT binding_id, plane_item_uuid, status, review_version, expires_at
		FROM cairn.work_advice_proposal
		WHERE proposal_uuid = $1
		FOR UPDATE
	`, receipt.ProposalUUID).Scan(&bindingID, &planeItemUUID, &currentStatus, &currentRev, &expiresAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, failure("NOT_FOUND", "advice proposal not found")
	}
	if err != nil {
		return nil, err
	}

	if currentStatus != "pending" && currentStatus != "partially_applied" {
		return nil, failure("INVALID_STATE", fmt.Sprintf("proposal is in state %q, cannot apply", currentStatus))
	}
	if currentRev != expectedReviewVersion {
		return nil, failure("VERSION_CONFLICT", fmt.Sprintf("proposal review version conflict: expected %d, got %d", expectedReviewVersion, currentRev))
	}
	if time.Now().After(expiresAt) {
		_, _ = tx.Exec(ctx, `UPDATE cairn.work_advice_proposal SET status = 'expired' WHERE proposal_uuid = $1`, receipt.ProposalUUID)
		return nil, failure("PROPOSAL_EXPIRED", "advice proposal has expired")
	}

	var collection string
	err = tx.QueryRow(ctx, `
		SELECT collection
		FROM cairn.work_project
		WHERE binding_id = $1
	`, bindingID).Scan(&collection)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, failure("NOT_FOUND", "work project binding not found")
	}
	if err != nil {
		return nil, err
	}
	if err := s.checkRepo(collection); err != nil {
		return nil, err
	}

	var manifestRaw []byte
	err = tx.QueryRow(ctx, `
		SELECT j.input_manifest
		FROM cairn.work_advice_proposal p
		JOIN cairn.work_advice_job j ON j.job_uuid = p.job_uuid
		WHERE p.proposal_uuid = $1
	`, receipt.ProposalUUID).Scan(&manifestRaw)
	if err == nil && len(manifestRaw) > 0 {
		var manifest WorkAdviceInputManifest
		if err := json.Unmarshal(manifestRaw, &manifest); err == nil && len(manifest.AllowedFields) > 0 {
			if err := s.verifyAdviceManifestTx(ctx, tx, bindingID, planeItemUUID, collection, &manifest); err != nil {
				return nil, err
			}
		}
	}

	var isAbsent bool
	err = tx.QueryRow(ctx, `
		SELECT is_absent
		FROM cairn.work_item
		WHERE binding_id = $1 AND plane_item_uuid = $2
		FOR UPDATE
	`, bindingID, planeItemUUID).Scan(&isAbsent)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, failure("NOT_FOUND", "work item not found")
	}
	if err != nil {
		return nil, err
	}
	if isAbsent {
		return nil, failure("ITEM_ABSENT", "work item is marked absent")
	}

	exclRows, err := tx.Query(ctx, `
		SELECT field_name, pattern
		FROM cairn.work_metadata_exclusion
		WHERE binding_id = $1
	`, bindingID)
	if err != nil {
		return nil, err
	}
	defer exclRows.Close()

	type metaExcl struct {
		fieldName string
		pattern   string
	}
	var exclusions []metaExcl
	for exclRows.Next() {
		var e metaExcl
		if err := exclRows.Scan(&e.fieldName, &e.pattern); err != nil {
			return nil, err
		}
		exclusions = append(exclusions, e)
	}
	if err := exclRows.Err(); err != nil {
		return nil, err
	}
	exclRows.Close()

	checkExclusion := func(text string) error {
		for _, ex := range exclusions {
			if ex.pattern == "" {
				continue
			}
			if ex.fieldName == "all" || ex.fieldName == "annotation" {
				if strings.Contains(strings.ToLower(text), strings.ToLower(ex.pattern)) {
					return failure("REFUSAL_EXCLUDED", fmt.Sprintf("content matches metadata exclusion pattern %q", ex.pattern))
				}
			}
		}
		return nil
	}

	validateSourceRefsTx := func(sourceRefs []string) error {
		for _, sRefID := range sourceRefs {
			recID, pErr := uuid.Parse(sRefID)
			if pErr != nil || recID == uuid.Nil {
				return failure("SOURCE_INELIGIBLE", fmt.Sprintf("invalid source record ID %q", sRefID))
			}
			rec, err := readRecord(ctx, tx, recID.String())
			if errors.Is(err, pgx.ErrNoRows) || Code(err) == "NOT_FOUND" {
				return failure("SOURCE_INELIGIBLE", fmt.Sprintf("source record %s no longer found", recID))
			}
			if err != nil {
				return err
			}
			if rec.Lifecycle != "active" || rec.Scope.Repo != collection || rec.Sensitivity != "shareable" {
				return failure("SOURCE_INELIGIBLE", fmt.Sprintf("source record %s eligibility mismatch", recID))
			}
			_, reason, err := eligible(ctx, tx, rec, "context")
			if err != nil {
				return err
			}
			if reason != "" {
				return failure("SOURCE_INELIGIBLE", fmt.Sprintf("source record %s is ineligible: %s", recID, reason))
			}
			var payloadDeletedBy *string
			err = tx.QueryRow(ctx, `SELECT payload_deleted_by FROM cairn.record_version WHERE record_id = $1 AND version = $2`, recID, rec.Version).Scan(&payloadDeletedBy)
			if err != nil && !errors.Is(err, pgx.ErrNoRows) {
				return err
			}
			if payloadDeletedBy != nil {
				return failure("SOURCE_INELIGIBLE", fmt.Sprintf("source record %s payload was deleted", recID))
			}
		}
		return nil
	}

	// Lock all actions belonging to this proposal
	actionRows, err := tx.Query(ctx, `
		SELECT action_uuid, action_index, action_type, target_payload, status
		FROM cairn.work_advice_action
		WHERE proposal_uuid = $1
		FOR UPDATE
	`, receipt.ProposalUUID)
	if err != nil {
		return nil, err
	}
	defer actionRows.Close()

	actionsByUUID := make(map[uuid.UUID]*WorkAdviceAction)
	var allActionUUIDs []uuid.UUID
	for actionRows.Next() {
		var act WorkAdviceAction
		if err := actionRows.Scan(&act.ActionUUID, &act.ActionIndex, &act.ActionType, &act.TargetPayload, &act.Status); err != nil {
			return nil, err
		}
		act.ProposalUUID = receipt.ProposalUUID
		actionsByUUID[act.ActionUUID] = &act
		allActionUUIDs = append(allActionUUIDs, act.ActionUUID)
	}
	if err := actionRows.Err(); err != nil {
		return nil, err
	}

	// Validate requested action UUIDs
	for _, actUUID := range actionUUIDs {
		act, found := actionsByUUID[actUUID]
		if !found {
			return nil, failure("NOT_FOUND", fmt.Sprintf("action %s does not belong to proposal %s", actUUID, receipt.ProposalUUID))
		}
		if act.Status != "pending" {
			return nil, failure("INVALID_STATE", fmt.Sprintf("action %s is in state %q, expected pending", actUUID, act.Status))
		}
	}

	// Real domain mutations on actual work coordination tables
	outputRevs := make(map[string]any)

	for _, actUUID := range actionUUIDs {
		act := actionsByUUID[actUUID]

		switch act.ActionType {
		case advice.ActionAnnotateProgress:
			var payload advice.ProgressPayload
			if err := json.Unmarshal(act.TargetPayload, &payload); err != nil {
				return nil, failure("INVALID_PAYLOAD", fmt.Sprintf("malformed progress payload: %v", err))
			}
			if strings.TrimSpace(payload.Text) == "" {
				return nil, failure("INVALID_REQUEST", "progress annotation text cannot be empty")
			}
			if err := checkExclusion(payload.Text); err != nil {
				return nil, err
			}
			if err := validateSourceRefsTx(payload.SourceReferences); err != nil {
				return nil, err
			}

			// Lock and supersede prior active progress annotation for this item
			var priorID uuid.UUID
			var priorRev int64
			var priorBody, priorAuthor string
			var priorSrcRefs json.RawMessage
			var supersededPrior map[string]any

			err = tx.QueryRow(ctx, `
				SELECT annotation_id, revision, body, author, source_references
				FROM cairn.work_annotation
				WHERE binding_id = $1 AND plane_item_uuid = $2 AND kind = 'progress' AND state = 'active'
				FOR UPDATE
			`, bindingID, planeItemUUID).Scan(&priorID, &priorRev, &priorBody, &priorAuthor, &priorSrcRefs)
			if err == nil {
				supersededRev := priorRev + 1
				_, err = tx.Exec(ctx, `
					UPDATE cairn.work_annotation
					SET state = 'superseded', revision = $2, author = $3, updated_at = clock_timestamp()
					WHERE annotation_id = $1
				`, priorID, supersededRev, s.channel.Principal)
				if err != nil {
					return nil, err
				}
				_, err = tx.Exec(ctx, `
					INSERT INTO cairn.work_annotation_history (
						annotation_id, revision, body, state, author, source_references, created_at
					) VALUES ($1, $2, $3, 'superseded', $4, $5, clock_timestamp())
				`, priorID, supersededRev, priorBody, s.channel.Principal, priorSrcRefs)
				if err != nil {
					return nil, err
				}
				supersededPrior = map[string]any{
					"annotation_id":       priorID.String(),
					"prior_revision":      priorRev,
					"superseded_revision": supersededRev,
				}
			} else if !errors.Is(err, pgx.ErrNoRows) {
				return nil, err
			}

			newAnnotID := uuid.New()
			srcRefsJSON, _ := json.Marshal(payload.SourceReferences)
			var rev int64 = 1
			err = tx.QueryRow(ctx, `
				INSERT INTO cairn.work_annotation (
					annotation_id, revision, binding_id, plane_item_uuid,
					kind, body, state, author, source_references, created_at, updated_at
				) VALUES (
					$1, $2, $3, $4,
					'progress', $5, 'active', $6, $7, clock_timestamp(), clock_timestamp()
				) RETURNING revision
			`, newAnnotID, rev, bindingID, planeItemUUID, payload.Text, s.channel.Principal, srcRefsJSON).Scan(&rev)
			if err != nil {
				return nil, err
			}

			_, err = tx.Exec(ctx, `
				INSERT INTO cairn.work_annotation_history (
					annotation_id, revision, body, state, author, source_references, created_at
				) VALUES ($1, $2, $3, 'active', $4, $5, clock_timestamp())
			`, newAnnotID, rev, payload.Text, s.channel.Principal, srcRefsJSON)
			if err != nil {
				return nil, err
			}

			outputRevs[act.ActionUUID.String()] = map[string]any{
				"type":             "progress",
				"annotation_id":    newAnnotID.String(),
				"revision":         rev,
				"superseded_prior": supersededPrior,
			}

		case advice.ActionAnnotateBlocker:
			var payload advice.BlockerPayload
			if err := json.Unmarshal(act.TargetPayload, &payload); err != nil {
				return nil, failure("INVALID_PAYLOAD", fmt.Sprintf("malformed blocker payload: %v", err))
			}

			if payload.BlockerAction == "resolve" || payload.ExistingBlockerID != "" {
				targetID, parseErr := uuid.Parse(payload.ExistingBlockerID)
				if parseErr != nil || targetID == uuid.Nil {
					return nil, failure("INVALID_REQUEST", "invalid existing blocker ID for resolution")
				}

				var curRev int64
				var curState, curBody, curAuthor string
				var curSrcRefs json.RawMessage
				err = tx.QueryRow(ctx, `
					SELECT revision, state, body, author, source_references
					FROM cairn.work_annotation
					WHERE annotation_id = $1 AND binding_id = $2 AND plane_item_uuid = $3 AND kind = 'blocker'
					FOR UPDATE
				`, targetID, bindingID, planeItemUUID).Scan(&curRev, &curState, &curBody, &curAuthor, &curSrcRefs)
				if errors.Is(err, pgx.ErrNoRows) {
					return nil, failure("NOT_FOUND", fmt.Sprintf("target blocker annotation %s not found", targetID))
				}
				if err != nil {
					return nil, err
				}

				if curState == "resolved" {
					return nil, failure("VERSION_CONFLICT", fmt.Sprintf("blocker %s is already resolved", targetID))
				}
				if payload.ExpectedRevision > 0 && curRev != payload.ExpectedRevision {
					return nil, failure("VERSION_CONFLICT", fmt.Sprintf("blocker expected revision %d, current is %d", payload.ExpectedRevision, curRev))
				}

				newRev := curRev + 1
				_, err = tx.Exec(ctx, `
					UPDATE cairn.work_annotation
					SET state = 'resolved', revision = $2, author = $3, updated_at = clock_timestamp()
					WHERE annotation_id = $1
				`, targetID, newRev, s.channel.Principal)
				if err != nil {
					return nil, err
				}

				_, err = tx.Exec(ctx, `
					INSERT INTO cairn.work_annotation_history (
						annotation_id, revision, body, state, author, source_references, created_at
					) VALUES ($1, $2, $3, 'resolved', $4, $5, clock_timestamp())
				`, targetID, newRev, curBody, s.channel.Principal, curSrcRefs)
				if err != nil {
					return nil, err
				}

				outputRevs[act.ActionUUID.String()] = map[string]any{
					"type":           "blocker_resolved",
					"annotation_id":  targetID.String(),
					"revision":       newRev,
					"prior_revision": curRev,
				}

			} else {
				if strings.TrimSpace(payload.Text) == "" {
					return nil, failure("INVALID_REQUEST", "blocker annotation text cannot be empty")
				}
				if err := checkExclusion(payload.Text); err != nil {
					return nil, err
				}
				if err := validateSourceRefsTx(payload.SourceReferences); err != nil {
					return nil, err
				}
				newAnnotID := uuid.New()
				srcRefsJSON, _ := json.Marshal(payload.SourceReferences)
				var rev int64 = 1
				err = tx.QueryRow(ctx, `
					INSERT INTO cairn.work_annotation (
						annotation_id, revision, binding_id, plane_item_uuid,
						kind, body, state, author, source_references, created_at, updated_at
					) VALUES (
						$1, $2, $3, $4,
						'blocker', $5, 'open', $6, $7, clock_timestamp(), clock_timestamp()
					) RETURNING revision
				`, newAnnotID, rev, bindingID, planeItemUUID, payload.Text, s.channel.Principal, srcRefsJSON).Scan(&rev)
				if err != nil {
					return nil, err
				}

				_, err = tx.Exec(ctx, `
					INSERT INTO cairn.work_annotation_history (
						annotation_id, revision, body, state, author, source_references, created_at
					) VALUES ($1, $2, $3, 'open', $4, $5, clock_timestamp())
				`, newAnnotID, rev, payload.Text, s.channel.Principal, srcRefsJSON)
				if err != nil {
					return nil, err
				}

				outputRevs[act.ActionUUID.String()] = map[string]any{
					"type":          "blocker_created",
					"annotation_id": newAnnotID.String(),
					"revision":      rev,
				}
			}

		case advice.ActionLinkArtifact:
			var payload advice.ArtifactPayload
			if err := json.Unmarshal(act.TargetPayload, &payload); err != nil {
				return nil, failure("INVALID_PAYLOAD", fmt.Sprintf("malformed artifact link payload: %v", err))
			}
			revUUID, parseErr := uuid.Parse(payload.ArtifactRevisionID)
			if parseErr != nil || revUUID == uuid.Nil {
				return nil, failure("INVALID_REQUEST", "invalid artifact revision ID")
			}
			role := strings.ToLower(strings.TrimSpace(payload.Role))
			if role != "input" && role != "output" && role != "evidence" {
				return nil, failure("INVALID_REQUEST", fmt.Sprintf("invalid artifact link role %q", payload.Role))
			}
			if err := checkExclusion(act.Rationale); err != nil {
				return nil, err
			}

			var featureStatus string
			err = tx.QueryRow(ctx, `SELECT status FROM cairn.work_feature WHERE feature_name = 'work-artifacts/1'`).Scan(&featureStatus)
			if errors.Is(err, pgx.ErrNoRows) || featureStatus != "active" {
				return nil, failure("FEATURE_DISABLED", "feature work-artifacts/1 is not active")
			}
			if err != nil {
				return nil, err
			}

			var artifactID uuid.UUID
			var rCollection string
			err = tx.QueryRow(ctx, `
				SELECT artifact_id, collection
				FROM cairn.work_artifact_revision
				WHERE revision_id = $1
				FOR SHARE
			`, revUUID).Scan(&artifactID, &rCollection)
			if errors.Is(err, pgx.ErrNoRows) {
				return nil, failure("SOURCE_INELIGIBLE", fmt.Sprintf("artifact revision %s not found", revUUID))
			}
			if err != nil {
				return nil, err
			}
			if rCollection != collection {
				return nil, failure("SOURCE_INELIGIBLE", fmt.Sprintf("artifact revision %s belongs to collection %s, expected %s", revUUID, rCollection, collection))
			}
			var fenced bool
			err = tx.QueryRow(ctx, `
				SELECT true FROM cairn.work_artifact_purge_fence WHERE collection = $1 AND artifact_id = $2
			`, collection, artifactID).Scan(&fenced)
			if err == nil && fenced {
				return nil, failure("SOURCE_INELIGIBLE", fmt.Sprintf("artifact %s is under purge fence", artifactID))
			}

			var existingLinkID uuid.UUID
			var existingRev int64
			var existingState, existingRationale, existingAuthor string
			err = tx.QueryRow(ctx, `
				SELECT link_id, revision, state, COALESCE(rationale, ''), author
				FROM cairn.work_artifact_link
				WHERE binding_id = $1 AND plane_item_uuid = $2 AND revision_id = $3 AND role = $4
				FOR UPDATE
			`, bindingID, planeItemUUID, revUUID, role).Scan(&existingLinkID, &existingRev, &existingState, &existingRationale, &existingAuthor)

			var linkID uuid.UUID
			var newRev int64
			var priorLinkInfo map[string]any

			if err == nil {
				linkID = existingLinkID
				newRev = existingRev + 1
				priorLinkInfo = map[string]any{
					"existed":         true,
					"prior_state":     existingState,
					"prior_revision":  existingRev,
					"prior_rationale": existingRationale,
				}
				_, err = tx.Exec(ctx, `
					UPDATE cairn.work_artifact_link
					SET state = 'active', revision = $2, rationale = $3, author = $4, updated_at = clock_timestamp()
					WHERE link_id = $1
				`, linkID, newRev, act.Rationale, s.channel.Principal)
				if err != nil {
					return nil, err
				}
			} else if errors.Is(err, pgx.ErrNoRows) {
				linkID = uuid.New()
				newRev = 1
				priorLinkInfo = map[string]any{
					"existed": false,
				}
				_, err = tx.Exec(ctx, `
					INSERT INTO cairn.work_artifact_link (
						link_id, revision, collection, binding_id, plane_item_uuid,
						artifact_id, revision_id, role, state, rationale, author,
						asserted_at, created_at
					) VALUES (
						$1, $2, $3, $4, $5,
						$6, $7, $8, 'active', $9, $10,
						clock_timestamp(), clock_timestamp()
					)
				`, linkID, newRev, collection, bindingID, planeItemUUID,
					artifactID, revUUID, role, act.Rationale, s.channel.Principal)
				if err != nil {
					return nil, err
				}
			} else {
				return nil, err
			}

			_, err = tx.Exec(ctx, `
				INSERT INTO cairn.work_artifact_link_history (
					history_id, link_id, revision, state, role, rationale, author, created_at
				) VALUES ($1, $2, $3, 'active', $4, $5, $6, clock_timestamp())
			`, uuid.New(), linkID, newRev, role, act.Rationale, s.channel.Principal)
			if err != nil {
				return nil, err
			}

			outputRevs[act.ActionUUID.String()] = map[string]any{
				"type":        "artifact_linked",
				"link_id":     linkID.String(),
				"revision_id": revUUID.String(),
				"revision":    newRev,
				"prior_link":  priorLinkInfo,
			}
		}

		// Update action status to applied
		_, err = tx.Exec(ctx, `
			UPDATE cairn.work_advice_action
			SET status = 'applied'
			WHERE action_uuid = $1
		`, act.ActionUUID)
		if err != nil {
			return nil, err
		}
		act.Status = "applied"
	}

	// Check if any unselected actions remain in pending status
	var unappliedPendingCount int
	err = tx.QueryRow(ctx, `
		SELECT COUNT(*)
		FROM cairn.work_advice_action
		WHERE proposal_uuid = $1 AND status = 'pending'
	`, receipt.ProposalUUID).Scan(&unappliedPendingCount)
	if err != nil {
		return nil, err
	}

	newProposalStatus := "applied"
	if unappliedPendingCount > 0 {
		newProposalStatus = "partially_applied"
	}

	_, err = tx.Exec(ctx, `
		UPDATE cairn.work_advice_proposal
		SET status = $2,
		    review_version = review_version + 1,
		    reviewed_at = clock_timestamp()
		WHERE proposal_uuid = $1
	`, receipt.ProposalUUID, newProposalStatus)
	if err != nil {
		return nil, err
	}

	if receipt.ReceiptUUID == uuid.Nil {
		receipt.ReceiptUUID = uuid.New()
	}
	actionUUIDsJSON, _ := json.Marshal(actionUUIDs)
	receipt.AppliedActionUUIDs = actionUUIDsJSON
	receipt.AppliedByChannel = s.channel.Principal
	outRevBytes, _ := json.Marshal(outputRevs)
	receipt.OutputRevisions = outRevBytes
	receipt.CompensatingRevisions = json.RawMessage("{}")

	_, err = tx.Exec(ctx, `
		INSERT INTO cairn.work_advice_application_receipt (
			receipt_uuid, request_uuid, proposal_uuid, applied_action_uuids,
			applied_by_channel, output_revisions, applied_at, undone,
			compensating_revisions
		) VALUES (
			$1, $2, $3, $4,
			$5, $6, clock_timestamp(), false,
			$7
		)
	`, receipt.ReceiptUUID, receipt.RequestUUID, receipt.ProposalUUID,
		receipt.AppliedActionUUIDs, receipt.AppliedByChannel, receipt.OutputRevisions,
		receipt.CompensatingRevisions)
	if err != nil {
		return nil, err
	}

	if err := tx.Commit(ctx); err != nil {
		return nil, err
	}
	return receipt, nil
}

// UndoAdviceApplication undos an applied advice application receipt, atomically rolling back
// domain mutations with UNDO_CONFLICT detection if any affected entity revision has moved.
func (s *Store) UndoAdviceApplication(ctx context.Context, requestUUID uuid.UUID) error {
	if err := s.requireOperator(); err != nil {
		return err
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	if err := s.checkAdviceFeatureTx(ctx, tx); err != nil {
		return err
	}

	var receiptUUID, proposalUUID uuid.UUID
	var alreadyUndone bool
	var appliedActionsRaw, outputRevsRaw json.RawMessage
	err = tx.QueryRow(ctx, `
		SELECT receipt_uuid, proposal_uuid, undone, applied_action_uuids, output_revisions
		FROM cairn.work_advice_application_receipt
		WHERE request_uuid = $1
		FOR UPDATE
	`, requestUUID).Scan(&receiptUUID, &proposalUUID, &alreadyUndone, &appliedActionsRaw, &outputRevsRaw)
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("NOT_FOUND", "advice application receipt not found")
	}
	if err != nil {
		return err
	}
	if alreadyUndone {
		return nil // idempotent already undone
	}

	// Guarded check: proposal must currently be in applied or partially_applied status
	var propStatus string
	err = tx.QueryRow(ctx, `
		SELECT status
		FROM cairn.work_advice_proposal
		WHERE proposal_uuid = $1
		FOR UPDATE
	`, proposalUUID).Scan(&propStatus)
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("NOT_FOUND", "proposal not found for receipt")
	}
	if err != nil {
		return err
	}
	if propStatus != "applied" && propStatus != "partially_applied" {
		return failure("UNDO_CONFLICT", fmt.Sprintf("proposal is in state %q, cannot undo unless applied or partially_applied", propStatus))
	}

	var outputRevs map[string]map[string]any
	if len(outputRevsRaw) > 0 {
		_ = json.Unmarshal(outputRevsRaw, &outputRevs)
	}

	compensatingRevs := make(map[string]any)

	// Verify all target entity revisions before making any compensating mutations
	for actUUIDStr, revInfo := range outputRevs {
		actionType, _ := revInfo["type"].(string)

		switch actionType {
		case "progress":
			annotIDStr, _ := revInfo["annotation_id"].(string)
			targetID, _ := uuid.Parse(annotIDStr)
			recordedRev := int64(revInfo["revision"].(float64))

			var curRev int64
			var curState, curBody string
			var curAuthor string
			var curSrcRefs json.RawMessage
			err = tx.QueryRow(ctx, `
				SELECT revision, state, body, author, source_references
				FROM cairn.work_annotation
				WHERE annotation_id = $1
				FOR UPDATE
			`, targetID).Scan(&curRev, &curState, &curBody, &curAuthor, &curSrcRefs)
			if errors.Is(err, pgx.ErrNoRows) {
				return failure("UNDO_CONFLICT", fmt.Sprintf("progress annotation %s not found", targetID))
			}
			if err != nil {
				return err
			}
			if curRev != recordedRev || curState != "active" {
				return failure("UNDO_CONFLICT", fmt.Sprintf("progress annotation %s revision moved from %d to %d or state moved, cannot undo", targetID, recordedRev, curRev))
			}

			newRev := curRev + 1
			_, err = tx.Exec(ctx, `
				UPDATE cairn.work_annotation
				SET state = 'superseded', revision = $2, author = $3, updated_at = clock_timestamp()
				WHERE annotation_id = $1
			`, targetID, newRev, s.channel.Principal)
			if err != nil {
				return err
			}

			_, err = tx.Exec(ctx, `
				INSERT INTO cairn.work_annotation_history (
					annotation_id, revision, body, state, author, source_references, created_at
				) VALUES ($1, $2, $3, 'superseded', $4, $5, clock_timestamp())
			`, targetID, newRev, curBody, s.channel.Principal, curSrcRefs)
			if err != nil {
				return err
			}

			compensatingRevs[actUUIDStr] = map[string]any{"type": "progress_superseded", "annotation_id": targetID.String(), "revision": newRev}

			if supPrior, ok := revInfo["superseded_prior"].(map[string]any); ok && supPrior != nil {
				priorIDStr, _ := supPrior["annotation_id"].(string)
				priorID, _ := uuid.Parse(priorIDStr)
				supRev := int64(supPrior["superseded_revision"].(float64))

				var pCurRev int64
				var pCurState, pCurBody, pCurAuthor string
				var pCurSrcRefs json.RawMessage
				err = tx.QueryRow(ctx, `
					SELECT revision, state, body, author, source_references
					FROM cairn.work_annotation
					WHERE annotation_id = $1
					FOR UPDATE
				`, priorID).Scan(&pCurRev, &pCurState, &pCurBody, &pCurAuthor, &pCurSrcRefs)
				if errors.Is(err, pgx.ErrNoRows) {
					return failure("UNDO_CONFLICT", fmt.Sprintf("superseded prior progress annotation %s not found", priorID))
				}
				if err != nil {
					return err
				}
				if pCurRev != supRev || pCurState != "superseded" {
					return failure("UNDO_CONFLICT", fmt.Sprintf("superseded prior progress annotation %s revision moved from %d to %d or state moved, cannot undo", priorID, supRev, pCurRev))
				}

				pRestoredRev := pCurRev + 1
				_, err = tx.Exec(ctx, `
					UPDATE cairn.work_annotation
					SET state = 'active', revision = $2, author = $3, updated_at = clock_timestamp()
					WHERE annotation_id = $1
				`, priorID, pRestoredRev, s.channel.Principal)
				if err != nil {
					return err
				}
				_, err = tx.Exec(ctx, `
					INSERT INTO cairn.work_annotation_history (
						annotation_id, revision, body, state, author, source_references, created_at
					) VALUES ($1, $2, $3, 'active', $4, $5, clock_timestamp())
				`, priorID, pRestoredRev, pCurBody, s.channel.Principal, pCurSrcRefs)
				if err != nil {
					return err
				}
				compensatingRevs[actUUIDStr+"_restored_prior"] = map[string]any{
					"type":          "progress_restored",
					"annotation_id": priorID.String(),
					"revision":      pRestoredRev,
				}
			}

		case "blocker_created":
			annotIDStr, _ := revInfo["annotation_id"].(string)
			targetID, _ := uuid.Parse(annotIDStr)
			recordedRev := int64(revInfo["revision"].(float64))

			var curRev int64
			var curState, curBody, curAuthor string
			var curSrcRefs json.RawMessage
			err = tx.QueryRow(ctx, `
				SELECT revision, state, body, author, source_references
				FROM cairn.work_annotation
				WHERE annotation_id = $1
				FOR UPDATE
			`, targetID).Scan(&curRev, &curState, &curBody, &curAuthor, &curSrcRefs)
			if errors.Is(err, pgx.ErrNoRows) {
				return failure("UNDO_CONFLICT", fmt.Sprintf("blocker annotation %s not found", targetID))
			}
			if err != nil {
				return err
			}
			if curRev != recordedRev || curState != "open" {
				return failure("UNDO_CONFLICT", fmt.Sprintf("blocker annotation %s revision moved from %d to %d or state moved, cannot undo", targetID, recordedRev, curRev))
			}

			newRev := curRev + 1
			_, err = tx.Exec(ctx, `
				UPDATE cairn.work_annotation
				SET state = 'resolved', revision = $2, author = $3, updated_at = clock_timestamp()
				WHERE annotation_id = $1
			`, targetID, newRev, s.channel.Principal)
			if err != nil {
				return err
			}

			_, err = tx.Exec(ctx, `
				INSERT INTO cairn.work_annotation_history (
					annotation_id, revision, body, state, author, source_references, created_at
				) VALUES ($1, $2, $3, 'resolved', $4, $5, clock_timestamp())
			`, targetID, newRev, curBody, s.channel.Principal, curSrcRefs)
			if err != nil {
				return err
			}

			compensatingRevs[actUUIDStr] = map[string]any{"type": "blocker_withdrawn", "annotation_id": targetID.String(), "revision": newRev}

		case "blocker_resolved":
			annotIDStr, _ := revInfo["annotation_id"].(string)
			targetID, _ := uuid.Parse(annotIDStr)
			recordedRev := int64(revInfo["revision"].(float64))

			var curRev int64
			var curState, curBody, curAuthor string
			var curSrcRefs json.RawMessage
			err = tx.QueryRow(ctx, `
				SELECT revision, state, body, author, source_references
				FROM cairn.work_annotation
				WHERE annotation_id = $1
				FOR UPDATE
			`, targetID).Scan(&curRev, &curState, &curBody, &curAuthor, &curSrcRefs)
			if errors.Is(err, pgx.ErrNoRows) {
				return failure("UNDO_CONFLICT", fmt.Sprintf("resolved blocker annotation %s not found", targetID))
			}
			if err != nil {
				return err
			}
			if curRev != recordedRev || curState != "resolved" {
				return failure("UNDO_CONFLICT", fmt.Sprintf("resolved blocker %s revision moved from %d to %d or state moved, cannot undo", targetID, recordedRev, curRev))
			}

			newRev := curRev + 1
			_, err = tx.Exec(ctx, `
				UPDATE cairn.work_annotation
				SET state = 'open', revision = $2, author = $3, updated_at = clock_timestamp()
				WHERE annotation_id = $1
			`, targetID, newRev, s.channel.Principal)
			if err != nil {
				return err
			}

			_, err = tx.Exec(ctx, `
				INSERT INTO cairn.work_annotation_history (
					annotation_id, revision, body, state, author, source_references, created_at
				) VALUES ($1, $2, $3, 'open', $4, $5, clock_timestamp())
			`, targetID, newRev, curBody, s.channel.Principal, curSrcRefs)
			if err != nil {
				return err
			}

			compensatingRevs[actUUIDStr] = map[string]any{"type": "blocker_reopened", "annotation_id": targetID.String(), "revision": newRev}

		case "artifact_linked":
			linkIDStr, _ := revInfo["link_id"].(string)
			linkID, _ := uuid.Parse(linkIDStr)
			recordedRev := int64(revInfo["revision"].(float64))

			var curRev int64
			var curState, curRole, curRationale, curAuthor string
			err = tx.QueryRow(ctx, `
				SELECT revision, state, role, COALESCE(rationale, ''), author
				FROM cairn.work_artifact_link
				WHERE link_id = $1
				FOR UPDATE
			`, linkID).Scan(&curRev, &curState, &curRole, &curRationale, &curAuthor)
			if errors.Is(err, pgx.ErrNoRows) {
				return failure("UNDO_CONFLICT", fmt.Sprintf("artifact link %s not found", linkID))
			}
			if err != nil {
				return err
			}
			if curRev != recordedRev || curState != "active" {
				return failure("UNDO_CONFLICT", fmt.Sprintf("artifact link %s revision moved from %d to %d or state moved, cannot undo", linkID, recordedRev, curRev))
			}

			newRev := curRev + 1
			priorLink, _ := revInfo["prior_link"].(map[string]any)
			existed, _ := priorLink["existed"].(bool)

			if existed {
				priorState, _ := priorLink["prior_state"].(string)
				priorRat, _ := priorLink["prior_rationale"].(string)
				_, err = tx.Exec(ctx, `
					UPDATE cairn.work_artifact_link
					SET state = $2, revision = $3, rationale = $4, author = $5, updated_at = clock_timestamp()
					WHERE link_id = $1
				`, linkID, priorState, newRev, priorRat, s.channel.Principal)
				if err != nil {
					return err
				}
				_, err = tx.Exec(ctx, `
					INSERT INTO cairn.work_artifact_link_history (
						history_id, link_id, revision, state, role, rationale, author, created_at
					) VALUES ($1, $2, $3, $4, $5, $6, $7, clock_timestamp())
				`, uuid.New(), linkID, newRev, priorState, curRole, priorRat, s.channel.Principal)
				if err != nil {
					return err
				}
				compensatingRevs[actUUIDStr] = map[string]any{"type": "artifact_restored", "link_id": linkID.String(), "revision": newRev, "restored_state": priorState}
			} else {
				_, err = tx.Exec(ctx, `
					UPDATE cairn.work_artifact_link
					SET state = 'removed', revision = $2, removed_at = clock_timestamp(), removed_by = $3
					WHERE link_id = $1
				`, linkID, newRev, s.channel.Principal)
				if err != nil {
					return err
				}

				_, err = tx.Exec(ctx, `
					INSERT INTO cairn.work_artifact_link_history (
						history_id, link_id, revision, state, role, rationale, author, created_at
					) VALUES ($1, $2, $3, 'removed', $4, $5, $6, clock_timestamp())
				`, uuid.New(), linkID, newRev, curRole, curRationale, s.channel.Principal)
				if err != nil {
					return err
				}

				compensatingRevs[actUUIDStr] = map[string]any{"type": "artifact_unlinked", "link_id": linkID.String(), "revision": newRev}
			}
		}
	}

	// Mark undone action rows
	var actionUUIDs []uuid.UUID
	if err := json.Unmarshal(appliedActionsRaw, &actionUUIDs); err == nil {
		for _, actUUID := range actionUUIDs {
			tag, err := tx.Exec(ctx, `
				UPDATE cairn.work_advice_action
				SET status = 'undone'
				WHERE action_uuid = $1 AND status = 'applied'
			`, actUUID)
			if err != nil {
				return err
			}
			if tag.RowsAffected() == 0 {
				return failure("UNDO_CONFLICT", fmt.Sprintf("action %s is not in applied status, cannot undo", actUUID))
			}
		}
	}

	// Determine new proposal status: if any other actions are still applied, partially_applied; else pending
	var remainingAppliedCount int
	err = tx.QueryRow(ctx, `
		SELECT COUNT(*)
		FROM cairn.work_advice_action
		WHERE proposal_uuid = $1 AND status = 'applied'
	`, proposalUUID).Scan(&remainingAppliedCount)
	if err != nil {
		return err
	}

	newPropStatus := "pending"
	if remainingAppliedCount > 0 {
		newPropStatus = "partially_applied"
	}

	_, err = tx.Exec(ctx, `
		UPDATE cairn.work_advice_proposal
		SET status = $2,
		    review_version = review_version + 1,
		    reviewed_at = clock_timestamp()
		WHERE proposal_uuid = $1
	`, proposalUUID, newPropStatus)
	if err != nil {
		return err
	}

	ch := s.channel.Principal
	compJSON, _ := json.Marshal(compensatingRevs)
	_, err = tx.Exec(ctx, `
		UPDATE cairn.work_advice_application_receipt
		SET undone = true,
		    undone_at = clock_timestamp(),
		    undone_by_channel = $2,
		    compensating_revisions = $3
		WHERE receipt_uuid = $1
	`, receiptUUID, ch, compJSON)
	if err != nil {
		return err
	}

	return tx.Commit(ctx)
}

// PurgeAdviceForWorkItem deletes all advice jobs, proposals, actions, and receipts for a work item (Forget privacy purge).
func (s *Store) PurgeAdviceForWorkItem(ctx context.Context, bindingID, planeItemUUID uuid.UUID) error {
	if err := s.requireOperator(); err != nil {
		return err
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	// Deleting proposals cascades to actions and application receipts
	_, err = tx.Exec(ctx, `
		DELETE FROM cairn.work_advice_proposal
		WHERE binding_id = $1 AND plane_item_uuid = $2
	`, bindingID, planeItemUUID)
	if err != nil {
		return err
	}

	_, err = tx.Exec(ctx, `
		DELETE FROM cairn.work_advice_job
		WHERE binding_id = $1 AND plane_item_uuid = $2
	`, bindingID, planeItemUUID)
	if err != nil {
		return err
	}

	return tx.Commit(ctx)
}
