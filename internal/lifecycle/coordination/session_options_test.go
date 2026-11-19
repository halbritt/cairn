package coordination

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func TestSessionInboxOptionsPerSessionCompactAndBatchOnSharedBinding(t *testing.T) {
	f := stage2Compact(t)
	// Remove binding-wide compact/batch flags so the shared binding itself keeps standard delivery.
	nativePop(f.config, "compact_inbox_version")
	nativePop(f.config, "notice_batch_version")
	cfgPath := filepath.Join(f.root, "bindings", "fixture.json")
	stage2WriteValue(t, f.p, cfgPath, f.config)
	beforeCfg, err := os.ReadFile(cfgPath)
	if err != nil {
		t.Fatal(err)
	}

	// Without per-session opt-in, advance refuses with UNSUPPORTED_CONTROL.
	code, result, _ := stage2CLIResult(t, f.c, "inbox", "compact", "advance", "abcdef0123456789")
	if code != 7 || !value.SameText(result.Get("status"), "UNSUPPORTED_CONTROL") {
		t.Fatalf("expected UNSUPPORTED_CONTROL before opt-in, got code=%d result=%v", code, result)
	}

	// Enable compact + batch for coordinator (stage2Agent) via CLI configure-session-inbox using --agent-id.
	coordPath, err := f.c.statePath(f.config, stage2Agent)
	if err != nil {
		t.Fatal(err)
	}
	stage2WriteValue(t, f.p, coordPath, f.state)

	code, cfgRes, stderr := stage2CLIResult(t, f.c, "configure-session-inbox", "--config", cfgPath, "--agent-id", stage2Agent, "--compact-inbox", "--notice-batch")
	if code != 0 || stderr != "" || !value.SameText(cfgRes.Get("status"), "configured") || cfgRes.Get("compact_inbox_version").Integer != "1" || cfgRes.Get("notice_batch_version").Integer != "1" {
		t.Fatalf("configure-session-inbox failed: code=%d stderr=%q res=%v", code, stderr, cfgRes)
	}

	// Shared binding config bytes must remain 100% untouched.
	afterCfg, err := os.ReadFile(cfgPath)
	if err != nil {
		t.Fatal(err)
	}
	if string(beforeCfg) != string(afterCfg) {
		t.Fatal("shared binding config was modified")
	}

	// Coordinator gets compact + batch; worker on the same binding keeps standard non-compact delivery.
	coordCompact, coordBatch, err := f.c.effectiveSessionInboxOptions(f.config, value.Text(stage2Agent))
	if err != nil || !coordCompact || !coordBatch {
		t.Fatalf("expected coordinator compact+batch, got compact=%v batch=%v err=%v", coordCompact, coordBatch, err)
	}
	workerCompact, workerBatch, err := f.c.effectiveSessionInboxOptions(f.config, value.Text("worker-session-id"))
	if err != nil || workerCompact || workerBatch {
		t.Fatalf("expected worker standard delivery, got compact=%v batch=%v err=%v", workerCompact, workerBatch, err)
	}

	// Coordinator advance now succeeds on the shared binding!
	next := nativeClone(f.state.Get("inbox_attempt"))
	next.Set("attempt_id", value.Text(stage2Execution))
	next.Set("batch_root", value.Text(stage2Attempt))
	next.Set("batch_index", value.IntegerText("2"))
	next.Set("batch_limit", value.IntegerText("3"))
	f.c.Host.Call = func(_ context.Context, _ *value.Value, op string, _ *value.Value, _ NativeCallOptions) (*value.Value, error) {
		if op == "session-inbox-advance" {
			return nativeMap("attempt", next, "remainder", "none"), nil
		}
		t.Fatalf("unexpected call %s", op)
		return nil, nil
	}
	f.c.NextContext = func(context.Context, *value.Value, *value.Value, string, *value.Value) (*value.Value, error) {
		return value.Text("compact advanced notice"), nil
	}
	code, advRes, _ := stage2CLIResult(t, f.c, "inbox", "compact", "advance", "abcdef0123456789")
	if code != 0 || !value.SameText(advRes.Get("next"), "compact advanced notice") || advRes.Get("batch_index").Integer != "2" {
		t.Fatalf("expected successful advance for opted-in session, got code=%d res=%v", code, advRes)
	}
}

func TestSessionInboxOptionsCapabilityAndTamperGuards(t *testing.T) {
	f := stage2Compact(t)
	nativePop(f.config, "compact_inbox_version")
	nativePop(f.config, "notice_batch_version")

	// Excluded harness (agy) refuses per-session compact opt-in with INVALID_CONFIG.
	agyCfg := nativeClone(f.config)
	agyCfg.Set("harness", value.Text("agy"))
	agyCfgPath := filepath.Join(f.root, "bindings", "agy.json")
	stage2WriteValue(t, f.p, agyCfgPath, agyCfg)
	code, _, stderr := stage2CLIResult(t, f.c, "configure-session-inbox", "--config", agyCfgPath, "--native-session-id", "sess-1", "--compact-inbox")
	if code != 1 || stderr == "" {
		t.Fatalf("expected INVALID_CONFIG exit 1 for agy, got code=%d stderr=%q", code, stderr)
	}

	// Group-writable session-options file fails closed with INVALID_HOST.
	cfgPath := filepath.Join(f.root, "bindings", "fixture.json")
	stage2WriteValue(t, f.p, cfgPath, f.config)
	code, cfgRes, _ := stage2CLIResult(t, f.c, "configure-session-inbox", "--config", cfgPath, "--native-session-id", "sess-1", "--compact-inbox")
	if code != 0 {
		t.Fatalf("configure failed: code=%d", code)
	}
	optPath := nativeText(cfgRes.Get("options_path"))
	if err := os.Chmod(optPath, 0o660); err != nil {
		t.Fatal(err)
	}
	if _, _, err := f.c.effectiveSessionInboxOptions(f.config, value.Text("sess-1")); nativeCode(err) != "INVALID_HOST" {
		t.Fatalf("expected INVALID_HOST on group-writable options file, got %v", err)
	}

	// Clearing restores clean standard behavior.
	if err := os.Chmod(optPath, 0o600); err != nil {
		t.Fatal(err)
	}
	code, clrRes, _ := stage2CLIResult(t, f.c, "configure-session-inbox", "--config", cfgPath, "--native-session-id", "sess-1", "--clear-session-options")
	if code != 0 || !value.SameText(clrRes.Get("status"), "cleared") || !nativeNone(clrRes.Get("compact_inbox_version")) {
		t.Fatalf("expected cleared status, got code=%d res=%v", code, clrRes)
	}
}

func TestSessionInboxAutoSettleActionNoneWatcherAndAdvance(t *testing.T) {
	f := stage2Compact(t)
	nativePop(f.config, "compact_inbox_version")
	nativePop(f.config, "notice_batch_version")

	// Auto-settle works on any native_delivery harness (including agy without compact inbox).
	agyCfg := nativeClone(f.config)
	agyCfg.Set("harness", value.Text("agy"))
	agyCfgPath := filepath.Join(f.root, "bindings", "agy-autosettle.json")
	stage2WriteValue(t, f.p, agyCfgPath, agyCfg)
	agyStatePath, err := f.c.statePath(agyCfg, stage2Agent)
	if err != nil {
		t.Fatal(err)
	}
	stage2WriteValue(t, f.p, agyStatePath, f.state)

	code, cfgRes, stderr := stage2CLIResult(t, f.c, "configure-session-inbox", "--config", agyCfgPath, "--agent-id", stage2Agent, "--auto-settle-action-none")
	if code != 0 || stderr != "" || cfgRes.Get("notice_auto_settle_version").Integer != "1" || !nativeNone(cfgRes.Get("compact_inbox_version")) {
		t.Fatalf("configure auto-settle failed: code=%d stderr=%q res=%v", code, stderr, cfgRes)
	}

	sref, err := f.c.sessionRef(f.state.Get("agent"))
	if err != nil {
		t.Fatal(err)
	}
	var ops []string
	f.c.Host.Call = func(_ context.Context, _ *value.Value, op string, body *value.Value, _ NativeCallOptions) (*value.Value, error) {
		ops = append(ops, op)
		switch op {
		case "session-inbox-ready":
			return nativeMap("delivery_id", stage2Delivery, "event_id", stage2Delivery, "action", "none"), nil
		case "session-inbox-settle-none":
			if !nativeEqual(body.Get("session"), sref) {
				t.Fatalf("unexpected settle-none session: %v", body)
			}
			return nativeMap("settled", &value.Value{Kind: value.Array, Items: []*value.Value{value.Text(stage2Delivery)}}, "ready", nativeMap()), nil
		default:
			t.Fatalf("unexpected op %s", op)
			return nil, nil
		}
	}
	cycle := nativeMap()
	ready, err := f.c.sessionInboxReadyForWake(context.Background(), agyCfg, f.state, sref, cycle)
	if err != nil || value.Truth(ready.Get("delivery_id")) || len(ops) != 2 || ops[1] != "session-inbox-settle-none" ||
		string(cycle.Get("auto_settled_deliveries").Items[0].String) != stage2Delivery {
		t.Fatalf("expected idle wake auto-settle without waking session: ready=%v ops=%v cycle=%v err=%v", ready, ops, cycle, err)
	}
}
