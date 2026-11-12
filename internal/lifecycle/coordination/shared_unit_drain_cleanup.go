package coordination

import (
	"errors"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// Retained stop/capture requests are not active merely because their keys
// survive. They need positive correspondence with the authoritative terminal
// attempt, including every captured tool; an unfinished report stays unknown.
func historicalLocalCleanup(state, remote *value.Value) error {
	for _, key := range []string{"cancel_turn_end", "cancel_scan_report", "cancel_final_report"} {
		request := state.Get(key)
		if nativeNone(request) {
			continue
		}
		if request.Kind != value.Object || !canonicalUUIDValue(request.Get("request_id")) ||
			!nativeEqual(request.Get("session"), remote.Get("session")) ||
			!nativeEqual(request.Get("attempt_id"), remote.Get("attempt_id")) {
			return errors.New("D2 retained stop report correspondence is unknown")
		}
		for field, status := range map[string]string{"turn_stop": "turn_stop_state", "capture_state": "capture_state", "terminal_scan": "terminal_scan"} {
			if reported := request.Get(field); !nativeNone(reported) && !nativeEqual(reported, remote.Get(status)) {
				return errors.New("D2 retained stop report is not authoritatively settled")
			}
		}
		tools := request.Get("tools")
		if tools == nil || tools.Kind != value.Array {
			return errors.New("D2 retained stop tool coverage is unknown")
		}
		for _, tool := range tools.Items {
			actual := historicalTool(remote, nativeText(tool.Get("item_id")))
			if actual == nil || !nativeEqual(tool.Get("stop_state"), actual.Get("stop_state")) {
				return errors.New("D2 retained tool stop does not match authoritative cleanup")
			}
		}
	}
	calls := state.Get("tool_calls")
	if nativeNone(calls) {
		return nil
	}
	if calls.Kind != value.Object {
		return errors.New("D2 local tool capture coverage is unknown")
	}
	for _, call := range calls.Members {
		capture := call.Value.Get("capture")
		if !nativeEqual(call.Value.Get("attempt_id"), remote.Get("attempt_id")) ||
			!canonicalUUIDValue(capture.Get("request_id")) || !nativeEqual(capture.Get("session"), remote.Get("session")) ||
			!nativeEqual(capture.Get("attempt_id"), remote.Get("attempt_id")) {
			return errors.New("D2 local captured tool attempt is unknown")
		}
		items := capture.Get("items")
		if items == nil || items.Kind != value.Array || len(items.Items) != 1 || !value.SameText(items.Items[0].Get("item_id"), string(call.Key)) {
			return errors.New("D2 local captured tool identity is unknown")
		}
		item, actual := items.Items[0], historicalTool(remote, string(call.Key))
		if actual == nil || !nativeEqual(item.Get("process_id"), actual.Get("process_id")) ||
			!nativeEqual(item.Get("command"), actual.Get("command")) {
			return errors.New("D2 local captured tool has no authoritative terminal correspondence")
		}
	}
	return nil
}

func historicalTool(attempt *value.Value, id string) *value.Value {
	tools := attempt.Get("tools")
	if id == "" || tools == nil || tools.Kind != value.Array {
		return nil
	}
	for _, tool := range tools.Items {
		if value.SameText(tool.Get("item_id"), id) {
			return tool
		}
	}
	return nil
}
