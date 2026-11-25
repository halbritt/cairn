package core

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

type TelemetryReportRequest struct {
	Repo            string `json:"repo"`
	Harness         string `json:"harness,omitempty"`
	NativeSessionID string `json:"native_session_id,omitempty"`
	RollupDays      int    `json:"rollup_days,omitempty"`
}

type TelemetryRecallCounters struct {
	Day                     string       `json:"day"`
	Harness                 string       `json:"harness"`
	Observations            int          `json:"observations"`
	Completed               int          `json:"completed"`
	Timeouts                int          `json:"timeouts"`
	Errors                  int          `json:"errors"`
	DedupSkippedTotal       *int64       `json:"dedup_skipped_total"`
	InjectedBytes           RecallMetric `json:"injected_bytes"`
	InjectedUnits           []string     `json:"injected_units"`
	InjectedTokenizers      []string     `json:"injected_tokenizers"`
	SelectorCalls           *int64       `json:"selector_calls"`
	SelectorReasoningTokens *int64       `json:"selector_reasoning_tokens"`
	SelectorPartialUsage    *int64       `json:"selector_partial_usage_calls"`
}

type TelemetryDeliveryCounters struct {
	Day                  string       `json:"day"`
	Harness              string       `json:"harness"`
	Observations         int          `json:"observations"`
	Full                 int          `json:"full"`
	Compact              int          `json:"compact"`
	Digest               int          `json:"digest"`
	AutoSettled          int          `json:"auto_settled"`
	Superseded           int          `json:"superseded"`
	SupersededCountTotal int64        `json:"superseded_count_total"`
	InjectedBytes        RecallMetric `json:"injected_bytes"`
	InjectedUnits        []string     `json:"injected_units"`
}

type WindowGrowthMetrics struct {
	SessionsMeasured             int      `json:"sessions_measured"`
	ObservationsMeasured         int      `json:"observations_measured"`
	ByteUnit                     string   `json:"byte_unit"`
	TokenUnit                    string   `json:"token_unit"`
	MeanDialogueBytesPerStep     *float64 `json:"mean_dialogue_bytes_per_step"`
	MedianDialogueBytesPerStep   *int64   `json:"median_dialogue_bytes_per_step"`
	MeanInjectedBytesPerPrompt   *float64 `json:"mean_injected_bytes_per_prompt"`
	MedianInjectedBytesPerPrompt *int64   `json:"median_injected_bytes_per_prompt"`
	MeanPromptTokensPerStep      *float64 `json:"mean_prompt_tokens_per_step"`
	MedianPromptTokensPerStep    *int64   `json:"median_prompt_tokens_per_step"`
}

type SessionLongevityMetrics struct {
	SessionsObserved                   int    `json:"sessions_observed"`
	CompactedSessions                  int    `json:"compacted_sessions"`
	UncompactedSessions                int    `json:"uncompacted_sessions"`
	MedianSecondsBeforeFirstCompaction *int64 `json:"median_seconds_before_first_compaction"`
	P95SecondsBeforeFirstCompaction    *int64 `json:"p95_seconds_before_first_compaction"`
	MedianTurnsBeforeFirstCompaction   *int64 `json:"median_turns_before_first_compaction"`
	P95TurnsBeforeFirstCompaction      *int64 `json:"p95_turns_before_first_compaction"`
	MedianUncompactedDurationSeconds   *int64 `json:"median_uncompacted_duration_seconds"`
}

type TelemetryReport struct {
	Repo                   string                      `json:"repo"`
	WindowDays             int                         `json:"window_days"`
	Since                  time.Time                   `json:"since"`
	RecallCounters         []TelemetryRecallCounters   `json:"recall_counters"`
	DeliveryCounters       []TelemetryDeliveryCounters `json:"delivery_counters"`
	PerMessageWindowGrowth WindowGrowthMetrics         `json:"per_message_window_growth"`
	SessionLongevity       SessionLongevityMetrics     `json:"session_longevity_before_compaction"`
	Interpretation         string                      `json:"interpretation"`
}

// TelemetryReport returns bounded recall/delivery counters alongside queryable
// per-message window growth and session longevity before compaction metrics.
// Null metrics are unobserved or lack >=2 ordered steps; they are never zero-filled.
func (s *Store) TelemetryReport(ctx context.Context, req TelemetryReportRequest) (TelemetryReport, error) {
	if req.Repo == "" || len(req.Repo) > 1024 {
		return TelemetryReport{}, failure("INVALID_REQUEST", "repo required")
	}
	if req.Harness != "" {
		switch req.Harness {
		case "claude", "codex", "opencode", "hermes", "agy":
		default:
			return TelemetryReport{}, failure("INVALID_REQUEST", "unknown harness")
		}
	}
	if req.NativeSessionID != "" && !validTelemetryString(req.NativeSessionID, 256) {
		return TelemetryReport{}, failure("INVALID_REQUEST", "invalid native_session_id")
	}
	if req.RollupDays == 0 {
		req.RollupDays = DefaultRecallRollupDays
	}
	if req.RollupDays < 1 || req.RollupDays > MaxRecallRollupDays {
		return TelemetryReport{}, failure("INVALID_REQUEST", "rollup_days must be 1-90")
	}
	if err := s.checkRepo(req.Repo); err != nil {
		return TelemetryReport{}, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return TelemetryReport{}, err
	}
	defer tx.Rollback(ctx)
	out := TelemetryReport{
		Repo:             req.Repo,
		WindowDays:       req.RollupDays,
		RecallCounters:   []TelemetryRecallCounters{},
		DeliveryCounters: []TelemetryDeliveryCounters{},
		PerMessageWindowGrowth: WindowGrowthMetrics{
			ByteUnit:  "utf8_bytes",
			TokenUnit: "provider_reported_tokens",
		},
		Interpretation: "Hook and session telemetry only. Byte measurements (utf8_bytes) and provider-reported token counts (provider_reported_tokens) are kept separate and never conflated. Null metrics indicate insufficient or unreported observations, never zero.",
	}
	if err := tx.QueryRow(ctx, `SELECT (date_trunc('day', now() AT TIME ZONE 'UTC') - make_interval(days => $1 - 1)) AT TIME ZONE 'UTC'`, req.RollupDays).Scan(&out.Since); err != nil {
		return out, err
	}
	out.Since = out.Since.UTC()

	rRows, err := tx.Query(ctx, `SELECT
 (observed_at AT TIME ZONE 'UTC')::date::text,
 harness,
 count(*)::int,
 count(*) FILTER (WHERE status='completed')::int,
 count(*) FILTER (WHERE status='timeout')::int,
 count(*) FILTER (WHERE status='error')::int,
 sum(dedup_skipped_count)::bigint,
 `+recallMetricSQL("injected_bytes")+`,
 COALESCE(array_agg(DISTINCT injected_unit) FILTER (WHERE injected_unit IS NOT NULL), ARRAY[]::text[]),
 COALESCE(array_agg(DISTINCT injected_tokenizer) FILTER (WHERE injected_tokenizer IS NOT NULL), ARRAY[]::text[]),
 sum(selector_calls)::bigint
 FROM cairn.recall_observation
 WHERE repo=$1 AND observed_at>=$2 AND ($3='' OR harness=$3) AND ($4='' OR native_session_id=$4)
 GROUP BY 1,2 ORDER BY 1,2`, req.Repo, out.Since, req.Harness, req.NativeSessionID)
	if err != nil {
		return out, err
	}
	recallIdx := map[recallDayHarness]int{}
	for rRows.Next() {
		var row TelemetryRecallCounters
		if err := rRows.Scan(&row.Day, &row.Harness, &row.Observations, &row.Completed, &row.Timeouts, &row.Errors,
			&row.DedupSkippedTotal,
			&row.InjectedBytes.Known, &row.InjectedBytes.Unknown, &row.InjectedBytes.Median, &row.InjectedBytes.P95, &row.InjectedBytes.Total,
			&row.InjectedUnits, &row.InjectedTokenizers, &row.SelectorCalls); err != nil {
			rRows.Close()
			return out, err
		}
		recallIdx[recallDayHarness{row.Day, row.Harness}] = len(out.RecallCounters)
		out.RecallCounters = append(out.RecallCounters, row)
	}
	if err := rRows.Err(); err != nil {
		rRows.Close()
		return out, err
	}
	rRows.Close()

	sRows, err := tx.Query(ctx, `SELECT
 (o.observed_at AT TIME ZONE 'UTC')::date::text,
 o.harness,
 sum(c.reasoning_tokens)::bigint,
 count(*) FILTER (WHERE c.partial_usage IS TRUE)::bigint
 FROM cairn.recall_selector_call c
 JOIN cairn.recall_observation o USING(observation_id)
 WHERE o.repo=$1 AND o.observed_at>=$2 AND ($3='' OR o.harness=$3) AND ($4='' OR o.native_session_id=$4)
 GROUP BY 1,2`, req.Repo, out.Since, req.Harness, req.NativeSessionID)
	if err != nil {
		return out, err
	}
	for sRows.Next() {
		var day, harness string
		var reasoning, partial *int64
		if err := sRows.Scan(&day, &harness, &reasoning, &partial); err != nil {
			sRows.Close()
			return out, err
		}
		if idx, ok := recallIdx[recallDayHarness{day, harness}]; ok {
			out.RecallCounters[idx].SelectorReasoningTokens = reasoning
			out.RecallCounters[idx].SelectorPartialUsage = partial
		}
	}
	if err := sRows.Err(); err != nil {
		sRows.Close()
		return out, err
	}
	sRows.Close()

	dRows, err := tx.Query(ctx, `SELECT
 (observed_at AT TIME ZONE 'UTC')::date::text,
 harness,
 count(*)::int,
 count(*) FILTER (WHERE disposition='full')::int,
 count(*) FILTER (WHERE disposition='compact')::int,
 count(*) FILTER (WHERE disposition='digest')::int,
 count(*) FILTER (WHERE disposition='auto_settled')::int,
 count(*) FILTER (WHERE disposition='superseded')::int,
 COALESCE(sum(superseded_count),0)::bigint,
 `+recallMetricSQL("injected_bytes")+`,
 COALESCE(array_agg(DISTINCT injected_unit) FILTER (WHERE injected_unit IS NOT NULL), ARRAY[]::text[])
 FROM cairn.delivery_observation
 WHERE repo=$1 AND observed_at>=$2 AND ($3='' OR harness=$3) AND ($4='' OR native_session_id=$4)
 GROUP BY 1,2 ORDER BY 1,2`, req.Repo, out.Since, req.Harness, req.NativeSessionID)
	if err != nil {
		return out, err
	}
	for dRows.Next() {
		var row TelemetryDeliveryCounters
		if err := dRows.Scan(&row.Day, &row.Harness, &row.Observations,
			&row.Full, &row.Compact, &row.Digest, &row.AutoSettled, &row.Superseded,
			&row.SupersededCountTotal,
			&row.InjectedBytes.Known, &row.InjectedBytes.Unknown, &row.InjectedBytes.Median, &row.InjectedBytes.P95, &row.InjectedBytes.Total,
			&row.InjectedUnits); err != nil {
			dRows.Close()
			return out, err
		}
		out.DeliveryCounters = append(out.DeliveryCounters, row)
	}
	if err := dRows.Err(); err != nil {
		dRows.Close()
		return out, err
	}
	dRows.Close()

	// Per-message window growth: compute honest per-step deltas across ordered
	// capture observations (dialogue_bytes / dialogue_messages and reported tokens)
	// plus injected_bytes per prompt from recall observations.
	err = tx.QueryRow(ctx, `
WITH ordered_capture AS (
 SELECT
  session_sha256,
  dialogue_bytes,
  dialogue_messages,
  CASE WHEN input_tokens IS NOT NULL OR cache_read_input_tokens IS NOT NULL OR cache_creation_input_tokens IS NOT NULL
   THEN COALESCE(input_tokens,0)+COALESCE(cache_read_input_tokens,0)+COALESCE(cache_creation_input_tokens,0)
   ELSE NULL END AS prompt_tokens,
  lag(dialogue_bytes) OVER (PARTITION BY session_sha256 ORDER BY sequence) AS prev_bytes,
  lag(dialogue_messages) OVER (PARTITION BY session_sha256 ORDER BY sequence) AS prev_messages,
  lag(CASE WHEN input_tokens IS NOT NULL OR cache_read_input_tokens IS NOT NULL OR cache_creation_input_tokens IS NOT NULL
   THEN COALESCE(input_tokens,0)+COALESCE(cache_read_input_tokens,0)+COALESCE(cache_creation_input_tokens,0)
   ELSE NULL END) OVER (PARTITION BY session_sha256 ORDER BY sequence) AS prev_tokens
 FROM cairn.capture_observation
 WHERE repo=$1 AND observed_at>=$2 AND ($3='' OR harness=$3) AND ($4='' OR native_session_id=$4)
),
byte_steps AS (
 SELECT
  session_sha256,
  ((dialogue_bytes - prev_bytes)::double precision / NULLIF(dialogue_messages - prev_messages, 0)) AS bytes_per_msg
 FROM ordered_capture
 WHERE dialogue_bytes IS NOT NULL AND prev_bytes IS NOT NULL
   AND dialogue_messages IS NOT NULL AND prev_messages IS NOT NULL
   AND dialogue_messages > prev_messages AND dialogue_bytes >= prev_bytes
),
token_steps AS (
 SELECT
  ((prompt_tokens - prev_tokens)::double precision / GREATEST(1, COALESCE(dialogue_messages - prev_messages, 1))) AS tokens_per_step
 FROM ordered_capture
 WHERE prompt_tokens IS NOT NULL AND prev_tokens IS NOT NULL AND prompt_tokens >= prev_tokens
),
prompt_injects AS (
 SELECT injected_bytes, native_session_id
 FROM cairn.recall_observation
 WHERE repo=$1 AND observed_at>=$2 AND ($3='' OR harness=$3) AND ($4='' OR native_session_id=$4)
   AND injected_bytes IS NOT NULL
)
SELECT
 (SELECT count(DISTINCT session_sha256)::int FROM ordered_capture),
 (SELECT count(*)::int FROM ordered_capture),
 (SELECT avg(bytes_per_msg) FROM byte_steps),
 (SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY round(bytes_per_msg)::bigint) FROM byte_steps),
 (SELECT avg(injected_bytes::double precision) FROM prompt_injects),
 (SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY injected_bytes) FROM prompt_injects),
 (SELECT avg(tokens_per_step) FROM token_steps),
 (SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY round(tokens_per_step)::bigint) FROM token_steps)`,
		req.Repo, out.Since, req.Harness, req.NativeSessionID).Scan(
		&out.PerMessageWindowGrowth.SessionsMeasured,
		&out.PerMessageWindowGrowth.ObservationsMeasured,
		&out.PerMessageWindowGrowth.MeanDialogueBytesPerStep,
		&out.PerMessageWindowGrowth.MedianDialogueBytesPerStep,
		&out.PerMessageWindowGrowth.MeanInjectedBytesPerPrompt,
		&out.PerMessageWindowGrowth.MedianInjectedBytesPerPrompt,
		&out.PerMessageWindowGrowth.MeanPromptTokensPerStep,
		&out.PerMessageWindowGrowth.MedianPromptTokensPerStep,
	)
	if err != nil {
		return out, err
	}

	// Session longevity before compaction: combine agent_session compaction
	// timestamps with capture_observation PreCompact timing and pre-compaction turns.
	err = tx.QueryRow(ctx, `
WITH session_stats AS (
 SELECT
  a.agent_id,
  a.compaction_count,
  CASE
   WHEN a.first_compacted_at IS NOT NULL THEN GREATEST(0, floor(extract(epoch FROM (a.first_compacted_at - a.started_at)))::bigint)
   WHEN pc.first_precompact_at IS NOT NULL THEN GREATEST(0, floor(extract(epoch FROM (pc.first_precompact_at - a.started_at)))::bigint)
   ELSE NULL
  END AS seconds_before_compaction,
  CASE
   WHEN a.compaction_count = 0 AND pc.first_precompact_at IS NULL
   THEN GREATEST(0, floor(extract(epoch FROM (a.last_seen - a.started_at)))::bigint)
   ELSE NULL
  END AS uncompacted_seconds,
  CASE
   WHEN a.first_compacted_at IS NOT NULL OR pc.first_precompact_at IS NOT NULL
   THEN COALESCE((
    SELECT count(*)::bigint FROM cairn.recall_observation r
    WHERE r.repo=a.repo AND r.native_session_id=a.native_session_id
      AND r.hook_event='UserPromptSubmit'
      AND r.observed_at <= COALESCE(a.first_compacted_at, pc.first_precompact_at)
   ), 0)
   ELSE NULL
  END AS turns_before_compaction
 FROM cairn.agent_session a
 LEFT JOIN LATERAL (
  SELECT min(c.observed_at) AS first_precompact_at
  FROM cairn.capture_observation c
  WHERE c.repo=a.repo AND c.native_session_id=a.native_session_id AND c.hook_event='PreCompact'
 ) pc ON true
 WHERE a.repo=$1 AND a.started_at>=$2
   AND ($3='' OR lower(a.metadata->>'harness')=$3)
   AND ($4='' OR a.native_session_id=$4)
)
SELECT
 count(*)::int,
 count(*) FILTER (WHERE compaction_count > 0 OR seconds_before_compaction IS NOT NULL)::int,
 count(*) FILTER (WHERE compaction_count = 0 AND seconds_before_compaction IS NULL)::int,
 percentile_disc(0.5) WITHIN GROUP (ORDER BY seconds_before_compaction),
 percentile_disc(0.95) WITHIN GROUP (ORDER BY seconds_before_compaction),
 percentile_disc(0.5) WITHIN GROUP (ORDER BY turns_before_compaction),
 percentile_disc(0.95) WITHIN GROUP (ORDER BY turns_before_compaction),
 percentile_disc(0.5) WITHIN GROUP (ORDER BY uncompacted_seconds)
FROM session_stats`,
		req.Repo, out.Since, req.Harness, req.NativeSessionID).Scan(
		&out.SessionLongevity.SessionsObserved,
		&out.SessionLongevity.CompactedSessions,
		&out.SessionLongevity.UncompactedSessions,
		&out.SessionLongevity.MedianSecondsBeforeFirstCompaction,
		&out.SessionLongevity.P95SecondsBeforeFirstCompaction,
		&out.SessionLongevity.MedianTurnsBeforeFirstCompaction,
		&out.SessionLongevity.P95TurnsBeforeFirstCompaction,
		&out.SessionLongevity.MedianUncompactedDurationSeconds,
	)
	if err != nil {
		return out, err
	}
	return out, tx.Commit(ctx)
}
