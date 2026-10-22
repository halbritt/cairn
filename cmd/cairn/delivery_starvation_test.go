package main

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

var starvationNow = time.Date(2026, 10, 21, 22, 0, 0, 0, time.UTC)

// starvationRow builds one review row as coordination-review renders it.
func starvationRow(id, consumer, state, recipientState string, waiting int) attentionDelivery {
	var d attentionDelivery
	raw := `{"delivery_id":"` + id + `","consumer":"` + consumer + `","state":"` + state + `","available_at":"2026-10-21T12:00:00Z",
	"event":{"kind":"request"},
	"diagnosis":{"stage":"waiting","waiting_seconds":` + itoa(waiting) + `,
	"recipient":{"kind":"session","agent_id":"a-` + consumer + `","execution_id":"x-` + consumer + `","state":"` + recipientState + `","presence":"online"},
	"host":{"condition":"busy","applies":"session","freshness":"current"}}}`
	if err := json.Unmarshal([]byte(raw), &d); err != nil {
		panic(err)
	}
	return d
}

func itoa(n int) string { b, _ := json.Marshal(n); return string(b) }

func handledAt(m map[string]time.Time) func([]string) (map[string]time.Time, error) {
	return func([]string) (map[string]time.Time, error) { return m, nil }
}

func TestStarvationReportsAgedPendingForOnlineBusySession(t *testing.T) {
	rows := []attentionDelivery{
		starvationRow("d1", "agent/449", "pending", "busy", 36000),
		starvationRow("d2", "agent/449", "pending", "busy", 2400),
		starvationRow("d3", "agent/449", "pending", "busy", 60),
	}
	got, err := detectStarvation(rows, starvationOptions{}, starvationNow, handledAt(nil))
	if err != nil || len(got) != 1 {
		t.Fatalf("want one starved session, got %+v %v", got, err)
	}
	s := got[0]
	if s.Recipient != "agent/449" || s.Condition != "pending_starved" || s.Count != 3 || s.OldestAgeSeconds != 36000 ||
		s.OldestDeliveryID != "d1" || s.LastHandledAt != nil || s.RecipientState != "busy" || s.ThresholdSeconds != 1800 || s.Diagnosis == "" {
		t.Fatalf("unexpected starved session: %+v", s)
	}
}

func TestStarvationThresholdDependsOnBusyOrIdle(t *testing.T) {
	rows := []attentionDelivery{
		starvationRow("busy20m", "agent/busy", "pending", "busy", 1200),
		starvationRow("idle11m", "agent/idle", "pending", "idle", 660),
	}
	got, err := detectStarvation(rows, starvationOptions{}, starvationNow, handledAt(nil))
	if err != nil || len(got) != 1 || got[0].Recipient != "agent/idle" || got[0].ThresholdSeconds != 600 {
		t.Fatalf("20 min busy must stay quiet and 11 min idle must alert: %+v %v", got, err)
	}
}

func TestStarvationQuietWhenSessionRecentlyHandledWork(t *testing.T) {
	rows := []attentionDelivery{starvationRow("d1", "agent/449", "pending", "busy", 7200)}
	recent := starvationNow.Add(-5 * time.Minute)
	got, err := detectStarvation(rows, starvationOptions{}, starvationNow, handledAt(map[string]time.Time{"agent/449": recent}))
	if err != nil || len(got) != 0 {
		t.Fatalf("a session that handled work 5 min ago is draining, not starved: %+v %v", got, err)
	}
	old := starvationNow.Add(-3 * time.Hour)
	got, err = detectStarvation(rows, starvationOptions{}, starvationNow, handledAt(map[string]time.Time{"agent/449": old}))
	if err != nil || len(got) != 1 || got[0].LastHandledAt == nil || !got[0].LastHandledAt.Equal(old) {
		t.Fatalf("old handling must not suppress and must be reported: %+v %v", got, err)
	}
}

func TestStarvationIgnoresOfflineScheduledAndNonSessionWork(t *testing.T) {
	for _, mode := range []string{"stopped", "offline", "replaced", "scheduled", "non-session", "handled"} {
		t.Run(mode, func(t *testing.T) {
			d := starvationRow("d1", "agent/x", "pending", "idle", 7200)
			switch mode {
			case "scheduled":
				d.AvailableAt = starvationNow.Add(time.Hour)
			case "non-session":
				d.Diagnosis.Recipient.Kind = "other"
			case "handled":
				d.State, d.Diagnosis.Stage = "handled", "handled"
			default:
				d.Diagnosis.Recipient.Presence = mode
			}
			got, err := detectStarvation([]attentionDelivery{d}, starvationOptions{}, starvationNow, handledAt(nil))
			if err != nil || len(got) != 0 {
				t.Fatalf("unexpected starvation: %+v %v", got, err)
			}
		})
	}
}

func heldRow(id, consumer string, nativeAge time.Duration, openTools *int) attentionDelivery {
	d := starvationRow(id, consumer, "leased", "busy", 0)
	d.Held, d.Diagnosis.Stage, d.Diagnosis.WaitingSeconds = true, "held", 0
	d.LatestNative = &attentionAttempt{State: "running", CreatedAt: starvationNow.Add(-nativeAge), OpenTools: openTools}
	return d
}

func TestStarvationReportsHoldsOlderThanAnHourWithNoOpenTools(t *testing.T) {
	zero, two := 0, 2
	rows := []attentionDelivery{
		heldRow("stuck", "agent/468", 12*time.Hour, &zero),
		heldRow("tooling", "agent/341", 5*time.Hour, &two),
		heldRow("young", "agent/510", 30*time.Minute, &zero),
	}
	recent := starvationNow.Add(-time.Minute)
	got, err := detectStarvation(rows, starvationOptions{}, starvationNow, handledAt(map[string]time.Time{"agent/468": recent}))
	if err != nil || len(got) != 1 {
		t.Fatalf("want only the 12h hold without tools, got %+v %v", got, err)
	}
	s := got[0]
	if s.Recipient != "agent/468" || s.Condition != "hold_stalled" || s.Count != 1 || s.OldestAgeSeconds != 12*3600 ||
		s.AgeBasis != "native_attempt" || s.ThresholdSeconds != 3600 || s.LastHandledAt == nil || s.Diagnosis == "" {
		t.Fatalf("unexpected hold report: %+v", s)
	}
}

func TestStarvationLeaseWithoutAttemptUsesAvailabilityAsUpperBound(t *testing.T) {
	d := starvationRow("lease", "agent/449", "leased", "busy", 0)
	d.Diagnosis.Stage = "leased"
	d.AvailableAt = starvationNow.Add(-9 * time.Hour)
	got, err := detectStarvation([]attentionDelivery{d}, starvationOptions{}, starvationNow, handledAt(nil))
	if err != nil || len(got) != 1 || got[0].Condition != "hold_stalled" || got[0].AgeBasis != "available_at" || got[0].OldestAgeSeconds != 9*3600 {
		t.Fatalf("lease with unknown start must be reported against availability: %+v %v", got, err)
	}
}

func TestStarvationSeparatesKindsAndExplainsHostCondition(t *testing.T) {
	notice := starvationRow("n", "agent/449", "pending", "busy", 4000)
	notice.Event.Kind = "notice"
	rows := []attentionDelivery{starvationRow("r", "agent/449", "pending", "busy", 3000), notice}
	got, err := detectStarvation(rows, starvationOptions{}, starvationNow, handledAt(nil))
	if err != nil || len(got) != 1 {
		t.Fatalf("got %+v %v", got, err)
	}
	s := got[0]
	if s.Requests != 1 || s.Notices != 1 || s.Responses != 0 || s.HostCondition != "busy" {
		t.Fatalf("kinds or host condition lost: %+v", s)
	}
	for _, want := range []string{"busy", "no handled delivery"} {
		if !strings.Contains(s.Diagnosis, want) {
			t.Fatalf("diagnosis %q lacks %q", s.Diagnosis, want)
		}
	}
	// Stale or inapplicable host reports are not repeated as the current cause.
	for i := range rows {
		rows[i].Diagnosis.Host.Freshness = "stale"
	}
	got, _ = detectStarvation(rows, starvationOptions{}, starvationNow, handledAt(nil))
	if got[0].HostCondition != "unknown" {
		t.Fatalf("stale host condition promoted: %+v", got[0])
	}
}

func TestStarvationThresholdBounds(t *testing.T) {
	for _, o := range []starvationOptions{{BusyAfterSeconds: -1}, {IdleAfterSeconds: 604801}, {HoldAfterSeconds: -5}} {
		if _, err := detectStarvation(nil, o, starvationNow, handledAt(nil)); err == nil {
			t.Fatalf("invalid thresholds accepted: %+v", o)
		}
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
		Attention *attentionResult `json:"attention"`
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
