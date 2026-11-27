package core

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"math/rand"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
)

// swarmFixture is one campaign with a root session on a fresh collection.
type swarmFixture struct {
	*nativeFixture
	campaign string
	root     AgentInstance
	rootView *Store
	op       *Store
}

func newSwarmFixture(t *testing.T, campaign string) *swarmFixture {
	t.Helper()
	f := newNativeFixture(t)
	root := f.register(nativeAgent{Binding: "swarm-root", Native: "swarm-root-1", TaskSummary: "Lead the swarm (CAIRN-829)"})
	view := f.view(sessionRef(root))
	if _, err := view.SetTeamCampaign(context.Background(), SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: campaign, RootAgent: root.Inbox, OutOfChainPolicy: "allow", Sensitivity: "shareable",
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	return &swarmFixture{nativeFixture: f, campaign: campaign, root: root, rootView: view, op: f.operator()}
}

// session registers a session that is not a member.
func (f *swarmFixture) session(binding string) (AgentInstance, *Store) {
	f.t.Helper()
	a := f.register(nativeAgent{Binding: binding})
	return a, f.view(sessionRef(a))
}

// join makes a registered session a worker under the root.
func (f *swarmFixture) join(a AgentInstance) {
	f.t.Helper()
	if _, err := f.rootView.SetTeamMember(context.Background(), SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: f.campaign, Member: a.Inbox, Lead: f.root.Inbox, Role: "worker",
	}, f.dest); err != nil {
		f.t.Fatal(err)
	}
}

func (f *swarmFixture) member(binding string) (AgentInstance, *Store) {
	f.t.Helper()
	a, view := f.session(binding)
	f.join(a)
	return a, view
}

func (f *swarmFixture) noticeRequest(to string) PublishEventRequest {
	return PublishEventRequest{RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Action: "review",
		Ref: RecordVersionRef{f.source.RecordID, f.source.Version}, Destination: EventDestination{"agent", to}}
}

func (f *swarmFixture) notice(from *Store, to string) AgentEvent {
	f.t.Helper()
	e, err := from.PublishEvent(context.Background(), f.noticeRequest(to), f.dest)
	if err != nil {
		f.t.Fatal(err)
	}
	return e
}

func (f *swarmFixture) read(cursor string) SwarmStreamRead {
	f.t.Helper()
	out, err := f.op.ReadSwarmStream(context.Background(), SwarmStreamRequest{Repo: f.repo, Campaign: f.campaign, Cursor: cursor})
	if err != nil {
		f.t.Fatal(err)
	}
	return out
}

func swarmEventIDs(events []SwarmEvent) []string {
	ids := make([]string, len(events))
	for i, e := range events {
		ids[i] = e.EventID
	}
	return ids
}

// swarmClient is what a browser keeps between connections: the last stream ID
// and the log it has shown.
type swarmClient struct {
	t      *testing.T
	f      *swarmFixture
	cursor string
	log    []SwarmEvent
	seen   map[string]int
	resets int
}

func newSwarmClient(f *swarmFixture) *swarmClient {
	return &swarmClient{t: f.t, f: f, seen: map[string]int{}}
}

// connect reads once, as one connection does on one tick. keep limits how
// many events the client receives before the connection drops; a negative
// keep receives the whole read. The cursor advances only over what arrived.
func (c *swarmClient) connect(batch, keep int) SwarmStreamRead {
	c.t.Helper()
	out, err := c.f.op.ReadSwarmStream(context.Background(), SwarmStreamRequest{Repo: c.f.repo, Campaign: c.f.campaign, Cursor: c.cursor, Batch: batch})
	if err != nil {
		c.t.Fatal(err)
	}
	if out.Resync {
		// The page replaces its log with the new window.
		c.log, c.seen = nil, map[string]int{}
		c.resets++
	}
	events := out.Events
	dropped := keep >= 0 && keep < len(events)
	if dropped {
		events = events[:keep]
	}
	for _, e := range events {
		c.log = append(c.log, e)
		c.seen[e.EventID]++
		c.cursor = e.Cursor
	}
	if !dropped {
		// The snapshot and tick events carry the read's cursor.
		c.cursor = out.Cursor
	}
	return out
}

func (c *swarmClient) requireExactlyOnce(want []string) {
	c.t.Helper()
	for i := 1; i < len(c.log); i++ {
		if c.log[i-1].Position >= c.log[i].Position {
			c.t.Fatalf("log is not in commit order at %d: %d then %d", i, c.log[i-1].Position, c.log[i].Position)
		}
	}
	for id, n := range c.seen {
		if n != 1 {
			c.t.Fatalf("event %s received %d times", id, n)
		}
	}
	for _, id := range want {
		if c.seen[id] != 1 {
			c.t.Fatalf("event %s received %d times; log %v", id, c.seen[id], swarmEventIDs(c.log))
		}
	}
	if len(c.seen) != len(want) {
		c.t.Fatalf("received %d events, want %d: %v", len(c.seen), len(want), swarmEventIDs(c.log))
	}
}

func TestSwarmStreamConcurrentPublishersReconnectExactlyOnce(t *testing.T) {
	f := newSwarmFixture(t, "alpha")
	_, a := f.member("worker-a")
	_, b := f.member("worker-b")
	outsider, _ := f.session("outsider")
	_, stranger := f.session("stranger")

	const each = 20
	var mu sync.Mutex
	var published []string
	var wg sync.WaitGroup
	publish := func(from *Store, to string, record bool) {
		defer wg.Done()
		for i := 0; i < each; i++ {
			e, err := from.PublishEvent(context.Background(), f.noticeRequest(to), f.dest)
			if err != nil {
				t.Error(err)
				return
			}
			if record {
				mu.Lock()
				published = append(published, e.EventID)
				mu.Unlock()
			}
			time.Sleep(time.Duration(rand.Intn(3)) * time.Millisecond)
		}
	}
	wg.Add(3)
	go publish(a, f.root.Inbox, true)
	go publish(b, f.root.Inbox, true)
	// Traffic of the shared collection that is not the campaign's.
	go publish(stranger, outsider.Inbox, false)

	client := newSwarmClient(f)
	done := make(chan struct{})
	go func() { wg.Wait(); close(done) }()
	random := rand.New(rand.NewSource(829))
	for running := true; running; {
		select {
		case <-done:
			running = false
		default:
		}
		// Small batches, and a connection that often drops part-way.
		keep := -1
		if random.Intn(2) == 0 {
			keep = random.Intn(4)
		}
		client.connect(1+random.Intn(4), keep)
	}
	for {
		if out := client.connect(3, -1); !out.More && len(out.Events) == 0 {
			break
		}
	}
	if t.Failed() {
		return
	}
	client.requireExactlyOnce(published)
	if client.resets != 1 {
		t.Fatalf("log window was replaced %d times; only the first connection starts a window", client.resets)
	}
	// A browser that connects now sees the same events once.
	fresh := newSwarmClient(f)
	fresh.connect(0, -1)
	fresh.requireExactlyOnce(published)
}

// swarmGate holds an advisory lock that a trigger waits on or tests, so that a
// transaction of the code under test stops at a chosen statement.
type swarmGate struct {
	t    *testing.T
	f    *swarmFixture
	name string
	drop []string
	held bool
	exec func(ctx context.Context, sql string, args ...any) error
	free func()
}

func newSwarmGate(f *swarmFixture) *swarmGate {
	f.t.Helper()
	ctx := context.Background()
	conn, err := f.op.pool.Acquire(ctx)
	if err != nil {
		f.t.Fatal(err)
	}
	g := &swarmGate{t: f.t, f: f, name: "swarm-gate-" + uuid.NewString()}
	g.exec = func(ctx context.Context, sql string, args ...any) error {
		_, err := conn.Exec(ctx, sql, args...)
		return err
	}
	g.free = conn.Release
	if err = g.exec(ctx, `SELECT pg_advisory_lock(hashtextextended($1,0))`, g.name); err != nil {
		f.t.Fatal(err)
	}
	g.held = true
	f.t.Cleanup(func() {
		g.open()
		for _, sql := range g.drop {
			f.op.pool.Exec(context.Background(), sql)
		}
		g.free()
	})
	return g
}

func (g *swarmGate) open() {
	if g.held {
		g.exec(context.Background(), `SELECT pg_advisory_unlock(hashtextextended($1,0))`, g.name)
		g.held = false
	}
}

// trigger installs a row trigger for this fixture's collection only.
func (g *swarmGate) trigger(table, timing, when, body string) {
	g.t.Helper()
	name := "swarm_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	sql := fmt.Sprintf(`CREATE FUNCTION cairn.%[1]s() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN %[5]s RETURN NEW; END $$;
		CREATE TRIGGER %[1]s %[3]s ON cairn.%[2]s FOR EACH ROW WHEN (NEW.repo = '%[6]s'%[4]s) EXECUTE FUNCTION cairn.%[1]s()`,
		name, table, timing, when, strings.ReplaceAll(body, "GATE", "hashtextextended('"+g.name+"',0)"), g.f.repo)
	g.drop = append(g.drop, fmt.Sprintf(`DROP TRIGGER IF EXISTS %[1]s ON cairn.%[2]s; DROP FUNCTION IF EXISTS cairn.%[1]s()`, name, table))
	if _, err := g.f.op.pool.Exec(context.Background(), sql); err != nil {
		g.t.Fatal(err)
	}
}

func (g *swarmGate) waitForWaiter(ctx context.Context, key string, ended <-chan error) {
	g.t.Helper()
	for {
		var waiting bool
		err := g.f.op.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted AND classid=((hashtextextended($1,0)>>32)&4294967295)::oid AND objid=(hashtextextended($1,0)&4294967295)::oid)`, key).Scan(&waiting)
		if err != nil {
			g.t.Fatal(err)
		}
		if waiting {
			return
		}
		select {
		case err := <-ended:
			g.t.Fatalf("transaction ended before it reached %s: %v", key, err)
		case <-ctx.Done():
			g.t.Fatal(ctx.Err())
		case <-time.After(5 * time.Millisecond):
		}
	}
}

func TestSwarmStreamHoldsBehindAnUncommittedEarlierPosition(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	f := newSwarmFixture(t, "alpha")
	aSession, a := f.member("worker-a")
	_, b := f.member("worker-b")
	client := newSwarmClient(f)
	client.connect(0, -1)

	// Stop worker A's publication after its row has a position and before
	// it commits.
	gate := newSwarmGate(f)
	gate.trigger("agent_event", "AFTER INSERT", " AND NEW.publisher = '"+aSession.Inbox+"'", "PERFORM pg_advisory_xact_lock(GATE);")
	first := make(chan error, 1)
	var firstEvent AgentEvent
	go func() {
		e, err := a.PublishEvent(ctx, f.noticeRequest(f.root.Inbox), f.dest)
		firstEvent = e
		first <- err
	}()
	gate.waitForWaiter(ctx, gate.name, first)

	// A later publication cannot take a position visible before the first
	// one: it waits for the collection lock.
	second := make(chan error, 1)
	var secondEvent AgentEvent
	go func() {
		e, err := b.PublishEvent(ctx, f.noticeRequest(f.root.Inbox), f.dest)
		secondEvent = e
		second <- err
	}()
	gate.waitForWaiter(ctx, "agent-events:"+f.repo, second)
	if out := client.connect(0, -1); len(out.Events) != 0 {
		t.Fatalf("stream showed an event before its commit: %v", swarmEventIDs(out.Events))
	}
	gate.open()
	if err := <-first; err != nil {
		t.Fatal(err)
	}
	if err := <-second; err != nil {
		t.Fatal(err)
	}
	out := client.connect(0, -1)
	if len(out.Events) != 2 || out.Events[0].EventID != firstEvent.EventID || out.Events[1].EventID != secondEvent.EventID {
		t.Fatalf("reconnect after both commits: %v", swarmEventIDs(out.Events))
	}
	client.requireExactlyOnce([]string{firstEvent.EventID, secondEvent.EventID})
}

// The control for the test above and the reason for the insert-site guard
// (TestAgentEventInsertSitesTakeTheCollectionLock): an insert that skips the
// collection lock can commit a later position first, and the cursor then
// passes the earlier event for good.
func TestSwarmStreamControlInsertWithoutTheCollectionLockLosesAnEvent(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	f := newSwarmFixture(t, "alpha")
	aSession, a := f.member("worker-a")
	bSession, _ := f.member("worker-b")
	client := newSwarmClient(f)
	client.connect(0, -1)

	gate := newSwarmGate(f)
	gate.trigger("agent_event", "AFTER INSERT", " AND NEW.publisher = '"+aSession.Inbox+"'", "PERFORM pg_advisory_xact_lock(GATE);")
	first := make(chan error, 1)
	var firstEvent AgentEvent
	go func() {
		e, err := a.PublishEvent(ctx, f.noticeRequest(f.root.Inbox), f.dest)
		firstEvent = e
		first <- err
	}()
	gate.waitForWaiter(ctx, gate.name, first)

	// A writer that does not take "agent-events:<repo>".
	unlocked := uuid.NewString()
	if _, err := f.op.pool.Exec(ctx, `INSERT INTO cairn.agent_event(event_id,repo,publisher,kind,notice_action,record_id,version,sensitivity,destination_type,destination_name) VALUES($1,$2,$3,'notice','review',$4,$5,'shareable','agent',$6)`,
		unlocked, f.repo, bSession.Inbox, f.source.RecordID, f.source.Version, f.root.Inbox); err != nil {
		t.Fatal(err)
	}
	if out := client.connect(0, -1); len(out.Events) != 1 || out.Events[0].EventID != unlocked {
		t.Fatalf("the unlocked insert should be visible alone: %v", swarmEventIDs(out.Events))
	}
	gate.open()
	if err := <-first; err != nil {
		t.Fatal(err)
	}
	var earlier, later int64
	if err := f.op.pool.QueryRow(ctx, `SELECT (SELECT position FROM cairn.agent_event WHERE event_id=$1), (SELECT position FROM cairn.agent_event WHERE event_id=$2)`, firstEvent.EventID, unlocked).Scan(&earlier, &later); err != nil {
		t.Fatal(err)
	}
	if earlier >= later {
		t.Fatalf("control did not commit out of position order: %d, %d", earlier, later)
	}
	if out := client.connect(0, -1); len(out.Events) != 0 {
		t.Fatalf("control: expected the cursor to have passed the earlier event; got %v", swarmEventIDs(out.Events))
	}
	if client.seen[firstEvent.EventID] != 0 {
		t.Fatal("control: the earlier event was delivered; the scenario no longer shows the hazard")
	}
}

// The ledger link of a campaign publication is written by a later transaction
// than the one that commits the event. A client that already holds a cursor
// resumes while the first event is still unlinked and a later event has been
// delivered; it gets both, once. The link then appears in the snapshot and
// resends nothing. The same holds when the coordinator hands off in between.
func TestSwarmStreamDelayedJournalLinkIsNotAFilter(t *testing.T) {
	for name, handoff := range map[string]bool{"same coordinator": false, "across a coordinator handoff": true} {
		t.Run(name, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			f := newSwarmFixture(t, "alpha")
			// The publisher is a member that survives a handoff, so it can
			// finish its own operation afterwards.
			publisher, publisherView := f.member("worker-a")
			reader, readerView := f.member("worker-b")

			// The browser is connected before either event exists.
			client := newSwarmClient(f)
			if out := client.connect(0, -1); !out.Resync || len(out.Events) != 0 {
				t.Fatalf("initial window: resync=%v events=%v", out.Resync, swarmEventIDs(out.Events))
			}

			// The coordinator plans the assignment; its owner then publishes
			// against it.
			if _, err := f.rootView.SetCampaignLedgerItem(ctx, SetCampaignLedgerItemRequest{RequestID: uuid.NewString(), Repo: f.repo, Campaign: f.campaign, Kind: "assignment", ItemKey: "slice-1", Owner: publisher.Inbox, Status: "planned", Summary: "Slice one"}, f.dest); err != nil {
				t.Fatal(err)
			}
			// Make the link transaction fail after the event has committed,
			// as a crash between the two transactions would.
			gate := newSwarmGate(f)
			gate.trigger("campaign_ledger_item", "BEFORE INSERT OR UPDATE", "", "IF NOT pg_try_advisory_xact_lock_shared(GATE) THEN RAISE EXCEPTION 'swarm test: ledger link delayed'; END IF;")
			publication := CampaignPublishRequest{
				Repo: f.repo, Campaign: f.campaign, OpKey: "send-slice-1", ItemKey: "slice-1",
				Publication: PublishEventRequest{Kind: "request", Ref: RecordVersionRef{f.source.RecordID, f.source.Version}, Destination: EventDestination{"agent", reader.Inbox}},
			}
			if _, err := publisherView.CampaignPublish(ctx, publication, f.dest); err == nil || !strings.Contains(err.Error(), "ledger link delayed") {
				t.Fatalf("the link step should have failed after the event committed: %v", err)
			}
			var first, state string
			if err := f.op.pool.QueryRow(ctx, `SELECT e.event_id::text, j.state FROM cairn.agent_event e JOIN cairn.campaign_operation_journal j ON j.repo=e.repo AND j.campaign=$2 AND j.op_key='send-slice-1' WHERE e.repo=$1 AND e.publisher=$3 AND e.kind='request'`, f.repo, f.campaign, publisher.Inbox).Scan(&first, &state); err != nil {
				t.Fatal(err)
			}
			if state == "committed" {
				t.Fatalf("journal is %s; the link was not delayed", state)
			}

			// A later event is published and delivered to its recipient.
			later := f.notice(readerView, publisher.Inbox)
			next, err := publisherView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
			if err != nil || next.Delivery == nil || next.Delivery.Event.EventID != later.EventID {
				t.Fatalf("the recipient claims the later event: %+v %v", next, err)
			}
			if _, err = publisherView.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: next.Delivery.DeliveryID, LeaseID: next.Delivery.LeaseID, Disposition: "handled"}, f.dest); err != nil {
				t.Fatal(err)
			}
			if handoff {
				done, hErr := f.rootView.HandoffCampaignCoordinator(ctx, HandoffCampaignCoordinatorRequest{
					RequestID: uuid.NewString(), Repo: f.repo, Campaign: f.campaign,
					NewSession: &NewCoordinatorSessionSpec{Binding: "swarm-root", NativeSessionID: "swarm-root-2"}, Note: "handing off", Shareable: true,
				}, f.dest)
				if hErr != nil || !done.Performed {
					t.Fatalf("handoff: %+v %v", done, hErr)
				}
			}
			want := []string{first, later.EventID}
			if handoff {
				// The handoff may publish events of its own; all of them are
				// the campaign's.
				rows, qErr := f.op.pool.Query(ctx, `SELECT event_id::text FROM cairn.agent_event WHERE repo=$1 ORDER BY position`, f.repo)
				if qErr != nil {
					t.Fatal(qErr)
				}
				want = nil
				for rows.Next() {
					var id string
					if qErr = rows.Scan(&id); qErr != nil {
						t.Fatal(qErr)
					}
					want = append(want, id)
				}
				rows.Close()
			}

			// The browser resumes from the cursor it held before both events,
			// while the first is still unlinked.
			out := client.connect(0, -1)
			if out.Resync != handoff {
				t.Fatalf("resume: resync=%v reason=%q", out.Resync, out.ResyncReason)
			}
			client.requireExactlyOnce(want)
			if client.seen[first] != 1 || client.seen[later.EventID] != 1 {
				t.Fatalf("resume did not deliver both events: %v", swarmEventIDs(client.log))
			}
			for _, item := range out.Snapshot.Ledger {
				if item.EventID == first {
					t.Fatal("the ledger already links the first event; the link was not delayed")
				}
			}

			// The publisher retries its operation, which writes the link. The
			// cursor is already past both events.
			gate.open()
			retried, err := publisherView.CampaignPublish(ctx, publication, f.dest)
			if err != nil || retried.EventID != first {
				t.Fatalf("retry finalizes the same event: %+v %v", retried, err)
			}
			out = client.connect(0, -1)
			if out.Resync || len(out.Events) != 0 {
				t.Fatalf("reconnect after the link: resync=%v events=%v", out.Resync, swarmEventIDs(out.Events))
			}
			client.requireExactlyOnce(want)
			linked := false
			for _, item := range out.Snapshot.Ledger {
				linked = linked || (item.Kind == "assignment" && item.ItemKey == "slice-1" && item.EventID == first)
			}
			if !linked {
				t.Fatalf("snapshot does not annotate the event with its ledger item: %+v", out.Snapshot.Ledger)
			}
			fresh := newSwarmClient(f)
			fresh.connect(0, -1)
			fresh.requireExactlyOnce(want)
		})
	}
}

// A read is cut at the batch size and says so; the next read continues from
// its cursor. A client that stopped reading when More was false too early
// would lose the rest.
func TestSwarmStreamBatchesSayWhenMoreRemains(t *testing.T) {
	f := newSwarmFixture(t, "alpha")
	_, a := f.member("worker-a")
	start := f.read("").Cursor
	var want []string
	for i := 0; i < 7; i++ {
		want = append(want, f.notice(a, f.root.Inbox).EventID)
	}
	var got []string
	cursor := start
	for i, size := range []int{3, 3, 1} {
		out, err := f.op.ReadSwarmStream(context.Background(), SwarmStreamRequest{Repo: f.repo, Campaign: f.campaign, Cursor: cursor, Batch: 3})
		if err != nil {
			t.Fatal(err)
		}
		if out.Resync || len(out.Events) != size || out.More != (i < 2) {
			t.Fatalf("read %d: resync=%v events=%d more=%v", i, out.Resync, len(out.Events), out.More)
		}
		if out.More && out.Cursor != out.Events[len(out.Events)-1].Cursor {
			t.Fatalf("read %d: a cut read must resume after its last event", i)
		}
		got = append(got, swarmEventIDs(out.Events)...)
		cursor = out.Cursor
	}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("batched reads:\n got %v\nwant %v", got, want)
	}
	// A new window holds at most the window size, newest last, and never
	// says More: the window is whole.
	out, err := f.op.ReadSwarmStream(context.Background(), SwarmStreamRequest{Repo: f.repo, Campaign: f.campaign, Window: 4})
	if err != nil || !out.Resync || out.More || strings.Join(swarmEventIDs(out.Events), ",") != strings.Join(want[3:], ",") {
		t.Fatalf("window of four: resync=%v more=%v events=%v err=%v", out.Resync, out.More, swarmEventIDs(out.Events), err)
	}
	for _, bad := range []SwarmStreamRequest{{Window: -1}, {Window: swarmLogWindowMax + 1}, {Batch: -1}, {Batch: swarmLogBatchMax + 1}} {
		bad.Repo, bad.Campaign = f.repo, f.campaign
		if _, err := f.op.ReadSwarmStream(context.Background(), bad); Code(err) != "INVALID_REQUEST" {
			t.Fatalf("window %d batch %d: %v", bad.Window, bad.Batch, err)
		}
	}
}

func TestSwarmStreamCoordinatorHandoffKeepsEveryEventExactlyOnce(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "alpha")
	worker, workerView := f.member("worker-a")
	role := "role/" + f.campaign + "/root"

	before := []string{
		f.notice(f.rootView, worker.Inbox).EventID,
		f.notice(workerView, role).EventID, // stays pending in the old coordinator's inbox
	}
	client := newSwarmClient(f)
	first := client.connect(0, -1)
	client.requireExactlyOnce(before)
	if len(first.Snapshot.Lineage) != 0 {
		t.Fatalf("lineage before handoff: %+v", first.Snapshot.Lineage)
	}

	handoff, err := f.rootView.HandoffCampaignCoordinator(ctx, HandoffCampaignCoordinatorRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: f.campaign,
		NewSession: &NewCoordinatorSessionSpec{Binding: "swarm-root", NativeSessionID: "swarm-root-2"}, Note: "handing off", Shareable: true,
	}, f.dest)
	if err != nil || !handoff.Performed || handoff.ToSession == nil {
		t.Fatalf("handoff: %+v %v", handoff, err)
	}
	successor := *handoff.ToSession
	successorView := f.view(sessionRef(successor))
	after := []string{
		f.notice(successorView, worker.Inbox).EventID,
		f.notice(workerView, role).EventID, // resolves to the successor now
	}

	// Every event of the campaign in commit order, straight from the store.
	rows, err := f.op.pool.Query(ctx, `SELECT event_id::text FROM cairn.agent_event WHERE repo=$1 ORDER BY position`, f.repo)
	if err != nil {
		t.Fatal(err)
	}
	var all []string
	for rows.Next() {
		var id string
		if err = rows.Scan(&id); err != nil {
			t.Fatal(err)
		}
		all = append(all, id)
	}
	rows.Close()
	for _, want := range append(append([]string{}, before...), after...) {
		found := false
		for _, id := range all {
			found = found || id == want
		}
		if !found {
			t.Fatalf("event %s is not in the collection", want)
		}
	}

	// The reconnecting browser gets a new window, because the successor is a
	// new campaign principal. Nothing is lost and nothing repeats, and the
	// old coordinator's events are still there.
	out := client.connect(0, -1)
	if !out.Resync || out.ResyncReason != "membership_changed" {
		t.Fatalf("reconnect across a handoff: resync=%v reason=%q", out.Resync, out.ResyncReason)
	}
	client.requireExactlyOnce(all)
	fresh := newSwarmClient(f)
	fresh.connect(0, -1)
	fresh.requireExactlyOnce(all)

	// The log continues from the new window.
	more := f.notice(successorView, worker.Inbox).EventID
	if out = client.connect(0, -1); out.Resync || len(out.Events) != 1 || out.Events[0].EventID != more {
		t.Fatalf("after the window: resync=%v events=%v", out.Resync, swarmEventIDs(out.Events))
	}
	client.requireExactlyOnce(append(all, more))

	// The snapshot shows the lineage and the current holder.
	snap := out.Snapshot
	if len(snap.Lineage) != 1 || snap.Lineage[0].FromPrincipal != f.root.Inbox || snap.Lineage[0].ToPrincipal != successor.Inbox || snap.Lineage[0].FromLabel != f.root.DisplayName || snap.Lineage[0].ToLabel != successor.DisplayName {
		t.Fatalf("lineage: %+v", snap.Lineage)
	}
	var oldRoot, newRoot *SwarmMember
	for i := range snap.Members {
		switch snap.Members[i].Member {
		case f.root.Inbox:
			oldRoot = &snap.Members[i]
		case successor.Inbox:
			newRoot = &snap.Members[i]
		}
	}
	if oldRoot == nil || oldRoot.Active || oldRoot.RoleAddress != "" || oldRoot.Session == nil || oldRoot.Session.Presence != "stopped" {
		t.Fatalf("old coordinator row: %+v", oldRoot)
	}
	if newRoot == nil || !newRoot.Active || newRoot.Role != "root" || newRoot.RoleAddress != role || snap.Campaign.RootAgent != successor.Inbox {
		t.Fatalf("successor row: %+v campaign root %s", newRoot, snap.Campaign.RootAgent)
	}
}

func TestSwarmStreamWorkerJoiningMidRunResendsTheWindow(t *testing.T) {
	f := newSwarmFixture(t, "alpha")
	_, a := f.member("worker-a")
	late, lateView := f.session("worker-late")
	outsider, _ := f.session("outsider")

	// Before it joins, the late worker's event is not the campaign's.
	early := f.notice(lateView, outsider.Inbox).EventID
	own := f.notice(a, f.root.Inbox).EventID
	client := newSwarmClient(f)
	client.connect(0, -1)
	client.requireExactlyOnce([]string{own})
	connected := newSwarmClient(f) // a browser that stays connected
	connected.connect(0, -1)

	f.join(late)
	joined := f.notice(lateView, f.root.Inbox).EventID

	// The late worker's earlier event lies behind both cursors. A resync
	// marker replaces the window, which now holds it.
	want := []string{early, own, joined}
	for name, c := range map[string]*swarmClient{"reconnecting": client, "connected": connected} {
		out := c.connect(0, -1)
		if !out.Resync || out.ResyncReason != "membership_changed" {
			t.Fatalf("%s: resync=%v reason=%q", name, out.Resync, out.ResyncReason)
		}
		c.requireExactlyOnce(want)
		if c.log[0].EventID != early {
			t.Fatalf("%s: window is not in commit order: %v", name, swarmEventIDs(c.log))
		}
		if out = c.connect(0, -1); out.Resync || len(out.Events) != 0 {
			t.Fatalf("%s: the next read should continue quietly: resync=%v events=%v", name, out.Resync, swarmEventIDs(out.Events))
		}
	}
	// A member that is made inactive stays a campaign principal, so no window
	// is resent and its events stay.
	inactive := false
	if _, err := f.rootView.SetTeamMember(context.Background(), SetTeamMemberRequest{RequestID: uuid.NewString(), Repo: f.repo, Campaign: f.campaign, Member: late.Inbox, Lead: f.root.Inbox, Role: "worker", Active: &inactive}, f.dest); err != nil {
		t.Fatal(err)
	}
	if out := client.connect(0, -1); out.Resync || len(out.Events) != 0 {
		t.Fatalf("deactivation resent the window: resync=%v events=%v", out.Resync, swarmEventIDs(out.Events))
	}
	client.requireExactlyOnce(want)
}

func TestSwarmStreamAttribution(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	// An event from before the campaign exists is never the campaign's.
	root := f.register(nativeAgent{Binding: "swarm-root"})
	rootView := f.view(sessionRef(root))
	workerSession := f.register(nativeAgent{Binding: "worker-a"})
	workerView := f.view(sessionRef(workerSession))
	note := func(from *Store, destination EventDestination) string {
		t.Helper()
		e, err := from.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Action: "review", Ref: RecordVersionRef{f.source.RecordID, f.source.Version}, Destination: destination}, f.dest)
		if err != nil {
			t.Fatal(err)
		}
		return e.EventID
	}
	beforeCampaign := note(workerView, EventDestination{"agent", root.Inbox})
	if _, err := rootView.SetTeamCampaign(ctx, SetTeamCampaignRequest{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "alpha", RootAgent: root.Inbox, OutOfChainPolicy: "allow", Sensitivity: "shareable"}, f.dest); err != nil {
		t.Fatal(err)
	}
	sf := &swarmFixture{nativeFixture: f, campaign: "alpha", root: root, rootView: rootView, op: f.operator()}
	sf.join(workerSession)
	outsider, outsiderView := sf.session("outsider")
	other, otherView := sf.session("other")

	subscribe := func(view *Store, topic string) {
		t.Helper()
		if _, err := view.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: f.repo, Topic: topic, Active: true}); err != nil {
			t.Fatal(err)
		}
	}
	subscribe(workerView, "swarm.shared")
	subscribe(otherView, "swarm.shared")
	subscribe(otherView, "swarm.elsewhere")

	want := []string{
		note(workerView, EventDestination{"agent", outsider.Inbox}),        // a member publishes
		note(outsiderView, EventDestination{"agent", workerSession.Inbox}), // addressed to a member
		note(outsiderView, EventDestination{"agent", "role/alpha/root"}),   // routed through the campaign's role address
		note(outsiderView, EventDestination{"topic", "swarm.shared"}),      // a topic delivery to a member
		note(workerView, EventDestination{"topic", "swarm.elsewhere"}),     // a member publishes to a topic
	}
	excluded := map[string]string{
		beforeCampaign: "created before the campaign",
		note(outsiderView, EventDestination{"agent", other.Inbox}):       "between two outsiders",
		note(outsiderView, EventDestination{"topic", "swarm.elsewhere"}): "a topic with no member subscribed",
	}
	client := newSwarmClient(sf)
	client.connect(0, -1)
	for id, why := range excluded {
		if client.seen[id] != 0 {
			t.Fatalf("event %s is in the campaign log: %s", id, why)
		}
	}
	client.requireExactlyOnce(want)

	// A second campaign on the same collection has its own log.
	if _, err := outsiderView.SetTeamCampaign(ctx, SetTeamCampaignRequest{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "beta", RootAgent: outsider.Inbox, OutOfChainPolicy: "allow", Sensitivity: "shareable"}, f.dest); err != nil {
		t.Fatal(err)
	}
	beta := &swarmFixture{nativeFixture: f, campaign: "beta", root: outsider, rootView: outsiderView, op: sf.op}
	betaOnly := note(outsiderView, EventDestination{"agent", other.Inbox})
	betaClient := newSwarmClient(beta)
	betaClient.connect(0, -1)
	betaClient.requireExactlyOnce([]string{betaOnly})
	if out := client.connect(0, -1); len(out.Events) != 0 {
		t.Fatalf("an event of another campaign reached this one: %v", swarmEventIDs(out.Events))
	}
}

func TestSwarmStreamCursorBinding(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "alpha")
	_, a := f.member("worker-a")
	f.notice(a, f.root.Inbox)
	cursor := f.read("").Cursor
	read := func(campaign, cursor string) error {
		_, err := f.op.ReadSwarmStream(ctx, SwarmStreamRequest{Repo: f.repo, Campaign: campaign, Cursor: cursor})
		return err
	}
	if err := read("alpha", cursor); err != nil {
		t.Fatal(err)
	}
	for name, bad := range map[string]string{"not base64": "!!!", "padding": cursor + "=", "truncated": cursor[:len(cursor)/2], "empty object": "e30"} {
		if err := read("alpha", bad); Code(err) != "INVALID_REQUEST" {
			t.Fatalf("%s cursor: %v", name, err)
		}
	}
	// A cursor of another campaign of the same collection. A principal
	// belongs to one active campaign, so the second campaign has its own root.
	betaRoot, betaView := f.session("beta-root")
	if _, err := betaView.SetTeamCampaign(ctx, SetTeamCampaignRequest{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "beta", RootAgent: betaRoot.Inbox, Sensitivity: "shareable"}, f.dest); err != nil {
		t.Fatal(err)
	}
	if err := read("beta", cursor); Code(err) != "INVALID_REQUEST" {
		t.Fatalf("cursor of another campaign: %v", err)
	}
	if err := read("gamma", ""); Code(err) != "NOT_FOUND" {
		t.Fatalf("unknown campaign: %v", err)
	}
	if err := read("not a name", ""); Code(err) != "INVALID_REQUEST" {
		t.Fatalf("invalid campaign name: %v", err)
	}
	// The encoder's own output is always accepted, also for the longest
	// collection name with characters that JSON escapes.
	long := swarmScope{Repo: strings.Repeat("&", 256), Campaign: TeamCampaign{Campaign: strings.Repeat("c", 64)}, Generation: 1, Fence: uuid.NewString(), Epoch: strings.Repeat("e", 24)}
	if c, err := decodeSwarmCursor(encodeSwarmCursor(long, 1<<40), long); err != nil || c == nil || c.Position != 1<<40 {
		t.Fatalf("the longest cursor the encoder emits is refused: %v", err)
	}
	// A cursor ahead of the collection, as after a restore to an older state.
	sc := swarmScope{Repo: f.repo, Campaign: TeamCampaign{Campaign: "alpha"}, Generation: f.read("").Snapshot.RestoreGeneration, Epoch: f.read("").Snapshot.MembershipEpoch}
	if err := f.op.pool.QueryRow(ctx, `SELECT COALESCE((SELECT fence_id::text FROM cairn.restore_fence ORDER BY generation DESC LIMIT 1),'')`).Scan(&sc.Fence); err != nil {
		t.Fatal(err)
	}
	if err := read("alpha", encodeSwarmCursor(sc, 0)); err != nil {
		t.Fatalf("a cursor built for this store is refused: %v", err)
	}
	if err := read("alpha", encodeSwarmCursor(sc, 1<<40)); Code(err) != "STALE_CURSOR" {
		t.Fatalf("cursor ahead of the collection: %v", err)
	}
	// A cursor from before a restore fence.
	older := sc
	older.Generation--
	if err := read("alpha", encodeSwarmCursor(older, 0)); Code(err) != "STALE_CURSOR" {
		t.Fatalf("cursor of an older generation: %v", err)
	}
	// Two restores of one backup reach the same generation number. The
	// cursor of the discarded branch carries another fence identity and must
	// not continue here, even at a position this branch has reached.
	other := sc
	other.Fence = uuid.NewString()
	if err := read("alpha", encodeSwarmCursor(other, 0)); Code(err) != "STALE_CURSOR" {
		t.Fatalf("cursor of another restore of the same backup: %v", err)
	}
	var fence string
	if err := f.op.pool.QueryRow(ctx, `SELECT COALESCE((SELECT fence_id::text FROM cairn.restore_fence ORDER BY generation DESC LIMIT 1),'')`).Scan(&fence); err != nil {
		t.Fatal(err)
	}
	var decoded swarmCursor
	if raw, err := base64.RawURLEncoding.DecodeString(cursor); err != nil || json.Unmarshal(raw, &decoded) != nil || decoded.Fence != fence || decoded.Generation != sc.Generation {
		t.Fatalf("cursor does not carry the store's fence %q and generation: %+v", fence, decoded)
	}
	// The read needs the operator channel, and a channel scoped to another
	// collection is refused.
	if _, err := f.rootView.ReadSwarmStream(ctx, SwarmStreamRequest{Repo: f.repo, Campaign: "alpha"}); Code(err) != "AUTHORITY_DENIED" {
		t.Fatalf("an agent session read the swarm view: %v", err)
	}
	scoped := testStore(t, Channel{Principal: "operator-scoped", Operator: true, Repo: uuid.NewString()})
	if _, err := scoped.ReadSwarmStream(ctx, SwarmStreamRequest{Repo: f.repo, Campaign: "alpha"}); Code(err) != "AUTHORITY_DENIED" {
		t.Fatalf("an operator scoped elsewhere read the swarm view: %v", err)
	}
}

func TestSwarmSnapshotRosterLedgerAndLanding(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "alpha")
	worker, _ := f.member("worker-a")
	idle, _ := f.session("unaffiliated")
	set := func(view *Store, kind, key, owner, status, summary string) {
		t.Helper()
		if _, err := view.SetCampaignLedgerItem(ctx, SetCampaignLedgerItemRequest{RequestID: uuid.NewString(), Repo: f.repo, Campaign: f.campaign, Kind: kind, ItemKey: key, Owner: owner, Status: status, Summary: summary}, f.dest); err != nil {
			t.Fatal(err)
		}
	}
	set(f.rootView, "assignment", "slice-1", worker.Inbox, "dispatched", "Build the stream (CAIRN-829)")
	set(f.rootView, "assignment", "slice-0", worker.Inbox, "completed", "Design pass CAIRN-827")
	set(f.rootView, "decision", "port", f.root.Inbox, "open", "Choose the default port")
	set(f.rootView, "hold", "freeze", f.root.Inbox, "released", "Old hold")

	snap, err := f.op.GetSwarmSnapshot(ctx, SwarmSnapshotRequest{Repo: f.repo, Campaign: f.campaign})
	if err != nil {
		t.Fatal(err)
	}
	if snap.Schema != SwarmSnapshotSchema || snap.Collection != f.repo || snap.Campaign.Campaign != "alpha" || snap.Campaign.RoleAddress != "role/alpha/root" || len(snap.Campaign.Members) != 0 || snap.OperatorPrincipal != f.op.channel.Principal {
		t.Fatalf("header: %+v", snap)
	}
	if len(snap.Members) != 2 || snap.Members[0].Member != f.root.Inbox || snap.Members[0].Role != "root" || snap.Members[1].Member != worker.Inbox {
		t.Fatalf("members: %+v", snap.Members)
	}
	root, member := snap.Members[0], snap.Members[1]
	if root.RoleAddress != "role/alpha/root" || root.Label != f.root.DisplayName || root.AgentID != f.root.AgentID || root.Session == nil || root.Session.Presence != "online" || root.Session.LastSeen != nil || root.Session.Harness != "codex" || root.Session.TaskSummary == "" {
		t.Fatalf("root row: %+v session %+v", root, root.Session)
	}
	if len(root.Holds) != 1 || root.Holds[0].Kind != "decision" || len(root.PlaneIssues) != 1 || root.PlaneIssues[0] != "CAIRN-829" {
		t.Fatalf("root holds and issues: %+v %v", root.Holds, root.PlaneIssues)
	}
	if member.RoleAddress != "" || len(member.Holds) != 1 || member.Holds[0].ItemKey != "slice-1" || len(member.PlaneIssues) != 1 || member.PlaneIssues[0] != "CAIRN-829" {
		t.Fatalf("worker row: %+v", member)
	}
	open := map[string]bool{}
	for _, item := range snap.Ledger {
		open[item.Kind+"/"+item.ItemKey] = item.Open
	}
	if len(snap.Ledger) != 4 || !open["assignment/slice-1"] || open["assignment/slice-0"] || !open["decision/port"] || open["hold/freeze"] {
		t.Fatalf("ledger: %+v", snap.Ledger)
	}
	if snap.Handoff != nil || snap.SectionErrors != nil {
		t.Fatalf("handoff %+v errors %v", snap.Handoff, snap.SectionErrors)
	}

	// A stopped member keeps its row and shows when it was last seen.
	if _, err = f.receiver.LeaveAgent(ctx, sessionRef(worker), f.dest); err != nil {
		t.Fatal(err)
	}
	// A coordinator past its age threshold is due for handoff.
	if _, err = f.op.pool.Exec(ctx, `UPDATE cairn.agent_session SET started_at=clock_timestamp()-interval '5 hours' WHERE agent_id=$1`, f.root.AgentID); err != nil {
		t.Fatal(err)
	}
	snap, err = f.op.GetSwarmSnapshot(ctx, SwarmSnapshotRequest{Repo: f.repo, Campaign: f.campaign})
	if err != nil {
		t.Fatal(err)
	}
	if s := snap.Members[1].Session; s == nil || s.Presence != "stopped" || s.LastSeen == nil || s.State != "" {
		t.Fatalf("stopped worker: %+v", s)
	}
	if snap.Handoff == nil || snap.Handoff.HolderPrincipal != f.root.Inbox || len(snap.Handoff.Reasons) != 1 || snap.Handoff.Since == nil {
		t.Fatalf("handoff due: %+v", snap.Handoff)
	}

	landing, err := f.op.GetSwarmLanding(ctx, f.repo)
	if err != nil {
		t.Fatal(err)
	}
	if landing.Schema != SwarmLandingSchema || len(landing.Campaigns) != 1 || landing.Campaigns[0].Campaign != "alpha" || landing.Campaigns[0].ActiveMembers != 2 || landing.Campaigns[0].OnlineMembers != 1 || landing.Campaigns[0].RootLabel != f.root.DisplayName {
		t.Fatalf("landing campaigns: %+v", landing.Campaigns)
	}
	if len(landing.Unaffiliated) != 1 || landing.Unaffiliated[0].AgentID != idle.AgentID {
		t.Fatalf("landing sessions: %+v", landing.Unaffiliated)
	}
}

// A read takes no lock and writes nothing: the transaction is read-only, and
// a held collection lock does not delay it.
func TestSwarmReadTakesNoLock(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	f := newSwarmFixture(t, "alpha")
	_, a := f.member("worker-a")
	f.notice(a, f.root.Inbox)

	tx, err := f.op.beginSwarmRead(ctx)
	if err != nil {
		t.Fatal(err)
	}
	_, err = tx.Exec(ctx, `UPDATE cairn.agent_team_campaign SET updated_at=updated_at WHERE repo=$1`, f.repo)
	tx.Rollback(ctx)
	if err == nil || !strings.Contains(err.Error(), "read-only") {
		t.Fatalf("a write inside the swarm read was not refused: %v", err)
	}

	// Hold the collection's publication lock and a row lock on the campaign.
	holder, err := f.op.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer holder.Rollback(context.Background())
	if err = lock(ctx, holder, "agent-events:"+f.repo); err != nil {
		t.Fatal(err)
	}
	if _, err = holder.Exec(ctx, `SELECT 1 FROM cairn.agent_team_campaign WHERE repo=$1 FOR UPDATE`, f.repo); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() {
		_, err := f.op.ReadSwarmStream(ctx, SwarmStreamRequest{Repo: f.repo, Campaign: f.campaign})
		done <- err
	}()
	select {
	case err = <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("the read waited for a lock")
	}
}

// A coordinator handoff moves the old coordinator's pending deliveries to its
// successor. When either of them is a principal of another campaign, a topic
// event enters or leaves that other campaign's log. Its readers must get a
// new window then, or a reader that was connected and one that connects
// afresh would disagree for good.
func TestSwarmStreamHandoffInAnotherCampaignResendsTheWindow(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "alpha")
	_, outsider := f.session("outsider")
	subscribe := func(view *Store, topic string) {
		t.Helper()
		if _, err := view.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: f.repo, Topic: topic, Active: true}); err != nil {
			t.Fatal(err)
		}
	}
	topic := func(name string) string {
		t.Helper()
		e, err := outsider.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Action: "review", Ref: RecordVersionRef{f.source.RecordID, f.source.Version}, Destination: EventDestination{"topic", name}}, f.dest)
		if err != nil {
			t.Fatal(err)
		}
		return e.EventID
	}
	campaign := func(name string, root AgentInstance, view *Store) *swarmFixture {
		t.Helper()
		if _, err := view.SetTeamCampaign(ctx, SetTeamCampaignRequest{RequestID: uuid.NewString(), Repo: f.repo, Campaign: name, RootAgent: root.Inbox, OutOfChainPolicy: "allow", Sensitivity: "shareable"}, f.dest); err != nil {
			t.Fatal(err)
		}
		return &swarmFixture{nativeFixture: f.nativeFixture, campaign: name, root: root, rootView: view, op: f.op}
	}
	leave := func(c *swarmFixture, member AgentInstance) {
		t.Helper()
		inactive := false
		if _, err := c.rootView.SetTeamMember(ctx, SetTeamMemberRequest{RequestID: uuid.NewString(), Repo: f.repo, Campaign: c.campaign, Member: member.Inbox, Lead: c.root.Inbox, Role: "worker", Active: &inactive}, f.dest); err != nil {
			t.Fatal(err)
		}
	}
	handoff := func(c *swarmFixture, to AgentInstance) {
		t.Helper()
		done, err := c.rootView.HandoffCampaignCoordinator(ctx, HandoffCampaignCoordinatorRequest{RequestID: uuid.NewString(), Repo: f.repo, Campaign: c.campaign, Successor: &AgentSessionRef{AgentID: to.AgentID, ExecutionID: to.ExecutionID}, Note: "handing off", Shareable: true}, f.dest)
		if err != nil || !done.Performed {
			t.Fatalf("handoff of %s to %s: %+v %v", c.campaign, to.DisplayName, done, err)
		}
	}
	agree := func(name string, connected *swarmClient, c *swarmFixture, want []string) {
		t.Helper()
		out := connected.connect(0, -1)
		if !out.Resync || out.ResyncReason != "membership_changed" {
			t.Fatalf("%s: the connected reader got no new window: resync=%v reason=%q", name, out.Resync, out.ResyncReason)
		}
		fresh := newSwarmClient(c)
		fresh.connect(0, -1)
		if got, now := swarmEventIDs(connected.log), swarmEventIDs(fresh.log); strings.Join(got, ",") != strings.Join(now, ",") {
			t.Fatalf("%s: a connected reader and a new reader disagree:\n connected %v\n new       %v", name, got, now)
		}
		for _, id := range want {
			if connected.seen[id] != 1 {
				t.Fatalf("%s: event %s is in the log %d times", name, id, connected.seen[id])
			}
		}
	}

	// An event enters another campaign's log. The successor of alpha's
	// coordinator is a former member of beta.
	betaRoot, betaView := f.session("beta-root")
	beta := campaign("beta", betaRoot, betaView)
	successor, _ := f.session("successor")
	beta.join(successor)
	leave(beta, successor)
	subscribe(f.rootView, "swarm.alpha")
	entering := topic("swarm.alpha") // delivered to alpha's coordinator only, and left pending
	betaReader := newSwarmClient(beta)
	betaReader.connect(0, -1)
	if betaReader.seen[entering] != 0 {
		t.Fatal("the topic event is in beta's log before any handoff")
	}
	handoff(f, successor)
	agree("entering", betaReader, beta, []string{entering})

	// An event leaves another campaign's log. The coordinator of gamma that
	// hands off is a former member of delta.
	deltaRoot, deltaView := f.session("delta-root")
	delta := campaign("delta", deltaRoot, deltaView)
	gammaRoot, gammaView := f.session("gamma-root")
	delta.join(gammaRoot)
	leave(delta, gammaRoot)
	gamma := campaign("gamma", gammaRoot, gammaView)
	subscribe(gammaView, "swarm.gamma")
	leaving := topic("swarm.gamma") // delivered to gamma's coordinator, a delta principal
	deltaReader := newSwarmClient(delta)
	deltaReader.connect(0, -1)
	if deltaReader.seen[leaving] != 1 {
		t.Fatal("the topic event is not in delta's log before the handoff")
	}
	heir, _ := f.session("gamma-heir")
	handoff(gamma, heir)
	agree("leaving", deltaReader, delta, nil)
	if deltaReader.seen[leaving] != 0 {
		t.Fatal("the moved delivery's event stayed in delta's log for the connected reader only")
	}

	// A handoff that touches no principal of a campaign resends nothing there.
	quietRoot, quietView := f.session("quiet-root")
	quiet := campaign("quiet", quietRoot, quietView)
	quietReader := newSwarmClient(quiet)
	quietReader.connect(0, -1)
	epsilonRoot, epsilonView := f.session("epsilon-root")
	epsilon := campaign("epsilon", epsilonRoot, epsilonView)
	epsilonHeir, _ := f.session("epsilon-heir")
	handoff(epsilon, epsilonHeir)
	if out := quietReader.connect(0, -1); out.Resync {
		t.Fatalf("an unrelated handoff resent the window: %q", out.ResyncReason)
	}
}
