package core

import (
	"context"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestInboxCursorFindsPoolAssignedAfterNewerPublication(t *testing.T) {
	ctx := context.Background()
	_, a, b, r, d, worker := poolFixture(t)
	pooled := poolPublish(t, a, r, d, PoolRequirements{Workspace: worker.Spec.Workspace})
	notice, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "response", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"agent", b.channel.Principal}}, d)
	if err != nil {
		t.Fatal(err)
	}
	first, err := b.InboxEvents(ctx, InboxWatchRequest{}, d)
	if err != nil || len(first.Deliveries) != 1 || first.Deliveries[0].Event.EventID != notice.EventID {
		t.Fatalf("first page: %+v %v", first, err)
	}
	claim := poolClaim(t, b, worker, d)
	if claim == nil || claim.Delivery.Event.EventID != pooled.EventID {
		t.Fatal("pool assignment missing")
	}
	next, err := b.InboxEvents(ctx, InboxWatchRequest{Cursor: first.Cursor}, d)
	if err != nil || len(next.Deliveries) != 1 || next.Deliveries[0].Event.EventID != pooled.EventID {
		t.Fatalf("late assignment lost: %+v %v", next, err)
	}
	if next.Deliveries[0].Position <= first.Deliveries[0].Position || next.Deliveries[0].Event.Position >= notice.Position {
		t.Fatal("fixture must distinguish delivery and publication order")
	}
	empty, err := b.InboxEvents(ctx, InboxWatchRequest{Cursor: next.Cursor}, d)
	if err != nil || len(empty.Deliveries) != 0 || empty.Cursor != next.Cursor {
		t.Fatalf("resume repeated work: %+v %v", empty, err)
	}
}

func TestInboxCursorPagesPrivacyAndRestore(t *testing.T) {
	ctx := context.Background()
	a, b, r, d := eventFixture(t)
	privateDraft := projectNote(r.Scope.Repo)
	privateDraft.Sensitivity = "local"
	private, err := a.Create(ctx, CreateRequest{uuid.NewString(), privateDraft})
	if err != nil {
		t.Fatal(err)
	}
	publishFixture(t, a, b, private, Destination{"local", true})
	var ids []string
	for range 3 {
		ids = append(ids, publishFixture(t, a, b, r, d).EventID)
	}
	first, err := b.InboxEvents(ctx, InboxWatchRequest{Limit: 2}, d)
	if err != nil || !first.More || len(first.Deliveries) != 2 {
		t.Fatalf("page: %+v %v", first, err)
	}
	for i, v := range first.Deliveries {
		if v.Event.EventID != ids[i] || v.State != "pending" {
			t.Fatalf("wrong visible arrival %+v", v)
		}
	}
	next, err := b.InboxEvents(ctx, InboxWatchRequest{Cursor: first.Cursor, Limit: 2}, d)
	if err != nil || next.More || len(next.Deliveries) != 1 || next.Deliveries[0].Event.EventID != ids[2] {
		t.Fatalf("next: %+v %v", next, err)
	}
	// Watching neither claims a lease nor hides already observed work from inbox next.
	claim := nextFixture(t, b, d)
	if claim.Event.EventID != ids[0] || claim.Attempts != 1 {
		t.Fatalf("watch changed claim state: %+v", claim)
	}
	for _, tc := range []struct {
		store *Store
		req   InboxWatchRequest
		dest  Destination
	}{
		{a, InboxWatchRequest{Cursor: first.Cursor}, d},
		{b, InboxWatchRequest{Cursor: first.Cursor, Topic: "other"}, d},
		{b, InboxWatchRequest{Cursor: first.Cursor}, Destination{"local", true}},
	} {
		_, err := tc.store.InboxEvents(ctx, tc.req, tc.dest)
		requireCode(t, err, "STALE_CURSOR")
	}
	for _, req := range []InboxWatchRequest{{Cursor: "invalid!"}, {Cursor: strings.Repeat("a", 4097)}, {Limit: 101}, {Limit: -1}} {
		_, err := b.InboxEvents(ctx, req, d)
		requireCode(t, err, "INVALID_REQUEST")
	}
	op := testStore(t, Channel{Principal: "watch-restore-operator", Operator: true})
	if _, err = op.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "Invalidate a disposable inbox cursor"}); err != nil {
		t.Fatal(err)
	}
	_, err = b.InboxEvents(ctx, InboxWatchRequest{Cursor: next.Cursor}, d)
	requireCode(t, err, "STALE_CURSOR")
	rescan, err := b.InboxEvents(ctx, InboxWatchRequest{}, d)
	if err != nil || len(rescan.Deliveries) != 3 {
		t.Fatalf("explicit rescan: %+v %v", rescan, err)
	}
}

func TestInboxCursorWaitsForEarlierPoolAssignmentCommit(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_, a, b, r, d, worker := poolFixture(t)
	pooled := poolPublish(t, a, r, d, PoolRequirements{Workspace: worker.Spec.Workspace})
	gate, err := b.pool.Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer gate.Release()
	gateName := "watch-gate-" + uuid.NewString()
	if _, err = gate.Exec(ctx, `SELECT pg_advisory_lock(hashtextextended($1,0))`, gateName); err != nil {
		t.Fatal(err)
	}
	defer gate.Exec(context.Background(), `SELECT pg_advisory_unlock(hashtextextended($1,0))`, gateName)
	trigger := "watch_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	sql := fmt.Sprintf(`CREATE FUNCTION cairn.%s() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock(hashtextextended('%s',0)); RETURN NEW; END $$; CREATE TRIGGER %s AFTER INSERT ON cairn.agent_delivery FOR EACH ROW WHEN (NEW.event_id='%s'::uuid) EXECUTE FUNCTION cairn.%s()`, trigger, gateName, trigger, pooled.EventID, trigger)
	if _, err = b.pool.Exec(ctx, sql); err != nil {
		t.Fatal(err)
	}
	// Unlock before removing the trigger even on an assertion failure.
	defer func() {
		gate.Exec(context.Background(), `SELECT pg_advisory_unlock(hashtextextended($1,0))`, gateName)
		b.pool.Exec(context.Background(), fmt.Sprintf(`DROP TRIGGER IF EXISTS %s ON cairn.agent_delivery; DROP FUNCTION IF EXISTS cairn.%s()`, trigger, trigger))
	}()
	claimDone := make(chan error, 1)
	go func() {
		_, err := b.ClaimWake(ctx, WakeClaimRequest{RequestID: uuid.NewString(), WorkerID: worker.SupervisorID}, d)
		claimDone <- err
	}()
	// Wait for the AFTER INSERT gate, proving its sequence value is allocated.
	for {
		var waiting bool
		err = b.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted AND classid=((hashtextextended($1,0)>>32)&4294967295)::oid AND objid=(hashtextextended($1,0)&4294967295)::oid)`, gateName).Scan(&waiting)
		if err != nil {
			t.Fatal(err)
		}
		if waiting {
			break
		}
		select {
		case err := <-claimDone:
			t.Fatalf("claim ended before gate: %v", err)
		case <-ctx.Done():
			t.Fatal(ctx.Err())
		case <-time.After(5 * time.Millisecond):
		}
	}
	publishDone := make(chan error, 1)
	go func() {
		_, err := a.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "response", Ref: RecordVersionRef{r.RecordID, 1}, Destination: EventDestination{"agent", b.channel.Principal}}, d)
		publishDone <- err
	}()
	// The publisher must block on the collection lock held by the pool assignment.
	for {
		var waiting bool
		err = b.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted AND classid=((hashtextextended($1,0)>>32)&4294967295)::oid AND objid=(hashtextextended($1,0)&4294967295)::oid)`, "agent-events:"+r.Scope.Repo).Scan(&waiting)
		if err != nil {
			t.Fatal(err)
		}
		if waiting {
			break
		}
		select {
		case err := <-publishDone:
			t.Fatalf("later publication passed uncommitted assignment: %v", err)
		case <-ctx.Done():
			t.Fatal(ctx.Err())
		case <-time.After(5 * time.Millisecond):
		}
	}
	before, err := b.InboxEvents(ctx, InboxWatchRequest{}, d)
	if err != nil || len(before.Deliveries) != 0 {
		t.Fatalf("advanced past incomplete commit: %+v %v", before, err)
	}
	if _, err = gate.Exec(ctx, `SELECT pg_advisory_unlock(hashtextextended($1,0))`, gateName); err != nil {
		t.Fatal(err)
	}
	if err = <-claimDone; err != nil {
		t.Fatal(err)
	}
	if err = <-publishDone; err != nil {
		t.Fatal(err)
	}
	after, err := b.InboxEvents(ctx, InboxWatchRequest{Cursor: before.Cursor}, d)
	if err != nil || len(after.Deliveries) != 2 || after.Deliveries[0].Event.EventID != pooled.EventID || after.Deliveries[0].Position >= after.Deliveries[1].Position {
		t.Fatalf("commit order: %+v %v", after, err)
	}
}

func TestInboxCursorSurvivesSessionResumeButFencesOldExecution(t *testing.T) {
	ctx := context.Background()
	publisher, profile, source, dest := eventFixture(t)
	req := RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "watch-session", NativeSessionID: "first", Metadata: AgentMetadata{Harness: "codex", Project: "rhumb", Workspace: "/fixture/rhumb", State: "idle", DeliveryMode: "existing-session"}}
	one, err := profile.RegisterAgent(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	first, err := profile.ForAgentSession(AgentSessionRef{AgentID: one.AgentID, ExecutionID: one.ExecutionID}, dest)
	if err != nil {
		t.Fatal(err)
	}
	page, err := first.InboxEvents(ctx, InboxWatchRequest{}, dest)
	if err != nil {
		t.Fatal(err)
	}
	publish := PublishEventRequest{RequestID: uuid.NewString(), Ref: RecordVersionRef{source.RecordID, 1}, Kind: "request", Destination: EventDestination{Type: "agent", Name: one.Inbox}}
	event, err := publisher.PublishEvent(ctx, publish, dest)
	if err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	resumed, err := profile.RegisterAgent(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	_, err = first.InboxEvents(ctx, InboxWatchRequest{Cursor: page.Cursor}, dest)
	requireCode(t, err, "STALE_SESSION")
	current, err := profile.ForAgentSession(AgentSessionRef{AgentID: resumed.AgentID, ExecutionID: resumed.ExecutionID}, dest)
	if err != nil {
		t.Fatal(err)
	}
	next, err := current.InboxEvents(ctx, InboxWatchRequest{Cursor: page.Cursor}, dest)
	if err != nil || len(next.Deliveries) != 1 || next.Deliveries[0].Event.EventID != event.EventID {
		t.Fatalf("resume lost arrival: %+v %v", next, err)
	}
}
