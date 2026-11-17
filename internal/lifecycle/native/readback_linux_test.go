package native

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestNativeReadbackRefusesCachedMissingOrForeignRows(t *testing.T) {
	raw := `{"hooks":{"SessionStart":[{"matcher":"","hooks":[{"type":"command","command":"checked","timeout":13}]}]}}`
	settings := map[string]json.RawMessage{"effective": json.RawMessage(raw)}
	listing := map[string]json.RawMessage{"hooks": json.RawMessage(`[{"event":"SessionStart","commandText":"checked","source":"flagSettings"}]`)}
	if err := verifyNativeReadback([]byte(raw), settings, listing); err != nil {
		t.Fatal(err)
	}
	for _, bad := range []string{`[]`, `[{"event":"SessionStart","commandText":"old-cached","source":"flagSettings"}]`, `[{"event":"SessionStart","commandText":"checked","source":"userSettings"}]`} {
		if err := verifyNativeReadback([]byte(raw), settings, map[string]json.RawMessage{"hooks": json.RawMessage(bad)}); err == nil {
			t.Fatal("unqualified listing accepted", bad)
		}
	}
	settings["effective"] = json.RawMessage(strings.Replace(raw, "checked", "cached", 1))
	if err := verifyNativeReadback([]byte(raw), settings, listing); err == nil {
		t.Fatal("cached settings accepted")
	}
	if _, err := ownedCommandRows([]byte(strings.Replace(raw, `"type":"command"`, `"type":"prompt"`, 1))); err == nil {
		t.Fatal("model prompt hook qualified as control-only")
	}
}

func TestNativeHookFailureIsNotSuccessfulInitializationOrJoin(t *testing.T) {
	for _, raw := range []string{`{"subtype":"hook_response","exit_code":2,"outcome":"error"}`, `{"subtype":"hook_response","exit_code":0,"outcome":"error"}`, `{"subtype":"hook_response","outcome":"success"}`} {
		var event map[string]json.RawMessage
		if err := json.Unmarshal([]byte(raw), &event); err != nil {
			t.Fatal(err)
		}
		if err := checkNativeSystem(event); err == nil {
			t.Fatal("failed/incomplete actual hook response ignored", raw)
		}
	}
}
