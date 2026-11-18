package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestMCPScopeOptions(t *testing.T) {
	for _, tc := range []struct {
		args  []string
		valid bool
	}{
		{[]string{"--task", "task", "--run", "run"}, true},
		{[]string{"--codex-thread"}, true},
		{nil, false},
		{[]string{"--codex-thread", "--task", "task"}, false},
		{[]string{"--codex-thread", "--run", "run"}, false},
		{[]string{"--codex-thread", "--repo", "*"}, false},
		{[]string{"--codex-thread", "--tokens", "255"}, false},
		{[]string{"--codex-thread", "--max-tokens", "160000"}, true},
		{[]string{"--codex-thread", "--max-tokens", "31000"}, false},
		{[]string{"--codex-thread", "--max-tokens", "1000001"}, false},
		{[]string{"--codex-thread", "--search-view", "compact/1"}, true},
		{[]string{"--codex-thread", "--search-view", "compact/2"}, false},
		{[]string{"--codex-thread", "--tool-profile", "compact/1"}, true},
		{[]string{"--codex-thread", "--tool-profile", "compact/2"}, true},
		{[]string{"--codex-thread", "--tool-profile", "compact/3"}, false},
		{[]string{"--codex-thread", "--messaging"}, true},
		{[]string{"--task", "task", "--run", "run", "--messaging", "--native-session", "native"}, true},
		{[]string{"--task", "task", "--run", "run", "--messaging", "--coordination-state-root", "/state"}, true},
		{[]string{"--task", "task", "--run", "run", "--native-session", "native"}, false},
		{[]string{"--task", "task", "--run", "run", "--coordination-state-root", "/state"}, false},
		{[]string{"--codex-thread", "--messaging", "--native-session", "native"}, false},
		{[]string{"--codex-thread", "--messaging", "--coordination-state-root", "/state"}, false},
	} {
		f := flags("mcp")
		o := mcpFlags(f)
		args := append([]string{"--socket", "/api.sock", "--token-file", "/agent.token", "--repo", "repo"}, tc.args...)
		if err := f.Parse(args); err != nil {
			t.Fatal(err)
		}
		if err := o.validate(f.NArg()); (err == nil) != tc.valid {
			t.Fatalf("%v: %v", tc.args, err)
		}
	}
}

func TestCompactSearchViewPropagatesToMCPCommand(t *testing.T) {
	f := flags("mcp")
	o := mcpFlags(f)
	if err := f.Parse([]string{"--socket", "/api.sock", "--token-file", "/agent.token", "--repo", "repo", "--codex-thread", "--search-view", "compact/1"}); err != nil {
		t.Fatal(err)
	}
	if err := o.validate(f.NArg()); err != nil {
		t.Fatal(err)
	}
	command, err := o.command("/cairn")
	if err != nil {
		t.Fatal(err)
	}
	if command[len(command)-2] != "--search-view" || command[len(command)-1] != "compact/1" {
		t.Fatalf("generated MCP command lost opt-in view: %q", command)
	}
}

func TestCompactToolProfilePropagatesToMCPCommand(t *testing.T) {
	for _, profile := range []string{"compact/1", "compact/2"} {
		f := flags("mcp")
		o := mcpFlags(f)
		if err := f.Parse([]string{"--socket", "/api.sock", "--token-file", "/agent.token", "--repo", "repo", "--codex-thread", "--tool-profile", profile}); err != nil {
			t.Fatal(err)
		}
		if err := o.validate(f.NArg()); err != nil {
			t.Fatal(err)
		}
		command, err := o.command("/cairn")
		if err != nil {
			t.Fatal(err)
		}
		if command[len(command)-2] != "--tool-profile" || command[len(command)-1] != profile {
			t.Fatalf("generated MCP command lost opt-in profile %q: %q", profile, command)
		}
	}
}

func TestMessagingPropagatesToMCPCommand(t *testing.T) {
	// The generating shell's own conversation must never leak into configuration.
	t.Setenv("CLAUDE_CODE_SESSION_ID", "generating-shell")
	for _, tc := range []struct {
		args []string
		want []string
	}{
		{[]string{"--codex-thread"}, nil},
		{[]string{"--codex-thread", "--messaging"}, []string{"--messaging"}},
		{[]string{"--task", "task", "--run", "run", "--messaging"}, []string{"--messaging"}},
		{[]string{"--task", "task", "--run", "run", "--messaging", "--native-session", "native"}, []string{"--messaging", "--native-session", "native"}},
	} {
		f := flags("mcp")
		o := mcpFlags(f)
		if err := f.Parse(append([]string{"--socket", "/api.sock", "--token-file", "/agent.token", "--repo", "repo"}, tc.args...)); err != nil {
			t.Fatal(err)
		}
		if err := o.validate(f.NArg()); err != nil {
			t.Fatal(err)
		}
		command, err := o.command("/cairn")
		if err != nil {
			t.Fatal(err)
		}
		var got []string
		for i, arg := range command {
			if arg == "--messaging" {
				got = command[i:]
			}
		}
		if len(got) != len(tc.want) {
			t.Fatalf("%v generated %q", tc.args, command)
		}
		for i := range got {
			if got[i] != tc.want[i] {
				t.Fatalf("%v generated %q", tc.args, command)
			}
		}
	}
}

func TestDefaultCoordinationStateRoot(t *testing.T) {
	home := t.TempDir()
	t.Setenv("CAIRN_HOME", "")
	t.Setenv("XDG_DATA_HOME", filepath.Join(home, "xdg"))
	t.Setenv("HOME", home)
	if got := defaultCoordinationStateRoot(); got != "" {
		t.Fatalf("missing state root returned %q", got)
	}
	xdgState := filepath.Join(home, "xdg", "cairn", "coordination", "state")
	if err := os.MkdirAll(xdgState, 0700); err != nil {
		t.Fatal(err)
	}
	if got := defaultCoordinationStateRoot(); got != xdgState {
		t.Fatalf("got %q, want %q", got, xdgState)
	}
	cairnHome := filepath.Join(home, "custom-cairn")
	customState := filepath.Join(cairnHome, "coordination", "state")
	if err := os.MkdirAll(customState, 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("CAIRN_HOME", cairnHome)
	if got := defaultCoordinationStateRoot(); got != customState {
		t.Fatalf("CAIRN_HOME override: got %q, want %q", got, customState)
	}
}
