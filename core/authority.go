package core

import (
	"context"
	"errors"
	"slices"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

var capabilities = []string{"grant", "revoke", "issue", "promote", "correct", "retract", "resolve", "redact"}

type Grant struct {
	ID           string     `json:"grant_id"`
	Principal    string     `json:"principal"`
	Repo         string     `json:"repo"`
	Capabilities []string   `json:"capabilities"`
	ParentID     string     `json:"parent_id,omitempty"`
	Depth        int        `json:"depth"`
	Version      int        `json:"version"`
	ExpiresAt    *time.Time `json:"expires_at,omitempty"`
	Live         bool       `json:"live"`
}
type BootstrapRequest struct {
	RequestID string `json:"request_id"`
	Reason    string `json:"reason"`
}
type GrantRequest struct {
	RequestID    string     `json:"request_id"`
	ParentID     string     `json:"parent_id"`
	Principal    string     `json:"principal"`
	Repo         string     `json:"repo"`
	Capabilities []string   `json:"capabilities"`
	ExpiresAt    *time.Time `json:"expires_at,omitempty"`
	Reason       string     `json:"reason"`
}
type RevokeGrantRequest struct {
	RequestID       string `json:"request_id"`
	GrantID         string `json:"grant_id"`
	AuthorityID     string `json:"authority_id"`
	ExpectedVersion int    `json:"expected_version"`
	Reason          string `json:"reason"`
}

func reasonValid(reason string) error {
	if n := len([]rune(strings.TrimSpace(reason))); n < 8 || n > 4000 {
		return failure("INVALID_REQUEST", "reason must contain 8-4000 characters")
	}
	return nil
}

func (s *Store) Bootstrap(ctx context.Context, req BootstrapRequest) (Grant, error) {
	if !s.channel.Operator {
		return Grant{}, failure("AUTHORITY_DENIED", "installation requires trusted operator channel")
	}
	if err := reasonValid(req.Reason); err != nil {
		return Grant{}, err
	}
	return privileged(ctx, s, "bootstrap", req.RequestID, req, func(tx pgx.Tx) (Grant, error) {
		if err := lock(ctx, tx, "root-installation"); err != nil {
			return Grant{}, err
		}
		var exists bool
		if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.authority_grant WHERE parent_id IS NULL)`).Scan(&exists); err != nil {
			return Grant{}, err
		}
		if exists {
			return Grant{}, failure("AUTHORITY_DENIED", "root already installed; retain the original bootstrap request for retry")
		}
		id := uuid.NewString()
		event, err := audit(ctx, tx, "bootstrap", id, 0, 1, []Grant{}, req.Reason)
		if err != nil {
			return Grant{}, err
		}
		_, err = tx.Exec(ctx, `INSERT INTO cairn.authority_grant(grant_id,principal,repo,capabilities,depth,event_id) VALUES($1,$2,'*',$3,0,$4)`, id, s.channel.Principal, capabilities, event)
		return Grant{ID: id, Principal: s.channel.Principal, Repo: "*", Capabilities: slices.Clone(capabilities), Depth: 0, Version: 1, Live: true}, err
	})
}

func grantChain(ctx context.Context, tx pgx.Tx, id string, locking bool) ([]Grant, error) {
	query := `WITH RECURSIVE chain AS (
 SELECT grant_id,parent_id FROM cairn.authority_grant WHERE grant_id=$1
 UNION ALL SELECT g.grant_id,g.parent_id FROM cairn.authority_grant g JOIN chain c ON g.grant_id=c.parent_id)
 SELECT g.grant_id::text,g.principal,g.repo,g.capabilities,COALESCE(g.parent_id::text,''),g.depth,g.version,g.expires_at,
 NOT g.revoked AND (g.expires_at IS NULL OR g.expires_at>transaction_timestamp())
 FROM cairn.authority_grant g JOIN chain c ON c.grant_id=g.grant_id ORDER BY g.depth,g.grant_id`
	if locking {
		query += " FOR SHARE OF g"
	}
	rows, err := tx.Query(ctx, query, id)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	chain := []Grant{}
	for rows.Next() {
		var g Grant
		if err = rows.Scan(&g.ID, &g.Principal, &g.Repo, &g.Capabilities, &g.ParentID, &g.Depth, &g.Version, &g.ExpiresAt, &g.Live); err != nil {
			return nil, err
		}
		chain = append(chain, g)
	}
	if err = rows.Err(); err != nil {
		return nil, err
	}
	if len(chain) == 0 || len(chain) > 3 {
		return nil, failure("AUTHORITY_DENIED", "invalid authority chain")
	}
	for i, g := range chain {
		if !g.Live || g.Depth != i || (i > 0 && g.ParentID != chain[i-1].ID) {
			return nil, failure("AUTHORITY_DENIED", "authority chain is revoked, expired or invalid")
		}
	}
	return chain, nil
}

func (s *Store) authorize(ctx context.Context, tx pgx.Tx, id, capability, repo string) ([]Grant, error) {
	if err := validID(id); err != nil {
		return nil, err
	}
	if err := s.checkRepo(repo); err != nil {
		return nil, err
	}
	chain, err := grantChain(ctx, tx, id, true)
	if err != nil {
		return nil, err
	}
	leaf := chain[len(chain)-1]
	if leaf.Principal != s.channel.Principal || !slices.Contains(leaf.Capabilities, capability) || (leaf.Repo != "*" && leaf.Repo != repo) {
		return nil, failure("AUTHORITY_DENIED", "grant does not authorize this actor, operation and repository")
	}
	return chain, nil
}

func (s *Store) Grant(ctx context.Context, req GrantRequest) (Grant, error) {
	// PostgreSQL timestamps have microsecond precision. Normalize before both
	// request hashing and containment checks so equal expiries remain equal.
	if req.ExpiresAt != nil {
		normalized := req.ExpiresAt.UTC().Truncate(time.Microsecond)
		req.ExpiresAt = &normalized
	}
	if err := reasonValid(req.Reason); err != nil {
		return Grant{}, err
	}
	if strings.TrimSpace(req.Principal) == "" || len(req.Principal) > 256 || strings.TrimSpace(req.Repo) == "" || len(req.Repo) > 256 || len(req.Capabilities) == 0 || len(req.Capabilities) > len(capabilities) {
		return Grant{}, failure("INVALID_REQUEST", "bounded principal, repository and capabilities required")
	}
	for _, cap := range req.Capabilities {
		if !slices.Contains(capabilities, cap) {
			return Grant{}, failure("INVALID_REQUEST", "unknown grant capability")
		}
	}
	return privileged(ctx, s, "grant", req.RequestID, req, func(tx pgx.Tx) (Grant, error) {
		chain, err := s.authorize(ctx, tx, req.ParentID, "grant", req.Repo)
		if err != nil {
			return Grant{}, err
		}
		parent := chain[len(chain)-1]
		if parent.Depth >= 2 {
			return Grant{}, failure("AUTHORITY_DENIED", "delegation depth exceeds two")
		}
		for _, cap := range req.Capabilities {
			if !slices.Contains(parent.Capabilities, cap) {
				return Grant{}, failure("AUTHORITY_DENIED", "child capability exceeds parent")
			}
		}
		if parent.ExpiresAt != nil && (req.ExpiresAt == nil || req.ExpiresAt.After(*parent.ExpiresAt)) {
			return Grant{}, failure("AUTHORITY_DENIED", "child validity exceeds parent")
		}
		var now time.Time
		if err = tx.QueryRow(ctx, `SELECT transaction_timestamp()`).Scan(&now); err != nil {
			return Grant{}, err
		}
		if req.ExpiresAt != nil && !req.ExpiresAt.After(now) {
			return Grant{}, failure("INVALID_REQUEST", "grant expiry must be in the future")
		}
		id := uuid.NewString()
		event, err := audit(ctx, tx, "grant", id, 0, 1, chain, req.Reason)
		if err != nil {
			return Grant{}, err
		}
		_, err = tx.Exec(ctx, `INSERT INTO cairn.authority_grant(grant_id,principal,repo,capabilities,parent_id,depth,expires_at,event_id) VALUES($1,$2,$3,$4,$5,$6,$7,$8)`, id, req.Principal, req.Repo, req.Capabilities, req.ParentID, parent.Depth+1, req.ExpiresAt, event)
		return Grant{ID: id, Principal: req.Principal, Repo: req.Repo, Capabilities: req.Capabilities, ParentID: req.ParentID, Depth: parent.Depth + 1, Version: 1, ExpiresAt: req.ExpiresAt, Live: true}, err
	})
}

func (s *Store) RevokeGrant(ctx context.Context, req RevokeGrantRequest) (Grant, error) {
	if err := reasonValid(req.Reason); err != nil {
		return Grant{}, err
	}
	if err := validID(req.GrantID); err != nil {
		return Grant{}, err
	}
	return privileged(ctx, s, "revoke-grant", req.RequestID, req, func(tx pgx.Tx) (Grant, error) {
		var repo string
		if err := tx.QueryRow(ctx, `SELECT repo FROM cairn.authority_grant WHERE grant_id=$1`, req.GrantID).Scan(&repo); err != nil {
			return Grant{}, err
		}
		chain, err := s.authorize(ctx, tx, req.AuthorityID, "revoke", repo)
		if err != nil {
			return Grant{}, err
		}
		targetChain, err := grantChain(ctx, tx, req.GrantID, true)
		if err != nil {
			return Grant{}, err
		}
		target := targetChain[len(targetChain)-1]
		if target.Version != req.ExpectedVersion {
			return Grant{}, failure("VERSION_CONFLICT", "grant version changed")
		}
		if target.ParentID == "" {
			return Grant{}, failure("AUTHORITY_DENIED", "root revocation requires a separate recovery procedure")
		}
		allowed := false
		for _, ancestor := range targetChain[:len(targetChain)-1] {
			if ancestor.ID == req.AuthorityID {
				allowed = true
			}
		}
		if !allowed {
			return Grant{}, failure("AUTHORITY_DENIED", "revocation authority must be an ancestor")
		}
		event, err := audit(ctx, tx, "revoke_grant", req.GrantID, target.Version, target.Version+1, chain, req.Reason)
		if err != nil {
			return Grant{}, err
		}
		_, err = tx.Exec(ctx, `UPDATE cairn.authority_grant SET revoked=true,version=version+1,event_id=$2 WHERE grant_id=$1`, req.GrantID, event)
		target.Live = false
		target.Version++
		return target, err
	})
}

func audit(ctx context.Context, tx pgx.Tx, kind, subject string, previous, next int, basis []Grant, reason string) (string, error) {
	id := uuid.NewString()
	_, err := tx.Exec(ctx, `INSERT INTO cairn.authority_event(event_id,event_type,subject_id,previous_version,resulting_version,basis,reason) VALUES($1,$2,$3,$4,$5,$6,$7)`, id, kind, subject, previous, next, basis, strings.TrimSpace(reason))
	return id, err
}

func (s *Store) Grants(ctx context.Context) ([]Grant, error) {
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT grant_id::text FROM cairn.authority_grant WHERE principal=$1 ORDER BY grant_id`, s.channel.Principal)
	if err != nil {
		return nil, err
	}
	ids, err := pgx.CollectRows(rows, pgx.RowTo[string])
	if err != nil {
		return nil, err
	}
	result := []Grant{}
	for _, id := range ids {
		chain, err := grantChain(ctx, tx, id, false)
		if Code(err) == "AUTHORITY_DENIED" {
			continue
		}
		if err != nil {
			return nil, err
		}
		result = append(result, chain[len(chain)-1])
	}
	return result, tx.Commit(ctx)
}

func lockRecord(ctx context.Context, tx pgx.Tx, id string, expected int) (Record, error) {
	var version int
	err := tx.QueryRow(ctx, `SELECT current_version FROM cairn.memory_record WHERE record_id=$1 FOR UPDATE`, id).Scan(&version)
	if errors.Is(err, pgx.ErrNoRows) {
		return Record{}, failure("NOT_FOUND", "record not found")
	}
	if err != nil {
		return Record{}, err
	}
	if version != expected {
		return Record{}, failure("VERSION_CONFLICT", "record version changed")
	}
	return readRecord(ctx, tx, id)
}
