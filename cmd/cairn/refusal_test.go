package main

import (
	"encoding/json"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"testing"
)

// Exercise the public CLI against a synthetic API, including its real exit and
// envelope handling. No database, profile provisioning or provider is involved.
func TestCLIRefusalClassification(t *testing.T) {
	dir, err := os.MkdirTemp("", "cairn-refusal-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	socket, token := filepath.Join(dir, "api.sock"), filepath.Join(dir, "token")
	if err := os.WriteFile(token, []byte("synthetic"), 0600); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	type refusalCase struct {
		code   string
		exit   int
		public bool
	}
	cases := []refusalCase{
		{"INVALID_REQUEST", 2, true}, {"NOT_FOUND", 3, true}, {"VERSION_CONFLICT", 4, true},
		{"AUTHORITY_DENIED", 6, true}, {"RESTORE_PAUSED", 7, true}, {"REFUSAL_UNRECORDED", 7, true},
		{"RUN_FAILED", 1, true}, {"BUDGET_REFUSED", 2, true}, {"STALE_SESSION", 4, true},
		{"STORE_ERROR", 7, false}, {"UNRECOGNIZED_CODE", 7, false},
		{"STALE_WORKER", 4, true}, {"WORKER_UNAVAILABLE", 4, true}, {"POOL_FULL", 4, true},
		{"POOL_PAUSED", 4, true}, {"STALE_RESOLUTION", 4, true}, {"AGENT_BUSY", 4, true},
		{"DELIVERY_ACTIVE", 4, true}, {"FORGET_REQUIRED", 4, true},
	}
	requested := make(chan refusalCase, 1)
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c := <-requested
		w.WriteHeader(http.StatusConflict)
		json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": false, "status": c.code, "message": "fixture diagnostic"})
	})}
	go server.Serve(listener)
	t.Cleanup(func() { server.Close() })
	for _, c := range cases {
		t.Run(c.code, func(t *testing.T) {
			requested <- c
			exit, result := operatorProcess(t, "agent", "--socket", socket, "--token-file", token, "pull", "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002")
			wantMessage := "operation failed; inspect the local store and task runtime"
			if c.public {
				wantMessage = c.code + ": fixture diagnostic"
			}
			if exit != c.exit || result.Status != c.code || result.Message != wantMessage {
				t.Fatalf("exit=%d result=%+v; want exit=%d message=%q", exit, result, c.exit, wantMessage)
			}
		})
	}
}
