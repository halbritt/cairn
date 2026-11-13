package coordination

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func TestWatchStateNativeSessionProduced(t *testing.T) {
	f := newCensusFixture(t)
	for _, pin := range f.catalog.Sessions {
		raw, err := os.ReadFile(pin.Path)
		if err != nil {
			t.Fatal(err)
		}
		state, err := value.DecodeTextWithObjectPolicy(raw, "utf-8", 0, value.DuplicateReject)
		if err != nil {
			t.Fatal(err)
		}
		if state.Get("native_session_id") != nil {
			t.Fatal("actual hook producer fixture acquired a synthetic outer identity")
		}
		want := nativeText(state.Get("registration").Get("native_session_id"))
		if want == "" || !value.SameText(state.Get("agent").Get("native_session_id"), want) {
			t.Fatal("producer registration and returned agent do not identify the same fixture session")
		}
		before := nativeClone(state)
		got, err := watchStateNativeSession(state)
		if err != nil || got != want {
			t.Fatalf("producer identity refused: got %q, want %q: %v", got, want, err)
		}
		if !reflect.DeepEqual(before, nativeClone(state)) {
			t.Fatal("identity inspection changed producer state")
		}
		after, err := os.ReadFile(pin.Path)
		if err != nil || string(after) != string(raw) {
			t.Fatal("identity inspection changed the saved producer bytes", err)
		}
	}
}

func TestWatchStateNativeSessionReaderCorrespondence(t *testing.T) {
	f := newCensusFixture(t)
	selected := f.proposal.Bindings[0]
	path := f.proposal.Sessions[0]
	state := mustCurrentnessRead(t, path)
	r := &watchDrainRead{}
	for name := range selected.Pins {
		r.configs = append(r.configs, mustCurrentnessRead(t, filepath.Join(selected.Directory, name)))
	}
	before := censusTestSnapshot(t, f.root)
	config := r.sessionConfig(path, state)
	if config == nil {
		t.Fatal("reader could not map the actual producer state")
	}
	native, err := watchStateNativeSession(state)
	if err != nil || SessionStatePath(nativeText(config.Get("state_dir")), nativeText(config.Get("binding")), native) != path {
		t.Fatal("reader lost exact config/binding/path correspondence", err)
	}
	for _, problem := range []string{"path", "binding", "directory", "conflict", "missing"} {
		t.Run(problem, func(t *testing.T) {
			candidate := nativeClone(state)
			cfg := nativeClone(config)
			candidatePath := path
			switch problem {
			case "path":
				candidatePath += ".other"
			case "binding":
				cfg.Set("binding", value.Text("different-binding"))
			case "directory":
				cfg.Set("state_dir", value.Text(filepath.Join(f.root, "different-state")))
			case "conflict":
				candidate.Get("registration").Set("native_session_id", value.Text("native-old"))
			case "missing":
				nativePop(candidate.Get("registration"), "native_session_id")
				nativePop(candidate.Get("agent"), "native_session_id")
			}
			read := &watchDrainRead{configs: []*value.Value{cfg}}
			if read.sessionConfig(candidatePath, candidate) != nil {
				t.Fatal("reader mapped unknown identity or mismatched config/path")
			}
		})
	}
	if !reflect.DeepEqual(before, censusTestSnapshot(t, f.root)) {
		t.Fatal("mapping inspection changed fixture bytes")
	}
}

// This exercises only the projection of an already settled internal result.
// The marker below supplies no authentication; real API/PG coverage is separate.
func TestWatchStateNativeSessionD2Projection(t *testing.T) {
	f := newCensusFixture(t)
	path := f.proposal.Sessions[0]
	pin := censusD2RetainPolicy(t, f, path)
	state := mustCurrentnessRead(t, path)
	selected := f.proposal.Bindings[0]
	r := &watchDrainRead{facts: map[string]string{"file:" + path: pin.SHA256}, settled: map[*value.Value]*value.Value{}}
	for name := range selected.Pins {
		r.configs = append(r.configs, mustCurrentnessRead(t, filepath.Join(selected.Directory, name)))
	}
	config := r.sessionConfig(path, state)
	if config == nil {
		t.Fatal("producer identity did not map to its selected config")
	}
	ref := nativeMap("agent_id", state.Get("agent").Get("agent_id"), "execution_id", state.Get("agent").Get("execution_id"))
	r.sessions = []watchDrainSession{{config: config, ref: ref, state: state}}
	before := censusTestSnapshot(t, f.root)
	if got := r.d2SessionResults(); len(got) != 0 {
		t.Fatal("an unverified session produced D2 correspondence", got)
	}
	r.settled[state] = value.Mapping()
	got := r.d2SessionResults()
	result, ok := got[path]
	if !ok || len(got) != 1 || result.Path != path || result.NativeSession != nativeText(state.Get("agent").Get("native_session_id")) ||
		result.AgentID != nativeText(ref.Get("agent_id")) || result.ExecutionID != nativeText(ref.Get("execution_id")) ||
		result.SHA256 != pin.SHA256 || !reflect.DeepEqual(result.Fields, []string{"inbox_result_policy"}) {
		t.Fatalf("producer identity lost pinned D2 correspondence: %+v", got)
	}
	state.Get("registration").Set("native_session_id", value.Text("native-old"))
	if got := r.d2SessionResults(); len(got) != 0 {
		t.Fatal("a conflicting identity produced D2 correspondence", got)
	}
	if !reflect.DeepEqual(before, censusTestSnapshot(t, f.root)) {
		t.Fatal("D2 projection changed saved producer state")
	}
}

// Read-only API doubles exercise local refusal and receipt invalidation only.
// They do not qualify an authenticated API/PG or native-client success.
func watchNativeIdentityReadCLI(t *testing.T) *CLI {
	t.Helper()
	profile := stage2ServicesProfile()
	cli := &CLI{Profile: profile, Host: NewFileHost(FileHostOptions{Profile: profile})}
	cli.Host.Call = func(_ context.Context, _ *value.Value, op string, _ *value.Value, _ NativeCallOptions) (*value.Value, error) {
		switch op {
		case "wake-attempts":
			return nativeMap("attempts", nativeList(), "more", false), nil
		case "event-watch":
			return nativeMap("deliveries", nativeList(), "more", false, "cursor", ""), nil
		case "session-inbox-control":
			return nativeMap("attempt", nil), nil
		default:
			t.Fatalf("identity unit test attempted unexpected API operation: %s", op)
			return nil, nil
		}
	}
	return cli
}

func TestWatchStateNativeSessionMissingJournalRefuses(t *testing.T) {
	f := censusFixtureAt(t, privateCurrentnessDirectory(t))
	path := f.proposal.Sessions[0]
	state := mustCurrentnessRead(t, path)
	selected := f.proposal.Bindings[0]
	cli := watchNativeIdentityReadCLI(t)
	r := &watchDrainRead{cli: cli, facts: map[string]string{}, settled: map[*value.Value]*value.Value{}}
	for name := range selected.Pins {
		r.configs = append(r.configs, mustCurrentnessRead(t, filepath.Join(selected.Directory, name)))
	}
	config := r.sessionConfig(path, state)
	if config == nil {
		t.Fatal("producer identity did not map to its selected config")
	}
	ref := nativeMap("agent_id", state.Get("agent").Get("agent_id"), "execution_id", state.Get("agent").Get("execution_id"))
	attempt := nativeMap("attempt_id", stage2Attempt, "session", ref, "delivery", nativeMap("delivery_id", stage2Delivery))
	state.Set("inbox_attempt", attempt)
	remote := nativeClone(attempt)
	remote.Set("finished_at", value.Text("2026-11-12T22:00:00Z"))
	remote.Set("reason", value.Text("delivery_completed"))
	remote.Get("delivery").Set("state", value.Text("handled"))
	reads := 0
	cli.Host.Call = func(_ context.Context, _ *value.Value, op string, body *value.Value, _ NativeCallOptions) (*value.Value, error) {
		if op != "session-inbox-attempt-status" || !nativeEqual(body.Get("session"), ref) || !value.SameText(body.Get("attempt_id"), stage2Attempt) {
			t.Fatalf("wrong historical correspondence read: %s %+v", op, body)
		}
		reads++
		return nativeClone(remote), nil
	}
	journal := filepath.Join(nativeText(config.Get("state_dir")), "intents", stage2Attempt+".json")
	if _, err := os.Stat(journal); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("missing-journal fixture unexpectedly has a journal", err)
	}
	before := censusTestSnapshot(t, f.root)
	session := watchDrainSession{config: config, ref: ref, state: state}
	r.sessions = []watchDrainSession{session}
	err := r.historicalAttempt(context.Background(), session)
	if !errors.Is(err, os.ErrNotExist) || reads != 1 || r.settled[state] != nil || len(r.d2SessionResults()) != 0 {
		t.Fatalf("producer identity substituted for missing journal evidence: reads=%d err=%v", reads, err)
	}
	if !reflect.DeepEqual(before, censusTestSnapshot(t, f.root)) {
		t.Fatal("missing-journal refusal changed producer fixture bytes")
	}
}

func TestWatchStateNativeSessionFinalChangeInvalidates(t *testing.T) {
	for _, change := range []string{"agreeing-legacy-added", "conflicting-agent", "identities-and-path-disagree"} {
		t.Run(change, func(t *testing.T) {
			f := censusFixtureAt(t, privateCurrentnessDirectory(t))
			selected := f.proposal.Bindings[0]
			writePrivateFile(t, filepath.Join(selected.Directory, ".watch.lock"), nil, 0600)
			cli := watchNativeIdentityReadCLI(t)
			before := censusTestSnapshot(t, f.root)
			receipt, err := cli.CaptureWatchDrain(context.Background(), selected, []*value.Value{})
			if err != nil || receipt == nil {
				t.Fatal("producer-shaped local no-obligation receipt refused", err)
			}
			if err := receipt.recheck(context.Background()); err != nil {
				t.Fatal("unchanged producer state invalidated local receipt", err)
			}
			if !reflect.DeepEqual(before, censusTestSnapshot(t, f.root)) {
				t.Fatal("initial/final reads changed producer bytes")
			}
			path := f.proposal.Sessions[0]
			state := mustCurrentnessRead(t, path)
			switch change {
			case "agreeing-legacy-added":
				state.Set("native_session_id", nativeClone(state.Get("agent").Get("native_session_id")))
			case "conflicting-agent":
				state.Get("agent").Set("native_session_id", value.Text("native-old"))
			case "identities-and-path-disagree":
				state.Get("agent").Set("native_session_id", value.Text("native-old"))
				state.Get("registration").Set("native_session_id", value.Text("native-old"))
			}
			stage2WriteValue(t, stage2ServicesProfile(), path, state)
			changed := censusTestSnapshot(t, f.root)
			if err := receipt.recheck(context.Background()); err == nil || !strings.Contains(err.Error(), "D6 drain receipt invalidated") {
				t.Fatal("changed producer identity bytes did not invalidate the receipt", err)
			}
			if !reflect.DeepEqual(changed, censusTestSnapshot(t, f.root)) {
				t.Fatal("refusing final read changed fixture bytes")
			}
		})
	}
}

func TestWatchStateNativeSessionForms(t *testing.T) {
	state := func(fields ...any) *value.Value {
		return nativeMap(append([]any{"schema", NativeSessionSchema}, fields...)...)
	}
	const native = "native-current"
	for _, tc := range []struct {
		name string
		body *value.Value
		want string
	}{
		{"legacy-outer", state("native_session_id", native, "agent", nativeMap("agent_id", stage2Agent)), native},
		{"nested-agent", state("agent", nativeMap("native_session_id", native)), native},
		{"registration", state("registration", nativeMap("native_session_id", native)), native},
		{"mixed-agree", state("native_session_id", native, "registration", nativeMap("native_session_id", native), "agent", nativeMap("native_session_id", native)), native},
		{"exact-spaces", state("native_session_id", " native-current ", "agent", nativeMap("native_session_id", " native-current ")), " native-current "},
		{"unicode", state("agent", nativeMap("native_session_id", "native-caf\u00e9")), "native-caf\u00e9"},
		{"maximum-bytes", state("native_session_id", strings.Repeat("x", 256)), strings.Repeat("x", 256)},
		{"missing", state(), ""},
		{"unknown-location", state("session", nativeMap("native_session_id", native)), ""},
		{"nil-state", nil, ""},
		{"non-object-state", value.Text(native), ""},
		{"unknown-schema", nativeMap("schema", "cairn.native-session/2", "native_session_id", native), ""},
		{"registration-agent-conflict", state("registration", nativeMap("native_session_id", "native-old"), "agent", nativeMap("native_session_id", native)), ""},
		{"outer-agent-conflict", state("native_session_id", "native-old", "agent", nativeMap("native_session_id", native)), ""},
		{"outer-registration-conflict", state("native_session_id", "native-old", "registration", nativeMap("native_session_id", native)), ""},
		{"no-trim-rescue", state("native_session_id", " "+native, "agent", nativeMap("native_session_id", native)), ""},
		{"no-unicode-normalization", state("native_session_id", "caf\u00e9", "agent", nativeMap("native_session_id", "cafe\u0301")), ""},
		{"malformed-registration", state("registration", value.Text(native), "native_session_id", native), ""},
		{"malformed-agent", state("agent", &value.Value{Kind: value.Null}, "native_session_id", native), ""},
		{"null-outer", state("native_session_id", &value.Value{Kind: value.Null}, "agent", nativeMap("native_session_id", native)), ""},
		{"non-string-registration", state("registration", nativeMap("native_session_id", 1), "agent", nativeMap("native_session_id", native)), ""},
		{"empty-agent", state("native_session_id", native, "agent", nativeMap("native_session_id", "")), ""},
		{"blank", state("native_session_id", " \t"), ""},
		{"nul", state("native_session_id", "native\x00current"), ""},
		{"oversized", state("native_session_id", strings.Repeat("x", 257)), ""},
		{"oversized-utf8", state("native_session_id", strings.Repeat("\u00e9", 129)), ""},
		{"surrogate", state("native_session_id", &value.Value{Kind: value.String, String: []rune{0xd800}}), ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			before := nativeClone(tc.body)
			got, err := watchStateNativeSession(tc.body)
			if tc.want == "" {
				if err == nil || got != "" {
					t.Fatalf("unknown/conflicting identity accepted: %q %v", got, err)
				}
			} else if err != nil || got != tc.want {
				t.Fatalf("got %q, want %q: %v", got, tc.want, err)
			}
			if !reflect.DeepEqual(before, nativeClone(tc.body)) {
				t.Fatal("identity inspection mutated its input")
			}
		})
	}
}
