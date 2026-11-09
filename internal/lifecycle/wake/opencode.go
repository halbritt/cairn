package wake

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/halbritt/cairn/internal/procpeer"
)

const (
	OpenCodeConnectTimeout = 4 * time.Second
	OpenCodeReplyTimeout   = 4 * time.Second
	OpenCodeCancelTimeout  = 35 * time.Second
	OpenCodeBusyCode       = -32600
	queueMaxPeerBytes      = 1024 * 1024
)

type OpenCodeQueueError struct {
	Message string
	Cause   error
}
type OpenCodeQueueUnavailable struct {
	Message string
	Cause   error
}
type OpenCodeQueueUnsupported struct {
	Message string
	Cause   error
}
type OpenCodeQueueRefused struct {
	Message string
	Cause   error
}
type OpenCodeQueueAlreadyAdmitted struct {
	Message string
	Cause   error
}
type OpenCodeCancelRefused struct {
	Reason, Message string
	Cause           error
}
type OpenCodeCancelUncertain struct {
	Message string
	Cause   error
}

func (e *OpenCodeQueueError) Error() string           { return e.Message }
func (e *OpenCodeQueueError) Unwrap() error           { return e.Cause }
func (e *OpenCodeQueueUnavailable) Error() string     { return e.Message }
func (e *OpenCodeQueueUnavailable) Unwrap() error     { return e.Cause }
func (e *OpenCodeQueueUnsupported) Error() string     { return e.Message }
func (e *OpenCodeQueueUnsupported) Unwrap() error     { return e.Cause }
func (e *OpenCodeQueueRefused) Error() string         { return e.Message }
func (e *OpenCodeQueueRefused) Unwrap() error         { return e.Cause }
func (e *OpenCodeQueueAlreadyAdmitted) Error() string { return e.Message }
func (e *OpenCodeQueueAlreadyAdmitted) Unwrap() error { return e.Cause }
func (e *OpenCodeCancelRefused) Error() string        { return e.Message }
func (e *OpenCodeCancelRefused) Unwrap() error        { return e.Cause }
func (e *OpenCodeCancelUncertain) Error() string      { return e.Message }
func (e *OpenCodeCancelUncertain) Unwrap() error      { return e.Cause }

func IsOpenCodeQueueUnavailable(err error) bool {
	var unavailable *OpenCodeQueueUnavailable
	var unsupported *OpenCodeQueueUnsupported
	return errors.As(err, &unavailable) || errors.As(err, &unsupported)
}
func IsOpenCodeQueueUnsupported(err error) bool {
	var e *OpenCodeQueueUnsupported
	return errors.As(err, &e)
}
func IsOpenCodeQueueRefused(err error) bool { var e *OpenCodeQueueRefused; return errors.As(err, &e) }
func IsOpenCodeQueueAlreadyAdmitted(err error) bool {
	var e *OpenCodeQueueAlreadyAdmitted
	return errors.As(err, &e)
}
func IsOpenCodeCancelRefused(err error) bool { var e *OpenCodeCancelRefused; return errors.As(err, &e) }
func IsOpenCodeCancelUncertain(err error) bool {
	var e *OpenCodeCancelUncertain
	return errors.As(err, &e)
}
func IsOpenCodeQueueError(err error) bool {
	var e *OpenCodeQueueError
	return errors.As(err, &e) || IsOpenCodeQueueUnavailable(err) || IsOpenCodeQueueRefused(err) ||
		IsOpenCodeQueueAlreadyAdmitted(err) || IsOpenCodeCancelRefused(err) || IsOpenCodeCancelUncertain(err)
}

// DefaultClientMessages are the Python Unix transport's pre-payload refusals.
func DefaultClientMessages() procpeer.ClientMessages {
	return procpeer.ClientMessages{WrongProcess: "native socket belongs to a different process", WrongUser: "native socket belongs to another user",
		IdentityChanged: "native process identity has changed", EndpointUnavailable: "native queue endpoint is unavailable"}
}

type queueRequest struct {
	ID     int            `json:"id"`
	Method string         `json:"method"`
	Params map[string]any `json:"params"`
}

type queueTransportError struct {
	beforeSend bool
	cause      error
}

func (e *queueTransportError) Error() string { return e.cause.Error() }
func (e *queueTransportError) Unwrap() error { return e.cause }

func queueDuration(selected, fallback time.Duration) time.Duration {
	if selected > 0 {
		return selected
	}
	return fallback
}

// queueExchange is mechanism shared by the two line-based native clients.
// Admission/refusal interpretation stays in each protocol below. A write error
// is always uncertain, including a write that reports zero bytes.
func queueExchange(ctx context.Context, endpoint string, process ProcessRef, request queueRequest, connectTimeout, replyTimeout time.Duration) (any, error) {
	frame, err := CompactASCIIJSONLine(request, 0, nil)
	if err != nil {
		return nil, &queueTransportError{true, err}
	}
	conn, _, err := procpeer.ConnectClient(ctx, endpoint, process, procpeer.ClientPolicy{Owner: procpeer.ProcessOwner, Messages: DefaultClientMessages()}, connectTimeout)
	if err != nil {
		return nil, &queueTransportError{true, err}
	}
	defer conn.Close()
	if err := ctx.Err(); err != nil {
		return nil, &queueTransportError{true, err}
	}
	stop := context.AfterFunc(ctx, func() { _ = conn.Close() })
	defer stop()
	writeTimeout := replyTimeout
	if deadline, ok := ctx.Deadline(); ok {
		writeTimeout = min(writeTimeout, max(time.Nanosecond, time.Until(deadline)))
	}
	if err := procpeer.WriteClientFrame(conn, frame, writeTimeout); err != nil {
		return nil, &queueTransportError{false, err}
	}
	raw, err := procpeer.ReadClientLine(conn, queueMaxPeerBytes, replyTimeout, false)
	if err != nil {
		return nil, &queueTransportError{false, err}
	}
	v, err := decodeStrictJSONValue(raw)
	if err != nil {
		return nil, &queueTransportError{false, err}
	}
	return v, nil
}

func queueUnavailableMessage(err error) string {
	var refusal *procpeer.ClientRefusal
	if errors.As(err, &refusal) {
		return refusal.Message
	}
	return "native queue endpoint is unavailable"
}

func queueBeforeSend(err error) bool {
	var e *queueTransportError
	return errors.As(err, &e) && e.beforeSend
}
func queueResponse(v any, id int64) (map[string]any, bool) {
	m, ok := v.(map[string]any)
	if !ok {
		return nil, false
	}
	actual, ok := parseExactInt64(m["id"])
	return m, ok && actual == id
}

// Python numeric equality also matches integral float values. This is limited
// to capability IDs and Hermes error codes; mutating response IDs remain exact.
func queueNumberEqual(v any, want int64) bool {
	n, ok := v.(json.Number)
	if !ok {
		return false
	}
	if i, ok := parseExactInt64(n); ok {
		return i == want
	}
	f, err := n.Float64()
	return err == nil && f == float64(want)
}

func openCodeRPCError(v any) (int64, string, bool) {
	e, ok := v.(map[string]any)
	if !ok {
		return 0, "", false
	}
	code, ok := parseExactInt64(e["code"])
	message, stringOK := e["message"].(string)
	return code, message, ok && stringOK
}

type OpenCodeQueueClient struct {
	ConnectTimeout, ReplyTimeout, CancelTimeout time.Duration
	// Peer attestation remains kernel-rooted in procpeer.ConnectClient.
	ProcRoot string
	TmpDir   string
}

func SelectOpenCodeQueueEndpoint(harness string, process ProcessRef) (string, bool) {
	return (OpenCodeQueueClient{}).SelectOpenCodeQueueEndpoint(harness, process)
}
func (c OpenCodeQueueClient) SelectOpenCodeQueueEndpoint(harness string, process ProcessRef) (string, bool) {
	if harness != HarnessOpenCode || process.PID <= 0 {
		return "", false
	}
	tmp := c.TmpDir
	if tmp == "" {
		tmp = "/tmp"
	}
	endpoint := filepath.Join(tmp, fmt.Sprintf("cairn-opencode-%d.sock", process.PID))
	info, err := os.Stat(endpoint)
	if err != nil || info.Mode()&os.ModeSocket == 0 {
		return "", false
	}
	return endpoint, true
}
func SelectOpenCodeIdleEndpoint(ctx context.Context, harness string, process ProcessRef, nativeID string, unsupported *ProcessRef) (string, bool) {
	return (OpenCodeQueueClient{}).SelectOpenCodeIdleEndpoint(ctx, harness, process, nativeID, unsupported)
}
func (c OpenCodeQueueClient) SelectOpenCodeIdleEndpoint(ctx context.Context, harness string, process ProcessRef, nativeID string, unsupported *ProcessRef) (string, bool) {
	if unsupported != nil && *unsupported == process {
		return "", false
	}
	endpoint, ok := c.SelectOpenCodeQueueEndpoint(harness, process)
	if !ok || !c.SupportsIdle(ctx, endpoint, process, nativeID) {
		return "", false
	}
	return endpoint, true
}

func (c OpenCodeQueueClient) supports(ctx context.Context, endpoint string, process ProcessRef, nativeID, capability string) bool {
	v, err := queueExchange(ctx, endpoint, process, queueRequest{4, "session/capabilities", map[string]any{"session_id": nativeID}},
		queueDuration(c.ConnectTimeout, OpenCodeConnectTimeout), queueDuration(c.ReplyTimeout, OpenCodeReplyTimeout))
	if err != nil {
		return false
	}
	m, ok := v.(map[string]any)
	if !ok || !queueNumberEqual(m["id"], 4) {
		return false
	}
	result, ok := m["result"].(map[string]any)
	if !ok {
		return false
	}
	flag, ok := result[capability].(bool)
	return ok && flag && result["session_id"] == nativeID
}
func (c OpenCodeQueueClient) SupportsIdle(ctx context.Context, endpoint string, process ProcessRef, nativeID string) bool {
	return c.supports(ctx, endpoint, process, nativeID, "prompt_idle")
}
func (c OpenCodeQueueClient) SupportsCancel(ctx context.Context, endpoint string, process ProcessRef, nativeID string) bool {
	return c.supports(ctx, endpoint, process, nativeID, "cancel_request")
}
func (c OpenCodeQueueClient) SupportsToolCapture(ctx context.Context, endpoint string, process ProcessRef, nativeID string) bool {
	return c.supports(ctx, endpoint, process, nativeID, "tool_capture")
}
func OpenCodeSupportsIdle(ctx context.Context, endpoint string, process ProcessRef, nativeID string) bool {
	return (OpenCodeQueueClient{}).SupportsIdle(ctx, endpoint, process, nativeID)
}
func OpenCodeSupportsCancel(ctx context.Context, endpoint string, process ProcessRef, nativeID string) bool {
	return (OpenCodeQueueClient{}).SupportsCancel(ctx, endpoint, process, nativeID)
}
func OpenCodeSupportsToolCapture(ctx context.Context, endpoint string, process ProcessRef, nativeID string) bool {
	return (OpenCodeQueueClient{}).SupportsToolCapture(ctx, endpoint, process, nativeID)
}

func OpenCodeEnqueue(ctx context.Context, endpoint string, process ProcessRef, nativeID, text, deliveryID, expectedSessionID, requestID string) (string, bool, error) {
	return (OpenCodeQueueClient{}).Enqueue(ctx, endpoint, process, nativeID, text, deliveryID, expectedSessionID, requestID)
}
func (c OpenCodeQueueClient) Enqueue(ctx context.Context, endpoint string, process ProcessRef, nativeID, text, deliveryID, expectedSessionID, requestID string) (string, bool, error) {
	if requestID == "" {
		requestID = deliveryID
	}
	if expectedSessionID == "" {
		expectedSessionID = nativeID
	}
	for _, field := range []struct{ name, value string }{{"native_id", nativeID}, {"delivery_id", deliveryID}, {"request_id", requestID}, {"expected_session_id", expectedSessionID}} {
		if pythonStrip(field.value) == "" {
			return "", false, &OpenCodeQueueUnavailable{Message: field.name + " must be a nonempty string"}
		}
	}
	v, err := queueExchange(ctx, endpoint, process, queueRequest{1, "session/prompt_idle", map[string]any{"session_id": nativeID, "text": text, "delivery_id": deliveryID, "request_id": requestID, "expected_session_id": expectedSessionID}},
		queueDuration(c.ConnectTimeout, OpenCodeConnectTimeout), queueDuration(c.ReplyTimeout, OpenCodeReplyTimeout))
	if err != nil {
		if queueBeforeSend(err) {
			return "", false, &OpenCodeQueueUnavailable{queueUnavailableMessage(err), err}
		}
		if errors.Is(err, io.EOF) {
			return "", false, &OpenCodeQueueError{"native queue outcome is uncertain; OpenCode closed connection without response; do not automatically resend", err}
		}
		return "", false, &OpenCodeQueueError{"native queue outcome is uncertain; do not automatically resend", err}
	}
	m, ok := queueResponse(v, 1)
	_, hasError := m["error"]
	_, hasResult := m["result"]
	if !ok || hasError == hasResult {
		return "", false, &OpenCodeQueueError{Message: "native queue response is invalid; outcome is uncertain; do not automatically resend"}
	}
	if hasError {
		code, message, ok := openCodeRPCError(m["error"])
		if !ok {
			return "", false, &OpenCodeQueueError{Message: "native queue error is invalid; outcome is uncertain; do not automatically resend"}
		}
		switch {
		case code == OpenCodeBusyCode:
			return "", false, &OpenCodeQueueUnavailable{Message: "native OpenCode busy: " + message}
		case code == -32004 || code == -32601 || code == -32000 && strings.HasPrefix(message, "UNSUPPORTED_CONTROL:"):
			return "", false, &OpenCodeQueueUnsupported{Message: "native OpenCode prompt_idle unavailable: " + message}
		case code == -32001 || code == -32002:
			return "", false, &OpenCodeQueueUnavailable{Message: "native session unavailable: " + message}
		case code == -32003:
			return "", false, &OpenCodeQueueRefused{Message: "native OpenCode refused prompt_idle: " + message}
		case code == -32005:
			return "", false, &OpenCodeQueueAlreadyAdmitted{Message: "native OpenCode request already admitted: " + message}
		default:
			return "", false, &OpenCodeQueueError{Message: "native OpenCode prompt_idle outcome is uncertain: " + message}
		}
	}
	result, ok := m["result"].(map[string]any)
	queued, qok := result["queued"].(bool)
	started, sok := result["started"].(bool)
	if !ok || !qok || !queued || !sok || !started || result["queued_id"] != deliveryID || result["request_id"] != requestID || result["session_id"] != nativeID {
		return "", false, &OpenCodeQueueError{Message: "native queue acknowledgment is invalid; outcome is uncertain; do not automatically resend"}
	}
	return deliveryID, true, nil
}

func OpenCodeCancelRequest(ctx context.Context, endpoint string, process ProcessRef, nativeID, requestID, expectedTurnID string) (map[string]any, error) {
	return (OpenCodeQueueClient{}).CancelRequest(ctx, endpoint, process, nativeID, requestID, expectedTurnID)
}
func (c OpenCodeQueueClient) CancelRequest(ctx context.Context, endpoint string, process ProcessRef, nativeID, requestID, expectedTurnID string) (map[string]any, error) {
	for _, field := range []struct{ name, value string }{{"native_id", nativeID}, {"request_id", requestID}, {"expected_turn_id", expectedTurnID}} {
		if pythonStrip(field.value) == "" {
			return nil, &OpenCodeCancelRefused{Reason: "invalid", Message: field.name + " must be a nonempty string"}
		}
	}
	v, err := queueExchange(ctx, endpoint, process, queueRequest{5, "session/cancel_request", map[string]any{"session_id": nativeID, "request_id": requestID, "expected_turn_id": expectedTurnID}}, queueDuration(c.ConnectTimeout, OpenCodeConnectTimeout), queueDuration(c.CancelTimeout, OpenCodeCancelTimeout))
	if err != nil {
		if queueBeforeSend(err) {
			return nil, &OpenCodeCancelRefused{"unavailable", queueUnavailableMessage(err), err}
		}
		if errors.Is(err, io.EOF) {
			return nil, &OpenCodeCancelUncertain{"native cancel outcome is uncertain; OpenCode closed connection without response", err}
		}
		return nil, &OpenCodeCancelUncertain{"native cancel outcome is uncertain; do not automatically resend", err}
	}
	m, ok := queueResponse(v, 5)
	_, hasError := m["error"]
	_, hasResult := m["result"]
	if !ok || hasError == hasResult {
		return nil, &OpenCodeCancelUncertain{Message: "native cancel response is invalid; outcome is uncertain"}
	}
	if hasError {
		code, message, ok := openCodeRPCError(m["error"])
		if !ok {
			return nil, &OpenCodeCancelUncertain{Message: "native cancel error is invalid; outcome is uncertain"}
		}
		reason := ""
		switch code {
		case -32601, -32004:
			reason = "unsupported"
		case -32602:
			reason = "invalid"
		case -32002:
			reason = "session_unavailable"
		case -32006:
			reason = "turn_mismatch"
		case -32007:
			reason = "not_active"
		case -32003:
			reason = "native_refused"
		}
		if code == -32000 && strings.HasPrefix(message, "UNSUPPORTED_CONTROL:") {
			reason = "unsupported"
		}
		if reason != "" {
			return nil, &OpenCodeCancelRefused{Reason: reason, Message: "native OpenCode did not cancel: " + message}
		}
		return nil, &OpenCodeCancelUncertain{Message: "native OpenCode cancel outcome is uncertain: " + message}
	}
	result, ok := m["result"].(map[string]any)
	if !ok || result["outcome"] != "accepted" || result["session_id"] != nativeID || result["request_id"] != requestID || result["turn_id"] != expectedTurnID {
		return nil, &OpenCodeCancelUncertain{Message: "native cancel acknowledgment is invalid; outcome is uncertain"}
	}
	return result, nil
}

func OpenCodeStatus(ctx context.Context, endpoint string, process ProcessRef, nativeID string) (map[string]any, error) {
	return (OpenCodeQueueClient{}).Status(ctx, endpoint, process, nativeID)
}
func (c OpenCodeQueueClient) Status(ctx context.Context, endpoint string, process ProcessRef, nativeID string) (map[string]any, error) {
	v, err := queueExchange(ctx, endpoint, process, queueRequest{3, "session/status", map[string]any{"session_id": nativeID}}, queueDuration(c.ConnectTimeout, OpenCodeConnectTimeout), queueDuration(c.ReplyTimeout, OpenCodeReplyTimeout))
	if err != nil {
		if queueBeforeSend(err) {
			return nil, &OpenCodeQueueUnavailable{queueUnavailableMessage(err), err}
		}
		return nil, &OpenCodeQueueError{"native OpenCode closed connection without response", err}
	}
	m, ok := queueResponse(v, 3)
	if !ok {
		return nil, &OpenCodeQueueError{Message: "invalid bridge status response identity"}
	}
	if e, found := m["error"]; found {
		return nil, &OpenCodeQueueError{Message: fmt.Sprintf("native OpenCode status error: %v", e)}
	}
	result, ok := m["result"].(map[string]any)
	status, stringOK := result["status"].(string)
	if !ok || result["session_id"] != nativeID || !stringOK || status == "" {
		return nil, &OpenCodeQueueError{Message: "invalid bridge status response schema"}
	}
	return result, nil
}
