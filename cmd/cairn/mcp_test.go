package main

import "testing"

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
		{[]string{"--codex-thread", "--tool-profile", "compact/2"}, false},
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
	f := flags("mcp")
	o := mcpFlags(f)
	if err := f.Parse([]string{"--socket", "/api.sock", "--token-file", "/agent.token", "--repo", "repo", "--codex-thread", "--tool-profile", "compact/1"}); err != nil {
		t.Fatal(err)
	}
	if err := o.validate(f.NArg()); err != nil {
		t.Fatal(err)
	}
	command, err := o.command("/cairn")
	if err != nil {
		t.Fatal(err)
	}
	if command[len(command)-2] != "--tool-profile" || command[len(command)-1] != "compact/1" {
		t.Fatalf("generated MCP command lost opt-in profile: %q", command)
	}
}
