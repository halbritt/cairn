// Package claudechannel runs the "cairn-events" local MCP stdio server that
// turns verified one-line requests on a private owner-only Unix socket into
// notifications/claude/channel notifications for the MCP client.
package claudechannel

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/google/uuid"
	"github.com/modelcontextprotocol/go-sdk/jsonrpc"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const (
	serverName         = "cairn-events"
	capability         = "claude/channel"
	notificationMethod = "notifications/claude/channel"
	registrySchema     = "cairn.claude-channel/1"
	// One JSON request line excluding its newline, and bounded socket waits.
	maxLine        = 8192
	socketDeadline = 2 * time.Second
	// ioConn.Write checks its context only before the raw write, so a blocked
	// writer is unbounced by closing the connection when this timer fires.
	writeTimeout = 2 * time.Second
)

// ProcessRef identifies one operating-system process across PID reuse.
type ProcessRef struct {
	PID   int    `json:"pid"`
	Start uint64 `json:"start"`
	Boot  string `json:"boot"`
}

// ProcessRefOf measures a live process, or returns an error when it is gone.
func ProcessRefOf(pid int) (ProcessRef, error) {
	raw, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if err != nil {
		return ProcessRef{}, err
	}
	text := string(raw)
	fields := strings.Fields(text[strings.LastIndexByte(text, ')')+1:])
	if len(fields) < 20 {
		return ProcessRef{}, fmt.Errorf("short /proc/%d/stat", pid)
	}
	start, err := strconv.ParseUint(fields[19], 10, 64)
	if err != nil {
		return ProcessRef{}, err
	}
	boot, err := bootID()
	if err != nil {
		return ProcessRef{}, err
	}
	return ProcessRef{PID: pid, Start: start, Boot: boot}, nil
}

func bootID() (string, error) {
	raw, err := os.ReadFile("/proc/sys/kernel/random/boot_id")
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(raw)), nil
}

func liveProcess(ref ProcessRef) bool {
	current, err := ProcessRefOf(ref.PID)
	return err == nil && current == ref
}

type channelMeta struct {
	NativeSessionID string `json:"native_session_id"`
	AgentID         string `json:"agent_id"`
	ExecutionID     string `json:"execution_id"`
	DeliveryID      string `json:"delivery_id"`
}

type channelRequest struct {
	Parent  ProcessRef  `json:"parent"`
	Content string      `json:"content"`
	Meta    channelMeta `json:"meta"`
}

type channelReply struct {
	Status string `json:"status"`
}

type registryRecord struct {
	Schema  string     `json:"schema"`
	Parent  ProcessRef `json:"parent"`
	Process ProcessRef `json:"process"`
	Socket  string     `json:"socket"`
}

// capturingTransport records the logical connection the SDK established so
// notifications can be written outside the SDK's typed helpers. Its connection
// also observes the first initialization message — the legacy
// notifications/initialized handshake or a sessionless server/discover
// request — because the SDK offers no custom-notification helper and both
// handshake generations must count as "MCP initialized".
type capturingTransport struct {
	transport mcp.Transport
	ready     func()
	mu        sync.Mutex
	conn      mcp.Connection
}

type capturingConnection struct {
	mcp.Connection
	transport *capturingTransport
}

func (t *capturingTransport) Connect(ctx context.Context) (mcp.Connection, error) {
	conn, err := t.transport.Connect(ctx)
	if err != nil {
		return nil, err
	}
	t.mu.Lock()
	t.conn = conn
	t.mu.Unlock()
	return &capturingConnection{Connection: conn, transport: t}, nil
}

func (t *capturingTransport) connection() mcp.Connection {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.conn
}

func (c *capturingConnection) Read(ctx context.Context) (jsonrpc.Message, error) {
	message, err := c.Connection.Read(ctx)
	if err == nil {
		if request, ok := message.(*jsonrpc.Request); ok &&
			(request.Method == "notifications/initialized" || request.Method == "server/discover") {
			c.transport.ready()
		}
	}
	return message, err
}

type bridge struct {
	directory  string
	parent     ProcessRef
	self       ProcessRef
	registry   string
	socketPath string
}

// Run serves the channel until the MCP session ends or ctx is cancelled.
// transport is the MCP transport to serve (StdioTransport for the CLI).
func Run(ctx context.Context, directory string, transport mcp.Transport) error {
	if !filepath.IsAbs(directory) {
		return errors.New("claude-channel requires an absolute --directory")
	}
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return fmt.Errorf("claude-channel directory: %w", err)
	}
	if err := os.Chmod(directory, 0o700); err != nil {
		return fmt.Errorf("claude-channel directory permissions: %w", err)
	}
	parent, err := ProcessRefOf(os.Getppid())
	if err != nil {
		return fmt.Errorf("claude-channel cannot measure its parent: %w", err)
	}
	self, err := ProcessRefOf(os.Getpid())
	if err != nil {
		return fmt.Errorf("claude-channel cannot measure itself: %w", err)
	}
	b := &bridge{directory: directory, parent: parent, self: self,
		registry:   filepath.Join(directory, fmt.Sprintf("%d.json", parent.PID)),
		socketPath: filepath.Join(directory, fmt.Sprintf("%d.sock", self.PID))}
	if len(b.socketPath)+1 > 108 {
		return errors.New("claude-channel socket path exceeds the Unix limit; use a shorter --directory")
	}
	if err := b.refuseCompetingBridge(); err != nil {
		return err
	}
	// A socket under this process's own ID can never belong to a live peer.
	_ = os.Remove(b.socketPath)
	listener, err := net.Listen("unix", b.socketPath)
	if err != nil {
		return fmt.Errorf("claude-channel socket: %w", err)
	}
	defer b.cleanup(listener)
	if err := os.Chmod(b.socketPath, 0o600); err != nil {
		return fmt.Errorf("claude-channel socket permissions: %w", err)
	}
	var captured *capturingTransport
	captured = &capturingTransport{transport: transport, ready: sync.OnceFunc(func() {
		if err := b.writeRegistry(); err != nil {
			fmt.Fprintln(os.Stderr, "cairn claude-channel: registry unavailable; socket disabled")
			listener.Close()
			return
		}
		go b.serve(listener, captured)
	})}
	server := mcp.NewServer(&mcp.Implementation{Name: serverName, Version: "1"}, &mcp.ServerOptions{
		Capabilities: &mcp.ServerCapabilities{
			Experimental: map[string]any{capability: map[string]any{}},
		},
	})
	return server.Run(ctx, captured)
}

// refuseCompetingBridge fails before serving when another live bridge already
// serves the same parent; stale records from dead processes are replaced.
func (b *bridge) refuseCompetingBridge() error {
	data, err := os.ReadFile(b.registry)
	if err != nil {
		return nil
	}
	var record registryRecord
	if json.Unmarshal(data, &record) != nil || record.Schema != registrySchema {
		return nil
	}
	if liveProcess(record.Process) {
		return fmt.Errorf("claude-channel: another live bridge serves parent %d", b.parent.PID)
	}
	return nil
}

func (b *bridge) writeRegistry() error {
	data, err := json.Marshal(registryRecord{Schema: registrySchema, Parent: b.parent,
		Process: b.self, Socket: b.socketPath})
	if err != nil {
		return err
	}
	file, err := os.CreateTemp(b.directory, ".claude-channel-")
	if err != nil {
		return err
	}
	temp := file.Name()
	defer os.Remove(temp)
	if err := file.Chmod(0o600); err != nil {
		file.Close()
		return err
	}
	if _, err := file.Write(data); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	// Publication is serialized per parent and claimed by an atomic
	// create-if-absent link: exactly one of many simultaneous writers
	// publishes, and a live winner is never displaced.
	return b.withPublishLock(func() error {
		if existing, err := os.ReadFile(b.registry); err == nil {
			var record registryRecord
			if json.Unmarshal(existing, &record) == nil && record.Schema == registrySchema &&
				liveProcess(record.Process) {
				return fmt.Errorf("claude-channel: another live bridge serves parent %d", b.parent.PID)
			}
			if err := os.Remove(b.registry); err != nil && !errors.Is(err, fs.ErrNotExist) {
				return err
			}
		}
		return os.Link(temp, b.registry)
	})
}

// withPublishLock holds a brief per-parent exclusion around one publication
// decision. The kernel releases ownership on close or process exit. Keep the
// lock file: unlinking it would let another publisher lock a different inode.
func (b *bridge) withPublishLock(decision func() error) error {
	lock := filepath.Join(b.directory, fmt.Sprintf(".publish.%d.lock", b.parent.PID))
	file, err := os.OpenFile(lock, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return err
	}
	defer file.Close()
	if err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		return fmt.Errorf("claude-channel: publication lock unavailable: %w", err)
	}
	return decision()
}

// serve accepts and handles one connection at a time; each connection carries
// exactly one request line and receives exactly one status reply.
func (b *bridge) serve(listener net.Listener, captured *capturingTransport) {
	for {
		conn, err := listener.Accept()
		if err != nil {
			return
		}
		b.handle(conn, captured)
	}
}

func (b *bridge) handle(conn net.Conn, captured *capturingTransport) {
	defer conn.Close()
	status := b.handleLine(conn, captured)
	data, err := json.Marshal(channelReply{Status: status})
	if err != nil {
		return
	}
	_ = conn.SetWriteDeadline(time.Now().Add(socketDeadline))
	_, _ = conn.Write(append(data, '\n'))
}

func (b *bridge) handleLine(conn net.Conn, captured *capturingTransport) string {
	if err := conn.SetReadDeadline(time.Now().Add(socketDeadline)); err != nil {
		return "unavailable"
	}
	line, err := bufio.NewReader(io.LimitReader(conn, maxLine+1)).ReadBytes('\n')
	if err != nil || len(line) > maxLine+1 {
		return "unavailable" // malformed or oversized input; nothing was sent.
	}
	line = line[:len(line)-1]
	if len(line) > maxLine {
		return "unavailable"
	}
	var request channelRequest
	if err := json.Unmarshal(line, &request); err != nil {
		return "unavailable"
	}
	if request.Parent != b.parent || os.Getppid() != b.parent.PID || !liveProcess(b.parent) {
		return "unavailable" // confirmed refusal before any transport write.
	}
	if request.Content == "" || !validMeta(request.Meta) {
		return "unavailable"
	}
	params, err := json.Marshal(struct {
		Content string      `json:"content"`
		Meta    channelMeta `json:"meta"`
	}{Content: request.Content, Meta: request.Meta})
	if err != nil {
		return "unavailable"
	}
	return b.emit(captured, params)
}

func validMeta(meta channelMeta) bool {
	for _, value := range []string{meta.NativeSessionID, meta.AgentID, meta.ExecutionID, meta.DeliveryID} {
		if _, err := uuid.Parse(value); err != nil {
			return false
		}
	}
	return true
}

// emit writes one notification through the captured SDK connection. A write
// failure or timeout is reported as uncertain: the watcher keeps its intent
// and never retries through this channel.
func (b *bridge) emit(captured *capturingTransport, params json.RawMessage) string {
	connection := captured.connection()
	if connection == nil {
		return "unavailable"
	}
	message := &jsonrpc.Request{Method: notificationMethod, Params: params} // zero ID: a notification.
	timer := time.AfterFunc(writeTimeout, func() {
		if conn := captured.connection(); conn != nil {
			_ = conn.Close() // Unblocks a raw write the context cannot cancel.
		}
	})
	err := connection.Write(context.Background(), message)
	timer.Stop()
	if err == nil {
		return "written" // Written to the transport only; not model acceptance.
	}
	return "uncertain"
}

// cleanup removes only this bridge's registry record and socket.
func (b *bridge) cleanup(listener net.Listener) {
	_ = listener.Close()
	if data, err := os.ReadFile(b.registry); err == nil {
		var record registryRecord
		if json.Unmarshal(data, &record) == nil && record.Process == b.self &&
			record.Socket == b.socketPath && record.Schema == registrySchema {
			_ = os.Remove(b.registry)
		}
	}
	_ = os.Remove(b.socketPath)
}
