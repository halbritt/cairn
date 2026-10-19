package main

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

func TestSuccessorTakeoverEndToEndCLIAndAPI(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	ctx := context.Background()
	t.Setenv("CAIRN_DATABASE_URL", dsn)
	if _, err := run(ctx, []string{"migrate"}, strings.NewReader("")); err != nil {
		t.Fatal(err)
	}

	repo := "cli-successor-" + uuid.NewString()
	token := "successor-cli-token-" + uuid.NewString()
	srv, err := localapi.New(ctx, dsn, []localapi.Identity{{
		TokenSHA256: tokenDigest(token),
		Principal:   "profile-owner-" + repo,
		Repo:        repo,
		Role:        "agent",
		Destination: "hosted",
	}})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(srv.Close)

	dir := t.TempDir()
	socketPath := filepath.Join(dir, "api.sock")
	tokenPath := filepath.Join(dir, "agent.token")
	if err := os.WriteFile(tokenPath, []byte(token+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	ln, err := net.Listen("unix", socketPath)
	if err != nil {
		t.Fatal(err)
	}
	httpSrv := &http.Server{Handler: srv}
	go func() { _ = httpSrv.Serve(ln) }()
	t.Cleanup(func() {
		_ = httpSrv.Close()
		_ = ln.Close()
	})

	runSuccessorCLIScenario(t, ctx, socketPath, tokenPath, repo)
}

func runSuccessorCLIScenario(t *testing.T, ctx context.Context, socketPath, tokenPath, repo string) {
	t.Helper()
	callCLI := func(args []string, stdin string, target any) {
		t.Helper()
		out, err := run(ctx, args, strings.NewReader(stdin))
		if err != nil {
			t.Fatalf("%v: %v", args, err)
		}
		if target != nil {
			raw, err := json.Marshal(out)
			if err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal(raw, target); err != nil {
				t.Fatalf("unmarshal %s: %v", raw, err)
			}
		}
	}

	var sender, pred, succ core.AgentInstance
	callCLI([]string{"agents", "register", "--socket", socketPath, "--token-file", tokenPath, "--request-id", uuid.NewString(), "--binding", "codex", "--native-session", "sender-469", "--harness", "codex", "--project", "cairn", "--workspace", "/work/cairn"}, "", &sender)
	callCLI([]string{"agents", "register", "--socket", socketPath, "--token-file", tokenPath, "--request-id", uuid.NewString(), "--binding", "claude", "--native-session", "pred-464", "--harness", "claude", "--project", "cairn", "--workspace", "/work/cairn"}, "", &pred)
	callCLI([]string{"agents", "register", "--socket", socketPath, "--token-file", tokenPath, "--request-id", uuid.NewString(), "--binding", "claude", "--native-session", "succ-470", "--harness", "claude", "--project", "cairn", "--workspace", "/work/cairn"}, "", &succ)

	var source, reply core.Record
	callCLI([]string{"agent", "--socket", socketPath, "--token-file", tokenPath, "remember", "--repo", repo, "--request-id", uuid.NewString(), "--shareable", "Request payload for predecessor"}, "", &source)
	callCLI([]string{"agent", "--socket", socketPath, "--token-file", tokenPath, "remember", "--repo", repo, "--request-id", uuid.NewString(), "--shareable", "Linked follow-up from successor"}, "", &reply)

	var reqEvent core.AgentEvent
	callCLI([]string{"publish", "--socket", socketPath, "--token-file", tokenPath, "--agent-id", sender.AgentID, "--execution-id", sender.ExecutionID, "--request-id", uuid.NewString(), "--to", pred.Inbox, "--kind", "request", "--version", "1", source.RecordID}, "", &reqEvent)

	// Claim and reconcile pred's inbox as turn_ended, then leave.
	var claim core.SessionInboxResult
	callCLI([]string{"agent", "--socket", socketPath, "--token-file", tokenPath, "session-inbox-claim"}, `{"request_id":"`+uuid.NewString()+`","session":{"agent_id":"`+pred.AgentID+`","execution_id":"`+pred.ExecutionID+`"}}`, &claim)
	callCLI([]string{"agent", "--socket", socketPath, "--token-file", tokenPath, "session-inbox-reconcile"}, `{"request_id":"`+uuid.NewString()+`","session":{"agent_id":"`+pred.AgentID+`","execution_id":"`+pred.ExecutionID+`"},"attempt_id":"`+claim.Attempt.ID+`","reason":"turn_ended"}`, nil)
	callCLI([]string{"agents", "leave", "--socket", socketPath, "--token-file", tokenPath, "--agent-id", pred.AgentID, "--execution-id", pred.ExecutionID}, "", nil)

	// Before operator grant, successor publish with --causation-id returns NOT_FOUND.
	_, err := run(ctx, []string{"publish", "--socket", socketPath, "--token-file", tokenPath, "--agent-id", succ.AgentID, "--execution-id", succ.ExecutionID, "--request-id", uuid.NewString(), "--to", sender.Inbox, "--kind", "response", "--version", "1", "--causation-id", reqEvent.EventID, reply.RecordID}, strings.NewReader(""))
	if core.Code(err) != "NOT_FOUND" {
		t.Fatalf("expected NOT_FOUND before grant, got %v", err)
	}

	// Operator authorizes successor session.
	var grant core.SessionSuccessorGrant
	authJSON := `{"request_id":"` + uuid.NewString() + `","repo":"` + repo + `","predecessor_agent_id":"` + pred.AgentID + `","successor":{"agent_id":"` + succ.AgentID + `","execution_id":"` + succ.ExecutionID + `"},"event_ids":["` + reqEvent.EventID + `"],"reason":"Authorize successor CLI fixture"}`
	callCLI([]string{"session-successor-authorize"}, authJSON, &grant)

	var status core.EventStatus
	callCLI([]string{"event-status", "--socket", socketPath, "--token-file", tokenPath, "--agent-id", succ.AgentID, "--execution-id", succ.ExecutionID, reqEvent.EventID}, "", &status)
	if status.Event.EventID != reqEvent.EventID || len(status.Deliveries) != 1 || status.Deliveries[0].State != "failed" {
		t.Fatalf("unexpected CLI event-status: %+v", status)
	}

	var followUp core.AgentEvent
	callCLI([]string{"publish", "--socket", socketPath, "--token-file", tokenPath, "--agent-id", succ.AgentID, "--execution-id", succ.ExecutionID, "--request-id", uuid.NewString(), "--to", sender.Inbox, "--kind", "response", "--version", "1", "--causation-id", reqEvent.EventID, reply.RecordID}, "", &followUp)
	if followUp.From != succ.Inbox || followUp.CausationID != reqEvent.EventID {
		t.Fatalf("unexpected CLI followUp: %+v", followUp)
	}

	// Operator revokes the grant; subsequent inspection returns NOT_FOUND.
	revokeJSON := `{"request_id":"` + uuid.NewString() + `","grant_id":"` + grant.GrantID + `","reason":"Completed takeover follow-up"}`
	callCLI([]string{"session-successor-revoke"}, revokeJSON, nil)
	_, err = run(ctx, []string{"event-status", "--socket", socketPath, "--token-file", tokenPath, "--agent-id", succ.AgentID, "--execution-id", succ.ExecutionID, reqEvent.EventID}, strings.NewReader(""))
	if core.Code(err) != "NOT_FOUND" {
		t.Fatalf("expected NOT_FOUND after revoke, got %v", err)
	}
}
