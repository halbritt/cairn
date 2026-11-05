package main

import (
	"context"
	"encoding/json"
	"flag"
	"io"
	"strings"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/jsontext"
	"github.com/halbritt/cairn/localapi"
)

const waitHelp = `Cairn durable coordination waits (authenticated API; registered session or operator)

Subcommands:
  cairn wait create --request-id UUID --predicate KIND --dependency UUID --deadline RFC3339 [--repo COLLECTION]
  cairn wait get (--wait UUID | --dependency UUID) [--repo COLLECTION]
  cairn wait list [--repo COLLECTION] [--owner UUID] [--state STATE] [--dependency UUID] [--limit N] [--offset N]
  cairn wait cancel --request-id UUID (--wait UUID | --dependency UUID) [--repo COLLECTION]

Common flags: --profile NAME | --token-file FILE, --socket PATH, --repo COLLECTION, --agent-id UUID, --execution-id UUID
`

func waitCommand(ctx context.Context, args []string, input io.Reader, client *localapi.Client) (any, error) {
	if len(args) == 0 || args[0] == "--help" || args[0] == "-h" || args[0] == "help" {
		return commandHelp(waitHelp), nil
	}

	subcommand := args[0]
	subArgs := args[1:]

	f := flags("wait " + subcommand)
	token := f.String("token-file", "", "API token file")
	profile := f.String("profile", "", "provisioned event profile name")
	socket := f.String("socket", "", "API socket")
	repo := f.String("repo", "", "collection repository, defaults to profile")
	collection := f.String("collection", "", "alias for repo")
	session := f.String("session", "", "registered session ref (agent-id:execution-id)")
	agentID := f.String("agent-id", "", "registered session UUID")
	executionID := f.String("execution-id", "", "current session execution UUID")

	// Subcommand specific flags
	requestID := f.String("request-id", "", "stable retry UUID")
	predicate := f.String("predicate", "", "predicate kind (request_response or response_group_closed)")
	dependency := f.String("dependency", "", "dependency event UUID")
	deadlineStr := f.String("deadline", "", "wait deadline (RFC3339)")
	waitUUID := f.String("wait", "", "wait UUID")
	ownerUUID := f.String("owner", "", "owner agent UUID filter")
	state := f.String("state", "", "wait state filter (waiting, satisfied, timed_out, cancelled, restore_fenced)")
	limit := f.Int("limit", 0, "maximum number of waits to list")
	offset := f.Int("offset", 0, "offset for list pagination")

	if err := f.Parse(subArgs); err != nil {
		if err == flag.ErrHelp {
			return commandHelp(waitHelp), nil
		}
		return nil, invalid(err.Error())
	}

	scopeRepo := *repo
	if scopeRepo == "" {
		scopeRepo = *collection
	}

	if *profile != "" {
		if client != nil {
			return nil, invalid("--profile requires a top-level command and cannot combine with --token-file")
		}
		var err error
		*token, err = agentProfileToken(*profile, *token)
		if err != nil {
			return nil, err
		}
	}

	if *session != "" {
		parts := strings.SplitN(*session, ":", 2)
		if len(parts) == 2 {
			if *agentID == "" {
				*agentID = parts[0]
			}
			if *executionID == "" {
				*executionID = parts[1]
			}
		} else if *agentID == "" {
			*agentID = parts[0]
		}
	}

	var sessionSelected bool
	f.Visit(func(option *flag.Flag) {
		if option.Name == "agent-id" || option.Name == "execution-id" || option.Name == "session" {
			sessionSelected = true
		}
	})
	if sessionSelected {
		if err := (core.AgentSessionRef{AgentID: *agentID, ExecutionID: *executionID}).Validate(); err != nil {
			return nil, err
		}
	}

	var sessionRef *core.AgentSessionRef
	if sessionSelected {
		sessionRef = &core.AgentSessionRef{AgentID: *agentID, ExecutionID: *executionID}
	}

	var operation string
	var req any

	switch subcommand {
	case "create":
		if *requestID == "" {
			return nil, invalid("--request-id UUID is required for create")
		}
		if *predicate == "" {
			return nil, invalid("--predicate is required (request_response or response_group_closed)")
		}
		depUUID := *dependency
		if depUUID == "" && f.NArg() > 0 {
			depUUID = f.Arg(0)
		}
		depUUID = strings.TrimPrefix(depUUID, "cairn:")
		if depUUID == "" {
			return nil, invalid("--dependency event UUID is required")
		}
		if *deadlineStr == "" {
			return nil, invalid("--deadline (RFC3339) is required")
		}
		parsedDeadline, err := time.Parse(time.RFC3339Nano, *deadlineStr)
		if err != nil {
			parsedDeadline, err = time.Parse(time.RFC3339, *deadlineStr)
			if err != nil {
				return nil, invalid("invalid deadline timestamp: must be RFC3339")
			}
		}

		operation = "wait-create"
		req = core.CreateWaitRequest{
			RequestID:           *requestID,
			Collection:          scopeRepo,
			Session:             sessionRef,
			PredicateKind:       *predicate,
			DependencyEventUUID: depUUID,
			Deadline:            parsedDeadline,
		}

	case "get":
		targetWait := *waitUUID
		if targetWait == "" && f.NArg() > 0 {
			targetWait = f.Arg(0)
		}
		targetWait = strings.TrimPrefix(targetWait, "cairn:")
		depUUID := strings.TrimPrefix(*dependency, "cairn:")

		if targetWait == "" && depUUID == "" {
			return nil, invalid("--wait UUID or --dependency UUID is required")
		}

		operation = "wait-get"
		req = core.GetWaitRequest{
			WaitUUID:            targetWait,
			DependencyEventUUID: depUUID,
			Collection:          scopeRepo,
			Session:             sessionRef,
		}

	case "list":
		operation = "wait-list"
		req = core.ListWaitsRequest{
			Collection:          scopeRepo,
			OwnerAgentUUID:      *ownerUUID,
			Session:             sessionRef,
			State:               *state,
			DependencyEventUUID: strings.TrimPrefix(*dependency, "cairn:"),
			Limit:               *limit,
			Offset:              *offset,
		}

	case "cancel":
		if *requestID == "" {
			return nil, invalid("--request-id UUID is required for cancel")
		}
		targetWait := *waitUUID
		if targetWait == "" && f.NArg() > 0 {
			targetWait = f.Arg(0)
		}
		targetWait = strings.TrimPrefix(targetWait, "cairn:")
		depUUID := strings.TrimPrefix(*dependency, "cairn:")

		if targetWait == "" && depUUID == "" {
			return nil, invalid("--wait UUID or --dependency UUID is required for cancel")
		}

		operation = "wait-cancel"
		req = core.CancelWaitRequest{
			RequestID:           *requestID,
			WaitUUID:            targetWait,
			DependencyEventUUID: depUUID,
			Collection:          scopeRepo,
			Session:             sessionRef,
		}

	default:
		return nil, invalid("unknown wait subcommand; expected create, get, list, or cancel")
	}

	if err := jsontext.CheckValue(req); err != nil {
		return nil, invalid(err.Error())
	}

	if client == nil {
		connectionArgs := []string{}
		if *token != "" {
			connectionArgs = append(connectionArgs, "--token-file", *token)
		}
		if *socket != "" {
			connectionArgs = append(connectionArgs, "--socket", *socket)
		}
		if sessionSelected {
			connectionArgs = append(connectionArgs, "--agent-id", *agentID, "--execution-id", *executionID)
		}
		encoded, err := json.Marshal(req)
		if err != nil {
			return nil, err
		}
		return agentRequest(ctx, append(connectionArgs, operation), strings.NewReader(string(encoded)))
	}

	var result json.RawMessage
	if err := client.Call(ctx, operation, req, &result); err != nil {
		return nil, err
	}
	return result, nil
}
