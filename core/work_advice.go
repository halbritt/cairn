package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/advice"
	"github.com/jackc/pgx/v5"
)

// WorkAdviceJob records an advice generation execution attempt.
type WorkAdviceJob struct {
	JobUUID                uuid.UUID       `json:"job_uuid"`
	BindingID              uuid.UUID       `json:"binding_id"`
	PlaneItemUUID          uuid.UUID       `json:"plane_item_uuid"`
	Caller                 string          `json:"caller"`
	ProfileName            string          `json:"profile_name"`
	ModelRoute             string          `json:"model_route"`
	TemplateVersion        int             `json:"template_version"`
	RequestUUID            uuid.UUID       `json:"request_uuid"`
	InputManifest          json.RawMessage `json:"input_manifest,omitempty"`
	InputManifestDigest    string          `json:"input_manifest_digest"`
	InputPayloadDigest     string          `json:"input_payload_digest"`
	Status                 string          `json:"status"` // pending, running, completed, failed, timed_out, expired, cleanup_unconfirmed, uncertain
	DeadlineSeconds        int             `json:"deadline_seconds"`
	StartedAt              time.Time       `json:"started_at"`
	FinishedAt             *time.Time      `json:"finished_at,omitempty"`
	ErrorCode              string          `json:"error_code,omitempty"`
	ProcessIdentity        json.RawMessage `json:"process_identity,omitempty"`
	ExpiresAt              time.Time       `json:"expires_at"`
	InputSnapshot          json.RawMessage `json:"input_snapshot,omitempty"`
	InputExpiresAt         time.Time       `json:"input_expires_at"`
	InputUnavailableReason *string         `json:"input_unavailable_reason,omitempty"`
	RequestDigest          string          `json:"request_digest,omitempty"`
	CreatedAt              time.Time       `json:"created_at"`
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
	BindingID             uuid.UUID       `json:"binding_id,omitempty"`
	PlaneItemUUID         uuid.UUID       `json:"plane_item_uuid,omitempty"`
	SourceRecordIDs       json.RawMessage `json:"source_record_ids,omitempty"`
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

// checkAdviceFeatureTx verifies that work-advice/1 is active in the current transaction.
func (s *Store) checkAdviceFeatureTx(ctx context.Context, tx pgx.Tx) error {
	var status string
	err := tx.QueryRow(ctx, `SELECT status FROM cairn.work_feature WHERE feature_name = 'work-advice/1' FOR SHARE`).Scan(&status)
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
	return s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
		RequestID:   uuid.NewString(),
		FeatureName: "work-advice/1",
		Status:      status,
	})
}

// WorkAdviceControl represents the singleton control configuration for advice generation and application.
type WorkAdviceControl struct {
	GenerationEnabled bool      `json:"generation_enabled"`
	ApplyEnabled      bool      `json:"apply_enabled"`
	UpdatedAt         time.Time `json:"updated_at"`
}

func (s *Store) getAdviceControlTx(ctx context.Context, tx pgx.Tx) (*WorkAdviceControl, error) {
	var ctrl WorkAdviceControl
	err := tx.QueryRow(ctx, `SELECT generation_enabled, apply_enabled, updated_at FROM cairn.work_advice_control WHERE singleton = true FOR SHARE`).Scan(&ctrl.GenerationEnabled, &ctrl.ApplyEnabled, &ctrl.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return &WorkAdviceControl{GenerationEnabled: true, ApplyEnabled: true, UpdatedAt: time.Now()}, nil
	}
	if err != nil {
		return nil, err
	}
	return &ctrl, nil
}

// GetAdviceControl returns the current generation and apply control status.
func (s *Store) GetAdviceControl(ctx context.Context) (*WorkAdviceControl, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}
	var ctrl WorkAdviceControl
	err := s.pool.QueryRow(ctx, `SELECT generation_enabled, apply_enabled, updated_at FROM cairn.work_advice_control WHERE singleton = true`).Scan(&ctrl.GenerationEnabled, &ctrl.ApplyEnabled, &ctrl.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return &WorkAdviceControl{GenerationEnabled: true, ApplyEnabled: true, UpdatedAt: time.Now()}, nil
	}
	if err != nil {
		return nil, err
	}
	return &ctrl, nil
}

// PatchAdviceControl atomically updates the generation and/or apply control status.
func (s *Store) PatchAdviceControl(ctx context.Context, genEnabled, applyEnabled *bool) (*WorkAdviceControl, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}
	if genEnabled == nil && applyEnabled == nil {
		return nil, failure("INVALID_REQUEST", "at least one control field is required")
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	var ctrl WorkAdviceControl
	err = tx.QueryRow(ctx, `
		INSERT INTO cairn.work_advice_control (singleton, generation_enabled, apply_enabled, updated_at)
		VALUES (true, COALESCE($1::boolean, true), COALESCE($2::boolean, true), clock_timestamp())
		ON CONFLICT (singleton) DO UPDATE
		SET generation_enabled = COALESCE($1::boolean, cairn.work_advice_control.generation_enabled),
		    apply_enabled = COALESCE($2::boolean, cairn.work_advice_control.apply_enabled),
		    updated_at = clock_timestamp()
		RETURNING generation_enabled, apply_enabled, updated_at
	`, genEnabled, applyEnabled).Scan(&ctrl.GenerationEnabled, &ctrl.ApplyEnabled, &ctrl.UpdatedAt)
	if err != nil {
		return nil, err
	}
	if err := tx.Commit(ctx); err != nil {
		return nil, err
	}
	return &ctrl, nil
}

// SetAdviceControl updates the generation and apply control status.
func (s *Store) SetAdviceControl(ctx context.Context, genEnabled, applyEnabled bool) (*WorkAdviceControl, error) {
	return s.PatchAdviceControl(ctx, &genEnabled, &applyEnabled)
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
	if job.InputExpiresAt.IsZero() {
		job.InputExpiresAt = time.Now().Add(24 * time.Hour)
	}
	if len(job.ProcessIdentity) == 0 {
		job.ProcessIdentity = json.RawMessage("{}")
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	if len(job.InputManifest) == 0 || string(job.InputManifest) == "{}" {
		var policyRev int64
		var policyFieldsRaw []byte
		_ = tx.QueryRow(ctx, `SELECT policy_revision, allowed_fields FROM cairn.work_advice_policy WHERE binding_id = $1`, job.BindingID).Scan(&policyRev, &policyFieldsRaw)
		var restoreGen int64
		_ = tx.QueryRow(ctx, `SELECT generation FROM cairn.retrieval_generation WHERE singleton = true`).Scan(&restoreGen)
		var itemCacheGen int64
		_ = tx.QueryRow(ctx, `SELECT cache_generation FROM cairn.work_item WHERE binding_id = $1 AND plane_item_uuid = $2`, job.BindingID, job.PlaneItemUUID).Scan(&itemCacheGen)
		var exclRev int64
		var configRev string
		_ = tx.QueryRow(ctx, `SELECT metadata_exclusion_revision, config_revision FROM cairn.work_project WHERE binding_id = $1`, job.BindingID).Scan(&exclRev, &configRev)

		manifest := WorkAdviceInputManifest{
			PolicyRevision:            policyRev,
			AllowedFields:             []string{"progress", "blocker", "artifact"},
			RestoreGeneration:         restoreGen,
			CacheGeneration:           itemCacheGen,
			MetadataExclusionRevision: exclRev,
			ConfigRevision:            configRev,
		}
		if len(policyFieldsRaw) > 0 {
			_ = json.Unmarshal(policyFieldsRaw, &manifest.AllowedFields)
		}
		mBytes, _ := json.Marshal(manifest)
		job.InputManifest = mBytes
	}

	if err := s.lockAndCheckFeatureAdmissionTx(ctx, tx, "work-advice/1"); err != nil {
		return err
	}

	ctrl, err := s.getAdviceControlTx(ctx, tx)
	if err != nil {
		return err
	}
	if !ctrl.GenerationEnabled {
		return failure("FEATURE_DISABLED", "advice generation is disabled by operator control")
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
	var existingProcessIdentity, existingManifest, existingSnapshot []byte
	var existingManifestDigest string
	var existingInputExpiresAt time.Time
	var existingInputUnavailableReason *string

	err = tx.QueryRow(ctx, `
		SELECT job_uuid, binding_id, plane_item_uuid, profile_name, model_route,
		       template_version, input_manifest, input_manifest_digest, input_payload_digest,
		       status, deadline_seconds, started_at, finished_at, error_code,
		       process_identity, expires_at, input_snapshot, input_expires_at, input_unavailable_reason, created_at
		FROM cairn.work_advice_job
		WHERE caller = $1 AND request_uuid = $2
		FOR UPDATE
	`, job.Caller, job.RequestUUID).Scan(
		&existingJobUUID, &existingBindingID, &existingPlaneItemUUID, &existingProfileName, &existingModelRoute,
		&existingTemplateVersion, &existingManifest, &existingManifestDigest, &existingDigest,
		&existingStatus, &existingDeadlineSeconds, &existingStartedAt, &existingFinishedAt, &existingErrorCode,
		&existingProcessIdentity, &existingExpiresAt, &existingSnapshot, &existingInputExpiresAt, &existingInputUnavailableReason, &existingCreatedAt,
	)
	if err == nil {
		if existingDigest != job.InputPayloadDigest ||
			existingBindingID != job.BindingID ||
			existingPlaneItemUUID != job.PlaneItemUUID ||
			existingProfileName != job.ProfileName ||
			existingModelRoute != job.ModelRoute ||
			existingTemplateVersion != job.TemplateVersion ||
			existingDeadlineSeconds != job.DeadlineSeconds {
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
		job.InputSnapshot = existingSnapshot
		job.InputExpiresAt = existingInputExpiresAt
		job.InputUnavailableReason = existingInputUnavailableReason
		job.CreatedAt = existingCreatedAt
		return nil
	} else if !errors.Is(err, pgx.ErrNoRows) {
		return err
	}

	// Only for new (caller, request_uuid):
	// Acquire exclusive advisory transaction locks to serialize host admission and profile quota checks
	if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended('work_advice_host_admission', 0))`); err != nil {
		return err
	}
	if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended('work_advice_profile:' || $1, 0))`, job.ProfileName); err != nil {
		return err
	}

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
		WHERE status IN ('pending', 'running', 'cleanup_unconfirmed', 'uncertain')
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
			status, deadline_seconds, started_at, error_code, process_identity, expires_at,
			input_snapshot, input_expires_at, input_unavailable_reason, created_at
		) VALUES (
			$1, $2, $3, $4, $5, $6,
			$7, $8, $9, $10, $11,
			$12, $13, clock_timestamp(), $14, $15, $16,
			$17, $18, $19, clock_timestamp()
		)
	`, job.JobUUID, job.BindingID, job.PlaneItemUUID, job.Caller, job.ProfileName, job.ModelRoute,
		job.TemplateVersion, job.RequestUUID, job.InputManifest, job.InputManifestDigest, job.InputPayloadDigest,
		job.Status, job.DeadlineSeconds, job.ErrorCode, job.ProcessIdentity, job.ExpiresAt,
		job.InputSnapshot, job.InputExpiresAt, job.InputUnavailableReason)
	if err != nil {
		return err
	}

	insertDigest := job.RequestDigest
	if insertDigest == "" {
		insertDigest = job.InputPayloadDigest
	}
	_, err = tx.Exec(ctx, `
		INSERT INTO cairn.work_advice_request_tombstone (
			caller, request_uuid, job_uuid, binding_id, plane_item_uuid, request_digest, terminal_status, error_code
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
		ON CONFLICT (caller, request_uuid) DO UPDATE
		SET terminal_status = EXCLUDED.terminal_status,
		    error_code = EXCLUDED.error_code,
		    updated_at = clock_timestamp()
	`, job.Caller, job.RequestUUID, job.JobUUID, job.BindingID, job.PlaneItemUUID, insertDigest, job.Status, job.ErrorCode)
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
	var startedAt *time.Time
	var errorCode *string
	var snapshot []byte
	var inputExpiresAt time.Time
	var inputUnavailableReason *string
	err = tx.QueryRow(ctx, `
		SELECT job_uuid, binding_id, plane_item_uuid, caller, profile_name, model_route,
		       template_version, request_uuid, input_manifest, input_manifest_digest, input_payload_digest,
		       status, deadline_seconds, started_at, finished_at, error_code, process_identity, expires_at,
		       input_snapshot, input_expires_at, input_unavailable_reason, created_at
		FROM cairn.work_advice_job
		WHERE job_uuid = $1
	`, jobUUID).Scan(
		&job.JobUUID, &job.BindingID, &job.PlaneItemUUID, &job.Caller, &job.ProfileName, &job.ModelRoute,
		&job.TemplateVersion, &job.RequestUUID, &job.InputManifest, &job.InputManifestDigest, &job.InputPayloadDigest,
		&job.Status, &job.DeadlineSeconds, &startedAt, &job.FinishedAt, &errorCode, &job.ProcessIdentity, &job.ExpiresAt,
		&snapshot, &inputExpiresAt, &inputUnavailableReason, &job.CreatedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, failure("NOT_FOUND", "advice job not found")
	}
	if err != nil {
		return nil, err
	}
	if startedAt != nil {
		job.StartedAt = *startedAt
	}
	if errorCode != nil {
		job.ErrorCode = *errorCode
	}
	job.InputSnapshot = snapshot
	job.InputExpiresAt = inputExpiresAt
	job.InputUnavailableReason = inputUnavailableReason

	s.sanitizeAdviceJobSnapshotTx(ctx, tx, &job)

	var collection string
	if err := tx.QueryRow(ctx, `SELECT collection FROM cairn.work_project WHERE binding_id = $1`, job.BindingID).Scan(&collection); err != nil {
		return nil, err
	}
	if err := s.checkRepo(collection); err != nil {
		return nil, err
	}

	return &job, tx.Commit(ctx)
}

func (s *Store) sanitizeAdviceJobSnapshotTx(ctx context.Context, tx pgx.Tx, job *WorkAdviceJob) {
	if len(job.InputSnapshot) == 0 {
		return
	}
	if job.InputUnavailableReason != nil && *job.InputUnavailableReason != "" {
		job.InputSnapshot = nil
		return
	}
	now := time.Now().UTC()
	if now.After(job.InputExpiresAt) {
		job.InputSnapshot = nil
		exp := "EXPIRED"
		job.InputUnavailableReason = &exp
		return
	}
	if len(job.InputManifest) == 0 {
		return
	}
	var manifest WorkAdviceInputManifest
	if err := json.Unmarshal(job.InputManifest, &manifest); err != nil {
		return
	}
	for _, sRef := range manifest.Sources {
		rec, err := readRecord(ctx, tx, sRef.RecordID.String())
		if err != nil {
			job.InputSnapshot = nil
			purged := "PURGED"
			job.InputUnavailableReason = &purged
			return
		}
		var payloadDeletedBy *string
		_ = tx.QueryRow(ctx, `SELECT payload_deleted_by FROM cairn.record_version WHERE record_id = $1 AND version = $2`, sRef.RecordID, sRef.Version).Scan(&payloadDeletedBy)
		if payloadDeletedBy != nil || rec.Sensitivity != "shareable" {
			job.InputSnapshot = nil
			purged := "PURGED"
			job.InputUnavailableReason = &purged
			return
		}
	}
}

// GetAdviceJobByRequestUUID fetches an advice job by request UUID through transaction guards.
func (s *Store) GetAdviceJobByRequestUUID(ctx context.Context, requestUUID uuid.UUID) (*WorkAdviceJob, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	var job WorkAdviceJob
	var startedAt *time.Time
	var errorCode *string
	var snapshot []byte
	var inputExpiresAt time.Time
	var inputUnavailableReason *string
	err = tx.QueryRow(ctx, `
		SELECT job_uuid, binding_id, plane_item_uuid, caller, profile_name, model_route,
		       template_version, request_uuid, input_manifest, input_manifest_digest, input_payload_digest,
		       status, deadline_seconds, started_at, finished_at, error_code, process_identity, expires_at,
		       input_snapshot, input_expires_at, input_unavailable_reason, created_at
		FROM cairn.work_advice_job
		WHERE request_uuid = $1
		ORDER BY created_at DESC
		LIMIT 1
	`, requestUUID).Scan(
		&job.JobUUID, &job.BindingID, &job.PlaneItemUUID, &job.Caller, &job.ProfileName, &job.ModelRoute,
		&job.TemplateVersion, &job.RequestUUID, &job.InputManifest, &job.InputManifestDigest, &job.InputPayloadDigest,
		&job.Status, &job.DeadlineSeconds, &startedAt, &job.FinishedAt, &errorCode, &job.ProcessIdentity, &job.ExpiresAt,
		&snapshot, &inputExpiresAt, &inputUnavailableReason, &job.CreatedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		var tTerminalStatus string
		var tErrorCode *string
		var tBindingID uuid.UUID
		tErr := tx.QueryRow(ctx, `
			SELECT terminal_status, error_code, binding_id
			FROM cairn.work_advice_request_tombstone
			WHERE request_uuid = $1
		`, requestUUID).Scan(&tTerminalStatus, &tErrorCode, &tBindingID)
		if tErr == nil {
			var collection string
			if pErr := tx.QueryRow(ctx, `SELECT collection FROM cairn.work_project WHERE binding_id = $1`, tBindingID).Scan(&collection); pErr != nil {
				return nil, pErr
			}
			if err := s.checkRepo(collection); err != nil {
				return nil, err
			}
			if tTerminalStatus == "completed" || tTerminalStatus == "expired" {
				return nil, failure("EXPIRED", "advice job was deleted by retention policy and proposal has expired")
			}
			code := "MODEL_EXECUTION_FAILED"
			if tErrorCode != nil && *tErrorCode != "" {
				code = *tErrorCode
			}
			return nil, failure(code, fmt.Sprintf("advice job was deleted by retention policy and recorded %s", code))
		}
		return nil, failure("NOT_FOUND", "advice job not found")
	}
	if err != nil {
		return nil, err
	}
	if startedAt != nil {
		job.StartedAt = *startedAt
	}
	if errorCode != nil {
		job.ErrorCode = *errorCode
	}
	job.InputSnapshot = snapshot
	job.InputExpiresAt = inputExpiresAt
	job.InputUnavailableReason = inputUnavailableReason

	s.sanitizeAdviceJobSnapshotTx(ctx, tx, &job)

	var collection string
	if err := tx.QueryRow(ctx, `SELECT collection FROM cairn.work_project WHERE binding_id = $1`, job.BindingID).Scan(&collection); err != nil {
		return nil, err
	}
	if err := s.checkRepo(collection); err != nil {
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
	_, err = tx.Exec(ctx, `
		UPDATE cairn.work_advice_request_tombstone
		SET terminal_status = $2, error_code = $3, updated_at = clock_timestamp()
		WHERE job_uuid = $1
	`, jobUUID, status, errorCode)
	if err != nil {
		return err
	}

	return tx.Commit(ctx)
}

func (s *Store) verifyDerivedSourceEligibleTx(ctx context.Context, tx pgx.Tx, collection string, recID uuid.UUID) error {
	rec, err := readRecord(ctx, tx, recID.String())
	if errors.Is(err, pgx.ErrNoRows) || Code(err) == "NOT_FOUND" {
		return failure("SOURCE_INELIGIBLE", fmt.Sprintf("derived source record %s not found", recID))
	}
	if err != nil {
		return err
	}
	if rec.Lifecycle != "active" || rec.Scope.Repo != collection || rec.Sensitivity != "shareable" {
		return failure("SOURCE_INELIGIBLE", fmt.Sprintf("derived source record %s lifecycle, repo, or sensitivity ineligible", recID))
	}
	_, reason, err := eligible(ctx, tx, rec, "context")
	if err != nil {
		return err
	}
	if reason != "" {
		return failure("SOURCE_INELIGIBLE", fmt.Sprintf("derived source record %s is ineligible: %s", recID, reason))
	}
	var deletedVersionExists bool
	err = tx.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM cairn.record_version WHERE record_id = $1 AND payload_deleted_by IS NOT NULL)`, recID).Scan(&deletedVersionExists)
	if err != nil {
		return err
	}
	if deletedVersionExists {
		return failure("SOURCE_INELIGIBLE", fmt.Sprintf("derived source record %s has deleted version", recID))
	}
	return nil
}

func (s *Store) verifyAdviceManifestTx(ctx context.Context, tx pgx.Tx, bindingID, planeItemUUID uuid.UUID, collection string, manifest *WorkAdviceInputManifest, proposalUUID ...uuid.UUID) error {
	if manifest == nil {
		return failure("INVALID_REQUEST", "missing input manifest for revalidation")
	}

	// 1. Re-verify restore generation
	var currentRestoreGen int64
	err := tx.QueryRow(ctx, `SELECT generation FROM cairn.retrieval_generation WHERE singleton = true FOR SHARE`).Scan(&currentRestoreGen)
	if err != nil {
		return err
	}
	if currentRestoreGen != manifest.RestoreGeneration {
		return failure("RESTORE_FENCE_TRIGGERED", fmt.Sprintf("restore generation moved from %d to %d", manifest.RestoreGeneration, currentRestoreGen))
	}

	// 2. Re-verify metadata exclusions, config revision, project status, and auth from work_project
	var currentExclRev int64
	var currentConfigRev, currentProjStatus string
	var currentAuthRejected bool
	err = tx.QueryRow(ctx, `SELECT metadata_exclusion_revision, config_revision, status, auth_rejected FROM cairn.work_project WHERE binding_id = $1 FOR SHARE`, bindingID).Scan(&currentExclRev, &currentConfigRev, &currentProjStatus, &currentAuthRejected)
	if err != nil {
		return err
	}
	if currentProjStatus != "active" {
		return failure("INVALID_STATE", fmt.Sprintf("work project status is %q, expected active", currentProjStatus))
	}
	if currentAuthRejected {
		return failure("SOURCE_INELIGIBLE", "work project Plane authentication is rejected")
	}
	if currentExclRev != manifest.MetadataExclusionRevision {
		return failure("EXCLUSION_CONFLICT", fmt.Sprintf("metadata exclusions revision moved from %d to %d", manifest.MetadataExclusionRevision, currentExclRev))
	}
	if manifest.ConfigRevision != "" && currentConfigRev != manifest.ConfigRevision {
		return failure("CONFIG_CONFLICT", fmt.Sprintf("work project config revision moved from %s to %s", manifest.ConfigRevision, currentConfigRev))
	}

	// Re-verify policy revision
	if manifest.PolicyRevision > 0 {
		var currentPolicyRev int64
		err = tx.QueryRow(ctx, `SELECT policy_revision FROM cairn.work_advice_policy WHERE binding_id = $1 FOR SHARE`, bindingID).Scan(&currentPolicyRev)
		if errors.Is(err, pgx.ErrNoRows) {
			return failure("POLICY_UNENFORCEABLE", "work advice policy not configured")
		}
		if err != nil {
			return err
		}
		if currentPolicyRev != manifest.PolicyRevision {
			return failure("POLICY_CONFLICT", fmt.Sprintf("work advice policy revision moved from %d to %d", manifest.PolicyRevision, currentPolicyRev))
		}
	}

	// Re-verify progress annotation precondition
	expectedPresent := manifest.PreconditionProgressPresent
	var expectedID *uuid.UUID = manifest.PreconditionProgressID
	expectedRev := manifest.PreconditionProgressRevision

	type expLink struct {
		linkID   uuid.UUID
		revision int64
		state    string
	}
	type linkKey struct {
		revID uuid.UUID
		role  string
	}
	expectedLinks := make(map[linkKey]expLink)
	for _, cl := range manifest.CapturedArtifactLinks {
		expectedLinks[linkKey{revID: cl.RevisionID, role: cl.Role}] = expLink{
			linkID:   cl.LinkID,
			revision: cl.Revision,
			state:    cl.State,
		}
	}

	if len(proposalUUID) > 0 && proposalUUID[0] != uuid.Nil {
		receiptRows, err := tx.Query(ctx, `
			SELECT output_revisions, undone, compensating_revisions
			FROM cairn.work_advice_application_receipt
			WHERE proposal_uuid = $1
			ORDER BY applied_at ASC, receipt_uuid ASC
		`, proposalUUID[0])
		if err != nil {
			return err
		}
		defer receiptRows.Close()

		for receiptRows.Next() {
			var outRaw, compRaw json.RawMessage
			var undone bool
			if err := receiptRows.Scan(&outRaw, &undone, &compRaw); err != nil {
				return err
			}
			if !undone {
				var outMap map[string]map[string]any
				if len(outRaw) > 0 {
					_ = json.Unmarshal(outRaw, &outMap)
				}
				for _, info := range outMap {
					t, _ := info["type"].(string)
					if t == "progress" {
						if aIDStr, ok := info["annotation_id"].(string); ok {
							if aID, err := uuid.Parse(aIDStr); err == nil {
								if rFloat, ok := info["revision"].(float64); ok {
									expectedPresent = true
									expectedID = &aID
									expectedRev = int64(rFloat)
								}
							}
						}
					} else if t == "artifact_linked" {
						linkIDStr, _ := info["link_id"].(string)
						linkID, _ := uuid.Parse(linkIDStr)
						revIDStr, _ := info["revision_id"].(string)
						revID, _ := uuid.Parse(revIDStr)
						role, _ := info["role"].(string)
						if rFloat, ok := info["revision"].(float64); ok && revID != uuid.Nil && role != "" {
							expectedLinks[linkKey{revID: revID, role: role}] = expLink{
								linkID:   linkID,
								revision: int64(rFloat),
								state:    "active",
							}
						}
					}
				}
			} else {
				var compMap map[string]map[string]any
				if len(compRaw) > 0 {
					_ = json.Unmarshal(compRaw, &compMap)
				}
				hasRestored := false
				for _, compInfo := range compMap {
					t, _ := compInfo["type"].(string)
					if t == "progress_restored" {
						if aIDStr, ok := compInfo["annotation_id"].(string); ok {
							if aID, err := uuid.Parse(aIDStr); err == nil {
								if rFloat, ok := compInfo["revision"].(float64); ok {
									expectedPresent = true
									expectedID = &aID
									expectedRev = int64(rFloat)
									hasRestored = true
								}
							}
						}
					} else if t == "artifact_restored" {
						linkIDStr, _ := compInfo["link_id"].(string)
						linkID, _ := uuid.Parse(linkIDStr)
						rFloat, _ := compInfo["revision"].(float64)
						restState, _ := compInfo["restored_state"].(string)
						if linkID != uuid.Nil {
							for k, el := range expectedLinks {
								if el.linkID == linkID {
									el.revision = int64(rFloat)
									el.state = restState
									expectedLinks[k] = el
								}
							}
						}
					}
				}
				if !hasRestored {
					expectedPresent = false
					expectedID = nil
					expectedRev = 0
				}
			}
		}
		receiptRows.Close()
	}

	var currentProgID uuid.UUID
	var currentProgRev int64
	err = tx.QueryRow(ctx, `
		SELECT annotation_id, revision
		FROM cairn.work_annotation
		WHERE binding_id = $1 AND plane_item_uuid = $2 AND kind = 'progress' AND state = 'active'
		FOR SHARE
		LIMIT 1
	`, bindingID, planeItemUUID).Scan(&currentProgID, &currentProgRev)
	if err == nil {
		if !expectedPresent {
			return failure("STALE_OBSERVATION", "active progress was added after advice generation")
		}
		if expectedID == nil || *expectedID != currentProgID || expectedRev != currentProgRev {
			return failure("STALE_OBSERVATION", "active progress annotation modified after advice generation")
		}
	} else if errors.Is(err, pgx.ErrNoRows) {
		if expectedPresent {
			return failure("STALE_OBSERVATION", "active progress annotation was removed after advice generation")
		}
	} else {
		return err
	}

	// Re-verify candidate artifact links
	if len(manifest.CandidateRevisionIDs) > 0 {
		cRows, err := tx.Query(ctx, `
			SELECT link_id, revision, revision_id, role, state
			FROM cairn.work_artifact_link
			WHERE binding_id = $1 AND plane_item_uuid = $2 AND revision_id = ANY($3)
			FOR SHARE
		`, bindingID, planeItemUUID, manifest.CandidateRevisionIDs)
		if err != nil {
			return err
		}
		defer cRows.Close()

		liveLinks := make(map[linkKey]expLink)
		for cRows.Next() {
			var lID, rID uuid.UUID
			var rev int64
			var role, st string
			if err := cRows.Scan(&lID, &rev, &rID, &role, &st); err != nil {
				return err
			}
			liveLinks[linkKey{revID: rID, role: role}] = expLink{
				linkID:   lID,
				revision: rev,
				state:    st,
			}
		}
		cRows.Close()
		if err := cRows.Err(); err != nil {
			return err
		}

		for k, exp := range expectedLinks {
			live, ok := liveLinks[k]
			if !ok {
				return failure("STALE_OBSERVATION", fmt.Sprintf("candidate artifact link for revision %s role %s was removed after advice generation", k.revID, k.role))
			}
			if live.linkID != exp.linkID || live.revision != exp.revision || live.state != exp.state {
				return failure("STALE_OBSERVATION", fmt.Sprintf("candidate artifact link for revision %s role %s modified after advice generation", k.revID, k.role))
			}
		}
		for k := range liveLinks {
			if _, ok := expectedLinks[k]; !ok {
				return failure("STALE_OBSERVATION", fmt.Sprintf("candidate artifact link for revision %s role %s was added after advice generation", k.revID, k.role))
			}
		}
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

	// 4b. Re-verify derived source records
	for _, recID := range manifest.DerivedSourceRecordIDs {
		if err := s.verifyDerivedSourceEligibleTx(ctx, tx, collection, recID); err != nil {
			return err
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
	if err != nil {
		return err
	}
	if len(manifestRaw) == 0 {
		return failure("INVALID_REQUEST", "missing input manifest")
	}
	var manifest WorkAdviceInputManifest
	if err := json.Unmarshal(manifestRaw, &manifest); err != nil {
		return failure("INVALID_REQUEST", "malformed input manifest: "+err.Error())
	}
	if len(manifest.AllowedFields) == 0 {
		return failure("POLICY_UNENFORCEABLE", "empty allowed_fields in manifest")
	}
	if err := s.verifyAdviceManifestTx(ctx, tx, proposal.BindingID, proposal.PlaneItemUUID, collection, &manifest); err != nil {
		return err
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

// RecordAdviceProposalWithJobCompletion records a proposal, actions, and marks the job completed atomically in one transaction.
func (s *Store) RecordAdviceProposalWithJobCompletion(ctx context.Context, proposal *WorkAdviceProposal, actions []*WorkAdviceAction, processIdentity json.RawMessage) error {
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
	if err != nil {
		return err
	}
	if len(manifestRaw) == 0 {
		return failure("INVALID_REQUEST", "missing input manifest")
	}
	var manifest WorkAdviceInputManifest
	if err := json.Unmarshal(manifestRaw, &manifest); err != nil {
		return failure("INVALID_REQUEST", "malformed input manifest: "+err.Error())
	}
	if len(manifest.AllowedFields) == 0 {
		return failure("POLICY_UNENFORCEABLE", "empty allowed_fields in manifest")
	}
	if err := s.verifyAdviceManifestTx(ctx, tx, proposal.BindingID, proposal.PlaneItemUUID, collection, &manifest); err != nil {
		return err
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

	if len(processIdentity) == 0 {
		processIdentity = json.RawMessage("{}")
	}
	res, err := tx.Exec(ctx, `
		UPDATE cairn.work_advice_job
		SET status = 'completed',
		    finished_at = clock_timestamp(),
		    error_code = NULL,
		    process_identity = CASE WHEN $2::jsonb = '{}'::jsonb THEN process_identity ELSE $2::jsonb END
		WHERE job_uuid = $1 AND status IN ('pending', 'running')
	`, proposal.JobUUID, processIdentity)
	if err != nil {
		return err
	}
	if res.RowsAffected() == 0 {
		return failure("INVALID_STATE", fmt.Sprintf("job %s was not in running state during completion", proposal.JobUUID))
	}

	_, err = tx.Exec(ctx, `
		UPDATE cairn.work_advice_request_tombstone
		SET terminal_status = 'completed', error_code = NULL, updated_at = clock_timestamp()
		WHERE job_uuid = $1
	`, proposal.JobUUID)
	if err != nil {
		return err
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

	var collection string
	if err := tx.QueryRow(ctx, `SELECT collection FROM cairn.work_project WHERE binding_id = $1`, prop.BindingID).Scan(&collection); err != nil {
		return nil, nil, err
	}
	if err := s.checkRepo(collection); err != nil {
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

// GetAdviceProposalByJobUUID fetches the proposal (if any) and all its actions for a job.
func (s *Store) GetAdviceProposalByJobUUID(ctx context.Context, jobUUID uuid.UUID) (*WorkAdviceProposal, []*WorkAdviceAction, error) {
	if err := s.requireOperator(); err != nil {
		return nil, nil, err
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return nil, nil, err
	}
	defer tx.Rollback(ctx)

	var proposalUUID uuid.UUID
	err = tx.QueryRow(ctx, `
		SELECT proposal_uuid
		FROM cairn.work_advice_proposal
		WHERE job_uuid = $1
		ORDER BY created_at DESC
		LIMIT 1
	`, jobUUID).Scan(&proposalUUID)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil, nil // No proposal produced
	}
	if err != nil {
		return nil, nil, err
	}

	_ = tx.Commit(ctx)
	return s.GetAdviceProposal(ctx, proposalUUID)
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

	var collection string
	if err := tx.QueryRow(ctx, `SELECT collection FROM cairn.work_project WHERE binding_id = $1`, bindingID).Scan(&collection); err != nil {
		return nil, err
	}
	if err := s.checkRepo(collection); err != nil {
		return nil, err
	}

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
	ObservedAt time.Time `json:"observed_at"`
}

// WorkAdviceCapturedLink records captured existing artifact link state for candidate revisions.
type WorkAdviceCapturedLink struct {
	LinkID     uuid.UUID `json:"link_id"`
	Revision   int64     `json:"revision"`
	RevisionID uuid.UUID `json:"revision_id"`
	Role       string    `json:"role"`
	State      string    `json:"state"`
}

// WorkAdvicePolicy records policy configuration for a project.
type WorkAdvicePolicy struct {
	BindingID      uuid.UUID `json:"binding_id"`
	PolicyRevision int64     `json:"policy_revision"`
	AllowedFields  []string  `json:"allowed_fields"`
	UpdatedAt      time.Time `json:"updated_at"`
}

type WorkAdviceCapturedBlockerRev struct {
	BlockerID uuid.UUID `json:"blocker_id"`
	Revision  int64     `json:"revision"`
}

// WorkAdviceInputManifest records immutable provenance facts captured at advice launch.
type WorkAdviceInputManifest struct {
	RestoreGeneration            int64                          `json:"restore_generation"`
	MetadataExclusionRevision    int64                          `json:"metadata_exclusion_revision"`
	ConfigRevision               string                         `json:"config_revision,omitempty"`
	PolicyRevision               int64                          `json:"policy_revision"`
	CacheGeneration              int64                          `json:"cache_generation"`
	LastRefreshedAt              time.Time                      `json:"last_refreshed_at"`
	AllowedFields                []string                       `json:"allowed_fields"`
	Sources                      []WorkAdviceSourceDigest       `json:"sources"`
	DerivedSourceRecordIDs       []uuid.UUID                    `json:"derived_source_record_ids,omitempty"`
	CandidateRevisionIDs         []uuid.UUID                    `json:"candidate_revision_ids"`
	PreconditionProgressID       *uuid.UUID                     `json:"precondition_progress_id,omitempty"`
	PreconditionProgressRevision int64                          `json:"precondition_progress_revision,omitempty"`
	PreconditionProgressPresent  bool                           `json:"precondition_progress_present"`
	CapturedArtifactLinks        []WorkAdviceCapturedLink       `json:"captured_artifact_links,omitempty"`
	CapturedBlockerRevs          []WorkAdviceCapturedBlockerRev `json:"captured_blocker_revs,omitempty"`
	Controller                   *advice.ProcessIdentity        `json:"controller,omitempty"`
}

func captureCurrentProcessIdentity() *advice.ProcessIdentity {
	pid := os.Getpid()
	bootData, err := os.ReadFile("/proc/sys/kernel/random/boot_id")
	if err != nil {
		return nil
	}
	bootID := strings.TrimSpace(string(bootData))
	statData, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if err != nil {
		return nil
	}
	text := string(statData)
	open := strings.IndexByte(text, '(')
	close := strings.LastIndexByte(text, ')')
	if open < 1 || close <= open {
		return nil
	}
	fields := strings.Fields(text[close+1:])
	if len(fields) < 20 {
		return nil
	}
	ticks, err := strconv.ParseUint(fields[19], 10, 64)
	if err != nil {
		return nil
	}
	group, _ := strconv.Atoi(fields[2])
	return &advice.ProcessIdentity{
		PID:        pid,
		PGID:       group,
		StartTicks: ticks,
		BootID:     bootID,
	}
}

func isControllerProvenDead(ctrl *advice.ProcessIdentity) bool {
	if ctrl == nil || ctrl.PID <= 0 || ctrl.BootID == "" {
		return false
	}
	bootData, err := os.ReadFile("/proc/sys/kernel/random/boot_id")
	if err != nil {
		return false
	}
	if strings.TrimSpace(string(bootData)) != ctrl.BootID {
		return true
	}
	statData, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", ctrl.PID))
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return true
		}
		return false
	}
	text := string(statData)
	open := strings.IndexByte(text, '(')
	close := strings.LastIndexByte(text, ')')
	if open < 1 || close <= open {
		return false
	}
	fields := strings.Fields(text[close+1:])
	if len(fields) < 20 {
		return false
	}
	ticks, err := strconv.ParseUint(fields[19], 10, 64)
	if err != nil {
		return false
	}
	state := fields[0]
	if ticks != ctrl.StartTicks || state == "Z" || state == "X" {
		return true
	}
	return false
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
		INSERT INTO cairn.work_advice_policy (binding_id, policy_revision, allowed_fields, updated_at)
		VALUES ($1, 1, $2, clock_timestamp())
		ON CONFLICT (binding_id) DO UPDATE SET
			allowed_fields = EXCLUDED.allowed_fields,
			policy_revision = cairn.work_advice_policy.policy_revision + 1,
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

// GetWorkAdvicePolicyDetail retrieves the policy struct with revision for a work project.
func (s *Store) GetWorkAdvicePolicyDetail(ctx context.Context, bindingID uuid.UUID) (*WorkAdvicePolicy, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	var p WorkAdvicePolicy
	var fieldsJSON []byte
	err = tx.QueryRow(ctx, `
		SELECT binding_id, policy_revision, allowed_fields, updated_at
		FROM cairn.work_advice_policy
		WHERE binding_id = $1
	`, bindingID).Scan(&p.BindingID, &p.PolicyRevision, &fieldsJSON, &p.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, failure("POLICY_UNENFORCEABLE", "work advice policy not configured")
	}
	if err != nil {
		return nil, err
	}
	if err := json.Unmarshal(fieldsJSON, &p.AllowedFields); err != nil {
		return nil, err
	}
	return &p, tx.Commit(ctx)
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
	var policyRev int64
	err = tx.QueryRow(ctx, `SELECT policy_revision, allowed_fields FROM cairn.work_advice_policy WHERE binding_id = $1`, spec.BindingID).Scan(&policyRev, &policyFieldsRaw)
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
	var capturedBlockerRevs []WorkAdviceCapturedBlockerRev
	var derivedSourceRecordIDs []uuid.UUID
	if allowedMap["blockers"] {
		rows, err := tx.Query(ctx, `
			SELECT annotation_id, revision, body, source_references
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
			var rawSrcRefs []byte
			if err := rows.Scan(&annID, &eb.Revision, &eb.Text, &rawSrcRefs); err != nil {
				return nil, nil, err
			}
			eb.BlockerID = annID.String()
			existingBlockers = append(existingBlockers, eb)
			capturedBlockerRevs = append(capturedBlockerRevs, WorkAdviceCapturedBlockerRev{
				BlockerID: annID,
				Revision:  eb.Revision,
			})
			if len(rawSrcRefs) > 0 {
				var blkSrcRefs []string
				if err := json.Unmarshal(rawSrcRefs, &blkSrcRefs); err != nil {
					return nil, nil, err
				}
				for _, ref := range blkSrcRefs {
					u, pErr := uuid.Parse(ref)
					if pErr != nil || u == uuid.Nil {
						return nil, nil, failure("SOURCE_INELIGIBLE", fmt.Sprintf("malformed blocker source reference UUID %q", ref))
					}
					derivedSourceRecordIDs = append(derivedSourceRecordIDs, u)
				}
			}
		}
		if err := rows.Err(); err != nil {
			return nil, nil, err
		}
	}

	if len(capturedBlockerRevs) > 0 {
		capturedBlkMap := make(map[uuid.UUID]int64, len(capturedBlockerRevs))
		for _, cb := range capturedBlockerRevs {
			capturedBlkMap[cb.BlockerID] = cb.Revision
		}
		bRows, bErr := tx.Query(ctx, `
			SELECT output_revisions, source_record_ids
			FROM cairn.work_advice_application_receipt
			WHERE binding_id = $1 AND plane_item_uuid = $2 AND NOT undone
		`, spec.BindingID, spec.PlaneItemUUID)
		if bErr != nil {
			return nil, nil, bErr
		}
		defer bRows.Close()

		for bRows.Next() {
			var rawOutRevs, rawSrcs []byte
			if err := bRows.Scan(&rawOutRevs, &rawSrcs); err != nil {
				return nil, nil, err
			}
			var outRevs map[string]map[string]any
			if err := json.Unmarshal(rawOutRevs, &outRevs); err != nil {
				return nil, nil, err
			}
			var sIDs []uuid.UUID
			if err := json.Unmarshal(rawSrcs, &sIDs); err != nil {
				return nil, nil, err
			}

			for _, entry := range outRevs {
				if entry["type"] != "blocker_created" {
					continue
				}
				annIDStr, _ := entry["annotation_id"].(string)
				if annIDStr == "" {
					continue
				}
				annID, pErr := uuid.Parse(annIDStr)
				if pErr != nil {
					continue
				}
				expRev, exists := capturedBlkMap[annID]
				if !exists {
					continue
				}
				var createdRev int64
				switch v := entry["revision"].(type) {
				case float64:
					createdRev = int64(v)
				case int64:
					createdRev = v
				case int:
					createdRev = int64(v)
				case json.Number:
					n, _ := v.Int64()
					createdRev = n
				}
				if createdRev > 0 && createdRev <= expRev {
					derivedSourceRecordIDs = append(derivedSourceRecordIDs, sIDs...)
				}
			}
		}
		if err := bRows.Err(); err != nil {
			return nil, nil, err
		}
		bRows.Close()
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
			var cairnRecID *uuid.UUID
			err = tx.QueryRow(ctx, `
				SELECT r.revision_id, r.collection, a.artifact_id, a.kind, COALESCE(a.report_locator, COALESCE(a.git_repo, '')) as locator, a.cairn_record_id
				FROM cairn.work_artifact_revision r
				JOIN cairn.work_artifact a ON a.artifact_id = r.artifact_id
				WHERE r.revision_id = $1
			`, revID).Scan(&ca.RevisionUUID, &rCollection, &artID, &ca.Kind, &ca.Locator, &cairnRecID)
			if errors.Is(err, pgx.ErrNoRows) {
				return nil, nil, failure("SOURCE_INELIGIBLE", fmt.Sprintf("candidate artifact revision %s not found", revID))
			}
			if err != nil {
				return nil, nil, err
			}
			if cairnRecID != nil {
				derivedSourceRecordIDs = append(derivedSourceRecordIDs, *cairnRecID)
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
				ObservedAt: rec.WrittenAt,
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

	var progAnnID uuid.UUID
	var progRev int64
	var progBody string
	var progPresent bool
	err = tx.QueryRow(ctx, `
		SELECT annotation_id, revision, body
		FROM cairn.work_annotation
		WHERE binding_id = $1 AND plane_item_uuid = $2 AND kind = 'progress' AND state = 'active'
		LIMIT 1
	`, spec.BindingID, spec.PlaneItemUUID).Scan(&progAnnID, &progRev, &progBody)
	if err == nil {
		progPresent = true
	} else if !errors.Is(err, pgx.ErrNoRows) {
		return nil, nil, err
	}

	var capturedLinks []WorkAdviceCapturedLink
	if len(spec.CandidateRevisionIDs) > 0 {
		cRows, qErr := tx.Query(ctx, `
			SELECT link_id, revision, revision_id, role, state
			FROM cairn.work_artifact_link
			WHERE binding_id = $1 AND plane_item_uuid = $2 AND revision_id = ANY($3)
		`, spec.BindingID, spec.PlaneItemUUID, spec.CandidateRevisionIDs)
		if qErr != nil {
			return nil, nil, qErr
		}
		defer cRows.Close()
		for cRows.Next() {
			var cl WorkAdviceCapturedLink
			if err := cRows.Scan(&cl.LinkID, &cl.Revision, &cl.RevisionID, &cl.Role, &cl.State); err != nil {
				return nil, nil, err
			}
			capturedLinks = append(capturedLinks, cl)
		}
		if err := cRows.Err(); err != nil {
			return nil, nil, err
		}
	}

	if len(derivedSourceRecordIDs) > 0 {
		seenSrc := make(map[uuid.UUID]bool)
		var dedupSrc []uuid.UUID
		for _, id := range derivedSourceRecordIDs {
			if id != uuid.Nil && !seenSrc[id] {
				seenSrc[id] = true
				dedupSrc = append(dedupSrc, id)
			}
		}
		derivedSourceRecordIDs = dedupSrc
		for _, recID := range derivedSourceRecordIDs {
			if err := s.verifyDerivedSourceEligibleTx(ctx, tx, collection, recID); err != nil {
				return nil, nil, err
			}
		}
	}

	manifest := &WorkAdviceInputManifest{
		RestoreGeneration:           restoreGen,
		MetadataExclusionRevision:   metadataExclRev,
		ConfigRevision:              configRevision,
		PolicyRevision:              policyRev,
		CacheGeneration:             cacheGen,
		LastRefreshedAt:             *lastRefreshedAt,
		AllowedFields:               allowedFields,
		Sources:                     sourceDigests,
		DerivedSourceRecordIDs:      derivedSourceRecordIDs,
		CandidateRevisionIDs:        spec.CandidateRevisionIDs,
		PreconditionProgressPresent: progPresent,
		CapturedArtifactLinks:       capturedLinks,
		CapturedBlockerRevs:         capturedBlockerRevs,
		Controller:                  captureCurrentProcessIdentity(),
	}
	if progPresent {
		manifest.PreconditionProgressID = &progAnnID
		manifest.PreconditionProgressRevision = progRev
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
	if job.Caller == "" {
		job.Caller = s.channel.Principal
	}

	active, err := s.CheckAdviceFeature(ctx)
	if err != nil {
		return nil, nil, err
	}
	if !active {
		return nil, nil, failure("FEATURE_DISABLED", "work-advice/1 feature is disabled")
	}

	// Check if this request_uuid was already admitted/executed before building snapshot
	var existingJob WorkAdviceJob
	var existingErrorCode *string
	var existingStartedAt, existingFinishedAt *time.Time
	var existingProcessIdent, existingManifestRaw []byte
	var existingInputExpiresAt time.Time
	var existingInputUnavailableReason *string
	err = s.pool.QueryRow(ctx, `
		SELECT job_uuid, binding_id, plane_item_uuid, caller, profile_name, model_route,
		       template_version, request_uuid, input_manifest, input_manifest_digest, input_payload_digest,
		       status, deadline_seconds, started_at, finished_at, error_code,
		       process_identity, expires_at, input_expires_at, input_unavailable_reason, created_at
		FROM cairn.work_advice_job
		WHERE caller = $1 AND request_uuid = $2
	`, job.Caller, job.RequestUUID).Scan(
		&existingJob.JobUUID, &existingJob.BindingID, &existingJob.PlaneItemUUID, &existingJob.Caller,
		&existingJob.ProfileName, &existingJob.ModelRoute, &existingJob.TemplateVersion, &existingJob.RequestUUID,
		&existingManifestRaw, &existingJob.InputManifestDigest, &existingJob.InputPayloadDigest,
		&existingJob.Status, &existingJob.DeadlineSeconds, &existingStartedAt, &existingFinishedAt,
		&existingErrorCode, &existingProcessIdent, &existingJob.ExpiresAt,
		&existingInputExpiresAt, &existingInputUnavailableReason, &existingJob.CreatedAt,
	)
	if err == nil {
		if existingStartedAt != nil {
			existingJob.StartedAt = *existingStartedAt
		}
		if existingFinishedAt != nil {
			existingJob.FinishedAt = existingFinishedAt
		}
		if existingErrorCode != nil {
			existingJob.ErrorCode = *existingErrorCode
		}
		existingJob.ProcessIdentity = existingProcessIdent
		existingJob.InputManifest = existingManifestRaw
		existingJob.InputExpiresAt = existingInputExpiresAt
		existingJob.InputUnavailableReason = existingInputUnavailableReason
		if existingJob.BindingID != job.BindingID ||
			existingJob.PlaneItemUUID != job.PlaneItemUUID ||
			(job.ProfileName != "" && existingJob.ProfileName != job.ProfileName) ||
			(job.ModelRoute != "" && existingJob.ModelRoute != job.ModelRoute) ||
			(job.TemplateVersion > 0 && existingJob.TemplateVersion != job.TemplateVersion) ||
			(job.DeadlineSeconds > 0 && existingJob.DeadlineSeconds != job.DeadlineSeconds) {
			return nil, nil, failure("IDEMPOTENCY_CONFLICT", "request_uuid reused with different job parameters")
		}

		var existingManifest WorkAdviceInputManifest
		if len(existingManifestRaw) > 0 {
			if err := json.Unmarshal(existingManifestRaw, &existingManifest); err != nil {
				return nil, nil, err
			}
		}

		if !sameUUIDs(spec.CandidateRevisionIDs, existingManifest.CandidateRevisionIDs) ||
			!sameSourceSpecs(spec.SelectedSources, existingManifest.Sources) {
			return nil, nil, failure("IDEMPOTENCY_CONFLICT", "request_uuid reused with different input selection")
		}
		if existingJob.Status == "completed" {
			proposals, lErr := s.ListAdviceProposals(ctx, existingJob.BindingID, existingJob.PlaneItemUUID)
			if lErr == nil {
				for _, p := range proposals {
					if p.JobUUID == existingJob.JobUUID {
						prop, acts, gErr := s.GetAdviceProposal(ctx, p.ProposalUUID)
						if gErr == nil {
							*job = existingJob
							return prop, acts, nil
						}
					}
				}
			}
			return nil, nil, failure("NOT_FOUND", "proposal not found for completed job")
		}
		switch existingJob.Status {
		case "running":
			return nil, nil, failure("INFLIGHT_LIMIT", fmt.Sprintf("job %s is already running", existingJob.JobUUID))
		case "failed", "timed_out", "expired", "cleanup_unconfirmed", "uncertain":
			code := "MODEL_EXECUTION_FAILED"
			if existingErrorCode != nil && *existingErrorCode != "" {
				code = *existingErrorCode
			}
			return nil, nil, failure(code, fmt.Sprintf("job %s is in state %q", existingJob.JobUUID, existingJob.Status))
		}
		*job = existingJob
	} else if errors.Is(err, pgx.ErrNoRows) {
		reqDigest := computeRequestDigest(job, spec)
		var tJobUUID, tBindingID, tPlaneItemUUID uuid.UUID
		var tReqDigest, tTerminalStatus string
		var tErrorCode *string
		tErr := s.pool.QueryRow(ctx, `
			SELECT job_uuid, binding_id, plane_item_uuid, request_digest, terminal_status, error_code
			FROM cairn.work_advice_request_tombstone
			WHERE caller = $1 AND request_uuid = $2
		`, job.Caller, job.RequestUUID).Scan(&tJobUUID, &tBindingID, &tPlaneItemUUID, &tReqDigest, &tTerminalStatus, &tErrorCode)
		if tErr == nil {
			if tReqDigest != reqDigest {
				return nil, nil, failure("IDEMPOTENCY_CONFLICT", "request_uuid reused with different job parameters or input selection")
			}
			if tTerminalStatus == "completed" || tTerminalStatus == "expired" {
				return nil, nil, failure("EXPIRED", "advice job was deleted by retention policy and proposal has expired")
			}
			code := "MODEL_EXECUTION_FAILED"
			if tErrorCode != nil && *tErrorCode != "" {
				code = *tErrorCode
			}
			return nil, nil, failure(code, fmt.Sprintf("advice job was deleted by retention policy and recorded %s", code))
		} else if !errors.Is(tErr, pgx.ErrNoRows) {
			return nil, nil, tErr
		}
	} else if !errors.Is(err, pgx.ErrNoRows) {
		return nil, nil, err
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
	job.InputSnapshot = canonical
	job.RequestDigest = computeRequestDigest(job, spec)
	capturedAt := time.Now().UTC()
	if !snapshot.ObservedAt.IsZero() {
		capturedAt = snapshot.ObservedAt
	}
	job.ExpiresAt = capturedAt.Add(1 * time.Hour)
	job.InputExpiresAt = capturedAt.Add(24 * time.Hour)

	var prof *advice.Profile
	if job.ProfileName != "" {
		p, pErr := s.GetAdviceProfile(ctx, job.ProfileName)
		if pErr == nil {
			prof = p
		} else if !errors.Is(pErr, pgx.ErrNoRows) && Code(pErr) != "NOT_FOUND" {
			return nil, nil, pErr
		}
	}
	if prof != nil {
		if job.ModelRoute != "" && job.ModelRoute != prof.Model {
			return nil, nil, failure("INVALID_REQUEST", fmt.Sprintf("model route %q does not match registered profile model %q", job.ModelRoute, prof.Model))
		}
		job.ModelRoute = prof.Model
		if provider == nil {
			adp, err := prof.CreateAdapter()
			if err != nil {
				return nil, nil, failure("SUBSCRIPTION_REQUIRED", err.Error())
			}
			provider = adp
		}
	} else if provider == nil {
		if job.ProfileName == "" {
			return nil, nil, failure("INVALID_REQUEST", "profile name is required")
		}
		return nil, nil, failure("SUBSCRIPTION_REQUIRED", fmt.Sprintf("registered advice profile %q not found", job.ProfileName))
	}

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

	// Define real onLaunch callback to persist child process identity before stdin is written
	onLaunch := func(identity advice.ProcessIdentity) error {
		if identity.PID <= 0 || identity.PGID <= 0 || identity.StartTicks <= 0 || strings.TrimSpace(identity.BootID) == "" {
			return failure("INVALID_PROCESS_IDENTITY", "invalid or unverified process identity at launch boundary")
		}
		idBytes, err := json.Marshal(identity)
		if err != nil {
			return err
		}
		tx, err := s.begin(ctx)
		if err != nil {
			return err
		}
		defer tx.Rollback(ctx)

		var collection string
		err = tx.QueryRow(ctx, `SELECT collection FROM cairn.work_project WHERE binding_id = $1`, job.BindingID).Scan(&collection)
		if err != nil {
			return err
		}

		if err := s.verifyAdviceManifestTx(ctx, tx, job.BindingID, job.PlaneItemUUID, collection, manifest); err != nil {
			return err
		}

		r, err := tx.Exec(ctx, `
			UPDATE cairn.work_advice_job
			SET status = 'running', started_at = clock_timestamp(), process_identity = $1
			WHERE job_uuid = $2 AND status = 'pending'
		`, idBytes, job.JobUUID)
		if err != nil {
			return err
		}
		if r.RowsAffected() == 0 {
			return failure("INVALID_STATE", "job was not in pending state at launch boundary")
		}
		job.ProcessIdentity = idBytes
		job.Status = "running"
		return tx.Commit(ctx)
	}

	// Invoke model provider with deadline and real onLaunch callback
	deadline := time.Duration(job.DeadlineSeconds) * time.Second
	proposal, procRes, err := provider.GenerateAdvice(ctx, snapshot, deadline, onLaunch)
	if err != nil {
		persistCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
		defer cancel()

		var clErr *advice.CleanupUnconfirmedError
		if errors.As(err, &clErr) {
			clIdent, _ := json.Marshal(clErr.Identity)
			if pErr := s.FinishAdviceJobWithProcessIdentity(persistCtx, job.JobUUID, "cleanup_unconfirmed", advice.ErrCleanupUnconfirmed, clIdent); pErr != nil {
				return nil, nil, failure("JOB_STATE_UNCERTAIN", fmt.Sprintf("advice job failed and terminal state could not be recorded: %v", errors.Join(err, pErr)))
			}
			return nil, nil, err
		}
		var advErr *advice.AdviceError
		errCode := "MODEL_EXECUTION_FAILED"
		if errors.As(err, &advErr) {
			errCode = advErr.Code
		}
		if pErr := s.FinishAdviceJob(persistCtx, job.JobUUID, "failed", errCode); pErr != nil {
			return nil, nil, failure("JOB_STATE_UNCERTAIN", fmt.Sprintf("advice job failed and terminal state could not be recorded: %v", errors.Join(err, pErr)))
		}
		return nil, nil, err
	}

	// Completed: Record proposal and actions in PostgreSQL
	proposalUUID := uuid.New()
	normPayload, _ := json.Marshal(proposal)
	rawResult := ""

	workProposal := &WorkAdviceProposal{
		ProposalUUID:      proposalUUID,
		JobUUID:           job.JobUUID,
		BindingID:         job.BindingID,
		PlaneItemUUID:     job.PlaneItemUUID,
		ReviewVersion:     1,
		Status:            "pending",
		RawResult:         rawResult,
		NormalizedPayload: normPayload,
		ExpiresAt:         capturedAt.Add(1 * time.Hour),
		CreatedAt:         time.Now(),
	}

	var actions []*WorkAdviceAction
	for idx, pa := range proposal.Actions {
		var actPayload []byte
		switch pa.ActionType {
		case advice.ActionAnnotateProgress:
			if pa.Progress != nil {
				actPayload, _ = json.Marshal(pa.Progress)
			}
		case advice.ActionAnnotateBlocker:
			if pa.Blocker != nil {
				actPayload, _ = json.Marshal(pa.Blocker)
			}
		case advice.ActionLinkArtifact:
			if pa.Artifact != nil {
				actPayload, _ = json.Marshal(pa.Artifact)
			}
		default:
			actPayload, _ = json.Marshal(pa)
		}
		if len(actPayload) == 0 {
			actPayload = json.RawMessage("{}")
		}
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

	var finishedIdent json.RawMessage
	if procRes != nil {
		finishedIdent, _ = json.Marshal(procRes.Identity)
	}

	if err := s.RecordAdviceProposalWithJobCompletion(ctx, workProposal, actions, finishedIdent); err != nil {
		persistCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
		defer cancel()
		if pErr := s.FinishAdviceJob(persistCtx, job.JobUUID, "uncertain", "RECORD_FAILED"); pErr != nil {
			return nil, nil, failure("JOB_STATE_UNCERTAIN", fmt.Sprintf("advice job failed and terminal state could not be recorded: %v", errors.Join(err, pErr)))
		}
		return nil, nil, err
	}

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

func sameSourceSpecs(specSources []SourceRefSpec, manifestSources []WorkAdviceSourceDigest) bool {
	if len(specSources) != len(manifestSources) {
		return false
	}
	s1 := make([]SourceRefSpec, len(specSources))
	copy(s1, specSources)
	sort.Slice(s1, func(i, j int) bool {
		return s1[i].RecordID.String() < s1[j].RecordID.String()
	})
	s2 := make([]WorkAdviceSourceDigest, len(manifestSources))
	copy(s2, manifestSources)
	sort.Slice(s2, func(i, j int) bool {
		return s2[i].RecordID.String() < s2[j].RecordID.String()
	})
	for i := range s1 {
		if s1[i].RecordID != s2[i].RecordID || s1[i].Version != s2[i].Version {
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

	ctrl, err := s.getAdviceControlTx(ctx, tx)
	if err != nil {
		return nil, err
	}
	if !ctrl.ApplyEnabled {
		return nil, failure("FEATURE_DISABLED", "advice apply is disabled by operator control")
	}

	// Idempotent retry check on request_uuid with canonical parameter verification
	var existing WorkAdviceApplicationReceipt
	var existingPropUUID *uuid.UUID
	err = tx.QueryRow(ctx, `
		SELECT receipt_uuid, request_uuid, proposal_uuid, binding_id, plane_item_uuid, source_record_ids,
		       applied_action_uuids, applied_by_channel,
		       output_revisions, applied_at, undone, undone_at, undone_by_channel, compensating_revisions
		FROM cairn.work_advice_application_receipt
		WHERE request_uuid = $1
	`, receipt.RequestUUID).Scan(
		&existing.ReceiptUUID, &existing.RequestUUID, &existingPropUUID, &existing.BindingID, &existing.PlaneItemUUID, &existing.SourceRecordIDs,
		&existing.AppliedActionUUIDs,
		&existing.AppliedByChannel, &existing.OutputRevisions, &existing.AppliedAt,
		&existing.Undone, &existing.UndoneAt, &existing.UndoneByChannel, &existing.CompensatingRevisions,
	)
	if err == nil {
		if existingPropUUID != nil {
			existing.ProposalUUID = *existingPropUUID
		}
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
	if err != nil {
		return nil, err
	}
	if len(manifestRaw) == 0 {
		return nil, failure("INVALID_REQUEST", "missing input manifest")
	}
	var manifest WorkAdviceInputManifest
	if err := json.Unmarshal(manifestRaw, &manifest); err != nil {
		return nil, failure("INVALID_REQUEST", "malformed input manifest: "+err.Error())
	}
	if len(manifest.AllowedFields) == 0 {
		return nil, failure("POLICY_UNENFORCEABLE", "empty allowed_fields in manifest")
	}
	if err := s.verifyAdviceManifestTx(ctx, tx, bindingID, planeItemUUID, collection, &manifest, receipt.ProposalUUID); err != nil {
		return nil, err
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
		SELECT action_uuid, action_index, action_type, target_payload, rationale, status
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
		if err := actionRows.Scan(&act.ActionUUID, &act.ActionIndex, &act.ActionType, &act.TargetPayload, &act.Rationale, &act.Status); err != nil {
			return nil, err
		}
		act.ProposalUUID = receipt.ProposalUUID
		actionsByUUID[act.ActionUUID] = &act
		allActionUUIDs = append(allActionUUIDs, act.ActionUUID)
	}
	if err := actionRows.Err(); err != nil {
		return nil, err
	}

	// Validate requested action UUIDs and enforce target exclusivity
	seenActionUUIDs := make(map[uuid.UUID]bool)
	var progressActionCount int
	seenBlockerTargets := make(map[string]bool)
	seenArtifactTargets := make(map[string]bool)

	for _, actUUID := range actionUUIDs {
		if seenActionUUIDs[actUUID] {
			return nil, failure("INVALID_REQUEST", fmt.Sprintf("duplicate action UUID %s in apply request", actUUID))
		}
		seenActionUUIDs[actUUID] = true

		act, found := actionsByUUID[actUUID]
		if !found {
			return nil, failure("NOT_FOUND", fmt.Sprintf("action %s does not belong to proposal %s", actUUID, receipt.ProposalUUID))
		}
		if act.Status != "pending" {
			return nil, failure("INVALID_STATE", fmt.Sprintf("action %s is in state %q, expected pending", actUUID, act.Status))
		}

		switch act.ActionType {
		case advice.ActionAnnotateProgress:
			progressActionCount++
			if progressActionCount > 1 {
				return nil, failure("INVALID_REQUEST", "cannot apply multiple progress actions in one request")
			}
		case advice.ActionAnnotateBlocker:
			var payload advice.BlockerPayload
			if err := json.Unmarshal(act.TargetPayload, &payload); err == nil {
				targetKey := payload.BlockerAction + ":" + payload.ExistingBlockerID + ":" + payload.Text
				if seenBlockerTargets[targetKey] {
					return nil, failure("INVALID_REQUEST", "cannot apply duplicate blocker actions targeting the same entity in one request")
				}
				seenBlockerTargets[targetKey] = true
			}
		case advice.ActionLinkArtifact:
			var payload advice.ArtifactPayload
			if err := json.Unmarshal(act.TargetPayload, &payload); err == nil {
				targetKey := payload.ArtifactRevisionID + ":" + payload.Role
				if seenArtifactTargets[targetKey] {
					return nil, failure("INVALID_REQUEST", "cannot apply duplicate artifact links targeting the same revision and role in one request")
				}
				seenArtifactTargets[targetKey] = true
			}
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
					SET state = 'active', revision = $2, rationale = $3, author = $4, asserted_at = clock_timestamp(), removed_at = NULL, removed_by = NULL
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
				"role":        role,
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

	var srcRecIDs []uuid.UUID
	for _, s := range manifest.Sources {
		srcRecIDs = append(srcRecIDs, s.RecordID)
	}
	srcRecIDs = append(srcRecIDs, manifest.DerivedSourceRecordIDs...)
	if len(srcRecIDs) > 0 {
		seenSrc := make(map[uuid.UUID]bool)
		var dedupSrc []uuid.UUID
		for _, id := range srcRecIDs {
			if id != uuid.Nil && !seenSrc[id] {
				seenSrc[id] = true
				dedupSrc = append(dedupSrc, id)
			}
		}
		srcRecIDs = dedupSrc
	}
	srcRecBytes, _ := json.Marshal(srcRecIDs)
	receipt.BindingID = bindingID
	receipt.PlaneItemUUID = planeItemUUID
	receipt.SourceRecordIDs = srcRecBytes

	err = tx.QueryRow(ctx, `
		INSERT INTO cairn.work_advice_application_receipt (
			receipt_uuid, request_uuid, proposal_uuid, binding_id, plane_item_uuid, source_record_ids,
			applied_action_uuids, applied_by_channel, output_revisions, applied_at, undone,
			compensating_revisions
		) VALUES (
			$1, $2, $3, $4, $5, $6,
			$7, $8, $9, clock_timestamp(), false,
			$10
		) RETURNING applied_at
	`, receipt.ReceiptUUID, receipt.RequestUUID, receipt.ProposalUUID, receipt.BindingID, receipt.PlaneItemUUID, receipt.SourceRecordIDs,
		receipt.AppliedActionUUIDs, receipt.AppliedByChannel, receipt.OutputRevisions,
		receipt.CompensatingRevisions).Scan(&receipt.AppliedAt)
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

	var receiptUUID uuid.UUID
	var proposalUUID *uuid.UUID
	var bindingID, planeItemUUID uuid.UUID
	var alreadyUndone bool
	var appliedActionsRaw, outputRevsRaw json.RawMessage
	err = tx.QueryRow(ctx, `
		SELECT receipt_uuid, proposal_uuid, binding_id, plane_item_uuid, undone, applied_action_uuids, output_revisions
		FROM cairn.work_advice_application_receipt
		WHERE request_uuid = $1
		FOR UPDATE
	`, requestUUID).Scan(&receiptUUID, &proposalUUID, &bindingID, &planeItemUUID, &alreadyUndone, &appliedActionsRaw, &outputRevsRaw)
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("NOT_FOUND", "advice application receipt not found")
	}
	if err != nil {
		return err
	}

	var collection string
	err = tx.QueryRow(ctx, `SELECT collection FROM cairn.work_project WHERE binding_id = $1`, bindingID).Scan(&collection)
	if err != nil {
		return err
	}
	if err := s.checkRepo(collection); err != nil {
		return err
	}

	if alreadyUndone {
		return nil // idempotent already undone
	}

	var propStatus string
	if proposalUUID != nil && *proposalUUID != uuid.Nil {
		_ = tx.QueryRow(ctx, `
			SELECT status
			FROM cairn.work_advice_proposal
			WHERE proposal_uuid = $1
			FOR UPDATE
		`, *proposalUUID).Scan(&propStatus)
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
				if priorState == "removed" {
					_, err = tx.Exec(ctx, `
						UPDATE cairn.work_artifact_link
						SET state = $2, revision = $3, rationale = $4, author = $5, removed_at = clock_timestamp(), removed_by = $5
						WHERE link_id = $1
					`, linkID, priorState, newRev, priorRat, s.channel.Principal)
				} else {
					_, err = tx.Exec(ctx, `
						UPDATE cairn.work_artifact_link
						SET state = $2, revision = $3, rationale = $4, author = $5, asserted_at = clock_timestamp(), removed_at = NULL, removed_by = NULL
						WHERE link_id = $1
					`, linkID, priorState, newRev, priorRat, s.channel.Principal)
				}
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

	if proposalUUID != nil && *proposalUUID != uuid.Nil {
		var remainingAppliedCount int
		err = tx.QueryRow(ctx, `
			SELECT COUNT(*)
			FROM cairn.work_advice_action
			WHERE proposal_uuid = $1 AND status = 'applied'
		`, *proposalUUID).Scan(&remainingAppliedCount)
		if err == nil {
			newPropStatus := "pending"
			if remainingAppliedCount > 0 {
				newPropStatus = "partially_applied"
			} else if propStatus == "rejected" {
				newPropStatus = "rejected"
			}

			_, _ = tx.Exec(ctx, `
				UPDATE cairn.work_advice_proposal
				SET status = $2,
				    review_version = review_version + 1,
				    reviewed_at = clock_timestamp()
				WHERE proposal_uuid = $1
			`, *proposalUUID, newPropStatus)
		}
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

// GetAnnotationHistorySnapshot retrieves historical body and state for a specific annotation revision.
func (s *Store) GetAnnotationHistorySnapshot(ctx context.Context, annotationID uuid.UUID, revision int64) (body, state string, ok bool, err error) {
	if err := s.requireOperator(); err != nil {
		return "", "", false, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return "", "", false, err
	}
	defer tx.Rollback(ctx)

	var collection string
	err = tx.QueryRow(ctx, `
		SELECT p.collection
		FROM cairn.work_annotation a
		JOIN cairn.work_project p ON p.binding_id = a.binding_id
		WHERE a.annotation_id = $1
	`, annotationID).Scan(&collection)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", "", false, nil
	}
	if err != nil {
		return "", "", false, err
	}
	if err := s.checkRepo(collection); err != nil {
		return "", "", false, err
	}

	err = tx.QueryRow(ctx, `
		SELECT body, state
		FROM cairn.work_annotation_history
		WHERE annotation_id = $1 AND revision = $2
	`, annotationID, revision).Scan(&body, &state)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", "", false, nil
	}
	if err != nil {
		return "", "", false, err
	}
	return body, state, true, nil
}

// GetAdviceReceipt fetches an application receipt by its request_uuid.
func (s *Store) GetAdviceReceipt(ctx context.Context, requestUUID uuid.UUID) (*WorkAdviceApplicationReceipt, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	var r WorkAdviceApplicationReceipt
	var propUUID *uuid.UUID
	err = tx.QueryRow(ctx, `
		SELECT receipt_uuid, request_uuid, proposal_uuid, binding_id, plane_item_uuid, source_record_ids,
		       applied_action_uuids, applied_by_channel,
		       output_revisions, applied_at, undone, undone_at, undone_by_channel, compensating_revisions
		FROM cairn.work_advice_application_receipt
		WHERE request_uuid = $1
	`, requestUUID).Scan(
		&r.ReceiptUUID, &r.RequestUUID, &propUUID, &r.BindingID, &r.PlaneItemUUID, &r.SourceRecordIDs,
		&r.AppliedActionUUIDs,
		&r.AppliedByChannel, &r.OutputRevisions, &r.AppliedAt,
		&r.Undone, &r.UndoneAt, &r.UndoneByChannel, &r.CompensatingRevisions,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, failure("NOT_FOUND", "advice application receipt not found")
	}
	if err != nil {
		return nil, err
	}
	if propUUID != nil {
		r.ProposalUUID = *propUUID
	}
	var collection string
	if err := tx.QueryRow(ctx, `SELECT collection FROM cairn.work_project WHERE binding_id = $1`, r.BindingID).Scan(&collection); err != nil {
		return nil, err
	}
	if err := s.checkRepo(collection); err != nil {
		return nil, err
	}
	return &r, nil
}

// GetAdviceReceiptByUUID fetches an application receipt by receipt_uuid or request_uuid.
func (s *Store) GetAdviceReceiptByUUID(ctx context.Context, id uuid.UUID) (*WorkAdviceApplicationReceipt, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	var r WorkAdviceApplicationReceipt
	var propUUID *uuid.UUID
	err = tx.QueryRow(ctx, `
		SELECT receipt_uuid, request_uuid, proposal_uuid, binding_id, plane_item_uuid, source_record_ids,
		       applied_action_uuids, applied_by_channel,
		       output_revisions, applied_at, undone, undone_at, undone_by_channel, compensating_revisions
		FROM cairn.work_advice_application_receipt
		WHERE receipt_uuid = $1 OR request_uuid = $1
		LIMIT 1
	`, id).Scan(
		&r.ReceiptUUID, &r.RequestUUID, &propUUID, &r.BindingID, &r.PlaneItemUUID, &r.SourceRecordIDs,
		&r.AppliedActionUUIDs,
		&r.AppliedByChannel, &r.OutputRevisions, &r.AppliedAt,
		&r.Undone, &r.UndoneAt, &r.UndoneByChannel, &r.CompensatingRevisions,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, failure("NOT_FOUND", "advice application receipt not found")
	}
	if err != nil {
		return nil, err
	}
	if propUUID != nil {
		r.ProposalUUID = *propUUID
	}
	var collection string
	if err := tx.QueryRow(ctx, `SELECT collection FROM cairn.work_project WHERE binding_id = $1`, r.BindingID).Scan(&collection); err != nil {
		return nil, err
	}
	if err := s.checkRepo(collection); err != nil {
		return nil, err
	}
	return &r, nil
}

// ListAdviceApplications returns all application receipts for a proposal ordered newest-first.
func (s *Store) ListAdviceApplications(ctx context.Context, proposalUUID uuid.UUID) ([]*WorkAdviceApplicationReceipt, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	var collection string
	err = tx.QueryRow(ctx, `
		SELECT p.collection
		FROM cairn.work_advice_proposal prop
		JOIN cairn.work_project p ON p.binding_id = prop.binding_id
		WHERE prop.proposal_uuid = $1
	`, proposalUUID).Scan(&collection)
	if errors.Is(err, pgx.ErrNoRows) {
		err = tx.QueryRow(ctx, `
			SELECT p.collection
			FROM cairn.work_advice_application_receipt r
			JOIN cairn.work_project p ON p.binding_id = r.binding_id
			WHERE r.proposal_uuid = $1
			LIMIT 1
		`, proposalUUID).Scan(&collection)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, failure("NOT_FOUND", "advice proposal not found")
		}
	}
	if err != nil {
		return nil, err
	}
	if err := s.checkRepo(collection); err != nil {
		return nil, err
	}

	rows, err := tx.Query(ctx, `
		SELECT receipt_uuid, request_uuid, proposal_uuid, binding_id, plane_item_uuid, source_record_ids,
		       applied_action_uuids, applied_by_channel,
		       output_revisions, applied_at, undone, undone_at, undone_by_channel, compensating_revisions
		FROM cairn.work_advice_application_receipt
		WHERE proposal_uuid = $1
		ORDER BY applied_at DESC, receipt_uuid DESC
	`, proposalUUID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var receipts []*WorkAdviceApplicationReceipt
	for rows.Next() {
		var r WorkAdviceApplicationReceipt
		var propUUID *uuid.UUID
		if err := rows.Scan(
			&r.ReceiptUUID, &r.RequestUUID, &propUUID, &r.BindingID, &r.PlaneItemUUID, &r.SourceRecordIDs,
			&r.AppliedActionUUIDs,
			&r.AppliedByChannel, &r.OutputRevisions, &r.AppliedAt,
			&r.Undone, &r.UndoneAt, &r.UndoneByChannel, &r.CompensatingRevisions,
		); err != nil {
			return nil, err
		}
		if propUUID != nil {
			r.ProposalUUID = *propUUID
		}
		receipts = append(receipts, &r)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return receipts, nil
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

	if err := s.PurgeAdviceForWorkItemTx(ctx, tx, bindingID, planeItemUUID); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

// ScrubWorkAdviceForRecordTx purges advice payloads referencing a forgotten or deleted record.
func (s *Store) ScrubWorkAdviceForRecordTx(ctx context.Context, tx pgx.Tx, recordID string) error {
	return scrubWorkAdviceForRecordTx(ctx, tx, recordID)
}

func scrubWorkAdviceForRecordTx(ctx context.Context, tx pgx.Tx, recordID string) error {
	recUUID, err := uuid.Parse(recordID)
	if err != nil || recUUID == uuid.Nil {
		return nil
	}
	idStr := recUUID.String()

	// 1. Freeze affected job_uuids via input_manifest source refs, derived source refs, OR action target_payload citations
	jobRows, err := tx.Query(ctx, `
		SELECT job_uuid FROM cairn.work_advice_job
		WHERE jsonb_path_exists(input_manifest, '$.sources[*] ? (@.record_id == $id)', jsonb_build_object('id', $1::text))
		UNION
		SELECT job_uuid FROM cairn.work_advice_job
		WHERE jsonb_path_exists(input_manifest, '$.derived_source_record_ids[*] ? (@ == $id)', jsonb_build_object('id', $1::text))
		UNION
		SELECT p.job_uuid FROM cairn.work_advice_proposal p
		JOIN cairn.work_advice_action a ON a.proposal_uuid = p.proposal_uuid
		WHERE jsonb_path_exists(a.target_payload, '$.source_references[*] ? (@ == $id)', jsonb_build_object('id', $1::text))
	`, idStr)
	if err != nil {
		return err
	}
	defer jobRows.Close()

	var jobUUIDs []uuid.UUID
	for jobRows.Next() {
		var id uuid.UUID
		if err := jobRows.Scan(&id); err != nil {
			return err
		}
		jobUUIDs = append(jobUUIDs, id)
	}
	jobRows.Close()

	var proposalUUIDs []uuid.UUID
	if len(jobUUIDs) > 0 {
		// 2. Freeze affected proposal_uuids for these jobs
		propRows, err := tx.Query(ctx, `
			SELECT proposal_uuid FROM cairn.work_advice_proposal
			WHERE job_uuid = ANY($1)
		`, jobUUIDs)
		if err != nil {
			return err
		}
		defer propRows.Close()

		for propRows.Next() {
			var id uuid.UUID
			if err := propRows.Scan(&id); err != nil {
				return err
			}
			proposalUUIDs = append(proposalUUIDs, id)
		}
		propRows.Close()

		// 3. Scrub input_snapshot on exact job_uuids, retaining structural dependency facts in input_manifest
		_, err = tx.Exec(ctx, `
			UPDATE cairn.work_advice_job
			SET input_snapshot = NULL,
			    input_unavailable_reason = 'PURGED',
			    input_manifest = CASE
			        WHEN input_manifest ? 'precondition_progress_body'
			        THEN jsonb_set(input_manifest, '{precondition_progress_body}', '"[forgotten]"'::jsonb)
			        ELSE input_manifest
			    END
			WHERE job_uuid = ANY($1)
		`, jobUUIDs)
		if err != nil {
			return err
		}

		if len(proposalUUIDs) > 0 {
			// 4. Scrub derived proposal payloads by exact proposal_uuid PKs
			_, err = tx.Exec(ctx, `
				UPDATE cairn.work_advice_proposal
				SET raw_result = '',
				    normalized_payload = '{}'::jsonb
				WHERE proposal_uuid = ANY($1)
			`, proposalUUIDs)
			if err != nil {
				return err
			}

			// 5. Scrub action target payloads and rationale by exact proposal_uuid PKs
			_, err = tx.Exec(ctx, `
				UPDATE cairn.work_advice_action
				SET target_payload = '{}'::jsonb,
				    rationale = ''
				WHERE proposal_uuid = ANY($1)
			`, proposalUUIDs)
			if err != nil {
				return err
			}
		}
	}

	// 6. Query all receipts referencing this record or affected proposals (OUTSIDE job/proposal guards)
	type receiptData struct {
		outputRevs json.RawMessage
		undone     bool
		compRevs   json.RawMessage
	}
	var receiptWhere string
	var receiptArgs []any
	if len(proposalUUIDs) > 0 {
		receiptWhere = `WHERE proposal_uuid = ANY($1) OR jsonb_path_exists(source_record_ids, '$[*] ? (@ == $id)', jsonb_build_object('id', $2::text))`
		receiptArgs = []any{proposalUUIDs, idStr}
	} else {
		receiptWhere = `WHERE jsonb_path_exists(source_record_ids, '$[*] ? (@ == $id)', jsonb_build_object('id', $1::text))`
		receiptArgs = []any{idStr}
	}

	receiptRows, err := tx.Query(ctx, `
		SELECT output_revisions, undone, COALESCE(compensating_revisions, '{}'::jsonb)
		FROM cairn.work_advice_application_receipt
		`+receiptWhere, receiptArgs...)
	if err != nil {
		return err
	}
	defer receiptRows.Close()

	var allReceipts []receiptData
	for receiptRows.Next() {
		var rd receiptData
		if err := receiptRows.Scan(&rd.outputRevs, &rd.undone, &rd.compRevs); err != nil {
			return err
		}
		if len(rd.outputRevs) > 0 {
			allReceipts = append(allReceipts, rd)
		}
	}
	receiptRows.Close()

	for _, rd := range allReceipts {
		var revMap map[string]map[string]any
		if err := json.Unmarshal(rd.outputRevs, &revMap); err != nil {
			continue
		}
		var compMap map[string]any
		if len(rd.compRevs) > 0 {
			_ = json.Unmarshal(rd.compRevs, &compMap)
		}

		for actUUIDStr, entry := range revMap {
			typ, _ := entry["type"].(string)
			if typ == "progress" || typ == "blocker_created" {
				annotIDStr, _ := entry["annotation_id"].(string)
				annotUUID, pErr := uuid.Parse(annotIDStr)
				if pErr != nil || annotUUID == uuid.Nil {
					continue
				}

				createdRev := int64(1)
				switch v := entry["revision"].(type) {
				case float64:
					createdRev = int64(v)
				case int64:
					createdRev = v
				case int:
					createdRev = int64(v)
				case json.Number:
					n, _ := v.Int64()
					createdRev = n
				}

				// State-only transitions (supersession, resolution and Undo) can
				// copy the created body into later revisions. Match those exact
				// bytes while preserving independently edited progress text.
				var createdBody string
				err := tx.QueryRow(ctx, `
					SELECT body FROM cairn.work_annotation_history
					WHERE annotation_id = $1 AND revision = $2
				`, annotUUID, createdRev).Scan(&createdBody)
				if errors.Is(err, pgx.ErrNoRows) {
					continue // The annotation history has already been removed.
				}
				if err != nil {
					return err
				}
				for _, query := range []string{
					`UPDATE cairn.work_annotation SET body = '[forgotten]'
					 WHERE annotation_id = $1 AND (revision = $2 OR body = $3)`,
					`UPDATE cairn.work_annotation_history SET body = '[forgotten]'
					 WHERE annotation_id = $1 AND (revision = $2 OR body = $3)`,
					`UPDATE cairn.mutation_request
					 SET response = jsonb_set(response, '{body}', '"[forgotten]"'::jsonb)
					 WHERE response->>'annotation_id' = ($1::uuid)::text
					   AND (response->>'revision' = ($2::bigint)::text OR response->>'body' = $3)`,
				} {
					if _, err := tx.Exec(ctx, query, annotUUID, createdRev, createdBody); err != nil {
						return err
					}
				}
			} else if typ == "artifact_linked" {
				linkIDStr, _ := entry["link_id"].(string)
				linkUUID, pErr := uuid.Parse(linkIDStr)
				if pErr != nil || linkUUID == uuid.Nil {
					continue
				}
				var recRev int64
				switch v := entry["revision"].(type) {
				case float64:
					recRev = int64(v)
				case int64:
					recRev = v
				case int:
					recRev = int64(v)
				}

				var compRestoredRev int64
				if rd.undone && compMap != nil {
					if actComp, ok := compMap[actUUIDStr].(map[string]any); ok {
						if actComp["type"] == "artifact_restored" {
							switch v := actComp["revision"].(type) {
							case float64:
								compRestoredRev = int64(v)
							case int64:
								compRestoredRev = v
							case int:
								compRestoredRev = int64(v)
							}
						}
					}
				}

				histRows, err := tx.Query(ctx, `
					SELECT revision, state
					FROM cairn.work_artifact_link_history
					WHERE link_id = $1 AND revision >= $2
					ORDER BY revision ASC
				`, linkUUID, recRev)
				if err != nil {
					continue
				}

				var taintedRevs []int64
				tainted := false
				for histRows.Next() {
					var rev int64
					var st string
					if err := histRows.Scan(&rev, &st); err != nil {
						break
					}
					if rev == recRev {
						taintedRevs = append(taintedRevs, rev)
						tainted = true
						continue
					}
					if compRestoredRev > 0 && rev == compRestoredRev {
						tainted = false
						break
					} else if st == "removed" && tainted {
						taintedRevs = append(taintedRevs, rev)
					} else if st == "active" {
						tainted = false
						break
					}
				}
				histRows.Close()

				if len(taintedRevs) > 0 {
					_, _ = tx.Exec(ctx, `
						UPDATE cairn.work_artifact_link_history
						SET rationale = '[purged due to source deletion]'
						WHERE link_id = $1 AND revision = ANY($2)
					`, linkUUID, taintedRevs)

					_, _ = tx.Exec(ctx, `
						UPDATE cairn.work_artifact_link
						SET rationale = '[purged due to source deletion]'
						WHERE link_id = $1 AND revision = ANY($2)
					`, linkUUID, taintedRevs)

					_, _ = tx.Exec(ctx, `
						UPDATE cairn.mutation_request
						SET response = jsonb_set(response, '{rationale}', '"[purged due to source deletion]"'::jsonb)
						WHERE jsonb_typeof(response) = 'object'
						  AND (response->>'link_id') = $1::text
						  AND CASE WHEN jsonb_typeof(response->'revision') = 'number' THEN (response->>'revision')::bigint = ANY($2) ELSE false END
					`, linkUUID.String(), taintedRevs)
				}
			}
		}
	}

	// 7. Plus any annotation/history row whose source_references contains recordID
	_, _ = tx.Exec(ctx, `
		UPDATE cairn.work_annotation
		SET body = '[forgotten]'
		WHERE jsonb_path_exists(source_references, '$[*] ? (@ == $id)', jsonb_build_object('id', $1::text))
	`, idStr)
	_, _ = tx.Exec(ctx, `
		UPDATE cairn.work_annotation_history
		SET body = '[forgotten]'
		WHERE jsonb_path_exists(source_references, '$[*] ? (@ == $id)', jsonb_build_object('id', $1::text))
	`, idStr)

	return nil
}

// PurgeAdviceForWorkItemTx transactionally deletes all advice records for a given work item.
func (s *Store) PurgeAdviceForWorkItemTx(ctx context.Context, tx pgx.Tx, bindingID uuid.UUID, planeItemUUID uuid.UUID) error {
	_, err := tx.Exec(ctx, `
		DELETE FROM cairn.work_advice_application_receipt
		WHERE proposal_uuid IN (
			SELECT proposal_uuid FROM cairn.work_advice_proposal
			WHERE binding_id = $1 AND plane_item_uuid = $2
		)
	`, bindingID, planeItemUUID)
	if err != nil {
		return err
	}

	_, err = tx.Exec(ctx, `
		DELETE FROM cairn.work_advice_action
		WHERE proposal_uuid IN (
			SELECT proposal_uuid FROM cairn.work_advice_proposal
			WHERE binding_id = $1 AND plane_item_uuid = $2
		)
	`, bindingID, planeItemUUID)
	if err != nil {
		return err
	}

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
	return err
}

// ReconciledAdviceJob describes an advice job reconciled on startup or manual sweep.
type ReconciledAdviceJob struct {
	JobUUID        uuid.UUID `json:"job_uuid"`
	BindingID      uuid.UUID `json:"binding_id"`
	PlaneItemUUID  uuid.UUID `json:"plane_item_uuid"`
	PreviousState  string    `json:"previous_state"`
	FinalState     string    `json:"final_state"`
	FinalStatus    string    `json:"final_status,omitempty"`
	FinalErrorCode string    `json:"final_error_code,omitempty"`
	ActionTaken    string    `json:"action_taken"`
	CleanupError   string    `json:"cleanup_error,omitempty"`
}

// ReconcileAdviceJobs reconciles running, pending, uncertain, or cleanup_unconfirmed advice jobs.
func (s *Store) ReconcileAdviceJobs(ctx context.Context) ([]ReconciledAdviceJob, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	var currentRestoreGen int64
	var dbNow time.Time
	err = tx.QueryRow(ctx, `SELECT generation, clock_timestamp() FROM cairn.retrieval_generation WHERE singleton = true FOR SHARE`).Scan(&currentRestoreGen, &dbNow)
	if err != nil {
		return nil, err
	}

	rows, err := tx.Query(ctx, `
		SELECT job_uuid, binding_id, plane_item_uuid, status, process_identity, created_at, started_at, deadline_seconds, input_manifest, error_code
		FROM cairn.work_advice_job
		WHERE status IN ('running', 'pending', 'cleanup_unconfirmed', 'uncertain')
		FOR UPDATE
	`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	type candidate struct {
		jobUUID         uuid.UUID
		bindingID       uuid.UUID
		planeItemUUID   uuid.UUID
		status          string
		processIdentity []byte
		createdAt       time.Time
		startedAt       *time.Time
		deadlineSeconds int
		inputManifest   []byte
		errorCode       *string
	}
	var candidates []candidate
	for rows.Next() {
		var c candidate
		if err := rows.Scan(&c.jobUUID, &c.bindingID, &c.planeItemUUID, &c.status, &c.processIdentity, &c.createdAt, &c.startedAt, &c.deadlineSeconds, &c.inputManifest, &c.errorCode); err != nil {
			return nil, err
		}
		candidates = append(candidates, c)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return nil, err
	}
	rows.Close()

	var results []ReconciledAdviceJob
	for _, c := range candidates {
		var ident advice.ProcessIdentity
		if len(c.processIdentity) > 0 {
			_ = json.Unmarshal(c.processIdentity, &ident)
		}
		var manifest WorkAdviceInputManifest
		hasManifest := false
		if len(c.inputManifest) > 0 {
			if mErr := json.Unmarshal(c.inputManifest, &manifest); mErr == nil {
				hasManifest = true
			}
		}

		oldRestoreGen := hasManifest && manifest.RestoreGeneration != currentRestoreGen
		refTime := c.createdAt
		if c.startedAt != nil {
			refTime = *c.startedAt
		}
		pastDeadline := c.deadlineSeconds > 0 && dbNow.After(refTime.Add(time.Duration(c.deadlineSeconds)*time.Second))
		controllerDead := hasManifest && isControllerProvenDead(manifest.Controller)
		hasChild := ident.PID > 0 || ident.PGID > 0 || ident.StartTicks > 0 || ident.BootID != ""

		// Preserve healthy in-flight jobs
		if (c.status == "pending" || c.status == "running") && !oldRestoreGen && !pastDeadline && !controllerDead {
			continue
		}

		var finalStatus, finalErrorCode, action, cleanupErr string
		if c.status == "uncertain" {
			var recErr error
			if hasChild {
				recErr = advice.RecoverProcessGroup(ctx, ident)
				if recErr != nil {
					cleanupErr = recErr.Error()
				}
			}
			finalStatus = "uncertain"
			finalErrorCode = "JOB_STATE_UNCERTAIN"
			if c.errorCode != nil && *c.errorCode != "" {
				finalErrorCode = *c.errorCode
			}
			if hasChild && recErr != nil {
				action = "retained_uncertain_cleanup_unconfirmed"
			} else {
				action = "reconciled_uncertain"
			}
		} else if c.status == "cleanup_unconfirmed" {
			recErr := advice.RecoverProcessGroup(ctx, ident)
			if recErr != nil {
				cleanupErr = recErr.Error()
				finalStatus = "cleanup_unconfirmed"
				finalErrorCode = "CLEANUP_UNCONFIRMED"
				action = "retained_cleanup_unconfirmed"
			} else {
				finalStatus = "failed"
				finalErrorCode = "RECONCILED_AT_STARTUP"
				if c.errorCode != nil && *c.errorCode != "" {
					finalErrorCode = *c.errorCode
				}
				action = "reconciled_cleanup_confirmed"
			}
		} else if c.status == "pending" && !hasChild {
			if oldRestoreGen {
				finalStatus = "failed"
				finalErrorCode = "RESTORE_FENCE_TRIGGERED"
				action = "reconciled_restore_fenced_pending"
			} else if pastDeadline {
				finalStatus = "timed_out"
				finalErrorCode = "TIMEOUT"
				action = "reconciled_timed_out_pending"
			} else {
				finalStatus = "failed"
				finalErrorCode = "RECONCILED_AT_STARTUP"
				action = "reconciled_dead_controller_pending"
			}
		} else {
			recErr := advice.RecoverProcessGroup(ctx, ident)
			if recErr != nil {
				finalStatus = "cleanup_unconfirmed"
				finalErrorCode = "CLEANUP_UNCONFIRMED"
				action = "retained_cleanup_unconfirmed"
			} else if oldRestoreGen {
				finalStatus = "failed"
				finalErrorCode = "RESTORE_FENCE_TRIGGERED"
				action = "reconciled_restore_fenced_running"
			} else if pastDeadline {
				finalStatus = "timed_out"
				finalErrorCode = "MODEL_TIMEOUT"
				action = "reconciled_timed_out_running"
			} else {
				finalStatus = "failed"
				finalErrorCode = "RECONCILED_AT_STARTUP"
				action = "reconciled_dead_controller_running"
			}
		}

		_, err = tx.Exec(ctx, `
			UPDATE cairn.work_advice_job
			SET status = $2, finished_at = clock_timestamp(), error_code = $3
			WHERE job_uuid = $1
		`, c.jobUUID, finalStatus, finalErrorCode)
		if err != nil {
			return nil, err
		}

		_, err = tx.Exec(ctx, `
			UPDATE cairn.work_advice_request_tombstone
			SET terminal_status = $2, error_code = $3, updated_at = clock_timestamp()
			WHERE job_uuid = $1
		`, c.jobUUID, finalStatus, finalErrorCode)
		if err != nil {
			return nil, err
		}

		results = append(results, ReconciledAdviceJob{
			JobUUID:        c.jobUUID,
			BindingID:      c.bindingID,
			PlaneItemUUID:  c.planeItemUUID,
			PreviousState:  c.status,
			FinalState:     finalStatus,
			FinalStatus:    finalStatus,
			FinalErrorCode: finalErrorCode,
			ActionTaken:    action,
			CleanupError:   cleanupErr,
		})
	}

	return results, tx.Commit(ctx)
}

// SweepExpiredAdvice marks unreviewed proposals past expiry as 'expired', past-deadline jobs as 'timed_out',
// and scrubs old proposal payloads per retention policy.
func (s *Store) SweepExpiredAdvice(ctx context.Context) (int64, error) {
	if err := s.requireOperator(); err != nil {
		return 0, err
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback(ctx)

	var totalSwept int64

	// 1. Expire unreviewed pending or partially applied proposals past proposal expiry (1 hour after snapshot capture)
	tag1, err := tx.Exec(ctx, `
		UPDATE cairn.work_advice_proposal
		SET status = 'expired'
		WHERE status IN ('pending', 'partially_applied') AND expires_at < clock_timestamp()
	`)
	if err != nil {
		return 0, err
	}
	totalSwept += tag1.RowsAffected()

	// 2. Mark pending jobs past deadline 'timed_out' ONLY when process_identity = '{}'::jsonb (unlaunched)
	rows2, err := tx.Query(ctx, `
		UPDATE cairn.work_advice_job
		SET status = 'timed_out', finished_at = clock_timestamp(), error_code = 'TIMEOUT'
		WHERE status = 'pending' AND (process_identity IS NULL OR process_identity = '{}'::jsonb)
		  AND created_at + (deadline_seconds || ' seconds')::interval < clock_timestamp()
		RETURNING job_uuid
	`)
	if err != nil {
		return 0, err
	}
	defer rows2.Close()
	var sweptJobUUIDs []uuid.UUID
	for rows2.Next() {
		var jID uuid.UUID
		if err := rows2.Scan(&jID); err != nil {
			return 0, err
		}
		sweptJobUUIDs = append(sweptJobUUIDs, jID)
	}
	if err := rows2.Err(); err != nil {
		return 0, err
	}
	rows2.Close()
	totalSwept += int64(len(sweptJobUUIDs))

	if len(sweptJobUUIDs) > 0 {
		_, err = tx.Exec(ctx, `
			UPDATE cairn.work_advice_request_tombstone
			SET terminal_status = 'timed_out', error_code = 'TIMEOUT', updated_at = clock_timestamp()
			WHERE job_uuid = ANY($1)
		`, sweptJobUUIDs)
		if err != nil {
			return 0, err
		}
	}

	// 3. Clear 24h expired input snapshots
	tag3, err := tx.Exec(ctx, `
		UPDATE cairn.work_advice_job
		SET input_snapshot = NULL,
		    input_unavailable_reason = 'EXPIRED'
		WHERE input_snapshot IS NOT NULL AND input_expires_at < clock_timestamp()
	`)
	if err != nil {
		return 0, err
	}
	totalSwept += tag3.RowsAffected()

	// 4. 7-day retention: scrub raw result and normalized payload on proposals, target_payload and rationale on actions
	tag4, err := tx.Exec(ctx, `
		UPDATE cairn.work_advice_proposal
		SET raw_result = '', normalized_payload = '{}'::jsonb
		WHERE created_at < clock_timestamp() - interval '7 days'
		  AND (raw_result != '' OR normalized_payload != '{}'::jsonb)
	`)
	if err != nil {
		return 0, err
	}
	totalSwept += tag4.RowsAffected()

	tag5, err := tx.Exec(ctx, `
		UPDATE cairn.work_advice_action
		SET target_payload = '{}'::jsonb, rationale = ''
		WHERE created_at < clock_timestamp() - interval '7 days'
		  AND (target_payload != '{}'::jsonb OR rationale != '')
	`)
	if err != nil {
		return 0, err
	}
	totalSwept += tag5.RowsAffected()

	// 5. 30-day retention for non-active jobs (not pending, running, cleanup_unconfirmed, uncertain):
	// If 0 receipts: delete actions, proposals, and jobs.
	tag6, err := tx.Exec(ctx, `
		DELETE FROM cairn.work_advice_action
		WHERE proposal_uuid IN (
			SELECT p.proposal_uuid FROM cairn.work_advice_proposal p
			JOIN cairn.work_advice_job j ON j.job_uuid = p.job_uuid
			WHERE j.status NOT IN ('pending', 'running', 'cleanup_unconfirmed', 'uncertain')
			  AND j.created_at < clock_timestamp() - interval '30 days'
			  AND NOT EXISTS (
				  SELECT 1 FROM cairn.work_advice_application_receipt r
				  WHERE r.proposal_uuid = p.proposal_uuid
			  )
		)
	`)
	if err != nil {
		return 0, err
	}
	totalSwept += tag6.RowsAffected()

	tag7, err := tx.Exec(ctx, `
		DELETE FROM cairn.work_advice_proposal
		WHERE job_uuid IN (
			SELECT j.job_uuid FROM cairn.work_advice_job j
			WHERE j.status NOT IN ('pending', 'running', 'cleanup_unconfirmed', 'uncertain')
			  AND j.created_at < clock_timestamp() - interval '30 days'
			  AND NOT EXISTS (
				  SELECT 1 FROM cairn.work_advice_proposal p
				  JOIN cairn.work_advice_application_receipt r ON r.proposal_uuid = p.proposal_uuid
				  WHERE p.job_uuid = j.job_uuid
			  )
		)
	`)
	if err != nil {
		return 0, err
	}
	totalSwept += tag7.RowsAffected()

	tag8, err := tx.Exec(ctx, `
		DELETE FROM cairn.work_advice_job
		WHERE status NOT IN ('pending', 'running', 'cleanup_unconfirmed', 'uncertain')
		  AND created_at < clock_timestamp() - interval '30 days'
		  AND NOT EXISTS (
			  SELECT 1 FROM cairn.work_advice_proposal p
			  JOIN cairn.work_advice_application_receipt r ON r.proposal_uuid = p.proposal_uuid
			  WHERE p.job_uuid = cairn.work_advice_job.job_uuid
		  )
	`)
	if err != nil {
		return 0, err
	}
	totalSwept += tag8.RowsAffected()

	// If >=1 receipt: ensure payload columns are cleared on >=30d jobs/proposals/actions while keeping minimal metadata rows
	tag9, err := tx.Exec(ctx, `
		UPDATE cairn.work_advice_job
		SET input_snapshot = NULL,
		    input_unavailable_reason = COALESCE(input_unavailable_reason, 'EXPIRED')
		WHERE status NOT IN ('pending', 'running', 'cleanup_unconfirmed', 'uncertain')
		  AND created_at < clock_timestamp() - interval '30 days'
		  AND input_snapshot IS NOT NULL
	`)
	if err != nil {
		return 0, err
	}
	totalSwept += tag9.RowsAffected()

	return totalSwept, tx.Commit(ctx)
}

// RegisterAdviceProfile registers an immutable advice profile in PostgreSQL.
// Re-registering an identical profile is idempotent. Differing configuration returns CONFLICT.
func (s *Store) RegisterAdviceProfile(ctx context.Context, prof *advice.Profile) error {
	if err := s.requireOperator(); err != nil {
		return err
	}
	if prof == nil || prof.Name == "" {
		return failure("INVALID_REQUEST", "profile name is required")
	}
	if prof.Route != advice.RouteCodexSubscription {
		return failure("INVALID_REQUEST", fmt.Sprintf("unsupported route %q, only %s supported", prof.Route, advice.RouteCodexSubscription))
	}
	if prof.ExecutablePath == "" || prof.BinarySHA256 == "" || prof.CodexHome == "" || prof.Model == "" {
		return failure("INVALID_REQUEST", "executable_path, binary_sha256, codex_home, and model are required")
	}
	if prof.TimeoutMs <= 0 {
		prof.TimeoutMs = advice.DefaultCodexTimeoutMs
	}
	if prof.MaxOutputBytes <= 0 {
		prof.MaxOutputBytes = advice.DefaultCodexMaxBytes
	}
	if prof.ExpectedUpstreamCommit == "" {
		prof.ExpectedUpstreamCommit = advice.RequiredCodexUpstreamCommit
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	var existing advice.Profile
	err = tx.QueryRow(ctx, `
		SELECT profile_name, route, executable_path, binary_sha256,
		       expected_upstream_commit, expected_patch_sha256, codex_home,
		       model, timeout_ms, max_output_bytes, created_at
		FROM cairn.work_advice_profile
		WHERE profile_name = $1
		FOR UPDATE
	`, prof.Name).Scan(
		&existing.Name, &existing.Route, &existing.ExecutablePath, &existing.BinarySHA256,
		&existing.ExpectedUpstreamCommit, &existing.ExpectedPatchSHA256, &existing.CodexHome,
		&existing.Model, &existing.TimeoutMs, &existing.MaxOutputBytes, &existing.CreatedAt,
	)
	if err == nil {
		if existing.Route != prof.Route ||
			existing.ExecutablePath != prof.ExecutablePath ||
			existing.BinarySHA256 != prof.BinarySHA256 ||
			existing.ExpectedUpstreamCommit != prof.ExpectedUpstreamCommit ||
			existing.ExpectedPatchSHA256 != prof.ExpectedPatchSHA256 ||
			existing.CodexHome != prof.CodexHome ||
			existing.Model != prof.Model ||
			existing.TimeoutMs != prof.TimeoutMs ||
			existing.MaxOutputBytes != prof.MaxOutputBytes {
			return failure("CONFLICT", fmt.Sprintf("advice profile %q is already registered with different configuration", prof.Name))
		}
		prof.CreatedAt = existing.CreatedAt
		return tx.Commit(ctx)
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return err
	}

	err = tx.QueryRow(ctx, `
		INSERT INTO cairn.work_advice_profile (
			profile_name, route, executable_path, binary_sha256,
			expected_upstream_commit, expected_patch_sha256, codex_home,
			model, timeout_ms, max_output_bytes, created_at
		) VALUES (
			$1, $2, $3, $4,
			$5, $6, $7,
			$8, $9, $10, clock_timestamp()
		) RETURNING created_at
	`, prof.Name, prof.Route, prof.ExecutablePath, prof.BinarySHA256,
		prof.ExpectedUpstreamCommit, prof.ExpectedPatchSHA256, prof.CodexHome,
		prof.Model, prof.TimeoutMs, prof.MaxOutputBytes).Scan(&prof.CreatedAt)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}

// GetAdviceProfile retrieves a registered advice profile by name.
func (s *Store) GetAdviceProfile(ctx context.Context, profileName string) (*advice.Profile, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}
	var prof advice.Profile
	err := s.pool.QueryRow(ctx, `
		SELECT profile_name, route, executable_path, binary_sha256,
		       expected_upstream_commit, expected_patch_sha256, codex_home,
		       model, timeout_ms, max_output_bytes, created_at
		FROM cairn.work_advice_profile
		WHERE profile_name = $1
	`, profileName).Scan(
		&prof.Name, &prof.Route, &prof.ExecutablePath, &prof.BinarySHA256,
		&prof.ExpectedUpstreamCommit, &prof.ExpectedPatchSHA256, &prof.CodexHome,
		&prof.Model, &prof.TimeoutMs, &prof.MaxOutputBytes, &prof.CreatedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, failure("NOT_FOUND", fmt.Sprintf("advice profile %q not found", profileName))
	}
	if err != nil {
		return nil, err
	}
	return &prof, nil
}

// ListAdviceProfiles retrieves all registered advice profiles ordered by name.
func (s *Store) ListAdviceProfiles(ctx context.Context) ([]*advice.Profile, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}
	rows, err := s.pool.Query(ctx, `
		SELECT profile_name, route, executable_path, binary_sha256,
		       expected_upstream_commit, expected_patch_sha256, codex_home,
		       model, timeout_ms, max_output_bytes, created_at
		FROM cairn.work_advice_profile
		ORDER BY profile_name ASC
	`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var profiles []*advice.Profile
	for rows.Next() {
		var prof advice.Profile
		if err := rows.Scan(
			&prof.Name, &prof.Route, &prof.ExecutablePath, &prof.BinarySHA256,
			&prof.ExpectedUpstreamCommit, &prof.ExpectedPatchSHA256, &prof.CodexHome,
			&prof.Model, &prof.TimeoutMs, &prof.MaxOutputBytes, &prof.CreatedAt,
		); err != nil {
			return nil, err
		}
		profiles = append(profiles, &prof)
	}
	return profiles, rows.Err()
}

func computeRequestDigest(job *WorkAdviceJob, spec AdviceInputSpec) string {
	var srcStrs []string
	for _, s := range spec.SelectedSources {
		srcStrs = append(srcStrs, fmt.Sprintf("%s:%d", s.RecordID, s.Version))
	}
	sort.Strings(srcStrs)
	var candStrs []string
	for _, c := range spec.CandidateRevisionIDs {
		candStrs = append(candStrs, c.String())
	}
	sort.Strings(candStrs)
	raw := fmt.Sprintf("%s|%s|%s|%s|%d|%d|%s|%s",
		job.BindingID, job.PlaneItemUUID, job.ProfileName, job.ModelRoute,
		job.TemplateVersion, job.DeadlineSeconds,
		strings.Join(srcStrs, ","), strings.Join(candStrs, ","))
	h := sha256.Sum256([]byte(raw))
	return hex.EncodeToString(h[:])
}

// CheckSourceEligibleLive checks whether a source record exists and is shareable and undeleted.
func (s *Store) CheckSourceEligibleLive(ctx context.Context, recordID uuid.UUID, version int) bool {
	var sensitivity, lifecycle string
	var payloadDeletedBy *string
	err := s.pool.QueryRow(ctx, `
		SELECT m.sensitivity, m.lifecycle, v.payload_deleted_by
		FROM cairn.memory_record m
		JOIN cairn.record_version v ON v.record_id = m.record_id AND v.version = $2
		WHERE m.record_id = $1
	`, recordID.String(), version).Scan(&sensitivity, &lifecycle, &payloadDeletedBy)
	if err != nil || payloadDeletedBy != nil || sensitivity != "shareable" || lifecycle != "active" {
		return false
	}
	return true
}

// GetArtifactRevisionDetail returns metadata about a candidate artifact revision.
func (s *Store) GetArtifactRevisionDetail(ctx context.Context, revID uuid.UUID) (artifactID uuid.UUID, kind string, displayName string, payloadAvailable bool, err error) {
	err = s.pool.QueryRow(ctx, `
		SELECT a.artifact_id, a.kind, COALESCE(a.display_name, COALESCE(a.report_locator, COALESCE(a.git_repo, ''))), true
		FROM cairn.work_artifact_revision r
		JOIN cairn.work_artifact a ON a.artifact_id = r.artifact_id
		WHERE r.revision_id = $1
	`, revID).Scan(&artifactID, &kind, &displayName, &payloadAvailable)
	if errors.Is(err, pgx.ErrNoRows) {
		return uuid.Nil, "artifact", revID.String()[:8], false, nil
	}
	if displayName == "" {
		displayName = revID.String()[:8]
	}
	return artifactID, kind, displayName, payloadAvailable, err
}

// VerifyAdviceCurrentness checks whether a proposal's observation manifest is still current with live database rows.
func (s *Store) VerifyAdviceCurrentness(ctx context.Context, proposalUUID uuid.UUID) error {
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	var bindingID, planeItemUUID uuid.UUID
	err = tx.QueryRow(ctx, `SELECT binding_id, plane_item_uuid FROM cairn.work_advice_proposal WHERE proposal_uuid = $1`, proposalUUID).Scan(&bindingID, &planeItemUUID)
	if err != nil {
		return err
	}
	var collection string
	err = tx.QueryRow(ctx, `SELECT collection FROM cairn.work_project WHERE binding_id = $1`, bindingID).Scan(&collection)
	if err != nil {
		return err
	}
	var manifestRaw []byte
	err = tx.QueryRow(ctx, `
		SELECT j.input_manifest
		FROM cairn.work_advice_proposal p
		JOIN cairn.work_advice_job j ON j.job_uuid = p.job_uuid
		WHERE p.proposal_uuid = $1
	`, proposalUUID).Scan(&manifestRaw)
	if err != nil {
		return err
	}
	var manifest WorkAdviceInputManifest
	if err := json.Unmarshal(manifestRaw, &manifest); err != nil {
		return err
	}
	if err := s.verifyAdviceManifestTx(ctx, tx, bindingID, planeItemUUID, collection, &manifest, proposalUUID); err != nil {
		return err
	}

	actionRows, err := tx.Query(ctx, `
		SELECT target_payload
		FROM cairn.work_advice_action
		WHERE proposal_uuid = $1 AND action_type = 'annotate_blocker' AND status = 'pending'
	`, proposalUUID)
	if err != nil {
		return err
	}
	defer actionRows.Close()

	var payloads [][]byte
	for actionRows.Next() {
		var payloadBytes []byte
		if err := actionRows.Scan(&payloadBytes); err != nil {
			return err
		}
		payloads = append(payloads, payloadBytes)
	}
	if err := actionRows.Err(); err != nil {
		return err
	}
	actionRows.Close()

	for _, payloadBytes := range payloads {
		var b struct {
			BlockerAction     string `json:"blocker_action"`
			ExistingBlockerID string `json:"existing_blocker_id"`
			ExpectedRevision  int64  `json:"expected_revision"`
		}
		if err := json.Unmarshal(payloadBytes, &b); err == nil && b.BlockerAction == "resolve" {
			bID, err := uuid.Parse(b.ExistingBlockerID)
			if err != nil {
				continue
			}
			var curRev int64
			var curState string
			err = tx.QueryRow(ctx, `
				SELECT revision, state
				FROM cairn.work_annotation
				WHERE annotation_id = $1
				FOR SHARE
			`, bID).Scan(&curRev, &curState)
			if errors.Is(err, pgx.ErrNoRows) {
				return failure("STALE_OBSERVATION", fmt.Sprintf("blocker %s was removed after advice generation", b.ExistingBlockerID))
			}
			if err != nil {
				return err
			}
			if curRev != b.ExpectedRevision || curState != "open" {
				return failure("STALE_OBSERVATION", fmt.Sprintf("blocker %s modified or resolved after advice generation", b.ExistingBlockerID))
			}
		}
	}
	return nil
}

// CheckReceiptUndoable checks whether the output entities of an application receipt can still be undone without conflict.
func (s *Store) CheckReceiptUndoable(ctx context.Context, requestUUID uuid.UUID) error {
	var outputRevsRaw json.RawMessage
	err := s.pool.QueryRow(ctx, `SELECT output_revisions FROM cairn.work_advice_application_receipt WHERE request_uuid = $1`, requestUUID).Scan(&outputRevsRaw)
	if err != nil {
		return err
	}
	var outputRevs map[string]map[string]any
	if len(outputRevsRaw) > 0 {
		_ = json.Unmarshal(outputRevsRaw, &outputRevs)
	}
	for _, revInfo := range outputRevs {
		actionType, _ := revInfo["type"].(string)
		switch actionType {
		case "progress":
			annotIDStr, _ := revInfo["annotation_id"].(string)
			targetID, _ := uuid.Parse(annotIDStr)
			recordedRev := int64(revInfo["revision"].(float64))
			var curRev int64
			var curState string
			err = s.pool.QueryRow(ctx, `SELECT revision, state FROM cairn.work_annotation WHERE annotation_id = $1`, targetID).Scan(&curRev, &curState)
			if err != nil || curRev != recordedRev || curState != "active" {
				return failure("UNDO_CONFLICT", "progress annotation changed")
			}
		case "blocker_created":
			annotIDStr, _ := revInfo["annotation_id"].(string)
			targetID, _ := uuid.Parse(annotIDStr)
			recordedRev := int64(revInfo["revision"].(float64))
			var curRev int64
			var curState string
			err = s.pool.QueryRow(ctx, `SELECT revision, state FROM cairn.work_annotation WHERE annotation_id = $1`, targetID).Scan(&curRev, &curState)
			if err != nil || curRev != recordedRev || curState != "open" {
				return failure("UNDO_CONFLICT", "blocker annotation changed")
			}
		case "blocker_resolved":
			annotIDStr, _ := revInfo["annotation_id"].(string)
			targetID, _ := uuid.Parse(annotIDStr)
			recordedRev := int64(revInfo["revision"].(float64))
			var curRev int64
			var curState string
			err = s.pool.QueryRow(ctx, `SELECT revision, state FROM cairn.work_annotation WHERE annotation_id = $1`, targetID).Scan(&curRev, &curState)
			if err != nil || curRev != recordedRev || curState != "resolved" {
				return failure("UNDO_CONFLICT", "blocker annotation changed")
			}
		case "artifact_linked":
			linkIDStr, _ := revInfo["link_id"].(string)
			linkID, _ := uuid.Parse(linkIDStr)
			recordedRev := int64(revInfo["revision"].(float64))
			var curRev int64
			var curState string
			err = s.pool.QueryRow(ctx, `SELECT revision, state FROM cairn.work_artifact_link WHERE link_id = $1`, linkID).Scan(&curRev, &curState)
			if err != nil || curRev != recordedRev || curState != "active" {
				return failure("UNDO_CONFLICT", "artifact link changed")
			}
		}
	}
	return nil
}
