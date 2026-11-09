package transcript

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/coordination"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/lifecycle/wake"
	"github.com/halbritt/cairn/internal/procpeer"
)

const nativeID = "11111111-2222-4333-8444-555555555555"
const turnID = "66666666-7777-4888-8999-aaaaaaaaaaaa"
const baseStamp = "2026-11-08T12:00:00.000Z"
const fixtureBoot = "00000000-1111-4222-8333-444444444444"

func mustWrite(t *testing.T, path string, raw []byte) {
	t.Helper()
	if e := os.MkdirAll(filepath.Dir(path), 0700); e != nil {
		t.Fatal(e)
	}
	if e := os.WriteFile(path, raw, 0600); e != nil {
		t.Fatal(e)
	}
}
func mustJSON(t *testing.T, v any) []byte {
	t.Helper()
	raw, e := json.Marshal(v)
	if e != nil {
		t.Fatal(e)
	}
	return append(raw, '\n')
}
func mustLink(t *testing.T, target, path string) {
	t.Helper()
	if e := os.MkdirAll(filepath.Dir(path), 0700); e != nil {
		t.Fatal(e)
	}
	if e := os.Symlink(target, path); e != nil {
		t.Fatal(e)
	}
}
func requireError(t *testing.T, e error, message string) {
	t.Helper()
	var te *TranscriptError
	if !errors.As(e, &te) || te.Code != "INVALID_REQUEST" || te.Message != message {
		t.Fatalf("error=%v, want INVALID_REQUEST: %s", e, message)
	}
}
func stamp(second int) string { return fmt.Sprintf("2026-11-08T12:00:%02d.000Z", second) }
func epochStamp(t *testing.T, s string) int64 {
	t.Helper()
	v, e := time.Parse(time.RFC3339Nano, s)
	if e != nil {
		t.Fatal(e)
	}
	return v.Unix()
}

func TestJSONPoliciesAndTokenParity(t *testing.T) {
	for _, raw := range []string{`{"a":1,"a":2}`, `{"x":[{"a":1,"a":2}]}`, `{"\ud800":1,"\ud800":2}`} {
		if _, e := parsePlainFixture(t, []byte(raw)); e != nil {
			t.Fatal(e)
		}
		_, e := strictFixture(t, []byte(raw+"\n"))
		requireError(t, e, "native record is not strict JSON")
	}
	m, e := parsePlainFixture(t, []byte(`{"a":1,"a":2,"integer":9007199254740993,"float":1.0,"exp":1e0}`))
	if e != nil {
		t.Fatal(e)
	}
	if !hasInteger(field(m, "a"), 2) || !hasInteger(field(m, "integer"), 9007199254740993) {
		t.Fatal(m)
	}
	for _, v := range []any{field(m, "float"), field(m, "exp"), true, "1", float64(1), math.NaN(), math.Inf(1)} {
		if _, ok := exactInt(v); ok {
			t.Fatalf("accepted exact integer %#v", v)
		}
	}
	for _, v := range []any{math.NaN(), math.Inf(1), math.Inf(-1)} {
		if _, ok := numberSeconds(v, false); !ok {
			t.Fatal("narrowed numeric type", v)
		}
	}
	if _, ok := numberSeconds(true, false); ok {
		t.Fatal("refusal accepts bool")
	}
	if n, ok := numberSeconds(true, true); !ok || n != 1 {
		t.Fatal("queue rejects bool", n, ok)
	}
	for _, raw := range []string{`{"x":NaN}`, `{"x":Infinity}`, `{"x":-Infinity}`, `{"x":"\ud800"}`, `{"x":"\udc00"}`, `{"x":"\ud800\u1234"}`} {
		if _, e := parsePlainFixture(t, []byte(raw)); e != nil {
			t.Fatal(raw, e)
		}
		if _, e := strictFixture(t, []byte(raw)); e != nil {
			t.Fatal(raw, e)
		}
	}
	m, e = strictFixture(t, []byte(`{"\ud800":1,"\ud801":2,"\ufffd":3}`))
	if e != nil || len(m.Members) != 3 || m.Members[0].Key[0] != 0xd800 || m.Members[1].Key[0] != 0xd801 || m.Members[2].Key[0] != 0xfffd {
		t.Fatal("lost surrogate identity", m, e)
	}
	for _, raw := range [][]byte{[]byte("{\"x\":\"\xed\xa0\x80\"}"), []byte("{\"x\":\"\xff\"}"), append([]byte{0xef, 0xbb, 0xbf}, []byte(`{}`)...), []byte(`{"x":1} {}`), []byte(`[]`)} {
		if _, e := parsePlainFixture(t, raw); e == nil {
			t.Fatalf("text plain accepted %q", raw)
		}
		if _, e := strictFixture(t, raw); e == nil {
			t.Fatalf("strict accepted %q", raw)
		}
	}
	for _, raw := range []string{`{"x":"\ud83d\ude00"}`, `{"x":"\\ud800"}`, `{"x":"a\\\"b"}`} {
		if _, e := strictFixture(t, []byte(raw)); e != nil {
			t.Fatal(raw, e)
		}
	}
	// 4300 is an explicit synthetic fixture profile, never a production default.
	for digits := 4300; digits <= 4301; digits++ {
		raw := []byte(`{"x":` + strings.Repeat("1", digits) + `}`)
		_, e := strictFixture(t, raw)
		if (e == nil) != (digits == 4300) {
			t.Fatalf("fixture digit limit %d: %v", digits, e)
		}
	}
	_, e = strictFixture(t, []byte("[]\n"))
	requireError(t, e, "native record is not an object")
	for _, s := range []any{"2026-11-08T12:00:00Z", "2026-11-08T12:00:00.000+00:00", true, "2026-99-08T12:00:00.000Z"} {
		if _, e := NativeTime(s); e == nil {
			t.Fatal("accepted timestamp", s)
		}
	}
	if _, e := NativeTime(baseStamp); e != nil {
		t.Fatal(e)
	}
	rootErr := errors.New("root")
	e = &TranscriptError{"INVALID_REQUEST", "example", rootErr}
	if e.Error() != "INVALID_REQUEST: example" || !errors.Is(e, rootErr) {
		t.Fatal(e)
	}
}

func TestPinnedRecordsAndLastJSONL(t *testing.T) {
	p := filepath.Join(t.TempDir(), "transcript.jsonl")
	raw := []byte("{\"a\":1}\n")
	mustWrite(t, p, raw)
	l := pin(raw, 0)
	text := fmt.Sprintf("%d:%d:%s", l.Offset, l.Length, l.SHA256)
	got, e := ParseLocator(text)
	if e != nil || got != l {
		t.Fatal(got, e)
	}
	for _, s := range []string{"00:2:" + l.SHA256, "0:0:" + l.SHA256, "0:65537:" + l.SHA256, "10000000000000000:1:" + l.SHA256, "0:2:" + strings.ToUpper(l.SHA256)} {
		if _, e := ParseLocator(s); e == nil {
			t.Fatalf("accepted locator %s", s)
		}
	}
	if _, e = readNativeFixture(t, p, l); e != nil {
		t.Fatal(e)
	}
	for _, bad := range []Locator{{math.MaxInt64, 2, l.SHA256}, {0, 0, l.SHA256}, {0, NativeRecordLimit + 1, l.SHA256}, {0, l.Length, strings.Repeat("0", 64)}, {1, l.Length, l.SHA256}} {
		if _, e := readNativeFixture(t, p, bad); e == nil {
			t.Fatal("accepted bad pin", bad)
		}
	}
	if _, e = readNativeAtFixture(t, p, l, uint32(os.Getuid())+1); e == nil {
		t.Fatal("accepted wrong owner")
	}
	link := filepath.Join(t.TempDir(), "link")
	mustLink(t, p, link)
	if _, e = readNativeFixture(t, link, l); e == nil {
		t.Fatal("accepted symlink")
	}
	for _, bad := range []string{"{\"a\":1}", "{\"a\":1}\n{\"b\":2}\n", "{\"a\":1,\"a\":2}\n"} {
		mustWrite(t, p, []byte(bad))
		if _, e := readNativeFixture(t, p, pin([]byte(bad), 0)); e == nil {
			t.Fatalf("accepted bad framing/strict record %q", bad)
		}
	}
	mustWrite(t, p, []byte("{\"a\":1}\n{\"a\":1,\"a\":2}\n\n"))
	if !hasInteger(field(lastFixture(t, p), "a"), 2) {
		t.Fatal("plain policy lost")
	}
	for _, raw := range []string{"", "{\"a\":", "[]\n", strings.Repeat(" ", NativeRecordLimit) + `{"a":1}`} {
		mustWrite(t, p, []byte(raw))
		if got := lastFixture(t, p); got != nil {
			t.Fatalf("last accepted %q", raw)
		}
	}
	if lastFixture(t, filepath.Join(t.TempDir(), "missing")) != nil {
		t.Fatal("missing returned a record")
	}
}

func claudeFixture(t *testing.T) (ClaudeConfig, SessionState, string) {
	t.Helper()
	cfg := ClaudeConfig{Harness: "claude", ConfigHome: t.TempDir(), ConfigPath: filepath.Join(t.TempDir(), "binding.json"), HomeDir: t.TempDir(), HasProcessExeDirs: true, ProcessExeDirs: []string{"/opt/claude"}}
	state := SessionState{NativeSessionID: nativeID, Workspace: "/workspace/test", AgentID: turnID, ExecutionID: nativeID}
	p, e := ClaudeTranscriptPath(cfg, state)
	if e != nil {
		t.Fatal(e)
	}
	mustWrite(t, p, []byte("{}\n"))
	return cfg, state, p
}
func TestNonblockingNodeRefusal(t *testing.T) {
	cfg, state, p := claudeFixture(t)
	if e := os.Remove(p); e != nil {
		t.Fatal(e)
	}
	if e := syscall.Mkfifo(p, 0600); e != nil {
		t.Fatal(e)
	}
	start := time.Now()
	if _, e := ClaudeTranscriptPathAt(cfg, state, uint32(os.Getuid())); e == nil {
		t.Fatal("accepted FIFO")
	}
	if _, e := readNativeFixture(t, p, pin([]byte("{}\n"), 0)); e == nil {
		t.Fatal("accepted pinned FIFO")
	}
	if time.Since(start) >= 100*time.Millisecond {
		t.Fatal("FIFO refusal blocked")
	}
	f := codexFixture(t)
	if e := os.Remove(f.path); e != nil {
		t.Fatal(e)
	}
	if e := syscall.Mkfifo(f.path, 0600); e != nil {
		t.Fatal(e)
	}
	start = time.Now()
	if findFixture(t, f.reader, f.cfg, f.state, turnID) != nil {
		t.Fatal("FIFO discovery accepted")
	}
	if time.Since(start) >= 100*time.Millisecond {
		t.Fatal("FIFO discovery blocked")
	}
	sock := filepath.Join(t.TempDir(), "sock")
	listener, e := net.Listen("unix", sock)
	if e != nil {
		t.Fatal(e)
	}
	defer listener.Close()
	if _, e = readNativeFixture(t, sock, pin([]byte("{}\n"), 0)); e == nil {
		t.Fatal("accepted socket")
	}
	if _, e = readNativeFixture(t, t.TempDir(), pin([]byte("{}\n"), 0)); e == nil {
		t.Fatal("accepted directory")
	}
}

func TestClaudePathAndClaimSourceContract(t *testing.T) {
	for in, want := range map[string]string{"/a//./b/../c": "-a----b----c", "//a/b": "--a-b", "///a/b": "---a-b", "/a/é": "-a--"} {
		got, e := ClaudeProjectSlug(in)
		if e != nil || got != want {
			t.Errorf("slug %q=%q,%v want %q", in, got, e, want)
		}
	}
	if _, e := ClaudeProjectSlug("relative"); e == nil {
		t.Fatal("accepted relative")
	}
	cfg, state, p := claudeFixture(t)
	if _, e := ClaudeTranscriptPathAt(cfg, state, uint32(os.Getuid())); e != nil {
		t.Fatal(e)
	}
	if _, e := ClaudeTranscriptPathAt(cfg, state, uint32(os.Getuid())+1); e == nil {
		t.Fatal("accepted wrong UID")
	}
	f, e := os.OpenFile(p, os.O_WRONLY, 0600)
	if e != nil {
		t.Fatal(e)
	}
	if e = f.Truncate(ClaudeTranscriptLimit + 1); e != nil {
		t.Fatal(e)
	}
	f.Close()
	if _, e = ClaudeTranscriptPathAt(cfg, state, uint32(os.Getuid())); e == nil {
		t.Fatal("accepted large transcript")
	}
	attempt := Attempt{CreatedAt: stamp(2)}
	good := []byte(`{"type":"user","timestamp":"wrong","timestamp":"` + baseStamp + `"}` + "\n" + `{"type":"cost-state"}` + "\n" + `{"type":"last-prompt"}` + "\n")
	mustWrite(t, p, good)
	proof, e := daemonClaimFixture(t, cfg, state, attempt)
	if e != nil || proof["records_after"] != 2 || proof["transcript_bytes"] != len(good) || fixtureText(proof["last_turn_record_at"]) != baseStamp {
		t.Fatal(proof, e)
	}
	for _, tc := range []struct{ raw, message string }{
		{`{"type":"user","timestamp":"` + stamp(2) + `"}`, "native transcript records activity at or after the claim"},
		{`{"type":"user","timestamp":"` + baseStamp + `"}` + "\n" + `{"type":"attachment"}`, "native transcript records a turn or unknown record after the claim"},
		{`{"type":"last-prompt"}`, "native transcript has no timestamped turn record"},
		{`{"type":"user","timestamp":"` + baseStamp + `"}` + "\n" + `{"type":"last-prompt","timestamp":null}`, "native transcript records a turn or unknown record after the claim"},
		{"broken", "native transcript has an unreadable record"},
		{"[]", "native transcript has a non-object record"},
	} {
		mustWrite(t, p, []byte(tc.raw+"\n"))
		_, e := daemonClaimFixture(t, cfg, state, attempt)
		requireError(t, e, tc.message)
	}
}

func claudeRefusalRecords(state SessionState, cfg ClaudeConfig, attempt Attempt) []map[string]any {
	message := wake.WakeMessages(wake.SessionRef{AgentID: state.AgentID, ExecutionID: state.ExecutionID}, attempt.Delivery.DeliveryID)[0]
	channel := wake.FormatClaudeChannelPrompt(wake.ClaudeChannelMeta{NativeSessionID: state.NativeSessionID, AgentID: state.AgentID, ExecutionID: state.ExecutionID, DeliveryID: attempt.Delivery.DeliveryID}, message)
	third := map[string]any{}
	for _, key := range NativeRefusalKeys {
		third[key] = nil
	}
	for key, value := range map[string]any{"type": "system", "subtype": "informational", "level": "warning", "preventContinuation": true, "isSidechain": false, "isMeta": false, "sessionId": nativeID, "timestamp": stamp(3), "uuid": turnID, "version": "fixture", "content": "UserPromptSubmit operation blocked by hook:\n[/usr/bin/python3 /fixture/coordination.py hook --config " + cfg.ConfigPath + "]: " + NativeRefusalMessage + "\n\n\nOriginal prompt: " + channel} {
		third[key] = value
	}
	return []map[string]any{{"type": "queue-operation", "operation": "enqueue", "timestamp": baseStamp, "sessionId": nativeID, "content": channel}, {"type": "queue-operation", "operation": "dequeue", "timestamp": stamp(1), "sessionId": nativeID}, third}
}
func writeRecords(t *testing.T, path string, records []map[string]any) []Locator {
	t.Helper()
	var raw []byte
	ls := []Locator{}
	for _, record := range records {
		line := mustJSON(t, record)
		ls = append(ls, pin(line, int64(len(raw))))
		raw = append(raw, line...)
	}
	mustWrite(t, path, raw)
	return ls
}
func TestClaudeRefusalPinnedContract(t *testing.T) {
	cfg, state, p := claudeFixture(t)
	attempt := Attempt{CreatedAt: stamp(2), Delivery: Delivery{DeliveryID: turnID, Attempts: 1, State: "leased"}}
	records := claudeRefusalRecords(state, cfg, attempt)
	ls := writeRecords(t, p, records)
	got, e := nativeRefusalFixture(t, cfg, state, attempt, ls)
	if e != nil || fixtureText(got["refusal_uuid"]) != turnID || fixtureText(got["claude_version"]) != "fixture" {
		t.Fatal(got, e)
	}
	for _, change := range []func([]map[string]any){func(rs []map[string]any) { rs[0]["content"] = "wrong wake" }, func(rs []map[string]any) { rs[1]["extra"] = true }, func(rs []map[string]any) { rs[2]["isMeta"] = true }, func(rs []map[string]any) { rs[2]["timestamp"] = stamp(16) }, func(rs []map[string]any) {
		rs[2]["content"] = strings.Replace(fixtureText(rs[2]["content"]), cfg.ConfigPath, "/other/binding.json", 1)
	}} {
		rs := claudeRefusalRecords(state, cfg, attempt)
		change(rs)
		ls := writeRecords(t, p, rs)
		if _, e := nativeRefusalFixture(t, cfg, state, attempt, ls); e == nil {
			t.Fatal("accepted altered refusal")
		}
	}
	ls = writeRecords(t, p, records)
	bad := append([]Locator(nil), ls...)
	bad[1].Offset++
	if _, e := nativeRefusalFixture(t, cfg, state, attempt, bad); e == nil {
		t.Fatal("accepted noncontiguous")
	}
	attempt.Delivery.Attempts = 2
	if _, e := nativeRefusalFixture(t, cfg, state, attempt, ls); e == nil {
		t.Fatal("accepted repeated delivery")
	}
}

func writeProc(t *testing.T, root string, pid, parent int, start uint64, comm, exe string, argv []string) procpeer.ProcessRef {
	t.Helper()
	fields := make([]string, 22)
	for i := range fields {
		fields[i] = "0"
	}
	fields[0] = "S"
	fields[1] = strconv.Itoa(parent)
	fields[2] = strconv.Itoa(pid)
	fields[3] = strconv.Itoa(pid)
	fields[19] = strconv.FormatUint(start, 10)
	dir := filepath.Join(root, strconv.Itoa(pid))
	mustWrite(t, filepath.Join(root, "sys/kernel/random/boot_id"), []byte(fixtureBoot+"\n"))
	mustWrite(t, filepath.Join(dir, "stat"), []byte(fmt.Sprintf("%d (fixture) %s\n", pid, strings.Join(fields, " "))))
	mustWrite(t, filepath.Join(dir, "comm"), []byte(comm+"\n"))
	mustWrite(t, filepath.Join(dir, "cmdline"), []byte(strings.Join(argv, "\x00")+"\x00"))
	if exe != "" {
		mustLink(t, exe, filepath.Join(dir, "exe"))
	}
	return procpeer.ProcessRef{PID: pid, Start: start, Boot: fixtureBoot}
}
func TestDaemonWorkerUsesOnlySyntheticProc(t *testing.T) {
	cfg, state, p := claudeFixture(t)
	root := t.TempDir()
	state.Process = writeProc(t, root, 400, 2, 100, "daemon", "/opt/claude/claude", []string{"claude", "daemon"})
	writeProc(t, root, 401, 400, 101, "bg-pty-host", "/opt/claude/claude", []string{"claude", "bg-pty-host"})
	worker := writeProc(t, root, 402, 401, 102, "claude", "/opt/claude/claude", []string{"claude", "--resume", p})
	got, e := FindDaemonWorkerAt(root, cfg, state, uint32(os.Getuid()))
	if e != nil || got != worker {
		t.Fatal(got, e)
	}
	writeProc(t, root, 403, 401, 103, "claude", "/opt/claude/claude", []string{"claude", "--resume", p})
	_, e = FindDaemonWorkerAt(root, cfg, state, uint32(os.Getuid()))
	requireError(t, e, "expected exactly one daemon worker for this conversation, found 2")
	cfg.ProcessExeDirs = []string{"/wrong"}
	_, e = FindDaemonWorkerAt(root, cfg, state, uint32(os.Getuid()))
	requireError(t, e, "expected exactly one daemon worker for this conversation, found 0")
}

type codexFiles struct {
	cfg         CodexConfig
	state       SessionState
	reader      Reader
	path, fdDir string
}

func codexFixture(t *testing.T) codexFiles {
	t.Helper()
	home, root := t.TempDir(), t.TempDir()
	state := SessionState{NativeSessionID: nativeID, Workspace: "/workspace/test", AgentID: turnID, ExecutionID: nativeID, Process: procpeer.ProcessRef{PID: 600, Start: 100, Boot: fixtureBoot}}
	p := filepath.Join(home, "sessions/2026/11/08/rollout-fixture-"+nativeID+".jsonl")
	mustWrite(t, p, []byte("{}\n"))
	dir := filepath.Join(root, "600/fd")
	mustLink(t, p, filepath.Join(dir, "7"))
	r := NewReader(fixtureProfile(t))
	r.ProcRoot = root
	r.Alive = func(ref procpeer.ProcessRef) bool { return ref == state.Process }
	return codexFiles{CodexConfig{Harness: "codex", ConfigHome: home, Runtime: r.Runtime}, state, r, p, dir}
}
func TestCodexLiveOwnerRollout(t *testing.T) {
	f := codexFixture(t)
	p, e := f.reader.CodexRolloutPath(f.cfg, f.state)
	if e != nil || p != f.path {
		t.Fatal(p, e)
	}
	mustLink(t, f.path, filepath.Join(f.fdDir, "8"))
	if _, e = f.reader.CodexRolloutPath(f.cfg, f.state); e != nil {
		t.Fatal("duplicate fd must deduplicate", e)
	}
	other := filepath.Join(filepath.Dir(f.path), "rollout-other-"+nativeID+".jsonl")
	mustWrite(t, other, []byte("{}\n"))
	mustLink(t, other, filepath.Join(f.fdDir, "9"))
	_, e = f.reader.CodexRolloutPath(f.cfg, f.state)
	requireError(t, e, "Codex owner does not hold exactly one matching rollout")
	f = codexFixture(t)
	if e = os.Remove(filepath.Join(f.fdDir, "7")); e != nil {
		t.Fatal(e)
	}
	if _, e = f.reader.CodexRolloutPath(f.cfg, f.state); e == nil {
		t.Fatal("accepted zero")
	}
	f = codexFixture(t)
	f.reader.Alive = func(procpeer.ProcessRef) bool { return false }
	_, e = f.reader.CodexRolloutPath(f.cfg, f.state)
	requireError(t, e, "Codex rollout owner is unavailable")
	f = codexFixture(t)
	calls := 0
	f.reader.Alive = func(procpeer.ProcessRef) bool { calls++; return calls == 1 }
	_, e = f.reader.CodexRolloutPath(f.cfg, f.state)
	requireError(t, e, "Codex owner does not hold exactly one matching rollout")
	f = codexFixture(t)
	stalePath := filepath.Join(t.TempDir(), "stale")
	mustWrite(t, stalePath, []byte("{}\n"))
	stale, e := os.Stat(stalePath)
	if e != nil {
		t.Fatal(e)
	}
	_, e = codexRolloutPathAt(f.reader.ProcRoot, f.cfg, f.state, f.reader.ExpectedUID, f.reader.Alive, func(string) (os.FileInfo, error) { return stale, nil })
	requireError(t, e, "Codex rollout path no longer names its open file")
	f = codexFixture(t)
	f.reader.ExpectedUID++
	if _, e = f.reader.CodexRolloutPath(f.cfg, f.state); e == nil {
		t.Fatal("accepted wrong UID")
	}
	f = codexFixture(t)
	alias := filepath.Join(f.cfg.ConfigHome, "sessions/alias")
	mustLink(t, filepath.Dir(f.path), alias)
	if e = os.Remove(filepath.Join(f.fdDir, "7")); e != nil {
		t.Fatal(e)
	}
	mustLink(t, filepath.Join(alias, filepath.Base(f.path)), filepath.Join(f.fdDir, "7"))
	_, e = f.reader.CodexRolloutPath(f.cfg, f.state)
	requireError(t, e, "Codex rollout is outside its configured session directory")
}

func codexRecord(second, ordinal int, kind string, payload map[string]any) map[string]any {
	return map[string]any{"timestamp": stamp(second), "ordinal": ordinal, "type": kind, "payload": payload}
}
func codexRefusalRecords(t *testing.T) []map[string]any {
	t.Helper()
	started := epochStamp(t, baseStamp)
	return []map[string]any{codexRecord(0, 10, "event_msg", map[string]any{"type": "task_started", "turn_id": turnID, "root_turn_id": turnID, "started_at": started, "model_context_window": 1000, "collaboration_mode_kind": "default", "turn_attribution": map[string]any{"turn_id": turnID, "turn_trigger": "queue", "parent_turn_id": nil, "initiating_agent_path": nil, "root_turn_id": turnID}}), codexRecord(1, 11, "turn_context", map[string]any{"turn_id": turnID, "root_turn_id": turnID, "cwd": "/workspace/test"}), codexRecord(3, 12, "event_msg", map[string]any{"type": "task_complete", "turn_id": turnID, "root_turn_id": turnID, "last_agent_message": nil, "started_at": started, "completed_at": started + 3, "duration_ms": 3000})}
}
func TestCodexRefusalAndNumericShape(t *testing.T) {
	f := codexFixture(t)
	attempt := Attempt{CreatedAt: stamp(2), NativeTurnID: turnID}
	recorded := map[string]any{"at": epochStamp(t, stamp(2))}
	records := codexRefusalRecords(t)
	ls := writeRecords(t, f.path, records)
	proof, e := f.reader.CodexRefusalProof(f.cfg, f.state, attempt, recorded, ls)
	if e != nil || proof["schema"] != "cairn.codex-refused-turn/1" {
		t.Fatal(proof, e)
	}
	// Python ordinals are arbitrary precision; retain tokens beyond int64.
	for i, rs := range records {
		rs["ordinal"] = json.Number([]string{"9223372036854775808", "9223372036854775809", "9223372036854775810"}[i])
	}
	ls = writeRecords(t, f.path, records)
	if _, e = f.reader.CodexRefusalProof(f.cfg, f.state, attempt, recorded, ls); e != nil {
		t.Fatal("large integer ordinal lost", e)
	}
	records = codexRefusalRecords(t)
	for _, change := range []func([]map[string]any){func(rs []map[string]any) { rs[0]["ordinal"] = json.Number("10.0") }, func(rs []map[string]any) { rs[1]["ordinal"] = json.Number("11e0") }, func(rs []map[string]any) {
		fixtureObject(rs[0]["payload"])["started_at"] = json.Number(strconv.FormatInt(epochStamp(t, baseStamp), 10) + ".0")
	}, func(rs []map[string]any) { fixtureObject(rs[2]["payload"])["duration_ms"] = json.Number("3000.0") }, func(rs []map[string]any) { fixtureObject(rs[2]["payload"])["error"] = nil }, func(rs []map[string]any) { fixtureObject(rs[2]["payload"])["last_agent_message"] = "assistant" }, func(rs []map[string]any) {
		fixtureObject(fixtureObject(rs[0]["payload"])["turn_attribution"])["parent_turn_id"] = turnID
	}, func(rs []map[string]any) { rs[2]["timestamp"] = stamp(16) }} {
		rs := codexRefusalRecords(t)
		change(rs)
		ls := writeRecords(t, f.path, rs)
		if _, e := f.reader.CodexRefusalProof(f.cfg, f.state, attempt, recorded, ls); e == nil {
			t.Fatal("accepted altered refusal")
		}
	}
	ls = writeRecords(t, f.path, records)
	ls[1].Offset++
	if _, e := f.reader.CodexRefusalProof(f.cfg, f.state, attempt, recorded, ls); e == nil {
		t.Fatal("accepted noncontiguous")
	}
	ls = writeRecords(t, f.path, records)
	recorded["at"] = true
	if _, e := f.reader.CodexRefusalProof(f.cfg, f.state, attempt, recorded, ls); e == nil {
		t.Fatal("accepted bool refusal timestamp")
	}
	recorded["at"] = epochStamp(t, stamp(2))
	replaceDuringRecheck(t, &f)
	if _, e := f.reader.CodexRefusalProof(f.cfg, f.state, attempt, recorded, ls); e == nil {
		t.Fatal("accepted replaced rollout")
	}
}
func replaceDuringRecheck(t *testing.T, f *codexFiles) {
	t.Helper()
	calls := 0
	old := f.reader.Alive
	f.reader.Alive = func(ref procpeer.ProcessRef) bool {
		calls++
		if calls == 3 {
			raw, e := os.ReadFile(f.path)
			if e != nil {
				t.Fatal(e)
			}
			replacement := f.path + ".replacement"
			mustWrite(t, replacement, raw)
			if e = os.Rename(replacement, f.path); e != nil {
				t.Fatal(e)
			}
		}
		return old(ref)
	}
}

func endedRecords(t *testing.T) []map[string]any {
	rs := codexRefusalRecords(t)
	middle := []map[string]any{codexRecord(1, 11, "event_msg", map[string]any{"type": "token_count", "turn_id": turnID}), codexRecord(1, 12, "event_msg", map[string]any{"type": "item_completed", "item": map[string]any{"type": "UserMessage"}}), codexRecord(1, 13, "turn_context", map[string]any{"turn_id": turnID}), codexRecord(1, 14, "world_state", map[string]any{}), codexRecord(1, 15, "response_item", map[string]any{"type": "message", "role": "user"}), codexRecord(1, 16, "response_item", map[string]any{"type": "message", "role": "developer"})}
	last := rs[2]
	last["ordinal"] = 17
	fixtureObject(last["payload"])["error"] = map[string]any{"codex_error_info": "provider_error"}
	return append(append([]map[string]any{rs[0]}, middle...), last)
}
func endPins(ls []Locator) []Locator { return []Locator{ls[0], ls[len(ls)-1]} }
func TestCodexEndedTurnRegionAndTail(t *testing.T) {
	f := codexFixture(t)
	attempt := Attempt{CreatedAt: stamp(2), NativeTurnID: turnID}
	records := endedRecords(t)
	ls := writeRecords(t, f.path, records)
	p, e := f.reader.CodexEndedTurnProof(f.cfg, f.state, attempt, endPins(ls))
	if e != nil || fixtureText(p["codex_error_info"]) != "provider_error" {
		t.Fatal(p, e)
	}
	for _, change := range []func([]map[string]any){func(rs []map[string]any) { rs[2]["type"] = "tool_call" }, func(rs []map[string]any) { fixtureObject(rs[5]["payload"])["role"] = "assistant" }, func(rs []map[string]any) { rs[1]["ordinal"] = json.Number("11.0") }, func(rs []map[string]any) { fixtureObject(rs[1]["payload"])["turn_id"] = "different" }, func(rs []map[string]any) {
		fixtureObject(fixtureObject(rs[len(rs)-1]["payload"])["error"])["codex_error_info"] = "Not_Allowed"
	}} {
		rs := endedRecords(t)
		change(rs)
		ls := writeRecords(t, f.path, rs)
		if _, e := f.reader.CodexEndedTurnProof(f.cfg, f.state, attempt, endPins(ls)); e == nil {
			t.Fatal("accepted activity/shape")
		}
	}
	ls = writeRecords(t, f.path, records)
	file, e := os.OpenFile(f.path, os.O_APPEND|os.O_WRONLY, 0600)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = file.WriteString(turnID + "\n"); e != nil {
		t.Fatal(e)
	}
	file.Close()
	_, e = f.reader.CodexEndedTurnProof(f.cfg, f.state, attempt, endPins(ls))
	requireError(t, e, "Codex rollout records this turn again after its end")
	// A duplicate only in an unpinned inner line must still fail strict-raise.
	lines := [][]byte{}
	for i, rs := range records {
		line := mustJSON(t, rs)
		if i == 1 {
			line = bytes.Replace(line, []byte(`"type":"token_count"`), []byte(`"type":"ignored","type":"token_count"`), 1)
		}
		lines = append(lines, line)
	}
	raw := bytes.Join(lines, nil)
	mustWrite(t, f.path, raw)
	pins := []Locator{pin(lines[0], 0), pin(lines[len(lines)-1], int64(len(raw)-len(lines[len(lines)-1])))}
	_, e = f.reader.CodexEndedTurnProof(f.cfg, f.state, attempt, pins)
	requireError(t, e, "native record is not strict JSON")
	ls = writeRecords(t, f.path, records)
	replaceDuringRecheck(t, &f)
	if _, e = f.reader.CodexEndedTurnProof(f.cfg, f.state, attempt, endPins(ls)); e == nil {
		t.Fatal("accepted replaced rollout")
	}
}

func TestCodexQueueFourOutcomesAndCustodyMarkers(t *testing.T) {
	f := codexFixture(t)
	w := Wake{AttemptedAt: epochStamp(t, baseStamp), DeliveryID: "delivery-marker", QueuedSubmissionID: "submission-marker", Session: wake.SessionRef{AgentID: turnID, ExecutionID: nativeID}}
	for _, outcome := range []string{"completed", "cancelled", "admitted_active", "never_admitted"} {
		rs := codexRefusalRecords(t)
		switch outcome {
		case "completed":
			fixtureObject(rs[2]["payload"])["last_agent_message"] = "done"
		case "admitted_active":
			rs = rs[:2]
		case "never_admitted":
			fixtureObject(rs[0]["payload"])["turn_attribution"] = map[string]any{"turn_trigger": "owner"}
		}
		ls := writeRecords(t, f.path, rs)
		p, e := f.reader.CodexQueueRolloutProof(f.cfg, f.state, w, ls)
		if e != nil || p["outcome"] != outcome {
			t.Fatalf("%s: %v %v", outcome, p, e)
		}
	}
	for _, marker := range []string{w.DeliveryID, w.QueuedSubmissionID} {
		rs := codexRefusalRecords(t)
		fixtureObject(rs[0]["payload"])["turn_attribution"] = map[string]any{"turn_trigger": "owner"}
		fixtureObject(rs[1]["payload"])["marker"] = marker
		ls := writeRecords(t, f.path, rs)
		if _, e := f.reader.CodexQueueRolloutProof(f.cfg, f.state, w, ls); e == nil {
			t.Fatal("accepted custody marker")
		}
	}
	for _, marker := range []string{`"turn_trigger": "owner"`} {
		rs := codexRefusalRecords(t)
		fixtureObject(rs[0]["payload"])["turn_attribution"] = map[string]any{"turn_trigger": "owner"}
		ls := writeRecords(t, f.path, rs)
		custom := w
		custom.DeliveryID = marker
		if _, e := f.reader.CodexQueueRolloutProof(f.cfg, f.state, custom, ls); e == nil {
			t.Fatal("missed serialized marker", marker)
		}
	}
	rs := codexRefusalRecords(t)
	fixtureObject(rs[0]["payload"])["turn_attribution"] = map[string]any{"turn_trigger": "owner"}
	ls := writeRecords(t, f.path, rs)
	file, e := os.OpenFile(f.path, os.O_APPEND|os.O_WRONLY, 0600)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = file.WriteString("{}\n"); e != nil {
		t.Fatal(e)
	}
	file.Close()
	if _, e := f.reader.CodexQueueRolloutProof(f.cfg, f.state, w, ls); e == nil {
		t.Fatal("accepted non-EOF never-admitted")
	}
	for _, bad := range []any{true, math.NaN(), math.Inf(1), nil} {
		ls := writeRecords(t, f.path, rs)
		w.AttemptedAt = bad
		if _, e := f.reader.CodexQueueRolloutProof(f.cfg, f.state, w, ls); e == nil {
			t.Fatal("accepted nonfinite attempt", bad)
		}
	}
	// Baseline: size is captured once; queue proof rechecks inode, not growth.
	w.AttemptedAt = epochStamp(t, baseStamp)
	ls = writeRecords(t, f.path, rs)
	calls := 0
	old := f.reader.Alive
	f.reader.Alive = func(ref procpeer.ProcessRef) bool {
		calls++
		if calls == 3 {
			file, e := os.OpenFile(f.path, os.O_APPEND|os.O_WRONLY, 0600)
			if e != nil {
				t.Fatal(e)
			}
			if _, e = file.WriteString("{}\n"); e != nil {
				t.Fatal(e)
			}
			file.Close()
		}
		return old(ref)
	}
	p, e := f.reader.CodexQueueRolloutProof(f.cfg, f.state, w, ls)
	if e != nil || p["outcome"] != "never_admitted" {
		t.Fatal("source size-race characterization changed", p, e)
	}
}

func TestQueueMarkerPythonSerialization(t *testing.T) {
	raw := [][]byte{[]byte(`{"z":1.00,"a":"é😀<>&","nested":{"y":1e0,"x":-0}}`)}
	for _, marker := range []string{`"z": 1.0, "a": "\u00e9\ud83d\ude00<>&"`, `"y": 1.0, "x": 0`} {
		if !queueMarkerFixture(t, raw, Wake{DeliveryID: marker}) {
			t.Fatal("missed Python serialization marker", marker)
		}
	}
	if queueMarkerFixture(t, raw, Wake{DeliveryID: "absent-marker"}) {
		t.Fatal("invented marker")
	}
}

func TestLatestCodexEndStrictSkipAndStartBarrier(t *testing.T) {
	f := codexFixture(t)
	records := codexRefusalRecords(t)
	ls := writeRecords(t, f.path, records)
	p, e := f.reader.LatestCodexTurnEnd(f.cfg, f.state)
	if e != nil || p == nil || p.Locator != ls[2] {
		t.Fatal(p, e)
	}
	raw, e := os.ReadFile(f.path)
	if e != nil {
		t.Fatal(e)
	}
	raw = append(raw, []byte("malformed\n{\"type\":\"event_msg\",\"type\":\"event_msg\",\"payload\":{\"type\":\"task_complete\"}}\n{\"type\":\"event_msg\",\"payload\":{\"type\":\"turn_aborted\"}}\npartial")...)
	mustWrite(t, f.path, raw)
	p, e = f.reader.LatestCodexTurnEnd(f.cfg, f.state)
	if e != nil || p == nil || p.Locator != ls[2] {
		t.Fatal(p, e)
	}
	raw = append(raw[:bytes.LastIndexByte(raw, '\n')+1], mustJSON(t, records[0])...)
	mustWrite(t, f.path, raw)
	p, e = f.reader.LatestCodexTurnEnd(f.cfg, f.state)
	if e != nil || p != nil {
		t.Fatal("task_started did not block historical end", p, e)
	}
	// The retained window starts inside a long physical line. Its suffix cannot
	// become a valid first record, while the complete pinned end survives.
	end := mustJSON(t, records[2])
	prefix := bytes.Repeat([]byte{'x'}, CodexTurnEndLimit+1)
	prefix = append(prefix, '\n')
	mustWrite(t, f.path, append(prefix, end...))
	p, e = f.reader.LatestCodexTurnEnd(f.cfg, f.state)
	if e != nil || p == nil || p.Locator.Offset != int64(len(prefix)) {
		t.Fatal(p, e)
	}
}

func TestCAIRN423WholeSnapshotDiscovery(t *testing.T) {
	f := codexFixture(t)
	rs := codexRefusalRecords(t)
	ls := writeRecords(t, f.path, rs)
	base, e := os.ReadFile(f.path)
	if e != nil {
		t.Fatal(e)
	}
	// A multi-chunk suffix is scanned completely; discovery has no tail-only cap.
	suffix := bytes.Repeat([]byte("{\"unrelated\":true}\n"), 12000)
	mustWrite(t, f.path, append(append([]byte(nil), base...), suffix...))
	if got := findFixture(t, f.reader, f.cfg, f.state, turnID); !reflect.DeepEqual(got, ls) {
		t.Fatal(got, ls)
	}
	// Plain policy accepts duplicate keys and skips malformed unrelated lines.
	duplicate := bytes.Replace(base, []byte(`"turn_id":"`+turnID+`"`), []byte(`"turn_id":"wrong","turn_id":"`+turnID+`"`), 1)
	mustWrite(t, f.path, append([]byte("malformed\n"), duplicate...))
	if got := findFixture(t, f.reader, f.cfg, f.state, turnID); len(got) != 3 {
		t.Fatal(got)
	}
	oversized := append(bytes.Repeat([]byte{'x'}, NativeRecordLimit+12), mustJSON(t, rs[0])...)
	mustWrite(t, f.path, append(append([]byte(nil), oversized...), base...))
	got := findFixture(t, f.reader, f.cfg, f.state, turnID)
	if len(got) != 3 || got[0].Offset != int64(len(oversized)) {
		t.Fatal("oversized suffix was parsed", got)
	}
	for name, raw := range map[string][]byte{"four": append(append([]byte(nil), base...), mustJSON(t, rs[2])...), "separated": bytes.Join([][]byte{mustJSON(t, rs[0]), []byte("{}\n"), mustJSON(t, rs[1]), mustJSON(t, rs[2])}, nil), "partial": append(append([]byte(nil), base...), []byte("{")...), "oversized_partial": append(append([]byte(nil), base...), bytes.Repeat([]byte{'x'}, NativeRecordLimit*2)...)} {
		t.Run(name, func(t *testing.T) {
			mustWrite(t, f.path, raw)
			if got := findFixture(t, f.reader, f.cfg, f.state, turnID); got != nil {
				t.Fatal(got)
			}
		})
	}
	mustWrite(t, f.path, base)
	clock := time.Now()
	calls := 0
	f.reader.Now = func() time.Time {
		calls++
		if calls > 1 {
			return clock.Add(2 * time.Second)
		}
		return clock
	}
	if got := findFixture(t, f.reader, f.cfg, f.state, turnID); got != nil {
		t.Fatal("accepted budget exhaustion")
	}
	f.reader.Now = time.Now
	replaceDuringRecheck(t, &f)
	if got := findFixture(t, f.reader, f.cfg, f.state, turnID); got != nil {
		t.Fatal("accepted identity change")
	}
	// Same-inode append during final liveness recheck must also return unknown.
	f = codexFixture(t)
	writeRecords(t, f.path, rs)
	alive := f.reader.Alive
	calls = 0
	f.reader.Alive = func(ref procpeer.ProcessRef) bool {
		calls++
		if calls == 3 {
			file, e := os.OpenFile(f.path, os.O_APPEND|os.O_WRONLY, 0600)
			if e != nil {
				t.Fatal(e)
			}
			if _, e = file.WriteString("{}\n"); e != nil {
				t.Fatal(e)
			}
			file.Close()
		}
		return alive(ref)
	}
	if got := findFixture(t, f.reader, f.cfg, f.state, turnID); got != nil {
		t.Fatal("accepted changed snapshot size")
	}
}

// This provider qualifies only synthetic fixture operations. It is not an
// isolated-launch/runtime profile, locale or timezone selection for production.
func (f fixtureRoutes) TranscriptRuntime() *SourceRuntime {
	return &SourceRuntime{ParseClaim: fixtureClaimInstant, Filesystem: fixtureFilesystem{}}
}
func fixtureClaimInstant(site ClaimSite, raw *value.Value) (ClaimInstant, error) {
	if raw == nil {
		return ClaimInstant{}, &value.Error{Class: "KeyError", Message: "created_at"}
	}
	if raw.Kind != value.String {
		class := "TypeError"
		if site != ClaimNative {
			class = "AttributeError"
		}
		return ClaimInstant{}, &value.Error{Class: class, Message: "synthetic datetime input"}
	}
	s := string(raw.String)
	if site != ClaimNative {
		s = strings.ReplaceAll(s, "Z", "+00:00")
	}
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02 15:04:05Z07:00", "2006-01-02T15:04:05-0700"} {
		if at, err := time.Parse(layout, s); err == nil {
			return ClaimInstant{Seconds: epoch(at), Aware: true}, nil
		}
	}
	if at, err := time.ParseInLocation("2006-01-02T15:04:05", s, time.UTC); err == nil {
		return ClaimInstant{Seconds: epoch(at), Aware: false}, nil
	}
	return ClaimInstant{}, &value.Error{Class: "ValueError", Message: "invalid synthetic ISO timestamp"}
}

type fixtureFilesystem struct {
	cwd, home string
	homeCalls *int
}

func (f fixtureFilesystem) Encode(points []rune) (string, error) {
	var raw []byte
	for _, r := range points {
		if r >= 0xdc80 && r <= 0xdcff {
			raw = append(raw, byte(r-0xdc00))
		} else if !utf8.ValidRune(r) {
			return "", &value.Error{Class: "UnicodeEncodeError", Message: "synthetic surrogateescape filesystem"}
		} else {
			raw = utf8.AppendRune(raw, r)
		}
	}
	return string(raw), nil
}
func (f fixtureFilesystem) Decode(raw string) ([]rune, error) {
	var points []rune
	for len(raw) > 0 {
		r, n := utf8.DecodeRuneInString(raw)
		if r == utf8.RuneError && n == 1 {
			points = append(points, rune(0xdc00+uint16(raw[0])))
		} else {
			points = append(points, r)
		}
		raw = raw[n:]
	}
	return points, nil
}
func (f fixtureFilesystem) Resolve(v *value.Value) ([]rune, error) {
	if v == nil || v.Kind != value.String {
		return nil, &value.Error{Class: "TypeError", Message: "synthetic path"}
	}
	p, err := f.Encode(v.String)
	if err != nil {
		return nil, err
	}
	if strings.IndexByte(p, 0) >= 0 {
		return nil, &value.Error{Class: "ValueError", Message: "embedded null byte"}
	}
	if !strings.HasPrefix(p, "/") {
		cwd := f.cwd
		if cwd == "" {
			cwd, err = os.Getwd()
			if err != nil {
				return nil, err
			}
		}
		p = cwd + "/" + p
	}
	todo := strings.Split(p, "/")
	var parts []string
	links := 0
	for len(todo) > 0 {
		part := todo[0]
		todo = todo[1:]
		switch part {
		case "", ".":
			continue
		case "..":
			if len(parts) > 0 {
				parts = parts[:len(parts)-1]
			}
			continue
		}
		candidate := "/" + strings.Join(append(append([]string(nil), parts...), part), "/")
		info, err := os.Lstat(candidate)
		if err == nil && info.Mode()&os.ModeSymlink != 0 {
			links++
			if links > 40 {
				return nil, &os.PathError{Op: "resolve", Path: candidate, Err: syscall.ELOOP}
			}
			target, err := os.Readlink(candidate)
			if err != nil {
				return nil, err
			}
			if strings.HasPrefix(target, "/") {
				parts = nil
			}
			todo = append(strings.Split(target, "/"), todo...)
			continue
		}
		if err != nil && !os.IsNotExist(err) {
			return nil, err
		}
		parts = append(parts, part)
	}
	return f.Decode("/" + strings.Join(parts, "/"))
}
func (f fixtureFilesystem) IsDir(points []rune) (bool, error) {
	p, e := f.Encode(points)
	if e != nil {
		return false, e
	}
	st, e := os.Stat(p)
	if os.IsNotExist(e) {
		return false, nil
	}
	return e == nil && st.IsDir(), e
}
func (f fixtureFilesystem) Exists(points []rune) (bool, error) {
	p, e := f.Encode(points)
	if e != nil {
		return false, e
	}
	_, e = os.Stat(p)
	if os.IsNotExist(e) {
		return false, nil
	}
	return e == nil, e
}
func (f fixtureFilesystem) Home() ([]rune, error) {
	if f.homeCalls != nil {
		(*f.homeCalls)++
	}
	if f.home == "" {
		return nil, &value.ProfileUnavailable{}
	}
	return []rune(f.home), nil
}

func TestStage2TranscriptReadAndByteStrip(t *testing.T) {
	for _, tail := range [][]byte{[]byte("\xc2\x85"), []byte("\xc2\xa0"), []byte("\xe2\x80\x83")} {
		p := filepath.Join(t.TempDir(), "record")
		mustWrite(t, p, append([]byte("{\"a\":1}\n"), tail...))
		v, e := LastJSONLRecord(p, fixtureProfile(t))
		if e != nil || v != nil {
			t.Fatal("bytes.strip broadened", v, e)
		}
	}
	raw, e := readAtFrom(0, 8, func(b []byte, _ int64) (int, error) { copy(b, "short"); return 5, io.EOF })
	if e != nil || string(raw) != "short" {
		t.Fatal(raw, e)
	}
	actual := &os.PathError{Op: "pread", Path: "synthetic", Err: syscall.EIO}
	_, e = readAtFrom(0, 8, func([]byte, int64) (int, error) { return 0, actual })
	if e != actual {
		t.Fatal("I/O error became a short read", e)
	}
	p := filepath.Join(t.TempDir(), "missing")
	_, e = ReadNativeRecord(p, pin([]byte("{}\n"), 0), fixtureProfile(t))
	var path *os.PathError
	var errno syscall.Errno
	if !errors.As(e, &path) && !errors.As(e, &errno) {
		t.Fatal("open OSError was remapped", e)
	}
	cfg, state, p := claudeFixture(t)
	if e = os.Remove(p); e != nil {
		t.Fatal(e)
	}
	_, e = DaemonStartClaimEvidence(cfg, state, Attempt{CreatedAt: stamp(2)}, fixtureProfile(t))
	if !errors.As(e, &path) && !errors.As(e, &errno) {
		t.Fatal("claim open OSError was remapped", e)
	}
}
func TestStage2TranscriptClaimSites(t *testing.T) {
	runtime := fixtureRoutes{}.TranscriptRuntime()
	for _, site := range []ClaimSite{ClaimDaemon, ClaimCodexRefused, ClaimCodexEnded} {
		_, e := claimTime(runtime, site, nil)
		if value.ErrorClass(e) != "KeyError" {
			t.Fatal(site, e)
		}
		_, e = claimTime(runtime, site, "invalid")
		if value.ErrorClass(e) != "ValueError" {
			t.Fatal(site, e)
		}
		_, e = claimTime(runtime, site, &value.Value{Kind: value.Null})
		if value.ErrorClass(e) != "AttributeError" {
			t.Fatal(site, e)
		}
	}
	_, e := claimTime(runtime, ClaimNative, nil)
	requireError(t, e, "held attempt has no claim time")
	_, e = claimTime(runtime, ClaimNative, &value.Value{Kind: value.Bool, Boolean: true})
	requireError(t, e, "held attempt has no claim time")
	naive := "2026-11-08T12:00:02"
	_, e = claimTime(runtime, ClaimNative, naive)
	requireError(t, e, "native chronology does not bracket the single claim of this delivery")
	claim, e := claimTime(runtime, ClaimDaemon, naive)
	if e != nil || claim.Aware {
		t.Fatal("daemon comparison must retain naive claim", claim, e)
	}
	if _, e = claimTime(runtime, ClaimCodexRefused, naive); e != nil {
		t.Fatal("synthetic local timestamp route rejected", e)
	}
	if _, e = claimTime(runtime, ClaimNative, "2026-11-08 12:00:02+00:00"); e != nil {
		t.Fatal(e)
	}
	_, e = claimTime(nil, ClaimNative, stamp(2))
	requireProfileUnavailable(t, e)
}
func TestStage2TranscriptResolvedPaths(t *testing.T) {
	root := t.TempDir()
	destination := filepath.Join(root, "outside/inside")
	if e := os.MkdirAll(destination, 0700); e != nil {
		t.Fatal(e)
	}
	link := filepath.Join(root, "link")
	mustLink(t, destination, link)
	runtime := &SourceRuntime{Filesystem: fixtureFilesystem{cwd: root}}
	got, e := resolveLoose(link+"/../missing", runtime)
	want := filepath.Join(root, "outside/missing")
	if e != nil || got != want {
		t.Fatal("symlink must be resolved before ..", got, want, e)
	}
	got, e = resolveLoose("link/../missing", runtime)
	if e != nil || got != want {
		t.Fatal("relative resolution", got, e)
	}
	rawName := filepath.Join(root, "raw-\xff")
	mustWrite(t, rawName, []byte("{}\n"))
	got, e = resolveLoose(rawName, runtime)
	if e != nil || got != rawName {
		t.Fatal("filesystem surrogateescape lost bytes", got, e)
	}
	slug, e := claudeProjectSlugPoints([]rune{'/', 'x', '/', 0xd800})
	if e != nil || slug != "-x--" {
		t.Fatal(slug, e)
	}
	_, e = resolveLoose("relative", nil)
	requireProfileUnavailable(t, e)
}

func TestStage2TranscriptEvidenceAdapter(t *testing.T) {
	f := codexFixture(t)
	ls := writeRecords(t, f.path, codexRefusalRecords(t))
	agent := value.Mapping(value.Field("native_session_id", value.Text(nativeID)), value.Field("agent_id", value.Text(turnID)), value.Field("execution_id", value.Text(nativeID)))
	state := value.Mapping(value.Field("agent", agent), value.Field("workspace", value.Text(f.state.Workspace)), value.Field("process", processRecord(f.state.Process)))
	cfg := value.Mapping(value.Field("harness", value.Text("codex")), value.Field("config_home", value.Text(f.cfg.ConfigHome)))
	// Neither the supplied home key nor executable directories are read by
	// non-worker proofs or scans, even when they would fail if reached.
	cfg.Set("home", value.Mapping())
	cfg.Set("process_exe_dirs", value.Boolean(true))
	attempt := value.Mapping(value.Field("created_at", value.Text(stamp(2))), value.Field("native_turn_id", value.Text(turnID)))
	req := coordination.EvidenceRequest{Config: cfg, State: state, Attempt: attempt, Recorded: value.Mapping(value.Field("at", value.IntegerText(strconv.FormatInt(epochStamp(t, stamp(2)), 10)))), Locators: locatorsRecord(ls)}
	evidence := f.reader.RecoveryEvidence()
	proof, e := evidence.Read(context.Background(), coordination.EvidenceCodexRefusal, req)
	if e != nil || !stringIs(field(proof, "schema"), "cairn.codex-refused-turn/1") {
		t.Fatal(proof, e)
	}
	order := []string{"schema", "records", "native_session_id", "native_turn_id"}
	for i, key := range order {
		if !slices.Equal(proof.Members[i].Key, []rune(key)) {
			t.Fatal("Source proof order lost", proof)
		}
	}
	latest, e := evidence.Read(context.Background(), coordination.EvidenceCodexLatest, req)
	if e != nil || latest == nil || latest.Kind != value.Array || len(latest.Items) != 2 || !hasInteger(field(latest.Items[1], "offset"), ls[2].Offset) {
		t.Fatal(latest, e)
	}
	// A latest-end scan never indexes workspace; the refusal proof does, only
	// after its pinned record shape checks have succeeded.
	req.State = state.Without("workspace")
	if _, e = evidence.Read(context.Background(), coordination.EvidenceCodexLatest, req); e != nil {
		t.Fatal("unused workspace required", e)
	}
	_, e = evidence.Read(context.Background(), coordination.EvidenceCodexRefusal, req)
	if value.ErrorClass(e) != "KeyError" {
		t.Fatal("missing workspace lost Source exception class", e)
	}

}

func TestStage2DaemonNaiveComparisonOrder(t *testing.T) {
	cfg, state, path := claudeFixture(t)
	for _, tc := range []struct{ name, raw, message string }{
		{"unreadable", "{\n", "native transcript has an unreadable record"},
		{"unstamped", "{}\n", "native transcript has no timestamped turn record"},
		{"bad_stamp", "{\"timestamp\":\"invalid\"}\n", "native record timestamp shape is unknown"},
		{"stamped", "{\"timestamp\":\"" + baseStamp + "\"}\n", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			mustWrite(t, path, []byte(tc.raw))
			_, err := DaemonStartClaimEvidence(cfg, state, Attempt{CreatedAt: "2026-11-08T12:00:02"}, fixtureProfile(t))
			if tc.message != "" {
				requireError(t, err, tc.message)
			} else if value.ErrorClass(err) != "TypeError" {
				t.Fatal("valid timestamp must reach naive/aware comparison", err)
			}
		})
	}
}

func TestStage2DaemonWorkerEvidence(t *testing.T) {
	cfg, state, path := claudeFixture(t)
	root, home := t.TempDir(), t.TempDir()
	exeDir := filepath.Join(home, ".local/share/claude/versions")
	state.Process = writeProc(t, root, 400, 2, 100, "daemon", "/opt/claude/claude", []string{"claude", "daemon"})
	writeProc(t, root, 401, 400, 101, "bg-pty-host", "/opt/claude/claude", []string{"claude", "bg-pty-host"})
	worker := writeProc(t, root, 402, 401, 102, "claude", exeDir+"/claude", []string{"claude", "--resume", path})
	stateRecord := value.Mapping(value.Field("agent", value.Mapping(value.Field("native_session_id", value.Text(nativeID)))), value.Field("workspace", value.Text(state.Workspace)), value.Field("process", processRecord(state.Process)))
	config := value.Mapping(value.Field("harness", value.Text("claude")), value.Field("config_home", value.Text(cfg.ConfigHome)), value.Field("home", value.Mapping()))
	homeCalls := 0
	r := NewReader(fixtureProfile(t))
	r.ProcRoot = root
	r.Runtime.Filesystem = fixtureFilesystem{home: home, homeCalls: &homeCalls}
	req := coordination.EvidenceRequest{Config: config, State: stateRecord}
	got, err := r.RecoveryEvidence().Read(context.Background(), coordination.EvidenceDaemonWorker, req)
	if err != nil || homeCalls != 1 {
		t.Fatal(got, err, homeCalls)
	}
	want := value.Mapping(value.Field("pid", value.IntegerText(strconv.Itoa(worker.PID))), value.Field("start", value.IntegerText("102")), value.Field("state", value.Text("S")), value.Field("parent", value.IntegerText("401")), value.Field("boot", value.Text(fixtureBoot)))
	if !reflect.DeepEqual(got, want) {
		t.Fatal("worker must retain the selected view's five ordered fields", got, want)
	}
	for _, tc := range []struct {
		name, harness string
		dirs          *value.Value
		found         bool
	}{
		{"explicit", "claude", &value.Value{Kind: value.Array, Items: []*value.Value{value.Text(exeDir)}}, true},
		{"explicit_empty", "claude", &value.Value{Kind: value.Array}, false},
		{"other_harness", "codex", nil, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			homeCalls = 0
			req.Config = config.Without("process_exe_dirs")
			req.Config.Set("harness", value.Text(tc.harness))
			if tc.dirs != nil {
				req.Config.Set("process_exe_dirs", tc.dirs)
			}
			got, err := r.RecoveryEvidence().Read(context.Background(), coordination.EvidenceDaemonWorker, req)
			if homeCalls != 0 {
				t.Fatal("unreached Home called", homeCalls)
			}
			if tc.found {
				if err != nil || !reflect.DeepEqual(got, want) {
					t.Fatal(got, err)
				}
			} else {
				requireError(t, err, "expected exactly one daemon worker for this conversation, found 0")
			}
		})
	}
	// A non-worker site ignores both home and executable-directory settings.
	req.Config = config.Without("process_exe_dirs")
	req.Config.Set("process_exe_dirs", value.Boolean(true))
	req.Attempt = value.Mapping(value.Field("created_at", value.Text(stamp(2))))
	mustWrite(t, path, []byte("{\"timestamp\":\""+baseStamp+"\"}\n"))
	homeCalls = 0
	if _, err := r.RecoveryEvidence().Read(context.Background(), coordination.EvidenceDaemonClaim, req); err != nil || homeCalls != 0 {
		t.Fatal("non-worker read executable directory inputs", err, homeCalls)
	}
}

func TestStage2EvidenceConfigHomeReach(t *testing.T) {
	f := codexFixture(t)
	state := value.Mapping(value.Field("agent", value.Mapping(value.Field("native_session_id", value.Text(nativeID)))), value.Field("workspace", value.Text(f.state.Workspace)), value.Field("process", processRecord(f.state.Process)))
	for _, home := range []*value.Value{nil, value.Boolean(true), value.Mapping()} {
		config := value.Mapping(value.Field("harness", value.Text("claude")), value.Field("process_exe_dirs", value.Boolean(true)))
		if home != nil {
			config.Set("config_home", home)
		}
		req := coordination.EvidenceRequest{Config: config, State: state}
		for _, tc := range []struct {
			kind                coordination.EvidenceKind
			contiguity, reached string
		}{
			{coordination.EvidenceNativeRefusal, "native records are not one contiguous enqueue, dequeue, refusal sequence", "native session file cannot be derived from the binding"},
			{coordination.EvidenceCodexRefusal, "Codex refusal records are not contiguous", "Codex rollout owner is unavailable"},
		} {
			req.Locators = locatorsRecord(nil)
			_, err := f.reader.RecoveryEvidence().Read(context.Background(), tc.kind, req)
			requireError(t, err, tc.contiguity)
			req.Locators = locatorsRecord([]Locator{pin([]byte("{}\n"), 0), pin([]byte("{}\n"), 3), pin([]byte("{}\n"), 6)})
			_, err = f.reader.RecoveryEvidence().Read(context.Background(), tc.kind, req)
			requireError(t, err, tc.reached)
		}
	}
}

func TestStage2NativeRefusalCommandAndContent(t *testing.T) {
	for _, path := range []string{"/coordination.py", "//coordination.py", "/x/coordination.py"} {
		_, _, ok := nativeCommand(value.Text("UserPromptSubmit operation blocked by hook:\n[/usr/bin/python3 " + path + " hook --config /binding.json]: refused"))
		if ok != (path == "/x/coordination.py") {
			t.Fatal(path, ok)
		}
	}
	cfg, state, path := claudeFixture(t)
	attempt := Attempt{CreatedAt: stamp(2), Delivery: Delivery{DeliveryID: turnID, Attempts: 1, State: "leased"}}
	for _, content := range []any{nil, true, map[string]any{}} {
		records := claudeRefusalRecords(state, cfg, attempt)
		records[2]["content"] = content
		ls := writeRecords(t, path, records)
		_, err := nativeRefusalFixture(t, cfg, state, attempt, ls)
		if value.ErrorClass(err) != "AttributeError" {
			t.Fatal("present non-string content must fail at partition", content, err)
		}
	}
	records := claudeRefusalRecords(state, cfg, attempt)
	delete(records[2], "content")
	_, err := nativeRefusalFixture(t, cfg, state, attempt, writeRecords(t, path, records))
	requireError(t, err, "native refusal record is not this binding's post-claim refusal of this wake")
}

type stage2BindingRoutes struct{ fixtureRoutes }

func (stage2BindingRoutes) Filesystem() value.FilesystemRoutes { return fixtureFilesystem{} }

func TestStage2ConcreteRecoveryBinding(t *testing.T) {
	profile := fixtureProfile(t)
	profile.Routes = stage2BindingRoutes{}
	profile.Definition.Readers[coordination.SiteSessionRead] = value.ReaderRoute{Input: "text-json", Codec: &value.Codec{Name: "utf-8", Errors: "strict"}, Domain: "synthetic-kernel-fixtures"}
	yes, no := true, false
	profile.Definition.Writers[coordination.SiteSessionWrite] = value.WriterRoute{Route: "dump", Codec: &value.Codec{Name: "utf-8", Errors: "strict"}, EnsureASCII: &no, Compact: &no, TrailingLF: &yes, Domain: "synthetic-kernel-fixtures"}
	cfg, state, path := claudeFixture(t)
	root := t.TempDir()
	daemon := writeProc(t, root, 400, 2, 100, "daemon", "/opt/claude/claude", []string{"claude", "daemon"})
	writeProc(t, root, 401, 400, 101, "bg-pty-host", "/opt/claude/claude", []string{"claude", "bg-pty-host"})
	writeProc(t, root, 402, 401, 102, "claude", "/opt/claude/claude", []string{"claude", "--resume", path})
	mustWrite(t, path, []byte("{\"timestamp\":\""+baseStamp+"\"}\n"))
	config := value.Mapping(value.Field("binding", value.Text("concrete-fixture")), value.Field("state_dir", value.Text(t.TempDir())), value.Field("harness", value.Text("claude")), value.Field("config_home", value.Text(cfg.ConfigHome)), value.Field("process_exe_dirs", &value.Value{Kind: value.Array, Items: []*value.Value{value.Text("/opt/claude")}}))
	ref := value.Mapping(value.Field("agent_id", value.Text(turnID)), value.Field("execution_id", value.Text(nativeID)))
	agent := value.Mapping(value.Field("agent_id", value.Text(turnID)), value.Field("execution_id", value.Text(nativeID)), value.Field("native_session_id", value.Text(nativeID)))
	stateRecord := value.Mapping(value.Field("agent", agent), value.Field("workspace", value.Text(state.Workspace)), value.Field("process", processRecord(daemon)), value.Field("inbox_attempt", value.Mapping(value.Field("attempt_id", value.Text(turnID)), value.Field("session", ref), value.Field("created_at", value.Text(stamp(2))))), value.Field("inbox_intent", value.Mapping(value.Field("request_id", value.Text(turnID)), value.Field("session", ref))))
	calls := 0
	host := coordination.NewFileHost(coordination.FileHostOptions{Profile: profile, Log: func(string) error { return nil }, Call: func(_ context.Context, _ *value.Value, operation string, _ *value.Value, _ coordination.NativeCallOptions) (*value.Value, error) {
		calls++
		if operation != "session-inbox-reconcile" {
			t.Fatal(operation)
		}
		return nil, nil
	}})
	s, release, err := coordination.OpenSession(config, nativeID, host, true)
	if err != nil {
		t.Fatal(err)
	}
	s.Record = stateRecord
	if err = s.Save(); err != nil {
		t.Fatal(err)
	}
	if err = release(); err != nil {
		t.Fatal(err)
	}
	intentPath := filepath.Join(text(config.Get("state_dir")), "intents", turnID+".json")
	mustWrite(t, intentPath, []byte(`{"schema":"cairn.inbox-intent/1","attempt_id":"`+turnID+`","kind":"notice","session":{"agent_id":"`+turnID+`","execution_id":"`+nativeID+`"}}`))
	r := NewReader(profile)
	r.ProcRoot = root
	engine := r.BindRecovery(host, coordination.RecoveryHooks{})
	request := coordination.RecoveryRequest{NativeSessionID: nativeID, AttemptID: turnID, CheckOnly: true}
	out, err := engine.RecoverDaemonStartClaim(context.Background(), config, request)
	if err != nil || !value.SameText(out.Get("status"), "eligible") || calls != 0 {
		t.Fatal(out, err, calls)
	}
	request.CheckOnly = false
	out, err = engine.RecoverDaemonStartClaim(context.Background(), config, request)
	if err != nil || out == nil || calls != 1 {
		t.Fatal(out, err, calls)
	}
	stored, err := host.Load(s.Path)
	if err != nil {
		t.Fatal(err)
	}
	worker := stored.Get("process")
	if len(worker.Members) != 5 || worker.Get("pid").Integer != "402" || worker.Get("parent").Integer != "401" || stored.Get("inbox_attempt") != nil {
		t.Fatal("concrete recovery did not persist/settle its bound worker", stored)
	}
}

func TestStage2BoundReaderAliveErrors(t *testing.T) {
	f := codexFixture(t)
	sentinel := &os.PathError{Op: "read", Path: "synthetic-proc", Err: syscall.EIO}
	f.reader.AliveError = func(procpeer.ProcessRef) (bool, error) { return false, sentinel }
	_, err := f.reader.CodexRolloutPath(f.cfg, f.state)
	if err != sentinel {
		t.Fatal("concrete liveness error became a transcript refusal", err)
	}
}
