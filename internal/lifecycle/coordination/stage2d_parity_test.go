package coordination

import (
	"context"
	"encoding/json"
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
mod.write_state = lambda p, st: None
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
rem = mod.unacknowledged_turn_end_reminder(cfg, state, pathlib.Path(root) / "s.json", {"native_turn_id": "t-1"})
print(json.dumps({"scope": scope, "reminder": rem, "revision": state["inbox_ack_reminder_revision"]["sha256"]}))
`
	cmd := exec.Command("python3", "-I", "-B", "-c", pyInline, coordPy, root, claimID, delivID)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("python corpus failed: %v (%s)", err, string(out))
	}
	var pyRes struct {
		Scope    []string `json:"scope"`
		Reminder string   `json:"reminder"`
		Revision string   `json:"revision"`
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
	m := NewMemoryHost(nil)
	m.Contexts = map[string]*value.Value{
		ctxPath: nativeMap("attempt_id", claimID, "kind", "notice", "acknowledgement", stringArray("/usr/bin/python3", coordPy, "inbox", "--context", ctxPath+" with spaces", "ack")),
	}
	cfg := nativeMap("state_dir", root, "binding", "claude-one", "harness", "claude", "remind_unacknowledged_turn_end", true)
	ns := &NativeSession{Config: cfg, Path: filepath.Join(root, "s.json"), Record: state, Host: &m.Host}
	goRem, err := ns.UnacknowledgedTurnEndReminder(context.Background(), nativeMap("native_turn_id", "t-1"))
	if err != nil || goRem != pyRes.Reminder {
		t.Fatalf("reminder mismatch:\ngo: %q\npy: %q\nerr=%v", goRem, pyRes.Reminder, err)
	}
	revObj := state.Get("inbox_ack_reminder_revision")
	if revObj == nil || !value.SameText(revObj.Get("sha256"), pyRes.Revision) {
		t.Fatalf("revision sha256 mismatch: go=%+v py=%q", revObj, pyRes.Revision)
	}
}
