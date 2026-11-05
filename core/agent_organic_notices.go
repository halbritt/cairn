package core

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type OwnerInputAttestation struct {
	Schema    string `json:"schema"`
	Payload   string `json:"payload"`
	Signature string `json:"signature"`
}

type OwnerInputAttestationPayload struct {
	IssuedAtUnixMS    int64   `json:"issued_at_unix_ms"`
	LaunchID          string  `json:"launch_id"`
	RestoreGeneration int64   `json:"restore_generation"`
	NativeSessionID   string  `json:"native_session_id"`
	NativeTurnID      string  `json:"native_turn_id"`
	OriginContract    string  `json:"origin_contract"`
	OriginVersion     int     `json:"origin_version"`
	Source            string  `json:"source"`
	Admission         string  `json:"admission"`
	InputID           *string `json:"input_id"`
}

type OwnerInputOrigin struct {
	Version   int    `json:"version"`
	Source    string `json:"source"`
	Admission string `json:"admission"`
	InputID   string `json:"input_id"`
}

type SessionOrganicNoticeClaim struct {
	RequestID             string                 `json:"request_id"`
	Session               AgentSessionRef        `json:"session"`
	NativeOwnerTurnID     string                 `json:"native_owner_turn_id"`
	OwnerInputOrigin      *OwnerInputOrigin      `json:"owner_input_origin,omitempty"`
	OwnerInputAttestation *OwnerInputAttestation `json:"owner_input_attestation,omitempty"`
}

type SessionOrganicNoticeResult struct {
	RootID    string               `json:"root_id,omitempty"`
	Decision  string               `json:"decision"` // admitted, none, exhausted
	Attempt   *SessionInboxAttempt `json:"attempt,omitempty"`
	Remainder string               `json:"remainder,omitempty"`
}

type SessionOrganicNoticeAdvance struct {
	RequestID string          `json:"request_id"`
	Session   AgentSessionRef `json:"session"`
	AttemptID string          `json:"attempt_id"`
	RootID    string          `json:"root_id"`
	Version   int             `json:"version"`
}

type NoticePendingRequest struct {
	Repo    string `json:"repo,omitempty"`
	Agent   string `json:"agent,omitempty"`
	AgentID string `json:"agent_id,omitempty"`
	State   string `json:"state,omitempty"`
	Limit   int    `json:"limit,omitempty"`
}

type OrganicNoticeItem struct {
	NoticeID            string     `json:"notice_id"`
	EventID             string     `json:"event_id"`
	RecipientAgentID    string     `json:"recipient_agent_id"`
	RecipientInbox      string     `json:"recipient_inbox"`
	PublisherPrincipal  string     `json:"publisher_principal"`
	ReservedDeliveryID  string     `json:"reserved_delivery_id"`
	State               string     `json:"state"`
	RestoreGeneration   int64      `json:"restore_generation"`
	CreatedAt           time.Time  `json:"created_at"`
	ExpiresAt           time.Time  `json:"expires_at"`
	AdmittedAt          *time.Time `json:"admitted_at,omitempty"`
	AdmissionTurnRootID string     `json:"admission_turn_root_id,omitempty"`
	UnavailableReason   string     `json:"unavailable_reason,omitempty"`
}

type NoticePendingResult struct {
	Repo           string              `json:"repo"`
	Notices        []OrganicNoticeItem `json:"notices"`
	Count          int                 `json:"count"`
	OldestAgeSec   *float64            `json:"oldest_age_sec,omitempty"`
	EarliestExpiry *time.Time          `json:"earliest_expiry,omitempty"`
	Supported      bool                `json:"supported"`
}

func (s *Store) NoticePending(ctx context.Context, req NoticePendingRequest, dest Destination) (NoticePendingResult, error) {
	repo := req.Repo
	if repo == "" {
		repo = s.channel.Repo
	}
	if err := s.checkRepo(repo); err != nil {
		return NoticePendingResult{}, err
	}
	limit := req.Limit
	if limit <= 0 || limit > 100 {
		limit = 50
	}
	agentFilter := req.AgentID
	if agentFilter == "" {
		agentFilter = req.Agent
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return NoticePendingResult{}, err
	}
	defer tx.Rollback(ctx)

	rows, err := tx.Query(ctx, `SELECT notice_id::text, event_id::text, recipient_agent_id::text, recipient_inbox, publisher_principal,
		reserved_delivery_id::text, state, restore_generation, created_at, expires_at, admitted_at,
		COALESCE(admission_turn_root_id::text, ''), COALESCE(unavailable_reason, '')
		FROM cairn.agent_organic_notice
		WHERE repo = $1
		  AND ($2 = '' OR recipient_agent_id::text = $2 OR recipient_inbox = $2 OR recipient_inbox = 'agent/' || $2)
		  AND ($3 = '' OR state = $3)
		  AND (publisher_principal = $4 OR recipient_inbox = $4 OR recipient_inbox = 'agent/' || $4 OR $5)
		ORDER BY created_at ASC
		LIMIT $6`, repo, agentFilter, req.State, s.channel.Principal, s.channel.Operator, limit)
	if err != nil {
		return NoticePendingResult{}, err
	}
	defer rows.Close()

	items := make([]OrganicNoticeItem, 0)
	for rows.Next() {
		var it OrganicNoticeItem
		if err := rows.Scan(&it.NoticeID, &it.EventID, &it.RecipientAgentID, &it.RecipientInbox, &it.PublisherPrincipal,
			&it.ReservedDeliveryID, &it.State, &it.RestoreGeneration, &it.CreatedAt, &it.ExpiresAt, &it.AdmittedAt,
			&it.AdmissionTurnRootID, &it.UnavailableReason); err != nil {
			return NoticePendingResult{}, err
		}
		items = append(items, it)
	}
	if err := rows.Err(); err != nil {
		return NoticePendingResult{}, err
	}
	rows.Close()

	var oldestAgeSec *float64
	var earliestExpiry *time.Time
	var count int
	var ageSecVal float64

	err = tx.QueryRow(ctx, `SELECT COALESCE(EXTRACT(EPOCH FROM (clock_timestamp() - min(created_at))), 0), min(expires_at), count(*)
		FROM cairn.agent_organic_notice
		WHERE repo = $1 AND state = 'queued'
		  AND ($2 = '' OR recipient_agent_id::text = $2 OR recipient_inbox = $2 OR recipient_inbox = 'agent/' || $2)
		  AND (publisher_principal = $3 OR recipient_inbox = $3 OR recipient_inbox = 'agent/' || $3 OR $4)`,
		repo, agentFilter, s.channel.Principal, s.channel.Operator).Scan(&ageSecVal, &earliestExpiry, &count)
	if err != nil {
		return NoticePendingResult{}, err
	}
	if count > 0 {
		oldestAgeSec = &ageSecVal
	}

	supported := true
	if agentFilter != "" {
		cleanID := strings.TrimPrefix(agentFilter, "agent/")
		if validID(cleanID) == nil {
			var executionID string
			var dbGen int64
			var metaRaw []byte
			err := tx.QueryRow(ctx, `SELECT execution_id::text, database_generation, metadata FROM cairn.agent_session WHERE repo=$1 AND agent_id=$2`, repo, cleanID).Scan(&executionID, &dbGen, &metaRaw)
			if errors.Is(err, pgx.ErrNoRows) {
				supported = false
			} else if err != nil {
				return NoticePendingResult{}, err
			} else {
				var meta AgentMetadata
				if jsonErr := json.Unmarshal(metaRaw, &meta); jsonErr != nil {
					return NoticePendingResult{}, jsonErr
				}
				if meta.DeliveryMode == "existing-session" && meta.Harness == "codex" {
					var activeBinding bool
					err = tx.QueryRow(ctx, `SELECT EXISTS(
						SELECT 1 FROM cairn.agent_native_owner_session_binding b
						JOIN cairn.agent_native_owner_launch l ON l.launch_id = b.launch_id
						WHERE b.repo = $1 AND b.agent_id = $2 AND b.execution_id = $3 AND b.restore_generation = $4
						  AND b.state = 'active' AND l.status = 'active'
					)`, repo, cleanID, executionID, dbGen).Scan(&activeBinding)
					if err != nil {
						return NoticePendingResult{}, err
					}
					supported = activeBinding
				} else {
					supported = false
				}
			}
		} else {
			supported = false
		}
	}

	return NoticePendingResult{
		Repo:           repo,
		Notices:        items,
		Count:          count,
		OldestAgeSec:   oldestAgeSec,
		EarliestExpiry: earliestExpiry,
		Supported:      supported,
	}, nil
}

type NoticeExpirySweepRequest struct {
	Repo string `json:"repo,omitempty"`
}

type NoticeExpirySweepResult struct {
	ExpiredCount int `json:"expired_count"`
}

func (s *Store) SweepNoticeExpiries(ctx context.Context, req NoticeExpirySweepRequest) (NoticeExpirySweepResult, error) {
	repo := req.Repo
	if repo == "" {
		repo = s.channel.Repo
	}
	tag, err := s.pool.Exec(ctx, `UPDATE cairn.agent_organic_notice SET state='expired' WHERE ($1 = '' OR repo=$1) AND state='queued' AND expires_at<=clock_timestamp()`, repo)
	if err != nil {
		return NoticeExpirySweepResult{}, err
	}
	return NoticeExpirySweepResult{ExpiredCount: int(tag.RowsAffected())}, nil
}

func (s *Store) ClaimOrganicNotice(ctx context.Context, req SessionOrganicNoticeClaim, dest Destination) (SessionOrganicNoticeResult, error) {
	var empty SessionOrganicNoticeResult
	if err := s.nativeInboxProfile(req.Session, dest); err != nil {
		return empty, err
	}
	if validID(req.RequestID) != nil || validID(req.Session.AgentID) != nil || validID(req.Session.ExecutionID) != nil || strings.TrimSpace(req.NativeOwnerTurnID) == "" {
		return empty, failure("INVALID_REQUEST", "stable UUIDs and native turn ID required")
	}
	if req.OwnerInputAttestation == nil {
		return empty, failure("AUTHORITY_DENIED", "owner_input_attestation is required")
	}
	if req.OwnerInputAttestation.Schema != "cairn.owner-input-attestation/1" {
		return empty, failure("INVALID_REQUEST", "owner_input_attestation schema must be cairn.owner-input-attestation/1")
	}
	payloadEncoded := req.OwnerInputAttestation.Payload
	sigEncoded := req.OwnerInputAttestation.Signature
	if len(payloadEncoded) == 0 || len(payloadEncoded) > 2732 {
		return empty, failure("INVALID_REQUEST", "attestation payload length out of bounds (must be 1-2732 chars)")
	}
	if len(sigEncoded) != 86 {
		return empty, failure("INVALID_REQUEST", "attestation signature must be unpadded base64url of 64 bytes (86 chars)")
	}
	if strings.Contains(payloadEncoded, "=") || strings.Contains(sigEncoded, "=") {
		return empty, failure("INVALID_REQUEST", "attestation payload and signature must use unpadded base64url")
	}
	rawPayload, err := base64.RawURLEncoding.DecodeString(payloadEncoded)
	if err != nil || len(rawPayload) == 0 || len(rawPayload) > 2048 {
		return empty, failure("INVALID_REQUEST", "attestation payload must be valid base64url JSON (<= 2048 bytes)")
	}
	sigBytes, err := base64.RawURLEncoding.DecodeString(sigEncoded)
	if err != nil || len(sigBytes) != ed25519.SignatureSize {
		return empty, failure("INVALID_REQUEST", "attestation signature must be a 64-byte Ed25519 signature encoded in base64url")
	}
	signedBytes := append([]byte("cairn.owner-input-attestation/1\n"), rawPayload...)

	var payload OwnerInputAttestationPayload
	dec := json.NewDecoder(bytes.NewReader(rawPayload))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&payload); err != nil {
		return empty, failure("INVALID_REQUEST", "invalid attestation payload: "+err.Error())
	}
	var trailingExtra any
	if err := dec.Decode(&trailingExtra); !errors.Is(err, io.EOF) {
		return empty, failure("INVALID_REQUEST", "trailing data in attestation payload")
	}
	if payload.IssuedAtUnixMS <= 0 {
		return empty, failure("INVALID_REQUEST", "issued_at_unix_ms must be positive")
	}
	if payload.Source != "owner_composer" && payload.Source != "unknown" {
		return empty, failure("INVALID_REQUEST", fmt.Sprintf("invalid attestation source: %q", payload.Source))
	}
	if payload.Admission != "fresh_start" && payload.Admission != "joined" && payload.Admission != "recovery" && payload.Admission != "unknown" {
		return empty, failure("INVALID_REQUEST", fmt.Sprintf("invalid attestation admission: %q", payload.Admission))
	}
	if validID(payload.LaunchID) != nil {
		return empty, failure("INVALID_REQUEST", "attestation launch_id must be a valid UUID")
	}
	if payload.OriginContract != "codex.owner-input/1" || payload.OriginVersion != 1 {
		return empty, failure("INVALID_REQUEST", "unsupported origin contract or version")
	}
	if strings.TrimSpace(payload.NativeSessionID) == "" || strings.TrimSpace(payload.NativeTurnID) == "" {
		return empty, failure("INVALID_REQUEST", "native_session_id and native_turn_id required in attestation")
	}
	var inputIDStr string
	if payload.InputID != nil {
		inputIDStr = *payload.InputID
	}
	if payload.Source == "owner_composer" && payload.Admission == "fresh_start" {
		if inputIDStr == "" || validID(inputIDStr) != nil {
			return empty, failure("INVALID_REQUEST", "owner_composer fresh_start requires canonical UUID input_id")
		}
	} else if inputIDStr != "" && validID(inputIDStr) != nil {
		return empty, failure("INVALID_REQUEST", "invalid input_id UUID in attestation")
	}

	if req.NativeOwnerTurnID != payload.NativeTurnID {
		return empty, failure("INVALID_REQUEST", "request native_owner_turn_id does not match signed attestation")
	}
	if req.OwnerInputOrigin != nil {
		if req.OwnerInputOrigin.Version != payload.OriginVersion ||
			req.OwnerInputOrigin.Source != payload.Source ||
			req.OwnerInputOrigin.Admission != payload.Admission ||
			req.OwnerInputOrigin.InputID != inputIDStr {
			return empty, failure("INVALID_REQUEST", "top-level owner_input_origin does not match signed attestation")
		}
	}

	guard := func(tx pgx.Tx) error {
		if err := s.checkFeatureEnabledTx(ctx, tx, "next-turn-notices/1"); err != nil {
			return err
		}
		gen, err := retrievalGeneration(ctx, tx)
		if err != nil {
			return err
		}
		if payload.RestoreGeneration != gen {
			return failure("STALE_SESSION", "attestation restore generation does not match database generation")
		}
		a, err := currentAgentSession(ctx, tx, req.Session, s.channel.Principal, s.channel.Repo, true, dest)
		if err != nil {
			return err
		}
		if err = activeAgent(a); err != nil {
			return err
		}
		if a.Metadata.DeliveryMode != "existing-session" {
			return failure("INVALID_REQUEST", "organic claim requires existing-session delivery mode")
		}

		var lRepo, lOwner, lBinding, lStatus string
		var lPubKey []byte
		var lGen int64
		err = tx.QueryRow(ctx, `SELECT repo, profile_principal, binding, public_key, status, restore_generation FROM cairn.agent_native_owner_launch WHERE launch_id = $1`, payload.LaunchID).
			Scan(&lRepo, &lOwner, &lBinding, &lPubKey, &lStatus, &lGen)
		if errors.Is(err, pgx.ErrNoRows) {
			return failure("AUTHORITY_DENIED", "active native owner launch not found")
		}
		if err != nil {
			return err
		}
		if lStatus != "active" {
			return failure("AUTHORITY_DENIED", "active native owner launch not found")
		}
		if lGen != gen {
			return failure("STALE_SESSION", "launch restore generation does not match current generation")
		}
		var sessionBinding string
		err = tx.QueryRow(ctx, `SELECT binding FROM cairn.agent_session WHERE agent_id = $1`, a.AgentID).Scan(&sessionBinding)
		if err != nil {
			return err
		}
		if lOwner != a.ProfileOwner || lRepo != a.Repo || lBinding != sessionBinding {
			return failure("AUTHORITY_DENIED", "launch parameters do not match agent session")
		}
		if lOwner != s.channel.Principal {
			return failure("AUTHORITY_DENIED", "caller profile does not match launch owner")
		}
		if a.NativeSessionID != payload.NativeSessionID {
			return failure("AUTHORITY_DENIED", "native session ID does not match attestation")
		}
		if !ed25519.Verify(lPubKey, signedBytes, sigBytes) {
			return failure("AUTHORITY_DENIED", "invalid owner-input attestation signature")
		}

		var resAgentID, resExecutionID, resState string
		var resGen int64
		err = tx.QueryRow(ctx, `SELECT agent_id::text, execution_id::text, restore_generation, state FROM cairn.agent_native_owner_session_binding WHERE launch_id = $1 AND native_session_id = $2`,
			payload.LaunchID, payload.NativeSessionID).Scan(&resAgentID, &resExecutionID, &resGen, &resState)
		if errors.Is(err, pgx.ErrNoRows) {
			return failure("AUTHORITY_DENIED", "no launch reservation for native session")
		}
		if err != nil {
			return err
		}
		if resAgentID != a.AgentID || resExecutionID != a.ExecutionID || resGen != gen {
			return failure("AUTHORITY_DENIED", "stale execution or generation for reserved launch pair")
		}

		// The request may recover an older attempt from this same turn. Recheck
		// the exact frozen disclosure, not the request UUID or a live successor.
		var response []byte
		var responseDeleted bool
		err = tx.QueryRow(ctx, `SELECT response,payload_deleted_by IS NOT NULL OR ordinary_deleted
			FROM cairn.mutation_request WHERE caller=$1 AND operation='session-organic-notice-claim' AND request_id=$2`,
			s.channel.Principal, req.RequestID).Scan(&response, &responseDeleted)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		if responseDeleted {
			return nil // mutate returns PAYLOAD_UNAVAILABLE without disclosing the receipt.
		}
		var original SessionOrganicNoticeResult
		if err = json.Unmarshal(response, &original); err != nil {
			return err
		}
		if original.Attempt != nil {
			return s.checkOrganicClaimSource(ctx, tx, original.Attempt.ID, req.Session, original.RootID, req.NativeOwnerTurnID, dest)
		}

		return nil
	}

	return mutate(ctx, s, "session-organic-notice-claim", req.RequestID, req, func(tx pgx.Tx) (SessionOrganicNoticeResult, error) {
		a, err := currentAgentSession(ctx, tx, req.Session, s.channel.Principal, s.channel.Repo, true, dest)
		if err != nil {
			return empty, err
		}
		gen, err := retrievalGeneration(ctx, tx)
		if err != nil {
			return empty, err
		}
		var resState string
		err = tx.QueryRow(ctx, `SELECT state FROM cairn.agent_native_owner_session_binding WHERE launch_id = $1 AND native_session_id = $2 FOR UPDATE`,
			payload.LaunchID, payload.NativeSessionID).Scan(&resState)
		if err != nil {
			return empty, err
		}

		// Check if turn root already exists (replay recovery vs conflict) BEFORE clock freshness window
		var existingRootID string
		var existingContractVer int
		var existingDecision string
		var existingAdmittedCount int
		var existingInputID *string
		var existingIssuedAt int64
		err = tx.QueryRow(ctx, `SELECT root_id::text, owner_input_id::text, origin_contract_version, decision, admitted_count, issued_at_unix_ms FROM cairn.agent_organic_notice_turn_root WHERE repo=$1 AND agent_id=$2 AND execution_id=$3 AND restore_generation=$4 AND native_owner_turn_id=$5 FOR UPDATE`,
			a.Repo, a.AgentID, a.ExecutionID, gen, req.NativeOwnerTurnID).Scan(&existingRootID, &existingInputID, &existingContractVer, &existingDecision, &existingAdmittedCount, &existingIssuedAt)
		if err == nil {
			if existingContractVer != payload.OriginVersion {
				return empty, failure("VERSION_CONFLICT", "conflicting origin version cannot reopen turn")
			}
			// 1. If incoming signed payload is non-fresh (Source != "owner_composer" or Admission != "fresh_start" or inputIDStr == ""):
			if payload.Source != "owner_composer" || payload.Admission != "fresh_start" || inputIDStr == "" {
				// Validate timestamp freshness [-5s, +30s]
				var nowMS int64
				if err = tx.QueryRow(ctx, `SELECT (EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::bigint`).Scan(&nowMS); err != nil {
					return empty, err
				}
				diff := nowMS - payload.IssuedAtUnixMS
				if diff > 30000 {
					return empty, failure("INVALID_REQUEST", "unseen attestation is too old (> 30s)")
				}
				if diff < -5000 {
					return empty, failure("INVALID_REQUEST", "unseen attestation timestamp is in the future (> 5s)")
				}

				// Ensure inputIDStr isn't reusing another turn's owner_input_id
				if inputIDStr != "" {
					var otherTurnID string
					err = tx.QueryRow(ctx, `SELECT native_owner_turn_id FROM cairn.agent_organic_notice_turn_root WHERE repo=$1 AND agent_id=$2 AND execution_id=$3 AND restore_generation=$4 AND owner_input_id=$5 LIMIT 1`,
						a.Repo, a.AgentID, a.ExecutionID, gen, inputIDStr).Scan(&otherTurnID)
					if err == nil && otherTurnID != req.NativeOwnerTurnID {
						return empty, failure("INPUT_ID_REUSED", fmt.Sprintf("owner_input_id %s is already bound to turn %s", inputIDStr, otherTurnID))
					} else if err != nil && !errors.Is(err, pgx.ErrNoRows) {
						return empty, err
					}
				}

				// Leave frozen root untouched, and return:
				// SessionOrganicNoticeResult{RootID: existingRootID, Decision: "none", Remainder: "non_composer_input"} with Attempt: nil!
				return SessionOrganicNoticeResult{RootID: existingRootID, Decision: "none", Remainder: "non_composer_input"}, nil
			}

			// 2. Incoming payload IS owner_composer + fresh_start:
			existingInputIDStr := ""
			if existingInputID != nil {
				existingInputIDStr = *existingInputID
			}
			if existingInputIDStr != inputIDStr || existingIssuedAt != payload.IssuedAtUnixMS {
				return empty, failure("CONFLICT", "existing native turn arrived with conflicting attestation parameters")
			}
			if existingDecision == "none" || existingDecision == "exhausted" {
				return SessionOrganicNoticeResult{RootID: existingRootID, Decision: existingDecision, Remainder: existingDecision}, nil
			}
			var attemptID string
			err = tx.QueryRow(ctx, `SELECT attempt_id::text FROM cairn.agent_session_attempt WHERE agent_id=$1 AND organic_root=$2 ORDER BY created_at ASC LIMIT 1`, a.AgentID, existingRootID).Scan(&attemptID)
			if errors.Is(err, pgx.ErrNoRows) {
				return SessionOrganicNoticeResult{RootID: existingRootID, Decision: "admitted", Remainder: "already_admitted"}, nil
			}
			if err != nil {
				return empty, err
			}
			if err = s.checkOrganicClaimSource(ctx, tx, attemptID, req.Session, existingRootID, req.NativeOwnerTurnID, dest); err != nil {
				return empty, err
			}
			att, rErr := s.readSessionInboxAttempt(ctx, tx, attemptID, req.Session, dest)
			if rErr != nil {
				return empty, rErr
			}
			return SessionOrganicNoticeResult{RootID: existingRootID, Decision: "admitted", Attempt: &att}, nil
		} else if !errors.Is(err, pgx.ErrNoRows) {
			return empty, err
		}

		// Check if non-null input_id is already bound to a different native turn
		if inputIDStr != "" {
			var otherTurnID string
			err = tx.QueryRow(ctx, `SELECT native_owner_turn_id FROM cairn.agent_organic_notice_turn_root WHERE repo=$1 AND agent_id=$2 AND execution_id=$3 AND restore_generation=$4 AND owner_input_id=$5 LIMIT 1`,
				a.Repo, a.AgentID, a.ExecutionID, gen, inputIDStr).Scan(&otherTurnID)
			if err == nil && otherTurnID != req.NativeOwnerTurnID {
				return empty, failure("INPUT_ID_REUSED", fmt.Sprintf("owner_input_id %s is already bound to turn %s", inputIDStr, otherTurnID))
			} else if err != nil && !errors.Is(err, pgx.ErrNoRows) {
				return empty, err
			}
		}

		// When creating a NEW root: enforce freshness window [-5s, +30s]
		var nowMS int64
		if err = tx.QueryRow(ctx, `SELECT (EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::bigint`).Scan(&nowMS); err != nil {
			return empty, err
		}
		diff := nowMS - payload.IssuedAtUnixMS
		if diff > 30000 {
			return empty, failure("INVALID_REQUEST", "unseen attestation is too old (> 30s)")
		}
		if diff < -5000 {
			return empty, failure("INVALID_REQUEST", "unseen attestation timestamp is in the future (> 5s)")
		}

		// Activate reservation if pending (only for fresh unseen attestation!)
		if resState == "pending" {
			_, err = tx.Exec(ctx, `UPDATE cairn.agent_native_owner_session_binding SET state='active', activated_at=clock_timestamp() WHERE launch_id=$1 AND native_session_id=$2`, payload.LaunchID, payload.NativeSessionID)
			if err != nil {
				return empty, err
			}
		}

		// Can this input admit notices?
		// Only owner_composer + fresh_start + non-null input_id admits notices.
		if payload.Source != "owner_composer" || payload.Admission != "fresh_start" || inputIDStr == "" {
			rootID := uuid.NewString()
			_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_organic_notice_turn_root(root_id, repo, agent_id, execution_id, native_owner_turn_id, owner_input_id, origin_contract_version, decision, cutoff, admitted_count, admitted_limit, restore_generation, issued_at_unix_ms) VALUES($1, $2, $3, $4, $5, NULLIF($6, '')::uuid, $7, 'none', clock_timestamp(), 0, 3, $8, $9)`,
				rootID, a.Repo, a.AgentID, a.ExecutionID, req.NativeOwnerTurnID, inputIDStr, payload.OriginVersion, gen, payload.IssuedAtUnixMS)
			if err != nil {
				return empty, err
			}
			return SessionOrganicNoticeResult{RootID: rootID, Decision: "none", Remainder: "non_composer_input"}, nil
		}

		// Check for live hold on session:
		var liveHold bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_session_attempt WHERE agent_id=$1 AND finished_at IS NULL)`, a.AgentID).Scan(&liveHold); err != nil {
			return empty, err
		}
		if liveHold {
			rootID := uuid.NewString()
			_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_organic_notice_turn_root(root_id, repo, agent_id, execution_id, native_owner_turn_id, owner_input_id, origin_contract_version, decision, cutoff, admitted_count, admitted_limit, restore_generation, issued_at_unix_ms) VALUES($1, $2, $3, $4, $5, $6::uuid, $7, 'none', clock_timestamp(), 0, 3, $8, $9)`,
				rootID, a.Repo, a.AgentID, a.ExecutionID, req.NativeOwnerTurnID, inputIDStr, payload.OriginVersion, gen, payload.IssuedAtUnixMS)
			if err != nil {
				return empty, err
			}
			return SessionOrganicNoticeResult{RootID: rootID, Decision: "none", Remainder: "hold_active"}, nil
		}

		// Check for ready request or urgent notice:
		var priority bool
		err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1`+sessionInboxEligibleBase+
			` AND (e.kind='request' OR e.notice_policy->>'urgency'='urgent'))`, a.Repo, a.Inbox, dest.AllowLocal).Scan(&priority)
		if err != nil {
			return empty, err
		}
		if priority {
			rootID := uuid.NewString()
			_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_organic_notice_turn_root(root_id, repo, agent_id, execution_id, native_owner_turn_id, owner_input_id, origin_contract_version, decision, cutoff, admitted_count, admitted_limit, restore_generation, issued_at_unix_ms) VALUES($1, $2, $3, $4, $5, $6::uuid, $7, 'none', clock_timestamp(), 0, 3, $8, $9)`,
				rootID, a.Repo, a.AgentID, a.ExecutionID, req.NativeOwnerTurnID, inputIDStr, payload.OriginVersion, gen, payload.IssuedAtUnixMS)
			if err != nil {
				return empty, err
			}
			return SessionOrganicNoticeResult{RootID: rootID, Decision: "none", Remainder: "priority_waiting"}, nil
		}

		var cutoff time.Time
		if err = tx.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&cutoff); err != nil {
			return empty, err
		}

		// Scan oldest eligible notice with materialized candidates:
		rows, err := tx.Query(ctx, `SELECT notice_id::text, event_id::text, reserved_delivery_id::text, created_at, expires_at
			FROM cairn.agent_organic_notice
			WHERE repo=$1 AND recipient_agent_id=$2 AND state='queued' AND created_at<=$3 AND expires_at>$3 AND restore_generation=$4
			ORDER BY created_at ASC LIMIT 10 FOR UPDATE`, a.Repo, a.AgentID, cutoff, gen)
		if err != nil {
			return empty, err
		}
		type candidateNotice struct {
			noticeID           string
			eventID            string
			reservedDeliveryID string
			createdAt          time.Time
			expiresAt          time.Time
		}
		var candidates []candidateNotice
		for rows.Next() {
			var c candidateNotice
			if err := rows.Scan(&c.noticeID, &c.eventID, &c.reservedDeliveryID, &c.createdAt, &c.expiresAt); err != nil {
				rows.Close()
				return empty, err
			}
			candidates = append(candidates, c)
		}
		if err := rows.Err(); err != nil {
			rows.Close()
			return empty, err
		}
		rows.Close()

		var selectedNoticeID, selectedEventID, selectedReservedDeliveryID string
		for _, cand := range candidates {
			var sourceEligible bool
			err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_event e JOIN cairn.memory_record m ON m.record_id=e.record_id JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=e.version WHERE e.event_id=$1 AND m.lifecycle='active' AND v.payload_deleted_by IS NULL AND (m.sensitivity='shareable' OR $2))`, cand.eventID, dest.AllowLocal).Scan(&sourceEligible)
			if err != nil {
				return empty, err
			}
			if !sourceEligible {
				_, err = tx.Exec(ctx, `UPDATE cairn.agent_organic_notice SET state='unavailable', unavailable_reason='source_unavailable' WHERE notice_id=$1`, cand.noticeID)
				if err != nil {
					return empty, err
				}
				continue
			}
			selectedNoticeID = cand.noticeID
			selectedEventID = cand.eventID
			selectedReservedDeliveryID = cand.reservedDeliveryID
			break
		}

		if selectedNoticeID == "" {
			rootID := uuid.NewString()
			_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_organic_notice_turn_root(root_id, repo, agent_id, execution_id, native_owner_turn_id, owner_input_id, origin_contract_version, decision, cutoff, admitted_count, admitted_limit, restore_generation, issued_at_unix_ms) VALUES($1, $2, $3, $4, $5, $6::uuid, $7, 'none', $8, 0, 3, $9, $10)`,
				rootID, a.Repo, a.AgentID, a.ExecutionID, req.NativeOwnerTurnID, inputIDStr, payload.OriginVersion, cutoff, gen, payload.IssuedAtUnixMS)
			if err != nil {
				return empty, err
			}
			return SessionOrganicNoticeResult{RootID: rootID, Decision: "none", Remainder: "empty"}, nil
		}

		rootID := uuid.NewString()
		_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_organic_notice_turn_root(root_id, repo, agent_id, execution_id, native_owner_turn_id, owner_input_id, origin_contract_version, decision, cutoff, admitted_count, admitted_limit, restore_generation, issued_at_unix_ms) VALUES($1, $2, $3, $4, $5, $6::uuid, $7, 'admitted', $8, 1, 3, $9, $10)`,
			rootID, a.Repo, a.AgentID, a.ExecutionID, req.NativeOwnerTurnID, inputIDStr, payload.OriginVersion, cutoff, gen, payload.IssuedAtUnixMS)
		if err != nil {
			return empty, err
		}

		leaseID := uuid.NewString()
		_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_delivery(delivery_id, event_id, consumer, state, lease_id, lease_until, available_at) VALUES($1, $2, $3, 'leased', $4, clock_timestamp() + make_interval(secs=>$5), clock_timestamp())`,
			selectedReservedDeliveryID, selectedEventID, a.Inbox, leaseID, int(nativeLeaseTTL.Seconds()))
		if err != nil {
			return empty, err
		}

		if err = openAttempt(ctx, tx, nativeAttemptOpen{
			AttemptID:     req.RequestID,
			AgentID:       a.AgentID,
			ExecutionID:   a.ExecutionID,
			Repo:          a.Repo,
			DeliveryID:    selectedReservedDeliveryID,
			LeaseID:       leaseID,
			NativeTurnID:  req.NativeOwnerTurnID,
			Batch:         &nativeBatchPosition{Root: req.RequestID, Index: 1, Limit: 3},
			AdmissionKind: "organic",
			OrganicRoot:   rootID,
		}); err != nil {
			return empty, err
		}

		_, err = tx.Exec(ctx, `UPDATE cairn.agent_organic_notice SET state='admitted', admitted_at=clock_timestamp(), admission_turn_root_id=$2 WHERE notice_id=$1`, selectedNoticeID, rootID)
		if err != nil {
			return empty, err
		}

		attempt, err := s.readSessionInboxAttempt(ctx, tx, req.RequestID, req.Session, dest)
		if err != nil {
			return empty, err
		}
		return SessionOrganicNoticeResult{RootID: rootID, Decision: "admitted", Attempt: &attempt}, nil
	}, guard)
}

func (s *Store) checkOrganicClaimSource(ctx context.Context, tx pgx.Tx, attemptID string, ref AgentSessionRef, rootID, nativeTurnID string, dest Destination) error {
	var agentID, executionID, attemptRoot, attemptTurn string
	err := tx.QueryRow(ctx, `SELECT agent_id::text,execution_id::text,COALESCE(organic_root::text,''),native_turn_id
		FROM cairn.agent_session_attempt WHERE attempt_id=$1 AND owner=$2`, attemptID, s.channel.Principal).
		Scan(&agentID, &executionID, &attemptRoot, &attemptTurn)
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("NOT_FOUND", "native inbox attempt not found")
	}
	if err != nil {
		return err
	}
	if agentID != ref.AgentID || executionID != ref.ExecutionID || attemptRoot != rootID || attemptTurn != nativeTurnID {
		return failure("AUTHORITY_DENIED", "organic attempt does not match the claimed execution and turn")
	}
	var sourceVisible bool
	err = tx.QueryRow(ctx, `SELECT EXISTS(
		SELECT 1 FROM cairn.agent_session_attempt att
		JOIN cairn.agent_delivery d USING(delivery_id)
		JOIN cairn.agent_event e USING(event_id)
		JOIN cairn.memory_record m USING(record_id)
		JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=e.version
		WHERE att.attempt_id=$1 AND m.lifecycle='active' AND v.payload_deleted_by IS NULL
		  AND (m.sensitivity='shareable' OR $2)
	)`, attemptID, dest.AllowLocal).Scan(&sourceVisible)
	if err != nil {
		return err
	}
	if !sourceVisible {
		return failure("NOT_FOUND", "source version unavailable")
	}
	return nil
}

func (s *Store) AdvanceOrganicNotice(ctx context.Context, req SessionOrganicNoticeAdvance, dest Destination) (SessionInboxAdvanceResult, error) {
	var empty SessionInboxAdvanceResult
	if err := s.nativeInboxProfile(req.Session, dest); err != nil {
		return empty, err
	}
	if validID(req.RequestID) != nil || validID(req.AttemptID) != nil || validID(req.RootID) != nil || req.Version != 1 {
		return empty, failure("INVALID_REQUEST", "stable UUIDs and version 1 required")
	}

	guard := func(tx pgx.Tx) error {
		if err := s.checkFeatureEnabledTx(ctx, tx, "next-turn-notices/1"); err != nil {
			return err
		}
		gen, err := retrievalGeneration(ctx, tx)
		if err != nil {
			return err
		}
		a, err := currentAgentSession(ctx, tx, req.Session, s.channel.Principal, s.channel.Repo, true, dest)
		if err != nil {
			return err
		}
		if err = activeAgent(a); err != nil {
			return err
		}
		var rootGen int64
		err = tx.QueryRow(ctx, `SELECT restore_generation FROM cairn.agent_organic_notice_turn_root WHERE root_id=$1`, req.RootID).Scan(&rootGen)
		if errors.Is(err, pgx.ErrNoRows) {
			return failure("NOT_FOUND", "turn root not found")
		}
		if err != nil {
			return err
		}
		if rootGen != gen {
			return failure("STALE_SESSION", "turn root restore generation does not match current store generation")
		}

		var launchStatus string
		err = tx.QueryRow(ctx, `SELECT l.status
			FROM cairn.agent_native_owner_session_binding b
			JOIN cairn.agent_native_owner_launch l USING(launch_id)
			WHERE b.agent_id=$1 AND b.execution_id=$2 AND b.restore_generation=$3 AND b.state='active'`,
			a.AgentID, a.ExecutionID, gen).Scan(&launchStatus)
		if errors.Is(err, pgx.ErrNoRows) {
			return failure("AUTHORITY_DENIED", "no active launch binding for session execution")
		}
		if err != nil {
			return err
		}
		if launchStatus != "active" {
			return failure("AUTHORITY_DENIED", "launch binding is not active")
		}

		var hasAdmittedAttempt bool
		err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_session_attempt WHERE attempt_id=$1)`, req.RequestID).Scan(&hasAdmittedAttempt)
		if err != nil {
			return err
		}
		if hasAdmittedAttempt {
			var attAgentID, attExecutionID string
			err = tx.QueryRow(ctx, `SELECT att.agent_id::text, att.execution_id::text
				FROM cairn.agent_session_attempt att
				WHERE att.attempt_id = $1`, req.RequestID).Scan(&attAgentID, &attExecutionID)
			if err != nil && !errors.Is(err, pgx.ErrNoRows) {
				return err
			}
			if attAgentID != "" && (attAgentID != a.AgentID || attExecutionID != a.ExecutionID) {
				return failure("AUTHORITY_DENIED", "stale execution or agent for attempt")
			}
			var sourceVisible bool
			err = tx.QueryRow(ctx, `SELECT EXISTS(
				SELECT 1 FROM cairn.agent_session_attempt att
				JOIN cairn.agent_delivery d USING(delivery_id)
				JOIN cairn.agent_event e USING(event_id)
				JOIN cairn.memory_record m USING(record_id)
				JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=e.version
				WHERE att.attempt_id=$1
				  AND m.lifecycle='active' AND v.payload_deleted_by IS NULL
				  AND (m.sensitivity='shareable' OR $2)
			)`, req.RequestID, dest.AllowLocal).Scan(&sourceVisible)
			if err != nil {
				return err
			}
			if !sourceVisible {
				return failure("NOT_FOUND", "source version unavailable")
			}
		}
		return nil
	}

	return mutate(ctx, s, "session-organic-notice-advance", req.RequestID, req, func(tx pgx.Tx) (SessionInboxAdvanceResult, error) {
		if err := s.checkFeatureEnabledTx(ctx, tx, "next-turn-notices/1"); err != nil {
			return empty, err
		}
		current, err := s.lockedSessionAttempt(ctx, tx, req.AttemptID, req.Session, dest)
		if err != nil {
			return empty, err
		}
		if current.FinishedAt != nil || current.Cancel != nil || current.NativeTurnID == "" || current.TurnExclusive ||
			current.Delivery.Event.Kind != "notice" || current.AdmissionKind != "organic" || current.OrganicRoot != req.RootID {
			return empty, failure("INVALID_REQUEST", "only a settled organic notice attempt matching the root may advance")
		}
		if current.Delivery.State != "handled" && current.Delivery.State != "ignored" && current.Delivery.State != "failed" {
			return empty, failure("DELIVERY_ACTIVE", "settle this notice explicitly before advancing")
		}

		var decision string
		var cutoff time.Time
		var admittedCount, admittedLimit int
		err = tx.QueryRow(ctx, `SELECT decision, cutoff, admitted_count, admitted_limit FROM cairn.agent_organic_notice_turn_root WHERE root_id=$1 FOR UPDATE`, req.RootID).Scan(&decision, &cutoff, &admittedCount, &admittedLimit)
		if err != nil {
			return empty, err
		}
		if decision != "admitted" {
			return SessionInboxAdvanceResult{Remainder: "batch_ended"}, nil
		}
		endBatch := func(reason string) (SessionInboxAdvanceResult, error) {
			if _, err = finishAttempt(ctx, tx, current.ID, "delivery_completed", attemptClose{}); err != nil {
				return empty, err
			}
			_, err := tx.Exec(ctx, `UPDATE cairn.agent_organic_notice_turn_root SET decision='exhausted' WHERE root_id=$1`, req.RootID)
			if err != nil {
				return empty, err
			}
			return SessionInboxAdvanceResult{Remainder: reason}, nil
		}

		if admittedCount >= admittedLimit || admittedCount >= 3 {
			return endBatch("batch_limit")
		}

		// Priority arrivals end the batch:
		var priority bool
		err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1`+sessionInboxEligibleBase+
			` AND (e.kind='request' OR e.notice_policy->>'urgency'='urgent'))`, s.channel.Repo, "agent/"+req.Session.AgentID, dest.AllowLocal).Scan(&priority)
		if err != nil {
			return empty, err
		}
		if priority {
			return endBatch("priority_waiting")
		}

		gen, err := retrievalGeneration(ctx, tx)
		if err != nil {
			return empty, err
		}

		// Query next queued notice existing at or before cutoff:
		rows, err := tx.Query(ctx, `SELECT notice_id::text, event_id::text, reserved_delivery_id::text
			FROM cairn.agent_organic_notice
			WHERE repo=$1 AND recipient_agent_id=$2 AND state='queued' AND created_at<=$3 AND expires_at>clock_timestamp() AND restore_generation=$4
			ORDER BY created_at ASC LIMIT 10 FOR UPDATE`, s.channel.Repo, req.Session.AgentID, cutoff, gen)
		if err != nil {
			return empty, err
		}
		type advanceCand struct {
			noticeID           string
			eventID            string
			reservedDeliveryID string
		}
		var advanceCandidates []advanceCand
		for rows.Next() {
			var c advanceCand
			if err := rows.Scan(&c.noticeID, &c.eventID, &c.reservedDeliveryID); err != nil {
				rows.Close()
				return empty, err
			}
			advanceCandidates = append(advanceCandidates, c)
		}
		if err := rows.Err(); err != nil {
			rows.Close()
			return empty, err
		}
		rows.Close()

		var nextNoticeID, nextEventID, nextReservedDeliveryID string
		for _, cand := range advanceCandidates {
			var sourceEligible bool
			err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_event e JOIN cairn.memory_record m ON m.record_id=e.record_id JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=e.version WHERE e.event_id=$1 AND m.lifecycle='active' AND v.payload_deleted_by IS NULL AND (m.sensitivity='shareable' OR $2))`, cand.eventID, dest.AllowLocal).Scan(&sourceEligible)
			if err != nil {
				return empty, err
			}
			if !sourceEligible {
				_, err = tx.Exec(ctx, `UPDATE cairn.agent_organic_notice SET state='unavailable', unavailable_reason='source_unavailable' WHERE notice_id=$1`, cand.noticeID)
				if err != nil {
					return empty, err
				}
				continue
			}
			nextNoticeID = cand.noticeID
			nextEventID = cand.eventID
			nextReservedDeliveryID = cand.reservedDeliveryID
			break
		}

		if nextNoticeID == "" {
			return endBatch("empty")
		}

		if _, err = finishAttempt(ctx, tx, current.ID, "delivery_completed", attemptClose{}); err != nil {
			return empty, err
		}

		leaseID := uuid.NewString()
		_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_delivery(delivery_id, event_id, consumer, state, lease_id, lease_until, available_at) VALUES($1, $2, $3, 'leased', $4, clock_timestamp() + make_interval(secs=>$5), clock_timestamp())`,
			nextReservedDeliveryID, nextEventID, "agent/"+req.Session.AgentID, leaseID, int(nativeLeaseTTL.Seconds()))
		if err != nil {
			return empty, err
		}

		if err = openAttempt(ctx, tx, nativeAttemptOpen{
			AttemptID:     req.RequestID,
			AgentID:       req.Session.AgentID,
			ExecutionID:   req.Session.ExecutionID,
			Repo:          s.channel.Repo,
			DeliveryID:    nextReservedDeliveryID,
			LeaseID:       leaseID,
			NativeTurnID:  current.NativeTurnID,
			Batch:         &nativeBatchPosition{Root: current.BatchRoot, Index: current.BatchIndex + 1, Limit: 3},
			AdmissionKind: "organic",
			OrganicRoot:   req.RootID,
		}); err != nil {
			return empty, err
		}

		_, err = tx.Exec(ctx, `UPDATE cairn.agent_organic_notice SET state='admitted', admitted_at=clock_timestamp(), admission_turn_root_id=$2 WHERE notice_id=$1`, nextNoticeID, req.RootID)
		if err != nil {
			return empty, err
		}
		_, err = tx.Exec(ctx, `UPDATE cairn.agent_organic_notice_turn_root SET admitted_count=admitted_count+1 WHERE root_id=$1`, req.RootID)
		if err != nil {
			return empty, err
		}

		attempt, err := s.readSessionInboxAttempt(ctx, tx, req.RequestID, req.Session, dest)
		if err != nil {
			return empty, err
		}
		return SessionInboxAdvanceResult{Attempt: &attempt}, nil
	}, guard)
}

func flushOrganicNoticeTx(ctx context.Context, tx pgx.Tx, s *Store, eventID, requestID string) (NoticeFlushResult, error) {
	var noticeID, recipientInbox, reservedDeliveryID, state string
	var createdAt, expiresAt time.Time
	err := tx.QueryRow(ctx, `SELECT notice_id::text, recipient_inbox, reserved_delivery_id::text, state, created_at, expires_at FROM cairn.agent_organic_notice WHERE event_id=$1 FOR UPDATE`, eventID).Scan(&noticeID, &recipientInbox, &reservedDeliveryID, &state, &createdAt, &expiresAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return NoticeFlushResult{}, failure("NOT_FOUND", "organic notice queue row not found")
	}
	if err != nil {
		return NoticeFlushResult{}, err
	}
	if state == "expired" {
		return NoticeFlushResult{}, failure("INVALID_REQUEST", "expired notice cannot be flushed")
	}
	if state == "flushed" {
		return readNoticeFlush(ctx, tx, eventID)
	}
	var now time.Time
	if err := tx.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&now); err != nil {
		return NoticeFlushResult{}, err
	}
	result := NoticeFlushResult{
		EventID:      eventID,
		RequestID:    requestID,
		FlushedAt:    now,
		FlushedBy:    s.channel.Principal,
		Released:     0,
		AlreadyReady: 0,
		Leased:       0,
		Terminal:     0,
	}
	if state == "queued" {
		if expiresAt.Before(now) {
			_, err = tx.Exec(ctx, `UPDATE cairn.agent_organic_notice SET state='expired' WHERE notice_id=$1`, noticeID)
			if err != nil {
				return NoticeFlushResult{}, err
			}
			return NoticeFlushResult{}, failure("INVALID_REQUEST", "expired notice cannot be flushed")
		}
		_, err = tx.Exec(ctx, `UPDATE cairn.agent_organic_notice SET state='flushed' WHERE notice_id=$1`, noticeID)
		if err != nil {
			return NoticeFlushResult{}, err
		}
		_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_delivery(delivery_id, event_id, consumer, state, available_at) VALUES($1, $2, $3, 'pending', $4)`, reservedDeliveryID, eventID, recipientInbox, now)
		if err != nil {
			return NoticeFlushResult{}, err
		}
		result.Released = 1
	} else if state == "delivered" || state == "admitted" {
		result.Terminal = 1
	} else if state == "claimed" {
		result.Leased = 1
	} else {
		result.AlreadyReady = 1
	}
	err = tx.QueryRow(ctx, `INSERT INTO cairn.agent_notice_flush(event_id,request_id,flushed_at,released,already_ready,leased,terminal) VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING flushed_by`, eventID, requestID, now, result.Released, result.AlreadyReady, result.Leased, result.Terminal).Scan(&result.FlushedBy)
	return result, err
}
