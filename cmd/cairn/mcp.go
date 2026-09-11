package main

import (
	"context"
	"flag"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
	"github.com/halbritt/cairn/mcpapi"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"os"
)

type mcpOptions struct {
	socket string
	token  string
	config mcpapi.Config
	pins   core.ContextPins
}

func mcpFlags(f *flag.FlagSet) *mcpOptions {
	o := &mcpOptions{}
	f.StringVar(&o.socket, "socket", "", "Cairn Unix socket (required)")
	f.StringVar(&o.token, "token-file", "", "owner-only ordinary agent token (required)")
	f.StringVar(&o.config.Scope.Repo, "repo", "", "repository identity (required)")
	f.StringVar(&o.config.Scope.TaskID, "task", "", "host task identity (required unless --codex-thread)")
	f.StringVar(&o.config.Scope.RunID, "run", "", "host run identity (required unless --codex-thread)")
	f.BoolVar(&o.config.CodexThread, "codex-thread", false, "group searches by Codex tool-call thread metadata instead of --task/--run")
	f.IntVar(&o.config.AvailableTokens, "tokens", 32000, "memory input room per tool result")
	f.StringVar(&o.pins.Revision, "revision", "", "declared repository revision")
	f.StringVar(&o.pins.WorkspaceSHA256, "workspace-sha256", "", "declared workspace digest")
	f.StringVar(&o.pins.TaskClass, "task-class", "", "task category")
	f.StringVar(&o.pins.TaskPhase, "task-phase", "", "declared task phase (exact label)")
	f.StringVar(&o.pins.BindingID, "binding", "", "binding identity")
	f.StringVar(&o.pins.CapabilityID, "capability", "", "capability identity")
	return o
}

func (o *mcpOptions) validate(positional int) error {
	if positional != 0 || o.socket == "" || o.token == "" {
		return invalid("MCP requires --socket and --token-file and accepts no positional arguments")
	}
	if err := validateHarnessText(o.socket, o.token); err != nil {
		return err
	}
	if o.pins != (core.ContextPins{}) {
		o.config.Context = &o.pins
	}
	return o.config.Validate()
}

func serveMCP(ctx context.Context, args []string) error {
	f := flags("mcp")
	o := mcpFlags(f)
	if err := parseHarnessFlags(f, args, os.Stdout); err != nil {
		return err
	}
	if err := o.validate(f.NArg()); err != nil {
		return err
	}
	client, err := localapi.NewClient(o.socket, o.token)
	if err != nil {
		return err
	}
	defer client.Close()
	server, err := mcpapi.NewServer(client, o.config)
	if err != nil {
		return err
	}
	return server.Run(ctx, &mcp.StdioTransport{})
}
