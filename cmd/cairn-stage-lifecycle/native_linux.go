package main

import (
	"context"
	"errors"
	"flag"
	"io"

	"github.com/halbritt/cairn/internal/lifecycle/native"
)

func runNativeClaudeOwner(ctx context.Context, args []string, input io.Reader, output, diagnostic io.Writer) error {
	flags := flag.NewFlagSet("native-claude-owner", flag.ContinueOnError)
	flags.SetOutput(diagnostic)
	cfg := native.ClaudeConfig{}
	flags.StringVar(&cfg.Client.Path, "client", "", "exact selected native Claude executable")
	flags.StringVar(&cfg.Client.SHA256, "client-sha256", "", "selected native executable SHA-256")
	flags.StringVar(&cfg.ClientVersion, "client-version", "", "qualified native version")
	flags.StringVar(&cfg.SourceSettings.Path, "source-settings", "", "pinned original settings to copy into a new owned namespace")
	flags.StringVar(&cfg.SourceSettings.SHA256, "source-sha256", "", "original settings SHA-256")
	flags.StringVar(&cfg.Parent, "parent", "", "existing private parent for a newly allocated native settings namespace")
	flags.StringVar(&cfg.Account, "account-home", "", "explicit native application account; HOME and PATH are preserved")
	flags.StringVar(&cfg.Work, "work", "", "explicit native working directory")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return errors.New("native-claude-owner takes no positional arguments")
	}
	return native.RunClaudeOwner(ctx, cfg, input, output)
}
