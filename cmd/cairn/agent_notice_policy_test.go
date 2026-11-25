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

func TestPublishNoticePolicyFlagsPreserveExplicitPublisherChoice(t *testing.T) {
	ctx := context.Background()
	help, err := run(ctx, []string{"publish", "--help"}, strings.NewReader(""))
	if err != nil || !strings.Contains(string(help.(commandHelp)), "--notice-urgency") ||
		!strings.Contains(string(help.(commandHelp)), "--notice-defer-seconds") {
		t.Fatalf("notice policy help: %v %v", help, err)
	}
	for _, flags := range [][]string{
		{"--action", "review", "--notice-defer-seconds", "60"},
		{"--action", "review", "--notice-urgency", "urgent", "--notice-defer-seconds", "1"},
		{"--action", "review", "--notice-urgency", "routine", "--notice-defer-seconds", "61"},
		{"--notice-urgency", "routine", "--kind", "request"},
	} {
		args := append([]string{"publish", "--request-id", uuid.NewString(), "--to", "agent:worker", "--kind", "notice", "--version", "1"}, flags...)
		args = append(args, uuid.NewString())
		if _, err := run(ctx, args, strings.NewReader("")); core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("invalid policy flags %v: %v", flags, err)
		}
	}

	root := t.TempDir()
	tokenPath := filepath.Join(root, "token")
	if err := os.WriteFile(tokenPath, []byte("test-token"), 0600); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("unix", filepath.Join(root, "api.sock"))
	if err != nil {
		t.Fatal(err)
	}
	var received core.PublishEventRequest
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/event-publish" || r.Header.Get("Authorization") != "Bearer test-token" {
			http.Error(w, "wrong request", http.StatusBadRequest)
			return
		}
		if err := json.NewDecoder(r.Body).Decode(&received); err != nil {
			http.Error(w, "invalid JSON", http.StatusBadRequest)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true,
			"status": "OK", "data": map[string]any{"event_id": uuid.NewString(),
				"kind": received.Kind, "ref": received.Ref, "destination": received.Destination}})
	})}
	go func() { _ = server.Serve(listener) }()
	t.Cleanup(func() { _ = server.Close() })
	_, err = run(ctx, []string{"publish", "--socket", filepath.Join(root, "api.sock"), "--token-file", tokenPath,
		"--request-id", uuid.NewString(), "--to", "agent:worker", "--kind", "notice", "--action", "review", "--version", "1",
		"--notice-urgency", "routine", "--notice-defer-seconds", "60", uuid.NewString()}, strings.NewReader(""))
	if err != nil || received.NoticePolicy == nil || *received.NoticePolicy != (core.NoticeWakePolicyRequest{Version: 1, Urgency: "routine", DeferSeconds: 60}) {
		t.Fatalf("published notice policy: %+v %v", received.NoticePolicy, err)
	}
}

func TestNoticeFlushCLIUsesAuthenticatedEventRoute(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	tokenPath := filepath.Join(root, "token")
	if err := os.WriteFile(tokenPath, []byte("test-token"), 0600); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("unix", filepath.Join(root, "api.sock"))
	if err != nil {
		t.Fatal(err)
	}
	var received core.NoticeFlushRequest
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/event-notice-flush" || r.Header.Get("Authorization") != "Bearer test-token" {
			http.Error(w, "wrong request", http.StatusBadRequest)
			return
		}
		if err := json.NewDecoder(r.Body).Decode(&received); err != nil {
			http.Error(w, "invalid JSON", http.StatusBadRequest)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true,
			"status": "OK", "data": map[string]any{"event_id": received.EventID, "request_id": received.RequestID,
				"flushed_at": "2026-10-24T00:00:00Z", "flushed_by": "sender", "released": 1,
				"already_ready": 0, "leased": 0, "terminal": 0}})
	})}
	go func() { _ = server.Serve(listener) }()
	t.Cleanup(func() { _ = server.Close() })
	eventID, requestID := uuid.NewString(), uuid.NewString()
	_, err = run(ctx, []string{"notice-flush", "--socket", filepath.Join(root, "api.sock"), "--token-file", tokenPath,
		"--request-id", requestID, eventID}, strings.NewReader(""))
	if err != nil || received.EventID != eventID || received.RequestID != requestID {
		t.Fatalf("notice flush route: %+v %v", received, err)
	}
}

func TestPublishAndSendNoticeIntentFlags(t *testing.T) {
	ctx := context.Background()
	for _, helpArgs := range [][]string{{"publish", "--help"}, {"agent", "send", "--help"}} {
		help, err := run(ctx, helpArgs, strings.NewReader(""))
		if err != nil || !strings.Contains(string(help.(commandHelp)), "--action") ||
			!strings.Contains(string(help.(commandHelp)), "--supersedes") ||
			!strings.Contains(string(help.(commandHelp)), "--expect-next-by") ||
			!strings.Contains(string(help.(commandHelp)), "--expect-by") {
			t.Fatalf("%v help missing notice/request deadline flags: %v %v", helpArgs, help, err)
		}
	}
	for _, flags := range [][]string{
		{"--kind", "request", "--action", "decide"},
		{"--kind", "request", "--supersedes", "stream/a"},
		{"--kind", "request", "--expect-next-by", "2026-11-20T12:00:00Z"},
		{"--kind", "notice"},
		{"--kind", "notice", "--action", "review", "--expect-by", "2026-11-20T12:00:00Z"},
		{"--kind", "request", "--expect-by", "not-a-timestamp"},
		{"--kind", "notice", "--action", "review", "--summary", ""},
		{"--kind", "notice", "--action", "review", "--summary", "line1\nline2"},
		{"--kind", "notice", "--action", "review", "--summary", strings.Repeat("x", 281)},
		{"--kind", "notice", "--action", "bad"},
		{"--kind", "notice", "--action", "review", "--supersedes", "bad space"},
		{"--kind", "notice", "--action", "review", "--expect-next-by", "not-a-timestamp"},
		{"--kind", "notice", "--action", "none", "--notice-urgency", "urgent"},
	} {
		args := append([]string{"publish", "--request-id", uuid.NewString(), "--to", "agent:worker", "--version", "1"}, flags...)
		args = append(args, uuid.NewString())
		if _, err := run(ctx, args, strings.NewReader("")); core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("expected INVALID_REQUEST for %v, got %v", flags, err)
		}
	}

	root := t.TempDir()
	tokenPath := filepath.Join(root, "token")
	if err := os.WriteFile(tokenPath, []byte("test-token"), 0600); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("unix", filepath.Join(root, "api.sock"))
	if err != nil {
		t.Fatal(err)
	}
	var received core.PublishEventRequest
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/event-publish" {
			http.Error(w, "wrong request", http.StatusBadRequest)
			return
		}
		if err := json.NewDecoder(r.Body).Decode(&received); err != nil {
			http.Error(w, "invalid JSON", http.StatusBadRequest)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true,
			"status": "OK", "data": map[string]any{"event_id": uuid.NewString(),
				"kind": received.Kind, "ref": received.Ref, "destination": received.Destination,
				"action": received.Action, "supersedes": received.Supersedes, "expect_next_by": received.ExpectNextBy, "expect_by": received.ExpectBy}})
	})}
	go func() { _ = server.Serve(listener) }()
	t.Cleanup(func() { _ = server.Close() })
	_, err = run(ctx, []string{"publish", "--socket", filepath.Join(root, "api.sock"), "--token-file", tokenPath,
		"--request-id", uuid.NewString(), "--to", "agent:worker", "--kind", "notice", "--version", "1",
		"--action", "review", "--supersedes", "stream/build", "--expect-next-by", "2026-11-20T12:00:00Z", uuid.NewString()}, strings.NewReader(""))
	if err != nil || received.Action != "review" || received.Supersedes != "stream/build" || received.ExpectNextBy == nil {
		t.Fatalf("publish notice intent fields: %+v %v", received, err)
	}
	received = core.PublishEventRequest{}
	_, err = run(ctx, []string{"publish", "--socket", filepath.Join(root, "api.sock"), "--token-file", tokenPath,
		"--request-id", uuid.NewString(), "--to", "agent:worker", "--kind", "request", "--version", "1",
		"--expect-by", "2026-11-20T13:00:00Z", uuid.NewString()}, strings.NewReader(""))
	if err != nil || received.ExpectBy == nil || received.Kind != "request" {
		t.Fatalf("publish request expect-by field: %+v %v", received, err)
	}
	if _, err := run(ctx, []string{"agent", "--socket", filepath.Join(root, "api.sock"), "--token-file", tokenPath, "send", "--journal", filepath.Join(root, "noact.json"), "--repo", "r", "--stdin", "--kind", "notice", "--to", "agent:w"}, strings.NewReader("hello")); err == nil || !strings.Contains(err.Error(), "none, review, or decide") {
		t.Fatalf("expected agent send missing --action refusal, got %v", err)
	}
}
