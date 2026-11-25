package core

import (
	"context"
	"errors"
	"math"
	"regexp"
	"strings"
	"time"
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
	ReasoningTokens          *int64   `json:"reasoning_tokens,omitempty"`
	PartialUsage             *bool    `json:"partial_usage,omitempty"`
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

	InjectedBytes       *int64     `json:"injected_bytes,omitempty"`
	PromptSHA256        string     `json:"prompt_sha256,omitempty"`
	DeliveredRecordIDs  []string   `json:"delivered_record_ids,omitempty"`
	AttemptID           string     `json:"attempt_id,omitempty"`
	DeliveryID          string     `json:"delivery_id,omitempty"`
	NativeSessionID     string     `json:"native_session_id,omitempty"`
	ClientGeneratedAt   *time.Time `json:"client_generated_at,omitempty"`
	InjectedUnit        string     `json:"injected_unit,omitempty"`
	InjectedTokenizer   string     `json:"injected_tokenizer,omitempty"`
	DedupSkippedCount   *int       `json:"dedup_skipped_count,omitempty"`
	ExpansionReason     string     `json:"expansion_reason,omitempty"`
	NativeContextTokens *int64     `json:"native_context_tokens,omitempty"`
	NativeMessageCount  *int       `json:"native_message_count,omitempty"`
}

const (
	maxRecallMilliseconds = 3600000
	maxRecallCalls        = 1000
	maxRecallSelectors    = 8
	maxRecallReceipts     = 8
	maxRecallDelivered    = 64
	maxRecallInjected     = 1048576
	maxRecallTokens       = 1 << 40
	maxRecallCostUSD      = 1000.0
)

var (
	recallErrorClass   = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_]{0,63}$`)
	recallPromptDigest = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

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
	for _, tokens := range []*int64{call.InputTokens, call.OutputTokens, call.CacheCreationInputTokens, call.CacheReadInputTokens, call.ReasoningTokens} {
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
	case "claude", "codex", "opencode", "hermes", "agy":
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
	if req.PromptSHA256 != "" && !recallPromptDigest.MatchString(req.PromptSHA256) {
		return invalid("prompt_sha256 must be a lowercase 64-character hex digest")
	}
	if req.AttemptID != "" {
		if err := validID(req.AttemptID); err != nil {
			return invalid("attempt_id must be a UUID")
		}
	}
	if req.DeliveryID != "" {
		if err := validID(req.DeliveryID); err != nil {
			return invalid("delivery_id must be a UUID")
		}
	}
	if req.NativeSessionID != "" && !validTelemetryString(req.NativeSessionID, 256) {
		return invalid("native_session_id must be bounded non-control text")
	}
	switch req.InjectedUnit {
	case "", "utf8_bytes", "chars", "tokens_cl100k", "tokens_o200k", "tokens_reported":
	default:
		return invalid("unknown injected_unit")
	}
	if req.InjectedTokenizer != "" && !validTelemetryString(req.InjectedTokenizer, 64) {
		return invalid("injected_tokenizer must be bounded non-control text")
	}
	if req.DedupSkippedCount != nil && (*req.DedupSkippedCount < 0 || *req.DedupSkippedCount > maxRecallCalls) {
		return invalid("dedup_skipped_count out of range")
	}
	if req.ExpansionReason != "" && !validTelemetryString(req.ExpansionReason, 128) {
		return invalid("expansion_reason must be bounded non-control text")
	}
	if req.NativeContextTokens != nil && (*req.NativeContextTokens < 0 || *req.NativeContextTokens > maxRecallTokens) {
		return invalid("native_context_tokens out of range")
	}
	if req.NativeMessageCount != nil && (*req.NativeMessageCount < 0 || *req.NativeMessageCount > 100000) {
		return invalid("native_message_count out of range")
	}
	if len(req.DeliveredRecordIDs) > maxRecallDelivered {
		return invalid("too many delivered record IDs")
	}
	seenDelivered := map[string]bool{}
	for _, id := range req.DeliveredRecordIDs {
		if err := validID(id); err != nil {
			return err
		}
		if seenDelivered[id] {
			return invalid("duplicate delivered record ID")
		}
		seenDelivered[id] = true
	}
	return nil
}

// ObserveRecall aliases RecordRecallObservation for recall-miss and hook callers.
func (s *Store) ObserveRecall(ctx context.Context, req RecallObservationRequest) (Observation, error) {
	return s.RecordRecallObservation(ctx, req)
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
 elapsed_ms,search_ms,selector_ms,pulls_ms,search_calls,pull_calls,selector_calls,injected_bytes,
 attempt_id,delivery_id,native_session_id,client_generated_at,injected_unit,injected_tokenizer,
 dedup_skipped_count,expansion_reason,native_context_tokens,native_message_count)
 VALUES($1,$2,$3,$4,$5,$6,NULLIF($7,''),$8,$9,$10,$11,$12,$13,$14,$15,
 NULLIF($16,'')::uuid,NULLIF($17,'')::uuid,NULLIF($18,''),$19,NULLIF($20,''),NULLIF($21,''),$22,NULLIF($23,''),$24,$25)`,
			id, req.Repo, req.Harness, req.HookEvent, req.Method, req.Status, req.ErrorClass,
			req.ElapsedMS, req.SearchMS, req.SelectorMS, req.PullsMS, req.SearchCalls, req.PullCalls, selectorCalls, req.InjectedBytes,
			req.AttemptID, req.DeliveryID, req.NativeSessionID, req.ClientGeneratedAt, req.InjectedUnit, req.InjectedTokenizer,
			req.DedupSkippedCount, req.ExpansionReason, req.NativeContextTokens, req.NativeMessageCount)
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
 input_tokens,output_tokens,cache_creation_input_tokens,cache_read_input_tokens,reasoning_tokens,partial_usage,cost_usd)
 VALUES($1,$2,$3,$4,$5,NULLIF($6,''),NULLIF($7,''),$8,$9,$10,$11,$12,$13,$14)`,
				id, i+1, call.Stage, call.Outcome, call.ElapsedMS, call.Model, call.ModelSource,
				call.InputTokens, call.OutputTokens, call.CacheCreationInputTokens, call.CacheReadInputTokens, call.ReasoningTokens, call.PartialUsage, call.CostUSD); err != nil {
				return Observation{}, err
			}
		}
		if req.HookEvent == "UserPromptSubmit" && len(req.ReceiptIDs) > 0 {
			if err = s.recordRecallMissCandidates(ctx, tx, id, req); err != nil {
				return Observation{}, err
			}
		}
		return Observation{id}, nil
	})
}
