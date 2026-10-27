package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

// The attention and starvation evaluation is tested in core through
// SummarizeAttention and DetectStarvation. These tests cover the CLI's own
// presentation: the private state file and the coordination-review page.

func reviewFixture(id string) core.ReviewDelivery {
	waiting := int64(900)
	return core.ReviewDelivery{
		DeliveryID: id, Consumer: "agent/fixture", State: "pending", Event: core.AgentEvent{Kind: "request"},
		Diagnosis: &core.DeliveryDiagnosis{
			Stage: "waiting", WaitingSeconds: &waiting,
			Recipient: core.DiagnosisRecipient{Kind: "session", State: "idle", Presence: "online"},
			Host:      &core.DiagnosisHost{Condition: "wake_retained", Applies: "exact", Freshness: "current"},
		},
	}
}

func TestAttentionCacheScopesAndBoundedPrivateState(t *testing.T) {
	path := filepath.Join(t.TempDir(), "attention.json")
	options := core.AttentionOptions{StateFile: path}
	calls := 0
	run := func(c core.AttentionCache) (core.AttentionResult, core.AttentionCache, error) {
		calls++
		return core.SummarizeAttention([]core.ReviewDelivery{reviewFixture("one")}, true, options, c, time.Now())
	}
	first, err := withAttentionCache(options, "scope", run)
	if err != nil || len(first.Changed) != 1 {
		t.Fatal(first, err)
	}
	second, err := withAttentionCache(options, "scope", run)
	if err != nil || len(second.Changed) != 0 {
		t.Fatal(second, err)
	}
	if _, err = withAttentionCache(options, "different-scope", run); err == nil || calls != 2 {
		t.Fatal("scope mismatch accepted", err, calls)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), "event") || strings.Contains(string(raw), "payload") {
		t.Fatal("unselected material cached")
	}
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatal("private mode missing", info, err)
	}
	if err = os.WriteFile(path, []byte("invalid"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err = withAttentionCache(options, "scope", run); err == nil {
		t.Fatal("corrupt cache silently overwritten")
	}
}

func TestCoordinationReviewAttentionReportsStarvedOnlineSession(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	t.Setenv("CAIRN_DATABASE_URL", dsn)
	ctx := context.Background()
	repo := "starvation-" + uuid.NewString()
	pub, err := core.Open(ctx, dsn, core.Channel{Principal: "agent/publisher", Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	defer pub.Close()
	if err := pub.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	host, err := core.Open(ctx, dsn, core.Channel{Principal: "agent/host-busy", Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	defer host.Close()
	dest := core.Destination{Name: "hosted", AllowLocal: false}
	busy, err := host.RegisterAgent(ctx, core.RegisterAgentRequest{
		RequestID: uuid.NewString(), Binding: "busy-coordinator", NativeSessionID: uuid.NewString(),
		Metadata: core.AgentMetadata{Harness: "claude-code", Project: "fleet", Workspace: "/work/fleet", State: "busy", DeliveryMode: "existing-session"},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	note, err := pub.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "status update for the coordinator", ClaimType: "self", Sensitivity: "shareable", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}})
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 3; i++ {
		if _, err := pub.PublishEvent(ctx, core.PublishEventRequest{RequestID: uuid.NewString(), Repo: repo, Kind: "notice", Ref: core.RecordVersionRef{RecordID: note.RecordID, Version: note.Version}, Destination: core.EventDestination{Type: "agent", Name: busy.Inbox}}, dest); err != nil {
			t.Fatal(err)
		}
	}
	time.Sleep(1100 * time.Millisecond)
	if _, err := host.HeartbeatAgent(ctx, core.AgentSessionRef{AgentID: busy.AgentID, ExecutionID: busy.ExecutionID}, dest); err != nil {
		t.Fatal(err)
	}
	out, err := run(ctx, []string{"coordination-review"}, strings.NewReader(`{"repo":"`+repo+`","state":"pending","limit":100,"attention":{"starvation":{"busy_after_seconds":1}}}`))
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(out)
	var review struct {
		Attention *core.AttentionResult `json:"attention"`
	}
	if err := json.Unmarshal(raw, &review); err != nil || review.Attention == nil {
		t.Fatalf("unmarshal review: %s %v", raw, err)
	}
	if len(review.Attention.Changed) != 0 || len(review.Attention.Groups) != 0 {
		t.Fatalf("busy recipients stay out of the existing groups: %+v", review.Attention)
	}
	starved := review.Attention.StarvedSessions
	if len(starved) != 1 || starved[0].Recipient != busy.Inbox || starved[0].Count != 3 || starved[0].Notices != 3 ||
		starved[0].Condition != "pending_starved" || starved[0].LastHandledAt != nil || starved[0].AgentID != busy.AgentID {
		t.Fatalf("starved_sessions: %+v", starved)
	}
}

func TestCoordinationReviewAgent463IncidentAndNotifications(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	t.Setenv("CAIRN_DATABASE_URL", dsn)
	ctx := context.Background()
	repo := "incident-463-" + uuid.NewString()
	pub, err := core.Open(ctx, dsn, core.Channel{Principal: "agent/integrator449", Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	defer pub.Close()
	if err := pub.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	recStore, err := core.Open(ctx, dsn, core.Channel{Principal: "agent/host-463", Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	defer recStore.Close()
	dest := core.Destination{Name: "hosted", AllowLocal: false}
	agent463, err := recStore.RegisterAgent(ctx, core.RegisterAgentRequest{
		RequestID: uuid.NewString(), Binding: "agent-463", NativeSessionID: uuid.NewString(),
		Metadata: core.AgentMetadata{Harness: "codex", Project: "placer", Workspace: "/work/placer", State: "idle", DeliveryMode: "existing-session", TaskSummary: "placer search"},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref463 := core.AgentSessionRef{AgentID: agent463.AgentID, ExecutionID: agent463.ExecutionID}
	note, err := pub.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "placer-search proposal and candidate-launch update", ClaimType: "self", Sensitivity: "shareable", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}})
	if err != nil {
		t.Fatal(err)
	}
	var firstDeliveryID string
	for i := 0; i < 35; i++ {
		evt, err := pub.PublishEvent(ctx, core.PublishEventRequest{RequestID: uuid.NewString(), Repo: repo, Kind: "notice", Ref: core.RecordVersionRef{RecordID: note.RecordID, Version: note.Version}, Destination: core.EventDestination{Type: "agent", Name: agent463.Inbox}}, dest)
		if err != nil {
			t.Fatal(err)
		}
		if i == 0 {
			st, err := pub.AgentEventStatus(ctx, core.EventStatusRequest{EventID: evt.EventID}, dest)
			if err != nil || len(st.Deliveries) != 1 {
				t.Fatalf("status: %+v %v", st, err)
			}
			firstDeliveryID = st.Deliveries[0].DeliveryID
			now := time.Now().UTC().Add(-time.Second)
			if _, err := recStore.ObserveSessionDelivery(ctx, core.SessionDeliveryObservation{
				Session: ref463, DeliveryID: firstDeliveryID, Condition: "wake_retained",
				Wake: &core.ObservedWake{Transport: "codex-queue", Status: "refused", AttemptedAt: &now}, ObservedAt: now,
			}, dest); err != nil {
				t.Fatal(err)
			}
		}
	}
	if _, err := recStore.LeaveAgent(ctx, ref463, dest); err != nil {
		t.Fatal(err)
	}

	stateFile := filepath.Join(t.TempDir(), "attention.json")
	out, err := run(ctx, []string{"coordination-review"}, strings.NewReader(`{"repo":"`+repo+`","consumer":"`+agent463.Inbox+`","state":"pending","limit":100,"attention":{"older_than_seconds":300,"state_file":"`+stateFile+`","notify_publishers":true}}`))
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(out)
	var review struct {
		Attention *core.AttentionResult `json:"attention"`
	}
	if err := json.Unmarshal(raw, &review); err != nil || review.Attention == nil {
		t.Fatalf("unmarshal review: %+v %v", review, err)
	}
	if len(review.Attention.StoppedSessions) != 1 || review.Attention.StoppedSessions[0].Notices != 35 || review.Attention.StoppedSessions[0].OldestDeliveryID != firstDeliveryID {
		t.Fatalf("stopped_sessions: %+v", review.Attention.StoppedSessions)
	}
	if review.Attention.PublisherNotifications == nil || review.Attention.PublisherNotifications.Sent != 35 || review.Attention.PublisherNotifications.Suppressed != 0 {
		t.Fatalf("publisher_notifications first run: %+v", review.Attention.PublisherNotifications)
	}
	// Second run suppresses all 35 notifications via state_file cache.
	out2, err := run(ctx, []string{"coordination-review"}, strings.NewReader(`{"repo":"`+repo+`","consumer":"`+agent463.Inbox+`","state":"pending","limit":100,"attention":{"older_than_seconds":300,"state_file":"`+stateFile+`","notify_publishers":true}}`))
	if err != nil {
		t.Fatal(err)
	}
	raw2, _ := json.Marshal(out2)
	if err := json.Unmarshal(raw2, &review); err != nil || review.Attention.PublisherNotifications.Sent != 0 || review.Attention.PublisherNotifications.Suppressed != 35 || len(review.Attention.Changed) != 0 {
		t.Fatalf("publisher_notifications second run: %+v %v", review.Attention, err)
	}
}
