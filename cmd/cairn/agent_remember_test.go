package main

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func agentCaptureAPI(t *testing.T, handler http.HandlerFunc) []string {
	t.Helper()
	root, err := os.MkdirTemp("/tmp", "cairn-capture-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.RemoveAll(root); err != nil {
			t.Error(err)
		}
	})
	token, socket := filepath.Join(root, "agent.token"), filepath.Join(root, "api.sock")
	if err := os.WriteFile(token, []byte("capture-test-token"), 0600); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	server := &http.Server{Handler: handler}
	go func() {
		if err := server.Serve(listener); err != nil && err != http.ErrServerClosed {
			t.Error(err)
		}
	}()
	t.Cleanup(func() {
		if err := server.Close(); err != nil {
			t.Error(err)
		}
	})
	t.Setenv("HOME", "")
	t.Setenv("CAIRN_HOME", "")
	t.Setenv("CAIRN_DATABASE_URL", "host=/absent-agent-capture dbname=denied")
	return []string{"agent", "--socket", socket, "--token-file", token}
}

func TestAgentRememberUsesAuthenticatedCreate(t *testing.T) {
	requestID, recordID := uuid.NewString(), uuid.NewString()
	body := "Preserve literal $(printf nope), 'quotes', and 日本語."
	scope := core.Scope{Repo: "fixture:remember", TaskID: "*", RunID: "*"}
	observed := make(chan core.CreateRequest, 1)
	args := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/create" || r.Header.Get("Authorization") != "Bearer capture-test-token" {
			t.Error("capture did not use authenticated create")
		}
		var req core.CreateRequest
		decoder := json.NewDecoder(r.Body)
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&req); err != nil {
			t.Error(err)
		}
		observed <- req
		record := core.Record{RecordID: recordID, Version: 1, Class: "A", Lifecycle: "active", Draft: req.Draft, ObservedWriter: "agent:server-owned", Witness: "testimony"}
		if err := json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": record}); err != nil {
			t.Error(err)
		}
	})
	result, err := run(context.Background(), append(args, "remember", "--repo", scope.Repo, "--kind", "lesson", "--shareable", "--pins", `{"task_phase":"validation"}`, "--request-id", requestID, body), strings.NewReader(""))
	if err != nil {
		t.Fatal(err)
	}
	req := <-observed
	if req.RequestID != requestID || req.Draft.Scope != scope || req.Draft.Body != body || req.Draft.Kind != "lesson" || req.Draft.Sensitivity != "shareable" || req.Draft.ClaimType != "self" {
		t.Fatalf("capture changed note or intent: %+v", req)
	}
	if req.Draft.Pins == nil || req.Draft.Pins.TaskPhase != "validation" {
		t.Fatalf("capture lost pins: %+v", req.Draft.Pins)
	}
	encoded, err := json.Marshal(result)
	if err != nil {
		t.Fatal(err)
	}
	var record core.Record
	if err = json.Unmarshal(encoded, &record); err != nil {
		t.Fatal(err)
	}
	if record.RecordID != recordID || record.ObservedWriter != "agent:server-owned" || record.Witness != "testimony" || record.Class != "A" {
		t.Fatalf("capture changed service-owned identity or class: %s", encoded)
	}
}

func TestAgentRememberReadsStdinExactly(t *testing.T) {
	body := "First line with \"quotes\".\nSecond line with 日本語.\n"
	scope := core.Scope{Repo: "fixture:remember", TaskID: "capture", RunID: "run-1"}
	observed := make(chan core.CreateRequest, 1)
	args := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		var req core.CreateRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Error(err)
		}
		observed <- req
		if err := json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": core.Record{RecordID: uuid.NewString(), Draft: req.Draft}}); err != nil {
			t.Error(err)
		}
	})
	_, err := run(context.Background(), append(args, "remember", "--repo", scope.Repo, "--task", scope.TaskID, "--run", scope.RunID, "--stdin"), strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req := <-observed
	if req.Draft.Body != body || req.Draft.Scope != scope || req.Draft.Kind != "note" || req.Draft.Sensitivity != "local" {
		t.Fatalf("stdin capture changed text, scope or defaults: %+v", req)
	}
	if _, err := uuid.Parse(req.RequestID); err != nil {
		t.Fatalf("missing default retry UUID: %v", err)
	}
}

func TestRememberExplicitPins(t *testing.T) {
	args := []string{"--pins", `{"task_phase":"validation","task_class":"repair","revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","valid_until":"2030-01-01T00:00:00Z"}`, "selected guidance"}
	req, err := rememberRequest(args, strings.NewReader(""))
	if err != nil {
		t.Fatal(err)
	}
	if req.Draft.Pins == nil || req.Draft.Pins.TaskPhase != "validation" || req.Draft.Pins.TaskClass != "repair" || req.Draft.Pins.Revision != strings.Repeat("a", 40) || req.Draft.Pins.ValidUntil == nil {
		t.Fatalf("lost capture constraints: %+v", req.Draft.Pins)
	}
	plain, err := rememberRequest([]string{"selected guidance"}, strings.NewReader(""))
	if err != nil || plain.Draft.Pins != nil {
		t.Fatalf("default capture gained pins: %+v %v", plain, err)
	}
	for _, pins := range []string{"", `null`, `{"task_phaze":"validation"}`, `{"task_phase":7}`, `{} {}`, `{"valid_until":"tomorrow"}`} {
		_, err := rememberRequest([]string{"--pins", pins, "note"}, strings.NewReader(""))
		if core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("invalid pins %s: %v", pins, err)
		}
	}
}

func TestRememberExplicitEntities(t *testing.T) {
	req, err := rememberRequest([]string{"--entity-file", "core/currentness.go", "--entity-symbol", "core.applicabilityReason", "selected guidance"}, strings.NewReader(""))
	if err != nil {
		t.Fatal(err)
	}
	if len(req.Draft.Entities) != 2 || req.Draft.Entities[0] != (core.EntityRef{Kind: "file", Name: "core/currentness.go"}) || req.Draft.Entities[1].Kind != "symbol" {
		t.Fatalf("lost associations: %+v", req.Draft.Entities)
	}
	if _, err = rememberRequest([]string{"--entity-file", "../outside", "note"}, strings.NewReader("")); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("invalid relative identity accepted: %v", err)
	}
}

func TestAgentRememberRejectsInvalidInputBeforeCapture(t *testing.T) {
	args := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		t.Error("invalid capture reached the API")
		w.WriteHeader(http.StatusBadRequest)
	})
	for _, test := range []struct {
		name  string
		flags []string
		text  string
	}{
		{"empty", nil, ""},
		{"empty_pins", []string{"--pins", "", "note"}, ""},
		{"empty_pins_equals", []string{"--pins=", "note"}, ""},
		{"blank_stdin", []string{"--stdin"}, " \n\t"},
		{"oversized_stdin", []string{"--stdin"}, strings.Repeat("x", 65537)},
		{"invalid_utf8", []string{"--stdin"}, string([]byte{0xff})},
		{"mixed_sources", []string{"--stdin", "argv text"}, "stdin text"},
		{"identity_flag", []string{"--principal", "somebody", "note"}, ""},
		{"destination_flag", []string{"--destination", "local", "note"}, ""},
	} {
		t.Run(test.name, func(t *testing.T) {
			command := append(append([]string{}, args...), "remember")
			_, err := run(context.Background(), append(command, test.flags...), strings.NewReader(test.text))
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("invalid input was accepted or had wrong error: %v", err)
			}
		})
	}
}
