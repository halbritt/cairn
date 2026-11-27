package core

import (
	"context"
	"reflect"
	"testing"
	"time"
)

func swarmContextExec(t *testing.T, f *swarmFixture, sql string, args ...any) {
	t.Helper()
	if _, err := f.op.pool.Exec(context.Background(), sql, args...); err != nil {
		t.Fatal(err)
	}
}

func swarmContextRecall(t *testing.T, f *swarmFixture, a AgentInstance, at time.Time, event, method string, deliveryID *string, bytes int64) {
	t.Helper()
	swarmContextExec(t, f, `INSERT INTO cairn.recall_observation
 (observation_id,caller,repo,harness,hook_event,method,status,native_session_id,delivery_id,injected_bytes,injected_unit,observed_at)
 VALUES (gen_random_uuid(),'test',$1,$2,$3,$4,'completed',$5,$6,$7,'utf8_bytes',$8)`,
		f.repo, a.Metadata.Harness, event, method, a.NativeSessionID, deliveryID, bytes, at)
}

func swarmContextDelivery(t *testing.T, f *swarmFixture, a AgentInstance, at time.Time, id, disposition, unit string, bytes *int64, reason string) {
	t.Helper()
	swarmContextExec(t, f, `INSERT INTO cairn.delivery_observation
 (observation_id,caller,repo,harness,hook_event,disposition,native_session_id,delivery_id,injected_bytes,injected_unit,observed_at,expansion_reason)
 VALUES (gen_random_uuid(),'test',$1,$2,'SessionInbox',$3,$4,$5,$6,NULLIF($7,''),$8,NULLIF($9,''))`,
		f.repo, a.Metadata.Harness, disposition, a.NativeSessionID, id, bytes, unit, at, reason)
}

func swarmContextCapture(t *testing.T, f *swarmFixture, a AgentInstance, at time.Time, event string) {
	t.Helper()
	swarmContextExec(t, f, `INSERT INTO cairn.capture_observation
 (observation_id,caller,repo,harness,hook_event,method,session_sha256,outcome,native_session_id,observed_at)
 VALUES (gen_random_uuid(),'test',$1,$2,$3,'cairn-lifecycle/capture-meter/2',repeat('0',64),'empty',$4,$5)`,
		f.repo, a.Metadata.Harness, event, a.NativeSessionID, at)
}

func swarmContextSnapshot(t *testing.T, f *swarmFixture) SwarmContextRoom {
	t.Helper()
	snap, err := f.op.GetSwarmSnapshot(context.Background(), SwarmSnapshotRequest{Repo: f.repo, Campaign: f.campaign})
	if err != nil {
		t.Fatal(err)
	}
	if message := snap.SectionErrors["context_room"]; message != "" {
		t.Fatal(message)
	}
	return snap.ContextRoom
}

func swarmContextRequire[T comparable](t *testing.T, name string, got *T, want T) {
	t.Helper()
	if got == nil {
		t.Fatalf("%s is unknown, want %v", name, want)
	}
	if *got != want {
		t.Fatalf("%s = %v, want %v", name, *got, want)
	}
}

func swarmContextRequireTime(t *testing.T, name string, got *time.Time, want time.Time) {
	t.Helper()
	if got == nil || !got.Equal(want) {
		t.Fatalf("%s = %v, want %v", name, got, want)
	}
}

func TestSwarmContextRoomSinceStartAndCampaignScope(t *testing.T) {
	f := newSwarmFixture(t, "context-room")
	unknown, _ := f.member("missing-counters")
	outsider, _ := f.session("other-campaign")
	start := f.root.StartedAt
	deliveryID := "b11bb289-b780-455f-857e-49e32bfea63b"
	full := int64(30)
	swarmContextRecall(t, f, f.root, start, "SessionStart", "cairn-lifecycle/recall-meter/1", nil, 70)
	swarmContextRecall(t, f, f.root, start.Add(time.Second), "UserPromptSubmit", "cairn-lifecycle/inbox-recall-meter/1", &deliveryID, 20)
	swarmContextDelivery(t, f, f.root, start.Add(2*time.Second), deliveryID, "full", "utf8_bytes", &full, "")
	swarmContextDelivery(t, f, f.root, start.Add(3*time.Second), deliveryID, "full", "utf8_bytes", nil, "")
	swarmContextRecall(t, f, outsider, start.Add(time.Hour), "UserPromptSubmit", "test", nil, 90000)

	// Matching native IDs elsewhere in the collection must also match harness.
	otherHarness := f.root
	otherHarness.Metadata.Harness = "claude"
	swarmContextRecall(t, f, otherHarness, start.Add(time.Hour), "UserPromptSubmit", "test", nil, 90000)
	latest := start.Add(4 * time.Second)
	swarmContextCapture(t, f, f.root, latest, "Stop")

	out := swarmContextSnapshot(t, f)
	entry := out.ByAgent[f.root.AgentID]
	if !entry.Known || entry.Compactions != nil || entry.LastCompactionAt != nil || entry.NearLimit {
		t.Fatalf("unexpected root counters: %+v", entry)
	}
	swarmContextRequireTime(t, "counting since", entry.CountingSince, start)
	swarmContextRequireTime(t, "latest observation", out.ObservedAt, latest)
	swarmContextRequire(t, "injected bytes", entry.InjectedBytes, int64(100))
	swarmContextRequire(t, "deliveries", entry.InjectedDeliveries, 1)
	swarmContextRequire(t, "recalls", entry.InjectedRecalls, 1)
	if entry, ok := out.ByAgent[unknown.AgentID]; !ok || !reflect.DeepEqual(entry, SwarmAgentContext{}) {
		t.Fatalf("session without counters = %+v, present=%t", entry, ok)
	}
	if _, ok := out.ByAgent[outsider.AgentID]; ok || len(out.ByAgent) != 2 {
		t.Fatalf("campaign scope: %+v", out.ByAgent)
	}
}

func TestSwarmContextRoomSinceLastPreCompact(t *testing.T) {
	f := newSwarmFixture(t, "context-room")
	start := f.root.StartedAt
	before, last, after := start.Add(time.Second), start.Add(2*time.Second), start.Add(3*time.Second)
	swarmContextCapture(t, f, f.root, start, "PreCompact")
	swarmContextCapture(t, f, f.root, last, "PreCompact")
	swarmContextRecall(t, f, f.root, before, "SessionStart", "test", nil, 80000)
	swarmContextRecall(t, f, f.root, last, "UserPromptSubmit", "test", nil, 1000)
	swarmContextRecall(t, f, f.root, after, "UserPromptSubmit", "test", nil, 100)
	delivered := int64(50)
	swarmContextDelivery(t, f, f.root, before, "0303dc3c-29e8-448b-91b5-7db31bf4d5c4", "full", "utf8_bytes", &delivered, "")
	swarmContextDelivery(t, f, f.root, after, "1dd83a6b-8a32-4cde-9d9c-614cc7ec5e65", "compact", "utf8_bytes", &delivered, "")

	// The coordinator counter and PreCompact reports can describe the same compactions.
	swarmContextExec(t, f, `UPDATE cairn.agent_session SET compaction_count=2,first_compacted_at=$2 WHERE agent_id=$1`, f.root.AgentID, before)
	out := swarmContextSnapshot(t, f)
	entry := out.ByAgent[f.root.AgentID]
	swarmContextRequire(t, "compactions", entry.Compactions, 2)
	swarmContextRequireTime(t, "last compaction", entry.LastCompactionAt, last)
	swarmContextRequireTime(t, "counting since", entry.CountingSince, last)
	swarmContextRequire(t, "injected bytes", entry.InjectedBytes, int64(150))
	swarmContextRequire(t, "deliveries", entry.InjectedDeliveries, 1)
	swarmContextRequire(t, "recalls", entry.InjectedRecalls, 1)
	if !entry.Known || entry.NearLimit {
		t.Fatalf("unexpected compaction counters: %+v", entry)
	}
}

func TestSwarmContextRoomCoordinatorWithoutPreCompact(t *testing.T) {
	f := newSwarmFixture(t, "context-room")
	worker, _ := f.member("missing-compaction-time")
	earlierMarker, _ := f.member("only-first-compaction-time")
	first := f.root.StartedAt.Add(time.Second)
	swarmContextExec(t, f, `UPDATE cairn.agent_session SET compaction_count=1,first_compacted_at=$2 WHERE agent_id=$1`, f.root.AgentID, first)
	swarmContextExec(t, f, `UPDATE cairn.agent_session SET compaction_count=2,first_compacted_at=$2 WHERE agent_id=$1`, worker.AgentID, first)
	swarmContextExec(t, f, `UPDATE cairn.agent_session SET compaction_count=2,first_compacted_at=$2 WHERE agent_id=$1`, earlierMarker.AgentID, earlierMarker.StartedAt.Add(time.Second))
	swarmContextCapture(t, f, earlierMarker, earlierMarker.StartedAt, "PreCompact")
	swarmContextRecall(t, f, earlierMarker, earlierMarker.StartedAt.Add(2*time.Second), "SessionStart", "test", nil, 5)
	swarmContextRecall(t, f, f.root, first.Add(time.Second), "SessionStart", "test", nil, 3)
	swarmContextRecall(t, f, worker, first.Add(time.Second), "SessionStart", "test", nil, 4)

	room := swarmContextSnapshot(t, f)
	root := room.ByAgent[f.root.AgentID]
	swarmContextRequire(t, "compactions", root.Compactions, 1)
	swarmContextRequireTime(t, "last compaction", root.LastCompactionAt, first)
	swarmContextRequireTime(t, "counting since", root.CountingSince, first)
	swarmContextRequire(t, "injected bytes", root.InjectedBytes, int64(3))
	for _, a := range []AgentInstance{worker, earlierMarker} {
		entry := room.ByAgent[a.AgentID]
		swarmContextRequire(t, "compactions", entry.Compactions, 2)
		if !entry.Known || entry.LastCompactionAt != nil || entry.CountingSince != nil || entry.InjectedBytes != nil ||
			entry.InjectedDeliveries != nil || entry.InjectedRecalls != nil {
			t.Fatalf("first of several reported compactions cannot start the interval: %+v", entry)
		}
	}
}

func TestSwarmContextRoomCoverageAndThreshold(t *testing.T) {
	f := newSwarmFixture(t, "context-room")
	over, _ := f.member("at-threshold")
	under, _ := f.member("under-threshold")
	noBytes, _ := f.member("unmeasured")
	zero, _ := f.member("measured-zero")
	unlinked, _ := f.member("unlinked-inbox")
	const threshold = int64(swarmContextNearLimitKiB * 1024)
	swarmContextRecall(t, f, over, over.StartedAt, "UserPromptSubmit", "test", nil, threshold)
	swarmContextRecall(t, f, under, under.StartedAt, "UserPromptSubmit", "test", nil, threshold-1)
	swarmContextRecall(t, f, zero, zero.StartedAt, "UserPromptSubmit", "test", nil, 0)
	swarmContextRecall(t, f, unlinked, unlinked.StartedAt, "UserPromptSubmit", "cairn-lifecycle/inbox-recall-meter/1", nil, 90)
	otherUnit := threshold
	swarmContextDelivery(t, f, under, under.StartedAt, "491ecb70-4ca7-4999-aa95-44745fa44726", "full", "chars", &otherUnit, "")
	swarmContextDelivery(t, f, noBytes, noBytes.StartedAt, "58da957e-c987-47da-8339-491a30bb3511", "digest", "", nil, "digest_claim")
	swarmContextDelivery(t, f, noBytes, noBytes.StartedAt, "f10a19b7-d058-4c42-858a-34f848a2cf87", "auto_settled", "", nil, "")

	room := swarmContextSnapshot(t, f)
	if room.NearLimitRule == "" {
		t.Fatal("empty near-limit rule")
	}
	if entry := room.ByAgent[over.AgentID]; !entry.NearLimit || len(entry.Reasons) != 1 {
		t.Fatalf("at threshold: %+v", entry)
	}
	if entry := room.ByAgent[under.AgentID]; entry.NearLimit || len(entry.Reasons) != 0 {
		t.Fatalf("below threshold: %+v", entry)
	}
	swarmContextRequire(t, "below threshold bytes", room.ByAgent[under.AgentID].InjectedBytes, threshold-1)
	entry := room.ByAgent[noBytes.AgentID]
	if !entry.Known || entry.InjectedBytes != nil || entry.InjectedDeliveries != nil || entry.InjectedRecalls != nil {
		t.Fatalf("unmeasured output from claim/auto-settle: %+v", entry)
	}
	swarmContextRequire(t, "measured zero bytes", room.ByAgent[zero.AgentID].InjectedBytes, int64(0))
	swarmContextRequire(t, "measured zero recalls", room.ByAgent[zero.AgentID].InjectedRecalls, 0)
	entry = room.ByAgent[unlinked.AgentID]
	swarmContextRequire(t, "unlinked inbox bytes", entry.InjectedBytes, int64(90))
	if entry.InjectedDeliveries != nil || entry.InjectedRecalls != nil {
		t.Fatalf("unlinked inbox output has bytes but no known delivery or ordinary recall count: %+v", entry)
	}
}

func TestSwarmContextRoomReadOnly(t *testing.T) {
	f := newSwarmFixture(t, "context-room")
	ctx := context.Background()
	start := f.root.StartedAt
	swarmContextCapture(t, f, f.root, start, "PreCompact")
	swarmContextRecall(t, f, f.root, start.Add(time.Second), "SessionStart", "test", nil, 70)
	swarmContextDelivery(t, f, f.root, start.Add(2*time.Second), "0303dc3c-29e8-448b-91b5-7db31bf4d5c4", "full", "", nil, "")

	state := func() string {
		t.Helper()
		var contents string
		err := f.op.pool.QueryRow(ctx, `SELECT jsonb_build_array(
 (SELECT jsonb_agg(to_jsonb(a) ORDER BY agent_id) FROM cairn.agent_session a WHERE repo=$1),
 (SELECT jsonb_agg(to_jsonb(r) ORDER BY sequence) FROM cairn.recall_observation r WHERE repo=$1),
 (SELECT jsonb_agg(to_jsonb(d) ORDER BY sequence) FROM cairn.delivery_observation d WHERE repo=$1),
 (SELECT jsonb_agg(to_jsonb(c) ORDER BY sequence) FROM cairn.capture_observation c WHERE repo=$1)
)::text`, f.repo).Scan(&contents)
		if err != nil {
			t.Fatal(err)
		}
		return contents
	}
	before := state()
	tx, err := f.op.beginSwarmRead(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	var mode, isolation string
	if err := tx.QueryRow(ctx, `SELECT current_setting('transaction_read_only'),current_setting('transaction_isolation')`).Scan(&mode, &isolation); err != nil {
		t.Fatal(err)
	}
	if mode != "on" || isolation != "repeatable read" {
		t.Fatalf("swarm transaction: read-only=%s isolation=%s", mode, isolation)
	}
	sc, _, _, err := f.op.swarmScopeTx(ctx, tx, f.repo, f.campaign)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.op.swarmContextRoomTx(ctx, tx, sc); err != nil {
		t.Fatal(err)
	}
	if err := tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	swarmContextSnapshot(t, f)
	if after := state(); after != before {
		t.Fatal("read changed stored session or observation rows")
	}
}
