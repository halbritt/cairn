package localapi

import (
	"context"
	"net/http"

	"github.com/halbritt/cairn/core"
)

var agentEventOperations = []OperationSpec{
	bindDestination(OperationSpec{Name: "event-group", SourceFile: "agent_events.go", RemoteAllowed: true, RetryClass: "read"}, (*core.Store).ResponseGroup),
	bindDestination(OperationSpec{Name: "event-groups", SourceFile: "agent_events.go", RemoteAllowed: true, RetryClass: "read"}, (*core.Store).ResponseGroups),
	bindDestination(OperationSpec{Name: "worker-register", SourceFile: "agent_events.go", RemoteAllowed: false, RetryClass: "request-id"}, (*core.Store).RegisterWorkerSlot),
	bindDestination(OperationSpec{Name: "worker-heartbeat", SourceFile: "agent_events.go", RemoteAllowed: false, RetryClass: "idempotent"}, (*core.Store).HeartbeatWorker),
	bindDestination(OperationSpec{Name: "worker-health", SourceFile: "agent_events.go", RemoteAllowed: false, RetryClass: "request-id"}, (*core.Store).ChangeWorkerHealth),
	bindDestination(OperationSpec{Name: "worker-list", SourceFile: "agent_events.go", RemoteAllowed: false, RetryClass: "read"}, (*core.Store).WorkerSlots),
	bindDestination(OperationSpec{Name: "pool-list", SourceFile: "agent_events.go", RemoteAllowed: false, RetryClass: "read"}, (*core.Store).WorkerPools),
	bindDestination(OperationSpec{Name: "wake-claim", SourceFile: "agent_events.go", RemoteAllowed: false, RetryClass: "request-id"}, (*core.Store).ClaimWake),
	bindDestination(OperationSpec{Name: "wake-attempts", SourceFile: "agent_events.go", RemoteAllowed: false, RetryClass: "read"}, (*core.Store).WakeAttempts),
	bindDestination(OperationSpec{Name: "wake-control", SourceFile: "agent_events.go", RemoteAllowed: false, RetryClass: "read"}, (*core.Store).WakeControl),
	bindDestination(OperationSpec{Name: "wake-change", SourceFile: "agent_events.go", RemoteAllowed: false, RetryClass: "request-id"}, (*core.Store).ChangeWake),
	bindOperation(OperationSpec{Name: "event-publish", SourceFile: "agent_events.go", RemoteAllowed: true, RetryClass: "request-id"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req core.PublishEventRequest) (core.AgentEvent, error) {
		if c.remote && (req.Destination.Type == "pool" || req.Pool != nil) {
			return core.AgentEvent{}, &core.Error{Code: "AUTHORITY_DENIED", Message: "remote worker pool placement is unavailable"}
		}
		return c.store.PublishEvent(ctx, req, c.destination)
	}),
	bindDestination(OperationSpec{Name: "event-notice-flush", SourceFile: "agent_events.go", RemoteAllowed: true, RetryClass: "request-id"}, (*core.Store).FlushNotice),
	bindDestination(OperationSpec{Name: "event-next", SourceFile: "agent_events.go", RemoteAllowed: false, RetryClass: "no-key"}, (*core.Store).NextEvent),
	bindDestination(OperationSpec{Name: "event-complete", SourceFile: "agent_events.go", RemoteAllowed: true, RequestBodyLimit: 512 * 1024, RetryClass: "request-id"}, (*core.Store).CompleteEvent),
	bindOperation(OperationSpec{Name: "event-retry", SourceFile: "agent_events.go", RemoteAllowed: false, RetryClass: "lease"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req core.EventLeaseRequest) (core.AgentDelivery, error) {
		return c.store.ChangeEventLease(ctx, req, false, c.destination)
	}),
	bindOperation(OperationSpec{Name: "event-renew", SourceFile: "agent_events.go", RemoteAllowed: true, RetryClass: "lease"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req core.EventLeaseRequest) (core.AgentDelivery, error) {
		return c.store.ChangeEventLease(ctx, req, true, c.destination)
	}),
	bindStore(OperationSpec{Name: "event-subscribe", SourceFile: "agent_events.go", RemoteAllowed: true, RetryClass: "request-id"}, (*core.Store).SetSubscription),
	bindStore(OperationSpec{Name: "event-subscriptions", SourceFile: "agent_events.go", RemoteAllowed: true, RetryClass: "read"}, (*core.Store).EventSubscriptions),
	bindDestination(OperationSpec{Name: "event-list", SourceFile: "agent_events.go", RemoteAllowed: true, RetryClass: "read"}, (*core.Store).Events),
	bindDestination(OperationSpec{Name: "event-watch", SourceFile: "agent_events.go", RemoteAllowed: true, RetryClass: "read"}, (*core.Store).InboxEvents),
	bindDestination(OperationSpec{Name: "event-inspect", SourceFile: "agent_events.go", RemoteAllowed: true, RetryClass: "read"}, (*core.Store).AgentEventStatus),
	bindOperation(OperationSpec{Name: "handoff-request-status", SourceFile: "agent_events.go", RemoteAllowed: false, RetryClass: "read"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req core.HandoffRequestStatusRequest) (core.HandoffRequestStatus, error) {
		out, err := c.store.HandoffRequestStatus(ctx, req, c.destination)
		if err != nil {
			return out, err
		}
		return out, boundEnvelope(out, core.HandoffRequestStatusEnvelopeLimit)
	}),
	bindDestination(OperationSpec{Name: "event-metrics", SourceFile: "agent_events.go", RemoteAllowed: false, RetryClass: "read"}, (*core.Store).AgentEventStats),
}
