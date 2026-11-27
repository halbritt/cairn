package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
)

const swarmTestToken = "0123456789abcdef0123456789abcdef0123456789abcdef"

// swarmScriptedReads plays back stream reads, so that stream behaviour can be
// tested without a database.
type swarmScriptedReads struct {
	mu       sync.Mutex
	requests []core.SwarmStreamRequest
	next     func(call int, req core.SwarmStreamRequest) (core.SwarmStreamRead, error)
}

func (r *swarmScriptedReads) ReadSwarmStream(ctx context.Context, req core.SwarmStreamRequest) (core.SwarmStreamRead, error) {
	r.mu.Lock()
	call := len(r.requests)
	r.requests = append(r.requests, req)
	r.mu.Unlock()
	return r.next(call, req)
}

func (r *swarmScriptedReads) GetSwarmSnapshot(ctx context.Context, req core.SwarmSnapshotRequest) (core.SwarmSnapshot, error) {
	return core.SwarmSnapshot{Schema: core.SwarmSnapshotSchema, Campaign: core.TeamCampaign{Campaign: req.Campaign}}, nil
}

func (r *swarmScriptedReads) GetSwarmLanding(ctx context.Context, repo string) (core.SwarmLanding, error) {
	return core.SwarmLanding{Schema: core.SwarmLandingSchema, Collection: repo}, nil
}

func (r *swarmScriptedReads) cursors() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := make([]string, len(r.requests))
	for i, req := range r.requests {
		out[i] = req.Cursor
	}
	return out
}

// swarmTestServer is a swarm server on a real loopback listener.
type swarmTestServer struct {
	t      *testing.T
	server *swarmServer
	http   *httptest.Server
	client *http.Client
	csrf   string
}

func swarmNoPanes(ctx context.Context) swarmPaneObservation {
	return swarmPaneObservation{ObservedAt: time.Now(), Status: "ok", Panes: map[string]swarmPane{}}
}

func newSwarmTestServer(t *testing.T, store *core.Store, collection string, panes swarmPaneReader) *swarmTestServer {
	t.Helper()
	listener := httptest.NewUnstartedServer(nil)
	port := listener.Listener.Addr().(*net.TCPAddr).Port
	server := newSwarmServer(store, collection, port, swarmTestToken, panes)
	server.tick = 20 * time.Millisecond
	listener.Config.Handler = server
	listener.Start()
	t.Cleanup(listener.Close)
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	return &swarmTestServer{t: t, server: server, http: listener, client: &http.Client{Jar: jar}}
}

func (s *swarmTestServer) do(method, path string, body any, header map[string]string) (*http.Response, []byte) {
	s.t.Helper()
	var reader io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			s.t.Fatal(err)
		}
		reader = strings.NewReader(string(raw))
	}
	req, err := http.NewRequest(method, s.http.URL+path, reader)
	if err != nil {
		s.t.Fatal(err)
	}
	for key, value := range header {
		if key == "Host" {
			req.Host = value
		} else {
			req.Header.Set(key, value)
		}
	}
	resp, err := s.client.Do(req)
	if err != nil {
		s.t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		s.t.Fatal(err)
	}
	return resp, raw
}

func (s *swarmTestServer) login() {
	s.t.Helper()
	resp, raw := s.do(http.MethodPost, "/api/auth/login", map[string]string{"token": swarmTestToken}, nil)
	var body struct {
		OK   bool   `json:"ok"`
		CSRF string `json:"csrf_token"`
	}
	if resp.StatusCode != http.StatusOK || json.Unmarshal(raw, &body) != nil || !body.OK || body.CSRF == "" {
		s.t.Fatalf("login: %d %s", resp.StatusCode, raw)
	}
	s.csrf = body.CSRF
}

func swarmErrorCode(raw []byte) string {
	var body struct {
		Code string `json:"code"`
	}
	_ = json.Unmarshal(raw, &body)
	return body.Code
}

type swarmStreamEvent struct {
	Event string
	ID    string
	Data  string
}

// swarmStreamConn is one stream connection, as an EventSource holds it.
type swarmStreamConn struct {
	Status int
	Body   []byte // of a refused connection
	Events chan swarmStreamEvent
	cancel context.CancelFunc
}

func (c *swarmStreamConn) close() { c.cancel() }

// next returns the next event, or fails the test when none arrives.
func (c *swarmStreamConn) next(t *testing.T) swarmStreamEvent {
	t.Helper()
	select {
	case event, ok := <-c.Events:
		if !ok {
			t.Fatal("the stream ended")
		}
		return event
	case <-time.After(10 * time.Second):
		t.Fatal("no stream event within 10 s")
	}
	return swarmStreamEvent{}
}

// until reads events until one has the wanted name, and returns it with
// everything read before it.
func (c *swarmStreamConn) until(t *testing.T, name string) (swarmStreamEvent, []swarmStreamEvent) {
	t.Helper()
	var before []swarmStreamEvent
	for {
		event := c.next(t)
		if event.Event == name {
			return event, before
		}
		before = append(before, event)
	}
}

func (s *swarmTestServer) stream(campaign, lastEventID string, header map[string]string) *swarmStreamConn {
	s.t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, s.http.URL+"/api/swarm/stream?campaign="+campaign, nil)
	if err != nil {
		s.t.Fatal(err)
	}
	if lastEventID != "" {
		req.Header.Set("Last-Event-ID", lastEventID)
	}
	for key, value := range header {
		req.Header.Set(key, value)
	}
	resp, err := s.client.Do(req)
	if err != nil {
		cancel()
		s.t.Fatal(err)
	}
	conn := &swarmStreamConn{Status: resp.StatusCode, Events: make(chan swarmStreamEvent, 4096), cancel: cancel}
	if resp.StatusCode != http.StatusOK || !strings.HasPrefix(resp.Header.Get("Content-Type"), "text/event-stream") {
		conn.Body, _ = io.ReadAll(resp.Body)
		resp.Body.Close()
		close(conn.Events)
		return conn
	}
	go func() {
		defer resp.Body.Close()
		defer close(conn.Events)
		scanner := bufio.NewScanner(resp.Body)
		scanner.Buffer(make([]byte, 0, 1<<20), 16<<20)
		var event swarmStreamEvent
		for scanner.Scan() {
			line := scanner.Text()
			switch {
			case line == "":
				if event.Event != "" || event.Data != "" {
					conn.Events <- event
				}
				event = swarmStreamEvent{}
			case strings.HasPrefix(line, "event: "):
				event.Event = line[len("event: "):]
			case strings.HasPrefix(line, "id: "):
				event.ID = line[len("id: "):]
			case strings.HasPrefix(line, "data: "):
				event.Data = line[len("data: "):]
			}
		}
	}()
	s.t.Cleanup(cancel)
	return conn
}

func TestSwarmServerAccessChecks(t *testing.T) {
	s := newSwarmTestServer(t, nil, "fixture", swarmNoPanes)
	s.server.reads = &swarmScriptedReads{}
	port := strconv.Itoa(s.server.port)
	own := "http://127.0.0.1:" + port

	// Host and Origin: only this loopback listener and this page's origin.
	for name, header := range map[string]map[string]string{
		"foreign host":         {"Host": "example.com:" + port},
		"other port":           {"Host": "127.0.0.1:1"},
		"no port":              {"Host": "127.0.0.1"},
		"rebinding host":       {"Host": "127.0.0.1." + port + ".example.com:" + port},
		"foreign origin":       {"Origin": "http://example.com:" + port},
		"https origin":         {"Origin": "https://127.0.0.1:" + port},
		"other loopback port":  {"Origin": "http://127.0.0.1:1"},
		"origin without port":  {"Origin": "http://127.0.0.1"},
		"null origin":          {"Origin": "null"},
		"localhost other port": {"Origin": "http://localhost:1"},
	} {
		if resp, raw := s.do(http.MethodGet, "/api/auth/session", nil, header); resp.StatusCode != http.StatusForbidden {
			t.Fatalf("%s: %d %s", name, resp.StatusCode, raw)
		}
		if resp, _ := s.do(http.MethodGet, "/", nil, header); resp.StatusCode != http.StatusForbidden {
			t.Fatalf("%s reached the page: %d", name, resp.StatusCode)
		}
	}
	for name, header := range map[string]map[string]string{
		"plain":            nil,
		"localhost host":   {"Host": "localhost:" + port},
		"own origin":       {"Origin": own},
		"localhost origin": {"Origin": "http://localhost:" + port},
		"same-origin":      {"Sec-Fetch-Site": "same-origin"},
	} {
		if resp, raw := s.do(http.MethodGet, "/api/auth/session", nil, header); resp.StatusCode != http.StatusOK || !strings.Contains(string(raw), `"authenticated":false`) {
			t.Fatalf("%s: %d %s", name, resp.StatusCode, raw)
		}
	}
	// Another page of this machine is same-site, not same-origin.
	for _, site := range []string{"same-site", "cross-site", "none"} {
		if resp, raw := s.do(http.MethodGet, "/api/auth/session", nil, map[string]string{"Sec-Fetch-Site": site}); resp.StatusCode != http.StatusForbidden || swarmErrorCode(raw) != "AUTHORITY_DENIED" {
			t.Fatalf("Sec-Fetch-Site %s: %d %s", site, resp.StatusCode, raw)
		}
	}

	// The page itself, with headers that keep it out of a frame.
	resp, raw := s.do(http.MethodGet, "/", nil, map[string]string{"Sec-Fetch-Site": "none"})
	if resp.StatusCode != http.StatusOK || !strings.Contains(string(raw), "<title>") || resp.Header.Get("X-Frame-Options") != "DENY" || !strings.Contains(resp.Header.Get("Content-Security-Policy"), "default-src 'self'") || resp.Header.Get("X-Content-Type-Options") != "nosniff" {
		t.Fatalf("page: %d %v", resp.StatusCode, resp.Header)
	}
	for _, asset := range []string{"/app.js", "/styles.css"} {
		if resp, raw := s.do(http.MethodGet, asset, nil, nil); resp.StatusCode != http.StatusOK || len(raw) == 0 {
			t.Fatalf("%s: %d", asset, resp.StatusCode)
		}
	}
	if resp, _ := s.do(http.MethodGet, "/nothing", nil, nil); resp.StatusCode != http.StatusNotFound {
		t.Fatalf("unknown path: %d", resp.StatusCode)
	}

	// Nothing but sign-in answers without a session.
	for _, path := range []string{"/api/swarm/landing", "/api/swarm/campaign?campaign=alpha", "/api/swarm/stream?campaign=alpha", "/api/swarm/panes", "/api/swarm/event", "/api/swarm/action/send", "/api/auth/logout"} {
		if resp, raw := s.do(http.MethodGet, path, nil, nil); resp.StatusCode != http.StatusUnauthorized || swarmErrorCode(raw) != "UNAUTHENTICATED" {
			t.Fatalf("%s without a session: %d %s", path, resp.StatusCode, raw)
		}
	}

	// Sign-in: the token, exactly.
	for name, body := range map[string]any{
		"wrong token":    map[string]string{"token": swarmTestToken[:len(swarmTestToken)-1] + "0"},
		"prefix":         map[string]string{"token": swarmTestToken[:10]},
		"empty":          map[string]string{"token": ""},
		"token as array": map[string]any{"token": []string{swarmTestToken}},
	} {
		resp, raw := s.do(http.MethodPost, "/api/auth/login", body, nil)
		if resp.StatusCode != http.StatusUnauthorized && resp.StatusCode != http.StatusBadRequest {
			t.Fatalf("%s: %d %s", name, resp.StatusCode, raw)
		}
		if len(resp.Cookies()) != 0 {
			t.Fatalf("%s set a cookie", name)
		}
	}
	if resp, raw := s.do(http.MethodPost, "/api/auth/login", map[string]string{"token": swarmTestToken, "extra": "x"}, nil); resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("unknown login field: %d %s", resp.StatusCode, raw)
	}
	if resp, _ := s.do(http.MethodGet, "/api/auth/login", nil, nil); resp.StatusCode != http.StatusMethodNotAllowed {
		t.Fatalf("GET login: %d", resp.StatusCode)
	}
	resp, raw = s.do(http.MethodPost, "/api/auth/login", map[string]string{"token": swarmTestToken}, nil)
	if resp.StatusCode != http.StatusOK || len(resp.Cookies()) != 1 {
		t.Fatalf("login: %d %s", resp.StatusCode, raw)
	}
	cookie := resp.Cookies()[0]
	if cookie.Name != "cairn_swarm_"+port || !cookie.HttpOnly || cookie.SameSite != http.SameSiteStrictMode || cookie.Path != "/" || len(cookie.Value) != 48 {
		t.Fatalf("session cookie: %+v", cookie)
	}
	var login struct {
		CSRF string `json:"csrf_token"`
	}
	if err := json.Unmarshal(raw, &login); err != nil || len(login.CSRF) != 32 {
		t.Fatalf("csrf token: %s", raw)
	}
	if resp, raw := s.do(http.MethodGet, "/api/auth/session", nil, nil); !strings.Contains(string(raw), `"authenticated":true`) || !strings.Contains(string(raw), login.CSRF) || !strings.Contains(string(raw), `"collection":"fixture"`) {
		t.Fatalf("session: %d %s", resp.StatusCode, raw)
	}
	if resp, raw := s.do(http.MethodGet, "/api/swarm/landing", nil, nil); resp.StatusCode != http.StatusOK || !strings.Contains(string(raw), core.SwarmLandingSchema) {
		t.Fatalf("landing with a session: %d %s", resp.StatusCode, raw)
	}

	// A cookie of the view on another port is not this view's session.
	bare := &http.Client{}
	request, _ := http.NewRequest(http.MethodGet, s.http.URL+"/api/swarm/landing", nil)
	request.AddCookie(&http.Cookie{Name: "cairn_swarm_" + strconv.Itoa(s.server.port+1), Value: cookie.Value})
	if other, err := bare.Do(request); err != nil || other.StatusCode != http.StatusUnauthorized {
		t.Fatalf("cookie named for another port: %v %v", other, err)
	}

	// The same token can sign in more than one browser.
	second := newSwarmTestServer(t, nil, "fixture", swarmNoPanes)
	second.login()
	second.login()

	// A request that changes state needs the session's CSRF token.
	for name, header := range map[string]map[string]string{
		"no token":    nil,
		"wrong token": {"X-CSRF-Token": strings.Repeat("0", 32)},
		"other form":  {"X-CSRF-Token": strings.ToUpper(login.CSRF)},
	} {
		if resp, raw := s.do(http.MethodPost, "/api/auth/logout", nil, header); resp.StatusCode != http.StatusForbidden || swarmErrorCode(raw) != "AUTHORITY_DENIED" {
			t.Fatalf("%s: %d %s", name, resp.StatusCode, raw)
		}
		if resp, raw := s.do(http.MethodPost, "/api/swarm/action/send", map[string]string{}, header); resp.StatusCode != http.StatusForbidden {
			t.Fatalf("action with %s: %d %s", name, resp.StatusCode, raw)
		}
	}
	if resp, raw := s.do(http.MethodGet, "/api/swarm/landing", nil, nil); resp.StatusCode != http.StatusOK {
		t.Fatalf("a refused request ended the session: %d %s", resp.StatusCode, raw)
	}

	// A session ends at logout and at its lifetime.
	if resp, raw := s.do(http.MethodPost, "/api/auth/logout", nil, map[string]string{"X-CSRF-Token": login.CSRF}); resp.StatusCode != http.StatusOK {
		t.Fatalf("logout: %d %s", resp.StatusCode, raw)
	}
	if resp, _ := s.do(http.MethodGet, "/api/swarm/landing", nil, nil); resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("after logout: %d", resp.StatusCode)
	}
	s.login()
	later := time.Now().Add(swarmSessionLifetime + time.Minute)
	s.server.now = func() time.Time { return later }
	if resp, _ := s.do(http.MethodGet, "/api/swarm/landing", nil, nil); resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("after the session lifetime: %d", resp.StatusCode)
	}
	s.server.now = time.Now

	// The number of sessions is bounded; the oldest goes first.
	for i := 0; i < swarmSessionLimit+5; i++ {
		s.login()
	}
	s.server.mu.Lock()
	sessions := len(s.server.sessions)
	s.server.mu.Unlock()
	if sessions > swarmSessionLimit {
		t.Fatalf("%d sessions kept; the limit is %d", sessions, swarmSessionLimit)
	}
}

func TestSwarmTokenFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "swarm-view.token")
	token, err := readOrCreateSwarmToken(path)
	if err != nil || !swarmTokenPattern.MatchString(token) || len(token) != 64 {
		t.Fatalf("create: %q %v", token, err)
	}
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm() != 0o600 {
		t.Fatalf("token file mode: %v %v", info, err)
	}
	again, err := readOrCreateSwarmToken(path)
	if err != nil || again != token {
		t.Fatalf("second read: %q %v", again, err)
	}
	refuse := func(name, path string) {
		t.Helper()
		if _, err := readOrCreateSwarmToken(path); err == nil || core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("%s was accepted: %v", name, err)
		}
	}
	for _, mode := range []os.FileMode{0o640, 0o604, 0o660, 0o644} {
		if err = os.Chmod(path, mode); err != nil {
			t.Fatal(err)
		}
		refuse(fmt.Sprintf("mode %o", mode), path)
	}
	if err = os.Chmod(path, 0o600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(dir, "link.token")
	if err = os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	refuse("a symbolic link", link)
	refuse("a directory", dir)
	refuse("a relative path", "swarm-view.token")
	for name, content := range map[string]string{"short": "abc\n", "empty": "", "two lines": token + "\n" + token + "\n", "space inside": token[:20] + " " + token[20:]} {
		other := filepath.Join(dir, strings.ReplaceAll(name, " ", "-"))
		if err = os.WriteFile(other, []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
		refuse(name+" content", other)
	}
	// The default location is the Cairn state directory.
	t.Setenv("CAIRN_HOME", dir)
	if got, err := swarmTokenPath(""); err != nil || got != path {
		t.Fatalf("default token path: %q %v", got, err)
	}
	if out, err := runSwarmURL([]string{"--port", "8123"}); err != nil || out.(map[string]any)["url"] != "http://127.0.0.1:8123/#token="+token {
		t.Fatalf("swarm url: %v %v", out, err)
	}
}

func swarmScriptedEvent(position int64) core.SwarmEvent {
	return core.SwarmEvent{Position: position, EventID: fmt.Sprintf("event-%d", position), Kind: "notice", From: "agent/a", Cursor: fmt.Sprintf("cursor-%d", position)}
}

func swarmScriptedSnapshot(revision int64) core.SwarmSnapshot {
	return core.SwarmSnapshot{Schema: core.SwarmSnapshotSchema, ObservedAt: time.Now(), MembershipEpoch: "epoch", Campaign: core.TeamCampaign{Campaign: "alpha", Revision: revision}}
}

// The order of events on one connection, the stream IDs, and what a refused
// or failed read does.
func TestSwarmStreamProtocol(t *testing.T) {
	s := newSwarmTestServer(t, nil, "fixture", swarmNoPanes)
	failing := &core.Error{Code: "STALE_CURSOR", Message: "swarm cursor predates the restore fence; reload the page"}
	reads := &swarmScriptedReads{next: func(call int, req core.SwarmStreamRequest) (core.SwarmStreamRead, error) {
		switch call {
		case 0: // a new window of two events, cut at the batch size
			return core.SwarmStreamRead{Resync: true, ResyncReason: "new_window", Events: []core.SwarmEvent{swarmScriptedEvent(1), swarmScriptedEvent(2)}, More: true, Cursor: "cursor-2", Snapshot: swarmScriptedSnapshot(1)}, nil
		case 1: // the rest of the batch, read at once; the state is unchanged
			return core.SwarmStreamRead{Events: []core.SwarmEvent{swarmScriptedEvent(3)}, Cursor: "cursor-9", Snapshot: swarmScriptedSnapshot(1)}, nil
		case 2: // nothing new
			return core.SwarmStreamRead{Cursor: "cursor-9", Snapshot: swarmScriptedSnapshot(1)}, nil
		case 3: // the store cannot be read
			return core.SwarmStreamRead{}, errors.New("connection refused")
		case 4: // it can again, and the state changed
			return core.SwarmStreamRead{Cursor: "cursor-9", Snapshot: swarmScriptedSnapshot(2)}, nil
		case 5: // a member joined
			return core.SwarmStreamRead{Resync: true, ResyncReason: "membership_changed", Events: []core.SwarmEvent{swarmScriptedEvent(1), swarmScriptedEvent(2), swarmScriptedEvent(3), swarmScriptedEvent(12)}, Cursor: "cursor-12", Snapshot: swarmScriptedSnapshot(3)}, nil
		}
		return core.SwarmStreamRead{}, failing
	}}
	s.server.reads = reads
	s.login()
	conn := s.stream("alpha", "", nil)
	if conn.Status != http.StatusOK {
		t.Fatalf("stream: %d %s", conn.Status, conn.Body)
	}
	var got []string
	paneEvents := 0
	for {
		event := conn.next(t)
		if event.Event == "herdr" || event.Event == "window" {
			// The host observations come from their own loops, so when
			// they arrive is not part of the order checked here.
			paneEvents++
			continue
		}
		got = append(got, strings.TrimSpace(event.Event+" "+event.ID))
		if event.Event == "reset" {
			if !strings.Contains(event.Data, "STALE_CURSOR") {
				t.Fatalf("reset data: %s", event.Data)
			}
			break
		}
		if event.Event == "fault" && !strings.Contains(event.Data, "connection refused") {
			t.Fatalf("fault data: %s", event.Data)
		}
	}
	want := []string{
		"hello",
		"resync", "log cursor-1", "log cursor-2", "snapshot cursor-2", "tick cursor-2",
		"log cursor-3", "tick cursor-9",
		"tick cursor-9",
		"fault",
		"snapshot cursor-9", "tick cursor-9",
		"resync", "log cursor-1", "log cursor-2", "log cursor-3", "log cursor-12", "snapshot cursor-12", "tick cursor-12",
		"reset",
	}
	if strings.Join(got, "|") != strings.Join(want, "|") {
		t.Fatalf("stream events:\n got %v\nwant %v", got, want)
	}
	if paneEvents == 0 {
		t.Fatal("the stream sent no pane observation")
	}
	if _, open := <-conn.Events; open {
		t.Fatal("the stream stayed open after reset")
	}
	// Every read after the first continues from the cursor of the read before.
	if cursors := reads.cursors(); strings.Join(cursors, "|") != "|cursor-2|cursor-9|cursor-9|cursor-9|cursor-9|cursor-12" {
		t.Fatalf("cursors asked for: %v", cursors)
	}

	// A reconnecting browser sends the last stream ID it received.
	reads = &swarmScriptedReads{next: func(call int, req core.SwarmStreamRequest) (core.SwarmStreamRead, error) {
		return core.SwarmStreamRead{Cursor: req.Cursor, Snapshot: swarmScriptedSnapshot(1)}, nil
	}}
	s.server.reads = reads
	conn = s.stream("alpha", "cursor-7", nil)
	conn.until(t, "tick")
	conn.close()
	if cursors := reads.cursors(); cursors[0] != "cursor-7" {
		t.Fatalf("the first read ignored Last-Event-ID: %v", cursors)
	}

	// A refused first read is still an event stream. A browser's EventSource
	// gives up for good on any other answer and cannot read why, so a
	// reconnect with a cursor from before a restore would strand the page.
	for name, refusal := range map[string]*core.Error{
		"stale cursor":     failing,
		"unknown campaign": {Code: "NOT_FOUND", Message: "team campaign not found"},
		"malformed cursor": {Code: "INVALID_REQUEST", Message: "invalid swarm cursor"},
	} {
		s.server.reads = &swarmScriptedReads{next: func(int, core.SwarmStreamRequest) (core.SwarmStreamRead, error) {
			return core.SwarmStreamRead{}, refusal
		}}
		conn = s.stream("alpha", "old", nil)
		if conn.Status != http.StatusOK {
			t.Fatalf("%s at connect: status %d %s", name, conn.Status, conn.Body)
		}
		if first := conn.next(t); first.Event != "hello" {
			t.Fatalf("%s at connect: first event %q", name, first.Event)
		}
		if reset := conn.next(t); reset.Event != "reset" || !strings.Contains(reset.Data, `"code":"`+refusal.Code+`"`) {
			t.Fatalf("%s at connect: %q %s", name, reset.Event, reset.Data)
		}
		if _, open := <-conn.Events; open {
			t.Fatalf("%s at connect: the stream stayed open", name)
		}
	}
	// A first read that fails because the store is down is a fault, and the
	// same connection delivers the window once the store answers.
	s.server.reads = &swarmScriptedReads{next: func(call int, req core.SwarmStreamRequest) (core.SwarmStreamRead, error) {
		if call < 2 {
			return core.SwarmStreamRead{}, errors.New("connection refused")
		}
		return core.SwarmStreamRead{Resync: true, ResyncReason: "new_window", Events: []core.SwarmEvent{swarmScriptedEvent(1)}, Cursor: "cursor-1", Snapshot: swarmScriptedSnapshot(1)}, nil
	}}
	conn = s.stream("alpha", "", nil)
	var recovered []string
	for {
		event := conn.next(t)
		if event.Event == "herdr" || event.Event == "window" {
			continue
		}
		recovered = append(recovered, event.Event)
		if event.Event == "tick" {
			break
		}
	}
	conn.close()
	if strings.Join(recovered, "|") != "hello|fault|fault|resync|log|snapshot|tick" {
		t.Fatalf("store down at connect: %v", recovered)
	}
	// The stream is a GET with no Origin header; Sec-Fetch-Site keeps
	// another local page out.
	if conn = s.stream("alpha", "", map[string]string{"Sec-Fetch-Site": "same-site"}); conn.Status != http.StatusForbidden {
		t.Fatalf("stream from another local page: %d %s", conn.Status, conn.Body)
	}
	if resp, _ := s.do(http.MethodPost, "/api/swarm/stream?campaign=alpha", nil, map[string]string{"X-CSRF-Token": s.csrf}); resp.StatusCode != http.StatusMethodNotAllowed {
		t.Fatalf("POST stream: %d", resp.StatusCode)
	}
}

// The Herdr read runs on its own loop. While it hangs, the store is still
// read every tick and only the pane data is marked stale. The reads stop when
// the last browser leaves.
func TestSwarmStreamPaneLoopIsIndependent(t *testing.T) {
	release := make(chan struct{})
	var mu sync.Mutex
	calls := 0
	panes := func(ctx context.Context) swarmPaneObservation {
		mu.Lock()
		calls++
		call := calls
		mu.Unlock()
		if call == 2 {
			// Herdr hangs.
			select {
			case <-release:
			case <-ctx.Done():
			}
		}
		return swarmPaneObservation{ObservedAt: time.Now(), Status: "ok", Sessions: 1, Panes: map[string]swarmPane{"agent-uuid": {PaneID: fmt.Sprintf("w1:p%d", call), AgentStatus: "working", FocusCommand: "herdr agent focus w1:p1"}}}
	}
	s := newSwarmTestServer(t, nil, "fixture", panes)
	s.server.panes = newSwarmPaneLoop(panes, 10*time.Millisecond, 80*time.Millisecond)
	var revision int64 = 1
	s.server.reads = &swarmScriptedReads{next: func(call int, req core.SwarmStreamRequest) (core.SwarmStreamRead, error) {
		mu.Lock()
		defer mu.Unlock()
		return core.SwarmStreamRead{Cursor: "cursor", Snapshot: swarmScriptedSnapshot(revision)}, nil
	}}
	s.login()
	conn := s.stream("alpha", "", nil)

	type tick struct {
		Panes struct {
			Status string `json:"status"`
			Stale  bool   `json:"stale"`
		} `json:"panes"`
	}
	// The first observation arrives, then the second read hangs and the
	// observation ages.
	sawPane := false
	for {
		event := conn.next(t)
		if event.Event == "herdr" && strings.Contains(event.Data, `"w1:p1"`) {
			sawPane = true
		}
		if event.Event == "tick" {
			var body tick
			if err := json.Unmarshal([]byte(event.Data), &body); err != nil {
				t.Fatal(err)
			}
			if sawPane && body.Panes.Stale {
				break
			}
		}
	}
	// The store state changes while Herdr is still hung: the snapshot
	// arrives, and the tick still says the pane data is stale.
	mu.Lock()
	revision = 2
	mu.Unlock()
	snapshot, before := conn.until(t, "snapshot")
	if !strings.Contains(snapshot.Data, `"revision":2`) {
		t.Fatalf("snapshot after the change: %s", snapshot.Data)
	}
	for _, event := range before {
		if event.Event == "herdr" && !strings.Contains(event.Data, `"stale":true`) {
			t.Fatalf("pane data changed while Herdr was hung: %s", event.Data)
		}
	}
	next, _ := conn.until(t, "tick")
	var body tick
	if err := json.Unmarshal([]byte(next.Data), &body); err != nil || !body.Panes.Stale || body.Panes.Status != "ok" {
		t.Fatalf("tick while Herdr is hung: %s %v", next.Data, err)
	}
	// Herdr answers again: the pane data is fresh and current.
	close(release)
	for {
		event := conn.next(t)
		if event.Event == "herdr" && strings.Contains(event.Data, `"stale":false`) && !strings.Contains(event.Data, `"w1:p1"`) {
			break
		}
	}

	// With no browser connected, neither the store nor Herdr is read.
	conn.close()
	deadline := time.Now().Add(5 * time.Second)
	for {
		s.server.panes.mu.Lock()
		users := s.server.panes.users
		s.server.panes.mu.Unlock()
		if users == 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("the pane loop still has a user after the browser left")
		}
		time.Sleep(5 * time.Millisecond)
	}
	time.Sleep(60 * time.Millisecond)
	mu.Lock()
	paneCalls := calls
	mu.Unlock()
	storeCalls := len(s.server.reads.(*swarmScriptedReads).cursors())
	time.Sleep(150 * time.Millisecond)
	mu.Lock()
	laterPaneCalls := calls
	mu.Unlock()
	if laterStoreCalls := len(s.server.reads.(*swarmScriptedReads).cursors()); laterStoreCalls != storeCalls || laterPaneCalls != paneCalls {
		t.Fatalf("reads continued with no browser: store %d -> %d, Herdr %d -> %d", storeCalls, laterStoreCalls, paneCalls, laterPaneCalls)
	}
}

// A stream lives no longer than the session that opened it: it ends at
// logout, at the session's lifetime and when the session limit evicts the
// session, and nothing is read for it afterwards.
func TestSwarmStreamEndsWithItsSession(t *testing.T) {
	open := func(t *testing.T) (*swarmTestServer, *swarmScriptedReads, *swarmStreamConn) {
		t.Helper()
		s := newSwarmTestServer(t, nil, "fixture", swarmNoPanes)
		reads := &swarmScriptedReads{next: func(call int, req core.SwarmStreamRequest) (core.SwarmStreamRead, error) {
			return core.SwarmStreamRead{Cursor: "cursor", Snapshot: swarmScriptedSnapshot(int64(call))}, nil
		}}
		s.server.reads = reads
		s.login()
		conn := s.stream("alpha", "", nil)
		if conn.Status != http.StatusOK {
			t.Fatalf("stream: %d %s", conn.Status, conn.Body)
		}
		conn.until(t, "tick")
		return s, reads, conn
	}
	ended := func(t *testing.T, reads *swarmScriptedReads, conn *swarmStreamConn) {
		t.Helper()
		reset, _ := conn.until(t, "reset")
		if !strings.Contains(reset.Data, `"code":"UNAUTHENTICATED"`) {
			t.Fatalf("reset: %s", reset.Data)
		}
		if _, open := <-conn.Events; open {
			t.Fatal("the stream stayed open after its session ended")
		}
		before := len(reads.cursors())
		time.Sleep(120 * time.Millisecond)
		if after := len(reads.cursors()); after != before {
			t.Fatalf("the store was read %d more times for an ended session", after-before)
		}
	}
	t.Run("logout", func(t *testing.T) {
		s, reads, conn := open(t)
		if resp, raw := s.do(http.MethodPost, "/api/auth/logout", nil, map[string]string{"X-CSRF-Token": s.csrf}); resp.StatusCode != http.StatusOK {
			t.Fatalf("logout: %d %s", resp.StatusCode, raw)
		}
		ended(t, reads, conn)
		if again := s.stream("alpha", "", nil); again.Status != http.StatusUnauthorized {
			t.Fatalf("a new stream after logout: %d", again.Status)
		}
	})
	t.Run("lifetime", func(t *testing.T) {
		s, reads, conn := open(t)
		// The session reaches its lifetime while the stream is open.
		s.server.mu.Lock()
		for id, session := range s.server.sessions {
			session.expires = time.Now().Add(-time.Second)
			s.server.sessions[id] = session
		}
		s.server.mu.Unlock()
		ended(t, reads, conn)
	})
	t.Run("evicted by the session limit", func(t *testing.T) {
		s, reads, conn := open(t)
		for i := 0; i < swarmSessionLimit; i++ {
			// Other browsers sign in; each takes a session of its own.
			fresh := &swarmTestServer{t: t, server: s.server, http: s.http, client: &http.Client{}}
			resp, raw := fresh.do(http.MethodPost, "/api/auth/login", map[string]string{"token": swarmTestToken}, nil)
			if resp.StatusCode != http.StatusOK {
				t.Fatalf("login %d: %d %s", i, resp.StatusCode, raw)
			}
		}
		ended(t, reads, conn)
	})
	t.Run("logout while a read is waiting on the store", func(t *testing.T) {
		s := newSwarmTestServer(t, nil, "fixture", swarmNoPanes)
		blocked := &swarmBlockingReads{after: 1, entered: make(chan struct{}, 1), cancelled: make(chan struct{})}
		blocked.swarmScriptedReads = &swarmScriptedReads{next: func(call int, req core.SwarmStreamRequest) (core.SwarmStreamRead, error) {
			return core.SwarmStreamRead{Cursor: "cursor", Snapshot: swarmScriptedSnapshot(1)}, nil
		}}
		s.server.reads = blocked
		s.login()
		conn := s.stream("alpha", "", nil)
		conn.until(t, "tick")
		<-blocked.entered // the second read is waiting on the store
		if resp, raw := s.do(http.MethodPost, "/api/auth/logout", nil, map[string]string{"X-CSRF-Token": s.csrf}); resp.StatusCode != http.StatusOK {
			t.Fatalf("logout: %d %s", resp.StatusCode, raw)
		}
		// The waiting read is cancelled, and its failure is not reported as
		// a store fault to a session that no longer exists.
		for {
			event := conn.next(t)
			if event.Event == "fault" || event.Event == "snapshot" || event.Event == "log" {
				t.Fatalf("%s sent after logout: %s", event.Event, event.Data)
			}
			if event.Event == "reset" {
				if !strings.Contains(event.Data, `"code":"UNAUTHENTICATED"`) {
					t.Fatalf("reset: %s", event.Data)
				}
				break
			}
		}
		select {
		case <-blocked.cancelled:
		case <-time.After(5 * time.Second):
			t.Fatal("the read that was waiting on the store was not cancelled")
		}
	})
	t.Run("logout in the middle of a batch", func(t *testing.T) {
		s := newSwarmTestServer(t, nil, "fixture", swarmNoPanes)
		const batch = 20000
		events := make([]core.SwarmEvent, batch)
		for i := range events {
			events[i] = swarmScriptedEvent(int64(i + 1))
			events[i].Summary = strings.Repeat("x", 200)
		}
		s.server.reads = &swarmScriptedReads{next: func(call int, req core.SwarmStreamRequest) (core.SwarmStreamRead, error) {
			return core.SwarmStreamRead{Resync: true, ResyncReason: "new_window", Events: events, Cursor: "cursor", Snapshot: swarmScriptedSnapshot(1)}, nil
		}}
		s.login()
		conn := s.stream("alpha", "", nil)
		conn.until(t, "log")
		if resp, raw := s.do(http.MethodPost, "/api/auth/logout", nil, map[string]string{"X-CSRF-Token": s.csrf}); resp.StatusCode != http.StatusOK {
			t.Fatalf("logout: %d %s", resp.StatusCode, raw)
		}
		logs := 1
		for {
			event := conn.next(t)
			if event.Event == "log" {
				logs++
				continue
			}
			if event.Event != "reset" || !strings.Contains(event.Data, `"code":"UNAUTHENTICATED"`) {
				t.Fatalf("after logout mid-batch: %s %s", event.Event, event.Data)
			}
			break
		}
		// Frames already in flight may arrive; the rest of the batch, the
		// snapshot and the host observations must not.
		if logs >= batch {
			t.Fatalf("the whole batch of %d was sent after logout", batch)
		}
	})
	t.Run("another session's logout", func(t *testing.T) {
		s, _, conn := open(t)
		other := &swarmTestServer{t: t, server: s.server, http: s.http, client: &http.Client{}}
		jar, _ := cookiejar.New(nil)
		other.client.Jar = jar
		other.login()
		if resp, raw := other.do(http.MethodPost, "/api/auth/logout", nil, map[string]string{"X-CSRF-Token": other.csrf}); resp.StatusCode != http.StatusOK {
			t.Fatalf("logout of the other session: %d %s", resp.StatusCode, raw)
		}
		for i := 0; i < 3; i++ {
			if event, _ := conn.until(t, "tick"); event.Event != "tick" {
				t.Fatalf("the stream of the first session ended: %s", event.Event)
			}
		}
	})
}

// The context-window observation is a third observation with its own loop
// and its own time: it reaches the page as its own event and in every tick.
func TestSwarmStreamCarriesTheWindowObservation(t *testing.T) {
	s := newSwarmTestServer(t, nil, "fixture", swarmNoPanes)
	s.server.windows = newSwarmWindowLoop(func(ctx context.Context) swarmWindowObservation {
		return swarmWindowObservation{Status: "ok", Sessions: 2, NearLimitRule: "rule", Unknown: map[string]string{"agent-b": "no usage records"},
			Windows: map[string]swarmWindow{"agent-a": {UsedTokens: 181948, WindowTokens: 258400, Model: "gpt", MeasuredAt: time.Now(), Source: "codex rollout", NearLimit: false}}}
	}, 10*time.Millisecond, time.Minute)
	s.server.reads = &swarmScriptedReads{next: func(int, core.SwarmStreamRequest) (core.SwarmStreamRead, error) {
		return core.SwarmStreamRead{Cursor: "cursor", Snapshot: swarmScriptedSnapshot(1)}, nil
	}}
	s.login()
	conn := s.stream("alpha", "", nil)
	defer conn.close()
	for {
		event := conn.next(t)
		if event.Event != "window" || !strings.Contains(event.Data, `"status":"ok"`) {
			continue
		}
		var view swarmWindowView
		if err := json.Unmarshal([]byte(event.Data), &view); err != nil {
			t.Fatal(err)
		}
		if view.Windows["agent-a"].UsedTokens != 181948 || view.Windows["agent-a"].WindowTokens != 258400 || view.Unknown["agent-b"] == "" || view.Stale || view.ObservedAt.IsZero() {
			t.Fatalf("window event: %s", event.Data)
		}
		break
	}
	tick, _ := conn.until(t, "tick")
	if !strings.Contains(tick.Data, `"window":{`) || !strings.Contains(tick.Data, `"panes":{`) || !strings.Contains(tick.Data, `"db_observed_at"`) {
		t.Fatalf("tick does not carry each observation's time: %s", tick.Data)
	}
	resp, raw := s.do(http.MethodGet, "/api/swarm/panes", nil, nil)
	if resp.StatusCode != http.StatusOK || !strings.Contains(string(raw), `"herdr":{`) || !strings.Contains(string(raw), `"window":{`) {
		t.Fatalf("host observations route: %d %s", resp.StatusCode, raw)
	}
}

// swarmBlockingReads lets the first reads through and then waits on the
// store until the read's context ends.
type swarmBlockingReads struct {
	*swarmScriptedReads
	after     int
	entered   chan struct{}
	cancelled chan struct{}
	once      sync.Once
}

func (r *swarmBlockingReads) ReadSwarmStream(ctx context.Context, req core.SwarmStreamRequest) (core.SwarmStreamRead, error) {
	if len(r.swarmScriptedReads.cursors()) < r.after {
		return r.swarmScriptedReads.ReadSwarmStream(ctx, req)
	}
	select {
	case r.entered <- struct{}{}:
	default:
	}
	<-ctx.Done()
	r.once.Do(func() { close(r.cancelled) })
	return core.SwarmStreamRead{}, ctx.Err()
}
