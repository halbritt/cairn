package localapi

import (
	"context"
	"net/http"

	"github.com/halbritt/cairn/core"
)

func serveAgentSessions(w http.ResponseWriter, r *http.Request, c client) bool {
	switch r.URL.Path {
	case "/v1/session-inbox-claim":
		serveJSON(w, r, func(ctx context.Context, req core.SessionInboxClaim) (core.SessionInboxResult, error) {
			return c.store.ClaimSessionInbox(ctx, req, c.destination)
		})
	case "/v1/session-inbox-reconcile":
		serveJSON(w, r, func(ctx context.Context, req core.SessionInboxReconcile) (core.SessionInboxAttempt, error) {
			return c.store.ReconcileSessionInbox(ctx, req, c.destination)
		})
	case "/v1/agent-register":
		serveJSON(w, r, func(ctx context.Context, req core.RegisterAgentRequest) (core.AgentInstance, error) {
			return c.store.RegisterAgent(ctx, req, c.destination)
		})
	case "/v1/agent-context":
		serveJSON(w, r, func(ctx context.Context, req core.UpdateAgentRequest) (core.AgentInstance, error) {
			return c.store.UpdateAgent(ctx, req, c.destination)
		})
	case "/v1/agent-heartbeat":
		serveJSON(w, r, func(ctx context.Context, req core.AgentSessionRef) (core.AgentInstance, error) {
			return c.store.HeartbeatAgent(ctx, req, c.destination)
		})
	case "/v1/agent-leave":
		serveJSON(w, r, func(ctx context.Context, req core.AgentSessionRef) (core.AgentInstance, error) {
			return c.store.LeaveAgent(ctx, req, c.destination)
		})
	case "/v1/agent-resolve":
		serveJSON(w, r, func(ctx context.Context, req core.AgentDirectoryQuery) (core.ResolveAgentResult, error) {
			return c.store.ResolveAgent(ctx, req, c.destination)
		})
	case "/v1/agent-directory":
		serveJSON(w, r, func(ctx context.Context, req core.AgentDirectoryQuery) (core.AgentDirectoryPage, error) {
			return c.store.AgentDirectory(ctx, req, c.destination)
		})
	default:
		return false
	}
	return true
}
