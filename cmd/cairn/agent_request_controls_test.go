package main

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestPublishTimingFlagHelpAndValidation(t *testing.T) {
	ctx := context.Background()

	// 1. Built-in help documents the timing flags.
	for _, args := range [][]string{{"publish", "--help"}, {"agent", "publish", "--help"}} {
		out, err := run(ctx, args, strings.NewReader(""))
		if err != nil {
			t.Fatalf("%v: %v", args, err)
		}
		help, ok := out.(commandHelp)
		if !ok {
			t.Fatalf("%v: unexpected output type %T", args, out)
		}
		helpText := string(help)
		if !strings.Contains(helpText, "--admission-expires-at RFC3339") {
			t.Fatalf("%v: help missing --admission-expires-at RFC3339: %s", args, helpText)
		}
		if !strings.Contains(helpText, "--task-deadline RFC3339") {
			t.Fatalf("%v: help missing --task-deadline RFC3339: %s", args, helpText)
		}
	}

	validReqID := uuid.NewString()
	recordID := uuid.NewString()

	// 2. Reject explicitly empty flags or whitespace.
	emptyCases := []struct {
		name string
		args []string
	}{
		{"empty_admission", []string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--admission-expires-at", "", recordID}},
		{"whitespace_admission", []string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--admission-expires-at", "   ", recordID}},
		{"empty_deadline", []string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--task-deadline", "", recordID}},
		{"whitespace_deadline", []string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--task-deadline", "   ", recordID}},
	}
	for _, tc := range emptyCases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := run(ctx, tc.args, strings.NewReader(""))
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("%s: expected INVALID_REQUEST, got: %v", tc.name, err)
			}
			if !strings.Contains(err.Error(), "cannot be explicitly empty") {
				t.Fatalf("%s: expected 'cannot be explicitly empty', got: %v", tc.name, err)
			}
		})
	}

	// 3. Reject invalid timestamp formats.
	invalidTimeCases := []struct {
		name string
		args []string
	}{
		{"not_a_time_admission", []string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--admission-expires-at", "tomorrow", recordID}},
		{"date_only_admission", []string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--admission-expires-at", "2026-09-16", recordID}},
		{"missing_tz_admission", []string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--admission-expires-at", "2026-09-16T12:00:00", recordID}},
		{"not_a_time_deadline", []string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--task-deadline", "next-week", recordID}},
		{"date_only_deadline", []string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--task-deadline", "2026-09-16", recordID}},
		{"missing_tz_deadline", []string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--task-deadline", "2026-09-16T12:00:00", recordID}},
	}
	for _, tc := range invalidTimeCases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := run(ctx, tc.args, strings.NewReader(""))
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("%s: expected INVALID_REQUEST, got: %v", tc.name, err)
			}
			if !strings.Contains(err.Error(), "must be an RFC3339 timestamp") {
				t.Fatalf("%s: expected 'must be an RFC3339 timestamp', got: %v", tc.name, err)
			}
		})
	}

	// 4. Timing flags are permitted ONLY on the publish command.
	stamp := "2026-09-16T12:00:00Z"
	otherCommands := []struct {
		command string
		args    []string
	}{
		{"inbox", []string{"inbox", "next", "--admission-expires-at", stamp}},
		{"inbox_deadline", []string{"inbox", "next", "--task-deadline", stamp}},
		{"ack", []string{"ack", "--request-id", validReqID, "--lease", uuid.NewString(), "--admission-expires-at", stamp, uuid.NewString()}},
		{"ack_deadline", []string{"ack", "--request-id", validReqID, "--lease", uuid.NewString(), "--task-deadline", stamp, uuid.NewString()}},
		{"complete", []string{"complete", "--request-id", validReqID, "--lease", uuid.NewString(), "--admission-expires-at", stamp, "--stdin", uuid.NewString()}},
		{"complete_deadline", []string{"complete", "--request-id", validReqID, "--lease", uuid.NewString(), "--task-deadline", stamp, "--stdin", uuid.NewString()}},
		{"retry", []string{"retry", "--lease", uuid.NewString(), "--admission-expires-at", stamp, uuid.NewString()}},
		{"renew", []string{"renew", "--lease", uuid.NewString(), "--task-deadline", stamp, uuid.NewString()}},
		{"subscribe", []string{"subscribe", "--request-id", validReqID, "--topic", "alerts", "--admission-expires-at", stamp}},
		{"unsubscribe", []string{"unsubscribe", "--request-id", validReqID, "--topic", "alerts", "--task-deadline", stamp}},
		{"subscriptions", []string{"subscriptions", "--admission-expires-at", stamp}},
		{"events", []string{"events", "--task-deadline", stamp}},
		{"event-status", []string{"event-status", "--admission-expires-at", stamp, uuid.NewString()}},
		{"event-stats", []string{"event-stats", "--task-deadline", stamp}},
	}
	for _, tc := range otherCommands {
		t.Run("unsupported_"+tc.command, func(t *testing.T) {
			_, err := run(ctx, tc.args, strings.NewReader(""))
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("%s: expected INVALID_REQUEST, got: %v", tc.command, err)
			}
			if !strings.Contains(err.Error(), "is not supported by") {
				t.Fatalf("%s: expected 'is not supported by', got: %v", tc.command, err)
			}
		})
	}
}

func TestPublishTimingRequestWiring(t *testing.T) {
	ctx := context.Background()

	root, err := os.MkdirTemp("/tmp", "cairn-publish-timing-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = os.RemoveAll(root)
	})

	socketPath := filepath.Join(root, "api.sock")
	tokenPath := filepath.Join(root, "agent.token")
	token := "test-agent-token"
	if err := os.WriteFile(tokenPath, []byte(token), 0600); err != nil {
		t.Fatal(err)
	}

	listener, err := net.Listen("unix", socketPath)
	if err != nil {
		t.Fatal(err)
	}

	var mu sync.Mutex
	var lastRequest core.PublishEventRequest
	var callCount int

	server := &http.Server{
		Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path != "/v1/event-publish" {
				http.NotFound(w, r)
				return
			}
			if r.Header.Get("Authorization") != "Bearer "+token {
				w.WriteHeader(http.StatusForbidden)
				return
			}
			var req core.PublishEventRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				t.Errorf("decode publish request: %v", err)
				w.WriteHeader(http.StatusBadRequest)
				return
			}
			mu.Lock()
			lastRequest = req
			callCount++
			mu.Unlock()

			eventID := uuid.NewString()
			resp := map[string]any{
				"schema": "cairn.response/1",
				"ok":     true,
				"status": "OK",
				"data": map[string]any{
					"event_id":    eventID,
					"position":    1,
					"repo":        req.Repo,
					"from":        "agent:test-agent",
					"created_at":  time.Now().UTC().Format(time.RFC3339Nano),
					"kind":        req.Kind,
					"ref":         req.Ref,
					"destination": req.Destination,
				},
			}
			_ = json.NewEncoder(w).Encode(resp)
		}),
	}

	go func() {
		_ = server.Serve(listener)
	}()
	t.Cleanup(func() {
		_ = server.Close()
	})

	recordID := uuid.NewString()
	connArgs := []string{"--socket", socketPath, "--token-file", tokenPath}

	// 1. Both timing flags set with RFC3339 and RFC3339Nano.
	admissionStr := "2026-09-16T12:00:00.123456789Z"
	deadlineStr := "2026-09-16T13:30:00-07:00"
	wantAdmission, err := time.Parse(time.RFC3339Nano, admissionStr)
	if err != nil {
		t.Fatal(err)
	}
	wantDeadline, err := time.Parse(time.RFC3339, deadlineStr)
	if err != nil {
		t.Fatal(err)
	}

	reqID := uuid.NewString()
	args := append(append([]string{"publish"}, connArgs...),
		"--request-id", reqID,
		"--to", "agent:worker",
		"--kind", "request",
		"--version", "1",
		"--admission-expires-at", admissionStr,
		"--task-deadline", deadlineStr,
		recordID,
	)

	_, err = run(ctx, args, strings.NewReader(""))
	if err != nil {
		t.Fatalf("publish with timing flags failed: %v", err)
	}

	mu.Lock()
	if callCount != 1 {
		t.Fatalf("expected 1 call, got %d", callCount)
	}
	if lastRequest.AdmissionExpiresAt == nil || !lastRequest.AdmissionExpiresAt.Equal(wantAdmission) {
		t.Fatalf("admission_expires_at mismatch: got %v, want %v", lastRequest.AdmissionExpiresAt, wantAdmission)
	}
	if lastRequest.AdmissionExpiresAt.Nanosecond() != wantAdmission.Nanosecond() {
		t.Fatalf("admission_expires_at nanoseconds lost: got %d, want %d", lastRequest.AdmissionExpiresAt.Nanosecond(), wantAdmission.Nanosecond())
	}
	if lastRequest.TaskDeadline == nil || !lastRequest.TaskDeadline.Equal(wantDeadline) {
		t.Fatalf("task_deadline mismatch: got %v, want %v", lastRequest.TaskDeadline, wantDeadline)
	}
	mu.Unlock()

	// 2. Only --admission-expires-at provided.
	reqID = uuid.NewString()
	args = append(append([]string{"publish"}, connArgs...),
		"--request-id", reqID,
		"--to", "agent:worker",
		"--kind", "request",
		"--version", "1",
		"--admission-expires-at", "2026-09-16T15:00:00Z",
		recordID,
	)
	wantOnlyAdmission, _ := time.Parse(time.RFC3339, "2026-09-16T15:00:00Z")

	_, err = run(ctx, args, strings.NewReader(""))
	if err != nil {
		t.Fatalf("publish with only admission failed: %v", err)
	}

	mu.Lock()
	if lastRequest.AdmissionExpiresAt == nil || !lastRequest.AdmissionExpiresAt.Equal(wantOnlyAdmission) {
		t.Fatalf("only admission mismatch: got %v, want %v", lastRequest.AdmissionExpiresAt, wantOnlyAdmission)
	}
	if lastRequest.TaskDeadline != nil {
		t.Fatalf("task_deadline should be nil, got: %v", lastRequest.TaskDeadline)
	}
	mu.Unlock()

	// 3. Only --task-deadline provided.
	reqID = uuid.NewString()
	args = append(append([]string{"publish"}, connArgs...),
		"--request-id", reqID,
		"--to", "agent:worker",
		"--kind", "request",
		"--version", "1",
		"--task-deadline", "2026-09-16T16:00:00Z",
		recordID,
	)
	wantOnlyDeadline, _ := time.Parse(time.RFC3339, "2026-09-16T16:00:00Z")

	_, err = run(ctx, args, strings.NewReader(""))
	if err != nil {
		t.Fatalf("publish with only deadline failed: %v", err)
	}

	mu.Lock()
	if lastRequest.AdmissionExpiresAt != nil {
		t.Fatalf("admission_expires_at should be nil, got: %v", lastRequest.AdmissionExpiresAt)
	}
	if lastRequest.TaskDeadline == nil || !lastRequest.TaskDeadline.Equal(wantOnlyDeadline) {
		t.Fatalf("only deadline mismatch: got %v, want %v", lastRequest.TaskDeadline, wantOnlyDeadline)
	}
	mu.Unlock()

	// 4. Normal publication without timing flags leaves both nil.
	reqID = uuid.NewString()
	args = append(append([]string{"publish"}, connArgs...),
		"--request-id", reqID,
		"--to", "agent:worker",
		"--kind", "request",
		"--version", "1",
		recordID,
	)

	_, err = run(ctx, args, strings.NewReader(""))
	if err != nil {
		t.Fatalf("normal publish failed: %v", err)
	}

	mu.Lock()
	if lastRequest.AdmissionExpiresAt != nil || lastRequest.TaskDeadline != nil {
		t.Fatalf("normal publish should have nil timing fields: admission=%v, deadline=%v", lastRequest.AdmissionExpiresAt, lastRequest.TaskDeadline)
	}
	mu.Unlock()
}
