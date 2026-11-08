package journal

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"
)

const attemptID = "c1a2b3c4-d5e6-4f70-8a91-b2c3d4e5f607"
const agentID = "a5d7b51e-6f47-4e5e-9a55-2b0d6b3f2a11"
const resultID = "9a2b3c4d-5e6f-4a70-8b91-c2d3e4f50617"

func dict(t *testing.T, v any) map[string]any {
	t.Helper()
	m, err := object(v, "test value")
	if err != nil {
		t.Fatal(err)
	}
	return m
}

func parse(t *testing.T, data string) any {
	t.Helper()
	var v any
	d := json.NewDecoder(strings.NewReader(data))
	d.UseNumber()
	if err := d.Decode(&v); err != nil {
		t.Fatal(err)
	}
	return v
}

func golden(t *testing.T, name string) any {
	t.Helper()
	// This package is three levels below the repository root.
	b, err := os.ReadFile(filepath.Join("..", "..", "..", "scripts", "fixtures", "inbox_characterization_v1.json"))
	if err != nil {
		t.Fatal(err)
	}
	return dict(t, dict(t, parse(t, string(b)))["traces"])[name]
}

func normalize(v any) any {
	switch v := value(v).(type) {
	case map[string]any:
		m := make(map[string]any, len(v))
		for key, v := range v {
			if key == "recorded_at" {
				m[key] = "$TIME"
			} else {
				m[key] = normalize(v)
			}
		}
		return m
	case []any:
		a := make([]any, len(v))
		for i, v := range v {
			a[i] = normalize(v)
		}
		return a
	}
	return value(v)
}

func assertJSON(t *testing.T, got, want any) {
	t.Helper()
	canonical := func(v any) any {
		b, err := json.Marshal(normalize(v))
		if err != nil {
			t.Fatal(err)
		}
		var result any
		d := json.NewDecoder(strings.NewReader(string(b)))
		d.UseNumber()
		if err := d.Decode(&result); err != nil {
			t.Fatal(err)
		}
		return result
	}
	if a, b := canonical(got), canonical(want); !reflect.DeepEqual(a, b) {
		gotJSON, _ := json.MarshalIndent(a, "", "  ")
		wantJSON, _ := json.MarshalIndent(b, "", "  ")
		t.Fatalf("got:\n%s\nwant:\n%s", gotJSON, wantJSON)
	}
}

func fixtureContext(t *testing.T) Context {
	t.Helper()
	c := dict(t, dict(t, golden(t, "finish_reply"))["context"])
	n := Context{StateDir: t.TempDir(), AttemptID: text(c["attempt_id"]), DeliveryID: text(c["delivery_id"]),
		Kind: text(c["kind"]), Session: Session{AgentID: text(c["agent_id"]), ExecutionID: text(c["execution_id"])},
		Commands: map[string][]string{}, RequestIDs: map[string]string{}}
	for k, v := range dict(t, c["commands"]) {
		argv, err := stringList(v, k)
		if err != nil {
			t.Fatal(err)
		}
		n.Commands[k] = argv
	}
	for k, v := range dict(t, c["request_ids"]) {
		n.RequestIDs[k] = text(v)
	}
	return n
}

func put(t *testing.T, stateDir, id string, v any) {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	putRaw(t, stateDir, id, string(b))
}

func putRaw(t *testing.T, stateDir, id, data string) {
	t.Helper()
	target := path(stateDir, id)
	if err := os.MkdirAll(filepath.Dir(target), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(target, []byte(data), 0600); err != nil {
		t.Fatal(err)
	}
}

func read(t *testing.T, n Context) map[string]any {
	t.Helper()
	v, err := load(path(n.StateDir, n.AttemptID))
	if err != nil {
		t.Fatal(err)
	}
	return dict(t, v)
}

func handled(t *testing.T) Result {
	t.Helper()
	v := dict(t, dict(t, golden(t, "explicit_context_grammar"))["complete"])["output"]
	b, err := encode(v)
	if err != nil {
		t.Fatal(err)
	}
	return Result{Stdout: string(b)}
}

func published() Result {
	return Result{Stdout: `{"schema":"cairn.response/1","ok":true,"status":"OK","data":{"event_id":"f1a2b3c4-d5e6-4f70-8a91-b2c3d4e5f607","ref":{"record_id":"` + resultID + `","version":1}}}`}
}

func refusal(code string, exit int) Result {
	b, _ := json.Marshal(map[string]any{"schema": "cairn.response/1", "ok": false, "status": code})
	return Result{Stdout: string(b), ExitCode: exit}
}

type scripted struct {
	t         *testing.T
	replies   []Result
	calls     []any
	deadlines []time.Duration
	before    func([]string)
}

func (s *scripted) Run(ctx context.Context, argv []string, input string) (Result, error) {
	if s.before != nil {
		s.before(argv)
	}
	deadline, ok := ctx.Deadline()
	if !ok {
		s.t.Error("command has no timeout")
	}
	s.deadlines = append(s.deadlines, time.Until(deadline))
	s.calls = append(s.calls, map[string]any{"argv": append([]string(nil), argv[1:]...), "input": input, "operation": argv[1]})
	if len(s.replies) == 0 {
		s.t.Error("unexpected command", argv)
		return Result{}, errors.New("unexpected command")
	}
	result := s.replies[0]
	if len(s.replies) > 1 {
		s.replies = s.replies[1:]
	}
	return result, nil
}

func requireError(t *testing.T, err error, code string, exit int) {
	t.Helper()
	var public *Error
	if !errors.As(err, &public) || public.Code != code || public.ExitCode != exit {
		t.Fatalf("error = %v; want %s/%d", err, code, exit)
	}
}

func observed(v any, err error) any {
	if err == nil {
		return v
	}
	name := ""
	var nonObject *NonObjectError
	var key *KeyError
	var typ *TypeError
	var syntax *json.SyntaxError
	var pathError *os.PathError
	switch {
	case errors.As(err, &nonObject):
		name = "AttributeError"
	case errors.As(err, &key):
		name = "KeyError"
	case errors.As(err, &typ):
		name = "TypeError"
	case errors.As(err, &syntax):
		name = "JSONDecodeError"
	case errors.As(err, &pathError) && errors.Is(err, syscall.EISDIR):
		name = "IsADirectoryError"
	default:
		name = err.Error()
	}
	return map[string]any{"raises": name}
}

func TestMissingJournalReaderPolicies(t *testing.T) {
	dir := t.TempDir()
	status := Inspect(dir, "not-created")
	if status.Exists || !status.ClaimOnly {
		t.Fatalf("missing journal: %+v", status)
	}
	if !SettledJournalIndex(dir, map[string]any{"inbox_journals": []any{"not-created"}}) {
		t.Fatal("the legacy index treats a missing file as settled")
	}
}

func TestGoldenReaderPoliciesAndNoInspectionEffects(t *testing.T) {
	rows := golden(t, "v5_reader_policies").([]any)
	for _, v := range rows {
		want := dict(t, v)
		name := text(want["case"])
		t.Run(name, func(t *testing.T) {
			dir := t.TempDir()
			base := map[string]any{"schema": Schema, "attempt_id": attemptID, "session": map[string]any{"agent_id": agentID, "execution_id": "old-execution"}}
			switch name {
			case "missing":
			case "corrupt":
				putRaw(t, dir, attemptID, "{")
			case "non_object":
				putRaw(t, dir, attemptID, "[]")
			case "directory":
				if err := os.MkdirAll(path(dir, attemptID), 0700); err != nil {
					t.Fatal(err)
				}
			default:
				completion := map[string]string{"completion_null": "null", "completion_false": "false", "completion_zero": "0",
					"completion_empty_list": "[]", "completion_empty_string": `""`, "completion_string": `"bad"`, "completion_list": "[1]",
					"completion_pending": `{"status":"pending"}`, "completion_committed": `{"status":"committed"}`, "completion_refused": `{"status":"refused"}`}
				if raw, ok := completion[name]; ok {
					base["completion"] = parse(t, raw)
				} else {
					switch name {
					case "extension":
						base["extension"] = true
					case "non_dict_session":
						base["session"] = []any{"bad"}
					case "ownerless_non_dict_slot":
						base = map[string]any{"attempt_id": attemptID, "completion": []any{"bad"}}
					case "missing_attempt_id":
						base = map[string]any{"session": base["session"]}
					default:
						t.Fatal("unhandled golden reader cell", name)
					}
				}
				put(t, dir, attemptID, base)
			}
			before, beforeErr := os.ReadFile(path(dir, attemptID))
			s := Inspect(dir, attemptID)
			committed, commitErr := s.Committed()
			state, stateErr := s.CompletionState()
			seeded, seedErr := SessionJournals(dir, map[string]any{"agent": map[string]any{"agent_id": agentID}})
			got := map[string]any{"case": name, "claim_only": s.ClaimOnly, "committed": observed(committed, commitErr),
				"completion_state": observed(state, stateErr), "seeded": observed(seeded, seedErr),
				"settled": SettledJournalIndex(dir, map[string]any{"inbox_journals": []any{attemptID}})}
			assertJSON(t, got, want)
			after, afterErr := os.ReadFile(path(dir, attemptID))
			if string(before) != string(after) || (beforeErr == nil) != (afterErr == nil) {
				t.Fatal("reader changed journal bytes or existence")
			}
			if _, err := os.Stat(strings.TrimSuffix(path(dir, attemptID), ".json") + ".lock"); !errors.Is(err, os.ErrNotExist) {
				t.Fatal("inspection created or opened a lock", err)
			}
		})
	}
}

func TestGoldenRefusalsAndPendingOnlyRecovery(t *testing.T) {
	rows := golden(t, "refusal_matrix").([]any)
	final := 0
	for _, v := range rows {
		want := dict(t, v)
		outcome := text(want["outcome"])
		if FinalRefusal(outcome) {
			final++
		}
		t.Run(outcome, func(t *testing.T) {
			n := fixtureContext(t)
			reply := refusal(outcome, 4)
			if outcome == "non_json" {
				reply = Result{Stdout: "not JSON", ExitCode: 7}
			}
			runner := &scripted{t: t, replies: []Result{reply}}
			result, err := Execute(context.Background(), n, Request{Kind: "complete", Input: "Selected result"}, Options{Runner: runner})
			if err != nil {
				t.Fatal(err)
			}
			recovery, err := Recover(context.Background(), n.StateDir, n.AttemptID, Options{Runner: runner})
			if err != nil {
				t.Fatal(err)
			}
			slot := dict(t, read(t, n)["completion"])
			assertJSON(t, map[string]any{"api_sends": len(runner.calls), "code": slot["code"], "exit": result.ExitCode,
				"outcome": outcome, "status": slot["status"], "watcher_pending": recovery.Pending}, want)
			if len(runner.deadlines) == 0 || runner.deadlines[0] > 30*time.Second || runner.deadlines[0] < 29*time.Second {
				t.Fatal("explicit send did not receive 30 seconds", runner.deadlines)
			}
			if len(runner.deadlines) > 1 && (runner.deadlines[1] > 10*time.Second || runner.deadlines[1] < 9*time.Second) {
				t.Fatal("recovery send did not receive 10 seconds", runner.deadlines)
			}
		})
	}
	if final != 12 || FinalRefusal("FUTURE_REFUSAL") {
		t.Fatal("final refusal set differs from the closed golden set")
	}
}

func TestGoldenPassThroughStreams(t *testing.T) {
	for _, v := range golden(t, "v5_passthrough_streams").([]any) {
		want := dict(t, v)
		t.Run(text(want["case"]), func(t *testing.T) {
			n := fixtureContext(t)
			exit, _ := integer(want["exit"])
			reply := Result{Stdout: text(want["stdout"]), Stderr: text(want["stderr"]), ExitCode: int(exit.Int64())}
			runner := &scripted{t: t, replies: []Result{reply}}
			result, err := Execute(context.Background(), n, Request{Kind: "complete", Input: "Selected result"}, Options{Runner: runner})
			if err != nil || result != reply {
				t.Fatalf("pass-through changed: %+v, %v", result, err)
			}
			slot := dict(t, read(t, n)["completion"])
			assertJSON(t, map[string]any{"case": want["case"], "stdout": result.Stdout, "stderr": result.Stderr,
				"exit": result.ExitCode, "code": slot["code"], "status": slot["status"]}, want)
		})
	}
}

func TestGoldenFinishReplyAndWriteBeforeSend(t *testing.T) {
	n := fixtureContext(t)
	want := dict(t, golden(t, "finish_reply"))
	runner := &scripted{t: t, replies: []Result{handled(t), published()}}
	var before []any
	runner.before = func(argv []string) {
		journal := read(t, n)
		before = append(before, map[string]any{"directory_mode": "0o700", "file_mode": "0o600", "journal": normalize(journal)})
		if !truth(journal["finish_reply"]) || !truth(journal["completion"]) {
			t.Fatal("command ran without both durable intents")
		}
		for _, target := range []string{filepath.Join(n.StateDir, "intents"), path(n.StateDir, n.AttemptID)} {
			info, err := os.Stat(target)
			if err != nil {
				t.Fatal(err)
			}
			mode := os.FileMode(0600)
			if info.IsDir() {
				mode = 0700
			}
			if info.Mode().Perm() != mode {
				t.Fatalf("%s mode = %o", target, info.Mode().Perm())
			}
		}
	}
	result, err := Execute(context.Background(), n, Request{Kind: "finish-reply", Input: "Selected result"}, Options{Runner: runner})
	if err != nil {
		t.Fatal(err)
	}
	assertJSON(t, parse(t, result.Stdout), want["output"])
	assertJSON(t, result.ExitCode, want["exit"])
	assertJSON(t, read(t, n), want["journal"])
	assertJSON(t, runner.calls, want["api"])
	assertJSON(t, before, want["before_sends"])
}

func TestGoldenFinishPriorityAndReplayFencing(t *testing.T) {
	for _, v := range golden(t, "v6_finish_reply_priority").([]any) {
		want := dict(t, v)
		name := text(want["case"])
		t.Run(name, func(t *testing.T) {
			n := fixtureContext(t)
			runner := &scripted{t: t, replies: []Result{handled(t), published()}}
			switch name {
			case "completion_refused":
				runner.replies[0] = refusal("STALE_LEASE", 4)
			case "completion_lost":
				runner.replies[0] = refusal("API_CONNECTION_FAILED", 7)
			case "publication_refused":
				runner.replies[1] = refusal("DESTINATION_PROHIBITED", 4)
			}
			result, err := Execute(context.Background(), n, Request{Kind: "finish-reply", Input: "Selected result"}, Options{Runner: runner})
			if name == "changed_input" {
				runner.calls = nil
				_, err = Execute(context.Background(), n, Request{Kind: "finish-reply", Input: "Changed result"}, Options{Runner: runner})
				requireError(t, err, "IDEMPOTENCY_CONFLICT", 4)
				assertJSON(t, len(runner.calls), 0)
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			assertJSON(t, result.ExitCode, want["exit"])
			assertJSON(t, parse(t, result.Stdout), want["output"])
			operations := []any{}
			for _, call := range runner.calls {
				operations = append(operations, dict(t, call)["operation"])
			}
			assertJSON(t, operations, want["operations"])
		})
	}
	t.Run("both committed then stale publication replay", func(t *testing.T) {
		n := fixtureContext(t)
		runner := &scripted{t: t, replies: []Result{handled(t), published()}}
		if _, err := Execute(context.Background(), n, Request{Kind: "finish-reply", Input: "Selected result"}, Options{Runner: runner}); err != nil {
			t.Fatal(err)
		}
		runner.replies = []Result{refusal("STALE_SESSION", 4)}
		result, err := Execute(context.Background(), n, Request{Kind: "finish-reply", Input: "Selected result"}, Options{Runner: runner})
		if err != nil {
			t.Fatal(err)
		}
		want := dict(t, golden(t, "v5_finish_reply_stale_replay"))
		assertJSON(t, parse(t, result.Stdout), want["output"])
		assertJSON(t, result.ExitCode, want["exit"])
		assertJSON(t, read(t, n), want["journal"])
	})
}

func TestGoldenExplicitFinalRetriesAndInlineConflict(t *testing.T) {
	for _, v := range golden(t, "explicit_final_retries").([]any) {
		want := dict(t, v)
		t.Run(text(dict(t, want["original"])["status"]), func(t *testing.T) {
			n := fixtureContext(t)
			initial, _ := encode(want["initial"])
			runner := &scripted{t: t, replies: []Result{{Stdout: string(initial)}}}
			if text(dict(t, want["original"])["status"]) == "refused" {
				runner.replies[0].ExitCode = 4
			}
			if _, err := Execute(context.Background(), n, Request{Kind: "complete", Input: "Selected result"}, Options{Runner: runner}); err != nil {
				t.Fatal(err)
			}
			recovery, err := Recover(context.Background(), n.StateDir, n.AttemptID, Options{Runner: runner})
			if err != nil || len(runner.calls) != 1 {
				t.Fatal("recovery replayed final completion", err, runner.calls)
			}
			assertJSON(t, recovery.Pending, want["watcher_pending"])
			for _, v := range want["retries"].([]any) {
				retry := dict(t, v)
				runner.replies = []Result{refusal(text(retry["replay_code"]), 4)}
				result, err := Execute(context.Background(), n, Request{Kind: "complete", Input: "Selected result"}, Options{Runner: runner})
				if err != nil {
					t.Fatal(err)
				}
				slot := dict(t, read(t, n)["completion"])
				assertJSON(t, map[string]any{"exit": result.ExitCode, "reply": parse(t, result.Stdout), "status": slot["status"], "code": slot["code"], "replay_code": slot["replay_code"]}, retry)
			}
			_, err = Execute(context.Background(), n, Request{Kind: "complete", Input: "Changed result"}, Options{Runner: runner})
			requireError(t, err, "IDEMPOTENCY_CONFLICT", 4)
			assertJSON(t, len(runner.calls), want["api_sends"])
			for _, call := range runner.calls {
				assertJSON(t, call, want["api_request"])
			}
		})
	}
}

func TestGoldenExactResultRecoveryAndPersistentDiscrepancy(t *testing.T) {
	for _, v := range golden(t, "missing_result_recovery").([]any) {
		want := dict(t, v)
		t.Run(text(want["recovery"]), func(t *testing.T) {
			n := fixtureContext(t)
			put(t, n.StateDir, n.AttemptID, want["before"])
			runner := &scripted{t: t, replies: []Result{handled(t), published()}}
			if text(want["recovery"]) != "OK" {
				runner.replies = []Result{refusal("API_CONNECTION_FAILED", 7)}
			}
			result, err := Recover(context.Background(), n.StateDir, n.AttemptID, Options{Runner: runner})
			if err != nil {
				t.Fatal(err)
			}
			assertJSON(t, result.Pending, want["watcher_pending"])
			assertJSON(t, read(t, n), want["after"])
			assertJSON(t, SettledJournalIndex(n.StateDir, map[string]any{"inbox_journals": []any{n.AttemptID}}), want["settled_index"])
			if text(want["recovery"]) != "OK" {
				for range 2 {
					if _, err := Recover(context.Background(), n.StateDir, n.AttemptID, Options{Runner: runner}); err != nil {
						t.Fatal(err)
					}
				}
				if len(runner.calls) != 3 || !Inspect(n.StateDir, n.AttemptID).UnmaterializedFinishReply {
					t.Fatal("persistent missing-ref compatibility behavior changed")
				}
			}
		})
	}
}

func TestGoldenExpectedRefMismatchAndMaterializedConflict(t *testing.T) {
	t.Run("published reference mismatch then recovery", func(t *testing.T) {
		n := fixtureContext(t)
		want := dict(t, golden(t, "reply_reference_recovery"))
		bad := published()
		bad.Stdout = strings.Replace(bad.Stdout, `"version":1`, `"version":9`, 1)
		runner := &scripted{t: t, replies: []Result{handled(t), bad}}
		result, err := Execute(context.Background(), n, Request{Kind: "finish-reply", Input: "Selected result"}, Options{Runner: runner})
		if err != nil {
			t.Fatal(err)
		}
		assertJSON(t, result.ExitCode, want["exit"])
		assertJSON(t, parse(t, result.Stdout), want["output"])
		assertJSON(t, read(t, n), want["before"])
		runner.replies = []Result{published()}
		recovery, err := Recover(context.Background(), n.StateDir, n.AttemptID, Options{Runner: runner})
		if err != nil {
			t.Fatal(err)
		}
		assertJSON(t, recovery.Pending, want["watcher_pending"])
		assertJSON(t, read(t, n), want["after"])
		assertJSON(t, runner.calls, want["api"])
	})
	t.Run("materialized slot identity differs", func(t *testing.T) {
		n := fixtureContext(t)
		journal := dict(t, dict(t, golden(t, "finish_reply"))["journal"])
		dict(t, journal["response"])["request_id"] = "changed"
		put(t, n.StateDir, n.AttemptID, journal)
		runner := &scripted{t: t}
		_, err := Execute(context.Background(), n, Request{Kind: "finish-reply", Input: "Selected result"}, Options{Runner: runner})
		requireError(t, err, "IDEMPOTENCY_CONFLICT", 7)
		if len(runner.calls) != 0 {
			t.Fatal("materialized conflict sent a command")
		}
	})
}

func TestGoldenExplicitRespondShapes(t *testing.T) {
	for _, v := range golden(t, "v5_explicit_respond").([]any) {
		want := dict(t, v)
		name := text(want["case"])
		t.Run(name, func(t *testing.T) {
			n := fixtureContext(t)
			completion := dict(t, dict(t, golden(t, "finish_reply"))["journal"])["completion"]
			journal := map[string]any{"completion": completion}
			version := "1"
			switch name {
			case "leading_zero":
				version = "01"
			case "missing_version":
				dict(t, completion)["result"] = map[string]any{"record_id": resultID}
			case "non_dict_result":
				dict(t, completion)["result"] = []any{"bad"}
			}
			put(t, n.StateDir, n.AttemptID, journal)
			runner := &scripted{t: t, replies: []Result{{Stdout: `{"schema":"cairn.response/1","ok":true,"status":"OK","data":{"event_id":"not-a-uuid","ref":{"record_id":"` + resultID + `","version":9}}}`}}}
			result, err := Execute(context.Background(), n, Request{Kind: "respond", Version: []string{version, resultID}}, Options{Runner: runner})
			if name == "explicit_legacy_shape" {
				if err != nil {
					t.Fatal(err)
				}
				assertJSON(t, result.ExitCode, want["exit"])
				assertJSON(t, parse(t, result.Stdout), want["output"])
				slot := dict(t, read(t, n)["response"])
				if _, present := slot["expected_ref"]; present {
					t.Fatal("explicit response gained expected_ref")
				}
				assertJSON(t, slot, want["response"])
				// request_id is not part of the explicit retry comparison.
				n.RequestIDs["response"] = "different-context-metadata"
				if _, err := Execute(context.Background(), n, Request{Kind: "respond", Version: []string{version, resultID}}, Options{Runner: runner}); err != nil {
					t.Fatal("explicit retry compared request_id", err)
				}
			} else {
				exit, _ := integer(want["exit"])
				requireError(t, err, text(dict(t, want["output"])["status"]), int(exit.Int64()))
				assertJSON(t, len(runner.calls), want["api_sends"])
			}
		})
	}
}

func TestDurablePublicationFaultsPreventSend(t *testing.T) {
	for _, phase := range []string{"write", "short_write", "file_sync", "file_close", "rename", "directory_sync", "directory_close"} {
		t.Run(phase, func(t *testing.T) {
			n := fixtureContext(t)
			fault := errors.New("injected " + phase)
			calls := IO{}
			isDir := func(f *os.File) bool {
				info, err := f.Stat()
				if err != nil {
					t.Fatal(err)
				}
				return info.IsDir()
			}
			switch phase {
			case "write":
				calls.Write = func(*os.File, []byte) (int, error) { return 0, fault }
			case "short_write":
				calls.Write = func(_ *os.File, data []byte) (int, error) { return len(data) - 1, nil }
				fault = io.ErrShortWrite
			case "file_sync", "directory_sync":
				calls.Sync = func(f *os.File) error {
					if isDir(f) == (phase == "directory_sync") {
						return fault
					}
					return f.Sync()
				}
			case "file_close", "directory_close":
				calls.Close = func(f *os.File) error {
					if isDir(f) == (phase == "directory_close") {
						return fault
					}
					return f.Close()
				}
			case "rename":
				calls.Rename = func(string, string) error { return fault }
			}
			runner := &scripted{t: t}
			_, err := Execute(context.Background(), n, Request{Kind: "complete", Input: "Selected result"}, Options{Runner: runner, IO: calls})
			if !errors.Is(err, fault) || len(runner.calls) != 0 {
				t.Fatalf("publication fault lost or command sent: %v, %v", err, runner.calls)
			}
			s := Inspect(n.StateDir, n.AttemptID)
			published := phase == "directory_sync" || phase == "directory_close"
			if s.Exists != published || published && s.CompletionStatus != "pending" {
				t.Fatalf("wrong partial publication state: %+v", s)
			}
			entries, err := os.ReadDir(filepath.Join(n.StateDir, "intents"))
			if err != nil {
				t.Fatal(err)
			}
			for _, entry := range entries {
				if strings.HasPrefix(entry.Name(), ".intent-") {
					t.Fatal("temporary file leaked", entry.Name())
				}
			}
		})
	}
}

func TestPublicationOrderingAndLostOutcomeRecovery(t *testing.T) {
	t.Run("fsync and close precede send", func(t *testing.T) {
		n := fixtureContext(t)
		var order []string
		kind := func(f *os.File) string {
			info, err := f.Stat()
			if err != nil {
				t.Fatal(err)
			}
			if info.IsDir() {
				return "directory"
			}
			return "file"
		}
		calls := IO{
			Write:  func(f *os.File, data []byte) (int, error) { order = append(order, "write"); return f.Write(data) },
			Sync:   func(f *os.File) error { order = append(order, kind(f)+"_sync"); return f.Sync() },
			Close:  func(f *os.File) error { order = append(order, kind(f)+"_close"); return f.Close() },
			Rename: func(from, to string) error { order = append(order, "rename"); return os.Rename(from, to) },
		}
		runner := &scripted{t: t, replies: []Result{handled(t)}}
		runner.before = func([]string) {
			assertJSON(t, order, []string{"write", "file_sync", "file_close", "rename", "directory_sync", "directory_close"})
			if Inspect(n.StateDir, n.AttemptID).CompletionStatus != "pending" {
				t.Fatal("initial slot was not visible before send")
			}
		}
		if _, err := Execute(context.Background(), n, Request{Kind: "complete", Input: "Selected result"}, Options{Runner: runner, IO: calls}); err != nil {
			t.Fatal(err)
		}
	})
	for _, stage := range []string{"completion_outcome", "response_materialization"} {
		t.Run(stage, func(t *testing.T) {
			n := fixtureContext(t)
			writes := 0
			stopAt := 2
			request := Request{Kind: "complete", Input: "Selected result"}
			if stage == "response_materialization" {
				stopAt, request.Kind = 3, "finish-reply"
			}
			fault := errors.New("crash boundary")
			calls := IO{Write: func(f *os.File, b []byte) (int, error) {
				writes++
				if writes == stopAt {
					return 0, fault
				}
				return f.Write(b)
			}}
			runner := &scripted{t: t, replies: []Result{handled(t)}}
			_, err := Execute(context.Background(), n, request, Options{Runner: runner, IO: calls})
			if !errors.Is(err, fault) || len(runner.calls) != 1 {
				t.Fatal("wrong crash boundary", err, runner.calls)
			}
			if stage == "completion_outcome" {
				if Inspect(n.StateDir, n.AttemptID).CompletionStatus != "pending" {
					t.Fatal("lost outcome was guessed to have committed")
				}
			} else {
				runner.replies = []Result{published()}
			}
			if _, err := Recover(context.Background(), n.StateDir, n.AttemptID, Options{Runner: runner}); err != nil {
				t.Fatal(err)
			}
			if len(runner.calls) != 2 || Inspect(n.StateDir, n.AttemptID).CompletionStatus != "committed" {
				t.Fatal("recovery did not replay the durable intent", runner.calls)
			}
			if stage == "completion_outcome" {
				assertJSON(t, runner.calls[0], runner.calls[1])
			} else {
				assertJSON(t, read(t, n), dict(t, golden(t, "finish_reply"))["journal"])
			}
		})
	}
}

func TestPythonFlockInteroperabilityAndExistingModes(t *testing.T) {
	for _, mode := range []os.FileMode{0644, 0664} {
		for _, symlink := range []bool{false, true} {
			name := mode.String()
			if symlink {
				name += "-symlink"
			}
			t.Run(name, func(t *testing.T) {
				n := fixtureContext(t)
				journal := dict(t, dict(t, golden(t, "finish_reply"))["journal"])
				put(t, n.StateDir, n.AttemptID, journal)
				lockPath := strings.TrimSuffix(path(n.StateDir, n.AttemptID), ".json") + ".lock"
				backing := lockPath
				if symlink {
					backing = filepath.Join(n.StateDir, "python-lock")
				}
				if err := os.WriteFile(backing, nil, mode); err != nil {
					t.Fatal(err)
				}
				if err := os.Chmod(backing, mode); err != nil {
					t.Fatal(err)
				}
				if symlink {
					if err := os.Symlink(backing, lockPath); err != nil {
						t.Fatal(err)
					}
				}
				before, err := os.Stat(backing)
				if err != nil {
					t.Fatal(err)
				}
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				python := exec.CommandContext(ctx, "python3", "-c", "import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB); print('locked',flush=True); sys.stdin.read(1)", lockPath)
				out, err := python.StdoutPipe()
				if err != nil {
					t.Fatal(err)
				}
				in, err := python.StdinPipe()
				if err != nil {
					t.Fatal(err)
				}
				if err := python.Start(); err != nil {
					t.Fatal(err)
				}
				defer func() { in.Close(); _ = python.Wait() }()
				if python.Process.Pid < 2 {
					t.Fatal("invalid child PID")
				}
				if line, err := bufio.NewReader(out).ReadString('\n'); err != nil || line != "locked\n" {
					t.Fatalf("Python lock was not acquired: %q %v", line, err)
				}
				runner := &scripted{t: t, replies: []Result{published()}}
				if _, err := Recover(context.Background(), n.StateDir, n.AttemptID, Options{Runner: runner}); !errors.Is(err, Busy) {
					t.Fatal("recovery did not try-lock", err)
				}
				for _, v := range golden(t, "v6_busy_flush_scopes").([]any) {
					want := dict(t, v)
					scope := text(want["scope"])
					state := map[string]any{"agent": map[string]any{"agent_id": agentID}, "inbox_attempt": map[string]any{"attempt_id": attemptID}, "inbox_journals": []any{attemptID}}
					settled, err := Flush(context.Background(), n.StateDir, state, scope, Options{Runner: runner})
					if err != nil {
						t.Fatal(err)
					}
					assertJSON(t, map[string]any{"scope": scope, "settled": settled, "api_sends": len(runner.calls), "inbox_journals": state["inbox_journals"]}, want)
				}
				completed := make(chan error, 1)
				go func() {
					_, err := Execute(ctx, n, Request{Kind: "finish-reply", Input: "Selected result"}, Options{Runner: runner})
					completed <- err
				}()
				select {
				case err := <-completed:
					t.Fatal("explicit execution did not wait for Python lock", err)
				case <-time.After(50 * time.Millisecond):
				}
				if _, err := in.Write([]byte{'x'}); err != nil {
					t.Fatal(err)
				}
				if err := <-completed; err != nil {
					t.Fatal(err)
				}
				after, err := os.Stat(backing)
				if err != nil || !os.SameFile(before, after) || after.Mode().Perm() != mode {
					t.Fatal("existing Python lock was replaced or chmodded", err)
				}
			})
		}
	}
}

func TestGoldenFlushScopesAndAgentOnlySeeding(t *testing.T) {
	t.Run("pending historical journal and active unused", func(t *testing.T) {
		for _, scope := range []string{"attempt", "all"} {
			n := fixtureContext(t)
			historical := "00000000-0000-0000-0000-0000000000ca"
			journal := dict(t, dict(t, golden(t, "missing_result_recovery").([]any)[0])["before"])
			journal["attempt_id"] = historical
			dict(t, journal["completion"])["status"] = "pending"
			delete(journal, "finish_reply")
			put(t, n.StateDir, historical, journal)
			state := map[string]any{"agent": map[string]any{"agent_id": agentID}, "inbox_attempt": map[string]any{"attempt_id": attemptID}, "inbox_journals": []any{attemptID, historical}}
			runner := &scripted{t: t, replies: []Result{refusal("API_CONNECTION_FAILED", 7)}}
			settled, err := Flush(context.Background(), n.StateDir, state, scope, Options{Runner: runner})
			if err != nil {
				t.Fatal(err)
			}
			for _, v := range dict(t, golden(t, "v5_flush_scopes"))["outcomes"].([]any) {
				want := dict(t, v)
				if text(want["scope"]) == scope {
					assertJSON(t, map[string]any{"scope": scope, "settled": settled, "journal_index": state["inbox_journals"]}, want)
				}
			}
		}
	})
	t.Run("final other-execution notice and response adopted then pruned", func(t *testing.T) {
		n := fixtureContext(t)
		for i, kind := range []string{"notice", "response", "notice"} {
			id := string(rune('0' + i))
			journal := map[string]any{"attempt_id": id, "kind": kind, "session": map[string]any{"agent_id": agentID, "execution_id": "predecessor"},
				"completion": map[string]any{"status": "committed", "result": map[string]any{"record_id": resultID, "version": 1}}}
			if i == 2 {
				delete(journal, "session")
				dict(t, journal["completion"])["argv"] = []string{"--agent-id", agentID}
			}
			put(t, n.StateDir, id, journal)
		}
		state := map[string]any{"agent": map[string]any{"agent_id": agentID, "execution_id": "successor"}}
		seeded, err := SessionJournals(n.StateDir, state)
		if err != nil {
			t.Fatal(err)
		}
		want := dict(t, golden(t, "v5_agent_only_seeding"))
		assertJSON(t, seeded, want["seeded"])
		runner := &scripted{t: t}
		settled, err := Flush(context.Background(), n.StateDir, state, "all", Options{Runner: runner})
		if err != nil {
			t.Fatal(err)
		}
		assertJSON(t, settled, want["settled"])
		assertJSON(t, state["inbox_journals"], want["remaining"])
		assertJSON(t, len(runner.calls), 0)
	})
}

func TestExtensionRoundTripAndCompactAckFacts(t *testing.T) {
	n := fixtureContext(t)
	raw := `{"schema":"cairn.inbox-intent/1","attempt_id":"` + n.AttemptID + `","delivery_id":"` + n.DeliveryID + `","session":{"agent_id":"` + n.Session.AgentID + `","execution_id":"` + n.Session.ExecutionID + `","extension":{"huge":123456789012345678901234567890,"float":1.0,"text":"1"}},"extension":{"html":"<>&","unicode":"雪  ","literal":"\\u2028","number":1e1000},"completion":{"kind":"ack","request_id":"` + n.RequestIDs["completion"] + `","status":"committed","extension":{"nested":[1,"1",false,null]}}}`
	putRaw(t, n.StateDir, n.AttemptID, raw)
	before := dict(t, parse(t, raw))
	ack := dict(t, before["completion"])
	ack["argv"], ack["input"], ack["code"] = n.Commands["ack"], "", "OK"
	put(t, n.StateDir, n.AttemptID, before)
	runner := &scripted{t: t, replies: []Result{refusal("STALE_SESSION", 4)}}
	if _, err := Execute(context.Background(), n, Request{Kind: "ack"}, Options{Runner: runner}); err != nil {
		t.Fatal(err)
	}
	after := read(t, n)
	assertJSON(t, after["extension"], before["extension"])
	assertJSON(t, dict(t, after["session"])["extension"], dict(t, before["session"])["extension"])
	assertJSON(t, dict(t, after["completion"])["extension"], ack["extension"])
	b, err := os.ReadFile(path(n.StateDir, n.AttemptID))
	if err != nil {
		t.Fatal(err)
	}
	for _, fragment := range []string{"<>&", "雪  ", `123456789012345678901234567890`, `1.0`, `1e1000`, `\\u2028`} {
		if !strings.Contains(string(b), fragment) {
			t.Fatal("extension representation lost", fragment, string(b))
		}
	}
	if b[len(b)-1] != '\n' || strings.Contains(string(b), `\u003c`) {
		t.Fatal("journal serialization escaped HTML or lost trailing newline")
	}
	s := Inspect(n.StateDir, n.AttemptID)
	if s.ConfirmsAck(n.DeliveryID, n.Session, n.RequestIDs["completion"]) {
		t.Fatal("compact accepted an extra session field")
	}
	delete(dict(t, after["session"]), "extension")
	put(t, n.StateDir, n.AttemptID, after)
	if !Inspect(n.StateDir, n.AttemptID).ConfirmsAck(n.DeliveryID, n.Session, n.RequestIDs["completion"]) {
		t.Fatal("extra top-level extension must not invalidate compact ack")
	}
}

func TestPreparationAndResultValidationHaveNoEffects(t *testing.T) {
	cases := []struct {
		name    string
		request Request
		change  func(*Context)
		code    string
		exit    int
	}{
		{"missing_command", Request{Kind: "complete"}, func(n *Context) { delete(n.Commands, "complete") }, "INVALID_REQUEST", 2},
		{"finish_notice", Request{Kind: "finish-reply"}, func(n *Context) { n.Kind = "notice" }, "INVALID_REQUEST", 2},
		{"version_on_complete", Request{Kind: "complete", Version: []string{"1", resultID}}, nil, "INVALID_REQUEST", 2},
		{"missing_version", Request{Kind: "respond"}, nil, "INVALID_REQUEST", 2},
		{"not_digits", Request{Kind: "respond", Version: []string{"1.0", "invalid-uuid"}}, nil, "INVALID_REQUEST", 2},
		{"bad_uuid", Request{Kind: "respond", Version: []string{"1", "invalid-uuid"}}, nil, "INVALID_HOST", 7},
		{"unicode_digit_then_bad_uuid", Request{Kind: "respond", Version: []string{"²", "invalid-uuid"}}, nil, "INVALID_HOST", 7},
		{"reason_without_disposition", Request{Kind: "ack", Code: "processing_failed"}, nil, "INVALID_REQUEST", 2},
		{"ignored_without_reason", Request{Kind: "ack", Disposition: "ignored"}, nil, "INVALID_REQUEST", 2},
		{"oversize_characters", Request{Kind: "complete", Input: strings.Repeat("雪", 1024*1024+1)}, nil, "INVALID_REQUEST", 2},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			n := fixtureContext(t)
			if tc.change != nil {
				tc.change(&n)
			}
			runner := &scripted{t: t}
			_, err := Execute(context.Background(), n, tc.request, Options{Runner: runner})
			requireError(t, err, tc.code, tc.exit)
			if len(runner.calls) != 0 {
				t.Fatal("invalid input invoked runner")
			}
			if _, err := os.Stat(filepath.Join(n.StateDir, "intents")); !errors.Is(err, os.ErrNotExist) {
				t.Fatal("pre-lock validation created intent directory", err)
			}
		})
	}
	t.Run("limit counts characters not UTF-8 bytes", func(t *testing.T) {
		n := fixtureContext(t)
		body := strings.Repeat("雪", 1024*1024)
		runner := &scripted{t: t, replies: []Result{handled(t)}}
		if _, err := Execute(context.Background(), n, Request{Kind: "complete", Input: body}, Options{Runner: runner}); err != nil {
			t.Fatal(err)
		}
		if len(runner.calls) != 1 || text(dict(t, runner.calls[0])["input"]) != body {
			t.Fatal("accepted Unicode input changed")
		}
	})
	t.Run("ack ignored reason and unused stdin", func(t *testing.T) {
		n := fixtureContext(t)
		runner := &scripted{t: t, replies: []Result{{Stdout: `{"ok":true,"status":"OK"}`}}}
		if _, err := Execute(context.Background(), n, Request{Kind: "ack", Input: "ignored stdin", Disposition: "ignored", Code: "unsupported_kind"}, Options{Runner: runner}); err != nil {
			t.Fatal(err)
		}
		slot := dict(t, read(t, n)["completion"])
		argv, err := stringList(slot["argv"], "argv")
		if err != nil {
			t.Fatal(err)
		}
		assertJSON(t, argv[len(argv)-5:], []string{"--disposition", "ignored", "--code", "unsupported_kind", n.DeliveryID})
		assertJSON(t, slot["input"], "")
	})
}

func TestRequiredSubscriptsAndNonObjectErrorsStayDistinct(t *testing.T) {
	for _, tc := range []struct {
		name, raw, errorClass string
	}{
		{"non_object", `[]`, "AttributeError"},
		{"slot_non_object", `{"completion":["bad"]}`, "TypeError"},
		{"slot_missing_status", `{"completion":{"kind":"complete"}}`, "KeyError"},
		{"malformed", `{`, "JSONDecodeError"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			n := fixtureContext(t)
			putRaw(t, n.StateDir, n.AttemptID, tc.raw)
			runner := &scripted{t: t}
			_, err := Recover(context.Background(), n.StateDir, n.AttemptID, Options{Runner: runner})
			assertJSON(t, observed(nil, err), map[string]any{"raises": tc.errorClass})
			if Classified(err) == (tc.errorClass == "AttributeError") {
				t.Fatal("escaping vs classified error family lost", err)
			}
			if len(runner.calls) != 0 {
				t.Fatal("corrupt journal sent a command")
			}
		})
	}
	for _, kind := range []string{"complete", "finish-reply"} {
		t.Run("execute_missing_kind_"+kind, func(t *testing.T) {
			n := fixtureContext(t)
			putRaw(t, n.StateDir, n.AttemptID, `{"completion":{"status":"pending"}}`)
			runner := &scripted{t: t}
			_, err := Execute(context.Background(), n, Request{Kind: kind}, Options{Runner: runner})
			requireError(t, err, "INVALID_HOST", 7)
			var missing *KeyError
			if !errors.As(err, &missing) || missing.Key != "kind" || len(runner.calls) != 0 {
				t.Fatal("required subscript became an idempotency conflict", err)
			}
		})
	}
}

func TestSendTimeoutLaunchFailureAndRunnerDecodeFailure(t *testing.T) {
	for _, failure := range []error{context.DeadlineExceeded, &os.PathError{Op: "exec", Path: "missing-command", Err: os.ErrNotExist}} {
		t.Run(failure.Error(), func(t *testing.T) {
			n := fixtureContext(t)
			calls := 0
			runner := RunnerFunc(func(context.Context, []string, string) (Result, error) { calls++; return Result{}, failure })
			result, err := Execute(context.Background(), n, Request{Kind: "complete", Input: "Selected result"}, Options{Runner: runner})
			if err != nil || result.ExitCode != 7 || text(dict(t, parse(t, result.Stdout))["status"]) != "API_CONNECTION_FAILED" {
				t.Fatal("uncertain send lost public mapping", result, err)
			}
			slot := dict(t, read(t, n)["completion"])
			if text(slot["status"]) != "pending" || text(slot["code"]) != "API_UNAVAILABLE" {
				t.Fatal("send failure was treated as final", slot)
			}
			if _, err := Recover(context.Background(), n.StateDir, n.AttemptID, Options{Runner: runner}); err != nil || calls != 2 {
				t.Fatal("pending send not replayed", err, calls)
			}
		})
	}
	t.Run("text decoding error is classified rather than a transport retry", func(t *testing.T) {
		n := fixtureContext(t)
		failure := &ValueError{"invalid UTF-8 output"}
		_, err := Execute(context.Background(), n, Request{Kind: "complete"}, Options{Runner: RunnerFunc(func(context.Context, []string, string) (Result, error) { return Result{}, failure })})
		requireError(t, err, "INVALID_HOST", 7)
		if !errors.Is(err, failure) || dict(t, read(t, n)["completion"])["code"] != nil {
			t.Fatal("decode failure was flattened into API_UNAVAILABLE", err)
		}
	})
}

func TestStatusCodeScalarAndBooleanGuards(t *testing.T) {
	for _, tc := range []struct{ name, stdout, code string }{
		{"unicode_truncation", `{"ok":false,"status":"` + strings.Repeat("雪", 80) + `"}`, strings.Repeat("雪", 64)},
		{"integer", `{"ok":false,"status":123}`, "123"},
		{"float", `{"ok":false,"status":1e15}`, "1000000000000000.0"},
		{"underflow", `{"ok":false,"status":1e-1000}`, "API_UNAVAILABLE"},
		{"bool", `{"ok":false,"status":true}`, "True"},
		{"not_boolean_ok", `{"ok":1,"status":"OK"}`, "API_UNAVAILABLE"},
		{"not_object", `[]`, "API_UNAVAILABLE"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			n := fixtureContext(t)
			runner := &scripted{t: t, replies: []Result{{Stdout: tc.stdout, ExitCode: 7}}}
			if _, err := Execute(context.Background(), n, Request{Kind: "complete"}, Options{Runner: runner}); err != nil {
				t.Fatal(err)
			}
			slot := dict(t, read(t, n)["completion"])
			if text(slot["status"]) != "pending" || text(slot["code"]) != tc.code {
				t.Fatal("wrong send classification", slot)
			}
		})
	}
}

func TestFinishRefStrictTypesAndExplicitRespondExactStrings(t *testing.T) {
	for _, raw := range []string{
		`{"record_id":"` + resultID + `","version":1.0}`,
		`{"record_id":"` + resultID + `","version":"1"}`,
		`{"record_id":"` + resultID + `","version":true}`,
		`{"record_id":"` + resultID + `","version":0}`,
		`{"record_id":"` + resultID + `","version":1,"extension":true}`,
		`{"record_id":"bad","version":1}`,
	} {
		t.Run(raw, func(t *testing.T) {
			n := fixtureContext(t)
			journal := dict(t, dict(t, golden(t, "missing_result_recovery").([]any)[0])["before"])
			dict(t, journal["completion"])["result"] = parse(t, raw)
			put(t, n.StateDir, n.AttemptID, journal)
			s := Inspect(n.StateDir, n.AttemptID)
			if s.HasUsableFinishReplyRef || !s.UnmaterializedFinishReply || !s.PendingReplay {
				t.Fatal("malformed result became usable", s)
			}
			runner := &scripted{t: t, replies: []Result{handled(t)}}
			for range 2 {
				r, err := Recover(context.Background(), n.StateDir, n.AttemptID, Options{Runner: runner})
				if err != nil {
					t.Fatal(err)
				}
				assertJSON(t, r.Pending, []string{"response"})
			}
			if len(runner.calls) != 2 || Inspect(n.StateDir, n.AttemptID).HasUsableFinishReplyRef {
				t.Fatal("non-nil malformed result was silently replaced")
			}
			if !SettledJournalIndex(n.StateDir, map[string]any{"inbox_journals": []any{n.AttemptID}}) {
				t.Fatal("unmaterialized reply wedge was introduced")
			}
		})
	}
	t.Run("stored UUID is not recanonicalized for explicit respond", func(t *testing.T) {
		n := fixtureContext(t)
		journal := dict(t, dict(t, golden(t, "finish_reply"))["journal"])
		delete(journal, "response")
		delete(journal, "finish_reply")
		dict(t, dict(t, journal["completion"])["result"])["record_id"] = strings.ToUpper(resultID)
		put(t, n.StateDir, n.AttemptID, journal)
		runner := &scripted{t: t}
		_, err := Execute(context.Background(), n, Request{Kind: "respond", Version: []string{"1", resultID}}, Options{Runner: runner})
		requireError(t, err, "INVALID_REQUEST", 2)
		if !Inspect(n.StateDir, n.AttemptID).HasUsableFinishReplyRef || len(runner.calls) != 0 {
			t.Fatal("finish_reply canonicalization and explicit comparison were conflated")
		}
	})
}

func TestRecoveryBlocksReplyBehindPendingCompletion(t *testing.T) {
	n := fixtureContext(t)
	journal := dict(t, dict(t, golden(t, "finish_reply"))["journal"])
	dict(t, journal["completion"])["status"] = "pending"
	dict(t, journal["response"])["status"] = "pending"
	put(t, n.StateDir, n.AttemptID, journal)
	runner := &scripted{t: t, replies: []Result{refusal("API_CONNECTION_FAILED", 7)}}
	r, err := Recover(context.Background(), n.StateDir, n.AttemptID, Options{Runner: runner})
	if err != nil {
		t.Fatal(err)
	}
	assertJSON(t, r.Pending, []string{"completion", "response"})
	if len(runner.calls) != 1 || text(dict(t, runner.calls[0])["operation"]) != "complete" {
		t.Fatal("reply escaped before completion was confirmed", runner.calls)
	}
}

func TestCommandRunnerUsesTextStreamsAndCancellation(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	r, err := (CommandRunner{}).Run(ctx, []string{"python3", "-c", "import sys; sys.stdout.write('a\\r\\nb\\rc'); sys.stderr.write('diagnostic\\n'); sys.exit(3)"}, "")
	if err != nil || r.Stdout != "a\nb\nc" || r.Stderr != "diagnostic\n" || r.ExitCode != 3 {
		t.Fatalf("text subprocess result: %+v, %v", r, err)
	}
	bounded, done := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer done()
	_, err = (CommandRunner{}).Run(bounded, []string{"python3", "-c", "import time; time.sleep(10)"}, "")
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("runner ignored context timeout", err)
	}
}

func TestExplicitLockDeadlineIsThirtySeconds(t *testing.T) {
	n := fixtureContext(t)
	target := path(n.StateDir, n.AttemptID)
	if err := os.MkdirAll(filepath.Dir(target), 0700); err != nil {
		t.Fatal(err)
	}
	f, err := os.OpenFile(strings.TrimSuffix(target, ".json")+".lock", os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 40*time.Second)
	defer cancel()
	runner := &scripted{t: t}
	start := time.Now()
	_, err = Execute(ctx, n, Request{Kind: "complete"}, Options{Runner: runner})
	requireError(t, err, "SESSION_BUSY", 7)
	if time.Since(start) < 30*time.Second || len(runner.calls) != 0 {
		t.Fatal("explicit lock did not wait its 30-second budget", time.Since(start), runner.calls)
	}
	if _, err := os.Stat(target); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("busy command wrote an intent", err)
	}
}

func TestMalformedUTF8AndLoneSurrogatesRetainFailureBoundaries(t *testing.T) {
	t.Run("read invalid UTF-8", func(t *testing.T) {
		n := fixtureContext(t)
		putRaw(t, n.StateDir, n.AttemptID, "{\"extension\":\"\xff\"}")
		s := Inspect(n.StateDir, n.AttemptID)
		if s.ParseErr == nil || !Classified(s.ParseErr) || s.ClaimOnly {
			t.Fatal("invalid UTF-8 was replaced or classified as absence", s)
		}
	})
	for _, raw := range []string{`"\ud800"`, `"\udfff"`, `"\ud800x"`} {
		t.Run(raw, func(t *testing.T) {
			n := fixtureContext(t)
			putRaw(t, n.StateDir, n.AttemptID, `{"extension":`+raw+`}`)
			before, err := os.ReadFile(path(n.StateDir, n.AttemptID))
			if err != nil {
				t.Fatal(err)
			}
			runner := &scripted{t: t}
			_, err = Execute(context.Background(), n, Request{Kind: "complete"}, Options{Runner: runner})
			requireError(t, err, "INVALID_HOST", 7)
			if !Classified(err) || len(runner.calls) != 0 {
				t.Fatal("UTF-8 write failure was swallowed", err)
			}
			after, err := os.ReadFile(path(n.StateDir, n.AttemptID))
			if err != nil || string(before) != string(after) {
				t.Fatal("failed write changed original bytes", err)
			}
		})
	}
}

func TestHistoricalBusyJournalIsRetainedUnderBothScopes(t *testing.T) {
	for _, scope := range []string{"attempt", "all"} {
		t.Run(scope, func(t *testing.T) {
			n := fixtureContext(t)
			put(t, n.StateDir, "historical", map[string]any{"kind": "notice", "completion": map[string]any{"status": "committed"}})
			f, err := os.OpenFile(filepath.Join(n.StateDir, "intents", "historical.lock"), os.O_CREATE|os.O_RDWR, 0600)
			if err != nil {
				t.Fatal(err)
			}
			defer f.Close()
			if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
				t.Fatal(err)
			}
			state := map[string]any{"agent": map[string]any{"agent_id": agentID}, "inbox_attempt": map[string]any{"attempt_id": attemptID}, "inbox_journals": []any{"historical"}}
			runner := &scripted{t: t}
			settled, err := Flush(context.Background(), n.StateDir, state, scope, Options{Runner: runner})
			if err != nil || settled != (scope == "attempt") || len(runner.calls) != 0 {
				t.Fatal("historical Busy scope changed", settled, err, runner.calls)
			}
			assertJSON(t, state["inbox_journals"], []string{"historical", attemptID})
		})
	}
}

func TestReviewFixM1UnmaterializedUsableReplyIsPendingReplay(t *testing.T) {
	n := fixtureContext(t)
	journal := dict(t, dict(t, golden(t, "finish_reply"))["journal"])
	delete(journal, "response")
	put(t, n.StateDir, n.AttemptID, journal)
	s := Inspect(n.StateDir, n.AttemptID)
	if !s.PendingReplay || s.ReplyPossible || !s.UnmaterializedFinishReply || !s.HasUsableFinishReplyRef {
		t.Fatalf("owed reply facts: pending=%v possible=%v unmaterialized=%v usable=%v", s.PendingReplay, s.ReplyPossible, s.UnmaterializedFinishReply, s.HasUsableFinishReplyRef)
	}
	if !SettledJournalIndex(n.StateDir, map[string]any{"inbox_journals": []any{n.AttemptID}}) {
		t.Fatal("legacy settled-index compatibility changed")
	}
	runner := &scripted{t: t, replies: []Result{published()}}
	r, err := Recover(context.Background(), n.StateDir, n.AttemptID, Options{Runner: runner})
	if err != nil || len(r.Pending) != 0 || len(runner.calls) != 1 || text(dict(t, runner.calls[0])["operation"]) != "publish" {
		t.Fatal("recovery did not publish exactly the owed reply", r, err, runner.calls)
	}
}

func TestReviewFixL1MissingStatusNeverSends(t *testing.T) {
	for _, kind := range []string{"finish-reply", "complete"} {
		t.Run(kind, func(t *testing.T) {
			n := fixtureContext(t)
			journal := dict(t, dict(t, golden(t, "finish_reply"))["journal"])
			delete(journal, "response")
			delete(dict(t, journal["completion"]), "status")
			put(t, n.StateDir, n.AttemptID, journal)
			input := "Selected result"
			if kind == "complete" {
				input = "Conflicting result"
			}
			runner := &scripted{t: t, replies: []Result{handled(t)}}
			_, err := Execute(context.Background(), n, Request{Kind: kind, Input: input}, Options{Runner: runner})
			requireError(t, err, "INVALID_HOST", 7)
			var key *KeyError
			if !errors.As(err, &key) || key.Key != "status" || len(runner.calls) != 0 {
				t.Fatal("missing status did not fail at the original subscript", err, runner.calls)
			}
		})
	}
}

func TestReviewFixL2StatAbsenceKeepsDirectReadErrors(t *testing.T) {
	for _, name := range []string{"long_id", "not_directory", "nul", "symlink_loop"} {
		t.Run(name, func(t *testing.T) {
			n := fixtureContext(t)
			if err := os.MkdirAll(filepath.Join(n.StateDir, "intents"), 0700); err != nil {
				t.Fatal(err)
			}
			switch name {
			case "long_id":
				n.AttemptID = strings.Repeat("x", 300)
			case "not_directory":
				n.StateDir = filepath.Join(n.StateDir, "file")
				if err := os.WriteFile(n.StateDir, nil, 0600); err != nil {
					t.Fatal(err)
				}
			case "nul":
				n.AttemptID = "bad\x00id"
			case "symlink_loop":
				target := path(n.StateDir, n.AttemptID)
				if err := os.MkdirAll(filepath.Dir(target), 0700); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(filepath.Base(target), target); err != nil {
					t.Fatal(err)
				}
			}
			s := Inspect(n.StateDir, n.AttemptID)
			if s.Exists || s.ReadErr != nil || s.ClaimOnly {
				t.Fatalf("stat-gated absence: exists=%v read=%v claim_only=%v", s.Exists, s.ReadErr, s.ClaimOnly)
			}
			state, err := s.CompletionState()
			if err != nil || state != "absent" {
				t.Fatal("exists-gated completion state", state, err)
			}
			if _, err := s.Committed(); err == nil || !Classified(err) {
				t.Fatal("direct committed reader lost its read error", err)
			}
			if s.ConfirmsAck(n.DeliveryID, n.Session, n.RequestIDs["completion"]) {
				t.Fatal("compact confirmed an unreadable journal")
			}
			if !SettledJournalIndex(n.StateDir, map[string]any{"inbox_journals": []any{n.AttemptID}}) {
				t.Fatal("exists-gated index did not skip the failed stat")
			}
			runner := &scripted{t: t}
			r, err := Recover(context.Background(), n.StateDir, n.AttemptID, Options{Runner: runner})
			if err != nil || len(runner.calls) != 0 {
				t.Fatal("exists-gated recovery did not report unused", r, err)
			}
			assertJSON(t, r.Pending, []string{"unused"})
		})
	}
}

func TestReviewFixL3SurrogateObjectKeysFailBeforeSend(t *testing.T) {
	for _, raw := range []string{`{"\ud800":1}`, `{"extension":{"\udfff":1}}`, `{"extension":[{"\ud800x":1}]}`} {
		t.Run(raw, func(t *testing.T) {
			n := fixtureContext(t)
			putRaw(t, n.StateDir, n.AttemptID, raw)
			if s := Inspect(n.StateDir, n.AttemptID); s.ParseErr != nil || s.NonObject {
				t.Fatal("Python accepts the key on read", s)
			}
			runner := &scripted{t: t, replies: []Result{handled(t)}}
			_, err := Execute(context.Background(), n, Request{Kind: "complete", Input: "Selected result"}, Options{Runner: runner})
			requireError(t, err, "INVALID_HOST", 7)
			var invalidUTF8 *ValueError
			if !errors.As(err, &invalidUTF8) || len(runner.calls) != 0 {
				t.Fatal("key encoding error was replaced or sent", err, runner.calls)
			}
			after, err := os.ReadFile(path(n.StateDir, n.AttemptID))
			if err != nil || string(after) != raw {
				t.Fatal("failed key write changed the original", err)
			}
		})
	}
}

func TestReviewFixL5CancellationBeforePublication(t *testing.T) {
	for _, kind := range []string{"complete", "finish-reply"} {
		t.Run(kind, func(t *testing.T) {
			n := fixtureContext(t)
			ctx, cancel := context.WithCancel(context.Background())
			cancel()
			runner := &scripted{t: t, replies: []Result{handled(t)}}
			_, err := Execute(ctx, n, Request{Kind: kind, Input: "Selected result"}, Options{Runner: runner})
			if !errors.Is(err, context.Canceled) || len(runner.calls) != 0 {
				t.Fatal("cancelled command sent", err, runner.calls)
			}
			if _, err := os.Stat(path(n.StateDir, n.AttemptID)); !errors.Is(err, os.ErrNotExist) {
				t.Fatal("cancelled command created a replayable intent", err)
			}
		})
	}
}

func TestReviewFixL5WaitDelayIsUncertain(t *testing.T) {
	n := fixtureContext(t)
	runner := RunnerFunc(func(context.Context, []string, string) (Result, error) { return Result{}, exec.ErrWaitDelay })
	r, err := Execute(context.Background(), n, Request{Kind: "complete"}, Options{Runner: runner})
	if err != nil || r.ExitCode != 7 || text(dict(t, parse(t, r.Stdout))["status"]) != "API_CONNECTION_FAILED" {
		t.Fatal("pipe-drain timeout was not uncertain", r, err)
	}
	slot := dict(t, read(t, n)["completion"])
	if text(slot["status"]) != "pending" || text(slot["code"]) != "API_UNAVAILABLE" {
		t.Fatal("pipe-drain timeout became final", slot)
	}
}

func TestReviewFixL5CommandRunnerBoundsDescendantPipes(t *testing.T) {
	for _, prefix := range []string{"", "sys.stdout.buffer.write(b'\\xff'); sys.stdout.flush(); "} {
		t.Run(prefix, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			_, err := (CommandRunner{}).Run(ctx, []string{"python3", "-c", "import subprocess,sys; " + prefix + "subprocess.Popen([sys.executable,'-c','import time; time.sleep(0.5)'])"}, "")
			if !errors.Is(err, exec.ErrWaitDelay) {
				t.Fatal("descendant-held pipes did not reach the bounded drain", err)
			}
		})
	}
}

func TestReviewFixL7Unicode16Digits(t *testing.T) {
	for _, bounds := range [][2]rune{{0x10d40, 0x10d49}, {0x116d0, 0x116e3}, {0x11bf0, 0x11bf9}, {0x16130, 0x16139}, {0x16d70, 0x16d79}, {0x1ccf0, 0x1ccf9}, {0x1e5f1, 0x1e5fa}} {
		t.Run(string(bounds[0]), func(t *testing.T) {
			for r := bounds[0]; r <= bounds[1]; r++ {
				if !digits(string(r)) {
					t.Fatalf("Python 3.14 digit U+%04X rejected", r)
				}
			}
			n := fixtureContext(t)
			runner := &scripted{t: t}
			_, err := Execute(context.Background(), n, Request{Kind: "respond", Version: []string{string(bounds[0]), "invalid-uuid"}}, Options{Runner: runner})
			requireError(t, err, "INVALID_HOST", 7)
			if len(runner.calls) != 0 {
				t.Fatal("invalid UUID escaped validation")
			}
		})
	}
}

func TestReviewFixL6RecoveryOutcomesAreCallerOwned(t *testing.T) {
	for _, name := range []string{"completion_pending", "completion_refused", "response_pending", "response_refused", "response_committed", "already_final", "earlier_diagnostic_before_error"} {
		t.Run(name, func(t *testing.T) {
			n := fixtureContext(t)
			journal := dict(t, dict(t, golden(t, "finish_reply"))["journal"])
			completion, response := dict(t, journal["completion"]), dict(t, journal["response"])
			runner := &scripted{t: t}
			var want []SlotDiagnostic
			diagnostic := SlotDiagnostic{Target: path(n.StateDir, n.AttemptID)}
			switch name {
			case "completion_pending":
				completion["status"], response["status"] = "pending", "pending"
				runner.replies = []Result{refusal("API_CONNECTION_FAILED", 7)}
				diagnostic.Slot, diagnostic.Kind, diagnostic.Status, diagnostic.Code = "completion", "complete", "pending", "API_CONNECTION_FAILED"
				want = []SlotDiagnostic{diagnostic}
			case "completion_refused", "earlier_diagnostic_before_error":
				completion["status"] = "pending"
				delete(journal, "response")
				if name == "earlier_diagnostic_before_error" {
					journal["response"] = []any{"bad"}
				}
				runner.replies = []Result{refusal("STALE_LEASE", 4)}
				diagnostic.Slot, diagnostic.Kind, diagnostic.Status, diagnostic.Code = "completion", "complete", "refused", "STALE_LEASE"
				want = []SlotDiagnostic{diagnostic}
			case "response_pending", "response_refused", "response_committed":
				response["status"] = "pending"
				diagnostic.Slot, diagnostic.Kind = "response", "respond"
				if name == "response_pending" {
					runner.replies = []Result{refusal("API_CONNECTION_FAILED", 7)}
					diagnostic.Status, diagnostic.Code = "pending", "API_CONNECTION_FAILED"
					want = []SlotDiagnostic{diagnostic}
				} else if name == "response_refused" {
					runner.replies = []Result{refusal("DESTINATION_PROHIBITED", 4)}
					diagnostic.Status, diagnostic.Code = "refused", "DESTINATION_PROHIBITED"
					want = []SlotDiagnostic{diagnostic}
				} else {
					runner.replies = []Result{published()}
				}
			case "already_final":
			}
			put(t, n.StateDir, n.AttemptID, journal)
			r, err := Recover(context.Background(), n.StateDir, n.AttemptID, Options{Runner: runner})
			if name == "earlier_diagnostic_before_error" {
				var typ *TypeError
				if !errors.As(err, &typ) {
					t.Fatal("later malformed response lost its error", err)
				}
			} else if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(r.Outcomes, want) {
				t.Fatalf("recovery diagnostics: got %+v want %+v", r.Outcomes, want)
			}
		})
	}
}

func TestReviewFixL3ValidKeysKeepTheirIdentity(t *testing.T) {
	n := fixtureContext(t)
	raw := `{"\ud83d\ude00":1,"\\ud800":2,"\ufffd":3,"\b\f\n\r\t\"\\\/":4}`
	putRaw(t, n.StateDir, n.AttemptID, raw)
	runner := &scripted{t: t, replies: []Result{handled(t)}}
	if _, err := Execute(context.Background(), n, Request{Kind: "complete"}, Options{Runner: runner}); err != nil {
		t.Fatal(err)
	}
	after := read(t, n)
	for key, expected := range dict(t, parse(t, raw)) {
		assertJSON(t, after[key], expected)
	}
}
