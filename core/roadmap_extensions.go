package core

import (
	"context"
	"errors"
	"math"
	"slices"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

type H1DriverConformanceProfile struct {
	ReceiptID        string    `json:"receipt_id"`
	HandleCount      int       `json:"handle_count"`
	ExpandedCount    int       `json:"expanded_count"`
	CreditsRemaining int       `json:"credits_remaining"`
	BytesRemaining   int       `json:"bytes_remaining"`
	ExpiresAt        time.Time `json:"expires_at"`
	Expired          bool      `json:"expired"`
	ExpansionReader  string    `json:"expansion_reader,omitempty"`
	ConformanceReady bool      `json:"conformance_ready"`
}

func (s *Store) InspectH1HandleProfile(ctx context.Context, receiptID string) (H1DriverConformanceProfile, error) {
	if err := validID(receiptID); err != nil {
		return H1DriverConformanceProfile{}, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return H1DriverConformanceProfile{}, err
	}
	defer tx.Rollback(ctx)
	var repo string
	err = tx.QueryRow(ctx, `SELECT scope->>'repo' FROM cairn.retrieval_receipt WHERE receipt_id=$1`, receiptID).Scan(&repo)
	if errors.Is(err, pgx.ErrNoRows) {
		return H1DriverConformanceProfile{}, failure("NOT_FOUND", "receipt not found")
	}
	if err != nil {
		return H1DriverConformanceProfile{}, err
	}
	if err = s.checkRepo(repo); err != nil {
		return H1DriverConformanceProfile{}, err
	}
	profile := H1DriverConformanceProfile{ReceiptID: receiptID}
	err = tx.QueryRow(ctx, `SELECT s.credits,s.remaining_bytes,s.expires_at,s.expires_at<=transaction_timestamp(),COALESCE(s.expansion_reader,'')
		FROM cairn.index_session s WHERE s.receipt_id=$1`, receiptID).Scan(&profile.CreditsRemaining, &profile.BytesRemaining, &profile.ExpiresAt, &profile.Expired, &profile.ExpansionReader)
	if errors.Is(err, pgx.ErrNoRows) {
		return H1DriverConformanceProfile{}, failure("NOT_FOUND", "index session not found for receipt")
	}
	if err != nil {
		return H1DriverConformanceProfile{}, err
	}
	if err = tx.QueryRow(ctx, `SELECT count(*) FROM cairn.index_handle WHERE receipt_id=$1`, receiptID).Scan(&profile.HandleCount); err != nil {
		return H1DriverConformanceProfile{}, err
	}
	if err = tx.QueryRow(ctx, `SELECT count(*) FROM cairn.usage_observation WHERE receipt_id=$1 AND signal='expanded'`, receiptID).Scan(&profile.ExpandedCount); err != nil {
		return H1DriverConformanceProfile{}, err
	}
	profile.ConformanceReady = profile.HandleCount > 0 && !profile.Expired && profile.CreditsRemaining >= 0 && profile.BytesRemaining >= 0
	return profile, tx.Commit(ctx)
}

type OfflineGroomerProposal struct {
	Repo                   string   `json:"repo"`
	TaskID                 string   `json:"task_id"`
	SourceRecordIDs        []string `json:"source_record_ids"`
	TotalVersions          int      `json:"total_versions"`
	StoppedSessionCount    int      `json:"stopped_session_count"`
	Disposition            string   `json:"disposition"`
	AutomaticAuthorityGain bool     `json:"automatic_authority_gain"`
}

func (s *Store) PreviewOfflineSessionConsolidation(ctx context.Context, repo string) ([]OfflineGroomerProposal, error) {
	if err := s.checkRepo(repo); err != nil {
		return nil, err
	}
	if strings.TrimSpace(repo) == "" || repo == "*" {
		return nil, failure("INVALID_REQUEST", "explicit repo required")
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	var stoppedSessions int
	if err = tx.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_session WHERE repo=$1 AND stopped=true`, repo).Scan(&stoppedSessions); err != nil {
		return nil, err
	}
	rows, err := tx.Query(ctx, `SELECT v.task_id,
		array_agg(DISTINCT v.record_id::text ORDER BY v.record_id::text),
		count(*)
		FROM cairn.record_version v
		JOIN cairn.memory_record m USING(record_id)
		WHERE v.repo=$1 AND m.lifecycle='active' AND m.class='A' AND v.kind IN ('handoff','note')
		GROUP BY v.task_id
		HAVING count(*) > 1 OR count(DISTINCT v.record_id) > 1
		ORDER BY v.task_id`, repo)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	proposals := []OfflineGroomerProposal{}
	for rows.Next() {
		p := OfflineGroomerProposal{
			Repo:                   repo,
			StoppedSessionCount:    stoppedSessions,
			Disposition:            "review_required",
			AutomaticAuthorityGain: false,
		}
		if err = rows.Scan(&p.TaskID, &p.SourceRecordIDs, &p.TotalVersions); err != nil {
			return nil, err
		}
		proposals = append(proposals, p)
	}
	if err = rows.Err(); err != nil {
		return nil, err
	}
	return proposals, tx.Commit(ctx)
}

type DecayCandidateComparison struct {
	RecordID       string  `json:"record_id"`
	Version        int     `json:"version"`
	BaselineRank   int     `json:"baseline_rank"`
	ExperimentRank int     `json:"experiment_rank"`
	RankShift      int     `json:"rank_shift"`
	DecayFactor    float64 `json:"decay_factor"`
	DecayedScore   float64 `json:"decayed_score"`
}

type DecayExperimentComparison struct {
	HalfLifeDays             float64                    `json:"half_life_days"`
	EvaluatedCount           int                        `json:"evaluated_count"`
	RankShiftCount           int                        `json:"rank_shift_count"`
	ProductionRankingMutated bool                       `json:"production_ranking_mutated"`
	Candidates               []DecayCandidateComparison `json:"candidates"`
}

func EvaluateDecayExperiment(candidates []CandidateEvaluation, now time.Time, halfLifeDays float64, incidentActive bool) (DecayExperimentComparison, error) {
	if incidentActive {
		return DecayExperimentComparison{}, failure("POLICY_UNENFORCEABLE", "learned ranking and decay experiments are disabled during active incidents")
	}
	if halfLifeDays <= 0 || math.IsNaN(halfLifeDays) || math.IsInf(halfLifeDays, 0) || now.IsZero() {
		return DecayExperimentComparison{}, failure("INVALID_REQUEST", "positive finite halfLifeDays and non-zero evaluation timestamp required")
	}
	base := slices.Clone(candidates)
	slices.SortFunc(base, func(a, b CandidateEvaluation) int {
		if a.Mandatory != b.Mandatory {
			if a.Mandatory {
				return -1
			}
			return 1
		}
		if a.LexicalMatches != b.LexicalMatches {
			return b.LexicalMatches - a.LexicalMatches
		}
		if a.ScopeSpecificity != b.ScopeSpecificity {
			return b.ScopeSpecificity - a.ScopeSpecificity
		}
		if !a.WrittenAt.Equal(b.WrittenAt) {
			if a.WrittenAt.After(b.WrittenAt) {
				return -1
			}
			return 1
		}
		if c := strings.Compare(a.RecordID, b.RecordID); c != 0 {
			return c
		}
		return b.Version - a.Version
	})
	return computeDecayComparison(base, now, halfLifeDays), nil
}

func computeDecayComparison(base []CandidateEvaluation, now time.Time, halfLifeDays float64) DecayExperimentComparison {
	type scored struct {
		cand         CandidateEvaluation
		baselineRank int
		decayFactor  float64
		decayedScore float64
	}
	items := make([]scored, len(base))
	for i, c := range base {
		ageDays := max(0.0, now.Sub(c.WrittenAt).Hours()/24.0)
		factor := math.Exp(-math.Ln2 * ageDays / halfLifeDays)
		baseScore := float64(c.LexicalMatches*100 + c.ScopeSpecificity*10)
		items[i] = scored{
			cand:         c,
			baselineRank: i + 1,
			decayFactor:  factor,
			decayedScore: baseScore * factor,
		}
	}
	slices.SortFunc(items, func(a, b scored) int {
		if a.cand.Mandatory != b.cand.Mandatory {
			if a.cand.Mandatory {
				return -1
			}
			return 1
		}
		if a.decayedScore != b.decayedScore {
			if a.decayedScore > b.decayedScore {
				return -1
			}
			return 1
		}
		if !a.cand.WrittenAt.Equal(b.cand.WrittenAt) {
			if a.cand.WrittenAt.After(b.cand.WrittenAt) {
				return -1
			}
			return 1
		}
		if c := strings.Compare(a.cand.RecordID, b.cand.RecordID); c != 0 {
			return c
		}
		return b.cand.Version - a.cand.Version
	})
	out := DecayExperimentComparison{
		HalfLifeDays:             halfLifeDays,
		EvaluatedCount:           len(items),
		ProductionRankingMutated: false,
		Candidates:               make([]DecayCandidateComparison, len(items)),
	}
	for i, item := range items {
		expRank := i + 1
		shift := item.baselineRank - expRank
		if shift != 0 {
			out.RankShiftCount++
		}
		out.Candidates[i] = DecayCandidateComparison{
			RecordID:       item.cand.RecordID,
			Version:        item.cand.Version,
			BaselineRank:   item.baselineRank,
			ExperimentRank: expRank,
			RankShift:      shift,
			DecayFactor:    item.decayFactor,
			DecayedScore:   item.decayedScore,
		}
	}
	return out
}

type H3MediationDecision struct {
	Repo             string            `json:"repo"`
	Destination      string            `json:"destination"`
	Purpose          string            `json:"purpose"`
	Allowed          bool              `json:"allowed"`
	EvaluatedCount   int               `json:"evaluated_count"`
	BlockedRecordIDs []string          `json:"blocked_record_ids"`
	BlockReasons     map[string]string `json:"block_reasons"`
}

func (s *Store) CheckH3RuntimeMediation(ctx context.Context, repo, destination, purpose string, recordIDs []string) (H3MediationDecision, error) {
	if err := s.checkRepo(repo); err != nil {
		return H3MediationDecision{}, err
	}
	if strings.TrimSpace(repo) == "" || repo == "*" || len(recordIDs) == 0 {
		return H3MediationDecision{}, failure("INVALID_REQUEST", "explicit repo and at least one record_id required")
	}
	if destination != "local" && destination != "hosted" {
		return H3MediationDecision{}, failure("INVALID_REQUEST", "destination must be local or hosted")
	}
	if purpose != "context" && purpose != "planning" && purpose != "consequential" {
		return H3MediationDecision{}, failure("INVALID_REQUEST", "purpose must be context, planning, or consequential")
	}
	for _, id := range recordIDs {
		if err := validID(id); err != nil {
			return H3MediationDecision{}, err
		}
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return H3MediationDecision{}, err
	}
	defer tx.Rollback(ctx)
	decision := H3MediationDecision{
		Repo:             repo,
		Destination:      destination,
		Purpose:          purpose,
		EvaluatedCount:   len(recordIDs),
		BlockedRecordIDs: []string{},
		BlockReasons:     map[string]string{},
	}
	for _, id := range recordIDs {
		reason, err := evaluateH3Record(ctx, tx, repo, destination, purpose, id)
		if err != nil {
			return H3MediationDecision{}, err
		}
		if reason != "" {
			decision.BlockedRecordIDs = append(decision.BlockedRecordIDs, id)
			decision.BlockReasons[id] = reason
		}
	}
	decision.Allowed = len(decision.BlockedRecordIDs) == 0
	return decision, tx.Commit(ctx)
}

func evaluateH3Record(ctx context.Context, tx pgx.Tx, repo, destination, purpose, id string) (string, error) {
	var recRepo, class, lifecycle, sensitivity string
	var openConflict bool
	err := tx.QueryRow(ctx, `SELECT v.repo,m.class,m.lifecycle,m.sensitivity,
		EXISTS (SELECT 1 FROM cairn.conflict_member cm JOIN cairn.conflict_group cg USING(conflict_id)
			WHERE cm.record_id=m.record_id AND cg.resolved_event IS NULL)
		FROM cairn.memory_record m
		JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version
		WHERE m.record_id=$1`, id).Scan(&recRepo, &class, &lifecycle, &sensitivity, &openConflict)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", failure("NOT_FOUND", "record not found")
	}
	if err != nil {
		return "", err
	}
	if recRepo != repo {
		return "", failure("AUTHORITY_DENIED", "record belongs to another repository")
	}
	if lifecycle != "active" {
		return "RECORD_INACTIVE", nil
	}
	if destination == "hosted" && sensitivity != "shareable" {
		return "DESTINATION_PROHIBITED", nil
	}
	if purpose == "consequential" && class != "B" && class != "C" {
		return "CLASS_NOT_CONSEQUENTIAL", nil
	}
	if openConflict {
		return "OPEN_CONFLICT", nil
	}
	return "", nil
}
