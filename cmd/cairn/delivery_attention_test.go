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

func attentionFixture(id, kind, condition string) attentionDelivery {
	var d attentionDelivery
	err := json.Unmarshal([]byte(`{"delivery_id":"`+id+`","consumer":"agent/fixture","state":"pending","event":{"kind":"`+kind+`"},"diagnosis":{"stage":"waiting","waiting_seconds":900,"recipient":{"kind":"session","state":"idle","presence":"online"},"host":{"condition":"`+condition+`","applies":"exact","freshness":"current"}}}`), &d)
	if err != nil {
		panic(err)
	}
	return d
}
func emptyAttentionCache() attentionCache {
	return attentionCache{Schema: "cairn.delivery-attention/1", Scope: "fixture", Deliveries: map[string]attentionEntry{}}
}

func TestAttentionSeparatesKindsAndSuppressesUnchanged(t *testing.T) {
	now := time.Now()
	request := attentionFixture("request", "request", "wake_retained")
	notice := attentionFixture("notice", "notice", "wake_retained")
	result, cache, err := summarizeAttention([]attentionDelivery{request, notice}, true, attentionOptions{}, emptyAttentionCache(), now)
	if err != nil || len(result.Changed) != 1 || result.Groups[0].Requests != 1 || result.Groups[0].Notices != 1 {
		t.Fatalf("first: %+v %v", result, err)
	}
	request.Diagnosis.WaitingSeconds += 100
	notice.DeliveryID = "new-notice"
	result, cache, err = summarizeAttention([]attentionDelivery{request, notice}, true, attentionOptions{}, cache, now)
	if err != nil || len(result.Changed) != 0 || len(cache.Deliveries) != 1 {
		t.Fatalf("unchanged request repeated: %+v %v", result, err)
	}
	second := request
	second.DeliveryID = "second-request"
	result, _, err = summarizeAttention([]attentionDelivery{request, second}, true, attentionOptions{}, cache, now)
	if err != nil || len(result.Changed) != 1 || result.Changed[0].Requests != 2 {
		t.Fatalf("new request not visible: %+v %v", result, err)
	}
}

func TestAttentionPartialAbsenceCannotClearButProgressCan(t *testing.T) {
	now := time.Now()
	request := attentionFixture("one", "request", "wake_retained")
	_, cache, _ := summarizeAttention([]attentionDelivery{request}, true, attentionOptions{}, emptyAttentionCache(), now)
	result, partial, err := summarizeAttention(nil, false, attentionOptions{}, cache, now)
	if err != nil || len(result.Cleared) != 0 || len(partial.Deliveries) != 1 || result.Complete {
		t.Fatalf("partial erased condition: %+v %v", result, err)
	}
	request.State = "handled"
	request.Diagnosis.Stage = "handled"
	result, progress, err := summarizeAttention([]attentionDelivery{request}, false, attentionOptions{}, partial, now)
	if err != nil || len(result.Cleared) != 1 || len(progress.Deliveries) != 0 {
		t.Fatalf("explicit progress failed: %+v %v", result, err)
	}
	result, _, err = summarizeAttention(nil, true, attentionOptions{}, cache, now)
	if err != nil || len(result.Cleared) != 1 {
		t.Fatalf("complete absence failed: %+v %v", result, err)
	}
}

func TestAttentionQuietForIntentionalWaiting(t *testing.T) {
	now := time.Now()
	for _, mode := range []string{"busy", "scheduled", "young", "notice", "response", "non-session"} {
		t.Run(mode, func(t *testing.T) {
			d := attentionFixture("one", "request", "native_transport_unavailable")
			switch mode {
			case "busy":
				d.Diagnosis.Recipient.State = "busy"
			case "scheduled":
				d.AvailableAt = now.Add(time.Minute)
			case "young":
				d.Diagnosis.WaitingSeconds = 5
			case "notice", "response":
				d.Event.Kind = mode
			case "non-session":
				d.Diagnosis.Recipient.Kind = "other"
			}
			result, _, err := summarizeAttention([]attentionDelivery{d}, true, attentionOptions{}, emptyAttentionCache(), now)
			if err != nil || len(result.Changed) != 0 {
				t.Fatalf("unexpected attention: %+v %v", result, err)
			}
		})
	}
}

func TestAttentionAvailableRouteDoesNotProveProgress(t *testing.T) {
	d := attentionFixture("one", "request", "automatic_available")
	result, _, err := summarizeAttention([]attentionDelivery{d}, true, attentionOptions{}, emptyAttentionCache(), time.Now())
	if err != nil || len(result.Changed) != 1 || !strings.Contains(result.Changed[0].NextCheck, "no claim") {
		t.Fatalf("available route hid aged pending request: %+v %v", result, err)
	}
}

func TestAttentionIncludesExpiredLeaseWhenStoreSaysWaiting(t *testing.T) {
	d := attentionFixture("one", "request", "unknown")
	d.State = "leased"
	result, _, err := summarizeAttention([]attentionDelivery{d}, true, attentionOptions{}, emptyAttentionCache(), time.Now())
	if err != nil || len(result.Changed) != 1 {
		t.Fatalf("expired lease disappeared: %+v %v", result, err)
	}
	d.Diagnosis.Stage = "leased"
	result, _, err = summarizeAttention([]attentionDelivery{d}, true, attentionOptions{}, emptyAttentionCache(), time.Now())
	if err != nil || len(result.Changed) != 0 {
		t.Fatalf("live lease alerted: %+v %v", result, err)
	}
}

func TestAttentionUnknownDoesNotRepeatOldCauseAsCurrent(t *testing.T) {
	for _, mode := range []string{"stale", "replaced", "offline", "absent", "other_delivery"} {
		t.Run(mode, func(t *testing.T) {
			d := attentionFixture("one", "request", "channel_not_launched")
			if mode == "other_delivery" {
				d.Diagnosis.Host.Applies = mode
			} else {
				d.Diagnosis.Host.Freshness = mode
			}
			result, _, err := summarizeAttention([]attentionDelivery{d}, true, attentionOptions{}, emptyAttentionCache(), time.Now())
			if err != nil || len(result.Changed) != 1 || result.Changed[0].Condition != "unknown" {
				t.Fatalf("stale cause promoted: %+v %v", result, err)
			}
		})
	}
}

func TestAttentionCacheScopesAndBoundedPrivateState(t *testing.T) {
	path := filepath.Join(t.TempDir(), "attention.json")
	options := attentionOptions{StateFile: path}
	calls := 0
	run := func(c attentionCache) (attentionResult, attentionCache, error) {
		calls++
		return summarizeAttention([]attentionDelivery{attentionFixture("one", "request", "wake_retained")}, true, options, c, time.Now())
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

func TestAttentionBounds(t *testing.T) {
	_, _, err := summarizeAttention(nil, true, attentionOptions{OlderThanSeconds: -1}, emptyAttentionCache(), time.Now())
	if err == nil {
		t.Fatal("negative cutoff accepted")
	}
	cache := emptyAttentionCache()
	for i := 0; i < 1001; i++ {
		cache.Deliveries[string(rune(i))] = attentionEntry{Group: "known"}
	}
	if _, _, err = summarizeAttention(nil, false, attentionOptions{}, cache, time.Now()); err == nil {
		t.Fatal("unbounded partial cache")
	}
}

func TestAttentionStoppedSessionAllKindsAndOfflineDistinction(t *testing.T) {
	now := time.Now()
	for _, kind := range []string{"notice", "response", "request"} {
		t.Run("stopped-"+kind, func(t *testing.T) {
			d := attentionFixture("del-"+kind, kind, "unknown")
			d.Diagnosis.Recipient.Presence = "stopped"
			d.Diagnosis.Recipient.AgentID = "46300000-0000-0000-0000-000000000463"
			d.Diagnosis.Recipient.ExecutionID = "exec-463"
			d.Diagnosis.WaitingSeconds = 5 // Even young deliveries on a stopped session are stranded.
			d.Diagnosis.Host.Freshness = "offline"
			result, cache, err := summarizeAttention([]attentionDelivery{d}, true, attentionOptions{}, emptyAttentionCache(), now)
			if err != nil || len(result.Changed) != 1 || result.Changed[0].Condition != "recipient_stopped" || len(result.StoppedSessions) != 1 {
				t.Fatalf("stopped %s missed: %+v %v", kind, result, err)
			}
			// Unchanged repeat is suppressed.
			repeat, _, err := summarizeAttention([]attentionDelivery{d}, true, attentionOptions{}, cache, now)
			if err != nil || len(repeat.Changed) != 0 || len(repeat.StoppedSessions) != 1 {
				t.Fatalf("stopped %s repeat: %+v %v", kind, repeat, err)
			}
		})
	}
	// Transiently offline presence does not become confirmed stop or alert on notices/responses.
	for _, kind := range []string{"notice", "response"} {
		d := attentionFixture("offline-"+kind, kind, "unknown")
		d.Diagnosis.Recipient.Presence = "offline"
		d.Diagnosis.Host.Freshness = "offline"
		result, _, err := summarizeAttention([]attentionDelivery{d}, true, attentionOptions{}, emptyAttentionCache(), now)
		if err != nil || len(result.Changed) != 0 || len(result.StoppedSessions) != 0 {
			t.Fatalf("transient offline %s promoted to stopped: %+v %v", kind, result, err)
		}
	}
}

func TestAttentionStoppedSessionRetainedHoldAndPagination(t *testing.T) {
	now := time.Now()
	held := attentionFixture("held-notice", "notice", "unknown")
	held.Held = true
	held.Diagnosis.Stage = "held"
	held.Diagnosis.WaitingSeconds = 0
	held.AvailableAt = now.Add(-2 * time.Minute)
	held.Diagnosis.Recipient.Presence = "stopped"
	held.Diagnosis.Host = nil

	result, cache, err := summarizeAttention([]attentionDelivery{held}, false, attentionOptions{}, emptyAttentionCache(), now)
	if err != nil || len(result.Changed) != 1 || len(result.StoppedSessions) != 1 {
		t.Fatalf("held stopped delivery missed: %+v %v", result, err)
	}
	ss := result.StoppedSessions[0]
	if ss.Held != 1 || ss.AdmissionUncertainty != "unknown" || ss.Complete || !strings.Contains(ss.Coverage, "Partial page") || ss.Deliveries[0].Stage != "held" {
		t.Fatalf("held stopped backlog: %+v", ss)
	}
	// Stage transition from held to waiting triggers a changed condition.
	held.Held = false
	held.Diagnosis.Stage = "waiting"
	changed, _, err := summarizeAttention([]attentionDelivery{held}, true, attentionOptions{}, cache, now)
	if err != nil || len(changed.Changed) != 1 || !changed.StoppedSessions[0].Complete {
		t.Fatalf("stage change not reported: %+v %v", changed, err)
	}
}

func TestAttentionAgent463ThirtyFiveNoticesFixture(t *testing.T) {
	now := time.Now()
	const oldestID = "473dfc21-3361-4ea3-8563-7849d13720c6"
	deliveries := make([]attentionDelivery, 0, 35)
	for i := 0; i < 35; i++ {
		id := oldestID
		applies := "exact"
		if i > 0 {
			id = "00000000-0000-0000-0000-0000000000" + string(rune('a'+i/10)) + string(rune('0'+i%10))
			applies = "other_delivery"
		}
		var d attentionDelivery
		err := json.Unmarshal([]byte(`{"delivery_id":"`+id+`","consumer":"agent/46300000-0000-0000-0000-000000000463","state":"pending","attempts":0,"execution":"never_launched","event":{"event_id":"evt-`+id+`","from":"agent/integrator449","kind":"notice","ref":{"record_id":"rec-`+id+`","version":1}},"diagnosis":{"stage":"waiting","waiting_seconds":1800,"recipient":{"kind":"session","agent_id":"46300000-0000-0000-0000-000000000463","execution_id":"exec-463","state":"idle","presence":"stopped"},"host":{"condition":"unknown","reported":"wake_retained","applies":"`+applies+`","freshness":"offline","wake":{"transport":"codex-queue","status":"refused"}}}}`), &d)
		if err != nil {
			t.Fatal(err)
		}
		d.Diagnosis.WaitingSeconds = float64(1800 - i*10)
		deliveries = append(deliveries, d)
	}
	result, cache, err := summarizeAttention(deliveries, true, attentionOptions{}, emptyAttentionCache(), now)
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Groups) != 1 || len(result.Changed) != 1 || result.Changed[0].Condition != "recipient_stopped" || result.Changed[0].Notices != 35 || result.Changed[0].SampleDeliveryID != oldestID {
		t.Fatalf("expected 1 changed recipient_stopped group with 35 notices: %+v", result)
	}
	if len(result.StoppedSessions) != 1 {
		t.Fatalf("expected 1 stopped session backlog: %+v", result.StoppedSessions)
	}
	ss := result.StoppedSessions[0]
	if ss.Notices != 35 || ss.Requests != 0 || ss.Responses != 0 || ss.Unattempted != 35 || ss.OldestDeliveryID != oldestID || ss.AdmissionUncertainty != "unknown" || len(ss.Deliveries) != 35 {
		t.Fatalf("unexpected stopped session summary: %+v", ss)
	}
	if ss.RetainedWake == nil || ss.RetainedWake.DeliveryID != oldestID || ss.RetainedWake.Transport != "codex-queue" || ss.RetainedWake.Status != "refused" || ss.RetainedWake.Freshness != "offline" || ss.RetainedWake.Applies != "exact" {
		t.Fatalf("unexpected retained wake: %+v", ss.RetainedWake)
	}
	if ss.Deliveries[0].Admission != "unknown" || ss.Deliveries[1].Admission != "not_admitted" {
		t.Fatalf("unexpected per-delivery admission: first=%+v second=%+v", ss.Deliveries[0], ss.Deliveries[1])
	}
	repeat, _, err := summarizeAttention(deliveries, true, attentionOptions{}, cache, now)
	if err != nil || len(repeat.Changed) != 0 || len(repeat.StoppedSessions) != 1 {
		t.Fatalf("repeat should suppress changed while keeping stopped_sessions view: %+v %v", repeat, err)
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
		Attention *attentionResult `json:"attention"`
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
