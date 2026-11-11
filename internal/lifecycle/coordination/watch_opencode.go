package coordination

import (
	"context"
	"errors"
	"fmt"
	"io"
	"strconv"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/lifecycle/wake"
)

func sourceInt64(v *value.Value, field string) (int64, error) {
	if v == nil || v.Kind != value.Int {
		return 0, &journal.TypeError{Field: field}
	}
	n, err := strconv.ParseInt(v.Integer, 10, 64)
	if err != nil {
		return 0, &value.Error{Class: "OverflowError", Message: field}
	}
	return n, nil
}

func (c *CLI) opencodeCaptureAvailable() bool {
	return true
}

func (c *CLI) scanProcessMarkers(name, val string, since int64) (MarkerScanResult, error) {
	return ScanMarkersAt(c.ProcRoot, c.uid(), name, val, since)
}

func (c *CLI) opencodeCancelRequest(ctx context.Context, endpoint string, process wake.ProcessRef, nativeID, requestID, expectedTurnID string) (map[string]any, error) {
	client := wake.OpenCodeQueueClient{ProcRoot: c.ProcRoot}
	return client.CancelRequest(ctx, endpoint, process, nativeID, requestID, expectedTurnID)
}

func (c *CLI) advanceOpenCodeCancel(ctx context.Context, ns *NativeSession, config, state *value.Value, statePath string) (bool, *value.Value, error) {
	attempt, err := c.opencodeInboxControl(ctx, ns, config, state)
	if err != nil {
		return false, nil, err
	}
	closeObj, err := nativeObj(state.Get("inbox_close"), "inbox_close")
	if err != nil {
		return false, nil, err
	}
	if !value.Truth(attempt) && nativeContains([]string{"cancel_confirmed", "exclusivity_revoked"}, nativeText(closeObj.Get("reason"))) {
		_, err := ns.Release(ctx, nativeText(closeObj.Get("reason")), false)
		return true, nil, err
	}
	if value.Truth(attempt) && value.Truth(attempt.Get("cancel")) {
		cancel, err := nativeObj(attempt.Get("cancel"), "cancel")
		if err != nil {
			return false, nil, err
		}
		if !value.Truth(cancel.Get("confirmed_at")) {
			if err := c.scanOpenCodeCancel(ctx, ns, config, state, statePath, attempt); err != nil {
				return false, nil, err
			}
			if !value.Truth(state.Get("inbox_intent")) {
				return true, nil, nil
			}
			prep, err := c.prepareOpenCodeCancel(config, state, statePath, attempt)
			return true, prep, err
		}
	}
	return false, nil, nil
}

func (c *CLI) prepareOpenCodeCancel(config, state *value.Value, statePath string, attempt *value.Value) (*value.Value, error) {
	if !value.Truth(attempt.Get("turn_exclusive")) || !value.Truth(attempt.Get("native_turn_id")) || nativeContains([]string{"interrupted", "ended"}, nativeText(attempt.Get("turn_stop_state"))) {
		return nil, nil
	}
	prior, err := nativeObj(state.Get("cancel_submission"), "cancel_submission")
	if err != nil {
		return nil, err
	}
	if nativeEqual(prior.Get("attempt_id"), attempt.Get("attempt_id")) {
		return nil, nil
	}
	wakeObj, err := nativeObj(state.Get("idle_wake"), "idle_wake")
	if err != nil {
		return nil, err
	}
	delivery, err := nativeRequired(attempt, "delivery")
	if err != nil {
		return nil, err
	}
	if delivery.Kind != value.Object {
		return nil, &journal.NonObjectError{Field: "delivery"}
	}
	deliveryID, err := nativeRequired(delivery, "delivery_id")
	if err != nil {
		return nil, err
	}
	endpointVal := state.Get("opencode_request_endpoint")
	if !value.Truth(endpointVal) && nativeEqual(wakeObj.Get("delivery_id"), deliveryID) {
		endpointVal = wakeObj.Get("endpoint")
	}
	endpoint := nativeText(endpointVal)
	if endpoint == "" {
		var err error
		endpoint, err = c.opencodeQueueEndpoint(config, state.Get("process"))
		if err != nil {
			return nil, err
		}
	}
	if endpoint == "" {
		return nil, nil
	}
	agent, err := nativeRequired(state, "agent")
	if err != nil {
		return nil, err
	}
	if agent.Kind != value.Object {
		return nil, &journal.NonObjectError{Field: "agent"}
	}
	nativeID, err := nativeRequired(agent, "native_session_id")
	if err != nil {
		return nil, err
	}
	proc, err := nativeRequired(state, "process")
	if err != nil {
		return nil, err
	}
	request := nativeMap(
		"attempt_id", attempt.Get("attempt_id"),
		"session_id", nativeID,
		"request_id", deliveryID,
		"expected_turn_id", attempt.Get("native_turn_id"),
		"process", proc,
		"endpoint", value.Text(endpoint),
	)
	sub := nativeClone(request)
	sub.Set("status", value.Text("uncertain"))
	state.Set("cancel_submission", sub)
	if err := c.writeState(statePath, state); err != nil {
		return nil, err
	}
	return request, nil
}

func allToolsTerminatedOrUnavailable(toolsVal *value.Value) (bool, error) {
	if toolsVal == nil {
		return true, nil
	}
	if toolsVal.Kind != value.Array {
		return false, &journal.TypeError{Field: "tools"}
	}
	for _, item := range toolsVal.Items {
		if item == nil || item.Kind != value.Object {
			return false, &journal.TypeError{Field: "tools"}
		}
		st, err := nativeRequired(item, "stop_state")
		if err != nil {
			return false, err
		}
		if !nativeContains([]string{"terminated", "unavailable"}, nativeText(st)) {
			return false, nil
		}
	}
	return true, nil
}

func (c *CLI) scanOpenCodeCancel(ctx context.Context, ns *NativeSession, config, state *value.Value, statePath string, attempt *value.Value) error {
	if !nativeContains([]string{"ended", "interrupted"}, nativeText(attempt.Get("turn_stop_state"))) {
		return nil
	}
	if !c.opencodeCaptureAvailable() {
		return nil
	}
	calls, err := nativeObj(state.Get("tool_calls"), "tool_calls")
	if err != nil {
		return err
	}
	attemptID, err := nativeRequired(attempt, "attempt_id")
	if err != nil {
		return err
	}
	for _, m := range calls.Members {
		if m.Value == nil || m.Value.Kind != value.Object {
			return &journal.NonObjectError{Field: "tool_calls"}
		}
		if !nativeEqual(m.Value.Get("attempt_id"), attemptID) {
			return nativeRefusal("INBOX_ATTEMPT_MISMATCH", "tool capture belongs to another attempt")
		}
	}
	proc, err := nativeRequired(state, "process")
	if err != nil {
		return err
	}
	if proc.Kind != value.Object {
		return &journal.NonObjectError{Field: "process"}
	}
	startVal, err := nativeRequired(proc, "start")
	if err != nil {
		return err
	}
	sinceVal := state.Get("opencode_turn_since")
	if sinceVal == nil {
		sinceVal = startVal
	}
	since, err := sourceInt64(sinceVal, "since")
	if err != nil {
		return err
	}
	for _, m := range calls.Members {
		sVal, err := nativeRequired(m.Value, "since")
		if err != nil {
			return err
		}
		s, err := sourceInt64(sVal, "since")
		if err != nil {
			return err
		}
		if s < since {
			since = s
		}
	}
	return c.finishScanOpenCodeCancel(ctx, ns, config, state, statePath, attempt, attemptID, since)
}

func (c *CLI) finishScanOpenCodeCancel(ctx context.Context, ns *NativeSession, config, state *value.Value, statePath string, attempt, attemptID *value.Value, since int64) error {
	delivery, err := nativeRequired(attempt, "delivery")
	if err != nil {
		return err
	}
	if delivery.Kind != value.Object {
		return &journal.NonObjectError{Field: "delivery"}
	}
	deliveryID, err := nativeRequired(delivery, "delivery_id")
	if err != nil {
		return err
	}
	scan, err := c.scanProcessMarkers("CAIRN_REQUEST_ID", nativeText(deliveryID), since)
	if err != nil {
		return err
	}
	if !scan.Clear {
		if !value.SameText(attempt.Get("terminal_scan"), "unknown_remaining") {
			req := state.Get("cancel_scan_report")
			if !value.Truth(req) || req.Kind != value.Object || !nativeEqual(req.Get("attempt_id"), attemptID) {
				req = nativeMap("request_id", c.newUUID(), "session", attempt.Get("session"), "attempt_id", attemptID, "terminal_scan", "unknown_remaining", "tools", nativeList())
				state.Set("cancel_scan_report", req)
				if err := c.writeState(statePath, state); err != nil {
					return err
				}
			}
			updated, err := c.call(ctx, config, "session-tool-stop", req, NativeCallOptions{})
			if err != nil {
				return err
			}
			state.Set("inbox_attempt", updated)
			return c.writeState(statePath, state)
		}
		return nil
	}
	nativePop(state, "cancel_scan_report")
	allDone, err := allToolsTerminatedOrUnavailable(attempt.Get("tools"))
	if err != nil {
		return err
	}
	if value.SameText(attempt.Get("terminal_scan"), "clear") && allDone {
		_, err := ns.Release(ctx, OpenCodeCancelReleaseReason(value.Truth(attempt.Get("turn_exclusive"))), false)
		return err
	}
	var stops []*value.Value
	if toolsVal := attempt.Get("tools"); toolsVal != nil && toolsVal.Kind == value.Array {
		for _, item := range toolsVal.Items {
			if !nativeContains([]string{"terminated", "unavailable"}, nativeText(item.Get("stop_state"))) {
				itemID, err := nativeRequired(item, "item_id")
				if err != nil {
					return err
				}
				stops = append(stops, nativeMap("item_id", itemID, "stop_state", "terminated"))
			}
		}
	}
	stopsVal := &value.Value{Kind: value.Array, Items: stops}
	req := state.Get("cancel_final_report")
	if !value.Truth(req) || req.Kind != value.Object || !nativeEqual(req.Get("session"), attempt.Get("session")) || !nativeEqual(req.Get("attempt_id"), attemptID) || !value.SameText(req.Get("terminal_scan"), "clear") || !nativeEqual(req.Get("tools"), stopsVal) {
		req = nativeMap("request_id", c.newUUID(), "session", attempt.Get("session"), "attempt_id", attemptID, "terminal_scan", "clear", "tools", stopsVal)
		state.Set("cancel_final_report", req)
		if err := c.writeState(statePath, state); err != nil {
			return err
		}
	}
	updated, err := c.call(ctx, config, "session-tool-stop", req, NativeCallOptions{})
	if err != nil {
		return err
	}
	state.Set("inbox_attempt", updated)
	if err := c.writeState(statePath, state); err != nil {
		return err
	}
	if updated == nil || updated.Kind != value.Object {
		return &journal.NonObjectError{Field: "inbox_attempt"}
	}
	latestDone, err := allToolsTerminatedOrUnavailable(updated.Get("tools"))
	if err != nil {
		return err
	}
	if value.SameText(updated.Get("terminal_scan"), "clear") && latestDone {
		_, err := ns.Release(ctx, OpenCodeCancelReleaseReason(value.Truth(updated.Get("turn_exclusive"))), false)
		return err
	}
	return nil
}

func (c *CLI) submitOpenCodeCancel(ctx context.Context, config *value.Value, path string, request *value.Value, errOut io.Writer) error {
	if request == nil || request.Kind != value.Object {
		return nil
	}
	var status string
	_, err := c.opencodeCancelRequest(
		ctx,
		nativeText(request.Get("endpoint")),
		wakeProcessRef(request.Get("process")),
		nativeText(request.Get("session_id")),
		nativeText(request.Get("request_id")),
		nativeText(request.Get("expected_turn_id")),
	)
	var refused *wake.OpenCodeCancelRefused
	switch {
	case err == nil:
		status = "accepted"
	case errors.As(err, &refused):
		status = "refused:" + refused.Reason
	case wake.IsOpenCodeCancelUncertain(err) || wake.IsOpenCodeQueueError(err):
		status = "uncertain"
	default:
		return err
	}
	if err := func() error {
		lock, err := c.lockState(path)
		if err != nil {
			return err
		}
		defer func() { _ = lock.Close() }()
		state, err := c.read(path, SiteSessionRead)
		if err != nil {
			return err
		}
		if state == nil || state.Kind != value.Object {
			return &journal.NonObjectError{Field: "state"}
		}
		sub, err := nativeObj(state.Get("cancel_submission"), "cancel_submission")
		if err != nil {
			return err
		}
		matched := true
		for _, m := range request.Members {
			if !nativeEqual(sub.Get(string(m.Key)), m.Value) {
				matched = false
				break
			}
		}
		if matched {
			sub.Set("status", value.Text(status))
			state.Set("cancel_submission", sub)
			return c.writeState(path, state)
		}
		return nil
	}(); err != nil {
		return err
	}
	if status != "accepted" && errOut != nil {
		_, _ = fmt.Fprintf(errOut, "Cairn presence %s: native OpenCode request cancellation is %s; the inbox hold remains until cleanup is observed\n", nativeText(config.Get("binding")), status)
	}
	return nil
}
