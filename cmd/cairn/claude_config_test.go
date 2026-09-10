package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"path/filepath"
	"reflect"
	"testing"
)

func TestClaudeConfigPreservesLaunchArguments(t *testing.T) {
	args := []string{"--socket", "socket with space", "--token-file", "token\"file", "--repo", "repo", "--task", "repair", "--run", "attempt", "--tokens", "64000", "--revision", "0123456789012345678901234567890123456789", "--task-class", "repair", "--binding", "native", "--capability", "declared", "--workspace-sha256", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
	args = append(args, "--task-phase", "validation")
	var out bytes.Buffer
	if err := writeClaudeConfig(&out, args, "bin/cairn"); err != nil {
		t.Fatal(err)
	}
	var config claudeMemoryConfig
	if err := json.Unmarshal(out.Bytes(), &config); err != nil {
		t.Fatal(err)
	}
	server := config.Servers["cairn"]
	executable, _ := filepath.Abs("bin/cairn")
	socket, _ := filepath.Abs(args[1])
	token, _ := filepath.Abs(args[3])
	want := []string{"mcp", "--socket", socket, "--token-file", token, "--repo", "repo", "--task", "repair", "--run", "attempt", "--tokens", "64000", "--revision", args[13], "--workspace-sha256", args[21], "--task-class", "repair", "--task-phase", "validation", "--binding", "native", "--capability", "declared"}
	if len(config.Servers) != 1 || server.Type != "stdio" || server.Command != executable || !reflect.DeepEqual(server.Args, want) {
		t.Fatalf("generated launch changes configured arguments: %+v", config)
	}
}

func TestClaudeConfigRefusesBeforeOutput(t *testing.T) {
	base := []string{"--socket", "/missing.sock", "--token-file", "/missing.token", "--repo", "repo", "--task", "task", "--run", "run"}
	for _, args := range [][]string{nil, base[:6], append(append([]string{}, base...), "extra"),
		append(append([]string{}, base...), "--codex-thread"),
		append(append([]string{}, base...), "--tokens", "255"),
		append(append([]string{}, base...), "--repo", "*"),
		append(append([]string{}, base...), "--token-file="),
		append(append([]string{}, base...), "--repo", "${REPO}"),
		append(append([]string{}, base...), "--revision", "invalid\xff")} {
		var out bytes.Buffer
		if err := writeClaudeConfig(&out, args, "/cairn"); err == nil || out.Len() != 0 {
			t.Fatalf("invalid invocation produced configuration: %q %s", args, out.Bytes())
		}
	}
	for _, executable := range []string{"/invalid\xff", "/${BIN}/cairn"} {
		var out bytes.Buffer
		if err := writeClaudeConfig(&out, base, executable); err == nil || out.Len() != 0 {
			t.Fatalf("invalid executable produced configuration: %q", executable)
		}
	}
	want := errors.New("output failed")
	if err := writeClaudeConfig(configFailureWriter{want}, base, "/cairn"); !errors.Is(err, want) {
		t.Fatalf("output failure lost: %v", err)
	}
}
