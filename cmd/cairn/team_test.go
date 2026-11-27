package main

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
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

func TestTeamAndLedgerMetadataThroughHostedProfile(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("PostgreSQL integration test: run make test-integration")
	}
	ctx := context.Background()
	if _, err := run(ctx, []string{"migrate"}, strings.NewReader("")); err != nil {
		t.Fatal(err)
	}
	const profile = "campaign-test"
	dir := t.TempDir()
	t.Setenv("CAIRN_HOME", dir)
	profiles := filepath.Join(dir, "event-profiles")
	if err := os.Mkdir(profiles, 0700); err != nil {
		t.Fatal(err)
	}
	token := uuid.NewString()
	if err := os.WriteFile(filepath.Join(profiles, profile+".token"), []byte(token+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	server, err := localapi.New(ctx, dsn, []localapi.Identity{{
		TokenSHA256: tokenDigest(token), Principal: "profile:" + uuid.NewString(),
		Repo: "/cli-team/" + uuid.NewString(), Role: "agent", Destination: "hosted",
	}})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(server.Close)
	socket := filepath.Join(dir, "api.sock")
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	httpServer := &http.Server{Handler: server}
	go func() { _ = httpServer.Serve(listener) }()
	t.Cleanup(func() { _ = httpServer.Close() })

	call := func(target any, stdin string, args ...string) {
		t.Helper()
		out, err := run(ctx, args, strings.NewReader(stdin))
		if err != nil {
			t.Fatalf("%v: %v", args, err)
		}
		if target == nil {
			return
		}
		raw, err := json.Marshal(out)
		if err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal(raw, target); err != nil {
			t.Fatalf("decode %v: %v", args, err)
		}
	}
	register := func(binding string) core.AgentInstance {
		t.Helper()
		var agent core.AgentInstance
		call(&agent, "", "agents", "register", "--socket", socket, "--profile", profile,
			"--request-id", uuid.NewString(), "--binding", binding, "--native-session", uuid.NewString(),
			"--harness", "codex", "--project", "cairn", "--workspace", "/work/cairn")
		return agent
	}
	root, worker := register("root"), register("worker")
	campaignArgs := func(command, action string, flags ...string) []string {
		return append([]string{command, action, "--socket", socket, "--profile", profile,
			"--agent-id", root.AgentID, "--execution-id", root.ExecutionID}, flags...)
	}
	const campaign = "release-swarm"
	const description = "Coordinate the café rollout.\nVerify the Spanish and Japanese menus.\n"
	const rootLabel, workerLabel = "Release coordinator", "Menú y日本語 reviewer"
	requestID := uuid.NewString()
	var created, replay core.TeamCampaign
	call(&created, description, campaignArgs("team", "set-campaign", "--campaign", campaign,
		"--description-file", "-", "--request-id", requestID)...)
	if created.Description != description || created.RootAgent != root.Inbox {
		t.Fatalf("created campaign = %+v", created)
	}
	call(&replay, "", campaignArgs("team", "set-campaign", "--campaign", campaign,
		"--description", description, "--request-id", requestID)...)
	if !reflect.DeepEqual(created, replay) {
		t.Fatalf("idempotent replay = %+v, want %+v", replay, created)
	}
	_, err = run(ctx, campaignArgs("team", "set-campaign", "--campaign", campaign,
		"--description", "Different intent", "--request-id", requestID), strings.NewReader(""))
	if core.Code(err) != "IDEMPOTENCY_CONFLICT" {
		t.Fatalf("changed retry = %v", err)
	}
	call(nil, "", campaignArgs("team", "set-member", "--campaign", campaign, "--member", root.Inbox,
		"--role-label", rootLabel)...)
	call(nil, "", campaignArgs("team", "set-member", "--campaign", campaign, "--member", worker.Inbox,
		"--lead", root.Inbox, "--role", "worker", "--role-label", workerLabel)...)
	call(nil, "", campaignArgs("ledger", "set", "--campaign", campaign, "--kind", "assignment",
		"--key", "menu-check", "--owner", worker.Inbox, "--status", "planned", "--summary", "Review menus")...)

	assertMetadata := func(got core.TeamCampaign) {
		t.Helper()
		if got.Description != description || len(got.Members) != 2 ||
			got.Members[0].Member != root.Inbox || got.Members[0].Role != "root" || got.Members[0].RoleLabel != rootLabel ||
			got.Members[1].Member != worker.Inbox || got.Members[1].Role != "worker" || got.Members[1].RoleLabel != workerLabel {
			t.Fatalf("campaign metadata = %+v", got)
		}
	}
	var got core.TeamCampaign
	call(&got, "", campaignArgs("team", "get", "--campaign", campaign)...)
	assertMetadata(got)

	// An inactive campaign supplies a second row to exercise a full list page.
	call(nil, "", campaignArgs("team", "set-campaign", "--campaign", "z-archive", "--inactive")...)
	var page core.TeamCampaignPage
	call(&page, "", campaignArgs("team", "list", "--limit", "1")...)
	if len(page.Campaigns) != 1 || !page.More {
		t.Fatalf("campaign list page = %+v", page)
	}
	assertMetadata(page.Campaigns[0])
	var snapshot core.CampaignLedgerSnapshot
	call(&snapshot, "", campaignArgs("ledger", "get", "--campaign", campaign)...)
	assertMetadata(snapshot.Campaign)
	if !slices.ContainsFunc(snapshot.Assignments, func(item core.CampaignLedgerItem) bool {
		return item.ItemKey == "menu-check" && item.Owner == worker.Inbox
	}) {
		t.Fatalf("ledger snapshot lacks the worker's assignment: %+v", snapshot.Assignments)
	}

	var updated core.TeamCampaign
	call(&updated, "", campaignArgs("team", "set-campaign", "--campaign", campaign,
		"--out-of-chain-policy", "reroute")...)
	assertMetadata(updated)
	var member core.TeamMember
	call(&member, "", campaignArgs("team", "set-member", "--campaign", campaign, "--member", worker.Inbox)...)
	if member.RoleLabel != workerLabel {
		t.Fatalf("omitting role_label changed it: %+v", member)
	}
	for _, badLabel := range []string{strings.Repeat("é", 81), "tab\there"} {
		_, err := run(ctx, campaignArgs("team", "set-member", "--campaign", campaign, "--member", worker.Inbox,
			"--role-label", badLabel), strings.NewReader(""))
		if core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("invalid role label = %v", err)
		}
	}
	call(&got, "", campaignArgs("team", "get", "--campaign", campaign)...)
	assertMetadata(got)
	for _, args := range [][]string{
		campaignArgs("team", "set-campaign", "--campaign", campaign, "--description", description, "--description-file", "-"),
		campaignArgs("team", "set-campaign", "--campaign", campaign, "--description-file", ""),
	} {
		_, err := run(ctx, args, strings.NewReader(""))
		if core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("invalid description flags = %v", err)
		}
	}
	_, err = run(ctx, campaignArgs("team", "set-campaign", "--campaign", campaign, "--description-file", "-"),
		strings.NewReader(strings.Repeat("x", core.MaxCampaignDescriptionBytes+1)))
	if core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("oversized description file = %v", err)
	}
	descriptionPath := filepath.Join(dir, "description.txt")
	const fileText = "File description\n"
	if err := os.WriteFile(descriptionPath, []byte(fileText), 0600); err != nil {
		t.Fatal(err)
	}
	call(&updated, "", campaignArgs("team", "set-campaign", "--campaign", campaign, "--description-file", descriptionPath)...)
	if updated.Description != fileText {
		t.Fatalf("description from file = %q", updated.Description)
	}
	call(nil, "", campaignArgs("team", "set-campaign", "--campaign", campaign, "--description", "")...)
	call(nil, "", campaignArgs("team", "set-member", "--campaign", campaign, "--member", worker.Inbox, "--role-label", "")...)
	got = core.TeamCampaign{}
	call(&got, "", campaignArgs("team", "get", "--campaign", campaign)...)
	if got.Description != "" || len(got.Members) != 2 || got.Members[1].RoleLabel != "" {
		t.Fatalf("clear left metadata set: %+v", got)
	}
}
