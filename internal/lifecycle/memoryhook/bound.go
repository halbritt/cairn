package memoryhook

import (
	"context"
	"strings"

	"github.com/halbritt/cairn/internal/lifecycle/binding"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// RunBound joins a resolved activation to the existing recall/capture body. The
// original configuration retains its binding even when Source excludes activation.
func RunBound(ctx context.Context, invocation *binding.MemoryInvocation) Result {
	checked, directory, environment, err := invocation.Inputs()
	if err != nil {
		return HostFailure("activation", err)
	}
	if captureEventName(checked.Event) {
		return runMemory(ctx, checked.Config, checked.Event, checked.Disposition == "checked", directory, environment, true, invocation)
	}
	if value.SameText(checked.Config.Get("harness"), "claude") {
		return runMemory(ctx, checked.Config, checked.Event, checked.Disposition == "checked", directory, environment, true, invocation)
	}
	return RunStart(ctx, checked.Config, checked.Event, checked.Disposition == "checked", directory, environment, checked.Coordinators...)
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
		if isRequired(requiredFailure(config, event, classify(err), true, false)) {
			refused := requiredMemoryOutput(config, event, required("current required Claude context could not be restored"), diagnostic)
			out.Trace["outcome"] = "required_refused"
			refused.Trace = out.Trace
			return refused
		}
	}
	return out
}
