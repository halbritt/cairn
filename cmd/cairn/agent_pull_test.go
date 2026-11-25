package main

import (
	"context"
	"encoding/json"
	"net/http"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/halbritt/cairn/core"
)

func TestAgentPullAcceptsStructuredArguments(t *testing.T) {
	for _, operation := range []string{"pull", "pull-evidence"} {
		t.Run(operation, func(t *testing.T) {
			payload := map[string]any{"request_id": "retry-id", "receipt_id": "receipt-id", "handle": "handle-id", "span": map[string]any{"offset": float64(7), "length": float64(13)}}
			endpoint := "/v1/expand"
			if operation == "pull-evidence" {
				endpoint = "/v1/expand-evidence"
				payload["evidence_id"] = "evidence-id"
				payload["expected_sha256"] = strings.Repeat("a", 64)
			}
			var calls atomic.Int32
			args := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				if r.URL.Path != endpoint || r.Method != http.MethodPost || r.Header.Get("Authorization") != "Bearer capture-test-token" {
					t.Error("pull did not use its authenticated expansion endpoint")
				}
				var got map[string]any
				if err := json.NewDecoder(r.Body).Decode(&got); err != nil {
					t.Error(err)
				}
				if !reflect.DeepEqual(got, payload) {
					t.Errorf("structured arguments changed: got %#v, want %#v", got, payload)
				}
				if err := json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": map[string]int{"credits_remaining": 3}}); err != nil {
					t.Error(err)
				}
			})
			body, err := json.Marshal(payload)
			if err != nil {
				t.Fatal(err)
			}
			result, err := run(context.Background(), append(args, operation), strings.NewReader(string(body)))
			if err != nil {
				t.Fatal(err)
			}
			encoded, err := json.Marshal(result)
			if err != nil || string(encoded) != `{"credits_remaining":3}` || calls.Load() != 1 {
				t.Fatalf("result=%s calls=%d err=%v", encoded, calls.Load(), err)
			}
		})
	}
}

func TestAgentPullRejectsInvalidInputBeforeAPI(t *testing.T) {
	args := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		t.Error("invalid pull reached API")
		w.WriteHeader(http.StatusBadRequest)
	})
	for _, operation := range []string{"pull", "pull-evidence"} {
		for _, body := range []string{"", "{", `{} {}`, `{"handle":"\ud800"}`, strings.Repeat(" ", 128*1024+1)} {
			_, err := run(context.Background(), append(append([]string{}, args...), operation), strings.NewReader(body))
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("%s accepted invalid input: %v", operation, err)
			}
		}
		_, err := run(context.Background(), append(append([]string{}, args...), operation, "--request-id", "retry-id"), strings.NewReader(`{"receipt_id":"receipt-id","handle":"handle-id"}`))
		if core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("%s combined partial shell arguments with stdin: %v", operation, err)
		}
	}
}

func TestAgentPullOpeningFlagForwarded(t *testing.T) {
	var calls atomic.Int32
	args := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		var got core.ExpandRequest
		if err := json.NewDecoder(r.Body).Decode(&got); err != nil {
			t.Error(err)
		}
		if got.OpeningBytes != 96 || got.Span == nil || got.Span.Offset != 700 || got.Span.Length != 300 || got.RequestID != "retry-id" || r.URL.Path != "/v1/expand" {
			t.Errorf("changed opening request: %+v", got)
		}
		json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": map[string]int{"credits_remaining": 3}})
	})
	_, err := run(context.Background(), append(args, "pull", "--request-id", "retry-id", "--offset", "700", "--length", "300", "--opening-bytes", "96", "receipt-id", "handle-id"), strings.NewReader(""))
	if err != nil || calls.Load() != 1 {
		t.Fatalf("opening flag: %v calls=%d", err, calls.Load())
	}
}

func TestAgentPullSessionDedupForceAndOptOut(t *testing.T) {
	t.Setenv("XDG_CACHE_HOME", t.TempDir())
	t.Setenv("CAIRN_SESSION_ID", "test-cli-dedup-session")
	var expandCalls atomic.Int32
	var version atomic.Int32
	version.Store(1)
	args := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1/expand":
			expandCalls.Add(1)
			var req core.ExpandRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				t.Error(err)
			}
			v := int(version.Load())
			var span *core.NoteSpan
			body := "complete body"
			if req.Span != nil {
				body = ""
				span = &core.NoteSpan{
					ByteSpan: core.ByteSpan{Offset: req.Span.Offset, End: req.Span.Offset + req.Span.Length, TotalBytes: 500, Body: "part"},
				}
			}
			json.NewEncoder(w).Encode(map[string]any{
				"schema": "cairn.response/1",
				"ok":     true,
				"status": "OK",
				"data": core.Expansion{
					Selection: core.Selection{
						Record: core.Record{
							RecordID: "11111111-1111-4111-8111-111111111111",
							Version:  v,
							Class:    "A",
							Draft:    core.Draft{Kind: "note", Body: body},
						},
					},
					Span:             span,
					CreditsRemaining: 3,
					BytesRemaining:   20000,
				},
			})
		default:
			t.Fatalf("unexpected path %s", r.URL.Path)
		}
	})

	// 1. First span pull hits API
	res, err := run(context.Background(), append(append([]string{}, args...), "pull", "--request-id", "r1", "--offset", "0", "--length", "50", "receipt-1", "handle-1"), strings.NewReader(""))
	if err != nil || expandCalls.Load() != 1 {
		t.Fatalf("first span pull failed: err=%v calls=%d", err, expandCalls.Load())
	}

	// 2. Same span with new request-id returns already_delivered with 0 API calls
	res, err = run(context.Background(), append(append([]string{}, args...), "pull", "--request-id", "r2", "--offset", "0", "--length", "50", "receipt-1", "handle-1"), strings.NewReader(""))
	if err != nil || expandCalls.Load() != 1 {
		t.Fatalf("repeat span pull failed: err=%v calls=%d", err, expandCalls.Load())
	}
	var hit map[string]any
	if err := json.Unmarshal(res.(json.RawMessage), &hit); err != nil || hit["already_delivered"] != true {
		t.Fatalf("expected already_delivered=true, got %s err=%v", res, err)
	}

	// 3. --force bypasses dedup
	_, err = run(context.Background(), append(append([]string{}, args...), "pull", "--force", "--request-id", "r3", "--offset", "0", "--length", "50", "receipt-1", "handle-1"), strings.NewReader(""))
	if err != nil || expandCalls.Load() != 2 {
		t.Fatalf("--force failed: err=%v calls=%d", err, expandCalls.Load())
	}

	// 4. Whole pull misses span cache and hits API
	_, err = run(context.Background(), append(append([]string{}, args...), "pull", "--request-id", "r4", "receipt-1", "handle-1"), strings.NewReader(""))
	if err != nil || expandCalls.Load() != 3 {
		t.Fatalf("whole pull failed: err=%v calls=%d", err, expandCalls.Load())
	}

	// 5. Repeat whole pull via stdin JSON returns already_delivered unless "force": true
	res, err = run(context.Background(), append(append([]string{}, args...), "pull"), strings.NewReader(`{"request_id":"r5","receipt_id":"receipt-1","handle":"handle-1"}`))
	if err != nil || expandCalls.Load() != 3 {
		t.Fatalf("stdin repeat pull failed: err=%v calls=%d", err, expandCalls.Load())
	}
	hit = nil
	if err := json.Unmarshal(res.(json.RawMessage), &hit); err != nil || hit["already_delivered"] != true {
		t.Fatalf("expected stdin already_delivered=true, got %s err=%v", res, err)
	}
	_, err = run(context.Background(), append(append([]string{}, args...), "pull"), strings.NewReader(`{"request_id":"r6","receipt_id":"receipt-1","handle":"handle-1","force":true}`))
	if err != nil || expandCalls.Load() != 4 {
		t.Fatalf("stdin force=true failed: err=%v calls=%d", err, expandCalls.Load())
	}

	// 6. CAIRN_RECALL_DEDUP=0 disables dedup
	t.Setenv("CAIRN_RECALL_DEDUP", "0")
	_, err = run(context.Background(), append(append([]string{}, args...), "pull", "--request-id", "r7", "receipt-1", "handle-1"), strings.NewReader(""))
	if err != nil || expandCalls.Load() != 5 {
		t.Fatalf("CAIRN_RECALL_DEDUP=0 failed: err=%v calls=%d", err, expandCalls.Load())
	}
}
