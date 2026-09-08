package core

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

// CandidateEvaluation contains features, never query text or candidate bodies.
// Rank is the one-based eligible ordering before packing; zero means unranked.
type CandidateEvaluation struct {
	Facts             *CandidateFacts `json:"facts,omitempty"`
	RecordID          string          `json:"record_id"`
	Version           int             `json:"version"`
	Class             string          `json:"class"`
	Reason            string          `json:"reason"`
	EscalationBlocked bool            `json:"escalation_blocked"`
	LexicalMatches    int             `json:"lexical_matches"`
	ScopeSpecificity  int             `json:"scope_specificity"`
	WrittenAt         time.Time       `json:"written_at"`
	Mandatory         bool            `json:"mandatory"`
	Rank              int             `json:"rank"`
	Cost              int             `json:"cost_bytes"`
}

type Explanation struct {
	ReceiptID  string                `json:"receipt_id"`
	Version    int                   `json:"version"`
	Candidates []CandidateEvaluation `json:"candidates"`
}

// Explain requires the same authenticated caller and repository as Replay.
// Version zero explicitly means a legacy receipt has no candidate detail.
func (s *Store) Explain(ctx context.Context, receiptID string) (Explanation, error) {
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return Explanation{}, err
	}
	defer tx.Rollback(ctx)
	if err = s.receiptAccess(ctx, tx, receiptID); err != nil {
		return Explanation{}, err
	}
	result := Explanation{ReceiptID: receiptID, Candidates: []CandidateEvaluation{}}
	if err = tx.QueryRow(ctx, `SELECT explanation_version FROM cairn.retrieval_receipt WHERE receipt_id=$1`, receiptID).Scan(&result.Version); err != nil {
		return result, err
	}
	rows, err := tx.Query(ctx, `SELECT detail FROM cairn.retrieval_candidate WHERE receipt_id=$1 ORDER BY record_id,version`, receiptID)
	if err != nil {
		return result, err
	}
	for rows.Next() {
		var item CandidateEvaluation
		if err = rows.Scan(&item); err != nil {
			rows.Close()
			return result, err
		}
		result.Candidates = append(result.Candidates, item)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return result, err
	}
	return result, tx.Commit(ctx)
}

func omissionCensus() map[string]int {
	return map[string]int{"CONTEXT_MISSING": 0, "CURRENTNESS_MISMATCH": 0, "OUTSIDE_VALIDITY": 0, "CLASS_NOT_CONSEQUENTIAL": 0, "AUTHORITY_INACTIVE": 0, "POLICY_UNENFORCEABLE": 0, "OPEN_CONFLICT": 0, "ATTRIBUTION_UNRECONCILED": 0, "EVIDENCE_UNAVAILABLE": 0, "NO_LEXICAL_MATCH": 0, "REDUNDANT": 0, "OPTIONAL_BUDGET": 0, "TOTAL_BUDGET": 0}
}

// Frozen gate facts reference immutable record versions; raw evidence and query
// bytes are not duplicated here. These facts are for historical inspection only.
type CandidateFacts struct {
	Category         string     `json:"category,omitempty"`
	BodySHA256       string     `json:"body_sha256"`
	Sensitivity      string     `json:"sensitivity"`
	AttributionState string     `json:"attribution_state"`
	Evidence         []Evidence `json:"evidence"`
	Authority        []Grant    `json:"authority"`
}
