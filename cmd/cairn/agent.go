package main

import (
	"context"
	"encoding/json"
	"io"
	"path/filepath"

	"github.com/halbritt/cairn/localapi"
)

func agentRequest(ctx context.Context, args []string, input io.Reader) (any, error) {
	directory, err := dataDirectory()
	if err != nil {
		return nil, err
	}
	f := flags("agent")
	tokenFile := f.String("token-file", filepath.Join(directory, "agent.token"), "owner-only API token file")
	socket := f.String("socket", filepath.Join(directory, "api.sock"), "Cairn Unix socket")
	if err = f.Parse(args); err != nil {
		return nil, invalid(err.Error())
	}
	if f.NArg() < 1 {
		return nil, invalid("agent requires an API operation and JSON on stdin")
	}
	operation := f.Arg(0)
	commandArgs := operation == "run" || operation == "search" || operation == "pull" || operation == "pull-evidence"
	if !commandArgs && f.NArg() != 1 {
		return nil, invalid("agent operation requires one JSON request on stdin")
	}
	switch operation {
	case "search", "pull", "pull-evidence":
	case "run", "run-status", "register-context", "check-evidence", "refusal", "index", "expand", "expand-evidence", "create", "edit", "delete", "compile", "get", "usage", "usage-coverage", "evidence", "spawn", "terminal", "task-state", "bind-run", "link-run-retrieval", "claim-run", "delivery", "outcome", "assess-run", "use-report", "run-report", "conflict", "conflicts", "supersede", "supersession", "preview-retract":
	default:
		return nil, invalid("unknown agent operation")
	}
	client, err := localapi.NewClient(*socket, *tokenFile)
	if err != nil {
		return nil, err
	}
	defer client.Close()
	if operation == "run" {
		return runTask(ctx, client, f.Args()[1:])
	}
	if operation == "search" {
		return agentSearch(ctx, client, f.Args()[1:], *socket, *tokenFile)
	}
	if operation == "pull" || operation == "pull-evidence" {
		return agentPull(ctx, client, operation, f.Args()[1:])
	}
	body, err := io.ReadAll(io.LimitReader(input, 128*1024+1))
	if err != nil {
		return nil, err
	}
	if len(body) > 128*1024 {
		return nil, invalid("request exceeds 128 KiB")
	}
	if !json.Valid(body) {
		return nil, invalid("expected a JSON request")
	}
	var result json.RawMessage
	if err = client.Call(ctx, operation, json.RawMessage(body), &result); err != nil {
		return nil, err
	}
	return result, nil
}
