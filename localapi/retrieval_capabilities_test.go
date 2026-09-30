package localapi

import (
	"encoding/json"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/buildinfo"
)

func TestVersionRetrievalCapabilitiesAuthenticatedAndLegacyCompatible(t *testing.T) {
	for _, auth := range []bool{false, true} {
		req := httptest.NewRequest("POST", "/v1/version", strings.NewReader("{}"))
		if auth {
			req.Header.Set("Authorization", "Bearer synthetic-protocol-token")
		}
		response := httptest.NewRecorder()
		protocolServer().ServeHTTP(response, req)
		if !auth {
			if response.Code != 401 || strings.Contains(response.Body.String(), "retrieval_capabilities") {
				t.Fatalf("unauthenticated declaration: %d %s", response.Code, response.Body)
			}
			continue
		}
		var reply struct {
			OK   bool            `json:"ok"`
			Data json.RawMessage `json:"data"`
		}
		if err := json.Unmarshal(response.Body.Bytes(), &reply); err != nil || !reply.OK {
			t.Fatalf("version: %v %s", err, response.Body)
		}
		var current map[string]json.RawMessage
		if err := json.Unmarshal(reply.Data, &current); err != nil {
			t.Fatal(err)
		}
		var capabilities map[string]any
		if err := json.Unmarshal(current["retrieval_capabilities"], &capabilities); err != nil {
			t.Fatal(err)
		}
		want := map[string]any{"schema": "cairn.retrieval-capabilities/1", "search_memory_budget_bytes": true, "search_min_pull_bytes": true}
		if !reflect.DeepEqual(capabilities, want) || len(current["retrieval_capabilities"]) > 256 {
			t.Fatalf("declaration: %s", current["retrieval_capabilities"])
		}
		// Exact pre-declaration response shape: ordinary old decoders ignore the new field.
		var legacy struct {
			buildinfo.Info
			Protocol *ProtocolRange `json:"protocol,omitempty"`
		}
		if err := json.Unmarshal(reply.Data, &legacy); err != nil || !reflect.DeepEqual(legacy.Info, buildinfo.Read()) || legacy.Protocol == nil || *legacy.Protocol != Protocol {
			t.Fatalf("legacy decode changed: %+v %v", legacy, err)
		}
	}
}

func TestVersionInfoIgnoresUnknownCapabilityFormats(t *testing.T) {
	// Health/protocol readers must still decode the build if a future declaration
	// changes optional capability formats. Only client_info interprets support.
	raw := []byte(`{"schema":"cairn.build/1","go_version":"go1.25.0","protocol":{"min":1,"current":2},"retrieval_capabilities":{"schema":"future/2","search_min_pull_bytes":"different-format"}}`)
	var info VersionInfo
	if err := json.Unmarshal(raw, &info); err != nil {
		t.Fatal(err)
	}
	protocol, declared := info.ServerProtocol()
	if info.GoVersion != "go1.25.0" || !declared || protocol != Protocol {
		t.Fatalf("optional capability changed identity/protocol: %+v", info)
	}
}
