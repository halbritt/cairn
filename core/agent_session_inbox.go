package core

import (
	"context"
	"errors"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

type SessionInboxClaim struct {
	RequestID            string          `json:"request_id"`
	Session              AgentSessionRef `json:"session"`
	DeliveryID           string          `json:"delivery_id,omitempty"`
	NativeTurnID         string          `json:"native_turn_id,omitempty"`
	TurnExclusive        bool            `json:"turn_exclusive,omitempty"`
	AutoSettleActionNone bool            `json:"auto_settle_action_none,omitempty"`
	DigestWindowSeconds  int             `json:"digest_window_seconds,omitempty"`
	DigestFlushNow       bool            `json:"digest_flush_now,omitempty"`
	DigestOnly           bool            `json:"digest_only,omitempty"`
}

type SessionInboxSettleNone struct {
	RequestID string          `json:"request_id"`
	Session   AgentSessionRef `json:"session"`
}

type SessionInboxSettleNoneResult struct {
	Settled []string              `json:"settled"`
	Ready   SessionInboxReadiness `json:"ready"`
}
type SessionInboxReconcile struct {
	RequestID string          `json:"request_id"`
	Session   AgentSessionRef `json:"session"`
	AttemptID string          `json:"attempt_id"`
	Reason    string          `json:"reason"`
}
type SessionInboxAttempt struct {
	ID                    string              `json:"attempt_id"`
	Session               AgentSessionRef     `json:"session"`
	CreatedAt             time.Time           `json:"created_at"`
	FinishedAt            *time.Time          `json:"finished_at,omitempty"`
	Reason                string              `json:"reason,omitempty"`
	Delivery              AgentDelivery       `json:"delivery"`
	NativeTurnID          string              `json:"native_turn_id,omitempty"`
	TurnExclusive         bool                `json:"turn_exclusive,omitempty"`
	BatchRoot             string              `json:"batch_root,omitempty"`
	BatchIndex            int                 `json:"batch_index,omitempty"`
	BatchLimit            int                 `json:"batch_limit,omitempty"`
	CaptureGap            bool                `json:"capture_gap,omitempty"`
	TurnStopState         string              `json:"turn_stop_state,omitempty"`
	CaptureState          string              `json:"capture_state,omitempty"`
	TerminalScan          string              `json:"terminal_scan,omitempty"`
	Cancel                *SessionCancel      `json:"cancel,omitempty"`
	Tools                 []SessionTool       `json:"tools,omitempty"`
	AdmissionKind         string              `json:"admission_kind,omitempty"`
	OrganicRoot           string              `json:"organic_root,omitempty"`
	DigestSupersededCount int                 `json:"digest_superseded_count,omitempty"`
	WakeDeliveryID        string              `json:"wake_delivery_id,omitempty"`
	DigestItems           []SessionDigestItem `json:"digest_items,omitempty"`
}
type SessionInboxResult struct {
	Attempt *SessionInboxAttempt `json:"attempt"`
}

type SessionInboxReadiness struct {
	DeliveryID string `json:"delivery_id,omitempty"`
	EventID    string `json:"event_id,omitempty"`
	Action     string `json:"action,omitempty"`
}

const maxAutoSettleBatch = 64

const sessionInboxEligibleBase = ` FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.consumer=$2 AND (e.sensitivity='shareable' OR $3) AND d.available_at<=clock_timestamp() AND e.task_deadline IS NULL AND ` + requestAdmissionOpen + ` AND ` + wakeHold + ` AND ` + sessionInboxHold + ` AND ` + waitNotificationAdmissionOpen + ` AND (d.state='pending' OR (d.state='leased' AND d.lease_until<=clock_timestamp()))`

const sessionInboxEligible = sessionInboxEligibleBase + ` ORDER BY e.position`

const sessionInboxNext = `SELECT d.delivery_id::text` + sessionInboxEligible

// SessionInboxReady is a bounded hint for an idle host wakeup, never a claim.
// The native turn still acquires ownership through ClaimSessionInbox.
func (s *Store) SessionInboxReady(ctx context.Context, ref AgentSessionRef, dest Destination) (SessionInboxReadiness, error) {
	var out SessionInboxReadiness
	if err := s.nativeInboxProfile(ref, dest); err != nil {
		return out, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	a, err := currentAgentSession(ctx, tx, ref, s.channel.Principal, s.channel.Repo, false, dest)
	if err != nil {
		return out, err
	}
	if err = activeAgent(a); err != nil {
		return out, err
	}
	if !a.Online || a.Metadata.DeliveryMode != "existing-session" || a.Metadata.State != "idle" {
		return out, nil
	}
	var held bool
	if held, err = inboxOccupied(ctx, tx, a.AgentID, a.Inbox); err != nil {
		return out, err
	}
	if held {
		return out, nil
	}
	err = tx.QueryRow(ctx, `SELECT d.delivery_id::text, e.event_id::text, COALESCE(e.notice_action, '')`+sessionInboxEligible+` LIMIT 1`, a.Repo, a.Inbox, dest.AllowLocal).Scan(&out.DeliveryID, &out.EventID, &out.Action)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, nil
	}
	return out, err
}

func autoSettleActionNoneDeliveryTx(ctx context.Context, tx pgx.Tx, deliveryID, eventID string, allowLocal bool) (bool, bool, error) {
	var sensitivity string
	var payloadAvailable bool
	err := tx.QueryRow(ctx, `SELECT m.sensitivity, (m.lifecycle = 'active' AND v.payload_deleted_by IS NULL)
		FROM cairn.agent_event e
		JOIN cairn.memory_record m ON m.record_id = e.record_id
		JOIN cairn.record_version v ON v.record_id = m.record_id AND v.version = e.version
		WHERE e.event_id = $1`, eventID).Scan(&sensitivity, &payloadAvailable)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, false, settleDelivery(ctx, tx, deliveryID, "failed", "source_unavailable", nil, nil, false)
	}
	if err != nil {
		return false, false, err
	}
	if sensitivity != "shareable" && !allowLocal {
		return false, true, nil
	}
	if !payloadAvailable {
		return false, false, settleDelivery(ctx, tx, deliveryID, "failed", "source_unavailable", nil, nil, false)
	}
	return true, false, settleDelivery(ctx, tx, deliveryID, "handled", "auto_settled_none", nil, nil, false)
}

// SettleSessionActionNone settles up to maxAutoSettleBatch consecutive eligible
// action:none notices at the head of an unoccupied existing-session inbox
// without opening a model turn, returning the next ready item when idle.
func (s *Store) SettleSessionActionNone(ctx context.Context, req SessionInboxSettleNone, dest Destination) (SessionInboxSettleNoneResult, error) {
	out := SessionInboxSettleNoneResult{Settled: []string{}}
	if err := s.nativeInboxProfile(req.Session, dest); err != nil {
		return out, err
	}
	if validID(req.RequestID) != nil {
		return out, failure("INVALID_REQUEST", "stable request UUID required")
	}
	guard := func(tx pgx.Tx) error {
		a, err := currentAgentSession(ctx, tx, req.Session, s.channel.Principal, s.channel.Repo, true, dest)
		if err != nil {
			return err
		}
		return activeAgent(a)
	}
	input := struct {
		SessionInboxSettleNone
		Destination string `json:"destination"`
		AllowLocal  bool   `json:"allow_local"`
	}{req, dest.Name, dest.AllowLocal}
	return mutate(ctx, s, "session-inbox-settle-none", req.RequestID, input, func(tx pgx.Tx) (SessionInboxSettleNoneResult, error) {
		res := SessionInboxSettleNoneResult{Settled: []string{}}
		a, err := currentAgentSession(ctx, tx, req.Session, s.channel.Principal, s.channel.Repo, true, dest)
		if err != nil {
			return res, err
		}
		if err = activeAgent(a); err != nil {
			return res, err
		}
		if a.Metadata.DeliveryMode != "existing-session" {
			return res, failure("INVALID_REQUEST", "native inbox requires existing-session delivery mode")
		}
		held, err := inboxOccupied(ctx, tx, a.AgentID, a.Inbox)
		if err != nil || held {
			return res, err
		}
		if _, err = expireRequestDeliveries(ctx, tx, s, a.Repo, a.Inbox, dest.AllowLocal); err != nil {
			return res, err
		}
		for i := 0; i <= maxAutoSettleBatch; i++ {
			var id, eventID, kind, action string
			err = tx.QueryRow(ctx, `SELECT d.delivery_id::text, e.event_id::text, e.kind, COALESCE(e.notice_action, '')`+sessionInboxEligible+` FOR UPDATE OF d SKIP LOCKED LIMIT 1`, a.Repo, a.Inbox, dest.AllowLocal).Scan(&id, &eventID, &kind, &action)
			if errors.Is(err, pgx.ErrNoRows) {
				break
			}
			if err != nil {
				return res, err
			}
			if kind == "notice" && action == "none" && i < maxAutoSettleBatch {
				handled, skip, err := autoSettleActionNoneDeliveryTx(ctx, tx, id, eventID, dest.AllowLocal)
				if err != nil {
					return res, err
				}
				if skip {
					break
				}
				if handled {
					res.Settled = append(res.Settled, id)
				}
				continue
			}
			if a.Online && a.Metadata.State == "idle" {
				res.Ready = SessionInboxReadiness{DeliveryID: id, EventID: eventID, Action: action}
			}
			break
		}
		return res, nil
	}, guard)
}

func (s *Store) nativeInboxProfile(ref AgentSessionRef, dest Destination) error {
	if err := validEventDestination(dest); err != nil {
		return err
	}
	if err := ref.Validate(); err != nil {
		return err
	}
	if s.session != nil || s.channel.Instrumented {
		return failure("INVALID_REQUEST", "native inbox operations require the existing base profile and explicit session")
	}
	return nil
}

// The caller's association is checked before constructing this transaction-local
// view. It cannot begin a transaction or borrow another profile's session.
func (s *Store) readSessionInboxAttempt(ctx context.Context, tx pgx.Tx, id string, ref AgentSessionRef, dest Destination) (SessionInboxAttempt, error) {
	var out SessionInboxAttempt
	var deliveryID, leaseID string
	err := tx.QueryRow(ctx, `SELECT attempt_id::text,agent_id::text,execution_id::text,delivery_id::text,lease_id::text,created_at,finished_at,reason,native_turn_id,turn_exclusive,COALESCE(batch_root::text,''),batch_index,batch_limit,admission_kind,COALESCE(organic_root::text,''),digest_superseded_count FROM cairn.agent_session_attempt WHERE attempt_id=$1 AND owner=$2 AND agent_id=$3 AND execution_id=$4`, id, s.channel.Principal, ref.AgentID, ref.ExecutionID).Scan(&out.ID, &out.Session.AgentID, &out.Session.ExecutionID, &deliveryID, &leaseID, &out.CreatedAt, &out.FinishedAt, &out.Reason, &out.NativeTurnID, &out.TurnExclusive, &out.BatchRoot, &out.BatchIndex, &out.BatchLimit, &out.AdmissionKind, &out.OrganicRoot, &out.DigestSupersededCount)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, failure("NOT_FOUND", "native inbox attempt not found for this execution")
	}
	if err != nil {
		return out, err
	}
	var captureGap bool
	if err = tx.QueryRow(ctx, `SELECT capture_gap FROM cairn.agent_session_attempt WHERE attempt_id=$1`, id).Scan(&captureGap); err != nil {
		return out, err
	}
	out.CaptureGap = captureGap
	out.Cancel, out.TurnStopState, out.CaptureState, out.TerminalScan, err = readSessionCancel(tx.QueryRow(ctx, `SELECT cancel_requested_at,cancel_confirmed_at,cancel_by,cancel_reason,turn_stop_state,capture_state,terminal_scan FROM cairn.agent_session_attempt WHERE attempt_id=$1`, id))
	if err != nil {
		return out, err
	}
	if out.Tools, err = readSessionTools(ctx, tx, id); err != nil {
		return out, err
	}
	if out.AdmissionKind == "digest" {
		if err = lockDigestAttemptDeliveriesByUUIDTx(ctx, tx, id, deliveryID); err != nil {
			return out, err
		}
		_ = tx.QueryRow(ctx, `SELECT COALESCE(requested_delivery_id::text,'') FROM cairn.agent_session_poll WHERE attempt_id=$1 AND requested_delivery_id IS NOT NULL AND requested_delivery_id<>$2 LIMIT 1`, id, deliveryID).Scan(&out.WakeDeliveryID)
	}
	view := *s
	view.channel.Principal = "agent/" + ref.AgentID
	out.Delivery, err = view.ownedDelivery(ctx, tx, deliveryID, dest)
	out.Delivery.LeaseID = leaseID
	if err != nil || out.AdmissionKind != "digest" {
		return out, err
	}
	out.DigestItems, err = view.readSessionDigestItems(ctx, tx, id, dest)
	return out, err
}

func (s *Store) ClaimSessionInbox(ctx context.Context, req SessionInboxClaim, dest Destination) (SessionInboxResult, error) {
	var out SessionInboxResult
	if err := s.nativeInboxProfile(req.Session, dest); err != nil {
		return out, err
	}
	if validID(req.RequestID) != nil {
		return out, failure("INVALID_REQUEST", "stable native inbox claim UUID required")
	}
	if (req.DeliveryID != "" && validID(req.DeliveryID) != nil) ||
		(req.NativeTurnID != "" && (strings.TrimSpace(req.NativeTurnID) == "" || len(req.NativeTurnID) > 256 || strings.ContainsRune(req.NativeTurnID, 0))) ||
		(req.DeliveryID != "" && req.NativeTurnID == "") {
		return out, failure("INVALID_REQUEST", "a native turn ID must be bounded, and an exact delivery claim requires its native turn ID")
	}
	if req.TurnExclusive && (req.NativeTurnID == "" || req.DeliveryID == "") {
		return out, failure("INVALID_REQUEST", "an exclusive turn binding requires an exact delivery and its native turn ID")
	}
	if req.DigestWindowSeconds < 0 || req.DigestWindowSeconds > 3600 {
		return out, failure("INVALID_REQUEST", "digest_window_seconds must be between 0 and 3600")
	}
	if (req.DigestFlushNow || req.DigestOnly) && req.DigestWindowSeconds == 0 {
		return out, failure("INVALID_REQUEST", "digest_flush_now and digest_only require a positive digest_window_seconds")
	}
	if req.DigestOnly && req.DeliveryID != "" {
		return out, failure("INVALID_REQUEST", "digest_only cannot be combined with an exact delivery_id")
	}
	if req.TurnExclusive && req.DigestWindowSeconds > 0 {
		return out, failure("INVALID_REQUEST", "an exclusive turn binding cannot use notice digest admission")
	}
	// A pinned-but-unproven turn keeps its 048 semantics: the binding is
	// valid for delivery identity, but without an explicit exclusivity
	// attestation the attempt never qualifies for cancellation.
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	if err = lock(ctx, tx, "native-inbox-claim:"+req.RequestID); err != nil {
		return out, err
	}
	a, err := currentAgentSession(ctx, tx, req.Session, s.channel.Principal, s.channel.Repo, true, dest)
	if err != nil {
		return out, err
	}
	if err = activeAgent(a); err != nil {
		return out, err
	}
	if a.Metadata.DeliveryMode != "existing-session" {
		return out, failure("INVALID_REQUEST", "native inbox requires existing-session delivery mode")
	}
	var owner, agentID, executionID, requestedDelivery, nativeTurn string
	var prior *string
	var priorExclusive *bool
	var priorAutoSettle, priorFlushNow, priorDigestOnly bool
	var priorDigestWindow int
	err = tx.QueryRow(ctx, `SELECT owner,agent_id::text,execution_id::text,attempt_id::text,COALESCE(requested_delivery_id::text,''),native_turn_id,turn_exclusive,auto_settle_action_none,digest_window_seconds,digest_flush_now,digest_only FROM cairn.agent_session_poll WHERE request_id=$1`, req.RequestID).Scan(&owner, &agentID, &executionID, &prior, &requestedDelivery, &nativeTurn, &priorExclusive, &priorAutoSettle, &priorDigestWindow, &priorFlushNow, &priorDigestOnly)
	if err == nil {
		if owner != s.channel.Principal || agentID != req.Session.AgentID || executionID != req.Session.ExecutionID {
			return out, failure("IDEMPOTENCY_CONFLICT", "native poll UUID already belongs to another execution")
		}
		if requestedDelivery != req.DeliveryID || nativeTurn != req.NativeTurnID || priorExclusive == nil || *priorExclusive != req.TurnExclusive || priorAutoSettle != req.AutoSettleActionNone || priorDigestWindow != req.DigestWindowSeconds || priorFlushNow != req.DigestFlushNow || priorDigestOnly != req.DigestOnly {
			return out, failure("IDEMPOTENCY_CONFLICT", "native poll UUID already binds another delivery, turn or exclusivity attestation")
		}
		if prior == nil {
			return out, tx.Commit(ctx)
		}
		attempt, err := s.readSessionInboxAttempt(ctx, tx, req.RequestID, req.Session, dest)
		if err != nil {
			return out, err
		}
		return SessionInboxResult{&attempt}, tx.Commit(ctx)
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return out, err
	}
	commit := func(attempt *SessionInboxAttempt) (SessionInboxResult, error) {
		var id *string
		if attempt != nil {
			id = &attempt.ID
		}
		if _, err := tx.Exec(ctx, `INSERT INTO cairn.agent_session_poll(request_id,agent_id,execution_id,attempt_id,requested_delivery_id,native_turn_id,turn_exclusive,auto_settle_action_none,digest_window_seconds,digest_flush_now,digest_only) VALUES($1,$2,$3,$4,NULLIF($5,'')::uuid,$6,$7,$8,$9,$10,$11)`, req.RequestID, req.Session.AgentID, req.Session.ExecutionID, id, req.DeliveryID, req.NativeTurnID, req.TurnExclusive, req.AutoSettleActionNone, req.DigestWindowSeconds, req.DigestFlushNow, req.DigestOnly); err != nil {
			return out, err
		}
		return SessionInboxResult{attempt}, tx.Commit(ctx)
	}
	var exists bool
	if exists, err = inboxOccupied(ctx, tx, a.AgentID, a.Inbox); err != nil {
		return out, err
	}
	if exists {
		return commit(nil)
	}
	if _, err = expireRequestDeliveries(ctx, tx, s, a.Repo, a.Inbox, dest.AllowLocal); err != nil {
		return out, err
	}
	if req.DigestWindowSeconds > 0 {
		return s.claimSessionDigestAttempt(ctx, tx, a, req, dest, commit)
	}
	var id string
	limit := 1
	if req.AutoSettleActionNone {
		limit = maxAutoSettleBatch + 1
	}
	for i := 0; i < limit; i++ {
		var eventID, kind, action string
		err = tx.QueryRow(ctx, `SELECT d.delivery_id::text, e.event_id::text, e.kind, COALESCE(e.notice_action, '')`+sessionInboxEligible+` FOR UPDATE OF d SKIP LOCKED LIMIT 1`, a.Repo, a.Inbox, dest.AllowLocal).Scan(&id, &eventID, &kind, &action)
		if errors.Is(err, pgx.ErrNoRows) {
			return commit(nil)
		}
		if err != nil {
			return out, err
		}
		if req.AutoSettleActionNone && kind == "notice" && action == "none" {
			if i == maxAutoSettleBatch {
				return commit(nil)
			}
			_, skip, err := autoSettleActionNoneDeliveryTx(ctx, tx, id, eventID, dest.AllowLocal)
			if err != nil {
				return out, err
			}
			if skip {
				return commit(nil)
			}
			if req.DeliveryID != "" && id == req.DeliveryID {
				return commit(nil)
			}
			id = ""
			continue
		}
		break
	}
	if id == "" || (req.DeliveryID != "" && id != req.DeliveryID) {
		return commit(nil) // A stale wake must not acquire a different request.
	}
	lease := uuid.NewString()
	if err = leaseDelivery(ctx, tx, id, lease, nativeLeaseTTL); err != nil {
		return out, err
	}
	if err = openAttempt(ctx, tx, nativeAttemptOpen{AttemptID: req.RequestID, AgentID: a.AgentID, ExecutionID: a.ExecutionID, Repo: a.Repo, DeliveryID: id, LeaseID: lease, NativeTurnID: req.NativeTurnID, TurnExclusive: req.TurnExclusive}); err != nil {
		if isTurnAlreadyBound(err) {
			// The pinned native turn already owns an unfinished attempt: a
			// joined or shared prompt is not per-request identity.
			return out, failure("TURN_ALREADY_BOUND", "this native turn already owns a request; it cannot claim another delivery")
		}
		return out, err
	}
	attempt, err := s.readSessionInboxAttempt(ctx, tx, req.RequestID, req.Session, dest)
	if err != nil {
		return out, err
	}
	return commit(&attempt)
}

// Reconciliation is a trusted-host report. It accepts an obsolete execution only
// to close that execution's own attempt after a confirmed process/turn end. It
// never transfers work or establishes that external effects did not happen.
func (s *Store) ReconcileSessionInbox(ctx context.Context, req SessionInboxReconcile, dest Destination) (SessionInboxAttempt, error) {
	if err := s.nativeInboxProfile(req.Session, dest); err != nil {
		return SessionInboxAttempt{}, err
	}
	if validID(req.AttemptID) != nil {
		return SessionInboxAttempt{}, failure("INVALID_REQUEST", "native inbox attempt UUID required")
	}
	if req.Reason != "delivery_completed" && req.Reason != "process_exited" && req.Reason != "turn_ended" && req.Reason != "cancel_confirmed" && req.Reason != "exclusivity_revoked" {
		return SessionInboxAttempt{}, failure("INVALID_REQUEST", "native reconciliation requires completion, an observed process/turn end, or a confirmed cancellation cleanup")
	}
	guard := func(tx pgx.Tx) error {
		var repo string
		err := tx.QueryRow(ctx, `SELECT repo FROM cairn.agent_session WHERE agent_id=$1 AND owner=$2 AND (visibility='hosted' OR $3) FOR UPDATE`, req.Session.AgentID, s.channel.Principal, dest.AllowLocal).Scan(&repo)
		if errors.Is(err, pgx.ErrNoRows) {
			return failure("NOT_FOUND", "native session not found for this profile")
		}
		if err != nil {
			return err
		}
		if err = s.checkRepo(repo); err != nil {
			return err
		}
		if err = lock(ctx, tx, "session-attempt:"+req.AttemptID); err != nil {
			return err
		}
		_, err = s.readSessionInboxAttempt(ctx, tx, req.AttemptID, req.Session, dest)
		return err
	}
	return mutate(ctx, s, "session-inbox-reconcile", req.RequestID, req, func(tx pgx.Tx) (SessionInboxAttempt, error) {
		attempt, err := s.lockedSessionAttempt(ctx, tx, req.AttemptID, req.Session, dest)
		if err != nil {
			return attempt, err
		}
		if attempt.FinishedAt != nil {
			if attempt.Reason != req.Reason {
				return attempt, failure("VERSION_CONFLICT", "native inbox attempt already closed with a different reason")
			}
			return attempt, nil
		}
		settled, err := settleNativeCleanup(ctx, tx, s, attempt, req.RequestID, req.Reason)
		if err != nil {
			return attempt, err
		}
		if settled {
			return s.readSessionInboxAttempt(ctx, tx, req.AttemptID, req.Session, dest)
		}
		if err = reconcileAttemptDeliveries(ctx, tx, attempt, req.Reason); err != nil {
			return attempt, err
		}
		if _, err = finishAttempt(ctx, tx, req.AttemptID, req.Reason, attemptClose{}); err != nil {
			return attempt, err
		}
		return s.readSessionInboxAttempt(ctx, tx, req.AttemptID, req.Session, dest)
	}, guard)
}

func isTurnAlreadyBound(err error) bool {
	var pgErr *pgconn.PgError
	return errors.As(err, &pgErr) && pgErr.Code == "23505" && pgErr.ConstraintName == "agent_session_one_exclusive_turn"
}
