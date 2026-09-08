package core

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

type AuthorizeScopeRequest struct {
	RequestID       string         `json:"request_id"`
	RecordID        string         `json:"record_id"`
	ExpectedVersion int            `json:"expected_version"`
	Scope           Scope          `json:"scope"`
	Pins            *Applicability `json:"pins"`
	GrantID         string         `json:"grant_id"`
	PreviewID       string         `json:"preview_id"`
	Reason          string         `json:"reason"`
}
type ScopeAuthorization struct {
	ScopeGrantLive  bool           `json:"scope_grant_live"`
	RecordID        string         `json:"record_id"`
	PreviousVersion int            `json:"previous_version"`
	Version         int            `json:"version"`
	Scope           Scope          `json:"scope"`
	Pins            *Applicability `json:"pins"`
	GrantID         string         `json:"grant_id"`
	EventID         string         `json:"event_id"`
	Actor           string         `json:"actor"`
	AuthorizedAt    time.Time      `json:"authorized_at"`
}

func (s *Store) AuthorizeScope(ctx context.Context, req AuthorizeScopeRequest) (ScopeAuthorization, error) {
	if err := validID(req.RecordID); err != nil {
		return ScopeAuthorization{}, err
	}
	if err := reasonValid(req.Reason); err != nil {
		return ScopeAuthorization{}, err
	}
	if err := req.Scope.validate(); err != nil {
		return ScopeAuthorization{}, err
	}
	if req.ExpectedVersion < 1 || req.Pins == nil {
		return ScopeAuthorization{}, failure("INVALID_REQUEST", "positive expected version and explicit pins required; use {} for unconstrained applicability")
	}
	if err := req.Pins.validate(); err != nil {
		return ScopeAuthorization{}, err
	}
	var scope Scope
	result, err := privileged(ctx, s, "authorize-scope", req.RequestID, req, func(tx pgx.Tx) (ScopeAuthorization, error) {
		old, err := readRecord(ctx, tx, req.RecordID)
		if err != nil {
			return ScopeAuthorization{}, err
		}
		scope = old.Scope
		chain, err := s.authorize(ctx, tx, req.GrantID, "issue", scope.Repo)
		if err != nil {
			return ScopeAuthorization{}, err
		}
		var qualification string
		var policy IssueRequest
		if old.Class != "A" {
			if err = tx.QueryRow(ctx, `SELECT grant_id::text,mandatory,requires_runtime,policy_key FROM cairn.record_authority WHERE record_id=$1 AND version=$2`, old.RecordID, old.Version).Scan(&qualification, &policy.Mandatory, &policy.RequiresRuntime, &policy.PolicyKey); err != nil {
				return ScopeAuthorization{}, err
			}
			if _, err = grantChain(ctx, tx, qualification, true); err != nil {
				return ScopeAuthorization{}, err
			}
		}
		old, err = lockRecord(ctx, tx, old.RecordID, req.ExpectedVersion)
		if err != nil {
			return ScopeAuthorization{}, err
		}
		if old.Lifecycle != "active" {
			return ScopeAuthorization{}, failure("VERSION_CONFLICT", "only active records can receive scope authorization")
		}
		if scope.Repo != req.Scope.Repo || (req.Scope.TaskID != "*" && req.Scope.TaskID != scope.TaskID) || (req.Scope.RunID != "*" && req.Scope.RunID != scope.RunID) || !applicabilityContains(req.Pins, old.Pins) {
			return ScopeAuthorization{}, failure("AUTHORITY_DENIED", "target must contain the existing applicability within the same repository")
		}
		var previous bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.scope_authorization WHERE record_id=$1)`, old.RecordID).Scan(&previous); err != nil {
			return ScopeAuthorization{}, err
		}
		if !previous && scope == req.Scope && applicabilityContains(old.Pins, req.Pins) {
			return ScopeAuthorization{}, failure("INVALID_REQUEST", "initial scope authorization must expand applicability")
		}
		// A new scope decision can replace withdrawn scope authority, but cannot
		// renew revoked claim/instruction qualification or missing supporting data.
		purpose := "planning"
		if old.Class == "A" {
			purpose = "context"
		}
		selection, reason, err := eligibleWithoutScope(ctx, tx, old, purpose)
		if err != nil {
			return ScopeAuthorization{}, err
		}
		if reason != "" {
			return ScopeAuthorization{}, failure(reason, "source qualification does not permit scope authorization")
		}
		evidenceIDs := make([]string, 0, len(selection.Evidence))
		for _, e := range selection.Evidence {
			if e.State != "resolvable" {
				return ScopeAuthorization{}, failure("EVIDENCE_UNAVAILABLE", "degraded evidence cannot support broader applicability")
			}
			evidenceIDs = append(evidenceIDs, e.ID)
		}
		if err = s.checkRetractionPreview(ctx, tx, RetractRequest{RecordID: old.RecordID, PreviewID: req.PreviewID}); err != nil {
			return ScopeAuthorization{}, err
		}
		draft := old.Draft
		draft.Scope = req.Scope
		draft.Pins = req.Pins
		next, err := advanceRecord(ctx, tx, old, draft, old.Class, "active")
		if err != nil {
			return ScopeAuthorization{}, err
		}
		if old.Class == "B" {
			if err = linkEvidence(ctx, tx, next, evidenceIDs); err != nil {
				return ScopeAuthorization{}, err
			}
		}
		event, err := audit(ctx, tx, "authorize_scope", old.RecordID, old.Version, next.Version, chain, req.Reason)
		if err != nil {
			return ScopeAuthorization{}, err
		}
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.scope_authorization(record_id,version,previous_version,event_id,grant_id) VALUES($1,$2,$3,$4,$5)`, old.RecordID, next.Version, old.Version, event, req.GrantID); err != nil {
			return ScopeAuthorization{}, err
		}
		if old.Class != "A" {
			if _, err = tx.Exec(ctx, `INSERT INTO cairn.record_authority(record_id,version,event_id,grant_id,mandatory,requires_runtime,policy_key) VALUES($1,$2,$3,$4,$5,$6,$7)`, next.RecordID, next.Version, event, qualification, policy.Mandatory, policy.RequiresRuntime, policy.PolicyKey); err != nil {
				return ScopeAuthorization{}, err
			}
		}
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.record_correction(record_id,old_version,new_version,event_id) VALUES($1,$2,$3,$4)`, old.RecordID, old.Version, next.Version, event); err != nil {
			return ScopeAuthorization{}, err
		}
		if old.Class == "C" {
			if err = openPolicyConflicts(ctx, tx, next, policy.PolicyKey); err != nil {
				return ScopeAuthorization{}, err
			}
		}
		return readScopeAuthorization(ctx, tx, old.RecordID)
	})
	if durablePolicyRefusal(err) {
		err = s.retainRefusal(ctx, req, Refusal{RequestID: req.RequestID, Operation: "authorize-scope", Scope: scope, Considered: []RecordVersionRef{{req.RecordID, req.ExpectedVersion}}, TraceComplete: false}, err)
	}
	return result, err
}

// Scope authority applies to every subsequent version in the unchanged range.
// The most recent explicit C decision replaces only the scope grant.
func scopeAuthority(ctx context.Context, tx pgx.Tx, id string) ([]Grant, error) {
	var grant string
	err := tx.QueryRow(ctx, `SELECT grant_id::text FROM cairn.scope_authorization WHERE record_id=$1 ORDER BY version DESC LIMIT 1`, id).Scan(&grant)
	if err == pgx.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return grantChain(ctx, tx, grant, false)
}

func (s *Store) ScopeAuthorization(ctx context.Context, id string) (ScopeAuthorization, error) {
	if err := validID(id); err != nil {
		return ScopeAuthorization{}, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return ScopeAuthorization{}, err
	}
	defer tx.Rollback(ctx)
	record, err := readRecord(ctx, tx, id)
	if err != nil {
		return ScopeAuthorization{}, err
	}
	if err = s.checkRepo(record.Scope.Repo); err != nil {
		return ScopeAuthorization{}, err
	}
	result, err := readScopeAuthorization(ctx, tx, id)
	if err != nil {
		return result, err
	}
	return result, tx.Commit(ctx)
}
func readScopeAuthorization(ctx context.Context, tx pgx.Tx, id string) (ScopeAuthorization, error) {
	var result ScopeAuthorization
	err := tx.QueryRow(ctx, `SELECT s.record_id::text,s.previous_version,s.version,v.repo,v.task_id,v.run_id,p.pins,s.grant_id::text,s.event_id::text,e.actor,e.occurred_at FROM cairn.scope_authorization s JOIN cairn.record_version v ON v.record_id=s.record_id AND v.version=s.version LEFT JOIN cairn.record_applicability p ON p.record_id=s.record_id AND p.version=s.version JOIN cairn.authority_event e USING(event_id) WHERE s.record_id=$1 ORDER BY s.version DESC LIMIT 1`, id).Scan(&result.RecordID, &result.PreviousVersion, &result.Version, &result.Scope.Repo, &result.Scope.TaskID, &result.Scope.RunID, &result.Pins, &result.GrantID, &result.EventID, &result.Actor, &result.AuthorizedAt)
	if err == pgx.ErrNoRows {
		return result, failure("NOT_FOUND", "record has no scope authorization")
	}
	if err != nil {
		return result, err
	}
	_, err = grantChain(ctx, tx, result.GrantID, false)
	if Code(err) == "AUTHORITY_DENIED" {
		return result, nil
	}
	if err != nil {
		return result, err
	}
	result.ScopeGrantLive = true
	return result, nil
}
