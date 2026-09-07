package localapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"net/http/httptest"
	"os"
	"testing"
)

func TestAuthenticatedChannelOwnsIdentityAndScope(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	ctx := context.Background()
	admin, err := core.Open(ctx, dsn, core.Channel{Principal: "local-api-test", Operator: true})
	if err != nil {
		t.Fatal(err)
	}
	defer admin.Close()
	if err = admin.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	token := "synthetic-agent-token"
	digest := sha256.Sum256([]byte(token))
	repo := uuid.NewString()
	server, err := New(ctx, dsn, []Identity{{TokenSHA256: hex.EncodeToString(digest[:]), Principal: "agent:api", Repo: repo, Role: "agent", Destination: "hosted"}})
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	call := func(path, auth string, body any) (int, map[string]any) {
		t.Helper()
		encoded, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		req := httptest.NewRequest("POST", path, bytes.NewReader(encoded))
		req.Header.Set("Authorization", "Bearer "+auth)
		w := httptest.NewRecorder()
		server.ServeHTTP(w, req)
		var result map[string]any
		if err = json.Unmarshal(w.Body.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		return w.Code, result
	}
	draft := core.Draft{Kind: "note", Body: "API lesson", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, ClaimType: "self"}
	status, _ := call("/v1/create", "wrong", core.CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if status != 401 {
		t.Fatal("unauthenticated write")
	}
	status, result := call("/v1/create", token, core.CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if status != 200 {
		t.Fatalf("create: %v", result)
	}
	record := result["data"].(map[string]any)
	if record["observed_writer"] != "agent:api" || record["witness"] != "testimony" {
		t.Fatal("payload selected identity")
	}
	status, _ = call("/v1/get", token, map[string]any{"record_id": record["record_id"]})
	if status != 404 {
		t.Fatal("hosted profile read local content outside compiler")
	}
	status, _ = call("/v1/use-report", token, core.UseReportRequest{Repo: repo, Limit: 100})
	if status != 403 {
		t.Fatal("hosted profile read protected usage details")
	}
	draft.Scope.Repo = "outside"
	status, _ = call("/v1/create", token, core.CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if status != 403 {
		t.Fatal("cross-repo create")
	}
	status, _ = call("/v1/spawn", token, core.SpawnRequest{RequestID: uuid.NewString(), AttemptID: uuid.NewString(), Dispatcher: "spoof", Delegate: "spoof", Scope: core.Scope{Repo: repo, TaskID: "t", RunID: "r"}})
	if status != 403 {
		t.Fatal("agent asserted service observation")
	}
	status, _ = call("/v1/bootstrap", token, map[string]any{})
	if status != 404 {
		t.Fatal("operator path exposed")
	}
	status, _ = call("/v1/compile", token, map[string]any{"principal": "operator", "request_id": uuid.NewString()})
	if status != 400 {
		t.Fatal("unknown caller field accepted")
	}
	status, result = call("/v1/compile", token, core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "t", RunID: "r"}, Purpose: "context", AvailableTokens: 32000})
	if status != 200 {
		t.Fatal(result)
	}
	encoded, _ := json.Marshal(result)
	if bytes.Contains(encoded, []byte("API lesson")) {
		t.Fatal("hosted profile leaked local content")
	}
}
