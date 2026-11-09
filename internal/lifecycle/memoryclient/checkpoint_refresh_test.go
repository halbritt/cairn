package memoryclient

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/buildinfo"
	"github.com/halbritt/cairn/internal/indexview"
	"github.com/halbritt/cairn/localapi"
)

const refreshTestTitle = "Handoff: p / T"
const refreshTestBody = "Handoff: p / T\n\nbody"
const refreshTestReceipt = "11111111-1111-4111-8111-111111111111"
const refreshTestRecord = "22222222-2222-4222-8222-222222222222"
const refreshTestRequest = "44444444-4444-4444-8444-444444444444"

var refreshTestScope = core.Scope{Repo: "refresh-fixture", TaskID: "fixture-task", RunID: "fixture-run"}

type refreshTestCall struct {
	operation string
	header    http.Header
	body      []byte
}

type refreshTestLog struct {
	mu    sync.Mutex
	calls []refreshTestCall
}

func (l *refreshTestLog) snapshot() []refreshTestCall {
	l.mu.Lock()
	defer l.mu.Unlock()
	return append([]refreshTestCall(nil), l.calls...)
}

func refreshTestAPI(t *testing.T, respond func(http.ResponseWriter, *http.Request, refreshTestCall)) (*localapi.Client, *refreshTestLog) {
	t.Helper()
	dir, err := os.MkdirTemp("", "checkpoint-refresh-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.RemoveAll(dir); err != nil {
			t.Error(err)
		}
	})
	token := filepath.Join(dir, "token")
	if err := os.WriteFile(token, []byte("synthetic-refresh-token\n"), 0600); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("unix", filepath.Join(dir, "api.sock"))
	if err != nil {
		t.Fatal(err)
	}
	log := new(refreshTestLog)
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
			return
		}
		call := refreshTestCall{strings.TrimPrefix(r.URL.Path, "/v1/"), r.Header.Clone(), body}
		log.mu.Lock()
		log.calls = append(log.calls, call)
		log.mu.Unlock()
		if call.operation == "version" {
			refreshTestSuccess(t, w, map[string]any{"preview_capabilities": localapi.PreviewCapabilities{
				Schema: localapi.PreviewCapabilitiesSchema, EntitiesOmitted: true}})
			return
		}
		respond(w, r, call)
	})}
	finished := make(chan error, 1)
	go func() { finished <- server.Serve(listener) }()
	t.Cleanup(func() {
		if err := server.Close(); err != nil {
			t.Error(err)
		}
		if err := <-finished; !errors.Is(err, http.ErrServerClosed) {
			t.Error(err)
		}
	})
	api, err := localapi.NewClient(filepath.Join(dir, "api.sock"), token)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(api.Close)
	return api, log
}

func refreshTestSuccess(t *testing.T, w http.ResponseWriter, reply any) {
	t.Helper()
	if err := json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": reply}); err != nil {
		t.Error(err)
	}
}

func refreshTestNew(t *testing.T, api *localapi.Client, options Options) *Client {
	t.Helper()
	c, err := New(api, options)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func refreshTestEntry() core.IndexEntry {
	return core.IndexEntry{RecordID: refreshTestRecord, Version: 7, Class: "A", Kind: "note", Summary: refreshTestTitle + "\nsummary"}
}

func refreshTestIndex(offset int, next *int, entries ...core.IndexEntry) core.IndexResult {
	result := core.IndexResult{Package: core.Package{ReceiptID: refreshTestReceipt, Seal: "blake3:" + strings.Repeat("a", 64),
		Semantic: core.SemanticPackage{Schema: "cairn.semantic/17", Mode: "index", Status: "READY", Scope: refreshTestScope,
			Purpose: "context", Destination: core.Destination{Name: "hosted"}, Query: "sha256:fixture-query", AvailableTokens: 16000,
			Selected: []core.Selection{}, Omitted: map[string]int{}, Index: entries, Page: &core.BrowsePage{Offset: offset, NextOffset: next}}},
		CreditsRemaining: 4, BytesRemaining: 24000, ExpiresAt: time.Date(2030, 1, 1, 0, 0, 0, 0, time.UTC)}
	seen := map[string]bool{}
	for _, entry := range entries {
		key := entry.RecordID + ":" + strconv.Itoa(entry.Version)
		if !seen[key] {
			result.Handles = append(result.Handles, core.IndexHandle{RecordID: entry.RecordID, Version: entry.Version, Handle: "handle-" + entry.RecordID})
			seen[key] = true
		}
	}
	return result
}

func refreshTestPull(body string) map[string]any {
	return map[string]any{"selection": map[string]any{"record": map[string]any{
		"record_id": refreshTestRecord, "version": 7, "body": body}}, "credits_remaining": 3}
}

func refreshTestPipeline(t *testing.T, pages []any, pulled any) (*Client, *localapi.Client, *refreshTestLog) {
	t.Helper()
	position := 0
	api, log := refreshTestAPI(t, func(w http.ResponseWriter, _ *http.Request, call refreshTestCall) {
		switch call.operation {
		case "index":
			if position >= len(pages) {
				t.Errorf("unexpected extra index call")
				refreshTestSuccess(t, w, refreshTestIndex(0, nil))
				return
			}
			refreshTestSuccess(t, w, pages[position])
			position++
		case "expand":
			refreshTestSuccess(t, w, pulled)
		default:
			t.Errorf("unexpected operation %q", call.operation)
		}
	})
	return refreshTestNew(t, api, Options{Scope: refreshTestScope}), api, log
}

func refreshTestInput() CheckpointRefreshInput {
	title := refreshTestTitle
	return CheckpointRefreshInput{ProjectBase: "p", Workstream: &title}
}

func refreshTestContext(t *testing.T) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	t.Cleanup(cancel)
	return ctx
}

func refreshTestCue(title string) string {
	return "Current unfinished checkpoint was not restored for " + title + "; search current Cairn memory before relying on earlier context."
}

func refreshTestOutcome(t *testing.T, c *Client, input CheckpointRefreshInput, reason string) CheckpointRefreshResult {
	t.Helper()
	result, err := c.RefreshCheckpoint(refreshTestContext(t), input)
	if err != nil || result.Reason != reason {
		t.Fatalf("refresh outcome: %+v, %v; want %q", result, err, reason)
	}
	if reason == "complete" || reason == "covered_by_required" || reason == "" {
		if result.Text != "" || result.Cause != nil {
			t.Fatalf("unexpected empty-view details: %+v", result)
		}
	} else if reason != "restored" && result.Text != refreshTestCue(*input.Workstream) {
		t.Fatalf("cue mismatch: %q", result.Text)
	}
	return result
}

func refreshTestOperations(t *testing.T, log *refreshTestLog, want ...string) {
	t.Helper()
	var got []string
	for _, call := range log.snapshot() {
		got = append(got, call.operation)
	}
	if len(got) != len(want) {
		t.Fatalf("operations %v; want %v", got, want)
	}
	for i := range got {
		if got[i] != want[i] {
			t.Fatalf("operations %v; want %v", got, want)
		}
	}
}

func refreshTestDecode(t *testing.T, raw []byte) map[string]any {
	t.Helper()
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var fields map[string]any
	if err := d.Decode(&fields); err != nil {
		t.Fatal(err)
	}
	return fields
}

func refreshTestRequestBody(t *testing.T, call refreshTestCall, want any) {
	t.Helper()
	raw, err := json.Marshal(want)
	if err != nil {
		t.Fatal(err)
	}
	fields := refreshTestDecode(t, raw)
	if localapi.Protocol.Min > 1 {
		fields[localapi.BodyProtocolField] = json.Number(strconv.Itoa(localapi.Protocol.Min))
	}
	if !reflect.DeepEqual(refreshTestDecode(t, call.body), fields) {
		t.Fatalf("wire got %s; want %s", call.body, raw)
	}
}

func refreshTestInvalid(t *testing.T, c *Client, ctx context.Context, input CheckpointRefreshInput) {
	t.Helper()
	result, err := c.RefreshCheckpoint(ctx, input)
	var invalid *core.Error
	if !errors.As(err, &invalid) || invalid.Code != "INVALID_REQUEST" ||
		invalid.Message != "checkpoint refresh requires valid Unicode input and an enclosing deadline" ||
		result.Text != "" || result.Reason != "" || result.Cause != nil {
		t.Fatalf("input contract: %+v, %v", result, err)
	}
}

func TestCheckpointRefreshTitleAndRequired(t *testing.T) {
	t.Run("MissingOrInvalidWorkstream", func(t *testing.T) {
		c, _, log := refreshTestPipeline(t, nil, nil)
		for _, title := range []*string{nil, new(string)} {
			input := CheckpointRefreshInput{Workstream: title}
			if title == nil {
				input.ProjectBase = "\xff"
			}
			result, err := c.RefreshCheckpoint(nil, input)
			if err != nil || result != (CheckpointRefreshResult{}) {
				t.Fatalf("invalid stored title: %+v, %v", result, err)
			}
		}
		refreshTestOperations(t, log)
	})
	t.Run("PrefixAndOriginalUTF8Limit", func(t *testing.T) {
		c, _, log := refreshTestPipeline(t, nil, nil)
		prefix := "Handoff: p / "
		limit := prefix + strings.Repeat("é", 121) + "x" // 256 original UTF-8 bytes.
		for _, tc := range []struct{ title, reason string }{{limit, "covered_by_required"}, {limit + "x", ""}, {prefix + " T ", "covered_by_required"}, {"Handoff: other / T", ""}} {
			input := refreshTestInput()
			input.Workstream = &tc.title
			input.Selected = []core.Selection{{Record: core.Record{Draft: core.Draft{Body: tc.title + "\ncovered"}}}}
			result, err := c.RefreshCheckpoint(nil, input)
			if err != nil || result.Reason != tc.reason || result.Text != "" {
				t.Fatalf("title bytes/prefix %q: %+v, %v", tc.title, result, err)
			}
		}
		refreshTestOperations(t, log)
	})
	t.Run("ForbiddenTitleCharacters", func(t *testing.T) {
		c, _, log := refreshTestPipeline(t, nil, nil)
		for _, ending := range []string{"\r", "\n", "\""} {
			input := refreshTestInput()
			title := refreshTestTitle + ending
			input.Workstream = &title
			input.Selected = []core.Selection{{Record: core.Record{Draft: core.Draft{Body: title + "\ncovered"}}}}
			result, err := c.RefreshCheckpoint(nil, input)
			if err != nil || result != (CheckpointRefreshResult{}) {
				t.Fatalf("forbidden title: %+v, %v", result, err)
			}
		}
		refreshTestOperations(t, log)
	})
	t.Run("RequiredCoverageBeforeDeadline", func(t *testing.T) {
		c, _, log := refreshTestPipeline(t, nil, nil)
		input := refreshTestInput()
		input.Selected = []core.Selection{{Record: core.Record{Draft: core.Draft{Body: refreshTestBody}}}}
		expired, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
		defer cancel()
		for _, ctx := range []context.Context{nil, context.Background(), expired} {
			result, err := c.RefreshCheckpoint(ctx, input)
			if err != nil || result.Reason != "covered_by_required" || result.Text != "" || result.Cause != nil {
				t.Fatalf("covered before deadline: %+v, %v", result, err)
			}
		}
		if c.Accounting().OperationCalls["search"] != 0 || c.Accounting().OperationCalls["pull"] != 0 {
			t.Fatal("coverage counted an operation")
		}
		refreshTestOperations(t, log)
	})
	t.Run("RequiredBodyPrefixOnly", func(t *testing.T) {
		c, _, log := refreshTestPipeline(t, nil, nil)
		input := refreshTestInput()
		input.Selected = []core.Selection{
			{Record: core.Record{Draft: core.Draft{Body: "not this checkpoint"}}},
			{Record: core.Record{RecordID: "\xff", Version: 0, Class: "C", Draft: core.Draft{Kind: "decision", Body: refreshTestTitle + "\n" + strings.Repeat("x", 5000)}}},
			{Record: core.Record{Draft: core.Draft{Body: "\xff"}}},
		}
		result, err := c.RefreshCheckpoint(nil, input)
		if err != nil || result.Reason != "covered_by_required" || result.Text != "" || result.Cause != nil {
			t.Fatalf("body-only coverage: %+v, %v", result, err)
		}
		refreshTestOperations(t, log)
	})
	t.Run("ProjectClipQuoteAndEmptyBase", func(t *testing.T) {
		c, _, log := refreshTestPipeline(t, nil, nil)
		for _, tc := range []struct{ project, title string }{
			{"", "Handoff:  / T"}, {strings.Repeat("a", 39) + "é", "Handoff: " + strings.Repeat("a", 39) + " / T"},
			{"\"" + strings.Repeat("a", 39) + "é", "Handoff: " + strings.Repeat("a", 39) + " / T"},
		} {
			input := CheckpointRefreshInput{ProjectBase: tc.project, Workstream: &tc.title, Selected: []core.Selection{{Record: core.Record{Draft: core.Draft{Body: tc.title + "\ncovered"}}}}}
			result, err := c.RefreshCheckpoint(nil, input)
			if err != nil || result.Reason != "covered_by_required" {
				t.Fatalf("literal clipped prefix: %+v, %v", result, err)
			}
		}
		refreshTestOperations(t, log)
	})
	t.Run("UnicodeDomainShortCircuit", func(t *testing.T) {
		c, _, log := refreshTestPipeline(t, nil, nil)
		if result, err := c.RefreshCheckpoint(nil, CheckpointRefreshInput{ProjectBase: "\xff"}); err != nil || result != (CheckpointRefreshResult{}) {
			t.Fatalf("nil title inspected unused input: %+v, %v", result, err)
		}
		for _, project := range []string{strings.Repeat("a", 40) + "\xff", "\xc3\"\xa9"} {
			input := refreshTestInput()
			input.ProjectBase = project
			refreshTestInvalid(t, c, nil, input)
		}
		wrong := "wrong prefix\xff"
		input := refreshTestInput()
		input.Workstream = &wrong
		if result, err := c.RefreshCheckpoint(nil, input); err != nil || result != (CheckpointRefreshResult{}) {
			t.Fatalf("wrong prefix encoded title: %+v, %v", result, err)
		}
		for _, ending := range []string{"\xff", strings.Repeat("x", 260) + "\xff\n"} {
			title := refreshTestTitle + ending
			input.Workstream = &title
			refreshTestInvalid(t, c, nil, input)
		}
		refreshTestOperations(t, log)
	})
}

func TestCheckpointRefreshPagination(t *testing.T) {
	t.Run("QuotedSearchOptionsAndOffsetZero", func(t *testing.T) {
		c, _, log := refreshTestPipeline(t, []any{refreshTestIndex(0, nil)}, nil)
		refreshTestOutcome(t, c, refreshTestInput(), "not_found")
		refreshTestOperations(t, log, "version", "index")
		call := log.snapshot()[1]
		id := refreshTestDecode(t, call.body)["request_id"].(string)
		if parsed, err := uuid.Parse(id); err != nil || parsed.String() != id {
			t.Fatal("search did not generate its own canonical UUID")
		}
		room, offset := 16000, 0
		refreshTestRequestBody(t, call, core.CompileRequest{Scope: refreshTestScope, Query: `"Handoff: p / T"`, Purpose: "context", AvailableTokens: room,
			Context: &core.ContextPins{}, RequestID: id, PageOffset: &offset, Kinds: []string{"note"}, CompactPreviewEntities: true})
	})
	t.Run("MissingOrNullPage", func(t *testing.T) {
		for _, explicitNull := range []bool{false, true} {
			page := refreshTestIndex(0, nil, refreshTestEntry())
			page.Package.Semantic.Page = nil
			var reply any = page
			if explicitNull {
				raw, err := json.Marshal(page)
				if err != nil {
					t.Fatal(err)
				}
				fields := refreshTestDecode(t, raw)
				fields["package"].(map[string]any)["semantic"].(map[string]any)["page"] = nil
				reply = fields
			}
			c, _, log := refreshTestPipeline(t, []any{reply}, refreshTestPull(refreshTestBody))
			refreshTestOutcome(t, c, refreshTestInput(), "incomplete_search")
			refreshTestOperations(t, log, "version", "index")
		}
	})
	t.Run("MismatchedOffset", func(t *testing.T) {
		c, _, log := refreshTestPipeline(t, []any{refreshTestIndex(9, nil, refreshTestEntry(), refreshTestEntry())}, nil)
		refreshTestOutcome(t, c, refreshTestInput(), "incomplete_search")
		refreshTestOperations(t, log, "version", "index")
	})
	t.Run("NonIncreasingNext", func(t *testing.T) {
		zero, five, four := 0, 5, 4
		for _, pages := range [][]any{{refreshTestIndex(0, &zero)}, {refreshTestIndex(0, &five), refreshTestIndex(5, &four)}} {
			c, _, log := refreshTestPipeline(t, pages, nil)
			refreshTestOutcome(t, c, refreshTestInput(), "incomplete_search")
			if len(log.snapshot()) != 2*len(pages) {
				t.Fatal("nonincreasing next caused another request")
			}
		}
	})
	t.Run("ThreePagesEnd", func(t *testing.T) {
		five, ten := 5, 10
		c, _, log := refreshTestPipeline(t, []any{refreshTestIndex(0, &five), refreshTestIndex(5, &ten), refreshTestIndex(10, nil)}, nil)
		refreshTestOutcome(t, c, refreshTestInput(), "not_found")
		refreshTestOperations(t, log, "version", "index", "version", "index", "version", "index")
	})
	t.Run("ThirdContinuationNoFourthSearch", func(t *testing.T) {
		five, ten, fifteen := 5, 10, 15
		c, _, log := refreshTestPipeline(t, []any{refreshTestIndex(0, &five, refreshTestEntry()), refreshTestIndex(5, &ten), refreshTestIndex(10, &fifteen)}, nil)
		refreshTestOutcome(t, c, refreshTestInput(), "incomplete_search")
		refreshTestOperations(t, log, "version", "index", "version", "index", "version", "index")
	})
	t.Run("ExactMatchingFilter", func(t *testing.T) {
		badClass, badKind, badSummary, conflict := refreshTestEntry(), refreshTestEntry(), refreshTestEntry(), refreshTestEntry()
		badClass.RecordID, badClass.Class = "aaaaaaaa-aaaa-4aaa-8aaa-000000000001", "B"
		badKind.RecordID, badKind.Kind = "aaaaaaaa-aaaa-4aaa-8aaa-000000000002", "decision"
		badSummary.RecordID, badSummary.Summary = "aaaaaaaa-aaaa-4aaa-8aaa-000000000003", "other\nsummary"
		conflict.RecordID, conflict.Conflicts = "aaaaaaaa-aaaa-4aaa-8aaa-000000000004", []core.AdvisoryConflict{{ID: "fixture-conflict", Version: 1}}
		c, _, log := refreshTestPipeline(t, []any{refreshTestIndex(0, nil, badClass, badKind, badSummary, conflict, refreshTestEntry())}, refreshTestPull(refreshTestBody))
		refreshTestOutcome(t, c, refreshTestInput(), "restored")
		refreshTestOperations(t, log, "version", "index", "expand")
		if got := refreshTestDecode(t, log.snapshot()[2].body)["handle"]; got != "handle-"+refreshTestRecord {
			t.Fatalf("pulled a filtered entry: %v", got)
		}
	})
	t.Run("AmbiguousAcrossPagesNoPull", func(t *testing.T) {
		five, zero := 5, 0
		for _, pages := range [][]any{{refreshTestIndex(0, &zero, refreshTestEntry(), refreshTestEntry())},
			{refreshTestIndex(0, &five, refreshTestEntry()), refreshTestIndex(5, &zero, refreshTestEntry())}} {
			c, _, log := refreshTestPipeline(t, pages, nil)
			refreshTestOutcome(t, c, refreshTestInput(), "ambiguous")
			if len(log.snapshot()) != 2*len(pages) {
				t.Fatal("ambiguity pulled or continued pagination")
			}
		}
	})
	t.Run("NoMatchBeforeFinalPageStillContinues", func(t *testing.T) {
		five, ten := 5, 10
		c, _, log := refreshTestPipeline(t, []any{refreshTestIndex(0, &five), refreshTestIndex(5, &ten, refreshTestEntry()), refreshTestIndex(10, nil)}, refreshTestPull(refreshTestBody))
		refreshTestOutcome(t, c, refreshTestInput(), "restored")
		refreshTestOperations(t, log, "version", "index", "version", "index", "version", "index", "expand")
	})
}

func TestCheckpointRefreshWholePull(t *testing.T) {
	t.Run("CopiedArgumentsAndLiteralUUID", func(t *testing.T) {
		c, _, log := refreshTestPipeline(t, nil, refreshTestPull(refreshTestBody))
		entry := indexview.Entry{IndexEntry: refreshTestEntry(), PullArguments: core.ExpandRequest{
			RequestID: refreshTestRequest, ReceiptID: refreshTestReceipt, Handle: "fixture-handle"}}
		before := entry
		_, body, err := c.refreshPull(refreshTestContext(t), entry)
		if err != nil || body != refreshTestBody || !reflect.DeepEqual(entry, before) {
			t.Fatalf("copied whole pull: %q, %v", body, err)
		}
		seed := `["cairn.lifecycle.whole-pull/1",{"handle":"fixture-handle","receipt_id":"11111111-1111-4111-8111-111111111111","request_id":"44444444-4444-4444-8444-444444444444"}]`
		refreshTestOperations(t, log, "expand")
		refreshTestRequestBody(t, log.snapshot()[0], map[string]any{"request_id": uuid.NewSHA1(uuid.NameSpaceURL, []byte(seed)).String(),
			"receipt_id": refreshTestReceipt, "handle": "fixture-handle"})
	})
	t.Run("GeneratedThreeMemberDomain", func(t *testing.T) {
		c, _, log := refreshTestPipeline(t, []any{refreshTestIndex(0, nil, refreshTestEntry())}, refreshTestPull(refreshTestBody))
		room, offset, timeout := 16000, 0, 2*time.Second
		ctx := refreshTestContext(t)
		view, err := c.Search(ctx, `"Handoff: p / T"`, SearchOptions{Room: &room, Offset: &offset, Kinds: []string{"note"}, Timeout: &timeout})
		if err != nil || len(view.Index) != 1 {
			t.Fatalf("generated entry: %v", err)
		}
		original := view.Index[0].PullArguments
		if original.Span != nil || original.OpeningBytes != 0 || original.RequestID == "" {
			t.Fatal("presenter no longer generates the admitted three-member argument shape")
		}
		raw, err := json.Marshal(original)
		if err != nil {
			t.Fatal(err)
		}
		fields := refreshTestDecode(t, raw)
		if len(fields) != 3 || fields["request_id"] != original.RequestID || fields["receipt_id"] != refreshTestReceipt || fields["handle"] != "handle-"+refreshTestRecord {
			t.Fatalf("generated arguments: %s", raw)
		}
		if _, _, err := c.refreshPull(ctx, view.Index[0]); err != nil {
			t.Fatal(err)
		}
		if view.Index[0].PullArguments != original {
			t.Fatal("internal pull consumed the caller's request UUID")
		}
		seed := `["cairn.lifecycle.whole-pull/1",{"handle":"handle-22222222-2222-4222-8222-222222222222","receipt_id":"11111111-1111-4111-8111-111111111111","request_id":"` + original.RequestID + `"}]`
		refreshTestOperations(t, log, "version", "index", "expand")
		refreshTestRequestBody(t, log.snapshot()[2], map[string]any{"request_id": uuid.NewSHA1(uuid.NameSpaceURL, []byte(seed)).String(), "receipt_id": refreshTestReceipt, "handle": original.Handle})
	})
	t.Run("IdentityVersionAndStringBody", func(t *testing.T) {
		for _, change := range []func(map[string]any){
			func(record map[string]any) { record["record_id"] = "aaaaaaaa-aaaa-4aaa-8aaa-000000000001" },
			func(record map[string]any) { record["version"] = 8 },
			func(record map[string]any) { record["body"] = true },
			func(record map[string]any) { delete(record, "body") },
		} {
			pull := refreshTestPull(refreshTestBody)
			change(pull["selection"].(map[string]any)["record"].(map[string]any))
			pull["span"] = false
			c, _, log := refreshTestPipeline(t, []any{refreshTestIndex(0, nil, refreshTestEntry())}, pull)
			result := refreshTestOutcome(t, c, refreshTestInput(), "unavailable")
			if result.Cause == nil || result.Cause.Error() != "optional body identity or version changed" {
				t.Fatalf("identity must precede whole check: %v", result.Cause)
			}
			if c.Accounting().ReceiptCredits[refreshTestReceipt] != 3 {
				t.Fatal("identity failure refunded the confirmed pull")
			}
			refreshTestOperations(t, log, "version", "index", "expand")
		}
	})
	t.Run("OptionalBodyHash", func(t *testing.T) {
		sum := sha256.Sum256([]byte(refreshTestBody))
		for _, hash := range []string{"", hex.EncodeToString(sum[:]), strings.Repeat("0", 64)} {
			entry := refreshTestEntry()
			entry.BodySHA256 = hash
			pull := refreshTestPull(refreshTestBody)
			if hash == strings.Repeat("0", 64) {
				pull["source_extent"] = "partial_span"
			}
			c, _, log := refreshTestPipeline(t, []any{refreshTestIndex(0, nil, entry)}, pull)
			want := "restored"
			if hash == strings.Repeat("0", 64) {
				want = "unavailable"
			}
			result := refreshTestOutcome(t, c, refreshTestInput(), want)
			if want == "unavailable" && (result.Cause == nil || result.Cause.Error() != "optional body hash changed") {
				t.Fatalf("hash before extent: %v", result.Cause)
			}
			refreshTestOperations(t, log, "version", "index", "expand")
		}
	})
	t.Run("WholeExtentAndSpanPresence", func(t *testing.T) {
		for _, tc := range []struct {
			field  string
			value  any
			reason string
		}{
			{"", nil, "restored"}, {"span", nil, "restored"}, {"span", false, "not_whole"}, {"span", map[string]any{}, "not_whole"},
			{"span", []any{}, "not_whole"}, {"span", "", "not_whole"}, {"source_extent", "partial_span", "not_whole"},
			{"source_extent", nil, "restored"}, {"source_extent", false, "restored"}, {"source_extent", map[string]any{}, "restored"}, {"source_extent", []any{}, "restored"},
		} {
			pull := refreshTestPull(refreshTestBody)
			if tc.field != "" {
				pull[tc.field] = tc.value
			}
			c, _, log := refreshTestPipeline(t, []any{refreshTestIndex(0, nil, refreshTestEntry())}, pull)
			result := refreshTestOutcome(t, c, refreshTestInput(), tc.reason)
			if result.Cause != nil {
				t.Fatalf("raw extent/span value became a decode failure: %v", result.Cause)
			}
			refreshTestOperations(t, log, "version", "index", "expand")
		}
	})
	t.Run("PrefixAnd4500ByteLimit", func(t *testing.T) {
		for _, size := range []int{4500, 4501} {
			remaining := size - len(refreshTestTitle+"\n")
			body := refreshTestTitle + "\n" + strings.Repeat("é", remaining/2) + strings.Repeat("x", remaining%2)
			c, _, log := refreshTestPipeline(t, []any{refreshTestIndex(0, nil, refreshTestEntry())}, refreshTestPull(body))
			want := "restored"
			if size == 4501 {
				want = "not_whole"
			}
			refreshTestOutcome(t, c, refreshTestInput(), want)
			refreshTestOperations(t, log, "version", "index", "expand")
		}
		c, _, log := refreshTestPipeline(t, []any{refreshTestIndex(0, nil, refreshTestEntry())}, refreshTestPull("different title\nbody"))
		refreshTestOutcome(t, c, refreshTestInput(), "not_whole")
		refreshTestOperations(t, log, "version", "index", "expand")
	})
	t.Run("PythonLeadingWhitespace", func(t *testing.T) {
		for _, tc := range []struct{ leading, reason string }{{"\u001c\u001d\u001e\u001f\u0085\u00a0\u2003\t\r\n", "complete"}, {"\u200b", "restored"}} {
			body := refreshTestTitle + "\n\n" + tc.leading + "Status: complete\n"
			c, _, log := refreshTestPipeline(t, []any{refreshTestIndex(0, nil, refreshTestEntry())}, refreshTestPull(body))
			refreshTestOutcome(t, c, refreshTestInput(), tc.reason)
			refreshTestOperations(t, log, "version", "index", "expand")
		}
	})
	t.Run("CompletionRequiresLiteralTrailingLF", func(t *testing.T) {
		for _, tc := range []struct{ ending, reason string }{{"Status: complete\n", "complete"}, {"Status: complete", "restored"}, {"Status: Complete\n", "restored"}} {
			c, _, log := refreshTestPipeline(t, []any{refreshTestIndex(0, nil, refreshTestEntry())}, refreshTestPull(refreshTestTitle+"\n\n"+tc.ending))
			refreshTestOutcome(t, c, refreshTestInput(), tc.reason)
			refreshTestOperations(t, log, "version", "index", "expand")
		}
	})
	t.Run("RestoredFixedJSONText", func(t *testing.T) {
		body := refreshTestTitle + "\n\n<>&/\u2028\u2029é\"\t\u001c\\body"
		c, _, log := refreshTestPipeline(t, []any{refreshTestIndex(0, nil, refreshTestEntry())}, refreshTestPull(body))
		result := refreshTestOutcome(t, c, refreshTestInput(), "restored")
		want := "Current saved unfinished work (ordinary note, verify against this task):\n" +
			`{"schema":"cairn.checkpoint-refresh/1","record_id":"22222222-2222-4222-8222-222222222222","version":7,"source_extent":"whole","body":"Handoff: p / T\n\n<>&/` + "\u2028\u2029é" + `\"\t\u001c\\body"}`
		if result.Text != want || result.Cause != nil {
			t.Fatalf("literal restored text got %q; want %q", result.Text, want)
		}
		refreshTestOperations(t, log, "version", "index", "expand")
	})
}

func TestCheckpointRefreshOutcomes(t *testing.T) {
	t.Run("NotFound", func(t *testing.T) {
		c, _, log := refreshTestPipeline(t, []any{refreshTestIndex(0, nil)}, nil)
		result := refreshTestOutcome(t, c, refreshTestInput(), "not_found")
		if result.Text != "Current unfinished checkpoint was not restored for Handoff: p / T; search current Cairn memory before relying on earlier context." || result.Cause != nil {
			t.Fatalf("fixed cue: %+v", result)
		}
		refreshTestOperations(t, log, "version", "index")
	})
	t.Run("ExactReasonAndTextSet", func(t *testing.T) {
		for _, reason := range []string{"", "covered_by_required", "incomplete_search", "ambiguous", "not_found", "not_whole", "complete", "restored", "unavailable"} {
			page := refreshTestIndex(0, nil, refreshTestEntry())
			pull := refreshTestPull(refreshTestBody)
			input := refreshTestInput()
			switch reason {
			case "":
				input.Workstream = nil
			case "covered_by_required":
				input.Selected = []core.Selection{{Record: core.Record{Draft: core.Draft{Body: refreshTestBody}}}}
			case "incomplete_search":
				page.Package.Semantic.Page = nil
			case "ambiguous":
				page = refreshTestIndex(0, nil, refreshTestEntry(), refreshTestEntry())
			case "not_found":
				page = refreshTestIndex(0, nil)
			case "not_whole":
				pull["span"] = false
			case "complete":
				pull = refreshTestPull(refreshTestTitle + "\n\nStatus: complete\n")
			case "unavailable":
				pull["selection"].(map[string]any)["record"].(map[string]any)["body"] = false
			}
			c, _, log := refreshTestPipeline(t, []any{page}, pull)
			result := refreshTestOutcome(t, c, input, reason)
			if reason == "restored" {
				want := "Current saved unfinished work (ordinary note, verify against this task):\n" + `{"schema":"cairn.checkpoint-refresh/1","record_id":"22222222-2222-4222-8222-222222222222","version":7,"source_extent":"whole","body":"Handoff: p / T\n\nbody"}`
				if result.Text != want {
					t.Fatalf("fixed ordinary view: %q", result.Text)
				}
			}
			wantCalls := 3
			if reason == "" || reason == "covered_by_required" {
				wantCalls = 0
			} else if reason == "incomplete_search" || reason == "ambiguous" || reason == "not_found" {
				wantCalls = 2
			}
			if len(log.snapshot()) != wantCalls {
				t.Fatalf("reason %q made unexpected IO", reason)
			}
		}
	})
	t.Run("OptionalFailureUnavailableAndCause", func(t *testing.T) {
		for _, scenario := range []string{"status", "destination", "INVALID_REQUEST", "BUDGET_REFUSED", "truncated", "schema"} {
			api, log := refreshTestAPI(t, func(w http.ResponseWriter, _ *http.Request, _ refreshTestCall) {
				switch scenario {
				case "status":
					page := refreshTestIndex(0, nil)
					page.Package.Semantic.Status = "REFUSED"
					refreshTestSuccess(t, w, page)
				case "destination":
					page := refreshTestIndex(0, nil)
					page.Package.Semantic.Destination.Name = "local"
					refreshTestSuccess(t, w, page)
				case "INVALID_REQUEST", "BUDGET_REFUSED":
					if err := json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": false, "status": scenario, "message": "synthetic service refusal"}); err != nil {
						t.Error(err)
					}
				case "truncated":
					w.Header().Set("Content-Length", "500")
					if _, err := io.WriteString(w, `{"schema":"cairn.response/1"`); err != nil {
						t.Error(err)
					}
				case "schema":
					if _, err := io.WriteString(w, `{"schema":"wrong","ok":true,"data":{}}`); err != nil {
						t.Error(err)
					}
				}
			})
			c := refreshTestNew(t, api, Options{Scope: refreshTestScope})
			result := refreshTestOutcome(t, c, refreshTestInput(), "unavailable")
			if result.Cause == nil || strings.Contains(result.Text, "synthetic") {
				t.Fatalf("optional failure lost/redacted cause: %+v", result)
			}
			if scenario == "status" && !errors.Is(result.Cause, ErrSearchStatus) {
				t.Fatal(result.Cause)
			}
			if scenario == "destination" && !errors.Is(result.Cause, ErrNotHosted) {
				t.Fatal(result.Cause)
			}
			if scenario == "truncated" && !errors.Is(result.Cause, io.ErrUnexpectedEOF) {
				t.Fatal(result.Cause)
			}
			if scenario == "schema" && !errors.Is(result.Cause, ErrNotConfirmed) {
				t.Fatal(result.Cause)
			}
			if scenario == "INVALID_REQUEST" || scenario == "BUDGET_REFUSED" {
				var service *core.Error
				if !errors.As(result.Cause, &service) || service.Code != scenario {
					t.Fatalf("typed optional service cause: %v", result.Cause)
				}
			}
			refreshTestOperations(t, log, "version", "index")
		}
	})
	t.Run("NoWriteRPCOrFrontendState", func(t *testing.T) {
		c, _, log := refreshTestPipeline(t, []any{refreshTestIndex(0, nil, refreshTestEntry())}, refreshTestPull(refreshTestBody))
		input := refreshTestInput()
		input.Selected = []core.Selection{{Record: core.Record{Draft: core.Draft{Body: "other note"}}}}
		refreshTestOutcome(t, c, input, "restored")
		if input.ProjectBase != "p" || *input.Workstream != refreshTestTitle || input.Selected[0].Record.Body != "other note" {
			t.Fatal("consumer mutated supplied state projection")
		}
		refreshTestOperations(t, log, "version", "index", "expand")
		if len(c.Accounting().OperationCalls) != 2 {
			t.Fatal("refresh introduced a writer or new operation counter")
		}
	})
}

func TestCheckpointRefreshContextAndAccounting(t *testing.T) {
	t.Run("MissingDeadlineAfterShortCircuits", func(t *testing.T) {
		c, _, log := refreshTestPipeline(t, nil, nil)
		for _, ctx := range []context.Context{nil, context.Background()} {
			refreshTestInvalid(t, c, ctx, refreshTestInput())
		}
		covered := refreshTestInput()
		covered.Selected = []core.Selection{{Record: core.Record{Draft: core.Draft{Body: refreshTestBody}}}}
		if result, err := c.RefreshCheckpoint(nil, covered); err != nil || result.Reason != "covered_by_required" {
			t.Fatalf("deadline check moved before coverage: %+v, %v", result, err)
		}
		ctx, cancel := context.WithDeadline(context.Background(), time.Now().Add(time.Minute))
		cancel()
		result, err := c.RefreshCheckpoint(ctx, refreshTestInput())
		if err != nil || result.Reason != "unavailable" || result.Text != refreshTestCue(refreshTestTitle) || !errors.Is(result.Cause, context.Canceled) {
			t.Fatalf("deadline-bearing cancellation is optional: %+v, %v", result, err)
		}
		refreshTestOperations(t, log)
		if c.Accounting().OperationCalls["search"] != 0 || c.Accounting().OperationCalls["pull"] != 0 {
			t.Fatal("pre-call failure initiated counters")
		}
	})
	t.Run("ExpiredBeforeCounters", func(t *testing.T) {
		c, _, log := refreshTestPipeline(t, nil, nil)
		ctx, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
		defer cancel()
		result, err := c.RefreshCheckpoint(ctx, refreshTestInput())
		if err != nil || result.Reason != "unavailable" || result.Text != refreshTestCue(refreshTestTitle) || !errors.Is(result.Cause, context.DeadlineExceeded) {
			t.Fatalf("expired search: %+v, %v", result, err)
		}
		entry := indexview.Entry{IndexEntry: refreshTestEntry(), PullArguments: core.ExpandRequest{RequestID: refreshTestRequest, ReceiptID: refreshTestReceipt, Handle: "fixture-handle"}}
		if _, _, err := c.refreshPull(ctx, entry); !errors.Is(err, context.DeadlineExceeded) {
			t.Fatalf("expired pull: %v", err)
		}
		if c.Accounting().OperationCalls["search"] != 0 || c.Accounting().OperationCalls["pull"] != 0 || len(c.Accounting().PullCalls) != 0 {
			t.Fatal("expired operation changed counters")
		}
		refreshTestOperations(t, log)
	})
	t.Run("SharedDeadlineAndTwoSecondCap", func(t *testing.T) {
		for _, tc := range []struct{ remaining, want time.Duration }{{5 * time.Second, 2 * time.Second}, {100 * time.Millisecond, 100 * time.Millisecond}, {0, 0}, {-time.Second, 0}} {
			got, err := refreshCallLimit(tc.remaining)
			if got != tc.want || ((tc.remaining <= 0) != errors.Is(err, context.DeadlineExceeded)) {
				t.Fatalf("pure cap remaining=%v: %v, %v", tc.remaining, got, err)
			}
		}
		ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
		defer cancel()
		next := 5
		api, log := refreshTestAPI(t, func(w http.ResponseWriter, _ *http.Request, call refreshTestCall) {
			if call.operation != "index" {
				t.Errorf("unexpected operation %q", call.operation)
				return
			}
			refreshTestSuccess(t, w, refreshTestIndex(0, &next))
			cancel()
		})
		c := refreshTestNew(t, api, Options{Scope: refreshTestScope})
		result, err := c.RefreshCheckpoint(ctx, refreshTestInput())
		if err != nil || result.Reason != "unavailable" || !errors.Is(result.Cause, context.Canceled) {
			t.Fatalf("shared parent canceled between pages: %+v, %v", result, err)
		}
		refreshTestOperations(t, log, "version", "index")
		if c.Accounting().OperationCalls["search"] != 1 || c.Accounting().OperationCalls["pull"] != 0 {
			t.Fatal("consumer restarted its parent deadline or operation")
		}
	})
	t.Run("KnownZeroCreditsPreflight", func(t *testing.T) {
		c, _, log := refreshTestPipeline(t, []any{refreshTestIndex(0, nil, refreshTestEntry())}, refreshTestPull(refreshTestBody))
		c.accounting.ReceiptCredits[refreshTestReceipt] = 0
		result := refreshTestOutcome(t, c, refreshTestInput(), "unavailable")
		var service *core.Error
		if !errors.As(result.Cause, &service) || service.Code != "BUDGET_REFUSED" {
			t.Fatalf("known-zero optional cause: %v", result.Cause)
		}
		got := c.Accounting()
		if got.OperationCalls["search"] != 1 || got.OperationCalls["pull"] != 0 || len(got.PullCalls) != 0 || got.ReceiptCredits[refreshTestReceipt] != 0 {
			t.Fatalf("known-zero preflight: %+v", got)
		}
		refreshTestOperations(t, log, "version", "index")
	})
	t.Run("ReceiptAndCreditOrderBeforeValidation", func(t *testing.T) {
		for _, scenario := range []string{"page", "identity", "span", "unconfirmed"} {
			page := refreshTestIndex(0, nil, refreshTestEntry())
			var pull any = refreshTestPull(refreshTestBody)
			want := "unavailable"
			switch scenario {
			case "page":
				page.Package.Semantic.Page.Offset = 9
				want = "incomplete_search"
			case "identity":
				pull.(map[string]any)["selection"].(map[string]any)["record"].(map[string]any)["version"] = 8
			case "span":
				pull.(map[string]any)["span"] = false
				want = "not_whole"
			case "unconfirmed":
				pull = nil
			}
			c, _, log := refreshTestPipeline(t, []any{page}, pull)
			refreshTestOutcome(t, c, refreshTestInput(), want)
			got := c.Accounting()
			if got.OperationCalls["search"] != 1 || !reflect.DeepEqual(got.SearchReceipts, []string{refreshTestReceipt}) {
				t.Fatalf("search receipt lost after rejection: %+v", got)
			}
			if scenario == "page" {
				if got.OperationCalls["pull"] != 0 {
					t.Fatal("page validation pulled")
				}
				refreshTestOperations(t, log, "version", "index")
			} else {
				if got.OperationCalls["pull"] != 1 || got.PullCalls[refreshTestReceipt] != 1 {
					t.Fatal("attempted pull not counted")
				}
				if scenario == "unconfirmed" {
					if len(got.ReceiptCredits) != 0 {
						t.Fatal("unconfirmed data updated credits")
					}
				} else if got.ReceiptCredits[refreshTestReceipt] != 3 {
					t.Fatal("confirmed credit update lost or refunded")
				}
				refreshTestOperations(t, log, "version", "index", "expand")
			}
		}
	})
	t.Run("SessionDiagnosticsAndBorrowedLifetime", func(t *testing.T) {
		_, api, log := refreshTestPipeline(t, []any{refreshTestIndex(0, nil, refreshTestEntry())}, refreshTestPull(refreshTestBody))
		session := core.AgentSessionRef{AgentID: refreshTestRecord, ExecutionID: refreshTestRequest}
		diagnostics := localapi.ClientDiagnostics{Surface: "cli", Harness: "unknown", Origin: &localapi.ClientOrigin{Component: "lifecycle-memory", Basis: "reported"}}
		c := refreshTestNew(t, api.ForAgentSession(session), Options{Scope: refreshTestScope, Diagnostics: diagnostics})
		refreshTestOutcome(t, c, refreshTestInput(), "restored")
		refreshTestOperations(t, log, "version", "index", "expand")
		diagnostics.TransportBuild = buildinfo.Read()
		for _, call := range log.snapshot() {
			if call.header.Get("Authorization") != "Bearer synthetic-refresh-token" || call.header.Get("Cairn-Agent-ID") != session.AgentID || call.header.Get("Cairn-Execution-ID") != session.ExecutionID ||
				call.header.Get(localapi.ProtocolHeader) != strconv.Itoa(localapi.Protocol.Current) || call.header.Get(localapi.ClientDiagnosticsHeader) != localapi.EncodeClientDiagnostics(diagnostics) {
				t.Fatal("borrowed authentication/session/diagnostics changed")
			}
			opening := ""
			if call.operation == "expand" {
				opening = "1"
			}
			if call.header.Get(localapi.SourceOpeningHeader) != opening {
				t.Fatal("source-opening declaration changed")
			}
			for _, field := range []string{"agent_id", "execution_id", "principal", "attributed_producer", "attempt_id", "result_ref"} {
				if _, present := refreshTestDecode(t, call.body)[field]; present {
					t.Fatalf("payload invented attribution %q", field)
				}
			}
		}
		var raw json.RawMessage
		if err := api.Call(context.Background(), "version", struct{}{}, &raw); err != nil {
			t.Fatalf("consumer closed borrowed SDK: %v", err)
		}
		if call := log.snapshot()[3]; call.header.Get(localapi.ClientDiagnosticsHeader) != "" || call.header.Get("Cairn-Agent-ID") != "" {
			t.Fatal("consumer mutated original SDK view")
		}
	})
}
