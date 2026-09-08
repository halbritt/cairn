package core

import (
	"context"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// PolicyRules can narrow the accepted optional-memory ceiling. Hard authority,
// destination and mandatory-policy checks are not configurable through it.
type PolicyRules struct {
	OptionalPercent   int `json:"optional_percent"`
	OptionalMaxTokens int `json:"optional_max_tokens"`
}

func (r PolicyRules) validate() error {
	if r.OptionalPercent < 0 || r.OptionalPercent > 10 || r.OptionalMaxTokens < 0 || r.OptionalMaxTokens > 6000 {
		return failure("INVALID_REQUEST", "optional policy budget must remain within 0-10 percent and 0-6000 tokens")
	}
	return nil
}

type RevisePolicyRequest struct {
	RequestID          string       `json:"request_id"`
	Repo               string       `json:"repo"`
	ExpectedRevisionID string       `json:"expected_revision_id"`
	RestoreRevisionID  string       `json:"restore_revision_id,omitempty"`
	Rules              *PolicyRules `json:"rules,omitempty"`
	GrantID            string       `json:"grant_id"`
	Reason             string       `json:"reason"`
}

type PolicyRevision struct {
	RevisionID         string      `json:"revision_id"`
	Repo               string      `json:"repo"`
	Version            int         `json:"version"`
	Engine             string      `json:"engine"`
	PreviousRevisionID string      `json:"previous_revision_id,omitempty"`
	RestoresRevisionID string      `json:"restores_revision_id,omitempty"`
	Rules              PolicyRules `json:"rules"`
	GrantID            string      `json:"grant_id"`
	EventID            string      `json:"event_id"`
	Actor              string      `json:"actor"`
	WrittenAt          time.Time   `json:"written_at"`
	AuthorityLive      bool        `json:"authority_live"`
}

// PolicySnapshot is deliverable structured configuration and frozen authority.
// Free-form reasons and administrative history are not sent to the destination.
type PolicySnapshot struct {
	RevisionID string      `json:"revision_id"`
	Version    int         `json:"version"`
	Rules      PolicyRules `json:"rules"`
	Authority  []Grant     `json:"authority"`
}

type EffectivePolicy struct {
	Engine   string          `json:"engine"`
	Rules    PolicyRules     `json:"rules"`
	Revision *PolicyRevision `json:"revision,omitempty"`
}

func (s *Store) RevisePolicy(ctx context.Context, req RevisePolicyRequest) (PolicyRevision, error) {
	if err := (Scope{req.Repo, "*", "*"}).validate(); err != nil {
		return PolicyRevision{}, err
	}
	if err := reasonValid(req.Reason); err != nil {
		return PolicyRevision{}, err
	}
	if (req.Rules == nil) == (req.RestoreRevisionID == "") {
		return PolicyRevision{}, failure("INVALID_REQUEST", "supply either explicit rules or a revision to restore")
	}
	for _, id := range []string{req.ExpectedRevisionID, req.RestoreRevisionID} {
		if id != "" {
			if err := validID(id); err != nil {
				return PolicyRevision{}, err
			}
		}
	}
	if req.Rules != nil {
		if err := req.Rules.validate(); err != nil {
			return PolicyRevision{}, err
		}
	}
	return privileged(ctx, s, "policy-revise", req.RequestID, req, func(tx pgx.Tx) (PolicyRevision, error) {
		chain, err := s.authorize(ctx, tx, req.GrantID, "issue", req.Repo)
		if err != nil {
			return PolicyRevision{}, err
		}
		if _, err = tx.Exec(ctx, `UPDATE cairn.policy_generation SET generation=generation+1 WHERE singleton`); err != nil {
			return PolicyRevision{}, err
		}
		previous, err := currentPolicy(ctx, tx, req.Repo)
		if err != nil {
			return PolicyRevision{}, err
		}
		version, previousID := 1, ""
		if previous != nil {
			version, previousID = previous.Version+1, previous.RevisionID
		}
		if previousID != req.ExpectedRevisionID {
			return PolicyRevision{}, failure("VERSION_CONFLICT", "effective policy changed; inspect the current revision")
		}
		var rules PolicyRules
		if req.Rules != nil {
			rules = *req.Rules
		} else {
			restored, err := readPolicyRevision(ctx, tx, req.RestoreRevisionID)
			if err != nil {
				return PolicyRevision{}, err
			}
			if restored.Repo != req.Repo {
				return PolicyRevision{}, failure("AUTHORITY_DENIED", "rollback cannot import policy from another repository")
			}
			if restored.Engine != "local-loop/2" {
				return PolicyRevision{}, failure("POLICY_UNENFORCEABLE", "rollback policy engine is unsupported")
			}
			rules = restored.Rules
		}
		id := uuid.NewString()
		event, err := audit(ctx, tx, "policy_revise", id, version-1, version, chain, req.Reason)
		if err != nil {
			return PolicyRevision{}, err
		}
		_, err = tx.Exec(ctx, `INSERT INTO cairn.policy_revision(revision_id,repo,version,previous_revision_id,restores_revision_id,rules,grant_id,event_id) VALUES($1,$2,$3,NULLIF($4,'')::uuid,NULLIF($5,'')::uuid,$6,$7,$8)`, id, req.Repo, version, previousID, req.RestoreRevisionID, rules, req.GrantID, event)
		if err != nil {
			return PolicyRevision{}, err
		}
		result, err := readPolicyRevision(ctx, tx, id)
		result.AuthorityLive = true
		return result, err
	})
}

func readPolicyRevision(ctx context.Context, tx pgx.Tx, id string) (PolicyRevision, error) {
	var p PolicyRevision
	err := tx.QueryRow(ctx, `SELECT p.revision_id::text,p.repo,p.version,p.engine,COALESCE(p.previous_revision_id::text,''),COALESCE(p.restores_revision_id::text,''),p.rules,p.grant_id::text,p.event_id::text,e.actor,e.occurred_at FROM cairn.policy_revision p JOIN cairn.authority_event e USING(event_id) WHERE p.revision_id=$1`, id).Scan(&p.RevisionID, &p.Repo, &p.Version, &p.Engine, &p.PreviousRevisionID, &p.RestoresRevisionID, &p.Rules, &p.GrantID, &p.EventID, &p.Actor, &p.WrittenAt)
	if err == pgx.ErrNoRows {
		return p, failure("NOT_FOUND", "policy revision not found")
	}
	return p, err
}

func currentPolicy(ctx context.Context, tx pgx.Tx, repo string) (*PolicyRevision, error) {
	var id string
	err := tx.QueryRow(ctx, `SELECT revision_id::text FROM cairn.policy_revision WHERE repo=$1 ORDER BY version DESC LIMIT 1`, repo).Scan(&id)
	if err == pgx.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	p, err := readPolicyRevision(ctx, tx, id)
	return &p, err
}

func policyAuthority(ctx context.Context, tx pgx.Tx, p *PolicyRevision, locking bool) ([]Grant, error) {
	chain, err := grantChain(ctx, tx, p.GrantID, locking)
	if Code(err) == "AUTHORITY_DENIED" {
		return nil, failure("POLICY_UNENFORCEABLE", "effective policy authority is revoked or expired; authorize a new revision")
	}
	return chain, err
}

func policySnapshot(ctx context.Context, tx pgx.Tx, repo string) (*PolicySnapshot, error) {
	// A shared row lock gives repeatable-read callers a serialization failure if
	// any revision committed after their snapshot, including a first revision.
	var generation int64
	if err := tx.QueryRow(ctx, `SELECT generation FROM cairn.policy_generation WHERE singleton FOR SHARE`).Scan(&generation); err != nil {
		return nil, err
	}
	p, err := currentPolicy(ctx, tx, repo)
	if err != nil || p == nil {
		return nil, err
	}
	if p.Engine != "local-loop/2" || p.Rules.validate() != nil {
		return nil, failure("POLICY_UNENFORCEABLE", "effective policy engine or rules are unsupported")
	}
	chain, err := policyAuthority(ctx, tx, p, true)
	if err != nil {
		return nil, err
	}
	return &PolicySnapshot{p.RevisionID, p.Version, p.Rules, chain}, nil
}

func (s *Store) Policy(ctx context.Context, repo string) (EffectivePolicy, error) {
	result := EffectivePolicy{Engine: "local-loop/1", Rules: PolicyRules{10, 6000}}
	if err := (Scope{repo, "*", "*"}).validate(); err != nil {
		return result, err
	}
	if err := s.checkRepo(repo); err != nil {
		return result, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return result, err
	}
	defer tx.Rollback(ctx)
	p, err := currentPolicy(ctx, tx, repo)
	if err != nil {
		return result, err
	}
	if p != nil {
		_, err = policyAuthority(ctx, tx, p, false)
		if err != nil && Code(err) != "POLICY_UNENFORCEABLE" {
			return result, err
		}
		p.AuthorityLive = err == nil
		result = EffectivePolicy{Engine: "local-loop/2", Rules: p.Rules, Revision: p}
	}
	return result, tx.Commit(ctx)
}

func (s *Store) PolicyRevision(ctx context.Context, id string) (PolicyRevision, error) {
	if err := validID(id); err != nil {
		return PolicyRevision{}, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return PolicyRevision{}, err
	}
	defer tx.Rollback(ctx)
	p, err := readPolicyRevision(ctx, tx, id)
	if err != nil {
		return p, err
	}
	if err = s.checkRepo(p.Repo); err != nil {
		return PolicyRevision{}, err
	}
	_, err = policyAuthority(ctx, tx, &p, false)
	if err != nil && Code(err) != "POLICY_UNENFORCEABLE" {
		return PolicyRevision{}, err
	}
	p.AuthorityLive = err == nil
	return p, tx.Commit(ctx)
}

// Delivery requires the current repository policy as well as the restore fence.
// Replay and late observations deliberately do not pass through this guard.
func receiptDeliveryCurrent(ctx context.Context, tx pgx.Tx, id string) error {
	if err := receiptCurrentGeneration(ctx, tx, id); err != nil {
		return err
	}
	var repo, revision string
	err := tx.QueryRow(ctx, `SELECT r.scope->>'repo',COALESCE(p.revision_id::text,'') FROM cairn.retrieval_receipt r LEFT JOIN cairn.retrieval_policy p USING(receipt_id) WHERE r.receipt_id=$1`, id).Scan(&repo, &revision)
	if err != nil {
		return err
	}
	current, err := policySnapshot(ctx, tx, repo)
	if err != nil {
		return err
	}
	currentID := ""
	if current != nil {
		currentID = current.RevisionID
	}
	if currentID != revision {
		return failure("STALE_PACKAGE", "effective policy changed; compile with a new request ID")
	}
	return nil
}
