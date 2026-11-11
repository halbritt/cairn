// Package memoryhost owns the ordinary raw-stdin memory process boundary.
package memoryhost

import (
	"context"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/halbritt/cairn/internal/lifecycle/memoryhook"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// Run accepts Source's memory --config PATH entry. Private activation remains a
// separate dispatcher and cannot be selected by changing the ordinary harness.
func Run(ctx context.Context, args []string, input io.Reader, out, diagnostic io.Writer) (code int) {
	phase := "configuration"
	defer func() {
		if recover() != nil {
			fmt.Fprintf(diagnostic, "Cairn lifecycle diagnostic: phase=%s error_class=UnexpectedError\n", phase)
			code = 1
		}
	}()
	var configPath string
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if arg == "--help" || arg == "-h" {
			io.WriteString(out, "usage: memory.py [-h] --config CONFIG\n\noptions:\n  -h, --help       show this help message and exit\n  --config CONFIG\n")
			return 0
		}
		if arg == "--config" && i+1 < len(args) {
			i++
			configPath = args[i]
		} else if strings.HasPrefix(arg, "--config=") {
			configPath = strings.TrimPrefix(arg, "--config=")
		} else {
			io.WriteString(diagnostic, "usage: memory.py [-h] --config CONFIG\nmemory.py: error: unrecognized arguments: "+arg+"\n")
			return 2
		}
	}
	if configPath == "" {
		io.WriteString(diagnostic, "usage: memory.py [-h] --config CONFIG\nmemory.py: error: the following arguments are required: --config\n")
		return 2
	}
	emit := func(result memoryhook.Result) int {
		io.WriteString(out, result.Stdout)
		io.WriteString(diagnostic, result.Stderr)
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
	phase = "handle"
	cwd, err := os.Getwd()
	if err != nil {
		return emit(memoryhook.HostFailure(phase, err))
	}
	return emit(memoryhook.RunOrdinary(ctx, config, event, cwd, os.Environ()))
}
