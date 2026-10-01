package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestAgentHandoffRequestStatusForwardsOneReadRequest(t *testing.T) {
	recordID, eventID := uuid.NewString(), uuid.NewString()
	stdin := `{"handoff":{"record_id":"` + recordID + `","version":3},"items":[{"item_id":"review","event_id":"` + eventID + `"}]}`
	data := `{"schema":"cairn.handoff-request-status/1","handoff":{"record_id":"` + recordID + `","version":3,"body_sha256":"` + strings.Repeat("a", 64) + `"},"links":"caller_asserted","items":[{"item_id":"review","event_id":"` + eventID + `","availability":"unavailable","task_outcome":"unknown"}]}`
	type observation struct {
		path, authorization string
		body                []byte
	}
	observed := make(chan observation, 1)
	connection := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
		}
		observed <- observation{r.URL.Path, r.Header.Get("Authorization"), body}
		_, _ = w.Write([]byte(`{"schema":"cairn.response/1","ok":true,"status":"OK","data":` + data + `}`))
	})
	out, err := run(context.Background(), append(append([]string{}, connection...), "handoff-request-status"), strings.NewReader(stdin))
	if err != nil {
		t.Fatal(err)
	}
	got := <-observed
	if got.path != "/v1/handoff-request-status" || got.authorization != "Bearer capture-test-token" {
		t.Fatalf("not the authenticated status operation: %s %q", got.path, got.authorization)
	}
	var sent, want map[string]any
	if json.Unmarshal(got.body, &sent) != nil || json.Unmarshal([]byte(stdin), &want) != nil || !reflect.DeepEqual(sent["handoff"], want["handoff"]) || !reflect.DeepEqual(sent["items"], want["items"]) {
		t.Fatalf("request was changed: %s", got.body)
	}
	if raw, ok := out.(json.RawMessage); !ok || string(raw) != data {
		t.Fatalf("response data changed: %T %v", out, out)
	}
}

func TestAgentHandoffRequestStatusRefusesBadInputBeforeTheAPI(t *testing.T) {
	var calls atomic.Int32
	connection := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		_, _ = w.Write([]byte(`{"schema":"cairn.response/1","ok":true,"status":"OK","data":{}}`))
	})
	base := append(append([]string{}, connection...), "handoff-request-status")
	for name, tc := range map[string]struct {
		args  []string
		stdin string
	}{
		"not JSON":             {nil, "review"},
		"empty":                {nil, ""},
		"positional arguments": {[]string{uuid.NewString()}, `{}`},
		"item flags":           {[]string{"--item", "review=" + uuid.NewString()}, `{}`},
	} {
		if _, err := run(context.Background(), append(append([]string{}, base...), tc.args...), strings.NewReader(tc.stdin)); core.Code(err) != "INVALID_REQUEST" {
			t.Errorf("%s: %v", name, err)
		}
	}
	if calls.Load() != 0 {
		t.Fatalf("invalid input reached the API %d times", calls.Load())
	}
}

func TestAgentHandoffRequestStatusReportsCodedRefusals(t *testing.T) {
	for status, code := range map[int]string{409: "VERSION_CONFLICT", 404: "NOT_FOUND", 422: "BUDGET_REFUSED"} {
		connection := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(status)
			_, _ = w.Write([]byte(`{"schema":"cairn.response/1","ok":false,"status":"` + code + `","message":"refused"}`))
		})
		args := append(append([]string{}, connection...), "handoff-request-status")
		out, err := run(context.Background(), args, strings.NewReader(`{"handoff":{"record_id":"`+uuid.NewString()+`","version":1},"items":[{"item_id":"a","event_id":"`+uuid.NewString()+`"}]}`))
		if core.Code(err) != code || out != nil {
			t.Errorf("%s: %v %v", code, out, err)
		}
	}
}

func TestAgentHandoffRequestStatusHelpNeedsNoCredentials(t *testing.T) {
	t.Setenv("HOME", "")
	t.Setenv("CAIRN_HOME", "")
	for _, args := range [][]string{{"agent", "handoff-request-status", "--help"}, {"agent", "--help"}} {
		out, err := run(context.Background(), args, strings.NewReader(""))
		help, ok := out.(commandHelp)
		if err != nil || !ok || !strings.Contains(string(help), "handoff-request-status") {
			t.Fatalf("%v: %v %v", args, out, err)
		}
	}
	out, err := run(context.Background(), []string{"agent", "handoff-request-status", "--help"}, strings.NewReader(""))
	if err != nil {
		t.Fatal(err)
	}
	for _, phrase := range []string{`"handoff":{"record_id"`, "task_outcome is always unknown", "caller assertions", "BUDGET_REFUSED", "VERSION_CONFLICT"} {
		if !strings.Contains(string(out.(commandHelp)), phrase) {
			t.Errorf("operation help missing %q", phrase)
		}
	}
}
