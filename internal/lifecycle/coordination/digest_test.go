package coordination

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func TestNoticeDigestSessionOptionsWakeRenderAndSettleLifecycle(t *testing.T) {
	f := stage2Compact(t)
	nativePop(f.config, "compact_inbox_version")
	nativePop(f.config, "notice_batch_version")
	cfgPath := filepath.Join(f.root, "bindings", "fixture.json")
	stage2WriteValue(t, f.p, cfgPath, f.config)

	coordPath, err := f.c.statePath(f.config, stage2Agent)
	if err != nil {
		t.Fatal(err)
	}
	stage2WriteValue(t, f.p, coordPath, f.state)

	// Enable per-session compact + notice-digest with custom 120s window.
	code, cfgRes, stderr := stage2CLIResult(t, f.c, "configure-session-inbox", "--config", cfgPath, "--agent-id", stage2Agent, "--compact-inbox", "--notice-digest", "--digest-window-seconds", "120")
	if code != 0 || stderr != "" || cfgRes.Get("notice_digest_version").Integer != "1" || cfgRes.Get("notice_digest_window_seconds").Integer != "120" {
		t.Fatalf("configure notice-digest failed: code=%d stderr=%q res=%v", code, stderr, cfgRes)
	}

	sref, err := f.c.sessionRef(f.state.Get("agent"))
	if err != nil {
		t.Fatal(err)
	}
	// Watcher uses session-inbox-digest-ready with digest_window_seconds=120.
	var sawDigestReady bool
	f.c.Host.Call = func(_ context.Context, _ *value.Value, op string, body *value.Value, _ NativeCallOptions) (*value.Value, error) {
		if op == "session-inbox-digest-ready" {
			sawDigestReady = body.Get("digest_window_seconds").Integer == "120"
			return nativeMap("delivery_id", stage2Delivery, "event_id", stage2Delivery, "digest", true, "count", 2), nil
		}
		t.Fatalf("unexpected op %s", op)
		return nil, nil
	}
	ready, err := f.c.sessionInboxReadyForWake(context.Background(), f.config, f.state, sref, nativeMap())
	if err != nil || !sawDigestReady || !value.SameText(ready.Get("delivery_id"), stage2Delivery) {
		t.Fatalf("expected digest-ready wake, got ready=%v saw=%v err=%v", ready, sawDigestReady, err)
	}

	// Build a 2-item digest attempt in state and verify RenderActiveAttemptContext + cairn-inbox read/settle.
	deliv1 := nativeClone(f.state.Get("inbox_attempt").Get("delivery"))
	deliv2 := nativeClone(deliv1)
	const deliv2ID = "55555555-5555-4555-8555-555555555555"
	const lease2ID = "66666666-6666-4666-8666-666666666666"
	const rec2ID = "77777777-7777-4777-8777-777777777777"
	deliv2.Set("delivery_id", value.Text(deliv2ID))
	deliv2.Set("lease_id", value.Text(lease2ID))
	deliv2.Get("event").Set("from", value.Text("agent/worker-two"))
	deliv2.Get("event").Set("ref", nativeMap("record_id", rec2ID, "version", 1))

	items := &value.Value{Kind: value.Array, Items: []*value.Value{
		nativeMap("ordinal", 1, "topic_key", "slot/one", "delivery", deliv1),
		nativeMap("ordinal", 2, "topic_key", "slot/two", "delivery", deliv2),
	}}
	att := nativeClone(f.state.Get("inbox_attempt"))
	att.Set("admission_kind", value.Text("digest"))
	att.Set("digest_superseded_count", value.IntegerText("3"))
	att.Set("digest_items", items)
	f.state.Set("inbox_attempt", att)
	_ = os.Remove(filepath.Join(f.root, "state", "fixture", "intents", stage2Attempt+".json"))
	_ = os.Remove(f.index)
	_ = os.Remove(f.contextPath)
	stage2WriteValue(t, f.p, coordPath, f.state)

	body1, body2 := "alpha routine summary", "beta review summary"
	sum1, sum2 := sha256.Sum256([]byte(body1)), sha256.Sum256([]byte(body2))
	itemStates := map[string]string{stage2Delivery: "leased", deliv2ID: "leased"}
	itemCodes := map[string]string{}
	lapsed := false

	f.c.Host.Call = func(_ context.Context, _ *value.Value, op string, req *value.Value, _ NativeCallOptions) (*value.Value, error) {
		switch op {
		case "session-inbox-digest-renew":
			if lapsed {
				return nil, nativeRefusal("STALE_LEASE", "delivery lease expired")
			}
			curItems := nativeClone(items)
			curItems.Items[0].Get("delivery").Set("state", value.Text(itemStates[stage2Delivery]))
			curItems.Items[1].Get("delivery").Set("state", value.Text(itemStates[deliv2ID]))
			if c := itemCodes[deliv2ID]; c != "" {
				curItems.Items[1].Get("delivery").Set("code", value.Text(c))
			}
			curAtt := nativeClone(att)
			curAtt.Set("digest_items", curItems)
			return curAtt, nil
		case "session-inbox-attempt-status":
			stItems := nativeClone(items)
			for i, dID := range []string{stage2Delivery, deliv2ID} {
				d := stItems.Items[i].Get("delivery")
				nativePop(d, "lease_id", "lease_until")
				d.Set("state", value.Text(itemStates[dID]))
				if c := itemCodes[dID]; c != "" {
					d.Set("code", value.Text(c))
				}
			}
			stAtt := nativeClone(att)
			stAtt.Set("digest_items", stItems)
			return stAtt, nil
		case "history":
			recID := nativeText(req.Get("record_id"))
			txt, hexSum := body1, hex.EncodeToString(sum1[:])
			if recID == rec2ID {
				txt, hexSum = body2, hex.EncodeToString(sum2[:])
			}
			ver := nativeMap("version", 1, "payload_available", true, "body", txt, "body_bytes", len(txt), "body_sha256", hexSum)
			return nativeMap("record_id", recID, "historical", true, "versions", &value.Value{Kind: value.Array, Items: []*value.Value{ver}}), nil
		case "session-inbox-digest-settle":
			ocs := req.Get("outcomes").Items
			settled := &value.Value{Kind: value.Array}
			for _, oc := range ocs {
				dID := nativeText(oc.Get("delivery_id"))
				disp := nativeText(oc.Get("disposition"))
				cd := nativeText(oc.Get("code"))
				itemStates[dID] = disp
				itemCodes[dID] = cd
				ord := 1
				if dID == deliv2ID {
					ord = 2
				}
				settled.Items = append(settled.Items, nativeMap("ordinal", ord, "delivery_id", dID, "state", disp, "code", cd))
			}
			rem := 0
			for _, st := range itemStates {
				if st == "leased" {
					rem++
				}
			}
			return nativeMap("attempt_id", att.Get("attempt_id"), "items", settled, "remaining", rem), nil
		case "session-inbox-reconcile":
			for _, st := range itemStates {
				if st == "leased" {
					return nil, nativeRefusal("DELIVERY_ACTIVE", "native digest has unsettled deliveries")
				}
			}
			closed := nativeClone(att)
			closed.Set("finished_at", value.Text("2026-11-19T00:00:00Z"))
			closed.Set("reason", req.Get("reason"))
			return closed, nil
		default:
			t.Fatalf("unexpected op %s", op)
			return nil, nil
		}
	}

	rendered, err := f.c.RenderActiveAttemptContext(context.Background(), f.config, f.state, coordPath)
	if err != nil {
		t.Fatal(err)
	}
	txt := nativeText(rendered)
	if !strings.Contains(txt, "Cairn notice digest (2 item(s), 3 superseded)") || !strings.Contains(txt, body1) || !strings.Contains(txt, body2) {
		t.Fatalf("unexpected rendered digest context: %s", txt)
	}

	ctxVal, err := f.c.read(f.contextPath, SiteCompactContextRead)
	if err != nil {
		t.Fatal(err)
	}
	handle := nativeText(ctxVal.Get("compact_handle"))

	// Read item 2 specifically, settle item 2 as ignored, then settle remaining (item 1) as handled!
	code, read2, _ := stage2CLIResult(t, f.c, "inbox", "compact", "read", "--item", "2", handle)
	if code != 0 || !value.SameText(read2.Get("body"), body2) || read2.Get("ordinal").Integer != "2" {
		t.Fatalf("read --item 2 failed: code=%d res=%v", code, read2)
	}
	code, set2, _ := stage2CLIResult(t, f.c, "inbox", "compact", "settle", "--item", "2", "--code", "unsupported_kind", handle, "ignored")
	if code != 0 || set2.Get("remaining").Integer != "1" {
		t.Fatalf("settle --item 2 failed: code=%d res=%v", code, set2)
	}
	// Journal must NOT be committed yet while item 1 is still unsettled!
	ja := &SessionJournalAdapter{PathText: func(v *value.Value) (string, error) { return f.c.pathText(v) }}
	if comm, err := ja.Committed(f.config, f.state); err != nil || comm {
		t.Fatalf("expected uncommitted journal while 1 digest item remains, got comm=%v err=%v", comm, err)
	}

	// Simulate 90s lease expiry after partial settlement: status overlay keeps item 2 ignored and settles only item 1!
	lapsed = true
	code, readAfterLapse, _ := stage2CLIResult(t, f.c, "inbox", "compact", "read", "--item", "1", handle)
	if code != 0 || !value.SameText(readAfterLapse.Get("body"), body1) {
		t.Fatalf("read after lease lapse failed: code=%d res=%v", code, readAfterLapse)
	}

	// Settle remaining available items as handled -> remaining=0 and journal commits!
	code, setRem, _ := stage2CLIResult(t, f.c, "inbox", "compact", "settle", handle, "handled")
	if code != 0 || setRem.Get("remaining").Integer != "0" || setRem.Get("settled_count").Integer != "1" {
		t.Fatalf("settle remaining failed: code=%d res=%v", code, setRem)
	}
	if comm, err := ja.Committed(f.config, f.state); err != nil || !comm {
		t.Fatalf("expected committed journal once remaining==0, got comm=%v err=%v", comm, err)
	}
	if itemStates[stage2Delivery] != "handled" || itemStates[deliv2ID] != "ignored" {
		t.Fatalf("unexpected final per-item states: %v", itemStates)
	}
}

func TestNoticeDigestOverflowRefsWithinBudgetAndRetryIdempotency(t *testing.T) {
	f := stage2Compact(t)
	nativePop(f.config, "notice_batch_version")
	f.config.Set("harness", value.Text("agy"))
	f.config.Set("notice_digest_version", value.IntegerText("1"))
	f.config.Set("notice_digest_window_seconds", value.IntegerText("300"))
	cfgPath := filepath.Join(f.root, "bindings", "agy-digest.json")
	stage2WriteValue(t, f.p, cfgPath, f.config)
	coordPath, err := f.c.statePath(f.config, stage2Agent)
	if err != nil {
		t.Fatal(err)
	}

	// Simulate an outer CLAUDE_SESSION_ID in env; agy compact CLI must ignore it!
	t.Setenv("CLAUDE_SESSION_ID", "foreign-claude-session")

	// Build 64 digest items so manifest > 4,096 B, plus 6 store overflow items (digest_overflow_count=6).
	baseDeliv := f.state.Get("inbox_attempt").Get("delivery")
	items := &value.Value{Kind: value.Array}
	bodies := map[string]string{}
	for i := 1; i <= 64; i++ {
		d := nativeClone(baseDeliv)
		dID := "10000000-0000-4000-8000-000000000" + string([]byte{byte('0' + i/100), byte('0' + (i/10)%10), byte('0' + i%10)})
		rID := "20000000-0000-4000-8000-000000000" + string([]byte{byte('0' + i/100), byte('0' + (i/10)%10), byte('0' + i%10)})
		d.Set("delivery_id", value.Text(dID))
		d.Get("event").Set("from", value.Text("agent/sender"))
		d.Get("event").Set("ref", nativeMap("record_id", rID, "version", 1))
		items.Items = append(items.Items, nativeMap("ordinal", i, "topic_key", "topic/long-name-padding-"+rID, "delivery", d))
		bodies[rID] = "short note body"
	}
	overflowRefs := &value.Value{Kind: value.Array, Items: []*value.Value{
		nativeMap("delivery_id", "99990001-0000-4000-8000-000000000001", "event_id", "88880001-0000-4000-8000-000000000001", "sender", "agent/overflow-one", "topic_key", "topic/ov1", "record_id", "30000001-0000-4000-8000-000000000001", "version", 1, "kind", "notice"),
		nativeMap("delivery_id", "99990002-0000-4000-8000-000000000002", "event_id", "88880002-0000-4000-8000-000000000002", "sender", "agent/overflow-two", "topic_key", "topic/ov2", "record_id", "30000002-0000-4000-8000-000000000002", "version", 2, "kind", "notice"),
	}}

	att := nativeClone(f.state.Get("inbox_attempt"))
	att.Set("admission_kind", value.Text("digest"))
	att.Set("digest_superseded_count", value.IntegerText("0"))
	att.Set("digest_overflow_count", value.IntegerText("6"))
	att.Set("digest_overflow_refs", overflowRefs)
	att.Set("digest_items", items)
	f.state.Set("inbox_attempt", att)
	_ = os.Remove(filepath.Join(f.root, "state", "fixture", "intents", stage2Attempt+".json"))
	_ = os.Remove(f.index)
	_ = os.Remove(f.contextPath)
	stage2WriteValue(t, f.p, coordPath, f.state)

	allSettled := false
	f.c.Host.Call = func(_ context.Context, _ *value.Value, op string, req *value.Value, _ NativeCallOptions) (*value.Value, error) {
		switch op {
		case "session-inbox-digest-renew":
			return nativeClone(att), nil
		case "history":
			recID := nativeText(req.Get("record_id"))
			txt := bodies[recID]
			sum := sha256.Sum256([]byte(txt))
			ver := nativeMap("version", 1, "payload_available", true, "body", txt, "body_bytes", len(txt), "body_sha256", hex.EncodeToString(sum[:]))
			return nativeMap("record_id", recID, "historical", true, "versions", &value.Value{Kind: value.Array, Items: []*value.Value{ver}}), nil
		case "session-inbox-digest-settle":
			ocs := req.Get("outcomes").Items
			settled := &value.Value{Kind: value.Array}
			for _, oc := range ocs {
				settled.Items = append(settled.Items, nativeMap("ordinal", 1, "delivery_id", nativeText(oc.Get("delivery_id")), "state", nativeText(oc.Get("disposition"))))
			}
			if len(ocs) == 64 {
				allSettled = true
			}
			rem := 64
			if allSettled {
				rem = 0
			}
			return nativeMap("attempt_id", att.Get("attempt_id"), "items", settled, "remaining", rem), nil
		case "session-inbox-reconcile":
			if !allSettled && nativeText(req.Get("reason")) == "delivery_completed" {
				return nil, nativeRefusal("DELIVERY_ACTIVE", "native digest has unsettled deliveries")
			}
			closed := nativeClone(att)
			closed.Set("finished_at", value.Text("2026-11-25T00:00:00Z"))
			closed.Set("reason", req.Get("reason"))
			return closed, nil
		default:
			t.Fatalf("unexpected op %s", op)
			return nil, nil
		}
	}

	rendered, err := f.c.RenderActiveAttemptContext(context.Background(), f.config, f.state, coordPath)
	if err != nil {
		t.Fatal(err)
	}
	txt := nativeText(rendered)
	if len(txt) > 4096 {
		t.Fatalf("rendered digest exceeded 4096 bytes: %d", len(txt))
	}
	if !strings.Contains(txt, "6 deferred") {
		t.Fatalf("expected 6 deferred in summary, got: %s", txt)
	}
	if !strings.Contains(txt, "more digest item(s):") || !strings.Contains(txt, "Deferred overflow (6") || !strings.Contains(txt, "agent/overflow-one") {
		t.Fatalf("expected explicit dropped + deferred overflow refs, got: %s", txt)
	}

	ctxVal, err := f.c.read(f.contextPath, SiteCompactContextRead)
	if err != nil {
		t.Fatal(err)
	}
	handle := nativeText(ctxVal.Get("compact_handle"))
	// Settle item 1 twice idempotently (even with outer CLAUDE_SESSION_ID set), then settle remaining!
	for i := 0; i < 2; i++ {
		code, res, stderr := stage2CLIResult(t, f.c, "inbox", "compact", "settle", "--item", "1", handle, "handled")
		if code != 0 || stderr != "" || res.Get("remaining").Integer != "64" {
			t.Fatalf("settle --item 1 attempt %d failed: code=%d stderr=%q res=%v", i+1, code, stderr, res)
		}
	}
	code, resAll, stderr := stage2CLIResult(t, f.c, "inbox", "compact", "settle", handle, "handled")
	if code != 0 || stderr != "" || resAll.Get("remaining").Integer != "0" {
		t.Fatalf("final settle failed: code=%d stderr=%q res=%v", code, stderr, resAll)
	}
}
