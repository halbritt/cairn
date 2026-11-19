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

func TestLedgerCLIAndCampaignSendWithoutJournal(t *testing.T) {
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
	srcRecID := uuid.NewString()
	evID := uuid.NewString()
	delivID := uuid.NewString()
	leaseID := uuid.NewString()

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
		ok := func(data any) {
			_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": data})
		}
		switch r.URL.Path {
		case "/v1/ledger-get":
			ok(core.CampaignLedgerSnapshot{Schema: core.CampaignLedgerSchema, Campaign: core.TeamCampaign{Repo: "repo", Campaign: "alpha"}})
		case "/v1/ledger-item-set":
			ok(core.CampaignLedgerItem{Repo: "repo", Campaign: "alpha", Kind: "assignment", ItemKey: "slice-1", Status: "planned", Revision: 1})
		case "/v1/ledger-sync-context":
			ok(core.SyncCampaignContextResult{Repo: "repo", Campaign: "alpha", Principal: "agent/root", DerivedContextSummary: "campaign alpha [root]"})
		case "/v1/ledger-publish":
			ok(core.AgentEvent{EventID: evID, Repo: "repo", Kind: "request", Ref: core.RecordVersionRef{RecordID: srcRecID, Version: 1}, Destination: core.EventDestination{Type: "agent", Name: "agent/w1"}})
		case "/v1/ledger-send":
			ok(core.CampaignSendResult{
				OpKey: "op-send-1", State: "committed",
				Source:               core.RecordVersionRef{RecordID: srcRecID, Version: 1},
				Event:                core.AgentEvent{EventID: evID, Repo: "repo", Kind: "request", Ref: core.RecordVersionRef{RecordID: srcRecID, Version: 1}, Destination: core.EventDestination{Type: "agent", Name: "agent/w1"}},
				SourceRequestID:      uuid.NewString(),
				PublicationRequestID: uuid.NewString(),
			})
		case "/v1/ledger-complete":
			ok(core.CampaignCompleteResult{
				OpKey: "op-comp-1", State: "committed",
				Delivery: core.AgentDelivery{DeliveryID: delivID, State: "handled"},
				Result:   core.RecordVersionRef{RecordID: srcRecID, Version: 1},
			})
		case "/v1/ledger-ack":
			ok(core.AgentDelivery{DeliveryID: delivID, State: "handled"})
		case "/v1/team-campaign-set":
			ok(core.TeamCampaign{Repo: "repo", Campaign: "alpha", RoleAddress: "role/alpha/root", RootAgent: "agent/root", Active: true, Revision: 1})
		case "/v1/ledger-handoff-check":
			ok(core.CoordinatorHandoffStatus{Repo: "repo", Campaign: "alpha", RoleAddress: "role/alpha/root", HolderPrincipal: "agent/root", ShouldHandoff: false, Reasons: []string{}})
		case "/v1/ledger-compaction":
			ok(core.CoordinatorHandoffStatus{Repo: "repo", Campaign: "alpha", RoleAddress: "role/alpha/root", HolderPrincipal: "agent/root", CompactionCount: 3, ShouldHandoff: true, Reasons: []string{"compactions"}})
		case "/v1/ledger-handoff":
			ok(core.CoordinatorHandoffResult{Performed: true, Campaign: core.TeamCampaign{Repo: "repo", Campaign: "alpha", RoleAddress: "role/alpha/root"}, RoleAddress: "role/alpha/root", FromPrincipal: "agent/root", ToPrincipal: "agent/new"})
		default:
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": false, "status": "INVALID_REQUEST", "message": "unexpected " + r.URL.Path})
		}
	})}
	go func() { _ = srv.Serve(ln) }()
	t.Cleanup(func() { _ = srv.Close() })

	// 1. ledger get
	if _, err := run(ctx, []string{"ledger", "get", "--socket", sock, "--token-file", tok, "--repo", "repo", "--campaign", "alpha", "--kind", "assignment"}, strings.NewReader("")); err != nil || lastPath != "/v1/ledger-get" || lastBody["campaign"] != "alpha" {
		t.Fatalf("ledger get failed: path=%s body=%+v err=%v", lastPath, lastBody, err)
	}
	// 2. ledger set
	if _, err := run(ctx, []string{"ledger", "set", "--socket", sock, "--token-file", tok, "--repo", "repo", "--campaign", "alpha", "--kind", "assignment", "--key", "slice-1", "--owner", "agent/w1", "--status", "planned", "--summary", "Slice 1"}, strings.NewReader("")); err != nil || lastPath != "/v1/ledger-item-set" || lastBody["item_key"] != "slice-1" {
		t.Fatalf("ledger set failed: path=%s body=%+v err=%v", lastPath, lastBody, err)
	}
	// 3. ledger sync-context
	sessStr := uuid.NewString() + ":" + uuid.NewString()
	if _, err := run(ctx, []string{"ledger", "sync-context", "--socket", sock, "--token-file", tok, "--session", sessStr, "--repo", "repo", "--campaign", "alpha"}, strings.NewReader("")); err != nil || lastPath != "/v1/ledger-sync-context" {
		t.Fatalf("ledger sync-context failed: path=%s err=%v", lastPath, err)
	}
	// 4. ledger publish
	if _, err := run(ctx, []string{"ledger", "publish", "--socket", sock, "--token-file", tok, "--repo", "repo", "--campaign", "alpha", "--op-key", "op-pub-1", "--to", "agent/w1", "--kind", "request", "--version", "1", "--item-key", "slice-1", srcRecID}, strings.NewReader("")); err != nil || lastPath != "/v1/ledger-publish" || lastBody["op_key"] != "op-pub-1" {
		t.Fatalf("ledger publish failed: path=%s body=%+v err=%v", lastPath, lastBody, err)
	}
	// 5. ledger send
	if _, err := run(ctx, []string{"ledger", "send", "--socket", sock, "--token-file", tok, "--repo", "repo", "--campaign", "alpha", "--op-key", "op-send-1", "--to", "agent/w1", "--kind", "request", "--shareable", "--stdin", "--item-key", "slice-1"}, strings.NewReader("Assignment body")); err != nil || lastPath != "/v1/ledger-send" || lastBody["op_key"] != "op-send-1" {
		t.Fatalf("ledger send failed: path=%s body=%+v err=%v", lastPath, lastBody, err)
	}
	// 6. ledger complete
	if _, err := run(ctx, []string{"ledger", "complete", "--socket", sock, "--token-file", tok, "--repo", "repo", "--campaign", "alpha", "--op-key", "op-comp-1", "--lease", leaseID, "--reply-to", "agent/root", "--shareable", "--stdin", "--item-key", "slice-1", delivID}, strings.NewReader("Result body")); err != nil || lastPath != "/v1/ledger-complete" || lastBody["delivery_id"] != delivID {
		t.Fatalf("ledger complete failed: path=%s body=%+v err=%v", lastPath, lastBody, err)
	}
	// 7. ledger ack
	if _, err := run(ctx, []string{"ledger", "ack", "--socket", sock, "--token-file", tok, "--repo", "repo", "--campaign", "alpha", "--op-key", "op-ack-1", "--lease", leaseID, delivID}, strings.NewReader("")); err != nil || lastPath != "/v1/ledger-ack" || lastBody["op_key"] != "op-ack-1" {
		t.Fatalf("ledger ack failed: path=%s body=%+v err=%v", lastPath, lastBody, err)
	}
	// 8. cairn agent send --campaign alpha --op-key op-send-1 WITHOUT --journal (no journal file created!)
	sendDir := filepath.Join(dir, "empty-workdir")
	if err := os.Mkdir(sendDir, 0700); err != nil {
		t.Fatal(err)
	}
	sendRes, err := run(ctx, []string{"agent", "--socket", sock, "--token-file", tok, "send", "--campaign", "alpha", "--op-key", "op-send-1", "--repo", "repo", "--stdin", "--shareable", "--kind", "request", "--to", "agent/w1"}, strings.NewReader("Assignment body"))
	if err != nil {
		t.Fatalf("agent send --campaign --op-key failed: %v", err)
	}
	sr, ok := sendRes.(sendResult)
	if !ok || sr.PublicationPending || sr.EventID != evID || sr.Source == nil || sr.Source.RecordID != srcRecID {
		t.Fatalf("unexpected sendResult: %+v", sendRes)
	}
	entries, _ := os.ReadDir(sendDir)
	if len(entries) != 0 {
		t.Fatalf("expected zero private files in workdir, got %d", len(entries))
	}
	// 9. Mutual exclusion & flag guards
	if _, err := run(ctx, []string{"agent", "--socket", sock, "--token-file", tok, "send", "--journal", filepath.Join(dir, "j.json"), "--campaign", "alpha", "--op-key", "op-1", "--stdin", "--to", "agent/w1"}, strings.NewReader("body")); err == nil {
		t.Fatal("expected mutual exclusion error between --journal and --campaign/--op-key")
	}
	if _, err := run(ctx, []string{"ledger", "get", "--socket", sock, "--token-file", tok, "--campaign", "alpha", "--lease", leaseID}, strings.NewReader("")); err == nil {
		t.Fatal("expected disallowed flag --lease on ledger get to fail")
	}
	// 10. team thresholds + ledger handoff-check, compact, and handoff
	if _, err := run(ctx, []string{"team", "set-campaign", "--socket", sock, "--token-file", tok, "--campaign", "alpha", "--max-coordinator-age-seconds", "3600", "--max-coordinator-compactions", "3"}, strings.NewReader("")); err != nil || lastPath != "/v1/team-campaign-set" || lastBody["max_coordinator_compactions"] != float64(3) {
		t.Fatalf("team set-campaign thresholds failed: path=%s body=%+v err=%v", lastPath, lastBody, err)
	}
	if _, err := run(ctx, []string{"ledger", "handoff-check", "--socket", sock, "--token-file", tok, "--campaign", "alpha"}, strings.NewReader("")); err != nil || lastPath != "/v1/ledger-handoff-check" {
		t.Fatalf("ledger handoff-check failed: %v", err)
	}
	if _, err := run(ctx, []string{"ledger", "compact", "--socket", sock, "--token-file", tok, "--campaign", "alpha"}, strings.NewReader("")); err != nil || lastPath != "/v1/ledger-compaction" {
		t.Fatalf("ledger compact failed: %v", err)
	}
	if _, err := run(ctx, []string{"ledger", "handoff", "--socket", sock, "--token-file", tok, "--campaign", "alpha", "--if-needed", "--new-binding", "coord-alpha", "--new-native-session", "sess-new"}, strings.NewReader("")); err != nil || lastPath != "/v1/ledger-handoff" || lastBody["only_if_threshold_reached"] != true {
		t.Fatalf("ledger handoff failed: path=%s body=%+v err=%v", lastPath, lastBody, err)
	}
}
