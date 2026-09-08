package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestOpenCodeConfigPreservesArgumentsWithoutCredentials(t *testing.T) {
	t.Setenv("HOME", "")
	t.Setenv("CAIRN_HOME", "")
	t.Setenv("CAIRN_DATABASE_URL", "host=/absent-config-db dbname=denied")
	socket := "missing socket '$(command)'.sock"
	token := "missing token 日本語"
	args := []string{"--socket", socket, "--token-file", token, "--repo", "repo:fixture",
		"--task", "task 'quoted'\nsecond line", "--run", "--literal-run", "--tokens", "64000",
		"--revision", strings.Repeat("a", 40), "--workspace-sha256", strings.Repeat("b", 64),
		"--task-class", "configuration", "--binding", "binding; literal", "--capability", "native"}
	for _, memoryOnly := range []bool{false, true} {
		invocation := append([]string{}, args...)
		if memoryOnly {
			invocation = append(invocation, "--memory-only")
		}
		var out bytes.Buffer
		if err := writeOpenCodeConfig(&out, invocation, "/absolute/cairn executable"); err != nil {
			t.Fatal(err)
		}
		var config openCodeMemoryConfig
		if err := json.Unmarshal(out.Bytes(), &config); err != nil {
			t.Fatal(err)
		}
		wantSocket, err := filepath.Abs(socket)
		if err != nil {
			t.Fatal(err)
		}
		wantToken, err := filepath.Abs(token)
		if err != nil {
			t.Fatal(err)
		}
		want := append([]string{"/absolute/cairn executable", "mcp"}, args...)
		want[3], want[5] = wantSocket, wantToken
		server := config.MCP["cairn"]
		if !reflect.DeepEqual(server.Command, want) || server.Type != "local" || !server.Enabled || len(config.MCP) != 1 {
			t.Fatalf("literal command changed: %s", out.Bytes())
		}
		if memoryOnly {
			if !reflect.DeepEqual(config.Permission, map[string]string{"*": "deny", "cairn_cairn_search": "allow", "cairn_cairn_pull": "allow"}) {
				t.Fatal(config.Permission)
			}
		} else if strings.Contains(out.String(), "permission") {
			t.Fatal("default fragment changed host tool policy")
		}
	}
}

func TestOpenCodeConfigRejectsIncompleteInvocation(t *testing.T) {
	base := []string{"--socket", "/missing.sock", "--token-file", "/missing.token", "--repo", "repo", "--task", "task", "--run", "run"}
	for _, args := range [][]string{nil, append(append([]string{}, base...), "extra"),
		append(append([]string{}, base...), "--task", "*"), append(append([]string{}, base...), "--tokens", "255"),
		append(append([]string{}, base...), "--token-file=")} {
		var out bytes.Buffer
		if err := writeOpenCodeConfig(&out, args, "/cairn"); err == nil || out.Len() != 0 {
			t.Fatalf("bad invocation produced configuration: %v %s", err, out.Bytes())
		}
	}
	want := errors.New("output failed")
	if err := writeOpenCodeConfig(configFailureWriter{want}, base, "/cairn"); !errors.Is(err, want) {
		t.Fatalf("output failure lost: %v", err)
	}
}

type configFailureWriter struct{ err error }

func (w configFailureWriter) Write([]byte) (int, error) { return 0, w.err }
