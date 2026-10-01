package main

import (
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
)

func TestMCPClientHarnessIsAnExplicitReportedLabelNeverInferred(t *testing.T) {
	parse := func(args ...string) (*mcpOptions, error) {
		f := flags("mcp")
		o := mcpFlags(f)
		if err := f.Parse(args); err != nil {
			return nil, err
		}
		return o, o.validate(f.NArg())
	}
	base := []string{"--socket", "/s", "--token-file", "/t", "--repo", "/r", "--task", "t", "--run", "r"}
	for _, harness := range []string{"codex", "claude", "opencode", "hermes", "agy", "other"} {
		o, err := parse(append(base, "--client-harness", harness)...)
		if err != nil {
			t.Fatalf("%s: %v", harness, err)
		}
		if d := o.declaration(); d.Surface != "mcp" || d.Harness != harness || d.RetrievalCapabilities == nil {
			t.Fatalf("%+v", d)
		}
	}
	// Codex thread grouping or an installer-style command line does not name a harness.
	for _, args := range [][]string{base, []string{"--socket", "/s", "--token-file", "/t", "--repo", "/r", "--codex-thread"}} {
		o, err := parse(args...)
		if err != nil {
			t.Fatal(err)
		}
		if d := o.declaration(); d.Harness != "unknown" {
			t.Fatalf("the harness was guessed: %+v", d)
		}
	}
	for _, bad := range []string{"CODEX", "robot", "../x", strings.Repeat("a", 100), "claude "} {
		if _, err := parse(append(base, "--client-harness", bad)...); core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("%q: %v", bad, err)
		}
	}
}
