package localapi

import (
	"context"
	"net/http"

	"github.com/halbritt/cairn/core"
)

func (s *Server) serveAgentSessions(w http.ResponseWriter, r *http.Request, c client) bool {
	switch r.URL.Path {
	case "/v1/session-inbox-ready":
		serveJSON(w, r, func(ctx context.Context, req core.AgentSessionRef) (core.SessionInboxReadiness, error) {
			return c.store.SessionInboxReady(ctx, req, c.destination)
		})
	case "/v1/session-inbox-claim":
		serveJSON(w, r, func(ctx context.Context, req core.SessionInboxClaim) (core.SessionInboxResult, error) {
			if c.remote && req.TurnExclusive {
				return core.SessionInboxResult{}, &core.Error{Code: "AUTHORITY_DENIED", Message: "remote exclusive turns and cancellation are unavailable"}
			}
			return c.store.ClaimSessionInbox(ctx, req, c.destination)
		})
	case "/v1/session-inbox-reconcile":
		serveJSON(w, r, func(ctx context.Context, req core.SessionInboxReconcile) (core.SessionInboxAttempt, error) {
			if c.remote && (req.Reason == "cancel_confirmed" || req.Reason == "exclusivity_revoked") {
				return core.SessionInboxAttempt{}, &core.Error{Code: "AUTHORITY_DENIED", Message: "remote cancellation reconciliation is unavailable"}
			}
			return c.store.ReconcileSessionInbox(ctx, req, c.destination)
		})
	case "/v1/session-delivery-observe":
		serveJSON(w, r, func(ctx context.Context, req core.SessionDeliveryObservation) (core.SessionDeliveryObservationResult, error) {
			return c.store.ObserveSessionDelivery(ctx, req, c.destination)
		})
	case "/v1/session-inbox-control":
		serveJSON(w, r, func(ctx context.Context, req core.AgentSessionRef) (core.SessionInboxControlStatus, error) {
			return c.store.SessionInboxControl(ctx, req, c.destination)
		})
	case "/v1/session-tool-capture":
		serveJSON(w, r, func(ctx context.Context, req core.SessionToolCapture) (core.SessionInboxAttempt, error) {
			return c.store.CaptureSessionTools(ctx, req, c.destination)
		})
	case "/v1/session-tool-stop":
		serveJSON(w, r, func(ctx context.Context, req core.SessionToolStopReport) (core.SessionInboxAttempt, error) {
			return c.store.ReportSessionToolStop(ctx, req, c.destination)
		})
	case "/v1/agent-register":
		serveJSON(w, r, func(ctx context.Context, req core.RegisterAgentRequest) (core.AgentInstance, error) {
			if c.remote && req.Metadata.DeliveryMode != "existing-session" {
				return core.AgentInstance{}, &core.Error{Code: "AUTHORITY_DENIED", Message: "remote registration supports existing sessions only"}
			}
			agent, err := c.store.RegisterAgent(ctx, req, c.destination)
			agent.MachineID = c.machineID
			return agent, err
		})
	case "/v1/agent-context":
		serveJSON(w, r, func(ctx context.Context, req core.UpdateAgentRequest) (core.AgentInstance, error) {
			if c.remote && req.Metadata.DeliveryMode != "existing-session" {
				return core.AgentInstance{}, &core.Error{Code: "AUTHORITY_DENIED", Message: "remote registration supports existing sessions only"}
			}
			agent, err := c.store.UpdateAgent(ctx, req, c.destination)
			agent.MachineID = c.machineID
			return agent, err
		})
	case "/v1/agent-heartbeat":
		serveJSON(w, r, func(ctx context.Context, req core.AgentSessionRef) (core.AgentInstance, error) {
			agent, err := c.store.HeartbeatAgent(ctx, req, c.destination)
			agent.MachineID = c.machineID
			return agent, err
		})
	case "/v1/agent-leave":
		serveJSON(w, r, func(ctx context.Context, req core.AgentSessionRef) (core.AgentInstance, error) {
			agent, err := c.store.LeaveAgent(ctx, req, c.destination)
			agent.MachineID = c.machineID
			return agent, err
		})
	case "/v1/agent-resolve":
		serveJSON(w, r, func(ctx context.Context, req core.AgentDirectoryQuery) (core.ResolveAgentResult, error) {
			if err := s.machineSelector(&req); err != nil {
				return core.ResolveAgentResult{}, err
			}
			result, err := c.store.ResolveAgent(ctx, req, c.destination)
			s.attributeMachines(result.Candidates)
			return result, err
		})
	case "/v1/agent-directory":
		serveJSON(w, r, func(ctx context.Context, req core.AgentDirectoryQuery) (core.AgentDirectoryPage, error) {
			if err := s.machineSelector(&req); err != nil {
				return core.AgentDirectoryPage{}, err
			}
			page, err := c.store.AgentDirectory(ctx, req, c.destination)
			s.attributeMachines(page.Agents)
			return page, err
		})
	default:
		return false
	}
	return true
}

func (s *Server) machineSelector(req *core.AgentDirectoryQuery) error {
	if req.MachineID == "" {
		return nil
	}
	if !machineIDPattern.MatchString(req.MachineID) {
		return &core.Error{Code: "INVALID_REQUEST", Message: "invalid machine ID selector"}
	}
	req.OwnerPrincipals = []string{}
	for principal, machine := range s.machines {
		if machine == req.MachineID {
			req.OwnerPrincipals = append(req.OwnerPrincipals, principal)
		}
	}
	req.MachineID = ""
	return nil
}

func (s *Server) attributeMachines(agents []core.AgentInstance) {
	for i := range agents {
		agents[i].MachineID = s.machines[agents[i].ProfileOwner]
	}
}
