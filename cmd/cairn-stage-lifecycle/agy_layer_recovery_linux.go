package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"github.com/halbritt/cairn/internal/lifecycle/install"
	"io"
)

func runAgyLayerRecovery(ctx context.Context, args []string, out, diagnostic io.Writer) error {
	flags := flag.NewFlagSet("recover-agy-layer", flag.ContinueOnError)
	flags.SetOutput(diagnostic)
	plan := install.AgyLayerRecovery{}
	flags.StringVar(&plan.AccountHome, "account-home", "", "explicit rooted Agy account home")
	flags.StringVar(&plan.Workspace, "workspace", "", "explicit rooted Agy workspace")
	flags.StringVar(&plan.Layer, "layer", "", "exact account or project layer")
	flags.StringVar(&plan.Record.Path, "record", "", "explicit retained recovery record path")
	flags.StringVar(&plan.Record.SHA256, "record-sha256", "", "independently selected recovery record SHA-256")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return errors.New("recover-agy-layer takes no positional arguments")
	}
	restored, err := install.RecoverAgyLayer(ctx, plan)
	if err != nil {
		return err
	}
	return json.NewEncoder(out).Encode(restored)
}
