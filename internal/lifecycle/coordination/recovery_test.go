package coordination

import (
	"context"
	"errors"
	"io"
	"path/filepath"
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

type stage2Evidence func(context.Context, EvidenceKind, EvidenceRequest) (*value.Value, error)

func (f stage2Evidence) Read(ctx context.Context, kind EvidenceKind, r EvidenceRequest) (*value.Value, error) {
	return f(ctx, kind, r)
}
func stage2Engine(t *testing.T, harness string, record *value.Value) (*RecoveryEngine, *MemoryHost, *value.Value) {
	t.Helper()
	h := NewMemoryHost(nil)
	config := nativeMap("binding", "fixture", "harness", harness, "state_dir", t.TempDir(), "repo", "fixture", "inbox_recall_binding", true)
	path := filepath.Join(t.TempDir(), "session.json")
	h.Locate = func(*value.Value, string) (string, error) { return path, nil }
	h.Lock = func(string) (io.Closer, error) { return stage2Closer{func() error { return nil }}, nil }
	h.Exists = func(string) (bool, error) { return true, nil }
	h.Load = func(string) (*value.Value, error) { return nativeClone(record), nil }
	e := &RecoveryEngine{Host: &h.Host, Journal: &SessionJournalAdapter{Projection: stage2Projection{}}, Now: func() time.Time { return time.Unix(105, 0) }, Hooks: RecoveryHooks{Alive: func(*value.Value) (bool, error) { return true, nil }, Daemon: func(*value.Value) (bool, error) { return true, nil }, GrantState: func(context.Context, *value.Value, *value.Value, *value.Value) (string, error) { return "absent", nil }, GrantEvidence: func(context.Context, *value.Value, *value.Value, *value.Value) (string, *value.Value, error) {
		return "absent", nil, nil
	}, PreGrantLedgerAbsent: func(context.Context, *value.Value, *value.Value, *value.Value, *value.Value) (bool, error) {
		return false, nil
	}, ProbeCodex: func(_ context.Context, _ *value.Value, state *value.Value, force bool) (*value.Value, error) {
		if !force {
			t.Fatal("probe not forced")
		}
		health := nativeMap("process_alive", true, "status_type", "idle", "queued_present", false)
		state.Set("native_health", health)
		return health, nil
	}, ObserveTime: func(_ *value.Value, now float64) (*value.Value, *value.Value, error) {
		if now != 105 {
			t.Fatal(now)
		}
		return value.Text("synthetic timestamp"), value.IntegerText("5"), nil
	}}}
	e.Evidence = stage2Evidence(func(_ context.Context, kind EvidenceKind, _ EvidenceRequest) (*value.Value, error) {
		switch kind {
		case EvidenceDaemonWorker:
			return nativeMap("pid", 901, "start", 101, "boot", stage2Agent), nil
		case EvidenceDaemonClaim:
			return nativeMap("last_turn_record_at", "2026-11-09T00:00:00.000Z", "records_after", 0, "transcript_bytes", 100), nil
		case EvidenceCodexLocators:
			return &value.Value{Kind: value.Array, Items: []*value.Value{value.Text("synthetic")}}, nil
		default:
			return nativeMap("schema", "synthetic-evidence", "native_turn_id", stage2Turn), nil
		}
	})
	return e, h, config
}
func stage2Recorded(record *value.Value, turn string) {
	record.Get("inbox_attempt").Set("native_turn_id", value.Text(turn))
	record.Get("inbox_intent").Set("native_turn_id", value.Text(turn))
	record.Set("wake_refusal_diagnostic", nativeMap("phase", "bridge_after_claim", "origin", "hook", "settlement", "held", "attempt_id", stage2Attempt, "delivery_id", stage2Delivery, "native_turn_id", turn, "at", 100))
}
func TestStage2RecoverDaemonStartClaim(t *testing.T) {
	record := stage2State()
	nativePop(record.Get("inbox_attempt"), "native_turn_id")
	nativePop(record.Get("inbox_intent"), "native_turn_id")
	e, h, config := stage2Engine(t, "claude", record)
	r := RecoveryRequest{NativeSessionID: stage2Agent, AttemptID: stage2Attempt, CheckOnly: true}
	out, err := e.RecoverDaemonStartClaim(context.Background(), config, r)
	if err != nil || !value.SameText(out.Get("status"), "eligible") || len(h.Persisted) != 0 {
		t.Fatal(out, err, h.Persisted)
	}
	r.CheckOnly = false
	out, err = e.RecoverDaemonStartClaim(context.Background(), config, r)
	if err != nil || out == nil || len(h.Persisted) == 0 || !nativeEqual(h.Persisted[0].Get("process").Get("pid"), value.IntegerText("901")) {
		t.Fatal(out, err, h.Persisted)
	}
	record.Set("tool_calls", value.Boolean(true))
	e, h, config = stage2Engine(t, "claude", record)
	_, err = e.RecoverDaemonStartClaim(context.Background(), config, r)
	stage2Code(t, err, "INVALID_REQUEST")
	if len(h.Calls) != 0 || len(h.Persisted) != 0 {
		t.Fatal("invalid start claim had effects")
	}
}
func TestStage2RecoverRefusedWake(t *testing.T) {
	record := stage2State()
	turn := "claude-channel:fixture"
	stage2Recorded(record, turn)
	e, h, config := stage2Engine(t, "claude", record)
	r := RecoveryRequest{NativeSessionID: stage2Agent, AttemptID: stage2Attempt, NativeTurnID: turn, CheckOnly: true}
	// The Source recorded/native Claude modes do not honor check_only.
	out, err := e.RecoverRefusedWake(context.Background(), config, r)
	if err != nil || !value.SameText(out.Get("origin"), "operator_recorded") || len(h.Persisted) == 0 {
		t.Fatal(out, err)
	}
	e, h, config = stage2Engine(t, "claude", record)
	out, err = e.RecoverNativeRefusedWake(context.Background(), config, r)
	if err != nil || !value.SameText(out.Get("origin"), "native_record") || len(h.Persisted) == 0 {
		t.Fatal(out, err)
	}
	e, h, config = stage2Engine(t, "claude", record)
	e.Hooks.GrantState = func(context.Context, *value.Value, *value.Value, *value.Value) (string, error) { return "present", nil }
	_, err = e.RecoverRefusedWake(context.Background(), config, r)
	stage2Code(t, err, "INVALID_REQUEST")
	if len(h.Persisted) != 0 {
		t.Fatal("grant-present recovery persisted")
	}
	record.Set("active_prompt", nativeMap("id", turn))
	e, _, config = stage2Engine(t, "claude", record)
	_, err = e.RecoverNativeRefusedWake(context.Background(), config, r)
	stage2Code(t, err, "INVALID_REQUEST")
}
func TestStage2RecoverCodexModes(t *testing.T) {
	record := stage2State()
	stage2Recorded(record, stage2Turn)
	e, h, config := stage2Engine(t, "codex", record)
	r := RecoveryRequest{NativeSessionID: stage2Agent, AttemptID: stage2Attempt, CheckOnly: true}
	out, err := e.RecoverCodexRefusedWake(context.Background(), config, r)
	if err != nil || !value.SameText(out.Get("status"), "eligible") || len(h.Persisted) != 0 {
		t.Fatal(out, err)
	}
	e.Hooks.GrantState = func(context.Context, *value.Value, *value.Value, *value.Value) (string, error) { return "unknown", nil }
	e.Hooks.PreGrantLedgerAbsent = func(context.Context, *value.Value, *value.Value, *value.Value, *value.Value) (bool, error) {
		return true, nil
	}
	out, err = e.RecoverCodexRefusedWake(context.Background(), config, r)
	if err != nil || !value.SameText(out.Get("grant_evidence"), "pre_grant_refusal_without_ledger") {
		t.Fatal(out, err)
	}
	// Automatic recovery does not borrow the explicit-recovery pre-grant fallback.
	s := &NativeSession{config, "synthetic", nativeClone(record), &h.Host}
	ok, err := e.AutoSettleCodexRefusedWake(context.Background(), s)
	if err != nil || ok {
		t.Fatal(ok, err)
	}
	e.Hooks.GrantState = func(context.Context, *value.Value, *value.Value, *value.Value) (string, error) { return "absent", nil }
	e.Evidence = stage2Evidence(func(context.Context, EvidenceKind, EvidenceRequest) (*value.Value, error) {
		return nil, &value.ProfileUnavailable{}
	})
	_, err = e.AutoSettleCodexRefusedWake(context.Background(), s)
	var profile *value.ProfileUnavailable
	if !errors.As(err, &profile) {
		t.Fatal("profile failure hidden", err)
	}
	e, h, config = stage2Engine(t, "codex", record)
	out, err = e.RecoverCodexEndedTurn(context.Background(), config, r)
	if err != nil || !value.SameText(out.Get("status"), "eligible") || len(h.Persisted) != 0 {
		t.Fatal(out, err)
	}
	r.CheckOnly = false
	out, err = e.RecoverCodexEndedTurn(context.Background(), config, r)
	if err != nil || !value.SameText(out.Get("recovery_origin"), "codex_ended_turn") || len(h.Persisted) == 0 {
		t.Fatal(out, err)
	}
	e, h, config = stage2Engine(t, "codex", record)
	e.Hooks.ProbeCodex = func(context.Context, *value.Value, *value.Value, bool) (*value.Value, error) {
		return nativeMap("status_type", "active"), nil
	}
	_, err = e.RecoverCodexEndedTurn(context.Background(), config, r)
	stage2Code(t, err, "INVALID_REQUEST")
	if len(h.Persisted) != 0 {
		t.Fatal("active native thread settled")
	}
}
func stage2QueueState() *value.Value {
	record := stage2State()
	nativePop(record, "inbox_attempt", "inbox_intent")
	ref := nativeMap("agent_id", stage2Agent, "execution_id", stage2Execution)
	record.Set("idle_wake", nativeMap("transport", "codex-queue", "session", ref, "native_id", stage2Agent, "delivery_id", stage2Delivery, "queued_submission_id", stage2Turn, "event_id", stage2Delivery, "status", "queued"))
	record.Set("wake_refusal", value.Text("retained"))
	return record
}
func TestStage2ReconcileAndInspect(t *testing.T) {
	for _, outcome := range []string{"never_admitted", "completed", "cancelled", "admitted_active", "unknown"} {
		t.Run(outcome, func(t *testing.T) {
			record := stage2QueueState()
			e, h, config := stage2Engine(t, "codex", record)
			r := RecoveryRequest{NativeSessionID: stage2Agent, DeliveryID: stage2Delivery, QueuedSubmissionID: stage2Turn, Records: &value.Value{Kind: value.Array, Items: []*value.Value{value.Text("fixture")}}, CheckOnly: true}
			e.Evidence = stage2Evidence(func(context.Context, EvidenceKind, EvidenceRequest) (*value.Value, error) {
				return nativeMap("outcome", outcome), nil
			})
			e.Hooks.ProbeCodex = func(_ context.Context, _ *value.Value, state *value.Value, _ bool) (*value.Value, error) {
				state.Set("probe_extension", value.Boolean(true))
				return nativeMap("process_alive", true, "status_type", "idle", "queued_present", outcome == "admitted_active"), nil
			}
			storeState := "pending"
			if outcome == "completed" {
				storeState = "handled"
			}
			if outcome == "cancelled" {
				storeState = "cancelled"
			}
			h.Responses = map[string]MemoryResponse{"session-inbox-ready": func(*value.Value) (*value.Value, error) {
				return nativeMap("delivery_id", stage2Delivery, "event_id", stage2Delivery), nil
			}, "event-inspect": func(body *value.Value) (*value.Value, error) {
				if !nativeHasKeys(body, "event_id", "limit") {
					t.Fatal("extra event-inspect fields", body)
				}
				return nativeMap("event", nativeMap("repo", "fixture"), "deliveries", &value.Value{Kind: value.Array, Items: []*value.Value{nativeMap("delivery_id", stage2Delivery, "consumer", "agent/"+stage2Agent, "state", storeState, "attempts", 0)}}), nil
			}}
			out, err := e.ReconcileCodexQueueWake(context.Background(), config, r)
			if err != nil || !value.SameText(out.Get("outcome"), outcome) || len(h.Persisted) != 0 {
				t.Fatal(out, err)
			}
			r.CheckOnly = false
			out, err = e.ReconcileCodexQueueWake(context.Background(), config, r)
			if err != nil || len(h.Persisted) != 1 {
				t.Fatal(out, err, h.Persisted)
			}
			saved := h.Persisted[0]
			if saved.Get("probe_extension") != nil {
				t.Fatal("probe clone leaked into state")
			}
			switch outcome {
			case "completed", "cancelled", "never_admitted":
				if saved.Get("idle_wake") != nil || saved.Get("wake_reconciled") == nil {
					t.Fatal(saved)
				}
			case "admitted_active":
				if !value.SameText(saved.Get("idle_wake").Get("status"), "queued") || saved.Get("wake_refusal") != nil {
					t.Fatal(saved)
				}
			case "unknown":
				if !value.SameText(saved.Get("idle_wake").Get("status"), "refused") || saved.Get("wake_reconciliation") == nil {
					t.Fatal(saved)
				}
			}
		})
	}
	record := stage2State()
	stage2Recorded(record, stage2Turn)
	record.Get("wake_refusal_diagnostic").Set("error_class", value.Text("private/path/error"))
	record.Get("wake_refusal_diagnostic").Set("grant", value.Text("present"))
	record.Get("wake_refusal_diagnostic").Set("grant_kind", value.Text("ordinary_context"))
	record.Get("wake_refusal_diagnostic").Set("recall_attempts", value.Boolean(true))
	record.Get("wake_refusal_diagnostic").Set("refusal_stage", value.Text("required_render"))
	record.Get("wake_refusal_diagnostic").Set("refusal_reason", value.Text("required_context"))
	record.Get("wake_refusal_diagnostic").Set("budget_bytes", value.IntegerText("9500"))
	e, h, config := stage2Engine(t, "codex", record)
	obs, err := e.InspectHeldRefusal(context.Background(), config, RecoveryRequest{NativeSessionID: stage2Agent, AttemptID: stage2Attempt})
	if err != nil || !value.SameText(obs.Get("error_class"), "OtherError") || !value.SameText(obs.Get("evidence_state"), "ordinary_grant_present_manual_review") || obs.Get("recall_attempts") != nil || obs.Get("budget_bytes") == nil || len(h.Persisted) != 0 {
		t.Fatal(obs, err)
	}
	_, err = e.VerifyTurnRecovery(context.Background(), config, "unknown", "mode", RecoveryRequest{})
	stage2Code(t, err, "INVALID_REQUEST")
	_, err = e.VerifyTurnRecovery(context.Background(), config, "opencode", "mode", RecoveryRequest{})
	stage2Code(t, err, "INVALID_REQUEST")
}
func TestStage2NativeHealthAndNonAdmissionGuards(t *testing.T) {
	record := stage2QueueState()
	e, h, config := stage2Engine(t, "codex", record)
	out, err := e.CheckCodexNativeHealth(context.Background(), config, stage2Agent)
	if err != nil || out == nil || len(h.Persisted) != 1 || h.Persisted[0].Get("native_health") == nil {
		t.Fatal(out, err)
	}
	e, h, config = stage2Engine(t, "codex", record)
	e.Evidence = stage2Evidence(func(context.Context, EvidenceKind, EvidenceRequest) (*value.Value, error) {
		return nativeMap("outcome", "never_admitted"), nil
	})
	h.Responses = map[string]MemoryResponse{"session-inbox-ready": func(*value.Value) (*value.Value, error) { return nativeMap("delivery_id", stage2Delivery), nil }, "event-inspect": func(*value.Value) (*value.Value, error) {
		return nativeMap("deliveries", &value.Value{Kind: value.Array, Items: []*value.Value{nativeMap("delivery_id", stage2Delivery, "state", "pending", "attempts", false)}}), nil
	}}
	_, err = e.ReconcileCodexQueueWake(context.Background(), config, RecoveryRequest{NativeSessionID: stage2Agent, DeliveryID: stage2Delivery, QueuedSubmissionID: stage2Turn, Outcome: value.Text("never_admitted"), Records: &value.Value{Kind: value.Array, Items: []*value.Value{value.Text("fixture")}}})
	stage2Code(t, err, "INVALID_REQUEST")
	if len(h.Persisted) != 0 {
		t.Fatal("bool store attempts accepted")
	}
}
