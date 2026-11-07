package binding

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"reflect"
	"strconv"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func profileJSON(t *testing.T, raw string) *value.Value {
	t.Helper()
	v, err := value.DecodeBytes([]byte(raw), 4300) // explicit test-reference setting
	if err != nil {
		t.Fatal(err)
	}
	return v
}

func TestJSONProfileEqualityKeepsSourceExpressionContexts(t *testing.T) {
	for _, row := range []struct {
		left, right string
		identity    bool
		want        bool
	}{
		{`true`, `1`, false, true}, {`1`, `1.0`, false, true}, {`0`, `-0.0`, false, true},
		{`9007199254740993`, `9007199254740993.0`, false, false},
		{`9007199254740992`, `9007199254740992.0`, false, true},
		{`NaN`, `NaN`, false, false}, {`NaN`, `NaN`, true, true},
		{`[NaN]`, `[NaN]`, false, true},
		{`{"first":true,"last":NaN}`, `{"last":NaN,"first":1}`, true, true},
		{`{"\ud800":1}`, `{"�":1}`, true, false},
		{`{"case":1}`, `{"Case":1}`, true, false},
	} {
		left, right := profileJSON(t, row.left), profileJSON(t, row.right)
		equal, err := equalJSONProfiles(left, right, row.identity)
		if err != nil || equal != row.want {
			t.Fatalf("%s == %s (identity=%v): %v, %v; want %v", row.left, row.right, row.identity, equal, err, row.want)
		}
	}
	large := &value.Value{Kind: value.Int, Integer: "1" + string(makeZeros(400))}
	equal, err := equalJSONProfiles(large, &value.Value{Kind: value.Float, Number: math.Inf(1)}, false)
	if err != nil || equal {
		t.Fatalf("large integer became infinity: %v, %v", equal, err)
	}
}

func makeZeros(n int) []byte {
	result := make([]byte, n)
	for i := range result {
		result[i] = '0'
	}
	return result
}

func validProfiles(t *testing.T) configurationProfiles {
	t.Helper()
	memory := profileJSON(t, `{"harness":"codex","cairn":"/unused-cli","socket":"/unused.sock","token_file":"/never-open-token","repo":"fixture","inbox_recall_binding":"/binding","recall_mode":"agent_tools","context_bytes":9500,"state_dir":"/memory","opaque":NaN}`)
	current := profileJSON(t, `{"opaque":NaN,"state_dir":"/memory","context_bytes":9500,"recall_mode":"agent_tools","inbox_recall_binding":"/binding","repo":"fixture","token_file":"/never-open-token","socket":"/unused.sock","cairn":"/unused-cli","harness":"codex"}`)
	coordination := profileJSON(t, `{"harness":"codex","cairn":"/unused-cli","socket":"/unused.sock","token_file":"/never-open-token","repo":"fixture","inbox_recall_binding":"/binding","native_delivery":true,"state_dir":"/coordination"}`)
	return configurationProfiles{Current: current, Memory: memory, Coordinators: []*value.Value{coordination}, ManifestPath: []rune("/binding"),
		Resolve: func(path *value.Value) ([]rune, error) {
			if path == nil || path.Kind != value.String {
				return nil, &value.Error{Class: "TypeError", Message: "source path conversion"}
			}
			return append([]rune{}, path.String...), nil
		}}
}

func TestConfigurationCompatibilityRetainsTypesAndDictionaryMembership(t *testing.T) {
	in := validProfiles(t)
	if err := validateConfigurationProfiles(in); err != nil {
		t.Fatal(err)
	}
	if in.Memory.Get("opaque").Kind != value.Float || !math.IsNaN(in.Memory.Get("opaque").Number) {
		t.Fatal("profile comparison mutated opaque data")
	}
	for _, config := range []*value.Value{in.Current, in.Memory} {
		budget := config.Get("context_bytes")
		budget.Kind, budget.Boolean, budget.Integer = value.Bool, true, ""
	}
	resolved := 0
	in.Resolve = func(path *value.Value) ([]rune, error) { resolved++; return path.String, nil }
	if err := validateConfigurationProfiles(in); value.ErrorClass(err) != "ValueError" || resolved != 2 {
		t.Fatalf("strict budget guard or its late source order changed: %v; resolutions=%d", err, resolved)
	}
}

func TestConfigurationHomeGuardPrecedesRemainingProfileChecks(t *testing.T) {
	in := validProfiles(t)
	in.Current = nil
	in.Coordinators = append(in.Coordinators, profileJSON(t, `{"config_home":"/alias-b"}`))
	in.Coordinators[0].Members = append(in.Coordinators[0].Members, value.Member{Key: []rune("config_home"), Value: profileJSON(t, `"/alias-a"`)})
	in.Resolve = func(*value.Value) ([]rune, error) { return []rune("/same-home"), nil }
	err := validateConfigurationProfiles(in)
	if value.ErrorClass(err) != "ValueError" || err.Error() != "ValueError: shared memory binding requires distinct explicit configuration homes" {
		t.Fatalf("home order changed: %v", err)
	}
}

func TestConfigurationResolutionFailureRetainsIdentity(t *testing.T) {
	in := validProfiles(t)
	failure := errors.New("selected source resolve failure")
	calls := 0
	in.Resolve = func(*value.Value) ([]rune, error) { calls++; return nil, failure }
	err := validateConfigurationProfiles(in)
	if err != failure || calls != 1 {
		t.Fatalf("resolve failure changed: %v; calls=%d", err, calls)
	}
}

func TestConfigurationResolutionOrderRepeatsMemoryForEachCoordinator(t *testing.T) {
	in := validProfiles(t)
	in.Coordinators[0].Members = append(in.Coordinators[0].Members, value.Member{Key: []rune("config_home"), Value: profileJSON(t, `"/home/a"`)})
	in.Coordinators = append(in.Coordinators, profileJSON(t, `{"harness":"codex","cairn":"/unused-cli","socket":"/unused.sock","token_file":"/never-open-token","repo":"fixture","inbox_recall_binding":"/binding","native_delivery":true,"state_dir":"/coordination-two","config_home":"/home/b"}`))
	var paths []string
	in.Resolve = func(path *value.Value) ([]rune, error) {
		paths = append(paths, string(path.String)) // this unit's fixture paths are ASCII
		return path.String, nil
	}
	if err := validateConfigurationProfiles(in); err != nil {
		t.Fatal(err)
	}
	want := []string{"/home/a", "/home/b", "/memory", "/coordination", "/memory", "/coordination-two"}
	if !reflect.DeepEqual(paths, want) {
		t.Fatalf("source resolution order changed: %v", paths)
	}
}

// Compiled-test-only wire for the private configuration-predicate kernel.
// It is not a binding loader, runtime/profile selector or production command.
func TestConfigurationProfilesOracleHelper(t *testing.T) {
	if os.Getenv("CAIRN_F01_PROFILE_HELPER") != "1" {
		return
	}
	digits, present := os.LookupEnv("CAIRN_F01_PROFILE_DIGITS")
	if !present {
		t.Fatal("explicit test-wire reader setting required")
	}
	limit, err := strconv.Atoi(digits)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := io.ReadAll(os.Stdin)
	if err != nil {
		t.Fatal(err)
	}
	input, err := value.DecodeBytes(raw, limit)
	if err != nil {
		t.Fatal(err)
	}
	var resolutions [][]rune
	in := configurationProfiles{Current: input.Get("current"), Memory: input.Get("memory"),
		Coordinators: input.Get("coordinators").Items, ManifestPath: input.Get("manifest_path").String,
		Resolve: func(path *value.Value) ([]rune, error) {
			if path == nil || path.Kind != value.String {
				return nil, profileFailure("TypeError", "source path conversion")
			}
			resolutions = append(resolutions, append([]rune{}, path.String...))
			for _, item := range input.Get("resolutions").Items {
				if equalCodePoints(item.Get("path").String, path.String) {
					return item.Get("resolved").String, nil
				}
			}
			return nil, profileFailure("FixtureResolutionMismatch", "unrecorded fixture path")
		}}
	err = validateConfigurationProfiles(in)
	output := map[string]any{"error_class": value.ErrorClass(err), "resolutions": resolutions}
	if err != nil {
		var source *value.Error
		if errors.As(err, &source) {
			output["error_message"] = source.Message
		}
	} else {
		output["current_endpoint"] = profileEndpoint(in.Current.Get("opaque"))
		output["memory_endpoint"] = profileEndpoint(in.Memory.Get("opaque"))
	}
	if err := json.NewEncoder(os.Stdout).Encode(output); err != nil {
		t.Fatal(err)
	}
}

func profileEndpoint(v *value.Value) any {
	height := 0
	for v != nil && (v.Kind == value.Array || v.Kind == value.Object) {
		height++
		if v.Kind == value.Array {
			v = v.Items[0]
		} else {
			v = v.Get("x")
		}
	}
	if v == nil || v.Kind == value.Null {
		return []any{height, "null"}
	}
	switch v.Kind {
	case value.Bool:
		return []any{height, "bool", v.Boolean}
	case value.Int:
		return []any{height, "int", v.Integer}
	case value.Float:
		class := fmt.Sprintf("%016x", math.Float64bits(v.Number))
		if math.IsNaN(v.Number) {
			class = "NaN"
		}
		return []any{height, "float", class}
	case value.String:
		return []any{height, "str", append([]rune{}, v.String...)}
	}
	panic("invalid test endpoint")
}
