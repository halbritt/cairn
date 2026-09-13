package main

import (
	"bytes"
	"errors"
	"testing"
)

func TestCodexConfigRejectsInvalidInputBeforeOutput(t *testing.T) {
	base := []string{"--socket", "/missing.sock", "--token-file", "/missing.token", "--repo", "repo", "--codex-thread"}
	for _, args := range [][]string{nil, base[:6], append(append([]string{}, base...), "extra"),
		append(append([]string{}, base...), "--task", "task"),
		append(append([]string{}, base...), "--run", "run"),
		append(append([]string{}, base...), "--tokens", "255"),
		append(append([]string{}, base...), "--tokens", "1000001"),
		append(append([]string{}, base...), "--repo", "*"),
		append(append([]string{}, base...), "--token-file="),
		append(append([]string{}, base...), "--revision", "invalid\xff"),
		append(append([]string{}, base...), "--memory-only")} {
		var out bytes.Buffer
		if err := writeCodexConfig(&out, args, "/cairn"); err == nil || out.Len() != 0 {
			t.Fatalf("invalid invocation produced configuration: %q %s", args, out.Bytes())
		}
	}
	var out bytes.Buffer
	if err := writeCodexConfig(&out, base, "/invalid\xff"); err == nil || out.Len() != 0 {
		t.Fatalf("invalid executable encoding produced configuration: %v %s", err, out.Bytes())
	}
	want := errors.New("output failed")
	if err := writeCodexConfig(configFailureWriter{want}, base, "/cairn"); !errors.Is(err, want) {
		t.Fatalf("output failure lost: %v", err)
	}
}
