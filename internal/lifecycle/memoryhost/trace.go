package memoryhost

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"golang.org/x/sys/unix"
)

// invocationTrace is a best-effort Source observer. It never touches a memory
// lock or changes output, state, authority or outcome when a write is dropped.
type invocationTrace struct {
	directory     int
	invocation    string
	started       time.Time
	entryRecorded bool
	fields        map[string]any
}

func newTrace(config string) *invocationTrace {
	t := &invocationTrace{directory: -1, invocation: strings.ReplaceAll(uuid.NewString(), "-", ""), started: time.Now(), fields: map[string]any{}}
	fd, err := unix.Open(filepath.Join(filepath.Dir(config), "invocation-trace"), unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return t
	}
	var info unix.Stat_t
	if unix.Fstat(fd, &info) != nil || info.Mode&unix.S_IFMT != unix.S_IFDIR || info.Uid != uint32(os.Geteuid()) || info.Mode&0077 != 0 {
		unix.Close(fd)
		return t
	}
	t.directory = fd
	return t
}
func (t *invocationTrace) write(record map[string]any) bool {
	if t == nil || t.directory < 0 {
		return false
	}
	raw, err := json.Marshal(record)
	if err != nil || len(raw)+1 > 512 {
		return false
	}
	raw = append(raw, '\n')
	fd, err := unix.Openat(t.directory, "trace.jsonl", unix.O_RDWR|unix.O_APPEND|unix.O_CREAT|unix.O_NOFOLLOW|unix.O_NONBLOCK|unix.O_CLOEXEC, 0600)
	if err != nil {
		return false
	}
	defer unix.Close(fd)
	var info unix.Stat_t
	if unix.Fstat(fd, &info) != nil || info.Mode&unix.S_IFMT != unix.S_IFREG || info.Uid != uint32(os.Geteuid()) || info.Mode&0077 != 0 {
		return false
	}
	if unix.Flock(fd, unix.LOCK_EX|unix.LOCK_NB) != nil {
		return false
	}
	if unix.Fstat(fd, &info) != nil {
		return false
	}
	if info.Size > 0 {
		var last [1]byte
		if _, err = unix.Pread(fd, last[:], info.Size-1); err != nil {
			return false
		}
		if last[0] != '\n' {
			raw = append([]byte{'\n'}, raw...)
		}
	}
	if info.Size+int64(len(raw)) > 8*1024*1024 {
		return false
	}
	n, err := unix.Write(fd, raw)
	return err == nil && n == len(raw)
}
func (t *invocationTrace) record(kind string) map[string]any {
	return map[string]any{"schema": "cairn.hook-invocation/1", "kind": kind, "inv": t.invocation, "at": time.Now().UnixMilli()}
}
func (t *invocationTrace) entry() {
	r := t.record("entry")
	if n := os.Getpid(); n >= 1 && n <= 4194304 {
		r["pid"] = n
	}
	if n := os.Getppid(); n >= 1 && n <= 4194304 {
		r["ppid"] = n
	}
	t.entryRecorded = t.write(r)
}
func (t *invocationTrace) parsed(event *value.Value) {
	r := t.record("parsed")
	if name := event.Get("hook_event_name"); name != nil && name.Kind == value.String {
		s := string(name.String)
		if !member(s, "SessionStart", "UserPromptSubmit", "PreCompact", "SessionEnd", "PostToolUse", "PostToolUseFailure", "Stop") {
			s = "other"
		}
		t.fields["event"] = s
		r["event"] = s
	}
	for _, pair := range [][2]string{{"session_id", "session"}, {"turn_id", "turn"}} {
		v := event.Get(pair[0])
		if v == nil || v.Kind != value.String {
			continue
		}
		s := string(v.String)
		id, err := uuid.Parse(s)
		if err == nil && id != uuid.Nil && id.String() == s {
			t.fields[pair[1]] = s
			r[pair[1]] = s
		}
	}
	t.write(r)
}
func member(s string, choices ...string) bool {
	for _, c := range choices {
		if c == s {
			return true
		}
	}
	return false
}
func (t *invocationTrace) finish(code int, phase string) {
	if t == nil {
		return
	}
	defer func() {
		if t.directory >= 0 {
			unix.Close(t.directory)
			t.directory = -1
		}
	}()
	r := t.record("exit")
	r["entry_recorded"] = t.entryRecorded
	if d := time.Since(t.started).Milliseconds(); d >= 0 && d <= 3600000 {
		r["duration_ms"] = d
	}
	r["main_return_code"] = code
	r["outcome"] = "unlabelled_return"
	if s, ok := t.fields["outcome"].(string); ok && member(s, "engine_completed", "early_return:disabled_env", "early_return:no_memory_marker", "early_return:wake_coordinated", "early_return:stop_not_boundary", "early_return:unsupported_event", "early_return:stop_too_little_dialogue", "lock_busy_skip", "required_refused", "error", "unexpected", "unlabelled_return") {
		r["outcome"] = s
	}
	if v, ok := t.fields["phase"].(string); ok {
		phase = v
	}
	if member(phase, "configuration", "input", "activation", "handle") {
		r["phase"] = phase
	}
	for _, key := range []string{"event", "session", "turn"} {
		if v, ok := t.fields[key]; ok {
			r[key] = v
		}
	}
	for key, allowed := range map[string][]string{"stage": {"activation", "validated", "bridge", "lock_attempt", "locked", "recall", "capture", "saved"}, "cause": {"lock_busy", "blocking_io"}, "error_class": {"RequiredContextRefused", "HookError", "BlockingIOError", "OSError", "ValueError", "KeyError", "TypeError"}} {
		if s, ok := t.fields[key].(string); ok && member(s, allowed...) {
			r[key] = s
		}
	}
	if n, ok := t.fields["context_bytes"].(int); ok && n >= 0 && n <= 1024*1024 {
		r["context_bytes"] = n
	}
	if v, ok := t.fields["bound"].(bool); ok {
		r["bound"] = v
	}
	t.write(r)
}
