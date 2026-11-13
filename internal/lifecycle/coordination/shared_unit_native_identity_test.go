package coordination

import (
	"os"
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
