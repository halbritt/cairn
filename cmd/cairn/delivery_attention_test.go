package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
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
	for _, mode := range []string{"busy", "scheduled", "available", "young", "notice", "response", "non-session"} {
		t.Run(mode, func(t *testing.T) {
			d := attentionFixture("one", "request", "native_transport_unavailable")
			switch mode {
			case "busy":
				d.Diagnosis.Recipient.State = "busy"
			case "scheduled":
				d.AvailableAt = now.Add(time.Minute)
			case "available":
				d.Diagnosis.Host.Condition = "automatic_available"
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
