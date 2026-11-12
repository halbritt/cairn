package coordination

import (
	"context"
	"errors"
	"fmt"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func (r *watchDrainRead) store(ctx context.Context) error {
	seen := map[string]bool{}
	for _, config := range r.configs {
		if err := r.inboxAndWakeFacts(ctx, config, nil, seen); err != nil {
			return err
		}
	}
	for _, session := range r.sessions {
		control, err := r.readView(ctx, session.config, "session-inbox-control", session.ref, nil)
		if err != nil {
			return err
		}
		if control == nil || control.Kind != value.Object || control.Get("attempt") == nil {
			return errors.New("D4 native hold visibility is unknown")
		}
		if !nativeNone(control.Get("attempt")) {
			return errors.New("D4 native inbox hold is not closed")
		}
		if err := r.historicalAttempt(ctx, session); err != nil {
			return err
		}
		if err := r.inboxAndWakeFacts(ctx, session.config, session.ref, seen); err != nil {
			return err
		}
	}
	return nil
}

func (r *watchDrainRead) readView(ctx context.Context, config *value.Value, op string, body, session *value.Value) (*value.Value, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	view, err := r.cli.call(ctx, config, op, body, NativeCallOptions{Session: session})
	if err != nil {
		return nil, fmt.Errorf("D4 %s visibility is unknown: %w", op, err)
	}
	key := nativeMap("cairn", config.Get("cairn"), "socket", config.Get("socket"), "token_file", config.Get("token_file"),
		"operation", op, "body", body, "session", session)
	identity, err := canonicalSortedJSONSHA256(key)
	if err != nil {
		return nil, err
	}
	if err := r.record("store:"+identity, view); err != nil {
		return nil, err
	}
	return view, nil
}

func (r *watchDrainRead) inboxAndWakeFacts(ctx context.Context, config, session *value.Value, seen map[string]bool) error {
	identity, err := canonicalSortedJSONSHA256(nativeMap("cairn", config.Get("cairn"), "socket", config.Get("socket"),
		"token_file", config.Get("token_file"), "repo", config.Get("repo"), "session", session))
	if err != nil {
		return err
	}
	if seen[identity] {
		return nil
	}
	seen[identity] = true
	wakes, err := r.readView(ctx, config, "wake-attempts", nativeMap("repo", config.Get("repo"), "active", true, "limit", 100), session)
	if err != nil {
		return err
	}
	if wakes == nil || wakes.Kind != value.Object || wakes.Get("attempts") == nil || wakes.Get("attempts").Kind != value.Array ||
		wakes.Get("more") == nil || wakes.Get("more").Kind != value.Bool {
		return errors.New("D4 wake coverage is unknown")
	}
	if len(wakes.Get("attempts").Items) != 0 {
		return errors.New("D4 active wake attempt blocks transition")
	}
	if wakes.Get("more").Boolean {
		return errors.New("D4 incomplete wake visibility")
	}
	return r.inboxFacts(ctx, config, session)
}

func (r *watchDrainRead) inboxFacts(ctx context.Context, config, session *value.Value) error {
	cursor := ""
	seen := map[string]bool{}
	for {
		page, err := r.readView(ctx, config, "event-watch", nativeMap("repo", config.Get("repo"), "cursor", cursor, "limit", 100), session)
		if err != nil {
			return err
		}
		if page == nil || page.Kind != value.Object || page.Get("deliveries") == nil || page.Get("deliveries").Kind != value.Array ||
			page.Get("more") == nil || page.Get("more").Kind != value.Bool || page.Get("cursor") == nil || page.Get("cursor").Kind != value.String {
			return errors.New("D4 delivery coverage is unknown")
		}
		for _, arrival := range page.Get("deliveries").Items {
			if arrival == nil || arrival.Kind != value.Object || !value.Truth(arrival.Get("delivery_id")) {
				return errors.New("D4 unknown delivery identity")
			}
			switch nativeText(arrival.Get("state")) {
			case "pending", "handled", "ignored", "failed":
			case "leased":
				// The read feed has no lease deadline. Expiry is not inferred.
				return errors.New("D4 leased delivery blocks; live/expired hold disposition is unknown")
			default:
				return errors.New("D4 delivery state is unknown")
			}
		}
		if !page.Get("more").Boolean {
			return nil
		}
		cursor = nativeText(page.Get("cursor"))
		if cursor == "" || seen[cursor] {
			return errors.New("D4 delivery pagination is incomplete")
		}
		seen[cursor] = true
	}
}
