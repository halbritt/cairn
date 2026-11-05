package core

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type WorkMetadataExclusion struct {
	ExclusionID uuid.UUID `json:"exclusion_id"`
	BindingID   uuid.UUID `json:"binding_id"`
	FieldName   string    `json:"field_name"`
	Pattern     string    `json:"pattern"`
	Revision    int64     `json:"revision"`
	CreatedAt   time.Time `json:"created_at"`
}

type PurgeMetadataRequest struct {
	RequestID   string `json:"request_id,omitempty"`
	Collection  string `json:"collection"`
	ProjectSlug string `json:"project_slug"`
	Pattern     string `json:"pattern"`
	FieldName   string `json:"field_name,omitempty"`
	Exclude     bool   `json:"exclude"`
}

type ExcludeMetadataRequest struct {
	RequestID   string `json:"request_id,omitempty"`
	Collection  string `json:"collection"`
	ProjectSlug string `json:"project_slug"`
	FieldName   string `json:"field_name"`
	Pattern     string `json:"pattern"`
}

type PurgeResult struct {
	PurgedItems       int64 `json:"purged_items"`
	PurgedAnnotations int64 `json:"purged_annotations"`
	PurgedAssocs      int64 `json:"purged_associations"`
	ExclusionRevision int64 `json:"exclusion_revision"`
}

func (s *Store) PurgeMetadata(ctx context.Context, req PurgeMetadataRequest) (PurgeResult, error) {
	if err := s.requireOperator(); err != nil {
		return PurgeResult{}, err
	}
	if req.Collection == "" {
		req.Collection = s.channel.Repo
		if req.Collection == "" {
			req.Collection = "default"
		}
	}
	if err := s.checkRepo(req.Collection); err != nil {
		return PurgeResult{}, err
	}
	if strings.TrimSpace(req.ProjectSlug) == "" {
		return PurgeResult{}, failure("INVALID_REQUEST", "project slug required")
	}
	if strings.TrimSpace(req.Pattern) == "" {
		return PurgeResult{}, failure("INVALID_REQUEST", "purge pattern required")
	}
	if req.FieldName == "" {
		req.FieldName = "all"
	}
	if req.FieldName != "title" && req.FieldName != "annotation" && req.FieldName != "all" {
		return PurgeResult{}, failure("INVALID_REQUEST", fmt.Sprintf("invalid field_name %q", req.FieldName))
	}

	return mutate(ctx, s, "work-metadata-purge", req.RequestID, req, func(tx pgx.Tx) (PurgeResult, error) {
		var bindingID uuid.UUID
		var newRev int64
		err := tx.QueryRow(ctx, `UPDATE cairn.work_project
			SET metadata_exclusion_revision = metadata_exclusion_revision + 1, updated_at = clock_timestamp()
			WHERE collection = $1 AND local_slug = $2
			RETURNING binding_id, metadata_exclusion_revision`, req.Collection, req.ProjectSlug).Scan(&bindingID, &newRev)
		if errors.Is(err, pgx.ErrNoRows) {
			return PurgeResult{}, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", req.ProjectSlug, req.Collection))
		}
		if err != nil {
			return PurgeResult{}, err
		}

		res := PurgeResult{ExclusionRevision: newRev}
		patternParam := "%" + req.Pattern + "%"

		// 1. Purge titles in work_item
		if req.FieldName == "title" || req.FieldName == "all" {
			tag, err := tx.Exec(ctx, `UPDATE cairn.work_item
				SET title = '[purged]'
				WHERE binding_id = $1 AND title ILIKE $2`, bindingID, patternParam)
			if err != nil {
				return PurgeResult{}, err
			}
			res.PurgedItems = tag.RowsAffected()
		}

		// 2. Purge annotations in work_annotation and work_annotation_history
		if req.FieldName == "annotation" || req.FieldName == "all" {
			tag1, err := tx.Exec(ctx, `UPDATE cairn.work_annotation
				SET body = '[purged]'
				WHERE binding_id = $1 AND body ILIKE $2`, bindingID, patternParam)
			if err != nil {
				return PurgeResult{}, err
			}
			tag2, err := tx.Exec(ctx, `UPDATE cairn.work_annotation_history h
				SET body = '[purged]'
				FROM cairn.work_annotation a
				WHERE h.annotation_id = a.annotation_id AND a.binding_id = $1 AND h.body ILIKE $2`, bindingID, patternParam)
			if err != nil {
				return PurgeResult{}, err
			}
			res.PurgedAnnotations = tag1.RowsAffected() + tag2.RowsAffected()
		}

		// 3. Purge association rationales
		tagAssoc, err := tx.Exec(ctx, `UPDATE cairn.work_association
			SET rationale = '[purged]'
			WHERE binding_id = $1 AND rationale ILIKE $2`, bindingID, patternParam)
		if err != nil {
			return PurgeResult{}, err
		}
		_, _ = tx.Exec(ctx, `UPDATE cairn.work_association_history h
			SET rationale = '[purged]'
			FROM cairn.work_association a
			WHERE h.association_id = a.association_id AND a.binding_id = $1 AND h.rationale ILIKE $2`, bindingID, patternParam)
		res.PurgedAssocs = tagAssoc.RowsAffected()

		// 4. Scrub mutation_request responses tied to this exact binding
		_, _ = tx.Exec(ctx, `UPDATE cairn.mutation_request
			SET response = '{"purged": true}'::jsonb, ordinary_deleted = true
			WHERE operation LIKE 'work-%'
			  AND (response->>'binding_id' = $1::text OR response->>'project_slug' = $2 OR request_payload->>'project_slug' = $2)
			  AND (response->>'body' ILIKE $3 OR response->>'text' ILIKE $3 OR response->>'rationale' ILIKE $3 OR response::text ILIKE $3)`,
			bindingID, req.ProjectSlug, patternParam)

		// 5. Register persistent exclusion
		if req.Exclude {
			_, err = tx.Exec(ctx, `INSERT INTO cairn.work_metadata_exclusion (binding_id, field_name, pattern, revision)
				VALUES ($1, $2, $3, $4)`, bindingID, req.FieldName, req.Pattern, newRev)
			if err != nil {
				return PurgeResult{}, err
			}
		}

		return res, nil
	})
}

func (s *Store) ExcludeMetadata(ctx context.Context, req ExcludeMetadataRequest) (WorkMetadataExclusion, error) {
	if err := s.requireOperator(); err != nil {
		return WorkMetadataExclusion{}, err
	}
	if req.Collection == "" {
		req.Collection = s.channel.Repo
		if req.Collection == "" {
			req.Collection = "default"
		}
	}
	if err := s.checkRepo(req.Collection); err != nil {
		return WorkMetadataExclusion{}, err
	}
	if strings.TrimSpace(req.ProjectSlug) == "" {
		return WorkMetadataExclusion{}, failure("INVALID_REQUEST", "project slug required")
	}
	if strings.TrimSpace(req.Pattern) == "" {
		return WorkMetadataExclusion{}, failure("INVALID_REQUEST", "exclusion pattern required")
	}
	if req.FieldName == "" {
		req.FieldName = "all"
	}
	if req.FieldName != "title" && req.FieldName != "annotation" && req.FieldName != "all" {
		return WorkMetadataExclusion{}, failure("INVALID_REQUEST", fmt.Sprintf("invalid field_name %q", req.FieldName))
	}

	return mutate(ctx, s, "work-metadata-exclude", req.RequestID, req, func(tx pgx.Tx) (WorkMetadataExclusion, error) {
		var bindingID uuid.UUID
		var newRev int64
		err := tx.QueryRow(ctx, `UPDATE cairn.work_project
			SET metadata_exclusion_revision = metadata_exclusion_revision + 1, updated_at = clock_timestamp()
			WHERE collection = $1 AND local_slug = $2
			RETURNING binding_id, metadata_exclusion_revision`, req.Collection, req.ProjectSlug).Scan(&bindingID, &newRev)
		if errors.Is(err, pgx.ErrNoRows) {
			return WorkMetadataExclusion{}, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", req.ProjectSlug, req.Collection))
		}
		if err != nil {
			return WorkMetadataExclusion{}, err
		}

		var excl WorkMetadataExclusion
		err = tx.QueryRow(ctx, `INSERT INTO cairn.work_metadata_exclusion (binding_id, field_name, pattern, revision)
			VALUES ($1, $2, $3, $4)
			RETURNING exclusion_id, binding_id, field_name, pattern, revision, created_at`,
			bindingID, req.FieldName, req.Pattern, newRev).Scan(
			&excl.ExclusionID, &excl.BindingID, &excl.FieldName, &excl.Pattern, &excl.Revision, &excl.CreatedAt)
		if err != nil {
			return WorkMetadataExclusion{}, err
		}
		return excl, nil
	})
}

// ReapplyExclusions reapplies all stored exclusions across work items and annotations.
// Called during restore recovery (AC-09).
func ReapplyExclusions(ctx context.Context, tx pgx.Tx) error {
	rows, err := tx.Query(ctx, `SELECT binding_id, field_name, pattern FROM cairn.work_metadata_exclusion`)
	if err != nil {
		return err
	}
	defer rows.Close()

	type exRec struct {
		bindingID uuid.UUID
		fieldName string
		pattern   string
	}
	var exclusions []exRec
	for rows.Next() {
		var e exRec
		if err := rows.Scan(&e.bindingID, &e.fieldName, &e.pattern); err == nil {
			exclusions = append(exclusions, e)
		}
	}
	rows.Close()

	for _, e := range exclusions {
		patternParam := "%" + e.pattern + "%"
		if e.fieldName == "title" || e.fieldName == "all" {
			_, err = tx.Exec(ctx, `UPDATE cairn.work_item SET title = '[redacted]' WHERE binding_id = $1 AND title ILIKE $2`, e.bindingID, patternParam)
			if err != nil {
				return err
			}
		}
		if e.fieldName == "annotation" || e.fieldName == "all" {
			_, err = tx.Exec(ctx, `UPDATE cairn.work_annotation SET body = '[redacted]' WHERE binding_id = $1 AND body ILIKE $2`, e.bindingID, patternParam)
			if err != nil {
				return err
			}
		}
	}
	return nil
}
