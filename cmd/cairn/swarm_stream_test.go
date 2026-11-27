package main

import (
	"context"
	"encoding/json"
	"math/rand"
	"net/http"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

// swarmStoreFixture is one campaign on a fresh collection of the disposable
// test database, with the swarm server in front of the real store.
type swarmStoreFixture struct {
	*swarmTestServer
	repo     string
	campaign string
	op       *core.Store
	profile  *core.Store
	dest     core.Destination
	source   core.Record
	root     core.AgentInstance
	rootView *core.Store
}

func newSwarmStoreFixture(t *testing.T, panes swarmPaneReader) *swarmStoreFixture {
	t.Helper()
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("PostgreSQL integration test: run make test-integration")
	}
	ctx := context.Background()
	repo := uuid.NewString()
	open := func(channel core.Channel) *core.Store {
		store, err := core.Open(ctx, dsn, channel)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(store.Close)
		return store
	}
	f := &swarmStoreFixture{repo: repo, campaign: "alpha", dest: core.Destination{Name: "hosted"}}
	// The operator channel is unscoped, as `cairn swarm serve` opens it.
	f.op = open(core.Channel{Principal: "operator-" + repo, Operator: true})
	if err := f.op.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	f.profile = open(core.Channel{Principal: "profile-" + repo, Repo: repo})
	source, err := f.profile.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{
		Kind: "note", Body: "swarm stream fixture source", Sensitivity: "shareable", ClaimType: "self", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	f.source = source
	f.swarmTestServer = newSwarmTestServer(t, f.op, repo, panes)
	f.root, f.rootView = f.session("swarm-root")
	if _, err = f.rootView.SetTeamCampaign(ctx, core.SetTeamCampaignRequest{RequestID: uuid.NewString(), Repo: repo, Campaign: f.campaign, RootAgent: f.root.Inbox, OutOfChainPolicy: "allow", Sensitivity: "shareable"}, f.dest); err != nil {
		t.Fatal(err)
	}
	f.login()
	return f
}

func (f *swarmStoreFixture) session(binding string) (core.AgentInstance, *core.Store) {
	f.t.Helper()
	agent, err := f.profile.RegisterAgent(context.Background(), core.RegisterAgentRequest{RequestID: uuid.NewString(), Binding: binding, NativeSessionID: binding,
		Metadata: core.AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"}}, f.dest)
	if err != nil {
		f.t.Fatal(err)
	}
	view, err := f.profile.ForAgentSession(core.AgentSessionRef{AgentID: agent.AgentID, ExecutionID: agent.ExecutionID}, f.dest)
	if err != nil {
		f.t.Fatal(err)
	}
	return agent, view
}

func (f *swarmStoreFixture) member(binding string) (core.AgentInstance, *core.Store) {
	f.t.Helper()
	agent, view := f.session(binding)
	if _, err := f.rootView.SetTeamMember(context.Background(), core.SetTeamMemberRequest{RequestID: uuid.NewString(), Repo: f.repo, Campaign: f.campaign, Member: agent.Inbox, Lead: f.root.Inbox, Role: "worker"}, f.dest); err != nil {
		f.t.Fatal(err)
	}
	return agent, view
}

func (f *swarmStoreFixture) notice(from *core.Store, to string) (core.AgentEvent, error) {
	return from.PublishEvent(context.Background(), core.PublishEventRequest{RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Action: "review",
		Ref: core.RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version}, Destination: core.EventDestination{Type: "agent", Name: to}}, f.dest)
}

// swarmBrowser keeps what an EventSource and the page keep across
// connections: the last stream ID and the log.
type swarmBrowser struct {
	t          *testing.T
	f          *swarmStoreFixture
	lastID     string
	log        []core.SwarmEvent
	seen       map[string]int
	snapshots  int
	connects   int
	lastStatus int
}

func newSwarmBrowser(f *swarmStoreFixture) *swarmBrowser {
	return &swarmBrowser{t: f.t, f: f, seen: map[string]int{}}
}

// connect opens the stream and applies events until stop returns true or the
// connection has delivered `limit` log events, then drops the connection.
func (b *swarmBrowser) connect(limit int, stop func(event swarmStreamEvent) bool) {
	b.t.Helper()
	conn := b.f.stream(b.f.campaign, b.lastID, map[string]string{"Sec-Fetch-Site": "same-origin"})
	b.connects++
	b.lastStatus = conn.Status
	if conn.Status != http.StatusOK {
		b.t.Fatalf("stream connection %d: %d %s", b.connects, conn.Status, conn.Body)
	}
	defer conn.close()
	if first := conn.next(b.t); first.Event != "hello" {
		b.t.Fatalf("first event is %q, not hello", first.Event)
	}
	logs, gotSnapshot := 0, false
	for {
		event := conn.next(b.t)
		if event.ID != "" {
			// An EventSource remembers the ID of every event that has one.
			b.lastID = event.ID
		}
		switch event.Event {
		case "resync":
			b.log, b.seen = nil, map[string]int{}
		case "log":
			var e core.SwarmEvent
			if err := json.Unmarshal([]byte(event.Data), &e); err != nil {
				b.t.Fatal(err)
			}
			if e.Cursor != event.ID {
				b.t.Fatalf("log event ID %q is not its cursor %q", event.ID, e.Cursor)
			}
			b.log = append(b.log, e)
			b.seen[e.EventID]++
			logs++
		case "snapshot":
			gotSnapshot = true
			b.snapshots++
		case "tick":
			if !gotSnapshot {
				b.t.Fatal("a connection sent a tick before its snapshot")
			}
		case "reset", "fault":
			b.t.Fatalf("unexpected %s: %s", event.Event, event.Data)
		}
		if (limit > 0 && logs >= limit) || (stop != nil && stop(event)) {
			return
		}
	}
}

func (b *swarmBrowser) requireExactlyOnce(want []string) {
	b.t.Helper()
	for i := 1; i < len(b.log); i++ {
		if b.log[i-1].Position >= b.log[i].Position {
			b.t.Fatalf("log is not in commit order at %d", i)
		}
	}
	for id, n := range b.seen {
		if n != 1 {
			b.t.Fatalf("event %s received %d times over %d connections", id, n, b.connects)
		}
	}
	for _, id := range want {
		if b.seen[id] != 1 {
			b.t.Fatalf("event %s received %d times over %d connections", id, b.seen[id], b.connects)
		}
	}
	if len(b.seen) != len(want) {
		b.t.Fatalf("received %d events, want %d", len(b.seen), len(want))
	}
}

// Two members publish at the same time while a browser's stream connection
// keeps dropping. Over real HTTP connections, with Last-Event-ID as the only
// state the browser carries, every event arrives exactly once.
func TestSwarmStreamOverHTTPReconnectsExactlyOnce(t *testing.T) {
	f := newSwarmStoreFixture(t, swarmNoPanes)
	_, a := f.member("worker-a")
	_, b := f.member("worker-b")
	outsider, _ := f.session("outsider")
	_, stranger := f.session("stranger")

	const each = 25
	var mu sync.Mutex
	var published []string
	var wg sync.WaitGroup
	publish := func(from *core.Store, to string, record bool) {
		defer wg.Done()
		for i := 0; i < each; i++ {
			event, err := f.notice(from, to)
			if err != nil {
				t.Error(err)
				return
			}
			if record {
				mu.Lock()
				published = append(published, event.EventID)
				mu.Unlock()
			}
			time.Sleep(time.Duration(rand.Intn(4)) * time.Millisecond)
		}
	}
	wg.Add(3)
	go publish(a, f.root.Inbox, true)
	go publish(b, f.root.Inbox, true)
	go publish(stranger, outsider.Inbox, false) // other traffic of the shared collection
	done := make(chan struct{})
	go func() { wg.Wait(); close(done) }()

	browser := newSwarmBrowser(f)
	random := rand.New(rand.NewSource(829))
	for running := true; running; {
		select {
		case <-done:
			running = false
		default:
		}
		// Drop after a few log events, or after the first tick when the
		// connection had nothing to deliver.
		browser.connect(1+random.Intn(5), func(event swarmStreamEvent) bool { return event.Event == "tick" })
	}
	if t.Failed() {
		return
	}
	// Drain: stay connected until two ticks in a row bring no log event.
	quiet := 0
	browser.connect(0, func(event swarmStreamEvent) bool {
		switch event.Event {
		case "log":
			quiet = 0
		case "tick":
			quiet++
		}
		return quiet >= 3
	})
	browser.requireExactlyOnce(published)
	if browser.connects < 5 {
		t.Fatalf("only %d connections; the test did not exercise reconnects", browser.connects)
	}
	// A connection that lasts to its first tick has had the current
	// snapshot (connect checks the order); one that drops earlier has not.
	if browser.snapshots == 0 {
		t.Fatal("no connection delivered a snapshot")
	}

	// A new browser gets the same events once, as one window.
	fresh := newSwarmBrowser(f)
	fresh.connect(0, func(event swarmStreamEvent) bool { return event.Event == "tick" })
	fresh.requireExactlyOnce(published)

	// The snapshot route gives the same state without a stream.
	resp, raw := f.do(http.MethodGet, "/api/swarm/campaign?campaign=alpha", nil, nil)
	var snapshot core.SwarmSnapshot
	if resp.StatusCode != http.StatusOK || json.Unmarshal(raw, &snapshot) != nil || snapshot.Schema != core.SwarmSnapshotSchema || len(snapshot.Members) != 3 {
		t.Fatalf("snapshot route: %d %s", resp.StatusCode, raw)
	}
	resp, raw = f.do(http.MethodGet, "/api/swarm/landing", nil, nil)
	var landing core.SwarmLanding
	if resp.StatusCode != http.StatusOK || json.Unmarshal(raw, &landing) != nil || len(landing.Campaigns) != 1 || len(landing.Unaffiliated) != 2 {
		t.Fatalf("landing route: %d %s", resp.StatusCode, raw)
	}
}

// The store's own refusals reach the browser with their codes.
func TestSwarmStreamOverHTTPRefusals(t *testing.T) {
	f := newSwarmStoreFixture(t, swarmNoPanes)
	if conn := f.stream("gamma", "", nil); conn.Status != http.StatusNotFound || swarmErrorCode(conn.Body) != "NOT_FOUND" {
		t.Fatalf("unknown campaign: %d %s", conn.Status, conn.Body)
	}
	if conn := f.stream("alpha", "not-a-cursor", nil); conn.Status != http.StatusBadRequest || swarmErrorCode(conn.Body) != "INVALID_REQUEST" {
		t.Fatalf("malformed cursor: %d %s", conn.Status, conn.Body)
	}
	if resp, raw := f.do(http.MethodGet, "/api/swarm/campaign?campaign=gamma", nil, nil); resp.StatusCode != http.StatusNotFound {
		t.Fatalf("snapshot of an unknown campaign: %d %s", resp.StatusCode, raw)
	}
	// A valid cursor of this campaign is accepted on reconnect and brings no
	// second window.
	browser := newSwarmBrowser(f)
	browser.connect(0, func(event swarmStreamEvent) bool { return event.Event == "tick" })
	conn := f.stream("alpha", browser.lastID, nil)
	defer conn.close()
	if conn.Status != http.StatusOK {
		t.Fatalf("reconnect with the last stream ID: %d %s", conn.Status, conn.Body)
	}
	_, before := conn.until(t, "tick")
	for _, event := range before {
		if event.Event == "resync" || event.Event == "log" {
			t.Fatalf("reconnect resent %s: %s", event.Event, event.Data)
		}
	}
	// The action routes are present and refuse until CAIRN-833 lands; they
	// never answer without the CSRF token.
	if resp, raw := f.do(http.MethodPost, "/api/swarm/action/send", map[string]string{}, nil); resp.StatusCode != http.StatusForbidden {
		t.Fatalf("action without CSRF: %d %s", resp.StatusCode, raw)
	}
	if !strings.HasPrefix(f.http.URL, "http://127.0.0.1:") {
		t.Fatalf("test server is not on loopback: %s", f.http.URL)
	}
}
