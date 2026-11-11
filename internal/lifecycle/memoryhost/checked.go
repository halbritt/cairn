package memoryhost

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"flag"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/binding"
	"github.com/halbritt/cairn/internal/lifecycle/memoryhook"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// RunChecked consumes only the direct Go R04 selection agreed with Release.
// H and V come from the actual entry; no payload/vector/deadline flag grants
// authority. Every immutable pin and the actual L precedes config/body use.
func RunChecked(parent context.Context, args []string, input io.Reader, out, diagnostic io.Writer) (code int) {
	entered := time.Now()
	refuse := func() int {
		prefix := "Cairn lifecycle"
		for i, a := range args {
			if a == "--role" && i+1 < len(args) && args[i+1] == "coordination" {
				prefix = "Cairn coordination"
				break
			}
		}
		_, _ = io.WriteString(diagnostic, prefix+" diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n")
		return 1
	}
	vector := append([]string(nil), os.Args...)
	for i, a := range vector {
		if a == "--" {
			vector = vector[:i]
			break
		}
	}
	if len(vector) < 2 || vector[1] != "memory" {
		return refuse()
	}
	for _, word := range vector {
		if !utf8.ValidString(word) {
			return refuse()
		}
	}
	selection, launchRoot, shell, timeout, err := checkedArguments(args)
	if err != nil {
		return refuse()
	}
	ctx, cancel := context.WithDeadline(parent, entered.Add(timeout))
	defer cancel()
	raw, err := checkedLauncherBytes(vector, shell)
	if err != nil {
		return refuse()
	}
	sum := sha256.Sum256(raw)
	path := filepath.Join(launchRoot, hex.EncodeToString(sum[:]), "inbox")
	if err = binding.VerifyOrdinaryLauncher(path, raw); err != nil {
		return refuse()
	}
	selection.ImagePath = vector[0]
	snapshot, err := binding.VerifyMemoryEntry(ctx, selection)
	if err != nil {
		var data *binding.OrdinaryDataError
		if errors.As(err, &data) {
			return checkedEmit(memoryhook.HostFailure(data.Phase, data.Err), out, diagnostic)
		}
		return refuse()
	}
	defer snapshot.Close()
	if !snapshot.Bound() && timeout != 13*time.Second {
		return refuse()
	}
	rawEvent, err := checkedInput(ctx, input)
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
			return refuse()
		}
		return checkedEmit(memoryhook.HostFailure("input", err), out, diagnostic)
	}
	if len(rawEvent) > 1024*1024 {
		return checkedEmit(memoryhook.InvalidInput("input", "host event exceeds input limit"), out, diagnostic)
	}
	event, err := value.DecodeBytes(rawEvent, 0)
	if err != nil {
		return checkedEmit(memoryhook.HostFailure("input", err), out, diagnostic)
	}
	if event.Kind != value.Object {
		return checkedEmit(memoryhook.InvalidInput("input", "configuration and host event must be objects"), out, diagnostic)
	}
	if name := event.Get("hook_event_name"); !snapshot.Bound() && !value.SameText(name, "SessionStart") && !value.SameText(name, "UserPromptSubmit") && !value.SameText(name, "PreToolUse") && !value.SameText(name, "PostToolUse") && !value.SameText(name, "PostToolUseFailure") {
		return refuse()
	}
	directory, err := os.Getwd()
	if err != nil {
		return checkedEmit(memoryhook.HostFailure("handle", err), out, diagnostic)
	}
	trace := newTrace(selection.ConfigPath)
	trace.entry()
	trace.parsed(event)
	if name := event.Get("hook_event_name"); value.SameText(name, "PreToolUse") || value.SameText(name, "PostToolUse") || value.SameText(name, "PostToolUseFailure") {
		trace.fields["stage"] = "activation"
	}
	defer func() {
		if recover() != nil {
			panicDiagnostic(diagnostic, "handle")
			code = 1
		}
		trace.finish(code, "handle")
	}()
	var result memoryhook.Result
	if snapshot.Bound() {
		trace.fields["phase"] = "activation"
		trace.fields["stage"] = "activation"
		result = runCheckedBound(ctx, snapshot, event, rawEvent, directory, timeout)
	} else {
		result = memoryhook.RunOrdinary(ctx, snapshot.Config(), event, directory, os.Environ())
	}
	for k, v := range result.Trace {
		trace.fields[k] = v
	}
	return checkedEmit(result, out, diagnostic)
}

func checkedEmit(result memoryhook.Result, out, diagnostic io.Writer) int {
	if _, err := io.WriteString(out, result.Stdout); err != nil {
		return 1
	}
	if _, err := io.WriteString(diagnostic, result.Stderr); err != nil {
		return 1
	}
	return result.ExitCode
}

func checkedArguments(args []string) (binding.OrdinarySelection, string, string, time.Duration, error) {
	fail := func() (binding.OrdinarySelection, string, string, time.Duration, error) {
		return binding.OrdinarySelection{}, "", "", 0, errors.New("invalid checked memory selection")
	}
	flags := flag.NewFlagSet("checked memory", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	vals := map[string]*string{}
	for _, key := range []string{"receipt", "receipt-sha256", "role", "entrypoint", "config", "hook-timeout", "launch-root", "shell"} {
		vals[key] = flags.String(key, "", "")
	}
	// flag accepts single dashes and dash-prefixed separate values that the
	// checked Python entry refuses. Validate the selected grammar first.
	for i := 0; i < len(args); i++ {
		if args[i] == "--" {
			break
		}
		name, _, joined := strings.Cut(args[i], "=")
		if !strings.HasPrefix(name, "--") || vals[strings.TrimPrefix(name, "--")] == nil {
			return fail()
		}
		if !joined {
			i++
			if i == len(args) || strings.HasPrefix(args[i], "-") {
				return fail()
			}
		}
	}
	if flags.Parse(args) != nil {
		return fail()
	}
	for _, v := range vals {
		if *v == "" {
			return fail()
		}
	}
	if !checkedTimeout.MatchString(*vals["hook-timeout"]) {
		return fail()
	}
	h, err := strconv.ParseFloat(*vals["hook-timeout"], 64)
	if err != nil || (h != 13 && h != 150) || *vals["role"] != "memory" || !filepath.IsAbs(*vals["launch-root"]) || filepath.Clean(*vals["launch-root"]) != *vals["launch-root"] {
		return fail()
	}
	tail := flags.Args()
	if len(tail) != 0 && (len(tail) != 2 || tail[0] != "--config" || tail[1] != *vals["config"]) {
		return fail()
	}
	return binding.OrdinarySelection{ReceiptPath: *vals["receipt"], ReceiptSHA256: *vals["receipt-sha256"], Role: *vals["role"], Entrypoint: *vals["entrypoint"], ConfigPath: *vals["config"]}, *vals["launch-root"], *vals["shell"], time.Duration(h) * time.Second, nil
}

var (
	checkedTimeout   = regexp.MustCompile(`^[0-9]+(\.[0-9]*)?([eE][+-]?[0-9]+)?$`)
	checkedShellWord = regexp.MustCompile(`^[a-zA-Z0-9_@%+=:,./-]+$`)
)

func checkedLauncherBytes(vector []string, shell string) ([]byte, error) {
	if !filepath.IsAbs(shell) || filepath.Clean(shell) != shell || strings.ContainsRune(shell, 0) {
		return nil, errors.New("invalid shell")
	}
	for _, r := range shell {
		if unicode.IsSpace(r) || r < 32 {
			return nil, errors.New("invalid shell")
		}
	}
	words := make([]string, len(vector))
	for i, s := range vector {
		if strings.ContainsRune(s, 0) {
			return nil, errors.New("invalid vector")
		}
		if checkedShellWord.MatchString(s) {
			words[i] = s
		} else {
			words[i] = "'" + strings.ReplaceAll(s, "'", "'\"'\"'") + "'"
		}
	}
	return []byte("#!" + shell + "\nexec " + strings.Join(words, " ") + " -- \"$@\"\n"), nil
}

// The direct entry is one process per hook. Closing its owned stdin at D also
// releases a blocked read; no late event gets a fresh body/SDK allowance.
func checkedInput(ctx context.Context, input io.Reader) ([]byte, error) {
	type result struct {
		raw []byte
		err error
	}
	done := make(chan result, 1)
	go func() { raw, err := io.ReadAll(io.LimitReader(input, 1024*1024+1)); done <- result{raw, err} }()
	select {
	case value := <-done:
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		return value.raw, value.err
	case <-ctx.Done():
		if closer, ok := input.(io.Closer); ok {
			_ = closer.Close()
		}
		return nil, ctx.Err()
	}
}
