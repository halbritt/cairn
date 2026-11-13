package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"io"

	"github.com/halbritt/cairn/internal/jsontext"
	"github.com/halbritt/cairn/internal/lifecycle/install"
)

func runAgyLayer(ctx context.Context, args []string, input io.Reader, out, diagnostic io.Writer) error {
	flags := flag.NewFlagSet("agy-layer", flag.ContinueOnError)
	flags.SetOutput(diagnostic)
	plan := install.AgyLayerPlan{}
	flags.StringVar(&plan.AccountHome, "account-home", "", "explicit rooted Agy account home")
	flags.StringVar(&plan.Workspace, "workspace", "", "explicit rooted Agy workspace")
	flags.StringVar(&plan.Layer, "layer", "", "single selected account or project layer")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 || plan.AccountHome == "" || plan.Workspace == "" || plan.Layer != "account" && plan.Layer != "project" {
		return errors.New("agy-layer requires --account-home ABS --workspace ABS --layer account|project and no positional arguments")
	}
	raw, err := io.ReadAll(io.LimitReader(input, 1024*1024+1))
	if err != nil || len(raw) > 1024*1024 {
		return errors.Join(err, errors.New("invalid Agy publication plan size"))
	}
	if err := jsontext.CheckUnicode(raw); err != nil {
		return err
	}
	fields, err := planObject(raw)
	if err != nil || len(fields) < 2 || len(fields) > 3 || string(fields["schema"]) != `"cairn.agy-layer-install/1"` {
		return errors.Join(err, errors.New("Agy plan requires schema cairn.agy-layer-install/1 and settings"))
	}
	for name := range fields {
		if name != "schema" && name != "settings" && name != "replace_commands" {
			return errors.New("unknown Agy publication plan field")
		}
	}
	if commands, ok := fields["replace_commands"]; ok {
		var entries []json.RawMessage
		if err := json.Unmarshal(commands, &entries); err != nil || string(commands) == "null" {
			return errors.New("Agy replace_commands must be a string array")
		}
		for _, entry := range entries {
			var command string
			if err := json.Unmarshal(entry, &command); err != nil || string(entry) == "null" || command == "" {
				return errors.New("Agy replacement commands must be nonempty strings")
			}
			plan.ReplaceCommands = append(plan.ReplaceCommands, command)
		}
	}
	settings, err := planObject(fields["settings"])
	if err != nil || len(settings) != 2 || settings["path"] == nil || settings["sha256"] == nil {
		return errors.Join(err, errors.New("Agy settings selection requires exactly path and sha256"))
	}
	if err := json.Unmarshal(fields["settings"], &plan.Settings); err != nil {
		return err
	}
	selected, err := install.PublishAgyLayer(ctx, plan)
	if err != nil {
		return err
	}
	return json.NewEncoder(out).Encode(selected)
}
