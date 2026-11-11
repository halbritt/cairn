package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func rustServeConfig(t *testing.T, mode string) string {
	t.Helper()
	dir := t.TempDir()
	config := map[string]string{"mode": mode, "executable": "/bin/true", "model_dir": dir, "source_root": dir}
	for _, key := range []string{"ort_runtime", "openblas", "gfortran", "quadmath"} {
		path := filepath.Join(dir, key+".so")
		if err := os.WriteFile(path, nil, 0600); err != nil {
			t.Fatal(err)
		}
		config[key] = path
	}
	data, err := json.Marshal(config)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "rust.json")
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestServeRustConfigurationIsExplicitAndExclusive(t *testing.T) {
	t.Setenv("CAIRN_HOME", t.TempDir())
	t.Setenv("CAIRN_SEMANTIC_RUST_CONFIG", "/must-not-be-read.json")
	// No Rust flag: the ordinary missing identity configuration is still the
	// first startup boundary, even when an ambient Rust-looking variable exists.
	if err := serveLocal(context.Background(), "unused", nil); !os.IsNotExist(err) {
		t.Fatalf("default selected an unexpected worker configuration: %v", err)
	}
	for _, legacy := range []string{"--semantic-command", "--semantic-stream-command", "--embedding-command"} {
		err := serveLocal(context.Background(), "unused", []string{legacy, "/unused", "--semantic-rust-config", "/unused.json"})
		if err == nil || !strings.Contains(err.Error(), "choose one semantic command mode") {
			t.Fatalf("conflicting %s selection: %v", legacy, err)
		}
	}
}

func TestServeRustModesAndIdleSelectionValidateBeforeOpeningStore(t *testing.T) {
	t.Setenv("CAIRN_HOME", t.TempDir())
	for _, mode := range []string{"rank", "stream", "embed"} {
		config := rustServeConfig(t, mode)
		args := []string{"--semantic-rust-config", config}
		if err := serveLocal(context.Background(), "unused", args); !os.IsNotExist(err) {
			t.Fatalf("valid %s selection did not reach the missing identity file: %v", mode, err)
		}
		args = append(args, "--semantic-idle-timeout", "10ms")
		err := serveLocal(context.Background(), "unused", args)
		if mode == "stream" {
			if !os.IsNotExist(err) {
				t.Fatalf("Rust stream idle setting refused: %v", err)
			}
		} else if err == nil || !strings.Contains(err.Error(), "requires --semantic-stream-command") {
			t.Fatalf("idle setting without stream: %v", err)
		}
	}
}

func TestServeRejectsInvalidSemanticIdleConfiguration(t *testing.T) {
	t.Setenv("CAIRN_HOME", t.TempDir())
	for _, args := range [][]string{
		{"--semantic-idle-timeout", "5m"},
		{"--semantic-idle-timeout", "30s"},
		{"--semantic-command", "/unused", "--semantic-idle-timeout", "5m"},
	} {
		if err := serveLocal(context.Background(), "unused", args); err == nil || !strings.Contains(err.Error(), "requires --semantic-stream-command") {
			t.Fatalf("%v: %v", args, err)
		}
	}
}

func TestServeRejectsNonpositiveSemanticIdleTimeout(t *testing.T) {
	t.Setenv("CAIRN_HOME", t.TempDir())
	for _, idle := range []string{"0", "-1s"} {
		err := serveLocal(context.Background(), "unused", []string{"--semantic-stream-command", "/unused", "--semantic-idle-timeout", idle})
		if err == nil || !strings.Contains(err.Error(), "INVALID_REQUEST: semantic idle timeout must be positive") {
			t.Fatalf("%s: %v", idle, err)
		}
	}
}

func TestServeRefusesAMalformedOrUnconfiguredSourceOpeningBinding(t *testing.T) {
	home := t.TempDir()
	t.Setenv("CAIRN_HOME", home)
	identities := filepath.Join(home, "identities.json")
	digest := sha256.Sum256([]byte("synthetic-serve-binding"))
	config := `[{"token_sha256":"` + hex.EncodeToString(digest[:]) + `","principal":"serve:listed","repo":"fixture:repo","role":"agent","destination":"hosted"}]`
	if err := os.WriteFile(identities, []byte(config), 0o600); err != nil {
		t.Fatal(err)
	}
	args := []string{"--identities", identities, "--socket", filepath.Join(home, "api.sock")}
	for _, value := range []string{"serve:listed,", ",serve:listed", "serve:listed,,x", " serve:listed", "serve:listed,serve:listed", "serve:other", "*"} {
		t.Setenv("CAIRN_SOURCE_OPENING_BINDING", value)
		// The DSN is unusable on purpose: refusal must come before any store is opened.
		if err := serveLocal(context.Background(), "unused", args); err == nil || !strings.Contains(err.Error(), "invalid source-opening binding configuration") {
			t.Fatalf("%q: %v", value, err)
		}
	}
	for _, value := range []string{"", "serve:listed"} {
		t.Setenv("CAIRN_SOURCE_OPENING_BINDING", value)
		if err := serveLocal(context.Background(), "unused", args); err == nil || strings.Contains(err.Error(), "source-opening binding") {
			t.Fatalf("%q: a valid setting must pass validation and fail later: %v", value, err)
		}
	}
}
