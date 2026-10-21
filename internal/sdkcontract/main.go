// Command sdkcontract emits the two source-authored retrieval tool declarations.
// It uses only an in-memory MCP transport and never invokes a tool or API.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/mcpapi"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func run() error {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	server, err := mcpapi.NewServer(nil, mcpapi.Config{Scope: core.Scope{Repo: "contract", TaskID: "contract", RunID: "contract"}, AvailableTokens: 32000, MaxTokens: 32000})
	if err != nil {
		return err
	}
	clientTransport, serverTransport := mcp.NewInMemoryTransports()
	serverSession, err := server.Connect(ctx, serverTransport, nil)
	if err != nil {
		return err
	}
	defer serverSession.Close()
	client, err := mcp.NewClient(&mcp.Implementation{Name: "cairn-source-contract", Version: "1"}, nil).Connect(ctx, clientTransport, nil)
	if err != nil {
		return err
	}
	defer client.Close()
	listed, err := client.ListTools(ctx, nil)
	if err != nil {
		return err
	}
	selected := []*mcp.Tool{}
	for _, tool := range listed.Tools {
		if tool.Name == "cairn_search" || tool.Name == "cairn_pull" {
			selected = append(selected, tool)
		}
	}
	if len(selected) != 2 {
		return fmt.Errorf("retrieval contract missing")
	}
	return json.NewEncoder(os.Stdout).Encode(selected)
}
func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
