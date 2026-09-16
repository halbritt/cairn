package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"path/filepath"

	"github.com/halbritt/cairn/internal/buildinfo"
	"github.com/halbritt/cairn/localapi"
)

func agentRequest(ctx context.Context, args []string, input io.Reader) (any, error) {
	f := flags("agent")
	tokenFile := f.String("token-file", "", "owner-only API token file")
	socket := f.String("socket", "", "Cairn Unix socket")
	if err := f.Parse(args); err != nil {
		if err == flag.ErrHelp {
			return commandHelp(agentHelp), nil
		}
		return nil, invalid(err.Error())
	}
	if f.NArg() >= 2 && (f.Arg(1) == "--help" || f.Arg(1) == "-h") {
		if f.NArg() != 2 {
			return nil, invalid("operation help takes no additional arguments")
		}
		help, err := agentOperationHelp(f.Arg(0))
		if err != nil {
			return nil, err
		}
		return help, nil
	}
	provided := map[string]bool{}
	f.Visit(func(fl *flag.Flag) { provided[fl.Name] = true })
	socketPath := *socket
	tokenPath := *tokenFile
	if provided["socket"] && socketPath == "" {
		return nil, invalid("empty agent socket path")
	}
	if provided["token-file"] && tokenPath == "" {
		return nil, invalid("empty agent token file path")
	}
	if !provided["socket"] || !provided["token-file"] {
		directory, err := dataDirectory()
		if err != nil {
			return nil, err
		}
		if !provided["socket"] {
			socketPath = filepath.Join(directory, "api.sock")
		}
		if !provided["token-file"] {
			tokenPath = filepath.Join(directory, "agent.token")
		}
	}
	if f.NArg() < 1 {
		return nil, invalid("agent requires an API operation and JSON on stdin")
	}
	operation := f.Arg(0)
	commandArgs := isEventCommand(operation) || operation == "evidence" || operation == "remember" || operation == "run" || operation == "start" || operation == "search" || operation == "pull" || operation == "pull-evidence"
	if !commandArgs && f.NArg() != 1 {
		return nil, invalid("agent operation requires one JSON request on stdin")
	}
	switch operation {
	case "event-list", "event-inspect", "event-metrics", "event-publish", "event-next", "event-complete", "event-retry", "event-renew", "event-subscribe", "event-subscriptions", "publish", "inbox", "ack", "complete", "retry", "renew", "subscribe", "unsubscribe", "subscriptions", "events", "event-status", "event-stats":
	case "version", "remember", "search", "start", "pull", "pull-evidence", "run-package", "run-index", "revise", "append", "replace", "cite", "assessments", "history", "recompile":
	case "run", "run-status", "register-context", "check-evidence", "evidence-impact", "refusal", "index", "expand", "expand-evidence", "create", "edit", "delete", "compile", "get", "usage", "usage-coverage", "evidence", "spawn", "terminal", "task-state", "bind-run", "link-run-retrieval", "claim-run", "delivery", "outcome", "assess-run", "use-report", "run-report", "conflict", "conflicts", "supersede", "supersession", "preview-retract":
	default:
		return nil, invalid("unknown agent operation")
	}
	client, err := localapi.NewClient(socketPath, tokenPath)
	if err != nil {
		return nil, err
	}
	defer client.Close()
	if isEventCommand(operation) {
		return eventCommand(ctx, operation, f.Args()[1:], input, client)
	}
	if operation == "version" {
		var server buildinfo.Info
		if err := client.Call(ctx, "version", struct{}{}, &server); err != nil {
			return nil, err
		}
		return struct {
			Schema string         `json:"schema"`
			Client buildinfo.Info `json:"client"`
			Server buildinfo.Info `json:"server"`
		}{"cairn.version/1", buildinfo.Read(), server}, nil
	}
	if operation == "evidence" && f.NArg() > 1 {
		req, err := evidenceFileRequest(f.Args()[1:])
		if err != nil {
			return nil, err
		}
		var result json.RawMessage
		if err := client.Call(ctx, "evidence", req, &result); err != nil {
			return nil, err
		}
		return result, nil
	}
	if operation == "remember" {
		req, err := rememberRequest(f.Args()[1:], input)
		if err != nil {
			return nil, err
		}
		var result json.RawMessage
		if err := client.Call(ctx, "create", req, &result); err != nil {
			return nil, err
		}
		return result, nil
	}
	if operation == "run" {
		return runTask(ctx, client, f.Args()[1:])
	}
	if operation == "start" {
		return prepareAgentStart(ctx, client, f.Args()[1:])
	}
	if operation == "search" {
		return agentSearch(ctx, client, f.Args()[1:], socketPath, tokenPath)
	}
	if operation == "pull" || operation == "pull-evidence" {
		if f.NArg() > 1 {
			return agentPull(ctx, client, operation, f.Args()[1:])
		}
		if operation == "pull" {
			operation = "expand"
		} else {
			operation = "expand-evidence"
		}
	}
	limit := localapi.RequestBodyLimit(operation)
	body, err := io.ReadAll(io.LimitReader(input, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(body)) > limit {
		return nil, invalid(fmt.Sprintf("request exceeds %d KiB", limit/1024))
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
