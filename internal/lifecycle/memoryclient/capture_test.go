package memoryclient_test

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/lifecycle/memoryclient"
	"github.com/halbritt/cairn/localapi"
)

func captureJSON(t *testing.T, value any) json.RawMessage {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func captureSelection(t *testing.T, checkpoint, topic any, notes ...any) json.RawMessage {
	t.Helper()
	if notes == nil {
		notes = []any{}
	}
	return captureJSON(t, map[string]any{"checkpoint": checkpoint, "workstream": topic, "memories": notes})
}

func captureNote(id, kind, title, body any) map[string]any {
	return map[string]any{"record_id": id, "kind": kind, "title": title, "body": body}
}

// Responses are synthetic data on the existing fixture's private Unix socket.
// Lookup records deliberately contain only body, not record identity or version.
func captureAPI(t *testing.T, bodies map[string]string, write func(http.ResponseWriter, *http.Request, requestCapture)) (*localapi.Client, *recorder) {
	t.Helper()
	var selectedBody string
	return fixture(t, func(w http.ResponseWriter, r *http.Request, call requestCapture) {
		if probe(t, w, call) {
			return
		}
		switch call.operation {
		case "index":
			query := decode(t, call.body).(map[string]any)["query"].(string)
			title := strings.TrimSuffix(strings.TrimPrefix(query, `"`), `"`)
			if body, found := bodies[title]; found {
				selectedBody = body
				success(t, w, indexReply(entry(recordID, title+"\nsummary")))
			} else {
				success(t, w, indexReply())
			}
		case "expand":
			success(t, w, map[string]any{"selection": map[string]any{"record": map[string]any{"body": selectedBody}}, "credits_remaining": 3})
		case "create", "revise":
			if write == nil {
				success(t, w, map[string]string{"record_id": recordID})
			} else {
				write(w, r, call)
			}
		default:
			t.Errorf("unexpected capture operation %q", call.operation)
		}
	})
}

func captureOps(t *testing.T, seen *recorder, want ...string) {
	t.Helper()
	calls := seen.snapshot()
	if len(calls) != len(want) {
		t.Fatalf("operations: got %v, want %v", captureOperationNames(calls), want)
	}
	for i := range calls {
		if calls[i].operation != want[i] {
			t.Fatalf("operations: got %v, want %v", captureOperationNames(calls), want)
		}
	}
}

func captureOperationNames(calls []requestCapture) []string {
	names := make([]string, len(calls))
	for i := range calls {
		names[i] = calls[i].operation
	}
	return names
}

func captureEmpty(t *testing.T, result memoryclient.CaptureResult) {
	t.Helper()
	if len(result.Saved) != 0 || result.Workstream != nil {
		t.Fatalf("unexpected capture progress: %+v", result)
	}
}

func captureRefused(t *testing.T, c *memoryclient.Client, input memoryclient.CaptureInput, want string) {
	t.Helper()
	result, err := c.CommitCaptureSelection(context.Background(), input)
	captureEmpty(t, result)
	if !errors.Is(err, memoryclient.ErrCaptureSelection) || err.Error() != want ||
		errors.Is(err, memoryclient.ErrCaptureDomain) || errors.Is(err, memoryclient.ErrCaptureWrite) {
		t.Fatalf("selection refusal: %v; want %q", err, want)
	}
}

func captureUpdates(t *testing.T, ids ...string) memoryclient.CaptureInput {
	t.Helper()
	input := memoryclient.CaptureInput{}
	var notes []any
	for _, id := range ids {
		notes = append(notes, captureNote(id, "decision", "ignored", "a"))
		input.Candidates = append(input.Candidates, memoryclient.CaptureRecord{RecordID: id, Version: 7, Kind: "decision", Body: "old"})
	}
	input.Selection = captureSelection(t, nil, nil, notes...)
	return input
}

func captureServiceRefusal(t *testing.T, w http.ResponseWriter) {
	t.Helper()
	if _, err := io.WriteString(w, `{"schema":"cairn.response/1","ok":false,"status":"CONFLICT","message":"synthetic CAS refusal","refusal_id":"capture-fixture"}`); err != nil {
		t.Error(err)
	}
}

func TestCaptureSelectionValidation(t *testing.T) {
	t.Run("RequiredAndUnknownKeys", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		for _, raw := range []string{`{}`, `{"checkpoint":null,"memories":[]}`, `{"workstream":null,"memories":[]}`,
			`{"checkpoint":null,"workstream":null}`, `{"checkpoint":null,"workstream":null,"memories":[],"extra":0}`} {
			captureRefused(t, c, memoryclient.CaptureInput{Selection: json.RawMessage(raw)}, "memory selection did not return the required schema")
		}
		captureOps(t, seen)
	})
	t.Run("CheckpointStateNullAndDefaultOrder", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		for _, raw := range []string{
			`{"checkpoint":false,"workstream":null,"memories":null,"checkpoint_state":"wrong"}`,
			`{"checkpoint":null,"workstream":null,"memories":[],"checkpoint_state":"open"}`,
			`{"checkpoint":"body","workstream":"T","memories":[],"checkpoint_state":null}`,
			`{"checkpoint":null,"workstream":null,"memories":[],"checkpoint_state":false}`,
		} {
			captureRefused(t, c, memoryclient.CaptureInput{Selection: json.RawMessage(raw)}, "memory selection returned an invalid checkpoint state")
		}
		for _, raw := range []string{
			`{"checkpoint":null,"workstream":null,"memories":[]}`,
			`{"checkpoint":null,"workstream":null,"memories":[],"checkpoint_state":null}`,
		} {
			result, err := c.CommitCaptureSelection(context.Background(), memoryclient.CaptureInput{Selection: json.RawMessage(raw)})
			if err != nil {
				t.Fatal(err)
			}
			captureEmpty(t, result)
		}
		input := memoryclient.CaptureInput{Selection: captureSelection(t, " body ", "T"), ProjectBase: "p",
			Handoffs: []memoryclient.CaptureRecord{{Body: "Handoff: p / T\n\nbody"}}}
		result, err := c.CommitCaptureSelection(context.Background(), input)
		if err != nil || result.Workstream == nil || *result.Workstream != "Handoff: p / T" {
			t.Fatalf("default open state: %+v, %v", result, err)
		}
		captureOps(t, seen)
	})
	t.Run("CheckpointUTF8ByteLimit", func(t *testing.T) {
		for _, body := range []string{strings.Repeat("é", 2250), strings.Repeat(" ", 4499) + "x", strings.Repeat("é", 2251), "\u001c\u001f", ""} {
			api, seen := captureAPI(t, nil, nil)
			c := client(t, api, memoryclient.Options{Scope: scope})
			input := memoryclient.CaptureInput{Selection: captureSelection(t, body, "T"), ProjectBase: "p"}
			if body == strings.Repeat("é", 2250) {
				input.Handoffs = []memoryclient.CaptureRecord{{Body: "Handoff: p / T\n\n" + body}}
			} else if len(body) == 4500 {
				input.Handoffs = []memoryclient.CaptureRecord{{Body: "Handoff: p / T\n\nx"}}
			} else {
				captureRefused(t, c, input, "memory selection returned an invalid checkpoint")
				captureOps(t, seen)
				continue
			}
			if _, err := c.CommitCaptureSelection(context.Background(), input); err != nil {
				t.Fatalf("4500 original UTF-8 bytes: %v", err)
			}
			captureOps(t, seen)
		}
	})
	t.Run("ReusableNoteLimitAndExactKeys", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		for _, memories := range []any{nil, false, "notes", []any{nil, nil, nil, nil}} {
			raw := captureJSON(t, map[string]any{"checkpoint": nil, "workstream": nil, "memories": memories})
			captureRefused(t, c, memoryclient.CaptureInput{Selection: raw}, "memory selection returned invalid reusable notes")
		}
		for _, note := range []any{nil, false, []any{}, map[string]any{"record_id": nil, "kind": "decision", "body": "b"},
			map[string]any{"record_id": nil, "kind": "decision", "title": "T", "body": "b", "extra": 1}} {
			captureRefused(t, c, memoryclient.CaptureInput{Selection: captureSelection(t, nil, nil, note)}, "memory selection returned an invalid reusable note")
		}
		captureOps(t, seen)
	})
	t.Run("InvalidFieldsBeforeCandidateReference", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		for _, note := range []any{captureNote("unknown", "note", "T", "b"), captureNote("unknown", false, "T", "b"),
			captureNote("unknown", "decision", nil, "b"), captureNote("unknown", "decision", "T", ""),
			captureNote("unknown", "decision", "T", true)} {
			captureRefused(t, c, memoryclient.CaptureInput{Selection: captureSelection(t, nil, nil, note)}, "memory selection returned invalid note fields")
		}
		for _, id := range []any{"unknown", true, 12} {
			captureRefused(t, c, memoryclient.CaptureInput{Selection: captureSelection(t, nil, nil, captureNote(id, "decision", "", "b"))}, "memory selection referenced an unsupplied record")
		}
		input := captureUpdates(t, "same")
		input.Candidates[0].Kind = "lesson"
		captureRefused(t, c, input, "memory selection referenced an unsupplied record")
		captureOps(t, seen)
	})
	t.Run("PythonStripControlsAndExistingBody", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		input := memoryclient.CaptureInput{ProjectBase: "p", Selection: captureSelection(t, nil, nil,
			captureNote(nil, "decision", "\u001c\u001d\u001e\u001f \u0085\u00a0\tTopic \u001c", "\u001c\u2003Body\u001finside\u001c\n"),
			captureNote("old", "procedure", "\"\n", "\u001c  raw\u001f\n")),
			Candidates: []memoryclient.CaptureRecord{{RecordID: "old", Kind: "procedure", Version: 4, Body: "before"}}}
		if _, err := c.CommitCaptureSelection(context.Background(), input); err != nil {
			t.Fatal(err)
		}
		captureOps(t, seen, "version", "index", "create", "revise")
		calls := seen.snapshot()
		if body := decode(t, calls[2].body).(map[string]any)["draft"].(map[string]any)["body"]; body != "p: Topic\n\nBody\u001finside" {
			t.Fatalf("handwritten Python-strip oracle: %q", body)
		}
		if body := decode(t, calls[3].body).(map[string]any)["body"]; body != "\u001c  raw\u001f\n" {
			t.Fatalf("existing body was changed: %q", body)
		}
	})
	t.Run("TopicTitleOriginalBytesAndForbiddenCharacters", func(t *testing.T) {
		for _, title := range []string{strings.Repeat("é", 45), strings.Repeat("é", 46), strings.Repeat(" ", 90) + "T", "T\n", "T\r", `T"`, "\u001c\u001f"} {
			api, seen := captureAPI(t, nil, nil)
			c := client(t, api, memoryclient.Options{Scope: scope})
			input := memoryclient.CaptureInput{ProjectBase: "p", Selection: captureSelection(t, nil, nil, captureNote(nil, "lesson", title, "body"))}
			if title == strings.Repeat("é", 45) {
				if _, err := c.CommitCaptureSelection(context.Background(), input); err != nil {
					t.Fatal(err)
				}
				captureOps(t, seen, "version", "index", "create")
			} else {
				captureRefused(t, c, input, "memory selection returned an invalid topic title")
				captureOps(t, seen)
			}
		}
	})
	t.Run("PrefixedBodyByteLimit", func(t *testing.T) {
		for _, size := range []int{2997, 2998, 3001} {
			api, seen := captureAPI(t, nil, nil)
			c := client(t, api, memoryclient.Options{Scope: scope})
			input := memoryclient.CaptureInput{ProjectBase: "p", Selection: captureSelection(t, nil, nil, captureNote(nil, "decision", "T", strings.Repeat("é", size)))}
			if size == 2997 { // 5994 original bytes plus the literal "p: T\n\n" is 6000.
				if _, err := c.CommitCaptureSelection(context.Background(), input); err != nil {
					t.Fatal(err)
				}
				captureOps(t, seen, "version", "index", "create")
			} else {
				captureRefused(t, c, input, "memory selection returned invalid note fields")
				captureOps(t, seen)
			}
		}
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		input := captureUpdates(t, "old")
		input.Selection = captureSelection(t, nil, nil, captureNote("old", "decision", "", strings.Repeat("é", 3000)))
		if _, err := c.CommitCaptureSelection(context.Background(), input); err != nil {
			t.Fatalf("verbatim existing 6000-byte body: %v", err)
		}
		captureOps(t, seen, "revise")
	})
	t.Run("EmptySelectionNoMutation", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		result, err := c.CommitCaptureSelection(context.Background(), memoryclient.CaptureInput{Selection: captureSelection(t, nil, nil)})
		if err != nil {
			t.Fatal(err)
		}
		captureEmpty(t, result)
		captureOps(t, seen)
	})
	t.Run("CaseVariantSelectionAndNoteKeys", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		captureRefused(t, c, memoryclient.CaptureInput{Selection: json.RawMessage(`{"Checkpoint":null,"workstream":null,"memories":[]}`)}, "memory selection did not return the required schema")
		captureRefused(t, c, memoryclient.CaptureInput{Selection: json.RawMessage(`{"checkpoint":null,"workstream":null,"memories":[{"Record_id":null,"kind":"decision","title":"T","body":"b"}]}`)}, "memory selection returned an invalid reusable note")
		captureOps(t, seen)
	})
	t.Run("DuplicateSelectionAndNoteKeys", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		input := memoryclient.CaptureInput{Selection: json.RawMessage(`{"checkpoint":false,"checkpoint":null,"workstream":null,"memories":false,"memories":[{"record_id":"unknown","record_id":"old","kind":false,"kind":"decision","title":"","body":"wrong","body":"a"}]}`),
			Candidates: []memoryclient.CaptureRecord{{RecordID: "old", Kind: "decision", Body: "a"}}}
		result, err := c.CommitCaptureSelection(context.Background(), input)
		if err != nil {
			t.Fatal(err)
		}
		captureEmpty(t, result)
		input.Selection = json.RawMessage(`{"checkpoint":null,"checkpoint":false,"workstream":null,"memories":[]}`)
		captureRefused(t, c, input, "memory selection returned an invalid checkpoint")
		captureOps(t, seen)
	})
	t.Run("NilEmptyNullSelection", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		for _, raw := range []json.RawMessage{nil, {}, []byte(" \t\r\n"), []byte("null"), []byte("[]"), []byte("true"), []byte("12"), []byte(`"text"`)} {
			captureRefused(t, c, memoryclient.CaptureInput{Selection: raw}, "memory selection did not return the required schema")
		}
		captureOps(t, seen)
	})
	t.Run("NonRFCAndTrailingInputDomain", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		for _, raw := range []string{"NaN", "Infinity", "-Infinity", `{"checkpoint":NaN,"workstream":null,"memories":[]}`, `null {}`, `{"checkpoint":null,"workstream":null,"memories":[]} false`} {
			result, err := c.CommitCaptureSelection(context.Background(), memoryclient.CaptureInput{Selection: json.RawMessage(raw)})
			captureEmpty(t, result)
			if !errors.Is(err, memoryclient.ErrCaptureDomain) || errors.Is(err, memoryclient.ErrCaptureSelection) || errors.Is(err, memoryclient.ErrCaptureWrite) {
				t.Fatalf("out-of-domain JSON %q: %v", raw, err)
			}
		}
		captureOps(t, seen)
	})
	t.Run("RawUnicodeInputDomain", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		for _, raw := range []string{`{"checkpoint":"\ud800","workstream":null,"memories":[]}`, `{"checkpoint":null,"workstream":"\udc00","memories":[]}`, "{\"checkpoint\":null,\"workstream\":null,\"memories\":[],\"unused\":\"\xff\"}",
			`{"checkpoint":null,"workstream":null,"memories":[],"unused":"\ud800","unused":"valid"}`} {
			result, err := c.CommitCaptureSelection(context.Background(), memoryclient.CaptureInput{Selection: json.RawMessage(raw)})
			captureEmpty(t, result)
			if !errors.Is(err, memoryclient.ErrCaptureDomain) {
				t.Fatalf("invalid raw Unicode: %v", err)
			}
		}
		input := memoryclient.CaptureInput{Selection: json.RawMessage(`{"checkpoint":null,"workstream":null,"memories":[{"record_id":"old","kind":"decision","title":"","body":"\ud83d\ude00"}]}`),
			Candidates: []memoryclient.CaptureRecord{{RecordID: "old", Kind: "decision", Body: "😀"}}}
		if _, err := c.CommitCaptureSelection(context.Background(), input); err != nil {
			t.Fatalf("paired surrogate is in domain: %v", err)
		}
		captureOps(t, seen)
	})
	t.Run("EveryTypedStringInputDomain", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		for position := range 11 {
			input := memoryclient.CaptureInput{Selection: captureSelection(t, nil, nil), Previous: &memoryclient.CaptureRecord{},
				Candidates: []memoryclient.CaptureRecord{{}}, Handoffs: []memoryclient.CaptureRecord{{}}}
			fields := []*string{&input.ProjectBase, &input.ExplicitWorkstream, &input.Previous.RecordID, &input.Previous.Kind, &input.Previous.Body,
				&input.Candidates[0].RecordID, &input.Candidates[0].Kind, &input.Candidates[0].Body, &input.Handoffs[0].RecordID, &input.Handoffs[0].Kind, &input.Handoffs[0].Body}
			*fields[position] = "\xff"
			result, err := c.CommitCaptureSelection(context.Background(), input)
			captureEmpty(t, result)
			if !errors.Is(err, memoryclient.ErrCaptureDomain) {
				t.Fatalf("unused typed string %d: %v", position, err)
			}
		}
		if _, err := c.CommitCaptureSelection(context.Background(), memoryclient.CaptureInput{Selection: captureSelection(t, nil, nil), ProjectBase: "\"p\n"}); err != nil {
			t.Fatalf("quotes/LF are valid project input: %v", err)
		}
		captureOps(t, seen)
	})
	t.Run("TypedRecordCASLeftToService", func(t *testing.T) {
		for _, old := range []memoryclient.CaptureRecord{{RecordID: "", Version: 0, Kind: "decision"}, {RecordID: "typed", Version: -2, Kind: "decision"}} {
			api, seen := captureAPI(t, nil, func(w http.ResponseWriter, _ *http.Request, _ requestCapture) { captureServiceRefusal(t, w) })
			c := client(t, api, memoryclient.Options{Scope: scope})
			input := memoryclient.CaptureInput{Selection: captureSelection(t, nil, nil, captureNote(old.RecordID, "decision", "", "a")), Candidates: []memoryclient.CaptureRecord{old}}
			result, err := c.CommitCaptureSelection(context.Background(), input)
			captureEmpty(t, result)
			var service *core.Error
			if !errors.Is(err, memoryclient.ErrCaptureWrite) || !errors.As(err, &service) || service.Code != "CONFLICT" {
				t.Fatalf("service owns typed CAS refusal: %v", err)
			}
			captureOps(t, seen, "revise")
			body := decode(t, seen.snapshot()[0].body).(map[string]any)
			if body["record_id"] != old.RecordID || body["expected_version"] != json.Number(strconv.Itoa(old.Version)) {
				t.Fatalf("typed CAS fields changed: %s", seen.snapshot()[0].body)
			}
		}
	})
}

func TestCaptureSelectionReconciliation(t *testing.T) {
	t.Run("CandidateLastDuplicateWins", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		input := captureUpdates(t, "duplicate")
		input.Candidates = []memoryclient.CaptureRecord{{RecordID: "duplicate", Version: 2, Kind: "lesson", Body: "a"},
			{RecordID: "duplicate", Version: 8, Kind: "decision", Body: "before"}}
		if _, err := c.CommitCaptureSelection(context.Background(), input); err != nil {
			t.Fatal(err)
		}
		captureOps(t, seen, "revise")
		if version := decode(t, seen.snapshot()[0].body).(map[string]any)["expected_version"]; version != json.Number("8") {
			t.Fatalf("last candidate version: %v", version)
		}
		input.Candidates[1].Kind = "lesson"
		captureRefused(t, c, input, "memory selection referenced an unsupplied record")
		captureOps(t, seen, "revise")
	})
	t.Run("RepeatedKeysPreserveLookupOrder", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		input := memoryclient.CaptureInput{ProjectBase: "p", Selection: captureSelection(t, nil, nil,
			captureNote(nil, "decision", "T", "b"), captureNote(nil, "lesson", "T", "b"))}
		captureRefused(t, c, input, "memory selection repeated a topic")
		captureOps(t, seen, "version", "index", "version", "index")
		for _, index := range []int{1, 3} {
			if query := decode(t, seen.snapshot()[index].body).(map[string]any)["query"]; query != `"p: T"` {
				t.Fatalf("lookup before duplicate check: %v", query)
			}
		}
		input = captureUpdates(t, "same", "same")
		captureRefused(t, c, input, "memory selection repeated a topic")
		captureOps(t, seen, "version", "index", "version", "index")
	})
	t.Run("NewTopicEqualLookupNoop", func(t *testing.T) {
		api, seen := captureAPI(t, map[string]string{"p: T": "p: T\n\nb"}, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		result, err := c.CommitCaptureSelection(context.Background(), memoryclient.CaptureInput{ProjectBase: "p",
			Selection: captureSelection(t, nil, nil, captureNote(nil, "decision", "T", "b"))})
		if err != nil {
			t.Fatal(err)
		}
		captureEmpty(t, result)
		captureOps(t, seen, "version", "index", "expand")
	})
	t.Run("NewTopicDifferentLookupRefuses", func(t *testing.T) {
		api, seen := captureAPI(t, map[string]string{"p: T": "p: T\n\nother"}, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		captureRefused(t, c, memoryclient.CaptureInput{ProjectBase: "p", Selection: captureSelection(t, nil, nil, captureNote(nil, "decision", "T", "b"))},
			"matching topic needs reconciliation; use an explicit memory edit")
		captureOps(t, seen, "version", "index", "expand")
	})
	t.Run("HandoffOrderAndPreviousLast", func(t *testing.T) {
		first := memoryclient.CaptureRecord{RecordID: "first", Version: 2, Kind: "decision", Body: "Handoff: p / T\n\nfirst"}
		second := memoryclient.CaptureRecord{RecordID: "second", Version: 3, Kind: "note", Body: "Handoff: p / T\n\nsecond"}
		previous := memoryclient.CaptureRecord{RecordID: "previous", Version: 4, Kind: "note", Body: "Handoff: p / T\n\nprevious"}
		for _, tc := range []struct {
			handoffs []memoryclient.CaptureRecord
			want     memoryclient.CaptureRecord
		}{{[]memoryclient.CaptureRecord{first, second}, first}, {nil, previous},
			{[]memoryclient.CaptureRecord{{Body: "different title\n"}, second}, second}} {
			api, seen := captureAPI(t, nil, nil)
			c := client(t, api, memoryclient.Options{Scope: scope})
			result, err := c.CommitCaptureSelection(context.Background(), memoryclient.CaptureInput{ProjectBase: "p", Selection: captureSelection(t, "new", "T"),
				Handoffs: tc.handoffs, Previous: &previous})
			if err != nil || result.Workstream == nil || *result.Workstream != "Handoff: p / T" {
				t.Fatalf("ordered handoff: %+v, %v", result, err)
			}
			captureOps(t, seen, "revise")
			body := decode(t, seen.snapshot()[0].body).(map[string]any)
			if body["record_id"] != tc.want.RecordID || body["expected_version"] != json.Number(strconv.Itoa(tc.want.Version)) || body["body"] != "Handoff: p / T\n\nnew" {
				t.Fatalf("handoff order or body: %s", seen.snapshot()[0].body)
			}
		}
	})
	t.Run("CompletionRequiresSuppliedBeforeLookup", func(t *testing.T) {
		api, seen := captureAPI(t, map[string]string{"Handoff: p / T": "Handoff: p / T\n\nold"}, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		input := memoryclient.CaptureInput{ProjectBase: "p", Selection: captureJSON(t, map[string]any{
			"checkpoint": " done ", "checkpoint_state": "complete", "workstream": "T", "memories": []any{}})}
		captureRefused(t, c, input, "completion requires a supplied existing handoff")
		captureOps(t, seen)
		input.Previous = &memoryclient.CaptureRecord{RecordID: "prior", Version: 5, Body: "Handoff: p / T\n\nold"}
		if _, err := c.CommitCaptureSelection(context.Background(), input); err != nil {
			t.Fatal(err)
		}
		captureOps(t, seen, "revise")
		if body := decode(t, seen.snapshot()[0].body).(map[string]any)["body"]; body != "Handoff: p / T\n\nStatus: complete\ndone" {
			t.Fatalf("complete body: %q", body)
		}
	})
	t.Run("ExplicitWorkstreamBeforeTopicValidation", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		for _, topic := range []any{nil, false, "bad\n", "Other"} {
			captureRefused(t, c, memoryclient.CaptureInput{Selection: captureSelection(t, "cp", topic), ExplicitWorkstream: "chosen"},
				"selection changed the explicitly chosen workstream")
		}
		for _, explicit := range []string{"", "bad\n"} {
			captureRefused(t, c, memoryclient.CaptureInput{Selection: captureSelection(t, "cp", "bad\n"), ExplicitWorkstream: explicit},
				"memory selection returned an invalid workstream topic")
		}
		captureOps(t, seen)
	})
	t.Run("NullCheckpointRejectsWorkstream", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		for _, topic := range []any{"", "T", true} {
			captureRefused(t, c, memoryclient.CaptureInput{Selection: captureSelection(t, nil, topic)}, "workstream requires a checkpoint")
		}
		captureOps(t, seen)
		captureRefused(t, c, memoryclient.CaptureInput{ProjectBase: "p", Selection: captureSelection(t, nil, "T", captureNote(nil, "decision", "T", "b"))},
			"workstream requires a checkpoint")
		captureOps(t, seen, "version", "index")
	})
	t.Run("OpenHandoffDifferentLookupRefuses", func(t *testing.T) {
		api, seen := captureAPI(t, map[string]string{"Handoff: p / T": "Handoff: p / T\n\nold"}, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		captureRefused(t, c, memoryclient.CaptureInput{ProjectBase: "p", Selection: captureSelection(t, "new", "T")},
			"matching workstream needs reconciliation; use an explicit handoff edit")
		captureOps(t, seen, "version", "index", "expand")
	})
	t.Run("OpenHandoffBodyOnlyLookupNoop", func(t *testing.T) {
		api, seen := captureAPI(t, map[string]string{"Handoff:  / T": "Handoff:  / T\n\ncp"}, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		result, err := c.CommitCaptureSelection(context.Background(), memoryclient.CaptureInput{Selection: captureSelection(t, "cp", "T")})
		if err != nil || len(result.Saved) != 0 || result.Workstream == nil || *result.Workstream != "Handoff:  / T" {
			t.Fatalf("body-only handoff no-op: %+v, %v", result, err)
		}
		captureOps(t, seen, "version", "index", "expand")
	})
}

func TestCaptureCommitRequestIdentity(t *testing.T) {
	t.Run("CreateShapeScopeAndStableUUID", func(t *testing.T) {
		api, seen := captureAPI(t, nil, func(w http.ResponseWriter, _ *http.Request, _ requestCapture) {
			success(t, w, map[string]string{"record_id": "confirmed-by-reply"})
		})
		c := client(t, api, memoryclient.Options{Scope: scope, Context: &core.ContextPins{TaskClass: "must-not-copy"}})
		input := memoryclient.CaptureInput{ProjectBase: "p", Selection: captureSelection(t, nil, nil, captureNote(nil, "decision", "T", "a"))}
		// Literal CPython ensure_ascii=False name, including the final body's SHA-256.
		seed := `["fixture-repository","decision",null,0,"841cfdca1768b47b1afe2dd42a973307a9d6a1e9646c2a6a36b83c41346239eb"]`
		wantID := uuid.NewSHA1(uuid.NameSpaceURL, []byte(seed)).String()
		for range 2 { // Two caller invocations; neither invocation retries a call.
			result, err := c.CommitCaptureSelection(context.Background(), input)
			if err != nil || !reflect.DeepEqual(result.Saved, []string{"confirmed-by-reply"}) || result.Workstream != nil {
				t.Fatalf("create confirmation: %+v, %v", result, err)
			}
		}
		captureOps(t, seen, "version", "index", "create", "version", "index", "create")
		for _, position := range []int{2, 5} {
			requestBody(t, seen.snapshot()[position], map[string]any{"request_id": wantID, "draft": map[string]any{
				"kind": "decision", "body": "p: T\n\na", "scope": map[string]any{"repo": "fixture-repository", "task_id": "*", "run_id": "*"},
				"sensitivity": "shareable", "claim_type": "self"}})
		}
		var raw json.RawMessage
		if err := api.Call(context.Background(), "version", struct{}{}, &raw); err != nil {
			t.Fatalf("borrowed SDK after capture: %v", err)
		}
	})
	t.Run("ReviseCASIdentityAndVerbatimBody", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		input := captureUpdates(t, "old")
		result, err := c.CommitCaptureSelection(context.Background(), input)
		if err != nil || !reflect.DeepEqual(result.Saved, []string{recordID}) {
			t.Fatalf("revise: %+v, %v", result, err)
		}
		captureOps(t, seen, "revise")
		seed := `["fixture-repository","decision","old",7,"ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"]`
		requestBody(t, seen.snapshot()[0], map[string]any{"request_id": uuid.NewSHA1(uuid.NameSpaceURL, []byte(seed)).String(),
			"record_id": "old", "expected_version": 7, "repo": "fixture-repository", "body": "a"})
	})
	t.Run("UnicodeHTMLAndLineSeparatorEncoding", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		unicodeScope := scope
		unicodeScope.Repo = "quote\"\\\b\f\n\r\t\u0001\u001c\u007f\u2028\u2029<>&/é\\u0001"
		c := client(t, api, memoryclient.Options{Scope: unicodeScope})
		input := captureUpdates(t, `id\u0001`)
		if _, err := c.CommitCaptureSelection(context.Background(), input); err != nil {
			t.Fatal(err)
		}
		captureOps(t, seen, "revise")
		// The escape spellings are literal; the DEL/line separators/HTML/é are raw UTF-8.
		seed := `["quote\"\\\b\f\n\r\t\u0001\u001c` + "\u007f\u2028\u2029<>&/é" + `\\u0001","decision","id\\u0001",7,"ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"]`
		requestBody(t, seen.snapshot()[0], map[string]any{"request_id": uuid.NewSHA1(uuid.NameSpaceURL, []byte(seed)).String(),
			"record_id": `id\u0001`, "expected_version": 7, "repo": unicodeScope.Repo, "body": "a"})
	})
	t.Run("ProjectClipQuoteAndWorkstreamFirstLine", func(t *testing.T) {
		for _, tc := range []struct{ project, durable, handoff, firstLine string }{
			{strings.Repeat("a", 39) + "é", strings.Repeat("a", 39) + ": T\n\na", "Handoff: " + strings.Repeat("a", 39) + " / T\n\ncp", "Handoff: " + strings.Repeat("a", 39) + " / T"},
			{"\"" + strings.Repeat("a", 39) + "é", "\"" + strings.Repeat("a", 39) + ": T\n\na", "Handoff: " + strings.Repeat("a", 39) + " / T\n\ncp", "Handoff: " + strings.Repeat("a", 39) + " / T"},
			{"p\nq\"", "p\nq\": T\n\na", "Handoff: p\nq / T\n\ncp", "Handoff: p"},
		} {
			api, seen := captureAPI(t, nil, nil)
			c := client(t, api, memoryclient.Options{Scope: scope})
			result, err := c.CommitCaptureSelection(context.Background(), memoryclient.CaptureInput{ProjectBase: tc.project,
				Selection: captureSelection(t, "cp", "T", captureNote(nil, "decision", "T", "a"))})
			if err != nil || result.Workstream == nil || *result.Workstream != tc.firstLine {
				t.Fatalf("workstream first body line: %+v, %v", result, err)
			}
			captureOps(t, seen, "version", "index", "version", "index", "create", "create")
			for i, want := range []string{tc.durable, tc.handoff} {
				draft := decode(t, seen.snapshot()[4+i].body).(map[string]any)["draft"].(map[string]any)
				if draft["body"] != want || (i == 1 && draft["kind"] != "note") {
					t.Fatalf("literal prefix/clip oracle: %q, want %q", draft["body"], want)
				}
			}
		}
	})
	t.Run("ExactPriorBodyNoop", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		input := captureUpdates(t, "old")
		input.Candidates[0].Body = "a"
		result, err := c.CommitCaptureSelection(context.Background(), input)
		if err != nil {
			t.Fatal(err)
		}
		captureEmpty(t, result)
		captureOps(t, seen)
	})
}

func TestCaptureCommitPartialFailure(t *testing.T) {
	t.Run("FirstWriteFailureNoProgress", func(t *testing.T) {
		api, seen := captureAPI(t, nil, func(w http.ResponseWriter, _ *http.Request, _ requestCapture) { captureServiceRefusal(t, w) })
		c := client(t, api, memoryclient.Options{Scope: scope})
		result, err := c.CommitCaptureSelection(context.Background(), captureUpdates(t, "first", "later"))
		captureEmpty(t, result)
		if !errors.Is(err, memoryclient.ErrCaptureWrite) || core.Code(err) != "CONFLICT" {
			t.Fatalf("first write refusal: %v", err)
		}
		captureOps(t, seen, "revise")
	})
	t.Run("LaterWriteFailureSavedPrefix", func(t *testing.T) {
		position := 0
		api, seen := captureAPI(t, nil, func(w http.ResponseWriter, _ *http.Request, _ requestCapture) {
			position++
			if position == 1 {
				success(t, w, map[string]string{"record_id": "confirmed-first"})
			} else {
				captureServiceRefusal(t, w)
			}
		})
		c := client(t, api, memoryclient.Options{Scope: scope})
		result, err := c.CommitCaptureSelection(context.Background(), captureUpdates(t, "first", "second", "third"))
		if !errors.Is(err, memoryclient.ErrCaptureWrite) || !reflect.DeepEqual(result.Saved, []string{"confirmed-first"}) || result.Workstream != nil {
			t.Fatalf("partial progress: %+v, %v", result, err)
		}
		captureOps(t, seen, "revise", "revise")
	})
	t.Run("WorkstreamWriteFailureDoesNotAdvance", func(t *testing.T) {
		position := 0
		api, seen := captureAPI(t, nil, func(w http.ResponseWriter, _ *http.Request, _ requestCapture) {
			position++
			if position == 1 {
				success(t, w, map[string]string{"record_id": "durable"})
			} else {
				captureServiceRefusal(t, w)
			}
		})
		c := client(t, api, memoryclient.Options{Scope: scope})
		input := captureUpdates(t, "old")
		input.ProjectBase = "p"
		input.Selection = captureSelection(t, "cp", "T", captureNote("old", "decision", "", "a"))
		input.Handoffs = []memoryclient.CaptureRecord{{RecordID: "handoff", Version: 3, Body: "Handoff: p / T\n\nold"}}
		result, err := c.CommitCaptureSelection(context.Background(), input)
		if !errors.Is(err, memoryclient.ErrCaptureWrite) || !reflect.DeepEqual(result.Saved, []string{"durable"}) || result.Workstream != nil {
			t.Fatalf("failing handoff progress: %+v, %v", result, err)
		}
		captureOps(t, seen, "revise", "revise")
	})
	t.Run("WorkstreamNoopAdvancesWithoutSaved", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		result, err := c.CommitCaptureSelection(context.Background(), memoryclient.CaptureInput{ProjectBase: "p", Selection: captureSelection(t, "cp", "T"),
			Previous: &memoryclient.CaptureRecord{Body: "Handoff: p / T\n\ncp"}})
		if err != nil || len(result.Saved) != 0 || result.Workstream == nil || *result.Workstream != "Handoff: p / T" {
			t.Fatalf("handoff no-op progress: %+v, %v", result, err)
		}
		captureOps(t, seen)
	})
	t.Run("UnconfirmedRecordStops", func(t *testing.T) {
		for _, reply := range []json.RawMessage{[]byte("null"), []byte("[]"), []byte(`"scalar"`), []byte(`{}`), []byte(`{"record_id":null}`),
			[]byte(`{"record_id":true}`), []byte(`{"record_id":1}`), []byte(`{"record_id":""}`)} {
			position := 0
			api, seen := captureAPI(t, nil, func(w http.ResponseWriter, _ *http.Request, _ requestCapture) {
				position++
				if position == 1 {
					success(t, w, map[string]string{"record_id": "first"})
				} else {
					success(t, w, reply)
				}
			})
			c := client(t, api, memoryclient.Options{Scope: scope})
			result, err := c.CommitCaptureSelection(context.Background(), captureUpdates(t, "first", "second", "third"))
			if !errors.Is(err, memoryclient.ErrCaptureWrite) || !reflect.DeepEqual(result.Saved, []string{"first"}) || result.Workstream != nil {
				t.Fatalf("unconfirmed %s: %+v, %v", reply, result, err)
			}
			if reply[0] == '{' {
				if err.Error() != "Cairn did not confirm the selected record" || errors.Is(err, memoryclient.ErrNotConfirmed) {
					t.Fatalf("object confirmation refusal: %v", err)
				}
			} else if !errors.Is(err, memoryclient.ErrNotConfirmed) {
				t.Fatalf("nonobject confirmation refusal: %v", err)
			}
			captureOps(t, seen, "revise", "revise")
		}
	})
	t.Run("PlanLookupFailureNoRecordWrites", func(t *testing.T) {
		api, seen := fixture(t, func(w http.ResponseWriter, _ *http.Request, call requestCapture) {
			if !probe(t, w, call) {
				captureServiceRefusal(t, w)
			}
		})
		c := client(t, api, memoryclient.Options{Scope: scope})
		input := captureUpdates(t, "old")
		input.ProjectBase = "p"
		input.Selection = captureSelection(t, nil, nil, captureNote("old", "decision", "", "a"), captureNote(nil, "lesson", "T", "b"))
		result, err := c.CommitCaptureSelection(context.Background(), input)
		captureEmpty(t, result)
		if _, directService := err.(*core.Error); !directService || core.Code(err) != "CONFLICT" || errors.Is(err, memoryclient.ErrCaptureWrite) || errors.Is(err, memoryclient.ErrCaptureSelection) {
			t.Fatalf("direct lookup error before every mutation: %v", err)
		}
		captureOps(t, seen, "version", "index")
	})
}

func TestCaptureCommitCancellationAndAttribution(t *testing.T) {
	t.Run("CanceledBeforeWriteNoIO", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		result, err := c.CommitCaptureSelection(ctx, captureUpdates(t, "old", "later"))
		captureEmpty(t, result)
		if !errors.Is(err, memoryclient.ErrCaptureWrite) || !errors.Is(err, context.Canceled) {
			t.Fatalf("canceled write cause: %v", err)
		}
		captureOps(t, seen)
	})
	t.Run("EnclosingDeadlineCapsCall", func(t *testing.T) {
		second := make(chan struct{})
		position := 0
		api, seen := captureAPI(t, nil, func(w http.ResponseWriter, r *http.Request, _ requestCapture) {
			position++
			if position == 1 {
				success(t, w, map[string]string{"record_id": "first"})
			} else {
				close(second)
				<-r.Context().Done()
			}
		})
		c := client(t, api, memoryclient.Options{Scope: scope})
		input := captureUpdates(t, "first", "second", "third")
		ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
		defer cancel()
		finished := make(chan error, 1)
		var result memoryclient.CaptureResult
		go func() {
			var err error
			result, err = c.CommitCaptureSelection(ctx, input)
			finished <- err
		}()
		awaitSignal(t, second)
		if err := awaitError(t, finished); !errors.Is(err, memoryclient.ErrCaptureWrite) || !errors.Is(err, context.DeadlineExceeded) {
			t.Fatalf("shared enclosing deadline: %v", err)
		}
		if !reflect.DeepEqual(result.Saved, []string{"first"}) || result.Workstream != nil {
			t.Fatalf("deadline partial result: %+v", result)
		}
		captureOps(t, seen, "revise", "revise")
	})
	t.Run("SessionDiagnosticsAndNoPayloadAttribution", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		session := core.AgentSessionRef{AgentID: recordID, ExecutionID: handleID}
		diagnostics := localapi.ClientDiagnostics{Surface: "cli", Harness: "unknown", Origin: &localapi.ClientOrigin{Component: "lifecycle-memory", Basis: "reported"}}
		c := client(t, api.ForAgentSession(session), memoryclient.Options{Scope: scope, Diagnostics: diagnostics,
			Context: &core.ContextPins{TaskClass: "never-copy"}, CommandPrefix: []string{"never-executed"}})
		input := captureUpdates(t, "old")
		input.ProjectBase = "p"
		input.Selection = captureSelection(t, nil, nil, captureNote(nil, "decision", "T", "a"), captureNote("old", "decision", "", "a"))
		if _, err := c.CommitCaptureSelection(context.Background(), input); err != nil {
			t.Fatal(err)
		}
		captureOps(t, seen, "version", "index", "create", "revise")
		for _, call := range seen.snapshot() {
			assertHeader(t, call, diagnostics, &session)
			if call.operation != "create" && call.operation != "revise" {
				continue
			}
			body := decode(t, call.body).(map[string]any)
			members := []map[string]any{body}
			if draft, ok := body["draft"].(map[string]any); ok {
				members = append(members, draft)
			}
			for _, fields := range members {
				for _, key := range []string{"attributed_producer", "observed_writer", "attempt_id", "result_ref", "entities", "relations", "context", "pins", "principal", "session", "agent_id", "execution_id"} {
					if _, present := fields[key]; present {
						t.Fatalf("write payload acquired %q", key)
					}
				}
			}
		}
	})
	t.Run("TransportCauseAndNoRetry", func(t *testing.T) {
		for _, truncate := range []bool{true, false} {
			api, seen := captureAPI(t, nil, func(w http.ResponseWriter, _ *http.Request, _ requestCapture) {
				if truncate {
					w.Header().Set("Content-Length", "500")
					if _, err := io.WriteString(w, `{"schema":"cairn.response/1"`); err != nil {
						t.Error(err)
					}
					return
				}
				connection, _, err := w.(http.Hijacker).Hijack()
				if err != nil {
					t.Error(err)
					return
				}
				if err := connection.Close(); err != nil {
					t.Error(err)
				}
			})
			c := client(t, api, memoryclient.Options{Scope: scope})
			result, err := c.CommitCaptureSelection(context.Background(), captureUpdates(t, "old", "later"))
			captureEmpty(t, result)
			if !errors.Is(err, memoryclient.ErrCaptureWrite) || err.Error() != "capture write failed" {
				t.Fatalf("fixed transport diagnostic: %v", err)
			}
			var transport *url.Error
			if truncate && !errors.Is(err, io.ErrUnexpectedEOF) {
				t.Fatalf("truncation cause lost: %v", err)
			}
			if !truncate && !errors.As(err, &transport) {
				t.Fatalf("network cause lost: %v", err)
			}
			captureOps(t, seen, "revise")
		}
	})
	t.Run("TypedServiceRefusalAndPhase", func(t *testing.T) {
		for _, lookup := range []bool{true, false} {
			api, seen := fixture(t, func(w http.ResponseWriter, _ *http.Request, call requestCapture) {
				if !probe(t, w, call) {
					captureServiceRefusal(t, w)
				}
			})
			c := client(t, api, memoryclient.Options{Scope: scope})
			input := captureUpdates(t, "old")
			if lookup {
				input = memoryclient.CaptureInput{Selection: captureSelection(t, nil, nil, captureNote(nil, "decision", "T", "a"))}
			}
			result, err := c.CommitCaptureSelection(context.Background(), input)
			captureEmpty(t, result)
			var service *core.Error
			if !errors.As(err, &service) || service.Code != "CONFLICT" || service.RefusalID != "capture-fixture" || errors.Is(err, memoryclient.ErrCaptureSelection) {
				t.Fatalf("typed service cause: %v", err)
			}
			if lookup {
				if err != service || errors.Is(err, memoryclient.ErrCaptureWrite) {
					t.Fatalf("lookup error was wrapped: %v", err)
				}
				captureOps(t, seen, "version", "index")
			} else {
				if !errors.Is(err, memoryclient.ErrCaptureWrite) || errors.Unwrap(err) != service {
					t.Fatalf("write phase cause: %v", err)
				}
				captureOps(t, seen, "revise")
			}
		}
	})
}

func TestCaptureCommitAccounting(t *testing.T) {
	t.Run("WritesDoNotChangeRecallAccounting", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		before := c.Accounting()
		if _, err := c.CommitCaptureSelection(context.Background(), captureUpdates(t, "first", "second")); err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(c.Accounting(), before) {
			t.Fatalf("revision added recall accounting: %+v", c.Accounting())
		}
		captureOps(t, seen, "revise", "revise")
		if _, err := c.CommitCaptureSelection(context.Background(), memoryclient.CaptureInput{ProjectBase: "p",
			Selection: captureSelection(t, nil, nil, captureNote(nil, "decision", "T", "a"))}); err != nil {
			t.Fatal(err)
		}
		want := memoryclient.Accounting{OperationCalls: map[string]int{"search": 1, "pull": 0}, PullCalls: map[string]int{},
			ReceiptCredits: map[string]int{}, SearchReceipts: []string{receiptID}}
		if !reflect.DeepEqual(c.Accounting(), want) {
			t.Fatalf("create should add only its validation lookup: %+v", c.Accounting())
		}
		captureOps(t, seen, "revise", "revise", "version", "index", "create")
	})
	t.Run("LookupUsesCheckpointAccounting", func(t *testing.T) {
		api, seen := captureAPI(t, map[string]string{"p: T": "p: T\n\nb"}, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		if _, err := c.CommitCaptureSelection(context.Background(), memoryclient.CaptureInput{ProjectBase: "p",
			Selection: captureSelection(t, nil, nil, captureNote(nil, "decision", "T", "b"))}); err != nil {
			t.Fatal(err)
		}
		want := memoryclient.Accounting{OperationCalls: map[string]int{"search": 1, "pull": 1}, PullCalls: map[string]int{receiptID: 1},
			ReceiptCredits: map[string]int{receiptID: 3}, SearchReceipts: []string{receiptID}}
		if !reflect.DeepEqual(c.Accounting(), want) {
			t.Fatalf("checkpoint lookup accounting: %+v", c.Accounting())
		}
		captureOps(t, seen, "version", "index", "expand")
	})
	t.Run("SuppliedNoopHasNoLookupOrWrite", func(t *testing.T) {
		api, seen := captureAPI(t, nil, nil)
		c := client(t, api, memoryclient.Options{Scope: scope})
		before := c.Accounting()
		input := captureUpdates(t, "old")
		input.ProjectBase = "p"
		input.Candidates[0].Body = "a"
		input.Selection = captureSelection(t, "cp", "T", captureNote("old", "decision", "", "a"))
		input.Handoffs = []memoryclient.CaptureRecord{{Body: "Handoff: p / T\n\ncp"}}
		result, err := c.CommitCaptureSelection(context.Background(), input)
		if err != nil || len(result.Saved) != 0 || result.Workstream == nil || !reflect.DeepEqual(c.Accounting(), before) {
			t.Fatalf("supplied no-op accounting/progress: %+v, %+v, %v", c.Accounting(), result, err)
		}
		captureOps(t, seen)
	})
}
