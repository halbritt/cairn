package main

import (
	"context"
	"encoding/json"
	"io"
	"os"
	"time"

	"github.com/halbritt/cairn/core"
)

// Each section is a bounded observation. Reissue independently revalidates the
// delivery and its holds in the mutation transaction.
func coordinationReview(ctx context.Context, store *core.Store, input io.Reader) (any, error) {
	var req struct {
		core.EventReviewRequest
		ClosedAfter int64             `json:"closed_after,omitempty"`
		ClosedLimit int               `json:"closed_limit,omitempty"`
		QueuedAfter int64             `json:"queued_after,omitempty"`
		QueuedLimit int               `json:"queued_limit,omitempty"`
		AgentsAfter int64             `json:"agents_after,omitempty"`
		AgentsLimit int               `json:"agents_limit,omitempty"`
		Attention   *attentionOptions `json:"attention,omitempty"`
	}
	if err := decode(input, &req); err != nil {
		return nil, invalid(err.Error())
	}
	deliveries, err := store.ReviewEvents(ctx, req.EventReviewRequest)
	if err != nil {
		return nil, err
	}
	queued, err := store.ReviewQueuedEvents(ctx, req.Repo, req.QueuedAfter, req.QueuedLimit)
	if err != nil {
		return nil, err
	}
	closed, err := store.ReviewClosedPoolRequests(ctx, req.Repo, req.ClosedAfter, req.ClosedLimit)
	if err != nil {
		return nil, err
	}
	dest := core.Destination{Name: "local", AllowLocal: true}
	sessions, err := store.AgentDirectory(ctx, core.AgentDirectoryQuery{Repo: req.Repo, IncludeOffline: true, After: req.AgentsAfter, Limit: req.AgentsLimit}, dest)
	if err != nil {
		return nil, err
	}
	workers, err := store.WorkerSlots(ctx, core.WorkerListRequest{Repo: req.Repo}, dest)
	if err != nil {
		return nil, err
	}
	var attention *attentionResult
	if req.Attention != nil {
		// Project only the public review schema. This keeps the presentation
		// independent of internal store observation types; no payload is cached.
		raw, err := json.Marshal(deliveries.Deliveries)
		if err != nil {
			return nil, err
		}
		var rows []attentionDelivery
		if err = json.Unmarshal(raw, &rows); err != nil {
			return nil, err
		}
		scope := attentionScope(struct {
			Repo, Consumer, State, Delivery, Database, Home string
			Age                                             int64
		}{req.Repo, req.Consumer, req.State, req.DeliveryID, os.Getenv("CAIRN_DATABASE_URL"), os.Getenv("CAIRN_HOME"), req.Attention.OlderThanSeconds})
		result, err := withAttentionCache(*req.Attention, scope, func(previous attentionCache) (attentionResult, attentionCache, error) {
			return summarizeAttention(rows, req.After == 0 && !deliveries.More, *req.Attention, previous, time.Now())
		})
		if err != nil {
			return nil, err
		}
		attention = &result
	}
	return struct {
		ClosedPoolRequests core.ClosedPoolPage     `json:"closed_pool_requests"`
		QueuedRequests     core.EventPage          `json:"queued_requests"`
		Deliveries         core.EventReviewPage    `json:"deliveries"`
		Sessions           core.AgentDirectoryPage `json:"sessions"`
		Workers            []core.WorkerSlot       `json:"workers"`
		Attention          *attentionResult        `json:"attention,omitempty"`
	}{closed, queued, deliveries, sessions, workers.Workers, attention}, nil
}
