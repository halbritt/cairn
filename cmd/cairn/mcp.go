package main

import (
	"context"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
	"github.com/halbritt/cairn/mcpapi"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func serveMCP(ctx context.Context, args []string) error {
	f := flags("mcp")
	socket := f.String("socket", "", "Cairn Unix socket (required)")
	token := f.String("token-file", "", "owner-only ordinary agent token (required)")
	repo := f.String("repo", "", "repository identity (required)")
	task := f.String("task", "", "host task identity (required)")
	run := f.String("run", "", "host run identity (required)")
	room := f.Int("tokens", 32000, "memory input room per tool result")
	revision := f.String("revision", "", "declared repository revision")
	workspace := f.String("workspace-sha256", "", "declared workspace digest")
	taskClass := f.String("task-class", "", "task category")
	binding := f.String("binding", "", "binding identity")
	capability := f.String("capability", "", "capability identity")
	if err := f.Parse(args); err != nil {
		return err
	}
	if f.NArg() != 0 || *socket == "" || *token == "" {
		return invalid("mcp requires --socket and --token-file and accepts no positional arguments")
	}
	client, err := localapi.NewClient(*socket, *token)
	if err != nil {
		return err
	}
	defer client.Close()
	config := mcpapi.Config{Scope: core.Scope{Repo: *repo, TaskID: *task, RunID: *run}, AvailableTokens: *room}
	if *revision != "" || *workspace != "" || *taskClass != "" || *binding != "" || *capability != "" {
		config.Context = &core.ContextPins{Revision: *revision, WorkspaceSHA256: *workspace, TaskClass: *taskClass, BindingID: *binding, CapabilityID: *capability}
	}
	server, err := mcpapi.NewServer(client, config)
	if err != nil {
		return err
	}
	return server.Run(ctx, &mcp.StdioTransport{})
}
