package main

import (
	"sort"
	"strconv"
	"time"
)

// Starvation is a read-only presentation of inbox work that an online session
// is not draining. It neither claims, releases, nor reissues deliveries.
type starvationOptions struct {
	BusyAfterSeconds int64 `json:"busy_after_seconds,omitempty"`
	IdleAfterSeconds int64 `json:"idle_after_seconds,omitempty"`
	HoldAfterSeconds int64 `json:"hold_after_seconds,omitempty"`
}

type starvedSession struct {
	Recipient        string     `json:"recipient"`
	AgentID          string     `json:"agent_id,omitempty"`
	ExecutionID      string     `json:"execution_id,omitempty"`
	DisplayName      string     `json:"display_name,omitempty"`
	Condition        string     `json:"condition"`
	RecipientState   string     `json:"recipient_state"`
	Count            int        `json:"count"`
	Requests         int        `json:"requests"`
	Responses        int        `json:"responses"`
	Notices          int        `json:"notices"`
	OldestAgeSeconds float64    `json:"oldest_age_seconds"`
	OldestDeliveryID string     `json:"oldest_delivery_id"`
	AgeBasis         string     `json:"age_basis"`
	ThresholdSeconds int64      `json:"threshold_seconds"`
	LastHandledAt    *time.Time `json:"last_handled_at"`
	HostCondition    string     `json:"host_condition,omitempty"`
	Diagnosis        string     `json:"diagnosis"`
}

func detectStarvation(rows []attentionDelivery, options starvationOptions, now time.Time, lastHandled func([]string) (map[string]time.Time, error)) ([]starvedSession, error) {
	busy, idle, hold := options.BusyAfterSeconds, options.IdleAfterSeconds, options.HoldAfterSeconds
	if busy == 0 {
		busy = 1800
	}
	if idle == 0 {
		idle = 600
	}
	if hold == 0 {
		hold = 3600
	}
	for _, v := range []int64{busy, idle, hold} {
		if v < 1 || v > 604800 {
			return nil, invalid("attention starvation thresholds must be 1..604800 seconds")
		}
	}
	groups := map[string]*starvedSession{}
	for _, d := range rows {
		diag := d.Diagnosis
		if diag == nil || diag.Recipient.Kind != "session" || diag.Recipient.Presence != "online" || d.AvailableAt.After(now) {
			continue
		}
		var condition, basis string
		var age float64
		var threshold int64
		switch {
		case (d.State == "pending" || d.State == "leased") && diag.Stage == "waiting":
			// An expired lease that the store reports as waiting is pending work again.
			condition, basis, age, threshold = "pending_starved", "waiting", diag.WaitingSeconds, busy
			if diag.Recipient.State == "idle" {
				threshold = idle
			}
		case d.State == "leased" || d.Held || diag.Stage == "held" || diag.Stage == "leased":
			if d.LatestNative != nil && d.LatestNative.OpenTools != nil && *d.LatestNative.OpenTools > 0 {
				continue // An open tool call is visible progress, however slow.
			}
			condition, threshold = "hold_stalled", hold
			basis, age = holdAge(d, now)
		default:
			continue
		}
		key := d.Consumer + "|" + diag.Recipient.ExecutionID + "|" + condition
		g := groups[key]
		if g == nil {
			g = &starvedSession{Recipient: d.Consumer, AgentID: diag.Recipient.AgentID, ExecutionID: diag.Recipient.ExecutionID,
				Condition: condition, RecipientState: diag.Recipient.State, ThresholdSeconds: threshold, AgeBasis: basis}
			groups[key] = g
		}
		if threshold < g.ThresholdSeconds {
			g.ThresholdSeconds = threshold
		}
		g.Count++
		switch d.Event.Kind {
		case "request":
			g.Requests++
		case "response":
			g.Responses++
		case "notice":
			g.Notices++
		}
		if g.HostCondition == "" || g.HostCondition == "unknown" {
			g.HostCondition = currentHostCondition(diag)
		}
		if age > g.OldestAgeSeconds || g.OldestDeliveryID == "" {
			g.OldestAgeSeconds, g.OldestDeliveryID, g.AgeBasis = age, d.DeliveryID, basis
		}
	}
	aged := []*starvedSession{}
	consumers := []string{}
	seen := map[string]bool{}
	for _, g := range groups {
		if g.OldestAgeSeconds < float64(g.ThresholdSeconds) {
			continue
		}
		aged = append(aged, g)
		if !seen[g.Recipient] {
			seen[g.Recipient] = true
			consumers = append(consumers, g.Recipient)
		}
	}
	handled := map[string]time.Time{}
	if len(consumers) > 0 {
		sort.Strings(consumers)
		var err error
		if handled, err = lastHandled(consumers); err != nil {
			return nil, err
		}
	}
	out := []starvedSession{}
	for _, g := range aged {
		if at, ok := handled[g.Recipient]; ok {
			at := at
			g.LastHandledAt = &at
			if g.Condition == "pending_starved" && now.Sub(at) < time.Duration(g.ThresholdSeconds)*time.Second {
				continue // The session handled inbox work recently; it is draining, if slowly.
			}
		}
		g.Diagnosis = starvationDiagnosis(*g)
		out = append(out, *g)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Recipient < out[j].Recipient })
	return out, nil
}

// holdAge measures from the latest native or wake attempt. Without either,
// availability is an upper bound on how long the lease has been held.
func holdAge(d attentionDelivery, now time.Time) (string, float64) {
	since, basis := d.AvailableAt, "available_at"
	if d.LatestNative != nil && !d.LatestNative.CreatedAt.IsZero() {
		since, basis = d.LatestNative.CreatedAt, "native_attempt"
	} else if d.LatestWake != nil && !d.LatestWake.CreatedAt.IsZero() {
		since, basis = d.LatestWake.CreatedAt, "wake_attempt"
	}
	if since.IsZero() || since.After(now) {
		return basis, 0
	}
	return basis, float64(int64(now.Sub(since) / time.Second))
}

// currentHostCondition repeats a host report only while it is current and
// applies to this session or delivery; otherwise the cause is unknown.
func currentHostCondition(diag *attentionDiagnosis) string {
	if h := diag.Host; h != nil && h.Freshness == "current" && (h.Applies == "exact" || h.Applies == "session") && h.Condition != "" && h.Condition != "none" {
		return h.Condition
	}
	return "unknown"
}

func starvationDiagnosis(g starvedSession) string {
	age := (time.Duration(g.OldestAgeSeconds) * time.Second).String()
	handled := "no handled delivery is recorded for this inbox"
	if g.LastHandledAt != nil {
		handled = "no handled delivery since " + g.LastHandledAt.UTC().Format(time.RFC3339)
	}
	if g.Condition == "hold_stalled" {
		basis := map[string]string{"native_attempt": "since its native attempt started", "wake_attempt": "since its wake attempt", "available_at": "at most since it became available"}[g.AgeBasis]
		return "Online " + g.RecipientState + " session holds " + strconv.Itoa(g.Count) + " leased or held deliveries; the oldest has been held " + age + " " + basis +
			" with no open tool call, and " + handled + ". The hold is not visibly progressing; inspect the native turn and hold before any release. This report takes no action."
	}
	text := "Online " + g.RecipientState + " session has " + strconv.Itoa(g.Count) + " pending deliveries; the oldest has waited " + age + ", and " + handled + ". "
	switch g.HostCondition {
	case "busy":
		text += "The host reports the session busy, so automatic delivery waits for an idle boundary that has not come; a long busy turn is starving its inbox."
	default:
		text += "Host condition " + g.HostCondition + ": " + hostConditionHint(g.HostCondition)
	}
	return text + " This report takes no action."
}
