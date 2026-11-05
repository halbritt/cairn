package core

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type WorkAssociation struct {
	AssociationID     uuid.UUID  `json:"association_id"`
	Revision          int64      `json:"revision"`
	BindingID         uuid.UUID  `json:"binding_id"`
	PlaneItemUUID     uuid.UUID  `json:"plane_item_uuid"`
	TargetType        string     `json:"target_type"`
	TargetID          string     `json:"target_id"`
	TargetAgentID     *uuid.UUID `json:"target_agent_id,omitempty"`
	TargetExecutionID *uuid.UUID `json:"target_execution_id,omitempty"`
	TargetEventID     *uuid.UUID `json:"target_event_id,omitempty"`
	TargetRecordID    *uuid.UUID `json:"target_record_id,omitempty"`
	TargetVersion     *int       `json:"target_version,omitempty"`
	TargetGroupID     *uuid.UUID `json:"target_group_id,omitempty"`
	TargetRunID       string     `json:"target_run_id,omitempty"`
	Rationale         string     `json:"rationale,omitempty"`
	TaskSummary       string     `json:"task_summary,omitempty"`
	Author            string     `json:"author"`
	State             string     `json:"state"`
	CreatedAt         time.Time  `json:"created_at"`
	RemovedAt         *time.Time `json:"removed_at,omitempty"`
	RemovedBy         string     `json:"removed_by,omitempty"`
}

type AssociateWorkRequest struct {
	RequestID   string    `json:"request_id"`
	Collection  string    `json:"collection"`
	ProjectSlug string    `json:"project_slug"`
	ItemUUID    uuid.UUID `json:"item_uuid"`
	TargetType  string    `json:"target_type"`
	TargetID    string    `json:"target_id"`
	Rationale   string    `json:"rationale,omitempty"`
}

type UnassociateWorkRequest struct {
	RequestID        string    `json:"request_id"`
	Collection       string    `json:"collection"`
	ProjectSlug      string    `json:"project_slug"`
	AssociationID    uuid.UUID `json:"association_id,omitempty"`
	ItemUUID         uuid.UUID `json:"item_uuid,omitempty"`
	TargetType       string    `json:"target_type,omitempty"`
	TargetID         string    `json:"target_id,omitempty"`
	ExpectedRevision *int64    `json:"expected_revision,omitempty"`
}

func (s *Store) AssociateWork(ctx context.Context, req AssociateWorkRequest) (WorkAssociation, error) {
	if err := s.requireOperator(); err != nil {
		return WorkAssociation{}, err
	}
	if req.Collection == "" {
		req.Collection = s.channel.Repo
		if req.Collection == "" {
			req.Collection = "default"
		}
	}
	if err := s.checkRepo(req.Collection); err != nil {
		return WorkAssociation{}, err
	}
	if strings.TrimSpace(req.ProjectSlug) == "" {
		return WorkAssociation{}, failure("INVALID_REQUEST", "project slug required")
	}
	if req.ItemUUID == uuid.Nil {
		return WorkAssociation{}, failure("INVALID_REQUEST", "item UUID required")
	}

	validTypes := map[string]bool{
		"session":        true,
		"event":          true,
		"response_group": true,
		"run_receipt":    true,
		"result_record":  true,
	}
	if !validTypes[req.TargetType] {
		return WorkAssociation{}, failure("INVALID_REQUEST", fmt.Sprintf("invalid target_type %q", req.TargetType))
	}
	if strings.TrimSpace(req.TargetID) == "" {
		return WorkAssociation{}, failure("INVALID_REQUEST", "target_id required")
	}

	return mutate(ctx, s, "work-associate", req.RequestID, req, func(tx pgx.Tx) (WorkAssociation, error) {
		var bindingID uuid.UUID
		err := tx.QueryRow(ctx, `SELECT binding_id FROM cairn.work_project WHERE collection = $1 AND local_slug = $2`, req.Collection, req.ProjectSlug).Scan(&bindingID)
		if errors.Is(err, pgx.ErrNoRows) {
			return WorkAssociation{}, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", req.ProjectSlug, req.Collection))
		}
		if err != nil {
			return WorkAssociation{}, err
		}

		// Verify target item exists
		var planeItemUUID uuid.UUID
		err = tx.QueryRow(ctx, `SELECT plane_item_uuid FROM cairn.work_item WHERE binding_id = $1 AND (item_id = $2 OR plane_item_uuid = $2)`, bindingID, req.ItemUUID).Scan(&planeItemUUID)
		if errors.Is(err, pgx.ErrNoRows) {
			return WorkAssociation{}, failure("NOT_FOUND", fmt.Sprintf("work item %s not found in project %s", req.ItemUUID, req.ProjectSlug))
		}
		if err != nil {
			return WorkAssociation{}, err
		}

		var targetAgentID *uuid.UUID
		var targetExecID *uuid.UUID
		var targetEventID *uuid.UUID
		var targetRecordID *uuid.UUID
		var targetVersion *int
		var targetGroupID *uuid.UUID
		var targetRunID string
		var taskSummary string

		// Target validation
		switch req.TargetType {
		case "session", "execution":
			delim := ":"
			if strings.Contains(req.TargetID, "/") {
				delim = "/"
			}
			parts := strings.Split(req.TargetID, delim)
			if len(parts) != 2 {
				return WorkAssociation{}, failure("INVALID_REQUEST", "session target_id must be <agent_uuid>:<execution_uuid> or <agent_uuid>/<execution_uuid>")
			}
			aUUID, err1 := uuid.Parse(parts[0])
			eUUID, err2 := uuid.Parse(parts[1])
			if err1 != nil || err2 != nil {
				return WorkAssociation{}, failure("INVALID_REQUEST", "invalid UUIDs in session target_id")
			}
			var metaRaw []byte
			var actualExecUUID uuid.UUID
			err = tx.QueryRow(ctx, `SELECT execution_id, metadata FROM cairn.agent_session WHERE agent_id = $1`, aUUID).Scan(&actualExecUUID, &metaRaw)
			if err == nil && actualExecUUID == eUUID {
				var meta AgentMetadata
				_ = json.Unmarshal(metaRaw, &meta)
				taskSummary = meta.TaskSummary
			}
			targetAgentID = &aUUID
			targetExecID = &eUUID
			req.TargetID = fmt.Sprintf("%s:%s", aUUID, eUUID)

		case "request":
			targetRunID = req.TargetID
			if u, err := uuid.Parse(req.TargetID); err == nil {
				targetEventID = &u
			}

		case "event":
			evUUID, err := uuid.Parse(req.TargetID)
			if err != nil {
				return WorkAssociation{}, failure("INVALID_REQUEST", "event target_id must be a UUID")
			}
			targetEventID = &evUUID
			req.TargetID = evUUID.String()

		case "result_record", "record", "result":
			delim := ":"
			if strings.Contains(req.TargetID, "/") {
				delim = "/"
			}
			parts := strings.Split(req.TargetID, delim)
			if len(parts) != 2 {
				return WorkAssociation{}, failure("INVALID_REQUEST", "result_record target_id must be <record_uuid>:<version> or <record_uuid>/<version>")
			}
			rUUID, err1 := uuid.Parse(parts[0])
			ver, err2 := strconv.Atoi(parts[1])
			if err1 != nil || err2 != nil || ver <= 0 {
				return WorkAssociation{}, failure("INVALID_REQUEST", "result_record requires a valid record UUID and positive version")
			}
			var exists bool
			err = tx.QueryRow(ctx, `SELECT EXISTS(
				SELECT 1 FROM cairn.memory_record r
				JOIN cairn.record_version v ON r.record_id = v.record_id
				WHERE r.record_id = $1 AND v.version = $2 AND r.repo = $3
			)`, rUUID, ver, req.Collection).Scan(&exists)
			if err != nil {
				return WorkAssociation{}, err
			}
			if !exists {
				return WorkAssociation{}, failure("NOT_FOUND", fmt.Sprintf("record %s version %d not found in collection %q", rUUID, ver, req.Collection))
			}
			targetRecordID = &rUUID
			targetVersion = &ver
			req.TargetID = fmt.Sprintf("%s:%d", rUUID, ver)

		case "response_group":
			grpUUID, err := uuid.Parse(req.TargetID)
			if err != nil {
				return WorkAssociation{}, failure("INVALID_REQUEST", "response_group target_id must be a valid UUID")
			}
			targetGroupID = &grpUUID
			req.TargetID = grpUUID.String()

		case "run_receipt":
			targetRunID = req.TargetID
		}

		// Check if association already exists
		var existing WorkAssociation
		var prevRationale, prevTaskSummary *string
		err = tx.QueryRow(ctx, `SELECT association_id, revision, binding_id, plane_item_uuid, target_type, target_id,
			target_agent_id, target_execution_id, target_event_id, target_record_id, target_version, target_group_id, target_run_id,
			rationale, task_summary, author, state, created_at, removed_at
			FROM cairn.work_association
			WHERE binding_id = $1 AND plane_item_uuid = $2 AND target_type = $3 AND target_id = $4`,
			bindingID, planeItemUUID, req.TargetType, req.TargetID).Scan(
			&existing.AssociationID, &existing.Revision, &existing.BindingID, &existing.PlaneItemUUID, &existing.TargetType, &existing.TargetID,
			&existing.TargetAgentID, &existing.TargetExecutionID, &existing.TargetEventID, &existing.TargetRecordID, &existing.TargetVersion, &existing.TargetGroupID, &existing.TargetRunID,
			&prevRationale, &prevTaskSummary, &existing.Author, &existing.State, &existing.CreatedAt, &existing.RemovedAt)

		if err == nil {
			if prevRationale != nil {
				existing.Rationale = *prevRationale
			}
			if prevTaskSummary != nil {
				existing.TaskSummary = *prevTaskSummary
			}
			if existing.State == "active" {
				return existing, nil // Idempotent return
			}
			// Reactivate removed association
			newRev := existing.Revision + 1
			_, err = tx.Exec(ctx, `UPDATE cairn.work_association SET state = 'active', removed_at = NULL, removed_by = NULL, revision = $1, rationale = $2, author = $3
				WHERE association_id = $4`, newRev, req.Rationale, s.channel.Principal, existing.AssociationID)
			if err != nil {
				return WorkAssociation{}, err
			}
			existing.Revision = newRev
			existing.State = "active"
			existing.Rationale = req.Rationale
			existing.Author = s.channel.Principal
			existing.RemovedAt = nil

			// Record history row (STORE-3)
			_, err = tx.Exec(ctx, `INSERT INTO cairn.work_association_history (association_id, revision, state, rationale, task_summary, author)
				VALUES ($1, $2, $3, $4, $5, $6)`, existing.AssociationID, existing.Revision, existing.State, existing.Rationale, existing.TaskSummary, existing.Author)
			if err != nil {
				return WorkAssociation{}, err
			}
			return existing, nil
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return WorkAssociation{}, err
		}

		// Insert fresh association
		assoc := WorkAssociation{
			AssociationID:     uuid.New(),
			Revision:          1,
			BindingID:         bindingID,
			PlaneItemUUID:     planeItemUUID,
			TargetType:        req.TargetType,
			TargetID:          req.TargetID,
			TargetAgentID:     targetAgentID,
			TargetExecutionID: targetExecID,
			TargetEventID:     targetEventID,
			TargetRecordID:    targetRecordID,
			TargetVersion:     targetVersion,
			TargetGroupID:     targetGroupID,
			TargetRunID:       targetRunID,
			Rationale:         req.Rationale,
			TaskSummary:       taskSummary,
			Author:            s.channel.Principal,
			State:             "active",
		}

		err = tx.QueryRow(ctx, `INSERT INTO cairn.work_association (
			association_id, revision, binding_id, plane_item_uuid, target_type, target_id,
			target_agent_id, target_execution_id, target_event_id, target_record_id, target_version, target_group_id, target_run_id,
			rationale, task_summary, author, state
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
		RETURNING created_at`,
			assoc.AssociationID, assoc.Revision, assoc.BindingID, assoc.PlaneItemUUID, assoc.TargetType, assoc.TargetID,
			assoc.TargetAgentID, assoc.TargetExecutionID, assoc.TargetEventID, assoc.TargetRecordID, assoc.TargetVersion, assoc.TargetGroupID, assoc.TargetRunID,
			assoc.Rationale, assoc.TaskSummary, assoc.Author, assoc.State).Scan(&assoc.CreatedAt)
		if err != nil {
			return WorkAssociation{}, err
		}

		// Record history row (STORE-3)
		_, err = tx.Exec(ctx, `INSERT INTO cairn.work_association_history (association_id, revision, state, rationale, task_summary, author)
			VALUES ($1, $2, $3, $4, $5, $6)`, assoc.AssociationID, assoc.Revision, assoc.State, assoc.Rationale, assoc.TaskSummary, assoc.Author)
		if err != nil {
			return WorkAssociation{}, err
		}

		return assoc, nil
	})
}

func (s *Store) UnassociateWork(ctx context.Context, req UnassociateWorkRequest) (WorkAssociation, error) {
	if err := s.requireOperator(); err != nil {
		return WorkAssociation{}, err
	}
	if req.Collection == "" {
		req.Collection = s.channel.Repo
		if req.Collection == "" {
			req.Collection = "default"
		}
	}
	if err := s.checkRepo(req.Collection); err != nil {
		return WorkAssociation{}, err
	}
	if strings.TrimSpace(req.ProjectSlug) == "" {
		return WorkAssociation{}, failure("INVALID_REQUEST", "project slug required")
	}

	// Canonicalize target_id if provided
	if req.TargetType == "session" || req.TargetType == "execution" {
		delim := ":"
		if strings.Contains(req.TargetID, "/") {
			delim = "/"
		}
		parts := strings.Split(req.TargetID, delim)
		if len(parts) == 2 {
			aUUID, err1 := uuid.Parse(parts[0])
			eUUID, err2 := uuid.Parse(parts[1])
			if err1 == nil && err2 == nil {
				req.TargetID = fmt.Sprintf("%s:%s", aUUID, eUUID)
			}
		}
	} else if req.TargetType == "result_record" || req.TargetType == "record" || req.TargetType == "result" {
		delim := ":"
		if strings.Contains(req.TargetID, "/") {
			delim = "/"
		}
		parts := strings.Split(req.TargetID, delim)
		if len(parts) == 2 {
			rUUID, err1 := uuid.Parse(parts[0])
			ver, err2 := strconv.Atoi(parts[1])
			if err1 == nil && err2 == nil {
				req.TargetID = fmt.Sprintf("%s:%d", rUUID, ver)
			}
		}
	}

	return mutate(ctx, s, "work-unassociate", req.RequestID, req, func(tx pgx.Tx) (WorkAssociation, error) {
		var bindingID uuid.UUID
		err := tx.QueryRow(ctx, `SELECT binding_id FROM cairn.work_project WHERE collection = $1 AND local_slug = $2`, req.Collection, req.ProjectSlug).Scan(&bindingID)
		if errors.Is(err, pgx.ErrNoRows) {
			return WorkAssociation{}, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", req.ProjectSlug, req.Collection))
		}
		if err != nil {
			return WorkAssociation{}, err
		}

		var assoc WorkAssociation
		var rationale, taskSummary *string
		if req.AssociationID != uuid.Nil {
			err = tx.QueryRow(ctx, `SELECT association_id, revision, binding_id, plane_item_uuid, target_type, target_id,
				rationale, task_summary, author, state, created_at
				FROM cairn.work_association
				WHERE binding_id = $1 AND association_id = $2
				FOR UPDATE`,
				bindingID, req.AssociationID).Scan(
				&assoc.AssociationID, &assoc.Revision, &assoc.BindingID, &assoc.PlaneItemUUID, &assoc.TargetType, &assoc.TargetID,
				&rationale, &taskSummary, &assoc.Author, &assoc.State, &assoc.CreatedAt)
		} else {
			var planeItemUUID uuid.UUID
			err = tx.QueryRow(ctx, `SELECT plane_item_uuid FROM cairn.work_item WHERE binding_id = $1 AND (item_id = $2 OR plane_item_uuid = $2)`, bindingID, req.ItemUUID).Scan(&planeItemUUID)
			if errors.Is(err, pgx.ErrNoRows) {
				return WorkAssociation{}, failure("NOT_FOUND", fmt.Sprintf("work item %s not found in project %s", req.ItemUUID, req.ProjectSlug))
			}
			if err != nil {
				return WorkAssociation{}, err
			}

			err = tx.QueryRow(ctx, `SELECT association_id, revision, binding_id, plane_item_uuid, target_type, target_id,
				rationale, task_summary, author, state, created_at
				FROM cairn.work_association
				WHERE binding_id = $1 AND plane_item_uuid = $2 AND target_type = $3 AND target_id = $4
				FOR UPDATE`,
				bindingID, planeItemUUID, req.TargetType, req.TargetID).Scan(
				&assoc.AssociationID, &assoc.Revision, &assoc.BindingID, &assoc.PlaneItemUUID, &assoc.TargetType, &assoc.TargetID,
				&rationale, &taskSummary, &assoc.Author, &assoc.State, &assoc.CreatedAt)
		}
		if errors.Is(err, pgx.ErrNoRows) {
			return WorkAssociation{}, failure("NOT_FOUND", "association not found")
		}
		if err != nil {
			return WorkAssociation{}, err
		}
		if rationale != nil {
			assoc.Rationale = *rationale
		}
		if taskSummary != nil {
			assoc.TaskSummary = *taskSummary
		}

		if assoc.State == "removed" {
			return assoc, nil
		}

		if req.ExpectedRevision != nil && assoc.Revision != *req.ExpectedRevision {
			return WorkAssociation{}, failure("VERSION_CONFLICT", fmt.Sprintf("expected revision %d, current is %d", *req.ExpectedRevision, assoc.Revision))
		}

		newRev := assoc.Revision + 1
		var removedAt time.Time
		err = tx.QueryRow(ctx, `UPDATE cairn.work_association SET state = 'removed', revision = $1, removed_at = clock_timestamp(), removed_by = $2
			WHERE association_id = $3
			RETURNING removed_at`, newRev, s.channel.Principal, assoc.AssociationID).Scan(&removedAt)
		if err != nil {
			return WorkAssociation{}, err
		}

		assoc.Revision = newRev
		assoc.State = "removed"
		assoc.RemovedAt = &removedAt
		assoc.RemovedBy = s.channel.Principal

		// Record history row (STORE-3): record remover as author of this transition
		_, err = tx.Exec(ctx, `INSERT INTO cairn.work_association_history (association_id, revision, state, rationale, task_summary, author)
			VALUES ($1, $2, $3, $4, $5, $6)`, assoc.AssociationID, assoc.Revision, assoc.State, assoc.Rationale, assoc.TaskSummary, s.channel.Principal)
		if err != nil {
			return WorkAssociation{}, err
		}

		return assoc, nil
	})
}
