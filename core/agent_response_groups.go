package core

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
)

type ResponseGroupSpec struct {
	Deadline      time.Time `json:"deadline"`
	PartialPolicy string    `json:"partial_policy"`
}
type ResponseGroupQuery struct {
	EventID string `json:"event_id"`
	After   int64  `json:"after,omitempty"`
	Limit   int    `json:"limit,omitempty"`
}
type ResponseGroupListRequest struct {
	Repo  string `json:"repo,omitempty"`
	State string `json:"state,omitempty"`
	After int64  `json:"after,omitempty"`
	Limit int    `json:"limit,omitempty"`
}
type ResponseGroupSummary struct {
	TaskOutcome   string `json:"reported_task_outcome"`
	EventID       string `json:"event_id"`
	Position      int64  `json:"position"`
	CorrelationID string `json:"correlation_id"`
	ResponseGroupSpec
	State     string     `json:"state"`
	ClosedAt  *time.Time `json:"closed_at,omitempty"`
	Code      string     `json:"code,omitempty"`
	Expected  int        `json:"expected"`
	Responded int        `json:"responded"`
	Overdue   bool       `json:"overdue"`
}
type ResponseGroupMember struct {
	Consumer         string            `json:"consumer"`
	DeliveryID       string            `json:"delivery_id"`
	DeliveryState    string            `json:"delivery_state"`
	DeliveryCode     string            `json:"delivery_code,omitempty"`
	Responded        bool              `json:"responded"`
	ResponseEventID  string            `json:"response_event_id,omitempty"`
	Ref              *RecordVersionRef `json:"ref,omitempty"`
	PayloadAvailable bool              `json:"payload_available"`
}
type ResponseObservation struct {
	EventID     string    `json:"event_id"`
	Position    int64     `json:"position"`
	From        string    `json:"from"`
	Disposition string    `json:"disposition"`
	ObservedAt  time.Time `json:"observed_at"`
}
type ResponseGroupStatus struct {
	ResponseGroupSummary
	Members      []ResponseGroupMember `json:"members"`
	Observations []ResponseObservation `json:"observations"`
	More         bool                  `json:"more"`
	NextAfter    int64                 `json:"next_after,omitempty"`
}
type ResponseGroupPage struct {
	Groups    []ResponseGroupSummary `json:"groups"`
	More      bool                   `json:"more"`
	NextAfter int64                  `json:"next_after,omitempty"`
}

func (g ResponseGroupSpec) validate(req PublishEventRequest) error {
	if req.Kind != "request" || req.Destination.Type == "pool" {
		return failure("INVALID_REQUEST", "response groups require a request with fixed agent/topic recipients")
	}
	if g.Deadline.IsZero() || g.Deadline.Year() < 1 || g.Deadline.Year() > 9999 || (g.PartialPolicy != "all" && g.PartialPolicy != "partial") {
		return failure("INVALID_REQUEST", "response group requires absolute deadline and all or partial policy")
	}
	return nil
}

// The publication transaction already owns the collection insertion lock.
func createResponseGroup(ctx context.Context, tx pgx.Tx, id string, spec ResponseGroupSpec) error {
	var count int
	var future bool
	if err := tx.QueryRow(ctx, `SELECT count(*),$2::timestamptz>clock_timestamp() FROM cairn.agent_delivery WHERE event_id=$1`, id, spec.Deadline).Scan(&count, &future); err != nil {
		return err
	}
	if !future {
		return failure("GROUP_EXPIRED", "response deadline has already passed")
	}
	if count == 0 {
		return failure("GROUP_EMPTY", "response group has no recipients")
	}
	if count > 100 {
		return failure("GROUP_LIMIT", "response group exceeds 100 recipients")
	}
	if _, err := tx.Exec(ctx, `INSERT INTO cairn.agent_response_group(event_id,deadline,partial_policy) VALUES($1,$2,$3)`, id, spec.Deadline, spec.PartialPolicy); err != nil {
		return err
	}
	_, err := tx.Exec(ctx, `INSERT INTO cairn.agent_response_group_member(event_id,consumer,delivery_id) SELECT event_id,consumer,delivery_id FROM cairn.agent_delivery WHERE event_id=$1`, id)
	return err
}

func observeGroupResponse(ctx context.Context, tx pgx.Tx, event AgentEvent) error {
	if event.Kind != "response" || event.CausationID == "" {
		return nil
	}
	if _, err := expireResponseGroups(ctx, tx, event.Repo, event.CausationID); err != nil {
		return err
	}
	var owner, correlation, state string
	var deadline time.Time
	err := tx.QueryRow(ctx, `SELECT e.publisher,e.correlation_id::text,g.state,g.deadline FROM cairn.agent_response_group g JOIN cairn.agent_event e USING(event_id) WHERE g.event_id=$1 FOR UPDATE OF g`, event.CausationID).Scan(&owner, &correlation, &state, &deadline)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil
	}
	if err != nil {
		return err
	}
	var member, responded bool
	if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_response_group_member WHERE event_id=$1 AND consumer=$2),EXISTS(SELECT 1 FROM cairn.agent_response_group_member WHERE event_id=$1 AND consumer=$2 AND response_event_id IS NOT NULL)`, event.CausationID, event.From).Scan(&member, &responded); err != nil {
		return err
	}
	disposition := "responded"
	if !member || event.Destination.Type != "agent" || event.Destination.Name != owner || event.CorrelationID != correlation {
		disposition = "unmatched"
	} else if responded {
		disposition = "duplicate"
	}
	if disposition == "responded" {
		var overdue bool
		if err = tx.QueryRow(ctx, `SELECT $1::timestamptz<=clock_timestamp()`, deadline).Scan(&overdue); err != nil {
			return err
		}
		if overdue || state != "open" {
			disposition = "late"
		}
	}
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.agent_response_observation(response_event_id,group_event_id,disposition) VALUES($1,$2,$3)`, event.EventID, event.CausationID, disposition); err != nil {
		return err
	}
	if disposition == "responded" {
		if _, err = tx.Exec(ctx, `UPDATE cairn.agent_response_group_member SET response_event_id=$3 WHERE event_id=$1 AND consumer=$2`, event.CausationID, event.From, event.EventID); err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `UPDATE cairn.agent_response_group SET state='collected',closed_at=clock_timestamp(),code='all_responded' WHERE event_id=$1 AND state='open' AND NOT EXISTS(SELECT 1 FROM cairn.agent_response_group_member WHERE event_id=$1 AND response_event_id IS NULL)`, event.CausationID)
	}
	return err
}

const responseGroupColumns = `g.event_id::text,e.position,e.correlation_id::text,g.deadline,g.partial_policy,g.state,g.closed_at,g.code,(SELECT count(*) FROM cairn.agent_response_group_member m WHERE m.event_id=g.event_id),(SELECT count(*) FROM cairn.agent_response_group_member m WHERE m.event_id=g.event_id AND m.response_event_id IS NOT NULL),g.state='open' AND g.deadline<=clock_timestamp()`

func scanResponseGroup(row pgx.Row) (ResponseGroupSummary, error) {
	g := ResponseGroupSummary{TaskOutcome: "unknown"}
	err := row.Scan(&g.EventID, &g.Position, &g.CorrelationID, &g.Deadline, &g.PartialPolicy, &g.State, &g.ClosedAt, &g.Code, &g.Expected, &g.Responded, &g.Overdue)
	return g, err
}

func (s *Store) ResponseGroup(ctx context.Context, req ResponseGroupQuery, dest Destination) (ResponseGroupStatus, error) {
	out := ResponseGroupStatus{Members: []ResponseGroupMember{}, Observations: []ResponseObservation{}}
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	if validID(req.EventID) != nil || req.After < 0 {
		return out, failure("INVALID_REQUEST", "valid group event UUID and cursor required")
	}
	limit, err := eventLimit(req.Limit)
	if err != nil {
		return out, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	out.ResponseGroupSummary, err = scanResponseGroup(tx.QueryRow(ctx, `SELECT `+responseGroupColumns+` FROM cairn.agent_response_group g JOIN cairn.agent_event e USING(event_id) WHERE g.event_id=$1 AND e.publisher=$2 AND (e.sensitivity='shareable' OR $3) AND ($4='' OR e.repo=$4)`, req.EventID, s.channel.Principal, dest.AllowLocal, s.channel.Repo))
	if errors.Is(err, pgx.ErrNoRows) {
		return out, failure("NOT_FOUND", "owned response group not found")
	}
	if err != nil {
		return out, err
	}
	rows, err := tx.Query(ctx, `SELECT m.consumer,m.delivery_id::text,d.state,d.code,m.response_event_id IS NOT NULL,
 CASE WHEN v.payload_deleted_by IS NULL AND r.lifecycle='active' AND (e.sensitivity='shareable' OR $2) AND (r.sensitivity='shareable' OR $2) THEN e.event_id::text ELSE '' END,
 CASE WHEN v.payload_deleted_by IS NULL AND r.lifecycle='active' AND (e.sensitivity='shareable' OR $2) AND (r.sensitivity='shareable' OR $2) THEN jsonb_build_object('record_id',e.record_id,'version',e.version) END,
 COALESCE(v.payload_deleted_by IS NULL AND r.lifecycle='active' AND (e.sensitivity='shareable' OR $2) AND (r.sensitivity='shareable' OR $2),false)
 FROM cairn.agent_response_group_member m JOIN cairn.agent_delivery d USING(delivery_id) LEFT JOIN cairn.agent_event e ON e.event_id=m.response_event_id LEFT JOIN cairn.record_version v ON v.record_id=e.record_id AND v.version=e.version LEFT JOIN cairn.memory_record r ON r.record_id=e.record_id WHERE m.event_id=$1 ORDER BY m.consumer`, req.EventID, dest.AllowLocal)
	if err != nil {
		return out, err
	}
	for rows.Next() {
		var m ResponseGroupMember
		if err = rows.Scan(&m.Consumer, &m.DeliveryID, &m.DeliveryState, &m.DeliveryCode, &m.Responded, &m.ResponseEventID, &m.Ref, &m.PayloadAvailable); err != nil {
			rows.Close()
			return out, err
		}
		out.Members = append(out.Members, m)
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return out, err
	}
	rows, err = tx.Query(ctx, `SELECT e.event_id::text,e.position,e.publisher,o.disposition,o.observed_at FROM cairn.agent_response_observation o JOIN cairn.agent_event e ON e.event_id=o.response_event_id WHERE o.group_event_id=$1 AND e.position>$2 AND (e.sensitivity='shareable' OR $3) AND (e.publisher=$5 OR EXISTS(SELECT 1 FROM cairn.agent_delivery d WHERE d.event_id=e.event_id AND d.consumer=$5)) ORDER BY e.position LIMIT $4`, req.EventID, req.After, dest.AllowLocal, limit+1, s.channel.Principal)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		if len(out.Observations) == limit {
			out.More = true
			break
		}
		var o ResponseObservation
		if err = rows.Scan(&o.EventID, &o.Position, &o.From, &o.Disposition, &o.ObservedAt); err != nil {
			return out, err
		}
		out.Observations = append(out.Observations, o)
		out.NextAfter = o.Position
	}
	return out, rows.Err()
}

// Collection locking orders this sweep with publication and response observation.
// Delivery failure is reported on members; only an explicit reply fills a member.
func expireResponseGroups(ctx context.Context, tx pgx.Tx, repo, eventID string) (int64, error) {
	tag, err := tx.Exec(ctx, `WITH due AS (
 SELECT g.event_id FROM cairn.agent_response_group g JOIN cairn.agent_event e USING(event_id)
 WHERE e.repo=$1 AND ($2='' OR g.event_id=NULLIF($2,'')::uuid) AND g.state='open' AND g.deadline<=clock_timestamp()
 ORDER BY g.deadline,g.event_id LIMIT 100 FOR UPDATE OF g SKIP LOCKED)
 UPDATE cairn.agent_response_group g SET state=CASE WHEN g.partial_policy='partial' AND EXISTS(SELECT 1 FROM cairn.agent_response_group_member m WHERE m.event_id=g.event_id AND m.response_event_id IS NOT NULL) THEN 'partial' ELSE 'incomplete' END,closed_at=clock_timestamp(),code='deadline' FROM due WHERE due.event_id=g.event_id`, repo, eventID)
	return tag.RowsAffected(), err
}

func (s *Store) ResponseGroups(ctx context.Context, req ResponseGroupListRequest, dest Destination) (ResponseGroupPage, error) {
	out := ResponseGroupPage{Groups: []ResponseGroupSummary{}, NextAfter: req.After}
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return out, err
	}
	if req.After < 0 {
		return out, failure("INVALID_REQUEST", "invalid group cursor")
	}
	switch req.State {
	case "", "open", "collected", "partial", "incomplete":
	default:
		return out, failure("INVALID_REQUEST", "unknown response group state")
	}
	limit, err := eventLimit(req.Limit)
	if err != nil {
		return out, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT `+responseGroupColumns+` FROM cairn.agent_response_group g JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND e.publisher=$2 AND (e.sensitivity='shareable' OR $3) AND e.position>$4 AND ($5='' OR g.state=$5) ORDER BY e.position LIMIT $6`, repo, s.channel.Principal, dest.AllowLocal, req.After, req.State, limit+1)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		if len(out.Groups) == limit {
			out.More = true
			break
		}
		g, err := scanResponseGroup(rows)
		if err != nil {
			return out, err
		}
		out.Groups = append(out.Groups, g)
		out.NextAfter = g.Position
	}
	return out, rows.Err()
}
