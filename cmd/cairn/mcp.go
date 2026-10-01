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
	socket  string
	token   string
	harness string
	config  mcpapi.Config
	pins    core.ContextPins
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
	f.StringVar(&o.harness, "client-harness", "", "optional harness label reported in client observations (codex, claude, opencode, hermes, agy, other); reported, never verified or inferred")
	return o
}

func (o *mcpOptions) validate(positional int) error {
	if positional != 0 || o.socket == "" || o.token == "" {
		return invalid("MCP requires --socket and --token-file and accepts no positional arguments")
	}
	if err := validateHarnessText(o.socket, o.token); err != nil {
		return err
	}
	if o.harness != "" && !map[string]bool{"codex": true, "claude": true, "opencode": true, "hermes": true, "agy": true, "other": true}[o.harness] {
		return invalid("--client-harness must be one of codex, claude, opencode, hermes, agy, other")
	}
	if o.pins != (core.ContextPins{}) {
		o.config.Context = &o.pins
	}
	return o.config.Validate()
}

// declaration is what this MCP process reports about itself. The harness is
// only ever the explicitly configured label; absent configuration is unknown.
func (o *mcpOptions) declaration() localapi.ClientDiagnostics {
	harness := o.harness
	if harness == "" {
		harness = "unknown"
	}
	return localapi.ClientDiagnostics{Surface: "mcp", Harness: harness, RetrievalCapabilities: localapi.CurrentRetrievalCapabilities()}
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
	// The facade declares itself once, at construction, so replacing the installed
	// executable never changes what an already running process reports.
	declared := client.WithDiagnostics(o.declaration())
	server, err := mcpapi.NewServer(declared, o.config)
	if err != nil {
		return err
	}
	return server.Run(ctx, &mcp.StdioTransport{})
}
