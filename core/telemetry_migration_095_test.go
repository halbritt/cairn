package core

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestMigration095TelemetryAgyReasoningPartialUsageAndReports(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	s, repo := f.receiver, f.repo

	receipts := recallReceipts(t, s, repo, "run-agy-1", "run-agy-2")
	attemptID := uuid.NewString()
	deliveryID := uuid.NewString()
	generatedAt := time.Date(2026, 11, 25, 12, 0, 0, 0, time.UTC)
	reasoning := int64(44)
	partial := true
	dedupSkipped := 3
	nativeTokens := int64(12000)
	nativeMsgs := 6

	recReq := RecallObservationRequest{
		RequestID:           uuid.NewString(),
		Repo:                repo,
		ReceiptIDs:          receipts,
		Harness:             "agy",
		HookEvent:           "UserPromptSubmit",
		Method:              "cairn-lifecycle/recall-meter/1",
		Status:              "timeout",
		ErrorClass:          "HookError",
		ElapsedMS:           i64(900),
		SearchMS:            i64(200),
		SelectorMS:          i64(600),
		PullsMS:             i64(50),
		SearchCalls:         intp(1),
		PullCalls:           intp(1),
		InjectedBytes:       i64(512),
		AttemptID:           attemptID,
		DeliveryID:          deliveryID,
		NativeSessionID:     "agy-native-1",
		ClientGeneratedAt:   &generatedAt,
		InjectedUnit:        "utf8_bytes",
		InjectedTokenizer:   "utf8_byte_len",
		DedupSkippedCount:   &dedupSkipped,
		ExpansionReason:     "shortlist_fit",
		NativeContextTokens: &nativeTokens,
		NativeMessageCount:  &nativeMsgs,
		SelectorCalls: []RecallSelectorCall{{
			Stage: "recall", Outcome: "timeout", ElapsedMS: i64(600),
			Model: "claude-sonnet", ModelSource: "reported",
			InputTokens: i64(300), OutputTokens: i64(20),
			ReasoningTokens: &reasoning, PartialUsage: &partial,
		}},
	}
	if _, err := s.RecordRecallObservation(ctx, recReq); err != nil {
		t.Fatalf("agy recall observation: %v", err)
	}

	// Ordered capture observations allow honest byte and token per-message growth.
	msg1, msg2 := 4, 8
	bytes1, bytes2 := int64(400), int64(1000)
	tok1, tok2 := int64(1000), int64(1800)
	sessHash := strings.Repeat("a", 64)
	for i, step := range []struct {
		event   string
		msgs    int
		bytes   int64
		inTok   int64
		outcome string
	}{
		{"Stop", msg1, bytes1, tok1, "nothing_selected"},
		{"PreCompact", msg2, bytes2, tok2, "saved"},
	} {
		capReq := CaptureObservationRequest{
			RequestID:         uuid.NewString(),
			Repo:              repo,
			Harness:           "agy",
			HookEvent:         step.event,
			Method:            "cairn-lifecycle/capture-meter/2",
			SessionSHA256:     sessHash,
			Outcome:           step.outcome,
			SelectorOutcome:   "completed",
			InputTokens:       &step.inTok,
			ReasoningTokens:   &reasoning,
			PartialUsage:      &partial,
			AttemptID:         attemptID,
			DeliveryID:        deliveryID,
			NativeSessionID:   "agy-native-1",
			ClientGeneratedAt: &generatedAt,
			DialogueMessages:  &step.msgs,
			DialogueBytes:     &step.bytes,
		}
		if _, err := s.RecordCaptureObservation(ctx, capReq); err != nil {
			t.Fatalf("capture %d: %v", i, err)
		}
	}

	delReq := DeliveryObservationRequest{
		RequestID:         uuid.NewString(),
		Repo:              repo,
		Harness:           "agy",
		HookEvent:         "PreInvocation",
		AttemptID:         attemptID,
		DeliveryID:        deliveryID,
		NativeSessionID:   "agy-native-1",
		ClientGeneratedAt: &generatedAt,
		Disposition:       "compact",
		SupersededCount:   2,
		InjectedBytes:     i64(750),
		InjectedUnit:      "utf8_bytes",
		InjectedTokenizer: "utf8_byte_len",
		ExpansionReason:   "inbox_turn_boundary",
	}
	if _, err := s.RecordDeliveryObservation(ctx, delReq); err != nil {
		t.Fatalf("delivery observation: %v", err)
	}
	if _, err := s.RecordDeliveryObservation(ctx, delReq); err != nil {
		t.Fatalf("idempotent delivery replay: %v", err)
	}

	// Register an agy agent and compact it so session longevity metrics are populated.
	agent := f.register(nativeAgent{Binding: "agy-test", Native: "agy-native-1", Harness: "agy", State: "idle"})
	if _, err := s.HeartbeatAgent(ctx, sessionRef(agent), f.dest); err != nil {
		t.Fatal(err)
	}
	if _, err := s.UpdateAgent(ctx, UpdateAgentRequest{
		RequestID: uuid.NewString(), Session: sessionRef(agent), ExpectedRevision: agent.ContextRevision, Metadata: agent.Metadata, Compacted: true,
	}, f.dest); err != nil {
		t.Fatalf("compact update: %v", err)
	}

	use, err := s.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 20})
	if err != nil {
		t.Fatal(err)
	}
	row := recallRow(t, use, receipts[0])
	if row.Recall == nil || row.Recall.NativeSessionID == nil || *row.Recall.NativeSessionID != "agy-native-1" ||
		row.Recall.InjectedUnit == nil || *row.Recall.InjectedUnit != "utf8_bytes" ||
		row.Recall.DedupSkippedCount == nil || *row.Recall.DedupSkippedCount != 3 ||
		row.Recall.SelectorCalls == nil || len(*row.Recall.SelectorCalls) != 1 ||
		(*row.Recall.SelectorCalls)[0].ReasoningTokens == nil || *(*row.Recall.SelectorCalls)[0].ReasoningTokens != 44 ||
		(*row.Recall.SelectorCalls)[0].PartialUsage == nil || !*(*row.Recall.SelectorCalls)[0].PartialUsage {
		t.Fatalf("use-report missing 095 recall telemetry: %+v", row.Recall)
	}

	telemetry, err := s.TelemetryReport(ctx, TelemetryReportRequest{Repo: repo, Harness: "agy", RollupDays: 7})
	if err != nil {
		t.Fatalf("telemetry-report: %v", err)
	}
	if len(telemetry.RecallCounters) != 1 || telemetry.RecallCounters[0].DedupSkippedTotal == nil || *telemetry.RecallCounters[0].DedupSkippedTotal != 3 ||
		telemetry.RecallCounters[0].SelectorReasoningTokens == nil || *telemetry.RecallCounters[0].SelectorReasoningTokens != 44 ||
		telemetry.RecallCounters[0].SelectorPartialUsage == nil || *telemetry.RecallCounters[0].SelectorPartialUsage != 1 {
		t.Fatalf("unexpected recall counters: %+v", telemetry.RecallCounters)
	}
	if len(telemetry.DeliveryCounters) != 1 || telemetry.DeliveryCounters[0].Compact != 1 || telemetry.DeliveryCounters[0].SupersededCountTotal != 2 {
		t.Fatalf("unexpected delivery counters: %+v", telemetry.DeliveryCounters)
	}
	// (1000-400)/(8-4) = 150 bytes/message; (1800-1000)/4 = 200 tokens/message; 512 injected bytes.
	wg := telemetry.PerMessageWindowGrowth
	if wg.MedianDialogueBytesPerStep == nil || *wg.MedianDialogueBytesPerStep != 150 ||
		wg.MedianPromptTokensPerStep == nil || *wg.MedianPromptTokensPerStep != 200 ||
		wg.MedianInjectedBytesPerPrompt == nil || *wg.MedianInjectedBytesPerPrompt != 512 {
		t.Fatalf("unexpected window growth metrics: %+v", wg)
	}
	sl := telemetry.SessionLongevity
	if sl.SessionsObserved != 1 || sl.CompactedSessions != 1 || sl.MedianSecondsBeforeFirstCompaction == nil ||
		sl.MedianTurnsBeforeFirstCompaction == nil || *sl.MedianTurnsBeforeFirstCompaction != 1 {
		t.Fatalf("unexpected session longevity metrics: %+v", sl)
	}
}

func TestStoppedSessionReceivesZeroDeliveriesAtNextEventAndNextOrganicTurn(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	op := testStore(t, Channel{Principal: "operator-" + f.repo, Operator: true})
	if err := op.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
		RequestID:   uuid.NewString(),
		FeatureName: "next-turn-notices/1",
		Status:      "active",
	}); err != nil {
		t.Fatal(err)
	}
	meta := AgentMetadata{State: "idle", DeliveryMode: "existing-session", Harness: "codex", Project: "cairn", Workspace: "/tmp/w"}
	agent, err := f.receiver.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Binding: "stop-boundary", NativeSessionID: "stop-1", Metadata: meta,
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	view, err := f.receiver.ForAgentSession(sessionRef(agent), f.dest)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := view.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: f.repo, Topic: "work.updates", Active: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := f.receiver.LeaveAgent(ctx, sessionRef(agent), f.dest); err != nil {
		t.Fatal(err)
	}
	// Next-organic-turn publication targeting a stopped session is refused at admission.
	exp := time.Now().UTC().Add(5 * time.Minute)
	_, err = f.sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "none", NoticeDelivery: "next_organic_turn", NoticeExpiresAt: &exp,
		Ref:         RecordVersionRef{f.source.RecordID, f.source.Version},
		Destination: EventDestination{Type: "agent", Name: "agent/" + agent.AgentID},
	}, f.dest)
	if err == nil {
		t.Fatal("expected next-organic-turn publish to stopped session to fail")
	}
	requireCode(t, err, "UNSUPPORTED_DELIVERY")

	// Topic publication + manual NextEvent on a stopped session channel must fail with STALE_SESSION (zero delivery).
	if _, err := f.sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "none",
		Ref:         RecordVersionRef{f.source.RecordID, f.source.Version},
		Destination: EventDestination{Type: "topic", Name: "work.updates"},
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	if res, err := view.NextEvent(ctx, NextEventRequest{Repo: f.repo}, f.dest); err == nil || res.Delivery != nil {
		t.Fatalf("expected NextEvent on stopped session to fail with zero delivery, got %+v", res)
	} else {
		requireCode(t, err, "STALE_SESSION")
	}
}
