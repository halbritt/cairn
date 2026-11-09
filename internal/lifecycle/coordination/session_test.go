package coordination

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

const stage2Agent = "11111111-2222-4333-8444-555555555555"
const stage2Execution = "22222222-3333-4444-8555-666666666666"
const stage2Attempt = "33333333-4444-4555-8666-777777777777"
const stage2Delivery = "44444444-5555-4666-8777-888888888888"
const stage2Turn = "55555555-6666-4777-8888-999999999999"

func stage2State() *value.Value {
	ref := nativeMap("agent_id", stage2Agent, "execution_id", stage2Execution)
	attempt := nativeMap("attempt_id", stage2Attempt, "session", ref, "native_turn_id", stage2Turn, "created_at", "2026-11-09T00:00:00.000Z", "delivery", nativeMap("delivery_id", stage2Delivery, "lease_id", stage2Attempt, "event", nativeMap("event_id", stage2Delivery, "kind", "notice")))
	return nativeMap("agent", nativeMap("agent_id", stage2Agent, "execution_id", stage2Execution, "native_session_id", stage2Agent, "inbox", "agent/"+stage2Agent), "process", nativeMap("pid", 900, "start", 100, "boot", stage2Agent), "workspace", "/synthetic", "inbox_attempt", attempt, "inbox_intent", nativeMap("request_id", stage2Attempt, "session", ref, "native_turn_id", stage2Turn), "extension", nativeMap("float", &value.Value{Kind: value.Float, Number: 1}, "integer", value.IntegerText("1"), "bool", true))
}
func stage2Session() (*NativeSession, *MemoryHost) {
	h := NewMemoryHost(nil)
	h.NewID = func() string { return stage2Delivery }
	return &NativeSession{Config: nativeMap("binding", "fixture", "state_dir", ""), Path: "synthetic", Record: stage2State(), Host: &h.Host}, h
}
func stage2Code(t *testing.T, err error, code string) {
	t.Helper()
	if nativeCode(err) != code {
		t.Fatalf("error %v, want %s", err, code)
	}
}

type stage2Boundary struct {
	flush      func(FlushScope) (bool, error)
	committed  func() (bool, error)
	diagnostic func() error
}

func (b stage2Boundary) Flush(_ context.Context, _ *value.Value, _ *value.Value, scope FlushScope) (bool, error) {
	if b.flush != nil {
		return b.flush(scope)
	}
	return true, nil
}
func (b stage2Boundary) Committed(*value.Value, *value.Value) (bool, error) {
	if b.committed != nil {
		return b.committed()
	}
	return false, nil
}
func (b stage2Boundary) TurnEndDiagnostic(*value.Value, *value.Value) error {
	if b.diagnostic != nil {
		return b.diagnostic()
	}
	return nil
}

func TestStage2NativeSessionSettlementTaxonomy(t *testing.T) {
	for _, tc := range []struct {
		name          string
		strict, flush bool
		err           error
		want          string
		saves         int
	}{
		{"strict-unsettled", true, false, nil, SettlementPending, 2}, {"nonstrict-unsettled", false, false, nil, "held", 2},
		{"classified-refusal", false, false, nativeRefusal("STALE_LEASE", "synthetic"), SettlementPending, 2},
		{"classified-io", false, false, &os.PathError{Op: "read", Path: "fixture", Err: syscall.EIO}, SettlementPending, 2},
		{"classified-value", false, false, &value.Error{Class: "ValueError", Message: "synthetic"}, SettlementPending, 2},
		{"escaping-nonobject", true, false, &journal.NonObjectError{Field: "journal"}, "held", 1},
		{"escaping-attribute", true, false, &value.Error{Class: "AttributeError", Message: "synthetic"}, "held", 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s, h := stage2Session()
			s.Record.Set("active_prompt", nativeMap("id", stage2Turn))
			s.Record.Set("delivered_since_idle", value.Boolean(true))
			extension := nativeClone(s.Record.Get("extension"))
			s.Record.Members = append(s.Record.Members, value.Member{Key: []rune{0xd800}, Value: value.Text("retained")})
			h.Journal = stage2Boundary{flush: func(scope FlushScope) (bool, error) {
				if scope != FlushAttempt || len(h.Persisted) != 1 || !value.Truth(h.Persisted[0].Get("inbox_turn_ended")) || value.Truth(h.Persisted[0].Get("delivered_since_idle")) || h.Persisted[0].Get("active_prompt") != nil {
					t.Fatal("settlement persisted in wrong order", h.Persisted)
				}
				return tc.flush, tc.err
			}}
			result, err := s.Settle(context.Background(), "proof", nativeMap("settlement", "held"), value.Text(stage2Turn), tc.strict)
			if tc.saves == 1 {
				if err != tc.err {
					t.Fatal(err)
				}
			} else if err != nil || !value.SameText(result.Get("settlement"), tc.want) {
				t.Fatal(result, err)
			}
			if len(h.Persisted) != tc.saves || !value.SameText(s.Record.Get("proof").Get("settlement"), tc.want) || !nativeEqual(s.Record.Get("extension"), extension) {
				t.Fatal(h.Persisted, s.Record)
			}
			if s.Record.Members[len(s.Record.Members)-2].Key == nil {
				t.Fatal("extension key lost")
			}
		})
	}
	s, h := stage2Session()
	h.EndTurn = func(context.Context, *NativeSession, bool) (bool, error) { return s.Clear() }
	out, err := s.Settle(context.Background(), "proof", nativeMap("settlement", "pending"), nil, true)
	if err != nil || !value.SameText(out.Get("settlement"), "turn_ended") {
		t.Fatal(out, err)
	}
}
func TestStage2NativeSessionReleaseAndRenew(t *testing.T) {
	s, h := stage2Session()
	s.Record.Set("inbox_close", nativeMap("request_id", stage2Agent, "session", s.Intent().Get("session"), "attempt_id", stage2Attempt, "reason", "delivery_completed"))
	h.Responses = map[string]MemoryResponse{"session-inbox-reconcile": func(body *value.Value) (*value.Value, error) {
		if !value.SameText(body.Get("request_id"), stage2Agent) {
			t.Fatal("saved UUID replaced", body)
		}
		return nativeMap(), nil
	}}
	released, err := s.Release(context.Background(), "turn_ended", false)
	if err != nil || !released || len(h.Calls) != 1 || s.Intent() != nil {
		t.Fatal(released, err, h.Calls)
	}
	s, h = stage2Session()
	h.Responses = map[string]MemoryResponse{"session-inbox-reconcile": func(*value.Value) (*value.Value, error) { return nil, nativeRefusal("DELIVERY_ACTIVE", "fixture") }}
	released, err = s.Release(context.Background(), "delivery_completed", false)
	if err != nil || released || s.Record.Get("inbox_close") != nil || s.Intent() == nil {
		t.Fatal(released, err, s.Record)
	}
	s, h = stage2Session()
	h.Responses = map[string]MemoryResponse{"session-inbox-reconcile": func(*value.Value) (*value.Value, error) { return nil, nativeRefusal("NOT_FOUND", "fixture") }}
	released, err = s.Release(context.Background(), "restore", true)
	if err != nil || !released || s.Intent() != nil {
		t.Fatal(released, err)
	}
	s, h = stage2Session()
	old := s.Attempt().Get("delivery")
	h.Responses = map[string]MemoryResponse{"event-renew": func(body *value.Value) (*value.Value, error) {
		if !nativeEqual(body.Get("lease_seconds"), value.IntegerText("90")) {
			t.Fatal(body)
		}
		return nil, nativeRefusal("STALE_LEASE", "fixture")
	}}
	delivery, err := s.Renew(context.Background(), s.Attempt())
	if err != nil || delivery != old || !value.Truth(s.Attempt().Get("lease_lapsed")) || len(h.Logged) != 1 || len(h.Persisted) != 1 {
		t.Fatal(delivery, err, h.Logged)
	}
	_, err = s.Renew(context.Background(), s.Attempt())
	if err != nil || len(h.Calls) != 1 || len(h.Persisted) != 1 {
		t.Fatal("lapsed lease replayed", err, h.Calls)
	}
}
func TestStage2NativeSessionAdmissionAndBatch(t *testing.T) {
	s, h := stage2Session()
	nativePop(s.Record, "inbox_attempt", "inbox_intent")
	h.Responses = map[string]MemoryResponse{"session-inbox-claim": func(body *value.Value) (*value.Value, error) {
		if len(h.Persisted) != 1 || !value.SameText(body.Get("request_id"), stage2Delivery) {
			t.Fatal("claim before durable intent", h.Persisted)
		}
		return nativeMap("attempt", stage2State().Get("inbox_attempt")), nil
	}}
	binding := value.Mapping(value.Member{Key: []rune{0xd800}, Value: value.Boolean(true)})
	attempt, err := s.AdmitInbox(context.Background(), binding)
	if err != nil || attempt == nil || len(s.Intent().Members) != 3 || s.Intent().Members[2].Key[0] != 0xd800 {
		t.Fatal(attempt, err, s.Intent())
	}
	s, h = stage2Session()
	_, err = s.AdmitInbox(context.Background(), nativeMap("request_id", "replacement"))
	var typ *journal.TypeError
	if !errors.As(err, &typ) || len(h.Calls) != 0 {
		t.Fatal("duplicate keyword admitted", err)
	}
	s, h = stage2Session()
	old := s.Attempt()
	next := nativeClone(old)
	next.Set("attempt_id", value.Text(stage2Delivery))
	next.Set("batch_root", value.Text(stage2Attempt))
	next.Set("batch_index", value.IntegerText("2"))
	next.Set("batch_limit", value.IntegerText("3"))
	s.Record.Set("batch_advance_intent", nativeMap("request_id", stage2Delivery, "session", old.Get("session"), "attempt_id", stage2Attempt, "version", true))
	s.Record.Set("inbox_completion", value.Text("old"))
	h.Responses = map[string]MemoryResponse{"session-inbox-advance": func(*value.Value) (*value.Value, error) { return nativeMap("attempt", next, "remainder", "next"), nil }}
	out, err := s.AdvanceBatch(context.Background(), false)
	if err != nil || out.Get("attempt") != next || s.Record.Get("batch_advance_intent") != nil || s.Record.Get("inbox_completion") != nil || !value.SameText(s.Intent().Get("request_id"), stage2Delivery) {
		t.Fatal(out, err, s.Record)
	}
	s, h = stage2Session()
	s.Record.Set("batch_advance_intent", nativeMap("request_id", stage2Delivery, "session", old.Get("session"), "attempt_id", stage2Attempt, "version", 1))
	h.Responses = map[string]MemoryResponse{"session-inbox-advance": func(*value.Value) (*value.Value, error) { return nil, nativeRefusal("NOT_FOUND", "fixture") }}
	_, err = s.AdvanceBatch(context.Background(), false)
	stage2Code(t, err, "NOT_FOUND")
	if s.Record.Get("batch_advance_intent") != nil {
		t.Fatal("final refusal retained")
	}
}
func TestStage2NativeSessionOrganicAndRetirement(t *testing.T) {
	s, h := stage2Session()
	nativePop(s.Record, "inbox_attempt", "inbox_intent")
	selected := stage2State().Get("inbox_attempt")
	selected.Set("native_turn_id", value.Text(stage2Turn))
	selected.Set("admission_kind", value.Text("organic"))
	selected.Set("organic_root", value.Text(stage2Agent))
	h.Responses = map[string]MemoryResponse{"session-organic-notice-claim": func(*value.Value) (*value.Value, error) {
		return nativeMap("decision", "admitted", "attempt", selected, "root_id", stage2Agent), nil
	}}
	out, err := s.AdmitOrganicNotice(context.Background(), value.Text(stage2Turn), value.Text("owner"), nativeMap("signed", true))
	if err != nil || out == nil || s.Attempt() != selected || s.Record.Get("organic_claim_intent") != nil || !value.SameText(s.Record.Get("inbox_intent_kind"), "organic") {
		t.Fatal(out, err)
	}
	s, h = stage2Session()
	nativePop(s.Record, "inbox_attempt", "inbox_intent")
	h.Responses = map[string]MemoryResponse{"session-organic-notice-claim": func(*value.Value) (*value.Value, error) { return nil, nativeRefusal("FEATURE_DISABLED", "fixture") }}
	_, err = s.AdmitOrganicNotice(context.Background(), value.Text(stage2Turn), nil, nil)
	stage2Code(t, err, "FEATURE_DISABLED")
	if s.Record.Get("organic_claim_intent") != nil {
		t.Fatal("fresh final refusal persisted")
	}
	s, h = stage2Session()
	nativePop(s.Record, "agent")
	s.Record.Set("registration", nativeMap("binding", "fixture"))
	order := []string{}
	h.Responses = map[string]MemoryResponse{"agent-register": func(*value.Value) (*value.Value, error) {
		if len(h.Persisted) == 0 || !value.Truth(h.Persisted[0].Get("ending")) {
			t.Fatal("API before ending save")
		}
		order = append(order, "register")
		return stage2State().Get("agent"), nil
	}, "agent-leave": func(*value.Value) (*value.Value, error) {
		order = append(order, "leave")
		return nil, nativeRefusal("STALE_SESSION", "fixture")
	}}
	h.Journal = stage2Boundary{flush: func(scope FlushScope) (bool, error) {
		if scope != FlushAll {
			t.Fatal(scope)
		}
		order = append(order, "flush")
		return true, nil
	}}
	h.Release = func(_ context.Context, _ *NativeSession, reason string, fenced bool) (bool, error) {
		if !fenced || reason != "fixture_end" {
			t.Fatal(reason, fenced)
		}
		order = append(order, "release")
		return true, nil
	}
	if err = s.Retire(context.Background(), "fixture_end", 0); err != nil || !value.Truth(s.Record.Get("retired")) || strings.Join(order, ",") != "register,flush,leave,release" {
		t.Fatal(order, err)
	}
	s, h = stage2Session()
	h.FlushResult = false
	err = s.Retire(context.Background(), "fixture_end", 0)
	stage2Code(t, err, "INTENT_PENDING")
	if len(h.Calls) != 0 || !value.Truth(s.Record.Get("ending")) || value.Truth(s.Record.Get("retired")) {
		t.Fatal(h.Calls, s.Record)
	}
}
func TestStage2NativeSessionClearAndOpen(t *testing.T) {
	s, h := stage2Session()
	for _, key := range AttemptKeys {
		s.Record.Set(key, value.Boolean(true))
	}
	s.Record.Set("keep", value.Text("extension"))
	if len(AttemptKeys) != 23 {
		t.Fatal(AttemptKeys)
	}
	if _, err := s.Clear(); err != nil {
		t.Fatal(err)
	}
	for _, key := range AttemptKeys {
		if s.Record.Get(key) != nil {
			t.Fatal("uncleared", key)
		}
	}
	if !value.SameText(s.Record.Get("keep"), "extension") {
		t.Fatal(s.Record)
	}
	root := t.TempDir()
	path := filepath.Join(root, "state.json")
	locked, loaded, closed := false, false, false
	h.Locate = func(*value.Value, string) (string, error) { return path, nil }
	h.Exists = func(string) (bool, error) { return false, nil }
	h.Lock = func(string) (io.Closer, error) {
		locked = true
		return stage2Closer{func() error { closed = true; return nil }}, nil
	}
	h.Load = func(string) (*value.Value, error) { loaded = true; return value.Mapping(), nil }
	opened, release, err := OpenSession(value.Mapping(), stage2Agent, &h.Host, true)
	if err != nil || !locked || loaded || opened.Record.Kind != value.Object {
		t.Fatal(opened, err)
	}
	if err = release(); err != nil || !closed {
		t.Fatal(err)
	}
}

type stage2Closer struct{ close func() error }

func (c stage2Closer) Close() error { return c.close() }

// Test-only projection over admitted synthetic scalar metadata. All unknown
// native extensions remain in the original ordered Value and are never rebuilt.
type stage2Projection struct{}

func (stage2Projection) Project(record *value.Value) (map[string]any, error) {
	out := map[string]any{}
	for _, key := range []string{"agent", "inbox_attempt", "inbox_intent", "inbox_journals"} {
		if v := record.Get(key); v != nil {
			projected, err := stage2ProjectValue(v)
			if err != nil {
				return nil, err
			}
			out[key] = projected
		}
	}
	return out, nil
}
func stage2ProjectValue(v *value.Value) (any, error) {
	if v == nil {
		return nil, nil
	}
	switch v.Kind {
	case value.Null:
		return nil, nil
	case value.Bool:
		return v.Boolean, nil
	case value.Int:
		return json.Number(v.Integer), nil
	case value.Float:
		return v.Number, nil
	case value.String:
		for _, r := range v.String {
			if !utf8.ValidRune(r) {
				return nil, &value.ProfileUnavailable{}
			}
		}
		return string(v.String), nil
	case value.Array:
		out := []any{}
		for _, item := range v.Items {
			x, err := stage2ProjectValue(item)
			if err != nil {
				return nil, err
			}
			out = append(out, x)
		}
		return out, nil
	case value.Object:
		out := map[string]any{}
		for _, m := range v.Members {
			x, err := stage2ProjectValue(m.Value)
			if err != nil {
				return nil, err
			}
			out[string(m.Key)] = x
		}
		return out, nil
	}
	return nil, &value.ProfileUnavailable{}
}
func (stage2Projection) ApplyJournals(record *value.Value, state map[string]any) error {
	raw, present := state["inbox_journals"]
	if !present {
		return nil
	}
	items, ok := raw.([]any)
	if !ok {
		return &journal.TypeError{Field: "inbox_journals"}
	}
	out := &value.Value{Kind: value.Array}
	for _, item := range items {
		s, ok := item.(string)
		if !ok {
			return &value.ProfileUnavailable{}
		}
		out.Items = append(out.Items, value.Text(s))
	}
	record.Set("inbox_journals", out)
	return nil
}
func TestStage2JournalBoundaryAndAdapter(t *testing.T) {
	dir := t.TempDir()
	s, h := stage2Session()
	s.Config.Set("state_dir", value.Text(dir))
	adapter := &SessionJournalAdapter{Projection: stage2Projection{}, Options: journal.Options{Runner: journal.RunnerFunc(func(context.Context, []string, string) (journal.Result, error) {
		t.Fatal("unexpected process runner")
		return journal.Result{}, nil
	})}, Now: func() time.Time { return time.Unix(100, 0) }, Log: func(string) error { return nil }}
	h.Journal = adapter
	if err := os.MkdirAll(filepath.Join(dir, "intents"), 0700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "intents", stage2Attempt+".json")
	raw := `{"schema":"cairn.inbox-intent/1","attempt_id":"` + stage2Attempt + `","kind":"notice","session":{"agent_id":"` + stage2Agent + `","execution_id":"` + stage2Execution + `"},"completion":{"status":"committed"}}`
	if err := os.WriteFile(path, []byte(raw), 0600); err != nil {
		t.Fatal(err)
	}
	committed, err := adapter.Committed(s.Config, s.Record)
	if err != nil || !committed {
		t.Fatal(committed, err)
	}
	state, err := adapter.JournalCompletionState(s.Config, s.Attempt())
	if err != nil || state != "committed" {
		t.Fatal(state, err)
	}
	absent, err := adapter.AttemptCommandsAbsent(s.Config, s.Attempt())
	if err != nil || absent {
		t.Fatal(absent, err)
	}
	if err = adapter.TurnEndDiagnostic(s.Config, s.Record); err != nil {
		t.Fatal(err)
	}
	first := nativeClone(s.Record.Get("unacknowledged_turn_end_diagnostic"))
	if err = adapter.TurnEndDiagnostic(s.Config, s.Record); err != nil || !nativeEqual(first, s.Record.Get("unacknowledged_turn_end_diagnostic")) {
		t.Fatal(err)
	}
	// SessionJournals owns agent-only seeding; adapter applies its index mutation.
	if _, err = adapter.Flush(context.Background(), s.Config, s.Record, FlushAll); err != nil {
		t.Fatal(err)
	}
	if s.Record.Get("inbox_journals") == nil {
		t.Fatal("journal mutation not adopted")
	}
	if err = os.Remove(path); err != nil {
		t.Fatal(err)
	}
	absent, err = adapter.AttemptCommandsAbsent(s.Config, s.Attempt())
	if err != nil || !absent {
		t.Fatal(absent, err)
	}
}

func TestStage2NativeSessionRetirementOrganicControl(t *testing.T) {
	for _, advance := range []bool{false, true} {
		s, h := stage2Session()
		old := s.Attempt()
		old.Set("admission_kind", value.Text("organic"))
		old.Set("organic_root", value.Text(stage2Agent))
		selected := nativeClone(old)
		if advance {
			selected.Set("attempt_id", value.Text(stage2Execution))
			selected.Set("batch_root", value.Text(stage2Attempt))
			selected.Set("batch_index", value.IntegerText("2"))
			s.Record.Set("organic_advance_intent", nativeMap("request_id", stage2Delivery, "session", old.Get("session"), "attempt_id", stage2Attempt, "root_id", stage2Agent, "version", 1))
		} else {
			s.Record.Set("organic_claim_intent", nativeMap("request_id", stage2Delivery, "session", old.Get("session"), "native_owner_turn_id", stage2Turn, "owner_input_origin", nil, "owner_input_attestation", nil))
		}
		h.Responses = map[string]MemoryResponse{"session-organic-notice-claim": func(*value.Value) (*value.Value, error) {
			return nil, nativeRefusal("NOT_FOUND", "synthetic lost receipt")
		}, "session-organic-notice-advance": func(*value.Value) (*value.Value, error) {
			return nil, nativeRefusal("AUTHORITY_DENIED", "synthetic lost handoff")
		}, "session-inbox-control": func(*value.Value) (*value.Value, error) { return nativeMap("attempt", selected), nil }}
		if err := s.Retire(context.Background(), "process_exited", 0); err != nil {
			t.Fatal(advance, err)
		}
		if !value.Truth(s.Record.Get("retired")) || s.Record.Get("organic_claim_intent") != nil || s.Record.Get("organic_advance_intent") != nil || s.Attempt() != nil {
			t.Fatal(s.Record)
		}
		operations := []string{}
		for _, call := range h.Calls {
			operations = append(operations, call.Operation)
		}
		first := "session-organic-notice-claim"
		if advance {
			first = "session-organic-notice-advance"
		}
		if strings.Join(operations, ",") != first+",session-inbox-control,agent-leave,session-inbox-reconcile" {
			t.Fatal(operations)
		}
	}
}
func TestStage2NativeSessionLoggingAndMalformedOwnership(t *testing.T) {
	s, h := stage2Session()
	h.Responses = map[string]MemoryResponse{"event-renew": func(*value.Value) (*value.Value, error) { return nil, nativeRefusal("STALE_LEASE", "synthetic") }}
	ioFailure := &os.PathError{Op: "write", Path: "synthetic-stderr", Err: syscall.EIO}
	h.Log = func(string) error { return ioFailure }
	_, err := s.Renew(context.Background(), s.Attempt())
	if err != ioFailure || len(h.Persisted) != 0 {
		t.Fatal("logging OSError was hidden", err, h.Persisted)
	}
	s, h = stage2Session()
	nativePop(s.Record, "inbox_attempt", "inbox_intent")
	selected := stage2State().Get("inbox_attempt")
	selected.Set("admission_kind", value.Text("organic"))
	selected.Set("organic_root", value.Text(stage2Agent))
	selected.Set("delivery", &value.Value{Kind: value.Array, Items: []*value.Value{value.Text("bad")}})
	h.Responses = map[string]MemoryResponse{"session-organic-notice-claim": func(*value.Value) (*value.Value, error) {
		return nativeMap("decision", "admitted", "root_id", stage2Agent, "attempt", selected), nil
	}}
	_, err = s.AdmitOrganicNotice(context.Background(), value.Text(stage2Turn), nil, nil)
	var nonObject *journal.NonObjectError
	if !errors.As(err, &nonObject) {
		t.Fatal("ownership AttributeError was reclassified", err)
	}
}
