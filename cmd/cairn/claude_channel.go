package main

import (
	"context"
	"errors"
	"flag"
	"path/filepath"

	"github.com/halbritt/cairn/internal/claudechannel"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// serveClaudeChannel runs the local MCP stdio channel bridge for one Claude
// process. It owns stdio and therefore bypasses the ordinary response envelope.
func serveClaudeChannel(ctx context.Context, args []string) error {
	f := flags("claude-channel")
	directory := f.String("directory", "", "absolute owner-only directory for the socket and registry")
	if err := f.Parse(args); err != nil {
		return err
	}
	if f.NArg() != 0 || *directory == "" {
		return flag.ErrHelp
	}
	if !filepath.IsAbs(*directory) {
		return errors.New("claude-channel requires an absolute --directory")
	}
	return claudechannel.Run(ctx, *directory, &mcp.StdioTransport{})
}
