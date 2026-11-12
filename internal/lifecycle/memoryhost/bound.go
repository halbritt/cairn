package memoryhost

import (
	"context"
	"errors"
	"os"
	"syscall"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/binding"
	"github.com/halbritt/cairn/internal/lifecycle/memoryhook"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func runCheckedBound(ctx context.Context, entry *binding.MemoryEntry, event *value.Value, raw []byte, directory string, timeout time.Duration) memoryhook.Result {
	config := entry.Config()
	codex := value.SameText(config.Get("harness"), "codex")
	claude := value.SameText(config.Get("harness"), "claude")
	recall := value.SameText(event.Get("hook_event_name"), "SessionStart") || value.SameText(event.Get("hook_event_name"), "UserPromptSubmit")
	capture := value.SameText(event.Get("hook_event_name"), "PreCompact") || codex && value.SameText(event.Get("hook_event_name"), "Stop") || claude && value.SameText(event.Get("hook_event_name"), "SessionEnd")
	if !((recall || capture) && (codex || claude)) {
		return memoryhook.InvalidInput("activation", "checked bound memory cell remains unavailable")
	}
	if recall && timeout != 13*time.Second || capture && timeout != 150*time.Second {
		return memoryhook.Result{Stderr: "Cairn lifecycle diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n", ExitCode: 1}
	}
	invocation, err := entry.Resolve(ctx, raw, directory, os.Environ())
	if err != nil {
		return checkedBoundFailure(config, event, err)
	}
	result := memoryhook.RunBound(ctx, invocation)
	if err = invocation.Recheck(); err != nil {
		return checkedBoundFailure(config, event, err)
	}
	return result
}

func checkedBoundFailure(config, event *value.Value, err error) memoryhook.Result {
	var preflight *binding.ActivationError
	if errors.As(err, &preflight) {
		return memoryhook.Result{Stderr: "Cairn lifecycle diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n", ExitCode: 1}
	}
	if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
		err = syscall.ETIMEDOUT
	}
	var source *value.Error
	var path *os.PathError
	var number syscall.Errno
	if !errors.As(err, &source) && !errors.As(err, &path) && !errors.As(err, &number) {
		err = &value.Error{Class: "ValueError", Message: "invalid bound memory activation"}
	}
	return memoryhook.BoundFailure("activation", config, event, err)
}
