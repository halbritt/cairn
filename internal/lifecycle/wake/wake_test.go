package wake

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha1"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/procpeer"
)

const (
	testAgentID    = "11111111-1111-4111-8111-111111111111"
	testExecID     = "22222222-2222-4222-8222-222222222222"
	testDeliveryID = "33333333-3333-4333-8333-333333333333"
	testNativeID   = "caed9473-b01a-41e7-95ce-c3c1f28d66b3"
)

func TestWakeWordingAndRecognizers(t *testing.T) {
	if socketPath := os.Getenv("CAIRN_WAKE_TEST_CHILD_SOCK"); socketPath != "" {
		runWakeTestChildServer(t, socketPath)
		return
	}

	session := SessionRef{AgentID: testAgentID, ExecutionID: testExecID}
	current := WakeMessage(session, testDeliveryID)
	msgs := WakeMessages(session, testDeliveryID)
	if msgs[0] != current {
		t.Fatalf("WakeMessages[0] != WakeMessage")
	}
	if strings.Contains(current, "manually claim an inbox") || !strings.HasSuffix(current, WakeClosing) {
		t.Fatalf("unexpected current wording: %q", current)
	}
	if !strings.HasSuffix(msgs[1], LegacyWakeClosing) || msgs[0] == msgs[1] {
		t.Fatalf("unexpected legacy wording: %q", msgs[1])
	}

	meta := ClaudeChannelMeta{
		NativeSessionID: testNativeID,
		AgentID:         testAgentID,
		ExecutionID:     testExecID,
		DeliveryID:      testDeliveryID,
	}

	for i, body := range msgs {
		channelPrompt := FormatClaudeChannelPrompt(meta, body)
		wantPrefix := fmt.Sprintf(
			`<channel source="cairn-events" native_session_id="%s" agent_id="%s" execution_id="%s" delivery_id="%s">`+"\n",
			testNativeID, testAgentID, testExecID, testDeliveryID,
		)
		if !strings.HasPrefix(channelPrompt, wantPrefix) {
			t.Fatalf("wording[%d] channel prefix = %q", i, channelPrompt)
		}
		if !InboxRecallWake(PromptEvent{HookEventName: "UserPromptSubmit", Prompt: body}) {
			t.Fatalf("InboxRecallWake rejected bare wording[%d]", i)
		}
		if !InboxRecallWake(PromptEvent{HookEventName: "UserPromptSubmit", Prompt: channelPrompt}) {
			t.Fatalf("InboxRecallWake rejected channel wording[%d]", i)
		}
		if InboxRecallWake(PromptEvent{HookEventName: "UserPromptSubmit", Prompt: body + " extra"}) {
			t.Fatalf("InboxRecallWake accepted trailing text for wording[%d]", i)
		}

		codexWake := WakeEnvelope{Session: session, DeliveryID: testDeliveryID, Transport: TransportCodexQueue}
		bound, ok := BindCodexQueuedWake(session, codexWake, PromptEvent{
			HookEventName: "UserPromptSubmit",
			Prompt:        body,
			TurnID:        "t1",
		})
		if !ok || bound != (BoundWake{DeliveryID: testDeliveryID, NativeTurnID: "t1"}) {
			t.Fatalf("BindCodexQueuedWake[%d] = (%#v, %v)", i, bound, ok)
		}

		if _, ok := BindCodexQueuedWake(session, codexWake, PromptEvent{
			HookEventName: "UserPromptSubmit",
			Prompt:        body,
			TurnID:        "\x1c\x1d\x1e\x1f",
		}); ok {
			t.Fatalf("BindCodexQueuedWake[%d] accepted ASCII separator-only TurnID", i)
		}

		strPtr := func(s string) *string { return &s }
		hermesWake := WakeEnvelope{Session: session, DeliveryID: testDeliveryID, Transport: TransportHermesQueue, NativeID: "hermes-sess-1"}
		for _, turnID := range []string{"t2", "other-sess:t2"} {
			bound, ok = BindHermesQueuedWake(session, hermesWake, PromptEvent{
				HookEventName: "TurnStart",
				Prompt:        body,
				TurnID:        turnID,
			})
			if !ok || bound != (BoundWake{DeliveryID: testDeliveryID, NativeTurnID: turnID}) {
				t.Fatalf("BindHermesQueuedWake[%d] absent session (%s) = (%#v, %v)", i, turnID, bound, ok)
			}
		}
		bound, ok = BindHermesQueuedWake(session, hermesWake, PromptEvent{
			HookEventName: "TurnStart",
			Prompt:        body,
			TurnID:        "hermes-sess-1:t2",
			SessionID:     strPtr("hermes-sess-1"),
		})
		if !ok || bound != (BoundWake{DeliveryID: testDeliveryID, NativeTurnID: "hermes-sess-1:t2"}) {
			t.Fatalf("BindHermesQueuedWake[%d] = (%#v, %v)", i, bound, ok)
		}
		for _, badEvt := range []PromptEvent{
			{HookEventName: "TurnStart", Prompt: body, TurnID: "t2", SessionID: strPtr("")},
			{HookEventName: "TurnStart", Prompt: body, TurnID: "t2", SessionID: strPtr("\x1c\x1f")},
			{HookEventName: "TurnStart", Prompt: body, TurnID: "t2", SessionID: strPtr("other-sess")},
			{HookEventName: "TurnStart", Prompt: body, TurnID: "other-sess:t2", SessionID: strPtr("hermes-sess-1")},
		} {
			if _, ok := BindHermesQueuedWake(session, hermesWake, badEvt); ok {
				t.Fatalf("BindHermesQueuedWake[%d] accepted invalid session custody %#v", i, badEvt)
			}
		}
		var nullSessionEvt PromptEvent
		if err := json.Unmarshal([]byte(fmt.Sprintf(`{"hook_event_name":"TurnStart","prompt":%q,"turn_id":"t2","session_id":null}`, body)), &nullSessionEvt); err != nil {
			t.Fatal(err)
		}
		if _, ok := BindHermesQueuedWake(session, hermesWake, nullSessionEvt); !ok {
			t.Fatalf("BindHermesQueuedWake[%d] rejected JSON null session_id", i)
		}
		var nonStringSessionEvt PromptEvent
		if err := json.Unmarshal([]byte(fmt.Sprintf(`{"hook_event_name":"TurnStart","prompt":%q,"turn_id":"t2","session_id":123}`, body)), &nonStringSessionEvt); err == nil {
			t.Fatalf("json.Unmarshal accepted non-string session_id")
		}

		claudeWake := WakeEnvelope{Session: session, DeliveryID: testDeliveryID, Transport: TransportClaudeChannel}
		bound, ok = BindClaudeChannelWake(session, claudeWake, PromptEvent{
			HookEventName: "UserPromptSubmit",
			Prompt:        channelPrompt,
			PromptID:      "p1",
		}, false)
		if !ok || bound != (BoundWake{DeliveryID: testDeliveryID, NativeTurnID: "claude-channel:p1"}) {
			t.Fatalf("BindClaudeChannelWake[%d] = (%#v, %v)", i, bound, ok)
		}
		if _, ok := BindClaudeChannelWake(session, claudeWake, PromptEvent{
			HookEventName: "UserPromptSubmit",
			Prompt:        channelPrompt,
			PromptID:      "p1",
		}, true); ok {
			t.Fatalf("BindClaudeChannelWake accepted joined prompt")
		}
	}
}

func TestCompactJSONLineAndUnavailableReasons(t *testing.T) {
	meta := ClaudeChannelMeta{
		NativeSessionID: testNativeID,
		AgentID:         testAgentID,
		ExecutionID:     testExecID,
		DeliveryID:      testDeliveryID,
	}
	line, err := CompactJSONLine(struct {
		Text string            `json:"text"`
		Meta ClaudeChannelMeta `json:"meta"`
	}{
		Text: "<channel>&\u2028\u2029\\u2028",
		Meta: meta,
	}, ClaudeMaxLineBytes, errors.New("too large"))
	if err != nil {
		t.Fatal(err)
	}
	want := fmt.Sprintf(
		`{"text":"<channel>&`+"\u2028\u2029"+`\\u2028","meta":{"native_session_id":"%s","agent_id":"%s","execution_id":"%s","delivery_id":"%s"}}`+"\n",
		testNativeID, testAgentID, testExecID, testDeliveryID,
	)
	if string(line) != want {
		t.Fatalf("CompactJSONLine = %q; want %q", line, want)
	}

	if UnavailableWakeReason(HarnessCodex) != UnavailableReasonCodex ||
		UnavailableWakeReason(HarnessClaude) != UnavailableReasonClaude ||
		UnavailableWakeReason(HarnessOpenCode) != UnavailableReasonOpenCode ||
		UnavailableWakeReason(HarnessHermes) != UnavailableReasonHermes ||
		UnavailableWakeReason("unknown") != UnavailableReasonDefault {
		t.Fatalf("unexpected UnavailableWakeReason mappings")
	}
}

func writeFakeProcEntry(t *testing.T, procRoot string, pid int, state string, start uint64, boot, cmdline string) {
	t.Helper()
	sysDir := filepath.Join(procRoot, "sys", "kernel", "random")
	if err := os.MkdirAll(sysDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(sysDir, "boot_id"), []byte(boot+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	pidDir := filepath.Join(procRoot, fmt.Sprintf("%d", pid))
	if err := os.MkdirAll(pidDir, 0o755); err != nil {
		t.Fatal(err)
	}
	// Fields after ')': [0]=state, [1..18]=zeros, [19]=start
	fields := make([]string, 22)
	fields[0] = state
	for i := 1; i < len(fields); i++ {
		fields[i] = "0"
	}
	fields[19] = fmt.Sprintf("%d", start)
	statText := fmt.Sprintf("%d (proc) %s\n", pid, strings.Join(fields, " "))
	if err := os.WriteFile(filepath.Join(pidDir, "stat"), []byte(statText), 0o644); err != nil {
		t.Fatal(err)
	}
	if cmdline != "" {
		if err := os.WriteFile(filepath.Join(pidDir, "cmdline"), []byte(cmdline), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

func TestClaudeChannelRegistrySelection(t *testing.T) {
	procRoot := t.TempDir()
	channelDir := t.TempDir()
	parent := ProcessRef{PID: 100, Start: 555, Boot: "boot-1"}
	bridge := ProcessRef{PID: 200, Start: 777, Boot: "boot-1"}
	writeFakeProcEntry(t, procRoot, parent.PID, "S", parent.Start, parent.Boot, "")
	writeFakeProcEntry(t, procRoot, bridge.PID, "S", bridge.Start, bridge.Boot, "")

	sockPath := filepath.Join(channelDir, "bridge.sock")
	regPath := filepath.Join(channelDir, "100.json")
	writeRegistry := func(schema string, p, b ProcessRef, sock string) {
		raw, err := json.Marshal(map[string]any{
			"schema":  schema,
			"parent":  p,
			"process": b,
			"socket":  sock,
		})
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(regPath, raw, 0o600); err != nil {
			t.Fatal(err)
		}
	}

	writeRegistry(ClaudeChannelSchema, parent, bridge, sockPath)
	selected, ok := selectClaudeChannelEndpointAt(procRoot, HarnessClaude, channelDir, parent)
	if !ok || selected.Socket != sockPath || selected.Process != bridge || selected.Parent != parent {
		t.Fatalf("selected = (%#v, %v)", selected, ok)
	}

	// Wrong parent PID or start, wrong harness, foreign schema, relative socket.
	if _, ok := selectClaudeChannelEndpointAt(procRoot, HarnessClaude, channelDir, ProcessRef{PID: 101, Start: 555, Boot: "boot-1"}); ok {
		t.Fatal("accepted wrong parent PID")
	}
	if _, ok := selectClaudeChannelEndpointAt(procRoot, HarnessClaude, channelDir, ProcessRef{PID: 100, Start: 556, Boot: "boot-1"}); ok {
		t.Fatal("accepted wrong parent start")
	}
	if _, ok := selectClaudeChannelEndpointAt(procRoot, HarnessCodex, channelDir, parent); ok {
		t.Fatal("accepted wrong harness")
	}
	writeRegistry("foreign", parent, bridge, sockPath)
	if _, ok := selectClaudeChannelEndpointAt(procRoot, HarnessClaude, channelDir, parent); ok {
		t.Fatal("accepted foreign schema")
	}
	writeRegistry(ClaudeChannelSchema, parent, bridge, "relative.sock")
	if _, ok := selectClaudeChannelEndpointAt(procRoot, HarnessClaude, channelDir, parent); ok {
		t.Fatal("accepted relative socket path")
	}

	// Trailing '}' or ' x' in registry file must be rejected.
	writeRegistry(ClaudeChannelSchema, parent, bridge, sockPath)
	rawValid, err := os.ReadFile(regPath)
	if err != nil {
		t.Fatal(err)
	}
	for _, suffix := range []string{"}", " x"} {
		if err := os.WriteFile(regPath, append(slices.Clone(rawValid), suffix...), 0o600); err != nil {
			t.Fatal(err)
		}
		if _, ok := selectClaudeChannelEndpointAt(procRoot, HarnessClaude, channelDir, parent); ok {
			t.Fatalf("accepted registry with trailing %q", suffix)
		}
	}

	// Zombie (Z) and dead (X) bridge processes must be rejected by ProcessAlive.
	writeRegistry(ClaudeChannelSchema, parent, bridge, sockPath)
	for _, state := range []string{"Z", "X"} {
		writeFakeProcEntry(t, procRoot, bridge.PID, state, bridge.Start, bridge.Boot, "")
		if processAliveAt(procRoot, bridge) {
			t.Fatalf("processAliveAt accepted state %s", state)
		}
		if _, ok := selectClaudeChannelEndpointAt(procRoot, HarnessClaude, channelDir, parent); ok {
			t.Fatalf("selected zombie/dead bridge in state %s", state)
		}
	}
}

func serveClaudeStandinOnce(t *testing.T, listener *net.UnixListener, mode string) <-chan string {
	t.Helper()
	received := make(chan string, 1)
	go func() {
		conn, err := listener.Accept()
		if err != nil {
			received <- ""
			return
		}
		defer func() { _ = conn.Close() }()
		_ = conn.SetDeadline(time.Now().Add(3 * time.Second))
		if mode == "interrupt" {
			buf := make([]byte, 1)
			_, _ = conn.Read(buf)
			received <- string(buf)
			return
		}
		reader := bufio.NewReader(conn)
		line, _ := reader.ReadString('\n')
		received <- strings.TrimSuffix(line, "\n")
		switch mode {
		case "drop":
			return
		case "garbage":
			_, _ = io.WriteString(conn, "not json at all\n")
		case "array":
			_, _ = io.WriteString(conn, "[1,2,3]\n")
		case "nostatus":
			_, _ = io.WriteString(conn, `{"other":1}`+"\n")
		case "trailing_brace":
			_, _ = io.WriteString(conn, `{"status":"written"}}`+"\n")
		case "trailing_token":
			_, _ = io.WriteString(conn, `{"status":"written"} x`+"\n")
		case "oversized_int":
			_, _ = io.WriteString(conn, `{"status":"written","n":`+strings.Repeat("9", 4301)+`}`+"\n")
		case "overflow":
			_, _ = io.WriteString(conn, strings.Repeat("x", ClaudeMaxLineBytes+1)+"\n")
		case "drip":
			for range 12 {
				if _, err := io.WriteString(conn, " "); err != nil {
					return
				}
				time.Sleep(30 * time.Millisecond)
			}
		default:
			_, _ = fmt.Fprintf(conn, `{"status":%q}`+"\n", mode)
		}
	}()
	return received
}

func TestClaudeChannelClientWriteAndErrors(t *testing.T) {
	sockPath := filepath.Join(t.TempDir(), "b.sock")
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: sockPath, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = listener.Close() })

	selfRef, err := procpeer.ProcessRefOf(os.Getpid())
	if err != nil {
		t.Fatal(err)
	}
	meta := ClaudeChannelMeta{
		NativeSessionID: testNativeID,
		AgentID:         testAgentID,
		ExecutionID:     testExecID,
		DeliveryID:      testDeliveryID,
	}

	// 1. Written round-trip and exact wire key order.
	rec := serveClaudeStandinOnce(t, listener, "written")
	if err := WriteClaudeChannel(context.Background(), sockPath, selfRef, selfRef, "wake content", meta); err != nil {
		t.Fatalf("WriteClaudeChannel(written) = %v", err)
	}
	gotLine := <-rec
	wantMetaSub := fmt.Sprintf(
		`"meta":{"native_session_id":%q,"agent_id":%q,"execution_id":%q,"delivery_id":%q}`,
		testNativeID, testAgentID, testExecID, testDeliveryID,
	)
	if !strings.Contains(gotLine, wantMetaSub) {
		t.Fatalf("wire line %q missing exact meta key order %q", gotLine, wantMetaSub)
	}

	// 2. Pre-send unavailable reply from bridge.
	_ = serveClaudeStandinOnce(t, listener, "unavailable")
	err = WriteClaudeChannel(context.Background(), sockPath, selfRef, selfRef, "wake content", meta)
	if !IsClaudeChannelUnavailable(err) || !strings.Contains(err.Error(), "refused") {
		t.Fatalf("expected ClaudeChannelUnavailable on unavailable status, got %v", err)
	}

	// 3. Uncertain, drop, malformed, trailing-token/brace, >4300-digit int, overflow, and drip replies are uncertain (never unavailable).
	for _, mode := range []string{"uncertain", "drop", "garbage", "array", "nostatus", "trailing_brace", "trailing_token", "oversized_int", "overflow"} {
		_ = serveClaudeStandinOnce(t, listener, mode)
		err := WriteClaudeChannel(context.Background(), sockPath, selfRef, selfRef, "wake content", meta)
		if err == nil || IsClaudeChannelUnavailable(err) || !IsClaudeChannelError(err) {
			t.Fatalf("mode %s: expected non-unavailable ClaudeChannelError, got %v", mode, err)
		}
	}

	// 4. Drip reply respects one overall absolute deadline.
	_ = serveClaudeStandinOnce(t, listener, "drip")
	dripClient := ClaudeChannelClient{ConnectTimeout: time.Second, ReplyTimeout: 150 * time.Millisecond}
	err = dripClient.Write(context.Background(), sockPath, selfRef, selfRef, "wake content", meta)
	if err == nil || IsClaudeChannelUnavailable(err) || !strings.Contains(err.Error(), "reply timed out") {
		t.Fatalf("drip timeout: %v", err)
	}

	// 5. Pre-send refusals: oversized line (ASCII, DEL 0x7f, and ensure_ascii=True non-ASCII), wrong PID, changed start, absent socket.
	err = WriteClaudeChannel(context.Background(), sockPath, selfRef, selfRef, strings.Repeat("a", ClaudeMaxLineBytes), meta)
	if !IsClaudeChannelUnavailable(err) || !strings.Contains(err.Error(), "exceeds the line limit") {
		t.Fatalf("oversized request: %v", err)
	}
	err = WriteClaudeChannel(context.Background(), filepath.Join(t.TempDir(), "never-dialed-del.sock"), selfRef, selfRef, strings.Repeat("\x7f", 1400), meta)
	if !IsClaudeChannelUnavailable(err) || !strings.Contains(err.Error(), "exceeds the line limit") {
		t.Fatalf("oversized DEL (0x7f) ensure_ascii=True request: %v", err)
	}
	err = WriteClaudeChannel(context.Background(), filepath.Join(t.TempDir(), "never-dialed.sock"), selfRef, selfRef, strings.Repeat("雪", 2000), meta)
	if !IsClaudeChannelUnavailable(err) || !strings.Contains(err.Error(), "exceeds the line limit") {
		t.Fatalf("oversized non-ASCII ensure_ascii=True request: %v", err)
	}

	wrongPID := selfRef
	wrongPID.PID++
	rec = serveClaudeStandinOnce(t, listener, "written")
	err = WriteClaudeChannel(context.Background(), sockPath, wrongPID, selfRef, "wake content", meta)
	if !IsClaudeChannelUnavailable(err) || !strings.Contains(err.Error(), "different process") || (<-rec) != "" {
		t.Fatalf("wrong PID: %v", err)
	}

	wrongStart := selfRef
	wrongStart.Start++
	rec = serveClaudeStandinOnce(t, listener, "written")
	err = WriteClaudeChannel(context.Background(), sockPath, wrongStart, selfRef, "wake content", meta)
	if !IsClaudeChannelUnavailable(err) || !strings.Contains(err.Error(), "identity") || (<-rec) != "" {
		t.Fatalf("wrong start: %v", err)
	}

	err = WriteClaudeChannel(context.Background(), filepath.Join(t.TempDir(), "absent.sock"), selfRef, selfRef, "wake content", meta)
	if !IsClaudeChannelUnavailable(err) {
		t.Fatalf("absent socket: %v", err)
	}
}

type codexStandinOptions struct {
	loaded            []string
	add               string
	start             string
	malformedLoaded   bool
	requestCollision  bool
	oversizedMethod   string
	oversizedUpgrade  bool
	malformedMethod   string
	trailingMethod    string
	trailingSuffix    string
	upgradeChunkDelay time.Duration
	upgradeChunks     int
	stallMethod       string
	stallDelay        time.Duration
	startError        map[string]any
	threadStatus      any
	updatedAt         any
	queuedItems       []any
	nextCursor        any
}

func defaultCodexStandinOptions() codexStandinOptions {
	return codexStandinOptions{
		loaded:       []string{"native-one"},
		add:          "confirm",
		start:        "confirm",
		threadStatus: "idle",
		updatedAt:    1700000000,
	}
}

func writeWSServerFrame(w io.Writer, body []byte) error {
	var hdr []byte
	n := len(body)
	switch {
	case n < 126:
		hdr = []byte{0x81, byte(n)}
	case n < 65536:
		hdr = make([]byte, 4)
		hdr[0] = 0x81
		hdr[1] = 126
		binary.BigEndian.PutUint16(hdr[2:4], uint16(n))
	default:
		hdr = make([]byte, 10)
		hdr[0] = 0x81
		hdr[1] = 127
		binary.BigEndian.PutUint64(hdr[2:10], uint64(n))
	}
	if _, err := w.Write(hdr); err != nil {
		return err
	}
	_, err := w.Write(body)
	return err
}

func readWSClientFrame(r io.Reader) ([]byte, error) {
	var hdr [2]byte
	if _, err := io.ReadFull(r, hdr[:]); err != nil {
		return nil, err
	}
	if hdr[0]&0x0f == 8 {
		return nil, io.EOF
	}
	masked := hdr[1]&0x80 != 0
	length := uint64(hdr[1] & 0x7f)
	switch length {
	case 126:
		var ext [2]byte
		if _, err := io.ReadFull(r, ext[:]); err != nil {
			return nil, err
		}
		length = uint64(binary.BigEndian.Uint16(ext[:]))
	case 127:
		var ext [8]byte
		if _, err := io.ReadFull(r, ext[:]); err != nil {
			return nil, err
		}
		length = binary.BigEndian.Uint64(ext[:])
	}
	var mask [4]byte
	if masked {
		if _, err := io.ReadFull(r, mask[:]); err != nil {
			return nil, err
		}
	}
	raw := make([]byte, int(length))
	if _, err := io.ReadFull(r, raw); err != nil {
		return nil, err
	}
	if masked {
		for i := range raw {
			raw[i] ^= mask[i%4]
		}
	}
	return raw, nil
}

func serveCodexStandinOnce(t *testing.T, listener *net.UnixListener, opts codexStandinOptions) <-chan []map[string]any {
	t.Helper()
	done := make(chan []map[string]any, 1)
	go func() {
		var requests []map[string]any
		defer func() { done <- requests }()
		conn, err := listener.Accept()
		if err != nil {
			return
		}
		defer func() { _ = conn.Close() }()
		_ = conn.SetDeadline(time.Now().Add(3 * time.Second))
		reader := bufio.NewReader(conn)
		var secKey string
		for {
			line, err := reader.ReadString('\n')
			if err != nil || line == "\r\n" {
				if err != nil {
					return
				}
				break
			}
			if k, v, ok := strings.Cut(line, ":"); ok && strings.EqualFold(strings.TrimSpace(k), "sec-websocket-key") {
				secKey = strings.TrimSpace(v)
			}
		}
		sum := sha1.Sum([]byte(secKey + codexWebSocketGUID))
		accept := base64.StdEncoding.EncodeToString(sum[:])
		if opts.oversizedUpgrade {
			_, _ = io.WriteString(conn, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: "+accept+"\r\nX-Pad: "+strings.Repeat("p", CodexMaxPeerBytes)+"\r\n\r\n")
			return
		}
		upgradeResp := "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + "\r\n\r\n"
		if opts.upgradeChunks > 1 {
			chunkLen := len(upgradeResp) / opts.upgradeChunks
			for i := 0; i < opts.upgradeChunks; i++ {
				time.Sleep(opts.upgradeChunkDelay)
				part := upgradeResp[i*chunkLen : (i+1)*chunkLen]
				if i == opts.upgradeChunks-1 {
					part = upgradeResp[i*chunkLen:]
				}
				if _, err := io.WriteString(conn, part); err != nil {
					return
				}
			}
		} else {
			if opts.upgradeChunkDelay > 0 {
				time.Sleep(opts.upgradeChunkDelay)
			}
			_, _ = io.WriteString(conn, upgradeResp)
		}
		for {
			raw, err := readWSClientFrame(reader)
			if err != nil {
				return
			}
			var req map[string]any
			if err := json.Unmarshal(raw, &req); err != nil {
				return
			}
			requests = append(requests, req)
			method, _ := req["method"].(string)
			if method == "initialized" {
				continue
			}
			if method == opts.oversizedMethod {
				body, _ := json.Marshal(map[string]any{
					"id":     req["id"],
					"result": map[string]any{"padding": strings.Repeat("x", CodexMaxPeerBytes)},
				})
				_ = writeWSServerFrame(conn, body)
				return
			}
			var result any
			switch method {
			case "initialize":
				result = map[string]any{"userAgent": "fixture"}
			case "thread/loaded/list":
				if opts.malformedLoaded {
					result = map[string]any{}
				} else {
					result = map[string]any{"data": opts.loaded, "nextCursor": nil}
				}
			case "thread/read":
				params, _ := req["params"].(map[string]any)
				status := opts.threadStatus
				if s, ok := status.(string); ok {
					status = map[string]any{"type": s}
				}
				result = map[string]any{"thread": map[string]any{
					"id":        params["threadId"],
					"status":    status,
					"updatedAt": opts.updatedAt,
				}}
			case "thread/queue/list":
				items := make([]any, 0, len(opts.queuedItems))
				for _, item := range opts.queuedItems {
					if s, ok := item.(string); ok {
						items = append(items, map[string]any{"id": s})
					} else {
						items = append(items, item)
					}
				}
				result = map[string]any{"data": items, "nextCursor": opts.nextCursor}
			case "thread/queue/add":
				if opts.add == "refuse" {
					return
				}
				if opts.requestCollision {
					notice, _ := json.Marshal(map[string]any{"id": req["id"], "method": "server/notice", "params": map[string]any{}})
					_ = writeWSServerFrame(conn, notice)
				}
				params, _ := req["params"].(map[string]any)
				sub := map[string]any{"id": "queued-one"}
				for k, v := range params {
					sub[k] = v
				}
				result = map[string]any{"queuedSubmission": sub}
			case "thread/queue/start":
				if opts.startError != nil {
					body, _ := json.Marshal(map[string]any{"id": req["id"], "error": opts.startError})
					_ = writeWSServerFrame(conn, body)
					continue
				}
				if opts.start == "drop" {
					return
				}
				if opts.start == "busy" || opts.start == "busy_extra" || opts.start == "unknown" || opts.start == "missing" {
					params, _ := req["params"].(map[string]any)
					errObj := map[string]any{"code": CodexBusyCode, "message": CodexBusyMessage}
					switch opts.start {
					case "busy_extra":
						errObj["data"] = map[string]any{"hint": "tolerate extra fields"}
					case "unknown":
						errObj = map[string]any{"code": -32000, "message": "queued submission is not pending"}
					case "missing":
						errObj = map[string]any{"code": CodexBusyCode, "message": fmt.Sprintf("queued submission not found: %v", params["queuedSubmissionId"])}
					}
					body, _ := json.Marshal(map[string]any{"id": req["id"], "error": errObj})
					_ = writeWSServerFrame(conn, body)
					continue
				}
				result = map[string]any{"turn": map[string]any{"id": "turn-one", "status": "inProgress"}}
			}
			if method == opts.malformedMethod {
				result = []any{}
			}
			if method == opts.stallMethod {
				time.Sleep(opts.stallDelay)
				return
			}
			body, _ := json.Marshal(map[string]any{"id": req["id"], "result": result})
			if method == opts.trailingMethod {
				body = append(body, opts.trailingSuffix...)
			}
			_ = writeWSServerFrame(conn, body)
		}
	}()
	return done
}

func TestCodexQueueEndpointDiscovery(t *testing.T) {
	ep, ok := ParseCodexQueueArgv(HarnessCodex, []string{"codex", "app-server", "--listen", "unix:///tmp/codex.sock"})
	if !ok || ep.Endpoint != "/tmp/codex.sock" || ep.Owner != procpeer.ProcessOwner || ep.OwnerMode != "process" {
		t.Fatalf("app-server --listen: (%#v, %v)", ep, ok)
	}
	ep, ok = ParseCodexQueueArgv(HarnessCodex, []string{"codex", "app-server", "--remote", "unix:///tmp/shared.sock"})
	if !ok || ep.Endpoint != "/tmp/shared.sock" || ep.Owner != procpeer.UserOwner || ep.OwnerMode != "uid" {
		t.Fatalf("app-server --remote should select uid mode: (%#v, %v)", ep, ok)
	}
	ep, ok = ParseCodexQueueArgv(HarnessCodex, []string{"codex", "--remote", "unix:///tmp/shared.sock"})
	if !ok || ep.Endpoint != "/tmp/shared.sock" || ep.Owner != procpeer.UserOwner || ep.OwnerMode != "uid" {
		t.Fatalf("tui --remote: (%#v, %v)", ep, ok)
	}
	for _, argv := range [][]string{
		{"codex", "app-server", "--listen", "ws://127.0.0.1:9000"},
		{"codex", "app-server", "--listen", "unix://relative.sock"},
		{"codex", "--listen", "unix:///tmp/no-mode.sock"},
	} {
		if _, ok := ParseCodexQueueArgv(HarnessCodex, argv); ok {
			t.Fatalf("unexpectedly accepted argv %v", argv)
		}
	}
	prior := WakeEnvelope{
		Transport:          TransportCodexQueue,
		Status:             "queued",
		QueuedSubmissionID: "q-1",
		Endpoint:           "/tmp/shared.sock",
		NativeID:           "native-one",
	}
	if !CanResumeCodexQueued(prior, ep, "native-one") || CanResumeCodexQueued(prior, ep, "other") {
		t.Fatalf("CanResumeCodexQueued mismatch")
	}
}

func TestCodexQueueEnqueueAndStartOutcomes(t *testing.T) {
	sockPath := filepath.Join(t.TempDir(), "c.sock")
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: sockPath, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = listener.Close() })

	selfRef, err := procpeer.ProcessRefOf(os.Getpid())
	if err != nil {
		t.Fatal(err)
	}

	// 1. Confirmed add + start.
	reqsCh := serveCodexStandinOnce(t, listener, defaultCodexStandinOptions())
	qID, started, err := CodexEnqueue(context.Background(), sockPath, selfRef, "native-one", "wake only", "delivery-one", procpeer.ProcessOwner)
	if err != nil || qID != "queued-one" || !started {
		t.Fatalf("Enqueue(confirm) = (%q, %v, %v)", qID, started, err)
	}
	reqs := <-reqsCh
	var methods []string
	for _, r := range reqs {
		m, _ := r["method"].(string)
		methods = append(methods, m)
	}
	if !slices.Equal(methods, []string{"initialize", "initialized", "thread/loaded/list", "thread/queue/add", "thread/queue/start"}) {
		t.Fatalf("methods = %v", methods)
	}

	// 2. Busy start (with and without extra fields), unknown start, and dropped start keep queuedID with started=false.
	for _, startMode := range []string{"busy", "busy_extra", "unknown", "drop"} {
		opts := defaultCodexStandinOptions()
		opts.start = startMode
		_ = serveCodexStandinOnce(t, listener, opts)
		qID, started, err := CodexEnqueue(context.Background(), sockPath, selfRef, "native-one", "wake only", "delivery-one", procpeer.ProcessOwner)
		if err != nil || qID != "queued-one" || started {
			t.Fatalf("Enqueue(%s) = (%q, %v, %v)", startMode, qID, started, err)
		}
	}

	// 3. StartOutcome classifications: confirm, busy_extra, missing, drop, refused, unknown.
	for _, tc := range []struct {
		name   string
		opts   codexStandinOptions
		want   string
		wantOK bool
	}{
		{"started", defaultCodexStandinOptions(), CodexStartStarted, true},
		{"busy", func() codexStandinOptions { o := defaultCodexStandinOptions(); o.start = "busy_extra"; return o }(), CodexStartBusy, false},
		{"missing", func() codexStandinOptions { o := defaultCodexStandinOptions(); o.start = "missing"; return o }(), CodexStartMissing, false},
		{"drop", func() codexStandinOptions { o := defaultCodexStandinOptions(); o.start = "drop"; return o }(), CodexStartUnknown, false},
		{"refused-other-item", func() codexStandinOptions {
			o := defaultCodexStandinOptions()
			o.startError = map[string]any{"code": -32600, "message": "queued submission not found: another-item"}
			return o
		}(), CodexStartRefused, false},
		{"refused-empty", func() codexStandinOptions {
			o := defaultCodexStandinOptions()
			o.startError = map[string]any{"code": -32600, "message": "queue is empty"}
			return o
		}(), CodexStartRefused, false},
		{"unknown-code", func() codexStandinOptions {
			o := defaultCodexStandinOptions()
			o.startError = map[string]any{"code": -32000, "message": "queued submission not found: queued-one"}
			return o
		}(), CodexStartUnknown, false},
		{"unknown-string-code", func() codexStandinOptions {
			o := defaultCodexStandinOptions()
			o.startError = map[string]any{"code": "-32600", "message": "queued submission not found: queued-one"}
			return o
		}(), CodexStartUnknown, false},
		{"trailing-token-on-start", func() codexStandinOptions {
			o := defaultCodexStandinOptions()
			o.trailingMethod = "thread/queue/start"
			o.trailingSuffix = " x"
			return o
		}(), CodexStartUnknown, false},
		{"trailing-brace-on-start", func() codexStandinOptions {
			o := defaultCodexStandinOptions()
			o.trailingMethod = "thread/queue/start"
			o.trailingSuffix = "}"
			return o
		}(), CodexStartUnknown, false},
	} {
		_ = serveCodexStandinOnce(t, listener, tc.opts)
		outcome, err := CodexStartOutcome(context.Background(), sockPath, selfRef, "native-one", "queued-one", procpeer.ProcessOwner)
		if err != nil || outcome != tc.want {
			t.Fatalf("%s: StartOutcome = (%q, %v); want %q", tc.name, outcome, err, tc.want)
		}
		_ = serveCodexStandinOnce(t, listener, tc.opts)
		ok, err := CodexStart(context.Background(), sockPath, selfRef, "native-one", "queued-one", procpeer.ProcessOwner)
		if err != nil || ok != tc.wantOK {
			t.Fatalf("%s: Start = (%v, %v); want %v", tc.name, ok, err, tc.wantOK)
		}
	}
}

func runWakeTestChildServer(t *testing.T, sockPath string) {
	t.Helper()
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: sockPath, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = listener.Close() }()
	if _, err := fmt.Fprintln(os.Stdout, "READY"); err != nil {
		t.Fatal(err)
	}
	ch := serveCodexStandinOnce(t, listener, defaultCodexStandinOptions())
	<-ch
}

func startWakeTestChildServer(t *testing.T) (string, ProcessRef) {
	t.Helper()
	sockPath := filepath.Join(t.TempDir(), "child.sock")
	cmd := exec.Command(os.Args[0], "-test.run=^TestWakeWordingAndRecognizers$")
	cmd.Env = append(os.Environ(), "CAIRN_WAKE_TEST_CHILD_SOCK="+sockPath)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	if cmd.Process.Pid <= 1 {
		t.Fatal("invalid child PID")
	}
	t.Cleanup(func() {
		if cmd.Process.Pid > 1 {
			_ = cmd.Process.Kill()
			_ = cmd.Wait()
		}
	})
	ready := make(chan string, 1)
	go func() {
		line, _ := bufio.NewReader(stdout).ReadString('\n')
		ready <- line
	}()
	select {
	case line := <-ready:
		if line != "READY\n" {
			t.Fatalf("child readiness = %q (%s)", line, stderr.String())
		}
	case <-time.After(5 * time.Second):
		t.Fatalf("child readiness timeout (%s)", stderr.String())
	}
	ref, err := procpeer.ProcessRefOf(cmd.Process.Pid)
	if err != nil {
		t.Fatal(err)
	}
	return sockPath, ref
}

func TestCodexQueueRefusalsLimitsAndInspect(t *testing.T) {
	sockPath := filepath.Join(t.TempDir(), "c2.sock")
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: sockPath, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = listener.Close() })

	selfRef, err := procpeer.ProcessRefOf(os.Getpid())
	if err != nil {
		t.Fatal(err)
	}

	// 1. Unloaded conversation and malformed loaded list are pre-send QueueUnavailable.
	unloaded := defaultCodexStandinOptions()
	unloaded.loaded = []string{}
	reqsCh := serveCodexStandinOnce(t, listener, unloaded)
	_, _, err = CodexEnqueue(context.Background(), sockPath, selfRef, "native-one", "wake", "d1", procpeer.ProcessOwner)
	if !IsCodexQueueUnavailable(err) || !strings.Contains(err.Error(), "not loaded") {
		t.Fatalf("unloaded: %v", err)
	}
	for _, r := range <-reqsCh {
		if r["method"] == "thread/queue/add" {
			t.Fatal("thread/queue/add called on unloaded conversation")
		}
	}

	// 2. Oversized responses: handshake -> QueueUnavailable, initialize -> QueueUnavailable, add -> QueueError ("byte limit").
	overUpgrade := defaultCodexStandinOptions()
	overUpgrade.oversizedUpgrade = true
	_ = serveCodexStandinOnce(t, listener, overUpgrade)
	_, _, err = CodexEnqueue(context.Background(), sockPath, selfRef, "native-one", "wake", "d1", procpeer.ProcessOwner)
	if !IsCodexQueueUnavailable(err) || !strings.Contains(err.Error(), "handshake did not complete within byte limit") {
		t.Fatalf("oversized upgrade: %v", err)
	}

	overInit := defaultCodexStandinOptions()
	overInit.oversizedMethod = "initialize"
	_ = serveCodexStandinOnce(t, listener, overInit)
	_, _, err = CodexEnqueue(context.Background(), sockPath, selfRef, "native-one", "wake", "d1", procpeer.ProcessOwner)
	if !IsCodexQueueUnavailable(err) {
		t.Fatalf("oversized initialize: %v", err)
	}

	overAdd := defaultCodexStandinOptions()
	overAdd.oversizedMethod = "thread/queue/add"
	_ = serveCodexStandinOnce(t, listener, overAdd)
	_, _, err = CodexEnqueue(context.Background(), sockPath, selfRef, "native-one", "wake", "d1", procpeer.ProcessOwner)
	if err == nil || IsCodexQueueUnavailable(err) || !strings.Contains(err.Error(), "byte limit") {
		t.Fatalf("oversized add: %v", err)
	}

	// 2b. Trailing tokens/braces on initialize -> QueueUnavailable; on thread/queue/add -> uncertain QueueError.
	for _, suffix := range []string{" x", "}"} {
		trailInit := defaultCodexStandinOptions()
		trailInit.trailingMethod = "initialize"
		trailInit.trailingSuffix = suffix
		_ = serveCodexStandinOnce(t, listener, trailInit)
		_, _, err = CodexEnqueue(context.Background(), sockPath, selfRef, "native-one", "wake", "d1", procpeer.ProcessOwner)
		if !IsCodexQueueUnavailable(err) || !strings.Contains(err.Error(), "native queue is not ready:") {
			t.Fatalf("trailing %q on initialize: %v", suffix, err)
		}

		trailAdd := defaultCodexStandinOptions()
		trailAdd.trailingMethod = "thread/queue/add"
		trailAdd.trailingSuffix = suffix
		_ = serveCodexStandinOnce(t, listener, trailAdd)
		_, _, err = CodexEnqueue(context.Background(), sockPath, selfRef, "native-one", "wake", "d1", procpeer.ProcessOwner)
		if err == nil || IsCodexQueueUnavailable(err) || !strings.Contains(err.Error(), "native queue outcome is uncertain; do not automatically resend") {
			t.Fatalf("trailing %q on thread/queue/add: %v", suffix, err)
		}
	}

	// 2c. Per-read handshake timeout (T1): 3 chunks at 60ms each (180ms total) succeed under 150ms ConnectTimeout; single 260ms stall fails.
	dribbleOK := defaultCodexStandinOptions()
	dribbleOK.upgradeChunks = 3
	dribbleOK.upgradeChunkDelay = 60 * time.Millisecond
	_ = serveCodexStandinOnce(t, listener, dribbleOK)
	dribbleClient := CodexQueueClient{ConnectTimeout: 150 * time.Millisecond, RPCTimeout: time.Second}
	qID, started, err := dribbleClient.Enqueue(context.Background(), sockPath, selfRef, "native-one", "wake", "d1", procpeer.ProcessOwner)
	if err != nil || qID != "queued-one" || !started {
		t.Fatalf("dribbled 101 handshake under per-read timeout: (%q, %v, %v)", qID, started, err)
	}

	dribbleStall := defaultCodexStandinOptions()
	dribbleStall.upgradeChunks = 1
	dribbleStall.upgradeChunkDelay = 260 * time.Millisecond
	_ = serveCodexStandinOnce(t, listener, dribbleStall)
	_, _, err = dribbleClient.Enqueue(context.Background(), sockPath, selfRef, "native-one", "wake", "d1", procpeer.ProcessOwner)
	if !IsCodexQueueUnavailable(err) || !strings.Contains(err.Error(), "native queue endpoint is unavailable") {
		t.Fatalf("stalled 101 handshake: %v", err)
	}

	// 2d. Socket read timeout during thread/queue/add recv (T2) -> "native queue outcome is uncertain; do not automatically resend".
	stallAdd := defaultCodexStandinOptions()
	stallAdd.stallMethod = "thread/queue/add"
	stallAdd.stallDelay = 300 * time.Millisecond
	_ = serveCodexStandinOnce(t, listener, stallAdd)
	shortRPCClient := CodexQueueClient{ConnectTimeout: time.Second, RPCTimeout: 150 * time.Millisecond}
	_, _, err = shortRPCClient.Enqueue(context.Background(), sockPath, selfRef, "native-one", "wake", "d1", procpeer.ProcessOwner)
	if err == nil || IsCodexQueueUnavailable(err) || err.Error() != "native queue outcome is uncertain; do not automatically resend" {
		t.Fatalf("stalled thread/queue/add recv timeout: %v", err)
	}

	// 3. Deadline expired before thread/queue/add send is retryable QueueUnavailable with 0 add sends.
	reqsCh = serveCodexStandinOnce(t, listener, defaultCodexStandinOptions())
	expiringClient := CodexQueueClient{
		BeforeCall: func(method string, rpc *codexRPC) {
			if method == "thread/queue/add" {
				rpc.Deadline = time.Now().Add(-time.Second)
			}
		},
	}
	_, _, err = expiringClient.Enqueue(context.Background(), sockPath, selfRef, "native-one", "wake", "d1", procpeer.ProcessOwner)
	if !IsCodexQueueUnavailable(err) || !strings.Contains(err.Error(), "deadline expired before request send") {
		t.Fatalf("expiring before add: %v", err)
	}
	for _, r := range <-reqsCh {
		if r["method"] == "thread/queue/add" {
			t.Fatal("thread/queue/add sent after pre-send deadline expiry")
		}
	}

	// 4. Server request with colliding id is ignored.
	collision := defaultCodexStandinOptions()
	collision.requestCollision = true
	_ = serveCodexStandinOnce(t, listener, collision)
	qID, started, err = CodexEnqueue(context.Background(), sockPath, selfRef, "native-one", "wake", "d1", procpeer.ProcessOwner)
	if err != nil || qID != "queued-one" || !started {
		t.Fatalf("collision: (%q, %v, %v)", qID, started, err)
	}

	// 5. Shared app-server UserOwner route with distinct child server process + VerifiedPeer.
	childSock, childRef := startWakeTestChildServer(t)
	peer, err := CodexVerifiedPeer(context.Background(), childSock, selfRef, procpeer.UserOwner)
	if err != nil || peer != childRef || peer.PID == selfRef.PID {
		t.Fatalf("VerifiedPeer(UserOwner) = (%#v, %v); want %#v", peer, err, childRef)
	}

	// 6. InspectThread read-only status & queue probe.
	for _, status := range []string{"active", "idle", "systemError"} {
		opts := defaultCodexStandinOptions()
		opts.threadStatus = status
		opts.updatedAt = 1700000123
		opts.queuedItems = []any{"queued-one", "queued-two"}
		reqsCh := serveCodexStandinOnce(t, listener, opts)
		insp, err := CodexInspectThread(context.Background(), sockPath, selfRef, "native-one", procpeer.ProcessOwner, "queued-two")
		if err != nil || insp.StatusType != status || insp.UpdatedAt == nil || *insp.UpdatedAt != 1700000123 ||
			insp.QueueCount != 2 || !slices.Equal(insp.QueuedIDs, []string{"queued-one", "queued-two"}) ||
			insp.QueuedPresent == nil || !*insp.QueuedPresent || insp.Peer != selfRef {
			t.Fatalf("InspectThread(%s) = (%#v, %v)", status, insp, err)
		}
		for _, r := range <-reqsCh {
			m, _ := r["method"].(string)
			if slices.Contains([]string{"thread/resume", "turn/start", "turn/interrupt", "thread/queue/add", "thread/queue/start"}, m) {
				t.Fatalf("InspectThread invoked mutating method %s", m)
			}
		}
	}
}
