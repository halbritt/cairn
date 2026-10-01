package core

import (
	"context"
	"errors"
	"math"
	"regexp"
	"strings"
	"unicode"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// RecallSelectorCall is one model call the hook made while choosing context.
// Omitted values are unknown. Cost is only what the provider reported.
type RecallSelectorCall struct {
	Stage                    string   `json:"stage"`
	Outcome                  string   `json:"outcome"`
	ElapsedMS                *int64   `json:"elapsed_ms,omitempty"`
	Model                    string   `json:"model,omitempty"`
	ModelSource              string   `json:"model_source,omitempty"`
	InputTokens              *int64   `json:"input_tokens,omitempty"`
	OutputTokens             *int64   `json:"output_tokens,omitempty"`
	CacheCreationInputTokens *int64   `json:"cache_creation_input_tokens,omitempty"`
	CacheReadInputTokens     *int64   `json:"cache_read_input_tokens,omitempty"`
	CostUSD                  *float64 `json:"cost_usd,omitempty"`
}

// RecallObservationRequest is one lifecycle recall hook invocation as the hook
// reports it. Every measurement is optional: an omitted value is unknown, and
// zero means the hook observed zero. Nothing here carries prompt, query, note
// or selector text. SelectorCalls is null when the hook did not report its
// selector work and an empty list when it observed none.
type RecallObservationRequest struct {
	RequestID  string   `json:"request_id"`
	Repo       string   `json:"repo"`
	ReceiptIDs []string `json:"receipt_ids,omitempty"`
	Harness    string   `json:"harness"`
	HookEvent  string   `json:"hook_event"`
	Method     string   `json:"method"`
	Status     string   `json:"status"`
	ErrorClass string   `json:"error_class,omitempty"`

	ElapsedMS  *int64 `json:"elapsed_ms,omitempty"`
	SearchMS   *int64 `json:"search_ms,omitempty"`
	SelectorMS *int64 `json:"selector_ms,omitempty"`
	PullsMS    *int64 `json:"pulls_ms,omitempty"`

	SearchCalls   *int                 `json:"search_calls,omitempty"`
	PullCalls     *int                 `json:"pull_calls,omitempty"`
	SelectorCalls []RecallSelectorCall `json:"selector_calls"`

	InjectedBytes *int64 `json:"injected_bytes,omitempty"`
}

const (
	maxRecallMilliseconds = 3600000
	maxRecallCalls        = 1000
	maxRecallSelectors    = 8
	maxRecallReceipts     = 8
	maxRecallInjected     = 1048576
	maxRecallTokens       = 1 << 40
	maxRecallCostUSD      = 1000.0
)

var recallErrorClass = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_]{0,63}$`)

func recallMilliseconds(values ...*int64) bool {
	for _, value := range values {
		if value != nil && (*value < 0 || *value > maxRecallMilliseconds) {
			return false
		}
	}
	return true
}

func validRecallModel(model string) bool {
	if model == "" || len(model) > 256 || model != strings.TrimSpace(model) {
		return false
	}
	for _, char := range model {
		if unicode.IsSpace(char) || unicode.IsControl(char) {
			return false
		}
	}
	return true
}

func (call RecallSelectorCall) validate() error {
	invalid := func(message string) error { return failure("INVALID_REQUEST", message) }
	if call.Stage != "preview" && call.Stage != "recall" {
		return invalid("unknown selector stage")
	}
	if call.Outcome != "completed" && call.Outcome != "timeout" && call.Outcome != "error" {
		return invalid("unknown selector outcome")
	}
	if !recallMilliseconds(call.ElapsedMS) {
		return invalid("selector value out of range")
	}
	for _, tokens := range []*int64{call.InputTokens, call.OutputTokens, call.CacheCreationInputTokens, call.CacheReadInputTokens} {
		if tokens != nil && (*tokens < 0 || *tokens > maxRecallTokens) {
			return invalid("selector token count out of range")
		}
	}
	if (call.Model == "") != (call.ModelSource == "") {
		return invalid("selector model and its source are reported together")
	}
	if call.Model != "" && (!validRecallModel(call.Model) || (call.ModelSource != "reported" && call.ModelSource != "requested")) {
		return invalid("selector model requires a valid name and a reported or requested source")
	}
	if cost := call.CostUSD; cost != nil && (math.IsNaN(*cost) || math.IsInf(*cost, 0) || *cost < 0 || *cost >= maxRecallCostUSD) {
		return invalid("selector cost out of range")
	}
	return nil
}

func (req RecallObservationRequest) validate() error {
	invalid := func(message string) error { return failure("INVALID_REQUEST", message) }
	if req.Repo == "" || len(req.Repo) > 1024 {
		return invalid("recall observation requires a bounded repository")
	}
	switch req.Harness {
	case "claude", "codex", "opencode", "hermes":
	default:
		return invalid("unknown recall harness")
	}
	switch req.HookEvent {
	case "SessionStart", "UserPromptSubmit":
	default:
		return invalid("unknown recall hook event")
	}
	switch req.Status {
	case "completed", "timeout", "error":
	default:
		return invalid("unknown recall status")
	}
	if strings.TrimSpace(req.Method) == "" || len(req.Method) > 256 {
		return invalid("recall observation requires a bounded observer method/version label")
	}
	if req.ErrorClass != "" && (req.Status == "completed" || !recallErrorClass.MatchString(req.ErrorClass)) {
		return invalid("error_class is a bounded identifier allowed only on a timeout or error")
	}
	if !recallMilliseconds(req.ElapsedMS, req.SearchMS, req.SelectorMS, req.PullsMS) {
		return invalid("recall observation value out of range")
	}
	for _, calls := range []*int{req.SearchCalls, req.PullCalls} {
		if calls != nil && (*calls < 0 || *calls > maxRecallCalls) {
			return invalid("recall observation value out of range")
		}
	}
	if req.InjectedBytes != nil && (*req.InjectedBytes < 0 || *req.InjectedBytes > maxRecallInjected) {
		return invalid("recall observation value out of range")
	}
	if len(req.SelectorCalls) > maxRecallSelectors {
		return invalid("too many selector calls")
	}
	for _, call := range req.SelectorCalls {
		if err := call.validate(); err != nil {
			return err
		}
	}
	if len(req.ReceiptIDs) > maxRecallReceipts {
		return invalid("too many recall receipts")
	}
	seen := map[string]bool{}
	for _, id := range req.ReceiptIDs {
		if err := validID(id); err != nil {
			return err
		}
		if seen[id] {
			return invalid("duplicate recall receipt")
		}
		seen[id] = true
	}
	return nil
}

// RecordRecallObservation stores one hook invocation's metrics. It grants no
// authority and changes no retrieval, delivery or usage state. Each named
// receipt must belong to this channel and repository.
func (s *Store) RecordRecallObservation(ctx context.Context, req RecallObservationRequest) (Observation, error) {
	if err := req.validate(); err != nil {
		return Observation{}, err
	}
	if err := s.checkRepo(req.Repo); err != nil {
		return Observation{}, err
	}
	return mutate(ctx, s, "recall-observation", req.RequestID, req, func(tx pgx.Tx) (Observation, error) {
		for _, receipt := range req.ReceiptIDs {
			if err := s.receiptAccess(ctx, tx, receipt); err != nil {
				return Observation{}, err
			}
			var repo string
			if err := tx.QueryRow(ctx, `SELECT scope->>'repo' FROM cairn.retrieval_receipt WHERE receipt_id=$1`, receipt).Scan(&repo); err != nil {
				if errors.Is(err, pgx.ErrNoRows) {
					return Observation{}, failure("NOT_FOUND", "receipt not found")
				}
				return Observation{}, err
			}
			if repo != req.Repo {
				return Observation{}, failure("INVALID_REQUEST", "recall receipt belongs to a different repository")
			}
		}
		var selectorCalls *int
		if req.SelectorCalls != nil {
			count := len(req.SelectorCalls)
			selectorCalls = &count
		}
		id := uuid.NewString()
		_, err := tx.Exec(ctx, `INSERT INTO cairn.recall_observation(observation_id,repo,harness,hook_event,method,status,error_class,
 elapsed_ms,search_ms,selector_ms,pulls_ms,search_calls,pull_calls,selector_calls,injected_bytes)
 VALUES($1,$2,$3,$4,$5,$6,NULLIF($7,''),$8,$9,$10,$11,$12,$13,$14,$15)`,
			id, req.Repo, req.Harness, req.HookEvent, req.Method, req.Status, req.ErrorClass,
			req.ElapsedMS, req.SearchMS, req.SelectorMS, req.PullsMS, req.SearchCalls, req.PullCalls, selectorCalls, req.InjectedBytes)
		if err != nil {
			return Observation{}, err
		}
		for _, receipt := range req.ReceiptIDs {
			if _, err = tx.Exec(ctx, `INSERT INTO cairn.recall_observation_receipt(observation_id,receipt_id) VALUES($1,$2)`, id, receipt); err != nil {
				return Observation{}, err
			}
		}
		for i, call := range req.SelectorCalls {
			if _, err = tx.Exec(ctx, `INSERT INTO cairn.recall_selector_call(observation_id,ordinal,stage,outcome,elapsed_ms,model,model_source,
 input_tokens,output_tokens,cache_creation_input_tokens,cache_read_input_tokens,cost_usd)
 VALUES($1,$2,$3,$4,$5,NULLIF($6,''),NULLIF($7,''),$8,$9,$10,$11,$12)`,
				id, i+1, call.Stage, call.Outcome, call.ElapsedMS, call.Model, call.ModelSource,
				call.InputTokens, call.OutputTokens, call.CacheCreationInputTokens, call.CacheReadInputTokens, call.CostUSD); err != nil {
				return Observation{}, err
			}
		}
		return Observation{id}, nil
	})
}
