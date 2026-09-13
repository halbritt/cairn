package main

import (
	"context"
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
