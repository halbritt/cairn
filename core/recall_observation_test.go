package core

import (
	"context"
	"encoding/json"
	"math"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func i64(value int64) *int64 { return &value }
func asJSON(value any) string {
	encoded, _ := json.Marshal(value)
	return string(encoded)
}
func intp(value int) *int { return &value }
func f64(value float64) *float64 {
	return &value
}

// recallReceipts creates one retrieval receipt per run name in the repository.
func recallReceipts(t *testing.T, s *Store, repo string, runs ...string) []string {
	t.Helper()
	ids := []string{}
	for _, run := range runs {
		p, err := s.Compile(context.Background(), CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", run}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
		if err != nil {
			t.Fatal(err)
		}
		ids = append(ids, p.ReceiptID)
	}
	return ids
}

func recallBase(repo string, receipts ...string) RecallObservationRequest {
	return RecallObservationRequest{RequestID: uuid.NewString(), Repo: repo, ReceiptIDs: receipts, Harness: "claude",
		HookEvent: "UserPromptSubmit", Method: "cairn-lifecycle/recall-meter/1", Status: "completed"}
}

func recallRow(t *testing.T, report UseReport, receipt string) UseRow {
	t.Helper()
	for _, row := range report.Rows {
		if row.ReceiptID == receipt {
			return row
		}
	}
	t.Fatalf("no row for receipt %s in %+v", receipt, report.Rows)
	return UseRow{}
}

func recallRollup(t *testing.T, report UseReport, harness string) RecallRollup {
	t.Helper()
	for _, row := range report.RecallRollups.Rows {
		if row.Harness == harness {
			return row
		}
	}
	t.Fatalf("no %s rollup in %+v", harness, report.RecallRollups)
	return RecallRollup{}
}

func TestRecallObservationAppearsPerReceiptAndStaysDistinctFromUse(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "recall-observation"})
	repo := uuid.NewString()
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)}); err != nil {
		t.Fatal(err)
	}
	receipts := recallReceipts(t, s, repo, "observed-a", "observed-b", "unobserved")
	before, err := s.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 100})
	if err != nil || len(before.Rows) != 3 {
		t.Fatalf("report: %+v %v", before, err)
	}
	req := recallBase(repo, receipts[0], receipts[1])
	req.ElapsedMS, req.SearchMS, req.SelectorMS, req.PullsMS = i64(1840), i64(210), i64(1390), i64(120)
	req.SearchCalls, req.PullCalls = intp(2), intp(1)
	req.SelectorCalls = []RecallSelectorCall{
		{Stage: "preview", Outcome: "completed", ElapsedMS: i64(400), Model: "model-p", ModelSource: "requested"},
		{Stage: "recall", Outcome: "completed", ElapsedMS: i64(990), Model: "model-x", ModelSource: "reported",
			InputTokens: i64(2100), OutputTokens: i64(40), CacheReadInputTokens: i64(900), CostUSD: f64(0.0121)},
	}
	req.InjectedBytes = i64(3300)
	first, err := s.RecordRecallObservation(ctx, req)
	if err != nil || first.ID == "" {
		t.Fatalf("record: %+v %v", first, err)
	}
	replay, err := s.RecordRecallObservation(ctx, req)
	if err != nil || replay.ID != first.ID {
		t.Fatalf("an exact retry must return the original observation: %+v %v", replay, err)
	}
	changed := req
	changed.Status = "error"
	_, err = s.RecordRecallObservation(ctx, changed)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")

	report, err := s.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 100})
	if err != nil || len(report.Rows) != len(before.Rows) {
		t.Fatalf("an observation must not add or multiply exposure rows: %+v %v", report, err)
	}
	for _, receipt := range receipts[:2] {
		row := recallRow(t, report, receipt)
		got := row.Recall
		if got == nil || got.ObservationID != first.ID || got.Receipts != 2 || got.Harness != "claude" || got.Status != "completed" ||
			got.ElapsedMS == nil || *got.ElapsedMS != 1840 || got.SearchMS == nil || *got.SearchMS != 210 ||
			got.SelectorMS == nil || *got.SelectorMS != 1390 || got.PullsMS == nil || *got.PullsMS != 120 ||
			got.SearchCalls == nil || *got.SearchCalls != 2 || got.PullCalls == nil || *got.PullCalls != 1 ||
			got.SelectorCostUSD == nil || math.Abs(*got.SelectorCostUSD-0.0121) > 1e-9 ||
			got.SelectorCallsWithoutCost == nil || *got.SelectorCallsWithoutCost != 1 ||
			got.InjectedBytes == nil || *got.InjectedBytes != 3300 {
			t.Fatalf("recall observation not reported exactly: %+v", got)
		}
		if got.SelectorCalls == nil || len(*got.SelectorCalls) != 2 {
			t.Fatalf("selector calls lost: %+v", got.SelectorCalls)
		}
		preview, recall := (*got.SelectorCalls)[0], (*got.SelectorCalls)[1]
		if preview.Ordinal != 1 || preview.Stage != "preview" || preview.Model == nil || *preview.Model != "model-p" || *preview.ModelSource != "requested" ||
			preview.InputTokens != nil || preview.OutputTokens != nil || preview.CostUSD != nil || preview.ElapsedMS == nil || *preview.ElapsedMS != 400 {
			t.Fatalf("a call that reported no usage or cost gained some: %+v", preview)
		}
		if recall.Ordinal != 2 || recall.Stage != "recall" || *recall.Model != "model-x" || *recall.ModelSource != "reported" ||
			recall.InputTokens == nil || *recall.InputTokens != 2100 || recall.OutputTokens == nil || *recall.OutputTokens != 40 ||
			recall.CacheReadInputTokens == nil || *recall.CacheReadInputTokens != 900 || recall.CacheCreationInputTokens != nil ||
			recall.CostUSD == nil || math.Abs(*recall.CostUSD-0.0121) > 1e-9 {
			t.Fatalf("selector call not reported exactly: %+v", recall)
		}
		// Delivery, usage and outcome are separate streams that no latency observation fills in.
		if row.Delivery != "unknown" || row.Usage != "unknown" || row.TaskOutcome != "unknown" || row.DurationMS != nil {
			t.Fatalf("latency observation implied delivery, use or outcome: %+v", row)
		}
	}
	if row := recallRow(t, report, receipts[2]); row.Recall != nil {
		t.Fatalf("an unobserved receipt must stay unknown, not become a recall: %+v", row.Recall)
	}
	rollup := recallRollup(t, report, "claude")
	if rollup.Observations != 1 || rollup.ElapsedMS.Known != 1 || rollup.Selector.CostUSD == nil || math.Abs(*rollup.Selector.CostUSD-0.0121) > 1e-9 ||
		rollup.Selector.Calls == nil || *rollup.Selector.Calls != 2 || *rollup.Selector.CallsWithoutCost != 1 {
		t.Fatalf("two receipts of one invocation must be counted once: %+v", rollup)
	}

	// The latest observation of a receipt is the one the row shows.
	later := recallBase(repo, receipts[0])
	later.Status, later.ErrorClass = "timeout", "HookError"
	second, err := s.RecordRecallObservation(ctx, later)
	if err != nil {
		t.Fatal(err)
	}
	report, err = s.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 100})
	if err != nil {
		t.Fatal(err)
	}
	if got := recallRow(t, report, receipts[0]).Recall; got == nil || got.ObservationID != second.ID || got.Status != "timeout" || got.ErrorClass == nil || *got.ErrorClass != "HookError" || got.ElapsedMS != nil ||
		got.SelectorCalls != nil || got.SelectorCostUSD != nil || got.SelectorCallsWithoutCost != nil {
		t.Fatalf("latest observation not shown: %+v", got)
	}
	if got := recallRow(t, report, receipts[1]).Recall; got == nil || got.ObservationID != first.ID {
		t.Fatalf("an observation of another receipt changed this row: %+v", got)
	}
}

func TestRecallRollupsKeepUnknownApartFromZeroAndCountReportedCostOnly(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "recall-rollup"})
	repo := uuid.NewString()
	record := func(mutate func(*RecallObservationRequest)) string {
		t.Helper()
		req := recallBase(repo)
		mutate(&req)
		observed, err := s.RecordRecallObservation(ctx, req)
		if err != nil {
			t.Fatal(err)
		}
		return observed.ID
	}
	for _, elapsed := range []int64{100, 200, 300, 400, 1000} {
		record(func(r *RecallObservationRequest) { r.ElapsedMS = i64(elapsed) })
	}
	// Two more reported nothing about latency: unknown, not zero.
	record(func(r *RecallObservationRequest) {})
	record(func(r *RecallObservationRequest) { r.Status, r.ErrorClass = "error", "OSError" })
	report, err := s.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 1})
	if err != nil {
		t.Fatal(err)
	}
	claude := recallRollup(t, report, "claude")
	elapsed := claude.ElapsedMS
	if claude.Observations != 7 || claude.Completed != 6 || claude.Errors != 1 || claude.Timeouts != 0 ||
		elapsed.Known != 5 || elapsed.Unknown != 2 || elapsed.Median == nil || *elapsed.Median != 300 ||
		elapsed.P95 == nil || *elapsed.P95 != 1000 || elapsed.Total == nil || *elapsed.Total != 2000 {
		t.Fatalf("latency rollup counted unknown as zero or lost a value: %+v", claude)
	}
	// Phases nobody reported have no median, p95 or total.
	if search := claude.SearchMS; search.Known != 0 || search.Unknown != 7 || search.Median != nil || search.P95 != nil || search.Total != nil {
		t.Fatalf("unreported phase became a number: %+v", search)
	}
	if sel := claude.Selector; sel.ObservationsReporting != 0 || sel.ObservationsNotReporting != 7 || sel.Calls != nil || sel.Timeouts != nil || sel.CostUSD != nil ||
		sel.CallsWithCost != nil || sel.CallsWithoutCost != nil || sel.InputTokens != nil || len(sel.Models) != 0 {
		t.Fatalf("selector work was invented from nothing: %+v", sel)
	}

	// An observed zero is a value: it joins the median and the known counts.
	record(func(r *RecallObservationRequest) {
		r.Harness, r.ElapsedMS, r.InjectedBytes = "opencode", i64(0), i64(0)
	})
	// Selector observations: calls with and without reported cost or usage, none at all, and unreported.
	record(func(r *RecallObservationRequest) {
		r.Harness = "codex"
		r.SelectorCalls = []RecallSelectorCall{{Stage: "recall", Outcome: "completed", Model: "model-a", ModelSource: "requested",
			InputTokens: i64(1000), OutputTokens: i64(10), CostUSD: f64(0.02)}}
	})
	record(func(r *RecallObservationRequest) {
		r.Harness = "codex"
		r.SelectorCalls = []RecallSelectorCall{
			{Stage: "preview", Outcome: "timeout", Model: "model-b", ModelSource: "reported"},
			{Stage: "recall", Outcome: "completed", Model: "model-b", ModelSource: "reported", InputTokens: i64(500), CostUSD: f64(0.01)}}
	})
	record(func(r *RecallObservationRequest) {
		r.Harness, r.SelectorCalls, r.PullsMS = "codex", []RecallSelectorCall{}, i64(0)
	})
	record(func(r *RecallObservationRequest) {
		r.Harness, r.SelectorCalls = "codex", []RecallSelectorCall{{Stage: "recall", Outcome: "error"}}
	})
	record(func(r *RecallObservationRequest) { r.Harness = "codex" })
	report, err = s.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 1})
	if err != nil {
		t.Fatal(err)
	}
	opencode := recallRollup(t, report, "opencode")
	if opencode.ElapsedMS.Known != 1 || opencode.ElapsedMS.Median == nil || *opencode.ElapsedMS.Median != 0 || opencode.ElapsedMS.Total == nil || *opencode.ElapsedMS.Total != 0 ||
		opencode.InjectedBytes.Known != 1 || *opencode.InjectedBytes.Total != 0 {
		t.Fatalf("an observed zero was lost or treated as unknown: %+v", opencode)
	}
	sel := recallRollup(t, report, "codex").Selector
	if sel.ObservationsReporting != 4 || sel.ObservationsNotReporting != 1 || sel.ObservationsWithCalls != 3 || sel.Calls == nil || *sel.Calls != 4 ||
		*sel.Timeouts != 1 || *sel.Errors != 1 || *sel.CallsWithoutModel != 1 ||
		*sel.CallsWithUsage != 2 || *sel.CallsWithoutUsage != 2 ||
		sel.InputTokens == nil || *sel.InputTokens != 1500 || sel.OutputTokens == nil || *sel.OutputTokens != 10 ||
		sel.CacheCreationInputTokens != nil || sel.CacheReadInputTokens != nil ||
		*sel.CallsWithCost != 2 || *sel.CallsWithoutCost != 2 || sel.CostUSD == nil || math.Abs(*sel.CostUSD-0.03) > 1e-9 {
		t.Fatalf("selector rollup: %s", asJSON(sel))
	}
	if len(sel.Models) != 2 || sel.Models[0].Model != "model-a" || sel.Models[0].Calls != 1 || sel.Models[0].CallsWithCost != 1 || sel.Models[0].CostUSD == nil || math.Abs(*sel.Models[0].CostUSD-0.02) > 1e-9 ||
		sel.Models[1].Model != "model-b" || sel.Models[1].Calls != 2 || sel.Models[1].CallsWithCost != 1 || sel.Models[1].CostUSD == nil || math.Abs(*sel.Models[1].CostUSD-0.01) > 1e-9 {
		t.Fatalf("per-model rollup: %+v", sel.Models)
	}
	if len(report.Rows) != 0 || report.RecallRollups.WindowDays != 7 {
		t.Fatalf("rollups must not depend on the exposure page: %+v", report)
	}
}

func TestRecallRollupsAreDailyPerHarnessAndBounded(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "recall-days"})
	repo := uuid.NewString()
	for _, days := range []int{0, 3, 10} {
		req := recallBase(repo)
		req.ElapsedMS = i64(100)
		observed, err := s.RecordRecallObservation(ctx, req)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = s.pool.Exec(ctx, `UPDATE cairn.recall_observation SET observed_at=observed_at - make_interval(days => $2) WHERE observation_id=$1`, observed.ID, days); err != nil {
			t.Fatal(err)
		}
	}
	other := recallBase(uuid.NewString())
	if _, err := s.RecordRecallObservation(ctx, other); err != nil {
		t.Fatal(err)
	}
	report, err := s.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 1})
	if err != nil {
		t.Fatal(err)
	}
	days := map[string]bool{}
	for _, row := range report.RecallRollups.Rows {
		days[row.Day] = true
		if row.Observations != 1 || row.Harness != "claude" {
			t.Fatalf("each day and harness is its own rollup: %+v", row)
		}
	}
	if len(days) != 2 {
		t.Fatalf("the default window is seven days and one repository: %+v", report.RecallRollups)
	}
	wide, err := s.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 1, RollupDays: 30})
	if err != nil || len(wide.RecallRollups.Rows) != 3 || wide.RecallRollups.WindowDays != 30 {
		t.Fatalf("wide window: %+v %v", wide.RecallRollups, err)
	}
	narrow, err := s.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 1, RollupDays: 1})
	if err != nil || len(narrow.RecallRollups.Rows) != 1 {
		t.Fatalf("one day is today only: %+v %v", narrow.RecallRollups, err)
	}
	for _, bad := range []int{-1, MaxRecallRollupDays + 1} {
		_, err = s.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 1, RollupDays: bad})
		requireCode(t, err, "INVALID_REQUEST")
	}
	empty, err := s.UseReport(ctx, UseReportRequest{Repo: uuid.NewString(), Limit: 1})
	if err != nil || empty.RecallRollups.Rows == nil || len(empty.RecallRollups.Rows) != 0 {
		t.Fatalf("no observation means no rollup rows, not zero rows of data: %+v %v", empty.RecallRollups, err)
	}
}

func TestRecallObservationKeepsReceiptRepositoryAndCallerBoundaries(t *testing.T) {
	ctx := context.Background()
	owner := testStore(t, Channel{Principal: "recall-owner"})
	other := testStore(t, Channel{Principal: "recall-other"})
	repo := uuid.NewString()
	if _, err := owner.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)}); err != nil {
		t.Fatal(err)
	}
	mine := recallReceipts(t, owner, repo, "mine")[0]
	elsewhere := uuid.NewString()
	if _, err := owner.Create(ctx, CreateRequest{uuid.NewString(), projectNote(elsewhere)}); err != nil {
		t.Fatal(err)
	}
	foreign := recallReceipts(t, owner, elsewhere, "foreign")[0]

	_, err := other.RecordRecallObservation(ctx, recallBase(repo, mine))
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = owner.RecordRecallObservation(ctx, recallBase(repo, foreign))
	requireCode(t, err, "INVALID_REQUEST")
	_, err = owner.RecordRecallObservation(ctx, recallBase(repo, uuid.NewString()))
	requireCode(t, err, "NOT_FOUND")
	scoped := testStore(t, Channel{Principal: "recall-owner", Repo: elsewhere})
	_, err = scoped.RecordRecallObservation(ctx, recallBase(repo))
	requireCode(t, err, "AUTHORITY_DENIED")

	report, err := owner.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 10})
	if err != nil || len(report.RecallRollups.Rows) != 0 {
		t.Fatalf("a refused observation left something behind: %+v %v", report.RecallRollups, err)
	}
	var count int
	if err = owner.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.recall_observation WHERE repo=ANY($1)`, []string{repo, elsewhere}).Scan(&count); err != nil || count != 0 {
		t.Fatalf("refused observations were stored: %d %v", count, err)
	}
	// A repository-scoped channel can still report for its own repository.
	if _, err = scoped.RecordRecallObservation(ctx, recallBase(elsewhere, foreign)); err != nil {
		t.Fatal(err)
	}
}

func TestRecallObservationRejectsInvalidAndUnboundedValues(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "recall-invalid"})
	repo := uuid.NewString()
	receipt := recallReceipts(t, s, repo, "valid")[0]
	nan := math.NaN()
	call := func() RecallSelectorCall { return RecallSelectorCall{Stage: "recall", Outcome: "completed"} }
	cases := map[string]func(*RecallObservationRequest){
		"repo":              func(r *RecallObservationRequest) { r.Repo = "" },
		"long repo":         func(r *RecallObservationRequest) { r.Repo = strings.Repeat("r", 1025) },
		"harness":           func(r *RecallObservationRequest) { r.Harness = "other" },
		"hook event":        func(r *RecallObservationRequest) { r.HookEvent = "Stop" },
		"status":            func(r *RecallObservationRequest) { r.Status = "ok" },
		"method":            func(r *RecallObservationRequest) { r.Method = " " },
		"error class on ok": func(r *RecallObservationRequest) { r.ErrorClass = "HookError" },
		"error class text":  func(r *RecallObservationRequest) { r.Status, r.ErrorClass = "error", "failed: /secret/path" },
		"negative elapsed":  func(r *RecallObservationRequest) { r.ElapsedMS = i64(-1) },
		"huge phase":        func(r *RecallObservationRequest) { r.SearchMS = i64(maxRecallMilliseconds + 1) },
		"negative bytes":    func(r *RecallObservationRequest) { r.InjectedBytes = i64(-1) },
		"huge bytes":        func(r *RecallObservationRequest) { r.InjectedBytes = i64(maxRecallInjected + 1) },
		"call count":        func(r *RecallObservationRequest) { r.PullCalls = intp(maxRecallCalls + 1) },
		"too many selectors": func(r *RecallObservationRequest) {
			r.SelectorCalls = make([]RecallSelectorCall, maxRecallSelectors+1)
			for i := range r.SelectorCalls {
				r.SelectorCalls[i] = call()
			}
		},
		"stage": func(r *RecallObservationRequest) {
			c := call()
			c.Stage = "capture"
			r.SelectorCalls = []RecallSelectorCall{c}
		},
		"outcome": func(r *RecallObservationRequest) {
			c := call()
			c.Outcome = "ok"
			r.SelectorCalls = []RecallSelectorCall{c}
		},
		"call elapsed": func(r *RecallObservationRequest) {
			c := call()
			c.ElapsedMS = i64(-1)
			r.SelectorCalls = []RecallSelectorCall{c}
		},
		"model unsourced": func(r *RecallObservationRequest) {
			c := call()
			c.Model = "model"
			r.SelectorCalls = []RecallSelectorCall{c}
		},
		"source only": func(r *RecallObservationRequest) {
			c := call()
			c.ModelSource = "reported"
			r.SelectorCalls = []RecallSelectorCall{c}
		},
		"model whitespace": func(r *RecallObservationRequest) {
			c := call()
			c.Model, c.ModelSource = "two words", "reported"
			r.SelectorCalls = []RecallSelectorCall{c}
		},
		"model source": func(r *RecallObservationRequest) {
			c := call()
			c.Model, c.ModelSource = "model", "guessed"
			r.SelectorCalls = []RecallSelectorCall{c}
		},
		"negative tokens": func(r *RecallObservationRequest) {
			c := call()
			c.InputTokens = i64(-5)
			r.SelectorCalls = []RecallSelectorCall{c}
		},
		"huge tokens": func(r *RecallObservationRequest) {
			c := call()
			c.CacheReadInputTokens = i64(maxRecallTokens + 1)
			r.SelectorCalls = []RecallSelectorCall{c}
		},
		"negative cost": func(r *RecallObservationRequest) {
			c := call()
			c.CostUSD = f64(-0.01)
			r.SelectorCalls = []RecallSelectorCall{c}
		},
		"absurd cost": func(r *RecallObservationRequest) {
			c := call()
			c.CostUSD = f64(1000)
			r.SelectorCalls = []RecallSelectorCall{c}
		},
		"nan cost": func(r *RecallObservationRequest) {
			c := call()
			c.CostUSD = &nan
			r.SelectorCalls = []RecallSelectorCall{c}
		},
		"duplicate receipt":  func(r *RecallObservationRequest) { r.ReceiptIDs = []string{receipt, receipt} },
		"receipt not a uuid": func(r *RecallObservationRequest) { r.ReceiptIDs = []string{"receipt"} },
		"too many receipts": func(r *RecallObservationRequest) {
			r.ReceiptIDs = nil
			for range maxRecallReceipts + 1 {
				r.ReceiptIDs = append(r.ReceiptIDs, uuid.NewString())
			}
		},
		"request id": func(r *RecallObservationRequest) { r.RequestID = "not-a-uuid" },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			req := recallBase(repo, receipt)
			mutate(&req)
			_, err := s.RecordRecallObservation(ctx, req)
			requireCode(t, err, "INVALID_REQUEST")
		})
	}
	var count int
	if err := s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.recall_observation WHERE repo=$1`, repo).Scan(&count); err != nil || count != 0 {
		t.Fatalf("an invalid observation was stored: %d %v", count, err)
	}
	// The valid minimum is just the identity and status: everything else may stay unknown.
	if _, err := s.RecordRecallObservation(ctx, recallBase(repo, receipt)); err != nil {
		t.Fatal(err)
	}
}

// The schema refuses what the request validation also refuses, so a direct write cannot store a contradiction.
func TestRecallObservationSchemaRefusesContradictoryRows(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "recall-schema"})
	repo := uuid.NewString()
	observed, err := s.RecordRecallObservation(ctx, recallBase(repo))
	if err != nil {
		t.Fatal(err)
	}
	for name, statement := range map[string]string{
		"model without source": `INSERT INTO cairn.recall_selector_call(observation_id,ordinal,stage,outcome,model) VALUES($1,1,'recall','completed','m')`,
		"negative tokens":      `INSERT INTO cairn.recall_selector_call(observation_id,ordinal,stage,outcome,input_tokens) VALUES($1,1,'recall','completed',-1)`,
		"negative cost":        `INSERT INTO cairn.recall_selector_call(observation_id,ordinal,stage,outcome,cost_usd) VALUES($1,1,'recall','completed',-0.01)`,
		"unknown stage":        `INSERT INTO cairn.recall_selector_call(observation_id,ordinal,stage,outcome) VALUES($1,1,'capture','completed')`,
		"ninth call":           `INSERT INTO cairn.recall_selector_call(observation_id,ordinal,stage,outcome) VALUES($1,9,'recall','completed')`,
	} {
		if _, err := s.pool.Exec(ctx, statement, observed.ID); err == nil {
			t.Fatalf("%s was stored", name)
		}
	}
	if _, err := s.pool.Exec(ctx, `UPDATE cairn.recall_observation SET status='completed',error_class='HookError' WHERE observation_id=$1`, observed.ID); err == nil {
		t.Fatal("an error class was stored on a completed recall")
	}
}
