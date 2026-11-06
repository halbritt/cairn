package core

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
)

// Closure reservations protect the capacity that accepted obligations need
// to finish: the reply a request delivery may still receive and the terminal
// notice a wait owes its owner. A reservation is created with its obligation,
// consumed only by the exact reply or notice, and released only by an
// explicit terminal decision. Completion of the request delivery never
// releases it, and no expiry is invented: an ordinary request without a
// declared deadline keeps its reservation until a reply arrives or its
// publisher or an operator closes it.
const (
	ClosureRequestReply = "request_reply"
	ClosureGroupReply   = "group_reply"
	ClosurePoolReply    = "pool_reply"
	ClosureWaitNotice   = "wait_notice"
)

// closureTerminalControlCodes maps the stop-control codes that end a request
// delivery by an explicit publisher, operator or declared-deadline decision.
// A consumer's own failure (failDelivery without control) and settlement are
// deliberately absent.
var closureTerminalControlCodes = map[string]string{
	"operator_cancelled": "work_cancelled",
	"admission_expired":  "request_expired",
	"task_deadline":      "request_expired",
	"consumer_retired":   "stopped_expired",
	"operator_released":  "operator_released",
}

// ClosureCounts reports closure capacity for one scope. Counts are absent,
// not zero, until a complete reconciliation exists for the current restore
// generation, because legacy obligations would otherwise be missing.
type ClosureCounts struct {
	Available            bool   `json:"available"`
	Reason               string `json:"reason,omitempty"`
	RestoreGeneration    int64  `json:"restore_generation"`
	Reserved             *int64 `json:"reserved,omitempty"`
	DeliveredOutstanding *int64 `json:"delivered_outstanding,omitempty"`
	Outstanding          *int64 `json:"outstanding,omitempty"`
	Legacy               *int64 `json:"legacy,omitempty"`
	LegacyUnreserved     *int64 `json:"legacy_unreserved,omitempty"`
}

type ClosureReservation struct {
	ReservationID      string    `json:"reservation_id"`
	Repo               string    `json:"repo"`
	Obligation         string    `json:"obligation"`
	RequestEventID     string    `json:"request_event_id,omitempty"`
	RequestDeliveryID  string    `json:"request_delivery_id,omitempty"`
	WaitUUID           string    `json:"wait_uuid,omitempty"`
	ExpectedSender     string    `json:"expected_sender,omitempty"`
	Recipient          string    `json:"recipient"`
	CorrelationID      string    `json:"correlation_id,omitempty"`
	State              string    `json:"state"`
	Revision           int       `json:"revision"`
	ConsumedEventID    string    `json:"consumed_event_id,omitempty"`
	ConsumedDeliveryID string    `json:"consumed_delivery_id,omitempty"`
	ConsumedRedacted   bool      `json:"consumed_redacted,omitempty"`
	ReleaseReason      string    `json:"release_reason,omitempty"`
	ReleasedBy         string    `json:"released_by,omitempty"`
	Legacy             bool      `json:"legacy"`
	RestoreGeneration  int64     `json:"restore_generation"`
	CreatedAt          time.Time `json:"created_at"`
	TransitionedAt     time.Time `json:"transitioned_at"`
}

type ClosureReservationQuery struct {
	Repo       string `json:"repo,omitempty"`
	State      string `json:"state,omitempty"`
	Obligation string `json:"obligation,omitempty"`
	After      string `json:"after,omitempty"`
	Limit      int    `json:"limit,omitempty"`
}

type ClosureReservationPage struct {
	Scope        string               `json:"scope"`
	Counts       ClosureCounts        `json:"counts"`
	Reservations []ClosureReservation `json:"reservations"`
	More         bool                 `json:"more"`
	NextAfter    string               `json:"next_after,omitempty"`
}

type CloseReplyObligationItem struct {
	ReservationID    string `json:"reservation_id"`
	ExpectedRevision int    `json:"expected_revision"`
}

type CloseReplyObligationsRequest struct {
	RequestID string                     `json:"request_id"`
	Repo      string                     `json:"repo,omitempty"`
	Reason    string                     `json:"reason"`
	Items     []CloseReplyObligationItem `json:"items"`
}

type CloseReplyObligationsResult struct {
	Reservations []ClosureReservation `json:"reservations"`
}

// ClosureReconcileRequest runs one bounded reconciliation batch. Limit caps
// the rows each step may change (default 500, at most 5000).
type ClosureReconcileRequest struct {
	RequestID string `json:"request_id"`
	Repo      string `json:"repo,omitempty"`
	Limit     int    `json:"limit,omitempty"`
}

// ClosureReconciliation reports one batch. Complete means this batch found
// every candidate class exhausted and recorded completion for the restore
// generation; More means another batch is needed and nothing was recorded.
// Completion is evidence for an enforcement gate together with writer
// compatibility, not the gate itself.
type ClosureReconciliation struct {
	Repo              string     `json:"repo"`
	RestoreGeneration int64      `json:"restore_generation"`
	Released          int64      `json:"released"`
	LegacyReserved    int64      `json:"legacy_reserved"`
	Adopted           int64      `json:"adopted"`
	More              bool       `json:"more"`
	Complete          bool       `json:"complete"`
	LegacyRows        *int64     `json:"legacy_rows,omitempty"`
	LegacyUnreserved  *int64     `json:"legacy_unreserved,omitempty"`
	ReconciledAt      *time.Time `json:"reconciled_at,omitempty"`
}

const (
	closureReconcileDefaultLimit = 500
	closureReconcileMaxLimit     = 5000
	closureSweepLimit            = 100
)

// closureScope selects the reservations a release pass examines. All covers
// every reservation in the repository ("" repo: every repository). A positive
// Limit bounds how many reservations one pass releases.
type closureScope struct {
	All         bool
	DeliveryIDs []string
	EventIDs    []string
	WaitUUIDs   []string
	Limit       int
}

// validatedClosure is the reserved reply closure an incoming publication
// will consume. matchReplyClosureTx resolves and row-locks it under the
// collection lock before any insert, so admission can exempt exactly this
// reply; consumeReplyClosureTx then binds it to the inserted reply.
type validatedClosure struct {
	ReservationID string
	Obligation    string
	Repo          string
	Recipient     string
	Sender        string
}

// closureAdmitTx refuses n new obligations for recipient when an enforced
// policy's closure capacity would be exceeded. The caller holds the
// collection insertion lock. Disabled and observe modes never refuse.
func closureAdmitTx(ctx context.Context, tx pgx.Tx, repo, recipient string, n int64) error {
	if n <= 0 {
		return nil
	}
	policy, err := admissionPolicyTx(ctx, tx, repo)
	if err != nil {
		return err
	}
	if policy.Policy.Mode != "enforce" || policy.Policy.Limits == nil {
		return nil
	}
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return err
	}
	reconciled, err := closureReconciledTx(ctx, tx, repo, generation)
	if err != nil {
		return err
	}
	if !reconciled {
		return failure("CLOSURE_UNRECONCILED", fmt.Sprintf("closure reservations are not reconciled for restore generation %d (policy revision %d); new obligations wait for an operator reconciliation", generation, policy.Revision))
	}
	collection, own, err := closureOutstandingTx(ctx, tx, repo, recipient)
	if err != nil {
		return err
	}
	limits := policy.Policy.Limits
	if collection+n > limits.MaxClosureReserved {
		return closureReserveFull("closure_collection", policy.Revision, n > limits.MaxClosureReserved)
	}
	if own+n > limits.MaxRecipientClosureReserved {
		return closureReserveFull("closure_recipient", policy.Revision, n > limits.MaxRecipientClosureReserved)
	}
	return nil
}

// closureReserveFull carries the typed admission details beside its own code:
// the dimension and policy revision only, never counts or other principals.
// Demand larger than the limit itself can never be admitted unchanged.
func closureReserveFull(dimension string, revision int64, exceedsLimit bool) error {
	details := &EventAdmissionRefusal{Dimension: dimension, PolicyRevision: revision, RetryAfterSeconds: 1, Retryable: true}
	guidance := "retry unchanged intent after accepted obligations close"
	if exceedsLimit {
		details.RetryAfterSeconds, details.Retryable = 0, false
		guidance = "the obligation needs more closure capacity than the policy allows; change the request or the policy"
	}
	return &Error{Code: "CLOSURE_RESERVE_FULL", Message: fmt.Sprintf("closure capacity dimension %s at policy revision %d; %s", dimension, revision, guidance), Admission: details}
}

const closureOutstandingPredicate = `(r.state='reserved' OR (r.state='delivered' AND EXISTS(SELECT 1 FROM cairn.agent_delivery cd WHERE cd.delivery_id=r.consumed_delivery_id AND cd.state IN ('pending','leased'))))`

// closureOutstandingTx counts reserved closures plus consumed closures whose
// reply or notice delivery is still pending or leased.
func closureOutstandingTx(ctx context.Context, tx pgx.Tx, repo, recipient string) (collection, own int64, err error) {
	err = tx.QueryRow(ctx, `SELECT count(*), count(*) FILTER (WHERE r.recipient=$2) FROM cairn.agent_closure_reservation r WHERE r.repo=$1 AND `+closureOutstandingPredicate, repo, recipient).Scan(&collection, &own)
	return collection, own, err
}

func closureReconciledTx(ctx context.Context, tx pgx.Tx, repo string, generation int64) (bool, error) {
	var ok bool
	err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_closure_reconciliation WHERE repo=$1 AND restore_generation=$2)`, repo, generation).Scan(&ok)
	return ok, err
}

// closureCountsTx reports counts for recipient, or for the collection when
// recipient is empty. Callers choose the scope the caller may see: an
// operator aggregate counts every obligation, while a recipient's counts
// include only obligations its destination may see (allowLocal), exactly as
// ClosureReservations lists them.
func closureCountsTx(ctx context.Context, tx pgx.Tx, repo, recipient string, allowLocal bool) (ClosureCounts, error) {
	var out ClosureCounts
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return out, err
	}
	out.RestoreGeneration = generation
	var legacyUnreserved *int64
	err = tx.QueryRow(ctx, `SELECT legacy_unreserved FROM cairn.agent_closure_reconciliation WHERE repo=$1 AND restore_generation=$2`, repo, generation).Scan(&legacyUnreserved)
	if errors.Is(err, pgx.ErrNoRows) {
		out.Reason = "unreconciled"
		return out, nil
	}
	if err != nil {
		return out, err
	}
	var reserved, delivered, legacy int64
	err = tx.QueryRow(ctx, `SELECT count(*) FILTER (WHERE r.state='reserved'), count(*) FILTER (WHERE r.state='delivered'), count(*) FILTER (WHERE r.state='reserved' AND r.legacy)
 FROM cairn.agent_closure_reservation r
 LEFT JOIN cairn.agent_event e ON e.event_id=r.request_event_id
 LEFT JOIN cairn.memory_record m ON m.record_id=e.record_id
 LEFT JOIN cairn.agent_wait w ON w.wait_uuid=r.wait_uuid
 WHERE r.repo=$1 AND ($2='' OR (r.recipient=$2 AND (`+fmt.Sprintf(closureVisible, 3, 3)+`))) AND `+closureOutstandingPredicate, repo, recipient, allowLocal).Scan(&reserved, &delivered, &legacy)
	if err != nil {
		return out, err
	}
	total := reserved + delivered
	out.Available = true
	out.Reserved, out.DeliveredOutstanding, out.Outstanding, out.Legacy = &reserved, &delivered, &total, &legacy
	if recipient == "" {
		out.LegacyUnreserved = legacyUnreserved
	}
	return out, nil
}

// reserveRequestClosuresTx reserves one reply closure per delivery of a newly
// inserted request (group members reserve group replies). The caller holds the
// collection lock; a refusal rolls back the whole publication.
func reserveRequestClosuresTx(ctx context.Context, tx pgx.Tx, eventID string) error {
	var repo, publisher string
	var grouped bool
	var n int64
	err := tx.QueryRow(ctx, `SELECT e.repo, e.publisher, EXISTS(SELECT 1 FROM cairn.agent_response_group g WHERE g.event_id=e.event_id), (SELECT count(*) FROM cairn.agent_delivery d WHERE d.event_id=e.event_id)
 FROM cairn.agent_event e WHERE e.event_id=$1 AND e.kind='request' AND e.destination_type IN ('agent','topic')`, eventID).Scan(&repo, &publisher, &grouped, &n)
	if errors.Is(err, pgx.ErrNoRows) || n == 0 {
		return nil
	}
	if err != nil {
		return err
	}
	if err = closureAdmitTx(ctx, tx, repo, publisher, n); err != nil {
		return err
	}
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return err
	}
	obligation := ClosureRequestReply
	if grouped {
		obligation = ClosureGroupReply
	}
	_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_closure_reservation(reservation_id,repo,obligation,request_event_id,request_delivery_id,expected_sender,recipient,correlation_id,restore_generation)
 SELECT gen_random_uuid(), e.repo, $2, e.event_id, d.delivery_id, d.consumer, e.publisher, e.correlation_id, $3
 FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE d.event_id=$1
 ON CONFLICT DO NOTHING`, eventID, obligation, generation)
	return err
}

// reservePoolClosureTx reserves the reply closure of an admitted pool
// request. assignPool later transfers it to the assigned delivery.
func reservePoolClosureTx(ctx context.Context, tx pgx.Tx, eventID string) error {
	var repo, publisher string
	if err := tx.QueryRow(ctx, `SELECT repo, publisher FROM cairn.agent_event WHERE event_id=$1 AND kind='request' AND destination_type='pool'`, eventID).Scan(&repo, &publisher); err != nil {
		return err
	}
	if err := closureAdmitTx(ctx, tx, repo, publisher, 1); err != nil {
		return err
	}
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_closure_reservation(reservation_id,repo,obligation,request_event_id,recipient,correlation_id,restore_generation)
 SELECT gen_random_uuid(), repo, 'pool_reply', event_id, publisher, correlation_id, $2 FROM cairn.agent_event WHERE event_id=$1
 ON CONFLICT DO NOTHING`, eventID, generation)
	return err
}

// transferPoolClosureTx binds the pool request's reservation to its assigned
// delivery without a second charge. A request admitted before reservations
// existed gets a legacy row, reported as such, rather than a capacity check.
func transferPoolClosureTx(ctx context.Context, tx pgx.Tx, eventID, deliveryID, consumer string) error {
	tag, err := tx.Exec(ctx, `UPDATE cairn.agent_closure_reservation SET request_delivery_id=$2, expected_sender=$3, revision=revision+1, transitioned_at=clock_timestamp()
 WHERE obligation='pool_reply' AND request_event_id=$1 AND state='reserved' AND request_delivery_id IS NULL`, eventID, deliveryID, consumer)
	if err != nil || tag.RowsAffected() == 1 {
		return err
	}
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_closure_reservation(reservation_id,repo,obligation,request_event_id,request_delivery_id,expected_sender,recipient,correlation_id,legacy,restore_generation)
 SELECT gen_random_uuid(), repo, 'pool_reply', event_id, $2, $3, publisher, correlation_id, true, $4 FROM cairn.agent_event WHERE event_id=$1
 ON CONFLICT DO NOTHING`, eventID, deliveryID, consumer, generation)
	return err
}

// matchReplyClosureTx resolves, before any insert, the reserved closure that
// a reply publication answers. Kind alone earns nothing: the reply must come
// from the original consumer, address the original publisher, carry the
// request correlation and arrive before any declared group or task deadline.
// Due group deadlines and declared expiries are applied first, so the first
// reply after a deadline is ordinary work. A nil result is ordinary work.
func matchReplyClosureTx(ctx context.Context, tx pgx.Tx, req PublishEventRequest, publisher string) (*validatedClosure, error) {
	if (req.Kind != "response" && req.Kind != "failure") || req.CausationID == "" || req.Destination.Type != "agent" {
		return nil, nil
	}
	if _, err := expireResponseGroups(ctx, tx, req.Repo, req.CausationID); err != nil {
		return nil, err
	}
	if _, err := releaseClosuresTx(ctx, tx, req.Repo, closureScope{EventIDs: []string{req.CausationID}}); err != nil {
		return nil, err
	}
	c := validatedClosure{Repo: req.Repo, Sender: publisher}
	err := tx.QueryRow(ctx, `SELECT r.reservation_id::text, r.obligation, r.recipient
 FROM cairn.agent_closure_reservation r
 JOIN cairn.agent_event e ON e.event_id=r.request_event_id
 LEFT JOIN cairn.agent_response_group g ON r.obligation='group_reply' AND g.event_id=r.request_event_id
 WHERE r.repo=$1 AND r.request_event_id=$2 AND r.state='reserved' AND r.obligation IN ('request_reply','group_reply','pool_reply')
   AND r.expected_sender=$3 AND r.recipient=$4 AND r.correlation_id IS NOT DISTINCT FROM NULLIF($5,'')::uuid
   AND (e.task_deadline IS NULL OR e.task_deadline>clock_timestamp())
   AND (r.obligation<>'group_reply' OR ($6='response' AND g.state='open' AND g.deadline>clock_timestamp()))
 ORDER BY r.created_at, r.reservation_id LIMIT 1 FOR UPDATE OF r`, req.Repo, req.CausationID, publisher, req.Destination.Name, req.CorrelationID, req.Kind).Scan(&c.ReservationID, &c.Obligation, &c.Recipient)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &c, nil
}

// consumeReplyClosureTx binds a matched reservation to the inserted reply
// event and its delivery to the reservation's recipient.
func consumeReplyClosureTx(ctx context.Context, tx pgx.Tx, c *validatedClosure, eventID string) error {
	if c == nil {
		return nil
	}
	tag, err := tx.Exec(ctx, `UPDATE cairn.agent_closure_reservation r SET state='delivered', consumed_event_id=e.event_id, consumed_delivery_id=d.delivery_id, revision=r.revision+1, transitioned_at=clock_timestamp()
 FROM cairn.agent_event e JOIN cairn.agent_delivery d ON d.event_id=e.event_id
 WHERE r.reservation_id=$1 AND r.state='reserved' AND e.event_id=$2 AND e.publisher=r.expected_sender AND d.consumer=r.recipient`, c.ReservationID, eventID)
	if err != nil {
		return err
	}
	if tag.RowsAffected() != 1 {
		return failure("INTEGRITY_FAILURE", "matched reply closure changed before consumption")
	}
	return nil
}

// reserveWaitClosureTx reserves the terminal notice of a new wait.
func reserveWaitClosureTx(ctx context.Context, tx pgx.Tx, repo, waitUUID, recipient string) error {
	if err := closureAdmitTx(ctx, tx, repo, recipient, 1); err != nil {
		return err
	}
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_closure_reservation(reservation_id,repo,obligation,wait_uuid,recipient,restore_generation) VALUES(gen_random_uuid(),$1,'wait_notice',$2,$3,$4) ON CONFLICT DO NOTHING`, repo, waitUUID, recipient, generation)
	return err
}

// ensureWaitClosureTx confirms that a pending wait notice holds a reserved
// closure before it is published. A replayed drain finds the closure already
// consumed by this exact notice and proceeds idempotently. A wait accepted
// before reservations existed acquires one now; a capacity refusal is
// returned so the outbox keeps the notice pending instead of publishing past
// the limit.
func ensureWaitClosureTx(ctx context.Context, tx pgx.Tx, repo, waitUUID, noticeEventID, recipient string) error {
	var state, consumed string
	err := tx.QueryRow(ctx, `SELECT state, COALESCE(consumed_event_id::text,'') FROM cairn.agent_closure_reservation WHERE wait_uuid=$1 FOR UPDATE`, waitUUID).Scan(&state, &consumed)
	if err == nil {
		if state == "reserved" || (state == "delivered" && consumed == noticeEventID) {
			return nil
		}
		return failure("INTEGRITY_FAILURE", "pending wait notice has no reserved closure ("+state+")")
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return err
	}
	if err = closureAdmitTx(ctx, tx, repo, recipient, 1); err != nil {
		return err
	}
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_closure_reservation(reservation_id,repo,obligation,wait_uuid,recipient,legacy,restore_generation) VALUES(gen_random_uuid(),$1,'wait_notice',$2,$3,true,$4)`, repo, waitUUID, recipient, generation)
	return err
}

// consumeWaitClosureTx binds the wait's reservation to its published notice.
// A replay of a notice that already consumed it changes nothing.
func consumeWaitClosureTx(ctx context.Context, tx pgx.Tx, waitUUID, noticeEventID, recipient string) error {
	tag, err := tx.Exec(ctx, `UPDATE cairn.agent_closure_reservation r SET state='delivered', consumed_event_id=d.event_id, consumed_delivery_id=d.delivery_id, revision=r.revision+1, transitioned_at=clock_timestamp()
 FROM cairn.agent_delivery d WHERE r.wait_uuid=$1 AND r.state='reserved' AND d.event_id=$2 AND d.consumer=$3`, waitUUID, noticeEventID, recipient)
	if err != nil || tag.RowsAffected() == 1 {
		return err
	}
	var replay bool
	if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_closure_reservation WHERE wait_uuid=$1 AND state='delivered' AND consumed_event_id=$2)`, waitUUID, noticeEventID).Scan(&replay); err != nil {
		return err
	}
	if !replay {
		return failure("INTEGRITY_FAILURE", "wait notice closure changed before consumption")
	}
	return nil
}

// closureReleaseReason is the single release rule. Each branch is an
// explicit terminal: a recorded stop control on the request delivery, a
// declared task deadline, a closed response group or pool request, or a
// cancelled, restricted or fenced wait. Consumer settlement is not a branch.
const closureReleaseReason = `COALESCE(
 CASE WHEN r.obligation<>'wait_notice' AND d.state='failed' AND d.control_at IS NOT NULL THEN
  CASE d.code WHEN 'operator_cancelled' THEN 'work_cancelled' WHEN 'admission_expired' THEN 'request_expired' WHEN 'task_deadline' THEN 'request_expired' WHEN 'consumer_retired' THEN 'stopped_expired' WHEN 'operator_released' THEN 'operator_released' END END,
 CASE WHEN r.obligation<>'wait_notice' AND e.task_deadline<=clock_timestamp() THEN 'request_expired' END,
 CASE WHEN r.obligation='group_reply' AND g.state<>'open' THEN CASE WHEN g.code='restore_fenced' THEN 'restore_fenced' ELSE 'group_closed' END END,
 CASE WHEN r.obligation='pool_reply' AND r.request_delivery_id IS NULL AND q.closed_at IS NOT NULL THEN
  CASE q.closed_code WHEN 'operator_cancelled' THEN 'work_cancelled' WHEN 'admission_expired' THEN 'request_expired' WHEN 'task_deadline' THEN 'request_expired' END END,
 CASE WHEN r.obligation='wait_notice' AND w.state='cancelled' THEN 'wait_cancelled'
  WHEN r.obligation='wait_notice' AND o.state='restricted' THEN 'wait_restricted'
  WHEN r.obligation='wait_notice' AND (w.state='restore_fenced' OR o.state='restore_fenced') THEN 'restore_fenced' END)`

// releaseClosuresTx applies the release rule to reserved rows in scope and
// returns how many it released.
func releaseClosuresTx(ctx context.Context, tx pgx.Tx, repo string, scope closureScope) (int64, error) {
	if !scope.All && len(scope.DeliveryIDs) == 0 && len(scope.EventIDs) == 0 && len(scope.WaitUUIDs) == 0 {
		return 0, nil
	}
	var limit *int
	if scope.Limit > 0 {
		limit = &scope.Limit
	}
	tag, err := tx.Exec(ctx, `WITH candidate AS (
 SELECT r.reservation_id, `+closureReleaseReason+` AS reason
 FROM cairn.agent_closure_reservation r
 LEFT JOIN cairn.agent_event e ON e.event_id=r.request_event_id
 LEFT JOIN cairn.agent_delivery d ON d.delivery_id=r.request_delivery_id
 LEFT JOIN cairn.agent_response_group g ON r.obligation='group_reply' AND g.event_id=r.request_event_id
 LEFT JOIN cairn.agent_pool_request q ON r.obligation='pool_reply' AND q.event_id=r.request_event_id
 LEFT JOIN cairn.agent_wait w ON w.wait_uuid=r.wait_uuid
 LEFT JOIN cairn.agent_wait_outbox o ON o.wait_uuid=r.wait_uuid
 WHERE r.state='reserved' AND ($1='' OR r.repo=$1)
   AND ($2 OR r.request_delivery_id=ANY($3::uuid[]) OR r.request_event_id=ANY($4::uuid[]) OR r.wait_uuid=ANY($5::uuid[]))
   AND `+closureReleaseReason+` IS NOT NULL
 ORDER BY r.reservation_id LIMIT $6 FOR UPDATE OF r)
 UPDATE cairn.agent_closure_reservation r SET state='released', release_reason=c.reason, released_by=current_setting('cairn.caller'), revision=r.revision+1, transitioned_at=clock_timestamp()
 FROM candidate c WHERE c.reservation_id=r.reservation_id`,
		repo, scope.All, nonNilIDs(scope.DeliveryIDs), nonNilIDs(scope.EventIDs), nonNilIDs(scope.WaitUUIDs), limit)
	return tag.RowsAffected(), err
}

func nonNilIDs(ids []string) []string {
	if ids == nil {
		return []string{}
	}
	return ids
}

// closureTablesPresentTx guards the restore fence, which can run against a
// restored database that predates the closure schema.
func closureTablesPresentTx(ctx context.Context, tx pgx.Tx) (bool, error) {
	var present bool
	err := tx.QueryRow(ctx, `SELECT to_regclass('cairn.agent_closure_reservation') IS NOT NULL`).Scan(&present)
	return present, err
}

// closureVisible restricts reservation metadata to what the caller's
// destination may see: the request event and its source record, or the
// wait's ceiling. Source privacy holds even after the source is forgotten.
const closureVisible = `CASE WHEN r.obligation='wait_notice' THEN (w.sensitivity_ceiling='shareable' OR $%d)
 ELSE (e.sensitivity='shareable' AND COALESCE(m.sensitivity,'local')='shareable') OR $%d END`

// closureConsumedVisible redacts a consumed reply or notice whose exact
// source version is deleted or forgotten, or that is more private than the
// caller may see, as the response-group view does. The obligation's own
// metadata and delivered state stay visible.
const closureConsumedVisible = `(ce.event_id IS NOT NULL AND cv.payload_deleted_by IS NULL AND cm.lifecycle='active' AND ((ce.sensitivity='shareable' AND cm.sensitivity='shareable') OR $%d))`

const closureSelect = `SELECT r.reservation_id::text, r.repo, r.obligation, COALESCE(r.request_event_id::text,''), COALESCE(r.request_delivery_id::text,''), COALESCE(r.wait_uuid::text,''),
 COALESCE(r.expected_sender,''), r.recipient, COALESCE(r.correlation_id::text,''), r.state, r.revision,
 CASE WHEN %[2]s THEN COALESCE(r.consumed_event_id::text,'') ELSE '' END,
 CASE WHEN %[2]s THEN COALESCE(r.consumed_delivery_id::text,'') ELSE '' END,
 r.state='delivered' AND NOT %[2]s,
 r.release_reason, r.released_by, r.legacy, r.restore_generation, r.created_at, r.transitioned_at
 FROM cairn.agent_closure_reservation r
 LEFT JOIN cairn.agent_event e ON e.event_id=r.request_event_id
 LEFT JOIN cairn.memory_record m ON m.record_id=e.record_id
 LEFT JOIN cairn.agent_wait w ON w.wait_uuid=r.wait_uuid
 LEFT JOIN cairn.agent_event ce ON ce.event_id=r.consumed_event_id
 LEFT JOIN cairn.record_version cv ON cv.record_id=ce.record_id AND cv.version=ce.version
 LEFT JOIN cairn.memory_record cm ON cm.record_id=ce.record_id
 WHERE %[1]s`

func closureSelectSQL(where string, allowLocalParam int) string {
	visible := fmt.Sprintf(closureVisible, allowLocalParam, allowLocalParam)
	consumed := fmt.Sprintf(closureConsumedVisible, allowLocalParam)
	return fmt.Sprintf(closureSelect, "("+visible+") AND "+where, consumed)
}

func scanClosure(row pgx.Row) (ClosureReservation, error) {
	var c ClosureReservation
	err := row.Scan(&c.ReservationID, &c.Repo, &c.Obligation, &c.RequestEventID, &c.RequestDeliveryID, &c.WaitUUID,
		&c.ExpectedSender, &c.Recipient, &c.CorrelationID, &c.State, &c.Revision,
		&c.ConsumedEventID, &c.ConsumedDeliveryID, &c.ConsumedRedacted,
		&c.ReleaseReason, &c.ReleasedBy, &c.Legacy, &c.RestoreGeneration, &c.CreatedAt, &c.TransitionedAt)
	return c, err
}

// ClosureReservations lists reservations. An operator sees the collection;
// a session sees only reservations whose recipient is its own inbox, and its
// counts cover only that inbox.
func (s *Store) ClosureReservations(ctx context.Context, req ClosureReservationQuery, dest Destination) (ClosureReservationPage, error) {
	out := ClosureReservationPage{Reservations: []ClosureReservation{}}
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return out, err
	}
	switch req.State {
	case "", "reserved", "delivered", "released":
	default:
		return out, failure("INVALID_REQUEST", "unknown closure reservation state")
	}
	switch req.Obligation {
	case "", ClosureRequestReply, ClosureGroupReply, ClosurePoolReply, ClosureWaitNotice:
	default:
		return out, failure("INVALID_REQUEST", "unknown closure obligation")
	}
	if req.After != "" && validID(req.After) != nil {
		return out, failure("INVALID_REQUEST", "invalid reservation cursor")
	}
	limit, err := eventLimit(req.Limit)
	if err != nil {
		return out, err
	}
	recipient := s.channel.Principal
	out.Scope = "inbox"
	if s.channel.Operator {
		recipient, out.Scope = "", "collection"
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	if out.Counts, err = closureCountsTx(ctx, tx, repo, recipient, dest.AllowLocal); err != nil {
		return out, err
	}
	rows, err := tx.Query(ctx, closureSelectSQL(`r.repo=$1 AND ($2='' OR r.recipient=$2) AND ($3='' OR r.state=$3) AND ($4='' OR r.obligation=$4)
 AND ($5='' OR (r.created_at, r.reservation_id) > (SELECT a.created_at, a.reservation_id FROM cairn.agent_closure_reservation a WHERE a.reservation_id=NULLIF($5,'')::uuid))
 ORDER BY r.created_at, r.reservation_id LIMIT $6`, 7), repo, recipient, req.State, req.Obligation, req.After, limit+1, dest.AllowLocal)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		if len(out.Reservations) == limit {
			out.More = true
			break
		}
		c, err := scanClosure(rows)
		if err != nil {
			return out, err
		}
		out.Reservations = append(out.Reservations, c)
		out.NextAfter = c.ReservationID
	}
	if err = rows.Err(); err != nil {
		return out, err
	}
	return out, tx.Commit(ctx)
}

// CloseReplyObligations releases reply reservations the publisher no longer
// needs, for requests that have no declared deadline. The caller is the
// request publisher (the reservation recipient) or an operator. Every item
// must still be reserved at its expected revision; the request is
// all-or-nothing and a cached retry re-checks visibility.
func (s *Store) CloseReplyObligations(ctx context.Context, req CloseReplyObligationsRequest, dest Destination) (CloseReplyObligationsResult, error) {
	var zero CloseReplyObligationsResult
	if err := validEventDestination(dest); err != nil {
		return zero, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return zero, err
	}
	req.Repo = repo
	if err = reasonValid(req.Reason); err != nil {
		return zero, err
	}
	if len(req.Items) == 0 || len(req.Items) > 100 {
		return zero, failure("INVALID_REQUEST", "close between 1 and 100 reply obligations")
	}
	seen := map[string]bool{}
	ids := make([]string, 0, len(req.Items))
	for _, item := range req.Items {
		if validID(item.ReservationID) != nil || item.ExpectedRevision < 1 {
			return zero, failure("INVALID_REQUEST", "each item needs a reservation UUID and positive expected revision")
		}
		if seen[item.ReservationID] {
			return zero, failure("INVALID_REQUEST", "reservation UUIDs must be distinct")
		}
		seen[item.ReservationID] = true
		ids = append(ids, item.ReservationID)
	}
	reason := "publisher_closed"
	if s.channel.Operator {
		reason = "operator_closed"
	}
	visibleOwned := func(tx pgx.Tx, lock bool) ([]ClosureReservation, error) {
		suffix := ` ORDER BY r.reservation_id`
		if lock {
			suffix += ` FOR UPDATE OF r`
		}
		rows, err := tx.Query(ctx, closureSelectSQL(`r.repo=$1 AND r.reservation_id=ANY($2::uuid[]) AND ($3 OR r.recipient=$4)`+suffix, 5), repo, ids, s.channel.Operator, s.channel.Principal, dest.AllowLocal)
		if err != nil {
			return nil, err
		}
		defer rows.Close()
		var list []ClosureReservation
		for rows.Next() {
			c, err := scanClosure(rows)
			if err != nil {
				return nil, err
			}
			list = append(list, c)
		}
		if err = rows.Err(); err != nil {
			return nil, err
		}
		if len(list) != len(ids) {
			return nil, failure("NOT_FOUND", "reply obligation not found")
		}
		return list, nil
	}
	return mutate(ctx, s, "closure-close-replies", req.RequestID, req, func(tx pgx.Tx) (CloseReplyObligationsResult, error) {
		if _, err := retrievalGeneration(ctx, tx); err != nil {
			return zero, err
		}
		if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
			return zero, err
		}
		list, err := visibleOwned(tx, true)
		if err != nil {
			return zero, err
		}
		expected := map[string]int{}
		for _, item := range req.Items {
			expected[item.ReservationID] = item.ExpectedRevision
		}
		for _, c := range list {
			if c.Obligation == ClosureWaitNotice {
				return zero, failure("INVALID_REQUEST", "wait notices close through wait cancellation")
			}
			if c.State != "reserved" || c.Revision != expected[c.ReservationID] {
				return zero, failure("VERSION_CONFLICT", "reply obligation changed; inspect it again")
			}
		}
		// An open response group and a waiting request_response wait still
		// expect this reply; they end through their deadline, closure or
		// CancelWait, not through an explicit close.
		var open bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_closure_reservation r JOIN cairn.agent_response_group g ON g.event_id=r.request_event_id WHERE r.reservation_id=ANY($1::uuid[]) AND g.state='open')
 OR EXISTS(SELECT 1 FROM cairn.agent_closure_reservation r JOIN cairn.agent_wait w ON w.dependency_event_uuid=r.request_event_id WHERE r.reservation_id=ANY($1::uuid[]) AND w.state='waiting' AND w.predicate_kind='request_response')`, ids).Scan(&open); err != nil {
			return zero, err
		}
		if open {
			return zero, failure("OPEN_CONFLICT", "an open response group or waiting request_response wait still expects this reply; close the group by its deadline or cancel the wait first")
		}
		if _, err = tx.Exec(ctx, `UPDATE cairn.agent_closure_reservation SET state='released', release_reason=$2, released_by=current_setting('cairn.caller'), revision=revision+1, transitioned_at=clock_timestamp() WHERE reservation_id=ANY($1::uuid[])`, ids, reason); err != nil {
			return zero, err
		}
		list, err = visibleOwned(tx, false)
		return CloseReplyObligationsResult{Reservations: list}, err
	}, func(tx pgx.Tx) error {
		_, err := visibleOwned(tx, false)
		return err
	})
}

// closureReplyMatch matches a retained reply event e2 to reservation r by
// exact identity: causation, sender, destination and correlation.
const closureReplyMatch = `e2.causation_id=r.request_event_id AND e2.publisher=r.expected_sender AND e2.destination_type='agent' AND e2.destination_name=r.recipient
 AND e2.correlation_id IS NOT DISTINCT FROM r.correlation_id
 AND ((r.obligation='group_reply' AND e2.kind='response') OR (r.obligation<>'group_reply' AND e2.kind IN ('response','failure')))`

// ReconcileClosureReservations runs one bounded batch for a collection under
// the collection insertion lock: it applies the release rule, reserves
// still-open legacy obligations and adopts replies or notices published
// without consumption, each step changing at most Limit rows. Current writers
// already ledger new obligations and older schemas cannot write, so the
// legacy candidate set only shrinks and the lock can be released between
// batches. Only a batch in which every step stays under its limit is
// complete; it alone records completion for the restore generation.
// Requests their consumer settled before reservations existed may still
// expect a reply; their intent is unknown, so the complete batch counts them
// (a read-only aggregate taken before the lock) instead of reserving them.
func (s *Store) ReconcileClosureReservations(ctx context.Context, req ClosureReconcileRequest) (ClosureReconciliation, error) {
	var zero ClosureReconciliation
	if err := s.requireOperator(); err != nil {
		return zero, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return zero, err
	}
	req.Repo = repo
	if req.Limit == 0 {
		req.Limit = closureReconcileDefaultLimit
	}
	if req.Limit < 1 || req.Limit > closureReconcileMaxLimit {
		return zero, failure("INVALID_REQUEST", fmt.Sprintf("limit must be between 1 and %d", closureReconcileMaxLimit))
	}
	limit := int64(req.Limit)
	return mutate(ctx, s, "closure-reconcile", req.RequestID, req, func(tx pgx.Tx) (ClosureReconciliation, error) {
		out := ClosureReconciliation{Repo: repo}
		generation, err := retrievalGeneration(ctx, tx)
		if err != nil {
			return zero, err
		}
		out.RestoreGeneration = generation
		var unreserved int64
		// Grouped requests are excluded: an open group's members are reserved
		// below and a closed group is already a terminal for its replies.
		if err = tx.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)
 WHERE e.repo=$1 AND e.kind='request' AND d.state IN ('handled','ignored','failed') AND d.control_at IS NULL
   AND (e.task_deadline IS NULL OR e.task_deadline>clock_timestamp())
   AND NOT EXISTS(SELECT 1 FROM cairn.agent_response_group g WHERE g.event_id=e.event_id)
   AND NOT EXISTS(SELECT 1 FROM cairn.agent_closure_reservation x WHERE x.request_delivery_id=d.delivery_id)
   AND NOT EXISTS(SELECT 1 FROM cairn.agent_event e2 WHERE e2.causation_id=e.event_id AND e2.publisher=d.consumer AND e2.kind IN ('response','failure') AND e2.destination_type='agent' AND e2.destination_name=e.publisher)`, repo).Scan(&unreserved); err != nil {
			return zero, err
		}
		if err = lock(ctx, tx, "agent-events:"+repo); err != nil {
			return zero, err
		}
		exhausted := true
		step := func(n int64) int64 {
			if n >= limit {
				exhausted = false
			}
			return n
		}
		released, err := releaseClosuresTx(ctx, tx, repo, closureScope{All: true, Limit: req.Limit})
		if err != nil {
			return zero, err
		}
		out.Released = step(released)
		inserts := []string{
			// Open ungrouped direct/topic request deliveries.
			`INSERT INTO cairn.agent_closure_reservation(reservation_id,repo,obligation,request_event_id,request_delivery_id,expected_sender,recipient,correlation_id,legacy,restore_generation)
 SELECT gen_random_uuid(), e.repo, 'request_reply', e.event_id, d.delivery_id, d.consumer, e.publisher, e.correlation_id, true, $2
 FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)
 WHERE e.repo=$1 AND e.kind='request' AND e.destination_type IN ('agent','topic') AND d.state IN ('pending','leased')
   AND (e.task_deadline IS NULL OR e.task_deadline>clock_timestamp())
   AND NOT EXISTS(SELECT 1 FROM cairn.agent_response_group g WHERE g.event_id=e.event_id)
   AND NOT EXISTS(SELECT 1 FROM cairn.agent_closure_reservation x WHERE x.request_delivery_id=d.delivery_id)
 ORDER BY d.delivery_id LIMIT $3
 ON CONFLICT DO NOTHING`,
			// Open response-group members without a response.
			`INSERT INTO cairn.agent_closure_reservation(reservation_id,repo,obligation,request_event_id,request_delivery_id,expected_sender,recipient,correlation_id,legacy,restore_generation)
 SELECT gen_random_uuid(), e.repo, 'group_reply', e.event_id, mb.delivery_id, mb.consumer, e.publisher, e.correlation_id, true, $2
 FROM cairn.agent_response_group g JOIN cairn.agent_event e USING(event_id) JOIN cairn.agent_response_group_member mb ON mb.event_id=g.event_id
 WHERE e.repo=$1 AND g.state='open' AND g.deadline>clock_timestamp() AND mb.response_event_id IS NULL
   AND NOT EXISTS(SELECT 1 FROM cairn.agent_closure_reservation x WHERE x.request_delivery_id=mb.delivery_id)
 ORDER BY mb.delivery_id LIMIT $3
 ON CONFLICT DO NOTHING`,
			// Open pool requests, assigned or not.
			`INSERT INTO cairn.agent_closure_reservation(reservation_id,repo,obligation,request_event_id,request_delivery_id,expected_sender,recipient,correlation_id,legacy,restore_generation)
 SELECT gen_random_uuid(), e.repo, 'pool_reply', e.event_id, q.delivery_id, d.consumer, e.publisher, e.correlation_id, true, $2
 FROM cairn.agent_pool_request q JOIN cairn.agent_event e USING(event_id) LEFT JOIN cairn.agent_delivery d ON d.delivery_id=q.delivery_id
 WHERE e.repo=$1 AND (e.task_deadline IS NULL OR e.task_deadline>clock_timestamp())
   AND ((q.delivery_id IS NULL AND q.closed_at IS NULL) OR d.state IN ('pending','leased'))
   AND NOT EXISTS(SELECT 1 FROM cairn.agent_closure_reservation x WHERE x.obligation='pool_reply' AND x.request_event_id=e.event_id)
 ORDER BY e.event_id LIMIT $3
 ON CONFLICT DO NOTHING`,
			// Waits that still owe a terminal notice.
			`INSERT INTO cairn.agent_closure_reservation(reservation_id,repo,obligation,wait_uuid,recipient,legacy,restore_generation)
 SELECT gen_random_uuid(), w.collection, 'wait_notice', w.wait_uuid, 'agent/'||w.owner_agent_uuid::text, true, $2
 FROM cairn.agent_wait w LEFT JOIN cairn.agent_wait_outbox o USING(wait_uuid)
 WHERE w.collection=$1 AND (w.state='waiting' OR o.state='pending')
   AND NOT EXISTS(SELECT 1 FROM cairn.agent_closure_reservation x WHERE x.wait_uuid=w.wait_uuid)
 ORDER BY w.wait_uuid LIMIT $3
 ON CONFLICT DO NOTHING`,
		}
		for _, statement := range inserts {
			tag, err := tx.Exec(ctx, statement, repo, generation, limit)
			if err != nil {
				return zero, err
			}
			out.LegacyReserved += step(tag.RowsAffected())
		}
		// Replies published by an older writer, or before this pass reserved
		// them, consume their reservation by exact identity.
		tag, err := tx.Exec(ctx, `WITH adopt AS (
 SELECT DISTINCT ON (r.reservation_id) r.reservation_id, e2.event_id, d2.delivery_id
 FROM cairn.agent_closure_reservation r
 JOIN cairn.agent_event e2 ON `+closureReplyMatch+`
 JOIN cairn.agent_delivery d2 ON d2.event_id=e2.event_id AND d2.consumer=r.recipient
 WHERE r.repo=$1 AND r.state='reserved' AND r.obligation<>'wait_notice'
   AND NOT EXISTS(SELECT 1 FROM cairn.agent_closure_reservation x WHERE x.consumed_event_id=e2.event_id)
 ORDER BY r.reservation_id, e2.position LIMIT $2)
 UPDATE cairn.agent_closure_reservation r SET state='delivered', consumed_event_id=a.event_id, consumed_delivery_id=a.delivery_id, revision=r.revision+1, transitioned_at=clock_timestamp()
 FROM adopt a WHERE a.reservation_id=r.reservation_id`, repo, limit)
		if err != nil {
			return zero, err
		}
		out.Adopted = step(tag.RowsAffected())
		tag, err = tx.Exec(ctx, `WITH adopt AS (
 SELECT r.reservation_id, d.event_id, d.delivery_id
 FROM cairn.agent_closure_reservation r JOIN cairn.agent_wait_outbox o ON o.wait_uuid=r.wait_uuid JOIN cairn.agent_delivery d ON d.event_id=o.notice_event_uuid AND d.consumer=r.recipient
 WHERE r.repo=$1 AND r.state='reserved' AND r.obligation='wait_notice' AND o.state='published'
 ORDER BY r.reservation_id LIMIT $2)
 UPDATE cairn.agent_closure_reservation r SET state='delivered', consumed_event_id=a.event_id, consumed_delivery_id=a.delivery_id, revision=r.revision+1, transitioned_at=clock_timestamp()
 FROM adopt a WHERE a.reservation_id=r.reservation_id`, repo, limit)
		if err != nil {
			return zero, err
		}
		out.Adopted += step(tag.RowsAffected())
		if !exhausted {
			out.More = true
			return out, nil
		}
		var legacyRows int64
		if err = tx.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_closure_reservation WHERE repo=$1 AND legacy`, repo).Scan(&legacyRows); err != nil {
			return zero, err
		}
		var at time.Time
		if err = tx.QueryRow(ctx, `INSERT INTO cairn.agent_closure_reconciliation(repo,restore_generation,legacy_rows,legacy_unreserved) VALUES($1,$2,$3,$4)
 ON CONFLICT (repo,restore_generation) DO UPDATE SET reconciled_at=clock_timestamp(), reconciled_by=current_setting('cairn.caller'), legacy_rows=EXCLUDED.legacy_rows, legacy_unreserved=EXCLUDED.legacy_unreserved
 RETURNING reconciled_at`, repo, generation, legacyRows, unreserved).Scan(&at); err != nil {
			return zero, err
		}
		out.Complete, out.LegacyRows, out.LegacyUnreserved, out.ReconciledAt = true, &legacyRows, &unreserved, &at
		return out, nil
	})
}
