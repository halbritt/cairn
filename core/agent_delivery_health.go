package core

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
)

// Host observations are selected diagnostics reported under the existing
// profile/session boundary. A model sharing that profile credential could write
// one; the store therefore labels them host_reported and never treats them as
// attestation, policy, a delivery guarantee or recovery authorization.

// observationTTL bounds how long a host observation can describe current state,
// checked against both the store receipt and the host's own observation time.
// Hosts refresh a persisting condition at least every 60 seconds.
const observationTTL = 120 * time.Second

// observationSkew is the largest host clock lead accepted as ordinary jitter.
const observationSkew = 5 * time.Second

// observationScopes is the closed condition vocabulary. The store, not the
// host, decides whether a condition describes one delivery or the session.
var observationScopes = map[string]string{
	"wake_retained":                "delivery",
	"native_admission_retained":    "delivery",
	"automatic_available":          "delivery",
	"channel_not_launched":         "session",
	"channel_not_selected":         "session",
	"channel_unconfigured":         "session",
	"native_transport_unavailable": "session",
	"terminal_host_unavailable":    "session",
	"terminal_target_ambiguous":    "session",
	"owner_turn_required":          "session",
	"busy":                         "session",
	"unknown":                      "session",
	"none":                         "session",
}

// Session-level conditions that need no delivery. Every other condition names
// the exact ready delivery the host observed.
var observationWithoutDelivery = map[string]bool{"owner_turn_required": true, "busy": true, "unknown": true, "none": true}

var wakeTransports = map[string]bool{"claude-channel": true, "codex-queue": true, "opencode-queue": true, "hermes-queue": true, "terminal": true}
var wakeStatuses = map[string]bool{"uncertain": true, "submitted": true, "queued": true, "refused": true}

// SessionDeliveryObservation is one current host observation for a session.
type SessionDeliveryObservation struct {
	Session    AgentSessionRef `json:"session"`
	DeliveryID string          `json:"delivery_id,omitempty"`
	Condition  string          `json:"condition"`
	Wake       *ObservedWake   `json:"wake,omitempty"`
	ObservedAt time.Time       `json:"observed_at"`
}

// ObservedWake is the host's retained wake marker, as testimony.
type ObservedWake struct {
	Transport   string     `json:"transport"`
	Status      string     `json:"status"`
	AttemptedAt *time.Time `json:"attempted_at,omitempty"`
}

// StoredDeliveryObservation is the current row with store-owned fields.
type StoredDeliveryObservation struct {
	Session    AgentSessionRef `json:"session"`
	DeliveryID string          `json:"delivery_id,omitempty"`
	Condition  string          `json:"condition"`
	Scope      string          `json:"scope"`
	Wake       *ObservedWake   `json:"wake,omitempty"`
	ObservedAt time.Time       `json:"observed_at"`
	ReceivedAt time.Time       `json:"received_at"`
	Source     string          `json:"source"`
}

// SessionDeliveryObservationResult says whether this call changed current
// state. An older or duplicate observation is not applied and returns the row
// that remains current.
type SessionDeliveryObservationResult struct {
	Applied     bool                       `json:"applied"`
	Observation *StoredDeliveryObservation `json:"observation"`
}

func (o SessionDeliveryObservation) validate() error {
	if err := o.Session.Validate(); err != nil {
		return err
	}
	if _, ok := observationScopes[o.Condition]; !ok {
		return failure("INVALID_REQUEST", "unknown delivery observation condition")
	}
	if o.DeliveryID == "" && !observationWithoutDelivery[o.Condition] {
		return failure("INVALID_REQUEST", "this condition requires the observed delivery_id")
	}
	if o.DeliveryID != "" && validID(o.DeliveryID) != nil {
		return failure("INVALID_REQUEST", "delivery_id must be a UUID")
	}
	if o.ObservedAt.IsZero() {
		return failure("INVALID_REQUEST", "observed_at required")
	}
	if o.Wake != nil {
		if o.Condition != "wake_retained" {
			return failure("INVALID_REQUEST", "wake detail is accepted only for wake_retained")
		}
		if !wakeTransports[o.Wake.Transport] || !wakeStatuses[o.Wake.Status] {
			return failure("INVALID_REQUEST", "unknown wake transport or status")
		}
		if o.Wake.AttemptedAt != nil && o.Wake.AttemptedAt.After(o.ObservedAt) {
			return failure("INVALID_REQUEST", "wake attempted_at follows observed_at")
		}
	}
	return nil
}

const observationColumns = `o.agent_id::text,o.execution_id::text,COALESCE(o.delivery_id::text,''),o.condition,o.scope,o.wake_transport,o.wake_status,o.wake_attempted_at,o.observed_at,o.received_at`

func scanObservation(row pgx.Row) (StoredDeliveryObservation, error) {
	var o StoredDeliveryObservation
	var transport, status *string
	var attempted *time.Time
	err := row.Scan(&o.Session.AgentID, &o.Session.ExecutionID, &o.DeliveryID, &o.Condition, &o.Scope, &transport, &status, &attempted, &o.ObservedAt, &o.ReceivedAt)
	if transport != nil && status != nil {
		o.Wake = &ObservedWake{Transport: *transport, Status: *status, AttemptedAt: attempted}
	}
	o.Source = "host_reported"
	return o, err
}

func sameObservation(a StoredDeliveryObservation, b SessionDeliveryObservation) bool {
	if a.DeliveryID != b.DeliveryID || a.Condition != b.Condition || (a.Wake == nil) != (b.Wake == nil) {
		return false
	}
	if a.Wake == nil {
		return true
	}
	if a.Wake.Transport != b.Wake.Transport || a.Wake.Status != b.Wake.Status || (a.Wake.AttemptedAt == nil) != (b.Wake.AttemptedAt == nil) {
		return false
	}
	return a.Wake.AttemptedAt == nil || a.Wake.AttemptedAt.Equal(*b.Wake.AttemptedAt)
}

// ObserveSessionDelivery replaces the session's current host observation. It
// uses the heartbeat authorization: the caller's profile must own the session
// at its current execution and database generation. It keeps no per-call
// receipt; the row is bounded current state. A delayed older observation, or a
// repeat of the stored one, changes nothing and cannot refresh freshness.
func (s *Store) ObserveSessionDelivery(ctx context.Context, req SessionDeliveryObservation, dest Destination) (SessionDeliveryObservationResult, error) {
	var out SessionDeliveryObservationResult
	if err := s.nativeInboxProfile(req.Session, dest); err != nil {
		return out, err
	}
	if err := req.validate(); err != nil {
		return out, err
	}
	// PostgreSQL retains microseconds. Compare the same representation that
	// will round-trip through storage so a nanosecond retry cannot renew age.
	req.ObservedAt = req.ObservedAt.Truncate(time.Microsecond)
	if req.Wake != nil && req.Wake.AttemptedAt != nil {
		wake := *req.Wake
		attempted := wake.AttemptedAt.Truncate(time.Microsecond)
		wake.AttemptedAt = &attempted
		req.Wake = &wake
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	a, err := currentAgentSession(ctx, tx, req.Session, s.channel.Principal, s.channel.Repo, true, dest)
	if err != nil {
		return out, err
	}
	if err = activeAgent(a); err != nil {
		return out, err
	}
	var now time.Time
	if err = tx.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&now); err != nil {
		return out, err
	}
	if req.ObservedAt.After(now.Add(observationSkew)) {
		return out, failure("INVALID_REQUEST", "observed_at is ahead of the store clock")
	}
	if req.DeliveryID != "" {
		var found bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE d.delivery_id=$1 AND d.consumer=$2 AND e.repo=$3 AND (e.sensitivity='shareable' OR $4))`, req.DeliveryID, a.Inbox, a.Repo, dest.AllowLocal).Scan(&found); err != nil {
			return out, err
		}
		if !found {
			return out, failure("NOT_FOUND", "delivery is not in this session's inbox")
		}
	}
	// Ordering applies only within the current execution and generation; a
	// replaced session's row is simply superseded.
	current, err := scanObservation(tx.QueryRow(ctx, `SELECT `+observationColumns+` FROM cairn.agent_session_delivery_observation o WHERE o.agent_id=$1 FOR UPDATE`, a.AgentID))
	var generation int64
	if err == nil {
		err = tx.QueryRow(ctx, `SELECT database_generation FROM cairn.agent_session_delivery_observation WHERE agent_id=$1`, a.AgentID).Scan(&generation)
	}
	switch {
	case errors.Is(err, pgx.ErrNoRows):
	case err != nil:
		return out, err
	case current.Session.ExecutionID == a.ExecutionID && generation == a.DatabaseGeneration:
		if req.ObservedAt.Before(current.ObservedAt) {
			out.Observation = &current
			return out, tx.Commit(ctx)
		}
		if req.ObservedAt.Equal(current.ObservedAt) {
			if !sameObservation(current, req) {
				return out, failure("VERSION_CONFLICT", "a different observation already has this observed_at")
			}
			out.Observation = &current
			return out, tx.Commit(ctx)
		}
	}
	if now.Sub(req.ObservedAt) > observationTTL {
		// Too old to describe current state; it neither creates nor replaces a row.
		if err == nil {
			out.Observation = &current
		}
		return out, tx.Commit(ctx)
	}
	var transport, status *string
	var attempted *time.Time
	if req.Wake != nil {
		transport, status, attempted = &req.Wake.Transport, &req.Wake.Status, req.Wake.AttemptedAt
	}
	stored, err := scanObservation(tx.QueryRow(ctx, `INSERT INTO cairn.agent_session_delivery_observation AS o(agent_id,execution_id,database_generation,reporter,delivery_id,condition,scope,wake_transport,wake_status,wake_attempted_at,observed_at,received_at)
 VALUES($1,$2,$3,$4,NULLIF($5,'')::uuid,$6,$7,$8,$9,$10,$11,$12)
 ON CONFLICT (agent_id) DO UPDATE SET execution_id=EXCLUDED.execution_id,database_generation=EXCLUDED.database_generation,reporter=EXCLUDED.reporter,delivery_id=EXCLUDED.delivery_id,condition=EXCLUDED.condition,scope=EXCLUDED.scope,wake_transport=EXCLUDED.wake_transport,wake_status=EXCLUDED.wake_status,wake_attempted_at=EXCLUDED.wake_attempted_at,observed_at=EXCLUDED.observed_at,received_at=EXCLUDED.received_at
 RETURNING `+observationColumns, a.AgentID, a.ExecutionID, a.DatabaseGeneration, s.channel.Principal, req.DeliveryID, req.Condition, observationScopes[req.Condition], transport, status, attempted, req.ObservedAt, now))
	if err != nil {
		return out, err
	}
	out.Applied, out.Observation = true, &stored
	return out, tx.Commit(ctx)
}

// DeliveryDiagnosis explains a delivery from store facts first. A host
// observation is attached only while the delivery waits before any claim, and
// only with its applicability and freshness. It is not a delivery guarantee:
// no progress claim, completion, reply or task acceptance is inferred.
type DeliveryDiagnosis struct {
	// Stage comes from the delivery row and holds: waiting, scheduled, leased,
	// held, handled, failed or ignored. Busy or scheduled waiting is intentional.
	Stage          string             `json:"stage"`
	WaitingSeconds *int64             `json:"waiting_seconds,omitempty"`
	Recipient      DiagnosisRecipient `json:"recipient"`
	Host           *DiagnosisHost     `json:"host,omitempty"`
	ComputedAt     time.Time          `json:"computed_at"`
}

// DiagnosisRecipient describes the consumer. Kind "other" covers profile,
// pool, topic and local inboxes, or sessions not visible to this destination.
type DiagnosisRecipient struct {
	Kind        string `json:"kind"`
	AgentID     string `json:"agent_id,omitempty"`
	ExecutionID string `json:"execution_id,omitempty"`
	Presence    string `json:"presence,omitempty"`
	State       string `json:"state,omitempty"`
}

// DiagnosisHost is the applicable host observation, labelled host_reported.
// Condition names a cause only for a current observation that applies to this
// delivery (exact) or its session; otherwise it is "unknown" and Reported keeps
// the host's code as context. Unknown is never healthy. Freshness is current,
// stale, replaced, offline, absent, superseded (the delivery was claimed after
// the observation) or withheld (the observation concerns work this reader's
// destination cannot see).
type DiagnosisHost struct {
	Source     string        `json:"source"`
	Condition  string        `json:"condition"`
	Reported   string        `json:"reported,omitempty"`
	Applies    string        `json:"applies"`
	Freshness  string        `json:"freshness"`
	DeliveryID string        `json:"delivery_id,omitempty"`
	Wake       *ObservedWake `json:"wake,omitempty"`
	ObservedAt *time.Time    `json:"observed_at,omitempty"`
	ReceivedAt *time.Time    `json:"received_at,omitempty"`
}

// progress_at bounds the latest claim: a lease grant sets lease_until after
// its claim time, and native or wake attempts record when they ran. A claimed
// delivery that later waits again inherits no pre-claim host observation.
const diagnosisQuery = `SELECT d.delivery_id::text,d.state,d.lease_until,d.available_at,clock_timestamp(),d.attempts,
 GREATEST(d.lease_until,
  (SELECT max(GREATEST(n.created_at,n.finished_at)) FROM cairn.agent_session_attempt n WHERE n.delivery_id=d.delivery_id),
  (SELECT max(GREATEST(w.created_at,w.finished_at)) FROM cairn.agent_wake_attempt w WHERE w.delivery_id=d.delivery_id)),
 COALESCE(oe.sensitivity='shareable',true),
 NOT (` + wakeHold + `) OR NOT (` + sessionInboxHold + `),
 (SELECT generation FROM cairn.retrieval_generation WHERE singleton),
 s.agent_id::text,s.execution_id::text,s.database_generation,s.stopped,s.expires_at,s.metadata->>'state',
 o.execution_id::text,o.database_generation,COALESCE(o.delivery_id::text,''),o.condition,o.scope,o.wake_transport,o.wake_status,o.wake_attempted_at,o.observed_at,o.received_at
 FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)
 LEFT JOIN cairn.agent_session s ON d.consumer='agent/'||s.agent_id::text AND s.repo=e.repo AND (s.visibility='hosted' OR $2)
 LEFT JOIN cairn.agent_session_delivery_observation o ON o.agent_id=s.agent_id
 LEFT JOIN cairn.agent_delivery od ON od.delivery_id=o.delivery_id
 LEFT JOIN cairn.agent_event oe ON oe.event_id=od.event_id
 WHERE d.delivery_id=ANY($1::uuid[])`

// deliveryDiagnoses reads diagnoses for already-authorized delivery IDs in the
// caller's transaction. allowLocal widens session visibility exactly as the
// caller's destination does.
func deliveryDiagnoses(ctx context.Context, tx pgx.Tx, ids []string, allowLocal bool) (map[string]*DeliveryDiagnosis, error) {
	out := map[string]*DeliveryDiagnosis{}
	if len(ids) == 0 {
		return out, nil
	}
	rows, err := tx.Query(ctx, diagnosisQuery, ids, allowLocal)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var id, state string
		var leaseUntil *time.Time
		var available, now time.Time
		var held, observedShareable bool
		var attempts int
		var progress *time.Time
		var generation int64
		var agentID, execution, sessionState, obsExecution, obsDelivery, condition, scope, transport, status *string
		var sessionGeneration, obsGeneration *int64
		var stopped *bool
		var expires, attempted, observed, received *time.Time
		if err = rows.Scan(&id, &state, &leaseUntil, &available, &now, &attempts, &progress, &observedShareable, &held, &generation,
			&agentID, &execution, &sessionGeneration, &stopped, &expires, &sessionState,
			&obsExecution, &obsGeneration, &obsDelivery, &condition, &scope, &transport, &status, &attempted, &observed, &received); err != nil {
			return nil, err
		}
		d := &DeliveryDiagnosis{ComputedAt: now, Recipient: DiagnosisRecipient{Kind: "other"}}
		switch {
		case state == "handled" || state == "failed" || state == "ignored":
			d.Stage = state
		case held:
			d.Stage = "held"
		case state == "leased" && leaseUntil != nil && leaseUntil.After(now):
			d.Stage = "leased"
		case available.After(now):
			d.Stage = "scheduled"
		default:
			d.Stage = "waiting"
			waited := int64(now.Sub(available) / time.Second)
			d.WaitingSeconds = &waited
		}
		if agentID != nil {
			d.Recipient = DiagnosisRecipient{Kind: "session", AgentID: *agentID, ExecutionID: *execution, State: deref(sessionState)}
			switch {
			case *stopped:
				d.Recipient.Presence = "stopped"
			case *sessionGeneration != generation:
				d.Recipient.Presence = "replaced"
			case !expires.After(now):
				d.Recipient.Presence = "offline"
			default:
				d.Recipient.Presence = "online"
			}
		}
		if d.Stage == "waiting" && agentID != nil {
			switch {
			case condition != nil && !allowLocal && !observedShareable:
				// The observed delivery is local-only: reveal neither it nor its cause.
				d.Host = &DiagnosisHost{Source: "host_reported", Condition: "unknown", Applies: "none", Freshness: "withheld"}
			default:
				d.Host = diagnoseHost(id, now, generation, d.Recipient, obsExecution, obsGeneration, obsDelivery, condition, scope, transport, status, attempted, observed, received)
				if condition != nil && attempts > 0 && (progress == nil || !observed.After(*progress) || !received.After(*progress)) {
					// Claimed after (or at an unknown time relative to) this observation.
					d.Host.Freshness, d.Host.Applies = "superseded", "none"
					if d.Host.Condition != "unknown" {
						d.Host.Reported, d.Host.Condition = d.Host.Condition, "unknown"
					}
				}
			}
		}
		out[id] = d
	}
	return out, rows.Err()
}

func deref(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}

func diagnoseHost(delivery string, now time.Time, generation int64, recipient DiagnosisRecipient, execution *string, obsGeneration *int64, obsDelivery, condition, scope, transport, status *string, attempted, observed, received *time.Time) *DiagnosisHost {
	h := &DiagnosisHost{Source: "host_reported", Condition: "unknown", Applies: "none", Freshness: "absent"}
	if condition == nil {
		return h
	}
	h.DeliveryID, h.ObservedAt, h.ReceivedAt = deref(obsDelivery), observed, received
	if transport != nil && status != nil {
		h.Wake = &ObservedWake{Transport: *transport, Status: *status, AttemptedAt: attempted}
	}
	switch {
	case recipient.Presence == "stopped" || recipient.Presence == "offline":
		h.Freshness = "offline"
	case recipient.Presence == "replaced" || *execution != recipient.ExecutionID || *obsGeneration != generation:
		h.Freshness = "replaced"
	case now.Sub(*received) > observationTTL || now.Sub(*observed) > observationTTL:
		h.Freshness = "stale"
	default:
		h.Freshness = "current"
	}
	switch {
	case *condition == "none" || *condition == "unknown":
		if *condition == "none" {
			h.Reported = "none"
		}
		return h
	case deref(obsDelivery) == delivery:
		h.Applies = "exact"
	case *scope == "session":
		h.Applies = "session"
	default:
		h.Applies = "other_delivery"
	}
	if h.Freshness == "current" && h.Applies != "other_delivery" {
		h.Condition = *condition
	} else {
		h.Reported = *condition
	}
	return h
}
