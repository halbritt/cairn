package coordination

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// JournalStateProjection is an explicit representation boundary. It must retain
// Source scalars while exposing journal's existing map API, and apply only its
// inbox_journals mutation back to the ordered native record. No JSON round trip
// or implicit production value-domain selection is performed here.
type JournalStateProjection interface {
	Project(*value.Value) (map[string]any, error)
	ApplyJournals(*value.Value, map[string]any) error
}

// LosslessJournalProjection is the representation adapter, not another journal
// state machine. Only the journal-owned index is copied back to a native record.
type LosslessJournalProjection struct{}

func (LosslessJournalProjection) Project(record *value.Value) (map[string]any, error) {
	if record == nil || record.Kind != value.Object {
		return nil, &journal.NonObjectError{Field: "state"}
	}
	x, err := journalGoValue(record)
	if err != nil {
		return nil, err
	}
	return journal.DefaultJSONConstants(x).(map[string]any), nil
}
func (LosslessJournalProjection) ApplyJournals(record *value.Value, state map[string]any) error {
	if record == nil || record.Kind != value.Object {
		return &journal.NonObjectError{Field: "state"}
	}
	x, present := state["inbox_journals"]
	if !present {
		nativePop(record, "inbox_journals")
		return nil
	}
	plain := journal.PlainValue(x)
	old, err := journalGoValue(record.Get("inbox_journals"))
	if err != nil {
		return err
	}
	if record.Get("inbox_journals") != nil && reflect.DeepEqual(old, plain) {
		return nil
	}
	next, err := journalNativeValue(plain)
	if err != nil {
		return err
	}
	record.Set("inbox_journals", next)
	return nil
}

// journal uses WTF-8 for Python code points that are not Unicode scalar values.
func journalText(points []rune) string {
	var b strings.Builder
	for _, r := range points {
		if r >= 0xd800 && r <= 0xdfff {
			b.WriteByte(0xe0 | byte(r>>12))
			b.WriteByte(0x80 | byte(r>>6&63))
			b.WriteByte(0x80 | byte(r&63))
		} else {
			b.WriteRune(r)
		}
	}
	return b.String()
}
func journalPoints(raw string) []rune {
	var out []rune
	for len(raw) > 0 {
		if len(raw) >= 3 && raw[0] == 0xed && raw[1] >= 0xa0 && raw[1] <= 0xbf && raw[2]&0xc0 == 0x80 {
			out = append(out, rune(raw[0]&15)<<12|rune(raw[1]&63)<<6|rune(raw[2]&63))
			raw = raw[3:]
			continue
		}
		r, n := utf8.DecodeRuneInString(raw)
		out = append(out, r)
		raw = raw[n:]
	}
	return out
}
func journalGoValue(v *value.Value) (any, error) {
	if v == nil || v.Kind == value.Null {
		return nil, nil
	}
	switch v.Kind {
	case value.Bool:
		return v.Boolean, nil
	case value.Int:
		return json.Number(v.Integer), nil
	case value.Float:
		return journalFloat(v.Number), nil
	case value.String:
		return journalText(v.String), nil
	case value.Array:
		out := make([]any, len(v.Items))
		for i, item := range v.Items {
			var err error
			out[i], err = journalGoValue(item)
			if err != nil {
				return nil, err
			}
		}
		return out, nil
	case value.Object:
		out := make(map[string]any, len(v.Members))
		for _, member := range v.Members {
			x, err := journalGoValue(member.Value)
			if err != nil {
				return nil, err
			}
			out[journalText(member.Key)] = x
		}
		return out, nil
	default:
		return nil, &journal.TypeError{Field: "source value"}
	}
}
func journalNativeValue(x any) (*value.Value, error) {
	switch x := x.(type) {
	case nil:
		return &value.Value{Kind: value.Null}, nil
	case bool:
		return value.Boolean(x), nil
	case string:
		return &value.Value{Kind: value.String, String: journalPoints(x)}, nil
	case json.Number:
		if !strings.ContainsAny(string(x), ".eE") && !strings.Contains(string(x), "NaN") && !strings.Contains(string(x), "Inf") {
			return value.IntegerText(string(x)), nil
		}
		n, err := strconv.ParseFloat(string(x), 64)
		if err != nil && !(errors.Is(err, strconv.ErrRange) && math.IsInf(n, 0)) {
			return nil, err
		}
		return &value.Value{Kind: value.Float, Number: n}, nil
	case float64:
		return &value.Value{Kind: value.Float, Number: x}, nil
	case []string:
		out := &value.Value{Kind: value.Array}
		for _, item := range x {
			out.Items = append(out.Items, &value.Value{Kind: value.String, String: journalPoints(item)})
		}
		return out, nil
	case []any:
		out := &value.Value{Kind: value.Array}
		for _, item := range x {
			v, err := journalNativeValue(item)
			if err != nil {
				return nil, err
			}
			out.Items = append(out.Items, v)
		}
		return out, nil
	case map[string]any:
		out := value.Mapping()
		keys := make([]string, 0, len(x))
		for key := range x {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			v, err := journalNativeValue(x[key])
			if err != nil {
				return nil, err
			}
			out.Members = append(out.Members, value.Member{Key: journalPoints(key), Value: v})
		}
		return out, nil
	default:
		return nil, &journal.TypeError{Field: "journal projection value"}
	}
}

func journalFloat(n float64) json.Number {
	if math.IsNaN(n) {
		return json.Number("NaN")
	}
	if math.IsInf(n, 1) {
		return json.Number("Infinity")
	}
	if math.IsInf(n, -1) {
		return json.Number("-Infinity")
	}
	style := byte('f')
	abs := math.Abs(n)
	if abs != 0 && (abs < 1e-4 || abs >= 1e16) {
		style = 'e'
	}
	text := strconv.FormatFloat(n, style, -1, 64)
	if !strings.ContainsAny(text, ".eE") {
		text += ".0"
	}
	return json.Number(text)
}

type SessionJournalAdapter struct {
	Projection JournalStateProjection
	Options    journal.Options
	Now        func() time.Time
	Log        func(string) error
	PathText   func(*value.Value) (string, error)
}

func (a *SessionJournalAdapter) projection() JournalStateProjection {
	if a.Projection != nil {
		return a.Projection
	}
	return LosslessJournalProjection{}
}

func (a *SessionJournalAdapter) pathText(v *value.Value) (string, error) {
	if a.PathText != nil {
		return a.PathText(v)
	}
	if v == nil || v.Kind != value.String {
		return "", &journal.TypeError{Field: "path"}
	}
	for _, r := range v.String {
		if !utf8.ValidRune(r) {
			return "", &value.ProfileUnavailable{}
		}
	}
	return string(v.String), nil
}
func (a *SessionJournalAdapter) stateDir(config *value.Value) (string, error) {
	v := config.Get("state_dir")
	if !value.Truth(v) {
		return "", nil
	}
	return a.pathText(v)
}
func (a *SessionJournalAdapter) Flush(ctx context.Context, config, record *value.Value, scope FlushScope) (bool, error) {
	return a.FlushInboxIntents(ctx, config, record, scope)
}
func (a *SessionJournalAdapter) FlushInboxIntents(ctx context.Context, config, record *value.Value, scope FlushScope) (bool, error) {
	dir, e := a.stateDir(config)
	if e != nil {
		return false, e
	}
	if dir == "" || !value.Truth(record.Get("agent")) {
		return true, nil
	}
	projected, e := a.projection().Project(record)
	if e != nil {
		return false, e
	}
	opts := a.Options
	if opts.OnDiagnostic == nil && a.Log != nil {
		opts.OnDiagnostic = func(attemptID string, d journal.SlotDiagnostic) error {
			binding, err := nativeRequired(config, "binding")
			if err != nil {
				return err
			}
			switch d.Status {
			case "pending":
				return a.Log(fmt.Sprintf("Cairn presence %s: journaled %s for attempt %s is still uncertain (%s); it will be replayed",
					nativeText(binding), d.Kind, attemptID, d.Code))
			case "refused":
				return a.Log(fmt.Sprintf("Cairn presence %s: journaled %s for attempt %s was refused (%s); its content stays in %s",
					nativeText(binding), d.Kind, attemptID, d.Code, d.Target))
			}
			return nil
		}
	}
	settled, e := journal.Flush(ctx, dir, projected, string(scope), opts)
	apply := a.projection().ApplyJournals(record, projected)
	if apply != nil {
		return false, apply
	}
	return settled, e
}
func (a *SessionJournalAdapter) Committed(config, record *value.Value) (bool, error) {
	return a.JournalCommitted(config, record)
}
func (a *SessionJournalAdapter) JournalCommitted(config, record *value.Value) (bool, error) {
	attempt := record.Get("inbox_attempt")
	dir, e := a.stateDir(config)
	if e != nil {
		return false, e
	}
	if !value.Truth(attempt) || dir == "" {
		return false, nil
	}
	id, e := nativeRequired(attempt, "attempt_id")
	if e != nil {
		return false, e
	}
	name, e := a.pathText(id)
	if e != nil {
		return false, e
	}
	return journal.Inspect(dir, name).Committed()
}
func (a *SessionJournalAdapter) JournalCompletionState(config, attempt *value.Value) (string, error) {
	dir, e := a.stateDir(config)
	if e != nil {
		return "", e
	}
	if !value.Truth(attempt) || dir == "" {
		return "absent", nil
	}
	id, e := nativeRequired(attempt, "attempt_id")
	if e != nil {
		return "", e
	}
	name, e := a.pathText(id)
	if e != nil {
		return "", e
	}
	return journal.Inspect(dir, name).CompletionState()
}
func (a *SessionJournalAdapter) TurnEndDiagnostic(config, record *value.Value) error {
	return a.RecordUnacknowledgedTurnEnd(config, record)
}
func (a *SessionJournalAdapter) RecordUnacknowledgedTurnEnd(config, record *value.Value) error {
	attempt := record.Get("inbox_attempt")
	if attempt == nil || attempt.Kind != value.Object || !value.Truth(attempt.Get("attempt_id")) {
		return nil
	}
	id := attempt.Get("attempt_id")
	wake, e := nativeObj(record.Get("wake_refusal_diagnostic"), "wake_refusal_diagnostic")
	if e != nil {
		return e
	}
	if nativeEqual(wake.Get("attempt_id"), id) {
		return nil
	}
	delivery, e := nativeObj(attempt.Get("delivery"), "delivery")
	if e != nil {
		return e
	}
	event, e := nativeObj(delivery.Get("event"), "event")
	if e != nil {
		return e
	}
	state, e := a.JournalCompletionState(config, attempt)
	if e != nil {
		return e
	}
	previous, e := nativeObj(record.Get("unacknowledged_turn_end_diagnostic"), "unacknowledged_turn_end_diagnostic")
	if e != nil {
		return e
	}
	at := previous.Get("at")
	same := nativeEqual(previous.Get("attempt_id"), id)
	if !same {
		if a.Now == nil {
			return &value.ProfileUnavailable{}
		}
		t := a.Now()
		at = &value.Value{Kind: value.Float, Number: float64(t.Unix()) + float64(t.Nanosecond())/1e9}
	}
	diagnostic := nativeMap("schema", "cairn.unacknowledged-turn-end/1", "attempt_id", id, "delivery_id", delivery.Get("delivery_id"), "event_id", event.Get("event_id"), "kind", event.Get("kind"), "native_turn_id", attempt.Get("native_turn_id"), "journal_state", state, "lease_lapsed", value.Truth(attempt.Get("lease_lapsed")), "reminded", nativeEqual(record.Get("inbox_ack_reminded"), id), "outcome", "processing_failed", "at", at)
	record.Set("unacknowledged_turn_end_diagnostic", diagnostic)
	if !same {
		if a.Log == nil {
			return &value.ProfileUnavailable{}
		}
		binding, e := nativeRequired(config, "binding")
		if e != nil {
			return e
		}
		kind := nativeText(event.Get("kind"))
		if kind == "" {
			kind = "inbox"
		}
		return a.Log(fmt.Sprintf("Cairn presence %s: turn ended without journaled completion/acknowledgement for %s attempt %s (delivery %s, journal=%s); reconciled as turn_ended (processing_failed)", nativeText(binding), kind, nativeText(id), nativeText(delivery.Get("delivery_id")), state))
	}

	return nil
}
func (a *SessionJournalAdapter) SettledJournalIndex(config, record *value.Value) (bool, error) {
	dir, e := a.stateDir(config)
	if e != nil {
		return false, e
	}
	state, e := a.projection().Project(record)
	if e != nil {
		return false, e
	}
	return journal.SettledJournalIndex(dir, state), nil
}
func (a *SessionJournalAdapter) AttemptCommandsAbsent(config, attempt *value.Value) (bool, error) {
	dir, e := a.stateDir(config)
	if e != nil {
		if journal.Classified(e) || sourceClass(e, "OSError", "ValueError", "JSONDecodeError", "KeyError", "TypeError", "UnicodeDecodeError", "UnicodeEncodeError") {
			return false, nil
		}
		return false, e
	}
	if dir == "" {
		return false, nil
	}
	id, e := nativeRequired(attempt, "attempt_id")
	if e != nil {
		if journal.Classified(e) {
			return false, nil
		}
		return false, e
	}
	name, e := a.pathText(id)
	if e != nil {
		if journal.Classified(e) || sourceClass(e, "ValueError", "TypeError", "UnicodeEncodeError") {
			return false, nil
		}
		return false, e
	}
	status := journal.Inspect(dir, name)
	if status.DirectReadErr != nil {
		return status.ClaimOnly, nil
	}
	if status.DirectParseErr != nil || status.NonObject {
		return false, nil
	}
	return status.ClaimOnly, nil
}
