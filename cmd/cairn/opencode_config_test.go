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
		"--task-class", "configuration", "--task-phase", "validation", "--binding", "binding; literal", "--capability", "native"}
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
		want := append([]string{"/absolute/cairn executable", "mcp"}, args[:12]...)
		want = append(want, "--search-view", "compact/1", "--tool-profile", "compact/2")
		want = append(want, args[12:]...)
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
		{"--socket", "/missing.sock", "--token-file", "/missing.token", "--repo", "repo", "--codex-thread"},
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

func TestOpenCodeConfigLocalLlamaCppStructuredAndOffSwitch(t *testing.T) {
	base := []string{"--socket", "/missing.sock", "--token-file", "/missing.token", "--repo", "repo", "--task", "task", "--run", "run"}

	// 1. Structured mode sets enable_thinking=false on both runtime and structured model
	var out bytes.Buffer
	if err := writeOpenCodeConfig(&out, append(append([]string{}, base...), "--llamacpp-structured"), "/cairn"); err != nil {
		t.Fatal(err)
	}
	var structured openCodeMemoryConfig
	if err := json.Unmarshal(out.Bytes(), &structured); err != nil {
		t.Fatal(err)
	}
	provider := structured.Provider["llamacpp"]
	if provider.Options["baseURL"] != "http://localhost:8081/v1" {
		t.Fatalf("unexpected llamacpp baseURL: %+v", provider.Options)
	}
	baseModel := provider.Models["qwen3.8-27b"]
	kwargs, _ := baseModel.Options["chat_template_kwargs"].(map[string]any)
	if kwargs["enable_thinking"] != false {
		t.Fatalf("expected enable_thinking=false on structured runtime model: %+v", baseModel.Options)
	}
	aliasModel := provider.Models["qwen3.8-27b-structured"]
	aliasKwargs, _ := aliasModel.Options["chat_template_kwargs"].(map[string]any)
	if aliasModel.ID != "qwen3.8-27b" || aliasKwargs["enable_thinking"] != false {
		t.Fatalf("expected bounded structured alias model: %+v", aliasModel)
	}

	// 2. --allow-thinking flag re-enables unrestricted thinking on non-structured model while keeping structured alias bounded
	out.Reset()
	if err := writeOpenCodeConfig(&out, append(append([]string{}, base...), "--llamacpp-structured", "--allow-thinking"), "/cairn"); err != nil {
		t.Fatal(err)
	}
	var allowed openCodeMemoryConfig
	if err := json.Unmarshal(out.Bytes(), &allowed); err != nil {
		t.Fatal(err)
	}
	if _, hasKwargs := allowed.Provider["llamacpp"].Models["qwen3.8-27b"].Options["chat_template_kwargs"]; hasKwargs {
		t.Fatalf("expected --allow-thinking to keep non-structured model thinking unrestricted: %+v", allowed.Provider["llamacpp"].Models["qwen3.8-27b"].Options)
	}
	aliasKwargs2, _ := allowed.Provider["llamacpp"].Models["qwen3.8-27b-structured"].Options["chat_template_kwargs"].(map[string]any)
	if aliasKwargs2["enable_thinking"] != false {
		t.Fatalf("structured alias must remain bounded even with --allow-thinking: %+v", allowed.Provider["llamacpp"].Models["qwen3.8-27b-structured"])
	}

	// 3. Env off-switch CAIRN_OPENCODE_ALLOW_THINKING=1 also re-enables unrestricted thinking on non-structured model
	t.Setenv("CAIRN_OPENCODE_ALLOW_THINKING", "1")
	out.Reset()
	if err := writeOpenCodeConfig(&out, append(append([]string{}, base...), "--llamacpp-structured"), "/cairn"); err != nil {
		t.Fatal(err)
	}
	var envAllowed openCodeMemoryConfig
	if err := json.Unmarshal(out.Bytes(), &envAllowed); err != nil {
		t.Fatal(err)
	}
	if _, hasKwargs := envAllowed.Provider["llamacpp"].Models["qwen3.8-27b"].Options["chat_template_kwargs"]; hasKwargs {
		t.Fatalf("expected CAIRN_OPENCODE_ALLOW_THINKING=1 to re-enable thinking: %+v", envAllowed.Provider["llamacpp"].Models["qwen3.8-27b"].Options)
	}
}
