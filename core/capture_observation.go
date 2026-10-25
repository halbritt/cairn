package core

import (
	"context"
	"math"
	"regexp"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// CaptureObservationRequest is bounded hook testimony about one capture
// invocation. Session and snapshot hashes are correlation references, not
// authenticated session identity or proof that a note was saved.
type CaptureObservationRequest struct {
	RequestID                string   `json:"request_id"`
	Repo                     string   `json:"repo"`
	Harness                  string   `json:"harness"`
	HookEvent                string   `json:"hook_event"`
	Method                   string   `json:"method"`
	SessionSHA256            string   `json:"session_sha256"`
	SnapshotSHA256           string   `json:"snapshot_sha256,omitempty"`
	Outcome                  string   `json:"outcome"`
	AdmissionCandidate       string   `json:"admission_candidate,omitempty"`
	SelectorOutcome          string   `json:"selector_outcome,omitempty"`
	RequestedModel           string   `json:"requested_model,omitempty"`
	ReportedModel            string   `json:"reported_model,omitempty"`
	DurationMS               *int64   `json:"duration_ms,omitempty"`
	SelectorDurationMS       *int64   `json:"selector_duration_ms,omitempty"`
	InputTokens              *int64   `json:"input_tokens,omitempty"`
	OutputTokens             *int64   `json:"output_tokens,omitempty"`
	CacheCreationInputTokens *int64   `json:"cache_creation_input_tokens,omitempty"`
	CacheReadInputTokens     *int64   `json:"cache_read_input_tokens,omitempty"`
	CostUSD                  *float64 `json:"cost_usd,omitempty"`
}

var captureMethod = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$`)

func (req CaptureObservationRequest) validate() error {
	bad := func() error { return failure("INVALID_REQUEST", "invalid bounded capture observation") }
	if req.Repo == "" || len(req.Repo) > 1024 || !captureMethod.MatchString(req.Method) ||
		!recallPromptDigest.MatchString(req.SessionSHA256) ||
		(req.SnapshotSHA256 != "" && !recallPromptDigest.MatchString(req.SnapshotSHA256)) {
		return bad()
	}
	switch req.Harness {
	case "claude", "codex", "opencode", "hermes":
	default:
		return bad()
	}
	switch req.HookEvent {
	case "Stop", "PreCompact", "SessionEnd":
	default:
		return bad()
	}
	switch req.Outcome {
	case "empty", "unchanged", "courtesy", "too_little_dialogue", "transcript_unavailable", "saved", "nothing_selected", "timeout", "error", "write_error", "state_commit_error":
	default:
		return bad()
	}
	if req.AdmissionCandidate != "" && req.AdmissionCandidate != "transport_only" {
		return bad()
	}
	switch req.SelectorOutcome {
	case "", "completed", "timeout", "error":
	default:
		return bad()
	}
	if !recallMilliseconds(req.DurationMS, req.SelectorDurationMS) {
		return bad()
	}
	if req.SelectorOutcome == "" && (req.SelectorDurationMS != nil || req.RequestedModel != "" || req.ReportedModel != "" || req.InputTokens != nil || req.OutputTokens != nil || req.CacheCreationInputTokens != nil || req.CacheReadInputTokens != nil || req.CostUSD != nil) {
		return bad()
	}
	for _, model := range []string{req.RequestedModel, req.ReportedModel} {
		if model != "" && !validRecallModel(model) {
			return bad()
		}
	}
	for _, tokens := range []*int64{req.InputTokens, req.OutputTokens, req.CacheCreationInputTokens, req.CacheReadInputTokens} {
		if tokens != nil && (*tokens < 0 || *tokens > maxRecallTokens) {
			return bad()
		}
	}
	if req.CostUSD != nil && (math.IsNaN(*req.CostUSD) || math.IsInf(*req.CostUSD, 0) || *req.CostUSD < 0 || *req.CostUSD >= maxRecallCostUSD) {
		return bad()
	}
	return nil
}

func (s *Store) RecordCaptureObservation(ctx context.Context, req CaptureObservationRequest) (Observation, error) {
	if err := req.validate(); err != nil {
		return Observation{}, err
	}
	if err := s.checkRepo(req.Repo); err != nil {
		return Observation{}, err
	}
	return mutate(ctx, s, "capture-observation", req.RequestID, req, func(tx pgx.Tx) (Observation, error) {
		id := uuid.NewString()
		_, err := tx.Exec(ctx, `INSERT INTO cairn.capture_observation(observation_id,repo,harness,hook_event,method,session_sha256,snapshot_sha256,outcome,admission_candidate,selector_outcome,requested_model,reported_model,duration_ms,selector_duration_ms,input_tokens,output_tokens,cache_creation_input_tokens,cache_read_input_tokens,cost_usd)
VALUES($1,$2,$3,$4,$5,$6,NULLIF($7,''),$8,NULLIF($9,''),NULLIF($10,''),NULLIF($11,''),NULLIF($12,''),$13,$14,$15,$16,$17,$18,$19)`,
			id, req.Repo, req.Harness, req.HookEvent, req.Method, req.SessionSHA256, req.SnapshotSHA256, req.Outcome, req.AdmissionCandidate, req.SelectorOutcome, req.RequestedModel, req.ReportedModel, req.DurationMS, req.SelectorDurationMS, req.InputTokens, req.OutputTokens, req.CacheCreationInputTokens, req.CacheReadInputTokens, req.CostUSD)
		return Observation{id}, err
	})
}

// CaptureObservation is a retained event, separate from last_capture in the
// hook state. Null metrics were not reported by the provider or hook.
type CaptureObservation struct {
	ObservationID            string    `json:"observation_id"`
	ObservedAt               time.Time `json:"observed_at"`
	Harness                  string    `json:"harness"`
	HookEvent                string    `json:"hook_event"`
	Method                   string    `json:"method"`
	SessionSHA256            string    `json:"session_sha256"`
	SnapshotSHA256           *string   `json:"snapshot_sha256"`
	Outcome                  string    `json:"outcome"`
	AdmissionCandidate       *string   `json:"admission_candidate"`
	SelectorOutcome          *string   `json:"selector_outcome"`
	RequestedModel           *string   `json:"requested_model"`
	ReportedModel            *string   `json:"reported_model"`
	DurationMS               *int64    `json:"duration_ms"`
	SelectorDurationMS       *int64    `json:"selector_duration_ms"`
	InputTokens              *int64    `json:"input_tokens"`
	OutputTokens             *int64    `json:"output_tokens"`
	CacheCreationInputTokens *int64    `json:"cache_creation_input_tokens"`
	CacheReadInputTokens     *int64    `json:"cache_read_input_tokens"`
	CostUSD                  *float64  `json:"cost_usd"`
}

type CaptureObservationsRequest struct {
	Repo          string `json:"repo"`
	SessionSHA256 string `json:"session_sha256,omitempty"`
	Limit         int    `json:"limit"`
	Offset        int    `json:"offset"`
}

type CaptureObservations struct {
	Rows           []CaptureObservation `json:"rows"`
	More           bool                 `json:"more"`
	NextOffset     int                  `json:"next_offset"`
	Interpretation string               `json:"interpretation"`
}

func (s *Store) CaptureObservations(ctx context.Context, req CaptureObservationsRequest) (CaptureObservations, error) {
	if req.Repo == "" || req.Limit < 1 || req.Limit > 200 || req.Offset < 0 || (req.SessionSHA256 != "" && !recallPromptDigest.MatchString(req.SessionSHA256)) {
		return CaptureObservations{}, failure("INVALID_REQUEST", "repo, limit 1-200, nonnegative offset and optional session SHA-256 required")
	}
	if err := s.checkRepo(req.Repo); err != nil {
		return CaptureObservations{}, err
	}
	rows, err := s.pool.Query(ctx, `SELECT observation_id::text,observed_at,harness,hook_event,method,session_sha256,snapshot_sha256,outcome,admission_candidate,selector_outcome,requested_model,reported_model,duration_ms,selector_duration_ms,input_tokens,output_tokens,cache_creation_input_tokens,cache_read_input_tokens,cost_usd
FROM cairn.capture_observation WHERE repo=$1 AND ($2='' OR session_sha256=$2) ORDER BY sequence LIMIT $3 OFFSET $4`, req.Repo, req.SessionSHA256, req.Limit+1, req.Offset)
	if err != nil {
		return CaptureObservations{}, err
	}
	defer rows.Close()
	result := CaptureObservations{Rows: []CaptureObservation{}, Interpretation: "Hook testimony only. Missing reports and null metrics are unknown; observed outcomes do not establish task acceptance or provider billing."}
	for rows.Next() {
		var row CaptureObservation
		if err := rows.Scan(&row.ObservationID, &row.ObservedAt, &row.Harness, &row.HookEvent, &row.Method, &row.SessionSHA256, &row.SnapshotSHA256, &row.Outcome, &row.AdmissionCandidate, &row.SelectorOutcome, &row.RequestedModel, &row.ReportedModel, &row.DurationMS, &row.SelectorDurationMS, &row.InputTokens, &row.OutputTokens, &row.CacheCreationInputTokens, &row.CacheReadInputTokens, &row.CostUSD); err != nil {
			return CaptureObservations{}, err
		}
		result.Rows = append(result.Rows, row)
	}
	if err := rows.Err(); err != nil {
		return CaptureObservations{}, err
	}
	if len(result.Rows) > req.Limit {
		result.Rows = result.Rows[:req.Limit]
		result.More = true
		result.NextOffset = req.Offset + req.Limit
	}
	return result, nil
}
