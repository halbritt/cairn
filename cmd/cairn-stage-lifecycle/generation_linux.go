package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"path/filepath"

	"github.com/halbritt/cairn/internal/jsontext"
	"github.com/halbritt/cairn/internal/lifecycle/install"
)

func runCommand(ctx context.Context, args []string, input io.Reader, out, diagnostic io.Writer) error {
	if len(args) != 0 && args[0] == "recover-agy-layer" {
		return runAgyLayerRecovery(ctx, args[1:], out, diagnostic)
	}
	if len(args) != 0 && args[0] == "agy-layer" {
		return runAgyLayer(ctx, args[1:], input, out, diagnostic)
	}
	if len(args) == 0 || args[0] != "generation" && args[0] != "inspect-generation" && args[0] != "directory" && args[0] != "inspect-directory" {
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
	identityKey := "id"
	if args[0] != "generation" {
		identityKey = "identity"
	}
	id, files, err := filePlan(raw, identityKey)
	if err != nil {
		return err
	}
	if args[0] != "generation" {
		selected := install.Directory{Path: filepath.Join(*root, id), Identity: id}
		if args[0] == "inspect-directory" {
			verified, err := install.ReadDirectory(ctx, selected, files)
			if err != nil {
				return err
			}
			return json.NewEncoder(out).Encode(struct {
				Selection install.Directory `json:"selection"`
				Files     []install.File    `json:"files"`
			}{selected, verified})
		}
		selected, err := install.StageDirectory(ctx, *root, id, files)
		if err != nil {
			return err
		}
		return json.NewEncoder(out).Encode(selected)
	}
	selected, err := install.StageGeneration(ctx, *root, id, files)
	if err != nil {
		return err
	}
	return json.NewEncoder(out).Encode(selected)
}

func filePlan(raw []byte, identityKey string) (string, []install.File, error) {
	plan, err := planObject(raw)
	if err != nil {
		return "", nil, err
	}
	if len(plan) != 2 || plan[identityKey] == nil || plan["files"] == nil {
		return "", nil, fmt.Errorf("file plan requires exactly %s and files", identityKey)
	}
	var id string
	var entries []json.RawMessage
	if err := json.Unmarshal(plan[identityKey], &id); err != nil {
		return "", nil, err
	}
	if err := json.Unmarshal(plan["files"], &entries); err != nil {
		return "", nil, err
	}
	files := make([]install.File, 0, len(entries))
	for _, raw := range entries {
		entry, err := planObject(raw)
		if err != nil {
			return "", nil, err
		}
		for _, key := range []string{"name", "path", "sha256"} {
			if entry[key] == nil {
				return "", nil, fmt.Errorf("generation file requires %s", key)
			}
		}
		for key := range entry {
			if key != "name" && key != "path" && key != "sha256" && key != "executable" {
				return "", nil, errors.New("unknown generation file field")
			}
		}
		if raw, present := entry["executable"]; present && !bytes.Equal(bytes.TrimSpace(raw), []byte("true")) && !bytes.Equal(bytes.TrimSpace(raw), []byte("false")) {
			return "", nil, errors.New("generation executable field must be a boolean")
		}
		var file install.File
		if err := json.Unmarshal(raw, &file); err != nil {
			return "", nil, err
		}
		files = append(files, file)
	}
	return id, files, nil
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
