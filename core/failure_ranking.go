package core

import (
	"context"

	"github.com/jackc/pgx/v5"
)

// FailureMatch identifies the exact review that supplied a soft retrieval hint.
// Its sources remain fallible observations, not authority or proof of benefit.
type FailureMatch struct {
	ProposalID    string `json:"proposal_id"`
	ReviewVersion int    `json:"review_version"`
}

func hasFailureRanking(version string) bool {
	return version == "lexical-scope-recency/6" || version == "semantic-scope-recency/3"
}

func failureReason(reason string, matched bool) string {
	if matched {
		return "reviewed failure signature match; " + reason
	}
	return reason
}

func currentFailureMatches(ctx context.Context, tx pgx.Tx, req CompileRequest, dest Destination) (map[string]*FailureMatch, error) {
	matches := map[string]*FailureMatch{}
	if req.ErrorSignature == "" {
		return matches, nil
	}
	// Source execution labels describe the old task. Lesson scope and pins govern
	// reuse on a different task/harness. Never retarget a pin to a later version.
	rows, err := tx.Query(ctx, `SELECT DISTINCT ON (r.result_record) r.result_record::text,p.proposal_id::text,r.version
 FROM cairn.lesson_proposal p JOIN cairn.proposal_review r ON r.proposal_id=p.proposal_id AND r.version=p.version
 JOIN cairn.memory_record m ON m.record_id=r.result_record AND m.current_version=r.result_version
 JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version
 WHERE p.repo=$1 AND p.detail->>'error_signature_sha256'=$2 AND p.disposition='converted' AND r.disposition='converted'
 AND ($5::boolean OR r.signature_shareable)
 AND v.repo=$1 AND v.task_id IN ('*',$3) AND v.run_id IN ('*',$4) AND m.lifecycle='active'
 AND p.failure_version=(SELECT max(version) FROM cairn.run_assessment WHERE receipt_id=p.failure_receipt)
 AND (p.recovery_receipt IS NULL OR p.recovery_version=(SELECT max(version) FROM cairn.run_assessment WHERE receipt_id=p.recovery_receipt))
 ORDER BY r.result_record,p.proposal_id LIMIT 10000`, req.Scope.Repo, req.ErrorSignature, req.Scope.TaskID, req.Scope.RunID, dest.AllowLocal)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var id string
		var match FailureMatch
		if err = rows.Scan(&id, &match.ProposalID, &match.ReviewVersion); err != nil {
			return nil, err
		}
		matches[id] = &match
	}
	return matches, rows.Err()
}

func validateFrozenFailure(ctx context.Context, tx pgx.Tx, p SemanticPackage, e *CandidateEvaluation) error {
	if e.FailureMatch == nil {
		return nil
	}
	invalid := func() error {
		return failure("INTEGRITY_FAILURE", "historical failure review does not match the candidate")
	}
	if !hasFailureRanking(p.Ranking) || e.Facts == nil || e.Mandatory || validID(e.FailureMatch.ProposalID) != nil || e.FailureMatch.ReviewVersion < 2 {
		return invalid()
	}
	var valid bool
	// Freshness is deliberately frozen. Reopening a proposal or correcting a
	// source assessment changes new searches, not what an earlier search saw.
	err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.lesson_proposal p JOIN cairn.proposal_review r USING(proposal_id)
 WHERE p.proposal_id=$1 AND r.version=$2 AND r.disposition='converted' AND r.result_record=$3 AND r.result_version=$4
 AND p.repo=$5 AND p.detail->>'error_signature_sha256'=$6 AND ($7::boolean OR r.signature_shareable))`, e.FailureMatch.ProposalID, e.FailureMatch.ReviewVersion, e.RecordID, e.Version, p.Scope.Repo, p.ErrorSignature, p.Destination.AllowLocal).Scan(&valid)
	if err != nil {
		return err
	}
	if !valid {
		return invalid()
	}
	return nil
}
