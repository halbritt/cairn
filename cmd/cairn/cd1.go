package main

import (
	"encoding/json"
	"flag"
	"io"
	"os"
	"strings"
	"time"

	"github.com/halbritt/cairn/core"
)

const cd1Help = `cairn cd1 (validate|render) [FLAGS]

Validate or render a cd1 (or cairn-c/1) message read from stdin.
Structural validation only — not proof of factual truth, source fidelity, or authority.

Subcommands:
  cairn cd1 validate [--parent-context FILE] [--thread-id ID] [--now RFC3339] [--envelope-kind KIND] [--envelope-action ACTION]
  cairn cd1 render   [--parent-context FILE] [--thread-id ID] [--now RFC3339] [--envelope-kind KIND] [--envelope-action ACTION] [--expand-on-missing]
`

func cd1Command(args []string, input io.Reader) (any, error) {
	if len(args) == 0 || args[0] == "--help" || args[0] == "-h" || args[0] == "help" {
		return commandHelp(cd1Help), nil
	}
	sub := args[0]
	if sub != "validate" && sub != "render" {
		return nil, invalid("unknown cd1 subcommand; expected validate or render")
	}
	f := flags("cd1 " + sub)
	parentContextPath := f.String("parent-context", "", "JSON file containing thread-scoped parent context")
	threadID := f.String("thread-id", "", "expected thread_id for cross-thread handle validation")
	nowStr := f.String("now", "", "override RFC3339 timestamp for expiry checks")
	envKind := f.String("envelope-kind", "", "Cairn envelope kind for header derivation / cross-check")
	envAction := f.String("envelope-action", "", "Cairn envelope action (none|review|decide) for cross-check")
	expandMissing := f.Bool("expand-on-missing", false, "with render, return an explicit cd1 expand request on missing/expired/conflicting context")
	if err := f.Parse(args[1:]); err != nil {
		if err == flag.ErrHelp {
			return commandHelp(cd1Help), nil
		}
		return nil, invalid(err.Error())
	}
	if f.NArg() != 0 {
		return nil, invalid("cd1 " + sub + " takes no positional arguments; provide message on stdin")
	}
	var parentCtx *core.CD1ParentContext
	if *parentContextPath != "" {
		raw, err := os.ReadFile(*parentContextPath)
		if err != nil {
			return nil, invalid("failed to read --parent-context: " + err.Error())
		}
		var ctx core.CD1ParentContext
		if err := json.Unmarshal(raw, &ctx); err != nil {
			return nil, invalid("invalid --parent-context JSON: " + err.Error())
		}
		parentCtx = &ctx
	}
	var nowPtr *time.Time
	if *nowStr != "" {
		parsed, err := time.Parse(time.RFC3339Nano, *nowStr)
		if err != nil {
			return nil, invalid("invalid --now RFC3339 timestamp")
		}
		nowPtr = &parsed
	}
	rawBody, err := io.ReadAll(io.LimitReader(input, 65537))
	if err != nil {
		return nil, err
	}
	if len(rawBody) > 65536 {
		return nil, invalid("cd1 message exceeds 65536 bytes")
	}
	opts := core.CD1ValidateOptions{
		ParentContext:  parentCtx,
		ThreadID:       *threadID,
		EnvelopeKind:   *envKind,
		EnvelopeAction: *envAction,
		Now:            nowPtr,
	}
	body := string(rawBody)
	if sub == "validate" {
		res := core.ValidateCD1Message(body, opts)
		if !res.Valid {
			return nil, invalid("invalid cd1 message: " + strings.Join(res.Errors, "; "))
		}
		return map[string]any{
			"schema": "cairn.cd1-validation/1",
			"valid":  true,
			"proto":  res.Proto,
			"parsed": res.Parsed,
		}, nil
	}
	if *expandMissing {
		rendered, expanded := core.RenderCD1OrExpandFallback(body, opts, "", "", "")
		return map[string]any{
			"schema":        "cairn.cd1-render/1",
			"expanded":      expanded,
			"rendered_text": rendered,
		}, nil
	}
	rendered, err := core.RenderCD1Human(body, opts)
	if err != nil {
		return nil, invalid(err.Error())
	}
	return map[string]any{
		"schema":        "cairn.cd1-render/1",
		"expanded":      false,
		"rendered_text": rendered,
	}, nil
}
