package core

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

type ConflictsRequest struct {
	Repo            string `json:"repo"`
	RecordID        string `json:"record_id,omitempty"`
	IncludeResolved bool   `json:"include_resolved"`
	Limit           int    `json:"limit"`
	Offset          int    `json:"offset"`
}

type ConflictSummary struct {
	ID          string    `json:"conflict_id"`
	Repo        string    `json:"repo"`
	Version     int       `json:"version"`
	OpenedBy    string    `json:"opened_by"`
	OpenedAt    time.Time `json:"opened_at"`
	Reason      string    `json:"reason"`
	Resolved    bool      `json:"resolved"`
	MemberCount int       `json:"member_count"`
}

type ConflictMember struct {
	RecordID           string  `json:"record_id"`
	Version            int     `json:"version"`
	VersionClass       string  `json:"version_class"`
	Scope              Scope   `json:"scope"`
	Body               *string `json:"body"`
	PayloadAvailable   bool    `json:"payload_available"`
	ObservedWriter     string  `json:"observed_writer"`
	AttributedProducer string  `json:"attributed_producer"`
	Witness            string  `json:"witness"`
	CurrentVersion     int     `json:"current_version"`
	CurrentClass       string  `json:"current_class"`
	CurrentLifecycle   string  `json:"current_lifecycle"`
}

type ConflictResolution struct {
	EventID    string    `json:"event_id"`
	Actor      string    `json:"actor"`
	Reason     string    `json:"reason"`
	OccurredAt time.Time `json:"occurred_at"`
}

type ConflictDetail struct {
	ConflictSummary
	Members    []ConflictMember    `json:"members"`
	Resolution *ConflictResolution `json:"resolution"`
}

type ConflictPage struct {
	Conflicts  []ConflictSummary `json:"conflicts"`
	More       bool              `json:"more"`
	NextOffset int               `json:"next_offset"`
}

func (s *Store) Conflict(ctx context.Context, id string) (ConflictDetail, error) {
	if err := validID(id); err != nil {
		return ConflictDetail{}, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return ConflictDetail{}, err
	}
	defer tx.Rollback(ctx)
	var detail ConflictDetail
	var resolutionID string
	err = tx.QueryRow(ctx, `SELECT g.conflict_id::text,g.repo,g.version,g.opened_by,g.opened_at,g.reason,g.resolved_event IS NOT NULL,(SELECT count(*) FROM cairn.conflict_member WHERE conflict_id=g.conflict_id),COALESCE(g.resolved_event::text,'') FROM cairn.conflict_group g WHERE g.conflict_id=$1`, id).Scan(&detail.ID, &detail.Repo, &detail.Version, &detail.OpenedBy, &detail.OpenedAt, &detail.Reason, &detail.Resolved, &detail.MemberCount, &resolutionID)
	if err == pgx.ErrNoRows {
		return ConflictDetail{}, failure("NOT_FOUND", "conflict not found")
	}
	if err != nil {
		return ConflictDetail{}, err
	}
	if err = s.checkRepo(detail.Repo); err != nil {
		return ConflictDetail{}, err
	}
	if detail.MemberCount > 16 {
		return ConflictDetail{}, failure("BUDGET_REFUSED", "conflict exceeds 16 retained members")
	}
	rows, err := tx.Query(ctx, `SELECT v.record_id::text,v.version,v.version_class,v.repo,v.task_id,v.run_id,
 CASE WHEN v.payload_deleted_by IS NULL AND m.lifecycle<>'tombstoned' THEN v.body ELSE NULL END,
 v.payload_deleted_by IS NULL AND m.lifecycle<>'tombstoned',v.observed_writer,v.attributed_producer,v.witness,m.current_version,m.class,m.lifecycle
 FROM cairn.conflict_member c JOIN cairn.record_version v ON v.record_id=c.record_id AND v.version=c.version
 JOIN cairn.memory_record m ON m.record_id=c.record_id
 WHERE c.conflict_id=$1 ORDER BY c.record_id,c.version`, id)
	if err != nil {
		return ConflictDetail{}, err
	}
	detail.Members = []ConflictMember{}
	for rows.Next() {
		var member ConflictMember
		if err = rows.Scan(&member.RecordID, &member.Version, &member.VersionClass, &member.Scope.Repo, &member.Scope.TaskID, &member.Scope.RunID, &member.Body, &member.PayloadAvailable, &member.ObservedWriter, &member.AttributedProducer, &member.Witness, &member.CurrentVersion, &member.CurrentClass, &member.CurrentLifecycle); err != nil {
			rows.Close()
			return ConflictDetail{}, err
		}
		detail.Members = append(detail.Members, member)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return ConflictDetail{}, err
	}
	if resolutionID != "" {
		detail.Resolution = &ConflictResolution{EventID: resolutionID}
		if err = tx.QueryRow(ctx, `SELECT actor,reason,occurred_at FROM cairn.authority_event WHERE event_id=$1`, resolutionID).Scan(&detail.Resolution.Actor, &detail.Resolution.Reason, &detail.Resolution.OccurredAt); err != nil {
			return ConflictDetail{}, err
		}
	}
	return detail, tx.Commit(ctx)
}
func (s *Store) Conflicts(ctx context.Context, req ConflictsRequest) (ConflictPage, error) {
	if err := s.checkRepo(req.Repo); err != nil {
		return ConflictPage{}, err
	}
	if req.Repo == "" || req.Limit < 1 || req.Limit > 200 || req.Offset < 0 {
		return ConflictPage{}, failure("INVALID_REQUEST", "repo, limit 1-200 and nonnegative offset required")
	}
	if req.RecordID != "" {
		if err := validID(req.RecordID); err != nil {
			return ConflictPage{}, err
		}
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return ConflictPage{}, err
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT g.conflict_id::text,g.repo,g.version,g.opened_by,g.opened_at,g.reason,g.resolved_event IS NOT NULL,(SELECT count(*) FROM cairn.conflict_member WHERE conflict_id=g.conflict_id)
 FROM cairn.conflict_group g WHERE g.repo=$1 AND ($2 OR g.resolved_event IS NULL)
 AND ($3='' OR EXISTS(SELECT 1 FROM cairn.conflict_member WHERE conflict_id=g.conflict_id AND record_id=NULLIF($3,'')::uuid))
 ORDER BY g.opened_at,g.conflict_id LIMIT $4 OFFSET $5`, req.Repo, req.IncludeResolved, req.RecordID, req.Limit+1, req.Offset)
	if err != nil {
		return ConflictPage{}, err
	}
	page := ConflictPage{Conflicts: []ConflictSummary{}}
	for rows.Next() {
		var summary ConflictSummary
		if err = rows.Scan(&summary.ID, &summary.Repo, &summary.Version, &summary.OpenedBy, &summary.OpenedAt, &summary.Reason, &summary.Resolved, &summary.MemberCount); err != nil {
			rows.Close()
			return ConflictPage{}, err
		}
		page.Conflicts = append(page.Conflicts, summary)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return ConflictPage{}, err
	}
	page.More = len(page.Conflicts) > req.Limit
	if page.More {
		page.Conflicts = page.Conflicts[:req.Limit]
	}
	page.NextOffset = req.Offset + len(page.Conflicts)
	return page, tx.Commit(ctx)
}
