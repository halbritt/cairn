package core

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

const MaxAnnotationBodyBytes = 2048

type WorkAnnotation struct {
	AnnotationID     uuid.UUID `json:"annotation_id"`
	Revision         int64     `json:"revision"`
	BindingID        uuid.UUID `json:"binding_id"`
	PlaneItemUUID    uuid.UUID `json:"plane_item_uuid"`
	Kind             string    `json:"kind"`
	Body             string    `json:"body"`
	State            string    `json:"state"`
	Author           string    `json:"author"`
	SourceReferences []string  `json:"source_references"`
	CreatedAt        time.Time `json:"created_at"`
	UpdatedAt        time.Time `json:"updated_at"`
}

type AddProgressAnnotationRequest struct {
	RequestID   string    `json:"request_id"`
	Collection  string    `json:"collection"`
	ProjectSlug string    `json:"project_slug"`
	ItemUUID    uuid.UUID `json:"item_uuid"`
	Body        string    `json:"body"`
}

type EditProgressAnnotationRequest struct {
	RequestID        string    `json:"request_id"`
	Collection       string    `json:"collection"`
	ProjectSlug      string    `json:"project_slug"`
	ItemUUID         uuid.UUID `json:"item_uuid,omitempty"`
	AnnotationID     uuid.UUID `json:"annotation_id"`
	Body             string    `json:"body"`
	ExpectedRevision int64     `json:"expected_revision"`
}

type AddBlockerAnnotationRequest struct {
	RequestID        string    `json:"request_id"`
	Collection       string    `json:"collection"`
	ProjectSlug      string    `json:"project_slug"`
	ItemUUID         uuid.UUID `json:"item_uuid"`
	Body             string    `json:"body"`
	SourceReferences []string  `json:"source_references,omitempty"`
}

type ResolveBlockerAnnotationRequest struct {
	RequestID        string    `json:"request_id"`
	Collection       string    `json:"collection"`
	ProjectSlug      string    `json:"project_slug"`
	ItemUUID         uuid.UUID `json:"item_uuid"`
	AnnotationID     uuid.UUID `json:"annotation_id"`
	ExpectedRevision int64     `json:"expected_revision"`
}

func (s *Store) AddProgressAnnotation(ctx context.Context, req AddProgressAnnotationRequest) (WorkAnnotation, error) {
	if err := s.requireOperator(); err != nil {
		return WorkAnnotation{}, err
	}
	if req.Collection == "" {
		req.Collection = s.channel.Repo
		if req.Collection == "" {
			req.Collection = "default"
		}
	}
	if err := s.checkRepo(req.Collection); err != nil {
		return WorkAnnotation{}, err
	}
	if strings.TrimSpace(req.ProjectSlug) == "" {
		return WorkAnnotation{}, failure("INVALID_REQUEST", "project slug required")
	}
	if req.ItemUUID == uuid.Nil {
		return WorkAnnotation{}, failure("INVALID_REQUEST", "item UUID required")
	}
	if strings.TrimSpace(req.Body) == "" {
		return WorkAnnotation{}, failure("INVALID_REQUEST", "annotation body required")
	}
	if len([]byte(req.Body)) > MaxAnnotationBodyBytes {
		return WorkAnnotation{}, failure("INVALID_REQUEST", fmt.Sprintf("progress annotation exceeds %d bytes", MaxAnnotationBodyBytes))
	}
	if req.RequestID == "" {
		req.RequestID = uuid.NewString()
	}

	return mutate(ctx, s, "work-progress-add", req.RequestID, req, func(tx pgx.Tx) (WorkAnnotation, error) {
		if err := s.checkWorkAdmissionTx(ctx, tx); err != nil {
			return WorkAnnotation{}, err
		}
		var bindingID uuid.UUID
		var projStatus string
		err := tx.QueryRow(ctx, `SELECT binding_id, status FROM cairn.work_project WHERE collection = $1 AND local_slug = $2`, req.Collection, req.ProjectSlug).Scan(&bindingID, &projStatus)
		if errors.Is(err, pgx.ErrNoRows) {
			return WorkAnnotation{}, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", req.ProjectSlug, req.Collection))
		}
		if err != nil {
			return WorkAnnotation{}, err
		}
		if projStatus != "active" {
			return WorkAnnotation{}, failure("INVALID_REQUEST", fmt.Sprintf("work project %q is %s", req.ProjectSlug, projStatus))
		}

		var planeItemUUID uuid.UUID
		err = tx.QueryRow(ctx, `SELECT plane_item_uuid FROM cairn.work_item WHERE binding_id = $1 AND (item_id = $2 OR plane_item_uuid = $2)`, bindingID, req.ItemUUID).Scan(&planeItemUUID)
		if errors.Is(err, pgx.ErrNoRows) {
			return WorkAnnotation{}, failure("NOT_FOUND", fmt.Sprintf("work item %s not found in project %s", req.ItemUUID, req.ProjectSlug))
		}
		if err != nil {
			return WorkAnnotation{}, err
		}

		// Supersede existing active progress annotations for this item
		_, err = tx.Exec(ctx, `UPDATE cairn.work_annotation SET state = 'superseded', updated_at = clock_timestamp()
			WHERE binding_id = $1 AND plane_item_uuid = $2 AND kind = 'progress' AND state = 'active'`,
			bindingID, planeItemUUID)
		if err != nil {
			return WorkAnnotation{}, err
		}

		an := WorkAnnotation{
			AnnotationID:     uuid.New(),
			Revision:         1,
			BindingID:        bindingID,
			PlaneItemUUID:    planeItemUUID,
			Kind:             "progress",
			Body:             req.Body,
			State:            "active",
			Author:           s.channel.Principal,
			SourceReferences: []string{},
		}

		srcRefsJSON, _ := json.Marshal(an.SourceReferences)
		err = tx.QueryRow(ctx, `INSERT INTO cairn.work_annotation (
			annotation_id, revision, binding_id, plane_item_uuid, kind, body, state, author, source_references
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
		RETURNING created_at, updated_at`,
			an.AnnotationID, an.Revision, an.BindingID, an.PlaneItemUUID, an.Kind, an.Body, an.State, an.Author, srcRefsJSON).
			Scan(&an.CreatedAt, &an.UpdatedAt)
		if err != nil {
			return WorkAnnotation{}, err
		}

		// Record history row (STORE-3)
		_, err = tx.Exec(ctx, `INSERT INTO cairn.work_annotation_history (annotation_id, revision, body, state, author, source_references)
			VALUES ($1, $2, $3, $4, $5, $6)`, an.AnnotationID, an.Revision, an.Body, an.State, an.Author, srcRefsJSON)
		if err != nil {
			return WorkAnnotation{}, err
		}

		return an, nil
	})
}

func (s *Store) EditProgressAnnotation(ctx context.Context, req EditProgressAnnotationRequest) (WorkAnnotation, error) {
	if err := s.requireOperator(); err != nil {
		return WorkAnnotation{}, err
	}
	if req.Collection == "" {
		req.Collection = s.channel.Repo
		if req.Collection == "" {
			req.Collection = "default"
		}
	}
	if err := s.checkRepo(req.Collection); err != nil {
		return WorkAnnotation{}, err
	}
	if strings.TrimSpace(req.ProjectSlug) == "" {
		return WorkAnnotation{}, failure("INVALID_REQUEST", "project slug required")
	}
	if req.ItemUUID == uuid.Nil {
		return WorkAnnotation{}, failure("INVALID_REQUEST", "item UUID required")
	}
	if req.AnnotationID == uuid.Nil {
		return WorkAnnotation{}, failure("INVALID_REQUEST", "annotation UUID required")
	}
	if req.ExpectedRevision <= 0 {
		return WorkAnnotation{}, failure("INVALID_REQUEST", "positive expected_revision required")
	}
	if strings.TrimSpace(req.Body) == "" {
		return WorkAnnotation{}, failure("INVALID_REQUEST", "annotation body required")
	}
	if len([]byte(req.Body)) > MaxAnnotationBodyBytes {
		return WorkAnnotation{}, failure("INVALID_REQUEST", fmt.Sprintf("progress annotation exceeds %d bytes", MaxAnnotationBodyBytes))
	}
	if req.RequestID == "" {
		req.RequestID = uuid.NewString()
	}

	return mutate(ctx, s, "work-progress-edit", req.RequestID, req, func(tx pgx.Tx) (WorkAnnotation, error) {
		if err := s.checkWorkAdmissionTx(ctx, tx); err != nil {
			return WorkAnnotation{}, err
		}
		var an WorkAnnotation
		var srcRefsRaw []byte
		var projStatus string
		err := tx.QueryRow(ctx, `SELECT a.annotation_id, a.revision, a.binding_id, a.plane_item_uuid, a.kind, a.body, a.state, a.author, a.source_references, a.created_at, a.updated_at, p.status
			FROM cairn.work_annotation a
			JOIN cairn.work_project p ON a.binding_id = p.binding_id
			WHERE a.annotation_id = $1 AND p.collection = $2 AND p.local_slug = $3
			FOR UPDATE OF a`, req.AnnotationID, req.Collection, req.ProjectSlug).Scan(
			&an.AnnotationID, &an.Revision, &an.BindingID, &an.PlaneItemUUID, &an.Kind, &an.Body, &an.State, &an.Author, &srcRefsRaw, &an.CreatedAt, &an.UpdatedAt, &projStatus)
		if errors.Is(err, pgx.ErrNoRows) {
			return WorkAnnotation{}, failure("NOT_FOUND", "annotation not found in requested project and collection")
		}
		if err != nil {
			return WorkAnnotation{}, err
		}
		if projStatus != "active" {
			return WorkAnnotation{}, failure("INVALID_REQUEST", fmt.Sprintf("work project %q is %s", req.ProjectSlug, projStatus))
		}
		_ = json.Unmarshal(srcRefsRaw, &an.SourceReferences)
		if an.SourceReferences == nil {
			an.SourceReferences = make([]string, 0)
		}

		var match bool
		err = tx.QueryRow(ctx, `SELECT EXISTS(
			SELECT 1 FROM cairn.work_item WHERE binding_id = $1 AND plane_item_uuid = $2 AND (item_id = $3 OR plane_item_uuid = $3)
		)`, an.BindingID, an.PlaneItemUUID, req.ItemUUID).Scan(&match)
		if err != nil {
			return WorkAnnotation{}, err
		}
		if !match {
			return WorkAnnotation{}, failure("NOT_FOUND", fmt.Sprintf("annotation %s does not belong to item %s", req.AnnotationID, req.ItemUUID))
		}

		if an.Kind != "progress" {
			return WorkAnnotation{}, failure("INVALID_REQUEST", "target annotation is not a progress annotation")
		}

		if an.Revision != req.ExpectedRevision {
			return WorkAnnotation{}, failure("VERSION_CONFLICT", fmt.Sprintf("expected revision %d, current is %d", req.ExpectedRevision, an.Revision))
		}

		newRev := an.Revision + 1
		an.Body = req.Body
		an.Revision = newRev
		an.Author = s.channel.Principal

		srcRefsJSON, _ := json.Marshal(an.SourceReferences)
		err = tx.QueryRow(ctx, `UPDATE cairn.work_annotation SET body = $1, revision = $2, author = $3, updated_at = clock_timestamp()
			WHERE annotation_id = $4
			RETURNING updated_at`, an.Body, an.Revision, an.Author, an.AnnotationID).Scan(&an.UpdatedAt)
		if err != nil {
			return WorkAnnotation{}, err
		}

		// Record history row (STORE-3)
		_, err = tx.Exec(ctx, `INSERT INTO cairn.work_annotation_history (annotation_id, revision, body, state, author, source_references)
			VALUES ($1, $2, $3, $4, $5, $6)`, an.AnnotationID, an.Revision, an.Body, an.State, an.Author, srcRefsJSON)
		if err != nil {
			return WorkAnnotation{}, err
		}

		return an, nil
	})
}

func (s *Store) AddBlockerAnnotation(ctx context.Context, req AddBlockerAnnotationRequest) (WorkAnnotation, error) {
	if err := s.requireOperator(); err != nil {
		return WorkAnnotation{}, err
	}
	if req.Collection == "" {
		req.Collection = s.channel.Repo
		if req.Collection == "" {
			req.Collection = "default"
		}
	}
	if err := s.checkRepo(req.Collection); err != nil {
		return WorkAnnotation{}, err
	}
	if strings.TrimSpace(req.ProjectSlug) == "" {
		return WorkAnnotation{}, failure("INVALID_REQUEST", "project slug required")
	}
	if req.ItemUUID == uuid.Nil {
		return WorkAnnotation{}, failure("INVALID_REQUEST", "item UUID required")
	}
	if strings.TrimSpace(req.Body) == "" {
		return WorkAnnotation{}, failure("INVALID_REQUEST", "annotation body required")
	}
	if len([]byte(req.Body)) > MaxAnnotationBodyBytes {
		return WorkAnnotation{}, failure("INVALID_REQUEST", fmt.Sprintf("blocker annotation exceeds %d bytes", MaxAnnotationBodyBytes))
	}
	if req.RequestID == "" {
		req.RequestID = uuid.NewString()
	}

	return mutate(ctx, s, "work-blocker-add", req.RequestID, req, func(tx pgx.Tx) (WorkAnnotation, error) {
		if err := s.checkWorkAdmissionTx(ctx, tx); err != nil {
			return WorkAnnotation{}, err
		}
		var bindingID uuid.UUID
		var projStatus string
		err := tx.QueryRow(ctx, `SELECT binding_id, status FROM cairn.work_project WHERE collection = $1 AND local_slug = $2`, req.Collection, req.ProjectSlug).Scan(&bindingID, &projStatus)
		if errors.Is(err, pgx.ErrNoRows) {
			return WorkAnnotation{}, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", req.ProjectSlug, req.Collection))
		}
		if err != nil {
			return WorkAnnotation{}, err
		}
		if projStatus != "active" {
			return WorkAnnotation{}, failure("INVALID_REQUEST", fmt.Sprintf("work project %q is %s", req.ProjectSlug, projStatus))
		}

		var planeItemUUID uuid.UUID
		err = tx.QueryRow(ctx, `SELECT plane_item_uuid FROM cairn.work_item WHERE binding_id = $1 AND (item_id = $2 OR plane_item_uuid = $2)`, bindingID, req.ItemUUID).Scan(&planeItemUUID)
		if errors.Is(err, pgx.ErrNoRows) {
			return WorkAnnotation{}, failure("NOT_FOUND", fmt.Sprintf("work item %s not found in project %s", req.ItemUUID, req.ProjectSlug))
		}
		if err != nil {
			return WorkAnnotation{}, err
		}

		srcRefs := req.SourceReferences
		if srcRefs == nil {
			srcRefs = []string{}
		}

		an := WorkAnnotation{
			AnnotationID:     uuid.New(),
			Revision:         1,
			BindingID:        bindingID,
			PlaneItemUUID:    planeItemUUID,
			Kind:             "blocker",
			Body:             req.Body,
			State:            "open",
			Author:           s.channel.Principal,
			SourceReferences: srcRefs,
		}

		srcRefsJSON, _ := json.Marshal(an.SourceReferences)
		err = tx.QueryRow(ctx, `INSERT INTO cairn.work_annotation (
			annotation_id, revision, binding_id, plane_item_uuid, kind, body, state, author, source_references
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
		RETURNING created_at, updated_at`,
			an.AnnotationID, an.Revision, an.BindingID, an.PlaneItemUUID, an.Kind, an.Body, an.State, an.Author, srcRefsJSON).
			Scan(&an.CreatedAt, &an.UpdatedAt)
		if err != nil {
			return WorkAnnotation{}, err
		}

		// Record history row (STORE-3)
		_, err = tx.Exec(ctx, `INSERT INTO cairn.work_annotation_history (annotation_id, revision, body, state, author, source_references)
			VALUES ($1, $2, $3, $4, $5, $6)`, an.AnnotationID, an.Revision, an.Body, an.State, an.Author, srcRefsJSON)
		if err != nil {
			return WorkAnnotation{}, err
		}

		return an, nil
	})
}

func (s *Store) ResolveBlockerAnnotation(ctx context.Context, req ResolveBlockerAnnotationRequest) (WorkAnnotation, error) {
	if err := s.requireOperator(); err != nil {
		return WorkAnnotation{}, err
	}
	if req.Collection == "" {
		req.Collection = s.channel.Repo
		if req.Collection == "" {
			req.Collection = "default"
		}
	}
	if err := s.checkRepo(req.Collection); err != nil {
		return WorkAnnotation{}, err
	}
	if strings.TrimSpace(req.ProjectSlug) == "" {
		return WorkAnnotation{}, failure("INVALID_REQUEST", "project slug required")
	}
	if req.ItemUUID == uuid.Nil {
		return WorkAnnotation{}, failure("INVALID_REQUEST", "item UUID required")
	}
	if req.AnnotationID == uuid.Nil {
		return WorkAnnotation{}, failure("INVALID_REQUEST", "annotation UUID required")
	}
	if req.ExpectedRevision <= 0 {
		return WorkAnnotation{}, failure("INVALID_REQUEST", "positive expected_revision required")
	}
	if req.RequestID == "" {
		req.RequestID = uuid.NewString()
	}

	return mutate(ctx, s, "work-blocker-resolve", req.RequestID, req, func(tx pgx.Tx) (WorkAnnotation, error) {
		if err := s.checkWorkAdmissionTx(ctx, tx); err != nil {
			return WorkAnnotation{}, err
		}
		var an WorkAnnotation
		var srcRefsRaw []byte
		var projStatus string
		err := tx.QueryRow(ctx, `SELECT a.annotation_id, a.revision, a.binding_id, a.plane_item_uuid, a.kind, a.body, a.state, a.author, a.source_references, a.created_at, a.updated_at, p.status
			FROM cairn.work_annotation a
			JOIN cairn.work_project p ON a.binding_id = p.binding_id
			WHERE a.annotation_id = $1 AND p.collection = $2 AND p.local_slug = $3
			FOR UPDATE OF a`, req.AnnotationID, req.Collection, req.ProjectSlug).Scan(
			&an.AnnotationID, &an.Revision, &an.BindingID, &an.PlaneItemUUID, &an.Kind, &an.Body, &an.State, &an.Author, &srcRefsRaw, &an.CreatedAt, &an.UpdatedAt, &projStatus)
		if errors.Is(err, pgx.ErrNoRows) {
			return WorkAnnotation{}, failure("NOT_FOUND", "blocker annotation not found in requested project and collection")
		}
		if err != nil {
			return WorkAnnotation{}, err
		}
		if projStatus != "active" {
			return WorkAnnotation{}, failure("INVALID_REQUEST", fmt.Sprintf("work project %q is %s", req.ProjectSlug, projStatus))
		}
		_ = json.Unmarshal(srcRefsRaw, &an.SourceReferences)
		if an.SourceReferences == nil {
			an.SourceReferences = make([]string, 0)
		}

		var match bool
		err = tx.QueryRow(ctx, `SELECT EXISTS(
			SELECT 1 FROM cairn.work_item WHERE binding_id = $1 AND plane_item_uuid = $2 AND (item_id = $3 OR plane_item_uuid = $3)
		)`, an.BindingID, an.PlaneItemUUID, req.ItemUUID).Scan(&match)
		if err != nil {
			return WorkAnnotation{}, err
		}
		if !match {
			return WorkAnnotation{}, failure("NOT_FOUND", fmt.Sprintf("annotation %s does not belong to item %s", req.AnnotationID, req.ItemUUID))
		}

		if an.Kind != "blocker" {
			return WorkAnnotation{}, failure("INVALID_REQUEST", "target annotation is not a blocker")
		}

		if an.Revision != req.ExpectedRevision {
			return WorkAnnotation{}, failure("VERSION_CONFLICT", fmt.Sprintf("expected revision %d, current is %d", req.ExpectedRevision, an.Revision))
		}

		if an.State == "resolved" {
			return WorkAnnotation{}, failure("VERSION_CONFLICT", "blocker is already resolved")
		}

		newRev := an.Revision + 1
		an.State = "resolved"
		an.Revision = newRev
		an.Author = s.channel.Principal

		srcRefsJSON, _ := json.Marshal(an.SourceReferences)
		err = tx.QueryRow(ctx, `UPDATE cairn.work_annotation SET state = 'resolved', revision = $1, author = $2, updated_at = clock_timestamp()
			WHERE annotation_id = $3
			RETURNING updated_at`, an.Revision, an.Author, an.AnnotationID).Scan(&an.UpdatedAt)
		if err != nil {
			return WorkAnnotation{}, err
		}

		// Record history row (STORE-3)
		_, err = tx.Exec(ctx, `INSERT INTO cairn.work_annotation_history (annotation_id, revision, body, state, author, source_references)
			VALUES ($1, $2, $3, $4, $5, $6)`, an.AnnotationID, an.Revision, an.Body, an.State, an.Author, srcRefsJSON)
		if err != nil {
			return WorkAnnotation{}, err
		}

		return an, nil
	})
}
