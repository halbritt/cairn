package coordination

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/install"
	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/ledger"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

const currentnessSession = "11111111-1111-4111-8111-111111111111"
const currentnessTurn = "22222222-2222-4222-8222-222222222222"
const currentnessLater = "00000000-0000-4000-8000-000000000001"

type grantCurrentnessFixture struct {
	cli                      *CLI
	root, memory, ledgerPath string
	statePath, journalPath   string
	turn                     string
	state, remote, journal   *value.Value
	selected                 WatchBindings
	next                     []install.File
	reads                    int
}

func privateCurrentnessDirectory(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	if err := os.Chmod(dir, 0700); err != nil {
		t.Fatal(err)
	}
	return dir
}

func newGrantCurrentnessFixture(t *testing.T, harness string) *grantCurrentnessFixture {
	t.Helper()
	f := &grantCurrentnessFixture{root: privateCurrentnessDirectory(t), turn: currentnessTurn}
	if harness == "claude" {
		f.turn = "claude-channel:owned-prompt"
	}
	f.memory = filepath.Join(f.root, "memory")
	stateDir := filepath.Join(f.root, "coord-state")
	manifestPath := filepath.Join(f.root, "binding.json")
	client := filepath.Join(f.root, "client")
	writePrivateFile(t, client, []byte("#!/bin/sh\nexit 99\n"), 0500)
	common := nativeMap("harness", harness, "cairn", client, "socket", filepath.Join(f.root, "fixture.sock"),
		"token_file", filepath.Join(f.root, "fixture.token"), "repo", "fixture:grant-currentness", "inbox_recall_binding", manifestPath)
	memory := nativeClone(common)
	memory.Set("state_dir", value.Text(f.memory))
	memory.Set("recall_mode", value.Text("agent_tools"))
	memory.Set("context_bytes", value.IntegerText("9500"))
	config := nativeClone(common)
	config.Set("state_dir", value.Text(stateDir))
	config.Set("native_delivery", value.Boolean(true))
	config.Set("binding", value.Text("currentness"))
	memoryPath, configPath := filepath.Join(f.root, "memory.json"), filepath.Join(f.root, "coord.json")
	profile := stage2ServicesProfile()
	write := func(path string, body *value.Value) string {
		stage2WriteValue(t, profile, path, body)
		raw, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256(raw)
		return hex.EncodeToString(sum[:])
	}
	// These are file/profile fixtures; API views below deliberately remain unit
	// doubles. The process driver supplies authenticated PostgreSQL evidence.
	for _, directory := range []string{stateDir, filepath.Join(stateDir, "intents"), f.memory, filepath.Join(f.root, "bindings")} {
		if err := os.MkdirAll(directory, 0700); err != nil {
			t.Fatal(err)
		}
	}
	mp, cp := write(memoryPath, memory), write(configPath, config)
	write(manifestPath, nativeMap("schema", "cairn.inbox-recall-binding/2", "enabled", false,
		"memory_config", nativeMap("path", memoryPath, "sha256", mp),
		"coordination_configs", nativeList(nativeMap("path", configPath, "sha256", cp))))
	f.selected = WatchBindings{Directory: filepath.Join(f.root, "bindings"), Pins: map[string]string{}}
	f.selected.Pins["one.json"] = write(filepath.Join(f.selected.Directory, "one.json"), config)
	writePrivateFile(t, filepath.Join(f.selected.Directory, ".watch.lock"), nil, 0600)
	nextPath := filepath.Join(f.root, "next.json")
	nextRaw := append([]byte("\n"), mustCurrentnessJSON(t, config)...)
	nextPin := writePrivateFile(t, nextPath, nextRaw, 0600)
	f.next = []install.File{{Name: "one.json", Path: nextPath, SHA256: nextPin}}
	ref := nativeMap("agent_id", stage2Agent, "execution_id", stage2Execution)
	result := nativeMap("record_id", stage2Turn, "version", 1)
	attempt := nativeMap("attempt_id", stage2Attempt, "session", ref, "native_turn_id", f.turn,
		"delivery", nativeMap("delivery_id", stage2Delivery))
	f.state = nativeMap("schema", NativeSessionSchema, "native_session_id", currentnessSession,
		"agent", nativeMap("agent_id", stage2Agent, "execution_id", stage2Execution, "native_session_id", currentnessSession),
		"inbox_attempt", attempt, "inbox_intent", nativeMap("request_id", stage2Attempt, "session", ref, "native_turn_id", f.turn),
		"inbox_close", nativeMap("request_id", stage2Delivery, "session", ref, "attempt_id", stage2Attempt, "reason", "delivery_completed"),
		"inbox_completion", stage2Turn, "inbox_response", stage2Execution, "inbox_journals", nativeList(value.Text(stage2Attempt)))
	f.remote = nativeClone(attempt)
	f.remote.Set("finished_at", value.Text("2026-11-12T22:00:00Z"))
	f.remote.Set("reason", value.Text("delivery_completed"))
	f.remote.Get("delivery").Set("state", value.Text("handled"))
	f.remote.Get("delivery").Set("result", result)
	f.remote.Set("turn_stop_state", value.Text("ended"))
	f.remote.Set("capture_state", value.Text("complete"))
	f.remote.Set("terminal_scan", value.Text("clear"))
	f.remote.Set("tools", nativeList())
	f.journal = nativeMap("schema", journal.Schema, "attempt_id", stage2Attempt, "delivery_id", stage2Delivery, "session", ref, "kind", "request",
		"completion", nativeMap("kind", "complete", "request_id", stage2Turn, "status", "committed", "result", result),
		"finish_reply", nativeMap("request_id", stage2Execution, "argv", nativeList(value.Text("fixture"))),
		"response", nativeMap("request_id", stage2Execution, "status", "committed", "expected_ref", result))
	f.statePath = SessionStatePath(stateDir, "currentness", currentnessSession)
	f.journalPath = filepath.Join(stateDir, "intents", stage2Attempt+".json")
	f.persist(t)
	if got, err := ledger.DecideActivation(context.Background(), ledger.Activation{Memory: memory, Harness: harness, Session: currentnessSession, Turn: f.turn, Enabled: true}); err != nil || !got {
		t.Fatal(got, err)
	}
	f.ledgerPath = filepath.Join(f.memory, currentnessSession+".inbox-recall.json")
	if harness == "codex" {
		f.ledgerPath = filepath.Join(f.memory, currentnessSession+".memory-budget.json")
		f.advanceCodex(t, currentnessTurn)
		f.advanceCodex(t, currentnessLater)
	}
	f.cli = &CLI{Profile: profile, ProcRoot: "/proc"}
	f.cli.Host = NewFileHost(FileHostOptions{Profile: profile, Call: func(_ context.Context, _ *value.Value, op string, _ *value.Value, _ NativeCallOptions) (*value.Value, error) {
		f.reads++
		switch op {
		case "session-inbox-attempt-status":
			return nativeClone(f.remote), nil
		case "session-inbox-control":
			return nativeMap("attempt", nil), nil
		case "wake-attempts":
			return nativeMap("attempts", nativeList(), "more", false), nil
		case "event-watch":
			return nativeMap("deliveries", nativeList(), "more", false, "cursor", ""), nil
		default:
			t.Fatalf("inspector attempted non-read operation %s", op)
			return nil, nil
		}
	}})
	return f
}

func mustCurrentnessJSON(t *testing.T, body *value.Value) []byte {
	t.Helper()
	raw, err := canonicalSortedJSONBytes(body)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func (f *grantCurrentnessFixture) persist(t *testing.T) {
	t.Helper()
	profile := stage2ServicesProfile()
	stage2WriteValue(t, profile, f.statePath, f.state)
	stage2WriteValue(t, profile, f.journalPath, f.journal)
}

func (f *grantCurrentnessFixture) advanceCodex(t *testing.T, turn string) {
	t.Helper()
	state, err := ledger.CodexBudget(f.ledgerPath, currentnessSession, 9500)
	if err != nil {
		t.Fatal(err)
	}
	if _, eligible, why := ledger.CodexPromptGrant(state, nativeMap("hook_event_name", "UserPromptSubmit", "turn_id", turn), 9500, true); !eligible {
		t.Fatal(why)
	}
	if err := ledger.SaveCodexBudget(f.ledgerPath, state); err != nil {
		t.Fatal(err)
	}
}

func TestR06GrantCurrentnessSettledOwnersRemainReadOnly(t *testing.T) {
	for _, harness := range []string{"codex", "claude"} {
		t.Run(harness, func(t *testing.T) {
			f := newGrantCurrentnessFixture(t, harness)
			before := censusTestSnapshot(t, f.root)
			receipt, err := f.cli.CaptureWatchDrain(context.Background(), f.selected, []*value.Value{})
			if err != nil || receipt == nil {
				t.Fatal("ended and settled owning turn refused", err)
			}
			if err := receipt.recheck(context.Background()); err != nil {
				t.Fatal("unchanged receipt did not survive final read", err)
			}
			if len(receipt.Facts()) < 10 || f.reads < 10 {
				t.Fatal("local/API facts not retained", receipt.Facts(), f.reads)
			}
			assertCurrentnessSnapshot(t, before, censusTestSnapshot(t, f.root))
		})
	}
}

func assertCurrentnessSnapshot(t *testing.T, before, after map[string]string) {
	t.Helper()
	if len(before) != len(after) {
		t.Fatal("inspection changed file membership")
	}
	for path, pin := range before {
		if after[path] != pin {
			t.Fatalf("inspection changed %s", path)
		}
	}
}

type currentnessOwnerSnapshot struct {
	files         map[string]string
	ledger        []byte
	state, remote *value.Value
}

func snapshotCurrentnessOwner(t *testing.T, f *grantCurrentnessFixture) currentnessOwnerSnapshot {
	t.Helper()
	raw, err := os.ReadFile(f.ledgerPath)
	if err != nil {
		t.Fatal(err)
	}
	return currentnessOwnerSnapshot{files: censusTestSnapshot(t, f.root), ledger: raw,
		state: nativeClone(f.state), remote: nativeClone(f.remote)}
}

func TestR06GrantCurrentnessCodexRetiredTurnWithoutStopPasses(t *testing.T) {
	f := newGrantCurrentnessFixture(t, "codex")
	nativePop(f.remote, "turn_stop_state")
	grants := mustCurrentnessRead(t, f.ledgerPath)
	grants.Set("active_turn_id", value.Text(currentnessLater))
	if grants.Get("turns").Get(currentnessTurn) == nil || grants.Get("turns").Get(currentnessLater) == nil {
		t.Fatal("fixture must retain both ordinary owner turn grants")
	}
	stage2WriteValue(t, stage2ServicesProfile(), f.ledgerPath, grants)
	before := snapshotCurrentnessOwner(t, f)
	t.Cleanup(func() {
		if !reflect.DeepEqual(before, snapshotCurrentnessOwner(t, f)) {
			t.Fatal("Codex no-stop positive changed filesystem, ledger, state or remote facts")
		}
	})
	// Only the existing retired-turn semantics can prove end here; all exact
	// delivery, close, completion and reply correspondence remains required.
	receipt, err := f.cli.CaptureWatchDrain(context.Background(), f.selected, []*value.Value{})
	if err != nil || receipt == nil {
		t.Fatal("retired Codex owner without a stop report refused", err)
	}
	if err := receipt.recheck(context.Background()); err != nil {
		t.Fatal("unchanged no-stop receipt did not survive final read", err)
	}
}

func TestR06GrantCurrentnessCodexMissingRetiredTurnWithoutStopRefuses(t *testing.T) {
	f := newGrantCurrentnessFixture(t, "codex")
	nativePop(f.remote, "turn_stop_state")
	grants := mustCurrentnessRead(t, f.ledgerPath)
	grants.Set("active_turn_id", value.Text(currentnessLater))
	if grants.Get("turns").Get(currentnessTurn) == nil || grants.Get("turns").Get(currentnessLater) == nil {
		t.Fatal("fixture must start with both ordinary owner turn grants")
	}
	// Keep the current grant, retained true activation and exact settled owner.
	// CodexBudget accepts this shape, so the membership guard must discriminate.
	nativePop(grants.Get("turns"), currentnessTurn)
	stage2WriteValue(t, stage2ServicesProfile(), f.ledgerPath, grants)
	before := snapshotCurrentnessOwner(t, f)
	t.Cleanup(func() {
		if !reflect.DeepEqual(before, snapshotCurrentnessOwner(t, f)) {
			t.Fatal("Codex no-stop negative changed filesystem, ledger, state or remote facts")
		}
	})
	receipt, err := f.cli.CaptureWatchDrain(context.Background(), f.selected, []*value.Value{})
	const want = "D1 retained positive or unknown activation grant needs owning-session disposition: Codex retired-turn membership is unknown"
	if receipt != nil || err == nil || err.Error() != want {
		t.Fatalf("missing retired member accepted or wrong refusal: receipt=%v err=%v; want %q", receipt != nil, err, want)
	}
}

func TestR06GrantCurrentnessRefusesUnprovenOwners(t *testing.T) {
	for _, harness := range []string{"codex", "claude"} {
		for _, problem := range []string{"current", "nonterminal", "mismatched-close", "unknown-end", "wrong-turn", "foreign-session", "unknown-positive", "unsettled-reply", "second-unmapped-positive"} {
			t.Run(harness+"/"+problem, func(t *testing.T) {
				f := newGrantCurrentnessFixture(t, harness)
				want := retainedGrantRefusal
				switch problem {
				case "current":
					if harness == "codex" {
						v := mustCurrentnessRead(t, f.ledgerPath)
						v.Set("active_turn_id", value.Text(f.turn))
						stage2WriteValue(t, stage2ServicesProfile(), f.ledgerPath, v)
					} else {
						f.state.Set("active_prompt", nativeMap("id", f.turn))
						want = "D2 active_prompt"
					}
				case "nonterminal":
					f.remote.Get("delivery").Set("state", value.Text("leased"))
					want = "D2 authoritative delivery is not terminal"
				case "mismatched-close":
					f.state.Get("inbox_close").Set("reason", value.Text("other-close"))
					want = "D2 retained close does not match"
				case "unknown-end":
					nativePop(f.remote, "turn_stop_state")
					if harness == "codex" {
						v := mustCurrentnessRead(t, f.ledgerPath)
						v.Set("turns", value.Mapping())
						v.Set("active_turn_id", &value.Value{Kind: value.Null})
						stage2WriteValue(t, stage2ServicesProfile(), f.ledgerPath, v)
					}
				case "wrong-turn":
					f.remote.Set("native_turn_id", value.Text("unrelated-turn"))
					want = "D2 historical native/batch ownership"
				case "foreign-session":
					f.remote.Get("session").Set("execution_id", value.Text(stage2Turn))
					want = "D2 historical attempt/session"
				case "unknown-positive", "second-unmapped-positive":
					v := mustCurrentnessRead(t, f.ledgerPath)
					decision := value.Boolean(true)
					if problem == "unknown-positive" {
						decision = value.Text("true")
					}
					v.Get("inbox_activation").Set(currentnessLater, decision)
					stage2WriteValue(t, stage2ServicesProfile(), f.ledgerPath, v)
				case "unsettled-reply":
					f.journal.Get("response").Set("status", value.Text("pending"))
					want = "D3"
				}
				f.persist(t)
				before := censusTestSnapshot(t, f.root)
				receipt, err := f.cli.CaptureWatchDrain(context.Background(), f.selected, []*value.Value{})
				if receipt != nil || err == nil || !strings.Contains(err.Error(), want) {
					t.Fatalf("%s accepted or wrong refusal: receipt=%v err=%v", problem, receipt != nil, err)
				}
				assertCurrentnessSnapshot(t, before, censusTestSnapshot(t, f.root))
			})
		}
	}
}

func mustCurrentnessRead(t *testing.T, path string) *value.Value {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	v, err := value.DecodeBytes(raw, 0)
	if err != nil {
		t.Fatal(err)
	}
	return v
}

func TestR06GrantCurrentnessEndChangeInvalidatesPublication(t *testing.T) {
	for _, harness := range []string{"codex", "claude"} {
		t.Run(harness, func(t *testing.T) {
			f := newGrantCurrentnessFixture(t, harness)
			receipt, err := f.cli.CaptureWatchDrain(context.Background(), f.selected, []*value.Value{})
			if err != nil {
				t.Fatal(err)
			}
			old := censusTestSnapshot(t, f.selected.Directory)
			// Both are terminal values; a Boolean-only end test would miss this
			// changed relied-on authoritative fact and incorrectly publish.
			f.remote.Set("turn_stop_state", value.Text("interrupted"))
			_, err = ExchangeWatchBindings(context.Background(), receipt, filepath.Join(f.root, "retained"), f.next)
			if err == nil || !strings.Contains(err.Error(), "D6 drain receipt invalidated") {
				t.Fatal("changed end evidence did not invalidate publication", err)
			}
			assertCurrentnessSnapshot(t, old, censusTestSnapshot(t, f.selected.Directory))
		})
	}
}

func TestR06GrantCurrentnessIndependentSourceOwnerLedgers(t *testing.T) {
	for _, harness := range []string{"codex", "claude"} {
		for _, ended := range []bool{true, false} {
			name := "ended"
			if !ended {
				name = "unknown-or-current"
			}
			t.Run(harness+"/"+name, func(t *testing.T) {
				f := newGrantCurrentnessFixture(t, harness)
				directory := filepath.Join(f.root, "independent-source")
				if err := os.Mkdir(directory, 0700); err != nil {
					t.Fatal(err)
				}
				_, here, _, ok := runtime.Caller(0)
				if !ok {
					t.Fatal("cannot locate unchanged Source")
				}
				source := filepath.Join(filepath.Dir(here), "..", "..", "..", "integrations", "lifecycle")
				code := `import importlib.util,pathlib,sys,time,types
source,directory,harness,session,turn,later,advance=sys.argv[1:]
sys.path.insert(0,source)
spec=importlib.util.spec_from_file_location('currentness_source_memory',str(pathlib.Path(source)/'memory.py'))
memory=importlib.util.module_from_spec(spec);spec.loader.exec_module(memory)
grants=memory._grant_ledger
config=dict(state_dir=directory,harness=harness,context_bytes=9500)
assert grants.activation_decision(config,harness,session,turn,True) is True
def ordinary(owner):
    event=dict(hook_event_name='UserPromptSubmit',session_id=session,turn_id=owner,prompt='ordinary owner prompt')
    memory.codex_agent_context(types.SimpleNamespace(config=config),event,dict(selected=[],index=[]),9500,dict(rejected={}),time.monotonic()+10,{})
if harness=='codex': ordinary(turn)
assert grants.activation_decision(config,harness,session,later,False) is False
if harness=='codex' and advance=='yes': ordinary(later)
assert grants.activation_decision(config,harness,session,turn,False) is True
`
				later := currentnessLater
				if harness == "claude" {
					later = "claude-channel:later-prompt"
				}
				advance := "no"
				if ended {
					advance = "yes"
				}
				ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
				defer cancel()
				command := exec.CommandContext(ctx, "python3", "-I", "-S", "-B", "-c", code, source, directory, harness, currentnessSession, f.turn, later, advance)
				if output, err := command.CombinedOutput(); err != nil {
					t.Fatalf("actual unmodified Source owner operations: %v: %s", err, output)
				}
				for _, file := range []string{"grant_ledger.py", "memory.py"} {
					raw, err := os.ReadFile(filepath.Join(source, file))
					if err != nil {
						t.Fatal(err)
					}
					sum := sha256.Sum256(raw)
					t.Logf("unchanged Python owner %s sha256=%x", file, sum)
				}
				entries, err := os.ReadDir(directory)
				if err != nil {
					t.Fatal(err)
				}
				for _, entry := range entries {
					if strings.HasSuffix(entry.Name(), ".lock") {
						continue
					}
					raw, err := os.ReadFile(filepath.Join(directory, entry.Name()))
					if err != nil {
						t.Fatal(err)
					}
					writePrivateFile(t, filepath.Join(f.memory, entry.Name()), raw, 0600)
				}
				produced := mustCurrentnessRead(t, f.ledgerPath)
				laterDecision := produced.Get("inbox_activation").Get(later)
				if !exactTrue(produced.Get("inbox_activation").Get(f.turn)) || laterDecision == nil || laterDecision.Kind != value.Bool || laterDecision.Boolean {
					t.Fatal("independent owner did not retain both exact decisions", produced)
				}
				if !ended && harness == "claude" {
					nativePop(f.remote, "turn_stop_state")
				}
				before := censusTestSnapshot(t, f.root)
				receipt, err := f.cli.CaptureWatchDrain(context.Background(), f.selected, []*value.Value{})
				if ended && (err != nil || receipt == nil) {
					t.Fatal("independent Source owner ledger refused", err)
				}
				if !ended && (err == nil || receipt != nil || !strings.Contains(err.Error(), retainedGrantRefusal)) {
					t.Fatal("newer false alone cleared an unended Source owner", err)
				}
				assertCurrentnessSnapshot(t, before, censusTestSnapshot(t, f.root))
			})
		}
	}
}
