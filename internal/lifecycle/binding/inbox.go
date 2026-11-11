package binding

import (
	"context"
	"errors"

	"github.com/halbritt/cairn/internal/lifecycle/admission"
	"github.com/halbritt/cairn/internal/lifecycle/ledger"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

type InboxInputs struct {
	Config, Memory, Event *value.Value
}

func IsInboxWake(event *value.Value) bool {
	return value.SameText(event.Get("hook_event_name"), "UserPromptSubmit") && reservedWake(event.Get("prompt"))
}

// ResolveInbox checks the same /3 code/configuration selection as activation,
// but consumes an existing decision rather than invoking DecideActivation.
func ResolveInbox(ctx context.Context, in admission.HookInvocation) (*InboxInputs, error) {
	if in.EntryPoint != "coordination" || !validDigest(in.ConfigSHA256) || !validDigest(in.BindingSHA256) {
		return nil, errors.New("checked inbox parent observation required")
	}
	// The parent already ran admission and activation exactly once. Recheck
	// code/configuration pins here, without rerunning admission: a newly added
	// no-memory marker belongs to output's retained privacy early return.
	checked, err := ResolveInboxReport(ctx, in)
	if err != nil {
		return nil, err
	}
	if len(in.Event) > inputLimit {
		return nil, errors.New("hook event exceeds limit")
	}
	event, err := value.DecodeBytes(in.Event, 0)
	if err != nil {
		return nil, err
	}
	checked.Event = event
	if !IsInboxWake(event) {
		return checked, nil
	}
	session, turn := event.Get("session_id"), event.Get("turn_id")
	if value.SameText(checked.Config.Get("harness"), "claude") {
		turn, err = claudeOwner(event.Get("prompt_id"))
		if err != nil {
			return nil, err
		}
	}
	if !canonicalUUID(session) || (value.SameText(checked.Config.Get("harness"), "codex") && !canonicalUUID(turn)) {
		// The Source first activation does not cache malformed native identity;
		// its delivery guard, rather than a fabricated decision, refuses it.
		return checked, nil
	}
	enabled, err := ledger.ResolvedInboxActivation(checked.Memory, string(checked.Config.Get("harness").String), string(session.String), string(turn.String))
	if err != nil {
		return nil, err
	}
	if !enabled {
		return nil, errors.New("checked enabled inbox selection required")
	}
	return checked, nil
}
