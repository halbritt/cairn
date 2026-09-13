package localapi

import (
	"crypto/sha256"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestBuildIdentityRequiresAuthenticationWithoutStoreAccess(t *testing.T) {
	// No store is attached: version inspection must not touch repository data.
	server := &Server{clients: map[[32]byte]client{sha256.Sum256([]byte("synthetic-version-token")): {}}}
	for _, test := range []struct {
		method, token, body string
		status              int
	}{
		{"POST", "wrong", "{}", 401},
		{"POST", "synthetic-version-token", "{}", 200},
		{"GET", "synthetic-version-token", "", 405},
		{"POST", "synthetic-version-token", `{"vcs_revision":"caller-chosen"}`, 400},
	} {
		request := httptest.NewRequest(test.method, "/v1/version", strings.NewReader(test.body))
		request.Header.Set("Authorization", "Bearer "+test.token)
		response := httptest.NewRecorder()
		server.ServeHTTP(response, request)
		if response.Code != test.status {
			t.Fatalf("version status: %d want %d: %s", response.Code, test.status, response.Body)
		}
		if response.Code == 200 {
			var value struct {
				Data map[string]any `json:"data"`
			}
			if err := json.Unmarshal(response.Body.Bytes(), &value); err != nil {
				t.Fatal(err)
			}
			if value.Data["schema"] != "cairn.build/1" || value.Data["go_version"] == nil {
				t.Fatalf("missing running executable identity: %s", response.Body)
			}
		}
	}
}
