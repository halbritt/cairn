package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/halbritt/cairn/core"
)

func TestEventHelpAndInvalidCommandsNeedNoCredentials(t *testing.T) {
	ctx := context.Background()
	for _, command := range []string{"publish", "inbox", "ack", "complete", "retry", "renew", "subscribe", "unsubscribe", "subscriptions", "events", "event-status", "event-stats"} {
		for _, args := range [][]string{{command, "--help"}, {"agent", command, "--help"}} {
			out, err := run(ctx, args, strings.NewReader(""))
			if err != nil {
				t.Fatalf("%v: %v", args, err)
			}
			if help, ok := out.(commandHelp); !ok || !strings.Contains(string(help), "authenticated") {
				t.Fatalf("%v: %v", args, out)
			}
		}
	}
	for _, args := range [][]string{{"publish", "--kind", "request", "--version", "1", "id"}, {"inbox", "next", "--to", "someone"}, {"ack", "--request-id", "id"}, {"complete", "--request-id", "id", "--lease", "id", "delivery"}, {"events", "--after", "-1", "unexpected"}} {
		_, err := run(ctx, args, strings.NewReader(""))
		if core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("%v: %v", args, err)
		}
	}
}

func TestCompleteResultScopeRequestEnvelope(t *testing.T) {
	requestID := "d3e3903e-aebb-4a25-8ee3-7660946a20a5"
	deliveryID := "b2e09aab-8d65-4548-b6a5-ef8340b5f9b9"
	leaseID := "e2c4b25d-af69-4ec1-8da9-6c543537c3cd"
	body := "Selected result 日本語.\n"
	for _, route := range []string{"agent", "top-level"} {
		for _, tc := range []struct {
			name      string
			flags     []string
			task, run string
		}{
			{"legacy", nil, "*", "*"},
			{"explicit-wildcards", []string{"--task", "*", "--run", "*"}, "*", "*"},
			{"task", []string{"--task", "task 日本語"}, "task 日本語", "*"},
			{"task-run", []string{"--task", "task-1", "--run", "run-2"}, "task-1", "run-2"},
			{"run-only", []string{"--run", "run-2"}, "*", "run-2"},
		} {
			t.Run(route+"/"+tc.name, func(t *testing.T) {
				observed := make(chan []byte, 2)
				connection := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
					if r.URL.Path != "/v1/event-complete" || r.Header.Get("Authorization") != "Bearer capture-test-token" {
						t.Error("completion did not use authenticated event-complete")
					}
					data, err := io.ReadAll(r.Body)
					if err != nil {
						t.Error(err)
					}
					observed <- data
					_, _ = w.Write([]byte(`{"schema":"cairn.response/1","ok":true,"status":"OK","data":{}}`))
				})
				args := append(append([]string{}, connection...), "complete")
				if route == "top-level" {
					args = append([]string{"complete"}, connection[1:]...)
				}
				args = append(args, "--request-id", requestID, "--lease", leaseID, "--repo", "fixture:results", "--shareable", "--kind", "lesson", "--stdin")
				args = append(args, tc.flags...)
				args = append(args, deliveryID)
				want := core.CompleteEventRequest{RequestID: requestID, DeliveryID: deliveryID, LeaseID: leaseID, Disposition: "handled", Draft: &core.Draft{Body: body, Kind: "lesson", ClaimType: "self", Sensitivity: "shareable", Scope: core.Scope{Repo: "fixture:results", TaskID: tc.task, RunID: tc.run}}}
				expected, err := json.Marshal(want)
				if err != nil {
					t.Fatal(err)
				}
				for attempt := 0; attempt < 2; attempt++ {
					if _, err := run(context.Background(), args, strings.NewReader(body)); err != nil {
						t.Fatal(err)
					}
					if got := <-observed; !bytes.Equal(got, expected) {
						t.Fatalf("changed completion envelope: got %s want %s", got, expected)
					}
				}
			})
		}
	}
}

func TestCompleteScopeValidationBeforeAPI(t *testing.T) {
	var calls atomic.Int32
	connection := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		_, _ = w.Write([]byte(`{"schema":"cairn.response/1","ok":true,"status":"OK","data":{}}`))
	})
	for _, flag := range []string{"--task", "--run"} {
		for _, value := range []string{"", " \t", strings.Repeat("x", 257), strings.Repeat("界", 86), "bad\xff", "bad\x00"} {
			args := append(append([]string{}, connection...), "complete", "--request-id", "d3e3903e-aebb-4a25-8ee3-7660946a20a5", "--lease", "e2c4b25d-af69-4ec1-8da9-6c543537c3cd", "--stdin", flag, value, "b2e09aab-8d65-4548-b6a5-ef8340b5f9b9")
			if _, err := run(context.Background(), args, strings.NewReader("result")); core.Code(err) != "INVALID_REQUEST" {
				t.Errorf("%s %q accepted: %v", flag, value, err)
			}
		}
	}
	if calls.Load() != 0 {
		t.Fatalf("invalid scope reached API: %d", calls.Load())
	}
}

func TestResultScopeFlagsOnlySupportedByComplete(t *testing.T) {
	for _, command := range []string{"publish", "inbox", "ack", "retry", "renew", "subscribe", "unsubscribe", "subscriptions", "events", "event-status", "event-stats", "response-group", "response-groups"} {
		for _, flag := range []string{"--task", "--run"} {
			args := []string{command}
			if command == "inbox" {
				args = append(args, "next")
			}
			args = append(args, flag, "*")
			_, err := run(context.Background(), args, strings.NewReader(""))
			if core.Code(err) != "INVALID_REQUEST" || !strings.Contains(err.Error(), flag+" is not supported by "+command) {
				t.Fatalf("%v: %v", args, err)
			}
		}
	}
}
