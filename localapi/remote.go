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

// Every current route is classified in the declarative operation catalog;
// unknown future routes remain unavailable remotely.
var remoteOperations = map[string]bool{}
