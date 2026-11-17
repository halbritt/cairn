package native

import (
	"context"
	"errors"
	"fmt"

	"github.com/halbritt/cairn/internal/lifecycle/install"
)

type PublicationResult struct {
	Exchange        install.SettingsExchangeResult `json:"exchange"`
	Owner           Observation                    `json:"owner"`
	PendingRecovery bool                           `json:"pending_recovery"`
}

func (o *Owner) changePublication(ctx context.Context, expected uint64, operation string) (out PublicationResult, err error) {
	if o == nil {
		return out, errors.New("native publication owner unavailable")
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.origin != o || o.closed || expected == 0 || expected != o.generation || o.client != nil || o.material == nil {
		return out, errors.New("native publication owner/selection is missing, busy or stale")
	}
	if o.pendingMutation && operation != "recover" {
		return out, errors.New("native recovery must finish before another mutation")
	}
	if operation == "recover" {
		if !o.pendingMutation || o.exchange == nil {
			return out, errors.New("native publication has no pending live recovery")
		}
		if _, err := install.InspectSettingsExchange(ctx, o.exchange.Recovery()); err != nil {
			return out, err
		}
		current, err := o.exchange.Snapshot(ctx)
		if err != nil {
			return out, err
		}
		// A pure pinned-record read can recognize our own committed rename
		// after a failed readback. It never blesses a drifted/unknown target.
		o.settings = current
	}
	guard := func(ctx context.Context) error {
		observation := o.readQuiescenceLocked(ctx)
		if observation.Verdict != Quiescent {
			return fmt.Errorf("native publication refused (%s): %s", observation.Verdict, observation.Reason)
		}
		return nil
	}
	if err := guard(ctx); err != nil {
		return out, err
	}
	if operation == "publish" && o.exchange == nil {
		raw, err := install.PatchSettingsHooks(o.settings.Bytes(), o.material.prepared.Bytes())
		if err != nil {
			return out, err
		}
		o.pendingMutation = true
		o.exchange, err = install.PrepareSettingsExchange(ctx, o.settings, raw)
		if err != nil {
			out.PendingRecovery = true
			out.Owner = o.inspectLocked(ctx)
			return out, err
		}
	}
	if o.exchange == nil {
		return out, errors.New("native inverse/recovery has no live exchange")
	}
	switch operation {
	case "publish":
		out.Exchange, err = o.exchange.Publish(ctx, guard)
	case "inverse":
		out.Exchange, err = o.exchange.Reverse(ctx, guard)
	case "recover":
		out.Exchange, err = o.exchange.Resume(ctx, guard)
	default:
		return out, errors.New("unsupported native publication operation")
	}
	inspection, inspectErr := install.InspectSettingsExchange(ctx, o.exchange.Recovery())
	if inspectErr == nil {
		current, currentErr := o.exchange.Snapshot(ctx)
		if currentErr == nil {
			o.settings = current
			out.Exchange = inspection
		} else {
			err = errors.Join(err, currentErr)
		}
	} else {
		err = errors.Join(err, inspectErr)
	}
	o.pendingMutation = err != nil
	o.generation++
	out.Owner = o.inspectLocked(ctx)
	out.PendingRecovery = o.pendingMutation
	return out, err
}
