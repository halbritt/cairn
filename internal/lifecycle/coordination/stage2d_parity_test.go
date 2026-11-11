package coordination

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func TestStage2DNaNLosslessProjectionAndFlush(t *testing.T) {
	dir := t.TempDir()
	nanVal := &value.Value{Kind: value.Float, Number: math.NaN()}
	record := nativeMap(
		"agent", nativeMap("agent_id", "ag-nan", "execution_id", nanVal),
		"inbox_attempt", nativeMap(
			"attempt_id", "att-nan",
			"session", nativeMap("agent_id", "ag-nan", "execution_id", &value.Value{Kind: value.Float, Number: math.NaN()}),
		),
	)
	jPath := filepath.Join(dir, "intents", "att-nan.json")
	if err := os.MkdirAll(filepath.Dir(jPath), 0o700); err != nil {
		t.Fatal(err)
	}
	jBody := `{"attempt_id":"att-nan","session":{"agent_id":"ag-nan","execution_id":NaN},"kind":"notice","completion":{"kind":"ack","argv":["cairn","ack","d1"],"input":"","request_id":"r1","status":"pending"}}`
	if err := os.WriteFile(jPath, []byte(jBody), 0o600); err != nil {
		t.Fatal(err)
	}

	var calls int
	adapter := &SessionJournalAdapter{
		Options: journal.Options{
			Runner: journal.RunnerFunc(func(context.Context, []string, string) (journal.Result, error) {
				calls++
				return journal.Result{ExitCode: 0, Stdout: `{"ok":true}`}, nil
			}),
		},
	}
	cfg := nativeMap("state_dir", dir, "binding", "claude-one")
	settled, err := adapter.FlushInboxIntents(context.Background(), cfg, record, FlushAttempt)
	if err != nil || !settled || calls != 1 {
		t.Fatalf("FlushInboxIntents with NaN container session: settled=%v calls=%d err=%v", settled, calls, err)
	}
	journals := record.Get("inbox_journals")
	if journals == nil || journals.Kind != value.Array || len(journals.Items) != 1 || !value.SameText(journals.Items[0], "att-nan") {
		t.Fatalf("expected active attempt att-nan in inbox_journals, got %+v", journals)
	}
}

func TestStage2DCompletionScopeSettleAndReminder(t *testing.T) {
	claimID := "301e246e-a3b7-498b-b6a7-3d84e592097f"
	delivID := "5e02aa76-2451-47c2-bd89-2eab763ffa46"
	sessRef := nativeMap("agent_id", "ag-1", "execution_id", "ex-1")
	attempt := nativeMap(
		"attempt_id", claimID,
		"session", sessRef,
		"delivery", nativeMap("delivery_id", delivID, "lease_id", "lease-1", "event", nativeMap("kind", "request", "event_id", "ev-1", "from", "agent/x", "ref", nativeMap("record_id", "r-1", "version", 1))),
	)
	state := nativeMap(
		"agent", nativeMap("agent_id", "ag-1", "execution_id", "ex-1"),
		"inbox_intent", nativeMap("request_id", claimID, "session", sessRef),
		"inbox_attempt", attempt,
	)
	if scope, err := CompletionScope(state, attempt); err != nil || len(scope) != 0 {
		t.Fatalf("legacy absent policy: scope=%v err=%v", scope, err)
	}
	state.Set("inbox_result_policy", nativeMap("claim_request_id", claimID, "version", 1))
	scope, err := CompletionScope(state, attempt)
	if err != nil || !reflect.DeepEqual(scope, []string{"--task", "coordination-result/" + delivID, "--run", "*"}) {
		t.Fatalf("valid policy: scope=%v err=%v", scope, err)
	}
	state.Set("inbox_result_policy", nativeMap("claim_request_id", claimID, "version", true))
	if _, err := CompletionScope(state, attempt); nativeCode(err) != "INVALID_HOST" {
		t.Fatalf("bool version must fail INVALID_HOST, got %v", err)
	}

	dir := t.TempDir()
	cfg := nativeMap("state_dir", dir, "binding", "claude-one", "harness", "claude", "remind_unacknowledged_turn_end", true)
	m := NewMemoryHost(map[string]MemoryResponse{
		"event-renew": func(*value.Value) (*value.Value, error) {
			return attempt.Get("delivery"), nil
		},
		"session-inbox-reconcile": func(*value.Value) (*value.Value, error) {
			return nativeMap("ok", true), nil
		},
	})
	ctxPath := filepath.Join(dir, "inbox", claimID+".json")
	m.Contexts = map[string]*value.Value{
		ctxPath: nativeMap(
			"attempt_id", claimID,
			"kind", "request",
			"completion", stringArray("/usr/bin/python3", "/work/coordination.py", "inbox", "--context", ctxPath, "complete"),
			"response", stringArray("/usr/bin/python3", "/work/coordination.py", "inbox", "--context", ctxPath, "respond"),
		),
	}
	ns := &NativeSession{Config: cfg, Path: filepath.Join(dir, "s.json"), Record: state, Host: &m.Host}
	obs := nativeMap("native_turn_id", "turn-99")
	rem, err := ns.UnacknowledgedTurnEndReminder(context.Background(), obs)
	if err != nil || !strings.Contains(rem, "has no journaled completion yet") || !strings.Contains(rem, "--version RESULT_VERSION RESULT_RECORD_UUID") {
		t.Fatalf("unexpected reminder: %q err=%v", rem, err)
	}
	if rem2, err := ns.UnacknowledgedTurnEndReminder(context.Background(), obs); err != nil || rem2 != "" {
		t.Fatalf("expected deduplicated empty reminder on repeat, got %q err=%v", rem2, err)
	}
	if settled, err := ns.SettleInbox(context.Background()); err != nil || !settled {
		t.Fatalf("SettleInbox failed: settled=%v err=%v", settled, err)
	}
}

func TestStage2DClassAPythonGoCoordinationCorpus(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	t.Setenv("CAIRN_INBOX_ROOT", "")
	coordPy, err := filepath.Abs("../../../integrations/lifecycle/coordination.py")
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	claimID := "301e246e-a3b7-498b-b6a7-3d84e592097f"
	delivID := "5e02aa76-2451-47c2-bd89-2eab763ffa46"
	inboxDir := filepath.Join(root, "inbox")
	if err := os.MkdirAll(inboxDir, 0o700); err != nil {
		t.Fatal(err)
	}
	ctxPath := filepath.Join(inboxDir, claimID+".json")
	ctxBody := map[string]any{
		"attempt_id": claimID,
		"kind":       "notice",
		"acknowledgement": []any{
			"/usr/bin/python3", coordPy, "inbox", "--context", ctxPath + " with spaces", "ack",
		},
	}
	rawCtx, err := json.Marshal(ctxBody)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(ctxPath, rawCtx, 0o600); err != nil {
		t.Fatal(err)
	}

	pyInline := `
import importlib.util, json, pathlib, sys
spec = importlib.util.spec_from_file_location("coord", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.renew_inbox = lambda cfg, st, p, att: att["delivery"]
root, claim_id, deliv_id = sys.argv[2], sys.argv[3], sys.argv[4]
sess = {"agent_id": "ag-1", "execution_id": "ex-1"}
att = {"attempt_id": claim_id, "session": sess, "delivery": {"delivery_id": deliv_id, "lease_id": "l-1", "event": {"kind": "notice"}}}
state = {
    "agent": sess,
    "inbox_intent": {"request_id": claim_id, "session": sess},
    "inbox_attempt": att,
    "inbox_result_policy": {"claim_request_id": claim_id, "version": 1},
}
cfg = {"state_dir": root, "binding": "claude-one", "harness": "claude", "remind_unacknowledged_turn_end": True}
scope = mod.completion_scope(state, att)
rem = mod.unacknowledged_turn_end_reminder(cfg, state, pathlib.Path(root) / "s-py.json", {"native_turn_id": "t-1"})

fb_id = "401e246e-a3b7-498b-b6a7-3d84e592097f"
(pathlib.Path(root) / "inbox" / (fb_id + ".json")).write_text(json.dumps({
    "attempt_id": fb_id,
    "kind": "\ud800",
    "compact_source_unavailable": True,
    "compact_fallback_control": "Run the fallback settlement.",
}, ensure_ascii=True))
fb_att = {"attempt_id": fb_id, "session": sess, "delivery": {"delivery_id": deliv_id, "lease_id": "l-1", "event": {}}}
fb_state = {"agent": sess, "inbox_intent": {"request_id": fb_id, "session": sess}, "inbox_attempt": fb_att}
fb_renews = 0
def count_renew(cfg, st, p, a):
    global fb_renews
    fb_renews += 1
    return a["delivery"]
mod.renew_inbox = count_renew
fb_rem = mod.unacknowledged_turn_end_reminder(cfg, fb_state, pathlib.Path(root) / "s-fb-py.json", {"native_turn_id": "t-1"})
print(json.dumps({"scope": scope, "reminder": rem, "revision": state["inbox_ack_reminder_revision"]["sha256"], "fb_reminder": fb_rem, "fb_renews": fb_renews}))
`
	cmd := exec.Command("python3", "-I", "-B", "-c", pyInline, coordPy, root, claimID, delivID)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("python corpus failed: %v (%s)", err, string(out))
	}
	var pyRes struct {
		Scope      []string `json:"scope"`
		Reminder   string   `json:"reminder"`
		Revision   string   `json:"revision"`
		FBReminder string   `json:"fb_reminder"`
		FBRenews   int      `json:"fb_renews"`
	}
	if err := json.Unmarshal(out, &pyRes); err != nil {
		t.Fatal(err)
	}

	sessRef := nativeMap("agent_id", "ag-1", "execution_id", "ex-1")
	att := nativeMap("attempt_id", claimID, "session", sessRef, "delivery", nativeMap("delivery_id", delivID, "lease_id", "l-1", "event", nativeMap("kind", "notice")))
	state := nativeMap("agent", sessRef, "inbox_intent", nativeMap("request_id", claimID, "session", sessRef), "inbox_attempt", att, "inbox_result_policy", nativeMap("claim_request_id", claimID, "version", 1))
	goScope, err := CompletionScope(state, att)
	if err != nil || !reflect.DeepEqual(goScope, pyRes.Scope) {
		t.Fatalf("scope mismatch: go=%v py=%v err=%v", goScope, pyRes.Scope, err)
	}
	fbRenewsGo := 0
	host := NewFileHost(FileHostOptions{
		Profile: stage2ServicesProfile(),
		Log:     func(string) error { return nil },
		Call: func(_ context.Context, _ *value.Value, op string, _ *value.Value, _ NativeCallOptions) (*value.Value, error) {
			if op == "event-renew" {
				fbRenewsGo++
				return att.Get("delivery"), nil
			}
			return nativeMap("ok", true), nil
		},
	})
	cfg := nativeMap("state_dir", root, "binding", "claude-one", "harness", "claude", "remind_unacknowledged_turn_end", true)
	ns := &NativeSession{Config: cfg, Path: filepath.Join(root, "s-go.json"), Record: state, Host: host}
	goRem, err := ns.UnacknowledgedTurnEndReminder(context.Background(), nativeMap("native_turn_id", "t-1"))
	if err != nil || goRem != pyRes.Reminder {
		t.Fatalf("reminder mismatch:\ngo: %q\npy: %q\nerr=%v", goRem, pyRes.Reminder, err)
	}
	revObj := state.Get("inbox_ack_reminder_revision")
	if revObj == nil || !value.SameText(revObj.Get("sha256"), pyRes.Revision) {
		t.Fatalf("revision sha256 mismatch: go=%+v py=%q", revObj, pyRes.Revision)
	}
	pyStateBytes, err := os.ReadFile(filepath.Join(root, "s-py.json"))
	if err != nil {
		t.Fatal(err)
	}
	goStateBytes, err := os.ReadFile(filepath.Join(root, "s-go.json"))
	if err != nil {
		t.Fatal(err)
	}
	var pySaved, goSaved any
	if err := json.Unmarshal(pyStateBytes, &pySaved); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(goStateBytes, &goSaved); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(pySaved, goSaved) {
		t.Fatalf("saved state mismatch:\npy=%s\ngo=%s", string(pyStateBytes), string(goStateBytes))
	}

	// Verify fallback reminder ignores surrogate kind, matching Python (J-R1 F2).
	fbID := "401e246e-a3b7-498b-b6a7-3d84e592097f"
	fbAtt := nativeMap("attempt_id", fbID, "session", sessRef, "delivery", nativeMap("delivery_id", delivID, "lease_id", "l-1", "event", nativeMap()))
	fbState := nativeMap("agent", sessRef, "inbox_intent", nativeMap("request_id", fbID, "session", sessRef), "inbox_attempt", fbAtt)
	fbRenewsGo = 0
	fbNS := &NativeSession{Config: cfg, Path: filepath.Join(root, "s-fb-go.json"), Record: fbState, Host: host}
	fbRemGo, err := fbNS.UnacknowledgedTurnEndReminder(context.Background(), nativeMap("native_turn_id", "t-1"))
	if err != nil || fbRemGo != pyRes.FBReminder || fbRenewsGo != pyRes.FBRenews {
		t.Fatalf("fallback surrogate kind mismatch: go=(%q, renews=%d, err=%v) py=(%q, renews=%d)", fbRemGo, fbRenewsGo, err, pyRes.FBReminder, pyRes.FBRenews)
	}

	// Verify lone surrogates fail with UnicodeEncodeError before renewHost (J-R2 E3).
	renewCalls := 0
	surrHost := NewMemoryHost(map[string]MemoryResponse{
		"event-renew": func(*value.Value) (*value.Value, error) {
			renewCalls++
			return att.Get("delivery"), nil
		},
	})
	surrCmd := &value.Value{Kind: value.Array, Items: []*value.Value{
		value.Text("/usr/bin/python3"),
		{Kind: value.String, String: []rune{'a', 0xd800, 'b'}},
	}}
	surrHost.Contexts = map[string]*value.Value{
		ctxPath: nativeMap("attempt_id", claimID, "kind", "notice", "acknowledgement", surrCmd),
	}
	surrState := nativeMap("agent", sessRef, "inbox_intent", nativeMap("request_id", claimID, "session", sessRef), "inbox_attempt", att)
	surrNS := &NativeSession{Config: cfg, Path: filepath.Join(root, "s-surr.json"), Record: surrState, Host: &surrHost.Host}
	_, surrErr := surrNS.UnacknowledgedTurnEndReminder(context.Background(), nativeMap("native_turn_id", "t-1"))
	if value.ErrorClass(surrErr) != "UnicodeEncodeError" || renewCalls != 0 {
		t.Fatalf("expected UnicodeEncodeError before renewHost, got err=%v renewCalls=%d", surrErr, renewCalls)
	}
}

type testLockCloser struct{ closed *int }

func (c testLockCloser) Close() error { *c.closed++; return nil }

func TestOpenSessionReleasesLockOnLoadError(t *testing.T) {
	closed := 0
	wantErr := &value.Error{Class: "OSError", Message: "load failed"}
	host := &Host{
		Locate: func(*value.Value, string) (string, error) { return "/tmp/s.json", nil },
		Lock:   func(string) (io.Closer, error) { return testLockCloser{&closed}, nil },
		Load:   func(string) (*value.Value, error) { return nil, wantErr },
	}
	sess, release, err := OpenSession(value.Mapping(), "n1", host, false)
	if sess != nil || release != nil || err != wantErr || closed != 1 {
		t.Fatalf("OpenSession load error: sess=%v hasRelease=%t err=%v closed=%d", sess, release != nil, err, closed)
	}
}

func TestJournalFloatPythonTimestampParity(t *testing.T) {
	pyBin, err := exec.LookPath("python3")
	if err != nil {
		t.Fatalf("python3 not found: %v", err)
	}
	samples := []float64{
		0.0,
		math.Copysign(0.0, -1),
		1e-5,
		-1e-5,
		9.999999999999999e-5,
		1e-4,
		-1e-4,
		0.000123456789,
		999999.5,
		1e6,
		-1e6,
		1700000000.0,
		1794345125.6512349,
		1794345125.7548075,
		1794345207.9413965,
		1794345208.1857958,
		1.23e14,
		1.1e15,
		9999999999999998.0,
		1e16,
		-1e16,
		math.NaN(),
		math.Inf(1),
		math.Inf(-1),
	}
	for _, x := range samples {
		got := string(journalFloat(x))
		hexBits := fmt.Sprintf("%016x", math.Float64bits(x))
		cmd := exec.Command(pyBin, "-I", "-B", "-c",
			"import json, struct, sys; x = struct.unpack('>d', bytes.fromhex(sys.argv[1]))[0]; sys.stdout.write(json.dumps(x))",
			hexBits,
		)
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("python3 json.dumps(%s): %v (%s)", hexBits, err, string(out))
		}
		want := string(out)
		if got != want {
			t.Fatalf("journalFloat(%v) (bits %s): got %q, want Python %q", x, hexBits, got, want)
		}
	}
}
