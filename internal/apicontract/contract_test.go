package apicontract

import (
	"bytes"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/halbritt/cairn/localapi"
)

var generated []byte

func document(t *testing.T) (map[string]any, []byte) {
	t.Helper()
	if localapi.Protocol.Min != 1 {
		t.Skip("the committed contract describes the default build, not test-only protocol builds")
	}
	if generated == nil {
		var err error
		if generated, err = Generate("../../localapi"); err != nil {
			t.Fatal(err)
		}
	}
	var parsed map[string]any
	if err := json.Unmarshal(generated, &parsed); err != nil {
		t.Fatal(err)
	}
	return parsed, generated
}

func operations(t *testing.T, doc map[string]any) map[string]map[string]any {
	t.Helper()
	out := map[string]map[string]any{}
	for path, item := range doc["paths"].(map[string]any) {
		out[strings.TrimPrefix(path, "/v1/")] = item.(map[string]any)["post"].(map[string]any)
	}
	return out
}

// TestCommittedContractIsCurrent is the drift check behind make check.
func TestCommittedContractIsCurrent(t *testing.T) {
	_, current := document(t)
	committed, err := os.ReadFile("../../docs/api/openapi.json")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(committed, current) {
		t.Fatal("docs/api/openapi.json is stale; run make contract and review the diff")
	}
}

func TestGenerationIsDeterministic(t *testing.T) {
	_, first := document(t)
	second, err := Generate("../../localapi")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(first, second) {
		t.Fatal("two generations differ")
	}
}

func TestEveryOperationIsFullyClassified(t *testing.T) {
	doc, _ := document(t)
	classes := doc["x-cairn-retry-classes"].(map[string]any)
	ops := operations(t, doc)
	if len(ops) < 70 {
		t.Fatalf("only %d operations extracted", len(ops))
	}
	for name, op := range ops {
		if _, ok := classes[op["x-cairn-retry"].(string)]; !ok {
			t.Errorf("%s: retry class %v is undefined", name, op["x-cairn-retry"])
		}
		switch op["x-cairn-remote"] {
		case "allowed", "denied":
		default:
			t.Errorf("%s: remote classification %v", name, op["x-cairn-remote"])
		}
		if op["x-cairn-request-limit-bytes"].(float64) <= 0 {
			t.Errorf("%s: no request limit", name)
		}
	}
	// Decisions that matter for retry safety must not drift silently.
	for name, want := range map[string]string{"event-complete": "request-id", "event-publish": "request-id", "create": "request-id", "event-next": "no-key", "claim-run": "no-key", "event-renew": "lease", "version": "read"} {
		if got := ops[name]["x-cairn-retry"]; got != want {
			t.Errorf("%s retry = %v, want %s", name, got, want)
		}
	}
	for name, want := range map[string]string{"register-context": "denied", "event-next": "denied", "event-complete": "allowed", "version": "allowed", "session-tool-capture": "denied"} {
		if got := ops[name]["x-cairn-remote"]; got != want {
			t.Errorf("%s remote = %v, want %s", name, got, want)
		}
	}
}

func TestErrorInventoryIncludesTransportAndStoreCodes(t *testing.T) {
	doc, _ := document(t)
	pairs := map[string]bool{}
	for _, raw := range doc["x-cairn-errors"].([]any) {
		entry := raw.(map[string]any)
		pairs[entry["code"].(string)+"/"+jsonString(entry["http_status"])] = true
	}
	for _, want := range []string{"UPSTREAM_UNAVAILABLE/502", "UPSTREAM_UNCERTAIN/504", "AUTHORITY_DENIED/401", "AUTHORITY_DENIED/403", "INVALID_REQUEST/400", "STALE_LEASE/409", "IDEMPOTENCY_CONFLICT/409", "STORE_ERROR/500", "RESTORE_PAUSED/503"} {
		if !pairs[want] {
			t.Errorf("error inventory lacks %s", want)
		}
	}
	if doc["x-cairn-default-error-status"].(float64) != 422 {
		t.Errorf("default status %v", doc["x-cairn-default-error-status"])
	}
}

func jsonString(value any) string {
	encoded, _ := json.Marshal(value)
	return string(encoded)
}

func TestAcceptedAndCanonicalRequestViews(t *testing.T) {
	doc, raw := document(t)
	validator, err := newSchemaValidator(raw, false)
	if err != nil {
		t.Fatal(err)
	}
	get := operations(t, doc)["get"]
	accepted := get["requestBody"].(map[string]any)["content"].(map[string]any)["application/json"].(map[string]any)["schema"]
	canonicalSchema := get["x-cairn-canonical-request"]
	for body, want := range map[string][2]bool{ // [accepted, canonical]
		`{"record_id":"x"}`:           {true, true},
		`{}`:                          {true, false},
		`null`:                        {true, false},
		`{"record_id":null}`:          {true, false},
		`{"record_id":"x","extra":1}`: {false, true},
		`{"record_id":1}`:             {false, false},
	} {
		if got := validator.Validate(accepted, []byte(body)) == nil; got != want[0] {
			t.Errorf("accepted %s = %v", body, got)
		}
		if got := validator.Validate(canonicalSchema, []byte(body)) == nil; got != want[1] {
			t.Errorf("canonical %s = %v", body, got)
		}
	}
}
