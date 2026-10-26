package core

import (
	"context"
	"sort"
	"strconv"
	"time"

	"github.com/google/uuid"
)

// StarvationOptions configures thresholds for detecting online sessions that
// are not draining their inbox; zero values use the documented defaults.
type StarvationOptions struct {
	BusyAfterSeconds int64 `json:"busy_after_seconds,omitempty"`
	IdleAfterSeconds int64 `json:"idle_after_seconds,omitempty"`
	HoldAfterSeconds int64 `json:"hold_after_seconds,omitempty"`
}

// AttentionOptions configures one bounded operator review attention evaluation.
type AttentionOptions struct {
	StateFile        string            `json:"state_file,omitempty"`
	OlderThanSeconds int64             `json:"older_than_seconds,omitempty"`
	NotifyPublishers bool              `json:"notify_publishers,omitempty"`
	Starvation       StarvationOptions `json:"starvation,omitempty"`
}

type AttentionGroup struct {
	Recipient            string     `json:"recipient"`
	Condition            string     `json:"condition"`
	Freshness            string     `json:"freshness"`
	Applies              string     `json:"applies"`
	Requests             int        `json:"requests"`
	Responses            int        `json:"responses"`
	Notices              int        `json:"notices"`
	OldestWaitingSeconds float64    `json:"oldest_waiting_seconds"`
	SampleDeliveryID     string     `json:"sample_delivery_id"`
	ObservedAt           *time.Time `json:"observed_at,omitempty"`
	NextCheck            string     `json:"next_check"`
}

type StoppedSessionRef struct {
	RecordID string `json:"record_id"`
	Version  int    `json:"version"`
}

type StoppedSessionWake struct {
	DeliveryID  string     `json:"delivery_id,omitempty"`
	Transport   string     `json:"transport"`
	Status      string     `json:"status"`
	AttemptedAt *time.Time `json:"attempted_at,omitempty"`
	Freshness   string     `json:"freshness"`
	Applies     string     `json:"applies"`
}

type StoppedSessionDelivery struct {
	DeliveryID        string              `json:"delivery_id"`
	EventID           string              `json:"event_id,omitempty"`
	From              string              `json:"from,omitempty"`
	Kind              string              `json:"kind"`
	State             string              `json:"state"`
	Stage             string              `json:"stage"`
	Attempts          int                 `json:"attempts"`
	Held              bool                `json:"held"`
	Execution         string              `json:"execution,omitempty"`
	Admission         string              `json:"admission"`
	WaitingSeconds    float64             `json:"waiting_seconds"`
	Ref               *StoppedSessionRef  `json:"ref,omitempty"`
	ReportedCondition string              `json:"reported_condition,omitempty"`
	Freshness         string              `json:"freshness,omitempty"`
	Applies           string              `json:"applies,omitempty"`
	RetainedWake      *StoppedSessionWake `json:"retained_wake,omitempty"`
}

type StoppedSessionBacklog struct {
	Recipient            string                   `json:"recipient"`
	AgentID              string                   `json:"agent_id,omitempty"`
	ExecutionID          string                   `json:"execution_id,omitempty"`
	Presence             string                   `json:"presence"`
	Requests             int                      `json:"requests"`
	Responses            int                      `json:"responses"`
	Notices              int                      `json:"notices"`
	Held                 int                      `json:"held"`
	Unattempted          int                      `json:"unattempted"`
	OldestWaitingSeconds float64                  `json:"oldest_waiting_seconds"`
	OldestDeliveryID     string                   `json:"oldest_delivery_id"`
	RetainedWake         *StoppedSessionWake      `json:"retained_wake,omitempty"`
	AdmissionUncertainty string                   `json:"admission_uncertainty"`
	DecisionOwner        string                   `json:"decision_owner"`
	SuccessorHint        string                   `json:"successor_hint,omitempty"`
	Complete             bool                     `json:"complete"`
	Coverage             string                   `json:"coverage"`
	Deliveries           []StoppedSessionDelivery `json:"deliveries"`
}

type PublisherNotificationBatch struct {
	Attempted   int                                 `json:"attempted"`
	Sent        int                                 `json:"sent"`
	Suppressed  int                                 `json:"suppressed"`
	Unavailable int                                 `json:"unavailable"`
	Skipped     int                                 `json:"skipped"`
	Deferred    int                                 `json:"deferred"`
	Results     []StoppedDeliveryNotificationResult `json:"results"`
}

type StarvedSession struct {
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

type AttentionResult struct {
	Complete               bool                        `json:"complete"`
	Scanned                int                         `json:"scanned"`
	OlderThanSeconds       int64                       `json:"older_than_seconds"`
	Groups                 []AttentionGroup            `json:"groups"`
	Changed                []AttentionGroup            `json:"changed"`
	Cleared                []string                    `json:"cleared"`
	StoppedSessions        []StoppedSessionBacklog     `json:"stopped_sessions,omitempty"`
	StarvedSessions        []StarvedSession            `json:"starved_sessions"`
	PublisherNotifications *PublisherNotificationBatch `json:"publisher_notifications,omitempty"`
	Coverage               string                      `json:"coverage"`
}

type AttentionEntry struct {
	Group               string `json:"group"`
	Fingerprint         string `json:"fingerprint"`
	NotifiedFingerprint string `json:"notified_fingerprint,omitempty"`
	NotificationEventID string `json:"notification_event_id,omitempty"`
}

type AttentionCache struct {
	Schema     string                    `json:"schema"`
	Scope      string                    `json:"scope"`
	Deliveries map[string]AttentionEntry `json:"deliveries"`
}

func AttentionKey(g AttentionGroup) string {
	return g.Recipient + "|" + g.Condition + "|" + g.Freshness + "|" + g.Applies
}

func diagnosisWaitingSeconds(diag *DeliveryDiagnosis) float64 {
	if diag == nil || diag.WaitingSeconds == nil {
		return 0
	}
	return float64(*diag.WaitingSeconds)
}

func AttentionCandidate(d ReviewDelivery, cutoff int64, now time.Time) (AttentionGroup, bool) {
	diag := d.Diagnosis
	if diag == nil || diag.Recipient.Kind != "session" || d.AvailableAt.After(now) {
		return AttentionGroup{}, false
	}
	waiting := diagnosisWaitingSeconds(diag)
	if diag.Recipient.Presence == "stopped" {
		if (d.State != "pending" && d.State != "leased" && !d.Held && diag.Stage != "held") || diag.Stage == "handled" || diag.Stage == "failed" || diag.Stage == "ignored" {
			return AttentionGroup{}, false
		}
		if waiting == 0 && !d.AvailableAt.IsZero() && now.After(d.AvailableAt) {
			waiting = float64(int64(now.Sub(d.AvailableAt) / time.Second))
		}
		g := AttentionGroup{
			Recipient:            d.Consumer,
			Condition:            "recipient_stopped",
			Freshness:            "offline",
			Applies:              "session",
			OldestWaitingSeconds: waiting,
			SampleDeliveryID:     d.DeliveryID,
			NextCheck:            "Recipient session is stopped; inspect stranded inbox work and decide whether to reissue, release holds, or notify publishers.",
		}
		if h := diag.Host; h != nil && h.ObservedAt != nil {
			g.ObservedAt = h.ObservedAt
		}
		switch d.Event.Kind {
		case "request":
			g.Requests = 1
		case "response":
			g.Responses = 1
		case "notice":
			g.Notices = 1
		default:
			return AttentionGroup{}, false
		}
		return g, true
	}
	if (d.State != "pending" && d.State != "leased") || diag.Stage != "waiting" || diag.Recipient.State == "busy" || waiting < float64(cutoff) {
		return AttentionGroup{}, false
	}
	g := AttentionGroup{Recipient: d.Consumer, Condition: "unknown", Freshness: "absent", Applies: "none", OldestWaitingSeconds: waiting, SampleDeliveryID: d.DeliveryID}
	if h := diag.Host; h != nil {
		g.Freshness = h.Freshness
		g.Applies = h.Applies
		g.ObservedAt = h.ObservedAt
		if h.Freshness == "current" && (h.Applies == "exact" || h.Applies == "session") {
			g.Condition = h.Condition
		}
	}
	if g.Condition == "busy" {
		return AttentionGroup{}, false
	}
	if g.Condition == "none" || g.Condition == "" {
		g.Condition = "unknown"
	}
	g.NextCheck = HostConditionHint(g.Condition)
	switch d.Event.Kind {
	case "request":
		g.Requests = 1
	case "response":
		g.Responses = 1
	case "notice":
		g.Notices = 1
	default:
		return AttentionGroup{}, false
	}
	return g, true
}

func HostConditionHint(condition string) string {
	switch condition {
	case "automatic_available":
		return "A route was observed available, but no claim followed; inspect the native wake and claim path."
	case "wake_retained", "native_admission_retained":
		return "Inspect this delivery's retained wake and native handling; no new claim observed."
	case "owner_turn_required":
		return "This recipient needs an owner turn for native delivery."
	case "terminal_target_missing":
		return "No Herdr pane identifies this session; check that the session still runs in its own pane. Waiting is not acknowledgement."
	case "terminal_target_not_idle":
		return "This session's Herdr pane is not idle; the wake waits for an idle boundary. Waiting is not acknowledgement."
	case "terminal_target_focused":
		return "This session's idle Herdr pane is focused; terminal wake defers to the owner. Waiting is not acknowledgement."
	case "terminal_target_ambiguous":
		return "Several eligible Herdr panes match this session; no pane is chosen. Waiting is not acknowledgement."
	case "terminal_host_unavailable":
		return "The session's process has no Herdr host environment; no terminal wake is available. Waiting is not acknowledgement."
	case "unknown":
		return "Check current recipient and host observations; the cause of delay is unknown."
	default:
		return "Inspect the recipient's native delivery configuration and watcher diagnostics."
	}
}

func SummarizeAttention(deliveries []ReviewDelivery, complete bool, options AttentionOptions, previous AttentionCache, now time.Time) (AttentionResult, AttentionCache, error) {
	cutoff := options.OlderThanSeconds
	if cutoff == 0 {
		cutoff = 300
	}
	if cutoff < 1 || cutoff > 604800 {
		return AttentionResult{}, previous, failure("INVALID_REQUEST", "attention older_than_seconds must be 1..604800")
	}
	result := AttentionResult{
		Complete:         complete,
		Scanned:          len(deliveries),
		OlderThanSeconds: cutoff,
		Groups:           []AttentionGroup{},
		Changed:          []AttentionGroup{},
		Cleared:          []string{},
		StoppedSessions:  []StoppedSessionBacklog{},
		Coverage:         "This delivery page only; queued pool work and other pages are excluded.",
	}
	if !complete {
		result.Coverage += " Partial snapshot: absence does not clear a previous condition."
	}
	current := AttentionCache{Schema: "cairn.delivery-attention/1", Scope: previous.Scope, Deliveries: map[string]AttentionEntry{}}
	if !complete {
		for id, e := range previous.Deliveries {
			current.Deliveries[id] = e
		}
	}
	groups := map[string]*AttentionGroup{}
	changed := map[string]bool{}
	stoppedByRecipient := map[string]*StoppedSessionBacklog{}
	for _, d := range deliveries {
		old, known := previous.Deliveries[d.DeliveryID]
		delete(current.Deliveries, d.DeliveryID) // An explicitly observed row can supersede a prior cause even on a partial page.
		group, ok := AttentionCandidate(d, cutoff, now)
		if !ok {
			continue
		}
		key := AttentionKey(group)
		if existing := groups[key]; existing != nil {
			existing.Requests += group.Requests
			existing.Responses += group.Responses
			existing.Notices += group.Notices
			if group.OldestWaitingSeconds > existing.OldestWaitingSeconds {
				existing.OldestWaitingSeconds = group.OldestWaitingSeconds
				existing.SampleDeliveryID = group.SampleDeliveryID
			}
		} else {
			cp := group
			groups[key] = &cp
		}
		if group.Condition == "recipient_stopped" {
			recordStoppedSessionDelivery(stoppedByRecipient, d, group, complete)
		}
		if group.Requests == 0 && group.Condition != "recipient_stopped" {
			continue
		} // Ordinary notices/responses to active or offline sessions never create an attention change.
		entry := AttentionEntry{
			Group:               key,
			Fingerprint:         attentionDeliveryFingerprint(d, group, key),
			NotifiedFingerprint: old.NotifiedFingerprint,
			NotificationEventID: old.NotificationEventID,
		}
		current.Deliveries[d.DeliveryID] = entry
		if !known || old.Fingerprint != entry.Fingerprint {
			changed[key] = true
		}
	}
	if len(current.Deliveries) > 1000 {
		return result, previous, failure("INVALID_REQUEST", "attention state exceeds 1000 requests; use a narrower review filter or separate state file")
	}
	finalizeAttentionResult(&result, groups, changed, stoppedByRecipient, previous, current)
	return result, current, nil
}

func finalizeAttentionResult(result *AttentionResult, groups map[string]*AttentionGroup, changed map[string]bool, stoppedByRecipient map[string]*StoppedSessionBacklog, previous, current AttentionCache) {
	keys := make([]string, 0, len(groups))
	for key := range groups {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		g := *groups[key]
		result.Groups = append(result.Groups, g)
		if changed[key] {
			result.Changed = append(result.Changed, g)
		}
	}
	stoppedKeys := make([]string, 0, len(stoppedByRecipient))
	for recipient := range stoppedByRecipient {
		stoppedKeys = append(stoppedKeys, recipient)
	}
	sort.Strings(stoppedKeys)
	for _, recipient := range stoppedKeys {
		result.StoppedSessions = append(result.StoppedSessions, *stoppedByRecipient[recipient])
	}
	oldGroups := map[string]bool{}
	newGroups := map[string]bool{}
	for _, entry := range previous.Deliveries {
		oldGroups[entry.Group] = true
	}
	for _, entry := range current.Deliveries {
		newGroups[entry.Group] = true
	}
	for key := range oldGroups {
		if !newGroups[key] {
			result.Cleared = append(result.Cleared, key)
		}
	}
	sort.Strings(result.Cleared)
}

func stoppedDeliveryDetails(d ReviewDelivery, waiting float64) (StoppedSessionDelivery, string) {
	diag := d.Diagnosis
	held := d.Held || diag.Stage == "held"
	admission := "not_admitted"
	if held || diag.Stage == "leased" || d.Attempts > 0 || d.Execution == "may_have_executed" {
		admission = "unknown"
	}
	var reported, freshness, applies, wakeSig string
	var retained *StoppedSessionWake
	if h := diag.Host; h != nil {
		freshness = h.Freshness
		applies = h.Applies
		if h.Applies == "exact" || h.Applies == "session" {
			if h.Reported != "" && h.Reported != "none" {
				reported = h.Reported
			} else if h.Condition != "" && h.Condition != "unknown" && h.Condition != "none" {
				reported = h.Condition
			}
			if reported == "wake_retained" || reported == "native_admission_retained" || h.Wake != nil {
				admission = "unknown"
			}
			if h.Wake != nil {
				retained = &StoppedSessionWake{
					DeliveryID:  d.DeliveryID,
					Transport:   h.Wake.Transport,
					Status:      h.Wake.Status,
					AttemptedAt: h.Wake.AttemptedAt,
					Freshness:   h.Freshness,
					Applies:     h.Applies,
				}
				wakeSig = h.Wake.Transport + ":" + h.Wake.Status
			}
		}
	}
	var ref *StoppedSessionRef
	if d.Event.Ref.RecordID != "" {
		ref = &StoppedSessionRef{RecordID: d.Event.Ref.RecordID, Version: d.Event.Ref.Version}
	}
	item := StoppedSessionDelivery{
		DeliveryID: d.DeliveryID, EventID: d.Event.EventID, From: d.Event.From,
		Kind: d.Event.Kind, State: d.State, Stage: diag.Stage, Attempts: d.Attempts,
		Held: held, Execution: d.Execution, Admission: admission, WaitingSeconds: waiting,
		Ref: ref, ReportedCondition: reported, Freshness: freshness, Applies: applies,
		RetainedWake: retained,
	}
	sig := diag.Stage + "|" + d.Execution + "|" + admission + "|" + reported + "|" + wakeSig
	return item, sig
}

func attentionDeliveryFingerprint(d ReviewDelivery, group AttentionGroup, key string) string {
	if group.Condition != "recipient_stopped" {
		return key
	}
	_, sig := stoppedDeliveryDetails(d, group.OldestWaitingSeconds)
	return key + "|" + sig
}

func recordStoppedSessionDelivery(byRecipient map[string]*StoppedSessionBacklog, d ReviewDelivery, group AttentionGroup, complete bool) {
	item, _ := stoppedDeliveryDetails(d, group.OldestWaitingSeconds)
	entry := byRecipient[d.Consumer]
	if entry == nil {
		coverage := "Complete for this review filter; all matching deliveries fit in this page."
		if !complete {
			coverage = "Partial page: additional deliveries for this or other stopped sessions may exist beyond this cursor."
		}
		entry = &StoppedSessionBacklog{
			Recipient:            d.Consumer,
			AgentID:              d.Diagnosis.Recipient.AgentID,
			ExecutionID:          d.Diagnosis.Recipient.ExecutionID,
			Presence:             "stopped",
			OldestWaitingSeconds: group.OldestWaitingSeconds,
			OldestDeliveryID:     d.DeliveryID,
			AdmissionUncertainty: "not_admitted",
			DecisionOwner:        "operator_or_publisher",
			Complete:             complete,
			Coverage:             coverage,
			Deliveries:           []StoppedSessionDelivery{},
		}
		byRecipient[d.Consumer] = entry
	}
	entry.Requests += group.Requests
	entry.Responses += group.Responses
	entry.Notices += group.Notices
	if item.Held {
		entry.Held++
	}
	if d.Attempts == 0 {
		entry.Unattempted++
	}
	if group.OldestWaitingSeconds > entry.OldestWaitingSeconds {
		entry.OldestWaitingSeconds = group.OldestWaitingSeconds
		entry.OldestDeliveryID = d.DeliveryID
	}
	if item.Admission == "unknown" {
		entry.AdmissionUncertainty = "unknown"
	}
	if entry.RetainedWake == nil && item.RetainedWake != nil {
		cp := *item.RetainedWake
		entry.RetainedWake = &cp
	}
	entry.Deliveries = append(entry.Deliveries, item)
}

const maxNotificationsPerReview = 64

func NotifyStoppedPublishers(ctx context.Context, store *Store, repo string, deliveries []ReviewDelivery, previous, current AttentionCache, now time.Time) (PublisherNotificationBatch, AttentionCache, error) {
	batch := PublisherNotificationBatch{Results: []StoppedDeliveryNotificationResult{}}
	for _, d := range deliveries {
		group, ok := AttentionCandidate(d, 1, now)
		if !ok || group.Condition != "recipient_stopped" {
			continue
		}
		_, sig := stoppedDeliveryDetails(d, group.OldestWaitingSeconds)
		reqID := uuid.NewSHA1(uuid.NameSpaceOID, []byte("cairn.stopped-delivery-notice/1|"+repo+"|"+d.DeliveryID+"|"+d.Diagnosis.Recipient.AgentID+"|"+d.Diagnosis.Recipient.ExecutionID+"|"+sig)).String()
		old := previous.Deliveries[d.DeliveryID]
		cur := current.Deliveries[d.DeliveryID]
		if old.NotifiedFingerprint == sig && sig != "" {
			batch.Suppressed++
			batch.Results = append(batch.Results, StoppedDeliveryNotificationResult{
				DeliveryID: d.DeliveryID, EventID: d.Event.EventID, Publisher: d.Event.From,
				NotificationID: reqID, NotificationEventID: old.NotificationEventID, Status: "duplicate_suppressed",
			})
			continue
		}
		if batch.Attempted >= maxNotificationsPerReview {
			batch.Deferred++
			continue
		}
		batch.Attempted++
		out, err := store.NotifyStoppedDeliveryPublisher(ctx, StoppedDeliveryNotificationRequest{
			RequestID: reqID, Repo: repo, DeliveryID: d.DeliveryID,
			RecipientAgentID: d.Diagnosis.Recipient.AgentID, RecipientExecution: d.Diagnosis.Recipient.ExecutionID,
			Stage: d.Diagnosis.Stage, Fingerprint: sig,
		})
		if err != nil {
			return batch, previous, err
		}
		switch out.Status {
		case "sent":
			batch.Sent++
			cur.NotifiedFingerprint, cur.NotificationEventID = sig, out.NotificationEventID
			current.Deliveries[d.DeliveryID] = cur
		case "duplicate_suppressed":
			batch.Suppressed++
			cur.NotifiedFingerprint, cur.NotificationEventID = sig, out.NotificationEventID
			current.Deliveries[d.DeliveryID] = cur
		case "recipient_unavailable":
			batch.Unavailable++
		default:
			batch.Skipped++
		}
		batch.Results = append(batch.Results, out)
	}
	return batch, current, nil
}

func DetectStarvation(rows []ReviewDelivery, options StarvationOptions, now time.Time, lastHandled func([]string) (map[string]time.Time, error)) ([]StarvedSession, error) {
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
			return nil, failure("INVALID_REQUEST", "attention starvation thresholds must be 1..604800 seconds")
		}
	}
	groups := map[string]*StarvedSession{}
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
			condition, basis, age, threshold = "pending_starved", "waiting", diagnosisWaitingSeconds(diag), busy
			if diag.Recipient.State == "idle" {
				threshold = idle
			}
		case d.State == "leased" || d.Held || diag.Stage == "held" || diag.Stage == "leased":
			if d.LatestNative != nil && d.LatestNative.OpenTools != nil && *d.LatestNative.OpenTools > 0 {
				continue
			}
			condition, threshold = "hold_stalled", hold
			basis, age = holdAge(d, now)
		default:
			continue
		}
		key := d.Consumer + "|" + diag.Recipient.ExecutionID + "|" + condition
		g := groups[key]
		if g == nil {
			g = &StarvedSession{Recipient: d.Consumer, AgentID: diag.Recipient.AgentID, ExecutionID: diag.Recipient.ExecutionID,
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
	return filterStarvedSessions(groups, now, lastHandled)
}

func filterStarvedSessions(groups map[string]*StarvedSession, now time.Time, lastHandled func([]string) (map[string]time.Time, error)) ([]StarvedSession, error) {
	aged := []*StarvedSession{}
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
	if len(consumers) > 0 && lastHandled != nil {
		sort.Strings(consumers)
		var err error
		if handled, err = lastHandled(consumers); err != nil {
			return nil, err
		}
	}
	out := []StarvedSession{}
	for _, g := range aged {
		if at, ok := handled[g.Recipient]; ok {
			at := at
			g.LastHandledAt = &at
			if g.Condition == "pending_starved" && now.Sub(at) < time.Duration(g.ThresholdSeconds)*time.Second {
				continue
			}
		}
		g.Diagnosis = starvationDiagnosis(*g)
		out = append(out, *g)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Recipient < out[j].Recipient })
	return out, nil
}

func holdAge(d ReviewDelivery, now time.Time) (string, float64) {
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

func currentHostCondition(diag *DeliveryDiagnosis) string {
	if h := diag.Host; h != nil && h.Freshness == "current" && (h.Applies == "exact" || h.Applies == "session") && h.Condition != "" && h.Condition != "none" {
		return h.Condition
	}
	return "unknown"
}

func starvationDiagnosis(g StarvedSession) string {
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
		text += "Host condition " + g.HostCondition + ": " + HostConditionHint(g.HostCondition)
	}
	return text + " This report takes no action."
}

// ScanStarvationDeliveries reads bounded pages of the review states that can
// starve a session directly as ReviewDelivery values without JSON round-trips.
func (s *Store) ScanStarvationDeliveries(ctx context.Context, repo string, maxPages int) ([]ReviewDelivery, int, bool, error) {
	seen := map[string]bool{}
	var rows []ReviewDelivery
	complete := true
	for _, state := range []string{"pending", "leased", "held"} {
		var after int64
		for page := 0; ; page++ {
			if page == maxPages {
				complete = false
				break
			}
			got, err := s.ReviewEvents(ctx, EventReviewRequest{Repo: repo, State: state, After: after, Limit: 100})
			if err != nil {
				return nil, 0, false, err
			}
			for _, d := range got.Deliveries {
				if !seen[d.DeliveryID] {
					seen[d.DeliveryID] = true
					rows = append(rows, d)
				}
			}
			if !got.More {
				break
			}
			after = got.NextAfter
		}
	}
	return rows, len(rows), complete, nil
}
