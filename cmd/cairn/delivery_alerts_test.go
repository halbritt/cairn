package main

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

var starvationNow = time.Date(2026, 10, 21, 22, 0, 0, 0, time.UTC)

func itoa(n int) string { return strconv.Itoa(n) }

func starved(recipient, condition, oldest string) core.StarvedSession {
	return core.StarvedSession{Recipient: recipient, AgentID: "id-" + recipient, ExecutionID: "x-" + recipient, Condition: condition,
		RecipientState: "busy", Count: 2, Notices: 2, OldestAgeSeconds: 36000, OldestDeliveryID: oldest, AgeBasis: "waiting",
		ThresholdSeconds: 1800, HostCondition: "busy", Diagnosis: "Online busy session has 2 pending deliveries."}
}

type recordingNotifier struct {
	sent        []starvationAlert
	fail        map[string]bool
	closed      []string
	failResolve bool
}

func (r *recordingNotifier) notify(_ context.Context, a starvationAlert) (string, error) {
	if r.fail[a.Session.Recipient] {
		return "", errors.New("plane unavailable")
	}
	r.sent = append(r.sent, a)
	return "CAIRN-" + itoa(900+len(r.sent)), nil
}

func (r *recordingNotifier) resolve(_ context.Context, e deliveryAlertEntry, _ time.Time) error {
	if r.failResolve {
		return errors.New("plane unavailable")
	}
	r.closed = append(r.closed, e.Ref)
	return nil
}

func emptyAlertState() deliveryAlertState {
	return deliveryAlertState{Schema: deliveryAlertSchema, Scope: "repo", Alerts: map[string]deliveryAlertEntry{}}
}

func TestDeliveryAlertsNotifyOncePerNewCondition(t *testing.T) {
	n := &recordingNotifier{}
	now := starvationNow
	current := []core.StarvedSession{starved("agent/449", "pending_starved", "d1"), starved("agent/468", "hold_stalled", "h1")}
	res, state, err := applyDeliveryAlerts(context.Background(), current, true, emptyAlertState(), deliveryAlertLimits{MaxNew: 5}, n.notify, n.resolve, now)
	if err != nil || res.Sent != 2 || len(n.sent) != 2 || len(state.Alerts) != 2 {
		t.Fatalf("first run: %+v %+v %v", res, state, err)
	}
	if n.sent[0].ExternalID == "" || n.sent[0].ExternalID == n.sent[1].ExternalID || !strings.Contains(n.sent[0].Title, "agent/449") {
		t.Fatalf("alerts lack distinct idempotency keys or titles: %+v", n.sent)
	}
	// The same conditions on a later run, even with more deliveries, stay quiet.
	current[0].Count, current[0].OldestAgeSeconds = 30, 40000
	res, state, err = applyDeliveryAlerts(context.Background(), current, true, state, deliveryAlertLimits{MaxNew: 5}, n.notify, n.resolve, now.Add(10*time.Minute))
	if err != nil || res.Sent != 0 || res.Ongoing != 2 || len(n.sent) != 2 {
		t.Fatalf("ongoing condition repeated: %+v %v", res, err)
	}
	if state.Alerts[deliveryAlertKey(current[0])].Ref != "CAIRN-901" {
		t.Fatalf("alert reference lost: %+v", state.Alerts)
	}
}

func TestDeliveryAlertsClearOnlyOnCompleteScanAndRealertNewEpisode(t *testing.T) {
	n := &recordingNotifier{}
	cur := []core.StarvedSession{starved("agent/449", "pending_starved", "d1")}
	_, state, _ := applyDeliveryAlerts(context.Background(), cur, true, emptyAlertState(), deliveryAlertLimits{MaxNew: 5}, n.notify, n.resolve, starvationNow)
	res, state, err := applyDeliveryAlerts(context.Background(), nil, false, state, deliveryAlertLimits{MaxNew: 5}, n.notify, n.resolve, starvationNow)
	if err != nil || len(res.Cleared) != 0 || len(state.Alerts) != 1 {
		t.Fatalf("partial scan cleared a condition: %+v %v", res, err)
	}
	res, state, err = applyDeliveryAlerts(context.Background(), nil, true, state, deliveryAlertLimits{MaxNew: 5}, n.notify, n.resolve, starvationNow)
	if err != nil || len(res.Cleared) != 1 || len(state.Alerts) != 0 {
		t.Fatalf("complete scan did not clear: %+v %v", res, err)
	}
	cur[0].OldestDeliveryID = "d7"
	_, _, err = applyDeliveryAlerts(context.Background(), cur, true, state, deliveryAlertLimits{MaxNew: 5}, n.notify, n.resolve, starvationNow.Add(time.Hour))
	if err != nil || len(n.sent) != 2 || n.sent[0].ExternalID == n.sent[1].ExternalID {
		t.Fatalf("a recurrence is a new episode with a new key: %+v %v", n.sent, err)
	}
}

// Work items used to stay open forever: 24 of 26 open items were cleared conditions.
func TestDeliveryAlertsCloseWorkItemsOfClearedConditions(t *testing.T) {
	n := &recordingNotifier{}
	limits := deliveryAlertLimits{MaxNew: 5}
	cur := []core.StarvedSession{starved("agent/449", "pending_starved", "d1"), starved("agent/468", "hold_stalled", "h1")}
	_, state, err := applyDeliveryAlerts(context.Background(), cur, true, emptyAlertState(), limits, n.notify, n.resolve, starvationNow)
	if err != nil || len(n.sent) != 2 {
		t.Fatalf("seed: %+v %v", n.sent, err)
	}
	// A partial scan proves nothing about absence.
	res, state, err := applyDeliveryAlerts(context.Background(), nil, false, state, limits, n.notify, n.resolve, starvationNow)
	if err != nil || len(n.closed) != 0 || len(res.Resolved) != 0 || len(state.Alerts) != 2 {
		t.Fatalf("partial scan closed work items: %+v %v", res, err)
	}
	// A continuing condition stays open; only the cleared one closes.
	res, state, err = applyDeliveryAlerts(context.Background(), cur[:1], true, state, limits, n.notify, n.resolve, starvationNow)
	if err != nil || len(n.closed) != 1 || n.closed[0] != "CAIRN-902" || res.Ongoing != 1 || len(res.Resolved) != 1 || res.Resolved[0].Status != "closed" || len(state.Alerts) != 1 {
		t.Fatalf("close: %+v closed=%v %v", res, n.closed, err)
	}
	// A failed close surfaces, stays recorded and retries on the next run.
	n.failResolve = true
	res, state, err = applyDeliveryAlerts(context.Background(), nil, true, state, limits, n.notify, n.resolve, starvationNow)
	if err == nil || len(state.Alerts) != 1 || len(res.Cleared) != 0 || res.Resolved[0].Status != "failed" {
		t.Fatalf("failed close was forgotten: %+v %+v %v", res, state, err)
	}
	n.failResolve = false
	res, state, err = applyDeliveryAlerts(context.Background(), nil, true, state, limits, n.notify, n.resolve, starvationNow)
	if err != nil || len(state.Alerts) != 0 || len(res.Cleared) != 1 || len(n.closed) != 2 {
		t.Fatalf("retry: %+v %+v %v", res, state, err)
	}
	// Each run closes a bounded number; the rest wait for the next run.
	many := emptyAlertState()
	for i := 0; i < 4; i++ {
		many.Alerts["key"+itoa(i)] = deliveryAlertEntry{Recipient: "agent/" + itoa(i), Ref: "CAIRN-" + itoa(i)}
	}
	res, many, err = applyDeliveryAlerts(context.Background(), nil, true, many, deliveryAlertLimits{MaxNew: 3}, n.notify, n.resolve, starvationNow)
	if err != nil || len(res.Resolved) != 3 || res.Deferred != 1 || len(many.Alerts) != 1 {
		t.Fatalf("bound: %+v %+v %v", res, many, err)
	}
}

func TestPlaneResolverClosesAndTreatsDeletedItemsAsClosed(t *testing.T) {
	dir := t.TempDir()
	log := filepath.Join(dir, "calls")
	script := `#!/bin/sh
printf '%s\n' "$@" >> "` + log + `"
case "$*" in
  *CAIRN-404*) echo '{"ok":false,"error":{"code":"not_found","message":"gone","http_status":404}}'; exit 4;;
  *CAIRN-500*) echo '{"ok":false,"error":{"code":"server","message":"down","http_status":502}}'; exit 6;;
esac
echo '{"ok":true,"data":{}}'
`
	if err := os.WriteFile(filepath.Join(dir, "plane"), []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	resolve := planeResolver("CAIRN")
	if err := resolve(context.Background(), deliveryAlertEntry{Ref: "CAIRN-321"}, starvationNow); err != nil {
		t.Fatal(err)
	}
	calls, _ := os.ReadFile(log)
	for _, want := range []string{"issue\nupdate\nCAIRN-321\n-p\nCAIRN\n--state\nDone", "comment\nadd\nCAIRN-321", "session stopped"} {
		if !strings.Contains(string(calls), want) {
			t.Fatalf("plane calls lack %q:\n%s", want, calls)
		}
	}
	if err := resolve(context.Background(), deliveryAlertEntry{Ref: "CAIRN-404"}, starvationNow); err != nil {
		t.Fatalf("a deleted item must not fail every later run: %v", err)
	}
	if err := resolve(context.Background(), deliveryAlertEntry{Ref: "CAIRN-500"}, starvationNow); err == nil {
		t.Fatal("server failure reported as closed")
	}
	if err := resolve(context.Background(), deliveryAlertEntry{}, starvationNow); err != nil {
		t.Fatal(err)
	}
}

func TestDeliveryAlertsBoundedAndFailuresRetryLater(t *testing.T) {
	n := &recordingNotifier{fail: map[string]bool{"agent/b": true}}
	cur := []core.StarvedSession{starved("agent/a", "pending_starved", "1"), starved("agent/b", "pending_starved", "2"), starved("agent/c", "pending_starved", "3")}
	res, state, err := applyDeliveryAlerts(context.Background(), cur, true, emptyAlertState(), deliveryAlertLimits{MaxNew: 2}, n.notify, n.resolve, starvationNow)
	if err == nil || res.Sent != 1 || res.Failed != 1 || res.Deferred != 1 || len(state.Alerts) != 1 {
		t.Fatalf("want one sent, one failed (error), one deferred: %+v %v", res, err)
	}
	n.fail = nil
	res, state, err = applyDeliveryAlerts(context.Background(), cur, true, state, deliveryAlertLimits{MaxNew: 2}, n.notify, n.resolve, starvationNow)
	if err != nil || res.Sent != 2 || len(state.Alerts) != 3 {
		t.Fatalf("failed and deferred alerts not retried: %+v %v", res, err)
	}
}

func TestDeliveryAlertsReportOnlyWithoutNotifier(t *testing.T) {
	cur := []core.StarvedSession{starved("agent/a", "pending_starved", "1")}
	res, state, err := applyDeliveryAlerts(context.Background(), cur, true, emptyAlertState(), deliveryAlertLimits{MaxNew: 5}, nil, nil, starvationNow)
	if err != nil || res.Sent != 0 || len(res.New) != 1 || len(state.Alerts) != 0 {
		t.Fatalf("report-only run must not record alerts: %+v %+v %v", res, state, err)
	}
}

func TestPlaneNotifierCreatesIdempotentIssue(t *testing.T) {
	dir := t.TempDir()
	log := filepath.Join(dir, "calls")
	script := `#!/bin/sh
printf '%s\n' "$@" >> "` + log + `"
case "$*" in
  *dup-key*) echo '{"ok":false,"error":{"code":"conflict","message":"exists","http_status":409,"details":{"id":"existing-uuid"}}}'; exit 5;;
  *broken*) echo '{"ok":false,"error":{"code":"server","message":"down","http_status":502}}'; exit 6;;
esac
echo '{"ok":true,"data":{"id":"new-uuid","sequence_id":321}}'
`
	if err := os.WriteFile(filepath.Join(dir, "plane"), []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	notify := planeNotifier("CAIRN")
	alert := newStarvationAlert(starved("agent/449", "pending_starved", "d1"), "agent-449", starvationNow)
	ref, err := notify(context.Background(), alert)
	if err != nil || ref != "CAIRN-321" {
		t.Fatalf("create: %q %v", ref, err)
	}
	calls, _ := os.ReadFile(log)
	for _, want := range []string{"issue", "create", "-p", "CAIRN", "--name", alert.Title, "external_source", alert.ExternalID, "--description-html"} {
		if !strings.Contains(string(calls), want) {
			t.Fatalf("plane call lacks %q:\n%s", want, calls)
		}
	}
	alert.ExternalID = "dup-key"
	if ref, err = notify(context.Background(), alert); err != nil || ref != "existing-uuid" {
		t.Fatalf("an existing issue for this episode counts as notified: %q %v", ref, err)
	}
	alert.ExternalID = "broken"
	if _, err = notify(context.Background(), alert); err == nil {
		t.Fatal("server failure reported as success")
	}
}

func TestStarvationAlertTextNamesSessionAgeAndLastHandled(t *testing.T) {
	s := starved("agent/449", "pending_starved", "d1")
	at := starvationNow.Add(-11 * time.Hour)
	s.LastHandledAt = &at
	a := newStarvationAlert(s, "agent-449", starvationNow)
	if !strings.Contains(a.Title, "agent-449") || !strings.Contains(a.Title, "10h0m0s") {
		t.Fatalf("title: %q", a.Title)
	}
	for _, want := range []string{"agent-449", "d1", at.UTC().Format(time.RFC3339), "busy", "no recovery action", "coordination-review"} {
		if !strings.Contains(a.DescriptionHTML, want) {
			t.Fatalf("description lacks %q: %s", want, a.DescriptionHTML)
		}
	}
	s.Diagnosis = "<script>"
	if strings.Contains(newStarvationAlert(s, "<b>", starvationNow).DescriptionHTML, "<script>") {
		t.Fatal("description is not escaped")
	}
}

func TestDeliveryAlertsCommandAlertsOnceThroughPlane(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	t.Setenv("CAIRN_DATABASE_URL", dsn)
	ctx := context.Background()
	repo := "alerts-" + uuid.NewString()
	pub, err := core.Open(ctx, dsn, core.Channel{Principal: "agent/alerts-publisher", Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	defer pub.Close()
	if err := pub.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	host, err := core.Open(ctx, dsn, core.Channel{Principal: "agent/alerts-host", Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	defer host.Close()
	dest := core.Destination{Name: "hosted", AllowLocal: false}
	idle, err := host.RegisterAgent(ctx, core.RegisterAgentRequest{
		RequestID: uuid.NewString(), Binding: "idle-worker", NativeSessionID: uuid.NewString(),
		Metadata: core.AgentMetadata{Harness: "codex", Project: "fleet", Workspace: "/work/fleet", State: "idle", DeliveryMode: "existing-session"},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	note, err := pub.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "please review the patch", ClaimType: "self", Sensitivity: "shareable", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := pub.PublishEvent(ctx, core.PublishEventRequest{RequestID: uuid.NewString(), Repo: repo, Kind: "request", Ref: core.RecordVersionRef{RecordID: note.RecordID, Version: note.Version}, Destination: core.EventDestination{Type: "agent", Name: idle.Inbox}}, dest); err != nil {
		t.Fatal(err)
	}
	time.Sleep(1100 * time.Millisecond)
	if _, err := host.HeartbeatAgent(ctx, core.AgentSessionRef{AgentID: idle.AgentID, ExecutionID: idle.ExecutionID}, dest); err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	calls := filepath.Join(dir, "calls")
	fake := "#!/bin/sh\nprintf '%s\\n' \"$@\" >> '" + calls + "'\necho '{\"ok\":true,\"data\":{\"id\":\"u\",\"sequence_id\":77}}'\n"
	if err := os.WriteFile(filepath.Join(dir, "plane"), []byte(fake), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	request := `{"repo":"` + repo + `","state_file":"` + filepath.Join(dir, "alerts.json") + `","plane_project":"CAIRN","starvation":{"idle_after_seconds":1}}`
	decodeResult := func() deliveryAlertsResult {
		t.Helper()
		out, err := run(ctx, []string{"delivery-alerts"}, strings.NewReader(request))
		if err != nil {
			t.Fatal(err)
		}
		raw, _ := json.Marshal(out)
		var res deliveryAlertsResult
		if err := json.Unmarshal(raw, &res); err != nil {
			t.Fatal(err)
		}
		return res
	}
	first := decodeResult()
	if !first.Complete || first.Sent != 1 || len(first.StarvedSessions) != 1 || first.StarvedSessions[0].DisplayName != idle.DisplayName || first.New[0].Ref != "CAIRN-77" {
		t.Fatalf("first run: %+v", first)
	}
	second := decodeResult()
	if second.Sent != 0 || second.Ongoing != 1 {
		t.Fatalf("second run repeated the alert: %+v", second)
	}
	raw, _ := os.ReadFile(calls)
	if strings.Count(string(raw), "create\n") != 1 || !strings.Contains(string(raw), idle.DisplayName) {
		t.Fatalf("plane calls:\n%s", raw)
	}
}
