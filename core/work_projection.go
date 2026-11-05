package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
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
	CreatedAt                 time.Time  `json:"created_at"`
	UpdatedAt                 time.Time  `json:"updated_at"`
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
	AgentID       uuid.UUID  `json:"agent_id"`
	ExecutionID   uuid.UUID  `json:"execution_id"`
	TaskSummary   string     `json:"task_summary"`
	IsHistorical  bool       `json:"is_historical"`
	Online        bool       `json:"online"`
	ExpiresAt     *time.Time `json:"expires_at,omitempty"`
	Rationale     string     `json:"rationale,omitempty"`
}

type AssociatedRequest struct {
	AssociationID    uuid.UUID `json:"association_id"`
	EventID          uuid.UUID `json:"event_id"`
	DeliveryState    string    `json:"delivery_state"`
	PayloadAvailable bool      `json:"payload_available"`
	Rationale        string    `json:"rationale,omitempty"`
}

type WorkItemDetail struct {
	Item         WorkItem            `json:"item"`
	ProjectSlug  string              `json:"project_slug"`
	Collection   string              `json:"collection"`
	AuthRejected bool                `json:"auth_rejected"`
	Sessions     []AssociatedSession `json:"sessions"`
	Requests     []AssociatedRequest `json:"requests"`
	Associations []WorkAssociation   `json:"associations"`
	Annotations  []WorkAnnotation    `json:"annotations"`
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

	tx, err := s.begin(ctx)
	if err != nil {
		return WorkProject{}, err
	}
	defer tx.Rollback(ctx)

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
			return existing, tx.Commit(ctx)
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

	return p, tx.Commit(ctx)
}

func (s *Store) GetWorkProject(ctx context.Context, collection string, slug string) (WorkProject, error) {
	if err := s.requireOperator(); err != nil {
		return WorkProject{}, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return WorkProject{}, err
	}
	defer tx.Rollback(ctx)

	var p WorkProject
	var lastRefresh *time.Time
	var lastAuthErr *string
	err = tx.QueryRow(ctx, `SELECT binding_id, local_slug, collection, plane_origin, workspace_uuid, project_uuid, config_revision, plane_instance, status, metadata_exclusion_revision, refresh_sequence, auth_rejected, last_auth_error, last_successful_refresh_at, created_at, updated_at
		FROM cairn.work_project WHERE collection = $1 AND local_slug = $2`, collection, slug).Scan(
		&p.BindingID, &p.LocalSlug, &p.Collection, &p.PlaneOrigin,
		&p.WorkspaceUUID, &p.ProjectUUID, &p.ConfigRevision, &p.PlaneInstance,
		&p.Status, &p.MetadataExclusionRevision, &p.RefreshSequence, &p.AuthRejected,
		&lastAuthErr, &lastRefresh, &p.CreatedAt, &p.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return WorkProject{}, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", slug, collection))
	}
	if err != nil {
		return WorkProject{}, err
	}
	if lastAuthErr != nil {
		p.LastAuthError = *lastAuthErr
	}
	p.LastSuccessfulRefreshAt = lastRefresh
	return p, tx.Commit(ctx)
}

func (s *Store) DisableWorkProject(ctx context.Context, collection string, slug string) (WorkProject, error) {
	if err := s.requireOperator(); err != nil {
		return WorkProject{}, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return WorkProject{}, err
	}
	defer tx.Rollback(ctx)

	var p WorkProject
	var lastRefresh *time.Time
	var lastAuthErr *string
	err = tx.QueryRow(ctx, `UPDATE cairn.work_project SET status='disabled', updated_at=clock_timestamp()
		WHERE collection = $1 AND local_slug = $2
		RETURNING binding_id, local_slug, collection, plane_origin, workspace_uuid, project_uuid, config_revision, plane_instance, status, metadata_exclusion_revision, refresh_sequence, auth_rejected, last_auth_error, last_successful_refresh_at, created_at, updated_at`, collection, slug).Scan(
		&p.BindingID, &p.LocalSlug, &p.Collection, &p.PlaneOrigin,
		&p.WorkspaceUUID, &p.ProjectUUID, &p.ConfigRevision, &p.PlaneInstance,
		&p.Status, &p.MetadataExclusionRevision, &p.RefreshSequence, &p.AuthRejected,
		&lastAuthErr, &lastRefresh, &p.CreatedAt, &p.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return WorkProject{}, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", slug, collection))
	}
	if err != nil {
		return WorkProject{}, err
	}
	if lastAuthErr != nil {
		p.LastAuthError = *lastAuthErr
	}
	p.LastSuccessfulRefreshAt = lastRefresh
	return p, tx.Commit(ctx)
}

func (s *Store) ListWorkProjects(ctx context.Context, collection string) ([]WorkProject, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	rows, err := tx.Query(ctx, `SELECT binding_id, local_slug, collection, plane_origin, workspace_uuid, project_uuid, config_revision, plane_instance, status, metadata_exclusion_revision, refresh_sequence, auth_rejected, last_auth_error, last_successful_refresh_at, created_at, updated_at
		FROM cairn.work_project WHERE collection = $1 ORDER BY local_slug`, collection)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var projects []WorkProject
	for rows.Next() {
		var p WorkProject
		var lastRefresh *time.Time
		var lastAuthErr *string
		if err := rows.Scan(&p.BindingID, &p.LocalSlug, &p.Collection, &p.PlaneOrigin,
			&p.WorkspaceUUID, &p.ProjectUUID, &p.ConfigRevision, &p.PlaneInstance,
			&p.Status, &p.MetadataExclusionRevision, &p.RefreshSequence, &p.AuthRejected,
			&lastAuthErr, &lastRefresh, &p.CreatedAt, &p.UpdatedAt); err != nil {
			return nil, err
		}
		if lastAuthErr != nil {
			p.LastAuthError = *lastAuthErr
		}
		p.LastSuccessfulRefreshAt = lastRefresh
		projects = append(projects, p)
	}
	return projects, tx.Commit(ctx)
}

func (s *Store) PrepareRefresh(ctx context.Context, collection string, slug string) (RefreshContext, error) {
	if err := s.requireOperator(); err != nil {
		return RefreshContext{}, err
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return RefreshContext{}, err
	}
	defer tx.Rollback(ctx)

	var p WorkProject
	err = tx.QueryRow(ctx, `SELECT binding_id, local_slug, collection, plane_origin, workspace_uuid, project_uuid, config_revision, plane_instance, status, metadata_exclusion_revision, refresh_sequence
		FROM cairn.work_project WHERE collection = $1 AND local_slug = $2 FOR UPDATE`, collection, slug).Scan(
		&p.BindingID, &p.LocalSlug, &p.Collection, &p.PlaneOrigin,
		&p.WorkspaceUUID, &p.ProjectUUID, &p.ConfigRevision, &p.PlaneInstance,
		&p.Status, &p.MetadataExclusionRevision, &p.RefreshSequence)
	if errors.Is(err, pgx.ErrNoRows) {
		return RefreshContext{}, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", slug, collection))
	}
	if err != nil {
		return RefreshContext{}, err
	}
	if p.Status != "active" {
		return RefreshContext{}, failure("INVALID_REQUEST", fmt.Sprintf("work project %q is %s", slug, p.Status))
	}

	// Capture database generation under lock (STORE-6)
	gen, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return RefreshContext{}, err
	}

	nextSeq := p.RefreshSequence + 1
	_, err = tx.Exec(ctx, `UPDATE cairn.work_project SET refresh_sequence = $1 WHERE binding_id = $2`, nextSeq, p.BindingID)
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
		StagedAt:                  time.Now(),
	}

	return refCtx, tx.Commit(ctx)
}

func (s *Store) CommitRefreshSnapshot(ctx context.Context, refCtx RefreshContext, items []plane.NormalizedItem, authRejected bool, barrierHook func()) (*WorkSnapshot, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	// Validate DB generation has not changed via restore fence (STORE-6)
	currentGen, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return nil, err
	}
	if currentGen != refCtx.DatabaseGeneration {
		return nil, failure("STALE_RESTORE", "database restore fence occurred during refresh; staged data discarded")
	}

	var p WorkProject
	err = tx.QueryRow(ctx, `SELECT binding_id, local_slug, collection, plane_origin, workspace_uuid, project_uuid, config_revision, status, metadata_exclusion_revision, refresh_sequence
		FROM cairn.work_project WHERE binding_id = $1 FOR UPDATE`, refCtx.BindingID).Scan(
		&p.BindingID, &p.LocalSlug, &p.Collection, &p.PlaneOrigin,
		&p.WorkspaceUUID, &p.ProjectUUID, &p.ConfigRevision, &p.Status, &p.MetadataExclusionRevision, &p.RefreshSequence)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, failure("NOT_FOUND", fmt.Sprintf("work project binding %s not found", refCtx.BindingID))
	}
	if err != nil {
		return nil, err
	}

	if p.Status != "active" {
		return nil, failure("INVALID_REQUEST", fmt.Sprintf("work project %q is %s", p.LocalSlug, p.Status))
	}

	// Validate refresh sequence ordering (STORE-6)
	if refCtx.RefreshSequence < p.RefreshSequence {
		return nil, failure("VERSION_CONFLICT", "stale refresh sequence; newer refresh already committed")
	}

	if authRejected {
		// STORE-5: Record auth rejection, do NOT advance last_refreshed_at, keep associations intact!
		_, err = tx.Exec(ctx, `UPDATE cairn.work_project
			SET auth_rejected = true, last_auth_error = 'Plane auth rejected', updated_at = clock_timestamp()
			WHERE binding_id = $1`, p.BindingID)
		if err != nil {
			return nil, err
		}
		if err := tx.Commit(ctx); err != nil {
			return nil, err
		}
		return nil, plane.ErrAuthRejected
	}

	// Deterministic barrier hook for testing concurrency (AC-10)
	if barrierHook != nil {
		barrierHook()
		// Re-read metadata exclusion revision under lock to check if concurrent purge committed
		_ = tx.QueryRow(ctx, `SELECT metadata_exclusion_revision FROM cairn.work_project WHERE binding_id = $1`, p.BindingID).Scan(&p.MetadataExclusionRevision)
	}

	if len(items) > plane.MaxItemLimit {
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
		if err := exclRows.Scan(&e.fieldName, &e.pattern); err == nil {
			exclusions = append(exclusions, e)
		}
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

	// Mark auth reauthorized and update last_successful_refresh_at (STORE-5)
	_, err = tx.Exec(ctx, `UPDATE cairn.work_project
		SET auth_rejected = false, last_auth_error = NULL, last_successful_refresh_at = clock_timestamp(), updated_at = clock_timestamp()
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

func (s *Store) ListWorkItems(ctx context.Context, collection string, slug string, stateGroup string, limit int, offset int) ([]WorkItem, int, error) {
	if err := s.requireOperator(); err != nil {
		return nil, 0, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, 0, err
	}
	defer tx.Rollback(ctx)

	var bindingID uuid.UUID
	var authRejected bool
	err = tx.QueryRow(ctx, `SELECT binding_id, auth_rejected FROM cairn.work_project WHERE collection = $1 AND local_slug = $2`, collection, slug).Scan(&bindingID, &authRejected)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, 0, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", slug, collection))
	}
	if err != nil {
		return nil, 0, err
	}

	query := `SELECT item_id, binding_id, plane_item_uuid, identifier, title, state_id, state_name, state_group,
		assignee_ids, assignee_names, updated_at_source, source_url, cache_generation, is_absent, last_refreshed_at, refresh_error
		FROM cairn.work_item WHERE binding_id = $1`
	args := []any{bindingID}

	if stateGroup != "" {
		args = append(args, stateGroup)
		query += fmt.Sprintf(` AND state_group = $%d`, len(args))
	}

	// Count query
	countQuery := fmt.Sprintf(`SELECT count(*) FROM cairn.work_item WHERE binding_id = $1 %s`,
		func() string {
			if stateGroup != "" {
				return `AND state_group = $2`
			}
			return ""
		}())
	var total int
	if stateGroup != "" {
		_ = tx.QueryRow(ctx, countQuery, bindingID, stateGroup).Scan(&total)
	} else {
		_ = tx.QueryRow(ctx, countQuery, bindingID).Scan(&total)
	}

	query += ` ORDER BY identifier`
	if limit > 0 {
		args = append(args, limit)
		query += fmt.Sprintf(` LIMIT $%d`, len(args))
	}
	if offset > 0 {
		args = append(args, offset)
		query += fmt.Sprintf(` OFFSET $%d`, len(args))
	}

	rows, err := tx.Query(ctx, query, args...)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()

	now := time.Now()
	var items []WorkItem
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

		if it.LastRefreshedAt == nil || now.Sub(*it.LastRefreshedAt) > 2*time.Minute || it.CacheGeneration == 0 {
			it.IsStale = true
		}

		// STORE-5: Mask external fields if auth rejected
		if authRejected {
			it.Title = "[hidden: auth rejected]"
			it.Identifier = "[hidden]"
			it.StateName = ""
			it.StateGroup = ""
			it.AssigneeIDs = []string{}
			it.AssigneeNames = []string{}
			it.SourceURL = ""
			it.RefreshError = "Plane auth rejected"
		}

		items = append(items, it)
	}

	return items, total, tx.Commit(ctx)
}

func (s *Store) GetWorkItemDetail(ctx context.Context, collection string, slug string, itemIDOrIdentifier string) (WorkItemDetail, error) {
	if err := s.requireOperator(); err != nil {
		return WorkItemDetail{}, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return WorkItemDetail{}, err
	}
	defer tx.Rollback(ctx)

	var bindingID uuid.UUID
	var authRejected bool
	err = tx.QueryRow(ctx, `SELECT binding_id, auth_rejected FROM cairn.work_project WHERE collection = $1 AND local_slug = $2`, collection, slug).Scan(&bindingID, &authRejected)
	if errors.Is(err, pgx.ErrNoRows) {
		return WorkItemDetail{}, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", slug, collection))
	}
	if err != nil {
		return WorkItemDetail{}, err
	}

	var it WorkItem
	var assigneeIDsRaw, assigneeNamesRaw []byte
	var stateID *uuid.UUID
	var refreshErr *string

	itemUUID, parseErr := uuid.Parse(itemIDOrIdentifier)
	var row pgx.Row
	if parseErr == nil {
		row = tx.QueryRow(ctx, `SELECT item_id, binding_id, plane_item_uuid, identifier, title, state_id, state_name, state_group,
			assignee_ids, assignee_names, updated_at_source, source_url, cache_generation, is_absent, last_refreshed_at, refresh_error
			FROM cairn.work_item WHERE binding_id = $1 AND (item_id = $2 OR plane_item_uuid = $2)`, bindingID, itemUUID)
	} else {
		row = tx.QueryRow(ctx, `SELECT item_id, binding_id, plane_item_uuid, identifier, title, state_id, state_name, state_group,
			assignee_ids, assignee_names, updated_at_source, source_url, cache_generation, is_absent, last_refreshed_at, refresh_error
			FROM cairn.work_item WHERE binding_id = $1 AND identifier = $2`, bindingID, itemIDOrIdentifier)
	}

	err = row.Scan(&it.ItemID, &it.BindingID, &it.PlaneItemUUID, &it.Identifier, &it.Title,
		&stateID, &it.StateName, &it.StateGroup, &assigneeIDsRaw, &assigneeNamesRaw,
		&it.UpdatedAtSource, &it.SourceURL, &it.CacheGeneration, &it.IsAbsent,
		&it.LastRefreshedAt, &refreshErr)
	if errors.Is(err, pgx.ErrNoRows) {
		return WorkItemDetail{}, failure("NOT_FOUND", fmt.Sprintf("work item %q not found in project %q", itemIDOrIdentifier, slug))
	}
	if err != nil {
		return WorkItemDetail{}, err
	}
	it.StateID = stateID
	if refreshErr != nil {
		it.RefreshError = *refreshErr
	}
	_ = json.Unmarshal(assigneeIDsRaw, &it.AssigneeIDs)
	_ = json.Unmarshal(assigneeNamesRaw, &it.AssigneeNames)
	if it.LastRefreshedAt == nil || time.Since(*it.LastRefreshedAt) > 2*time.Minute || it.CacheGeneration == 0 {
		it.IsStale = true
	}

	// STORE-5: Mask external fields if auth rejected
	if authRejected {
		it.Title = "[hidden: auth rejected]"
		it.Identifier = "[hidden]"
		it.StateName = ""
		it.StateGroup = ""
		it.AssigneeIDs = []string{}
		it.AssigneeNames = []string{}
		it.SourceURL = ""
		it.RefreshError = "Plane auth rejected"
	}

	detail := WorkItemDetail{
		Item:         it,
		ProjectSlug:  slug,
		Collection:   collection,
		AuthRejected: authRejected,
	}

	// Read associations
	assocRows, err := tx.Query(ctx, `SELECT association_id, revision, binding_id, plane_item_uuid, target_type, target_id,
		target_agent_id, target_execution_id, target_event_id, target_record_id, target_version, target_group_id, target_run_id,
		rationale, task_summary, author, state, created_at, removed_at, removed_by
		FROM cairn.work_association WHERE binding_id = $1 AND plane_item_uuid = $2 AND state = 'active' ORDER BY created_at`, bindingID, it.PlaneItemUUID)
	if err != nil {
		return WorkItemDetail{}, err
	}
	defer assocRows.Close()

	for assocRows.Next() {
		var a WorkAssociation
		var taskSummary, rationale, removedBy *string
		if err := assocRows.Scan(&a.AssociationID, &a.Revision, &a.BindingID, &a.PlaneItemUUID,
			&a.TargetType, &a.TargetID,
			&a.TargetAgentID, &a.TargetExecutionID, &a.TargetEventID, &a.TargetRecordID, &a.TargetVersion, &a.TargetGroupID, &a.TargetRunID,
			&rationale, &taskSummary, &a.Author, &a.State, &a.CreatedAt, &a.RemovedAt, &removedBy); err != nil {
			return WorkItemDetail{}, err
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
		detail.Associations = append(detail.Associations, a)

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
			} else {
				// Session row missing or purged: historical
				sess.IsHistorical = true
				sess.TaskSummary = a.TaskSummary
			}
			detail.Sessions = append(detail.Sessions, sess)
		}

		// Process request/event associations (AC-05)
		if a.TargetType == "event" && a.TargetEventID != nil {
			eventUUID := *a.TargetEventID
			req := AssociatedRequest{
				AssociationID: a.AssociationID,
				EventID:       eventUUID,
				Rationale:     a.Rationale,
				DeliveryState: "pending",
			}

			var delState string
			var recordID uuid.UUID
			var version int
			dErr := tx.QueryRow(ctx, `SELECT state, record_id, version FROM cairn.agent_delivery d
				JOIN cairn.agent_event e ON d.event_id = e.event_id
				WHERE e.event_id = $1 LIMIT 1`, eventUUID).Scan(&delState, &recordID, &version)
			if dErr == nil {
				req.DeliveryState = delState
				var bodyPurged bool
				pErr := tx.QueryRow(ctx, `SELECT (body IS NULL OR body = '') FROM cairn.record_version WHERE record_id = $1 AND version = $2`, recordID, version).Scan(&bodyPurged)
				if pErr == nil && !bodyPurged {
					req.PayloadAvailable = true
				}
			}
			detail.Requests = append(detail.Requests, req)
		}
	}
	assocRows.Close()

	// Read annotations
	annotRows, err := tx.Query(ctx, `SELECT annotation_id, revision, binding_id, plane_item_uuid, kind, body, state, author, source_references, created_at, updated_at
		FROM cairn.work_annotation WHERE binding_id = $1 AND plane_item_uuid = $2 ORDER BY created_at`, bindingID, it.PlaneItemUUID)
	if err != nil {
		return WorkItemDetail{}, err
	}
	defer annotRows.Close()

	for annotRows.Next() {
		var an WorkAnnotation
		var srcRefsRaw []byte
		if err := annotRows.Scan(&an.AnnotationID, &an.Revision, &an.BindingID, &an.PlaneItemUUID,
			&an.Kind, &an.Body, &an.State, &an.Author, &srcRefsRaw, &an.CreatedAt, &an.UpdatedAt); err != nil {
			return WorkItemDetail{}, err
		}
		_ = json.Unmarshal(srcRefsRaw, &an.SourceReferences)
		detail.Annotations = append(detail.Annotations, an)
	}

	return detail, tx.Commit(ctx)
}
