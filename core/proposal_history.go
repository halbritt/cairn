package core

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

type ProposalHistoryRequest struct {
	ProposalID    string `json:"proposal_id"`
	BeforeVersion int    `json:"before_version,omitempty"`
	Limit         int    `json:"limit,omitempty"`
}

type ProposalReview struct {
	SignatureShareable bool       `json:"signature_shareable,omitempty"`
	Version            int        `json:"version"`
	Disposition        string     `json:"disposition"`
	Reason             string     `json:"reason"`
	Observer           string     `json:"observer"`
	ObservedAt         time.Time  `json:"observed_at"`
	ResultRecord       string     `json:"result_record,omitempty"`
	ResultVersion      int        `json:"result_version,omitempty"`
	DueAt              *time.Time `json:"due_at,omitempty"`
}

type ProposalReviewHistory struct {
	Proposal          Proposal         `json:"proposal"`
	Reviews           []ProposalReview `json:"reviews"`
	More              bool             `json:"more"`
	NextBeforeVersion *int             `json:"next_before_version,omitempty"`
}

// ProposalHistory returns retained review decisions, not current lesson bodies.
// Creation is proposal version 1; reviews begin at version 2. Legacy result pins
// remain unknown instead of being reconstructed from today's lesson version.
func (s *Store) ProposalHistory(ctx context.Context, req ProposalHistoryRequest) (ProposalReviewHistory, error) {
	if err := validID(req.ProposalID); err != nil {
		return ProposalReviewHistory{}, err
	}
	if req.BeforeVersion < 0 || req.BeforeVersion > 2147483647 || req.Limit < 0 || req.Limit > 100 {
		return ProposalReviewHistory{}, failure("INVALID_REQUEST", "proposal history requires limit 1-100 and a positive before_version when supplied")
	}
	limit := req.Limit
	if limit == 0 {
		limit = 20
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return ProposalReviewHistory{}, err
	}
	defer tx.Rollback(ctx)
	p, err := readProposal(ctx, tx, req.ProposalID)
	if err != nil {
		return ProposalReviewHistory{}, err
	}
	if err = s.checkRepo(p.Repo); err != nil {
		return ProposalReviewHistory{}, err
	}
	rows, err := tx.Query(ctx, `SELECT version,disposition,reason,observer,observed_at,COALESCE(result_record::text,''),COALESCE(result_version,0),due_at,signature_shareable
 FROM cairn.proposal_review WHERE proposal_id=$1 AND ($2::integer=0 OR version<$2)
 ORDER BY version DESC LIMIT $3`, req.ProposalID, req.BeforeVersion, limit+1)
	if err != nil {
		return ProposalReviewHistory{}, err
	}
	result := ProposalReviewHistory{Proposal: p, Reviews: []ProposalReview{}}
	for rows.Next() {
		var r ProposalReview
		if err = rows.Scan(&r.Version, &r.Disposition, &r.Reason, &r.Observer, &r.ObservedAt, &r.ResultRecord, &r.ResultVersion, &r.DueAt, &r.SignatureShareable); err != nil {
			rows.Close()
			return ProposalReviewHistory{}, err
		}
		result.Reviews = append(result.Reviews, r)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return ProposalReviewHistory{}, err
	}
	if len(result.Reviews) > limit {
		result.More = true
		result.Reviews = result.Reviews[:limit]
		version := result.Reviews[limit-1].Version
		result.NextBeforeVersion = &version
	}
	return result, tx.Commit(ctx)
}
