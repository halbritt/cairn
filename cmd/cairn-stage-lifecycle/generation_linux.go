package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"

	"github.com/halbritt/cairn/internal/jsontext"
	"github.com/halbritt/cairn/internal/lifecycle/install"
)

func runCommand(ctx context.Context, args []string, input io.Reader, out, diagnostic io.Writer) error {
	if len(args) == 0 || args[0] != "generation" && args[0] != "inspect-generation" {
		return run(ctx, args, out, diagnostic)
	}
	flags := flag.NewFlagSet(args[0], flag.ContinueOnError)
	flags.SetOutput(diagnostic)
	if args[0] == "inspect-generation" {
		selected := install.Generation{}
		flags.StringVar(&selected.Path, "path", "", "independently selected immutable manifest path")
		flags.StringVar(&selected.SHA256, "sha256", "", "independently selected manifest SHA-256")
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if flags.NArg() != 0 || selected.Path == "" || selected.SHA256 == "" {
			return errors.New("inspect-generation requires --path and --sha256 without positional arguments")
		}
		files, err := install.ReadGeneration(ctx, selected)
		if err != nil {
			return err
		}
		return json.NewEncoder(out).Encode(struct {
			Selection install.Generation `json:"selection"`
			Files     []install.File     `json:"files"`
		}{selected, files})
	}
	root := flags.String("root", "", "existing private install root; never a production transition grant")
	if err := flags.Parse(args[1:]); err != nil {
		return err
	}
	if flags.NArg() != 0 || *root == "" {
		return errors.New("generation requires --root without positional arguments and a pinned-file plan on stdin")
	}
	raw, err := io.ReadAll(io.LimitReader(input, 1024*1024+1))
	if err != nil {
		return err
	}
	if len(raw) > 1024*1024 {
		return errors.New("generation plan exceeds 1 MiB")
	}
	if err := jsontext.CheckUnicode(raw); err != nil {
		return err
	}
	plan, err := planObject(raw)
	if err != nil {
		return err
	}
	if len(plan) != 2 || plan["id"] == nil || plan["files"] == nil {
		return errors.New("generation plan requires exactly id and files")
	}
	var id string
	var entries []json.RawMessage
	if err := json.Unmarshal(plan["id"], &id); err != nil {
		return err
	}
	if err := json.Unmarshal(plan["files"], &entries); err != nil {
		return err
	}
	files := make([]install.File, 0, len(entries))
	for _, raw := range entries {
		entry, err := planObject(raw)
		if err != nil {
			return err
		}
		for _, key := range []string{"name", "path", "sha256"} {
			if entry[key] == nil {
				return fmt.Errorf("generation file requires %s", key)
			}
		}
		for key := range entry {
			if key != "name" && key != "path" && key != "sha256" && key != "executable" {
				return errors.New("unknown generation file field")
			}
		}
		var file install.File
		if err := json.Unmarshal(raw, &file); err != nil {
			return err
		}
		files = append(files, file)
	}
	selected, err := install.StageGeneration(ctx, *root, id, files)
	if err != nil {
		return err
	}
	return json.NewEncoder(out).Encode(selected)
}

func planObject(raw []byte) (map[string]json.RawMessage, error) {
	d := json.NewDecoder(bytes.NewReader(raw))
	start, err := d.Token()
	if err != nil || start != json.Delim('{') {
		return nil, errors.New("generation plan/member must be an object")
	}
	fields := map[string]json.RawMessage{}
	for d.More() {
		key, err := d.Token()
		if err != nil {
			return nil, err
		}
		name, ok := key.(string)
		if !ok || fields[name] != nil {
			return nil, errors.New("duplicate or invalid generation plan field")
		}
		var value json.RawMessage
		if err := d.Decode(&value); err != nil {
			return nil, err
		}
		fields[name] = value
	}
	if end, err := d.Token(); err != nil || end != json.Delim('}') {
		return nil, errors.New("invalid generation plan object")
	}
	if _, err := d.Token(); err != io.EOF {
		return nil, errors.New("generation plan contains trailing input")
	}
	return fields, nil
}
