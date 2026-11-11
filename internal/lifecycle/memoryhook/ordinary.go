package memoryhook

import (
	"context"
	"strings"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/binding"
	"github.com/halbritt/cairn/internal/lifecycle/memoryclient"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// RunOrdinary runs actual non-Codex recall. Bound callbacks must use their
// checked activation path; an ordinary entry never downgrades that binding.
func RunOrdinary(ctx context.Context, config, event *value.Value, directory string, environment []string) Result {
	if value.Truth(config.Get("inbox_recall_binding")) {
		return HostFailure("activation", fail("bound ordinary memory requires receipt-selected activation"))
	}
	if !value.SameText(config.Get("harness"), "opencode") && !value.SameText(config.Get("harness"), "agy") {
		return HostFailure("handle", fail("ordinary memory harness remains unavailable"))
	}
	return runMemory(ctx, config, event, false, directory, environment, true)
}

// HostFailure preserves Source's optional public failure. A process panic or
// malformed input is never a required-context refusal or an argparse exit.
func HostFailure(phase string, err error) Result {
	out := failure(nil, err)
	out.Stderr = strings.Replace(out.Stderr, "phase=handle ", "phase="+phase+" ", 1)
	return out
}

// InvalidInput labels selected public shape refusals without exposing data.
func InvalidInput(phase, message string) Result { return HostFailure(phase, fail(message)) }

func ordinaryRecall(ctx context.Context, client *memoryclient.Client, config, event, state, status *value.Value, project string) (string, error) {
	started := time.Now()
	mode := config.Get("recall_mode")
	if !value.SameText(mode, "agent_tools") {
		return "", fail("ordinary ambient recall remains unavailable")
	}
	if view := config.Get("recall_view"); view != nil && !value.SameText(view, "full") && !value.SameText(view, "compact/1") {
		return "", fail("invalid lifecycle recall_view: expected full or compact/1")
	}
	if lifecycle := config.Get("context_lifecycle"); lifecycle != nil && !value.SameText(lifecycle, "full") {
		return "", fail("context_lifecycle compact/1 requires Codex agent_tools recall")
	}
	if config.Get("recall_task_key") != nil {
		return "", fail("invalid explicit recall_task_key or unsupported recall route")
	}
	deadline, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	notification := value.SameText(event.Get("hook_event_name"), "UserPromptSubmit") && strip(text(event.Get("workstream"))) == "" && text(event.Get("source")) != "resume" && text(event.Get("source")) != "compact" && binding.TasklessNotification(event.Get("prompt"))
	deferred := notification || value.SameText(event.Get("hook_event_name"), "SessionStart") && strip(text(event.Get("prompt"))) == "" && strip(text(event.Get("workstream"))) == "" && text(event.Get("source")) != "resume" && text(event.Get("source")) != "compact"
	semantic := !deferred && value.Truth(config.Get("semantic_fallback"))
	query, entities, err := retrievalIntent(event, state, project, semantic)
	if err != nil {
		return "", err
	}
	if notification {
		query, entities = baseName(project), nil
	}
	var kinds []string
	if deferred {
		kinds = []string{"decision", "preference"}
	}
	room := 32000
	view, err := client.Search(deadline, query, memoryclient.SearchOptions{Room: &room, Entities: entities, Kinds: kinds, Semantic: semantic})
	if err != nil {
		return "", classify(err)
	}
	result, err := decoded(view)
	if err != nil {
		return "", err
	}
	if discovery := result.Get("discovery"); discovery != nil {
		status.Set("search_discovery", discovery)
	}
	budget := 12000
	if n := config.Get("context_bytes"); n != nil {
		if n.Kind != value.Int {
			return "", fail("invalid context_bytes; expected an integer from 1000 to 65536")
		}
		budget = number(n)
	}
	if budget < 1000 || budget > 65536 {
		return "", fail("invalid context_bytes; expected an integer from 1000 to 65536")
	}
	selected := result.Get("selected")
	if selected == nil {
		selected = array()
	}
	selectedBody, err := ordinaryRender(selected, array(), nil, nil)
	if err != nil {
		return "", err
	}
	if len(selectedBody) > budget {
		return "", fail("retrieval exceeds lifecycle context budget; no partial instructions injected")
	}
	var body string
	if !deferred {
		body, err = eagerCandidates(deadline, client, config, selected, result, view.Index, budget, status, func(s string) int { return len(s) })
		status.Set("discovery", value.Text("agent_tools"))
		status.Set("bytes", integer(len(body)))
	} else {
		status.Set("discovery", value.Text("deferred"))
		why := "taskless_startup"
		if notification {
			why = "taskless_notification"
		}
		status.Set("optional_deferred", value.Text(why))
		if len(selected.Items) > 0 {
			body = selectedBody
			status.Set("outcome", value.Text("recalled"))
			status.Set("bytes", integer(len(body)))
			status.Set("expanded", nil)
			status.Set("partial_record", nil)
			status.Set("source_extent", nil)
		}
	}
	status.Set("elapsed_seconds", rounded(time.Since(started)))
	return body, err
}

func ordinaryRender(selected, entries, expanded, discovery *value.Value) (string, error) {
	view := value.Mapping(value.Field("selected", selected), value.Field("index", entries))
	if expanded != nil {
		view.Set("expanded", expanded)
	}
	if discovery != nil {
		view.Set("discovery", discovery)
	}
	raw, err := encoded(view, false)
	return guidance + string(raw), err
}
