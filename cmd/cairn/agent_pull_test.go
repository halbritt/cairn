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
