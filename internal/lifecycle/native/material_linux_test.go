package native

import (
	"strings"
	"testing"
)

func TestNativeSettingsMutationKeepsNonselectedHooksGroupMetadataAndLargeIntegers(t *testing.T) {
	before := `{"opaque":900719925474099312345,"hooks":{"SessionStart":[{"matcher":"original","opaque_group":7,"hooks":[{"type":"command","command":"old-memory","timeout":13},{"type":"command","command":"sentinel","timeout":5}]}]}}`
	prepared := strings.Replace(before, `"old-memory"`, `"/checked-memory"`, 1)
	row := hookRow{Role: "memory", Event: "SessionStart"}
	row.Hook.Type, row.Hook.Command, row.Hook.Timeout = "command", "/checked-memory", 13
	replacements := map[string][]string{"memory": {"old-memory"}, "coordination": {}}
	if err := validateSettingsMutation([]byte(before), []byte(prepared), []hookRow{row}, replacements); err != nil {
		t.Fatal(err)
	}
	for name, mutant := range map[string]string{
		"large-integer":        strings.Replace(prepared, "900719925474099312345", "900719925474099312346", 1),
		"unrelated-hook":       strings.Replace(prepared, "sentinel", "changed", 1),
		"group-metadata":       strings.Replace(prepared, "original", "changed", 1),
		"missing-selected-row": before,
	} {
		t.Run(name, func(t *testing.T) {
			if err := validateSettingsMutation([]byte(before), []byte(mutant), []hookRow{row}, replacements); err == nil {
				t.Fatal("nonselected mutation or omitted Go row accepted")
			}
		})
	}
}

func TestNativeSettingsMutationRefusesAmbiguousOriginalAndNonobject(t *testing.T) {
	row := hookRow{Role: "memory", Event: "SessionStart"}
	row.Hook.Type, row.Hook.Command, row.Hook.Timeout = "command", "/checked-memory", 13
	before := `{"hooks":{"SessionStart":[{"matcher":"","hooks":[{"type":"command","command":"old","timeout":13},{"type":"command","command":"old","timeout":13}]}]}}`
	prepared := strings.ReplaceAll(before, `"old"`, `"/checked-memory"`)
	if err := validateSettingsMutation([]byte(before), []byte(prepared), []hookRow{row}, map[string][]string{"memory": {"old"}, "coordination": {}}); err == nil {
		t.Fatal("ambiguous original command accepted")
	}
	for _, raw := range []string{"null", "[]", `{"hooks":null}`} {
		if err := validateSettingsMutation([]byte(raw), []byte(`{}`), []hookRow{row}, map[string][]string{"memory": {}, "coordination": {}}); err == nil {
			t.Fatalf("nonobject settings/hooks accepted: %s", raw)
		}
	}
}
