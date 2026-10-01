package localapi

import (
	"encoding/hex"
	"net/http"
	"regexp"
	"strings"

	"github.com/halbritt/cairn/core"
)

const maxAPIIdentities = 32

var machineIDPattern = regexp.MustCompile(`^[a-z][a-z0-9-]{0,62}$`)

// ValidateIdentities is shared by provisioning and server startup. Remote
// principals have a reserved machine namespace; a profile cannot borrow a local
// inbox identity by marking itself remote or putting a machine ID in a payload.
func ValidateIdentities(identities []Identity) error {
	invalid := func() error {
		return &core.Error{Code: "INVALID_REQUEST", Message: "invalid or duplicate API identity configuration"}
	}
	if len(identities) == 0 || len(identities) > maxAPIIdentities {
		return invalid()
	}
	principals := map[string]bool{}
	tokens := map[[32]byte]bool{}
	for _, identity := range identities {
		digest, err := hex.DecodeString(identity.TokenSHA256)
		if err != nil || len(digest) != 32 || strings.TrimSpace(identity.Principal) == "" || len(identity.Principal) > 256 || identity.Repo == "" || identity.Repo == "*" || len(identity.Repo) > 256 || principals[identity.Principal] {
			return invalid()
		}
		if identity.Role != "agent" && identity.Role != "observer" {
			return invalid()
		}
		if identity.Destination != "hosted" && identity.Destination != "local" {
			return invalid()
		}
		if identity.Remote {
			if !machineIDPattern.MatchString(identity.MachineID) || identity.Destination != "hosted" || identity.Principal != "machine:"+identity.MachineID+"/"+identity.Role {
				return invalid()
			}
		} else if identity.MachineID != "" || strings.HasPrefix(identity.Principal, "machine:") {
			return invalid()
		}
		key := [32]byte(digest)
		if tokens[key] {
			return invalid()
		}
		tokens[key] = true
		principals[identity.Principal] = true
	}
	return nil
}

// RemoteHandler admits only profiles explicitly provisioned for another
// machine. ServeHTTP still applies the remote allowlist on the private socket,
// so reaching that socket cannot bypass restrictions on remote credentials.
func (s *Server) RemoteHandler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { s.serveHTTP(w, r, true) })
}

// SetLocalMachineID supplies directory attribution from server startup config,
// never agent metadata. Call only before the server begins accepting requests.
func (s *Server) SetLocalMachineID(machine string) error {
	if !machineIDPattern.MatchString(machine) {
		return &core.Error{Code: "INVALID_REQUEST", Message: "machine ID must match [a-z][a-z0-9-]{0,62}"}
	}
	for _, c := range s.clients {
		if c.remote && c.machineID == machine {
			return &core.Error{Code: "INVALID_REQUEST", Message: "local machine ID collides with a remote machine"}
		}
	}
	for key, c := range s.clients {
		if !c.remote {
			c.machineID = machine
			s.clients[key] = c
			s.machines[c.principal] = machine
		}
	}
	return nil
}

// Every current route is classified; unknown future routes remain unavailable
// remotely. The route inventory test requires an explicit decision for additions.
var remoteOperations = map[string]bool{
	"version": true, "clients": true,
	"create": true, "edit": true, "revise": true, "append": true, "replace": true,
	"cite": true, "delete": true, "supersede": true,
	"index": true, "expand": true, "expand-evidence": true, "compile": true, "recompile": true,
	"evidence": true, "history": true, "get": true, "usage": true, "usage-coverage": true, "recall-observation": true,
	"agent-register": true, "agent-context": true, "agent-heartbeat": true, "agent-leave": true, "agent-resolve": true, "agent-directory": true,
	"session-inbox-ready": true, "session-inbox-pending": true, "session-inbox-claim": true, "session-inbox-reconcile": true, "session-inbox-control": true, "session-delivery-observe": true,
	"event-publish": true, "event-complete": true, "event-renew": true, "event-subscribe": true, "event-subscriptions": true, "event-list": true, "event-watch": true, "event-inspect": true, "event-group": true, "event-groups": true,
	"register-context": false, "event-next": false, "event-retry": false,
	"worker-register": false, "worker-heartbeat": false, "worker-health": false, "worker-list": false, "pool-list": false,
	"wake-claim": false, "wake-attempts": false, "wake-control": false, "wake-change": false,
	"session-tool-capture": false, "session-tool-stop": false,
	"preview-retract": false, "supersession": false, "evidence-impact": false, "check-evidence": false,
	"run-index": false, "run-package": false, "spawn": false, "terminal": false, "task-state": false, "run-status": false, "claim-run": false, "bind-run": false, "link-run-retrieval": false, "delivery": false, "outcome": false,
	"assess-run": false, "assessments": false, "assessments-page": false,
	"refusal": false, "use-report": false, "run-report": false, "conflicts": false, "conflict": false, "event-metrics": false,
	"handoff-request-status": false,
}
