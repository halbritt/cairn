package main

import (
	"context"
	"flag"
	"path/filepath"

	"github.com/halbritt/cairn/localapi"
)

const relayHelp = `Cairn local socket relay

  cairn relay --upstream https://HOST[:PORT] [--socket PATH]

Forwards provisioned bearer and session headers to one verified HTTPS origin.
The relay has no credentials, offline cache, redirects or automatic retries.
UPSTREAM_UNAVAILABLE means no upstream connection was established.
UPSTREAM_UNCERTAIN means effects may have committed; retain request IDs and
arguments for reconciliation. The central API enforces remote permissions.
`

func relayRemote(ctx context.Context, args []string) error {
	directory, err := dataDirectory()
	if err != nil {
		return err
	}
	f := flags("relay")
	socket := f.String("socket", filepath.Join(directory, "api.sock"), "private Unix socket")
	upstream := f.String("upstream", "", "verified HTTPS API origin")
	if err = f.Parse(args); err != nil {
		if err == flag.ErrHelp {
			return nil
		}
		return invalid(err.Error())
	}
	if f.NArg() != 0 {
		return invalid("unexpected relay arguments")
	}
	relay, err := localapi.NewRelay(*upstream)
	if err != nil {
		return err
	}
	defer relay.Close()
	listener, err := listenPrivateUnix(*socket)
	if err != nil {
		return err
	}
	defer listener.Close()
	return serveAPIs(ctx, []apiEndpoint{{listener: listener, handler: relay}})
}
