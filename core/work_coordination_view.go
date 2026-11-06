package core

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

const WorkCoordinationViewSchema = "cairn.work-coordination-view/1"

type WorkCoordinationRequest struct {
	Collection   string `json:"collection"`
	ProjectSlug  string `json:"project_slug"`
	Item         string `json:"item"`
	Limit        int    `json:"limit,omitempty"`
	WaitCursor   string `json:"wait_cursor,omitempty"`
	NoticeCursor string `json:"notice_cursor,omitempty"`
}

type WorkItemCoordination struct {
	Schema            string                      `json:"schema"`
	Collection        string                      `json:"collection"`
	BindingID         uuid.UUID                   `json:"binding_id"`
	PlaneItemUUID     uuid.UUID                   `json:"plane_item_uuid"`
	ObservedAt        time.Time                   `json:"observed_at"`
	RestoreGeneration int64                       `json:"restore_generation"`
	Waits             WorkCoordinationWaitPanel   `json:"waits"`
	QuietNotices      WorkCoordinationNoticePanel `json:"quiet_notices"`
}

type WorkCoordinationAssociationRef struct {
	AssociationID uuid.UUID `json:"association_id"`
	Revision      int64     `json:"revision"`
	Basis         string    `json:"basis"`
}

type WorkCoordinationDeliveryView struct {
	DeliveryID  uuid.UUID  `json:"delivery_id"`
	State       string     `json:"state"`
	Code        string     `json:"code"`
	CompletedAt *time.Time `json:"completed_at"`
}

type WorkCoordinationWaitPredicate struct {
	Version             int       `json:"version"`
	Kind                string    `json:"kind"`
	DependencyEventUUID uuid.UUID `json:"dependency_event_uuid"`
}

type WorkCoordinationWaitNotification struct {
	OutboxState       string                        `json:"outbox_state"`
	RestrictionReason *string                       `json:"restriction_reason"`
	EventID           *uuid.UUID                    `json:"event_id"`
	RestoreFenced     bool                          `json:"restore_fenced"`
	Delivery          *WorkCoordinationDeliveryView `json:"delivery"`
}

type WorkCoordinationWaitItem struct {
	WaitUUID              uuid.UUID                        `json:"wait_uuid"`
	OwnerAgentUUID        uuid.UUID                        `json:"owner_agent_uuid"`
	CreationExecutionUUID uuid.UUID                        `json:"creation_execution_uuid"`
	Predicate             WorkCoordinationWaitPredicate    `json:"predicate"`
	DependencyStatus      string                           `json:"dependency_status"`
	Deadline              time.Time                        `json:"deadline"`
	CreatedAt             time.Time                        `json:"created_at"`
	ResolvedAt            *time.Time                       `json:"resolved_at"`
	State                 string                           `json:"state"`
	TerminalReason        *string                          `json:"terminal_reason"`
	Overdue               bool                             `json:"overdue"`
	Notification          WorkCoordinationWaitNotification `json:"notification"`
	Associations          []WorkCoordinationAssociationRef `json:"associations"`
	Historical            bool                             `json:"historical"`
}

type WorkCoordinationWaitPanel struct {
	FeatureStatus string                     `json:"feature_status"`
	Items         []WorkCoordinationWaitItem `json:"items"`
	NextCursor    *string                    `json:"next_cursor"`
}

type WorkNoticeRecipientSummary struct {
	AssociationID       uuid.UUID `json:"association_id"`
	AssociationRevision int64     `json:"association_revision"`
	AgentID             uuid.UUID `json:"agent_id"`
	ExecutionID         uuid.UUID `json:"execution_id"`
	Historical          bool      `json:"historical"`
	Online              bool      `json:"online"`
	Support             string    `json:"support"`
}

type WorkCoordinationNoticeItem struct {
	NoticeID            uuid.UUID                        `json:"notice_id"`
	EventID             uuid.UUID                        `json:"event_id"`
	RecipientAgentID    uuid.UUID                        `json:"recipient_agent_id"`
	State               string                           `json:"state"`
	CreatedAt           time.Time                        `json:"created_at"`
	ExpiresAt           time.Time                        `json:"expires_at"`
	AdmittedAt          *time.Time                       `json:"admitted_at"`
	ExpiryOverdue       bool                             `json:"expiry_overdue"`
	SourceStatus        string                           `json:"source_status"`
	UnavailableReason   *string                          `json:"unavailable_reason"`
	AdmittedExecutionID *uuid.UUID                       `json:"admitted_execution_id"`
	Delivery            *WorkCoordinationDeliveryView    `json:"delivery"`
	Associations        []WorkCoordinationAssociationRef `json:"associations"`
	Historical          bool                             `json:"historical"`
}

type WorkCoordinationNoticePanel struct {
	FeatureStatus      string                       `json:"feature_status"`
	Items              []WorkCoordinationNoticeItem `json:"items"`
	NextCursor         *string                      `json:"next_cursor"`
	QueuedCount        int                          `json:"queued_count"`
	OverdueQueuedCount int                          `json:"overdue_queued_count"`
	OldestQueuedAt     *time.Time                   `json:"oldest_queued_at"`
	EarliestExpiry     *time.Time                   `json:"earliest_expiry"`
	Recipients         []WorkNoticeRecipientSummary `json:"recipients"`
}

type workCoordinationCursor struct {
	Version           int       `json:"v"`
	Panel             string    `json:"panel"`
	Collection        string    `json:"collection"`
	BindingID         uuid.UUID `json:"binding_id"`
	PlaneItemUUID     uuid.UUID `json:"plane_item_uuid"`
	Limit             int       `json:"limit"`
	RestoreGeneration int64     `json:"restore_generation"`
	CreatedAt         time.Time `json:"created_at"`
	RowID             uuid.UUID `json:"id"`
}

func encodeWorkCoordinationCursor(c workCoordinationCursor) (string, error) {
	c.Version = 1
	c.CreatedAt = c.CreatedAt.UTC().Truncate(time.Microsecond)
	raw, err := json.Marshal(c)
	if err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(raw), nil
}

func decodeWorkCoordinationCursor(raw, expectedPanel, collection string, bindingID, planeItemUUID uuid.UUID, limit int, currentGen int64) (*workCoordinationCursor, error) {
	if raw == "" {
		return nil, nil
	}
	if len(raw) > 2048 || strings.Contains(raw, "=") {
		return nil, failure("INVALID_REQUEST", "invalid coordination cursor")
	}
	decoded, err := base64.RawURLEncoding.DecodeString(raw)
	if err != nil || len(decoded) == 0 {
		return nil, failure("INVALID_REQUEST", "invalid coordination cursor")
	}
	var c workCoordinationCursor
	dec := json.NewDecoder(bytes.NewReader(decoded))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&c); err != nil {
		return nil, failure("INVALID_REQUEST", "invalid coordination cursor")
	}
	var extra any
	if err := dec.Decode(&extra); !errors.Is(err, io.EOF) {
		return nil, failure("INVALID_REQUEST", "invalid coordination cursor")
	}
	if c.Version != 1 || c.Panel != expectedPanel || c.Collection != collection ||
		c.BindingID != bindingID || c.PlaneItemUUID != planeItemUUID ||
		c.Limit != limit || c.RowID == uuid.Nil || c.CreatedAt.IsZero() {
		return nil, failure("INVALID_REQUEST", "invalid coordination cursor")
	}
	if c.RestoreGeneration != currentGen {
		return nil, failure("STALE_CURSOR", "coordination cursor predates restore fence; reload from the first page")
	}
	c.CreatedAt = c.CreatedAt.UTC().Truncate(time.Microsecond)
	return &c, nil
}

func (s *Store) GetWorkItemCoordination(ctx context.Context, req WorkCoordinationRequest) (WorkItemCoordination, error) {
	if err := s.requireOperator(); err != nil {
		return WorkItemCoordination{}, err
	}
	collection := req.Collection
	if collection == "" {
		collection = s.channel.Repo
	}
	if err := s.checkRepo(collection); err != nil {
		return WorkItemCoordination{}, err
	}
	if strings.TrimSpace(req.ProjectSlug) == "" {
		return WorkItemCoordination{}, failure("INVALID_REQUEST", "project_slug is required")
	}
	if strings.TrimSpace(req.Item) == "" {
		return WorkItemCoordination{}, failure("INVALID_REQUEST", "item identifier or UUID is required")
	}
	limit := req.Limit
	if limit == 0 {
		limit = 25
	}
	if limit < 1 || limit > 100 {
		return WorkItemCoordination{}, failure("INVALID_REQUEST", "limit must be between 1 and 100")
	}

	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return WorkItemCoordination{}, err
	}
	defer tx.Rollback(ctx)

	if err := s.checkWorkReadableTx(ctx, tx); err != nil {
		return WorkItemCoordination{}, err
	}
	gen, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return WorkItemCoordination{}, err
	}
	var now time.Time
	if err := tx.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&now); err != nil {
		return WorkItemCoordination{}, err
	}

	bindingID, item, _, associations, err := s.resolveWorkItemAndAssociationsTx(ctx, tx, collection, req.ProjectSlug, req.Item, now)
	if err != nil {
		return WorkItemCoordination{}, err
	}

	waitCursor, err := decodeWorkCoordinationCursor(req.WaitCursor, "waits", collection, bindingID, item.PlaneItemUUID, limit, gen)
	if err != nil {
		return WorkItemCoordination{}, err
	}
	noticeCursor, err := decodeWorkCoordinationCursor(req.NoticeCursor, "quiet_notices", collection, bindingID, item.PlaneItemUUID, limit, gen)
	if err != nil {
		return WorkItemCoordination{}, err
	}

	waitsFeatureStatus, err := s.getWorkFeatureStatusTx(ctx, tx, "coordination-waits/1")
	if err != nil {
		return WorkItemCoordination{}, err
	}
	noticesFeatureStatus, err := s.getWorkFeatureStatusTx(ctx, tx, "next-turn-notices/1")
	if err != nil {
		return WorkItemCoordination{}, err
	}

	waitPanel, err := s.projectWorkCoordinationWaitsTx(ctx, tx, collection, bindingID, item.PlaneItemUUID, associations, gen, now, limit, waitCursor, waitsFeatureStatus)
	if err != nil {
		return WorkItemCoordination{}, err
	}
	noticePanel, err := s.projectWorkCoordinationNoticesTx(ctx, tx, collection, bindingID, item.PlaneItemUUID, associations, gen, now, limit, noticeCursor, noticesFeatureStatus)
	if err != nil {
		return WorkItemCoordination{}, err
	}

	return WorkItemCoordination{
		Schema:            WorkCoordinationViewSchema,
		Collection:        collection,
		BindingID:         bindingID,
		PlaneItemUUID:     item.PlaneItemUUID,
		ObservedAt:        now,
		RestoreGeneration: gen,
		Waits:             waitPanel,
		QuietNotices:      noticePanel,
	}, tx.Commit(ctx)
}

type coordinationAgentState struct {
	exists      bool
	executionID uuid.UUID
	generation  int64
	visibility  string
	binding     string
	online      bool
	supported   bool
}

func loadCoordinationAgentStateTx(ctx context.Context, tx pgx.Tx, collection string, agentID uuid.UUID, currentGen int64, now time.Time) (coordinationAgentState, error) {
	var st coordinationAgentState
	var metaRaw []byte
	var expiresAt time.Time
	var stopped bool
	err := tx.QueryRow(ctx, `SELECT execution_id, database_generation, visibility, binding, metadata, expires_at, stopped
		FROM cairn.agent_session WHERE repo = $1 AND agent_id = $2`, collection, agentID).
		Scan(&st.executionID, &st.generation, &st.visibility, &st.binding, &metaRaw, &expiresAt, &stopped)
	if errors.Is(err, pgx.ErrNoRows) {
		return st, nil
	}
	if err != nil {
		return st, err
	}
	st.exists = true
	st.online = !stopped && expiresAt.After(now)
	if st.generation == currentGen {
		var meta AgentMetadata
		if err := json.Unmarshal(metaRaw, &meta); err != nil {
			return st, err
		}
		if meta.DeliveryMode == "existing-session" && meta.Harness == "codex" {
			var activeBinding bool
			if err := tx.QueryRow(ctx, `SELECT EXISTS(
				SELECT 1 FROM cairn.agent_native_owner_session_binding b
				JOIN cairn.agent_native_owner_launch l ON l.launch_id = b.launch_id
				WHERE b.repo = $1 AND b.agent_id = $2 AND b.execution_id = $3 AND b.restore_generation = $4
				  AND b.state = 'active' AND l.repo = $1 AND l.status = 'active' AND l.restore_generation = $4
			)`, collection, agentID, st.executionID, currentGen).Scan(&activeBinding); err != nil {
				return st, err
			}
			st.supported = activeBinding
		}
	}
	return st, nil
}

// classifyCoordinationEventSourceTx classifies source availability without reading any body column
// and without widening the retained destination/sensitivity ceiling (`allowsLocal`).
func classifyCoordinationEventSourceTx(ctx context.Context, tx pgx.Tx, collection string, eventID uuid.UUID, allowsLocal bool) (string, error) {
	var recordID uuid.UUID
	var version int
	var eventSensitivity string
	err := tx.QueryRow(ctx, `SELECT record_id, version, sensitivity FROM cairn.agent_event WHERE event_id = $1 AND repo = $2`, eventID, collection).
		Scan(&recordID, &version, &eventSensitivity)
	if errors.Is(err, pgx.ErrNoRows) {
		return "unavailable", nil
	}
	if err != nil {
		return "", err
	}

	var lifecycle, recSensitivity, curRepo string
	var payloadDeleted bool
	err = tx.QueryRow(ctx, `SELECT m.lifecycle, m.sensitivity, v_exact.payload_deleted_by IS NOT NULL, v_cur.repo
		FROM cairn.memory_record m
		JOIN cairn.record_version v_exact ON v_exact.record_id = m.record_id AND v_exact.version = $2
		JOIN cairn.record_version v_cur ON v_cur.record_id = m.record_id AND v_cur.version = m.current_version
		WHERE m.record_id = $1 AND v_exact.repo = $3`, recordID, version, collection).
		Scan(&lifecycle, &recSensitivity, &payloadDeleted, &curRepo)
	if errors.Is(err, pgx.ErrNoRows) {
		return "unavailable", nil
	}
	if err != nil {
		return "", err
	}
	if lifecycle == "tombstoned" || payloadDeleted {
		return "forgotten", nil
	}
	if lifecycle == "retracted" || curRepo != collection {
		return "restricted", nil
	}
	if lifecycle != "active" {
		return "unavailable", nil
	}
	if (eventSensitivity == "local" || recSensitivity == "local") && !allowsLocal {
		return "restricted", nil
	}
	return "available", nil
}

func normalizeWaitTerminalReason(state, raw string) *string {
	if state == WaitStateWaiting || raw == "" {
		return nil
	}
	switch raw {
	case "response", "group_closed", "timeout", "cancelled", "restore_fenced":
		r := raw
		return &r
	default:
		u := "unknown"
		return &u
	}
}

func normalizeOutboxRestrictionReason(outboxState, raw string) *string {
	if raw == "" && outboxState != "restricted" {
		return nil
	}
	if raw == "" {
		u := "unknown"
		return &u
	}
	switch raw {
	case "binding_revoked", "source_ineligible", "ceiling_exceeded":
		r := raw
		return &r
	default:
		u := "unknown"
		return &u
	}
}

func (s *Store) projectWorkCoordinationWaitsTx(
	ctx context.Context,
	tx pgx.Tx,
	collection string,
	bindingID, planeItemUUID uuid.UUID,
	associations []WorkAssociation,
	currentGen int64,
	now time.Time,
	limit int,
	cursor *workCoordinationCursor,
	featureStatus string,
) (WorkCoordinationWaitPanel, error) {
	panel := WorkCoordinationWaitPanel{
		FeatureStatus: featureStatus,
		Items:         make([]WorkCoordinationWaitItem, 0),
	}

	depProvenance := make(map[uuid.UUID][]WorkCoordinationAssociationRef)
	depUUIDs := make([]uuid.UUID, 0)
	for _, a := range associations {
		switch a.TargetType {
		case "event":
			if a.TargetEventID != nil {
				id := *a.TargetEventID
				if _, seen := depProvenance[id]; !seen {
					depUUIDs = append(depUUIDs, id)
				}
				depProvenance[id] = append(depProvenance[id], WorkCoordinationAssociationRef{
					AssociationID: a.AssociationID,
					Revision:      a.Revision,
					Basis:         "explicit_event",
				})
			}
		case "response_group":
			if a.TargetGroupID != nil {
				id := *a.TargetGroupID
				if _, seen := depProvenance[id]; !seen {
					depUUIDs = append(depUUIDs, id)
				}
				depProvenance[id] = append(depProvenance[id], WorkCoordinationAssociationRef{
					AssociationID: a.AssociationID,
					Revision:      a.Revision,
					Basis:         "explicit_response_group",
				})
			}
		}
	}
	if len(depUUIDs) == 0 {
		return panel, nil
	}

	var rows pgx.Rows
	var err error
	if cursor != nil {
		rows, err = tx.Query(ctx, `SELECT
			w.wait_uuid, w.owner_agent_uuid, w.creation_execution_uuid,
			w.registration_destination, w.sensitivity_ceiling, w.profile_binding_id,
			w.predicate_version, w.predicate_kind, w.dependency_event_uuid,
			w.deadline, w.generation, w.state, w.reserved_notice_event_uuid,
			w.created_at, w.resolved_at, w.terminal_reason,
			o.state, o.error_reason,
			EXISTS (SELECT 1 FROM cairn.agent_wait_notification_fence f WHERE f.event_uuid = w.reserved_notice_event_uuid),
			EXISTS (SELECT 1 FROM cairn.agent_event ne WHERE ne.event_id = w.reserved_notice_event_uuid AND ne.repo = $1)
			FROM cairn.agent_wait w
			LEFT JOIN cairn.agent_wait_outbox o ON o.wait_uuid = w.wait_uuid
			WHERE w.collection = $1
			  AND w.dependency_event_uuid = ANY($2)
			  AND (date_trunc('microseconds', w.created_at), w.wait_uuid) < ($3, $4)
			ORDER BY w.created_at DESC, w.wait_uuid DESC
			LIMIT $5`, collection, depUUIDs, cursor.CreatedAt, cursor.RowID, limit+1)
	} else {
		rows, err = tx.Query(ctx, `SELECT
			w.wait_uuid, w.owner_agent_uuid, w.creation_execution_uuid,
			w.registration_destination, w.sensitivity_ceiling, w.profile_binding_id,
			w.predicate_version, w.predicate_kind, w.dependency_event_uuid,
			w.deadline, w.generation, w.state, w.reserved_notice_event_uuid,
			w.created_at, w.resolved_at, w.terminal_reason,
			o.state, o.error_reason,
			EXISTS (SELECT 1 FROM cairn.agent_wait_notification_fence f WHERE f.event_uuid = w.reserved_notice_event_uuid),
			EXISTS (SELECT 1 FROM cairn.agent_event ne WHERE ne.event_id = w.reserved_notice_event_uuid AND ne.repo = $1)
			FROM cairn.agent_wait w
			LEFT JOIN cairn.agent_wait_outbox o ON o.wait_uuid = w.wait_uuid
			WHERE w.collection = $1
			  AND w.dependency_event_uuid = ANY($2)
			ORDER BY w.created_at DESC, w.wait_uuid DESC
			LIMIT $3`, collection, depUUIDs, limit+1)
	}
	if err != nil {
		return panel, err
	}

	type rawWaitRow struct {
		waitUUID              uuid.UUID
		ownerAgentUUID        uuid.UUID
		creationExecutionUUID uuid.UUID
		destJSON              []byte
		sensitivityCeiling    string
		profileBindingID      string
		predicateVersion      int
		predicateKind         string
		dependencyEventUUID   uuid.UUID
		deadline              time.Time
		generation            int64
		state                 string
		reservedNoticeUUID    uuid.UUID
		createdAt             time.Time
		resolvedAt            *time.Time
		terminalReason        string
		outboxState           *string
		outboxErrReason       *string
		hasFenceRow           bool
		noticeMaterialized    bool
	}
	rawRows := make([]rawWaitRow, 0, limit+1)
	for rows.Next() {
		var r rawWaitRow
		if err := rows.Scan(
			&r.waitUUID, &r.ownerAgentUUID, &r.creationExecutionUUID,
			&r.destJSON, &r.sensitivityCeiling, &r.profileBindingID,
			&r.predicateVersion, &r.predicateKind, &r.dependencyEventUUID,
			&r.deadline, &r.generation, &r.state, &r.reservedNoticeUUID,
			&r.createdAt, &r.resolvedAt, &r.terminalReason,
			&r.outboxState, &r.outboxErrReason,
			&r.hasFenceRow, &r.noticeMaterialized,
		); err != nil {
			rows.Close()
			return panel, err
		}
		rawRows = append(rawRows, r)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return panel, err
	}
	rows.Close()

	hasMore := len(rawRows) > limit
	if hasMore {
		rawRows = rawRows[:limit]
	}

	agentCache := make(map[uuid.UUID]coordinationAgentState)
	getAgent := func(agentID uuid.UUID) (coordinationAgentState, error) {
		if st, ok := agentCache[agentID]; ok {
			return st, nil
		}
		st, err := loadCoordinationAgentStateTx(ctx, tx, collection, agentID, currentGen, now)
		if err != nil {
			return st, err
		}
		agentCache[agentID] = st
		return st, nil
	}

	for _, r := range rawRows {
		ownerSt, err := getAgent(r.ownerAgentUUID)
		if err != nil {
			return panel, err
		}
		var regDest Destination
		if len(r.destJSON) > 0 {
			_ = json.Unmarshal(r.destJSON, &regDest)
		}
		allowsLocal := (r.sensitivityCeiling == "local") && regDest.AllowLocal &&
			ownerSt.exists && ownerSt.visibility == "local" &&
			(r.profileBindingID == "" || ownerSt.binding == r.profileBindingID)

		depStatus, err := classifyCoordinationEventSourceTx(ctx, tx, collection, r.dependencyEventUUID, allowsLocal)
		if err != nil {
			return panel, err
		}

		oState := "none"
		if r.outboxState != nil && *r.outboxState != "" {
			oState = *r.outboxState
		}
		oErr := ""
		if r.outboxErrReason != nil {
			oErr = *r.outboxErrReason
		}
		var matEventID *uuid.UUID
		var deliv *WorkCoordinationDeliveryView
		if r.noticeMaterialized {
			evID := r.reservedNoticeUUID
			matEventID = &evID
			var d WorkCoordinationDeliveryView
			var code *string
			dErr := tx.QueryRow(ctx, `SELECT delivery_id, state, code, completed_at
				FROM cairn.agent_delivery
				WHERE event_id = $1 AND consumer = $2
				ORDER BY delivery_id LIMIT 1`, evID, "agent/"+r.ownerAgentUUID.String()).
				Scan(&d.DeliveryID, &d.State, &code, &d.CompletedAt)
			if dErr == nil {
				if code != nil {
					d.Code = *code
				}
				deliv = &d
			} else if !errors.Is(dErr, pgx.ErrNoRows) {
				return panel, dErr
			}
		}

		prov := append([]WorkCoordinationAssociationRef(nil), depProvenance[r.dependencyEventUUID]...)
		historical := r.generation != currentGen || !ownerSt.exists || ownerSt.executionID != r.creationExecutionUUID || ownerSt.generation != currentGen

		panel.Items = append(panel.Items, WorkCoordinationWaitItem{
			WaitUUID:              r.waitUUID,
			OwnerAgentUUID:        r.ownerAgentUUID,
			CreationExecutionUUID: r.creationExecutionUUID,
			Predicate: WorkCoordinationWaitPredicate{
				Version:             r.predicateVersion,
				Kind:                r.predicateKind,
				DependencyEventUUID: r.dependencyEventUUID,
			},
			DependencyStatus: depStatus,
			Deadline:         r.deadline,
			CreatedAt:        r.createdAt,
			ResolvedAt:       r.resolvedAt,
			State:            r.state,
			TerminalReason:   normalizeWaitTerminalReason(r.state, r.terminalReason),
			Overdue:          r.state == WaitStateWaiting && !now.Before(r.deadline),
			Notification: WorkCoordinationWaitNotification{
				OutboxState:       oState,
				RestrictionReason: normalizeOutboxRestrictionReason(oState, oErr),
				EventID:           matEventID,
				RestoreFenced:     r.hasFenceRow || oState == "restore_fenced",
				Delivery:          deliv,
			},
			Associations: prov,
			Historical:   historical,
		})
	}

	if hasMore && len(panel.Items) > 0 {
		last := panel.Items[len(panel.Items)-1]
		encoded, err := encodeWorkCoordinationCursor(workCoordinationCursor{
			Panel:             "waits",
			Collection:        collection,
			BindingID:         bindingID,
			PlaneItemUUID:     planeItemUUID,
			Limit:             limit,
			RestoreGeneration: currentGen,
			CreatedAt:         last.CreatedAt,
			RowID:             last.WaitUUID,
		})
		if err != nil {
			return panel, err
		}
		panel.NextCursor = &encoded
	}

	return panel, nil
}

func normalizeNoticeUnavailableReason(state, raw string) *string {
	if raw == "" && state != "unavailable" {
		return nil
	}
	switch raw {
	case "source_unavailable":
		r := raw
		return &r
	default:
		u := "unknown"
		return &u
	}
}

type coordinationSessionAssoc struct {
	associationID uuid.UUID
	revision      int64
	agentID       uuid.UUID
	executionID   uuid.UUID
	isCurrent     bool
}

func (s *Store) projectWorkCoordinationNoticesTx(
	ctx context.Context,
	tx pgx.Tx,
	collection string,
	bindingID, planeItemUUID uuid.UUID,
	associations []WorkAssociation,
	currentGen int64,
	now time.Time,
	limit int,
	cursor *workCoordinationCursor,
	featureStatus string,
) (WorkCoordinationNoticePanel, error) {
	panel := WorkCoordinationNoticePanel{
		FeatureStatus: featureStatus,
		Items:         make([]WorkCoordinationNoticeItem, 0),
		Recipients:    make([]WorkNoticeRecipientSummary, 0),
	}

	var noticesTableExists bool
	if err := tx.QueryRow(ctx, `SELECT to_regclass('cairn.agent_organic_notice') IS NOT NULL`).Scan(&noticesTableExists); err != nil {
		return panel, err
	}

	agentCache := make(map[uuid.UUID]coordinationAgentState)
	getAgent := func(agentID uuid.UUID) (coordinationAgentState, error) {
		if st, ok := agentCache[agentID]; ok {
			return st, nil
		}
		st, err := loadCoordinationAgentStateTx(ctx, tx, collection, agentID, currentGen, now)
		if err != nil {
			return st, err
		}
		agentCache[agentID] = st
		return st, nil
	}

	if !noticesTableExists {
		return panel, failure("SCHEMA_MISMATCH", "organic notice tables are missing; upgrade database schema")
	}

	eventAssocs := make(map[uuid.UUID][]WorkCoordinationAssociationRef)
	eventUUIDs := make([]uuid.UUID, 0)
	sessionAssocs := make([]coordinationSessionAssoc, 0)
	currentRecipientAgents := make([]uuid.UUID, 0)
	seenCurrentAgent := make(map[uuid.UUID]bool)
	admittedPairs := make([]string, 0)
	seenPair := make(map[string]bool)

	for _, a := range associations {
		switch a.TargetType {
		case "event":
			if a.TargetEventID != nil {
				id := *a.TargetEventID
				if _, seen := eventAssocs[id]; !seen {
					eventUUIDs = append(eventUUIDs, id)
				}
				eventAssocs[id] = append(eventAssocs[id], WorkCoordinationAssociationRef{
					AssociationID: a.AssociationID,
					Revision:      a.Revision,
					Basis:         "explicit_event",
				})
			}
		case "session":
			if a.TargetAgentID != nil && a.TargetExecutionID != nil {
				agentID := *a.TargetAgentID
				execID := *a.TargetExecutionID
				st, err := getAgent(agentID)
				if err != nil {
					return panel, err
				}
				isCurrent := st.exists && st.executionID == execID && st.generation == currentGen
				recSummary := WorkNoticeRecipientSummary{
					AssociationID:       a.AssociationID,
					AssociationRevision: a.Revision,
					AgentID:             agentID,
					ExecutionID:         execID,
					Historical:          !isCurrent,
				}
				if !st.exists {
					recSummary.Support = "unavailable"
				} else if !isCurrent {
					recSummary.Support = "historical"
				} else {
					recSummary.Online = st.online
					if st.supported {
						recSummary.Support = "supported"
					} else {
						recSummary.Support = "unsupported"
					}
				}
				panel.Recipients = append(panel.Recipients, recSummary)

				sessionAssocs = append(sessionAssocs, coordinationSessionAssoc{
					associationID: a.AssociationID,
					revision:      a.Revision,
					agentID:       agentID,
					executionID:   execID,
					isCurrent:     isCurrent,
				})
				if isCurrent && !seenCurrentAgent[agentID] {
					seenCurrentAgent[agentID] = true
					currentRecipientAgents = append(currentRecipientAgents, agentID)
				}
				pairKey := agentID.String() + ":" + execID.String()
				if !seenPair[pairKey] {
					seenPair[pairKey] = true
					admittedPairs = append(admittedPairs, pairKey)
				}
			}
		}
	}

	if len(eventUUIDs) == 0 && len(currentRecipientAgents) == 0 && len(admittedPairs) == 0 {
		return panel, nil
	}

	const noticeMembershipClause = `
		n.repo = $1
		AND (
			n.event_id = ANY($2::uuid[])
			OR n.recipient_agent_id = ANY($3::uuid[])
			OR (r.root_id IS NOT NULL AND (r.agent_id::text || ':' || r.execution_id::text) = ANY($4::text[]))
		)`

	if err := tx.QueryRow(ctx, `
		SELECT
			count(*),
			count(*) FILTER (WHERE n.expires_at <= $5),
			min(n.created_at),
			min(n.expires_at)
		FROM cairn.agent_organic_notice n
		LEFT JOIN cairn.agent_organic_notice_turn_root r
		  ON r.root_id = n.admission_turn_root_id AND r.repo = n.repo
		WHERE `+noticeMembershipClause+` AND n.state = 'queued'`,
		collection, eventUUIDs, currentRecipientAgents, admittedPairs, now,
	).Scan(&panel.QueuedCount, &panel.OverdueQueuedCount, &panel.OldestQueuedAt, &panel.EarliestExpiry); err != nil {
		return panel, err
	}

	query := `
		SELECT
			n.notice_id, n.event_id, n.recipient_agent_id, n.recipient_inbox,
			n.reserved_delivery_id, n.state, n.restore_generation,
			n.created_at, n.expires_at, n.admitted_at,
			COALESCE(n.unavailable_reason, ''),
			r.agent_id, r.execution_id, r.restore_generation
		FROM cairn.agent_organic_notice n
		LEFT JOIN cairn.agent_organic_notice_turn_root r
		  ON r.root_id = n.admission_turn_root_id AND r.repo = n.repo
		WHERE ` + noticeMembershipClause
	args := []any{collection, eventUUIDs, currentRecipientAgents, admittedPairs}
	if cursor != nil {
		query += ` AND (date_trunc('microseconds', n.created_at), n.notice_id) < ($5, $6)
			ORDER BY n.created_at DESC, n.notice_id DESC LIMIT $7`
		args = append(args, cursor.CreatedAt, cursor.RowID, limit+1)
	} else {
		query += ` ORDER BY n.created_at DESC, n.notice_id DESC LIMIT $5`
		args = append(args, limit+1)
	}

	type rawNoticeRow struct {
		noticeID           uuid.UUID
		eventID            uuid.UUID
		recipientAgentID   uuid.UUID
		recipientInbox     string
		reservedDeliveryID uuid.UUID
		state              string
		restoreGen         int64
		createdAt          time.Time
		expiresAt          time.Time
		admittedAt         *time.Time
		unavailableReason  string
		rootAgentID        *uuid.UUID
		rootExecutionID    *uuid.UUID
		rootRestoreGen     *int64
	}

	rows, err := tx.Query(ctx, query, args...)
	if err != nil {
		return panel, err
	}
	rawRows := make([]rawNoticeRow, 0, limit+1)
	for rows.Next() {
		var r rawNoticeRow
		if err := rows.Scan(
			&r.noticeID, &r.eventID, &r.recipientAgentID, &r.recipientInbox,
			&r.reservedDeliveryID, &r.state, &r.restoreGen,
			&r.createdAt, &r.expiresAt, &r.admittedAt,
			&r.unavailableReason,
			&r.rootAgentID, &r.rootExecutionID, &r.rootRestoreGen,
		); err != nil {
			rows.Close()
			return panel, err
		}
		rawRows = append(rawRows, r)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return panel, err
	}
	rows.Close()

	hasMore := len(rawRows) > limit
	if hasMore {
		rawRows = rawRows[:limit]
	}

	for _, r := range rawRows {
		recSt, err := getAgent(r.recipientAgentID)
		if err != nil {
			return panel, err
		}
		allowsLocal := recSt.exists && recSt.visibility == "local"
		srcStatus, err := classifyCoordinationEventSourceTx(ctx, tx, collection, r.eventID, allowsLocal)
		if err != nil {
			return panel, err
		}

		var deliv *WorkCoordinationDeliveryView
		expectedConsumer := "agent/" + r.recipientAgentID.String()
		if r.reservedDeliveryID != uuid.Nil && r.recipientInbox == expectedConsumer {
			var d WorkCoordinationDeliveryView
			var code *string
			dErr := tx.QueryRow(ctx, `SELECT delivery_id, state, code, completed_at
				FROM cairn.agent_delivery
				WHERE delivery_id = $1 AND event_id = $2 AND consumer = $3`,
				r.reservedDeliveryID, r.eventID, expectedConsumer,
			).Scan(&d.DeliveryID, &d.State, &code, &d.CompletedAt)
			if dErr == nil {
				if code != nil {
					d.Code = *code
				}
				deliv = &d
			} else if !errors.Is(dErr, pgx.ErrNoRows) {
				return panel, dErr
			}
		}

		prov := make([]WorkCoordinationAssociationRef, 0, 2)
		for _, a := range associations {
			switch a.TargetType {
			case "event":
				if a.TargetEventID != nil && *a.TargetEventID == r.eventID {
					prov = append(prov, WorkCoordinationAssociationRef{
						AssociationID: a.AssociationID,
						Revision:      a.Revision,
						Basis:         "explicit_event",
					})
				}
			case "session":
				if a.TargetAgentID != nil && a.TargetExecutionID != nil {
					if r.rootAgentID != nil && r.rootExecutionID != nil &&
						*r.rootAgentID == *a.TargetAgentID && *r.rootExecutionID == *a.TargetExecutionID {
						prov = append(prov, WorkCoordinationAssociationRef{
							AssociationID: a.AssociationID,
							Revision:      a.Revision,
							Basis:         "admitted_execution",
						})
					} else if r.recipientAgentID == *a.TargetAgentID {
						st, sErr := getAgent(*a.TargetAgentID)
						if sErr != nil {
							return panel, sErr
						}
						if st.exists && st.executionID == *a.TargetExecutionID && st.generation == currentGen {
							prov = append(prov, WorkCoordinationAssociationRef{
								AssociationID: a.AssociationID,
								Revision:      a.Revision,
								Basis:         "linked_recipient_conversation",
							})
						}
					}
				}
			}
		}

		var admittedExecID *uuid.UUID
		if r.rootExecutionID != nil {
			execCopy := *r.rootExecutionID
			admittedExecID = &execCopy
		}

		historical := r.restoreGen != currentGen ||
			r.state == "restore_fenced" ||
			!recSt.exists ||
			recSt.generation != currentGen ||
			(r.rootExecutionID != nil && (recSt.executionID != *r.rootExecutionID || (r.rootRestoreGen != nil && *r.rootRestoreGen != currentGen)))

		panel.Items = append(panel.Items, WorkCoordinationNoticeItem{
			NoticeID:            r.noticeID,
			EventID:             r.eventID,
			RecipientAgentID:    r.recipientAgentID,
			State:               r.state,
			CreatedAt:           r.createdAt,
			ExpiresAt:           r.expiresAt,
			AdmittedAt:          r.admittedAt,
			ExpiryOverdue:       r.state == "queued" && !now.Before(r.expiresAt),
			SourceStatus:        srcStatus,
			UnavailableReason:   normalizeNoticeUnavailableReason(r.state, r.unavailableReason),
			AdmittedExecutionID: admittedExecID,
			Delivery:            deliv,
			Associations:        prov,
			Historical:          historical,
		})
	}

	if hasMore && len(panel.Items) > 0 {
		last := panel.Items[len(panel.Items)-1]
		encoded, err := encodeWorkCoordinationCursor(workCoordinationCursor{
			Panel:             "quiet_notices",
			Collection:        collection,
			BindingID:         bindingID,
			PlaneItemUUID:     planeItemUUID,
			Limit:             limit,
			RestoreGeneration: currentGen,
			CreatedAt:         last.CreatedAt,
			RowID:             last.NoticeID,
		})
		if err != nil {
			return panel, err
		}
		panel.NextCursor = &encoded
	}

	return panel, nil
}
