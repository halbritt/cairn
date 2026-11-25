package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
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

func TestAgentPullConflictAfterWholePullSameVersion(t *testing.T) {
	t.Setenv("XDG_CACHE_HOME", t.TempDir())
	t.Setenv("CAIRN_SESSION_ID", "test-cli-conflict-after-whole")
	recordID := "11111111-1111-4111-8111-111111111111"
	competingID := "22222222-2222-4222-8222-222222222222"
	var hasConflict atomic.Bool
	var expandCalls atomic.Int32
	args := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1/version":
			json.NewEncoder(w).Encode(map[string]any{
				"schema": "cairn.response/1",
				"ok":     true,
				"data":   map[string]any{"preview_capabilities": map[string]any{"schema": "cairn.preview-capabilities/1", "entities_omitted": true}},
			})
		case "/v1/index":
			receiptID := uuid.NewString()
			handleID := uuid.NewString()
			entry := core.IndexEntry{
				RecordID:   recordID,
				Version:    1,
				Class:      "A",
				Kind:       "note",
				Summary:    "summary",
				BodySHA256: strings.Repeat("a", 64),
			}
			if hasConflict.Load() {
				entry.Conflicts = []core.AdvisoryConflict{{
					ID:      uuid.NewString(),
					Version: 1,
					Members: []core.RecordVersionRef{{RecordID: recordID, Version: 1}, {RecordID: competingID, Version: 1}},
				}}
			}
			res := core.IndexResult{
				Package: core.Package{
					ReceiptID: receiptID,
					Seal:      "seal",
					Semantic: core.SemanticPackage{
						Schema:          "cairn.semantic/19",
						Mode:            "index",
						Status:          "READY",
						Scope:           core.Scope{Repo: "repo", TaskID: "task", RunID: "run"},
						Query:           "sha256:q",
						Purpose:         "context",
						Destination:     core.Destination{Name: "hosted"},
						Policy:          "local-loop/1",
						Ranking:         "lexical-scope-recency/3",
						Tokenizer:       "utf8-byte-upper-bound/1",
						AvailableTokens: 32000,
						OptionalLimit:   3200,
						Index:           []core.IndexEntry{entry},
					},
				},
				Handles:          []core.IndexHandle{{RecordID: recordID, Version: 1, Handle: handleID}},
				ExpiresAt:        time.Now().Add(time.Minute).UTC(),
				CreditsRemaining: 4,
				BytesRemaining:   24000,
			}
			json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": res})
		case "/v1/expand":
			expandCalls.Add(1)
			var competing []core.Selection
			if hasConflict.Load() {
				competing = []core.Selection{{
					Record: core.Record{RecordID: competingID, Version: 1, Class: "A", Draft: core.Draft{Kind: "note", Body: "competing"}},
				}}
			}
			json.NewEncoder(w).Encode(map[string]any{
				"schema": "cairn.response/1",
				"ok":     true,
				"status": "OK",
				"data": core.Expansion{
					Competing: competing,
					Selection: core.Selection{
						Record: core.Record{RecordID: recordID, Version: 1, Class: "A", Draft: core.Draft{Kind: "note", Body: "body"}},
					},
					CreditsRemaining: 3,
					BytesRemaining:   20000,
				},
			})
		default:
			t.Fatalf("unexpected path %s", r.URL.Path)
		}
	})

	searchAndPull := func(reqID string) any {
		t.Helper()
		res, err := run(context.Background(), append(append([]string{}, args...), "search", "--repo", "repo", "--task", "task", "--run", "run", "q"), strings.NewReader(""))
		if err != nil {
			t.Fatalf("search failed: %v", err)
		}
		view := res.(agentSearchView)
		pa := view.Index[0].PullArguments
		out, err := run(context.Background(), append(append([]string{}, args...), "pull", "--request-id", reqID, pa.ReceiptID, pa.Handle), strings.NewReader(""))
		if err != nil {
			t.Fatalf("pull failed: %v", err)
		}
		return out
	}

	// Whole pull v1 without conflicts -> hits /v1/expand
	searchAndPull("r1")
	if expandCalls.Load() != 1 {
		t.Fatalf("expected 1 expand call, got %d", expandCalls.Load())
	}

	// Repeat whole pull without conflicts -> deduped
	out2 := searchAndPull("r2")
	var hit2 map[string]any
	if err := json.Unmarshal(out2.(json.RawMessage), &hit2); err != nil || hit2["already_delivered"] != true || expandCalls.Load() != 1 {
		t.Fatalf("expected dedup hit, got %s calls=%d err=%v", out2, expandCalls.Load(), err)
	}

	// Same v1 gains conflict -> next search + whole pull must miss dedup and return competing
	hasConflict.Store(true)
	out3 := searchAndPull("r3")
	var exp3 map[string]any
	if err := json.Unmarshal(out3.(json.RawMessage), &exp3); err != nil {
		t.Fatal(err)
	}
	if exp3["already_delivered"] == true || expandCalls.Load() != 2 {
		t.Fatalf("newly-formed conflict on same v1 must bypass dedup: %s calls=%d", out3, expandCalls.Load())
	}
	if comp, ok := exp3["competing"].([]any); !ok || len(comp) != 1 {
		t.Fatalf("expected competing entry in CLI expansion, got %s", out3)
	}

	// Subsequent whole pull after conflict delivery dedups cleanly
	out4 := searchAndPull("r4")
	var hit4 map[string]any
	if err := json.Unmarshal(out4.(json.RawMessage), &hit4); err != nil || hit4["already_delivered"] != true || expandCalls.Load() != 2 {
		t.Fatalf("expected dedup after conflict delivery, got %s calls=%d err=%v", out4, expandCalls.Load(), err)
	}
}

func TestAgentPullWriteOperationsInvalidateDedup(t *testing.T) {
	t.Setenv("XDG_CACHE_HOME", t.TempDir())
	t.Setenv("CAIRN_SESSION_ID", "test-cli-write-invalidation")
	recordID := "11111111-1111-4111-8111-111111111111"
	var version atomic.Int32
	version.Store(1)
	var expandCalls atomic.Int32
	var failMutation atomic.Bool

	args := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1/expand":
			expandCalls.Add(1)
			var req core.ExpandRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				t.Error(err)
			}
			if req.Handle != fmt.Sprintf("handle-v%d", version.Load()) {
				w.WriteHeader(http.StatusConflict)
				json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": false, "status": "STALE_HANDLE", "message": "stale handle"})
				return
			}
			json.NewEncoder(w).Encode(map[string]any{
				"schema": "cairn.response/1",
				"ok":     true,
				"status": "OK",
				"data": core.Expansion{
					Selection: core.Selection{
						Record: core.Record{RecordID: recordID, Version: int(version.Load()), Class: "A", Draft: core.Draft{Kind: "note", Body: "body"}},
					},
					CreditsRemaining: 3,
					BytesRemaining:   20000,
				},
			})
		case "/v1/revise", "/v1/edit", "/v1/replace", "/v1/append", "/v1/set-sections", "/v1/cite", "/v1/delete", "/v1/supersede":
			if failMutation.Load() {
				w.WriteHeader(http.StatusConflict)
				json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": false, "status": "VERSION_CONFLICT", "message": "conflict"})
				return
			}
			newVer := int(version.Add(1))
			json.NewEncoder(w).Encode(map[string]any{
				"schema": "cairn.response/1",
				"ok":     true,
				"status": "OK",
				"data":   map[string]any{"record_id": recordID, "version": newVer},
			})
		default:
			t.Fatalf("unexpected path %s", r.URL.Path)
		}
	})

	// 1. Pull v1 with handle-v1 -> records delivered + handle
	_, err := run(context.Background(), append(append([]string{}, args...), "pull", "--request-id", "p1", "receipt-1", "handle-v1"), strings.NewReader(""))
	if err != nil || expandCalls.Load() != 1 {
		t.Fatalf("initial pull failed: %v calls=%d", err, expandCalls.Load())
	}

	// 2. Failed revise (VERSION_CONFLICT) must preserve dedup
	failMutation.Store(true)
	_, err = run(context.Background(), append(append([]string{}, args...), "revise"), strings.NewReader(fmt.Sprintf(`{"request_id":"m-fail","record_id":%q,"expected_version":1,"repo":"repo","body":"new"}`, recordID)))
	if core.Code(err) != "VERSION_CONFLICT" {
		t.Fatalf("expected VERSION_CONFLICT, got %v", err)
	}
	res, err := run(context.Background(), append(append([]string{}, args...), "pull", "--request-id", "p2", "receipt-1", "handle-v1"), strings.NewReader(""))
	if err != nil || expandCalls.Load() != 1 {
		t.Fatalf("failed mutation should preserve dedup: err=%v calls=%d", err, expandCalls.Load())
	}
	var hit map[string]any
	if err := json.Unmarshal(res.(json.RawMessage), &hit); err != nil || hit["already_delivered"] != true {
		t.Fatalf("expected already_delivered after failed revise, got %s", res)
	}

	// 3. Each successful CLI write operation invalidates dedup so stale handle-v1 hits /v1/expand -> STALE_HANDLE
	failMutation.Store(false)
	mutations := []struct {
		op   string
		body string
	}{
		{"revise", fmt.Sprintf(`{"request_id":"m1","record_id":%q,"expected_version":1,"repo":"repo","body":"v2"}`, recordID)},
		{"edit", fmt.Sprintf(`{"request_id":"m2","record_id":%q,"expected_version":2,"draft":{"scope":{"repo":"repo","task_id":"t","run_id":"r"},"kind":"note","body":"v3"}}`, recordID)},
		{"replace", fmt.Sprintf(`{"request_id":"m3","record_id":%q,"expected_version":3,"repo":"repo","old_text":"a","new_text":"b"}`, recordID)},
		{"append", fmt.Sprintf(`{"request_id":"m4","record_id":%q,"expected_version":4,"repo":"repo","body":"more"}`, recordID)},
		{"set-sections", fmt.Sprintf(`{"request_id":"m5","record_id":%q,"expected_version":5,"repo":"repo","sections":{}}`, recordID)},
		{"cite", fmt.Sprintf(`{"request_id":"m6","record_id":%q,"expected_version":6,"repo":"repo","evidence_citations":[]}`, recordID)},
		{"delete", fmt.Sprintf(`{"request_id":"m7","record_id":%q,"expected_version":7}`, recordID)},
		{"supersede", fmt.Sprintf(`{"request_id":"m8","record_id":%q,"expected_version":8,"replacement":{"record_id":"22222222-2222-4222-8222-222222222222","version":1},"preview_id":"p","reason":"superseded"}`, recordID)},
	}
	for i, m := range mutations {
		handle := fmt.Sprintf("handle-v%d", i+1)
		if i > 0 {
			// Pull current version using a fresh handle to re-seed dedup before next mutation
			_, err := run(context.Background(), append(append([]string{}, args...), "pull", "--request-id", fmt.Sprintf("reseed-%d", i), "receipt-1", handle), strings.NewReader(""))
			if err != nil {
				t.Fatalf("reseed pull %d failed: %v", i, err)
			}
		}
		_, err := run(context.Background(), append(append([]string{}, args...), m.op), strings.NewReader(m.body))
		if err != nil {
			t.Fatalf("%s failed: %v", m.op, err)
		}
		// Pre-mutation handle must bypass dedup and reach /v1/expand (receiving STALE_HANDLE)
		beforeCalls := expandCalls.Load()
		_, err = run(context.Background(), append(append([]string{}, args...), "pull", "--request-id", fmt.Sprintf("stale-%d", i), "receipt-1", handle), strings.NewReader(""))
		if core.Code(err) != "STALE_HANDLE" || expandCalls.Load() != beforeCalls+1 {
			t.Fatalf("after %s, expected stale %s to hit /v1/expand with STALE_HANDLE; got err=%v delta=%d", m.op, handle, err, expandCalls.Load()-beforeCalls)
		}
	}
}

func TestAgentPullContiguousSpanMergeAndOpeningBytesPolicy(t *testing.T) {
	t.Setenv("XDG_CACHE_HOME", t.TempDir())
	t.Setenv("CAIRN_SESSION_ID", "test-cli-span-merge-opening")
	var expandCalls atomic.Int32
	args := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/expand" {
			t.Fatalf("unexpected path %s", r.URL.Path)
		}
		expandCalls.Add(1)
		var req core.ExpandRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Error(err)
		}
		json.NewEncoder(w).Encode(map[string]any{
			"schema": "cairn.response/1",
			"ok":     true,
			"status": "OK",
			"data": core.Expansion{
				Selection: core.Selection{
					Record: core.Record{RecordID: "11111111-1111-4111-8111-111111111111", Version: 1, Class: "A", Draft: core.Draft{Kind: "note"}},
				},
				Span:             &core.NoteSpan{ByteSpan: core.ByteSpan{Offset: req.Span.Offset, End: req.Span.Offset + req.Span.Length, TotalBytes: 1000, Body: "part"}},
				CreditsRemaining: 3,
				BytesRemaining:   20000,
			},
		})
	})

	// 1. Contiguous spans [0, 50) + [50, 100) merge so [25, 60) hits CLI dedup
	for i, span := range [][2]int{{0, 50}, {50, 50}} {
		_, err := run(context.Background(), append(append([]string{}, args...), "pull", "--request-id", fmt.Sprintf("m-%d", i), "--offset", fmt.Sprintf("%d", span[0]), "--length", fmt.Sprintf("%d", span[1]), "receipt-1", "handle-1"), strings.NewReader(""))
		if err != nil {
			t.Fatalf("pull %d failed: %v", i, err)
		}
	}
	if expandCalls.Load() != 2 {
		t.Fatalf("expected 2 expand calls, got %d", expandCalls.Load())
	}
	res, err := run(context.Background(), append(append([]string{}, args...), "pull", "--request-id", "m-hit", "--offset", "25", "--length", "35", "receipt-1", "handle-1"), strings.NewReader(""))
	if err != nil || expandCalls.Load() != 2 {
		t.Fatalf("merged contiguous span [25,60) should dedup: err=%v calls=%d", err, expandCalls.Load())
	}
	var hit map[string]any
	if err := json.Unmarshal(res.(json.RawMessage), &hit); err != nil || hit["already_delivered"] != true {
		t.Fatalf("expected already_delivered=true, got %s", res)
	}

	// 2. Overlapping [90, 140) merges into [0, 140) so [30, 110) hits CLI dedup
	_, err = run(context.Background(), append(append([]string{}, args...), "pull", "--request-id", "m-overlap", "--offset", "90", "--length", "50", "receipt-1", "handle-1"), strings.NewReader(""))
	if err != nil || expandCalls.Load() != 3 {
		t.Fatalf("overlap pull failed: err=%v calls=%d", err, expandCalls.Load())
	}
	res, err = run(context.Background(), append(append([]string{}, args...), "pull", "--request-id", "m-hit2", "--offset", "30", "--length", "80", "receipt-1", "handle-1"), strings.NewReader(""))
	if err != nil || expandCalls.Load() != 3 {
		t.Fatalf("merged overlapping span [30,110) should dedup: err=%v calls=%d", err, expandCalls.Load())
	}

	// 3. OpeningBytes == 1 or 64 must NOT cover OpeningBytes == 0 (receipt policy up to 96)
	_, err = run(context.Background(), append(append([]string{}, args...), "pull", "--request-id", "ob1", "--offset", "200", "--length", "100", "--opening-bytes", "64", "receipt-1", "handle-1"), strings.NewReader(""))
	if err != nil || expandCalls.Load() != 4 {
		t.Fatalf("ob=64 pull failed: err=%v calls=%d", err, expandCalls.Load())
	}
	_, err = run(context.Background(), append(append([]string{}, args...), "pull", "--request-id", "ob0-miss", "--offset", "200", "--length", "100", "receipt-1", "handle-1"), strings.NewReader(""))
	if err != nil || expandCalls.Load() != 5 {
		t.Fatalf("opening_bytes=0 after opening_bytes=64 must miss: err=%v calls=%d", err, expandCalls.Load())
	}

	// 4. OpeningBytes == 96 DOES cover OpeningBytes == 0
	_, err = run(context.Background(), append(append([]string{}, args...), "pull", "--request-id", "ob96", "--offset", "400", "--length", "100", "--opening-bytes", "96", "receipt-1", "handle-1"), strings.NewReader(""))
	if err != nil || expandCalls.Load() != 6 {
		t.Fatalf("ob=96 pull failed: err=%v calls=%d", err, expandCalls.Load())
	}
	res, err = run(context.Background(), append(append([]string{}, args...), "pull", "--request-id", "ob0-hit", "--offset", "400", "--length", "50", "receipt-1", "handle-1"), strings.NewReader(""))
	if err != nil || expandCalls.Load() != 6 {
		t.Fatalf("opening_bytes=0 after opening_bytes=96 should dedup: err=%v calls=%d", err, expandCalls.Load())
	}
	hit = nil
	if err := json.Unmarshal(res.(json.RawMessage), &hit); err != nil || hit["already_delivered"] != true {
		t.Fatalf("expected already_delivered=true after ob=96, got %s", res)
	}
}

func TestCLIPullDedupPruneLRUCapAndReceiptScopes(t *testing.T) {
	cacheDir := t.TempDir()
	t.Setenv("XDG_CACHE_HOME", cacheDir)
	t.Setenv("CAIRN_SESSION_ID", "test-cli-prune-lru")
	socketPath := "/tmp/fake-prune.sock"
	tokenPath := "/tmp/fake-prune.token"
	scope := core.Scope{Repo: "repo", TaskID: "task", RunID: "run"}

	// Seed 256 delivered records + 1025 handles across distinct receipts so handle cap evicts handle-0 and its ReceiptScope.
	// Also test LRU: touch record 0 before adding record 256 so record 1 is evicted instead of record 0.
	now := time.Now().Unix()
	withCLIPullDedupLock(nil, socketPath, tokenPath, func(state *cliPullDedupState, _ int64) bool {
		state.ReceiptScopes = make(map[string]string)
		for i := 0; i <= cliPullDedupMaxHandles; i++ {
			receiptID := fmt.Sprintf("receipt-%04d", i)
			state.ReceiptScopes[receiptID] = scopeKeyForCLI(scope)
			state.Handles = append(state.Handles, cliPullHandle{
				ReceiptID: receiptID,
				Handle:    fmt.Sprintf("handle-%04d", i),
				RecordID:  fmt.Sprintf("rec-%04d", i),
				Version:   1,
				Scope:     scopeKeyForCLI(scope),
				At:        now,
			})
		}
		for i := 0; i < cliPullDedupMaxRecords; i++ {
			state.Delivered = append(state.Delivered, cliPullDelivered{
				Scope:        scopeKeyForCLI(scope),
				RecordID:     fmt.Sprintf("rec-%04d", i),
				WholeVersion: 1,
				At:           now,
			})
		}
		pruneCLIPullDedup(state, now)
		return true
	})

	// Touch rec-0001 via checkCLIPullDedup (handle-0000 was evicted, handle-0001 survived)
	hit, ok := checkCLIPullDedup(nil, socketPath, tokenPath, core.ExpandRequest{
		RequestID: "touch-1",
		ReceiptID: "receipt-0001",
		Handle:    "handle-0001",
	})
	if !ok || !hit.AlreadyDelivered {
		t.Fatalf("expected rec-0001 to hit before overflow, got ok=%v hit=%+v", ok, hit)
	}

	// Add rec-0256 -> evicts LRU rec-0000, keeping touched rec-0001
	rawExp, err := json.Marshal(core.Expansion{
		Selection: core.Selection{Record: core.Record{RecordID: "rec-0256", Version: 1, Class: "A", Draft: core.Draft{Kind: "note", Body: "x"}}},
	})
	if err != nil {
		t.Fatal(err)
	}
	recordCLIPullResult(nil, socketPath, tokenPath, core.ExpandRequest{
		RequestID: "add-256",
		ReceiptID: "receipt-0256",
		Handle:    "handle-0256",
	}, rawExp)

	path, err := cliPullDedupPath(nil, socketPath, tokenPath)
	if err != nil {
		t.Fatal(err)
	}
	rawState, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var state cliPullDedupState
	if err := json.Unmarshal(rawState, &state); err != nil {
		t.Fatal(err)
	}
	if len(state.Handles) != cliPullDedupMaxHandles {
		t.Fatalf("expected %d handles after cap, got %d", cliPullDedupMaxHandles, len(state.Handles))
	}
	if _, exists := state.ReceiptScopes["receipt-0000"]; exists {
		t.Fatalf("expected evicted receipt-0000 to be pruned from ReceiptScopes")
	}
	if len(state.ReceiptScopes) != cliPullDedupMaxHandles {
		t.Fatalf("expected ReceiptScopes len=%d matching surviving handles, got %d", cliPullDedupMaxHandles, len(state.ReceiptScopes))
	}
	if len(state.Delivered) != cliPullDedupMaxRecords {
		t.Fatalf("expected %d delivered records after cap, got %d", cliPullDedupMaxRecords, len(state.Delivered))
	}
	hasRec0, hasRec1, hasRec256 := false, false, false
	for _, d := range state.Delivered {
		switch d.RecordID {
		case "rec-0000":
			hasRec0 = true
		case "rec-0001":
			hasRec1 = true
		case "rec-0256":
			hasRec256 = true
		}
	}
	if hasRec0 || !hasRec1 || !hasRec256 {
		t.Fatalf("LRU eviction mismatch: hasRec0=%v hasRec1=%v hasRec256=%v", hasRec0, hasRec1, hasRec256)
	}

	// TTL expiry prunes both expired handles and their ReceiptScopes
	state.Handles = append(state.Handles, cliPullHandle{
		ReceiptID: "receipt-expired",
		Handle:    "handle-expired",
		RecordID:  "rec-expired",
		Version:   1,
		At:        now - cliPullDedupTTLSeconds - 5,
	})
	state.ReceiptScopes["receipt-expired"] = scopeKeyForCLI(scope)
	pruneCLIPullDedup(&state, now)
	if _, exists := state.ReceiptScopes["receipt-expired"]; exists {
		t.Fatalf("expected expired receipt-expired to be pruned from ReceiptScopes")
	}
}
