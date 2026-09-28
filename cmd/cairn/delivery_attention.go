package main

import (
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
)

// Attention is an opt-in presentation of one bounded operator review page.
// It neither claims deliveries nor changes the host's recovery decisions.
type attentionOptions struct {
	StateFile        string `json:"state_file,omitempty"`
	OlderThanSeconds int64  `json:"older_than_seconds,omitempty"`
}

type attentionDelivery struct {
	DeliveryID  string    `json:"delivery_id"`
	Consumer    string    `json:"consumer"`
	State       string    `json:"state"`
	AvailableAt time.Time `json:"available_at"`
	Event       struct {
		Kind string `json:"kind"`
	} `json:"event"`
	Diagnosis *attentionDiagnosis `json:"diagnosis"`
}

type attentionDiagnosis struct {
	Stage          string  `json:"stage"`
	WaitingSeconds float64 `json:"waiting_seconds"`
	Recipient      struct {
		Kind     string `json:"kind"`
		State    string `json:"state"`
		Presence string `json:"presence"`
	} `json:"recipient"`
	Host *struct {
		Condition  string     `json:"condition"`
		Applies    string     `json:"applies"`
		Freshness  string     `json:"freshness"`
		ObservedAt *time.Time `json:"observed_at"`
		ReceivedAt *time.Time `json:"received_at"`
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

type attentionResult struct {
	Complete         bool             `json:"complete"`
	Scanned          int              `json:"scanned"`
	OlderThanSeconds int64            `json:"older_than_seconds"`
	Groups           []attentionGroup `json:"groups"`
	Changed          []attentionGroup `json:"changed"`
	Cleared          []string         `json:"cleared"`
	Coverage         string           `json:"coverage"`
}

type attentionEntry struct {
	Group       string `json:"group"`
	Fingerprint string `json:"fingerprint"`
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
	if (d.State != "pending" && d.State != "leased") || d.AvailableAt.After(now) || diag == nil || diag.Stage != "waiting" || diag.Recipient.Kind != "session" || diag.Recipient.State == "busy" || diag.WaitingSeconds < float64(cutoff) {
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
	result := attentionResult{Complete: complete, Scanned: len(deliveries), OlderThanSeconds: cutoff, Groups: []attentionGroup{}, Changed: []attentionGroup{}, Cleared: []string{}, Coverage: "This delivery page only; queued pool work and other pages are excluded."}
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
		if group.Requests == 0 {
			continue
		} // Ordinary notices/responses never create an attention change.
		entry := attentionEntry{Group: key, Fingerprint: key}
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
