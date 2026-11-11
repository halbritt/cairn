package memoryhost

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"time"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/binding"
	"github.com/halbritt/cairn/internal/lifecycle/memoryhook"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// RunCheckedCapture consumes an explicitly unbound H150 receipt selection. The
// real entry time and argv cover preflight, stdin, capture and SDK work together.
func RunCheckedCapture(parent context.Context, args []string, input io.Reader, out, diagnostic io.Writer) (code int) {
	entered := time.Now()
	phase := "compatibility_preflight"
	var trace *invocationTrace
	defer func() {
		if recover() != nil {
			panicDiagnostic(diagnostic, phase)
			code = 1
			if trace != nil {
				trace.fields["outcome"] = "unexpected"
			}
		}
		trace.finish(code, phase)
	}()
	refuse := func() int {
		_, _ = io.WriteString(diagnostic, "Cairn lifecycle diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n")
		return 1
	}
	vector := append([]string(nil), os.Args...)
	for i, word := range vector {
		if word == "--" {
			vector = vector[:i]
			break
		}
	}
	if len(vector) < 2 || vector[1] != "memory-capture" {
		return refuse()
	}
	for _, word := range vector {
		if !utf8.ValidString(word) {
			return refuse()
		}
	}
	selection, launchRoot, shell, timeout, err := checkedArguments(args)
	if err != nil || timeout != 150*time.Second {
		return refuse()
	}
	ctx, cancel := context.WithDeadline(parent, entered.Add(timeout))
	defer cancel()
	raw, err := checkedLauncherBytes(vector, shell)
	if err != nil {
		return refuse()
	}
	sum := sha256.Sum256(raw)
	launcher := filepath.Join(launchRoot, hex.EncodeToString(sum[:]), "inbox")
	if err = binding.VerifyOrdinaryLauncher(launcher, raw); err != nil {
		return refuse()
	}
	selection.ImagePath = vector[0]
	snapshot, err := binding.VerifyUnboundSelection(ctx, selection)
	if err != nil {
		var data *binding.OrdinaryDataError
		if errors.As(err, &data) {
			return checkedEmit(memoryhook.HostFailure(data.Phase, data.Err), out, diagnostic)
		}
		return refuse()
	}
	defer snapshot.Close()
	phase = "input"
	rawEvent, err := checkedInput(ctx, input)
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
			return refuse()
		}
		return checkedEmit(memoryhook.HostFailure(phase, err), out, diagnostic)
	}
	if len(rawEvent) > 1024*1024 {
		return checkedEmit(memoryhook.InvalidInput(phase, "host event exceeds input limit"), out, diagnostic)
	}
	event, err := value.DecodeBytes(rawEvent, 0)
	if err != nil {
		return checkedEmit(memoryhook.HostFailure(phase, err), out, diagnostic)
	}
	if event.Kind != value.Object {
		return checkedEmit(memoryhook.InvalidInput(phase, "configuration and host event must be objects"), out, diagnostic)
	}
	if !checkedCaptureCell(snapshot.Config, event) {
		return refuse()
	}
	phase = "handle"
	directory, err := os.Getwd()
	if err != nil {
		return checkedEmit(memoryhook.HostFailure(phase, err), out, diagnostic)
	}
	trace = newTrace(selection.ConfigPath)
	trace.entry()
	trace.parsed(event)
	trace.fields["stage"] = "activation"
	trace.fields["bound"] = false
	result := memoryhook.RunOrdinary(ctx, snapshot.Config, event, directory, os.Environ())
	for key, v := range result.Trace {
		trace.fields[key] = v
	}
	return checkedEmit(result, out, diagnostic)
}

func checkedCaptureCell(config, event *value.Value) bool {
	name := event.Get("hook_event_name")
	if value.SameText(config.Get("harness"), "codex") {
		return value.SameText(name, "PreCompact") || value.SameText(name, "Stop")
	}
	if value.SameText(config.Get("harness"), "claude") || value.SameText(config.Get("harness"), "opencode") || value.SameText(config.Get("harness"), "hermes") {
		return value.SameText(name, "PreCompact") || value.SameText(name, "SessionEnd")
	}
	return false
}
