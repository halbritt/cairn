package mcpapi

import (
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
)

func TestStartupRejectsUnusableScope(t *testing.T) {
	for _, value := range []string{strings.Repeat("x", 257), strings.Repeat("界", 86), "bad\xff", "bad\x00"} {
		for field := range 3 {
			config := Config{Scope: core.Scope{Repo: "repo", TaskID: "task", RunID: "run"}, AvailableTokens: 32000}
			fields := []*string{&config.Scope.Repo, &config.Scope.TaskID, &config.Scope.RunID}
			*fields[field] = value
			if _, err := NewServer(nil, config); err == nil {
				t.Fatalf("scope field %d accepted %q", field, value)
			}
			if field == 0 {
				config.CodexThread = true
				config.Scope.TaskID, config.Scope.RunID = "", ""
				if _, err := NewServer(nil, config); err == nil {
					t.Fatalf("thread-scoped startup accepted repository %q", value)
				}
			}
		}
	}
}

func TestStartupRejectsMalformedContextText(t *testing.T) {
	for _, value := range []string{"bad\xff", "bad\x00"} {
		for field := range 6 {
			pins := &core.ContextPins{}
			fields := []*string{&pins.Revision, &pins.WorkspaceSHA256, &pins.TaskClass, &pins.TaskPhase, &pins.BindingID, &pins.CapabilityID}
			*fields[field] = value
			config := Config{Scope: core.Scope{Repo: "repo", TaskID: "task", RunID: "run"}, Context: pins, AvailableTokens: 32000}
			if _, err := NewServer(nil, config); err == nil {
				t.Fatalf("context field %d accepted malformed text", field)
			}
		}
	}
}

func TestStartupPreservesValidScopeAndDefersPinSemantics(t *testing.T) {
	for _, value := range []string{strings.Repeat("x", 256), strings.Repeat("界", 85) + "a", " literal � 日本語 😀 "} {
		config := Config{Scope: core.Scope{Repo: value, TaskID: value, RunID: value}, AvailableTokens: 32000,
			Context: &core.ContextPins{Revision: value, WorkspaceSHA256: "API validates the digest", TaskClass: "repair", TaskPhase: "review", BindingID: "literal �", CapabilityID: "literal 日本語"}}
		if _, err := NewServer(nil, config); err != nil {
			t.Fatalf("valid text or deferred pin semantics refused: %v", err)
		}
		if config.Scope != (core.Scope{Repo: value, TaskID: value, RunID: value}) || config.Context.Revision != value {
			t.Fatal("startup changed supplied text")
		}
	}
}
