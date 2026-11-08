package wake

import (
	"bufio"
	"context"
	"crypto/rand"
	"crypto/sha1"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/procpeer"
)

// Codex queue client constants matching integrations/lifecycle/codex_queue.py.
const (
	CodexBusyCode          = -32600
	CodexBusyMessage       = "thread already has an active or pending turn"
	CodexMaxPeerBytes      = 1024 * 1024
	CodexConnectTimeout    = 4 * time.Second
	CodexRPCTimeout        = 4 * time.Second
	CodexMaxQueuedIDs      = 64
	CodexMaxQueuedIDBytes  = 256
	CodexStartStarted      = "started"
	CodexStartBusy         = "busy"
	CodexStartRefused      = "refused"
	CodexStartUnknown      = "unknown"
	CodexStartMissing      = "missing"
	codexWebSocketGUID     = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
	codexOwnerProcessLabel = "process"
	codexOwnerUIDLabel     = "uid"
)

// CodexQueueError reports a post-send or uncertain Codex queue failure.
type CodexQueueError struct {
	Message string
	Cause   error
}

func (e *CodexQueueError) Error() string { return e.Message }
func (e *CodexQueueError) Unwrap() error { return e.Cause }

// CodexQueueUnavailable reports a checked pre-send refusal before any queue
// submission reached the native Codex app-server.
type CodexQueueUnavailable struct {
	Message string
	Cause   error
}

func (e *CodexQueueUnavailable) Error() string { return e.Message }
func (e *CodexQueueUnavailable) Unwrap() error { return e.Cause }

// CodexQueueRefused reports a structured native JSON-RPC error object.
type CodexQueueRefused struct {
	Method     string
	Code       any
	HasIntCode bool
	IntCode    int64
	Message    string
	HasStrMsg  bool
}

func (e *CodexQueueRefused) Error() string {
	return fmt.Sprintf("native Codex refused %s", e.Method)
}

// IsCodexQueueUnavailable reports whether err is a retryable pre-send refusal.
func IsCodexQueueUnavailable(err error) bool {
	var target *CodexQueueUnavailable
	return errors.As(err, &target)
}

// IsCodexQueueRefused reports whether err is a structured native refusal.
func IsCodexQueueRefused(err error) bool {
	var target *CodexQueueRefused
	return errors.As(err, &target)
}

// IsCodexQueueError reports whether err is any Codex queue error.
func IsCodexQueueError(err error) bool {
	var target *CodexQueueError
	return errors.As(err, &target) || IsCodexQueueUnavailable(err) || IsCodexQueueRefused(err)
}

// CodexQueueEndpoint describes the Unix listener and owner verification mode
// selected from a Codex process's command line.
type CodexQueueEndpoint struct {
	Endpoint  string               `json:"endpoint"`
	Owner     procpeer.ClientOwner `json:"-"`
	OwnerMode string               `json:"owner"`
}

// CodexThreadInspection holds the read-only thread status and queue snapshot
// returned by InspectThread.
type CodexThreadInspection struct {
	StatusType    string     `json:"status_type"`
	UpdatedAt     *int64     `json:"updated_at"`
	QueueCount    int        `json:"queue_count"`
	QueuedIDs     []string   `json:"queued_ids"`
	QueuedPresent *bool      `json:"queued_present"`
	Peer          ProcessRef `json:"peer"`
}

// SelectCodexQueueEndpoint inspects /proc/<pid>/cmdline and selects only an
// explicit absolute unix:// --listen or --remote endpoint.
func SelectCodexQueueEndpoint(harness string, process ProcessRef) (*CodexQueueEndpoint, bool) {
	return selectCodexQueueEndpointAt("/proc", harness, process)
}

func selectCodexQueueEndpointAt(procRoot, harness string, process ProcessRef) (*CodexQueueEndpoint, bool) {
	if harness != HarnessCodex || process.PID <= 0 {
		return nil, false
	}
	raw, err := os.ReadFile(fmt.Sprintf("%s/%d/cmdline", procRoot, process.PID))
	if err != nil || !utf8.Valid(raw) {
		return nil, false
	}
	argv := strings.Split(string(raw), "\x00")
	return ParseCodexQueueArgv(harness, argv)
}

// ParseCodexQueueArgv extracts the unix:// endpoint and owner policy from a
// decoded /proc/<pid>/cmdline argument vector.
func ParseCodexQueueArgv(harness string, argv []string) (*CodexQueueEndpoint, bool) {
	if harness != HarnessCodex {
		return nil, false
	}
	var owner procpeer.ClientOwner
	var ownerMode string
	hasAppServer := slices.Contains(argv, "app-server")
	hasRemote := slices.Contains(argv, "--remote")
	if hasAppServer && !hasRemote {
		owner = procpeer.ProcessOwner
		ownerMode = codexOwnerProcessLabel
	} else if hasRemote {
		owner = procpeer.UserOwner
		ownerMode = codexOwnerUIDLabel
	} else {
		return nil, false
	}
	for i, arg := range argv {
		var address string
		switch {
		case (arg == "--listen" || arg == "--remote") && i+1 < len(argv):
			address = argv[i+1]
		case strings.HasPrefix(arg, "--listen="):
			address = strings.TrimPrefix(arg, "--listen=")
		case strings.HasPrefix(arg, "--remote="):
			address = strings.TrimPrefix(arg, "--remote=")
		default:
			continue
		}
		if strings.HasPrefix(address, "unix://") && len(address) > 7 {
			sockPath := address[7:]
			if filepath.IsAbs(sockPath) {
				return &CodexQueueEndpoint{
					Endpoint:  sockPath,
					Owner:     owner,
					OwnerMode: ownerMode,
				}, true
			}
		}
	}
	return nil, false
}

// CanResumeCodexQueued reports whether prior represents a retained queued
// submission on the same endpoint and native conversation.
func CanResumeCodexQueued(prior WakeEnvelope, endpoint *CodexQueueEndpoint, nativeSessionID string) bool {
	return endpoint != nil &&
		prior.Transport == TransportCodexQueue &&
		prior.Status == "queued" &&
		prior.QueuedSubmissionID != "" &&
		prior.Endpoint == endpoint.Endpoint &&
		prior.NativeID == nativeSessionID
}

// CodexQueueClient configures timeouts, read budget, and optional test UID override.
type CodexQueueClient struct {
	ConnectTimeout time.Duration
	RPCTimeout     time.Duration
	MaxPeerBytes   int64
	ExpectedUID    *uint32
	Now            func() time.Time
	BeforeCall     func(method string, rpc *codexRPC)
}

type perOpTimeoutConn struct {
	net.Conn
	timeout time.Duration
}

func (c *perOpTimeoutConn) Read(p []byte) (int, error) {
	if c.timeout > 0 {
		if err := c.Conn.SetReadDeadline(time.Now().Add(c.timeout)); err != nil {
			return 0, err
		}
	}
	return c.Conn.Read(p)
}

func (c *perOpTimeoutConn) Write(p []byte) (int, error) {
	if c.timeout > 0 {
		if err := c.Conn.SetWriteDeadline(time.Now().Add(c.timeout)); err != nil {
			return 0, err
		}
	}
	return c.Conn.Write(p)
}

type codexWSConn struct {
	conn   *perOpTimeoutConn
	reader *bufio.Reader
}

func (c *codexWSConn) Close() error {
	return c.conn.Close()
}

func (c *codexWSConn) setTimeout(d time.Duration) {
	c.conn.timeout = d
}

func (c CodexQueueClient) connectTimeout() time.Duration {
	if c.ConnectTimeout > 0 {
		return c.ConnectTimeout
	}
	return CodexConnectTimeout
}

func (c CodexQueueClient) rpcTimeout() time.Duration {
	if c.RPCTimeout > 0 {
		return c.RPCTimeout
	}
	return CodexRPCTimeout
}

func (c CodexQueueClient) maxPeerBytes() int64 {
	if c.MaxPeerBytes > 0 {
		return c.MaxPeerBytes
	}
	return CodexMaxPeerBytes
}

func (c CodexQueueClient) now() time.Time {
	if c.Now != nil {
		return c.Now()
	}
	return time.Now()
}

func (c CodexQueueClient) open(ctx context.Context, endpoint string, process ProcessRef, owner procpeer.ClientOwner) (*codexWSConn, ProcessRef, error) {
	if owner == 0 {
		owner = procpeer.ProcessOwner
	}
	var expectedUID *uint32
	if owner == procpeer.UserOwner {
		if c.ExpectedUID != nil {
			expectedUID = c.ExpectedUID
		} else {
			uid := uint32(os.Geteuid())
			expectedUID = &uid
		}
	} else {
		expectedUID = c.ExpectedUID
	}
	policy := procpeer.ClientPolicy{
		Owner:       owner,
		ExpectedUID: expectedUID,
		Messages: procpeer.ClientMessages{
			WrongProcess:        "native socket belongs to a different process",
			WrongUser:           "native socket belongs to another user",
			IdentityChanged:     "native process identity has changed",
			EndpointUnavailable: "native queue endpoint is unavailable",
		},
	}

	rawConn, peer, err := procpeer.ConnectClient(ctx, endpoint, process, policy, c.connectTimeout())
	if err != nil {
		var refusal *procpeer.ClientRefusal
		if errors.As(err, &refusal) {
			return nil, ProcessRef{}, &CodexQueueUnavailable{Message: refusal.Message, Cause: refusal.Cause}
		}
		return nil, ProcessRef{}, &CodexQueueUnavailable{Message: "native queue endpoint is unavailable", Cause: err}
	}

	overflowErr := &CodexQueueError{Message: "native queue response exceeded byte limit"}
	limitedConn, err := procpeer.LimitClientReads(rawConn, c.maxPeerBytes(), overflowErr)
	if err != nil {
		_ = rawConn.Close()
		return nil, ProcessRef{}, &CodexQueueUnavailable{Message: "native queue endpoint is unavailable", Cause: err}
	}

	opConn := &perOpTimeoutConn{Conn: limitedConn, timeout: c.connectTimeout()}
	ws, err := wsHandshake(opConn)
	if err != nil {
		_ = limitedConn.Close()
		var queueErr *CodexQueueError
		if errors.As(err, &queueErr) {
			return nil, ProcessRef{}, &CodexQueueUnavailable{Message: "native queue handshake did not complete within byte limit", Cause: err}
		}
		return nil, ProcessRef{}, &CodexQueueUnavailable{Message: "native queue endpoint is unavailable", Cause: err}
	}
	return ws, peer, nil
}

func wsHandshake(conn *perOpTimeoutConn) (*codexWSConn, error) {
	var keyBytes [16]byte
	if _, err := io.ReadFull(rand.Reader, keyBytes[:]); err != nil {
		return nil, err
	}
	secKey := base64.StdEncoding.EncodeToString(keyBytes[:])
	reqText := "GET / HTTP/1.1\r\n" +
		"Host: localhost\r\n" +
		"Upgrade: websocket\r\n" +
		"Connection: Upgrade\r\n" +
		"Sec-WebSocket-Key: " + secKey + "\r\n" +
		"Sec-WebSocket-Version: 13\r\n\r\n"
	if _, err := io.WriteString(conn, reqText); err != nil {
		return nil, err
	}
	reader := bufio.NewReader(conn)
	req, err := http.NewRequest(http.MethodGet, "http://localhost/", nil)
	if err != nil {
		return nil, err
	}
	resp, err := http.ReadResponse(reader, req)
	if err != nil {
		return nil, err
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusSwitchingProtocols {
		return nil, fmt.Errorf("unexpected websocket status %d", resp.StatusCode)
	}
	if !strings.EqualFold(strings.TrimSpace(resp.Header.Get("Upgrade")), "websocket") {
		return nil, fmt.Errorf("missing Upgrade: websocket header")
	}
	sum := sha1.Sum([]byte(secKey + codexWebSocketGUID))
	expectedAccept := base64.StdEncoding.EncodeToString(sum[:])
	if strings.TrimSpace(resp.Header.Get("Sec-WebSocket-Accept")) != expectedAccept {
		return nil, fmt.Errorf("mismatched Sec-WebSocket-Accept")
	}
	return &codexWSConn{conn: conn, reader: reader}, nil
}

func (c *codexWSConn) sendText(payload []byte) error {
	return c.writeMaskedFrame(0x81, payload)
}

func (c *codexWSConn) writeMaskedFrame(opcode byte, payload []byte) error {
	var mask [4]byte
	if _, err := io.ReadFull(rand.Reader, mask[:]); err != nil {
		return err
	}
	var hdr []byte
	n := len(payload)
	switch {
	case n < 126:
		hdr = []byte{opcode, byte(0x80 | n)}
	case n < 65536:
		hdr = make([]byte, 4)
		hdr[0] = opcode
		hdr[1] = 0x80 | 126
		binary.BigEndian.PutUint16(hdr[2:4], uint16(n))
	default:
		hdr = make([]byte, 10)
		hdr[0] = opcode
		hdr[1] = 0x80 | 127
		binary.BigEndian.PutUint64(hdr[2:10], uint64(n))
	}
	frame := make([]byte, 0, len(hdr)+4+n)
	frame = append(frame, hdr...)
	frame = append(frame, mask[:]...)
	for i, b := range payload {
		frame = append(frame, b^mask[i%4])
	}
	for len(frame) > 0 {
		written, err := c.conn.Write(frame)
		frame = frame[written:]
		if err != nil {
			return err
		}
		if written == 0 {
			return io.ErrNoProgress
		}
	}
	return nil
}

func (c *codexWSConn) recvText() ([]byte, error) {
	var msg []byte
	for {
		var hdr [2]byte
		if _, err := io.ReadFull(c.reader, hdr[:]); err != nil {
			return nil, err
		}
		fin := hdr[0]&0x80 != 0
		opcode := hdr[0] & 0x0f
		masked := hdr[1]&0x80 != 0
		length := uint64(hdr[1] & 0x7f)
		switch length {
		case 126:
			var ext [2]byte
			if _, err := io.ReadFull(c.reader, ext[:]); err != nil {
				return nil, err
			}
			length = uint64(binary.BigEndian.Uint16(ext[:]))
		case 127:
			var ext [8]byte
			if _, err := io.ReadFull(c.reader, ext[:]); err != nil {
				return nil, err
			}
			length = binary.BigEndian.Uint64(ext[:])
		}
		if length > uint64(CodexMaxPeerBytes) {
			return nil, &CodexQueueError{Message: "native queue response exceeded byte limit"}
		}
		var mask [4]byte
		if masked {
			if _, err := io.ReadFull(c.reader, mask[:]); err != nil {
				return nil, err
			}
		}
		payload := make([]byte, int(length))
		if _, err := io.ReadFull(c.reader, payload); err != nil {
			return nil, err
		}
		if masked {
			for i := range payload {
				payload[i] ^= mask[i%4]
			}
		}
		switch opcode {
		case 8: // Close
			return nil, io.EOF
		case 9: // Ping
			_ = c.writeMaskedFrame(0x8A, payload)
			continue
		case 10: // Pong
			continue
		case 0, 1, 2:
			msg = append(msg, payload...)
			if len(msg) > CodexMaxPeerBytes {
				return nil, &CodexQueueError{Message: "native queue response exceeded byte limit"}
			}
			if fin {
				return msg, nil
			}
		default:
			return nil, fmt.Errorf("unsupported websocket opcode %d", opcode)
		}
	}
}

type codexRPC struct {
	ws         *codexWSConn
	Deadline   time.Time
	sequence   int64
	now        func() time.Time
	beforeCall func(method string, rpc *codexRPC)
}

func (r *codexRPC) remaining(sent bool) (time.Duration, error) {
	now := time.Now()
	if r.now != nil {
		now = r.now()
	}
	left := r.Deadline.Sub(now)
	if left <= 0 {
		if sent {
			return 0, &CodexQueueError{Message: "native queue response timed out"}
		}
		return 0, &CodexQueueUnavailable{Message: "native queue deadline expired before request send"}
	}
	return left, nil
}

func (r *codexRPC) call(method string, params map[string]any, busyOK bool) (map[string]any, error) {
	if r.beforeCall != nil {
		r.beforeCall(method, r)
	}
	r.sequence++
	left, err := r.remaining(false)
	if err != nil {
		return nil, err
	}
	r.ws.setTimeout(left)
	reqBytes, err := json.Marshal(map[string]any{
		"id":     r.sequence,
		"method": method,
		"params": params,
	})
	if err != nil {
		return nil, err
	}
	if err := r.ws.sendText(reqBytes); err != nil {
		return nil, err
	}
	for {
		left, err := r.remaining(true)
		if err != nil {
			return nil, err
		}
		r.ws.setTimeout(left)
		raw, err := r.ws.recvText()
		if err != nil {
			return nil, err
		}
		parsed, err := decodeStrictJSONValue(raw)
		if err != nil {
			return nil, err
		}
		msg, ok := parsed.(map[string]any)
		if !ok || msg == nil {
			return nil, &CodexQueueError{Message: "native Codex returned an invalid response"}
		}
		if _, hasMethod := msg["method"]; hasMethod {
			continue // Server requests are not responses, even when ids collide.
		}
		idVal, ok := parseExactInt64(msg["id"])
		if !ok || idVal != r.sequence {
			continue
		}
		if errRaw, hasErr := msg["error"]; hasErr {
			_, hasResult := msg["result"]
			errObj, errIsMap := errRaw.(map[string]any)
			if hasResult || !errIsMap || errObj == nil {
				return nil, &CodexQueueError{Message: "native Codex returned an invalid response"}
			}
			if busyOK && isCodexBusy(errObj) {
				return nil, nil // An existing turn keeps ownership; queued input waits.
			}
			return nil, newCodexQueueRefused(method, errObj)
		}
		resObj, ok := msg["result"].(map[string]any)
		if !ok || resObj == nil {
			return nil, &CodexQueueError{Message: "native Codex returned an invalid response"}
		}
		return resObj, nil
	}
}

func isCodexBusy(errObj map[string]any) bool {
	codeVal, ok := parseExactInt64(errObj["code"])
	if !ok || codeVal != CodexBusyCode {
		return false
	}
	msgVal, ok := errObj["message"].(string)
	return ok && msgVal == CodexBusyMessage
}

func newCodexQueueRefused(method string, errObj map[string]any) *CodexQueueRefused {
	codeVal, hasInt := parseExactInt64(errObj["code"])
	msgVal, hasStr := errObj["message"].(string)
	return &CodexQueueRefused{
		Method:     method,
		Code:       errObj["code"],
		HasIntCode: hasInt,
		IntCode:    codeVal,
		Message:    msgVal,
		HasStrMsg:  hasStr,
	}
}

func (r *codexRPC) admit(nativeID string) error {
	if _, err := r.call("initialize", map[string]any{
		"clientInfo":   map[string]any{"name": "cairn-idle-wakeup", "version": "1"},
		"capabilities": map[string]any{"experimentalApi": true},
	}, false); err != nil {
		return err
	}
	if err := r.ws.sendText([]byte(`{"method":"initialized"}`)); err != nil {
		return err
	}
	var cursor string
	for {
		params := map[string]any{"limit": 100}
		if cursor != "" {
			params["cursor"] = cursor
		}
		page, err := r.call("thread/loaded/list", params, false)
		if err != nil {
			return err
		}
		data, ok := page["data"].([]any)
		if !ok {
			return &CodexQueueError{Message: "native Codex returned an invalid thread list"}
		}
		for _, item := range data {
			if id, ok := item.(string); ok && id == nativeID {
				return nil
			}
		}
		next, ok := page["nextCursor"].(string)
		if !ok || next == "" {
			return &CodexQueueUnavailable{Message: "native conversation is not loaded; refusing to resume it"}
		}
		cursor = next
	}
}

// CodexVerifiedPeer connects to endpoint, verifies peer identity and WebSocket
// upgrade without sending RPC messages, and returns the verified peer identity.
func CodexVerifiedPeer(ctx context.Context, endpoint string, process ProcessRef, owner procpeer.ClientOwner) (ProcessRef, error) {
	var client CodexQueueClient
	return client.VerifiedPeer(ctx, endpoint, process, owner)
}

// VerifiedPeer connects and verifies the endpoint owner and WebSocket upgrade,
// returning the socket-owning ProcessRef.
func (c CodexQueueClient) VerifiedPeer(ctx context.Context, endpoint string, process ProcessRef, owner procpeer.ClientOwner) (ProcessRef, error) {
	ws, peer, err := c.open(ctx, endpoint, process, owner)
	if err != nil {
		return ProcessRef{}, err
	}
	_ = ws.Close()
	return peer, nil
}

// CodexEnqueue performs one thread/queue/add attempt plus a first thread/queue/start
// attempt for that exact submission.
func CodexEnqueue(ctx context.Context, endpoint string, process ProcessRef, nativeID, text, clientID string, owner procpeer.ClientOwner) (string, bool, error) {
	var client CodexQueueClient
	return client.Enqueue(ctx, endpoint, process, nativeID, text, clientID, owner)
}

// Enqueue admits the loaded conversation, submits thread/queue/add, and makes
// one best-effort thread/queue/start attempt.
func (c CodexQueueClient) Enqueue(ctx context.Context, endpoint string, process ProcessRef, nativeID, text, clientID string, owner procpeer.ClientOwner) (string, bool, error) {
	ws, _, err := c.open(ctx, endpoint, process, owner)
	if err != nil {
		return "", false, err
	}
	defer func() { _ = ws.Close() }()

	rpc := &codexRPC{
		ws:         ws,
		Deadline:   c.now().Add(c.rpcTimeout()),
		now:        c.now,
		beforeCall: c.BeforeCall,
	}
	if err := rpc.admit(nativeID); err != nil {
		if IsCodexQueueUnavailable(err) {
			return "", false, err
		}
		return "", false, &CodexQueueUnavailable{Message: fmt.Sprintf("native queue is not ready: %v", err), Cause: err}
	}

	result, err := rpc.call("thread/queue/add", map[string]any{
		"threadId":            nativeID,
		"clientUserMessageId": clientID,
		"input":               []any{map[string]any{"type": "text", "text": text}},
	}, false)
	if err != nil {
		if IsCodexQueueUnavailable(err) || IsCodexQueueRefused(err) {
			return "", false, err
		}
		var queueErr *CodexQueueError
		if errors.As(err, &queueErr) {
			return "", false, queueErr
		}
		return "", false, &CodexQueueError{Message: "native queue outcome is uncertain; do not automatically resend", Cause: err}
	}

	queued, ok := result["queuedSubmission"].(map[string]any)
	if !ok || queued == nil {
		return "", false, &CodexQueueError{Message: "native queue did not confirm this submission"}
	}
	gotClientID, _ := queued["clientUserMessageId"].(string)
	queuedID, _ := queued["id"].(string)
	if gotClientID != clientID || queuedID == "" {
		return "", false, &CodexQueueError{Message: "native queue did not confirm this submission"}
	}

	started, err := rpc.call("thread/queue/start", map[string]any{
		"threadId":           nativeID,
		"queuedSubmissionId": queuedID,
	}, true)
	if err != nil || started == nil {
		return queuedID, false, nil
	}
	turn, ok := started["turn"].(map[string]any)
	if !ok || turn == nil {
		return queuedID, false, nil
	}
	turnID, _ := turn["id"].(string)
	if turnID == "" {
		return queuedID, false, nil
	}
	return queuedID, true, nil
}

// CodexStart retries only the start of one retained submission and reports
// whether a native turn start was confirmed.
func CodexStart(ctx context.Context, endpoint string, process ProcessRef, nativeID, queuedID string, owner procpeer.ClientOwner) (bool, error) {
	var client CodexQueueClient
	return client.Start(ctx, endpoint, process, nativeID, queuedID, owner)
}

// Start retries only the start of one retained submission and returns true only
// when the outcome is CodexStartStarted.
func (c CodexQueueClient) Start(ctx context.Context, endpoint string, process ProcessRef, nativeID, queuedID string, owner procpeer.ClientOwner) (bool, error) {
	outcome, err := c.StartOutcome(ctx, endpoint, process, nativeID, queuedID, owner)
	if err != nil {
		return false, err
	}
	return outcome == CodexStartStarted, nil
}

// CodexStartOutcome retries only the start of one retained submission and
// classifies the outcome as started, busy, refused, unknown, or missing.
func CodexStartOutcome(ctx context.Context, endpoint string, process ProcessRef, nativeID, queuedID string, owner procpeer.ClientOwner) (string, error) {
	var client CodexQueueClient
	return client.StartOutcome(ctx, endpoint, process, nativeID, queuedID, owner)
}

// StartOutcome retries only the start of one retained submission.
func (c CodexQueueClient) StartOutcome(ctx context.Context, endpoint string, process ProcessRef, nativeID, queuedID string, owner procpeer.ClientOwner) (string, error) {
	ws, _, err := c.open(ctx, endpoint, process, owner)
	if err != nil {
		return "", err
	}
	defer func() { _ = ws.Close() }()

	rpc := &codexRPC{
		ws:         ws,
		Deadline:   c.now().Add(c.rpcTimeout()),
		now:        c.now,
		beforeCall: c.BeforeCall,
	}
	if err := rpc.admit(nativeID); err != nil {
		return "", &CodexQueueUnavailable{Message: fmt.Sprintf("native queue is not ready: %v", err), Cause: err}
	}

	started, err := rpc.call("thread/queue/start", map[string]any{
		"threadId":           nativeID,
		"queuedSubmissionId": queuedID,
	}, true)
	if err == nil {
		if started == nil {
			return CodexStartBusy, nil
		}
		if turn, ok := started["turn"].(map[string]any); ok && turn != nil {
			if turnID, _ := turn["id"].(string); turnID != "" {
				return CodexStartStarted, nil
			}
		}
		return CodexStartUnknown, nil
	}

	var refused *CodexQueueRefused
	if errors.As(err, &refused) {
		if refused.HasIntCode && refused.IntCode == CodexBusyCode && refused.HasStrMsg && refused.Message != "" {
			if refused.Message == "queued submission not found: "+queuedID {
				return CodexStartMissing, nil
			}
			return CodexStartRefused, nil
		}
		return CodexStartUnknown, nil
	}
	return CodexStartUnknown, nil
}

var codexNativeStatusTypes = []string{"active", "idle", "systemError"}

// CodexInspectThread performs a read-only, peer/session-verified native thread
// status and queue probe without mutating native state.
func CodexInspectThread(ctx context.Context, endpoint string, process ProcessRef, nativeID string, owner procpeer.ClientOwner, queuedID string) (*CodexThreadInspection, error) {
	var client CodexQueueClient
	return client.InspectThread(ctx, endpoint, process, nativeID, owner, queuedID)
}

// InspectThread connects, verifies peer identity and loaded conversation admission,
// and reads thread/read (includeTurns=false) and thread/queue/list.
func (c CodexQueueClient) InspectThread(ctx context.Context, endpoint string, process ProcessRef, nativeID string, owner procpeer.ClientOwner, queuedID string) (*CodexThreadInspection, error) {
	ws, peer, err := c.open(ctx, endpoint, process, owner)
	if err != nil {
		return nil, err
	}
	defer func() { _ = ws.Close() }()

	rpc := &codexRPC{
		ws:         ws,
		Deadline:   c.now().Add(c.rpcTimeout()),
		now:        c.now,
		beforeCall: c.BeforeCall,
	}
	if err := rpc.admit(nativeID); err != nil {
		if IsCodexQueueUnavailable(err) {
			return nil, err
		}
		return nil, &CodexQueueUnavailable{Message: fmt.Sprintf("native queue is not ready: %v", err), Cause: err}
	}

	statusType := "unknown"
	var updatedAt *int64
	queuedIDs := []string{}
	unknownResult := func() *CodexThreadInspection {
		return &CodexThreadInspection{
			StatusType:    "unknown",
			UpdatedAt:     updatedAt,
			QueueCount:    0,
			QueuedIDs:     []string{},
			QueuedPresent: nil,
			Peer:          peer,
		}
	}

	readResult, err := rpc.call("thread/read", map[string]any{
		"threadId":     nativeID,
		"includeTurns": false,
	}, false)
	if err != nil {
		return unknownResult(), nil
	}
	thread := readResult
	if sub, ok := readResult["thread"].(map[string]any); ok && sub != nil {
		thread = sub
	}
	threadID, hasThreadID := thread["id"]
	if !hasThreadID || threadID == nil || threadID == nativeID {
		rawStatus := thread["status"]
		var candidate string
		if statusMap, ok := rawStatus.(map[string]any); ok && statusMap != nil {
			candidate, _ = statusMap["type"].(string)
		} else {
			candidate, _ = rawStatus.(string)
		}
		if slices.Contains(codexNativeStatusTypes, candidate) {
			statusType = candidate
		}
		if ts, ok := parseExactInt64(thread["updatedAt"]); ok {
			updatedAt = &ts
		}
	}

	queuePage, err := rpc.call("thread/queue/list", map[string]any{"threadId": nativeID}, false)
	if err != nil {
		return unknownResult(), nil
	}
	rawItems, ok := queuePage["data"].([]any)
	if !ok {
		return unknownResult(), nil
	}
	for _, rawItem := range rawItems {
		var itemID string
		switch v := rawItem.(type) {
		case map[string]any:
			itemID, _ = v["id"].(string)
		case string:
			itemID = v
		}
		if itemID == "" || len(itemID) > CodexMaxQueuedIDBytes {
			return unknownResult(), nil
		}
		if len(queuedIDs) < CodexMaxQueuedIDs {
			queuedIDs = append(queuedIDs, itemID)
		}
	}

	hasQuery := queuedID != ""
	inPage := hasQuery && slices.Contains(queuedIDs, queuedID)
	if nextCursor, hasCursor := queuePage["nextCursor"]; (hasCursor && nextCursor != nil) || len(rawItems) > CodexMaxQueuedIDs {
		var present *bool
		if inPage {
			t := true
			present = &t
		}
		return &CodexThreadInspection{
			StatusType:    statusType,
			UpdatedAt:     updatedAt,
			QueueCount:    len(queuedIDs),
			QueuedIDs:     queuedIDs,
			QueuedPresent: present,
			Peer:          peer,
		}, nil
	}
	var present *bool
	if hasQuery {
		v := inPage
		present = &v
	}
	return &CodexThreadInspection{
		StatusType:    statusType,
		UpdatedAt:     updatedAt,
		QueueCount:    len(queuedIDs),
		QueuedIDs:     queuedIDs,
		QueuedPresent: present,
		Peer:          peer,
	}, nil
}
