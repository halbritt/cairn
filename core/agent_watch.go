package core

import (
	"context"
	"encoding/base64"
	"encoding/json"

	"github.com/jackc/pgx/v5"
)

type InboxWatchRequest struct {
	Repo   string `json:"repo,omitempty"`
	Agent  string `json:"agent,omitempty"`
	Topic  string `json:"topic,omitempty"`
	Cursor string `json:"cursor,omitempty"`
	Limit  int    `json:"limit,omitempty"`
}

// InboxArrival is a read-only observation of a delivery, never a lease claim.
type InboxArrival struct {
	Position   int64      `json:"position"`
	DeliveryID string     `json:"delivery_id"`
	State      string     `json:"state"`
	Event      AgentEvent `json:"event"`
}

type InboxPage struct {
	Deliveries []InboxArrival `json:"deliveries"`
	Cursor     string         `json:"cursor"`
	More       bool           `json:"more"`
}

type inboxCursor struct {
	Schema      string `json:"schema"`
	Repo        string `json:"repo"`
	Consumer    string `json:"consumer"`
	Topic       string `json:"topic"`
	Destination string `json:"destination"`
	AllowLocal  bool   `json:"allow_local"`
	Generation  int64  `json:"generation"`
	Position    int64  `json:"position"`
}

func (s *Store) InboxEvents(ctx context.Context, req InboxWatchRequest, dest Destination) (InboxPage, error) {
	out := InboxPage{Deliveries: []InboxArrival{}}
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	repo, err := s.eventScope(req.Repo, req.Agent)
	if err != nil {
		return out, err
	}
	limit, err := eventLimit(req.Limit)
	if err != nil {
		return out, err
	}
	if req.Topic != "" && !eventName.MatchString(req.Topic) {
		return out, failure("INVALID_REQUEST", "invalid watch topic")
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return out, err
	}
	cursor := inboxCursor{Schema: "cairn.inbox-cursor/1", Repo: repo, Consumer: s.channel.Principal, Topic: req.Topic, Destination: dest.Name, AllowLocal: dest.AllowLocal, Generation: generation}
	if req.Cursor != "" {
		if len(req.Cursor) > 4096 {
			return out, failure("INVALID_REQUEST", "inbox cursor exceeds limit")
		}
		raw, err := base64.RawURLEncoding.DecodeString(req.Cursor)
		var saved inboxCursor
		if err != nil || json.Unmarshal(raw, &saved) != nil || saved.Position < 0 {
			return out, failure("INVALID_REQUEST", "invalid inbox cursor")
		}
		cursor.Position = saved.Position
		if saved != cursor {
			return out, failure("STALE_CURSOR", "cursor collection, inbox, filter, destination or database generation differs; choose an explicit rescan")
		}
	}
	rows, err := tx.Query(ctx, `SELECT d.position,d.delivery_id::text,d.state,`+eventColumns+` FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.consumer=$2 AND (e.sensitivity='shareable' OR $3) AND d.position>$4 AND ($5='' OR (e.destination_type='topic' AND e.destination_name=$5)) ORDER BY d.position LIMIT $6`, repo, s.channel.Principal, dest.AllowLocal, cursor.Position, req.Topic, limit+1)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		if len(out.Deliveries) == limit {
			out.More = true
			break
		}
		var arrival InboxArrival
		fields := append([]any{&arrival.Position, &arrival.DeliveryID, &arrival.State}, eventScanFields(&arrival.Event)...)
		if err = rows.Scan(fields...); err != nil {
			return out, err
		}
		out.Deliveries = append(out.Deliveries, arrival)
		cursor.Position = arrival.Position
	}
	if err = rows.Err(); err != nil {
		return out, err
	}
	raw, err := json.Marshal(cursor)
	if err != nil {
		return out, err
	}
	out.Cursor = base64.RawURLEncoding.EncodeToString(raw)
	return out, nil
}
