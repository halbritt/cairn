package mcpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
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

// Deliberately closed and bounded. Unknown schema/fields, duplicate members,
// missing flags and contradictory declarations remain unknown, not unsupported.
func recognizedRetrievalCapabilities(raw json.RawMessage) (localapi.RetrievalCapabilities, bool) {
	var result localapi.RetrievalCapabilities
	if len(raw) == 0 || len(raw) > 256 {
		return result, false
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	token, err := decoder.Token()
	if err != nil || token != json.Delim('{') {
		return result, false
	}
	seen := map[string]bool{}
	for decoder.More() {
		token, err = decoder.Token()
		if err != nil {
			return result, false
		}
		key, ok := token.(string)
		if !ok || seen[key] {
			return result, false
		}
		seen[key] = true
		var value json.RawMessage
		if err = decoder.Decode(&value); err != nil {
			return result, false
		}
		switch key {
		case "schema":
			if err = json.Unmarshal(value, &result.Schema); err != nil {
				return result, false
			}
		case "search_memory_budget_bytes", "search_min_pull_bytes":
			if !bytes.Equal(value, []byte("true")) && !bytes.Equal(value, []byte("false")) {
				return result, false
			}
			flag := bytes.Equal(value, []byte("true"))
			if key == "search_memory_budget_bytes" {
				result.SearchMemoryBudgetBytes = flag
			} else {
				result.SearchMinPullBytes = flag
			}
		default:
			return result, false
		}
	}
	token, err = decoder.Token()
	if err != nil || token != json.Delim('}') {
		return result, false
	}
	if _, err = decoder.Token(); err != io.EOF {
		return result, false
	}
	return result, len(seen) == 3 && result.Schema == localapi.RetrievalCapabilitiesSchema && (!result.SearchMinPullBytes || result.SearchMemoryBudgetBytes)
}
