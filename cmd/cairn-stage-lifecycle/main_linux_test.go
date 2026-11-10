package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestRunStagesRealExecutableWithoutSelectingLegacy(t *testing.T) {
	source, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	content, err := os.ReadFile(source)
	if err != nil {
		t.Fatal(err)
	}
	pin := sha256.Sum256(content)
	base, err := os.MkdirTemp("/dev/shm", "cairn-stage-cli-test-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.RemoveAll(base); err != nil {
			t.Error(err)
		}
	})
	source = filepath.Join(base, "selected-go-executable")
	if err := os.WriteFile(source, content, 0700); err != nil {
		t.Fatal(err)
	}
	root := filepath.Join(base, "releases-雪 ' spaces")
	if err := os.Mkdir(root, 0700); err != nil {
		t.Fatal(err)
	}
	var out, diagnostic bytes.Buffer
	if err := run(context.Background(), []string{"--root", root, "--binary", source, "--sha256", hex.EncodeToString(pin[:])}, &out, &diagnostic); err != nil {
		t.Fatal(err)
	}
	var descriptor struct {
		Path   string `json:"path"`
		SHA256 string `json:"sha256"`
	}
	if err := json.Unmarshal(out.Bytes(), &descriptor); err != nil {
		t.Fatal(err)
	}
	if descriptor.Path != filepath.Join(root, descriptor.SHA256, "cairn") || diagnostic.Len() != 0 {
		t.Fatalf("output=%s stderr=%s", out.String(), diagnostic.String())
	}
	staged, err := os.ReadFile(descriptor.Path)
	if err != nil || !bytes.Equal(staged, content) {
		t.Fatalf("staged executable differs: %v", err)
	}
}

func TestRunRefusalHasNoSuccessDescriptor(t *testing.T) {
	var out, diagnostic bytes.Buffer
	if err := run(context.Background(), []string{"--root", t.TempDir()}, &out, &diagnostic); err == nil {
		t.Fatal("missing source/pin accepted")
	}
	if out.Len() != 0 {
		t.Fatalf("failure published descriptor: %s", out.String())
	}
}
