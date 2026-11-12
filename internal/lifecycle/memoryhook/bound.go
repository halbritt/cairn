package memoryhook

import (
	"context"
	"strings"

	"github.com/halbritt/cairn/internal/lifecycle/binding"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// RunBound joins a resolved activation to the existing recall/capture body.
// The checked snapshot retains its binding; the effective body configuration
// follows Source's removal when the resolved activation is disabled/excluded.
func RunBound(ctx context.Context, invocation *binding.MemoryInvocation) Result {
	checked, directory, environment, err := invocation.Inputs()
	if err != nil {
		return HostFailure("activation", err)
	}
	config := checked.Config
	if checked.Disposition != "checked" {
		config = config.Without("inbox_recall_binding")
	}
	if captureEventName(checked.Event) {
		return runMemory(ctx, config, checked.Event, checked.Disposition == "checked", directory, environment, true, invocation, checked.Coordinators...)
	}
	if value.SameText(config.Get("harness"), "claude") || !value.SameText(config.Get("recall_mode"), "agent_tools") {
		return runMemory(ctx, config, checked.Event, checked.Disposition == "checked", directory, environment, true, invocation, checked.Coordinators...)
	}
	return RunStart(ctx, config, checked.Event, checked.Disposition == "checked", directory, environment, checked.Coordinators...)
}

// BoundFailure preserves the main-boundary read-only required-context guard.
// No failed activation can create, refund or persist a native grant here.
func BoundFailure(phase string, config, event *value.Value, err error) Result {
	out := HostFailure(phase, err)
	diagnostic := strings.SplitN(out.Stderr, "\n", 2)[0] + "\n"
	_, class, _ := strings.Cut(strings.TrimSuffix(diagnostic, "\n"), "error_class=")
	out.Trace["error_class"] = class
	if phase == "activation" {
		out.Trace["stage"] = "activation"
	}
	if class == "BlockingIOError" {
		out.Trace["cause"] = "blocking_io"
	}
	if (value.SameText(config.Get("harness"), "codex") || boundClaude(config)) && nativeUUID(event.Get("session_id")) && (value.SameText(event.Get("hook_event_name"), "SessionStart") || value.SameText(event.Get("hook_event_name"), "UserPromptSubmit")) {
		converted := requiredFailure(config, event, classify(err), true, false)
		if boundClaude(config) {
			converted = claudeBoundaryError(config, event, converted)
		}
		if isRequired(converted) {
			refused := requiredMemoryOutput(config, event, required("current required Claude context could not be restored"), diagnostic)
			out.Trace["outcome"] = "required_refused"
			if boundClaude(config) && value.SameText(event.Get("hook_event_name"), "SessionStart") {
				out.Trace["outcome"] = "error"
			}
			refused.Trace = out.Trace
			return refused
		}
	}
	return out
}
