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
