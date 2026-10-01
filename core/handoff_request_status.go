package core

import (
	"context"
	"errors"
	"regexp"

	"github.com/jackc/pgx/v5"
)

const (
	HandoffRequestStatusSchema = "cairn.handoff-request-status/1"
	// HandoffRequestStatusEnvelopeLimit bounds the complete serialized API
	// response envelope, final newline included. The API layer enforces it with
	// the encoder that writes the response.
	HandoffRequestStatusEnvelopeLimit = 16384
	handoffStatusMaxItems             = 16
	handoffStatusMaxDeliveries        = 16
)

var handoffItemID = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

// HandoffItemLink is a caller assertion that a handoff item concerns a request
// event. Cairn does not verify it against the handoff body and never infers
// links from prose.
type HandoffItemLink struct {
	ItemID  string `json:"item_id"`
	EventID string `json:"event_id"`
}

type HandoffRequestStatusRequest struct {
	Handoff RecordVersionRef  `json:"handoff"`
	Items   []HandoffItemLink `json:"items"`
}

// HandoffSource attributes the view to the exact handoff version it was read
// against. The hash describes that version's body; the body is never returned.
type HandoffSource struct {
	RecordID   string `json:"record_id"`
	Version    int    `json:"version"`
	BodySHA256 string `json:"body_sha256"`
}

type HandoffDelivery struct {
	DeliveryID string `json:"delivery_id"`
	State      string `json:"state"`
}

// HandoffResponseGroup is the publisher-only reply collection summary. Its
// state is the stored collection state; it is never task acceptance.
type HandoffResponseGroup struct {
	State     string `json:"state"`
	Expected  int    `json:"expected"`
	Responded int    `json:"responded"`
}

// HandoffRequestObservation is present only for a request this caller may
// inspect. It reports handling, never the outcome of the work.
type HandoffRequestObservation struct {
	Deliveries     []HandoffDelivery     `json:"deliveries"`
	DeliveriesMore bool                  `json:"deliveries_more"`
	ResponseGroup  *HandoffResponseGroup `json:"response_group"`
}

// HandoffItemStatus is one requested link. An unavailable item carries only its
// identifiers: absent, inaccessible and non-request events are not told apart.
type HandoffItemStatus struct {
	ItemID       string `json:"item_id"`
	EventID      string `json:"event_id"`
	Availability string `json:"availability"`
	TaskOutcome  string `json:"task_outcome"`
	*HandoffRequestObservation
}

type HandoffRequestStatus struct {
	Schema  string              `json:"schema"`
	Handoff HandoffSource       `json:"handoff"`
	Links   string              `json:"links"`
	Items   []HandoffItemStatus `json:"items"`
}

func (r HandoffRequestStatusRequest) validate() error {
	if validID(r.Handoff.RecordID) != nil || r.Handoff.Version < 1 || r.Handoff.Version > 2147483647 {
		return failure("INVALID_REQUEST", "handoff requires a canonical record UUID and positive version")
	}
	if len(r.Items) < 1 || len(r.Items) > handoffStatusMaxItems {
		return failure("INVALID_REQUEST", "handoff status requires 1-16 items")
	}
	items, events := map[string]bool{}, map[string]bool{}
	for _, item := range r.Items {
		if !handoffItemID.MatchString(item.ItemID) || validID(item.EventID) != nil {
			return failure("INVALID_REQUEST", "each item requires a 1-64 character item_id of ASCII letters, digits, underscore or hyphen and a canonical event UUID")
		}
		if items[item.ItemID] || events[item.EventID] {
			return failure("INVALID_REQUEST", "item IDs and event IDs must be unique")
		}
		items[item.ItemID], events[item.EventID] = true, true
	}
	return nil
}

// HandoffRequestStatus reports observed handling of requests that the caller
// asserts belong to a saved handoff. It is a read: the handoff is checked for
// current readability, each linked request is resolved with this caller's
// existing event visibility, and nothing is claimed, acknowledged, closed or
// rewritten. The links are caller assertions and `task_outcome` is always
// "unknown": a handled, failed, cancelled or replied request does not say that
// the handoff's work is finished.
func (s *Store) HandoffRequestStatus(ctx context.Context, req HandoffRequestStatusRequest, dest Destination) (HandoffRequestStatus, error) {
	var out HandoffRequestStatus
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	if err := req.validate(); err != nil {
		return out, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	// Any accidental write must fail rather than change coordination state.
	if _, err = tx.Exec(ctx, `SET TRANSACTION READ ONLY`); err != nil {
		return out, err
	}
	source, err := s.readHandoffSource(ctx, tx, req.Handoff, dest)
	if err != nil {
		return out, err
	}
	out = HandoffRequestStatus{Schema: HandoffRequestStatusSchema, Handoff: source, Links: "caller_asserted", Items: make([]HandoffItemStatus, 0, len(req.Items))}
	for _, link := range req.Items {
		item, err := s.handoffItemStatus(ctx, tx, link, dest)
		if err != nil {
			return HandoffRequestStatus{}, err
		}
		out.Items = append(out.Items, item)
	}
	return out, nil
}

// An absent, foreign-collection, destination-hidden or inactive handoff is one
// NOT_FOUND, so the refusal does not describe records the caller cannot read.
func (s *Store) readHandoffSource(ctx context.Context, tx pgx.Tx, ref RecordVersionRef, dest Destination) (HandoffSource, error) {
	var source HandoffSource
	var current int
	var lifecycle, sensitivity, repo string
	var available bool
	err := tx.QueryRow(ctx, `SELECT m.current_version,m.lifecycle,m.sensitivity,v.repo,v.payload_deleted_by IS NULL,
 CASE WHEN v.payload_deleted_by IS NULL THEN encode(sha256(convert_to(v.body,'UTF8')),'hex') ELSE '' END
 FROM cairn.memory_record m JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version
 WHERE m.record_id=$1`, ref.RecordID).Scan(&current, &lifecycle, &sensitivity, &repo, &available, &source.BodySHA256)
	if errors.Is(err, pgx.ErrNoRows) {
		return source, failure("NOT_FOUND", "active handoff not found")
	}
	if err != nil {
		return source, err
	}
	if s.checkRepo(repo) != nil || (!dest.AllowLocal && sensitivity != "shareable") || lifecycle != "active" {
		return source, failure("NOT_FOUND", "active handoff not found")
	}
	if !available {
		return source, failure("PAYLOAD_UNAVAILABLE", "handoff payload was excluded by deletion")
	}
	if current != ref.Version {
		return source, failure("VERSION_CONFLICT", "handoff version is not the current version; read the current handoff and resubmit its links")
	}
	source.RecordID, source.Version = ref.RecordID, current
	return source, nil
}

// The event read mirrors event-status: the publisher or an addressed consumer
// of an event in the caller's collection, within the destination's sensitivity.
func (s *Store) handoffItemStatus(ctx context.Context, tx pgx.Tx, link HandoffItemLink, dest Destination) (HandoffItemStatus, error) {
	item := HandoffItemStatus{ItemID: link.ItemID, EventID: link.EventID, Availability: "unavailable", TaskOutcome: "unknown"}
	var request, publisher bool
	var group *string
	var expected, responded int
	err := tx.QueryRow(ctx, `SELECT e.kind='request',e.publisher=$2,g.state,
 (SELECT count(*) FROM cairn.agent_response_group_member m WHERE m.event_id=e.event_id),
 (SELECT count(*) FROM cairn.agent_response_group_member m WHERE m.event_id=e.event_id AND m.response_event_id IS NOT NULL)
 FROM cairn.agent_event e LEFT JOIN cairn.agent_response_group g ON g.event_id=e.event_id
 WHERE e.event_id=$1 AND `+eventVisible+` AND ($4='' OR e.repo=$4)`, link.EventID, s.channel.Principal, dest.AllowLocal, s.channel.Repo).Scan(&request, &publisher, &group, &expected, &responded)
	if errors.Is(err, pgx.ErrNoRows) || (err == nil && !request) {
		return item, nil
	}
	if err != nil {
		return item, err
	}
	item.Availability = "available"
	item.HandoffRequestObservation = &HandoffRequestObservation{Deliveries: []HandoffDelivery{}}
	if publisher && group != nil {
		item.ResponseGroup = &HandoffResponseGroup{State: *group, Expected: expected, Responded: responded}
	}
	// Only the publisher reads every recipient's delivery; a consumer reads its own.
	rows, err := tx.Query(ctx, `SELECT delivery_id::text,state FROM cairn.agent_delivery WHERE event_id=$1 AND ($2 OR consumer=$3) ORDER BY delivery_id LIMIT $4`, link.EventID, publisher, s.channel.Principal, handoffStatusMaxDeliveries+1)
	if err != nil {
		return item, err
	}
	defer rows.Close()
	for rows.Next() {
		var d HandoffDelivery
		if err = rows.Scan(&d.DeliveryID, &d.State); err != nil {
			return item, err
		}
		if len(item.Deliveries) == handoffStatusMaxDeliveries {
			item.DeliveriesMore = true
			break
		}
		item.Deliveries = append(item.Deliveries, d)
	}
	return item, rows.Err()
}
