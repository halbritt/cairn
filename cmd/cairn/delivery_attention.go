package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"syscall"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

// Attention is an opt-in presentation of one bounded operator review page.
// It neither claims deliveries nor changes the host's recovery decisions.
type attentionOptions struct {
	StateFile        string `json:"state_file,omitempty"`
	OlderThanSeconds int64  `json:"older_than_seconds,omitempty"`
	NotifyPublishers bool   `json:"notify_publishers,omitempty"`
}

type attentionDelivery struct {
	DeliveryID  string    `json:"delivery_id"`
	Consumer    string    `json:"consumer"`
	State       string    `json:"state"`
	Attempts    int       `json:"attempts"`
	Held        bool      `json:"held"`
	Execution   string    `json:"execution"`
	AvailableAt time.Time `json:"available_at"`
	Event       struct {
		EventID   string    `json:"event_id"`
		From      string    `json:"from"`
		Kind      string    `json:"kind"`
		CreatedAt time.Time `json:"created_at"`
		Ref       struct {
			RecordID string `json:"record_id"`
			Version  int    `json:"version"`
		} `json:"ref"`
	} `json:"event"`
	Diagnosis *attentionDiagnosis `json:"diagnosis"`
}

type attentionDiagnosis struct {
	Stage          string  `json:"stage"`
	WaitingSeconds float64 `json:"waiting_seconds"`
	Recipient      struct {
		Kind        string `json:"kind"`
		AgentID     string `json:"agent_id"`
		ExecutionID string `json:"execution_id"`
		State       string `json:"state"`
		Presence    string `json:"presence"`
	} `json:"recipient"`
	Host *struct {
		Condition  string     `json:"condition"`
		Reported   string     `json:"reported"`
		Applies    string     `json:"applies"`
		Freshness  string     `json:"freshness"`
		ObservedAt *time.Time `json:"observed_at"`
		ReceivedAt *time.Time `json:"received_at"`
		Wake       *struct {
			Transport   string     `json:"transport"`
			Status      string     `json:"status"`
			AttemptedAt *time.Time `json:"attempted_at,omitempty"`
		} `json:"wake,omitempty"`
	} `json:"host"`
}

type attentionGroup struct {
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

type stoppedSessionRef struct {
	RecordID string `json:"record_id"`
	Version  int    `json:"version"`
}

type stoppedSessionWake struct {
	DeliveryID  string     `json:"delivery_id,omitempty"`
	Transport   string     `json:"transport"`
	Status      string     `json:"status"`
	AttemptedAt *time.Time `json:"attempted_at,omitempty"`
	Freshness   string     `json:"freshness"`
	Applies     string     `json:"applies"`
}

type stoppedSessionDelivery struct {
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
	Ref               *stoppedSessionRef  `json:"ref,omitempty"`
	ReportedCondition string              `json:"reported_condition,omitempty"`
	Freshness         string              `json:"freshness,omitempty"`
	Applies           string              `json:"applies,omitempty"`
	RetainedWake      *stoppedSessionWake `json:"retained_wake,omitempty"`
}

type stoppedSessionBacklog struct {
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
	RetainedWake         *stoppedSessionWake      `json:"retained_wake,omitempty"`
	AdmissionUncertainty string                   `json:"admission_uncertainty"`
	DecisionOwner        string                   `json:"decision_owner"`
	SuccessorHint        string                   `json:"successor_hint,omitempty"`
	Complete             bool                     `json:"complete"`
	Coverage             string                   `json:"coverage"`
	Deliveries           []stoppedSessionDelivery `json:"deliveries"`
}

type publisherNotificationBatch struct {
	Attempted   int                                      `json:"attempted"`
	Sent        int                                      `json:"sent"`
	Suppressed  int                                      `json:"suppressed"`
	Unavailable int                                      `json:"unavailable"`
	Skipped     int                                      `json:"skipped"`
	Deferred    int                                      `json:"deferred"`
	Results     []core.StoppedDeliveryNotificationResult `json:"results"`
}

type attentionResult struct {
	Complete               bool                        `json:"complete"`
	Scanned                int                         `json:"scanned"`
	OlderThanSeconds       int64                       `json:"older_than_seconds"`
	Groups                 []attentionGroup            `json:"groups"`
	Changed                []attentionGroup            `json:"changed"`
	Cleared                []string                    `json:"cleared"`
	StoppedSessions        []stoppedSessionBacklog     `json:"stopped_sessions,omitempty"`
	PublisherNotifications *publisherNotificationBatch `json:"publisher_notifications,omitempty"`
	Coverage               string                      `json:"coverage"`
}

type attentionEntry struct {
	Group               string `json:"group"`
	Fingerprint         string `json:"fingerprint"`
	NotifiedFingerprint string `json:"notified_fingerprint,omitempty"`
	NotificationEventID string `json:"notification_event_id,omitempty"`
}
type attentionCache struct {
	Schema     string                    `json:"schema"`
	Scope      string                    `json:"scope"`
	Deliveries map[string]attentionEntry `json:"deliveries"`
}

func attentionKey(g attentionGroup) string {
	return g.Recipient + "|" + g.Condition + "|" + g.Freshness + "|" + g.Applies
}

func attentionCandidate(d attentionDelivery, cutoff int64, now time.Time) (attentionGroup, bool) {
	diag := d.Diagnosis
	if diag == nil || diag.Recipient.Kind != "session" || d.AvailableAt.After(now) {
		return attentionGroup{}, false
	}
	if diag.Recipient.Presence == "stopped" {
		if (d.State != "pending" && d.State != "leased" && !d.Held && diag.Stage != "held") || diag.Stage == "handled" || diag.Stage == "failed" || diag.Stage == "ignored" {
			return attentionGroup{}, false
		}
		waiting := diag.WaitingSeconds
		if waiting == 0 && !d.AvailableAt.IsZero() && now.After(d.AvailableAt) {
			waiting = float64(int64(now.Sub(d.AvailableAt) / time.Second))
		}
		g := attentionGroup{
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
			return attentionGroup{}, false
		}
		return g, true
	}
	if (d.State != "pending" && d.State != "leased") || diag.Stage != "waiting" || diag.Recipient.State == "busy" || diag.WaitingSeconds < float64(cutoff) {
		return attentionGroup{}, false
	}
	g := attentionGroup{Recipient: d.Consumer, Condition: "unknown", Freshness: "absent", Applies: "none", OldestWaitingSeconds: diag.WaitingSeconds, SampleDeliveryID: d.DeliveryID}
	if h := diag.Host; h != nil {
		g.Freshness = h.Freshness
		g.Applies = h.Applies
		g.ObservedAt = h.ObservedAt
		if h.Freshness == "current" && (h.Applies == "exact" || h.Applies == "session") {
			g.Condition = h.Condition
		}
	}
	if g.Condition == "busy" {
		return attentionGroup{}, false
	}
	if g.Condition == "none" || g.Condition == "" {
		g.Condition = "unknown"
	}
	switch g.Condition {
	case "automatic_available":
		g.NextCheck = "A route was observed available, but no claim followed; inspect the native wake and claim path."
	case "wake_retained", "native_admission_retained":
		g.NextCheck = "Inspect this delivery's retained wake and native handling; no new claim observed."
	case "owner_turn_required":
		g.NextCheck = "This recipient needs an owner turn for native delivery."
	case "terminal_target_missing":
		g.NextCheck = "No Herdr pane identifies this session; check that the session still runs in its own pane. Waiting is not acknowledgement."
	case "terminal_target_not_idle":
		g.NextCheck = "This session's Herdr pane is not idle; the wake waits for an idle boundary. Waiting is not acknowledgement."
	case "terminal_target_focused":
		g.NextCheck = "This session's idle Herdr pane is focused; terminal wake defers to the owner. Waiting is not acknowledgement."
	case "terminal_target_ambiguous":
		g.NextCheck = "Several eligible Herdr panes match this session; no pane is chosen. Waiting is not acknowledgement."
	case "terminal_host_unavailable":
		g.NextCheck = "The session's process has no Herdr host environment; no terminal wake is available. Waiting is not acknowledgement."
	case "unknown":
		g.NextCheck = "Check current recipient and host observations; the cause of delay is unknown."
	default:
		g.NextCheck = "Inspect the recipient's native delivery configuration and watcher diagnostics."
	}
	switch d.Event.Kind {
	case "request":
		g.Requests = 1
	case "response":
		g.Responses = 1
	case "notice":
		g.Notices = 1
	default:
		return attentionGroup{}, false
	}
	return g, true
}

func summarizeAttention(deliveries []attentionDelivery, complete bool, options attentionOptions, previous attentionCache, now time.Time) (attentionResult, attentionCache, error) {
	cutoff := options.OlderThanSeconds
	if cutoff == 0 {
		cutoff = 300
	}
	if cutoff < 1 || cutoff > 604800 {
		return attentionResult{}, previous, invalid("attention older_than_seconds must be 1..604800")
	}
	result := attentionResult{Complete: complete, Scanned: len(deliveries), OlderThanSeconds: cutoff, Groups: []attentionGroup{}, Changed: []attentionGroup{}, Cleared: []string{}, StoppedSessions: []stoppedSessionBacklog{}, Coverage: "This delivery page only; queued pool work and other pages are excluded."}
	if !complete {
		result.Coverage += " Partial snapshot: absence does not clear a previous condition."
	}
	current := attentionCache{Schema: "cairn.delivery-attention/1", Scope: previous.Scope, Deliveries: map[string]attentionEntry{}}
	if !complete {
		for id, e := range previous.Deliveries {
			current.Deliveries[id] = e
		}
	}
	groups := map[string]*attentionGroup{}
	changed := map[string]bool{}
	stoppedByRecipient := map[string]*stoppedSessionBacklog{}
	for _, d := range deliveries {
		old, known := previous.Deliveries[d.DeliveryID]
		delete(current.Deliveries, d.DeliveryID) // An explicitly observed row can supersede a prior cause even on a partial page.
		group, ok := attentionCandidate(d, cutoff, now)
		if !ok {
			continue
		}
		key := attentionKey(group)
		if existing := groups[key]; existing != nil {
			existing.Requests += group.Requests
			existing.Responses += group.Responses
			existing.Notices += group.Notices
			if group.OldestWaitingSeconds > existing.OldestWaitingSeconds {
				existing.OldestWaitingSeconds = group.OldestWaitingSeconds
				existing.SampleDeliveryID = group.SampleDeliveryID
			}
		} else {
			copy := group
			groups[key] = &copy
		}
		if group.Condition == "recipient_stopped" {
			recordStoppedSessionDelivery(stoppedByRecipient, d, group, complete)
		}
		if group.Requests == 0 && group.Condition != "recipient_stopped" {
			continue
		} // Ordinary notices/responses to active or offline sessions never create an attention change.
		entry := attentionEntry{
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
		return result, previous, invalid("attention state exceeds 1000 requests; use a narrower review filter or separate state file")
	}
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
	return result, current, nil
}

// Optional state is local presentation memory, containing IDs and closed labels,
// never source bodies. Scope changes fail rather than silently clear old alerts.
func withAttentionCache(options attentionOptions, scope string, run func(attentionCache) (attentionResult, attentionCache, error)) (attentionResult, error) {
	empty := attentionCache{Schema: "cairn.delivery-attention/1", Scope: scope, Deliveries: map[string]attentionEntry{}}
	if options.StateFile == "" {
		result, _, err := run(empty)
		return result, err
	}
	if !filepath.IsAbs(options.StateFile) {
		return attentionResult{}, invalid("attention state_file must be absolute")
	}
	lock, err := os.OpenFile(options.StateFile+".lock", os.O_CREATE|os.O_RDWR|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return attentionResult{}, err
	}
	defer lock.Close()
	if err = syscall.Flock(int(lock.Fd()), syscall.LOCK_EX); err != nil {
		return attentionResult{}, err
	}
	defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
	file, err := os.OpenFile(options.StateFile, os.O_RDONLY|syscall.O_NOFOLLOW, 0)
	if err == nil {
		raw, readErr := io.ReadAll(io.LimitReader(file, 512*1024+1))
		file.Close()
		if readErr != nil {
			return attentionResult{}, readErr
		}
		if len(raw) > 512*1024 {
			return attentionResult{}, invalid("attention state exceeds size limit")
		}
		if err = json.Unmarshal(raw, &empty); err != nil {
			return attentionResult{}, fmt.Errorf("invalid attention state: %w", err)
		}
		if empty.Schema != "cairn.delivery-attention/1" || empty.Scope != scope || empty.Deliveries == nil || len(empty.Deliveries) > 1000 {
			return attentionResult{}, invalid("attention state scope or schema mismatch; use a separate state file")
		}
	} else if !os.IsNotExist(err) {
		return attentionResult{}, err
	}
	result, next, err := run(empty)
	if err != nil {
		return result, err
	}
	raw, err := json.Marshal(next)
	if err != nil {
		return result, err
	}
	tmp, err := os.CreateTemp(filepath.Dir(options.StateFile), ".cairn-attention-*")
	if err != nil {
		return result, err
	}
	defer os.Remove(tmp.Name())
	if _, err = tmp.Write(raw); err == nil {
		err = tmp.Sync()
	}
	closeErr := tmp.Close()
	if err != nil {
		return result, err
	}
	if closeErr != nil {
		return result, closeErr
	}
	if err = os.Rename(tmp.Name(), options.StateFile); err != nil {
		return result, err
	}
	return result, nil
}

func attentionScope(source any) string {
	raw, _ := json.Marshal(source)
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

func stoppedDeliveryDetails(d attentionDelivery, waiting float64) (stoppedSessionDelivery, string) {
	diag := d.Diagnosis
	held := d.Held || diag.Stage == "held"
	admission := "not_admitted"
	if held || diag.Stage == "leased" || d.Attempts > 0 || d.Execution == "may_have_executed" {
		admission = "unknown"
	}
	var reported, freshness, applies, wakeSig string
	var retained *stoppedSessionWake
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
				retained = &stoppedSessionWake{
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
	var ref *stoppedSessionRef
	if d.Event.Ref.RecordID != "" {
		ref = &stoppedSessionRef{RecordID: d.Event.Ref.RecordID, Version: d.Event.Ref.Version}
	}
	item := stoppedSessionDelivery{
		DeliveryID: d.DeliveryID, EventID: d.Event.EventID, From: d.Event.From,
		Kind: d.Event.Kind, State: d.State, Stage: diag.Stage, Attempts: d.Attempts,
		Held: held, Execution: d.Execution, Admission: admission, WaitingSeconds: waiting,
		Ref: ref, ReportedCondition: reported, Freshness: freshness, Applies: applies,
		RetainedWake: retained,
	}
	sig := diag.Stage + "|" + d.Execution + "|" + admission + "|" + reported + "|" + wakeSig
	return item, sig
}

func attentionDeliveryFingerprint(d attentionDelivery, group attentionGroup, key string) string {
	if group.Condition != "recipient_stopped" {
		return key
	}
	_, sig := stoppedDeliveryDetails(d, group.OldestWaitingSeconds)
	return key + "|" + sig
}

func recordStoppedSessionDelivery(byRecipient map[string]*stoppedSessionBacklog, d attentionDelivery, group attentionGroup, complete bool) {
	item, _ := stoppedDeliveryDetails(d, group.OldestWaitingSeconds)
	entry := byRecipient[d.Consumer]
	if entry == nil {
		coverage := "Complete for this review filter; all matching deliveries fit in this page."
		if !complete {
			coverage = "Partial page: additional deliveries for this or other stopped sessions may exist beyond this cursor."
		}
		entry = &stoppedSessionBacklog{
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
			Deliveries:           []stoppedSessionDelivery{},
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

func notifyStoppedPublishers(ctx context.Context, store *core.Store, repo string, deliveries []attentionDelivery, previous, current attentionCache, now time.Time) (publisherNotificationBatch, attentionCache, error) {
	batch := publisherNotificationBatch{Results: []core.StoppedDeliveryNotificationResult{}}
	for _, d := range deliveries {
		group, ok := attentionCandidate(d, 1, now)
		if !ok || group.Condition != "recipient_stopped" {
			continue
		}
		_, sig := stoppedDeliveryDetails(d, group.OldestWaitingSeconds)
		reqID := uuid.NewSHA1(uuid.NameSpaceOID, []byte("cairn.stopped-delivery-notice/1|"+repo+"|"+d.DeliveryID+"|"+d.Diagnosis.Recipient.AgentID+"|"+d.Diagnosis.Recipient.ExecutionID+"|"+sig)).String()
		old := previous.Deliveries[d.DeliveryID]
		cur := current.Deliveries[d.DeliveryID]
		if old.NotifiedFingerprint == sig && sig != "" {
			batch.Suppressed++
			batch.Results = append(batch.Results, core.StoppedDeliveryNotificationResult{
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
		out, err := store.NotifyStoppedDeliveryPublisher(ctx, core.StoppedDeliveryNotificationRequest{
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
