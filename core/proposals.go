package core

import (
	"context"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"time"
)

type GenerateProposalsRequest struct {
	RequestID string `json:"request_id"`
	Repo      string `json:"repo"`
	Offset    int    `json:"offset"`
}
type Proposal struct {
	SourceCurrent   bool       `json:"source_current"`
	ID              string     `json:"proposal_id"`
	Repo            string     `json:"repo"`
	Version         int        `json:"version"`
	Disposition     string     `json:"disposition"`
	FailureReceipt  string     `json:"failure_receipt"`
	FailureVersion  int        `json:"failure_version"`
	RecoveryReceipt string     `json:"recovery_receipt"`
	RecoveryVersion int        `json:"recovery_version"`
	EvidenceIDs     []string   `json:"evidence_ids"`
	TaskClass       string     `json:"task_class"`
	BindingID       string     `json:"binding_id"`
	CapabilityID    string     `json:"capability_id"`
	ErrorSignature  string     `json:"error_signature_sha256"`
	Method          string     `json:"method"`
	DueAt           *time.Time `json:"due_at,omitempty"`
	ResultRecord    string     `json:"result_record,omitempty"`
}
type ProposalBatch struct {
	Proposals  []Proposal `json:"proposals"`
	More       bool       `json:"more"`
	NextOffset int        `json:"next_offset"`
}

func (s *Store) GenerateProposals(ctx context.Context, req GenerateProposalsRequest) (ProposalBatch, error) {
	if !s.channel.Operator {
		return ProposalBatch{}, failure("AUTHORITY_DENIED", "proposal generation is local operator work")
	}
	if err := s.checkRepo(req.Repo); err != nil {
		return ProposalBatch{}, err
	}
	if req.Repo == "" || req.Offset < 0 {
		return ProposalBatch{}, failure("INVALID_REQUEST", "repo and nonnegative offset required")
	}
	return mutate(ctx, s, "generate-proposals", req.RequestID, req, func(tx pgx.Tx) (ProposalBatch, error) {
		rows, err := tx.Query(ctx, `WITH latest AS (SELECT DISTINCT ON(receipt_id) * FROM cairn.run_assessment ORDER BY receipt_id,version DESC)
 SELECT f.receipt_id::text,f.version,success.receipt_id::text,success.version,b.task_class,b.binding_id,b.capability_id,f.detail->>'error_signature_sha256'
 FROM latest f JOIN cairn.retrieval_receipt r USING(receipt_id) JOIN cairn.run_binding b USING(receipt_id)
 JOIN LATERAL (SELECT a.receipt_id,a.version FROM latest a JOIN cairn.retrieval_receipt rr USING(receipt_id) JOIN cairn.run_binding bb USING(receipt_id)
 WHERE a.task_outcome='accepted' AND rr.scope->>'repo'=r.scope->>'repo' AND rr.scope->>'task_id'=r.scope->>'task_id'
 AND bb.task_class=b.task_class AND bb.binding_id=b.binding_id AND bb.capability_id=b.capability_id
 AND rr.created_at>r.created_at ORDER BY rr.created_at,rr.receipt_id LIMIT 1) success ON true
 WHERE r.scope->>'repo'=$1 AND f.task_outcome='rejected' AND f.failure_domain='task'
 AND COALESCE(f.detail->>'error_signature_sha256','')<>'' AND b.task_class<>'unknown' AND b.binding_id<>'unknown' AND b.capability_id<>'unknown'
 ORDER BY r.created_at,f.receipt_id LIMIT 101 OFFSET $2`, req.Repo, req.Offset)
		if err != nil {
			return ProposalBatch{}, err
		}
		candidates := []Proposal{}
		for rows.Next() {
			p := Proposal{Repo: req.Repo, Version: 1, Disposition: "open", Method: "failure-recovery-pair/1", EvidenceIDs: []string{}}
			if err = rows.Scan(&p.FailureReceipt, &p.FailureVersion, &p.RecoveryReceipt, &p.RecoveryVersion, &p.TaskClass, &p.BindingID, &p.CapabilityID, &p.ErrorSignature); err != nil {
				rows.Close()
				return ProposalBatch{}, err
			}
			candidates = append(candidates, p)
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return ProposalBatch{}, err
		}
		batch := ProposalBatch{Proposals: []Proposal{}, More: len(candidates) > 100, NextOffset: req.Offset + min(len(candidates), 100)}
		if batch.More {
			candidates = candidates[:100]
		}
		for _, p := range candidates {
			if err = tx.QueryRow(ctx, `SELECT COALESCE(array_agg(DISTINCT evidence_id::text ORDER BY evidence_id::text),'{}') FROM cairn.assessment_evidence WHERE (receipt_id=$1 AND version=$2) OR (receipt_id=$3 AND version=$4)`, p.FailureReceipt, p.FailureVersion, p.RecoveryReceipt, p.RecoveryVersion).Scan(&p.EvidenceIDs); err != nil {
				return batch, err
			}
			p.ID = uuid.NewString()
			_, err = tx.Exec(ctx, `INSERT INTO cairn.lesson_proposal(proposal_id,repo,failure_receipt,failure_version,recovery_receipt,recovery_version,detail) VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(failure_receipt,failure_version,recovery_receipt,recovery_version) DO NOTHING`, p.ID, p.Repo, p.FailureReceipt, p.FailureVersion, p.RecoveryReceipt, p.RecoveryVersion, p)
			if err != nil {
				return batch, err
			}
			var id string
			if err = tx.QueryRow(ctx, `SELECT proposal_id::text FROM cairn.lesson_proposal WHERE failure_receipt=$1 AND failure_version=$2 AND recovery_receipt=$3 AND recovery_version=$4`, p.FailureReceipt, p.FailureVersion, p.RecoveryReceipt, p.RecoveryVersion).Scan(&id); err != nil {
				return batch, err
			}
			stored, err := readProposal(ctx, tx, id)
			if err != nil {
				return batch, err
			}
			batch.Proposals = append(batch.Proposals, stored)
		}
		return batch, nil
	})
}
func readProposal(ctx context.Context, tx pgx.Tx, id string) (Proposal, error) {
	var p Proposal
	var version int
	var disposition, result string
	var due *time.Time
	err := tx.QueryRow(ctx, `SELECT detail,version,disposition,due_at,COALESCE(result_record::text,''),failure_version=(SELECT max(version) FROM cairn.run_assessment WHERE receipt_id=failure_receipt) AND recovery_version=(SELECT max(version) FROM cairn.run_assessment WHERE receipt_id=recovery_receipt) FROM cairn.lesson_proposal WHERE proposal_id=$1`, id).Scan(&p, &version, &disposition, &due, &result, &p.SourceCurrent)
	if err == pgx.ErrNoRows {
		return p, failure("NOT_FOUND", "proposal not found")
	}
	p.Version = version
	p.Disposition = disposition
	p.DueAt = due
	p.ResultRecord = result
	return p, err
}
func (s *Store) Proposal(ctx context.Context, id string) (Proposal, error) {
	if err := validID(id); err != nil {
		return Proposal{}, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return Proposal{}, err
	}
	defer tx.Rollback(ctx)
	p, err := readProposal(ctx, tx, id)
	if err != nil {
		return p, err
	}
	if err = s.checkRepo(p.Repo); err != nil {
		return Proposal{}, err
	}
	return p, tx.Commit(ctx)
}

type ReviewProposalRequest struct {
	RequestID       string     `json:"request_id"`
	ProposalID      string     `json:"proposal_id"`
	ExpectedVersion int        `json:"expected_version"`
	Disposition     string     `json:"disposition"`
	Until           *time.Time `json:"until"`
	ResultRecord    string     `json:"result_record,omitempty"`
	Reason          string     `json:"reason"`
}

func (s *Store) ReviewProposal(ctx context.Context, req ReviewProposalRequest) (Proposal, error) {
	if !s.channel.Operator {
		return Proposal{}, failure("AUTHORITY_DENIED", "proposal disposition requires operator review")
	}
	if err := validID(req.ProposalID); err != nil {
		return Proposal{}, err
	}
	if err := reasonValid(req.Reason); err != nil {
		return Proposal{}, err
	}
	switch req.Disposition {
	case "open", "dismissed", "deferred", "converted":
	default:
		return Proposal{}, failure("INVALID_REQUEST", "unknown disposition")
	}
	if req.Disposition == "deferred" && (req.Until == nil || !req.Until.After(time.Now())) {
		return Proposal{}, failure("INVALID_REQUEST", "deferral requires a future review time")
	}
	if req.Disposition != "deferred" && req.Until != nil {
		return Proposal{}, failure("INVALID_REQUEST", "only deferred proposals have a review time")
	}
	if req.Disposition == "converted" {
		if err := validID(req.ResultRecord); err != nil {
			return Proposal{}, err
		}
	} else if req.ResultRecord != "" {
		return Proposal{}, failure("INVALID_REQUEST", "only converted proposals link a resulting record")
	}
	return mutate(ctx, s, "review-proposal", req.RequestID, req, func(tx pgx.Tx) (Proposal, error) {
		if _, err := tx.Exec(ctx, `SELECT 1 FROM cairn.lesson_proposal WHERE proposal_id=$1 FOR UPDATE`, req.ProposalID); err != nil {
			return Proposal{}, err
		}
		p, err := readProposal(ctx, tx, req.ProposalID)
		if err != nil {
			return p, err
		}
		if err = s.checkRepo(p.Repo); err != nil {
			return Proposal{}, err
		}
		if p.Version != req.ExpectedVersion {
			return p, failure("VERSION_CONFLICT", "proposal review version changed")
		}
		if _, err = tx.Exec(ctx, `SELECT 1 FROM cairn.retrieval_receipt WHERE receipt_id IN ($1,$2) ORDER BY receipt_id FOR SHARE`, p.FailureReceipt, p.RecoveryReceipt); err != nil {
			return p, err
		}
		p, err = readProposal(ctx, tx, p.ID)
		if err != nil {
			return p, err
		}
		if !p.SourceCurrent && (req.Disposition == "converted" || req.Disposition == "open") {
			return p, failure("STALE_PROPOSAL", "source assessment changed; generate and review a current proposal")
		}
		if req.ResultRecord != "" {
			for _, id := range p.EvidenceIDs {
				if err = checkAssessmentEvidence(ctx, tx, id, p.Repo); err != nil {
					return p, err
				}
			}

			record, err := readRecord(ctx, tx, req.ResultRecord)
			if err != nil {
				return p, err
			}
			if record.Scope.Repo != p.Repo {
				return p, failure("AUTHORITY_DENIED", "result record is outside proposal repository")
			}
		}
		if _, err = tx.Exec(ctx, `UPDATE cairn.lesson_proposal SET version=version+1,disposition=$2,due_at=$3,result_record=NULLIF($4,'')::uuid WHERE proposal_id=$1`, p.ID, req.Disposition, req.Until, req.ResultRecord); err != nil {
			return p, err
		}
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.proposal_review(proposal_id,version,disposition,reason) VALUES($1,$2,$3,$4)`, p.ID, p.Version+1, req.Disposition, req.Reason); err != nil {
			return p, err
		}
		return readProposal(ctx, tx, p.ID)
	})
}
