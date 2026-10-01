package mcpapi

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/buildinfo"
	"github.com/halbritt/cairn/localapi"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type clientInfoComponent struct {
	Build                   *buildinfo.Info `json:"build,omitempty"`
	SearchMemoryBudgetBytes string          `json:"search_memory_budget_bytes"`
	SearchMinPullBytes      string          `json:"search_min_pull_bytes"`
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
		Facade: clientInfoComponent{Build: &t.facadeBuild, SearchMemoryBudgetBytes: "supported", SearchMinPullBytes: "supported", SupportBasis: "registered_search_contract"},
		API:    clientInfoAPI{clientInfoComponent: clientInfoComponent{SearchMemoryBudgetBytes: "unknown", SearchMinPullBytes: "unknown", SupportBasis: "no_api_capability_contract"}, State: "unavailable"}}
	probe, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	// Keep capability decoding separate: malformed optional declarations must
	// not discard a valid build identity or expose untrusted response strings.
	var version struct {
		buildinfo.Info
		PreviewCapabilities   json.RawMessage `json:"preview_capabilities"`
		RetrievalCapabilities json.RawMessage `json:"retrieval_capabilities"`
	}
	var err error
	if t.client == nil {
		result.API.Diagnostic = "API_NOT_CONNECTED"
	} else if err = t.client.Call(probe, "version", struct{}{}, &version); err != nil {
		result.API.Diagnostic = clientInfoError(probe, err)
	} else {
		t.previewChoice.Observe(version.PreviewCapabilities)
		if !validDiagnosticBuild(version.Info) {
			result.API.State, result.API.Diagnostic = "invalid_response", "INVALID_BUILD_IDENTITY"
		} else {
			result.API.State = "available"
			result.API.Build = &version.Info
			if capabilities, ok := recognizedRetrievalCapabilities(version.RetrievalCapabilities); ok {
				support := func(value bool) string {
					if value {
						return "supported"
					}
					return "unsupported"
				}
				result.API.SearchMemoryBudgetBytes = support(capabilities.SearchMemoryBudgetBytes)
				result.API.SearchMinPullBytes = support(capabilities.SearchMinPullBytes)
				result.API.SupportBasis = "api_retrieval_capabilities_v1"
			}
		}
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

// API metadata is a declaration, not trusted arbitrary text to relay to a model.
// Refuse malformed identities as a whole, without reflecting their contents.
func validDiagnosticBuild(b buildinfo.Info) bool { return b.Valid() }

// Deliberately closed and bounded; see localapi.ParseRetrievalCapabilities.
func recognizedRetrievalCapabilities(raw json.RawMessage) (localapi.RetrievalCapabilities, bool) {
	return localapi.ParseRetrievalCapabilities(raw)
}
