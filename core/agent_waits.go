package core

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type AgentWait struct {
	WaitUUID                 string            `json:"wait_uuid"`
	Collection               string            `json:"collection"`
	OwnerPrincipal           string            `json:"owner_principal"`
	OwnerAgentUUID           string            `json:"owner_agent_uuid"`
	CreationExecutionUUID    string            `json:"creation_execution_uuid"`
	RegistrationDestination  Destination       `json:"registration_destination"`
	SensitivityCeiling       string            `json:"sensitivity_ceiling"`
	ProfileBindingID         string            `json:"profile_binding_id"`
	PredicateVersion         int               `json:"predicate_version"`
	PredicateKind            string            `json:"predicate_kind"`
	DependencyEventUUID      string            `json:"dependency_event_uuid"`
	Deadline                 time.Time         `json:"deadline"`
	Generation               int64             `json:"generation"`
	State                    string            `json:"state"`
	ResponseRef              *RecordVersionRef `json:"response_ref,omitempty"`
	ReservedNoticeEventUUID  string            `json:"reserved_notice_event_uuid"`
	ReservedSourceRecordUUID string            `json:"reserved_source_record_uuid"`
	CreatedAt                time.Time         `json:"created_at"`
	ResolvedAt               *time.Time        `json:"resolved_at,omitempty"`
	TerminalReason           string            `json:"terminal_reason,omitempty"`
	Overdue                  bool              `json:"overdue,omitempty"`
	OutboxState              string            `json:"outbox_state,omitempty"`
	OutboxErrorReason        string            `json:"outbox_error_reason,omitempty"`
}

type CreateWaitRequest struct {
	RequestID           string           `json:"request_id"`
	Collection          string           `json:"collection,omitempty"`
	Session             *AgentSessionRef `json:"session,omitempty"`
	PredicateKind       string           `json:"predicate_kind"`
	DependencyEventUUID string           `json:"dependency_event_uuid"`
	Deadline            time.Time        `json:"deadline"`
}

type GetWaitRequest struct {
	WaitUUID            string           `json:"wait_uuid,omitempty"`
	DependencyEventUUID string           `json:"dependency_event_uuid,omitempty"`
	Collection          string           `json:"collection,omitempty"`
	Session             *AgentSessionRef `json:"session,omitempty"`
}

type ListWaitsRequest struct {
	Collection          string           `json:"collection,omitempty"`
	OwnerAgentUUID      string           `json:"owner_agent_uuid,omitempty"`
	Session             *AgentSessionRef `json:"session,omitempty"`
	State               string           `json:"state,omitempty"`
	DependencyEventUUID string           `json:"dependency_event_uuid,omitempty"`
	Limit               int              `json:"limit,omitempty"`
	Offset              int              `json:"offset,omitempty"`
}

type CancelWaitRequest struct {
	RequestID           string           `json:"request_id"`
	WaitUUID            string           `json:"wait_uuid,omitempty"`
	DependencyEventUUID string           `json:"dependency_event_uuid,omitempty"`
	Collection          string           `json:"collection,omitempty"`
	Session             *AgentSessionRef `json:"session,omitempty"`
}

type WaitListResult struct {
	Waits []AgentWait `json:"waits"`
	Total int         `json:"total"`
}

const (
	WaitPredicateRequestResponse     = "request_response"
	WaitPredicateResponseGroupClosed = "response_group_closed"

	WaitStateWaiting       = "waiting"
	WaitStateSatisfied     = "satisfied"
	WaitStateTimedOut      = "timed_out"
	WaitStateCancelled     = "cancelled"
	WaitStateRestoreFenced = "restore_fenced"

	OutboxStatePending       = "pending"
	OutboxStatePublished     = "published"
	OutboxStateRestricted    = "restricted"
	OutboxStateRestoreFenced = "restore_fenced"

	waitNotificationAdmissionOpen = `NOT EXISTS (SELECT 1 FROM cairn.agent_wait_notification_fence f WHERE f.event_uuid = e.event_id)`
)

func validatePredicateKind(kind string) error {
	switch kind {
	case WaitPredicateRequestResponse, WaitPredicateResponseGroupClosed:
		return nil
	default:
		return failure("INVALID_REQUEST", "unsupported predicate kind; supported: request_response, response_group_closed")
	}
}

type WorkFeatureStatus struct {
	FeatureName string     `json:"feature_name"`
	Status      string     `json:"status"`
	Enabled     bool       `json:"enabled"`
	RetiredAt   *time.Time `json:"retired_at,omitempty"`
	UpdatedAt   time.Time  `json:"updated_at"`
}

type WaitFeatureSetRequest struct {
	RequestID string `json:"request_id"`
	Enabled   bool   `json:"enabled"`
}

type WaitTimeoutSweepRequest struct {
	Repo string `json:"repo"`
}

type WaitTimeoutSweepResult struct {
	Repo          string `json:"repo"`
	SweptTimeouts int64  `json:"swept_timeouts"`
}

type WaitOutboxDrainRequest struct {
	Repo  string `json:"repo"`
	Limit int    `json:"limit,omitempty"`
}

type WaitOutboxDrainResult struct {
	Repo             string `json:"repo"`
	PublishedNotices int64  `json:"published_notices"`
}

type WaitSchedulerTickRequest struct {
	Repo  string `json:"repo"`
	Limit int    `json:"limit,omitempty"`
}

type WaitSchedulerTickResult struct {
	Repo             string `json:"repo"`
	SweptTimeouts    int64  `json:"swept_timeouts"`
	PublishedNotices int64  `json:"published_notices"`
}

// checkCoordinationWaitsEnabled fails closed: missing row, disabled, or retired refuses registration.
func (s *Store) checkCoordinationWaitsEnabled(ctx context.Context, tx pgx.Tx) error {
	var status string
	err := tx.QueryRow(ctx, `SELECT status FROM cairn.work_feature WHERE feature_name = 'coordination-waits/1'`).Scan(&status)
	if err != nil || status != "active" {
		return failure("FEATURE_DISABLED", "coordination-waits registration is currently disabled")
	}
	return nil
}

// SetCoordinationWaitsEnabled uses the privileged Store mutation contract with operator
// channel enforcement, mutation request idempotency, restore admission and generation serialization.
func (s *Store) SetCoordinationWaitsEnabled(ctx context.Context, req WaitFeatureSetRequest) (WorkFeatureStatus, error) {
	if !s.channel.Operator {
		return WorkFeatureStatus{}, failure("AUTHORITY_DENIED", "feature control requires operator channel")
	}
	if err := validID(req.RequestID); err != nil {
		return WorkFeatureStatus{}, err
	}
	return privileged(ctx, s, "wait-feature-set", req.RequestID, req, func(tx pgx.Tx) (WorkFeatureStatus, error) {
		if _, err := retrievalGeneration(ctx, tx); err != nil {
			return WorkFeatureStatus{}, err
		}
		var currentStatus string
		var retiredAt *time.Time
		err := tx.QueryRow(ctx, `SELECT status, retired_at FROM cairn.work_feature WHERE feature_name = 'coordination-waits/1'`).Scan(&currentStatus, &retiredAt)
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return WorkFeatureStatus{}, err
		}
		if currentStatus == "retired" {
			return WorkFeatureStatus{}, failure("INVALID_REQUEST", "coordination-waits/1 is retired and cannot be modified")
		}
		newStatus := "disabled"
		if req.Enabled {
			newStatus = "active"
		}
		var out WorkFeatureStatus
		err = tx.QueryRow(ctx, `
INSERT INTO cairn.work_feature (feature_name, status, updated_at)
VALUES ('coordination-waits/1', $1, clock_timestamp())
ON CONFLICT (feature_name) DO UPDATE
SET status = EXCLUDED.status, updated_at = clock_timestamp()
RETURNING feature_name, status, retired_at, updated_at`, newStatus).Scan(&out.FeatureName, &out.Status, &out.RetiredAt, &out.UpdatedAt)
		if err != nil {
			return WorkFeatureStatus{}, err
		}
		out.Enabled = (out.Status == "active")
		return out, nil
	})
}

func (s *Store) GetWorkFeatureStatus(ctx context.Context, featureName string) (WorkFeatureStatus, error) {
	if featureName == "" {
		featureName = "coordination-waits/1"
	}
	var out WorkFeatureStatus
	err := s.pool.QueryRow(ctx, `
SELECT feature_name, status, retired_at, updated_at
FROM cairn.work_feature
WHERE feature_name = $1`, featureName).Scan(&out.FeatureName, &out.Status, &out.RetiredAt, &out.UpdatedAt)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return WorkFeatureStatus{
				FeatureName: featureName,
				Status:      "disabled",
				Enabled:     false,
			}, nil
		}
		return WorkFeatureStatus{}, err
	}
	out.Enabled = (out.Status == "active")
	return out, nil
}

func (s *Store) ListWorkFeatures(ctx context.Context) ([]WorkFeatureStatus, error) {
	rows, err := s.pool.Query(ctx, `
SELECT feature_name, status, retired_at, updated_at
FROM cairn.work_feature
ORDER BY feature_name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var list []WorkFeatureStatus
	for rows.Next() {
		var item WorkFeatureStatus
		if err := rows.Scan(&item.FeatureName, &item.Status, &item.RetiredAt, &item.UpdatedAt); err != nil {
			return nil, err
		}
		item.Enabled = (item.Status == "active")
		list = append(list, item)
	}
	return list, rows.Err()
}

func (s *Store) SetWorkFeatureStatus(ctx context.Context, requestID, featureName, status string) (WorkFeatureStatus, error) {
	if !s.channel.Operator {
		return WorkFeatureStatus{}, failure("AUTHORITY_DENIED", "feature control requires operator channel")
	}
	if err := validID(requestID); err != nil {
		return WorkFeatureStatus{}, err
	}
	if status != "active" && status != "disabled" && status != "retired" {
		return WorkFeatureStatus{}, failure("INVALID_REQUEST", "status must be active, disabled, or retired")
	}
	req := struct {
		RequestID   string `json:"request_id"`
		FeatureName string `json:"feature_name"`
		Status      string `json:"status"`
	}{
		RequestID:   requestID,
		FeatureName: featureName,
		Status:      status,
	}
	return privileged(ctx, s, "work-feature-set", requestID, req, func(tx pgx.Tx) (WorkFeatureStatus, error) {
		if _, err := retrievalGeneration(ctx, tx); err != nil {
			return WorkFeatureStatus{}, err
		}
		var currentStatus string
		err := tx.QueryRow(ctx, `SELECT status FROM cairn.work_feature WHERE feature_name = $1`, featureName).Scan(&currentStatus)
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return WorkFeatureStatus{}, err
		}
		if currentStatus == "retired" && status != "retired" {
			return WorkFeatureStatus{}, failure("INVALID_REQUEST", "feature is retired and cannot be modified")
		}
		var out WorkFeatureStatus
		if status == "retired" {
			err = tx.QueryRow(ctx, `
INSERT INTO cairn.work_feature (feature_name, status, retired_at, updated_at)
VALUES ($1, $2, clock_timestamp(), clock_timestamp())
ON CONFLICT (feature_name) DO UPDATE
SET status = EXCLUDED.status, retired_at = coalesce(cairn.work_feature.retired_at, clock_timestamp()), updated_at = clock_timestamp()
RETURNING feature_name, status, retired_at, updated_at`, featureName, status).Scan(&out.FeatureName, &out.Status, &out.RetiredAt, &out.UpdatedAt)
		} else {
			err = tx.QueryRow(ctx, `
INSERT INTO cairn.work_feature (feature_name, status, updated_at)
VALUES ($1, $2, clock_timestamp())
ON CONFLICT (feature_name) DO UPDATE
SET status = EXCLUDED.status, updated_at = clock_timestamp()
RETURNING feature_name, status, retired_at, updated_at`, featureName, status).Scan(&out.FeatureName, &out.Status, &out.RetiredAt, &out.UpdatedAt)
		}
		if err != nil {
			return WorkFeatureStatus{}, err
		}
		out.Enabled = (out.Status == "active")
		return out, nil
	})
}

// resolveWaiterIdentity validates that the caller is a real authenticated registered conversation.
// Rejects all profile/operator fallback paths, nil identities, and fabricated UUIDs.
func (s *Store) resolveWaiterIdentity(ctx context.Context, tx pgx.Tx, reqSession *AgentSessionRef, repo string, dest Destination) (AgentInstance, error) {
	var ref AgentSessionRef
	var owner string

	if s.session != nil {
		ref = s.session.Ref
		owner = s.session.Profile
	} else if reqSession != nil {
		if err := reqSession.Validate(); err != nil {
			return AgentInstance{}, err
		}
		ref = *reqSession
		if strings.HasPrefix(s.channel.Principal, "agent/") {
			if s.channel.Principal != "agent/"+ref.AgentID {
				return AgentInstance{}, failure("AUTHORITY_DENIED", "authenticated agent principal does not match session")
			}
			err := tx.QueryRow(ctx, `SELECT owner FROM cairn.agent_session WHERE agent_id=$1 AND repo=$2`, ref.AgentID, repo).Scan(&owner)
			if errors.Is(err, pgx.ErrNoRows) {
				return AgentInstance{}, failure("NOT_FOUND", "session is not registered to this profile")
			}
			if err != nil {
				return AgentInstance{}, err
			}
		} else {
			owner = s.channel.Principal
		}
	} else if strings.HasPrefix(s.channel.Principal, "agent/") {
		agentUUID := strings.TrimPrefix(s.channel.Principal, "agent/")
		if err := validID(agentUUID); err != nil {
			return AgentInstance{}, failure("AUTHORITY_DENIED", "invalid agent principal")
		}
		var execUUID, profileOwner string
		err := tx.QueryRow(ctx, `SELECT execution_id::text, owner FROM cairn.agent_session WHERE agent_id=$1 AND repo=$2`, agentUUID, repo).Scan(&execUUID, &profileOwner)
		if errors.Is(err, pgx.ErrNoRows) {
			return AgentInstance{}, failure("NOT_FOUND", "session is not registered to this profile")
		}
		if err != nil {
			return AgentInstance{}, err
		}
		ref = AgentSessionRef{AgentID: agentUUID, ExecutionID: execUUID}
		owner = profileOwner
	} else {
		return AgentInstance{}, failure("AUTHORITY_DENIED", "wait registration requires an authenticated registered session; operator channel is for inspection and maintenance only")
	}

	a, err := currentAgentSession(ctx, tx, ref, owner, repo, false, dest)
	if err != nil {
		return AgentInstance{}, err
	}
	if err = activeAgent(a); err != nil {
		return AgentInstance{}, err
	}
	return a, nil
}

func (s *Store) CreateWait(ctx context.Context, req CreateWaitRequest, dest Destination) (AgentWait, error) {
	if err := validID(req.RequestID); err != nil {
		return AgentWait{}, err
	}
	if err := validID(req.DependencyEventUUID); err != nil {
		return AgentWait{}, failure("INVALID_REQUEST", "valid dependency event UUID required")
	}
	if err := validatePredicateKind(req.PredicateKind); err != nil {
		return AgentWait{}, err
	}
	if req.Deadline.IsZero() {
		return AgentWait{}, failure("INVALID_REQUEST", "explicit future deadline required")
	}

	repo, err := s.eventScope(req.Collection, "")
	if err != nil {
		return AgentWait{}, err
	}
	guard := func(tx pgx.Tx) error {
		_, err := s.resolveWaiterIdentity(ctx, tx, req.Session, repo, dest)
		return err
	}
	out, err := mutate(ctx, s, "wait-create", req.RequestID, req, func(tx pgx.Tx) (AgentWait, error) {
		if err := s.checkCoordinationWaitsEnabled(ctx, tx); err != nil {
			return AgentWait{}, err
		}

		gen, err := retrievalGeneration(ctx, tx)
		if err != nil {
			return AgentWait{}, err
		}

		if err = lock(ctx, tx, "agent-events:"+repo); err != nil {
			return AgentWait{}, err
		}

		var now time.Time
		if err = tx.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&now); err != nil {
			return AgentWait{}, err
		}
		if !req.Deadline.After(now) {
			return AgentWait{}, failure("INVALID_REQUEST", "deadline must be in the future")
		}
		if req.Deadline.After(now.Add(30 * 24 * time.Hour)) {
			return AgentWait{}, failure("INVALID_REQUEST", "deadline cannot exceed 30 days")
		}

		// Authenticated registered conversation check (WAIT-2)
		agent, err := s.resolveWaiterIdentity(ctx, tx, req.Session, repo, dest)
		if err != nil {
			return AgentWait{}, err
		}
		ownerPrincipal := "agent/" + agent.AgentID
		ownerAgentUUID := agent.AgentID
		creationExecUUID := agent.ExecutionID
		var profileBinding string
		if err = tx.QueryRow(ctx, `SELECT binding FROM cairn.agent_session WHERE agent_id=$1 AND repo=$2`, agent.AgentID, repo).Scan(&profileBinding); err != nil {
			return AgentWait{}, err
		}

		var depPublisher, depKind, depDestType, depDestName string
		var depCorrID *string
		var depCreatedAt time.Time
		err = tx.QueryRow(ctx, `SELECT publisher, kind, destination_type, destination_name, correlation_id::text, created_at FROM cairn.agent_event WHERE event_id=$1 AND repo=$2`, req.DependencyEventUUID, repo).Scan(&depPublisher, &depKind, &depDestType, &depDestName, &depCorrID, &depCreatedAt)
		if errors.Is(err, pgx.ErrNoRows) {
			return AgentWait{}, failure("NOT_FOUND", "dependency event not found in collection")
		}
		if err != nil {
			return AgentWait{}, err
		}

		if depPublisher != ownerPrincipal && depPublisher != agent.ProfileOwner {
			return AgentWait{}, failure("AUTHORITY_DENIED", "caller is not the owner of the dependency event")
		}

		var grpState, grpCode string
		var grpDeadline time.Time
		var grpClosedAt *time.Time

		if req.PredicateKind == WaitPredicateRequestResponse {
			if depKind != "request" || depDestType != "agent" {
				return AgentWait{}, failure("INVALID_REQUEST", "request_response predicate requires direct-agent request dependency")
			}
		} else if req.PredicateKind == WaitPredicateResponseGroupClosed {
			var isGroup bool
			err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_response_group WHERE event_id=$1)`, req.DependencyEventUUID).Scan(&isGroup)
			if err != nil {
				return AgentWait{}, err
			}
			if !isGroup {
				return AgentWait{}, failure("INVALID_REQUEST", "response_group_closed predicate requires response group dependency")
			}
			grpState, grpCode, grpDeadline, grpClosedAt, err = reconcileDueResponseGroupTx(ctx, tx, req.DependencyEventUUID, now)
			if err != nil {
				return AgentWait{}, err
			}
		}

		var sessionWaitCount int
		if err = tx.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_wait WHERE collection=$1 AND owner_agent_uuid=$2 AND state='waiting'`, repo, ownerAgentUUID).Scan(&sessionWaitCount); err != nil {
			return AgentWait{}, err
		}
		if sessionWaitCount >= 100 {
			return AgentWait{}, failure("QUEUE_FULL", "maximum open waits reached for session (100)")
		}

		var collectionWaitCount int
		if err = tx.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_wait WHERE collection=$1 AND state='waiting'`, repo).Scan(&collectionWaitCount); err != nil {
			return AgentWait{}, err
		}
		if collectionWaitCount >= 1000 {
			return AgentWait{}, failure("QUEUE_FULL", "maximum open waits reached for collection (1000)")
		}

		var activeExists bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_wait WHERE collection=$1 AND owner_agent_uuid=$2 AND predicate_kind=$3 AND dependency_event_uuid=$4 AND state='waiting')`, repo, ownerAgentUUID, req.PredicateKind, req.DependencyEventUUID).Scan(&activeExists); err != nil {
			return AgentWait{}, err
		}
		if activeExists {
			return AgentWait{}, failure("OPEN_CONFLICT", "an active wait already exists for this dependency and predicate")
		}

		waitUUID := uuid.NewString()
		reservedNoticeEventUUID := uuid.NewString()
		reservedSourceRecordUUID := uuid.NewString()

		state := WaitStateWaiting
		var resolvedAt *time.Time
		terminalReason := ""
		var responseRef *RecordVersionRef

		if req.PredicateKind == WaitPredicateRequestResponse {
			var origConsumer string
			_ = tx.QueryRow(ctx, `SELECT consumer FROM cairn.agent_delivery WHERE event_id=$1 LIMIT 1`, req.DependencyEventUUID).Scan(&origConsumer)

			var respEventID, respRecordID string
			var respVersion int
			var respCreatedAt time.Time
			var respKind string
			err = tx.QueryRow(ctx, `SELECT event_id::text, record_id::text, version, created_at, kind FROM cairn.agent_event
WHERE causation_id=$1 AND repo=$2 AND kind IN ('response','failure')
  AND ($3='' OR publisher=$3)
  AND destination_type='agent' AND (destination_name=$4 OR destination_name='agent/'||$5)
  AND (($6::uuid IS NULL AND correlation_id IS NULL) OR ($6::uuid IS NOT NULL AND correlation_id=$6::uuid))
ORDER BY position ASC LIMIT 1`, req.DependencyEventUUID, repo, origConsumer, depPublisher, ownerAgentUUID, depCorrID).Scan(&respEventID, &respRecordID, &respVersion, &respCreatedAt, &respKind)
			if err == nil {
				if respCreatedAt.Before(req.Deadline) {
					state = WaitStateSatisfied
					resolvedAt = &now
					terminalReason = "response"
					responseRef = &RecordVersionRef{RecordID: respRecordID, Version: respVersion}
				} else {
					state = WaitStateTimedOut
					resolvedAt = &now
					terminalReason = "timeout"
				}
			} else if !errors.Is(err, pgx.ErrNoRows) {
				return AgentWait{}, err
			}
		} else if req.PredicateKind == WaitPredicateResponseGroupClosed {
			if grpState != "" && grpState != "open" && grpCode != "restore_fenced" {
				effectiveTime := grpDeadline
				if grpCode == "all_responded" && grpClosedAt != nil {
					effectiveTime = *grpClosedAt
				}
				if !effectiveTime.After(req.Deadline) {
					state = WaitStateSatisfied
					resolvedAt = &now
					terminalReason = "group_closed"
				} else {
					state = WaitStateTimedOut
					resolvedAt = &now
					terminalReason = "timeout"
				}
			}
		}

		sensitivityCeiling := "shareable"
		if dest.AllowLocal {
			sensitivityCeiling = "local"
		}

		var destJSON []byte
		destJSON, err = json.Marshal(dest)
		if err != nil {
			return AgentWait{}, err
		}

		var refJSON []byte
		if responseRef != nil {
			refJSON, err = json.Marshal(responseRef)
			if err != nil {
				return AgentWait{}, err
			}
		}

		_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_wait(
wait_uuid, collection, owner_principal, owner_agent_uuid, creation_execution_uuid,
registration_destination, sensitivity_ceiling, profile_binding_id, predicate_version,
predicate_kind, dependency_event_uuid, deadline, generation, state, response_ref,
reserved_notice_event_uuid, reserved_source_record_uuid, created_at, resolved_at, terminal_reason)
VALUES($1,$2,$3,$4,$5,$6,$7,$8,1,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)`,
			waitUUID, repo, ownerPrincipal, ownerAgentUUID, creationExecUUID,
			destJSON, sensitivityCeiling, profileBinding,
			req.PredicateKind, req.DependencyEventUUID, req.Deadline, gen,
			state, refJSON, reservedNoticeEventUUID, reservedSourceRecordUUID,
			now, resolvedAt, terminalReason)
		if err != nil {
			return AgentWait{}, err
		}

		outboxState := ""
		if state != WaitStateWaiting {
			_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_wait_outbox(wait_uuid, notice_event_uuid, source_record_uuid, state, updated_at)
VALUES($1,$2,$3,'pending',$4)`, waitUUID, reservedNoticeEventUUID, reservedSourceRecordUUID, now)
			outboxState = OutboxStatePending
		}

		eligibleRef, err := resolveEligibleResponseRef(ctx, tx, repo, refJSON, dest, sensitivityCeiling, dest)
		if err != nil {
			return AgentWait{}, err
		}

		return AgentWait{
			WaitUUID:                 waitUUID,
			Collection:               repo,
			OwnerPrincipal:           ownerPrincipal,
			OwnerAgentUUID:           ownerAgentUUID,
			CreationExecutionUUID:    creationExecUUID,
			RegistrationDestination:  dest,
			SensitivityCeiling:       sensitivityCeiling,
			ProfileBindingID:         profileBinding,
			PredicateVersion:         1,
			PredicateKind:            req.PredicateKind,
			DependencyEventUUID:      req.DependencyEventUUID,
			Deadline:                 req.Deadline,
			Generation:               gen,
			State:                    state,
			ResponseRef:              eligibleRef,
			ReservedNoticeEventUUID:  reservedNoticeEventUUID,
			ReservedSourceRecordUUID: reservedSourceRecordUUID,
			CreatedAt:                now,
			ResolvedAt:               resolvedAt,
			TerminalReason:           terminalReason,
			OutboxState:              outboxState,
		}, nil
	}, guard)
	if err != nil {
		return AgentWait{}, err
	}

	if out.ResponseRef != nil {
		readTx, rErr := s.beginLevel(ctx, pgx.RepeatableRead)
		if rErr != nil {
			return AgentWait{}, rErr
		}
		defer readTx.Rollback(ctx)
		refJSON, _ := json.Marshal(out.ResponseRef)
		checkedRef, cErr := resolveEligibleResponseRef(ctx, readTx, repo, refJSON, out.RegistrationDestination, out.SensitivityCeiling, dest)
		if cErr != nil {
			return AgentWait{}, cErr
		}
		out.ResponseRef = checkedRef
	}

	return out, nil
}

// authenticateWaitCaller verifies that the caller has operator authority or is an
// authenticated registered conversation. Non-operators must match the wait owner.
func (s *Store) authenticateWaitCaller(ctx context.Context, tx pgx.Tx, reqSession *AgentSessionRef, repo string, dest Destination) (callerAgentUUID string, isOperator bool, err error) {
	if s.channel.Operator {
		return "", true, nil
	}

	var ref AgentSessionRef
	var owner string

	if s.session != nil {
		ref = s.session.Ref
		owner = s.session.Profile
	} else if reqSession != nil {
		if err := reqSession.Validate(); err != nil {
			return "", false, err
		}
		ref = *reqSession
		if strings.HasPrefix(s.channel.Principal, "agent/") {
			if s.channel.Principal != "agent/"+ref.AgentID {
				return "", false, failure("AUTHORITY_DENIED", "authenticated agent principal does not match session")
			}
			err := tx.QueryRow(ctx, `SELECT owner FROM cairn.agent_session WHERE agent_id=$1 AND repo=$2`, ref.AgentID, repo).Scan(&owner)
			if errors.Is(err, pgx.ErrNoRows) {
				return "", false, failure("NOT_FOUND", "session is not registered to this profile")
			}
			if err != nil {
				return "", false, err
			}
		} else {
			owner = s.channel.Principal
		}
	} else if strings.HasPrefix(s.channel.Principal, "agent/") {
		agentUUID := strings.TrimPrefix(s.channel.Principal, "agent/")
		if err := validID(agentUUID); err != nil {
			return "", false, failure("AUTHORITY_DENIED", "invalid agent principal")
		}
		var execUUID, profileOwner string
		err := tx.QueryRow(ctx, `SELECT execution_id::text, owner FROM cairn.agent_session WHERE agent_id=$1 AND repo=$2`, agentUUID, repo).Scan(&execUUID, &profileOwner)
		if errors.Is(err, pgx.ErrNoRows) {
			return "", false, failure("NOT_FOUND", "session is not registered to this profile")
		}
		if err != nil {
			return "", false, err
		}
		ref = AgentSessionRef{AgentID: agentUUID, ExecutionID: execUUID}
		owner = profileOwner
	} else {
		return "", false, failure("AUTHORITY_DENIED", "wait operations require an authenticated registered session or operator channel")
	}

	a, err := currentAgentSession(ctx, tx, ref, owner, repo, false, dest)
	if err != nil {
		return "", false, err
	}
	if err = activeAgent(a); err != nil {
		return "", false, err
	}
	return a.AgentID, false, nil
}

func resolveEligibleResponseRef(ctx context.Context, tx pgx.Tx, repo string, refJSON []byte, origDest Destination, origCeiling string, callerDest Destination) (*RecordVersionRef, error) {
	if len(refJSON) == 0 {
		return nil, nil
	}
	var ref RecordVersionRef
	if err := json.Unmarshal(refJSON, &ref); err != nil {
		return nil, nil
	}
	if ref.RecordID == "" || ref.Version <= 0 {
		return nil, nil
	}
	var recSensitivity, recLifecycle string
	var payloadDeleted bool
	err := tx.QueryRow(ctx, `SELECT m.sensitivity, m.lifecycle, v.payload_deleted_by IS NOT NULL 
FROM cairn.memory_record m 
JOIN cairn.record_version v ON v.record_id=m.record_id 
WHERE m.record_id=$1 AND v.version=$2 AND v.repo=$3`, ref.RecordID, ref.Version, repo).Scan(&recSensitivity, &recLifecycle, &payloadDeleted)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if recLifecycle != "active" || payloadDeleted {
		return nil, nil
	}
	var evtSensitivity string
	_ = tx.QueryRow(ctx, `SELECT sensitivity FROM cairn.agent_event WHERE record_id=$1 AND version=$2 AND repo=$3 LIMIT 1`, ref.RecordID, ref.Version, repo).Scan(&evtSensitivity)

	origAllowsLocal := origDest.AllowLocal && (origCeiling == "" || origCeiling == "local")
	callerAllowsLocal := callerDest.AllowLocal
	if (recSensitivity == "local" || evtSensitivity == "local") && (!origAllowsLocal || !callerAllowsLocal) {
		return nil, nil
	}
	return &ref, nil
}

func (s *Store) GetWait(ctx context.Context, req GetWaitRequest, dest Destination) (AgentWait, error) {
	if req.WaitUUID == "" && req.DependencyEventUUID == "" {
		return AgentWait{}, failure("INVALID_REQUEST", "wait UUID or dependency event UUID required")
	}
	repo, err := s.eventScope(req.Collection, "")
	if err != nil {
		return AgentWait{}, err
	}

	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return AgentWait{}, err
	}
	defer tx.Rollback(ctx)

	callerAgentUUID, isOperator, err := s.authenticateWaitCaller(ctx, tx, req.Session, repo, dest)
	if err != nil {
		return AgentWait{}, err
	}

	var w AgentWait
	var destJSON, refJSON []byte
	var outboxState, outboxErrReason *string
	var now time.Time

	row := tx.QueryRow(ctx, `SELECT
w.wait_uuid::text, w.collection, w.owner_principal, w.owner_agent_uuid::text, w.creation_execution_uuid::text,
w.registration_destination, w.sensitivity_ceiling, w.profile_binding_id, w.predicate_version,
w.predicate_kind, w.dependency_event_uuid::text, w.deadline, w.generation, w.state, w.response_ref,
w.reserved_notice_event_uuid::text, w.reserved_source_record_uuid::text, w.created_at, w.resolved_at,
w.terminal_reason, o.state, o.error_reason, clock_timestamp()
FROM cairn.agent_wait w
LEFT JOIN cairn.agent_wait_outbox o USING(wait_uuid)
WHERE w.collection=$1
  AND ($2='' OR w.wait_uuid=NULLIF($2,'')::uuid)
  AND ($3='' OR w.dependency_event_uuid=NULLIF($3,'')::uuid)
ORDER BY w.created_at DESC LIMIT 1`, repo, req.WaitUUID, req.DependencyEventUUID)

	err = row.Scan(
		&w.WaitUUID, &w.Collection, &w.OwnerPrincipal, &w.OwnerAgentUUID, &w.CreationExecutionUUID,
		&destJSON, &w.SensitivityCeiling, &w.ProfileBindingID, &w.PredicateVersion,
		&w.PredicateKind, &w.DependencyEventUUID, &w.Deadline, &w.Generation, &w.State, &refJSON,
		&w.ReservedNoticeEventUUID, &w.ReservedSourceRecordUUID, &w.CreatedAt, &w.ResolvedAt,
		&w.TerminalReason, &outboxState, &outboxErrReason, &now)
	if errors.Is(err, pgx.ErrNoRows) {
		return AgentWait{}, failure("NOT_FOUND", "wait not found")
	}
	if err != nil {
		return AgentWait{}, err
	}

	if !isOperator && w.OwnerAgentUUID != callerAgentUUID {
		return AgentWait{}, failure("AUTHORITY_DENIED", "foreign agent cannot access wait")
	}

	if len(destJSON) > 0 {
		_ = json.Unmarshal(destJSON, &w.RegistrationDestination)
	}
	eligibleRef, err := resolveEligibleResponseRef(ctx, tx, repo, refJSON, w.RegistrationDestination, w.SensitivityCeiling, dest)
	if err != nil {
		return AgentWait{}, err
	}
	w.ResponseRef = eligibleRef
	if outboxState != nil {
		w.OutboxState = *outboxState
	}
	if outboxErrReason != nil {
		w.OutboxErrorReason = *outboxErrReason
	}

	if w.State == WaitStateWaiting && !w.Deadline.After(now) {
		w.Overdue = true
	}

	return w, nil
}

func (s *Store) ListWaits(ctx context.Context, req ListWaitsRequest, dest Destination) (WaitListResult, error) {
	repo, err := s.eventScope(req.Collection, "")
	if err != nil {
		return WaitListResult{}, err
	}
	limit := req.Limit
	if limit <= 0 || limit > 100 {
		limit = 50
	}

	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return WaitListResult{}, err
	}
	defer tx.Rollback(ctx)

	callerAgentUUID, isOperator, err := s.authenticateWaitCaller(ctx, tx, req.Session, repo, dest)
	if err != nil {
		return WaitListResult{}, err
	}

	ownerFilter := req.OwnerAgentUUID
	if !isOperator {
		if ownerFilter != "" && ownerFilter != callerAgentUUID {
			return WaitListResult{}, failure("AUTHORITY_DENIED", "cannot list waits for another agent session")
		}
		ownerFilter = callerAgentUUID
	}

	rows, err := tx.Query(ctx, `SELECT
w.wait_uuid::text, w.collection, w.owner_principal, w.owner_agent_uuid::text, w.creation_execution_uuid::text,
w.registration_destination, w.sensitivity_ceiling, w.profile_binding_id, w.predicate_version,
w.predicate_kind, w.dependency_event_uuid::text, w.deadline, w.generation, w.state, w.response_ref,
w.reserved_notice_event_uuid::text, w.reserved_source_record_uuid::text, w.created_at, w.resolved_at,
w.terminal_reason, o.state, o.error_reason, clock_timestamp()
FROM cairn.agent_wait w
LEFT JOIN cairn.agent_wait_outbox o USING(wait_uuid)
WHERE w.collection=$1
  AND ($2='' OR w.owner_agent_uuid=NULLIF($2,'')::uuid)
  AND ($3='' OR w.state=$3)
  AND ($4='' OR w.dependency_event_uuid=NULLIF($4,'')::uuid)
ORDER BY w.created_at DESC LIMIT $5 OFFSET $6`, repo, ownerFilter, req.State, req.DependencyEventUUID, limit, req.Offset)
	if err != nil {
		return WaitListResult{}, err
	}
	defer rows.Close()

	list := make([]AgentWait, 0)
	var rawRefs [][]byte
	for rows.Next() {
		var w AgentWait
		var destJSON, refJSON []byte
		var outboxState, outboxErrReason *string
		var now time.Time

		if err = rows.Scan(
			&w.WaitUUID, &w.Collection, &w.OwnerPrincipal, &w.OwnerAgentUUID, &w.CreationExecutionUUID,
			&destJSON, &w.SensitivityCeiling, &w.ProfileBindingID, &w.PredicateVersion,
			&w.PredicateKind, &w.DependencyEventUUID, &w.Deadline, &w.Generation, &w.State, &refJSON,
			&w.ReservedNoticeEventUUID, &w.ReservedSourceRecordUUID, &w.CreatedAt, &w.ResolvedAt,
			&w.TerminalReason, &outboxState, &outboxErrReason, &now); err != nil {
			return WaitListResult{}, err
		}
		if len(destJSON) > 0 {
			_ = json.Unmarshal(destJSON, &w.RegistrationDestination)
		}
		if outboxState != nil {
			w.OutboxState = *outboxState
		}
		if outboxErrReason != nil {
			w.OutboxErrorReason = *outboxErrReason
		}
		if w.State == WaitStateWaiting && !w.Deadline.After(now) {
			w.Overdue = true
		}
		list = append(list, w)
		rawRefs = append(rawRefs, refJSON)
	}
	if err = rows.Err(); err != nil {
		return WaitListResult{}, err
	}
	rows.Close()

	for i := range list {
		eligibleRef, err := resolveEligibleResponseRef(ctx, tx, repo, rawRefs[i], list[i].RegistrationDestination, list[i].SensitivityCeiling, dest)
		if err != nil {
			return WaitListResult{}, err
		}
		list[i].ResponseRef = eligibleRef
	}

	var total int
	err = tx.QueryRow(ctx, `SELECT count(*)
FROM cairn.agent_wait w
WHERE w.collection=$1
  AND ($2='' OR w.owner_agent_uuid=NULLIF($2,'')::uuid)
  AND ($3='' OR w.state=$3)
  AND ($4='' OR w.dependency_event_uuid=NULLIF($4,'')::uuid)`, repo, ownerFilter, req.State, req.DependencyEventUUID).Scan(&total)
	if err != nil {
		return WaitListResult{}, err
	}

	return WaitListResult{Waits: list, Total: total}, nil
}

func (s *Store) CancelWait(ctx context.Context, req CancelWaitRequest, dest Destination) (AgentWait, error) {
	if err := validID(req.RequestID); err != nil {
		return AgentWait{}, err
	}
	if req.WaitUUID == "" && req.DependencyEventUUID == "" {
		return AgentWait{}, failure("INVALID_REQUEST", "wait UUID or dependency event UUID required for cancellation")
	}

	repo, err := s.eventScope(req.Collection, "")
	if err != nil {
		return AgentWait{}, err
	}
	req.Collection = repo

	return mutate(ctx, s, "wait-cancel", req.RequestID, req, func(tx pgx.Tx) (AgentWait, error) {
		callerAgentUUID, isOperator, err := s.authenticateWaitCaller(ctx, tx, req.Session, repo, dest)
		if err != nil {
			return AgentWait{}, err
		}

		gen, err := retrievalGeneration(ctx, tx)
		if err != nil {
			return AgentWait{}, err
		}
		if err = lock(ctx, tx, "agent-events:"+repo); err != nil {
			return AgentWait{}, err
		}

		var w AgentWait
		var destJSON, refJSON []byte
		var outboxState, outboxErrReason *string
		var now time.Time

		row := tx.QueryRow(ctx, `SELECT
w.wait_uuid::text, w.collection, w.owner_principal, w.owner_agent_uuid::text, w.creation_execution_uuid::text,
w.registration_destination, w.sensitivity_ceiling, w.profile_binding_id, w.predicate_version,
w.predicate_kind, w.dependency_event_uuid::text, w.deadline, w.generation, w.state, w.response_ref,
w.reserved_notice_event_uuid::text, w.reserved_source_record_uuid::text, w.created_at, w.resolved_at,
w.terminal_reason, o.state, o.error_reason, clock_timestamp()
FROM cairn.agent_wait w
LEFT JOIN cairn.agent_wait_outbox o USING(wait_uuid)
WHERE w.collection=$1
  AND ($2='' OR w.wait_uuid=NULLIF($2,'')::uuid)
  AND ($3='' OR w.dependency_event_uuid=NULLIF($3,'')::uuid)
ORDER BY w.created_at DESC LIMIT 1 FOR UPDATE OF w`, repo, req.WaitUUID, req.DependencyEventUUID)

		err = row.Scan(
			&w.WaitUUID, &w.Collection, &w.OwnerPrincipal, &w.OwnerAgentUUID, &w.CreationExecutionUUID,
			&destJSON, &w.SensitivityCeiling, &w.ProfileBindingID, &w.PredicateVersion,
			&w.PredicateKind, &w.DependencyEventUUID, &w.Deadline, &w.Generation, &w.State, &refJSON,
			&w.ReservedNoticeEventUUID, &w.ReservedSourceRecordUUID, &w.CreatedAt, &w.ResolvedAt,
			&w.TerminalReason, &outboxState, &outboxErrReason, &now)
		if errors.Is(err, pgx.ErrNoRows) {
			return AgentWait{}, failure("NOT_FOUND", "wait not found")
		}
		if err != nil {
			return AgentWait{}, err
		}

		if !isOperator && w.OwnerAgentUUID != callerAgentUUID {
			return AgentWait{}, failure("AUTHORITY_DENIED", "foreign agent cannot cancel wait")
		}

		if w.State != WaitStateWaiting {
			return AgentWait{}, failure("VERSION_CONFLICT", "wait is already terminal ("+w.State+")")
		}

		w.State = WaitStateCancelled
		w.ResolvedAt = &now
		w.TerminalReason = "cancelled"
		w.Generation = gen

		_, err = tx.Exec(ctx, `UPDATE cairn.agent_wait SET state='cancelled', resolved_at=$2, terminal_reason='cancelled' WHERE wait_uuid=$1`, w.WaitUUID, now)
		if err != nil {
			return AgentWait{}, err
		}

		return w, nil
	})
}

func resolveWaitsOnEvent(ctx context.Context, tx pgx.Tx, event AgentEvent) error {
	if event.CausationID == "" {
		return nil
	}

	rows, err := tx.Query(ctx, `SELECT
w.wait_uuid::text, w.collection, w.owner_principal, w.owner_agent_uuid::text, w.creation_execution_uuid::text,
w.dependency_event_uuid::text, w.deadline, w.reserved_notice_event_uuid::text, w.reserved_source_record_uuid::text
FROM cairn.agent_wait w
WHERE w.dependency_event_uuid=$1 AND w.collection=$2 AND w.predicate_kind=$3 AND w.state='waiting'
ORDER BY w.wait_uuid FOR UPDATE OF w`, event.CausationID, event.Repo, WaitPredicateRequestResponse)
	if err != nil {
		return err
	}
	defer rows.Close()

	type pendingWait struct {
		waitUUID            string
		collection          string
		ownerPrincipal      string
		ownerAgentUUID      string
		creationExecUUID    string
		dependencyEventUUID string
		deadline            time.Time
		reservedNoticeUUID  string
		reservedSourceUUID  string
	}
	var toResolve []pendingWait
	for rows.Next() {
		var pw pendingWait
		if err = rows.Scan(&pw.waitUUID, &pw.collection, &pw.ownerPrincipal, &pw.ownerAgentUUID, &pw.creationExecUUID, &pw.dependencyEventUUID, &pw.deadline, &pw.reservedNoticeUUID, &pw.reservedSourceUUID); err != nil {
			return err
		}
		toResolve = append(toResolve, pw)
	}
	rows.Close()

	if len(toResolve) == 0 {
		return nil
	}

	var origConsumer string
	var origPublisher string
	var origCorrID *string
	err = tx.QueryRow(ctx, `SELECT d.consumer, e.publisher, e.correlation_id::text
FROM cairn.agent_event e
LEFT JOIN cairn.agent_delivery d USING(event_id)
WHERE e.event_id=$1 LIMIT 1`, event.CausationID).Scan(&origConsumer, &origPublisher, &origCorrID)
	if err != nil {
		return err
	}

	if event.Kind != "response" && event.Kind != "failure" {
		return nil
	}
	if event.From != origConsumer {
		return nil
	}

	corrMatches := false
	if origCorrID == nil && event.CorrelationID == "" {
		corrMatches = true
	} else if origCorrID != nil && *origCorrID == event.CorrelationID {
		corrMatches = true
	}
	if !corrMatches {
		return nil
	}

	var now time.Time
	if err = tx.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&now); err != nil {
		return err
	}

	ref := RecordVersionRef{RecordID: event.Ref.RecordID, Version: event.Ref.Version}
	refJSON, err := json.Marshal(ref)
	if err != nil {
		return err
	}

	for _, pw := range toResolve {
		destMatches := (event.Destination.Type == "agent" && (event.Destination.Name == pw.ownerPrincipal || event.Destination.Name == "agent/"+pw.ownerAgentUUID || event.Destination.Name == origPublisher))
		if !destMatches {
			continue
		}

		if event.CreatedAt.Before(pw.deadline) {
			_, err = tx.Exec(ctx, `UPDATE cairn.agent_wait SET state='satisfied', resolved_at=$2, terminal_reason='response', response_ref=$3 WHERE wait_uuid=$1`, pw.waitUUID, now, refJSON)
			if err != nil {
				return err
			}
			_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_wait_outbox(wait_uuid, notice_event_uuid, source_record_uuid, state, updated_at)
VALUES($1,$2,$3,'pending',$4) ON CONFLICT (wait_uuid) DO NOTHING`, pw.waitUUID, pw.reservedNoticeUUID, pw.reservedSourceUUID, now)
			if err != nil {
				return err
			}
		} else {
			_, err = tx.Exec(ctx, `UPDATE cairn.agent_wait SET state='timed_out', resolved_at=$2, terminal_reason='timeout' WHERE wait_uuid=$1`, pw.waitUUID, now)
			if err != nil {
				return err
			}
			_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_wait_outbox(wait_uuid, notice_event_uuid, source_record_uuid, state, updated_at)
VALUES($1,$2,$3,'pending',$4) ON CONFLICT (wait_uuid) DO NOTHING`, pw.waitUUID, pw.reservedNoticeUUID, pw.reservedSourceUUID, now)
			if err != nil {
				return err
			}
		}
	}
	return nil
}

func resolveWaitsOnGroupClosure(ctx context.Context, tx pgx.Tx, groupEventID, code string, effectiveClosedAt time.Time) error {
	if code == "restore_fenced" {
		return nil
	}

	rows, err := tx.Query(ctx, `SELECT
w.wait_uuid::text, w.deadline, w.reserved_notice_event_uuid::text, w.reserved_source_record_uuid::text
FROM cairn.agent_wait w
WHERE w.dependency_event_uuid=$1 AND w.predicate_kind=$2 AND w.state='waiting'
ORDER BY w.wait_uuid FOR UPDATE OF w`, groupEventID, WaitPredicateResponseGroupClosed)
	if err != nil {
		return err
	}
	defer rows.Close()

	type groupWait struct {
		waitUUID           string
		deadline           time.Time
		reservedNoticeUUID string
		reservedSourceUUID string
	}
	var list []groupWait
	for rows.Next() {
		var gw groupWait
		if err = rows.Scan(&gw.waitUUID, &gw.deadline, &gw.reservedNoticeUUID, &gw.reservedSourceUUID); err != nil {
			return err
		}
		list = append(list, gw)
	}
	rows.Close()

	if len(list) == 0 {
		return nil
	}

	var now time.Time
	if err = tx.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&now); err != nil {
		return err
	}

	for _, gw := range list {
		if effectiveClosedAt.Before(gw.deadline) {
			_, err = tx.Exec(ctx, `UPDATE cairn.agent_wait SET state='satisfied', resolved_at=$2, terminal_reason='group_closed' WHERE wait_uuid=$1`, gw.waitUUID, now)
			if err != nil {
				return err
			}
		} else {
			_, err = tx.Exec(ctx, `UPDATE cairn.agent_wait SET state='timed_out', resolved_at=$2, terminal_reason='timeout' WHERE wait_uuid=$1`, gw.waitUUID, now)
			if err != nil {
				return err
			}
		}
		_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_wait_outbox(wait_uuid, notice_event_uuid, source_record_uuid, state, updated_at)
VALUES($1,$2,$3,'pending',$4) ON CONFLICT (wait_uuid) DO NOTHING`, gw.waitUUID, gw.reservedNoticeUUID, gw.reservedSourceUUID, now)
		if err != nil {
			return err
		}
	}
	return nil
}
