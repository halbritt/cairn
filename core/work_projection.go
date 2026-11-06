package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/plane"
	"github.com/jackc/pgx/v5"
)

const FeatureWorkView = "work-view/1"

type WorkProject struct {
	BindingID                 uuid.UUID  `json:"binding_id"`
	LocalSlug                 string     `json:"local_slug"`
	Collection                string     `json:"collection"`
	PlaneOrigin               string     `json:"plane_origin"`
	WorkspaceUUID             uuid.UUID  `json:"workspace_uuid"`
	ProjectUUID               uuid.UUID  `json:"project_uuid"`
	ConfigRevision            string     `json:"config_revision"`
	PlaneInstance             string     `json:"plane_instance"`
	Status                    string     `json:"status"`
	MetadataExclusionRevision int64      `json:"metadata_exclusion_revision"`
	RefreshSequence           int64      `json:"refresh_sequence"`
	AuthRejected              bool       `json:"auth_rejected"`
	LastAuthError             string     `json:"last_auth_error,omitempty"`
	LastSuccessfulRefreshAt   *time.Time `json:"last_successful_refresh_at,omitempty"`
	RefreshInflightToken      *uuid.UUID `json:"-"`
	RefreshInflightExpiresAt  *time.Time `json:"refresh_inflight_expires_at,omitempty"`
	NextRefreshAllowedAt      *time.Time `json:"next_refresh_allowed_at,omitempty"`
	LastRefreshError          string     `json:"last_refresh_error,omitempty"`
	LastRefreshErrorAt        *time.Time `json:"last_refresh_error_at,omitempty"`
	RefreshInFlight           bool       `json:"refresh_in_flight"`
	IsStale                   bool       `json:"is_stale"`
	CreatedAt                 time.Time  `json:"created_at"`
	UpdatedAt                 time.Time  `json:"updated_at"`
}

func populateWorkProjectDerived(p *WorkProject, now time.Time) {
	if p.RefreshInflightToken != nil && p.RefreshInflightExpiresAt != nil && p.RefreshInflightExpiresAt.After(now) {
		p.RefreshInFlight = true
	} else {
		p.RefreshInFlight = false
		p.RefreshInflightExpiresAt = nil
	}
	p.IsStale = p.LastSuccessfulRefreshAt == nil || now.Sub(*p.LastSuccessfulRefreshAt) > 2*time.Minute || p.LastRefreshError != "" || p.AuthRejected
}

type AddWorkProjectRequest struct {
	RequestID      string    `json:"request_id,omitempty"`
	LocalSlug      string    `json:"local_slug"`
	Collection     string    `json:"collection"`
	PlaneInstance  string    `json:"plane_instance"`
	WorkspaceUUID  uuid.UUID `json:"workspace_uuid"`
	ProjectUUID    uuid.UUID `json:"project_uuid"`
	PlaneOrigin    string    `json:"plane_origin"`
	ConfigRevision string    `json:"config_revision"`
}

type WorkItem struct {
	ItemID          uuid.UUID  `json:"item_id"`
	BindingID       uuid.UUID  `json:"binding_id"`
	PlaneItemUUID   uuid.UUID  `json:"plane_item_uuid"`
	Identifier      string     `json:"identifier"`
	Title           string     `json:"title"`
	StateID         *uuid.UUID `json:"state_id,omitempty"`
	StateName       string     `json:"state_name,omitempty"`
	StateGroup      string     `json:"state_group,omitempty"`
	AssigneeIDs     []string   `json:"assignee_ids"`
	AssigneeNames   []string   `json:"assignee_names"`
	UpdatedAtSource *time.Time `json:"updated_at_source,omitempty"`
	SourceURL       string     `json:"source_url,omitempty"`
	CacheGeneration int64      `json:"cache_generation"`
	IsAbsent        bool       `json:"is_absent"`
	LastRefreshedAt *time.Time `json:"last_refreshed_at,omitempty"`
	RefreshError    string     `json:"refresh_error,omitempty"`
	IsStale         bool       `json:"is_stale"`
}

type WorkSnapshot struct {
	SnapshotID      uuid.UUID `json:"snapshot_id"`
	BindingID       uuid.UUID `json:"binding_id"`
	CacheGeneration int64     `json:"cache_generation"`
	ItemCount       int       `json:"item_count"`
	SnapshotDigest  string    `json:"snapshot_digest"`
	StagedAt        time.Time `json:"staged_at"`
	CommittedAt     time.Time `json:"committed_at"`
}

type AssociatedSession struct {
	AssociationID uuid.UUID  `json:"association_id"`
	Revision      int64      `json:"revision"`
	AgentID       uuid.UUID  `json:"agent_id"`
	ExecutionID   uuid.UUID  `json:"execution_id"`
	TaskSummary   string     `json:"task_summary"`
	IsHistorical  bool       `json:"is_historical"`
	Online        bool       `json:"online"`
	ExpiresAt     *time.Time `json:"expires_at,omitempty"`
	Rationale     string     `json:"rationale,omitempty"`
}

type EventDeliveryView struct {
	DeliveryID  uuid.UUID  `json:"delivery_id"`
	Consumer    string     `json:"consumer"`
	State       string     `json:"state"`
	Code        string     `json:"code"`
	CompletedAt *time.Time `json:"completed_at,omitempty"`
}

type AssociatedRequest struct {
	AssociationID    uuid.UUID           `json:"association_id"`
	Revision         int64               `json:"revision"`
	EventID          uuid.UUID           `json:"event_id"`
	EventKind        string              `json:"event_kind"`
	SourceRecordID   *uuid.UUID          `json:"source_record_id,omitempty"`
	SourceVersion    *int                `json:"source_version,omitempty"`
	DeliveryState    string              `json:"delivery_state"`
	Deliveries       []EventDeliveryView `json:"deliveries"`
	PayloadAvailable bool                `json:"payload_available"`
	PayloadStatus    string              `json:"payload_status"`
	Rationale        string              `json:"rationale,omitempty"`
}

type AssociatedResponseGroup struct {
	AssociationID    uuid.UUID  `json:"association_id"`
	Revision         int64      `json:"revision"`
	GroupEventID     uuid.UUID  `json:"group_event_id"`
	State            string     `json:"state"`
	Code             string     `json:"code"`
	PartialPolicy    string     `json:"partial_policy"`
	Deadline         *time.Time `json:"deadline,omitempty"`
	ClosedAt         *time.Time `json:"closed_at,omitempty"`
	MemberCount      int        `json:"member_count"`
	RespondedCount   int        `json:"responded_count"`
	PayloadAvailable bool       `json:"payload_available"`
	PayloadStatus    string     `json:"payload_status"`
	Rationale        string     `json:"rationale,omitempty"`
}

type AssociatedResultRecord struct {
	AssociationID    uuid.UUID `json:"association_id"`
	Revision         int64     `json:"revision"`
	RecordID         uuid.UUID `json:"record_id"`
	Version          int       `json:"version"`
	Lifecycle        string    `json:"lifecycle"`
	Kind             string    `json:"kind,omitempty"`
	Sensitivity      string    `json:"sensitivity,omitempty"`
	PayloadAvailable bool      `json:"payload_available"`
	PayloadStatus    string    `json:"payload_status"`
	Rationale        string    `json:"rationale,omitempty"`
}

type AssociatedRunReceipt struct {
	AssociationID     uuid.UUID  `json:"association_id"`
	Revision          int64      `json:"revision"`
	TargetID          string     `json:"target_id"`
	ReceiptID         *uuid.UUID `json:"receipt_id,omitempty"`
	Status            string     `json:"status"`
	WakeState         string     `json:"wake_state"`
	AssessmentOutcome string     `json:"assessment_outcome"`
	PayloadAvailable  bool       `json:"payload_available"`
	PayloadStatus     string     `json:"payload_status"`
	Rationale         string     `json:"rationale,omitempty"`
}

type WorkItemDetail struct {
	Item           WorkItem                  `json:"item"`
	ProjectSlug    string                    `json:"project_slug"`
	Collection     string                    `json:"collection"`
	AuthRejected   bool                      `json:"auth_rejected"`
	Sessions       []AssociatedSession       `json:"sessions"`
	Requests       []AssociatedRequest       `json:"requests"`
	ResponseGroups []AssociatedResponseGroup `json:"response_groups"`
	ResultRecords  []AssociatedResultRecord  `json:"result_records"`
	RunReceipts    []AssociatedRunReceipt    `json:"run_receipts"`
	Associations   []WorkAssociation         `json:"associations"`
	Annotations    []WorkAnnotation          `json:"annotations"`
	Artifacts      []WorkArtifactLink        `json:"artifacts,omitempty"`
}

type RefreshContext struct {
	BindingID                 uuid.UUID `json:"binding_id"`
	LocalSlug                 string    `json:"local_slug"`
	Collection                string    `json:"collection"`
	PlaneOrigin               string    `json:"plane_origin"`
	WorkspaceUUID             uuid.UUID `json:"workspace_uuid"`
	ProjectUUID               uuid.UUID `json:"project_uuid"`
	PlaneInstance             string    `json:"plane_instance"`
	ConfigRevision            string    `json:"config_revision"`
	DatabaseGeneration        int64     `json:"database_generation"`
	MetadataExclusionRevision int64     `json:"metadata_exclusion_revision"`
	RefreshSequence           int64     `json:"refresh_sequence"`
	ClaimToken                uuid.UUID `json:"claim_token"`
	StagedAt                  time.Time `json:"staged_at"`
}

func (s *Store) requireOperator() error {
	if !s.channel.Operator {
		return failure("AUTHORITY_DENIED", "operator channel required")
	}
	return nil
}

func (s *Store) AddWorkProject(ctx context.Context, req AddWorkProjectRequest) (WorkProject, error) {
	if err := s.requireOperator(); err != nil {
		return WorkProject{}, err
	}
	if strings.TrimSpace(req.LocalSlug) == "" {
		return WorkProject{}, failure("INVALID_REQUEST", "local slug required")
	}
	if strings.TrimSpace(req.Collection) == "" {
		return WorkProject{}, failure("INVALID_REQUEST", "collection required")
	}
	if err := s.checkRepo(req.Collection); err != nil {
		return WorkProject{}, err
	}
	if req.WorkspaceUUID == uuid.Nil || req.ProjectUUID == uuid.Nil {
		return WorkProject{}, failure("INVALID_REQUEST", "valid workspace and project UUID required")
	}
	if strings.TrimSpace(req.PlaneOrigin) == "" || strings.TrimSpace(req.ConfigRevision) == "" {
		return WorkProject{}, failure("INVALID_REQUEST", "pinned Plane origin and config revision required")
	}

	normOrigin, err := plane.NormalizeOrigin(req.PlaneOrigin)
	if err != nil {
		return WorkProject{}, failure("INVALID_REQUEST", err.Error())
	}
	req.PlaneOrigin = normOrigin
	if req.RequestID == "" {
		req.RequestID = uuid.NewString()
	}

	return mutate(ctx, s, "work-project-add", req.RequestID, req, func(tx pgx.Tx) (WorkProject, error) {
		if err := s.checkWorkAdmissionTx(ctx, tx); err != nil {
			return WorkProject{}, err
		}

		// Check if this (collection, plane_origin, workspace, project) tuple already exists (STORE-2)
		var existing WorkProject
		var lastRefresh *time.Time
		var lastAuthErr *string
		err = tx.QueryRow(ctx, `SELECT binding_id, local_slug, collection, plane_origin, workspace_uuid, project_uuid, config_revision, plane_instance, status, metadata_exclusion_revision, refresh_sequence, auth_rejected, last_auth_error, last_successful_refresh_at, created_at, updated_at
			FROM cairn.work_project WHERE collection = $1 AND plane_origin = $2 AND workspace_uuid = $3 AND project_uuid = $4`,
			req.Collection, req.PlaneOrigin, req.WorkspaceUUID, req.ProjectUUID).Scan(
			&existing.BindingID, &existing.LocalSlug, &existing.Collection, &existing.PlaneOrigin,
			&existing.WorkspaceUUID, &existing.ProjectUUID, &existing.ConfigRevision, &existing.PlaneInstance,
			&existing.Status, &existing.MetadataExclusionRevision, &existing.RefreshSequence, &existing.AuthRejected,
			&lastAuthErr, &lastRefresh, &existing.CreatedAt, &existing.UpdatedAt)

		if err == nil {
			if lastAuthErr != nil {
				existing.LastAuthError = *lastAuthErr
			}
			existing.LastSuccessfulRefreshAt = lastRefresh
			if existing.LocalSlug == req.LocalSlug {
				return existing, nil
			}
			return WorkProject{}, failure("INVALID_REQUEST", fmt.Sprintf("external project is already bound to local slug %q in collection %q", existing.LocalSlug, req.Collection))
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return WorkProject{}, err
		}

		// Check if local slug is taken within this collection (STORE-2, STORE-4)
		var slugCount int
		err = tx.QueryRow(ctx, `SELECT count(*) FROM cairn.work_project WHERE collection = $1 AND local_slug = $2`, req.Collection, req.LocalSlug).Scan(&slugCount)
		if err != nil {
			return WorkProject{}, err
		}
		if slugCount > 0 {
			return WorkProject{}, failure("INVALID_REQUEST", fmt.Sprintf("local slug %q is already in use in collection %q", req.LocalSlug, req.Collection))
		}

		var p WorkProject
		p.BindingID = uuid.New()
		p.LocalSlug = req.LocalSlug
		p.Collection = req.Collection
		p.PlaneOrigin = req.PlaneOrigin
		p.WorkspaceUUID = req.WorkspaceUUID
		p.ProjectUUID = req.ProjectUUID
		p.ConfigRevision = req.ConfigRevision
		p.PlaneInstance = req.PlaneInstance
		p.Status = "active"

		err = tx.QueryRow(ctx, `INSERT INTO cairn.work_project (binding_id, local_slug, collection, plane_origin, workspace_uuid, project_uuid, config_revision, plane_instance, status)
			VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
			RETURNING metadata_exclusion_revision, refresh_sequence, auth_rejected, created_at, updated_at`,
			p.BindingID, p.LocalSlug, p.Collection, p.PlaneOrigin, p.WorkspaceUUID, p.ProjectUUID, p.ConfigRevision, p.PlaneInstance, p.Status).
			Scan(&p.MetadataExclusionRevision, &p.RefreshSequence, &p.AuthRejected, &p.CreatedAt, &p.UpdatedAt)
		if err != nil {
			return WorkProject{}, err
		}

		return p, nil
	})
}

func (s *Store) GetWorkProject(ctx context.Context, collection string, slug string) (WorkProject, error) {
	if err := s.requireOperator(); err != nil {
		return WorkProject{}, err
	}
	if err := s.checkRepo(collection); err != nil {
		return WorkProject{}, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return WorkProject{}, err
	}
	defer tx.Rollback(ctx)

	if err := s.checkWorkReadableTx(ctx, tx); err != nil {
		return WorkProject{}, err
	}

	var p WorkProject
	var lastRefresh, inflightExpires, nextAllowed, lastErrAt *time.Time
	var lastAuthErr, lastRefreshErr *string
	err = tx.QueryRow(ctx, `SELECT binding_id, local_slug, collection, plane_origin, workspace_uuid, project_uuid, config_revision, plane_instance, status, metadata_exclusion_revision, refresh_sequence, auth_rejected, last_auth_error, last_successful_refresh_at, refresh_inflight_token, refresh_inflight_expires_at, next_refresh_allowed_at, last_refresh_error, last_refresh_error_at, created_at, updated_at
		FROM cairn.work_project WHERE collection = $1 AND local_slug = $2`, collection, slug).Scan(
		&p.BindingID, &p.LocalSlug, &p.Collection, &p.PlaneOrigin,
		&p.WorkspaceUUID, &p.ProjectUUID, &p.ConfigRevision, &p.PlaneInstance,
		&p.Status, &p.MetadataExclusionRevision, &p.RefreshSequence, &p.AuthRejected,
		&lastAuthErr, &lastRefresh,
		&p.RefreshInflightToken, &inflightExpires, &nextAllowed, &lastRefreshErr, &lastErrAt,
		&p.CreatedAt, &p.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return WorkProject{}, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", slug, collection))
	}
	if err != nil {
		return WorkProject{}, err
	}
	if lastAuthErr != nil {
		p.LastAuthError = *lastAuthErr
	}
	if lastRefreshErr != nil {
		p.LastRefreshError = *lastRefreshErr
	}
	p.LastSuccessfulRefreshAt = lastRefresh
	p.RefreshInflightExpiresAt = inflightExpires
	p.NextRefreshAllowedAt = nextAllowed
	p.LastRefreshErrorAt = lastErrAt
	populateWorkProjectDerived(&p, time.Now())
	return p, tx.Commit(ctx)
}

func (s *Store) DisableWorkProject(ctx context.Context, collection string, slug string, requestID string) (WorkProject, error) {
	if err := s.requireOperator(); err != nil {
		return WorkProject{}, err
	}
	if err := s.checkRepo(collection); err != nil {
		return WorkProject{}, err
	}
	req := struct {
		Collection string `json:"collection"`
		LocalSlug  string `json:"local_slug"`
	}{Collection: collection, LocalSlug: slug}

	return mutate(ctx, s, "work-project-disable", requestID, req, func(tx pgx.Tx) (WorkProject, error) {
		if err := s.checkWorkReadableTx(ctx, tx); err != nil {
			return WorkProject{}, err
		}

		var p WorkProject
		var lastRefresh, inflightExpires, nextAllowed, lastErrAt *time.Time
		var lastAuthErr, lastRefreshErr *string
		err := tx.QueryRow(ctx, `UPDATE cairn.work_project SET status='disabled', updated_at=clock_timestamp()
			WHERE collection = $1 AND local_slug = $2
			RETURNING binding_id, local_slug, collection, plane_origin, workspace_uuid, project_uuid, config_revision, plane_instance, status, metadata_exclusion_revision, refresh_sequence, auth_rejected, last_auth_error, last_successful_refresh_at, refresh_inflight_token, refresh_inflight_expires_at, next_refresh_allowed_at, last_refresh_error, last_refresh_error_at, created_at, updated_at`, collection, slug).Scan(
			&p.BindingID, &p.LocalSlug, &p.Collection, &p.PlaneOrigin,
			&p.WorkspaceUUID, &p.ProjectUUID, &p.ConfigRevision, &p.PlaneInstance,
			&p.Status, &p.MetadataExclusionRevision, &p.RefreshSequence, &p.AuthRejected,
			&lastAuthErr, &lastRefresh,
			&p.RefreshInflightToken, &inflightExpires, &nextAllowed, &lastRefreshErr, &lastErrAt,
			&p.CreatedAt, &p.UpdatedAt)
		if errors.Is(err, pgx.ErrNoRows) {
			return WorkProject{}, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", slug, collection))
		}
		if err != nil {
			return WorkProject{}, err
		}
		if lastAuthErr != nil {
			p.LastAuthError = *lastAuthErr
		}
		if lastRefreshErr != nil {
			p.LastRefreshError = *lastRefreshErr
		}
		p.LastSuccessfulRefreshAt = lastRefresh
		p.RefreshInflightExpiresAt = inflightExpires
		p.NextRefreshAllowedAt = nextAllowed
		p.LastRefreshErrorAt = lastErrAt
		populateWorkProjectDerived(&p, time.Now())
		return p, nil
	})
}

func (s *Store) ListWorkProjects(ctx context.Context, collection string) ([]WorkProject, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}
	if err := s.checkRepo(collection); err != nil {
		return nil, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	if err := s.checkWorkReadableTx(ctx, tx); err != nil {
		return nil, err
	}

	rows, err := tx.Query(ctx, `SELECT binding_id, local_slug, collection, plane_origin, workspace_uuid, project_uuid, config_revision, plane_instance, status, metadata_exclusion_revision, refresh_sequence, auth_rejected, last_auth_error, last_successful_refresh_at, refresh_inflight_token, refresh_inflight_expires_at, next_refresh_allowed_at, last_refresh_error, last_refresh_error_at, created_at, updated_at
		FROM cairn.work_project WHERE collection = $1 ORDER BY local_slug`, collection)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	projects := make([]WorkProject, 0)
	now := time.Now()
	for rows.Next() {
		var p WorkProject
		var lastRefresh, inflightExpires, nextAllowed, lastErrAt *time.Time
		var lastAuthErr, lastRefreshErr *string
		if err := rows.Scan(&p.BindingID, &p.LocalSlug, &p.Collection, &p.PlaneOrigin,
			&p.WorkspaceUUID, &p.ProjectUUID, &p.ConfigRevision, &p.PlaneInstance,
			&p.Status, &p.MetadataExclusionRevision, &p.RefreshSequence, &p.AuthRejected,
			&lastAuthErr, &lastRefresh,
			&p.RefreshInflightToken, &inflightExpires, &nextAllowed, &lastRefreshErr, &lastErrAt,
			&p.CreatedAt, &p.UpdatedAt); err != nil {
			return nil, err
		}
		if lastAuthErr != nil {
			p.LastAuthError = *lastAuthErr
		}
		if lastRefreshErr != nil {
			p.LastRefreshError = *lastRefreshErr
		}
		p.LastSuccessfulRefreshAt = lastRefresh
		p.RefreshInflightExpiresAt = inflightExpires
		p.NextRefreshAllowedAt = nextAllowed
		p.LastRefreshErrorAt = lastErrAt
		populateWorkProjectDerived(&p, now)
		projects = append(projects, p)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return projects, tx.Commit(ctx)
}

func (s *Store) PrepareRefresh(ctx context.Context, collection string, slug string) (RefreshContext, error) {
	if err := s.requireOperator(); err != nil {
		return RefreshContext{}, err
	}
	if err := s.checkRepo(collection); err != nil {
		return RefreshContext{}, err
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return RefreshContext{}, err
	}
	defer tx.Rollback(ctx)

	if err := s.checkWorkAdmissionTx(ctx, tx); err != nil {
		return RefreshContext{}, err
	}

	var p WorkProject
	var inflightExpires, nextAllowed *time.Time
	var now time.Time
	err = tx.QueryRow(ctx, `SELECT binding_id, local_slug, collection, plane_origin, workspace_uuid, project_uuid, config_revision, plane_instance, status, metadata_exclusion_revision, refresh_sequence, refresh_inflight_token, refresh_inflight_expires_at, next_refresh_allowed_at, clock_timestamp()
		FROM cairn.work_project WHERE collection = $1 AND local_slug = $2 FOR UPDATE`, collection, slug).Scan(
		&p.BindingID, &p.LocalSlug, &p.Collection, &p.PlaneOrigin,
		&p.WorkspaceUUID, &p.ProjectUUID, &p.ConfigRevision, &p.PlaneInstance,
		&p.Status, &p.MetadataExclusionRevision, &p.RefreshSequence,
		&p.RefreshInflightToken, &inflightExpires, &nextAllowed, &now)
	if errors.Is(err, pgx.ErrNoRows) {
		return RefreshContext{}, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", slug, collection))
	}
	if err != nil {
		return RefreshContext{}, err
	}
	if p.Status != "active" {
		return RefreshContext{}, failure("INVALID_REQUEST", fmt.Sprintf("work project %q is %s", slug, p.Status))
	}

	// 1 in-flight lease check (35s expiry) (REFRESH-1)
	if p.RefreshInflightToken != nil && inflightExpires != nil && inflightExpires.After(now) {
		return RefreshContext{}, failure("REFRESH_IN_PROGRESS", "a refresh is already in progress for this project")
	}

	// Per-minute rate bound check (REFRESH-1)
	if nextAllowed != nil && nextAllowed.After(now) {
		return RefreshContext{}, failure("REFRESH_RATE_LIMITED", fmt.Sprintf("refresh rate limited; next refresh allowed at %s", nextAllowed.UTC().Format(time.RFC3339)))
	}

	// Capture database generation under lock (STORE-6)
	gen, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return RefreshContext{}, err
	}

	claimToken := uuid.New()
	nextSeq := p.RefreshSequence + 1
	_, err = tx.Exec(ctx, `UPDATE cairn.work_project
		SET refresh_sequence = $1,
		    refresh_inflight_token = $2,
		    refresh_inflight_expires_at = clock_timestamp() + interval '35 seconds',
		    next_refresh_allowed_at = clock_timestamp() + interval '60 seconds'
		WHERE binding_id = $3`, nextSeq, claimToken, p.BindingID)
	if err != nil {
		return RefreshContext{}, err
	}

	refCtx := RefreshContext{
		BindingID:                 p.BindingID,
		LocalSlug:                 p.LocalSlug,
		Collection:                p.Collection,
		PlaneOrigin:               p.PlaneOrigin,
		WorkspaceUUID:             p.WorkspaceUUID,
		ProjectUUID:               p.ProjectUUID,
		PlaneInstance:             p.PlaneInstance,
		ConfigRevision:            p.ConfigRevision,
		DatabaseGeneration:        gen,
		MetadataExclusionRevision: p.MetadataExclusionRevision,
		RefreshSequence:           nextSeq,
		ClaimToken:                claimToken,
		StagedAt:                  time.Now(),
	}

	return refCtx, tx.Commit(ctx)
}

func (s *Store) CommitRefreshSnapshot(ctx context.Context, refCtx RefreshContext, items []plane.NormalizedItem, authRejected bool, barrierHook func()) (*WorkSnapshot, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}
	if err := s.checkRepo(refCtx.Collection); err != nil {
		return nil, err
	}

	// Deterministic barrier hook for testing concurrency (AC-10): runs BEFORE transaction lock acquisition
	if barrierHook != nil {
		barrierHook()
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	if err := s.checkWorkAdmissionTx(ctx, tx); err != nil {
		return nil, err
	}

	// Validate DB generation has not changed via restore fence (STORE-6)
	currentGen, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return nil, err
	}
	if currentGen != refCtx.DatabaseGeneration {
		return nil, failure("STALE_RESTORE", "database restore fence occurred during refresh; staged data discarded")
	}

	var p WorkProject
	err = tx.QueryRow(ctx, `SELECT binding_id, local_slug, collection, plane_origin, workspace_uuid, project_uuid, config_revision, status, metadata_exclusion_revision, refresh_sequence, refresh_inflight_token
		FROM cairn.work_project WHERE binding_id = $1 FOR UPDATE`, refCtx.BindingID).Scan(
		&p.BindingID, &p.LocalSlug, &p.Collection, &p.PlaneOrigin,
		&p.WorkspaceUUID, &p.ProjectUUID, &p.ConfigRevision, &p.Status, &p.MetadataExclusionRevision, &p.RefreshSequence, &p.RefreshInflightToken)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, failure("NOT_FOUND", fmt.Sprintf("work project binding %s not found", refCtx.BindingID))
	}
	if err != nil {
		return nil, err
	}

	if p.Status != "active" {
		return nil, failure("INVALID_REQUEST", fmt.Sprintf("work project %q is %s", p.LocalSlug, p.Status))
	}

	// Exact live claim ownership: token must match AND sequence must match
	if p.RefreshInflightToken == nil || *p.RefreshInflightToken != refCtx.ClaimToken || refCtx.RefreshSequence != p.RefreshSequence {
		return nil, failure("VERSION_CONFLICT", "stale or unowned refresh claim")
	}

	// Validate refresh sequence ordering (STORE-6)
	if refCtx.RefreshSequence < p.RefreshSequence {
		return nil, failure("VERSION_CONFLICT", "stale refresh sequence; newer refresh already committed")
	}

	// Validate metadata exclusion revision has not changed during staging (STORE-6, AC-10)
	if refCtx.MetadataExclusionRevision != p.MetadataExclusionRevision {
		return nil, failure("VERSION_CONFLICT", fmt.Sprintf("stale metadata exclusion revision: captured %d, current is %d", refCtx.MetadataExclusionRevision, p.MetadataExclusionRevision))
	}

	// Validate config revision has not changed during staging (STORE-6)
	if refCtx.ConfigRevision != "" && refCtx.ConfigRevision != p.ConfigRevision {
		return nil, failure("VERSION_CONFLICT", fmt.Sprintf("stale config revision: captured %s, current is %s", refCtx.ConfigRevision, p.ConfigRevision))
	}

	if authRejected {
		if err := s.recordRefreshFailureLockedTx(ctx, tx, refCtx, &p, true, "Plane auth rejected"); err != nil {
			return nil, err
		}
		return nil, failure("AUTH_REJECTED", "Plane auth rejected")
	}

	if len(items) > plane.MaxItemLimit {
		if err := s.recordRefreshFailureLockedTx(ctx, tx, refCtx, &p, false, "PROJECT_TOO_LARGE: Plane project exceeds 10000 items"); err != nil {
			return nil, err
		}
		return nil, plane.ErrTooLarge
	}

	// Read active exclusions for this binding (purge wins over staged data)
	exclRows, err := tx.Query(ctx, `SELECT field_name, pattern FROM cairn.work_metadata_exclusion WHERE binding_id = $1`, p.BindingID)
	if err != nil {
		return nil, err
	}
	defer exclRows.Close()

	type exclusion struct {
		fieldName string
		pattern   string
	}
	var exclusions []exclusion
	for exclRows.Next() {
		var e exclusion
		if err := exclRows.Scan(&e.fieldName, &e.pattern); err != nil {
			return nil, err
		}
		exclusions = append(exclusions, e)
	}
	if err := exclRows.Err(); err != nil {
		return nil, err
	}
	exclRows.Close()

	// Calculate new cache generation
	var newGen int64
	err = tx.QueryRow(ctx, `SELECT COALESCE(MAX(cache_generation), 0) + 1 FROM cairn.work_snapshot WHERE binding_id = $1`, p.BindingID).Scan(&newGen)
	if err != nil {
		return nil, err
	}

	// Upsert items applying exclusions
	for _, it := range items {
		title := it.Title
		for _, ex := range exclusions {
			if (ex.fieldName == "title" || ex.fieldName == "all") && ex.pattern != "" {
				if strings.Contains(strings.ToLower(title), strings.ToLower(ex.pattern)) {
					title = "[redacted]"
				}
			}
		}

		assigneeIDsJSON, _ := json.Marshal(it.AssigneeIDs)
		assigneeNamesJSON, _ := json.Marshal(it.AssigneeNames)

		_, err = tx.Exec(ctx, `INSERT INTO cairn.work_item (
			binding_id, plane_item_uuid, identifier, title, state_id, state_name, state_group,
			assignee_ids, assignee_names, updated_at_source, source_url, cache_generation, is_absent, last_refreshed_at, refresh_error
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, false, clock_timestamp(), NULL)
		ON CONFLICT (binding_id, plane_item_uuid) DO UPDATE SET
			identifier = EXCLUDED.identifier,
			title = EXCLUDED.title,
			state_id = EXCLUDED.state_id,
			state_name = EXCLUDED.state_name,
			state_group = EXCLUDED.state_group,
			assignee_ids = EXCLUDED.assignee_ids,
			assignee_names = EXCLUDED.assignee_names,
			updated_at_source = EXCLUDED.updated_at_source,
			source_url = EXCLUDED.source_url,
			cache_generation = EXCLUDED.cache_generation,
			is_absent = false,
			last_refreshed_at = clock_timestamp(),
			refresh_error = NULL`,
			p.BindingID, it.PlaneItemUUID, it.Identifier, title, it.StateID, it.StateName, it.StateGroup,
			assigneeIDsJSON, assigneeNamesJSON, it.UpdatedAtSource, it.SourceURL, newGen)
		if err != nil {
			return nil, err
		}
	}

	// Mark items absent if not present in this refresh generation
	_, err = tx.Exec(ctx, `UPDATE cairn.work_item SET is_absent = true WHERE binding_id = $1 AND cache_generation != $2`, p.BindingID, newGen)
	if err != nil {
		return nil, err
	}

	// Mark auth reauthorized, clear errors and inflight token, set last_successful_refresh_at and next_refresh_allowed_at (STORE-5, REFRESH-1)
	_, err = tx.Exec(ctx, `UPDATE cairn.work_project
		SET auth_rejected = false,
		    last_auth_error = NULL,
		    last_refresh_error = NULL,
		    last_refresh_error_at = NULL,
		    refresh_inflight_token = NULL,
		    refresh_inflight_expires_at = NULL,
		    last_successful_refresh_at = clock_timestamp(),
		    next_refresh_allowed_at = clock_timestamp() + interval '60 seconds',
		    updated_at = clock_timestamp()
		WHERE binding_id = $1`, p.BindingID)
	if err != nil {
		return nil, err
	}

	// Compute snapshot digest
	digestInput := fmt.Sprintf("gen:%d:count:%d:items:", newGen, len(items))
	for _, it := range items {
		digestInput += it.PlaneItemUUID.String() + ";"
	}
	digestSum := sha256.Sum256([]byte(digestInput))
	snapshotDigest := hex.EncodeToString(digestSum[:])

	snapshot := &WorkSnapshot{
		SnapshotID:      uuid.New(),
		BindingID:       p.BindingID,
		CacheGeneration: newGen,
		ItemCount:       len(items),
		SnapshotDigest:  snapshotDigest,
		StagedAt:        refCtx.StagedAt,
	}

	err = tx.QueryRow(ctx, `INSERT INTO cairn.work_snapshot (snapshot_id, binding_id, cache_generation, item_count, snapshot_digest, staged_at, committed_at)
		VALUES ($1, $2, $3, $4, $5, $6, clock_timestamp())
		RETURNING committed_at`,
		snapshot.SnapshotID, snapshot.BindingID, snapshot.CacheGeneration, snapshot.ItemCount, snapshot.SnapshotDigest, snapshot.StagedAt).
		Scan(&snapshot.CommittedAt)
	if err != nil {
		return nil, err
	}

	return snapshot, tx.Commit(ctx)
}

func (s *Store) recordRefreshFailureLockedTx(ctx context.Context, tx pgx.Tx, refCtx RefreshContext, p *WorkProject, authRejected bool, summary string) error {
	// Active project check: if not active, clear lease if owned by ClaimToken, commit, return INVALID_REQUEST
	if p.Status != "active" {
		if p.RefreshInflightToken != nil && *p.RefreshInflightToken == refCtx.ClaimToken {
			if _, err := tx.Exec(ctx, `UPDATE cairn.work_project SET refresh_inflight_token = NULL, refresh_inflight_expires_at = NULL WHERE binding_id = $1`, p.BindingID); err != nil {
				return err
			}
		}
		if err := tx.Commit(ctx); err != nil {
			return err
		}
		return failure("INVALID_REQUEST", fmt.Sprintf("work project %q is %s", p.LocalSlug, p.Status))
	}

	// Exact live claim ownership: token must match AND sequence must match
	if p.RefreshInflightToken == nil || *p.RefreshInflightToken != refCtx.ClaimToken || refCtx.RefreshSequence != p.RefreshSequence {
		return failure("VERSION_CONFLICT", "stale or unowned refresh claim; cannot record failure")
	}

	// Privacy and config fences: metadata exclusion revision and config revision must match
	if refCtx.MetadataExclusionRevision != p.MetadataExclusionRevision || (refCtx.ConfigRevision != "" && refCtx.ConfigRevision != p.ConfigRevision) {
		if _, err := tx.Exec(ctx, `UPDATE cairn.work_project SET refresh_inflight_token = NULL, refresh_inflight_expires_at = NULL WHERE binding_id = $1`, p.BindingID); err != nil {
			return err
		}
		if err := tx.Commit(ctx); err != nil {
			return err
		}
		return failure("VERSION_CONFLICT", "metadata exclusion or config revision changed during refresh; lease released without writing failure")
	}

	if len(summary) > 512 {
		summary = summary[:512]
	}

	if authRejected {
		_, err := tx.Exec(ctx, `UPDATE cairn.work_project
			SET auth_rejected = true,
			    last_auth_error = $1,
			    last_refresh_error = $1,
			    last_refresh_error_at = clock_timestamp(),
			    refresh_inflight_token = NULL,
			    refresh_inflight_expires_at = NULL,
			    updated_at = clock_timestamp()
			WHERE binding_id = $2`, summary, p.BindingID)
		if err != nil {
			return err
		}
	} else {
		_, err := tx.Exec(ctx, `UPDATE cairn.work_project
			SET last_refresh_error = $1,
			    last_refresh_error_at = clock_timestamp(),
			    refresh_inflight_token = NULL,
			    refresh_inflight_expires_at = NULL,
			    updated_at = clock_timestamp()
			WHERE binding_id = $2`, summary, p.BindingID)
		if err != nil {
			return err
		}
	}

	// Retain nonsecret error on existing items (if any)
	if _, err := tx.Exec(ctx, `UPDATE cairn.work_item SET refresh_error = $1 WHERE binding_id = $2`, summary, p.BindingID); err != nil {
		return err
	}

	return tx.Commit(ctx)
}

func (s *Store) RecordRefreshFailure(ctx context.Context, refCtx RefreshContext, authRejected bool, summary string) error {
	if err := s.requireOperator(); err != nil {
		return err
	}
	if err := s.checkRepo(refCtx.Collection); err != nil {
		return err
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	// Validate DB generation has not changed via restore fence (STORE-6)
	currentGen, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return err
	}
	if currentGen != refCtx.DatabaseGeneration {
		return failure("STALE_RESTORE", "database restore fence occurred during refresh; staged data discarded")
	}

	var p WorkProject
	err = tx.QueryRow(ctx, `SELECT binding_id, local_slug, collection, status, refresh_sequence, config_revision, metadata_exclusion_revision, refresh_inflight_token
		FROM cairn.work_project WHERE binding_id = $1 FOR UPDATE`, refCtx.BindingID).Scan(
		&p.BindingID, &p.LocalSlug, &p.Collection, &p.Status, &p.RefreshSequence, &p.ConfigRevision, &p.MetadataExclusionRevision, &p.RefreshInflightToken)
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("NOT_FOUND", fmt.Sprintf("work project binding %s not found", refCtx.BindingID))
	}
	if err != nil {
		return err
	}

	return s.recordRefreshFailureLockedTx(ctx, tx, refCtx, &p, authRejected, summary)
}

func (s *Store) ListWorkItems(ctx context.Context, collection string, slug string, stateGroup string, limit int, offset int) ([]WorkItem, int, error) {
	return s.ListWorkItemsExtended(ctx, collection, slug, stateGroup, false, limit, offset)
}

func (s *Store) ListWorkItemsExtended(ctx context.Context, collection string, slug string, stateGroup string, blockerOnly bool, limit int, offset int) ([]WorkItem, int, error) {
	if err := s.requireOperator(); err != nil {
		return nil, 0, err
	}
	if err := s.checkRepo(collection); err != nil {
		return nil, 0, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return nil, 0, err
	}
	defer tx.Rollback(ctx)

	if err := s.checkWorkReadableTx(ctx, tx); err != nil {
		return nil, 0, err
	}

	var bindingID uuid.UUID
	var authRejected bool
	err = tx.QueryRow(ctx, `SELECT binding_id, auth_rejected FROM cairn.work_project WHERE collection = $1 AND local_slug = $2`, collection, slug).Scan(&bindingID, &authRejected)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, 0, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", slug, collection))
	}
	if err != nil {
		return nil, 0, err
	}

	if authRejected && stateGroup != "" {
		return []WorkItem{}, 0, tx.Commit(ctx)
	}

	whereClause := "WHERE binding_id = $1"
	args := []any{bindingID}

	if stateGroup != "" {
		args = append(args, stateGroup)
		whereClause += fmt.Sprintf(" AND state_group = $%d", len(args))
	}
	if blockerOnly {
		whereClause += " AND EXISTS (SELECT 1 FROM cairn.work_annotation a WHERE a.binding_id = cairn.work_item.binding_id AND a.plane_item_uuid = cairn.work_item.plane_item_uuid AND a.kind = 'blocker' AND a.state = 'open')"
	}

	countQuery := "SELECT count(*) FROM cairn.work_item " + whereClause
	var total int
	if err := tx.QueryRow(ctx, countQuery, args...).Scan(&total); err != nil {
		return nil, 0, err
	}

	query := `SELECT item_id, binding_id, plane_item_uuid, identifier, title, state_id, state_name, state_group,
		assignee_ids, assignee_names, updated_at_source, COALESCE(source_url, ''), cache_generation, is_absent, last_refreshed_at, refresh_error
		FROM cairn.work_item ` + whereClause + " ORDER BY identifier"

	if limit > 0 {
		args = append(args, limit)
		query += fmt.Sprintf(" LIMIT $%d", len(args))
	}
	if offset > 0 {
		args = append(args, offset)
		query += fmt.Sprintf(" OFFSET $%d", len(args))
	}

	rows, err := tx.Query(ctx, query, args...)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()

	now := time.Now()
	items := make([]WorkItem, 0)
	for rows.Next() {
		var it WorkItem
		var assigneeIDsRaw, assigneeNamesRaw []byte
		var stateID *uuid.UUID
		var refreshErr *string

		if err := rows.Scan(&it.ItemID, &it.BindingID, &it.PlaneItemUUID, &it.Identifier, &it.Title,
			&stateID, &it.StateName, &it.StateGroup, &assigneeIDsRaw, &assigneeNamesRaw,
			&it.UpdatedAtSource, &it.SourceURL, &it.CacheGeneration, &it.IsAbsent,
			&it.LastRefreshedAt, &refreshErr); err != nil {
			return nil, 0, err
		}
		it.StateID = stateID
		if refreshErr != nil {
			it.RefreshError = *refreshErr
		}
		_ = json.Unmarshal(assigneeIDsRaw, &it.AssigneeIDs)
		_ = json.Unmarshal(assigneeNamesRaw, &it.AssigneeNames)
		if it.AssigneeIDs == nil {
			it.AssigneeIDs = make([]string, 0)
		}
		if it.AssigneeNames == nil {
			it.AssigneeNames = make([]string, 0)
		}

		if it.LastRefreshedAt == nil || now.Sub(*it.LastRefreshedAt) > 2*time.Minute || it.CacheGeneration == 0 {
			it.IsStale = true
		}

		// STORE-5: Mask external fields if auth rejected
		if authRejected {
			it.Title = "[hidden: auth rejected]"
			it.Identifier = "[hidden]"
			it.StateID = nil
			it.StateName = ""
			it.StateGroup = ""
			it.AssigneeIDs = []string{}
			it.AssigneeNames = []string{}
			it.UpdatedAtSource = nil
			it.SourceURL = ""
			it.RefreshError = "Plane auth rejected"
		}

		items = append(items, it)
	}
	if err := rows.Err(); err != nil {
		return nil, 0, err
	}

	return items, total, tx.Commit(ctx)
}

func isTerminalDeliveryState(st string) bool {
	switch st {
	case "completed", "acknowledged", "expired", "failed", "cancelled", "handled":
		return true
	default:
		return false
	}
}

func (s *Store) resolveWorkItemAndAssociationsTx(ctx context.Context, tx pgx.Tx, collection, slug, itemIDOrIdentifier string, now time.Time) (uuid.UUID, WorkItem, bool, []WorkAssociation, error) {
	var bindingID uuid.UUID
	var authRejected bool
	err := tx.QueryRow(ctx, `SELECT binding_id, auth_rejected FROM cairn.work_project WHERE collection = $1 AND local_slug = $2`, collection, slug).Scan(&bindingID, &authRejected)
	if errors.Is(err, pgx.ErrNoRows) {
		return uuid.Nil, WorkItem{}, false, nil, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", slug, collection))
	}
	if err != nil {
		return uuid.Nil, WorkItem{}, false, nil, err
	}

	var it WorkItem
	var assigneeIDsRaw, assigneeNamesRaw []byte
	var stateID *uuid.UUID
	var refreshErr *string

	itemUUID, parseErr := uuid.Parse(itemIDOrIdentifier)
	var row pgx.Row
	if parseErr == nil {
		row = tx.QueryRow(ctx, `SELECT item_id, binding_id, plane_item_uuid, identifier, title, state_id, state_name, state_group,
			assignee_ids, assignee_names, updated_at_source, COALESCE(source_url, ''), cache_generation, is_absent, last_refreshed_at, refresh_error
			FROM cairn.work_item WHERE binding_id = $1 AND (item_id = $2 OR plane_item_uuid = $2)`, bindingID, itemUUID)
	} else {
		row = tx.QueryRow(ctx, `SELECT item_id, binding_id, plane_item_uuid, identifier, title, state_id, state_name, state_group,
			assignee_ids, assignee_names, updated_at_source, COALESCE(source_url, ''), cache_generation, is_absent, last_refreshed_at, refresh_error
			FROM cairn.work_item WHERE binding_id = $1 AND identifier = $2`, bindingID, itemIDOrIdentifier)
	}

	err = row.Scan(&it.ItemID, &it.BindingID, &it.PlaneItemUUID, &it.Identifier, &it.Title,
		&stateID, &it.StateName, &it.StateGroup, &assigneeIDsRaw, &assigneeNamesRaw,
		&it.UpdatedAtSource, &it.SourceURL, &it.CacheGeneration, &it.IsAbsent,
		&it.LastRefreshedAt, &refreshErr)
	if errors.Is(err, pgx.ErrNoRows) {
		return uuid.Nil, WorkItem{}, false, nil, failure("NOT_FOUND", fmt.Sprintf("work item %q not found in project %q", itemIDOrIdentifier, slug))
	}
	if err != nil {
		return uuid.Nil, WorkItem{}, false, nil, err
	}
	it.StateID = stateID
	if refreshErr != nil {
		it.RefreshError = *refreshErr
	}
	_ = json.Unmarshal(assigneeIDsRaw, &it.AssigneeIDs)
	_ = json.Unmarshal(assigneeNamesRaw, &it.AssigneeNames)
	if it.AssigneeIDs == nil {
		it.AssigneeIDs = make([]string, 0)
	}
	if it.AssigneeNames == nil {
		it.AssigneeNames = make([]string, 0)
	}
	if it.LastRefreshedAt == nil || now.Sub(*it.LastRefreshedAt) > 2*time.Minute || it.CacheGeneration == 0 {
		it.IsStale = true
	}

	// STORE-5: Mask external fields if auth rejected
	if authRejected {
		it.Title = "[hidden: auth rejected]"
		it.Identifier = "[hidden]"
		it.StateID = nil
		it.StateName = ""
		it.StateGroup = ""
		it.AssigneeIDs = []string{}
		it.AssigneeNames = []string{}
		it.UpdatedAtSource = nil
		it.SourceURL = ""
		it.RefreshError = "Plane auth rejected"
	}

	associations := make([]WorkAssociation, 0)
	assocRows, err := tx.Query(ctx, `SELECT association_id, revision, binding_id, plane_item_uuid, target_type, target_id,
		target_agent_id, target_execution_id, target_event_id, target_record_id, target_version, target_group_id, target_run_id,
		rationale, task_summary, author, state, created_at, removed_at, removed_by
		FROM cairn.work_association WHERE binding_id = $1 AND plane_item_uuid = $2 AND state = 'active' ORDER BY created_at, association_id`, bindingID, it.PlaneItemUUID)
	if err != nil {
		return uuid.Nil, WorkItem{}, false, nil, err
	}
	for assocRows.Next() {
		var a WorkAssociation
		var taskSummary, rationale, removedBy *string
		if err := assocRows.Scan(&a.AssociationID, &a.Revision, &a.BindingID, &a.PlaneItemUUID,
			&a.TargetType, &a.TargetID,
			&a.TargetAgentID, &a.TargetExecutionID, &a.TargetEventID, &a.TargetRecordID, &a.TargetVersion, &a.TargetGroupID, &a.TargetRunID,
			&rationale, &taskSummary, &a.Author, &a.State, &a.CreatedAt, &a.RemovedAt, &removedBy); err != nil {
			assocRows.Close()
			return uuid.Nil, WorkItem{}, false, nil, err
		}
		if rationale != nil {
			a.Rationale = *rationale
		}
		if taskSummary != nil {
			a.TaskSummary = *taskSummary
		}
		if removedBy != nil {
			a.RemovedBy = *removedBy
		}
		associations = append(associations, a)
	}
	if err = assocRows.Err(); err != nil {
		assocRows.Close()
		return uuid.Nil, WorkItem{}, false, nil, err
	}
	assocRows.Close()

	return bindingID, it, authRejected, associations, nil
}

func (s *Store) GetWorkItemDetail(ctx context.Context, collection string, slug string, itemIDOrIdentifier string) (WorkItemDetail, error) {
	if err := s.requireOperator(); err != nil {
		return WorkItemDetail{}, err
	}
	if err := s.checkRepo(collection); err != nil {
		return WorkItemDetail{}, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return WorkItemDetail{}, err
	}
	defer tx.Rollback(ctx)

	if err := s.checkWorkReadableTx(ctx, tx); err != nil {
		return WorkItemDetail{}, err
	}

	bindingID, it, authRejected, associations, err := s.resolveWorkItemAndAssociationsTx(ctx, tx, collection, slug, itemIDOrIdentifier, time.Now())
	if err != nil {
		return WorkItemDetail{}, err
	}

	detail := WorkItemDetail{
		Item:           it,
		ProjectSlug:    slug,
		Collection:     collection,
		AuthRejected:   authRejected,
		Associations:   associations,
		Sessions:       make([]AssociatedSession, 0),
		Requests:       make([]AssociatedRequest, 0),
		ResponseGroups: make([]AssociatedResponseGroup, 0),
		ResultRecords:  make([]AssociatedResultRecord, 0),
		RunReceipts:    make([]AssociatedRunReceipt, 0),
		Annotations:    make([]WorkAnnotation, 0),
	}

	for _, a := range detail.Associations {
		// Process session associations (AC-03, STORE-3)
		if a.TargetType == "session" && a.TargetAgentID != nil && a.TargetExecutionID != nil {
			agentUUID := *a.TargetAgentID
			execUUID := *a.TargetExecutionID

			var currentExecUUID uuid.UUID
			var metaRaw []byte
			var expiresAt time.Time
			var stopped bool

			sErr := tx.QueryRow(ctx, `SELECT execution_id, metadata, expires_at, stopped FROM cairn.agent_session WHERE agent_id = $1`, agentUUID).
				Scan(&currentExecUUID, &metaRaw, &expiresAt, &stopped)

			sess := AssociatedSession{
				AssociationID: a.AssociationID,
				Revision:      a.Revision,
				AgentID:       agentUUID,
				ExecutionID:   execUUID,
				Rationale:     a.Rationale,
			}

			if sErr == nil {
				var meta AgentMetadata
				_ = json.Unmarshal(metaRaw, &meta)

				if currentExecUUID == execUUID {
					// Current execution: active context
					sess.IsHistorical = false
					sess.TaskSummary = meta.TaskSummary
					sess.Online = !stopped && expiresAt.After(time.Now())
					sess.ExpiresAt = &expiresAt
				} else {
					// Old execution: historical context, NEVER borrow new task summary (AC-03)!
					sess.IsHistorical = true
					sess.TaskSummary = a.TaskSummary
					sess.Online = false
				}
			} else if errors.Is(sErr, pgx.ErrNoRows) {
				// Session row missing or purged: historical
				sess.IsHistorical = true
				sess.TaskSummary = a.TaskSummary
			} else {
				return WorkItemDetail{}, sErr
			}
			detail.Sessions = append(detail.Sessions, sess)
		}

		// Process request/event associations (AC-05)
		if a.TargetType == "event" && a.TargetEventID != nil {
			eventUUID := *a.TargetEventID
			req := AssociatedRequest{
				AssociationID: a.AssociationID,
				Revision:      a.Revision,
				EventID:       eventUUID,
				Rationale:     a.Rationale,
				Deliveries:    make([]EventDeliveryView, 0),
			}

			var evKind string
			var recordID *uuid.UUID
			var version *int
			eErr := tx.QueryRow(ctx, `SELECT kind, record_id, version FROM cairn.agent_event WHERE event_id = $1`, eventUUID).Scan(&evKind, &recordID, &version)
			if errors.Is(eErr, pgx.ErrNoRows) {
				req.EventKind = "unknown"
				req.DeliveryState = "unknown"
				req.PayloadStatus = "unavailable"
				req.PayloadAvailable = false
			} else if eErr != nil {
				return WorkItemDetail{}, eErr
			} else {
				req.EventKind = evKind
				req.SourceRecordID = recordID
				req.SourceVersion = version

				if recordID != nil && version != nil {
					var lifecycle string
					var payloadDeletedBy *string
					var body *string
					pErr := tx.QueryRow(ctx, `SELECT m.lifecycle, v.payload_deleted_by, v.body
						FROM cairn.memory_record m
						JOIN cairn.record_version v ON m.record_id = v.record_id AND v.version = $2
						WHERE m.record_id = $1 AND v.repo = $3`, *recordID, *version, collection).Scan(&lifecycle, &payloadDeletedBy, &body)
					if errors.Is(pErr, pgx.ErrNoRows) {
						req.PayloadStatus = "unavailable"
						req.PayloadAvailable = false
					} else if pErr != nil {
						return WorkItemDetail{}, pErr
					} else {
						if lifecycle == "tombstoned" {
							req.PayloadStatus = "forgotten"
							req.PayloadAvailable = false
						} else if payloadDeletedBy != nil || body == nil || *body == "" {
							req.PayloadStatus = "deleted"
							req.PayloadAvailable = false
						} else if lifecycle == "active" {
							req.PayloadStatus = "available"
							req.PayloadAvailable = true
						} else {
							req.PayloadStatus = "unavailable"
							req.PayloadAvailable = false
						}
					}
				} else {
					req.PayloadStatus = "unavailable"
					req.PayloadAvailable = false
				}

				delRows, dErr := tx.Query(ctx, `SELECT d.delivery_id, d.consumer, d.state, d.code, d.completed_at
					FROM cairn.agent_delivery d
					WHERE d.event_id = $1
					ORDER BY d.consumer, d.delivery_id`, eventUUID)
				if dErr != nil {
					return WorkItemDetail{}, dErr
				}
				for delRows.Next() {
					var dv EventDeliveryView
					if err := delRows.Scan(&dv.DeliveryID, &dv.Consumer, &dv.State, &dv.Code, &dv.CompletedAt); err != nil {
						delRows.Close()
						return WorkItemDetail{}, err
					}
					req.Deliveries = append(req.Deliveries, dv)
				}
				if err := delRows.Err(); err != nil {
					delRows.Close()
					return WorkItemDetail{}, err
				}
				delRows.Close()

				if len(req.Deliveries) == 0 {
					req.DeliveryState = "no_deliveries"
				} else if len(req.Deliveries) == 1 {
					req.DeliveryState = req.Deliveries[0].State
				} else {
					allSame := true
					firstState := req.Deliveries[0].State
					hasTerminal := false
					hasActive := false
					diffTerminal := false
					firstTermState := ""

					for _, d := range req.Deliveries {
						if d.State != firstState {
							allSame = false
						}
						isTerm := isTerminalDeliveryState(d.State)
						if isTerm {
							hasTerminal = true
							if firstTermState == "" {
								firstTermState = d.State
							} else if firstTermState != d.State {
								diffTerminal = true
							}
						} else {
							hasActive = true
						}
					}

					if allSame {
						req.DeliveryState = firstState
					} else if hasActive && hasTerminal {
						req.DeliveryState = "partial"
					} else if diffTerminal {
						req.DeliveryState = "mixed"
					} else {
						req.DeliveryState = "partial"
					}
				}
			}
			detail.Requests = append(detail.Requests, req)
		}

		// 3. Process response_group associations (VIEW-1)
		if a.TargetType == "response_group" {
			var grpUUID uuid.UUID
			if a.TargetGroupID != nil {
				grpUUID = *a.TargetGroupID
			} else if u, err := uuid.Parse(a.TargetID); err == nil {
				grpUUID = u
			}
			rg := AssociatedResponseGroup{
				AssociationID: a.AssociationID,
				Revision:      a.Revision,
				GroupEventID:  grpUUID,
				Rationale:     a.Rationale,
			}

			if grpUUID != uuid.Nil {
				var deadline time.Time
				var partialPolicy string
				var state string
				var closedAt *time.Time
				var code string
				var recID *uuid.UUID
				var ver *int

				gErr := tx.QueryRow(ctx, `SELECT g.deadline, g.partial_policy, g.state, g.closed_at, g.code, e.record_id, e.version
					FROM cairn.agent_response_group g
					JOIN cairn.agent_event e USING(event_id)
					WHERE g.event_id = $1 AND e.repo = $2`, grpUUID, collection).
					Scan(&deadline, &partialPolicy, &state, &closedAt, &code, &recID, &ver)

				if errors.Is(gErr, pgx.ErrNoRows) {
					rg.State = "unknown"
					rg.PayloadStatus = "unavailable"
					rg.PayloadAvailable = false
				} else if gErr != nil {
					return WorkItemDetail{}, gErr
				} else {
					rg.Deadline = &deadline
					rg.PartialPolicy = partialPolicy
					rg.State = state
					rg.ClosedAt = closedAt
					rg.Code = code

					if err := tx.QueryRow(ctx, `SELECT count(*), count(response_event_id)
						FROM cairn.agent_response_group_member
						WHERE event_id = $1`, grpUUID).Scan(&rg.MemberCount, &rg.RespondedCount); err != nil && !errors.Is(err, pgx.ErrNoRows) {
						return WorkItemDetail{}, err
					}

					if recID != nil && ver != nil {
						var lifecycle string
						var payloadDeletedBy *string
						var body *string
						pErr := tx.QueryRow(ctx, `SELECT m.lifecycle, v.payload_deleted_by, v.body
							FROM cairn.memory_record m
							JOIN cairn.record_version v ON m.record_id = v.record_id AND v.version = $2
							WHERE m.record_id = $1 AND v.repo = $3`, *recID, *ver, collection).Scan(&lifecycle, &payloadDeletedBy, &body)
						if errors.Is(pErr, pgx.ErrNoRows) {
							rg.PayloadStatus = "unavailable"
							rg.PayloadAvailable = false
						} else if pErr != nil {
							return WorkItemDetail{}, pErr
						} else {
							if lifecycle == "tombstoned" {
								rg.PayloadStatus = "forgotten"
								rg.PayloadAvailable = false
							} else if payloadDeletedBy != nil || body == nil || *body == "" {
								rg.PayloadStatus = "deleted"
								rg.PayloadAvailable = false
							} else if lifecycle == "active" {
								rg.PayloadStatus = "available"
								rg.PayloadAvailable = true
							} else {
								rg.PayloadStatus = "unavailable"
								rg.PayloadAvailable = false
							}
						}
					} else {
						rg.PayloadStatus = "unavailable"
						rg.PayloadAvailable = false
					}
				}
			} else {
				rg.State = "unknown"
				rg.PayloadStatus = "unavailable"
			}
			detail.ResponseGroups = append(detail.ResponseGroups, rg)
		}

		// 4. Process result_record associations (VIEW-1)
		if a.TargetType == "result_record" {
			var recID uuid.UUID
			var ver int
			if a.TargetRecordID != nil && a.TargetVersion != nil {
				recID = *a.TargetRecordID
				ver = *a.TargetVersion
			} else {
				delim := ":"
				if strings.Contains(a.TargetID, "/") {
					delim = "/"
				}
				parts := strings.Split(a.TargetID, delim)
				if len(parts) == 2 {
					recID, _ = uuid.Parse(parts[0])
					ver, _ = strconv.Atoi(parts[1])
				}
			}

			rr := AssociatedResultRecord{
				AssociationID: a.AssociationID,
				Revision:      a.Revision,
				RecordID:      recID,
				Version:       ver,
				Rationale:     a.Rationale,
			}

			if recID != uuid.Nil && ver > 0 {
				var lifecycle, sensitivity, kind string
				var payloadDeletedBy *string
				var body *string

				rErr := tx.QueryRow(ctx, `SELECT m.lifecycle, m.sensitivity, v.kind, v.payload_deleted_by, v.body
					FROM cairn.memory_record m
					JOIN cairn.record_version v ON m.record_id = v.record_id AND v.version = $2
					WHERE m.record_id = $1 AND v.repo = $3`, recID, ver, collection).
					Scan(&lifecycle, &sensitivity, &kind, &payloadDeletedBy, &body)

				if errors.Is(rErr, pgx.ErrNoRows) {
					rr.Lifecycle = "unknown"
					rr.PayloadStatus = "unavailable"
					rr.PayloadAvailable = false
				} else if rErr != nil {
					return WorkItemDetail{}, rErr
				} else {
					rr.Lifecycle = lifecycle
					rr.Sensitivity = sensitivity
					rr.Kind = kind

					if lifecycle == "tombstoned" {
						rr.PayloadStatus = "forgotten"
						rr.PayloadAvailable = false
					} else if payloadDeletedBy != nil || body == nil || *body == "" {
						rr.PayloadStatus = "deleted"
						rr.PayloadAvailable = false
					} else if lifecycle == "active" {
						rr.PayloadStatus = "available"
						rr.PayloadAvailable = true
					} else {
						rr.PayloadStatus = "unavailable"
						rr.PayloadAvailable = false
					}
				}
			} else {
				rr.Lifecycle = "unknown"
				rr.PayloadStatus = "unavailable"
			}
			detail.ResultRecords = append(detail.ResultRecords, rr)
		}

		// 5. Process run_receipt associations (VIEW-1)
		if a.TargetType == "run_receipt" {
			targetID := a.TargetID
			if a.TargetRunID != "" {
				targetID = a.TargetRunID
			}
			receiptUUID, parseErr := uuid.Parse(targetID)

			runR := AssociatedRunReceipt{
				AssociationID:     a.AssociationID,
				Revision:          a.Revision,
				TargetID:          targetID,
				Rationale:         a.Rationale,
				Status:            "unknown",
				WakeState:         "",
				AssessmentOutcome: "",
				PayloadStatus:     "unavailable",
				PayloadAvailable:  false,
			}

			if parseErr == nil {
				runR.ReceiptID = &receiptUUID

				var status string
				rErr := tx.QueryRow(ctx, `SELECT status FROM cairn.retrieval_receipt
					WHERE receipt_id = $1 AND scope->>'repo' = $2`, receiptUUID, collection).Scan(&status)
				if rErr == nil {
					runR.Status = status
					runR.PayloadStatus = "available"
					runR.PayloadAvailable = true

					if err := tx.QueryRow(ctx, `SELECT state FROM cairn.agent_wake_attempt
						WHERE receipt_id = $1 ORDER BY created_at DESC LIMIT 1`, receiptUUID).Scan(&runR.WakeState); err != nil && !errors.Is(err, pgx.ErrNoRows) {
						return WorkItemDetail{}, err
					}

					if err := tx.QueryRow(ctx, `SELECT task_outcome FROM cairn.run_assessment
						WHERE receipt_id = $1 ORDER BY version DESC LIMIT 1`, receiptUUID).Scan(&runR.AssessmentOutcome); err != nil && !errors.Is(err, pgx.ErrNoRows) {
						return WorkItemDetail{}, err
					}
				} else if !errors.Is(rErr, pgx.ErrNoRows) {
					return WorkItemDetail{}, rErr
				}
			}
			detail.RunReceipts = append(detail.RunReceipts, runR)
		}
	}

	// Read annotations
	annotRows, err := tx.Query(ctx, `SELECT annotation_id, revision, binding_id, plane_item_uuid, kind, body, state, author, source_references, created_at, updated_at
		FROM cairn.work_annotation WHERE binding_id = $1 AND plane_item_uuid = $2 ORDER BY created_at`, bindingID, it.PlaneItemUUID)
	if err != nil {
		return WorkItemDetail{}, err
	}
	for annotRows.Next() {
		var an WorkAnnotation
		var srcRefsRaw []byte
		if err := annotRows.Scan(&an.AnnotationID, &an.Revision, &an.BindingID, &an.PlaneItemUUID,
			&an.Kind, &an.Body, &an.State, &an.Author, &srcRefsRaw, &an.CreatedAt, &an.UpdatedAt); err != nil {
			annotRows.Close()
			return WorkItemDetail{}, err
		}
		_ = json.Unmarshal(srcRefsRaw, &an.SourceReferences)
		if an.SourceReferences == nil {
			an.SourceReferences = make([]string, 0)
		}
		detail.Annotations = append(detail.Annotations, an)
	}
	if err = annotRows.Err(); err != nil {
		annotRows.Close()
		return WorkItemDetail{}, err
	}
	annotRows.Close()

	links, lErr := s.listWorkArtifactLinksTx(ctx, tx, collection, bindingID, it.PlaneItemUUID, "", false)
	if lErr != nil {
		return WorkItemDetail{}, lErr
	}
	detail.Artifacts = links

	return detail, tx.Commit(ctx)
}

func (s *Store) ResolveWorkItemUUID(ctx context.Context, collection, slug, itemIDOrIdent string) (uuid.UUID, error) {
	if err := s.requireOperator(); err != nil {
		return uuid.Nil, err
	}
	if err := s.checkRepo(collection); err != nil {
		return uuid.Nil, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return uuid.Nil, err
	}
	defer tx.Rollback(ctx)

	if err := s.checkWorkReadableTx(ctx, tx); err != nil {
		return uuid.Nil, err
	}

	var bindingID uuid.UUID
	err = tx.QueryRow(ctx, `SELECT binding_id FROM cairn.work_project WHERE collection = $1 AND local_slug = $2`, collection, slug).Scan(&bindingID)
	if errors.Is(err, pgx.ErrNoRows) {
		return uuid.Nil, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", slug, collection))
	}
	if err != nil {
		return uuid.Nil, err
	}

	var itemUUID uuid.UUID
	if u, parseErr := uuid.Parse(itemIDOrIdent); parseErr == nil {
		err = tx.QueryRow(ctx, `SELECT plane_item_uuid FROM cairn.work_item WHERE binding_id = $1 AND (item_id = $2 OR plane_item_uuid = $2)`, bindingID, u).Scan(&itemUUID)
	} else {
		err = tx.QueryRow(ctx, `SELECT plane_item_uuid FROM cairn.work_item WHERE binding_id = $1 AND identifier = $2`, bindingID, itemIDOrIdent).Scan(&itemUUID)
	}
	if errors.Is(err, pgx.ErrNoRows) {
		return uuid.Nil, failure("NOT_FOUND", fmt.Sprintf("work item %q not found in project %q", itemIDOrIdent, slug))
	}
	if err != nil {
		return uuid.Nil, err
	}
	return itemUUID, tx.Commit(ctx)
}
