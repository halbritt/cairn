package core

import (
	"context"
	"slices"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type DisputeRequest struct {
	RequestID string   `json:"request_id"`
	RecordIDs []string `json:"record_ids"`
	Reason    string   `json:"reason"`
}
type ResolveRequest struct {
	RequestID       string `json:"request_id"`
	ConflictID      string `json:"conflict_id"`
	ExpectedVersion int    `json:"expected_version"`
	GrantID         string `json:"grant_id"`
	Reason          string `json:"reason"`
}
type Conflict struct {
	ID        string   `json:"conflict_id"`
	Repo      string   `json:"repo"`
	Version   int      `json:"version"`
	RecordIDs []string `json:"record_ids"`
	Resolved  bool     `json:"resolved"`
}

func (s *Store) Dispute(ctx context.Context, req DisputeRequest) (Conflict, error) {
	if err := reasonValid(req.Reason); err != nil {
		return Conflict{}, err
	}
	if len(req.RecordIDs) < 2 || len(req.RecordIDs) > 16 {
		return Conflict{}, failure("INVALID_REQUEST", "dispute requires 2-16 distinct records")
	}
	ids := slices.Clone(req.RecordIDs)
	slices.Sort(ids)
	if len(slices.Compact(ids)) != len(req.RecordIDs) {
		return Conflict{}, failure("INVALID_REQUEST", "duplicate conflict member")
	}
	for _, id := range req.RecordIDs {
		if err := validID(id); err != nil {
			return Conflict{}, err
		}
	}
	return privileged(ctx, s, "dispute", req.RequestID, req, func(tx pgx.Tx) (Conflict, error) {
		members := []Record{}
		repo := ""
		for _, id := range ids {
			var version int
			if err := tx.QueryRow(ctx, `SELECT current_version FROM cairn.memory_record WHERE record_id=$1 FOR UPDATE`, id).Scan(&version); err != nil {
				return Conflict{}, err
			}
			r, err := readRecord(ctx, tx, id)
			if err != nil {
				return Conflict{}, err
			}
			if err = s.checkRepo(r.Scope.Repo); err != nil {
				return Conflict{}, err
			}
			if r.Lifecycle != "active" {
				return Conflict{}, failure("INVALID_REQUEST", "conflict member is inactive")
			}
			if repo != "" && repo != r.Scope.Repo {
				return Conflict{}, failure("INVALID_REQUEST", "conflict members must share a repository")
			}
			repo = r.Scope.Repo
			members = append(members, r)
		}
		id := uuid.NewString()
		if _, err := tx.Exec(ctx, `INSERT INTO cairn.conflict_group(conflict_id,repo,reason) VALUES($1,$2,$3)`, id, repo, req.Reason); err != nil {
			return Conflict{}, err
		}
		for _, r := range members {
			if _, err := tx.Exec(ctx, `INSERT INTO cairn.conflict_member(conflict_id,record_id,version) VALUES($1,$2,$3)`, id, r.RecordID, r.Version); err != nil {
				return Conflict{}, err
			}
		}
		return Conflict{id, repo, 1, ids, false}, nil
	})
}
func (s *Store) Resolve(ctx context.Context, req ResolveRequest) (Conflict, error) {
	if err := reasonValid(req.Reason); err != nil {
		return Conflict{}, err
	}
	if err := validID(req.ConflictID); err != nil {
		return Conflict{}, err
	}
	return privileged(ctx, s, "resolve", req.RequestID, req, func(tx pgx.Tx) (Conflict, error) {
		var c Conflict
		c.ID = req.ConflictID
		if err := tx.QueryRow(ctx, `SELECT repo FROM cairn.conflict_group WHERE conflict_id=$1`, req.ConflictID).Scan(&c.Repo); err != nil {
			return c, err
		}
		chain, err := s.authorize(ctx, tx, req.GrantID, "resolve", c.Repo)
		if err != nil {
			return c, err
		}
		if err = tx.QueryRow(ctx, `SELECT version,resolved_event IS NOT NULL FROM cairn.conflict_group WHERE conflict_id=$1 FOR UPDATE`, req.ConflictID).Scan(&c.Version, &c.Resolved); err != nil {
			return c, err
		}
		if c.Version != req.ExpectedVersion || c.Resolved {
			return c, failure("VERSION_CONFLICT", "conflict changed or already resolved")
		}
		event, err := audit(ctx, tx, "resolve", c.ID, c.Version, c.Version+1, chain, req.Reason)
		if err != nil {
			return c, err
		}
		_, err = tx.Exec(ctx, `UPDATE cairn.conflict_group SET version=version+1,resolved_event=$2 WHERE conflict_id=$1`, c.ID, event)
		c.Version++
		c.Resolved = true
		return c, err
	})
}
