package main

import (
	"context"
	"errors"
	"flag"
	"os"

	"github.com/halbritt/cairn/internal/wakeup"
)

const wakeHelp = `Cairn automatic request workers (systemd user host)
  cairn wake check --config /absolute/binding.json
  cairn wake serve --config /absolute/binding.json
  cairn wake worker --config /absolute/binding.json --attempt UUID
Inspect with agent wake-attempts < query.json. See docs/agent-wakeups.md.
Only owner-configured commands launch. Worker is an internal one-shot entry point.
`

func wakeCommand(ctx context.Context, args []string) (any, error) {
	if len(args) == 0 || args[0] == "--help" {
		return commandHelp(wakeHelp), nil
	}
	op := args[0]
	f := flags("wake " + op)
	config := f.String("config", "", "binding file")
	attempt := f.String("attempt", "", "attempt UUID")
	if err := f.Parse(args[1:]); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return commandHelp(wakeHelp), nil
		}
		return nil, invalid(err.Error())
	}
	if f.NArg() != 0 {
		return nil, invalid("unexpected wake arguments")
	}
	switch op {
	case "check":
		_, err := wakeup.ReadConfig(*config)
		return map[string]bool{"valid": err == nil}, err
	case "serve":
		if *attempt != "" {
			return nil, invalid("serve does not accept attempt")
		}
		return nil, wakeup.Serve(ctx, *config, os.Stderr)
	case "worker":
		return nil, wakeup.Worker(ctx, *config, *attempt)
	default:
		return nil, invalid("wake requires serve or worker")
	}
}
