package main

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
)

type unreadHelpInput struct{}

func (unreadHelpInput) Read([]byte) (int, error) { panic("help read stdin") }

func TestAgentHelpWorksWithoutConnectionOrHome(t *testing.T) {
	t.Setenv("HOME", "")
	t.Setenv("CAIRN_HOME", "")
	for _, args := range [][]string{
		{"agent", "--help"},
		{"agent", "replace", "--help"},
		{"agent", "--socket", "/absent/api.sock", "--token-file", "/absent/token", "history", "-h"},
	} {
		got, err := run(context.Background(), args, unreadHelpInput{})
		if err != nil || !strings.Contains(fmt.Sprint(got), "Usage: cairn agent") {
			t.Fatalf("offline help %v: %v %v", args, got, err)
		}
	}
}

func TestAgentHelpDoesNotHideMalformedArguments(t *testing.T) {
	for _, args := range [][]string{
		{"agent", "--unknown", "--help"},
		{"agent", "unknown-operation", "--help"},
		{"agent", "replace", "--help", "extra"},
	} {
		if _, err := run(context.Background(), args, strings.NewReader("")); core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("invalid invocation %v became help: %v", args, err)
		}
	}
}
