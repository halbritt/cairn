package claudechannel

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/modelcontextprotocol/go-sdk/jsonrpc"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// recordingConnection records every message the server writes.
type recordingConnection struct {
	mcp.Connection
	mu       sync.Mutex
	messages []jsonrpc.Message
}

func (c *recordingConnection) Write(ctx context.Context, msg jsonrpc.Message) error {
	c.mu.Lock()
	c.messages = append(c.messages, msg)
	c.mu.Unlock()
	return c.Connection.Write(ctx, msg)
}

type recordingTransport struct {
	inner mcp.Transport
	conn  *recordingConnection
}

func (t *recordingTransport) Connect(ctx context.Context) (mcp.Connection, error) {
	conn, err := t.inner.Connect(ctx)
	if err != nil {
		return nil, err
	}
	t.conn = &recordingConnection{Connection: conn}
	return t.conn, nil
}

func (t *recordingTransport) recorded() []jsonrpc.Message {
	t.conn.mu.Lock()
	defer t.conn.mu.Unlock()
	return append([]jsonrpc.Message(nil), t.conn.messages...)
}

// blockingConnection refuses to drain writes of the channel notification,
// standing in for a client that never reads.
type blockingConnection struct {
	mcp.Connection
	closed chan struct{}
	once   sync.Once
}

func (c *blockingConnection) Write(ctx context.Context, msg jsonrpc.Message) error {
	if request, ok := msg.(*jsonrpc.Request); ok && request.Method == notificationMethod {
		select {
		case <-c.closed:
			return errors.New("connection closed")
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	return c.Connection.Write(ctx, msg)
}

func (c *blockingConnection) Close() error {
	c.once.Do(func() { close(c.closed) })
	return c.Connection.Close()
}

type blockingTransport struct{ inner mcp.Transport }

func (t *blockingTransport) Connect(ctx context.Context) (mcp.Connection, error) {
	conn, err := t.inner.Connect(ctx)
	if err != nil {
		return nil, err
	}
	return &blockingConnection{Connection: conn, closed: make(chan struct{})}, nil
}

// rawConnection speaks the newline-delimited JSON-RPC framing directly over a
// pipe, without the SDK client, so tests can perform Claude's legacy
// initialize/initialized handshake by hand.
type rawConnection struct {
	conn   net.Conn
	reader *bufio.Reader
	mu     sync.Mutex
}

func (c *rawConnection) Read(context.Context) (jsonrpc.Message, error) {
	line, err := c.reader.ReadString('\n')
	if err != nil {
		return nil, err
	}
	return jsonrpc.DecodeMessage([]byte(line))
}

func (c *rawConnection) Write(ctx context.Context, msg jsonrpc.Message) error {
	data, err := jsonrpc.EncodeMessage(msg)
	if err != nil {
		return err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	_, err = c.conn.Write(append(data, '\n'))
	return err
}

func (c *rawConnection) Close() error      { return c.conn.Close() }
func (c *rawConnection) SessionID() string { return "" }

type rawTransport struct{ conn net.Conn }

func (t *rawTransport) Connect(context.Context) (mcp.Connection, error) {
	return &rawConnection{conn: t.conn, reader: bufio.NewReader(t.conn)}, nil
}

type harness struct {
	t          *testing.T
	directory  string
	parent     ProcessRef
	recording  *recordingTransport
	socketPath string
	runDone    chan error
	cancel     context.CancelFunc
}

// startBridge runs the channel over in-memory MCP transports. wrap, when set,
// layers an extra transport between the bridge and the recording transport.
func startBridge(t *testing.T, wrap func(mcp.Transport) mcp.Transport, directory string) *harness {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	serverTransport, clientTransport := mcp.NewInMemoryTransports()
	recording := &recordingTransport{inner: serverTransport}
	transport := mcp.Transport(recording)
	if wrap != nil {
		transport = wrap(recording)
	}
	done := make(chan error, 1)
	go func() { done <- Run(ctx, directory, transport) }()
	client := mcp.NewClient(&mcp.Implementation{Name: "channel-test"}, nil)
	session, err := client.Connect(context.Background(), clientTransport, nil)
	if err != nil {
		cancel()
		t.Fatalf("client initialize failed: %v", err)
	}
	t.Cleanup(func() { _ = session.Close() })
	parent, err := ProcessRefOf(os.Getppid())
	if err != nil {
		cancel()
		t.Fatalf("parent measurement failed: %v", err)
	}
	h := &harness{t: t, directory: directory, parent: parent, recording: recording,
		runDone: done, cancel: cancel}
	registry := filepath.Join(directory, fmt.Sprintf("%d.json", parent.PID))
	h.waitFor(func() bool { _, err := os.Stat(registry); return err == nil }, "registry file")
	data, err := os.ReadFile(registry)
	if err != nil {
		t.Fatalf("registry unreadable: %v", err)
	}
	var record registryRecord
	if err := json.Unmarshal(data, &record); err != nil || record.Schema != registrySchema ||
		record.Parent != parent || record.Socket == "" || !filepath.IsAbs(record.Socket) {
		t.Fatalf("unexpected registry content %s", data)
	}
	if info, err := os.Stat(record.Socket); err != nil || info.Mode()&0o077 != 0 {
		t.Fatalf("socket missing or too open: %v %v", info, err)
	}
	if info, err := os.Stat(directory); err != nil || info.Mode()&0o077 != 0 {
		t.Fatalf("directory too open: %v %v", info, err)
	}
	h.socketPath = record.Socket
	return h
}

func (h *harness) waitFor(condition func() bool, what string) {
	h.t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if condition() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	h.t.Fatalf("timed out waiting for %s", what)
}

func (h *harness) exchange(line string) string {
	h.t.Helper()
	conn, err := net.Dial("unix", h.socketPath)
	if err != nil {
		h.t.Fatalf("dial failed: %v", err)
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(5 * time.Second))
	if _, err := conn.Write([]byte(line + "\n")); err != nil {
		h.t.Fatalf("write failed: %v", err)
	}
	reply, err := bufio.NewReader(conn).ReadString('\n')
	if err != nil {
		h.t.Fatalf("no reply: %v", err)
	}
	var status channelReply
	if err := json.Unmarshal([]byte(strings.TrimSpace(reply)), &status); err != nil {
		h.t.Fatalf("bad reply %q", reply)
	}
	return status.Status
}

func (h *harness) requestLine(content string) string {
	h.t.Helper()
	body, err := json.Marshal(channelRequest{Parent: h.parent, Content: content,
		Meta: channelMeta{NativeSessionID: uuid.NewString(), AgentID: uuid.NewString(),
			ExecutionID: uuid.NewString(), DeliveryID: uuid.NewString()}})
	if err != nil {
		h.t.Fatal(err)
	}
	return string(body)
}

func (h *harness) channelNotifications() []*jsonrpc.Request {
	var found []*jsonrpc.Request
	for _, message := range h.recording.recorded() {
		if request, ok := message.(*jsonrpc.Request); ok && request.Method == notificationMethod {
			found = append(found, request)
		}
	}
	return found
}

func TestLegacyHandshakeCapabilityAndNotification(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	serverConn, clientConn := net.Pipe()
	recording := &recordingTransport{inner: &rawTransport{conn: serverConn}}
	directory := t.TempDir()
	done := make(chan error, 1)
	go func() { done <- Run(ctx, directory, recording) }()
	raw := &rawConnection{conn: clientConn, reader: bufio.NewReader(clientConn)}
	t.Cleanup(func() { _ = raw.Close() })

	// Claude's current client performs the legacy initialize handshake.
	params, err := json.Marshal(map[string]any{
		"protocolVersion": "2025-11-25",
		"capabilities":    map[string]any{},
		"clientInfo":      map[string]string{"name": "claude-raw", "version": "1"},
	})
	if err != nil {
		t.Fatal(err)
	}
	initializeID, err := jsonrpc.MakeID(float64(1))
	if err != nil {
		t.Fatal(err)
	}
	if err := raw.Write(ctx, &jsonrpc.Request{ID: initializeID, Method: "initialize", Params: params}); err != nil {
		t.Fatal(err)
	}
	var initialize *struct {
		ServerInfo struct {
			Name string `json:"name"`
		} `json:"serverInfo"`
		Capabilities struct {
			Experimental map[string]any `json:"experimental"`
		} `json:"capabilities"`
	}
	replies := make(chan jsonrpc.Message, 4)
	go func() {
		for {
			message, err := raw.Read(ctx)
			if err != nil {
				close(replies)
				return
			}
			replies <- message
		}
	}()
	deadline := time.After(5 * time.Second)
	for initialize == nil {
		select {
		case message, ok := <-replies:
			if !ok {
				t.Fatal("connection closed before initialize response")
			}
			if response, ok := message.(*jsonrpc.Response); ok {
				candidate := initialize
				if err := json.Unmarshal(response.Result, &candidate); err == nil && candidate != nil {
					initialize = candidate
				}
			}
		case <-deadline:
			t.Fatal("no initialize response")
		}
	}
	if initialize.ServerInfo.Name != serverName {
		t.Fatalf("server name %q", initialize.ServerInfo.Name)
	}
	if _, ok := initialize.Capabilities.Experimental[capability]; !ok {
		t.Fatalf("experimental capability missing: %+v", initialize.Capabilities)
	}
	if err := raw.Write(ctx, &jsonrpc.Request{Method: "notifications/initialized"}); err != nil {
		t.Fatal(err)
	}

	// The registry and socket appear only after initialization.
	parent, err := ProcessRefOf(os.Getppid())
	if err != nil {
		t.Fatal(err)
	}
	registry := filepath.Join(directory, fmt.Sprintf("%d.json", parent.PID))
	for range 500 {
		if _, err := os.Stat(registry); err == nil {
			break
		}
		select {
		case <-time.After(10 * time.Millisecond):
		case <-deadline:
			t.Fatal("registry never appeared after initialized")
		}
	}
	data, err := os.ReadFile(registry)
	if err != nil {
		t.Fatal(err)
	}
	var record registryRecord
	if json.Unmarshal(data, &record) != nil || record.Socket == "" {
		t.Fatalf("unexpected registry %s", data)
	}

	// A socket request reaches the raw client as a channel notification.
	body, err := json.Marshal(channelRequest{Parent: parent, Content: "legacy probe",
		Meta: channelMeta{NativeSessionID: uuid.NewString(), AgentID: uuid.NewString(),
			ExecutionID: uuid.NewString(), DeliveryID: uuid.NewString()}})
	if err != nil {
		t.Fatal(err)
	}
	socket, err := net.Dial("unix", record.Socket)
	if err != nil {
		t.Fatal(err)
	}
	defer socket.Close()
	_ = socket.SetDeadline(time.Now().Add(5 * time.Second))
	if _, err := socket.Write(append(body, '\n')); err != nil {
		t.Fatal(err)
	}
	status, err := bufio.NewReader(socket).ReadString('\n')
	if err != nil || !strings.Contains(status, "written") {
		t.Fatalf("socket reply %q err %v", status, err)
	}
	for {
		select {
		case message, ok := <-replies:
			if !ok {
				t.Fatal("connection closed before the channel notification")
			}
			request, ok := message.(*jsonrpc.Request)
			if !ok || request.Method != notificationMethod {
				continue
			}
			if request.ID != (jsonrpc.ID{}) {
				t.Fatalf("channel message is not a notification: %+v", request.ID)
			}
			var received struct {
				Content string `json:"content"`
			}
			if err := json.Unmarshal(request.Params, &received); err != nil || received.Content != "legacy probe" {
				t.Fatalf("notification params %s err %v", request.Params, err)
			}
			return
		case <-deadline:
			t.Fatal("channel notification never arrived")
		}
	}
}

func TestValidRequestIsWrittenAndEmittedUnchanged(t *testing.T) {
	h := startBridge(t, nil, t.TempDir())
	defer h.cancel()
	if status := h.exchange(h.requestLine("probe payload")); status != "written" {
		t.Fatalf("status %q", status)
	}
	h.waitFor(func() bool { return len(h.channelNotifications()) == 1 }, "channel notification")
	notifications := h.channelNotifications()
	if len(notifications) != 1 {
		t.Fatalf("recorded %d channel notifications", len(notifications))
	}
	sent := notifications[0]
	if sent.ID != (jsonrpc.ID{}) {
		t.Fatalf("channel message is not a notification: %+v", sent.ID)
	}
	var params struct {
		Content string      `json:"content"`
		Meta    channelMeta `json:"meta"`
	}
	if err := json.Unmarshal(sent.Params, &params); err != nil {
		t.Fatalf("params undecodable: %v", err)
	}
	if params.Content != "probe payload" {
		t.Fatalf("content changed: %q", params.Content)
	}
	if !validMeta(params.Meta) {
		t.Fatalf("meta changed: %+v", params.Meta)
	}
}

func TestChangedParentIsRefusedWithoutSending(t *testing.T) {
	h := startBridge(t, nil, t.TempDir())
	defer h.cancel()
	for _, changed := range []ProcessRef{
		{PID: h.parent.PID + 1, Start: h.parent.Start, Boot: h.parent.Boot},
		{PID: h.parent.PID, Start: h.parent.Start + 1, Boot: h.parent.Boot},
		{PID: h.parent.PID, Start: h.parent.Start, Boot: "00000000-0000-0000-0000-000000000000"},
	} {
		body, _ := json.Marshal(channelRequest{Parent: changed, Content: "x", Meta: channelMeta{
			NativeSessionID: uuid.NewString(), AgentID: uuid.NewString(),
			ExecutionID: uuid.NewString(), DeliveryID: uuid.NewString()}})
		if status := h.exchange(string(body)); status != "unavailable" {
			t.Fatalf("changed parent %v accepted: %q", changed, status)
		}
	}
	if notifications := h.channelNotifications(); len(notifications) != 0 {
		t.Fatalf("refused requests still emitted %d notifications", len(notifications))
	}
}

func TestMalformedAndOversizedInputIsRefused(t *testing.T) {
	h := startBridge(t, nil, t.TempDir())
	defer h.cancel()
	if status := h.exchange("not json at all"); status != "unavailable" {
		t.Fatalf("malformed status %q", status)
	}
	empty, _ := json.Marshal(channelRequest{Parent: h.parent, Content: "x", Meta: channelMeta{}})
	if status := h.exchange(string(empty)); status != "unavailable" {
		t.Fatalf("invalid meta status %q", status)
	}
	big := h.requestLine(strings.Repeat("a", maxLine))
	if len(big) <= maxLine {
		t.Fatal("oversized sample is not oversized")
	}
	if status := h.exchange(big); status != "unavailable" {
		t.Fatalf("oversized status %q", status)
	}
	if notifications := h.channelNotifications(); len(notifications) != 0 {
		t.Fatalf("invalid input emitted %d notifications", len(notifications))
	}
	// A line without its newline terminator is refused as well.
	conn, err := net.Dial("unix", h.socketPath)
	if err != nil {
		t.Fatal(err)
	}
	_ = conn.SetDeadline(time.Now().Add(5 * time.Second))
	_, _ = conn.Write([]byte("truncated"))
	_ = conn.Close()
	if notifications := h.channelNotifications(); len(notifications) != 0 {
		t.Fatalf("truncated input emitted %d notifications", len(notifications))
	}
}

func TestExitRemovesOnlyOwnRegistryAndSocket(t *testing.T) {
	directory := t.TempDir()
	h := startBridge(t, nil, directory)
	registry := filepath.Join(directory, fmt.Sprintf("%d.json", h.parent.PID))
	if status := h.exchange(h.requestLine("before exit")); status != "written" {
		t.Fatalf("status %q", status)
	}
	h.cancel()
	select {
	case <-h.runDone:
	case <-time.After(5 * time.Second):
		t.Fatal("Run did not return after cancellation")
	}
	if _, err := os.Stat(registry); !os.IsNotExist(err) {
		t.Fatalf("registry survived exit: %v", err)
	}
	if _, err := os.Stat(h.socketPath); !os.IsNotExist(err) {
		t.Fatalf("socket survived exit: %v", err)
	}
}

func TestBlockedWriteReportsUncertainAndShutsDown(t *testing.T) {
	h := startBridge(t, func(inner mcp.Transport) mcp.Transport {
		return &blockingTransport{inner: inner}
	}, t.TempDir())
	defer h.cancel()
	status := make(chan string, 1)
	go func() { status <- h.exchange(h.requestLine("blocked payload")) }()
	select {
	case reply := <-status:
		if reply != "uncertain" {
			t.Fatalf("blocked write status %q", reply)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("blocked write never resolved")
	}
	select {
	case <-h.runDone:
	case <-time.After(5 * time.Second):
		t.Fatal("Run did not end after the forced close")
	}
}

func TestSessionlessClientAlsoBecomesReady(t *testing.T) {
	// The SDK's own client defaults to the sessionless server/discover
	// protocol; that discovery must count as initialization too.
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	serverTransport, clientTransport := mcp.NewInMemoryTransports()
	recording := &recordingTransport{inner: serverTransport}
	directory := t.TempDir()
	done := make(chan error, 1)
	go func() { done <- Run(ctx, directory, recording) }()
	client := mcp.NewClient(&mcp.Implementation{Name: "channel-test"}, nil)
	session, err := client.Connect(context.Background(), clientTransport, nil)
	if err != nil {
		t.Fatalf("client initialize failed: %v", err)
	}
	defer session.Close()
	parent, err := ProcessRefOf(os.Getppid())
	if err != nil {
		t.Fatal(err)
	}
	registry := filepath.Join(directory, fmt.Sprintf("%d.json", parent.PID))
	deadline := time.Now().Add(5 * time.Second)
	for {
		if _, err := os.Stat(registry); err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("registry never appeared for the sessionless client")
		}
		time.Sleep(10 * time.Millisecond)
	}
	data, err := os.ReadFile(registry)
	if err != nil {
		t.Fatal(err)
	}
	var record registryRecord
	if json.Unmarshal(data, &record) != nil || record.Socket == "" {
		t.Fatalf("unexpected registry %s", data)
	}
	conn, err := net.Dial("unix", record.Socket)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	body, _ := json.Marshal(channelRequest{Parent: parent, Content: "sessionless probe",
		Meta: channelMeta{NativeSessionID: uuid.NewString(), AgentID: uuid.NewString(),
			ExecutionID: uuid.NewString(), DeliveryID: uuid.NewString()}})
	_ = conn.SetDeadline(time.Now().Add(5 * time.Second))
	if _, err := conn.Write(append(body, '\n')); err != nil {
		t.Fatal(err)
	}
	reply, err := bufio.NewReader(conn).ReadString('\n')
	if err != nil || !strings.Contains(reply, "written") {
		t.Fatalf("sessionless reply %q err %v", reply, err)
	}
}

func TestSimultaneousPublishersElectExactlyOneWinner(t *testing.T) {
	directory := t.TempDir()
	parent, err := ProcessRefOf(os.Getppid())
	if err != nil {
		t.Fatal(err)
	}
	var children []*exec.Cmd
	for i := 0; i < 6; i++ {
		cmd := exec.Command("sleep", "30")
		if err := cmd.Start(); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = cmd.Process.Kill(); _ = cmd.Wait() })
		children = append(children, cmd)
	}
	registry := filepath.Join(directory, fmt.Sprintf("%d.json", parent.PID))
	for round := 0; round < 5; round++ {
		_ = os.Remove(registry) // Only the record; publication is re-elected.
		start := make(chan struct{})
		results := make(chan error, len(children))
		var writers sync.WaitGroup
		for _, cmd := range children {
			self, err := ProcessRefOf(cmd.Process.Pid)
			if err != nil {
				t.Fatal(err)
			}
			b := &bridge{directory: directory, parent: parent, self: self,
				registry: registry, socketPath: filepath.Join(directory, fmt.Sprintf("%d.sock", self.PID))}
			writers.Add(1)
			go func() {
				defer writers.Done()
				<-start
				results <- b.writeRegistry()
			}()
		}
		close(start)
		writers.Wait()
		close(results)
		winners := 0
		for err := range results {
			if err == nil {
				winners++
			}
		}
		if winners != 1 {
			t.Fatalf("round %d: %d live bridges published the same parent's registry; want one", round, winners)
		}
		published, err := os.ReadFile(registry)
		if err != nil {
			t.Fatal(err)
		}
		var record registryRecord
		if json.Unmarshal(published, &record) != nil || record.Parent != parent {
			t.Fatalf("unexpected published record %s", published)
		}
	}
}

func TestCrashedPublisherLockAndStaleRecordAreRecovered(t *testing.T) {
	directory := t.TempDir()
	parent, err := ProcessRefOf(os.Getppid())
	if err != nil {
		t.Fatal(err)
	}
	self, err := ProcessRefOf(os.Getpid())
	if err != nil {
		t.Fatal(err)
	}
	registry := filepath.Join(directory, fmt.Sprintf("%d.json", parent.PID))
	// A dead process' stale record, plus a crashed publisher's old lock.
	stale, err := json.Marshal(registryRecord{Schema: registrySchema, Parent: parent,
		Process: ProcessRef{PID: self.PID, Start: 1, Boot: self.Boot}, Socket: "/gone"})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(registry, stale, 0o600); err != nil {
		t.Fatal(err)
	}
	lock := filepath.Join(directory, fmt.Sprintf(".publish.%d.lock", parent.PID))
	if err := os.WriteFile(lock, []byte("crashed"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(lock, time.Now().Add(-time.Minute), time.Now().Add(-time.Minute)); err != nil {
		t.Fatal(err)
	}
	b := &bridge{directory: directory, parent: parent, self: self,
		registry: registry, socketPath: filepath.Join(directory, "own.sock")}
	if err := b.writeRegistry(); err != nil {
		t.Fatalf("stale publication was not recovered: %v", err)
	}
	published, err := os.ReadFile(registry)
	if err != nil {
		t.Fatal(err)
	}
	var record registryRecord
	if json.Unmarshal(published, &record) != nil || record.Socket != b.socketPath {
		t.Fatalf("stale registry not replaced: %s", published)
	}
	if _, err := os.Stat(lock); err != nil {
		t.Fatalf("publication must retain the shared lock inode: %v", err)
	}
	if err := b.withPublishLock(func() error { return nil }); err != nil {
		t.Fatalf("publication did not release lock ownership: %v", err)
	}
}

func TestOldLockDoesNotDisplaceLivePublisher(t *testing.T) {
	directory := t.TempDir()
	b := &bridge{directory: directory, parent: ProcessRef{PID: os.Getpid()}}
	entered, release := make(chan struct{}), make(chan struct{})
	done := make(chan error, 1)
	go func() {
		done <- b.withPublishLock(func() error {
			close(entered)
			<-release
			return nil
		})
	}()
	select {
	case <-entered:
	case err := <-done:
		t.Fatalf("publisher did not acquire lock: %v", err)
	case <-time.After(3 * time.Second):
		t.Fatal("publisher did not start")
	}
	defer func() { close(release); <-done }()
	lock := filepath.Join(directory, fmt.Sprintf(".publish.%d.lock", b.parent.PID))
	old := time.Now().Add(-time.Minute)
	if err := os.Chtimes(lock, old, old); err != nil {
		t.Fatal(err)
	}
	secondEntered := false
	err := b.withPublishLock(func() error { secondEntered = true; return nil })
	if secondEntered || err == nil {
		t.Fatal("an old lock allowed a second publisher while its owner was still active")
	}
}

func TestPublisherExitReleasesLock(t *testing.T) {
	const childDirectory = "CAIRN_TEST_PUBLISH_LOCK_DIRECTORY"
	if directory := os.Getenv(childDirectory); directory != "" {
		b := &bridge{directory: directory, parent: ProcessRef{PID: 1}}
		if err := b.withPublishLock(func() error {
			fmt.Println("locked")
			time.Sleep(30 * time.Second)
			return nil
		}); err != nil {
			t.Fatal(err)
		}
		return
	}
	directory := t.TempDir()
	child := exec.Command(os.Args[0], "-test.run=^TestPublisherExitReleasesLock$")
	child.Env = append(os.Environ(), childDirectory+"="+directory)
	output, err := child.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if child.ProcessState == nil {
			_ = child.Process.Kill()
			_ = child.Wait()
		}
	})
	ready := make(chan string, 1)
	go func() { line, _ := bufio.NewReader(output).ReadString('\n'); ready <- line }()
	select {
	case line := <-ready:
		if line != "locked\n" {
			t.Fatalf("child did not acquire lock: %q", line)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("child did not announce lock ownership")
	}
	b := &bridge{directory: directory, parent: ProcessRef{PID: 1}}
	if err := b.withPublishLock(func() error { return nil }); err == nil {
		t.Fatal("live child did not exclude another publisher")
	}
	if err := child.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	_ = child.Wait()
	if err := b.withPublishLock(func() error { return nil }); err != nil {
		t.Fatalf("terminated publisher retained lock ownership: %v", err)
	}
}

func TestLiveCompetingBridgeIsRefusedAndStaleIsReplaced(t *testing.T) {
	directory := t.TempDir()
	self, err := ProcessRefOf(os.Getpid())
	if err != nil {
		t.Fatal(err)
	}
	parent, err := ProcessRefOf(os.Getppid())
	if err != nil {
		t.Fatal(err)
	}
	registry := filepath.Join(directory, fmt.Sprintf("%d.json", parent.PID))
	live, err := json.Marshal(registryRecord{Schema: registrySchema, Parent: parent,
		Process: self, Socket: filepath.Join(directory, "other.sock")})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(registry, live, 0o600); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := Run(ctx, directory, nil); err == nil || !strings.Contains(err.Error(), "another live bridge") {
		t.Fatalf("competing bridge not refused: %v", err)
	}
	stale, err := json.Marshal(registryRecord{Schema: registrySchema, Parent: parent,
		Process: ProcessRef{PID: self.PID, Start: 1, Boot: self.Boot}, Socket: "/gone"})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(registry, stale, 0o600); err != nil {
		t.Fatal(err)
	}
	h := startBridge(t, nil, directory) // A fresh Run replaces the stale record.
	defer h.cancel()
	data, err := os.ReadFile(registry)
	if err != nil {
		t.Fatal(err)
	}
	var record registryRecord
	if json.Unmarshal(data, &record) != nil || record.Socket != h.socketPath {
		t.Fatalf("stale registry not replaced: %s", data)
	}
}
