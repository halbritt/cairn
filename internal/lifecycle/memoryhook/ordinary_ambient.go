package memoryhook

import (
	"context"
	"strings"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/indexview"
	"github.com/halbritt/cairn/internal/lifecycle/memoryclient"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"golang.org/x/text/cases"
	"golang.org/x/text/language"
)

func overlap(left, right []string) int {
	seen := map[string]bool{}
	for _, s := range left {
		if contains(right, s) {
			seen[s] = true
		}
	}
	return len(seen)
}
func associated(entry *value.Value, intent recallIntent) bool {
	entities := entry.Get("entities")
	if entities == nil {
		return false
	}
	for _, entity := range entities.Items {
		if value.SameText(entity.Get("kind"), "file") && contains(intent.files, text(entity.Get("name"))) {
			return true
		}
	}
	return false
}
func projectDirection(entry *value.Value, body string, intent recallIntent) bool {
	if !intent.startup || !contains([]string{"decision", "preference"}, text(entry.Get("kind"))) {
		return false
	}
	body = cases.Lower(language.Und).String(body)
	project := cases.Lower(language.Und).String(intent.project)
	if !strings.HasPrefix(body, project) {
		return false
	}
	rest := strings.TrimPrefix(body, project)
	if rest == "" {
		return false
	}
	r, _ := utf8.DecodeRuneInString(rest)
	return r == ':' || pySpace(r)
}
func relevantPreview(entry *value.Value, intent recallIntent) bool {
	summary := text(entry.Get("summary"))
	if associated(entry, intent) {
		return true
	}
	for _, phrase := range intent.phrases {
		if strings.Contains(summary, phrase) {
			return true
		}
	}
	return overlap(terms(summary), intent.words) >= 2 || projectDirection(entry, summary, intent)
}
func relevantBody(entry *value.Value, body string, intent recallIntent) bool {
	if associated(entry, intent) || projectDirection(entry, body, intent) {
		return true
	}
	for _, phrase := range intent.phrases {
		if strings.Contains(body, phrase) {
			return true
		}
	}
	if len(intent.words) < 2 {
		return false
	}
	points := []rune(body)
	for start := 0; start < len(points); start += 400 {
		if overlap(terms(string(points[start:min(start+600, len(points))])), intent.words) >= 2 {
			return true
		}
	}
	return false
}
func ambientEntry(entry *value.Value) *value.Value {
	result := value.Mapping()
	for _, key := range []string{"record_id", "version", "summary", "pull_arguments"} {
		result.Set(key, entry.Get(key))
	}
	if hint := entry.Get("match_span"); hint != nil {
		result.Set("match_span", hint)
	}
	return result
}
func ambientRender(selected *value.Value, entries []*value.Value, pulled, discovery *value.Value) (string, error) {
	display := array()
	for _, entry := range entries {
		display.Items = append(display.Items, ambientEntry(entry))
	}
	return ordinaryRender(selected, display, pulled, discovery)
}

// ambientRecall is the non-provider Source path. Model-selected discovery is a
// retained cell, rather than a synthetic successful selection by this body.
func ambientRecall(ctx context.Context, client *memoryclient.Client, event, state, status, result, selected *value.Value, typed []indexview.Entry, intent recallIntent, budget int, deferred, notification bool, project string) (string, error) {
	var entries, previews []*value.Value
	seen := state.Get("seen")
	if !deferred {
		for _, entry := range result.Get("index").Items {
			old := seen.Get(text(entry.Get("record_id")))
			if old != nil && sameInteger(old, entry.Get("version")) {
				continue
			}
			entries = append(entries, entry)
			if len(entries) == 6 {
				break
			}
		}
	}
	for _, entry := range entries {
		if relevantPreview(entry, intent) {
			previews = append(previews, entry)
		}
	}
	ordered := append([]*value.Value{}, previews...)
	for _, entry := range entries {
		found := false
		for _, preview := range previews {
			found = found || entry == preview
		}
		if !found {
			ordered = append(ordered, entry)
		}
	}
	byID := map[string]indexview.Entry{}
	for _, entry := range typed {
		byID[entry.RecordID+"/"+integer(entry.Version).Integer] = entry
	}
	var chosen, pulled *value.Value
	reject := func(reason string) {
		rejected := status.Get("rejected")
		rejected.Set(reason, integer(number(rejected.Get(reason))+1))
	}
	if deferred {
		status.Set("discovery", value.Text("deferred"))
		why := "taskless_startup"
		if notification {
			why = "taskless_notification"
		}
		status.Set("optional_deferred", value.Text(why))
	} else {
		for _, entry := range ordered {
			raw, err := client.CurrentWhole(ctx, byID[candidateKey(entry)])
			var candidate *value.Value
			if err == nil {
				candidate, err = value.DecodeText(raw, "utf-8", 0)
			}
			if pullBudget(err) {
				hint, hintErr := optionalHint(entry)
				if hintErr == nil {
					candidate, hintErr = spanCall(ctx, client, entry, hint)
				}
				if hintErr == nil {
					hintErr = checkedSpan(entry, candidate, hint)
				}
				if pullBudget(hintErr) {
					reject("budget_refused")
					break
				}
				if hintErr != nil {
					if errorFamily(classify(hintErr)) != "HookError" {
						return "", hintErr
					}
					reject("budget_refused")
					continue
				}
				err = nil
			} else if err != nil {
				if errorFamily(classify(err)) != "HookError" {
					return "", err
				}
				reject("unavailable")
				continue
			}
			status.Set("inspected", integer(number(status.Get("inspected"))+1))
			body := candidate.Get("selection").Get("record").Get("body")
			if value.SameText(candidate.Get("source_extent"), "partial_span") {
				body = candidate.Get("span").Get("body")
			}
			if !validText(body) {
				return "", &value.Error{Class: "UnicodeEncodeError", Message: "invalid optional body"}
			}
			if !relevantBody(entry, text(body), intent) {
				reject("not_relevant")
				for i, preview := range previews {
					if entry == preview {
						previews = append(previews[:i], previews[i+1:]...)
						break
					}
				}
				continue
			}
			rendered, err := ambientRender(selected, []*value.Value{entry}, candidate, nil)
			if err != nil {
				return "", err
			}
			if len(rendered) > budget && !value.SameText(candidate.Get("source_extent"), "partial_span") {
				hint, hintErr := optionalHint(entry)
				if hintErr == nil {
					candidate, hintErr = excerptWhole(entry, candidate, hint)
				}
				if hintErr != nil {
					reject("context_budget")
					continue
				}
				if !relevantBody(entry, text(candidate.Get("span").Get("body")), intent) {
					reject("context_budget")
					continue
				}
			}
			rendered, err = ambientRender(selected, []*value.Value{entry}, candidate, nil)
			if err != nil {
				return "", err
			}
			if len(rendered) > budget {
				reject("context_budget")
				continue
			}
			chosen, pulled = entry, candidate
			break
		}
	}
	if chosen != nil {
		found := false
		for _, entry := range previews {
			found = found || entry == chosen
		}
		if !found {
			previews = append([]*value.Value{chosen}, previews...)
		}
	}
	if len(selected.Items) == 0 && len(previews) == 0 {
		return "", nil
	}
	var packed []*value.Value
	if chosen != nil {
		packed = append(packed, chosen)
	}
	for _, entry := range previews {
		found := false
		for _, p := range packed {
			found = found || p == entry
		}
		if found || len(packed) >= 6 {
			continue
		}
		candidate := append(append([]*value.Value{}, packed...), entry)
		rendered, err := ambientRender(selected, candidate, pulled, result.Get("discovery"))
		if err != nil {
			return "", err
		}
		if len(rendered) <= budget {
			packed = candidate
		}
	}
	if len(selected.Items) == 0 && len(packed) == 0 {
		return "", nil
	}
	body, err := ambientRender(selected, packed, pulled, result.Get("discovery"))
	if err != nil {
		return "", err
	}
	var expanded, partial *value.Value
	if chosen != nil {
		title, _, _ := strings.Cut(text(pulled.Get("selection").Get("record").Get("body")), "\n")
		if strings.HasPrefix(title, workstreamPrefix(project)) && !strings.Contains(title, " / Claude session ") {
			state.Set("workstream", value.Text(title))
		}
		if value.SameText(pulled.Get("source_extent"), "partial_span") {
			partial = chosen.Get("record_id")
		} else {
			expanded = chosen.Get("record_id")
			seen.Set(text(expanded), chosen.Get("version"))
			if len(seen.Members) > 256 {
				seen.Members = seen.Members[len(seen.Members)-256:]
			}
		}
	} else if len(previews) > 0 && number(status.Get("rejected").Get("unavailable")) > 0 {
		warning := "Optional body unavailable; search again before relying on its preview.\n"
		if len(warning)+len(body) <= budget {
			body = warning + body
		}
	}
	records := array()
	for _, entry := range packed {
		records.Items = append(records.Items, value.Mapping(value.Field("record_id", entry.Get("record_id")), value.Field("version", entry.Get("version"))))
	}
	status.Set("outcome", value.Text("recalled"))
	status.Set("bytes", integer(len(body)))
	status.Set("records", records)
	status.Set("expanded", expanded)
	status.Set("partial_record", partial)
	extent := pulled.Get("source_extent")
	if pulled != nil && extent == nil {
		extent = value.Text("full_body")
	}
	status.Set("source_extent", extent)
	return body, nil
}
