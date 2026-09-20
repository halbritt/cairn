package main

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/halbritt/cairn/core"
)

func TestCompleteRejectsLossyTextBeforeAPI(t *testing.T) {
	var calls atomic.Int32
	args := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		_, _ = w.Write([]byte(`{"schema":"cairn.response/1","ok":true,"status":"OK","data":{}}`))
	})
	for _, route := range []string{"agent", "top-level"} {
		for _, source := range []string{"stdin", "repo", "kind"} {
			t.Run(route+"/"+source, func(t *testing.T) {
				command := append([]string{}, args...)
				command = append(command, "complete")
				if route == "top-level" {
					command = append([]string{"complete"}, args[1:]...)
				}
				body := "selected result"
				if source == "stdin" {
					body = "result \xff text"
				} else {
					command = append(command, "--"+source, "label-\xff")
				}
				command = append(command, "--request-id", "8c699051-59dd-405b-b87e-65a083a1741a", "--lease", "41182873-ad24-4858-ae10-1ad4a5090778", "--stdin", "f9df052d-fb86-4d83-bc59-7503c0a7bd32")
				_, err := run(context.Background(), command, strings.NewReader(body))
				if core.Code(err) != "INVALID_REQUEST" {
					t.Fatalf("lossy %s accepted: %v", source, err)
				}
			})
		}
	}
	if calls.Load() != 0 {
		t.Fatalf("lossy requests reached API: %d", calls.Load())
	}
}

func TestAgentRejectsLossyFlagsBeforeAPI(t *testing.T) {
	var calls atomic.Int32
	args := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		_, _ = w.Write([]byte(`{"schema":"cairn.response/1","ok":true,"status":"OK","data":{}}`))
	})
	file := filepath.Join(t.TempDir(), "evidence")
	if err := os.WriteFile(file, []byte("selected evidence"), 0600); err != nil {
		t.Fatal(err)
	}
	for _, flag := range []string{"repo", "kind", "task", "run", "source"} {
		t.Run(flag, func(t *testing.T) {
			command := append(append([]string{}, args...), "remember", "--"+flag, "label-\xff", "selected note")
			if flag == "source" {
				command = append(append([]string{}, args...), "evidence", "--file", file, "--source", "label-\xff")
			}
			_, err := run(context.Background(), command, strings.NewReader(""))
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("lossy flag accepted: %v", err)
			}
		})
	}
	if calls.Load() != 0 {
		t.Fatalf("lossy requests reached API: %d", calls.Load())
	}
}

func TestCompletePreservesUnicodeText(t *testing.T) {
	body := "日本語 café � \\ud800\n"
	var calls atomic.Int32
	args := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		var request core.CompleteEventRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Error(err)
		}
		if request.Draft == nil || request.Draft.Body != body || request.Draft.Scope.Repo != "fixture:日本語" {
			t.Errorf("changed valid text: %+v", request.Draft)
		}
		_, _ = w.Write([]byte(`{"schema":"cairn.response/1","ok":true,"status":"OK","data":{}}`))
	})
	for _, route := range []string{"agent", "top-level"} {
		command := append(append([]string{}, args...), "complete")
		if route == "top-level" {
			command = append([]string{"complete"}, args[1:]...)
		}
		command = append(command, "--repo", "fixture:日本語", "--request-id", "8c699051-59dd-405b-b87e-65a083a1741a", "--lease", "41182873-ad24-4858-ae10-1ad4a5090778", "--stdin", "f9df052d-fb86-4d83-bc59-7503c0a7bd32")
		if _, err := run(context.Background(), command, strings.NewReader(body)); err != nil {
			t.Fatal(err)
		}
	}
	if calls.Load() != 2 {
		t.Fatalf("valid requests not delivered: %d", calls.Load())
	}
}

func TestRequestJSONRejectsLossyUnicode(t *testing.T) {
	for _, body := range []string{"{\"body\":\"\xff\"}", `{"body":"\ud800"}`, `{"body":"\udc00"}`} {
		var request core.EvidenceRequest
		if err := decode(strings.NewReader(body), &request); err == nil {
			t.Errorf("operator JSON changed source: %q", body)
		}
	}
}

func TestAgentRejectsLossyJSONBeforeAPI(t *testing.T) {
	args := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		t.Error("lossy JSON reached API")
		_, _ = w.Write([]byte(`{"schema":"cairn.response/1","ok":true,"status":"OK","data":{}}`))
	})
	for _, body := range []string{"{\"body\":\"\xff\"}", `{"body":"\ud800"}`, `{"body":"\udc00"}`} {
		_, err := run(context.Background(), append(append([]string{}, args...), "evidence"), strings.NewReader(body))
		if core.Code(err) != "INVALID_REQUEST" {
			t.Errorf("lossy input did not refuse locally: %v", err)
		}
	}
}
