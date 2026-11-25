package main

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestTeamCLIAndFilteredSubscribeAndReroutedSend(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	if err := os.Chmod(dir, 0700); err != nil {
		t.Fatal(err)
	}
	sock := filepath.Join(dir, "api.sock")
	tok := filepath.Join(dir, "agent.token")
	if err := os.WriteFile(tok, []byte("test-token"), 0600); err != nil {
		t.Fatal(err)
	}

	var lastPath string
	var lastBody map[string]any
	ln, err := net.Listen("unix", sock)
	if err != nil {
		t.Fatal(err)
	}
	srv := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		lastPath = r.URL.Path
		lastBody = nil
		_ = json.Unmarshal(raw, &lastBody)
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/v1/team-campaign-set":
			_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": core.TeamCampaign{Repo: "repo", Campaign: "alpha", RootAgent: "agent/root", OutOfChainPolicy: "reroute", Active: true, Revision: 1}})
		case "/v1/team-member-set":
			_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": core.TeamMember{Repo: "repo", Campaign: "alpha", Member: "agent/w1", Lead: "agent/l1", Role: "worker", Active: true}})
		case "/v1/event-subscribe":
			_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": core.AgentSubscription{Consumer: "agent/root", Topic: "team.alpha", Active: true}})
		case "/v1/create":
			var createReq core.CreateRequest
			_ = json.Unmarshal(raw, &createReq)
			_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": core.Record{RecordID: "55555555-5555-4555-8555-555555555555", Version: 1, Sensitivity: createReq.Draft.Sensitivity, Draft: createReq.Draft}})
		case "/v1/event-publish":
			orig := core.EventDestination{Type: "agent", Name: "agent/root"}
			eff := core.EventDestination{Type: "agent", Name: "agent/lead"}
			_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": core.AgentEvent{
				EventID: uuid.NewString(), Repo: "repo", From: "agent/w1", Kind: "notice", Blocker: true,
				Ref:         core.RecordVersionRef{RecordID: "55555555-5555-4555-8555-555555555555", Version: 1},
				Destination: eff,
				Routing:     &core.EventRouting{Campaign: "alpha", Policy: "reroute", OutOfChain: true, Rerouted: true, OriginalDestination: &orig, EffectiveDestination: eff, Lead: "agent/lead"},
			}})
		default:
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": false, "status": "INVALID_REQUEST", "message": "unexpected " + r.URL.Path})
		}
	})}
	go func() { _ = srv.Serve(ln) }()
	t.Cleanup(func() { _ = srv.Close() })

	if _, err := run(ctx, []string{"team", "set-campaign", "--socket", sock, "--token-file", tok, "--repo", "repo", "--campaign", "alpha", "--out-of-chain-policy", "reroute"}, strings.NewReader("")); err != nil || lastPath != "/v1/team-campaign-set" || lastBody["out_of_chain_policy"] != "reroute" {
		t.Fatalf("team set-campaign failed: path=%s body=%+v err=%v", lastPath, lastBody, err)
	}
	if _, err := run(ctx, []string{"subscribe", "--socket", sock, "--token-file", tok, "--repo", "repo", "--request-id", uuid.NewString(), "--topic", "team.alpha", "--kinds", "request,notice", "--actions", "decide,complete", "--blockers", "--subtree-only"}, strings.NewReader("")); err != nil || lastPath != "/v1/event-subscribe" {
		t.Fatalf("filtered subscribe failed: %v", err)
	}
	filterMap := lastBody["filter"].(map[string]any)
	if filterMap["include_blockers"] != true || filterMap["subtree_only"] != true {
		t.Fatalf("unexpected filter payload: %+v", filterMap)
	}

	// Rerouted `cairn agent send --blocker` succeeds and records routing in sendResult!
	jPath := filepath.Join(dir, "send.journal.json")
	sendRes, err := run(ctx, []string{"agent", "--socket", sock, "--token-file", tok, "send", "--journal", jPath, "--repo", "repo", "--stdin", "--shareable", "--kind", "notice", "--action", "decide", "--blocker", "--to", "agent/root"}, strings.NewReader("Blocker details"))
	if err != nil {
		t.Fatalf("rerouted agent send failed: %v", err)
	}
	decoded, ok := sendRes.(sendResult)
	if !ok {
		t.Fatalf("unexpected sendRes type %T: %+v", sendRes, sendRes)
	}
	if decoded.Routing == nil || !decoded.Routing.Rerouted || decoded.Routing.EffectiveDestination.Name != "agent/lead" {
		t.Fatalf("expected rerouted routing on sendResult, got %+v", decoded)
	}

	// --no-coordinator-limits clears both limits with 0.
	if _, err := run(ctx, []string{"team", "set-campaign", "--socket", sock, "--token-file", tok, "--repo", "repo", "--campaign", "alpha", "--no-coordinator-limits"}, strings.NewReader("")); err != nil || lastBody["max_coordinator_age_seconds"] != float64(0) || lastBody["max_coordinator_compactions"] != float64(0) {
		t.Fatalf("expected --no-coordinator-limits to send 0 thresholds: body=%+v err=%v", lastBody, err)
	}
	// subscribe with --actions defaults include_blockers=true unless --blockers=false; --all-events clears filter.
	if _, err := run(ctx, []string{"subscribe", "--socket", sock, "--token-file", tok, "--repo", "repo", "--request-id", uuid.NewString(), "--topic", "team.alpha", "--actions", "decide"}, strings.NewReader("")); err != nil {
		t.Fatalf("subscribe with --actions failed: %v", err)
	}
	if lastBody["filter"].(map[string]any)["include_blockers"] != true {
		t.Fatalf("expected include_blockers=true default with --actions, got %+v", lastBody["filter"])
	}
	if _, err := run(ctx, []string{"subscribe", "--socket", sock, "--token-file", tok, "--repo", "repo", "--request-id", uuid.NewString(), "--topic", "team.alpha", "--all-events"}, strings.NewReader("")); err != nil {
		t.Fatalf("subscribe --all-events failed: %v", err)
	}
	if lastBody["filter"] != nil {
		t.Fatalf("expected nil filter for --all-events, got %+v", lastBody["filter"])
	}
	if _, err := run(ctx, []string{"subscribe", "--socket", sock, "--token-file", tok, "--repo", "repo", "--request-id", uuid.NewString(), "--topic", "team.alpha", "--all-events", "--actions", "decide"}, strings.NewReader("")); err == nil {
		t.Fatal("expected --all-events combined with --actions to fail")
	}
}
