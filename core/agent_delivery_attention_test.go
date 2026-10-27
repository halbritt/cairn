package core

import (
	"strings"
	"testing"
	"time"
)

// These tests cross the exported attention interface only: SummarizeAttention
// and DetectStarvation over ReviewDelivery rows as coordination-review and
// delivery-alerts render them. The CLI keeps presentation tests only.

var starvationNow = time.Date(2026, 10, 21, 22, 0, 0, 0, time.UTC)

func waitingSeconds(n int64) *int64 { return &n }

// starvationRow builds one review row as coordination-review renders it.
func starvationRow(id, consumer, state, recipientState string, waiting int64) ReviewDelivery {
	return ReviewDelivery{
		DeliveryID:  id,
		Consumer:    consumer,
		State:       state,
		AvailableAt: time.Date(2026, 10, 21, 12, 0, 0, 0, time.UTC),
		Event:       AgentEvent{Kind: "request"},
		Diagnosis: &DeliveryDiagnosis{
			Stage:          "waiting",
			WaitingSeconds: waitingSeconds(waiting),
			Recipient:      DiagnosisRecipient{Kind: "session", AgentID: "a-" + consumer, ExecutionID: "x-" + consumer, State: recipientState, Presence: "online"},
			Host:           &DiagnosisHost{Condition: "busy", Applies: "session", Freshness: "current"},
		},
	}
}

func handledAt(m map[string]time.Time) func([]string) (map[string]time.Time, error) {
	return func([]string) (map[string]time.Time, error) { return m, nil }
}

func heldRow(id, consumer string, nativeAge time.Duration, openTools *int) ReviewDelivery {
	d := starvationRow(id, consumer, "leased", "busy", 0)
	d.Held, d.Diagnosis.Stage, d.Diagnosis.WaitingSeconds = true, "held", waitingSeconds(0)
	d.LatestNative = &ReviewAttempt{State: "running", CreatedAt: starvationNow.Add(-nativeAge), OpenTools: openTools}
	return d
}

func attentionFixture(id, kind, condition string) ReviewDelivery {
	return ReviewDelivery{
		DeliveryID: id,
		Consumer:   "agent/fixture",
		State:      "pending",
		Event:      AgentEvent{Kind: kind},
		Diagnosis: &DeliveryDiagnosis{
			Stage:          "waiting",
			WaitingSeconds: waitingSeconds(900),
			Recipient:      DiagnosisRecipient{Kind: "session", State: "idle", Presence: "online"},
			Host:           &DiagnosisHost{Condition: condition, Applies: "exact", Freshness: "current"},
		},
	}
}

func emptyAttentionCache() AttentionCache {
	return AttentionCache{Schema: "cairn.delivery-attention/1", Scope: "fixture", Deliveries: map[string]AttentionEntry{}}
}

func TestStarvationReportsAgedPendingForOnlineBusySession(t *testing.T) {
	rows := []ReviewDelivery{
		starvationRow("d1", "agent/449", "pending", "busy", 36000),
		starvationRow("d2", "agent/449", "pending", "busy", 2400),
		starvationRow("d3", "agent/449", "pending", "busy", 60),
	}
	got, err := DetectStarvation(rows, StarvationOptions{}, starvationNow, handledAt(nil))
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
	rows := []ReviewDelivery{
		starvationRow("busy20m", "agent/busy", "pending", "busy", 1200),
		starvationRow("idle11m", "agent/idle", "pending", "idle", 660),
	}
	got, err := DetectStarvation(rows, StarvationOptions{}, starvationNow, handledAt(nil))
	if err != nil || len(got) != 1 || got[0].Recipient != "agent/idle" || got[0].ThresholdSeconds != 600 {
		t.Fatalf("20 min busy must stay quiet and 11 min idle must alert: %+v %v", got, err)
	}
}

func TestStarvationQuietWhenSessionRecentlyHandledWork(t *testing.T) {
	rows := []ReviewDelivery{starvationRow("d1", "agent/449", "pending", "busy", 7200)}
	recent := starvationNow.Add(-5 * time.Minute)
	got, err := DetectStarvation(rows, StarvationOptions{}, starvationNow, handledAt(map[string]time.Time{"agent/449": recent}))
	if err != nil || len(got) != 0 {
		t.Fatalf("a session that handled work 5 min ago is draining, not starved: %+v %v", got, err)
	}
	old := starvationNow.Add(-3 * time.Hour)
	got, err = DetectStarvation(rows, StarvationOptions{}, starvationNow, handledAt(map[string]time.Time{"agent/449": old}))
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
			got, err := DetectStarvation([]ReviewDelivery{d}, StarvationOptions{}, starvationNow, handledAt(nil))
			if err != nil || len(got) != 0 {
				t.Fatalf("unexpected starvation: %+v %v", got, err)
			}
		})
	}
}

func TestStarvationReportsHoldsOlderThanAnHourWithNoOpenTools(t *testing.T) {
	zero, two := 0, 2
	rows := []ReviewDelivery{
		heldRow("stuck", "agent/468", 12*time.Hour, &zero),
		heldRow("tooling", "agent/341", 5*time.Hour, &two),
		heldRow("young", "agent/510", 30*time.Minute, &zero),
	}
	recent := starvationNow.Add(-time.Minute)
	got, err := DetectStarvation(rows, StarvationOptions{}, starvationNow, handledAt(map[string]time.Time{"agent/468": recent}))
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
	got, err := DetectStarvation([]ReviewDelivery{d}, StarvationOptions{}, starvationNow, handledAt(nil))
	if err != nil || len(got) != 1 || got[0].Condition != "hold_stalled" || got[0].AgeBasis != "available_at" || got[0].OldestAgeSeconds != 9*3600 {
		t.Fatalf("lease with unknown start must be reported against availability: %+v %v", got, err)
	}
}

func TestStarvationSeparatesKindsAndExplainsHostCondition(t *testing.T) {
	notice := starvationRow("n", "agent/449", "pending", "busy", 4000)
	notice.Event.Kind = "notice"
	rows := []ReviewDelivery{starvationRow("r", "agent/449", "pending", "busy", 3000), notice}
	got, err := DetectStarvation(rows, StarvationOptions{}, starvationNow, handledAt(nil))
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
	got, _ = DetectStarvation(rows, StarvationOptions{}, starvationNow, handledAt(nil))
	if got[0].HostCondition != "unknown" {
		t.Fatalf("stale host condition promoted: %+v", got[0])
	}
}

func TestStarvationThresholdBounds(t *testing.T) {
	for _, o := range []StarvationOptions{{BusyAfterSeconds: -1}, {IdleAfterSeconds: 604801}, {HoldAfterSeconds: -5}} {
		if _, err := DetectStarvation(nil, o, starvationNow, handledAt(nil)); err == nil {
			t.Fatalf("invalid thresholds accepted: %+v", o)
		}
	}
}

func TestAttentionSeparatesKindsAndSuppressesUnchanged(t *testing.T) {
	now := time.Now()
	request := attentionFixture("request", "request", "wake_retained")
	notice := attentionFixture("notice", "notice", "wake_retained")
	result, cache, err := SummarizeAttention([]ReviewDelivery{request, notice}, true, AttentionOptions{}, emptyAttentionCache(), now)
	if err != nil || len(result.Changed) != 1 || result.Groups[0].Requests != 1 || result.Groups[0].Notices != 1 {
		t.Fatalf("first: %+v %v", result, err)
	}
	*request.Diagnosis.WaitingSeconds += 100
	notice.DeliveryID = "new-notice"
	result, cache, err = SummarizeAttention([]ReviewDelivery{request, notice}, true, AttentionOptions{}, cache, now)
	if err != nil || len(result.Changed) != 0 || len(cache.Deliveries) != 1 {
		t.Fatalf("unchanged request repeated: %+v %v", result, err)
	}
	second := request
	second.DeliveryID = "second-request"
	result, _, err = SummarizeAttention([]ReviewDelivery{request, second}, true, AttentionOptions{}, cache, now)
	if err != nil || len(result.Changed) != 1 || result.Changed[0].Requests != 2 {
		t.Fatalf("new request not visible: %+v %v", result, err)
	}
}

func TestAttentionPartialAbsenceCannotClearButProgressCan(t *testing.T) {
	now := time.Now()
	request := attentionFixture("one", "request", "wake_retained")
	_, cache, _ := SummarizeAttention([]ReviewDelivery{request}, true, AttentionOptions{}, emptyAttentionCache(), now)
	result, partial, err := SummarizeAttention(nil, false, AttentionOptions{}, cache, now)
	if err != nil || len(result.Cleared) != 0 || len(partial.Deliveries) != 1 || result.Complete {
		t.Fatalf("partial erased condition: %+v %v", result, err)
	}
	request.State = "handled"
	request.Diagnosis.Stage = "handled"
	result, progress, err := SummarizeAttention([]ReviewDelivery{request}, false, AttentionOptions{}, partial, now)
	if err != nil || len(result.Cleared) != 1 || len(progress.Deliveries) != 0 {
		t.Fatalf("explicit progress failed: %+v %v", result, err)
	}
	result, _, err = SummarizeAttention(nil, true, AttentionOptions{}, cache, now)
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
				d.Diagnosis.WaitingSeconds = waitingSeconds(5)
			case "notice", "response":
				d.Event.Kind = mode
			case "non-session":
				d.Diagnosis.Recipient.Kind = "other"
			}
			result, _, err := SummarizeAttention([]ReviewDelivery{d}, true, AttentionOptions{}, emptyAttentionCache(), now)
			if err != nil || len(result.Changed) != 0 {
				t.Fatalf("unexpected attention: %+v %v", result, err)
			}
		})
	}
}

func TestAttentionAvailableRouteDoesNotProveProgress(t *testing.T) {
	d := attentionFixture("one", "request", "automatic_available")
	result, _, err := SummarizeAttention([]ReviewDelivery{d}, true, AttentionOptions{}, emptyAttentionCache(), time.Now())
	if err != nil || len(result.Changed) != 1 || !strings.Contains(result.Changed[0].NextCheck, "no claim") {
		t.Fatalf("available route hid aged pending request: %+v %v", result, err)
	}
}

func TestAttentionIncludesExpiredLeaseWhenStoreSaysWaiting(t *testing.T) {
	d := attentionFixture("one", "request", "unknown")
	d.State = "leased"
	result, _, err := SummarizeAttention([]ReviewDelivery{d}, true, AttentionOptions{}, emptyAttentionCache(), time.Now())
	if err != nil || len(result.Changed) != 1 {
		t.Fatalf("expired lease disappeared: %+v %v", result, err)
	}
	d.Diagnosis.Stage = "leased"
	result, _, err = SummarizeAttention([]ReviewDelivery{d}, true, AttentionOptions{}, emptyAttentionCache(), time.Now())
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
			result, _, err := SummarizeAttention([]ReviewDelivery{d}, true, AttentionOptions{}, emptyAttentionCache(), time.Now())
			if err != nil || len(result.Changed) != 1 || result.Changed[0].Condition != "unknown" {
				t.Fatalf("stale cause promoted: %+v %v", result, err)
			}
		})
	}
}

func TestAttentionBounds(t *testing.T) {
	_, _, err := SummarizeAttention(nil, true, AttentionOptions{OlderThanSeconds: -1}, emptyAttentionCache(), time.Now())
	if err == nil {
		t.Fatal("negative cutoff accepted")
	}
	cache := emptyAttentionCache()
	for i := 0; i < 1001; i++ {
		cache.Deliveries[string(rune(i))] = AttentionEntry{Group: "known"}
	}
	if _, _, err = SummarizeAttention(nil, false, AttentionOptions{}, cache, time.Now()); err == nil {
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
			d.Diagnosis.WaitingSeconds = waitingSeconds(5) // Even young deliveries on a stopped session are stranded.
			d.Diagnosis.Host.Freshness = "offline"
			result, cache, err := SummarizeAttention([]ReviewDelivery{d}, true, AttentionOptions{}, emptyAttentionCache(), now)
			if err != nil || len(result.Changed) != 1 || result.Changed[0].Condition != "recipient_stopped" || len(result.StoppedSessions) != 1 {
				t.Fatalf("stopped %s missed: %+v %v", kind, result, err)
			}
			// Unchanged repeat is suppressed.
			repeat, _, err := SummarizeAttention([]ReviewDelivery{d}, true, AttentionOptions{}, cache, now)
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
		result, _, err := SummarizeAttention([]ReviewDelivery{d}, true, AttentionOptions{}, emptyAttentionCache(), now)
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
	held.Diagnosis.WaitingSeconds = waitingSeconds(0)
	held.AvailableAt = now.Add(-2 * time.Minute)
	held.Diagnosis.Recipient.Presence = "stopped"
	held.Diagnosis.Host = nil

	result, cache, err := SummarizeAttention([]ReviewDelivery{held}, false, AttentionOptions{}, emptyAttentionCache(), now)
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
	changed, _, err := SummarizeAttention([]ReviewDelivery{held}, true, AttentionOptions{}, cache, now)
	if err != nil || len(changed.Changed) != 1 || !changed.StoppedSessions[0].Complete {
		t.Fatalf("stage change not reported: %+v %v", changed, err)
	}
}

func TestAttentionAgent463ThirtyFiveNoticesFixture(t *testing.T) {
	now := time.Now()
	const oldestID = "473dfc21-3361-4ea3-8563-7849d13720c6"
	deliveries := make([]ReviewDelivery, 0, 35)
	for i := 0; i < 35; i++ {
		id := oldestID
		applies := "exact"
		if i > 0 {
			id = "00000000-0000-0000-0000-0000000000" + string(rune('a'+i/10)) + string(rune('0'+i%10))
			applies = "other_delivery"
		}
		deliveries = append(deliveries, ReviewDelivery{
			DeliveryID: id,
			Consumer:   "agent/46300000-0000-0000-0000-000000000463",
			State:      "pending",
			Attempts:   0,
			Execution:  "never_launched",
			Event:      AgentEvent{EventID: "evt-" + id, From: "agent/integrator449", Kind: "notice", Ref: RecordVersionRef{RecordID: "rec-" + id, Version: 1}},
			Diagnosis: &DeliveryDiagnosis{
				Stage:          "waiting",
				WaitingSeconds: waitingSeconds(int64(1800 - i*10)),
				Recipient:      DiagnosisRecipient{Kind: "session", AgentID: "46300000-0000-0000-0000-000000000463", ExecutionID: "exec-463", State: "idle", Presence: "stopped"},
				Host:           &DiagnosisHost{Condition: "unknown", Reported: "wake_retained", Applies: applies, Freshness: "offline", Wake: &ObservedWake{Transport: "codex-queue", Status: "refused"}},
			},
		})
	}
	result, cache, err := SummarizeAttention(deliveries, true, AttentionOptions{}, emptyAttentionCache(), now)
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
	repeat, _, err := SummarizeAttention(deliveries, true, AttentionOptions{}, cache, now)
	if err != nil || len(repeat.Changed) != 0 || len(repeat.StoppedSessions) != 1 {
		t.Fatalf("repeat should suppress changed while keeping stopped_sessions view: %+v %v", repeat, err)
	}
}

func TestAttentionDistinguishesMissingFromAmbiguousTerminalTargets(t *testing.T) {
	checks := map[string]string{}
	for _, condition := range []string{"terminal_target_missing", "terminal_target_not_idle", "terminal_target_focused", "terminal_target_ambiguous", "terminal_host_unavailable"} {
		result, _, err := SummarizeAttention([]ReviewDelivery{attentionFixture("one", "request", condition)}, true, AttentionOptions{}, emptyAttentionCache(), time.Now())
		if err != nil || len(result.Changed) != 1 || result.Changed[0].Condition != condition {
			t.Fatalf("%s: %+v %v", condition, result, err)
		}
		next := result.Changed[0].NextCheck
		for other, seen := range checks {
			if seen == next {
				t.Fatalf("%s and %s share next check %q", condition, other, next)
			}
		}
		if !strings.Contains(next, "not acknowledgement") {
			t.Fatalf("%s next check does not say waiting is not acknowledgement: %q", condition, next)
		}
		checks[condition] = next
	}
}
