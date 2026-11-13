package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"github.com/halbritt/cairn/internal/lifecycle/install"
	"os"
	"path/filepath"
	"testing"
)

func TestAgyRecoveryCommandRequiresAnExplicitPinnedSelection(t *testing.T) {
	for _, args := range [][]string{nil, {"--account-home", "/never-open/account"}, {"--layer", "project", "--record", "/never-open/journal", "--record-sha256", "bad"}, {"unexpected"}} {
		var out, diagnostic bytes.Buffer
		if err := runCommand(context.Background(), append([]string{"recover-agy-layer"}, args...), nil, &out, &diagnostic); err == nil || out.Len() != 0 {
			t.Fatal("unselected recovery accepted", err, out.String())
		}
	}
}

func TestAgyRecoveryCommandRestoresThroughDispatch(t *testing.T) {
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	account, workspace := filepath.Join(root, "account"), filepath.Join(root, "workspace")
	for _, p := range []string{account, workspace, filepath.Join(workspace, ".agents")} {
		if err := os.Mkdir(p, 0700); err != nil {
			t.Fatal(err)
		}
	}
	selected := filepath.Join(workspace, ".agents", "hooks.json")
	old := []byte("{\"credential_selector\":\"/never-read\",\"big\":900719925474099312345}\n")
	if err := os.WriteFile(selected, old, 0640); err != nil {
		t.Fatal(err)
	}
	prepared := filepath.Join(root, "prepared.json")
	raw := []byte(`{"cairn-coordination":{"Stop":[{"type":"command","command":"/pinned/cairn hook"}]}}`)
	if err := os.WriteFile(prepared, raw, 0600); err != nil {
		t.Fatal(err)
	}
	// Drive the real publisher before exercising recovery through dispatch.
	sum := sha256.Sum256(raw)
	pin := hex.EncodeToString(sum[:])
	published, err := install.PublishAgyLayer(context.Background(), install.AgyLayerPlan{AccountHome: account, Workspace: workspace, Layer: "project", Settings: install.AgyLayerSettings{Path: prepared, SHA256: pin}})
	if err != nil || published.Recovery == nil {
		t.Fatal(published, err)
	}
	var out, diagnostic bytes.Buffer
	args := []string{"recover-agy-layer", "--account-home", account, "--workspace", workspace, "--layer", "project", "--record", published.Recovery.Path, "--record-sha256", published.Recovery.SHA256}
	if err := runCommand(context.Background(), args, nil, &out, &diagnostic); err != nil {
		t.Fatal(err, diagnostic.String())
	}
	actual, err := os.ReadFile(selected)
	if err != nil || !bytes.Equal(actual, old) || !bytes.Contains(out.Bytes(), []byte(`"changed":true`)) {
		t.Fatal("actual recovery dispatch failed", out.String(), err)
	}
}
