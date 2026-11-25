package core

import (
	"context"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// DeliveryObservationRequest records bounded delivery disposition and context
// size testimony from lifecycle coordination/hooks. It grants no authority and
// never turns absent values into zero.
type DeliveryObservationRequest struct {
	RequestID         string     `json:"request_id"`
	Repo              string     `json:"repo"`
	Harness           string     `json:"harness"`
	HookEvent         string     `json:"hook_event"`
	AttemptID         string     `json:"attempt_id,omitempty"`
	DeliveryID        string     `json:"delivery_id,omitempty"`
	NativeSessionID   string     `json:"native_session_id,omitempty"`
	ClientGeneratedAt *time.Time `json:"client_generated_at,omitempty"`
	Disposition       string     `json:"disposition"`
	SupersededCount   int        `json:"superseded_count,omitempty"`
	InjectedBytes     *int64     `json:"injected_bytes,omitempty"`
	InjectedUnit      string     `json:"injected_unit,omitempty"`
	InjectedTokenizer string     `json:"injected_tokenizer,omitempty"`
	ExpansionReason   string     `json:"expansion_reason,omitempty"`
}

func (req DeliveryObservationRequest) validate() error {
	invalid := func(message string) error { return failure("INVALID_REQUEST", message) }
	if req.Repo == "" || len(req.Repo) > 1024 {
		return invalid("delivery observation requires a bounded repository")
	}
	switch req.Harness {
	case "claude", "codex", "opencode", "hermes", "agy":
	default:
		return invalid("unknown delivery harness")
	}
	if !validTelemetryString(req.HookEvent, 64) {
		return invalid("delivery observation requires a bounded hook_event")
	}
	switch req.Disposition {
	case "full", "compact", "digest", "auto_settled", "superseded":
	default:
		return invalid("unknown delivery disposition")
	}
	if req.SupersededCount < 0 || req.SupersededCount > maxRecallCalls {
		return invalid("superseded_count out of range")
	}
	if req.InjectedBytes != nil && (*req.InjectedBytes < 0 || *req.InjectedBytes > maxRecallInjected) {
		return invalid("injected_bytes out of range")
	}
	switch req.InjectedUnit {
	case "", "utf8_bytes", "chars", "tokens_cl100k", "tokens_o200k", "tokens_reported":
	default:
		return invalid("unknown injected_unit")
	}
	if req.InjectedTokenizer != "" && !validTelemetryString(req.InjectedTokenizer, 64) {
		return invalid("injected_tokenizer must be bounded non-control text")
	}
	if req.ExpansionReason != "" && !validTelemetryString(req.ExpansionReason, 128) {
		return invalid("expansion_reason must be bounded non-control text")
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
	return nil
}

// RecordDeliveryObservation stores one bounded delivery disposition counter row.
func (s *Store) RecordDeliveryObservation(ctx context.Context, req DeliveryObservationRequest) (Observation, error) {
	req.HookEvent = strings.TrimSpace(req.HookEvent)
	if err := req.validate(); err != nil {
		return Observation{}, err
	}
	if err := s.checkRepo(req.Repo); err != nil {
		return Observation{}, err
	}
	return mutate(ctx, s, "delivery-observation", req.RequestID, req, func(tx pgx.Tx) (Observation, error) {
		id := uuid.NewString()
		_, err := tx.Exec(ctx, `INSERT INTO cairn.delivery_observation(
 observation_id,repo,harness,hook_event,attempt_id,delivery_id,native_session_id,client_generated_at,
 disposition,superseded_count,injected_bytes,injected_unit,injected_tokenizer,expansion_reason)
 VALUES($1,$2,$3,$4,NULLIF($5,'')::uuid,NULLIF($6,'')::uuid,NULLIF($7,''),$8,$9,$10,$11,NULLIF($12,''),NULLIF($13,''),NULLIF($14,''))`,
			id, req.Repo, req.Harness, req.HookEvent, req.AttemptID, req.DeliveryID, req.NativeSessionID, req.ClientGeneratedAt,
			req.Disposition, req.SupersededCount, req.InjectedBytes, req.InjectedUnit, req.InjectedTokenizer, req.ExpansionReason)
		return Observation{id}, err
	})
}

func recordStoreDeliveryTelemetry(ctx context.Context, tx pgx.Tx, repo, harness, nativeSessionID, attemptID, deliveryID, hookEvent, disposition string, supersededCount int, expansionReason string) error {
	switch harness {
	case "claude", "codex", "opencode", "hermes", "agy":
	default:
		return nil
	}
	if !validTelemetryString(hookEvent, 64) {
		hookEvent = "SessionInbox"
	}
	if nativeSessionID != "" && !validTelemetryString(nativeSessionID, 256) {
		nativeSessionID = ""
	}
	if supersededCount < 0 {
		supersededCount = 0
	}
	_, err := tx.Exec(ctx, `INSERT INTO cairn.delivery_observation(
 observation_id,repo,harness,hook_event,attempt_id,delivery_id,native_session_id,
 disposition,superseded_count,expansion_reason)
 VALUES(gen_random_uuid(),$1,$2,$3,NULLIF($4,'')::uuid,NULLIF($5,'')::uuid,NULLIF($6,''),$7,$8,NULLIF($9,''))`,
		repo, harness, hookEvent, attemptID, deliveryID, nativeSessionID, disposition, supersededCount, expansionReason)
	return err
}
