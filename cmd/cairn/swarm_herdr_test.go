package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/wake"
)

func TestSwarmHerdrReader(t *testing.T) {
	root := t.TempDir()
	t.Setenv("CAIRN_HOME", root)
	bindings, stateDir, procRoot := makeSwarmHerdrDirectories(t, root)
	log := filepath.Join(root, "herdr-args")
	t.Setenv("SWARM_TEST_HERDR_LOG", log)
	executable := writeSwarmHerdrExecutable(t, root, `
printf '%s\n' "$*" >> "$SWARM_TEST_HERDR_LOG"
[ "$#" -eq 2 ] && [ "$1" = agent ] && [ "$2" = list ] || exit 99
[ "$HERDR_ENV" = 1 ] && [ "$HERDR_SOCKET_PATH" = /fixture/herdr.sock ] || exit 98
printf '%s\n' '{"result":{"agents":[{"pane_id":"w1:p2","tab_id":"w1:t3","workspace_id":"w1","agent":"claude","agent_session":{"kind":"id","agent":"claude","value":"native"},"agent_status":"working","focused":true,"terminal_title":"raw","terminal_title_stripped":"Ready"}]}}'
`)
	writeSwarmHerdrJSON(t, bindings, "test", map[string]any{"binding": "test", "harness": "claude", "state_dir": stateDir, "idle_wakeup": executable})

	withoutSocket := writeSwarmPaneProcess(t, procRoot, 100, "")
	active := writeSwarmPaneProcess(t, procRoot, 101, "/fixture/herdr.sock")
	writeSwarmPaneState(t, stateDir, "a-no-socket", "no-socket", "other-native", withoutSocket, nil)
	writeSwarmPaneState(t, stateDir, "active", "active-agent", "native", active, map[string]any{"retired": 0})
	writeSwarmPaneState(t, stateDir, "retired", "retired-agent", "native", active, map[string]any{"retired": true})
	writeSwarmPaneState(t, stateDir, "ending", "ending-agent", "native", active, map[string]any{"ending": true})
	writeSwarmPaneState(t, stateDir, "dead", "dead-agent", "native", wake.ProcessRef{PID: 200, Boot: active.Boot, Start: active.Start}, nil)
	reused := active
	reused.Start--
	writeSwarmPaneState(t, stateDir, "reused", "reused-agent", "native", reused, nil)

	got := newHerdrPaneReader("", procRoot)(t.Context())
	want := swarmPane{
		PaneID: "w1:p2", TabID: "w1:t3", WorkspaceID: "w1", AgentStatus: "working",
		TerminalTitle: "Ready", Focused: true, FocusCommand: "herdr agent focus w1:p2",
	}
	if got.Status != "ok" || got.Error != "" || got.Sessions != 2 || len(got.Panes) != 1 || got.Panes["active-agent"] != want {
		t.Fatalf("observation = %+v, want one pane %+v out of two live sessions", got, want)
	}
	args, err := os.ReadFile(log)
	if err != nil || string(args) != "agent list\n" {
		t.Fatalf("Herdr args = %q, error = %v", args, err)
	}
}

func TestSwarmHerdrUnavailable(t *testing.T) {
	for _, scenario := range []struct {
		name, idleWakeup, socket, message string
	}{
		{"missing_socket", "/not-a-fixture-herdr", "", "no live session has a Herdr socket"},
		{"relative_socket", "/not-a-fixture-herdr", "relative.sock", "no live session has a Herdr socket"},
		{"no_absolute_executable", "herdr", "/fixture/herdr.sock", "no Cairn binding has an absolute Herdr executable"},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			bindings, stateDir, procRoot := makeSwarmHerdrDirectories(t, t.TempDir())
			writeSwarmHerdrJSON(t, bindings, "test", map[string]any{"harness": "claude", "state_dir": stateDir, "idle_wakeup": scenario.idleWakeup})
			process := writeSwarmPaneProcess(t, procRoot, 100, scenario.socket)
			writeSwarmPaneState(t, stateDir, "native", "agent", "native", process, nil)
			t.Setenv("HERDR_ENV", "1")
			t.Setenv("HERDR_SOCKET_PATH", "/ignored/server.sock")
			got := newHerdrPaneReader(bindings, procRoot)(t.Context())
			if got.Status != "unavailable" || got.Error != scenario.message || got.Sessions != 1 || len(got.Panes) != 0 {
				t.Fatalf("observation = %+v, want unavailable with %q and one session", got, scenario.message)
			}
		})
	}
}

func TestSwarmHerdrSkipsMalformedState(t *testing.T) {
	root := t.TempDir()
	bindings, stateDir, procRoot := makeSwarmHerdrDirectories(t, root)
	executable := writeSwarmHerdrExecutable(t, root, `
[ "$#" -eq 2 ] && [ "$1" = agent ] && [ "$2" = list ] || exit 99
cat <<'JSON'
{"result":{"agents":[
  {"pane_id":"w1:p2","agent":"claude","agent_session":{"kind":"id","agent":"claude","value":"native-one"},"agent_status":"working"},
  {"pane_id":"w1:p3","agent":"claude","agent_session":{"kind":"id","agent":"claude","value":"native-two"},"agent_status":"idle"}
]}}
JSON
`)
	writeSwarmHerdrJSON(t, bindings, "test", map[string]any{"harness": "claude", "state_dir": stateDir, "idle_wakeup": executable})
	first := writeSwarmPaneProcess(t, procRoot, 100, "/fixture/herdr.sock")
	second := writeSwarmPaneProcess(t, procRoot, 101, "/fixture/herdr.sock")
	writeSwarmPaneState(t, stateDir, "a-valid", "agent-one", "native-one", first, nil)
	if err := os.WriteFile(filepath.Join(stateDir, "b-truncated.json"), []byte(`{"agent":`), 0600); err != nil {
		t.Fatal(err)
	}
	writeSwarmPaneState(t, stateDir, "c-valid", "agent-two", "native-two", second, nil)

	got := newHerdrPaneReader(bindings, procRoot)(t.Context())
	if got.Status != "ok" || got.Error != "" || got.Sessions != 2 || got.SkippedStateFiles != 1 ||
		len(got.Panes) != 2 || got.Panes["agent-one"].PaneID != "w1:p2" || got.Panes["agent-two"].PaneID != "w1:p3" {
		t.Fatalf("observation = %+v, want both live panes and one skipped state file", got)
	}
}

func TestSwarmHerdrDeadline(t *testing.T) {
	root := t.TempDir()
	bindings, stateDir, procRoot := makeSwarmHerdrDirectories(t, root)
	log := filepath.Join(root, "herdr-started")
	t.Setenv("SWARM_TEST_HERDR_LOG", log)
	executable := writeSwarmHerdrExecutable(t, root, `
[ "$#" -eq 2 ] && [ "$1" = agent ] && [ "$2" = list ] || exit 99
printf '%s\n' "$*" > "$SWARM_TEST_HERDR_LOG"
exec sleep 5
`)
	writeSwarmHerdrJSON(t, bindings, "test", map[string]any{"harness": "claude", "state_dir": stateDir, "idle_wakeup": executable})
	process := writeSwarmPaneProcess(t, procRoot, 110, "/fixture/herdr.sock")
	writeSwarmPaneState(t, stateDir, "active", "agent", "native", process, nil)
	ctx, cancel := context.WithTimeout(t.Context(), 150*time.Millisecond)
	defer cancel()
	started := time.Now()
	got := newHerdrPaneReader(bindings, procRoot)(ctx)
	elapsed := time.Since(started)
	if got.Status != "unavailable" || got.Error != context.DeadlineExceeded.Error() || len(got.Panes) != 0 {
		t.Fatalf("observation = %+v, want deadline failure", got)
	}
	if _, err := os.Stat(log); err != nil {
		t.Fatalf("deadline test didn't reach the Herdr fixture: %v", err)
	}
	// Leave scheduling headroom on the shared host while requiring the caller's
	// deadline to cut the read short well before Herdr's four-second deadline.
	if elapsed > 450*time.Millisecond {
		t.Fatalf("reader took %v with a 150ms context deadline", elapsed)
	}
}

func makeSwarmHerdrDirectories(t *testing.T, root string) (bindings, state, proc string) {
	t.Helper()
	bindings = filepath.Join(root, "coordination", "bindings")
	state = filepath.Join(root, "state")
	proc = filepath.Join(root, "proc")
	for _, dir := range []string{bindings, state, filepath.Join(proc, "sys", "kernel", "random")} {
		if err := os.MkdirAll(dir, 0700); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(proc, "sys", "kernel", "random", "boot_id"), []byte("fixture-boot\n"), 0600); err != nil {
		t.Fatal(err)
	}
	return bindings, state, proc
}

func writeSwarmPaneProcess(t *testing.T, root string, pid int, socket string) wake.ProcessRef {
	t.Helper()
	process := wake.ProcessRef{PID: pid, Boot: "fixture-boot", Start: 1000}
	dir := filepath.Join(root, fmt.Sprint(pid))
	if err := os.MkdirAll(dir, 0700); err != nil {
		t.Fatal(err)
	}
	stat := fmt.Sprintf("%d (test process) S %s%d\n", pid, strings.Repeat("0 ", 18), process.Start)
	if err := os.WriteFile(filepath.Join(dir, "stat"), []byte(stat), 0600); err != nil {
		t.Fatal(err)
	}
	env := "HERDR_ENV=1\x00HERDR_SOCKET_PATH=" + socket + "\x00"
	if err := os.WriteFile(filepath.Join(dir, "environ"), []byte(env), 0600); err != nil {
		t.Fatal(err)
	}
	return process
}

func writeSwarmPaneState(t *testing.T, directory, name, agent, native string, process wake.ProcessRef, flags map[string]any) {
	t.Helper()
	state := map[string]any{
		"process": process,
		"agent": map[string]any{
			"agent_id": agent, "native_session_id": native, "metadata": map[string]string{"harness": "claude"},
		},
	}
	for key, value := range flags {
		state[key] = value
	}
	writeSwarmHerdrJSON(t, directory, name, state)
}

func writeSwarmHerdrJSON(t *testing.T, directory, name string, value any) {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, name+".json"), data, 0600); err != nil {
		t.Fatal(err)
	}
}

func writeSwarmHerdrExecutable(t *testing.T, directory, content string) string {
	t.Helper()
	executable := filepath.Join(directory, "herdr-fixture")
	if err := os.WriteFile(executable, []byte("#!/bin/sh\nset -eu\n"+content), 0700); err != nil {
		t.Fatal(err)
	}
	return executable
}
