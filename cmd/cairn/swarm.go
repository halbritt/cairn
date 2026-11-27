package main

import (
	"context"

	"github.com/halbritt/cairn/core"
)

const swarmHelp = `cairn swarm: the swarm view, one live page per campaign (local operator)

  cairn swarm serve --collection REPO [--port PORT] [--token-file PATH] [--bindings DIR]
      Serve the page on 127.0.0.1 (default port 8087) until interrupted. The
      page signs in with the token in the token file. A missing token file is
      created with mode 0600 in the Cairn state directory.
  cairn swarm url [--port PORT] [--token-file PATH]
      Print the page address with the token in the URL fragment.

The view reads stored coordination state and the Herdr panes of this host. It
sends nothing to an agent unless the owner uses an action on the page.
See docs/swarm-view.md.
`

func swarmHelpRequested(args []string) bool {
	if len(args) == 0 {
		return true
	}
	for _, arg := range args {
		if arg == "--help" || arg == "-h" || arg == "help" {
			return true
		}
	}
	return false
}

// swarmCommand runs a swarm subcommand that needs the operator store.
func swarmCommand(ctx context.Context, store *core.Store, args []string) (any, error) {
	switch args[0] {
	case "serve":
		return runSwarmServe(ctx, store, args[1:])
	}
	return nil, invalid("unknown swarm subcommand; run cairn swarm --help")
}
