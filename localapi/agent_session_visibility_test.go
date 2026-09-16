package localapi_test

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

func TestSessionMetadataRespectsNarrowedProfileDestination(t *testing.T) {
	client, _, repo, _ := authenticatedHost(t, "agent", "local", nil)
	ctx := context.Background()
	registration := core.RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "codex", NativeSessionID: "private-session",
		Metadata: core.AgentMetadata{Harness: "codex", Project: "private-project", Workspace: "/private/workspace", TaskSummary: "LOCAL-SESSION-METADATA-CANARY", State: "busy", DeliveryMode: "existing-session"}}
	var agent core.AgentInstance
	if err := client.Call(ctx, "agent-register", registration, &agent); err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256([]byte("synthetic-observer-token"))
	server, err := localapi.New(ctx, os.Getenv("CAIRN_TEST_DATABASE_URL"), []localapi.Identity{{TokenSHA256: hex.EncodeToString(digest[:]), Principal: "host:test", Repo: repo, Role: "agent", Destination: "hosted"}})
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	body, err := json.Marshal(core.AgentSessionRef{AgentID: agent.AgentID, ExecutionID: agent.ExecutionID})
	if err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest("POST", "/v1/agent-heartbeat", bytes.NewReader(body))
	request.Header.Set("Authorization", "Bearer synthetic-observer-token")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	var envelope struct{ Status string }
	if err := json.Unmarshal(response.Body.Bytes(), &envelope); err != nil {
		t.Fatal(err)
	}
	if envelope.Status != "NOT_FOUND" || bytes.Contains(response.Body.Bytes(), []byte("LOCAL-SESSION-METADATA-CANARY")) {
		t.Fatalf("narrowed profile exposed prior local metadata: %s", response.Body.String())
	}
}
