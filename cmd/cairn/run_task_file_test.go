package main

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/runner"
)

func TestRunRejectsInvalidTaskFileBeforeRetrieval(t *testing.T) {
	dir := t.TempDir()
	for _, scenario := range []struct{ name, text, code string }{
		{"empty", "", "INVALID_REQUEST"},
		{"whitespace", " \r\n", "INVALID_REQUEST"},
		{"nul", "task\x00tail", "INVALID_REQUEST"},
		{"utf8", "task\xff", "INVALID_REQUEST"},
		{"oversized", strings.Repeat("x", runner.MaxPromptBytes+1), "BUDGET_REFUSED"},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			path := filepath.Join(dir, scenario.name)
			if err := os.WriteFile(path, []byte(scenario.text), 0600); err != nil {
				t.Fatal(err)
			}
			// A nil store makes any attempted retrieval fail the test.
			_, err := runTask(context.Background(), nil, []string{"--prompt-file", path, "--", "/bin/true"})
			if core.Code(err) != scenario.code {
				t.Fatalf("got %v, want %s", err, scenario.code)
			}
		})
	}
	fifo := filepath.Join(dir, "fifo")
	if err := syscall.Mkfifo(fifo, 0600); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{dir, fifo, "/dev/null", filepath.Join(dir, "missing"), ""} {
		if _, err := runTask(context.Background(), nil, []string{"--prompt-file", path, "--", "/bin/true"}); err == nil {
			t.Fatalf("accepted %q", path)
		}
	}
	for _, inline := range []string{"", "task"} {
		_, err := runTask(context.Background(), nil, []string{"--prompt-file", fifo, "--prompt", inline, "--", "/bin/true"})
		if core.Code(err) != "INVALID_REQUEST" || !strings.Contains(err.Error(), "only one") {
			t.Fatalf("ambiguous task sources: %v", err)
		}
	}
}
