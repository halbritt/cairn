package coordination

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/procpeer"
)

const fixtureBoot = "fixture-boot"

func mustRelease(t *testing.T, lease *LockLease) {
	t.Helper()
	if err := lease.Release(); err != nil {
		t.Fatal(err)
	}
}

func TestCoordinationLocks(t *testing.T) {
	if path := os.Getenv("CAIRN_COORDINATION_LOCK_CHILD"); path != "" {
		file, err := os.OpenFile(path, os.O_RDWR, 0)
		if err != nil {
			os.Exit(2)
		}
		if err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
			os.Exit(3)
		}
		fmt.Println("READY")
		_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
		if err := file.Close(); err != nil {
			os.Exit(4)
		}
		os.Exit(0)
	}
	t.Run("new_session_paths_and_private_files", func(t *testing.T) {
		root := filepath.Join(t.TempDir(), "new")
		state := SessionStatePath(root, "binding", "native")
		lease, err := AcquireSessionLock(state)
		if err != nil {
			t.Fatal(err)
		}
		mustRelease(t, lease)
		info, err := os.Stat(SessionLockPath(state))
		if err != nil || info.Mode().Perm() != 0o600 {
			t.Fatal(info, err)
		}
		directory, err := os.Stat(root)
		if err != nil || directory.Mode().Perm() != 0o700 {
			t.Fatal(directory, err)
		}
		if SessionLockPath(filepath.Join(root, "legacy")) != filepath.Join(root, "legacy.lock") || CueLockPath(state) != strings.TrimSuffix(state, ".json")+".cuelock" {
			t.Fatal("legacy lock path changed")
		}
		ledgerDir := filepath.Join(root, "ledger")
		if err := os.Mkdir(ledgerDir, 0o700); err != nil {
			t.Fatal(err)
		}
		control, err := os.Create(filepath.Join(ledgerDir, "umask-reference"))
		if err != nil {
			t.Fatal(err)
		}
		if err := control.Close(); err != nil {
			t.Fatal(err)
		}
		controlInfo, err := os.Stat(control.Name())
		if err != nil {
			t.Fatal(err)
		}
		ledger, err := TryAcquireGrantLedgerLock(ledgerDir, "native")
		if err != nil {
			t.Fatal(err)
		}
		mustRelease(t, ledger)
		ledgerInfo, err := os.Stat(GrantLedgerLockPath(ledgerDir, "native"))
		if err != nil || ledgerInfo.Mode().Perm() != controlInfo.Mode().Perm() {
			t.Fatal("ledger creation did not preserve inherited umask", ledgerInfo, controlInfo, err)
		}
		configDir := filepath.Join(root, "watcher", "new")
		watcher, err := AcquireWatcherLock(configDir)
		if err != nil {
			t.Fatal(err)
		}
		mustRelease(t, watcher)
		configInfo, err := os.Stat(configDir)
		if err != nil || configInfo.Mode().Perm() != 0o700 {
			t.Fatal(configInfo, err)
		}
		watchInfo, err := os.Stat(WatcherLockPath(configDir))
		if err != nil || watchInfo.Mode().Perm() != 0o600 {
			t.Fatal(watchInfo, err)
		}
	})
	t.Run("families_preserve_legacy_inodes", func(t *testing.T) {
		for _, mode := range []os.FileMode{0o644, 0o664} {
			t.Run(fmt.Sprintf("%o", mode), func(t *testing.T) {
				root := t.TempDir()
				state := SessionStatePath(root, "binding", "native")
				if filepath.Base(state) == filepath.Base(SessionStatePath(root, "bindin", "gnative")) {
					t.Fatal("state key lost its NUL separator")
				}
				for _, family := range []struct {
					name, path string
					acquire    func() (*LockLease, error)
				}{
					{"session", SessionLockPath(state), func() (*LockLease, error) { return AcquireSessionLock(state) }},
					{"ledger", GrantLedgerLockPath(root, "native"), func() (*LockLease, error) { return TryAcquireGrantLedgerLock(root, "native") }},
					{"intent", IntentLockPath(root, "attempt"), func() (*LockLease, error) { return TryAcquireIntentLock(root, "attempt") }},
					{"watcher", WatcherLockPath(root), func() (*LockLease, error) { return AcquireWatcherLock(root) }},
					{"cue", CueLockPath(state), func() (*LockLease, error) { lease, _, err := TryAcquireCueLock(state); return lease, err }},
				} {
					t.Run(family.name, func(t *testing.T) {
						if err := os.MkdirAll(filepath.Dir(family.path), 0o700); err != nil {
							t.Fatal(err)
						}
						if err := os.WriteFile(family.path, []byte("legacy-lock"), mode); err != nil {
							t.Fatal(err)
						}
						if err := os.Chmod(family.path, mode); err != nil {
							t.Fatal(err)
						}
						before, err := os.Stat(family.path)
						if err != nil {
							t.Fatal(err)
						}
						lease, err := family.acquire()
						if err != nil || lease == nil {
							t.Fatal(err)
						}
						t.Cleanup(func() { mustRelease(t, lease) })
						blocked, err := family.acquire()
						if blocked != nil || family.name == "cue" && err != nil || family.name != "cue" && !IsSessionBusy(err) {
							t.Fatalf("second open did not contend: %v %v", blocked, err)
						}
						mustRelease(t, lease)
						mustRelease(t, lease)
						after, err := os.Stat(family.path)
						if err != nil {
							t.Fatal(err)
						}
						content, err := os.ReadFile(family.path)
						if err != nil {
							t.Fatal(err)
						}
						if !os.SameFile(before, after) || after.Mode().Perm() != mode || string(content) != "legacy-lock" {
							t.Fatal("legacy inode, permissions or bytes changed")
						}
						again, err := family.acquire()
						if err != nil {
							t.Fatal(err)
						}
						mustRelease(t, again)
					})
				}
			})
		}
	})
	t.Run("symlink_and_other_process_contend", func(t *testing.T) {
		root := t.TempDir()
		state := filepath.Join(root, "session.json")
		target := filepath.Join(root, "legacy")
		if err := os.WriteFile(target, []byte("kept"), 0o644); err != nil {
			t.Fatal(err)
		}
		if err := os.Symlink(target, SessionLockPath(state)); err != nil {
			t.Fatal(err)
		}
		before, err := os.Stat(target)
		if err != nil {
			t.Fatal(err)
		}
		cmd := exec.Command(os.Args[0], "-test.run=^TestCoordinationLocks$")
		cmd.Env = []string{"CAIRN_COORDINATION_LOCK_CHILD=" + target, "GORACE=atexit_sleep_ms=0"}
		input, err := cmd.StdinPipe()
		if err != nil {
			t.Fatal(err)
		}
		output, err := cmd.StdoutPipe()
		if err != nil {
			t.Fatal(err)
		}
		var stderr bytes.Buffer
		cmd.Stderr = &stderr
		if err := cmd.Start(); err != nil {
			t.Fatal(err)
		}
		if cmd.Process.Pid <= 1 {
			t.Fatal("invalid owned lock-holder PID")
		}
		waited := false
		t.Cleanup(func() {
			_ = input.Close()
			if !waited && cmd.Process.Pid > 1 {
				_ = cmd.Process.Kill()
				_ = cmd.Wait()
			}
		})
		ready := make(chan string, 1)
		go func() { line, _ := bufio.NewReader(output).ReadString('\n'); ready <- line }()
		select {
		case line := <-ready:
			if line != "READY\n" {
				t.Fatalf("holder not ready: %q %s", line, stderr.String())
			}
		case <-time.After(3 * time.Second):
			t.Fatal("holder startup timed out")
		}
		if lease, err := AcquireSessionLock(state); lease != nil || !IsSessionBusy(err) {
			t.Fatal("symlink bypassed owned child lock", lease, err)
		}
		if _, err := input.Write([]byte("release\n")); err != nil {
			t.Fatal(err)
		}
		if err := cmd.Wait(); err != nil {
			t.Fatal(err, stderr.String())
		}
		waited = true
		lease, err := AcquireSessionLock(state)
		if err != nil {
			t.Fatal(err)
		}
		mustRelease(t, lease)
		after, err := os.Stat(target)
		if err != nil {
			t.Fatal(err)
		}
		link, err := os.Lstat(SessionLockPath(state))
		if err != nil {
			t.Fatal(err)
		}
		if !os.SameFile(before, after) || link.Mode()&os.ModeSymlink == 0 {
			t.Fatal("symlink or target inode replaced")
		}
	})
	t.Run("nested_ledger_wait_and_context", func(t *testing.T) {
		root := t.TempDir()
		session, err := AcquireSessionLock(filepath.Join(root, "session.json"))
		if err != nil {
			t.Fatal(err)
		}
		defer mustRelease(t, session)
		holder, err := TryAcquireGrantLedgerLock(root, "native")
		if err != nil {
			t.Fatal(err)
		}
		released := make(chan error, 1)
		go func() { time.Sleep(60 * time.Millisecond); released <- holder.Release() }()
		started := time.Now()
		lease, err := AcquireGrantLedgerLock(context.Background(), root, "native", DefaultLedgerLockWait)
		if err != nil || time.Since(started) >= DefaultLedgerLockWait {
			t.Fatal("bounded nested acquisition failed", err)
		}
		if err := <-released; err != nil {
			t.Fatal(err)
		}
		defer mustRelease(t, lease)
		started = time.Now()
		blocked, err := AcquireGrantLedgerLock(context.Background(), root, "native", 35*time.Millisecond)
		if blocked != nil || !IsSessionBusy(err) || time.Since(started) > 500*time.Millisecond {
			t.Fatal("ledger wait did not remain bounded", blocked, err)
		}
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
		defer cancel()
		blocked, err = AcquireGrantLedgerLock(ctx, root, "native", time.Second)
		if blocked != nil || !IsSessionBusy(err) || !errors.Is(err, context.DeadlineExceeded) {
			t.Fatal("context deadline lost", blocked, err)
		}
		cancelled, stop := context.WithCancel(context.Background())
		stop()
		blocked, err = AcquireGrantLedgerLock(cancelled, root, "native", time.Second)
		if blocked != nil || !IsSessionBusy(err) || !errors.Is(err, context.Canceled) {
			t.Fatal("cancellation lost", blocked, err)
		}
		// Python makes one attempt before examining a contended deadline.
		free, err := AcquireGrantLedgerLock(cancelled, root, "free", 0)
		if err != nil {
			t.Fatal(err)
		}
		mustRelease(t, free)
		intent, err := TryAcquireIntentLock(root, "attempt")
		if err != nil {
			t.Fatal(err)
		}
		defer mustRelease(t, intent)
		if _, err := AcquireIntentLock(context.Background(), root, "attempt", 10*time.Millisecond); !IsSessionBusy(err) || err.Error() != "SESSION_BUSY: another inbox command for this attempt is active" {
			t.Fatal(err)
		}
	})
	t.Run("cue_lock_and_arrival", func(t *testing.T) {
		root := t.TempDir()
		state := filepath.Join(root, "state.json")
		first, owned, err := TryAcquireCueLock(state)
		if err != nil || !owned {
			t.Fatal(first, owned, err)
		}
		if lease, owned, err := TryAcquireCueLock(state); lease != nil || owned || err != nil {
			t.Fatal("cue contention must skip", lease, owned, err)
		}
		mustRelease(t, first)
		if lease, owned, err := TryAcquireCueLock(state); !owned || err != nil {
			t.Fatal(owned, err)
		} else {
			mustRelease(t, lease)
		}
		previous := []any{"exec", int64(10), int64(3), int64(2), int64(0)}
		for _, tc := range []struct {
			name     string
			previous any
			sig      CueSignature
			want     bool
		}{
			{"empty", nil, CueSignature{ExecutionID: "exec", LatestPosition: 20}, false},
			{"new_execution", previous, CueSignature{"new", 10, 3, 2, 0}, true},
			{"malformed", []any{"exec", 10, 3}, CueSignature{"exec", 10, 3, 2, 0}, true},
			{"non_integer", []any{"exec", 10.0, 3, 2, 0}, CueSignature{"exec", 10, 3, 2, 0}, true},
			{"boolean", []any{"exec", true, 3, 2, 0}, CueSignature{"exec", 10, 3, 2, 0}, true},
			{"position", previous, CueSignature{"exec", 11, 3, 2, 0}, true},
			{"growth", previous, CueSignature{"exec", 10, 3, 3, 0}, true},
			{"redistribution", previous, CueSignature{"exec", 10, 1, 2, 2}, false},
			{"shrink", previous, CueSignature{"exec", 9, 1, 1, 0}, false},
			{"exact_json_numbers", []any{"exec", json.Number("10"), json.Number("3"), json.Number("2"), json.Number("0")}, CueSignature{"exec", 10, 3, 2, 0}, false},
			{"exponent_is_not_int", []any{"exec", json.Number("1e1"), 3, 2, 0}, CueSignature{"exec", 10, 3, 2, 0}, true},
			{"wide_sum", []any{"exec", 10, int64(math.MaxInt64), int64(0), int64(0)}, CueSignature{"exec", 10, math.MaxInt64, 1, 0}, true},
		} {
			t.Run(tc.name, func(t *testing.T) {
				if got := CueArrival(tc.previous, tc.sig); got != tc.want {
					t.Fatal(got, tc.want)
				}
			})
		}
	})
	t.Run("atomic_state_write_and_cleanup", func(t *testing.T) {
		root := t.TempDir()
		path := filepath.Join(root, "new", "session.json")
		state := map[string]any{"text": "雪<&", "count": 7}
		if err := WriteStateFile(path, state); err != nil {
			t.Fatal(err)
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if !utf8.Valid(raw) || !bytes.HasSuffix(raw, []byte{'\n'}) || !bytes.Contains(raw, []byte("雪<&")) {
			t.Fatalf("invalid UTF-8 JSON framing: %q", raw)
		}
		var decoded map[string]any
		if err := json.Unmarshal(raw, &decoded); err != nil || decoded["text"] != "雪<&" || decoded["count"] != float64(7) {
			t.Fatal(decoded, err)
		}
		before, err := os.Stat(path)
		if err != nil {
			t.Fatal(err)
		}
		if before.Mode().Perm() != 0o600 {
			t.Fatal("state is not private", before.Mode())
		}
		if err := WriteStateFile(path, map[string]any{"invalid": math.Inf(1)}); err == nil {
			t.Fatal("invalid JSON replaced state")
		}
		still, err := os.ReadFile(path)
		if err != nil || !bytes.Equal(still, raw) {
			t.Fatal("failed encode changed state", err)
		}
		if err := WriteStateFile(path, map[string]any{"text": "next"}); err != nil {
			t.Fatal(err)
		}
		after, err := os.Stat(path)
		if err != nil {
			t.Fatal(err)
		}
		if os.SameFile(before, after) {
			t.Fatal("state was overwritten instead of atomically replaced")
		}
		badTarget := filepath.Join(filepath.Dir(path), "directory")
		if err := os.Mkdir(badTarget, 0o700); err != nil {
			t.Fatal(err)
		}
		if err := WriteStateFile(badTarget, state); err == nil {
			t.Fatal("rename over directory succeeded")
		}
		entries, err := os.ReadDir(filepath.Dir(path))
		if err != nil {
			t.Fatal(err)
		}
		for _, entry := range entries {
			if strings.HasPrefix(entry.Name(), ".coordination-") {
				t.Fatal("temporary state leaked", entry.Name())
			}
		}
	})
}

type processFixture struct {
	pid, parent, pgid, sid int
	start                  uint64
	state, comm, exe       string
	argv                   []string
	environ                []byte
}

func procFixtureRoot(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	directory := filepath.Join(root, "sys/kernel/random")
	if err := os.MkdirAll(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "boot_id"), []byte(fixtureBoot+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	return root
}

func writeProcessFixture(t *testing.T, root string, p processFixture) procpeer.ProcessRef {
	t.Helper()
	directory := filepath.Join(root, strconv.Itoa(p.pid))
	if err := os.MkdirAll(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	if p.state == "" {
		p.state = "S"
	}
	if p.comm == "" {
		p.comm = "helper"
	}
	fields := make([]string, 22)
	for i := range fields {
		fields[i] = "0"
	}
	fields[0] = p.state
	fields[1] = strconv.Itoa(p.parent)
	fields[2] = strconv.Itoa(p.pgid)
	fields[3] = strconv.Itoa(p.sid)
	fields[19] = strconv.FormatUint(p.start, 10)
	values := map[string][]byte{"stat": []byte(fmt.Sprintf("%d (name (with) spaces) %s\n", p.pid, strings.Join(fields, " "))), "comm": []byte(p.comm + "\n"), "cmdline": []byte(strings.Join(p.argv, "\x00") + "\x00"), "environ": p.environ}
	for name, raw := range values {
		if err := os.WriteFile(filepath.Join(directory, name), raw, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if p.exe != "" {
		if err := os.Symlink(p.exe, filepath.Join(directory, "exe")); err != nil {
			t.Fatal(err)
		}
	}
	return procpeer.ProcessRef{PID: p.pid, Start: p.start, Boot: fixtureBoot}
}

func requireCoordinationError(t *testing.T, err error, code, message string) {
	t.Helper()
	var coordination *CoordinationError
	if !errors.As(err, &coordination) || coordination.Code != code || coordination.Message != message {
		t.Fatalf("want %s: %s; got %T %v", code, message, err, err)
	}
}

func TestOwnerProcessAndSupervisors(t *testing.T) {
	t.Run("declared_owner_requires_observed_ancestry", func(t *testing.T) {
		root := procFixtureRoot(t)
		writeProcessFixture(t, root, processFixture{pid: 210, parent: 220, start: 11, argv: []string{"hook"}})
		ancestor := writeProcessFixture(t, root, processFixture{pid: 220, parent: 230, start: 22, comm: "native", argv: []string{"native"}})
		writeProcessFixture(t, root, processFixture{pid: 230, parent: 1, start: 23, comm: "native", argv: []string{"native"}})
		cfg := OwnerConfig{ProcessNames: []string{"native"}}
		for _, declared := range []any{int(220), int64(220), json.Number("220")} {
			got, err := OwnerProcessAt(root, 210, cfg, declared)
			if err != nil || got != ancestor {
				t.Fatal(got, err)
			}
		}
		for _, declared := range []any{0, 1, -1, "220", 220.0, true, json.Number("220.0"), json.Number("220e0")} {
			_, err := OwnerProcessAt(root, 210, cfg, declared)
			requireCoordinationError(t, err, "INVALID_HOST", "invalid native process ID")
		}
		_, err := OwnerProcessAt(root, 210, cfg, 999)
		requireCoordinationError(t, err, "INVALID_HOST", "native process is not an observed ancestor")
		got, err := OwnerProcessAt(root, 210, cfg, nil)
		if err != nil || got != ancestor {
			t.Fatal(got, err)
		}
	})
	t.Run("supervisors_are_not_conversation_owners", func(t *testing.T) {
		for _, argv := range [][]string{{"claude", "daemon"}, {"claude", "bg-pty-host"}, {"claude bg-pty-host", "--bg-pty-host"}, {"claude", "bg-spare"}, {"claude bg-spare", "--bg-spare"}} {
			root := procFixtureRoot(t)
			writeProcessFixture(t, root, processFixture{pid: 310, parent: 320, start: 10, comm: "hook"})
			supervisor := writeProcessFixture(t, root, processFixture{pid: 320, parent: 330, start: 20, comm: "claude", argv: argv})
			owner := writeProcessFixture(t, root, processFixture{pid: 330, parent: 1, start: 30, comm: "claude", argv: []string{"claude", "conversation"}})
			got, err := OwnerProcessAt(root, 310, OwnerConfig{ProcessNames: []string{"claude"}}, nil)
			if err != nil || got != owner {
				t.Fatal(argv, got, err)
			}
			declared, err := OwnerProcessAt(root, 310, OwnerConfig{}, 320)
			if err != nil || declared != supervisor {
				t.Fatal("declared ancestor was filtered by supervisor policy", declared, err)
			}
		}
		if role := ClaudeSupervisorRole(ProcView{}); role != "" {
			t.Fatal(role)
		}
		if role := ClaudeSupervisorRole(ProcView{Argv: []string{"claude bg-pty-host "}}); role != "" {
			t.Fatal("retitled trailing space changed the last-token rule", role)
		}
	})
	t.Run("executable_directory_and_bounded_argv", func(t *testing.T) {
		root := procFixtureRoot(t)
		home := "/fixture/home"
		owner := writeProcessFixture(t, root, processFixture{pid: 410, parent: 1, pgid: 77, start: 44, comm: "  2.1.308\u0085", exe: home + "/.local/share/claude/versions/2.1.308", argv: []string{"native", string([]byte{'x', 0xff}), strings.Repeat("a", 70000)}})
		cfg := OwnerConfig{Harness: "claude", HomeDir: home}
		got, err := OwnerProcessAt(root, 410, cfg, nil)
		if err != nil || got != owner {
			t.Fatal(got, err)
		}
		view, err := ReadProcViewAt(root, 410)
		if err != nil || view.Ref != owner || view.Parent != 1 || view.PGID != 77 || view.State != "S" || view.Comm != "2.1.308" || len(view.Argv) != 3 || view.Argv[1] != "x\ufffd" {
			t.Fatal(view, err)
		}
		record := OwnerRecord{PID: 410, Start: 44, State: "S", Parent: 1, Boot: fixtureBoot}
		if view.OwnerRecord() != record {
			t.Fatal("owner metadata lost", view.OwnerRecord())
		}
		selected, err := OwnerProcessViewAt(root, 410, cfg, nil)
		if err != nil || selected.OwnerRecord() != record {
			t.Fatal(selected, err)
		}
		raw, err := json.Marshal(selected.OwnerRecord())
		if err != nil {
			t.Fatal(err)
		}
		var wire map[string]any
		if err := json.Unmarshal(raw, &wire); err != nil || len(wire) != 5 || wire["state"] != "S" || wire["parent"] != float64(1) {
			t.Fatal("owner wire metadata changed", wire, err)
		}
		// The read cap applies before replacement of an invalid byte expands its
		// UTF-8 representation; the returned text can therefore be longer.
		if len(view.Argv[2]) != 65536-len("native\x00x\xff\x00") {
			t.Fatal("argv read exceeded its raw-byte bound", len(view.Argv[2]))
		}
		cfg.HasProcessExeDirs = true
		_, err = OwnerProcessAt(root, 410, cfg, nil)
		requireCoordinationError(t, err, "INVALID_HOST", "native process is not an observed ancestor")
		cfg.ProcessExeDirs = []string{home + "/.local/share/claude/versions/../versions/"}
		_, err = OwnerProcessAt(root, 410, cfg, nil)
		requireCoordinationError(t, err, "INVALID_HOST", "native process is not an observed ancestor")
		cfg.ProcessExeDirs = []string{home + "/.local/share/claude/versions/./"}
		if got, err := OwnerProcessAt(root, 410, cfg, nil); err != nil || got != owner {
			t.Fatal(got, err)
		}
		cfg.ProcessExeDirs = []string{"/" + home + "/.local/share/claude/versions"}
		_, err = OwnerProcessAt(root, 410, cfg, nil)
		requireCoordinationError(t, err, "INVALID_HOST", "native process is not an observed ancestor")
		for _, tc := range []struct{ path, want string }{{"//fixture///versions/./", "//fixture/versions"}, {"///fixture/versions/", "/fixture/versions"}, {"a/../versions/", "a/../versions"}, {"", "."}} {
			if got := ClaudeExeDirs("claude", true, []string{tc.path}, ""); len(got) != 1 || got[0] != tc.want {
				t.Fatal(tc, got)
			}
		}
		for _, home := range []string{"/", "//"} {
			if got := ClaudeExeDirs("claude", false, nil, home); len(got) != 1 || got[0] != home+".local/share/claude/versions" {
				t.Fatal(home, got)
			}
		}
		if err := os.WriteFile(filepath.Join(root, "410/comm"), []byte("\x1cclaude\x1f\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		if got, err := OwnerProcessAt(root, 410, OwnerConfig{ProcessNames: []string{"claude"}}, nil); err != nil || got != owner {
			t.Fatal("comm strip differs from Python", got, err)
		}
		cfg = OwnerConfig{ProcessNames: []string{"2.1.308"}}
		if err := os.WriteFile(filepath.Join(root, "410/comm"), []byte("unlisted\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		if got, err := OwnerProcessAt(root, 410, cfg, nil); err != nil || got != owner {
			t.Fatal("exe basename did not identify owner", got, err)
		}
		if err := os.Remove(filepath.Join(root, "410/cmdline")); err != nil {
			t.Fatal(err)
		}
		if view, err := ReadProcViewAt(root, 410); err != nil || len(view.Argv) != 0 {
			t.Fatal("unreadable argv discarded the other observations", view, err)
		}
	})
	t.Run("depth_and_mid_walk_exit", func(t *testing.T) {
		root := procFixtureRoot(t)
		var deepest procpeer.ProcessRef
		for depth := 0; depth <= 128; depth++ {
			name := "helper"
			if depth == 128 {
				name = "native"
			}
			deepest = writeProcessFixture(t, root, processFixture{pid: 1000 + depth, parent: 1001 + depth, start: uint64(1000 + depth), comm: name})
		}
		_, err := OwnerProcessAt(root, 1000, OwnerConfig{ProcessNames: []string{"native"}}, nil)
		requireCoordinationError(t, err, "INVALID_HOST", "native process is not an observed ancestor")
		if CallerDescendsFromAt(root, 1000, deepest) {
			t.Fatal("caller search exceeded 64 observations")
		}
		if err := os.WriteFile(filepath.Join(root, "1127/comm"), []byte("native\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		view, err := ReadProcViewAt(root, 1127)
		if err != nil {
			t.Fatal(err)
		}
		if got, err := OwnerProcessAt(root, 1000, OwnerConfig{ProcessNames: []string{"native"}}, nil); err != nil || got != view.Ref {
			t.Fatal("last allowed ancestor was missed", got, err)
		}
		lastCaller, err := ReadProcViewAt(root, 1063)
		if err != nil {
			t.Fatal(err)
		}
		outsideCaller, err := ReadProcViewAt(root, 1064)
		if err != nil {
			t.Fatal(err)
		}
		if !CallerDescendsFromAt(root, 1000, lastCaller.Ref) || CallerDescendsFromAt(root, 1000, outsideCaller.Ref) {
			t.Fatal("caller depth 63/64 boundary changed")
		}
		self, err := procpeer.ProcessRefOf(os.Getpid())
		if err != nil {
			t.Fatal(err)
		}
		if !CallerDescendsFrom(self) {
			t.Fatal("caller wrapper did not begin with its own process")
		}
		writeProcessFixture(t, root, processFixture{pid: 500, parent: 501, start: 9})
		_, err = OwnerProcessAt(root, 500, OwnerConfig{}, nil)
		requireCoordinationError(t, err, "INVALID_HOST", "native process exited during owner lookup")
		if !errors.Is(err, os.ErrNotExist) {
			t.Fatal("owner lookup lost the exit cause", err)
		}
	})
	t.Run("daemon_custody_is_bound_to_identity", func(t *testing.T) {
		root := procFixtureRoot(t)
		worker := writeProcessFixture(t, root, processFixture{pid: 610, parent: 620, start: 61, argv: []string{"claude", "conversation"}})
		writeProcessFixture(t, root, processFixture{pid: 620, parent: 630, start: 62, argv: []string{"claude bg-pty-host", "--bg-pty-host"}})
		daemon := writeProcessFixture(t, root, processFixture{pid: 630, parent: 1, start: 63, argv: []string{"claude", "daemon"}})
		if !ClaudeDaemonManagedAt(root, worker) || !DaemonWorkerOfAt(root, worker, daemon) || !CallerDescendsFromAt(root, 610, daemon) {
			t.Fatal("valid daemon ancestry rejected")
		}
		for _, change := range []func(*procpeer.ProcessRef){func(p *procpeer.ProcessRef) { p.Start++ }, func(p *procpeer.ProcessRef) { p.Boot = "different" }} {
			wrong := daemon
			change(&wrong)
			if DaemonWorkerOfAt(root, worker, wrong) || CallerDescendsFromAt(root, 610, wrong) {
				t.Fatal("foreign daemon identity accepted")
			}
		}
		stale := worker
		stale.Start++
		if ClaudeDaemonManagedAt(root, stale) || DaemonWorkerOfAt(root, stale, daemon) {
			t.Fatal("reused worker PID inherited custody")
		}
		if err := os.WriteFile(filepath.Join(root, "620/cmdline"), []byte("claude\x00bg-spare\x00"), 0o600); err != nil {
			t.Fatal(err)
		}
		if ClaudeDaemonManagedAt(root, worker) || DaemonWorkerOfAt(root, worker, daemon) {
			t.Fatal("non-pty-host parent accepted")
		}
	})
}

func TestProcessScanMarkers(t *testing.T) {
	t.Run("marker_validation_and_observation", func(t *testing.T) {
		for _, pair := range [][2]string{{"", "value"}, {"A=B", "value"}, {"A", ""}} {
			_, err := ScanMarkersAt("/absent", uint32(os.Getuid()), pair[0], pair[1], 0)
			if err == nil || err.Error() != "marker name and value must be nonempty and name must not contain =" {
				t.Fatal(err)
			}
		}
		root := procFixtureRoot(t)
		writeProcessFixture(t, root, processFixture{pid: 101, parent: 1, pgid: 17, sid: 18, start: 100, environ: []byte("CAIRN_REQUEST_ID=delivery\x00"), argv: []string{strings.Repeat("雪", 205), string([]byte{0xff})}})
		writeProcessFixture(t, root, processFixture{pid: 102, parent: 1, start: 200, environ: []byte("CAIRN_REQUEST_ID=delivery-extra\x00")})
		writeProcessFixture(t, root, processFixture{pid: 103, parent: 1, state: "Z", start: 200, environ: []byte("CAIRN_REQUEST_ID=delivery\x00")})
		if err := os.Mkdir(filepath.Join(root, "१२"), 0o700); err != nil {
			t.Fatal(err)
		}
		result, err := ScanMarkersAt(root, uint32(os.Getuid()), "CAIRN_REQUEST_ID", "delivery", 1000)
		if err != nil || result.Clear || result.Coverage != "complete" || len(result.Processes) != 1 || len(result.Unknown) != 0 {
			t.Fatal(result, err)
		}
		p := result.Processes[0]
		if p.PID != 101 || p.Start != 100 || p.PGID != 17 || p.SID != 18 || p.Boot != fixtureBoot || p.Cmd != strings.Repeat("雪", 200) {
			t.Fatal(p)
		}
		if ticks, err := StartTicksAt(root, 101); err != nil || ticks != 100 {
			t.Fatal(ticks, err)
		}
		if result, err := ScanMarkersAt(root, uint32(os.Getuid()+1), "CAIRN_REQUEST_ID", "delivery", 0); err != nil || !result.Clear || len(result.Processes) != 0 {
			t.Fatal("other UID entered scan", result, err)
		}
		if err := os.WriteFile(filepath.Join(root, "101/cmdline"), []byte("\t cmd\x00"+string([]byte{0xff})+"\x00 \n"), 0o600); err != nil {
			t.Fatal(err)
		}
		if result, err := ScanMarkersAt(root, uint32(os.Getuid()), "CAIRN_REQUEST_ID", "delivery", 0); err != nil || result.Processes[0].Cmd != "cmd \ufffd" {
			t.Fatal(result, err)
		}
	})
	t.Run("unreadable_processes_never_imply_clear", func(t *testing.T) {
		for _, tc := range []struct {
			name, state      string
			start            uint64
			badStat, unknown bool
		}{{"recent", "S", 100, false, true}, {"old", "S", 99, false, false}, {"zombie", "Z", 100, false, false}, {"malformed_stat", "S", 100, true, true}} {
			t.Run(tc.name, func(t *testing.T) {
				root := procFixtureRoot(t)
				writeProcessFixture(t, root, processFixture{pid: 201, parent: 1, state: tc.state, start: tc.start})
				path := filepath.Join(root, "201/environ")
				if err := os.Chmod(path, 0); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = os.Chmod(path, 0o600) })
				if tc.badStat {
					if err := os.WriteFile(filepath.Join(root, "201/stat"), []byte("malformed"), 0o600); err != nil {
						t.Fatal(err)
					}
				}
				result, err := ScanMarkersAt(root, uint32(os.Getuid()), "CAIRN_REQUEST_ID", "delivery", 100)
				if err != nil || result.Clear == tc.unknown || (result.Coverage == "unknown") != tc.unknown {
					t.Fatal(result, err)
				}
				want := []int{}
				if tc.unknown {
					want = []int{201}
				}
				if !slices.Equal(result.Unknown, want) {
					t.Fatal(result.Unknown, want)
				}
			})
		}
	})
	t.Run("owned_live_children_only", func(t *testing.T) {
		root := procFixtureRoot(t)
		boot, err := os.ReadFile("/proc/sys/kernel/random/boot_id")
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(root, "sys/kernel/random/boot_id"), boot, 0o600); err != nil {
			t.Fatal(err)
		}
		var marked procpeer.ProcessRef
		for _, carry := range []bool{true, false} {
			wantEnv := "UNRELATED=1"
			if carry {
				wantEnv = "CAIRN_REQUEST_ID=owned-delivery"
			}
			cmd := exec.Command("sleep", "60")
			cmd.Env = []string{wantEnv}
			if err := cmd.Start(); err != nil {
				t.Fatal(err)
			}
			if cmd.Process.Pid <= 1 {
				t.Fatal("invalid owned child PID")
			}
			t.Cleanup(func() {
				if cmd.Process.Pid > 1 {
					_ = cmd.Process.Kill()
					_ = cmd.Wait()
				}
			})
			ref, err := procpeer.ProcessRefOf(cmd.Process.Pid)
			if err != nil {
				t.Fatal(err)
			}
			if carry {
				marked = ref
			}
			directory := filepath.Join(root, strconv.Itoa(ref.PID))
			if err := os.Mkdir(directory, 0o700); err != nil {
				t.Fatal(err)
			}
			// The overlay reads only metadata of these two owned children. It does
			// not enumerate unrelated host agents or read their environments.
			for _, name := range []string{"stat", "cmdline", "environ"} {
				if err := os.Symlink(filepath.Join("/proc", strconv.Itoa(ref.PID), name), filepath.Join(directory, name)); err != nil {
					t.Fatal(err)
				}
			}
			deadline := time.Now().Add(5 * time.Second)
			for {
				environ, envErr := os.ReadFile(filepath.Join(directory, "environ"))
				cmdline, cmdErr := os.ReadFile(filepath.Join(directory, "cmdline"))
				if envErr == nil && cmdErr == nil && slices.ContainsFunc(bytes.Split(environ, []byte{0}), func(item []byte) bool {
					return bytes.Equal(item, []byte(wantEnv))
				}) && len(bytes.TrimSpace(cmdline)) > 0 {
					break
				}
				if time.Now().After(deadline) {
					t.Fatalf("child %d /proc environ/cmdline not ready: env=%q (%v) cmd=%q (%v)", ref.PID, environ, envErr, cmdline, cmdErr)
				}
				time.Sleep(time.Millisecond)
			}
		}
		result, err := ScanMarkersAt(root, uint32(os.Getuid()), "CAIRN_REQUEST_ID", "owned-delivery", 0)
		if err != nil || result.Clear || result.Coverage != "complete" || len(result.Processes) != 1 || result.Processes[0].PID != marked.PID || result.Processes[0].Start != marked.Start || result.Processes[0].Boot != marked.Boot {
			t.Fatal(result, err)
		}
		if result, err := ScanMarkersAt(root, uint32(os.Getuid()), "CAIRN_REQUEST_ID", "absent-delivery", 0); err != nil || !result.Clear || result.Coverage != "complete" {
			t.Fatal(result, err)
		}
	})
	t.Run("cancel_capture_ownership", func(t *testing.T) {
		turn := int64(80)
		calls := map[string]ToolCallCapture{"a": {AttemptID: "attempt", Since: 60}, "b": {AttemptID: "attempt", Since: 70}}
		if since, err := OpenCodeCancelMinSince(100, &turn, calls, "attempt"); err != nil || since != 60 {
			t.Fatal(since, err)
		}
		if since, err := OpenCodeCancelMinSince(50, nil, calls, "attempt"); err != nil || since != 50 {
			t.Fatal(since, err)
		}
		calls["foreign"] = ToolCallCapture{AttemptID: "other", Since: 1}
		_, err := OpenCodeCancelMinSince(100, &turn, calls, "attempt")
		requireCoordinationError(t, err, "INBOX_ATTEMPT_MISMATCH", "tool capture belongs to another attempt")
		if !slices.Equal([]string{OpenCodeCancelReleaseReason(true), OpenCodeCancelReleaseReason(false)}, []string{"cancel_confirmed", "exclusivity_revoked"}) {
			t.Fatal("cancel release reason changed")
		}
	})
}
