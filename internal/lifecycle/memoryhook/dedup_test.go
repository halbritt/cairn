package memoryhook

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/indexview"
	"github.com/halbritt/cairn/internal/lifecycle/memoryclient"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/localapi"
)

func testDecodeValue(t *testing.T, v any) *value.Value {
	t.Helper()
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := value.DecodeText(raw, "utf-8", 0)
	if err != nil {
		t.Fatal(err)
	}
	return parsed
}

func startDedupTestServer(t *testing.T, handler func(op string, req map[string]any) (int, map[string]any)) (*memoryclient.Client, func()) {
	t.Helper()
	dir := t.TempDir()
	tok := filepath.Join(dir, "token")
	sock := filepath.Join(dir, "api.sock")
	if err := os.WriteFile(tok, []byte("dedup-test-token"), 0600); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("unix", sock)
	if err != nil {
		t.Fatal(err)
	}
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer dedup-test-token" {
			w.WriteHeader(401)
			return
		}
		req := map[string]any{}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(400)
			return
		}
		op := strings.TrimPrefix(r.URL.Path, "/v1/")
		if op == "version" {
			_ = json.NewEncoder(w).Encode(map[string]any{
				"schema": "cairn.response/1",
				"ok":     true,
				"status": "OK",
				"data": map[string]any{
					"preview_capabilities": map[string]any{"schema": "cairn.preview-capabilities/1", "entities_omitted": true},
				},
			})
			return
		}
		statusCode, payload := handler(op, req)
		if statusCode >= 400 {
			w.WriteHeader(statusCode)
		}
		_ = json.NewEncoder(w).Encode(payload)
	})}
	done := make(chan struct{})
	go func() {
		defer close(done)
		_ = server.Serve(listener)
	}()
	api, err := localapi.NewClient(sock, tok)
	if err != nil {
		_ = server.Close()
		<-done
		t.Fatal(err)
	}
	client, err := memoryclient.New(api, memoryclient.Options{
		Scope: core.Scope{Repo: "fixture", TaskID: "t1", RunID: "r1"},
	})
	if err != nil {
		api.Close()
		_ = server.Close()
		<-done
		t.Fatal(err)
	}
	cleanup := func() {
		api.Close()
		_ = server.Close()
		<-done
	}
	return client, cleanup
}

func TestEagerCandidatesDedupNoBackfillPartialSpanAndOptOut(t *testing.T) {
	recIDs := []string{
		"11111111-1111-4111-8111-111111111111",
		"22222222-2222-4222-8222-222222222222",
		"33333333-3333-4333-8333-333333333333",
		"44444444-4444-4444-8444-444444444444",
	}
	bodies := map[string]string{
		recIDs[0]: "Body zero guidance.",
		recIDs[1]: "Body one guidance.",
		recIDs[2]: "Body two guidance.",
		recIDs[3]: "Body three guidance.",
	}
	versions := map[string]int{recIDs[0]: 1, recIDs[1]: 1, recIDs[2]: 1, recIDs[3]: 1}
	var mu sync.Mutex
	expands := []string{}
	client, cleanup := startDedupTestServer(t, func(op string, req map[string]any) (int, map[string]any) {
		mu.Lock()
		defer mu.Unlock()
		switch op {
		case "index":
			idx := make([]any, 0, len(recIDs))
			handles := make([]any, 0, len(recIDs))
			for _, id := range recIDs {
				b := bodies[id]
				v := versions[id]
				idx = append(idx, map[string]any{
					"record_id":    id,
					"version":      v,
					"class":        "A",
					"kind":         "note",
					"summary":      "Summary " + id[:4],
					"body_sha256":  hashText(b),
					"summary_span": map[string]any{"offset": 0, "length": 10},
				})
				handles = append(handles, map[string]any{"record_id": id, "version": v, "handle": id})
			}
			return 200, map[string]any{
				"schema": "cairn.response/1",
				"ok":     true,
				"status": "OK",
				"data": map[string]any{
					"package": map[string]any{
						"receipt_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
						"seal":       "blake3:" + strings.Repeat("b", 64),
						"semantic": map[string]any{
							"schema":           "cairn.semantic/17",
							"mode":             "index",
							"status":           "READY",
							"scope":            req["scope"],
							"purpose":          "context",
							"destination":      map[string]any{"name": "hosted", "allow_local": false},
							"query":            "sha256:q",
							"available_tokens": 32000,
							"selected":         []any{},
							"omitted":          map[string]any{},
							"index":            idx,
						},
					},
					"handles":           handles,
					"expires_at":        "2030-01-01T00:00:00Z",
					"credits_remaining": 4,
					"bytes_remaining":   24000,
				},
			}
		case "expand":
			id := req["handle"].(string)
			expands = append(expands, id)
			return 200, map[string]any{
				"schema": "cairn.response/1",
				"ok":     true,
				"status": "OK",
				"data": map[string]any{
					"selection": map[string]any{
						"record": map[string]any{
							"record_id": id,
							"version":   versions[id],
							"class":     "A",
							"kind":      "note",
							"body":      bodies[id],
						},
						"mandatory": false,
						"reason":    "candidate",
						"evidence":  []any{},
						"authority": []any{},
						"conflicts": []any{},
					},
					"credits_remaining": 3,
					"bytes_remaining":   20000,
				},
			}
		default:
			return 400, map[string]any{"ok": false}
		}
	})
	defer cleanup()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	view, err := client.Search(ctx, "fixture", memoryclient.SearchOptions{})
	if err != nil {
		t.Fatal(err)
	}
	result, err := decoded(view)
	if err != nil {
		t.Fatal(err)
	}
	config := testDecodeValue(t, map[string]any{"context_bytes": 9500})
	state := testDecodeValue(t, map[string]any{"seen": map[string]any{}})
	measure := func(s string) int { return len(s) }
	status1 := value.Mapping(value.Field("rejected", value.Mapping()))

	// Turn 1: pulls top 2 and records both in seen.
	text1, err := eagerCandidatesWithSeen(ctx, client, config, state, map[string][]rune{}, result.Get("selected"), result, view.Index, 9500, status1, measure)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(text1, bodies[recIDs[0]]) || !strings.Contains(text1, bodies[recIDs[1]]) {
		t.Fatalf("expected top 2 bodies delivered on turn 1, got %q", text1)
	}
	mu.Lock()
	if len(expands) != 2 {
		t.Fatalf("expected 2 expands on turn 1, got %v", expands)
	}
	expands = nil
	mu.Unlock()
	if got := state.Get("seen").Get(recIDs[0]); got == nil || number(got) != 1 {
		t.Fatalf("expected rec0 seen=1, got %#v", got)
	}
	if got := state.Get("seen").Get(recIDs[1]); got == nil || number(got) != 1 {
		t.Fatalf("expected rec1 seen=1, got %#v", got)
	}

	// Turn 2: top 2 are already seen -> 0 expands, slotCount prevents backfilling rec2/rec3,
	// and preview cap counts skipped slots so rec3 (4th slot) is trimmed from preview.
	status2 := value.Mapping(value.Field("rejected", value.Mapping()))
	text2, err := eagerCandidatesWithSeen(ctx, client, config, state, map[string][]rune{}, result.Get("selected"), result, view.Index, 9500, status2, measure)
	if err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	if len(expands) != 0 {
		t.Fatalf("expected 0 expands on turn 2 dedup hit, got %v", expands)
	}
	mu.Unlock()
	if strings.Contains(text2, bodies[recIDs[2]]) || strings.Contains(text2, bodies[recIDs[3]]) {
		t.Fatalf("slotCount backfill occurred on turn 2: %s", text2)
	}
	if strings.Contains(text2, "Summary 4444") {
		t.Fatalf("preview cap backfilled 4th entry past 3-slot limit: %s", text2)
	}

	// Turn 3: version bump on rec0 invalidates seen for rec0 only.
	versions[recIDs[0]] = 2
	view3, err := client.Search(ctx, "fixture", memoryclient.SearchOptions{})
	if err != nil {
		t.Fatal(err)
	}
	result3, err := decoded(view3)
	if err != nil {
		t.Fatal(err)
	}
	status3 := value.Mapping(value.Field("rejected", value.Mapping()))
	_, err = eagerCandidatesWithSeen(ctx, client, config, state, map[string][]rune{}, result3.Get("selected"), result3, view3.Index, 9500, status3, measure)
	if err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	if len(expands) != 1 || expands[0] != recIDs[0] {
		t.Fatalf("expected only bumped rec0 expand, got %v", expands)
	}
	expands = nil
	mu.Unlock()
	if got := state.Get("seen").Get(recIDs[0]); got == nil || number(got) != 2 {
		t.Fatalf("expected rec0 seen=2 after version bump, got %#v", got)
	}

	// Turn 4: CAIRN_RECALL_DEDUP=0 disables dedup and pulls top 2 again.
	status4 := value.Mapping(value.Field("rejected", value.Mapping()))
	_, err = eagerCandidatesWithSeen(ctx, client, config, state, map[string][]rune{"CAIRN_RECALL_DEDUP": []rune("0")}, result3.Get("selected"), result3, view3.Index, 9500, status4, measure)
	if err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	if len(expands) != 2 {
		t.Fatalf("expected 2 expands when CAIRN_RECALL_DEDUP=0, got %v", expands)
	}
	mu.Unlock()
}

func TestPartialSpanSeenAndEOFClipping(t *testing.T) {
	state := testDecodeValue(t, map[string]any{"seen": map[string]any{}})
	recID := "55555555-5555-4555-8555-555555555555"
	entryV1 := testDecodeValue(t, map[string]any{"record_id": recID, "version": 1})
	entryV2 := testDecodeValue(t, map[string]any{"record_id": recID, "version": 2})

	// Record a clipped EOF span [10, 25) when total_bytes=25
	markSeenSpan(state, recID, integer(1), 10, 25, 25, 100.0)
	if !seenCoversSpan(state.Get("seen"), entryV1, &core.ByteSpanRequest{Offset: 10, Length: 50}) {
		t.Fatal("expected exact/longer EOF span to be covered when eof=true")
	}
	if !seenCoversSpan(state.Get("seen"), entryV1, &core.ByteSpanRequest{Offset: 12, Length: 8}) {
		t.Fatal("expected sub-span [12, 20) to be covered")
	}
	if seenCoversSpan(state.Get("seen"), entryV1, &core.ByteSpanRequest{Offset: 0, Length: 10}) {
		t.Fatal("expected disjoint span [0, 10) not to be covered")
	}
	if seenCoversSpan(state.Get("seen"), entryV2, &core.ByteSpanRequest{Offset: 10, Length: 15}) {
		t.Fatal("expected version 2 not to be covered by version 1")
	}
	// Merging adjacent span [0, 10) produces single [0, 25) eof=true entry
	markSeenSpan(state, recID, integer(1), 0, 10, 25, 101.0)
	spans := state.Get("seen").Get(recID).Get("spans").Items
	if len(spans) != 1 || number(spans[0].Get("offset")) != 0 || number(spans[0].Get("end")) != 25 || !value.Truth(spans[0].Get("eof")) {
		t.Fatalf("unexpected merged spans: %#v", spans)
	}
}

func TestCaptureCacheHitInvalidationLRUEvictionAndOptOut(t *testing.T) {
	state := value.Mapping()
	body := "Cached durable fact body."
	entry := indexview.Entry{
		IndexEntry: core.IndexEntry{
			RecordID:   "66666666-6666-4666-8666-666666666666",
			Version:    1,
			Class:      "A",
			Kind:       "decision",
			BodySHA256: hashText(body),
		},
	}
	rec := testDecodeValue(t, map[string]any{
		"record_id": entry.RecordID,
		"version":   1,
		"class":     "A",
		"kind":      "decision",
		"body":      body,
	})
	captureCacheStore(state, entry, rec, true)
	gotRec, _, ok := captureCacheLookup(state, entry, true)
	if !ok || gotRec == nil || text(gotRec.Get("body")) != body {
		t.Fatalf("expected capture cache hit, got ok=%v rec=%#v", ok, gotRec)
	}
	bumped := entry
	bumped.Version = 2
	if _, _, ok := captureCacheLookup(state, bumped, true); ok {
		t.Fatal("expected miss on version bump")
	}
	shaChanged := entry
	shaChanged.BodySHA256 = hashText("different body")
	if _, _, ok := captureCacheLookup(state, shaChanged, true); ok {
		t.Fatal("expected miss on sha mismatch")
	}
	if _, _, ok := captureCacheLookup(state, entry, false); ok {
		t.Fatal("expected miss when dedup disabled")
	}
	for i := 0; i < 35; i++ {
		b := fmt.Sprintf("entry-%02d", i)
		e := indexview.Entry{
			IndexEntry: core.IndexEntry{
				RecordID:   fmt.Sprintf("00000000-0000-4000-8000-%012d", i),
				Version:    1,
				Class:      "A",
				Kind:       "note",
				BodySHA256: hashText(b),
			},
		}
		r := testDecodeValue(t, map[string]any{"record_id": e.RecordID, "version": 1, "class": "A", "kind": "note", "body": b})
		captureCacheStore(state, e, r, true)
	}
	cache := state.Get("capture_cache")
	if cache == nil || len(cache.Members) > 32 {
		t.Fatalf("expected <= 32 entries in capture_cache, got %d", len(cache.Members))
	}
	for _, m := range cache.Members {
		if m.Value.Get("at") != nil {
			t.Fatalf("capture_cache entry %s unexpectedly stored wall-clock 'at'", string(m.Key))
		}
	}
}

func TestPrepareSeenStateCompactionEpochAndBoundedTTL(t *testing.T) {
	config := value.Mapping()
	state := testDecodeValue(t, map[string]any{
		"seen": map[string]any{"rec-1": 1},
		"seen_meta": map[string]any{
			"epoch":   0,
			"turn":    1,
			"records": map[string]any{"rec-1": map[string]any{"turn": 1, "at": 100.0}},
		},
		"compaction_epoch": 1,
	})
	var err error
	if state, err = prepareSeenState(state, config, testDecodeValue(t, map[string]any{"hook_event_name": "UserPromptSubmit"}), map[string][]rune{}, 200.0); err != nil {
		t.Fatal(err)
	}
	if len(state.Get("seen").Members) != 0 || state.Get("seen_meta") != nil {
		t.Fatalf("expected epoch mismatch to clear seen and drop seen_meta, got seen=%#v seen_meta=%#v", state.Get("seen"), state.Get("seen_meta"))
	}

	state2 := testDecodeValue(t, map[string]any{
		"seen": map[string]any{"rec-old": 1, "rec-fresh": 1},
		"seen_meta": map[string]any{
			"epoch": 0,
			"turn":  5,
			"records": map[string]any{
				"rec-old":   map[string]any{"turn": 5, "at": 100.0},
				"rec-fresh": map[string]any{"turn": 5, "at": 980.0},
			},
		},
	})
	env := map[string][]rune{"CAIRN_SEEN_TTL_SECONDS": []rune("60"), "CAIRN_SEEN_TTL_TURNS": []rune("32")}
	if state2, err = prepareSeenState(state2, config, testDecodeValue(t, map[string]any{"hook_event_name": "UserPromptSubmit"}), env, 1000.0); err != nil {
		t.Fatal(err)
	}
	if state2.Get("seen").Get("rec-old") != nil || state2.Get("seen").Get("rec-fresh") == nil {
		t.Fatalf("unexpected TTL eviction result: %#v", state2.Get("seen"))
	}
}
