package coordination

import (
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

type stage2Evidence func(context.Context, EvidenceKind, EvidenceRequest) (*value.Value, error)

func (f stage2Evidence) Read(ctx context.Context, kind EvidenceKind, r EvidenceRequest) (*value.Value, error) {
	return f(ctx, kind, r)
}
func stage2Engine(t *testing.T, harness string, record *value.Value) (*RecoveryEngine, *MemoryHost, *value.Value) {
	t.Helper()
	h := NewMemoryHost(nil)
	config := nativeMap("binding", "fixture", "harness", harness, "state_dir", t.TempDir(), "repo", "fixture", "inbox_recall_binding", true)
	path := filepath.Join(t.TempDir(), "session.json")
	h.Locate = func(*value.Value, string) (string, error) { return path, nil }
	h.Lock = func(string) (io.Closer, error) { return stage2Closer{func() error { return nil }}, nil }
	h.Exists = func(string) (bool, error) { return true, nil }
	h.Load = func(string) (*value.Value, error) { return nativeClone(record), nil }
	e := &RecoveryEngine{Host: &h.Host, Journal: &SessionJournalAdapter{Projection: stage2Projection{}}, Now: func() time.Time { return time.Unix(105, 0) }, Hooks: RecoveryHooks{Alive: func(*value.Value) (bool, error) { return true, nil }, Daemon: func(*value.Value) (bool, error) { return true, nil }, GrantState: func(context.Context, *value.Value, *value.Value, *value.Value) (string, error) { return "absent", nil }, GrantEvidence: func(context.Context, *value.Value, *value.Value, *value.Value) (string, *value.Value, error) {
		return "absent", nil, nil
	}, PreGrantLedgerAbsent: func(context.Context, *value.Value, *value.Value, *value.Value, *value.Value) (bool, error) {
		return false, nil
	}, ProbeCodex: func(_ context.Context, _ *value.Value, state *value.Value, force bool) (*value.Value, error) {
		if !force {
			t.Fatal("probe not forced")
		}
		health := nativeMap("process_alive", true, "status_type", "idle", "queued_present", false)
		state.Set("native_health", health)
		return health, nil
	}, ObserveTime: func(_ *value.Value, now float64) (*value.Value, *value.Value, error) {
		if now != 105 {
			t.Fatal(now)
		}
		return value.Text("synthetic timestamp"), value.IntegerText("5"), nil
	}}}
	e.Evidence = stage2Evidence(func(_ context.Context, kind EvidenceKind, _ EvidenceRequest) (*value.Value, error) {
		switch kind {
		case EvidenceDaemonWorker:
			return nativeMap("pid", 901, "start", 101, "state", "S", "parent", 900, "boot", stage2Agent), nil
		case EvidenceDaemonClaim:
			return nativeMap("last_turn_record_at", "2026-11-09T00:00:00.000Z", "records_after", 0, "transcript_bytes", 100), nil
		case EvidenceCodexLocators:
			return &value.Value{Kind: value.Array, Items: []*value.Value{value.Text("synthetic")}}, nil
		default:
			return nativeMap("schema", "synthetic-evidence", "native_turn_id", stage2Turn), nil
		}
	})
	return e, h, config
}
func stage2Recorded(record *value.Value, turn string) {
	record.Get("inbox_attempt").Set("native_turn_id", value.Text(turn))
	record.Get("inbox_intent").Set("native_turn_id", value.Text(turn))
	record.Set("wake_refusal_diagnostic", nativeMap("phase", "bridge_after_claim", "origin", "hook", "settlement", "held", "attempt_id", stage2Attempt, "delivery_id", stage2Delivery, "native_turn_id", turn, "at", 100))
}
func stage2OrderedKeys(t *testing.T, record *value.Value, keys ...string) {
	t.Helper()
	if record == nil || record.Kind != value.Object || len(record.Members) != len(keys) {
		t.Fatal("unexpected record fields", record, keys)
	}
	for i, key := range keys {
		if string(record.Members[i].Key) != key {
			t.Fatal("Source field order changed", record, keys)
		}
	}
}
func TestStage2RecoverDaemonStartClaim(t *testing.T) {
	record := stage2State()
	nativePop(record.Get("inbox_attempt"), "native_turn_id")
	nativePop(record.Get("inbox_intent"), "native_turn_id")
	e, h, config := stage2Engine(t, "claude", record)
	r := RecoveryRequest{NativeSessionID: stage2Agent, AttemptID: stage2Attempt, CheckOnly: true}
	out, err := e.RecoverDaemonStartClaim(context.Background(), config, r)
	if err != nil || !value.SameText(out.Get("status"), "eligible") || len(h.Persisted) != 0 {
		t.Fatal(out, err, h.Persisted)
	}
	stage2OrderedKeys(t, out.Get("worker"), "pid", "start", "boot")
	stage2OrderedKeys(t, out.Get("native_record").Get("daemon"), "pid", "start", "boot")
	stage2OrderedKeys(t, out.Get("native_record").Get("worker"), "pid", "start", "boot")
	r.CheckOnly = false
	out, err = e.RecoverDaemonStartClaim(context.Background(), config, r)
	if err != nil || out == nil || len(h.Persisted) == 0 || !nativeEqual(h.Persisted[0].Get("process").Get("pid"), value.IntegerText("901")) {
		t.Fatal(out, err, h.Persisted)
	}
	process := h.Persisted[0].Get("process")
	stage2OrderedKeys(t, process, "pid", "start", "state", "parent", "boot")
	if !nativeEqual(process, nativeMap("pid", 901, "start", 101, "state", "S", "parent", 900, "boot", stage2Agent)) {
		t.Fatal("first settlement save lost the worker reference", process)
	}
	proof := h.Persisted[0].Get("daemon_start_claim")
	stage2OrderedKeys(t, proof.Get("daemon"), "pid", "start", "boot")
	stage2OrderedKeys(t, proof.Get("worker"), "pid", "start", "boot")
	record.Set("tool_calls", value.Boolean(true))
	e, h, config = stage2Engine(t, "claude", record)
	_, err = e.RecoverDaemonStartClaim(context.Background(), config, r)
	stage2Code(t, err, "INVALID_REQUEST")
	if len(h.Calls) != 0 || len(h.Persisted) != 0 {
		t.Fatal("invalid start claim had effects")
	}
}
func TestStage2RecoverRefusedWake(t *testing.T) {
	record := stage2State()
	turn := "claude-channel:fixture"
	stage2Recorded(record, turn)
	e, h, config := stage2Engine(t, "claude", record)
	r := RecoveryRequest{NativeSessionID: stage2Agent, AttemptID: stage2Attempt, NativeTurnID: turn, CheckOnly: true}
	// The Source recorded/native Claude modes do not honor check_only.
	out, err := e.RecoverRefusedWake(context.Background(), config, r)
	if err != nil || !value.SameText(out.Get("origin"), "operator_recorded") || len(h.Persisted) == 0 {
		t.Fatal(out, err)
	}
	e, h, config = stage2Engine(t, "claude", record)
	out, err = e.RecoverNativeRefusedWake(context.Background(), config, r)
	if err != nil || !value.SameText(out.Get("origin"), "native_record") || len(h.Persisted) == 0 {
		t.Fatal(out, err)
	}
	e, h, config = stage2Engine(t, "claude", record)
	e.Hooks.GrantState = func(context.Context, *value.Value, *value.Value, *value.Value) (string, error) { return "present", nil }
	_, err = e.RecoverRefusedWake(context.Background(), config, r)
	stage2Code(t, err, "INVALID_REQUEST")
	if len(h.Persisted) != 0 {
		t.Fatal("grant-present recovery persisted")
	}
	record.Set("active_prompt", nativeMap("id", turn))
	e, _, config = stage2Engine(t, "claude", record)
	_, err = e.RecoverNativeRefusedWake(context.Background(), config, r)
	stage2Code(t, err, "INVALID_REQUEST")
}

func TestStage2DaemonHookErrorFamily(t *testing.T) {
	for _, tc := range []struct {
		name   string
		err    error
		caught bool
	}{
		{"path", &os.PathError{Op: "read", Path: "synthetic", Err: syscall.EIO}, true},
		{"syscall", &os.SyscallError{Syscall: "read", Err: syscall.EIO}, true},
		{"errno", syscall.EIO, true},
		{"source_os", &value.Error{Class: "OSError"}, true},
		{"source_value", &value.Error{Class: "ValueError"}, true},
		{"json_value", &value.Error{Class: "JSONDecodeError"}, true},
		{"journal_value", &journal.ValueError{Message: "synthetic"}, true},
		{"index", &value.Error{Class: "IndexError"}, true},
		{"source_key", &value.Error{Class: "KeyError"}, false},
		{"source_type", &value.Error{Class: "TypeError"}, false},
		{"journal_key", &journal.KeyError{Key: "process"}, false},
		{"journal_type", &journal.TypeError{Field: "process"}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			record := stage2State()
			nativePop(record.Get("inbox_attempt"), "native_turn_id")
			nativePop(record.Get("inbox_intent"), "native_turn_id")
			e, h, config := stage2Engine(t, "claude", record)
			e.Hooks.Daemon = func(*value.Value) (bool, error) { return false, tc.err }
			calls := 0
			e.Hooks.Alive = func(*value.Value) (bool, error) { calls++; return true, nil }
			_, err := e.RecoverDaemonStartClaim(context.Background(), config, RecoveryRequest{NativeSessionID: stage2Agent, AttemptID: stage2Attempt})
			if tc.caught {
				stage2Code(t, err, "INVALID_REQUEST")
			} else if err != tc.err {
				t.Fatal("uncaught Source exception changed", err, tc.err)
			}
			if calls != 0 || len(h.Calls) != 0 || len(h.Persisted) != 0 {
				t.Fatal("failed daemon check reached later effects", calls, h.Calls, h.Persisted)
			}
		})
	}
	t.Run("not_daemon", func(t *testing.T) {
		record := stage2State()
		nativePop(record.Get("inbox_attempt"), "native_turn_id")
		nativePop(record.Get("inbox_intent"), "native_turn_id")
		e, _, config := stage2Engine(t, "claude", record)
		e.Hooks.Daemon = func(*value.Value) (bool, error) { return false, nil }
		calls := 0
		e.Hooks.Alive = func(*value.Value) (bool, error) { calls++; return true, nil }
		_, err := e.RecoverDaemonStartClaim(context.Background(), config, RecoveryRequest{NativeSessionID: stage2Agent, AttemptID: stage2Attempt})
		stage2Code(t, err, "INVALID_REQUEST")
		if calls != 1 {
			t.Fatal("Alive must precede daemon role refusal", calls)
		}
		sentinel := &journal.KeyError{Key: "start"}
		e.Hooks.Alive = func(*value.Value) (bool, error) { calls++; return false, sentinel }
		_, err = e.RecoverDaemonStartClaim(context.Background(), config, RecoveryRequest{NativeSessionID: stage2Agent, AttemptID: stage2Attempt})
		if calls != 2 || err != sentinel {
			t.Fatal("Alive error hidden by daemon role refusal", calls, err)
		}
	})
}

func TestStage2RecoveryAliveOrder(t *testing.T) {
	for _, mode := range []string{"recorded", "native", "codex_refused", "codex_ended"} {
		for _, guard := range []string{"ending", "retired", "identity", "harness", "attempt"} {
			if guard == "harness" && (mode == "recorded" || mode == "native") {
				continue // Source's Claude refusal guard does not read harness.
			}
			t.Run(mode+"/"+guard, func(t *testing.T) {
				record := stage2State()
				turn, harness := "claude-channel:fixture", "claude"
				if mode == "codex_refused" || mode == "codex_ended" {
					turn, harness = stage2Turn, "codex"
				}
				stage2Recorded(record, turn)
				switch guard {
				case "ending", "retired":
					record.Set(guard, value.Boolean(true))
				case "identity":
					record.Get("agent").Set("native_session_id", value.Text(stage2Turn))
				case "harness":
					harness = "other"
				case "attempt":
					record.Get("inbox_attempt").Set("attempt_id", value.Text(stage2Turn))
				}
				e, h, config := stage2Engine(t, harness, record)
				calls := 0
				sentinel := errors.New("synthetic Alive error")
				e.Hooks.Alive = func(*value.Value) (bool, error) { calls++; return false, sentinel }
				r := RecoveryRequest{NativeSessionID: stage2Agent, AttemptID: stage2Attempt, NativeTurnID: turn, CheckOnly: true}
				var err error
				switch mode {
				case "recorded":
					_, err = e.RecoverRefusedWake(context.Background(), config, r)
				case "native":
					_, err = e.RecoverNativeRefusedWake(context.Background(), config, r)
				case "codex_refused":
					_, err = e.RecoverCodexRefusedWake(context.Background(), config, r)
				case "codex_ended":
					_, err = e.RecoverCodexEndedTurn(context.Background(), config, r)
				}
				if guard == "attempt" {
					if calls != 1 || err != sentinel {
						t.Fatal("attempt guard preceded Source's Alive call", calls, err)
					}
				} else {
					stage2Code(t, err, "INVALID_REQUEST")
					if calls != 0 {
						t.Fatal("Alive preceded identity/ended/harness guard", calls)
					}
				}
				if len(h.Calls) != 0 || len(h.Persisted) != 0 {
					t.Fatal("refused recovery had effects", h.Calls, h.Persisted)
				}
			})
		}
	}
}

func TestStage2RecoveryGuardPrecedence(t *testing.T) {
	for _, mode := range []string{"daemon", "recorded", "native", "codex_refused", "codex_ended"} {
		for _, guard := range []string{"identity", "ending", "attempt", "reached_ref"} {
			if guard == "reached_ref" && mode != "daemon" && mode != "codex_ended" {
				continue
			}
			t.Run(mode+"/"+guard, func(t *testing.T) {
				record := stage2State()
				harness, turn := "claude", "claude-channel:fixture"
				if mode == "codex_refused" || mode == "codex_ended" {
					harness, turn = "codex", stage2Turn
				}
				stage2Recorded(record, turn)
				if mode == "daemon" {
					nativePop(record.Get("inbox_attempt"), "native_turn_id")
					nativePop(record.Get("inbox_intent"), "native_turn_id")
				}
				switch guard {
				case "identity":
					nativePop(record, "agent")
				case "ending":
					record.Set("ending", value.Boolean(true))
				case "attempt":
					record.Get("inbox_attempt").Set("attempt_id", value.Text("other"))
				}
				if guard != "reached_ref" {
					record.Set("inbox_intent", value.Text("unreached intent"))
					record.Set("active_prompt", value.Text("unreached active prompt"))
					record.Set("wake_refusal_diagnostic", value.Text("unreached diagnostic"))
				}
				e, h, config := stage2Engine(t, harness, record)
				refCalls, aliveCalls := 0, 0
				sentinel := errors.New("synthetic session_ref error")
				h.SessionRef = func(*value.Value) (*value.Value, error) { refCalls++; return nil, sentinel }
				e.Hooks.Alive = func(*value.Value) (bool, error) { aliveCalls++; return true, nil }
				r := RecoveryRequest{NativeSessionID: stage2Agent, AttemptID: stage2Attempt, NativeTurnID: turn, CheckOnly: true}
				var err error
				switch mode {
				case "daemon":
					_, err = e.RecoverDaemonStartClaim(context.Background(), config, r)
				case "recorded":
					_, err = e.RecoverRefusedWake(context.Background(), config, r)
				case "native":
					_, err = e.RecoverNativeRefusedWake(context.Background(), config, r)
				case "codex_refused":
					_, err = e.RecoverCodexRefusedWake(context.Background(), config, r)
				case "codex_ended":
					_, err = e.RecoverCodexEndedTurn(context.Background(), config, r)
				}
				if guard == "reached_ref" {
					if err != sentinel || refCalls != 1 {
						t.Fatal("session_ref not reached at Source point", err, refCalls)
					}
				} else {
					stage2Code(t, err, "INVALID_REQUEST")
					if refCalls != 0 {
						t.Fatal("session_ref preceded a Source short-circuit", refCalls)
					}
				}
				wantAlive := 0
				if mode != "daemon" && (guard == "attempt" || guard == "reached_ref") {
					wantAlive = 1
				}
				if aliveCalls != wantAlive || len(h.Calls) != 0 || len(h.Persisted) != 0 {
					t.Fatal(aliveCalls, wantAlive, h.Calls, h.Persisted)
				}
			})
		}
	}
	for _, mode := range []string{"daemon", "codex_refused", "codex_ended"} {
		t.Run(mode+"/wrong_harness", func(t *testing.T) {
			record := stage2State()
			record.Set("agent", value.Text("unreached agent"))
			record.Set("inbox_intent", value.Text("unreached intent"))
			if mode == "daemon" {
				record.Set("inbox_attempt", value.Text("unreached attempt"))
			}
			e, h, config := stage2Engine(t, "other", record)
			h.SessionRef = func(*value.Value) (*value.Value, error) { t.Fatal("unreached ref"); return nil, nil }
			e.Hooks.Alive = func(*value.Value) (bool, error) { t.Fatal("unreached Alive"); return false, nil }
			r := RecoveryRequest{NativeSessionID: stage2Agent, AttemptID: stage2Attempt}
			var err error
			switch mode {
			case "daemon":
				_, err = e.RecoverDaemonStartClaim(context.Background(), config, r)
			case "codex_refused":
				_, err = e.RecoverCodexRefusedWake(context.Background(), config, r)
			case "codex_ended":
				_, err = e.RecoverCodexEndedTurn(context.Background(), config, r)
			}
			stage2Code(t, err, "INVALID_REQUEST")
		})
	}
}

func TestStage2RefusedWakeSessionRefOrder(t *testing.T) {
	for _, guard := range []string{"turn", "request", "session", "finished", "reached_ref"} {
		t.Run(guard, func(t *testing.T) {
			s, h := stage2Session()
			e := &RecoveryEngine{}
			s.Record.Set("active_prompt", value.Text("unreached active prompt"))
			switch guard {
			case "turn":
				s.Intent().Set("native_turn_id", value.Text("other"))
			case "request":
				s.Intent().Set("request_id", value.Text("other"))
			case "session":
				s.Intent().Set("session", value.Mapping())
			case "finished":
				s.Attempt().Set("finished_at", value.Text("done"))
			}
			calls := 0
			sentinel := errors.New("synthetic ref")
			h.SessionRef = func(*value.Value) (*value.Value, error) {
				calls++
				if guard == "finished" {
					return s.Attempt().Get("session"), nil
				}
				return nil, sentinel
			}
			ok, err := e.RefusedWakeUnprocessed(s, stage2Turn)
			if guard == "reached_ref" {
				if err != sentinel || calls != 1 {
					t.Fatal(ok, err, calls)
				}
			} else {
				want := 0
				if guard == "finished" {
					want = 1
				}
				if err != nil || ok || calls != want {
					t.Fatal(ok, err, calls)
				}
			}
		})
	}
}

func TestStage2AutoRefusalNoAgentPrecondition(t *testing.T) {
	for _, process := range []*value.Value{nil, &value.Value{Kind: value.Null}, value.Boolean(false), value.IntegerText("0"), value.Text(""), &value.Value{Kind: value.Array}} {
		record := stage2State()
		nativePop(record, "agent", "process")
		if process != nil {
			record.Set("process", process)
		}
		e, h, config := stage2Engine(t, "codex", record)
		calls := 0
		e.Hooks.Alive = func(got *value.Value) (bool, error) {
			calls++
			if got == nil || got.Kind != value.Object || len(got.Members) != 0 {
				t.Fatal("Source's process or {} was not supplied", got)
			}
			return false, nil
		}
		h.SessionRef = func(*value.Value) (*value.Value, error) { t.Fatal("unreached ref"); return nil, nil }
		s := &NativeSession{config, "synthetic", record, &h.Host}
		ok, err := e.AutoSettleCodexRefusedWake(context.Background(), s)
		if err != nil || ok || calls != 1 || len(h.Persisted) != 0 {
			t.Fatal(ok, err, calls)
		}
	}
	for _, guard := range []string{"harness", "ending", "retired", "missing_id"} {
		t.Run(guard, func(t *testing.T) {
			record := stage2State()
			record.Set("agent", value.Text("unused agent"))
			harness := "codex"
			switch guard {
			case "harness":
				harness = "other"
			case "missing_id":
				nativePop(record.Get("inbox_attempt"), "attempt_id")
			default:
				record.Set(guard, value.Boolean(true))
			}
			e, h, config := stage2Engine(t, harness, record)
			calls := 0
			e.Hooks.Alive = func(*value.Value) (bool, error) { calls++; return true, nil }
			ok, err := e.AutoSettleCodexRefusedWake(context.Background(), &NativeSession{config, "synthetic", record, &h.Host})
			want := 0
			if guard == "missing_id" {
				want = 1
			}
			if ok || err != nil || calls != want || len(h.Persisted) != 0 {
				t.Fatal(ok, err, calls)
			}
		})
	}
}

func TestStage2CodexStoreNonArrayDeliveries(t *testing.T) {
	for _, deliveries := range []*value.Value{value.Text("synthetic"), nativeMap("delivery_id", stage2Delivery), value.IntegerText("1"), &value.Value{Kind: value.Float, Number: 1}, value.Boolean(true), value.IntegerText("0"), &value.Value{Kind: value.Float}, value.Boolean(false)} {
		e, h, config := stage2Engine(t, "codex", stage2State())
		h.Responses = map[string]MemoryResponse{
			"session-inbox-ready": func(*value.Value) (*value.Value, error) {
				return nativeMap("delivery_id", stage2Delivery, "event_id", stage2Delivery), nil
			},
			"event-inspect": func(*value.Value) (*value.Value, error) {
				return nativeMap("event", nativeMap("repo", "fixture"), "deliveries", deliveries), nil
			},
		}
		s := &NativeSession{Config: config, Record: stage2State(), Host: &h.Host}
		match, ready, err := e.codexStoreDelivery(context.Background(), s, stage2Delivery, nil)
		if value.Truth(deliveries) && (deliveries.Kind == value.Int || deliveries.Kind == value.Float || deliveries.Kind == value.Bool) {
			var nonIterable *journal.TypeError
			if !errors.As(err, &nonIterable) || match != nil {
				t.Fatal("truthy non-iterable must raise TypeError", deliveries, match, err)
			}
		} else if err != nil || match != nil || !value.SameText(ready, stage2Delivery) {
			t.Fatal("iterable or falsy inspection lost ready identity or invented a match", match, ready, err)
		}
		if len(h.Calls) != 2 || len(h.Persisted) != 0 {
			t.Fatal(h.Calls, h.Persisted)
		}
	}
}
func TestStage2RecoverCodexModes(t *testing.T) {
	record := stage2State()
	stage2Recorded(record, stage2Turn)
	e, h, config := stage2Engine(t, "codex", record)
	r := RecoveryRequest{NativeSessionID: stage2Agent, AttemptID: stage2Attempt, CheckOnly: true}
	out, err := e.RecoverCodexRefusedWake(context.Background(), config, r)
	if err != nil || !value.SameText(out.Get("status"), "eligible") || len(h.Persisted) != 0 {
		t.Fatal(out, err)
	}
	e.Hooks.GrantState = func(context.Context, *value.Value, *value.Value, *value.Value) (string, error) { return "unknown", nil }
	e.Hooks.PreGrantLedgerAbsent = func(context.Context, *value.Value, *value.Value, *value.Value, *value.Value) (bool, error) {
		return true, nil
	}
	out, err = e.RecoverCodexRefusedWake(context.Background(), config, r)
	if err != nil || !value.SameText(out.Get("grant_evidence"), "pre_grant_refusal_without_ledger") {
		t.Fatal(out, err)
	}
	// Automatic recovery does not borrow the explicit-recovery pre-grant fallback.
	s := &NativeSession{config, "synthetic", nativeClone(record), &h.Host}
	ok, err := e.AutoSettleCodexRefusedWake(context.Background(), s)
	if err != nil || ok {
		t.Fatal(ok, err)
	}
	e.Hooks.GrantState = func(context.Context, *value.Value, *value.Value, *value.Value) (string, error) { return "absent", nil }
	e.Evidence = stage2Evidence(func(context.Context, EvidenceKind, EvidenceRequest) (*value.Value, error) {
		return nil, &value.ProfileUnavailable{}
	})
	_, err = e.AutoSettleCodexRefusedWake(context.Background(), s)
	var profile *value.ProfileUnavailable
	if !errors.As(err, &profile) {
		t.Fatal("profile failure hidden", err)
	}
	e, h, config = stage2Engine(t, "codex", record)
	out, err = e.RecoverCodexEndedTurn(context.Background(), config, r)
	if err != nil || !value.SameText(out.Get("status"), "eligible") || len(h.Persisted) != 0 {
		t.Fatal(out, err)
	}
	r.CheckOnly = false
	out, err = e.RecoverCodexEndedTurn(context.Background(), config, r)
	if err != nil || !value.SameText(out.Get("recovery_origin"), "codex_ended_turn") || len(h.Persisted) == 0 {
		t.Fatal(out, err)
	}
	e, h, config = stage2Engine(t, "codex", record)
	e.Hooks.ProbeCodex = func(context.Context, *value.Value, *value.Value, bool) (*value.Value, error) {
		return nativeMap("status_type", "active"), nil
	}
	_, err = e.RecoverCodexEndedTurn(context.Background(), config, r)
	stage2Code(t, err, "INVALID_REQUEST")
	if len(h.Persisted) != 0 {
		t.Fatal("active native thread settled")
	}
}
func stage2QueueState() *value.Value {
	record := stage2State()
	nativePop(record, "inbox_attempt", "inbox_intent")
	ref := nativeMap("agent_id", stage2Agent, "execution_id", stage2Execution)
	record.Set("idle_wake", nativeMap("transport", "codex-queue", "session", ref, "native_id", stage2Agent, "delivery_id", stage2Delivery, "queued_submission_id", stage2Turn, "event_id", stage2Delivery, "status", "queued"))
	record.Set("wake_refusal", value.Text("retained"))
	return record
}
func TestStage2ReconcileAndInspect(t *testing.T) {
	for _, outcome := range []string{"never_admitted", "completed", "cancelled", "admitted_active", "unknown"} {
		t.Run(outcome, func(t *testing.T) {
			record := stage2QueueState()
			e, h, config := stage2Engine(t, "codex", record)
			r := RecoveryRequest{NativeSessionID: stage2Agent, DeliveryID: stage2Delivery, QueuedSubmissionID: stage2Turn, Records: &value.Value{Kind: value.Array, Items: []*value.Value{value.Text("fixture")}}, CheckOnly: true}
			e.Evidence = stage2Evidence(func(context.Context, EvidenceKind, EvidenceRequest) (*value.Value, error) {
				return nativeMap("outcome", outcome), nil
			})
			e.Hooks.ProbeCodex = func(_ context.Context, _ *value.Value, state *value.Value, _ bool) (*value.Value, error) {
				state.Set("probe_extension", value.Boolean(true))
				return nativeMap("process_alive", true, "status_type", "idle", "queued_present", outcome == "admitted_active"), nil
			}
			storeState := "pending"
			if outcome == "completed" {
				storeState = "handled"
			}
			if outcome == "cancelled" {
				storeState = "cancelled"
			}
			h.Responses = map[string]MemoryResponse{"session-inbox-ready": func(*value.Value) (*value.Value, error) {
				return nativeMap("delivery_id", stage2Delivery, "event_id", stage2Delivery), nil
			}, "event-inspect": func(body *value.Value) (*value.Value, error) {
				if !nativeHasKeys(body, "event_id", "limit") {
					t.Fatal("extra event-inspect fields", body)
				}
				return nativeMap("event", nativeMap("repo", "fixture"), "deliveries", &value.Value{Kind: value.Array, Items: []*value.Value{nativeMap("delivery_id", stage2Delivery, "consumer", "agent/"+stage2Agent, "state", storeState, "attempts", 0)}}), nil
			}}
			out, err := e.ReconcileCodexQueueWake(context.Background(), config, r)
			if err != nil || !value.SameText(out.Get("outcome"), outcome) || len(h.Persisted) != 0 {
				t.Fatal(out, err)
			}
			r.CheckOnly = false
			out, err = e.ReconcileCodexQueueWake(context.Background(), config, r)
			if err != nil || len(h.Persisted) != 1 {
				t.Fatal(out, err, h.Persisted)
			}
			saved := h.Persisted[0]
			if saved.Get("probe_extension") != nil {
				t.Fatal("probe clone leaked into state")
			}
			switch outcome {
			case "completed", "cancelled", "never_admitted":
				if saved.Get("idle_wake") != nil || saved.Get("wake_reconciled") == nil {
					t.Fatal(saved)
				}
			case "admitted_active":
				if !value.SameText(saved.Get("idle_wake").Get("status"), "queued") || saved.Get("wake_refusal") != nil {
					t.Fatal(saved)
				}
			case "unknown":
				if !value.SameText(saved.Get("idle_wake").Get("status"), "refused") || saved.Get("wake_reconciliation") == nil {
					t.Fatal(saved)
				}
			}
		})
	}
	record := stage2State()
	stage2Recorded(record, stage2Turn)
	record.Get("wake_refusal_diagnostic").Set("error_class", value.Text("private/path/error"))
	record.Get("wake_refusal_diagnostic").Set("grant", value.Text("present"))
	record.Get("wake_refusal_diagnostic").Set("grant_kind", value.Text("ordinary_context"))
	record.Get("wake_refusal_diagnostic").Set("recall_attempts", value.Boolean(true))
	record.Get("wake_refusal_diagnostic").Set("refusal_stage", value.Text("required_render"))
	record.Get("wake_refusal_diagnostic").Set("refusal_reason", value.Text("required_context"))
	record.Get("wake_refusal_diagnostic").Set("budget_bytes", value.IntegerText("9500"))
	e, h, config := stage2Engine(t, "codex", record)
	obs, err := e.InspectHeldRefusal(context.Background(), config, RecoveryRequest{NativeSessionID: stage2Agent, AttemptID: stage2Attempt})
	if err != nil || !value.SameText(obs.Get("error_class"), "OtherError") || !value.SameText(obs.Get("evidence_state"), "ordinary_grant_present_manual_review") || obs.Get("recall_attempts") != nil || obs.Get("budget_bytes") == nil || len(h.Persisted) != 0 {
		t.Fatal(obs, err)
	}
	_, err = e.VerifyTurnRecovery(context.Background(), config, "unknown", "mode", RecoveryRequest{})
	stage2Code(t, err, "INVALID_REQUEST")
	_, err = e.VerifyTurnRecovery(context.Background(), config, "opencode", "mode", RecoveryRequest{})
	stage2Code(t, err, "INVALID_REQUEST")
}
func TestStage2NativeHealthAndNonAdmissionGuards(t *testing.T) {
	record := stage2QueueState()
	e, h, config := stage2Engine(t, "codex", record)
	out, err := e.CheckCodexNativeHealth(context.Background(), config, stage2Agent)
	if err != nil || out == nil || len(h.Persisted) != 1 || h.Persisted[0].Get("native_health") == nil {
		t.Fatal(out, err)
	}
	e, h, config = stage2Engine(t, "codex", record)
	e.Evidence = stage2Evidence(func(context.Context, EvidenceKind, EvidenceRequest) (*value.Value, error) {
		return nativeMap("outcome", "never_admitted"), nil
	})
	h.Responses = map[string]MemoryResponse{"session-inbox-ready": func(*value.Value) (*value.Value, error) { return nativeMap("delivery_id", stage2Delivery), nil }, "event-inspect": func(*value.Value) (*value.Value, error) {
		return nativeMap("deliveries", &value.Value{Kind: value.Array, Items: []*value.Value{nativeMap("delivery_id", stage2Delivery, "state", "pending", "attempts", false)}}), nil
	}}
	_, err = e.ReconcileCodexQueueWake(context.Background(), config, RecoveryRequest{NativeSessionID: stage2Agent, DeliveryID: stage2Delivery, QueuedSubmissionID: stage2Turn, Outcome: value.Text("never_admitted"), Records: &value.Value{Kind: value.Array, Items: []*value.Value{value.Text("fixture")}}})
	stage2Code(t, err, "INVALID_REQUEST")
	if len(h.Persisted) != 0 {
		t.Fatal("bool store attempts accepted")
	}
}

func TestStage2ResidualReconcileGuards(t *testing.T) {
	cases := []struct {
		name, harness string
		change        func(*value.Value)
	}{
		{"wrong_harness", "other", func(r *value.Value) { r.Set("agent", value.Text("unreached agent")) }},
		{"mismatched", "codex", func(r *value.Value) { r.Get("agent").Set("native_session_id", value.Text(stage2Turn)) }},
		{"ending", "codex", func(r *value.Value) { r.Set("ending", value.Boolean(true)) }},
		{"retired", "codex", func(r *value.Value) { r.Set("retired", value.Boolean(true)) }},
		{"absent_agent", "codex", func(r *value.Value) { nativePop(r, "agent") }},
	}
	for _, tc := range []struct {
		name string
		raw  *value.Value
	}{
		{"null", &value.Value{Kind: value.Null}},
		{"array", &value.Value{Kind: value.Array}},
		{"text", value.Text("")},
		{"zero", value.IntegerText("0")},
		{"false", value.Boolean(false)},
	} {
		cases = append(cases, struct {
			name, harness string
			change        func(*value.Value)
		}{
			"falsy_agent_" + tc.name, "codex", func(r *value.Value) { r.Set("agent", tc.raw) },
		})
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			record := stage2QueueState()
			record.Set("idle_wake", value.Text("unreached wake"))
			record.Set("inbox_attempt", value.Text("unreached attempt"))
			record.Set("inbox_intent", value.Text("unreached intent"))
			tc.change(record)
			e, h, config := stage2Engine(t, tc.harness, record)
			h.SessionRef = func(*value.Value) (*value.Value, error) { t.Fatal("unreached session_ref"); return nil, nil }
			e.Hooks.ProbeCodex = func(context.Context, *value.Value, *value.Value, bool) (*value.Value, error) {
				t.Fatal("unreached probe")
				return nil, nil
			}
			_, err := e.ReconcileCodexQueueWake(context.Background(), config, RecoveryRequest{NativeSessionID: stage2Agent, DeliveryID: stage2Delivery, QueuedSubmissionID: stage2Turn})
			stage2Code(t, err, "INVALID_REQUEST")
			if len(h.Calls) != 0 || len(h.Persisted) != 0 {
				t.Fatal("rejected reconciliation had effects", h.Calls, h.Persisted)
			}
		})
	}
}

func TestStage2ResidualHealthAgentDefault(t *testing.T) {
	for _, tc := range []struct {
		name, harness string
		agent         *value.Value
		attribute     bool
	}{
		{"wrong_harness", "other", value.Text("unreached agent"), false},
		{"absent", "codex", nil, false},
		{"null", "codex", &value.Value{Kind: value.Null}, true},
		{"array", "codex", &value.Value{Kind: value.Array}, true},
		{"text", "codex", value.Text(""), true},
		{"zero", "codex", value.IntegerText("0"), true},
		{"false", "codex", value.Boolean(false), true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			record := stage2State()
			nativePop(record, "agent")
			if tc.agent != nil {
				record.Set("agent", tc.agent)
			}
			e, h, config := stage2Engine(t, tc.harness, record)
			e.Hooks.ProbeCodex = func(context.Context, *value.Value, *value.Value, bool) (*value.Value, error) {
				t.Fatal("unreached health probe")
				return nil, nil
			}
			_, err := e.CheckCodexNativeHealth(context.Background(), config, stage2Agent)
			if tc.attribute {
				var nonObject *journal.NonObjectError
				if !errors.As(err, &nonObject) || nonObject.Field != "agent" {
					t.Fatal("present non-object agent lost AttributeError", err)
				}
			} else {
				stage2Code(t, err, "INVALID_REQUEST")
			}
			if len(h.Calls) != 0 || len(h.Persisted) != 0 {
				t.Fatal("rejected health check had effects", h.Calls, h.Persisted)
			}
		})
	}
}

func TestStage2ResidualRefusalDeliveryDefault(t *testing.T) {
	for _, tc := range []struct {
		name string
		raw  *value.Value
	}{
		{"null", &value.Value{Kind: value.Null}},
		{"array", &value.Value{Kind: value.Array}},
		{"text", value.Text("")},
		{"zero", value.IntegerText("0")},
		{"false", value.Boolean(false)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			record := stage2State()
			stage2Recorded(record, stage2Turn)
			record.Get("inbox_attempt").Set("delivery", tc.raw)
			e, h, config := stage2Engine(t, "codex", record)
			h.SessionRef = func(*value.Value) (*value.Value, error) {
				t.Fatal("delivery failure must precede unprocessed session_ref")
				return nil, nil
			}
			s := &NativeSession{config, "synthetic", record, &h.Host}
			_, _, _, err := e.codexRefusalReady(s, stage2Agent, stage2Attempt)
			var nonObject *journal.NonObjectError
			if !errors.As(err, &nonObject) || nonObject.Field != "delivery" {
				t.Fatal("present non-object delivery lost AttributeError", err)
			}
			_, err = e.RecoverCodexRefusedWake(context.Background(), config, RecoveryRequest{NativeSessionID: stage2Agent, AttemptID: stage2Attempt, CheckOnly: true})
			if !errors.As(err, &nonObject) || nonObject.Field != "delivery" {
				t.Fatal("entrypoint hid delivery AttributeError", err)
			}
			if len(h.Calls) != 0 || len(h.Persisted) != 0 {
				t.Fatal(h.Calls, h.Persisted)
			}
		})
	}
	for _, ready := range []bool{true, false} {
		t.Run(map[bool]string{true: "absent_match", false: "absent_mismatch"}[ready], func(t *testing.T) {
			record := stage2State()
			stage2Recorded(record, stage2Turn)
			nativePop(record.Get("inbox_attempt"), "delivery")
			if ready {
				nativePop(record.Get("wake_refusal_diagnostic"), "delivery_id")
			}
			e, h, config := stage2Engine(t, "codex", record)
			_, _, got, err := e.codexRefusalReady(&NativeSession{config, "synthetic", record, &h.Host}, stage2Agent, stage2Attempt)
			if err != nil || got != ready {
				t.Fatal("absent delivery did not default to an empty object", got, err)
			}
			out, err := e.RecoverCodexRefusedWake(context.Background(), config, RecoveryRequest{NativeSessionID: stage2Agent, AttemptID: stage2Attempt, CheckOnly: true})
			if ready {
				if err != nil || !value.SameText(out.Get("status"), "eligible") {
					t.Fatal(out, err)
				}
			} else {
				stage2Code(t, err, "INVALID_REQUEST")
			}
			if len(h.Calls) != 0 || len(h.Persisted) != 0 {
				t.Fatal("check-only readiness had effects", h.Calls, h.Persisted)
			}
		})
	}
}
