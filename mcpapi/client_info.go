package mcpapi

import (
	"context"
	"errors"
	"regexp"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/buildinfo"
	"github.com/halbritt/cairn/localapi"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type clientInfoComponent struct {
	Build                   *buildinfo.Info `json:"build,omitempty"`
	SearchMemoryBudgetBytes string          `json:"search_memory_budget_bytes"`
	SupportBasis            string          `json:"support_basis"`
}
type clientInfoAPI struct {
	clientInfoComponent
	State      string `json:"state"`
	Diagnostic string `json:"diagnostic,omitempty"`
}
type clientInfoResult struct {
	Schema string              `json:"schema"`
	Facade clientInfoComponent `json:"facade"`
	API    clientInfoAPI       `json:"api"`
}

// This declaration accompanies the registered search handler; invocation tests
// cover both its exposed argument and forwarding to the authenticated API.
func (t memoryTools) clientInfo(ctx context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, any, error) {
	result := clientInfoResult{Schema: "cairn.client-info/1",
		Facade: clientInfoComponent{Build: &t.facadeBuild, SearchMemoryBudgetBytes: "supported", SupportBasis: "registered_search_contract"},
		API:    clientInfoAPI{clientInfoComponent: clientInfoComponent{SearchMemoryBudgetBytes: "unknown", SupportBasis: "no_api_capability_contract"}, State: "unavailable"}}
	probe, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	var version localapi.VersionInfo
	var err error
	if t.client == nil {
		result.API.Diagnostic = "API_NOT_CONNECTED"
	} else if err = t.client.Call(probe, "version", struct{}{}, &version); err != nil {
		result.API.Diagnostic = clientInfoError(probe, err)
	} else if !validDiagnosticBuild(version.Info) {
		result.API.State, result.API.Diagnostic = "invalid_response", "INVALID_BUILD_IDENTITY"
	} else {
		result.API.State = "available"
		result.API.Build = &version.Info
		// /version currently has no capability declaration contract. A revision,
		// protocol range or unrecognized extra field cannot establish support.
	}
	return toolResult(result, nil, min(t.config.AvailableTokens, 4096))
}

func clientInfoError(ctx context.Context, err error) string {
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return "API_TIMEOUT"
	}
	if errors.Is(ctx.Err(), context.Canceled) {
		return "API_CANCELED"
	}
	switch core.Code(err) {
	case "API_CONNECTION_FAILED":
		return "API_CONNECTION_FAILED"
	case "AUTHORITY_DENIED":
		return "API_ACCESS_DENIED"
	case "NOT_FOUND":
		return "VERSION_UNAVAILABLE"
	case "PROTOCOL_UNSUPPORTED":
		return "PROTOCOL_UNSUPPORTED"
	default:
		return "API_PROBE_FAILED"
	}
}

var diagnosticGoVersion = regexp.MustCompile(`^(devel )?go[0-9][A-Za-z0-9._+-]*( [A-Za-z0-9:+ -]+)?$`)
var diagnosticModuleVersion = regexp.MustCompile(`^(\(devel\)|v[0-9][A-Za-z0-9.+-]*)$`)
var diagnosticRevision = regexp.MustCompile(`^([0-9a-f]{40}|[0-9a-f]{64})$`)
var diagnosticSVNRevision = regexp.MustCompile(`^[0-9]{1,20}$`)

// API metadata is a declaration, not trusted arbitrary text to relay to a model.
// Refuse malformed identities as a whole, without reflecting their contents.
func validDiagnosticBuild(b buildinfo.Info) bool {
	if b.Schema != "cairn.build/1" || len(b.GoVersion) > 128 || !diagnosticGoVersion.MatchString(b.GoVersion) {
		return false
	}
	if b.ModuleVersion != "" && (len(b.ModuleVersion) > 128 || !diagnosticModuleVersion.MatchString(b.ModuleVersion)) {
		return false
	}
	if b.VCS != "" && b.VCS != "git" && b.VCS != "hg" && b.VCS != "svn" && b.VCS != "bzr" && b.VCS != "fossil" {
		return false
	}
	if b.Revision != "" {
		valid := diagnosticRevision.MatchString(b.Revision)
		if b.VCS == "svn" {
			valid = diagnosticSVNRevision.MatchString(b.Revision)
		}
		if !valid {
			return false
		}
	}
	if b.Time != "" {
		if len(b.Time) > 40 {
			return false
		}
		if _, err := time.Parse(time.RFC3339, b.Time); err != nil {
			return false
		}
	}
	return true
}
