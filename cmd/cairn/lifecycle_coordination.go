package main

import (
	"context"
	"io"
	"os"

	"github.com/halbritt/cairn/internal/lifecycle/coordination"
	"github.com/halbritt/cairn/internal/lifecycle/transcript"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

type lifecycleCoordinationPlan struct {
	args  []string
	input io.Reader
}

func (p lifecycleCoordinationPlan) execute(ctx context.Context, out, errOut io.Writer) (int, error) {
	return executeLifecycleCoordination(ctx, p.args, p.input, out, errOut)
}

type lifecycleCoordinationOptions = transcript.CoordinationCLIOptions

// executeLifecycleCoordination is the actual J-05/J-06 CLI handler behind the
// closed public lifecycle registration. F-04 hook/watcher activation remains gated.
func executeLifecycleCoordination(ctx context.Context, args []string, input io.Reader, out, errOut io.Writer) (int, error) {
	cli, err := newLifecycleCoordinationCLI(lifecycleCoordinationOptions{ErrOut: errOut})
	if err != nil {
		return 1, err
	}
	return cli.RunCLI(ctx, args, input, out, errOut)
}

func newLifecycleCoordinationCLI(options lifecycleCoordinationOptions) (*coordination.CLI, error) {
	executable := options.Executable
	if executable == "" {
		var err error
		executable, err = os.Executable()
		if err != nil {
			return nil, err
		}
	}
	options.Executable = executable
	if len(options.Command) == 0 {
		options.Command = []string{executable, "lifecycle", "coordination"}
	}
	return transcript.NewCoordinationCLI(options)
}

func newLifecycleCoordinationProfile(workDir string, envFn func(string) string, lookupEnvFn func(string) (string, bool)) *value.SourceReaderProfile {
	return transcript.NewCoordinationProfile(workDir, envFn, lookupEnvFn)
}

func parseLifecycleClaimInstant(site transcript.ClaimSite, raw *value.Value) (transcript.ClaimInstant, error) {
	return transcript.ParseCoordinationClaimInstant(site, raw)
}

func resolveBoundMemoryStateDir(profile *value.SourceReaderProfile, config *value.Value, uid uint32) (*value.Value, error) {
	return transcript.ResolveBoundMemoryStateDir(profile, config, uid)
}
