package main

import (
	"bytes"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestOpenCodeConfigRejectsMalformedText(t *testing.T) {
	var out bytes.Buffer
	args := []string{"--socket", "/missing.sock", "--token-file", "/missing.token", "--repo", "repo", "--task", "task", "--run", "run", "--revision", "change-\xff"}
	if err := writeOpenCodeConfig(&out, args, "/cairn"); err == nil || out.Len() != 0 {
		t.Fatalf("malformed text produced configuration: %v %q", err, out.String())
	}
}

func TestHarnessConfigRejectsUnusableArgumentsBeforeOutput(t *testing.T) {
	writers := map[string]func(io.Writer, []string, string) error{
		"opencode": writeOpenCodeConfig, "codex": writeCodexConfig, "claude": writeClaudeConfig,
	}
	base := []string{"--socket", "/missing.sock", "--token-file", "/missing.token", "--repo", "repo", "--task", "task", "--run", "run"}
	for name, write := range writers {
		t.Run(name, func(t *testing.T) {
			for _, field := range []string{"--socket", "--token-file", "--repo", "--task", "--run", "--revision", "--workspace-sha256", "--task-class", "--task-phase", "--binding", "--capability"} {
				for _, value := range []string{"bad\xff", "bad\x00"} {
					var out bytes.Buffer
					if err := write(&out, append(append([]string{}, base...), field, value), "/cairn"); err == nil || out.Len() != 0 {
						t.Fatalf("%s accepted malformed %s: %v %q", name, field, err, out.String())
					}
				}
			}
			for _, field := range []string{"--repo", "--task", "--run"} {
				var out bytes.Buffer
				if err := write(&out, append(append([]string{}, base...), field, strings.Repeat("界", 86)), "/cairn"); err == nil || out.Len() != 0 {
					t.Fatalf("%s accepted oversized %s: %v", name, field, err)
				}
			}
			for _, executable := range []string{"/bad\xff", "/bad\x00"} {
				var out bytes.Buffer
				if err := write(&out, base, executable); err == nil || out.Len() != 0 {
					t.Fatalf("%s accepted malformed executable: %v", name, err)
				}
			}
		})
	}
}

func TestOpenCodeInstallRejectsMalformedTextBeforeFiles(t *testing.T) {
	for _, field := range []string{"--project", "--socket", "--token-file", "--repo", "--task", "--run", "--revision", "--workspace-sha256", "--task-class", "--task-phase", "--binding", "--capability", "executable"} {
		for _, value := range []string{"bad\xff", "bad\x00"} {
			project := t.TempDir()
			args, executable := installArgs(project), "/cairn"
			if field == "executable" {
				executable = value
			} else {
				args = append(args, field, value)
			}
			if _, err := installOpenCode(args, executable); err == nil {
				t.Fatalf("malformed %s was installed", field)
			}
			if _, err := os.Stat(filepath.Join(project, ".opencode")); !os.IsNotExist(err) {
				t.Fatalf("invalid installation touched files: %v", err)
			}
		}
	}
}
