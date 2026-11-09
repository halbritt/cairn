package coordination

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func TestStage2ConcreteProjection(t *testing.T) {
	big := strings.Repeat("9", 10000)
	lone := &value.Value{Kind: value.String, String: []rune{0xd800, 'x', 0xdc80}}
	record := nativeMap("before", true, "extension", value.Mapping(value.Member{Key: []rune{0xd800}, Value: lone}), "numbers", &value.Value{Kind: value.Array, Items: []*value.Value{value.IntegerText(big), {Kind: value.Float, Number: 1}, value.Boolean(true), {Kind: value.Null}}}, "after", false)
	before := nativeClone(record)
	p := LosslessJournalProjection{}
	projected, err := p.Project(record)
	if err != nil {
		t.Fatal(err)
	}
	if projected["numbers"].([]any)[0] != json.Number(big) || projected["numbers"].([]any)[1] != float64(1) {
		t.Fatal("scalar representation lost", projected["numbers"])
	}
	key := string([]byte{0xed, 0xa0, 0x80})
	if projected["extension"].(map[string]any)[key] != journalText(lone.String) {
		t.Fatal("WTF-8 key/value replaced", projected)
	}
	projected["before"] = "not applied"
	projected["inbox_journals"] = []any{json.Number(big), journalText(lone.String), math.Copysign(0, -1), true, nil}
	if err = p.ApplyJournals(record, projected); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(record.Without("inbox_journals"), before) {
		t.Fatal("projection changed unrelated record values or order", record)
	}
	index := record.Get("inbox_journals")
	if index.Items[0].Integer != big || !reflect.DeepEqual(index.Items[1], lone) || !math.Signbit(index.Items[2].Number) || index.Items[3].Kind != value.Bool || index.Items[4].Kind != value.Null {
		t.Fatal(index)
	}
	stage2OrderedKeys(t, record, "before", "extension", "numbers", "after", "inbox_journals")
}

func TestStage2ConcreteJournalAdapter(t *testing.T) {
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "intents"), 0700); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"history", stage2Attempt} {
		raw := `{"schema":"cairn.inbox-intent/1","attempt_id":"` + id + `","kind":"notice","session":{"agent_id":"` + stage2Agent + `","execution_id":"` + stage2Execution + `"}}`
		if err := os.WriteFile(filepath.Join(dir, "intents", id+".json"), []byte(raw), 0600); err != nil {
			t.Fatal(err)
		}
	}
	record := stage2State()
	original := nativeClone(record)
	a := &SessionJournalAdapter{Options: journal.Options{Runner: journal.RunnerFunc(func(context.Context, []string, string) (journal.Result, error) {
		t.Fatal("claim-only journal must not execute")
		return journal.Result{}, nil
	})}}
	ok, err := a.FlushInboxIntents(context.Background(), nativeMap("state_dir", dir), record, FlushAttempt)
	if err != nil || !ok {
		t.Fatal(ok, err)
	}
	ids := record.Get("inbox_journals")
	if ids == nil || len(ids.Items) != 1 || !value.SameText(ids.Items[0], stage2Attempt) {
		t.Fatal("journal seeding/append/prune did not return to native record", ids)
	}
	if !reflect.DeepEqual(record.Without("inbox_journals"), original) {
		t.Fatal("unrelated state changed", record)
	}
	ok, err = a.SettledJournalIndex(nativeMap("state_dir", dir), record)
	if err != nil || !ok {
		t.Fatal(ok, err)
	}
	if err = os.WriteFile(filepath.Join(dir, "intents", stage2Attempt+".json"), []byte(`{"completion":{"status":"pending"}}`), 0600); err != nil {
		t.Fatal(err)
	}
	ok, err = a.SettledJournalIndex(nativeMap("state_dir", dir), record)
	if err != nil || ok {
		t.Fatal("pending journal considered settled", ok, err)
	}
}

func TestStage2ProcessHooks(t *testing.T) {
	root := procFixtureRoot(t)
	writeProcessFixture(t, root, processFixture{pid: 900, start: 100, argv: []string{"claude", "daemon"}})
	hooks := NewProcessHooks(root)
	ref := nativeMap("pid", 900, "start", 100, "boot", fixtureBoot)
	if alive, err := hooks.Alive(ref); err != nil || !alive {
		t.Fatal(alive, err)
	}
	if daemon, err := hooks.Daemon(ref); err != nil || !daemon {
		t.Fatal(daemon, err)
	}
	for _, state := range []string{"Z", "X"} {
		writeProcessFixture(t, root, processFixture{pid: 900, state: state, start: 100})
		alive, err := hooks.Alive(nativeMap("pid", 900))
		if err != nil || alive {
			t.Fatal("dead state indexed later identity keys", state, alive, err)
		}
	}
	writeProcessFixture(t, root, processFixture{pid: 900, start: 100})
	_, err := hooks.Alive(nativeMap("pid", 900))
	var missing *journal.KeyError
	if !errors.As(err, &missing) || missing.Key != "start" {
		t.Fatal(err)
	}
	alive, err := hooks.Alive(nativeMap("pid", 900, "start", 99))
	if err != nil || alive {
		t.Fatal("mismatching start indexed missing boot", alive, err)
	}
	alive, err = hooks.Alive(nativeMap("pid", 999999))
	if err != nil || alive {
		t.Fatal("missing process did not short-circuit", alive, err)
	}
	// Source retains the pid argument's scalar type and unbounded stat integers.
	ref.Set("pid", value.Text("900"))
	ref.Set("start", value.IntegerText(strings.Repeat("8", 100)))
	raw, err := os.ReadFile(filepath.Join(root, "900/stat"))
	if err != nil {
		t.Fatal(err)
	}
	raw = []byte(strings.Replace(string(raw), " 100 ", " "+strings.Repeat("8", 100)+" ", 1))
	if err = os.WriteFile(filepath.Join(root, "900/stat"), raw, 0600); err != nil {
		t.Fatal(err)
	}
	alive, err = hooks.Alive(ref)
	if err != nil || !alive {
		t.Fatal("identity coerced to Go process integer bounds", alive, err)
	}
}

func TestStage2ConcreteDaemonFailures(t *testing.T) {
	for _, tc := range []struct {
		name string
		raw  []byte
		file string
	}{
		{"stat", []byte("broken"), "stat"}, {"comm", []byte{0xff}, "comm"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			root := procFixtureRoot(t)
			writeProcessFixture(t, root, processFixture{pid: 900, start: 100, argv: []string{"claude", "daemon"}})
			if err := os.WriteFile(filepath.Join(root, "900", tc.file), tc.raw, 0600); err != nil {
				t.Fatal(err)
			}
			record := stage2State()
			record.Get("process").Set("boot", value.Text(fixtureBoot))
			nativePop(record.Get("inbox_attempt"), "native_turn_id")
			nativePop(record.Get("inbox_intent"), "native_turn_id")
			e, h, config := stage2Engine(t, "claude", record)
			e.Hooks = NewProcessHooks(root)
			_, err := e.RecoverDaemonStartClaim(context.Background(), config, RecoveryRequest{NativeSessionID: stage2Agent, AttemptID: stage2Attempt, CheckOnly: true})
			stage2Code(t, err, "INVALID_REQUEST")
			if len(h.Persisted) != 0 || len(h.Calls) != 0 {
				t.Fatal(h.Persisted, h.Calls)
			}
		})
	}
	t.Run("snapshot_identity", func(t *testing.T) {
		root := procFixtureRoot(t)
		writeProcessFixture(t, root, processFixture{pid: 900, start: 100, argv: []string{"claude", "daemon"}})
		record := stage2State()
		record.Get("process").Set("boot", value.Text(fixtureBoot))
		record.Get("process").Set("start", value.IntegerText("101"))
		nativePop(record.Get("inbox_attempt"), "native_turn_id")
		nativePop(record.Get("inbox_intent"), "native_turn_id")
		e, _, config := stage2Engine(t, "claude", record)
		e.Hooks = NewProcessHooks(root)
		probe := e.Hooks.DaemonView
		e.Hooks.DaemonView = func(ref *value.Value) (*NativeProcView, error) {
			v, err := probe(ref)
			writeProcessFixture(t, root, processFixture{pid: 900, start: 101, argv: []string{"claude", "daemon"}})
			return v, err
		}
		_, err := e.RecoverDaemonStartClaim(context.Background(), config, RecoveryRequest{NativeSessionID: stage2Agent, AttemptID: stage2Attempt, CheckOnly: true})
		stage2Code(t, err, "INVALID_REQUEST")
	})
}

func TestStage2CarriedLazyRecovery(t *testing.T) {
	t.Run("health_harness_before_mapping", func(t *testing.T) {
		e, _, config := stage2Engine(t, "other", value.Text("non-object"))
		_, err := e.CheckCodexNativeHealth(context.Background(), config, stage2Agent)
		stage2Code(t, err, "INVALID_REQUEST")
		_, err = e.ReconcileCodexQueueWake(context.Background(), config, RecoveryRequest{NativeSessionID: stage2Agent})
		var nonObject *journal.NonObjectError
		if !errors.As(err, &nonObject) {
			t.Fatal("reconcile must map before harness", err)
		}
	})
	for _, tc := range []struct {
		name              string
		delivery, session bool
		wake              bool
		refs              int
		attribute         bool
	}{
		{"unreached_intent", true, true, false, 1, false},
		{"delivery_fallback", false, true, false, 0, true},
		{"session_fallback", true, false, false, 0, true},
		{"two_ref_comparisons", true, true, true, 2, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			record := stage2State()
			record.Set("inbox_intent", value.Text("corrupt but lazy"))
			record.Set("idle_wake", nativeMap("transport", "other"))
			if !tc.delivery {
				record.Get("inbox_attempt").Get("delivery").Set("delivery_id", value.Text(""))
			}
			if !tc.session {
				record.Get("inbox_attempt").Set("session", value.Mapping())
			}
			if tc.wake {
				record.Set("idle_wake", nativeMap("transport", "codex-queue", "session", record.Get("inbox_attempt").Get("session"), "native_id", stage2Agent, "delivery_id", stage2Delivery, "queued_submission_id", stage2Turn))
			}
			e, h, config := stage2Engine(t, "codex", record)
			calls := 0
			ref := record.Get("inbox_attempt").Get("session")
			h.SessionRef = func(*value.Value) (*value.Value, error) { calls++; return ref, nil }
			_, err := e.ReconcileCodexQueueWake(context.Background(), config, RecoveryRequest{NativeSessionID: stage2Agent, DeliveryID: stage2Delivery, QueuedSubmissionID: stage2Turn, CheckOnly: true})
			if tc.attribute {
				var nonObject *journal.NonObjectError
				if !errors.As(err, &nonObject) {
					t.Fatal(err)
				}
			} else if err != nil {
				t.Fatal(err)
			}
			// codexStoreDelivery performs its own Source session_ref after guards.
			want := tc.refs
			if !tc.attribute {
				want++
			}
			if calls != want {
				t.Fatal(calls, want)
			}
		})
	}
	t.Run("unreached_ref", func(t *testing.T) {
		record := stage2State()
		nativePop(record.Get("agent"), "agent_id", "execution_id")
		record.Set("idle_wake", value.Mapping())
		record.Get("inbox_attempt").Get("delivery").Set("delivery_id", value.Text("other"))
		record.Set("inbox_intent", value.Text("unreached"))
		e, h, config := stage2Engine(t, "codex", record)
		h.SessionRef = func(*value.Value) (*value.Value, error) { t.Fatal("unreached session_ref"); return nil, nil }
		_, err := e.ReconcileCodexQueueWake(context.Background(), config, RecoveryRequest{NativeSessionID: stage2Agent, DeliveryID: stage2Delivery, QueuedSubmissionID: stage2Turn})
		stage2Code(t, err, "INVALID_REQUEST")
	})
}
