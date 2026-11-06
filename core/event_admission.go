package core

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

// Admission limits count real deliveries. Future schedule membership is unknown.
// Rates use publications or deliveries per second, with explicitly selected bursts.
type EventAdmissionRate struct {
	PerSecond int64 `json:"per_second"`
	Burst     int64 `json:"burst"`
}
type EventAdmissionLimits struct {
	MaxRecipientClosureReserved     int64              `json:"max_recipient_closure_reserved"`
	MaxTopicRecipients              int64              `json:"max_topic_recipients"`
	MaxRecipientOutstanding         int64              `json:"max_recipient_outstanding"`
	MaxProducerRecipientOutstanding int64              `json:"max_producer_recipient_outstanding"`
	MaxCollectionOutstanding        int64              `json:"max_collection_outstanding"`
	MaxClosureReserved              int64              `json:"max_closure_reserved"`
	ProducerPublications            EventAdmissionRate `json:"producer_publications"`
	ProducerDeliveries              EventAdmissionRate `json:"producer_deliveries"`
	OwnerPublications               EventAdmissionRate `json:"owner_publications"`
	OwnerDeliveries                 EventAdmissionRate `json:"owner_deliveries"`
	CollectionPublications          EventAdmissionRate `json:"collection_publications"`
	CollectionDeliveries            EventAdmissionRate `json:"collection_deliveries"`
}
type EventAdmissionPolicy struct {
	Version  int                   `json:"version"`
	Mode     string                `json:"mode"`
	Workload string                `json:"workload"`
	Limits   *EventAdmissionLimits `json:"limits,omitempty"`
}
type EventAdmissionPolicyState struct {
	Repo             string               `json:"repo"`
	Revision         int64                `json:"revision"`
	Policy           EventAdmissionPolicy `json:"policy"`
	UpdatedBy        string               `json:"updated_by,omitempty"`
	UpdatedAt        *time.Time           `json:"updated_at,omitempty"`
	EnforcementReady bool                 `json:"enforcement_ready"`
}
type EventAdmissionPolicyQuery struct {
	Repo string `json:"repo,omitempty"`
}
type SetEventAdmissionPolicyRequest struct {
	RequestID        string               `json:"request_id"`
	Repo             string               `json:"repo,omitempty"`
	ExpectedRevision int64                `json:"expected_revision"`
	Policy           EventAdmissionPolicy `json:"policy"`
}

func (p EventAdmissionPolicy) validate() error {
	if p.Version != 1 || (p.Mode != "disabled" && p.Mode != "observe" && p.Mode != "enforce") {
		return failure("INVALID_REQUEST", "admission policy requires version 1 and disabled, observe or enforce mode")
	}
	if len(p.Workload) > 2000 {
		return failure("INVALID_REQUEST", "workload must be at most 2000 bytes")
	}
	if p.Limits == nil {
		if p.Mode == "disabled" {
			return nil
		}
		return failure("INVALID_REQUEST", "enabled policy requires explicit finite limits and a declared workload")
	}
	if strings.TrimSpace(p.Workload) == "" {
		return failure("INVALID_REQUEST", "explicit limits require a declared workload")
	}
	l := p.Limits
	for name, value := range map[string]int64{"max_topic_recipients": l.MaxTopicRecipients, "max_recipient_outstanding": l.MaxRecipientOutstanding, "max_producer_recipient_outstanding": l.MaxProducerRecipientOutstanding, "max_collection_outstanding": l.MaxCollectionOutstanding, "max_closure_reserved": l.MaxClosureReserved, "max_recipient_closure_reserved": l.MaxRecipientClosureReserved} {
		upper := int64(1000000000)
		if name == "max_topic_recipients" {
			upper = 10000
		}
		if value < 1 || value > upper {
			return failure("INVALID_REQUEST", fmt.Sprintf("%s must be between 1 and %d", name, upper))
		}
	}
	for _, r := range []EventAdmissionRate{l.ProducerPublications, l.ProducerDeliveries, l.OwnerPublications, l.OwnerDeliveries, l.CollectionPublications, l.CollectionDeliveries} {
		if r.PerSecond < 1 || r.PerSecond > 1000000000 || r.Burst < 1 || r.Burst > 1000000000 {
			return failure("INVALID_REQUEST", "each rate and burst must be between 1 and 1000000000")
		}
	}
	return nil
}
func admissionPolicyTx(ctx context.Context, tx pgx.Tx, repo string) (EventAdmissionPolicyState, error) {
	out := EventAdmissionPolicyState{Repo: repo, Policy: EventAdmissionPolicy{Version: 1, Mode: "disabled"}, EnforcementReady: eventAdmissionEnforcementReady()}
	err := tx.QueryRow(ctx, `SELECT revision,policy,updated_by,updated_at FROM cairn.agent_event_admission_policy WHERE repo=$1`, repo).Scan(&out.Revision, &out.Policy, &out.UpdatedBy, &out.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		err = nil
	}
	return out, err
}
func (s *Store) EventAdmissionPolicy(ctx context.Context, req EventAdmissionPolicyQuery) (EventAdmissionPolicyState, error) {
	if err := s.requireOperator(); err != nil {
		return EventAdmissionPolicyState{}, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return EventAdmissionPolicyState{}, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return EventAdmissionPolicyState{}, err
	}
	defer tx.Rollback(ctx)
	out, err := admissionPolicyTx(ctx, tx, repo)
	if err != nil {
		return out, err
	}
	return out, tx.Commit(ctx)
}
func (s *Store) SetEventAdmissionPolicy(ctx context.Context, req SetEventAdmissionPolicyRequest) (EventAdmissionPolicyState, error) {
	if err := s.requireOperator(); err != nil {
		return EventAdmissionPolicyState{}, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return EventAdmissionPolicyState{}, err
	}
	req.Repo = repo
	if err = req.Policy.validate(); err != nil {
		return EventAdmissionPolicyState{}, err
	}
	if req.ExpectedRevision < 0 {
		return EventAdmissionPolicyState{}, failure("INVALID_REQUEST", "expected_revision must be nonnegative")
	}
	// This is a binary capability gate, not an operator-selectable bypass.
	if req.Policy.Mode == "enforce" && !eventAdmissionEnforcementReady() {
		return EventAdmissionPolicyState{}, failure("FEATURE_UNAVAILABLE", "event admission enforcement requires closure reservations and every delivery writer")
	}
	return mutate(ctx, s, "event-admission-policy", req.RequestID, req, func(tx pgx.Tx) (EventAdmissionPolicyState, error) {
		if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
			return EventAdmissionPolicyState{}, err
		}
		current, err := admissionPolicyTx(ctx, tx, repo)
		if err != nil {
			return current, err
		}
		if current.Revision != req.ExpectedRevision {
			return current, failure("VERSION_CONFLICT", "admission policy revision changed")
		}

		if req.Policy.Mode == "enforce" {
			generation, err := retrievalGeneration(ctx, tx)
			if err != nil {
				return EventAdmissionPolicyState{}, err
			}
			reconciled, err := closureReconciledTx(ctx, tx, repo, generation)
			if err != nil {
				return EventAdmissionPolicyState{}, err
			}
			if !reconciled {
				return EventAdmissionPolicyState{}, failure("CLOSURE_UNRECONCILED", "complete closure reconciliation for the current restore generation before enabling admission")
			}
		}
		_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_event_admission_policy(repo,revision,policy) VALUES($1,$2,$3) ON CONFLICT(repo) DO UPDATE SET revision=EXCLUDED.revision,policy=EXCLUDED.policy,updated_by=current_setting('cairn.caller'),updated_at=clock_timestamp()`, repo, current.Revision+1, req.Policy)
		if err != nil {
			return EventAdmissionPolicyState{}, err
		}
		return admissionPolicyTx(ctx, tx, repo)
	})
}

// PressureIdentity is stamped from the authenticated channel and registration;
// neither payload labels nor correlation UUIDs identify a producer.
type PressureIdentity struct {
	Kind      string           `json:"kind"`
	Principal string           `json:"principal"`
	Session   *AgentSessionRef `json:"session,omitempty"`
}

func (s *Store) pressureIdentity() PressureIdentity {
	out := PressureIdentity{Kind: "principal", Principal: s.channel.Principal}
	if s.session != nil {
		ref := s.session.Ref
		out.Kind = "session"
		out.Session = &ref
	}
	return out
}

type EventPressureQuery struct {
	Repo  string `json:"repo,omitempty"`
	After string `json:"after,omitempty"`
	Limit int    `json:"limit,omitempty"`
}
type EventPressureReservoirs struct {
	PendingDeliveries           int64  `json:"pending_deliveries"`
	LeasedDeliveries            int64  `json:"leased_deliveries"`
	QueuedOrganicNotices        int64  `json:"queued_organic_notices"`
	UnassignedPoolRequests      int64  `json:"unassigned_pool_requests"`
	PendingSchedules            int64  `json:"pending_schedules"`
	WaitingTerminalNotices      int64  `json:"waiting_terminal_notices"`
	PendingWaitOutbox           int64  `json:"pending_wait_outbox"`
	StoppedDeliveries           int64  `json:"stopped_deliveries"`
	ClosureReserved             *int64 `json:"closure_reserved"`
	ClosureDeliveredOutstanding *int64 `json:"closure_delivered_outstanding"`
	NormalOutstanding           *int64 `json:"normal_outstanding"`
	ClosureState                string `json:"closure_state"`
}
type EventPressureRecipient struct {
	Identity            PressureIdentity `json:"identity"`
	Pending             int64            `json:"pending"`
	Leased              int64            `json:"leased"`
	OldestSeconds       float64          `json:"oldest_seconds"`
	ActiveSubscriptions int64            `json:"active_subscriptions"`
	Presence            string           `json:"presence"`
}
type EventPressure struct {
	Schema                   string                    `json:"schema"`
	Scope                    string                    `json:"scope"`
	Identity                 *PressureIdentity         `json:"identity,omitempty"`
	ComputedAt               time.Time                 `json:"computed_at"`
	Policy                   EventAdmissionPolicyState `json:"policy"`
	Reservoirs               EventPressureReservoirs   `json:"reservoirs"`
	Closure                  ClosureCounts             `json:"closure"`
	PublishedLastMinute      int64                     `json:"published_last_minute"`
	DeliveriesLastMinute     int64                     `json:"delivery_fanout_last_minute_publications"`
	RateWindowSeconds        int                       `json:"rate_window_seconds"`
	RateEvaluation           string                    `json:"rate_evaluation"`
	OldestOutstandingSeconds float64                   `json:"oldest_outstanding_seconds"`
	Recipients               []EventPressureRecipient  `json:"recipients"`
	NextAfter                string                    `json:"next_after,omitempty"`
	More                     bool                      `json:"more"`
}

func (s *Store) EventPressure(ctx context.Context, req EventPressureQuery, dest Destination) (EventPressure, error) {
	return s.eventPressure(ctx, req, dest, false)
}
func (s *Store) CollectionEventPressure(ctx context.Context, req EventPressureQuery) (EventPressure, error) {
	if err := s.requireOperator(); err != nil {
		return EventPressure{}, err
	}
	return s.eventPressure(ctx, req, Destination{Name: "local", AllowLocal: true}, true)
}
func (s *Store) eventPressure(ctx context.Context, req EventPressureQuery, dest Destination, collection bool) (EventPressure, error) {
	out := EventPressure{Schema: "cairn.event-pressure/1", Scope: "inbox", Recipients: []EventPressureRecipient{}, RateWindowSeconds: 60, RateEvaluation: "unavailable", Reservoirs: EventPressureReservoirs{ClosureState: "unsupported"}}
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return out, err
	}
	limit, err := eventLimit(req.Limit)
	if err != nil {
		return out, err
	}
	if len(req.After) > 512 {
		return out, failure("INVALID_REQUEST", "after exceeds 512 bytes")
	}
	if collection {
		out.Scope = "collection"
	} else {
		id := s.pressureIdentity()
		out.Identity = &id
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	if out.Policy, err = admissionPolicyTx(ctx, tx, repo); err != nil {
		return out, err
	}
	if !collection {
		out.Policy.UpdatedBy = ""
		out.Policy.Policy.Workload = ""
	}
	if err = tx.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&out.ComputedAt); err != nil {
		return out, err
	}
	principal := s.channel.Principal
	// Independent reservoirs are deliberately separate: schedules and pools do
	// not yet know their final recipient snapshot; wait/outbox states are disjoint.
	err = tx.QueryRow(ctx, `SELECT
 (SELECT count(*) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.state='pending' AND ($2 OR d.consumer=$3) AND (e.sensitivity='shareable' OR $4)),
 (SELECT count(*) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.state='leased' AND ($2 OR d.consumer=$3) AND (e.sensitivity='shareable' OR $4)),
 (SELECT count(*) FROM cairn.agent_organic_notice n JOIN cairn.agent_event e USING(event_id) WHERE n.repo=$1 AND n.state='queued' AND ($2 OR n.recipient_inbox=$3) AND (e.sensitivity='shareable' OR $4)),
 (SELECT count(*) FROM cairn.agent_pool_request p JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND p.delivery_id IS NULL AND p.closed_at IS NULL AND ($2 OR e.publisher=$3) AND (e.sensitivity='shareable' OR $4)),
 (SELECT count(*) FROM cairn.agent_schedule WHERE repo=$1 AND state='pending' AND ($2 OR scheduled_by=$3)),
 (SELECT count(*) FROM cairn.agent_wait WHERE collection=$1 AND state='waiting' AND ($2 OR owner_principal=$3) AND (sensitivity_ceiling='shareable' OR $4)),
 (SELECT count(*) FROM cairn.agent_wait_outbox o JOIN cairn.agent_wait w USING(wait_uuid) WHERE w.collection=$1 AND o.state='pending' AND ($2 OR w.owner_principal=$3) AND (w.sensitivity_ceiling='shareable' OR $4)),
 (SELECT count(*) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) JOIN cairn.agent_session a ON d.consumer='agent/'||a.agent_id::text AND a.repo=e.repo WHERE e.repo=$1 AND a.stopped AND d.state IN ('pending','leased') AND ($2 OR d.consumer=$3) AND (e.sensitivity='shareable' OR $4)),
 (SELECT count(*) FROM cairn.agent_event e WHERE e.repo=$1 AND e.created_at>$5::timestamptz-interval '60 seconds' AND ($2 OR e.publisher=$3) AND (e.sensitivity='shareable' OR $4)),
 (SELECT count(*) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND e.created_at>$5::timestamptz-interval '60 seconds' AND ($2 OR e.publisher=$3) AND (e.sensitivity='shareable' OR $4)),
 COALESCE((SELECT greatest(0,extract(epoch FROM $5::timestamptz-min(e.created_at))) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.state IN ('pending','leased') AND ($2 OR d.consumer=$3) AND (e.sensitivity='shareable' OR $4)),0)`, repo, collection, principal, dest.AllowLocal, out.ComputedAt).Scan(&out.Reservoirs.PendingDeliveries, &out.Reservoirs.LeasedDeliveries, &out.Reservoirs.QueuedOrganicNotices, &out.Reservoirs.UnassignedPoolRequests, &out.Reservoirs.PendingSchedules, &out.Reservoirs.WaitingTerminalNotices, &out.Reservoirs.PendingWaitOutbox, &out.Reservoirs.StoppedDeliveries, &out.PublishedLastMinute, &out.DeliveriesLastMinute, &out.OldestOutstandingSeconds)
	if err != nil {
		return out, err
	}
	closureRecipient := ""
	if !collection {
		closureRecipient = principal
	}
	out.Closure, err = closureCountsTx(ctx, tx, repo, closureRecipient, dest.AllowLocal)
	if err != nil {
		return out, err
	}
	out.Reservoirs.ClosureState = out.Closure.Reason
	if out.Closure.Available {
		out.Reservoirs.ClosureState = "available"
		out.Reservoirs.ClosureReserved = out.Closure.Reserved
		out.Reservoirs.ClosureDeliveredOutstanding = out.Closure.DeliveredOutstanding
		var normal int64
		err = tx.QueryRow(ctx, `SELECT
   (SELECT count(*) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.state IN ('pending','leased') AND ($2 OR d.consumer=$3) AND (e.sensitivity='shareable' OR $4) AND NOT EXISTS(SELECT 1 FROM cairn.agent_closure_reservation r WHERE r.consumed_delivery_id=d.delivery_id AND r.state='delivered')) +
   (SELECT count(*) FROM cairn.agent_organic_notice n JOIN cairn.agent_event e USING(event_id) WHERE n.repo=$1 AND n.state='queued' AND ($2 OR n.recipient_inbox=$3) AND (e.sensitivity='shareable' OR $4)) +
   (SELECT count(*) FROM cairn.agent_pool_request p JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND p.delivery_id IS NULL AND p.closed_at IS NULL AND ($2 OR e.publisher=$3) AND (e.sensitivity='shareable' OR $4))`, repo, collection, principal, dest.AllowLocal).Scan(&normal)
		if err != nil {
			return out, err
		}
		out.Reservoirs.NormalOutstanding = &normal
	}
	rows, err := tx.Query(ctx, `WITH consumers AS (
 SELECT d.consumer FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.state IN ('pending','leased') AND (e.sensitivity='shareable' OR $4)
 UNION SELECT consumer FROM cairn.agent_subscription WHERE repo=$1 AND active
 ) SELECT c.consumer,
 (SELECT count(*) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.consumer=c.consumer AND d.state='pending' AND (e.sensitivity='shareable' OR $4)),
 (SELECT count(*) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.consumer=c.consumer AND d.state='leased' AND (e.sensitivity='shareable' OR $4)),
 COALESCE((SELECT greatest(0,extract(epoch FROM $5::timestamptz-min(e.created_at))) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.consumer=c.consumer AND d.state IN ('pending','leased') AND (e.sensitivity='shareable' OR $4)),0),
 (SELECT count(*) FROM cairn.agent_subscription s WHERE s.repo=$1 AND s.consumer=c.consumer AND s.active),
 a.agent_id::text,a.execution_id::text,CASE WHEN a.agent_id IS NULL THEN 'unknown' WHEN a.stopped THEN 'stopped' WHEN a.database_generation<>(SELECT generation FROM cairn.retrieval_generation WHERE singleton) THEN 'replaced' WHEN a.expires_at<=$5 THEN 'offline' ELSE 'online' END
 FROM consumers c LEFT JOIN cairn.agent_session a ON c.consumer='agent/'||a.agent_id::text AND a.repo=$1 AND (a.visibility='hosted' OR $4)
 WHERE ($2 OR c.consumer=$3) AND c.consumer>$6 ORDER BY c.consumer LIMIT $7`, repo, collection, principal, dest.AllowLocal, out.ComputedAt, req.After, limit+1)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		var r EventPressureRecipient
		var agent, execution *string
		r.Identity.Kind = "principal"
		if err = rows.Scan(&r.Identity.Principal, &r.Pending, &r.Leased, &r.OldestSeconds, &r.ActiveSubscriptions, &agent, &execution, &r.Presence); err != nil {
			return out, err
		}
		if agent != nil {
			r.Identity.Kind = "session"
			r.Identity.Session = &AgentSessionRef{AgentID: *agent, ExecutionID: *execution}
		}
		out.Recipients = append(out.Recipients, r)
	}
	if err = rows.Err(); err != nil {
		return out, err
	}
	rows.Close()
	if len(out.Recipients) > limit {
		out.More = true
		out.Recipients = out.Recipients[:limit]
	}
	if len(out.Recipients) > 0 {
		out.NextAfter = out.Recipients[len(out.Recipients)-1].Identity.Principal
	}
	return out, tx.Commit(ctx)
}

// A preview is an observation, not a reservation or promise of acceptance.
// Other recipient identities and private queue counts never leave this method.
type EventAdmissionPreviewRequest struct {
	Repo        string           `json:"repo,omitempty"`
	Destination EventDestination `json:"destination"`
}
type EventAdmissionPreview struct {
	Schema              string           `json:"schema"`
	PolicyRevision      int64            `json:"policy_revision"`
	Mode                string           `json:"mode"`
	Producer            PressureIdentity `json:"producer"`
	RecipientCount      *int64           `json:"recipient_count"`
	RecipientCountExact bool             `json:"recipient_count_exact"`
	RecipientCountState string           `json:"recipient_count_state"`
	WouldRefuse         []string         `json:"would_refuse"`
	Unsupported         []string         `json:"unsupported"`
	Unevaluated         []string         `json:"unevaluated"`
	EnforcementReady    bool             `json:"enforcement_ready"`
}

func (s *Store) PreviewEventAdmission(ctx context.Context, req EventAdmissionPreviewRequest, dest Destination) (EventAdmissionPreview, error) {
	out := EventAdmissionPreview{Schema: "cairn.event-admission-preview/1", Producer: s.pressureIdentity(), RecipientCountState: "unavailable", WouldRefuse: []string{}, Unsupported: []string{"rate_buckets"}, Unevaluated: []string{"source_authorization", "validated_closure_eligibility"}, EnforcementReady: eventAdmissionEnforcementReady()}
	if !eventAdmissionEnforcementReady() {
		out.Unsupported = append(out.Unsupported, "enforcement_not_ready")
	}
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return out, err
	}
	if (req.Destination.Type != "agent" && req.Destination.Type != "topic") || strings.TrimSpace(req.Destination.Name) == "" || len(req.Destination.Name) > 256 || (req.Destination.Type == "topic" && !eventName.MatchString(req.Destination.Name)) {
		return out, failure("INVALID_REQUEST", "preview requires an exact agent or topic destination")
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	policy, err := admissionPolicyTx(ctx, tx, repo)
	if err != nil {
		return out, err
	}
	out.Mode = policy.Policy.Mode
	out.PolicyRevision = policy.Revision
	if policy.Policy.Limits == nil || out.Mode == "disabled" {
		out.Unsupported = append(out.Unsupported, "disabled_policy")
		return out, tx.Commit(ctx)
	}
	limits := policy.Policy.Limits
	// Only a bounded recipient snapshot is inspected. Stopped sessions stay in
	// the snapshot; expiry of presence never silently unsubscribes a recipient.
	recipients := []string{req.Destination.Name}
	if req.Destination.Type == "topic" {
		recipients = []string{}
		rows, err := tx.Query(ctx, `SELECT consumer FROM cairn.agent_subscription WHERE repo=$1 AND topic=$2 AND active ORDER BY consumer LIMIT $3`, repo, req.Destination.Name, limits.MaxTopicRecipients+1)
		if err != nil {
			return out, err
		}
		for rows.Next() {
			var consumer string
			if err = rows.Scan(&consumer); err != nil {
				rows.Close()
				return out, err
			}
			recipients = append(recipients, consumer)
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return out, err
		}
	}
	recipientCount := int64(len(recipients))
	snapshotExact := recipientCount <= limits.MaxTopicRecipients || req.Destination.Type == "agent"
	out.RecipientCountState = "withheld"
	if s.channel.Operator && dest.AllowLocal {
		out.RecipientCount = &recipientCount
		out.RecipientCountExact = snapshotExact
		out.RecipientCountState = "exact"
		if !snapshotExact {
			out.RecipientCountState = "lower_bound"
		}
	}
	if !snapshotExact {
		out.WouldRefuse = append(out.WouldRefuse, "topic_recipients")
	}
	// These counts are used internally only; a publisher may not inspect a
	// different recipient's queue or infer hidden principals from a refusal.
	var collection, recipientMax, pairMax int64
	err = tx.QueryRow(ctx, `WITH outstanding AS (
 SELECT d.consumer,e.publisher FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.state IN ('pending','leased') AND NOT EXISTS(SELECT 1 FROM cairn.agent_closure_reservation r WHERE r.consumed_delivery_id=d.delivery_id AND r.state='delivered')
 UNION ALL SELECT n.recipient_inbox,n.publisher_principal FROM cairn.agent_organic_notice n WHERE n.repo=$1 AND n.state='queued'
 UNION ALL SELECT NULL::text,e.publisher FROM cairn.agent_pool_request p JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND p.delivery_id IS NULL AND p.closed_at IS NULL
 ) SELECT (SELECT count(*) FROM outstanding),
 COALESCE((SELECT max(n) FROM (SELECT count(*) n FROM outstanding WHERE consumer=ANY($2::text[]) GROUP BY consumer) q),0),
 COALESCE((SELECT max(n) FROM (SELECT count(*) n FROM outstanding WHERE consumer=ANY($2::text[]) AND publisher=$3 GROUP BY consumer) q),0)`, repo, recipients, s.channel.Principal).Scan(&collection, &recipientMax, &pairMax)
	if err != nil {
		return out, err
	}
	if recipientCount > 0 && collection+recipientCount > limits.MaxCollectionOutstanding {
		out.WouldRefuse = append(out.WouldRefuse, "collection_outstanding")
	}
	if len(recipients) > 0 && recipientMax+1 > limits.MaxRecipientOutstanding {
		out.WouldRefuse = append(out.WouldRefuse, "recipient_outstanding")
	}
	if len(recipients) > 0 && pairMax+1 > limits.MaxProducerRecipientOutstanding {
		out.WouldRefuse = append(out.WouldRefuse, "producer_recipient_outstanding")
	}
	return out, tx.Commit(ctx)
}

// Event fanout is the retained snapshot, not current topic membership.
// Only the publisher or an operator can read a complete fanout count.
type EventFanoutQuery struct {
	Repo  string `json:"repo,omitempty"`
	After int64  `json:"after,omitempty"`
	Limit int    `json:"limit,omitempty"`
}
type EventFanout struct {
	RecipientCount         *int64    `json:"recipient_count"`
	EventID                string    `json:"event_id"`
	Position               int64     `json:"position"`
	CreatedAt              time.Time `json:"created_at"`
	DestinationType        string    `json:"destination_type"`
	Deliveries             int64     `json:"deliveries"`
	QueuedOrganicNotices   int64     `json:"queued_organic_notices"`
	Pending                int64     `json:"pending"`
	Leased                 int64     `json:"leased"`
	RecipientSnapshotState string    `json:"recipient_snapshot_state"`
}
type EventFanoutPage struct {
	Events    []EventFanout `json:"events"`
	NextAfter int64         `json:"next_after"`
	More      bool          `json:"more"`
}

func (s *Store) EventFanout(ctx context.Context, req EventFanoutQuery, dest Destination) (EventFanoutPage, error) {
	return s.eventFanout(ctx, req, dest, false)
}
func (s *Store) CollectionEventFanout(ctx context.Context, req EventFanoutQuery) (EventFanoutPage, error) {
	if err := s.requireOperator(); err != nil {
		return EventFanoutPage{}, err
	}
	return s.eventFanout(ctx, req, Destination{Name: "local", AllowLocal: true}, true)
}
func (s *Store) eventFanout(ctx context.Context, req EventFanoutQuery, dest Destination, collection bool) (EventFanoutPage, error) {
	out := EventFanoutPage{Events: []EventFanout{}, NextAfter: req.After}
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return out, err
	}
	limit, err := eventLimit(req.Limit)
	if err != nil {
		return out, err
	}
	if req.After < 0 {
		return out, failure("INVALID_REQUEST", "after must be nonnegative")
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT e.event_id::text,e.position,e.created_at,e.destination_type,
 (SELECT count(*) FROM cairn.agent_delivery d WHERE d.event_id=e.event_id),
 (SELECT count(*) FROM cairn.agent_organic_notice n WHERE n.event_id=e.event_id AND n.state='queued'),
 (SELECT count(*) FROM cairn.agent_delivery d WHERE d.event_id=e.event_id AND d.state='pending'),
 (SELECT count(*) FROM cairn.agent_delivery d WHERE d.event_id=e.event_id AND d.state='leased'),
 CASE WHEN e.destination_type='pool' AND EXISTS(SELECT 1 FROM cairn.agent_pool_request p WHERE p.event_id=e.event_id AND p.delivery_id IS NULL AND p.closed_at IS NULL) THEN 'awaiting_assignment' ELSE 'snapshotted' END,
 CASE WHEN e.destination_type='pool' AND EXISTS(SELECT 1 FROM cairn.agent_pool_request p WHERE p.event_id=e.event_id AND p.delivery_id IS NULL AND p.closed_at IS NULL) THEN NULL ELSE
 (SELECT count(*) FROM cairn.agent_delivery d WHERE d.event_id=e.event_id) +
 (SELECT count(*) FROM cairn.agent_organic_notice n WHERE n.event_id=e.event_id AND NOT EXISTS(SELECT 1 FROM cairn.agent_delivery d WHERE d.delivery_id=n.reserved_delivery_id)) END
 FROM cairn.agent_event e WHERE e.repo=$1 AND ($2 OR e.publisher=$3) AND (e.sensitivity='shareable' OR $4) AND e.position>$5 ORDER BY e.position LIMIT $6`, repo, collection, s.channel.Principal, dest.AllowLocal, req.After, limit+1)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		var e EventFanout
		if err = rows.Scan(&e.EventID, &e.Position, &e.CreatedAt, &e.DestinationType, &e.Deliveries, &e.QueuedOrganicNotices, &e.Pending, &e.Leased, &e.RecipientSnapshotState, &e.RecipientCount); err != nil {
			return out, err
		}
		out.Events = append(out.Events, e)
	}
	if err = rows.Err(); err != nil {
		return out, err
	}
	rows.Close()
	if len(out.Events) > limit {
		out.More = true
		out.Events = out.Events[:limit]
	}
	if len(out.Events) > 0 {
		out.NextAfter = out.Events[len(out.Events)-1].Position
	}
	return out, tx.Commit(ctx)
}
