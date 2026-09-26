package localapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestRemoteIdentityValidation(t *testing.T) {
	digest := sha256.Sum256([]byte("test"))
	base := Identity{TokenSHA256: hex.EncodeToString(digest[:]), Principal: "machine:box/agent", Repo: "/collection", Role: "agent", Destination: "hosted", Remote: true, MachineID: "box"}
	for _, change := range []func(*Identity){func(i *Identity) { i.MachineID = "" }, func(i *Identity) { i.MachineID = "../box" }, func(i *Identity) { i.Destination = "local" }} {
		identity := base
		change(&identity)
		if server, err := New(context.Background(), "not-a-dsn", []Identity{identity}); core.Code(err) != "INVALID_REQUEST" {
			if server != nil {
				server.Close()
			}
			t.Fatalf("invalid remote identity reached store: %v", err)
		}
	}
}

func TestRemoteBoundariesAndMachineDirectory(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	ctx := context.Background()
	admin, err := core.Open(ctx, dsn, core.Channel{Principal: "remote-test", Operator: true})
	if err != nil {
		t.Fatal(err)
	}
	defer admin.Close()
	if err = admin.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	repo := "remote/" + uuid.NewString()
	identities := []Identity{}
	for _, name := range []string{"a", "b", "observer", "local"} {
		digest := sha256.Sum256([]byte("synthetic-" + name))
		identity := Identity{TokenSHA256: hex.EncodeToString(digest[:]), Principal: "machine:" + name + "/agent", Repo: repo, Role: "agent", Destination: "hosted", Remote: true, MachineID: name}
		if name == "observer" {
			identity.Role = "observer"
			identity.Principal = "machine:a/observer"
			identity.MachineID = "a"
		}
		if name == "local" {
			identity.Remote = false
			identity.MachineID = ""
			identity.Principal = "local-remote-test"
		}
		identities = append(identities, identity)
	}
	server, err := New(ctx, dsn, identities)
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	call := func(handler http.Handler, token, operation string, body any) (int, json.RawMessage) {
		t.Helper()
		encoded, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		req := httptest.NewRequest("POST", "/v1/"+operation, bytes.NewReader(encoded))
		req.Header.Set("Authorization", "Bearer synthetic-"+token)
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, req)
		var envelope struct {
			Data json.RawMessage `json:"data"`
		}
		_ = json.Unmarshal(w.Body.Bytes(), &envelope)
		if w.Code == 200 {
			return w.Code, envelope.Data
		}
		return w.Code, w.Body.Bytes()
	}
	remote := server.RemoteHandler()
	for _, handler := range []http.Handler{server, remote} {
		for _, op := range []string{"register-context", "event-next", "event-retry", "wake-claim", "wake-change", "worker-register", "session-tool-capture", "session-tool-stop", "use-report", "future-operation"} {
			if code, body := call(handler, "a", op, map[string]any{}); code != 403 {
				t.Fatalf("remote %s got %d %s", op, code, body)
			}
		}
	}
	if code, _ := call(remote, "local", "version", struct{}{}); code != 403 {
		t.Fatal("local credential admitted on network")
	}
	if code, _ := call(server, "local", "version", struct{}{}); code != 200 {
		t.Fatal("local workflow broken")
	}
	if code, _ := call(remote, "a", "version", struct{}{}); code != 200 {
		t.Fatal("remote version unavailable")
	}
	if code, _ := call(remote, "a", "session-inbox-claim", core.SessionInboxClaim{RequestID: uuid.NewString(), TurnExclusive: true}); code != 403 {
		t.Fatal("remote exclusive claim reached store")
	}
	for _, reason := range []string{"cancel_confirmed", "exclusivity_revoked"} {
		if code, _ := call(remote, "a", "session-inbox-reconcile", core.SessionInboxReconcile{RequestID: uuid.NewString(), Reason: reason}); code != 403 {
			t.Fatal("remote cancellation claim reached store")
		}
	}
	metadata := core.AgentMetadata{Harness: "codex", Project: "test", Workspace: "/same/path", State: "idle", DeliveryMode: "existing-session"}
	register := func(name string) core.AgentInstance {
		code, body := call(remote, name, "agent-register", core.RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "same", NativeSessionID: "same", Metadata: metadata})
		if code != 200 {
			t.Fatalf("register: %d %s", code, body)
		}
		var a core.AgentInstance
		if err := json.Unmarshal(body, &a); err != nil {
			t.Fatal(err)
		}
		return a
	}
	a, b := register("a"), register("b")
	if a.AgentID == b.AgentID || a.MachineID != "a" || b.MachineID != "b" {
		t.Fatalf("machine attribution: %+v %+v", a, b)
	}
	if code, _ := call(remote, "a", "agent-heartbeat", core.AgentSessionRef{AgentID: b.AgentID, ExecutionID: b.ExecutionID}); code == 200 {
		t.Fatal("cross-machine heartbeat accepted")
	}
	if code, _ := call(remote, "observer", "agent-register", core.RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "same", NativeSessionID: "same", Metadata: metadata}); code == 200 {
		t.Fatal("observer registered ordinary agent")
	}
	for _, machine := range []string{"a", "b", "absent"} {
		code, body := call(remote, "a", "agent-directory", map[string]any{"machine_id": machine, "limit": 1})
		if code != 200 {
			t.Fatalf("directory %d %s", code, body)
		}
		var page core.AgentDirectoryPage
		_ = json.Unmarshal(body, &page)
		if machine == "absent" {
			if len(page.Agents) != 0 {
				t.Fatal("unknown machine broadened query")
			}
			continue
		}
		if len(page.Agents) != 1 || page.Agents[0].MachineID != machine || page.More {
			t.Fatalf("machine filter %+v", page)
		}
		code, body = call(remote, "a", "agent-resolve", map[string]any{"machine_id": machine})
		if code != 200 {
			t.Fatalf("resolve %d %s", code, body)
		}
		var resolved core.ResolveAgentResult
		_ = json.Unmarshal(body, &resolved)
		if resolved.State != "unique" || resolved.Candidates[0].MachineID != machine {
			t.Fatalf("resolve %+v", resolved)
		}
	}
	if code, _ := call(remote, "a", "agent-directory", map[string]any{"owner_principals": []string{identities[1].Principal}}); code != 400 {
		t.Fatal("internal owner selector exposed")
	}
	if code, _ := call(remote, "a", "agent-register", map[string]any{"request_id": uuid.NewString(), "binding": "spoof", "native_session_id": "spoof", "metadata": map[string]any{"machine_id": "b"}}); code != 400 {
		t.Fatal("payload machine identity accepted")
	}
}
