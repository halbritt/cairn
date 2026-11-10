package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/install"
)

func TestGenerationCommandPublishesAndReadsExactSelection(t *testing.T) {
	base, err := os.MkdirTemp("/dev/shm", "cairn-generation-command-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.RemoveAll(base); err != nil {
			t.Error(err)
		}
	})
	root := filepath.Join(base, "releases")
	if err := os.Mkdir(root, 0700); err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(base, "source")
	raw := []byte("{ \"custom_field\": \"preserved\", \"token_file\": \"never-opened\" }\n")
	if err := os.WriteFile(source, raw, 0600); err != nil {
		t.Fatal(err)
	}
	h := sha256.Sum256(raw)
	plan := fmt.Sprintf(`{"id":"147fc8dd-b0d9-4b9f-8be1-59a2c77ef0cb","files":[{"name":"memory.json","path":%q,"sha256":%q}]}`, source, hex.EncodeToString(h[:]))
	var out, diagnostic bytes.Buffer
	if err := runCommand(context.Background(), []string{"generation", "--root", root}, strings.NewReader(plan), &out, &diagnostic); err != nil {
		t.Fatal(err)
	}
	var selected install.Generation
	if err := json.Unmarshal(out.Bytes(), &selected); err != nil {
		t.Fatal(err)
	}
	out.Reset()
	if err := runCommand(context.Background(), []string{"inspect-generation", "--path", selected.Path, "--sha256", selected.SHA256}, nil, &out, &diagnostic); err != nil {
		t.Fatal(err)
	}
	var inspected struct {
		Selection install.Generation
		Files     []install.File
	}
	if err := json.Unmarshal(out.Bytes(), &inspected); err != nil {
		t.Fatal(err)
	}
	if inspected.Selection != selected || len(inspected.Files) != 1 {
		t.Fatalf("inspection = %+v", inspected)
	}
	installed, err := os.ReadFile(inspected.Files[0].Path)
	if err != nil || !bytes.Equal(raw, installed) {
		t.Fatalf("installed bytes changed: %v", err)
	}
	if diagnostic.Len() != 0 {
		t.Fatalf("unexpected diagnostic: %s", diagnostic.String())
	}
}

func TestGenerationCommandRefusesAmbiguousPlansBeforeWrites(t *testing.T) {
	plans := []string{
		`{"id":"one","id":"two","files":[]}`,
		`{"id":"one","files":[{"name":"a","name":"b","path":"/a","sha256":"hash"}]}`,
		`{"id":"one","files":[],"extra":true}`,
		`{"id":"one","files":[]} {}`,
		`{"id":"one","files":[{"name":"a","path":"/a","sha256":"hash","extra":true}]}`,
		`{"id":"one","files":[{"name":"\ud800","path":"/a","sha256":"hash"}]}`,
		`null`,
	}
	for _, plan := range plans {
		var out, diagnostic bytes.Buffer
		if err := runCommand(context.Background(), []string{"generation", "--root", "/never-opened"}, strings.NewReader(plan), &out, &diagnostic); err == nil || out.Len() != 0 {
			t.Fatalf("accepted ambiguous input: %s: %v", plan, err)
		}
	}
}
