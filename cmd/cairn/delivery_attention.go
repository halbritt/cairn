package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"time"

	"github.com/halbritt/cairn/core"
)

// Attention is an opt-in presentation of one bounded operator review page.
// It neither claims deliveries nor changes the host's recovery decisions.
type attentionOptions struct {
	StateFile        string            `json:"state_file,omitempty"`
	OlderThanSeconds int64             `json:"older_than_seconds,omitempty"`
	NotifyPublishers bool              `json:"notify_publishers,omitempty"`
	Starvation       starvationOptions `json:"starvation,omitempty"`
}

func (o attentionOptions) toCore() core.AttentionOptions {
	return core.AttentionOptions{
		StateFile:        o.StateFile,
		OlderThanSeconds: o.OlderThanSeconds,
		NotifyPublishers: o.NotifyPublishers,
		Starvation:       core.StarvationOptions(o.Starvation),
	}
}

type attentionGroup = core.AttentionGroup
type stoppedSessionRef = core.StoppedSessionRef
type stoppedSessionWake = core.StoppedSessionWake
type stoppedSessionDelivery = core.StoppedSessionDelivery
type stoppedSessionBacklog = core.StoppedSessionBacklog
type publisherNotificationBatch = core.PublisherNotificationBatch
type attentionEntry = core.AttentionEntry
type attentionCache = core.AttentionCache

type attentionResult struct {
	Complete               bool                        `json:"complete"`
	Scanned                int                         `json:"scanned"`
	OlderThanSeconds       int64                       `json:"older_than_seconds"`
	Groups                 []attentionGroup            `json:"groups"`
	Changed                []attentionGroup            `json:"changed"`
	Cleared                []string                    `json:"cleared"`
	StoppedSessions        []stoppedSessionBacklog     `json:"stopped_sessions,omitempty"`
	StarvedSessions        []starvedSession            `json:"starved_sessions"`
	PublisherNotifications *publisherNotificationBatch `json:"publisher_notifications,omitempty"`
	Coverage               string                      `json:"coverage"`
}

func fromCoreAttentionResult(r core.AttentionResult) attentionResult {
	return attentionResult{
		Complete:               r.Complete,
		Scanned:                r.Scanned,
		OlderThanSeconds:       r.OlderThanSeconds,
		Groups:                 r.Groups,
		Changed:                r.Changed,
		Cleared:                r.Cleared,
		StoppedSessions:        r.StoppedSessions,
		StarvedSessions:        fromCoreStarvedSessions(r.StarvedSessions),
		PublisherNotifications: r.PublisherNotifications,
		Coverage:               r.Coverage,
	}
}

func fromCoreStarvedSessions(src []core.StarvedSession) []starvedSession {
	if src == nil {
		return nil
	}
	out := make([]starvedSession, len(src))
	for i, s := range src {
		out[i] = starvedSession(s)
	}
	return out
}

func summarizeReviewAttention(deliveries []core.ReviewDelivery, complete bool, options attentionOptions, previous attentionCache, now time.Time) (attentionResult, attentionCache, error) {
	res, next, err := core.SummarizeAttention(deliveries, complete, options.toCore(), previous, now)
	return fromCoreAttentionResult(res), next, err
}

func notifyStoppedPublishers(ctx context.Context, store *core.Store, repo string, deliveries []core.ReviewDelivery, previous, current attentionCache, now time.Time) (publisherNotificationBatch, attentionCache, error) {
	return core.NotifyStoppedPublishers(ctx, store, repo, deliveries, previous, current, now)
}

func detectReviewStarvation(rows []core.ReviewDelivery, options starvationOptions, now time.Time, lastHandled func([]string) (map[string]time.Time, error)) ([]starvedSession, error) {
	got, err := core.DetectStarvation(rows, core.StarvationOptions(options), now, lastHandled)
	if err != nil {
		return nil, err
	}
	return fromCoreStarvedSessions(got), nil
}

func hostConditionHint(condition string) string {
	return core.HostConditionHint(condition)
}

// Optional state is local presentation memory, containing IDs and closed labels,
// never source bodies. Scope changes fail rather than silently clear old alerts.
func withAttentionCache(options attentionOptions, scope string, run func(attentionCache) (attentionResult, attentionCache, error)) (attentionResult, error) {
	var result attentionResult
	stateFile := LockedStateFile[attentionCache]{
		Path:             options.StateFile,
		TempPattern:      ".cairn-attention-*",
		MaxBytes:         512 * 1024,
		CreateDir:        true,
		AbsPathError:     "attention state_file must be absolute",
		SizeLimitError:   "attention state exceeds size limit",
		ParseErrorPrefix: "invalid attention state",
		Default: func() attentionCache {
			return attentionCache{Schema: "cairn.delivery-attention/1", Scope: scope, Deliveries: map[string]attentionEntry{}}
		},
		Validate: func(c attentionCache) error {
			if c.Schema != "cairn.delivery-attention/1" || c.Scope != scope || c.Deliveries == nil || len(c.Deliveries) > 1000 {
				return invalid("attention state scope or schema mismatch; use a separate state file")
			}
			return nil
		},
	}
	err := stateFile.Update(func(current attentionCache, _ func(attentionCache) error) (attentionCache, bool, error) {
		var next attentionCache
		var runErr error
		result, next, runErr = run(current)
		return next, runErr == nil, runErr
	})
	return result, err
}

func attentionScope(source any) string {
	raw, _ := json.Marshal(source)
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

type attentionAttempt struct {
	State     string    `json:"state"`
	CreatedAt time.Time `json:"created_at"`
	OpenTools *int      `json:"open_tools"`
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
	LatestWake   *attentionAttempt   `json:"latest_wake"`
	LatestNative *attentionAttempt   `json:"latest_native"`
	Diagnosis    *attentionDiagnosis `json:"diagnosis"`
}

func (d attentionDelivery) toReviewDelivery() core.ReviewDelivery {
	out := core.ReviewDelivery{
		DeliveryID:  d.DeliveryID,
		Consumer:    d.Consumer,
		State:       d.State,
		Attempts:    d.Attempts,
		Held:        d.Held,
		Execution:   d.Execution,
		AvailableAt: d.AvailableAt,
		Event: core.AgentEvent{
			EventID:   d.Event.EventID,
			From:      d.Event.From,
			Kind:      d.Event.Kind,
			CreatedAt: d.Event.CreatedAt,
			Ref: core.RecordVersionRef{
				RecordID: d.Event.Ref.RecordID,
				Version:  d.Event.Ref.Version,
			},
		},
	}
	if d.LatestWake != nil {
		out.LatestWake = &core.ReviewAttempt{State: d.LatestWake.State, CreatedAt: d.LatestWake.CreatedAt, OpenTools: d.LatestWake.OpenTools}
	}
	if d.LatestNative != nil {
		out.LatestNative = &core.ReviewAttempt{State: d.LatestNative.State, CreatedAt: d.LatestNative.CreatedAt, OpenTools: d.LatestNative.OpenTools}
	}
	if d.Diagnosis != nil {
		waited := int64(d.Diagnosis.WaitingSeconds)
		diag := &core.DeliveryDiagnosis{
			Stage:          d.Diagnosis.Stage,
			WaitingSeconds: &waited,
			Recipient: core.DiagnosisRecipient{
				Kind:        d.Diagnosis.Recipient.Kind,
				AgentID:     d.Diagnosis.Recipient.AgentID,
				ExecutionID: d.Diagnosis.Recipient.ExecutionID,
				State:       d.Diagnosis.Recipient.State,
				Presence:    d.Diagnosis.Recipient.Presence,
			},
		}
		if h := d.Diagnosis.Host; h != nil {
			host := &core.DiagnosisHost{
				Condition:  h.Condition,
				Reported:   h.Reported,
				Applies:    h.Applies,
				Freshness:  h.Freshness,
				ObservedAt: h.ObservedAt,
				ReceivedAt: h.ReceivedAt,
			}
			if h.Wake != nil {
				host.Wake = &core.ObservedWake{
					Transport:   h.Wake.Transport,
					Status:      h.Wake.Status,
					AttemptedAt: h.Wake.AttemptedAt,
				}
			}
			diag.Host = host
		}
		out.Diagnosis = diag
	}
	return out
}

func summarizeAttention(deliveries []attentionDelivery, complete bool, options attentionOptions, previous attentionCache, now time.Time) (attentionResult, attentionCache, error) {
	rows := make([]core.ReviewDelivery, len(deliveries))
	for i, d := range deliveries {
		rows[i] = d.toReviewDelivery()
	}
	return summarizeReviewAttention(rows, complete, options, previous, now)
}
