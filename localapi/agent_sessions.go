package localapi

import (
	"context"
	"net/http"

	"github.com/halbritt/cairn/core"
)

var agentSessionOperations = []OperationSpec{
	bindDestination(OperationSpec{Name: "session-inbox-ready", SourceFile: "agent_sessions.go", RemoteAllowed: true, RetryClass: "read"}, (*core.Store).SessionInboxReady),
	bindDestination(OperationSpec{Name: "session-inbox-pending", SourceFile: "agent_sessions.go", RemoteAllowed: true, RetryClass: "read"}, (*core.Store).SessionInboxPending),
	bindOperation(OperationSpec{Name: "session-inbox-claim", SourceFile: "agent_sessions.go", RemoteAllowed: true, RetryClass: "request-id"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req core.SessionInboxClaim) (core.SessionInboxResult, error) {
		if c.remote && req.TurnExclusive {
			return core.SessionInboxResult{}, &core.Error{Code: "AUTHORITY_DENIED", Message: "remote exclusive turns and cancellation are unavailable"}
		}
		return c.store.ClaimSessionInbox(ctx, req, c.destination)
	}),
	bindDestination(OperationSpec{Name: "session-inbox-advance", SourceFile: "agent_sessions.go", RemoteAllowed: true, RetryClass: "request-id"}, (*core.Store).AdvanceSessionInbox),
	bindOperation(OperationSpec{Name: "session-inbox-reconcile", SourceFile: "agent_sessions.go", RemoteAllowed: true, RetryClass: "request-id"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req core.SessionInboxReconcile) (core.SessionInboxAttempt, error) {
		if c.remote && (req.Reason == "cancel_confirmed" || req.Reason == "exclusivity_revoked") {
			return core.SessionInboxAttempt{}, &core.Error{Code: "AUTHORITY_DENIED", Message: "remote cancellation reconciliation is unavailable"}
		}
		return c.store.ReconcileSessionInbox(ctx, req, c.destination)
	}),
	bindDestination(OperationSpec{Name: "session-delivery-observe", SourceFile: "agent_sessions.go", RemoteAllowed: true, RetryClass: "idempotent"}, (*core.Store).ObserveSessionDelivery),
	bindDestination(OperationSpec{Name: "session-inbox-control", SourceFile: "agent_sessions.go", RemoteAllowed: true, RetryClass: "read"}, (*core.Store).SessionInboxControl),
	bindDestination(OperationSpec{Name: "session-tool-capture", SourceFile: "agent_sessions.go", RemoteAllowed: false, RetryClass: "request-id"}, (*core.Store).CaptureSessionTools),
	bindDestination(OperationSpec{Name: "session-tool-stop", SourceFile: "agent_sessions.go", RemoteAllowed: false, RetryClass: "request-id"}, (*core.Store).ReportSessionToolStop),
	bindOperation(OperationSpec{Name: "agent-register", SourceFile: "agent_sessions.go", RemoteAllowed: true, RetryClass: "request-id"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req core.RegisterAgentRequest) (core.AgentInstance, error) {
		if c.remote && req.Metadata.DeliveryMode != "existing-session" {
			return core.AgentInstance{}, &core.Error{Code: "AUTHORITY_DENIED", Message: "remote registration supports existing sessions only"}
		}
		agent, err := c.store.RegisterAgent(ctx, req, c.destination)
		agent.MachineID = c.machineID
		return agent, err
	}),
	bindOperation(OperationSpec{Name: "agent-context", SourceFile: "agent_sessions.go", RemoteAllowed: true, RetryClass: "request-id"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req core.UpdateAgentRequest) (core.AgentInstance, error) {
		if c.remote && req.Metadata.DeliveryMode != "existing-session" {
			return core.AgentInstance{}, &core.Error{Code: "AUTHORITY_DENIED", Message: "remote registration supports existing sessions only"}
		}
		agent, err := c.store.UpdateAgent(ctx, req, c.destination)
		agent.MachineID = c.machineID
		return agent, err
	}),
	bindOperation(OperationSpec{Name: "agent-heartbeat", SourceFile: "agent_sessions.go", RemoteAllowed: true, RetryClass: "idempotent"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req core.AgentSessionRef) (core.AgentInstance, error) {
		agent, err := c.store.HeartbeatAgent(ctx, req, c.destination)
		agent.MachineID = c.machineID
		return agent, err
	}),
	bindOperation(OperationSpec{Name: "agent-leave", SourceFile: "agent_sessions.go", RemoteAllowed: true, RetryClass: "idempotent"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req core.AgentSessionRef) (core.AgentInstance, error) {
		agent, err := c.store.LeaveAgent(ctx, req, c.destination)
		agent.MachineID = c.machineID
		return agent, err
	}),
	bindOperation(OperationSpec{Name: "agent-resolve", SourceFile: "agent_sessions.go", RemoteAllowed: true, RetryClass: "read"}, func(ctx context.Context, s *Server, _ *http.Request, c client, req core.AgentDirectoryQuery) (core.ResolveAgentResult, error) {
		if err := s.machineSelector(&req); err != nil {
			return core.ResolveAgentResult{}, err
		}
		result, err := c.store.ResolveAgent(ctx, req, c.destination)
		s.attributeMachines(result.Candidates)
		return result, err
	}),
	bindOperation(OperationSpec{Name: "agent-directory", SourceFile: "agent_sessions.go", RemoteAllowed: true, RetryClass: "read"}, func(ctx context.Context, s *Server, _ *http.Request, c client, req core.AgentDirectoryQuery) (core.AgentDirectoryPage, error) {
		if err := s.machineSelector(&req); err != nil {
			return core.AgentDirectoryPage{}, err
		}
		page, err := c.store.AgentDirectory(ctx, req, c.destination)
		s.attributeMachines(page.Agents)
		return page, err
	}),
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
