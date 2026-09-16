package localapi

import (
	"context"
	"net/http"

	"github.com/halbritt/cairn/core"
)

func serveAgentEvents(w http.ResponseWriter, r *http.Request, c client) bool {
	switch r.URL.Path {
	case "/v1/worker-register":
		serveJSON(w, r, func(ctx context.Context, req core.WorkerRegisterRequest) (core.WorkerSlot, error) {
			return c.store.RegisterWorkerSlot(ctx, req, c.destination)
		})
	case "/v1/worker-heartbeat":
		serveJSON(w, r, func(ctx context.Context, req core.WorkerHeartbeatRequest) (core.WorkerSlot, error) {
			return c.store.HeartbeatWorker(ctx, req, c.destination)
		})
	case "/v1/worker-health":
		serveJSON(w, r, func(ctx context.Context, req core.WorkerHealthRequest) (core.WorkerSlot, error) {
			return c.store.ChangeWorkerHealth(ctx, req, c.destination)
		})
	case "/v1/worker-list":
		serveJSON(w, r, func(ctx context.Context, req core.WorkerListRequest) (core.WorkerList, error) {
			return c.store.WorkerSlots(ctx, req, c.destination)
		})
	case "/v1/pool-list":
		serveJSON(w, r, func(ctx context.Context, req core.WorkerListRequest) (core.WorkerPoolList, error) {
			return c.store.WorkerPools(ctx, req, c.destination)
		})

	case "/v1/wake-claim":
		serveJSON(w, r, func(ctx context.Context, req core.WakeClaimRequest) (core.WakeResult, error) {
			return c.store.ClaimWake(ctx, req, c.destination)
		})
	case "/v1/wake-attempts":
		serveJSON(w, r, func(ctx context.Context, req core.WakeQuery) (core.WakePage, error) {
			return c.store.WakeAttempts(ctx, req, c.destination)
		})
	case "/v1/wake-change":
		serveJSON(w, r, func(ctx context.Context, req core.WakeChangeRequest) (core.WakeAttempt, error) {
			return c.store.ChangeWake(ctx, req, c.destination)
		})
	case "/v1/event-publish":
		serveJSON(w, r, func(ctx context.Context, req core.PublishEventRequest) (core.AgentEvent, error) {
			return c.store.PublishEvent(ctx, req, c.destination)
		})
	case "/v1/event-next":
		serveJSON(w, r, func(ctx context.Context, req core.NextEventRequest) (core.NextEventResult, error) {
			return c.store.NextEvent(ctx, req, c.destination)
		})
	case "/v1/event-complete":
		serveJSON(w, r, func(ctx context.Context, req core.CompleteEventRequest) (core.AgentDelivery, error) {
			return c.store.CompleteEvent(ctx, req, c.destination)
		})
	case "/v1/event-retry", "/v1/event-renew":
		serveJSON(w, r, func(ctx context.Context, req core.EventLeaseRequest) (core.AgentDelivery, error) {
			return c.store.ChangeEventLease(ctx, req, r.URL.Path == "/v1/event-renew", c.destination)
		})
	case "/v1/event-subscribe":
		serveJSON(w, r, c.store.SetSubscription)
	case "/v1/event-subscriptions":
		serveJSON(w, r, c.store.EventSubscriptions)
	case "/v1/event-list":
		serveJSON(w, r, func(ctx context.Context, req core.EventQuery) (core.EventPage, error) {
			return c.store.Events(ctx, req, c.destination)
		})
	case "/v1/event-inspect":
		serveJSON(w, r, func(ctx context.Context, req core.EventStatusRequest) (core.EventStatus, error) {
			return c.store.AgentEventStatus(ctx, req, c.destination)
		})
	case "/v1/event-metrics":
		serveJSON(w, r, func(ctx context.Context, req core.EventQuery) (core.EventStats, error) {
			return c.store.AgentEventStats(ctx, req, c.destination)
		})
	default:
		return false
	}
	return true
}
