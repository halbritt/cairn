package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestOpenCodeCommandNamesAndSymlinks(t *testing.T) {
	for name, want := range map[string]bool{"opencode": true, "/bin/opencode.exe": true, "OPENCODE": true, "my-opencode-plugin": false, "opencode-helper": false} {
		if got := isOpenCodeCommand(name); got != want {
			t.Errorf("%s: %v", name, got)
		}
	}
	dir := t.TempDir()
	target := filepath.Join(dir, "opencode")
	if err := os.WriteFile(target, []byte("#!/bin/sh\nexit 0\n"), 0700); err != nil {
		t.Fatal(err)
	}
	alias := filepath.Join(dir, "renamed")
	if err := os.Symlink(target, alias); err != nil {
		t.Fatal(err)
	}
	if !isOpenCodeCommand(alias) {
		t.Fatal("renamed symlink missed")
	}
	t.Setenv("PATH", dir)
	if !isOpenCodeCommand("renamed") {
		t.Fatal("PATH symlink missed")
	}
}
