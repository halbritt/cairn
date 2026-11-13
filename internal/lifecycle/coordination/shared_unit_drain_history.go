package coordination

import (
	"context"
	"errors"
	"path/filepath"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// Historical attempt status uses the existing authenticated host client.
// An older API refusing the operation blocks the
// read; there is no null-as-terminal fallback or local settlement.
func (r *watchDrainRead) historicalAttempt(ctx context.Context, session watchDrainSession) error {
	local := session.state.Get("inbox_attempt")
	intent, close := session.state.Get("inbox_intent"), session.state.Get("inbox_close")
	retained := false
	for _, key := range AttemptKeys {
		retained = retained || !nativeNone(session.state.Get(key))
	}
	if !retained {
		return nil
	}
	if local == nil || local.Kind != value.Object || !value.Truth(local.Get("attempt_id")) {
		return errors.New("D2 exact retained attempt identity is unknown")
	}
	remote, err := r.readView(ctx, session.config, "session-inbox-attempt-status", nativeMap(
		"session", session.ref, "attempt_id", local.Get("attempt_id")), nil)
	if err != nil {
		return err
	}
	if remote == nil || remote.Kind != value.Object || !nativeEqual(remote.Get("attempt_id"), local.Get("attempt_id")) ||
		!nativeEqual(remote.Get("session"), local.Get("session")) || !nativeEqual(remote.Get("session").Get("agent_id"), session.ref.Get("agent_id")) {
		return errors.New("D2 historical attempt/session correspondence is unknown")
	}
	if _, err := time.Parse(time.RFC3339Nano, nativeText(remote.Get("finished_at"))); err != nil {
		return errors.New("D2 authoritative attempt is unfinished or unknown")
	}
	if !value.Truth(remote.Get("reason")) {
		return errors.New("D2 authoritative close reason is unknown")
	}
	for _, key := range []string{"native_turn_id", "batch_root", "batch_index", "batch_limit", "admission_kind", "organic_root"} {
		if retained := local.Get(key); !nativeNone(retained) && !nativeEqual(retained, remote.Get(key)) {
			return errors.New("D2 historical native/batch ownership correspondence is unknown")
		}
	}
	if turn := intent.Get("native_turn_id"); !nativeNone(turn) && !nativeEqual(turn, remote.Get("native_turn_id")) {
		return errors.New("D2 retained intent native turn does not match the historical attempt")
	}
	if root := session.state.Get("organic_root"); !nativeNone(root) && !nativeEqual(root, remote.Get("organic_root")) {
		return errors.New("D2 retained organic root does not match the historical attempt")
	}
	if value.Truth(intent) && (!nativeEqual(intent.Get("request_id"), local.Get("attempt_id")) || !nativeEqual(intent.Get("session"), local.Get("session"))) {
		return errors.New("D2 retained intent does not match the closed attempt")
	}
	if value.Truth(close) && (!nativeEqual(close.Get("attempt_id"), local.Get("attempt_id")) ||
		!nativeEqual(close.Get("session"), local.Get("session")) || !nativeEqual(close.Get("reason"), remote.Get("reason")) || !value.Truth(close.Get("request_id"))) {
		return errors.New("D2 retained close does not match the authoritative close")
	}
	if err := r.historicalCompletion(session, local, remote); err != nil {
		return err
	}
	if r.settled == nil {
		r.settled = map[*value.Value]*value.Value{}
	}
	r.settled[session.state] = remote
	return nil
}

func (r *watchDrainRead) historicalCompletion(session watchDrainSession, local, remote *value.Value) error {
	delivery, claimed := remote.Get("delivery"), local.Get("delivery")
	if delivery == nil || claimed == nil || !nativeEqual(delivery.Get("delivery_id"), claimed.Get("delivery_id")) || !value.Truth(delivery.Get("delivery_id")) {
		return errors.New("D2 authoritative delivery correspondence is unknown")
	}
	switch nativeText(delivery.Get("state")) {
	case "handled", "ignored", "failed":
	default:
		return errors.New("D2 authoritative delivery is not terminal")
	}
	path := filepath.Join(nativeText(session.config.Get("state_dir")), "intents", nativeText(local.Get("attempt_id"))+".json")
	intent, err := r.file(path)
	if err != nil {
		return err
	}
	if !nativeEqual(intent.Get("session"), remote.Get("session")) || !nativeEqual(intent.Get("delivery_id"), delivery.Get("delivery_id")) {
		return errors.New("D2 journal does not match the historical delivery/session")
	}
	completion := intent.Get("completion")
	if id := session.state.Get("inbox_completion"); !nativeNone(id) &&
		(id.Kind != value.String || !nativeEqual(id, completion.Get("request_id"))) {
		return errors.New("D2 retained completion retry identity does not match the journal")
	}
	if id := session.state.Get("inbox_response"); !nativeNone(id) && value.SameText(intent.Get("kind"), "request") {
		if id.Kind != value.String || !nativeEqual(id, intent.Get("finish_reply").Get("request_id")) ||
			!nativeEqual(id, intent.Get("response").Get("request_id")) {
			return errors.New("D2 retained response retry identity does not match the settled reply")
		}
	}
	if policy := session.state.Get("inbox_result_policy"); !nativeNone(policy) {
		if !nativeEqual(policy.Get("claim_request_id"), local.Get("attempt_id")) {
			return errors.New("D2 retained result policy does not match the historical attempt")
		}
		if _, err := CompletionScope(session.state, local); err != nil {
			return errors.Join(errors.New("D2 retained result policy is unknown"), err)
		}
	}
	if completion != nil && value.SameText(completion.Get("kind"), "complete") && value.SameText(completion.Get("status"), "committed") {
		if nativeNone(delivery.Get("result")) || !nativeEqual(completion.Get("result"), delivery.Get("result")) {
			return errors.New("D2 exact completion result does not match the authoritative delivery")
		}
	}
	if err := historicalCancellation(remote); err != nil {
		return err
	}
	return historicalLocalCleanup(session.state, remote)
}

func historicalCancellation(attempt *value.Value) error {
	if value.Truth(attempt.Get("capture_gap")) ||
		(!nativeNone(attempt.Get("capture_state")) && !value.SameText(attempt.Get("capture_state"), "complete")) {
		return errors.New("D2 authoritative capture coverage is unknown")
	}
	if scan := attempt.Get("terminal_scan"); !nativeNone(scan) && !value.SameText(scan, "clear") {
		return errors.New("D2 authoritative terminal scan is unknown")
	}
	if stop := attempt.Get("turn_stop_state"); !nativeNone(stop) && !value.SameText(stop, "ended") && !value.SameText(stop, "interrupted") {
		return errors.New("D2 authoritative turn stop is unknown")
	}
	if !nativeNone(attempt.Get("cancel")) {
		if !value.Truth(attempt.Get("cancel").Get("confirmed_at")) || !value.SameText(attempt.Get("terminal_scan"), "clear") {
			return errors.New("D2 cancellation/capture cleanup is unknown")
		}
		stop := nativeText(attempt.Get("turn_stop_state"))
		if stop != "ended" && stop != "interrupted" {
			return errors.New("D2 cancellation has no positive turn stop")
		}
	}
	if tools := attempt.Get("tools"); tools != nil {
		if tools.Kind != value.Array {
			return errors.New("D2 captured tool coverage is unknown")
		}
		for _, tool := range tools.Items {
			state := nativeText(tool.Get("stop_state"))
			if state != "terminated" && state != "unavailable" {
				return errors.New("D2 captured tool is not terminal")
			}
		}
		if len(tools.Items) > 0 && !value.SameText(attempt.Get("terminal_scan"), "clear") {
			return errors.New("D2 captured tool terminal scan is unknown")
		}
	}
	return nil
}
