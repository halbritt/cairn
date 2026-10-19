package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

const stoppedDeliveryNoticeSchema = "cairn.stopped-delivery-notice/1"

type StoppedDeliveryNotificationRequest struct {
	RequestID          string `json:"request_id"`
	Repo               string `json:"repo"`
	DeliveryID         string `json:"delivery_id"`
	RecipientAgentID   string `json:"recipient_agent_id"`
	RecipientExecution string `json:"recipient_execution_id"`
	Stage              string `json:"stage"`
	Fingerprint        string `json:"fingerprint"`
}

type StoppedDeliveryNotificationResult struct {
	DeliveryID          string `json:"delivery_id"`
	EventID             string `json:"event_id,omitempty"`
	Publisher           string `json:"publisher,omitempty"`
	NotificationID      string `json:"notification_id"`
	NotificationEventID string `json:"notification_event_id,omitempty"`
	Status              string `json:"status"`
	Reason              string `json:"reason,omitempty"`
}

type stoppedDeliveryNoticePayload struct {
	Schema            string                     `json:"schema"`
	DeliveryID        string                     `json:"delivery_id"`
	EventID           string                     `json:"event_id"`
	Kind              string                     `json:"kind"`
	Ref               RecordVersionRef           `json:"ref"`
	Recipient         DiagnosisRecipient         `json:"recipient"`
	State             string                     `json:"state"`
	Stage             string                     `json:"stage"`
	Attempts          int                        `json:"attempts"`
	Held              bool                       `json:"held"`
	Execution         string                     `json:"execution"`
	Admission         string                     `json:"admission"`
	WaitingSeconds    int64                      `json:"waiting_seconds"`
	ReportedCondition string                     `json:"reported_condition,omitempty"`
	RetainedWake      *stoppedDeliveryNoticeWake `json:"retained_wake,omitempty"`
	DecisionOwner     string                     `json:"decision_owner"`
}

type stoppedDeliveryNoticeWake struct {
	Transport   string     `json:"transport"`
	Status      string     `json:"status"`
	AttemptedAt *time.Time `json:"attempted_at,omitempty"`
	Freshness   string     `json:"freshness"`
	Applies     string     `json:"applies"`
}

// NotifyStoppedDeliveryPublisher sends at most one deduplicated notice to the
// original publisher of an unresolved delivery to a confirmed-stopped session.
// It leaves the original delivery state untouched and refuses to notify an
// unavailable recipient or recursively notify about a stopped-delivery notice.
func (s *Store) NotifyStoppedDeliveryPublisher(ctx context.Context, req StoppedDeliveryNotificationRequest) (StoppedDeliveryNotificationResult, error) {
	out := StoppedDeliveryNotificationResult{DeliveryID: req.DeliveryID, NotificationID: req.RequestID}
	if !s.channel.Operator || s.channel.Repo != "" {
		return out, failure("AUTHORITY_DENIED", "stopped delivery notification requires an unscoped operator channel")
	}
	for _, id := range []string{req.RequestID, req.DeliveryID, req.RecipientAgentID, req.RecipientExecution} {
		if err := validID(id); err != nil {
			return out, err
		}
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return out, err
	}
	req.Repo = repo
	if strings.TrimSpace(req.Stage) == "" || strings.TrimSpace(req.Fingerprint) == "" {
		return out, failure("INVALID_REQUEST", "stage and fingerprint required")
	}
	canonical, err := json.Marshal(req)
	if err != nil {
		return out, err
	}
	digest := sha256.Sum256(canonical)
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(context.Background())
	if err = lock(ctx, tx, "request:"+s.channel.Principal+":stopped-delivery-notify:"+req.RequestID); err != nil {
		return out, err
	}
	var previousDigest, response []byte
	err = tx.QueryRow(ctx, `SELECT request_digest,response FROM cairn.mutation_request WHERE caller=$1 AND operation='stopped-delivery-notify' AND request_id=$2`, s.channel.Principal, req.RequestID).Scan(&previousDigest, &response)
	if err == nil {
		if !bytes.Equal(previousDigest, digest[:]) {
			return out, failure("IDEMPOTENCY_CONFLICT", "request UUID already used with different content")
		}
		if err = json.Unmarshal(response, &out); err != nil {
			return out, err
		}
		out.Status = "duplicate_suppressed"
		return out, tx.Commit(ctx)
	} else if !errors.Is(err, pgx.ErrNoRows) {
		return out, err
	}
	return s.notifyStoppedDeliveryPublisherTx(ctx, tx, req, digest[:])
}

const stoppedDeliveryNotifyQuery = `SELECT d.consumer,d.state,d.attempts,d.available_at,
 e.event_id::text,e.repo,e.publisher,e.kind,e.record_id::text,e.version,e.sensitivity,COALESCE(e.correlation_id::text,''),COALESCE(v.body,''),
 NOT (` + wakeHold + `) OR NOT (` + sessionInboxHold + `),
 CASE WHEN d.attempts=0 OR (
  d.attempts=(SELECT count(*) FROM cairn.agent_wake_attempt w WHERE w.delivery_id=d.delivery_id)
  AND NOT EXISTS(SELECT 1 FROM cairn.agent_wake_attempt w WHERE w.delivery_id=d.delivery_id AND w.state<>'prepared' AND w.process_state<>'prelaunch_failed')
  AND NOT EXISTS(SELECT 1 FROM cairn.agent_session_attempt n WHERE n.delivery_id=d.delivery_id)
 ) THEN 'never_launched' ELSE 'may_have_executed' END,
 s.agent_id::text,s.execution_id::text,s.stopped,clock_timestamp()
 FROM cairn.agent_delivery d
 JOIN cairn.agent_event e USING(event_id)
 LEFT JOIN cairn.record_version v ON v.record_id=e.record_id AND v.version=e.version
 LEFT JOIN cairn.agent_session s ON d.consumer='agent/'||s.agent_id::text AND s.repo=e.repo
 WHERE d.delivery_id=$1 FOR SHARE OF d`

func (s *Store) notifyStoppedDeliveryPublisherTx(ctx context.Context, tx pgx.Tx, req StoppedDeliveryNotificationRequest, digest []byte) (StoppedDeliveryNotificationResult, error) {
	out := StoppedDeliveryNotificationResult{DeliveryID: req.DeliveryID, NotificationID: req.RequestID}
	var consumer, state, eventID, repo, publisher, kind, recordID, sensitivity, correlationID, body, execution string
	var attempts, version int
	var available, now time.Time
	var held bool
	var agentID, executionID *string
	var stopped *bool
	err := tx.QueryRow(ctx, stoppedDeliveryNotifyQuery, req.DeliveryID).Scan(
		&consumer, &state, &attempts, &available,
		&eventID, &repo, &publisher, &kind, &recordID, &version, &sensitivity, &correlationID, &body,
		&held, &execution, &agentID, &executionID, &stopped, &now,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, failure("NOT_FOUND", "delivery not found")
	}
	if err != nil {
		return out, err
	}
	if repo != req.Repo {
		return out, failure("INVALID_REQUEST", "delivery does not belong to the specified repository")
	}
	out.EventID, out.Publisher = eventID, publisher
	if publisher == s.channel.Principal || publisher == consumer || strings.Contains(body, `"schema":"`+stoppedDeliveryNoticeSchema+`"`) {
		out.Status, out.Reason = "skipped", "recursive_or_self"
		return out, tx.Commit(ctx)
	}
	if state == "handled" || state == "failed" || state == "ignored" {
		out.Status, out.Reason = "skipped", "delivery_resolved"
		return out, tx.Commit(ctx)
	}
	if agentID == nil || stopped == nil || !*stopped || *agentID != req.RecipientAgentID || *executionID != req.RecipientExecution {
		out.Status, out.Reason = "skipped", "recipient_not_stopped"
		return out, tx.Commit(ctx)
	}
	if available, reason, err := publisherRecipientAvailable(ctx, tx, req.Repo, publisher); err != nil {
		return out, err
	} else if !available {
		out.Status, out.Reason = "recipient_unavailable", reason
		return out, tx.Commit(ctx)
	}
	return s.publishStoppedDeliveryNoticeTx(ctx, tx, req, out, digest, state, attempts, available, now, eventID, publisher, kind, recordID, version, sensitivity, correlationID, held, execution)
}

func publisherRecipientAvailable(ctx context.Context, tx pgx.Tx, repo, publisher string) (bool, string, error) {
	if !strings.HasPrefix(publisher, "agent/") {
		return true, "", nil
	}
	suffix := strings.TrimPrefix(publisher, "agent/")
	if validID(suffix) != nil {
		return true, "", nil
	}
	var stopped, live, currentGen bool
	err := tx.QueryRow(ctx, `SELECT s.stopped,s.expires_at>clock_timestamp(),s.database_generation=(SELECT generation FROM cairn.retrieval_generation WHERE singleton) FROM cairn.agent_session s WHERE s.agent_id=$1::uuid AND s.repo=$2`, suffix, repo).Scan(&stopped, &live, &currentGen)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, "publisher_session_not_found", nil
	}
	if err != nil {
		return false, "", err
	}
	switch {
	case stopped:
		return false, "publisher_stopped", nil
	case !currentGen:
		return false, "publisher_replaced", nil
	case !live:
		return false, "publisher_offline", nil
	default:
		return true, "", nil
	}
}

func (s *Store) publishStoppedDeliveryNoticeTx(ctx context.Context, tx pgx.Tx, req StoppedDeliveryNotificationRequest, out StoppedDeliveryNotificationResult, digest []byte, state string, attempts int, available, now time.Time, eventID, publisher, kind, recordID string, version int, sensitivity, correlationID string, held bool, execution string) (StoppedDeliveryNotificationResult, error) {
	diagnoses, err := deliveryDiagnoses(ctx, tx, []string{req.DeliveryID}, sensitivity == "local")
	if err != nil {
		return out, err
	}
	diag := diagnoses[req.DeliveryID]
	if diag == nil || diag.Recipient.Presence != "stopped" || (diag.Stage != "waiting" && diag.Stage != "leased" && diag.Stage != "held") {
		out.Status, out.Reason = "skipped", "condition_not_applicable"
		return out, tx.Commit(ctx)
	}
	waited := int64(0)
	if diag.WaitingSeconds != nil {
		waited = *diag.WaitingSeconds
	} else if now.After(available) {
		waited = int64(now.Sub(available) / time.Second)
	}
	admission := "not_admitted"
	if diag.Stage == "held" || held || diag.Stage == "leased" || attempts > 0 || execution == "may_have_executed" {
		admission = "unknown"
	}
	var reported string
	var retainedWake *stoppedDeliveryNoticeWake
	if h := diag.Host; h != nil && (h.Applies == "exact" || h.Applies == "session") {
		if h.Reported != "" && h.Reported != "none" {
			reported = h.Reported
		} else if h.Condition != "" && h.Condition != "unknown" && h.Condition != "none" {
			reported = h.Condition
		}
		if reported == "wake_retained" || reported == "native_admission_retained" || h.Wake != nil {
			admission = "unknown"
		}
		if h.Wake != nil {
			retainedWake = &stoppedDeliveryNoticeWake{Transport: h.Wake.Transport, Status: h.Wake.Status, AttemptedAt: h.Wake.AttemptedAt, Freshness: h.Freshness, Applies: h.Applies}
		}
	}
	payload := stoppedDeliveryNoticePayload{
		Schema: stoppedDeliveryNoticeSchema, DeliveryID: req.DeliveryID, EventID: eventID, Kind: kind,
		Ref: RecordVersionRef{RecordID: recordID, Version: version}, Recipient: diag.Recipient,
		State: state, Stage: diag.Stage, Attempts: attempts, Held: held, Execution: execution,
		Admission: admission, WaitingSeconds: waited, ReportedCondition: reported,
		RetainedWake: retainedWake, DecisionOwner: "operator_or_publisher",
	}
	return s.commitStoppedDeliveryNoticeTx(ctx, tx, req, out, digest, publisher, eventID, correlationID, sensitivity, payload)
}

func (s *Store) commitStoppedDeliveryNoticeTx(ctx context.Context, tx pgx.Tx, req StoppedDeliveryNotificationRequest, out StoppedDeliveryNotificationResult, digest []byte, publisher, eventID, correlationID, sensitivity string, payload stoppedDeliveryNoticePayload) (StoppedDeliveryNotificationResult, error) {
	if _, err := retrievalGeneration(ctx, tx); err != nil {
		return out, err
	}
	if err := lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
		return out, err
	}
	rawPayload, err := json.Marshal(payload)
	if err != nil {
		return out, err
	}
	draft := Draft{
		Body:        string(rawPayload),
		Kind:        "note",
		ClaimType:   "self",
		Sensitivity: sensitivity,
		Scope:       Scope{Repo: req.Repo, TaskID: "*", RunID: "*"},
	}
	if err = draft.validate(); err != nil {
		return out, err
	}
	noteID := uuid.NewString()
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.memory_record(record_id,current_version,sensitivity) VALUES($1,1,$2)`, noteID, sensitivity); err != nil {
		return out, err
	}
	rec, err := insertVersion(ctx, tx, noteID, 1, draft)
	if err != nil {
		return out, err
	}
	corr := correlationID
	if corr == "" {
		corr = eventID
	}
	newEventID := uuid.NewString()
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.agent_event(event_id,repo,publisher,kind,record_id,version,sensitivity,destination_type,destination_name,causation_id,correlation_id) VALUES($1,$2,$3,'notice',$4,$5,$6,'agent',$7,$8::uuid,$9::uuid)`,
		newEventID, req.Repo, s.channel.Principal, rec.RecordID, rec.Version, sensitivity, publisher, eventID, corr,
	); err != nil {
		return out, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.agent_delivery(delivery_id,event_id,consumer) VALUES($1,$2,$3)`, uuid.NewString(), newEventID, publisher); err != nil {
		return out, err
	}
	out.NotificationEventID = newEventID
	out.Status = "sent"
	encoded, err := json.Marshal(out)
	if err != nil {
		return out, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.mutation_request(caller,operation,request_id,request_digest,response) VALUES($1,'stopped-delivery-notify',$2,$3,$4)`, s.channel.Principal, req.RequestID, digest, encoded); err != nil {
		return out, err
	}
	return out, tx.Commit(ctx)
}
