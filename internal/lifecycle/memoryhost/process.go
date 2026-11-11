// Package memoryhost owns the ordinary raw-stdin memory process boundary.
package memoryhost

import (
	"context"
	"fmt"
	"io"
	"os"
	"regexp"
	"strings"

	"github.com/halbritt/cairn/internal/lifecycle/memoryhook"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// Run accepts Source's memory --config PATH entry. Private activation remains a
// separate dispatcher and cannot be selected by changing the ordinary harness.
func Run(ctx context.Context, args []string, input io.Reader, out, diagnostic io.Writer) (code int) {
	phase := "configuration"
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
	configPath, parseError, help := parseArgs(args)
	if help {
		io.WriteString(out, "usage: memory.py [-h] --config CONFIG\n\nSelected Cairn memory at authorized host lifecycle boundaries (stdlib only).\n\noptions:\n  -h, --help       show this help message and exit\n  --config CONFIG\n")
		return 0
	}
	if parseError != "" {
		io.WriteString(diagnostic, "usage: memory.py [-h] --config CONFIG\nmemory.py: error: "+parseError+"\n")
		return 2
	}

	trace = newTrace(configPath)
	trace.entry()
	emit := func(result memoryhook.Result) int {
		for key, v := range result.Trace {
			trace.fields[key] = v
		}
		if result.ExitCode != 0 && trace.fields["outcome"] == nil {
			trace.fields["outcome"] = "error"
		}
		if result.Stderr != "" {
			line, _, _ := strings.Cut(result.Stderr, "\n")
			if _, class, ok := strings.Cut(line, "error_class="); ok {
				trace.fields["error_class"] = class
			}
		}

		if _, err := io.WriteString(out, result.Stdout); err != nil {
			trace.fields["outcome"] = "unexpected"
			return 1
		}
		if _, err := io.WriteString(diagnostic, result.Stderr); err != nil {
			trace.fields["outcome"] = "unexpected"
			return 1
		}
		return result.ExitCode
	}
	raw, err := os.ReadFile(configPath)
	if err != nil {
		return emit(memoryhook.HostFailure(phase, err))
	}
	config, err := value.DecodeText(raw, "utf-8", 0)
	if err != nil {
		return emit(memoryhook.HostFailure(phase, err))
	}
	phase = "input"
	raw, err = io.ReadAll(io.LimitReader(input, 1024*1024+1))
	if err != nil {
		return emit(memoryhook.HostFailure(phase, err))
	}
	if len(raw) > 1024*1024 {
		return emit(memoryhook.InvalidInput(phase, "host event exceeds input limit"))
	}
	event, err := value.DecodeBytes(raw, 0)
	if err != nil {
		return emit(memoryhook.HostFailure(phase, err))
	}
	if config.Kind != value.Object || event.Kind != value.Object {
		return emit(memoryhook.InvalidInput(phase, "configuration and host event must be objects"))
	}
	trace.parsed(event)
	phase = "activation"
	trace.fields["stage"] = "activation"
	trace.fields["bound"] = false
	phase = "handle"
	cwd, err := os.Getwd()
	if err != nil {
		return emit(memoryhook.HostFailure(phase, err))
	}
	return emit(memoryhook.RunOrdinary(ctx, config, event, cwd, os.Environ()))
}

var negativeArgument = regexp.MustCompile(`^-([0-9]+|[0-9]*\.[0-9]+)$`)

func parseArgs(args []string) (path, problem string, help bool) {
	supplied := false
	var extra []string
	for i := 0; i < len(args); i++ {
		arg := args[i]
		key, argument, equals := strings.Cut(arg, "=")
		long := strings.HasPrefix(key, "--") && len(key) > 2
		if key == "-h" || long && strings.HasPrefix("--help", key) {
			if equals {
				return "", "argument -h/--help: ignored explicit argument '" + argument + "'", false
			}
			return "", "", true
		}
		if long && strings.HasPrefix("--config", key) {
			supplied = true
			if equals {
				path = argument
				continue
			}
			if i+1 >= len(args) || strings.HasPrefix(args[i+1], "-") && !negativeArgument.MatchString(args[i+1]) {
				return "", "argument --config: expected one argument", false
			}
			i++
			path = args[i]
			continue
		}
		extra = append(extra, arg)
	}
	if !supplied {
		return "", "the following arguments are required: --config", false
	}
	if len(extra) > 0 {
		return "", "unrecognized arguments: " + strings.Join(extra, " "), false
	}
	if path == "" {
		path = "."
	}
	return path, "", false
}

// A failed diagnostic sink cannot turn an already handled public panic into a
// second panic. Fixed diagnostics are best effort; the public failure stays 1.
func panicDiagnostic(out io.Writer, phase string) {
	defer func() { _ = recover() }()
	_, _ = fmt.Fprintf(out, "Cairn lifecycle diagnostic: phase=%s error_class=UnexpectedError\n", phase)
}
