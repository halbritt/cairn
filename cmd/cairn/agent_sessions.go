package main

import (
	"context"
	"encoding/json"
	"flag"
	"strings"

	"github.com/halbritt/cairn/core"
)

const agentsHelp = `Cairn live agent sessions (existing trusted-host profile)

Common connection flags: --profile NAME | --token-file FILE, --socket PATH
No additional credentials are created. UUIDs select registered sessions/inboxes.

  agents register --request-id UUID --binding NAME --native-session ID
    --harness NAME --project NAME --workspace /absolute/path
    [--model MODEL] [--observed-model MODEL] [--task TEXT] [--state idle|busy]
    [--delivery-mode existing-session|fresh-worker] [--repo COLLECTION]
  agents context --request-id UUID --agent-id UUID --execution-id UUID
    --expected-revision N --harness NAME --project NAME --workspace /absolute/path
    [same metadata flags as register]
  agents heartbeat --agent-id UUID --execution-id UUID
  agents leave --agent-id UUID --execution-id UUID
  agents list [--harness NAME] [--project NAME] [--model MODEL] [--workspace PATH]
    [--state idle|busy] [--delivery-mode MODE] [--agent-id UUID] [--include-offline]
    [--repo COLLECTION] [--after ORDINAL] [--limit N]

register with a new request ID resumes the same binding/native-session pair with
a new execution UUID; old executions are fenced. Use heartbeat every 30 seconds.
Presence expires after 90 seconds. Metadata is reported context, not attestation.
Ordinary event commands accept --agent-id UUID --execution-id UUID to select the
session inbox using its existing profile token. Pass flags before positional values.
`

func agentsCommand(ctx context.Context, args []string) (any, error) {
	if len(args) == 0 || args[0] == "--help" || args[0] == "-h" {
		return commandHelp(agentsHelp), nil
	}
	op := args[0]
	f := flags("agents " + op)
	profile := f.String("profile", "", "existing event profile")
	token := f.String("token-file", "", "existing profile token file")
	socket := f.String("socket", "", "API socket")
	repo := f.String("repo", "", "collection repository")
	request := f.String("request-id", "", "retry UUID")
	binding := f.String("binding", "", "launcher/account namespace")
	native := f.String("native-session", "", "native conversation ID")
	agentID := f.String("agent-id", "", "registered agent UUID")
	executionID := f.String("execution-id", "", "current execution UUID")
	revision := f.Int64("expected-revision", 0, "current context revision")
	harness := f.String("harness", "", "harness name")
	model := f.String("model", "", "configured model")
	observed := f.String("observed-model", "", "model reported by the harness")
	project := f.String("project", "", "actual project name")
	workspace := f.String("workspace", "", "actual absolute workspace")
	task := f.String("task", "", "selected task summary")
	state := f.String("state", "", "idle or busy")
	mode := f.String("delivery-mode", "", "existing-session or fresh-worker")
	all := f.Bool("include-offline", false, "include offline and stopped sessions")
	after := f.Int64("after", 0, "exclusive ordinal cursor")
	limit := f.Int("limit", 0, "page size")
	if err := f.Parse(args[1:]); err != nil {
		if err == flag.ErrHelp {
			return commandHelp(agentsHelp), nil
		}
		return nil, invalid(err.Error())
	}
	allowed := " profile token-file socket "
	switch op {
	case "register":
		allowed += "repo request-id binding native-session harness model observed-model project workspace task state delivery-mode "
	case "context":
		allowed += "request-id agent-id execution-id expected-revision harness model observed-model project workspace task state delivery-mode "
	case "heartbeat", "leave":
		allowed += "agent-id execution-id "
	case "list":
		allowed += "repo agent-id harness model project workspace state delivery-mode include-offline after limit "
	default:
		return nil, invalid("agents requires register, context, heartbeat, leave or list")
	}
	var unsupported string
	f.Visit(func(option *flag.Flag) {
		if !strings.Contains(allowed, " "+option.Name+" ") {
			unsupported = option.Name
		}
	})
	if unsupported != "" || f.NArg() != 0 {
		return nil, invalid("unexpected agent session argument: " + unsupported)
	}
	var req any
	operation := "agent-" + op
	ref := core.AgentSessionRef{AgentID: *agentID, ExecutionID: *executionID}
	metadata := core.AgentMetadata{Harness: *harness, Model: *model, ObservedModel: *observed, Project: *project, Workspace: *workspace, TaskSummary: *task, State: *state, DeliveryMode: *mode}
	if metadata.State == "" {
		metadata.State = "idle"
	}
	if metadata.DeliveryMode == "" {
		metadata.DeliveryMode = "existing-session"
	}
	switch op {
	case "register":
		req = core.RegisterAgentRequest{RequestID: *request, Repo: *repo, Binding: *binding, NativeSessionID: *native, Metadata: metadata}
	case "context":
		req = core.UpdateAgentRequest{RequestID: *request, Session: ref, ExpectedRevision: *revision, Metadata: metadata}
	case "heartbeat", "leave":
		req = ref
	case "list":
		operation = "agent-directory"
		req = core.AgentDirectoryQuery{Repo: *repo, AgentID: *agentID, Harness: *harness, Model: *model, Project: *project, Workspace: *workspace, State: *state, DeliveryMode: *mode, IncludeOffline: *all, After: *after, Limit: *limit}
	}
	path, err := agentProfileToken(*profile, *token)
	if err != nil {
		return nil, err
	}
	connection := []string{}
	if path != "" {
		connection = append(connection, "--token-file", path)
	}
	if *socket != "" {
		connection = append(connection, "--socket", *socket)
	}
	encoded, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}
	return agentRequest(ctx, append(connection, operation), strings.NewReader(string(encoded)))
}
