package core

import (
	"context"
	"time"
)

// Operator actions of the swarm view (CAIRN-833) use verbs the store already
// has: PublishEvent, NextEvent, CompleteEvent, CancelWork and CancelWait, all
// as the local operator principal. This file holds the one new read: what a
// cancel would affect, shown before the owner confirms it.
//
// The types below are the wire contract of the cancel preview. Add a field
// when a reader needs one; do not rename or remove one without the lead.

const SwarmCancelPreviewSchema = "cairn.swarm-cancel-preview/1"

// SwarmCancelPreviewRequest names one delivery of one campaign event.
type SwarmCancelPreviewRequest struct {
	Repo       string `json:"repo,omitempty"`
	Campaign   string `json:"campaign"`
	DeliveryID string `json:"delivery_id"`
}

// SwarmCancelPreview is what cancelling one recipient's delivery affects.
// Cancel is one delivery of one recipient: other recipients of the same
// request, and a reply that already counts in a group, are not affected.
type SwarmCancelPreview struct {
	Schema     string    `json:"schema"`
	ObservedAt time.Time `json:"observed_at"`
	// Delivery is the exact delivery and recipient, as stored now.
	Delivery SwarmDelivery `json:"delivery"`
	// Cancellable is false when the store would refuse the cancel now, for
	// example while the recipient holds a live lease. Refusal says why.
	Cancellable bool          `json:"cancellable"`
	Refusal     *SwarmRefusal `json:"refusal,omitempty"`
	// DirectReplyWaits are the waits registered directly on the request
	// with the request_response predicate. They keep waiting after a
	// cancel, until their own deadline.
	DirectReplyWaits SwarmWaitLookup `json:"direct_reply_waits"`
	// Group is nil when the request has no response group.
	Group *SwarmCancelGroup `json:"group,omitempty"`
	// Siblings are the deliveries of the same event to other recipients.
	Siblings []SwarmDelivery `json:"siblings"`
}

// SwarmWaitLookup tells an empty lookup from an unavailable one. Status is
// found, empty or unavailable; Reason is set when it is unavailable.
type SwarmWaitLookup struct {
	Status string      `json:"status"`
	Reason string      `json:"reason,omitempty"`
	Waits  []SwarmWait `json:"waits"`
}

// SwarmCancelGroup is the response group of a grouped request.
type SwarmCancelGroup struct {
	EventID   string    `json:"event_id"`
	State     string    `json:"state"`
	Policy    string    `json:"policy"`
	Deadline  time.Time `json:"deadline"`
	Expected  int       `json:"expected"`
	Responded int       `json:"responded"`
	// RecipientReplyCounts is set when this recipient's reply already
	// counts in the group. The cancel does not remove it.
	RecipientReplyCounts bool `json:"recipient_reply_counts"`
	// ClosureWaits are the waits on the group's closure.
	ClosureWaits SwarmWaitLookup `json:"closure_waits"`
}

// SwarmRefusal is the store's own refusal code and reason.
type SwarmRefusal struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// GetSwarmCancelPreview reads what a cancel of one delivery would affect.
// CAIRN-833 replaces this stub.
func (s *Store) GetSwarmCancelPreview(ctx context.Context, req SwarmCancelPreviewRequest) (SwarmCancelPreview, error) {
	return SwarmCancelPreview{}, failure("FEATURE_UNAVAILABLE", "the cancel preview is not built yet")
}
