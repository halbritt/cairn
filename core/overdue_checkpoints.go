package core

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

const (
	DefaultOverdueGraceSeconds = 60
	MaxOverdueGraceSeconds     = 86400
	MaxCoalescedOverdueItems   = 50
)

var overdueUUIDNamespace = uuid.MustParse("9f320467-71a4-4f29-9c83-2d84a9b10467")

type OverdueSweepRequest struct {
	Repo         string `json:"repo"`
	GraceSeconds *int   `json:"grace_seconds,omitempty"`
	Limit        int    `json:"limit,omitempty"`
}

type OverdueNoticeSummary struct {
	EventID    string   `json:"event_id"`
	RecordID   string   `json:"record_id"`
	Subscriber string   `json:"subscriber"`
	ItemCount  int      `json:"item_count"`
	Sources    []string `json:"sources"`
}

type OverdueSweepResult struct {
	Repo                         string                 `json:"repo"`
	GraceSeconds                 int                    `json:"grace_seconds"`
	EmittedNotices               int                    `json:"emitted_notices"`
	EmittedItems                 int                    `json:"emitted_items"`
	CoalescedItems               int                    `json:"coalesced_items"`
	SatisfiedItems               int                    `json:"satisfied_items"`
	SkippedItems                 int                    `json:"skipped_items"`
	DeferredNotices              int                    `json:"deferred_notices,omitempty"`
	UnrecordedRefusalDiagnostics int                    `json:"unrecorded_refusal_diagnostics,omitempty"`
	DiagnosticWarning            string                 `json:"diagnostic_warning,omitempty"`
	Published                    []OverdueNoticeSummary `json:"published"`
}

type overdueCheckpointRow struct {
	checkpointID       string
	repo               string
	subscriber         string
	sourceKind         string
	sourceKey          string
	topicKey           string
	sender             string
	sensitivity        string
	deadlineAt         time.Time
	sourceUpdatedAt    time.Time
	sourceEventID      string
	sourceRecordID     string
	sourceVersion      int
	workstreamRevision int64
	workstreamState    string
}

type overdueRecordVer struct {
	recordID string
	version  int
}

type overdueRecordState struct {
	eligible    bool
	sensitivity string
}

func (s *Store) overdueSweepScope(repo string) (string, error) {
	if !s.channel.Operator || s.channel.Repo != "" {
		return "", failure("AUTHORITY_DENIED", "overdue checkpoint sweep requires the existing unscoped operator channel")
	}
	return s.eventScope(repo, "")
}

// SweepOverdueDeadlines evaluates overdue notice expect_next_by, request
// expect_by, and workstream next_checkpoint deadlines under database time.
// Missed deadlines are deduplicated per (repo, subscriber, source_kind,
// source_key, deadline_at) across restarts and coalesced while an earlier
// overdue notice to that subscriber remains pending or leased.
func (s *Store) SweepOverdueDeadlines(ctx context.Context, req OverdueSweepRequest) (OverdueSweepResult, error) {
	out := OverdueSweepResult{Published: []OverdueNoticeSummary{}}
	repo, err := s.overdueSweepScope(req.Repo)
	if err != nil {
		return out, err
	}
	grace := DefaultOverdueGraceSeconds
	if req.GraceSeconds != nil {
		if *req.GraceSeconds < 0 || *req.GraceSeconds > MaxOverdueGraceSeconds {
			return out, failure("INVALID_REQUEST", "grace_seconds must be 0-86400")
		}
		grace = *req.GraceSeconds
	}
	limit, err := eventLimit(req.Limit)
	if err != nil {
		return out, err
	}
	out.Repo = repo
	out.GraceSeconds = grace

	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	if _, err = retrievalGeneration(ctx, tx); err != nil {
		return out, err
	}
	if err = lock(ctx, tx, "agent-events:"+repo); err != nil {
		return out, err
	}
	var now time.Time
	var fencedAt *time.Time
	var minEventPos int64
	err = tx.QueryRow(ctx, `SELECT
		clock_timestamp(),
		COALESCE(
			(SELECT fenced_at FROM cairn.agent_overdue_fence ORDER BY generation DESC LIMIT 1),
			(SELECT max(observed_at) FROM cairn.restore_fence)
		),
		COALESCE((SELECT max_event_position FROM cairn.agent_overdue_fence ORDER BY generation DESC LIMIT 1), 0)`).
		Scan(&now, &fencedAt, &minEventPos)
	if err != nil {
		return out, err
	}
	cutoff := now.Add(-time.Duration(grace) * time.Second)

	justInserted := make(map[string]bool)
	if err = s.discoverDueNoticeOverdueTx(ctx, tx, repo, cutoff, limit, fencedAt, minEventPos, justInserted, &out); err != nil {
		return out, err
	}
	if err = s.discoverDueRequestOverdueTx(ctx, tx, repo, cutoff, limit, fencedAt, minEventPos, justInserted, &out); err != nil {
		return out, err
	}
	if err = s.discoverDueWorkstreamOverdueTx(ctx, tx, repo, cutoff, limit, fencedAt, justInserted, &out); err != nil {
		return out, err
	}
	retainedRefusals, err := s.revalidateAndEmitOverdueBatchesTx(ctx, tx, repo, limit*4, justInserted, &out)
	if err != nil {
		return out, err
	}
	if err = tx.Commit(ctx); err != nil {
		return out, err
	}
	for _, re := range retainedRefusals {
		if retained := s.retainEventAdmissionRefusal(ctx, repo, re); retained != re {
			out.UnrecordedRefusalDiagnostics++
		}
	}
	if out.UnrecordedRefusalDiagnostics > 0 {
		out.DiagnosticWarning = "overdue sweep committed; admission refusal diagnostic could not be retained"
	}
	return out, nil
}

// subscriberCeilingExceededTx fails closed for local sensitivity: only a
// registered session in the collection with visibility='local' may receive a
// local overdue disclosure.
func subscriberCeilingExceededTx(ctx context.Context, tx pgx.Tx, repo, subscriber, sensitivity string) (bool, error) {
	if sensitivity != "local" {
		return false, nil
	}
	agentID, ok := strings.CutPrefix(subscriber, "agent/")
	if !ok || validID(agentID) != nil {
		return true, nil
	}
	var visibility string
	err := tx.QueryRow(ctx, `SELECT visibility FROM cairn.agent_session WHERE repo=$1 AND agent_id=$2::uuid FOR SHARE`, repo, agentID).Scan(&visibility)
	if errors.Is(err, pgx.ErrNoRows) {
		return true, nil
	}
	if err != nil {
		return false, err
	}
	return visibility != "local", nil
}

func subscriberStoppedTx(ctx context.Context, tx pgx.Tx, repo, subscriber string) (bool, error) {
	agentID, ok := strings.CutPrefix(subscriber, "agent/")
	if !ok || validID(agentID) != nil {
		return false, nil
	}
	var stopped bool
	err := tx.QueryRow(ctx, `SELECT stopped FROM cairn.agent_session WHERE repo=$1 AND agent_id=$2::uuid`, repo, agentID).Scan(&stopped)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	return stopped, err
}

func lockAndInspectSourceRecordsTx(ctx context.Context, tx pgx.Tx, repo string, refs []overdueRecordVer) (map[overdueRecordVer]overdueRecordState, error) {
	seen := make(map[overdueRecordVer]struct{}, len(refs))
	sorted := make([]overdueRecordVer, 0, len(refs))
	for _, rv := range refs {
		if rv.recordID == "" || rv.version < 1 {
			continue
		}
		if _, dup := seen[rv]; !dup {
			seen[rv] = struct{}{}
			sorted = append(sorted, rv)
		}
	}
	sort.Slice(sorted, func(i, j int) bool {
		if sorted[i].recordID != sorted[j].recordID {
			return sorted[i].recordID < sorted[j].recordID
		}
		return sorted[i].version < sorted[j].version
	})
	states := make(map[overdueRecordVer]overdueRecordState, len(sorted))
	for _, rv := range sorted {
		var sens, life string
		var deleted bool
		err := tx.QueryRow(ctx, `SELECT m.sensitivity, m.lifecycle, v.payload_deleted_by IS NOT NULL
			FROM cairn.memory_record m
			JOIN cairn.record_version v ON v.record_id = m.record_id
			WHERE m.record_id = $1::uuid AND v.version = $2 AND v.repo = $3
			FOR SHARE OF m`, rv.recordID, rv.version, repo).Scan(&sens, &life, &deleted)
		if errors.Is(err, pgx.ErrNoRows) {
			states[rv] = overdueRecordState{eligible: false}
			continue
		}
		if err != nil {
			return nil, err
		}
		states[rv] = overdueRecordState{
			eligible:    life == "active" && !deleted,
			sensitivity: sens,
		}
	}
	return states, nil
}

const overdueNonSyntheticEvent = `NOT EXISTS (SELECT 1 FROM cairn.agent_overdue_checkpoint oc WHERE oc.notice_event_id = e2.event_id)`

func isNoticeSatisfiedTx(ctx context.Context, tx pgx.Tx, repo, eventID, sender, subscriber, supersedes, destType, destName, sensitivity string, eventPos int64, createdAt time.Time) (bool, error) {
	var superseded bool
	err := tx.QueryRow(ctx, `SELECT EXISTS(
		SELECT 1 FROM cairn.agent_delivery WHERE event_id = $1::uuid AND consumer = $2 AND superseded_by IS NOT NULL
		UNION ALL
		SELECT 1 FROM cairn.agent_organic_notice WHERE event_id = $1::uuid AND recipient_inbox = $2 AND unavailable_reason = 'superseded'
	)`, eventID, subscriber).Scan(&superseded)
	if err != nil || superseded {
		return superseded, err
	}
	const reachedSubscriber = `(
		EXISTS(SELECT 1 FROM cairn.agent_delivery d2 WHERE d2.event_id = e2.event_id AND d2.consumer = $6)
		OR EXISTS(SELECT 1 FROM cairn.agent_organic_notice q2 WHERE q2.event_id = e2.event_id AND q2.recipient_inbox = $6)
	)`
	const nonAuthorizeHistory = `NOT EXISTS(
		SELECT 1 FROM cairn.workstream_status_history prev
		WHERE prev.repo = h.repo AND prev.stream = h.stream AND prev.revision = h.revision - 1
		  AND (prev.state, prev.current_item, prev.last_landed, prev.next_checkpoint, prev.blocker, prev.decision_needed, prev.detail)
		    IS NOT DISTINCT FROM (h.state, h.current_item, h.last_landed, h.next_checkpoint, h.blocker, h.decision_needed, h.detail)
	)`
	if supersedes != "" {
		var ok bool
		err = tx.QueryRow(ctx, `SELECT EXISTS(
			SELECT 1 FROM cairn.agent_event e2
			WHERE e2.repo = $1 AND e2.publisher = $2 AND e2.position > $3
			  AND ($8 = 'local' OR e2.sensitivity = 'shareable')
			  AND `+overdueNonSyntheticEvent+`
			  AND (
			    e2.notice_supersedes = $4
			    OR (e2.destination_type = 'topic' AND e2.destination_name = $4)
			    OR e2.causation_id = $5::uuid
			  )
			  AND `+reachedSubscriber+`
		) OR EXISTS(
			SELECT 1 FROM cairn.workstream_status_history h
			WHERE h.repo = $1 AND h.stream = $4 AND h.writer = $2 AND h.writer <> $6
			  AND ($8 = 'local' OR h.sensitivity = 'shareable')
			  AND h.written_at > $7
			  AND `+nonAuthorizeHistory+`
		)`, repo, sender, eventPos, supersedes, eventID, subscriber, createdAt, sensitivity).Scan(&ok)
		return ok, err
	}
	if destType == "topic" {
		var ok bool
		err = tx.QueryRow(ctx, `SELECT EXISTS(
			SELECT 1 FROM cairn.agent_event e2
			WHERE e2.repo = $1 AND e2.publisher = $2 AND e2.position > $3
			  AND ($8 = 'local' OR e2.sensitivity = 'shareable')
			  AND `+overdueNonSyntheticEvent+`
			  AND (
			    (e2.destination_type = 'topic' AND e2.destination_name = $4)
			    OR e2.notice_supersedes = $4
			    OR e2.causation_id = $5::uuid
			  )
			  AND `+reachedSubscriber+`
		) OR EXISTS(
			SELECT 1 FROM cairn.workstream_status_history h
			WHERE h.repo = $1 AND h.stream = $4 AND h.writer = $2 AND h.writer <> $6
			  AND ($8 = 'local' OR h.sensitivity = 'shareable')
			  AND h.written_at > $7
			  AND `+nonAuthorizeHistory+`
		)`, repo, sender, eventPos, destName, eventID, subscriber, createdAt, sensitivity).Scan(&ok)
		return ok, err
	}
	var ok bool
	err = tx.QueryRow(ctx, `SELECT EXISTS(
		SELECT 1 FROM cairn.agent_event e2
		WHERE e2.repo = $1 AND e2.publisher = $2 AND e2.position > $3
		  AND ($6 = 'local' OR e2.sensitivity = 'shareable')
		  AND `+overdueNonSyntheticEvent+`
		  AND (
		    e2.causation_id = $5::uuid
		    OR (e2.kind = 'notice' AND e2.destination_type = 'agent' AND e2.notice_supersedes IS NULL)
		  )
		  AND (
		    EXISTS(SELECT 1 FROM cairn.agent_delivery d2 WHERE d2.event_id = e2.event_id AND d2.consumer = $4)
		    OR EXISTS(SELECT 1 FROM cairn.agent_organic_notice q2 WHERE q2.event_id = e2.event_id AND q2.recipient_inbox = $4)
		  )
	)`, repo, sender, eventPos, subscriber, eventID, sensitivity).Scan(&ok)
	return ok, err
}

func isRequestSatisfiedTx(ctx context.Context, tx pgx.Tx, repo, eventID, destType, destName, correlationID, sensitivity string, eventPos int64) (bool, error) {
	if destType == "pool" {
		var ok bool
		err := tx.QueryRow(ctx, `SELECT EXISTS(
			SELECT 1 FROM cairn.agent_event r
			WHERE r.repo = $1 AND r.position > $2 AND r.kind IN ('response', 'failure')
			  AND ($4 = 'local' OR r.sensitivity = 'shareable')
			  AND r.causation_id = $3::uuid
		) OR EXISTS(
			SELECT 1 FROM cairn.agent_pool_request pr
			LEFT JOIN cairn.agent_delivery d ON d.delivery_id = pr.delivery_id
			WHERE pr.event_id = $3::uuid
			  AND (
			    pr.closed_code = 'operator_cancelled'
			    OR d.state = 'handled'
			    OR (d.state = 'failed' AND d.code = 'operator_cancelled')
			    OR (d.consumer IS NOT NULL AND EXISTS(
			      SELECT 1 FROM cairn.agent_event e2
			      WHERE e2.repo = $1 AND e2.publisher = d.consumer AND e2.position > $2
			        AND ($4 = 'local' OR e2.sensitivity = 'shareable')
			        AND `+overdueNonSyntheticEvent+`
			        AND (e2.causation_id = $3::uuid OR (e2.causation_id IS NULL AND $5 <> '' AND e2.correlation_id = NULLIF($5, '')::uuid))
			    ))
			  )
		)`, repo, eventPos, eventID, sensitivity, correlationID).Scan(&ok)
		return ok, err
	}
	if destType == "agent" {
		var ok bool
		err := tx.QueryRow(ctx, `SELECT EXISTS(
			SELECT 1 FROM cairn.agent_delivery d
			WHERE d.event_id = $1::uuid
			  AND (d.state = 'handled' OR (d.state = 'failed' AND d.code = 'operator_cancelled'))
		) OR EXISTS(
			SELECT 1 FROM cairn.agent_event e2
			WHERE e2.repo = $2 AND e2.position > $3
			  AND ($6 = 'local' OR e2.sensitivity = 'shareable')
			  AND `+overdueNonSyntheticEvent+`
			  AND (
			    (e2.kind IN ('response', 'failure') AND e2.causation_id = $1::uuid)
			    OR (e2.publisher = $4 AND (e2.causation_id = $1::uuid OR (e2.causation_id IS NULL AND $5 <> '' AND e2.correlation_id = NULLIF($5, '')::uuid)))
			  )
		)`, eventID, repo, eventPos, destName, correlationID, sensitivity).Scan(&ok)
		return ok, err
	}
	var total, done int
	err := tx.QueryRow(ctx, `SELECT
		count(*),
		count(*) FILTER (
			WHERE d.state = 'handled'
			   OR (d.state = 'failed' AND d.code = 'operator_cancelled')
			   OR EXISTS(
			     SELECT 1 FROM cairn.agent_event e2
			     WHERE e2.repo = $2 AND e2.publisher = d.consumer AND e2.position > $3
			       AND ($5 = 'local' OR e2.sensitivity = 'shareable')
			       AND `+overdueNonSyntheticEvent+`
			       AND (e2.causation_id = $1::uuid OR (e2.causation_id IS NULL AND $4 <> '' AND e2.correlation_id = NULLIF($4, '')::uuid))
			   )
		)
	FROM cairn.agent_delivery d WHERE d.event_id = $1::uuid`, eventID, repo, eventPos, correlationID, sensitivity).Scan(&total, &done)
	if err != nil {
		return false, err
	}
	return total > 0 && done == total, nil
}

func isWorkstreamSatisfiedTx(ctx context.Context, tx pgx.Tx, repo, stream, subscriber, sensitivity string, deadlineAt time.Time) (bool, error) {
	var state, owner, writer string
	var writers []string
	var nextCP *time.Time
	var rev int64
	var updatedAt time.Time
	err := tx.QueryRow(ctx, `SELECT state, owner, writer, authorized_writers, revision, next_checkpoint, updated_at
		FROM cairn.workstream_status WHERE repo = $1 AND stream = $2`, repo, stream).
		Scan(&state, &owner, &writer, &writers, &rev, &nextCP, &updatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return true, nil
	}
	if err != nil {
		return false, err
	}
	if state == "done" || nextCP == nil || !nextCP.Equal(deadlineAt) {
		return true, nil
	}
	var lastDiffRev int64
	err = tx.QueryRow(ctx, `SELECT COALESCE(max(revision), 0)
		FROM cairn.workstream_status_history
		WHERE repo = $1 AND stream = $2 AND next_checkpoint IS DISTINCT FROM $3`,
		repo, stream, deadlineAt).Scan(&lastDiffRev)
	if err != nil {
		return false, err
	}
	declRev := lastDiffRev + 1
	var newerBoardRev bool
	err = tx.QueryRow(ctx, `SELECT EXISTS(
		SELECT 1 FROM cairn.workstream_status_history h
		WHERE h.repo = $1 AND h.stream = $2 AND h.revision > $3
		  AND h.written_at >= $4 AND h.writer <> $5
		  AND ($6 = 'local' OR h.sensitivity = 'shareable')
		  AND NOT EXISTS(
		    SELECT 1 FROM cairn.workstream_status_history prev
		    WHERE prev.repo = h.repo AND prev.stream = h.stream AND prev.revision = h.revision - 1
		      AND (prev.state, prev.current_item, prev.last_landed, prev.next_checkpoint, prev.blocker, prev.decision_needed, prev.detail)
		        IS NOT DISTINCT FROM (h.state, h.current_item, h.last_landed, h.next_checkpoint, h.blocker, h.decision_needed, h.detail)
		  )
	)`, repo, stream, declRev, deadlineAt, subscriber, sensitivity).Scan(&newerBoardRev)
	if err != nil || newerBoardRev {
		return newerBoardRev, err
	}
	var newerEvent bool
	err = tx.QueryRow(ctx, `SELECT EXISTS(
		SELECT 1 FROM cairn.agent_event e2
		WHERE e2.repo = $1
		  AND e2.publisher <> $7
		  AND (e2.publisher = $3 OR e2.publisher = $4 OR e2.publisher = ANY($5))
		  AND ($8 = 'local' OR e2.sensitivity = 'shareable')
		  AND `+overdueNonSyntheticEvent+`
		  AND (
		    e2.created_at >= $9
		    OR (e2.created_at > $6 AND e2.notice_expect_next_by IS NOT NULL AND e2.notice_expect_next_by > $9)
		  )
		  AND (e2.notice_supersedes = $2 OR (e2.destination_type = 'topic' AND e2.destination_name = $2))
		  AND (
		    EXISTS(SELECT 1 FROM cairn.agent_delivery d2 WHERE d2.event_id = e2.event_id AND d2.consumer = $7)
		    OR EXISTS(SELECT 1 FROM cairn.agent_organic_notice q2 WHERE q2.event_id = e2.event_id AND q2.recipient_inbox = $7)
		  )
	)`, repo, stream, owner, writer, writers, updatedAt, subscriber, sensitivity, deadlineAt).Scan(&newerEvent)
	return newerEvent, err
}

func overdueCheckpointUUID(r overdueCheckpointRow) string {
	return uuid.NewSHA1(overdueUUIDNamespace, []byte(fmt.Sprintf("%s|%s|%s|%s|%s",
		r.repo, r.subscriber, r.sourceKind, r.sourceKey, r.deadlineAt.UTC().Format(time.RFC3339Nano)))).String()
}

func insertOverdueDecisionTx(ctx context.Context, tx pgx.Tx, r overdueCheckpointRow, state, skipReason string) (string, bool, error) {
	id := overdueCheckpointUUID(r)
	var evID, recID any
	var ver, wsRev any
	if r.sourceEventID != "" {
		evID = r.sourceEventID
	}
	if r.sourceRecordID != "" {
		recID = r.sourceRecordID
		ver = r.sourceVersion
	}
	if r.workstreamRevision > 0 {
		wsRev = r.workstreamRevision
	}
	tag, err := tx.Exec(ctx, `INSERT INTO cairn.agent_overdue_checkpoint(
		checkpoint_id, repo, subscriber_consumer, source_kind, source_key, topic_key,
		sender, sensitivity, deadline_at, source_updated_at, source_event_id,
		source_record_id, source_version, workstream_revision, workstream_state,
		state, skip_reason, created_at, updated_at
	) VALUES($1::uuid,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::uuid,$12::uuid,$13,$14,$15,$16,$17,clock_timestamp(),clock_timestamp())
	ON CONFLICT (repo, subscriber_consumer, source_kind, source_key, deadline_at) DO NOTHING`,
		id, r.repo, r.subscriber, r.sourceKind, r.sourceKey, r.topicKey,
		r.sender, r.sensitivity, r.deadlineAt, r.sourceUpdatedAt, evID,
		recID, ver, wsRev, r.workstreamState, state, skipReason)
	return id, tag.RowsAffected() > 0, err
}

const activeNotStoppedSubscriber = `NOT EXISTS (
	SELECT 1 FROM cairn.agent_session a
	WHERE a.repo = $1 AND 'agent/' || a.agent_id::text = sub.consumer AND a.stopped = true
)`

func (s *Store) discoverDueNoticeOverdueTx(ctx context.Context, tx pgx.Tx, repo string, cutoff time.Time, limit int, fencedAt *time.Time, minEventPos int64, justInserted map[string]bool, out *OverdueSweepResult) error {
	rows, err := tx.Query(ctx, `SELECT
		e.event_id::text, e.position, e.publisher, e.record_id::text, e.version,
		e.sensitivity, e.destination_type, e.destination_name,
		COALESCE(e.notice_supersedes, ''), e.notice_expect_next_by, e.created_at,
		rec.consumer
	FROM cairn.agent_event e
	JOIN LATERAL (
		SELECT d.consumer FROM cairn.agent_delivery d WHERE d.event_id = e.event_id AND d.consumer LIKE 'agent/%'
		UNION
		SELECT q.recipient_inbox AS consumer FROM cairn.agent_organic_notice q WHERE q.event_id = e.event_id AND q.recipient_inbox LIKE 'agent/%'
	) rec ON true
	WHERE e.repo = $1
	  AND e.kind = 'notice'
	  AND e.notice_expect_next_by IS NOT NULL
	  AND e.notice_expect_next_by < $3
	  AND e.position > $4
	  AND ($2::timestamptz IS NULL OR e.created_at > $2)
	  AND NOT (e.destination_type = 'topic' AND rec.consumer = e.publisher)
	  AND NOT EXISTS (
	    SELECT 1 FROM cairn.agent_overdue_checkpoint oc
	    WHERE oc.repo = e.repo AND oc.subscriber_consumer = rec.consumer
	      AND oc.source_kind = 'notice' AND oc.source_key = e.event_id::text
	      AND oc.deadline_at = e.notice_expect_next_by
	  )
	ORDER BY e.notice_expect_next_by, e.position, rec.consumer
	LIMIT $5`, repo, fencedAt, cutoff, minEventPos, limit)
	if err != nil {
		return err
	}
	type noticeCand struct {
		row        overdueCheckpointRow
		pos        int64
		supersedes string
		destType   string
		destName   string
	}
	var cands []noticeCand
	var refs []overdueRecordVer
	for rows.Next() {
		var c noticeCand
		c.row.repo = repo
		c.row.sourceKind = "notice"
		if err = rows.Scan(&c.row.sourceEventID, &c.pos, &c.row.sender, &c.row.sourceRecordID, &c.row.sourceVersion,
			&c.row.sensitivity, &c.destType, &c.destName, &c.supersedes, &c.row.deadlineAt, &c.row.sourceUpdatedAt, &c.row.subscriber); err != nil {
			rows.Close()
			return err
		}
		c.row.sourceKey = c.row.sourceEventID
		if c.supersedes != "" {
			c.row.topicKey = c.supersedes
		} else if c.destType == "topic" {
			c.row.topicKey = "topic/" + c.destName
		} else {
			c.row.topicKey = "direct/" + c.row.subscriber
		}
		refs = append(refs, overdueRecordVer{recordID: c.row.sourceRecordID, version: c.row.sourceVersion})
		cands = append(cands, c)
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return err
	}
	recStates, err := lockAndInspectSourceRecordsTx(ctx, tx, repo, refs)
	if err != nil {
		return err
	}
	for _, c := range cands {
		rs := recStates[overdueRecordVer{recordID: c.row.sourceRecordID, version: c.row.sourceVersion}]
		if !rs.eligible {
			if _, ok, err := insertOverdueDecisionTx(ctx, tx, c.row, "skipped", "source_unavailable"); err != nil {
				return err
			} else if ok {
				out.SkippedItems++
			}
			continue
		}
		if rs.sensitivity == "local" {
			c.row.sensitivity = "local"
		}
		exceeded, err := subscriberCeilingExceededTx(ctx, tx, repo, c.row.subscriber, c.row.sensitivity)
		if err != nil {
			return err
		}
		if exceeded {
			if _, ok, err := insertOverdueDecisionTx(ctx, tx, c.row, "skipped", "ceiling_exceeded"); err != nil {
				return err
			} else if ok {
				out.SkippedItems++
			}
			continue
		}
		sat, err := isNoticeSatisfiedTx(ctx, tx, repo, c.row.sourceEventID, c.row.sender, c.row.subscriber, c.supersedes, c.destType, c.destName, c.row.sensitivity, c.pos, c.row.sourceUpdatedAt)
		if err != nil {
			return err
		}
		if sat {
			if _, ok, err := insertOverdueDecisionTx(ctx, tx, c.row, "satisfied", ""); err != nil {
				return err
			} else if ok {
				out.SatisfiedItems++
			}
			continue
		}
		id, ok, err := insertOverdueDecisionTx(ctx, tx, c.row, "coalesced_pending", "")
		if err != nil {
			return err
		}
		if ok {
			justInserted[id] = true
		}
	}
	return nil
}

func (s *Store) discoverDueRequestOverdueTx(ctx context.Context, tx pgx.Tx, repo string, cutoff time.Time, limit int, fencedAt *time.Time, minEventPos int64, justInserted map[string]bool, out *OverdueSweepResult) error {
	rows, err := tx.Query(ctx, `SELECT
		e.event_id::text, e.position, e.publisher, e.record_id::text, e.version,
		e.sensitivity, e.destination_type, e.destination_name,
		COALESCE(e.correlation_id::text, ''), e.request_expect_by, e.created_at,
		rec.consumer
	FROM cairn.agent_event e
	JOIN LATERAL (
		SELECT e.publisher AS consumer WHERE e.publisher LIKE 'agent/%'
		UNION
		SELECT sub.consumer FROM cairn.agent_subscription sub
		WHERE sub.repo = e.repo AND sub.active AND sub.consumer LIKE 'agent/%'
		  AND `+activeNotStoppedSubscriber+`
		  AND (
		    (e.destination_type = 'topic' AND sub.topic = 'overdue.' || e.destination_name)
		    OR (e.publisher NOT LIKE 'agent/%' AND sub.topic = 'request-overdue')
		  )
	) rec ON true
	WHERE e.repo = $1
	  AND e.kind = 'request'
	  AND e.request_expect_by IS NOT NULL
	  AND e.request_expect_by < $3
	  AND e.position > $4
	  AND ($2::timestamptz IS NULL OR e.created_at > $2)
	  AND NOT EXISTS (
	    SELECT 1 FROM cairn.agent_overdue_checkpoint oc
	    WHERE oc.repo = e.repo AND oc.subscriber_consumer = rec.consumer
	      AND oc.source_kind = 'request' AND oc.source_key = e.event_id::text
	      AND oc.deadline_at = e.request_expect_by
	  )
	ORDER BY e.request_expect_by, e.position, rec.consumer
	LIMIT $5`, repo, fencedAt, cutoff, minEventPos, limit)
	if err != nil {
		return err
	}
	type reqCand struct {
		row      overdueCheckpointRow
		pos      int64
		destType string
		destName string
		corrID   string
	}
	var cands []reqCand
	var refs []overdueRecordVer
	for rows.Next() {
		var c reqCand
		c.row.repo = repo
		c.row.sourceKind = "request"
		if err = rows.Scan(&c.row.sourceEventID, &c.pos, &c.row.sender, &c.row.sourceRecordID, &c.row.sourceVersion,
			&c.row.sensitivity, &c.destType, &c.destName, &c.corrID, &c.row.deadlineAt, &c.row.sourceUpdatedAt, &c.row.subscriber); err != nil {
			rows.Close()
			return err
		}
		c.row.sourceKey = c.row.sourceEventID
		c.row.topicKey = c.destType + "/" + c.destName
		refs = append(refs, overdueRecordVer{recordID: c.row.sourceRecordID, version: c.row.sourceVersion})
		cands = append(cands, c)
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return err
	}
	recStates, err := lockAndInspectSourceRecordsTx(ctx, tx, repo, refs)
	if err != nil {
		return err
	}
	for _, c := range cands {
		rs := recStates[overdueRecordVer{recordID: c.row.sourceRecordID, version: c.row.sourceVersion}]
		if !rs.eligible {
			if _, ok, err := insertOverdueDecisionTx(ctx, tx, c.row, "skipped", "source_unavailable"); err != nil {
				return err
			} else if ok {
				out.SkippedItems++
			}
			continue
		}
		if rs.sensitivity == "local" {
			c.row.sensitivity = "local"
		}
		exceeded, err := subscriberCeilingExceededTx(ctx, tx, repo, c.row.subscriber, c.row.sensitivity)
		if err != nil {
			return err
		}
		if exceeded {
			if _, ok, err := insertOverdueDecisionTx(ctx, tx, c.row, "skipped", "ceiling_exceeded"); err != nil {
				return err
			} else if ok {
				out.SkippedItems++
			}
			continue
		}
		sat, err := isRequestSatisfiedTx(ctx, tx, repo, c.row.sourceEventID, c.destType, c.destName, c.corrID, c.row.sensitivity, c.pos)
		if err != nil {
			return err
		}
		if sat {
			if _, ok, err := insertOverdueDecisionTx(ctx, tx, c.row, "satisfied", ""); err != nil {
				return err
			} else if ok {
				out.SatisfiedItems++
			}
			continue
		}
		id, ok, err := insertOverdueDecisionTx(ctx, tx, c.row, "coalesced_pending", "")
		if err != nil {
			return err
		}
		if ok {
			justInserted[id] = true
		}
	}
	return nil
}

func (s *Store) discoverDueWorkstreamOverdueTx(ctx context.Context, tx pgx.Tx, repo string, cutoff time.Time, limit int, fencedAt *time.Time, justInserted map[string]bool, out *OverdueSweepResult) error {
	rows, err := tx.Query(ctx, `SELECT
		ws.stream, ws.writer, ws.revision, ws.state,
		ws.sensitivity, ws.next_checkpoint, ws.updated_at, sub.consumer
	FROM cairn.workstream_status ws
	JOIN LATERAL (
		SELECT DISTINCT sub.consumer
		FROM cairn.agent_subscription sub
		WHERE sub.repo = ws.repo AND sub.active AND sub.consumer LIKE 'agent/%'
		  AND `+activeNotStoppedSubscriber+`
		  AND sub.topic IN ('workstream-overdue', 'overdue', 'workstream-overdue.' || ws.stream)
	) sub ON true
	WHERE ws.repo = $1
	  AND ws.state <> 'done'
	  AND ws.next_checkpoint IS NOT NULL
	  AND ws.next_checkpoint < $3
	  AND ($2::timestamptz IS NULL OR ws.updated_at > $2)
	  AND NOT EXISTS (
	    SELECT 1 FROM cairn.agent_overdue_checkpoint oc
	    WHERE oc.repo = ws.repo AND oc.subscriber_consumer = sub.consumer
	      AND oc.source_kind = 'workstream' AND oc.source_key = ws.stream
	      AND oc.deadline_at = ws.next_checkpoint
	  )
	ORDER BY ws.next_checkpoint, ws.stream, sub.consumer
	LIMIT $4`, repo, fencedAt, cutoff, limit)
	if err != nil {
		return err
	}
	var cands []overdueCheckpointRow
	for rows.Next() {
		var r overdueCheckpointRow
		r.repo = repo
		r.sourceKind = "workstream"
		if err = rows.Scan(&r.sourceKey, &r.sender, &r.workstreamRevision, &r.workstreamState,
			&r.sensitivity, &r.deadlineAt, &r.sourceUpdatedAt, &r.subscriber); err != nil {
			rows.Close()
			return err
		}
		r.topicKey = r.sourceKey
		cands = append(cands, r)
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return err
	}
	for _, r := range cands {
		exceeded, err := subscriberCeilingExceededTx(ctx, tx, repo, r.subscriber, r.sensitivity)
		if err != nil {
			return err
		}
		if exceeded {
			if _, ok, err := insertOverdueDecisionTx(ctx, tx, r, "skipped", "ceiling_exceeded"); err != nil {
				return err
			} else if ok {
				out.SkippedItems++
			}
			continue
		}
		sat, err := isWorkstreamSatisfiedTx(ctx, tx, repo, r.sourceKey, r.subscriber, r.sensitivity, r.deadlineAt)
		if err != nil {
			return err
		}
		if sat {
			if _, ok, err := insertOverdueDecisionTx(ctx, tx, r, "satisfied", ""); err != nil {
				return err
			} else if ok {
				out.SatisfiedItems++
			}
			continue
		}
		id, ok, err := insertOverdueDecisionTx(ctx, tx, r, "coalesced_pending", "")
		if err != nil {
			return err
		}
		if ok {
			justInserted[id] = true
		}
	}
	return nil
}

func hasPendingOverdueNoticeTx(ctx context.Context, tx pgx.Tx, repo, subscriber, sensitivity string) (bool, error) {
	var pending bool
	err := tx.QueryRow(ctx, `SELECT EXISTS(
		SELECT 1
		FROM cairn.agent_overdue_checkpoint oc
		JOIN cairn.agent_delivery d ON d.event_id = oc.notice_event_id AND d.consumer = oc.subscriber_consumer
		WHERE oc.repo = $1
		  AND oc.subscriber_consumer = $2
		  AND oc.sensitivity = $3
		  AND oc.state = 'emitted'
		  AND d.state IN ('pending', 'leased')
	)`, repo, subscriber, sensitivity).Scan(&pending)
	return pending, err
}

func formatOverdueNoticeBody(items []overdueCheckpointRow) string {
	var b strings.Builder
	b.WriteString("ACTION: review\nSUPERSEDES: none\nDUE: none\n")
	if len(items) == 1 {
		b.WriteString("SUMMARY: 1 declared checkpoint or expect_by deadline is overdue.\n\n")
	} else {
		fmt.Fprintf(&b, "SUMMARY: %d declared checkpoints or expect_by deadlines are overdue.\n\n", len(items))
	}
	for _, item := range items {
		dl := item.deadlineAt.UTC().Format(time.RFC3339)
		upd := item.sourceUpdatedAt.UTC().Format(time.RFC3339)
		switch item.sourceKind {
		case "workstream":
			fmt.Fprintf(&b, "- workstream %s (writer %s, state %s, rev %d): next_checkpoint %s passed without update since %s.\n",
				item.sourceKey, item.sender, item.workstreamState, item.workstreamRevision, dl, upd)
		case "notice":
			fmt.Fprintf(&b, "- notice %s from %s (topic %s, source %s@v%d): expect_next_by %s passed without newer update since %s.\n",
				item.sourceEventID, item.sender, item.topicKey, item.sourceRecordID, item.sourceVersion, dl, upd)
		case "request":
			fmt.Fprintf(&b, "- request %s (%s, from %s, source %s@v%d): expect_by %s passed without completion or response since %s.\n",
				item.sourceEventID, item.topicKey, item.sender, item.sourceRecordID, item.sourceVersion, dl, upd)
		}
	}
	return b.String()
}

func (s *Store) revalidateGroupPendingTx(ctx context.Context, tx pgx.Tx, repo string, group []overdueCheckpointRow, justInserted map[string]bool, out *OverdueSweepResult) ([]overdueCheckpointRow, error) {
	var refs []overdueRecordVer
	for _, r := range group {
		if r.sourceRecordID != "" && r.sourceVersion > 0 {
			refs = append(refs, overdueRecordVer{recordID: r.sourceRecordID, version: r.sourceVersion})
		}
	}
	// Always lock referenced memory_record rows FOR SHARE in deterministic UUID order before emitting.
	recStates, err := lockAndInspectSourceRecordsTx(ctx, tx, repo, refs)
	if err != nil {
		return nil, err
	}
	eligible := make([]overdueCheckpointRow, 0, len(group))
	for _, r := range group {
		if justInserted[r.checkpointID] {
			eligible = append(eligible, r)
			continue
		}
		markSkip := func(reason string) error {
			_, uErr := tx.Exec(ctx, `UPDATE cairn.agent_overdue_checkpoint SET state='skipped', skip_reason=$2, updated_at=clock_timestamp() WHERE checkpoint_id=$1::uuid`, r.checkpointID, reason)
			if uErr == nil {
				out.SkippedItems++
			}
			return uErr
		}
		if r.sourceKind == "notice" || r.sourceKind == "request" {
			rs := recStates[overdueRecordVer{recordID: r.sourceRecordID, version: r.sourceVersion}]
			if !rs.eligible {
				if err := markSkip("source_unavailable"); err != nil {
					return nil, err
				}
				continue
			}
			if rs.sensitivity == "local" && r.sensitivity != "local" {
				r.sensitivity = "local"
				if _, err := tx.Exec(ctx, `UPDATE cairn.agent_overdue_checkpoint SET sensitivity='local', updated_at=clock_timestamp() WHERE checkpoint_id=$1::uuid`, r.checkpointID); err != nil {
					return nil, err
				}
			}
		}
		exceeded, err := subscriberCeilingExceededTx(ctx, tx, repo, r.subscriber, r.sensitivity)
		if err != nil {
			return nil, err
		}
		if exceeded {
			if err := markSkip("ceiling_exceeded"); err != nil {
				return nil, err
			}
			continue
		}
		var satisfied bool
		switch r.sourceKind {
		case "notice":
			var pos int64
			var pub, sup, dType, dName string
			var createdAt time.Time
			err = tx.QueryRow(ctx, `SELECT position, publisher, COALESCE(notice_supersedes,''), destination_type, destination_name, created_at FROM cairn.agent_event WHERE event_id=$1::uuid`, r.sourceEventID).
				Scan(&pos, &pub, &sup, &dType, &dName, &createdAt)
			if errors.Is(err, pgx.ErrNoRows) {
				if err := markSkip("source_unavailable"); err != nil {
					return nil, err
				}
				continue
			}
			if err != nil {
				return nil, err
			}
			satisfied, err = isNoticeSatisfiedTx(ctx, tx, repo, r.sourceEventID, pub, r.subscriber, sup, dType, dName, r.sensitivity, pos, createdAt)
			if err != nil {
				return nil, err
			}
		case "request":
			var pos int64
			var pub, dType, dName, corr string
			err = tx.QueryRow(ctx, `SELECT position, publisher, destination_type, destination_name, COALESCE(correlation_id::text,'') FROM cairn.agent_event WHERE event_id=$1::uuid`, r.sourceEventID).
				Scan(&pos, &pub, &dType, &dName, &corr)
			if errors.Is(err, pgx.ErrNoRows) {
				if err := markSkip("source_unavailable"); err != nil {
					return nil, err
				}
				continue
			}
			if err != nil {
				return nil, err
			}
			if r.subscriber != pub {
				stopped, err := subscriberStoppedTx(ctx, tx, repo, r.subscriber)
				if err != nil {
					return nil, err
				}
				if stopped {
					if err := markSkip("recipient_stopped"); err != nil {
						return nil, err
					}
					continue
				}
				var subActive bool
				err = tx.QueryRow(ctx, `SELECT EXISTS(
					SELECT 1 FROM cairn.agent_subscription
					WHERE repo = $1 AND consumer = $2 AND active
					  AND (($3 = 'topic' AND topic = 'overdue.' || $4) OR ($5 NOT LIKE 'agent/%' AND topic = 'request-overdue'))
				)`, repo, r.subscriber, dType, dName, pub).Scan(&subActive)
				if err != nil {
					return nil, err
				}
				if !subActive {
					if err := markSkip("unsubscribed"); err != nil {
						return nil, err
					}
					continue
				}
			}
			satisfied, err = isRequestSatisfiedTx(ctx, tx, repo, r.sourceEventID, dType, dName, corr, r.sensitivity, pos)
			if err != nil {
				return nil, err
			}
		case "workstream":
			stopped, err := subscriberStoppedTx(ctx, tx, repo, r.subscriber)
			if err != nil {
				return nil, err
			}
			if stopped {
				if err := markSkip("recipient_stopped"); err != nil {
					return nil, err
				}
				continue
			}
			var subActive bool
			if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_subscription WHERE repo=$1 AND consumer=$2 AND active AND topic IN ('workstream-overdue', 'overdue', 'workstream-overdue.' || $3))`, repo, r.subscriber, r.sourceKey).Scan(&subActive); err != nil {
				return nil, err
			}
			if !subActive {
				if err := markSkip("unsubscribed"); err != nil {
					return nil, err
				}
				continue
			}
			satisfied, err = isWorkstreamSatisfiedTx(ctx, tx, repo, r.sourceKey, r.subscriber, r.sensitivity, r.deadlineAt)
			if err != nil {
				return nil, err
			}
		}
		if satisfied {
			if _, err = tx.Exec(ctx, `UPDATE cairn.agent_overdue_checkpoint SET state='satisfied', updated_at=clock_timestamp() WHERE checkpoint_id=$1::uuid`, r.checkpointID); err != nil {
				return nil, err
			}
			out.SatisfiedItems++
			continue
		}
		eligible = append(eligible, r)
	}
	return eligible, nil
}

func (s *Store) revalidateAndEmitOverdueBatchesTx(ctx context.Context, tx pgx.Tx, repo string, maxPending int, justInserted map[string]bool, out *OverdueSweepResult) ([]error, error) {
	if maxPending < 50 {
		maxPending = 50
	}
	rows, err := tx.Query(ctx, `SELECT
		checkpoint_id::text, repo, subscriber_consumer, source_kind, source_key,
		topic_key, sender, sensitivity, deadline_at, source_updated_at,
		COALESCE(source_event_id::text, ''), COALESCE(source_record_id::text, ''),
		COALESCE(source_version, 0), COALESCE(workstream_revision, 0), workstream_state
	FROM cairn.agent_overdue_checkpoint
	WHERE repo = $1 AND state = 'coalesced_pending'
	ORDER BY deadline_at, checkpoint_id
	LIMIT $2
	FOR UPDATE`, repo, maxPending)
	if err != nil {
		return nil, err
	}
	type subKey struct {
		subscriber  string
		sensitivity string
	}
	var order []subKey
	groups := make(map[subKey][]overdueCheckpointRow)
	for rows.Next() {
		var r overdueCheckpointRow
		if err = rows.Scan(&r.checkpointID, &r.repo, &r.subscriber, &r.sourceKind, &r.sourceKey,
			&r.topicKey, &r.sender, &r.sensitivity, &r.deadlineAt, &r.sourceUpdatedAt,
			&r.sourceEventID, &r.sourceRecordID, &r.sourceVersion, &r.workstreamRevision, &r.workstreamState); err != nil {
			rows.Close()
			return nil, err
		}
		k := subKey{subscriber: r.subscriber, sensitivity: r.sensitivity}
		if _, exists := groups[k]; !exists {
			order = append(order, k)
		}
		groups[k] = append(groups[k], r)
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return nil, err
	}

	sort.Slice(order, func(i, j int) bool {
		if order[i].subscriber != order[j].subscriber {
			return order[i].subscriber < order[j].subscriber
		}
		return order[i].sensitivity < order[j].sensitivity
	})

	var retainedRefusals []error
	for _, k := range order {
		group := groups[k]
		pending, err := hasPendingOverdueNoticeTx(ctx, tx, repo, k.subscriber, k.sensitivity)
		if err != nil {
			return nil, err
		}
		if pending {
			out.CoalescedItems += len(group)
			continue
		}
		eligible, err := s.revalidateGroupPendingTx(ctx, tx, repo, group, justInserted, out)
		if err != nil {
			return nil, err
		}
		// Keep only items whose effective sensitivity still matches k.sensitivity;
		// any item upgraded to local will emit on a local pass.
		filtered := make([]overdueCheckpointRow, 0, len(eligible))
		for _, item := range eligible {
			if item.sensitivity == k.sensitivity {
				filtered = append(filtered, item)
			} else {
				out.CoalescedItems++
			}
		}
		if len(filtered) == 0 {
			continue
		}
		batch := filtered
		overflow := 0
		if len(batch) > MaxCoalescedOverdueItems {
			overflow = len(batch) - MaxCoalescedOverdueItems
			batch = batch[:MaxCoalescedOverdueItems]
		}
		ids := make([]string, len(batch))
		sources := make([]string, len(batch))
		for i, item := range batch {
			ids[i] = item.checkpointID
			sources[i] = item.sourceKind + ":" + item.sourceKey
		}
		sortedIDs := append([]string(nil), ids...)
		sort.Strings(sortedIDs)
		batchKey := fmt.Sprintf("%s|%s|%s|%s", repo, k.subscriber, k.sensitivity, strings.Join(sortedIDs, ","))
		noticeEventID := uuid.NewSHA1(overdueUUIDNamespace, []byte("event|"+batchKey)).String()
		noticeRecordID := uuid.NewSHA1(overdueUUIDNamespace, []byte("record|"+batchKey)).String()
		body := formatOverdueNoticeBody(batch)

		nested, err := tx.Begin(ctx)
		if err != nil {
			return nil, err
		}
		draft := Draft{
			Body:        body,
			Kind:        "note",
			ClaimType:   "self",
			Sensitivity: k.sensitivity,
			Scope:       Scope{Repo: repo, TaskID: "overdue-" + noticeEventID, RunID: "*"},
		}
		if err = draft.validate(); err != nil {
			_ = nested.Rollback(ctx)
			return nil, err
		}
		tag, err := nested.Exec(ctx, `INSERT INTO cairn.memory_record(record_id,current_version,sensitivity) VALUES($1::uuid,1,$2) ON CONFLICT (record_id) DO NOTHING`, noticeRecordID, k.sensitivity)
		if err == nil && tag.RowsAffected() > 0 {
			_, err = insertVersion(ctx, nested, noticeRecordID, 1, draft)
		}
		if err == nil {
			overdueSum := "1 declared checkpoint or expect_by deadline is overdue."
			if len(batch) > 1 {
				overdueSum = fmt.Sprintf("%d declared checkpoints or expect_by deadlines are overdue.", len(batch))
			}
			pubReq := PublishEventRequest{
				Summary: overdueSum,
				Action:  "review",
				NoticePolicy: &NoticeWakePolicyRequest{
					Version:      1,
					Urgency:      "urgent",
					DeferSeconds: 0,
				},
				Repo:        repo,
				Kind:        "notice",
				Ref:         RecordVersionRef{RecordID: noticeRecordID, Version: 1},
				Destination: EventDestination{Type: "agent", Name: k.subscriber},
			}
			_, err = s.publishDaemonEventTx(ctx, nested, pubReq, Destination{Name: "local", AllowLocal: true}, noticeEventID)
		}
		if err != nil {
			if rbErr := nested.Rollback(ctx); rbErr != nil {
				return nil, rbErr
			}
			var refusal *Error
			if !errors.As(err, &refusal) {
				return nil, err
			}
			if refusal.Admission != nil {
				retainedRefusals = append(retainedRefusals, err)
			}
			switch {
			case refusal.Admission != nil,
				refusal.Code == "CLOSURE_RESERVE_FULL",
				refusal.Code == "CLOSURE_UNRECONCILED",
				refusal.Code == "RATE_UNRECONCILED",
				refusal.Code == "EVENT_ADMISSION_REFUSED":
				if _, uErr := tx.Exec(ctx, `UPDATE cairn.agent_overdue_checkpoint SET last_error_code=$2, updated_at=clock_timestamp() WHERE checkpoint_id=ANY($1::uuid[])`, ids, refusal.Code); uErr != nil {
					return nil, uErr
				}
				out.DeferredNotices++
				continue
			default:
				return nil, err
			}
		}
		if err = nested.Commit(ctx); err != nil {
			return nil, err
		}
		if _, err = tx.Exec(ctx, `UPDATE cairn.agent_overdue_checkpoint SET state='emitted', notice_event_id=$2::uuid, notice_record_id=$3::uuid, last_error_code='', emitted_at=clock_timestamp(), updated_at=clock_timestamp() WHERE checkpoint_id=ANY($1::uuid[])`, ids, noticeEventID, noticeRecordID); err != nil {
			return nil, err
		}
		out.CoalescedItems += overflow
		out.EmittedNotices++
		out.EmittedItems += len(batch)
		out.Published = append(out.Published, OverdueNoticeSummary{
			EventID:    noticeEventID,
			RecordID:   noticeRecordID,
			Subscriber: k.subscriber,
			ItemCount:  len(batch),
			Sources:    sources,
		})
	}
	return retainedRefusals, nil
}
