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
	checked, err := checkInvocation(ctx, in, func(config, manifest, event *value.Value, enabled bool) (bool, error) {
		if !value.SameText(event.Get("hook_event_name"), "UserPromptSubmit") || !reservedWake(event.Get("prompt")) {
			return enabled, nil
		}
		session, turn := event.Get("session_id"), event.Get("turn_id")
		if value.SameText(config.Get("harness"), "claude") {
			var err error
			turn, err = claudeOwner(event.Get("prompt_id"))
			if err != nil {
				return false, err
			}
		}
		if !canonicalUUID(session) || (value.SameText(config.Get("harness"), "codex") && !canonicalUUID(turn)) {
			// Source's first activation does not cache malformed native identity;
			// its real delivery guard, not a fabricated decision, refuses it.
			return enabled, nil
		}
		d, err := descriptor(manifest.Get("memory_config"))
		if err != nil {
			return false, err
		}
		memory, err := checkedConfig(d, "", nil)
		if err != nil {
			return false, err
		}
		return ledger.ResolvedInboxActivation(memory, string(config.Get("harness").String), string(session.String), string(turn.String))
	})
	if err != nil {
		return nil, err
	}
	if checked.Disposition != "checked" {
		return nil, errors.New("checked enabled inbox selection required")
	}
	receipt, err := readCompatibilityReceipt(in.ReceiptPath, in.ReceiptSHA256)
	if err != nil {
		return nil, err
	}
	memory, err := checkedConfig(receipt.MemoryConfig, "", nil)
	if err != nil {
		return nil, err
	}
	return &InboxInputs{Config: checked.Config, Memory: memory, Event: checked.Event}, nil
}
