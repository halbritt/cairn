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
)

const (
	HermesConnectTimeout = 4 * time.Second
	HermesReplyTimeout   = 4 * time.Second
	HermesAbortTimeout   = 30 * time.Second
)

type HermesQueueError struct {
	Message string
	Cause   error
}
type HermesQueueUnavailable struct {
	Message string
	Cause   error
}
type HermesRequestMismatchError struct {
	Message string
	Cause   error
}

func (e *HermesQueueError) Error() string           { return e.Message }
func (e *HermesQueueError) Unwrap() error           { return e.Cause }
func (e *HermesQueueUnavailable) Error() string     { return e.Message }
func (e *HermesQueueUnavailable) Unwrap() error     { return e.Cause }
func (e *HermesRequestMismatchError) Error() string { return e.Message }
func (e *HermesRequestMismatchError) Unwrap() error { return e.Cause }
func IsHermesQueueUnavailable(err error) bool {
	var e *HermesQueueUnavailable
	return errors.As(err, &e)
}
func IsHermesRequestMismatchError(err error) bool {
	var e *HermesRequestMismatchError
	return errors.As(err, &e)
}
func IsHermesQueueError(err error) bool {
	var e *HermesQueueError
	return errors.As(err, &e) || IsHermesQueueUnavailable(err) || IsHermesRequestMismatchError(err)
}

type HermesQueueClient struct {
	ConnectTimeout, ReplyTimeout, AbortTimeout time.Duration
	// Peer attestation remains kernel-rooted in procpeer.ConnectClient.
	ProcRoot string
	TmpDir   string
}

func SelectHermesQueueEndpoint(harness string, process ProcessRef) (string, bool) {
	return (HermesQueueClient{}).SelectHermesQueueEndpoint(harness, process)
}
func (c HermesQueueClient) SelectHermesQueueEndpoint(harness string, process ProcessRef) (string, bool) {
	if harness != HarnessHermes || process.PID <= 0 {
		return "", false
	}
	tmp := c.TmpDir
	if tmp == "" {
		tmp = "/tmp"
	}
	endpoint := filepath.Join(tmp, fmt.Sprintf("cairn-hermes-%d.sock", process.PID))
	info, err := os.Stat(endpoint)
	if err != nil || info.Mode()&os.ModeSocket == 0 {
		return "", false
	}
	return endpoint, true
}

type hermesResponseMessages struct{ uncertain, eof, identity string }

func (c HermesQueueClient) exchange(ctx context.Context, endpoint string, process ProcessRef, request queueRequest, timeout time.Duration, messages hermesResponseMessages) (map[string]any, error) {
	v, err := queueExchange(ctx, endpoint, process, request, queueDuration(c.ConnectTimeout, HermesConnectTimeout), timeout)
	if err != nil {
		if queueBeforeSend(err) {
			return nil, &HermesQueueUnavailable{queueUnavailableMessage(err), err}
		}
		if errors.Is(err, io.EOF) {
			return nil, &HermesQueueError{messages.eof, err}
		}
		return nil, &HermesQueueError{messages.uncertain, err}
	}
	m, ok := queueResponse(v, int64(request.ID))
	if !ok {
		return nil, &HermesQueueError{Message: messages.identity}
	}
	return m, nil
}

func pythonJSONTypeName(v any) string {
	switch v := v.(type) {
	case nil:
		return "NoneType"
	case bool:
		return "bool"
	case json.Number:
		if strings.ContainsAny(v.String(), ".eE") {
			return "float"
		}
		return "int"
	case string:
		return "str"
	case []any:
		return "list"
	case map[string]any:
		return "dict"
	default:
		return fmt.Sprintf("%T", v)
	}
}

func pythonMismatchValue(v any) string {
	switch v {
	case nil:
		return "None"
	case true:
		return "True"
	case false:
		return "False"
	default:
		return fmt.Sprint(v)
	}
}

func hermesError(v any) (map[string]any, string, bool) {
	e, ok := v.(map[string]any)
	if !ok {
		return nil, "", false
	}
	message, ok := e["message"].(string)
	return e, message, ok
}

func HermesEnqueue(ctx context.Context, endpoint string, process ProcessRef, nativeID, text, clientID, expectedSessionID, requestID, deliveryID string) (string, bool, error) {
	return (HermesQueueClient{}).Enqueue(ctx, endpoint, process, nativeID, text, clientID, expectedSessionID, requestID, deliveryID)
}
func (c HermesQueueClient) Enqueue(ctx context.Context, endpoint string, process ProcessRef, nativeID, text, clientID, expectedSessionID, requestID, deliveryID string) (string, bool, error) {
	if expectedSessionID == "" {
		expectedSessionID = nativeID
	}
	params := map[string]any{"session_id": nativeID, "text": text, "client_id": clientID, "expected_session_id": expectedSessionID}
	if requestID != "" {
		params["request_id"] = requestID
	}
	if deliveryID != "" {
		params["delivery_id"] = deliveryID
	}
	m, err := c.exchange(ctx, endpoint, process, queueRequest{1, "session/queue_message", params}, queueDuration(c.ReplyTimeout, HermesReplyTimeout), hermesResponseMessages{
		uncertain: "native queue outcome is uncertain; do not automatically resend",
		eof:       "native queue outcome is uncertain; Hermes closed connection without response; do not automatically resend",
		identity:  "native queue outcome is uncertain; invalid bridge response identity",
	})
	if err != nil {
		return "", false, err
	}
	if v, present := m["error"]; present {
		e, message, ok := hermesError(v)
		if !ok {
			return "", false, &HermesQueueError{Message: "native queue outcome is uncertain; invalid bridge error response"}
		}
		if queueNumberEqual(e["code"], -32001) || queueNumberEqual(e["code"], -32002) || strings.Contains(message, "SESSION_NOT_FOUND") || strings.Contains(message, "SESSION_MISMATCH") {
			return "", false, &HermesQueueUnavailable{Message: "native session unavailable: " + message}
		}
		return "", false, &HermesQueueError{Message: "native Hermes refused queue_message: " + message}
	}
	result, ok := m["result"].(map[string]any)
	if !ok {
		return "", false, &HermesQueueError{Message: "invalid bridge response schema: expected dict result, got " + pythonJSONTypeName(m["result"])}
	}
	queued, ok := result["queued"].(bool)
	if !ok || !queued {
		return "", false, &HermesQueueError{Message: "invalid bridge response schema: expected queued=True in result"}
	}
	started, hasStarted := result["started"].(bool)
	if !hasStarted {
		busy, ok := result["busy"].(bool)
		if !ok {
			return "", false, &HermesQueueError{Message: "invalid bridge response schema: missing started or busy boolean field"}
		}
		started = !busy
	}
	if result["session_id"] != nativeID {
		return "", false, &HermesQueueError{Message: fmt.Sprintf("session ID mismatch in response: expected %s, got %s", nativeID, pythonMismatchValue(result["session_id"]))}
	}
	if requestID != "" && result["request_id"] != requestID {
		return "", false, &HermesQueueError{Message: fmt.Sprintf("request ID mismatch in response: expected %s, got %s", requestID, pythonMismatchValue(result["request_id"]))}
	}
	if deliveryID != "" && result["delivery_id"] != deliveryID {
		return "", false, &HermesQueueError{Message: fmt.Sprintf("delivery ID mismatch in response: expected %s, got %s", deliveryID, pythonMismatchValue(result["delivery_id"]))}
	}
	queuedID, stringOK := result["queued_id"].(string)
	if !stringOK || queuedID != clientID {
		return "", false, &HermesQueueError{Message: "native queue outcome is uncertain; queue submission ID mismatch"}
	}
	return queuedID, started, nil
}

func HermesAbort(ctx context.Context, endpoint string, process ProcessRef, nativeID, expectedRequestID, expectedTurnID string) (map[string]any, error) {
	return (HermesQueueClient{}).Abort(ctx, endpoint, process, nativeID, expectedRequestID, expectedTurnID)
}
func (c HermesQueueClient) Abort(ctx context.Context, endpoint string, process ProcessRef, nativeID, expectedRequestID, expectedTurnID string) (map[string]any, error) {
	params := map[string]any{"session_id": nativeID}
	if expectedRequestID != "" {
		params["expected_request_id"] = expectedRequestID
	}
	if expectedTurnID != "" {
		params["expected_turn_id"] = expectedTurnID
	}
	m, err := c.exchange(ctx, endpoint, process, queueRequest{2, "session/abort", params}, queueDuration(c.AbortTimeout, HermesAbortTimeout), hermesResponseMessages{
		uncertain: "native Hermes abort outcome is uncertain; do not resend",
		eof:       "native Hermes closed connection during abort; outcome uncertain; do not resend",
		identity:  "native Hermes abort outcome is uncertain; invalid bridge response identity",
	})
	if err != nil {
		return nil, err
	}
	if v, present := m["error"]; present {
		e, message, ok := hermesError(v)
		if !ok {
			return nil, &HermesQueueError{Message: "native Hermes abort outcome is uncertain; invalid bridge error response"}
		}
		if queueNumberEqual(e["code"], -32001) || queueNumberEqual(e["code"], -32003) || queueNumberEqual(e["code"], -32004) || strings.Contains(message, "REQUEST_MISMATCH") || strings.Contains(message, "TURN_MISMATCH") || strings.Contains(message, "UNSPECIFIED_ABORT") {
			return nil, &HermesRequestMismatchError{Message: "abort refused: " + message}
		}
		if queueNumberEqual(e["code"], -32002) || strings.Contains(message, "SESSION_MISMATCH") || strings.Contains(message, "SESSION_NOT_FOUND") {
			return nil, &HermesQueueUnavailable{Message: "native session unavailable: " + message}
		}
		return nil, &HermesQueueError{Message: "native Hermes abort refused: " + message}
	}
	result, ok := m["result"].(map[string]any)
	if !ok {
		return nil, &HermesQueueError{Message: "invalid bridge abort response schema: expected dict result, got " + pythonJSONTypeName(m["result"])}
	}
	if result["session_id"] != nativeID {
		return nil, &HermesQueueError{Message: "native Hermes abort outcome is uncertain; session ID mismatch"}
	}
	if expectedRequestID != "" && result["request_id"] != expectedRequestID {
		return nil, &HermesQueueError{Message: "native Hermes abort outcome is uncertain; request ID mismatch"}
	}
	if expectedTurnID != "" && result["turn_id"] != expectedTurnID {
		return nil, &HermesQueueError{Message: "native Hermes abort outcome is uncertain; turn ID mismatch"}
	}
	if _, ok := result["aborted"].(bool); !ok {
		return nil, &HermesQueueError{Message: `invalid bridge abort response schema: missing or invalid boolean "aborted" field`}
	}
	if _, ok := result["turn_stop"].(string); !ok {
		return nil, &HermesQueueError{Message: `invalid bridge abort response schema: missing or invalid string "turn_stop" field`}
	}
	return result, nil
}

func HermesStatus(ctx context.Context, endpoint string, process ProcessRef, nativeID string) (map[string]any, error) {
	return (HermesQueueClient{}).Status(ctx, endpoint, process, nativeID)
}
func (c HermesQueueClient) Status(ctx context.Context, endpoint string, process ProcessRef, nativeID string) (map[string]any, error) {
	return c.inspect(ctx, endpoint, process, nativeID, "session/status", 3, nil)
}
func HermesToolsStatus(ctx context.Context, endpoint string, process ProcessRef, nativeID string, toolIDs []string) (map[string]any, error) {
	return (HermesQueueClient{}).ToolsStatus(ctx, endpoint, process, nativeID, toolIDs)
}
func (c HermesQueueClient) ToolsStatus(ctx context.Context, endpoint string, process ProcessRef, nativeID string, toolIDs []string) (map[string]any, error) {
	return c.inspect(ctx, endpoint, process, nativeID, "session/tools_status", 4, toolIDs)
}
func (c HermesQueueClient) inspect(ctx context.Context, endpoint string, process ProcessRef, nativeID, method string, id int, toolIDs []string) (map[string]any, error) {
	params := map[string]any{"session_id": nativeID}
	label := "status"
	if id == 4 {
		label = "tools_status"
		if toolIDs == nil {
			toolIDs = []string{}
		}
		params["tool_ids"] = toolIDs
	}
	m, err := c.exchange(ctx, endpoint, process, queueRequest{id, method, params}, queueDuration(c.ReplyTimeout, HermesReplyTimeout), hermesResponseMessages{
		uncertain: "native Hermes closed connection without response",
		eof:       "native Hermes closed connection without response",
		identity:  "invalid bridge " + label + " response identity",
	})
	if err != nil {
		return nil, err
	}
	if e, present := m["error"]; present {
		return nil, &HermesQueueError{Message: fmt.Sprintf("native Hermes %s error: %v", label, e)}
	}
	result, ok := m["result"].(map[string]any)
	valid := ok && result["session_id"] == nativeID
	if id == 3 {
		status, ok := result["status"].(string)
		valid = valid && ok && status != ""
	} else {
		_, ok := result["tools"].([]any)
		valid = valid && ok
	}
	if !valid {
		return nil, &HermesQueueError{Message: "invalid bridge " + label + " response schema"}
	}
	return result, nil
}
