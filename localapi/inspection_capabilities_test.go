package localapi

import (
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestVersionInspectionCapabilitiesSeparateAuthenticated(t *testing.T) {
	for _, auth := range []bool{false, true} {
		req := httptest.NewRequest("POST", "/v1/version", strings.NewReader("{}"))
		if auth {
			req.Header.Set("Authorization", "Bearer synthetic-protocol-token")
		}
		response := httptest.NewRecorder()
		protocolServer().ServeHTTP(response, req)
		if !auth {
			if response.Code != 401 || strings.Contains(response.Body.String(), "inspection_capabilities") {
				t.Fatalf("unauthenticated declaration: %d", response.Code)
			}
			continue
		}
		var reply struct {
			Data map[string]json.RawMessage `json:"data"`
		}
		if err := json.Unmarshal(response.Body.Bytes(), &reply); err != nil {
			t.Fatal(err)
		}
		var inspection map[string]any
		if err := json.Unmarshal(reply.Data["inspection_capabilities"], &inspection); err != nil {
			t.Fatalf("missing inspection declaration: %v", err)
		}
		if len(inspection) != 2 || inspection["schema"] != "cairn.inspection-capabilities/1" || inspection["first_fitting_whole_v1"] != true {
			t.Fatalf("inspection declaration: %v", inspection)
		}
		if _, ok := ParseRetrievalCapabilities(reply.Data["retrieval_capabilities"]); !ok {
			t.Fatal("closed old retrieval reader broken")
		}
		raw, _ := json.Marshal(reply.Data)
		var legacy VersionInfo
		if err := json.Unmarshal(raw, &legacy); err != nil || legacy.Protocol == nil || *legacy.Protocol != Protocol {
			t.Fatalf("legacy version broken: %v %+v", err, legacy)
		}
	}
}

func TestInspectionCapabilitiesStrictParserAndVersionReader(t *testing.T) {
	valid := `{"schema":"cairn.inspection-capabilities/1","first_fitting_whole_v1":true}`
	for _, raw := range []string{valid + `{}`, valid + ` trailing`, strings.TrimSuffix(valid, "}"), `{"schema":"cairn.inspection-capabilities/1","first_fitting_whole_v1": true, "first_fitting_whole_v1": true}`, `{"schema":"cairn.inspection-capabilities/1","first_fitting_whole_v1":true,"unknown":false}`} {
		if _, ok := ParseInspectionCapabilities([]byte(raw)); ok {
			t.Fatalf("accepted malformed declaration %q", raw)
		}
	}
	for _, raw := range []string{valid, `{"first_fitting_whole_v1": false, "schema":"cairn.inspection-capabilities/1"}`} {
		if _, ok := ParseInspectionCapabilities([]byte(raw)); !ok {
			t.Fatalf("rejected valid declaration %q", raw)
		}
	}
	for _, capability := range []string{`null`, `[]`, `"future-shape"`, `{"schema":"future/2","private":"canary"}`} {
		raw := []byte(`{"schema":"cairn.build/1","go_version":"go1.25.0","protocol":{"min":1,"current":2},"inspection_capabilities":` + capability + `}`)
		var info VersionInfo
		if err := json.Unmarshal(raw, &info); err != nil {
			t.Fatal(err)
		}
		protocol, declared := info.ServerProtocol()
		if info.GoVersion != "go1.25.0" || !declared || protocol != Protocol {
			t.Fatalf("optional format changed version: %+v", info)
		}
	}
}
