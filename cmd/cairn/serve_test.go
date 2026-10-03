package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

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
