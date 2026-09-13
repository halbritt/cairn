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

func TestAgentFlagOperationHelpWorksWithoutConnectionOrHome(t *testing.T) {
	t.Setenv("HOME", "")
	t.Setenv("CAIRN_HOME", "")
	for _, operation := range []struct {
		name    string
		needles []string
	}{
		{"search", []string{"--task TASK", "advisory-conflicts", "entity-symbol"}},
		{"remember", []string{"--stdin", "shareable", "pins"}},
		{"pull", []string{"RECEIPT_UUID HANDLE_UUID", "length", "request-id"}},
		{"pull-evidence", []string{"EVIDENCE_UUID EXPECTED_SHA256", "length", "request-id"}},
	} {
		for _, helpFlag := range []string{"--help", "-h"} {
			got, err := run(context.Background(), []string{"agent", operation.name, helpFlag}, unreadHelpInput{})
			text := fmt.Sprint(got)
			if err != nil {
				t.Fatalf("offline %s help: %v", operation.name, err)
			}
			for _, needle := range operation.needles {
				if !strings.Contains(text, needle) {
					t.Errorf("offline %s help omitted %q:\n%s", operation.name, needle, text)
				}
			}
		}
	}
}

func TestAgentHelpDoesNotHideMalformedArguments(t *testing.T) {
	for _, args := range [][]string{
		{"agent", "--unknown", "--help"},
		{"agent", "unknown-operation", "--help"},
		{"agent", "replace", "--help", "extra"},
		{"agent", "search", "--help", "extra"},
		{"agent", "remember", "-h", "extra"},
		{"agent", "pull", "--help", "extra"},
		{"agent", "pull-evidence", "-h", "extra"},
	} {
		if _, err := run(context.Background(), args, strings.NewReader("")); core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("invalid invocation %v became help: %v", args, err)
		}
	}
}
