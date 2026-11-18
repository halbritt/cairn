package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"io"

	"github.com/halbritt/cairn/internal/jsontext"
	"github.com/halbritt/cairn/internal/lifecycle/install"
)

func runSettingsPublish(ctx context.Context, args []string, input io.Reader, out, diagnostic io.Writer) error {
	flags := flag.NewFlagSet("settings-publish", flag.ContinueOnError)
	flags.SetOutput(diagnostic)
	target := flags.String("target", "", "canonical live settings target path")
	role := flags.String("role", "", "optional OpenCode plugin role: memory or coordination")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 || *target == "" {
		return errors.New("settings-publish requires --target ABS [--role memory|coordination] and no positional arguments")
	}
	raw, err := io.ReadAll(io.LimitReader(input, 1024*1024+1))
	if err != nil {
		return err
	}
	if len(raw) > 1024*1024 {
		return errors.New("settings-publish plan exceeds 1 MiB")
	}
	if err := jsontext.CheckUnicode(raw); err != nil {
		return err
	}
	fields, err := planObject(raw)
	if err != nil || len(fields) != 3 || string(fields["schema"]) != `"cairn.hook-settings-publish/1"` || fields["selection"] == nil || fields["replace_commands"] == nil {
		return errors.Join(err, errors.New("settings-publish plan requires schema cairn.hook-settings-publish/1, selection and replace_commands"))
	}
	selFields, err := planObject(fields["selection"])
	if err != nil || len(selFields) != 2 || selFields["path"] == nil || selFields["sha256"] == nil {
		return errors.Join(err, errors.New("settings-publish selection requires exact path and sha256"))
	}
	var selection install.Generation
	if err := json.Unmarshal(fields["selection"], &selection); err != nil {
		return err
	}
	repFields, err := planObject(fields["replace_commands"])
	if err != nil || len(repFields) != 2 || repFields["memory"] == nil || repFields["coordination"] == nil {
		return errors.Join(err, errors.New("settings-publish replace_commands requires memory and coordination arrays"))
	}
	replaceCommands := map[string][]string{}
	for _, key := range []string{"memory", "coordination"} {
		if bytes.Equal(bytes.TrimSpace(repFields[key]), []byte("null")) {
			return errors.New("replace_commands values must be arrays")
		}
		var rawItems []json.RawMessage
		if err := json.Unmarshal(repFields[key], &rawItems); err != nil {
			return err
		}
		items := make([]string, len(rawItems))
		for i, item := range rawItems {
			if bytes.Equal(bytes.TrimSpace(item), []byte("null")) || json.Unmarshal(item, &items[i]) != nil {
				return errors.New("replace_commands entries must be strings")
			}
		}
		replaceCommands[key] = items
	}
	result, err := install.PublishHookSettings(ctx, install.HookSettingsPublishPlan{
		Target:          *target,
		Role:            *role,
		Selection:       selection,
		ReplaceCommands: replaceCommands,
	})
	if err != nil {
		return err
	}
	return json.NewEncoder(out).Encode(result)
}

func runSettingsReverse(ctx context.Context, args []string, out, diagnostic io.Writer) error {
	flags := flag.NewFlagSet("settings-reverse", flag.ContinueOnError)
	flags.SetOutput(diagnostic)
	var selected install.SettingsRecovery
	flags.StringVar(&selected.Path, "record", "", "canonical recovery record path")
	flags.StringVar(&selected.SHA256, "record-sha256", "", "recovery record SHA-256")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 || selected.Path == "" || selected.SHA256 == "" {
		return errors.New("settings-reverse requires --record ABS --record-sha256 HEX and no positional arguments")
	}
	result, err := install.ReversePublishedSettings(ctx, selected)
	if err != nil {
		return err
	}
	return json.NewEncoder(out).Encode(result)
}
