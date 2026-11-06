package main

import (
	"context"
	"encoding/json"
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

func envelopeFields(t *testing.T, envelope response) []string {
	t.Helper()
	encoded, err := json.Marshal(envelope)
	if err != nil {
		t.Fatal(err)
	}
	var fields map[string]json.RawMessage
	if err = json.Unmarshal(encoded, &fields); err != nil {
		t.Fatal(err)
	}
	names := make([]string, 0, len(fields))
	for name := range fields {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

func TestCommandEnvelopeCarriesEventAdmissionRefusal(t *testing.T) {
	details := &core.EventAdmissionRefusal{Dimension: "topic_recipients", PolicyRevision: 4, RetryAfterSeconds: 2, Retryable: true}
	refused := &core.Error{Code: "EVENT_ADMISSION_REFUSED", Message: "event admission dimension topic_recipients at policy revision 4", Admission: details}
	envelope, code := commandEnvelope(nil, refused)
	if code != 4 || envelope.OK || envelope.Status != "EVENT_ADMISSION_REFUSED" || envelope.Admission != details ||
		envelope.Message != refused.Error() {
		t.Fatalf("admission envelope %+v exit %d", envelope, code)
	}
	if got := envelopeFields(t, envelope); !reflect.DeepEqual(got, []string{"admission", "message", "ok", "schema", "status"}) {
		t.Fatalf("admission envelope fields %v", got)
	}

	closure := &core.Error{Code: "CLOSURE_RESERVE_FULL", Message: "dimension closure_recipient; policy revision 4", Admission: details}
	envelope, code = commandEnvelope(nil, closure)
	if code != 4 || envelope.Admission != details || envelope.Message != closure.Error() {
		t.Fatalf("closure capacity envelope %+v exit %d", envelope, code)
	}
	unreconciled := &core.Error{Code: "CLOSURE_UNRECONCILED", Message: "closure reservations are not reconciled", Admission: details}
	envelope, code = commandEnvelope(nil, unreconciled)
	if code != 4 || envelope.Admission != nil || envelope.Message != unreconciled.Error() {
		t.Fatalf("unreconciled envelope %+v exit %d", envelope, code)
	}

	other := &core.Error{Code: "INVALID_REQUEST", Message: "bad", Admission: details, RefusalID: "refusal-1"}
	envelope, code = commandEnvelope(nil, other)
	if envelope.Admission != nil || code != 2 || envelope.RefusalID != "refusal-1" {
		t.Fatalf("non-admission envelope %+v exit %d", envelope, code)
	}
	if got := envelopeFields(t, envelope); !reflect.DeepEqual(got, []string{"message", "ok", "refusal_id", "schema", "status"}) {
		t.Fatalf("ordinary refusal fields %v", got)
	}

	envelope, code = commandEnvelope(map[string]int{"n": 1}, nil)
	if !envelope.OK || code != 0 || envelope.Admission != nil || envelope.Status != "OK" {
		t.Fatalf("success envelope %+v exit %d", envelope, code)
	}
}

func TestEventAdmissionRefusalsIsOperatorOnly(t *testing.T) {
	op, ok := operatorOperations["event-admission-refusals"]
	if !ok {
		t.Fatal("missing operator command event-admission-refusals")
	}
	if _, exposed := localapi.LookupOperation("event-admission-refusals"); exposed {
		t.Fatal("operator command event-admission-refusals in API")
	}
	if _, err := op.run(context.Background(), nil, strings.NewReader(`{"unexpected":true}`)); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("strict decode: %v", err)
	}
}
