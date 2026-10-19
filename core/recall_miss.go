package core

import (
	"context"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

const (
	DefaultRecallMissMinLexicalMatches = 3
	RecallMissDocketAction             = "Review whether the owner re-explained an existing note that was not delivered; candidates are proposals, not confirmed misses."
)

type RecallMissCandidateView struct {
	CandidateID    string    `json:"candidate_id"`
	ObservationID  string    `json:"observation_id"`
	ReceiptID      string    `json:"receipt_id"`
	PromptSHA256   string    `json:"prompt_sha256"`
	RecordID       string    `json:"record_id"`
	Version        int       `json:"version"`
	LexicalMatches int       `json:"lexical_matches"`
	IDFScore       int64     `json:"idf_score"`
	Confirmed      bool      `json:"confirmed"`
	CreatedAt      time.Time `json:"created_at"`
}

type RecallMissSummary struct {
	Repo                       string                    `json:"repo"`
	WindowDays                 int                       `json:"window_days"`
	Since                      time.Time                 `json:"since"`
	Observations               int                       `json:"observations"`
	ObservationsWithCandidates int                       `json:"observations_with_candidates"`
	CandidateCount             int                       `json:"candidate_count"`
	DistinctNotes              int                       `json:"distinct_notes"`
	ConfirmedCount             int                       `json:"confirmed_count"`
	UnconfirmedCount           int                       `json:"unconfirmed_count"`
	PendingCount               int                       `json:"pending_count"`
	Precision                  *float64                  `json:"precision,omitempty"`
	Candidates                 []RecallMissCandidateView `json:"candidates"`
}

func (s *Store) recordRecallMissCandidates(ctx context.Context, tx pgx.Tx, observationID string, req RecallObservationRequest) error {
	promptSHA256 := req.PromptSHA256
	if promptSHA256 == "" {
		if err := tx.QueryRow(ctx, `SELECT encode(request_digest,'hex') FROM cairn.retrieval_receipt WHERE receipt_id=$1`, req.ReceiptIDs[0]).Scan(&promptSHA256); err != nil {
			return err
		}
	}
	delivered := req.DeliveredRecordIDs
	if delivered == nil {
		delivered = []string{}
	}
	rows, err := tx.Query(ctx, `SELECT DISTINCT ON (c.record_id,c.version)
 c.receipt_id::text,c.record_id::text,c.version,
 COALESCE((c.detail->>'lexical_matches')::int,0),
 COALESCE((c.detail->>'idf_score')::bigint,0)
 FROM cairn.retrieval_candidate c
 JOIN cairn.retrieval_receipt r USING (receipt_id)
 JOIN cairn.memory_record m ON m.record_id=c.record_id AND m.current_version=c.version
 WHERE r.receipt_id=ANY($1::uuid[])
  AND r.caller=$2 AND r.scope->>'repo'=$3 AND m.lifecycle='active'
  AND COALESCE((c.detail->>'lexical_matches')::int,0)>=$4
  AND c.reason IN ('SELECTED','INDEXED','OPTIONAL_BUDGET','TOTAL_BUDGET','WHOLE_PULL_BUDGET')
  AND NOT (c.record_id=ANY($5::uuid[]))
  AND NOT EXISTS (SELECT 1 FROM cairn.usage_observation uo WHERE uo.receipt_id=ANY($1::uuid[]) AND uo.record_id=c.record_id AND uo.version=c.version AND uo.signal IN ('expanded','cited'))
  AND NOT EXISTS (SELECT 1 FROM cairn.record_use u WHERE u.receipt_id=ANY($1::uuid[]) AND u.record_id=c.record_id AND u.version=c.version AND u.purpose='context-expansion')
  AND (
   c.reason IN ('INDEXED','OPTIONAL_BUDGET','TOTAL_BUDGET','WHOLE_PULL_BUDGET')
   OR $6::text='agent_tools'
   OR ($6::text='ambient' AND NOT EXISTS (SELECT 1 FROM cairn.record_use u WHERE u.receipt_id=c.receipt_id AND u.record_id=c.record_id AND u.version=c.version AND u.exposure_kind='body'))
   OR EXISTS (SELECT 1 FROM cairn.record_use u WHERE u.receipt_id=c.receipt_id AND u.record_id=c.record_id AND u.version=c.version AND u.exposure_kind='index')
   OR cardinality($5::uuid[])>0
  )
 ORDER BY c.record_id,c.version,r.created_at DESC,c.receipt_id`,
		req.ReceiptIDs, s.channel.Principal, req.Repo, DefaultRecallMissMinLexicalMatches, delivered, req.Method)
	if err != nil {
		return err
	}
	type pendingCandidate struct {
		receiptID      string
		recordID       string
		version        int
		lexicalMatches int
		idfScore       int64
	}
	pending := []pendingCandidate{}
	for rows.Next() {
		var item pendingCandidate
		if err = rows.Scan(&item.receiptID, &item.recordID, &item.version, &item.lexicalMatches, &item.idfScore); err != nil {
			rows.Close()
			return err
		}
		pending = append(pending, item)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	for _, item := range pending {
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.recall_miss_candidate(candidate_id,repo,observation_id,receipt_id,prompt_sha256,record_id,version,lexical_matches,idf_score)
 VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
 ON CONFLICT (repo,prompt_sha256,record_id,version) DO NOTHING`,
			uuid.NewString(), req.Repo, observationID, item.receiptID, promptSHA256, item.recordID, item.version, item.lexicalMatches, item.idfScore); err != nil {
			return err
		}
	}
	return nil
}

func recallMissDocketItems(ctx context.Context, tx pgx.Tx, repo string) ([]DocketItem, error) {
	rows, err := tx.Query(ctx, `SELECT DISTINCT ON (c.record_id,c.version) c.record_id::text,c.version,c.receipt_id::text
 FROM cairn.recall_miss_candidate c
 JOIN cairn.memory_record m ON m.record_id=c.record_id AND m.current_version=c.version
 WHERE c.repo=$1 AND m.lifecycle='active'
 ORDER BY c.record_id,c.version,c.created_at DESC,c.receipt_id LIMIT 101`, repo)
	if err != nil {
		return nil, err
	}
	items := []DocketItem{}
	for rows.Next() {
		item := DocketItem{Reason: "RECALL_MISS_CANDIDATE", Action: RecallMissDocketAction}
		if err = rows.Scan(&item.RecordID, &item.Version, &item.ReceiptID); err != nil {
			rows.Close()
			return nil, err
		}
		items = append(items, item)
	}
	err = rows.Err()
	rows.Close()
	return items, err
}

const recallMissConfirmedSQL = `(
 EXISTS (SELECT 1 FROM cairn.usage_observation uo JOIN cairn.retrieval_receipt r USING (receipt_id)
  WHERE r.scope->>'repo'=c.repo AND uo.record_id=c.record_id AND uo.version=c.version
   AND uo.signal IN ('expanded','cited') AND uo.observed_at>=c.created_at)
 OR EXISTS (SELECT 1 FROM cairn.record_use u JOIN cairn.retrieval_receipt r USING (receipt_id)
  WHERE r.scope->>'repo'=c.repo AND u.record_id=c.record_id AND u.version=c.version
   AND (u.purpose='context-expansion' OR (u.exposure_kind='body' AND u.receipt_id<>c.receipt_id))
   AND u.used_at>=c.created_at)
 OR EXISTS (SELECT 1 FROM cairn.run_assessment a WHERE a.receipt_id=c.receipt_id AND a.task_outcome='rejected')
)`

// RecallMissReport returns a bounded candidate review and precision summary
// over the requested UTC day window (defaulting to 7 days) without storing raw
// prompt text.
func (s *Store) RecallMissReport(ctx context.Context, repo string, days int) (RecallMissSummary, error) {
	if err := s.checkRepo(repo); err != nil {
		return RecallMissSummary{}, err
	}
	if repo == "" {
		return RecallMissSummary{}, failure("INVALID_REQUEST", "repository required")
	}
	if days == 0 {
		days = DefaultRecallRollupDays
	}
	if days < 1 || days > MaxRecallRollupDays {
		return RecallMissSummary{}, failure("INVALID_REQUEST", "recall miss report window out of range")
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return RecallMissSummary{}, err
	}
	defer tx.Rollback(ctx)
	summary := RecallMissSummary{Repo: repo, WindowDays: days, Candidates: []RecallMissCandidateView{}}
	if err = tx.QueryRow(ctx, `SELECT (date_trunc('day', now() AT TIME ZONE 'UTC') - make_interval(days => $1 - 1)) AT TIME ZONE 'UTC'`, days).Scan(&summary.Since); err != nil {
		return summary, err
	}
	summary.Since = summary.Since.UTC()
	if err = tx.QueryRow(ctx, `SELECT count(*) FROM cairn.recall_observation WHERE repo=$1 AND hook_event='UserPromptSubmit' AND observed_at>=$2`, repo, summary.Since).Scan(&summary.Observations); err != nil {
		return summary, err
	}
	if err = tx.QueryRow(ctx, `SELECT count(*),count(DISTINCT observation_id),count(DISTINCT (record_id,version)),
 count(*) FILTER (WHERE `+recallMissConfirmedSQL+`)
 FROM cairn.recall_miss_candidate c WHERE c.repo=$1 AND c.created_at>=$2`, repo, summary.Since).Scan(
		&summary.CandidateCount, &summary.ObservationsWithCandidates, &summary.DistinctNotes, &summary.ConfirmedCount); err != nil {
		return summary, err
	}
	summary.UnconfirmedCount = summary.CandidateCount - summary.ConfirmedCount
	summary.PendingCount = summary.UnconfirmedCount
	if summary.CandidateCount > 0 {
		p := float64(summary.ConfirmedCount) / float64(summary.CandidateCount)
		summary.Precision = &p
	}
	rows, err := tx.Query(ctx, `SELECT c.candidate_id::text,c.observation_id::text,c.receipt_id::text,c.prompt_sha256,
 c.record_id::text,c.version,c.lexical_matches,c.idf_score,`+recallMissConfirmedSQL+`,c.created_at
 FROM cairn.recall_miss_candidate c WHERE c.repo=$1 AND c.created_at>=$2
 ORDER BY c.created_at DESC,c.candidate_id LIMIT 100`, repo, summary.Since)
	if err != nil {
		return summary, err
	}
	for rows.Next() {
		var view RecallMissCandidateView
		if err = rows.Scan(&view.CandidateID, &view.ObservationID, &view.ReceiptID, &view.PromptSHA256,
			&view.RecordID, &view.Version, &view.LexicalMatches, &view.IDFScore, &view.Confirmed, &view.CreatedAt); err != nil {
			rows.Close()
			return summary, err
		}
		summary.Candidates = append(summary.Candidates, view)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return summary, err
	}
	return summary, tx.Commit(ctx)
}
