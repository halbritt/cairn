package main

import (
	"context"

	"github.com/halbritt/cairn/core"
)

const swarmHelp = `cairn swarm: the swarm view, one live page per campaign (local operator)

  cairn swarm serve --collection REPO [--port PORT] [--token-file PATH] [--bindings DIR] [--plane-url TEMPLATE]
                    [--tailnet-socket PATH --tailnet-origin URL --tailnet-login LOGIN --tailnet-clients PATH]
      Serve the page on 127.0.0.1 (default port 8087) until interrupted. The
      page signs in with the token in the token file. A missing token file is
      created with mode 0600 in the Cairn state directory.
      The four tailnet flags come together. They add an address on the
      owner's tailnet: a Unix socket for Tailscale Serve to proxy to, the
      https address of the page there, the one tailnet login that may open
      it, and a file that lists the tailnet addresses of the devices it may
      be opened from. That address needs no token.
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
