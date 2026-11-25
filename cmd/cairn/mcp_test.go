package main

import (
	"os"
	"path/filepath"
	"strings"
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
		{[]string{"--codex-thread", "--max-tokens", "15000"}, false},
		{[]string{"--codex-thread", "--tokens", "32000", "--max-tokens", "31000"}, false},
		{[]string{"--codex-thread", "--max-tokens", "1000001"}, false},
		{[]string{"--codex-thread", "--output-max-bytes", "0"}, true},
		{[]string{"--codex-thread", "--output-max-bytes", "8000"}, true},
		{[]string{"--codex-thread", "--output-max-bytes", "-1"}, false},
		{[]string{"--codex-thread", "--output-max-bytes", "1000001"}, false},
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
	for _, tc := range []struct {
		extra []string
		want  string
	}{
		{nil, "compact/1"},
		{[]string{"--search-view", "compact/1"}, "compact/1"},
		{[]string{"--search-view", "full"}, "full"},
	} {
		f := flags("mcp")
		o := mcpFlags(f)
		args := append([]string{"--socket", "/api.sock", "--token-file", "/agent.token", "--repo", "repo", "--codex-thread"}, tc.extra...)
		if err := f.Parse(args); err != nil {
			t.Fatal(err)
		}
		if err := o.validate(f.NArg()); err != nil {
			t.Fatal(err)
		}
		command, err := o.command("/cairn")
		if err != nil {
			t.Fatal(err)
		}
		if command[len(command)-4] != "--search-view" || command[len(command)-3] != tc.want {
			t.Fatalf("generated MCP command lost search view %q: %q", tc.want, command)
		}
	}
}

func TestCompactToolProfilePropagatesToMCPCommand(t *testing.T) {
	for _, tc := range []struct {
		extra []string
		want  string
	}{
		{nil, "compact/2"},
		{[]string{"--tool-profile", "compact/1"}, "compact/1"},
		{[]string{"--tool-profile", "compact/2"}, "compact/2"},
		{[]string{"--tool-profile", "full"}, "full"},
	} {
		f := flags("mcp")
		o := mcpFlags(f)
		args := append([]string{"--socket", "/api.sock", "--token-file", "/agent.token", "--repo", "repo", "--codex-thread"}, tc.extra...)
		if err := f.Parse(args); err != nil {
			t.Fatal(err)
		}
		if err := o.validate(f.NArg()); err != nil {
			t.Fatal(err)
		}
		command, err := o.command("/cairn")
		if err != nil {
			t.Fatal(err)
		}
		if command[len(command)-2] != "--tool-profile" || command[len(command)-1] != tc.want {
			t.Fatalf("generated MCP command lost profile %q: %q", tc.want, command)
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

func TestOutputMaxBytesAndTokensDefaultsInMCPCommand(t *testing.T) {
	f := flags("mcp")
	o := mcpFlags(f)
	if err := f.Parse([]string{"--socket", "/api.sock", "--token-file", "/agent.token", "--repo", "repo", "--codex-thread"}); err != nil {
		t.Fatal(err)
	}
	if err := o.validate(f.NArg()); err != nil {
		t.Fatal(err)
	}
	if o.config.AvailableTokens != 16000 || o.config.MaxTokens != 160000 || o.config.OutputMaxBytes != 12000 || o.config.UnboundedOutput {
		t.Fatalf("unexpected defaults: %+v", o.config)
	}
	command, err := o.command("/cairn")
	if err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(command, " ")
	if !strings.Contains(joined, "--tokens 16000") || strings.Contains(joined, "--max-tokens") || strings.Contains(joined, "--output-max-bytes") {
		t.Fatalf("default command unexpectedly emitted overrides: %q", command)
	}

	for _, tc := range []struct {
		flagVal string
		wantArg string
	}{{"0", "0"}, {"8000", "8000"}} {
		f2 := flags("mcp")
		o2 := mcpFlags(f2)
		if err := f2.Parse([]string{"--socket", "/api.sock", "--token-file", "/agent.token", "--repo", "repo", "--codex-thread", "--output-max-bytes", tc.flagVal}); err != nil {
			t.Fatal(err)
		}
		if err := o2.validate(f2.NArg()); err != nil {
			t.Fatal(err)
		}
		cmd2, err := o2.command("/cairn")
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(strings.Join(cmd2, " "), "--output-max-bytes "+tc.wantArg) {
			t.Fatalf("expected --output-max-bytes %s in %q", tc.wantArg, cmd2)
		}
	}
}
