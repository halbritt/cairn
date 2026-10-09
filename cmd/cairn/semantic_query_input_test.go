package main

import (
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
)

func TestSemanticQueryInputBoundariesAndPrivacy(t *testing.T) {
	text := "Do NOT use public storage.\n  private private! "
	for _, file := range []bool{false, true} {
		path := ""
		if file {
			path = filepath.Join(t.TempDir(), "private-path-canary")
			if err := os.WriteFile(path, []byte(text), 0600); err != nil {
				t.Fatal(err)
			}
		}
		got, err := readSemanticQuery("storage", path, file, !file, strings.NewReader(text))
		if err != nil || got != text {
			t.Fatalf("exact bytes changed: %q %v", got, err)
		}
	}
	for _, text := range []string{"", " \t\n", "private\x00canary", string([]byte{255}), strings.Repeat("x", 4090), strings.Repeat("x", 4097)} {
		if _, err := readSemanticQuery("storage", "", false, true, strings.NewReader(text)); err == nil || strings.Contains(err.Error(), "canary") {
			t.Fatal("invalid input accepted or disclosed")
		}
	}
	boundary := strings.Repeat("é", 2044) + "x"
	if got, err := readSemanticQuery("storage", "", false, true, strings.NewReader(boundary)); err != nil || got != boundary {
		t.Fatal("UTF8 aggregate boundary changed")
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "private-file-canary")
	if _, err := readSemanticQuery("storage", path, true, false, nil); err == nil || strings.Contains(err.Error(), path) {
		t.Fatal("open refusal leaks path")
	}
	if err := syscall.Mkfifo(path, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := readSemanticQuery("storage", path, true, false, nil); err == nil {
		t.Fatal("FIFO accepted")
	}
	if _, err := readSemanticQuery("storage", path, true, true, nil); err == nil {
		t.Fatal("ambiguous sources accepted")
	}
	if _, err := readSemanticQuery("storage", "", true, false, nil); err == nil {
		t.Fatal("empty selected path downgraded")
	}
}
