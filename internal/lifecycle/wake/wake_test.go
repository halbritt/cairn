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
	"strconv"
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

// Queue fixtures are private Go sockets owned by this test process. No native
// harness, Python adapter, installed controller, or external session is invoked.
func wakeQueueFixture(t *testing.T, response []byte, delay time.Duration) (string, ProcessRef, <-chan []byte) {
	t.Helper()
	directory, err := os.MkdirTemp("", "wake-q-")
	if err != nil {
		t.Fatal(err)
	}
	endpoint := filepath.Join(directory, "q.sock")
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: endpoint, Net: "unix"})
	if err != nil {
		_ = os.RemoveAll(directory)
		t.Fatal(err)
	}
	process, err := procpeer.ProcessRefOf(os.Getpid())
	if err != nil {
		_ = listener.Close()
		_ = os.RemoveAll(directory)
		t.Fatal(err)
	}
	requests := make(chan []byte, 32)
	done := make(chan struct{})
	go func() {
		defer close(done)
		for {
			conn, err := listener.AcceptUnix()
			if err != nil {
				return
			}
			_ = conn.SetDeadline(time.Now().Add(2 * time.Second))
			frame, err := bufio.NewReader(conn).ReadBytes('\n')
			if len(frame) != 0 {
				requests <- frame
			}
			if err == nil {
				if delay > 0 {
					time.Sleep(delay)
				}
				_, _ = conn.Write(response)
			}
			_ = conn.Close()
		}
	}()
	t.Cleanup(func() {
		_ = listener.Close()
		<-done
		_ = os.RemoveAll(directory)
	})
	return endpoint, process, requests
}

func wakeQueueRequest(t *testing.T, requests <-chan []byte, id int, method string) map[string]any {
	t.Helper()
	var raw []byte
	select {
	case raw = <-requests:
	case <-time.After(2 * time.Second):
		t.Fatal("private fixture received no request")
	}
	if len(raw) == 0 || raw[len(raw)-1] != '\n' || bytes.Count(raw, []byte{'\n'}) != 1 {
		t.Fatalf("request is not one JSON line: %q", raw)
	}
	for _, b := range raw {
		if b > 127 {
			t.Fatal("request is not ASCII JSON")
		}
	}
	var request struct {
		ID     int            `json:"id"`
		Method string         `json:"method"`
		Params map[string]any `json:"params"`
	}
	if err := json.Unmarshal(raw, &request); err != nil {
		t.Fatal(err)
	}
	if request.ID != id || request.Method != method {
		t.Fatalf("wrong request identity: %s", raw)
	}
	return request.Params
}

func wakeJSON(t *testing.T, value any) []byte {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func wakeDribbledReplyFixture(t *testing.T, response []byte, gaps []time.Duration) (string, ProcessRef, <-chan []byte) {
	t.Helper()
	directory, err := os.MkdirTemp("", "wake-drip-")
	if err != nil {
		t.Fatal(err)
	}
	endpoint := filepath.Join(directory, "q.sock")
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: endpoint, Net: "unix"})
	if err != nil {
		_ = os.RemoveAll(directory)
		t.Fatal(err)
	}
	process, err := procpeer.ProcessRefOf(os.Getpid())
	if err != nil {
		_ = listener.Close()
		_ = os.RemoveAll(directory)
		t.Fatal(err)
	}
	requests := make(chan []byte, 1)
	done, stop := make(chan struct{}), make(chan struct{})
	go func() {
		defer close(done)
		conn, err := listener.AcceptUnix()
		if err != nil {
			return
		}
		defer conn.Close()
		_ = conn.SetDeadline(time.Now().Add(3 * time.Second))
		frame, err := bufio.NewReader(conn).ReadBytes('\n')
		if err != nil {
			return
		}
		requests <- frame
		chunks := len(gaps) + 1
		for i := 0; i < chunks; i++ {
			if i > 0 {
				timer := time.NewTimer(gaps[i-1])
				select {
				case <-timer.C:
				case <-stop:
					timer.Stop()
					return
				}
			}
			if _, err := conn.Write(response[i*len(response)/chunks : (i+1)*len(response)/chunks]); err != nil {
				return
			}
		}
	}()
	t.Cleanup(func() { close(stop); _ = listener.Close(); <-done; _ = os.RemoveAll(directory) })
	return endpoint, process, requests
}

func checkQueueDribbledReply(t *testing.T, response string, id int, method string, call func(string, ProcessRef, time.Duration) error, uncertain func(error) bool) {
	t.Helper()
	const timeout = 150 * time.Millisecond
	t.Run("total_exceeds_read_timeout", func(t *testing.T) {
		endpoint, process, requests := wakeDribbledReplyFixture(t, []byte(response), []time.Duration{40 * time.Millisecond, 40 * time.Millisecond, 40 * time.Millisecond, 40 * time.Millisecond, 40 * time.Millisecond})
		started := time.Now()
		if err := call(endpoint, process, timeout); err != nil {
			t.Fatal("short inter-chunk gaps refused", err)
		}
		if time.Since(started) <= timeout {
			t.Fatal("fixture did not exceed a single read timeout")
		}
		wakeQueueRequest(t, requests, id, method)
	})
	t.Run("one_gap_exceeds_read_timeout", func(t *testing.T) {
		endpoint, process, requests := wakeDribbledReplyFixture(t, []byte(response), []time.Duration{2 * timeout})
		if err := call(endpoint, process, timeout); !uncertain(err) {
			t.Fatalf("long gap: %T %v", err, err)
		}
		wakeQueueRequest(t, requests, id, method)
	})
}

func TestOpenCodeQueue(t *testing.T) {
	t.Run("OpenCode request binding", func(t *testing.T) {
		agent := SessionRef{AgentID: testAgentID, ExecutionID: testExecID}
		wake := WakeEnvelope{Session: agent, Transport: TransportOpenCodeQueue, DeliveryID: testDeliveryID, RequestID: "request", CancelCapable: true}
		event := PromptEvent{HookEventName: "TurnStart", TurnID: "turn", DeliveryID: testDeliveryID, RequestID: "request"}
		bound, ok := BindOpenCodeQueuedWake(agent, wake, event)
		if !ok || bound.DeliveryID != testDeliveryID || bound.NativeTurnID != "turn" || !bound.TurnExclusive {
			t.Fatal("valid OpenCode binding", bound, ok)
		}
		for _, change := range []func(*WakeEnvelope, *PromptEvent){
			func(w *WakeEnvelope, _ *PromptEvent) { w.Transport = TransportCodexQueue },
			func(w *WakeEnvelope, _ *PromptEvent) { w.Session.ExecutionID = "other" },
			func(w *WakeEnvelope, _ *PromptEvent) { w.RequestID = "" },
			func(w *WakeEnvelope, _ *PromptEvent) { w.DeliveryID = "" },
			func(_ *WakeEnvelope, e *PromptEvent) { e.RequestID = "other" },
			func(_ *WakeEnvelope, e *PromptEvent) { e.DeliveryID = "other" },
			func(_ *WakeEnvelope, e *PromptEvent) { e.HookEventName = "TurnEnd" },
			func(_ *WakeEnvelope, e *PromptEvent) { e.TurnID = "\x00" },
			func(_ *WakeEnvelope, e *PromptEvent) { e.TurnID = strings.Repeat("雪", 86) },
		} {
			w, e := wake, event
			change(&w, &e)
			if _, ok := BindOpenCodeQueuedWake(agent, w, e); ok {
				t.Fatal("mismatched wake bound")
			}
		}
		wake.CancelCapable = false
		if bound, ok := BindOpenCodeQueuedWake(agent, wake, event); !ok || bound.TurnExclusive {
			t.Fatal("non-cancel-capable binding became exclusive", bound, ok)
		}
		for _, raw := range []string{`{"request_id":1}`, `{"cancel_capable":"true"}`} {
			var decoded WakeEnvelope
			if err := json.Unmarshal([]byte(raw), &decoded); err == nil {
				t.Fatalf("accepted wrong field type: %s", raw)
			}
		}
	})
	ctx := context.Background()
	ack := `{"id":1,"result":{"queued":true,"queued_id":"d","request_id":"d","session_id":"n","started":true}}`
	t.Run("enqueue_and_ASCII_frame", func(t *testing.T) {
		endpoint, process, requests := wakeQueueFixture(t, []byte(ack), 0)
		id, started, err := OpenCodeEnqueue(ctx, endpoint, process, "n", "雪😀\n", "d", "", "")
		if err != nil || id != "d" || !started {
			t.Fatalf("enqueue: %q %v %v", id, started, err)
		}
		params := wakeQueueRequest(t, requests, 1, "session/prompt_idle")
		for k, want := range map[string]string{"session_id": "n", "text": "雪😀\n", "delivery_id": "d", "request_id": "d", "expected_session_id": "n"} {
			if params[k] != want {
				t.Fatalf("%s=%v, want %q", k, params[k], want)
			}
		}
	})
	t.Run("explicit_request_and_expected_session", func(t *testing.T) {
		endpoint, process, requests := wakeQueueFixture(t, []byte(strings.Replace(ack, `"request_id":"d"`, `"request_id":"r"`, 1)), 0)
		if _, _, err := OpenCodeEnqueue(ctx, endpoint, process, "n", "wake", "d", "expected", "r"); err != nil {
			t.Fatal(err)
		}
		params := wakeQueueRequest(t, requests, 1, "session/prompt_idle")
		if params["request_id"] != "r" || params["expected_session_id"] != "expected" {
			t.Fatal(params)
		}
	})
	t.Run("input_validation_before_connect", func(t *testing.T) {
		for _, field := range []string{"native_id", "delivery_id", "request_id", "expected_session_id"} {
			args := []string{"n", "wake", "d", "expected", "r"}
			index := map[string]int{"native_id": 0, "delivery_id": 2, "expected_session_id": 3, "request_id": 4}[field]
			args[index] = "\x1c\u0085 "
			_, _, err := OpenCodeEnqueue(ctx, "/absent", ProcessRef{}, args[0], args[1], args[2], args[3], args[4])
			if !IsOpenCodeQueueUnavailable(err) || err.Error() != field+" must be a nonempty string" {
				t.Fatalf("%s: %v", field, err)
			}
		}
	})
	t.Run("atomic_error_classes", func(t *testing.T) {
		for _, tc := range []struct {
			code          int
			message, kind string
		}{
			{-32600, "busy", "unavailable"}, {-32001, "missing", "unavailable"}, {-32002, "session", "unavailable"},
			{-32004, "unsupported", "unsupported"}, {-32601, "method", "unsupported"}, {-32000, "UNSUPPORTED_CONTROL: idle", "unsupported"},
			{-32003, "refused", "refused"}, {-32005, "admitted", "admitted"}, {-32000, "other", "uncertain"},
		} {
			t.Run(strconv.Itoa(tc.code)+tc.kind, func(t *testing.T) {
				response := wakeJSON(t, map[string]any{"id": 1, "error": map[string]any{"code": tc.code, "message": tc.message}})
				endpoint, process, _ := wakeQueueFixture(t, response, 0)
				_, _, err := OpenCodeEnqueue(ctx, endpoint, process, "n", "wake", "d", "", "")
				if err == nil {
					t.Fatal("accepted RPC error")
				}
				match := false
				switch tc.kind {
				case "unavailable":
					match = IsOpenCodeQueueUnavailable(err) && !IsOpenCodeQueueUnsupported(err)
				case "unsupported":
					match = IsOpenCodeQueueUnsupported(err) && IsOpenCodeQueueUnavailable(err)
				case "refused":
					match = IsOpenCodeQueueRefused(err) && err.Error() == "native OpenCode refused prompt_idle: "+tc.message
				case "admitted":
					match = IsOpenCodeQueueAlreadyAdmitted(err) && err.Error() == "native OpenCode request already admitted: "+tc.message
				case "uncertain":
					var uncertain *OpenCodeQueueError
					match = errors.As(err, &uncertain) && !IsOpenCodeQueueUnavailable(err)
				}
				if !match {
					t.Fatalf("%s: %T %v", tc.kind, err, err)
				}
			})
		}
	})
	t.Run("unconfirmed_acknowledgements_are_uncertain", func(t *testing.T) {
		responses := []string{"", `null`, `{}`, `{"id":1,"error":{"code":-32600}}`, `{"id":1,"error":{"code":-32600.0,"message":"busy"}}`,
			strings.Replace(ack, `"id":1`, `"id":1.0`, 1), strings.Replace(ack, `"id":1`, `"id":true`, 1),
			strings.Replace(ack, `"queued":true`, `"queued":1`, 1), strings.Replace(ack, `"started":true`, `"started":false`, 1),
			strings.Replace(ack, `"queued_id":"d"`, `"queued_id":"other"`, 1), strings.Replace(ack, `"request_id":"d"`, `"request_id":"other"`, 1),
			strings.Replace(ack, `"session_id":"n"`, `"session_id":"other"`, 1), strings.Replace(ack, `"result":`, `"error":{},"result":`, 1), ack + ` {}`,
			strings.Replace(ack, `"session_id":"n"`, `"session_id":"`+string([]byte{0xff})+`"`, 1), strings.Repeat("x", 1024*1024+1),
		}
		for i, response := range responses {
			t.Run(strconv.Itoa(i), func(t *testing.T) {
				endpoint, process, requests := wakeQueueFixture(t, []byte(response), 0)
				_, _, err := OpenCodeEnqueue(ctx, endpoint, process, "n", "wake", "d", "", "")
				var uncertain *OpenCodeQueueError
				if !errors.As(err, &uncertain) || IsOpenCodeQueueUnavailable(err) {
					t.Fatalf("%T %v", err, err)
				}
				wakeQueueRequest(t, requests, 1, "session/prompt_idle")
				select {
				case raw := <-requests:
					t.Fatalf("automatically resent: %s", raw)
				default:
				}
			})
		}
	})
	t.Run("capabilities_and_endpoint", func(t *testing.T) {
		valid := `{"id":4,"result":{"session_id":"n","prompt_idle":true,"cancel_request":true,"tool_capture":true}}`
		endpoint, process, requests := wakeQueueFixture(t, []byte(valid), 0)
		client := OpenCodeQueueClient{TmpDir: filepath.Dir(endpoint)}
		selected := filepath.Join(client.TmpDir, fmt.Sprintf("cairn-opencode-%d.sock", process.PID))
		if err := os.Symlink(endpoint, selected); err != nil {
			t.Fatal(err)
		}
		if got, ok := client.SelectOpenCodeQueueEndpoint(HarnessOpenCode, process); !ok || got != selected {
			t.Fatal(got, ok)
		}
		for _, bad := range []struct {
			harness string
			process ProcessRef
		}{{HarnessCodex, process}, {HarnessOpenCode, ProcessRef{}}} {
			if _, ok := client.SelectOpenCodeQueueEndpoint(bad.harness, bad.process); ok {
				t.Fatal("invalid endpoint selected")
			}
		}
		if got, ok := client.SelectOpenCodeIdleEndpoint(ctx, HarnessOpenCode, process, "n", &process); ok || got != "" {
			t.Fatal("unsupported process retried")
		}
		select {
		case <-requests:
			t.Fatal("unsupported cache sent a probe")
		default:
		}
		stale := process
		stale.Start++
		if got, ok := client.SelectOpenCodeIdleEndpoint(ctx, HarnessOpenCode, process, "n", &stale); !ok || got != selected {
			t.Fatal("different process cache suppressed capability", got, ok)
		}
		if !client.SupportsCancel(ctx, endpoint, process, "n") || !client.SupportsToolCapture(ctx, endpoint, process, "n") {
			t.Fatal("valid capabilities refused")
		}
		for i := 0; i < 3; i++ {
			params := wakeQueueRequest(t, requests, 4, "session/capabilities")
			if params["session_id"] != "n" {
				t.Fatal(params)
			}
		}
		if err := os.Remove(selected); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(selected, []byte("regular"), 0600); err != nil {
			t.Fatal(err)
		}
		if _, ok := client.SelectOpenCodeQueueEndpoint(HarnessOpenCode, process); ok {
			t.Fatal("regular file selected")
		}
		for _, rawID := range []string{"4.0", "4e0"} {
			ep, p, _ := wakeQueueFixture(t, []byte(strings.Replace(valid, `"id":4`, `"id":`+rawID, 1)), 0)
			if !client.SupportsIdle(ctx, ep, p, "n") || !client.SupportsCancel(ctx, ep, p, "n") || !client.SupportsToolCapture(ctx, ep, p, "n") {
				t.Fatalf("equal-float capability ID %s refused", rawID)
			}
		}
		for _, raw := range []string{strings.Replace(valid, `"id":4`, `"id":4.5`, 1), strings.Replace(valid, `"id":4`, `"id":"4"`, 1), strings.Replace(valid, `"id":4`, `"id":true`, 1), strings.Replace(valid, `"session_id":"n"`, `"session_id":"other"`, 1), strings.Replace(valid, `"prompt_idle":true`, `"prompt_idle":1`, 1), `{"id":4,"result":[]}`, `not json`} {
			ep, p, _ := wakeQueueFixture(t, []byte(raw), 0)
			if client.SupportsIdle(ctx, ep, p, "n") {
				t.Fatalf("accepted %s", raw)
			}
		}
	})
	t.Run("dribbled_enqueue", func(t *testing.T) {
		checkQueueDribbledReply(t, ack, 1, "session/prompt_idle", func(ep string, p ProcessRef, timeout time.Duration) error {
			_, _, err := (OpenCodeQueueClient{ReplyTimeout: timeout}).Enqueue(ctx, ep, p, "n", "wake", "d", "", "")
			return err
		}, func(err error) bool {
			var uncertain *OpenCodeQueueError
			return errors.As(err, &uncertain) && !IsOpenCodeQueueUnavailable(err)
		})
	})
	t.Run("dribbled_cancel", func(t *testing.T) {
		checkQueueDribbledReply(t, `{"id":5,"result":{"outcome":"accepted","session_id":"n","request_id":"r","turn_id":"t"}}`, 5, "session/cancel_request", func(ep string, p ProcessRef, timeout time.Duration) error {
			_, err := (OpenCodeQueueClient{ReplyTimeout: time.Millisecond, CancelTimeout: timeout}).CancelRequest(ctx, ep, p, "n", "r", "t")
			return err
		}, IsOpenCodeCancelUncertain)
	})
	t.Run("exact_unavailable_and_EOF_messages", func(t *testing.T) {
		absent := filepath.Join(t.TempDir(), "absent.sock")
		_, _, err := OpenCodeEnqueue(ctx, absent, ProcessRef{}, "n", "wake", "d", "", "")
		if !IsOpenCodeQueueUnavailable(err) || err.Error() != "native queue endpoint is unavailable" {
			t.Fatalf("pre-send refusal: %T %v", err, err)
		}
		_, err = OpenCodeCancelRequest(ctx, absent, ProcessRef{}, "n", "r", "t")
		var refused *OpenCodeCancelRefused
		if !errors.As(err, &refused) || refused.Reason != "unavailable" || refused.Message != "native queue endpoint is unavailable" {
			t.Fatalf("cancel pre-send refusal: %T %v", err, err)
		}
		for _, tc := range []struct {
			name, want string
			call       func(string, ProcessRef) error
			uncertain  func(error) bool
		}{
			{"enqueue", "native queue outcome is uncertain; OpenCode closed connection without response; do not automatically resend", func(ep string, p ProcessRef) error {
				_, _, err := OpenCodeEnqueue(ctx, ep, p, "n", "wake", "d", "", "")
				return err
			}, func(err error) bool { var e *OpenCodeQueueError; return errors.As(err, &e) }},
			{"cancel", "native cancel outcome is uncertain; OpenCode closed connection without response", func(ep string, p ProcessRef) error {
				_, err := OpenCodeCancelRequest(ctx, ep, p, "n", "r", "t")
				return err
			}, IsOpenCodeCancelUncertain},
			{"status", "native OpenCode closed connection without response", func(ep string, p ProcessRef) error { _, err := OpenCodeStatus(ctx, ep, p, "n"); return err }, func(err error) bool { var e *OpenCodeQueueError; return errors.As(err, &e) }},
		} {
			t.Run(tc.name, func(t *testing.T) {
				ep, p, _ := wakeQueueFixture(t, nil, 0)
				err := tc.call(ep, p)
				if !tc.uncertain(err) || err.Error() != tc.want || !errors.Is(err, io.EOF) {
					t.Fatalf("%T %v", err, err)
				}
			})
		}
	})
	t.Run("exact_reply_cap_and_context_cancellation", func(t *testing.T) {
		prefix := strings.TrimSuffix(ack, "}") + `,"padding":"`
		raw := prefix + strings.Repeat("x", 1024*1024-len(prefix)-2) + `"}`
		if len(raw) != 1024*1024 {
			t.Fatal("invalid exact-cap fixture", len(raw))
		}
		ep, p, _ := wakeQueueFixture(t, []byte(raw), 0)
		if _, _, err := OpenCodeEnqueue(ctx, ep, p, "n", "wake", "d", "", ""); err != nil {
			t.Fatal("exact-cap EOF reply refused", err)
		}
		ep, p, requests := wakeQueueFixture(t, []byte(ack), 30*time.Millisecond)
		bounded, cancel := context.WithCancel(ctx)
		done := make(chan error, 1)
		go func() { _, _, err := OpenCodeEnqueue(bounded, ep, p, "n", "wake", "d", "", ""); done <- err }()
		wakeQueueRequest(t, requests, 1, "session/prompt_idle")
		cancel()
		select {
		case err := <-done:
			if err == nil || IsOpenCodeQueueUnavailable(err) {
				t.Fatalf("cancelled in-flight request: %T %v", err, err)
			}
		case <-time.After(time.Second):
			t.Fatal("context cancellation did not stop queue read")
		}
	})
	t.Run("peer", func(t *testing.T) {
		for _, change := range []func(*ProcessRef){func(p *ProcessRef) { p.PID++ }, func(p *ProcessRef) { p.Start++ }, func(p *ProcessRef) { p.Boot = "other" }} {
			ep, p, requests := wakeQueueFixture(t, []byte(ack), 0)
			change(&p)
			_, _, err := OpenCodeEnqueue(ctx, ep, p, "n", "wake", "d", "", "")
			if !IsOpenCodeQueueUnavailable(err) {
				t.Fatalf("%T %v", err, err)
			}
			select {
			case raw := <-requests:
				t.Fatalf("payload sent before peer refusal: %s", raw)
			default:
			}
		}
		ep, child := startWakeTestChildServer(t)
		self, err := procpeer.ProcessRefOf(os.Getpid())
		if err != nil {
			t.Fatal(err)
		}
		if child.PID == self.PID {
			t.Fatal("child fixture is not separate")
		}
		if _, _, err := OpenCodeEnqueue(ctx, ep, self, "n", "wake", "d", "", ""); !IsOpenCodeQueueUnavailable(err) {
			t.Fatalf("child squatter: %T %v", err, err)
		}
	})
	t.Run("cancel_and_deadlines", func(t *testing.T) {
		accepted := `{"id":5,"result":{"outcome":"accepted","session_id":"n","request_id":"r","turn_id":"t"}}`
		client := OpenCodeQueueClient{ReplyTimeout: time.Millisecond, CancelTimeout: time.Second}
		ep, p, requests := wakeQueueFixture(t, []byte(accepted), 30*time.Millisecond)
		if _, err := client.CancelRequest(ctx, ep, p, "n", "r", "t"); err != nil {
			t.Fatal("cancel used ordinary reply deadline", err)
		}
		params := wakeQueueRequest(t, requests, 5, "session/cancel_request")
		if params["session_id"] != "n" || params["request_id"] != "r" || params["expected_turn_id"] != "t" {
			t.Fatal(params)
		}
		for _, tc := range []struct {
			code            int
			message, reason string
		}{{-32601, "method", "unsupported"}, {-32004, "capability", "unsupported"}, {-32602, "invalid", "invalid"}, {-32002, "session", "session_unavailable"}, {-32006, "turn", "turn_mismatch"}, {-32007, "inactive", "not_active"}, {-32003, "refused", "native_refused"}, {-32000, "UNSUPPORTED_CONTROL: cancel", "unsupported"}, {-32000, "unknown", ""}} {
			raw := wakeJSON(t, map[string]any{"id": 5, "error": map[string]any{"code": tc.code, "message": tc.message}})
			ep, p, _ := wakeQueueFixture(t, raw, 0)
			_, err := OpenCodeCancelRequest(ctx, ep, p, "n", "r", "t")
			if tc.reason == "" {
				if !IsOpenCodeCancelUncertain(err) {
					t.Fatalf("%v", err)
				}
				continue
			}
			var refused *OpenCodeCancelRefused
			if !errors.As(err, &refused) || refused.Reason != tc.reason {
				t.Fatalf("%d: %T %v", tc.code, err, err)
			}
		}
		for _, raw := range []string{strings.Replace(accepted, `"outcome":"accepted"`, `"outcome":"refused"`, 1), strings.Replace(accepted, `"session_id":"n"`, `"session_id":"other"`, 1), strings.Replace(accepted, `"request_id":"r"`, `"request_id":"other"`, 1), strings.Replace(accepted, `"turn_id":"t"`, `"turn_id":"other"`, 1), strings.Replace(accepted, `"id":5`, `"id":5.0`, 1), `{"id":5,"error":{"code":-32006}}`} {
			ep, p, _ := wakeQueueFixture(t, []byte(raw), 0)
			if _, err := OpenCodeCancelRequest(ctx, ep, p, "n", "r", "t"); !IsOpenCodeCancelUncertain(err) {
				t.Fatalf("%s: %v", raw, err)
			}
		}
		for i, field := range []string{"native_id", "request_id", "expected_turn_id"} {
			args := []string{"n", "r", "t"}
			args[i] = "\x1c "
			_, err := OpenCodeCancelRequest(ctx, "/absent", ProcessRef{}, args[0], args[1], args[2])
			var refused *OpenCodeCancelRefused
			if !errors.As(err, &refused) || refused.Reason != "invalid" || refused.Message != field+" must be a nonempty string" {
				t.Fatal(err)
			}
		}
		_, err := OpenCodeCancelRequest(ctx, "/absent", ProcessRef{}, "n", "r", "t")
		var refused *OpenCodeCancelRefused
		if !errors.As(err, &refused) || refused.Reason != "unavailable" {
			t.Fatal(err)
		}
		ep, p, _ = wakeQueueFixture(t, []byte(accepted), 30*time.Millisecond)
		if _, err := (OpenCodeQueueClient{CancelTimeout: time.Millisecond}).CancelRequest(ctx, ep, p, "n", "r", "t"); !IsOpenCodeCancelUncertain(err) {
			t.Fatalf("cancel timeout: %T %v", err, err)
		}
		ep, p, requests = wakeQueueFixture(t, []byte(ack), 30*time.Millisecond)
		if _, _, err := (OpenCodeQueueClient{ReplyTimeout: time.Millisecond}).Enqueue(ctx, ep, p, "n", "wake", "d", "", ""); IsOpenCodeQueueUnavailable(err) || err == nil {
			t.Fatalf("enqueue timeout: %T %v", err, err)
		}
		wakeQueueRequest(t, requests, 1, "session/prompt_idle")
	})
	t.Run("status", func(t *testing.T) {
		valid := `{"id":3,"result":{"session_id":"n","status":"idle"}}`
		for i, raw := range []string{valid, strings.Replace(valid, `"status":"idle"`, `"status":""`, 1), strings.Replace(valid, `"session_id":"n"`, `"session_id":false`, 1), strings.Replace(valid, `"id":3`, `"id":3.0`, 1), `{"id":3,"error":{"message":"no"}}`} {
			ep, p, requests := wakeQueueFixture(t, []byte(raw), 0)
			result, err := OpenCodeStatus(ctx, ep, p, "n")
			if (err == nil) != (i == 0) {
				t.Fatalf("status %d: %v %v", i, result, err)
			}
			wakeQueueRequest(t, requests, 3, "session/status")
		}
	})
}

func TestHermesQueue(t *testing.T) {
	ctx := context.Background()
	ack := `{"id":1,"result":{"queued":true,"queued_id":"c","session_id":"n","started":false,"request_id":"r","delivery_id":"d"}}`
	t.Run("dribbled_enqueue", func(t *testing.T) {
		checkQueueDribbledReply(t, ack, 1, "session/queue_message", func(ep string, p ProcessRef, timeout time.Duration) error {
			_, _, err := (HermesQueueClient{ReplyTimeout: timeout}).Enqueue(ctx, ep, p, "n", "wake", "c", "", "r", "d")
			return err
		}, func(err error) bool {
			var uncertain *HermesQueueError
			return errors.As(err, &uncertain) && !IsHermesQueueUnavailable(err)
		})
	})
	t.Run("dribbled_abort", func(t *testing.T) {
		checkQueueDribbledReply(t, `{"id":2,"result":{"session_id":"n","request_id":"r","turn_id":"t","aborted":true,"turn_stop":""}}`, 2, "session/abort", func(ep string, p ProcessRef, timeout time.Duration) error {
			_, err := (HermesQueueClient{ReplyTimeout: time.Millisecond, AbortTimeout: timeout}).Abort(ctx, ep, p, "n", "r", "t")
			return err
		}, func(err error) bool {
			var uncertain *HermesQueueError
			return errors.As(err, &uncertain) && !IsHermesRequestMismatchError(err) && !IsHermesQueueUnavailable(err)
		})
	})
	t.Run("exact_operation_EOF_and_identity", func(t *testing.T) {
		for _, tc := range []struct {
			name, eof, identity string
			id                  int
			call                func(string, ProcessRef) error
		}{
			{"enqueue", "native queue outcome is uncertain; Hermes closed connection without response; do not automatically resend", "native queue outcome is uncertain; invalid bridge response identity", 1, func(ep string, p ProcessRef) error {
				_, _, err := HermesEnqueue(ctx, ep, p, "n", "wake", "c", "", "r", "d")
				return err
			}},
			{"abort", "native Hermes closed connection during abort; outcome uncertain; do not resend", "native Hermes abort outcome is uncertain; invalid bridge response identity", 2, func(ep string, p ProcessRef) error { _, err := HermesAbort(ctx, ep, p, "n", "r", "t"); return err }},
			{"status", "native Hermes closed connection without response", "invalid bridge status response identity", 3, func(ep string, p ProcessRef) error { _, err := HermesStatus(ctx, ep, p, "n"); return err }},
			{"tools_status", "native Hermes closed connection without response", "invalid bridge tools_status response identity", 4, func(ep string, p ProcessRef) error { _, err := HermesToolsStatus(ctx, ep, p, "n", nil); return err }},
		} {
			t.Run(tc.name, func(t *testing.T) {
				ep, p, _ := wakeQueueFixture(t, nil, 0)
				err := tc.call(ep, p)
				var uncertain *HermesQueueError
				if !errors.As(err, &uncertain) || err.Error() != tc.eof || !errors.Is(err, io.EOF) {
					t.Fatalf("EOF: %T %v", err, err)
				}
				for _, raw := range []string{`null`, `{}`, fmt.Sprintf(`{"id":%d.0}`, tc.id), `{"id":true}`, `{"id":99}`} {
					ep, p, _ := wakeQueueFixture(t, []byte(raw), 0)
					err := tc.call(ep, p)
					if !errors.As(err, &uncertain) || err.Error() != tc.identity {
						t.Fatalf("identity %s: %T %v", raw, err, err)
					}
				}
			})
		}
	})
	t.Run("exact_result_types_and_mismatch_values", func(t *testing.T) {
		for _, tc := range []struct{ raw, name string }{{"null", "NoneType"}, {"true", "bool"}, {"1", "int"}, {"1.0", "float"}, {`"value"`, "str"}, {"[]", "list"}} {
			for _, abort := range []bool{false, true} {
				id, prefix := 1, "invalid bridge response schema: expected dict result, got "
				if abort {
					id, prefix = 2, "invalid bridge abort response schema: expected dict result, got "
				}
				ep, p, _ := wakeQueueFixture(t, []byte(fmt.Sprintf(`{"id":%d,"result":%s}`, id, tc.raw)), 0)
				var err error
				if abort {
					_, err = HermesAbort(ctx, ep, p, "n", "r", "t")
				} else {
					_, _, err = HermesEnqueue(ctx, ep, p, "n", "wake", "c", "", "r", "d")
				}
				var uncertain *HermesQueueError
				if !errors.As(err, &uncertain) || err.Error() != prefix+tc.name {
					t.Fatalf("%s: %T %v", tc.raw, err, err)
				}
			}
		}
		for _, abort := range []bool{false, true} {
			id, prefix := 1, "invalid bridge response schema: expected dict result, got NoneType"
			if abort {
				id, prefix = 2, "invalid bridge abort response schema: expected dict result, got NoneType"
			}
			ep, p, _ := wakeQueueFixture(t, []byte(fmt.Sprintf(`{"id":%d}`, id)), 0)
			var err error
			if abort {
				_, err = HermesAbort(ctx, ep, p, "n", "r", "t")
			} else {
				_, _, err = HermesEnqueue(ctx, ep, p, "n", "wake", "c", "", "r", "d")
			}
			if err == nil || err.Error() != prefix {
				t.Fatal("missing result", err)
			}
		}
		for _, field := range []struct{ key, label, expected string }{{"session_id", "session", "n"}, {"request_id", "request", "r"}, {"delivery_id", "delivery", "d"}} {
			for _, value := range []struct {
				value any
				text  string
			}{{nil, "None"}, {true, "True"}, {false, "False"}} {
				var response map[string]any
				if err := json.Unmarshal([]byte(ack), &response); err != nil {
					t.Fatal(err)
				}
				result := response["result"].(map[string]any)
				result[field.key] = value.value
				if value.value == nil {
					delete(result, field.key)
				}
				ep, p, _ := wakeQueueFixture(t, wakeJSON(t, response), 0)
				_, _, err := HermesEnqueue(ctx, ep, p, "n", "wake", "c", "", "r", "d")
				want := field.label + " ID mismatch in response: expected " + field.expected + ", got " + value.text
				if err == nil || err.Error() != want {
					t.Fatalf("%s: %T %v", field.key, err, err)
				}
			}
		}
	})
	t.Run("enqueue_optional_fields_and_busy_fallback", func(t *testing.T) {
		for _, tc := range []struct {
			request, delivery string
			fallback          bool
		}{{"r", "d", false}, {"", "", true}} {
			raw := ack
			if tc.fallback {
				raw = strings.Replace(raw, `"started":false`, `"busy":false`, 1)
			}
			ep, p, requests := wakeQueueFixture(t, []byte(raw), 0)
			id, started, err := HermesEnqueue(ctx, ep, p, "n", "雪😀\n", "c", "", tc.request, tc.delivery)
			if err != nil || id != "c" || started != tc.fallback {
				t.Fatalf("%q %v %v", id, started, err)
			}
			params := wakeQueueRequest(t, requests, 1, "session/queue_message")
			if params["session_id"] != "n" || params["expected_session_id"] != "n" || params["text"] != "雪😀\n" || params["client_id"] != "c" {
				t.Fatal(params)
			}
			for key, want := range map[string]string{"request_id": tc.request, "delivery_id": tc.delivery} {
				value, present := params[key]
				if (want != "") != present || present && value != want {
					t.Fatalf("%s: %v", key, params)
				}
			}
		}
		ep, p, requests := wakeQueueFixture(t, []byte(ack), 0)
		if _, _, err := HermesEnqueue(ctx, ep, p, "n", "wake", "c", "expected", "r", "d"); err != nil {
			t.Fatal(err)
		}
		if params := wakeQueueRequest(t, requests, 1, "session/queue_message"); params["expected_session_id"] != "expected" {
			t.Fatal(params)
		}
	})
	t.Run("queue_rejection_and_schema", func(t *testing.T) {
		for _, tc := range []struct {
			code        any
			message     string
			unavailable bool
		}{{-32001, "missing", true}, {-32002, "mismatch", true}, {-32001.0, "missing", true}, {-32000, "SESSION_NOT_FOUND native", true}, {-32000, "SESSION_MISMATCH native", true}, {-32000, "refused", false}} {
			raw := wakeJSON(t, map[string]any{"id": 1, "error": map[string]any{"code": tc.code, "message": tc.message}})
			ep, p, _ := wakeQueueFixture(t, raw, 0)
			_, _, err := HermesEnqueue(ctx, ep, p, "n", "wake", "c", "", "r", "d")
			if err == nil || IsHermesQueueUnavailable(err) != tc.unavailable {
				t.Fatalf("%v: %T %v", tc, err, err)
			}
		}
		for _, raw := range []string{``, `null`, `{"id":1,"result":null}`, `{"id":1,"error":{"message":12}}`,
			strings.Replace(ack, `"id":1`, `"id":1.0`, 1), strings.Replace(ack, `"queued":true`, `"queued":1`, 1),
			strings.Replace(ack, `"started":false`, `"started":null`, 1), strings.Replace(ack, `"queued_id":"c"`, `"queued_id":"other"`, 1),
			strings.Replace(ack, `"session_id":"n"`, `"session_id":"other"`, 1), strings.Replace(ack, `"request_id":"r"`, `"request_id":"other"`, 1),
			strings.Replace(ack, `"delivery_id":"d"`, `"delivery_id":"other"`, 1), ack + ` false`, strings.Repeat("x", 1024*1024+1),
		} {
			ep, p, requests := wakeQueueFixture(t, []byte(raw), 0)
			_, _, err := HermesEnqueue(ctx, ep, p, "n", "wake", "c", "", "r", "d")
			var uncertain *HermesQueueError
			if !errors.As(err, &uncertain) || IsHermesQueueUnavailable(err) {
				t.Fatalf("%T %v", err, err)
			}
			wakeQueueRequest(t, requests, 1, "session/queue_message")
			select {
			case raw := <-requests:
				t.Fatalf("automatic resend: %s", raw)
			default:
			}
		}
	})
	t.Run("abort_scope_and_refusals", func(t *testing.T) {
		accepted := `{"id":2,"result":{"session_id":"n","request_id":"r","turn_id":"t","aborted":false,"turn_stop":""}}`
		for _, withScope := range []bool{true, false} {
			request, turn := "", ""
			if withScope {
				request, turn = "r", "t"
			}
			ep, p, requests := wakeQueueFixture(t, []byte(accepted), 0)
			result, err := HermesAbort(ctx, ep, p, "n", request, turn)
			if err != nil || result["turn_stop"] != "" || result["aborted"] != false {
				t.Fatal(result, err)
			}
			params := wakeQueueRequest(t, requests, 2, "session/abort")
			if params["session_id"] != "n" {
				t.Fatal(params)
			}
			for key, want := range map[string]string{"expected_request_id": request, "expected_turn_id": turn} {
				value, present := params[key]
				if present != withScope || present && value != want {
					t.Fatal(params)
				}
			}
		}
		for _, tc := range []struct {
			code          int
			message, kind string
		}{
			{-32001, "request", "mismatch"}, {-32003, "turn", "mismatch"}, {-32004, "unspecified", "mismatch"},
			{-32000, "REQUEST_MISMATCH", "mismatch"}, {-32000, "TURN_MISMATCH", "mismatch"}, {-32000, "UNSPECIFIED_ABORT", "mismatch"},
			{-32002, "session", "unavailable"}, {-32000, "SESSION_NOT_FOUND", "unavailable"}, {-32000, "SESSION_MISMATCH", "unavailable"}, {-32000, "other", "uncertain"},
		} {
			raw := wakeJSON(t, map[string]any{"id": 2, "error": map[string]any{"code": tc.code, "message": tc.message}})
			ep, p, _ := wakeQueueFixture(t, raw, 0)
			_, err := HermesAbort(ctx, ep, p, "n", "r", "t")
			if err == nil || IsHermesRequestMismatchError(err) != (tc.kind == "mismatch") || IsHermesQueueUnavailable(err) != (tc.kind == "unavailable") {
				t.Fatalf("%v: %T %v", tc, err, err)
			}
		}
		for _, raw := range []string{
			strings.Replace(accepted, `"session_id":"n"`, `"session_id":"other"`, 1), strings.Replace(accepted, `"request_id":"r"`, `"request_id":"other"`, 1),
			strings.Replace(accepted, `"turn_id":"t"`, `"turn_id":"other"`, 1), strings.Replace(accepted, `"aborted":false`, `"aborted":0`, 1),
			strings.Replace(accepted, `"turn_stop":""`, `"turn_stop":null`, 1), strings.Replace(accepted, `"id":2`, `"id":true`, 1),
		} {
			ep, p, _ := wakeQueueFixture(t, []byte(raw), 0)
			if _, err := HermesAbort(ctx, ep, p, "n", "r", "t"); err == nil || IsHermesRequestMismatchError(err) || IsHermesQueueUnavailable(err) {
				t.Fatalf("%s: %T %v", raw, err, err)
			}
		}
		client := HermesQueueClient{ReplyTimeout: time.Millisecond, AbortTimeout: time.Second}
		ep, p, _ := wakeQueueFixture(t, []byte(accepted), 30*time.Millisecond)
		if _, err := client.Abort(ctx, ep, p, "n", "r", "t"); err != nil {
			t.Fatal("abort used ordinary reply deadline", err)
		}
		ep, p, _ = wakeQueueFixture(t, []byte(accepted), 30*time.Millisecond)
		if _, err := (HermesQueueClient{AbortTimeout: time.Millisecond}).Abort(ctx, ep, p, "n", "r", "t"); err == nil || IsHermesQueueUnavailable(err) {
			t.Fatalf("abort timeout: %T %v", err, err)
		}
	})
	t.Run("status_and_tools", func(t *testing.T) {
		for _, tc := range []struct {
			raw          string
			tools, valid bool
		}{
			{`{"id":3,"result":{"session_id":"n","status":"busy"}}`, false, true},
			{`{"id":3,"result":{"session_id":"other","status":"busy"}}`, false, false},
			{`{"id":3,"result":{"session_id":"n","status":""}}`, false, false},
			{`{"id":3.0,"result":{"session_id":"n","status":"busy"}}`, false, false},
			{`{"id":4,"result":{"session_id":"n","tools":[]}}`, true, true},
			{`{"id":4,"result":{"session_id":"n","tools":{}}}`, true, false},
			{`{"id":4,"result":{"session_id":"other","tools":[]}}`, true, false},
			{`{"id":4,"error":{"message":"no"}}`, true, false},
		} {
			ep, p, requests := wakeQueueFixture(t, []byte(tc.raw), 0)
			var err error
			if tc.tools {
				_, err = HermesToolsStatus(ctx, ep, p, "n", nil)
			} else {
				_, err = HermesStatus(ctx, ep, p, "n")
			}
			if (err == nil) != tc.valid {
				t.Fatalf("%s: %v", tc.raw, err)
			}
			id, method := 3, "session/status"
			if tc.tools {
				id, method = 4, "session/tools_status"
			}
			params := wakeQueueRequest(t, requests, id, method)
			if tc.tools {
				if ids, ok := params["tool_ids"].([]any); !ok || len(ids) != 0 {
					t.Fatalf("nil tools are not []: %v", params)
				}
			}
		}
		ep, p, requests := wakeQueueFixture(t, []byte(`{"id":4,"result":{"session_id":"n","tools":[]}}`), 0)
		if _, err := HermesToolsStatus(ctx, ep, p, "n", []string{"one", "two"}); err != nil {
			t.Fatal(err)
		}
		params := wakeQueueRequest(t, requests, 4, "session/tools_status")
		ids := params["tool_ids"].([]any)
		if len(ids) != 2 || ids[0] != "one" || ids[1] != "two" {
			t.Fatal(params)
		}
	})
	t.Run("endpoint_and_peer", func(t *testing.T) {
		ep, p, requests := wakeQueueFixture(t, []byte(ack), 0)
		client := HermesQueueClient{TmpDir: filepath.Dir(ep)}
		selected := filepath.Join(client.TmpDir, fmt.Sprintf("cairn-hermes-%d.sock", p.PID))
		if err := os.Symlink(ep, selected); err != nil {
			t.Fatal(err)
		}
		if got, ok := client.SelectHermesQueueEndpoint(HarnessHermes, p); !ok || got != selected {
			t.Fatal(got, ok)
		}
		if _, ok := client.SelectHermesQueueEndpoint(HarnessCodex, p); ok {
			t.Fatal("wrong harness selected")
		}
		if _, ok := client.SelectHermesQueueEndpoint(HarnessHermes, ProcessRef{}); ok {
			t.Fatal("invalid process selected")
		}
		for _, change := range []func(*ProcessRef){func(p *ProcessRef) { p.PID += 100000 }, func(p *ProcessRef) { p.Start++ }, func(p *ProcessRef) { p.Boot = "another-boot" }} {
			target := p
			change(&target)
			if _, _, err := client.Enqueue(ctx, ep, target, "n", "wake", "c", "", "r", "d"); !IsHermesQueueUnavailable(err) {
				t.Fatalf("wrong peer: %T %v", err, err)
			}
		}
		select {
		case raw := <-requests:
			t.Fatalf("payload sent before identity refusal: %s", raw)
		default:
		}
		if _, _, err := HermesEnqueue(ctx, filepath.Join(t.TempDir(), "absent.sock"), ProcessRef{}, "n", "wake", "c", "", "", ""); !IsHermesQueueUnavailable(err) || err.Error() != "native queue endpoint is unavailable" {
			t.Fatal(err)
		}
		// A different live child cannot authorize the fixture's socket. Only this
		// owned child is terminated; never signal a PID or process group <= 1.
		child := exec.Command("sleep", "5")
		if err := child.Start(); err != nil {
			t.Fatal(err)
		}
		if child.Process.Pid <= 1 {
			t.Fatal("invalid owned child PID")
		}
		t.Cleanup(func() {
			if child.Process.Pid > 1 {
				_ = child.Process.Kill()
				_ = child.Wait()
			}
		})
		childRef, err := procpeer.ProcessRefOf(child.Process.Pid)
		if err != nil {
			t.Fatal(err)
		}
		if _, _, err := client.Enqueue(ctx, ep, childRef, "n", "wake", "c", "", "r", "d"); !IsHermesQueueUnavailable(err) || err.Error() != "native socket belongs to a different process" {
			t.Fatalf("live child mismatch: %T %v", err, err)
		}
		t.Run("child_socket_squatter", func(t *testing.T) {
			endpoint, child := startWakeTestChildServer(t)
			if child.PID == p.PID {
				t.Fatal("socket fixture has no separate child")
			}
			if _, _, err := client.Enqueue(ctx, endpoint, p, "n", "wake", "c", "", "r", "d"); !IsHermesQueueUnavailable(err) || err.Error() != "native socket belongs to a different process" {
				t.Fatalf("child socket owner: %T %v", err, err)
			}
		})
		ep, p, requests = wakeQueueFixture(t, []byte(ack), 30*time.Millisecond)
		if _, _, err := (HermesQueueClient{ReplyTimeout: time.Millisecond}).Enqueue(ctx, ep, p, "n", "wake", "c", "", "r", "d"); err == nil || IsHermesQueueUnavailable(err) {
			t.Fatalf("post-send timeout: %T %v", err, err)
		}
		wakeQueueRequest(t, requests, 1, "session/queue_message")
	})
}

type herdrFixture struct {
	t             *testing.T
	host, current map[string]any
	candidates    []any
	pgid          any
	foreground    []any
	processExit   int
	processStderr []byte
	prompt        map[string]any
	readyDelivery string
	readyErr      error
	calls         [][]string
	readyCalls    int
}

func newHerdrFixture(t *testing.T, harness string) (*herdrFixture, HerdrClient, TerminalWakeRequest) {
	t.Helper()
	root := t.TempDir()
	process := ProcessRef{PID: 4321, Start: 98765, Boot: "fixture-boot"}
	writeFakeProcEntry(t, root, process.PID, "S", process.Start, process.Boot, "")
	statPath := filepath.Join(root, strconv.Itoa(process.PID), "stat")
	raw, err := os.ReadFile(statPath)
	if err != nil {
		t.Fatal(err)
	}
	fields := strings.Fields(string(raw[strings.LastIndexByte(string(raw), ')')+1:]))
	fields[2] = "742"
	if err := os.WriteFile(statPath, []byte(fmt.Sprintf("%d (fixture (nested) name) %s\n", process.PID, strings.Join(fields, " "))), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, strconv.Itoa(process.PID), "environ"), []byte("HERDR_ENV=1\x00HERDR_SOCKET_PATH=/fixture/own.sock\x00"), 0600); err != nil {
		t.Fatal(err)
	}
	host := map[string]any{"pane_id": "own-pane", "terminal_id": "own-terminal", "revision": int64(17), "state_change_seq": int64(29), "agent": harness, "agent_status": "idle", "focused": false,
		"agent_session": map[string]any{"kind": "id", "value": testNativeID, "agent": harness}}
	f := &herdrFixture{t: t, host: host, current: host, candidates: []any{host}, pgid: 742, foreground: []any{map[string]any{"pid": process.PID}}, readyDelivery: testDeliveryID,
		prompt: map[string]any{"type": "agent_prompted", "agent": map[string]any{"pane_id": "own-pane", "terminal_id": "own-terminal"}}}
	request := TerminalWakeRequest{Harness: harness, NativeSessionID: testNativeID, Process: process, Session: SessionRef{AgentID: testAgentID, ExecutionID: testExecID}, DeliveryID: testDeliveryID, RequestID: "terminal-request", StateDir: t.TempDir()}
	request.StatePath = filepath.Join(request.StateDir, "own.json")
	client := HerdrClient{Executable: "/usr/bin/herdr", ProcRoot: root, Runner: f.run,
		InboxReady: func(_ context.Context, session SessionRef) (string, error) {
			f.readyCalls++
			if session != request.Session {
				t.Fatalf("read another inbox: %#v", session)
			}
			return f.readyDelivery, f.readyErr
		}}
	return f, client, request
}

func (f *herdrFixture) run(ctx context.Context, argv, env []string) ([]byte, []byte, int, error) {
	f.t.Helper()
	if _, ok := ctx.Deadline(); !ok {
		f.t.Fatal("Herdr command has no deadline")
	}
	if !slices.Contains(env, "HERDR_SOCKET_PATH=/fixture/own.sock") || slices.Contains(env, "HERDR_PANE_ID=watcher") {
		f.t.Fatal("host context leaked", env)
	}
	args := argv[1:]
	f.calls = append(f.calls, slices.Clone(args))
	var result map[string]any
	switch {
	case slices.Equal(args, []string{"agent", "list"}):
		result = map[string]any{"agents": f.candidates}
	case slices.Equal(args, []string{"agent", "get", "own-pane"}):
		result = map[string]any{"agent": f.current}
	case len(args) == 4 && args[0] == "pane" && args[1] == "process-info" && args[2] == "--pane":
		if f.processExit != 0 {
			return nil, f.processStderr, f.processExit, nil
		}
		result = map[string]any{"process_info": map[string]any{"foreground_process_group_id": f.pgid, "foreground_processes": f.foreground}}
	case len(args) == 4 && args[0] == "agent" && args[1] == "prompt":
		result = f.prompt
	default:
		f.t.Fatalf("unexpected fixture command: %q", argv)
	}
	return wakeJSON(f.t, map[string]any{"result": result}), nil, 0, nil
}

func TestHerdrTerminalWake(t *testing.T) {
	// A subprocess fixture for the default runner. It never runs a real Herdr
	// executable or harness. All process termination remains owned by the runner.
	if mode := os.Getenv("CAIRN_HERDR_TEST_MODE"); mode != "" {
		switch mode {
		case "valid":
			fmt.Fprint(os.Stdout, `{"result":{"agents":[]}}`)
			_, _ = os.Stderr.Write([]byte{0xff})
		case "exit":
			fmt.Fprint(os.Stderr, `{"error":{"code":"pane_not_found"}}`)
			os.Exit(7)
		case "invalid_utf8":
			_, _ = os.Stdout.Write([]byte{0xff})
		case "stdout_overflow":
			_, _ = os.Stdout.Write(bytes.Repeat([]byte{'x'}, HerdrOutputBytes+1))
		case "stderr_overflow":
			_, _ = os.Stderr.Write(bytes.Repeat([]byte{'x'}, HerdrOutputBytes+1))
		case "both_at_limit":
			_, _ = os.Stdout.Write(bytes.Repeat([]byte{'x'}, HerdrOutputBytes))
			_, _ = os.Stderr.Write(bytes.Repeat([]byte{'y'}, HerdrOutputBytes))
		case "delay":
			if err := os.WriteFile(os.Getenv("CAIRN_HERDR_TEST_PID"), []byte(strconv.Itoa(os.Getpid())), 0600); err != nil {
				os.Exit(3)
			}
			time.Sleep(time.Minute)
		default:
			os.Exit(4)
		}
		os.Exit(0)
	}
	ctx := context.Background()
	t.Run("explicit_executable_and_delivery_guards", func(t *testing.T) {
		for _, prompt := range []bool{false, true} {
			args, code := []string{"agent", "list"}, "HOST_UNAVAILABLE"
			if prompt {
				args, code = []string{"agent", "prompt", "p", "wake"}, "WAKE_UNCERTAIN"
			}
			if _, err := (HerdrClient{}).Call(ctx, nil, args...); err == nil || err.Error() != code+": this binding has no Herdr executable" {
				t.Fatalf("zero client: %v", err)
			}
			for _, executable := range []string{"", " \t", "herdr", "./herdr"} {
				calls := 0
				client := HerdrClient{Executable: executable, Runner: func(context.Context, []string, []string) ([]byte, []byte, int, error) {
					calls++
					return nil, nil, 0, nil
				}}
				_, err := client.Call(ctx, nil, args...)
				var hostErr *HerdrError
				if calls != 0 || !errors.As(err, &hostErr) || hostErr.Code != code || hostErr.Message != "this binding has no Herdr executable" {
					t.Fatalf("%q: calls=%d %v", executable, calls, err)
				}
			}
		}
		for _, delivery := range []string{"", " \t", "\x1c\u0085"} {
			f, client, request := newHerdrFixture(t, HarnessHermes)
			request.DeliveryID = delivery
			prepared, refusal, err := client.PrepareTerminalWake(ctx, request, nil)
			if prepared != nil || refusal != nil || err == nil || err.Error() != "delivery_id must be a nonempty string" || len(f.calls) != 0 || f.readyCalls != 0 {
				t.Fatal("blank delivery reached host", prepared, refusal, err, f.calls)
			}
		}
		for _, prompt := range []bool{false, true} {
			args, code := []string{"/missing/private-herdr", "agent", "list"}, "HOST_UNAVAILABLE"
			if prompt {
				args, code = []string{"/missing/private-herdr", "agent", "prompt", "p", "wake"}, "WAKE_UNCERTAIN"
			}
			_, _, _, err := DefaultHerdrCommandRunner(ctx, args, nil)
			var hostErr *HerdrError
			if !errors.As(err, &hostErr) || hostErr.Code != code || hostErr.Message != "native process has no Herdr host environment" {
				t.Fatal("nil environment inherited watcher context", err)
			}
		}
	})
	t.Run("environment_custody_and_limit", func(t *testing.T) {
		_, client, request := newHerdrFixture(t, HarnessCodex)
		path := filepath.Join(client.ProcRoot, strconv.Itoa(request.Process.PID), "environ")
		base := []string{"PATH=/fixture/bin", "KEEP=yes", "HERDR_ENV=1", "HERDR_SOCKET_PATH=/watcher.sock", "HERDR_PANE_ID=watcher", "HERDR_REMOTE=yes"}
		env, ok, err := client.HerdrEnvironment(request.Process, base)
		want := []string{"PATH=/fixture/bin", "KEEP=yes", "HERDR_ENV=1", "HERDR_SOCKET_PATH=/fixture/own.sock"}
		if err != nil || !ok || !slices.Equal(env, want) {
			t.Fatal(env, ok, err)
		}
		for _, raw := range [][]byte{[]byte("HERDR_ENV=0\x00HERDR_SOCKET_PATH=/own\x00"), []byte("HERDR_ENV=1\x00HERDR_SOCKET_PATH=relative\x00"), []byte("HERDR_SOCKET_PATH=/own\x00"), bytes.Repeat([]byte{'x'}, HerdrMaxEnvironBytes)} {
			if err := os.WriteFile(path, raw, 0600); err != nil {
				t.Fatal(err)
			}
			if env, ok, err := client.HerdrEnvironment(request.Process, base); err != nil || ok || env != nil {
				t.Fatal(env, ok, err)
			}
		}
		for _, raw := range [][]byte{bytes.Repeat([]byte{'x'}, HerdrMaxEnvironBytes+1), []byte("HERDR_ENV=1\x00HERDR_SOCKET_PATH=/bad\xff\x00")} {
			if err := os.WriteFile(path, raw, 0600); err != nil {
				t.Fatal(err)
			}
			_, _, err := client.HerdrEnvironment(request.Process, base)
			var hostErr *HerdrError
			if !errors.As(err, &hostErr) || hostErr.Code != "INVALID_HOST" {
				t.Fatalf("%T %v", err, err)
			}
		}
		if err := os.WriteFile(path, []byte("OTHER=\xff\x00HERDR_ENV=1\x00HERDR_SOCKET_PATH=/own\x00"), 0600); err != nil {
			t.Fatal(err)
		}
		if _, ok, err := client.HerdrEnvironment(request.Process, base); !ok || err != nil {
			t.Fatal("unselected environment bytes affected host", ok, err)
		}
		if err := os.Remove(path); err != nil {
			t.Fatal(err)
		}
		if _, _, err := client.HerdrEnvironment(request.Process, base); err == nil {
			t.Fatal("missing native environment accepted")
		}
	})
	t.Run("strict_call_schema_and_error_classes", func(t *testing.T) {
		for _, tc := range []struct {
			args           []string
			stdout, stderr string
			exit           int
			code           string
		}{
			{[]string{"agent", "list"}, `{"result":{"agents":[]}}`, "", 0, ""},
			{[]string{"agent", "list"}, `{"result":{"agents":[{"pane_id":"p","agent_session":null}]}}`, "", 0, ""},
			{[]string{"agent", "list"}, `{"result":{"agents":[{"pane_id":"","agent_session":null}]}}`, "", 0, "HOST_UNAVAILABLE"},
			{[]string{"agent", "list"}, `{"result":{"agents":[{"pane_id":"p","agent_session":false}]}}`, "", 0, "HOST_UNAVAILABLE"},
			{[]string{"agent", "get", "p"}, `{"result":{"agent":[]}}`, "", 0, "HOST_UNAVAILABLE"},
			{[]string{"pane", "process-info"}, `{"result":{"process_info":{"foreground_process_group_id":1.0,"foreground_processes":[]}}}`, "", 0, "HOST_UNAVAILABLE"},
			{[]string{"pane", "process-info"}, `{"result":{"process_info":{"foreground_process_group_id":2,"foreground_processes":[{"pid":true}]}}}`, "", 0, "HOST_UNAVAILABLE"},
			{[]string{"agent", "prompt", "p", "text"}, `{"result":{"type":"agent_prompted","agent":{}}}`, "", 0, ""},
			{[]string{"agent", "prompt", "p", "text"}, `{"result":{"type":"unknown","agent":{}}}`, "", 0, "WAKE_UNCERTAIN"},
			{[]string{"agent", "prompt", "p", "text"}, `null`, "", 0, "WAKE_UNCERTAIN"},
			{[]string{"agent", "list"}, `{"result":{}} trailing`, "", 0, "HOST_UNAVAILABLE"},
			{[]string{"agent", "list"}, string([]byte{0xff}), "", 0, "HOST_UNAVAILABLE"},
			{[]string{"agent", "prompt", "p", "text"}, strings.Repeat("x", HerdrOutputBytes+1), "", 0, "WAKE_UNCERTAIN"},
			{[]string{"agent", "list"}, "", strings.Repeat("x", HerdrOutputBytes+1), 1, "HOST_UNAVAILABLE"},
			{[]string{"agent", "list"}, "", `{"error":{"code":"pane_not_found"}}`, 1, "HOST_TARGET_GONE"},
			{[]string{"agent", "prompt", "p", "text"}, "", `{"error":{"code":"agent_not_found"}}`, 1, "HOST_TARGET_GONE"},
			{[]string{"agent", "list"}, "", `{"error":{"code":"agent_not_running"}}`, 1, "HOST_TARGET_GONE"},
			{[]string{"agent", "prompt", "p", "text"}, "", "garbled", 1, "WAKE_UNCERTAIN"},
		} {
			client := HerdrClient{Executable: "/usr/bin/herdr", Runner: func(context.Context, []string, []string) ([]byte, []byte, int, error) {
				return []byte(tc.stdout), []byte(tc.stderr), tc.exit, nil
			}}
			_, err := client.Call(ctx, nil, tc.args...)
			if tc.code == "" {
				if err != nil {
					t.Fatal(tc.args, err)
				}
				continue
			}
			var hostErr *HerdrError
			if !errors.As(err, &hostErr) || hostErr.Code != tc.code {
				t.Fatalf("%v: %T %v", tc.args, err, err)
			}
		}
		sentinel := errors.New("fixture unavailable")
		client := HerdrClient{Executable: "/usr/bin/herdr", Runner: func(context.Context, []string, []string) ([]byte, []byte, int, error) { return nil, nil, 0, sentinel }}
		if _, err := client.Call(ctx, nil, "agent", "list"); !errors.Is(err, sentinel) {
			t.Fatal("cause lost", err)
		}
		cancelled, cancel := context.WithCancel(ctx)
		cancel()
		client.Runner = func(context.Context, []string, []string) ([]byte, []byte, int, error) {
			return []byte(`{"result":{"agent":{},"type":"agent_prompted"}}`), nil, 0, nil
		}
		if _, err := client.Call(cancelled, nil, "agent", "prompt"); err == nil {
			t.Fatal("cancelled prompt accepted")
		}
	})
	t.Run("identity_and_foreground_ownership", func(t *testing.T) {
		for _, change := range []func(*herdrFixture, *HerdrClient, *TerminalWakeRequest){
			func(f *herdrFixture, _ *HerdrClient, _ *TerminalWakeRequest) { f.host["agent"] = "other" },
			func(f *herdrFixture, _ *HerdrClient, _ *TerminalWakeRequest) {
				f.host["agent_session"] = map[string]any{"kind": "id", "value": "other", "agent": HarnessHermes}
			},
			func(f *herdrFixture, _ *HerdrClient, _ *TerminalWakeRequest) { f.host["agent_session"] = nil },
			func(f *herdrFixture, _ *HerdrClient, _ *TerminalWakeRequest) { f.pgid = 743 },
			func(f *herdrFixture, _ *HerdrClient, _ *TerminalWakeRequest) {
				f.foreground = []any{map[string]any{"pid": 4322}}
			},
			func(_ *herdrFixture, _ *HerdrClient, r *TerminalWakeRequest) { r.Process.Start++ },
			func(_ *herdrFixture, _ *HerdrClient, r *TerminalWakeRequest) { r.Process.Boot = "other" },
			func(f *herdrFixture, _ *HerdrClient, _ *TerminalWakeRequest) {
				f.processExit = 1
				f.processStderr = []byte(`{"error":{"code":"pane_not_found"}}`)
			},
		} {
			f, client, request := newHerdrFixture(t, HarnessHermes)
			env, _, _ := client.HerdrEnvironment(request.Process, nil)
			if match, err := client.HostIdentifiesSession(ctx, request.Harness, request.NativeSessionID, request.Process, f.host, env); !match || err != nil {
				t.Fatal(match, err)
			}
			change(f, &client, &request)
			if match, err := client.HostIdentifiesSession(ctx, request.Harness, request.NativeSessionID, request.Process, f.host, env); match || err != nil {
				t.Fatal(match, err)
			}
		}
		f, client, request := newHerdrFixture(t, HarnessCodex)
		delete(f.host, "agent_session")
		fdDir := filepath.Join(client.ProcRoot, strconv.Itoa(request.Process.PID), "fd")
		if err := os.Mkdir(fdDir, 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.Symlink("/private/rollout-2026-11-08-"+testNativeID+".jsonl", filepath.Join(fdDir, "5")); err != nil {
			t.Fatal(err)
		}
		env, _, _ := client.HerdrEnvironment(request.Process, nil)
		if match, err := client.HostIdentifiesSession(ctx, HarnessCodex, testNativeID, request.Process, f.host, env); !match || err != nil {
			t.Fatal(match, err)
		}
		if err := os.Symlink("/private/rollout-2026-11-08-"+testNativeID+".jsonl", filepath.Join(fdDir, "6")); err != nil {
			t.Fatal(err)
		}
		if match, err := client.HostIdentifiesSession(ctx, HarnessCodex, testNativeID, request.Process, f.host, env); !match || err != nil {
			t.Fatal("duplicate same-session fds", match, err)
		}
		if err := os.Symlink("/private/rollout-2026-11-08-"+testAgentID+".jsonl", filepath.Join(fdDir, "7")); err != nil {
			t.Fatal(err)
		}
		if match, err := client.HostIdentifiesSession(ctx, HarnessCodex, testNativeID, request.Process, f.host, env); match || err != nil {
			t.Fatal("ambiguous rollout association", match, err)
		}
	})
	t.Run("target_recheck_and_inbox_readiness", func(t *testing.T) {
		f, client, request := newHerdrFixture(t, HarnessHermes)
		prepared, refusal, err := client.PrepareTerminalWake(ctx, request, []string{"HERDR_PANE_ID=watcher"})
		if err != nil || refusal != nil || prepared == nil || prepared.Target != (HerdrTarget{"own-pane", "own-terminal", 17, 29}) || f.readyCalls != 1 {
			t.Fatal(prepared, refusal, err, f.readyCalls)
		}
		if prepared.Process != request.Process || prepared.Wake != (WakeEnvelope{Session: request.Session, DeliveryID: request.DeliveryID, RequestID: request.RequestID, Status: "uncertain"}) {
			t.Fatalf("missing caller-owned attempt projection: %#v", prepared)
		}
		var serialized map[string]any
		if err := json.Unmarshal(wakeJSON(t, prepared.Wake), &serialized); err != nil {
			t.Fatal(err)
		}
		if _, present := serialized["transport"]; present {
			t.Fatalf("terminal wake gained transport field: %v", serialized)
		}
		wantCalls := [][]string{{"agent", "list"}, {"pane", "process-info", "--pane", "own-pane"}, {"agent", "get", "own-pane"}, {"pane", "process-info", "--pane", "own-pane"}}
		if len(f.calls) != len(wantCalls) {
			t.Fatal(f.calls)
		}
		for i := range wantCalls {
			if !slices.Equal(f.calls[i], wantCalls[i]) {
				t.Fatal(f.calls)
			}
		}
		if ok, err := client.SubmitTerminalWake(ctx, *prepared, "private wake"); !ok || err != nil {
			t.Fatal(ok, err)
		}
		if !slices.Equal(f.calls[len(f.calls)-1], []string{"agent", "prompt", "own-pane", "private wake"}) {
			t.Fatal(f.calls)
		}
		for _, key := range []string{"pane_id", "terminal_id", "revision", "state_change_seq", "agent_status", "focused", "agent_session"} {
			f, client, request := newHerdrFixture(t, HarnessHermes)
			current := map[string]any{}
			for k, v := range f.host {
				current[k] = v
			}
			f.current = current
			switch key {
			case "revision", "state_change_seq":
				current[key] = int64(999)
			case "focused":
				current[key] = true
			case "agent_session":
				current[key] = map[string]any{"kind": "id", "value": "other", "agent": HarnessHermes}
			default:
				current[key] = "other"
			}
			prepared, refusal, err := client.PrepareTerminalWake(ctx, request, nil)
			if prepared != nil || refusal != nil || err != nil || f.readyCalls != 0 {
				t.Fatalf("stale %s: %v %v %v %d", key, prepared, refusal, err, f.readyCalls)
			}
		}
		f, client, request = newHerdrFixture(t, HarnessHermes)
		f.readyDelivery = "another-delivery"
		if prepared, refusal, err := client.PrepareTerminalWake(ctx, request, nil); prepared != nil || refusal != nil || err != nil {
			t.Fatal(prepared, refusal, err)
		}
		f.readyErr = errors.New("fixture readiness failure")
		if _, _, err := client.PrepareTerminalWake(ctx, request, nil); !errors.Is(err, f.readyErr) {
			t.Fatal(err)
		}
		client.InboxReady = nil
		if prepared, _, err := client.PrepareTerminalWake(ctx, request, nil); prepared != nil || err == nil {
			t.Fatal("missing readiness provider accepted", prepared, err)
		}
		for _, confirmed := range []map[string]any{{"pane_id": "other", "terminal_id": "own-terminal"}, {"pane_id": "own-pane", "terminal_id": "other"}} {
			f, client, request := newHerdrFixture(t, HarnessHermes)
			prepared, _, err := client.PrepareTerminalWake(ctx, request, nil)
			if err != nil {
				t.Fatal(err)
			}
			f.prompt = map[string]any{"type": "agent_prompted", "agent": confirmed}
			ok, err := client.SubmitTerminalWake(ctx, *prepared, "wake")
			var hostErr *HerdrError
			if ok || !errors.As(err, &hostErr) || hostErr.Code != "WAKE_UNCERTAIN" || hostErr.Message != "host did not confirm prompt submission; no automatic resend" {
				t.Fatal(ok, err)
			}
			if len(f.calls) != 5 {
				t.Fatal("uncertain prompt retried", f.calls)
			}
		}
	})
	t.Run("diagnostics_and_Agy_parent", func(t *testing.T) {
		for _, tc := range []struct {
			status            string
			focused           any
			duplicate         bool
			condition, reason string
		}{
			{"idle", false, true, "terminal_target_ambiguous", "2 eligible Herdr panes match this session; exactly one is required"},
			{"busy", false, false, "terminal_target_not_idle", "0 eligible Herdr panes match this session (1 not idle, 0 focused); exactly one idle unfocused pane is required"},
			{"done", true, false, "terminal_target_focused", "0 eligible Herdr panes match this session (0 not idle, 1 focused); exactly one idle unfocused pane is required"},
			{"idle", nil, false, "terminal_target_focused", "0 eligible Herdr panes match this session (0 not idle, 1 focused); exactly one idle unfocused pane is required"},
		} {
			f, client, request := newHerdrFixture(t, HarnessHermes)
			f.host["agent_status"], f.host["focused"] = tc.status, tc.focused
			if tc.duplicate {
				f.candidates = append(f.candidates, f.host)
			}
			prepared, refusal, err := client.PrepareTerminalWake(ctx, request, nil)
			if err != nil || prepared != nil || refusal == nil || refusal.Condition != tc.condition || refusal.Reason != tc.reason || f.readyCalls != 0 {
				t.Fatal(prepared, refusal, err)
			}
		}
		f, client, request := newHerdrFixture(t, HarnessHermes)
		f.candidates = []any{}
		_, refusal, err := client.PrepareTerminalWake(ctx, request, nil)
		if err != nil || refusal == nil || refusal.Condition != "terminal_target_missing" || refusal.Reason != "no Herdr pane identifies this session (harness, native session and foreground process); exactly one eligible pane is required" {
			t.Fatal(refusal, err)
		}
		if err := os.WriteFile(filepath.Join(client.ProcRoot, strconv.Itoa(request.Process.PID), "environ"), nil, 0600); err != nil {
			t.Fatal(err)
		}
		_, refusal, err = client.PrepareTerminalWake(ctx, request, nil)
		if err != nil || refusal == nil || refusal.Condition != "terminal_host_unavailable" || refusal.Reason != "native process has no Herdr host environment" {
			t.Fatal(refusal, err)
		}
		f, client, request = newHerdrFixture(t, "agy")
		parentID := "parent-session"
		f.host["agent_session"] = map[string]any{"kind": "id", "value": parentID, "agent": "agy"}
		state := map[string]any{"agent": map[string]any{"display_name": "agent-parent", "native_session_id": parentID}, "process": request.Process, "retired": 0.0}
		if err := os.WriteFile(filepath.Join(request.StateDir, "parent.json"), wakeJSON(t, state), 0600); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(request.StateDir, "broken.json"), []byte("malformed"), 0600); err != nil {
			t.Fatal(err)
		}
		prepared, refusal, err := client.PrepareTerminalWake(ctx, request, nil)
		if err != nil || prepared != nil || refusal == nil || refusal.Condition != "native_transport_unavailable" || refusal.Reason != "Agy subagent conversation inside agent-parent's process (native parent-session); it runs only when its parent messages it, so it has no wake transport" {
			t.Fatal(prepared, refusal, err)
		}
		state["retired"] = true
		if err := os.WriteFile(filepath.Join(request.StateDir, "parent.json"), wakeJSON(t, state), 0600); err != nil {
			t.Fatal(err)
		}
		if _, refusal, err := client.PrepareTerminalWake(ctx, request, nil); err != nil || refusal == nil || refusal.Condition != "terminal_target_missing" {
			t.Fatal(refusal, err)
		}
	})
	t.Run("bounded_owned_command_runner", func(t *testing.T) {
		directory := t.TempDir()
		shim := filepath.Join(directory, "private-herdr")
		if err := os.WriteFile(shim, []byte("#!/bin/sh\nexec \"$CAIRN_HERDR_TEST_BINARY\" '-test.run=^TestHerdrTerminalWake$'\n"), 0700); err != nil {
			t.Fatal(err)
		}
		base := append(os.Environ(), "CAIRN_HERDR_TEST_BINARY="+os.Args[0], "GORACE=atexit_sleep_ms=0")
		for _, tc := range []struct {
			mode          string
			prompt        bool
			code, message string
		}{
			{"valid", false, "", ""}, {"exit", false, "", ""}, {"invalid_utf8", false, "HOST_UNAVAILABLE", "Herdr returned an invalid response"},
			{"stdout_overflow", false, "HOST_UNAVAILABLE", "Herdr output exceeded limit"}, {"stderr_overflow", true, "WAKE_UNCERTAIN", "Herdr output exceeded limit"}, {"both_at_limit", false, "", ""},
		} {
			args := []string{shim, "agent", "list"}
			if tc.prompt {
				args = []string{shim, "agent", "prompt", "p", "wake"}
			}
			stdout, stderr, exit, err := DefaultHerdrCommandRunner(ctx, args, append(slices.Clone(base), "CAIRN_HERDR_TEST_MODE="+tc.mode))
			if len(stdout) > HerdrOutputBytes || len(stderr) > HerdrOutputBytes {
				t.Fatal("runner retained oversized pipes")
			}
			if tc.code != "" {
				var hostErr *HerdrError
				if !errors.As(err, &hostErr) || hostErr.Code != tc.code || hostErr.Message != tc.message {
					t.Fatalf("%s: %T %v", tc.mode, err, err)
				}
				continue
			}
			if err != nil {
				t.Fatalf("%s: %v", tc.mode, err)
			}
			switch tc.mode {
			case "valid":
				if exit != 0 || !bytes.Equal(stdout, []byte(`{"result":{"agents":[]}}`)) || string(stderr) != "\ufffd" {
					t.Fatal(string(stdout), string(stderr), exit)
				}
			case "exit":
				if exit != 7 {
					t.Fatal(exit)
				}
			case "both_at_limit":
				if len(stdout) != HerdrOutputBytes || len(stderr) != HerdrOutputBytes {
					t.Fatal(len(stdout), len(stderr))
				}
			}
		}
		pidFile := filepath.Join(directory, "child.pid")
		bounded, cancel := context.WithTimeout(ctx, time.Second)
		defer cancel()
		_, _, _, err := DefaultHerdrCommandRunner(bounded, []string{shim, "agent", "prompt", "p", "wake"}, append(slices.Clone(base), "CAIRN_HERDR_TEST_MODE=delay", "CAIRN_HERDR_TEST_PID="+pidFile))
		var hostErr *HerdrError
		if !errors.As(err, &hostErr) || hostErr.Code != "WAKE_UNCERTAIN" {
			t.Fatal(err)
		}
		raw, err := os.ReadFile(pidFile)
		if err != nil {
			t.Fatal("owned child did not start", err)
		}
		pid, err := strconv.Atoi(string(raw))
		if err != nil || pid <= 1 {
			t.Fatal("invalid owned child PID", string(raw), err)
		}
		if _, err := procpeer.ProcessRefOf(pid); err == nil {
			t.Fatal("owned timed-out child was not reaped", pid)
		}
	})
}

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
