package coordination

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func (c *CLI) fitsCompactInline(text string) (bool, error) {
	if len(text) > 4096 {
		return false, nil
	}
	if c.Compatibility == nil {
		return true, nil
	}
	var buf bytes.Buffer
	yes, no := true, false
	if err := value.WriteKernel(&buf, value.Text(text), value.WriterRoute{
		Route:       "dumps",
		Codec:       &value.Codec{Name: "utf-8", Errors: "strict"},
		EnsureASCII: &yes,
		Compact:     &no,
		TrailingLF:  &no,
	}, 0); err != nil {
		return false, err
	}
	return buf.Len() <= 4096, nil
}

func formatDigestItemHeader(ordStr, topicKey string, ev *value.Value) (string, error) {
	source, err := nativeRequired(ev, "ref")
	if err != nil {
		return "", err
	}
	if source.Kind != value.Object {
		return "", &journal.NonObjectError{Field: "ref"}
	}
	recID, err := nativeRequired(source, "record_id")
	if err != nil {
		return "", err
	}
	ver, err := nativeRequired(source, "version")
	if err != nil {
		return "", err
	}
	header := fmt.Sprintf("[#%s] Cairn notice from %s; exact source %s version %s.",
		ordStr, nativeText(ev.Get("from")), nativeText(recID), sourceScalarText(ver))
	var parts []string
	for _, k := range []string{"action", "supersedes", "expect_next_by"} {
		if v := ev.Get(k); value.Truth(v) {
			parts = append(parts, k+"="+nativeText(v))
		}
	}
	if topicKey != "" && !value.Truth(ev.Get("supersedes")) {
		parts = append(parts, "topic="+topicKey)
	}
	if len(parts) > 0 {
		header += " Intent: " + strings.Join(parts, ", ") + "."
	}
	return header, nil
}

func (c *CLI) renderDigestAttemptContext(ctx context.Context, config *value.Value, target string, native, attempt *value.Value) (*value.Value, error) {
	itemsVal := attempt.Get("digest_items")
	if itemsVal == nil || itemsVal.Kind != value.Array || len(itemsVal.Items) == 0 {
		return nil, nativeRefusal("INVALID_HOST", "digest attempt has no items")
	}
	handle := nativeText(native.Get("compact_handle"))
	supCount := sourceScalarText(nativeOr(attempt.Get("digest_superseded_count"), value.IntegerText("0")))
	if supCount == "" {
		supCount = "0"
	}
	var distinctSenders, itemHeaders, bodyBlocks, unavailOrds []string
	seenSenders := map[string]bool{}
	for _, it := range itemsVal.Items {
		if it == nil || it.Kind != value.Object {
			return nil, &journal.NonObjectError{Field: "digest_item"}
		}
		ordStr := sourceScalarText(it.Get("ordinal"))
		topicKey := nativeText(it.Get("topic_key"))
		deliv, err := nativeObj(it.Get("delivery"), "delivery")
		if err != nil {
			return nil, err
		}
		ev, err := nativeObj(deliv.Get("event"), "event")
		if err != nil {
			return nil, err
		}
		sender := nativeText(ev.Get("from"))
		if !seenSenders[sender] {
			seenSenders[sender] = true
			distinctSenders = append(distinctSenders, sender)
		}
		hdr, err := formatDigestItemHeader(ordStr, topicKey, ev)
		if err != nil {
			return nil, err
		}
		bodyVal, err := c.exactInboxSource(ctx, config, attempt, ev.Get("ref"))
		if err != nil {
			var ce *CoordinationError
			if !errors.As(err, &ce) || !nativeContains([]string{"SOURCE_UNAVAILABLE", "PAYLOAD_UNAVAILABLE", "NOT_FOUND"}, ce.Code) {
				return nil, err
			}
			unavailOrds = append(unavailOrds, ordStr)
			itemHeaders = append(itemHeaders, hdr+fmt.Sprintf(" Source unavailable (%s).", ce.Code))
			continue
		}
		bodyText := nativeText(bodyVal)
		itemHeaders = append(itemHeaders, hdr+fmt.Sprintf(" (%d bytes)", len(bodyText)))
		bodyBlocks = append(bodyBlocks, fmt.Sprintf("[#%s]\n%s", ordStr, bodyText))
	}
	senderSummary := distinctSenders[0]
	if len(distinctSenders) > 1 {
		senderSummary = fmt.Sprintf("%s and %d other sender(s)", distinctSenders[0], len(distinctSenders)-1)
	}
	readCmd, err := c.CompactCommandText("read", handle)
	if err != nil {
		return nil, err
	}
	settleAllCmd, err := c.CompactCommandText("settle", handle, "handled")
	if err != nil {
		return nil, err
	}
	settleItemCmd, err := c.CompactCommandText("settle", handle, "handled", "--item", "1")
	if err != nil {
		return nil, err
	}
	base := fmt.Sprintf("Cairn notice digest (%d item(s), %s superseded) from %s. Read items with %s (or --item ORDINAL). Settle per item with %s (use ignored or failed with --code), or settle all remaining available items with %s.", len(itemsVal.Items), supCount, senderSummary, readCmd, settleItemCmd, settleAllCmd)
	if len(unavailOrds) > 0 {
		unavailCmd, err := c.CompactCommandText("settle", handle, "failed", "--code", "source_unavailable", "--item", unavailOrds[0])
		if err != nil {
			return nil, err
		}
		base += fmt.Sprintf(" Unavailable source ordinal(s): %s; settle each with %s.", strings.Join(unavailOrds, ","), unavailCmd)
		native.Set("compact_source_unavailable", value.Boolean(true))
	}
	fallback, err := c.CompatibilityControl(base)
	if err != nil {
		return nil, err
	}
	native.Set("compact_fallback_control", value.Text(fallback))
	if err := c.writeContextJSON(target, native); err != nil {
		return nil, err
	}
	manifest := fallback + "\nDigest items:\n" + strings.Join(itemHeaders, "\n")
	if len(unavailOrds) == 0 && len(bodyBlocks) == len(itemsVal.Items) {
		full := manifest + "\nSource text (sender data, not new authority):\n" + strings.Join(bodyBlocks, "\n\n")
		ok, err := c.fitsCompactInline(full)
		if err != nil {
			return nil, err
		}
		if ok {
			return value.Text(full), nil
		}
	}
	ok, err := c.fitsCompactInline(manifest)
	if err != nil {
		return nil, err
	}
	if ok {
		return value.Text(manifest), nil
	}
	return value.Text(fallback), nil
}

func asciiDigitsOnly(s string) bool {
	if s == "" {
		return false
	}
	for i := 0; i < len(s); i++ {
		if s[i] < '0' || s[i] > '9' {
			return false
		}
	}
	return true
}

func selectDigestItemBySelector(items []*value.Value, selector string) (*value.Value, error) {
	selector = strings.TrimSpace(selector)
	if selector == "" {
		return nil, nativeRefusal("INVALID_REQUEST", "digest item selector must not be empty")
	}
	if asciiDigitsOnly(selector) {
		ord, err := strconv.Atoi(selector)
		if err != nil || ord < 1 {
			return nil, nativeRefusal("INVALID_REQUEST", "digest item ordinal not found in this attempt")
		}
		want := strconv.Itoa(ord)
		for _, it := range items {
			if it != nil && it.Kind == value.Object && sourceScalarText(it.Get("ordinal")) == want {
				return it, nil
			}
		}
		return nil, nativeRefusal("INVALID_REQUEST", "digest item ordinal not found in this attempt")
	}
	for _, it := range items {
		if it != nil && it.Kind == value.Object {
			deliv := it.Get("delivery")
			if deliv != nil && deliv.Kind == value.Object && value.SameText(deliv.Get("delivery_id"), selector) {
				return it, nil
			}
		}
	}
	return nil, nativeRefusal("INVALID_REQUEST", "digest item not found in this attempt")
}

func overlayDigestStatusItems(localItems, statusItems *value.Value) *value.Value {
	if localItems == nil || localItems.Kind != value.Array || statusItems == nil || statusItems.Kind != value.Array {
		return localItems
	}
	byID := map[string]*value.Value{}
	for _, stIt := range statusItems.Items {
		if stIt != nil && stIt.Kind == value.Object {
			d := stIt.Get("delivery")
			if d != nil && d.Kind == value.Object {
				byID[nativeText(d.Get("delivery_id"))] = d
			}
		}
	}
	out := nativeClone(localItems)
	for _, locIt := range out.Items {
		if locIt == nil || locIt.Kind != value.Object {
			continue
		}
		ld := locIt.Get("delivery")
		sd := byID[nativeText(ld.Get("delivery_id"))]
		if ld != nil && ld.Kind == value.Object && sd != nil {
			for _, k := range []string{"state", "code", "control", "completed_at", "completed_after_lease", "attempts"} {
				if v := sd.Get(k); v != nil {
					ld.Set(k, v)
				} else {
					nativePop(ld, k)
				}
			}
		}
	}
	return out
}

func (c *CLI) markDigestJournalCommitted(dir string, native, attempt, delivery *value.Value, fallbackReqID string) error {
	attemptID, err := requiredText(attempt, "attempt_id")
	if err != nil {
		return err
	}
	compID := fallbackReqID
	if ids := native.Get("request_ids"); ids != nil && ids.Kind == value.Object && value.Truth(ids.Get("completion")) {
		compID = nativeText(ids.Get("completion"))
	}
	intentPath := filepath.Join(dir, "intents", attemptID+".json")
	rec := nativeMap(
		"schema", "cairn.inbox-intent/1",
		"attempt_id", attempt.Get("attempt_id"),
		"delivery_id", delivery.Get("delivery_id"),
		"kind", "notice",
		"session", attempt.Get("session"),
		"completion", nativeMap("kind", "ack", "request_id", compID, "status", "committed", "code", "OK"),
	)
	return c.writeState(intentPath, rec)
}

func (c *CLI) compactDigestOperation(ctx context.Context, config *value.Value, dir, handle, op, disposition, code, itemSelector string, native, attempt, delivery *value.Value, out io.Writer) (int, error) {
	if !value.SameText(native.Get("admission_kind"), "digest") {
		return 0, nativeRefusal("INVALID_HOST", "compact inbox delivery changed")
	}
	if op == "advance" {
		return 0, nativeRefusal("UNSUPPORTED_CONTROL", "notice batching is unavailable for a notice digest")
	}
	attID, err := nativeRequired(attempt, "attempt_id")
	if err != nil {
		return 0, err
	}
	session, err := nativeRequired(attempt, "session")
	if err != nil {
		return 0, err
	}
	itemsVal := attempt.Get("digest_items")
	statusFinished := false
	renewed, renewErr := c.Host.Call(ctx, config, "session-inbox-digest-renew", nativeMap("session", session, "attempt_id", attID, "lease_seconds", 90), NativeCallOptions{})
	if renewErr != nil {
		var ce *CoordinationError
		if !errors.As(renewErr, &ce) || ce.Code != "STALE_LEASE" {
			return 0, renewErr
		}
		stAtt, stErr := c.Host.Call(ctx, config, "session-inbox-attempt-status", nativeMap("session", session, "attempt_id", attID), NativeCallOptions{})
		if stErr != nil || stAtt == nil || stAtt.Kind != value.Object || !nativeEqual(stAtt.Get("attempt_id"), attID) {
			return 0, renewErr
		}
		if value.Truth(stAtt.Get("finished_at")) {
			if op != "settle" {
				return 0, renewErr
			}
			statusFinished = true
		}
		itemsVal = overlayDigestStatusItems(itemsVal, stAtt.Get("digest_items"))
	} else {
		if renewed == nil || renewed.Kind != value.Object || !nativeEqual(renewed.Get("attempt_id"), attID) || value.Truth(renewed.Get("finished_at")) {
			return 0, nativeRefusal("STALE_LEASE", "compact inbox digest is no longer leased")
		}
		if di := renewed.Get("digest_items"); di != nil && di.Kind == value.Array {
			itemsVal = di
		}
	}
	if itemsVal == nil || itemsVal.Kind != value.Array || len(itemsVal.Items) == 0 {
		return 0, nativeRefusal("INVALID_HOST", "digest attempt has no items")
	}
	if op == "inspect" {
		return 0, c.output(out, nativeMap("schema", "cairn.compact-inbox/1", "ok", true, "digest", true, "context", native), false)
	}
	if op == "read" {
		return c.compactDigestRead(ctx, config, attempt, itemsVal.Items, itemSelector, out)
	}
	return c.compactDigestSettle(ctx, config, dir, handle, disposition, code, itemSelector, native, attempt, delivery, session, attID, itemsVal.Items, statusFinished, out)
}

func (c *CLI) compactDigestRead(ctx context.Context, config, attempt *value.Value, items []*value.Value, itemSelector string, out io.Writer) (int, error) {
	if itemSelector != "" {
		it, err := selectDigestItemBySelector(items, itemSelector)
		if err != nil {
			return 0, err
		}
		deliv, err := nativeObj(it.Get("delivery"), "delivery")
		if err != nil {
			return 0, err
		}
		ev, err := nativeObj(deliv.Get("event"), "event")
		if err != nil {
			return 0, err
		}
		body, err := c.exactInboxSource(ctx, config, attempt, ev.Get("ref"))
		if err != nil {
			return 0, err
		}
		readOut := nativeMap("schema", "cairn.compact-inbox/1", "ok", true, "digest", true, "ordinal", it.Get("ordinal"), "delivery_id", deliv.Get("delivery_id"), "state", deliv.Get("state"), "kind", ev.Get("kind"), "sender", ev.Get("from"), "source", ev.Get("ref"), "body", body)
		if tk := it.Get("topic_key"); value.Truth(tk) {
			readOut.Set("topic_key", tk)
		}
		for _, k := range []string{"action", "supersedes", "expect_next_by"} {
			if v := ev.Get(k); value.Truth(v) {
				readOut.Set(k, v)
			}
		}
		return 0, c.output(out, readOut, false)
	}
	outItems := &value.Value{Kind: value.Array, Items: make([]*value.Value, 0, len(items))}
	for _, it := range items {
		deliv, err := nativeObj(it.Get("delivery"), "delivery")
		if err != nil {
			return 0, err
		}
		ev, err := nativeObj(deliv.Get("event"), "event")
		if err != nil {
			return 0, err
		}
		entry := nativeMap("ordinal", it.Get("ordinal"), "delivery_id", deliv.Get("delivery_id"), "state", deliv.Get("state"), "kind", ev.Get("kind"), "sender", ev.Get("from"), "source", ev.Get("ref"))
		if tk := it.Get("topic_key"); value.Truth(tk) {
			entry.Set("topic_key", tk)
		}
		for _, k := range []string{"action", "supersedes", "expect_next_by"} {
			if v := ev.Get(k); value.Truth(v) {
				entry.Set(k, v)
			}
		}
		body, err := c.exactInboxSource(ctx, config, attempt, ev.Get("ref"))
		if err != nil {
			var ce *CoordinationError
			if !errors.As(err, &ce) || !nativeContains([]string{"SOURCE_UNAVAILABLE", "PAYLOAD_UNAVAILABLE", "NOT_FOUND"}, ce.Code) {
				return 0, err
			}
			entry.Set("body", nil)
			entry.Set("source_error", value.Text(ce.Code))
		} else {
			entry.Set("body", body)
		}
		outItems.Items = append(outItems.Items, entry)
	}
	supCount := nativeOr(attempt.Get("digest_superseded_count"), value.IntegerText("0"))
	return 0, c.output(out, nativeMap("schema", "cairn.compact-inbox/1", "ok", true, "digest", true, "superseded_count", supCount, "items", outItems), false)
}

func (c *CLI) compactDigestSettle(ctx context.Context, config *value.Value, dir, handle, disposition, code, itemSelector string, native, attempt, delivery, session, attID *value.Value, items []*value.Value, statusFinished bool, out io.Writer) (int, error) {
	if disposition == "handled" && code != "" || disposition != "handled" && code == "" {
		return 0, nativeRefusal("INVALID_REQUEST", "ignored or failed acknowledgement needs a reason code")
	}
	var targets []*value.Value
	if itemSelector != "" {
		it, err := selectDigestItemBySelector(items, itemSelector)
		if err != nil {
			return 0, err
		}
		targets = []*value.Value{it}
	} else if statusFinished {
		for _, it := range items {
			deliv := it.Get("delivery")
			if nativeText(deliv.Get("state")) == disposition && nativeText(deliv.Get("code")) == code && nativeNone(deliv.Get("control")) {
				targets = append(targets, it)
			}
		}
		if len(targets) == 0 {
			targets = []*value.Value{items[0]}
		}
	} else {
		hasControlled := false
		for _, it := range items {
			deliv := it.Get("delivery")
			if !nativeNone(deliv.Get("control")) {
				hasControlled = true
			}
			st := nativeText(deliv.Get("state"))
			if st == "pending" || st == "leased" {
				targets = append(targets, it)
			}
		}
		if len(targets) == 0 {
			if hasControlled {
				targets = []*value.Value{items[0]}
			} else {
				for _, it := range items {
					deliv := it.Get("delivery")
					if nativeText(deliv.Get("state")) == disposition && nativeText(deliv.Get("code")) == code {
						targets = append(targets, it)
					}
				}
				if len(targets) == 0 {
					if disposition != "handled" {
						return 0, nativeRefusal("VERSION_CONFLICT", "digest items are already settled with a different disposition")
					}
					reqID := uuid.NewSHA1(uuid.NameSpaceURL, []byte("cairn.digest-settled/1:"+nativeText(attID))).String()
					if err := c.markDigestJournalCommitted(dir, native, attempt, delivery, reqID); err != nil {
						return 0, err
					}
					return 0, c.output(out, nativeMap("schema", "cairn.compact-inbox/1", "ok", true, "digest", true, "status", disposition, "settled_count", 0, "remaining", 0, "handle", handle), false)
				}
			}
		}
	}
	outcomes := &value.Value{Kind: value.Array, Items: make([]*value.Value, 0, len(targets))}
	ids := make([]string, 0, len(targets))
	var firstUnavailErr error
	for _, it := range targets {
		deliv, err := nativeObj(it.Get("delivery"), "delivery")
		if err != nil {
			return 0, err
		}
		ev, err := nativeObj(deliv.Get("event"), "event")
		if err != nil {
			return 0, err
		}
		if !(disposition == "failed" && code == "source_unavailable") {
			if _, err := c.exactInboxSource(ctx, config, attempt, ev.Get("ref")); err != nil {
				var ce *CoordinationError
				if itemSelector == "" && errors.As(err, &ce) && nativeContains([]string{"SOURCE_UNAVAILABLE", "PAYLOAD_UNAVAILABLE", "NOT_FOUND"}, ce.Code) {
					if firstUnavailErr == nil {
						firstUnavailErr = err
					}
					continue
				}
				return 0, err
			}
		}
		dID := nativeText(deliv.Get("delivery_id"))
		ids = append(ids, dID)
		oc := nativeMap("delivery_id", deliv.Get("delivery_id"), "lease_id", deliv.Get("lease_id"), "disposition", disposition)
		if code != "" {
			oc.Set("code", value.Text(code))
		}
		outcomes.Items = append(outcomes.Items, oc)
	}
	if len(outcomes.Items) == 0 && firstUnavailErr != nil {
		return 0, firstUnavailErr
	}
	sort.Strings(ids)
	reqID := uuid.NewSHA1(uuid.NameSpaceURL, []byte("cairn.digest-settle/1:"+nativeText(attID)+":"+disposition+":"+code+":"+strings.Join(ids, ","))).String()
	res, err := c.Host.Call(ctx, config, "session-inbox-digest-settle", nativeMap("request_id", reqID, "session", session, "attempt_id", attID, "outcomes", outcomes), NativeCallOptions{})
	if err != nil {
		return 0, err
	}
	if res == nil || res.Kind != value.Object {
		return 0, &journal.NonObjectError{Field: "digest settlement reply"}
	}
	remVal := res.Get("remaining")
	if remVal != nil && remVal.Kind == value.Int && remVal.Integer == "0" {
		if err := c.markDigestJournalCommitted(dir, native, attempt, delivery, reqID); err != nil {
			return 0, err
		}
	}
	settledItems := res.Get("items")
	settledCount := 0
	if settledItems != nil && settledItems.Kind == value.Array {
		settledCount = len(settledItems.Items)
	}
	return 0, c.output(out, nativeMap("schema", "cairn.compact-inbox/1", "ok", true, "digest", true, "status", disposition, "settled_count", settledCount, "remaining", remVal, "items", settledItems, "handle", handle), false)
}
