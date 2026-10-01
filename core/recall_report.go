package core

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

const (
	DefaultRecallRollupDays = 7
	MaxRecallRollupDays     = 90
)

// RecallSelectorCallView is one selector call as reported. Null is unknown.
type RecallSelectorCallView struct {
	Ordinal                  int      `json:"ordinal"`
	Stage                    string   `json:"stage"`
	Outcome                  string   `json:"outcome"`
	ElapsedMS                *int64   `json:"elapsed_ms"`
	Model                    *string  `json:"model"`
	ModelSource              *string  `json:"model_source"`
	InputTokens              *int64   `json:"input_tokens"`
	OutputTokens             *int64   `json:"output_tokens"`
	CacheCreationInputTokens *int64   `json:"cache_creation_input_tokens"`
	CacheReadInputTokens     *int64   `json:"cache_read_input_tokens"`
	CostUSD                  *float64 `json:"cost_usd"`
}

// RecallObservation is one hook invocation as reported. Null is unknown.
// Zero is a value the hook observed. selector_calls is null when the hook did
// not report its selector work and [] when it observed none.
// selector_cost_usd totals only the costs the provider reported for this
// invocation; selector_calls_without_cost says how many calls it leaves out.
type RecallObservation struct {
	ObservationID string `json:"observation_id"`
	// Receipts is how many of this invocation's receipts the hook reported.
	Receipts   int       `json:"receipts"`
	ObservedAt time.Time `json:"observed_at"`
	Harness    string    `json:"harness"`
	HookEvent  string    `json:"hook_event"`
	Method     string    `json:"method"`
	Status     string    `json:"status"`
	ErrorClass *string   `json:"error_class"`

	ElapsedMS  *int64 `json:"elapsed_ms"`
	SearchMS   *int64 `json:"search_ms"`
	SelectorMS *int64 `json:"selector_ms"`
	PullsMS    *int64 `json:"pulls_ms"`

	SearchCalls *int `json:"search_calls"`
	PullCalls   *int `json:"pull_calls"`

	SelectorCalls            *[]RecallSelectorCallView `json:"selector_calls"`
	SelectorCostUSD          *float64                  `json:"selector_cost_usd"`
	SelectorCallsWithoutCost *int                      `json:"selector_calls_without_cost"`

	InjectedBytes *int64 `json:"injected_bytes"`
}

// RecallMetric summarizes one measurement over the observations that reported
// it. Unknown observations are counted, never treated as zero, and take no part
// in the median, p95 or total. Percentiles are nearest-rank: an observed value.
type RecallMetric struct {
	Known   int    `json:"known"`
	Unknown int    `json:"unknown"`
	Median  *int64 `json:"median"`
	P95     *int64 `json:"p95"`
	Total   *int64 `json:"total"`
}

// RecallModelRollup is the selector work attributed to one model name, which
// is the provider-reported name when the call reported one and otherwise the
// requested name.
type RecallModelRollup struct {
	Model         string   `json:"model"`
	Calls         int64    `json:"calls"`
	CallsWithCost int64    `json:"calls_with_cost"`
	CostUSD       *float64 `json:"cost_usd"`
}

// RecallSelectorRollup describes selector work. Counts and sums cover the
// calls that were reported. cost_usd is the total of provider-reported costs
// only; it is null when no call reported one, and calls_without_cost says how
// many reported calls it leaves out. It is not an estimate of the real total.
// Everything is null when no observation reported its selector work.
type RecallSelectorRollup struct {
	ObservationsReporting    int                 `json:"observations_reporting_calls"`
	ObservationsNotReporting int                 `json:"observations_not_reporting_calls"`
	ObservationsWithCalls    int                 `json:"observations_with_calls"`
	Calls                    *int64              `json:"calls"`
	Timeouts                 *int64              `json:"timeouts"`
	Errors                   *int64              `json:"errors"`
	CallsWithoutModel        *int64              `json:"calls_without_model"`
	Models                   []RecallModelRollup `json:"models"`
	CallsWithUsage           *int64              `json:"calls_with_usage"`
	CallsWithoutUsage        *int64              `json:"calls_without_usage"`
	InputTokens              *int64              `json:"input_tokens"`
	OutputTokens             *int64              `json:"output_tokens"`
	CacheCreationInputTokens *int64              `json:"cache_creation_input_tokens"`
	CacheReadInputTokens     *int64              `json:"cache_read_input_tokens"`
	CallsWithCost            *int64              `json:"calls_with_cost"`
	CallsWithoutCost         *int64              `json:"calls_without_cost"`
	CostUSD                  *float64            `json:"cost_usd"`
}

// RecallRollup is one UTC day for one harness. A harness with no observation
// that day has no row: absence is unknown, not zero.
type RecallRollup struct {
	Day           string               `json:"day"`
	Harness       string               `json:"harness"`
	Observations  int                  `json:"observations"`
	Completed     int                  `json:"completed"`
	Timeouts      int                  `json:"timeouts"`
	Errors        int                  `json:"errors"`
	ElapsedMS     RecallMetric         `json:"elapsed_ms"`
	SearchMS      RecallMetric         `json:"search_ms"`
	SelectorMS    RecallMetric         `json:"selector_ms"`
	PullsMS       RecallMetric         `json:"pulls_ms"`
	InjectedBytes RecallMetric         `json:"injected_bytes"`
	Selector      RecallSelectorRollup `json:"selector"`
}

type RecallRollups struct {
	WindowDays int            `json:"window_days"`
	Since      time.Time      `json:"since"`
	Rows       []RecallRollup `json:"rows"`
}

var recallSummaryColumns = []string{
	"error_class", "elapsed_ms", "search_ms", "selector_ms", "pulls_ms", "search_calls", "pull_calls", "injected_bytes",
}

// recallSummarySQL builds the JSON object of the latest observation, aliased o,
// from the fixed column list so the field names are the column names.
var recallSummarySQL = func() string {
	pairs := []string{
		"'observation_id',o.observation_id",
		"'receipts',(SELECT count(*) FROM cairn.recall_observation_receipt x WHERE x.observation_id=o.observation_id)",
		"'observed_at',o.observed_at", "'harness',o.harness", "'hook_event',o.hook_event", "'method',o.method", "'status',o.status",
	}
	for _, column := range recallSummaryColumns {
		pairs = append(pairs, "'"+column+"',o."+column)
	}
	pairs = append(pairs,
		`'selector_calls',CASE WHEN o.selector_calls IS NULL THEN NULL ELSE (SELECT COALESCE(jsonb_agg(jsonb_build_object('ordinal',c.ordinal,'stage',c.stage,'outcome',c.outcome,'elapsed_ms',c.elapsed_ms,'model',c.model,'model_source',c.model_source,'input_tokens',c.input_tokens,'output_tokens',c.output_tokens,'cache_creation_input_tokens',c.cache_creation_input_tokens,'cache_read_input_tokens',c.cache_read_input_tokens,'cost_usd',c.cost_usd) ORDER BY c.ordinal),'[]'::jsonb) FROM cairn.recall_selector_call c WHERE c.observation_id=o.observation_id) END`,
		`'selector_cost_usd',(SELECT sum(c.cost_usd) FROM cairn.recall_selector_call c WHERE c.observation_id=o.observation_id)`,
		`'selector_calls_without_cost',CASE WHEN o.selector_calls IS NULL THEN NULL ELSE (SELECT count(*) FROM cairn.recall_selector_call c WHERE c.observation_id=o.observation_id AND c.cost_usd IS NULL) END`)
	return "jsonb_build_object(" + strings.Join(pairs, ",") + ")"
}()

var recallMetricColumns = []string{"elapsed_ms", "search_ms", "selector_ms", "pulls_ms", "injected_bytes"}

func recallMetricSQL(column string) string {
	return fmt.Sprintf("count(%[1]s),count(*)-count(%[1]s),percentile_disc(0.5) WITHIN GROUP (ORDER BY %[1]s),percentile_disc(0.95) WITHIN GROUP (ORDER BY %[1]s),sum(%[1]s)::bigint", column)
}

type recallDayHarness struct{ day, harness string }

// recallRollups reads the caller's repository inside the report's snapshot.
// A rollup counts each hook invocation once, however many receipts it produced,
// and includes invocations that failed before any receipt existed.
func recallRollups(ctx context.Context, tx pgx.Tx, repo string, days int) (RecallRollups, error) {
	rollups := RecallRollups{WindowDays: days, Rows: []RecallRollup{}}
	if err := tx.QueryRow(ctx, `SELECT (date_trunc('day', now() AT TIME ZONE 'UTC') - make_interval(days => $1 - 1)) AT TIME ZONE 'UTC'`, days).Scan(&rollups.Since); err != nil {
		return rollups, err
	}
	rollups.Since = rollups.Since.UTC() // the day buckets are UTC; show the window the same way
	metrics := make([]string, 0, len(recallMetricColumns))
	for _, column := range recallMetricColumns {
		metrics = append(metrics, recallMetricSQL(column))
	}
	rows, err := tx.Query(ctx, `SELECT (observed_at AT TIME ZONE 'UTC')::date::text,harness,count(*),
 count(*) FILTER (WHERE status='completed'),count(*) FILTER (WHERE status='timeout'),count(*) FILTER (WHERE status='error'),
 `+strings.Join(metrics, ",\n ")+`,
 count(selector_calls),count(*)-count(selector_calls),count(*) FILTER (WHERE selector_calls>0),sum(selector_calls)::bigint
 FROM cairn.recall_observation WHERE repo=$1 AND observed_at>=$2
 GROUP BY 1,2 ORDER BY 1,2`, repo, rollups.Since)
	if err != nil {
		return rollups, err
	}
	defer rows.Close()
	index := map[recallDayHarness]int{}
	for rows.Next() {
		var row RecallRollup
		metric := []*RecallMetric{&row.ElapsedMS, &row.SearchMS, &row.SelectorMS, &row.PullsMS, &row.InjectedBytes}
		targets := []any{&row.Day, &row.Harness, &row.Observations, &row.Completed, &row.Timeouts, &row.Errors}
		for _, m := range metric {
			targets = append(targets, &m.Known, &m.Unknown, &m.Median, &m.P95, &m.Total)
		}
		s := &row.Selector
		targets = append(targets, &s.ObservationsReporting, &s.ObservationsNotReporting, &s.ObservationsWithCalls, &s.Calls)
		if err = rows.Scan(targets...); err != nil {
			return rollups, err
		}
		s.Models = []RecallModelRollup{}
		index[recallDayHarness{row.Day, row.Harness}] = len(rollups.Rows)
		rollups.Rows = append(rollups.Rows, row)
	}
	if err = rows.Err(); err != nil {
		return rollups, err
	}
	rows.Close()
	// Selector calls, from the observations that reported them. A reporting
	// observation with no call rows observed zero calls, so counts start at zero;
	// token and cost sums stay null until a call reports one.
	calls, err := tx.Query(ctx, `SELECT (o.observed_at AT TIME ZONE 'UTC')::date::text,o.harness,
 count(*) FILTER (WHERE c.outcome='timeout'),count(*) FILTER (WHERE c.outcome='error'),count(*)-count(c.model),
 count(*) FILTER (WHERE c.input_tokens IS NOT NULL OR c.output_tokens IS NOT NULL OR c.cache_creation_input_tokens IS NOT NULL OR c.cache_read_input_tokens IS NOT NULL),
 sum(c.input_tokens)::bigint,sum(c.output_tokens)::bigint,sum(c.cache_creation_input_tokens)::bigint,sum(c.cache_read_input_tokens)::bigint,
 count(c.cost_usd),sum(c.cost_usd)
 FROM cairn.recall_selector_call c JOIN cairn.recall_observation o USING(observation_id)
 WHERE o.repo=$1 AND o.observed_at>=$2 GROUP BY 1,2`, repo, rollups.Since)
	if err != nil {
		return rollups, err
	}
	defer calls.Close()
	for calls.Next() {
		var day, harness string
		var timeouts, errors, withoutModel, withUsage, withCost int64
		var s RecallSelectorRollup
		if err = calls.Scan(&day, &harness, &timeouts, &errors, &withoutModel, &withUsage, &s.InputTokens, &s.OutputTokens, &s.CacheCreationInputTokens, &s.CacheReadInputTokens, &withCost, &s.CostUSD); err != nil {
			return rollups, err
		}
		target := &rollups.Rows[index[recallDayHarness{day, harness}]].Selector
		target.Timeouts, target.Errors, target.CallsWithoutModel = &timeouts, &errors, &withoutModel
		target.CallsWithUsage, target.CallsWithCost = &withUsage, &withCost
		target.InputTokens, target.OutputTokens, target.CacheCreationInputTokens, target.CacheReadInputTokens = s.InputTokens, s.OutputTokens, s.CacheCreationInputTokens, s.CacheReadInputTokens
		target.CostUSD = s.CostUSD
	}
	if err = calls.Err(); err != nil {
		return rollups, err
	}
	calls.Close()
	models, err := tx.Query(ctx, `SELECT (o.observed_at AT TIME ZONE 'UTC')::date::text,o.harness,c.model,count(*),count(c.cost_usd),sum(c.cost_usd)
 FROM cairn.recall_selector_call c JOIN cairn.recall_observation o USING(observation_id)
 WHERE o.repo=$1 AND o.observed_at>=$2 AND c.model IS NOT NULL GROUP BY 1,2,3 ORDER BY 1,2,3`, repo, rollups.Since)
	if err != nil {
		return rollups, err
	}
	defer models.Close()
	for models.Next() {
		var day, harness string
		var model RecallModelRollup
		if err = models.Scan(&day, &harness, &model.Model, &model.Calls, &model.CallsWithCost, &model.CostUSD); err != nil {
			return rollups, err
		}
		target := &rollups.Rows[index[recallDayHarness{day, harness}]].Selector
		target.Models = append(target.Models, model)
	}
	if err = models.Err(); err != nil {
		return rollups, err
	}
	for i := range rollups.Rows {
		s := &rollups.Rows[i].Selector
		if s.ObservationsReporting == 0 {
			// Nothing reported selector work: every selector figure is unknown.
			continue
		}
		zero := func(value **int64) {
			if *value == nil {
				*value = new(int64)
			}
		}
		for _, value := range []**int64{&s.Timeouts, &s.Errors, &s.CallsWithoutModel, &s.CallsWithUsage, &s.CallsWithCost} {
			zero(value)
		}
		s.CallsWithoutUsage = new(int64)
		*s.CallsWithoutUsage = *s.Calls - *s.CallsWithUsage
		s.CallsWithoutCost = new(int64)
		*s.CallsWithoutCost = *s.Calls - *s.CallsWithCost
	}
	return rollups, nil
}
