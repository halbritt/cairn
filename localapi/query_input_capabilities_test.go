package localapi

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
)

func TestQueryInputDeclarationClosedAndChoicesIndependent(t *testing.T) {
	good := `{"schema":"cairn.query-input-capabilities/1","indexed_semantic_query_v1":true}`
	for _, raw := range []string{"", "null", `{}`, `{"schema":"cairn.query-input-capabilities/1"}`, strings.Replace(good, "/1", "/2", 1), strings.Replace(good, "true", "null", 1), strings.Replace(good, "true", `"true"`, 1), good[:len(good)-1] + `,"extra":1}`, good[:len(good)-1] + `,"indexed_semantic_query_v1":true}`, good + `{}`, good + strings.Repeat(" ", 256)} {
		t.Run(raw, func(t *testing.T) {
			if _, ok := ParseQueryInputCapabilities([]byte(raw)); ok {
				t.Fatal("accepted unknown declaration")
			}
			choice := new(PreviewCapabilityChoice)
			choice.ObserveVersion(nil, json.RawMessage(raw))
			choice.ObserveVersion(nil, json.RawMessage(good))
			got, err := choice.ResolveInputs(context.Background(), nil)
			if err != nil || got.DistinctCompile || got.DistinctReplay {
				t.Fatalf("unknown choice upgraded: %+v %v", got, err)
			}
		})
	}
	for _, flag := range []bool{false, true} {
		raw, _ := json.Marshal(QueryInputCapabilities{QueryInputCapabilitiesSchema, flag})
		choice := new(PreviewCapabilityChoice)
		choice.ObserveVersion(nil, raw)
		choice.ObserveVersion(nil, nil)
		got, err := choice.ResolveInputs(context.Background(), nil)
		if err != nil || got.DistinctCompile != flag || !got.DistinctReplay {
			t.Fatalf("compile/replay choice: %+v %v", got, err)
		}
		if got.RequireDistinctInput(true) != nil {
			t.Fatal("known replay refused")
		}
		if (got.RequireDistinctInput(false) == nil) != flag {
			t.Fatal("compile ignores configured flag")
		}
	}
	for _, version := range []string{`{"query_input_capabilities":` + good + `,"query_input_capabilities":` + good + `}`, `{"Query_Input_Capabilities":` + good + `}`, `{"query_input_capabilities":null,"query_input_capabilities":` + good + `}`} {
		if QueryInputDeclaration(json.RawMessage(version)) != nil {
			t.Fatal("duplicate or ambiguous version member accepted")
		}
	}
	if _, ok := ParseQueryInputCapabilities(QueryInputDeclaration(json.RawMessage(`{"build":"diagnostic","query_input_capabilities":` + good + `}`))); !ok {
		t.Fatal("known declaration lost")
	}
	if ConfirmDistinctQuerySchema(core.DistinctQuerySchema) != nil {
		t.Fatal("new schema refused")
	}
	for _, s := range []string{"", "cairn.semantic/19", "cairn.semantic/21"} {
		if core.Code(ConfirmDistinctQuerySchema(s)) != "UNSUPPORTED_QUERY_INPUT" {
			t.Fatal("legacy/future acknowledgement accepted")
		}
	}
}
