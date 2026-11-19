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
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestStatusCLISetGetHistoryAuthorizeAndMarkdown(t *testing.T) {
	ctx := context.Background()
	for _, helpArgs := range [][]string{{"status", "--help"}, {"agent", "status", "--help"}} {
		help, err := run(ctx, helpArgs, strings.NewReader(""))
		if err != nil || !strings.Contains(string(help.(commandHelp)), "cairn status set") ||
			!strings.Contains(string(help.(commandHelp)), "cairn status get") {
			t.Fatalf("status help %v: %v %v", helpArgs, help, err)
		}
	}

	root := t.TempDir()
	tokenPath := filepath.Join(root, "token")
	if err := os.WriteFile(tokenPath, []byte("test-token"), 0600); err != nil {
		t.Fatal(err)
	}
	sockPath := filepath.Join(root, "api.sock")
	listener, err := net.Listen("unix", sockPath)
	if err != nil {
		t.Fatal(err)
	}
	var gotSet core.SetWorkstreamStatusRequest
	cp := time.Date(2026, 11, 19, 9, 0, 0, 0, time.UTC)
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1/status-set":
			if err := json.NewDecoder(r.Body).Decode(&gotSet); err != nil {
				http.Error(w, "bad json", http.StatusBadRequest)
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK",
				"data": core.WorkstreamStatus{Repo: "repo", Stream: gotSet.Stream, State: gotSet.State, Current: gotSet.Current, Revision: 1, UpdatedAt: cp}})
		case "/v1/status-get":
			_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK",
				"data": core.WorkstreamBoard{Repo: "repo", MaxStreamBytes: 2048, Streams: []core.WorkstreamStatus{
					{Repo: "repo", Stream: "foundation", State: "active", Current: "CAIRN-466", NextCheckpoint: &cp, Blocker: "none", Writer: "agent/lead", Revision: 2, UpdatedAt: cp},
				}}})
		default:
			http.Error(w, "unexpected path", http.StatusNotFound)
		}
	})}
	go func() { _ = server.Serve(listener) }()
	t.Cleanup(func() { _ = server.Close() })

	reqID := uuid.NewString()
	_, err = run(ctx, []string{"status", "set", "--socket", sockPath, "--token-file", tokenPath,
		"--request-id", reqID, "--stream", "foundation", "--state", "active", "--current", "CAIRN-466",
		"--next-checkpoint", "2026-11-19T09:00:00Z", "--blocker", "none", "--decision-needed", "none"}, strings.NewReader(""))
	if err != nil || gotSet.RequestID != reqID || gotSet.Stream != "foundation" || gotSet.State != "active" || gotSet.NextCheckpoint == nil {
		t.Fatalf("status set mismatch: %+v %v", gotSet, err)
	}

	md, err := run(ctx, []string{"status", "get", "--socket", sockPath, "--token-file", tokenPath, "--format", "markdown"}, strings.NewReader(""))
	if err != nil || !strings.Contains(string(md.(commandHelp)), "| foundation | active | CAIRN-466 |") {
		t.Fatalf("status get markdown mismatch: %v %v", md, err)
	}
}

func TestStatusCLIFlagValidationAndRefusalExitCode(t *testing.T) {
	ctx := context.Background()
	for _, badArgs := range [][]string{
		{"status", "bogus", "--help"},
		{"status", "set", "--stream", "s1", "--state", "active", "--transfer-owner", "agent/x"},
		{"status", "get", "--local"},
		{"status", "history", "--stream", "s1", "--state", "blocked"},
		{"status", "authorize", "--stream", "s1", "--writer", "agent/x", "--owner", "agent/y"},
		{"status", "get", "--token-file", ""},
		{"status", "set", "--stream", "s1", "--state", "active", "--local", "--shareable"},
	} {
		if _, err := run(ctx, badArgs, strings.NewReader("")); core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("expected INVALID_REQUEST for %v, got %v", badArgs, err)
		}
	}
	env, exitCode := commandEnvelope(nil, &core.Error{Code: "BUDGET_REFUSED", Message: "workstream entry exceeds 2048-byte per-stream budget"})
	if exitCode != 2 || !strings.Contains(env.Message, "2048-byte") {
		t.Fatalf("BUDGET_REFUSED scrubbed or wrong exit code: code=%d env=%+v", exitCode, env)
	}
	md := renderWorkstreamMarkdown(core.WorkstreamBoard{
		Repo: "repo", More: true, NextAfter: "foundation",
		Streams: []core.WorkstreamStatus{{Stream: "foundation", State: "active", Current: `foo\|bar`, Writer: "agent/1", Revision: 1}},
	})
	if !strings.Contains(md, `foo\\\|bar`) || !strings.Contains(md, "--after foundation") {
		t.Fatalf("markdown escaping or pagination notice missing: %s", md)
	}
}
