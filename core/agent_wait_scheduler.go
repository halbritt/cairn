package core

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// SweepWaitTimeouts sweeps waiting rows that have passed their deadline.
// Critical invariant: Before marking any wait as timed_out, it first evaluates
// whether an eligible retained response or group closure occurred before wait.deadline,
// ensuring scheduler downtime never converts a timely retained event into a timeout.
// Lock order: generation lock -> collection insertion lock -> dependency/wait rows in stable UUID order.
func (s *Store) SweepWaitTimeouts(ctx context.Context, repo string) (int64, error) {
	if !s.channel.Operator {
		return 0, failure("AUTHORITY_DENIED", "timeout sweep requires operator channel")
	}

	if repo == "" {
		tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
		if err != nil {
			return 0, err
		}
		rows, err := tx.Query(ctx, `SELECT DISTINCT collection FROM cairn.agent_wait WHERE state='waiting' AND deadline<=clock_timestamp() LIMIT 50`)
		if err != nil {
			_ = tx.Rollback(ctx)
			return 0, err
		}
		var collections []string
		for rows.Next() {
			var col string
			if err = rows.Scan(&col); err == nil {
				collections = append(collections, col)
			}
		}
		rows.Close()
		_ = tx.Rollback(ctx)

		var total int64
		for _, col := range collections {
			n, err := s.SweepWaitTimeouts(ctx, col)
			if err != nil {
				return total, err
			}
			total += n
		}
		return total, nil
	}

	repo, err := s.eventScope(repo, "")
	if err != nil {
		return 0, err
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback(ctx)

	// 1. Generation lock
	if _, err = retrievalGeneration(ctx, tx); err != nil {
		return 0, err
	}
	// 2. Collection insertion lock
	if err = lock(ctx, tx, "agent-events:"+repo); err != nil {
		return 0, err
	}

	// 3. Wait rows in stable UUID order
	rows, err := tx.Query(ctx, `SELECT
w.wait_uuid::text, w.collection, w.owner_principal, w.owner_agent_uuid::text,
w.predicate_kind, w.dependency_event_uuid::text, w.deadline,
w.reserved_notice_event_uuid::text, w.reserved_source_record_uuid::text
FROM cairn.agent_wait w
WHERE w.collection=$1 AND w.state='waiting' AND w.deadline<=clock_timestamp()
ORDER BY w.wait_uuid
LIMIT 100
FOR UPDATE OF w SKIP LOCKED`, repo)
	if err != nil {
		return 0, err
	}
	defer rows.Close()

	type overdueWait struct {
		waitUUID           string
		collection         string
		ownerPrincipal     string
		ownerAgentUUID     string
		predicateKind      string
		depEventUUID       string
		deadline           time.Time
		reservedNoticeUUID string
		reservedSourceUUID string
	}
	var overdueList []overdueWait
	for rows.Next() {
		var ow overdueWait
		if err = rows.Scan(&ow.waitUUID, &ow.collection, &ow.ownerPrincipal, &ow.ownerAgentUUID, &ow.predicateKind, &ow.depEventUUID, &ow.deadline, &ow.reservedNoticeUUID, &ow.reservedSourceUUID); err != nil {
			return 0, err
		}
		overdueList = append(overdueList, ow)
	}
	rows.Close()

	if len(overdueList) == 0 {
		return 0, nil
	}

	var now time.Time
	if err = tx.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&now); err != nil {
		return 0, err
	}

	for _, ow := range overdueList {
		terminalReason := "timeout"
		state := WaitStateTimedOut
		var refJSON []byte

		if ow.predicateKind == WaitPredicateRequestResponse {
			var origConsumer, origPublisher string
			var origCorrID *string
			_ = tx.QueryRow(ctx, `SELECT d.consumer, e.publisher, e.correlation_id::text
FROM cairn.agent_event e
LEFT JOIN cairn.agent_delivery d USING(event_id)
WHERE e.event_id=$1 LIMIT 1`, ow.depEventUUID).Scan(&origConsumer, &origPublisher, &origCorrID)

			var respRecordID string
			var respVersion int
			var respCreatedAt time.Time
			err = tx.QueryRow(ctx, `SELECT record_id::text, version, created_at FROM cairn.agent_event
WHERE causation_id=$1 AND repo=$2 AND kind IN ('response','failure')
  AND ($3='' OR publisher=$3)
  AND destination_type='agent' AND (destination_name=$4 OR destination_name='agent/'||$5)
  AND (($6::uuid IS NULL AND correlation_id IS NULL) OR ($6::uuid IS NOT NULL AND correlation_id=$6::uuid))
ORDER BY position ASC LIMIT 1`, ow.depEventUUID, ow.collection, origConsumer, origPublisher, ow.ownerAgentUUID, origCorrID).Scan(&respRecordID, &respVersion, &respCreatedAt)
			if err == nil {
				if respCreatedAt.Before(ow.deadline) {
					state = WaitStateSatisfied
					terminalReason = "response"
					ref := RecordVersionRef{RecordID: respRecordID, Version: respVersion}
					refJSON, _ = json.Marshal(ref)
				}
			}
		} else if ow.predicateKind == WaitPredicateResponseGroupClosed {
			var grpState, grpCode string
			var grpDeadline time.Time
			var grpClosedAt *time.Time
			err = tx.QueryRow(ctx, `SELECT state, code, deadline, closed_at FROM cairn.agent_response_group WHERE event_id=$1`, ow.depEventUUID).Scan(&grpState, &grpCode, &grpDeadline, &grpClosedAt)
			if err == nil && grpState != "open" && grpCode != "restore_fenced" {
				effectiveTime := grpDeadline
				if grpCode == "all_responded" && grpClosedAt != nil {
					effectiveTime = *grpClosedAt
				}
				if effectiveTime.Before(ow.deadline) {
					state = WaitStateSatisfied
					terminalReason = "group_closed"
				}
			}
		}

		_, err = tx.Exec(ctx, `UPDATE cairn.agent_wait SET state=$2, resolved_at=$3, terminal_reason=$4, response_ref=$5 WHERE wait_uuid=$1`, ow.waitUUID, state, now, terminalReason, refJSON)
		if err != nil {
			return 0, err
		}
		_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_wait_outbox(wait_uuid, notice_event_uuid, source_record_uuid, state, updated_at)
VALUES($1,$2,$3,'pending',$4) ON CONFLICT (wait_uuid) DO NOTHING`, ow.waitUUID, ow.reservedNoticeUUID, ow.reservedSourceUUID, now)
		if err != nil {
			return 0, err
		}
	}

	return int64(len(overdueList)), tx.Commit(ctx)
}

// DrainWaitOutbox drains pending notification rows under the scheduler's operator identity.
// Revalidates destination ceiling, binding permissions, and source eligibility; marks outbox restricted if revoked/ineligible.
// Lock order: generation lock -> collection insertion lock -> dependency/wait rows in stable UUID order.
func (s *Store) DrainWaitOutbox(ctx context.Context, repo string, limit int) (int64, error) {
	if !s.channel.Operator {
		return 0, failure("AUTHORITY_DENIED", "outbox drain requires operator channel")
	}
	if limit <= 0 || limit > 100 {
		limit = 50
	}

	if repo == "" {
		tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
		if err != nil {
			return 0, err
		}
		rows, err := tx.Query(ctx, `SELECT DISTINCT w.collection FROM cairn.agent_wait_outbox o JOIN cairn.agent_wait w USING(wait_uuid) WHERE o.state='pending' LIMIT 50`)
		if err != nil {
			_ = tx.Rollback(ctx)
			return 0, err
		}
		var collections []string
		for rows.Next() {
			var col string
			if err = rows.Scan(&col); err == nil {
				collections = append(collections, col)
			}
		}
		rows.Close()
		_ = tx.Rollback(ctx)

		var total int64
		for _, col := range collections {
			n, err := s.DrainWaitOutbox(ctx, col, limit)
			if err != nil {
				return total, err
			}
			total += n
		}
		return total, nil
	}

	repo, err := s.eventScope(repo, "")
	if err != nil {
		return 0, err
	}

	tx, err := s.begin(ctx)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback(ctx)

	// 1. Generation lock
	if _, err = retrievalGeneration(ctx, tx); err != nil {
		return 0, err
	}
	// 2. Collection insertion lock
	if err = lock(ctx, tx, "agent-events:"+repo); err != nil {
		return 0, err
	}

	// 3. Outbox and wait rows in stable UUID order
	rows, err := tx.Query(ctx, `SELECT
o.wait_uuid::text, o.notice_event_uuid::text, o.source_record_uuid::text,
w.collection, w.owner_principal, w.owner_agent_uuid::text,
w.registration_destination, w.sensitivity_ceiling, w.profile_binding_id,
w.state, w.terminal_reason, w.dependency_event_uuid::text, w.response_ref
FROM cairn.agent_wait_outbox o
JOIN cairn.agent_wait w USING(wait_uuid)
WHERE o.state='pending' AND w.collection=$1
ORDER BY o.updated_at, o.wait_uuid
LIMIT $2
FOR UPDATE OF o, w SKIP LOCKED`, repo, limit)
	if err != nil {
		return 0, err
	}
	defer rows.Close()

	type outboxItem struct {
		waitUUID            string
		noticeEventUUID     string
		sourceRecordUUID    string
		collection          string
		ownerPrincipal      string
		ownerAgentUUID      string
		destJSON            []byte
		ceiling             string
		bindingID           string
		waitState           string
		terminalReason      string
		dependencyEventUUID string
		responseRefJSON     []byte
	}
	var items []outboxItem
	for rows.Next() {
		var item outboxItem
		if err = rows.Scan(
			&item.waitUUID, &item.noticeEventUUID, &item.sourceRecordUUID,
			&item.collection, &item.ownerPrincipal, &item.ownerAgentUUID,
			&item.destJSON, &item.ceiling, &item.bindingID,
			&item.waitState, &item.terminalReason, &item.dependencyEventUUID, &item.responseRefJSON); err != nil {
			return 0, err
		}
		items = append(items, item)
	}
	rows.Close()

	if len(items) == 0 {
		return 0, nil
	}

	var now time.Time
	if err = tx.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&now); err != nil {
		return 0, err
	}

	publishedCount := int64(0)
	for _, item := range items {
		// 1. Revalidate binding: check if session exists and binding has not changed.
		// Note: stopped is NOT checked because LeaveAgent is normal offline presence, NOT revocation.
		// Addressed offline delivery to registered conversations is preserved.
		var currentBinding, currentVisibility string
		err = tx.QueryRow(ctx, `SELECT binding, visibility FROM cairn.agent_session WHERE agent_id=$1 AND repo=$2`, item.ownerAgentUUID, item.collection).Scan(&currentBinding, &currentVisibility)
		if errors.Is(err, pgx.ErrNoRows) || (item.bindingID != "" && currentBinding != item.bindingID) {
			_, _ = tx.Exec(ctx, `UPDATE cairn.agent_wait_outbox SET state='restricted', error_reason='binding_revoked', updated_at=$2 WHERE wait_uuid=$1`, item.waitUUID, now)
			continue
		}
		if err != nil {
			return 0, err
		}

		// 2. Revalidate dependency exact source/version eligibility and payload deletion
		var depRecordID string
		var depVersion int
		var depSensitivity string
		err = tx.QueryRow(ctx, `SELECT record_id::text, version, sensitivity FROM cairn.agent_event WHERE event_id=$1`, item.dependencyEventUUID).Scan(&depRecordID, &depVersion, &depSensitivity)
		if errors.Is(err, pgx.ErrNoRows) {
			_, _ = tx.Exec(ctx, `UPDATE cairn.agent_wait_outbox SET state='restricted', error_reason='source_ineligible', updated_at=$2 WHERE wait_uuid=$1`, item.waitUUID, now)
			continue
		}
		if err != nil {
			return 0, err
		}
		var depRecordSensitivity string
		var depLifecycle string
		var depPayloadDeleted bool
		err = tx.QueryRow(ctx, `SELECT m.sensitivity, m.lifecycle, v.payload_deleted_by IS NOT NULL 
FROM cairn.memory_record m 
JOIN cairn.record_version v ON v.record_id=m.record_id 
WHERE m.record_id=$1 AND v.version=$2 AND v.repo=$3`, depRecordID, depVersion, item.collection).Scan(&depRecordSensitivity, &depLifecycle, &depPayloadDeleted)
		if errors.Is(err, pgx.ErrNoRows) || depLifecycle != "active" || depPayloadDeleted {
			_, _ = tx.Exec(ctx, `UPDATE cairn.agent_wait_outbox SET state='restricted', error_reason='source_ineligible', updated_at=$2 WHERE wait_uuid=$1`, item.waitUUID, now)
			continue
		}
		if err != nil {
			return 0, err
		}

		// 3. Revalidate response exact source/version eligibility and payload deletion
		var respSensitivity string
		var respRecordSensitivity string
		if len(item.responseRefJSON) > 0 {
			var respRef RecordVersionRef
			if err = json.Unmarshal(item.responseRefJSON, &respRef); err == nil && respRef.RecordID != "" {
				var respLifecycle string
				var respPayloadDeleted bool
				err = tx.QueryRow(ctx, `SELECT m.sensitivity, m.lifecycle, v.payload_deleted_by IS NOT NULL 
FROM cairn.memory_record m 
JOIN cairn.record_version v ON v.record_id=m.record_id 
WHERE m.record_id=$1 AND v.version=$2 AND v.repo=$3`, respRef.RecordID, respRef.Version, item.collection).Scan(&respRecordSensitivity, &respLifecycle, &respPayloadDeleted)
				if errors.Is(err, pgx.ErrNoRows) || respLifecycle != "active" || respPayloadDeleted {
					_, _ = tx.Exec(ctx, `UPDATE cairn.agent_wait_outbox SET state='restricted', error_reason='source_ineligible', updated_at=$2 WHERE wait_uuid=$1`, item.waitUUID, now)
					continue
				}
				if err != nil {
					return 0, err
				}
				_ = tx.QueryRow(ctx, `SELECT sensitivity FROM cairn.agent_event WHERE record_id=$1 AND version=$2 AND repo=$3 LIMIT 1`, respRef.RecordID, respRef.Version, item.collection).Scan(&respSensitivity)
			}
		}

		// 4. Intersect original registration destination ceiling with owner's current valid binding visibility
		var regDest Destination
		_ = json.Unmarshal(item.destJSON, &regDest)
		allowsLocal := (item.ceiling == "local") && regDest.AllowLocal && (currentVisibility == "local")
		depIsLocal := (depSensitivity == "local" || depRecordSensitivity == "local")
		respIsLocal := (respSensitivity == "local" || respRecordSensitivity == "local")
		if (depIsLocal || respIsLocal) && !allowsLocal {
			_, _ = tx.Exec(ctx, `UPDATE cairn.agent_wait_outbox SET state='restricted', error_reason='ceiling_exceeded', updated_at=$2 WHERE wait_uuid=$1`, item.waitUUID, now)
			continue
		}

		noticeSensitivity := "shareable"
		if allowsLocal && item.ceiling == "local" {
			noticeSensitivity = "local"
		}

		// 5. Publish fixed-format notice event using dedicated task scope and real channel provenance
		noticeBody := fmt.Sprintf("Wait %s resolved (%s: %s). Inspect wait for details.", item.waitUUID, item.waitState, item.terminalReason)

		draft := Draft{
			Body:        noticeBody,
			Kind:        "note",
			ClaimType:   "self",
			Sensitivity: noticeSensitivity,
			Scope:       Scope{Repo: item.collection, TaskID: "wait-" + item.waitUUID, RunID: "*"},
		}
		if err = draft.validate(); err != nil {
			return 0, err
		}

		tag, err := tx.Exec(ctx, `INSERT INTO cairn.memory_record(record_id,current_version,sensitivity) VALUES($1,1,$2) ON CONFLICT (record_id) DO NOTHING`, item.sourceRecordUUID, noticeSensitivity)
		if err != nil {
			return 0, err
		}
		if tag.RowsAffected() > 0 {
			if _, err = insertVersion(ctx, tx, item.sourceRecordUUID, 1, draft); err != nil {
				return 0, err
			}
		}

		recipient := "agent/" + item.ownerAgentUUID
		_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_event(
			event_id, repo, kind, record_id, version, sensitivity, destination_type, destination_name, publisher, created_at)
			VALUES($1, $2, 'notice', $3, 1, $4, 'agent', $5, $6, clock_timestamp())
			ON CONFLICT (event_id) DO NOTHING`,
			item.noticeEventUUID, item.collection, item.sourceRecordUUID, noticeSensitivity, recipient, s.channel.Principal)
		if err != nil {
			return 0, err
		}

		deliveryID := uuid.NewString()
		_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_delivery(delivery_id, event_id, consumer, state)
			VALUES($1, $2, $3, 'pending')
			ON CONFLICT (event_id, consumer) DO NOTHING`, deliveryID, item.noticeEventUUID, recipient)
		if err != nil {
			return 0, err
		}

		_, err = tx.Exec(ctx, `UPDATE cairn.agent_wait_outbox SET state='published', updated_at=$2 WHERE wait_uuid=$1`, item.waitUUID, now)
		if err != nil {
			return 0, err
		}
		publishedCount++
	}

	return publishedCount, tx.Commit(ctx)
}
