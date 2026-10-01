package localapi

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"testing"
)

func TestClientsCanReadEveryRetainedCohort(t *testing.T) {
	s := observationServer(newFakeClock())
	for i := 0; i < 128; i++ {
		post(s, "/v1/version", localToken, `{}`, map[string][]string{ClientDiagnosticsHeader: {declare(i)}})
	}
	got := decodeClients(t, post(s, "/v1/clients", localToken, `{"limit":128}`, nil))
	if got.Returned != 128 || got.Truncated || got.Partial {
		t.Fatalf("retained rows inaccessible: %+v", got)
	}
	if got.Rows[127].Reported.TransportBuild.Revision != fmt.Sprintf("%040x", 0) {
		t.Fatal("oldest cohort hidden")
	}
}

func TestConfiguredPrincipalQuotasDoNotEvictQuietPrincipals(t *testing.T) {
	s := &Server{clients: map[[32]byte]client{}, obs: testObserver(newFakeClock())}
	var identities []Identity
	for p := 0; p < 32; p++ {
		principal, token := fmt.Sprintf("agent:%d", p), fmt.Sprintf("token-%d", p)
		digest := sha256.Sum256([]byte(token))
		identities = append(identities, Identity{Principal: principal, TokenSHA256: hex.EncodeToString(digest[:]), Repo: "fixture", Role: "agent", Destination: "hosted"})
		s.clients[digest] = client{principal: principal, role: "agent"}
	}
	if err := ValidateIdentities(identities); err != nil {
		t.Fatal(err)
	}
	post(s, "/v1/version", "token-0", `{}`, map[string][]string{ClientDiagnosticsHeader: {declare(0)}})
	for p := 1; p < 32; p++ {
		for i := 0; i < 129; i++ {
			post(s, "/v1/version", fmt.Sprintf("token-%d", p), `{}`, map[string][]string{ClientDiagnosticsHeader: {declare(i)}})
		}
	}
	quiet := decodeClients(t, post(s, "/v1/clients", "token-0", `{}`, nil))
	if quiet.EligibleRows != 1 || quiet.Counters.EvictedCohorts != 0 || quiet.Partial {
		t.Fatalf("other traffic evicted quiet principal: %+v", quiet)
	}
	for i := 1; i < 128; i++ {
		post(s, "/v1/version", "token-0", `{}`, map[string][]string{ClientDiagnosticsHeader: {declare(i)}})
	}
	for p := 0; p < 32; p++ {
		got := decodeClients(t, post(s, "/v1/clients", fmt.Sprintf("token-%d", p), `{"limit":128}`, nil))
		if got.Returned != 128 {
			t.Fatalf("principal %d lost quota: %d", p, got.Returned)
		}
	}
	beforeRows, beforeCounters := len(s.obs.rows), len(s.obs.counters)
	for i := 0; i < 40; i++ {
		if got := post(s, "/v1/version", fmt.Sprintf("unconfigured-%d", i), `{}`, map[string][]string{ClientDiagnosticsHeader: {declare(i)}}); got.Code != http.StatusUnauthorized {
			t.Fatalf("unconfigured admitted: %d", got.Code)
		}
	}
	if len(s.obs.rows) != beforeRows || len(s.obs.counters) != beforeCounters {
		t.Fatal("unconfigured requests allocated observation state")
	}
}

func TestCaseCollidingDiagnosticMembersAreInvalid(t *testing.T) {
	for _, object := range []string{
		`{"schema":"cairn.client-diagnostics/1","surface":"cli","Surface":"mcp","harness":"unknown","transport_build":` + validBuildJSON + `}`,
		`{"schema":"cairn.client-diagnostics/1","surface":"cli","ſurface":"mcp","harness":"unknown","transport_build":` + validBuildJSON + `}`,
		`{"schema":"cairn.client-diagnostics/1","surface":"cli","harness":"unknown","transport_build":` + validBuildJSON + `,"TRANSPORT_BUILD":` + validBuildJSON + `}`,
		`{"schema":"cairn.client-diagnostics/1","surface":"cli","harness":"unknown","transport_build":{"schema":"cairn.build/1","go_version":"go1.25.0","GO_VERSION":"go1.26.0"}}`,
	} {
		if _, state := parseDiagnostics([]string{raw(object)}); state != MetadataInvalid {
			t.Fatalf("ambiguous declaration classified %s", state)
		}
	}
}
