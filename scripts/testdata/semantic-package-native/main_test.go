package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestInventoryRejectsRuntimePythonAndCheckout(t *testing.T) {
	for _, name := range []string{"usr/bin/python3", "opt/renamed/libpython3.13.so", "work/.git/config", "work/go.mod"} {
		t.Run(name, func(t *testing.T) {
			root := t.TempDir()
			path := filepath.Join(root, name)
			if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(path, []byte("contaminant"), 0o600); err != nil {
				t.Fatal(err)
			}
			if _, err := inventory(root); err == nil {
				t.Fatal("contaminated runtime accepted")
			}
		})
	}
}

func TestInventoryBindsAllOrdinaryBytesAndRejectsAliases(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "worker")
	if err := os.WriteFile(path, []byte("native fixture bytes"), 0o500); err != nil {
		t.Fatal(err)
	}
	files, err := inventory(root)
	if err != nil {
		t.Fatal(err)
	}
	if len(files) != 1 || files["/worker"] == "" {
		t.Fatalf("incomplete inventory: %v", files)
	}
	if err := sameFiles(files, map[string]string{"/worker": files["/worker"]}); err != nil {
		t.Fatal(err)
	}
	if err := sameFiles(files, map[string]string{"/worker": "wrong"}); err == nil {
		t.Fatal("changed bytes accepted")
	}
	if err := sameFiles(files, map[string]string{}); err == nil {
		t.Fatal("unlisted executable accepted, even with an innocent name")
	}
	if err := sameFiles(map[string]string{}, files); err == nil {
		t.Fatal("missing runtime file accepted")
	}
	if err := os.Symlink("/usr/bin/python3", filepath.Join(root, "alias")); err != nil {
		t.Fatal(err)
	}
	if _, err := inventory(root); err == nil {
		t.Fatal("runtime alias accepted")
	}
}

func TestReplyRequiresExactlyOneJSONValueAndPreservesNumbers(t *testing.T) {
	for _, raw := range []string{"", "{}\n{}", "{}\ntrailing"} {
		if _, err := decoded([]byte(raw)); err == nil {
			t.Fatalf("accepted %q", raw)
		}
	}
	value, err := decoded([]byte(`{"score":9007199254740993}`))
	if err != nil {
		t.Fatal(err)
	}
	if got := value.(map[string]any)["score"].(interface{ String() string }).String(); got != "9007199254740993" {
		t.Fatalf("lost exact score: %s", got)
	}
}

func TestPositiveReplyNeverTreatsErrorsAsRetainedSuccess(t *testing.T) {
	for _, test := range []struct {
		mode  string
		route string
		reply any
	}{
		{"rank", "direct", map[string]any{"error": "bad request"}},
		{"rank", "go", map[string]any{"error": nil}},
		{"rank", "direct", map[string]any{}},
		{"stream", "direct", map[string]any{"result": nil}},
		{"embed", "go", map[string]any{"result": map[string]any{"error": "bad request"}}},
		{"embed", "direct", map[string]any{"identity": map[string]any{"model_sha256": "not-a-hash"}}},
		{"rank", "go", map[string]any{"result": map[string]any{"model_sha256": strings.Repeat("a", 64), "algorithm": "bge-max-chunk/1", "error": nil}}},
		{"rank", "direct", map[string]any{"model_sha256": strings.Repeat("a", 64)}},
		{"rank", "direct", map[string]any{"model_sha256": strings.Repeat("a", 64), "algorithm": "wrong"}},
		{"embed", "direct", map[string]any{"identity": map[string]any{"model_sha256": strings.Repeat("a", 64), "algorithm": "bge-original-passages/1"}}},
		{"embed", "go", map[string]any{"result": map[string]any{"model_sha256": strings.Repeat("a", 64), "algorithm": "bge-original-passages/1", "dimensions": "384"}}},
	} {
		if err := positiveReply(test.reply, test.mode, test.route); err == nil {
			t.Fatalf("invalid positive accepted: %s/%s %#v", test.route, test.mode, test.reply)
		}
	}
	for _, route := range []string{"direct", "go"} {
		for _, mode := range []string{"rank", "stream", "embed"} {
			identity := map[string]any{"model_sha256": strings.Repeat("a", 64), "algorithm": "bge-max-chunk/1"}
			if mode == "embed" {
				identity["algorithm"], identity["dimensions"] = "bge-original-passages/1", json.Number("384")
			}
			var reply any = identity
			if route == "go" || mode == "stream" {
				reply = map[string]any{"result": identity}
			} else if mode == "embed" {
				reply = map[string]any{"identity": identity}
			}
			if err := positiveReply(reply, mode, route); err != nil {
				t.Fatalf("valid %s/%s identity refused: %v", route, mode, err)
			}
		}
	}
}
