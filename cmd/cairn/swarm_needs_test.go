package main

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/jackc/pgx/v5"
)

func TestSwarmNeedsCopyCommandsUseLocalOperatorCLI(t *testing.T) {
	f := newSwarmStoreFixture(t, swarmNoPanes)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	member, _ := f.member("command-recipient")
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	conn, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close(context.Background())

	publish := func(kind, destinationType, principal, summary string) core.AgentEvent {
		t.Helper()
		req := core.PublishEventRequest{RequestID: uuid.NewString(), Repo: f.repo, Kind: kind, Summary: summary,
			Ref:         core.RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
			Destination: core.EventDestination{Type: destinationType, Name: principal}}
		if kind == "notice" {
			req.Action = "review"
		}
		e, err := f.rootView.PublishEvent(ctx, req, f.dest)
		if err != nil {
			t.Fatal(err)
		}
		return e
	}
	publish("request", "topic", "agent-board", "Choose the first option")
	publish("notice", "agent", f.op.Channel().Principal, "Read the operator note")
	failed := publish("request", "agent", member.Inbox, "Prepare the rollout")
	var failedDelivery string
	err = conn.QueryRow(ctx, `UPDATE cairn.agent_delivery SET state='failed',code='processing_failed',completed_at=clock_timestamp()
		WHERE event_id=$1 RETURNING delivery_id::text`, failed.EventID).Scan(&failedDelivery)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Exec(ctx, `UPDATE cairn.agent_session SET started_at=clock_timestamp()-interval '5 hours'
		WHERE agent_id=$1`, f.root.AgentID); err != nil {
		t.Fatal(err)
	}

	for _, item := range []struct{ kind, key, status, owner string }{
		{"decision", "command-decision", "open", f.root.Inbox},
		{"blocker", "command-blocker", "escalated", member.Inbox},
	} {
		if _, err := f.rootView.SetCampaignLedgerItem(ctx, core.SetCampaignLedgerItemRequest{
			RequestID: uuid.NewString(), Repo: f.repo, Campaign: f.campaign, Kind: item.kind, ItemKey: item.key,
			Status: item.status, Owner: item.owner, Summary: "Review " + item.key,
		}, f.dest); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := f.rootView.SetWorkstreamStatus(ctx, core.SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Stream: "operator-command-stream", State: "blocked", DecisionNeeded: "Select the release",
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	snap, err := f.op.GetSwarmSnapshot(ctx, core.SwarmSnapshotRequest{Repo: f.repo, Campaign: f.campaign})
	if err != nil || len(snap.SectionErrors) != 0 || len(snap.NeedsYou.Unavailable) != 0 {
		t.Fatalf("read needs-you snapshot: %v sections=%v sources=%+v", err, snap.SectionErrors, snap.NeedsYou.Unavailable)
	}

	binDir := t.TempDir()
	build := exec.CommandContext(ctx, "go", "build", "-o", filepath.Join(binDir, "cairn"), ".")
	if out, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build private candidate: %v\n%s", err, out)
	}
	want := map[string]string{
		core.SwarmNeedBoardQuestion:      "",
		core.SwarmNeedOperatorDelivery:   "",
		core.SwarmNeedDecision:           "command-decision",
		core.SwarmNeedEscalation:         "command-blocker",
		core.SwarmNeedWorkstreamDecision: "operator-command-stream",
		core.SwarmNeedHandoffDue:         f.root.Inbox,
		core.SwarmNeedFailedDelivery:     failedDelivery,
	}
	if len(snap.NeedsYou.Items) != len(want) {
		t.Fatalf("expected %d seeded rows, got %+v", len(want), snap.NeedsYou.Items)
	}
	for _, item := range snap.NeedsYou.Items {
		t.Run(item.Type, func(t *testing.T) {
			marker, ok := want[item.Type]
			if !ok {
				t.Fatalf("unexpected or duplicate row: %+v", item)
			}
			delete(want, item.Type)
			command := item.Action.Command
			if marker == "" {
				if command != "" {
					t.Errorf("the row should use its event control, got command %q", command)
				}
				return
			}
			if command == "" {
				t.Fatal("missing local operator command")
			}
			// Run the exact string copied from the snapshot against the same
			// disposable database using only the private Cairn executable.
			run := exec.CommandContext(ctx, "/bin/sh", "-c", command)
			run.Env = append(run.Environ(), "PATH="+binDir, "CAIRN_DATABASE_URL="+dsn)
			out, err := run.CombinedOutput()
			if err != nil {
				t.Fatalf("%s: %v\n%s", command, err, out)
			}
			var envelope struct {
				OK   bool            `json:"ok"`
				Data json.RawMessage `json:"data"`
			}
			if err := json.Unmarshal(out, &envelope); err != nil || !envelope.OK || !strings.Contains(string(envelope.Data), marker) {
				t.Fatalf("%s failed to return %q: %s (%v)", command, marker, out, err)
			}
		})
	}
	if len(want) != 0 {
		t.Errorf("missing rows: %v", want)
	}
}
