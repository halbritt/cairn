package coordination

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/lifecycle/wake"
	"github.com/halbritt/cairn/internal/procpeer"
)

type testJournalBoundary struct {
	flush     func(context.Context, *value.Value, *value.Value, FlushScope) (bool, error)
	committed func(*value.Value, *value.Value) (bool, error)
	diag      func(*value.Value, *value.Value) error
}

func (b testJournalBoundary) Flush(ctx context.Context, c, s *value.Value, scope FlushScope) (bool, error) {
	return b.flush(ctx, c, s, scope)
}
func (b testJournalBoundary) Committed(c, s *value.Value) (bool, error) { return b.committed(c, s) }
func (b testJournalBoundary) TurnEndDiagnostic(c, s *value.Value) error { return b.diag(c, s) }

func TestDeliveryActiveBackoffPreventsSpinAndResetsOnTurnEnd(t *testing.T) {
	root := t.TempDir()
	cli, _ := newTestHookCLI(t, root)
	statePath := filepath.Join(root, "session.json")
	reconcileCalls := 0
	renewCalls := 0
	cli.Host.Call = func(_ context.Context, _ *value.Value, op string, body *value.Value, _ NativeCallOptions) (*value.Value, error) {
		switch op {
		case "session-inbox-reconcile":
			reconcileCalls++
			if value.SameText(body.Get("reason"), "delivery_completed") {
				return nil, &CoordinationError{Code: "DELIVERY_ACTIVE", Message: "still active"}
			}
			return nativeMap("released", true), nil
		case "event-renew":
			renewCalls++
			return nativeMap("delivery_id", "d1", "lease_id", "l1", "state", "leased", "event", nativeMap("event_id", "e1", "kind", "notice")), nil
		default:
			return nativeMap("ok", true), nil
		}
	}
	cli.Host.Journal = testJournalBoundary{
		flush: func(_ context.Context, _ *value.Value, _ *value.Value, _ FlushScope) (bool, error) {
			return true, nil
		},
		committed: func(_ *value.Value, _ *value.Value) (bool, error) { return false, nil },
		diag:      func(_ *value.Value, _ *value.Value) error { return nil },
	}
	now := time.Unix(1700000000, 0)
	cli.Now = func() time.Time { return now }
	cli.Host.Now = cli.Now

	cfg := nativeMap("binding", "test-binding", "harness", "codex", "repo", "shared")
	sess := nativeMap("agent_id", "11111111-1111-4111-8111-111111111111", "execution_id", "22222222-2222-4222-8222-222222222222")
	state := nativeMap(
		"schema", NativeSessionSchema,
		"agent", sess,
		"inbox_intent", nativeMap("request_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "session", sess),
		"inbox_attempt", nativeMap("attempt_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "session", sess, "delivery", nativeMap("delivery_id", "d1", "lease_id", "l1", "event", nativeMap("event_id", "e1", "kind", "notice"))),
	)
	if err := cli.writeState(statePath, state); err != nil {
		t.Fatal(err)
	}

	// Cycle 1 hits DELIVERY_ACTIVE and sets bounded exponential backoff + jitter.
	if _, err := cli.watchInbox(context.Background(), cfg, state, statePath); err != nil {
		t.Fatal(err)
	}
	if reconcileCalls != 1 {
		t.Fatalf("expected 1 reconcile call on cycle 1, got %d", reconcileCalls)
	}
	backoff := state.Get("inbox_reconcile_backoff")
	if backoff == nil || backoff.Kind != value.Object || backoff.Get("next_eligible_epoch") == nil {
		t.Fatalf("expected inbox_reconcile_backoff set, got %v", backoff)
	}

	// Cycle 2 immediately after: skips session-inbox-reconcile (no 409 spin) and renews lease.
	if _, err := cli.watchInbox(context.Background(), cfg, state, statePath); err != nil {
		t.Fatal(err)
	}
	if reconcileCalls != 1 || renewCalls != 2 {
		t.Fatalf("expected reconcile suppressed (got %d) and renew called twice (got %d)", reconcileCalls, renewCalls)
	}

	// Marking inbox_turn_ended bypasses/clears backoff and reconciles immediately with turn_ended.
	state.Set("inbox_turn_ended", value.Boolean(true))
	if _, err := cli.watchInbox(context.Background(), cfg, state, statePath); err != nil {
		t.Fatal(err)
	}
	if reconcileCalls != 2 {
		t.Fatalf("expected immediate turn_ended reconcile, got %d", reconcileCalls)
	}
	if state.Get("inbox_reconcile_backoff") != nil {
		t.Fatal("expected inbox_reconcile_backoff cleared after release")
	}
}

func TestSubmitIdleWakeAndHerdrRefuseEndingOrRetiredSessions(t *testing.T) {
	root := t.TempDir()
	cli, _ := newTestHookCLI(t, root)
	selfRef, err := procpeer.ProcessRefOf(os.Getpid())
	if err != nil {
		t.Fatal(err)
	}
	statePath := filepath.Join(root, "ending-session.json")
	rawState, _ := json.Marshal(map[string]any{
		"ending": true,
		"agent": map[string]any{
			"agent_id":     "11111111-1111-4111-8111-111111111111",
			"execution_id": "22222222-2222-4222-8222-222222222222",
		},
	})
	if err := os.WriteFile(statePath, rawState, 0o600); err != nil {
		t.Fatal(err)
	}

	// Herdr PrepareTerminalWake refuses without invoking Herdr when StatePath is ending/retired.
	herdrCalled := false
	client := wake.HerdrClient{
		Runner: func(_ context.Context, _ []string, _ []string) ([]byte, []byte, int, error) {
			herdrCalled = true
			return nil, nil, 0, nil
		},
	}
	prepared, refusal, err := client.PrepareTerminalWake(context.Background(), wake.TerminalWakeRequest{
		Harness: "claude", NativeSessionID: "n1", Process: selfRef,
		Session:    wake.SessionRef{AgentID: "11111111-1111-4111-8111-111111111111", ExecutionID: "22222222-2222-4222-8222-222222222222"},
		DeliveryID: "33333333-3333-4333-8333-333333333333", StatePath: statePath,
	}, nil)
	if err != nil || prepared != nil || refusal != nil || herdrCalled {
		t.Fatalf("expected zero wake on ending session: prepared=%v refusal=%v err=%v called=%v", prepared, refusal, err, herdrCalled)
	}

	// submitIdleWake re-reads statePath outside lock and skips wake dispatch for retired/ending.
	submitted := false
	cli.Host.Call = func(_ context.Context, _ *value.Value, _ string, _ *value.Value, _ NativeCallOptions) (*value.Value, error) {
		submitted = true
		return nil, errors.New("unexpected call")
	}
	prepVal := nativeMap(
		"wake", nativeMap(
			"session", nativeMap("agent_id", "11111111-1111-4111-8111-111111111111", "execution_id", "22222222-2222-4222-8222-222222222222"),
			"delivery_id", "33333333-3333-4333-8333-333333333333",
			"transport", "terminal",
		),
		"process", nativeMap("pid", selfRef.PID, "start", int64(selfRef.Start), "boot", selfRef.Boot),
	)
	var errOut bytes.Buffer
	if err := cli.submitIdleWake(context.Background(), nativeMap("binding", "b"), statePath, prepVal, &errOut); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if submitted {
		t.Fatal("submitIdleWake dispatched wake to ending session")
	}
}
