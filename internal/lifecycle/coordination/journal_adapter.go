package coordination

import (
	"context"
	"fmt"
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
type SessionJournalAdapter struct {
	Projection JournalStateProjection
	Options    journal.Options
	Now        func() time.Time
	Log        func(string) error
	PathText   func(*value.Value) (string, error)
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
	if a.Projection == nil {
		return false, &value.ProfileUnavailable{}
	}
	projected, e := a.Projection.Project(record)
	if e != nil {
		return false, e
	}
	settled, e := journal.Flush(ctx, dir, projected, string(scope), a.Options)
	apply := a.Projection.ApplyJournals(record, projected)
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
	if a.Projection == nil {
		return false, &value.ProfileUnavailable{}
	}
	state, e := a.Projection.Project(record)
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
