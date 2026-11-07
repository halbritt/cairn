package admission

import (
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"math"
	"os"
	"reflect"
	"strconv"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func decoded(t *testing.T, raw string) *value.Value {
	t.Helper()
	v, err := value.DecodeBytes([]byte(raw), 4300) // explicit test-reference setting
	if err != nil {
		t.Fatal(err)
	}
	return v
}

type fixtureProbe struct {
	directories, files [][]rune
	calls              int
}

func (p *fixtureProbe) Resolve(path *value.Value) ([]rune, error) {
	p.calls++
	if path == nil || path.Kind != value.String {
		return nil, &value.Error{Class: "TypeError", Message: "expected a path"}
	}
	return append([]rune{}, path.String...), nil
}

func (p *fixtureProbe) IsDir(path []rune) (bool, error) {
	p.calls++
	for _, directory := range p.directories {
		if reflect.DeepEqual(path, directory) {
			return true, nil
		}
	}
	return false, nil
}

func (p *fixtureProbe) Exists(path []rune) (bool, error) {
	p.calls++
	for _, file := range p.files {
		if reflect.DeepEqual(path, file) {
			return true, nil
		}
	}
	return p.IsDir(path)
}

func TestGuardOrderingRetainsMalformedBodyAndDisabledDistinctions(t *testing.T) {
	config := decoded(t, `{"harness":"codex"}`)
	for _, row := range []struct {
		name, kind, config, event, explicitName, errorClass, reason string
		environment                                                 map[string][]rune
	}{
		{"body before disabled", "memory", `{"harness":"codex"}`, `[1]`, `null`, "AttributeError", "", map[string][]rune{"CAIRN_LIFECYCLE_DISABLED": []rune("1")}},
		{"explicit name before disabled", "memory", `{"harness":"codex"}`, `[1]`, `"SessionStart"`, "", "disabled_env", map[string][]rune{"CAIRN_LIFECYCLE_DISABLED": []rune("1")}},
		{"coordination disabled before config", "coordination", `null`, `{}`, `null`, "", "coordination_disabled", map[string][]rune{"CAIRN_COORDINATION_DISABLED": []rune("1")}},
	} {
		t.Run(row.name, func(t *testing.T) {
			result, err := Decide(Invocation{Kind: row.kind, Config: decoded(t, row.config), Event: decoded(t, row.event), EventName: decoded(t, row.explicitName), Environment: row.environment})
			if value.ErrorClass(err) != row.errorClass {
				t.Fatalf("error = %v, want %s", err, row.errorClass)
			}
			if err == nil && result.Reason != row.reason {
				t.Fatalf("reason = %q, want %q", result.Reason, row.reason)
			}
		})
	}
	called := false
	result, err := Decide(Invocation{Kind: "coordination", Config: config,
		Event:        decoded(t, `{"hook_event_name":"PostToolUse"}`),
		Environment:  map[string][]rune{"CAIRN_WAKE_CONTEXT": []rune("selected")},
		WakeAccepted: func() (bool, error) { called = true; return true, nil }})
	if err != nil || result.Reason != "wake_context" || called {
		t.Fatalf("cue guard changed: %+v, %v, callback=%v", result, err, called)
	}
}

func TestHomeLookupIsExplicitAndEagerOnlyAtSourceAccountGuard(t *testing.T) {
	probe := &fixtureProbe{}
	homes := 0
	result, err := Decide(Invocation{Kind: "memory", Config: decoded(t, `{"harness":"codex","config_home":"/other"}`),
		Event: decoded(t, `{}`), Environment: map[string][]rune{"CODEX_HOME": []rune("/account"), "CAIRN_LIFECYCLE_DISABLED": []rune("1")},
		Home: func() ([]rune, error) { homes++; return []rune("/owner"), nil }, Filesystem: probe})
	if err != nil || result.Reason != "other_account_home" || homes != 1 || probe.calls != 2 {
		t.Fatalf("account guard order: %+v, %v, home=%d, probes=%d", result, err, homes, probe.calls)
	}
}

func TestCoordinationObservationKeepsUnicodeAndOwnerErrorOrder(t *testing.T) {
	probe := &fixtureProbe{directories: [][]rune{[]rune("/work/repo")}, files: [][]rune{[]rune("/work/.git")}}
	in := Invocation{Kind: "coordination", Config: decoded(t, `{"harness":"claude"}`), Environment: map[string][]rune{}, Filesystem: probe,
		Event: decoded(t, `{"hook_event_name":"UserPromptSubmit","session_id":"native-thread","cwd":"/work/repo","model":"\ud800","prompt_id":"p1"}`)}
	result, err := Decide(in)
	if err != nil || !result.Run() || string(result.NativeTurnID.String) != "claude-channel:p1" || string(result.Project.String) != "work" || result.ObservedModel.String[0] != 0xd800 {
		t.Fatalf("observation changed: %+v, %v", result, err)
	}
	in.Event = decoded(t, `{"hook_event_name":"SessionStart","session_id":"native","cwd":"/work/repo","prompt_id":"\u0000\ud800"}`)
	_, err = Decide(in)
	if value.ErrorClass(err) != "UnicodeEncodeError" {
		t.Fatalf("owner encoding must precede control check: %v", err)
	}
}

func TestPrivacyWalkReachesSingleSlashRoot(t *testing.T) {
	probe := &fixtureProbe{directories: [][]rune{[]rune("/work/repo")}, files: [][]rune{[]rune("/.cairn-no-memory")}}
	result, err := Decide(Invocation{Kind: "memory", Config: decoded(t, `{"harness":"codex"}`),
		Event:       decoded(t, `{"hook_event_name":"SessionStart","session_id":"11111111-1111-4111-8111-111111111111","cwd":"/work/repo"}`),
		Environment: map[string][]rune{}, Filesystem: probe})
	if err != nil || result.Reason != "privacy_marker" || len(result.Exclusions) != 1 || !reflect.DeepEqual(result.Exclusions[0], []rune("/.cairn-no-memory")) {
		t.Fatalf("root marker lost: %+v, %v", result, err)
	}
}

type failingProbe struct {
	fixtureProbe
	failure error
}

func (p *failingProbe) IsDir([]rune) (bool, error) {
	p.calls++
	return false, p.failure
}

func TestCallbackFailureRetainsIdentityAndStopsBeforeLaterProbes(t *testing.T) {
	failure := errors.New("selected source callback error")
	probe := &failingProbe{failure: failure}
	result, err := Decide(Invocation{Kind: "memory", Config: decoded(t, `{"harness":"codex"}`),
		Event:       decoded(t, `{"hook_event_name":"SessionStart","session_id":"11111111-1111-4111-8111-111111111111","cwd":"/work/repo"}`),
		Environment: map[string][]rune{}, Filesystem: probe})
	if result != nil || err != failure || probe.calls != 1 {
		t.Fatalf("callback failure changed: %+v, %v, calls=%d", result, err, probe.calls)
	}
}

// This helper exists only in a compiled test binary. Its callback tape is a
// recorded source observation, not a filesystem adapter, profile selector,
// production command, compatibility frame, or activation interface.
func TestAdmissionOracleHelper(t *testing.T) {
	if os.Getenv("CAIRN_F01_ADMISSION_HELPER") != "1" {
		return
	}
	raw, err := io.ReadAll(os.Stdin)
	if err != nil {
		t.Fatal(err)
	}
	digits, present := os.LookupEnv("CAIRN_F01_ADMISSION_DIGITS")
	if !present {
		t.Fatal("explicit test-wire reader setting required")
	}
	limit, err := strconv.Atoi(digits)
	if err != nil {
		t.Fatal(err)
	}
	input, err := value.DecodeBytes(raw, limit)
	if err != nil {
		t.Fatal(err)
	}
	tape := &sourceTape{rows: input.Get("tape").Items}
	environment := map[string][]rune{}
	for _, item := range input.Get("environment").Members {
		if item.Value.Kind != value.String {
			t.Fatal("test environment must contain captured strings")
		}
		environment[string(item.Key)] = item.Value.String // fixture keys are ASCII
	}
	in := Invocation{Kind: string(input.Get("kind").String), Config: input.Get("config"), Event: input.Get("event"),
		EventName: input.Get("event_name"), Environment: environment, Filesystem: tape,
		Home: func() ([]rune, error) {
			v, err := tape.next("home", nil)
			if err != nil {
				return nil, err
			}
			return v.String, nil
		}}
	wake := input.Get("wake")
	if wake != nil && wake.Kind == value.Object {
		if stringIs(wake.Get("mode"), "callback") {
			in.WakeAccepted = func() (bool, error) {
				v, err := tape.next("wake", nil)
				if err != nil {
					return false, err
				}
				return v.Boolean, nil
			}
		} else if truth(wake.Get("value")) {
			in.WakeAccepted = func() (bool, error) { return true, nil }
		}
	}
	result, err := Decide(in)
	output := map[string]any{"error_class": value.ErrorClass(err), "calls_consumed": tape.index, "calls_total": len(tape.rows)}
	if err == nil {
		found := &value.Value{Kind: value.Array}
		for _, path := range result.Exclusions {
			found.Items = append(found.Items, pathText(path))
		}
		decision := object(member("kind", text(result.Kind)), member("verdict", text(result.Verdict)),
			member("reason", optionalText(result.Reason)), member("message", optionalText(result.Message)),
			member("event", result.Event), member("phase", optionalText(result.Phase)), member("native_id", result.NativeID),
			member("workspace", result.Workspace), member("project", result.Project), member("observed_model", result.ObservedModel),
			member("native_turn_id", result.NativeTurnID), member("home", nil), member("exclusions", found),
			member("owner_observation", result.OwnerObservation))
		output["decision"], output["observation"] = tagged(decision), tagged(result.Observation())
	}
	if err := json.NewEncoder(os.Stdout).Encode(output); err != nil {
		t.Fatal(err)
	}
}

type sourceTape struct {
	rows  []*value.Value
	index int
}

func (p *sourceTape) next(operation string, argument *value.Value) (*value.Value, error) {
	if p.index >= len(p.rows) {
		return nil, sourceError("ProbeTapeMismatch", "unexpected "+operation)
	}
	row := p.rows[p.index]
	p.index++
	if !stringIs(row.Get("operation"), operation) || !reflect.DeepEqual(tagged(row.Get("argument")), tagged(argument)) {
		return nil, sourceError("ProbeTapeMismatch", "operation or source argument differs at "+operation)
	}
	if class := row.Get("error_class"); class != nil && truth(class) {
		return nil, sourceError(string(class.String), "recorded source callback error")
	}
	return row.Get("result"), nil
}

func (p *sourceTape) Resolve(path *value.Value) ([]rune, error) {
	v, err := p.next("resolve", path)
	if err != nil {
		return nil, err
	}
	return v.String, nil
}
func (p *sourceTape) IsDir(path []rune) (bool, error) {
	v, err := p.next("is_dir", pathText(path))
	if err != nil {
		return false, err
	}
	return v.Boolean, nil
}
func (p *sourceTape) Exists(path []rune) (bool, error) {
	v, err := p.next("exists", pathText(path))
	if err != nil {
		return false, err
	}
	return v.Boolean, nil
}

func tagged(v *value.Value) any {
	if isNone(v) {
		return []any{"null"}
	}
	switch v.Kind {
	case value.Bool:
		return []any{"bool", v.Boolean}
	case value.Int:
		return []any{"int", v.Integer}
	case value.Float:
		class := ""
		switch {
		case math.IsNaN(v.Number):
			class = "nan"
		case math.IsInf(v.Number, 1):
			class = "+inf"
		case math.IsInf(v.Number, -1):
			class = "-inf"
		default:
			raw := make([]byte, 8)
			binary.BigEndian.PutUint64(raw, math.Float64bits(v.Number))
			class = hex.EncodeToString(raw)
		}
		return []any{"float", class}
	case value.String:
		return []any{"string", append([]rune{}, v.String...)}
	case value.Array:
		items := make([]any, 0, len(v.Items))
		for _, item := range v.Items {
			items = append(items, tagged(item))
		}
		return []any{"array", items}
	case value.Object:
		items := make([]any, 0, len(v.Members))
		for _, item := range v.Members {
			items = append(items, []any{append([]rune{}, item.Key...), tagged(item.Value)})
		}
		return []any{"object", items}
	}
	panic("invalid test Value kind")
}
