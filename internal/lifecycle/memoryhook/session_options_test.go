package memoryhook

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/ledger"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func TestSessionCompactInboxEnabledPerSessionAndGuards(t *testing.T) {
	dir := t.TempDir()
	if err := os.Chmod(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	cfg := value.Mapping(
		value.Field("harness", value.Text("claude")),
		value.Field("native_delivery", value.Boolean(true)),
		value.Field("binding", value.Text("claude-two")),
		value.Field("state_dir", value.Text(dir)),
	)
	enabled, err := sessionCompactInboxEnabled(cfg, "coord-session")
	if err != nil || enabled {
		t.Fatalf("expected disabled by default, got enabled=%v err=%v", enabled, err)
	}

	optDir := filepath.Join(dir, "session-options")
	if err := os.MkdirAll(optDir, 0o700); err != nil {
		t.Fatal(err)
	}
	optPath := filepath.Join(optDir, hashText("claude-two\x00coord-session")+".json")
	validJSON := `{"binding":"claude-two","compact_inbox_version":1,"native_session_id":"coord-session","notice_batch_version":1,"schema":"cairn.session-inbox-options/1","updated_at":100}`
	if err := os.WriteFile(optPath, []byte(validJSON), 0o600); err != nil {
		t.Fatal(err)
	}

	enabled, err = sessionCompactInboxEnabled(cfg, "coord-session")
	if err != nil || !enabled {
		t.Fatalf("expected enabled for coord-session, got enabled=%v err=%v", enabled, err)
	}
	otherEnabled, err := sessionCompactInboxEnabled(cfg, "worker-session")
	if err != nil || otherEnabled {
		t.Fatalf("expected disabled for worker-session, got enabled=%v err=%v", otherEnabled, err)
	}

	// Auto-settle alone (without compact_inbox_version) must return false without refusing.
	autoOnlyJSON := `{"binding":"claude-two","native_session_id":"coord-session","notice_auto_settle_version":1,"schema":"cairn.session-inbox-options/1","updated_at":100}`
	if err := os.WriteFile(optPath, []byte(autoOnlyJSON), 0o600); err != nil {
		t.Fatal(err)
	}
	autoEnabled, err := sessionCompactInboxEnabled(cfg, "coord-session")
	if err != nil || autoEnabled {
		t.Fatalf("expected compact=false err=nil for auto-settle-only options, got enabled=%v err=%v", autoEnabled, err)
	}
	if err := os.WriteFile(optPath, []byte(validJSON), 0o600); err != nil {
		t.Fatal(err)
	}

	// Group-writable or excluded-harness refuses with command_context/compact_fallback.
	if err := os.Chmod(optPath, 0o660); err != nil {
		t.Fatal(err)
	}
	_, err = sessionCompactInboxEnabled(cfg, "coord-session")
	var refErr *ledger.InboxRefusal
	if !errors.As(err, &refErr) || refErr.Stage != "command_context" || refErr.Reason != "compact_fallback" {
		t.Fatalf("expected command_context/compact_fallback refusal, got %v", err)
	}

	// Exact refusal message parity on invalid timestamp, excluded harness, and invalid batch version.
	badCases := []struct {
		raw     string
		cfgMod  func(*value.Value)
		wantMsg string
	}{
		{
			raw:     `{"binding":"claude-two","compact_inbox_version":1,"native_session_id":"coord-session","schema":"cairn.session-inbox-options/1","updated_at":1` + strings.Repeat("0", 310) + `}`,
			wantMsg: "session inbox options timestamp is invalid",
		},
		{
			raw:     validJSON,
			cfgMod:  func(c *value.Value) { c.Set("harness", value.Text("agy")) },
			wantMsg: "compact_inbox_version 1 requires Codex or Claude native delivery",
		},
		{
			raw:     `{"binding":"claude-two","compact_inbox_version":1,"native_session_id":"coord-session","notice_batch_version":2,"schema":"cairn.session-inbox-options/1"}`,
			wantMsg: "notice_batch_version 1 requires compact Codex or Claude native delivery",
		},
		{
			raw:     `{"binding":"claude-two","native_session_id":"coord-session","notice_auto_settle_version":2,"schema":"cairn.session-inbox-options/1"}`,
			wantMsg: "notice_auto_settle_version 1 requires native_delivery",
		},
	}
	for _, tc := range badCases {
		if err := os.WriteFile(optPath, []byte(tc.raw), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.Chmod(optPath, 0o600); err != nil {
			t.Fatal(err)
		}
		testCfg := clone(cfg)
		if tc.cfgMod != nil {
			tc.cfgMod(testCfg)
		}
		_, err = sessionCompactInboxEnabled(testCfg, "coord-session")
		if !errors.As(err, &refErr) || refErr.Message != tc.wantMsg || refErr.Reason != "compact_fallback" {
			t.Fatalf("expected %q, got %v", tc.wantMsg, err)
		}
	}
}
