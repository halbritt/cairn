package core

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

// Delivery and native-attempt transitions.
//
// One owner for the SET clauses and hold predicates that the agent fabric's
// Store methods used to re-spell per file: lease, renew, release, fail,
// settle, and native attempt open/finish. Every helper takes the caller's
// pgx.Tx and never locks: Store methods keep the lock order (request key,
// then attempt key, then record row), their admission checks, reads and
// responses. This is an in-process module, not a repository interface;
// PostgreSQL remains the one store.

// nativeLeaseTTL is the lease a native session claim, a notice-batch advance
// or a wake claim holds before its host must renew, settle or reconcile it.
// Publisher-side polls (NextEvent) choose their own lease per request through
// leaseSeconds; the two lengths are deliberately distinct.
const nativeLeaseTTL = 90 * time.Second

// Hold fragments. The per-delivery forms reference the delivery as `d` and
// compose into eligibility queries; the per-session forms take the agent id
// as $1 and its inbox principal as $2.
const (
	// wakeHold: no unfinished wake attempt holds delivery d.
	wakeHold = `NOT EXISTS(SELECT 1 FROM cairn.agent_wake_attempt w WHERE w.delivery_id=d.delivery_id AND w.finished_at IS NULL)`
	// sessionInboxHold: no unfinished native attempt holds delivery d.
	sessionInboxHold = `NOT EXISTS(SELECT 1 FROM cairn.agent_session_attempt n WHERE n.delivery_id=d.delivery_id AND n.finished_at IS NULL)`

	unfinishedNativeByAgent = `EXISTS(SELECT 1 FROM cairn.agent_session_attempt WHERE agent_id=$1 AND finished_at IS NULL)`
	unfinishedWakeByAgent   = `EXISTS(SELECT 1 FROM cairn.agent_wake_attempt WHERE agent_id=$1 AND finished_at IS NULL)`
	unfinishedWakeByInbox   = `EXISTS(SELECT 1 FROM cairn.agent_wake_attempt WHERE (consumer=$2 OR agent_id=$1) AND finished_at IS NULL)`
	liveLeaseByInbox        = `EXISTS(SELECT 1 FROM cairn.agent_delivery WHERE consumer=$2 AND state='leased' AND lease_until>clock_timestamp())`

	// sessionInboxOccupied: the session or its inbox already holds native
	// work, an unfinished wake, or a live lease, so a new claim must wait.
	sessionInboxOccupied = `SELECT ` + unfinishedNativeByAgent + ` OR ` + unfinishedWakeByInbox + ` OR ` + liveLeaseByInbox
	// sessionWorkOpen: the session itself owns unfinished work, as a wake
	// attachment checks before binding a fresh-worker session.
	sessionWorkOpen = `SELECT ` + unfinishedWakeByAgent + ` OR ` + unfinishedNativeByAgent + ` OR ` + liveLeaseByInbox
)

// deliveryHeld reports the unfinished wake and native attempts on a delivery.
func deliveryHeld(ctx context.Context, tx pgx.Tx, deliveryID string) (wake, native bool, err error) {
	err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_wake_attempt WHERE delivery_id=$1 AND finished_at IS NULL),EXISTS(SELECT 1 FROM cairn.agent_session_attempt WHERE delivery_id=$1 AND finished_at IS NULL)`, deliveryID).Scan(&wake, &native)
	return wake, native, err
}

// inboxOccupied reports whether a registered session or its inbox already
// holds native work, an unfinished wake, or a live lease.
func inboxOccupied(ctx context.Context, tx pgx.Tx, agentID, inbox string) (bool, error) {
	var held bool
	err := tx.QueryRow(ctx, sessionInboxOccupied, agentID, inbox).Scan(&held)
	return held, err
}

// sessionBusy reports whether a session owns unfinished wake or native work
// or its inbox carries a live lease.
func sessionBusy(ctx context.Context, tx pgx.Tx, agentID, inbox string) (bool, error) {
	var busy bool
	err := tx.QueryRow(ctx, sessionWorkOpen, agentID, inbox).Scan(&busy)
	return busy, err
}

// principalWorkOpen reports whether a profile principal's own session or wake
// attempts are unfinished, which excludes it from publisher-side polling.
func principalWorkOpen(ctx context.Context, tx pgx.Tx, principal string) (bool, error) {
	var busy bool
	err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_session_attempt n JOIN cairn.agent_session a USING(agent_id) WHERE 'agent/'||a.agent_id::text=$1 AND n.finished_at IS NULL) OR EXISTS(SELECT 1 FROM cairn.agent_wake_attempt WHERE 'agent/'||agent_id::text=$1 AND finished_at IS NULL)`, principal).Scan(&busy)
	return busy, err
}

// wakeOpen reports whether a supervisor consumer already has an unfinished
// wake attempt in the repository.
func wakeOpen(ctx context.Context, tx pgx.Tx, repo, consumer string) (bool, error) {
	var busy bool
	err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_wake_attempt WHERE repo=$1 AND consumer=$2 AND finished_at IS NULL)`, repo, consumer).Scan(&busy)
	return busy, err
}

// leaseDelivery claims a pending or lapsed delivery under a fresh lease.
func leaseDelivery(ctx context.Context, tx pgx.Tx, deliveryID, leaseID string, ttl time.Duration) error {
	_, err := tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='leased',lease_id=$2,lease_until=clock_timestamp()+make_interval(secs=>$3),attempts=attempts+1 WHERE delivery_id=$1`, deliveryID, leaseID, ttl.Seconds())
	return err
}

// renewDelivery extends a live lease to at least ttl from now.
func renewDelivery(ctx context.Context, tx pgx.Tx, deliveryID string, ttl time.Duration) error {
	_, err := tx.Exec(ctx, `UPDATE cairn.agent_delivery SET lease_until=GREATEST(lease_until,clock_timestamp()+make_interval(secs=>$2)) WHERE delivery_id=$1`, deliveryID, ttl.Seconds())
	return err
}

// releaseDelivery returns a delivery to pending; a positive backoff defers
// its availability.
func releaseDelivery(ctx context.Context, tx pgx.Tx, deliveryID string, backoff time.Duration) error {
	if backoff > 0 {
		_, err := tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='pending',lease_id=NULL,lease_until=NULL,available_at=clock_timestamp()+make_interval(secs=>$2) WHERE delivery_id=$1`, deliveryID, backoff.Seconds())
		return err
	}
	_, err := tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='pending',lease_id=NULL,lease_until=NULL WHERE delivery_id=$1`, deliveryID)
	return err
}

// releaseLeasedDeliveries releases every lease, as a restore fence does:
// leases are capabilities from the pre-restore world and need a new claim.
func releaseLeasedDeliveries(ctx context.Context, tx pgx.Tx) error {
	_, err := tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='pending',lease_id=NULL,lease_until=NULL WHERE state='leased'`)
	return err
}

// stopControl names the stop decision recorded with a failure. An empty
// By records the caller identity stamped on the transaction.
type stopControl struct {
	By     string
	Reason string
}

// failDelivery fails a live (pending or leased) delivery with code. A nil
// control leaves the control columns untouched; otherwise the decision is
// recorded as a stop control. It reports whether a row changed, so callers
// that must observe a race can refuse instead of assuming the effect.
func failDelivery(ctx context.Context, tx pgx.Tx, deliveryID, code string, control *stopControl) (bool, error) {
	if control == nil {
		tag, err := tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='failed',lease_id=NULL,lease_until=NULL,completed_at=clock_timestamp(),code=$2 WHERE delivery_id=$1 AND state IN ('pending','leased')`, deliveryID, code)
		return tag.RowsAffected() == 1, err
	}
	tag, err := tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='failed',lease_id=NULL,lease_until=NULL,completed_at=clock_timestamp(),code=$2,control_at=clock_timestamp(),control_by=COALESCE(NULLIF($3,''),current_setting('cairn.caller')),control_reason=$4 WHERE delivery_id=$1 AND state IN ('pending','leased')`, deliveryID, code, control.By, control.Reason)
	return tag.RowsAffected() == 1, err
}

// failPendingDeliveries fails only the still-pending deliveries among ids as a
// recorded stop control and returns how many changed, so a reviewed set that
// moved since review is detected rather than partially applied.
func failPendingDeliveries(ctx context.Context, tx pgx.Tx, ids []string, code string, control stopControl) (int64, error) {
	tag, err := tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='failed',lease_id=NULL,lease_until=NULL,completed_at=clock_timestamp(),code=$2,control_at=clock_timestamp(),control_by=COALESCE(NULLIF($3,''),current_setting('cairn.caller')),control_reason=$4 WHERE delivery_id=ANY($1::uuid[]) AND state='pending'`, ids, code, control.By, control.Reason)
	return tag.RowsAffected(), err
}

// settleDelivery records the consumer's disposition with an optional result
// reference; late marks completion accepted after the lease lapsed.
func settleDelivery(ctx context.Context, tx pgx.Tx, deliveryID, disposition, code string, resultID *string, resultVersion *int, late bool) error {
	_, err := tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state=$2,lease_id=NULL,lease_until=NULL,completed_at=clock_timestamp(),code=$3,result_id=$4,result_version=$5,completed_after_lease=$6 WHERE delivery_id=$1`, deliveryID, disposition, code, resultID, resultVersion, late)
	return err
}

// nativeAttemptOpen describes a fresh native attempt: the lease it binds to a
// session execution and turn, and its position in a notice batch when the
// hold is being transferred from a settled attempt.
type nativeAttemptOpen struct {
	AttemptID, AgentID, ExecutionID, Repo string
	DeliveryID, LeaseID, NativeTurnID     string
	TurnExclusive                         bool
	Batch                                 *nativeBatchPosition
}

// nativeBatchPosition places an attempt in a bounded notice batch.
type nativeBatchPosition struct {
	Root  string
	Index int
	Limit int
}

// openAttempt inserts the native attempt row. The schema refuses a second
// exclusive attempt for one native turn (isTurnAlreadyBound).
func openAttempt(ctx context.Context, tx pgx.Tx, a nativeAttemptOpen) error {
	if a.Batch == nil {
		_, err := tx.Exec(ctx, `INSERT INTO cairn.agent_session_attempt(attempt_id,agent_id,execution_id,repo,delivery_id,lease_id,native_turn_id,turn_exclusive) VALUES($1,$2,$3,$4,$5,$6,$7,$8)`, a.AttemptID, a.AgentID, a.ExecutionID, a.Repo, a.DeliveryID, a.LeaseID, a.NativeTurnID, a.TurnExclusive)
		return err
	}
	_, err := tx.Exec(ctx, `INSERT INTO cairn.agent_session_attempt(attempt_id,agent_id,execution_id,repo,delivery_id,lease_id,native_turn_id,turn_exclusive,batch_root,batch_index,batch_limit) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)`, a.AttemptID, a.AgentID, a.ExecutionID, a.Repo, a.DeliveryID, a.LeaseID, a.NativeTurnID, a.TurnExclusive, a.Batch.Root, a.Batch.Index, a.Batch.Limit)
	return err
}

// attemptClose carries the facts some closures record beside the reason.
type attemptClose struct {
	CancelConfirmed bool   // confirmed operator cancellation cleanup
	ReleasedBy      string // operator who released an unreturned host's hold
	ReleaseReason   string
}

// finishAttempt closes a native attempt with its reason and returns the
// recorded close time.
func finishAttempt(ctx context.Context, tx pgx.Tx, attemptID, reason string, extras attemptClose) (time.Time, error) {
	var finished time.Time
	err := tx.QueryRow(ctx, `UPDATE cairn.agent_session_attempt SET finished_at=clock_timestamp(),reason=$2,cancel_confirmed_at=CASE WHEN $3 THEN clock_timestamp() ELSE cancel_confirmed_at END,released_by=COALESCE(NULLIF($4,''),released_by),release_reason=COALESCE(NULLIF($5,''),release_reason) WHERE attempt_id=$1 RETURNING finished_at`, attemptID, reason, extras.CancelConfirmed, extras.ReleasedBy, extras.ReleaseReason).Scan(&finished)
	return finished, err
}
