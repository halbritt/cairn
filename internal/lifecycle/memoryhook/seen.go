package memoryhook

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/indexview"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

const (
	defaultSeenTTLTurns   = 32
	defaultSeenTTLSeconds = 3600
	maxSeenRecords        = 256
	maxCaptureCacheCount  = 32
	maxCaptureCacheBytes  = 131072
)

func recallDedupEnabled(config *value.Value, env map[string][]rune) bool {
	if env != nil {
		if v, ok := env["CAIRN_RECALL_DEDUP"]; ok && string(v) == "0" {
			return false
		}
	} else if os.Getenv("CAIRN_RECALL_DEDUP") == "0" {
		return false
	}
	if config != nil {
		if v := config.Get("recall_dedup"); v != nil && v.Kind == value.Bool && !v.Boolean {
			return false
		}
	}
	return true
}

func envString(env map[string][]rune, key string) (string, bool) {
	if env != nil {
		v, ok := env[key]
		return string(v), ok
	}
	return os.LookupEnv(key)
}

func seenTTLTurns(config *value.Value, env map[string][]rune) int {
	if raw, ok := envString(env, "CAIRN_SEEN_TTL_TURNS"); ok && strings.TrimSpace(raw) != "" {
		if n, err := strconv.Atoi(strings.TrimSpace(raw)); err == nil {
			if n <= 0 {
				return 0
			}
			return n
		}
	}
	if config != nil {
		if v := config.Get("seen_ttl_turns"); v != nil && v.Kind == value.Int {
			if n, err := strconv.Atoi(v.Integer); err == nil {
				if n <= 0 {
					return 0
				}
				return n
			}
		}
	}
	return defaultSeenTTLTurns
}

func seenTTLSeconds(config *value.Value, env map[string][]rune) float64 {
	if raw, ok := envString(env, "CAIRN_SEEN_TTL_SECONDS"); ok && strings.TrimSpace(raw) != "" {
		if n, err := strconv.ParseFloat(strings.TrimSpace(raw), 64); err == nil {
			if n <= 0 {
				return 0
			}
			return n
		}
	}
	if config != nil {
		if v := config.Get("seen_ttl_seconds"); v != nil {
			switch v.Kind {
			case value.Int:
				if n, err := strconv.ParseFloat(v.Integer, 64); err == nil {
					if n <= 0 {
						return 0
					}
					return n
				}
			case value.Float:
				if v.Number <= 0 {
					return 0
				}
				return v.Number
			}
		}
	}
	return defaultSeenTTLSeconds
}

func nowSeconds() float64 {
	return float64(time.Now().UnixNano()) / 1e9
}

func prepareSeenState(state, config, event *value.Value, env map[string][]rune, now float64) (*value.Value, error) {
	compactionEpoch := number(state.Get("compaction_epoch"))
	meta := state.Get("seen_meta")
	metaEpoch := number(meta.Get("epoch"))
	epochReset := compactionEpoch > 0 && (meta == nil || meta.Kind != value.Object || metaEpoch != compactionEpoch)
	compactPrompt := event != nil && value.SameText(event.Get("hook_event_name"), "UserPromptSubmit") && value.SameText(event.Get("source"), "compact")
	sessionStart := event != nil && value.SameText(event.Get("hook_event_name"), "SessionStart")

	if sessionStart || compactPrompt || epochReset {
		state.Set("seen", value.Mapping())
		state = state.Without("seen_meta")
	}
	if event != nil {
		if retained := event.Get("retained_record_ids"); retained != nil {
			if retained.Kind != value.Array {
				return nil, fail("invalid retained context identities")
			}
			for _, id := range retained.Items {
				if id.Kind != value.String {
					return nil, fail("invalid retained context identities")
				}
			}
			seen := value.Mapping()
			previous := state.Get("seen")
			if previous != nil && previous.Kind == value.Object {
				for _, member := range previous.Members {
					for _, id := range retained.Items {
						if string(id.String) == string(member.Key) {
							seen.Members = append(seen.Members, member)
							break
						}
					}
				}
			}
			state.Set("seen", seen)
		}
	}
	seen := state.Get("seen")
	if seen == nil || seen.Kind != value.Object {
		seen = value.Mapping()
		state.Set("seen", seen)
	}
	if len(seen.Members) == 0 {
		state.Members = state.Without("seen_meta").Members
		return state, nil
	}

	meta = state.Get("seen_meta")
	if meta == nil || meta.Kind != value.Object {
		meta = value.Mapping(
			value.Field("epoch", integer(compactionEpoch)),
			value.Field("turn", integer(0)),
			value.Field("records", value.Mapping()),
		)
	}
	turn := number(meta.Get("turn")) + 1
	meta.Set("epoch", integer(compactionEpoch))
	meta.Set("turn", integer(turn))
	records := meta.Get("records")
	if records == nil || records.Kind != value.Object {
		records = value.Mapping()
	}

	ttlTurns := seenTTLTurns(config, env)
	ttlSec := seenTTLSeconds(config, env)
	keptSeen := value.Mapping()
	keptRecords := value.Mapping()
	for _, m := range seen.Members {
		key := string(m.Key)
		rec := records.Get(key)
		if rec == nil || rec.Kind != value.Object {
			keptSeen.Members = append(keptSeen.Members, m)
			keptRecords.Set(key, value.Mapping(
				value.Field("turn", integer(turn)),
				value.Field("at", floating(now)),
			))
			continue
		}
		expired := false
		if ttlTurns > 0 && turn-number(rec.Get("turn")) >= ttlTurns {
			expired = true
		}
		if !expired && ttlSec > 0 {
			if at := rec.Get("at"); at != nil && (at.Kind == value.Float || at.Kind == value.Int) {
				var stamp float64
				if at.Kind == value.Float {
					stamp = at.Number
				} else {
					stamp, _ = strconv.ParseFloat(at.Integer, 64)
				}
				if now-stamp >= ttlSec {
					expired = true
				}
			}
		}
		if !expired {
			keptSeen.Members = append(keptSeen.Members, m)
			keptRecords.Set(key, rec)
		}
	}
	state.Set("seen", keptSeen)
	if len(keptSeen.Members) == 0 {
		state.Members = state.Without("seen_meta").Members
	} else {
		meta.Set("records", keptRecords)
		state.Set("seen_meta", meta)
	}
	return state, nil
}

func seenCoversWhole(seen, entry *value.Value) bool {
	if seen == nil || entry == nil {
		return false
	}
	old := seen.Get(text(entry.Get("record_id")))
	return old != nil && old.Kind == value.Int && sameInteger(old, entry.Get("version"))
}

func seenCoversSpan(seen, entry *value.Value, hint *core.ByteSpanRequest) bool {
	if seenCoversWhole(seen, entry) {
		return true
	}
	if seen == nil || entry == nil || hint == nil {
		return false
	}
	old := seen.Get(text(entry.Get("record_id")))
	if old == nil || old.Kind != value.Object || !sameInteger(old.Get("version"), entry.Get("version")) {
		return false
	}
	spans := old.Get("spans")
	if spans == nil || spans.Kind != value.Array {
		return false
	}
	reqEnd := hint.Offset + hint.Length
	for _, s := range spans.Items {
		if s == nil || s.Kind != value.Object {
			continue
		}
		sOff := number(s.Get("offset"))
		sEnd := number(s.Get("end"))
		sEOF := value.Truth(s.Get("eof"))
		if sOff <= hint.Offset && (sEnd >= reqEnd || sEOF) {
			return true
		}
	}
	return false
}

func seenCoversEntry(seen, entry *value.Value) bool {
	if seenCoversWhole(seen, entry) {
		return true
	}
	if seen == nil || entry == nil {
		return false
	}
	old := seen.Get(text(entry.Get("record_id")))
	if old == nil || old.Kind != value.Object || !sameInteger(old.Get("version"), entry.Get("version")) {
		return false
	}
	hint, err := optionalHint(entry)
	if err != nil {
		return false
	}
	return seenCoversSpan(seen, entry, hint)
}

func seenCoversGroup(seen *value.Value, group []*value.Value) bool {
	if seen == nil || len(group) == 0 {
		return false
	}
	if len(group) == 1 {
		return seenCoversEntry(seen, group[0])
	}
	for _, entry := range group {
		if !seenCoversWhole(seen, entry) {
			return false
		}
	}
	return true
}

func ensureSeenMeta(state *value.Value) (*value.Value, int) {
	epoch := number(state.Get("compaction_epoch"))
	meta := state.Get("seen_meta")
	if meta == nil || meta.Kind != value.Object {
		meta = value.Mapping(
			value.Field("epoch", integer(epoch)),
			value.Field("turn", integer(1)),
			value.Field("records", value.Mapping()),
		)
		state.Set("seen_meta", meta)
	}
	turn := number(meta.Get("turn"))
	if turn <= 0 {
		turn = 1
		meta.Set("turn", integer(turn))
	}
	meta.Set("epoch", integer(epoch))
	if meta.Get("records") == nil || meta.Get("records").Kind != value.Object {
		meta.Set("records", value.Mapping())
	}
	return meta, turn
}

func capSeenState(state *value.Value) {
	seen := state.Get("seen")
	if seen == nil || seen.Kind != value.Object {
		return
	}
	if len(seen.Members) > maxSeenRecords {
		seen.Members = seen.Members[len(seen.Members)-maxSeenRecords:]
	}
	if len(seen.Members) == 0 {
		state.Members = state.Without("seen_meta").Members
		return
	}
	meta := state.Get("seen_meta")
	if meta == nil || meta.Kind != value.Object {
		return
	}
	records := meta.Get("records")
	if records == nil || records.Kind != value.Object {
		return
	}
	kept := value.Mapping()
	for _, m := range seen.Members {
		if rec := records.Get(string(m.Key)); rec != nil {
			kept.Set(string(m.Key), rec)
		}
	}
	meta.Set("records", kept)
}

func markSeenWhole(state *value.Value, recordID string, version *value.Value, now float64) {
	if state == nil || recordID == "" || version == nil || version.Kind != value.Int {
		return
	}
	seen := state.Get("seen")
	if seen == nil || seen.Kind != value.Object {
		seen = value.Mapping()
	}
	seen = seen.Without(recordID)
	seen.Set(recordID, version)
	state.Set("seen", seen)

	meta, turn := ensureSeenMeta(state)
	records := meta.Get("records")
	records = records.Without(recordID)
	records.Set(recordID, value.Mapping(
		value.Field("turn", integer(turn)),
		value.Field("at", floating(now)),
	))
	meta.Set("records", records)
	capSeenState(state)
}

type seenSpanItem struct {
	offset int
	end    int
	eof    bool
}

func markSeenSpan(state *value.Value, recordID string, version *value.Value, offset, end, totalBytes int, now float64) {
	if state == nil || recordID == "" || version == nil || version.Kind != value.Int || end <= offset {
		return
	}
	seen := state.Get("seen")
	if seen == nil || seen.Kind != value.Object {
		seen = value.Mapping()
	}
	old := seen.Get(recordID)
	if old != nil && old.Kind == value.Int && sameInteger(old, version) {
		return
	}
	var existing []seenSpanItem
	if old != nil && old.Kind == value.Object && sameInteger(old.Get("version"), version) {
		if spans := old.Get("spans"); spans != nil && spans.Kind == value.Array {
			for _, item := range spans.Items {
				if item != nil && item.Kind == value.Object {
					existing = append(existing, seenSpanItem{
						offset: number(item.Get("offset")),
						end:    number(item.Get("end")),
						eof:    value.Truth(item.Get("eof")),
					})
				}
			}
		}
	}
	added := seenSpanItem{
		offset: offset,
		end:    end,
		eof:    totalBytes > 0 && end >= totalBytes,
	}
	merged := mergeSeenSpans(existing, added)
	arr := array()
	for _, s := range merged {
		arr.Items = append(arr.Items, value.Mapping(
			value.Field("offset", integer(s.offset)),
			value.Field("end", integer(s.end)),
			value.Field("eof", value.Boolean(s.eof)),
		))
	}
	entry := value.Mapping(
		value.Field("version", version),
		value.Field("spans", arr),
	)
	seen = seen.Without(recordID)
	seen.Set(recordID, entry)
	state.Set("seen", seen)

	meta, turn := ensureSeenMeta(state)
	records := meta.Get("records")
	records = records.Without(recordID)
	records.Set(recordID, value.Mapping(
		value.Field("turn", integer(turn)),
		value.Field("at", floating(now)),
	))
	meta.Set("records", records)
	capSeenState(state)
}

func mergeSeenSpans(existing []seenSpanItem, added seenSpanItem) []seenSpanItem {
	all := append(append([]seenSpanItem{}, existing...), added)
	// Simple insertion sort by (offset, end).
	for i := 1; i < len(all); i++ {
		for j := i; j > 0 && (all[j].offset < all[j-1].offset || (all[j].offset == all[j-1].offset && all[j].end < all[j-1].end)); j-- {
			all[j], all[j-1] = all[j-1], all[j]
		}
	}
	out := make([]seenSpanItem, 0, len(all))
	for _, cur := range all {
		if len(out) == 0 || cur.offset > out[len(out)-1].end {
			out = append(out, cur)
			continue
		}
		last := &out[len(out)-1]
		if cur.end > last.end {
			last.end = cur.end
		}
		last.eof = last.eof || cur.eof
	}
	return out
}

func recordEagerSeen(state *value.Value, bodies []*value.Value, now float64) {
	if state == nil || len(bodies) == 0 {
		return
	}
	for _, candidate := range bodies {
		response := candidate.Get("response")
		if response == nil {
			continue
		}
		if value.SameText(response.Get("source_extent"), "partial_span") {
			rec := response.Get("selection").Get("record")
			span := response.Get("span")
			if rec != nil && span != nil {
				markSeenSpan(state, text(rec.Get("record_id")), rec.Get("version"), number(span.Get("offset")), number(span.Get("end")), number(span.Get("total_bytes")), now)
			}
			continue
		}
		if sel := response.Get("selection"); sel != nil {
			if rec := sel.Get("record"); rec != nil {
				markSeenWhole(state, text(rec.Get("record_id")), rec.Get("version"), now)
			}
		}
		if competing := response.Get("competing"); competing != nil {
			for _, item := range competing.Items {
				if rec := item.Get("record"); rec != nil {
					markSeenWhole(state, text(rec.Get("record_id")), rec.Get("version"), now)
				}
			}
		}
	}
}

func loadInboxSeenState(directory, session string, config, event *value.Value) (*value.Value, bool, bool) {
	if !recallDedupEnabled(config, nil) {
		return nil, false, false
	}
	path := filepath.Join(directory, session+".json")
	raw, err := os.ReadFile(path)
	existed := err == nil
	state := value.Mapping()
	if err == nil {
		decodedState, decErr := value.DecodeText(raw, "utf-8", 0)
		if decErr != nil || decodedState.Kind != value.Object {
			return nil, existed, false
		}
		state = decodedState
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, false, false
	}
	updated, err := prepareSeenState(state, config, event, nil, nowSeconds())
	if err != nil {
		return nil, existed, false
	}
	return updated, existed, true
}

func saveInboxSeenState(directory, session string, state *value.Value, existed bool) error {
	if state == nil {
		return nil
	}
	seen := state.Get("seen")
	if !existed && (seen == nil || len(seen.Members) == 0) {
		return nil
	}
	return saveState(filepath.Join(directory, session+".json"), state)
}

func captureCacheLookup(state *value.Value, entry indexview.Entry, dedup bool) (*value.Value, json.RawMessage, bool) {
	if !dedup || state == nil || !bodyHash.MatchString(entry.BodySHA256) || entry.RecordID == "" || entry.Version <= 0 {
		return nil, nil, false
	}
	cache := state.Get("capture_cache")
	if cache == nil || cache.Kind != value.Object {
		return nil, nil, false
	}
	item := cache.Get(entry.RecordID)
	if item == nil || item.Kind != value.Object {
		return nil, nil, false
	}
	ver := item.Get("version")
	if ver == nil || ver.Kind != value.Int || number(ver) != entry.Version {
		return nil, nil, false
	}
	if text(item.Get("body_sha256")) != entry.BodySHA256 {
		return nil, nil, false
	}
	if entry.Class != "" && text(item.Get("class")) != entry.Class {
		return nil, nil, false
	}
	if entry.Kind != "" && text(item.Get("kind")) != entry.Kind {
		return nil, nil, false
	}
	bodyVal := item.Get("body")
	if bodyVal == nil || bodyVal.Kind != value.String {
		return nil, nil, false
	}
	bodyText := text(bodyVal)
	if hashText(bodyText) != entry.BodySHA256 {
		return nil, nil, false
	}
	// Promote to LRU tail.
	cache = cache.Without(entry.RecordID)
	cache.Set(entry.RecordID, item)
	state.Set("capture_cache", cache)

	record := value.Mapping(
		value.Field("record_id", value.Text(entry.RecordID)),
		value.Field("version", integer(entry.Version)),
		value.Field("class", item.Get("class")),
		value.Field("kind", item.Get("kind")),
		value.Field("body", value.Text(bodyText)),
	)
	raw, err := encoded(record, false)
	if err != nil {
		return record, nil, true
	}
	return record, json.RawMessage(raw), true
}

func captureCacheStore(state *value.Value, entry indexview.Entry, record *value.Value, dedup bool) {
	if !dedup || state == nil || record == nil || record.Kind != value.Object {
		return
	}
	if !bodyHash.MatchString(entry.BodySHA256) || entry.RecordID == "" || entry.Version <= 0 {
		return
	}
	recID := text(record.Get("record_id"))
	ver := record.Get("version")
	bodyVal := record.Get("body")
	if recID != entry.RecordID || ver == nil || ver.Kind != value.Int || number(ver) != entry.Version || bodyVal == nil || bodyVal.Kind != value.String {
		return
	}
	bodyText := text(bodyVal)
	if len(bodyText) > maxCaptureCacheBytes || hashText(bodyText) != entry.BodySHA256 {
		return
	}
	classVal := record.Get("class")
	kindVal := record.Get("kind")
	if classVal == nil || classVal.Kind != value.String {
		classVal = value.Text(entry.Class)
	}
	if kindVal == nil || kindVal.Kind != value.String {
		kindVal = value.Text(entry.Kind)
	}
	cache := state.Get("capture_cache")
	if cache == nil || cache.Kind != value.Object {
		cache = value.Mapping()
	}
	cached := value.Mapping(
		value.Field("version", integer(entry.Version)),
		value.Field("body_sha256", value.Text(entry.BodySHA256)),
		value.Field("class", classVal),
		value.Field("kind", kindVal),
		value.Field("body", value.Text(bodyText)),
	)
	cache = cache.Without(recID)
	cache.Set(recID, cached)
	totalBytes := 0
	for _, m := range cache.Members {
		totalBytes += len(text(m.Value.Get("body")))
	}
	for len(cache.Members) > maxCaptureCacheCount || totalBytes > maxCaptureCacheBytes {
		evicted := cache.Members[0]
		totalBytes -= len(text(evicted.Value.Get("body")))
		cache.Members = cache.Members[1:]
	}
	if len(cache.Members) == 0 {
		state.Members = state.Without("capture_cache").Members
		return
	}
	state.Set("capture_cache", cache)
}
