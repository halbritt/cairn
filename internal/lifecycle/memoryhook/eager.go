package memoryhook

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"regexp"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/indexview"
	"github.com/halbritt/cairn/internal/lifecycle/memoryclient"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

var bodyHash = regexp.MustCompile(`^[0-9a-f]{64}$`)

func clone(v *value.Value) *value.Value {
	if v == nil {
		return nil
	}
	copied := *v
	copied.String = append([]rune(nil), v.String...)
	copied.Items = make([]*value.Value, len(v.Items))
	for i, item := range v.Items {
		copied.Items[i] = clone(item)
	}
	copied.Members = make([]value.Member, len(v.Members))
	for i, member := range v.Members {
		copied.Members[i] = value.Member{Key: append([]rune(nil), member.Key...), Value: clone(member.Value)}
	}
	return &copied
}
func hashText(s string) string    { sum := sha256.Sum256([]byte(s)); return hex.EncodeToString(sum[:]) }
func contextError(err error) bool { var refused *contextRefused; return errors.As(err, &refused) }
func pullBudget(err error) bool   { return core.Code(err) == "BUDGET_REFUSED" }
func unicodeFault(err error) bool {
	var source *value.Error
	return errors.As(err, &source) && source.Class == "UnicodeEncodeError"
}

func checkedWhole(group []*value.Value, response *value.Value) error {
	if span := response.Get("span"); span != nil && span.Kind != value.Null {
		return fail("whole competing candidate identity changed")
	}
	selections := []*value.Value{response.Get("selection")}
	if competing := response.Get("competing"); competing != nil {
		selections = append(selections, competing.Items...)
	}
	expected := map[string]*value.Value{}
	for _, entry := range group {
		expected[candidateKey(entry)] = entry
	}
	actual := map[string]bool{}
	for _, selection := range selections {
		record := selection.Get("record")
		if record == nil || record.Get("version") == nil {
			return fail("whole competing candidate identity changed")
		}
		key := candidateKey(record)
		entry := expected[key]
		if entry == nil || actual[key] {
			return fail("whole competing candidate identity changed")
		}
		actual[key] = true
	}
	if len(selections) != len(expected) || len(actual) != len(expected) {
		return fail("whole competing candidate identity changed")
	}
	for _, selection := range selections {
		record := selection.Get("record")
		entry := expected[candidateKey(record)]
		body := record.Get("body")
		if body == nil || body.Kind != value.String {
			return fail("whole competing candidate identity changed")
		}
		if !validText(body) {
			return &value.Error{Class: "UnicodeEncodeError", Message: "optional body has invalid Unicode"}
		}
		if hashText(text(body)) != text(entry.Get("body_sha256")) {
			return fail("whole competing candidate identity changed")
		}
	}
	return nil
}

// eagerCandidates consumes current receipts before emitting advisory bodies.
// Optional read failures remain visible in bounded bookkeeping, with no refund.
func eagerCandidates(ctx context.Context, client *memoryclient.Client, config, selected, result *value.Value, typed []indexview.Entry, budget int, status *value.Value, measure func(string) int, observers ...candidateObserver) (string, error) {
	entries := result.Get("index")
	anyHash := false
	for _, entry := range entries.Items {
		anyHash = anyHash || bodyHash.MatchString(text(entry.Get("body_sha256")))
	}
	if !anyHash {
		return renderCandidates(config, selected, result, budget, status, measure, nil, observers...)
	}
	inspection := value.Mapping(value.Field("pull_calls", integer(0)), value.Field("remaining_pull_calls", integer(4)), value.Field("delivered_records", integer(0)), value.Field("refusals", value.Mapping()))
	progress := &candidateProgress{inspection: inspection}
	render := func(p *candidateProgress, target *value.Value) (string, error) {
		return renderCandidates(config, selected, result, budget, target, measure, p, observers...)
	}
	body, err := render(progress, status)
	if err != nil || !value.SameText(status.Get("outcome"), "delegated") {
		return body, err
	}
	if allowance := number(status.Get("native_allowance_bytes")); allowance < budget/2 || allowance == budget/2 && !value.Truth(status.Get("candidate_previews")) {
		status.Set("candidate_inspection", inspection)
		return body, nil
	}
	byKey := map[string]indexview.Entry{}
	for _, entry := range typed {
		byKey[entry.RecordID+"/"+integer(entry.Version).Integer] = entry
	}
	groups, err := candidateGroups(entries)
	if err != nil {
		return "", err
	}
	var oversizedEntry, oversized *value.Value
	refusal := func(key string) { inspection.Get("refusals").Set(key, integer(1)) }
	spend := func() {
		calls := number(inspection.Get("pull_calls")) + 1
		inspection.Set("pull_calls", integer(calls))
		inspection.Set("remaining_pull_calls", integer(4-calls))
	}
	var lastGroup []*value.Value
	for _, group := range groups {
		lastGroup = group
		candidateRoute(observers, "candidate_start", group)
		if len(progress.bodies) > 0 {
			future := clone(inspection)
			for _, key := range []string{"record_limit", "unverifiable_identity", "deadline", "pull_limit", "whole_pull_budget", "whole_context_budget", "whole_pull_unavailable", "span_pull_budget", "span_context_budget", "span_unavailable"} {
				future.Get("refusals").Set(key, integer(1))
			}
			if _, err := render(&candidateProgress{bodies: progress.bodies, inspection: future}, clone(status)); contextError(err) {
				candidateRoute(observers, "protected_body_budget", group)
				break
			} else if err != nil {
				return "", err
			}
		}
		if number(inspection.Get("delivered_records"))+len(group) > 2 {
			refusal("record_limit")
			candidateRoute(observers, "record_limit", group)
			oversized = nil
			break
		}
		verifiable := true
		for _, entry := range group {
			verifiable = verifiable && bodyHash.MatchString(text(entry.Get("body_sha256")))
		}
		if !verifiable {
			refusal("unverifiable_identity")
			candidateRoute(observers, "unverifiable_identity", group)
			oversized = nil
			break
		}
		if ctx.Err() != nil {
			refusal("deadline")
			candidateRoute(observers, "deadline", group)
			oversized = nil
			break
		}
		if number(inspection.Get("pull_calls")) >= 2 {
			refusal("pull_limit")
			candidateRoute(observers, "pull_limit", group)
			break
		}
		oversized = nil
		var pulled *value.Value
		spend()
		raw, readErr := client.CurrentWhole(ctx, byKey[candidateKey(group[0])])
		if readErr == nil {
			pulled, readErr = value.DecodeText(raw, "utf-8", 0)
		}
		if readErr == nil {
			readErr = checkedWhole(group, pulled)
		}
		if unicodeFault(readErr) {
			return "", readErr
		}
		if readErr == nil {
			candidateValidated(observers, "whole", pulled)
			candidate := value.Mapping(value.Field("pull_arguments", group[0].Get("pull_arguments")), value.Field("response", pulled))
			tentative := clone(inspection)
			tentative.Set("delivered_records", integer(number(inspection.Get("delivered_records"))+len(group)))
			next := &candidateProgress{bodies: append(append([]*value.Value{}, progress.bodies...), candidate), inspection: tentative}
			body, readErr = render(next, status)
			if readErr == nil {
				progress = next
				inspection = tentative
				continue
			}
		}
		if unicodeFault(readErr) {
			return "", readErr
		}
		switch {
		case pullBudget(readErr):
			refusal("whole_pull_budget")
			candidateRoute(observers, "whole_pull_budget", group)
		case contextError(readErr):
			refusal("whole_context_budget")
			candidateRoute(observers, "whole_context_budget", group)
		default:
			if core.Code(readErr) == "STALE_HANDLE" && value.SameText(config.Get("recall_view"), "compact/1") {
				refusal("stale_handle")
				candidateRoute(observers, "stale_handle", group)
			} else {
				refusal("whole_pull_unavailable")
				candidateRoute(observers, "whole_pull_unavailable", group)
			}
			// Unconfirmed or changed bytes are never a paid whole response.
			pulled = nil
		}
		if !pullBudget(readErr) && !contextError(readErr) {
			break
		}
		entry := group[0]
		excerptable := len(group) == 1 && (value.SameText(entry.Get("class"), "A") || value.SameText(entry.Get("class"), "B")) && !value.Truth(entry.Get("mandatory")) && !value.Truth(entry.Get("conflicts"))
		for _, item := range selected.Items {
			if value.Truth(item.Get("mandatory")) && text(item.Get("record").Get("record_id")) == text(entry.Get("record_id")) {
				excerptable = false
			}
		}
		if !excerptable {
			candidateRoute(observers, "group_not_excerptable", group)
			break
		}
		spanObserved := false
		candidate, spanErr := recoverSpan(ctx, client, entry, pulled, inspection, spend, func(item *value.Value) (string, error) {
			if !spanObserved {
				if pulled != nil {
					candidateRoute(observers, "span_from_paid_whole", group)
				}
				candidateValidated(observers, "span", item.Get("response"))
				spanObserved = true
			}
			tentative := clone(inspection)
			tentative.Set("delivered_records", integer(number(inspection.Get("delivered_records"))+1))
			return render(&candidateProgress{bodies: append(append([]*value.Value{}, progress.bodies...), item), inspection: tentative}, status)
		})
		if pulled != nil && !spanObserved && spanErr != nil {
			// A failure before the helper's first render cannot expose whether
			// its paid-whole excerpt branch was reached. Preserve unknown.
			candidateObserve(observers, func(o candidateObserver) {
				if loss, ok := o.(interface{ ObservationLost() }); ok {
					loss.ObservationLost()
				}
			})
		}
		if spanErr == nil {
			tentative := clone(inspection)
			tentative.Set("delivered_records", integer(number(inspection.Get("delivered_records"))+1))
			next := &candidateProgress{bodies: append(append([]*value.Value{}, progress.bodies...), candidate), inspection: tentative}
			body, spanErr = render(next, status)
			if spanErr == nil {
				progress = next
				inspection = tentative
				continue
			}
		}
		var stopped *inspectionStopped
		if errors.As(spanErr, &stopped) {
			if value.Truth(inspection.Get("refusals").Get("deadline")) {
				candidateRoute(observers, "deadline", group)
			} else if value.Truth(inspection.Get("refusals").Get("pull_limit")) {
				candidateRoute(observers, "pull_limit", group)
			}
			break
		}
		if unicodeFault(spanErr) {
			return "", spanErr
		}
		switch {
		case pullBudget(spanErr):
			refusal("span_pull_budget")
			candidateRoute(observers, "span_pull_budget", group)
		case contextError(spanErr):
			refusal("span_context_budget")
			candidateRoute(observers, "span_context_budget", group)
			if len(progress.bodies) > 0 {
				break
			}
			oversizedEntry, oversized = entry, candidate
			continue
		default:
			refusal("span_unavailable")
			candidateRoute(observers, "span_unavailable", group)
		}
		break
	}
	if len(progress.bodies) == 0 && oversized != nil {
		candidateRoute(observers, "fit_start", []*value.Value{oversizedEntry})
		tentative := clone(inspection)
		tentative.Set("delivered_records", integer(1))
		fitted, err := fitExcerpt(ctx, oversizedEntry, oversized, func(item *value.Value) (string, error) {
			return render(&candidateProgress{bodies: []*value.Value{item}, inspection: tentative}, status)
		})
		if err == nil {
			candidateRoute(observers, "fit_admitted", []*value.Value{oversizedEntry})
			progress.bodies = append(progress.bodies, fitted)
			inspection = tentative
		} else if !contextError(err) {
			return "", err
		} else {
			candidateRoute(observers, "fit_refused", []*value.Value{oversizedEntry})
			if ctx.Err() != nil {
				refusal("deadline")
			}
		}
	}
	for {
		progress.inspection = inspection
		body, err = render(progress, status)
		if err == nil {
			status.Set("candidate_inspection", inspection)
			return body, nil
		}
		if !contextError(err) {
			return "", err
		}
		changed := false
		for i := len(progress.bodies) - 1; i >= 0; i-- {
			candidate := progress.bodies[i]
			if value.SameText(candidate.Get("source_opening_excerpt").Get("status"), "provided") {
				candidate.Set("source_opening_excerpt", value.Mapping(value.Field("status", value.Text("unavailable")), value.Field("reason", value.Text("context_budget"))))
				changed = true
				break
			}
		}
		if changed {
			continue
		}
		for i := len(progress.bodies) - 1; i >= 0; i-- {
			if progress.bodies[i].Get("source_opening_excerpt") != nil {
				progress.bodies[i] = progress.bodies[i].Without("source_opening_excerpt")
				changed = true
				break
			}
		}
		if changed {
			continue
		}
		last := progress.bodies[len(progress.bodies)-1]
		progress.bodies = progress.bodies[:len(progress.bodies)-1]
		count := 1
		if competing := last.Get("response").Get("competing"); competing != nil {
			count += len(competing.Items)
		}
		inspection.Set("delivered_records", integer(number(inspection.Get("delivered_records"))-count))
		refusal("whole_context_budget")
		candidateRoute(observers, "whole_context_budget", lastGroup)
	}
}
