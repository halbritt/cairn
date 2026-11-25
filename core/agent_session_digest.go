package core

import (
	"context"
	"errors"
	"sort"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

const (
	maxDigestItems         = 64
	maxDigestCandidateScan = 256
	defaultDigestWindowSec = 300
)

const maxDigestOverflowRefs = 16

type SessionDigestItem struct {
	Ordinal  int           `json:"ordinal"`
	TopicKey string        `json:"topic_key,omitempty"`
	Delivery AgentDelivery `json:"delivery"`
}

type SessionDigestOverflowRef struct {
	DeliveryID string `json:"delivery_id"`
	EventID    string `json:"event_id"`
	Sender     string `json:"sender,omitempty"`
	TopicKey   string `json:"topic_key,omitempty"`
	RecordID   string `json:"record_id,omitempty"`
	Version    int64  `json:"version,omitempty"`
	Kind       string `json:"kind"`
}

type SessionInboxDigestReadyRequest struct {
	Session              AgentSessionRef `json:"session"`
	DigestWindowSeconds  int             `json:"digest_window_seconds"`
	DigestFlushNow       bool            `json:"digest_flush_now,omitempty"`
	AutoSettleActionNone bool            `json:"auto_settle_action_none,omitempty"`
}

type SessionInboxDigestReady struct {
	DeliveryID string `json:"delivery_id,omitempty"`
	EventID    string `json:"event_id,omitempty"`
	Action     string `json:"action,omitempty"`
	Kind       string `json:"kind,omitempty"`
	Digest     bool   `json:"digest,omitempty"`
	Count      int    `json:"count,omitempty"`
}

type SessionInboxDigestRenew struct {
	Session      AgentSessionRef `json:"session"`
	AttemptID    string          `json:"attempt_id"`
	LeaseSeconds int             `json:"lease_seconds,omitempty"`
}

type SessionDigestItemOutcome struct {
	DeliveryID  string `json:"delivery_id"`
	LeaseID     string `json:"lease_id"`
	Disposition string `json:"disposition"`
	Code        string `json:"code,omitempty"`
}

type SessionInboxDigestSettle struct {
	RequestID string                     `json:"request_id"`
	Session   AgentSessionRef            `json:"session"`
	AttemptID string                     `json:"attempt_id"`
	Outcomes  []SessionDigestItemOutcome `json:"outcomes"`
}

type SessionDigestSettledItem struct {
	Ordinal             int    `json:"ordinal"`
	DeliveryID          string `json:"delivery_id"`
	State               string `json:"state"`
	Code                string `json:"code,omitempty"`
	CompletedAfterLease bool   `json:"completed_after_lease,omitempty"`
	AlreadySettled      bool   `json:"already_settled,omitempty"`
}

type SessionInboxDigestSettleResult struct {
	AttemptID string                     `json:"attempt_id"`
	Items     []SessionDigestSettledItem `json:"items"`
	Remaining int                        `json:"remaining"`
}

const sessionInboxPriorityFilter = ` AND (e.kind<>'notice' OR e.blocker OR COALESCE(e.notice_policy->>'urgency','')='urgent' OR COALESCE(e.notice_action,'')='decide')`

const sessionInboxFYIFilter = ` AND e.kind='notice' AND NOT e.blocker AND COALESCE(e.notice_policy->>'urgency','')<>'urgent' AND COALESCE(e.notice_action,'')<>'decide'`

const digestTopicKeyExpr = `CASE WHEN COALESCE(e.notice_supersedes,'')<>'' THEN e.notice_supersedes WHEN e.destination_type='topic' THEN 'topic/'||e.destination_name ELSE '' END`

type digestCandidate struct {
	deliveryID       string
	eventID          string
	action           string
	publisher        string
	topicKey         string
	sensitivity      string
	payloadAvailable bool
	availableAt      time.Time
	createdAt        time.Time
}

type supersededCandidate struct {
	cand       digestCandidate
	winnerID   string
	winnerSens string
}

func selectDigestBatch(candidates []digestCandidate) ([]digestCandidate, []supersededCandidate) {
	active := make([]digestCandidate, 0, min(len(candidates), maxDigestItems))
	var superseded []supersededCandidate
	for _, cand := range candidates {
		replaced := false
		if cand.payloadAvailable && cand.topicKey != "" {
			next := make([]digestCandidate, 0, len(active))
			for _, prev := range active {
				if prev.publisher == cand.publisher && prev.topicKey == cand.topicKey &&
					(cand.sensitivity == "shareable" || prev.sensitivity == "local") {
					superseded = append(superseded, supersededCandidate{cand: prev, winnerID: cand.eventID, winnerSens: cand.sensitivity})
					replaced = true
					continue
				}
				next = append(next, prev)
			}
			active = next
		}
		if replaced || len(active) < maxDigestItems {
			active = append(active, cand)
		}
	}
	return active, superseded
}

func lockDigestAttemptDeliveriesByUUIDTx(ctx context.Context, tx pgx.Tx, attemptID, primaryDeliveryID string) error {
	rows, err := tx.Query(ctx, `SELECT delivery_id::text FROM cairn.agent_session_digest_item WHERE attempt_id=$1`, attemptID)
	if err != nil {
		return err
	}
	seen := map[string]struct{}{primaryDeliveryID: {}}
	for rows.Next() {
		var dID string
		if err = rows.Scan(&dID); err != nil {
			rows.Close()
			return err
		}
		seen[dID] = struct{}{}
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return err
	}
	ids := make([]string, 0, len(seen))
	for dID := range seen {
		ids = append(ids, dID)
	}
	sort.Strings(ids)
	_, err = lockDeliveryStatesTx(ctx, tx, ids)
	return err
}

const supersededLineageCTE = `WITH RECURSIVE superseded_lineage(delivery_id,event_id,available_at,created_at) AS (
	SELECT sd.delivery_id::text,sd.event_id::text,sd.available_at,se.created_at
	FROM cairn.agent_delivery sd JOIN cairn.agent_event se USING(event_id)
	WHERE sd.consumer=$1 AND sd.state='ignored' AND sd.code='superseded' AND sd.superseded_by=ANY($2::uuid[]) AND (se.sensitivity='shareable' OR $3)
	UNION
	SELECT sd.delivery_id::text,sd.event_id::text,sd.available_at,se.created_at
	FROM cairn.agent_delivery sd JOIN cairn.agent_event se USING(event_id)
	JOIN superseded_lineage sl ON sd.superseded_by::text=sl.event_id
	WHERE sd.consumer=$1 AND sd.state='ignored' AND sd.code='superseded' AND (se.sensitivity='shareable' OR $3)
) `

func (s *Store) readSessionDigestItems(ctx context.Context, tx pgx.Tx, attemptID string, dest Destination) ([]SessionDigestItem, error) {
	rows, err := tx.Query(ctx, `SELECT ordinal,delivery_id::text,lease_id::text,topic_key FROM cairn.agent_session_digest_item WHERE attempt_id=$1 ORDER BY ordinal`, attemptID)
	if err != nil {
		return nil, err
	}
	type rawItem struct {
		ordinal    int
		deliveryID string
		leaseID    string
		topicKey   string
	}
	var raw []rawItem
	for rows.Next() {
		var r rawItem
		if err = rows.Scan(&r.ordinal, &r.deliveryID, &r.leaseID, &r.topicKey); err != nil {
			rows.Close()
			return nil, err
		}
		raw = append(raw, r)
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return nil, err
	}
	items := make([]SessionDigestItem, 0, len(raw))
	for _, r := range raw {
		d, err := s.ownedDelivery(ctx, tx, r.deliveryID, dest)
		if err != nil {
			return nil, err
		}
		d.LeaseID = r.leaseID
		items = append(items, SessionDigestItem{Ordinal: r.ordinal, TopicKey: r.topicKey, Delivery: d})
	}
	return items, nil
}

func loadDigestCandidatesTx(ctx context.Context, tx pgx.Tx, repo, inbox string, allowLocal, excludeActionNone, forUpdate bool) ([]digestCandidate, error) {
	q := `SELECT d.delivery_id::text,e.event_id::text,COALESCE(e.notice_action,''),e.publisher,` + digestTopicKeyExpr + `,e.sensitivity,COALESCE(m.lifecycle='active' AND v.payload_deleted_by IS NULL,false),d.available_at,e.created_at FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) LEFT JOIN cairn.memory_record m ON m.record_id=e.record_id LEFT JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=e.version WHERE e.repo=$1 AND d.consumer=$2 AND (e.sensitivity='shareable' OR $3) AND d.available_at<=clock_timestamp() AND e.task_deadline IS NULL AND ` + requestAdmissionOpen + ` AND ` + wakeHold + ` AND ` + sessionInboxHold + ` AND ` + waitNotificationAdmissionOpen + ` AND (d.state='pending' OR (d.state='leased' AND d.lease_until<=clock_timestamp()))` + sessionInboxFYIFilter
	if excludeActionNone {
		q += ` AND NOT (` + sessionInboxAutoSettleSafe + `)`
	}
	q += ` ORDER BY e.position`
	if forUpdate {
		q += ` FOR UPDATE OF d SKIP LOCKED`
	}
	q += ` LIMIT $4`
	rows, err := tx.Query(ctx, q, repo, inbox, allowLocal, maxDigestCandidateScan)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []digestCandidate
	for rows.Next() {
		var c digestCandidate
		if err = rows.Scan(&c.deliveryID, &c.eventID, &c.action, &c.publisher, &c.topicKey, &c.sensitivity, &c.payloadAvailable, &c.availableAt, &c.createdAt); err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

func loadDigestOverflowTx(ctx context.Context, tx pgx.Tx, repo, inbox string, allowLocal bool) (int, []SessionDigestOverflowRef, error) {
	where := ` FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.consumer=$2 AND (e.sensitivity='shareable' OR $3) AND d.available_at<=clock_timestamp() AND e.task_deadline IS NULL AND ` + requestAdmissionOpen + ` AND ` + wakeHold + ` AND ` + sessionInboxHold + ` AND ` + waitNotificationAdmissionOpen + ` AND (d.state='pending' OR (d.state='leased' AND d.lease_until<=clock_timestamp()))` + sessionInboxFYIFilter
	var total int
	if err := tx.QueryRow(ctx, `SELECT count(*)`+where, repo, inbox, allowLocal).Scan(&total); err != nil {
		return 0, nil, err
	}
	if total == 0 {
		return 0, nil, nil
	}
	rows, err := tx.Query(ctx, `SELECT d.delivery_id::text,e.event_id::text,e.publisher,`+digestTopicKeyExpr+`,COALESCE(e.record_id::text,''),COALESCE(e.version,0),e.kind`+where+` ORDER BY e.position LIMIT $4`, repo, inbox, allowLocal, maxDigestOverflowRefs)
	if err != nil {
		return 0, nil, err
	}
	defer rows.Close()
	refs := make([]SessionDigestOverflowRef, 0, min(total, maxDigestOverflowRefs))
	for rows.Next() {
		var r SessionDigestOverflowRef
		if err := rows.Scan(&r.DeliveryID, &r.EventID, &r.Sender, &r.TopicKey, &r.RecordID, &r.Version, &r.Kind); err != nil {
			return 0, nil, err
		}
		refs = append(refs, r)
	}
	if err := rows.Err(); err != nil {
		return 0, nil, err
	}
	return total, refs, nil
}

func digestDueTx(ctx context.Context, tx pgx.Tx, repo, inbox string, allowLocal bool, candidates []digestCandidate, windowSeconds int, flushNow bool) (bool, error) {
	if len(candidates) == 0 {
		return false, nil
	}
	if flushNow {
		return true, nil
	}
	minAvail, minCreated := candidates[0].availableAt, candidates[0].createdAt
	candEventIDs := make([]string, len(candidates))
	candEventIDs[0] = candidates[0].eventID
	for i, c := range candidates[1:] {
		candEventIDs[i+1] = c.eventID
		if c.availableAt.Before(minAvail) {
			minAvail = c.availableAt
		}
		if c.createdAt.Before(minCreated) {
			minCreated = c.createdAt
		}
	}
	var linMinAvail, linMinCreated *time.Time
	if err := tx.QueryRow(ctx, supersededLineageCTE+`SELECT min(available_at),min(created_at) FROM superseded_lineage`, inbox, candEventIDs, allowLocal).Scan(&linMinAvail, &linMinCreated); err != nil {
		return false, err
	}
	if linMinAvail != nil && linMinAvail.Before(minAvail) {
		minAvail = *linMinAvail
	}
	if linMinCreated != nil && linMinCreated.Before(minCreated) {
		minCreated = *linMinCreated
	}
	var due bool
	err := tx.QueryRow(ctx, `SELECT ($4::timestamptz + make_interval(secs => $5) <= clock_timestamp()) OR EXISTS(SELECT 1 FROM cairn.agent_delivery ud JOIN cairn.agent_event ue USING(event_id) WHERE ue.repo=$1 AND ud.consumer=$2 AND (ue.sensitivity='shareable' OR $3) AND ue.created_at>=$6 AND (ue.kind='request' OR ue.kind='response' OR ue.blocker OR ue.kind='blocker' OR COALESCE(ue.notice_policy->>'urgency','')='urgent' OR COALESCE(ue.notice_action,'')='decide'))`, repo, inbox, allowLocal, minAvail, float64(windowSeconds), minCreated).Scan(&due)
	return due, err
}

func (s *Store) SessionInboxDigestReady(ctx context.Context, req SessionInboxDigestReadyRequest, dest Destination) (SessionInboxDigestReady, error) {
	var out SessionInboxDigestReady
	if err := s.nativeInboxProfile(req.Session, dest); err != nil {
		return out, err
	}
	if req.DigestWindowSeconds <= 0 || req.DigestWindowSeconds > 3600 {
		return out, failure("INVALID_REQUEST", "digest_window_seconds must be between 1 and 3600")
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	a, err := currentAgentSession(ctx, tx, req.Session, s.channel.Principal, s.channel.Repo, false, dest)
	if err != nil {
		return out, err
	}
	if err = activeAgent(a); err != nil {
		return out, err
	}
	if !a.Online || a.Metadata.DeliveryMode != "existing-session" || a.Metadata.State != "idle" {
		return out, nil
	}
	if held, err := inboxOccupied(ctx, tx, a.AgentID, a.Inbox); err != nil || held {
		return out, err
	}
	if req.AutoSettleActionNone {
		var headID, headEventID, headKind, headAction string
		var headAutoSafe bool
		err = tx.QueryRow(ctx, `SELECT d.delivery_id::text,e.event_id::text,e.kind,COALESCE(e.notice_action,''),(`+sessionInboxAutoSettleSafe+`)`+sessionInboxEligible+` LIMIT 1`, a.Repo, a.Inbox, dest.AllowLocal).Scan(&headID, &headEventID, &headKind, &headAction, &headAutoSafe)
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return out, err
		}
		if err == nil && headKind == "notice" && headAction == "none" && headAutoSafe {
			return SessionInboxDigestReady{DeliveryID: headID, EventID: headEventID, Action: "none", Kind: "notice"}, nil
		}
	}
	err = tx.QueryRow(ctx, `SELECT d.delivery_id::text,e.event_id::text,COALESCE(e.notice_action,''),e.kind`+sessionInboxEligibleBase+sessionInboxPriorityFilter+` ORDER BY e.position LIMIT 1`, a.Repo, a.Inbox, dest.AllowLocal).Scan(&out.DeliveryID, &out.EventID, &out.Action, &out.Kind)
	if err == nil {
		out.Count = 1
		return out, nil
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return out, err
	}
	candidates, err := loadDigestCandidatesTx(ctx, tx, a.Repo, a.Inbox, dest.AllowLocal, req.AutoSettleActionNone, false)
	if err != nil || len(candidates) == 0 {
		return out, err
	}
	due, err := digestDueTx(ctx, tx, a.Repo, a.Inbox, dest.AllowLocal, candidates, req.DigestWindowSeconds, req.DigestFlushNow)
	if err != nil || !due {
		return out, err
	}
	active, _ := selectDigestBatch(candidates)
	if len(active) == 0 {
		return out, nil
	}
	return SessionInboxDigestReady{DeliveryID: active[0].deliveryID, EventID: active[0].eventID, Action: active[0].action, Kind: "notice", Digest: true, Count: len(active)}, nil
}

func (s *Store) claimSessionDigestAttempt(ctx context.Context, tx pgx.Tx, a AgentInstance, req SessionInboxClaim, dest Destination, commit func(*SessionInboxAttempt) (SessionInboxResult, error)) (SessionInboxResult, error) {
	var out SessionInboxResult
	if req.AutoSettleActionNone {
		for i := 0; i < maxAutoSettleBatch; i++ {
			var noneID, noneEventID string
			err := tx.QueryRow(ctx, `SELECT d.delivery_id::text,e.event_id::text`+sessionInboxEligibleBase+` AND (`+sessionInboxAutoSettleSafe+`) ORDER BY e.position FOR UPDATE OF d SKIP LOCKED LIMIT 1`, a.Repo, a.Inbox, dest.AllowLocal).Scan(&noneID, &noneEventID)
			if errors.Is(err, pgx.ErrNoRows) {
				break
			}
			if err != nil {
				return out, err
			}
			outcome, err := autoSettleActionNoneDeliveryTx(ctx, tx, noneID, noneEventID, dest.AllowLocal)
			if err != nil {
				return out, err
			}
			if outcome == autoSettleLocalHidden || (req.DeliveryID != "" && noneID == req.DeliveryID) {
				return commit(nil)
			}
			if outcome == autoSettleUnsafe {
				break
			}
		}
	}
	var priorityID string
	err := tx.QueryRow(ctx, `SELECT d.delivery_id::text`+sessionInboxEligibleBase+sessionInboxPriorityFilter+` ORDER BY e.position FOR UPDATE OF d SKIP LOCKED LIMIT 1`, a.Repo, a.Inbox, dest.AllowLocal).Scan(&priorityID)
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return out, err
	}
	if err == nil {
		if req.DigestOnly || (req.DeliveryID != "" && priorityID != req.DeliveryID) {
			return commit(nil)
		}
		lease := uuid.NewString()
		if err = leaseDelivery(ctx, tx, priorityID, lease, nativeLeaseTTL); err != nil {
			return out, err
		}
		if err = openAttempt(ctx, tx, nativeAttemptOpen{AttemptID: req.RequestID, AgentID: a.AgentID, ExecutionID: a.ExecutionID, Repo: a.Repo, DeliveryID: priorityID, LeaseID: lease, NativeTurnID: req.NativeTurnID}); err != nil {
			if isTurnAlreadyBound(err) {
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
	candidates, err := loadDigestCandidatesTx(ctx, tx, a.Repo, a.Inbox, dest.AllowLocal, req.AutoSettleActionNone, true)
	if err != nil {
		return out, err
	}
	if len(candidates) == 0 {
		return commit(nil)
	}
	due, err := digestDueTx(ctx, tx, a.Repo, a.Inbox, dest.AllowLocal, candidates, req.DigestWindowSeconds, req.DigestFlushNow)
	if err != nil {
		return out, err
	}
	if !due {
		return commit(nil)
	}
	active, superseded := selectDigestBatch(candidates)
	if len(active) == 0 {
		return commit(nil)
	}
	winnerEventIDs := make([]string, len(active))
	for i, item := range active {
		winnerEventIDs[i] = item.eventID
	}
	if req.DeliveryID != "" && active[0].deliveryID != req.DeliveryID {
		matchedWake := false
		for _, sup := range superseded {
			if sup.cand.deliveryID == req.DeliveryID {
				matchedWake = true
				break
			}
		}
		if !matchedWake {
			if err := tx.QueryRow(ctx, supersededLineageCTE+`SELECT EXISTS(SELECT 1 FROM superseded_lineage WHERE delivery_id=$4)`, a.Inbox, winnerEventIDs, dest.AllowLocal, req.DeliveryID).Scan(&matchedWake); err != nil {
				return out, err
			}
		}
		if !matchedWake {
			return commit(nil)
		}
	}
	for _, sup := range superseded {
		if _, err := tx.Exec(ctx, `UPDATE cairn.agent_delivery sd SET superseded_by=$1 FROM cairn.agent_event se WHERE sd.event_id=se.event_id AND sd.consumer=$2 AND sd.state='ignored' AND sd.code='superseded' AND sd.superseded_by=$3 AND ($4='shareable' OR se.sensitivity='local')`, sup.winnerID, a.Inbox, sup.cand.eventID, sup.winnerSens); err != nil {
			return out, err
		}
		if !sup.cand.payloadAvailable {
			if err := settleDelivery(ctx, tx, sup.cand.deliveryID, "failed", "source_unavailable", nil, nil, false); err != nil {
				return out, err
			}
			continue
		}
		if _, err := tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='ignored',lease_id=NULL,lease_until=NULL,completed_at=clock_timestamp(),code='superseded',superseded_by=$2,completed_after_lease=false WHERE delivery_id=$1`, sup.cand.deliveryID, sup.winnerID); err != nil {
			return out, err
		}
	}
	leases := make([]string, len(active))
	for i, item := range active {
		leases[i] = uuid.NewString()
		if err := leaseDelivery(ctx, tx, item.deliveryID, leases[i], nativeLeaseTTL); err != nil {
			return out, err
		}
	}
	var supersededCount int
	if err := tx.QueryRow(ctx, supersededLineageCTE+`SELECT count(*) FROM superseded_lineage`, a.Inbox, winnerEventIDs, dest.AllowLocal).Scan(&supersededCount); err != nil {
		return out, err
	}
	if err := openAttempt(ctx, tx, nativeAttemptOpen{AttemptID: req.RequestID, AgentID: a.AgentID, ExecutionID: a.ExecutionID, Repo: a.Repo, DeliveryID: active[0].deliveryID, LeaseID: leases[0], NativeTurnID: req.NativeTurnID, AdmissionKind: "digest", DigestSupersededCount: supersededCount}); err != nil {
		return out, err
	}
	for i, item := range active {
		if _, err := tx.Exec(ctx, `INSERT INTO cairn.agent_session_digest_item(attempt_id,ordinal,delivery_id,lease_id,topic_key) VALUES($1,$2,$3,$4,$5)`, req.RequestID, i+1, item.deliveryID, leases[i], item.topicKey); err != nil {
			return out, err
		}
	}
	attempt, err := s.readSessionInboxAttempt(ctx, tx, req.RequestID, req.Session, dest)
	if err != nil {
		return out, err
	}
	if req.DeliveryID != "" && req.DeliveryID != active[0].deliveryID {
		attempt.WakeDeliveryID = req.DeliveryID
	}
	return commit(&attempt)
}

type releasedDeliveryTransition struct {
	deliveryID  string
	eventID     string
	stateBefore string
	stateAfter  string
}

func sortedAttemptDeliveryIDs(attempt SessionInboxAttempt) []string {
	if attempt.AdmissionKind != "digest" || len(attempt.DigestItems) == 0 {
		return []string{attempt.Delivery.DeliveryID}
	}
	ids := make([]string, len(attempt.DigestItems))
	for i, item := range attempt.DigestItems {
		ids[i] = item.Delivery.DeliveryID
	}
	sort.Strings(ids)
	return ids
}

func lockDeliveryStatesTx(ctx context.Context, tx pgx.Tx, ids []string) (map[string]string, error) {
	rows, err := tx.Query(ctx, `SELECT delivery_id::text,state FROM cairn.agent_delivery WHERE delivery_id=ANY($1::uuid[]) ORDER BY delivery_id FOR UPDATE`, ids)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	states := make(map[string]string, len(ids))
	for rows.Next() {
		var id, st string
		if err = rows.Scan(&id, &st); err != nil {
			return nil, err
		}
		states[id] = st
	}
	return states, rows.Err()
}

func reconcileAttemptDeliveries(ctx context.Context, tx pgx.Tx, attempt SessionInboxAttempt, reason string) error {
	ids := sortedAttemptDeliveryIDs(attempt)
	states, err := lockDeliveryStatesTx(ctx, tx, ids)
	if err != nil {
		return err
	}
	for _, id := range ids {
		st := states[id]
		if st == "pending" || st == "leased" {
			if reason == "delivery_completed" {
				if attempt.AdmissionKind == "digest" {
					return failure("DELIVERY_ACTIVE", "native digest has unsettled deliveries")
				}
				return failure("DELIVERY_ACTIVE", "native delivery has not been explicitly completed")
			}
		}
	}
	if reason == "delivery_completed" {
		return nil
	}
	if attempt.AdmissionKind != "digest" || len(attempt.DigestItems) == 0 {
		st := states[attempt.Delivery.DeliveryID]
		if st == "pending" || st == "leased" {
			_, err := failDelivery(ctx, tx, attempt.Delivery.DeliveryID, "processing_failed", nil)
			return err
		}
		return nil
	}
	for _, item := range attempt.DigestItems {
		st := states[item.Delivery.DeliveryID]
		if st == "pending" || st == "leased" {
			if _, err := failDelivery(ctx, tx, item.Delivery.DeliveryID, "processing_failed", nil); err != nil {
				return err
			}
		}
	}
	return nil
}

func releaseDigestOrPrimaryDeliveries(ctx context.Context, tx pgx.Tx, attemptID, primaryDeliveryID, primaryEventID string, control *stopControl) ([]releasedDeliveryTransition, error) {
	rows, err := tx.Query(ctx, `SELECT d.delivery_id::text,d.event_id::text FROM cairn.agent_session_digest_item di JOIN cairn.agent_delivery d USING(delivery_id) WHERE di.attempt_id=$1 ORDER BY d.delivery_id`, attemptID)
	if err != nil {
		return nil, err
	}
	var targets []releasedDeliveryTransition
	for rows.Next() {
		var t releasedDeliveryTransition
		if err = rows.Scan(&t.deliveryID, &t.eventID); err != nil {
			rows.Close()
			return nil, err
		}
		targets = append(targets, t)
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return nil, err
	}
	if len(targets) == 0 {
		targets = []releasedDeliveryTransition{{deliveryID: primaryDeliveryID, eventID: primaryEventID}}
	}
	for i := range targets {
		if err = tx.QueryRow(ctx, `SELECT state FROM cairn.agent_delivery WHERE delivery_id=$1 FOR UPDATE`, targets[i].deliveryID).Scan(&targets[i].stateBefore); err != nil {
			return nil, err
		}
		if targets[i].stateBefore == "pending" || targets[i].stateBefore == "leased" {
			if _, err = failDelivery(ctx, tx, targets[i].deliveryID, "operator_released", control); err != nil {
				return nil, err
			}
		}
		if err = tx.QueryRow(ctx, `SELECT state FROM cairn.agent_delivery WHERE delivery_id=$1`, targets[i].deliveryID).Scan(&targets[i].stateAfter); err != nil {
			return nil, err
		}
	}
	return targets, nil
}

func (s *Store) RenewSessionDigest(ctx context.Context, req SessionInboxDigestRenew, dest Destination) (SessionInboxAttempt, error) {
	var out SessionInboxAttempt
	if err := s.nativeInboxProfile(req.Session, dest); err != nil {
		return out, err
	}
	if validID(req.AttemptID) != nil {
		return out, failure("INVALID_REQUEST", "native inbox attempt UUID required")
	}
	seconds, err := leaseSeconds(req.LeaseSeconds)
	if err != nil {
		return out, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	if _, err = retrievalGeneration(ctx, tx); err != nil {
		return out, err
	}
	a, err := currentAgentSession(ctx, tx, req.Session, s.channel.Principal, s.channel.Repo, false, dest)
	if err != nil {
		return out, err
	}
	if err = activeAgent(a); err != nil {
		return out, err
	}
	attempt, err := s.lockedSessionAttempt(ctx, tx, req.AttemptID, req.Session, dest)
	if err != nil {
		return out, err
	}
	if attempt.AdmissionKind != "digest" {
		return out, failure("INVALID_REQUEST", "attempt is not a notice digest")
	}
	if attempt.FinishedAt != nil {
		return out, failure("STALE_LEASE", "native digest attempt is already closed")
	}
	ids := sortedAttemptDeliveryIDs(attempt)
	if _, err = lockDeliveryStatesTx(ctx, tx, ids); err != nil {
		return out, err
	}
	ttl := time.Duration(seconds) * time.Second
	for _, item := range attempt.DigestItems {
		var curState, curLease string
		var live bool
		if err = tx.QueryRow(ctx, `SELECT state,COALESCE(lease_id::text,''),COALESCE(lease_until>clock_timestamp(),false) FROM cairn.agent_delivery WHERE delivery_id=$1`, item.Delivery.DeliveryID).Scan(&curState, &curLease, &live); err != nil {
			return out, err
		}
		switch curState {
		case "handled", "ignored", "failed":
			continue
		case "leased":
			if curLease != item.Delivery.LeaseID || !live {
				return out, failure("STALE_LEASE", "delivery lease expired")
			}
			if err = renewDelivery(ctx, tx, item.Delivery.DeliveryID, ttl); err != nil {
				return out, err
			}
		default:
			return out, failure("STALE_LEASE", "delivery is not owned by this lease")
		}
	}
	out, err = s.readSessionInboxAttempt(ctx, tx, req.AttemptID, req.Session, dest)
	if err != nil {
		return out, err
	}
	return out, tx.Commit(ctx)
}

func validDigestOutcome(o SessionDigestItemOutcome) error {
	if validID(o.DeliveryID) != nil || validID(o.LeaseID) != nil {
		return failure("INVALID_REQUEST", "delivery and lease UUIDs required")
	}
	if o.Disposition != "handled" && o.Disposition != "ignored" && o.Disposition != "failed" {
		return failure("INVALID_REQUEST", "disposition must be handled, ignored or failed")
	}
	if (o.Disposition == "handled" && o.Code != "") || (o.Disposition != "handled" && o.Code != "unsupported_kind" && o.Code != "source_unavailable" && o.Code != "processing_failed") {
		return failure("INVALID_REQUEST", "invalid disposition code")
	}
	return nil
}

func (s *Store) SettleSessionDigest(ctx context.Context, req SessionInboxDigestSettle, dest Destination) (SessionInboxDigestSettleResult, error) {
	out := SessionInboxDigestSettleResult{AttemptID: req.AttemptID, Items: []SessionDigestSettledItem{}}
	if err := s.nativeInboxProfile(req.Session, dest); err != nil {
		return out, err
	}
	if validID(req.RequestID) != nil || validID(req.AttemptID) != nil {
		return out, failure("INVALID_REQUEST", "stable request and attempt UUIDs required")
	}
	if len(req.Outcomes) == 0 || len(req.Outcomes) > maxDigestItems {
		return out, failure("INVALID_REQUEST", "digest settlement requires between 1 and 64 item outcomes")
	}
	seen := make(map[string]struct{}, len(req.Outcomes))
	for _, o := range req.Outcomes {
		if err := validDigestOutcome(o); err != nil {
			return out, err
		}
		if _, dup := seen[o.DeliveryID]; dup {
			return out, failure("INVALID_REQUEST", "duplicate delivery UUID in digest settlement")
		}
		seen[o.DeliveryID] = struct{}{}
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
	input := struct {
		SessionInboxDigestSettle
		Destination string `json:"destination"`
		AllowLocal  bool   `json:"allow_local"`
	}{req, dest.Name, dest.AllowLocal}
	return mutate(ctx, s, "session-inbox-digest-settle", req.RequestID, input, func(tx pgx.Tx) (SessionInboxDigestSettleResult, error) {
		res := SessionInboxDigestSettleResult{AttemptID: req.AttemptID, Items: make([]SessionDigestSettledItem, 0, len(req.Outcomes))}
		attempt, err := s.lockedSessionAttempt(ctx, tx, req.AttemptID, req.Session, dest)
		if err != nil {
			return res, err
		}
		if attempt.AdmissionKind != "digest" {
			return res, failure("INVALID_REQUEST", "attempt is not a notice digest")
		}
		if attempt.FinishedAt != nil {
			if attempt.Reason == "turn_ended" || attempt.Reason == "process_exited" || attempt.Reason == "operator_released" {
				return res, failure("HOLD_RELEASED", "this native hold was reconciled or released before completion; the result was not accepted")
			}
			return res, failure("STALE_LEASE", "delivery is not owned by this lease")
		}
		byDelivery := make(map[string]SessionDigestItem, len(attempt.DigestItems))
		for _, item := range attempt.DigestItems {
			byDelivery[item.Delivery.DeliveryID] = item
		}
		for _, o := range req.Outcomes {
			item, ok := byDelivery[o.DeliveryID]
			if !ok {
				return res, failure("INVALID_REQUEST", "delivery does not belong to this digest attempt")
			}
			if item.Delivery.LeaseID != o.LeaseID {
				return res, failure("STALE_LEASE", "delivery is not owned by this lease")
			}
		}
		if _, err = lockDeliveryStatesTx(ctx, tx, sortedAttemptDeliveryIDs(attempt)); err != nil {
			return res, err
		}
		hold := &agentSessionChannel{Ref: req.Session, Profile: s.channel.Principal}
		view := *s
		view.channel.Principal = "agent/" + req.Session.AgentID
		for _, o := range req.Outcomes {
			item := byDelivery[o.DeliveryID]
			d, err := view.ownedDelivery(ctx, tx, o.DeliveryID, dest)
			if err != nil {
				return res, err
			}
			if d.State == o.Disposition && d.Code == o.Code && d.Control == nil {
				res.Items = append(res.Items, SessionDigestSettledItem{Ordinal: item.Ordinal, DeliveryID: o.DeliveryID, State: d.State, Code: d.Code, CompletedAfterLease: d.Late, AlreadySettled: true})
				continue
			}
			if d.State == "handled" || d.State == "ignored" || (d.State == "failed" && d.Control == nil) {
				return res, failure("VERSION_CONFLICT", "digest item already settled with a different disposition")
			}
			late, err := eventLease(ctx, tx, d, o.LeaseID, hold)
			if err != nil {
				return res, err
			}
			if err = settleDelivery(ctx, tx, d.DeliveryID, o.Disposition, o.Code, nil, nil, late); err != nil {
				return res, err
			}
			res.Items = append(res.Items, SessionDigestSettledItem{Ordinal: item.Ordinal, DeliveryID: o.DeliveryID, State: o.Disposition, Code: o.Code, CompletedAfterLease: late})
		}
		if err = tx.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_session_digest_item di JOIN cairn.agent_delivery d USING(delivery_id) WHERE di.attempt_id=$1 AND d.state IN ('pending','leased')`, req.AttemptID).Scan(&res.Remaining); err != nil {
			return res, err
		}
		return res, nil
	}, guard)
}
