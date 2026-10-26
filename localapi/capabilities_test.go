package localapi

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
)

func TestCapabilityTableDrivenCodecClosedAndBounded(t *testing.T) {
	cases := []struct {
		name      string
		kind      capabilityKind
		valid     []string
		invalid   []string
		wantFlags map[string]bool
	}{
		{
			name:  "retrieval",
			kind:  capabilityRetrieval,
			valid: []string{`{"schema":"cairn.retrieval-capabilities/1","search_memory_budget_bytes":true,"search_min_pull_bytes":true}`},
			invalid: []string{
				`{"schema":"cairn.retrieval-capabilities/1","search_memory_budget_bytes":false,"search_min_pull_bytes":true}`,
				`{"schema":"cairn.retrieval-capabilities/1","search_memory_budget_bytes":true}`,
			},
			wantFlags: map[string]bool{"search_memory_budget_bytes": true, "search_min_pull_bytes": true},
		},
		{
			name:      "preview",
			kind:      capabilityPreview,
			valid:     []string{`{"schema":"cairn.preview-capabilities/1","entities_omitted":true}`},
			invalid:   []string{`{"schema":"cairn.preview-capabilities/1"}`},
			wantFlags: map[string]bool{"entities_omitted": true},
		},
		{
			name:  "query-input",
			kind:  capabilityQueryInput,
			valid: []string{`{"schema":"cairn.query-input-capabilities/1","indexed_semantic_query_v1":true}`},
			invalid: []string{
				`{"schema":"cairn.query-input-capabilities/1"}`,
			},
			wantFlags: map[string]bool{"indexed_semantic_query_v1": true},
		},
		{
			name:      "inspection",
			kind:      capabilityInspection,
			valid:     []string{`{"schema":"cairn.inspection-capabilities/1","first_fitting_whole_v1":true}`},
			invalid:   []string{`{"schema":"cairn.inspection-capabilities/1"}`},
			wantFlags: map[string]bool{"first_fitting_whole_v1": true},
		},
		{
			name:      "source-opening",
			kind:      capabilitySourceOpening,
			valid:     []string{`{"schema":"cairn.source-opening-capabilities/1","reader_guard":true,"receipt_binding":false}`},
			invalid:   []string{`{"schema":"cairn.source-opening-capabilities/1","reader_guard":true}`},
			wantFlags: map[string]bool{"reader_guard": true, "receipt_binding": false},
		},
		{
			name:      "reader",
			kind:      capabilityReader,
			valid:     []string{`{"schema":"cairn.reader-capabilities/1","source_opening":true}`},
			invalid:   []string{`{"schema":"cairn.reader-capabilities/1"}`},
			wantFlags: map[string]bool{"source_opening": true},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			for _, raw := range tc.valid {
				flags, ok := parseCapabilityFlags(tc.kind, []byte(raw))
				if !ok {
					t.Fatalf("rejected valid declaration %s", raw)
				}
				for k, want := range tc.wantFlags {
					if flags[k] != want {
						t.Fatalf("flag %s = %v, want %v", k, flags[k], want)
					}
				}
			}
			base := tc.valid[0]
			flagName := capabilityTable[tc.kind].flags[0]
			malformed := append([]string{
				"",
				"null",
				"{}",
				strings.Replace(base, "/1", "/2", 1),
				strings.Replace(base, `"schema"`, `"Schema"`, 1),
				strings.Replace(base, `true`, `"true"`, 1),
				strings.Replace(base, `true`, `1`, 1),
				strings.Replace(base, `true`, `null`, 1),
				base[:len(base)-1] + `,"extra":true}`,
				base[:len(base)-1] + `,"` + flagName + `":true}`,
				base + `{}`,
				base + strings.Repeat(" ", 256),
			}, tc.invalid...)
			for _, raw := range malformed {
				if _, ok := parseCapabilityFlags(tc.kind, []byte(raw)); ok {
					t.Fatalf("accepted invalid declaration %q", raw)
				}
			}
		})
	}
}

func TestCapabilityFieldExtractionAndDistinctSchemaConfirm(t *testing.T) {
	for kind, spec := range capabilityTable {
		member := `{"schema":"` + spec.schema + `"}`
		envelope := json.RawMessage(`{"build":"ok","` + spec.field + `":` + member + `}`)
		if got := extractCapabilityField(envelope, kind); string(got) != member {
			t.Fatalf("%s extract = %s, want %s", kind, got, member)
		}
		for _, bad := range []string{
			`{"` + spec.field + `":` + member + `,"` + spec.field + `":` + member + `}`,
			`{"` + strings.ToUpper(spec.field) + `":` + member + `}`,
			string(envelope) + ` trailing`,
		} {
			if got := extractCapabilityField(json.RawMessage(bad), kind); got != nil {
				t.Fatalf("%s accepted ambiguous envelope %s: %s", kind, bad, got)
			}
		}
	}
	if err := ConfirmDistinctQuerySchema(core.DistinctQuerySchema); err != nil {
		t.Fatal(err)
	}
	if err := ConfirmDistinctReplaySchema(core.DistinctQuerySchema); err != nil {
		t.Fatal(err)
	}
	if err := ConfirmDistinctQuerySchema("cairn.semantic/19"); core.Code(err) != "UNSUPPORTED_QUERY_INPUT" || !strings.Contains(err.Error(), "committed") {
		t.Fatalf("compile schema error mismatch: %v", err)
	}
	if err := ConfirmDistinctReplaySchema("cairn.semantic/19"); core.Code(err) != "UNSUPPORTED_QUERY_INPUT" || strings.Contains(err.Error(), "committed") {
		t.Fatalf("replay schema error mismatch: %v", err)
	}
}
