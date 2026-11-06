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

// This transport boundary verifies CLI routing, not server authentication.
// Session identity must not silently become a recipient-selection predicate.
func TestNoticePendingKeepsSessionOutOfRecipientFilter(t *testing.T) {
	root := t.TempDir()
	token := filepath.Join(root, "token")
	if err := os.WriteFile(token, []byte("notice-review-token"), 0600); err != nil {
		t.Fatal(err)
	}
	socket := filepath.Join(root, "api.sock")
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	type observed struct {
		path, auth, agent, execution string
		body                         core.NoticePendingRequest
		err                          error
	}
	requests := make(chan observed, 1)
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got := observed{path: r.URL.Path, auth: r.Header.Get("Authorization"),
			agent: r.Header.Get("Cairn-Agent-ID"), execution: r.Header.Get("Cairn-Execution-ID")}
		got.err = json.NewDecoder(r.Body).Decode(&got.body)
		requests <- got
		_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true,
			"status": "OK", "data": map[string]any{"notices": []any{}, "count": 0}})
	})}
	done := make(chan error, 1)
	go func() { done <- server.Serve(listener) }()
	t.Cleanup(func() {
		if err := server.Close(); err != nil {
			t.Error(err)
		}
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			t.Error("test HTTP server did not stop")
		}
	})
	sender, execution, recipient := uuid.NewString(), uuid.NewString(), uuid.NewString()
	connection := []string{"--socket", socket, "--token-file", token, "--agent-id", sender, "--execution-id", execution}
	for _, test := range []struct {
		name, recipient string
		prebound        bool
	}{
		{name: "top-level sender inspection"},
		{name: "explicit distinct recipient", recipient: recipient},
		{name: "prebound sender inspection", prebound: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			args := []string{"notice-pending"}
			if test.prebound {
				args = append([]string{"agent"}, connection...)
				args = append(args, "notice-pending")
			} else {
				args = append(args, connection...)
			}
			args = append(args, "--repo", "fixture:notice-review", "--state", "queued", "--limit", "7")
			if test.recipient != "" {
				args = append(args, "--agent", test.recipient)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			if _, err := run(ctx, args, strings.NewReader("")); err != nil {
				t.Fatal(err)
			}
			var got observed
			select {
			case got = <-requests:
			case <-ctx.Done():
				t.Fatal("CLI did not issue the pending-notice read")
			}
			if got.err != nil || got.path != "/v1/event-notice-pending" || got.auth != "Bearer notice-review-token" ||
				got.agent != sender || got.execution != execution {
				t.Fatalf("session routing changed: %+v", got)
			}
			if got.body.AgentID != "" || got.body.Agent != test.recipient || got.body.Repo != "fixture:notice-review" ||
				got.body.State != "queued" || got.body.Limit != 7 {
				t.Fatalf("session identity leaked into recipient filter or explicit selection was lost: %+v", got.body)
			}
		})
	}
}
