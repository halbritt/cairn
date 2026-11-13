package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/install"
)

func TestAgyLayerCommandPublishesActualScopedSettings(t *testing.T) {
	for _, layer := range []string{"account", "project"} {
		t.Run(layer, func(t *testing.T) {
			root := t.TempDir()
			if err := os.Chmod(root, 0700); err != nil {
				t.Fatal(err)
			}
			account, workspace := filepath.Join(root, "account"), filepath.Join(root, "workspace")
			for _, path := range []string{account, workspace} {
				if err := os.Mkdir(path, 0700); err != nil {
					t.Fatal(err)
				}
			}
			source := filepath.Join(root, "prepared.json")
			raw := []byte(`{"cairn-coordination":{"Stop":[{"type":"command","command":"/pinned/cairn hook"}]}}`)
			if err := os.WriteFile(source, raw, 0600); err != nil {
				t.Fatal(err)
			}
			sum := sha256.Sum256(raw)
			plan, err := json.Marshal(map[string]any{"schema": "cairn.agy-layer-install/1", "settings": map[string]string{"path": source, "sha256": hex.EncodeToString(sum[:])}, "replace_commands": []string{}})
			if err != nil {
				t.Fatal(err)
			}
			var out, diagnostic bytes.Buffer
			if err := runCommand(context.Background(), []string{"agy-layer", "--account-home", account, "--workspace", workspace, "--layer", layer}, bytes.NewReader(plan), &out, &diagnostic); err != nil {
				t.Fatal(err, diagnostic.String())
			}
			var result install.AgyLayerSelection
			if err := json.Unmarshal(out.Bytes(), &result); err != nil || !result.Changed {
				t.Fatal(result, err)
			}
			want := filepath.Join(account, ".gemini", "config", "hooks.json")
			if layer == "project" {
				want = filepath.Join(workspace, ".agents", "hooks.json")
			}
			actual, err := os.ReadFile(want)
			if err != nil || result.Path != want || !bytes.Contains(actual, []byte("/pinned/cairn hook")) {
				t.Fatal("dispatch did not publish", result, err)
			}
			if diagnostic.Len() != 0 {
				t.Fatal(diagnostic.String())
			}
		})
	}
}

func TestAgyLayerCommandRefusesMalformedIntentBeforeOpeningRoots(t *testing.T) {
	for _, raw := range []string{
		`{"schema":"cairn.agy-layer-install/1","schema":"other","settings":{}}`,
		`{"schema":"cairn.agy-layer-install/1","settings":{"path":"/a","path":"/b","sha256":"x"}}`,
		`{"schema":"cairn.agy-layer-install/1","settings":{"path":"/a","sha256":"x"},"replace_commands":null}`,
		`{"schema":"cairn.agy-layer-install/1","settings":{"path":"/a","sha256":"x"},"replace_commands":[null]}`,
		`{"schema":"cairn.agy-layer-install/1","settings":{"path":"/a","sha256":"x"},"replace_commands":[false]}`,
		`{"schema":"cairn.agy-layer-install/1","settings":{"path":"/a","sha256":"x"},"extra":true}`,
	} {
		var out, diagnostic bytes.Buffer
		if err := runCommand(context.Background(), []string{"agy-layer", "--account-home", "/never-open/account", "--workspace", "/never-open/project", "--layer", "project"}, strings.NewReader(raw), &out, &diagnostic); err == nil || out.Len() != 0 {
			t.Fatalf("accepted malformed plan %s: %v", raw, err)
		}
	}
}
