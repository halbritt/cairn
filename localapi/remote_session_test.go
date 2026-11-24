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

// A remote machine profile may claim from its own verified session inbox
// (CAIRN-794), and from nothing else: not its base profile, not another
// machine's session, not as an observer, and with no other operation widened.
func TestRemoteEventNextOnlyForOwnVerifiedSession(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	ctx := context.Background()
	admin, err := core.Open(ctx, dsn, core.Channel{Principal: "remote-session-test", Operator: true})
	if err != nil {
		t.Fatal(err)
	}
	defer admin.Close()
	if err = admin.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	repo := "remote-session/" + uuid.NewString()
	suffix := uuid.NewString()[:8]
	token := func(name string) string { return "synthetic-session-" + suffix + "-" + name }
	identities := []Identity{}
	for _, name := range []string{"a", "b", "observer", "local"} {
		digest := sha256.Sum256([]byte(token(name)))
		identity := Identity{TokenSHA256: hex.EncodeToString(digest[:]), Principal: "machine:s" + suffix + name + "/agent", Repo: repo, Role: "agent", Destination: "hosted", Remote: true, MachineID: "s" + suffix + name}
		switch name {
		case "observer":
			identity.Role = "observer"
			identity.Principal = "machine:s" + suffix + "a/observer"
			identity.MachineID = "s" + suffix + "a"
		case "local":
			identity.Remote = false
			identity.MachineID = ""
			identity.Principal = "local-remote-session-test-" + suffix
		}
		identities = append(identities, identity)
	}
	server, err := New(ctx, dsn, identities)
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	if err := server.SetLocalMachineID("central-" + suffix); err != nil {
		t.Fatal(err)
	}
	type reply struct {
		code   int
		status string
		msg    string
		data   json.RawMessage
	}
	call := func(handler http.Handler, who, operation string, body any, session *core.AgentInstance, only string) reply {
		t.Helper()
		encoded, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		req := httptest.NewRequest("POST", "/v1/"+operation, bytes.NewReader(encoded))
		req.Header.Set("Authorization", "Bearer "+token(who))
		if session != nil {
			if only != "execution" {
				req.Header.Set("Cairn-Agent-ID", session.AgentID)
			}
			if only != "agent" {
				req.Header.Set("Cairn-Execution-ID", session.ExecutionID)
			}
		}
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, req)
		var envelope struct {
			Status  string          `json:"status"`
			Message string          `json:"message"`
			Data    json.RawMessage `json:"data"`
		}
		_ = json.Unmarshal(w.Body.Bytes(), &envelope)
		return reply{w.Code, envelope.Status, envelope.Message, envelope.Data}
	}
	remote := server.RemoteHandler()
	metadata := core.AgentMetadata{Harness: "codex", Project: "test", Workspace: "/same/path", State: "idle", DeliveryMode: "existing-session"}
	register := func(name string) core.AgentInstance {
		r := call(remote, name, "agent-register", core.RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "same", NativeSessionID: "same", Metadata: metadata}, nil, "")
		if r.code != 200 {
			t.Fatalf("register %s: %d %s %s", name, r.code, r.status, r.msg)
		}
		var a core.AgentInstance
		if err := json.Unmarshal(r.data, &a); err != nil {
			t.Fatal(err)
		}
		return a
	}
	a, b := register("a"), register("b")
	r := call(server, "local", "create", core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "remote session inbox", Sensitivity: "shareable", ClaimType: "self", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}}, nil, "")
	var note core.Record
	if r.code != 200 || json.Unmarshal(r.data, &note) != nil {
		t.Fatalf("create: %d %s %s", r.code, r.status, r.msg)
	}
	publish := func(to core.AgentInstance) core.AgentEvent {
		r := call(server, "local", "event-publish", core.PublishEventRequest{RequestID: uuid.NewString(), Ref: core.RecordVersionRef{RecordID: note.RecordID, Version: 1}, Kind: "request", Destination: core.EventDestination{Type: "agent", Name: to.Inbox}}, nil, "")
		var event core.AgentEvent
		if r.code != 200 || json.Unmarshal(r.data, &event) != nil {
			t.Fatalf("publish: %d %s %s", r.code, r.status, r.msg)
		}
		return event
	}
	forA, forB := publish(a), publish(b)
	next := func(r reply) *core.AgentDelivery {
		t.Helper()
		var out core.NextEventResult
		if r.code != 200 || json.Unmarshal(r.data, &out) != nil {
			t.Fatalf("event-next: %d %s %s", r.code, r.status, r.msg)
		}
		return out.Delivery
	}
	denied := func(what string, r reply) {
		t.Helper()
		if r.code != 403 || r.status != "AUTHORITY_DENIED" || r.msg != "operation is unavailable to remote machine profiles" {
			t.Fatalf("%s: got %d %s %q, want the remote allowlist refusal", what, r.code, r.status, r.msg)
		}
	}
	lease := map[string]any{"lease_seconds": 60}
	for _, handler := range []http.Handler{server, remote} {
		// The base (session-free) remote profile and an incomplete session stay refused.
		denied("base profile", call(handler, "a", "event-next", lease, nil, ""))
		denied("agent header only", call(handler, "a", "event-next", lease, &a, "agent"))
		denied("execution header only", call(handler, "a", "event-next", lease, &a, "execution"))
		// The observer role gains nothing from session headers.
		denied("observer session", call(handler, "observer", "event-next", lease, &a, ""))
		// No other remote-denied operation is widened by session headers.
		for _, op := range []string{"event-retry", "register-context", "wake-claim", "session-tool-capture"} {
			denied(op+" with session", call(handler, "a", op, map[string]any{}, &a, ""))
		}
		// Another machine's session is refused by the store's ownership check before any claim.
		cross := call(handler, "a", "event-next", lease, &b, "")
		if cross.code != 404 || cross.status != "NOT_FOUND" || cross.msg != "NOT_FOUND: session is not registered to this profile" {
			t.Fatalf("cross-machine session: got %d %s %q", cross.code, cross.status, cross.msg)
		}
	}
	// The remote profile claims exactly its own session's work, and only once.
	got := next(call(remote, "a", "event-next", lease, &a, ""))
	if got == nil || got.Event.EventID != forA.EventID {
		t.Fatalf("own session claim: %+v", got)
	}
	if again := next(call(remote, "a", "event-next", lease, &a, "")); again != nil {
		t.Fatalf("own session claimed twice or reached other work: %+v", again)
	}
	done := call(remote, "a", "event-complete", core.CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: got.DeliveryID, LeaseID: got.LeaseID, Disposition: "handled"}, &a, "")
	if done.code != 200 {
		t.Fatalf("complete own claim: %d %s %s", done.code, done.status, done.msg)
	}
	// The cross-machine attempts left b's work untouched for b.
	if other := next(call(remote, "b", "event-next", lease, &b, "")); other == nil || other.Event.EventID != forB.EventID || other.Attempts != 1 {
		t.Fatalf("other machine's work after cross attempts: %+v", other)
	}
	// A replaced execution of the own session is refused as stale.
	stale := a
	stale.ExecutionID = uuid.NewString()
	if r := call(remote, "a", "event-next", lease, &stale, ""); r.status != "STALE_SESSION" {
		t.Fatalf("stale own session: got %d %s %q", r.code, r.status, r.msg)
	}
}
