package coordination

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func TestCD1ProseFallbackForUnprimedPeersAndPlainSwitch(t *testing.T) {
	f := stage2Compact(t)
	cd1Body := "cd1 notice cairn T1 done act=info\nobs index rebuilt\nchk unit pass"
	sum := sha256.Sum256([]byte(cd1Body))
	f.c.Host.Call = func(_ context.Context, _ *value.Value, op string, _ *value.Value, _ NativeCallOptions) (*value.Value, error) {
		switch op {
		case "event-renew":
			d := nativeClone(f.delivery)
			d.Set("state", value.Text("leased"))
			return d, nil
		case "history":
			return nativeMap("record_id", stage2Turn, "historical", true, "versions", &value.Value{
				Kind:  value.Array,
				Items: []*value.Value{nativeMap("version", 1, "payload_available", true, "body", cd1Body, "body_bytes", len([]byte(cd1Body)), "body_sha256", hex.EncodeToString(sum[:]))},
			}), nil
		}
		t.Fatalf("unexpected op %s", op)
		return nil, nil
	}

	// Default codex/claude session keeps compact cd1 body untouched.
	code, resCodex, stderr := stage2CLIResult(t, f.c, "inbox", "compact", "read", "abcdef0123456789")
	if code != 0 || stderr != "" {
		t.Fatalf("codex compact read failed: %d %q", code, stderr)
	}
	if nativeText(resCodex.Get("body")) != cd1Body {
		t.Fatalf("expected codex default to preserve compact cd1 body, got:\n%s", nativeText(resCodex.Get("body")))
	}

	// Explicit --plain off-switch renders cd1 to human prose.
	code, resPlain, stderr := stage2CLIResult(t, f.c, "inbox", "compact", "read", "--plain", "abcdef0123456789")
	if code != 0 || stderr != "" {
		t.Fatalf("compact read --plain failed: %d %q", code, stderr)
	}
	plainBody := nativeText(resPlain.Get("body"))
	if strings.HasPrefix(plainBody, "cd1 ") || !strings.Contains(plainBody, "Observation: index rebuilt") || !strings.Contains(plainBody, "status=pass") {
		t.Fatalf("expected --plain prose rendering, got:\n%s", plainBody)
	}

	// Unprimed peers (agy, opencode, hermes) automatically receive prose fallback.
	for _, h := range []string{"agy", "opencode", "hermes"} {
		cfg := nativeClone(f.config)
		cfg.Set("harness", value.Text(h))
		ev := nativeMap("kind", "notice", "action", "none", "event_id", "387bd21d-6a8f-4b40-8835-7be2d2f42539")
		got := f.c.maybeRenderCD1Prose(cfg, ev, cd1Body, false)
		if !strings.Contains(got, "Observation: index rebuilt") {
			t.Fatalf("harness %s expected auto prose fallback, got:\n%s", h, got)
		}
	}

	// L1 delta without local context emits explicit expand fallback + raw payload for unprimed peers.
	l1Body := "cd1 resp cairn T1 verified re=e1\nchk unit pass"
	cfgAgy := nativeClone(f.config)
	cfgAgy.Set("harness", value.Text("agy"))
	ev := nativeMap("kind", "response", "event_id", "387bd21d-6a8f-4b40-8835-7be2d2f42539")
	gotL1 := f.c.maybeRenderCD1Prose(cfgAgy, ev, l1Body, false)
	if !strings.Contains(gotL1, "why=missing_ref") || !strings.Contains(gotL1, l1Body) {
		t.Fatalf("expected expand fallback + raw payload for unprimed L1, got:\n%s", gotL1)
	}
}
