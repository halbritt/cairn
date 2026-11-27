package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/transcript"
)

func TestSwarmWindowClaudeTailAndCache(t *testing.T) {
	root := t.TempDir()
	bindings, stateDir, proc := makeSwarmHerdrDirectories(t, root)
	home := filepath.Join(root, "claude")
	writeSwarmHerdrJSON(t, bindings, "claude", map[string]any{"harness": "claude", "config_home": home, "state_dir": stateDir})
	native := "f927312d-7aea-48c0-b691-2626839e5714"
	fixtureSwarmWindowSession(t, stateDir, proc, "claude", "agent-claude", native, root)
	path, err := transcript.ClaudeTranscriptPath(transcript.ClaudeConfig{ConfigHome: home},
		transcript.SessionState{NativeSessionID: native, Workspace: root})
	if err != nil {
		t.Fatal(err)
	}
	const marker = "PRIVATE_TRANSCRIPT_MARKER"
	compacted := `{"type":"system","subtype":"compact_boundary","timestamp":"2026-11-27T19:00:01Z"}`
	usage := fmt.Sprintf(`{"type":"assistant","timestamp":"2026-11-27T19:00:02Z","message":{"content":"%s%s","model":"claude-fable-5-2","usage":{"input_tokens":20000,"cache_read_input_tokens":870000,"cache_creation_input_tokens":10000}}}`, marker, strings.Repeat("x", swarmWindowBlock))
	truncated := `{"type":"assistant","message":{"content":"` + marker
	writeSwarmWindowFixture(t, path, strings.Join([]string{compacted, usage, truncated}, "\n"))
	opens := 0
	read := transcriptWindowReader(bindings, proc, swarmTranscriptAccess{
		open: func(root *os.Root, name string) (*os.File, error) {
			opens++
			return openSwarmTranscript(root, name)
		},
		stat: (*os.File).Stat,
	})

	observed := read(t.Context())
	got := observed.Windows["agent-claude"]
	if observed.Status != "ok" || observed.Sessions != 1 || len(observed.Unknown) != 0 ||
		got.UsedTokens != 900_000 || got.WindowTokens != 1_000_000 || !got.NearLimit ||
		got.Source != "claude transcript" || got.Model != "claude-fable-5-2" ||
		got.Compactions != 1 || got.LastCompactionAt == nil || got.LastCompactionAt.Second() != 1 || got.MeasuredAt.Second() != 2 {
		t.Fatalf("unexpected observation: %+v", observed)
	}
	data, err := json.Marshal(observed)
	if err != nil || strings.Contains(string(data), marker) {
		t.Fatal("observation leaked transcript text or failed to marshal")
	}
	warm := read(t.Context())
	if opens != 1 || warm.Windows["agent-claude"].UsedTokens != got.UsedTokens {
		t.Fatalf("warm read opens = %d, want 1; observation = %+v", opens, warm)
	}

	tail := `{"type":"assistant","timestamp":"2026-11-27T19:00:03Z","message":{"model":"new-unknown-model","usage":{"input_tokens":31}}}`
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_APPEND, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.WriteString("\n" + tail + "\n"); err != nil {
		f.Close()
		t.Fatal(err)
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}
	changed := read(t.Context()).Windows["agent-claude"]
	if opens != 2 || changed.UsedTokens != 31 || changed.WindowTokens != 0 || changed.NearLimit || changed.Model != "new-unknown-model" {
		t.Fatalf("changed read opens = %d and window = %+v", opens, changed)
	}
}

func TestSwarmWindowCodexAndUnknownHarness(t *testing.T) {
	root := t.TempDir()
	bindings, stateDir, proc := makeSwarmHerdrDirectories(t, root)
	home := filepath.Join(root, "codex")
	writeSwarmHerdrJSON(t, bindings, "codex", map[string]any{"harness": "codex", "config_home": home, "state_dir": stateDir})
	native := "33652c68-2f28-41b4-9335-3eb1bb311a91"
	ownerPID := fixtureSwarmWindowSession(t, stateDir, proc, "codex", "agent-codex", native, root)
	fixtureSwarmWindowSession(t, stateDir, proc, "hermes", "agent-hermes", "fixture-hermes", root)
	path := filepath.Join(home, "sessions", "2026", "11", "27", "rollout-fixture-"+native+".jsonl")
	writeSwarmWindowFixture(t, path, `{"type":"compacted","timestamp":"2026-11-27T19:00:00Z","payload":{"message":"PRIVATE_TRANSCRIPT_MARKER"}}
{"type":"turn_context","timestamp":"2026-11-27T19:00:01Z","payload":{"model":"gpt-test"}}
{"type":"event_msg","timestamp":"2026-11-27T19:00:02Z","payload":{"type":"token_count","info":{"model_context_window":1000,"last_token_usage":{"input_tokens":910,"cached_input_tokens":15,"output_tokens":10,"total_tokens":920},"total_token_usage":{"input_tokens":99999}}}}
{"type":"turn_context","timestamp":"2026-11-27T19:00:03Z","payload":{"model":"later-model"}}
`)
	fd := filepath.Join(proc, strconv.Itoa(ownerPID), "fd")
	if err := os.MkdirAll(fd, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(path, filepath.Join(fd, "5")); err != nil {
		t.Fatal(err)
	}
	observation := newTranscriptWindowReader(bindings, proc)(t.Context())
	window := observation.Windows["agent-codex"]
	if observation.Status != "ok" || observation.Sessions != 2 || len(observation.Windows) != 1 ||
		observation.Unknown["agent-hermes"] == "" || window.UsedTokens != 910 || window.WindowTokens != 1000 ||
		window.Source != "codex rollout" || window.Model != "gpt-test" || !window.NearLimit ||
		window.Compactions != 1 || window.LastCompactionAt == nil || window.MeasuredAt.Second() != 2 {
		t.Fatalf("unexpected observation: %+v", observation)
	}
	data, err := json.Marshal(observation)
	if err != nil || strings.Contains(string(data), "PRIVATE_TRANSCRIPT_MARKER") {
		t.Fatal("observation leaked transcript text or failed to marshal")
	}
}

func TestSwarmWindowRefusesReplacedCodexRollout(t *testing.T) {
	root := t.TempDir()
	bindings, stateDir, proc := makeSwarmHerdrDirectories(t, root)
	home := filepath.Join(root, "codex")
	writeSwarmHerdrJSON(t, bindings, "codex", map[string]any{"harness": "codex", "config_home": home, "state_dir": stateDir})
	native := "33652c68-2f28-41b4-9335-3eb1bb311a91"
	pid := fixtureSwarmWindowSession(t, stateDir, proc, "codex", "agent-codex", native, root)
	path := filepath.Join(home, "sessions", "rollout-fixture-"+native+".jsonl")
	const usage = `{"type":"event_msg","timestamp":"2026-11-27T19:00:02Z","payload":{"type":"token_count","info":{"model_context_window":1000,"last_token_usage":{"input_tokens":%d}}}}`
	writeSwarmWindowFixture(t, path, fmt.Sprintf(usage, 100))
	held := filepath.Join(root, "held-inode")
	if err := os.Link(path, held); err != nil {
		t.Fatal(err)
	}
	fd := filepath.Join(proc, strconv.Itoa(pid), "fd", "5")
	if err := os.MkdirAll(filepath.Dir(fd), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(path, fd); err != nil {
		t.Fatal(err)
	}
	scan, err := swarmHerdrSessions(t.Context(), bindings, proc)
	if err != nil || len(scan.sessions) != 1 {
		t.Fatalf("scan = %+v, error = %v", scan, err)
	}
	resolver := transcript.NewReader(transcript.NewCoordinationProfile("/", os.Getenv, os.LookupEnv))
	resolver.ProcRoot, resolver.Alive = proc, scan.client.ProcessAlive
	file, reason := findSwarmWindowFile(resolver, scan.sessions[0])
	if reason != "" {
		t.Fatalf("rollout resolution failed: %s", reason)
	}

	replacement := filepath.Join(root, "replacement")
	writeSwarmWindowFixture(t, replacement, fmt.Sprintf(usage, 900))
	if err := os.Rename(replacement, path); err != nil {
		t.Fatal(err)
	}
	// A real /proc fd still names the original inode after its path is replaced.
	if err := os.Remove(fd); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(held, fd); err != nil {
		t.Fatal(err)
	}
	opens := 0
	got := file.measure(t.Context(), swarmTranscriptAccess{
		open: func(root *os.Root, name string) (*os.File, error) {
			opens++
			return openSwarmTranscript(root, name)
		},
		stat: (*os.File).Stat,
	}, nil, make(map[swarmTranscriptStamp]cachedSwarmWindow))
	if got.reason != "Codex process no longer holds the session rollout" || !got.window.MeasuredAt.IsZero() || opens != 0 {
		t.Fatalf("replacement rollout was usable: %+v, opens = %d", got, opens)
	}
}

func TestSwarmWindowRefusesEscapedTranscript(t *testing.T) {
	root := t.TempDir()
	bindings, stateDir, proc := makeSwarmHerdrDirectories(t, root)
	home := filepath.Join(root, "claude")
	if err := os.Mkdir(home, 0700); err != nil {
		t.Fatal(err)
	}
	writeSwarmHerdrJSON(t, bindings, "claude", map[string]any{"harness": "claude", "config_home": home, "state_dir": stateDir})
	native := "f927312d-7aea-48c0-b691-2626839e5714"
	fixtureSwarmWindowSession(t, stateDir, proc, "claude", "agent-claude", native, root)
	outside := filepath.Join(home, "outside")
	slug, err := transcript.ClaudeProjectSlug(root)
	if err != nil {
		t.Fatal(err)
	}
	writeSwarmWindowFixture(t, filepath.Join(outside, slug, native+".jsonl"), `{"type":"assistant","timestamp":"2026-11-27T19:00:02Z","message":{"model":"model","usage":{"input_tokens":900}}}`)
	projects := filepath.Join(home, "projects")
	if err := os.Mkdir(projects, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join("..", "outside", slug), filepath.Join(projects, slug)); err != nil {
		t.Fatal(err)
	}
	opens := 0
	read := transcriptWindowReader(bindings, proc, swarmTranscriptAccess{
		open: func(root *os.Root, name string) (*os.File, error) {
			opens++
			return openSwarmTranscript(root, name)
		},
		stat: (*os.File).Stat,
	})
	observation := read(t.Context())
	if observation.Status != "ok" || observation.Unknown["agent-claude"] == "" || len(observation.Windows) != 0 || opens != 0 {
		t.Fatalf("out-of-root transcript was usable: %+v, opens=%d", observation, opens)
	}
}

func TestSwarmWindowRefusesClaudeTranscriptSymlink(t *testing.T) {
	bindings, proc, path := fixtureClaudeWindowFile(t)
	target := filepath.Join(t.TempDir(), "outside-transcript")
	writeSwarmWindowFixture(t, target, fixtureClaudeWindowUsage(321))
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, path); err != nil {
		t.Fatal(err)
	}
	got := newTranscriptWindowReader(bindings, proc)(t.Context())
	if got.Status != "ok" || len(got.Windows) != 0 || got.Unknown["agent-claude"] != "Session transcript is missing or is not a regular file" {
		t.Fatalf("symlink transcript was usable: %+v", got)
	}
}

type swarmFileInfoWithOwner struct {
	os.FileInfo
	stat syscall.Stat_t
}

func (f swarmFileInfoWithOwner) Sys() any { return &f.stat }

func TestSwarmWindowChecksClaudeOwnerOnOpenedDescriptor(t *testing.T) {
	bindings, proc, path := fixtureClaudeWindowFile(t)
	writeSwarmWindowFixture(t, path, fixtureClaudeWindowUsage(321))
	stats := 0
	read := transcriptWindowReader(bindings, proc, swarmTranscriptAccess{
		open: openSwarmTranscript,
		stat: func(file *os.File) (os.FileInfo, error) {
			stats++
			info, err := file.Stat()
			if err != nil {
				return nil, err
			}
			altered := *info.Sys().(*syscall.Stat_t)
			altered.Uid++
			return swarmFileInfoWithOwner{info, altered}, nil
		},
	})
	got := read(t.Context())
	if got.Status != "ok" || len(got.Windows) != 0 || got.Unknown["agent-claude"] != "Session transcript is not owned by this user" || stats != 1 {
		t.Fatalf("foreign-owned transcript was usable: %+v, stats = %d", got, stats)
	}
}

func TestSwarmWindowCacheMissOnInodeReplacement(t *testing.T) {
	bindings, proc, path := fixtureClaudeWindowFile(t)
	writeSwarmWindowFixture(t, path, fixtureClaudeWindowUsage(111))
	read := newTranscriptWindowReader(bindings, proc)
	first := read(t.Context())
	if first.Windows["agent-claude"].UsedTokens != 111 {
		t.Fatalf("initial reading: %+v", first)
	}
	before, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	replacement := filepath.Join(filepath.Dir(path), "replacement")
	writeSwarmWindowFixture(t, replacement, fixtureClaudeWindowUsage(222))
	if err := os.Chtimes(replacement, before.ModTime(), before.ModTime()); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(replacement, path); err != nil {
		t.Fatal(err)
	}
	after, err := os.Stat(path)
	if err != nil || before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) || os.SameFile(before, after) {
		t.Fatalf("invalid replacement fixture: before = %v, after = %v, error = %v", before, after, err)
	}
	second := read(t.Context())
	if second.Status != "ok" || second.Windows["agent-claude"].UsedTokens != 222 {
		t.Fatalf("cache served the original inode: %+v", second)
	}
}

func TestSwarmWindowTailReadLimit(t *testing.T) {
	ctx := context.Background()
	line := `{"type":"assistant","timestamp":"2026-11-27T19:00:02Z","message":{"model":"model","usage":{"input_tokens":61}}}` + "\n"
	padding := strings.Repeat("{\"content\":\""+strings.Repeat("x", swarmWindowBlock-1)+"\"}\n", swarmWindowMaxTail/swarmWindowBlock+1)
	parsed, err := readSwarmWindowTail(ctx, strings.NewReader(line+padding), int64(len(line)+len(padding)), "claude")
	if err != nil || !parsed.MeasuredAt.Equal(time.Time{}) {
		t.Fatalf("read beyond tail limit: %+v, %v", parsed, err)
	}
}

func fixtureSwarmWindowSession(t *testing.T, stateDir, proc, harness, agent, native, workspace string) int {
	t.Helper()
	pid := 110
	if harness == "hermes" {
		pid++
	}
	process := writeSwarmPaneProcess(t, proc, pid, "")
	writeSwarmHerdrJSON(t, stateDir, agent, map[string]any{
		"workspace": workspace, "process": process,
		"agent": map[string]any{"agent_id": agent, "native_session_id": native, "metadata": map[string]string{"harness": harness}},
	})
	return pid
}

func fixtureClaudeWindowFile(t *testing.T) (bindings, proc, path string) {
	t.Helper()
	root := t.TempDir()
	bindings, stateDir, proc := makeSwarmHerdrDirectories(t, root)
	home := filepath.Join(root, "claude")
	writeSwarmHerdrJSON(t, bindings, "claude", map[string]any{"harness": "claude", "config_home": home, "state_dir": stateDir})
	native := "f927312d-7aea-48c0-b691-2626839e5714"
	fixtureSwarmWindowSession(t, stateDir, proc, "claude", "agent-claude", native, root)
	path, err := transcript.ClaudeTranscriptPath(transcript.ClaudeConfig{ConfigHome: home},
		transcript.SessionState{NativeSessionID: native, Workspace: root})
	if err != nil {
		t.Fatal(err)
	}
	return bindings, proc, path
}

func fixtureClaudeWindowUsage(input int) string {
	return fmt.Sprintf(`{"type":"assistant","timestamp":"2026-11-27T19:00:02Z","message":{"model":"model","usage":{"input_tokens":%d}}}`, input)
}

func writeSwarmWindowFixture(t *testing.T, path, contents string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(contents), 0600); err != nil {
		t.Fatal(err)
	}
}
